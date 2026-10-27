//! `groundworks store supervise`: keep the home's store answering, and export it nightly. Every
//! session starts the store on demand (`store::Launcher`), so with no session open nothing keeps it
//! up, and a crash costs the next tool call its deadline. The supervisor adopts a server that is
//! already answering, starts one when none is, starts it again when it stops (with a backoff on
//! repeated failure), and once a day writes each namespace out as gzipped SurrealQL through the
//! `surreal export` command, keeping the newest few days. Its log is a file beside the data.

use std::fs::File;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use anyhow::{Context, Result, ensure};
use chrono::Timelike as _;

use crate::home::Home;
use crate::store::{Launcher, Store};

/// The Windows scheduled task's name.
pub const TASK_NAME: &str = "Groundworks store";

/// The home's store with its launcher, the server not started yet (the supervisor does that and
/// counts it). `surreal` names the binary when the caller cannot rely on PATH.
pub fn open_store(home: &Home, surreal: Option<PathBuf>) -> Result<Store> {
    let launcher = Launcher::for_home(home)?.with_binary(surreal);
    Ok(Store::with_launcher_lazy(launcher, &crate::store::read_secret(home, "agent")?))
}

/// The supervisor's log, beside the server's own log under the data directory.
pub fn log_path(home: &Home, store: &Store) -> PathBuf {
    store
        .launcher()
        .map_or_else(|| home.root.join("db"), |l| l.data().to_path_buf())
        .join("supervisor.log")
}

/// Windows: register `store supervise` as a scheduled task for this user, at logon, S4U (no stored
/// password, no window, no interactive session), no run-time limit, restarted on failure; then
/// start it. Returns the task's state.
pub fn install_task(home: &Home, store: &Store, exe: &Path) -> Result<String> {
    ensure!(
        cfg!(windows),
        "store install registers a Windows scheduled task; elsewhere run `groundworks store supervise --home <home>` from the service manager"
    );
    let binary = store
        .launcher()
        .and_then(|l| l.binary().map(Path::to_path_buf))
        .or_else(gw_store::find_surreal)
        .context("SurrealDB binary not found")?;
    let ps = |p: &Path| std::path::absolute(p).map(|p| p.display().to_string().replace('\'', "''"));
    let (exe, home_root, surreal) = (ps(exe)?, ps(&home.root)?, ps(&binary)?);
    let work = ps(&home.workspace)?;
    let script = format!(
        "$ErrorActionPreference = 'Stop'\n\
         $user = \"$env:USERDOMAIN\\$env:USERNAME\"\n\
         $a = New-ScheduledTaskAction -Execute '{exe}' -Argument 'store supervise --home \"{home_root}\" --surreal \"{surreal}\"' -WorkingDirectory '{work}'\n\
         $t = New-ScheduledTaskTrigger -AtLogOn -User $user\n\
         $p = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited\n\
         $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden\n\
         Register-ScheduledTask -TaskName '{TASK_NAME}' -Description 'Keeps the Groundworks store answering and exports it daily (groundworks store supervise)' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null\n\
         Start-ScheduledTask -TaskName '{TASK_NAME}'\n\
         Start-Sleep -Seconds 3\n\
         $task = Get-ScheduledTask -TaskName '{TASK_NAME}'\n\
         \"task '{TASK_NAME}': $($task.State); runs '{exe}' at logon as $user (S4U: no window, no stored password)\"\n"
    );
    powershell(&script)
}

/// Windows: stop and remove the task. The server it started keeps running until it stops.
pub fn uninstall_task() -> Result<String> {
    ensure!(cfg!(windows), "there is no task to remove outside Windows");
    powershell(&format!(
        "$ErrorActionPreference = 'Stop'\n\
         if (Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue) {{\n\
           Stop-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue\n\
           Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false\n\
           \"task '{TASK_NAME}' removed\"\n\
         }} else {{ \"no task named '{TASK_NAME}'\" }}\n"
    ))
}

fn powershell(script: &str) -> Result<String> {
    let output = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])
        .stdin(Stdio::null())
        .output()
        .context("running powershell.exe")?;
    let (out, err) = (
        String::from_utf8_lossy(&output.stdout).trim().to_owned(),
        String::from_utf8_lossy(&output.stderr).trim().to_owned(),
    );
    ensure!(output.status.success(), "powershell failed: {err}\n{out}");
    Ok(out)
}

pub struct Options {
    /// How often the store is asked for its health.
    pub poll: Duration,
    /// Local time of day (hour, minute) after which the day's export runs, if any.
    pub export_at: Option<(u32, u32)>,
    /// How many days of exports to keep.
    pub keep: usize,
    /// Stop after this many starts (tests); None runs until killed.
    pub exit_after_starts: Option<usize>,
    pub log: PathBuf,
    /// Where exports go, one folder per day.
    pub backups: PathBuf,
}

/// The supervisor's log: one line per event, local time, machine named, rotated at 2 MB.
pub struct Log {
    path: PathBuf,
    machine: String,
}

impl Log {
    pub fn open(path: &Path) -> Result<Self> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        Ok(Self {
            path: path.to_owned(),
            machine: std::env::var("COMPUTERNAME")
                .or_else(|_| std::env::var("HOSTNAME"))
                .unwrap_or_else(|_| "unknown".to_owned()),
        })
    }

    pub fn line(&self, text: &str) -> Result<()> {
        if std::fs::metadata(&self.path).map_or(0, |m| m.len()) > 2 * 1024 * 1024 {
            let _ = std::fs::rename(&self.path, self.path.with_extension("log.1"));
        }
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        writeln!(f, "{} [{}] {text}", chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%z"), self.machine)?;
        Ok(())
    }
}

/// Run until killed (or until `exit_after_starts`). Never returns Ok while the store is down.
pub fn run(store: &Store, opts: &Options) -> Result<()> {
    let launcher = store
        .launcher()
        .context("this store was opened without a launcher; supervise needs a home")?;
    let log = Log::open(&opts.log)?;
    log.line(&format!(
        "supervisor up: store {} data {} poll {}s export {} keep {}",
        launcher.url,
        launcher.data().display(),
        opts.poll.as_secs_f64(),
        opts.export_at
            .map_or("off".to_owned(), |(h, m)| format!("daily at {h:02}:{m:02}")),
        opts.keep
    ))?;
    let mut starts = 0usize;
    let mut backoff = Duration::from_secs(1);
    let mut adopted = false;
    loop {
        if gw_store::healthy(&launcher.url) {
            if !adopted && starts == 0 {
                log.line("adopted a server already answering")?;
            }
            adopted = true;
            backoff = Duration::from_secs(1);
        } else {
            log.line(if adopted || starts > 0 {
                "store stopped answering; starting it"
            } else {
                "no server answering; starting it"
            })?;
            match launcher.ensure() {
                Ok(()) => {
                    starts += 1;
                    let pid = launcher.last_started.lock().map(|p| *p).unwrap_or(None);
                    log.line(&format!(
                        "started surreal pid {} (start {starts})",
                        pid.map_or("?".to_owned(), |p| p.to_string())
                    ))?;
                    adopted = true;
                    if opts.exit_after_starts.is_some_and(|n| starts >= n) {
                        log.line(&format!("exiting after {starts} start(s) as asked"))?;
                        return Ok(());
                    }
                }
                Err(e) => {
                    log.line(&format!("start failed: {e:#}; next try in {}s", backoff.as_secs()))?;
                    std::thread::sleep(backoff);
                    backoff = (backoff * 2).min(Duration::from_secs(60));
                    continue;
                }
            }
        }
        if let Some((h, m)) = opts.export_at {
            let now = chrono::Local::now();
            let day = now.format("%Y-%m-%d").to_string();
            if (now.hour(), now.minute()) >= (h, m) && !opts.backups.join(&day).is_dir() {
                match export(store, &opts.backups, opts.keep) {
                    Ok(files) => log.line(&format!(
                        "exported {} namespace(s) to {}",
                        files.len(),
                        opts.backups.join(&day).display()
                    ))?,
                    Err(e) => {
                        log.line(&format!("export failed: {e:#}"))?;
                        // Leave the day's folder so it is not retried every poll; the log has why.
                        let _ = std::fs::create_dir_all(opts.backups.join(&day));
                    }
                }
            }
        }
        std::thread::sleep(opts.poll);
    }
}

/// Export every namespace of the store as `<dir>/<YYYY-MM-DD>/<namespace>.surql.gz` through
/// `surreal export`, then drop the oldest day folders beyond `keep`. Returns the files written.
pub fn export(store: &Store, dir: &Path, keep: usize) -> Result<Vec<PathBuf>> {
    let binary = store
        .launcher()
        .and_then(|l| l.binary().map(Path::to_path_buf))
        .or_else(gw_store::find_surreal)
        .context("SurrealDB binary not found for export")?;
    let base = store
        .url()
        .strip_suffix("/sql")
        .unwrap_or(store.url())
        .trim_end_matches('/')
        .to_owned();
    let day = chrono::Local::now().format("%Y-%m-%d").to_string();
    let out = dir.join(&day);
    std::fs::create_dir_all(&out)?;
    let mut files = Vec::new();
    for ns in store.namespaces()? {
        let output = Command::new(&binary)
            .args(["export", "--endpoint", &base, "--namespace", &ns, "--database", gw_store::DB_NAME, "-"])
            .env("SURREAL_USER", "root")
            .env("SURREAL_PASS", store.root_pass())
            .stdin(Stdio::null())
            .output()
            .with_context(|| format!("running {} export", binary.display()))?;
        ensure!(
            output.status.success(),
            "surreal export of {ns} failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        );
        let path = out.join(format!("{ns}.surql.gz"));
        let mut enc = flate2::write::GzEncoder::new(File::create(&path)?, flate2::Compression::default());
        enc.write_all(&output.stdout)?;
        enc.finish()?;
        files.push(path);
    }
    let mut days: Vec<PathBuf> = std::fs::read_dir(dir)?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.is_dir() && p.file_name().is_some_and(|n| n.len() == 10))
        .collect();
    days.sort();
    while days.len() > keep.max(1) {
        let old = days.remove(0);
        std::fs::remove_dir_all(&old).with_context(|| format!("removing old export {}", old.display()))?;
    }
    Ok(files)
}

/// "HH:MM" to (hour, minute).
pub fn parse_time(text: &str) -> Result<(u32, u32)> {
    let (h, m) = text.split_once(':').context("time is HH:MM")?;
    let (h, m): (u32, u32) = (h.parse()?, m.parse()?);
    ensure!(h < 24 && m < 60, "time is HH:MM");
    Ok((h, m))
}
