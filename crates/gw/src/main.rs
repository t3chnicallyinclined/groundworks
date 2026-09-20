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

use std::io::Read;
use std::path::Path;
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
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
            println!("gw: try --oracle, --split-statements, or config spec/check");
            ExitCode::SUCCESS
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
