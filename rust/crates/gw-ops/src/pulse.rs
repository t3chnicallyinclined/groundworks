//! Pulse: a few numbers, taken from the store with no model, that say whether the knowledge is growing,
//! being used, and holding up. One line is appended to `<home>/metrics/pulse.jsonl` per run, so the trend
//! is a file anyone can read; `history` prints it; `digest` prints one line per red condition.
//!
//! What "smarter" looks like in these numbers, over weeks:
//! - **stock**: live facts rise; the share CONFIRMED (receipt, quote or owner statement) rises; attacks
//!   (disputes, supports) appear, so disagreements are kept rather than lost.
//! - **use**: asks are answered more often with a recorded fact among the candidates ("served from
//!   knowledge"), and more passed answers are recorded; checks fail less. Since 2026-10-29 (decision
//!   1itizcerhaj5x1mjfyj2, layer 5) the hooks' telemetry says, per project, how many sessions started, how
//!   many wrote, how often the Stop hook had to ask and was obeyed, what the deny list stopped, and whether
//!   commits happened with no session at all (bypassed). Every number is defined in docs/METRICS.md with
//!   its red threshold.
//! - **health**: stale facts (their source line changed) get re-confirmed rather than piling up; units
//!   marked gone are few.
//! - **compaction**: facts per thousand units rises: more of what the files say is distilled into checked,
//!   citable statements.

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::io::Write as _;
use std::path::Path;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::home::Home;
use crate::registry::Registry;
use crate::store::Store;

/// The window for use numbers (asks, answers, recordings, sessions), in days.
pub const WINDOW_DAYS: u64 = 7;
/// A `run` receipt this soon after a deny counts the deny as converted to the sanctioned path.
pub const DENY_CONVERSION_SECS: i64 = 600;
/// A project with at least this many sessions in the window is judged on its write rate.
pub const WRITE_RATE_MIN_SESSIONS: u64 = 3;
/// Below this share of sessions writing, a project is red.
pub const WRITE_RATE_RED: f64 = 0.5;
/// A commit is seen when a hook row or a `run`/`gate` receipt of its project lies this close to it, either
/// side. A commit made through a hooked session's Bash call has its PreToolUse row seconds before it.
pub const COMMIT_COVER_SECS: i64 = 900;
/// Red when the Stop hook's p95 latency in the window is above this (A1.4: Stop reads the transcript).
pub const STOP_P95_RED_MS: i64 = 300;

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct ProjectPulse {
    pub units: u64,
    pub units_gone: u64,
    pub facts_live: u64,
    pub facts_stale: u64,
    pub facts_confirmed: u64,
    /// Facts whose source is an ask packet: answers kept as knowledge.
    pub facts_from_asks: u64,
    pub attacks: u64,
    pub evidence_links: u64,
}

/// The use numbers for one scope (a project, or everything) over one span (the window, or all time).
/// Definitions and red thresholds: docs/METRICS.md.
#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq)]
pub struct UseNumbers {
    /// Session rows (SessionStart hook) in the span.
    pub sessions: u64,
    /// Sessions with a Stop or SubagentStop row whose journal_writes_since_start is above zero.
    pub sessions_that_wrote: u64,
    /// sessions_that_wrote / sessions; 0 when there were no sessions.
    pub write_rate: f64,
    /// Stop rows decided block for no-writes.
    pub blocks_at_stop: u64,
    /// Blocks followed, in the same session, by a later Stop row that saw a journal write.
    pub blocks_converted: u64,
    /// PreToolUse rows decided deny (any rule).
    pub denies: u64,
    /// Denies followed within DENY_CONVERSION_SECS by a `run` receipt in the same project.
    pub denies_converted: u64,
    /// Hook rows carrying an error_code.
    pub hook_errors: u64,
    pub hook_latency_p50_ms: i64,
    pub hook_latency_p95_ms: i64,
    /// Journal entries (facts of every kind) recorded in the span.
    pub journal_writes: u64,
    pub writes_by_kind: BTreeMap<String, u64>,
    pub writes_by_confidence: BTreeMap<String, u64>,
    pub writes_by_agent: BTreeMap<String, u64>,
    /// CONFIRMED entries / journal_writes; 0 when there were none.
    pub confirmed_share: f64,
    /// Writes the store refused (rejection rows).
    pub store_rejections: u64,
    /// Git commits in the span on every branch of the project's local repository; None when the project
    /// has no local repository here (or the scope is the total of projects that have none).
    pub commits: Option<u64>,
    /// `commits` when the span had commits and no session: work happened with no hook running (a
    /// Copilot or VS Code session, or a session with the plugin off). None when commits is None.
    pub commits_without_sessions: Option<u64>,
    /// Commits with no hook row and no receipt of their project within COMMIT_COVER_SECS: work no hooked
    /// session accounts for, even in a span that had hooked sessions (Phase 1 of the window plan: a
    /// session with the plugin off committed beside hooked ones and the pulse stayed green). None when
    /// commits is None.
    #[serde(default)]
    pub commits_unseen: Option<u64>,
}

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct ProjectUse {
    pub window: UseNumbers,
    pub all_time: UseNumbers,
}

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct UseSection {
    pub window_days: u64,
    /// When telemetry began: the earliest session row, so "all time" has a start.
    pub telemetry_since: Option<String>,
    pub window: UseNumbers,
    pub all_time: UseNumbers,
    pub per_project: BTreeMap<String, ProjectUse>,
    /// Rejection rows in the week before the window, for the week-on-week comparison.
    pub rejections_previous_week: u64,
    /// Nearest-rank p95 of `latency_ms` per hook (`SessionStart`, `PreToolUse`, `Stop`, ...) in the window.
    #[serde(default)]
    pub hook_p95_ms_by_event: BTreeMap<String, i64>,
}

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct Pulse {
    pub at: String,
    pub machine: String,
    pub projects: u64,
    pub totals: ProjectPulse,
    pub per_project: BTreeMap<String, ProjectPulse>,
    /// Asks in the window, eval sessions left out.
    pub asks: u64,
    /// Asks by eval sessions (recording off; `by` starts with `eval:`), counted apart so the use
    /// numbers say what real sessions did. Measured 2026-10-29: in the first week every passed ask
    /// in the store came from eval runs, and the pulse could not tell.
    #[serde(default)]
    pub asks_eval: u64,
    pub asks_passed: u64,
    pub asks_failed: u64,
    pub asks_not_executed: u64,
    /// Asks whose packet showed at least one recorded fact.
    pub asks_served_from_knowledge: u64,
    /// Facts recorded from asks in the window.
    pub recorded_in_window: u64,
    /// The newest finished export's day (`YYYY-MM-DD`), if any.
    #[serde(default)]
    pub last_export: Option<String>,
    /// That export's restore drill: `PASS`, `FAIL`, or `not drilled`.
    #[serde(default)]
    pub last_drill: Option<String>,
    /// The hooks' telemetry, read back (docs/METRICS.md).
    #[serde(default, rename = "use")]
    pub use_: UseSection,
    /// The newest `groundworks cost` line (metrics/cost.jsonl), as recorded: the pulse never reads a
    /// transcript itself.
    #[serde(default)]
    pub cost: Option<crate::meter::CostLine>,
}

impl Pulse {
    /// Live facts per thousand present units: how much of the files is distilled.
    pub fn compaction(&self) -> f64 {
        if self.totals.units == 0 {
            0.0
        } else {
            1000.0 * self.totals.facts_live as f64 / self.totals.units as f64
        }
    }
}

/// Take the pulse of every registered project the store can read, and of asks in the window.
pub fn take(home: &Home, store: &Store) -> Result<Pulse> {
    let registry = Registry::load(home)?;
    let mut pulse = Pulse {
        at: now_iso(),
        machine: crate::home::machine(),
        ..Pulse::default()
    };
    for name in registry.projects.keys() {
        let Ok(p) = store.pulse_project(name) else { continue };
        let t = &mut pulse.totals;
        t.units += p.units;
        t.units_gone += p.units_gone;
        t.facts_live += p.facts_live;
        t.facts_stale += p.facts_stale;
        t.facts_confirmed += p.facts_confirmed;
        t.facts_from_asks += p.facts_from_asks;
        t.attacks += p.attacks;
        t.evidence_links += p.evidence_links;
        pulse.recorded_in_window += store.facts_from_asks_since(name, WINDOW_DAYS).unwrap_or(0);
        pulse.per_project.insert(name.clone(), p);
        pulse.projects += 1;
    }
    let (last_export, last_drill) = exports_status(home);
    pulse.last_export = last_export;
    pulse.last_drill = last_drill;
    let asks = store.ask_packets_since(WINDOW_DAYS)?;
    for row in &asks {
        if row["by"].as_str().is_some_and(|b| b.starts_with("eval:")) {
            pulse.asks_eval += 1;
            continue;
        }
        pulse.asks += 1;
        match row["status"].as_str().unwrap_or("") {
            "PASS" => pulse.asks_passed += 1,
            "FAIL" => pulse.asks_failed += 1,
            "NOT_EXECUTED" => pulse.asks_not_executed += 1,
            _ => {}
        }
        let served = row["candidates"]
            .as_array()
            .is_some_and(|cs| cs.iter().any(|c| c.get("fact").is_some_and(|f| !f.is_null())));
        pulse.asks_served_from_knowledge += u64::from(served);
    }
    pulse.use_ = take_use(home, store, &registry, &pulse.at)?;
    pulse.cost = crate::meter::latest(home);
    Ok(pulse)
}

/// Everything the hooks recorded, read once and cut two ways: the window and all time, per project and
/// in total. One query per table plus one per project for its journal, receipts and commits.
fn take_use(home: &Home, store: &Store, registry: &Registry, now: &str) -> Result<UseSection> {
    let now_secs = epoch_secs(now).unwrap_or(0);
    let window_since = iso_from_secs(now_secs - WINDOW_DAYS as i64 * 86_400);
    let previous_since = iso_from_secs(now_secs - 2 * WINDOW_DAYS as i64 * 86_400);
    let sessions = store.sessions_since(None)?;
    let events = store.hook_events_since(None)?;
    let rejections = store.rejections_since(None)?;
    let telemetry_since = sessions
        .iter()
        .filter_map(|s| s["started_at"].as_str())
        .map(seconds)
        .min();
    let mut section = UseSection {
        window_days: WINDOW_DAYS,
        telemetry_since: telemetry_since.clone(),
        rejections_previous_week: rejections
            .iter()
            .filter(|r| {
                r["at"]
                    .as_str()
                    .is_some_and(|t| seconds(t) >= previous_since && seconds(t) < window_since)
            })
            .count() as u64,
        ..UseSection::default()
    };
    // The receipts, journal rows and commits are per project; the telemetry rows carry the project name.
    let mut receipts: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut journal: BTreeMap<String, Vec<(String, String, String, String)>> = BTreeMap::new();
    let mut commits_window: BTreeMap<String, Option<Vec<i64>>> = BTreeMap::new();
    let mut commits_all: BTreeMap<String, Option<Vec<i64>>> = BTreeMap::new();
    for (name, project) in &registry.projects {
        receipts.insert(name.clone(), store.receipt_times_since(name, None).unwrap_or_default());
        let rows = store.facts_query(name, None, None, 100_000).unwrap_or_default();
        journal.insert(
            name.clone(),
            rows.iter()
                .map(|f| {
                    (
                        seconds(&f.at),
                        f.kind.clone(),
                        f.grade.clone(),
                        f.by.clone().unwrap_or_else(|| "?".to_owned()),
                    )
                })
                .collect(),
        );
        let dir = project.path(&home.workspace);
        commits_window.insert(name.clone(), git_commit_times_since(&dir, &window_since));
        commits_all.insert(
            name.clone(),
            telemetry_since
                .as_deref()
                .and_then(|since| git_commit_times_since(&dir, since)),
        );
    }
    let scope = |project: Option<&str>, since: Option<&str>| {
        use_numbers(
            UseInput {
                sessions: &sessions,
                events: &events,
                rejections: &rejections,
                receipts: &receipts,
                journal: &journal,
                commits: if since.is_some() { &commits_window } else { &commits_all },
            },
            project,
            since,
        )
    };
    for name in registry.projects.keys() {
        section.per_project.insert(
            name.clone(),
            ProjectUse {
                window: scope(Some(name), Some(&window_since)),
                all_time: scope(Some(name), None),
            },
        );
    }
    let mut by_event: BTreeMap<String, Vec<i64>> = BTreeMap::new();
    for e in &events {
        if let (Some(hook), Some(ms), Some(at)) = (e["hook"].as_str(), e["latency_ms"].as_i64(), e["at"].as_str())
            && seconds(at) >= window_since
        {
            by_event.entry(hook.to_owned()).or_default().push(ms);
        }
    }
    section.hook_p95_ms_by_event = by_event
        .into_iter()
        .map(|(hook, mut v)| {
            v.sort_unstable();
            (hook, percentile(&v, 95))
        })
        .collect();
    section.window = scope(None, Some(&window_since));
    section.all_time = scope(None, None);
    Ok(section)
}

/// The rows a use number is computed from (borrowed once, cut many ways).
pub struct UseInput<'a> {
    pub sessions: &'a [Value],
    pub events: &'a [Value],
    pub rejections: &'a [Value],
    pub receipts: &'a BTreeMap<String, Vec<String>>,
    pub journal: &'a BTreeMap<String, Vec<(String, String, String, String)>>,
    /// Each project's commit times (epoch seconds) in the span; None when it has no repository here.
    pub commits: &'a BTreeMap<String, Option<Vec<i64>>>,
}

/// The use numbers for `project` (None: every project and the sessions outside any) since `since`
/// (None: all time). Pure over the rows, so a test can feed it a fixture.
pub fn use_numbers(input: UseInput<'_>, project: Option<&str>, since: Option<&str>) -> UseNumbers {
    let in_scope = |row: &Value, time_field: &str| {
        project.is_none_or(|p| row["project"] == p)
            && since.is_none_or(|s| row[time_field].as_str().is_some_and(|t| seconds(t).as_str() >= s))
    };
    let mut n = UseNumbers::default();
    let session_ids: Vec<&str> = input
        .sessions
        .iter()
        .filter(|s| in_scope(s, "started_at"))
        .filter_map(|s| s["id"].as_str())
        .collect();
    n.sessions = session_ids.len() as u64;
    let events: Vec<&Value> = input.events.iter().filter(|e| in_scope(e, "at")).collect();
    let stops: Vec<&Value> = events
        .iter()
        .copied()
        .filter(|e| e["hook"] == "Stop" || e["hook"] == "SubagentStop")
        .collect();
    let wrote = |sid: &str| {
        stops
            .iter()
            .any(|e| e["session"] == sid && e["journal_writes_since_start"].as_u64().is_some_and(|w| w > 0))
    };
    // A session row's id reads `session:⟨id⟩`; the hook rows carry the bare id.
    n.sessions_that_wrote = session_ids.iter().filter(|id| wrote(bare_id(id))).count() as u64;
    n.write_rate = ratio(n.sessions_that_wrote, n.sessions);
    let blocks: Vec<&Value> = stops
        .iter()
        .copied()
        .filter(|e| e["decision"] == "block" && e["rule_id"] == "no-writes")
        .collect();
    n.blocks_at_stop = blocks.len() as u64;
    n.blocks_converted = blocks
        .iter()
        .filter(|b| {
            stops.iter().any(|e| {
                e["session"] == b["session"]
                    && e["at"].as_str() > b["at"].as_str()
                    && e["journal_writes_since_start"].as_u64().is_some_and(|w| w > 0)
            })
        })
        .count() as u64;
    let denies: Vec<&Value> = events
        .iter()
        .copied()
        .filter(|e| e["hook"] == "PreToolUse" && e["decision"] == "deny")
        .collect();
    n.denies = denies.len() as u64;
    n.denies_converted = denies
        .iter()
        .filter(|d| {
            let Some(at) = d["at"].as_str().and_then(epoch_secs) else {
                return false;
            };
            d["project"].as_str().is_some_and(|p| {
                input.receipts.get(p).is_some_and(|times| {
                    times
                        .iter()
                        .filter_map(|t| epoch_secs(t))
                        .any(|r| r >= at && r - at <= DENY_CONVERSION_SECS)
                })
            })
        })
        .count() as u64;
    n.hook_errors = events.iter().filter(|e| e["error_code"].is_string()).count() as u64;
    let mut latencies: Vec<i64> = events.iter().filter_map(|e| e["latency_ms"].as_i64()).collect();
    latencies.sort_unstable();
    n.hook_latency_p50_ms = percentile(&latencies, 50);
    n.hook_latency_p95_ms = percentile(&latencies, 95);
    for (name, rows) in input.journal {
        if project.is_some_and(|p| p != name) {
            continue;
        }
        for (at, kind, grade, by) in rows {
            if since.is_some_and(|s| at.as_str() < s) {
                continue;
            }
            n.journal_writes += 1;
            *n.writes_by_kind.entry(kind.clone()).or_default() += 1;
            *n.writes_by_confidence.entry(grade.clone()).or_default() += 1;
            *n.writes_by_agent.entry(by.clone()).or_default() += 1;
        }
    }
    n.confirmed_share = ratio(
        n.writes_by_confidence.get("CONFIRMED").copied().unwrap_or(0),
        n.journal_writes,
    );
    n.store_rejections = input.rejections.iter().filter(|r| in_scope(r, "at")).count() as u64;
    let mut commits: Option<u64> = None;
    let mut bypassed: Option<u64> = None;
    let mut unseen: Option<u64> = None;
    for (name, times) in input.commits {
        if project.is_some_and(|p| p != name) {
            continue;
        }
        if let Some(times) = times {
            let c = &(times.len() as u64);
            commits = Some(commits.unwrap_or(0) + c);
            // Every hook row and receipt of this project, whatever the span: a commit at the window's
            // edge may be covered by a row just before it.
            let mut marks: Vec<i64> = input
                .events
                .iter()
                .filter(|e| e["project"] == name.as_str())
                .filter_map(|e| e["at"].as_str().and_then(epoch_secs))
                .chain(
                    input
                        .receipts
                        .get(name)
                        .into_iter()
                        .flatten()
                        .filter_map(|t| epoch_secs(t)),
                )
                .collect();
            marks.sort_unstable();
            let covered = |t: i64| {
                let i = marks.partition_point(|m| *m < t - COMMIT_COVER_SECS);
                marks.get(i).is_some_and(|m| *m <= t + COMMIT_COVER_SECS)
            };
            unseen = Some(unseen.unwrap_or(0) + times.iter().filter(|t| !covered(**t)).count() as u64);
            let sessions_here = session_ids.len() as u64;
            let sessions_here = if project.is_some() {
                sessions_here
            } else {
                input
                    .sessions
                    .iter()
                    .filter(|s| s["project"] == name.as_str() && in_scope(s, "started_at"))
                    .count() as u64
            };
            let b = if *c > 0 && sessions_here == 0 { *c } else { 0 };
            bypassed = Some(bypassed.unwrap_or(0) + b);
        }
    }
    n.commits = commits;
    n.commits_without_sessions = bypassed;
    n.commits_unseen = unseen;
    n
}

/// `session:⟨abc⟩` or `session:abc` -> `abc`.
fn bare_id(id: &str) -> &str {
    id.rsplit_once(':')
        .map_or(id, |(_, k)| k)
        .trim_matches(|c| c == '\u{27e8}' || c == '\u{27e9}' || c == '`')
}

fn ratio(a: u64, b: u64) -> f64 {
    if b == 0 { 0.0 } else { a as f64 / b as f64 }
}

/// The nearest-rank percentile of a sorted list; 0 for an empty one.
fn percentile(sorted: &[i64], p: usize) -> i64 {
    if sorted.is_empty() {
        return 0;
    }
    let rank = (p * sorted.len()).div_ceil(100).max(1);
    sorted[rank - 1]
}

/// The commit times (epoch seconds, committer date) on every branch of the repository at `dir` since
/// `since` (RFC 3339); None when `dir` is not a git repository on this machine.
pub fn git_commit_times_since(dir: &Path, since: &str) -> Option<Vec<i64>> {
    if !dir.join(".git").exists() {
        return None;
    }
    let out = std::process::Command::new("git")
        .args(["--no-optional-locks", "-C"])
        .arg(dir)
        .args(["log", "--all", "--format=%ct", &format!("--since={since}")])
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    out.status.success().then(|| {
        String::from_utf8_lossy(&out.stdout)
            .lines()
            .filter_map(|l| l.trim().parse().ok())
            .collect()
    })
}

/// The red conditions of this pulse, one line each (docs/METRICS.md). Empty means nothing is red, which
/// is only meaningful once sessions are being recorded: the first line says so when they are not.
pub fn digest(p: &Pulse) -> Vec<String> {
    let mut red = Vec::new();
    if p.machine == crate::home::UNKNOWN_MACHINE {
        red.push("RED machine unknown: this box cannot name itself (doctor fails on it)".to_owned());
    }
    let u = &p.use_;
    if u.all_time.sessions == 0 {
        red.push("RED no sessions recorded yet: the hooks have never run against this store (install the plugin, open a session, run doctor)".to_owned());
    }
    if u.window.hook_errors > 0 {
        red.push(format!(
            "RED hook_errors {} in {}d: a hook failed internally (hook_event.error_code names the kind)",
            u.window.hook_errors, u.window_days
        ));
    }
    for (name, pu) in &u.per_project {
        if let Some(b) = pu.window.commits_without_sessions
            && b > 0
        {
            red.push(format!(
                "RED {name}: {b} commit(s) in {}d with no session: work bypassed the hooks (Copilot/VS Code, or plugin off)",
                u.window_days
            ));
        } else if let Some(b) = pu.window.commits_unseen
            && b > 0
        {
            red.push(format!(
                "RED {name}: {b} commit(s) in {}d with no hook row or receipt within {} min: a session beside the hooked ones bypassed them (plugin off, or a client without hooks)",
                u.window_days,
                COMMIT_COVER_SECS / 60
            ));
        }
        if pu.window.sessions >= WRITE_RATE_MIN_SESSIONS && pu.window.write_rate < WRITE_RATE_RED {
            red.push(format!(
                "RED {name}: write_rate {:.2} over {} sessions in {}d (below {WRITE_RATE_RED}): sessions start but do not record",
                pu.window.write_rate, pu.window.sessions, u.window_days
            ));
        }
    }
    if let Some(stop) = u.hook_p95_ms_by_event.get("Stop")
        && *stop > STOP_P95_RED_MS
    {
        red.push(format!(
            "RED Stop hook p95 {stop} ms in {}d (above {STOP_P95_RED_MS} ms): the Stop hook reads the whole transcript at every turn end",
            u.window_days
        ));
    }
    if let Some(cost) = &p.cost {
        red.extend(
            cost.red
                .iter()
                .map(|r| format!("{r} (cost {} on {})", &cost.at[..cost.at.len().min(10)], cost.machine)),
        );
    }
    if u.window.store_rejections > u.rejections_previous_week && u.window.store_rejections > 0 {
        red.push(format!(
            "RED store rejections rising: {} this week vs {} the week before (CONFIRMED without evidence, secret-shaped text, ...)",
            u.window.store_rejections, u.rejections_previous_week
        ));
    }
    red
}

/// The newest finished export under the home's backups, and the verdict of its restore drill (the
/// first word of its DRILL file), so a pulse says whether the data could be got back.
fn exports_status(home: &Home) -> (Option<String>, Option<String>) {
    let dir = crate::supervise::backups_dir(home);
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return (None, None);
    };
    let mut days: Vec<std::path::PathBuf> = entries
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| {
            p.is_dir() && p.file_name().is_some_and(|n| n.len() == 10) && p.join(crate::supervise::DONE_FILE).is_file()
        })
        .collect();
    days.sort();
    let Some(newest) = days.pop() else {
        return (None, None);
    };
    let day = newest.file_name().map(|n| n.to_string_lossy().into_owned());
    let drill = std::fs::read_to_string(newest.join(crate::supervise::DRILL_FILE))
        .ok()
        .and_then(|t| t.split_whitespace().next().map(str::to_owned))
        .unwrap_or_else(|| "not drilled".to_owned());
    (day, Some(drill))
}

/// Append the pulse to `metrics/pulse.jsonl` in the home; returns the path.
pub fn record(home: &Home, pulse: &Pulse) -> Result<std::path::PathBuf> {
    let dir = home.root.join("metrics");
    std::fs::create_dir_all(&dir)?;
    let path = dir.join("pulse.jsonl");
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&path)?;
    writeln!(f, "{}", serde_json::to_string(pulse)?)?;
    Ok(path)
}

fn counts(map: &BTreeMap<String, u64>) -> String {
    if map.is_empty() {
        return "none".to_owned();
    }
    map.iter()
        .map(|(k, v)| format!("{k} {v}"))
        .collect::<Vec<_>>()
        .join(", ")
}

/// The pulse as a reader sees it.
pub fn render(p: &Pulse) -> String {
    let t = &p.totals;
    let pct = |a: u64, b: u64| if b == 0 { 0.0 } else { 100.0 * a as f64 / b as f64 };
    let mut s = format!(
        "pulse {} on {} | {} projects\n\
         stock   units {} ({} gone) | facts live {} (CONFIRMED {:.0}%), stale {} | from asks {} | attacks {} | evidence links {}\n\
         compaction  {:.1} live facts per 1000 units\n\
         use ({}d)  asks {} (eval sessions apart: {}) | passed {} | failed {} | not executed {} | served from knowledge {} ({:.0}%) | answers recorded {}\n\
         ops     last export {} | restore drill {}\n",
        &p.at[..p.at.len().min(19)],
        p.machine,
        p.projects,
        t.units,
        t.units_gone,
        t.facts_live,
        pct(t.facts_confirmed, t.facts_live + t.facts_stale),
        t.facts_stale,
        t.facts_from_asks,
        t.attacks,
        t.evidence_links,
        p.compaction(),
        WINDOW_DAYS,
        p.asks,
        p.asks_eval,
        p.asks_passed,
        p.asks_failed,
        p.asks_not_executed,
        p.asks_served_from_knowledge,
        pct(p.asks_served_from_knowledge, p.asks),
        p.recorded_in_window,
        p.last_export.as_deref().unwrap_or("none"),
        p.last_drill.as_deref().unwrap_or("none")
    );
    let u = &p.use_;
    for (label, n) in [
        (format!("{}d", u.window_days), &u.window),
        ("all".to_owned(), &u.all_time),
    ] {
        let _ = writeln!(
            s,
            "hooks ({label:>3})  sessions {} (wrote {}, rate {:.2}) | stop blocks {} (converted {}) | denies {} (converted {}) | hook errors {} | latency p50 {} ms p95 {} ms\n\
             journal ({label:>3})  writes {} (CONFIRMED {:.0}%) | by kind: {} | by confidence: {} | by agent: {} | store rejections {} | commits {} (without a session: {}, unseen by any hook: {})",
            n.sessions,
            n.sessions_that_wrote,
            n.write_rate,
            n.blocks_at_stop,
            n.blocks_converted,
            n.denies,
            n.denies_converted,
            n.hook_errors,
            n.hook_latency_p50_ms,
            n.hook_latency_p95_ms,
            n.journal_writes,
            100.0 * n.confirmed_share,
            counts(&n.writes_by_kind),
            counts(&n.writes_by_confidence),
            counts(&n.writes_by_agent),
            n.store_rejections,
            n.commits.map_or("n/a".to_owned(), |c| c.to_string()),
            n.commits_without_sessions.map_or("n/a".to_owned(), |c| c.to_string()),
            n.commits_unseen.map_or("n/a".to_owned(), |c| c.to_string()),
        );
    }
    if let Some(since) = &u.telemetry_since {
        let _ = writeln!(
            s,
            "telemetry since {since}; week before this one: {} rejection(s)",
            u.rejections_previous_week
        );
    }
    if !u.hook_p95_ms_by_event.is_empty() {
        let stop = u
            .hook_p95_ms_by_event
            .get("Stop")
            .map_or("n/a".to_owned(), |v| format!("{v} ms"));
        let others: Vec<String> = u
            .hook_p95_ms_by_event
            .iter()
            .filter(|(k, _)| k.as_str() != "Stop")
            .map(|(k, v)| format!("{k} {v} ms"))
            .collect();
        let _ = writeln!(s, "hook p95 ({}d)  Stop {stop} | {}", u.window_days, others.join(" | "));
    }
    match &p.cost {
        None => s.push_str("cost    none recorded yet (groundworks cost)\n"),
        Some(c) => {
            let w = &c.totals.window;
            let pw = &c.totals.previous_window;
            let _ = writeln!(
                s,
                "cost ({}d, taken {} on {})  tokens per human turn {} (week before {}) | main ctx p90 {} | overhead share {} | asks passed {}/{}, gw tokens per passed {} | re-reads per session median {} | work per Mtok {}",
                c.window_days,
                &c.at[..c.at.len().min(16)],
                c.machine,
                crate::meter::fmt_n(w.tokens_per_human_turn),
                crate::meter::fmt_n(pw.tokens_per_human_turn),
                crate::meter::fmt_n(w.main_ctx_p90 as f64),
                w.overhead_share
                    .map_or("off".to_owned(), |v| format!("{:.2}% (est.)", 100.0 * v)),
                c.asks.passed,
                c.asks.asks,
                c.asks
                    .gw_tokens_per_passed
                    .map_or("off".to_owned(), crate::meter::fmt_n),
                w.rereads_per_session_median
                    .map_or("off".to_owned(), |v| format!("{v:.1}")),
                w.work_per_mtok.map_or("n/a".to_owned(), |v| format!("{v:.3}")),
            );
        }
    }
    let mut rows: Vec<(&String, &ProjectPulse)> = p.per_project.iter().collect();
    rows.sort_by(|a, b| (b.1.facts_live + b.1.facts_stale).cmp(&(a.1.facts_live + a.1.facts_stale)));
    for (name, pp) in rows.iter().take(8) {
        let pu = u.per_project.get(*name).map(|x| &x.window);
        let _ = writeln!(
            s,
            "  {:<22} units {:>6} | facts {:>4} live, {:>3} stale, {:>3} from asks | attacks {} | {}d: sessions {} wrote {} writes {} commits {}",
            name,
            pp.units,
            pp.facts_live,
            pp.facts_stale,
            pp.facts_from_asks,
            pp.attacks,
            u.window_days,
            pu.map_or(0, |x| x.sessions),
            pu.map_or(0, |x| x.sessions_that_wrote),
            pu.map_or(0, |x| x.journal_writes),
            pu.and_then(|x| x.commits).map_or("n/a".to_owned(), |c| c.to_string()),
        );
    }
    let red = digest(p);
    if red.is_empty() {
        s.push_str("digest: nothing red\n");
    } else {
        for line in red {
            let _ = writeln!(s, "digest: {line}");
        }
    }
    s
}

/// The trend: one line per recorded pulse, newest last.
pub fn history(home: &Home) -> Result<String> {
    let path = home.root.join("metrics").join("pulse.jsonl");
    let text = std::fs::read_to_string(&path).with_context(|| format!("no pulse yet at {}", path.display()))?;
    let mut s = String::from(
        "when                 units  facts  stale  from-asks  attacks  compaction  asks  served%  recorded  sessions  wrote  blocks  denies  drill\n",
    );
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        let Ok(p) = serde_json::from_str::<Pulse>(line) else {
            continue;
        };
        let served = if p.asks == 0 {
            0.0
        } else {
            100.0 * p.asks_served_from_knowledge as f64 / p.asks as f64
        };
        let w = &p.use_.window;
        let _ = writeln!(
            s,
            "{:<20} {:>6} {:>6} {:>6} {:>10} {:>8} {:>11.1} {:>5} {:>7.0} {:>9} {:>9} {:>6} {:>7} {:>7}  {}",
            &p.at[..p.at.len().min(19)],
            p.totals.units,
            p.totals.facts_live,
            p.totals.facts_stale,
            p.totals.facts_from_asks,
            p.totals.attacks,
            p.compaction(),
            p.asks,
            served,
            p.recorded_in_window,
            w.sessions,
            w.sessions_that_wrote,
            w.blocks_at_stop,
            w.denies,
            p.last_drill.as_deref().unwrap_or("-")
        );
    }
    Ok(s)
}

/// `2026-10-25T04:19:34.853088800Z` -> `2026-10-25T04:19:34Z` (comparable as text).
fn seconds(at: &str) -> String {
    let at = at.trim_matches(|c| c == 'd' || c == '\'');
    match (at.get(..19), at.len() > 19) {
        (Some(head), true) => format!("{head}Z"),
        _ => at.to_owned(),
    }
}

/// Seconds since the epoch of an RFC 3339 UTC time (`YYYY-MM-DDTHH:MM:SS[.frac]Z`), or None.
pub fn epoch_secs(ts: &str) -> Option<i64> {
    let ts = ts.trim_matches(|c| c == 'd' || c == '\'');
    let b = ts.as_bytes();
    if b.len() < 19 || b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || b[13] != b':' || b[16] != b':' {
        return None;
    }
    let num = |r: std::ops::Range<usize>| ts.get(r)?.parse::<i64>().ok();
    let (y, m, d) = (num(0..4)?, num(5..7)?, num(8..10)?);
    let (hh, mm, ss) = (num(11..13)?, num(14..16)?, num(17..19)?);
    // Days from civil (Howard Hinnant's algorithm), the inverse of iso_from_secs.
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * 86_400 + hh * 3600 + mm * 60 + ss)
}

/// Now, as `YYYY-MM-DDTHH:MM:SSZ`.
pub fn now_iso() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs()) as i64;
    iso_from_secs(secs)
}

/// Seconds since the epoch as `YYYY-MM-DDTHH:MM:SSZ`.
pub fn iso_from_secs(secs: i64) -> String {
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn compaction_is_live_facts_per_thousand_units_and_render_holds_the_numbers() {
        let mut p = Pulse::default();
        p.totals.units = 4000;
        p.totals.facts_live = 10;
        p.totals.facts_stale = 2;
        p.totals.facts_confirmed = 6;
        p.asks = 4;
        p.asks_served_from_knowledge = 3;
        assert!((p.compaction() - 2.5).abs() < 1e-9);
        let text = render(&p);
        assert!(text.contains("2.5 live facts per 1000 units"), "{text}");
        assert!(text.contains("served from knowledge 3 (75%)"), "{text}");
        assert!(text.contains("CONFIRMED 50%"), "{text}");
        assert_eq!(Pulse::default().compaction(), 0.0);
    }

    #[test]
    fn times_round_trip_and_compare() {
        for ts in [
            "2026-10-29T20:25:25Z",
            "2000-02-29T00:00:00Z",
            "1970-01-01T00:00:00Z",
            "2024-12-31T23:59:59Z",
        ] {
            assert_eq!(iso_from_secs(epoch_secs(ts).unwrap()), ts);
        }
        assert_eq!(
            epoch_secs("2026-10-29T20:25:25.853Z"),
            epoch_secs("2026-10-29T20:25:25Z")
        );
        assert_eq!(epoch_secs("yesterday"), None);
        assert_eq!(seconds("2026-10-25T04:19:34.853088800Z"), "2026-10-25T04:19:34Z");
        assert_eq!(percentile(&[1, 2, 3, 4, 10], 50), 3);
        assert_eq!(percentile(&[1, 2, 3, 4, 10], 95), 10);
        assert_eq!(percentile(&[], 50), 0);
        assert_eq!(bare_id("session:\u{27e8}abc-1\u{27e9}"), "abc-1");
        assert_eq!(bare_id("abc"), "abc");
    }

    fn fixture() -> (Vec<Value>, Vec<Value>, Vec<Value>) {
        let sessions = vec![
            json!({"id": "session:\u{27e8}s1\u{27e9}", "project": "demo", "started_at": "2026-10-29T10:00:00Z"}),
            json!({"id": "session:\u{27e8}s2\u{27e9}", "project": "demo", "started_at": "2026-10-29T11:00:00Z"}),
            json!({"id": "session:\u{27e8}s3\u{27e9}", "project": "other", "started_at": "2026-10-01T11:00:00Z"}),
            json!({"id": "session:\u{27e8}s4\u{27e9}", "project": null, "started_at": "2026-10-29T12:00:00Z"}),
        ];
        let events = vec![
            json!({"session": "s1", "project": "demo", "hook": "Stop", "at": "2026-10-29T10:30:00Z", "decision": "block", "rule_id": "no-writes", "journal_writes_since_start": 0, "latency_ms": 40}),
            json!({"session": "s1", "project": "demo", "hook": "Stop", "at": "2026-10-29T10:31:00Z", "decision": "approve", "journal_writes_since_start": 1, "latency_ms": 60}),
            json!({"session": "s2", "project": "demo", "hook": "Stop", "at": "2026-10-29T11:30:00Z", "decision": "block", "rule_id": "no-writes", "journal_writes_since_start": 0, "latency_ms": 50}),
            json!({"session": "s2", "project": "demo", "hook": "PreToolUse", "at": "2026-10-29T11:10:00Z", "decision": "deny", "rule_id": "git-push", "latency_ms": 20}),
            json!({"session": "s2", "project": "demo", "hook": "PreToolUse", "at": "2026-10-29T11:40:00Z", "decision": "deny", "rule_id": "git-push", "latency_ms": 900, "error_code": "other"}),
            json!({"session": "s3", "project": "other", "hook": "Stop", "at": "2026-10-01T11:30:00Z", "decision": "approve", "journal_writes_since_start": 2, "latency_ms": 10}),
        ];
        let rejections = vec![
            json!({"at": "2026-10-29T11:00:00Z", "surface": "journal_add", "project": "demo", "reason": "confirmed-needs-evidence"}),
            json!({"at": "2026-10-20T11:00:00Z", "surface": "note", "project": "demo", "reason": "kv-secret"}),
        ];
        (sessions, events, rejections)
    }

    #[test]
    fn use_numbers_are_cut_by_project_and_window() {
        let (sessions, events, rejections) = fixture();
        let receipts = BTreeMap::from([("demo".to_owned(), vec!["2026-10-29T11:15:00Z".to_owned()])]);
        let journal = BTreeMap::from([
            (
                "demo".to_owned(),
                vec![
                    (
                        "2026-10-29T10:31:00Z".to_owned(),
                        "status".to_owned(),
                        "INFERRED".to_owned(),
                        "gw-engineer".to_owned(),
                    ),
                    (
                        "2026-10-29T10:32:00Z".to_owned(),
                        "gotcha".to_owned(),
                        "CONFIRMED".to_owned(),
                        "gw-engineer".to_owned(),
                    ),
                ],
            ),
            (
                "other".to_owned(),
                vec![(
                    "2026-10-01T11:30:00Z".to_owned(),
                    "fact".to_owned(),
                    "CONFIRMED".to_owned(),
                    "owner".to_owned(),
                )],
            ),
        ]);
        let at = |ts: &str| epoch_secs(ts).unwrap();
        // demo: two commits beside its hooked sessions' rows, one at 15:00 with no hook row near it
        // (Phase 1: a session with the plugin off, beside hooked ones). other: five, no session this week.
        let commits = BTreeMap::from([
            (
                "demo".to_owned(),
                Some(vec![
                    at("2026-10-29T10:35:00Z"),
                    at("2026-10-29T11:16:00Z"),
                    at("2026-10-29T15:00:00Z"),
                ]),
            ),
            ("other".to_owned(), Some(vec![at("2026-10-25T09:00:00Z"); 5])),
            ("norepo".to_owned(), None),
        ]);
        let input = UseInput {
            sessions: &sessions,
            events: &events,
            rejections: &rejections,
            receipts: &receipts,
            journal: &journal,
            commits: &commits,
        };
        let since = "2026-10-22T00:00:00Z";
        let demo = use_numbers(UseInput { ..input }, Some("demo"), Some(since));
        assert_eq!(demo.sessions, 2);
        assert_eq!(
            demo.sessions_that_wrote, 1,
            "s1 wrote, s2 was blocked and never came back"
        );
        assert!((demo.write_rate - 0.5).abs() < 1e-9);
        assert_eq!((demo.blocks_at_stop, demo.blocks_converted), (2, 1));
        assert_eq!(
            (demo.denies, demo.denies_converted),
            (2, 1),
            "the 11:10 deny had a run receipt at 11:15"
        );
        assert_eq!(demo.hook_errors, 1);
        assert_eq!((demo.hook_latency_p50_ms, demo.hook_latency_p95_ms), (50, 900));
        assert_eq!(demo.journal_writes, 2);
        assert_eq!(demo.writes_by_kind.get("gotcha"), Some(&1));
        assert!((demo.confirmed_share - 0.5).abs() < 1e-9);
        assert_eq!(demo.store_rejections, 1, "the 10-20 rejection is outside the window");
        assert_eq!((demo.commits, demo.commits_without_sessions), (Some(3), Some(0)));
        assert_eq!(
            demo.commits_unseen,
            Some(1),
            "10:35 is beside a Stop row, 11:16 beside a receipt; 15:00 has nothing within 15 min"
        );
        let input = UseInput {
            sessions: &sessions,
            events: &events,
            rejections: &rejections,
            receipts: &receipts,
            journal: &journal,
            commits: &commits,
        };
        let other = use_numbers(input, Some("other"), Some(since));
        assert_eq!(other.sessions, 0);
        assert_eq!(
            (other.commits, other.commits_without_sessions),
            (Some(5), Some(5)),
            "commits with no session in the window are bypassed work"
        );
        assert_eq!(other.commits_unseen, Some(5));
        let input = UseInput {
            sessions: &sessions,
            events: &events,
            rejections: &rejections,
            receipts: &receipts,
            journal: &journal,
            commits: &commits,
        };
        let all = use_numbers(input, None, None);
        assert_eq!(all.sessions, 4, "the session outside any project counts too");
        assert_eq!(all.sessions_that_wrote, 2);
        assert_eq!(all.journal_writes, 3);
        assert_eq!(all.store_rejections, 2);
        assert_eq!(
            (all.commits, all.commits_without_sessions),
            (Some(8), Some(0)),
            "other had a session all time"
        );
        assert_eq!(
            all.commits_unseen,
            Some(6),
            "but no hook row is near any of other's commits"
        );
        let input = UseInput {
            sessions: &sessions,
            events: &events,
            rejections: &rejections,
            receipts: &receipts,
            journal: &journal,
            commits: &commits,
        };
        assert_eq!(use_numbers(input, Some("norepo"), None).commits, None);
    }

    #[test]
    fn the_digest_names_every_red_condition_and_nothing_else() {
        let mut p = Pulse {
            machine: "THERIGXD".to_owned(),
            ..Pulse::default()
        };
        p.use_.window_days = 7;
        assert_eq!(digest(&p).len(), 1, "no sessions yet is itself red: {:?}", digest(&p));
        p.use_.all_time.sessions = 5;
        assert!(digest(&p).is_empty(), "{:?}", digest(&p));
        p.machine = crate::home::UNKNOWN_MACHINE.to_owned();
        p.use_.window.hook_errors = 2;
        p.use_.window.store_rejections = 3;
        p.use_.rejections_previous_week = 1;
        p.use_.per_project.insert(
            "bypassed".to_owned(),
            ProjectUse {
                window: UseNumbers {
                    commits: Some(4),
                    commits_without_sessions: Some(4),
                    ..UseNumbers::default()
                },
                all_time: UseNumbers::default(),
            },
        );
        p.use_.per_project.insert(
            "quiet".to_owned(),
            ProjectUse {
                window: UseNumbers {
                    sessions: 3,
                    sessions_that_wrote: 1,
                    write_rate: 1.0 / 3.0,
                    ..UseNumbers::default()
                },
                all_time: UseNumbers::default(),
            },
        );
        p.use_.per_project.insert(
            "small".to_owned(),
            ProjectUse {
                window: UseNumbers {
                    sessions: 2,
                    write_rate: 0.0,
                    ..UseNumbers::default()
                },
                all_time: UseNumbers::default(),
            },
        );
        p.use_.per_project.insert(
            "beside".to_owned(),
            ProjectUse {
                window: UseNumbers {
                    sessions: 1,
                    sessions_that_wrote: 1,
                    write_rate: 1.0,
                    commits: Some(2),
                    commits_without_sessions: Some(0),
                    commits_unseen: Some(1),
                    ..UseNumbers::default()
                },
                all_time: UseNumbers::default(),
            },
        );
        let red = digest(&p);
        assert_eq!(red.len(), 6, "{red:?}");
        assert!(
            red.iter()
                .any(|l| l.contains("beside: 1 commit(s)") && l.contains("no hook row"))
        );
        assert!(
            !red.iter().any(|l| l.contains("bypassed:") && l.contains("no hook row")),
            "a project with no session at all is named once, by the older line"
        );
        assert!(red.iter().any(|l| l.contains("machine unknown")));
        assert!(red.iter().any(|l| l.contains("hook_errors 2")));
        assert!(red.iter().any(|l| l.contains("bypassed: 4 commit(s)")));
        assert!(red.iter().any(|l| l.contains("quiet: write_rate 0.33")));
        assert!(red.iter().any(|l| l.contains("rejections rising: 3 this week vs 1")));
        assert!(
            !red.iter().any(|l| l.contains("small:")),
            "two sessions are too few to judge"
        );
        let text = render(&p);
        assert!(text.contains("digest: RED"), "{text}");
    }
}
