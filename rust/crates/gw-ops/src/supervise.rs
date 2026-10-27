//! `groundworks store supervise`: keep the home's store answering, and export it nightly. Every
//! session starts the store on demand (`store::Launcher`), so with no session open nothing keeps it
//! up, and a crash costs the next tool call its deadline. The supervisor adopts a server that is
//! already answering, starts one when none is, starts it again when it stops (with a backoff on
//! repeated failure), and once a day writes each namespace out as gzipped SurrealQL through the
//! `surreal export` command, keeping the newest few days. Its log is a file beside the data.

use std::fs::File;
use std::fmt::Write as _;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

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

/// Windows: register `store supervise` as a scheduled task for this user, at logon, no run-time
/// limit, restarted on failure, then start it. First as S4U (no stored password, no window, no
/// interactive session); where Task Scheduler refuses that to a user without administrator rights
/// (measured 2026-10-26: "Access is denied"), as an interactive-logon task run through
/// `conhost.exe --headless`, which gives the console program no window (measured the same day:
/// MainWindowHandle 0). Returns the task's state.
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
    let args = format!("store supervise --home \"{home_root}\" --surreal \"{surreal}\"");
    let script = |principal: &str, execute: &str, argument: &str, how: &str| {
        format!(
            "$ErrorActionPreference = 'Stop'\n\
             $user = \"$env:USERDOMAIN\\$env:USERNAME\"\n\
             $a = New-ScheduledTaskAction -Execute '{execute}' -Argument '{argument}' -WorkingDirectory '{work}'\n\
             $t = New-ScheduledTaskTrigger -AtLogOn -User $user\n\
             $p = New-ScheduledTaskPrincipal -UserId $user -LogonType {principal} -RunLevel Limited\n\
             $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden\n\
             Register-ScheduledTask -TaskName '{TASK_NAME}' -Description 'Keeps the Groundworks store answering and exports it daily (groundworks store supervise)' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null\n\
             Start-ScheduledTask -TaskName '{TASK_NAME}'\n\
             Start-Sleep -Seconds 3\n\
             $task = Get-ScheduledTask -TaskName '{TASK_NAME}'\n\
             \"task '{TASK_NAME}': $($task.State); runs '{exe}' at logon as $user ({how})\"\n"
        )
    };
    match powershell(&script("S4U", &exe, &args, "S4U: no window, no stored password")) {
        Ok(out) => Ok(out),
        Err(e) if format!("{e:#}").contains("denied") => {
            let conhost = std::env::var("SystemRoot").unwrap_or_else(|_| "C:\\Windows".to_owned());
            powershell(&script(
                "Interactive",
                &format!("{conhost}\\System32\\conhost.exe"),
                &format!("--headless \"{exe}\" {args}"),
                "interactive logon through conhost --headless: no window; S4U was refused to this user",
            ))
        }
        Err(e) => Err(e),
    }
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

    /// Append one line. Never fails the caller: a full disk must not stop the supervisor (it
    /// would be restarted three times into the same full disk and then given up on).
    pub fn line(&self, text: &str) {
        if std::fs::metadata(&self.path).map_or(0, |m| m.len()) > 2 * 1024 * 1024 {
            let _ = std::fs::rename(&self.path, self.path.with_extension("log.1"));
        }
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&self.path) {
            let _ = writeln!(
                f,
                "{} [{}] {text}",
                chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%z"),
                self.machine
            );
        }
    }
}

/// Health misses in a row before the supervisor starts a server. One 800 ms miss during a busy
/// eval started a competing server that died on rocksdb's lock (measured 2026-10-26, twice).
pub const MISSES_BEFORE_START: u32 = 2;
/// Free space on the store's volume under which the supervisor logs loudly, once an hour.
pub const DISK_WARN_BYTES: u64 = 20 * 1024 * 1024 * 1024;
/// Free space under which the export is refused: an export on a full disk left a partial day
/// folder that counted as done (measured 2026-10-26).
pub const DISK_EXPORT_FLOOR_BYTES: u64 = 5 * 1024 * 1024 * 1024;
/// The file in a day's export folder that says the export finished; a folder without it is a
/// partial export and is replaced.
pub const DONE_FILE: &str = "DONE";

fn gib(bytes: u64) -> f64 {
    bytes as f64 / 2f64.powi(30)
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
    ));
    let mut starts = 0usize;
    let mut misses = 0u32;
    let mut backoff = Duration::from_secs(1);
    let mut adopted = false;
    let mut last_disk_line: Option<Instant> = None;
    let mut last_export_try: Option<Instant> = None;
    loop {
        if gw_store::healthy(&launcher.url) {
            if !adopted && starts == 0 {
                log.line("adopted a server already answering");
            }
            adopted = true;
            misses = 0;
            backoff = Duration::from_secs(1);
        } else {
            misses += 1;
            if misses < MISSES_BEFORE_START {
                log.line(&format!("health miss {misses} of {MISSES_BEFORE_START}"));
                std::thread::sleep(opts.poll);
                continue;
            }
            log.line(&if adopted || starts > 0 {
                format!("store stopped answering ({misses} misses in a row); starting it")
            } else {
                "no server answering; starting it".to_owned()
            });
            if let Ok(mut last) = launcher.last_started.lock() {
                *last = None;
            }
            match launcher.ensure() {
                Ok(()) => {
                    misses = 0;
                    adopted = true;
                    match launcher.last_started.lock().map(|p| *p).unwrap_or(None) {
                        Some(pid) => {
                            starts += 1;
                            log.line(&format!("started surreal pid {pid} (start {starts})"));
                            if opts.exit_after_starts.is_some_and(|n| starts >= n) {
                                log.line(&format!("exiting after {starts} start(s) as asked"));
                                return Ok(());
                            }
                        }
                        None => log.line("answered again without a start"),
                    }
                }
                Err(e) => {
                    log.line(&format!("start failed: {e:#}; next try in {}s", backoff.as_secs()));
                    std::thread::sleep(backoff);
                    backoff = (backoff * 2).min(Duration::from_secs(60));
                    continue;
                }
            }
        }
        let free = fs2::available_space(launcher.data()).ok();
        if let Some(free) = free
            && free < DISK_WARN_BYTES
            && last_disk_line.is_none_or(|t| t.elapsed() > Duration::from_secs(3600))
        {
            log.line(&format!(
                "DISK LOW: {:.1} GB free on the store's volume (warned under {:.0} GB; exports refused under {:.0} GB)",
                gib(free),
                gib(DISK_WARN_BYTES),
                gib(DISK_EXPORT_FLOOR_BYTES)
            ));
            last_disk_line = Some(Instant::now());
        }
        if let Some((h, m)) = opts.export_at {
            let now = chrono::Local::now();
            let day = now.format("%Y-%m-%d").to_string();
            let due = (now.hour(), now.minute()) >= (h, m) && !opts.backups.join(&day).join(DONE_FILE).is_file();
            let may_try = last_export_try.is_none_or(|t| t.elapsed() > Duration::from_secs(3600));
            if due && may_try {
                last_export_try = Some(Instant::now());
                if free.is_some_and(|f| f < DISK_EXPORT_FLOOR_BYTES) {
                    log.line(&format!(
                        "export skipped: {:.1} GB free is under the {:.0} GB floor; next try in an hour",
                        gib(free.unwrap_or(0)),
                        gib(DISK_EXPORT_FLOOR_BYTES)
                    ));
                } else {
                    match export(store, &opts.backups, opts.keep) {
                        Ok(files) => {
                            let bytes: u64 = files.iter().filter_map(|f| std::fs::metadata(f).ok()).map(|m| m.len()).sum();
                            log.line(&format!(
                                "exported {} namespace(s), {:.1} MB, to {}",
                                files.len(),
                                bytes as f64 / 1e6,
                                opts.backups.join(&day).display()
                            ));
                        }
                        Err(e) => log.line(&format!("export failed: {e:#}; next try in an hour")),
                    }
                }
            }
        }
        std::thread::sleep(opts.poll);
    }
}

/// Export every namespace of the store as `<dir>/<YYYY-MM-DD>/<namespace>.surql.gz` through
/// `surreal export`, into a `.part` folder first; when every namespace is written and no project
/// export is implausibly small, a `DONE` file listing the files is written and the folder takes
/// the day's name (replacing a partial one). Then the oldest day folders beyond `keep` go.
/// Returns the files written.
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
    let part = dir.join(format!("{day}.part"));
    let _ = std::fs::remove_dir_all(&part);
    std::fs::create_dir_all(&part)?;
    let mut files = Vec::new();
    let mut listing = String::new();
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
        ensure!(
            !ns.starts_with("proj_") || output.stdout.len() >= 1024,
            "surreal export of {ns} is {} bytes: too small to be a project",
            output.stdout.len()
        );
        let name = format!("{ns}.surql.gz");
        let path = part.join(&name);
        let mut enc = flate2::write::GzEncoder::new(File::create(&path)?, flate2::Compression::default());
        enc.write_all(&output.stdout)?;
        enc.finish()?;
        let _ = writeln!(listing, "{name} {} bytes ({} uncompressed)", std::fs::metadata(&path).map_or(0, |m| m.len()), output.stdout.len());
        files.push(out.join(name));
    }
    std::fs::write(part.join(DONE_FILE), listing)?;
    if out.exists() {
        std::fs::remove_dir_all(&out).with_context(|| format!("replacing the partial export {}", out.display()))?;
    }
    std::fs::rename(&part, &out).with_context(|| format!("naming the export {}", out.display()))?;
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
