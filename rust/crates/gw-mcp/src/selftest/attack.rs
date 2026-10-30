//! The attack checks (idea-bridges design 2026-10-30, amendment A2, slice 10; schema 0018), both ways, in the
//! selftest's throwaway home and store: a verdict is required, answers are rows with what their outcome needs,
//! nothing deletes an attack or an answer, an attack cannot be withdrawn by editing its fact, and verdicts,
//! rulings and answers are written once. Each rule is checked through the tools and, where a tool would refuse
//! first, straight against the store with the agent's (and the owner's) own login. Append-only, like the rest
//! of the selftest.

use std::path::Path;

use anyhow::Result;
use serde_json::{Value, json};

use gw_ops::store::Store;
use gw_store::testkit::OWNER_PASS;

use super::{Checks, Client, receipt_in};

/// The first `<table>:<key>` in `text`.
fn id_in(text: &str, table: &str) -> Option<String> {
    let prefix = format!("{table}:");
    text.match_indices(&prefix).find_map(|(at, _)| {
        let key: String = text[at + prefix.len()..]
            .chars()
            .take_while(|c| c.is_ascii_alphanumeric() || *c == '_')
            .collect();
        (!key.is_empty()).then(|| format!("{prefix}{key}"))
    })
}

/// Whether the store refused `statement`: a rule's error, or PERMISSIONS turning the write into nothing
/// (gw-core reports that as "refused by datastore permissions"). A macro, so this crate needs no gw-core.
macro_rules! refused {
    ($db:expr, $statement:expr $(,)?) => {{
        let statement: &str = &$statement;
        match $db.try_sql(statement) {
            // A statement that did not parse proves nothing (an id the fixture failed to make, say).
            Ok((false, message)) if message.contains("Parse error") => (false, message),
            Ok((false, message)) => (true, message),
            Ok((true, _)) => (false, format!("accepted: {statement}")),
            Err(e) if e.to_string().contains("refused by datastore permissions") => (true, e.to_string()),
            Err(e) => (false, format!("{statement}: {e}")),
        }
    }};
}

pub(super) fn attack_checks(exe: &Path, base: &Path, home: &Path, owner: &Store, c: &mut Checks) -> Result<()> {
    let (home_env, experts_env, claude_env) = (
        home.display().to_string(),
        base.join("experts").display().to_string(),
        base.join("claude").display().to_string(),
    );
    let mut client = Client::start(
        exe,
        &[
            ("GROUNDWORKS_HOME", &home_env),
            ("GROUNDWORKS_MACHINE", "SELFTEST"),
            ("GROUNDWORKS_EXPERTS", &experts_env),
            ("GROUNDWORKS_CLAUDE_DIR", &claude_env),
        ],
        &["GROUNDWORKS_START"],
    )?;
    let s = &mut client;
    let agent = owner.agent("proj_demo")?;
    let owner_db = owner.owner("proj_demo", OWNER_PASS)?;

    let note = |text: &str| {
        json!({"project": "demo", "kind": "fact", "text": text, "source": "selftest", "confidence": "INFERRED",
               "by": "selftest", "lens": "selftest-expert"})
    };
    let (_, text) = s.call("journal_add", note("the cache never evicts an entry"))?;
    let target = id_in(&text, "fact").unwrap_or_default();
    let attack = |verdict: Option<&str>, ruling: Option<&str>, text: &str| {
        let mut a = json!({"project": "demo", "target": target, "text": text, "source": "notes.txt",
                           "confidence": "INFERRED", "lens": "selftest-reviewer", "by": "selftest"});
        if let Some(v) = verdict {
            a["verdict"] = json!(v);
        }
        if let Some(r) = ruling {
            a["ruling"] = json!(r);
        }
        a
    };

    // ---- a verdict is required ----------------------------------------------------------------------------
    let (err, text) = s.call(
        "attack_add",
        attack(None, None, "evictions happen under memory pressure"),
    )?;
    c.check("attack: attack_add without a verdict is refused", err, &text);
    let (err, text) = s.call(
        "attack_add",
        attack(Some("minor"), Some("small"), "evictions happen under memory pressure"),
    )?;
    c.check(
        "attack: a verdict outside fatal, damaging, survivable, unjudged is refused",
        err && text.contains("not one of"),
        &text,
    );
    let (err, text) = s.call(
        "attack_add",
        attack(Some("fatal"), None, "evictions happen under memory pressure"),
    )?;
    c.check(
        "attack: a fatal verdict without its ruling is refused",
        err && text.contains("needs its ruling"),
        &text,
    );
    let (_, text) = s.call("journal_add", note("a second claim to attack in the store"))?;
    let other = id_in(&text, "fact").unwrap_or_default();
    let (no, detail) = refused!(
        &agent,
        &format!("RELATE {other}->attack->{target} SET why = 'no verdict given';"),
    );
    c.check(
        "attack: the store refuses an attack edge with no verdict (agent login)",
        no,
        &detail,
    );

    let mut damaging = attack(
        Some("damaging"),
        Some("the parser path shows the cache is bypassed, not evicted"),
        "the demo is a selftest fixture, so no cache runs in it",
    );
    damaging["quote"] = json!({"file": "README.md", "line": 3, "text": "A demo project for the selftest."});
    let (err, text) = s.call("attack_add", damaging)?;
    let damaging_attack = id_in(&text, "attack");
    c.check(
        "attack: attack_add records the attack with its verdict and ruling",
        !err && damaging_attack.is_some() && text.contains("DAMAGING") && text.contains("ruling"),
        &text,
    );

    // ---- every dispute is an attack, unjudged until judged -------------------------------------------------
    let mut dispute = note("entries are evicted after an hour");
    dispute["lens"] = json!("selftest-reviewer");
    dispute["attacks"] = json!(target);
    dispute["stance"] = json!("disputes");
    let (_, text) = s.call("journal_add", dispute)?;
    let disputer = id_in(&text, "fact").unwrap_or_default();
    let (err, text) = s.call("attack_list", json!({"project": "demo", "of": disputer}))?;
    let unjudged = id_in(&text, "attack");
    c.check(
        "attack: a dispute recorded with journal_add is an UNJUDGED attack, read as fatal",
        !err && unjudged.is_some() && text.contains("UNJUDGED (read as fatal until judged)"),
        &text,
    );
    let unjudged = unjudged.unwrap_or_default();
    let judge = |verdict: &str, ruling: &str| json!({"project": "demo", "attack": unjudged, "verdict": verdict, "ruling": ruling});
    let (err, text) = s.call("attack_judge", judge("fatal", "an hour-old entry is still served"))?;
    c.check(
        "attack: attack_judge sets the verdict with its ruling",
        !err && text.contains("FATAL") && text.contains("an hour-old entry is still served"),
        &text,
    );
    let (err, text) = s.call("attack_judge", judge("survivable", "on second thought"))?;
    c.check(
        "attack: a verdict is set once: judging again is refused",
        err && text.contains("set once"),
        &text,
    );

    // ---- append-only: a judged attack is frozen, in the store as well --------------------------------------
    let (no, detail) = refused!(&agent, &format!("UPDATE {unjudged} SET verdict = 'survivable';"),);
    c.check("attack: the agent login cannot lower a judged verdict", no, &detail);
    let (no, detail) = refused!(&agent, &format!("UPDATE {unjudged} SET ruling = 'softer words';"),);
    c.check("attack: the agent login cannot re-word a ruling", no, &detail);
    let (no, detail) = refused!(
        &owner_db,
        &format!("UPDATE {unjudged} SET verdict = 'unjudged', ruling = NONE;"),
    );
    c.check("attack: the owner login cannot take a verdict back", no, &detail);

    // ---- no withdrawal --------------------------------------------------------------------------------------
    let (no, detail) = refused!(&agent, &format!("UPDATE {disputer} SET attacks = NONE;"));
    c.check(
        "attack: no withdrawal: UPDATE fact SET attacks = NONE is refused (agent login)",
        no,
        &detail,
    );
    let (no, detail) = refused!(&agent, &format!("UPDATE {disputer} SET stance = 'supports';"));
    c.check("attack: no withdrawal: a dispute cannot become a support", no, &detail);
    let (no, detail) = refused!(&owner_db, &format!("UPDATE {disputer} SET attacks = NONE;"));
    c.check("attack: no withdrawal by the owner login either", no, &detail);

    // ---- no delete -----------------------------------------------------------------------------------------
    let (no_agent, a) = refused!(&agent, &format!("DELETE {unjudged} RETURN BEFORE;"));
    let (no_owner, o) = refused!(&owner_db, &format!("DELETE {unjudged} RETURN BEFORE;"));
    c.check(
        "attack: nobody deletes an attack (agent and owner logins)",
        no_agent && no_owner,
        &format!("{a} / {o}"),
    );

    // ---- answers --------------------------------------------------------------------------------------------
    let damaging_attack = damaging_attack.unwrap_or_default();
    let answer = |outcome: &str, text: &str| {
        json!({"project": "demo", "attack": damaging_attack, "outcome": outcome, "text": text,
               "source": "selftest", "confidence": "INFERRED", "lens": "selftest-expert", "by": "selftest"})
    };
    let (err, text) = s.call("answer_add", answer("rebutted", "the cache is read first"))?;
    c.check(
        "answer: rebutted without a quote is refused",
        err && text.contains("quote"),
        &text,
    );
    let (_, text) = s.call("journal_add", note("an answer with nothing behind it"))?;
    let bare = id_in(&text, "fact").unwrap_or_default();
    let (no, detail) = refused!(
        &agent,
        &format!("RELATE {bare}->answer->{damaging_attack} SET outcome = 'rebutted';"),
    );
    c.check(
        "answer: the store refuses rebutted from a fact with no evidence (agent login)",
        no,
        &detail,
    );
    let mut rebut = answer("rebutted", "the guide says confirmed frames are resimulated");
    rebut["quote"] =
        json!({"file": "docs/GUIDE.md", "line": 3, "text": "Rollback netcode resimulates confirmed frames."});
    let (err, text) = s.call("answer_add", rebut)?;
    let rebutter = id_in(text.split("by ").nth(1).unwrap_or_default(), "fact");
    c.check(
        "answer: rebutted with a word-for-word quote is recorded as a row on the attack",
        !err && text.contains("answer: rebutted by fact:"),
        &text,
    );
    let (_, text) = s.call(
        "journal_add",
        note("the cache never evicts an entry it served in the last minute"),
    )?;
    let narrower = id_in(&text, "fact").unwrap_or_default();
    let mut narrow = answer("narrowed", "true for the last minute only");
    narrow["replaced_by"] = json!(narrower);
    let (err, text) = s.call("answer_add", narrow)?;
    c.check(
        "answer: narrowed names the narrower claim",
        !err && text.contains("answer: narrowed by fact:") && text.contains(&format!("-> {narrower}")),
        &text,
    );
    let (_, text) = s.call("run", json!({"project": "demo", "env": "local", "command": "exit 3"}))?;
    let red = receipt_in(&text);
    let mut fixed = answer("fixed", "fixed, but the test failed");
    fixed["receipt"] = json!(red);
    let (err, text) = s.call("answer_add", fixed)?;
    c.check(
        "answer: fixed with a receipt that did not run green is refused",
        red.is_some() && err && text.contains("receipt that ran green"),
        &text,
    );
    let (_, text) = s.call(
        "run",
        json!({"project": "demo", "env": "local", "command": "echo evicted"}),
    )?;
    let mut fixed = answer("fixed", "eviction added; the run prints evicted");
    fixed["receipt"] = json!(receipt_in(&text));
    let (err, text) = s.call("answer_add", fixed)?;
    c.check(
        "answer: fixed with a green receipt is recorded",
        !err && text.contains("answer: fixed by fact:"),
        &text,
    );
    let (err, text) = s.call(
        "answer_add",
        json!({"project": "demo", "attack": damaging_attack, "outcome": "conceded", "fact": narrower}),
    )?;
    c.check("answer: conceded by a fact already recorded is recorded", !err, &text);
    let (err, text) = s.call(
        "answer_add",
        json!({"project": "demo", "attack": damaging_attack, "outcome": "conceded", "fact": narrower}),
    )?;
    c.check(
        "answer: an answer is written once: the same fact answering again is refused",
        err && text.contains("written once"),
        &text,
    );
    let rebutter = rebutter.unwrap_or_default();
    let ids = [
        &target,
        &other,
        &disputer,
        &unjudged,
        &damaging_attack,
        &bare,
        &narrower,
        &rebutter,
    ];
    c.check(
        "attack: the fixture made every fact and attack the store probes name",
        ids.iter().all(|id| !id.is_empty()),
        &format!("{ids:?}"),
    );
    let (no, detail) = refused!(
        &agent,
        &format!("UPDATE answer SET outcome = 'conceded' WHERE in = {rebutter};"),
    );
    c.check("answer: the agent login cannot edit an answer", no, &detail);
    let (no_agent, a) = refused!(&agent, &format!("DELETE answer WHERE in = {rebutter} RETURN BEFORE;"));
    let (no_owner, o) = refused!(
        &owner_db,
        &format!("DELETE answer WHERE in = {rebutter} RETURN BEFORE;")
    );
    c.check(
        "answer: nobody deletes an answer (agent and owner logins)",
        no_agent && no_owner,
        &format!("{a} / {o}"),
    );

    let (err, text) = s.call(
        "attack_add",
        json!({"project": "demo", "target": rebutter, "text": "the guide is about frames, not the cache",
               "source": "docs/GUIDE.md", "confidence": "INFERRED", "lens": "selftest-reviewer",
               "verdict": "survivable", "ruling": "it still shows the read path", "by": "selftest"}),
    )?;
    c.check(
        "answer: an answer is a fact, so it can be attacked in its turn",
        !err && text.contains("SURVIVABLE"),
        &text,
    );

    let (err, text) = s.call("attack_list", json!({"project": "demo", "of": target}))?;
    let answers = text.matches("    answer: ").count();
    c.check(
        "attack: attack_list shows the attacks on a fact with verdicts and every answer",
        !err && text.starts_with("attacks 2: fatal 1, damaging 1, survivable 0, unjudged 0") && answers == 4,
        &text,
    );
    let rows = agent.sql(
        "RETURN [count(SELECT id FROM attack), count(SELECT id FROM answer)];",
        Some(1),
    );
    let counts = rows.ok().and_then(|r| r.into_iter().next()).unwrap_or(Value::Null);
    c.check(
        "attack: everything written stayed: 4 attacks and 4 answers in the store",
        counts == json!([4, 4]),
        &counts.to_string(),
    );
    Ok(())
}
