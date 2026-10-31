//! The Stop hook's tool-use count without reading the whole transcript (PERF-BASELINE.md row #3, A.3:
//! a 317 MB transcript took 1.6-2.0 s and 312 MB per turn end).
//!
//! Claude Code appends transcript lines in time order, so the assistant lines at or after the session's
//! start are a suffix of the file. [`scan_back`] reads backwards from the end in fixed chunks and stops
//! only after a run of assistant lines that are all older than `since` and long enough to be safe
//! ([`MARGIN`]: lines are not strictly in order; sidechains and compaction summaries move). Lines are
//! split on bytes and only those holding `"assistant"` are deserialized, into a small typed row that
//! keeps `type`, `timestamp` and each content block's `type` and `name`; nothing else is read.
//!
//! For a long-lived session the start stays fixed while the suffix grows, so a [`Checkpoint`] keeps the
//! counts, the byte offset they cover and a hash of the 4 KB before it under
//! `<home>/state/stop/<session>.json`. The next Stop reads only the new bytes; any doubt (a different
//! start, a shorter file, a hash that does not match) falls back to the backward scan. The checkpoint
//! holds counts, offsets and hashes only: never content, and the transcript path only as a hash.
//!
//! The result is the count `hook::count_tool_uses` gives over the whole file, or more ("would rather ask
//! once too often"); `examples/transcript_differential.rs` checks that over every real transcript.

use std::borrow::Cow;
use std::fmt;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use serde::de::value::MapAccessDeserializer;
use serde::de::{self, Deserializer, IgnoredAny, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::hook::{ToolUses, classify};

/// Bytes read per step, backwards or forwards.
pub const CHUNK: u64 = 1 << 20;

/// How far past the first older line the backward scan keeps going. Both must hold before it stops.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Margin {
    /// Older assistant lines in an unbroken run (a newer one resets the run).
    pub lines: u64,
    /// Bytes between the end of the run's first older line and the start of its latest.
    pub bytes: u64,
}

/// The margin in use. It must exceed the widest out-of-order gap between assistant lines, in lines or
/// in bytes, for the count to stay a superset: a missed line needs a whole run of older lines after it.
/// Census of every transcript on THERIGXD (2026-10-30, `examples/transcript_census.rs`, 2,155 files,
/// 3.97 GB, 240,472 assistant lines): 0 assistant lines without a timestamp; 1,835 out of order in 14
/// files, newer by up to 20.6 h; the widest gap 655 assistant lines / 36.5 MB. 64 lines lost counts in
/// the differential (6 of 10,795 cases); 1,024 lost none.
pub const MARGIN: Margin = Margin {
    lines: 1024,
    bytes: 1 << 20,
};

/// The hash covers this many bytes before a checkpoint's offset.
const GUARD: u64 = 4096;
/// Checkpoints untouched this long are removed.
const KEEP: Duration = Duration::from_secs(7 * 24 * 3600);
/// At most this many checkpoint files are looked at for pruning per Stop.
const PRUNE_BUDGET: usize = 64;
/// The head read for a first timestamp stops here.
const HEAD_MAX: u64 = 4 << 20;

/// `since` as compared: the first 19 bytes of an RFC 3339 time (to the second), as `count_tool_uses` does.
pub fn since_key(since: &str) -> &[u8] {
    &since.as_bytes()[..since.len().min(19)]
}

// ---- one line --------------------------------------------------------------------------------------

/// A JSON string, or nothing for any other value (skipped, never kept).
struct MaybeStr<'a>(Option<Cow<'a, str>>);

/// The visitor methods that skip a value of a kind the caller does not want.
macro_rules! skip_other_kinds {
    ($none:expr) => {
        fn visit_bool<E: de::Error>(self, _: bool) -> Result<Self::Value, E> {
            Ok($none)
        }
        fn visit_i64<E: de::Error>(self, _: i64) -> Result<Self::Value, E> {
            Ok($none)
        }
        fn visit_u64<E: de::Error>(self, _: u64) -> Result<Self::Value, E> {
            Ok($none)
        }
        fn visit_f64<E: de::Error>(self, _: f64) -> Result<Self::Value, E> {
            Ok($none)
        }
        fn visit_unit<E: de::Error>(self) -> Result<Self::Value, E> {
            Ok($none)
        }
        fn visit_none<E: de::Error>(self) -> Result<Self::Value, E> {
            Ok($none)
        }
    };
}

impl<'de: 'a, 'a> Deserialize<'de> for MaybeStr<'a> {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V<'a>(std::marker::PhantomData<&'a ()>);
        impl<'de: 'a, 'a> Visitor<'de> for V<'a> {
            type Value = MaybeStr<'a>;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("any JSON value")
            }
            skip_other_kinds!(MaybeStr(None));
            fn visit_borrowed_str<E: de::Error>(self, v: &'de str) -> Result<Self::Value, E> {
                Ok(MaybeStr(Some(Cow::Borrowed(v))))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Self::Value, E> {
                Ok(MaybeStr(Some(Cow::Owned(v.to_owned()))))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut a: A) -> Result<Self::Value, A::Error> {
                while a.next_element::<IgnoredAny>()?.is_some() {}
                Ok(MaybeStr(None))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut a: A) -> Result<Self::Value, A::Error> {
                while a.next_entry::<IgnoredAny, IgnoredAny>()?.is_some() {}
                Ok(MaybeStr(None))
            }
        }
        d.deserialize_any(V(std::marker::PhantomData))
    }
}

/// A content block: its `type` and `name`; `input` and everything else are skipped unread.
#[derive(Deserialize)]
struct Block<'a> {
    #[serde(rename = "type", default, borrow)]
    kind: Option<MaybeStr<'a>>,
    #[serde(default, borrow)]
    name: Option<MaybeStr<'a>>,
}

/// `message.content` when it is an array: its blocks (a non-object element is no block). Any other
/// value (a plain string) has none.
struct Blocks<'a>(Vec<Block<'a>>);

impl<'de: 'a, 'a> Deserialize<'de> for Blocks<'a> {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct OneBlock<'a>(Option<Block<'a>>);
        impl<'de: 'a, 'a> Deserialize<'de> for OneBlock<'a> {
            fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
                struct V<'a>(std::marker::PhantomData<&'a ()>);
                impl<'de: 'a, 'a> Visitor<'de> for V<'a> {
                    type Value = OneBlock<'a>;
                    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                        f.write_str("any JSON value")
                    }
                    skip_other_kinds!(OneBlock(None));
                    fn visit_str<E: de::Error>(self, _: &str) -> Result<Self::Value, E> {
                        Ok(OneBlock(None))
                    }
                    fn visit_seq<A: SeqAccess<'de>>(self, mut a: A) -> Result<Self::Value, A::Error> {
                        while a.next_element::<IgnoredAny>()?.is_some() {}
                        Ok(OneBlock(None))
                    }
                    fn visit_map<A: MapAccess<'de>>(self, a: A) -> Result<Self::Value, A::Error> {
                        Block::deserialize(MapAccessDeserializer::new(a)).map(|b| OneBlock(Some(b)))
                    }
                }
                d.deserialize_any(V(std::marker::PhantomData))
            }
        }
        struct V<'a>(std::marker::PhantomData<&'a ()>);
        impl<'de: 'a, 'a> Visitor<'de> for V<'a> {
            type Value = Blocks<'a>;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("any JSON value")
            }
            skip_other_kinds!(Blocks(Vec::new()));
            fn visit_str<E: de::Error>(self, _: &str) -> Result<Self::Value, E> {
                Ok(Blocks(Vec::new()))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut a: A) -> Result<Self::Value, A::Error> {
                let mut out = Vec::new();
                while let Some(OneBlock(b)) = a.next_element()? {
                    out.extend(b);
                }
                Ok(Blocks(out))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut a: A) -> Result<Self::Value, A::Error> {
                while a.next_entry::<IgnoredAny, IgnoredAny>()?.is_some() {}
                Ok(Blocks(Vec::new()))
            }
        }
        d.deserialize_any(V(std::marker::PhantomData))
    }
}

#[derive(Deserialize)]
struct Message<'a> {
    #[serde(default, borrow)]
    content: Option<Blocks<'a>>,
}

/// The fields of a transcript row the count reads.
#[derive(Deserialize)]
struct Row<'a> {
    #[serde(rename = "type", default, borrow)]
    kind: Option<MaybeStr<'a>>,
    #[serde(default, borrow)]
    timestamp: Option<MaybeStr<'a>>,
    #[serde(default, borrow)]
    message: Option<Message<'a>>,
}

/// What one line is to the count.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Seen {
    /// Not an assistant row (or not JSON): neither counted nor part of a run.
    Other,
    /// An assistant row at or after `since` (or any assistant row when there is no `since`).
    Counted(ToolUses),
    /// An assistant row with no timestamp: counted wherever it is, and no evidence of age.
    Untimed(ToolUses),
    /// An assistant row older than `since`.
    Older,
}

fn finder() -> memchr::memmem::Finder<'static> {
    memchr::memmem::Finder::new(b"\"assistant\"")
}

/// Classify one line (without its newline; a trailing `\r` is whitespace to the parser).
pub fn see(line: &[u8], since: Option<&[u8]>, f: &memchr::memmem::Finder) -> Seen {
    // Any row whose `type` is "assistant" holds those bytes; most other rows do not, and are skipped
    // without a parse.
    if f.find(line).is_none() {
        return Seen::Other;
    }
    let Ok(row) = serde_json::from_slice::<Row>(line) else {
        return Seen::Other;
    };
    if row.kind.and_then(|k| k.0).as_deref() != Some("assistant") {
        return Seen::Other;
    }
    let ts = row.timestamp.and_then(|t| t.0);
    if let (Some(since), Some(ts)) = (since, ts.as_deref())
        && since_key(ts) < since
    {
        return Seen::Older;
    }
    let mut t = ToolUses::default();
    for b in row.message.and_then(|m| m.content).map(|c| c.0).unwrap_or_default() {
        if b.kind.and_then(|k| k.0).as_deref() == Some("tool_use")
            && let Some(f) = b.name.and_then(|n| n.0).as_deref().and_then(classify)
        {
            f(&mut t);
        }
    }
    if ts.is_some() {
        Seen::Counted(t)
    } else {
        Seen::Untimed(t)
    }
}

pub fn add(a: &mut ToolUses, b: ToolUses) {
    a.edit += b.edit;
    a.write += b.write;
    a.bash += b.bash;
    a.run += b.run;
    a.mcp_write += b.mcp_write;
    a.denied += b.denied;
}

// ---- scanning --------------------------------------------------------------------------------------

/// The count over a stretch of the file, split where a checkpoint may resume.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Tail {
    /// Uses on newline-terminated lines.
    pub uses: ToolUses,
    /// Uses on a final line with no newline yet (it may still be being written; it is counted now
    /// and read again next time, so it is kept out of a checkpoint).
    pub partial: ToolUses,
    /// The end of the last newline-terminated line: where the next read resumes.
    pub resume_at: u64,
    /// Bytes read (for the bench's own accounting; never logged).
    pub bytes_read: u64,
}

impl Tail {
    pub fn total(&self) -> ToolUses {
        let mut t = self.uses;
        add(&mut t, self.partial);
        t
    }
}

fn read_at<R: Read + Seek>(r: &mut R, at: u64, buf: &mut [u8]) -> io::Result<()> {
    r.seek(SeekFrom::Start(at))?;
    r.read_exact(buf)
}

/// Count backwards from `len` (the file's length) until [`Margin`] is met or the file starts.
pub fn scan_back<R: Read + Seek>(r: &mut R, len: u64, since: Option<&[u8]>, margin: Margin) -> io::Result<Tail> {
    let f = finder();
    let mut out = Tail::default();
    let mut chunk = Vec::new();
    let mut spill = Vec::new();
    // The line being assembled ends here (exclusive of its newline).
    let mut line_end = len;
    // Whether that line had a newline after it: the file's last line may not.
    let mut terminated = false;
    let mut resume_at = None;
    let mut run_lines = 0u64;
    let mut run_from: Option<u64> = None;
    let mut pos = len;
    let mut take = |line: &[u8], start: u64, end: u64, terminated: bool, out: &mut Tail| -> bool {
        if line.is_empty() {
            return false;
        }
        match see(line, since, &f) {
            Seen::Other => {}
            Seen::Counted(t) | Seen::Untimed(t) if !terminated => add(&mut out.partial, t),
            Seen::Counted(t) => {
                add(&mut out.uses, t);
                run_lines = 0;
                run_from = None;
            }
            Seen::Untimed(t) => add(&mut out.uses, t),
            Seen::Older => {
                let from = *run_from.get_or_insert(end);
                run_lines += 1;
                return run_lines >= margin.lines && from - start >= margin.bytes;
            }
        }
        false
    };
    'outer: while pos > 0 {
        let start = pos.saturating_sub(CHUNK);
        chunk.resize((pos - start) as usize, 0);
        read_at(r, start, &mut chunk)?;
        out.bytes_read += pos - start;
        let mut hi = chunk.len();
        while let Some(i) = memchr::memrchr(b'\n', &chunk[..hi]) {
            let nl = start + i as u64;
            if nl + 1 == len {
                // The file ends with a newline: the last line is terminated.
                terminated = true;
                resume_at.get_or_insert(len);
                line_end = nl;
                hi = i;
                continue;
            }
            resume_at.get_or_insert(nl + 1);
            let line_start = nl + 1;
            let line: &[u8] = if line_end <= pos {
                &chunk[i + 1..(line_end - start) as usize]
            } else {
                // The line runs past this chunk: read it whole, once.
                spill.resize((line_end - line_start) as usize, 0);
                read_at(r, line_start, &mut spill)?;
                out.bytes_read += line_end - pos;
                &spill
            };
            if take(line, line_start, line_end, terminated, &mut out) {
                break 'outer;
            }
            terminated = true;
            line_end = nl;
            hi = i;
        }
        pos = start;
        if pos == 0 && line_end > 0 {
            // `chunk` holds [0, old pos) here.
            let line: &[u8] = if line_end as usize <= chunk.len() {
                &chunk[..line_end as usize]
            } else {
                spill.resize(line_end as usize, 0);
                read_at(r, 0, &mut spill)?;
                &spill
            };
            take(line, 0, line_end, terminated, &mut out);
        }
    }
    out.resume_at = resume_at.unwrap_or(0);
    Ok(out)
}

/// Count forwards over `[from, len)`: the new bytes after a checkpoint. Older lines are skipped; no run
/// is needed, every line is seen.
pub fn scan_forward<R: Read + Seek>(r: &mut R, from: u64, len: u64, since: Option<&[u8]>) -> io::Result<Tail> {
    let f = finder();
    let mut out = Tail {
        resume_at: from,
        ..Tail::default()
    };
    let mut chunk = Vec::new();
    let mut carry: Vec<u8> = Vec::new();
    let mut pos = from;
    while pos < len {
        let end = (pos + CHUNK).min(len);
        chunk.resize((end - pos) as usize, 0);
        read_at(r, pos, &mut chunk)?;
        out.bytes_read += end - pos;
        let mut lo = 0;
        while let Some(i) = memchr::memchr(b'\n', &chunk[lo..]) {
            let line: &[u8] = if carry.is_empty() {
                &chunk[lo..lo + i]
            } else {
                carry.extend_from_slice(&chunk[lo..lo + i]);
                &carry
            };
            if let Seen::Counted(t) | Seen::Untimed(t) = see(line, since, &f) {
                add(&mut out.uses, t);
            }
            carry.clear();
            lo += i + 1;
            out.resume_at = pos + lo as u64;
        }
        carry.extend_from_slice(&chunk[lo..]);
        pos = end;
    }
    if !carry.is_empty()
        && let Seen::Counted(t) | Seen::Untimed(t) = see(&carry, since, &f)
    {
        add(&mut out.partial, t);
    }
    Ok(out)
}

/// The first row with a string `timestamp`, read from the head of the file only (at most 4 MB):
/// the session's start when nothing else says. None if the head has none; the caller then counts
/// everything.
pub fn first_timestamp<R: Read>(r: R) -> Option<String> {
    #[derive(Deserialize)]
    struct Ts<'a> {
        #[serde(default, borrow)]
        timestamp: Option<MaybeStr<'a>>,
    }
    // Line by line, stopping at the first timestamp: usually the first line or two, not 4 MB.
    let mut head = io::BufReader::with_capacity(64 << 10, r.take(HEAD_MAX));
    let mut line = Vec::new();
    loop {
        line.clear();
        if io::BufRead::read_until(&mut head, b'\n', &mut line).ok()? == 0 {
            return None;
        }
        if let Some(ts) = serde_json::from_slice::<Ts>(&line)
            .ok()
            .and_then(|r| r.timestamp.and_then(|t| t.0).map(Cow::into_owned))
        {
            return Some(ts);
        }
    }
}

// ---- the checkpoint ---------------------------------------------------------------------------------

fn sha256_hex(parts: &[&[u8]]) -> String {
    let mut h = Sha256::new();
    for p in parts {
        h.update(p);
    }
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// What a Stop keeps between turns: counts, offsets and hashes; no content, no path in clear.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Checkpoint {
    pub v: u32,
    pub session: String,
    pub path_sha256: String,
    pub since: Option<String>,
    /// The counts cover `[.., offset)`; the next read starts here.
    pub offset: u64,
    /// The file's length when this was written.
    pub len: u64,
    /// SHA-256 of the (up to) 4 KB before `offset`.
    pub guard_sha256: String,
    pub edit: u64,
    pub write: u64,
    pub bash: u64,
    pub run: u64,
    pub mcp_write: u64,
}

impl Checkpoint {
    fn uses(&self) -> ToolUses {
        ToolUses {
            edit: self.edit,
            write: self.write,
            bash: self.bash,
            run: self.run,
            mcp_write: self.mcp_write,
            denied: 0,
        }
    }
}

fn guard<R: Read + Seek>(r: &mut R, offset: u64) -> io::Result<String> {
    let from = offset.saturating_sub(GUARD);
    let mut buf = vec![0; (offset - from) as usize];
    read_at(r, from, &mut buf)?;
    Ok(sha256_hex(&[&buf]))
}

/// The checkpoint file's name: the session id when it is plain, else its hash.
fn file_name(session: &str) -> Option<String> {
    if session.is_empty() {
        return None;
    }
    let plain = session.len() <= 80
        && session
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
    Some(if plain {
        format!("{session}.json")
    } else {
        format!("{}.json", &sha256_hex(&[session.as_bytes()])[..32])
    })
}

/// Where checkpoints live: `<home>/state/stop`.
pub fn dir(home_root: &Path) -> PathBuf {
    home_root.join("state").join("stop")
}

/// How a count was come to (for tests and the bench; never logged).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Route {
    /// From the checkpoint, reading only the bytes after it.
    Resumed,
    /// Backwards from the end (no usable checkpoint).
    Scanned,
}

/// Count the tool uses in `transcript` at or after `since`, resuming from the session's checkpoint
/// under `ckpt_dir` when it still fits, and writing a new one. Checkpoint trouble of any kind falls
/// back to the scan and is otherwise ignored: it is an optimisation, never a source of truth.
pub fn count(
    transcript: &Path,
    since: Option<&str>,
    session: &str,
    ckpt_dir: Option<&Path>,
    margin: Margin,
) -> io::Result<(ToolUses, Route)> {
    let mut file = File::open(transcript)?;
    let len = file.metadata()?.len();
    let key = since.map(since_key);
    let path_sha = sha256_hex(&[transcript.to_string_lossy().as_bytes()]);
    let since_s = key.map(|k| String::from_utf8_lossy(k).into_owned());
    let ckpt_file = ckpt_dir.zip(file_name(session)).map(|(d, n)| d.join(n));
    let prior = ckpt_file
        .as_deref()
        .and_then(|p| std::fs::read(p).ok())
        .and_then(|b| serde_json::from_slice::<Checkpoint>(&b).ok())
        .filter(|c| c.v == 1 && c.session == session && c.path_sha256 == path_sha && c.since == since_s)
        .filter(|c| c.offset <= len && c.len <= len)
        .filter(|c| guard(&mut file, c.offset).is_ok_and(|g| g == c.guard_sha256));
    let (base, tail, how) = match prior {
        Some(c) => (c.uses(), scan_forward(&mut file, c.offset, len, key)?, Route::Resumed),
        None => (
            ToolUses::default(),
            scan_back(&mut file, len, key, margin)?,
            Route::Scanned,
        ),
    };
    let mut kept = base;
    add(&mut kept, tail.uses);
    if let Some(path) = ckpt_file
        && let Ok(g) = guard(&mut file, tail.resume_at)
    {
        let c = Checkpoint {
            v: 1,
            session: session.to_owned(),
            path_sha256: path_sha,
            since: since_s,
            offset: tail.resume_at,
            len,
            guard_sha256: g,
            edit: kept.edit,
            write: kept.write,
            bash: kept.bash,
            run: kept.run,
            mcp_write: kept.mcp_write,
        };
        let _ = write_atomic(&path, &c);
        prune(path.parent().unwrap_or(Path::new(".")), &path);
    }
    let mut total = kept;
    add(&mut total, tail.partial);
    Ok((total, how))
}

fn write_atomic(path: &Path, c: &Checkpoint) -> io::Result<()> {
    if let Some(d) = path.parent() {
        std::fs::create_dir_all(d)?;
    }
    let tmp = path.with_extension(format!("tmp-{}", std::process::id()));
    std::fs::write(&tmp, serde_json::to_vec(c).map_err(io::Error::other)?)?;
    std::fs::rename(&tmp, path).inspect_err(|_| {
        let _ = std::fs::remove_file(&tmp);
    })
}

/// Remove checkpoints untouched for 7 days, looking at no more than [`PRUNE_BUDGET`] entries.
fn prune(dir: &Path, keep: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    let now = SystemTime::now();
    for e in entries.flatten().take(PRUNE_BUDGET) {
        let p = e.path();
        if p == keep {
            continue;
        }
        let old = e
            .metadata()
            .and_then(|m| m.modified())
            .ok()
            .and_then(|m| now.duration_since(m).ok())
            .is_some_and(|age| age > KEEP);
        if old {
            let _ = std::fs::remove_file(p);
        }
    }
}

/// Seconds since the epoch (tests age checkpoint files with it).
#[cfg(test)]
fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hook::count_tool_uses;
    use std::io::Cursor;

    fn line(kind: &str, ts: Option<&str>, tools: &[&str]) -> String {
        let blocks: Vec<String> = tools
            .iter()
            .map(|n| format!(r#"{{"type":"tool_use","name":"{n}","input":{{"command":"x"}}}}"#))
            .collect();
        match ts {
            Some(ts) => format!(
                r#"{{"type":"{kind}","timestamp":"{ts}","message":{{"content":[{}]}}}}"#,
                blocks.join(",")
            ),
            None => format!(r#"{{"type":"{kind}","message":{{"content":[{}]}}}}"#, blocks.join(",")),
        }
    }

    fn ts(sec: u64) -> String {
        format!(
            "2026-10-30T{:02}:{:02}:{:02}.000Z",
            sec / 3600,
            (sec / 60) % 60,
            sec % 60
        )
    }

    /// `n` assistant lines one second apart from `from`, each one Bash, padded so each is ~`pad` bytes.
    fn session(from: u64, n: u64, pad: usize) -> String {
        let mut s = String::new();
        for i in 0..n {
            s.push_str(&format!(
                r#"{{"type":"user","timestamp":"{}","message":{{"content":"{}"}}}}"#,
                ts(from + i),
                "u".repeat(pad)
            ));
            s.push('\n');
            s.push_str(&line("assistant", Some(&ts(from + i)), &["Bash"]));
            s.push('\n');
        }
        s
    }

    fn back(text: &str, since: Option<&str>, margin: Margin) -> ToolUses {
        let mut c = Cursor::new(text.as_bytes());
        scan_back(&mut c, text.len() as u64, since.map(since_key), margin)
            .unwrap()
            .total()
    }

    fn full(text: &str, since: Option<&str>) -> ToolUses {
        count_tool_uses(text, since)
    }

    #[test]
    fn the_backward_scan_matches_the_full_read_on_ordered_lines() {
        let text = session(0, 400, 3000);
        for since in [
            None,
            Some(ts(0)),
            Some(ts(1)),
            Some(ts(200)),
            Some(ts(399)),
            Some(ts(5000)),
        ] {
            assert_eq!(
                back(&text, since.as_deref(), MARGIN),
                full(&text, since.as_deref()),
                "since {since:?}"
            );
        }
    }

    #[test]
    fn a_tool_use_one_line_before_the_chunk_boundary_is_counted() {
        // Lines of ~3 KB: the 1 MB chunk boundary falls inside the session; every since must agree.
        let text = session(0, 600, 3000);
        let boundary = text.len() as u64 - CHUNK;
        // The assistant line that ends just before the boundary.
        let before = text[..boundary as usize].matches("\"assistant\"").count() as u64;
        let since = ts(before - 1);
        let got = back(&text, Some(&since), MARGIN);
        assert_eq!(got, full(&text, Some(&since)));
        assert_eq!(got.bash, 600 - (before - 1));
    }

    #[test]
    fn out_of_order_lines_inside_the_margin_are_counted() {
        // A newer line (a sidechain written late) sits 40 older lines before the end of a run.
        let mut text = session(0, 100, 100);
        text.push_str(&line("assistant", Some(&ts(9000)), &["Edit"]));
        text.push('\n');
        text.push_str(&session(100, 40, 100));
        text.push_str(&session(9100, 3, 100));
        let since = ts(9000);
        let want = full(&text, Some(&since));
        assert_eq!(want.edit, 1);
        assert_eq!(back(&text, Some(&since), MARGIN), want);
        // The red arm: stop at the first older line and the late Edit is lost.
        let first_older = Margin { lines: 1, bytes: 0 };
        assert_eq!(back(&text, Some(&since), first_older).edit, 0);
    }

    #[test]
    fn a_gap_as_wide_as_the_census_found_is_inside_the_margin() {
        // The widest real gap (census 2026-10-30): a newer line, then 655 older assistant lines and
        // more than 1 MB, then the session goes on.
        let mut text = session(0, 100, 100);
        text.push_str(&line("assistant", Some(&ts(80_000)), &["Edit"]));
        text.push('\n');
        text.push_str(&session(1000, 655, 1700));
        text.push_str(&session(80_100, 3, 100));
        let since = ts(80_000);
        let want = full(&text, Some(&since));
        assert_eq!(want.edit, 1);
        assert_eq!(back(&text, Some(&since), MARGIN), want);
        // Red: the margin first proposed (64 lines and 1 MB) stops inside the gap and loses the Edit.
        let short = Margin {
            lines: 64,
            bytes: 1 << 20,
        };
        assert_eq!(back(&text, Some(&since), short).edit, 0);
    }

    #[test]
    fn untimed_assistant_lines_are_counted_where_the_scan_reaches() {
        let mut text = session(0, 10, 10);
        text.push_str(&line("assistant", None, &["Write"]));
        text.push('\n');
        text.push_str(&session(100, 3, 10));
        assert_eq!(back(&text, Some(&ts(100)), MARGIN), full(&text, Some(&ts(100))));
        assert_eq!(back(&text, Some(&ts(100)), MARGIN).write, 1);
    }

    #[test]
    fn a_truncated_last_line_and_crlf_are_read_as_the_full_read_reads_them() {
        let mut text = session(0, 20, 50).replace('\n', "\r\n");
        let last = line("assistant", Some(&ts(30)), &["Edit"]);
        text.push_str(&last[..last.len() / 2]);
        for since in [None, Some(ts(5))] {
            assert_eq!(back(&text, since.as_deref(), MARGIN), full(&text, since.as_deref()));
        }
        // Completed without a newline, the last line is counted, as a partial.
        let whole = format!("{}{}", &text[..text.len() - last.len() / 2], last);
        let mut c = Cursor::new(whole.as_bytes());
        let t = scan_back(&mut c, whole.len() as u64, Some(since_key(&ts(5))), MARGIN).unwrap();
        assert_eq!(t.partial.edit, 1);
        assert_eq!(t.total(), full(&whole, Some(&ts(5))));
        assert_eq!(t.resume_at as usize, whole.len() - last.len());
    }

    #[test]
    fn a_single_line_longer_than_a_chunk_is_read_whole() {
        let mut text = session(0, 3, 10);
        text.push_str(&format!(
            r#"{{"type":"assistant","timestamp":"{}","message":{{"content":[{{"type":"text","text":"{}"}},{{"type":"tool_use","name":"Write","input":{{}}}}]}}}}"#,
            ts(10),
            "x".repeat(3 * CHUNK as usize)
        ));
        text.push('\n');
        text.push_str(&session(20, 2, 10));
        for since in [None, Some(ts(10)), Some(ts(11))] {
            assert_eq!(back(&text, since.as_deref(), MARGIN), full(&text, since.as_deref()));
        }
    }

    #[test]
    fn odd_rows_are_skipped_as_the_full_read_skips_them() {
        let text = [
            r#"{"type":"assistant","timestamp":"2026-10-30T00:00:01Z","message":{"content":"just text"}}"#,
            r#"{"type":"assistant","timestamp":"2026-10-30T00:00:02Z","message":{"content":["s",1,{"type":"tool_use","name":"Edit"}]}}"#,
            r#"{"type":"assistant","timestamp":5,"message":{"content":[{"type":"tool_use","name":"Bash"}]}}"#,
            r#"{"type":"user","note":"\"assistant\"","message":{"content":[{"type":"tool_use","name":"Bash"}]}}"#,
            r#"{"type":"assistant","timestamp":"2026-10-30T00:00:03Z","message":{"content":[{"type":"tool_use","name":{"x":1}},{"type":"tool_use","name":"Write","input":{"a":[1,{"b":null}]}}]}}"#,
            "not json \"assistant\"",
            "",
        ]
        .join("\n");
        for since in [None, Some("2026-10-30T00:00:02Z")] {
            assert_eq!(back(&text, since, MARGIN), full(&text, since), "since {since:?}");
        }
    }

    fn tmp_dir(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("gw-tail-{tag}-{}-{}", std::process::id(), unix_now()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn the_checkpoint_resumes_on_append_and_falls_back_on_a_rewrite_or_a_shorter_file() {
        let d = tmp_dir("ckpt");
        let t = d.join("t.jsonl");
        let ck = d.join("state");
        let since = ts(50);
        let mut text = session(0, 100, 200);
        std::fs::write(&t, &text).unwrap();
        let go = |text: &str| {
            let (u, how) = count(&t, Some(&since), "sess-1", Some(&ck), MARGIN).unwrap();
            assert_eq!(u, full(text, Some(&since)));
            how
        };
        assert_eq!(go(&text), Route::Scanned);
        assert_eq!(go(&text), Route::Resumed);
        // Append: resumed, and the new lines are added.
        text.push_str(&session(100, 30, 200));
        std::fs::write(&t, &text).unwrap();
        assert_eq!(go(&text), Route::Resumed);
        // A half-written line, then its completion: counted once each time.
        let tail = line("assistant", Some(&ts(200)), &["Edit"]);
        std::fs::write(&t, format!("{text}{}", &tail[..10])).unwrap();
        assert_eq!(go(&format!("{text}{}", &tail[..10])), Route::Resumed);
        text.push_str(&tail);
        text.push('\n');
        std::fs::write(&t, &text).unwrap();
        assert_eq!(go(&text), Route::Resumed);
        // Rewrite of earlier bytes (same length, the guard's bytes differ): full fallback.
        let rewritten = text.replacen("\"Bash\"", "\"Edit\"", 1000);
        std::fs::write(&t, &rewritten).unwrap();
        assert_eq!(go(&rewritten), Route::Scanned);
        // A shorter file (rotated): full fallback.
        let short = session(0, 70, 200);
        std::fs::write(&t, &short).unwrap();
        assert_eq!(go(&short), Route::Scanned);
        // A different since never resumes another's counts.
        let (u, how) = count(&t, Some(&ts(10)), "sess-1", Some(&ck), MARGIN).unwrap();
        assert_eq!((u, how), (full(&short, Some(&ts(10))), Route::Scanned));
        // The file holds counts, offsets and hashes: no path, no content.
        let saved = std::fs::read_to_string(ck.join("sess-1.json")).unwrap();
        assert!(!saved.contains("t.jsonl") && !saved.contains("Bash") && !saved.contains("assistant"));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn a_session_id_that_is_not_plain_is_hashed_into_the_file_name() {
        assert_eq!(file_name("abc-123_x").as_deref(), Some("abc-123_x.json"));
        let n = file_name("../../etc/passwd").unwrap();
        let stem = n.strip_suffix(".json").unwrap();
        assert!(stem.len() == 32 && stem.bytes().all(|b| b.is_ascii_hexdigit()), "{n}");
        assert_eq!(file_name(""), None);
    }

    #[test]
    fn the_first_timestamp_comes_from_the_head() {
        let text = format!("{{\"type\":\"summary\"}}\nnot json\n{}", session(7, 3, 10));
        assert_eq!(first_timestamp(text.as_bytes()), Some(ts(7)));
        assert_eq!(first_timestamp(&b"{\"type\":\"summary\"}\n"[..]), None);
    }
}
