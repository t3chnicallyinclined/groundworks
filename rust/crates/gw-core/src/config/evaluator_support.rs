//! The one thing `config.py` needs from `groundworks/evaluator.py`: a declared `regression_file`
//! must exist and parse as a regression file (`config.refuse_unusable_regression_file` ->
//! `evaluator.read_file` -> `parse`). Running the checks themselves (writes/loads/validator, needing
//! a live database or a loaded corpus) is out of this ticket's scope.

use serde_json::Value;
use std::path::Path;

pub const FORMAT: &str = "groundworks.known_errors/1";
const KINDS: &[&str] = &["writes", "loads", "validator"];
const ENTRY_KEYS: &[&str] = &["id", "error", "found", "added", "check", "note"];
const WRITE_OPS: &[&str] = &[
    "propose",
    "cite",
    "confirm",
    "rule_out",
    "support",
    "relate",
    "critique",
    "answer",
    "mark_ready",
];
const WRITE_OUTCOMES: &[&str] = &["accepted", "refused", "denied"];
const LOAD_LABELS: &[&str] = &["fixture", "empty_dir", "missing_dir", "wrong_extension"];
const LOAD_STEPS: &[&str] = &[
    "count",
    "load",
    "correct",
    "expect_count",
    "expect_unit",
    "expect_load",
];

/// `groundworks/evaluator.py`'s `RegressionFileError`, carrying the same message text.
#[derive(Debug, Clone)]
pub struct RegressionFileError(pub String);

impl std::fmt::Display for RegressionFileError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}
impl std::error::Error for RegressionFileError {}

/// `groundworks/evaluator.py:100 parse`.
pub fn parse(text: &str, where_: &str) -> Result<Value, RegressionFileError> {
    let data: Value = serde_json::from_str(text)
        .map_err(|e| RegressionFileError(format!("{where_} is not valid JSON: {e}")))?;
    let Some(obj) = data.as_object() else {
        return Err(RegressionFileError(format!(
            "{where_} must be a JSON object with format and entries"
        )));
    };
    let mut problems = Vec::new();
    if obj.get("format").and_then(Value::as_str) != Some(FORMAT) {
        problems.push(format!(
            "format must be '{FORMAT}' (got {})",
            super::py_repr_value(obj.get("format").unwrap_or(&Value::Null))
        ));
    }
    let entries = obj.get("entries").and_then(Value::as_array);
    if entries.is_none() {
        problems.push(
            "entries must be a list (it may be empty only until the first error is recorded)"
                .to_string(),
        );
    }
    let empty = Vec::new();
    let entries = entries.unwrap_or(&empty);
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    for (i, entry) in entries.iter().enumerate() {
        let mut label = format!("entry {i}");
        let Some(entry_obj) = entry.as_object() else {
            problems.push(format!("{label} is not an object"));
            continue;
        };
        let id_repr = entry_obj
            .get("id")
            .map(super::py_str_of)
            .unwrap_or_else(|| i.to_string());
        label = format!("entry {id_repr}");
        let mut unknown: Vec<&str> = entry_obj
            .keys()
            .map(String::as_str)
            .filter(|k| !ENTRY_KEYS.contains(k))
            .collect();
        unknown.sort();
        if !unknown.is_empty() {
            problems.push(format!("{label} has unknown key(s) {}", unknown.join(", ")));
        }
        for key in ["id", "error", "found", "check"] {
            let present = entry_obj.get(key).map(|v| !is_falsy(v)).unwrap_or(false);
            if !present {
                problems.push(format!("{label} needs {key}"));
            }
        }
        let id_key = entry_obj.get("id").cloned().unwrap_or(Value::Null);
        let id_marker = super::py_str_of(&id_key);
        if !seen.insert(id_marker) {
            problems.push(format!("{label}: duplicate id"));
        }
        match entry_obj.get("check") {
            Some(Value::Object(check)) => {
                for p in check_problems(check) {
                    problems.push(format!("{label}: {p}"));
                }
            }
            Some(Value::Null) | None => {}
            Some(_) => problems.push(format!("{label}: check must be an object")),
        }
    }
    if !problems.is_empty() {
        return Err(RegressionFileError(format!(
            "{where_} is not a valid regression file:\n  - {}",
            problems.join("\n  - ")
        )));
    }
    Ok(data)
}

fn is_falsy(v: &Value) -> bool {
    matches!(v, Value::Null)
        || matches!(v, Value::Bool(false))
        || matches!(v, Value::String(s) if s.is_empty())
        || matches!(v, Value::Array(a) if a.is_empty())
        || matches!(v, Value::Object(o) if o.is_empty())
        || matches!(v, Value::Number(n) if n.as_f64() == Some(0.0))
}

fn check_problems(check: &serde_json::Map<String, Value>) -> Vec<String> {
    let kind = check.get("kind").and_then(Value::as_str);
    let Some(kind) = kind.filter(|k| KINDS.contains(k)) else {
        return vec![format!(
            "check kind must be one of {} (got {})",
            KINDS.join(", "),
            super::py_repr_value(check.get("kind").unwrap_or(&Value::Null))
        )];
    };
    let mut problems = Vec::new();
    if kind == "writes" {
        let writers = check
            .get("writers")
            .and_then(Value::as_object)
            .filter(|w| !w.is_empty());
        if writers.is_none() {
            problems.push("a writes check needs writers: {name: {role, lens}}".to_string());
        }
        let writer_names: std::collections::HashSet<&str> = writers
            .map(|w| w.keys().map(String::as_str).collect())
            .unwrap_or_default();
        let steps = check.get("steps").and_then(Value::as_array);
        if steps.map(|s| s.is_empty()).unwrap_or(true) {
            problems.push("a writes check needs steps".to_string());
        }
        let empty = Vec::new();
        let steps = steps.unwrap_or(&empty);
        let mut expects: std::collections::HashSet<String> = std::collections::HashSet::new();
        for (n, step) in steps.iter().enumerate() {
            let n = n + 1;
            let Some(step_obj) = step.as_object() else {
                problems.push(format!("step {n} is not an object"));
                continue;
            };
            let as_ = step_obj.get("as").and_then(Value::as_str);
            if !as_.map(|a| writer_names.contains(a)).unwrap_or(false) {
                problems.push(format!(
                    "step {n}: as {} is not one of the declared writers",
                    super::py_repr_value(step_obj.get("as").unwrap_or(&Value::Null))
                ));
            }
            let op = step_obj.get("op").and_then(Value::as_str);
            if !op.map(|o| WRITE_OPS.contains(&o)).unwrap_or(false) {
                problems.push(format!(
                    "step {n}: op must be one of {}",
                    WRITE_OPS.join(", ")
                ));
            }
            let expect = step_obj.get("expect").and_then(Value::as_str);
            if !expect.map(|e| WRITE_OUTCOMES.contains(&e)).unwrap_or(false) {
                problems.push(format!(
                    "step {n}: expect must be one of {}",
                    WRITE_OUTCOMES.join(", ")
                ));
            }
            if let Some(e) = expect {
                expects.insert(e.to_string());
            }
        }
        if !steps.is_empty()
            && (!expects.contains("accepted")
                || !(expects.contains("refused") || expects.contains("denied")))
        {
            problems.push(
                "a writes check must show both directions: at least one step accepted and at least one \
refused or denied (a check that only refuses passes a gate that refuses everything)"
                    .to_string(),
            );
        }
        let setup_ok = check.get("setup").map(|s| s.is_array()).unwrap_or(true);
        if !setup_ok {
            problems.push("setup must be a list of SurrealQL statements".to_string());
        }
    } else if kind == "loads" {
        let empty = Vec::new();
        let steps = check
            .get("steps")
            .and_then(Value::as_array)
            .unwrap_or(&empty);
        let names: Vec<Option<String>> = steps
            .iter()
            .map(|s| {
                s.as_object()
                    .filter(|o| o.len() == 1)
                    .and_then(|o| o.keys().next().cloned())
            })
            .collect();
        for (n, (step, name)) in steps.iter().zip(names.iter()).enumerate() {
            let n = n + 1;
            match name {
                None => problems.push(format!(
                    "step {n} must be an object with exactly one of {}",
                    LOAD_STEPS.join(", ")
                )),
                Some(name) if !LOAD_STEPS.contains(&name.as_str()) => problems.push(format!(
                    "step {n} must be an object with exactly one of {}",
                    LOAD_STEPS.join(", ")
                )),
                Some(name) if name == "load" => {
                    let load = step.get("load").and_then(Value::as_object);
                    let labels = load
                        .and_then(|l| l.get("labels"))
                        .and_then(Value::as_str)
                        .unwrap_or("fixture");
                    if !LOAD_LABELS.contains(&labels) {
                        problems.push(format!(
                            "step {n}: load labels must be one of {}",
                            LOAD_LABELS.join(", ")
                        ));
                    }
                }
                _ => {}
            }
        }
        if !names.iter().flatten().any(|n| n.starts_with("expect")) {
            problems.push(
                "a loads check needs at least one expect_ step: a check that expects nothing passes whatever happens"
                    .to_string(),
            );
        }
        if !names.iter().flatten().any(|n| n == "load") {
            problems.push("a loads check needs at least one load step".to_string());
        }
    } else if kind == "validator" {
        for key in ["slice", "labels", "control_labels", "expect_errors"] {
            let ok = check
                .get(key)
                .and_then(Value::as_array)
                .map(|a| !a.is_empty())
                .unwrap_or(false);
            if !ok {
                problems.push(format!("a validator check needs a non-empty {key} list"));
            }
        }
        for pair in check
            .get("expect_errors")
            .and_then(Value::as_array)
            .unwrap_or(&Vec::new())
        {
            let ok = pair
                .as_object()
                .map(|o| {
                    !is_falsy(o.get("unit").unwrap_or(&Value::Null))
                        && !is_falsy(o.get("field").unwrap_or(&Value::Null))
                })
                .unwrap_or(false);
            if !ok {
                problems.push("expect_errors entries are {unit, field}".to_string());
            }
        }
    }
    problems
}

/// `groundworks/evaluator.py:186 read_file`, narrowed to what `config.py` needs from it: the parsed
/// data, or a `RegressionFileError` naming the file. (The oracle also returns a sha256 of the raw
/// bytes so a later runner can prove the file went unwritten; `config.load`'s own call site --
/// `refuse_unusable_regression_file` -- discards that value, and nothing in the config port needs it,
/// so it is left for whichever port unit adds `groundworks.evaluator` itself.)
pub fn read_file(path: &Path) -> Result<Value, RegressionFileError> {
    let raw = std::fs::read(path).map_err(|e| {
        RegressionFileError(format!(
            "regression file {} cannot be read: {e}",
            path.display()
        ))
    })?;
    let text = String::from_utf8(raw).map_err(|e| {
        RegressionFileError(format!(
            "regression file {} is not UTF-8: {e}",
            path.display()
        ))
    })?;
    parse(&text, &path.display().to_string())
}
