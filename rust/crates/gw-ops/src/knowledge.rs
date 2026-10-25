//! The team's shared knowledge: markdown pages under `knowledge/`, one topic
//! per file. What is specific to this owner, not what any model already knows.

use anyhow::{Context, Result};

use crate::home::{Home, validate_name};

pub struct Page {
    pub topic: String,
    pub title: String,
}

pub fn list(home: &Home) -> Result<Vec<Page>> {
    let mut pages = Vec::new();
    for entry in std::fs::read_dir(home.path("knowledge"))? {
        let path = entry?.path();
        if path.extension().is_some_and(|ext| ext == "md") {
            let text = std::fs::read_to_string(&path)?;
            pages.push(Page {
                topic: path.file_stem().unwrap_or_default().to_string_lossy().into_owned(),
                title: text
                    .lines()
                    .next()
                    .unwrap_or("")
                    .trim_start_matches('#')
                    .trim()
                    .to_owned(),
            });
        }
    }
    pages.sort_by(|a, b| a.topic.cmp(&b.topic));
    Ok(pages)
}

pub fn get(home: &Home, topic: &str) -> Result<String> {
    validate_name(topic)?;
    let path = home.path("knowledge").join(format!("{topic}.md"));
    std::fs::read_to_string(&path).with_context(|| format!("no knowledge page {topic:?} (see knowledge_list)"))
}

/// Case-insensitive line search across every page, as `topic:line: text`.
pub fn search(home: &Home, query: &str, limit: usize) -> Result<Vec<String>> {
    let needle = query.to_lowercase();
    let mut hits = Vec::new();
    for page in list(home)? {
        let text = get(home, &page.topic)?;
        for (n, line) in text.lines().enumerate() {
            if line.to_lowercase().contains(&needle) {
                hits.push(format!("{}:{}: {}", page.topic, n + 1, line.trim()));
                if hits.len() == limit {
                    return Ok(hits);
                }
            }
        }
    }
    Ok(hits)
}
