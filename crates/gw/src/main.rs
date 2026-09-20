//! gw: the Groundworks command line (the Rust candidate the differential gate runs).
//!
//! `config spec <instance>` and `config check <fixture.json>` are port-01's surface: the differential
//! harness's `--config` mode drives both (`groundworks/harness/differential.py`).

use std::path::Path;
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        Some("--oracle") => {
            println!("{} {}", gw_core::ORACLE_TAG, gw_core::ORACLE_COMMIT);
            ExitCode::SUCCESS
        }
        Some("config") => config_command(args.collect()),
        Some(other) => {
            eprintln!("gw: unknown argument {other}");
            ExitCode::from(2)
        }
        None => {
            println!("gw: try --oracle, or config spec/check");
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
                    eprintln!("ConfigError: {e}");
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
                    println!("REFUSE  ->  ConfigError: {e}");
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
