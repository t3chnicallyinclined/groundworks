//! Each project's journal: decisions, gotchas, status, todos and facts the team
//! learned, one JSON line each in `journal/<project>.jsonl`. Append-only.

use std::io::Write as _;

use anyhow::{Context, Result, ensure};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::home::{Home, machine};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, clap::ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Decision,
    Gotcha,
    Status,
    Todo,
    Fact,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, clap::ValueEnum)]
#[serde(rename_all = "UPPERCASE")]
pub enum Confidence {
    Confirmed,
    Inferred,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Entry {
    pub ts: String,
    pub project: String,
    pub kind: Kind,
    pub text: String,
    /// file:line, a command and its output, or a URL.
    pub source: String,
    pub confidence: Confidence,
    /// Which team member wrote it.
    pub by: String,
    pub machine: String,
}

impl Entry {
    pub fn new(project: &str, kind: Kind, text: &str, source: &str, confidence: Confidence, by: &str) -> Result<Self> {
        ensure!(!text.trim().is_empty(), "a journal entry needs text");
        ensure!(
            confidence != Confidence::Confirmed || !source.trim().is_empty(),
            "a CONFIRMED entry needs a source (file:line, command output or URL); record it as INFERRED otherwise"
        );
        Ok(Self {
            ts: chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true),
            project: project.to_owned(),
            kind,
            text: text.trim().to_owned(),
            source: source.trim().to_owned(),
            confidence,
            by: by.trim().to_owned(),
            machine: machine(),
        })
    }

    pub fn render(&self) -> String {
        let kind = serde_json::to_value(self.kind).unwrap_or_default();
        let confidence = serde_json::to_value(self.confidence).unwrap_or_default();
        let source = if self.source.is_empty() {
            "no source"
        } else {
            &self.source
        };
        format!(
            "{} [{}] {} {} ({}; by {} on {})",
            self.ts,
            kind.as_str().unwrap_or("?"),
            confidence.as_str().unwrap_or("?"),
            self.text,
            source,
            self.by,
            self.machine
        )
    }
}

pub fn append(home: &Home, entry: &Entry) -> Result<()> {
    let dir = home.path("journal");
    std::fs::create_dir_all(&dir)?;
    let path = dir.join(format!("{}.jsonl", entry.project));
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .with_context(|| format!("opening {}", path.display()))?;
    writeln!(file, "{}", serde_json::to_string(entry)?)?;
    Ok(())
}

/// The newest `limit` entries, oldest first.
pub fn read(home: &Home, project: &str, kind: Option<Kind>, limit: usize) -> Result<Vec<Entry>> {
    let path = home.path("journal").join(format!("{project}.jsonl"));
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e).with_context(|| format!("reading {}", path.display())),
    };
    let mut entries = Vec::new();
    for (n, line) in text.lines().enumerate().filter(|(_, line)| !line.trim().is_empty()) {
        let entry: Entry = serde_json::from_str(line)
            .with_context(|| format!("{}:{} is not a journal entry", path.display(), n + 1))?;
        if kind.is_none_or(|k| k == entry.kind) {
            entries.push(entry);
        }
    }
    let skip = entries.len().saturating_sub(limit);
    Ok(entries.split_off(skip))
}
