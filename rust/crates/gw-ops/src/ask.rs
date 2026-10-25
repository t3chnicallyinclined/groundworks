//! `ask` and `answer`: a question, the units retrieved for it, and an answer whose every quote a
//! program has checked. Ported from the Python engine's `groundworks/ask` (packet.py, check.py; tag
//! v0.2.0), rule for rule on the check, with three stated deviations:
//!
//! 1. A packet is a record in the store (question, candidates as locator + hash + rank), not a folder.
//! 2. Ranking is the store's full-text BM25 over ingested units (stemmed, with kind and expert filters),
//!    not a second in-memory index; k defaults to 20 (Python: 200), because a Claude session reads every
//!    candidate in full and 200 would flood its context. What was never read is still reported.
//! 3. The status axis becomes: a `support` quote must come from a unit still present and unchanged since
//!    it was retrieved, so an answer never rests on text that moved after it was read.
//!
//! An answer never becomes a fact by itself: recording one is `journal_add` with a quote, through the
//! store's evidence rule.

use std::collections::{BTreeMap, HashMap};
use std::fmt::Write as _;

use anyhow::{Result, ensure};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::home::Home;
use crate::registry::Registry;
use crate::store::{self, Hit, Store};

pub const DEFAULT_K: usize = 20;
pub const MAX_K: usize = 200;
/// Python's `labels.quote_min_words` default.
pub const QUOTE_MIN_WORDS: usize = 3;

/// One retrieved unit as the packet records it: no text, only where it is and what it said (hash).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Candidate {
    pub n: usize,
    pub project: String,
    pub key: String,
    pub locator: String,
    pub hash: String,
    pub kind: Option<String>,
    pub score: Option<f64>,
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct QuoteIn {
    /// The candidate number the quote comes from.
    pub n: usize,
    /// Copied character for character from that candidate (whitespace may differ).
    pub quote: String,
    /// `support` (the answer rests on it) or `note` (it says something relevant). Default support.
    #[serde(rename = "use")]
    pub use_: Option<String>,
    pub why: Option<String>,
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct AnswerIn {
    pub answer: String,
    #[serde(default)]
    pub quotes: Vec<QuoteIn>,
    /// What the candidates did not settle.
    #[serde(default)]
    pub gaps: Vec<String>,
    #[serde(default)]
    pub not_answerable: bool,
}

/// What a unit is now, by key: (hash, text, present).
pub type Now = HashMap<String, (String, String, bool)>;

/// Python's `_norm`: runs of whitespace become one space, ends trimmed.
pub fn normalize(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

#[derive(Debug, Serialize)]
pub struct Report {
    pub verdict: &'static str,
    pub corpus_total: usize,
    pub retrieved: usize,
    pub read: usize,
    pub not_read: usize,
    pub quotes: usize,
    pub quotes_checked: usize,
    pub quotes_found: usize,
    pub errors: Vec<String>,
}

impl Report {
    pub fn header(&self) -> String {
        format!(
            "corpus {} | retrieved {} | read {} | not read {} | quotes {} (checked {}, found {}) | check {}",
            self.corpus_total,
            self.retrieved,
            self.read,
            self.not_read,
            self.quotes,
            self.quotes_checked,
            self.quotes_found,
            self.verdict
        )
    }
}

/// The check, as one pure function: every rule of Python's check.py, against what the units are now.
pub fn check(candidates: &[Candidate], corpus_total: usize, now: &Now, read: &[usize], answer: &AnswerIn) -> Report {
    let mut report = Report {
        verdict: "PASS",
        corpus_total,
        retrieved: candidates.len(),
        read: 0,
        not_read: corpus_total,
        quotes: answer.quotes.len(),
        quotes_checked: 0,
        quotes_found: 0,
        errors: Vec::new(),
    };
    if !candidates.is_empty() && read.is_empty() {
        report.verdict = "NOT_EXECUTED";
        report
            .errors
            .push("nothing was read, so nothing can be checked".to_owned());
        return report;
    }
    let by_n: BTreeMap<usize, &Candidate> = candidates.iter().map(|c| (c.n, c)).collect();
    let mut times_read: BTreeMap<usize, usize> = BTreeMap::new();
    let errors = &mut report.errors;
    for &n in read {
        if !by_n.contains_key(&n) {
            errors.push(format!("read names candidate {n}, which is not in this packet"));
            continue;
        }
        let count = times_read.entry(n).or_default();
        *count += 1;
        if *count == 2 {
            errors.push(format!("read names candidate {n} twice"));
        }
    }
    for c in candidates {
        if !times_read.contains_key(&c.n) {
            errors.push(format!("candidate {} ({}) was not read", c.n, c.locator));
        }
    }
    for (i, q) in answer.quotes.iter().enumerate() {
        let k = i + 1;
        let use_ = q.use_.as_deref().unwrap_or("support");
        if use_ != "support" && use_ != "note" {
            errors.push(format!("quote {k} use must be support or note, not {use_:?}"));
        }
        let Some(c) = by_n.get(&q.n) else {
            errors.push(format!("quote {k} cites candidate {}, outside this packet", q.n));
            continue;
        };
        if !times_read.contains_key(&q.n) {
            errors.push(format!("quote {k} cites candidate {}, which was not read", q.n));
        }
        let text = normalize(&q.quote);
        if text.split(' ').filter(|w| !w.is_empty()).count() < QUOTE_MIN_WORDS {
            errors.push(format!("quote {k} has fewer than {QUOTE_MIN_WORDS} words"));
            continue;
        }
        report.quotes_checked += 1;
        match now.get(&c.key) {
            None => errors.push(format!("quote {k}: candidate {} ({}) no longer exists", q.n, c.locator)),
            Some((hash, _, _)) if *hash != c.hash => errors.push(format!(
                "quote {k}: candidate {} ({}) changed since it was retrieved; ask again",
                q.n, c.locator
            )),
            Some((_, unit_text, present)) => {
                if normalize(unit_text).contains(&text) {
                    report.quotes_found += 1;
                } else {
                    errors.push(format!(
                        "quote {k} not found word for word in candidate {} ({})",
                        q.n, c.locator
                    ));
                }
                if use_ == "support" && !present {
                    errors.push(format!(
                        "quote {k} supports from candidate {} ({}), which is no longer present",
                        q.n, c.locator
                    ));
                }
            }
        }
    }
    report.read = times_read.len();
    report.not_read = corpus_total.saturating_sub(report.read);
    if !report.errors.is_empty() {
        report.verdict = "FAIL";
    }
    report
}

/// What to look for and where: shared by `search` and `ask`.
#[derive(Debug, Clone, Copy)]
pub struct Query<'a> {
    pub text: &'a str,
    /// One project (private ones too, never an isolated one); None = every shared project.
    pub project: Option<&'a str>,
    pub expert: Option<&'a str>,
    pub kinds: &'a [String],
    pub limit: usize,
}

/// What a query found across the projects it was allowed to read.
pub struct Found {
    pub hits: Vec<Hit>,
    pub searched: Vec<String>,
    /// Projects not searched, each with why (its level, or the error).
    pub skipped: Vec<String>,
    pub corpus_total: usize,
}

/// The one cross-project read: each project the query may read (a named one, or every shared one),
/// its best `limit` units, merged best first and cut to `limit`.
pub fn fan_out(home: &Home, store: &Store, q: Query<'_>) -> Result<Found> {
    let registry = Registry::load(home)?;
    let names: Vec<String> = match q.project {
        Some(p) => vec![registry.project(p)?.name.clone()],
        None => registry.projects.keys().cloned().collect(),
    };
    let mut found = Found {
        hits: Vec::new(),
        searched: Vec::new(),
        skipped: Vec::new(),
        corpus_total: 0,
    };
    for name in &names {
        if q.project.is_none() {
            let level = store.level(&gw_store::project_ns(&store::slug(name)?)?)?;
            if level != "shared" {
                found.skipped.push(format!("{name} ({level})"));
                continue;
            }
        }
        match store.retrieve(name, q.text, q.expert, q.kinds, q.limit) {
            Ok(hits) => {
                found.corpus_total += store.corpus_size(name)?;
                found.searched.push(name.clone());
                found.hits.extend(hits);
            }
            Err(e) => found.skipped.push(format!("{name} ({e:#})")),
        }
    }
    found
        .hits
        .sort_by(|a, b| b.score.unwrap_or(0.0).total_cmp(&a.score.unwrap_or(0.0)));
    found.hits.truncate(q.limit);
    Ok(found)
}

/// "searched: a, b; skipped: c (private)": the line every read ends with, so a reader knows its reach.
pub fn reach(found: &Found) -> String {
    format!(
        "searched: {}{}",
        if found.searched.is_empty() {
            "nothing".to_owned()
        } else {
            found.searched.join(", ")
        },
        if found.skipped.is_empty() {
            String::new()
        } else {
            format!("; skipped: {}", found.skipped.join(", "))
        }
    )
}

/// Open a packet: retrieve the best `q.limit` units for the question, record them, and hand the
/// reader every candidate in full, numbered.
pub fn open(home: &Home, store: &Store, q: Query<'_>, by: &str) -> Result<String> {
    let question = q.text.trim();
    ensure!(!question.is_empty(), "the question is empty");
    let q = Query {
        text: question,
        limit: q.limit.clamp(1, MAX_K),
        ..q
    };
    let found = fan_out(home, store, q)?;
    let candidates: Vec<Candidate> = found
        .hits
        .iter()
        .enumerate()
        .map(|(i, h)| Candidate {
            n: i + 1,
            project: h.project.clone(),
            key: h.key.clone(),
            locator: h.locator.clone(),
            hash: h.hash.clone(),
            kind: h.kind.clone(),
            score: h.score,
        })
        .collect();
    let status = if candidates.is_empty() { "NO_HITS" } else { "OPEN" };
    let id = store.create_packet(&json!({
        "question": question,
        "by": by,
        "searched": found.searched,
        "skipped": found.skipped,
        "corpus_total": found.corpus_total,
        "candidates": candidates,
        "status": status,
    }))?;
    let mut out = format!(
        "{id} | {status} | corpus {} | retrieved {} | {}\n",
        found.corpus_total,
        candidates.len(),
        reach(&found)
    );
    if candidates.is_empty() {
        out.push_str("No unit shares a word with the question. That is the answer: nothing ingested speaks to it.\n");
        return Ok(out);
    }
    out.push_str(
        "Read EVERY candidate below in full, then call `answer` with this packet id, the numbers you read, \
         your answer, and quotes copied word for word (at least 3 words each) from the candidates you read.\n",
    );
    for (c, h) in candidates.iter().zip(&found.hits) {
        let experts = if h.experts.is_empty() {
            String::new()
        } else {
            format!("; experts: {}", h.experts.join(", "))
        };
        let commit = h
            .commit
            .as_deref()
            .map_or(String::new(), |c| format!(" @ {}", &c[..c.len().min(10)]));
        writeln!(
            out,
            "\n[{}] {}:{}{commit} ({}{experts})\n{}",
            c.n,
            c.project,
            c.locator,
            c.kind.as_deref().unwrap_or("?"),
            h.text
        )?;
    }
    Ok(out)
}

/// Check an answer against its packet and record the verdict. Returns (text, passed).
pub fn answer(store: &Store, packet_id: &str, read: &[usize], answer: &AnswerIn) -> Result<(String, bool)> {
    let packet = store.packet(packet_id)?;
    let candidates: Vec<Candidate> = serde_json::from_value(packet["candidates"].clone())?;
    let corpus_total = packet["corpus_total"].as_u64().unwrap_or(0) as usize;
    let mut now = Now::new();
    let mut by_project: BTreeMap<&str, Vec<String>> = BTreeMap::new();
    for c in &candidates {
        by_project.entry(c.project.as_str()).or_default().push(c.key.clone());
    }
    for (project, keys) in by_project {
        now.extend(store.units_now(project, &keys)?);
    }
    let report = check(&candidates, corpus_total, &now, read, answer);
    let answer_json: Value = json!({
        "answer": answer.answer,
        "quotes": answer.quotes.iter().map(|q| json!({"n": q.n, "quote": q.quote, "use": q.use_.as_deref().unwrap_or("support"), "why": q.why})).collect::<Vec<_>>(),
        "gaps": answer.gaps,
        "not_answerable": answer.not_answerable,
    });
    store.finish_packet(packet_id, report.verdict, &answer_json, &serde_json::to_value(&report)?)?;
    let mut out = format!("{packet_id}: {}\n", report.header());
    for e in &report.errors {
        writeln!(out, "  - {e}")?;
    }
    let passed = report.verdict == "PASS";
    if passed {
        out.push_str(
            "Every quote was found word for word in a candidate that was read. To keep any of it as a fact, record \
             it with journal_add and a quote of the file (file, line, text): the store checks it again there.\n",
        );
    }
    Ok((out, passed))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cand(n: usize, key: &str) -> Candidate {
        Candidate {
            n,
            project: "p".into(),
            key: key.into(),
            locator: format!("f.md#L{n}-{n}"),
            hash: format!("h{key}"),
            kind: Some("context".into()),
            score: Some(1.0),
        }
    }

    fn now() -> Now {
        let mut m = Now::new();
        m.insert(
            "a".into(),
            ("ha".into(), "The parser   rejects empty\ninput early.".into(), true),
        );
        m.insert(
            "b".into(),
            ("hb".into(), "Rollback resimulates confirmed frames.".into(), true),
        );
        m
    }

    fn ans(quotes: Vec<(usize, &str, &str)>) -> AnswerIn {
        AnswerIn {
            answer: "a".into(),
            quotes: quotes
                .into_iter()
                .map(|(n, q, u)| QuoteIn {
                    n,
                    quote: q.into(),
                    use_: Some(u.into()),
                    why: None,
                })
                .collect(),
            gaps: vec![],
            not_answerable: false,
        }
    }

    #[test]
    fn a_whole_read_with_true_quotes_passes_and_reports_what_was_not_read() {
        let c = [cand(1, "a"), cand(2, "b")];
        let r = check(
            &c,
            500,
            &now(),
            &[1, 2],
            &ans(vec![(1, "parser rejects empty input", "support")]),
        );
        assert_eq!(r.verdict, "PASS", "{:?}", r.errors);
        assert_eq!((r.read, r.not_read, r.quotes_found), (2, 498, 1));
    }

    #[test]
    fn every_rule_refuses() {
        let c = [cand(1, "a"), cand(2, "b")];
        let n = now();
        let errs = |read: &[usize], a: AnswerIn| check(&c, 10, &n, read, &a).errors.join("\n");
        assert!(errs(&[1], ans(vec![])).contains("candidate 2 (f.md#L2-2) was not read"));
        assert!(errs(&[1, 2, 2], ans(vec![])).contains("candidate 2 twice"));
        assert!(errs(&[1, 2, 9], ans(vec![])).contains("candidate 9, which is not in this packet"));
        assert!(
            errs(&[1, 2], ans(vec![(1, "the parser accepts input", "support")])).contains("not found word for word")
        );
        assert!(errs(&[1, 2], ans(vec![(1, "rejects empty", "support")])).contains("fewer than 3 words"));
        assert!(errs(&[1, 2], ans(vec![(7, "parser rejects empty", "support")])).contains("outside this packet"));
        assert!(errs(&[1, 2], ans(vec![(1, "parser rejects empty", "maybe")])).contains("support or note"));
        assert_eq!(check(&c, 10, &n, &[], &ans(vec![])).verdict, "NOT_EXECUTED");
    }

    #[test]
    fn a_unit_that_changed_or_left_cannot_carry_the_answer() {
        let c = [cand(1, "a")];
        let mut changed = now();
        changed.insert(
            "a".into(),
            ("other".into(), "The parser rejects empty input early.".into(), true),
        );
        let r = check(
            &c,
            10,
            &changed,
            &[1],
            &ans(vec![(1, "parser rejects empty input", "support")]),
        );
        assert!(
            r.errors.join(" ").contains("changed since it was retrieved"),
            "{:?}",
            r.errors
        );
        let mut gone = now();
        gone.insert(
            "a".into(),
            ("ha".into(), "The parser rejects empty input early.".into(), false),
        );
        let r = check(
            &c,
            10,
            &gone,
            &[1],
            &ans(vec![(1, "parser rejects empty input", "support")]),
        );
        assert!(r.errors.join(" ").contains("no longer present"));
        let r = check(
            &c,
            10,
            &gone,
            &[1],
            &ans(vec![(1, "parser rejects empty input", "note")]),
        );
        assert_eq!(r.verdict, "PASS", "a note may cite a unit that left: {:?}", r.errors);
    }

    #[test]
    fn whitespace_is_normalised_like_the_python() {
        assert_eq!(normalize("  a \n\t b  c "), "a b c");
    }
}
