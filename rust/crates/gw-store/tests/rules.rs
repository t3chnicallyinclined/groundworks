//! Every store rule, proven both ways against a throwaway in-memory SurrealDB this test owns:
//! refused without its evidence or role, accepted with it. If no SurrealDB binary is found the
//! tests FAIL ("NOT EXECUTED"), never pass, so a gate that did not run cannot read green.

use gw_core::db::{Db, DbError};
use gw_store::testkit::Throwaway;
use gw_store::{GLOBAL_NS, SCHEMA, Scope, connect_member, current_version, migrate};

/// One server with `global`, `proj_a` and `proj_b` migrated, and `owner1`/`agent1` in each.
struct Estate {
    server: Throwaway,
}

impl Estate {
    fn new() -> Self {
        let server = Throwaway::start();
        server.estate();
        Estate { server }
    }

    fn root(&self, ns: &str) -> Db {
        self.server.root(ns)
    }

    fn agent(&self, ns: &str) -> Db {
        self.server.agent(ns)
    }

    fn owner(&self, ns: &str) -> Db {
        self.server.owner(ns)
    }
}

fn ok(db: &Db, statement: &str) {
    match db.try_sql(statement) {
        Ok((true, _)) => {}
        Ok((false, message)) => panic!("expected accepted, was refused: {statement}\n  {message}"),
        Err(e) => panic!("expected accepted, was refused: {statement}\n  {e}"),
    }
}

/// Refused by a rule (an error), or by PERMISSIONS (gw-core turns SurrealDB's silent empty-OK into
/// `PermissionDenied`). Either way the write must not land.
fn refused(db: &Db, statement: &str) {
    match db.try_sql(statement) {
        Ok((false, _)) | Err(DbError::PermissionDenied { .. }) => {}
        Ok((true, _)) => panic!("expected refused, was accepted: {statement}"),
        Err(e) => panic!("expected a refusal, got a different failure: {statement}\n  {e}"),
    }
}

fn value(db: &Db, query: &str) -> serde_json::Value {
    db.sql(query, Some(1)).unwrap().remove(0)
}

#[test]
fn migrations_apply_once_and_a_failed_file_rolls_back() {
    let estate = Estate::new();
    let root = estate.root("proj_a");
    assert!(
        migrate(&root, Scope::Project, SCHEMA, true).unwrap().is_empty(),
        "second run is a no-op"
    );
    // The number of the last project-scoped file.
    let last: i64 = SCHEMA
        .iter()
        .filter(|(_, text)| text.starts_with("-- scope: project"))
        .filter_map(|(name, _)| name[..4].parse().ok())
        .max()
        .unwrap();
    assert_eq!(current_version(&root).unwrap().unwrap()["applied"], last);

    let mut broken: Vec<(&str, &str)> = SCHEMA.to_vec();
    broken.push((
        "9999_broken.surql",
        "-- scope: project\nDEFINE TABLE ok_table SCHEMAFULL;\nTHROW 'a broken migration';",
    ));
    assert!(migrate(&root, Scope::Project, &broken, true).is_err());
    assert_eq!(
        current_version(&root).unwrap().unwrap()["applied"],
        last,
        "version did not move"
    );
    let tables = value(&root, "INFO FOR DB;");
    assert!(
        tables["tables"].get("ok_table").is_none(),
        "the failed file's first statement rolled back"
    );
}

#[test]
fn r1_a_changed_unit_marks_its_facts_stale() {
    let estate = Estate::new();
    let (root, agent) = (estate.root("proj_a"), estate.agent("proj_a"));
    ok(&root, "CREATE unit:u1 SET locator = 'src/a.rs#L1-5', hash = 'h1';");
    ok(&root, "CREATE fact:f1 SET text = 'a is safe', grade = 'INFERRED';");
    ok(&root, "RELATE fact:f1->evidence->unit:u1;");
    ok(&agent, "UPDATE unit:u1 SET seen_at_commit = 'abc';");
    assert_eq!(
        value(&root, "SELECT VALUE status FROM ONLY fact:f1;"),
        "live",
        "same hash: still live"
    );
    ok(&agent, "UPDATE unit:u1 SET hash = 'h2';");
    assert_eq!(
        value(&root, "SELECT VALUE status FROM ONLY fact:f1;"),
        "stale",
        "hash changed: stale"
    );
}

#[test]
fn r2_confirmed_needs_evidence_a_receipt_or_the_owner() {
    let estate = Estate::new();
    let (root, agent) = (estate.root("proj_a"), estate.agent("proj_a"));
    ok(&root, "CREATE unit:u1 SET locator = 'README.md#L1', hash = 'h1';");

    refused(&agent, "CREATE fact:bare SET text = 't', grade = 'CONFIRMED';");
    refused(&root, "CREATE fact:bare_root SET text = 't', grade = 'CONFIRMED';");

    ok(&agent, "CREATE fact:linked SET text = 't', grade = 'INFERRED';");
    ok(&agent, "RELATE fact:linked->evidence->unit:u1;");
    ok(&agent, "UPDATE fact:linked SET grade = 'CONFIRMED';");

    ok(
        &agent,
        "CREATE receipt:r1 SET command = 'cargo test', machine = 'M', location = '.', exit_code = 0, ok = true, output_hash = 'x';",
    );
    ok(
        &agent,
        "CREATE fact:ran SET text = 't', grade = 'CONFIRMED', receipt = receipt:r1;",
    );
    ok(
        &agent,
        "CREATE fact:said SET text = 't', grade = 'CONFIRMED', owner_statement = 'owner, 2026-10-24';",
    );

    refused(&agent, "UPDATE fact:ran SET receipt = NONE;");
    refused(&agent, "DELETE evidence WHERE in = fact:linked;");
    refused(&agent, "DELETE fact:linked;");
}

#[test]
fn r3_receipts_cannot_be_edited_backdated_or_forged() {
    let estate = Estate::new();
    let (root, agent) = (estate.root("proj_a"), estate.agent("proj_a"));
    ok(
        &agent,
        "CREATE receipt:r1 SET command = 'cargo test', machine = 'M', location = '.', exit_code = 1, ok = false, output_hash = 'x';",
    );
    assert_eq!(
        value(&root, "SELECT VALUE ran_by FROM ONLY receipt:r1;"),
        "member:agent1"
    );
    refused(&agent, "UPDATE receipt:r1 SET ok = true;");
    refused(&root, "UPDATE receipt:r1 SET ok = true;");
    refused(
        &agent,
        "CREATE receipt:future SET command = 'c', machine = 'M', location = '.', ok = true, output_hash = 'x', ran_at = d'2099-01-01T00:00:00Z';",
    );
    refused(
        &agent,
        "CREATE receipt:forged SET command = 'c', machine = 'M', location = '.', ok = true, output_hash = 'x', ran_by = member:owner1;",
    );
}

#[test]
fn scope_policy_walls_and_confirmations_are_the_owners_alone() {
    let estate = Estate::new();
    let (agent, owner) = (estate.agent(GLOBAL_NS), estate.owner(GLOBAL_NS));

    refused(
        &agent,
        "CREATE scope_policy:proj_a SET name = 'proj_a', kind = 'project', level = 'shared';",
    );
    ok(
        &owner,
        "CREATE scope_policy:proj_a SET name = 'proj_a', kind = 'project', level = 'isolated';",
    );
    refused(&agent, "UPDATE scope_policy:proj_a SET level = 'shared';");
    ok(&owner, "UPDATE scope_policy:proj_a SET level = 'private';");
    refused(&owner, "CREATE scope_policy:bad SET name = 'Proj A', kind = 'project';");

    refused(
        &agent,
        "CREATE wall SET a = 'proj_a', b = 'proj_b', why = 'two clients';",
    );
    ok(
        &owner,
        "CREATE wall SET a = 'proj_a', b = 'proj_b', why = 'two clients';",
    );
    refused(
        &owner,
        "CREATE wall SET a = 'proj_b', b = 'proj_a', why = 'reversed pair';",
    );
    refused(&owner, "CREATE wall SET a = 'proj_a', b = 'proj_b', why = 'again';");

    ok(&agent, "CREATE project:a SET slug = 'a', name = 'A', root = 'C:/x/a';");
    refused(
        &agent,
        "CREATE gate_def:g1 SET project = project:a, command = 'cargo test', confirmed = true;",
    );
    ok(
        &agent,
        "CREATE gate_def:g2 SET project = project:a, command = 'cargo test';",
    );
    refused(&agent, "UPDATE gate_def:g2 SET confirmed = true;");
    ok(&owner, "UPDATE gate_def:g2 SET confirmed = true;");

    ok(
        &agent,
        "CREATE dead_end:d1 SET title = 'learned prediction', why = 'more rollbacks', from_scope = 'proj_a';",
    );
    refused(&agent, "UPDATE dead_end:d1 SET status = 'accepted';");
    ok(&owner, "UPDATE dead_end:d1 SET status = 'accepted';");

    let changes = value(
        &estate.root(GLOBAL_NS),
        "SHOW CHANGES FOR TABLE scope_policy SINCE 0 LIMIT 10;",
    );
    assert!(
        changes.as_array().is_some_and(|c| c.len() >= 2),
        "policy changes are in the audit feed: {changes}"
    );
}

#[test]
fn a_project_credential_cannot_reach_another_project() {
    let estate = Estate::new();
    ok(
        &estate.root("proj_b"),
        "CREATE fact:secret SET text = 'b only', grade = 'INFERRED';",
    );
    let agent_a = estate.agent("proj_a");
    let crossed = agent_a.sql("USE NS proj_b; SELECT * FROM fact;", None);
    match crossed {
        Err(_) => {}
        Ok(blocks) => assert!(
            !serde_json::to_string(&blocks).unwrap().contains("b only"),
            "proj_a's credential read proj_b: {blocks:?}"
        ),
    }
    // proj_a's credential does not exist in proj_b at all: its sign-in there fails.
    let wrong = connect_member(&estate.server.url(), "proj_b", "agent1", "not-the-password").unwrap();
    assert!(wrong.sql("SELECT * FROM fact;", None).is_err());
}

fn vars(pairs: &[(&str, serde_json::Value)]) -> serde_json::Map<String, serde_json::Value> {
    pairs.iter().map(|(k, v)| ((*k).to_owned(), v.clone())).collect()
}

#[test]
fn bound_variables_are_data_never_statements() {
    let estate = Estate::new();
    let agent = estate.agent("proj_a");
    let hostile = "x'; DELETE fact; --";
    agent
        .query(
            "CREATE fact:v SET text = $text, grade = 'INFERRED';",
            &vars(&[("text", hostile.into())]),
            Some(1),
        )
        .unwrap();
    let stored = estate
        .root("proj_a")
        .sql("SELECT VALUE text FROM ONLY fact:v;", Some(1))
        .unwrap();
    assert_eq!(stored[0], hostile, "the value was stored as text, not run");
    // Over JSON-RPC, 3.1.6 turned this into the record `gate:cd` and dropped the rest (2026-10-25).
    for looks_like_a_record in ["gate: cd rust && cargo test", "person:tobie", "receipt:abc"] {
        let back = agent
            .query(
                "RETURN [$v, type::is_string($v)];",
                &vars(&[("v", looks_like_a_record.into())]),
                Some(1),
            )
            .unwrap();
        assert_eq!(
            back[0],
            serde_json::json!([looks_like_a_record, true]),
            "{looks_like_a_record}"
        );
    }
    // A write PERMISSIONS refuse through query() is still an error, never an empty OK.
    let refused = agent.query("DELETE fact:v;", &serde_json::Map::new(), Some(1));
    assert!(matches!(refused, Err(DbError::PermissionDenied { .. })), "{refused:?}");
}

#[test]
fn the_store_records_who_wrote_a_fact_and_nobody_can_say_otherwise() {
    let estate = Estate::new();
    let (root, agent) = (estate.root("proj_a"), estate.agent("proj_a"));
    ok(&agent, "CREATE fact:mine SET text = 't', grade = 'INFERRED';");
    assert_eq!(
        root.sql("SELECT VALUE <string> written_by FROM ONLY fact:mine;", Some(1))
            .unwrap()[0],
        "member:agent1"
    );
    refused(
        &agent,
        "CREATE fact:forged SET text = 't', grade = 'INFERRED', written_by = member:owner1;",
    );
    refused(&agent, "UPDATE fact:mine SET written_by = member:owner1;");
}

/// Regression (code review 2026-10-25, refuted claim): 0006's `written_by` rule must not block
/// updates to facts the loader wrote (written_by NONE), including the stale event's.
#[test]
fn loader_facts_still_go_stale_and_can_be_updated() {
    let estate = Estate::new();
    let (root, agent) = (estate.root("proj_a"), estate.agent("proj_a"));
    ok(&root, "CREATE unit:u1 SET locator = 'a#L1', hash = 'h1';");
    ok(&root, "CREATE fact:old SET text = 't', grade = 'INFERRED';");
    ok(&root, "RELATE fact:old->evidence->unit:u1;");
    ok(&agent, "UPDATE unit:u1 SET hash = 'h2';");
    assert_eq!(value(&root, "SELECT VALUE status FROM ONLY fact:old;"), "stale");
    ok(&agent, "UPDATE fact:old SET status = 'gone';");
}

/// Regression (live store, 2026-10-25): upgrading a namespace that already holds units must work. On
/// rocksdb, building a full-text index over existing rows could not see an analyzer defined earlier in
/// the same transaction ("The analyzer 'gw_words' does not exist").
#[test]
fn an_upgrade_over_existing_units_migrates() {
    let server = gw_store::testkit::Throwaway::start();
    let root = server.root("proj_old");
    gw_store::ensure(&root, "proj_old").unwrap();
    let before: Vec<(&str, &str)> = SCHEMA.iter().copied().filter(|(f, _)| f < &"0007").collect();
    migrate(&root, Scope::Project, &before, true).unwrap();
    root.sql(
        "CREATE unit:q SET locator = 'a#L1-1', hash = 'h', text = 'the parser rejects empty input', kind = 'quote';",
        None,
    )
    .unwrap();
    migrate(&root, Scope::Project, SCHEMA, true).unwrap();
    let hits = root
        .sql("SELECT VALUE locator FROM unit WHERE text @1@ 'parser';", Some(1))
        .unwrap();
    assert_eq!(
        hits[0],
        serde_json::json!(["a#L1-1"]),
        "existing units are indexed for search"
    );
}
