//! The bridge gate in the schema (0020; idea-bridges design 2026-10-30), each step proven both ways against a
//! throwaway SurrealDB under the agent credential. FAILS as NOT EXECUTED without a SurrealDB binary.

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

#[test]
fn a_bridge_moves_only_with_its_evidence() {
    let server = Throwaway::start();
    server.estate();
    let agent = server.agent("proj_a");
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
    ok(
        &agent,
        "CREATE receipt:bad SET command = 'exit 3', machine = 'm', location = 'l', ok = false, output_hash = 'h';",
    );
    ok(
        &agent,
        "CREATE receipt:good SET command = 'true', machine = 'm', location = 'l', ok = true, output_hash = 'h';",
    );
    let new = |id: &str| {
        format!(
            "CREATE bridge:{id} SET target = card:p, source_project = 'beta', source_card = 'k1', source_hash = '{H}', \
             source_a = 1, found_by = 'facet';"
        )
    };
    ok(&agent, &new("b1"));
    // Born a candidate, never anything else, and never CONFIRMED below supported.
    refused(
        &agent,
        &new("b0").replace("found_by = 'facet'", "found_by = 'facet', status = 'testable'"),
    );
    refused(&agent, "UPDATE bridge:b1 SET grade = 'CONFIRMED';");
    // The source is held as a hash, not text.
    refused(
        &agent,
        &new("bx").replace(&format!("source_hash = '{H}'"), "source_hash = 'the whole card text'"),
    );

    let check = "[{assumption: 'slow change', verdict: 'holds', unit: 'u', cite: 'n.md#L1-1', quote: 'steady', quote_hash: 'h'}]";
    let novelty = "{query: 'q', reach: ['a', 'beta'], hits: []}";
    refused(
        &agent,
        &format!("UPDATE bridge:b1 SET status = 'testable', novelty_check = {novelty}, test = 't';"),
    );
    refused(
        &agent,
        &format!("UPDATE bridge:b1 SET status = 'testable', assumption_check = {check}, test = 't';"),
    );
    refused(
        &agent,
        &format!("UPDATE bridge:b1 SET status = 'testable', assumption_check = {check}, novelty_check = {novelty};"),
    );
    // Straight to supported skips the gate.
    refused(
        &agent,
        "UPDATE bridge:b1 SET status = 'supported', receipt = receipt:good;",
    );
    ok(
        &agent,
        &format!(
            "UPDATE bridge:b1 SET status = 'testable', assumption_check = {check}, novelty_check = {novelty}, test = 't';"
        ),
    );
    refused(&agent, "UPDATE bridge:b1 SET status = 'supported';");
    refused(
        &agent,
        "UPDATE bridge:b1 SET status = 'supported', receipt = receipt:bad;",
    );
    ok(
        &agent,
        "UPDATE bridge:b1 SET status = 'supported', receipt = receipt:good, grade = 'CONFIRMED';",
    );
    // Supported is final.
    refused(&agent, "UPDATE bridge:b1 SET status = 'refuted';");

    ok(&agent, &new("b2"));
    refused(&agent, "UPDATE bridge:b2 SET status = 'refuted';");
    ok(
        &agent,
        "UPDATE bridge:b2 SET status = 'refuted', assumption_check = [{assumption: 'a', verdict: 'fails', unit: 'u', \
         cite: 'c', quote: 'steady', quote_hash: 'h'}];",
    );
    refused(&agent, "UPDATE bridge:b2 SET status = 'candidate';");
    ok(&agent, &new("b3"));
    ok(
        &agent,
        "UPDATE bridge:b3 SET status = 'refuted', receipt = receipt:bad;",
    );
    // Refuted bridges stay, as negatives.
    refused(&agent, "DELETE bridge:b2;");
}
