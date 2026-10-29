//! The plugin's hooks (`groundworks hook <event>`): Claude Code calls this binary at SessionStart,
//! PreToolUse (Bash), Stop and SubagentStop with the hook's JSON on stdin, and reads the hook's JSON
//! from stdout (decision 1itizcerhaj5x1mjfyj2, 2026-10-29). Each run is one process, so everything here
//! is blocking and quick: no model, one store round trip or two, and never a server start.
//!
//! What the hooks hold to:
//! - A session always starts. SessionStart prints one visible notice when the store is down or the
//!   folder is not a registered project, and exits 0; it never blocks.
//! - PreToolUse fails closed: a rule file that does not parse denies every Bash call with a notice.
//! - Stop blocks once (it honours `stop_hook_active`) when the session used tools that change things
//!   and recorded nothing in the project's journal since it started; `[enforce] stop = "warn"` in
//!   groundworks.toml, or `GROUNDWORKS_ENFORCE=warn`, turns the block into a system message.
//! - Telemetry is counts, ids and timings (`hook_event`, `session` in the global namespace). No command
//!   text, no tool input, no transcript text, no file contents. A command is identified only by an HMAC
//!   keyed with `<home>/secrets/hook_hmac.key`.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::Instant;

use anyhow::{Context, Result, bail, ensure};
use hmac::{Hmac, Mac};
use regex::Regex;
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::Sha256;

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

/// The error text kept on a hook_event row is cut here, after redaction.
const ERROR_CAP: usize = 200;

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

// ---- redaction and the command HMAC --------------------------------------------------------------

/// Anything that could be a token is replaced before an error text is kept: `Bearer <x>` and any run
/// of 32 or more identifier characters. The result is cut at [`ERROR_CAP`] characters.
pub fn redact(text: &str) -> String {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"Bearer \S+|[A-Za-z0-9_\-]{32,}").expect("redaction regex"));
    re.replace_all(text, "<redacted>").chars().take(ERROR_CAP).collect()
}

/// The 32-byte HMAC key at `<home>/secrets/hook_hmac.key` (hex), created once and never printed.
pub fn hmac_key(home: &Home) -> Result<Vec<u8>> {
    let path = home.path("secrets").join("hook_hmac.key");
    if let Ok(text) = std::fs::read_to_string(&path) {
        let bytes = unhex(text.trim()).with_context(|| format!("{} is not a hex key", path.display()))?;
        ensure!(bytes.len() == 32, "{} is not 32 bytes", path.display());
        return Ok(bytes);
    }
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| anyhow::anyhow!("no OS randomness: {e}"))?;
    std::fs::create_dir_all(home.path("secrets"))?;
    std::fs::write(&path, crate::store::hex(&bytes))?;
    Ok(bytes.to_vec())
}

fn unhex(text: &str) -> Option<Vec<u8>> {
    if !text.len().is_multiple_of(2) {
        return None;
    }
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(text.get(i..i + 2)?, 16).ok())
        .collect()
}

/// HMAC-SHA256 of the command under the home's key, as hex: the same command counts the same way, and
/// nobody without the key can test a guess against the stored value.
pub fn command_hmac(key: &[u8], command: &str) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(key).expect("HMAC takes any key length");
    mac.update(command.as_bytes());
    crate::store::hex(&mac.finalize().into_bytes())
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
    pub rule: Option<String>,
    pub output: Value,
}

struct Ctx {
    event: Event,
    input: Input,
    started: Instant,
    home: Option<Home>,
    store: Option<Store>,
    project: Option<String>,
    errors: Vec<String>,
}

impl Ctx {
    fn latency_ms(&self) -> i64 {
        i64::try_from(self.started.elapsed().as_millis()).unwrap_or(i64::MAX)
    }

    fn agent(&self) -> String {
        match self.event {
            Event::SubagentStop => self
                .input
                .agent_type
                .clone()
                .or_else(|| self.input.agent_id.clone())
                .unwrap_or_else(|| "subagent".to_owned()),
            _ => "main".to_owned(),
        }
    }

    fn note_error(&mut self, what: &str, e: &anyhow::Error) {
        let text = format!("{what}: {e:#}");
        eprintln!("groundworks hook {}: {text}", self.event.name());
        self.errors.push(text);
    }

    /// Write the hook_event row; a failure here goes to stderr and nowhere else.
    fn log(&mut self, outcome: &Outcome, uses: ToolUses, writes: u64, hmac: Option<String>) {
        let Some(store) = &self.store else { return };
        let error = (!self.errors.is_empty()).then(|| redact(&self.errors.join(" | ")));
        let row = HookEventIn {
            session: self.input.session_id.clone(),
            project: self.project.clone(),
            agent: self.agent(),
            hook: self.event.name(),
            decision: outcome.decision,
            rule: outcome.rule.clone(),
            tool_uses: uses.json(),
            journal_writes_since_start: writes,
            latency_ms: self.latency_ms(),
            error,
            command_hmac: hmac,
        };
        if let Err(e) = store.hook_event(&row) {
            eprintln!("groundworks hook {}: hook_event not recorded: {e:#}", self.event.name());
        }
    }
}

/// Run one hook: `input` is stdin's JSON; the returned value is printed to stdout. Never panics on bad
/// input and never returns an error: a hook that cannot do its job says so in its output.
pub fn run(event: Event, input: &str, rules_path: Option<&Path>) -> Value {
    let started = Instant::now();
    let mut errors = Vec::new();
    let input: Input = match serde_json::from_str(input) {
        Ok(v) => v,
        Err(e) => {
            errors.push(format!("stdin is not hook JSON: {e}"));
            Input::default()
        }
    };
    let cwd = input.cwd.clone().map(PathBuf::from);
    let home = match home_for(cwd.as_deref()) {
        Ok(h) => h,
        Err(e) => {
            errors.push(format!("no home: {e:#}"));
            None
        }
    };
    let store = match &home {
        Some(h) => match Store::open_if_up(h) {
            Ok(s) => s,
            Err(e) => {
                errors.push(format!("store: {e:#}"));
                None
            }
        },
        None => None,
    };
    let project = std::env::var("GROUNDWORKS_PROJECT")
        .ok()
        .filter(|p| !p.is_empty())
        .or_else(|| home.as_ref().and_then(|h| project_for(h, cwd.as_deref())));
    let mut ctx = Ctx {
        event,
        input,
        started,
        home,
        store,
        project,
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
        let notice = format!(
            "Groundworks: no home found from {} (no .groundworks/groundworks.toml above it, GROUNDWORKS_HOME unset); nothing recorded.",
            ctx.input.cwd.as_deref().unwrap_or("?")
        );
        return context_output(Event::SessionStart, None, Some(notice));
    };
    let Some(project) = ctx.project.clone() else {
        let notice = format!(
            "Groundworks: {} is not a registered project (project_register or /groundworks:init to bring it in); this session is not recorded.",
            ctx.input.cwd.as_deref().unwrap_or("?")
        );
        let outcome = Outcome {
            decision: "noop",
            rule: Some("not-registered".to_owned()),
            output: context_output(Event::SessionStart, None, Some(notice)),
        };
        ctx.log(&outcome, ToolUses::default(), 0, None);
        return outcome.output;
    };
    let session_id = ctx.input.session_id.clone();
    if let Err(e) = persist_env(&[
        ("GROUNDWORKS_PROJECT", &project),
        ("GROUNDWORKS_SESSION_ID", &session_id),
        ("GROUNDWORKS_SESSION_START", &now),
    ]) {
        ctx.note_error("CLAUDE_ENV_FILE", &e);
    }
    let Some(store) = store else {
        let _ = persist_env(&[("GROUNDWORKS_DOWN", "1")]);
        let notice = format!(
            "Groundworks: store down at {} for {project}: the journal could not be read and nothing this session will be recorded (start it: groundworks store status).",
            crate::store::store_url(&home)
        );
        ctx.home = Some(home);
        return context_output(Event::SessionStart, None, Some(notice));
    };
    let (context, injected) = match crate::ops::session_brief(&home, &store, &project, 10) {
        Ok(pair) => pair,
        Err(e) => {
            ctx.note_error("journal", &e);
            (
                format!("Groundworks: the journal of {project} could not be read ({e:#})."),
                0,
            )
        }
    };
    let row = SessionIn {
        id: session_id,
        project: project.clone(),
        machine: crate::home::machine(),
        client: client_name(),
        agent: "main".to_owned(),
        started_at: now,
        cwd: ctx.input.cwd.clone().unwrap_or_default(),
        injected_entries: injected as u64,
        store_ok: true,
        hook_latency_ms: ctx.latency_ms(),
    };
    let resumed = matches!(ctx.input.source.as_deref(), Some("resume" | "compact" | "clear"));
    match store.session_exists(&row.id) {
        Ok(true) => {}
        Ok(false) => {
            if let Err(e) = store.session_start(&row) {
                ctx.note_error("session row", &e);
            }
        }
        Err(e) => ctx.note_error("session lookup", &e),
    }
    let notice = ctx
        .errors
        .first()
        .map(|e| format!("Groundworks: {} (the session continues)", redact(e)));
    let outcome = Outcome {
        decision: if resumed { "noop" } else { "allow" },
        rule: None,
        output: context_output(Event::SessionStart, Some(context), notice),
    };
    ctx.home = Some(home);
    ctx.store = Some(store);
    ctx.log(&outcome, ToolUses::default(), 0, None);
    outcome.output
}

/// Which client runs this session: `GROUNDWORKS_CLIENT`, else Claude Code's own entrypoint variable.
fn client_name() -> String {
    if let Ok(c) = std::env::var("GROUNDWORKS_CLIENT")
        && !c.is_empty()
    {
        return c;
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
    let hmac = ctx.home.as_ref().and_then(|h| match hmac_key(h) {
        Ok(key) => Some(command_hmac(&key, command)),
        Err(e) => {
            eprintln!("groundworks hook PreToolUse: no HMAC key: {e:#}");
            None
        }
    });
    let mode = mode(ctx.home.as_ref(), Event::PreToolUse);
    let outcome = match Rules::load(rules_path) {
        Err(e) => {
            // Fail closed: a guard that cannot read its rules blocks (principle 8).
            ctx.note_error("rules", &e);
            Outcome {
                decision: "deny",
                rule: Some("rules-unloadable".to_owned()),
                output: deny_output(&format!(
                    "Groundworks: the deny list could not be loaded ({}); every Bash call is refused until it is fixed.",
                    redact(&format!("{e:#}"))
                )),
            }
        }
        Ok(rules) => match rules.check(command) {
            None => Outcome {
                decision: "allow",
                rule: None,
                output: json!({}),
            },
            Some(rule) if rule.action == Action::Deny && (mode == EnforceMode::Block || rule.hard) => Outcome {
                decision: "deny",
                rule: Some(rule.id.clone()),
                output: deny_output(&rule.reason),
            },
            Some(rule) => Outcome {
                decision: "warn",
                rule: Some(rule.id.clone()),
                output: json!({"systemMessage": format!("Groundworks ({}): {}", rule.id, rule.reason)}),
            },
        },
    };
    ctx.log(&outcome, ToolUses::default(), 0, hmac);
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
            rule: Some("not-registered".to_owned()),
            output: json!({"decision": "approve"}),
        };
        ctx.log(&outcome, ToolUses::default(), 0, None);
        return outcome.output;
    };
    let transcript = ctx
        .input
        .transcript_path
        .as_deref()
        .map(|p| std::fs::read_to_string(p).unwrap_or_default())
        .unwrap_or_default();
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
                ctx.note_error("facts_since", &e);
                None
            }
        },
        (Some(store), None) => match store.facts(&project, None, 1) {
            // No start known: any entry at all counts, rather than blocking a session that did record.
            Ok(rows) => Some(rows.len() as u64),
            Err(e) => {
                ctx.note_error("facts", &e);
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
            rule: rule.map(str::to_owned),
            output: json!({"decision": "approve", "systemMessage": format!("Groundworks ({}): {reason}", rule.unwrap_or(""))}),
        },
        ("block", EnforceMode::Block) => Outcome {
            decision: "block",
            rule: rule.map(str::to_owned),
            output: json!({"decision": "block", "reason": format!("Groundworks ({}): {reason}", rule.unwrap_or(""))}),
        },
        _ => Outcome {
            decision: "approve",
            rule: None,
            output: json!({"decision": "approve"}),
        },
    };
    ctx.log(&outcome, uses, writes.unwrap_or(0), None);
    outcome.output
}

/// Telemetry read back for the pulse: hook events grouped by session, newest window first.
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
            .expect("the placeholder rule");
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
    fn redaction_hides_tokens_and_caps_length() {
        let key = ["AK", "IAIOSFODNN7EXAMPLE"].concat().repeat(2);
        let r = redact(&format!("curl -H 'Authorization: Bearer abc.def' x; key={key} ok"));
        assert_eq!(r, "curl -H 'Authorization: <redacted> x; key=<redacted> ok");
        assert_eq!(redact(&"a".repeat(31)), "a".repeat(31));
        assert_eq!(redact(&"a".repeat(32)), "<redacted>");
        assert_eq!(redact(&"word ".repeat(100)).chars().count(), ERROR_CAP);
    }

    #[test]
    fn the_hmac_is_keyed_and_hex() {
        let a = command_hmac(b"key-one", "git push");
        let b = command_hmac(b"key-two", "git push");
        assert_eq!(a.len(), 64);
        assert_ne!(a, b);
        assert_eq!(a, command_hmac(b"key-one", "git push"));
        assert_eq!(unhex("00ff").unwrap(), vec![0, 255]);
        assert!(unhex("0").is_none());
        assert!(unhex("zz").is_none());
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
}
