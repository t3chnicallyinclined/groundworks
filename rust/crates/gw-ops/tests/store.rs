//! The store layer against a real throwaway SurrealDB: receipts, facts, verified quotes, stale
//! marking, and owner-only policy. Fails as NOT EXECUTED without a SurrealDB binary.

use std::path::PathBuf;

use gw_ops::journal::{Confidence, Kind};
use gw_ops::store::{FactIn, Quote, ReceiptIn, Store};
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
    f.store.provision_owner(gw_store::GLOBAL_NS, OWNER_PASS).unwrap();
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
        .import_fact(&f.dir, "demo", &fact(Confidence::Inferred), "2026-10-24T05:47:08Z")
        .unwrap();
    let facts = f.store.facts("demo", None, 10).unwrap();
    assert!(facts[0].at.starts_with("2026-10-24T05:47:08"), "{}", facts[0].at);
    assert!(
        f.store
            .import_fact(&f.dir, "demo", &fact(Confidence::Confirmed), "2026-10-24T05:47:08Z")
            .is_err()
    );
    assert!(
        f.store
            .import_fact(&f.dir, "demo", &fact(Confidence::Inferred), "yesterday; DELETE fact")
            .is_err()
    );
}
