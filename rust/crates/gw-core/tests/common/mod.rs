//! Shared fixture helpers for the config integration tests (`test_config.rs`, `semantics_test.rs`).
//! `minimal()`/`broken(...)` mirror `tests/test_config.py:21-48` (oracle v0.2.0) exactly; a change
//! to the oracle fixture must be mirrored here by hand, same as every other goldens-adjacent
//! fixture in this port.

#![allow(dead_code)]

use gw_core::config;
use serde_json::{json, Value};
use std::path::PathBuf;

pub fn oracle_dir() -> PathBuf {
    PathBuf::from(
        std::env::var("GROUNDWORKS_ORACLE_DIR")
            .unwrap_or_else(|_| "C:/Users/trist/projects/groundworks-oracle".to_string()),
    )
}

pub fn demo_path() -> PathBuf {
    oracle_dir()
        .join("instances")
        .join("demo-incident")
        .join("instance.yaml")
}

pub fn template_path() -> PathBuf {
    oracle_dir()
        .join("instances")
        .join("_template")
        .join("instance.yaml")
}

/// `tests/test_config.py:21-34 MINIMAL`.
pub fn minimal() -> Value {
    json!({
        "instance": {"id": "toy", "goal": "establish the cause", "holder": "team", "off_limits": []},
        "units": [{"name": "line", "locator_fields": ["file", "line"], "chunk_size": 50}],
        "fidelity": {"order": ["source", "extract", "agent"], "confirming": ["source", "extract"],
                     "agent_grades": ["agent"]},
        "status": {"values": ["in", "out"], "usable": ["in"]},
        "vocabulary": {"tiers": [0, 1, 2, 3], "directions": ["for_goal", "against_goal"],
                       "issues": ["one", "two"], "roles": ["note"], "targets": ["t1"]},
        "gates": [{"id": "confirmed_needs_source", "table": "fact", "field": "status", "value": "confirmed",
                   "requires": [{"edge": "cites", "to": ["unit"], "min": 1}]}],
        "evaluator": {"regression_file": "evals/known_errors.json"},
        "human_owned": ["gates", "fidelity", "status", "evaluator", "privacy"],
        "privacy": {"deny_list": "~/.x/deny.txt", "providers_allowed": ["approved"]},
    })
}

/// `tests/test_config.py:37-48 broken(**changes)`: `MINIMAL` with dotted-path overrides, `"__"`
/// joining path segments (matching the oracle's own separator, chosen because `.` appears in real
/// values). `None` marks a delete (the oracle's `config.ConfigError` sentinel).
pub fn broken(changes: &[(&str, Option<Value>)]) -> Value {
    let mut data = minimal();
    for (dotted, value) in changes {
        let parts: Vec<&str> = dotted.split("__").collect();
        let (path, leaf) = parts.split_at(parts.len() - 1);
        let leaf = leaf[0];
        let mut node = &mut data;
        for part in path {
            node = node
                .get_mut(*part)
                .unwrap_or_else(|| panic!("no path segment {part} in {dotted}"));
        }
        let obj = node
            .as_object_mut()
            .unwrap_or_else(|| panic!("{dotted}'s parent is not a mapping"));
        match value {
            None => {
                obj.remove(leaf);
            }
            Some(v) => {
                obj.insert(leaf.to_string(), v.clone());
            }
        }
    }
    data
}

pub fn broken1(dotted: &str, value: Value) -> Value {
    broken(&[(dotted, Some(value))])
}

pub fn broken_del(dotted: &str) -> Value {
    broken(&[(dotted, None)])
}

/// `assertRefused`: `config.load(data)` must raise `ConfigError`, and `phrase` (lowercased) must
/// appear in the lowercased message.
pub fn assert_refused(data: Value, phrase: &str) {
    match config::load_from_mapping(data) {
        Ok(_) => panic!("expected a refusal containing {phrase:?}, but the config was accepted"),
        Err(e) => {
            let msg = e.to_string().to_lowercase();
            assert!(
                msg.contains(&phrase.to_lowercase()),
                "expected {phrase:?} in refusal, got: {msg}"
            );
        }
    }
}

pub fn assert_accepted(data: Value) -> config::Instance {
    config::load_from_mapping(data)
        .unwrap_or_else(|e| panic!("expected ACCEPT, got a refusal: {e}"))
}

pub fn demo_data() -> Value {
    config::load_from_path(&demo_path())
        .expect("demo-incident should load")
        .data
}
