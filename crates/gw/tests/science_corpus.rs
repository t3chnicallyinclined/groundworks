use std::collections::BTreeMap;

use gw::science_corpus::{
    sha256, CollectionManifest, Locator, MediaType, ModelDerivative, Original, PageExtraction,
    PageStatus, SourceUnit,
};

fn original(path: &str, bytes: &[u8], media_type: MediaType) -> Original {
    Original {
        path: path.into(),
        sha256: sha256(bytes),
        bytes: bytes.len() as u64,
        media_type,
    }
}

fn manifest() -> CollectionManifest {
    let born_digital = b"born-digital";
    let scanned = b"scanned";
    let video = b"video";
    CollectionManifest {
        originals: vec![
            original(
                "born-digital.pdf",
                born_digital,
                MediaType::Pdf { pages: 1 },
            ),
            original("scanned.pdf", scanned, MediaType::Pdf { pages: 2 }),
            original("duplicate.pdf", born_digital, MediaType::Pdf { pages: 1 }),
            original(
                "experiment.mp4",
                video,
                MediaType::Video { duration_ms: 1_000 },
            ),
        ],
        pages: vec![
            PageExtraction {
                source_hash: sha256(born_digital),
                page: 1,
                status: PageStatus::Extracted,
            },
            PageExtraction {
                source_hash: sha256(scanned),
                page: 1,
                status: PageStatus::OcrRequired,
            },
            PageExtraction {
                source_hash: sha256(scanned),
                page: 2,
                status: PageStatus::Retry,
            },
        ],
        units: vec![
            SourceUnit {
                id: "unit:born:1".into(),
                source_hash: sha256(born_digital),
                locator: Locator::Page {
                    page: 1,
                    offset_start: 0,
                    offset_end: 12,
                },
                text: "born source text".into(),
            },
            SourceUnit {
                id: "unit:video:0".into(),
                source_hash: sha256(video),
                locator: Locator::Timestamp {
                    start_ms: 100,
                    end_ms: 900,
                },
                text: "video transcript text".into(),
            },
        ],
        derivatives: vec![ModelDerivative {
            source_unit_ids: vec!["unit:born:1".into()],
            model: "local-test-model".into(),
            model_version: "1".into(),
            parameters: BTreeMap::from([("dimensions".into(), "8".into())]),
            output_hash: sha256(b"embedding"),
            authoritative_source: false,
        }],
    }
}

#[test]
fn synthetic_fixture_preserves_sources_and_derivative_provenance() {
    let manifest = manifest();
    manifest.validate().unwrap();
    assert_eq!(
        manifest.unique_source_count(),
        3,
        "duplicate bytes are aliases, not a new source"
    );

    let contents = BTreeMap::from([
        ("born-digital.pdf".into(), b"born-digital".to_vec()),
        ("scanned.pdf".into(), b"scanned".to_vec()),
        ("duplicate.pdf".into(), b"born-digital".to_vec()),
        ("experiment.mp4".into(), b"video".to_vec()),
    ]);
    manifest.verify_original_bytes(&contents).unwrap();
}

#[test]
fn rejects_changed_originals_missing_pages_bad_locators_and_unprovenanced_models() {
    let manifest = manifest();
    let changed = BTreeMap::from([
        ("born-digital.pdf".into(), b"changed".to_vec()),
        ("scanned.pdf".into(), b"scanned".to_vec()),
        ("duplicate.pdf".into(), b"born-digital".to_vec()),
        ("experiment.mp4".into(), b"video".to_vec()),
    ]);
    assert!(manifest.verify_original_bytes(&changed).is_err());

    let mut missing_page = manifest.clone();
    missing_page.pages.pop();
    assert!(missing_page.validate().is_err());

    let mut bad_locator = manifest.clone();
    bad_locator.units[1].locator = Locator::Timestamp {
        start_ms: 900,
        end_ms: 1_100,
    };
    assert!(bad_locator.validate().is_err());

    let mut missing_provenance = manifest.clone();
    missing_provenance.derivatives[0].parameters.clear();
    assert!(missing_provenance.validate().is_err());

    let mut missing_model_version = manifest.clone();
    missing_model_version.derivatives[0].model_version.clear();
    assert!(missing_model_version.validate().is_err());

    let mut authoritative_model = manifest;
    authoritative_model.derivatives[0].authoritative_source = true;
    assert!(authoritative_model.validate().is_err());
}
