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
