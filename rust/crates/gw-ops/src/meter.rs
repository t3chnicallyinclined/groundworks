//! The meter (`groundworks cost`): what Claude Code sessions cost in tokens, per registered project, read
//! from the transcripts Claude Code keeps under `~/.claude/projects`, with no model and nothing sent
//! anywhere. One line per run is appended to `<home>/metrics/cost.jsonl`; `pulse` reads the newest line.
//!
//! A port of tokenmeter.py (033ef15) `scan` / `summarize` / `compare` / `weekly` / `preload`, starting
//! from fleet-core rs-02 (`turn.rs`: a turn is one API response, deduplicated on (message.id, requestId);
//! `lines.rs`: complete lines only, invalid UTF-8 dropped), with these divergences, each tested:
//! 1. No assistant text and no tool input is kept: a turn is token numbers, a model, a time and tool names.
//! 2. Subagent transcripts are found at any depth under `<session>/subagents/` (tokenmeter's glob stops at
//!    one level and misses `subagents/workflows/`).
//! 3. Model prices are data (`prices.toml`); tokens are the unit and USD a derived column.
//! 4. Turns are attributed by the working directory each line records, to a registered project (its
//!    worktrees included) or to a class, never to a transcript folder: a folder name is a path.
//!
//! Privacy (team/security TELEMETRY-POLICY.md; gw-security ruling 2026-10-30): a transcript folder whose
//! name, dashes read as spaces, matches a `scan.local_only` glob, or names a project the owner isolated,
//! is never opened; a transcript whose recorded working directory falls under either is dropped whole and
//! counted nowhere. Nothing written names a path. Two reads beyond the ruling are behind flags that default
//! off until gw-security signs them: [`Reads::overhead`] (byte lengths of Groundworks tool results and of
//! the injected brief) and [`Reads::rereads`] (the Read tool's file_path, hashed in memory and never
//! stored, plus Read/Grep/Glob result byte lengths).
//!
//! Speed: streaming reads, a substring pre-filter before any JSON parse, typed borrowing structs (never
//! `Value`), files in parallel on `std::thread::scope`, and an incremental cache
//! (`<home>/metrics/cost-cache.jsonl`, counts only) keyed by session, size, mtime and byte offset, so a
//! second run parses only what changed or grew.

use std::borrow::Cow;
use std::cell::Cell;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fmt::Write as _;
use std::io::{BufRead, BufReader, Seek, SeekFrom, Write as _};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use anyhow::{Context, Result, bail};
use serde::de::{self, Deserializer, IgnoredAny, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::home::Home;
use crate::hook::{CwdClass, classify_cwd, project_for};
use crate::registry::Registry;
use crate::scan::Glob;
use crate::store::Store;

/// Bytes of text per token, measured over 114 packets (eval.rs). Every token count made from bytes is an
/// estimate and is labelled so; real `usage` numbers are used wherever a transcript has them.
pub const BYTES_PER_TOKEN: f64 = crate::eval::BYTES_PER_TOKEN;
/// The window, in days; the previous window is the same length just before it.
pub const WINDOW_DAYS: i64 = 7;
/// Red when Groundworks content is more than this share of the input tokens in the window (A1.4; an
/// inference, to be reset after the first measurement).
pub const OVERHEAD_RED: f64 = 0.10;
/// Red when tokens per human turn rise more than this, week on week, with no model-mix change.
pub const TURN_RISE_RED: f64 = 0.20;
/// A model's share of turns moving more than this, week on week, is a model-mix change.
pub const MODEL_MIX_SHIFT: f64 = 0.10;
/// Bumped whenever the cache's meaning changes: an old cache is then ignored, not misread.
const CACHE_VERSION: u32 = 1;

pub const ROOT_LABEL: &str = "(root)";
pub const WORKSPACE_LABEL: &str = "(workspace)";
pub const HOME_LABEL: &str = "(home)";
pub const OUTSIDE_LABEL: &str = "(outside)";

/// Groundworks MCP tools, as Claude Code names them: the plugin's server, or the server added by hand.
const GW_PREFIXES: [&str; 2] = ["mcp__plugin_groundworks_groundworks__", "mcp__groundworks__"];

/// A line is parsed only if it holds one of these; every line the meter reads holds at least one.
const PREFILTER: [&str; 6] = [
    "\"usage\"",
    "\"tool_use\"",
    "\"tool_result\"",
    "\"type\":\"user\"",
    "hook_additional_context",
    "compact_boundary",
];

/// The reads beyond the gw-security ruling of 2026-10-30, off until it signs them.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Reads {
    /// Byte lengths of Groundworks tool results and of the brief the SessionStart hook injects.
    pub overhead: bool,
    /// The Read tool's file_path, hashed in memory (never stored), and Read/Grep/Glob result byte lengths.
    pub rereads: bool,
}

thread_local! {
    /// The reads in force on this parsing thread (set by each worker before it parses).
    static READS: Cell<Reads> = const { Cell::new(Reads { overhead: false, rereads: false }) };
}

fn reads() -> Reads {
    READS.with(Cell::get)
}

pub struct Options {
    /// The transcripts root (`<claude dir>/projects`).
    pub root: PathBuf,
    pub reads: Reads,
    pub threads: usize,
    /// Read and write `<home>/metrics/cost-cache.jsonl`.
    pub cache: bool,
    /// tokenmeter's subagent glob (`*/subagents/*.jsonl`, one level), for the differential only.
    pub flat_subagents: bool,
}

impl Options {
    pub fn new(root: PathBuf) -> Self {
        Self {
            root,
            reads: Reads::default(),
            threads: default_threads(),
            cache: true,
            flat_subagents: false,
        }
    }
}

/// min(8, available parallelism).
pub fn default_threads() -> usize {
    std::thread::available_parallelism().map_or(4, |n| n.get()).min(8)
}

/// The transcripts root: `<claude dir>/projects`.
pub fn default_root() -> PathBuf {
    crate::ops::claude_dir().join("projects")
}

// ---- the fence --------------------------------------------------------------------------------------

/// What is never opened: `scan.local_only` globs, read against folder names with dashes as spaces, and
/// the directories of isolated projects, in Claude Code's folder-name form.
pub struct Fence {
    local_only: Vec<Glob>,
    isolated_slugs: Vec<String>,
}

/// A path as Claude Code names its transcript folder: every character that is not a letter or digit
/// becomes a dash. Lowercased here, for comparison only.
pub fn slug_form(path: &str) -> String {
    path.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() {
                c.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect()
}

impl Fence {
    pub fn new(local_only: &[String], isolated_dirs: &[PathBuf]) -> Self {
        let local_only = local_only
            .iter()
            .map(|g| {
                // Letters and digits kept, `*` and `?` kept, everything else a space (as a folder name
                // read with dashes as spaces has it), and matched anywhere in the name.
                let body: String = g
                    .chars()
                    .map(|c| match c {
                        '*' | '?' => c,
                        c if c.is_alphanumeric() => c.to_ascii_lowercase(),
                        _ => ' ',
                    })
                    .collect();
                Glob::new(&format!("*{}*", body.trim_matches('*')))
            })
            .collect();
        let isolated_slugs = isolated_dirs
            .iter()
            .map(|d| slug_form(&d.display().to_string()).trim_end_matches('-').to_owned())
            .filter(|s| !s.is_empty())
            .collect();
        Self {
            local_only,
            isolated_slugs,
        }
    }

    /// Whether a transcript folder (or a working directory in folder-name form) is fenced.
    pub fn slug_fenced(&self, slug: &str) -> bool {
        let spaced: String = slug.to_lowercase().replace('-', " ");
        if self.local_only.iter().any(|g| g.matches(&spaced)) {
            return true;
        }
        let lower = slug.to_lowercase();
        self.isolated_slugs.iter().any(|s| {
            lower
                .strip_prefix(s.as_str())
                .is_some_and(|rest| rest.is_empty() || rest.starts_with('-'))
        })
    }
}

/// Maps a recorded working directory to a label: a registered project's name, or a class. None: fenced.
pub struct Labeler<'a> {
    home: &'a Home,
    isolated: &'a BTreeSet<String>,
    fence: &'a Fence,
    workspace: PathBuf,
    memo: Mutex<HashMap<String, Option<String>>>,
}

impl<'a> Labeler<'a> {
    pub fn new(home: &'a Home, isolated: &'a BTreeSet<String>, fence: &'a Fence) -> Self {
        let workspace = std::fs::canonicalize(&home.workspace).unwrap_or_else(|_| home.workspace.clone());
        Self {
            home,
            isolated,
            fence,
            workspace,
            memo: Mutex::new(HashMap::new()),
        }
    }

    pub fn label(&self, cwd: &str) -> Option<String> {
        if let Some(hit) = self.memo.lock().expect("label memo poisoned").get(cwd) {
            return hit.clone();
        }
        let label = self.resolve(cwd);
        self.memo
            .lock()
            .expect("label memo poisoned")
            .insert(cwd.to_owned(), label.clone());
        label
    }

    fn resolve(&self, cwd: &str) -> Option<String> {
        let path = Path::new(cwd);
        let class = classify_cwd(self.home, Some(path));
        if class == CwdClass::LocalOnly || self.fence.slug_fenced(&slug_form(cwd)) {
            return None;
        }
        if let Some(name) = project_for(self.home, Some(path)) {
            return (!self.isolated.contains(&name)).then_some(name);
        }
        let canon = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
        Some(
            if canon == self.workspace {
                ROOT_LABEL
            } else {
                match class {
                    CwdClass::Home => HOME_LABEL,
                    CwdClass::WorkspaceUnregistered => WORKSPACE_LABEL,
                    _ => OUTSIDE_LABEL,
                }
            }
            .to_owned(),
        )
    }
}

// ---- what one transcript holds ----------------------------------------------------------------------

/// One API response: dedup key hash, time (epoch ms), model and label (indexes into the file's lists),
/// input, cache read, cache write, output, and the Groundworks bytes in context when it was sent.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub struct TurnRec(
    pub u64,
    pub i64,
    pub u16,
    pub u16,
    pub u64,
    pub u64,
    pub u64,
    pub u64,
    pub u64,
);

impl TurnRec {
    fn ctx(&self) -> u64 {
        self.4 + self.5 + self.6
    }
}

/// One Groundworks tool call: time (epoch ms), label, the tool (`ask`, `run`, ...), latency to its
/// result in ms, and the result's byte length (with [`Reads::overhead`]).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct GwCall(pub i64, pub u16, pub String, pub Option<i64>, pub Option<u64>);

/// Per-transcript Read/Grep counts (with [`Reads::rereads`]); aggregates only.
#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReadStats {
    pub read_calls: u64,
    /// Distinct (file, offset, limit) Read 3 or more times.
    pub reread_files: u64,
    /// Reads of those beyond the first.
    pub reread_calls: u64,
    /// Bytes those repeat reads returned.
    pub reread_bytes: u64,
    /// Read/Grep/Glob result bytes before the context's first Groundworks ask, and after it.
    pub search_bytes_before_ask: u64,
    pub search_bytes_after_ask: u64,
}

/// Everything the meter keeps of one transcript file. Counts, times, ids and names only. No `Debug`:
/// with `--rereads` it holds keyed Read hashes in memory, which nothing may print (gw-security 2026-10-30).
#[derive(Clone, Default, Serialize, Deserialize)]
pub struct FileScan {
    pub session: String,
    /// The subagent's file stem (and its sub-folder under `subagents/`), None for the main session.
    pub agent: Option<String>,
    pub size: u64,
    pub mtime: i64,
    /// Bytes consumed: complete lines only, so a growing file resumes here.
    pub offset: u64,
    /// Dropped whole: a recorded working directory is fenced. Nothing else is kept.
    pub fenced: bool,
    pub labels: Vec<String>,
    pub models: Vec<String>,
    pub turns: Vec<TurnRec>,
    /// Real user prompts (a user line whose content is text) in the main session: (time, label).
    pub prompts: Vec<(i64, u16)>,
    pub gw: Vec<GwCall>,
    /// The Groundworks brief injected at session start: (time, label, bytes), with [`Reads::overhead`].
    pub briefs: Vec<(i64, u16, u64)>,
    pub reads: ReadStats,
    pub first: Option<(i64, u16)>,
    // Resumable state.
    pub last_label: Option<u16>,
    pub gw_ctx: u64,
    pub asked: bool,
    pub pending: BTreeMap<String, usize>,
    #[serde(skip)]
    read_keys: HashMap<u64, (u32, u64)>,
    #[serde(skip)]
    read_ids: HashMap<String, Option<u64>>,
}

impl FileScan {
    fn intern(list: &mut Vec<String>, name: &str) -> u16 {
        match list.iter().position(|l| l == name) {
            Some(i) => i as u16,
            None => {
                list.push(name.to_owned());
                (list.len() - 1) as u16
            }
        }
    }

    fn fence(&mut self) {
        *self = FileScan {
            session: std::mem::take(&mut self.session),
            agent: self.agent.take(),
            size: self.size,
            mtime: self.mtime,
            fenced: true,
            ..FileScan::default()
        };
    }

    /// Fold the Read counts into [`ReadStats`] (called when a file is finished).
    fn settle_reads(&mut self) {
        let (mut files, mut calls, mut bytes) = (0, 0, 0);
        for (count, repeat_bytes) in self.read_keys.values() {
            if *count >= 3 {
                files += 1;
                calls += u64::from(*count - 1);
                bytes += repeat_bytes;
            }
        }
        self.reads.reread_files = files;
        self.reads.reread_calls = calls;
        self.reads.reread_bytes = bytes;
        // The hashes have done their job: nothing keeps them past the file.
        self.read_keys.clear();
        self.read_ids.clear();
    }

    /// The label most turns carry (the first line's when there are none).
    fn main_label(&self) -> Option<u16> {
        let mut n: HashMap<u16, usize> = HashMap::new();
        for t in &self.turns {
            *n.entry(t.3).or_default() += 1;
        }
        n.into_iter()
            .max_by_key(|(l, c)| (*c, std::cmp::Reverse(*l)))
            .map(|(l, _)| l)
            .or(self.first.map(|f| f.1))
    }
}

// ---- typed lines (borrowing, never Value) -------------------------------------------------------------

#[derive(Deserialize)]
struct Line<'a> {
    #[serde(rename = "type", borrow, default)]
    kind: Option<Cow<'a, str>>,
    #[serde(borrow, default)]
    timestamp: Option<Cow<'a, str>>,
    #[serde(borrow, default)]
    cwd: Option<Cow<'a, str>>,
    #[serde(rename = "requestId", borrow, default)]
    request_id: Option<Cow<'a, str>>,
    #[serde(borrow, default)]
    subtype: Option<Cow<'a, str>>,
    #[serde(borrow, default)]
    message: Option<Msg<'a>>,
    #[serde(borrow, default)]
    attachment: Option<Attachment<'a>>,
}

#[derive(Deserialize)]
struct Msg<'a> {
    #[serde(borrow, default)]
    id: Option<Cow<'a, str>>,
    #[serde(borrow, default)]
    model: Option<Cow<'a, str>>,
    #[serde(default)]
    usage: Option<Usage>,
    #[serde(borrow, default)]
    content: Option<Content<'a>>,
}

#[derive(Deserialize)]
struct Attachment<'a> {
    #[serde(rename = "type", borrow, default)]
    kind: Option<Cow<'a, str>>,
    #[serde(default)]
    content: Option<Marked>,
}

#[derive(Deserialize)]
struct Block<'a> {
    #[serde(rename = "type", borrow, default)]
    kind: Option<Cow<'a, str>>,
    #[serde(borrow, default)]
    name: Option<Cow<'a, str>>,
    #[serde(borrow, default)]
    id: Option<Cow<'a, str>>,
    #[serde(borrow, default)]
    tool_use_id: Option<Cow<'a, str>>,
    #[serde(default)]
    input: Option<ReadKey>,
    #[serde(default)]
    content: Option<Bytes>,
}

/// `message.usage`: the four token numbers, and whether the object had any field (tokenmeter skips an
/// empty one, as Python truthiness does).
#[derive(Default)]
struct Usage {
    fields: usize,
    input: i64,
    cache_read: i64,
    cache_write: i64,
    output: i64,
}

impl<'de> Deserialize<'de> for Usage {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Usage;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("a usage object")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut m: A) -> Result<Usage, A::Error> {
                let mut u = Usage::default();
                while let Some(key) = m.next_key::<Cow<'de, str>>()? {
                    u.fields += 1;
                    let slot = match key.as_ref() {
                        "input_tokens" => &mut u.input,
                        "cache_read_input_tokens" => &mut u.cache_read,
                        "cache_creation_input_tokens" => &mut u.cache_write,
                        "output_tokens" => &mut u.output,
                        _ => {
                            m.next_value::<IgnoredAny>()?;
                            continue;
                        }
                    };
                    *slot = m.next_value::<Option<i64>>()?.unwrap_or(0);
                }
                Ok(u)
            }
        }
        d.deserialize_map(V)
    }
}

/// `message.content`: a user's typed text (only that it was text is kept), or blocks.
enum Content<'a> {
    Text,
    Blocks(Vec<Block<'a>>),
    Other,
}

impl<'de: 'a, 'a> Deserialize<'de> for Content<'a> {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V<'a>(std::marker::PhantomData<&'a ()>);
        impl<'de: 'a, 'a> Visitor<'de> for V<'a> {
            type Value = Content<'a>;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("message content")
            }
            fn visit_str<E: de::Error>(self, _: &str) -> Result<Content<'a>, E> {
                Ok(Content::Text)
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut s: A) -> Result<Content<'a>, A::Error> {
                let mut blocks = Vec::new();
                while let Some(b) = s.next_element::<Option<Block<'de>>>()? {
                    blocks.extend(b);
                }
                Ok(Content::Blocks(blocks))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut m: A) -> Result<Content<'a>, A::Error> {
                while m.next_entry::<IgnoredAny, IgnoredAny>()?.is_some() {}
                Ok(Content::Other)
            }
            fn visit_unit<E: de::Error>(self) -> Result<Content<'a>, E> {
                Ok(Content::Other)
            }
        }
        d.deserialize_any(V(std::marker::PhantomData))
    }
}

/// The byte length of the text in a value (strings, and `text`/`content` fields of blocks; images and
/// other fields count nothing), or nothing at all when no byte read is on.
struct Bytes(Option<u64>);

fn text_len<'de, D: Deserializer<'de>>(d: D, marker: bool) -> Result<(u64, bool), D::Error> {
    struct V {
        marker: bool,
    }
    impl<'de> Visitor<'de> for V {
        type Value = (u64, bool);
        fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
            f.write_str("any value")
        }
        fn visit_str<E: de::Error>(self, v: &str) -> Result<(u64, bool), E> {
            let gw = self.marker && (v.contains("groundworks-journal") || v.starts_with("Groundworks"));
            Ok((v.len() as u64, gw))
        }
        fn visit_seq<A: SeqAccess<'de>>(self, mut s: A) -> Result<(u64, bool), A::Error> {
            let (mut n, mut gw) = (0, false);
            while let Some(TextLen(len, g)) = s.next_element_seed(TextLenSeed(self.marker))? {
                n += len;
                gw |= g;
            }
            Ok((n, gw))
        }
        fn visit_map<A: MapAccess<'de>>(self, mut m: A) -> Result<(u64, bool), A::Error> {
            let (mut n, mut gw) = (0, false);
            while let Some(key) = m.next_key::<Cow<'de, str>>()? {
                if key == "text" || key == "content" {
                    let TextLen(len, g) = m.next_value_seed(TextLenSeed(self.marker))?;
                    n += len;
                    gw |= g;
                } else {
                    m.next_value::<IgnoredAny>()?;
                }
            }
            Ok((n, gw))
        }
        fn visit_bool<E: de::Error>(self, _: bool) -> Result<(u64, bool), E> {
            Ok((0, false))
        }
        fn visit_i64<E: de::Error>(self, _: i64) -> Result<(u64, bool), E> {
            Ok((0, false))
        }
        fn visit_u64<E: de::Error>(self, _: u64) -> Result<(u64, bool), E> {
            Ok((0, false))
        }
        fn visit_f64<E: de::Error>(self, _: f64) -> Result<(u64, bool), E> {
            Ok((0, false))
        }
        fn visit_unit<E: de::Error>(self) -> Result<(u64, bool), E> {
            Ok((0, false))
        }
    }
    d.deserialize_any(V { marker })
}

struct TextLen(u64, bool);
struct TextLenSeed(bool);

impl<'de> de::DeserializeSeed<'de> for TextLenSeed {
    type Value = TextLen;
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<TextLen, D::Error> {
        text_len(d, self.0).map(|(n, g)| TextLen(n, g))
    }
}

impl<'de> Deserialize<'de> for Bytes {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        let r = reads();
        if r.overhead || r.rereads {
            text_len(d, false).map(|(n, _)| Bytes(Some(n)))
        } else {
            IgnoredAny::deserialize(d).map(|_| Bytes(None))
        }
    }
}

/// An attachment's content: its byte length and whether it carries the Groundworks brief's frame, read
/// only with [`Reads::overhead`].
struct Marked(Option<(u64, bool)>);

impl<'de> Deserialize<'de> for Marked {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        if reads().overhead {
            text_len(d, true).map(|x| Marked(Some(x)))
        } else {
            IgnoredAny::deserialize(d).map(|_| Marked(None))
        }
    }
}

/// A tool input, read only with [`Reads::rereads`]: a keyed hash of (file_path, offset, limit), the path
/// held in memory for the hash and dropped; every other field is skipped unread. An input that is not an
/// object, or a file_path that is not a string, gives no key and costs the line nothing (the line's
/// usage still counts).
struct ReadKey(Option<u64>);

impl<'de> Deserialize<'de> for ReadKey {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        if !reads().rereads {
            return IgnoredAny::deserialize(d).map(|_| ReadKey(None));
        }
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = ReadKey;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("a tool input")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut m: A) -> Result<ReadKey, A::Error> {
                let (mut path, mut offset, mut limit) = (None, String::new(), String::new());
                while let Some(key) = m.next_key::<Cow<'de, str>>()? {
                    match key.as_ref() {
                        "file_path" => path = m.next_value::<Value>()?.as_str().map(str::to_owned),
                        "offset" => offset = m.next_value::<Value>()?.to_string(),
                        "limit" => limit = m.next_value::<Value>()?.to_string(),
                        _ => {
                            m.next_value::<IgnoredAny>()?;
                        }
                    }
                }
                Ok(ReadKey(path.map(|p| read_hash(&p, &offset, &limit))))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut s: A) -> Result<ReadKey, A::Error> {
                while s.next_element::<IgnoredAny>()?.is_some() {}
                Ok(ReadKey(None))
            }
            fn visit_str<E: de::Error>(self, _: &str) -> Result<ReadKey, E> {
                Ok(ReadKey(None))
            }
            fn visit_bool<E: de::Error>(self, _: bool) -> Result<ReadKey, E> {
                Ok(ReadKey(None))
            }
            fn visit_i64<E: de::Error>(self, _: i64) -> Result<ReadKey, E> {
                Ok(ReadKey(None))
            }
            fn visit_u64<E: de::Error>(self, _: u64) -> Result<ReadKey, E> {
                Ok(ReadKey(None))
            }
            fn visit_f64<E: de::Error>(self, _: f64) -> Result<ReadKey, E> {
                Ok(ReadKey(None))
            }
            fn visit_unit<E: de::Error>(self) -> Result<ReadKey, E> {
                Ok(ReadKey(None))
            }
        }
        d.deserialize_any(V)
    }
}

/// A random key per process for the Read path hash (gw-security 2026-10-30): the hash lives in memory
/// only, and without the key it cannot confirm a guessed path even if one ever leaked.
static READ_KEY: std::sync::LazyLock<[u8; 32]> = std::sync::LazyLock::new(|| {
    let mut key = [0u8; 32];
    getrandom::fill(&mut key).expect("OS randomness for the meter's Read key");
    key
});

/// The keyed hash of one Read: (path, case- and separator-folded; offset; limit) under [`READ_KEY`].
pub fn read_hash(path: &str, offset: &str, limit: &str) -> u64 {
    let path = path.to_lowercase().replace('\\', "/");
    hash64(&[
        READ_KEY.as_slice(),
        b"\0",
        path.as_bytes(),
        b"\0",
        offset.as_bytes(),
        b"\0",
        limit.as_bytes(),
    ])
}

fn hash64(parts: &[&[u8]]) -> u64 {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    for p in parts {
        h.update(p);
    }
    let d = h.finalize();
    u64::from_be_bytes(d[..8].try_into().expect("8 bytes"))
}

/// The dedup key of a turn (fleet-core `TurnKey`): (message.id, requestId), a missing one distinct from
/// any string, so two turns missing both collapse into one, as the oracle's tuple does.
fn turn_key(id: Option<&str>, request: Option<&str>) -> u64 {
    let part = |s: Option<&str>| s.map_or_else(|| b"\x00none".to_vec(), |s| [b"s:", s.as_bytes()].concat());
    hash64(&[&part(id), b"\x01", &part(request)])
}

/// The tool's name after the Groundworks prefix, if it is a Groundworks tool.
pub fn gw_tool(name: &str) -> Option<&str> {
    GW_PREFIXES.iter().find_map(|p| name.strip_prefix(p))
}

/// A command-line time: `YYYY-MM-DD` (midnight UTC, as tokenmeter's `arg_time`) or RFC 3339 UTC.
pub fn arg_time(s: &str) -> Result<i64> {
    let full = if s.len() == 10 {
        format!("{s}T00:00:00Z")
    } else {
        s.to_owned()
    };
    match epoch_ms(&full) {
        Some(t) => Ok(t),
        None => bail!("not a time: {s} (use YYYY-MM-DD or 2026-10-19T00:00:00Z)"),
    }
}

/// Epoch milliseconds of an RFC 3339 UTC time.
pub fn epoch_ms(ts: &str) -> Option<i64> {
    let ts = ts.trim_matches(|c| c == 'd' || c == '\'');
    let secs = crate::pulse::epoch_secs(ts)?;
    let frac: String = ts
        .get(19..)
        .and_then(|r| r.strip_prefix('.'))
        .map(|r| r.chars().take_while(char::is_ascii_digit).take(3).collect())
        .unwrap_or_default();
    let ms = if frac.is_empty() {
        0
    } else {
        format!("{frac:0<3}").parse::<i64>().unwrap_or(0)
    };
    Some(secs * 1000 + ms)
}

/// Python `bytes.decode("utf-8", errors="ignore")` (fleet-core lines.rs, as-is).
fn decode_utf8_ignore(mut bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len());
    loop {
        match std::str::from_utf8(bytes) {
            Ok(valid) => {
                s.push_str(valid);
                break;
            }
            Err(e) => {
                let valid_up_to = e.valid_up_to();
                s.push_str(std::str::from_utf8(&bytes[..valid_up_to]).expect("validated prefix"));
                let skip = e.error_len().unwrap_or(bytes.len() - valid_up_to).max(1);
                bytes = &bytes[(valid_up_to + skip).min(bytes.len())..];
                if bytes.is_empty() {
                    break;
                }
            }
        }
    }
    s
}

/// Parse from `fs.offset` to the last complete line. Returns the lines parsed (the pre-filter's survivors).
pub fn scan_file(path: &Path, main: bool, fs: &mut FileScan, labeler: &Labeler<'_>) -> std::io::Result<u64> {
    let mut f = std::fs::File::open(path)?;
    if fs.offset > 0 {
        f.seek(SeekFrom::Start(fs.offset))?;
    }
    let mut r = BufReader::with_capacity(1 << 20, f);
    let mut buf = Vec::with_capacity(1 << 16);
    let mut parsed = 0;
    loop {
        buf.clear();
        let n = r.read_until(b'\n', &mut buf)?;
        if n == 0 || buf.last() != Some(&b'\n') {
            break; // a trailing partial line waits for the next run
        }
        fs.offset += n as u64;
        let raw = &buf[..n - 1];
        if raw.iter().all(|b| matches!(b, b' ' | b'\t' | b'\r' | 0x0b | 0x0c)) {
            continue;
        }
        let text = match std::str::from_utf8(raw) {
            Ok(s) => Cow::Borrowed(s),
            Err(_) => Cow::Owned(decode_utf8_ignore(raw)),
        };
        if text.starts_with("{\"type\":\"api-request") || !PREFILTER.iter().any(|p| text.contains(p)) {
            continue;
        }
        let Ok(line) = serde_json::from_str::<Line>(&text) else {
            continue;
        };
        parsed += 1;
        if !take_line(line, main, fs, labeler) {
            fs.fence();
            return Ok(parsed);
        }
    }
    fs.settle_reads();
    Ok(parsed)
}

/// Fold one parsed line into the file's record; false when its working directory is fenced.
fn take_line(line: Line<'_>, main: bool, fs: &mut FileScan, labeler: &Labeler<'_>) -> bool {
    let label = match line.cwd.as_deref() {
        Some(cwd) => match labeler.label(cwd) {
            Some(l) => FileScan::intern(&mut fs.labels, &l),
            None => return false,
        },
        None => match fs.last_label {
            Some(l) => l,
            None => FileScan::intern(&mut fs.labels, OUTSIDE_LABEL),
        },
    };
    let Some(ts) = line.timestamp.as_deref().and_then(epoch_ms) else {
        return true;
    };
    fs.last_label = Some(label);
    if fs.first.is_none() {
        fs.first = Some((ts, label));
    }
    let r = reads();
    if line.subtype.as_deref() == Some("compact_boundary") {
        fs.gw_ctx = 0;
    }
    if let Some(a) = &line.attachment
        && a.kind.as_deref() == Some("hook_additional_context")
        && let Some(Marked(Some((len, true)))) = a.content
    {
        fs.briefs.push((ts, label, len));
        fs.gw_ctx += len;
    }
    let Some(msg) = line.message else {
        return true;
    };
    if let (Some(u), Some(model)) = (&msg.usage, msg.model.as_deref())
        && u.fields > 0
        && !model.is_empty()
        && model != "<synthetic>"
    {
        let m = FileScan::intern(&mut fs.models, model);
        let clamp = |v: i64| v.max(0) as u64;
        fs.turns.push(TurnRec(
            turn_key(msg.id.as_deref(), line.request_id.as_deref()),
            ts,
            m,
            label,
            clamp(u.input),
            clamp(u.cache_read),
            clamp(u.cache_write),
            clamp(u.output),
            fs.gw_ctx,
        ));
    }
    match msg.content {
        Some(Content::Text) if main && line.kind.as_deref() == Some("user") => fs.prompts.push((ts, label)),
        Some(Content::Blocks(blocks)) => {
            for b in blocks {
                match b.kind.as_deref() {
                    Some("tool_use") => {
                        let name = b.name.as_deref().unwrap_or_default();
                        let id = b.id.as_deref().unwrap_or_default();
                        if let Some(tool) = gw_tool(name) {
                            if tool == "ask" {
                                fs.asked = true;
                            }
                            fs.gw.push(GwCall(ts, label, tool.to_owned(), None, None));
                            fs.pending.insert(id.to_owned(), fs.gw.len() - 1);
                        } else if r.rereads && name == "Read" {
                            fs.reads.read_calls += 1;
                            let key = b.input.and_then(|k| k.0);
                            if let Some(k) = key {
                                fs.read_keys.entry(k).or_default().0 += 1;
                            }
                            fs.read_ids.insert(id.to_owned(), key);
                        } else if r.rereads && matches!(name, "Grep" | "Glob") {
                            fs.read_ids.insert(id.to_owned(), None);
                        }
                    }
                    Some("tool_result") => {
                        let id = b.tool_use_id.as_deref().unwrap_or_default();
                        let bytes = b.content.and_then(|c| c.0);
                        if let Some(i) = fs.pending.remove(id) {
                            let call = &mut fs.gw[i];
                            call.3 = Some(ts - call.0);
                            if r.overhead {
                                call.4 = Some(bytes.unwrap_or(0));
                                fs.gw_ctx += bytes.unwrap_or(0);
                            }
                        } else if let Some(key) = fs.read_ids.remove(id) {
                            let n = bytes.unwrap_or(0);
                            if fs.asked {
                                fs.reads.search_bytes_after_ask += n;
                            } else {
                                fs.reads.search_bytes_before_ask += n;
                            }
                            if let Some(k) = key
                                && let Some(e) = fs.read_keys.get_mut(&k)
                                && e.0 >= 2
                            {
                                e.1 += n;
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        _ => {}
    }
    true
}

// ---- finding the transcripts --------------------------------------------------------------------------

/// One transcript file found under the root.
#[derive(Debug, Clone)]
pub struct Source {
    pub folder: usize,
    pub path: PathBuf,
    pub session: String,
    pub agent: Option<String>,
    pub main: bool,
    pub size: u64,
    pub mtime: i64,
}

fn mtime_ns(meta: &std::fs::Metadata) -> i64 {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_nanos() as i64)
}

fn sorted_entries(dir: &Path) -> Vec<std::fs::DirEntry> {
    let mut v: Vec<std::fs::DirEntry> =
        std::fs::read_dir(dir).map_or_else(|_| Vec::new(), |rd| rd.filter_map(Result::ok).collect());
    v.sort_by_key(|e| e.file_name().to_string_lossy().to_lowercase());
    v
}

fn is_jsonl(p: &Path) -> bool {
    p.extension().is_some_and(|e| e == "jsonl")
}

/// Every transcript under `root`, folders in name order (fenced ones never listed past their name),
/// within a folder the main sessions then the subagents, as tokenmeter orders them.
pub fn discover(root: &Path, fence: &Fence, flat_subagents: bool) -> Result<(Vec<String>, Vec<Source>)> {
    let mut folders: Vec<String> = std::fs::read_dir(root)
        .with_context(|| format!("reading the transcripts root {}", root.display()))?
        .filter_map(Result::ok)
        .filter(|e| e.path().is_dir())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|name| !fence.slug_fenced(name))
        .collect();
    folders.sort();
    let mut out = Vec::new();
    for (i, name) in folders.iter().enumerate() {
        let dir = root.join(name);
        let entries = sorted_entries(&dir);
        for e in &entries {
            let p = e.path();
            if p.is_file() && is_jsonl(&p) {
                let Ok(meta) = e.metadata() else { continue };
                out.push(Source {
                    folder: i,
                    session: p.file_stem().unwrap_or_default().to_string_lossy().into_owned(),
                    agent: None,
                    main: true,
                    size: meta.len(),
                    mtime: mtime_ns(&meta),
                    path: p,
                });
            }
        }
        for e in &entries {
            let sub = e.path().join("subagents");
            if !sub.is_dir() {
                continue;
            }
            let session = e.file_name().to_string_lossy().into_owned();
            let mut stack = vec![sub.clone()];
            let mut files = Vec::new();
            while let Some(d) = stack.pop() {
                for f in sorted_entries(&d) {
                    let p = f.path();
                    if p.is_dir() {
                        if !flat_subagents {
                            stack.push(p);
                        }
                    } else if is_jsonl(&p) {
                        files.push(p);
                    }
                }
            }
            files.sort_by_key(|p| p.to_string_lossy().to_lowercase());
            for p in files {
                let Ok(meta) = std::fs::metadata(&p) else { continue };
                let rel = p.strip_prefix(&sub).unwrap_or(&p).with_extension("");
                out.push(Source {
                    folder: i,
                    session: session.clone(),
                    agent: Some(rel.to_string_lossy().replace('\\', "/")),
                    main: false,
                    size: meta.len(),
                    mtime: mtime_ns(&meta),
                    path: p,
                });
            }
        }
    }
    Ok((folders, out))
}

// ---- the corpus: every file, cached ------------------------------------------------------------------

#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ScanStats {
    pub files: u64,
    pub files_cached: u64,
    pub files_resumed: u64,
    pub files_parsed: u64,
    pub bytes_total: u64,
    pub bytes_read: u64,
    pub lines_parsed: u64,
    pub threads: u64,
    pub wall_ms: u64,
}

pub struct Corpus {
    /// Transcript folder names (fenced ones never listed), in memory only: a folder name is a path.
    pub folders: Vec<String>,
    pub sources: Vec<Source>,
    pub scans: Vec<FileScan>,
    /// Per file, per turn: false for a turn an earlier file (in order) already counted.
    pub kept: Vec<Vec<bool>>,
    pub stats: ScanStats,
}

#[derive(Serialize, Deserialize)]
struct CacheHead {
    version: u32,
    fingerprint: String,
}

fn cache_key(session: &str, agent: Option<&str>) -> String {
    format!("{session}|{}", agent.unwrap_or(""))
}

fn load_cache(path: &Path, fingerprint: &str) -> HashMap<String, FileScan> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return HashMap::new();
    };
    let mut lines = text.lines();
    let ok = lines
        .next()
        .and_then(|l| serde_json::from_str::<CacheHead>(l).ok())
        .is_some_and(|h| h.version == CACHE_VERSION && h.fingerprint == fingerprint);
    if !ok {
        return HashMap::new();
    }
    lines
        .filter_map(|l| serde_json::from_str::<FileScan>(l).ok())
        .map(|f| (cache_key(&f.session, f.agent.as_deref()), f))
        .collect()
}

fn write_cache(path: &Path, fingerprint: &str, scans: &[FileScan]) -> Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = path.with_extension("jsonl.tmp");
    {
        let mut f = std::io::BufWriter::new(std::fs::File::create(&tmp)?);
        serde_json::to_writer(
            &mut f,
            &CacheHead {
                version: CACHE_VERSION,
                fingerprint: fingerprint.to_owned(),
            },
        )?;
        f.write_all(b"\n")?;
        for s in scans {
            serde_json::to_writer(&mut f, s)?;
            f.write_all(b"\n")?;
        }
        f.flush()?;
    }
    std::fs::rename(&tmp, path)?;
    Ok(())
}

pub fn cache_path(home: &Home) -> PathBuf {
    home.root.join("metrics").join("cost-cache.jsonl")
}

impl Corpus {
    /// Find, then parse what the cache does not already hold, in parallel; then mark duplicate turns.
    pub fn load(cache: Option<(&Path, &str)>, opts: &Options, fence: &Fence, labeler: &Labeler<'_>) -> Result<Self> {
        let started = Instant::now();
        let (folders, sources) = discover(&opts.root, fence, opts.flat_subagents)?;
        let mut cached = cache.map(|(p, fp)| load_cache(p, fp)).unwrap_or_default();
        let mut stats = ScanStats {
            files: sources.len() as u64,
            threads: opts.threads.max(1) as u64,
            ..ScanStats::default()
        };
        // Decide per file: reuse, resume from the offset, or parse from the start.
        let mut work: Vec<(usize, FileScan)> = Vec::new();
        let mut scans: Vec<Option<FileScan>> = vec![None; sources.len()];
        for (i, s) in sources.iter().enumerate() {
            stats.bytes_total += s.size;
            let prev = cached.remove(&cache_key(&s.session, s.agent.as_deref()));
            match prev {
                Some(f) if f.size == s.size && f.mtime == s.mtime => {
                    stats.files_cached += 1;
                    scans[i] = Some(f);
                }
                Some(f) if !f.fenced && !opts.reads.rereads && f.offset <= s.size && f.size <= s.size => {
                    stats.files_resumed += 1;
                    stats.bytes_read += s.size - f.offset;
                    work.push((i, f));
                }
                _ => {
                    stats.files_parsed += 1;
                    stats.bytes_read += s.size;
                    work.push((
                        i,
                        FileScan {
                            session: s.session.clone(),
                            agent: s.agent.clone(),
                            ..FileScan::default()
                        },
                    ));
                }
            }
        }
        let next = AtomicUsize::new(0);
        let lines = AtomicUsize::new(0);
        let work = Mutex::new(work.into_iter().map(Some).collect::<Vec<_>>());
        let n_work = work.lock().expect("work").len();
        let done: Mutex<Vec<(usize, FileScan)>> = Mutex::new(Vec::with_capacity(n_work));
        let errors: Mutex<Vec<String>> = Mutex::new(Vec::new());
        std::thread::scope(|scope| {
            for _ in 0..opts.threads.max(1) {
                scope.spawn(|| {
                    READS.with(|r| r.set(opts.reads));
                    loop {
                        let k = next.fetch_add(1, Ordering::Relaxed);
                        if k >= n_work {
                            break;
                        }
                        let Some((i, mut fs)) = work.lock().expect("work")[k].take() else {
                            continue;
                        };
                        let s = &sources[i];
                        match scan_file(&s.path, s.main, &mut fs, labeler) {
                            Ok(n) => {
                                lines.fetch_add(n as usize, Ordering::Relaxed);
                                fs.size = s.size;
                                fs.mtime = s.mtime;
                                done.lock().expect("done").push((i, fs));
                            }
                            // The error text can carry a path: keep a count, never the text.
                            Err(e) => errors.lock().expect("errors").push(format!("{:?}", e.kind())),
                        }
                    }
                });
            }
        });
        for (i, fs) in done.into_inner().expect("done") {
            scans[i] = Some(fs);
        }
        let unreadable = errors.into_inner().expect("errors");
        if !unreadable.is_empty() {
            tracing_line(&format!(
                "{} transcript(s) unreadable: {:?}",
                unreadable.len(),
                unreadable
            ));
        }
        let (sources, scans): (Vec<Source>, Vec<FileScan>) = sources
            .into_iter()
            .zip(scans)
            .filter_map(|(s, f)| f.map(|f| (s, f)))
            .unzip();
        stats.lines_parsed = lines.into_inner() as u64;
        if let Some((p, fp)) = cache {
            write_cache(p, fp, &scans)?;
        }
        // Global dedup, first occurrence wins, in file order (tokenmeter's `seen`).
        let mut seen: HashSet<u64> = HashSet::new();
        let kept = scans
            .iter()
            .map(|f| f.turns.iter().map(|t| seen.insert(t.0)).collect())
            .collect();
        stats.wall_ms = started.elapsed().as_millis() as u64;
        Ok(Self {
            folders,
            sources,
            scans,
            kept,
            stats,
        })
    }

    /// Every kept turn: (file index, turn).
    fn turns(&self) -> impl Iterator<Item = (usize, &TurnRec)> {
        self.scans.iter().enumerate().flat_map(move |(i, f)| {
            f.turns
                .iter()
                .zip(&self.kept[i])
                .filter(|(_, k)| **k)
                .map(move |(t, _)| (i, t))
        })
    }

    fn label(&self, file: usize, idx: u16) -> &str {
        self.scans[file]
            .labels
            .get(idx as usize)
            .map_or(OUTSIDE_LABEL, String::as_str)
    }

    fn model(&self, file: usize, idx: u16) -> &str {
        self.scans[file].models.get(idx as usize).map_or("?", String::as_str)
    }

    pub fn first_turn_ms(&self) -> Option<i64> {
        self.turns().map(|(_, t)| t.1).min()
    }
}

/// Diagnostics go to stderr, never into a row (they may name nothing but counts and kinds anyway).
fn tracing_line(s: &str) {
    eprintln!("groundworks cost: {s}");
}

// ---- prices -------------------------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, Deserialize)]
struct PriceRow {
    input: f64,
    output: f64,
    #[serde(default)]
    cache_write: Option<f64>,
    #[serde(default)]
    cache_read: Option<f64>,
}

#[derive(Deserialize)]
struct PriceFile {
    models: BTreeMap<String, PriceRow>,
}

static PRICES: std::sync::LazyLock<BTreeMap<String, PriceRow>> = std::sync::LazyLock::new(|| {
    toml::from_str::<PriceFile>(include_str!("prices.toml"))
        .expect("prices.toml parses")
        .models
});

/// fleet-core `normalize_model_id`: a trailing `-YYYYMMDD` is dropped.
pub fn normalize_model_id(model: &str) -> &str {
    let b = model.as_bytes();
    if b.len() >= 9 && b[b.len() - 9] == b'-' && b[b.len() - 8..].iter().all(u8::is_ascii_digit) {
        &model[..model.len() - 9]
    } else {
        model
    }
}

/// Dollars for one turn at its own model's price, or None when the model has no row.
pub fn price_turn(model: &str, input: u64, cache_write: u64, cache_read: u64, output: u64) -> Option<f64> {
    let p = PRICES.get(normalize_model_id(model))?;
    let per = |x: f64| x / 1_000_000.0;
    let cw = p.cache_write.unwrap_or(p.input * 1.25);
    let cr = p.cache_read.unwrap_or(p.input * 0.1);
    Some(
        input as f64 * per(p.input)
            + cache_write as f64 * per(cw)
            + cache_read as f64 * per(cr)
            + output as f64 * per(p.output),
    )
}

// ---- the KPIs -----------------------------------------------------------------------------------------

/// The numbers for one scope (a label, or everything) over one span. Docs: docs/METRICS.md, `cost`.
#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq)]
pub struct Kpis {
    /// Claude Code session ids with a priced turn in scope (a main transcript and its subagents are one).
    pub sessions: u64,
    /// Transcript files (main and subagent) with a priced turn in scope.
    pub contexts: u64,
    pub turns: u64,
    pub human_turns: u64,
    pub input: u64,
    pub cache_read: u64,
    pub cache_write: u64,
    pub output: u64,
    /// input + cache read + cache write + output.
    pub tokens: u64,
    /// tokens / sessions, subagent tokens included.
    pub tokens_per_session: f64,
    /// tokens / human turns; 0 when there were none.
    pub tokens_per_human_turn: f64,
    pub main_ctx_p90: u64,
    /// Turns per model (dated ids folded into their undated row).
    pub models: BTreeMap<String, u64>,
    pub usd: f64,
    pub unpriced_turns: u64,
    pub gw_calls: u64,
    pub gw_calls_by_tool: BTreeMap<String, u64>,
    pub asks: u64,
    pub ask_latency_p50_ms: Option<i64>,
    pub ask_latency_p95_ms: Option<i64>,
    /// With `--overhead`: bytes of Groundworks tool results and of injected briefs.
    pub gw_result_bytes: Option<u64>,
    /// The part of gw_result_bytes that ask packets returned.
    pub ask_result_bytes: Option<u64>,
    pub brief_bytes: Option<u64>,
    pub packet_bytes_p50: Option<u64>,
    pub packet_bytes_p95: Option<u64>,
    /// (brief + gw result bytes) / 2.98, an estimate.
    pub gw_tokens_est: Option<f64>,
    /// Groundworks content carried in context, as a share of all input tokens (input + cache read +
    /// cache write): every turn re-reads what earlier turns brought in, until a compaction. Estimate.
    pub overhead_share: Option<f64>,
    /// With `--rereads`.
    pub rereads_per_session_median: Option<f64>,
    pub reread_calls: Option<u64>,
    pub reread_bytes: Option<u64>,
    pub search_bytes_before_ask: Option<u64>,
    pub search_bytes_after_ask: Option<u64>,
    /// Git commits on every branch, and gate receipts, in the span; None outside registered projects.
    pub commits: Option<u64>,
    pub gate_receipts: Option<u64>,
    /// (commits + gate receipts) per million tokens.
    pub work_per_mtok: Option<f64>,
}

/// The nearest-rank percentile of a sorted slice.
fn nearest_rank<T: Copy>(sorted: &[T], p: usize) -> Option<T> {
    if sorted.is_empty() {
        return None;
    }
    let rank = (p * sorted.len()).div_ceil(100).max(1);
    Some(sorted[rank - 1])
}

fn median(sorted: &[f64]) -> Option<f64> {
    let n = sorted.len();
    match n {
        0 => None,
        _ if n % 2 == 1 => Some(sorted[n / 2]),
        _ => Some((sorted[n / 2 - 1] + sorted[n / 2]) / 2.0),
    }
}

/// A span: [from, to) in epoch ms.
#[derive(Debug, Clone, Copy)]
pub struct Span(pub i64, pub i64);

impl Span {
    pub const ALL: Span = Span(i64::MIN, i64::MAX);
    fn has(&self, t: i64) -> bool {
        t >= self.0 && t < self.1
    }
}

/// Work events of one registered project, epoch ms; None when there is no repository here.
#[derive(Debug, Default, Clone)]
pub struct Work {
    pub commits: Option<Vec<i64>>,
    pub gates: Vec<i64>,
}

pub fn kpis(c: &Corpus, scope: Option<&str>, span: Span, reads: Reads, work: &BTreeMap<String, Work>) -> Kpis {
    let in_scope = |file: usize, label: u16| scope.is_none_or(|s| c.label(file, label) == s);
    let mut k = Kpis::default();
    let mut sessions: HashMap<&str, ()> = HashMap::new();
    let mut contexts: HashSet<usize> = HashSet::new();
    let mut main_ctx: Vec<u64> = Vec::new();
    let mut carried = 0.0;
    for (i, t) in c.turns() {
        if !span.has(t.1) || !in_scope(i, t.3) {
            continue;
        }
        k.turns += 1;
        k.input += t.4;
        k.cache_read += t.5;
        k.cache_write += t.6;
        k.output += t.7;
        sessions.insert(c.scans[i].session.as_str(), ());
        contexts.insert(i);
        if c.sources[i].main {
            main_ctx.push(t.ctx());
        }
        carried += t.8 as f64 / BYTES_PER_TOKEN;
        let model = c.model(i, t.2);
        *k.models.entry(normalize_model_id(model).to_owned()).or_default() += 1;
        match price_turn(model, t.4, t.6, t.5, t.7) {
            Some(usd) => k.usd += usd,
            None => k.unpriced_turns += 1,
        }
    }
    k.sessions = sessions.len() as u64;
    k.contexts = contexts.len() as u64;
    k.tokens = k.input + k.cache_read + k.cache_write + k.output;
    k.tokens_per_session = if k.sessions == 0 {
        0.0
    } else {
        k.tokens as f64 / k.sessions as f64
    };
    main_ctx.sort_unstable();
    k.main_ctx_p90 = if main_ctx.is_empty() {
        0
    } else {
        main_ctx[(main_ctx.len() as f64 * 0.9) as usize]
    };
    let mut latency = Vec::new();
    let mut packets = Vec::new();
    let (mut gw_bytes, mut brief_bytes, mut ask_bytes) = (0u64, 0u64, 0u64);
    let mut reread_by_session: BTreeMap<&str, u64> = sessions.keys().map(|s| (*s, 0)).collect();
    let (mut rr_calls, mut rr_bytes, mut before, mut after) = (0, 0, 0, 0);
    for (i, f) in c.scans.iter().enumerate() {
        for &(ts, label) in &f.prompts {
            if span.has(ts) && in_scope(i, label) {
                k.human_turns += 1;
            }
        }
        for g in &f.gw {
            if !span.has(g.0) || !in_scope(i, g.1) {
                continue;
            }
            k.gw_calls += 1;
            *k.gw_calls_by_tool.entry(g.2.clone()).or_default() += 1;
            gw_bytes += g.4.unwrap_or(0);
            if g.2 == "ask" {
                k.asks += 1;
                latency.extend(g.3);
                packets.extend(g.4);
                ask_bytes += g.4.unwrap_or(0);
            }
        }
        for &(ts, label, n) in &f.briefs {
            if span.has(ts) && in_scope(i, label) {
                brief_bytes += n;
            }
        }
        // A context's read counts belong to its main label and its first time.
        if let (Some((ts, _)), Some(label)) = (f.first, f.main_label())
            && span.has(ts)
            && in_scope(i, label)
        {
            if let Some(n) = reread_by_session.get_mut(f.session.as_str()) {
                *n += f.reads.reread_files;
            }
            rr_calls += f.reads.reread_calls;
            rr_bytes += f.reads.reread_bytes;
            before += f.reads.search_bytes_before_ask;
            after += f.reads.search_bytes_after_ask;
        }
    }
    k.tokens_per_human_turn = if k.human_turns == 0 {
        0.0
    } else {
        k.tokens as f64 / k.human_turns as f64
    };
    latency.sort_unstable();
    k.ask_latency_p50_ms = nearest_rank(&latency, 50);
    k.ask_latency_p95_ms = nearest_rank(&latency, 95);
    if reads.overhead {
        packets.sort_unstable();
        k.packet_bytes_p50 = nearest_rank(&packets, 50);
        k.packet_bytes_p95 = nearest_rank(&packets, 95);
        k.gw_result_bytes = Some(gw_bytes);
        k.ask_result_bytes = Some(ask_bytes);
        k.brief_bytes = Some(brief_bytes);
        k.gw_tokens_est = Some((gw_bytes + brief_bytes) as f64 / BYTES_PER_TOKEN);
        let input = (k.input + k.cache_read + k.cache_write) as f64;
        k.overhead_share = Some(if input == 0.0 { 0.0 } else { carried / input });
    }
    if reads.rereads {
        let mut per: Vec<f64> = reread_by_session.values().map(|n| *n as f64).collect();
        per.sort_by(f64::total_cmp);
        k.rereads_per_session_median = median(&per);
        k.reread_calls = Some(rr_calls);
        k.reread_bytes = Some(rr_bytes);
        k.search_bytes_before_ask = Some(before);
        k.search_bytes_after_ask = Some(after);
    }
    let mut commits: Option<u64> = None;
    let mut gates: Option<u64> = None;
    for (name, w) in work {
        if scope.is_some_and(|s| s != name) {
            continue;
        }
        if let Some(times) = &w.commits {
            commits = Some(commits.unwrap_or(0) + times.iter().filter(|t| span.has(**t)).count() as u64);
        }
        gates = Some(gates.unwrap_or(0) + w.gates.iter().filter(|t| span.has(**t)).count() as u64);
    }
    k.commits = commits;
    k.gate_receipts = gates;
    if commits.is_some() || gates.is_some() {
        let units = (commits.unwrap_or(0) + gates.unwrap_or(0)) as f64;
        k.work_per_mtok = Some(if k.tokens == 0 {
            0.0
        } else {
            units / (k.tokens as f64 / 1e6)
        });
    }
    k
}

// ---- the run line -------------------------------------------------------------------------------------

#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq)]
pub struct Totals {
    pub window: Kpis,
    pub previous_window: Kpis,
    pub all_time: Kpis,
}

#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProjectCost {
    pub window: Kpis,
    pub all_time: Kpis,
    /// When Groundworks was first seen here: the earliest session row the hooks wrote for this project,
    /// or the earliest Groundworks tool call in its transcripts, whichever is first.
    pub first_seen: Option<String>,
    /// All time, split at first_seen. A trend, confounded (model mix, task mix), never proof.
    pub before: Option<Kpis>,
    pub after: Option<Kpis>,
}

#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq)]
pub struct AskUse {
    /// Ask packets in the window and in the week before, eval sessions left out (store).
    pub asks: u64,
    pub passed: u64,
    pub previous_asks: u64,
    pub previous_passed: u64,
    /// Ask result bytes in the window / 2.98 / passed asks (with `--overhead`), an estimate.
    pub gw_tokens_per_passed: Option<f64>,
}

/// What a "session" is, counted (all time, fenced transcripts excluded). A session is one Claude Code
/// session id: its main transcript `<id>.jsonl` and every subagent transcript under `<id>/subagents/`,
/// counted when at least one of them holds a priced turn. It is not the hooks' session row.
#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq)]
pub struct TranscriptCounts {
    pub main: u64,
    pub subagent: u64,
    pub main_with_turns: u64,
    pub subagent_with_turns: u64,
    /// Distinct session ids with a priced turn in any of their transcripts.
    pub sessions: u64,
    /// Of those, sessions whose turns are all in subagent transcripts (the main transcript pruned or empty).
    pub sessions_subagent_only: u64,
}

pub fn transcript_counts(c: &Corpus) -> TranscriptCounts {
    let mut t = TranscriptCounts::default();
    let mut with_main: HashSet<&str> = HashSet::new();
    let mut any: HashSet<&str> = HashSet::new();
    for (i, f) in c.scans.iter().enumerate() {
        if f.fenced {
            continue;
        }
        let main = c.sources[i].main;
        let has = c.kept[i].iter().any(|k| *k);
        if main {
            t.main += 1;
            t.main_with_turns += u64::from(has);
        } else {
            t.subagent += 1;
            t.subagent_with_turns += u64::from(has);
        }
        if has {
            any.insert(f.session.as_str());
            if main {
                with_main.insert(f.session.as_str());
            }
        }
    }
    t.sessions = any.len() as u64;
    t.sessions_subagent_only = any.difference(&with_main).count() as u64;
    t
}

#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq)]
pub struct CostLine {
    pub at: String,
    pub machine: String,
    pub window_days: i64,
    pub reads: Reads,
    pub bytes_per_token: f64,
    pub scan: ScanStats,
    #[serde(default)]
    pub transcripts: TranscriptCounts,
    pub totals: Totals,
    pub per_project: BTreeMap<String, ProjectCost>,
    pub asks: AskUse,
    pub red: Vec<String>,
}

fn iso(ms: i64) -> String {
    crate::pulse::iso_from_secs(ms.div_euclid(1000))
}

/// Which registered projects the owner isolated (store level). Fails closed: a level that cannot be read
/// is an error, never "not isolated".
pub fn isolated_projects(store: &Store, registry: &Registry) -> Result<BTreeSet<String>> {
    let mut out = BTreeSet::new();
    for name in registry.projects.keys() {
        let ns = gw_store::project_ns(&crate::store::slug(name)?)?;
        let level = store
            .level(&ns)
            .with_context(|| format!("reading the level of {name}: the meter will not run without it"))?;
        if level == "isolated" {
            out.insert(name.clone());
        }
    }
    Ok(out)
}

/// Load the corpus for this home (fence, labels, cache) with `opts`.
pub fn load(home: &Home, store: &Store, opts: &Options) -> Result<Corpus> {
    let registry = Registry::load(home)?;
    let isolated = isolated_projects(store, &registry)?;
    let isolated_dirs: Vec<PathBuf> = isolated
        .iter()
        .filter_map(|n| registry.projects.get(n))
        .map(|p| p.path(&home.workspace))
        .collect();
    let fence = Fence::new(&home.config.scan.local_only, &isolated_dirs);
    let labeler = Labeler::new(home, &isolated, &fence);
    let mut fp: Vec<String> = vec![
        format!("reads={:?}", opts.reads),
        format!("flat={}", opts.flat_subagents),
        format!("workspace={}", home.workspace.display()),
    ];
    fp.extend(home.config.scan.local_only.iter().map(|g| format!("local_only={g}")));
    fp.extend(isolated.iter().map(|g| format!("isolated={g}")));
    fp.extend(
        registry
            .projects
            .values()
            .map(|p| format!("project={}={}", p.name, p.dir)),
    );
    let fingerprint = crate::store::sha256_hex(&[fp.join("\n").as_bytes()]);
    let path = cache_path(home);
    let cache = (opts.cache && !opts.flat_subagents).then_some((path.as_path(), fingerprint.as_str()));
    Corpus::load(cache, opts, &fence, &labeler)
}

/// The meter's run: load, cut, and the red lines. Store reads: levels, session rows, gate receipts, asks.
pub fn take(home: &Home, store: &Store, opts: &Options) -> Result<CostLine> {
    let corpus = load(home, store, opts)?;
    let registry = Registry::load(home)?;
    let now = epoch_ms(&crate::pulse::now_iso()).unwrap_or(0);
    let day = 86_400_000;
    let window = Span(now - WINDOW_DAYS * day, i64::MAX);
    let previous = Span(now - 2 * WINDOW_DAYS * day, now - WINDOW_DAYS * day);
    let since = corpus.first_turn_ms().unwrap_or(now);
    let mut work: BTreeMap<String, Work> = BTreeMap::new();
    for (name, project) in &registry.projects {
        let dir = project.path(&home.workspace);
        let commits =
            crate::pulse::git_commit_times_since(&dir, &iso(since)).map(|v| v.into_iter().map(|s| s * 1000).collect());
        let gates = store
            .gate_receipt_times_since(name)
            .unwrap_or_default()
            .iter()
            .filter_map(|t| epoch_ms(t))
            .collect();
        work.insert(name.clone(), Work { commits, gates });
    }
    let r = opts.reads;
    let totals = Totals {
        window: kpis(&corpus, None, window, r, &work),
        previous_window: kpis(&corpus, None, previous, r, &work),
        all_time: kpis(&corpus, None, Span::ALL, r, &work),
    };
    // First seen: the hooks' session rows, and the first Groundworks call in each label.
    let mut first_seen: BTreeMap<String, i64> = BTreeMap::new();
    for row in store.sessions_since(None).unwrap_or_default() {
        if let (Some(p), Some(t)) = (row["project"].as_str(), row["started_at"].as_str().and_then(epoch_ms)) {
            let e = first_seen.entry(p.to_owned()).or_insert(t);
            *e = (*e).min(t);
        }
    }
    let mut labels: BTreeSet<String> = BTreeSet::new();
    for (i, f) in corpus.scans.iter().enumerate() {
        labels.extend(f.labels.iter().cloned());
        for g in &f.gw {
            let l = corpus.label(i, g.1).to_owned();
            let e = first_seen.entry(l).or_insert(g.0);
            *e = (*e).min(g.0);
        }
    }
    let mut per_project = BTreeMap::new();
    for label in &labels {
        let all_time = kpis(&corpus, Some(label), Span::ALL, r, &work);
        if all_time.turns == 0 {
            continue;
        }
        let seen = first_seen.get(label).copied();
        per_project.insert(
            label.clone(),
            ProjectCost {
                window: kpis(&corpus, Some(label), window, r, &work),
                first_seen: seen.map(iso),
                before: seen.map(|t| kpis(&corpus, Some(label), Span(i64::MIN, t), r, &work)),
                after: seen.map(|t| kpis(&corpus, Some(label), Span(t, i64::MAX), r, &work)),
                all_time,
            },
        );
    }
    let asks = ask_use(store, &totals.window, r);
    let mut line = CostLine {
        at: crate::pulse::now_iso(),
        machine: crate::home::machine(),
        window_days: WINDOW_DAYS,
        reads: r,
        bytes_per_token: BYTES_PER_TOKEN,
        scan: corpus.stats.clone(),
        transcripts: transcript_counts(&corpus),
        totals,
        per_project,
        asks,
        red: Vec::new(),
    };
    line.red = red(&line);
    Ok(line)
}

fn ask_use(store: &Store, window: &Kpis, r: Reads) -> AskUse {
    let mut a = AskUse::default();
    let now = epoch_ms(&crate::pulse::now_iso()).unwrap_or(0);
    let week = WINDOW_DAYS * 86_400_000;
    for row in store.ask_statuses_since(2 * WINDOW_DAYS as u64).unwrap_or_default() {
        if row["by"].as_str().is_some_and(|b| b.starts_with("eval:")) {
            continue;
        }
        let Some(at) = row["at"].as_str().and_then(epoch_ms) else {
            continue;
        };
        let passed = row["status"] == "PASS";
        if at >= now - week {
            a.asks += 1;
            a.passed += u64::from(passed);
        } else {
            a.previous_asks += 1;
            a.previous_passed += u64::from(passed);
        }
    }
    if r.overhead && a.passed > 0 {
        a.gw_tokens_per_passed = window
            .ask_result_bytes
            .map(|b| b as f64 / BYTES_PER_TOKEN / a.passed as f64);
    }
    a
}

fn share(a: u64, b: u64) -> f64 {
    if b == 0 { 0.0 } else { a as f64 / b as f64 }
}

/// The red lines of A1.4 that the meter can judge (the Stop hook's p95 is the pulse's).
pub fn red(line: &CostLine) -> Vec<String> {
    let mut red = Vec::new();
    let w = &line.totals.window;
    let p = &line.totals.previous_window;
    if let Some(s) = w.overhead_share
        && s > OVERHEAD_RED
    {
        red.push(format!(
            "RED overhead {:.1}% of input tokens in {}d (Groundworks content carried in context, estimate at {} bytes/token; red above {:.0}%)",
            100.0 * s,
            line.window_days,
            BYTES_PER_TOKEN,
            100.0 * OVERHEAD_RED
        ));
    }
    if p.tokens_per_human_turn > 0.0 && w.tokens_per_human_turn > p.tokens_per_human_turn * (1.0 + TURN_RISE_RED) {
        let shift = model_shift(&w.models, &p.models);
        if shift <= MODEL_MIX_SHIFT {
            red.push(format!(
                "RED tokens per human turn up {:.0}% week on week ({:.0} -> {:.0}) with no model-mix change (largest share move {:.0} points)",
                100.0 * (w.tokens_per_human_turn / p.tokens_per_human_turn - 1.0),
                p.tokens_per_human_turn,
                w.tokens_per_human_turn,
                100.0 * shift
            ));
        }
    }
    let a = &line.asks;
    if a.asks > 0 && a.previous_asks > 0 && share(a.passed, a.asks) < share(a.previous_passed, a.previous_asks) {
        red.push(format!(
            "RED asks passed falling: {}/{} this week vs {}/{} the week before",
            a.passed, a.asks, a.previous_passed, a.previous_asks
        ));
    }
    red
}

/// The largest move in any model's share of turns between two spans.
pub fn model_shift(a: &BTreeMap<String, u64>, b: &BTreeMap<String, u64>) -> f64 {
    let ta: u64 = a.values().sum();
    let tb: u64 = b.values().sum();
    a.keys()
        .chain(b.keys())
        .map(|m| (share(*a.get(m).unwrap_or(&0), ta) - share(*b.get(m).unwrap_or(&0), tb)).abs())
        .fold(0.0, f64::max)
}

/// Append the line to `metrics/cost.jsonl`; returns the path.
pub fn record(home: &Home, line: &CostLine) -> Result<PathBuf> {
    let dir = home.root.join("metrics");
    std::fs::create_dir_all(&dir)?;
    let path = dir.join("cost.jsonl");
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&path)?;
    writeln!(f, "{}", serde_json::to_string(line)?)?;
    Ok(path)
}

/// The newest recorded line, for the pulse (never a transcript).
pub fn latest(home: &Home) -> Option<CostLine> {
    let text = std::fs::read_to_string(home.root.join("metrics").join("cost.jsonl")).ok()?;
    text.lines()
        .rev()
        .find(|l| !l.trim().is_empty())
        .and_then(|l| serde_json::from_str(l).ok())
}

/// `1234567` -> `1.2M` (tokenmeter `fmt`).
pub fn fmt_n(n: f64) -> String {
    if n >= 1e9 {
        format!("{:.2}B", n / 1e9)
    } else if n >= 1e6 {
        format!("{:.1}M", n / 1e6)
    } else if n >= 1e3 {
        format!("{:.0}k", n / 1e3)
    } else if n.fract() == 0.0 {
        format!("{n:.0}")
    } else {
        format!("{n:.1}")
    }
}

fn opt_pct(x: Option<f64>) -> String {
    x.map_or_else(|| "off".to_owned(), |v| format!("{:.2}%", 100.0 * v))
}

fn opt_n<T: Into<f64> + Copy>(x: Option<T>) -> String {
    x.map_or_else(|| "off".to_owned(), |v| fmt_n(v.into()))
}

fn opt_u(x: Option<u64>) -> String {
    x.map_or_else(|| "n/a".to_owned(), |v| v.to_string())
}

/// The one screen (A1.4), plus the per-project table.
pub fn render(line: &CostLine) -> String {
    let w = &line.totals.window;
    let p = &line.totals.previous_window;
    let a = &line.totals.all_time;
    let s = &line.scan;
    let mut out = format!(
        "cost {} on {} | {} transcripts, {} bytes, {} parsed ({} resumed, {} from cache), {} lines, {} ms on {} threads\n",
        &line.at[..line.at.len().min(19)],
        line.machine,
        s.files,
        fmt_n(s.bytes_total as f64),
        s.files_parsed,
        s.files_resumed,
        s.files_cached,
        s.lines_parsed,
        s.wall_ms,
        s.threads
    );
    let _ = writeln!(
        out,
        "reads: overhead {} | rereads {} (rereads off by default until its gw-security changes land); token estimates use {} bytes/token",
        if line.reads.overhead { "ON" } else { "off" },
        if line.reads.rereads { "ON" } else { "off" },
        line.bytes_per_token
    );
    let t = &line.transcripts;
    let _ = writeln!(
        out,
        "sessions = Claude Code session ids (a main transcript and its subagent transcripts) with a priced turn: {} all time, from {} main transcripts ({} with a turn) and {} subagent transcripts ({} with a turn); {} session(s) have turns only in subagents",
        t.sessions, t.main, t.main_with_turns, t.subagent, t.subagent_with_turns, t.sessions_subagent_only
    );
    for (label, k) in [
        (format!("{}d", line.window_days), w),
        ("prev".to_owned(), p),
        ("all".to_owned(), a),
    ] {
        let _ = writeln!(
            out,
            "{label:>4}  sessions {} | human turns {} | tokens {} (in {}, cache read {}, cache write {}, out {}) | per human turn {} | per session (with its subagents) {} | main ctx p90 {} | ${:.0} ({} unpriced turns)",
            k.sessions,
            k.human_turns,
            fmt_n(k.tokens as f64),
            fmt_n(k.input as f64),
            fmt_n(k.cache_read as f64),
            fmt_n(k.cache_write as f64),
            fmt_n(k.output as f64),
            fmt_n(k.tokens_per_human_turn),
            fmt_n(k.tokens_per_session),
            fmt_n(k.main_ctx_p90 as f64),
            k.usd,
            k.unpriced_turns
        );
    }
    let _ = writeln!(
        out,
        "groundworks ({}d)  calls {} (asks {}) | overhead share {} (est.) | brief bytes {} | tool result bytes {} | ask latency p50 {} ms p95 {} ms | packet bytes p50 {} p95 {}",
        line.window_days,
        w.gw_calls,
        w.asks,
        opt_pct(w.overhead_share),
        opt_n(w.brief_bytes.map(|v| v as f64)),
        opt_n(w.gw_result_bytes.map(|v| v as f64)),
        opt_u(w.ask_latency_p50_ms.map(|v| v as u64)),
        opt_u(w.ask_latency_p95_ms.map(|v| v as u64)),
        opt_n(w.packet_bytes_p50.map(|v| v as f64)),
        opt_n(w.packet_bytes_p95.map(|v| v as f64)),
    );
    let q = &line.asks;
    let _ = writeln!(
        out,
        "asks (store, {}d)  passed {}/{} (week before {}/{}) | gw tokens per passed answer {}",
        line.window_days,
        q.passed,
        q.asks,
        q.previous_passed,
        q.previous_asks,
        opt_n(q.gw_tokens_per_passed)
    );
    let _ = writeln!(
        out,
        "re-reads ({}d)  per session median {} | repeat reads {} ({} bytes) | Read/Grep bytes before first ask {} after {}",
        line.window_days,
        w.rereads_per_session_median
            .map_or("off".to_owned(), |v| format!("{v:.1}")),
        opt_u(w.reread_calls),
        opt_n(w.reread_bytes.map(|v| v as f64)),
        opt_n(w.search_bytes_before_ask.map(|v| v as f64)),
        opt_n(w.search_bytes_after_ask.map(|v| v as f64)),
    );
    let _ = writeln!(
        out,
        "work ({}d)  commits {} + gate receipts {} = {} per million tokens",
        line.window_days,
        opt_u(w.commits),
        opt_u(w.gate_receipts),
        w.work_per_mtok.map_or("n/a".to_owned(), |v| format!("{v:.3}"))
    );
    let _ = writeln!(
        out,
        "\n  {:<22} {:>8} {:>9} {:>9} {:>8} {:>8} {:>9} | first seen  | before: sess tok/sess tok/turn | after: sess tok/sess tok/turn",
        "project (all time)", "sessions", "tokens", "tok/sess", "tok/turn", "gw calls", "work/Mtok"
    );
    let mut rows: Vec<(&String, &ProjectCost)> = line.per_project.iter().collect();
    rows.sort_by(|x, y| y.1.all_time.tokens.cmp(&x.1.all_time.tokens));
    for (name, pc) in rows {
        let k = &pc.all_time;
        let ba = |x: &Option<Kpis>| {
            x.as_ref().map_or("-".to_owned(), |k| {
                format!(
                    "{:>4} {:>7} {:>7}",
                    k.sessions,
                    fmt_n(k.tokens_per_session),
                    fmt_n(k.tokens_per_human_turn)
                )
            })
        };
        let _ = writeln!(
            out,
            "  {:<22} {:>8} {:>9} {:>9} {:>8} {:>8} {:>9} | {:<11} | {} | {}",
            name,
            k.sessions,
            fmt_n(k.tokens as f64),
            fmt_n(k.tokens_per_session),
            fmt_n(k.tokens_per_human_turn),
            k.gw_calls,
            k.work_per_mtok.map_or("n/a".to_owned(), |v| format!("{v:.3}")),
            pc.first_seen.as_deref().map_or("-", |s| &s[..s.len().min(10)]),
            ba(&pc.before),
            ba(&pc.after)
        );
    }
    out.push_str(
        "  before/after is a trend, not proof: model mix, task mix and the work itself changed over the same weeks; the paired eval (docs/EVAL-HELPS-OR-HURTS.md) is the proof.\n",
    );
    if line.red.is_empty() {
        out.push_str("digest: nothing red in cost\n");
    } else {
        for r in &line.red {
            let _ = writeln!(out, "digest: {r}");
        }
    }
    out
}

// ---- the tokenmeter port: snapshot, compare, weekly, preload, folders ---------------------------------

const BUCKETS: [(f64, &str); 5] = [
    (50e3, "<50k"),
    (150e3, "50-150k"),
    (300e3, "150-300k"),
    (600e3, "300-600k"),
    (f64::INFINITY, ">=600k"),
];

fn bucket(ctx: f64) -> &'static str {
    BUCKETS.iter().find(|(limit, _)| ctx < *limit).map_or(">=600k", |b| b.1)
}

/// Python's `round(x)` on a float: half to even.
fn py_round(x: f64) -> i64 {
    x.round_ties_even() as i64
}

struct Row {
    file: usize,
    main: bool,
    model: String,
    label: String,
    input: u64,
    cache_read: u64,
    cache_write: u64,
    output: u64,
    ctx: u64,
}

fn agg(rows: &[&Row]) -> Value {
    let mut ctxs: Vec<u64> = rows.iter().map(|r| r.ctx).collect();
    ctxs.sort_unstable();
    let total_in: u64 = ctxs.iter().sum();
    let mut b: BTreeMap<&str, u64> = BTreeMap::new();
    for r in rows {
        *b.entry(bucket(r.ctx as f64)).or_default() += r.ctx;
    }
    let n = rows.len();
    let shares: serde_json::Map<String, Value> = BUCKETS
        .iter()
        .map(|(_, name)| {
            let v = if total_in == 0 {
                0.0
            } else {
                (*b.get(name).unwrap_or(&0) as f64 / total_in as f64 * 1000.0).round_ties_even() / 1000.0
            };
            ((*name).to_owned(), json!(v))
        })
        .collect();
    json!({
        "turns": n,
        "input": rows.iter().map(|r| r.input).sum::<u64>(),
        "cache_read": rows.iter().map(|r| r.cache_read).sum::<u64>(),
        "cache_write": rows.iter().map(|r| r.cache_write).sum::<u64>(),
        "output": rows.iter().map(|r| r.output).sum::<u64>(),
        "ctx_mean": if n == 0 { 0 } else { py_round(total_in as f64 / n as f64) },
        "ctx_p50": ctxs.get(n / 2).copied().unwrap_or(0),
        "ctx_p90": if n == 0 { 0 } else { ctxs[(n as f64 * 0.9) as usize] },
        "input_share_by_ctx": shares,
    })
}

/// tokenmeter `summarize`, over [since, until) and, given, one label. `by_project` is keyed by label.
pub fn summarize(c: &Corpus, since: i64, until: i64, project: Option<&str>) -> Value {
    let span = Span(since, until);
    let rows: Vec<Row> = c
        .turns()
        .filter(|(i, t)| span.has(t.1) && project.is_none_or(|p| c.label(*i, t.3) == p))
        .map(|(i, t)| Row {
            file: i,
            main: c.sources[i].main,
            model: c.model(i, t.2).to_owned(),
            label: c.label(i, t.3).to_owned(),
            input: t.4,
            cache_read: t.5,
            cache_write: t.6,
            output: t.7,
            ctx: t.ctx(),
        })
        .collect();
    let all: Vec<&Row> = rows.iter().collect();
    let by = |key: &dyn Fn(&Row) -> String| -> serde_json::Map<String, Value> {
        let mut groups: BTreeMap<String, Vec<&Row>> = BTreeMap::new();
        for r in &rows {
            groups.entry(key(r)).or_default().push(r);
        }
        groups.into_iter().map(|(k, v)| (k, agg(&v))).collect()
    };
    let total = agg(&all);
    let days = ((until.min(i64::MAX / 2) - since) as f64 / 86_400_000.0).max(1e-9);
    let prompts: u64 = c
        .scans
        .iter()
        .enumerate()
        .map(|(i, f)| {
            f.prompts
                .iter()
                .filter(|(ts, l)| span.has(*ts) && project.is_none_or(|p| c.label(i, *l) == p))
                .count() as u64
        })
        .sum();
    let keys = ["turns", "input", "cache_read", "cache_write", "output"];
    let per_day: serde_json::Map<String, Value> = keys
        .iter()
        .map(|k| {
            (
                (*k).to_owned(),
                json!(py_round(total[*k].as_f64().unwrap_or(0.0) / days)),
            )
        })
        .collect();
    let per_prompt: serde_json::Map<String, Value> = keys
        .iter()
        .map(|k| {
            let v = if prompts == 0 {
                Value::Null
            } else {
                json!(py_round(total[*k].as_f64().unwrap_or(0.0) / prompts as f64))
            };
            ((*k).to_owned(), v)
        })
        .collect();
    let mains: Vec<&Row> = rows.iter().filter(|r| r.main).collect();
    let subs: Vec<&Row> = rows.iter().filter(|r| !r.main).collect();
    // Sessions as tokenmeter keys them: one per file with a turn in the window.
    let mut files: BTreeMap<usize, (u64, u64)> = BTreeMap::new();
    for r in &rows {
        let e = files.entry(r.file).or_default();
        e.0 += 1;
        e.1 = e.1.max(r.ctx);
    }
    let main_files: Vec<(u64, u64)> = files
        .iter()
        .filter(|(f, _)| c.sources[**f].main)
        .map(|(_, v)| *v)
        .collect();
    let med = |mut v: Vec<f64>| {
        v.sort_by(f64::total_cmp);
        median(&v).unwrap_or(0.0)
    };
    let by_project: serde_json::Map<String, Value> = by(&|r: &Row| r.label.clone())
        .into_iter()
        .map(|(k, v)| {
            (
                k,
                json!({"turns": v["turns"], "cache_read": v["cache_read"], "output": v["output"], "ctx_mean": v["ctx_mean"]}),
            )
        })
        .collect();
    json!({
        "window": {"since": iso(since), "until": iso(until.min(i64::MAX / 2)), "days": (days * 100.0).round_ties_even() / 100.0},
        "totals": total,
        "per_day": per_day,
        "user_prompts": prompts,
        "per_user_prompt": per_prompt,
        "main": agg(&mains),
        "subagent": agg(&subs),
        "by_model": by(&|r: &Row| r.model.clone()),
        "by_kind_model": by(&|r: &Row| format!("{}:{}", if r.main { "main" } else { "subagent" }, r.model)),
        "by_project": by_project,
        "sessions": {
            "main_count": main_files.len(),
            "subagent_count": files.len() - main_files.len(),
            "main_max_ctx_p50": med(main_files.iter().map(|v| v.1 as f64).collect()),
            "main_turns_p50": med(main_files.iter().map(|v| v.0 as f64).collect()),
        },
    })
}

/// tokenmeter `first_turns` + `summarize_preload`: each file's first priced turn, when it is in the span.
pub fn preload(c: &Corpus, since: i64, until: i64, project: Option<&str>) -> Value {
    let span = Span(since, until);
    let mut rows: Vec<(&'static str, String, u64)> = Vec::new();
    for (i, f) in c.scans.iter().enumerate() {
        let Some(t) = f.turns.first() else { continue };
        if !span.has(t.1) || project.is_some_and(|p| c.label(i, t.3) != p) {
            continue;
        }
        let kind = if c.sources[i].main { "main" } else { "subagent" };
        rows.push((kind, c.model(i, t.2).to_owned(), t.ctx()));
    }
    let summary = |rs: Vec<u64>| {
        let mut v = rs;
        v.sort_unstable();
        let n = v.len();
        json!({"n": n, "median": v.get(n / 2).copied().unwrap_or(0), "p90": if n == 0 { 0 } else { v[(n as f64 * 0.9) as usize] }})
    };
    let mut by_kind: BTreeMap<&str, Vec<u64>> = BTreeMap::new();
    let mut by_model: BTreeMap<String, Vec<u64>> = BTreeMap::new();
    for (k, m, ctx) in &rows {
        by_kind.entry(k).or_default().push(*ctx);
        by_model.entry(m.clone()).or_default().push(*ctx);
    }
    json!({
        "n": rows.len(),
        "by_kind": by_kind.into_iter().map(|(k, v)| (k.to_owned(), summary(v))).collect::<serde_json::Map<_, _>>(),
        "by_model": by_model.into_iter().map(|(k, v)| (k, summary(v))).collect::<serde_json::Map<_, _>>(),
        "window": {"since": iso(since), "until": iso(until)},
    })
}

/// tokenmeter `print_report`.
pub fn print_report(s: &Value) -> String {
    let n = |v: &Value| fmt_n(v.as_f64().unwrap_or(0.0));
    let w = &s["window"];
    let mut out = format!(
        "{}  {} -> {}  ({} days)\n",
        s["label"].as_str().unwrap_or(""),
        &w["since"].as_str().unwrap_or("")[..10.min(w["since"].as_str().unwrap_or("").len())],
        &w["until"].as_str().unwrap_or("")[..10.min(w["until"].as_str().unwrap_or("").len())],
        w["days"]
    );
    for name in ["main", "subagent", "totals"] {
        let r = &s[name];
        let _ = writeln!(
            out,
            "  {name:9} turns {:>7}  cacheR {:>7}  cacheW {:>7}  out {:>6}  ctx mean {} p90 {}",
            r["turns"],
            n(&r["cache_read"]),
            n(&r["cache_write"]),
            n(&r["output"]),
            n(&r["ctx_mean"]),
            n(&r["ctx_p90"])
        );
    }
    let _ = writeln!(
        out,
        "  main input share by context: {}",
        s["main"]["input_share_by_ctx"]
    );
    let per: Vec<String> = s["per_user_prompt"]
        .as_object()
        .into_iter()
        .flatten()
        .filter(|(_, v)| !v.is_null())
        .map(|(k, v)| format!("{k}: {}", n(v)))
        .collect();
    let _ = writeln!(out, "  per user prompt: {{{}}}", per.join(", "));
    out.push_str("  by kind:model\n");
    for (k, r) in s["by_kind_model"].as_object().into_iter().flatten() {
        let _ = writeln!(
            out,
            "    {k:32} turns {:>7}  cacheR {:>7}  out {:>6}  ctx {}",
            r["turns"],
            n(&r["cache_read"]),
            n(&r["output"]),
            n(&r["ctx_mean"])
        );
    }
    out
}

/// tokenmeter `compare`: two snapshots (this meter's or tokenmeter's own; the fields read are the same).
pub fn compare(a: &Value, b: &Value) -> String {
    let mut out = format!(
        "A = {} ({} d)   B = {} ({} d)\n",
        a["label"].as_str().unwrap_or(""),
        a["window"]["days"],
        b["label"].as_str().unwrap_or(""),
        b["window"]["days"]
    );
    let mut rows: Vec<(&str, &str)> = ["turns", "cache_read", "cache_write", "output"]
        .iter()
        .map(|k| ("per_user_prompt", *k))
        .collect();
    rows.extend(
        ["turns", "cache_read", "cache_write", "output"]
            .iter()
            .map(|k| ("per_day", *k)),
    );
    rows.extend([("main", "ctx_mean"), ("main", "ctx_p90"), ("subagent", "ctx_mean")]);
    for (sec, k) in rows {
        let x = a[sec][k].as_f64().unwrap_or(0.0);
        let y = b[sec][k].as_f64().unwrap_or(0.0);
        let pct = if x != 0.0 {
            format!("{:+.0}%", 100.0 * (y - x) / x)
        } else {
            "n/a".to_owned()
        };
        let _ = writeln!(
            out,
            "  {:28} {:>8} -> {:>8}  {pct}",
            format!("{sec}.{k}"),
            fmt_n(x),
            fmt_n(y)
        );
    }
    out.push_str("  model mix (share of turns):\n");
    for (s, tag) in [(a, "A"), (b, "B")] {
        let t = s["totals"]["turns"].as_f64().filter(|t| *t > 0.0).unwrap_or(1.0);
        let mix: Vec<String> = s["by_model"]
            .as_object()
            .into_iter()
            .flatten()
            .map(|(m, r)| format!("'{m}': '{:.0}%'", 100.0 * r["turns"].as_f64().unwrap_or(0.0) / t))
            .collect();
        let _ = writeln!(out, "    {tag} {{{}}}", mix.join(", "));
    }
    out
}

pub fn snapshots_dir(home: &Home) -> PathBuf {
    home.root.join("metrics").join("cost-snapshots")
}

/// Save a snapshot as tokenmeter does (`<local date>_<label>.json`); returns the path.
pub fn save_snapshot(home: &Home, snap: &Value, label: &str) -> Result<PathBuf> {
    let label = crate::hook::plain_name(label);
    let dir = snapshots_dir(home);
    std::fs::create_dir_all(&dir)?;
    let path = dir.join(format!("{}_{label}.json", chrono::Local::now().format("%Y-%m-%d_%H%M")));
    std::fs::write(&path, serde_json::to_string_pretty(snap)?)?;
    Ok(path)
}

/// tokenmeter `weekly`: the last 7 days against the baseline snapshot (the first `*baseline*.json` in
/// the snapshots folder, or `baseline`); writes `metrics/cost-reports/latest.txt` and a dated copy.
pub fn weekly(home: &Home, c: &Corpus, baseline: Option<&Path>) -> Result<String> {
    let now = epoch_ms(&crate::pulse::now_iso()).unwrap_or(0);
    let since = now - WINDOW_DAYS * 86_400_000;
    let mut snap = summarize(c, since, now, None);
    snap["preload"] = preload(c, since, now, None);
    snap["label"] = json!("weekly");
    snap["project_filter"] = Value::Null;
    snap["taken"] = json!(crate::pulse::now_iso());
    let saved = save_snapshot(home, &snap, "weekly")?;
    let base = match baseline {
        Some(p) => p.to_path_buf(),
        None => {
            let mut found: Vec<PathBuf> = std::fs::read_dir(snapshots_dir(home))?
                .filter_map(Result::ok)
                .map(|e| e.path())
                .filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy().contains("baseline")) && is_json(p))
                .collect();
            found.sort();
            match found.into_iter().next() {
                Some(p) => p,
                None => bail!(
                    "no baseline snapshot in {} (take one with `cost snapshot --label baseline`, or pass --baseline)",
                    snapshots_dir(home).display()
                ),
            }
        }
    };
    let a: Value = serde_json::from_str(&std::fs::read_to_string(&base)?)?;
    let text = format!(
        "weekly report {}\n{}",
        chrono::Local::now().format("%Y-%m-%d"),
        compare(&a, &snap)
    );
    let dir = home.root.join("metrics").join("cost-reports");
    std::fs::create_dir_all(&dir)?;
    for name in [
        "latest.txt".to_owned(),
        format!("{}.txt", chrono::Local::now().format("%Y-%m-%d")),
    ] {
        std::fs::write(dir.join(name), &text)?;
    }
    Ok(format!("{text}\nsaved {}", saved.display()))
}

fn is_json(p: &Path) -> bool {
    p.extension().is_some_and(|e| e == "json")
}

/// A fresh salt for one differential run (16 random bytes, hex).
pub fn random_salt() -> Result<String> {
    let mut b = [0u8; 16];
    getrandom::fill(&mut b).map_err(|e| anyhow::anyhow!("no OS randomness: {e}"))?;
    Ok(crate::store::hex(&b))
}

/// The differential's half: per transcript folder, its totals over [since, until), keyed by the first 12
/// hex of sha256(salt, folder name) with a salt chosen per run, so no folder name is printed or written
/// and a key from one run confirms nothing in another (gw-security 2026-10-30).
pub fn folder_totals(c: &Corpus, since: i64, until: i64, salt: &str) -> BTreeMap<String, [u64; 5]> {
    let span = Span(since, until);
    let mut out: BTreeMap<String, [u64; 5]> = BTreeMap::new();
    for (i, t) in c.turns() {
        if !span.has(t.1) {
            continue;
        }
        let key = crate::store::sha256_hex(&[salt.as_bytes(), b"\0", c.folders[c.sources[i].folder].as_bytes()])[..12]
            .to_owned();
        let e = out.entry(key).or_default();
        e[0] += 1;
        e[1] += t.4;
        e[2] += t.5;
        e[3] += t.6;
        e[4] += t.7;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const UUID: &str = "7c9e6679-7425-40de-944b-e07fc1f90ae7";

    #[test]
    fn the_fence_matches_folder_names_with_dashes_as_spaces_and_isolated_dirs() {
        // Generic stand-ins for a fenced matter folder (CLAUDE.md rule 1: no matter names in the repo):
        // the workspace folder's own transcript folder, and a scratchpad of a session started there.
        let fence = Fence::new(
            &["*Sample matter*".to_owned()],
            &[PathBuf::from("C:/Users/x/projects/secret")],
        );
        let workspace_slug = "c--Users-x-projects-ACME-Sample-matter";
        let scratch_slug =
            format!("C--Users-x-AppData-Local-Temp-claude-c--Users-x-projects-ACME-Sample-matter-{UUID}-scratchpad");
        assert!(fence.slug_fenced(workspace_slug));
        assert!(fence.slug_fenced(&scratch_slug), "the scratchpad slug is fenced too");
        assert!(fence.slug_fenced("C--Users-x-projects-secret"));
        assert!(fence.slug_fenced("c--users-x-projects-secret-sub"));
        // Red arm: near misses stay open.
        assert!(!fence.slug_fenced("c--Users-x-projects-secretive"));
        assert!(!fence.slug_fenced("c--Users-x-projects-Sample"));
        assert!(!fence.slug_fenced("c--Users-x-projects-supergun"));
        // A glob without stars still matches as a substring.
        let bare = Fence::new(&["Sample matter".to_owned()], &[]);
        assert!(bare.slug_fenced(workspace_slug));
        assert_eq!(slug_form("C:\\Users\\x\\a b.c"), "c--users-x-a-b-c");
    }

    fn fixture(tag: &str) -> (PathBuf, Home) {
        let base = std::env::temp_dir().join(format!("gw-meter-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join(".groundworks");
        let ws = base.join("ws");
        std::fs::create_dir_all(root.join("registry/projects")).unwrap();
        std::fs::create_dir_all(ws.join("demo")).unwrap();
        std::fs::create_dir_all(ws.join("secret")).unwrap();
        std::fs::create_dir_all(ws.join("ACME Sample matter")).unwrap();
        let toml_path = |p: &Path| p.display().to_string().replace('\\', "/");
        std::fs::write(
            root.join("groundworks.toml"),
            format!(
                "workspace = \"{}\"\n\n[scan]\nlocal_only = [\"*Sample matter*\"]\n",
                toml_path(&ws)
            ),
        )
        .unwrap();
        for p in ["demo", "secret"] {
            std::fs::write(
                root.join(format!("registry/projects/{p}.toml")),
                format!("name = \"{p}\"\ndir = \"{p}\"\nsummary = \"s\"\n"),
            )
            .unwrap();
        }
        (base.clone(), Home::open(&root).unwrap())
    }

    fn jline(v: Value) -> String {
        format!("{v}\n")
    }

    fn assistant(cwd: &Path, ts: &str, id: &str, req: &str, model: &str, usage: [u64; 4], blocks: Value) -> String {
        jline(
            json!({"type": "assistant", "timestamp": ts, "cwd": cwd.display().to_string(), "requestId": req,
            "message": {"id": id, "model": model, "role": "assistant", "content": blocks,
                "usage": {"input_tokens": usage[0], "cache_read_input_tokens": usage[1],
                          "cache_creation_input_tokens": usage[2], "output_tokens": usage[3], "service_tier": "standard"}}}),
        )
    }

    fn user_text(cwd: &Path, ts: &str, text: &str) -> String {
        jline(
            json!({"type": "user", "timestamp": ts, "cwd": cwd.display().to_string(),
            "message": {"role": "user", "content": text}}),
        )
    }

    fn tool_result(cwd: &Path, ts: &str, id: &str, content: Value) -> String {
        jline(
            json!({"type": "user", "timestamp": ts, "cwd": cwd.display().to_string(),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": id, "content": content}]}}),
        )
    }

    const CANARY: &str = "CANARY-5e1f9a";

    /// A transcripts root: demo's folder (a main session with a prompt, two lines of one response, a
    /// Groundworks ask and its result, a Read read three times, a nested workflow subagent), the
    /// fenced folder (by name), the isolated project's folder, and a root-folder session that wanders
    /// into the fenced folder.
    fn transcripts(base: &Path) -> PathBuf {
        let ws = base.join("ws");
        let demo = ws.join("demo");
        let root = base.join("claude/projects");
        let demo_slug = slug_form(&demo.display().to_string());
        let d = root.join(&demo_slug);
        std::fs::create_dir_all(d.join(format!("{UUID}/subagents/workflows/wf_1"))).unwrap();
        let read = |id: &str| json!([{"type": "tool_use", "id": id, "name": "Read", "input": {"file_path": format!("{}/notes.txt", demo.display())}}]);
        let mut main = String::new();
        main += &user_text(&demo, "2026-10-20T10:00:00.000Z", &format!("question {CANARY}"));
        main += &assistant(
            &demo,
            "2026-10-20T10:00:01.000Z",
            "msg1",
            "req1",
            "claude-opus-5-5",
            [10, 100, 20, 5],
            json!([{"type": "text", "text": format!("answer {CANARY}")}]),
        );
        // The same response's second content block: same usage, counted once.
        main += &assistant(
            &demo,
            "2026-10-20T10:00:01.500Z",
            "msg1",
            "req1",
            "claude-opus-5-5",
            [10, 100, 20, 5],
            json!([{"type": "tool_use", "id": "toolu_ask", "name": "mcp__plugin_groundworks_groundworks__ask", "input": {"question": CANARY}}]),
        );
        main += &tool_result(
            &demo,
            "2026-10-20T10:00:03.250Z",
            "toolu_ask",
            json!([{"type": "text", "text": "x".repeat(298)}]),
        );
        for (n, id) in ["r1", "r2", "r3"].iter().enumerate() {
            main += &assistant(
                &demo,
                &format!("2026-10-20T10:01:0{n}.000Z"),
                &format!("msg-r{n}"),
                &format!("req-r{n}"),
                "claude-opus-5-5",
                [1, 1, 1, 1],
                read(id),
            );
            main += &tool_result(
                &demo,
                &format!("2026-10-20T10:01:0{n}.500Z"),
                id,
                json!("y".repeat(100)),
            );
        }
        main += "{\"type\":\"api-request-blob\",\"message\":{\"usage\":{\"input_tokens\":999}}}\n";
        main += "not json at all \"usage\"\n";
        // A trailing partial line waits.
        main += "{\"type\":\"assistant\",\"timestamp\":\"2026-10-20T11:00:00Z\"";
        std::fs::write(d.join(format!("{UUID}.jsonl")), main).unwrap();
        std::fs::write(
            d.join(format!("{UUID}/subagents/workflows/wf_1/agent-a1.jsonl")),
            assistant(
                &demo,
                "2026-10-20T10:02:00.000Z",
                "msg-sub",
                "req-sub",
                "claude-sonnet-5-5",
                [3, 0, 0, 2],
                json!([]),
            ),
        )
        .unwrap();
        // The fenced folder by name, and the isolated project's folder: never opened.
        for p in [ws.join("ACME Sample matter"), ws.join("secret")] {
            let dir = root.join(slug_form(&p.display().to_string()));
            std::fs::create_dir_all(&dir).unwrap();
            std::fs::write(
                dir.join("s.jsonl"),
                assistant(
                    &p,
                    "2026-10-20T10:00:00.000Z",
                    "fenced",
                    "fenced",
                    "claude-opus-5-5",
                    [7777, 0, 0, 0],
                    json!([]),
                ),
            )
            .unwrap();
        }
        // The workspace root: one clean session, and one that moves into the fenced folder (dropped whole).
        let r = root.join(slug_form(&ws.display().to_string()));
        std::fs::create_dir_all(&r).unwrap();
        std::fs::write(
            r.join("clean.jsonl"),
            assistant(
                &ws,
                "2026-10-20T09:00:00.000Z",
                "root1",
                "root1",
                "claude-haiku-4-5-20251001",
                [2, 0, 0, 1],
                json!([]),
            ),
        )
        .unwrap();
        let wander = assistant(
            &ws,
            "2026-10-20T09:00:00.000Z",
            "w1",
            "w1",
            "claude-opus-5-5",
            [5000, 0, 0, 0],
            json!([]),
        ) + &assistant(
            &ws.join("ACME Sample matter"),
            "2026-10-20T09:01:00.000Z",
            "w2",
            "w2",
            "claude-opus-5-5",
            [6000, 0, 0, 0],
            json!([]),
        );
        std::fs::write(r.join("wander.jsonl"), wander).unwrap();
        root
    }

    fn corpus(home: &Home, root: &Path, reads: Reads, cache: Option<(&Path, &str)>) -> Corpus {
        let isolated: BTreeSet<String> = ["secret".to_owned()].into();
        let fence = Fence::new(&home.config.scan.local_only, &[home.workspace.join("secret")]);
        let labeler = Labeler::new(home, &isolated, &fence);
        let mut opts = Options::new(root.to_path_buf());
        opts.reads = reads;
        opts.threads = 2;
        Corpus::load(cache, &opts, &fence, &labeler).unwrap()
    }

    #[test]
    fn the_meter_counts_turns_once_maps_labels_and_never_opens_the_fence() {
        let (base, home) = fixture("count");
        let root = transcripts(&base);
        let c = corpus(&home, &root, Reads::default(), None);
        let work = BTreeMap::new();
        let all = kpis(&c, None, Span::ALL, Reads::default(), &work);
        // demo: msg1 once (10+100+20+5), three reads (4 each), the nested subagent (3+2); root: 2+1.
        assert_eq!(
            all.turns,
            1 + 3 + 1 + 1,
            "one response split over two lines counts once"
        );
        assert_eq!(all.tokens, 135 + 12 + 5 + 3);
        assert_eq!(all.human_turns, 1);
        assert_eq!(all.asks, 1);
        assert_eq!(all.ask_latency_p50_ms, Some(1750));
        let demo = kpis(&c, Some("demo"), Span::ALL, Reads::default(), &work);
        assert_eq!(demo.tokens, 135 + 12 + 5);
        assert_eq!(demo.sessions, 1, "the subagent belongs to its session");
        assert_eq!(demo.contexts, 2, "divergence 2: the nested workflow subagent is found");
        assert_eq!(kpis(&c, Some(ROOT_LABEL), Span::ALL, Reads::default(), &work).tokens, 3);
        // Never opened: the fenced and isolated folders are not even listed (demo main + subagent, root x2).
        assert_eq!(c.sources.len(), 4, "a fenced folder's transcripts are never listed");
        assert_eq!(c.folders.len(), 2);
        // The fence: no 7777 (fenced by name, isolated by name) and no 5000/6000 (a fenced cwd drops the file).
        assert!(c.scans.iter().all(|f| f.turns.iter().all(|t| t.4 < 5000)));
        assert!(
            c.scans.iter().any(|f| f.fenced),
            "the wandering session is dropped whole"
        );
        assert!(
            c.scans
                .iter()
                .all(|f| !f.labels.iter().any(|l| l.contains("secret") || l.contains("Sample")))
        );
        // Off by default: no byte lengths, no re-reads.
        assert_eq!(all.gw_result_bytes, None);
        assert_eq!(all.rereads_per_session_median, None);
        // Prices: dated id prices as its row; usd > 0.
        assert!(all.usd > 0.0 && all.unpriced_turns == 0);
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn the_flagged_reads_count_overhead_and_rereads_and_nothing_textual_is_kept() {
        let (base, home) = fixture("flags");
        let root = transcripts(&base);
        let both = Reads {
            overhead: true,
            rereads: true,
        };
        let c = corpus(&home, &root, both, None);
        let all = kpis(&c, Some("demo"), Span::ALL, both, &BTreeMap::new());
        assert_eq!(all.gw_result_bytes, Some(298));
        assert_eq!(all.packet_bytes_p50, Some(298));
        assert_eq!(all.reread_calls, Some(2), "one file read three times: two repeats");
        assert_eq!(all.reread_bytes, Some(200));
        assert_eq!(all.search_bytes_after_ask, Some(300));
        assert_eq!(all.search_bytes_before_ask, Some(0));
        assert_eq!(all.rereads_per_session_median, Some(1.0));
        assert!(all.overhead_share.unwrap() > 0.0);
        // Divergence 1: nothing kept is text or input: the canary appears in no serialized scan.
        for f in &c.scans {
            let s = serde_json::to_string(f).unwrap();
            let base_text = base.display().to_string();
            let leaks = [CANARY, "notes.txt", base_text.as_str(), "gw-meter-flags"];
            assert!(leaks.iter().all(|l| !s.contains(l)), "{s}");
        }
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn a_second_run_parses_only_what_changed_and_resumes_a_grown_file() {
        let (base, home) = fixture("cache");
        let root = transcripts(&base);
        let cache = base.join("cost-cache.jsonl");
        let first = corpus(&home, &root, Reads::default(), Some((&cache, "fp")));
        assert_eq!(first.stats.files_cached, 0);
        let second = corpus(&home, &root, Reads::default(), Some((&cache, "fp")));
        assert_eq!(second.stats.files_parsed + second.stats.files_resumed, 0);
        assert_eq!(second.stats.files_cached, second.stats.files);
        // The trailing partial line completes: only that file resumes, from its offset.
        let demo_slug = slug_form(&base.join("ws/demo").display().to_string());
        let main = root.join(&demo_slug).join(format!("{UUID}.jsonl"));
        let mut f = std::fs::OpenOptions::new().append(true).open(&main).unwrap();
        writeln!(f, ",\"cwd\":{:?},\"requestId\":\"late\",\"message\":{{\"id\":\"late\",\"model\":\"claude-opus-5-5\",\"usage\":{{\"input_tokens\":1000}}}}}}", base.join("ws/demo").display().to_string()).unwrap();
        drop(f);
        let third = corpus(&home, &root, Reads::default(), Some((&cache, "fp")));
        assert_eq!((third.stats.files_resumed, third.stats.files_parsed), (1, 0));
        let t = kpis(&third, Some("demo"), Span::ALL, Reads::default(), &BTreeMap::new());
        assert_eq!(t.tokens, 135 + 12 + 5 + 1000);
        // Same answer as a cold parse.
        let cold = corpus(&home, &root, Reads::default(), None);
        assert_eq!(
            kpis(&cold, None, Span::ALL, Reads::default(), &BTreeMap::new()).tokens,
            kpis(&third, None, Span::ALL, Reads::default(), &BTreeMap::new()).tokens
        );
        // A different fingerprint ignores the cache.
        let other = corpus(&home, &root, Reads::default(), Some((&cache, "other")));
        assert_eq!(other.stats.files_cached, 0);
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn a_session_is_a_main_transcript_with_its_subagents_and_fenced_files_are_not_counted() {
        let (base, home) = fixture("sessions");
        let root = transcripts(&base);
        let c = corpus(&home, &root, Reads::default(), None);
        // demo's main + its nested subagent are one session; the root's clean transcript is another;
        // the wandering transcript was dropped by the fence and is counted nowhere.
        assert_eq!(
            transcript_counts(&c),
            TranscriptCounts {
                main: 2,
                subagent: 1,
                main_with_turns: 2,
                subagent_with_turns: 1,
                sessions: 2,
                sessions_subagent_only: 0,
            }
        );
        let all = kpis(&c, None, Span::ALL, Reads::default(), &BTreeMap::new());
        assert_eq!((all.sessions, all.contexts), (2, 3));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn rereads_on_or_off_the_totals_are_the_same_even_for_inputs_that_are_not_objects() {
        let (base, home) = fixture("odd");
        let root = transcripts(&base);
        let demo = base.join("ws/demo");
        let odd = [
            json!([{"type": "tool_use", "id": "o1", "name": "Read", "input": "not an object"}]),
            json!([{"type": "tool_use", "id": "o2", "name": "Read", "input": {"file_path": 42, "offset": [1]}}]),
            json!([{"type": "tool_use", "id": "o3", "name": "Read", "input": [1, 2]}]),
        ];
        let body: String = odd
            .into_iter()
            .enumerate()
            .map(|(n, blocks)| {
                assistant(
                    &demo,
                    &format!("2026-10-20T12:00:0{n}.000Z"),
                    &format!("odd{n}"),
                    &format!("odd{n}"),
                    "claude-opus-5-5",
                    [1, 0, 0, 1],
                    blocks,
                )
            })
            .collect();
        std::fs::write(
            root.join(slug_form(&demo.display().to_string())).join("odd.jsonl"),
            body,
        )
        .unwrap();
        let on = Reads {
            overhead: true,
            rereads: true,
        };
        let off = kpis(
            &corpus(&home, &root, Reads::default(), None),
            None,
            Span::ALL,
            Reads::default(),
            &BTreeMap::new(),
        );
        let with = kpis(&corpus(&home, &root, on, None), None, Span::ALL, on, &BTreeMap::new());
        assert_eq!((off.turns, off.tokens), (with.turns, with.tokens));
        assert_eq!(
            off.tokens,
            135 + 12 + 5 + 3 + 6,
            "the three odd lines still count their usage"
        );
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn no_read_hash_reaches_cost_jsonl_or_the_cache() {
        let (base, home) = fixture("leak");
        let root = transcripts(&base);
        let on = Reads {
            overhead: true,
            rereads: true,
        };
        let cache = cache_path(&home);
        let c = corpus(&home, &root, on, Some((&cache, "fp")));
        let mut line = CostLine::default();
        line.totals.all_time = kpis(&c, None, Span::ALL, on, &BTreeMap::new());
        line.transcripts = transcript_counts(&c);
        assert_eq!(
            line.totals.all_time.reread_calls,
            Some(2),
            "the fixture's file was read three times"
        );
        let written = record(&home, &line).unwrap();
        let path = format!("{}/notes.txt", base.join("ws/demo").display());
        let h = read_hash(&path, "", "");
        let forms = [
            h.to_string(),
            format!("{h:016x}"),
            format!("{h:x}"),
            (h as i64).to_string(),
        ];
        for file in [written, cache] {
            let bytes = std::fs::read_to_string(&file).unwrap();
            assert!(!bytes.is_empty());
            for f in &forms {
                assert!(!bytes.contains(f.as_str()), "a Read hash reached {}", file.display());
            }
        }
        // The key is per process: the same path hashes differently under another key (here, none).
        assert_ne!(
            h,
            hash64(&[
                b"\0",
                path.to_lowercase().replace('\\', "/").as_bytes(),
                b"\0",
                b"",
                b"\0",
                b""
            ])
        );
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn prices_are_data_and_dated_ids_fold() {
        assert_eq!(normalize_model_id("claude-haiku-4-5-20251001"), "claude-haiku-4-5");
        assert_eq!(normalize_model_id("claude-opus-5-5"), "claude-opus-5-5");
        let usd = price_turn("claude-opus-5-5", 1_000_000, 0, 1_000_000, 1_000_000).unwrap();
        assert!((usd - (4.0 + 0.20 + 20.0)).abs() < 1e-9, "{usd}");
        let cw = price_turn("claude-opus-5", 0, 1_000_000, 0, 0).unwrap();
        assert!((cw - 6.25).abs() < 1e-9, "cache write defaults to 1.25 x input");
        assert_eq!(price_turn("claude-mystery-9", 1, 0, 0, 1), None);
    }

    #[test]
    fn the_red_lines_fire_and_stay_quiet() {
        let mut line = CostLine::default();
        line.window_days = 7;
        line.totals.window.overhead_share = Some(0.11);
        line.totals.previous_window.tokens_per_human_turn = 100.0;
        line.totals.window.tokens_per_human_turn = 130.0;
        line.totals.window.models = [("claude-opus-5-5".to_owned(), 10)].into();
        line.totals.previous_window.models = [("claude-opus-5-5".to_owned(), 10)].into();
        line.asks = AskUse {
            asks: 10,
            passed: 5,
            previous_asks: 10,
            previous_passed: 8,
            gw_tokens_per_passed: None,
        };
        let r = red(&line);
        assert_eq!(r.len(), 3, "{r:?}");
        // A model-mix change excuses the rise; a low overhead and a steady pass rate are quiet.
        line.totals.previous_window.models = [("claude-opus-5".to_owned(), 10)].into();
        line.totals.window.overhead_share = Some(0.02);
        line.asks.passed = 9;
        assert!(red(&line).is_empty(), "{:?}", red(&line));
    }

    #[test]
    fn epoch_ms_reads_milliseconds() {
        assert_eq!(epoch_ms("1970-01-01T00:00:01.250Z"), Some(1250));
        assert_eq!(epoch_ms("1970-01-01T00:00:01Z"), Some(1000));
        assert_eq!(epoch_ms("1970-01-01T00:00:01.5Z"), Some(1500));
        assert_eq!(epoch_ms("yesterday"), None);
    }
}
