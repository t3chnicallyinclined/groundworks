//! The judgement cache's rules in the schema (0021; idea-bridges amendment A6.3, slice 14), both ways, against a
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

const A: &str = "1111111111111111111111111111111111111111111111111111111111111111";
const B: &str = "2222222222222222222222222222222222222222222222222222222222222222";

fn judgement(id: &str, qa: &str, qb: &str, verdict: &str, grade: &str) -> String {
    format!(
        "CREATE judgement:{id} SET check = 'novelty', prompt_version = 'funnel-1', quote_hash_a = '{qa}', \
         quote_hash_b = '{qb}', round = 'first', verdict = '{verdict}', confidence = 0.9, borderline = false, \
         why = 'read', grade = '{grade}', target_card = 't', source_project = 'beta', source_card = 's';"
    )
}

#[test]
fn a_judgement_is_written_once_and_never_above_inferred() {
    let server = Throwaway::start();
    server.estate();
    let agent = server.agent("proj_a");
    ok(&agent, &judgement("j1", A, B, "not_made", "INFERRED"));
    // Never above INFERRED; UNKNOWN for an unknown verdict.
    refused(&agent, &judgement("j2", A, B, "not_made", "CONFIRMED"));
    refused(&agent, &judgement("j3", A, B, "unknown", "INFERRED"));
    ok(&agent, &judgement("j4", A, B, "unknown", "UNKNOWN"));
    // The quote hashes are sorted (one key per pair) and are hashes, not text.
    refused(&agent, &judgement("j5", B, A, "not_made", "INFERRED"));
    refused(&agent, &judgement("j6", "the quoted text", B, "not_made", "INFERRED"));
    // Written once: no second write to the same key, no update, no delete.
    refused(&agent, &judgement("j1", A, B, "made", "INFERRED"));
    refused(&agent, "UPDATE judgement:j1 SET verdict = 'made';");
    refused(&agent, "DELETE judgement:j1;");
}
