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
    assert_eq!(current_version(&root).unwrap().unwrap()["applied"], 5);

    let mut broken: Vec<(&str, &str)> = SCHEMA.to_vec();
    broken.push((
        "0006_broken.surql",
        "-- scope: project\nDEFINE TABLE ok_table SCHEMAFULL;\nTHROW 'a broken migration';",
    ));
    assert!(migrate(&root, Scope::Project, &broken, true).is_err());
    assert_eq!(
        current_version(&root).unwrap().unwrap()["applied"],
        5,
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
