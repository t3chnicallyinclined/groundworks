//! Secrets are referenced, never stored and never returned. A reference names
//! where the value lives; it is resolved only to inject into a child process,
//! and that process's output is redacted before anyone reads it.

use std::fmt;
use std::path::PathBuf;
use std::process::Stdio;
use std::str::FromStr;

use anyhow::{Context, Result, bail};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SecretRef {
    /// `env:NAME`, a variable in the Groundworks server's own environment.
    Env(String),
    /// `file:PATH`, the trimmed contents of a file.
    File(PathBuf),
    /// `bw:ITEM#FIELD`, a Bitwarden item field (default `password`).
    Bitwarden { item: String, field: String },
}

impl FromStr for SecretRef {
    type Err = anyhow::Error;

    fn from_str(s: &str) -> Result<Self> {
        let (scheme, rest) = s
            .split_once(':')
            .with_context(|| format!("secret ref {s:?} has no scheme; use env:, file: or bw:"))?;
        if rest.is_empty() {
            bail!("secret ref {s:?} is empty after the scheme");
        }
        match scheme {
            "env" => Ok(Self::Env(rest.to_owned())),
            "file" => Ok(Self::File(PathBuf::from(rest))),
            "bw" => {
                let (item, field) = rest.rsplit_once('#').unwrap_or((rest, "password"));
                Ok(Self::Bitwarden {
                    item: item.to_owned(),
                    field: field.to_owned(),
                })
            }
            other => bail!("unknown secret scheme {other:?} in {s:?}; use env:, file: or bw:"),
        }
    }
}

impl fmt::Display for SecretRef {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Env(var) => write!(f, "env:{var}"),
            Self::File(path) => write!(f, "file:{}", path.display()),
            Self::Bitwarden { item, field } => write!(f, "bw:{item}#{field}"),
        }
    }
}

impl SecretRef {
    pub async fn resolve(&self) -> Result<String> {
        match self {
            Self::Env(var) => std::env::var(var).with_context(|| format!("env var {var} is not set")),
            Self::File(path) => Ok(tokio::fs::read_to_string(path)
                .await
                .with_context(|| format!("reading {}", path.display()))?
                .trim_end()
                .to_owned()),
            Self::Bitwarden { item, field } => bitwarden(item, field).await,
        }
    }
}

async fn bitwarden(item: &str, field: &str) -> Result<String> {
    if std::env::var_os("BW_SESSION").is_none() {
        bail!("Bitwarden is locked: run `bw unlock`, export BW_SESSION, then restart Groundworks");
    }
    let args = ["get", "item", item, "--nointeraction"];
    // The npm install of the CLI is `bw.cmd` on Windows, which `bw` does not find.
    let output = match tokio::process::Command::new("bw")
        .args(args)
        .stdin(Stdio::null())
        .output()
        .await
    {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound && cfg!(windows) => {
            tokio::process::Command::new("bw.cmd")
                .args(args)
                .stdin(Stdio::null())
                .output()
                .await
        }
        other => other,
    }
    .context("could not start `bw`; is the Bitwarden CLI installed?")?;
    if !output.status.success() {
        bail!(
            "bw get item {item:?} failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).context("bw returned non-JSON")?;
    let found = match field {
        "password" | "username" | "totp" => value["login"][field].as_str(),
        "notes" => value["notes"].as_str(),
        name => value["fields"]
            .as_array()
            .and_then(|fields| fields.iter().find(|f| f["name"] == name))
            .and_then(|f| f["value"].as_str()),
    };
    found
        .map(str::to_owned)
        .with_context(|| format!("Bitwarden item {item:?} has no field {field:?}"))
}

/// Replaces every resolved secret value in text with `[REDACTED:<name>]`.
#[derive(Default)]
pub struct Redactor {
    /// (name, value), longest value first so a value containing another is replaced whole.
    values: Vec<(String, String)>,
}

impl Redactor {
    /// Values too short to find reliably are refused rather than leaked.
    pub fn add(&mut self, name: &str, value: &str) -> Result<()> {
        if value.len() < 4 {
            bail!("secret {name} is shorter than 4 characters and cannot be redacted safely");
        }
        self.values.push((name.to_owned(), value.to_owned()));
        // A multi-line value (a key file) can be printed one line at a time.
        for line in value
            .lines()
            .map(str::trim)
            .filter(|line| line.len() >= 8 && *line != value)
        {
            self.values.push((name.to_owned(), line.to_owned()));
        }
        self.values.sort_by(|a, b| b.1.len().cmp(&a.1.len()));
        Ok(())
    }

    pub fn redact(&self, text: &str) -> String {
        let mut out = text.to_owned();
        for (name, value) in &self.values {
            if out.contains(value.as_str()) {
                out = out.replace(value.as_str(), &format!("[REDACTED:{name}]"));
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_every_scheme() {
        assert_eq!(
            "env:TOKEN".parse::<SecretRef>().unwrap(),
            SecretRef::Env("TOKEN".into())
        );
        assert_eq!(
            "file:C:/keys/a.key".parse::<SecretRef>().unwrap(),
            SecretRef::File("C:/keys/a.key".into())
        );
        assert_eq!(
            "bw:rise3 server#key".parse::<SecretRef>().unwrap(),
            SecretRef::Bitwarden {
                item: "rise3 server".into(),
                field: "key".into()
            }
        );
        assert_eq!(
            "bw:github".parse::<SecretRef>().unwrap(),
            SecretRef::Bitwarden {
                item: "github".into(),
                field: "password".into()
            }
        );
        assert!("plaintext-value".parse::<SecretRef>().is_err());
        assert!("vault:x".parse::<SecretRef>().is_err());
        assert!("env:".parse::<SecretRef>().is_err());
    }

    #[test]
    fn redacts_values_and_key_lines() {
        let mut r = Redactor::default();
        r.add("TOKEN", "abcd1234").unwrap();
        r.add("KEY", "-----BEGIN-----\nAAAABBBBCCCC\n-----END-----").unwrap();
        assert_eq!(r.redact("token=abcd1234 ok"), "token=[REDACTED:TOKEN] ok");
        assert_eq!(r.redact("line AAAABBBBCCCC"), "line [REDACTED:KEY]");
        assert!(r.add("PIN", "123").is_err());
    }
}
