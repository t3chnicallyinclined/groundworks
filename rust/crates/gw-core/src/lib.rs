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

/// port-01 (this ticket): the instance config loader, ported from `groundworks/config.py`.
pub mod cbor;
pub mod config;

/// The oracle this crate is ported from. The harness refuses to compare against anything else
/// (`harness/differential.py --ensure-oracle`, `git describe --exact-match` must say this).
pub const ORACLE_TAG: &str = "v0.2.0";

/// The oracle commit `v0.2.0` points at (`_tickets` Log 2026-09-19 17:44).
pub const ORACLE_COMMIT: &str = "94ad5dca0882121c71245dd644fe533743788655";

/// decide: typed decisions over units. NOT a port -- it has no counterpart in the oracle,
/// so its own tests are its gate. See the module header.
pub mod decide;

pub mod db;
pub use db::{Db, DbError};

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn the_oracle_is_pinned_to_one_tag_and_one_commit() {
        // The gate's whole value is that "the oracle" is never "whatever main is".
        assert_eq!(ORACLE_TAG, "v0.2.0");
        assert_eq!(ORACLE_COMMIT.len(), 40);
        assert!(ORACLE_COMMIT.chars().all(|c| c.is_ascii_hexdigit()));
    }

    /// Minor finding (fix round, review of b2b7f99): this crate's only test asserted a constant
    /// against its own literal, which cannot drift. The invariant that CAN drift is this constant
    /// duplicated in `harness/differential.py` (`ORACLE_COMMIT = "..."`, next to `ORACLE_TAG = "..."`)
    /// -- nothing enforced the two stayed equal. This reads the Python source (by path, relative to
    /// this crate via CARGO_MANIFEST_DIR, so it works from any cwd) and checks both constants by a
    /// plain string search, without needing a Python interpreter at test time.
    #[test]
    fn oracle_tag_and_commit_match_the_python_harness() {
        let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
        let harness = manifest_dir
            .join("..")
            .join("..")
            .join("harness")
            .join("differential.py");
        let source = std::fs::read_to_string(&harness)
            .unwrap_or_else(|e| panic!("could not read {}: {e}", harness.display()));

        let extract = |needle: &str| -> String {
            let line = source
                .lines()
                .find(|l| l.trim_start().starts_with(needle))
                .unwrap_or_else(|| {
                    panic!("no line starting with {needle:?} in {}", harness.display())
                });
            let (_, rest) = line.split_once('"').expect("no opening quote");
            let (value, _) = rest.split_once('"').expect("no closing quote");
            value.to_string()
        };

        assert_eq!(
            extract("ORACLE_TAG = "),
            ORACLE_TAG,
            "differential.py's ORACLE_TAG drifted from gw-core's ORACLE_TAG"
        );
        assert_eq!(
            extract("ORACLE_COMMIT = "),
            ORACLE_COMMIT,
            "differential.py's ORACLE_COMMIT drifted from gw-core's ORACLE_COMMIT"
        );
    }
}
