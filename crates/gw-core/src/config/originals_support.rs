//! The one function `config.py` calls into `groundworks/originals.py` for: the `originals:` section's
//! own validation (`_validate_originals` -> `originals.config_problems`). Everything else in
//! originals.py (the document schema, the store, the report) is out of this ticket's scope.

use serde_json::Value;

pub const MODES: &[&str] = &["required", "transition"];
const SPAN_KINDS: &[&str] = &["line", "lines", "chars", "whole"];
const SECTION_KEYS: &[&str] = &["mode", "store", "spans"];

/// `groundworks/originals.py:90 config_problems`.
pub fn config_problems(data: &Value, problems: &mut Vec<String>) {
    let Some(value) = data.get("originals") else { return };
    let body: Value = match value {
        Value::String(_) => {
            let mut m = serde_json::Map::new();
            m.insert("mode".to_string(), value.clone());
            Value::Object(m)
        }
        other => other.clone(),
    };
    let Some(body_obj) = body.as_object() else {
        problems.push(format!(
            "originals must be a mode ({}) or a mapping with mode, store, spans",
            MODES.join(" | ")
        ));
        return;
    };
    let mut unknown: Vec<&str> =
        body_obj.keys().map(String::as_str).filter(|k| !SECTION_KEYS.contains(k)).collect();
    unknown.sort();
    if !unknown.is_empty() {
        problems.push(format!(
            "originals: unknown key(s) {}; allowed: {}",
            unknown.iter().map(|k| k.to_string()).collect::<Vec<_>>().join(", "),
            {
                let mut allowed = SECTION_KEYS.to_vec();
                allowed.sort();
                allowed.join(", ")
            }
        ));
    }
    let mode = body_obj.get("mode").and_then(Value::as_str);
    if !mode.map(|m| MODES.contains(&m)).unwrap_or(false) {
        problems.push(format!(
            "originals.mode must be one of {} (got {})",
            MODES.join(", "),
            super::py_repr_value(body_obj.get("mode").unwrap_or(&Value::Null))
        ));
    }
    // `originals.py:102-104`: `store = body.get("store"); if store is not None and not isinstance(store,
    // (str, dict)): ...` -- an explicit `store: null` is ACCEPTED (the guard is `is not None`, not
    // "present"), so a Value::Null here must be skipped exactly like a missing key.
    if let Some(store) = body_obj.get("store").filter(|v| !v.is_null()) {
        if !(store.is_string() || store.is_object()) {
            problems.push("originals.store must be a path or a mapping with a backend".to_string());
        }
    }
    let units: std::collections::HashMap<String, &Value> = data
        .get("units")
        .and_then(Value::as_array)
        .map(|list| {
            list.iter()
                .filter_map(|u| u.as_object().map(|o| (o.get("name"), u)))
                .filter_map(|(name, u)| name.and_then(Value::as_str).map(|n| (n.to_string(), u)))
                .collect()
        })
        .unwrap_or_default();
    let spans = body_obj.get("spans").and_then(Value::as_object);
    if let Some(spans) = spans {
        for (unit_type, spec) in spans {
            let where_ = format!("originals.spans.{unit_type}");
            let Some(unit) = units.get(unit_type) else {
                problems.push(format!("{where_}: no unit type {}", super::py_repr_str(unit_type)));
                continue;
            };
            let spec_obj = spec.as_object();
            let single_key = spec_obj.filter(|o| o.len() == 1).and_then(|o| o.keys().next());
            let valid_kind = single_key.map(|k| SPAN_KINDS.contains(&k.as_str())).unwrap_or(false);
            if spec_obj.is_none() || !valid_kind {
                problems.push(format!("{where_}: must be exactly one of {}", SPAN_KINDS.join(", ")));
                continue;
            }
            let kind = single_key.unwrap().as_str();
            let fields = spec_obj.unwrap().get(kind).expect("kind came from this map's own keys");
            let locator: Vec<String> = unit
                .get("locator_fields")
                .and_then(Value::as_array)
                .map(|l| l.iter().map(super::py_str_of).collect())
                .unwrap_or_default();
            if kind == "whole" {
                if fields != &Value::Bool(true) {
                    problems.push(format!("{where_}: whole must be true"));
                }
                continue;
            }
            let (names, names_is_list): (Vec<Value>, bool) = if kind == "line" {
                (vec![fields.clone()], true)
            } else {
                match fields.as_array() {
                    Some(a) => (a.clone(), true),
                    None => (Vec::new(), false),
                }
            };
            let expected_len = if kind == "line" { 1 } else { 2 };
            if !names_is_list || names.len() != expected_len {
                problems.push(format!(
                    "{where_}: {kind} takes {}",
                    if kind == "line" { "one locator field" } else { "[start_field, end_field]" }
                ));
                continue;
            }
            for name in &names {
                let name_str = super::py_str_of(name);
                if !locator.contains(&name_str) {
                    problems.push(format!(
                        "{where_}: {} is not a locator field of {unit_type} ({})",
                        super::py_repr_value(name),
                        locator.join(", ")
                    ));
                }
            }
        }
    }
}
