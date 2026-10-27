//! Knowledge that is current when it is used, with no scheduler and no hook. Before a read (`ask`,
//! `grep`, `search`) touches a project, its working tree is fingerprinted (HEAD, `git status`, and the
//! modification times of the files status names); when the fingerprint differs from the one stamped at
//! the last intake, the project is ingested first. Intake skips units whose text is unchanged, so a
//! refresh after a small edit costs about a second. At the server's first store open every project on
//! this machine is refreshed in the background, and a pulse is taken when the last one is over a day old.
//!
//! Stamps live in `<home>/db/intake-stamps.json` (beside the store's data, not in the home's git).
//! `GROUNDWORKS_NO_REFRESH` turns all of it off, which an eval run needs so that the store does not move
//! under its packets.

use std::collections::BTreeMap;
use std::path::Path;
use std::process::{Command, Stdio};

use anyhow::Result;
use serde::{Deserialize, Serialize};

use crate::home::Home;
use crate::registry::Registry;
use crate::store::{Store, sha256_hex};

pub const ENV_OFF: &str = "GROUNDWORKS_NO_REFRESH";
/// Commits an intake reads (as the `intake` tool's default).
const MAX_COMMITS: usize = 300;

#[derive(Debug, Default, Serialize, Deserialize)]
struct Stamps(BTreeMap<String, Stamp>);

#[derive(Debug, Serialize, Deserialize)]
struct Stamp {
    fingerprint: String,
    at: String,
}

fn stamps_path(home: &Home) -> std::path::PathBuf {
    home.root.join("db").join("intake-stamps.json")
}

fn load(home: &Home) -> Stamps {
    std::fs::read_to_string(stamps_path(home))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

fn save(home: &Home, stamps: &Stamps) -> Result<()> {
    let path = stamps_path(home);
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(path, serde_json::to_string_pretty(stamps)?)?;
    Ok(())
}

fn git(dir: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git")
        .arg("--no-optional-locks")
        .arg("-C")
        .arg(dir)
        .args(args)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).into_owned())
}

/// What the working tree is right now: HEAD, every path `git status` names, and when each of those was
/// last written (a modified file keeps one status line however often it changes). None when the
/// directory is not a git repository.
pub fn fingerprint(dir: &Path) -> Option<String> {
    let head = git(dir, &["rev-parse", "HEAD"])?;
    let status = git(dir, &["status", "--porcelain", "--untracked-files=all"])?;
    let mut parts = vec![head.trim().to_owned(), status.clone()];
    for line in status.lines() {
        let path = line.get(3..).unwrap_or("").trim().rsplit(" -> ").next().unwrap_or("");
        if let Ok(meta) = std::fs::metadata(dir.join(path))
            && let Ok(m) = meta.modified()
            && let Ok(d) = m.duration_since(std::time::UNIX_EPOCH)
        {
            parts.push(format!("{path}@{}.{}", d.as_secs(), d.subsec_nanos()));
        }
    }
    Some(sha256_hex(&[parts.join("\n").as_bytes()]))
}

fn off() -> bool {
    std::env::var_os(ENV_OFF).is_some()
}

/// Ingest `names` whose working tree changed since their last intake; returns the names refreshed and
/// the names that could not be (with why). Never fails the read it precedes.
pub fn ensure_fresh(home: &Home, store: &Store, names: &[String]) -> (Vec<String>, Vec<String>) {
    let (mut done, mut failed) = (Vec::new(), Vec::new());
    if off() {
        return (done, failed);
    }
    let Ok(registry) = Registry::load(home) else {
        return (done, failed);
    };
    let mut stamps = load(home);
    for name in names {
        let Ok(project) = registry.project(name) else { continue };
        let dir = project.path(&home.workspace);
        let Some(fp) = fingerprint(&dir) else { continue };
        if stamps.0.get(name).is_some_and(|s| s.fingerprint == fp) {
            continue;
        }
        match crate::ops::intake(home, store, name, MAX_COMMITS) {
            Ok(_) => {
                stamps.0.insert(
                    name.clone(),
                    Stamp {
                        fingerprint: fp,
                        at: crate::pulse::now_iso(),
                    },
                );
                done.push(name.clone());
            }
            Err(e) => failed.push(format!("{name} ({e:#})")),
        }
    }
    if !done.is_empty() {
        let _ = save(home, &stamps);
    }
    (done, failed)
}

/// The projects a read of `project` reaches (its related ones too, or every shared one), refreshed.
pub fn ensure_reach_fresh(home: &Home, store: &Store, project: Option<&str>) -> (Vec<String>, Vec<String>) {
    if off() {
        return (Vec::new(), Vec::new());
    }
    match crate::ask::reach_names(home, store, project) {
        Ok((names, _)) => ensure_fresh(home, store, &names),
        Err(_) => (Vec::new(), Vec::new()),
    }
}

/// One line for the top of a read's output when something was refreshed first; empty otherwise.
pub fn note(refreshed: &(Vec<String>, Vec<String>)) -> String {
    let (done, failed) = refreshed;
    if done.is_empty() && failed.is_empty() {
        return String::new();
    }
    let mut s = String::from("refreshed before reading: ");
    let names = done.join(", ");
    s.push_str(if done.is_empty() { "nothing" } else { &names });
    if !failed.is_empty() {
        s.push_str(&format!("; could not refresh: {}", failed.join(", ")));
    }
    s.push('\n');
    s
}

/// Every registered project on this machine, refreshed; then a pulse if the last one is over a day
/// old. Meant for a background task at the server's first store open; returns a short report.
pub fn refresh_all(home: &Home, store: &Store) -> String {
    if off() {
        return "refresh off".into();
    }
    let names: Vec<String> = Registry::load(home)
        .map(|r| {
            r.projects
                .values()
                .filter(|p| p.path(&home.workspace).is_dir())
                .map(|p| p.name.clone())
                .collect()
        })
        .unwrap_or_default();
    let (done, failed) = ensure_fresh(home, store, &names);
    let mut out = format!("refreshed {} of {} projects", done.len(), names.len());
    if !failed.is_empty() {
        out.push_str(&format!("; failed: {}", failed.join(", ")));
    }
    if pulse_due(home) {
        match crate::pulse::take(home, store).and_then(|p| crate::pulse::record(home, &p)) {
            Ok(_) => out.push_str("; pulse taken"),
            Err(e) => out.push_str(&format!("; pulse failed: {e:#}")),
        }
    }
    out
}

/// No pulse line in the last 24 hours.
fn pulse_due(home: &Home) -> bool {
    let path = home.root.join("metrics").join("pulse.jsonl");
    match std::fs::metadata(&path).and_then(|m| m.modified()) {
        Ok(m) => m.elapsed().map_or(true, |e| e.as_secs() > 24 * 3600),
        Err(_) => true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_fingerprint_changes_with_the_tree_and_is_none_outside_git() {
        let dir = std::env::temp_dir().join(format!("gw-fresh-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        assert_eq!(fingerprint(&dir), None);
        let git = |args: &[&str]| {
            assert!(
                Command::new("git")
                    .arg("-C")
                    .arg(&dir)
                    .args(args)
                    .status()
                    .unwrap()
                    .success()
            )
        };
        git(&["init", "-q"]);
        std::fs::write(dir.join("a.md"), "one\n").unwrap();
        git(&["add", "a.md"]);
        git(&["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "a"]);
        let clean = fingerprint(&dir).unwrap();
        assert_eq!(fingerprint(&dir).unwrap(), clean, "stable while nothing changes");
        std::fs::write(dir.join("b.md"), "two\n").unwrap();
        let with_untracked = fingerprint(&dir).unwrap();
        assert_ne!(with_untracked, clean, "a new untracked file changes it");
        std::thread::sleep(std::time::Duration::from_millis(30));
        std::fs::write(dir.join("b.md"), "two more\n").unwrap();
        assert_ne!(
            fingerprint(&dir).unwrap(),
            with_untracked,
            "editing an untracked file changes it"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_note_is_empty_when_nothing_happened() {
        assert_eq!(note(&(vec![], vec![])), "");
        assert!(note(&(vec!["demo".into()], vec![])).starts_with("refreshed before reading: demo"));
    }
}
