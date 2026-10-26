//! Ingestion, mechanical and model-free: a project's tracked files and recent commits become
//! citable units, each labelled with its kind and with the experts whose area covers it.
//! Untracked docs that git does not ignore (notes and handoffs nobody committed) are read too, with no
//! commit, so a quote of one says it was never committed; untracked code and config are not.
//!
//! - **Kinds**, so answers can prefer the project's own context: `context` (CLAUDE.md, CONTEXT.md,
//!   AGENTS.md, READMEs, docs/, handoffs and workstream notes), `doc`, `config`, `code`, `commit`.
//! - **Experts**: an expert agent's definition cites the files and folders it owns. Every cited path
//!   that exists in the project becomes part of that expert's area, with the agent file and line as
//!   its evidence; units under it are tagged with the expert.
//! - **Skipped, and counted**: files that look like secrets, binaries, lockfiles, anything over
//!   200 KB, and kinds of file nobody reads for knowledge.
//!
//! Units are cut along markdown headings or in fixed windows of lines; a unit's text is exactly the
//! file's lines, so a quote of it can always be checked against the file.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use anyhow::{Context, Result, bail};
use regex::Regex;
use serde::Serialize;

use crate::scan::Glob;
use crate::store::sha256_hex;

const MAX_FILE_BYTES: u64 = 200 * 1024;
const CODE_WINDOW: usize = 60;
const DOC_MAX: usize = 80;
/// A bare name in an expert's definition (`udp.rs`, `sg_net`) counts only if it is this specific.
const MAX_BARE_MATCHES: usize = 3;

const SECRET_NAMES: [&str; 8] = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "credentials*",
    "*.pfx",
    "*.p12",
];
const LOCKFILES: [&str; 6] = [
    "Cargo.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
];
const CODE_EXT: [&str; 30] = [
    "rs", "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "go", "c", "h", "cc", "cpp", "hpp", "cs", "java", "kt",
    "swift", "sh", "ps1", "sql", "surql", "html", "css", "svelte", "vue", "zig", "lua", "gd", "asm",
];
const CONFIG_EXT: [&str; 7] = ["toml", "yaml", "yml", "json", "ini", "cfg", "conf"];
const DOC_EXT: [&str; 3] = ["md", "txt", "rst"];

#[derive(Debug, Clone, Serialize)]
pub struct Unit {
    /// Store key: from the locator, so the same span is the same unit across intakes.
    #[serde(skip)]
    pub key: String,
    pub locator: String,
    pub kind: &'static str,
    pub text: String,
    pub hash: String,
    pub path: Option<String>,
    pub line_start: Option<usize>,
    pub line_end: Option<usize>,
    pub commit: Option<String>,
    pub experts: Vec<String>,
    /// When the file last changed (its last commit's date; an untracked file's mtime), ISO 8601.
    pub changed_at: Option<String>,
}

/// One expert area: a path inside the project (a file, or a folder ending in `/`), the expert who
/// owns it, and where the expert's definition says so.
#[derive(Debug, Clone, Serialize)]
pub struct AreaClaim {
    pub expert: String,
    pub path: String,
    pub evidence: String,
}

#[derive(Debug, Clone)]
pub struct Expert {
    pub name: String,
    pub summary: String,
    pub file: PathBuf,
    pub text: String,
}

#[derive(Debug, Default)]
pub struct Collected {
    pub head: Option<String>,
    pub dirty: Option<bool>,
    pub files_read: usize,
    /// Of `files_read`, docs git does not track (read from the working tree, no commit).
    pub untracked_read: usize,
    pub units: Vec<Unit>,
    /// Why files were skipped, with counts.
    pub skipped: BTreeMap<&'static str, usize>,
    pub areas: Vec<AreaClaim>,
    /// Every tracked path that is still there, for marking vanished units.
    pub paths: Vec<String>,
}

fn git(dir: &Path, args: &[&str]) -> Result<String> {
    let out = Command::new("git")
        .arg("--no-optional-locks")
        .arg("-C")
        .arg(dir)
        .args(args)
        .stdin(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .context("could not run git")?;
    if !out.status.success() {
        bail!(
            "git {} failed in {}: {}",
            args.join(" "),
            dir.display(),
            String::from_utf8_lossy(&out.stderr).trim()
        );
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// Every expert definition (`*.md` with `name:` and `description:` frontmatter) in `dirs`.
pub fn load_experts(dirs: &[PathBuf]) -> Result<Vec<Expert>> {
    let mut experts = Vec::new();
    for dir in dirs {
        let Ok(entries) = std::fs::read_dir(dir) else { continue };
        for entry in entries {
            let path = entry?.path();
            if path.extension().is_none_or(|e| e != "md") {
                continue;
            }
            let text = std::fs::read_to_string(&path)?;
            let front = text
                .strip_prefix("---")
                .and_then(|rest| rest.split_once("\n---"))
                .map(|(f, _)| f);
            let field = |key: &str| {
                front.and_then(|f| {
                    f.lines().find_map(|l| {
                        l.trim()
                            .strip_prefix(key)
                            .map(|v| v.trim().trim_matches('"').to_owned())
                    })
                })
            };
            if let (Some(name), Some(summary)) = (field("name:"), field("description:")) {
                experts.push(Expert {
                    name,
                    summary,
                    file: path,
                    text,
                });
            }
        }
    }
    experts.sort_by(|a, b| a.name.cmp(&b.name));
    experts.dedup_by(|a, b| a.name == b.name);
    Ok(experts)
}

/// The areas each expert's definition claims inside a project with these tracked files.
pub fn map_areas(experts: &[Expert], tracked: &[String]) -> Vec<AreaClaim> {
    static TOKEN: std::sync::LazyLock<Regex> =
        std::sync::LazyLock::new(|| Regex::new(r"`([A-Za-z0-9_][A-Za-z0-9_./-]{1,200})`").expect("valid"));
    let dirs: BTreeSet<String> = tracked
        .iter()
        .flat_map(|f| {
            let parts: Vec<&str> = f.split('/').collect();
            (1..parts.len()).map(move |n| format!("{}/", parts[..n].join("/")))
        })
        .collect();
    let mut claims = Vec::new();
    let mut seen = BTreeSet::new();
    for expert in experts {
        for (n, line) in expert.text.lines().enumerate() {
            for cap in TOKEN.captures_iter(line) {
                let token = cap[1].trim_end_matches('.');
                let paths: Vec<String> = if token.contains('/') {
                    let dir = token.trim_end_matches('/');
                    let as_dir: Vec<String> = dirs
                        .iter()
                        .filter(|d| d.trim_end_matches('/') == dir || d.ends_with(&format!("/{dir}/")))
                        .cloned()
                        .collect();
                    let as_file: Vec<String> = tracked
                        .iter()
                        .filter(|f| *f == token || f.ends_with(&format!("/{token}")))
                        .cloned()
                        .collect();
                    as_dir.into_iter().chain(as_file).collect()
                } else if token.contains('.') {
                    let found: Vec<String> = tracked
                        .iter()
                        .filter(|f| f.rsplit('/').next() == Some(token))
                        .cloned()
                        .collect();
                    if found.len() > MAX_BARE_MATCHES {
                        Vec::new()
                    } else {
                        found
                    }
                } else {
                    let found: Vec<String> = dirs
                        .iter()
                        .filter(|d| d.trim_end_matches('/').rsplit('/').next() == Some(token))
                        .cloned()
                        .collect();
                    if found.len() > MAX_BARE_MATCHES {
                        Vec::new()
                    } else {
                        found
                    }
                };
                for path in paths {
                    if seen.insert((expert.name.clone(), path.clone())) {
                        claims.push(AreaClaim {
                            expert: expert.name.clone(),
                            path,
                            evidence: format!("{}:{}", expert.file.display(), n + 1),
                        });
                    }
                }
            }
        }
    }
    claims
}

fn experts_for(areas: &[AreaClaim], path: &str) -> Vec<String> {
    let mut names: Vec<String> = areas
        .iter()
        .filter(|a| {
            if a.path.ends_with('/') {
                path.starts_with(&a.path)
            } else {
                a.path == path
            }
        })
        .map(|a| a.expert.clone())
        .collect();
    names.sort();
    names.dedup();
    names
}

fn classify(path: &str) -> Option<&'static str> {
    let name = path.rsplit('/').next().unwrap_or(path);
    let lower = name.to_lowercase();
    let ext = lower.rsplit_once('.').map(|(_, e)| e).unwrap_or("");
    let lower_path = path.to_lowercase();
    if lower.starts_with("readme")
        || matches!(lower.as_str(), "claude.md" | "context.md" | "agents.md")
        || (ext == "md"
            && (lower_path.starts_with("docs/")
                || lower_path.contains("/docs/")
                || lower.contains("handoff")
                || lower.contains("workstream")
                || !path.contains('/')))
    {
        return Some("context");
    }
    if DOC_EXT.contains(&ext) {
        Some("doc")
    } else if CONFIG_EXT.contains(&ext) || matches!(name, "Dockerfile" | "Makefile" | "Caddyfile") {
        Some("config")
    } else if CODE_EXT.contains(&ext) {
        Some("code")
    } else {
        None
    }
}

/// (first line, last line) spans, 1-based: markdown at headings (long sections split), others in windows.
fn spans(lines: &[&str], kind: &str) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    if lines.is_empty() {
        return out;
    }
    let markdown = matches!(kind, "context" | "doc");
    let max = if markdown { DOC_MAX } else { CODE_WINDOW };
    let mut start = 0;
    for i in 1..=lines.len() {
        let heading = markdown && i < lines.len() && lines[i].starts_with('#');
        let full = i - start >= max;
        if i == lines.len() || heading || full {
            out.push((start + 1, i));
            start = i;
        }
    }
    out
}

impl Unit {
    /// A unit with its key (from the locator) and hash (from the text); the rest is set by the caller.
    fn new(locator: String, kind: &'static str, text: String) -> Self {
        Unit {
            key: sha256_hex(&[locator.as_bytes()])[..32].to_owned(),
            hash: sha256_hex(&[text.as_bytes()]),
            locator,
            kind,
            text,
            path: None,
            line_start: None,
            line_end: None,
            commit: None,
            experts: Vec::new(),
            changed_at: None,
        }
    }
}

/// Each tracked path and the date of the last commit that touched it: one pass over the log, newest
/// first, so a path's first appearance is its last change.
fn last_changed(project_dir: &Path) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    let Ok(log) = git(project_dir, &["log", "--name-only", "--format=%x01%cI"]) else {
        return out;
    };
    let mut date = String::new();
    for line in log.lines() {
        if let Some(d) = line.strip_prefix('\u{1}') {
            date = d.trim().to_owned();
        } else if !line.trim().is_empty() && !date.is_empty() {
            out.entry(line.trim().to_owned()).or_insert_with(|| date.clone());
        }
    }
    out
}

/// A file's modification time as ISO 8601 (seconds), for files git does not track.
fn mtime_iso(meta: &std::fs::Metadata) -> Option<String> {
    let secs = meta
        .modified()
        .ok()?
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs() as i64;
    // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm), no extra crate.
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
    Some(format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    ))
}

/// Read a project: tracked files and untracked docs into units, the last `max_commits` commits into
/// commit units, and the experts' areas inside it.
pub fn collect(project_dir: &Path, experts: &[Expert], max_commits: usize) -> Result<Collected> {
    let list = |args: &[&str]| -> Result<Vec<String>> {
        Ok(git(project_dir, args)?
            .split('\0')
            .filter(|p| !p.is_empty())
            .map(str::to_owned)
            .collect())
    };
    let tracked = list(&["ls-files", "-z"])?;
    let untracked = list(&["ls-files", "-z", "--others", "--exclude-standard"])?;
    let changed = last_changed(project_dir);
    let mut c = Collected {
        head: git(project_dir, &["rev-parse", "HEAD"])
            .ok()
            .map(|s| s.trim().to_owned()),
        dirty: git(project_dir, &["status", "--porcelain"])
            .ok()
            .map(|s| !s.trim().is_empty()),
        areas: map_areas(experts, &tracked),
        ..Collected::default()
    };
    let secrets: Vec<Glob> = SECRET_NAMES.iter().map(|g| Glob::new(g)).collect();
    let files = tracked
        .iter()
        .map(|p| (p, true))
        .chain(untracked.iter().map(|p| (p, false)));
    for (path, is_tracked) in files {
        let name = path.rsplit('/').next().unwrap_or(path);
        let skip = |c: &mut Collected, why: &'static str| *c.skipped.entry(why).or_default() += 1;
        if secrets.iter().any(|g| g.matches(name)) {
            skip(&mut c, "looks like a secret");
            continue;
        }
        if LOCKFILES.contains(&name) {
            skip(&mut c, "lockfile");
            continue;
        }
        let Some(kind) = classify(path) else {
            skip(&mut c, "not a kind we read");
            continue;
        };
        if !is_tracked && !matches!(kind, "context" | "doc") {
            skip(&mut c, "untracked, and not a doc");
            continue;
        }
        let full = project_dir.join(path);
        let Ok(meta) = std::fs::metadata(&full) else {
            skip(&mut c, "tracked but missing on disk");
            continue;
        };
        if meta.len() > MAX_FILE_BYTES {
            skip(&mut c, "over 200 KB");
            continue;
        }
        let bytes = std::fs::read(&full)?;
        if bytes.iter().take(8192).any(|b| *b == 0) {
            skip(&mut c, "binary");
            continue;
        }
        let text = String::from_utf8_lossy(&bytes).replace("\r\n", "\n");
        let lines: Vec<&str> = text.lines().collect();
        let experts_here = experts_for(&c.areas, path);
        let changed_at = if is_tracked {
            changed.get(path).cloned()
        } else {
            mtime_iso(&meta)
        };
        c.paths.push(path.clone());
        c.files_read += 1;
        c.untracked_read += usize::from(!is_tracked);
        for (start, end) in spans(&lines, kind) {
            let body = lines[start - 1..end].join("\n");
            if body.trim().is_empty() {
                continue;
            }
            c.units.push(Unit {
                path: Some(path.clone()),
                line_start: Some(start),
                line_end: Some(end),
                commit: c.head.clone().filter(|_| is_tracked),
                experts: experts_here.clone(),
                changed_at: changed_at.clone(),
                ..Unit::new(format!("{path}#L{start}-{end}"), kind, body)
            });
        }
    }
    if max_commits > 0 && c.head.is_some() {
        let log = git(
            project_dir,
            &[
                "log",
                &format!("-n{max_commits}"),
                "--name-only",
                "--format=%x1e%H%x1f%aI%x1f%s%x1f%b%x1f",
            ],
        )?;
        for record in log.split('\u{1e}').filter(|r| !r.trim().is_empty()) {
            let parts: Vec<&str> = record.splitn(5, '\u{1f}').collect();
            if parts.len() < 5 {
                continue;
            }
            let (sha, date, subject, body, files) = (parts[0].trim(), parts[1], parts[2], parts[3].trim(), parts[4]);
            let files: Vec<&str> = files.lines().map(str::trim).filter(|f| !f.is_empty()).collect();
            let mut experts: Vec<String> = files.iter().flat_map(|f| experts_for(&c.areas, f)).collect();
            experts.sort();
            experts.dedup();
            let shown: Vec<&str> = files.iter().take(50).copied().collect();
            let more = files.len().saturating_sub(shown.len());
            let mut text = format!("{subject}\n\n{date}\n");
            if !body.is_empty() {
                text.push_str(&format!("\n{body}\n"));
            }
            text.push_str(&format!("\nfiles:\n{}", shown.join("\n")));
            if more > 0 {
                text.push_str(&format!("\n(and {more} more)"));
            }
            c.units.push(Unit {
                commit: Some(sha.to_owned()),
                experts,
                changed_at: Some(date.trim().to_owned()),
                ..Unit::new(format!("commit:{sha}"), "commit", text)
            });
        }
    }
    Ok(c)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kinds_put_context_first() {
        assert_eq!(classify("README.md"), Some("context"));
        assert_eq!(classify("CLAUDE.md"), Some("context"));
        assert_eq!(classify("docs/NETCODE-PLAN.md"), Some("context"));
        assert_eq!(classify("notes/idea.md"), Some("doc"));
        assert_eq!(classify("src/net.rs"), Some("code"));
        assert_eq!(classify("Cargo.toml"), Some("config"));
        assert_eq!(classify("assets/logo.png"), None);
    }

    #[test]
    fn markdown_splits_at_headings_and_code_in_windows() {
        let md: Vec<&str> = ["# A", "x", "## B", "y", "z"].to_vec();
        assert_eq!(spans(&md, "context"), vec![(1, 2), (3, 5)]);
        let code: Vec<String> = (0..130).map(|i| format!("line {i}")).collect();
        let code: Vec<&str> = code.iter().map(String::as_str).collect();
        assert_eq!(spans(&code, "code"), vec![(1, 60), (61, 120), (121, 130)]);
    }

    #[test]
    fn experts_own_what_their_definitions_cite_and_vague_names_own_nothing() {
        let expert = Expert {
            name: "netcode".into(),
            summary: "s".into(),
            file: PathBuf::from("netcode.md"),
            text: "See `sg_runner/src/net_sim.rs` and `docs/NETCODE-PLAN.md`, the `sg_net` crate, and `mod.rs`.".into(),
        };
        let tracked: Vec<String> = [
            "runner/sg_runner/src/net_sim.rs",
            "runner/sg_net/src/udp.rs",
            "docs/NETCODE-PLAN.md",
            "a/mod.rs",
            "b/mod.rs",
            "c/mod.rs",
            "d/mod.rs",
        ]
        .iter()
        .map(|s| (*s).to_owned())
        .collect();
        let areas = map_areas(&[expert], &tracked);
        let paths: Vec<&str> = areas.iter().map(|a| a.path.as_str()).collect();
        assert!(paths.contains(&"runner/sg_runner/src/net_sim.rs"), "{paths:?}");
        assert!(paths.contains(&"docs/NETCODE-PLAN.md"));
        assert!(paths.contains(&"runner/sg_net/"), "a crate name owns its folder");
        assert!(
            !paths.iter().any(|p| p.ends_with("mod.rs")),
            "four mod.rs files: too vague to own"
        );
        assert_eq!(areas[0].evidence, "netcode.md:1");
        assert_eq!(
            experts_for(&areas, "runner/sg_net/src/udp.rs"),
            vec!["netcode".to_owned()]
        );
    }
}
