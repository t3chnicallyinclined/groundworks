//! `init_survey` (the init design's S2, 2026-10-30): what Groundworks would bring in from a folder, read
//! and reported, nothing written. No store call at all, so the store is unchanged by construction, and
//! no file is created or changed. It reuses the estate scan's per-folder record (`scan::scan_one`), so a
//! folder the scan treats as local-only (a `[scan] local_only` glob, or a git repository with no remote)
//! is refused the same way and not walked.
//!
//! The JSON is the contract the desktop card and the init skill read; its shape is listed in
//! CHANGELOG.md (2026-10-30, slice S2). Every list is sorted; every path uses forward slashes.

use std::path::{Path, PathBuf};

use anyhow::{Result, ensure};
use serde_json::{Value, json};

use crate::home::Home;
use crate::registry::Registry;

/// The survey's version: bumped whenever a field is added, renamed or removed.
pub const VERSION: u32 = 1;

fn slashes(p: &Path) -> String {
    p.display().to_string().replace('\\', "/")
}

/// Where `path` (absolute, or relative to the workspace) points, made absolute.
fn resolve(home: &Home, path: &str) -> PathBuf {
    let p = PathBuf::from(path);
    if p.is_absolute() { p } else { home.workspace.join(p) }
}

/// The registered project whose folder is `dir`, compared case-insensitively on Windows.
fn registered(home: &Home, dir: &Path) -> Result<Option<String>> {
    let norm = |p: &Path| {
        let p = p.canonicalize().unwrap_or_else(|_| p.to_path_buf());
        let s = slashes(&p);
        if cfg!(windows) { s.to_lowercase() } else { s }
    };
    let want = norm(dir);
    Ok(Registry::load(home)?
        .projects
        .values()
        .find(|p| norm(&p.path(&home.workspace)) == want)
        .map(|p| p.name.clone()))
}

/// Gate commands a folder's own files name: the scan's build-file guesses, `run:` lines of GitHub
/// workflows that look like tests or checks, and `just` recipes named test, check or gate.
fn gate_candidates(dir: &Path, scan: &Value) -> Vec<Value> {
    let mut out: Vec<Value> = scan["candidate_gates"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(|g| json!({"command": g, "from": "build files"}))
        .collect();
    let workflows = dir.join(".github").join("workflows");
    let mut files: Vec<PathBuf> = std::fs::read_dir(&workflows)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|e| e == "yml" || e == "yaml"))
        .collect();
    files.sort();
    for file in files {
        let Ok(text) = std::fs::read_to_string(&file) else {
            continue;
        };
        let from = format!(
            ".github/workflows/{}",
            file.file_name().unwrap_or_default().to_string_lossy()
        );
        for line in text.lines() {
            let Some(cmd) = line
                .trim()
                .strip_prefix("run:")
                .or_else(|| line.trim().strip_prefix("- run:"))
            else {
                continue;
            };
            let cmd = cmd.trim().trim_matches(['"', '\'']);
            let lower = cmd.to_lowercase();
            if !cmd.is_empty()
                && cmd != "|"
                && ["test", "check", "clippy", "lint", "fmt", "gate"]
                    .iter()
                    .any(|w| lower.contains(w))
                && crate::refusal::check(cmd).is_none()
            {
                out.push(json!({"command": cmd, "from": from}));
            }
        }
    }
    for name in ["justfile", "Justfile", ".justfile"] {
        let Ok(text) = std::fs::read_to_string(dir.join(name)) else {
            continue;
        };
        for recipe in ["test", "check", "gate"] {
            if text
                .lines()
                .any(|l| l.starts_with(&format!("{recipe}:")) || l.starts_with(&format!("{recipe} ")))
            {
                out.push(json!({"command": format!("just {recipe}"), "from": name}));
            }
        }
        break;
    }
    out
}

/// Registered stacks a folder's build files hint at: a guess, named as one.
fn stack_guess(home: &Home, scan: &Value) -> Result<Vec<String>> {
    let kinds: Vec<&str> = scan["build_files"]
        .as_object()
        .map(|m| m.keys().map(String::as_str).collect())
        .unwrap_or_default();
    let hints: Vec<&str> = kinds
        .iter()
        .flat_map(|k| match *k {
            "cargo" => &["rust"][..],
            "npm" => &["web", "node", "typescript"][..],
            "pyproject" => &["python"][..],
            "go" => &["go"][..],
            _ => &[][..],
        })
        .copied()
        .collect();
    let registry = Registry::load(home)?;
    let mut out: Vec<String> = registry
        .stacks
        .values()
        .filter(|s| {
            let text = format!("{} {}", s.name, s.summary).to_lowercase();
            hints.iter().any(|h| text.contains(h))
        })
        .map(|s| s.name.clone())
        .collect();
    out.sort();
    Ok(out)
}

/// The folder's own Claude Code setup, names and counts only (no file text).
fn claude_setup(home: &Home, dir: &Path) -> Result<Value> {
    let folder = dir
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let local = crate::claude_setup::project_experts(dir, &folder)?;
    let (_, skills) = crate::claude_setup::names(dir);
    let paths = crate::claude_setup::setup_paths(dir);
    let mut instructions: Vec<&str> = paths
        .iter()
        .filter(|p| crate::intake::kind_of_path(p) == Some("instructions"))
        .map(String::as_str)
        .collect();
    instructions.sort();
    let commands = paths
        .iter()
        .filter(|p| crate::intake::kind_of_path(p) == Some("command"))
        .count();
    let mut agents: Vec<String> = crate::claude_setup::names(dir)
        .0
        .into_iter()
        .filter(|n| !crate::claude_setup::is_reserved(n))
        .collect();
    agents.sort();
    let sessions_dir = crate::claude_setup::session_dir(&crate::ops::claude_dir(), dir);
    let (sessions, newest) = crate::claude_setup::sessions(&sessions_dir);
    Ok(json!({
        "read": home.config.intake.claude_setup(),
        "instructions": instructions,
        "agents": agents,
        "reserved": local.reserved.iter().map(|r| json!({"name": r.name, "file": r.file})).collect::<Vec<_>>(),
        "disabled": local.disabled.iter().map(|d| json!({"folder": d.folder, "definitions": d.definitions})).collect::<Vec<_>>(),
        "skills": skills,
        "commands": commands,
        "memory_files": crate::claude_setup::memory_files(&sessions_dir).len(),
        "sessions": sessions,
        "newest_session": newest,
    }))
}

/// Survey `path` (absolute, or relative to the workspace). Reads the folder, its git state and its
/// Claude setup; writes nothing and never calls the store.
pub fn survey(home: &Home, path: &str) -> Result<Value> {
    let dir = resolve(home, path);
    ensure!(dir.is_dir(), "{} is not a folder on this machine", slashes(&dir));
    let scan = crate::scan::scan_one(&dir, &home.config.scan.local_only).unwrap_or(Value::Null);
    let base = json!({
        "survey_version": VERSION,
        "path": slashes(&dir),
        "machine": crate::home::machine(),
        "registered": registered(home, &dir)?,
        "writes": "none",
    });
    let mut out = base.as_object().cloned().unwrap_or_default();
    if scan["local_only"] == json!(true) {
        out.insert("local_only".into(), json!(true));
        out.insert(
            "why".into(),
            json!("local-only by the estate scan (a [scan] local_only glob, or a git repository with no remote): not walked"),
        );
        return Ok(Value::Object(out));
    }
    out.insert("local_only".into(), json!(false));
    let git = (scan["kind"] != json!("plain folder")).then(|| {
        json!({
            "kind": scan["kind"],
            "branch": scan["branch"],
            "head_sha": scan["head_sha"],
            "dirty": scan["dirty"],
            "remotes": scan["remotes"],
            "last_commit_time": scan["last_commit_time"],
        })
    });
    out.insert("git".into(), git.unwrap_or(Value::Null));
    let build_files: serde_json::Map<String, Value> = scan["build_files"]
        .as_object()
        .into_iter()
        .flatten()
        .map(|(kind, paths)| {
            let mut paths: Vec<String> = paths
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|p| p.as_str().map(|p| p.replace('\\', "/")))
                .collect();
            paths.sort();
            (kind.clone(), json!(paths))
        })
        .collect();
    out.insert("build_files".into(), Value::Object(build_files));
    out.insert("stack_guess".into(), json!(stack_guess(home, &scan)?));
    out.insert("gate_candidates".into(), json!(gate_candidates(&dir, &scan)));
    let mut docs: Vec<String> = scan["docs"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|d| d["path"].as_str().map(|p| p.replace('\\', "/")))
        .collect();
    docs.sort();
    out.insert("docs".into(), json!(docs));
    let mut flags: Vec<String> = scan["secret_flags"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|p| p.as_str().map(|p| p.replace('\\', "/")))
        .collect();
    flags.sort();
    out.insert("secret_named_files".into(), json!(flags));
    out.insert("claude_setup".into(), claude_setup(home, &dir)?);
    Ok(Value::Object(out))
}
