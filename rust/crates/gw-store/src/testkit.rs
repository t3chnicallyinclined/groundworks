//! Test support (feature `testkit`): a throwaway in-memory SurrealDB that a test owns and stops by
//! its own handle, never by name. If no SurrealDB binary is found it PANICS ("NOT EXECUTED"), so a
//! test that could not prove anything fails instead of passing.

use std::net::TcpListener;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use gw_core::db::Db;

use crate::{GLOBAL_NS, connect_member, connect_root, open_namespace, provision};

pub const ROOT_PASS: &str = "testkit-root-password-0123456789";
pub const AGENT_PASS: &str = "testkit-agent-password-0123456789";
pub const OWNER_PASS: &str = "testkit-owner-password-0123456789";

/// Backed by rocksdb in a temporary folder, like the real store: the in-memory engine differs (measured
/// 2026-10-25: an analyzer defined earlier in the same transaction is visible to an index on memory, not
/// on rocksdb).
pub struct Throwaway {
    child: Child,
    port: u16,
    data: std::path::PathBuf,
}

impl Throwaway {
    pub fn start() -> Self {
        let binary = crate::find_surreal().unwrap_or_else(|| {
            panic!(
                "NOT EXECUTED (fails this test, not a pass): SurrealDB binary not found. Install it or set \
                 GROUNDWORKS_SURREAL; this test cannot prove anything without a real server."
            )
        });
        let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        let data = std::env::temp_dir().join(format!("gw-throwaway-{}-{port}", std::process::id()));
        let _ = std::fs::remove_dir_all(&data);
        let child = Command::new(binary)
            .args([
                "start",
                "--bind",
                &format!("127.0.0.1:{port}"),
                "--user",
                "root",
                "--pass",
                ROOT_PASS,
            ])
            .arg(format!("rocksdb:{}", data.display()))
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("failed to spawn surreal");
        let mut me = Throwaway { child, port, data };
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            if let Ok(Some(status)) = me.child.try_wait() {
                panic!("surreal on port {port} exited early: {status:?}");
            }
            if crate::healthy(&me.url()) {
                std::thread::sleep(Duration::from_millis(300));
                if matches!(me.child.try_wait(), Ok(None)) {
                    return me;
                }
            }
            assert!(
                Instant::now() < deadline,
                "NOT EXECUTED: surreal did not become healthy on port {port}"
            );
            std::thread::sleep(Duration::from_millis(150));
        }
    }

    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}/sql", self.port)
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

impl Drop for Throwaway {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_dir_all(&self.data);
    }
}
