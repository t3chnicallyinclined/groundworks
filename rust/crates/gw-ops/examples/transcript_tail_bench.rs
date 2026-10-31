//! In-process cost of the Stop hook's count, without the process spawn and the store round trips that
//! dominate the hook's wall time. Run by hand, never in CI:
//! `cargo run --release -p gw-ops --example transcript_tail_bench -- <transcript> <since|-> [reps]`
//!
//! Prints timings and byte counts only. Rows: the full read (`read_to_string` + `count_tool_uses`, the
//! old path), the backward scan (no checkpoint), and a resume from a checkpoint with nothing new.

use std::path::Path;
use std::time::Instant;

use gw_ops::hook::count_tool_uses;
use gw_ops::transcript_tail::{self as tail, MARGIN, Route};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let path = Path::new(&args[0]);
    let since = (args[1] != "-").then(|| args[1].as_str());
    let reps: usize = args.get(2).map_or(3, |r| r.parse().expect("reps"));
    let ckpt = std::env::temp_dir().join(format!("gw-tail-bench-{}", std::process::id()));
    let ms = |t: Instant| t.elapsed().as_secs_f64() * 1e3;
    for rep in 1..=reps {
        let t = Instant::now();
        let text = std::fs::read_to_string(path).expect("readable");
        let full = count_tool_uses(&text, since);
        let full_ms = ms(t);
        drop(text);
        let _ = std::fs::remove_dir_all(&ckpt);
        let t = Instant::now();
        let (scan, how) = tail::count(path, since, "bench", Some(&ckpt), MARGIN).expect("readable");
        let scan_ms = ms(t);
        assert_eq!(how, Route::Scanned);
        let t = Instant::now();
        let (resumed, how) = tail::count(path, since, "bench", Some(&ckpt), MARGIN).expect("readable");
        let resume_ms = ms(t);
        assert_eq!(how, Route::Resumed);
        let len = std::fs::metadata(path).map_or(0, |m| m.len());
        let mut f = std::fs::File::open(path).expect("readable");
        let read = tail::scan_back(&mut f, len, since.map(tail::since_key), MARGIN)
            .expect("readable")
            .bytes_read;
        println!(
            "rep {rep}  full {full_ms:8.1} ms  scan {scan_ms:7.1} ms ({read} B read)  resume {resume_ms:6.1} ms  same {}",
            full == scan && scan == resumed
        );
    }
    let _ = std::fs::remove_dir_all(&ckpt);
}
