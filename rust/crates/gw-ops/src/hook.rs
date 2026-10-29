//! The plugin's hooks (`groundworks hook <event>`): Claude Code calls this binary at SessionStart,
//! PreToolUse, Stop and SubagentStop with the hook's JSON on stdin, and reads the hook's JSON from
//! stdout (decision 1itizcerhaj5x1mjfyj2, 2026-10-29; shape reviewed in team/security REVIEW.md and
//! TELEMETRY-POLICY.md). Each run is one process, so everything here is blocking and quick: no model,
//! one store round trip or two, and never a server start.
//!
//! What the hooks hold to:
//! - A session always starts. SessionStart prints one visible notice when the store is down or the
//!   folder is not a registered project, and exits 0; it never blocks.
//! - PreToolUse fails closed: a rule file that does not parse denies every Bash call with a notice.
//! - Stop blocks once (it honours `stop_hook_active`) when the session used tools that change things
//!   and recorded nothing in the project's journal since it started; `[enforce] stop = "warn"` in
//!   groundworks.toml, or `GROUNDWORKS_ENFORCE=warn`, turns the block into a system message.
//! - The transcript is read for COUNTS ONLY: the `type` and `name` of tool_use blocks and the row
//!   timestamps. No `input`, `content`, `text` or `result` is ever dereferenced, and the transcript path
//!   (which encodes the working directory) is opened and written nowhere.
//! - Telemetry is codes, counts, ids and timestamps (`hook_event`, `session` in the global namespace,
//!   schema 0014). No command text, no path, no tool input, no error text: a hook's own failure is an
//!   `error_code` from a fixed list plus the length of the text that was not stored. Stderr carries the
//!   same codes, never the text, because on a non-zero exit stderr is fed back to the model.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::{Context, Result, bail, ensure};
use regex::Regex;
use serde::Deserialize;
use serde_json::{Value, json};

use crate::home::{EnforceMode, Home, find_home};
use crate::registry::Registry;
use crate::store::{HookEventIn, SessionIn, Store};

/// The rule list the plugin ships (`plugin/hooks/deny.toml`, owned by gw-security), compiled in so the
/// CLI works without the plugin on disk. The plugin passes its own copy with `--rules`.
pub const DEFAULT_RULES: &str = include_str!("../../../../plugin/hooks/deny.toml");

/// What the Stop hook says when a session changed things and recorded nothing.
pub const NO_WRITES_REASON: &str = "Record what you learned in the project journal (journal_add: \
                                    status/decision/gotcha/eliminated), or record one status entry saying nothing new was learned.";
/// What the Stop hook says when the store was down for the whole session.
pub const STORE_DOWN_REASON: &str = "store down: nothing this session was recorded";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Event {
    SessionStart,
    PreToolUse,
    Stop,
    SubagentStop,
}

impl Event {
    pub fn name(self) -> &'static str {
        match self {
            Event::SessionStart => "SessionStart",
            Event::PreToolUse => "PreToolUse",
            Event::Stop => "Stop",
            Event::SubagentStop => "SubagentStop",
        }
    }
}

impl std::str::FromStr for Event {
    type Err = anyhow::Error;

    fn from_str(s: &str) -> Result<Self> {
        Ok(match s {
            "SessionStart" => Event::SessionStart,
            "PreToolUse" => Event::PreToolUse,
            "Stop" => Event::Stop,
            "SubagentStop" => Event::SubagentStop,
            other => bail!("unknown hook event {other:?} (SessionStart, PreToolUse, Stop, SubagentStop)"),
        })
    }
}

/// A hook's own failure, as the code the row keeps (TELEMETRY-POLICY.md). The text behind it is
/// counted (`error_len`) and stored nowhere.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCode {
    StoreDown,
    BinaryMissing,
    Timeout,
    BadHookInput,
    RegexCompile,
    RulesFileMissing,
    TranscriptUnreadable,
    ShowFailed,
    EnvFileUnwritable,
    RefusedSecretShaped,
    Other,
}

impl ErrorCode {
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCode::StoreDown => "store_down",
            ErrorCode::BinaryMissing => "binary_missing",
            ErrorCode::Timeout => "timeout",
            ErrorCode::BadHookInput => "bad_hook_input",
            ErrorCode::RegexCompile => "regex_compile",
            ErrorCode::RulesFileMissing => "rules_file_missing",
            ErrorCode::TranscriptUnreadable => "transcript_unreadable",
            ErrorCode::ShowFailed => "show_failed",
            ErrorCode::EnvFileUnwritable => "env_file_unwritable",
            ErrorCode::RefusedSecretShaped => "refused_secret_shaped",
            ErrorCode::Other => "other",
        }
    }
}

// ---- the rule list ------------------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Deny,
    Warn,
}

#[derive(Deserialize)]
struct RuleFile {
    #[serde(default)]
    rule: Vec<RuleIn>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RuleIn {
    id: String,
    regex: String,
    action: String,
    #[serde(default)]
    hard: bool,
    reason: String,
    #[serde(default)]
    samples_deny: Vec<String>,
    #[serde(default)]
    samples_allow: Vec<String>,
}

/// One rule of deny.toml, its regex compiled.
pub struct Rule {
    pub id: String,
    pub regex: Regex,
    pub action: Action,
    /// Stays a deny in warn mode (the secret-exfiltration rules).
    pub hard: bool,
    pub reason: String,
    pub samples_deny: Vec<String>,
    pub samples_allow: Vec<String>,
}

pub struct Rules {
    pub rules: Vec<Rule>,
}

impl Rules {
    /// Parse and compile a rule file. Any bad regex, unknown action, duplicate id or empty list is an
    /// error, and the caller fails closed on it.
    pub fn parse(text: &str) -> Result<Self> {
        let file: RuleFile = toml::from_str(text).context("deny.toml does not parse")?;
        ensure!(!file.rule.is_empty(), "deny.toml holds no [[rule]]");
        let mut seen = std::collections::BTreeSet::new();
        let mut rules = Vec::with_capacity(file.rule.len());
        for r in file.rule {
            ensure!(seen.insert(r.id.clone()), "deny.toml: rule id {:?} is used twice", r.id);
            ensure!(
                r.id.chars()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-'),
                "deny.toml: rule id {:?} is not [a-z0-9-]",
                r.id
            );
            let action = match r.action.as_str() {
                "deny" => Action::Deny,
                "warn" => Action::Warn,
                other => bail!("deny.toml: rule {} has action {other:?} (deny or warn)", r.id),
            };
            let regex = Regex::new(&r.regex).with_context(|| format!("deny.toml: rule {} regex", r.id))?;
            rules.push(Rule {
                id: r.id,
                regex,
                action,
                hard: r.hard,
                reason: r.reason,
                samples_deny: r.samples_deny,
                samples_allow: r.samples_allow,
            });
        }
        Ok(Rules { rules })
    }

    /// `path`, else `GROUNDWORKS_DENY_RULES`, else the compiled-in copy.
    pub fn load(path: Option<&Path>) -> Result<Self> {
        let path = path
            .map(Path::to_path_buf)
            .or_else(|| std::env::var_os("GROUNDWORKS_DENY_RULES").map(PathBuf::from));
        match path {
            Some(p) => {
                let text = std::fs::read_to_string(&p).with_context(|| format!("reading {}", p.display()))?;
                Self::parse(&text).with_context(|| p.display().to_string())
            }
            None => Self::parse(DEFAULT_RULES).context("compiled-in deny.toml"),
        }
    }

    /// The rule that decides `command`: the first deny rule that matches, else the first warn rule.
    pub fn check(&self, command: &str) -> Option<&Rule> {
        [Action::Deny, Action::Warn]
            .iter()
            .find_map(|a| self.rules.iter().find(|r| r.action == *a && r.regex.is_match(command)))
    }

    /// Every sample both ways, as the list's owner specified (deny.toml header; tests/test_deny.py):
    /// each `samples_deny` must match its own rule, each `samples_allow` must not match its rule and
    /// must not be denied by any deny rule. Returns the failures, one line each; empty means the list
    /// proves itself. A warn rule's sample may still be taken first by a deny rule when the whole list
    /// runs; [`Rules::preempted`] names those for the list's owner.
    pub fn selfcheck(&self) -> Vec<String> {
        let mut failures = Vec::new();
        for rule in &self.rules {
            if rule.samples_deny.len() < 2 || rule.samples_allow.len() < 2 {
                failures.push(format!("{}: fewer than 2 samples of each kind", rule.id));
            }
            for s in &rule.samples_deny {
                if !rule.regex.is_match(s) {
                    failures.push(format!("{}: sample {s:?} should match and does not", rule.id));
                }
            }
            for s in &rule.samples_allow {
                if rule.regex.is_match(s) {
                    failures.push(format!("{}: sample {s:?} should not match and does", rule.id));
                } else if let Some(hit) = self.check(s)
                    && hit.action == Action::Deny
                {
                    failures.push(format!("{}: allowed sample {s:?} is denied by {}", rule.id, hit.id));
                }
            }
        }
        failures
    }

    /// Warn-rule samples that a deny rule takes first when the whole list runs, so the warn never fires
    /// for them. Not a failure of the list's own contract; the owner of the list should know.
    pub fn preempted(&self) -> Vec<String> {
        let mut out = Vec::new();
        for rule in self.rules.iter().filter(|r| r.action == Action::Warn) {
            for s in &rule.samples_deny {
                if let Some(hit) = self.check(s)
                    && hit.id != rule.id
                {
                    out.push(format!("{}: sample {s:?} is decided by {} first", rule.id, hit.id));
                }
            }
        }
        out
    }
}

// ---- the transcript ---------------------------------------------------------------------------------

/// Counts of tool_use blocks in a transcript. Only names are read; inputs never are.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct ToolUses {
    pub edit: u64,
    pub write: u64,
    pub bash: u64,
    /// Groundworks `run` and `gate` (through the MCP server).
    pub run: u64,
    /// Groundworks `journal_add` (through the MCP server).
    pub mcp_write: u64,
}

impl ToolUses {
    /// Tool uses that change something outside the conversation; the Stop hook asks for a journal entry
    /// when this is above zero and the journal did not move.
    pub fn work(&self) -> u64 {
        self.edit + self.write + self.bash + self.run
    }

    pub fn json(&self) -> Value {
        json!({"edit": self.edit, "write": self.write, "bash": self.bash, "run": self.run, "mcp_write": self.mcp_write})
    }
}

/// Which counter a tool name feeds, if any.
fn classify(name: &str) -> Option<fn(&mut ToolUses)> {
    match name {
        "Edit" | "MultiEdit" | "NotebookEdit" => Some(|t| t.edit += 1),
        "Write" => Some(|t| t.write += 1),
        "Bash" | "PowerShell" => Some(|t| t.bash += 1),
        _ if name.starts_with("mcp__") && name.contains("groundworks") => match name.rsplit("__").next() {
            Some("run" | "gate") => Some(|t| t.run += 1),
            Some("journal_add") => Some(|t| t.mcp_write += 1),
            _ => None,
        },
        _ => None,
    }
}

/// Count the tool_use blocks of assistant messages at or after `since` (RFC 3339, compared to the second).
/// A line that is not JSON, or has no timestamp, is counted: the hook would rather ask once too often.
/// Reads `type`, `timestamp` and each block's `type` and `name`; nothing else.
pub fn count_tool_uses(transcript: &str, since: Option<&str>) -> ToolUses {
    let since = since.map(|s| &s[..s.len().min(19)]);
    let mut t = ToolUses::default();
    for line in transcript.lines() {
        let Ok(row) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if row["type"] != "assistant" {
            continue;
        }
        if let (Some(since), Some(ts)) = (since, row["timestamp"].as_str())
            && &ts[..ts.len().min(19)] < since
        {
            continue;
        }
        let Some(blocks) = row["message"]["content"].as_array() else {
            continue;
        };
        for b in blocks.iter().filter(|b| b["type"] == "tool_use") {
            if let Some(f) = b["name"].as_str().and_then(classify) {
                f(&mut t);
            }
        }
    }
    t
}

/// The earliest timestamp in a transcript, when the session's start is not known otherwise.
fn first_timestamp(transcript: &str) -> Option<String> {
    transcript
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .find_map(|r| r["timestamp"].as_str().map(str::to_owned))
}

// ---- the hook run ------------------------------------------------------------------------------------

/// What Claude Code sends on stdin; every field optional, unknown ones ignored.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct Input {
    pub session_id: String,
    pub transcript_path: Option<String>,
    pub cwd: Option<String>,
    pub source: Option<String>,
    pub tool_name: Option<String>,
    pub tool_input: Option<Value>,
    pub stop_hook_active: bool,
    pub agent_type: Option<String>,
    pub agent_id: Option<String>,
}

/// The decision a hook came to, as the row it logs and the JSON it prints.
pub struct Outcome {
    pub decision: &'static str,
    pub rule_id: Option<String>,
    pub output: Value,
}

/// Where a session started, without saying where (TELEMETRY-POLICY.md: `cwd` is never stored).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CwdClass {
    Registered,
    WorkspaceUnregistered,
    OutsideWorkspace,
    /// Under a `scan.local_only` glob: the class is recorded and nothing else about the path.
    LocalOnly,
    Home,
}

impl CwdClass {
    pub fn as_str(self) -> &'static str {
        match self {
            CwdClass::Registered => "registered",
            CwdClass::WorkspaceUnregistered => "workspace-unregistered",
            CwdClass::OutsideWorkspace => "outside-workspace",
            CwdClass::LocalOnly => "local-only",
            CwdClass::Home => "home",
        }
    }
}

struct Ctx {
    event: Event,
    input: Input,
    started: Instant,
    home: Option<Home>,
    store: Option<Store>,
    project: Option<String>,
    cwd_class: CwdClass,
    /// The hook's own failures: the code kept, the text's length counted, the text itself dropped.
    errors: Vec<(ErrorCode, usize)>,
}

impl Ctx {
    fn latency_ms(&self) -> i64 {
        i64::try_from(self.started.elapsed().as_millis()).unwrap_or(i64::MAX)
    }

    fn agent(&self) -> String {
        match self.event {
            Event::SubagentStop => plain_name(
                self.input
                    .agent_type
                    .as_deref()
                    .or(self.input.agent_id.as_deref())
                    .unwrap_or("subagent"),
            ),
            _ => "main".to_owned(),
        }
    }

    /// Keep the code and the length; print the code. The error's text goes nowhere (it can carry a
    /// path or a command, and stderr reaches the model on a non-zero exit).
    fn note_error(&mut self, code: ErrorCode, e: &anyhow::Error) {
        let len = format!("{e:#}").chars().count();
        eprintln!(
            "groundworks hook {}: {} ({len} chars of detail not shown)",
            self.event.name(),
            code.as_str()
        );
        self.errors.push((code, len));
    }

    /// Write the hook_event row; a failure here goes to stderr as a code and nowhere else.
    fn log(&mut self, outcome: &Outcome, uses: ToolUses, writes: u64) {
        let Some(store) = &self.store else { return };
        let row = HookEventIn {
            session: self.input.session_id.chars().take(80).collect(),
            project: self.project.clone(),
            agent: self.agent(),
            hook: self.event.name(),
            decision: outcome.decision,
            rule_id: outcome.rule_id.clone(),
            tool_uses: uses.json(),
            journal_writes_since_start: writes,
            latency_ms: self.latency_ms(),
            error_code: self.errors.first().map(|(c, _)| c.as_str()),
            error_exit: None,
            error_len: (!self.errors.is_empty()).then(|| self.errors.iter().map(|(_, n)| *n as u64).sum()),
        };
        if store.hook_event(&row).is_err() {
            eprintln!("groundworks hook {}: hook_event not recorded", self.event.name());
        }
    }
}

/// A name a row may hold (`session.agent`, `rejection.by`): 1-40 of `[A-Za-z0-9 _-]`, else `other`.
pub fn plain_name(name: &str) -> String {
    let name = name.trim();
    let ok = !name.is_empty()
        && name.chars().count() <= 40
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, ' ' | '_' | '-'));
    if ok { name.to_owned() } else { "other".to_owned() }
}

/// Run one hook: `input` is stdin's JSON; the returned value is printed to stdout. Never panics on bad
/// input and never returns an error: a hook that cannot do its job says so in its output.
pub fn run(event: Event, input: &str, rules_path: Option<&Path>) -> Value {
    let started = Instant::now();
    let mut errors = Vec::new();
    let input: Input = match serde_json::from_str(input) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("groundworks hook {}: bad_hook_input", event.name());
            errors.push((ErrorCode::BadHookInput, e.to_string().len()));
            Input::default()
        }
    };
    let cwd = input.cwd.clone().map(PathBuf::from);
    let home = match home_for(cwd.as_deref()) {
        Ok(h) => h,
        Err(e) => {
            eprintln!("groundworks hook {}: other (home)", event.name());
            errors.push((ErrorCode::Other, format!("{e:#}").len()));
            None
        }
    };
    let store = match &home {
        Some(h) => match Store::open_if_up(h) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("groundworks hook {}: store_down", event.name());
                errors.push((ErrorCode::StoreDown, format!("{e:#}").len()));
                None
            }
        },
        None => None,
    };
    let cwd_class = home
        .as_ref()
        .map_or(CwdClass::OutsideWorkspace, |h| classify_cwd(h, cwd.as_deref()));
    let project = match cwd_class {
        // A fenced-off folder is never resolved to anything, whatever the environment says.
        CwdClass::LocalOnly => None,
        _ => std::env::var("GROUNDWORKS_PROJECT")
            .ok()
            .filter(|p| !p.is_empty())
            .or_else(|| home.as_ref().and_then(|h| project_for(h, cwd.as_deref()))),
    };
    let cwd_class = if project.is_some() {
        CwdClass::Registered
    } else {
        cwd_class
    };
    let mut ctx = Ctx {
        event,
        input,
        started,
        home,
        store,
        project,
        cwd_class,
        errors,
    };
    match event {
        Event::SessionStart => session_start(&mut ctx),
        Event::PreToolUse => pre_tool_use(&mut ctx, rules_path),
        Event::Stop | Event::SubagentStop => stop(&mut ctx),
    }
}

/// `GROUNDWORKS_HOME`, else the nearest home above `cwd`, else above the current directory.
fn home_for(cwd: Option<&Path>) -> Result<Option<Home>> {
    if let Some(root) = std::env::var_os("GROUNDWORKS_HOME") {
        return Home::open(&PathBuf::from(root)).map(Some);
    }
    let start = match cwd {
        Some(c) => c.to_path_buf(),
        None => std::env::current_dir()?,
    };
    match find_home(&start) {
        Some(root) => Home::open(&root).map(Some),
        None => Ok(None),
    }
}

/// The registered project whose directory is `cwd` or holds it (the deepest one when nested).
pub fn project_for(home: &Home, cwd: Option<&Path>) -> Option<String> {
    let cwd = cwd?;
    let cwd = std::fs::canonicalize(cwd).unwrap_or_else(|_| cwd.to_path_buf());
    let registry = Registry::load(home).ok()?;
    registry
        .projects
        .values()
        .filter_map(|p| {
            let dir = p.path(&home.workspace);
            let dir = std::fs::canonicalize(&dir).unwrap_or(dir);
            cwd.starts_with(&dir)
                .then_some((dir.components().count(), p.name.clone()))
        })
        .max()
        .map(|(_, name)| name)
}

/// Where `cwd` is, as a class: a `scan.local_only` match first (recorded as that and nothing else), the
/// home itself, inside the workspace, or outside it. Registered projects are decided by the caller.
pub fn classify_cwd(home: &Home, cwd: Option<&Path>) -> CwdClass {
    let Some(cwd) = cwd else {
        return CwdClass::OutsideWorkspace;
    };
    let text = cwd.display().to_string();
    let local_only = home
        .config
        .scan
        .local_only
        .iter()
        .map(|g| crate::scan::Glob::new(g))
        .any(|g| {
            // The glob names a folder; the session may be in it or below it.
            cwd.ancestors().any(|a| {
                g.matches(&a.display().to_string()) || g.matches(&a.file_name().unwrap_or_default().to_string_lossy())
            })
        })
        || home
            .config
            .scan
            .local_only
            .iter()
            .any(|g| crate::scan::Glob::new(g).matches(&text));
    if local_only {
        return CwdClass::LocalOnly;
    }
    let canon = |p: &Path| std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    let cwd = canon(cwd);
    if cwd.starts_with(canon(&home.root)) {
        CwdClass::Home
    } else if cwd.starts_with(canon(&home.workspace)) {
        CwdClass::WorkspaceUnregistered
    } else {
        CwdClass::OutsideWorkspace
    }
}

/// The mode for `event`: the environment first, then `[enforce]` in groundworks.toml, then block.
fn mode(home: Option<&Home>, event: Event) -> EnforceMode {
    match std::env::var("GROUNDWORKS_ENFORCE").ok().as_deref().map(str::trim) {
        Some("warn") => EnforceMode::Warn,
        Some("block" | "deny") => EnforceMode::Block,
        _ => home.map_or(EnforceMode::Block, |h| match event {
            Event::PreToolUse => h.config.enforce.tool_mode(),
            _ => h.config.enforce.stop_mode(),
        }),
    }
}

fn context_output(event: Event, context: Option<String>, notice: Option<String>) -> Value {
    let mut out = serde_json::Map::new();
    if let Some(context) = context {
        out.insert(
            "hookSpecificOutput".into(),
            json!({"hookEventName": event.name(), "additionalContext": context}),
        );
    }
    if let Some(notice) = notice {
        out.insert("systemMessage".into(), Value::from(notice));
    }
    Value::Object(out)
}

/// `export KEY=value` lines appended to `CLAUDE_ENV_FILE`, so later hooks and Bash calls see them.
fn persist_env(vars: &[(&str, &str)]) -> Result<()> {
    let Some(path) = std::env::var_os("CLAUDE_ENV_FILE") else {
        return Ok(());
    };
    use std::io::Write as _;
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&path)?;
    for (k, v) in vars {
        let plain = v
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '/' | ':' | '-' | '+'));
        if plain {
            writeln!(f, "export {k}={v}")?;
        } else {
            writeln!(f, "export {k}='{}'", v.replace('\'', "'\\''"))?;
        }
    }
    Ok(())
}

fn session_start(ctx: &mut Ctx) -> Value {
    let now = crate::pulse::now_iso();
    // Taken out while this runs (the borrow checker's price for note_error); put back before the log.
    let home = ctx.home.take();
    let store = ctx.store.take();
    let Some(home) = home else {
        let notice = "Groundworks: this folder is under no Groundworks home (no .groundworks/groundworks.toml above \
                      it and GROUNDWORKS_HOME is unset); nothing is recorded."
            .to_owned();
        return context_output(Event::SessionStart, None, Some(notice));
    };
    let session_id = ctx.input.session_id.clone();
    let resumed = matches!(ctx.input.source.as_deref(), Some("resume" | "compact" | "clear"));
    let cwd_class = ctx.cwd_class;
    let row = |project: Option<String>, injected: usize, latency: i64| SessionIn {
        id: session_id.chars().take(80).collect(),
        project,
        machine: crate::home::machine(),
        client: client_name(),
        agent: "main".to_owned(),
        started_at: now.clone(),
        cwd_class,
        injected_entries: injected as u64,
        store_ok: true,
        hook_latency_ms: latency,
    };
    let Some(project) = ctx.project.clone() else {
        let notice = "Groundworks: this folder is not a registered project (project_register or /groundworks:init to \
                      bring it in); this session is not recorded against a project."
            .to_owned();
        if let Some(store) = &store {
            open_session(store, &row(None, 0, ctx.latency_ms()));
        }
        let outcome = Outcome {
            decision: "noop",
            rule_id: Some("not-registered".to_owned()),
            output: context_output(Event::SessionStart, None, Some(notice)),
        };
        ctx.home = Some(home);
        ctx.store = store;
        ctx.log(&outcome, ToolUses::default(), 0);
        return outcome.output;
    };
    if let Err(e) = persist_env(&[
        ("GROUNDWORKS_PROJECT", &project),
        ("GROUNDWORKS_SESSION_ID", &session_id),
        ("GROUNDWORKS_SESSION_START", &now),
    ]) {
        ctx.note_error(ErrorCode::EnvFileUnwritable, &e);
    }
    let Some(store) = store else {
        let _ = persist_env(&[("GROUNDWORKS_DOWN", "1")]);
        let notice = format!(
            "Groundworks: store down at {}: the journal of {project} could not be read and nothing this session \
             will be recorded (check it with: groundworks store status).",
            crate::store::store_url(&home)
        );
        ctx.home = Some(home);
        return context_output(Event::SessionStart, None, Some(notice));
    };
    let (context, injected) = match crate::ops::session_brief(&home, &store, &project, 10) {
        Ok(pair) => pair,
        Err(e) => {
            ctx.note_error(ErrorCode::ShowFailed, &e);
            (
                format!("Groundworks: the journal of {project} could not be read (show_failed)."),
                0,
            )
        }
    };
    if !resumed {
        open_session(&store, &row(Some(project.clone()), injected, ctx.latency_ms()));
    }
    let notice = ctx
        .errors
        .first()
        .map(|(code, _)| format!("Groundworks: {} (the session continues)", code.as_str()));
    let outcome = Outcome {
        decision: if resumed { "noop" } else { "allow" },
        rule_id: None,
        output: context_output(Event::SessionStart, Some(context), notice),
    };
    ctx.home = Some(home);
    ctx.store = Some(store);
    ctx.log(&outcome, ToolUses::default(), 0);
    outcome.output
}

/// Create the session row unless one with this id exists (a resumed session starts again).
fn open_session(store: &Store, row: &SessionIn) {
    match store.session_exists(&row.id) {
        Ok(true) => {}
        Ok(false) => {
            if store.session_start(row).is_err() {
                eprintln!("groundworks hook SessionStart: session row not recorded");
            }
        }
        Err(_) => eprintln!("groundworks hook SessionStart: session lookup failed"),
    }
}

/// Which client runs this session, from the fixed list: `GROUNDWORKS_CLIENT` when it names one, else
/// Claude Code's own entrypoint variable, else `claude-code` (the hook only runs under Claude Code).
fn client_name() -> String {
    const KNOWN: [&str; 4] = ["claude-code", "copilot", "vscode", "cli"];
    if let Ok(c) = std::env::var("GROUNDWORKS_CLIENT")
        && !c.is_empty()
    {
        return if KNOWN.contains(&c.as_str()) {
            c
        } else {
            "other".to_owned()
        };
    }
    match std::env::var("CLAUDE_CODE_ENTRYPOINT").ok().as_deref() {
        Some(e) if e.contains("vscode") => "vscode".to_owned(),
        _ => "claude-code".to_owned(),
    }
}

fn pre_tool_use(ctx: &mut Ctx, rules_path: Option<&Path>) -> Value {
    if ctx.input.tool_name.as_deref() != Some("Bash") {
        return json!({});
    }
    let command = ctx
        .input
        .tool_input
        .as_ref()
        .and_then(|t| t["command"].as_str())
        .unwrap_or("");
    let mode = mode(ctx.home.as_ref(), Event::PreToolUse);
    let outcome = match Rules::load(rules_path) {
        Err(e) => {
            // Fail closed: a guard that cannot read its rules blocks (principle 8).
            let code = if format!("{e:#}").contains("regex") {
                ErrorCode::RegexCompile
            } else {
                ErrorCode::RulesFileMissing
            };
            ctx.note_error(code, &e);
            Outcome {
                decision: "deny",
                rule_id: Some("rules-unloadable".to_owned()),
                output: deny_output(&format!(
                    "Groundworks: the deny list could not be loaded ({}); every Bash call is refused until it is fixed.",
                    code.as_str()
                )),
            }
        }
        Ok(rules) => match rules.check(command) {
            None => Outcome {
                decision: "allow",
                rule_id: None,
                output: json!({}),
            },
            Some(rule) if rule.action == Action::Deny && (mode == EnforceMode::Block || rule.hard) => Outcome {
                decision: "deny",
                rule_id: Some(rule.id.clone()),
                output: deny_output(&rule.reason),
            },
            Some(rule) => Outcome {
                decision: "warn",
                rule_id: Some(rule.id.clone()),
                output: json!({"systemMessage": format!("Groundworks ({}): {}", rule.id, rule.reason)}),
            },
        },
    };
    ctx.log(&outcome, ToolUses::default(), 0);
    outcome.output
}

fn deny_output(reason: &str) -> Value {
    json!({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}})
}

/// The Stop decision, as a pure function so it is tested on its own: block once when work was done and
/// nothing was recorded, or when the store was down the whole session.
pub fn decide_stop(
    uses: ToolUses,
    writes: Option<u64>,
    stop_hook_active: bool,
    down: bool,
) -> (&'static str, Option<&'static str>) {
    if stop_hook_active {
        return ("approve", None);
    }
    if down || writes.is_none() {
        return ("block", Some("store-down"));
    }
    if uses.work() > 0 && writes == Some(0) {
        return ("block", Some("no-writes"));
    }
    ("approve", None)
}

fn stop(ctx: &mut Ctx) -> Value {
    let Some(project) = ctx.project.clone() else {
        let outcome = Outcome {
            decision: "noop",
            rule_id: Some("not-registered".to_owned()),
            output: json!({"decision": "approve"}),
        };
        ctx.log(&outcome, ToolUses::default(), 0);
        return outcome.output;
    };
    // The transcript is opened for counts only (names and timestamps); its path is written nowhere.
    let transcript = match ctx.input.transcript_path.as_deref() {
        Some(p) => match std::fs::read_to_string(p) {
            Ok(text) => text,
            Err(e) => {
                ctx.note_error(ErrorCode::TranscriptUnreadable, &anyhow::Error::from(e));
                String::new()
            }
        },
        None => String::new(),
    };
    let start = std::env::var("GROUNDWORKS_SESSION_START")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| {
            ctx.store
                .as_ref()
                .and_then(|s| s.session_started_at(&ctx.input.session_id).ok().flatten())
        })
        .or_else(|| first_timestamp(&transcript));
    let uses = count_tool_uses(&transcript, start.as_deref());
    let down_flag = std::env::var("GROUNDWORKS_DOWN").is_ok_and(|v| v == "1");
    let writes = match (&ctx.store, &start) {
        (Some(store), Some(start)) => match store.facts_since(&project, start) {
            Ok(rows) => Some(rows.len() as u64),
            Err(e) => {
                ctx.note_error(ErrorCode::StoreDown, &e);
                None
            }
        },
        (Some(store), None) => match store.facts(&project, None, 1) {
            // No start known: any entry at all counts, rather than blocking a session that did record.
            Ok(rows) => Some(rows.len() as u64),
            Err(e) => {
                ctx.note_error(ErrorCode::StoreDown, &e);
                None
            }
        },
        (None, _) => None,
    };
    let (decision, rule) = decide_stop(uses, writes, ctx.input.stop_hook_active, down_flag);
    let reason = match rule {
        Some("store-down") => STORE_DOWN_REASON,
        Some(_) => NO_WRITES_REASON,
        None => "",
    };
    let mode = mode(ctx.home.as_ref(), ctx.event);
    let outcome = match (decision, mode) {
        ("block", EnforceMode::Warn) => Outcome {
            decision: "warn",
            rule_id: rule.map(str::to_owned),
            output: json!({"decision": "approve", "systemMessage": format!("Groundworks ({}): {reason}", rule.unwrap_or(""))}),
        },
        ("block", EnforceMode::Block) => Outcome {
            decision: "block",
            rule_id: rule.map(str::to_owned),
            output: json!({"decision": "block", "reason": format!("Groundworks ({}): {reason}", rule.unwrap_or(""))}),
        },
        _ => Outcome {
            decision: "approve",
            rule_id: None,
            output: json!({"decision": "approve"}),
        },
    };
    ctx.log(&outcome, uses, writes.unwrap_or(0));
    outcome.output
}

/// Telemetry read back for the pulse: hook events grouped by session.
pub fn events_by_session(rows: &[Value]) -> BTreeMap<String, Vec<&Value>> {
    let mut by: BTreeMap<String, Vec<&Value>> = BTreeMap::new();
    for r in rows {
        if let Some(s) = r["session"].as_str() {
            by.entry(s.to_owned()).or_default().push(r);
        }
    }
    by
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_shipped_rules_prove_themselves_both_ways() {
        let rules = Rules::parse(DEFAULT_RULES).unwrap();
        assert!(!rules.rules.is_empty());
        let failures = rules.selfcheck();
        assert!(failures.is_empty(), "{}", failures.join("\n"));
        // Known on 2026-10-29 (reported to gw-security): the confirm-on-cmdline warn samples are denied
        // first by kubectl-mutate and helm-mutate. Listed, not hidden; the list's owner decides.
        let preempted = rules.preempted();
        assert!(
            preempted
                .iter()
                .all(|p| p.starts_with("groundworks-confirm-on-cmdline")),
            "{preempted:?}"
        );
        let hit = rules
            .check("kubectl get secret regcred -n default -o yaml")
            .expect("the secret-read rule");
        assert_eq!(hit.action, Action::Deny);
        assert!(
            rules
                .check("kubectl get secrets -n default")
                .is_none_or(|r| r.action != Action::Deny)
        );
    }

    #[test]
    fn a_rule_list_with_a_wrong_sample_goes_red() {
        let text = r#"
[[rule]]
id = "x"
action = "deny"
regex = 'git\s+push'
reason = "no"
samples_deny = ["git push", "git status"]
samples_allow = ["git log", "git push origin"]
"#;
        let failures = Rules::parse(text).unwrap().selfcheck();
        assert_eq!(failures.len(), 2, "{failures:?}");
        assert!(failures[0].contains("\"git status\" should match"), "{failures:?}");
        assert!(
            failures[1].contains("\"git push origin\" should not match"),
            "{failures:?}"
        );
        let text = r#"
[[rule]]
id = "w"
action = "warn"
regex = 'cat'
reason = "w"
samples_deny = ["cat a", "cat b"]
samples_allow = ["ls", "rm -rf x"]
[[rule]]
id = "d"
action = "deny"
regex = 'rm -rf'
reason = "d"
samples_deny = ["rm -rf x", "rm -rf y"]
samples_allow = ["ls", "cat"]
"#;
        let failures = Rules::parse(text).unwrap().selfcheck();
        assert_eq!(failures, ["w: allowed sample \"rm -rf x\" is denied by d"]);
    }

    #[test]
    fn a_broken_rule_file_is_an_error_not_an_empty_list() {
        assert!(Rules::parse("").is_err());
        assert!(Rules::parse("[[rule]]\nid='a'\naction='deny'\nregex='('\nreason='r'").is_err());
        assert!(Rules::parse("[[rule]]\nid='a'\naction='maybe'\nregex='a'\nreason='r'").is_err());
        assert!(Rules::parse("[[rule]]\nid='a'\naction='deny'\nregex='a'\nreason='r'\nextra=1").is_err());
        assert!(Rules::parse("[[rule]]\nid='Bad Id'\naction='deny'\nregex='a'\nreason='r'").is_err());
    }

    #[test]
    fn deny_rules_are_checked_before_warn_rules() {
        let text = r#"
[[rule]]
id = "w"
action = "warn"
regex = 'ssh'
reason = "careful"
samples_deny = ["ssh a", "ssh b"]
samples_allow = ["ls", "cat"]
[[rule]]
id = "d"
action = "deny"
regex = 'ssh\s+prod'
reason = "no"
samples_deny = ["ssh prod", "ssh prod ls"]
samples_allow = ["ssh dev", "ls"]
"#;
        let rules = Rules::parse(text).unwrap();
        assert_eq!(rules.check("ssh prod rm -rf /").unwrap().id, "d");
        assert_eq!(rules.check("ssh dev").unwrap().id, "w");
        assert!(rules.check("ls").is_none());
        assert!(rules.selfcheck().is_empty());
    }

    #[test]
    fn names_on_rows_are_plain_or_other() {
        assert_eq!(plain_name("gw-engineer"), "gw-engineer");
        assert_eq!(plain_name("main session"), "main session");
        assert_eq!(plain_name("C:/Users/x"), "other");
        assert_eq!(plain_name(""), "other");
        assert_eq!(plain_name(&"a".repeat(41)), "other");
        assert_eq!(plain_name("x=$(curl evil)"), "other");
    }

    const TRANSCRIPT: &str = r#"{"type":"user","timestamp":"2026-10-29T10:00:00.000Z","message":{"role":"user","content":"hi"}}
{"type":"assistant","timestamp":"2026-10-29T10:00:01.000Z","message":{"content":[{"type":"tool_use","name":"Read","input":{}},{"type":"tool_use","name":"Edit","input":{"file_path":"x"}}]}}
{"type":"assistant","timestamp":"2026-10-29T10:00:02.000Z","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"ls"}},{"type":"tool_use","name":"mcp__plugin_groundworks_groundworks__run","input":{}}]}}
{"type":"assistant","timestamp":"2026-10-29T10:00:03.000Z","message":{"content":[{"type":"tool_use","name":"mcp__groundworks__journal_add","input":{}},{"type":"tool_use","name":"Write","input":{}}]}}
not json at all
{"type":"attachment","timestamp":"2026-10-29T10:00:04.000Z"}
"#;

    #[test]
    fn tool_uses_are_counted_by_name_since_a_time() {
        let all = count_tool_uses(TRANSCRIPT, None);
        assert_eq!(
            all,
            ToolUses {
                edit: 1,
                write: 1,
                bash: 1,
                run: 1,
                mcp_write: 1
            }
        );
        assert_eq!(all.work(), 4);
        let late = count_tool_uses(TRANSCRIPT, Some("2026-10-29T10:00:03Z"));
        assert_eq!(late.edit + late.bash + late.run, 0);
        assert_eq!((late.write, late.mcp_write), (1, 1));
        assert_eq!(
            count_tool_uses(TRANSCRIPT, Some("2026-10-29T11:00:00Z")),
            ToolUses::default()
        );
        assert_eq!(first_timestamp(TRANSCRIPT).as_deref(), Some("2026-10-29T10:00:00.000Z"));
    }

    #[test]
    fn the_stop_decision_blocks_once_and_only_when_work_was_not_recorded() {
        let edits = ToolUses {
            edit: 3,
            ..ToolUses::default()
        };
        assert_eq!(decide_stop(edits, Some(0), false, false), ("block", Some("no-writes")));
        assert_eq!(decide_stop(edits, Some(1), false, false), ("approve", None));
        assert_eq!(decide_stop(edits, Some(0), true, false), ("approve", None));
        assert_eq!(
            decide_stop(ToolUses::default(), Some(0), false, false),
            ("approve", None)
        );
        assert_eq!(decide_stop(edits, Some(5), false, true), ("block", Some("store-down")));
        assert_eq!(
            decide_stop(ToolUses::default(), None, false, false),
            ("block", Some("store-down"))
        );
        assert_eq!(decide_stop(edits, None, true, true), ("approve", None));
    }

    #[test]
    fn events_are_parsed_from_the_cli_name() {
        assert_eq!("Stop".parse::<Event>().unwrap(), Event::Stop);
        assert!("PostToolUse".parse::<Event>().is_err());
    }

    #[test]
    fn every_error_code_is_in_the_schema_list() {
        let schema = include_str!("../../gw-store/schema/0014_global_telemetry.surql");
        for code in [
            ErrorCode::StoreDown,
            ErrorCode::BinaryMissing,
            ErrorCode::Timeout,
            ErrorCode::BadHookInput,
            ErrorCode::RegexCompile,
            ErrorCode::RulesFileMissing,
            ErrorCode::TranscriptUnreadable,
            ErrorCode::ShowFailed,
            ErrorCode::EnvFileUnwritable,
            ErrorCode::RefusedSecretShaped,
            ErrorCode::Other,
        ] {
            assert!(schema.contains(&format!("'{}'", code.as_str())), "{}", code.as_str());
        }
        for class in [
            CwdClass::Registered,
            CwdClass::WorkspaceUnregistered,
            CwdClass::OutsideWorkspace,
            CwdClass::LocalOnly,
            CwdClass::Home,
        ] {
            assert!(schema.contains(&format!("'{}'", class.as_str())), "{}", class.as_str());
        }
    }
}
