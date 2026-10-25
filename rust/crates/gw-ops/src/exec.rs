//! Running a command in a project's environment: locally with its vars and
//! resolved secrets, or over ssh with vars only. Output is redacted, capped and
//! labelled with the machine it came from.

use std::path::PathBuf;
use std::process::Stdio;
use std::time::{Duration, Instant};

use anyhow::{Result, bail};
use tokio::process::Command;

use crate::home::{Home, machine};
use crate::registry::{Env, Project};
use crate::secrets::{Redactor, SecretRef};

/// Per stream, after redaction. The tail is kept: errors are at the end.
const MAX_STREAM_BYTES: usize = 24_000;

pub struct Outcome {
    pub machine: String,
    pub location: String,
    pub command: String,
    pub code: Option<i32>,
    pub timed_out: bool,
    pub elapsed: Duration,
    pub stdout: String,
    pub stderr: String,
}

impl Outcome {
    pub fn ok(&self) -> bool {
        !self.timed_out && self.code == Some(0)
    }

    pub fn render(&self) -> String {
        let exit = match (self.timed_out, self.code) {
            (true, _) => "TIMED OUT (killed)".to_owned(),
            (false, Some(code)) => code.to_string(),
            (false, None) => "killed by signal".to_owned(),
        };
        format!(
            "machine: {}\nwhere: {}\ncommand: {}\nexit: {exit}\nelapsed: {:.1}s\n--- stdout ---\n{}\n--- stderr ---\n{}",
            self.machine,
            self.location,
            self.command,
            self.elapsed.as_secs_f64(),
            self.stdout.trim_end(),
            self.stderr.trim_end()
        )
    }
}

pub async fn run(home: &Home, project: &Project, env: &Env, command: &str, timeout: Duration) -> Result<Outcome> {
    let mut redactor = Redactor::default();
    let (mut cmd, location) = match &env.ssh {
        Some(host) => (
            remote(host, env, command)?,
            format!("{host}:{}", env.dir.as_deref().unwrap_or("~")),
        ),
        None => {
            let dir = match &env.dir {
                Some(dir) => home.workspace.join(dir),
                None => project.path(&home.workspace),
            };
            if !dir.is_dir() {
                bail!("{} does not exist on {}", dir.display(), machine());
            }
            let mut cmd = local_shell(command);
            cmd.current_dir(&dir).envs(&env.vars);
            inject_secrets(&mut cmd, env, &mut redactor).await?;
            (cmd, dir.display().to_string())
        }
    };
    cmd.stdin(Stdio::null()).kill_on_drop(true);

    let start = Instant::now();
    let (code, timed_out, stdout, stderr) = match tokio::time::timeout(timeout, cmd.output()).await {
        Ok(output) => {
            let output = output?;
            (output.status.code(), false, output.stdout, output.stderr)
        }
        Err(_) => (None, true, Vec::new(), Vec::new()),
    };
    let clean = |bytes: Vec<u8>| tail(redactor.redact(&String::from_utf8_lossy(&bytes)));
    Ok(Outcome {
        machine: machine(),
        location,
        command: command.to_owned(),
        code,
        timed_out,
        elapsed: start.elapsed(),
        stdout: clean(stdout),
        stderr: clean(stderr),
    })
}

/// Every secret resolves or nothing runs: a half-configured env fails closed.
async fn inject_secrets(cmd: &mut Command, env: &Env, redactor: &mut Redactor) -> Result<()> {
    let mut failures = Vec::new();
    for (name, raw) in &env.secrets {
        let resolved = match raw.parse::<SecretRef>() {
            Ok(secret) => secret.resolve().await,
            Err(e) => Err(e),
        };
        match resolved.and_then(|value| redactor.add(name, &value).map(|()| value)) {
            Ok(value) => {
                cmd.env(name, value);
            }
            Err(e) => failures.push(format!("{name} ({raw}): {e:#}")),
        }
    }
    if !failures.is_empty() {
        bail!(
            "refusing to run: {} secret(s) did not resolve\n  {}",
            failures.len(),
            failures.join("\n  ")
        );
    }
    Ok(())
}

fn remote(host: &str, env: &Env, command: &str) -> Result<Command> {
    if !env.secrets.is_empty() {
        bail!(
            "env over ssh to {host} declares secrets ({}); secrets for a remote host must live on that host, \
             Groundworks never ships them over ssh",
            env.secrets.keys().cloned().collect::<Vec<_>>().join(", ")
        );
    }
    let mut script = String::new();
    if let Some(dir) = &env.dir {
        script.push_str(&format!("cd {} && ", sh_quote(dir)));
    }
    for (key, value) in &env.vars {
        if !key.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
            bail!("{key:?} is not a valid variable name");
        }
        script.push_str(&format!("export {key}={}; ", sh_quote(value)));
    }
    script.push_str(command);
    let mut cmd = Command::new("ssh");
    cmd.args(["-o", "BatchMode=yes", host, &script]);
    Ok(cmd)
}

/// `GROUNDWORKS_SHELL` (any shell taking `-c`) overrides. On Windows, Git Bash is
/// preferred and `bash` is never resolved from PATH: that can be WSL's.
fn local_shell(command: &str) -> Command {
    let shell = std::env::var_os("GROUNDWORKS_SHELL").map(PathBuf::from).or_else(|| {
        cfg!(windows)
            .then(|| PathBuf::from(r"C:\Program Files\Git\bin\bash.exe"))
            .filter(|path| path.is_file())
    });
    match shell {
        Some(shell) => {
            let mut cmd = Command::new(shell);
            cmd.arg("-c").arg(command);
            cmd
        }
        None if cfg!(windows) => {
            let mut cmd = Command::new("powershell");
            cmd.args(["-NoProfile", "-Command", command]);
            cmd
        }
        None => {
            let mut cmd = Command::new("sh");
            cmd.arg("-c").arg(command);
            cmd
        }
    }
}

fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', r"'\''"))
}

fn tail(s: String) -> String {
    if s.len() <= MAX_STREAM_BYTES {
        return s;
    }
    let mut cut = s.len() - MAX_STREAM_BYTES;
    while !s.is_char_boundary(cut) {
        cut += 1;
    }
    format!("[{cut} bytes truncated]\n{}", &s[cut..])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quotes_for_posix_shells() {
        assert_eq!(sh_quote("a b"), "'a b'");
        assert_eq!(sh_quote("it's"), r"'it'\''s'");
    }

    #[test]
    fn remote_refuses_secrets() {
        let mut env = Env {
            ssh: Some("rise3".into()),
            ..Env::default()
        };
        env.secrets.insert("TOKEN".into(), "env:TOKEN".into());
        assert!(remote("rise3", &env, "true").is_err());
    }

    #[test]
    fn tail_keeps_the_end_on_a_char_boundary() {
        let s = format!("{}é{}", "a".repeat(10), "b".repeat(MAX_STREAM_BYTES));
        let t = tail(s);
        assert!(t.ends_with('b'));
        assert!(t.starts_with('['));
    }
}
