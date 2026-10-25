//! The store layer against a real throwaway SurrealDB: receipts, facts, verified quotes, stale
//! marking, and owner-only policy. Fails as NOT EXECUTED without a SurrealDB binary.

use std::path::PathBuf;
use std::sync::Arc;

use gw_ops::journal::{Confidence, Kind};
use gw_ops::store::{FactIn, Launcher, Quote, ReceiptIn, Store};
use gw_store::testkit::{AGENT_PASS, OWNER_PASS, ROOT_PASS, Throwaway};

struct Fixture {
    _server: Throwaway,
    store: Store,
    dir: PathBuf,
}

impl Fixture {
    fn new(tag: &str) -> Self {
        let server = Throwaway::start();
        let store = Store::connect(&server.url(), ROOT_PASS, AGENT_PASS);
        let dir = std::env::temp_dir().join(format!("gw-store-{tag}-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        Fixture {
            _server: server,
            store,
            dir,
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn fact(confidence: Confidence) -> FactIn {
    FactIn {
        kind: Kind::Fact,
        text: "the parser rejects empty input".into(),
        confidence,
        source: "src/parse.rs".into(),
        by: "gw-engineer".into(),
        receipt: None,
        owner_statement: None,
        quote: None,
        lens: None,
        attacks: None,
        stance: None,
    }
}

#[test]
fn a_receipt_confirms_a_fact_and_nothing_else_does() {
    let f = Fixture::new("receipt");
    let project = "RetroReceipts-server";

    let bare = f.store.add_fact(&f.dir, project, &fact(Confidence::Confirmed));
    assert!(bare.is_err(), "CONFIRMED with no evidence must be refused");

    let receipt = f
        .store
        .record_receipt(
            project,
            &ReceiptIn {
                command: "cargo test".into(),
                machine: "M".into(),
                location: ".".into(),
                exit_code: Some(0),
                ok: true,
                output_hash: "abc".into(),
                hash_of: "test",
                commit: Some("deadbeef".into()),
            },
        )
        .unwrap();
    assert!(receipt.starts_with("receipt:"), "{receipt}");

    let mut with_receipt = fact(Confidence::Confirmed);
    with_receipt.receipt = Some(receipt.clone());
    f.store.add_fact(&f.dir, project, &with_receipt).unwrap();

    let mut injected = fact(Confidence::Confirmed);
    injected.receipt = Some("receipt:x; DELETE fact".into());
    assert!(f.store.add_fact(&f.dir, project, &injected).is_err());

    let facts = f.store.facts(project, None, 10).unwrap();
    assert_eq!(facts.len(), 1);
    assert_eq!(facts[0].grade, "CONFIRMED");
    assert!(facts[0].render().contains(&receipt), "{}", facts[0].render());
}

#[test]
fn a_quote_confirms_only_word_for_word_and_goes_stale_when_the_file_changes() {
    let f = Fixture::new("quote");
    let project = "demo";
    std::fs::write(
        f.dir.join("parse.rs"),
        "fn parse(s: &str) {\n    assert!(!s.is_empty());\n}\n",
    )
    .unwrap();
    let quote = |text: &str| Quote {
        file: "parse.rs".into(),
        line: 2,
        text: text.into(),
    };

    let mut wrong = fact(Confidence::Confirmed);
    wrong.quote = Some(quote("    assert!(s.is_empty());"));
    let err = f.store.add_fact(&f.dir, project, &wrong).unwrap_err();
    assert!(format!("{err:#}").contains("not found word for word"), "{err:#}");

    let mut right = fact(Confidence::Confirmed);
    right.quote = Some(quote("    assert!(!s.is_empty());"));
    f.store.add_fact(&f.dir, project, &right).unwrap();
    let facts = f.store.facts(project, None, 10).unwrap();
    assert_eq!(facts[0].grade, "CONFIRMED");
    assert_eq!(facts[0].status, "live");
    assert_eq!(facts[0].quotes, vec!["parse.rs#L2-2".to_owned()]);

    // The code changes; quoting the same span again updates the unit and marks the old fact stale.
    std::fs::write(f.dir.join("parse.rs"), "fn parse(s: &str) {\n    let _ = s;\n}\n").unwrap();
    let mut newer = fact(Confidence::Confirmed);
    newer.text = "the parser accepts empty input now".into();
    newer.quote = Some(quote("    let _ = s;"));
    f.store.add_fact(&f.dir, project, &newer).unwrap();
    let facts = f.store.facts(project, None, 10).unwrap();
    assert_eq!(facts.len(), 2);
    assert_eq!(facts[0].status, "stale", "the old claim no longer matches its file");
    assert_eq!(facts[1].status, "live");
}

#[test]
fn an_owner_statement_confirms_and_kinds_filter() {
    let f = Fixture::new("owner");
    let mut said = fact(Confidence::Confirmed);
    said.kind = Kind::Decision;
    said.owner_statement = Some("owner, 2026-10-25: use SurrealDB".into());
    f.store.add_fact(&f.dir, "demo", &said).unwrap();
    f.store.add_fact(&f.dir, "demo", &fact(Confidence::Inferred)).unwrap();
    assert_eq!(f.store.facts("demo", Some(Kind::Decision), 10).unwrap().len(), 1);
    assert_eq!(f.store.facts("demo", None, 10).unwrap().len(), 2);
    assert_eq!(f.store.facts("demo", None, 1).unwrap().len(), 1, "limit is honoured");
}

#[test]
fn scope_levels_and_walls_need_the_owner_password() {
    let f = Fixture::new("policy");
    f.store.provision_owner(OWNER_PASS).unwrap();
    assert!(
        f.store
            .set_level("not-the-owner", "proj_rega", "isolated", None)
            .is_err()
    );
    f.store.set_level(OWNER_PASS, "proj_rega", "isolated", None).unwrap();
    f.store
        .set_level(OWNER_PASS, "proj_supergun", "shared", Some("ws_fighting_games"))
        .unwrap();
    assert!(f.store.set_level(OWNER_PASS, "not a scope", "shared", None).is_err());
    f.store.add_wall(OWNER_PASS, "proj_b", "proj_a", "two clients").unwrap();
    assert!(
        f.store.add_wall(OWNER_PASS, "proj_a", "proj_b", "again").is_err(),
        "one wall per pair"
    );
    assert!(f.store.add_wall(OWNER_PASS, "proj_a", "proj_a", "self").is_err());
}

#[test]
fn an_import_keeps_its_original_time_and_still_obeys_the_evidence_rule() {
    let f = Fixture::new("import");
    f.store
        .import_fact(
            &f.dir,
            "demo",
            &fact(Confidence::Inferred),
            "2026-10-24T05:47:08Z",
            Some("OLDBOX"),
        )
        .unwrap();
    let facts = f.store.facts("demo", None, 10).unwrap();
    assert!(facts[0].at.starts_with("2026-10-24T05:47:08"), "{}", facts[0].at);
    assert_eq!(
        facts[0].machine.as_deref(),
        Some("OLDBOX"),
        "an import keeps its machine"
    );
    assert_eq!(
        facts[0].written_by.as_deref(),
        Some("NONE"),
        "the loader, not a member, wrote it"
    );
    assert!(
        f.store
            .import_fact(
                &f.dir,
                "demo",
                &fact(Confidence::Confirmed),
                "2026-10-24T05:47:08Z",
                Some("OLDBOX")
            )
            .is_err()
    );
    assert!(
        f.store
            .import_fact(
                &f.dir,
                "demo",
                &fact(Confidence::Inferred),
                "yesterday; DELETE fact",
                Some("OLDBOX")
            )
            .is_err()
    );
}

#[test]
fn an_owner_statement_typed_by_an_agent_says_so() {
    let f = Fixture::new("reported");
    let mut said = fact(Confidence::Confirmed);
    said.owner_statement = Some("owner, 2026-10-25: ship it".into());
    f.store.add_fact(&f.dir, "demo", &said).unwrap();
    let line = f.store.facts("demo", None, 1).unwrap()[0].render();
    assert!(
        line.contains("as reported by gw-engineer, not signed by the owner"),
        "{line}"
    );
}

#[test]
fn four_sessions_can_open_one_new_namespace_at_once() {
    let server = Throwaway::start();
    let url = server.url();
    let results: Vec<_> = (0..4)
        .map(|_| {
            let url = url.clone();
            std::thread::spawn(move || {
                Store::connect(&url, ROOT_PASS, AGENT_PASS)
                    .agent("proj_race")
                    .map(|_| ())
                    .map_err(|e| format!("{e:#}"))
            })
        })
        .map(|h| h.join().unwrap())
        .collect();
    assert!(results.iter().all(Result::is_ok), "{results:?}");
}

#[test]
fn each_namespace_has_its_own_agent_password() {
    let f = Fixture::new("passwords");
    f.store.agent("proj_a").unwrap();
    f.store.agent("proj_b").unwrap();
    let (a, b) = (f.store.agent_pass("proj_a"), f.store.agent_pass("proj_b"));
    assert_ne!(a, b);
    let crossed = gw_store::connect_member(f.store.url(), "proj_b", "agent", &a).unwrap();
    assert!(
        crossed.sql("SELECT * FROM fact;", None).is_err(),
        "proj_a's password must not open proj_b"
    );
}

#[test]
fn an_isolated_project_is_refused_by_the_shared_store() {
    let f = Fixture::new("isolated");
    f.store.provision_owner(OWNER_PASS).unwrap();
    f.store.set_level(OWNER_PASS, "proj_secret", "isolated", None).unwrap();
    let err = f
        .store
        .add_fact(&f.dir, "secret", &fact(Confidence::Inferred))
        .unwrap_err();
    assert!(format!("{err:#}").contains("isolated"), "{err:#}");
    f.store.add_fact(&f.dir, "open", &fact(Confidence::Inferred)).unwrap();
}

#[test]
fn the_owner_signs_with_its_own_login_and_a_wrong_password_signs_nothing() {
    let f = Fixture::new("signed");
    f.store.provision_owner(OWNER_PASS).unwrap();
    let mut said = fact(Confidence::Confirmed);
    said.by = "owner".into();
    said.owner_statement = Some("owner, 2026-10-25: ship it".into());
    assert!(
        f.store
            .add_fact_as_owner(&f.dir, "demo", &said, "not-the-owner-password")
            .is_err()
    );
    f.store.add_fact_as_owner(&f.dir, "demo", &said, OWNER_PASS).unwrap();
    let line = f.store.facts("demo", None, 1).unwrap()[0].render();
    assert!(line.contains("signed by the owner"), "{line}");
}

#[test]
fn an_import_written_twice_lands_once_and_a_missing_machine_stays_missing() {
    let f = Fixture::new("idempotent");
    let entry = fact(Confidence::Inferred);
    let first = f
        .store
        .import_fact(&f.dir, "demo", &entry, "2026-10-24T05:47:08Z", Some(""))
        .unwrap();
    let again = f
        .store
        .import_fact(&f.dir, "demo", &entry, "2026-10-24T05:47:08Z", Some(""))
        .unwrap();
    assert!(first.is_some() && again.is_none(), "{first:?} then {again:?}");
    let facts = f.store.facts("demo", None, 10).unwrap();
    assert_eq!(facts.len(), 1);
    assert_eq!(facts[0].machine, None, "an empty machine is not recorded as a machine");
}

/// A real server started the way sessions start it, on a free port with its own data directory.
struct Launched {
    launcher: Arc<Launcher>,
    dir: PathBuf,
}

impl Launched {
    fn new(tag: &str) -> Self {
        let port = std::net::TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        let dir = std::env::temp_dir().join(format!("gw-launch-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let launcher = Launcher::new(&format!("http://127.0.0.1:{port}/sql"), ROOT_PASS, None, dir.clone());
        Launched {
            launcher: Arc::new(launcher),
            dir,
        }
    }

    fn kill(pid: u32) {
        let _ = if cfg!(windows) {
            std::process::Command::new("taskkill")
                .args(["/F", "/PID", &pid.to_string()])
                .output()
        } else {
            std::process::Command::new("kill")
                .args(["-9", &pid.to_string()])
                .output()
        };
    }

    fn kill_started(launcher: &Launcher) {
        if let Some(pid) = *launcher.last_started.lock().unwrap() {
            Self::kill(pid);
        }
    }
}

impl Drop for Launched {
    fn drop(&mut self) {
        Self::kill_started(&self.launcher);
        std::thread::sleep(std::time::Duration::from_millis(500));
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

#[test]
fn two_sessions_starting_the_store_at_once_both_get_it() {
    let launched = Launched::new("race");
    let url = launched.launcher.url.clone();
    let dir = launched.dir.clone();
    // A second launcher for the same store, as a second session would have.
    let other = Arc::new(Launcher::new(&url, ROOT_PASS, None, dir));
    let (a, b) = (Arc::clone(&launched.launcher), Arc::clone(&other));
    let ta = std::thread::spawn(move || a.ensure().map_err(|e| format!("{e:#}")));
    let tb = std::thread::spawn(move || b.ensure().map_err(|e| format!("{e:#}")));
    let (ra, rb) = (ta.join().unwrap(), tb.join().unwrap());
    Launched::kill_started(&other);
    assert!(ra.is_ok() && rb.is_ok(), "{ra:?} / {rb:?}");
}

#[test]
fn a_store_that_stops_is_started_again_on_the_next_call() {
    let launched = Launched::new("restart");
    let launcher = Launcher::new(&launched.launcher.url, ROOT_PASS, None, launched.dir.clone());
    let store = Store::with_launcher(launcher, AGENT_PASS).unwrap();
    let dir = std::env::temp_dir();
    store.add_fact(&dir, "demo", &fact(Confidence::Inferred)).unwrap();
    let first = store
        .launcher()
        .unwrap()
        .last_started
        .lock()
        .unwrap()
        .expect("started one");
    Launched::kill(first);
    std::thread::sleep(std::time::Duration::from_millis(800));
    store.add_fact(&dir, "demo", &fact(Confidence::Inferred)).unwrap();
    let second = store
        .launcher()
        .unwrap()
        .last_started
        .lock()
        .unwrap()
        .expect("started again");
    assert_ne!(first, second, "a new server was started");
    assert_eq!(
        store.facts("demo", None, 10).unwrap().len(),
        2,
        "data survived the restart (rocksdb)"
    );
    Launched::kill(second);
}

#[test]
fn an_owner_login_with_no_password_works_and_a_short_one_too() {
    let f = Fixture::new("nopass");
    f.store.provision_owner("").unwrap();
    f.store.set_level("", "proj_a", "private", None).unwrap();
    assert!(
        f.store.set_level("guess", "proj_a", "shared", None).is_err(),
        "a wrong password still fails"
    );
    f.store.provision_owner("abc").unwrap();
    f.store.set_level("abc", "proj_a", "shared", None).unwrap();
}
