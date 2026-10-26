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
//! Recorded facts come first: a live fact whose text matches the question puts its evidence unit at the
//! top of the packet, with the fact above it. Candidates are shown with their file line numbers; a quote
//! copied with those numbers still matches. An answer becomes a fact only when asked (`record`), and then
//! through the store's evidence rule: its first support quote is re-checked against the file.

use std::collections::{BTreeMap, HashMap};
use std::fmt::Write as _;

use anyhow::{Context, Result, ensure};
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
/// At most this many recorded facts go above the files in a packet.
pub const FACTS_K: usize = 5;

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
    /// Preferred: the file lines of the quote, inclusive, as the candidate numbers them (e.g. [120, 122]).
    /// The program copies those lines word for word, so nothing can be mistyped.
    #[serde(default)]
    pub lines: Option<[usize; 2]>,
    /// Only when lines cannot be given: text copied character for character from the candidate
    /// (whitespace may differ).
    #[serde(default)]
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
    /// When the check passes, keep the answer as a fact, its first support quote as the evidence.
    #[serde(default)]
    pub record: bool,
}

/// Lines `a..=b` (file numbering) of a unit whose locator starts at some line, or None when outside it.
fn span_text(unit_text: &str, locator: &str, a: usize, b: usize) -> Option<String> {
    let start = store::first_line(locator)?;
    let lines: Vec<&str> = unit_text.lines().collect();
    let end = start + lines.len().checked_sub(1)?;
    (a >= start && b >= a && b <= end).then(|| lines[a - start..=b - start].join("\n"))
}

/// The line of `unit_text` sharing the most words with `wanted` (normalized), so a refused quote can be
/// fixed from the refusal instead of a new ask.
fn nearest_line(unit_text: &str, wanted: &str) -> Option<String> {
    let want: std::collections::BTreeSet<String> = wanted.split(' ').map(str::to_lowercase).collect();
    unit_text
        .lines()
        .map(|l| {
            let have: std::collections::BTreeSet<String> = normalize(l).split(' ').map(str::to_lowercase).collect();
            (want.intersection(&have).count(), l)
        })
        .filter(|(n, _)| *n > 0)
        .max_by_key(|(n, _)| *n)
        .map(|(_, l)| normalize(l))
}

/// A quote as a reader copies it from a numbered candidate: line numbers (`120| `) are not the text.
fn strip_numbers(quote: &str) -> String {
    static NUM: std::sync::LazyLock<regex::Regex> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"(^|\s)\d{1,7}\| ").expect("valid"));
    NUM.replace_all(quote, " ").into_owned()
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
        let text = match (q.lines, now.get(&c.key)) {
            (Some([a, b]), Some((_, unit_text, _))) => match span_text(unit_text, &c.locator, a, b) {
                Some(t) => normalize(&t),
                None => {
                    errors.push(format!(
                        "quote {k}: lines {a}-{b} are not inside candidate {} ({})",
                        q.n, c.locator
                    ));
                    continue;
                }
            },
            _ => normalize(&strip_numbers(&q.quote)),
        };
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
                        "quote {k} not found word for word in candidate {} ({}){}",
                        q.n,
                        c.locator,
                        nearest_line(unit_text, &text)
                            .map_or(String::new(), |l| format!("; its nearest line reads: {l:?}"))
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
    /// One project and the projects its registry entry names as related (private ones too, never an
    /// isolated one); None = every shared project.
    pub project: Option<&'a str>,
    pub expert: Option<&'a str>,
    pub kinds: &'a [String],
    pub limit: usize,
}

/// What a query found across the projects it was allowed to read.
pub struct Found {
    /// Recorded facts' evidence units, best first; they go above the hits.
    pub facts: Vec<Hit>,
    pub hits: Vec<Hit>,
    pub searched: Vec<String>,
    /// Projects not searched, each with why (its level, or the error).
    pub skipped: Vec<String>,
    pub corpus_total: usize,
}

/// The one cross-project read: each project the query may read (a named one, or every shared one),
/// its best `limit` units, merged best first and cut to `limit`.
/// The projects a read may reach: a named project and the projects its entry lists as related
/// (private ones too), or, with none named, every shared project; the rest are skipped with the reason.
pub fn reach_names(home: &Home, store: &Store, project: Option<&str>) -> Result<(Vec<String>, Vec<String>)> {
    let registry = Registry::load(home)?;
    let mut skipped = Vec::new();
    let names = match project {
        Some(p) => {
            let project = registry.project(p)?;
            let mut names = vec![project.name.clone()];
            for r in &project.related {
                let name = registry.project(r)?.name.clone();
                if !names.contains(&name) {
                    names.push(name);
                }
            }
            names
        }
        None => {
            let mut names = Vec::new();
            for name in registry.projects.keys() {
                let level = store.level(&gw_store::project_ns(&store::slug(name)?)?)?;
                if level == "shared" {
                    names.push(name.clone());
                } else {
                    skipped.push(format!("{name} ({level})"));
                }
            }
            names
        }
    };
    Ok((names, skipped))
}

pub fn fan_out(home: &Home, store: &Store, q: Query<'_>) -> Result<Found> {
    let (names, skipped) = reach_names(home, store, q.project)?;
    let mut found = Found {
        facts: Vec::new(),
        hits: Vec::new(),
        searched: Vec::new(),
        skipped,
        corpus_total: 0,
    };
    for name in &names {
        match store.retrieve(name, q.text, q.expert, q.kinds, q.limit) {
            Ok(hits) => {
                found.corpus_total += store.corpus_size(name)?;
                found.searched.push(name.clone());
                found.hits.extend(hits);
                found.facts.extend(store.retrieve_facts(name, q.text, FACTS_K)?);
            }
            Err(e) => found.skipped.push(format!("{name} ({e:#})")),
        }
    }
    found
        .hits
        .sort_by(|a, b| b.score.unwrap_or(0.0).total_cmp(&a.score.unwrap_or(0.0)));
    found.hits.truncate(q.limit);
    found
        .facts
        .sort_by(|a, b| b.score.unwrap_or(0.0).total_cmp(&a.score.unwrap_or(0.0)));
    let mut seen = std::collections::BTreeSet::new();
    found.facts.retain(|h| seen.insert((h.project.clone(), h.key.clone())));
    found.facts.truncate(FACTS_K);
    // A fact's evidence is shown once, as the fact; the same unit is not repeated among the hits.
    found
        .hits
        .retain(|h| !seen.contains(&(h.project.clone(), h.key.clone())));
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
    let shown: Vec<&Hit> = found.facts.iter().chain(&found.hits).take(q.limit).collect();
    let candidates: Vec<Candidate> = shown
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
         your answer, and quotes given as line ranges (n, lines [a, b]; the program copies the text) or copied \
         word for word (at least 3 words). Each candidate says when its file last changed; when two disagree, \
         give both and say which is current and why.\n",
    );
    for (c, h) in candidates.iter().zip(&shown) {
        if let Some(fact) = &h.fact {
            writeln!(
                out,
                "\n[{}] RECORDED FACT ({}): {fact}\n    its evidence, {}:{}\n{}",
                c.n,
                h.fact_grade.as_deref().unwrap_or("?"),
                c.project,
                c.locator,
                store::numbered(&h.text, &h.locator)
            )?;
            continue;
        }
        let experts = if h.experts.is_empty() {
            String::new()
        } else {
            format!("; experts: {}", h.experts.join(", "))
        };
        let commit = h
            .commit
            .as_deref()
            .map_or(String::new(), |c| format!(" @ {}", &c[..c.len().min(10)]));
        let changed = h
            .changed_at
            .as_deref()
            .map_or(String::new(), |d| format!("; changed {}", &d[..d.len().min(10)]));
        writeln!(
            out,
            "\n[{}] {}:{}{commit} ({}{experts}{changed})\n{}",
            c.n,
            c.project,
            c.locator,
            c.kind.as_deref().unwrap_or("?"),
            store::numbered(&h.text, &h.locator)
        )?;
    }
    Ok(out)
}

/// Check an answer against its packet and record the verdict. Returns (text, passed).
pub fn answer(
    home: &Home,
    store: &Store,
    packet_id: &str,
    read: &[usize],
    answer: &AnswerIn,
) -> Result<(String, bool)> {
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
        "quotes": answer.quotes.iter().map(|q| json!({"n": q.n, "quote": q.quote, "lines": q.lines, "use": q.use_.as_deref().unwrap_or("support"), "why": q.why})).collect::<Vec<_>>(),
        "gaps": answer.gaps,
        "not_answerable": answer.not_answerable,
    });
    store.finish_packet(packet_id, report.verdict, &answer_json, &serde_json::to_value(&report)?)?;
    let mut out = format!("{packet_id}: {}\n", report.header());
    for e in &report.errors {
        writeln!(out, "  - {e}")?;
    }
    let passed = report.verdict == "PASS";
    if passed && answer.record && std::env::var_os("GROUNDWORKS_NO_RECORD").is_some() {
        out.push_str("not recorded: recording is off in this session (GROUNDWORKS_NO_RECORD).\n");
    } else if passed && answer.record {
        match record(home, store, packet_id, &packet, &candidates, &now, answer) {
            Ok(line) => writeln!(out, "{line}")?,
            Err(e) => writeln!(out, "not recorded: {e:#}")?,
        }
    } else if passed {
        out.push_str(
            "Every quote was found word for word in a candidate that was read. To keep the answer as a checked \
             fact for the next session, answer again with record: true.\n",
        );
    }
    Ok((out, passed))
}

/// Keep a passed answer as a fact: its first support quote, widened to the whole lines it sits on, is the
/// evidence, and the store checks it against the file again (so a file that moved since retrieval refuses).
fn record(
    home: &Home,
    store: &Store,
    packet_id: &str,
    packet: &Value,
    candidates: &[Candidate],
    now: &Now,
    answer: &AnswerIn,
) -> Result<String> {
    let q = answer
        .quotes
        .iter()
        .find(|q| q.use_.as_deref().unwrap_or("support") == "support")
        .context("no support quote to keep it by")?;
    let c = candidates
        .iter()
        .find(|c| c.n == q.n)
        .context("the quote's candidate")?;
    let (path, _) = c
        .locator
        .rsplit_once("#L")
        .context("the evidence is not a file span (a commit cannot be quoted as a file)")?;
    let start = store::first_line(&c.locator).context("the evidence has no line numbers")?;
    let (_, text, _) = now.get(&c.key).context("the evidence unit is gone")?;
    let lines: Vec<&str> = text.lines().collect();
    let want = match q.lines {
        Some([a, b]) => normalize(&span_text(text, &c.locator, a, b).context("the quote's lines")?),
        None => normalize(&strip_numbers(&q.quote)),
    };
    // The smallest run of whole lines that holds the quote.
    let mut best: Option<(usize, usize)> = None;
    for i in 0..lines.len() {
        for j in i..lines.len() {
            if best.is_some_and(|(a, b)| j - i >= b - a) {
                break;
            }
            if normalize(&lines[i..=j].join(" ")).contains(&want) {
                best = Some((i, j));
                break;
            }
        }
    }
    let (i, j) = best.context("the quote is not inside whole lines of its candidate")?;
    let project = Registry::load(home)?.project(&c.project)?.clone();
    let mut fact_text: String = answer.answer.trim().chars().take(1500).collect();
    if fact_text.is_empty() {
        fact_text = want.clone();
    }
    let fact = store::FactIn {
        kind: crate::journal::Kind::Fact,
        text: fact_text,
        confidence: crate::journal::Confidence::Confirmed,
        source: format!("{packet_id}: {}", packet["question"].as_str().unwrap_or_default()),
        by: packet["by"].as_str().unwrap_or("ask").to_owned(),
        receipt: None,
        owner_statement: None,
        quote: Some(store::Quote {
            file: path.to_owned(),
            line: start + i,
            text: lines[i..=j].join("\n"),
        }),
        lens: None,
        attacks: None,
        stance: None,
    };
    let id = store.add_fact(&project.path(&home.workspace), &project.name, &fact)?;
    Ok(format!(
        "recorded {id} in {}: CONFIRMED by {}#L{}-{}",
        project.name,
        path,
        start + i,
        start + j
    ))
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
                    lines: None,
                    use_: Some(u.into()),
                    why: None,
                })
                .collect(),
            gaps: vec![],
            not_answerable: false,
            record: false,
        }
    }

    #[test]
    fn a_quote_copied_with_its_line_numbers_still_matches() {
        let c = [cand(1, "a")];
        let numbered = store::numbered("The parser   rejects empty\ninput early.", "f.md#L41-42");
        assert_eq!(numbered, "41| The parser   rejects empty\n42| input early.");
        let r = check(
            &c,
            10,
            &now(),
            &[1],
            &ans(vec![(1, "41| parser rejects empty 42| input", "support")]),
        );
        assert_eq!(r.verdict, "PASS", "{:?}", r.errors);
        // A number inside the text is text: "rejects 3| empty" is not in the unit.
        let r = check(
            &c,
            10,
            &now(),
            &[1],
            &ans(vec![(1, "parser rejects 3|empty input", "support")]),
        );
        assert_eq!(r.verdict, "FAIL");
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
    fn a_quote_given_as_lines_is_copied_by_the_program_and_checked_for_range() {
        let c = [cand(1, "a")];
        let mut by_lines = ans(vec![]);
        by_lines.quotes.push(QuoteIn {
            n: 1,
            quote: String::new(),
            lines: Some([1, 2]),
            use_: None,
            why: None,
        });
        let r = check(&c, 10, &now(), &[1], &by_lines);
        assert_eq!(r.verdict, "PASS", "{:?}", r.errors);
        by_lines.quotes[0].lines = Some([2, 5]);
        let r = check(&c, 10, &now(), &[1], &by_lines);
        assert!(r.errors.join(" ").contains("not inside candidate"), "{:?}", r.errors);
        assert_eq!(span_text("a\nb\nc", "f.md#L10-12", 11, 12).as_deref(), Some("b\nc"));
        assert_eq!(span_text("a\nb\nc", "f.md#L10-12", 9, 10), None);
    }

    #[test]
    fn whitespace_is_normalised_like_the_python() {
        assert_eq!(normalize("  a \n\t b  c "), "a b c");
    }
}
