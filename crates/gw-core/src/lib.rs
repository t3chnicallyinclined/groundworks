//! gw-core: the Groundworks engine, ported from the Python oracle (tag `v0.2.0`, commit 94ad5dc).
//!
//! EMPTY BY DESIGN (impl-10). This ticket builds the owner's gate BEFORE any engine code exists:
//! `groundworks/harness/differential.py` pins the oracle and diffs a candidate against it byte for
//! byte (goldens for every SurQL emitter, plus a seeded single-agent MCP transcript). Engine code
//! lands from impl-11 / port-01 onward, one unit at a time, each one green against that gate.
//!
//! Rules fixed for this crate in `groundworks/docs/RUST_PORT.md`:
//! * `unsafe_code = "forbid"` via `lints.workspace = true` (workspace root).
//! * one `thiserror` enum per crate, no `unwrap` outside tests, every dependency justified.
//! * the datastore is SurrealDB over HTTP `/sql` with a blocking client, not the async crate.
//! * gw holds NO ticket, claim or lane code: that is `ops-core`, reached through `gw-board-store`.

/// The oracle this crate is ported from. The harness refuses to compare against anything else
/// (`harness/differential.py --ensure-oracle`, `git describe --exact-match` must say this).
pub const ORACLE_TAG: &str = "v0.2.0";

/// The oracle commit `v0.2.0` points at (`_tickets` Log 2026-09-19 17:44).
pub const ORACLE_COMMIT: &str = "94ad5dca0882121c71245dd644fe533743788655";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_oracle_is_pinned_to_one_tag_and_one_commit() {
        // The gate's whole value is that "the oracle" is never "whatever main is".
        assert_eq!(ORACLE_TAG, "v0.2.0");
        assert_eq!(ORACLE_COMMIT.len(), 40);
        assert!(ORACLE_COMMIT.chars().all(|c| c.is_ascii_hexdigit()));
    }
}
