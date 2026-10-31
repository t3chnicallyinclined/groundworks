//! Census of Claude Code transcripts for the Stop hook's tail read (transcript_tail.rs). Run by hand,
//! never in CI: `cargo run --release -p gw-ops --example transcript_census -- <dir>...`
//!
//! Prints COUNTS ONLY: no file name, no path, no line content, not even in an error (one of the
//! folders it reads is a legal matter). Every `*.jsonl` under each directory is read.
//!
//! What it measures, to set `transcript_tail::MARGIN`:
//! - assistant lines with no timestamp (the tail read counts them only where it reaches);
//! - out-of-order lines: line B whose timestamp (to the second) is older than some earlier line A's;
//!   for the widest such pair, the distance from A's start to B's end in bytes and in assistant lines.
//!   The backward scan is a superset of the full read as long as that distance is below the margin in
//!   bytes or in lines.

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

use serde_json::Value;

#[derive(Default)]
struct Inv {
    /// Lines with an earlier line holding a later timestamp.
    lines: u64,
    max_bytes: u64,
    max_lines: u64,
    /// By how much the earlier line is newer: <= 1 s, <= 10 s, <= 60 s, <= 1 h, more.
    delta: [u64; 5],
    max_delta_s: i64,
    /// The widest gap among pairs more than 10 s apart, in bytes and in lines.
    wide_bytes_10s: u64,
    wide_lines_10s: u64,
}

fn secs(ts: &str) -> Option<i64> {
    chrono::NaiveDateTime::parse_from_str(ts, "%Y-%m-%dT%H:%M:%S")
        .ok()
        .map(|t| t.and_utc().timestamp())
}

/// Running maxima: (timestamp, start byte, index) each time the maximum rises.
#[derive(Default)]
struct Maxima(Vec<(String, u64, u64)>);

impl Maxima {
    /// Record line (`ts`, `start`, `end`, `idx`); fold any inversion into `inv`.
    fn see(&mut self, ts: &str, start: u64, end: u64, idx: u64, inv: &mut Inv) {
        let k = self.0.partition_point(|(t, _, _)| t.as_str() <= ts);
        if let Some((_, a_start, a_idx)) = self.0.get(k) {
            inv.lines += 1;
            inv.max_bytes = inv.max_bytes.max(end - a_start);
            inv.max_lines = inv.max_lines.max(idx - a_idx);
            // The newest earlier line: the largest delta for this line.
            let newest = &self.0.last().expect("non-empty").0;
            let d = secs(newest).zip(secs(ts)).map_or(i64::MAX, |(a, b)| a - b);
            let bucket = match d {
                ..=1 => 0,
                2..=10 => 1,
                11..=60 => 2,
                61..=3600 => 3,
                _ => 4,
            };
            inv.delta[bucket] += 1;
            inv.max_delta_s = inv.max_delta_s.max(d);
            // The earliest earlier line more than 10 s newer than this one.
            let k10 = self
                .0
                .partition_point(|(t, _, _)| secs(t).zip(secs(ts)).is_none_or(|(a, b)| a - b <= 10));
            if let Some((_, s10, i10)) = self.0.get(k10) {
                inv.wide_bytes_10s = inv.wide_bytes_10s.max(end - s10);
                inv.wide_lines_10s = inv.wide_lines_10s.max(idx - i10);
            }
        }
        if self.0.last().is_none_or(|(t, _, _)| ts > t.as_str()) {
            self.0.push((ts.to_owned(), start, idx));
        }
    }
}

#[derive(Default)]
struct Census {
    files: u64,
    unreadable: u64,
    bytes: u64,
    lines: u64,
    not_json: u64,
    assistant: u64,
    assistant_untimed: u64,
    files_with_untimed_assistant: u64,
    assistant_inv: Inv,
    all_inv: Inv,
    files_with_assistant_inv: u64,
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

fn file(path: &Path, c: &mut Census) -> std::io::Result<()> {
    let mut r = BufReader::with_capacity(1 << 20, std::fs::File::open(path)?);
    let mut buf = Vec::new();
    let (mut pos, mut a_idx, mut l_idx) = (0u64, 0u64, 0u64);
    let (mut am, mut lm) = (Maxima::default(), Maxima::default());
    let (mut untimed, inv_before) = (0u64, c.assistant_inv.lines);
    loop {
        buf.clear();
        let n = r.read_until(b'\n', &mut buf)? as u64;
        if n == 0 {
            break;
        }
        let (start, end) = (pos, pos + n);
        pos = end;
        c.lines += 1;
        let Ok(row) = serde_json::from_slice::<Value>(&buf) else {
            c.not_json += 1;
            continue;
        };
        let ts = row["timestamp"].as_str().map(|t| &t[..t.len().min(19)]);
        if let Some(ts) = ts {
            l_idx += 1;
            lm.see(ts, start, end, l_idx, &mut c.all_inv);
        }
        if row["type"] == "assistant" {
            c.assistant += 1;
            match ts {
                Some(ts) => {
                    a_idx += 1;
                    am.see(ts, start, end, a_idx, &mut c.assistant_inv);
                }
                None => untimed += 1,
            }
        }
    }
    c.bytes += pos;
    c.assistant_untimed += untimed;
    c.files_with_untimed_assistant += u64::from(untimed > 0);
    c.files_with_assistant_inv += u64::from(c.assistant_inv.lines > inv_before);
    Ok(())
}

fn main() {
    let mut paths = Vec::new();
    for d in std::env::args().skip(1) {
        walk(Path::new(&d), &mut paths);
    }
    let mut c = Census::default();
    for p in &paths {
        c.files += 1;
        if file(p, &mut c).is_err() {
            c.unreadable += 1;
        }
    }
    println!("files                         {}", c.files);
    println!("files unreadable              {}", c.unreadable);
    println!("bytes                         {}", c.bytes);
    println!("lines                         {}", c.lines);
    println!("lines not JSON                {}", c.not_json);
    println!("assistant lines               {}", c.assistant);
    println!("assistant lines, no timestamp {}", c.assistant_untimed);
    println!("files with such lines         {}", c.files_with_untimed_assistant);
    println!("assistant lines out of order  {}", c.assistant_inv.lines);
    println!("files with such lines         {}", c.files_with_assistant_inv);
    println!("  widest gap, bytes           {}", c.assistant_inv.max_bytes);
    println!("  widest gap, assistant lines {}", c.assistant_inv.max_lines);
    let a = &c.assistant_inv;
    println!(
        "  newer by <=1s {} <=10s {} <=60s {} <=1h {} more {}",
        a.delta[0], a.delta[1], a.delta[2], a.delta[3], a.delta[4]
    );
    println!("  largest delta, seconds      {}", a.max_delta_s);
    println!("  widest gap >10s apart, bytes {}", a.wide_bytes_10s);
    println!("  widest gap >10s apart, lines {}", a.wide_lines_10s);
    println!("timestamped lines out of order {}", c.all_inv.lines);
    println!("  widest gap, bytes           {}", c.all_inv.max_bytes);
    println!("  widest gap, lines           {}", c.all_inv.max_lines);
}
