//! A scratch SurrealDB a program starts for itself: a free loopback port, rocksdb in a temporary
//! folder (like the real store), root login through the environment, stopped by its own handle and
//! never by name. The restore drill imports an export into one so nothing it does can reach the live
//! store; the test kit's throwaway server is one of these with test passwords.

use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use gw_core::db::Db;

pub struct Scratch {
    child: Child,
    port: u16,
    data: PathBuf,
    root_pass: String,
}

fn other(text: String) -> std::io::Error {
    std::io::Error::other(text)
}

impl Scratch {
    /// Start one with `binary`, answering within `wait`. Err (nothing left running) when the process
    /// exits early or never answers.
    pub fn start(binary: &Path, root_pass: &str, wait: Duration) -> std::io::Result<Self> {
        let port = TcpListener::bind("127.0.0.1:0")?.local_addr()?.port();
        let data = std::env::temp_dir().join(format!("gw-scratch-{}-{port}", std::process::id()));
        let _ = std::fs::remove_dir_all(&data);
        let child = Command::new(binary)
            .args(["start", "--bind", &format!("127.0.0.1:{port}"), "--log", "warn"])
            .arg(format!("rocksdb:{}", data.display()))
            .env("SURREAL_USER", "root")
            .env("SURREAL_PASS", root_pass)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()?;
        let mut me = Scratch {
            child,
            port,
            data,
            root_pass: root_pass.to_owned(),
        };
        let deadline = Instant::now() + wait;
        loop {
            if let Some(status) = me.child.try_wait()? {
                return Err(other(format!("surreal on port {port} exited early: {status}")));
            }
            if crate::healthy(&me.url()) {
                std::thread::sleep(Duration::from_millis(300));
                if matches!(me.child.try_wait(), Ok(None)) {
                    return Ok(me);
                }
            }
            if Instant::now() >= deadline {
                return Err(other(format!(
                    "surreal did not become healthy on port {port} within {}s",
                    wait.as_secs()
                )));
            }
            std::thread::sleep(Duration::from_millis(150));
        }
    }

    /// `http://127.0.0.1:<port>/sql`.
    pub fn url(&self) -> String {
        format!("{}/sql", self.base())
    }

    /// `http://127.0.0.1:<port>`, for the `surreal` command's `--endpoint`.
    pub fn base(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    pub fn root_pass(&self) -> &str {
        &self.root_pass
    }

    /// A root connection to `ns` (which need not exist yet).
    pub fn root(&self, ns: &str) -> crate::Result<Db> {
        crate::connect_root(&self.url(), ns, "root", &self.root_pass)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_dir_all(&self.data);
    }
}
