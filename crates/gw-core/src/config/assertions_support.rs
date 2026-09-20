//! The slivers of `groundworks/assertions.py` that `config.py` calls: `field_domain` (every gate rule's
//! value is checked against one) and `validate` (the optional `assertions:` section). The SurQL
//! emission, the report and the write-time check are out of this ticket's scope (they need a live
//! database and are not part of `config.load`).

use super::gates_support::{CRITIQUE_VERDICTS, FACT_STATUSES};
use serde_json::Value;

/// `groundworks/assertions.py:64 fields_of`: field -> TYPE NAME (not the full declared spec), for a
/// built-in or declared node table; None for a table this instance does not have.
pub fn fields_of(data: &Value, table: &str) -> Option<Value> {
    if let Some((_, fields)) = super::BUILT_IN_FIELDS.iter().find(|(name, _)| *name == table) {
        let mut m = serde_json::Map::new();
        for (field, ty) in *fields {
            m.insert(field.to_string(), Value::String(ty.to_string()));
        }
        return Some(Value::Object(m));
    }
    let declared = super::declared_tables_of(data);
    let fields_map = declared.get(table)?.as_object()?;
    let mut m = serde_json::Map::new();
    for (name, spec) in fields_map {
        let ty = spec.get("type").and_then(Value::as_str).unwrap_or("").to_string();
        m.insert(name.clone(), Value::String(ty));
    }
    Some(Value::Object(m))
}

/// `groundworks/assertions.py:74 field_domain`: every value `table.field` may hold, or None.
pub fn field_domain(data: &Value, table: &str, field: &str) -> Option<Vec<Value>> {
    let declared = super::declared_tables_of(data);
    if let Some(fields) = declared.get(table) {
        let spec = fields.get(field)?;
        if let Some(one_of) = spec.get("one_of").filter(|v| !v.is_null()) {
            return one_of.as_array().cloned();
        }
        return if spec.get("type").and_then(Value::as_str) == Some("bool") {
            Some(vec![Value::Bool(true), Value::Bool(false)])
        } else {
            None
        };
    }
    let vocab = data.get("vocabulary").filter(|v| v.is_object());
    let status = data.get("status").filter(|v| v.is_object());
    let fidelity = data.get("fidelity").filter(|v| v.is_object());
    let units = data.get("units").and_then(Value::as_array);
    let unit_names: Vec<Value> = units
        .map(|list| {
            list.iter()
                .filter_map(|u| u.as_object())
                .filter_map(|o| o.get("name").cloned())
                .collect()
        })
        .unwrap_or_default();
    let values: Option<Vec<Value>> = match (table, field) {
        ("unit", "type") => Some(unit_names),
        ("unit", "status") => status.and_then(|s| s.get("values")).and_then(Value::as_array).cloned(),
        ("unit", "grade") => fidelity.and_then(|f| f.get("order")).and_then(Value::as_array).cloned(),
        ("fact", "status") => Some(FACT_STATUSES.iter().map(|s| Value::String(s.to_string())).collect()),
        ("critique", "verdict") => {
            Some(CRITIQUE_VERDICTS.iter().map(|s| Value::String(s.to_string())).collect())
        }
        ("critique", "answered") | ("conclusion", "ready") | ("authority", "verified") => {
            Some(vec![Value::Bool(true), Value::Bool(false)])
        }
        ("label", "tier") => vocab.and_then(|v| v.get("tiers")).and_then(Value::as_array).cloned(),
        ("label", "dir") => vocab.and_then(|v| v.get("directions")).and_then(Value::as_array).cloned(),
        ("label", "role") => vocab.and_then(|v| v.get("roles")).and_then(Value::as_array).cloned(),
        _ => None,
    };
    values.filter(|v| !v.is_empty())
}

const SPEC_KEYS: &[&str] = &["fields", "promoted_by", "enforce", "doc"];
const EDGE_OWNED: &[&str] =
    &["field", "value", "field_history", "value_history", "learned_at"];

fn gate_rules(data: &Value) -> Vec<Value> {
    data.get("gates")
        .and_then(Value::as_array)
        .map(|list| {
            list.iter()
                .filter(|r| r.as_object().map(|o| o.contains_key("id")).unwrap_or(false))
                .cloned()
                .collect()
        })
        .unwrap_or_default()
}

/// `groundworks/assertions.py:124 validate`. Called from `config::validate` only when `assertions:`
/// is present (never in the corpus this port's differential covers today, but kept so a later
/// instance that declares one gets the same refusals as the oracle).
pub fn validate(data: &Value, problems: &mut Vec<String>) {
    let Some(section) = data.get("assertions") else { return };
    let Some(section_obj) = section.as_object().filter(|o| !o.is_empty()) else {
        problems.push(
            "assertions must be a mapping of edge type -> {fields: [...], promoted_by, enforce}".to_string(),
        );
        return;
    };
    let edges = super::edge_map_of(data);
    let declared_edges = super::declared_edges_of(data);
    let rules = gate_rules(data);
    let built_in_edges: Vec<&str> = super::BUILT_IN_EDGES.iter().map(|(name, _, _)| *name).collect();

    for (edge, spec) in section_obj {
        let where_ = format!("assertions.{edge}");
        if built_in_edges.contains(&edge.as_str()) {
            problems.push(format!(
                "{where_}: {} is a built-in edge with its own meaning; declare an edge for assertions under edges:",
                super::py_repr_str(edge)
            ));
            continue;
        }
        let Some(pair) = edges.get(edge.as_str()).and_then(Value::as_array) else {
            let mut known: Vec<&str> = edges.keys().map(String::as_str).collect();
            known.sort();
            problems.push(format!(
                "{where_}: no edge {}; declare it under edges: with from and to (known edges: {})",
                super::py_repr_str(edge),
                known.join(", ")
            ));
            continue;
        };
        let src = pair[0].as_str().unwrap_or_default().to_string();
        let dst = pair[1].as_str().unwrap_or_default().to_string();
        let (src, dst) = (src.as_str(), dst.as_str());
        let Some(spec_obj) = spec.as_object() else {
            problems.push(format!("{where_}: must be a mapping with fields"));
            continue;
        };
        let mut unknown: Vec<&str> =
            spec_obj.keys().map(String::as_str).filter(|k| !SPEC_KEYS.contains(k)).collect();
        unknown.sort();
        if !unknown.is_empty() {
            let mut allowed = SPEC_KEYS.to_vec();
            allowed.sort();
            problems.push(format!(
                "{where_}: unknown key(s) {}; allowed: {}",
                unknown.join(", "),
                allowed.join(", ")
            ));
        }
        let mut owned: Vec<String> = declared_edges
            .get(edge.as_str())
            .and_then(|e| e.get("fields"))
            .and_then(Value::as_object)
            .map(|f| f.keys().filter(|k| EDGE_OWNED.contains(&k.as_str())).cloned().collect())
            .unwrap_or_default();
        owned.sort();
        if !owned.is_empty() {
            problems.push(format!(
                "{where_}: edges.{edge} declares {}, which the engine owns on an assertion edge",
                owned.join(", ")
            ));
        }
        let target_fields = fields_of(data, dst);
        let fields = spec_obj.get("fields").and_then(Value::as_array).filter(|f| !f.is_empty());
        match fields {
            None => problems.push(format!(
                "{where_}: fields must be a non-empty list of {dst} fields this edge may assert"
            )),
            Some(fields) => {
                let names: Vec<String> = fields.iter().map(super::py_str_of).collect();
                let mut uniq = names.clone();
                uniq.sort();
                uniq.dedup();
                if uniq.len() != names.len() {
                    problems.push(format!("{where_}: fields lists a field twice"));
                }
                for field in &names {
                    let has_field = target_fields
                        .as_ref()
                        .and_then(Value::as_object)
                        .map(|o| o.contains_key(field))
                        .unwrap_or(false);
                    if target_fields.is_none() || !has_field {
                        let mut known: Vec<&str> = target_fields
                            .as_ref()
                            .and_then(Value::as_object)
                            .map(|o| o.keys().map(String::as_str).collect())
                            .unwrap_or_default();
                        known.sort();
                        let known_text = if known.is_empty() { "none".to_string() } else { known.join(", ") };
                        problems.push(format!(
                            "{where_}: {} is not a field of {dst}; its fields are {known_text}",
                            super::py_repr_str(field)
                        ));
                    } else if field_domain(data, dst, field).is_none() {
                        problems.push(format!(
                            "{where_}: {dst}.{field} has no enumerated values (no vocabulary, no one_of, not bool), \
so no asserted value could be checked as legal; assert an enumerated field"
                        ));
                    } else if super::declared_tables_of(data).contains_key(dst)
                        && target_fields
                            .as_ref()
                            .and_then(Value::as_object)
                            .map(|o| o.contains_key(&format!("{field}_history")))
                            .unwrap_or(false)
                    {
                        problems.push(format!(
                            "{where_}: tables.{dst} declares {field}_history, which the engine uses for {field}'s history"
                        ));
                    }
                }
            }
        }
        if super::declared_tables_of(data).contains_key(dst) {
            let learned_at_type = target_fields
                .as_ref()
                .and_then(Value::as_object)
                .and_then(|o| o.get("learned_at"))
                .and_then(Value::as_str)
                .unwrap_or("datetime");
            if learned_at_type != "datetime" {
                problems.push(format!(
                    "{where_}: tables.{dst} declares learned_at as {learned_at_type}; history needs it to be a datetime"
                ));
            }
        }
        let on_source: Vec<&Value> = rules
            .iter()
            .filter(|r| r.get("table").and_then(Value::as_str) == Some(src))
            .collect();
        if on_source.is_empty() {
            problems.push(format!(
                "{where_}: no gate rule promotes a {src} row, so no assertion from {src} could ever count; gate {src} or assert from a gated table"
            ));
        }
        if let Some(wanted) = spec_obj.get("promoted_by") {
            match wanted.as_array().filter(|w| !w.is_empty()) {
                None => problems.push(format!("{where_}: promoted_by must be a non-empty list of gate rule ids on {src}")),
                Some(wanted) => {
                    let ids: std::collections::HashSet<String> = on_source
                        .iter()
                        .filter_map(|r| r.get("id").map(super::py_str_of))
                        .collect();
                    let bad: Vec<String> =
                        wanted.iter().map(super::py_str_of).filter(|w| !ids.contains(w)).collect();
                    if !bad.is_empty() {
                        let mut sorted_ids: Vec<&String> = ids.iter().collect();
                        sorted_ids.sort();
                        let ids_text = if sorted_ids.is_empty() {
                            "none".to_string()
                        } else {
                            sorted_ids.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")
                        };
                        problems.push(format!(
                            "{where_}: promoted_by names {}, not a gate rule on {src} (rules on {src}: {ids_text})",
                            bad.join(", ")
                        ));
                    }
                }
            }
        }
        // `assertions.py:192`: `spec.get("enforce", False) not in (True, False)`. Python's `in` on a
        // tuple of bools uses `==`, and `True == 1`/`False == 0` (bool is an int subtype), so
        // `enforce: 1` and `enforce: 0` are ACCEPTED, not just literal `true`/`false`.
        let enforce_ok = spec_obj.get("enforce").map(super::py_equals_bool).unwrap_or(true);
        if !enforce_ok {
            problems.push(format!("{where_}: enforce must be true or false"));
        }
    }
}
