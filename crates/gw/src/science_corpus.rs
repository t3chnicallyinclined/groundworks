use std::collections::{BTreeMap, BTreeSet};

use sha2::{Digest, Sha256};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MediaType {
    Pdf { pages: u32 },
    Video { duration_ms: u64 },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Original {
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
    pub media_type: MediaType,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PageStatus {
    Extracted,
    OcrRequired,
    Retry,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PageExtraction {
    pub source_hash: String,
    pub page: u32,
    pub status: PageStatus,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Locator {
    Page {
        page: u32,
        offset_start: u64,
        offset_end: u64,
    },
    Timestamp {
        start_ms: u64,
        end_ms: u64,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceUnit {
    pub id: String,
    pub source_hash: String,
    pub locator: Locator,
    pub text: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ModelDerivative {
    pub source_unit_ids: Vec<String>,
    pub model: String,
    pub model_version: String,
    pub parameters: BTreeMap<String, String>,
    pub output_hash: String,
    pub authoritative_source: bool,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct CollectionManifest {
    pub originals: Vec<Original>,
    pub pages: Vec<PageExtraction>,
    pub units: Vec<SourceUnit>,
    pub derivatives: Vec<ModelDerivative>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CorpusError(pub String);

impl std::fmt::Display for CorpusError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for CorpusError {}

impl CollectionManifest {
    pub fn unique_source_count(&self) -> usize {
        self.originals
            .iter()
            .map(|original| &original.sha256)
            .collect::<BTreeSet<_>>()
            .len()
    }

    pub fn validate(&self) -> Result<(), CorpusError> {
        let mut originals = BTreeMap::new();
        for original in &self.originals {
            if original.path.is_empty() || !is_sha256(&original.sha256) {
                return Err(CorpusError("original needs a path and SHA-256".into()));
            }
            if originals
                .insert(original.sha256.as_str(), original)
                .is_some()
            {
                continue;
            }
        }

        for original in originals.values() {
            if let MediaType::Pdf { pages } = original.media_type {
                for page in 1..=pages {
                    let found = self.pages.iter().any(|extraction| {
                        extraction.source_hash == original.sha256 && extraction.page == page
                    });
                    if !found {
                        return Err(CorpusError(format!(
                            "source page {page} is missing an extraction status"
                        )));
                    }
                }
            }
        }

        let mut unit_ids = BTreeSet::new();
        for unit in &self.units {
            let original = originals
                .get(unit.source_hash.as_str())
                .ok_or_else(|| CorpusError("unit references an unknown original".into()))?;
            if unit.id.is_empty() || unit.text.is_empty() || !unit_ids.insert(unit.id.as_str()) {
                return Err(CorpusError(
                    "source units need unique IDs and non-empty source text".into(),
                ));
            }
            validate_locator(&unit.locator, &original.media_type)?;
        }

        for derivative in &self.derivatives {
            if derivative.authoritative_source {
                return Err(CorpusError(
                    "model output cannot be an authoritative source".into(),
                ));
            }
            if derivative.source_unit_ids.is_empty()
                || derivative.model.is_empty()
                || derivative.model_version.is_empty()
                || derivative.parameters.is_empty()
                || !is_sha256(&derivative.output_hash)
            {
                return Err(CorpusError("model derivative lacks provenance".into()));
            }
            if derivative
                .source_unit_ids
                .iter()
                .any(|unit_id| !unit_ids.contains(unit_id.as_str()))
            {
                return Err(CorpusError(
                    "model derivative references an unknown source unit".into(),
                ));
            }
        }
        Ok(())
    }

    pub fn verify_original_bytes(
        &self,
        contents: &BTreeMap<String, Vec<u8>>,
    ) -> Result<(), CorpusError> {
        for original in &self.originals {
            let bytes = contents
                .get(&original.path)
                .ok_or_else(|| CorpusError("original bytes are unavailable".into()))?;
            if bytes.len() as u64 != original.bytes || sha256(bytes) != original.sha256 {
                return Err(CorpusError("original hash or byte length changed".into()));
            }
        }
        Ok(())
    }
}

pub fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn validate_locator(locator: &Locator, media_type: &MediaType) -> Result<(), CorpusError> {
    match (locator, media_type) {
        (
            Locator::Page {
                page,
                offset_start,
                offset_end,
            },
            MediaType::Pdf { pages },
        ) if *page > 0 && *page <= *pages && offset_start <= offset_end => Ok(()),
        (Locator::Timestamp { start_ms, end_ms }, MediaType::Video { duration_ms })
            if start_ms <= end_ms && *end_ms <= *duration_ms =>
        {
            Ok(())
        }
        _ => Err(CorpusError("locator is unresolved for its original".into())),
    }
}
