//! Pulse: a few numbers, taken from the store with no model, that say whether the knowledge is growing,
//! being used, and holding up. One line is appended to `<home>/metrics/pulse.jsonl` per run, so the trend
//! is a file anyone can read; `history` prints it.
//!
//! What "smarter" looks like in these numbers, over weeks:
//! - **stock**: live facts rise; the share CONFIRMED (receipt, quote or owner statement) rises; attacks
//!   (disputes, supports) appear, so disagreements are kept rather than lost.
//! - **use**: asks are answered more often with a recorded fact among the candidates ("served from
//!   knowledge"), and more passed answers are recorded; checks fail less.
//! - **health**: stale facts (their source line changed) get re-confirmed rather than piling up; units
//!   marked gone are few.
//! - **compaction**: facts per thousand units rises: more of what the files say is distilled into checked,
//!   citable statements.

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::io::Write as _;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

use crate::home::Home;
use crate::registry::Registry;
use crate::store::Store;

/// The window for use numbers (asks, answers, recordings), in days.
pub const WINDOW_DAYS: u64 = 7;

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct ProjectPulse {
    pub units: u64,
    pub units_gone: u64,
    pub facts_live: u64,
    pub facts_stale: u64,
    pub facts_confirmed: u64,
    /// Facts whose source is an ask packet: answers kept as knowledge.
    pub facts_from_asks: u64,
    pub attacks: u64,
    pub evidence_links: u64,
}

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct Pulse {
    pub at: String,
    pub machine: String,
    pub projects: u64,
    pub totals: ProjectPulse,
    pub per_project: BTreeMap<String, ProjectPulse>,
    /// Asks in the window, eval sessions left out.
    pub asks: u64,
    /// Asks by eval sessions (recording off; `by` starts with `eval:`), counted apart so the use
    /// numbers say what real sessions did. Measured 2026-10-29: in the first week every passed ask
    /// in the store came from eval runs, and the pulse could not tell.
    #[serde(default)]
    pub asks_eval: u64,
    pub asks_passed: u64,
    pub asks_failed: u64,
    pub asks_not_executed: u64,
    /// Asks whose packet showed at least one recorded fact.
    pub asks_served_from_knowledge: u64,
    /// Facts recorded from asks in the window.
    pub recorded_in_window: u64,
    /// The newest finished export's day (`YYYY-MM-DD`), if any.
    #[serde(default)]
    pub last_export: Option<String>,
    /// That export's restore drill: `PASS`, `FAIL`, or `not drilled`.
    #[serde(default)]
    pub last_drill: Option<String>,
}

impl Pulse {
    /// Live facts per thousand present units: how much of the files is distilled.
    pub fn compaction(&self) -> f64 {
        if self.totals.units == 0 {
            0.0
        } else {
            1000.0 * self.totals.facts_live as f64 / self.totals.units as f64
        }
    }
}

/// Take the pulse of every registered project the store can read, and of asks in the window.
pub fn take(home: &Home, store: &Store) -> Result<Pulse> {
    let registry = Registry::load(home)?;
    let mut pulse = Pulse {
        at: now_iso(),
        machine: crate::home::machine(),
        ..Pulse::default()
    };
    for name in registry.projects.keys() {
        let Ok(p) = store.pulse_project(name) else { continue };
        let t = &mut pulse.totals;
        t.units += p.units;
        t.units_gone += p.units_gone;
        t.facts_live += p.facts_live;
        t.facts_stale += p.facts_stale;
        t.facts_confirmed += p.facts_confirmed;
        t.facts_from_asks += p.facts_from_asks;
        t.attacks += p.attacks;
        t.evidence_links += p.evidence_links;
        pulse.recorded_in_window += store.facts_from_asks_since(name, WINDOW_DAYS).unwrap_or(0);
        pulse.per_project.insert(name.clone(), p);
        pulse.projects += 1;
    }
    let (last_export, last_drill) = exports_status(home);
    pulse.last_export = last_export;
    pulse.last_drill = last_drill;
    let asks = store.ask_packets_since(WINDOW_DAYS)?;
    for row in &asks {
        if row["by"].as_str().is_some_and(|b| b.starts_with("eval:")) {
            pulse.asks_eval += 1;
            continue;
        }
        pulse.asks += 1;
        match row["status"].as_str().unwrap_or("") {
            "PASS" => pulse.asks_passed += 1,
            "FAIL" => pulse.asks_failed += 1,
            "NOT_EXECUTED" => pulse.asks_not_executed += 1,
            _ => {}
        }
        let served = row["candidates"]
            .as_array()
            .is_some_and(|cs| cs.iter().any(|c| c.get("fact").is_some_and(|f| !f.is_null())));
        pulse.asks_served_from_knowledge += u64::from(served);
    }
    Ok(pulse)
}

/// The newest finished export under the home's backups, and the verdict of its restore drill (the
/// first word of its DRILL file), so a pulse says whether the data could be got back.
fn exports_status(home: &Home) -> (Option<String>, Option<String>) {
    let dir = crate::supervise::backups_dir(home);
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return (None, None);
    };
    let mut days: Vec<std::path::PathBuf> = entries
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| {
            p.is_dir() && p.file_name().is_some_and(|n| n.len() == 10) && p.join(crate::supervise::DONE_FILE).is_file()
        })
        .collect();
    days.sort();
    let Some(newest) = days.pop() else {
        return (None, None);
    };
    let day = newest.file_name().map(|n| n.to_string_lossy().into_owned());
    let drill = std::fs::read_to_string(newest.join(crate::supervise::DRILL_FILE))
        .ok()
        .and_then(|t| t.split_whitespace().next().map(str::to_owned))
        .unwrap_or_else(|| "not drilled".to_owned());
    (day, Some(drill))
}

/// Append the pulse to `metrics/pulse.jsonl` in the home; returns the path.
pub fn record(home: &Home, pulse: &Pulse) -> Result<std::path::PathBuf> {
    let dir = home.root.join("metrics");
    std::fs::create_dir_all(&dir)?;
    let path = dir.join("pulse.jsonl");
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&path)?;
    writeln!(f, "{}", serde_json::to_string(pulse)?)?;
    Ok(path)
}

/// The pulse as a reader sees it.
pub fn render(p: &Pulse) -> String {
    let t = &p.totals;
    let pct = |a: u64, b: u64| if b == 0 { 0.0 } else { 100.0 * a as f64 / b as f64 };
    let mut s = format!(
        "pulse {} on {} | {} projects\n\
         stock   units {} ({} gone) | facts live {} (CONFIRMED {:.0}%), stale {} | from asks {} | attacks {} | evidence links {}\n\
         compaction  {:.1} live facts per 1000 units\n\
         use ({}d)  asks {} (eval sessions apart: {}) | passed {} | failed {} | not executed {} | served from knowledge {} ({:.0}%) | answers recorded {}\n\
         ops     last export {} | restore drill {}\n",
        &p.at[..p.at.len().min(19)],
        p.machine,
        p.projects,
        t.units,
        t.units_gone,
        t.facts_live,
        pct(t.facts_confirmed, t.facts_live + t.facts_stale),
        t.facts_stale,
        t.facts_from_asks,
        t.attacks,
        t.evidence_links,
        p.compaction(),
        WINDOW_DAYS,
        p.asks,
        p.asks_eval,
        p.asks_passed,
        p.asks_failed,
        p.asks_not_executed,
        p.asks_served_from_knowledge,
        pct(p.asks_served_from_knowledge, p.asks),
        p.recorded_in_window,
        p.last_export.as_deref().unwrap_or("none"),
        p.last_drill.as_deref().unwrap_or("none")
    );
    let mut rows: Vec<(&String, &ProjectPulse)> = p.per_project.iter().collect();
    rows.sort_by(|a, b| (b.1.facts_live + b.1.facts_stale).cmp(&(a.1.facts_live + a.1.facts_stale)));
    for (name, pp) in rows.iter().take(8) {
        let _ = writeln!(
            s,
            "  {:<22} units {:>6} | facts {:>4} live, {:>3} stale, {:>3} from asks | attacks {}",
            name, pp.units, pp.facts_live, pp.facts_stale, pp.facts_from_asks, pp.attacks
        );
    }
    s
}

/// The trend: one line per recorded pulse, newest last.
pub fn history(home: &Home) -> Result<String> {
    let path = home.root.join("metrics").join("pulse.jsonl");
    let text = std::fs::read_to_string(&path).with_context(|| format!("no pulse yet at {}", path.display()))?;
    let mut s = String::from(
        "when                 units  facts  stale  from-asks  attacks  compaction  asks  served%  recorded  drill\n",
    );
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        let Ok(p) = serde_json::from_str::<Pulse>(line) else {
            continue;
        };
        let served = if p.asks == 0 {
            0.0
        } else {
            100.0 * p.asks_served_from_knowledge as f64 / p.asks as f64
        };
        let _ = writeln!(
            s,
            "{:<20} {:>6} {:>6} {:>6} {:>10} {:>8} {:>11.1} {:>5} {:>7.0} {:>9}  {}",
            &p.at[..p.at.len().min(19)],
            p.totals.units,
            p.totals.facts_live,
            p.totals.facts_stale,
            p.totals.facts_from_asks,
            p.totals.attacks,
            p.compaction(),
            p.asks,
            served,
            p.recorded_in_window,
            p.last_drill.as_deref().unwrap_or("-")
        );
    }
    Ok(s)
}

fn now_iso() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs()) as i64;
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compaction_is_live_facts_per_thousand_units_and_render_holds_the_numbers() {
        let mut p = Pulse::default();
        p.totals.units = 4000;
        p.totals.facts_live = 10;
        p.totals.facts_stale = 2;
        p.totals.facts_confirmed = 6;
        p.asks = 4;
        p.asks_served_from_knowledge = 3;
        assert!((p.compaction() - 2.5).abs() < 1e-9);
        let text = render(&p);
        assert!(text.contains("2.5 live facts per 1000 units"), "{text}");
        assert!(text.contains("served from knowledge 3 (75%)"), "{text}");
        assert!(text.contains("CONFIRMED 50%"), "{text}");
        assert_eq!(Pulse::default().compaction(), 0.0);
    }
}
