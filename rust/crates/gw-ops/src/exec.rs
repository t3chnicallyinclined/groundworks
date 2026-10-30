//! Running a command in a project's environment: locally with its vars and
//! resolved secrets, or over ssh with vars only. Output is redacted, capped and
//! labelled with the machine it came from.

use std::ffi::OsString;
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
    /// SHA-256 of the FULL redacted stdout, a zero byte, and the full redacted stderr, taken before
    /// the display tail is cut: a receipt proves the whole output, not only what was shown.
    pub output_hash: String,
}

/// What a run receipt's `output_hash` covers.
pub const RUN_HASH_OF: &str = "sha256 of the full redacted stdout, a zero byte, then the full redacted stderr";

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

/// The only variables a child inherits from the server's own environment (gw-security fact
/// xdys5ymuariaqw904juw, 2026-10-30). Everything else starts empty; the env's own `vars` and resolved
/// `secrets` are added on top, so an env that needs more (a toolchain root such as `CUDA_PATH`, a proxy)
/// declares it in the registry. Locations and process basics only, never a credential:
/// - every OS: `PATH`, `HOME`, the temp dirs, the user's name, locale and terminal, and the Rust toolchain
///   roots (`CARGO_HOME`, `RUSTUP_HOME`: cargo cannot find its toolchain without them when they are moved);
/// - Unix: the XDG base dirs and `SHELL`;
/// - Windows: what a shell, PowerShell, cargo's MSVC linker search and Node need to work at all
///   (`SystemRoot`, `windir`, `ComSpec`, `PATHEXT`, `USERPROFILE`, `APPDATA`, the Program Files roots, ...).
///
/// Left out on purpose: `BW_SESSION` and every `BW_*`, every `GROUNDWORKS_*`, `SSH_AUTH_SOCK` (signing with
/// the owner's keys; ssh envs get it for the ssh client only), proxy variables (they can carry a password),
/// and every token-shaped variable (`*_TOKEN`, `AWS_*`, `CARGO_REGISTRY_TOKEN`, ...). Names compare
/// case-insensitively, because Windows environment names do (`Path` is `PATH`).
pub const INHERITED: &[&str] = &[
    // Every OS.
    "PATH",
    "HOME",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "TZ",
    "TERM",
    "CARGO_HOME",
    "RUSTUP_HOME",
    // Unix.
    "SHELL",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR",
    // Windows.
    "SystemRoot",
    "SystemDrive",
    "windir",
    "ComSpec",
    "PATHEXT",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "USERNAME",
    "USERDOMAIN",
    "COMPUTERNAME",
    "APPDATA",
    "LOCALAPPDATA",
    "ProgramData",
    "ALLUSERSPROFILE",
    "PUBLIC",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "CommonProgramFiles",
    "CommonProgramFiles(x86)",
    "CommonProgramW6432",
    "PSModulePath",
    "OS",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
];

/// The ssh client alone also gets its agent socket: the remote command never sees it.
const INHERITED_SSH: &[&str] = &["SSH_AUTH_SOCK"];

/// The server's own credentials: never a child's var or secret name, never read by an `env:` secret.
fn reserved(name: &str) -> bool {
    let upper = name.to_ascii_uppercase();
    upper.starts_with("BW_") || upper.starts_with("GROUNDWORKS_")
}

/// Refuses an env that would hand a child the server's own credentials by name: a var or secret named
/// `BW_*` / `GROUNDWORKS_*`, or an `env:` secret that reads one.
fn refuse_reserved(env: &Env) -> Result<()> {
    let mut found = Vec::new();
    for name in env.vars.keys().chain(env.secrets.keys()) {
        if reserved(name) {
            found.push(format!("{name} (a name reserved for the Groundworks server)"));
        }
    }
    for (name, raw) in &env.secrets {
        if let Ok(SecretRef::Env(var)) = raw.parse::<SecretRef>()
            && reserved(&var)
        {
            found.push(format!(
                "{name} reads env:{var} (the Groundworks server's own credential)"
            ));
        }
    }
    if !found.is_empty() {
        bail!(
            "refusing to run: BW_* and GROUNDWORKS_* never reach a command\n  {}",
            found.join("\n  ")
        );
    }
    Ok(())
}

/// The allowlisted part of `vars` (the server's environment), names compared case-insensitively.
fn inherited<I>(vars: I, extra: &[&str]) -> Vec<(OsString, OsString)>
where
    I: IntoIterator<Item = (OsString, OsString)>,
{
    vars.into_iter()
        .filter(|(name, _)| {
            name.to_str().is_some_and(|name| {
                !reserved(name)
                    && INHERITED
                        .iter()
                        .chain(extra)
                        .any(|allowed| allowed.eq_ignore_ascii_case(name))
            })
        })
        .collect()
}

pub async fn run(home: &Home, project: &Project, env: &Env, command: &str, timeout: Duration) -> Result<Outcome> {
    refuse_reserved(env)?;
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
            cmd.current_dir(&dir)
                .env_clear()
                .envs(inherited(std::env::vars_os(), &[]))
                .envs(&env.vars);
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
    let (stdout, stderr) = (
        redactor.redact(&String::from_utf8_lossy(&stdout)),
        redactor.redact(&String::from_utf8_lossy(&stderr)),
    );
    let output_hash = crate::store::sha256_hex(&[stdout.as_bytes(), &[0u8], stderr.as_bytes()]);
    Ok(Outcome {
        machine: machine(),
        location,
        command: command.to_owned(),
        code,
        timed_out,
        elapsed: start.elapsed(),
        stdout: tail(stdout),
        stderr: tail(stderr),
        output_hash,
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
    cmd.args(["-o", "BatchMode=yes", host, &script])
        .env_clear()
        .envs(inherited(std::env::vars_os(), INHERITED_SSH));
    Ok(cmd)
}

/// `GROUNDWORKS_SHELL` (any shell taking `-c`) overrides. On Windows, Git Bash is
/// preferred and `bash` is never resolved from PATH: that can be WSL's.
fn local_shell(command: &str) -> Command {
    match shell_path() {
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

fn shell_path() -> Option<PathBuf> {
    std::env::var_os("GROUNDWORKS_SHELL").map(PathBuf::from).or_else(|| {
        cfg!(windows)
            .then(|| PathBuf::from(r"C:\Program Files\Git\bin\bash.exe"))
            .filter(|path| path.is_file())
    })
}

/// Whether a local command runs under PowerShell (Windows with no Git Bash and no `GROUNDWORKS_SHELL`)
/// rather than a POSIX shell: the selftest writes its probe in the matching syntax.
pub fn uses_powershell() -> bool {
    cfg!(windows) && shell_path().is_none()
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
    fn a_child_inherits_the_allowlist_only() {
        let server = [
            ("Path", "/bin"),
            ("SystemRoot", r"C:\Windows"),
            ("BW_SESSION", "canary"),
            ("bw_session", "canary"),
            ("GROUNDWORKS_OWNER_PASS", "canary"),
            ("GROUNDWORKS_HOME", "/home"),
            ("GITHUB_TOKEN", "canary"),
            ("SSH_AUTH_SOCK", "/agent"),
            ("HTTPS_PROXY", "http://user:pw@proxy"),
        ]
        .map(|(k, v)| (OsString::from(k), OsString::from(v)));
        let names = |kept: Vec<(OsString, OsString)>| -> Vec<String> {
            kept.into_iter().map(|(k, _)| k.into_string().unwrap()).collect()
        };
        assert_eq!(names(inherited(server.clone(), &[])), ["Path", "SystemRoot"]);
        assert_eq!(
            names(inherited(server, INHERITED_SSH)),
            ["Path", "SystemRoot", "SSH_AUTH_SOCK"]
        );
    }

    #[test]
    fn an_env_naming_a_server_credential_is_refused() {
        let mut env = Env::default();
        env.vars.insert("RUST_BACKTRACE".into(), "1".into());
        env.secrets.insert("TOKEN".into(), "env:TOKEN".into());
        assert!(refuse_reserved(&env).is_ok());
        for (name, raw) in [
            ("LEAK", "env:BW_SESSION"),
            ("LEAK", "env:groundworks_owner_pass"),
            ("BW_SESSION", "file:/x"),
        ] {
            let mut bad = env.clone();
            bad.secrets.insert(name.into(), raw.into());
            assert!(refuse_reserved(&bad).is_err(), "{name} <- {raw}");
        }
        let mut bad = env.clone();
        bad.vars.insert("GROUNDWORKS_HOME".into(), "/x".into());
        assert!(refuse_reserved(&bad).is_err());
    }

    #[test]
    fn tail_keeps_the_end_on_a_char_boundary() {
        let s = format!("{}é{}", "a".repeat(10), "b".repeat(MAX_STREAM_BYTES));
        let t = tail(s);
        assert!(t.ends_with('b'));
        assert!(t.starts_with('['));
    }
}
