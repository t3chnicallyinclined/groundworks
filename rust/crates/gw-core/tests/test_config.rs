//! Ported from `tests/test_config.py` (oracle v0.2.0, `groundworks/config.py`'s own test suite),
//! same accept/refuse verdict and the same phrase check the oracle's `assertRefused` does
//! (`phrase in str(exception).lower()`). `Done when` bullet 2 of the port-01 ticket: every
//! `tests/test_config.py` case here, not just the two YAML ones (`config::yaml::tests`).
//!
//! `MINIMAL` and `broken(...)` mirror `tests/test_config.py:21-48` exactly (same keys, same
//! values); a change to the oracle fixture must be mirrored here by hand, same as every other
//! goldens-adjacent fixture in this port.

mod common;

use common::*;
use gw_core::config;
use serde_json::{json, Value};

// ---- YamlSubsetTests (already covered by config::yaml::tests, kept here too as the direct port) ----

#[test]
fn test_parses_maps_lists_flows_comments_and_quotes() {
    let text = concat!(
        "# a comment\n",
        "a: 1\n",
        "b: text with spaces   # trailing comment\n",
        "c: \"quoted: colon\"\n",
        "d: [x, y, 3]\n",
        "e: {k: v, n: [1, 2]}\n",
        "f:\n",
        "  g: true\n",
        "  h: null\n",
        "items:\n",
        "  - name: one\n",
        "    size: 2\n",
        "  - name: two\n",
        "    nested:\n",
        "      deep: 3.5\n",
        "plain:\n",
        "  - a\n",
        "  - b\n",
    );
    let got = config::parse_yaml(text).unwrap();
    assert_eq!(got["a"], 1);
    assert_eq!(got["b"], "text with spaces");
    assert_eq!(got["c"], "quoted: colon");
    assert_eq!(got["d"], json!(["x", "y", 3]));
    assert_eq!(got["e"], json!({"k": "v", "n": [1, 2]}));
    assert_eq!(got["f"], json!({"g": true, "h": null}));
    assert_eq!(got["items"][0], json!({"name": "one", "size": 2}));
    assert_eq!(got["items"][1]["nested"], json!({"deep": 3.5}));
    assert_eq!(got["plain"], json!(["a", "b"]));
}

#[test]
fn test_rejects_what_it_cannot_parse() {
    for text in [
        "a:\n\tb: 1\n",
        "a: &anchor 1\n",
        "a: |\n  block\n",
        "a: 1\na: 2\n",
        "a: 1\n   b: 2\n",
        "no colon here\n",
    ] {
        assert!(
            config::parse_yaml(text).is_err(),
            "expected an error for {text:?}"
        );
    }
}

// ---- DemoInstanceTests -------------------------------------------------------------------------

#[test]
fn test_demo_instance_loads_and_answers_the_eight_questions() {
    let inst =
        config::load_from_path(&demo_path()).expect("demo-incident/instance.yaml should load");
    assert_eq!(inst.id, "demo_incident");
    let confirming: Vec<String> = inst
        .confirming_grades()
        .iter()
        .map(config::py_str_of)
        .collect();
    assert!(confirming.contains(&"original_log".to_string()));
    assert!(!confirming.contains(&"agent_summary".to_string()));
    let usable: Vec<String> = inst
        .usable_statuses()
        .iter()
        .map(config::py_str_of)
        .collect();
    assert_eq!(usable, vec!["in_window".to_string()]);
    let mut names = inst.unit_names();
    names.sort();
    assert_eq!(
        names,
        vec![
            "change".to_string(),
            "log_line".to_string(),
            "metric_point".to_string()
        ]
    );
    let log_line = inst.unit_type("log_line").expect("log_line unit type");
    assert_eq!(log_line["chunk_size"], 200);
    assert_eq!(inst.gate_rules().len(), 3);
    let mut edges: Vec<String> = inst.declared_edges().keys().cloned().collect();
    edges.sort();
    assert_eq!(
        edges,
        vec!["preserves".to_string(), "verified_from".to_string()]
    );
    assert_eq!(inst.edge_map()["preserves"], json!(["unit", "conclusion"]));
    let vocab = inst.allowed_vocabulary();
    let issues: Vec<String> = vocab["issues"]
        .as_array()
        .unwrap()
        .iter()
        .map(config::py_str_of)
        .collect();
    assert!(issues.contains(&"saturation".to_string()));
    let grades: Vec<String> = vocab["grades"]
        .as_array()
        .unwrap()
        .iter()
        .map(config::py_str_of)
        .collect();
    assert!(grades.contains(&"original_log".to_string()));
}

#[test]
fn test_json_config_loads_too() {
    let dir = std::env::temp_dir().join(format!("gw_config_json_test_{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join("evals")).unwrap();
    std::fs::write(
        dir.join("instance.json"),
        serde_json::to_string(&minimal()).unwrap(),
    )
    .unwrap();
    std::fs::write(
        dir.join("evals").join("known_errors.json"),
        r#"{"format": "groundworks.known_errors/1", "entries": []}"#,
    )
    .unwrap();
    let result = config::load_from_path(&dir.join("instance.json"));
    let cleanup = std::fs::remove_dir_all(&dir);
    let inst = result.unwrap_or_else(|e| panic!("expected the JSON config to load: {e}"));
    assert_eq!(inst.id, "toy");
    cleanup.ok();
}

// ---- ValidationTests ----------------------------------------------------------------------------

#[test]
fn test_minimal_config_is_valid() {
    assert_eq!(assert_accepted(minimal()).id, "toy");
}

#[test]
fn test_each_rule_is_enforced() {
    assert_refused(broken1("surprise", json!("x")), "unknown top-level");
    assert_refused(broken_del("gates"), "missing required section: gates");
    assert_refused(broken1("instance__id", json!("Toy Instance")), "must match");
    assert_refused(broken1("units", json!([])), "non-empty list");
    assert_refused(
        broken1(
            "units",
            json!([{"name": "line", "locator_fields": ["f"], "chunk_size": 0}]),
        ),
        "positive chunk_size",
    );
    assert_refused(
        broken1("units", json!([{"name": "line", "chunk_size": 5}])),
        "locator_fields",
    );
    assert_refused(
        broken1(
            "fidelity__confirming",
            json!(["source", "extract", "agent"]),
        ),
        "cannot be every grade",
    );
    assert_refused(
        broken1("fidelity__confirming", json!(["source", "agent"])),
        "agent output may never confirm",
    );
    assert_refused(
        broken1("fidelity__confirming", json!(["nope"])),
        "not in order",
    );
    assert_refused(
        broken1("status__usable", json!(["in", "out"])),
        "cannot be every status",
    );
    assert_refused(
        broken1("status__usable", json!(["elsewhere"])),
        "not in values",
    );
    assert_refused(
        broken1("vocabulary__tiers", json!([1, 2, 3])),
        "must start at 0",
    );
    assert_refused(
        broken1("vocabulary__tiers", json!([0, 3, 2])),
        "must ascend",
    );
    assert_refused(broken1("vocabulary__issues", json!([])), "non-empty list");
    assert_refused(broken1("gates", json!([])), "not gated");
    assert_refused(
        broken1(
            "gates",
            json!([{"id": "g1", "table": "fact", "field": "status", "value": "confirmed"}]),
        ),
        "requires or forbids nothing",
    );
    assert_refused(
        broken1("human_owned", json!(["gates"])),
        "human_owned must include",
    );
    assert_refused(broken1("evaluator", json!({})), "regression_file");
}

#[test]
fn test_an_instance_that_declares_nothing_has_exactly_the_built_in_graph() {
    let inst = assert_accepted(minimal());
    assert!(inst.declared_tables().is_empty());
    assert!(inst.declared_edges().is_empty());
    assert_eq!(inst.node_tables(), config::BUILT_IN_NODE_TABLES.to_vec());
    let edge_map = inst.edge_map();
    for (name, from, to) in config::BUILT_IN_EDGES {
        assert_eq!(edge_map.get(*name).unwrap(), &json!([from, to]));
    }
    assert_eq!(edge_map.len(), config::BUILT_IN_EDGES.len());
}

#[test]
fn test_a_declared_table_or_edge_may_not_take_a_name_the_engine_owns() {
    let cases: &[(&str, &str, &str)] = &[
        ("tables", "unit", "built-in node table"),
        ("tables", "cites", "already a built-in table name"),
        ("tables", "agent", "reserved by the engine"),
        ("edges", "cites", "built-in edge type"),
        ("edges", "unit", "already a built-in table name"),
    ];
    for (section, name, phrase) in cases {
        let body = if *section == "tables" {
            json!({"fields": {"a": "string"}})
        } else {
            json!({"from": "unit", "to": "fact", "fields": {"a": "string"}})
        };
        let data = broken1(section, json!({ *name: body }));
        match config::load_from_mapping(data) {
            Ok(_) => panic!("{section}.{name} should have been refused"),
            Err(e) => {
                let msg = e.to_string();
                assert!(
                    msg.contains(&format!("'{name}'")),
                    "expected repr({name}) in: {msg}"
                );
                assert!(msg.contains(phrase), "expected {phrase:?} in: {msg}");
            }
        }
    }
}

#[test]
fn test_a_name_may_not_be_a_table_and_an_edge_at_once() {
    let data = broken(&[
        (
            "tables",
            Some(json!({"event": {"fields": {"a": "string"}}})),
        ),
        (
            "edges",
            Some(json!({"event": {"from": "unit", "to": "fact"}})),
        ),
    ]);
    assert_refused(data, "already declared under tables");
}

#[test]
fn test_an_edge_naming_an_unknown_table_is_refused_naming_both() {
    let data = broken1("edges", json!({"mentions": {"from": "unit", "to": "evnt"}}));
    match config::load_from_mapping(data) {
        Ok(_) => panic!("expected a refusal"),
        Err(e) => {
            let msg = e.to_string();
            assert!(msg.contains("edges.mentions"));
            assert!(msg.contains("'evnt'"));
            assert!(msg.contains("declare it under tables:"));
        }
    }
    assert_refused(
        broken1(
            "edges",
            json!({"mentions": {"from": "cites", "to": "fact"}}),
        ),
        "which is an edge type, not a node table",
    );
    assert_refused(
        broken1("edges", json!({"mentions": {"from": "unit"}})),
        "to must name the table",
    );
    // ...and one that names a table the instance itself declared is fine
    let data = broken(&[
        (
            "tables",
            Some(json!({"event": {"fields": {"when": "string"}}})),
        ),
        (
            "edges",
            Some(json!({"mentions": {"from": "unit", "to": "event"}})),
        ),
    ]);
    assert_accepted(data);
}

#[test]
fn test_a_declared_field_must_say_what_it_holds() {
    fn table(fields: Value) -> Value {
        broken1("tables", json!({"event": {"fields": fields}}))
    }
    assert_refused(table(json!({"when": "sring"})), "type must be one of");
    assert_refused(
        table(json!({"when": {"one_of": ["a"]}})),
        "type must be one of",
    );
    assert_refused(
        table(json!({"when": {"type": "string", "one_of": []}})),
        "non-empty list",
    );
    assert_refused(
        table(json!({"when": {"type": "string", "one_of": ["a", "a"]}})),
        "duplicates",
    );
    assert_refused(
        table(json!({"when": {"type": "string", "nullable": true}})),
        "unknown key(s) nullable",
    );
    assert_refused(table(json!({"When": "string"})), "must match [a-z0-9_]+");
    assert_refused(
        broken1("tables", json!({"event": {"fields": {}}})),
        "non-empty mapping",
    );
    assert_refused(
        broken1(
            "edges",
            json!({"mentions": {"from": "unit", "to": "fact", "fields": {"in": "string"}}}),
        ),
        "owned by the datastore on a relation table",
    );
}

#[test]
fn test_an_access_role_may_name_a_declared_table() {
    let data = broken(&[
        (
            "tables",
            Some(json!({"event": {"fields": {"when": "string"}}})),
        ),
        (
            "edges",
            Some(json!({"mentions": {"from": "unit", "to": "event"}})),
        ),
        (
            "access",
            Some(
                json!({"roles": {"reader": {"read": ["unit", "event", "mentions"], "write": ["event", "mentions"]}}}),
            ),
        ),
    ]);
    let inst = assert_accepted(data);
    assert_eq!(
        inst.access_roles()["reader"]["write"],
        json!(["event", "mentions"])
    );
    assert_eq!(
        inst.table_fields("event"),
        json!({"when": {"type": "string", "one_of": null, "optional": false}})
    );
    assert_eq!(inst.edge_fields("mentions"), json!({}));
    assert_refused(
        broken1(
            "access",
            json!({"roles": {"reader": {"read": ["unit", "event"]}}}),
        ),
        "unknown table(s) event",
    );
}

#[test]
fn test_errors_are_collected_not_one_at_a_time() {
    let data = broken(&[
        ("human_owned", Some(json!(["gates"]))),
        ("vocabulary__tiers", Some(json!([1, 2]))),
    ]);
    match config::load_from_mapping(data) {
        Ok(_) => panic!("expected a refusal"),
        Err(e) => {
            let msg = e.to_string();
            assert!(msg.contains("human_owned"));
            assert!(msg.contains("tiers"));
        }
    }
}

// ---- VerdictDefinitionTests -----------------------------------------------------------------------

#[test]
fn test_a_new_instance_starts_from_required_originals_owned_by_a_human() {
    let inst = config::load_from_path(&template_path()).expect("_template should load");
    assert!(inst.data["human_owned"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v == "originals"));
    let mut data = inst.data.clone();
    let owned = data["human_owned"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|v| *v != "originals")
        .cloned()
        .collect();
    data["human_owned"] = Value::Array(owned);
    match config::validate(data) {
        Ok(_) => panic!("expected a refusal"),
        Err(e) => assert!(e.to_string().contains("originals")),
    }
}

#[test]
fn test_the_demo_and_the_template_both_define_every_verdict() {
    for path in [demo_path(), template_path()] {
        let inst =
            config::load_from_path(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
        let mut meanings: Vec<String> = inst.verdict_meanings().keys().cloned().collect();
        meanings.sort();
        let mut verdicts: Vec<String> = gw_core::config::gates_support::CRITIQUE_VERDICTS
            .iter()
            .map(|s| s.to_string())
            .collect();
        verdicts.sort();
        assert_eq!(meanings, verdicts, "instance {}", path.display());
    }
}

#[test]
fn test_an_instance_may_leave_them_undefined() {
    let mut data = demo_data();
    data.as_object_mut().unwrap().remove("verdicts");
    let owned: Vec<Value> = data["human_owned"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|v| *v != "verdicts")
        .cloned()
        .collect();
    data["human_owned"] = Value::Array(owned);
    let inst = assert_accepted(data);
    assert!(inst.verdict_meanings().is_empty());
}

#[test]
fn test_defining_only_some_of_them_is_refused() {
    let mut data = demo_data();
    data["verdicts"].as_object_mut().unwrap().remove("damaging");
    match config::load_from_mapping(data) {
        Ok(_) => panic!("expected a refusal"),
        Err(e) => {
            let msg = e.to_string();
            assert!(msg.contains("damaging"));
            assert!(msg.contains("undefined"));
        }
    }
}

#[test]
fn test_a_verdict_the_engine_does_not_have_is_refused() {
    let mut data = demo_data();
    data["verdicts"].as_object_mut().unwrap().insert(
        "catastrophic".to_string(),
        json!("a verdict the engine has never heard of"),
    );
    assert_refused(data, "catastrophic");
}

#[test]
fn test_a_definition_too_short_to_act_on_is_refused() {
    let mut data = demo_data();
    data["verdicts"]
        .as_object_mut()
        .unwrap()
        .insert("fatal".to_string(), json!("bad"));
    assert_refused(data, "not a definition");
}

#[test]
fn test_defining_them_without_owning_them_is_refused() {
    let mut data = demo_data();
    let owned: Vec<Value> = data["human_owned"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|v| *v != "verdicts")
        .cloned()
        .collect();
    data["human_owned"] = Value::Array(owned);
    match config::load_from_mapping(data) {
        Ok(_) => panic!("expected a refusal"),
        Err(e) => {
            let msg = e.to_string();
            assert!(msg.contains("human_owned"));
            assert!(msg.contains("verdicts"));
        }
    }
}
