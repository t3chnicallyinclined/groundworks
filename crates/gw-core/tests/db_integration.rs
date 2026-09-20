//! Integration tests for `gw_core::db` against a throwaway SurrealDB, started and stopped BY PID
//! (never by name), plus one hand-rolled fake HTTP server for the version RED arm.
//!
//! Covers the `2026-09-18-groundworks-port-04-db-client` ticket's "Done when" items that need a real
//! server rather than a pure function: a denied write under record auth (both `sql` and `try_sql`
//! arms), the version check against a server lying about its series, and a mid-run server restart
//! reconnecting on the SAME `Db` (ONE connection, not a pool -- that is impl-01's job).
//!
//! `cargo gw-test` runs this file. If no SurrealDB binary is found (`GROUNDWORKS_SURREAL`, `PATH`, or
//! the per-user Windows install location -- the same order as the oracle's `surreal_bin.find()`), the
//! surreal-backed tests FAIL LOUDLY (`panic!`, not a silent early `return`) rather than reading as a
//! pass -- `differential.py`'s own convention is that a missing dependency is a `NotExecuted`, which
//! exits nonzero, never a green gate (`2026-09-18-groundworks-port-04-db-client`, blocking finding
//! B4: `cargo test -p gw-core --test db_integration` with no surreal on PATH used to report "3
//! passed" in 0.03s, silently skipping both the denied-write and the reconnect done-when items). The
//! fake-server test needs no external binary and always runs.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use gw_core::db::{Db, DbError};

// ---------------------------------------------------------------------------------------------
// finding and starting a throwaway SurrealDB -- mirrors groundworks/surreal_bin.py exactly enough
// for these tests: same env var, same search order, same "stop by PID, never by name" rule.
// ---------------------------------------------------------------------------------------------

fn find_surreal() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("GROUNDWORKS_SURREAL") {
        let path = PathBuf::from(p);
        if path.exists() {
            return Some(path);
        }
    }
    let exe_name = if cfg!(windows) { "surreal.exe" } else { "surreal" };
    if let Some(path_var) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&path_var) {
            let candidate = dir.join(exe_name);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    if let Some(home) = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")) {
        let candidate = PathBuf::from(home).join("AppData").join("Local").join("SurrealDB").join("surreal.exe");
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

fn free_port() -> u16 {
    // Bind then drop: the OS will not hand this exact port to anyone else for a little while, and
    // it is never one of a live database's fixed ports (mirrors surreal_bin.free_port's reasoning).
    TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
}

/// A throwaway `surreal start ... memory` this test owns. Stopped by its own `Child` handle in
/// `Drop` -- never by name, and never left running if a test panics.
struct Throwaway {
    child: Child,
    port: u16,
}

impl Throwaway {
    fn start(binary: &std::path::Path, port: u16) -> Self {
        let child = Command::new(binary)
            .args(["start", "--bind", &format!("127.0.0.1:{port}"), "--user", "root", "--pass", "root", "memory"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("failed to spawn surreal");
        let mut me = Throwaway { child, port };
        me.wait_healthy();
        me
    }

    fn url(&self) -> String {
        format!("http://127.0.0.1:{}/sql", self.port)
    }

    fn wait_healthy(&mut self) {
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            if let Ok(Some(status)) = self.child.try_wait() {
                panic!("surreal on port {} exited early: {status:?}", self.port);
            }
            let health = format!("http://127.0.0.1:{}/health", self.port);
            if ureq::get(&health).timeout(Duration::from_millis(500)).call().is_ok() {
                std::thread::sleep(Duration::from_millis(300)); // settle, like surreal_bin.SETTLE
                if matches!(self.child.try_wait(), Ok(None)) {
                    return;
                }
            }
            if Instant::now() > deadline {
                panic!("NOT EXECUTED: surreal did not become healthy on port {}", self.port);
            }
            std::thread::sleep(Duration::from_millis(150));
        }
    }
}

impl Drop for Throwaway {
    fn drop(&mut self) {
        let _ = self.child.kill(); // by handle, never by name
        let _ = self.child.wait();
    }
}

/// Fails the test (not a silent `return`) when no SurrealDB binary can be found, so the absence of a
/// database is reported as a FAILURE, never as a pass. Matches `differential.py`'s own convention
/// (`NotExecuted` -> nonzero exit): a done-when item that never ran must not read as green.
macro_rules! skip_without_surreal {
    () => {
        match find_surreal() {
            Some(bin) => bin,
            None => panic!(
                "NOT EXECUTED (fails this test, not a pass): SurrealDB binary not found. Install it, or \
                 set GROUNDWORKS_SURREAL to its path. This test cannot prove its done-when item without a \
                 real server, and a missing dependency must fail the gate rather than silently report \
                 success (see differential.py's NotExecuted -> exit 2 convention)."
            ),
        }
    };
}

// ---------------------------------------------------------------------------------------------
// denied write under record auth: never empty-OK (topology trap T8)
// ---------------------------------------------------------------------------------------------

/// A minimal record-auth setup: an `agent` ACCESS whose SIGNIN checks a `user` row, and a `locked`
/// table whose PERMISSIONS refuse every write to anyone signed in that way. Root bypasses both.
const SCHEMA: &str = "
    DEFINE TABLE user SCHEMALESS PERMISSIONS NONE;
    DEFINE ACCESS agent ON DATABASE TYPE RECORD
        SIGNIN ( SELECT * FROM user WHERE login = $id AND pass = $pass );
    DEFINE TABLE locked SCHEMALESS PERMISSIONS FOR select FULL FOR create, update, delete NONE;
";

#[test]
fn a_denied_write_under_record_auth_raises_and_is_never_read_as_empty_ok() {
    let binary = skip_without_surreal!();
    let port = free_port();
    let surreal = Throwaway::start(&binary, port);

    let root = Db::new(&surreal.url(), "gwtest", "d1", "root", "root", Duration::from_secs(10), None);
    root.script(SCHEMA).expect("schema must apply");
    root.sql("CREATE user:u1 SET login = 'agent1', pass = 'pw'", None)
        .expect("seeding the signin row must succeed");

    let agent = Db::new(&surreal.url(), "gwtest", "d1", "agent1", "pw", Duration::from_secs(10), Some("agent"));

    // arm 1: `sql` raises PermissionDenied, never returns an empty-OK result.
    match agent.sql("CREATE locked:x SET v = 1", None) {
        Err(DbError::PermissionDenied { message, missing }) => {
            assert!(message.contains("CREATE"), "message should name the verb: {message}");
            assert!(message.contains("refused by datastore permissions"), "{message}");
            assert!(!missing.is_empty(), "a denial must name what is missing");
        }
        other => panic!("expected PermissionDenied, got {other:?}"),
    }

    // arm 2: `try_sql` raises the same PermissionDenied (it is not a gate answer to swallow into
    // (false, message) -- db.py's own comment: "a caller never explains a denied write as missing
    // evidence").
    match agent.try_sql("CREATE locked:y SET v = 1") {
        Err(DbError::PermissionDenied { .. }) => {}
        other => panic!("expected PermissionDenied from try_sql, got {other:?}"),
    }

    // root is never denied: the same write the agent could not make succeeds under root.
    root.sql("CREATE locked:z SET v = 1", None).expect("root bypasses table PERMISSIONS");
    assert_eq!(root.count("locked").unwrap(), 1, "only root's write landed");
}

// ---------------------------------------------------------------------------------------------
// reconnect: ONE connection per Db, not a pool; a server restart mid-run must not wedge it
// ---------------------------------------------------------------------------------------------

#[test]
fn a_server_restart_mid_run_reconnects_on_the_same_db() {
    let binary = skip_without_surreal!();
    let port = free_port();

    let first = Throwaway::start(&binary, port);
    let db = Db::new(&first.url(), "gwtest", "d1", "root", "root", Duration::from_secs(10), None);
    assert_eq!(db.sql("RETURN 1", Some(1)).unwrap(), vec![serde_json::json!(1)]);
    drop(first); // stop the first server by its own handle

    // Give the OS a moment to release the port before the second instance rebinds it.
    let deadline = Instant::now() + Duration::from_secs(5);
    while TcpStream::connect(("127.0.0.1", port)).is_ok() {
        if Instant::now() > deadline {
            panic!("NOT EXECUTED: port {port} did not free up after stopping the first surreal");
        }
        std::thread::sleep(Duration::from_millis(100));
    }

    let second = Throwaway::start(&binary, port);
    // Same `Db`, same URL, no pool to evict: it must not still be wedged on the dead connection.
    assert_eq!(db.sql("RETURN 1", Some(1)).unwrap(), vec![serde_json::json!(1)]);
    drop(second);
}

// ---------------------------------------------------------------------------------------------
// RED ARM: a fake /version that says 2.x; ping refuses with the oracle's wording. No real
// SurrealDB needed -- a real one would never lie about its own series.
// ---------------------------------------------------------------------------------------------

/// A tiny single-purpose HTTP/1.1 server: GET /version returns `version_body` verbatim; anything
/// else (i.e. POST /sql) returns a canned two-block OK response, as if `USE NS ... DB ...; RETURN 1`
/// had succeeded -- just enough for `Db::ping`'s preliminary `sql("RETURN 1")` to pass before it
/// reaches the version check under test.
fn spawn_fake_version_server(version_body: &'static str) -> (String, mpsc::Sender<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let port = listener.local_addr().unwrap().port();
    let (tx, rx) = mpsc::channel::<()>();
    std::thread::spawn(move || loop {
        if rx.try_recv().is_ok() {
            return;
        }
        match listener.accept() {
            Ok((stream, _)) => handle_fake_request(stream, version_body),
            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(10));
            }
            Err(_) => return,
        }
    });
    (format!("http://127.0.0.1:{port}/sql"), tx)
}

fn handle_fake_request(stream: TcpStream, version_body: &str) {
    let _ = stream.set_nodelay(true);
    let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
    let mut request_line = String::new();
    if reader.read_line(&mut request_line).is_err() {
        return;
    }
    let mut content_length = 0usize;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line).is_err() || line == "\r\n" || line.is_empty() {
            break;
        }
        if let Some(rest) = line.to_ascii_lowercase().strip_prefix("content-length:") {
            content_length = rest.trim().parse().unwrap_or(0);
        }
    }
    if content_length > 0 {
        let mut body = vec![0u8; content_length];
        let _ = reader.read_exact(&mut body);
    }
    let path = request_line.split_whitespace().nth(1).unwrap_or("/");
    let response_body = if path.ends_with("/version") {
        version_body.to_string()
    } else {
        r#"[{"status":"OK","result":null},{"status":"OK","result":[1]}]"#.to_string()
    };
    let response = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        response_body.len(),
        response_body
    );
    let mut stream = stream;
    let _ = stream.write_all(response.as_bytes());
}

#[test]
fn ping_refuses_a_fake_server_reporting_a_different_major_series() {
    let (url, stop) = spawn_fake_version_server("surrealdb-2.0.0+fake");
    let db = Db::new(&url, "gwtest", "d1", "root", "root", Duration::from_secs(5), None);

    let err = db.ping(true).expect_err("a 2.x server must refuse, not pass");
    let message = err.to_string();
    assert!(message.contains("measured against SurrealDB 3.1"), "{message}");
    assert!(message.contains("2.0.0"), "{message}");
    assert!(message.contains("re-measure"), "{message}");

    // and it can still be asked not to check, for offline use (db.py's own promise).
    assert!(db.ping(false).unwrap());

    let _ = stop.send(());
}
