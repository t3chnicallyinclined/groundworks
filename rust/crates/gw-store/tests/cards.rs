//! The card and bridge rules of the schema (0019, 0020; idea-bridges design 2026-10-30), each proven both ways
//! against a throwaway SurrealDB under the agent credential: refused without, accepted with. FAILS as NOT
//! EXECUTED without a SurrealDB binary.

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

fn value(db: &Db, query: &str) -> serde_json::Value {
    db.sql(query, Some(1)).unwrap().remove(0)
}

const H: &str = "0000000000000000000000000000000000000000000000000000000000000000";

/// A card statement quoting `unit` at `grade`, with the given extra fields.
fn card(id: &str, unit: &str, source_kind: &str, grade: &str, extra: &str) -> String {
    format!(
        "CREATE card:{id} SET kind = 'problem', x = 'keep it steady', y = 'bursts', unit = unit:{unit}, lines = [1, 1], \
         quote = 'it stays steady', quote_hash = '{H}', source_kind = '{source_kind}', hash = '{H}', grade = '{grade}'{extra};"
    )
}

#[test]
fn cards_cap_recollection_and_go_stale_with_their_quote() {
    let server = Throwaway::start();
    server.estate();
    let agent = server.agent("proj_a");
    ok(
        &agent,
        "CREATE unit:doc SET locator = 'n.md#L1-1', hash = 'h1', kind = 'doc', text = 'it stays steady';",
    );
    ok(
        &agent,
        "CREATE unit:mem SET locator = '~claude-memory/m.md#L1-1', hash = 'h2', kind = 'memory', text = 'x';",
    );

    // S0's cap: a memory unit never makes a CONFIRMED card, and source_kind cannot be lied about.
    refused(&agent, &card("m1", "mem", "memory", "CONFIRMED", ""));
    refused(&agent, &card("m2", "mem", "doc", "CONFIRMED", ""));
    ok(&agent, &card("m3", "mem", "memory", "INFERRED", ""));
    ok(&agent, &card("d1", "doc", "doc", "CONFIRMED", ""));
    // A card is written once: its grade and facets do not change.
    refused(&agent, "UPDATE card:m3 SET grade = 'CONFIRMED';");
    refused(&agent, "UPDATE card:d1 SET x = 'something else';");
    // Shape: a problem needs y; a capability needs an assumption; facets are capped.
    refused(
        &agent,
        &card("p1", "doc", "doc", "INFERRED", "").replace(", y = 'bursts'", ""),
    );
    refused(
        &agent,
        &card("c1", "doc", "doc", "INFERRED", "")
            .replace("kind = 'problem'", "kind = 'capability'")
            .replace(", y = 'bursts'", ""),
    );
    ok(
        &agent,
        &card(
            "c2",
            "doc",
            "doc",
            "INFERRED",
            ", a = ['steady input'], solves = card:d1",
        )
        .replace("kind = 'problem'", "kind = 'capability'")
        .replace(", y = 'bursts'", ""),
    );
    refused(
        &agent,
        &card("x1", "doc", "doc", "INFERRED", "").replace("keep it steady", &"s".repeat(301)),
    );
    // The quote is a unit's lines or a fact's words, exactly one; a fact makes a CONFIRMED card only if it is.
    refused(
        &agent,
        &card("q1", "doc", "doc", "INFERRED", "").replace("unit = unit:doc, lines = [1, 1], ", ""),
    );
    ok(
        &agent,
        "CREATE fact:guess SET text = 'it stays steady', grade = 'INFERRED';",
    );
    refused(&agent, &card("q2", "doc", "doc", "INFERRED", ", fact = fact:guess"));
    let from_fact = |id: &str, grade: &str| {
        card(id, "doc", "fact", grade, ", fact = fact:guess").replace("unit = unit:doc, lines = [1, 1], ", "")
    };
    refused(&agent, &from_fact("f1", "CONFIRMED"));
    ok(&agent, &from_fact("f2", "INFERRED"));
    // The memory project is recollection as a whole: even a doc unit there makes no CONFIRMED card.
    server.namespace("proj_memory");
    let memory = server.agent("proj_memory");
    ok(
        &memory,
        "CREATE unit:doc SET locator = 'n.md#L1-1', hash = 'h1', kind = 'doc', text = 'it stays steady';",
    );
    refused(&memory, &card("mm1", "doc", "doc", "CONFIRMED", ""));
    ok(&memory, &card("mm2", "doc", "doc", "INFERRED", ""));
    // Nobody deletes a card.
    refused(&agent, "DELETE card:d1;");

    // R1 for cards: the quoted unit changes, the card goes stale; a reclassified unit does not block it.
    ok(&agent, "UPDATE unit:mem SET kind = 'doc';");
    ok(&agent, "UPDATE unit:doc SET hash = 'h1-changed';");
    assert_eq!(value(&agent, "SELECT VALUE status FROM ONLY card:d1;"), "stale");
    assert_eq!(value(&agent, "SELECT VALUE status FROM ONLY card:m3;"), "live");
    ok(&agent, "UPDATE unit:mem SET present = false;");
    assert_eq!(value(&agent, "SELECT VALUE status FROM ONLY card:m3;"), "stale");
}
