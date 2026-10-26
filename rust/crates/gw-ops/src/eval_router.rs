//! Router test, offline: before building a cascade (cheap judge first, full agent only when needed),
//! measure whether any judge can tell, from a question and its `ask` candidates alone, that the
//! candidates are enough to answer it. No agent sessions; the right label for every question is known:
//!
//! - `sufficient`: every gold line of the question sits inside one of the candidates `ask` would show;
//! - `insufficient`: a trap (nothing answers it), or some gold is outside the candidates (another
//!   project, or ranked below the cut), so the agent must look further.
//!
//! Routers compared:
//! - `score`: no model; the top candidate's BM25 score, swept over thresholds;
//! - `haiku`: one small-model call that reads the candidates and says sufficient or not, with a
//!   confidence in [0, 1].
//!
//! A router is worth building on only if, when it says sufficient, it is right almost always (the
//! cascade would then answer from the candidates without the agent), on a useful share of questions.

use std::fmt::Write as _;
use std::path::Path;
use std::time::Duration;

use anyhow::Result;
use serde::Serialize;
use serde_json::Value;

use crate::ask::{DEFAULT_K, Query, fan_out};
use crate::eval::{Question, load};
use crate::home::Home;
use crate::store::{Store, numbered};

#[derive(Debug, Serialize)]
pub struct RouterRow {
    pub id: String,
    pub category: String,
    pub sufficient: bool,
    /// Why, when it is not.
    pub why: String,
    pub candidates: usize,
    pub top_score: f64,
    pub haiku_sufficient: Option<bool>,
    pub haiku_confidence: Option<f64>,
    pub haiku_cost_usd: f64,
}

fn judge_prompt(q: &Question, shown: &[String]) -> String {
    format!(
        "You are a router. Below is a question about a software project and the passages a search returned \
         for it. Decide ONLY whether these passages are enough to answer the question correctly and completely \
         as it stands, with no further reading: the answer, every number it needs, and if sources disagree, \
         which is current. If the question's premise is false (nothing here describes the thing it asks about), \
         the passages are NOT enough: that needs a wider look. Reply with ONE JSON object and nothing else: \
         {{\"sufficient\": true|false, \"confidence\": <0 to 1, how sure you are of that verdict>, \
         \"why\": \"one sentence\"}}\n\nQUESTION: {}\n\nPASSAGES:\n{}",
        q.question,
        shown.join("\n\n")
    )
}

/// Run the router test over a set. `haiku` adds the small-model router (one call per question).
pub fn run(home: &Home, store: &Store, set: &Path, haiku: bool, cwd: &Path) -> Result<(String, Vec<RouterRow>)> {
    let mut rows = Vec::new();
    for (_, q) in load(set)? {
        let Ok(q) = q else { continue };
        let found = fan_out(
            home,
            store,
            Query {
                text: &q.question,
                project: Some(&q.project),
                expert: None,
                kinds: &[],
                limit: DEFAULT_K,
            },
        )?;
        let shown: Vec<_> = found.facts.iter().chain(&found.hits).take(DEFAULT_K).collect();
        let mut why = Vec::new();
        if q.category == "trap" {
            why.push("trap: nothing answers it".to_owned());
        }
        for g in &q.gold {
            let repo = g.repo.as_deref().unwrap_or(&q.project);
            let keys = store.units_at(repo, &g.file.replace('\\', "/"), g.line)?;
            let present = shown.iter().any(|h| h.project == repo && keys.contains(&h.key));
            if !present {
                why.push(format!(
                    "{repo}:{}:{} not among the candidates{}",
                    g.file,
                    g.line,
                    if repo != q.project { " (other project)" } else { "" }
                ));
            }
        }
        let sufficient = why.is_empty() && !q.gold.is_empty();
        let top_score = shown.first().and_then(|h| h.score).unwrap_or(0.0);
        let (mut hs, mut hc, mut cost) = (None, None, 0.0);
        if haiku {
            let texts: Vec<String> = shown
                .iter()
                .enumerate()
                .map(|(i, h)| {
                    format!(
                        "[{}] {}:{}\n{}",
                        i + 1,
                        h.project,
                        h.locator,
                        numbered(&h.text, &h.locator)
                    )
                })
                .collect();
            let args: Vec<String> = [
                "--output-format",
                "json",
                "--no-session-persistence",
                "--model",
                "haiku",
                "--settings",
                r#"{"enabledPlugins":{"groundworks@groundworks":false}}"#,
                "--strict-mcp-config",
                "--mcp-config",
                r#"{"mcpServers":{}}"#,
                "--tools",
                "",
            ]
            .iter()
            .map(|s| s.to_string())
            .collect();
            match crate::eval_run::session(cwd, &judge_prompt(&q, &texts), &args, Duration::from_secs(300)) {
                Ok(out) => {
                    cost = out["total_cost_usd"].as_f64().unwrap_or(0.0);
                    let text = out["result"].as_str().unwrap_or_default();
                    if let Some(j) = text.find('{').and_then(|a| text.rfind('}').map(|b| &text[a..=b]))
                        && let Ok(v) = serde_json::from_str::<Value>(j)
                    {
                        hs = v["sufficient"].as_bool();
                        hc = v["confidence"].as_f64();
                    }
                }
                Err(e) => eprintln!("{}: haiku router failed: {e:#}", q.id),
            }
        }
        let row = RouterRow {
            id: q.id.clone(),
            category: q.category.clone(),
            sufficient,
            why: why.join("; "),
            candidates: shown.len(),
            top_score,
            haiku_sufficient: hs,
            haiku_confidence: hc,
            haiku_cost_usd: cost,
        };
        eprintln!(
            "{:<8} label {:<12} top {:>6.2} haiku {:?}@{:?}",
            row.id,
            if sufficient { "sufficient" } else { "insufficient" },
            top_score,
            hs,
            hc
        );
        rows.push(row);
    }
    Ok((report(&rows), rows))
}

/// Precision when the router says sufficient, and how many it covers, per router and threshold.
pub fn report(rows: &[RouterRow]) -> String {
    let mut s = String::new();
    let n = rows.len();
    let truly = rows.iter().filter(|r| r.sufficient).count();
    let _ = writeln!(
        s,
        "{n} questions; {truly} sufficient from the candidates alone, {} must escalate",
        n - truly
    );
    for r in rows.iter().filter(|r| !r.sufficient) {
        let _ = writeln!(s, "  escalate {:<8} {}", r.id, r.why);
    }
    let line = |name: &str, says: &dyn Fn(&RouterRow) -> bool| {
        let said: Vec<&RouterRow> = rows.iter().filter(|r| says(r)).collect();
        let right = said.iter().filter(|r| r.sufficient).count();
        format!(
            "{name:<34} says sufficient on {:>2}/{n}; right {:>2}/{:<2} ({:>3.0}%); misses {} that were sufficient",
            said.len(),
            right,
            said.len(),
            100.0 * right as f64 / said.len().max(1) as f64,
            truly - right
        )
    };
    let _ = writeln!(s, "\n## score router (top BM25 score at or above T)");
    let mut scores: Vec<f64> = rows.iter().map(|r| r.top_score).collect();
    scores.sort_by(f64::total_cmp);
    scores.dedup();
    for t in scores.iter().step_by((scores.len() / 8).max(1)) {
        let _ = writeln!(s, "{}", line(&format!("T = {t:.2}"), &|r| r.top_score >= *t));
    }
    if rows.iter().any(|r| r.haiku_sufficient.is_some()) {
        let _ = writeln!(s, "\n## haiku router");
        let _ = writeln!(
            s,
            "{}",
            line("says sufficient, any confidence", &|r| r.haiku_sufficient == Some(true))
        );
        for c in [0.7, 0.8, 0.9, 0.95] {
            let _ = writeln!(
                s,
                "{}",
                line(&format!("says sufficient, confidence >= {c}"), &|r| {
                    r.haiku_sufficient == Some(true) && r.haiku_confidence.is_some_and(|x| x >= c)
                })
            );
        }
        let wrong: Vec<String> = rows
            .iter()
            .filter(|r| r.haiku_sufficient == Some(true) && !r.sufficient)
            .map(|r| format!("{}@{:.2}", r.id, r.haiku_confidence.unwrap_or(0.0)))
            .collect();
        let _ = writeln!(
            s,
            "wrongly waved through: {}",
            if wrong.is_empty() {
                "none".into()
            } else {
                wrong.join(", ")
            }
        );
        let _ = writeln!(
            s,
            "haiku cost: ${:.2}",
            rows.iter().map(|r| r.haiku_cost_usd).sum::<f64>()
        );
    }
    s
}
