//! The gate funnel and its judgement cache (idea-bridges design, amendment A6.3, slice 14). No model: the funnel
//! decides what an agent is asked, and remembers what it answered.
//!
//! 1. A cheap score with a floor: the facet ranking for a problem card, screened as `bridge_propose` screens it
//!    (veto, relative threshold); only candidates scoring at least [`FLOOR_SHARE`] of the best, at most
//!    [`FUNNEL_K`], are ever asked about. Below the floor is never asked (Punchable `JUDGE_FLOOR`).
//! 2. Batched checks: per admitted pair, one check per source assumption and one novelty check, at most [`BATCH`]
//!    at a time, handed to the agent as data (Punchable `JUDGE_BATCH`).
//! 3. A second look only for a borderline answer (verdict unknown, or confidence under [`BORDERLINE`]).
//!
//! Every answer is a `judgement` row (schema 0021) keyed on a hash of both quotes, the check and the prompt
//! version (Punchable `merge.py` caches by a hash of both texts): a pair is not asked again until either quote
//! changes, and a changed quote is a new key. A judgement is INFERRED at most (UNKNOWN for an unknown verdict);
//! a cache hit never raises a grade. A cached verdict is reused only by being written onto the bridge row with
//! `bridge_advance`, where the schema's gate checks it: the cache fills the gate, it does not bypass it.

use std::collections::HashMap;
use std::fmt::Write as _;

use anyhow::{Context, Result, bail, ensure};
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::Value;

use crate::bridge::{self, Reach};
use crate::card::{self, Card};
use crate::home::Home;
use crate::store::{self, Store, Vars, sha256_hex};

/// The version of the questions below: a new version is a new cache key, so changed wording re-asks.
pub const PROMPT_VERSION: &str = "funnel-1";
/// A candidate is asked about only when its score is at least this share of the best admitted score.
pub const FLOOR_SHARE: f64 = 0.25;
/// At most this many candidates per target are ever asked about.
pub const FUNNEL_K: usize = 10;
/// At most this many checks per batch.
pub const BATCH: usize = 8;
/// A first answer under this confidence (or with verdict unknown) gets a second look.
pub const BORDERLINE: f64 = 0.6;

/// The cache key of one check on one pair of quotes: order-free in the two quotes.
pub fn key(quote_hash_a: &str, quote_hash_b: &str, check: &str) -> String {
    let (a, b) = if quote_hash_a <= quote_hash_b {
        (quote_hash_a, quote_hash_b)
    } else {
        (quote_hash_b, quote_hash_a)
    };
    sha256_hex(&[
        a.as_bytes(),
        b"\n",
        b.as_bytes(),
        b"\n",
        check.as_bytes(),
        b"\n",
        PROMPT_VERSION.as_bytes(),
    ])
}

fn row_id(key: &str, round: &str) -> String {
    format!("{}{}", &key[..40], if round == "second" { "s" } else { "f" })
}

/// Which of `scores` (best first) clear the floor, and which fall below it. Pure.
pub fn admit(scores: &[f64]) -> (Vec<usize>, Vec<usize>) {
    let best = scores.iter().copied().fold(0.0f64, f64::max);
    let floor = FLOOR_SHARE * best;
    let mut admitted = Vec::new();
    let mut below = Vec::new();
    for (i, s) in scores.iter().enumerate() {
        if *s >= floor && *s > 0.0 && admitted.len() < FUNNEL_K {
            admitted.push(i);
        } else {
            below.push(i);
        }
    }
    (admitted, below)
}

/// The floor on synthetic scores, both ways; the failures, empty when it holds. Run by the selftest.
pub fn floor_selfcheck() -> Vec<String> {
    let (admitted, below) = admit(&[10.0, 5.0, 2.6, 2.4, 0.0]);
    let mut failures = Vec::new();
    if admitted != [0, 1, 2] {
        failures.push(format!(
            "admitted {admitted:?}, expected [0, 1, 2] (floor 2.5 of best 10)"
        ));
    }
    if below != [3, 4] {
        failures.push(format!(
            "below the floor {below:?}, expected [3, 4]: below the floor must never be asked"
        ));
    }
    failures
}

/// One check the funnel may ask about one pair.
struct Check {
    check: String,
    question: String,
    verdicts: &'static [&'static str],
}

fn checks_for(source: &Card) -> Vec<Check> {
    let mut out: Vec<Check> = source
        .a
        .iter()
        .enumerate()
        .map(|(i, a)| Check {
            check: format!("assumption:{i}:{}", &sha256_hex(&[a.as_bytes()])[..12]),
            question: format!(
                "Does the source capability's assumption {a:?} hold in the target project? Answer holds, fails or \
                 unknown, with a confidence from 0 to 1."
            ),
            verdicts: &["holds", "fails", "unknown"],
        })
        .collect();
    out.push(Check {
        check: "novelty".to_owned(),
        question: "Has the target project already made this link (the program's search hits are listed)? Answer \
                   not_made, made or unknown, with a confidence from 0 to 1."
            .to_owned(),
        verdicts: &["not_made", "made", "unknown"],
    });
    out
}

/// A stored judgement as the funnel reads it.
#[derive(Debug, Clone)]
struct Judged {
    verdict: String,
    confidence: f64,
    borderline: bool,
    grade: String,
}

/// One open question: ask it (first or second look), or serve it from the cache.
enum State {
    Ask(&'static str),
    Cached(String, Judged),
}

struct Entry {
    id: String,
    key: String,
    source: usize,
    check: Check,
    state: State,
}

/// The funnel for one target: its pool, the admitted candidates' entries, and those below the floor.
struct Funnel {
    reach: Reach,
    target: Card,
    pool: Vec<Card>,
    entries: Vec<Entry>,
    below: Vec<(usize, f64, f64)>,
}

fn open(home: &Home, store: &Store, project: &str, target: &str, only: Option<(&str, &str)>) -> Result<Funnel> {
    let r = bridge::reach(home, store, project)?;
    let t = card::card(store, &r.target, target)?;
    ensure!(
        t.kind == "problem",
        "card:{} is a {}; the funnel takes a problem card",
        t.key,
        t.kind
    );
    ensure!(
        t.status == "live",
        "card:{} is {}, not live: its quote changed",
        t.key,
        t.status
    );
    let (pool, _) = bridge::pool(store, &r)?;
    let own = card::cards(
        store,
        &r.target,
        "WHERE status = 'live' AND kind IN ['problem', 'capability']",
        Vars::default(),
        5000,
    )?;
    let ranked = bridge::rank("facet", &t, &pool)?;
    let kept = bridge::screen("facet", &t, &pool, &own, ranked)?.kept;
    let scores: Vec<f64> = kept.iter().map(|r| r.score).collect();
    let (admitted, below_idx) = admit(&scores);
    let floor = FLOOR_SHARE * scores.iter().copied().fold(0.0f64, f64::max);
    let wanted = |c: &Card| only.is_none_or(|(p, k)| c.project == p && c.key == k.trim_start_matches("card:"));
    let below = below_idx
        .into_iter()
        .filter(|&i| wanted(&pool[kept[i].index]))
        .map(|i| (kept[i].index, kept[i].score, floor))
        .collect();
    let mut entries = Vec::new();
    for i in admitted {
        let c = &pool[kept[i].index];
        if !wanted(c) {
            continue;
        }
        for check in checks_for(c) {
            let key = key(&t.quote_hash, &c.quote_hash, &check.check);
            entries.push(Entry {
                id: row_id(&key, "first"),
                key,
                source: kept[i].index,
                check,
                state: State::Ask("first"),
            });
        }
    }
    // What the cache already holds for these keys.
    let ids: Vec<String> = entries
        .iter()
        .flat_map(|e| [row_id(&e.key, "first"), row_id(&e.key, "second")])
        .collect();
    let db = store.agent(&gw_store::project_ns(&store::slug(&r.target)?)?)?;
    let vars = Vars::default().set("ids", ids);
    let rows = store.retrying(|| {
        Ok(db.query(
            "SELECT record::id(id) AS id, verdict, confidence, borderline, grade FROM judgement \
             WHERE record::id(id) IN $ids;",
            &vars.0,
            Some(1),
        )?)
    })?;
    let cached: HashMap<String, Judged> = rows[0]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|r: &Value| {
            Some((
                r["id"].as_str()?.to_owned(),
                Judged {
                    verdict: r["verdict"].as_str()?.to_owned(),
                    confidence: r["confidence"].as_f64().unwrap_or(0.0),
                    borderline: r["borderline"].as_bool().unwrap_or(false),
                    grade: r["grade"].as_str()?.to_owned(),
                },
            ))
        })
        .collect();
    for e in &mut entries {
        let (f, s) = (row_id(&e.key, "first"), row_id(&e.key, "second"));
        e.state = match (cached.get(&f), cached.get(&s)) {
            (_, Some(j)) => State::Cached(s.clone(), j.clone()),
            (Some(j), None) if !j.borderline => State::Cached(f.clone(), j.clone()),
            (Some(_), None) => {
                e.id = s.clone();
                State::Ask("second")
            }
            (None, None) => State::Ask("first"),
        };
    }
    Ok(Funnel {
        reach: r,
        target: t,
        pool,
        entries,
        below,
    })
}

/// The next batch for one problem card of `project` (optionally one source card, `project`/`card:...`): what to
/// ask, at most BATCH checks, each framed as data with both quotes; what the cache already answered; and what fell
/// below the floor and is never asked. Writes nothing.
pub fn batch(home: &Home, store: &Store, project: &str, target: &str, only: Option<(&str, &str)>) -> Result<String> {
    let f = open(home, store, project, target, only)?;
    let asks: Vec<&Entry> = f.entries.iter().filter(|e| matches!(e.state, State::Ask(_))).collect();
    let mut out = format!(
        "funnel for {} card:{} ({PROMPT_VERSION}): {} check(s) to ask, {} cached, {} candidate(s) below the floor; \
         showing {}\n",
        f.reach.target,
        f.target.key,
        asks.len(),
        f.entries.len() - asks.len(),
        f.below.len(),
        asks.len().min(BATCH)
    );
    for (i, score, floor) in &f.below {
        let _ = writeln!(
            out,
            "below the floor, never asked: {} card:{} (score {score:.3} < floor {floor:.3})",
            f.pool[*i].project, f.pool[*i].key
        );
    }
    for e in &f.entries {
        if let State::Cached(id, j) = &e.state {
            let _ = writeln!(
                out,
                "cached judgement:{id}: {} card:{} {} -> {} (confidence {:.2}; grade {}); reused until either quote \
                 changes",
                f.pool[e.source].project, f.pool[e.source].key, e.check.check, j.verdict, j.confidence, j.grade
            );
        }
    }
    for e in asks.iter().take(BATCH) {
        let State::Ask(round) = e.state else { continue };
        let c = &f.pool[e.source];
        let mut body = format!(
            "ask judgement:{} [{round} look] {}\n  {}\n  target {} card:{} quote ({}): {:?}\n  source {} card:{} quote \
             ({}): {:?}",
            e.id,
            e.check.check,
            e.check.question,
            f.target.project,
            f.target.key,
            f.target.cite.as_deref().unwrap_or("?"),
            f.target.quote,
            c.project,
            c.key,
            c.cite.as_deref().unwrap_or("?"),
            c.quote
        );
        if e.check.check == "novelty" {
            let query = format!("{} {}", c.x, c.mech.as_deref().unwrap_or_default());
            let n = bridge::novelty(store, &f.reach, c, &query, "made")?;
            let _ = write!(
                body,
                "\n  search {:?} over {}: {} hit(s); already in the target: {}",
                query,
                n["reach"],
                n["hits"].as_array().map_or(0, Vec::len),
                n["made"]
            );
        }
        out.push_str(&card::framed(
            "groundworks-funnel-check",
            &[("project", &c.project), ("grade", c.grade_for(&f.reach.target))],
            "A check for an agent to answer: the quotes are each project's own text; the question is the program's. \
             Answer with funnel_answer.",
            &body,
        ));
    }
    Ok(out)
}

/// One answer to a funnel check.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct AnswerIn {
    /// The check's id (`judgement:...`) as the batch printed it.
    pub id: String,
    pub verdict: String,
    /// From 0 to 1. Under 0.6 (or verdict unknown) a first answer gets a second look.
    pub confidence: f64,
    /// Why, in a sentence or two.
    pub why: String,
    /// Leave out: a judgement is INFERRED (UNKNOWN for verdict unknown); anything higher is refused.
    #[serde(default)]
    pub grade: Option<String>,
}

/// Store answers to open checks of one target's funnel. Each must name a check the funnel would ask now.
pub fn answer(
    home: &Home,
    store: &Store,
    project: &str,
    target: &str,
    answers: &[AnswerIn],
    by: &str,
) -> Result<String> {
    let f = open(home, store, project, target, None)?;
    crate::refusal::check_text("by", by)?;
    let db = store.agent(&gw_store::project_ns(&store::slug(&f.reach.target)?)?)?;
    let mut out = String::new();
    for a in answers {
        let id = a.id.trim().trim_start_matches("judgement:");
        let e = f
            .entries
            .iter()
            .find(|e| e.id == id && matches!(e.state, State::Ask(_)))
            .with_context(|| {
                format!("judgement:{id} is not an open check of this target (answered, or its quote changed)")
            })?;
        let State::Ask(round) = e.state else { unreachable!() };
        let verdict = a.verdict.trim();
        ensure!(
            e.check.verdicts.contains(&verdict),
            "{} takes {}, not {verdict:?}",
            e.check.check,
            e.check.verdicts.join(", ")
        );
        ensure!((0.0..=1.0).contains(&a.confidence), "confidence is from 0 to 1");
        crate::refusal::check_text("why", &a.why)?;
        ensure!(a.why.len() <= 2000, "why is at most 2000 characters");
        let grade = if verdict == "unknown" { "UNKNOWN" } else { "INFERRED" };
        if let Some(asked) = a.grade.as_deref().map(str::trim).filter(|g| !g.is_empty())
            && asked != grade
        {
            bail!(
                "refused: a judgement is {grade} (an agent's reading, cached); a cache hit never raises a grade, so \
                 {asked} is not accepted"
            );
        }
        let borderline = round == "first" && (verdict == "unknown" || a.confidence < BORDERLINE);
        let c = &f.pool[e.source];
        let (qa, qb) = if f.target.quote_hash <= c.quote_hash {
            (&f.target.quote_hash, &c.quote_hash)
        } else {
            (&c.quote_hash, &f.target.quote_hash)
        };
        let vars = Vars::default()
            .set("id", id)
            .set("check", e.check.check.as_str())
            .set("pv", PROMPT_VERSION)
            .set("qa", qa.as_str())
            .set("qb", qb.as_str())
            .set("round", round)
            .set("verdict", verdict)
            .set("confidence", a.confidence)
            .set("borderline", borderline)
            .set("why", a.why.trim())
            .set(
                "grade",
                a.grade
                    .as_deref()
                    .map(str::trim)
                    .filter(|g| !g.is_empty())
                    .unwrap_or(grade),
            )
            .set("target", f.target.key.as_str())
            .set("sp", c.project.as_str())
            .set("sc", c.key.as_str())
            .set("by", by);
        store
            .retrying(|| {
                Ok(db.query(
                    "CREATE type::record('judgement', $id) SET check = $check, prompt_version = $pv, quote_hash_a = $qa, \
                     quote_hash_b = $qb, round = $round, verdict = $verdict, confidence = <float>$confidence, \
                     borderline = $borderline, why = $why, grade = $grade, target_card = $target, source_project = $sp, \
                     source_card = $sc, by = $by RETURN VALUE id;",
                    &vars.0,
                    Some(1),
                )?)
            })
            .with_context(|| format!("refused: judgement:{id} was not stored"))?;
        let _ = writeln!(
            out,
            "judgement:{id} stored: {} {verdict} ({grade}, {round} look{})",
            e.check.check,
            if borderline {
                "; borderline: a second look is owed"
            } else {
                ""
            }
        );
    }
    Ok(out)
}
