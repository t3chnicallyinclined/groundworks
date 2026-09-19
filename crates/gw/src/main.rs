//! gw: the Groundworks command line (the Rust candidate the differential gate runs).
//!
//! EMPTY BY DESIGN (impl-10). Today it can only say which oracle it is being held to; the harness
//! (`groundworks/harness/differential.py --mcp --candidate <bin>`) will drive the real binary once
//! the port begins. `--oracle` exists now so the gate has a candidate entry point to point at.

fn main() {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        Some("--oracle") => println!("{} {}", gw_core::ORACLE_TAG, gw_core::ORACLE_COMMIT),
        Some(other) => {
            eprintln!("gw: unknown argument {other}; the port has not started (impl-10)");
            std::process::exit(2);
        }
        None => println!("gw: no engine yet (impl-10 builds the gate first); try --oracle"),
    }
}
