//! Test support (feature `testkit`): a throwaway in-memory SurrealDB that a test owns and stops by
//! its own handle, never by name. If no SurrealDB binary is found it PANICS ("NOT EXECUTED"), so a
//! test that could not prove anything fails instead of passing.

use std::time::Duration;

use gw_core::db::Db;

use crate::{GLOBAL_NS, connect_member, connect_root, open_namespace, provision};

pub const ROOT_PASS: &str = "testkit-root-password-0123456789";
pub const AGENT_PASS: &str = "testkit-agent-password-0123456789";
pub const OWNER_PASS: &str = "testkit-owner-password-0123456789";

/// A [`Scratch`](crate::scratch::Scratch) with the test kit's passwords: rocksdb in a temporary
/// folder, like the real store (the in-memory engine differs; measured 2026-10-25: an analyzer
/// defined earlier in the same transaction is visible to an index on memory, not on rocksdb).
pub struct Throwaway {
    inner: crate::scratch::Scratch,
}

impl Throwaway {
    pub fn start() -> Self {
        let binary = crate::find_surreal().unwrap_or_else(|| {
            panic!(
                "NOT EXECUTED (fails this test, not a pass): SurrealDB binary not found. Install it or set \
                 GROUNDWORKS_SURREAL; this test cannot prove anything without a real server."
            )
        });
        let inner = crate::scratch::Scratch::start(&binary, ROOT_PASS, Duration::from_secs(20))
            .unwrap_or_else(|e| panic!("NOT EXECUTED: {e}"));
        Throwaway { inner }
    }

    pub fn url(&self) -> String {
        self.inner.url()
    }

    /// Migrate `ns` and provision `owner1` (owner) and `agent1` (agent) in it.
    pub fn namespace(&self, ns: &str) {
        let root = self.root(ns);
        open_namespace(&root, ns).unwrap();
        provision(&root, "owner1", "owner", OWNER_PASS).unwrap();
        provision(&root, "agent1", "agent", AGENT_PASS).unwrap();
    }

    /// `global`, `proj_a` and `proj_b`, each migrated with both members.
    pub fn estate(&self) {
        for ns in [GLOBAL_NS, "proj_a", "proj_b"] {
            self.namespace(ns);
        }
    }

    pub fn root(&self, ns: &str) -> Db {
        connect_root(&self.url(), ns, "root", ROOT_PASS).unwrap()
    }

    pub fn agent(&self, ns: &str) -> Db {
        connect_member(&self.url(), ns, "agent1", AGENT_PASS).unwrap()
    }

    pub fn owner(&self, ns: &str) -> Db {
        connect_member(&self.url(), ns, "owner1", OWNER_PASS).unwrap()
    }
}
