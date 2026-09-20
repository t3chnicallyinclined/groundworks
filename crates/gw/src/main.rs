//! gw: the Groundworks command line (the Rust candidate the differential gate runs).
//!
//! Mostly EMPTY BY DESIGN still (impl-10): the front door itself (`--mcp --candidate <bin>`) lands at
//! port-09/impl-12. `--oracle` exists so the gate has a candidate entry point to point at.
//!
//! `--split-statements` is port-04's own entry point: it reads a whole script from stdin, splits it
//! exactly the way `gw_core::db::split_statements` (the Rust port of `db.py::_split_statements`)
//! does, and writes the pieces to stdout as a JSON array of strings -- one array element per
//! statement, in order, never trimmed -- so `harness/differential.py --split` can diff it byte for
//! byte against the oracle's own `_split_statements` on the same input.

use std::io::Read;

fn main() {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        Some("--oracle") => println!("{} {}", gw_core::ORACLE_TAG, gw_core::ORACLE_COMMIT),
        Some("--split-statements") => {
            let mut input = String::new();
            if let Err(e) = std::io::stdin().read_to_string(&mut input) {
                eprintln!("gw: could not read stdin: {e}");
                std::process::exit(2);
            }
            let parts = gw_core::db::split_statements(&input);
            let json = serde_json::to_string(&parts).expect("Vec<String> always serializes");
            println!("{json}");
        }
        Some(other) => {
            eprintln!("gw: unknown argument {other}; the port has not started (impl-10)");
            std::process::exit(2);
        }
        None => println!("gw: no engine yet (impl-10 builds the gate first); try --oracle"),
    }
}
