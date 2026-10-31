//! Differential for the Stop hook's tail read: `hook::count_tool_uses` over the whole file (the
//! reference) against `transcript_tail` over every real transcript, at several `since` values per file.
//! Run by hand, never in CI:
//! `cargo run --release -p gw-ops --example transcript_differential -- [--margin LINES BYTES] <dir>...`
//!
//! Prints COUNTS ONLY: no file name, no path, no content, not even in an error.
//!
//! Per file and `since` (none, the first timestamp, the middle, the last assistant line, after the end)
//! it compares four routes with the full read: the backward scan; the checkpoint route emulated at a
//! line boundary half way (scan the prefix, then read on from its resume point); and the real
//! `transcript_tail::count` twice with a throwaway checkpoint folder (the second call resumes).
//! Each comparison is `equal`, `superset` (every counter >= the full read's) or `UNDER`.

use std::io::Cursor;
use std::path::{Path, PathBuf};

use gw_ops::hook::{ToolUses, count_tool_uses};
use gw_ops::transcript_tail::{self as tail, MARGIN, Margin, Route};

#[derive(Default)]
struct Tally {
    equal: u64,
    superset: u64,
    under: u64,
}

impl Tally {
    fn add(&mut self, full: ToolUses, got: ToolUses) {
        let ge = got.edit >= full.edit
            && got.write >= full.write
            && got.bash >= full.bash
            && got.run >= full.run
            && got.mcp_write >= full.mcp_write;
        if got == full {
            self.equal += 1;
        } else if ge {
            self.superset += 1;
        } else {
            self.under += 1;
        }
    }
}

fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let p = e.path();
        match e.file_type() {
            Ok(t) if t.is_dir() => walk(&p, out),
            Ok(t) if t.is_file() && p.extension().is_some_and(|x| x == "jsonl") => out.push(p),
            _ => {}
        }
    }
}

/// Timestamps to test `since` at: first, middle (by bytes), last assistant line, after the end.
fn sinces(text: &str) -> Vec<Option<String>> {
    let ts_of = |l: &str| {
        serde_json::from_str::<serde_json::Value>(l)
            .ok()
            .and_then(|v| v["timestamp"].as_str().map(str::to_owned))
    };
    let first = text.lines().find_map(ts_of);
    // Byte offsets cut only just after a newline, so the slices stay on char boundaries.
    let mid = text.as_bytes()[text.len() / 2..]
        .iter()
        .position(|&b| b == b'\n')
        .and_then(|i| text[text.len() / 2 + i + 1..].lines().find_map(ts_of));
    let last = text
        .lines()
        .rev()
        .filter(|l| l.contains("\"assistant\""))
        .find_map(ts_of);
    let mut out = vec![None, first, mid, last, Some("2099-01-01T00:00:00Z".to_owned())];
    out.extend(inversion_sinces(text).into_iter().map(Some));
    out
}

/// For the file's widest out-of-order gaps between assistant lines (by lines and by bytes), the newer
/// line's timestamp: a `since` there is where a margin that is too short loses a count.
fn inversion_sinces(text: &str) -> Vec<String> {
    // Running maxima of (timestamp, start byte, assistant index).
    let mut maxima: Vec<(String, usize, usize)> = Vec::new();
    let (mut by_lines, mut by_bytes) = ((0, None), (0, None));
    let (mut pos, mut idx) = (0, 0);
    for l in text.split_inclusive('\n') {
        let (start, end) = (pos, pos + l.len());
        pos = end;
        let Ok(v) = serde_json::from_str::<serde_json::Value>(l) else {
            continue;
        };
        if v["type"] != "assistant" {
            continue;
        }
        let Some(ts) = v["timestamp"].as_str().map(|t| t[..t.len().min(19)].to_owned()) else {
            continue;
        };
        idx += 1;
        let k = maxima.partition_point(|(t, _, _)| *t <= ts);
        if let Some((a_ts, a_start, a_idx)) = maxima.get(k) {
            if idx - a_idx > by_lines.0 {
                by_lines = (idx - a_idx, Some(a_ts.clone()));
            }
            if end - a_start > by_bytes.0 {
                by_bytes = (end - a_start, Some(a_ts.clone()));
            }
        }
        if maxima.last().is_none_or(|(t, _, _)| ts > *t) {
            maxima.push((ts, start, idx));
        }
    }
    let mut v: Vec<String> = [by_lines.1, by_bytes.1].into_iter().flatten().collect();
    v.dedup();
    v
}

fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let mut margin = MARGIN;
    if args.first().is_some_and(|a| a == "--margin") {
        margin = Margin {
            lines: args[1].parse().expect("lines"),
            bytes: args[2].parse().expect("bytes"),
        };
        args.drain(..3);
    }
    let mut paths = Vec::new();
    for d in &args {
        walk(Path::new(d), &mut paths);
    }
    let ckpt = std::env::temp_dir().join(format!("gw-tail-differential-{}", std::process::id()));
    let (mut files, mut not_utf8, mut errors, mut cases, mut resumed) = (0u64, 0u64, 0u64, 0u64, 0u64);
    let (mut back, mut halves, mut real) = (Tally::default(), Tally::default(), Tally::default());
    for (n, p) in paths.iter().enumerate() {
        files += 1;
        let Ok(bytes) = std::fs::read(p) else {
            errors += 1;
            continue;
        };
        let Ok(text) = std::str::from_utf8(&bytes) else {
            // The full read refuses such a file (read_to_string) and counts nothing; the tail read counts.
            not_utf8 += 1;
            continue;
        };
        let len = bytes.len() as u64;
        let cut = bytes[..bytes.len() / 2]
            .iter()
            .rposition(|&b| b == b'\n')
            .map_or(0, |i| i as u64 + 1);
        for since in sinces(text) {
            cases += 1;
            let s = since.as_deref();
            let key = s.map(tail::since_key);
            let full = count_tool_uses(text, s);
            let b = tail::scan_back(&mut Cursor::new(&bytes), len, key, margin).expect("in memory");
            back.add(full, b.total());
            let pre = tail::scan_back(&mut Cursor::new(&bytes[..cut as usize]), cut, key, margin).expect("in memory");
            let post = tail::scan_forward(&mut Cursor::new(&bytes), pre.resume_at, len, key).expect("in memory");
            let mut both = pre.uses;
            tail::add(&mut both, post.total());
            halves.add(full, both);
            let session = format!("diff-{n}");
            for _ in 0..2 {
                match tail::count(p, s, &session, Some(&ckpt), margin) {
                    Ok((u, how)) => {
                        resumed += u64::from(how == Route::Resumed);
                        real.add(full, u);
                    }
                    Err(_) => errors += 1,
                }
            }
        }
    }
    let _ = std::fs::remove_dir_all(&ckpt);
    println!("margin                {} lines, {} bytes", margin.lines, margin.bytes);
    println!("files                 {files}");
    println!("files not UTF-8       {not_utf8}");
    println!("read errors           {errors}");
    println!("cases (file x since)  {cases}");
    for (name, t) in [
        ("backward scan", &back),
        ("checkpoint halves", &halves),
        ("count() x2", &real),
    ] {
        println!(
            "{name:<21} equal {}  superset {}  UNDER {}",
            t.equal, t.superset, t.under
        );
    }
    println!("count() resumed       {resumed}");
}
