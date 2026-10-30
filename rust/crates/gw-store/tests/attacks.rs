//! Attack verdicts and answers (schema 0018, idea-bridges amendment A2, slice 10), both ways against a
//! throwaway SurrealDB this test owns: each rule refuses the write that breaks it and accepts the one that
//! keeps it. If no SurrealDB binary is found the tests FAIL ("NOT EXECUTED"), never pass.

use gw_core::db::{Db, DbError};
use gw_store::testkit::Throwaway;
use gw_store::{SCHEMA, Scope, ensure, migrate};

fn ok(db: &Db, statement: &str) {
    match db.try_sql(statement) {
        Ok((true, _)) => {}
        Ok((false, message)) => panic!("expected accepted, was refused: {statement}\n  {message}"),
        Err(e) => panic!("expected accepted, was refused: {statement}\n  {e}"),
    }
}

/// Refused by a rule (an error) or by PERMISSIONS (gw-core turns the silent empty OK into
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

/// A finding `fact:claim` (lens a), and `fact:hit` (lens b) disputing it, written as the agent.
fn disputed(server: &Throwaway) -> (Db, Db, Db) {
    server.namespace("proj_a");
    let (root, agent, owner) = (server.root("proj_a"), server.agent("proj_a"), server.owner("proj_a"));
    ok(&agent, "CREATE fact:other SET text = 'unrelated', grade = 'INFERRED';");
    ok(
        &agent,
        "CREATE fact:claim SET text = 'rollback resimulates every frame', grade = 'INFERRED', lens = 'a';",
    );
    ok(
        &agent,
        "CREATE fact:hit SET text = 'only confirmed frames', grade = 'INFERRED', lens = 'b', attacks = fact:claim, stance = 'disputes';",
    );
    (root, agent, owner)
}

#[test]
fn an_attack_cannot_be_withdrawn_by_editing_the_fact() {
    let server = Throwaway::start();
    let (root, agent, owner) = disputed(&server);
    refused(&agent, "UPDATE fact:hit SET attacks = NONE;");
    refused(&owner, "UPDATE fact:hit SET attacks = NONE;");
    refused(&root, "UPDATE fact:hit SET attacks = NONE;");
    refused(&agent, "UPDATE fact:hit SET stance = 'supports';");
    refused(&agent, "UPDATE fact:hit SET stance = NONE;");
    // The rest of the fact is as editable as before.
    ok(&agent, "UPDATE fact:hit SET status = 'stale';");
    assert_eq!(
        value(&root, "SELECT VALUE <string> attacks FROM ONLY fact:hit;"),
        "fact:claim"
    );
}

#[test]
fn a_dispute_is_an_unjudged_attack_and_a_support_is_not() {
    let server = Throwaway::start();
    let (root, agent, _) = disputed(&server);
    let edges = value(
        &root,
        "SELECT <string> in AS in, <string> out AS out, verdict, lens, <string> written_by AS by FROM attack;",
    );
    assert_eq!(
        edges,
        serde_json::json!([{"in": "fact:hit", "out": "fact:claim", "verdict": "unjudged", "lens": "b", "by": "member:agent1"}])
    );
    ok(
        &agent,
        "CREATE fact:agree SET text = 'yes', grade = 'INFERRED', attacks = fact:claim, stance = 'supports';",
    );
    assert_eq!(
        value(&root, "RETURN count(SELECT id FROM attack);"),
        1,
        "a support is not an attack"
    );
}

#[test]
fn an_attack_needs_a_verdict_and_a_reason() {
    let server = Throwaway::start();
    let (_, agent, owner) = disputed(&server);
    ok(&agent, "CREATE fact:c2 SET text = 'second', grade = 'INFERRED';");
    refused(&agent, "RELATE fact:c2->attack->fact:claim SET why = 'no verdict';");
    refused(
        &agent,
        "RELATE fact:c2->attack->fact:claim SET verdict = 'bad', why = 'not a verdict';",
    );
    refused(
        &agent,
        "RELATE fact:c2->attack->fact:claim SET verdict = 'unjudged', why = '  ';",
    );
    refused(
        &agent,
        "RELATE fact:c2->attack->fact:c2 SET verdict = 'unjudged', why = 'itself';",
    );
    refused(
        &agent,
        "RELATE fact:c2->attack->fact:claim SET verdict = 'unjudged', why = 'forged', written_by = member:owner1;",
    );
    refused(
        &agent,
        "RELATE fact:c2->attack->fact:claim SET verdict = 'damaging', why = 'no ruling';",
    );
    ok(
        &agent,
        "RELATE fact:c2->attack->fact:claim SET verdict = 'damaging', why = 'the fix is not enough', ruling = 'the gap holds';",
    );
    refused(
        &agent,
        "RELATE fact:c2->attack->fact:claim SET verdict = 'unjudged', why = 'same pair twice';",
    );
    ok(&owner, "CREATE fact:c3 SET text = 'third', grade = 'INFERRED';");
    ok(
        &owner,
        "RELATE fact:c3->attack->fact:claim SET verdict = 'survivable', why = 'owner attacks too', ruling = 'minor';",
    );
}

#[test]
fn a_verdict_is_set_once_and_a_judged_attack_is_frozen() {
    let server = Throwaway::start();
    let (root, agent, owner) = disputed(&server);
    // A verdict comes with its ruling, and an unjudged attack has none.
    refused(&agent, "UPDATE attack SET verdict = 'fatal' WHERE in = fact:hit;");
    refused(
        &agent,
        "UPDATE attack SET verdict = 'fatal', ruling = ' ' WHERE in = fact:hit;",
    );
    refused(&agent, "UPDATE attack SET ruling = 'early words' WHERE in = fact:hit;");
    // From unjudged, once.
    ok(
        &agent,
        "UPDATE attack SET verdict = 'fatal', ruling = 'the gap holds' WHERE in = fact:hit;",
    );
    let judged = value(
        &root,
        "SELECT verdict, ruling, <string> judged_by AS by, judged_at != NONE AS when FROM ONLY attack WHERE in = fact:hit LIMIT 1;",
    );
    assert_eq!(
        judged,
        serde_json::json!({"verdict": "fatal", "ruling": "the gap holds", "by": "member:agent1", "when": true})
    );
    for who in [&agent, &owner, &root] {
        refused(who, "UPDATE attack SET verdict = 'survivable' WHERE in = fact:hit;");
        refused(
            who,
            "UPDATE attack SET verdict = 'unjudged', ruling = NONE WHERE in = fact:hit;",
        );
        refused(who, "UPDATE attack SET ruling = 'softer words' WHERE in = fact:hit;");
        refused(who, "UPDATE attack SET why = 'other words' WHERE in = fact:hit;");
        refused(who, "UPDATE attack SET lens = 'a' WHERE in = fact:hit;");
        // SurrealDB keeps a relation's ends whatever an UPDATE says (measured on 3.1.6): accepted, unchanged.
        let _ = who.try_sql("UPDATE attack SET out = fact:other, in = fact:other WHERE in = fact:hit;");
    }
    assert_eq!(
        value(
            &root,
            "SELECT <string> in AS in, <string> out AS out FROM attack WHERE verdict = 'fatal';"
        ),
        serde_json::json!([{"in": "fact:hit", "out": "fact:claim"}]),
        "an attack cannot be re-pointed"
    );
    // Who judged and when are the store's: a write naming someone else changes nothing.
    ok(
        &root,
        "UPDATE attack SET judged_by = member:owner1, judged_at = d'2020-01-01T00:00:00Z' WHERE in = fact:hit;",
    );
    assert_eq!(
        judged,
        value(
            &root,
            "SELECT verdict, ruling, <string> judged_by AS by, judged_at != NONE AS when FROM ONLY attack WHERE in = fact:hit LIMIT 1;",
        )
    );
    assert_eq!(
        value(
            &root,
            "SELECT VALUE judged_at > d'2026-01-01T00:00:00Z' FROM ONLY attack WHERE in = fact:hit LIMIT 1;"
        ),
        true
    );
    // The owner judges an attack too, and an unjudged attack has no judge.
    ok(&owner, "CREATE fact:c2 SET text = 'second', grade = 'INFERRED';");
    ok(
        &owner,
        "RELATE fact:c2->attack->fact:claim SET verdict = 'unjudged', why = 'first words';",
    );
    assert_eq!(
        value(
            &root,
            "SELECT VALUE judged_by FROM ONLY attack WHERE in = fact:c2 LIMIT 1;"
        ),
        serde_json::Value::Null,
    );
    ok(
        &owner,
        "UPDATE attack SET verdict = 'survivable', ruling = 'costs a frame' WHERE in = fact:c2;",
    );
    assert_eq!(
        value(
            &root,
            "SELECT VALUE <string> judged_by FROM ONLY attack WHERE in = fact:c2 LIMIT 1;"
        ),
        "member:owner1"
    );
}

#[test]
fn nobody_deletes_an_attack_or_an_answer() {
    let server = Throwaway::start();
    let (root, agent, owner) = disputed(&server);
    ok(&agent, "CREATE fact:sorry SET text = 'conceded', grade = 'INFERRED';");
    ok(
        &agent,
        "RELATE fact:sorry->answer->(SELECT VALUE id FROM ONLY attack WHERE in = fact:hit LIMIT 1) SET outcome = 'conceded';",
    );
    for who in [&agent, &owner] {
        refused(who, "DELETE attack WHERE in = fact:hit;");
        refused(who, "DELETE answer WHERE in = fact:sorry;");
        refused(who, "DELETE fact:hit;");
    }
    assert_eq!(
        value(
            &root,
            "RETURN [count(SELECT id FROM attack), count(SELECT id FROM answer)];"
        ),
        serde_json::json!([1, 1])
    );
}

#[test]
fn an_answer_is_written_once_with_what_its_outcome_needs() {
    let server = Throwaway::start();
    let (root, agent, _) = disputed(&server);
    let attack = "(SELECT VALUE id FROM ONLY attack WHERE in = fact:hit LIMIT 1)";
    let answer = |by: &str, set: &str| format!("RELATE fact:{by}->answer->{attack} SET {set};");
    ok(&agent, "CREATE fact:bare SET text = 'no evidence', grade = 'INFERRED';");
    ok(&root, "CREATE unit:u1 SET locator = 'src/a.rs#L1-2', hash = 'h1';");
    ok(
        &agent,
        "CREATE fact:quoted SET text = 'the code says otherwise', grade = 'INFERRED';",
    );
    ok(&agent, "RELATE fact:quoted->evidence->unit:u1;");
    ok(
        &agent,
        "CREATE receipt:red SET command = 'cargo test', machine = 'M', location = '.', exit_code = 1, ok = false, output_hash = 'x';",
    );
    ok(
        &agent,
        "CREATE receipt:green SET command = 'cargo test', machine = 'M', location = '.', exit_code = 0, ok = true, output_hash = 'y';",
    );
    ok(
        &agent,
        "CREATE fact:tried SET text = 'fix attempt', grade = 'INFERRED', receipt = receipt:red;",
    );
    ok(
        &agent,
        "CREATE fact:fixed SET text = 'fixed in abc', grade = 'INFERRED', receipt = receipt:green;",
    );
    ok(
        &agent,
        "CREATE fact:narrow SET text = 'holds for confirmed frames only', grade = 'INFERRED';",
    );

    refused(&agent, &answer("bare", "outcome = 'maybe'"));
    refused(&agent, &answer("bare", "outcome = 'rebutted'"));
    refused(&agent, &answer("tried", "outcome = 'fixed'"));
    refused(&agent, &answer("bare", "outcome = 'narrowed'"));
    refused(
        &agent,
        &answer("bare", "outcome = 'narrowed', replaced_by = fact:claim"),
    );
    refused(&agent, &answer("hit", "outcome = 'conceded'"));
    ok(&agent, &answer("quoted", "outcome = 'rebutted'"));
    ok(&agent, &answer("fixed", "outcome = 'fixed'"));
    ok(
        &agent,
        &answer("bare", "outcome = 'narrowed', replaced_by = fact:narrow"),
    );
    refused(&agent, &answer("bare", "outcome = 'conceded'"));
    ok(&agent, &answer("narrow", "outcome = 'conceded'"));
    refused(
        &agent,
        &answer("tried", "outcome = 'conceded', written_by = member:owner1"),
    );

    // Written once: no field of an answer changes, for anyone.
    refused(&agent, "UPDATE answer SET outcome = 'conceded' WHERE in = fact:quoted;");
    refused(&root, "UPDATE answer SET outcome = 'conceded' WHERE in = fact:quoted;");
    refused(
        &root,
        "UPDATE answer SET replaced_by = fact:fixed WHERE in = fact:bare;",
    );
    // An answer is a fact: it can be attacked in its turn.
    ok(
        &agent,
        "CREATE fact:counter SET text = 'the quote is out of date', grade = 'INFERRED', attacks = fact:quoted, stance = 'disputes';",
    );
    assert_eq!(
        value(&root, "RETURN count(SELECT id FROM attack WHERE out = fact:quoted);"),
        1
    );
}

/// Disputes already on record when 0018 runs become unjudged attacks; supports do not; running it twice
/// changes nothing.
#[test]
fn the_backfill_turns_old_disputes_into_unjudged_attacks() {
    let server = Throwaway::start();
    let root = server.root("proj_old");
    ensure(&root, "proj_old").unwrap();
    let before: Vec<(&str, &str)> = SCHEMA.iter().copied().filter(|(f, _)| f < &"0018").collect();
    migrate(&root, Scope::Project, &before, true).unwrap();
    ok(
        &root,
        "CREATE fact:t SET text = 'target', grade = 'INFERRED', lens = 'a';",
    );
    ok(
        &root,
        "CREATE fact:d SET text = 'dispute', grade = 'INFERRED', lens = 'b', attacks = fact:t, stance = 'disputes';",
    );
    ok(
        &root,
        "CREATE fact:s SET text = 'support', grade = 'INFERRED', lens = 'c', attacks = fact:t, stance = 'supports';",
    );
    // Before 0018 a dispute could be withdrawn by an edit; this is the hole being closed.
    ok(
        &root,
        "CREATE fact:gone SET text = 'withdrawn', grade = 'INFERRED', attacks = fact:t, stance = 'disputes';",
    );
    ok(&root, "UPDATE fact:gone SET attacks = NONE, stance = NONE;");
    migrate(&root, Scope::Project, SCHEMA, true).unwrap();
    let edges = value(&root, "SELECT <string> in AS in, verdict, lens FROM attack;");
    assert_eq!(
        edges,
        serde_json::json!([{"in": "fact:d", "verdict": "unjudged", "lens": "b"}])
    );
    assert!(migrate(&root, Scope::Project, SCHEMA, true).unwrap().is_empty());
}
