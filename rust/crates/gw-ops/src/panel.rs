//! Panels: a fixed set of seats (lenses) judge one question in two rounds, and a program, not the
//! coordinator, checks the protocol before it tallies. Ported from the Python engine's
//! `groundworks/panel/panel.py` (tag v0.2.0; protocol in its PROTOCOL.md), rule for rule on the checks and
//! the tally, with these stated deviations:
//!
//! 1. A panel is a record in the project's store, not a run directory. It is locked when it is opened (the
//!    validated config and its digest are written once), so "answers before the lock" cannot happen.
//! 2. The round-2 label order comes from the lock's digest as in Python, by sorting seats on
//!    sha256(digest, seat) instead of Python's `random.Random(digest).shuffle`.
//! 3. One call moves a panel on: `status` anonymizes round 1 once every round-1 answer is on file, and
//!    tallies once nothing is owed. Python had separate `anonymize` and `tally` commands.
//! 4. The owner's thresholds come from `registry/panel.toml` in the home, if it exists (Python: a settings
//!    file passed at lock). A panel that sets different ones is refused. There is no launch identity, so a
//!    seat is not prevented from submitting another seat's answer; every answer records who sent it.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::sync::LazyLock;

use anyhow::{Result, bail, ensure};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::store::{Store, sha256_hex};

pub const MIN_NEW_WORDS: usize = 5;
pub const MIN_FLAW_WORDS: usize = 3;
pub const WEIGHTED_SEAT_BASE: f64 = 1.5;
const CONSENSUS: f64 = 2.0 / 3.0;
const AGREEMENT: f64 = 0.70;
const DISSENT_QUOTA: u64 = 2;
/// high, med, low.
const CONFIDENCE: [f64; 3] = [1.0, 0.75, 0.5];

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Seat {
    pub id: String,
    #[serde(default)]
    pub lens: Option<String>,
    #[serde(default)]
    pub aliases: Vec<String>,
}

/// A validated, locked panel. Serialized in this field order, it is what the digest covers.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Panel {
    pub panel_id: String,
    pub target: String,
    pub question: String,
    pub options: Vec<String>,
    pub seats: Vec<Seat>,
    pub categories: Vec<String>,
    pub weighted_seat: Option<String>,
    pub consensus: f64,
    pub agreement: f64,
    pub dissent_quota: usize,
    /// Weights for high, med, low.
    pub confidence: [f64; 3],
}

impl Panel {
    pub fn ids(&self) -> impl Iterator<Item = &str> {
        self.seats.iter().map(|s| s.id.as_str())
    }

    fn weight(&self, c: Conf) -> f64 {
        self.confidence[c as usize]
    }

    pub fn digest(&self) -> String {
        sha256_hex(&[serde_json::to_string(self).unwrap_or_default().as_bytes()])
    }
}

/// Every spelling that would reveal a seat: its id, its lens, its aliases, with _ read as space or hyphen.
fn identity_terms(seat: &Seat) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    for term in [Some(&seat.id), seat.lens.as_ref()]
        .into_iter()
        .flatten()
        .chain(&seat.aliases)
    {
        let term = term.trim();
        if !term.is_empty() {
            out.insert(term.to_owned());
            out.insert(term.replace('_', " "));
            out.insert(term.replace('_', "-"));
        }
    }
    out
}

const SETTINGS_KEYS: [&str; 2] = ["thresholds", "confidence_weights"];

/// Check a panel as Python's `load_panel` does, after copying in the owner's settings (`apply_settings`).
pub fn validate(cfg: &Value, settings: Option<&Value>) -> Result<Panel> {
    let mut cfg = cfg.clone();
    ensure!(cfg.is_object(), "a panel is a JSON object");
    if let Some(settings) = settings {
        for key in SETTINGS_KEYS {
            let Some(owner) = settings.get(key) else { continue };
            match cfg.get(key) {
                Some(mine) if mine != owner => {
                    bail!("the panel's {key} differ from the owner's settings (registry/panel.toml); leave them out")
                }
                _ => cfg[key] = owner.clone(),
            }
        }
    }
    let text = |k: &str| {
        cfg.get(k)
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or_default()
            .to_owned()
    };
    let mut errors: Vec<String> = Vec::new();
    for key in ["panel_id", "target", "question"] {
        if text(key).is_empty() {
            errors.push(format!("missing {key}"));
        }
    }
    let seats: Vec<Seat> = match cfg.get("seats").cloned().map(serde_json::from_value) {
        Some(Ok(s)) => s,
        _ => {
            errors.push("seats must be a list of {id, lens, aliases}".into());
            Vec::new()
        }
    };
    let ids: Vec<&str> = seats.iter().map(|s| s.id.as_str()).collect();
    if seats.len() < 3 {
        errors.push("a panel needs at least 3 seats".into());
    }
    if ids.iter().any(|i| i.trim().is_empty()) || ids.iter().collect::<BTreeSet<_>>().len() != ids.len() {
        errors.push("seat ids must be present and unique".into());
    }
    let mut owner: BTreeMap<String, &str> = BTreeMap::new();
    for seat in &seats {
        for term in identity_terms(seat) {
            let other = *owner.entry(term.to_lowercase()).or_insert(&seat.id);
            if other != seat.id {
                errors.push(format!("identity term {term:?} is shared by {other} and {}", seat.id));
            }
        }
    }
    let options: Vec<String> = cfg
        .get("options")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .map(|o| {
                    o.as_str()
                        .map_or_else(|| o.to_string(), str::to_owned)
                        .trim()
                        .to_lowercase()
                })
                .collect()
        })
        .unwrap_or_default();
    if options.len() < 2 || options.iter().collect::<BTreeSet<_>>().len() != options.len() {
        errors.push("options needs at least 2 distinct entries".into());
    }
    if options.iter().any(|o| o == "abstain") {
        errors.push("'abstain' is reserved".into());
    }
    let weighted_seat = match cfg.get("weighted_seat") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) if ids.contains(&s.as_str()) => Some(s.clone()),
        Some(other) => {
            errors.push(format!("weighted_seat {other} is not a seat"));
            None
        }
    };
    let th = cfg.get("thresholds").cloned().unwrap_or(json!({}));
    let num = |k: &str, default: f64| match th.get(k) {
        None => Some(default),
        Some(v) => v.as_f64(),
    };
    let consensus = num("consensus", CONSENSUS);
    let agreement = num("agreement", AGREEMENT);
    let quota = match th.get("dissent_quota") {
        None => Some(DISSENT_QUOTA),
        Some(v) => v.as_u64(),
    };
    if !consensus.is_some_and(|c| 0.5 < c && c <= 1.0) {
        errors.push("thresholds.consensus must be above 0.5 and at most 1".into());
    }
    if !agreement.is_some_and(|a| (0.5..1.0).contains(&a)) {
        errors.push("thresholds.agreement must be at least 0.5 and below 1 (1 would switch the check off)".into());
    }
    let quota = quota.filter(|q| (1..=seats.len() as u64).contains(q));
    if quota.is_none() {
        errors.push("thresholds.dissent_quota must be an integer from 1 to the number of seats".into());
    }
    // Objection categories are required: without them the category check was switched off in silence and
    // dissent was keyed on wording (Python, measured 2026-09-16). Fewer categories than the quota could never
    // satisfy it, so that is refused here rather than re-prompted forever.
    let categories: Option<Vec<String>> = cfg
        .get("objection_categories")
        .and_then(Value::as_array)
        .and_then(|a| a.iter().map(|c| c.as_str().map(|c| c.trim().to_lowercase())).collect());
    match &categories {
        Some(c)
            if !c.is_empty()
                && c.iter().all(|c| !c.is_empty())
                && c.iter().collect::<BTreeSet<_>>().len() == c.len() =>
        {
            if let Some(q) = quota
                && (c.len() as u64) < q
            {
                errors.push(format!(
                    "objection_categories has {} categories but thresholds.dissent_quota needs {q} distinct ones",
                    c.len()
                ));
            }
        }
        _ => errors.push(
            "objection_categories must be a list of distinct, non-empty category names: dissent and the \
             agreement check are judged by category, and without the list neither can be checked"
                .into(),
        ),
    }
    let mut weights = CONFIDENCE;
    let mut weights_ok = true;
    if let Some(w) = cfg.get("confidence_weights") {
        match w.as_object() {
            Some(map) => {
                for (k, v) in map {
                    let slot = ["high", "med", "low"].iter().position(|n| n == k);
                    match (slot, v.as_f64()) {
                        (Some(i), Some(x)) if !v.is_boolean() => weights[i] = x,
                        _ => weights_ok = false,
                    }
                }
            }
            None => weights_ok = false,
        }
    }
    let [high, med, low] = weights;
    if !(weights_ok && 0.0 < low && low <= med && med <= high && high <= 1.0) {
        errors.push("confidence_weights must set only high, med, low with 0 < low <= med <= high <= 1".into());
    }
    ensure!(errors.is_empty(), "panel invalid: {}", errors.join("; "));
    Ok(Panel {
        panel_id: text("panel_id"),
        target: text("target"),
        question: text("question"),
        options,
        seats,
        categories: categories.unwrap_or_default(),
        weighted_seat,
        consensus: consensus.unwrap_or(CONSENSUS),
        agreement: agreement.unwrap_or(AGREEMENT),
        dissent_quota: quota.unwrap_or(DISSENT_QUOTA) as usize,
        confidence: weights,
    })
}

// ---- machine lines ------------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Conf {
    High = 0,
    Med = 1,
    Low = 2,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Stance {
    pub option: String,
    pub confidence: Conf,
    pub dealbreaker: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Objection {
    pub category: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Flaw {
    pub quote: String,
    pub why: String,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct Parsed {
    /// Exactly one well-formed STANCE line, or none.
    pub stance: Option<Stance>,
    pub objections: Vec<Objection>,
    pub new: Vec<String>,
    pub flaws: Vec<Flaw>,
}

static STANCE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^STANCE:\s*(?P<option>[^|]+?)\s*\|\s*CONFIDENCE:\s*(?P<conf>high|med|low)\s*\|\s*DEALBREAKER:\s*(?P<deal>yes|no)\s*$",
    )
    .expect("valid")
});
static OBJECTION_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^OBJECTION:\s*(?P<cat>[^|]+?)\s*\|\s*(?P<text>\S.*?)\s*$").expect("valid"));
static NEW_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^NEW:\s*(?P<text>\S.*?)\s*$").expect("valid"));
static FLAW_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"^FLAW:\s*"(?P<quote>[^"]+)"\s*\|\s*(?P<why>\S.*?)\s*$"#).expect("valid"));

/// The machine lines of an answer. Models write prose; only these lines are read.
pub fn parse(text: &str) -> Parsed {
    let mut found = Parsed::default();
    let mut stance_lines = 0;
    for line in text.lines().map(str::trim) {
        if line.starts_with("STANCE:") {
            stance_lines += 1;
            if let Some(m) = STANCE_RE.captures(line) {
                found.stance = Some(Stance {
                    option: m["option"].trim().to_lowercase(),
                    confidence: match &m["conf"] {
                        "high" => Conf::High,
                        "med" => Conf::Med,
                        _ => Conf::Low,
                    },
                    dealbreaker: &m["deal"] == "yes",
                });
            }
        } else if let Some(m) = OBJECTION_RE.captures(line) {
            found.objections.push(Objection {
                category: m["cat"].trim().to_lowercase(),
                text: m["text"].to_owned(),
            });
        } else if let Some(m) = NEW_RE.captures(line) {
            found.new.push(m["text"].to_owned());
        } else if let Some(m) = FLAW_RE.captures(line) {
            found.flaws.push(Flaw {
                quote: m["quote"].to_owned(),
                why: m["why"].to_owned(),
            });
        }
    }
    if stance_lines != 1 {
        found.stance = None;
    }
    found
}

fn stance_ok(stance: Option<&Stance>, cfg: &Panel) -> bool {
    stance.is_some_and(|s| s.option == "abstain" || cfg.options.contains(&s.option))
}

/// Python's `words`: lowercase, every run of other than a-z0-9 is a break.
fn words(text: &str) -> Vec<String> {
    text.to_lowercase()
        .split(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit()))
        .filter(|w| !w.is_empty())
        .map(str::to_owned)
        .collect()
}

fn norm(text: &str) -> String {
    words(text).join(" ")
}

fn squash(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

// ---- what is owed --------------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Owed {
    pub condition: &'static str,
    pub seat: String,
    pub detail: String,
}

fn owed(condition: &'static str, seat: &str, detail: impl Into<String>) -> Owed {
    Owed {
        condition,
        seat: seat.to_owned(),
        detail: detail.into(),
    }
}

/// The re-prompt to send to the seat, word for word as the Python protocol words it.
pub fn owed_prompt(o: &Owed, cfg: &Panel) -> String {
    let options = cfg
        .options
        .iter()
        .map(String::as_str)
        .chain(["abstain"])
        .collect::<Vec<_>>()
        .join("|");
    let cats = cfg.categories.join("|");
    match o.condition {
        "missing_answer" => "No answer is on file for this seat in this round. Run the round for it.".into(),
        "missing_stance" => format!(
            "End your answer with exactly one line, and no other STANCE line: \
             STANCE: <{options}> | CONFIDENCE: high|med|low | DEALBREAKER: yes|no"
        ),
        "novelty_gate" => format!(
            "Your round-2 answer adds nothing your round-1 answer did not already say. Engage the strongest \
             answer that disagrees with yours, by its Member label, and add at least one line \
             NEW: <a claim, risk, test or reading you did not make in round 1> ({MIN_NEW_WORDS} words or more)."
        ),
        "flaw_required" => "You changed your stance from round 1. Change only if you can name the flaw in your \
             earlier argument: add FLAW: \"<exact words from your round-1 answer>\" | <why that reasoning was \
             wrong>. If you cannot name one, restore your round-1 stance."
            .into(),
        "bad_objection" => format!("Objection categories must be one of: {cats}. Found: {}.", o.detail),
        "dissent_quota" => format!(
            "The panel has {}. State your strongest objection to the majority position, on a point no other \
             seat has raised: OBJECTION: <{cats}> | <the objection>.",
            o.detail
        ),
        "agreement_check" => format!(
            "Most seats back '{}'. Assume that is wrong. Submit a counterfactual for seat {}: the strongest \
             alternative as OBJECTION: <{cats}> | <text> lines, at least one of them ({MIN_FLAW_WORDS} words or \
             more) an objection your round-2 answer does not already make, and what evidence would flip the \
             decision. This does not change your stance; to change it, revise your round-2 answer with a FLAW line.",
            o.detail, o.seat
        ),
        other => other.to_owned(),
    }
}

/// Round 1 before anonymizing: every seat has an answer with a valid stance.
pub fn round1_owed(cfg: &Panel, r1: &BTreeMap<String, String>) -> Vec<Owed> {
    cfg.ids()
        .filter_map(|sid| match r1.get(sid) {
            None => Some(owed("missing_answer", sid, "")),
            Some(t) if !stance_ok(parse(t).stance.as_ref(), cfg) => Some(owed("missing_stance", sid, "")),
            Some(_) => None,
        })
        .collect()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Anon {
    /// seat id -> "Member A", ...
    pub labels: BTreeMap<String, String>,
    pub order: Vec<String>,
    pub round1_sha256: BTreeMap<String, String>,
    pub bundle: String,
}

/// Every round-1 answer under a Member label, identities replaced, in an order fixed by the lock.
pub fn anonymize(cfg: &Panel, digest: &str, r1: &BTreeMap<String, String>) -> Result<Anon> {
    let problems: Vec<String> = round1_owed(cfg, r1)
        .iter()
        .map(|o| format!("{}: {}", o.seat, owed_prompt(o, cfg)))
        .collect();
    ensure!(problems.is_empty(), "round 1 incomplete:\n  {}", problems.join("\n  "));
    let mut order: Vec<String> = cfg.ids().map(str::to_owned).collect();
    order.sort_by_cached_key(|sid| sha256_hex(&[digest.as_bytes(), b"\n", sid.as_bytes()]));
    let labels: BTreeMap<String, String> = order
        .iter()
        .enumerate()
        .map(|(i, sid)| (sid.clone(), format!("Member {}", char::from(b'A' + i as u8))))
        .collect();
    let mut terms: Vec<(String, &str)> = cfg
        .seats
        .iter()
        .flat_map(|s| {
            identity_terms(s)
                .into_iter()
                .map(|t| (t.to_lowercase(), labels[&s.id].as_str()))
        })
        .collect();
    terms.sort_by(|a, b| b.0.len().cmp(&a.0.len()).then_with(|| a.0.cmp(&b.0)));
    terms.dedup_by(|a, b| a.0 == b.0);
    let mut parts = vec![
        "# Round 1 answers, anonymized".to_owned(),
        String::new(),
        "One of these is your own. Judge each by its argument, not its source.".to_owned(),
        String::new(),
    ];
    for sid in &order {
        parts.push(format!("## {}", labels[sid]));
        parts.push(String::new());
        parts.push(replace_terms(&r1[sid], &terms).trim().to_owned());
        parts.push(String::new());
    }
    Ok(Anon {
        round1_sha256: order
            .iter()
            .map(|sid| (sid.clone(), sha256_hex(&[r1[sid].as_bytes()])))
            .collect(),
        labels,
        order,
        bundle: parts.join("\n"),
    })
}

/// Replace each term (lowercase, longest first) where it stands alone: not touching a letter or digit,
/// case-insensitively. Python: a regex with `(?<![A-Za-z0-9])` and `(?![A-Za-z0-9])` around the terms.
fn replace_terms(text: &str, terms: &[(String, &str)]) -> String {
    let lower = text.to_ascii_lowercase();
    let bytes = text.as_bytes();
    let alnum = |i: usize| bytes.get(i).is_some_and(u8::is_ascii_alphanumeric);
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < text.len() {
        let hit = (i == 0 || !alnum(i - 1))
            .then(|| {
                terms
                    .iter()
                    .find(|(t, _)| lower[i..].starts_with(t.as_str()) && !alnum(i + t.len()))
            })
            .flatten();
        match hit {
            Some((t, label)) => {
                out.push_str(label);
                i += t.len();
            }
            None => {
                let ch = text[i..].chars().next().unwrap_or(' ');
                out.push(ch);
                i += ch.len_utf8();
            }
        }
    }
    out
}

/// The seats chosen to argue the counterfactual, kept while the majority option stays the same.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Chosen {
    pub option: String,
    pub seats: Vec<String>,
}

/// What a round-2 check reads: the latest answer of each seat, per round.
#[derive(Debug, Default)]
pub struct Answers {
    pub round1: BTreeMap<String, String>,
    pub round2: BTreeMap<String, String>,
    pub counterfactual: BTreeMap<String, String>,
}

/// At least `dissent_quota` seats must each contribute an objection category no other counted seat raised.
fn dissent_owed(cfg: &Panel, r2: &BTreeMap<&str, Parsed>) -> Vec<Owed> {
    let keys: BTreeMap<&str, BTreeSet<&str>> = r2
        .iter()
        .map(|(sid, p)| {
            (
                *sid,
                p.objections
                    .iter()
                    .map(|o| o.category.as_str())
                    .filter(|k| !k.is_empty())
                    .collect(),
            )
        })
        .collect();
    let mut seats: Vec<&str> = keys.keys().copied().collect();
    seats.sort_by_key(|s| (keys[s].len(), *s));
    let (mut used, mut dissenters) = (BTreeSet::new(), Vec::new());
    for sid in seats {
        if let Some(fresh) = keys[sid].iter().find(|k| !used.contains(*k)) {
            used.insert(*fresh);
            dissenters.push(sid);
        }
    }
    if dissenters.len() >= cfg.dissent_quota {
        return Vec::new();
    }
    let detail = format!(
        "{} distinct objection(s) from {} seat(s), {} required",
        used.len(),
        dissenters.len(),
        cfg.dissent_quota
    );
    cfg.ids()
        .filter(|sid| !dissenters.contains(sid))
        .take(cfg.dissent_quota - dissenters.len())
        .map(|sid| owed("dissent_quota", sid, detail.clone()))
        .collect()
}

/// A counterfactual does its work only with an objection the seat has not already made: every objection
/// in a panel category, and one of at least MIN_FLAW_WORDS words not already in the seat's round-2 answer.
fn counterfactual_ok(cfg: &Panel, counter: Option<&String>, round2: &str) -> bool {
    let objections = parse(counter.map_or("", String::as_str)).objections;
    if objections.is_empty() || objections.iter().any(|o| !cfg.categories.contains(&o.category)) {
        return false;
    }
    let own = norm(round2);
    objections
        .iter()
        .any(|o| words(&o.text).len() >= MIN_FLAW_WORDS && !own.contains(&norm(&o.text)))
}

/// Above the agreement threshold, the two least confident majority seats owe a counterfactual.
fn agreement_owed(
    cfg: &Panel,
    r2: &BTreeMap<&str, Parsed>,
    a: &Answers,
    chosen: Option<&Chosen>,
) -> (Vec<Owed>, Option<Chosen>) {
    let stance = |sid: &str| r2[sid].stance.as_ref().expect("checked by the caller");
    // Counter.most_common: the highest count, the first seen on a tie (seat order).
    let mut counts: Vec<(&str, usize)> = Vec::new();
    for sid in cfg.ids() {
        let option = stance(sid).option.as_str();
        if option == "abstain" {
            continue;
        }
        match counts.iter_mut().find(|(o, _)| *o == option) {
            Some((_, n)) => *n += 1,
            None => counts.push((option, 1)),
        }
    }
    let Some(&(top, n)) = counts.iter().reduce(|best, c| if c.1 > best.1 { c } else { best }) else {
        return (Vec::new(), None);
    };
    if n as f64 / cfg.seats.len() as f64 <= cfg.agreement {
        return (Vec::new(), None);
    }
    let chosen = match chosen.filter(|c| c.option == top) {
        Some(c) => c.clone(),
        None => {
            let mut majority: Vec<&str> = cfg.ids().filter(|sid| stance(sid).option == top).collect();
            majority.sort_by(|x, y| {
                cfg.weight(stance(x).confidence)
                    .total_cmp(&cfg.weight(stance(y).confidence))
                    .then(r2[x].objections.len().cmp(&r2[y].objections.len()))
                    .then(x.cmp(y))
            });
            Chosen {
                option: top.to_owned(),
                seats: majority.into_iter().take(2).map(str::to_owned).collect(),
            }
        }
    };
    let owed = chosen
        .seats
        .iter()
        .filter(|sid| !counterfactual_ok(cfg, a.counterfactual.get(*sid), &a.round2[*sid]))
        .map(|sid| owed("agreement_check", sid, top))
        .collect();
    (owed, Some(chosen))
}

/// Every re-prompt owed in round 2, and the seats chosen for the agreement check. Empty means the protocol
/// is satisfied.
pub fn evaluate(cfg: &Panel, a: &Answers, chosen: Option<&Chosen>) -> (Vec<Owed>, Option<Chosen>) {
    let mut out = Vec::new();
    let mut r2: BTreeMap<&str, Parsed> = BTreeMap::new();
    for sid in cfg.ids() {
        let t1 = a.round1.get(sid).map_or("", String::as_str);
        let p1 = parse(t1);
        let Some(t2) = a.round2.get(sid) else {
            out.push(owed("missing_answer", sid, ""));
            continue;
        };
        let p2 = parse(t2);
        match (&p2.stance, &p1.stance) {
            (s2, _) if !stance_ok(s2.as_ref(), cfg) => out.push(owed("missing_stance", sid, "")),
            (Some(s2), Some(s1)) if s2.option != s1.option => {
                let named = p2
                    .flaws
                    .iter()
                    .any(|f| words(&f.quote).len() >= MIN_FLAW_WORDS && squash(t1).contains(&squash(&f.quote)));
                if !named {
                    out.push(owed("flaw_required", sid, ""));
                }
            }
            _ => {}
        }
        let t1n = norm(t1);
        if !p2
            .new
            .iter()
            .any(|n| words(n).len() >= MIN_NEW_WORDS && !t1n.contains(&norm(n)))
        {
            out.push(owed("novelty_gate", sid, ""));
        }
        let bad: BTreeSet<&str> = p2
            .objections
            .iter()
            .map(|o| o.category.as_str())
            .filter(|c| !cfg.categories.iter().any(|k| k == c))
            .collect();
        if !bad.is_empty() {
            out.push(owed(
                "bad_objection",
                sid,
                bad.into_iter().collect::<Vec<_>>().join(", "),
            ));
        }
        r2.insert(sid, p2);
    }
    let mut kept = None;
    if r2.len() == cfg.seats.len() {
        out.extend(dissent_owed(cfg, &r2));
        if r2.values().all(|p| stance_ok(p.stance.as_ref(), cfg)) {
            let (o, c) = agreement_owed(cfg, &r2, a, chosen);
            out.extend(o);
            kept = c;
        }
    }
    (out, kept)
}

// ---- tally ---------------------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct Backer {
    pub seat: String,
    pub base: f64,
    pub confidence: Conf,
}

#[derive(Debug, Clone, Serialize)]
pub struct Tally {
    pub result: &'static str,
    pub verdict: Option<String>,
    pub w_total: f64,
    pub bar: f64,
    pub weights: BTreeMap<String, f64>,
    pub backers: BTreeMap<String, Vec<Backer>>,
    pub weighted_seat: Option<String>,
    /// Seats whose dealbreaker stance is not the verdict: kept, never averaged away.
    pub minority_report: Vec<Value>,
    pub seats: Vec<Value>,
}

/// The weighted tally of round-2 stances. Base weights make the bar, so a hesitant panel cannot lower it.
pub fn tally(cfg: &Panel, a: &Answers) -> Tally {
    let parsed: Vec<(&Seat, Parsed)> = cfg
        .seats
        .iter()
        .map(|s| (s, parse(a.round2.get(&s.id).map_or("", String::as_str))))
        .collect();
    let base = |sid: &str| {
        if cfg.weighted_seat.as_deref() == Some(sid) {
            WEIGHTED_SEAT_BASE
        } else {
            1.0
        }
    };
    let w_total: f64 = cfg.ids().map(base).sum();
    let mut weights: Vec<(String, f64)> = cfg.options.iter().map(|o| (o.clone(), 0.0)).collect();
    let mut backers: BTreeMap<String, Vec<Backer>> = cfg.options.iter().map(|o| (o.clone(), Vec::new())).collect();
    for (seat, p) in &parsed {
        let Some(s) = p.stance.as_ref().filter(|s| s.option != "abstain") else {
            continue;
        };
        if let Some(w) = weights.iter_mut().find(|(o, _)| *o == s.option) {
            w.1 += base(&seat.id) * cfg.weight(s.confidence);
        }
        if let Some(b) = backers.get_mut(&s.option) {
            b.push(Backer {
                seat: seat.id.clone(),
                base: base(&seat.id),
                confidence: s.confidence,
            });
        }
    }
    let bar = cfg.consensus * w_total;
    // Python's max(): the first option with the highest weight, among those at or above the bar.
    let verdict = weights
        .iter()
        .filter(|(_, w)| w + 1e-9 >= bar)
        .fold(None::<&(String, f64)>, |best, c| match best {
            Some(b) if b.1 >= c.1 => Some(b),
            _ => Some(c),
        })
        .map(|(o, _)| o.clone());
    let minority_report = parsed
        .iter()
        .filter_map(|(seat, p)| {
            let s = p.stance.as_ref()?;
            (s.dealbreaker && Some(&s.option) != verdict.as_ref())
                .then(|| json!({"seat": seat.id, "lens": seat.lens, "stance": s.option}))
        })
        .collect();
    let seats = parsed
        .iter()
        .map(|(seat, p)| {
            let counter = parse(a.counterfactual.get(&seat.id).map_or("", String::as_str));
            json!({"seat": seat.id, "lens": seat.lens, "stance": p.stance, "objections": p.objections,
                   "new": p.new, "flaws": p.flaws, "counterfactual_objections": counter.objections})
        })
        .collect();
    Tally {
        result: if verdict.is_some() { "consensus" } else { "split" },
        verdict,
        w_total,
        bar: (bar * 1e4).round() / 1e4,
        weights: weights.into_iter().map(|(o, w)| (o, (w * 1e4).round() / 1e4)).collect(),
        backers,
        weighted_seat: cfg.weighted_seat.clone(),
        minority_report,
        seats,
    }
}

// ---- the store -----------------------------------------------------------------------------------

/// The owner's panel settings, if the home has `registry/panel.toml`.
pub fn owner_settings(home: &crate::home::Home) -> Result<Option<Value>> {
    let path = home.root.join("registry").join("panel.toml");
    if !path.exists() {
        return Ok(None);
    }
    let table: toml::Table = toml::from_str(&std::fs::read_to_string(&path)?)?;
    Ok(Some(serde_json::to_value(table)?))
}

fn round_name(round: &str) -> Result<&'static str> {
    Ok(match round.trim().to_lowercase().as_str() {
        "1" | "round1" => "round1",
        "2" | "round2" => "round2",
        "counterfactual" => "counterfactual",
        other => bail!("round must be 1, 2 or counterfactual (got {other:?})"),
    })
}

/// The round-1 brief every seat gets: the question, the options, and the line the program reads.
fn brief(cfg: &Panel) -> String {
    format!(
        "Question: {}\nTarget: {}\nSeats: {}\nEach seat answers BLIND (only the question, its evidence and its own lens), \
         then submits with panel_submit round 1. The answer must end with exactly one line:\n  {}\n\
         Objection lines, when you make them: OBJECTION: <{}> | <the objection>",
        cfg.question,
        cfg.target,
        cfg.ids().collect::<Vec<_>>().join(", "),
        owed_prompt(&owed("missing_stance", "", ""), cfg)
            .rsplit_once(": STANCE")
            .map_or_else(String::new, |(_, s)| format!("STANCE{s}")),
        cfg.categories.join("|")
    )
}

/// Open (and lock) a panel in the project's store.
pub fn open(store: &Store, project: &str, cfg: &Value, settings: Option<&Value>, by: &str) -> Result<String> {
    let panel = validate(cfg, settings)?;
    let config = serde_json::to_string(&panel)?;
    let digest = panel.digest();
    let id = store.panel_create(project, &config, &digest, by)?;
    Ok(format!("{id} | LOCKED sha256={digest}\n{}", brief(&panel)))
}

/// A panel as stored: config verified against its digest.
pub struct Stored {
    pub id: String,
    pub cfg: Panel,
    pub digest: String,
    pub status: String,
    pub anon: Option<Anon>,
    pub chosen: Option<Chosen>,
    pub tally: Option<Value>,
    /// (round, seat) -> (text, sha256), the latest of each.
    pub answers: Answers,
    pub shas: BTreeMap<(String, String), String>,
}

pub fn load(store: &Store, project: &str, id: &str) -> Result<Stored> {
    let (row, answers) = store.panel_get(project, id)?;
    let config = row["config"].as_str().unwrap_or_default();
    let cfg: Panel = serde_json::from_str(config)?;
    let digest = row["digest"].as_str().unwrap_or_default().to_owned();
    ensure!(
        cfg.digest() == digest,
        "{id}: the config changed after it was locked (TAMPER); open a new panel"
    );
    let json_of = |k: &str| row[k].as_str().map(serde_json::from_str::<Value>).transpose();
    let mut a = Answers::default();
    let mut shas = BTreeMap::new();
    for r in answers {
        let (Some(round), Some(seat), Some(text)) = (r["round"].as_str(), r["seat"].as_str(), r["text"].as_str())
        else {
            continue;
        };
        let slot = match round {
            "round1" => &mut a.round1,
            "round2" => &mut a.round2,
            _ => &mut a.counterfactual,
        };
        slot.insert(seat.to_owned(), text.to_owned());
        shas.insert(
            (round.to_owned(), seat.to_owned()),
            r["sha256"].as_str().unwrap_or_default().to_owned(),
        );
    }
    Ok(Stored {
        id: id.to_owned(),
        status: row["status"].as_str().unwrap_or("round1").to_owned(),
        anon: json_of("anon")?.map(serde_json::from_value).transpose()?,
        chosen: json_of("counterfactual")?.map(serde_json::from_value).transpose()?,
        tally: json_of("tally")?,
        cfg,
        digest,
        answers: a,
        shas,
    })
}

/// Save a seat's answer verbatim. Round 1 closes when it is anonymized; round 2 opens then; nothing is
/// accepted after the tally.
pub fn submit(store: &Store, project: &str, id: &str, round: &str, seat: &str, text: &str, by: &str) -> Result<String> {
    let round = round_name(round)?;
    ensure!(
        !text.trim().is_empty(),
        "the answer is empty; submit the seat's full answer"
    );
    let p = load(store, project, id)?;
    ensure!(
        p.cfg.ids().any(|s| s == seat),
        "seat {seat:?} is not on panel {}",
        p.cfg.panel_id
    );
    ensure!(p.tally.is_none(), "{id} is tallied ({}); open a new panel", p.status);
    match (round, p.anon.is_some()) {
        ("round1", true) => {
            bail!("round 1 is closed: it was anonymized, so its answers are fixed. Open a new panel to redo round 1.")
        }
        ("round2" | "counterfactual", false) => bail!("round 2 opens only after round 1 is anonymized (call panel)"),
        _ => {}
    }
    let sha = sha256_hex(&[text.as_bytes()]);
    let replaced = p.shas.contains_key(&(round.to_owned(), seat.to_owned()));
    store.panel_answer(project, id, round, seat, text, &sha, by)?;
    Ok(format!(
        "{id}: {round} answer for {seat} saved ({} bytes, sha256 {}){}",
        text.len(),
        &sha[..12],
        if replaced {
            ", replacing the earlier one (kept on record)"
        } else {
            ""
        }
    ))
}

/// Where a panel stands, and the next step taken when it is due: round 1 is anonymized once complete, and
/// the panel is tallied once nothing is owed. Returns (text, done).
pub fn status(store: &Store, project: &str, id: &str) -> Result<(String, bool)> {
    let mut p = load(store, project, id)?;
    let cfg = &p.cfg;
    let mut out = format!(
        "{id} | {} | {} | target {}\nQuestion: {}\n",
        cfg.panel_id, p.status, cfg.target, cfg.question
    );
    if let Some(t) = &p.tally {
        writeln!(out, "Tallied: {}", serde_json::to_string_pretty(t)?)?;
        return Ok((out, true));
    }
    let anon = match &p.anon {
        Some(anon) => anon.clone(),
        None => {
            let owed = round1_owed(cfg, &p.answers.round1);
            if !owed.is_empty() {
                writeln!(out, "Round 1: {} answer(s) owed.", owed.len())?;
                for o in &owed {
                    writeln!(out, "OWED {} {}\n    {}", o.condition, o.seat, owed_prompt(o, cfg))?;
                }
                out.push_str(&brief(cfg));
                return Ok((out, false));
            }
            let anon = anonymize(cfg, &p.digest, &p.answers.round1)?;
            store.panel_set(
                project,
                id,
                "anon",
                &serde_json::to_string(&anon)?,
                Some("round2"),
                true,
            )?;
            writeln!(
                out,
                "Round 1 complete and anonymized. Give each seat this bundle (its own answer is among them) and \
                 run round 2: engage the strongest disagreeing Member by label, add NEW: lines, keep or change \
                 the stance (a change needs a FLAW: line quoting round 1), end with the STANCE line.\n"
            )?;
            out.push_str(&anon.bundle);
            return Ok((out, false));
        }
    };
    for sid in cfg.ids() {
        let now = p.answers.round1.get(sid).map(|t| sha256_hex(&[t.as_bytes()]));
        ensure!(
            now.as_ref() == anon.round1_sha256.get(sid),
            "round 1 of {sid} changed after anonymization (TAMPER)"
        );
    }
    let (owed, chosen) = evaluate(cfg, &p.answers, p.chosen.as_ref());
    if chosen.is_some() && chosen != p.chosen {
        store.panel_set(
            project,
            id,
            "counterfactual",
            &serde_json::to_string(&chosen)?,
            None,
            false,
        )?;
        p.chosen = chosen;
    }
    if !owed.is_empty() {
        let rows: Vec<Value> = owed
            .iter()
            .map(|o| {
                let round = if o.condition == "agreement_check" { "counterfactual" } else { "round2" };
                let sha = p.shas.get(&(round.to_owned(), o.seat.clone())).map_or("none", String::as_str);
                json!({"key": sha256_hex(&[id.as_bytes(), o.condition.as_bytes(), o.seat.as_bytes(), sha.as_bytes()])[..32],
                       "condition": o.condition, "seat": o.seat, "answer_sha256": sha, "detail": o.detail})
            })
            .collect();
        store.panel_owed(project, id, &rows)?;
        writeln!(
            out,
            "Round 2: {} re-prompt(s) owed. Send each to the seat it names.",
            owed.len()
        )?;
        for o in &owed {
            writeln!(out, "OWED {} {}\n    {}", o.condition, o.seat, owed_prompt(o, cfg))?;
        }
        return Ok((out, false));
    }
    let t = tally(cfg, &p.answers);
    let mut t_json = serde_json::to_value(&t)?;
    t_json["panel_id"] = json!(cfg.panel_id);
    t_json["target"] = json!(cfg.target);
    t_json["panel_sha256"] = json!(p.digest);
    t_json["enforcement"] = json!(store.panel_enforcement(project, id)?);
    store.panel_tally(
        project,
        id,
        &serde_json::to_string(&t_json)?,
        t.result,
        t.verdict.as_deref(),
    )?;
    match &t.verdict {
        Some(v) => writeln!(out, "CONSENSUS: {v} (bar {} of {})", t.bar, t.w_total)?,
        None => writeln!(
            out,
            "SPLIT: no option reached the consensus bar ({} of {}). Returned to the owner; do not write a consensus.",
            t.bar, t.w_total
        )?,
    }
    writeln!(out, "weights: {}", json!(t.weights))?;
    for m in &t.minority_report {
        writeln!(out, "minority (dealbreaker): {m}")?;
    }
    Ok((out, true))
}

#[cfg(test)]
mod tests {
    use super::*;

    const SEATS: [(&str, &str, &str); 3] = [
        ("seat_alpha", "lens_deference", "record"),
        ("seat_beta", "lens_test", "preservation"),
        ("seat_gamma", "lens_remedy", "remedy"),
    ];

    fn cfg_json() -> Value {
        json!({"panel_id": "synthetic_panel", "target": "conclusion:point_x", "question": "Does point X survive?",
               "options": ["affirm", "reverse", "remit"], "objection_categories": ["record", "preservation", "remedy"],
               "weighted_seat": "seat_alpha",
               "seats": SEATS.iter().map(|(id, lens, _)| json!({"id": id, "lens": lens})).collect::<Vec<_>>()})
    }

    fn cfg() -> Panel {
        validate(&cfg_json(), None).expect("valid")
    }

    fn cat(sid: &str) -> &'static str {
        SEATS.iter().find(|s| s.0 == sid).map(|s| s.2).unwrap()
    }

    struct A<'a> {
        option: &'a str,
        conf: &'a str,
        deal: &'a str,
        objections: Vec<(String, String)>,
        new: Vec<String>,
        flaw: Option<(String, String)>,
        body: String,
    }

    fn a(option: &str) -> A<'_> {
        A {
            option,
            conf: "high",
            deal: "no",
            objections: vec![],
            new: vec![],
            flaw: None,
            body: "Analysis of the target.".into(),
        }
    }

    impl A<'_> {
        fn text(&self) -> String {
            let mut lines = vec![self.body.clone(), String::new()];
            lines.extend(self.objections.iter().map(|(c, t)| format!("OBJECTION: {c} | {t}")));
            lines.extend(self.new.iter().map(|n| format!("NEW: {n}")));
            if let Some((q, w)) = &self.flaw {
                lines.push(format!("FLAW: \"{q}\" | {w}"));
            }
            lines.push(format!(
                "STANCE: {} | CONFIDENCE: {} | DEALBREAKER: {}",
                self.option, self.conf, self.deal
            ));
            lines.join("\n") + "\n"
        }
    }

    fn r1_body(sid: &str) -> String {
        format!("Round one reasoning for {sid}: the finding rests on testimony about the commute and nothing else.")
    }

    /// Round 1 (alpha, beta affirm; gamma reverses) and a good round 2 for every seat.
    fn answers() -> Answers {
        let r1 = [
            ("seat_alpha", "affirm"),
            ("seat_beta", "affirm"),
            ("seat_gamma", "reverse"),
        ];
        let mut x = Answers::default();
        for (sid, option) in r1 {
            x.round1.insert(
                sid.into(),
                A {
                    body: r1_body(sid),
                    ..a(option)
                }
                .text(),
            );
            x.round2.insert(sid.into(), good_r2(sid, option).text());
        }
        x
    }

    fn good_r2<'a>(sid: &str, option: &'a str) -> A<'a> {
        A {
            objections: vec![(cat(sid).into(), "A distinct objection raised by this seat only.".into())],
            new: vec![format!(
                "A new risk about the remedy that round one never raised for {sid}"
            )],
            ..a(option)
        }
    }

    fn conditions(x: &Answers) -> Vec<(&'static str, String)> {
        evaluate(&cfg(), x, None)
            .0
            .into_iter()
            .map(|o| (o.condition, o.seat))
            .collect()
    }

    #[test]
    fn invalid_panels_are_refused() {
        let bad = |edit: &dyn Fn(&mut Value)| {
            let mut v = cfg_json();
            edit(&mut v);
            validate(&v, None).unwrap_err().to_string()
        };
        assert!(bad(&|v| v["seats"] = json!([{"id": "a"}, {"id": "b"}])).contains("at least 3 seats"));
        assert!(bad(&|v| v["options"] = json!(["affirm"])).contains("at least 2 distinct"));
        assert!(bad(&|v| v["options"] = json!(["affirm", "abstain"])).contains("reserved"));
        assert!(bad(&|v| v["weighted_seat"] = json!("nobody")).contains("is not a seat"));
        assert!(bad(&|v| v["thresholds"] = json!({"consensus": 0.5})).contains("consensus"));
        assert!(bad(&|v| v["thresholds"] = json!({"agreement": 1.0})).contains("agreement"));
        assert!(bad(&|v| v["seats"][1]["lens"] = json!("seat_alpha")).contains("shared by"));
        assert!(bad(&|v| v["confidence_weights"] = json!({"high": 0.4})).contains("confidence_weights"));
        assert!(
            bad(&|v| {
                v.as_object_mut().unwrap().remove("objection_categories");
            })
            .contains("objection_categories")
        );
        assert!(bad(&|v| v["objection_categories"] = json!(["record"])).contains("needs 2 distinct"));
    }

    #[test]
    fn owner_settings_are_copied_in_and_a_panel_that_disagrees_is_refused() {
        let settings = json!({"thresholds": {"consensus": 0.8}});
        assert_eq!(validate(&cfg_json(), Some(&settings)).unwrap().consensus, 0.8);
        let mut v = cfg_json();
        v["thresholds"] = json!({"consensus": 0.6});
        assert!(
            validate(&v, Some(&settings))
                .unwrap_err()
                .to_string()
                .contains("owner's settings")
        );
    }

    #[test]
    fn the_digest_covers_the_whole_config() {
        let mut other = cfg();
        other.consensus = 0.9;
        assert_ne!(cfg().digest(), other.digest());
        assert_eq!(cfg().digest(), cfg().digest());
    }

    #[test]
    fn round1_needs_a_valid_stance_never_inferred() {
        let c = cfg();
        let mut r1 = answers().round1;
        r1.insert("seat_beta".into(), "I lean towards affirm.\n".into());
        assert_eq!(round1_owed(&c, &r1)[0].condition, "missing_stance");
        let two = format!("{}{}", a("affirm").text(), a("reverse").text());
        r1.insert("seat_beta".into(), two);
        assert_eq!(round1_owed(&c, &r1)[0].condition, "missing_stance", "two STANCE lines");
        r1.insert("seat_beta".into(), a("affirm").text());
        assert!(round1_owed(&c, &r1).is_empty());
        r1.remove("seat_gamma");
        assert_eq!(round1_owed(&c, &r1)[0].condition, "missing_answer");
    }

    #[test]
    fn anonymizing_strips_identities_in_an_order_fixed_by_the_lock() {
        let c = cfg();
        let mut r1 = answers().round1;
        r1.insert(
            "seat_alpha".into(),
            A {
                body: "As seat_alpha (lens deference, Seat-Alpha) I disagree with lens_test.".into(),
                ..a("affirm")
            }
            .text(),
        );
        let x = anonymize(&c, "d1", &r1).unwrap();
        for leak in ["seat_alpha", "lens deference", "Seat-Alpha", "lens_test", "seat_beta"] {
            assert!(!x.bundle.contains(leak), "{leak} leaked:\n{}", x.bundle);
        }
        assert_eq!(x, anonymize(&c, "d1", &r1).unwrap(), "same lock, same order");
        let orders: BTreeSet<Vec<String>> = (0..12)
            .map(|i| anonymize(&c, &format!("d{i}"), &r1).unwrap().order)
            .collect();
        assert!(orders.len() > 1, "the order comes from the digest, not the panel order");
        assert_eq!(
            replace_terms("xseat_alpha seat_alphas", &[("seat_alpha".into(), "M")]),
            "xseat_alpha seat_alphas"
        );
    }

    #[test]
    fn a_good_round2_owes_nothing_until_unanimity() {
        assert!(conditions(&answers()).is_empty(), "{:?}", conditions(&answers()));
    }

    #[test]
    fn stance_line_is_required() {
        let mut x = answers();
        x.round2.insert(
            "seat_beta".into(),
            "No stance here.\nNEW: a long enough brand new claim here\n".into(),
        );
        assert!(conditions(&x).contains(&("missing_stance", "seat_beta".into())));
        x.round2.remove("seat_beta");
        assert!(conditions(&x).contains(&("missing_answer", "seat_beta".into())));
    }

    #[test]
    fn novelty_gate_both_ways() {
        let mut x = answers();
        let repeat = A {
            new: vec![r1_body("seat_beta")],
            ..good_r2("seat_beta", "affirm")
        };
        x.round2.insert("seat_beta".into(), repeat.text());
        assert!(conditions(&x).contains(&("novelty_gate", "seat_beta".into())));
        let short = A {
            new: vec!["too short".into()],
            ..good_r2("seat_beta", "affirm")
        };
        x.round2.insert("seat_beta".into(), short.text());
        assert!(conditions(&x).contains(&("novelty_gate", "seat_beta".into())));
        x.round2
            .insert("seat_beta".into(), good_r2("seat_beta", "affirm").text());
        assert!(conditions(&x).is_empty());
    }

    #[test]
    fn a_changed_stance_needs_a_verbatim_flaw() {
        let mut x = answers();
        x.round2
            .insert("seat_gamma".into(), good_r2("seat_gamma", "affirm").text());
        assert!(conditions(&x).contains(&("flaw_required", "seat_gamma".into())));
        let made_up = A {
            flaw: Some(("words I never wrote at all".into(), "it was wrong".into())),
            ..good_r2("seat_gamma", "affirm")
        };
        x.round2.insert("seat_gamma".into(), made_up.text());
        assert!(conditions(&x).contains(&("flaw_required", "seat_gamma".into())));
        let named = A {
            flaw: Some((
                "rests on   testimony about the commute".into(),
                "testimony was corroborated".into(),
            )),
            ..good_r2("seat_gamma", "affirm")
        };
        x.round2.insert("seat_gamma".into(), named.text());
        assert!(
            !conditions(&x).iter().any(|c| c.0 == "flaw_required"),
            "{:?}",
            conditions(&x)
        );
    }

    #[test]
    fn objection_categories_come_from_the_panel() {
        let mut x = answers();
        let vibes = A {
            objections: vec![("vibes".into(), "x y z".into())],
            ..good_r2("seat_beta", "affirm")
        };
        x.round2.insert("seat_beta".into(), vibes.text());
        let owed = evaluate(&cfg(), &x, None).0;
        assert!(
            owed.iter()
                .any(|o| o.condition == "bad_objection" && o.detail == "vibes")
        );
    }

    #[test]
    fn dissent_is_counted_by_category_never_by_wording() {
        let mut x = answers();
        for sid in ["seat_alpha", "seat_beta", "seat_gamma"] {
            let opt = if sid == "seat_gamma" { "reverse" } else { "affirm" };
            let same = A {
                objections: vec![("record".into(), format!("the same point {sid}"))],
                ..good_r2(sid, opt)
            };
            x.round2.insert(sid.into(), same.text());
        }
        let owed = conditions(&x);
        assert_eq!(owed.iter().filter(|c| c.0 == "dissent_quota").count(), 1, "{owed:?}");
        x.round2
            .insert("seat_beta".into(), good_r2("seat_beta", "affirm").text());
        assert!(conditions(&x).is_empty());
    }

    #[test]
    fn unanimity_owes_counterfactuals_from_the_least_confident_and_they_must_add_something() {
        let c = cfg();
        let mut x = answers();
        x.round1.insert(
            "seat_gamma".into(),
            A {
                body: r1_body("seat_gamma"),
                ..a("affirm")
            }
            .text(),
        );
        x.round2.insert(
            "seat_gamma".into(),
            A {
                conf: "low",
                ..good_r2("seat_gamma", "affirm")
            }
            .text(),
        );
        x.round2.insert(
            "seat_beta".into(),
            A {
                conf: "med",
                ..good_r2("seat_beta", "affirm")
            }
            .text(),
        );
        let (owed, chosen) = evaluate(&c, &x, None);
        let chosen = chosen.unwrap();
        assert_eq!(chosen.seats, ["seat_gamma", "seat_beta"]);
        assert_eq!(owed.iter().filter(|o| o.condition == "agreement_check").count(), 2);
        // A token line, an objection outside the categories, or the seat's own round-2 objection does not do it.
        for weak in [
            "OBJECTION: remedy | x\n",
            "OBJECTION: vibes | a long and entirely new objection here\n",
            "OBJECTION: remedy | A distinct objection raised by this seat only.\n",
        ] {
            x.counterfactual.insert("seat_gamma".into(), weak.into());
            assert!(
                evaluate(&c, &x, Some(&chosen)).0.iter().any(|o| o.seat == "seat_gamma"),
                "{weak}"
            );
        }
        for sid in ["seat_gamma", "seat_beta"] {
            x.counterfactual.insert(
                sid.into(),
                "OBJECTION: record | the record never tested the commute claim\n".into(),
            );
        }
        assert!(evaluate(&c, &x, Some(&chosen)).0.is_empty());
    }

    #[test]
    fn weighted_consensus_and_a_hesitant_majority_split() {
        let c = cfg();
        let x = answers();
        let t = tally(&c, &x);
        // alpha 1.5 + beta 1.0 = 2.5 of 3.5; bar 2.3333
        assert_eq!(
            (t.result, t.verdict.as_deref(), t.bar),
            ("consensus", Some("affirm"), 2.3333)
        );
        let mut x = answers();
        x.round2.insert(
            "seat_beta".into(),
            A {
                conf: "low",
                ..good_r2("seat_beta", "affirm")
            }
            .text(),
        );
        let t = tally(&c, &x);
        assert_eq!((t.result, t.verdict), ("split", None));
    }

    #[test]
    fn a_dealbreaker_against_the_verdict_is_kept_as_a_minority_report() {
        let mut x = answers();
        x.round2.insert(
            "seat_gamma".into(),
            A {
                deal: "yes",
                ..good_r2("seat_gamma", "reverse")
            }
            .text(),
        );
        let t = tally(&cfg(), &x);
        assert_eq!(
            t.minority_report,
            [json!({"seat": "seat_gamma", "lens": "lens_remedy", "stance": "reverse"})]
        );
    }

    #[test]
    fn two_high_seats_of_three_equal_seats_reach_the_exact_two_thirds_bar() {
        let mut v = cfg_json();
        v["weighted_seat"] = Value::Null;
        let c = validate(&v, None).unwrap();
        assert_eq!(tally(&c, &answers()).verdict.as_deref(), Some("affirm"));
        v["thresholds"] = json!({"consensus": 0.6667});
        let c = validate(&v, None).unwrap();
        assert_eq!(
            tally(&c, &answers()).verdict,
            None,
            "a rounded-up bar turns the same votes into a split"
        );
    }
}
