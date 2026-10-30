//! Ingestion, mechanical and model-free: a project's tracked files and recent commits become
//! citable units, each labelled with its kind and with the experts whose area covers it.
//! Untracked docs that git does not ignore (notes and handoffs nobody committed) are read too, with no
//! commit, so a quote of one says it was never committed; untracked code and config are not.
//!
//! - **Kinds**, so answers can prefer the project's own context: `context` (CONTEXT.md, READMEs, docs/,
//!   handoffs and workstream notes), `doc`, `config`, `code`, `commit`; and the project's own Claude Code
//!   setup (2026-10-30): `instructions` (CLAUDE.md, AGENTS.md, a disabled `CLAUDE.md.disabled-*`), `agent`
//!   (`.claude/agents/*.md`), `skill` (`.claude/skills/*/SKILL.md`), `command` (`.claude/commands/`) and
//!   `memory` (Claude Code's auto-memory for the folder; see `claude_setup`).
//! - **Experts**: an expert agent's definition cites the files and folders it owns. Every cited path
//!   that exists in the project becomes part of that expert's area, with the agent file and line as
//!   its evidence; units under it are tagged with the expert.
//! - **Skipped, and counted**: files named like secrets, units whose text looks like one (the journal's
//!   secret refusal, run on every unit since 2026-10-30), binaries, lockfiles, anything over
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
    /// `global` (the user's agents folder) or `project:<name>` (that project's `.claude/agents/`).
    pub source: String,
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
    /// What was read of the project's own Claude Code setup (all zero when it is turned off).
    pub setup: SetupReport,
    /// Units whose own text the secret refusal fired on: the locator and the pattern id, never the text.
    /// Skipped, never stored (also counted in `skipped` as [`UNIT_SECRET`]).
    pub refused: Vec<(String, &'static str)>,
}

impl Collected {
    /// The refused units counted by pattern id.
    pub fn refused_by_pattern(&self) -> BTreeMap<&'static str, usize> {
        let mut by: BTreeMap<&'static str, usize> = BTreeMap::new();
        for (_, id) in &self.refused {
            *by.entry(id).or_default() += 1;
        }
        by
    }
}

/// Why a unit was skipped when its own text looks like it holds a secret.
pub const UNIT_SECRET: &str = "unit whose text looks like it holds a secret";

/// Drop every unit whose text the journal's secret refusal fires on, keeping its locator and pattern id
/// (gw-security 2026-10-30, the init design's S0 (c)): the match is never stored, logged or printed.
fn refuse_secret_units(c: &mut Collected) {
    let refused = &mut c.refused;
    c.units.retain(|u| match crate::refusal::check(&u.text) {
        Some(hit) => {
            refused.push((u.locator.clone(), hit.id));
            false
        }
        None => true,
    });
    if !c.refused.is_empty() {
        *c.skipped.entry(UNIT_SECRET).or_default() += c.refused.len();
    }
}

/// Counts of the Claude Code setup an intake read: files by kind, memory stored and refused, sessions.
#[derive(Debug, Default)]
pub struct SetupReport {
    /// False when `[intake] claude_setup = false`.
    pub on: bool,
    pub agents: usize,
    pub skills: usize,
    pub commands: usize,
    pub instructions: usize,
    /// Of `instructions`, files named `*.disabled*`.
    pub instructions_disabled: usize,
    /// Setup files git does not list (ignored), read from disk.
    pub off_git: usize,
    /// Setup files (tracked or not) the secret refusal fired on: skipped, never stored.
    pub setup_refused: usize,
    pub memory_stored: usize,
    pub memory_refused: usize,
    /// Session transcripts counted (never read) and when the newest last changed.
    pub sessions: usize,
    pub newest_session: Option<String>,
}

/// A kind filter as the store applies it: `context` also takes `instructions` and `agent`, which were
/// `context` (or `doc`) before they got kinds of their own, and are still the project's context.
pub fn expand_kinds(kinds: &[String]) -> Vec<String> {
    let mut out = kinds.to_vec();
    if kinds.iter().any(|k| k == "context") {
        for k in ["instructions", "agent", "report"] {
            if !out.iter().any(|o| o == k) {
                out.push(k.to_owned());
            }
        }
    }
    out
}

/// The kinds that are the project's own Claude Code setup.
pub const SETUP_KINDS: [&str; 5] = ["instructions", "agent", "skill", "command", "memory"];

/// The file kinds a quote can confirm a fact from (docs/DECISIONS.md 2026-10-30, the init design's S0 (b)):
/// the project's own code, config and docs. The setup kinds ([`SETUP_KINDS`]) are what an agent was told or
/// remembered: a word-for-word quote of one proves the line is there, not that it is true, so a fact that
/// rests on them alone is INFERRED.
pub const CONFIRMING_KINDS: [&str; 4] = ["code", "config", "doc", "context"];

/// Agent reports (handoff and workstream notes): read as context, never a fact's confirming evidence.
pub const REPORT_KIND: &str = "report";

/// The project that is the owner's agent memory (it stays shared, owner 2026-10-30): every unit of it is
/// recollection, so none confirms a fact, whatever its path.
pub const MEMORY_PROJECT: &str = "memory";

/// Whether a quote of `path` in `project` may confirm a fact: the client's mirror of schema 0017's
/// `fn::confirming_locator`, which the database applies to every write whatever this says. Not the memory
/// project, not a commit, not the project's Claude setup or auto-memory, not an agent report.
pub fn path_confirms(project: &str, path: &str) -> bool {
    if project == MEMORY_PROJECT || path.starts_with("commit:") {
        return false;
    }
    !kind_of_path(path).is_some_and(|k| SETUP_KINDS.contains(&k) || k == REPORT_KIND)
}

/// The kind intake gives a unit path: `memory` under [`crate::claude_setup::MEMORY_PREFIX`], otherwise by
/// the file's name and folder. None for a file intake does not read.
pub fn kind_of_path(path: &str) -> Option<&'static str> {
    if path.starts_with(crate::claude_setup::MEMORY_PREFIX) {
        return Some("memory");
    }
    classify(&path.replace('\\', "/"))
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
                    source: "global".to_owned(),
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
    if matches!(lower.as_str(), "claude.md" | "agents.md") || lower.starts_with("claude.md.disabled") {
        return Some("instructions");
    }
    if ext == "md"
        && let Some(rest) = lower_path.strip_prefix(".claude/")
    {
        if rest.strip_prefix("agents/").is_some_and(|f| !f.contains('/')) {
            return Some("agent");
        }
        if rest
            .strip_prefix("skills/")
            .is_some_and(|f| f.split('/').count() == 2 && lower == "skill.md")
        {
            return Some("skill");
        }
        if rest.starts_with("commands/") {
            return Some("command");
        }
    }
    // Handoff and workstream notes are agent reports (scholar-architect review, 2026-10-30): read as context,
    // never a fact's confirming evidence. Matched by name, whatever the extension, as schema 0017 does.
    if lower.contains("handoff") || lower.contains("workstream") {
        return Some("report");
    }
    if lower.starts_with("readme")
        || lower == "context.md"
        || (ext == "md" && (lower_path.starts_with("docs/") || lower_path.contains("/docs/") || !path.contains('/')))
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
    let markdown = matches!(kind, "context" | "doc" | "report") || SETUP_KINDS.contains(&kind);
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

/// `modified:`, `date:` or `updated:` from a file's YAML frontmatter, when the value starts like a date.
fn frontmatter_date(text: &str) -> Option<String> {
    let front = text
        .strip_prefix("---")?
        .split_once(
            "
---",
        )?
        .0;
    for key in ["modified:", "date:", "updated:"] {
        if let Some(v) = front.lines().find_map(|l| l.trim().strip_prefix(key)) {
            let v = v.trim().trim_matches(['"', '\'']);
            if v.len() >= 10 && v.as_bytes()[4] == b'-' && v.as_bytes()[7] == b'-' {
                return Some(v.to_owned());
            }
        }
    }
    None
}

/// A file's modification time as ISO 8601 (seconds), for files git does not track.
pub(crate) fn mtime_iso(meta: &std::fs::Metadata) -> Option<String> {
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

/// Counts one setup file by its kind.
fn count_setup(r: &mut SetupReport, kind: &str, path: &str) {
    match kind {
        "agent" => r.agents += 1,
        "skill" => r.skills += 1,
        "command" => r.commands += 1,
        "instructions" => {
            r.instructions += 1;
            if path.to_lowercase().contains(".disabled") {
                r.instructions_disabled += 1;
            }
        }
        _ => {}
    }
}

/// A file read for intake outside git's lists: its text, or why it was not stored.
enum Read {
    Text(String, std::fs::Metadata),
    /// The journal's secret refusal fired: skipped, never stored.
    Refused,
    Skip(&'static str),
}

fn read_checked(full: &Path) -> Result<Read> {
    let Ok(meta) = std::fs::metadata(full) else {
        return Ok(Read::Skip("vanished while being read"));
    };
    if meta.len() > MAX_FILE_BYTES {
        return Ok(Read::Skip("over 200 KB"));
    }
    let bytes = std::fs::read(full)?;
    if bytes.iter().take(8192).any(|b| *b == 0) {
        return Ok(Read::Skip("binary"));
    }
    let text = String::from_utf8_lossy(&bytes).replace("\r\n", "\n");
    if crate::refusal::check(&text).is_some() {
        return Ok(Read::Refused);
    }
    Ok(Read::Text(text, meta))
}

/// One file's units (cut by [`spans`]), its path counted as present.
fn push_file_units(c: &mut Collected, path: &str, kind: &'static str, text: &str, changed_at: Option<String>) {
    let lines: Vec<&str> = text.lines().collect();
    let experts = experts_for(&c.areas, path);
    c.paths.push(path.to_owned());
    c.files_read += 1;
    for (start, end) in spans(&lines, kind) {
        let body = lines[start - 1..end].join("\n");
        if body.trim().is_empty() {
            continue;
        }
        c.units.push(Unit {
            path: Some(path.to_owned()),
            line_start: Some(start),
            line_end: Some(end),
            experts: experts.clone(),
            changed_at: changed_at.clone(),
            ..Unit::new(format!("{path}#L{start}-{end}"), kind, body)
        });
    }
}

const SETUP_SECRET: &str = "Claude setup file that looks like it holds a secret";
const MEMORY_SECRET: &str = "memory file that looks like it holds a secret";

/// The project's own Claude setup that git does not list, its auto-memory, and its sessions (counted).
fn collect_setup(c: &mut Collected, project_dir: &Path, claude: &Path, listed: &BTreeSet<&str>) -> Result<()> {
    let off_git: Vec<String> = crate::claude_setup::setup_paths(project_dir)
        .into_iter()
        .filter(|p| !listed.contains(p.as_str()))
        .collect();
    for path in off_git {
        let Some(kind) = classify(&path) else { continue };
        match read_checked(&project_dir.join(&path))? {
            Read::Text(text, meta) => {
                count_setup(&mut c.setup, kind, &path);
                c.setup.off_git += 1;
                let changed_at = frontmatter_date(&text).or_else(|| mtime_iso(&meta));
                push_file_units(c, &path, kind, &text, changed_at);
            }
            Read::Refused => {
                c.setup.setup_refused += 1;
                *c.skipped.entry(SETUP_SECRET).or_default() += 1;
            }
            Read::Skip(why) => *c.skipped.entry(why).or_default() += 1,
        }
    }
    let sessions = crate::claude_setup::session_dir(claude, project_dir);
    for file in crate::claude_setup::memory_files(&sessions) {
        let name = file
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        let path = format!("{}{name}", crate::claude_setup::MEMORY_PREFIX);
        match read_checked(&file)? {
            Read::Text(text, meta) => {
                c.setup.memory_stored += 1;
                let changed_at = frontmatter_date(&text).or_else(|| mtime_iso(&meta));
                push_file_units(c, &path, "memory", &text, changed_at);
            }
            Read::Refused => {
                c.setup.memory_refused += 1;
                *c.skipped.entry(MEMORY_SECRET).or_default() += 1;
            }
            Read::Skip(why) => *c.skipped.entry(why).or_default() += 1,
        }
    }
    (c.setup.sessions, c.setup.newest_session) = crate::claude_setup::sessions(&sessions);
    c.units.extend(crate::claude_setup::transcript_units(&sessions));
    Ok(())
}

/// Read a project: tracked files and untracked docs into units, the last `max_commits` commits into
/// commit units, and the experts' areas inside it. With `claude` (Claude Code's user folder), also the
/// project's own Claude setup that git does not list, its auto-memory, and a count of its sessions.
pub fn collect(project_dir: &Path, experts: &[Expert], max_commits: usize, claude: Option<&Path>) -> Result<Collected> {
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
    c.setup.on = claude.is_some();
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
        if !is_tracked && !matches!(kind, "context" | "doc" | "report") && !SETUP_KINDS.contains(&kind) {
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
        // Every Claude setup file, tracked or not, passes the journal's secret refusal before it is stored
        // (gw-security, 2026-10-30), whole: a hit skips the file. Every other unit passes it one by one at the end.
        if SETUP_KINDS.contains(&kind) && crate::refusal::check(&text).is_some() {
            c.setup.setup_refused += 1;
            skip(&mut c, SETUP_SECRET);
            continue;
        }
        if claude.is_some() {
            count_setup(&mut c.setup, kind, path);
        }
        let lines: Vec<&str> = text.lines().collect();
        let experts_here = experts_for(&c.areas, path);
        // A note that dates itself (frontmatter `modified:` or `date:`) knows better than git: a folder of
        // notes committed in one snapshot would otherwise all carry the snapshot's date.
        let changed_at = frontmatter_date(&text).or_else(|| {
            if is_tracked {
                changed.get(path).cloned()
            } else {
                mtime_iso(&meta)
            }
        });
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
    if let Some(claude) = claude {
        let listed: BTreeSet<&str> = tracked.iter().chain(untracked.iter()).map(String::as_str).collect();
        collect_setup(&mut c, project_dir, claude, &listed)?;
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
    // Every unit, of every kind, passes the refusal on its own text: a file the name filter let through
    // can still hold a pasted token, and only the unit holding it is skipped.
    refuse_secret_units(&mut c);
    Ok(c)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_unit_holding_a_secret_is_skipped_and_counted_by_pattern_and_its_neighbours_stay() {
        let (_, secret) = crate::refusal::must_refuse()
            .into_iter()
            .find(|(id, _)| id == "kv-secret")
            .unwrap();
        let mut c = Collected::default();
        c.units.push(Unit::new(
            "notes.md#L1-2".into(),
            "doc",
            "# Clean\nThe manatee is fine.".into(),
        ));
        c.units.push(Unit::new(
            "notes.md#L3-4".into(),
            "doc",
            format!("# Pasted\nThe dugong: {secret}"),
        ));
        c.units
            .push(Unit::new("commit:abc".into(), "commit", format!("fix\n\n{secret}")));
        refuse_secret_units(&mut c);
        assert_eq!(c.units.len(), 1);
        assert_eq!(c.units[0].locator, "notes.md#L1-2");
        assert_eq!(c.refused_by_pattern().get("kv-secret"), Some(&2));
        assert_eq!(c.refused[1].0, "commit:abc");
        assert_eq!(c.skipped.get(UNIT_SECRET), Some(&2));
        // The policy table: setup and memory paths cannot confirm, code and docs can.
        assert_eq!(kind_of_path("~claude-memory/a.md"), Some("memory"));
        assert_eq!(kind_of_path("CLAUDE.md"), Some("instructions"));
        assert_eq!(kind_of_path(".claude/agents/x.md"), Some("agent"));
        assert_eq!(kind_of_path("src/main.rs"), Some("code"));
        assert!(CONFIRMING_KINDS.iter().all(|k| !SETUP_KINDS.contains(k)));
    }

    #[test]
    fn kinds_put_context_first() {
        assert_eq!(classify("README.md"), Some("context"));
        // Since 2026-10-30 a kind of its own; a `context` filter still takes it (`expand_kinds`).
        assert_eq!(classify("CLAUDE.md"), Some("instructions"));
        assert!(expand_kinds(&["context".to_owned()]).contains(&"instructions".to_owned()));
        assert_eq!(classify("docs/NETCODE-PLAN.md"), Some("context"));
        assert_eq!(classify("notes/idea.md"), Some("doc"));
        assert_eq!(classify("src/net.rs"), Some("code"));
        assert_eq!(classify("Cargo.toml"), Some("config"));
        assert_eq!(classify("assets/logo.png"), None);
    }

    #[test]
    fn a_projects_claude_setup_has_kinds_of_its_own() {
        assert_eq!(classify("CLAUDE.md"), Some("instructions"));
        assert_eq!(classify("AGENTS.md"), Some("instructions"));
        assert_eq!(classify("sub/CLAUDE.md"), Some("instructions"));
        assert_eq!(classify("CLAUDE.md.disabled-2026-09-21"), Some("instructions"));
        assert_eq!(classify(".claude/agents/netcode.md"), Some("agent"));
        assert_eq!(classify(".claude/agents.disabled-2026-09-21/old.md"), Some("doc"));
        assert_eq!(classify(".claude/skills/panel/SKILL.md"), Some("skill"));
        assert_eq!(classify(".claude/skills/panel/notes.md"), Some("doc"));
        assert_eq!(classify(".claude/commands/ops/restart.md"), Some("command"));
        assert_eq!(classify("CONTEXT.md"), Some("context"));
        assert_eq!(classify(".claude/settings.json"), Some("config"));
    }

    fn git_fixture(dir: &Path) {
        let run = |args: &[&str]| {
            let ok = Command::new("git")
                .args(["-c", "user.name=t", "-c", "user.email=t@localhost", "-C"])
                .arg(dir)
                .args(args)
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .unwrap()
                .success();
            assert!(ok, "git {args:?}");
        };
        run(&["init", "-q"]);
        run(&["add", "-A"]);
        run(&["commit", "-q", "-m", "init"]);
    }

    /// Both ways: with the Claude folder given, the ignored agent, the skill, the memory are read, the
    /// secret-shaped memory file is refused and counted, transcripts are counted and never read; without
    /// it (the opt-out) none of that happens.
    #[test]
    fn collect_reads_the_claude_setup_and_refuses_secret_memory() {
        let base = std::env::temp_dir().join(format!("gw-intake-setup-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let project = base.join("proj");
        let claude = base.join("claude");
        let write = |p: &Path, t: &str| {
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(p, t).unwrap();
        };
        write(&project.join("src/net.rs"), "fn net() {}\n");
        write(&project.join("CLAUDE.md"), "# Rules\n\nAlways run the gate.\n");
        write(&project.join(".gitignore"), ".claude/\n");
        write(
            &project.join(".claude/agents/local.md"),
            "---\nname: local\ndescription: d\n---\nOwns `src/net.rs`.\n",
        );
        write(
            &project.join(".claude/skills/check/SKILL.md"),
            "---\nname: check\n---\nquokka\n",
        );
        // The same kinds holding a fixture secret: one committed (a nested CLAUDE.md), one ignored (a skill).
        let (_, secret) = crate::refusal::must_refuse().into_iter().next().unwrap();
        write(
            &project.join("sub/CLAUDE.md"),
            &format!("# Sub\n\nquillwort {secret}\n"),
        );
        write(
            &project.join(".claude/skills/leaky/SKILL.md"),
            &format!("---\nname: leaky\n---\nquillwort {secret}\n"),
        );
        git_fixture(&project);
        let sessions = crate::claude_setup::session_dir(&claude, &project);
        write(
            &sessions.join("memory/clean.md"),
            "---\nname: clean\nmodified: 2026-01-02\n---\nwombat facts\n",
        );
        write(&sessions.join("memory/leaky.md"), &format!("numbat {secret}\n"));
        write(&sessions.join("a.jsonl"), "{\"platypus\": 1}\n");
        write(&sessions.join("b.jsonl"), "{\"platypus\": 2}\n");

        let local = crate::claude_setup::project_experts(&project, "proj").unwrap().experts;
        let on = collect(&project, &local, 0, Some(&claude)).unwrap();
        let kind_of =
            |c: &Collected, path: &str| c.units.iter().find(|u| u.path.as_deref() == Some(path)).map(|u| u.kind);
        assert_eq!(kind_of(&on, "CLAUDE.md"), Some("instructions"));
        assert_eq!(
            kind_of(&on, ".claude/agents/local.md"),
            Some("agent"),
            "ignored, read from disk"
        );
        assert_eq!(kind_of(&on, ".claude/skills/check/SKILL.md"), Some("skill"));
        let memory = on
            .units
            .iter()
            .find(|u| u.path.as_deref() == Some("~claude-memory/clean.md"))
            .expect("memory unit");
        assert_eq!(
            (memory.kind, memory.changed_at.as_deref()),
            ("memory", Some("2026-01-02"))
        );
        assert!(
            on.units
                .iter()
                .all(|u| !u.text.contains("numbat") && !u.text.contains(&secret))
        );
        assert!(
            on.units.iter().all(|u| !u.text.contains("quillwort")),
            "secret-shaped setup files are refused"
        );
        assert_eq!(kind_of(&on, "sub/CLAUDE.md"), None);
        assert_eq!(kind_of(&on, ".claude/skills/leaky/SKILL.md"), None);
        assert!(on.units.iter().all(|u| !u.text.contains("platypus")));
        let r = &on.setup;
        assert_eq!(
            (r.on, r.agents, r.skills, r.instructions, r.off_git, r.setup_refused),
            (true, 1, 1, 1, 2, 2)
        );
        assert_eq!((r.memory_stored, r.memory_refused, r.sessions), (1, 1, 2));
        assert!(r.newest_session.is_some());
        assert_eq!(
            experts_for(&on.areas, "src/net.rs"),
            vec!["local".to_owned()],
            "a project expert tags what it cites"
        );
        assert!(on.paths.contains(&"~claude-memory/clean.md".to_owned()));

        let off = collect(&project, &[], 0, None).unwrap();
        assert!(!off.setup.on);
        assert_eq!(
            kind_of(&off, ".claude/agents/local.md"),
            None,
            "opt-out reads nothing outside git"
        );
        assert!(off.units.iter().all(|u| u.kind != "memory"));
        assert_eq!((off.setup.sessions, off.setup.memory_refused), (0, 0));
        assert_eq!(
            off.setup.setup_refused, 1,
            "the committed one is refused even with the opt-out"
        );
        assert!(off.units.iter().all(|u| !u.text.contains(&secret)));
        let _ = std::fs::remove_dir_all(&base);
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
            source: "global".into(),
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
