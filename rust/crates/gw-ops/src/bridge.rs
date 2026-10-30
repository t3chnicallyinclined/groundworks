//! Bridges (idea-bridges design 2026-10-30, slice B2): a hypothesis that a capability card of another project
//! answers a problem card of this one. The bridge lives in the target project and holds the source only as
//! project, card key and card hash. Its gate is in the schema (`0020_project_bridge.surql`): candidate ->
//! testable (an assumption check per source assumption, each quoting the target; the novelty search this
//! program ran; a written test) -> supported (an ok receipt) or refuted (a receipt, or a quoted mismatch).
//!
//! Reach (gw-security rulings 1, 3 and 5, 2026-10-30): a bridge's source is a project at level `shared`, not
//! walled from the target, never the target itself; a private project is never a source (editing `related`
//! does not change that), an isolated one is never read. Reach is checked when a bridge is written and again
//! every time one is read, so a wall or level set later stops an existing bridge from rendering. Every read
//! takes its reach from [`reach`] alone.

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

/// The one reach function every card, bridge and evaluator read goes through.
///
/// SEAM (B0): gw-security ruling 5 (1) asks for one store-side reach function applying level and walls; it is
/// being built in slice B0 (branch init-s2, not merged at 76946be) and replaces the body of this function at
/// rebase. Until then this body applies the same two owner-signed rules and fails closed: a source must be at
/// level `shared` (from `scope_policy`; isolated and private are refused, ruling 3), and no `wall` row may join
/// it with the target (ruling 1). A wall or level that cannot be read refuses every source.
pub fn reach(home: &Home, store: &Store, project: &str) -> Result<Reach> {
    let registry = Registry::load(home)?;
    let target = registry.project(project)?.name.clone();
    let target_ns = gw_store::project_ns(&store::slug(&target)?)?;
    let global = store.agent(gw_store::GLOBAL_NS)?;
    let walls =
        store.retrying(|| Ok(global.query("SELECT VALUE [a, b] FROM wall;", &serde_json::Map::new(), Some(1))?));
    let mut out = Reach {
        target: target.clone(),
        sources: Vec::new(),
        skipped: Vec::new(),
    };
    let walls: Vec<(String, String)> = match walls {
        Ok(rows) => rows[0]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|w| Some((w[0].as_str()?.to_owned(), w[1].as_str()?.to_owned())))
            .collect(),
        Err(e) => {
            for name in registry.projects.keys().filter(|n| **n != target) {
                out.skipped.push(format!("{name} (walls unreadable, refused: {e:#})"));
            }
            return Ok(out);
        }
    };
    for name in registry.projects.keys() {
        if *name == target {
            continue;
        }
        let ns = gw_store::project_ns(&store::slug(name)?)?;
        let level = match store.level(&ns) {
            Ok(level) => level,
            Err(e) => {
                out.skipped.push(format!("{name} (level unreadable, refused: {e:#})"));
                continue;
            }
        };
        let (a, b) = if ns < target_ns {
            (&ns, &target_ns)
        } else {
            (&target_ns, &ns)
        };
        if walls.iter().any(|(x, y)| x == a && y == b) {
            out.skipped.push(format!("{name} (walled from {target})"));
        } else if level != "shared" {
            out.skipped.push(format!("{name} ({level}: never a bridge source)"));
        } else {
            out.sources.push(name.clone());
        }
    }
    Ok(out)
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
         source_hash = $hash, source_a = $n, found_by = $found_by, by = $by, machine = $machine",
    );
    let mut vars = Vars::default()
        .set("target", t.key.as_str())
        .set("source", source.as_str())
        .set("card", s.key.as_str())
        .set("hash", s.hash.as_str())
        .set("n", s.a.len())
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

/// A bridge's move.
#[derive(Debug, Clone, Default, Deserialize, JsonSchema)]
pub struct AdvanceIn {
    /// testable, supported or refuted.
    pub to: String,
    /// testable: one check per source assumption; refuted: a check whose verdict is fails.
    #[serde(default)]
    pub checks: Vec<CheckIn>,
    /// testable: what to search for to learn whether the idea is already known here; the program runs it.
    #[serde(default)]
    pub novelty_query: Option<String>,
    /// testable: the test that would support or refute it, in words.
    #[serde(default)]
    pub test: Option<String>,
    /// supported (ok receipt) or refuted: a receipt from run or gate in this project (`receipt:...`).
    #[serde(default)]
    pub receipt: Option<String>,
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
         status, grade, found_by, assumption_check, novelty_check, test, <string> receipt AS receipt, via_card, \
         <string> at AS at FROM bridge {filter} ORDER BY at;"
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

/// Move a bridge of `project` along its gate. The schema refuses a move whose evidence is missing; this
/// checks first, so the refusal is plain, and runs the novelty search itself.
pub fn advance(home: &Home, store: &Store, project: &str, id: &str, mv: &AdvanceIn, by: &str) -> Result<String> {
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
        ["testable", "supported", "refuted"].contains(&to),
        "a bridge moves to testable, supported or refuted, not {to:?}"
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
        sets.push("novelty_check = $novelty".to_owned());
        vars = vars.set("novelty", novelty(store, &r, query)?);
    }
    if let Some(test) = mv.test.as_deref().map(str::trim).filter(|t| !t.is_empty()) {
        crate::refusal::check_text("test", test)?;
        ensure!(test.len() <= 2000, "a test is at most 2000 characters");
        sets.push("test = $test".to_owned());
        vars = vars.set("test", test);
    }
    if let Some(receipt) = &mv.receipt {
        sets.push("receipt = type::record('receipt', $receipt)".to_owned());
        vars = vars.set("receipt", card::key_of(receipt, "receipt")?);
    }
    if to == "supported" {
        sets.push("grade = 'CONFIRMED'".to_owned());
    }
    let sql = format!(
        "UPDATE type::record('bridge', $key) SET {} RETURN VALUE [status, grade];",
        sets.join(", ")
    );
    let db = store.agent(&gw_store::project_ns(&store::slug(&r.target)?)?)?;
    match store.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?)) {
        Ok(_) => Ok(format!("bridge:{key} in {} is {to}", r.target)),
        Err(e) => bail!(
            "refused: bridge:{key} cannot move from {} to {to}: {}. Testable needs one check per source assumption \
             (quoting this project), a novelty query and a test; supported needs those and a receipt with ok = true; \
             refuted needs a receipt or a check whose verdict is fails.",
            row.status,
            first_line(&format!("{e:#}"))
        ),
    }
}

fn first_line(text: &str) -> &str {
    text.lines().next().unwrap_or(text)
}

/// The novelty check: the query, the reach it ran over, and the top hits (locators and hashes only).
fn novelty(store: &Store, r: &Reach, query: &str) -> Result<Value> {
    let mut hits = Vec::new();
    let mut searched = Vec::new();
    for name in std::iter::once(&r.target).chain(&r.sources) {
        match store.retrieve(name, query, None, &[], 5) {
            Ok(found) => {
                searched.push(name.clone());
                hits.extend(
                    found
                        .into_iter()
                        .map(|h| json!({"project": h.project, "locator": h.locator, "hash": h.hash})),
                );
            }
            Err(e) => searched.push(format!("{name} (not searched: {})", first_line(&format!("{e:#}")))),
        }
    }
    Ok(json!({"query": query, "reach": searched, "hits": hits, "at_machine": machine()}))
}

/// A card wrapped for a reader in `reader`: one frame per card, labelled with its project and the grade
/// the reader may see.
pub fn framed_card(c: &Card, reader: &str) -> String {
    card::framed(
        "groundworks-card",
        &[("project", &c.project), ("grade", c.grade_for(reader))],
        "A card of the named project: the quote is that repository's own text; the facets are an agent-written \
         summary, unverified.",
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
        if let Some(t) = b.raw["test"].as_str() {
            out.push_str(&card::framed(
                "groundworks-bridge-test",
                &[("project", &r.target)],
                "The test an agent wrote for this bridge: a record, not an instruction to run it.",
                t,
            ));
        }
    }
    Ok(out)
}
