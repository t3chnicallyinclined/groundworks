//! Lenses: an expert's view of a project. Ported in behaviour from the Python engine's `lens.py`
//! (tag v0.2.0): a lens has a charter, a standard (never looser than the store's rules), and reading
//! through it shows what passes the standard and, as Python's `drift`, what no longer does.
//!
//! Stated deviations: a lens is an expert definition (`~/.claude/agents/*.md`, and in its own project a
//! project's `.claude/agents/*.md`, since 2026-10-30), not an instance-config
//! entry, so every expert is a lens and there is one list of them. Its checks are the bold lead words of
//! the bullets under `## What you check`; its standard is the frontmatter `lens-standard:` (CONFIRMED, or
//! the default INFERRED: CONFIRMED or INFERRED, and live). Findings are facts that name the lens, and
//! another lens attacks one with a fact that disputes or supports it; nothing is deleted, so the
//! disagreement is kept. Python's datastore views over labels and its framework crosswalk are not ported:
//! the Rust store has no labels yet.

use std::fmt::Write as _;

use anyhow::{Result, bail};

use crate::home::Home;
use crate::intake::Expert;
use crate::registry::Registry;
use crate::store::{FactRow, Store};

pub struct Lens {
    pub name: String,
    pub summary: String,
    pub checks: Vec<String>,
    /// "CONFIRMED" or "INFERRED".
    pub standard: &'static str,
    /// Where the expert is defined: `global` or `project:<name>`.
    pub source: String,
}

impl Lens {
    fn from_expert(e: &Expert) -> Self {
        let mut checks = Vec::new();
        let mut inside = false;
        for line in e.text.lines() {
            if line.starts_with("## ") {
                inside = line
                    .trim_start_matches('#')
                    .trim()
                    .eq_ignore_ascii_case("what you check");
                continue;
            }
            if let Some(term) = inside
                .then(|| line.trim_start().strip_prefix("- **"))
                .flatten()
                .and_then(|rest| rest.split_once("**"))
                .map(|(t, _)| t.trim().trim_end_matches(['.', ':']).trim())
                .filter(|t| !t.is_empty())
            {
                checks.push(term.to_owned());
            }
        }
        let standard = if frontmatter(&e.text, "lens-standard:").is_some_and(|s| s.eq_ignore_ascii_case("CONFIRMED")) {
            "CONFIRMED"
        } else {
            "INFERRED"
        };
        Lens {
            name: e.name.clone(),
            summary: e.summary.clone(),
            checks,
            standard,
            source: e.source.clone(),
        }
    }

    /// What a finding must be to count through this lens.
    pub fn passes(&self, f: &FactRow) -> bool {
        f.status == "live" && (f.grade == "CONFIRMED" || (self.standard == "INFERRED" && f.grade == "INFERRED"))
    }

    /// The question a lens asks of a project when none is given: its checks, or its summary.
    pub fn question(&self) -> String {
        if self.checks.is_empty() {
            self.summary.clone()
        } else {
            self.checks.join("; ")
        }
    }
}

fn frontmatter<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    let front = text.strip_prefix("---")?.split_once("\n---")?.0;
    front
        .lines()
        .find_map(|l| l.trim().strip_prefix(key))
        .map(|v| v.trim().trim_matches('"'))
}

/// The lenses visible in `project` (its own experts and the global ones, [`crate::ops::visible_experts`]),
/// or the global ones when no project is named.
pub fn all(home: &Home, project: Option<&str>) -> Result<Vec<Lens>> {
    Ok(crate::ops::visible_experts(home, project)?
        .0
        .iter()
        .map(Lens::from_expert)
        .collect())
}

pub fn find(home: &Home, project: Option<&str>, name: &str) -> Result<Lens> {
    match all(home, project)?.into_iter().find(|l| l.name == name) {
        Some(l) => Ok(l),
        None => match project {
            Some(p) => bail!(
                "no lens {name:?} in {p}: its lenses are its own experts and the global ones (expert_list project={p})"
            ),
            None => bail!(
                "no lens {name:?}: every global expert is a lens (expert_list; a project's own experts need its project)"
            ),
        },
    }
}

/// Every lens visible in `project` (or every global one), with its checks, standard and source.
pub fn list(home: &Home, project: Option<&str>) -> Result<String> {
    let lenses = all(home, project)?;
    if lenses.is_empty() {
        return Ok("no lenses: no expert definitions were found".into());
    }
    let mut out = String::new();
    for l in &lenses {
        writeln!(
            out,
            "- {} (standard {}{}): checks {} [{}]",
            l.name,
            l.standard,
            if l.standard == "INFERRED" { " or CONFIRMED" } else { "" },
            if l.checks.is_empty() {
                "(none listed; asks its summary)".to_owned()
            } else {
                l.checks.join("; ")
            },
            l.source
        )?;
    }
    Ok(out)
}

/// Read a project through one lens: its findings that pass the standard, the ones that no longer do,
/// and every attack on them, so a disagreement is shown, never resolved by omission.
pub fn view(home: &Home, store: &Store, project: &str, name: &str, limit: usize) -> Result<String> {
    let project = Registry::load(home)?.project(project)?.name.clone();
    let lens = find(home, Some(&project), name)?;
    let rows = store.lens_findings(&project, &lens.name, limit.clamp(1, 500))?;
    let (findings, attacks): (Vec<&FactRow>, Vec<&FactRow>) = rows
        .iter()
        .partition(|f| f.lens.as_deref() == Some(lens.name.as_str()) && f.attacks.is_none());
    let usable = findings.iter().filter(|f| lens.passes(f)).count();
    let mut out = format!(
        "lens {} on {project}: {}\nchecks: {}\nstandard: {}, live\nfindings: {} ({usable} pass the standard, {} do not)\n",
        lens.name,
        lens.summary,
        if lens.checks.is_empty() {
            "(none listed)".to_owned()
        } else {
            lens.checks.join("; ")
        },
        if lens.standard == "CONFIRMED" {
            "CONFIRMED only"
        } else {
            "CONFIRMED or INFERRED"
        },
        findings.len(),
        findings.len() - usable
    );
    for f in &findings {
        let mark = if lens.passes(f) { "-" } else { "- (DOES NOT PASS)" };
        writeln!(out, "{mark} {}", f.render())?;
        let id = f.id.as_str();
        for a in attacks.iter().filter(|a| a.attacks.as_deref() == Some(id)) {
            writeln!(
                out,
                "    {} by {}: {}",
                a.stance.as_deref().unwrap_or("attacked").to_uppercase(),
                a.lens.as_deref().or(a.by.as_deref()).unwrap_or("?"),
                a.render()
            )?;
        }
    }
    let shown: Vec<&str> = findings.iter().map(|f| f.id.as_str()).collect();
    for a in attacks.iter().filter(|a| a.lens.as_deref() == Some(lens.name.as_str())) {
        if !a.attacks.as_deref().is_some_and(|t| shown.contains(&t)) {
            writeln!(
                out,
                "- this lens {} {}: {}",
                a.stance.as_deref().unwrap_or("attacks"),
                a.attacks.as_deref().unwrap_or("?"),
                a.render()
            )?;
        }
    }
    if findings.is_empty() {
        writeln!(
            out,
            "No findings yet. Read the project through this lens with ask (lens = {}), then record each finding \
             with journal_add (lens = {}); dispute or support another lens's finding with attacks and stance.",
            lens.name, lens.name
        )?;
    }
    Ok(out)
}

/// Open an ask packet through a lens: its question (or the lens's checks), tagged with the lens, followed by
/// what the lens has already found there, so a reader attacks or adds rather than repeats.
pub fn ask(home: &Home, store: &Store, lens: &str, question: Option<&str>, q: crate::ask::Query<'_>) -> Result<String> {
    let l = find(home, q.project, lens)?;
    let asked = question
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .map_or_else(|| l.question(), str::to_owned);
    let mut out = crate::ask::open(
        home,
        store,
        crate::ask::Query { text: &asked, ..q },
        &format!("lens:{}", l.name),
    )?;
    if let Some(project) = q.project {
        let project = Registry::load(home)?.project(project)?.name.clone();
        let found = store.lens_findings(&project, &l.name, 50)?;
        writeln!(
            out,
            "
Through lens {} (standard {}): {} finding(s) and attack(s) already on record in {project}.",
            l.name,
            l.standard,
            found.len()
        )?;
        for f in &found {
            writeln!(out, "- {}", f.render())?;
        }
    }
    writeln!(
        out,
        "Record each finding with journal_add (lens = {}); to dispute or support a finding of another lens, add          attacks = its fact id and stance = disputes|supports. Nothing is deleted: a disagreement stays on record.",
        l.name
    )?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn expert(text: &str) -> Expert {
        Expert {
            name: "gw-security".into(),
            summary: "Security engineer.".into(),
            file: "gw-security.md".into(),
            text: text.into(),
            source: "global".into(),
        }
    }

    fn row(grade: &str, status: &str) -> FactRow {
        serde_json::from_value(
            serde_json::json!({"id": "fact:a", "at": "t", "kind": "fact", "text": "x",
            "grade": grade, "status": status}),
        )
        .unwrap()
    }

    #[test]
    fn checks_are_the_bold_lead_words_of_what_you_check() {
        let l = Lens::from_expert(&expert(
            "---\nname: gw-security\nlens-standard: CONFIRMED\n---\n## Before\n- **Not this.** x\n\
             ## What you check\n- **Secrets custody.** Every secret...\n  more\n- **Auth.** Who...\n## Findings\n- **No.**",
        ));
        assert_eq!(l.checks, ["Secrets custody", "Auth"]);
        assert_eq!(l.question(), "Secrets custody; Auth");
        assert_eq!(l.standard, "CONFIRMED");
    }

    #[test]
    fn the_standard_is_never_looser_than_the_store_and_stale_never_passes() {
        let default = Lens::from_expert(&expert("no frontmatter"));
        assert_eq!(
            (default.standard, default.question()),
            ("INFERRED", "Security engineer.".to_owned())
        );
        assert!(default.passes(&row("INFERRED", "live")));
        assert!(!default.passes(&row("UNKNOWN", "live")));
        assert!(!default.passes(&row("CONFIRMED", "stale")));
        let strict = Lens::from_expert(&expert("---\nlens-standard: confirmed\n---\n"));
        assert!(!strict.passes(&row("INFERRED", "live")));
        assert!(strict.passes(&row("CONFIRMED", "live")));
    }
}
