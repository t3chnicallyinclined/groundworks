//! The bridge gate in the schema (0020; idea-bridges design 2026-10-30 with the scholar-architect's review
//! against METHOD section 13), each step proven both ways against a throwaway SurrealDB: refused without its
//! evidence, accepted with it. FAILS as NOT EXECUTED without a SurrealDB binary.

use gw_core::db::{Db, DbError};
use gw_store::testkit::Throwaway;

fn ok(db: &Db, statement: &str) {
    match db.try_sql(statement) {
        Ok((true, _)) => {}
        Ok((false, message)) => panic!("expected accepted, was refused: {statement}\n  {message}"),
        Err(e) => panic!("expected accepted, was refused: {statement}\n  {e}"),
    }
}

fn refused(db: &Db, statement: &str) {
    match db.try_sql(statement) {
        Ok((false, _)) | Err(DbError::PermissionDenied { .. }) => {}
        Ok((true, _)) => panic!("expected refused, was accepted: {statement}"),
        Err(e) => panic!("expected a refusal, got a different failure: {statement}\n  {e}"),
    }
}

const H: &str = "0000000000000000000000000000000000000000000000000000000000000000";
const CHECK: &str =
    "[{assumption: 'slow change', verdict: 'holds', unit: 'u', cite: 'n.md#L1-1', quote: 'steady', quote_hash: 'h'}]";
const NOVEL: &str = "{verdict: 'not_made', query: 'q', reach: ['a', 'beta'], hits: []}";
const SKEPTIC: &str = "[{lens: 'skeptic', verdict: 'proceed', why: 'the assumption is quoted in the target'}]";
const TEST: &str = "test = 'replay the burst trace', test_command = 'cargo test burst', test_expect = 'exit 0'";

fn receipt(db: &Db, id: &str, command: &str, ok_: bool) {
    ok(
        db,
        &format!(
            "CREATE receipt:{id} SET command = '{command}', machine = 'm', location = 'l', ok = {ok_}, output_hash = 'h';"
        ),
    );
}

#[test]
fn a_bridge_moves_only_with_its_evidence() {
    let server = Throwaway::start();
    server.estate();
    let agent = server.agent("proj_a");
    let owner = server.owner("proj_a");
    ok(
        &agent,
        "CREATE unit:doc SET locator = 'n.md#L1-1', hash = 'h1', kind = 'doc', text = 'steady';",
    );
    ok(
        &agent,
        &format!(
            "CREATE card:p SET kind = 'problem', x = 'keep it steady', y = 'bursts', unit = unit:doc, lines = [1, 1], \
             quote = 'steady', quote_hash = '{H}', source_kind = 'doc', hash = '{H}';"
        ),
    );
    // A receipt run before the test was registered proves nothing about it.
    receipt(&agent, "early", "cargo test burst", true);
    let new = |id: &str| {
        format!(
            "CREATE bridge:{id} SET target = card:p, source_project = 'beta', source_card = 'k1', source_hash = '{H}', \
             source_a = 1, found_by = 'facet';"
        )
    };
    ok(&agent, &new("b1"));
    // Born a candidate, never CONFIRMED below supported, the source held as a hash.
    refused(
        &agent,
        &new("b0").replace("found_by = 'facet'", "found_by = 'facet', status = 'testable'"),
    );
    refused(&agent, "UPDATE bridge:b1 SET grade = 'CONFIRMED';");
    refused(
        &agent,
        &new("bx").replace(&format!("source_hash = '{H}'"), "source_hash = 'the whole card text'"),
    );

    let all = format!("assumption_check = {CHECK}, novelty_check = {NOVEL}, lens_verdicts = {SKEPTIC}");
    let testable = |sets: &str| format!("UPDATE bridge:b1 SET status = 'testable', {sets};");
    // Unregistered test: refused even with everything else.
    refused(&agent, &testable(&all));
    // Registration is its own write, before the move (test_at is stamped by the store, not the writer).
    ok(&agent, &format!("UPDATE bridge:b1 SET {TEST};"));
    // Each piece of testable, missing in turn.
    refused(
        &agent,
        &testable(&all.replace(&format!("assumption_check = {CHECK}, "), "")),
    );
    refused(
        &agent,
        &testable(&all.replace(&format!("novelty_check = {NOVEL}, "), "")),
    );
    refused(
        &agent,
        &testable(&all.replace("verdict: 'not_made'", "verdict: 'made'")),
    );
    refused(
        &agent,
        &testable(&all.replace(&format!(", lens_verdicts = {SKEPTIC}"), "")),
    );
    refused(
        &agent,
        &testable(&all.replace("lens: 'skeptic'", "lens: 'statistician'")),
    );
    refused(
        &agent,
        &testable(&all.replace(
            SKEPTIC,
            "[{lens: 'skeptic', verdict: 'proceed', why: 'w'}, {lens: 'statistician', verdict: 'reject', why: 'n=1'}]",
        )),
    );
    refused(
        &agent,
        "UPDATE bridge:b1 SET status = 'supported', receipt = receipt:early;",
    );
    ok(&agent, &testable(&all));
    // Registered once: the command cannot be swapped after the fact, and its time cannot be moved.
    refused(&agent, "UPDATE bridge:b1 SET test_command = 'true';");
    ok(&agent, "UPDATE bridge:b1 SET test_at = d'2000-01-01T00:00:00Z';");
    let at: String = serde_json::from_value(
        agent
            .sql("SELECT VALUE <string> test_at FROM ONLY bridge:b1;", Some(1))
            .unwrap()
            .remove(0),
    )
    .unwrap();
    assert!(!at.starts_with("d'2000"), "test_at moved to {at}");

    receipt(&agent, "red", "cargo test burst", false);
    receipt(&agent, "green", "cargo test burst", true);
    receipt(&agent, "other", "true", true);
    let support = |r: &str, red: &str| {
        format!(
            "UPDATE bridge:b1 SET status = 'supported', receipt = receipt:{r}, red_receipt = receipt:{red}, grade = 'CONFIRMED';"
        )
    };
    // Supported needs the registered command, green after registration, and a red run after registration.
    refused(&owner, &support("early", "red"));
    refused(&owner, &support("other", "red"));
    refused(&owner, &support("green", "green"));
    refused(
        &owner,
        "UPDATE bridge:b1 SET status = 'supported', receipt = receipt:green, grade = 'CONFIRMED';",
    );
    // The proposer cannot promote its own bridge; a second credential can.
    refused(&agent, &support("green", "red"));
    ok(&owner, &support("green", "red"));
    refused(&owner, "UPDATE bridge:b1 SET status = 'refuted';");

    // A bridge from the memory project, or resting on recollection, is supported at INFERRED, never CONFIRMED.
    for (id, extra) in [
        ("bm", "source_project = 'memory'"),
        ("br", "source_recollection = true"),
    ] {
        ok(
            &agent,
            &new(id)
                .replace(
                    "source_project = 'beta'",
                    "source_project = 'beta', source_recollection = false",
                )
                .replace(
                    if extra.starts_with("source_project") {
                        "source_project = 'beta'"
                    } else {
                        "source_recollection = false"
                    },
                    extra,
                ),
        );
        ok(&agent, &format!("UPDATE bridge:{id} SET {TEST};"));
        ok(&agent, &format!("UPDATE bridge:{id} SET status = 'testable', {all};"));
        receipt(&agent, &format!("{id}red"), "cargo test burst", false);
        receipt(&agent, &format!("{id}green"), "cargo test burst", true);
        let promote = |grade: &str| {
            format!(
                "UPDATE bridge:{id} SET status = 'supported', receipt = receipt:{id}green, red_receipt = receipt:{id}red, grade = '{grade}';"
            )
        };
        refused(&owner, &promote("CONFIRMED"));
        ok(&owner, &promote("INFERRED"));
    }

    // Refuted: a failing run of the registered test, or a quoted mismatch. Kept.
    ok(&agent, &new("b2"));
    refused(&agent, "UPDATE bridge:b2 SET status = 'refuted';");
    refused(
        &agent,
        "UPDATE bridge:b2 SET status = 'refuted', receipt = receipt:red;",
    );
    ok(
        &agent,
        "UPDATE bridge:b2 SET status = 'refuted', assumption_check = [{assumption: 'a', verdict: 'fails', unit: 'u', \
         cite: 'c', quote: 'steady', quote_hash: 'h'}];",
    );
    refused(&agent, "UPDATE bridge:b2 SET status = 'candidate';");
    refused(&agent, "DELETE bridge:b2;");
}
