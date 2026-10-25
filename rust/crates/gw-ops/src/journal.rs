//! What a journal entry is: its kind and how sure it is. Entries live in the store as facts (see
//! `store.rs`). The old append-only files (`journal/<project>.jsonl`) are read once, by
//! `import`, and then renamed so nothing reads them again.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// Work items (todos) are not here: they live in GitHub issues.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, clap::ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Decision,
    Gotcha,
    Status,
    Fact,
    /// An approach that was tried and ruled out, with what ruled it out.
    Eliminated,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, clap::ValueEnum)]
#[serde(rename_all = "UPPERCASE")]
pub enum Confidence {
    Confirmed,
    Inferred,
    Unknown,
}

/// One line of an old file journal, kept loose so every historical entry can be read.
#[derive(Debug, Clone, Deserialize)]
pub struct LegacyEntry {
    pub ts: String,
    pub kind: String,
    pub text: String,
    #[serde(default)]
    pub source: String,
    pub confidence: String,
    #[serde(default)]
    pub by: String,
    #[serde(default)]
    pub machine: String,
}

impl LegacyEntry {
    /// The store kind for an old entry; a `todo` becomes a status line saying so.
    pub fn kind_and_text(&self) -> (Kind, String) {
        match self.kind.as_str() {
            "decision" => (Kind::Decision, self.text.clone()),
            "gotcha" => (Kind::Gotcha, self.text.clone()),
            "status" => (Kind::Status, self.text.clone()),
            "todo" => (Kind::Status, format!("TODO (belongs in GitHub issues): {}", self.text)),
            _ => (Kind::Fact, self.text.clone()),
        }
    }
}

pub fn legacy_path(home_root: &Path, project: &str) -> PathBuf {
    home_root.join("journal").join(format!("{project}.jsonl"))
}

/// Every entry of an old file journal, oldest first; an empty list if there is none.
pub fn read_legacy(path: &Path) -> Result<Vec<LegacyEntry>> {
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e).with_context(|| format!("reading {}", path.display())),
    };
    text.lines()
        .enumerate()
        .filter(|(_, line)| !line.trim().is_empty())
        .map(|(n, line)| {
            serde_json::from_str(line).with_context(|| format!("{}:{} is not a journal entry", path.display(), n + 1))
        })
        .collect()
}
