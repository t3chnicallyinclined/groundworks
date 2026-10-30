//! The bridge evaluator (idea-bridges design 2026-10-30 section 5, slice B4). No model.
//!
//! Time split: cards are taken only from rows with `at <= T`; gold is the cross-project links actually made
//! after T: a fact in project P whose text or source cites a fact or a file of project Q, and a passed ask
//! packet for a question about P whose support quote came from a Q candidate. Metric: hit@10 per target
//! problem card (a P problem card made from the P fact the gold starts at). Arms: random, flat (whole card),
//! facet, each compared to flat with the paired sign test (`eval::sign_test`). With fewer than [`MIN_GOLD`]
//! gold links the result is "no evidence", never a pass; then the owner labels a set.
//!
//! A shuffled-gold control runs beside the arms: the same facet ranking scored against gold dealt to the
//! wrong targets must land at chance (the hypergeometric expectation of the ranking it is scored against),
//! or the evaluator is measuring something other than the gold.
//!
//! Reach (gw-security ruling 5 (5)): the evaluator reads through `bridge::reach`, the same function every
//! bridge read uses: P is a shared project, Q a source in P's reach; it signs in as the agent and never as
//! root or the owner, never migrates a namespace (a read-only connection per project), and prints counts
//! for foreign projects, never their text.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;

use anyhow::{Context, Result};
use gw_core::db::Db;
use serde_json::Value;

use crate::bridge::{self, Ranked};
use crate::card::{self, Card};
use crate::home::Home;
use crate::registry::Registry;
use crate::store::{self, Store, Vars};

/// Fewer gold links than this is "no evidence" (design section 5).
pub const MIN_GOLD: usize = 20;
/// hit@K.
pub const K: usize = 10;

/// One cross-project link made after the cutoff.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct Gold {
    /// The project the link was made in, and what it starts at there (a fact key or an ask packet key).
    pub p: String,
    pub anchor_p: String,
    /// The project it reached, and what it cites there (a fact key, a unit key, or a file path).
    pub q: String,
    pub anchor_q: String,
    /// fact or packet.
    pub kind: &'static str,
}

/// `days` before now, UTC, as RFC 3339: the default cutoff.
pub fn days_ago(days: i64) -> String {
    (chrono::Utc::now() - chrono::Duration::days(days))
        .format("%Y-%m-%dT%H:%M:%SZ")
        .to_string()
}

/// A member client for `project` that never creates or migrates its namespace.
fn reader(store: &Store, project: &str) -> Result<Db> {
    let ns = gw_store::project_ns(&store::slug(project)?)?;
    Ok(gw_store::connect_member(
        store.url(),
        &ns,
        store::AGENT,
        &store.agent_pass(&ns),
    )?)
}

fn query(store: &Store, db: &Db, sql: &str, vars: &Vars) -> Result<Vec<Value>> {
    let rows = store.retrying(|| Ok(db.query(sql, &vars.0, Some(1))?))?;
    Ok(rows[0].as_array().cloned().unwrap_or_default())
}

/// `fact:<key>` ids and file paths named in `text`.
fn citations(text: &str) -> (BTreeSet<String>, BTreeSet<String>) {
    static FACT: std::sync::LazyLock<regex::Regex> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"fact:([A-Za-z0-9_]{8,64})").expect("valid"));
    static PATH: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
        regex::Regex::new(r"([A-Za-z0-9_.~-]+(?:/[A-Za-z0-9_.~-]+)+\.[A-Za-z0-9]{1,8})").expect("valid")
    });
    let facts = FACT.captures_iter(text).map(|c| c[1].to_owned()).collect();
    let paths = PATH.captures_iter(text).map(|c| c[1].to_owned()).collect();
    (facts, paths)
}

/// Every gold link made after `cutoff` (RFC 3339) between shared projects in each other's bridge reach, and
/// the projects that were not read, with why.
pub fn gold(home: &Home, store: &Store, cutoff: &str) -> Result<(Vec<Gold>, Vec<String>)> {
    let registry = Registry::load(home)?;
    let mut out = BTreeSet::new();
    let mut not_read = Vec::new();
    let mut reaches = BTreeMap::new();
    for name in registry.projects.keys() {
        let level = store.level(&gw_store::project_ns(&store::slug(name)?)?)?;
        if level != "shared" {
            not_read.push(format!("{name} ({level})"));
            continue;
        }
        reaches.insert(name.clone(), bridge::reach(home, store, name)?);
    }
    let cut = Vars::default().set("cutoff", cutoff);
    for (p, r) in &reaches {
        let Ok(db) = reader(store, p) else {
            not_read.push(format!("{p} (no store namespace)"));
            continue;
        };
        let facts = query(
            store,
            &db,
            "SELECT record::id(id) AS key, source, text FROM fact WHERE at > <datetime>$cutoff;",
            &cut,
        )
        .unwrap_or_default();
        let mut cites: Vec<(String, BTreeSet<String>, BTreeSet<String>)> = Vec::new();
        for f in &facts {
            let text = format!(
                "{}\n{}",
                f["text"].as_str().unwrap_or_default(),
                f["source"].as_str().unwrap_or_default()
            );
            let (ids, paths) = citations(&text);
            if !ids.is_empty() || !paths.is_empty() {
                cites.push((f["key"].as_str().unwrap_or_default().to_owned(), ids, paths));
            }
        }
        if cites.is_empty() {
            continue;
        }
        let all_ids: Vec<String> = cites.iter().flat_map(|c| c.1.iter().cloned()).collect();
        let all_paths: Vec<String> = cites.iter().flat_map(|c| c.2.iter().cloned()).collect();
        let present = |db: &Db, sql: &str, items: &[String]| -> BTreeSet<String> {
            query(store, db, sql, &Vars::default().set("items", items.to_vec()))
                .unwrap_or_default()
                .into_iter()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect()
        };
        let own_ids = present(
            &db,
            "SELECT VALUE record::id(id) FROM fact WHERE record::id(id) IN $items;",
            &all_ids,
        );
        let own_paths = present(&db, "SELECT VALUE path FROM unit WHERE path IN $items;", &all_paths);
        for q in &r.sources {
            let Ok(qdb) = reader(store, q) else { continue };
            let q_ids = present(
                &qdb,
                "SELECT VALUE record::id(id) FROM fact WHERE record::id(id) IN $items;",
                &all_ids,
            );
            let q_paths = present(&qdb, "SELECT VALUE path FROM unit WHERE path IN $items;", &all_paths);
            for (fact, ids, paths) in &cites {
                for id in ids.iter().filter(|i| q_ids.contains(*i) && !own_ids.contains(*i)) {
                    out.insert(Gold {
                        p: p.clone(),
                        anchor_p: fact.clone(),
                        q: q.clone(),
                        anchor_q: id.clone(),
                        kind: "fact",
                    });
                }
                for path in paths.iter().filter(|x| q_paths.contains(*x) && !own_paths.contains(*x)) {
                    out.insert(Gold {
                        p: p.clone(),
                        anchor_p: fact.clone(),
                        q: q.clone(),
                        anchor_q: path.clone(),
                        kind: "fact",
                    });
                }
            }
        }
    }
    // Passed packets for a question about P (an ask that named P: everything else it searched is P's
    // related list), whose support quote came from a Q candidate in P's bridge reach. Eval traffic is left out.
    let global = store.agent(gw_store::GLOBAL_NS)?;
    let packets = query(
        store,
        &global,
        "SELECT record::id(id) AS key, by, searched, candidates, answer FROM ask_packet \
         WHERE status = 'PASS' AND at > <datetime>$cutoff;",
        &cut,
    )?;
    for pk in &packets {
        if pk["by"].as_str().is_some_and(|b| b.starts_with("eval:")) {
            continue;
        }
        let searched: Vec<&str> = pk["searched"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect();
        let Some((&p, rest)) = searched.split_first() else {
            continue;
        };
        let (Some(r), Ok(entry)) = (reaches.get(p), registry.project(p)) else {
            continue;
        };
        if !rest.iter().all(|n| entry.related.iter().any(|x| x == n)) {
            continue;
        }
        let candidates = pk["candidates"].as_array().cloned().unwrap_or_default();
        for quote in pk["answer"]["quotes"].as_array().into_iter().flatten() {
            if quote["use"].as_str().unwrap_or("support") != "support" {
                continue;
            }
            let Some(c) = candidates.iter().find(|c| c["n"] == quote["n"]) else {
                continue;
            };
            let q = c["project"].as_str().unwrap_or_default();
            if q != p && r.sources.iter().any(|s| s == q) {
                out.insert(Gold {
                    p: p.to_owned(),
                    anchor_p: pk["key"].as_str().unwrap_or_default().to_owned(),
                    q: q.to_owned(),
                    anchor_q: c["key"].as_str().unwrap_or_default().to_owned(),
                    kind: "packet",
                });
            }
        }
    }
    Ok((out.into_iter().collect(), not_read))
}

/// One target problem and the pool it is ranked against, with the pool indices its gold points at.
pub struct Case<'a> {
    pub target: &'a Card,
    pub pool: &'a [Card],
    pub gold: Vec<usize>,
}

/// One arm's hits per case.
pub struct ArmResult {
    pub arm: &'static str,
    pub hits: Vec<bool>,
}

/// The ranking an arm gives a case, top K capability indices.
fn top_k(arm: &str, case: &Case<'_>, k: usize) -> Result<Vec<Ranked>> {
    let mut r = bridge::rank(arm, case.target, case.pool)?;
    r.truncate(k);
    Ok(r)
}

/// hit@k for every arm on every case.
pub fn score(cases: &[Case<'_>], k: usize) -> Result<Vec<ArmResult>> {
    bridge::ARMS
        .iter()
        .map(|&arm| {
            let hits = cases
                .iter()
                .map(|c| Ok(top_k(arm, c, k)?.iter().any(|r| c.gold.contains(&r.index))))
                .collect::<Result<Vec<bool>>>()?;
            Ok(ArmResult { arm, hits })
        })
        .collect()
}

/// P(at least one of `g` gold cards among `k` drawn from `n`), the chance a random top-k holds gold.
fn chance(n: usize, g: usize, k: usize) -> f64 {
    if n == 0 || g == 0 {
        return 0.0;
    }
    let k = k.min(n);
    // 1 - C(n-g, k) / C(n, k)
    let mut miss = 1.0f64;
    for i in 0..k {
        if n - g < i + 1 {
            return 1.0;
        }
        miss *= (n - g - i) as f64 / (n - i) as f64;
    }
    1.0 - miss
}

/// A seeded permutation of 0..n (xorshift; a fixed seed gives the same deal).
pub fn deal(n: usize, seed: u64) -> Vec<usize> {
    let mut order: Vec<usize> = (0..n).collect();
    let mut s = seed | 1;
    for i in (1..n).rev() {
        s ^= s << 13;
        s ^= s >> 7;
        s ^= s << 17;
        order.swap(i, (s % (i as u64 + 1)) as usize);
    }
    order
}

/// The shuffled-gold control: the facet ranking scored against gold dealt to other targets. Returns the hits
/// observed, the hits expected at chance for that same ranking, and whether the observation is within three
/// standard deviations (plus one) of chance.
pub fn shuffled_control(cases: &[Case<'_>], k: usize, seed: u64) -> Result<(usize, f64, bool)> {
    let perm = deal(cases.len(), seed);
    let mut observed = 0usize;
    let (mut expected, mut var) = (0.0f64, 0.0f64);
    for (i, c) in cases.iter().enumerate() {
        let donor = &cases[perm[i]];
        // The donor's gold, found in this case's pool by project and card key.
        let gold: Vec<usize> = donor
            .gold
            .iter()
            .filter_map(|&g| {
                let want = &donor.pool[g];
                c.pool
                    .iter()
                    .position(|x| x.project == want.project && x.key == want.key)
            })
            .collect();
        let top = top_k("facet", c, k)?;
        if top.iter().any(|r| gold.contains(&r.index)) {
            observed += 1;
        }
        let caps = c.pool.iter().filter(|x| x.kind == "capability").count();
        let p = chance(caps, gold.len(), top.len());
        // Chance that a random top-|top| of the capabilities holds the dealt gold.
        expected += p;
        var += p * (1.0 - p);
    }
    let ok = (observed as f64 - expected).abs() <= 3.0 * var.sqrt() + 1.0;
    Ok((observed, expected, ok))
}

/// A synthetic pool where the facet arm finds every target's gold at rank 1 through the blocker chain and
/// every blocker shares one word (so each top-k is full): true gold must score near 1, shuffled gold
/// at chance. Returns the failures, empty when the evaluator behaves. Run by the selftest.
pub fn selfcheck() -> Vec<String> {
    let n = 120;
    let blank = |project: &str, key: String, kind: &str| Card {
        project: project.to_owned(),
        key,
        kind: kind.to_owned(),
        x: String::new(),
        y: None,
        a: Vec::new(),
        mech: None,
        solves: None,
        quote: String::new(),
        hash: String::new(),
        grade: "INFERRED".to_owned(),
        status: "live".to_owned(),
        cite: None,
        unit: None,
        fact: None,
        at: String::new(),
    };
    let mut pool = Vec::new();
    for i in 0..n {
        let mut p = blank("q", format!("p{i}"), "problem");
        p.x = format!("sensor goal{i}");
        p.y = Some(format!("blocker{i} stall"));
        let mut c = blank("q", format!("c{i}"), "capability");
        c.x = format!("tool{i}");
        c.mech = Some(format!("mechanism{i}"));
        c.a = vec!["it holds".to_owned()];
        c.solves = Some(format!("p{i}"));
        pool.push(p);
        pool.push(c);
    }
    let targets: Vec<Card> = (0..n)
        .map(|i| {
            let mut t = blank("p", format!("t{i}"), "problem");
            t.x = "display".to_owned();
            t.y = Some(format!("blocker{i} stall"));
            t
        })
        .collect();
    let cases: Vec<Case<'_>> = targets
        .iter()
        .enumerate()
        .map(|(i, t)| Case {
            target: t,
            pool: &pool,
            gold: vec![2 * i + 1],
        })
        .collect();
    let mut failures = Vec::new();
    match score(&cases, K) {
        Ok(arms) => {
            let rate = |a: &ArmResult| a.hits.iter().filter(|h| **h).count() as f64 / n as f64;
            let facet = arms.iter().find(|a| a.arm == "facet").map(rate).unwrap_or(0.0);
            let random = arms.iter().find(|a| a.arm == "random").map(rate).unwrap_or(1.0);
            if facet < 0.95 {
                failures.push(format!("facet arm on true gold scored {facet:.2}, expected near 1"));
            }
            if (random - K as f64 / n as f64).abs() > 0.08 {
                failures.push(format!(
                    "random arm scored {random:.2}, expected near {:.2}",
                    K as f64 / n as f64
                ));
            }
        }
        Err(e) => failures.push(format!("scoring failed: {e:#}")),
    }
    match shuffled_control(&cases, K, 7) {
        Ok((observed, expected, true)) => {
            let _ = (observed, expected);
        }
        Ok((observed, expected, false)) => failures.push(format!(
            "shuffled-gold control scored {observed} of {n} where chance is {expected:.1}: the evaluator scores \
             something other than the gold"
        )),
        Err(e) => failures.push(format!("control failed: {e:#}")),
    }
    failures
}

/// The whole evaluation: gold after `cutoff`, and, with at least MIN_GOLD of it, the arms over the cards made
/// at or before the cutoff. Read-only.
pub fn run(home: &Home, store: &Store, cutoff: &str, k: usize, seed: u64) -> Result<String> {
    let (gold, not_read) = gold(home, store, cutoff)?;
    let facts = gold.iter().filter(|g| g.kind == "fact").count();
    let packets = gold.len() - facts;
    let mut out = format!(
        "bridge-eval (no model; read-only; machine {})\ncutoff: {cutoff}\ngold links after the cutoff: {} (fact links \
         {facts}, packet links {packets})\nnot read: {}\n",
        crate::home::machine(),
        gold.len(),
        if not_read.is_empty() {
            "none".to_owned()
        } else {
            not_read.join(", ")
        }
    );
    let mut pairs: BTreeMap<(String, String), usize> = BTreeMap::new();
    for g in &gold {
        *pairs.entry((g.p.clone(), g.q.clone())).or_default() += 1;
    }
    for ((p, q), n) in &pairs {
        let _ = writeln!(out, "  {p} -> {q}: {n}");
    }
    if gold.len() < MIN_GOLD {
        let _ = writeln!(
            out,
            "RESULT: no evidence. {} gold link(s), fewer than {MIN_GOLD}: no arm is compared and nothing passes. The \
             owner labels a set before this measures anything.",
            gold.len()
        );
        return Ok(out);
    }
    // Cards at or before the cutoff: targets in each P made from the P fact a gold link starts at.
    let cut = Vars::default().set("cutoff", cutoff);
    let mut pools: BTreeMap<String, Vec<Card>> = BTreeMap::new();
    let mut targets: Vec<(Card, Vec<&Gold>)> = Vec::new();
    for p in gold.iter().map(|g| g.p.clone()).collect::<BTreeSet<_>>() {
        let db = reader(store, &p)?;
        let problems = cards_or_none(card::read_cards(
            store,
            &db,
            &p,
            "WHERE kind = 'problem' AND at <= <datetime>$cutoff",
            cut.clone_vars(),
            5000,
        ))?;
        for t in problems {
            let mine: Vec<&Gold> = gold
                .iter()
                .filter(|g| g.p == p && g.kind == "fact" && t.fact.as_deref() == Some(g.anchor_p.as_str()))
                .collect();
            if !mine.is_empty() {
                targets.push((t, mine));
            }
        }
        let r = bridge::reach(home, store, &p)?;
        let mut pool = Vec::new();
        for q in &r.sources {
            let qdb = reader(store, q)?;
            pool.extend(
                cards_or_none(card::read_cards(
                    store,
                    &qdb,
                    q,
                    "WHERE kind IN ['problem', 'capability'] AND at <= <datetime>$cutoff",
                    cut.clone_vars(),
                    5000,
                ))?
                .into_iter()
                .filter(|c| card::unservable(c).is_none()),
            );
        }
        pools.insert(p, pool);
    }
    let cases: Vec<Case<'_>> = targets
        .iter()
        .map(|(t, golds)| {
            let pool = &pools[&t.project];
            let gold = (0..pool.len())
                .filter(|&i| {
                    let c = &pool[i];
                    golds.iter().any(|g| {
                        c.project == g.q
                            && (c.fact.as_deref() == Some(g.anchor_q.as_str())
                                || c.unit.as_deref() == Some(g.anchor_q.as_str())
                                || c.cite
                                    .as_deref()
                                    .is_some_and(|x| x.split("#L").next() == Some(g.anchor_q.as_str())))
                    })
                })
                .collect();
            Case { target: t, pool, gold }
        })
        .collect();
    let _ = writeln!(
        out,
        "evaluable targets (a problem card at or before the cutoff on a gold fact): {}",
        cases.len()
    );
    if cases.len() < MIN_GOLD {
        let _ = writeln!(
            out,
            "RESULT: no evidence. {} evaluable target(s), fewer than {MIN_GOLD}: cards must be made (at or before \
             the cutoff) for the facts the gold starts at.",
            cases.len()
        );
        return Ok(out);
    }
    let arms = score(&cases, k)?;
    let flat = arms.iter().find(|a| a.arm == "flat").context("the flat arm")?;
    for a in &arms {
        let hits = a.hits.iter().filter(|h| **h).count();
        let (wins, losses) = a
            .hits
            .iter()
            .zip(&flat.hits)
            .fold((0, 0), |(w, l), (x, f)| match (x, f) {
                (true, false) => (w + 1, l),
                (false, true) => (w, l + 1),
                _ => (w, l),
            });
        let _ = writeln!(
            out,
            "  {:<7} hit@{k} {hits}/{} ({:.1}%); against flat: {wins} wins, {losses} losses, sign test p = {:.4}",
            a.arm,
            cases.len(),
            100.0 * hits as f64 / cases.len() as f64,
            crate::eval::sign_test(wins, wins + losses)
        );
    }
    let (observed, expected, ok) = shuffled_control(&cases, k, seed)?;
    let _ = writeln!(
        out,
        "  shuffled-gold control (facet ranking, gold dealt to other targets): {observed} hits, chance {expected:.1}: {}",
        if ok {
            "at chance, as it must be"
        } else {
            "NOT at chance: the evaluator is broken, ignore the arms"
        }
    );
    Ok(out)
}

/// A project whose namespace has no card table yet (its schema predates cards) has no cards: zero targets
/// and an empty pool, which the evaluable count then reports. Any other failure stands.
fn cards_or_none(read: Result<Vec<Card>>) -> Result<Vec<Card>> {
    match read {
        Err(e) if format!("{e:#}").contains("table 'card' does not exist") => Ok(Vec::new()),
        other => other,
    }
}

impl Vars {
    /// A copy, for a second query with the same values.
    pub(crate) fn clone_vars(&self) -> Vars {
        Vars(self.0.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_evaluator_scores_true_gold_high_and_shuffled_gold_at_chance() {
        assert_eq!(selfcheck(), Vec::<String>::new());
    }

    #[test]
    fn citations_find_fact_ids_and_paths() {
        let (ids, paths) = citations("see fact:abc123def456 and rust/crates/gw-ops/src/ask.rs:448, not README.md");
        assert!(ids.contains("abc123def456"));
        assert!(paths.contains("rust/crates/gw-ops/src/ask.rs"));
        assert!(
            !paths.contains("README.md"),
            "a bare file name is in every project and proves no link"
        );
    }

    #[test]
    fn chance_is_the_hypergeometric_floor() {
        assert!((chance(100, 1, 10) - 0.1).abs() < 1e-9);
        assert_eq!(chance(5, 1, 10), 1.0);
        assert_eq!(chance(100, 0, 10), 0.0);
    }
}
