//! Bridges (idea-bridges design 2026-10-30, slice B2): a hypothesis that a capability card of another project
//! answers a problem card of this one. The bridge lives in the target project and holds the source only as
//! project, card key and card hash. Its gate is in the schema (`0020_project_bridge.surql`): candidate ->
//! testable (an assumption check per source assumption, each quoting the target; the novelty search this
//! program ran; a written test) -> supported (an ok receipt) or refuted (a receipt, or a quoted mismatch).
//!
//! Reach (gw-security rulings 1, 3 and 5, 2026-10-30; A3.20): a bridge's source is a project at level `shared`,
//! not walled from the target nor from a source admitted before it (registry order), never the target itself; a private project is never a source (editing `related`
//! does not change that), an isolated one is never read. Reach is checked when a bridge is written and again
//! every time one is read, so a wall or level set later stops an existing bridge from rendering. Every read
//! takes its reach from [`reach`] alone.

use std::collections::BTreeSet;
use std::fmt::Write as _;

use anyhow::{Context, Result, bail, ensure};
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::{Value, json};

use crate::card::{self, Card, CardQuote};
use crate::home::{Home, machine};
use crate::registry::Registry;
use crate::store::{self, Store, Vars, sha256_hex};

/// The projects a bridge into `project` may take its source from, and the rest with why.
pub struct Reach {
    pub target: String,
    pub sources: Vec<String>,
    pub skipped: Vec<String>,
}

impl Reach {
    /// Why `source` is out of reach, if it is.
    pub fn refuses(&self, source: &str) -> Option<String> {
        if self.sources.iter().any(|s| s == source) {
            return None;
        }
        Some(
            self.skipped
                .iter()
                .find(|s| s.split(' ').next() == Some(source))
                .cloned()
                .unwrap_or_else(|| format!("{source} (not a registered project, or the target itself)")),
        )
    }
}

/// The one reach function every card, bridge and evaluator read goes through (gw-security rulings 1, 3 and 5;
/// A3.20). Walls and levels come from the store's own readers ([`Store::walls`], [`Store::level`]), the
/// owner-signed rows in `global`; [`admit`] decides.
pub fn reach(home: &Home, store: &Store, project: &str) -> Result<Reach> {
    let registry = Registry::load(home)?;
    let target = registry.project(project)?.name.clone();
    let target_ns = gw_store::project_ns(&store::slug(&target)?)?;
    let walls = match store.walls() {
        Ok(walls) => walls,
        Err(e) => {
            let skipped = registry
                .projects
                .keys()
                .filter(|n| **n != target)
                .map(|name| format!("{name} (walls unreadable, refused: {e:#})"))
                .collect();
            return Ok(Reach {
                target,
                sources: Vec::new(),
                skipped,
            });
        }
    };
    let mut candidates = Vec::new();
    for name in registry.projects.keys().filter(|n| **n != target) {
        let ns = gw_store::project_ns(&store::slug(name)?)?;
        let level = store.level(&ns).map_err(|e| format!("{e:#}"));
        candidates.push((name.clone(), ns, level));
    }
    Ok(admit(&target, &target_ns, &walls, candidates))
}

/// Who a bridge into `target` may take its source from, in registry order (A3.20). Eligibility first: a source
/// walled from the target, or whose level cannot be read, or that is not `shared` (isolated and private never
/// are, ruling 3), is refused. Then each eligible source is admitted unless a wall joins it with a source
/// already admitted ([`store::walled_from`], the rule [`Store::reach`] uses), so one read never holds both sides
/// of a wall: the first of a walled pair is admitted, the second is skipped "walled from <first>".
pub fn admit(
    target: &str,
    target_ns: &str,
    walls: &[(String, String)],
    candidates: Vec<(String, String, std::result::Result<String, String>)>,
) -> Reach {
    let mut out = Reach {
        target: target.to_owned(),
        sources: Vec::new(),
        skipped: Vec::new(),
    };
    let mut admitted = vec![(target.to_owned(), target_ns.to_owned())];
    for (name, ns, level) in candidates {
        let level = match level {
            Ok(level) => level,
            Err(e) => {
                out.skipped.push(format!("{name} (level unreadable, refused: {e})"));
                continue;
            }
        };
        let wall = store::walled_from(walls, &ns, &admitted).map(str::to_owned);
        match wall {
            Some(w) if w == target => out.skipped.push(format!("{name} (walled from {target})")),
            _ if level != "shared" => out.skipped.push(format!("{name} ({level}: never a bridge source)")),
            Some(w) => out.skipped.push(format!("{name} (walled from {w})")),
            None => {
                out.sources.push(name.clone());
                admitted.push((name, ns));
            }
        }
    }
    out
}

/// A card of `source` read for a bridge: live, servable, and the kind asked for.
fn source_card(store: &Store, source: &str, id: &str, kind: &str) -> Result<Card> {
    let c = card::card(store, source, id)?;
    ensure!(
        c.kind == kind,
        "card:{} in {source} is a {}, not a {kind}",
        c.key,
        c.kind
    );
    ensure!(
        c.status == "live",
        "card:{} in {source} is {}, not live",
        c.key,
        c.status
    );
    if let Some(why) = card::unservable(&c) {
        bail!("card:{} in {source} cannot be served: {why}", c.key);
    }
    Ok(c)
}

/// Record a candidate bridge in `project` from `target` (a problem card of it) to `source_card`, a capability
/// of `source`. Returns `bridge:<key>` and a summary.
#[allow(clippy::too_many_arguments)]
pub fn add(
    home: &Home,
    store: &Store,
    project: &str,
    target: &str,
    source: &str,
    source_id: &str,
    via: Option<&str>,
    found_by: &str,
    by: &str,
) -> Result<String> {
    let r = reach(home, store, project)?;
    let source = Registry::load(home)?.project(source)?.name.clone();
    if let Some(why) = r.refuses(&source) {
        bail!("refused: {source} is out of this project's bridge reach: {why}");
    }
    let t = card::card(store, &r.target, target)?;
    ensure!(
        t.kind == "problem",
        "the target card:{} is a {}, not a problem",
        t.key,
        t.kind
    );
    ensure!(
        t.status == "live",
        "the target card:{} is {}, not live",
        t.key,
        t.status
    );
    let s = source_card(store, &source, source_id, "capability")?;
    let via = via.map(|v| source_card(store, &source, v, "problem")).transpose()?;
    crate::refusal::check_text("found_by", found_by)?;
    crate::refusal::check_text("by", by)?;
    let mut sql = String::from(
        "CREATE bridge SET target = type::record('card', $target), source_project = $source, source_card = $card, \
         source_hash = $hash, source_a = $n, source_recollection = $recollection, found_by = $found_by, by = $by, machine = $machine",
    );
    let mut vars = Vars::default()
        .set("target", t.key.as_str())
        .set("source", source.as_str())
        .set("card", s.key.as_str())
        .set("hash", s.hash.as_str())
        .set("n", s.a.len())
        .set("recollection", card::is_recollection(&s))
        .set("found_by", found_by)
        .set("by", by)
        .set("machine", machine());
    if let Some(v) = &via {
        sql.push_str(", via_card = $via, via_hash = $via_hash");
        vars = vars.set("via", v.key.as_str()).set("via_hash", v.hash.as_str());
    }
    sql.push_str(" RETURN VALUE id;");
    let db = store.agent(&gw_store::project_ns(&store::slug(&r.target)?)?)?;
    let rows = store.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?))?;
    let id = store::record_id(&rows[0]).context("CREATE bridge returned no id")?;
    Ok(format!(
        "{id} recorded in {}: candidate, INFERRED; card:{} -> {source} card:{} (hash {}); {} assumption(s) to check \
         before it is testable",
        r.target,
        t.key,
        s.key,
        &s.hash[..12],
        s.a.len()
    ))
}

/// One assumption check: the source assumption by its number (from 0), the verdict, and a quote of the target.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct CheckIn {
    /// Which source assumption (0 for the first).
    pub assumption: usize,
    /// holds, fails, or unknown.
    pub verdict: String,
    /// Where the target project says so: {unit, lines} or {fact, text}, as for a card.
    pub quote: CardQuote,
}

/// One lens's verdict on a bridge (METHOD section 13: statistician, target-field-expert, vocabulary-translator,
/// skeptic, each with its own). The skeptic's `proceed` is required before testable; any `reject` blocks it.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct LensVerdictIn {
    pub lens: String,
    /// proceed or reject.
    pub verdict: String,
    /// Why, in a sentence or two.
    pub why: String,
}

/// A bridge's move.
#[derive(Debug, Clone, Default, Deserialize, JsonSchema)]
pub struct AdvanceIn {
    /// candidate (record lens verdicts or register the test without moving), testable, supported or refuted.
    pub to: String,
    /// testable: one check per source assumption; refuted: a check whose verdict is fails.
    #[serde(default)]
    pub checks: Vec<CheckIn>,
    /// testable: what to search for to learn whether the link was already made; the program runs it.
    #[serde(default)]
    pub novelty_query: Option<String>,
    /// testable: your verdict on the search, not_made or made. The program refuses not_made when a hit in this
    /// project already quotes the source card or names it.
    #[serde(default)]
    pub novelty_verdict: Option<String>,
    /// Lens verdicts to add (the skeptic's is required before testable).
    #[serde(default)]
    pub lens_verdicts: Vec<LensVerdictIn>,
    /// The test, registered before it runs (once; never changed): what it shows, in words.
    #[serde(default)]
    pub test: Option<String>,
    /// The exact command `run` or `gate` will run for it.
    #[serde(default)]
    pub test_command: Option<String>,
    /// The expected result (e.g. "exit 0: the display holds through the burst trace").
    #[serde(default)]
    pub test_expect: Option<String>,
    /// Optional: a control command expected to FAIL, showing the test can fail.
    #[serde(default)]
    pub control_command: Option<String>,
    /// supported: the green receipt of the registered command; refuted: its red receipt (`receipt:...`).
    #[serde(default)]
    pub receipt: Option<String>,
    /// supported: a red receipt after registration: the registered command before the green run, or the control.
    #[serde(default)]
    pub red_receipt: Option<String>,
}

/// A bridge row as stored.
struct Row {
    key: String,
    target: String,
    source_project: String,
    source_card: String,
    source_hash: String,
    status: String,
    grade: String,
    found_by: String,
    raw: Value,
}

fn rows(store: &Store, project: &str, filter: &str, vars: Vars) -> Result<Vec<Row>> {
    let db = store.agent(&gw_store::project_ns(&store::slug(project)?)?)?;
    let sql = format!(
        "SELECT record::id(id) AS key, record::id(target) AS target, source_project, source_card, source_hash, \
         status, grade, found_by, assumption_check, novelty_check, lens_verdicts, test, test_command, test_expect, \
         control_command, <string> test_at AS test_at, <string> receipt AS receipt, \
         <string> red_receipt AS red_receipt, via_card, <string> at AS at FROM bridge {filter} ORDER BY at;"
    );
    let out = store.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?))?;
    Ok(out[0]
        .as_array()
        .into_iter()
        .flatten()
        .map(|r| {
            let s = |k: &str| r[k].as_str().unwrap_or_default().to_owned();
            Row {
                key: s("key"),
                target: s("target"),
                source_project: s("source_project"),
                source_card: s("source_card"),
                source_hash: s("source_hash"),
                status: s("status"),
                grade: s("grade"),
                found_by: s("found_by"),
                raw: r.clone(),
            }
        })
        .collect())
}

/// Move a bridge of `project` along its gate. The schema refuses a move whose evidence is missing; this checks
/// first where it can, so the refusal is plain, and runs the novelty search itself. The test is registered in a
/// write of its own before any move (the store stamps `test_at`), so a receipt can only count if it ran after.
/// With `owner_pass` the writes are the owner's: the second, independent credential `supported` needs (the
/// agent login that proposed the bridge cannot promote it).
pub fn advance(
    home: &Home,
    store: &Store,
    project: &str,
    id: &str,
    mv: &AdvanceIn,
    by: &str,
    owner_pass: Option<&str>,
) -> Result<String> {
    let key = card::key_of(id, "bridge")?;
    let r = reach(home, store, project)?;
    let row = rows(
        store,
        &r.target,
        "WHERE id = type::record('bridge', $key)",
        Vars::default().set("key", key.as_str()),
    )?
    .into_iter()
    .next()
    .with_context(|| format!("no bridge {id} in {}", r.target))?;
    if let Some(why) = r.refuses(&row.source_project) {
        bail!(
            "refused: the source {} is out of this project's bridge reach now: {why}",
            row.source_project
        );
    }
    let to = mv.to.trim();
    ensure!(
        ["candidate", "testable", "supported", "refuted"].contains(&to),
        "a bridge moves to testable, supported or refuted (or stays a candidate), not {to:?}"
    );
    crate::refusal::check_text("by", by)?;
    // The source must still say what it said when bridged, for any move that rests on it.
    let source = card::card(store, &row.source_project, &row.source_card)?;
    if to != "refuted" {
        ensure!(
            source.status == "live" && source.hash == row.source_hash,
            "the source card:{} in {} changed or went stale since it was bridged; record a new bridge",
            row.source_card,
            row.source_project
        );
    }
    let ns = gw_store::project_ns(&store::slug(&r.target)?)?;
    let db = match owner_pass {
        Some(pass) => std::sync::Arc::new(store.owner(&ns, pass)?),
        None => store.agent(&ns)?,
    };
    let refused = |e: anyhow::Error| -> anyhow::Error {
        anyhow::anyhow!(
            "refused: bridge:{key} cannot move from {} to {to}: {}. Testable needs a registered test (test, \
             test_command, test_expect), one check per source assumption (quoting this project), a novelty verdict \
             not_made, and a skeptic's proceed with no lens reject; supported needs all of that, a green receipt of \
             the registered command and a red one (the same command earlier, or the control), both run after \
             registration, written by a credential other than the proposer's; refuted needs a failing run of the \
             registered command or a check whose verdict is fails.",
            row.status,
            first_line(&format!("{e:#}"))
        )
    };

    // 1. Registration, its own write.
    let registering = [&mv.test, &mv.test_command, &mv.test_expect, &mv.control_command]
        .iter()
        .any(|f| f.as_deref().is_some_and(|t| !t.trim().is_empty()));
    if registering {
        let field = |name: &'static str, v: &Option<String>| -> Result<Option<String>> {
            let Some(t) = v.as_deref().map(str::trim).filter(|t| !t.is_empty()) else {
                return Ok(None);
            };
            crate::refusal::check_text(name, t)?;
            ensure!(t.len() <= 2000, "{name} is at most 2000 characters");
            Ok(Some(t.to_owned()))
        };
        let (test, command, expect, control) = (
            field("test", &mv.test)?,
            field("test_command", &mv.test_command)?,
            field("test_expect", &mv.test_expect)?,
            field("control_command", &mv.control_command)?,
        );
        ensure!(
            test.is_some() && command.is_some() && expect.is_some(),
            "a test is registered whole: test (what it shows), test_command and test_expect"
        );
        let mut sql = String::from(
            "UPDATE type::record('bridge', $key) SET test = $test, test_command = $command, test_expect = $expect",
        );
        let mut vars = Vars::default()
            .set("key", key.as_str())
            .set("test", test)
            .set("command", command)
            .set("expect", expect);
        if let Some(c) = control {
            sql.push_str(", control_command = $control");
            vars = vars.set("control", c);
        }
        sql.push_str(" RETURN VALUE id;");
        store.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?)).map_err(|e| {
            anyhow::anyhow!(
                "refused: the test is registered once and never changed: {}",
                first_line(&format!("{e:#}"))
            )
        })?;
    }

    // 2. The move.
    let mut sets = vec!["status = $to".to_owned()];
    let mut vars = Vars::default().set("key", key.as_str()).set("to", to);
    if !mv.checks.is_empty() {
        let mut checks = Vec::new();
        let mut seen = std::collections::BTreeSet::new();
        for c in &mv.checks {
            let assumption = source.a.get(c.assumption).with_context(|| {
                format!(
                    "the source states {} assumption(s); there is no {}",
                    source.a.len(),
                    c.assumption
                )
            })?;
            ensure!(
                seen.insert(c.assumption),
                "assumption {} is checked twice",
                c.assumption
            );
            let verdict = c.verdict.trim();
            ensure!(
                ["holds", "fails", "unknown"].contains(&verdict),
                "a verdict is holds, fails or unknown, not {verdict:?}"
            );
            let q = card::resolve(store, &r.target, &c.quote)?;
            crate::refusal::check_text("quote", &q.quote)?;
            checks.push(json!({
                "assumption": assumption,
                "verdict": verdict,
                "unit": q.unit_key.clone().or(q.fact_key.clone()).unwrap_or_default(),
                "cite": q.cite,
                "quote": q.quote,
                "quote_hash": sha256_hex(&[q.quote.as_bytes()]),
            }));
        }
        sets.push("assumption_check = $checks".to_owned());
        vars = vars.set("checks", checks);
    }
    if let Some(query) = mv.novelty_query.as_deref().map(str::trim).filter(|q| !q.is_empty()) {
        crate::refusal::check_text("novelty_query", query)?;
        ensure!(query.len() <= 300, "a novelty query is at most 300 characters");
        let verdict = mv.novelty_verdict.as_deref().map(str::trim).unwrap_or_default();
        ensure!(
            ["not_made", "made"].contains(&verdict),
            "a novelty check needs a verdict, not_made or made, on what the search found"
        );
        let check = novelty(store, &r, &source, query, verdict)?;
        sets.push("novelty_check = $novelty".to_owned());
        vars = vars.set("novelty", check);
    }
    if !mv.lens_verdicts.is_empty() {
        let mut verdicts = Vec::new();
        for v in &mv.lens_verdicts {
            crate::refusal::check_text("why", &v.why)?;
            ensure!(v.why.len() <= 2000, "a lens verdict's why is at most 2000 characters");
            verdicts.push(json!({"lens": v.lens.trim(), "verdict": v.verdict.trim(), "why": v.why.trim()}));
        }
        sets.push("lens_verdicts = array::concat(lens_verdicts, $verdicts)".to_owned());
        vars = vars.set("verdicts", verdicts);
    }
    if let Some(receipt) = &mv.receipt {
        sets.push("receipt = type::record('receipt', $receipt)".to_owned());
        vars = vars.set("receipt", card::key_of(receipt, "receipt")?);
    }
    if let Some(red) = &mv.red_receipt {
        sets.push("red_receipt = type::record('receipt', $red)".to_owned());
        vars = vars.set("red", card::key_of(red, "receipt")?);
    }
    // A bridge resting on recollection (the memory project, or a card quoting a memory, instructions, agent,
    // skill or command unit) stays INFERRED even when its test passes (owner decision 2026-10-30).
    let recollection = card::is_recollection(&source);
    if to == "supported" && !recollection {
        sets.push("grade = 'CONFIRMED'".to_owned());
    }
    let sql = format!(
        "UPDATE type::record('bridge', $key) SET {} RETURN VALUE [status, grade];",
        sets.join(", ")
    );
    let done = store
        .retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?))
        .map_err(refused)?;
    let grade = done[0][0][1].as_str().unwrap_or("?").to_owned();
    Ok(format!(
        "bridge:{key} in {} is {to}, grade {grade}{}",
        r.target,
        if registering { "; test registered" } else { "" }
    ))
}

fn first_line(text: &str) -> &str {
    text.lines().next().unwrap_or(text)
}

/// The novelty check, as a verdict with what was searched: the query, the reach it ran over, the top hits
/// (locators and hashes only), and `made` hits: target-project text that already quotes the source card, names
/// it, or holds every meaning word of its mechanism. A `not_made` verdict against such a hit is refused.
pub(crate) fn novelty(store: &Store, r: &Reach, source: &Card, query: &str, verdict: &str) -> Result<Value> {
    let mut hits = Vec::new();
    let mut searched = Vec::new();
    let mut made = Vec::new();
    let quote = crate::ask::normalize(&source.quote).to_lowercase();
    let mech: Vec<String> = source.mech.as_deref().map(card::content_words).unwrap_or_default();
    for name in std::iter::once(&r.target).chain(&r.sources) {
        match store.retrieve(name, query, None, &[], 5) {
            Ok(found) => {
                searched.push(name.clone());
                for h in found {
                    if *name == r.target {
                        let text = crate::ask::normalize(&h.text).to_lowercase();
                        let words = card::content_words(&h.text);
                        if text.contains(&quote)
                            || text.contains(&format!("card:{}", source.key).to_lowercase())
                            || (mech.len() >= 2 && mech.iter().all(|w| words.contains(w)))
                        {
                            made.push(json!({"project": h.project, "locator": h.locator, "hash": h.hash}));
                        }
                    }
                    hits.push(json!({"project": h.project, "locator": h.locator, "hash": h.hash}));
                }
            }
            Err(e) => searched.push(format!("{name} (not searched: {})", first_line(&format!("{e:#}")))),
        }
    }
    if verdict == "not_made" && !made.is_empty() {
        bail!(
            "refused: novelty verdict not_made, but {} already holds the link: {}",
            r.target,
            made.iter()
                .filter_map(|m| m["locator"].as_str())
                .collect::<Vec<_>>()
                .join(", ")
        );
    }
    Ok(
        json!({"verdict": verdict, "query": query, "reach": searched, "hits": hits, "made": made,
              "at_machine": machine()}),
    )
}

/// Why a refuted bridge was refuted: its failing checks, quoted from the target, or its red receipt.
fn mismatch(b: &Row, target: &str) -> String {
    let mut body = String::new();
    for c in b.raw["assumption_check"].as_array().into_iter().flatten() {
        if c["verdict"] == "fails" {
            let _ = writeln!(
                body,
                "assumption {:?} fails in {target}: {:?} ({})",
                c["assumption"].as_str().unwrap_or("?"),
                c["quote"].as_str().unwrap_or("?"),
                c["cite"].as_str().unwrap_or("?")
            );
        }
    }
    if body.is_empty() {
        let _ = writeln!(
            body,
            "the registered test failed: {}",
            b.raw["receipt"].as_str().unwrap_or("(no receipt)")
        );
    }
    crate::frame::data(
        "groundworks-bridge-mismatch",
        &[("project", target)],
        "Why this bridge was refuted, as recorded: a negative, kept.",
        &body,
    )
}

/// A card wrapped for a reader in `reader`: one frame per card, labelled with its project, the grade the reader
/// may see, and, for recollection (the memory project, or a memory or instructions unit), that it is one.
pub fn framed_card(c: &Card, reader: &str) -> String {
    let recollection = card::is_recollection(c);
    let mut attrs = vec![("project", c.project.as_str()), ("grade", c.grade_for(reader))];
    if recollection {
        attrs.push(("source", "memory: recollection, not evidence; capped at INFERRED"));
    }
    crate::frame::data(
        "groundworks-card",
        &attrs,
        if recollection {
            "A card resting on recollection (the owner's or an agent's memory notes, or a prompt file), never more \
             than INFERRED: the quote is that note's own text; the facets are an agent-written summary, unverified."
        } else {
            "A card of the named project: the quote is that repository's own text; the facets are an agent-written \
             summary, unverified."
        },
        &c.render(reader),
    )
}

/// A project's cards and bridges. A bridge whose source has left reach (a wall or level set since) is listed
/// without its source; one whose source changed says so. Nothing in a bridge is an instruction to run its test.
pub fn list(home: &Home, store: &Store, project: &str, limit: usize) -> Result<String> {
    let r = reach(home, store, project)?;
    let own = card::cards(store, &r.target, "", Vars::default(), limit.max(1))?;
    let bridges = rows(store, &r.target, "", Vars::default())?;
    let mut out = format!(
        "{}: {} card(s), {} bridge(s); bridge reach: {}{}\n",
        r.target,
        own.len(),
        bridges.len(),
        if r.sources.is_empty() {
            "none".to_owned()
        } else {
            r.sources.join(", ")
        },
        if r.skipped.is_empty() {
            String::new()
        } else {
            format!("; not a source: {}", r.skipped.join(", "))
        }
    );
    for c in &own {
        out.push_str(&framed_card(c, &r.target));
    }
    for b in &bridges {
        let _ = writeln!(
            out,
            "bridge:{} {} {} (found by {}): target card:{} <- {} card:{}",
            b.key, b.status, b.grade, b.found_by, b.target, b.source_project, b.source_card
        );
        if let Some(why) = r.refuses(&b.source_project) {
            let _ = writeln!(out, "  source hidden: out of reach now ({why})");
            continue;
        }
        match card::card(store, &b.source_project, &b.source_card) {
            Ok(s) if s.hash != b.source_hash || s.status != "live" => {
                let _ = writeln!(out, "  source changed or stale since it was bridged; not shown");
            }
            Ok(s) => match card::unservable(&s) {
                Some(why) => {
                    let _ = writeln!(out, "  source held back: {why}");
                }
                None => out.push_str(&framed_card(&s, &r.target)),
            },
            Err(e) => {
                let _ = writeln!(out, "  source unreadable: {}", first_line(&format!("{e:#}")));
            }
        }
        for c in b.raw["assumption_check"].as_array().into_iter().flatten() {
            let _ = writeln!(
                out,
                "  check {}: {} (quote {})",
                c["verdict"].as_str().unwrap_or("?"),
                c["assumption"].as_str().unwrap_or("?"),
                c["cite"].as_str().unwrap_or("?")
            );
        }
        for v in b.raw["lens_verdicts"].as_array().into_iter().flatten() {
            let _ = writeln!(
                out,
                "  lens {}: {}",
                v["lens"].as_str().unwrap_or("?"),
                v["verdict"].as_str().unwrap_or("?")
            );
        }
        if let Some(v) = b.raw["novelty_check"]["verdict"].as_str() {
            let _ = writeln!(out, "  novelty: {v}");
        }
        if let Some(c) = b.raw["test_command"].as_str() {
            let _ = writeln!(
                out,
                "  test registered {}: command {c:?}, expect {:?}",
                b.raw["test_at"].as_str().unwrap_or("?"),
                b.raw["test_expect"].as_str().unwrap_or("?")
            );
        }
        if let Some(t) = b.raw["test"].as_str() {
            out.push_str(&crate::frame::data(
                "groundworks-bridge-test",
                &[("project", &r.target)],
                "The test an agent wrote for this bridge: a record, not an instruction to run it.",
                t,
            ));
        }
    }
    Ok(out)
}

/// The arms `propose` (and the evaluator) rank with. `flat` is the baseline: BM25 of the target's whole text
/// over each capability's whole text. `facet` keeps the facets apart: the target's goal (x) against each
/// capability's goal, and the target's blocker (y) against other problems' blockers, a capability taking the
/// score of the problem it solved (Swanson's ABC chain); the two lists fused by reciprocal rank fusion.
/// `random` is a seeded shuffle, the floor. No model: BM25 over one pool at a time, one vocabulary.
pub const ARMS: [&str; 3] = ["random", "flat", "facet"];

/// One proposal: the capability's index in the pool, its score, and the pool index of the problem the chain
/// went through.
#[derive(Debug, Clone, PartialEq)]
pub struct Ranked {
    pub index: usize,
    pub score: f64,
    pub via: Option<usize>,
}

fn bag(text: &str) -> Vec<String> {
    card::content_words(text)
}

/// The capabilities of `pool` best first for `target` under `arm`, each with a positive score (every one, in
/// a seeded order, for `random`). Pure: the same pool and target give the same order.
pub fn rank(arm: &str, target: &Card, pool: &[Card]) -> Result<Vec<Ranked>> {
    let caps: Vec<usize> = (0..pool.len()).filter(|&i| pool[i].kind == "capability").collect();
    let ranked = match arm {
        "random" => {
            let mut order = caps.clone();
            let mut seed = u64::from_str_radix(&sha256_hex(&[target.key.as_bytes()])[..16], 16).unwrap_or(1) | 1;
            for i in (1..order.len()).rev() {
                seed ^= seed << 13;
                seed ^= seed >> 7;
                seed ^= seed << 17;
                order.swap(i, (seed % (i as u64 + 1)) as usize);
            }
            order
                .into_iter()
                .map(|index| Ranked {
                    index,
                    score: 0.0,
                    via: None,
                })
                .collect()
        }
        "flat" => {
            let docs: Vec<Vec<String>> = caps.iter().map(|&i| bag(&pool[i].whole_text())).collect();
            let scores = crate::rerank::bm25(&bag(&target.whole_text()), &docs);
            crate::rerank::order_desc(&scores)
                .into_iter()
                .filter(|&j| scores[j] > 0.0)
                .map(|j| Ranked {
                    index: caps[j],
                    score: scores[j],
                    via: None,
                })
                .collect()
        }
        "facet" => {
            let x_docs: Vec<Vec<String>> = caps.iter().map(|&i| bag(&pool[i].x)).collect();
            let by_x = crate::rerank::bm25(&bag(&target.x), &x_docs);
            // Y <-> Y: the target's blocker against every other problem's, then onto the capability that solved it.
            let problems: Vec<usize> = (0..pool.len())
                .filter(|&i| pool[i].kind == "problem" && pool[i].y.is_some())
                .collect();
            let y_docs: Vec<Vec<String>> = problems
                .iter()
                .map(|&i| bag(pool[i].y.as_deref().unwrap_or_default()))
                .collect();
            let y_scores = crate::rerank::bm25(&bag(target.y.as_deref().unwrap_or_default()), &y_docs);
            let mut by_y = vec![0.0f64; caps.len()];
            let mut via = vec![None; caps.len()];
            for (j, &c) in caps.iter().enumerate() {
                let Some(solves) = pool[c].solves.as_deref() else {
                    continue;
                };
                for (k, &p) in problems.iter().enumerate() {
                    if pool[p].key == solves && pool[p].project == pool[c].project && y_scores[k] > by_y[j] {
                        by_y[j] = y_scores[k];
                        via[j] = Some(p);
                    }
                }
            }
            let fused = crate::rerank::rrf_positive(&[by_x.clone(), by_y.clone()], crate::rerank::RRF_K);
            fused
                .into_iter()
                .map(|j| Ranked {
                    index: caps[j],
                    score: by_x[j] + by_y[j],
                    via: via[j].filter(|_| by_y[j] > 0.0),
                })
                .collect()
        }
        other => bail!("unknown arm {other:?}; the arms are {}", ARMS.join(", ")),
    };
    Ok(ranked)
}

/// The live problem and capability cards of every project in reach, each servable; and those held back.
pub fn pool(store: &Store, r: &Reach) -> Result<(Vec<Card>, Vec<String>)> {
    let mut pool = Vec::new();
    let mut held = Vec::new();
    for name in &r.sources {
        let cards = card::cards(
            store,
            name,
            "WHERE status = 'live' AND kind IN ['problem', 'capability']",
            Vars::default(),
            5000,
        )?;
        for c in cards {
            match card::unservable(&c) {
                Some(why) => held.push(format!("{name} card:{} ({why})", c.key)),
                None => pool.push(c),
            }
        }
    }
    Ok((pool, held))
}

/// A candidate whose mechanism shares at least this share of meaning words (Jaccard) with a mechanism the target
/// project already has is vetoed: it is the target's own idea again, not a bridge (the "Contrasts" rule of the
/// owner's Punchable `creative/connections.py`: the same move is not another angle).
pub const VETO_JACCARD: f64 = 0.5;
/// A candidate is kept for a target only when its score there stands out against its own scores for every other
/// problem it could answer: z >= Z_MIN over at least Z_MIN_N of them, or, with fewer, at or above its own p99
/// (the k-th highest, k = 1% of them, at least 1). Never one global cutoff (Punchable `creative/merge.py` `_ref`:
/// each row's own "usually this close" level).
pub const Z_MIN: f64 = 2.5;
pub const Z_MIN_N: usize = 20;

fn word_set(text: &str) -> BTreeSet<String> {
    card::content_words(text).into_iter().collect()
}

fn jaccard(a: &BTreeSet<String>, b: &BTreeSet<String>) -> f64 {
    let union = a.union(b).count();
    if union == 0 {
        0.0
    } else {
        a.intersection(b).count() as f64 / union as f64
    }
}

/// What the screen did to one ranking.
#[derive(Debug, Default)]
pub struct Screened {
    /// Kept, best first, each score lowered by its whole-text closeness to the target project's own capabilities.
    pub kept: Vec<Ranked>,
    /// Pool index of each vetoed candidate and the own card whose mechanism it repeats.
    pub vetoed: Vec<(usize, String)>,
    /// Pool index of each candidate that did not stand out against its own distribution, and why.
    pub below: Vec<(usize, String)>,
}

/// Screen `ranked` (from [`rank`]) for `target`: the mechanism veto against `own` (the target project's live
/// capability cards), the relative threshold over each candidate's own scores for every other problem it could
/// answer (the pool's problems of projects other than the candidate's and the target's), and the whole-text
/// penalty. The random arm is not screened (it is the floor). Pure.
pub fn screen(arm: &str, target: &Card, pool: &[Card], own: &[Card], ranked: Vec<Ranked>) -> Result<Screened> {
    let mut out = Screened::default();
    if arm == "random" {
        out.kept = ranked;
        return Ok(out);
    }
    let own_caps: Vec<&Card> = own.iter().filter(|c| c.kind == "capability").collect();
    let own_mech: Vec<(BTreeSet<String>, &Card)> = own_caps
        .iter()
        .filter_map(|c| c.mech.as_deref().map(|m| (word_set(m), *c)))
        .collect();
    let own_whole: Vec<BTreeSet<String>> = own_caps.iter().map(|c| word_set(&c.whole_text())).collect();
    // Each other problem's ranking, once: the distribution every candidate is judged against.
    // Problems of the target's own project are left out: a capability that answers two of the asker's problems
    // is no hub.
    let others: Vec<&Card> = pool
        .iter()
        .filter(|q| q.kind == "problem" && q.project != target.project)
        .collect();
    let mut other_scores: Vec<(String, std::collections::HashMap<usize, f64>)> = Vec::new();
    for q in &others {
        let scores = rank(arm, q, pool)?.into_iter().map(|r| (r.index, r.score)).collect();
        other_scores.push((q.project.clone(), scores));
    }
    for r in ranked {
        let c = &pool[r.index];
        if let Some(m) = c.mech.as_deref() {
            let mine = word_set(m);
            if let Some((_, o)) = own_mech.iter().find(|(w, _)| jaccard(&mine, w) >= VETO_JACCARD) {
                out.vetoed.push((r.index, o.key.clone()));
                continue;
            }
        }
        let dist: Vec<f64> = other_scores
            .iter()
            .filter(|(project, _)| *project != c.project)
            .map(|(_, s)| s.get(&r.index).copied().unwrap_or(0.0))
            .collect();
        if let Some(why) = below(r.score, &dist) {
            out.below.push((r.index, why));
            continue;
        }
        let whole = word_set(&c.whole_text());
        let close = own_whole.iter().map(|o| jaccard(&whole, o)).fold(0.0f64, f64::max);
        out.kept.push(Ranked {
            score: r.score * (1.0 - close),
            ..r
        });
    }
    out.kept.sort_by(|a, b| b.score.total_cmp(&a.score));
    Ok(out)
}

/// Why `score` does not stand out against `dist` (the candidate's scores for every other problem), if it does
/// not: z below Z_MIN over at least Z_MIN_N, or, with fewer, not above the candidate's own p99.
pub fn below(score: f64, dist: &[f64]) -> Option<String> {
    let n = dist.len();
    if n == 0 {
        return None;
    }
    if n >= Z_MIN_N {
        let mean = dist.iter().sum::<f64>() / n as f64;
        let sd = (dist.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / n as f64).sqrt();
        let z = if sd > 0.0 {
            (score - mean) / sd
        } else if score > mean {
            f64::INFINITY
        } else {
            0.0
        };
        return (z < Z_MIN).then(|| format!("z {z:.2} below {Z_MIN} over its {n} other problems"));
    }
    let k = (n / 100).max(1);
    let mut sorted = dist.to_vec();
    sorted.sort_by(|a, b| b.total_cmp(a));
    let p99 = sorted[k - 1];
    (score <= p99).then(|| format!("score {score:.3} not above its own p99 {p99:.3} over {n} other problem(s)"))
}

/// One plain line on why a candidate was proposed: the meaning words its goal shares with the target's goal,
/// and, through the chain, the words its problem's blocker shares with the target's blocker; with the quoted
/// evidence of both cards.
pub fn why(target: &Card, c: &Card, via: Option<&Card>) -> String {
    let shared = |a: &str, b: &str| -> Vec<String> {
        let b = word_set(b);
        let mut seen = BTreeSet::new();
        card::content_words(a)
            .into_iter()
            .filter(|w| b.contains(w) && seen.insert(w.clone()))
            .collect()
    };
    let mut parts = Vec::new();
    let goal = shared(&target.x, &c.x);
    if !goal.is_empty() {
        parts.push(format!("shared goal words: {}", goal.join(", ")));
    }
    if let Some(v) = via {
        let blocker = shared(
            target.y.as_deref().unwrap_or_default(),
            v.y.as_deref().unwrap_or_default(),
        );
        if !blocker.is_empty() {
            parts.push(format!(
                "shared blocker words through its problem card:{}: {}",
                v.key,
                blocker.join(", ")
            ));
        }
    }
    if parts.is_empty() {
        parts.push(format!(
            "shared words: {}",
            shared(&target.whole_text(), &c.whole_text()).join(", ")
        ));
    }
    format!(
        "why: {}; target quote {:?}; source quote {:?}{}",
        parts.join("; "),
        target.quote,
        c.quote,
        via.map_or(String::new(), |v| format!("; its problem's quote {:?}", v.quote))
    )
}

/// Candidate bridges for one problem card of `project`: the capabilities of the projects in its bridge reach
/// ranked under `arm`, screened (mechanism veto, relative threshold, whole-text penalty), the best `k` shown,
/// each framed as data with its project, the grade the reader may see and a why line. A pair already bridged is
/// not proposed again: a refuted one comes back with its mismatch. Writes nothing; a candidate becomes a bridge
/// only through `bridge_add`.
pub fn propose(home: &Home, store: &Store, project: &str, target: &str, arm: &str, k: usize) -> Result<String> {
    let r = reach(home, store, project)?;
    let t = card::card(store, &r.target, target)?;
    ensure!(
        t.kind == "problem",
        "card:{} is a {}; propose takes a problem card",
        t.key,
        t.kind
    );
    ensure!(t.status == "live", "card:{} is {}, not live", t.key, t.status);
    let (pool, held) = pool(store, &r)?;
    let own = card::cards(
        store,
        &r.target,
        "WHERE status = 'live' AND kind IN ['problem', 'capability']",
        Vars::default(),
        5000,
    )?;
    let scored = rank(arm, &t, &pool)?;
    let n_scored = scored.len();
    let s = screen(arm, &t, &pool, &own, scored)?;
    let mut out = format!(
        "candidates for {} card:{} by the {arm} arm: {} of {} capabilities in reach scored; {} vetoed (same \
         mechanism as this project's own), {} below their own distribution; showing {}\n\
         searched: {}{}{}\n",
        r.target,
        t.key,
        n_scored,
        pool.iter().filter(|c| c.kind == "capability").count(),
        s.vetoed.len(),
        s.below.len(),
        s.kept.len().min(k),
        if r.sources.is_empty() {
            "nothing".to_owned()
        } else {
            r.sources.join(", ")
        },
        if r.skipped.is_empty() {
            String::new()
        } else {
            format!("; not a source: {}", r.skipped.join(", "))
        },
        if held.is_empty() {
            String::new()
        } else {
            format!("; held back: {}", held.join(", "))
        },
    );
    for (i, own_key) in &s.vetoed {
        let _ = writeln!(
            out,
            "vetoed: {} card:{} (its mechanism repeats this project's card:{own_key})",
            pool[*i].project, pool[*i].key
        );
    }
    for (i, reason) in &s.below {
        let _ = writeln!(out, "below: {} card:{} ({reason})", pool[*i].project, pool[*i].key);
    }
    // A pair already bridged is not proposed again: a refuted one comes back with its mismatch (METHOD section 13:
    // negatives are kept and served; scholar-architect 2026-10-30), any other with its status.
    let existing = rows(
        store,
        &r.target,
        "WHERE target = type::record('card', $t)",
        Vars::default().set("t", t.key.as_str()),
    )?;
    for (n, p) in s.kept.iter().take(k).enumerate() {
        let c = &pool[p.index];
        if let Some(b) = existing
            .iter()
            .filter(|b| b.source_project == c.project && b.source_card == c.key)
            .max_by_key(|b| b.status == "refuted")
        {
            let _ = writeln!(
                out,
                "[{}] {} card:{} ALREADY {} as bridge:{}: not proposed again",
                n + 1,
                c.project,
                c.key,
                b.status.to_uppercase(),
                b.key
            );
            if b.status == "refuted" {
                out.push_str(&mismatch(b, &r.target));
            }
            continue;
        }
        let via = p.via.map(|v| &pool[v]);
        let _ = writeln!(
            out,
            "[{}] {} card:{} score {:.3}{}",
            n + 1,
            c.project,
            c.key,
            p.score,
            via.map_or(String::new(), |v| format!(" via its problem card:{}", v.key))
        );
        // The why line quotes the source card, so it goes inside the source's frame.
        let recollection = card::is_recollection(c);
        let mut attrs = vec![("project", c.project.as_str()), ("grade", c.grade_for(&r.target))];
        if recollection {
            attrs.push(("source", "memory: recollection, not evidence; capped at INFERRED"));
        }
        out.push_str(&crate::frame::data(
            "groundworks-card",
            &attrs,
            "A candidate card of the named project: the quote is that repository's own text; the facets and the why \
             line are a program's and an agent's summary, unverified.",
            &format!("{}\n  {}", c.render(&r.target), why(&t, c, via)),
        ));
        if let Some(v) = via {
            out.push_str(&framed_card(v, &r.target));
        }
    }
    out.push_str(
        "Each candidate is a hypothesis, not a finding. To keep one, call bridge_add with the target, the source \
         project and card (and via); it is tested through bridge_advance.\n",
    );
    Ok(out)
}

/// The screen's two rules on synthetic cards, both ways; the failures, empty when they hold. Run by the
/// selftest. (1) A candidate repeating a mechanism the target project has is vetoed; one that does not is kept.
/// (2) The same weak match is dropped for a hub card that matches many problems as strongly, and kept for a
/// sparse card that matches nothing else, with at least Z_MIN_N problems (the z rule) and with fewer (p99).
pub fn screen_selfcheck() -> Vec<String> {
    let card = |project: &str, key: &str, kind: &str, x: &str, mech: Option<&str>| Card {
        project: project.to_owned(),
        key: key.to_owned(),
        kind: kind.to_owned(),
        x: x.to_owned(),
        y: (kind == "problem").then(|| "a blocker".to_owned()),
        a: if kind == "capability" {
            vec!["it holds".to_owned()]
        } else {
            Vec::new()
        },
        mech: mech.map(str::to_owned),
        solves: None,
        quote: String::new(),
        hash: String::new(),
        quote_hash: String::new(),
        grade: "INFERRED".to_owned(),
        status: "live".to_owned(),
        cite: None,
        unit: None,
        fact: None,
        source_kind: Some("doc".to_owned()),
        at: String::new(),
    };
    let mut failures = Vec::new();
    let target = card("p", "t", "problem", "steady input display", None);
    // (1) the veto
    let pool = vec![
        card(
            "q",
            "dup",
            "capability",
            "steady input display",
            Some("double buffer swap"),
        ),
        card(
            "q",
            "new",
            "capability",
            "steady input display",
            Some("hysteresis band"),
        ),
    ];
    let own = vec![card(
        "p",
        "mine",
        "capability",
        "keep input steady",
        Some("a double buffer swap"),
    )];
    match rank("facet", &target, &pool).and_then(|r| screen("facet", &target, &pool, &own, r)) {
        Ok(s) => {
            if !s.vetoed.iter().any(|(i, o)| pool[*i].key == "dup" && o == "mine") {
                failures.push("a candidate repeating the target's own mechanism was not vetoed".to_owned());
            }
            if !s.kept.iter().any(|r| pool[r.index].key == "new") {
                failures.push("a candidate with a new mechanism was not kept".to_owned());
            }
        }
        Err(e) => failures.push(format!("veto: {e:#}")),
    }
    // (2) the relative threshold, at 30 other problems (z) and at 3 (p99)
    for others in [30usize, 3] {
        let mut pool = vec![
            card("q", "hub", "capability", "common", Some("hub mechanism")),
            card(
                "q",
                "sparse",
                "capability",
                "rare filler words that make this goal long and weak",
                Some("sparse mechanism"),
            ),
        ];
        for i in 0..others {
            pool.push(card("r", &format!("o{i}"), "problem", "common", None));
        }
        let target = card("p", "t", "problem", "common rare", None);
        match rank("facet", &target, &pool).and_then(|r| screen("facet", &target, &pool, &[], r)) {
            Ok(s) => {
                let score = |key: &str| {
                    rank("facet", &target, &pool)
                        .ok()
                        .and_then(|r| r.into_iter().find(|x| pool[x.index].key == key))
                        .map_or(0.0, |x| x.score)
                };
                if score("sparse") >= score("hub") {
                    failures.push(format!(
                        "fixture: the sparse match must be the weaker ({others} problems)"
                    ));
                }
                if s.kept.iter().any(|r| pool[r.index].key == "hub") {
                    failures.push(format!(
                        "a hub card matching {others} other problems as strongly was kept: no relative threshold"
                    ));
                }
                if !s.kept.iter().any(|r| pool[r.index].key == "sparse") {
                    failures.push(format!(
                        "a sparse card's weak but distinctive match was dropped ({others} problems)"
                    ));
                }
            }
            Err(e) => failures.push(format!("threshold: {e:#}")),
        }
    }
    failures
}

#[cfg(test)]
mod tests {
    #[test]
    fn the_screen_vetoes_repeats_and_keeps_only_what_stands_out() {
        assert_eq!(super::screen_selfcheck(), Vec::<String>::new());
    }

    #[test]
    fn below_uses_z_with_enough_problems_and_p99_with_few() {
        let many: Vec<f64> = (0..30).map(|i| f64::from(i % 3)).collect();
        assert!(super::below(10.0, &many).is_none());
        assert!(super::below(2.0, &many).is_some());
        assert!(super::below(1.0, &[0.0, 0.5]).is_none());
        assert!(super::below(0.4, &[0.0, 0.5]).is_some());
        assert!(
            super::below(0.5, &[0.0, 0.5]).is_some(),
            "level with p99 does not stand out"
        );
    }

    /// A3.20: three shared projects and a wall between two of them; a bridge read for the third admits the
    /// first of the walled pair (registry order) and skips the second as walled from it. Eligibility still
    /// refuses a project walled from the target and one that is not shared.
    #[test]
    fn a_bridge_read_never_holds_both_sides_of_a_wall() {
        let shared = || Ok("shared".to_owned());
        let walls = vec![("proj_a".to_owned(), "proj_b".to_owned())];
        let r = super::admit(
            "c",
            "proj_c",
            &walls,
            vec![
                ("a".into(), "proj_a".into(), shared()),
                ("b".into(), "proj_b".into(), shared()),
            ],
        );
        assert_eq!(r.sources, ["a"]);
        assert_eq!(r.skipped, ["b (walled from a)"]);
        assert_eq!(r.refuses("b").as_deref(), Some("b (walled from a)"));

        let walls = vec![("proj_c".to_owned(), "proj_d".to_owned())];
        let r = super::admit(
            "c",
            "proj_c",
            &walls,
            vec![
                ("d".into(), "proj_d".into(), shared()),
                ("p".into(), "proj_p".into(), Ok("private".to_owned())),
                ("q".into(), "proj_q".into(), Err("down".to_owned())),
            ],
        );
        assert!(r.sources.is_empty());
        assert_eq!(
            r.skipped,
            [
                "d (walled from c)",
                "p (private: never a bridge source)",
                "q (level unreadable, refused: down)"
            ]
        );
    }
}
