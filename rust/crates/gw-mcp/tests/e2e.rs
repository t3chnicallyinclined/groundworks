//! End to end: the real `groundworks` binary, as an MCP server, against a throwaway store,
//! through every rule an agent relies on (see src/selftest.rs). Fails as NOT EXECUTED without a
//! SurrealDB binary.

#[test]
fn the_real_server_passes_every_selftest_check() {
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_groundworks"))
        .arg("selftest")
        .env_remove("GROUNDWORKS_HOME")
        .output()
        .expect("running groundworks selftest");
    let report = String::from_utf8_lossy(&out.stdout);
    let errors = String::from_utf8_lossy(&out.stderr);
    assert!(out.status.success(), "selftest failed:\n{report}\n{errors}");
    assert!(report.contains("CHECKS PASSED"), "{report}");
}
