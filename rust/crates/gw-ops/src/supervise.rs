//! `groundworks store supervise`: keep the home's store answering, and export it nightly. Every
//! session starts the store on demand (`store::Launcher`), so with no session open nothing keeps it
//! up, and a crash costs the next tool call its deadline. The supervisor adopts a server that is
//! already answering, starts one when none is, starts it again when it stops (with a backoff on
//! repeated failure), and once a day writes each namespace out as gzipped SurrealQL through the
//! `surreal export` command, keeping the newest few days. Its log is a file beside the data.

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::fs::File;
use std::io::{Read as _, Write as _};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use anyhow::{Context, Result, ensure};
use chrono::Timelike as _;
use gw_core::db::Db;
use gw_store::scratch::Scratch;
use serde_json::Value;

use crate::home::Home;
use crate::store::{Launcher, Store};

/// The Windows scheduled task's name.
pub const TASK_NAME: &str = "Groundworks store";

/// The home's store with its launcher, the server not started yet (the supervisor does that and
/// counts it). `surreal` names the binary when the caller cannot rely on PATH.
pub fn open_store(home: &Home, surreal: Option<PathBuf>) -> Result<Store> {
    let launcher = Launcher::for_home(home)?.with_binary(surreal);
    Ok(Store::with_launcher_lazy(
        launcher,
        &crate::store::read_secret(home, "agent")?,
    ))
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
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ])
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
    /// Restore the day's export into a scratch server after writing it, and compare counts.
    pub drill: bool,
    /// The home, when the supervisor should take the pulse once a day after the export.
    pub home_root: Option<PathBuf>,
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
/// Every table's row count per namespace, taken before and after each namespace's export, so the
/// restore drill has something to compare a restore against.
pub const COUNTS_FILE: &str = "COUNTS.json";
/// The restore drill's report beside the export: `PASS` or `FAIL` first, then one line per namespace.
pub const DRILL_FILE: &str = "DRILL";

/// Where a home's exports go, one folder per day.
pub fn backups_dir(home: &Home) -> PathBuf {
    home.root.join("backup")
}

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
                            let bytes: u64 = files
                                .iter()
                                .filter_map(|f| std::fs::metadata(f).ok())
                                .map(|m| m.len())
                                .sum();
                            log.line(&format!(
                                "exported {} namespace(s), {:.1} MB, to {}",
                                files.len(),
                                bytes as f64 / 1e6,
                                opts.backups.join(&day).display()
                            ));
                            if opts.drill {
                                match drill(store, &opts.backups, Some(&day)) {
                                    Ok(d) if d.passed => log.line(&format!(
                                        "restore drill PASS in {:.1}s: {} namespace(s) restored into a scratch server, every count matched; {}",
                                        d.seconds,
                                        d.lines.len(),
                                        d.dir.join(DRILL_FILE).display()
                                    )),
                                    Ok(d) => log.line(&format!(
                                        "RESTORE DRILL FAILED: {}; see {}",
                                        d.lines
                                            .iter()
                                            .filter(|l| l.starts_with("FAIL"))
                                            .cloned()
                                            .collect::<Vec<_>>()
                                            .join(" | "),
                                        d.dir.join(DRILL_FILE).display()
                                    )),
                                    Err(e) => log.line(&format!("RESTORE DRILL NOT EXECUTED: {e:#}")),
                                }
                            }
                            if let Some(root) = &opts.home_root {
                                match daily_pulse(root, store) {
                                    Ok(line) => log.line(&line),
                                    Err(e) => log.line(&format!("pulse NOT TAKEN: {e:#}")),
                                }
                            }
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
    let mut counts: BTreeMap<String, BTreeMap<String, [i64; 2]>> = BTreeMap::new();
    for ns in store.namespaces()? {
        // Counts before and after: the store is live, so a session may write between the count and
        // the export; the drill accepts a restored count between the two and says so.
        let live = root_db(store.url(), &ns, store.root_pass());
        let before = table_counts(&live).with_context(|| format!("counting the tables of {ns}"))?;
        // To a file, never to stdout: `surreal export ... -` on 3.1.6 writes its own INFO log line
        // into stdout ahead of the script, and `surreal import` then refuses the file (the first
        // restore drill, 2026-10-27, failed on all 20 namespaces that way).
        let raw = part.join(format!("{ns}.surql"));
        let output = Command::new(&binary)
            .args([
                "export",
                "--log",
                "warn",
                "--endpoint",
                &base,
                "--namespace",
                &ns,
                "--database",
                gw_store::DB_NAME,
            ])
            .arg(&raw)
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
        let script = std::fs::read(&raw).with_context(|| format!("reading the export of {ns}"))?;
        let _ = std::fs::remove_file(&raw);
        ensure!(
            !ns.starts_with("proj_") || script.len() >= 1024,
            "surreal export of {ns} is {} bytes: too small to be a project",
            script.len()
        );
        ensure!(
            script.starts_with(b"--") || script.starts_with(b"OPTION") || script.starts_with(b"DEFINE"),
            "surreal export of {ns} does not start with SurrealQL: {:?}",
            String::from_utf8_lossy(&script[..script.len().min(60)])
        );
        // surreal export 3.1.6 writes tables alphabetically, so the `evidence` relation's rows come
        // before the `fact` and `unit` rows they join, and that table is ENFORCED: `surreal import`
        // refused every export at its first relation ("The record 'fact:...' does not exist"), found
        // 2026-10-29 by the first drill that got past the stdout defect. Relations go last.
        let script = relations_last(&script);
        let name = format!("{ns}.surql.gz");
        let path = part.join(&name);
        let mut enc = flate2::write::GzEncoder::new(File::create(&path)?, flate2::Compression::default());
        enc.write_all(&script)?;
        enc.finish()?;
        let _ = writeln!(
            listing,
            "{name} {} bytes ({} uncompressed)",
            std::fs::metadata(&path).map_or(0, |m| m.len()),
            script.len()
        );
        files.push(out.join(name));
        let after = table_counts(&live).with_context(|| format!("counting the tables of {ns}"))?;
        let mut ranges = BTreeMap::new();
        for (table, b) in &before {
            ranges.insert(table.clone(), [*b, *after.get(table).unwrap_or(b)]);
        }
        for (table, a) in &after {
            ranges.entry(table.clone()).or_insert([0, *a]);
        }
        counts.insert(ns.clone(), ranges);
    }
    std::fs::write(part.join(COUNTS_FILE), serde_json::to_string_pretty(&counts)?)?;
    let _ = writeln!(listing, "{COUNTS_FILE} table counts before and after each export");
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

/// A root connection to any namespace, including ones outside the data model's naming (a live
/// server may hold a stray one, and the export covers every namespace the server has).
fn root_db(url: &str, ns: &str, root_pass: &str) -> Db {
    Db::new(
        url,
        ns,
        gw_store::DB_NAME,
        "root",
        root_pass,
        Duration::from_secs(60),
        None,
    )
}

/// Every table of the connection's database with its row count (root, so nothing is hidden by a
/// permission). `INFO FOR DB` names the tables; each is counted by name through a bound variable.
pub fn table_counts(db: &Db) -> Result<BTreeMap<String, i64>> {
    let info = db.sql("INFO FOR DB;", Some(1))?;
    let tables = info
        .first()
        .and_then(|i| i["tables"].as_object())
        .context("INFO FOR DB named no tables")?;
    let mut counts = BTreeMap::new();
    for name in tables.keys() {
        let vars = serde_json::Map::from_iter([("t".to_owned(), Value::from(name.as_str()))]);
        let rows = db.query("RETURN count(SELECT VALUE id FROM type::table($t));", &vars, Some(1))?;
        let n = rows
            .first()
            .and_then(Value::as_i64)
            .with_context(|| format!("count of {name} was not a number: {rows:?}"))?;
        counts.insert(name.clone(), n);
    }
    Ok(counts)
}

/// What a restore drill found.
#[derive(Debug)]
pub struct Drill {
    /// The export folder drilled (`<dir>/<YYYY-MM-DD>`).
    pub dir: PathBuf,
    pub passed: bool,
    /// One line per namespace: `ok   <ns>: ...` or `FAIL <ns>: ...`, plus any file-level failure.
    pub lines: Vec<String>,
    pub seconds: f64,
}

/// The restore drill: import the day's export (the newest finished one when `day` is None) into a
/// scratch SurrealDB this function starts and stops, and compare every table's row count with the
/// counts taken at export time. Nothing here writes to the live store; it is read for its root
/// password and binary only. Writes `DRILL` beside the export and returns the same report. A backup
/// nobody has restored is not a backup (CLAUDE.md rule 4): the first drill, 2026-10-27, failed on
/// every namespace and found the export itself broken.
pub fn drill(store: &Store, dir: &Path, day: Option<&str>) -> Result<Drill> {
    let started = Instant::now();
    let binary = store
        .launcher()
        .and_then(|l| l.binary().map(Path::to_path_buf))
        .or_else(gw_store::find_surreal)
        .context("SurrealDB binary not found for the restore drill")?;
    let day_dir = match day {
        Some(d) => dir.join(d),
        None => newest_done(dir)?,
    };
    ensure!(
        day_dir.join(DONE_FILE).is_file(),
        "{} has no {DONE_FILE}: not a finished export",
        day_dir.display()
    );
    let counts_text = std::fs::read_to_string(day_dir.join(COUNTS_FILE)).with_context(|| {
        format!(
            "{} has no {COUNTS_FILE}: this export predates table counts and cannot be verified",
            day_dir.display()
        )
    })?;
    let expected: BTreeMap<String, BTreeMap<String, [i64; 2]>> =
        serde_json::from_str(&counts_text).context("reading COUNTS.json")?;
    let scratch = Scratch::start(&binary, store.root_pass(), Duration::from_secs(30))
        .context("starting a scratch SurrealDB for the drill")?;
    let port = scratch.port();
    let mut lines = Vec::new();
    let mut passed = !expected.is_empty();
    if expected.is_empty() {
        lines.push(format!("FAIL {COUNTS_FILE} names no namespace"));
    }
    for (ns, want) in &expected {
        let gz = day_dir.join(format!("{ns}.surql.gz"));
        match restore_one(&binary, &scratch, ns, &gz, want, &store.agent_pass(ns)) {
            Ok(summary) => lines.push(format!("ok   {ns}: {summary}")),
            Err(e) => {
                passed = false;
                lines.push(format!("FAIL {ns}: {e:#}"));
            }
        }
    }
    for entry in std::fs::read_dir(&day_dir)?.filter_map(Result::ok) {
        let name = entry.file_name().to_string_lossy().into_owned();
        if let Some(ns) = name.strip_suffix(".surql.gz")
            && !expected.contains_key(ns)
        {
            passed = false;
            lines.push(format!("FAIL {ns}: exported but no counts were recorded for it"));
        }
    }
    drop(scratch);
    let seconds = started.elapsed().as_secs_f64();
    let report = format!(
        "{} restore drill of {} on {} in {seconds:.1}s: {} namespace(s), scratch server on port {port} (stopped)\n{}\n",
        if passed { "PASS" } else { "FAIL" },
        day_dir
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default(),
        crate::home::machine(),
        expected.len(),
        lines.join("\n")
    );
    std::fs::write(day_dir.join(DRILL_FILE), report)
        .with_context(|| format!("writing {}", day_dir.join(DRILL_FILE).display()))?;
    Ok(Drill {
        dir: day_dir,
        passed,
        lines,
        seconds,
    })
}

/// One namespace: gunzip, import into the scratch server, count, compare.
fn restore_one(
    binary: &Path,
    scratch: &Scratch,
    ns: &str,
    gz: &Path,
    want: &BTreeMap<String, [i64; 2]>,
    agent_pass: &str,
) -> Result<String> {
    ensure!(
        !ns.is_empty()
            && ns
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_'),
        "namespace name {ns:?} is not one this store would have written"
    );
    let bytes = std::fs::read(gz).with_context(|| format!("reading {}", gz.display()))?;
    let mut script = Vec::new();
    flate2::read::GzDecoder::new(&bytes[..])
        .read_to_end(&mut script)
        .with_context(|| format!("gunzip of {}", gz.display()))?;
    ensure!(!script.is_empty(), "{} is empty once decompressed", gz.display());
    let raw = std::env::temp_dir().join(format!("gw-drill-{}-{ns}.surql", std::process::id()));
    std::fs::write(&raw, &script)?;
    let imported = (|| -> Result<()> {
        root_db(&scratch.url(), ns, scratch.root_pass())
            .sql(
                &format!(
                    "DEFINE NAMESPACE IF NOT EXISTS {ns}; USE NS {ns}; DEFINE DATABASE IF NOT EXISTS {};",
                    gw_store::DB_NAME
                ),
                None,
            )
            .context("creating the namespace on the scratch server")?;
        let output = Command::new(binary)
            .args([
                "import",
                "--log",
                "warn",
                "--endpoint",
                &scratch.base(),
                "--namespace",
                ns,
                "--database",
                gw_store::DB_NAME,
            ])
            .arg(&raw)
            .env("SURREAL_USER", "root")
            .env("SURREAL_PASS", scratch.root_pass())
            .stdin(Stdio::null())
            .output()
            .with_context(|| format!("running {} import", binary.display()))?;
        ensure!(
            output.status.success(),
            "surreal import refused the export: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        );
        Ok(())
    })();
    let _ = std::fs::remove_file(&raw);
    imported?;
    let restored = root_db(&scratch.url(), ns, scratch.root_pass());
    let got = table_counts(&restored).context("counting the restored tables")?;
    // The export carries the access with `KEY '[REDACTED]'`. Re-key it, as a restore must, and prove
    // the real agent credential signs in to the restored namespace (where an agent member exists).
    let mut signed_in = false;
    if got.contains_key("member") {
        gw_store::redefine_access(&restored).context("re-defining the member access on the restored namespace")?;
        let agents = restored
            .sql(
                "RETURN count(SELECT VALUE id FROM member WHERE id = type::record('member', 'agent'));",
                Some(1),
            )
            .context("looking for the agent member")?;
        if agents.first().and_then(Value::as_i64).unwrap_or(0) > 0 {
            gw_store::connect_member(&scratch.url(), ns, crate::store::AGENT, agent_pass)
                .context("connecting as the agent")?
                .sql("RETURN 1;", Some(1))
                .context("the agent credential could not sign in to the restored namespace")?;
            signed_in = true;
        }
    }
    let mut problems = Vec::new();
    let mut rows = 0i64;
    let mut inexact = 0usize;
    for (table, [a, b]) in want {
        let (lo, hi) = (*a.min(b), *a.max(b));
        match got.get(table) {
            Some(n) if (lo..=hi).contains(n) => {
                rows += n;
                inexact += usize::from(lo != hi);
            }
            Some(n) => problems.push(format!("{table}: restored {n}, exported {lo}..={hi}")),
            None => problems.push(format!("{table}: missing after restore (exported {lo}..={hi})")),
        }
    }
    for table in got.keys().filter(|t| !want.contains_key(*t)) {
        problems.push(format!("{table}: restored but never exported"));
    }
    ensure!(problems.is_empty(), "{}", problems.join("; "));
    Ok(format!(
        "{} table(s), {rows} row(s) match{}{}",
        want.len(),
        if inexact > 0 {
            format!(" ({inexact} within the export's before/after range)")
        } else {
            String::new()
        },
        if signed_in {
            "; access re-keyed and the agent signs in"
        } else {
            ""
        }
    ))
}

/// The export with every `INSERT RELATION` statement moved after the record statements, so an
/// ENFORCED relation finds both ends when the file is imported. A 3.1.6 export writes one statement
/// per line (measured over 20 namespaces and 150 INSERTs, none spanning lines); a relation line that
/// does not end in `;` is left where it is, and a script that is not UTF-8 is returned unchanged,
/// so the drill, not this function, is what says whether the file restores.
pub fn relations_last(script: &[u8]) -> Vec<u8> {
    let Ok(text) = std::str::from_utf8(script) else {
        return script.to_vec();
    };
    let (moved, kept): (Vec<&str>, Vec<&str>) = text
        .lines()
        .partition(|l| l.starts_with("INSERT RELATION") && l.trim_end().ends_with(';'));
    if moved.is_empty() {
        return script.to_vec();
    }
    let mut out = kept.join("\n");
    out.push_str(
        "\n\n-- ------------------------------\n\
         -- RELATIONS, moved after the records they join by the Groundworks export: an ENFORCED\n\
         -- relation refuses to import before both of its ends exist.\n\
         -- ------------------------------\n\n",
    );
    out.push_str(&moved.join("\n"));
    out.push('\n');
    out.into_bytes()
}

/// The newest `<dir>/<YYYY-MM-DD>` folder that has a DONE file.
fn newest_done(dir: &Path) -> Result<PathBuf> {
    let mut days: Vec<PathBuf> = std::fs::read_dir(dir)
        .with_context(|| format!("no exports under {}", dir.display()))?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.is_dir() && p.file_name().is_some_and(|n| n.len() == 10) && p.join(DONE_FILE).is_file())
        .collect();
    days.sort();
    days.pop().with_context(|| {
        format!(
            "no finished export (a day folder with {DONE_FILE}) under {}",
            dir.display()
        )
    })
}

/// The day's pulse, appended to the home's metrics; one log line summarising it.
fn daily_pulse(home_root: &Path, store: &Store) -> Result<String> {
    let home = Home::open(home_root)?;
    let p = crate::pulse::take(&home, store)?;
    let path = crate::pulse::record(&home, &p)?;
    Ok(format!(
        "pulse: {} units, {} live facts ({} from asks), asks {} in {}d ({} served from knowledge, {} recorded); appended to {}",
        p.totals.units,
        p.totals.facts_live,
        p.totals.facts_from_asks,
        p.asks,
        crate::pulse::WINDOW_DAYS,
        p.asks_served_from_knowledge,
        p.recorded_in_window,
        path.display()
    ))
}

#[cfg(test)]
mod tests {
    use super::relations_last;

    #[test]
    fn relation_inserts_move_after_the_records_they_join() {
        let script = b"OPTION IMPORT;\nDEFINE TABLE evidence TYPE RELATION IN fact OUT unit ENFORCED;\nINSERT RELATION [ { id: evidence:e1, in: fact:f1, out: unit:u1 } ];\nDEFINE TABLE fact;\nINSERT [ { id: fact:f1 } ];\nINSERT [ { id: unit:u1 } ];\n";
        let out = String::from_utf8(relations_last(script)).unwrap();
        let relation = out.find("INSERT RELATION").unwrap();
        assert!(out.find("INSERT [ { id: fact:f1 } ]").unwrap() < relation);
        assert!(out.find("INSERT [ { id: unit:u1 } ]").unwrap() < relation);
        assert!(out.contains("-- RELATIONS"));
        assert_eq!(out.matches("INSERT RELATION").count(), 1);
    }

    #[test]
    fn a_script_without_relations_is_untouched() {
        let script = b"OPTION IMPORT;\nINSERT [ { id: fact:f1 } ];\n";
        assert_eq!(relations_last(script), script.to_vec());
    }
}
