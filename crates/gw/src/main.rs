//! gw: the Groundworks command line (the Rust candidate the differential gate runs).
//!
//! `config spec <instance>` and `config check <fixture.json>` are port-01's surface: the differential
//! harness's `--config` mode drives both (`groundworks/harness/differential.py`).
//!
//! `--split-statements` is port-04's own entry point: it reads a whole script from stdin, splits it
//! exactly the way `gw_core::db::split_statements` (the Rust port of `db.py::_split_statements`)
//! does, and writes the pieces to stdout as a JSON array of strings -- one array element per
//! statement, in order, never trimmed -- so `harness/differential.py --split` can diff it byte for
//! byte against the oracle's own `_split_statements` on the same input.
//!
//! `corpus extract` and `corpus freeze` are the science-corpus lane's surface (their own gates:
//! `cargo test -p gw --test science_corpus*`). They predate the ExitCode return here, so each arm
//! ends in an explicit ExitCode instead of `std::process::exit`.

mod science_corpus_extract;
mod science_corpus_freeze;

use std::io::Read;
use std::path::Path;
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    let first = args.next();
    if first.as_deref() == Some("corpus") {
        return corpus_command(args.next().as_deref(), args.collect());
    }
    match first.as_deref() {
        Some("--oracle") => {
            println!("{} {}", gw_core::ORACLE_TAG, gw_core::ORACLE_COMMIT);
            ExitCode::SUCCESS
        }
        Some("--split-statements") => {
            let mut input = String::new();
            if let Err(e) = std::io::stdin().read_to_string(&mut input) {
                eprintln!("gw: could not read stdin: {e}");
                return ExitCode::from(2);
            }
            let parts = gw_core::db::split_statements(&input);
            let json = serde_json::to_string(&parts).expect("Vec<String> always serializes");
            println!("{json}");
            ExitCode::SUCCESS
        }
        Some("config") => config_command(args.collect()),
        Some(other) => {
            eprintln!("gw: unknown argument {other}");
            ExitCode::from(2)
        }
        None => {
            println!("gw: try --oracle, --split-statements, config spec/check, or corpus extract/freeze");
            ExitCode::SUCCESS
        }
    }
}

/// `gw corpus extract|freeze ...` -- the science-corpus lane's two subcommands, kept exactly as
/// they were written on master; only the failure path changed (ExitCode::from(2) in place of
/// std::process::exit(2), so main has one way out).
fn corpus_command(sub: Option<&str>, rest: Vec<String>) -> ExitCode {
    let mut args = rest.into_iter();
    match sub {
        Some("extract") => {
            let (mut manifest, mut output, mut captured_at) = (None, None, None);
            let (mut pdfinfo, mut pdftotext) = (None, None);
            while let Some(argument) = args.next() {
                match argument.as_str() {
                    "--manifest" => manifest = args.next(),
                    "--out" => output = args.next(),
                    "--captured-at" => captured_at = args.next(),
                    "--pdfinfo" | "--pdfinfo-executable" => pdfinfo = args.next(),
                    "--pdftotext" | "--pdftotext-executable" => pdftotext = args.next(),
                    _ => {
                        eprintln!("gw: unknown argument {argument}");
                        return ExitCode::from(2);
                    }
                }
            }
            let result = if let (Some(pi), Some(pt)) = (pdfinfo, pdftotext) {
                science_corpus_extract::extract_native(
                    Path::new(manifest.as_deref().unwrap_or("")),
                    Path::new(output.as_deref().unwrap_or("")),
                    captured_at.as_deref().unwrap_or(""),
                    Path::new(&pi),
                    Path::new(&pt),
                )
            } else {
                science_corpus_extract::extract(
                    Path::new(manifest.as_deref().unwrap_or("")),
                    Path::new(output.as_deref().unwrap_or("")),
                    captured_at.as_deref().unwrap_or(""),
                )
            };
            if let Err(error) = result {
                eprintln!("gw: {error}");
                return ExitCode::from(2);
            }
            ExitCode::SUCCESS
        }
        Some("freeze") => {
            let (mut collection, mut source, mut out) = (None, None, None);
            let mut capture_time = String::from("1970-01-01T00:00:00Z");
            let mut includes = Vec::new();
            while let Some(a) = args.next() {
                match a.as_str() {
                    "--collection" => collection = args.next(),
                    "--source" => source = args.next(),
                    "--out" => out = args.next(),
                    "--capture-time" | "--captured-at" => {
                        capture_time = args.next().unwrap_or_default()
                    }
                    "--include" => includes.push(args.next().unwrap_or_default()),
                    _ => {
                        eprintln!("gw: unknown argument {a}");
                        return ExitCode::from(2);
                    }
                }
            }
            if let Err(e) = science_corpus_freeze::freeze(
                collection.as_deref().unwrap_or(""),
                Path::new(source.as_deref().unwrap_or("")),
                Path::new(out.as_deref().unwrap_or("")),
                &capture_time,
                &includes,
            ) {
                eprintln!("gw: {e}");
                return ExitCode::from(2);
            }
            ExitCode::SUCCESS
        }
        other => {
            eprintln!("gw corpus <extract|freeze> ... (got {other:?})");
            ExitCode::from(2)
        }
    }
}

fn config_command(rest: Vec<String>) -> ExitCode {
    match rest.first().map(String::as_str) {
        Some("spec") => {
            let Some(path) = rest.get(1) else {
                eprintln!("gw config spec <instance.yaml|instance.json>");
                return ExitCode::from(2);
            };
            match gw_core::config::load_from_path(Path::new(path)) {
                Ok(inst) => {
                    let spec = gw_core::config::spec(&inst);
                    println!("{}", gw_core::config::json_sorted::dumps_sorted_indent2(&spec));
                    ExitCode::SUCCESS
                }
                Err(e) => {
                    eprintln!("{}: {e}", e.kind);
                    ExitCode::FAILURE
                }
            }
        }
        Some("check") => {
            let Some(path) = rest.get(1) else {
                eprintln!("gw config check <fixture.json>");
                return ExitCode::from(2);
            };
            let text = match std::fs::read_to_string(path) {
                Ok(t) => t,
                Err(e) => {
                    eprintln!("cannot read {path}: {e}");
                    return ExitCode::from(2);
                }
            };
            let data: serde_json::Value = match serde_json::from_str(&text) {
                Ok(d) => d,
                Err(e) => {
                    eprintln!("{path} is not valid JSON: {e}");
                    return ExitCode::from(2);
                }
            };
            match gw_core::config::load_from_mapping(data) {
                Ok(_) => {
                    println!("ACCEPT");
                    ExitCode::SUCCESS
                }
                Err(e) => {
                    println!("REFUSE  ->  {}: {e}", e.kind);
                    ExitCode::SUCCESS
                }
            }
        }
        _ => {
            eprintln!("gw config <spec|check> ...");
            ExitCode::from(2)
        }
    }
}
