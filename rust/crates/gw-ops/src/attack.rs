//! Attacks with verdicts, and answers (idea-bridges design 2026-10-30, amendment A2, slice 10; schema 0018).
//!
//! An attack is an edge from the attacking fact to the fact it attacks, and its outcome lives on the edge
//! (METHOD s2.6). The schema holds every rule; this module gives the tools plain refusals first:
//! - a verdict is required: `fatal`, `damaging`, `survivable`, or `unjudged` (read as fatal until judged);
//! - a verdict is set once, from `unjudged`, with its ruling, and is never lowered, raised or withdrawn
//!   afterwards: a fresh attack reopens a question, a changed verdict does not;
//! - an attack cannot be withdrawn: the attacking fact's `attacks` and `stance` are read-only, and nobody
//!   (owner or agent) deletes an attack or an answer;
//! - an answer is written once and says how the attack was met: `rebutted` (the answering fact holds a quote),
//!   `fixed` (it holds a receipt that ran green), `narrowed` (`replaced_by` names the narrower claim), or
//!   `conceded`.
//!
//! Every dispute is an attack: `attack_add` writes the attacking fact through the same store path as
//! `journal_add`, and schema 0018's event puts the edge on it as `unjudged`; a dispute recorded with
//! `journal_add` gets the same edge. The ready gate that reads these edges is slice 11; `journal_add` learns
//! verdicts in slice 19, after Phase 4 merges.

use std::fmt::Write as _;

use anyhow::{Context, Result, bail, ensure};
use schemars::JsonSchema;
use serde::Deserialize;

use crate::home::Home;
use crate::journal::{Confidence, Kind};
use crate::registry::Registry;
use crate::store::{self, FactIn, Quote, Store, Vars};

pub const VERDICTS: [&str; 4] = ["fatal", "damaging", "survivable", "unjudged"];
pub const OUTCOMES: [&str; 4] = ["rebutted", "fixed", "narrowed", "conceded"];

/// A new attack: the attacking fact (written like a journal entry) and its verdict.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct AttackIn {
    /// The fact attacked (`fact:...`).
    pub target: String,
    /// The attack itself, in one or two sentences: what is wrong with the target.
    pub text: String,
    /// Where it comes from: file:line, the command and what it printed, or a URL.
    pub source: String,
    pub confidence: Confidence,
    /// The lens (expert) attacking, e.g. gw-security.
    pub lens: Option<String>,
    /// Text that must appear word for word in a project file; checked before recording.
    pub quote: Option<Quote>,
    /// A receipt id from run or gate (`receipt:...`) behind the attack.
    pub receipt: Option<String>,
    /// fatal, damaging, survivable, or unjudged (read as fatal until judged). Required.
    pub verdict: String,
    /// Why this verdict; required with fatal, damaging or survivable, absent with unjudged.
    pub ruling: Option<String>,
}

/// An answer to an attack: by a fact already recorded, or by a new one written here.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct AnswerIn {
    /// The attack answered (`attack:...`, or the attacking `fact:...`).
    pub attack: String,
    /// rebutted (needs a quote), fixed (needs a receipt that ran green), narrowed (needs replaced_by) or
    /// conceded.
    pub outcome: String,
    /// The narrower claim (`fact:...`) that replaces the attacked one, for narrowed.
    pub replaced_by: Option<String>,
    /// An answering fact already recorded (`fact:...`); otherwise give text, source and confidence.
    pub fact: Option<String>,
    pub text: Option<String>,
    pub source: Option<String>,
    pub confidence: Option<Confidence>,
    pub lens: Option<String>,
    pub quote: Option<Quote>,
    pub receipt: Option<String>,
}

/// The key of `<table>:<key>`, letters, digits and `_` only.
fn key<'a>(id: &'a str, table: &str) -> Result<&'a str> {
    let key = id.trim();
    let key = key.strip_prefix(table).and_then(|k| k.strip_prefix(':')).unwrap_or(key);
    ensure!(
        !key.is_empty() && key.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'),
        "{id:?} is not a {table} id ({table}:<letters and digits>)"
    );
    Ok(key)
}

fn verdict_ok(verdict: &str, ruling: Option<&str>) -> Result<()> {
    ensure!(
        VERDICTS.contains(&verdict),
        "verdict {verdict:?} is not one of fatal, damaging, survivable, unjudged"
    );
    let ruling = ruling.map(str::trim).filter(|r| !r.is_empty());
    match (verdict, ruling) {
        ("unjudged", Some(_)) => bail!("an unjudged attack has no ruling yet; judge it with attack_judge"),
        ("unjudged", None) => {}
        (_, None) => bail!("a {verdict} verdict needs its ruling: why the attack is {verdict}"),
        (_, Some(r)) => crate::refusal::check_text("ruling", r)?,
    }
    Ok(())
}

fn db(store: &Store, project: &str) -> Result<std::sync::Arc<gw_core::db::Db>> {
    store.agent(&gw_store::project_ns(&store::slug(project)?)?)
}

/// `attack:...` for an attack id or the attacking `fact:...` (refused when that fact attacks more than once).
fn resolve(store: &Store, project: &str, id: &str) -> Result<String> {
    if id.trim().starts_with("fact:") {
        let vars = Vars::default().set("k", key(id, "fact")?);
        let db = db(store, project)?;
        let rows = store.retrying(|| {
            Ok(db.query(
                "SELECT VALUE <string> id FROM attack WHERE in = type::record('fact', $k);",
                &vars.0,
                Some(1),
            )?)
        })?;
        let found: Vec<String> = serde_json::from_value(rows[0].clone()).context("reading attacks")?;
        return match found.as_slice() {
            [one] => Ok(one.clone()),
            [] => bail!("{id} attacks nothing (a dispute, or attack_add, makes an attack; a support does not)"),
            _ => bail!("{id} is on {} attacks; name the one: {}", found.len(), found.join(", ")),
        };
    }
    Ok(format!("attack:{}", key(id, "attack")?))
}

/// Record an attack: the attacking fact (refusals and quotes as for journal_add), its edge, and its verdict.
pub fn add(home: &Home, store: &Store, project: &str, a: &AttackIn, by: &str) -> Result<String> {
    let verdict = a.verdict.trim();
    verdict_ok(verdict, a.ruling.as_deref())?;
    key(&a.target, "fact")?;
    let registry = Registry::load(home)?;
    let project = registry.project(project)?;
    let fact = FactIn {
        kind: Kind::Fact,
        text: a.text.clone(),
        confidence: a.confidence,
        source: a.source.clone(),
        by: by.to_owned(),
        receipt: a.receipt.clone(),
        owner_statement: None,
        quote: a.quote.clone(),
        lens: a.lens.clone(),
        attacks: Some(format!("fact:{}", key(&a.target, "fact")?)),
        stance: Some("disputes".to_owned()),
    };
    let dir = project.path(&home.workspace);
    let fact_id = store.add_fact(&dir, &project.name, &fact).inspect_err(|e| {
        crate::ops::record_refusal(store, "attack_add", &project.name, by, e);
    })?;
    let attack = resolve(store, &project.name, &fact_id)
        .with_context(|| format!("{fact_id} was recorded but its attack edge was not found"))?;
    if verdict != "unjudged" {
        judge(
            store,
            &project.name,
            &attack,
            verdict,
            a.ruling.as_deref().unwrap_or_default(),
        )
        .with_context(|| {
            format!(
                "{fact_id} and {attack} were recorded, UNJUDGED (read as fatal until judged); the verdict was refused"
            )
        })?;
    }
    Ok(format!(
        "recorded {attack} by {fact_id}\n{}",
        list(store, &project.name, Some(&attack), 1)?
    ))
}

/// Set an attack's verdict, once, with its ruling.
pub fn judge(store: &Store, project: &str, attack: &str, verdict: &str, ruling: &str) -> Result<String> {
    ensure!(
        verdict != "unjudged",
        "judging sets fatal, damaging or survivable; an attack starts unjudged"
    );
    verdict_ok(verdict, Some(ruling))?;
    let attack = resolve(store, project, attack)?;
    let db = db(store, project)?;
    let vars = Vars::default()
        .set("k", key(&attack, "attack")?)
        .set("verdict", verdict)
        .set("ruling", ruling.trim());
    let now = store.retrying(|| {
        Ok(db.query(
            "SELECT VALUE verdict FROM ONLY type::record('attack', $k);",
            &vars.0,
            Some(1),
        )?)
    })?;
    match now[0].as_str() {
        None => bail!("{attack} does not exist in {project}"),
        Some("unjudged") => {}
        Some(set) => bail!(
            "{attack} already has verdict {}: a verdict is set once and never changed or withdrawn; a new attack \
             reopens the question",
            set.to_uppercase()
        ),
    }
    store.retrying(|| {
        Ok(db.query(
            "UPDATE type::record('attack', $k) SET verdict = $verdict, ruling = $ruling;",
            &vars.0,
            Some(1),
        )?)
    })?;
    Ok(format!("judged {attack}\n{}", list(store, project, Some(&attack), 1)?))
}

/// Answer an attack, once, by a fact already recorded or by a new one.
pub fn answer(home: &Home, store: &Store, project: &str, a: &AnswerIn, by: &str) -> Result<String> {
    let outcome = a.outcome.trim();
    ensure!(
        OUTCOMES.contains(&outcome),
        "outcome {outcome:?} is not one of rebutted, fixed, narrowed, conceded"
    );
    let registry = Registry::load(home)?;
    let project = registry.project(project)?;
    let attack = resolve(store, &project.name, &a.attack)?;
    let replaced_by = a.replaced_by.as_deref().map(|r| key(r, "fact")).transpose()?;
    match (outcome, replaced_by.is_some()) {
        ("narrowed", false) => {
            bail!("narrowed needs replaced_by: the narrower claim (fact:...) that replaces the attacked one")
        }
        ("narrowed", true) => {}
        (_, true) => bail!("replaced_by goes with narrowed only"),
        _ => {}
    }
    let fact_id = match (&a.fact, &a.text) {
        (Some(id), None) => format!("fact:{}", key(id, "fact")?),
        (None, Some(text)) => {
            match outcome {
                "rebutted" if a.quote.is_none() => {
                    bail!("rebutted needs the answering fact's evidence: a quote found word for word")
                }
                "fixed" if a.receipt.is_none() => {
                    bail!("fixed needs the answering fact's receipt from run or gate, ok = true")
                }
                _ => {}
            }
            let fact = FactIn {
                kind: Kind::Fact,
                text: text.clone(),
                confidence: a.confidence.unwrap_or(Confidence::Inferred),
                source: a.source.clone().unwrap_or_default(),
                by: by.to_owned(),
                receipt: a.receipt.clone(),
                owner_statement: None,
                quote: a.quote.clone(),
                lens: a.lens.clone(),
                attacks: None,
                stance: None,
            };
            ensure!(!fact.source.trim().is_empty(), "a new answering fact needs its source");
            let dir = project.path(&home.workspace);
            store.add_fact(&dir, &project.name, &fact).inspect_err(|e| {
                crate::ops::record_refusal(store, "answer_add", &project.name, by, e);
            })?
        }
        _ => bail!("give the answering fact: fact (one already recorded) or text and source (a new one), not both"),
    };
    let db = db(store, &project.name)?;
    let mut vars = Vars::default()
        .set("f", key(&fact_id, "fact")?)
        .set("a", key(&attack, "attack")?)
        .set("outcome", outcome);
    let mut set = String::from("outcome = $outcome");
    if let Some(r) = replaced_by {
        vars = vars.set("r", r);
        set.push_str(", replaced_by = type::record('fact', $r)");
    }
    let sql = format!(
        "LET $from = type::record('fact', $f); LET $to = type::record('attack', $a); \
         RELATE $from->answer->$to SET {set} RETURN VALUE <string> id;"
    );
    store
        .retrying(|| Ok(db.query(&sql, &vars.0, None)?))
        .map_err(|e| anyhow::anyhow!(refused_answer(outcome, &format!("{e:#}"))))
        .with_context(|| format!("{fact_id} answering {attack}"))?;
    Ok(format!(
        "recorded answer {outcome} by {fact_id} to {attack}\n{}",
        list(store, &project.name, Some(&attack), 1)?
    ))
}

/// The plain reason a store refusal of an answer means.
fn refused_answer(outcome: &str, error: &str) -> String {
    let need = match outcome {
        "rebutted" => "the answering fact needs at least one quote (evidence link)",
        "fixed" => "the answering fact needs a receipt that ran green",
        "narrowed" => "replaced_by must be a live fact other than the attacked one",
        _ => "an answer must come from a live fact other than the attacker",
    };
    if error.contains("answer_pair") || error.contains("already contains") {
        "this fact has already answered this attack; an answer is written once".to_owned()
    } else {
        format!("the store refused the answer: {need} ({error})")
    }
}

#[derive(Debug, Deserialize)]
struct Row {
    id: String,
    attacker: String,
    target: String,
    verdict: String,
    ruling: Option<String>,
    lens: Option<String>,
    judged_by: Option<String>,
    text: Option<String>,
    #[serde(default)]
    answers: Vec<AnswerRow>,
}

#[derive(Debug, Deserialize)]
struct AnswerRow {
    by: String,
    outcome: String,
    replaced_by: Option<String>,
}

fn some(v: Option<String>) -> Option<String> {
    v.filter(|s| s != "NONE" && !s.is_empty())
}

fn excerpt(text: &str, max: usize) -> String {
    let one: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
    match one.char_indices().nth(max) {
        Some((i, _)) => format!("{}...", &one[..i]),
        None => one,
    }
}

/// The attacks in a project, on one fact, or one attack, oldest first, with their verdicts and answers.
pub fn list(store: &Store, project: &str, of: Option<&str>, limit: usize) -> Result<String> {
    let db = db(store, project)?;
    let mut vars = Vars::default().set("limit", limit.clamp(1, 500));
    let filter = match of.map(str::trim) {
        Some(id) if id.starts_with("attack:") => {
            vars = vars.set("k", key(id, "attack")?);
            "WHERE id = type::record('attack', $k)"
        }
        Some(id) => {
            vars = vars.set("k", key(id, "fact")?);
            "WHERE out = type::record('fact', $k) OR in = type::record('fact', $k)"
        }
        None => "",
    };
    let sql = format!(
        "SELECT <string> id AS id, <string> in AS attacker, <string> out AS target, verdict, ruling, lens, \
         <string> judged_by AS judged_by, in.text AS text, at, \
         (SELECT <string> in AS by, outcome, <string> replaced_by AS replaced_by, at FROM answer \
          WHERE out = $parent.id ORDER BY at) AS answers \
         FROM attack {filter} ORDER BY at LIMIT $limit;"
    );
    let rows = store.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?))?;
    let rows: Vec<Row> = serde_json::from_value(rows[0].clone()).context("reading attacks")?;
    let mut counts = [0usize; 4];
    let mut out = String::new();
    for r in &rows {
        if let Some(i) = VERDICTS.iter().position(|v| *v == r.verdict) {
            counts[i] += 1;
        }
        let verdict = match r.verdict.as_str() {
            "unjudged" => "UNJUDGED (read as fatal until judged)".to_owned(),
            v => v.to_uppercase(),
        };
        let lens = some(r.lens.clone()).map(|l| format!(" [lens {l}]")).unwrap_or_default();
        let _ = write!(out, "- {} {verdict}: {} attacks {}{lens}", r.id, r.attacker, r.target);
        if let Some(text) = &r.text {
            let _ = write!(out, ": \"{}\"", excerpt(text, 160));
        }
        out.push('\n');
        if let Some(ruling) = some(r.ruling.clone()) {
            let judge = some(r.judged_by.clone()).unwrap_or_else(|| "the loader".to_owned());
            let _ = writeln!(out, "    ruling ({judge}): {}", excerpt(&ruling, 300));
        }
        if r.answers.is_empty() {
            out.push_str("    answers: none\n");
        }
        for a in &r.answers {
            let narrower = some(a.replaced_by.clone())
                .map(|f| format!(" -> {f}"))
                .unwrap_or_default();
            let _ = writeln!(out, "    answer: {} by {}{narrower}", a.outcome, a.by);
        }
    }
    let [fatal, damaging, survivable, unjudged] = counts;
    let head = format!(
        "attacks {}: fatal {fatal}, damaging {damaging}, survivable {survivable}, unjudged {unjudged}\n",
        rows.len()
    );
    Ok(format!("{head}{out}").trim_end().to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_verdict_is_one_of_four_and_comes_with_its_ruling() {
        assert!(verdict_ok("unjudged", None).is_ok());
        assert!(verdict_ok("fatal", Some("the gap holds")).is_ok());
        assert!(verdict_ok("fatal", None).is_err());
        assert!(verdict_ok("fatal", Some("  ")).is_err());
        assert!(verdict_ok("unjudged", Some("early")).is_err());
        assert!(verdict_ok("minor", Some("x")).is_err());
        assert!(verdict_ok("", None).is_err());
    }

    #[test]
    fn ids_are_letters_digits_and_underscores() {
        assert_eq!(key("attack:ab12", "attack").unwrap(), "ab12");
        assert_eq!(key("ab_12", "fact").unwrap(), "ab_12");
        assert!(key("fact:a;DELETE", "fact").is_err());
        assert!(key("attack:", "attack").is_err());
    }

    #[test]
    fn a_long_text_is_cut_on_a_character_boundary() {
        assert_eq!(excerpt("a  b\nc", 10), "a b c");
        assert_eq!(excerpt("ééééé", 3), "ééé...");
    }

    #[test]
    fn an_already_answered_refusal_says_so() {
        assert!(refused_answer("conceded", "Database index `answer_pair` already contains").contains("written once"));
        assert!(refused_answer("rebutted", "assert failed").contains("quote"));
    }
}
