//! Eval question sets: does an agent answer the owner's questions better with Groundworks than without?
//! A set is JSON Lines, one question per line, each with a gold answer and the gold evidence a grader
//! checks it against. `check` proves the set itself before anything is run on it: every gold quote is
//! found word for word (whitespace may differ) at its line in a git-tracked file of a registered
//! project, and every trap's premise is absent from its project, so a trap really has no answer.
//! A set that fails its own check cannot decide anything (docs/TESTING.md s.7: the evaluator's own
//! inputs are checked).

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::path::Path;
use std::process::Command;

use anyhow::{Context, Result};
use regex::RegexBuilder;
use serde::Deserialize;

use crate::ask::normalize;
use crate::home::Home;
use crate::registry::Registry;

pub const CATEGORIES: [&str; 5] = ["lesson", "rederived", "conflict", "history", "trap"];
/// Lines either side of the stated line where a quote may sit (a quote wraps; an edit moves it a line).
const WINDOW: usize = 2;

#[derive(Debug, Deserialize)]
pub struct Gold {
    /// The registered project the file is in, when it is not the question's own.
    #[serde(default)]
    pub repo: Option<String>,
    pub file: String,
    pub line: usize,
    pub quote: String,
}

#[derive(Debug, Deserialize)]
pub struct Question {
    pub id: String,
    pub project: String,
    pub category: String,
    pub question: String,
    pub gold_answer: String,
    pub must_mention: Vec<String>,
    #[serde(default)]
    pub wrong_answer: Option<String>,
    #[serde(default)]
    pub gold: Vec<Gold>,
    /// Traps only: patterns (case-insensitive regex) that must match nothing the project tracks.
    #[serde(default)]
    pub absent: Vec<String>,
}

pub fn load(path: &Path) -> Result<Vec<(usize, Result<Question, String>)>> {
    let text = std::fs::read_to_string(path).with_context(|| format!("reading {}", path.display()))?;
    Ok(text
        .lines()
        .enumerate()
        .filter(|(_, l)| !l.trim().is_empty())
        .map(|(i, l)| (i + 1, serde_json::from_str(l).map_err(|e| e.to_string())))
        .collect())
}

fn tracked(dir: &Path) -> Result<Vec<String>> {
    let out = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
        .output()?;
    anyhow::ensure!(out.status.success(), "git ls-files failed in {}", dir.display());
    Ok(String::from_utf8_lossy(&out.stdout)
        .split('\0')
        .filter(|f| !f.is_empty())
        .map(str::to_owned)
        .collect())
}

/// Per project: its tracked files and its directory.
type Files = BTreeMap<String, (BTreeSet<String>, std::path::PathBuf)>;

/// Every problem with one question; empty means it holds.
fn problems(q: &Question, home: &Home, registry: &Registry, files: &mut Files) -> Vec<String> {
    let mut out = Vec::new();
    if !CATEGORIES.contains(&q.category.as_str()) {
        out.push(format!(
            "category {:?} is not one of {}",
            q.category,
            CATEGORIES.join(", ")
        ));
    }
    for (field, value) in [("question", &q.question), ("gold_answer", &q.gold_answer)] {
        if value.trim().is_empty() {
            out.push(format!("{field} is empty"));
        }
    }
    if q.must_mention.iter().all(|m| m.trim().is_empty()) {
        out.push("must_mention names nothing a grader can check".into());
    }
    let mut names: Vec<&str> = vec![q.project.as_str()];
    names.extend(q.gold.iter().filter_map(|g| g.repo.as_deref()));
    for name in names {
        if files.contains_key(name) {
            continue;
        }
        let loaded = registry.project(name).and_then(|p| {
            let dir = p.path(&home.workspace);
            Ok((tracked(&dir)?.into_iter().collect(), dir))
        });
        match loaded {
            Ok(entry) => {
                files.insert(name.to_owned(), entry);
            }
            Err(e) => {
                out.push(format!("{e:#}"));
                return out;
            }
        }
    }
    let files = &*files;
    let trap = q.category == "trap";
    match (trap, q.gold.is_empty(), q.absent.is_empty()) {
        (true, false, _) => out.push("a trap has no gold evidence; list what must be absent instead".into()),
        (true, _, true) => out.push("a trap must name the absent premise (absent: [patterns])".into()),
        (false, true, _) => out.push("no gold evidence".into()),
        (false, _, false) => out.push("only a trap lists absent patterns".into()),
        _ => {}
    }
    for g in &q.gold {
        let repo = g.repo.as_deref().unwrap_or(&q.project);
        let (tracked, dir) = &files[repo];
        let file = g.file.replace('\\', "/");
        if !tracked.contains(&file) {
            out.push(format!("{file} is not a tracked file of {repo}"));
            continue;
        }
        let name = Path::new(&file).file_name().and_then(|n| n.to_str()).unwrap_or(&file);
        if q.question.contains(name) {
            out.push(format!("the question names its gold file {name}"));
        }
        let wanted = normalize(&g.quote);
        if wanted.split(' ').count() < 8 {
            out.push(format!("{file}:{}: a gold quote needs at least 8 words", g.line));
        }
        let text = match std::fs::read_to_string(dir.join(&file)) {
            Ok(t) => t,
            Err(e) => {
                out.push(format!("{file}: {e}"));
                continue;
            }
        };
        let lines: Vec<&str> = text.lines().collect();
        let from = g.line.saturating_sub(1 + WINDOW);
        let to = (g.line + WINDOW).min(lines.len());
        let window = normalize(&lines.get(from..to).map(|l| l.join("\n")).unwrap_or_default());
        if g.line == 0 || !window.contains(&wanted) {
            let anywhere = normalize(&text).contains(&wanted);
            out.push(format!(
                "{file}:{}: quote not found word for word there{}",
                g.line,
                if anywhere {
                    " (it is elsewhere in the file: fix the line)"
                } else {
                    ""
                }
            ));
        }
    }
    let (tracked, dir) = &files[q.project.as_str()];
    for pattern in &q.absent {
        let re = match RegexBuilder::new(pattern).case_insensitive(true).build() {
            Ok(re) => re,
            Err(e) => {
                out.push(format!("absent pattern {pattern:?}: {e}"));
                continue;
            }
        };
        let hits: Vec<&String> = tracked
            .iter()
            .filter(|f| {
                std::fs::read(dir.join(f))
                    .ok()
                    .filter(|b| b.len() < 2_000_000 && !b.contains(&0))
                    .is_some_and(|b| re.is_match(&String::from_utf8_lossy(&b)))
            })
            .take(3)
            .collect();
        if !hits.is_empty() {
            out.push(format!(
                "the trap's premise {pattern:?} is in the project after all: {}",
                hits.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")
            ));
        }
    }
    out
}

/// Check a question set. Returns (report, every question holds).
pub fn check(home: &Home, path: &Path) -> Result<(String, bool)> {
    let registry = Registry::load(home)?;
    let mut files = BTreeMap::new();
    let mut ids = BTreeSet::new();
    let (mut good, mut bad) = (0, 0);
    let mut by: BTreeMap<(String, String), usize> = BTreeMap::new();
    let mut out = String::new();
    for (line, parsed) in load(path)? {
        let q = match parsed {
            Ok(q) => q,
            Err(e) => {
                bad += 1;
                writeln!(out, "FAIL line {line}: not a question: {e}")?;
                continue;
            }
        };
        let mut errs = problems(&q, home, &registry, &mut files);
        if !ids.insert(q.id.clone()) {
            errs.push("duplicate id".into());
        }
        if errs.is_empty() {
            good += 1;
            *by.entry((q.category.clone(), q.project.clone())).or_default() += 1;
        } else {
            bad += 1;
            writeln!(out, "FAIL {} ({}): {}", q.id, q.project, errs.join("; "))?;
        }
    }
    writeln!(out, "\n{good} question(s) hold, {bad} fail, in {}", path.display())?;
    for ((category, project), n) in &by {
        writeln!(out, "  {category:<10} {project}: {n}")?;
    }
    Ok((out, bad == 0 && good > 0))
}

/// Seed the journal from an approved set: each non-trap question's gold answer becomes a CONFIRMED fact in
/// the project that holds its first gold quote, with that quote (widened to whole file lines) as the
/// evidence the store checks. Idempotent: a fact with the same text in that project is not written twice.
pub fn seed(home: &Home, store: &crate::store::Store, path: &Path) -> Result<String> {
    let registry = Registry::load(home)?;
    let sha = &crate::store::sha256_hex(&[std::fs::read(path)?.as_slice()])[..12];
    let (mut written, mut skipped, mut out) = (0, 0, String::new());
    for (_, q) in load(path)? {
        let Ok(q) = q else { continue };
        let Some(g) = q.gold.first() else { continue };
        let repo = g.repo.as_deref().unwrap_or(&q.project);
        let project = registry.project(repo)?;
        let dir = project.path(&home.workspace);
        let file = g.file.replace('\\', "/");
        let text = std::fs::read_to_string(dir.join(&file))
            .with_context(|| format!("{}: reading {file}", q.id))?
            .replace("\r\n", "\n");
        let lines: Vec<&str> = text.lines().collect();
        let want = normalize(&g.quote);
        // The smallest run of whole lines around the stated line that holds the quote.
        let mut found = None;
        'outer: for width in 1..=4usize {
            for start in g.line.saturating_sub(1 + WINDOW)..=(g.line + WINDOW).min(lines.len()) {
                let end = start + width;
                if start < lines.len() && end <= lines.len() && normalize(&lines[start..end].join(" ")).contains(&want)
                {
                    found = Some((start + 1, lines[start..end].join("\n")));
                    break 'outer;
                }
            }
        }
        let Some((line, quote_text)) = found else {
            writeln!(
                out,
                "{}: gold quote not found around {file}:{}; not seeded",
                q.id, g.line
            )?;
            continue;
        };
        if store
            .facts(&project.name, None, 2000)?
            .iter()
            .any(|f| f.text == q.gold_answer.trim())
        {
            skipped += 1;
            continue;
        }
        let fact = crate::store::FactIn {
            kind: crate::journal::Kind::Fact,
            text: q.gold_answer.trim().to_owned(),
            confidence: crate::journal::Confidence::Confirmed,
            source: format!("owner-approved eval gold ({}, set {sha})", q.id),
            by: "eval-gold".to_owned(),
            receipt: None,
            owner_statement: None,
            quote: Some(crate::store::Quote {
                file: file.clone(),
                line,
                text: quote_text,
            }),
            lens: None,
            attacks: None,
            stance: None,
        };
        let id = store.add_fact(&dir, &project.name, &fact)?;
        writeln!(out, "{}: {id} in {} ({file}:{line})", q.id, project.name)?;
        written += 1;
    }
    writeln!(out, "\n{written} fact(s) written, {skipped} already there")?;
    Ok(out)
}

/// Retrieval alone, no model: for each question with gold, was every gold line ingested (a present unit
/// covers it), and at what rank does the store's ranked read put a gold unit when the question itself is
/// the query? Reports coverage, recall at 1, 5, 20 and 200, and the mean reciprocal rank.
pub fn retrieval(store: &crate::store::Store, path: &Path) -> Result<String> {
    const DEPTH: usize = 200;
    let mut out = String::new();
    let (mut asked, mut covered, mut mrr) = (0usize, 0usize, 0.0f64);
    let mut at = [0usize; 4];
    for (_, q) in load(path)? {
        let Ok(q) = q else { continue };
        if q.gold.is_empty() {
            continue;
        }
        asked += 1;
        let mut gold: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        let mut missing = Vec::new();
        for g in &q.gold {
            let repo = g.repo.clone().unwrap_or_else(|| q.project.clone());
            let keys = store.units_at(&repo, &g.file.replace('\\', "/"), g.line)?;
            if keys.is_empty() {
                missing.push(format!("{repo}:{}:{}", g.file, g.line));
            }
            gold.entry(repo).or_default().extend(keys);
        }
        if missing.is_empty() {
            covered += 1;
        }
        let mut best: Option<usize> = None;
        for (repo, keys) in &gold {
            let hits = store.retrieve(repo, &q.question, None, &[], DEPTH)?;
            if let Some(i) = hits.iter().position(|h| keys.contains(&h.key)) {
                best = Some(best.map_or(i + 1, |b| b.min(i + 1)));
            }
        }
        if let Some(rank) = best {
            mrr += 1.0 / rank as f64;
            for (slot, k) in [1, 5, 20, DEPTH].iter().enumerate() {
                at[slot] += usize::from(rank <= *k);
            }
        }
        writeln!(
            out,
            "{:<8} rank {:>5}  {}",
            q.id,
            best.map_or("miss".to_owned(), |r| r.to_string()),
            if missing.is_empty() {
                String::new()
            } else {
                format!("NOT INGESTED: {}", missing.join(", "))
            }
        )?;
    }
    let pct = |k: usize| format!("{k}/{asked} ({:.0}%)", 100.0 * k as f64 / asked.max(1) as f64);
    writeln!(
        out,
        "\ngold lines ingested: {}\nquestion as query, gold unit at rank 1: {}; top 5: {}; top 20: {}; top {DEPTH}: {}\nmean reciprocal rank: {:.3}",
        pct(covered),
        pct(at[0]),
        pct(at[1]),
        pct(at[2]),
        pct(at[3]),
        mrr / asked.max(1) as f64
    )?;
    Ok(out)
}

/// One question's place in the retrieval ladder: where each arm puts its best gold unit.
#[derive(Debug, serde::Serialize)]
pub struct LadderRow {
    pub id: String,
    pub set: String,
    pub project: String,
    pub category: String,
    pub machine: String,
    /// Candidates in the pool (the reach's merged read at the ladder's depth).
    pub pool: usize,
    pub gold_units: usize,
    pub gold_in_pool: bool,
    /// Best gold rank per arm, 1-based; None when no gold unit is in the pool.
    pub ranks: BTreeMap<String, Option<usize>>,
    /// Bytes of candidate text in each arm's top 10 and top 20.
    pub bytes_10: BTreeMap<String, usize>,
    pub bytes_20: BTreeMap<String, usize>,
    /// The store arm's top-20 bytes: the packet's cost today. Per arm, whether a gold unit is
    /// among the candidates that fit in that many bytes taken in the arm's order (the same token
    /// cost as today's packet).
    pub budget_bytes: usize,
    pub in_budget: BTreeMap<String, bool>,
    /// Each arm's top five as `project:locator (kind, bytes)`, with `*` on a gold unit: what sits
    /// above the gold when it is not first.
    pub top5: BTreeMap<String, Vec<String>>,
    /// The gold units' own `project:locator (kind, bytes)`, those in the pool.
    pub gold_in: Vec<String>,
    /// Per arm, whether a gold unit is among the candidates a byte-capped packet would hold:
    /// taken in the arm's order while the text fits in [`PACKET_CAP_BYTES`], at most 20.
    pub in_cap: BTreeMap<String, bool>,
    /// Per arm, how many candidates that packet holds.
    pub cap_count: BTreeMap<String, usize>,
}

/// Bytes of candidate text per prompt token, measured over 114 packets (gw-probe r1, 2026-10-26).
pub const BYTES_PER_TOKEN: f64 = 2.98;

/// The packet's byte cap, as ask applies it.
pub const PACKET_CAP_BYTES: usize = crate::ask::PACKET_CAP_BYTES;

/// The retrieval ladder (pre-registered as retrieval-ladder-r1): for every question with a gold
/// unit, the reach's merged read at `depth` (facts excluded) is reordered by each arm of
/// `rerank::ladder_orders`, and the best gold's rank under each is recorded. Returns the report
/// and the rows.
pub fn ladder(home: &Home, store: &crate::store::Store, sets: &[std::path::PathBuf], depth: usize) -> Result<(String, Vec<LadderRow>)> {
    let machine = std::env::var("COMPUTERNAME")
        .or_else(|_| std::env::var("HOSTNAME"))
        .unwrap_or_else(|_| "unknown".to_owned());
    let mut rows = Vec::new();
    for path in sets {
        let set = path
            .parent()
            .and_then(Path::file_name)
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        for (_, q) in load(path)? {
            let Ok(q) = q else { continue };
            if q.gold.is_empty() {
                continue;
            }
            let mut gold: BTreeSet<(String, String)> = BTreeSet::new();
            for g in &q.gold {
                let repo = g.repo.clone().unwrap_or_else(|| q.project.clone());
                for key in store.units_at(&repo, &g.file.replace('\\', "/"), g.line)? {
                    gold.insert((repo.clone(), key));
                }
            }
            let (names, _) = crate::ask::reach_names(home, store, Some(&q.project))
                .with_context(|| format!("{}: reach of {}", q.id, q.project))?;
            // The union of every project's read at `depth`, each hit remembering its rank in its
            // own project's read. The store arm is the packet's own order over the union, exactly
            // as `ask` orders it (ask::order_hits), so its numbers are what agents get.
            let mut union: Vec<(crate::store::Hit, usize)> = Vec::new();
            for name in &names {
                if let Ok(found) = store.retrieve(name, &q.question, None, &[], depth) {
                    union.extend(found.into_iter().zip(1..));
                }
            }
            let key = |h: &crate::store::Hit| (h.project.clone(), h.key.clone());
            let rank_in_project: BTreeMap<(String, String), usize> =
                union.iter().map(|(h, r)| (key(h), *r)).collect();
            let mut by_score: Vec<crate::store::Hit> = union.into_iter().map(|(h, _)| h).collect();
            by_score.sort_by(|a, b| b.score.unwrap_or(0.0).total_cmp(&a.score.unwrap_or(0.0)));
            // The pool's own order is the store's merged scores (the order ask shipped until
            // 2026-10-26, the ladder's baseline); the shipped order is the `bm25` arm.
            let pool = by_score;
            let texts: Vec<&str> = pool.iter().map(|h| h.text.as_str()).collect();
            let locators: Vec<&str> = pool.iter().map(|h| h.locator.as_str()).collect();
            let projects: Vec<&str> = pool.iter().map(|h| h.project.as_str()).collect();
            let project_rank: Vec<usize> = pool.iter().map(|h| rank_in_project[&key(h)]).collect();
            let arms_pool = crate::rerank::Pool {
                texts: &texts,
                locators: &locators,
                projects: &projects,
                project_rank: &project_rank,
                own: &q.project,
            };
            let is_gold: Vec<bool> = pool
                .iter()
                .map(|h| gold.contains(&(h.project.clone(), h.key.clone())))
                .collect();
            let label = |i: usize| {
                let h = &pool[i];
                format!(
                    "{}{}:{} ({}, {}b)",
                    if is_gold[i] { "*" } else { "" },
                    h.project,
                    h.locator,
                    h.kind.as_deref().unwrap_or("?"),
                    h.text.len()
                )
            };
            let (mut ranks, mut bytes_10, mut bytes_20, mut top5, mut in_budget) =
                (BTreeMap::new(), BTreeMap::new(), BTreeMap::new(), BTreeMap::new(), BTreeMap::new());
            let (mut in_cap, mut cap_count) = (BTreeMap::new(), BTreeMap::new());
            let budget_bytes: usize = texts.iter().take(20).map(|t| t.len()).sum();
            for (arm, order) in crate::rerank::ladder_orders(&q.question, &arms_pool) {
                ranks.insert(arm.to_owned(), order.iter().position(|&i| is_gold[i]).map(|p| p + 1));
                let bytes = |k: usize| order.iter().take(k).map(|&i| texts[i].len()).sum::<usize>();
                bytes_10.insert(arm.to_owned(), bytes(10));
                bytes_20.insert(arm.to_owned(), bytes(20));
                top5.insert(arm.to_owned(), order.iter().take(5).map(|&i| label(i)).collect());
                let mut used = 0usize;
                let mut hit = false;
                for &i in &order {
                    used += texts[i].len();
                    // The first candidate always fits, as it does in a real packet.
                    if used > budget_bytes && used != texts[i].len() {
                        break;
                    }
                    if is_gold[i] {
                        hit = true;
                        break;
                    }
                }
                in_budget.insert(arm.to_owned(), hit);
                let (mut used, mut count, mut hit) = (0usize, 0usize, false);
                for &i in &order {
                    if count == 20 || (used + texts[i].len() > PACKET_CAP_BYTES && count > 0) {
                        break;
                    }
                    used += texts[i].len();
                    count += 1;
                    hit |= is_gold[i];
                }
                in_cap.insert(arm.to_owned(), hit);
                cap_count.insert(arm.to_owned(), count);
            }
            let gold_in: Vec<String> = (0..pool.len()).filter(|&i| is_gold[i]).map(label).collect();
            rows.push(LadderRow {
                id: q.id.clone(),
                set: set.clone(),
                project: q.project.clone(),
                category: q.category.clone(),
                machine: machine.clone(),
                pool: pool.len(),
                gold_units: gold.len(),
                gold_in_pool: is_gold.iter().any(|&g| g),
                ranks,
                bytes_10,
                bytes_20,
                top5,
                gold_in,
                budget_bytes,
                in_budget,
                in_cap,
                cap_count,
            });
        }
    }
    Ok((ladder_report(&rows, depth), rows))
}

/// Exact two-sided sign test: `wins` of `n` non-tied pairs under p = 1/2.
pub fn sign_test(wins: usize, n: usize) -> f64 {
    if n == 0 {
        return 1.0;
    }
    let pmf = |k: usize| {
        let mut c = 1.0f64;
        for i in 0..k {
            c = c * (n - i) as f64 / (i + 1) as f64;
        }
        c / 2f64.powi(n as i32)
    };
    let at = pmf(wins);
    (0..=n)
        .map(pmf)
        .filter(|p| *p <= at * (1.0 + 1e-9))
        .sum::<f64>()
        .min(1.0)
}

fn ladder_report(rows: &[LadderRow], depth: usize) -> String {
    use crate::eval_run::wilson;
    use crate::rerank::ARMS;
    let mut out = String::new();
    let mut scopes: Vec<String> = rows.iter().map(|r| r.set.clone()).collect::<BTreeSet<_>>().into_iter().collect();
    scopes.push("all".to_owned());
    let machine = rows.first().map_or("unknown", |r| r.machine.as_str());
    let _ = writeln!(
        out,
        "# Retrieval ladder (depth {depth}, {} questions with gold, {machine})\n",
        rows.len()
    );
    for scope in &scopes {
        let in_scope: Vec<&LadderRow> = rows.iter().filter(|r| scope == "all" || r.set == *scope).collect();
        let n = in_scope.len();
        if n == 0 {
            continue;
        }
        let in_pool = in_scope.iter().filter(|r| r.gold_in_pool).count();
        let _ = writeln!(
            out,
            "## {scope}: n = {n}, gold in the pool {in_pool}/{n} ({:.0}%) bounds every arm\n",
            100.0 * in_pool as f64 / n as f64
        );
        let _ = writeln!(
            out,
            "| arm | gold at 1 (95% CI) | top 5 | top 10 | top 20 | in today's bytes | in a 40 KB packet (mean size) | MRR | vs store: wins/losses, p | tokens top 10 | tokens top 20 |\n|---|---|---|---|---|---|---|---|---|---|---|"
        );
        let rank_of = |r: &LadderRow, arm: &str| r.ranks.get(arm).copied().flatten().unwrap_or(depth + 1);
        for arm in ARMS {
            let at = |k: usize| in_scope.iter().filter(|r| rank_of(r, arm) <= k).count();
            let (lo, hi) = wilson(at(1), n);
            let mrr: f64 = in_scope.iter().map(|r| 1.0 / rank_of(r, arm) as f64).sum::<f64>() / n as f64;
            let (wins, losses) = in_scope.iter().fold((0, 0), |(w, l), r| {
                let (a, s) = (rank_of(r, arm), rank_of(r, "store"));
                (w + usize::from(a < s), l + usize::from(a > s))
            });
            let tokens = |bytes: &dyn Fn(&LadderRow) -> usize| {
                in_scope.iter().map(|r| bytes(r) as f64).sum::<f64>() / n as f64 / BYTES_PER_TOKEN
            };
            let t10 = tokens(&|r| r.bytes_10.get(arm).copied().unwrap_or(0));
            let t20 = tokens(&|r| r.bytes_20.get(arm).copied().unwrap_or(0));
            let in_budget = in_scope
                .iter()
                .filter(|r| r.in_budget.get(arm).copied().unwrap_or(false))
                .count();
            let in_cap = in_scope
                .iter()
                .filter(|r| r.in_cap.get(arm).copied().unwrap_or(false))
                .count();
            let cap_mean = in_scope.iter().map(|r| r.cap_count.get(arm).copied().unwrap_or(0)).sum::<usize>() as f64
                / n as f64;
            let _ = writeln!(
                out,
                "| {arm} | {}/{n} ({:.0}%, {:.0}-{:.0}) | {} | {} | {} | {in_budget} | {in_cap} ({cap_mean:.1}) | {mrr:.3} | {} | {t10:.0} | {t20:.0} |",
                at(1),
                100.0 * at(1) as f64 / n as f64,
                100.0 * lo,
                100.0 * hi,
                at(5),
                at(10),
                at(20),
                if arm == "store" {
                    "-".to_owned()
                } else {
                    format!("{wins}/{losses}, p = {:.3}", sign_test(wins, wins + losses))
                },
            );
        }
        out.push('\n');
    }
    let _ = writeln!(out, "## Per question (best gold rank per arm; - = not in the pool)\n");
    let _ = writeln!(out, "| id | set | {} |\n|---|---|{}", ARMS.join(" | "), "---|".repeat(ARMS.len()));
    for r in rows {
        let cells: Vec<String> = ARMS
            .iter()
            .map(|a| r.ranks.get(*a).copied().flatten().map_or("-".to_owned(), |k| k.to_string()))
            .collect();
        let _ = writeln!(out, "| {} | {} | {} |", r.id, r.set, cells.join(" | "));
    }
    out
}

#[cfg(test)]
mod ladder_tests {
    use super::sign_test;

    #[test]
    fn the_sign_test_matches_the_textbook() {
        assert!((sign_test(5, 5) - 0.0625).abs() < 1e-9, "{}", sign_test(5, 5));
        assert!((sign_test(0, 5) - 0.0625).abs() < 1e-9);
        assert!((sign_test(1, 10) - 0.021_484_375).abs() < 1e-9, "{}", sign_test(1, 10));
        assert_eq!(sign_test(3, 6), 1.0);
        assert_eq!(sign_test(0, 0), 1.0);
    }
}
