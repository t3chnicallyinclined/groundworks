//! `groundworks/config.py` (v0.2.0, 1229 lines), ported verbatim (`feedback-port-proven-code-asis`):
//! the strict YAML subset, the instance validation, and `Instance`'s read accessors. No re-design.
//!
//! Scope for this ticket (port-01): `config.py` itself, plus the SLIVERS of `gates.py`,
//! `assertions.py`, `originals.py` and `evaluator.py` that `config.py` calls into (a handful of
//! constants and pure functions each -- see `gates_support`, `assertions_support`,
//! `originals_support`, `evaluator_support`). The SurQL emitters, the database client and the rest of
//! those modules are port-02/03/04 and later.

pub mod assertions_support;
pub mod evaluator_support;
pub mod gates_support;
pub mod json_sorted;
pub mod originals_support;
pub mod yaml;

use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

pub use yaml::parse_yaml;

// ---- errors --------------------------------------------------------------------------------------

/// `groundworks/config.py`'s `ConfigError`: the config is unusable, message lists every problem.
///
/// `kind` carries the Python exception TYPE NAME the oracle would have raised (`type(exc).__name__`
/// in `differential.py`'s golden text) -- `YamlError` for a strict-YAML-subset refusal (`YamlError`
/// subclasses `ConfigError` in the oracle), `ConfigError` for everything else. `gw config check`
/// prints it so `REFUSE -> YamlError: ...` and `REFUSE -> ConfigError: ...` are told apart exactly as
/// the oracle's own text does, instead of a literal `"ConfigError"` on every refusal.
#[derive(Debug, Clone, thiserror::Error)]
#[error("{message}")]
pub struct ConfigError {
    pub kind: &'static str,
    pub message: String,
}

impl ConfigError {
    pub fn new(message: impl Into<String>) -> Self {
        ConfigError { kind: "ConfigError", message: message.into() }
    }
}

#[allow(non_snake_case)]
fn ConfigError(message: String) -> ConfigError {
    ConfigError::new(message)
}

impl From<yaml::YamlError> for ConfigError {
    fn from(e: yaml::YamlError) -> Self {
        ConfigError { kind: "YamlError", message: e.0 }
    }
}
impl From<evaluator_support::RegressionFileError> for ConfigError {
    fn from(e: evaluator_support::RegressionFileError) -> Self {
        ConfigError::new(e.0)
    }
}

pub type R<T> = Result<T, ConfigError>;

// ---- the graph shape (config.py module level) -----------------------------------------------------

pub const BUILT_IN_NODE_TABLES: &[&str] =
    &["unit", "label", "fact", "conclusion", "authority", "critique", "answer", "document"];

/// (name, from, to) -- `groundworks/config.py:43 BUILT_IN_EDGES`.
pub const BUILT_IN_EDGES: &[(&str, &str, &str)] = &[
    ("cites", "fact", "unit"),
    ("supports", "fact", "conclusion"),
    ("undercuts", "fact", "conclusion"),
    ("attacks", "critique", "conclusion"),
    ("answers", "answer", "critique"),
    ("bears_on", "label", "conclusion"),
    ("relies_on", "conclusion", "authority"),
];

pub const RESERVED_TABLES: &[&str] = &["agent"];

/// (table, [(field, type), ...]) -- `groundworks/config.py:59 BUILT_IN_FIELDS`.
pub const BUILT_IN_FIELDS: &[(&str, &[(&str, &str)])] = &[
    (
        "unit",
        &[
            ("type", "string"), ("text", "string"), ("locator", "object"), ("status", "string"),
            ("grade", "string"), ("hash", "string"), ("meta", "object"), ("document", "record"),
        ],
    ),
    (
        "label",
        &[
            ("unit", "record"), ("tier", "int"), ("dir", "string"), ("issues", "array"),
            ("targets", "array"), ("role", "string"), ("reason", "string"), ("quote", "string"),
            ("grade", "string"), ("chunk", "string"), ("run", "string"),
        ],
    ),
    (
        "fact",
        &[
            ("statement", "string"), ("status", "string"), ("note", "string"), ("tried", "string"),
            ("evidence", "string"), ("reasoning_type", "string"), ("author", "string"),
            ("lens", "string"), ("learned_at", "datetime"),
        ],
    ),
    ("conclusion", &[("statement", "string"), ("ready", "bool"), ("rating", "string"), ("author", "string")]),
    ("authority", &[("citation", "string"), ("verified", "bool"), ("source_grade", "string")]),
    (
        "critique",
        &[
            ("lens", "string"), ("head", "string"), ("verdict", "string"), ("text", "string"),
            ("author", "string"), ("answered", "bool"),
        ],
    ),
    ("answer", &[("text", "string"), ("author", "string")]),
    (
        "document",
        &[
            ("sha256", "string"), ("media_type", "string"), ("bytes", "int"), ("capture", "string"),
            ("source_uri", "string"), ("retrieved_at", "datetime"), ("original_name", "string"),
            ("integrity", "string"), ("verified_at", "datetime"),
        ],
    ),
];

pub const INDEPENDENCE_FIELD: &str = "written_by";
pub const INDEPENDENCE_BETWEEN: &[&str] = &["credential", "principal"];
const INDEPENDENCE_KEYS: &[&str] = &["between", "own_evidence"];
const AUDIT_TABLES: &[&str] = &["row_writer", "link_creator", "promotion_log", "promotion_head"];

const FIELD_TYPES: &[&str] = &["string", "int", "float", "bool", "datetime"];
const TABLE_KEYS: &[&str] = &["fields", "doc"];
const EDGE_KEYS: &[&str] = &["from", "to", "fields", "doc"];
const FIELD_KEYS: &[&str] = &["type", "one_of", "optional", "doc"];
const EDGE_RESERVED_FIELDS: &[&str] = &["id", "in", "out"];

const TOP_KEYS: &[&str] = &[
    "instance", "units", "fidelity", "status", "vocabulary", "gates", "lenses", "roles", "evaluator",
    "human_owned", "privacy", "panel", "present", "embed", "access", "authority", "staleness",
    "discovery", "tables", "edges", "verdicts", "unique", "assertions", "promotion_log", "labels",
    "originals",
];
const REQUIRED_TOP: &[&str] =
    &["instance", "units", "fidelity", "status", "vocabulary", "gates", "evaluator", "human_owned", "privacy"];
const MUST_BE_HUMAN_OWNED: &[&str] = &["gates", "fidelity", "status", "evaluator", "privacy"];
const HUMAN_OWNED_IF_PRESENT: &[&str] = &["verdicts", "unique", "assertions", "promotion_log", "originals", "labels"];

const LENS_KINDS: &[&str] = &["perspective", "discipline", "framework"];
const LENS_BACKINGS: &[&str] = &["prompt", "source", "graph", "calibrated"];
const LENS_KEYS: &[&str] = &[
    "name", "kind", "charter", "standard", "filter", "backing", "frame", "crosswalk", "signed", "order",
];
const LENS_STANDARD_KEYS: &[&str] = &["status", "grade"];
const LENS_FILTER_FIELDS: &[&str] = &["tier", "dir", "issues", "targets", "role", "chunk"];
const LENS_FILTER_WORDS: &[&str] = &[
    "AND", "OR", "NOT", "IN", "CONTAINS", "CONTAINSANY", "CONTAINSALL", "CONTAINSNOT", "NONE", "true", "false",
];

// ---- small python-shaped helpers ------------------------------------------------------------------

pub fn py_repr_str(s: &str) -> String {
    let has_single = s.contains('\'');
    let has_double = s.contains('"');
    let quote = if has_single && !has_double { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for ch in s.chars() {
        match ch {
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Python `repr(value)` for the JSON-shaped values a config can hold. Used everywhere config.py
/// applies `%r` to a value pulled out of the parsed config (not always a plain string).
pub fn py_repr_value(v: &Value) -> String {
    match v {
        Value::Null => "None".to_string(),
        Value::Bool(b) => if *b { "True".to_string() } else { "False".to_string() },
        Value::Number(n) => py_number_str(n),
        Value::String(s) => py_repr_str(s),
        Value::Array(items) => format!("[{}]", items.iter().map(py_repr_value).collect::<Vec<_>>().join(", ")),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(k, v)| format!("{}: {}", py_repr_str(k), py_repr_value(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

/// Python `str(value)` for the same value space (no quoting for a bare string).
pub fn py_str_of(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "None".to_string(),
        Value::Bool(b) => if *b { "True".to_string() } else { "False".to_string() },
        Value::Number(n) => py_number_str(n),
        other => py_repr_value(other),
    }
}

/// `str(n)` for a JSON number as Python would print the `int`/`float` it parsed from: an integer
/// prints as itself, a float goes through `float_repr` (CPython's `repr(float)` algorithm), which
/// diverges from `serde_json::Number`'s own `Display` on the exponent form (`1e30` here vs
/// Python's `1e+30`) and on whether a whole-valued float keeps its trailing `.0` (`1.0`, never
/// bare `1`).
fn py_number_str(n: &serde_json::Number) -> String {
    if n.is_i64() || n.is_u64() {
        n.to_string()
    } else {
        float_repr(n.as_f64().unwrap_or(0.0))
    }
}

/// `repr(float)` / `str(float)` (CPython's 'r' format, shortest round-tripping decimal, at most 17
/// significant digits) -- ported from `crates/ops-core/src/py/text.rs::float_repr` (kept local
/// rather than taken as a dependency; see docs/RUST_PORT.md section 3, "Attempted, then reverted").
fn float_repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let sci = format!("{x:e}"); // shortest round-trip digits, e.g. "-1.5e16", "0e0"
    let (neg, sci) = match sci.strip_prefix('-') {
        Some(r) => (true, r),
        None => (false, sci.as_str()),
    };
    let (mant, exp) = sci.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    let digits: String = mant.chars().filter(|c| *c != '.').collect();
    let decpt = exp + 1;
    let mut s = String::new();
    if neg {
        s.push('-');
    }
    if decpt <= -4 || decpt > 16 {
        let mut d = digits.chars();
        s.push(d.next().unwrap());
        let rest: String = d.collect();
        if !rest.is_empty() {
            s.push('.');
            s.push_str(&rest);
        }
        let e = decpt - 1;
        s.push('e');
        s.push(if e < 0 { '-' } else { '+' });
        s.push_str(&format!("{:02}", e.abs()));
    } else if decpt <= 0 {
        s.push_str("0.");
        for _ in 0..(-decpt) {
            s.push('0');
        }
        s.push_str(&digits);
    } else if decpt as usize >= digits.len() {
        s.push_str(&digits);
        for _ in 0..(decpt as usize - digits.len()) {
            s.push('0');
        }
        s.push_str(".0");
    } else {
        s.push_str(&digits[..decpt as usize]);
        s.push('.');
        s.push_str(&digits[decpt as usize..]);
    }
    s
}

/// True when `v == True` or `v == False` under Python equality: `bool` is an `int` subtype, so
/// `True == 1` and `False == 0` (and `== 1.0` / `== 0.0`) are also true. Used wherever the oracle
/// writes `x not in (True, False)` rather than `isinstance(x, bool)` -- the two are NOT the same
/// refusal (`assertions.py:192`'s `enforce` accepts `1`/`0`; `config.py`'s `promotion_log` does not,
/// because it also has an explicit `isinstance(..., bool)` check).
pub fn py_equals_bool(v: &Value) -> bool {
    match v {
        Value::Bool(_) => true,
        Value::Number(n) => n.as_f64().map(|f| f == 0.0 || f == 1.0).unwrap_or(false),
        _ => false,
    }
}

/// `bool(v)` for a `v` already known to equal `True` or `False` (`py_equals_bool` passed) --
/// `bool(1) == True`, `bool(0) == False`, same as the literal bools.
fn py_truthy_bool(v: &Value) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
        _ => false,
    }
}

/// Python `isinstance(x, int)` for the JSON-shaped value space: true for `bool` (a subtype of `int`)
/// and for a whole-numbered `Number`, false for a `float`-shaped `Number` (`5.0` parses as a JSON
/// float, `is_i64()`/`is_u64()` is false) or anything else. Returns the Python `int(x)` value.
pub fn py_int_like(v: &Value) -> Option<i64> {
    match v {
        Value::Bool(b) => Some(if *b { 1 } else { 0 }),
        Value::Number(n) if n.is_i64() || n.is_u64() => n.as_i64(),
        _ => None,
    }
}

/// `ID_RE.match(s)` (`config.py:25`, `^[a-z0-9_]+$`). Python's `$` (no `re.MULTILINE`) matches at
/// the end of the string OR immediately before a SINGLE trailing `\n` -- `re.match(r"^x$", "x\n")`
/// succeeds. `"demo\n"` is therefore a legal `instance.id`, `unit` name, gate id, lens name, table
/// name, field name, access-role name and frame-node id (every ID_RE call site); only a genuinely
/// embedded newline (more than the one trailing one) still fails, because it is not in the
/// `[a-z0-9_]+` character class.
fn is_id(s: &str) -> bool {
    let body = s.strip_suffix('\n').unwrap_or(s);
    !body.is_empty() && body.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
}

fn sorted_unique_strs<'a>(items: impl Iterator<Item = &'a str>) -> Vec<String> {
    let mut v: Vec<String> = items.map(str::to_string).collect();
    v.sort();
    v.dedup();
    v
}

fn is_truthy_nonempty(v: Option<&Value>) -> bool {
    match v {
        None => false,
        Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
    }
}

// ---- graph-shape helpers (config.py module-level functions) ---------------------------------------

fn declared_section<'a>(data: &'a Value, section: &str) -> Option<&'a Map<String, Value>> {
    data.get(section).and_then(Value::as_object)
}

/// `groundworks/config.py:136 _field_spec`.
fn field_spec(value: &Value) -> (Option<Value>, Option<String>) {
    let value = if let Value::String(s) = value {
        let mut m = Map::new();
        m.insert("type".to_string(), Value::String(s.clone()));
        Value::Object(m)
    } else {
        value.clone()
    };
    let Some(obj) = value.as_object() else {
        return (None, Some("must be a type name or a mapping with a type".to_string()));
    };
    let unknown = sorted_unique_strs(obj.keys().map(String::as_str).filter(|k| !FIELD_KEYS.contains(k)));
    if !unknown.is_empty() {
        let allowed = sorted_unique_strs(FIELD_KEYS.iter().copied());
        return (
            None,
            Some(format!("unknown key(s) {}; allowed: {}", unknown.join(", "), allowed.join(", "))),
        );
    }
    let kind = obj.get("type").and_then(Value::as_str);
    if !kind.map(|k| FIELD_TYPES.contains(&k)).unwrap_or(false) {
        return (
            None,
            Some(format!(
                "type must be one of {} (got {})",
                FIELD_TYPES.join(", "),
                py_repr_value(obj.get("type").unwrap_or(&Value::Null))
            )),
        );
    }
    let kind = kind.unwrap().to_string();
    let one_of = obj.get("one_of").filter(|v| !v.is_null());
    if let Some(one_of_v) = one_of {
        let arr = one_of_v.as_array();
        if arr.map(|a| a.is_empty()).unwrap_or(true) {
            return (None, Some("one_of must be a non-empty list of values".to_string()));
        }
        let arr = arr.unwrap();
        let mut seen = std::collections::HashSet::new();
        for v in arr {
            if !seen.insert(py_str_of(v)) {
                return (None, Some("one_of has duplicates".to_string()));
            }
        }
    }
    let optional = obj.get("optional").cloned().unwrap_or(Value::Bool(false));
    // `_field_spec`: `optional not in (True, False)` -- accepts `1`/`0` too (bool-is-int); the
    // stored value is then `bool(optional)`, so `optional: 1` becomes `true` in the spec.
    if !py_equals_bool(&optional) {
        return (None, Some("optional must be true or false".to_string()));
    }
    let mut spec = Map::new();
    spec.insert("type".to_string(), Value::String(kind));
    spec.insert("one_of".to_string(), one_of.cloned().unwrap_or(Value::Null));
    spec.insert("optional".to_string(), Value::Bool(py_truthy_bool(&optional)));
    (Some(Value::Object(spec)), None)
}

/// `groundworks/config.py:169 declared_tables_of`: name -> {field: spec}.
pub fn declared_tables_of(data: &Value) -> Map<String, Value> {
    let mut out = Map::new();
    if let Some(declared) = declared_section(data, "tables") {
        for (name, body) in declared {
            let mut fields = Map::new();
            if let Some(f) = body.as_object().and_then(|b| b.get("fields")).and_then(Value::as_object) {
                for (field, value) in f {
                    if let (Some(spec), None) = field_spec(value) {
                        fields.insert(field.clone(), spec);
                    }
                }
            }
            out.insert(name.clone(), Value::Object(fields));
        }
    }
    out
}

/// `groundworks/config.py:183 declared_edges_of`: name -> {"from", "to", "fields": {field: spec}}.
pub fn declared_edges_of(data: &Value) -> Map<String, Value> {
    let mut out = Map::new();
    if let Some(declared) = declared_section(data, "edges") {
        for (name, body) in declared {
            let body_obj = body.as_object();
            let mut fields = Map::new();
            if let Some(f) = body_obj.and_then(|b| b.get("fields")).and_then(Value::as_object) {
                for (field, value) in f {
                    if let (Some(spec), None) = field_spec(value) {
                        fields.insert(field.clone(), spec);
                    }
                }
            }
            let mut entry = Map::new();
            entry.insert("from".to_string(), body_obj.and_then(|b| b.get("from")).cloned().unwrap_or(Value::Null));
            entry.insert("to".to_string(), body_obj.and_then(|b| b.get("to")).cloned().unwrap_or(Value::Null));
            entry.insert("fields".to_string(), Value::Object(fields));
            out.insert(name.clone(), Value::Object(entry));
        }
    }
    out
}

/// `groundworks/config.py:197 node_tables_of`.
pub fn node_tables_of(data: &Value) -> Vec<String> {
    let mut out: Vec<String> = BUILT_IN_NODE_TABLES.iter().map(|s| s.to_string()).collect();
    for name in declared_tables_of(data).keys() {
        if !BUILT_IN_NODE_TABLES.contains(&name.as_str()) {
            out.push(name.clone());
        }
    }
    out
}

/// `groundworks/config.py:202 edge_map_of`: edge name -> [from, to].
pub fn edge_map_of(data: &Value) -> Map<String, Value> {
    let mut out = Map::new();
    for (name, from, to) in BUILT_IN_EDGES {
        out.insert(
            name.to_string(),
            Value::Array(vec![Value::String(from.to_string()), Value::String(to.to_string())]),
        );
    }
    for (name, body) in declared_edges_of(data) {
        if out.contains_key(&name) {
            continue;
        }
        let from = body.get("from").cloned().unwrap_or(Value::Null);
        let to = body.get("to").cloned().unwrap_or(Value::Null);
        if is_truthy_nonempty(Some(&from)) && is_truthy_nonempty(Some(&to)) {
            out.insert(name, Value::Array(vec![Value::String(py_str_of(&from)), Value::String(py_str_of(&to))]));
        }
    }
    out
}

/// `groundworks/config.py:118 independence_of`.
pub fn independence_of(rule: &Value) -> Option<Value> {
    let value = rule.get("independent").cloned().unwrap_or(Value::Bool(false));
    if value == Value::Bool(true) {
        let mut m = Map::new();
        m.insert("between".to_string(), Value::String("credential".to_string()));
        m.insert("own_evidence".to_string(), Value::Bool(false));
        return Some(Value::Object(m));
    }
    if let Some(obj) = value.as_object().filter(|o| !o.is_empty()) {
        let between = obj.get("between").and_then(Value::as_str).unwrap_or("credential").to_string();
        let own_evidence = obj.get("own_evidence") == Some(&Value::Bool(true));
        let mut m = Map::new();
        m.insert("between".to_string(), Value::String(between));
        m.insert("own_evidence".to_string(), Value::Bool(own_evidence));
        return Some(Value::Object(m));
    }
    None
}

/// `groundworks/config.py:757 expand_where`.
pub fn expand_where(data: &Value, where_: Option<&str>) -> String {
    fn lit(values: &[Value]) -> String {
        let items: Vec<String> =
            values.iter().map(|v| format!("'{}'", py_str_of(v).replace('\'', "\\'"))).collect();
        format!("[{}]", items.join(", "))
    }
    let confirming: Vec<Value> = data
        .get("fidelity")
        .and_then(Value::as_object)
        .and_then(|f| f.get("confirming"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let usable: Vec<Value> = data
        .get("status")
        .and_then(Value::as_object)
        .and_then(|s| s.get("usable"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    where_.unwrap_or("").replace("$confirming", &lit(&confirming)).replace("$usable", &lit(&usable))
}

// ---- validate() and its helpers -------------------------------------------------------------------

pub fn validate(data: Value) -> R<Value> {
    let mut problems: Vec<String> = Vec::new();
    let Some(data_obj) = data.as_object() else {
        return Err(ConfigError(format!(
            "config must be a mapping (got {})",
            type_name(&data)
        )));
    };
    let unknown = sorted_unique_strs(data_obj.keys().map(String::as_str).filter(|k| !TOP_KEYS.contains(k)));
    if !unknown.is_empty() {
        let allowed = sorted_unique_strs(TOP_KEYS.iter().copied());
        problems.push(format!(
            "unknown top-level key(s): {}; allowed: {}",
            unknown.join(", "),
            allowed.join(", ")
        ));
    }
    for key in REQUIRED_TOP {
        if !data_obj.contains_key(*key) {
            problems.push(format!("missing required section: {key}"));
        }
    }
    if !problems.is_empty() {
        return Err(ConfigError(problems.join("; ")));
    }

    let inst = &data["instance"];
    let inst_obj = inst.as_object();
    let id_ok = inst_obj.map(|o| is_truthy_nonempty(o.get("id"))).unwrap_or(false);
    let goal_ok = inst_obj.map(|o| is_truthy_nonempty(o.get("goal"))).unwrap_or(false);
    match inst_obj {
        Some(obj) if id_ok && goal_ok => {
            let id_str = py_str_of(&obj["id"]);
            if !is_id(&id_str) {
                problems.push(format!("instance.id {} must match [a-z0-9_]+", py_repr_value(&inst["id"])));
            }
        }
        _ => problems.push("instance needs an id and a goal".to_string()),
    }

    let units = &data["units"];
    let units_arr = units.as_array();
    if units_arr.map(|a| a.is_empty()).unwrap_or(true) {
        problems.push("units must be a non-empty list".to_string());
    } else {
        let units_arr = units_arr.unwrap();
        let mut names: Vec<String> = Vec::new();
        // `config.py:557`: `len(set(names)) != len(names)` where `names` holds each unit's RAW
        // `name` value (never stringified) -- `set()` dedups by Python equality/hash, so
        // `[{"name": 1}, {"name": true}]` collides (bool-is-int) but `{"name": "1"}` would not.
        // Kept alongside the display-friendly `names: Vec<String>` above.
        let mut raw_names: Vec<Value> = Vec::new();
        for unit in units_arr {
            let unit_obj = unit.as_object();
            let name_ok = unit_obj.map(|o| is_truthy_nonempty(o.get("name"))).unwrap_or(false);
            if unit_obj.is_none() || !name_ok {
                problems.push("each unit needs a name".to_string());
                continue;
            }
            let unit_obj = unit_obj.unwrap();
            let name = py_str_of(&unit_obj["name"]);
            names.push(name.clone());
            raw_names.push(unit_obj["name"].clone());
            if !is_id(&name) {
                problems.push(format!("unit name {} must match [a-z0-9_]+", py_repr_value(&unit_obj["name"])));
            }
            if !is_truthy_nonempty(unit_obj.get("locator_fields")) {
                problems.push(format!("unit {name} needs locator_fields a program can resolve"));
            }
            // `config.py:553-555`: `size = unit.get("chunk_size", 0); if not isinstance(size, int) or
            // size <= 0`. `isinstance(True, int)` is True in Python (bool is an int subtype), so
            // `chunk_size: true` is ACCEPTED as 1; only `chunk_size: false` (0) is refused.
            let size_ok = unit_obj.get("chunk_size").and_then(py_int_like).map(|v| v > 0).unwrap_or(false);
            if !size_ok {
                problems.push(format!("unit {name} needs a positive chunk_size"));
            }
        }
        if py_set_len(&raw_names) != raw_names.len() {
            problems.push("unit names must be unique".to_string());
        }
    }

    let fid = data.get("fidelity");
    let fid_obj = fid.and_then(Value::as_object);
    let order_ok = fid_obj.map(|o| is_truthy_nonempty(o.get("order"))).unwrap_or(false);
    let confirming_ok = fid_obj.map(|o| is_truthy_nonempty(o.get("confirming"))).unwrap_or(false);
    if !order_ok || !confirming_ok || fid_obj.is_none() {
        problems.push("fidelity needs order (best first) and confirming".to_string());
    } else if let Some(fid_obj) = fid_obj {
        // `config.py:559-565`: `fid["order"]`/`fid["confirming"]` are used AS DECLARED, not coerced to
        // a list first -- a string value iterates by character and its `len()`/`in` follow string
        // semantics (substring, code-point count), not list semantics. `py_iter_elems`/`py_len`/
        // `py_contains` give each raw value (`Value::Array` or `Value::String`) the same duck-typed
        // behaviour Python's `for`/`len`/`in` give it here.
        let order_val = fid_obj.get("order").cloned().unwrap_or(Value::Null);
        let confirming_val = fid_obj.get("confirming").cloned().unwrap_or(Value::Null);
        let confirming_elems = py_iter_elems(&confirming_val);
        let missing: Vec<String> = confirming_elems
            .iter()
            .filter(|g| !py_contains(&order_val, g))
            .map(py_str_of)
            .collect();
        if !missing.is_empty() {
            problems.push(format!("fidelity.confirming not in order: {}", missing.join(", ")));
        }
        if py_len(&confirming_val) >= py_len(&order_val) {
            problems.push("fidelity.confirming cannot be every grade: some grade must be unable to establish".to_string());
        }
        if !is_truthy_nonempty(fid_obj.get("agent_grades")) {
            problems.push("fidelity needs agent_grades: the grades that are model output".to_string());
        } else {
            let agent_grades_val = fid_obj.get("agent_grades").cloned().unwrap_or(Value::Null);
            let agent_grades_strs: Vec<String> = py_iter_elems(&agent_grades_val).iter().map(py_str_of).collect();
            let confirming_strs: Vec<String> = confirming_elems.iter().map(py_str_of).collect();
            let overlap = sorted_unique_strs(confirming_strs.iter().map(String::as_str).filter(|g| agent_grades_strs.iter().any(|a| a == g)));
            if !overlap.is_empty() {
                problems.push(format!(
                    "agent output may never confirm: {} is in both confirming and agent_grades",
                    overlap.join(", ")
                ));
            }
        }
    }

    let st = data.get("status");
    let st_obj = st.and_then(Value::as_object);
    let values_ok = st_obj.map(|o| is_truthy_nonempty(o.get("values"))).unwrap_or(false);
    let usable_ok = st_obj.map(|o| is_truthy_nonempty(o.get("usable"))).unwrap_or(false);
    if !values_ok || !usable_ok || st_obj.is_none() {
        problems.push("status needs values and usable".to_string());
    } else if let Some(st_obj) = st_obj {
        // Same duck-typing as `fidelity.order`/`fidelity.confirming` above (`config.py:576-584`
        // uses `st["values"]`/`st["usable"]` as declared, not coerced to a list).
        let values_val = st_obj.get("values").cloned().unwrap_or(Value::Null);
        let usable_val = st_obj.get("usable").cloned().unwrap_or(Value::Null);
        let missing: Vec<String> = py_iter_elems(&usable_val)
            .iter()
            .filter(|s| !py_contains(&values_val, s))
            .map(py_str_of)
            .collect();
        if !missing.is_empty() {
            problems.push(format!("status.usable not in values: {}", missing.join(", ")));
        }
        if py_len(&usable_val) >= py_len(&values_val) {
            problems.push("status.usable cannot be every status: 'true but unusable' must be expressible".to_string());
        }
    }

    let vocab = data.get("vocabulary");
    if !vocab.map(Value::is_object).unwrap_or(false) {
        problems.push("vocabulary must be a mapping".to_string());
    } else {
        let vocab_obj = vocab.unwrap().as_object().unwrap();
        for key in ["tiers", "directions", "issues", "roles"] {
            let values = vocab_obj.get(key);
            if !is_truthy_nonempty(values) {
                problems.push(format!("vocabulary.{key} must be a non-empty list"));
            } else if let Some(arr) = values.and_then(Value::as_array) {
                let strs: Vec<String> = arr.iter().map(py_str_of).collect();
                let uniq = sorted_unique_strs(strs.iter().map(String::as_str));
                if uniq.len() != strs.len() {
                    problems.push(format!("vocabulary.{key} has duplicates"));
                }
            }
        }
        // `config.py:595`: `tiers = vocab.get("tiers") or []` -- the raw declared value (a string
        // iterates by character below, matching `fidelity`/`status` above), not coerced to a list.
        let tiers_raw = vocab_obj.get("tiers").cloned().unwrap_or(Value::Null);
        let tiers: Vec<Value> = if is_truthy_nonempty(Some(&tiers_raw)) { py_iter_elems(&tiers_raw) } else { Vec::new() };
        if !tiers.is_empty() {
            // `config.py:597`: `if tiers and list(tiers) != sorted(tiers):`. `sorted()` compares
            // adjacent elements as it scans for the initial run; the FIRST incomparable pair (e.g. a
            // number next to a string) raises `TypeError` straight out of Python's `validate()` --
            // an outcome distinct from a `ConfigError` refusal, not a bug to swallow as "already
            // sorted" (`gate-discipline`/the sweep's rule 14). `tiers[i] < tiers[i-1]` is the
            // direction sorted() actually checks; mirrored here pairwise in the same order.
            for w in tiers.windows(2) {
                if let Err((a_type, b_type)) = py_less_than(&w[1], &w[0]) {
                    return Err(ConfigError {
                        kind: "TypeError",
                        message: format!("'<' not supported between instances of '{a_type}' and '{b_type}'"),
                    });
                }
            }
            let is_sorted = tiers.windows(2).all(|w| py_less_than(&w[1], &w[0]) == Ok(false));
            if !is_sorted {
                problems.push("vocabulary.tiers must ascend".to_string());
            }
            // `tiers[0] != 0` in Python: `False == 0` and `0.0 == 0` both satisfy it (bool-is-int,
            // and numeric equality across int/float).
            if !py_equals_number(&tiers[0], 0.0) {
                problems.push("vocabulary.tiers must start at 0 (noise)".to_string());
            }
        }
    }

    validate_graph_shape(&data, &mut problems);

    let gates_val = data.get("gates");
    let gates_arr = gates_val.and_then(Value::as_array);
    if gates_arr.map(|a| a.is_empty()).unwrap_or(true) {
        problems.push("gates must be a non-empty list: a graph with no gate is not gated".to_string());
    } else {
        let gates_arr = gates_arr.unwrap().clone();
        let mut ids: Vec<String> = Vec::new();
        for rule in &gates_arr {
            let rule_obj = rule.as_object();
            let has_id = rule_obj.map(|o| is_truthy_nonempty(o.get("id"))).unwrap_or(false);
            if rule_obj.is_none() || !has_id {
                problems.push("each gate rule needs an id".to_string());
                continue;
            }
            let rule_obj = rule_obj.unwrap();
            let id = py_str_of(&rule_obj["id"]);
            ids.push(id.clone());
            for key in ["table", "field", "value"] {
                if !rule_obj.contains_key(key) {
                    problems.push(format!("gate {id} needs {key}"));
                }
            }
            let independent = independence_value(rule, &mut problems);
            let has_requires = is_truthy_nonempty(rule_obj.get("requires"));
            let has_forbids = is_truthy_nonempty(rule_obj.get("forbids"));
            if !has_requires && !has_forbids && independent.is_none() {
                problems.push(format!("gate {id} requires or forbids nothing"));
            }
            validate_gate_domains(&data, rule, &mut problems);
            if let Some(independent) = &independent {
                validate_independent(&data, rule, &mut problems);
                validate_independence_mode(&data, rule, independent, &mut problems);
            }
        }
        let uniq = sorted_unique_strs(ids.iter().map(String::as_str));
        if uniq.len() != ids.len() {
            problems.push("gate ids must be unique".to_string());
        }
    }

    if let Some(meanings) = data.get("verdicts") {
        let meanings_obj = meanings.as_object().filter(|o| !o.is_empty());
        if let Some(meanings_obj) = meanings_obj {
            let unknown = sorted_unique_strs(
                meanings_obj.keys().map(String::as_str).filter(|k| !gates_support::CRITIQUE_VERDICTS.contains(k)),
            );
            if !unknown.is_empty() {
                problems.push(format!(
                    "verdicts: unknown verdict(s) {}; the engine's are {}",
                    unknown.join(", "),
                    gates_support::CRITIQUE_VERDICTS.join(", ")
                ));
            }
            let undefined: Vec<&str> =
                gates_support::CRITIQUE_VERDICTS.iter().copied().filter(|v| !meanings_obj.contains_key(*v)).collect();
            if !undefined.is_empty() {
                problems.push(format!(
                    "verdicts: {} left undefined. Define every verdict or none: a partly defined set is the \
case where a role reaches for the one with no meaning attached",
                    undefined.join(", ")
                ));
            }
            for (name, text) in meanings_obj {
                let ok = text.as_str().map(|s| s.trim().len() >= 10).unwrap_or(false);
                if !ok {
                    problems.push(format!(
                        "verdicts.{name}: say in a sentence what it means here; {} is not a definition a role can act on",
                        py_repr_value(text)
                    ));
                }
            }
        } else {
            problems.push("verdicts must be a mapping of verdict name -> what it means here".to_string());
        }
    }

    // `config.py:647-649`: `owned = data["human_owned"] or []; ... if k not in owned`. `owned` keeps
    // whatever TYPE `human_owned` was declared as (falsy collapses to `[]`); Python's `in` on that
    // value is type-dependent -- a STRING checks substring containment, not element equality, so
    // `human_owned: "gates and fidelity and status and evaluator and privacy"` is ACCEPTED for every
    // key that appears as a substring. `owned()` below reproduces that per-type `in`.
    let owned = human_owned_value(&data);
    let mut missing: Vec<String> =
        MUST_BE_HUMAN_OWNED.iter().filter(|k| !human_owned_contains(&owned, k)).map(|s| s.to_string()).collect();
    missing.extend(
        HUMAN_OWNED_IF_PRESENT
            .iter()
            .filter(|k| data_obj.contains_key(**k) && !human_owned_contains(&owned, k))
            .map(|s| s.to_string()),
    );
    if !missing.is_empty() {
        problems.push(format!("human_owned must include {} (CLAUDE.md rule 6)", missing.join(", ")));
    }

    if let Some(acc) = data.get("access") {
        let acc_obj = acc.as_object();
        let roles = acc_obj.and_then(|a| a.get("roles")).and_then(Value::as_object).filter(|r| !r.is_empty());
        if let Some(roles) = roles {
            let known_tables: Vec<String> = {
                let mut v = node_tables_of(&data);
                v.extend(edge_map_of(&data).keys().cloned());
                v
            };
            for (name, spec) in roles {
                if !is_id(name) {
                    problems.push(format!("access role {} must match [a-z0-9_]+", py_repr_str(name)));
                }
                let spec_obj = spec.as_object();
                let read_ok = spec_obj.map(|o| is_truthy_nonempty(o.get("read")) && o.get("read").unwrap().is_array()).unwrap_or(false);
                let Some(spec_obj) = spec_obj.filter(|_| read_ok) else {
                    problems.push(format!("access role {name} needs read: a non-empty list of tables"));
                    continue;
                };
                if let Some(write) = spec_obj.get("write") {
                    if !write.is_array() {
                        problems.push(format!("access role {name}: write must be a list of tables"));
                        continue;
                    }
                }
                let mut named: Vec<String> = spec_obj.get("read").and_then(Value::as_array).map(|a| a.iter().map(py_str_of).collect::<Vec<_>>()).unwrap_or_default();
                named.extend(spec_obj.get("write").and_then(Value::as_array).map(|a| a.iter().map(py_str_of).collect::<Vec<_>>()).unwrap_or_default());
                let unknown = sorted_unique_strs(named.iter().map(String::as_str).filter(|t| !known_tables.contains(&t.to_string())));
                if !unknown.is_empty() {
                    problems.push(format!(
                        "access role {name} names unknown table(s) {}; known: {}",
                        unknown.join(", "),
                        known_tables.join(", ")
                    ));
                }
                let writes: Vec<String> = spec_obj.get("write").and_then(Value::as_array).map(|a| a.iter().map(py_str_of).collect()).unwrap_or_default();
                if writes.contains(&"label".to_string()) && name != "loader" {
                    problems.push(format!("access role {name} may not write label: only the loader writes labels"));
                }
                if let Some(lb) = spec_obj.get("lens_bound") {
                    // `spec.get("lens_bound") not in (None, True, False)`: null passes (not present /
                    // explicit `~`), and `1`/`0` pass too (bool-is-int against `True`/`False`).
                    if !lb.is_null() && !py_equals_bool(lb) {
                        problems.push(format!("access role {name}: lens_bound must be true or false"));
                    }
                }
            }
        } else {
            problems.push(
                "access needs roles: a mapping of role name -> {read: [...], write: [...], lens_bound}".to_string(),
            );
        }
    }

    validate_unique(&data, &mut problems);
    validate_assertions(&data, &mut problems);
    validate_audit_tables(&data, &mut problems);
    originals_support::config_problems(&data, &mut problems);
    validate_labels(&data, &mut problems);

    let ev = data.get("evaluator");
    let ev_ok = ev.and_then(Value::as_object).map(|o| is_truthy_nonempty(o.get("regression_file"))).unwrap_or(false);
    if !ev.map(Value::is_object).unwrap_or(false) || !ev_ok {
        problems.push("evaluator needs a regression_file: the errors this project has actually made".to_string());
    }

    validate_lenses(&data, &mut problems);

    if !problems.is_empty() {
        return Err(ConfigError(format!("config invalid:\n  - {}", problems.join("\n  - "))));
    }
    Ok(data)
}

fn type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) => if n.is_i64() || n.is_u64() { "int" } else { "float" },
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Python `a < b` for the two shapes `vocabulary.tiers` may legally and illegally hold: numbers
/// (`bool` counts, bool-is-int) compare numerically; strings compare lexicographically; anything
/// else is a `TypeError` in real Python, returned here as `Err((type(a), type(b)))` rather than
/// silently treated as "already in order" -- `sorted([0, "a"])` does not return `[0, "a"]`, it raises.
fn py_less_than(a: &Value, b: &Value) -> Result<bool, (String, String)> {
    fn as_num(v: &Value) -> Option<f64> {
        match v {
            Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
            Value::Number(n) => n.as_f64(),
            _ => None,
        }
    }
    match (as_num(a), as_num(b)) {
        (Some(x), Some(y)) => Ok(x < y),
        _ => match (a, b) {
            (Value::String(x), Value::String(y)) => Ok(x < y),
            _ => Err((type_name(a).to_string(), type_name(b).to_string())),
        },
    }
}

/// Python `v == n` for a numeric literal `n`, matching `bool`-is-`int` (`False == 0`) and
/// int/float cross-type equality (`0.0 == 0`).
fn py_equals_number(v: &Value, n: f64) -> bool {
    match v {
        Value::Bool(b) => (if *b { 1.0 } else { 0.0 }) == n,
        Value::Number(x) => x.as_f64().map(|x| x == n).unwrap_or(false),
        _ => false,
    }
}

/// Python `a == b`, matching `bool`-is-`int` and int/float cross-type equality; everything else
/// (including a string compared to a number) falls back to `serde_json::Value`'s structural
/// equality, which agrees with Python for the only other case this schema exercises: two strings.
fn py_value_eq(a: &Value, b: &Value) -> bool {
    fn as_num(v: &Value) -> Option<f64> {
        match v {
            Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
            Value::Number(n) => n.as_f64(),
            _ => None,
        }
    }
    match (as_num(a), as_num(b)) {
        (Some(x), Some(y)) => x == y,
        _ => a == b,
    }
}

/// Python `for x in v`: a list yields its elements; a string yields its characters, each as its own
/// one-character string (`list("012") == ["0", "1", "2"]`). Every other JSON shape a raw config value
/// can take here (a mapping, a number, a bool, null) does not appear behind a `for`/`list()` call in
/// `config.py`'s `fidelity`/`status`/`vocabulary.tiers` checks without a dict/list guard in front of
/// it first, so this yields nothing for them rather than guessing at Python's `TypeError`.
fn py_iter_elems(v: &Value) -> Vec<Value> {
    match v {
        Value::Array(items) => items.clone(),
        Value::String(s) => s.chars().map(|c| Value::String(c.to_string())).collect(),
        _ => Vec::new(),
    }
}

/// Python `len(v)` for the shapes this schema puts through it: a list's element count, a string's
/// length in Unicode code points (what Python's `len(str)` counts), or a mapping's key count.
fn py_len(v: &Value) -> usize {
    match v {
        Value::Array(items) => items.len(),
        Value::String(s) => s.chars().count(),
        Value::Object(m) => m.len(),
        _ => 0,
    }
}

/// Python `item in container`: a list checks for an equal element (`py_value_eq`); a string checks
/// substring containment, which Python only allows when `item` is itself a string (comparing a
/// non-string probe against a string container would raise `TypeError` in Python -- every caller
/// here only probes with a value that came out of `py_iter_elems` on a sibling field, so it is
/// always array-element-or-one-character-string; a non-string probe is treated as not contained
/// rather than reproducing the crash); a mapping checks its keys.
fn py_contains(container: &Value, item: &Value) -> bool {
    match container {
        Value::Array(items) => items.iter().any(|v| py_value_eq(v, item)),
        Value::String(s) => match item {
            Value::String(t) => s.contains(t.as_str()),
            _ => false,
        },
        Value::Object(m) => match item {
            Value::String(k) => m.contains_key(k),
            _ => false,
        },
        _ => false,
    }
}

/// Python `len(set(items))` over RAW (not stringified) values, using `py_value_eq` so `True` and `1`
/// collide the way a real Python `set` would (bool-is-int hashes/compares equal to the matching int)
/// while a string like `"1"` does not collide with either. O(n^2) is fine: this only ever runs over a
/// single instance's unit list.
fn py_set_len(items: &[Value]) -> usize {
    let mut uniq: Vec<&Value> = Vec::new();
    for item in items {
        if !uniq.iter().any(|u| py_value_eq(u, item)) {
            uniq.push(item);
        }
    }
    uniq.len()
}

fn validate_labels(data: &Value, problems: &mut Vec<String>) {
    let Some(section) = data.get("labels") else { return };
    let Some(section_obj) = section.as_object() else {
        problems.push(
            "labels must be a mapping: {quote_min_words: <int>, quote_distinct_units: {<unit type>: <int>}}"
                .to_string(),
        );
        return;
    };
    let allowed = ["quote_min_words", "quote_distinct_units"];
    let unknown = sorted_unique_strs(section_obj.keys().map(String::as_str).filter(|k| !allowed.contains(k)));
    if !unknown.is_empty() {
        problems.push(format!(
            "labels: unknown key(s) {}; allowed: quote_min_words, quote_distinct_units",
            unknown.join(", ")
        ));
    }
    if let Some(v) = section_obj.get("quote_min_words") {
        if !is_whole_number(v) {
            problems.push(format!("labels.quote_min_words must be a whole number of at least 1, not {}", py_repr_value(v)));
        }
    }
    if let Some(distinct) = section_obj.get("quote_distinct_units") {
        if !distinct.is_null() {
            let Some(distinct_obj) = distinct.as_object() else {
                problems.push("labels.quote_distinct_units must map a unit type to a whole number".to_string());
                return;
            };
            let types: Vec<String> = data
                .get("units")
                .and_then(Value::as_array)
                .map(|units| units.iter().filter_map(|u| u.as_object()).filter_map(|o| o.get("name")).map(py_str_of).collect())
                .unwrap_or_default();
            for (name, limit) in distinct_obj {
                if !types.contains(name) {
                    problems.push(format!(
                        "labels.quote_distinct_units names unit type {}; known: {}",
                        py_repr_str(name),
                        types.join(", ")
                    ));
                }
                if !is_whole_number(limit) {
                    problems.push(format!(
                        "labels.quote_distinct_units.{name} must be a whole number of at least 1, not {}",
                        py_repr_value(limit)
                    ));
                }
            }
        }
    }
}

/// `data["human_owned"] or []` -- the raw section value, with anything Python treats as falsy
/// (missing, `null`, `false`, `0`, `""`, `[]`, `{}`) replaced by an empty list.
fn human_owned_value(data: &Value) -> Value {
    let raw = data.get("human_owned").cloned().unwrap_or(Value::Null);
    if is_truthy_nonempty(Some(&raw)) {
        raw
    } else {
        Value::Array(vec![])
    }
}

/// Python `key in owned` for whatever shape `human_owned` was declared as: a list checks for an equal
/// element, a string checks substring containment, a mapping checks its keys. Anything else (a lone
/// number or bool, which Python's `for`/`in` cannot iterate at all and would raise `TypeError` on) is
/// treated as not containing the key -- config.py itself would crash first in that case.
fn human_owned_contains(owned: &Value, key: &str) -> bool {
    match owned {
        Value::Array(items) => items.iter().any(|v| matches!(v, Value::String(s) if s == key)),
        Value::String(s) => s.contains(key),
        Value::Object(m) => m.contains_key(key),
        _ => false,
    }
}

fn is_whole_number(v: &Value) -> bool {
    matches!(v, Value::Number(n) if n.is_i64() && n.as_i64().unwrap() >= 1)
}

fn independence_value(rule: &Value, problems: &mut Vec<String>) -> Option<Value> {
    let id = rule.get("id").map(py_str_of).unwrap_or_default();
    let where_ = format!("gate {id}");
    let value = rule.get("independent").cloned().unwrap_or(Value::Bool(false));
    if value == Value::Bool(true) || value == Value::Bool(false) {
        return independence_of(rule);
    }
    let Some(obj) = value.as_object().filter(|o| !o.is_empty()) else {
        problems.push(format!(
            "{where_}: independent must be true or false, or a mapping with between and/or own_evidence (got {})",
            py_repr_value(&value)
        ));
        return None;
    };
    let unknown = sorted_unique_strs(obj.keys().map(String::as_str).filter(|k| !INDEPENDENCE_KEYS.contains(k)));
    if !unknown.is_empty() {
        let allowed = sorted_unique_strs(INDEPENDENCE_KEYS.iter().copied());
        problems.push(format!("{where_}: independent has unknown key(s) {}; allowed: {}", unknown.join(", "), allowed.join(", ")));
    }
    let between = obj.get("between").cloned().unwrap_or(Value::String("credential".to_string()));
    let between_str = between.as_str().unwrap_or("");
    if !INDEPENDENCE_BETWEEN.contains(&between_str) {
        problems.push(format!(
            "{where_}: independent.between must be one of {} (got {})",
            INDEPENDENCE_BETWEEN.join(", "),
            py_repr_value(&between)
        ));
    }
    let own_evidence = obj.get("own_evidence").cloned().unwrap_or(Value::Bool(false));
    if own_evidence != Value::Bool(true) && own_evidence != Value::Bool(false) {
        problems.push(format!("{where_}: independent.own_evidence must be true or false (got {})", py_repr_value(&own_evidence)));
    }
    independence_of(rule)
}

fn validate_gate_domains(data: &Value, rule: &Value, problems: &mut Vec<String>) {
    let rule_obj = rule.as_object();
    let has_all = ["table", "field", "value"].iter().all(|k| rule_obj.map(|o| o.contains_key(*k)).unwrap_or(false));
    if !has_all {
        return;
    }
    let rule_obj = rule_obj.unwrap();
    let id = rule_obj.get("id").map(py_str_of).unwrap_or_default();
    let where_ = format!("gate {id}");
    let table = py_str_of(&rule_obj["table"]);
    let field = py_str_of(&rule_obj["field"]);
    let domain = assertions_support::field_domain(data, &table, &field);
    match &domain {
        None => problems.push(format!(
            "{where_}: {table}.{field} has no enumerated domain (no vocabulary list, no one_of, not a bool), so \
the datastore stores a misspelt value without a word and this gate never sees it; declare the field with \
one_of, or gate a field that has one"
        )),
        Some(domain) => {
            let value = &rule_obj["value"];
            let value_lit = gates_support::value_literal(value);
            let domain_lits: Vec<String> = domain.iter().map(gates_support::value_literal).collect();
            if !domain_lits.contains(&value_lit) {
                let domain_strs: Vec<String> = domain.iter().map(py_str_of).collect();
                problems.push(format!(
                    "{where_}: value {} is not one of {table}.{field}'s values ({}), so the gate could never fire",
                    py_repr_value(value),
                    domain_strs.join(", ")
                ));
            }
        }
    }
    let edges = edge_map_of(data);
    for kind in ["requires", "forbids"] {
        let empty = Vec::new();
        let clauses = rule_obj.get(kind).and_then(Value::as_array).unwrap_or(&empty);
        for clause in clauses {
            let Some(clause_obj) = clause.as_object() else { continue };
            let where_clause = clause_obj.get("where").and_then(Value::as_str);
            let has_where = where_clause.map(|w| !w.is_empty()).unwrap_or(false);
            let edge_name = clause_obj.get("edge").and_then(Value::as_str);
            let in_edges = edge_name.map(|e| edges.contains_key(e)).unwrap_or(false);
            if !has_where || !in_edges {
                continue;
            }
            let where_clause = where_clause.unwrap();
            let edge_name = edge_name.unwrap();
            let pair = edges.get(edge_name).unwrap().as_array().unwrap();
            let src = pair[0].as_str().unwrap();
            let dst = pair[1].as_str().unwrap();
            let other = if table == src {
                Some(dst)
            } else if table == dst {
                Some(src)
            } else {
                None
            };
            let expanded = expand_where(data, Some(where_clause));
            let terms = gates_support::where_terms(&expanded);
            let (Ok(terms), Some(other)) = (terms, other) else { continue };
            for (term_field, literals) in terms {
                let values = assertions_support::field_domain(data, other, &term_field);
                let said = format!("{where_}: a {kind} clause on {edge_name} compares {other}.{term_field}");
                match &values {
                    None => {
                        problems.push(format!(
                            "{said}, which has no enumerated domain, so a misspelt value there is never counted{}; \
declare the field with one_of",
                            if kind == "forbids" { " and the forbidden row is let through" } else { "" }
                        ));
                    }
                    Some(values) => {
                        let allowed: Vec<String> = values.iter().map(gates_support::value_literal).collect();
                        for literal in &literals {
                            let text = literal.trim();
                            let mapped = if text.len() >= 2 && text.starts_with('"') && text.ends_with('"') {
                                gates_support::value_literal(&Value::String(text[1..text.len() - 1].to_string()))
                            } else if text.eq_ignore_ascii_case("true") || text.eq_ignore_ascii_case("false") {
                                text.to_lowercase()
                            } else {
                                text.to_string()
                            };
                            if !allowed.contains(&mapped) {
                                let values_strs: Vec<String> = values.iter().map(py_str_of).collect();
                                problems.push(format!(
                                    "{said} with {literal}, which is not one of its values ({}), so the clause never matches",
                                    values_strs.join(", ")
                                ));
                            }
                        }
                    }
                }
            }
        }
    }
}

fn validate_independent(data: &Value, rule: &Value, problems: &mut Vec<String>) {
    let id = rule.get("id").map(py_str_of).unwrap_or_default();
    let where_ = format!("gate {id}");
    let table = py_str_of(rule.get("table").unwrap_or(&Value::Null));
    if !node_tables_of(data).contains(&table) {
        problems.push(format!(
            "{where_}: independent promotion is recorded on a node table's rows; {} is not one",
            py_repr_str(&table)
        ));
        return;
    }
    if declared_tables_of(data).get(&table).and_then(Value::as_object).map(|f| f.contains_key(INDEPENDENCE_FIELD)).unwrap_or(false) {
        problems.push(format!(
            "{where_}: tables.{table} declares a field {}, which the engine owns for independent promotion",
            py_repr_str(INDEPENDENCE_FIELD)
        ));
    }
    let access = data.get("access").and_then(Value::as_object);
    let roles = access.and_then(|a| a.get("roles")).and_then(Value::as_object);
    let Some(roles) = roles.filter(|r| !r.is_empty()) else {
        problems.push(format!(
            "{where_}: independent: true needs an access: section. Without record credentials every write \
comes from root, which has no identity to tell apart, so nothing could ever make this change"
        ));
        return;
    };
    let writers: Vec<&String> = roles
        .iter()
        .filter(|(_, spec)| {
            spec.as_object()
                .map(|o| {
                    o.get("write").and_then(Value::as_array).map(|w| w.iter().any(|v| py_str_of(v) == table)).unwrap_or(false)
                        && o.get("read").and_then(Value::as_array).map(|r| r.iter().any(|v| py_str_of(v) == table)).unwrap_or(false)
                })
                .unwrap_or(false)
        })
        .map(|(name, _)| name)
        .collect();
    if writers.is_empty() {
        problems.push(format!(
            "{where_}: independent: true, but no access role both reads and writes {table}, so no credential \
could propose or promote it"
        ));
    }
}

fn validate_independence_mode(data: &Value, rule: &Value, mode: &Value, problems: &mut Vec<String>) {
    let own_evidence = mode.get("own_evidence") == Some(&Value::Bool(true));
    if !own_evidence {
        return;
    }
    let id = rule.get("id").map(py_str_of).unwrap_or_default();
    let where_ = format!("gate {id}");
    let edges: Vec<String> = rule
        .get("requires")
        .and_then(Value::as_array)
        .map(|reqs| reqs.iter().filter_map(|c| c.as_object()).map(|c| py_str_of(c.get("edge").unwrap_or(&Value::Null))).collect())
        .unwrap_or_default();
    if edges.is_empty() {
        problems.push(format!(
            "{where_}: independent.own_evidence needs requires clauses: the promoter's own evidence is one of \
the links they count, and this rule counts none"
        ));
        return;
    }
    let table = py_str_of(rule.get("table").unwrap_or(&Value::Null));
    let access = data.get("access").and_then(Value::as_object);
    let roles = access.and_then(|a| a.get("roles")).and_then(Value::as_object);
    let Some(roles) = roles else { return };
    let edge_set: std::collections::HashSet<&String> = edges.iter().collect();
    let able = roles.iter().any(|(_, spec)| {
        spec.as_object()
            .map(|o| {
                let writes: Vec<String> = o.get("write").and_then(Value::as_array).map(|w| w.iter().map(py_str_of).collect()).unwrap_or_default();
                let reads: Vec<String> = o.get("read").and_then(Value::as_array).map(|r| r.iter().map(py_str_of).collect()).unwrap_or_default();
                writes.contains(&table) && reads.contains(&table) && writes.iter().any(|w| edge_set.contains(w))
            })
            .unwrap_or(false)
    });
    if !roles.is_empty() && !able {
        let mut sorted_edges = sorted_unique_strs(edges.iter().map(String::as_str));
        sorted_edges.sort();
        problems.push(format!(
            "{where_}: independent.own_evidence, but no access role both reads and writes {table} and writes \
one of its evidence edges ({}), so no credential could promote it",
            sorted_edges.join(", ")
        ));
    }
}

fn validate_audit_tables(data: &Value, problems: &mut Vec<String>) {
    for section in ["tables", "edges"] {
        if let Some(declared) = declared_section(data, section) {
            for name in declared.keys() {
                if AUDIT_TABLES.contains(&name.as_str()) {
                    problems.push(format!(
                        "{section}.{name}: {} is owned by the engine (who wrote what, and the promotion log); \
choose another name",
                        py_repr_str(name)
                    ));
                }
            }
        }
    }
    let Some(pl) = data.get("promotion_log") else { return };
    if !matches!(pl, Value::Bool(_)) {
        problems.push(format!("promotion_log must be true or false (got {})", py_repr_value(pl)));
        return;
    }
    if pl == &Value::Bool(true) {
        let declared = declared_tables_of(data);
        if let Some(gates_arr) = data.get("gates").and_then(Value::as_array) {
            for rule in gates_arr {
                let Some(rule_obj) = rule.as_object() else { continue };
                let table = py_str_of(rule_obj.get("table").unwrap_or(&Value::Null));
                if declared.get(&table).and_then(Value::as_object).map(|f| f.contains_key(INDEPENDENCE_FIELD)).unwrap_or(false) {
                    problems.push(format!(
                        "promotion_log: tables.{table} declares a field {}, which the engine owns for \
recording who wrote a gated row",
                        py_repr_str(INDEPENDENCE_FIELD)
                    ));
                }
            }
        }
    }
}

fn validate_unique(data: &Value, problems: &mut Vec<String>) {
    let Some(section) = data.get("unique") else { return };
    let Some(section_obj) = section.as_object().filter(|o| !o.is_empty()) else {
        problems.push("unique must be a mapping of table -> [field, ...]".to_string());
        return;
    };
    let declared = declared_tables_of(data);
    let edges = edge_map_of(data);
    for (table, fields) in section_obj {
        let where_ = format!("unique.{table}");
        if edges.contains_key(table) {
            problems.push(format!("{where_}: {} is an edge type; unique applies to node tables", py_repr_str(table)));
            continue;
        }
        let known: Vec<String> = if let Some((_, f)) = BUILT_IN_FIELDS.iter().find(|(name, _)| name == table) {
            f.iter().map(|(n, _)| n.to_string()).collect()
        } else if let Some(f) = declared.get(table).and_then(Value::as_object) {
            f.keys().cloned().collect()
        } else {
            let node_tables = node_tables_of(data);
            problems.push(format!("{where_}: unknown table {}; known node tables: {}", py_repr_str(table), node_tables.join(", ")));
            continue;
        };
        let Some(fields_arr) = fields.as_array().filter(|a| !a.is_empty()) else {
            problems.push(format!("{where_}: must be a non-empty list of field names"));
            continue;
        };
        let field_strs: Vec<String> = fields_arr.iter().map(py_str_of).collect();
        let uniq = sorted_unique_strs(field_strs.iter().map(String::as_str));
        if uniq.len() != field_strs.len() {
            problems.push(format!("{where_}: lists a field twice"));
        }
        for field in &field_strs {
            if !known.contains(field) {
                let mut sorted_known = known.clone();
                sorted_known.sort();
                problems.push(format!(
                    "{where_}: {} is not a field of {table}; its fields are {}",
                    py_repr_str(field),
                    sorted_known.join(", ")
                ));
            }
        }
    }
}

fn validate_assertions(data: &Value, problems: &mut Vec<String>) {
    if data.get("assertions").is_none() {
        return;
    }
    assertions_support::validate(data, problems);
}

fn validate_graph_shape(data: &Value, problems: &mut Vec<String>) {
    let tables_val = data.get("tables");
    let edges_val = data.get("edges");
    for (section, value) in [("tables", tables_val), ("edges", edges_val)] {
        if let Some(v) = value {
            if !v.is_null() && !v.is_object() {
                problems.push(format!("{section} must be a mapping of name -> definition"));
            }
        }
    }
    let empty = Map::new();
    let tables = tables_val.and_then(Value::as_object).unwrap_or(&empty);
    let edges = edges_val.and_then(Value::as_object).unwrap_or(&empty);

    let built_in_edge_names: Vec<&str> = BUILT_IN_EDGES.iter().map(|(n, _, _)| *n).collect();
    let mut taken: std::collections::HashMap<String, &str> = std::collections::HashMap::new();
    for (section, value, built_in, other) in [
        ("tables", tables, BUILT_IN_NODE_TABLES, "node table"),
        ("edges", edges, &built_in_edge_names[..], "edge type"),
    ] {
        for name in value.keys() {
            let where_ = format!("{section}.{name}");
            if !is_id(name) {
                problems.push(format!("{where_}: name must match [a-z0-9_]+"));
            }
            if built_in.contains(&name.as_str()) {
                problems.push(format!("{where_}: {} is a built-in {other} and may not be redeclared; choose another name", py_repr_str(name)));
            } else if BUILT_IN_NODE_TABLES.contains(&name.as_str()) || BUILT_IN_EDGES.iter().any(|(n, _, _)| n == name) {
                problems.push(format!("{where_}: {} is already a built-in table name; choose another name", py_repr_str(name)));
            } else if RESERVED_TABLES.contains(&name.as_str()) {
                problems.push(format!("{where_}: {} is reserved by the engine; choose another name", py_repr_str(name)));
            } else if let Some(section_taken) = taken.get(name.as_str()) {
                problems.push(format!("{where_}: {} is already declared under {section_taken}; a name is one thing or the other", py_repr_str(name)));
            } else {
                taken.insert(name.clone(), section);
            }
        }
    }

    let check_fields = |where_: &str, body: Option<&Value>, reserved: &[&str], problems: &mut Vec<String>| {
        let fields = body.and_then(|b| b.as_object()).and_then(|o| o.get("fields")).and_then(Value::as_object);
        let Some(fields) = fields.filter(|f| !f.is_empty()) else {
            problems.push(format!("{where_}: fields must be a non-empty mapping of name -> type"));
            return;
        };
        for (field, value) in fields {
            if !is_id(field) {
                problems.push(format!("{where_}: field name {} must match [a-z0-9_]+", py_repr_str(field)));
            }
            if reserved.contains(&field.as_str()) {
                problems.push(format!("{where_}: field {} is owned by the datastore on a relation table", py_repr_str(field)));
            }
            let (_, problem) = field_spec(value);
            if let Some(problem) = problem {
                problems.push(format!("{where_}.{field}: {problem}"));
            }
        }
    };

    for (name, body) in tables {
        let where_ = format!("tables.{name}");
        if !body.is_object() {
            problems.push(format!("{where_}: must be a mapping with fields"));
            continue;
        }
        let body_obj = body.as_object().unwrap();
        let unknown = sorted_unique_strs(body_obj.keys().map(String::as_str).filter(|k| !TABLE_KEYS.contains(k)));
        if !unknown.is_empty() {
            let allowed = sorted_unique_strs(TABLE_KEYS.iter().copied());
            problems.push(format!("{where_}: unknown key(s) {}; allowed: {}", unknown.join(", "), allowed.join(", ")));
        }
        check_fields(&where_, Some(body), &[], problems);
    }

    let known_tables = node_tables_of(data);
    for (name, body) in edges {
        let where_ = format!("edges.{name}");
        if !body.is_object() {
            problems.push(format!("{where_}: must be a mapping with from, to and fields"));
            continue;
        }
        let body_obj = body.as_object().unwrap();
        let unknown = sorted_unique_strs(body_obj.keys().map(String::as_str).filter(|k| !EDGE_KEYS.contains(k)));
        if !unknown.is_empty() {
            let allowed = sorted_unique_strs(EDGE_KEYS.iter().copied());
            problems.push(format!("{where_}: unknown key(s) {}; allowed: {}", unknown.join(", "), allowed.join(", ")));
        }
        for (end, verb) in [("from", "starts at"), ("to", "ends at")] {
            let table = body_obj.get(end);
            if !is_truthy_nonempty(table) {
                problems.push(format!("{where_}: {end} must name the table this edge {verb}"));
            } else {
                let table_str = py_str_of(table.unwrap());
                let is_edge_type = BUILT_IN_EDGES.iter().any(|(n, _, _)| *n == table_str) || edges.contains_key(&table_str);
                if is_edge_type {
                    let mut sorted_known = known_tables.clone();
                    sorted_known.sort();
                    problems.push(format!(
                        "{where_}: {end} names {}, which is an edge type, not a node table; known tables: {}",
                        py_repr_str(&table_str),
                        sorted_known.join(", ")
                    ));
                } else if !known_tables.contains(&table_str) {
                    let mut sorted_known = known_tables.clone();
                    sorted_known.sort();
                    problems.push(format!(
                        "{where_}: {end} names unknown table {}; declare it under tables: or name one of {}",
                        py_repr_str(&table_str),
                        sorted_known.join(", ")
                    ));
                }
            }
        }
        if body_obj.get("fields").map(|f| !f.is_null()).unwrap_or(false) {
            check_fields(&where_, Some(body), EDGE_RESERVED_FIELDS, problems);
        }
    }
}

/// `groundworks/config.py:1028 check_lens_filter`.
pub fn check_lens_filter(text: &Value) -> Option<String> {
    let Some(text) = text.as_str().filter(|s| !s.trim().is_empty()) else {
        return Some("filter must be a non-empty string".to_string());
    };
    use regex::Regex;
    use std::sync::OnceLock;
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| {
        Regex::new(
            r#"(?s)^\s*(?:('(?:[^'\\]|\\.)*')|("(?:[^"\\]|\\.)*")|(-?\d+(?:\.\d+)?)|(>=|<=|!=|=|>|<|\(|\)|\[|\]|,)|([A-Za-z_][A-Za-z0-9_]*)|(\S))"#,
        )
        .unwrap()
    });
    let mut pos = 0usize;
    while pos < text.len() {
        let Some(caps) = re.captures(&text[pos..]) else {
            let tail: String = text[pos..].chars().take(10).collect();
            return Some(format!("filter {}: cannot read {}", py_repr_str(text), py_repr_str(&tail)));
        };
        let whole = caps.get(0).unwrap();
        if whole.end() == 0 {
            let tail: String = text[pos..].chars().take(10).collect();
            return Some(format!("filter {}: cannot read {}", py_repr_str(text), py_repr_str(&tail)));
        }
        let word = caps.get(5).map(|m| m.as_str());
        let stray = caps.get(6).map(|m| m.as_str());
        if let Some(stray) = stray {
            return Some(format!("filter {}: character {} is not allowed", py_repr_str(text), py_repr_str(stray)));
        }
        if let Some(word) = word {
            let upper = word.to_uppercase();
            if !LENS_FILTER_FIELDS.contains(&word) && !LENS_FILTER_WORDS.contains(&upper.as_str()) && word != "true" && word != "false" {
                return Some(format!(
                    "filter {}: {} is not a label field ({}) or an operator; unit fields belong in the standard",
                    py_repr_str(text),
                    py_repr_str(word),
                    LENS_FILTER_FIELDS.join(", ")
                ));
            }
        }
        pos += whole.end();
    }
    None
}

fn validate_lenses(data: &Value, problems: &mut Vec<String>) {
    let Some(lenses) = data.get("lenses") else { return };
    if lenses.is_null() {
        return;
    }
    let Some(lenses_arr) = lenses.as_array() else {
        problems.push("lenses must be a list".to_string());
        return;
    };
    let status = data.get("status").and_then(Value::as_object);
    let fid = data.get("fidelity").and_then(Value::as_object);
    let vocab = data.get("vocabulary").and_then(Value::as_object);
    let issues: Vec<Value> = vocab.and_then(|v| v.get("issues")).and_then(Value::as_array).cloned().unwrap_or_default();
    let mut names: Vec<String> = Vec::new();
    for lens in lenses_arr {
        let lens_obj = lens.as_object();
        let has_name = lens_obj.map(|o| is_truthy_nonempty(o.get("name"))).unwrap_or(false);
        if lens_obj.is_none() || !has_name {
            problems.push("each lens needs a name".to_string());
            continue;
        }
        let lens_obj = lens_obj.unwrap();
        let name = py_str_of(&lens_obj["name"]);
        names.push(name.clone());
        let where_ = format!("lens {name}");
        if !is_id(&name) {
            problems.push(format!("{where_}: name must match [a-z0-9_]+"));
        }
        let unknown = sorted_unique_strs(lens_obj.keys().map(String::as_str).filter(|k| !LENS_KEYS.contains(k)));
        if !unknown.is_empty() {
            let allowed = sorted_unique_strs(LENS_KEYS.iter().copied());
            problems.push(format!("{where_}: unknown key(s) {}; allowed: {}", unknown.join(", "), allowed.join(", ")));
        }
        let kind = lens_obj.get("kind").and_then(Value::as_str);
        if !kind.map(|k| LENS_KINDS.contains(&k)).unwrap_or(false) {
            problems.push(format!(
                "{where_}: kind must be one of {} (got {})",
                LENS_KINDS.join(", "),
                py_repr_value(lens_obj.get("kind").unwrap_or(&Value::Null))
            ));
        }
        let charter_ok = lens_obj.get("charter").and_then(Value::as_str).map(|s| !s.trim().is_empty()).unwrap_or(false);
        if !charter_ok {
            problems.push(format!("{where_}: charter is required (who looks, what they want, what hurts them; human-signed)"));
        }
        let backing = lens_obj.get("backing");
        let backing_str = backing.and_then(Value::as_str);
        if backing.map(|b| !b.is_null()).unwrap_or(false) && !backing_str.map(|b| LENS_BACKINGS.contains(&b)).unwrap_or(false) {
            problems.push(format!(
                "{where_}: backing must be one of {} (got {})",
                LENS_BACKINGS.join(", "),
                py_repr_value(backing.unwrap())
            ));
        }
        let signed = lens_obj.get("signed");
        if signed.map(|s| !s.is_null()).unwrap_or(false) && !matches!(signed, Some(Value::Bool(_))) {
            problems.push(format!("{where_}: signed must be true or false (a human signed this charter, or did not)"));
        }
        let signed_true = signed == Some(&Value::Bool(true));
        if !signed_true && matches!(backing_str, Some("source") | Some("graph") | Some("calibrated")) {
            problems.push(format!(
                "{where_}: backing {} claims more than prompt-backed, but signed is not true; sign the charter or set backing: prompt",
                py_repr_value(backing.unwrap())
            ));
        }
        if let Some(order) = lens_obj.get("order") {
            if !order.is_null() && !order.is_string() {
                problems.push(format!("{where_}: order must be a string (an ORDER BY over label fields)"));
            }
        }
        let standard = lens_obj.get("standard");
        if let Some(standard) = standard.filter(|s| !s.is_null()) {
          if standard.as_object().is_none() {
            problems.push(format!("{where_}: standard must be a mapping of status and/or grade lists"));
          } else {
            let standard_obj = standard.as_object().unwrap();
            let bad = sorted_unique_strs(standard_obj.keys().map(String::as_str).filter(|k| !LENS_STANDARD_KEYS.contains(k)));
            if !bad.is_empty() {
                problems.push(format!("{where_}: standard may only name {} (got {})", LENS_STANDARD_KEYS.join(", "), bad.join(", ")));
            }
            for (key, exists, base, base_name) in [
                (
                    "status",
                    status.and_then(|s| s.get("values")).and_then(Value::as_array).cloned().unwrap_or_default(),
                    status.and_then(|s| s.get("usable")).and_then(Value::as_array).cloned().unwrap_or_default(),
                    "status.usable",
                ),
                (
                    "grade",
                    fid.and_then(|f| f.get("order")).and_then(Value::as_array).cloned().unwrap_or_default(),
                    fid.and_then(|f| f.get("confirming")).and_then(Value::as_array).cloned().unwrap_or_default(),
                    "fidelity.confirming",
                ),
            ] {
                let Some(values) = standard_obj.get(key) else { continue };
                if values.is_null() {
                    continue;
                }
                let Some(values_arr) = values.as_array().filter(|a| !a.is_empty()) else {
                    problems.push(format!("{where_}: standard.{key} must be a non-empty list"));
                    continue;
                };
                let exists_strs: Vec<String> = exists.iter().map(py_str_of).collect();
                let missing: Vec<String> = values_arr.iter().map(py_str_of).filter(|v| !exists.iter().any(|e| py_str_of(e) == *v)).collect();
                if !missing.is_empty() {
                    problems.push(format!("{where_}: standard.{key} names {}, which does not exist", missing.join(", ")));
                }
                let base_strs: Vec<String> = base.iter().map(py_str_of).collect();
                let looser: Vec<String> = values_arr
                    .iter()
                    .map(py_str_of)
                    .filter(|v| exists_strs.contains(v) && !base_strs.contains(v))
                    .collect();
                if !looser.is_empty() {
                    problems.push(format!(
                        "{where_}: standard.{key} admits {}, outside the base {base_name}: a lens may be stricter, never looser",
                        looser.join(", ")
                    ));
                }
            }
          }
        }
        if let Some(filter) = lens_obj.get("filter") {
            if !filter.is_null() {
                if let Some(problem) = check_lens_filter(filter) {
                    problems.push(format!("{where_}: {problem}"));
                }
            }
        }
        let frame = lens_obj.get("frame").filter(|f| !f.is_null());
        let crosswalk = lens_obj.get("crosswalk").filter(|c| !c.is_null());
        if kind == Some("framework") && (frame.is_none() || crosswalk.is_none()) {
            problems.push(format!("{where_}: a framework lens needs frame (source, nodes) and crosswalk (node -> issues)"));
        }
        let mut nodes: Vec<String> = Vec::new();
        if let Some(frame) = frame {
            let frame_obj = frame.as_object();
            let source_ok = frame_obj.and_then(|f| f.get("source")).and_then(Value::as_str).map(|s| !s.trim().is_empty()).unwrap_or(false);
            if frame_obj.is_none() || !source_ok {
                problems.push(format!("{where_}: frame.source must say where the frame was transcribed from"));
            }
            let raw_nodes = frame_obj.and_then(|f| f.get("nodes")).and_then(Value::as_array).filter(|n| !n.is_empty());
            if let Some(raw_nodes) = raw_nodes {
                nodes = raw_nodes.iter().map(py_str_of).collect();
                let uniq = sorted_unique_strs(nodes.iter().map(String::as_str));
                if uniq.len() != nodes.len() {
                    problems.push(format!("{where_}: frame.nodes must be unique"));
                }
                let bad: Vec<&String> = nodes.iter().filter(|n| !is_id(n)).collect();
                if !bad.is_empty() {
                    problems.push(format!(
                        "{where_}: frame node id(s) {} must match [a-z0-9_]+",
                        bad.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")
                    ));
                }
            } else {
                problems.push(format!("{where_}: frame.nodes must be a non-empty list of node ids"));
            }
        }
        if let Some(crosswalk) = crosswalk {
            if frame.is_none() {
                problems.push(format!("{where_}: a crosswalk needs a frame to file under"));
            }
            let Some(crosswalk_obj) = crosswalk.as_object() else {
                problems.push(format!("{where_}: crosswalk must map frame node -> list of issues"));
                continue;
            };
            let not_nodes = sorted_unique_strs(crosswalk_obj.keys().map(String::as_str).filter(|k| !nodes.contains(&k.to_string())));
            if !not_nodes.is_empty() && !nodes.is_empty() {
                problems.push(format!("{where_}: crosswalk key(s) {} are not frame nodes", not_nodes.join(", ")));
            }
            let uncovered: Vec<&String> = nodes.iter().filter(|n| !crosswalk_obj.contains_key(*n)).collect();
            if !uncovered.is_empty() {
                problems.push(format!(
                    "{where_}: crosswalk must name every frame node (missing {}; use [] for none)",
                    uncovered.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")
                ));
            }
            for (node, values) in crosswalk_obj {
                let Some(values_arr) = values.as_array() else {
                    problems.push(format!("{where_}: crosswalk.{node} must be a list of issues"));
                    continue;
                };
                let bad: Vec<String> = values_arr.iter().filter(|v| !issues.iter().any(|i| i == *v)).map(py_str_of).collect();
                if !bad.is_empty() {
                    problems.push(format!("{where_}: crosswalk.{node} names {}, not in vocabulary.issues", bad.join(", ")));
                }
            }
        }
    }
    let uniq = sorted_unique_strs(names.iter().map(String::as_str));
    if uniq.len() != names.len() {
        problems.push("lens names must be unique".to_string());
    }
}

// ---- Instance --------------------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct Instance {
    pub data: Value,
    pub source: Option<PathBuf>,
    pub id: String,
}

impl Instance {
    pub fn confirming_grades(&self) -> Vec<Value> {
        self.data["fidelity"]["confirming"].as_array().cloned().unwrap_or_default()
    }
    pub fn grades(&self) -> Vec<Value> {
        self.data["fidelity"]["order"].as_array().cloned().unwrap_or_default()
    }
    pub fn usable_statuses(&self) -> Vec<Value> {
        self.data["status"]["usable"].as_array().cloned().unwrap_or_default()
    }
    pub fn status_values(&self) -> Vec<Value> {
        self.data["status"]["values"].as_array().cloned().unwrap_or_default()
    }
    pub fn verdict_meanings(&self) -> Map<String, Value> {
        self.data.get("verdicts").and_then(Value::as_object).cloned().unwrap_or_default()
    }
    pub fn unit_names(&self) -> Vec<String> {
        self.data["units"].as_array().map(|a| a.iter().filter_map(|u| u.get("name")).map(py_str_of).collect()).unwrap_or_default()
    }
    /// `config.py:439 unit_type`: the declared unit whose `name` equals `name`, or `None` (the
    /// oracle raises `KeyError`; a missing unit is a programming error here too, not a config
    /// refusal, so callers that need the oracle's exact message can match on `None` themselves).
    pub fn unit_type(&self, name: &str) -> Option<&Value> {
        self.data["units"].as_array()?.iter().find(|u| u.get("name").map(py_str_of).as_deref() == Some(name))
    }
    /// `config.py:445 allowed_vocabulary`: every declared vocabulary list, plus `grades` and
    /// `statuses` (which are not literally under `vocabulary:` but are part of what a label
    /// validator accepts).
    pub fn allowed_vocabulary(&self) -> Map<String, Value> {
        let mut vocab = self.data.get("vocabulary").and_then(Value::as_object).cloned().unwrap_or_default();
        vocab.insert("grades".to_string(), Value::Array(self.grades()));
        vocab.insert("statuses".to_string(), Value::Array(self.status_values()));
        vocab
    }
    pub fn gate_rules(&self) -> Vec<Value> {
        self.data["gates"].as_array().cloned().unwrap_or_default()
    }
    pub fn declared_tables(&self) -> Map<String, Value> {
        declared_tables_of(&self.data)
    }
    pub fn declared_edges(&self) -> Map<String, Value> {
        declared_edges_of(&self.data)
    }
    /// `config.py:471 table_fields`: the declared fields of a declared node table; `{}` for a
    /// built-in (its fields are the engine's).
    pub fn table_fields(&self, table: &str) -> Value {
        self.declared_tables().get(table).cloned().unwrap_or(Value::Object(Map::new()))
    }
    /// `config.py:475 edge_fields`: the declared fields of a declared edge type; `{}` for a
    /// built-in (they carry none).
    pub fn edge_fields(&self, edge: &str) -> Value {
        self.declared_edges()
            .get(edge)
            .and_then(|e| e.get("fields"))
            .cloned()
            .unwrap_or(Value::Object(Map::new()))
    }
    pub fn node_tables(&self) -> Vec<String> {
        node_tables_of(&self.data)
    }
    pub fn edge_map(&self) -> Map<String, Value> {
        edge_map_of(&self.data)
    }
    /// `groundworks/config.py:479 unique_fields`: table -> [field, ...] in DECLARED order (the oracle
    /// does not sort here -- `gates.py:1235 spec()` sorts when it renders the gate spec; sorting here
    /// too would be invisible to `gw config spec` but would corrupt any consumer that emits SurQL
    /// `DEFINE INDEX ... FIELDS a, b` in declared order, such as port-02's unique_surql).
    pub fn unique_fields(&self) -> Map<String, Value> {
        let mut out = Map::new();
        if let Some(section) = self.data.get("unique").and_then(Value::as_object) {
            for (table, fields) in section {
                let strs: Vec<String> = fields.as_array().map(|a| a.iter().map(py_str_of).collect()).unwrap_or_default();
                out.insert(table.clone(), Value::Array(strs.into_iter().map(Value::String).collect()));
            }
        }
        out
    }
    pub fn independent_rules(&self) -> Vec<Value> {
        self.gate_rules().into_iter().filter(|r| independence_of(r).is_some()).collect()
    }
    pub fn promotion_log_enabled(&self) -> bool {
        self.data.get("promotion_log") == Some(&Value::Bool(true))
    }
    pub fn access_roles(&self) -> Map<String, Value> {
        let mut out = Map::new();
        if let Some(roles) = self.data.get("access").and_then(Value::as_object).and_then(|a| a.get("roles")).and_then(Value::as_object) {
            for (name, spec) in roles {
                let mut m = Map::new();
                m.insert("read".to_string(), spec.get("read").cloned().unwrap_or(Value::Array(vec![])));
                m.insert("write".to_string(), spec.get("write").cloned().unwrap_or(Value::Array(vec![])));
                // `config.py:499`: `bool(spec.get("lens_bound"))` -- Python truthiness, not `== True`.
                m.insert("lens_bound".to_string(), Value::Bool(is_truthy_nonempty(spec.get("lens_bound"))));
                out.insert(name.clone(), Value::Object(m));
            }
        }
        out
    }
    pub fn lenses(&self) -> Vec<Value> {
        self.data.get("lenses").and_then(Value::as_array).cloned().unwrap_or_default()
    }
    pub fn roles(&self) -> Vec<Value> {
        self.data.get("roles").and_then(Value::as_array).cloned().unwrap_or_default()
    }
    pub fn evaluator(&self) -> Value {
        self.data.get("evaluator").cloned().unwrap_or(Value::Null)
    }
    pub fn privacy(&self) -> Value {
        self.data.get("privacy").cloned().unwrap_or(Value::Null)
    }
    pub fn originals(&self) -> Value {
        self.data.get("originals").cloned().unwrap_or(Value::Null)
    }
    pub fn section(&self, name: &str) -> Value {
        self.data.get(name).cloned().unwrap_or(Value::Null)
    }
}

/// `groundworks/config.py:1161 load`: an `Instance` from a path (`.yaml`/`.yml`/`.json`) or an
/// already-parsed mapping.
pub fn load_from_mapping(data: Value) -> R<Instance> {
    let validated = validate(data)?;
    let id = py_str_of(&validated["instance"]["id"]);
    Ok(Instance { data: validated, source: None, id })
}

pub fn load_from_path(path: &Path) -> R<Instance> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| ConfigError(format!("cannot read config {}: {e}", path.display())))?;
    let data = if path.extension().map(|e| e == "json").unwrap_or(false) {
        serde_json::from_str::<Value>(&text)
            .map_err(|e| ConfigError(format!("{} is not valid JSON: {e}", path.display())))?
    } else {
        parse_yaml(&text)?
    };
    let validated = validate(data)?;
    let id = py_str_of(&validated["instance"]["id"]);
    let inst = Instance { data: validated, source: Some(path.to_path_buf()), id };
    refuse_unusable_regression_file(&inst, None)?;
    Ok(inst)
}

/// `groundworks/config.py:1194 regression_file_path`.
pub fn regression_file_path(inst: &Instance, base_dir: Option<&Path>) -> R<PathBuf> {
    let declared = inst.data["evaluator"]["regression_file"].as_str().unwrap_or("").to_string();
    let declared_path = shellexpand_home(&declared);
    if declared_path.is_absolute() {
        return Ok(declared_path);
    }
    let base = match base_dir {
        Some(b) => b.to_path_buf(),
        None => {
            let Some(source) = &inst.source else {
                return Err(ConfigError(format!(
                    "instance {} was loaded from a mapping, so its evaluator.regression_file {} has no directory \
to resolve against; load it from its instance.yaml or pass base_dir",
                    inst.id,
                    py_repr_str(&declared_path.display().to_string())
                )));
            };
            let resolved = source.canonicalize().unwrap_or_else(|_| source.clone());
            resolved.parent().map(Path::to_path_buf).unwrap_or_default()
        }
    };
    let joined = base.join(&declared_path);
    Ok(joined.canonicalize().unwrap_or(joined))
}

fn shellexpand_home(p: &str) -> PathBuf {
    if let Some(rest) = p.strip_prefix("~/") {
        if let Ok(home) = std::env::var("USERPROFILE").or_else(|_| std::env::var("HOME")) {
            return Path::new(&home).join(rest);
        }
    }
    PathBuf::from(p)
}

/// `groundworks/config.py:1208 refuse_unusable_regression_file`.
pub fn refuse_unusable_regression_file(inst: &Instance, base_dir: Option<&Path>) -> R<PathBuf> {
    let path = regression_file_path(inst, base_dir)?;
    if !path.is_file() {
        return Err(ConfigError(format!(
            "config invalid:\n  - evaluator.regression_file {} does not exist: the evaluator this config \
declares has never been written, so it can never run. Create it (format {}; an empty entries list until \
the first real error is recorded)",
            path.display(),
            evaluator_support::FORMAT
        )));
    }
    match evaluator_support::read_file(&path) {
        Ok(_) => Ok(path),
        Err(e) => Err(ConfigError(format!("config invalid:\n  - evaluator.regression_file: {e}"))),
    }
}

/// `gates.spec(inst)` (`groundworks/gates.py:1209`), reimplemented here because everything it reads
/// (edge_map, gate_rules, independence_of, promotion_log_enabled, unique_fields, expand_where) is
/// already in this module -- porting the rest of gates.py is not needed for `gw config spec`.
pub fn spec(inst: &Instance) -> Value {
    let edges = inst.edge_map();
    let rules = inst.gate_rules();
    let mut used: Vec<String> = Vec::new();
    for rule in &rules {
        for kind in ["requires", "forbids"] {
            if let Some(clauses) = rule.get(kind).and_then(Value::as_array) {
                for clause in clauses {
                    if let Some(edge) = clause.get("edge") {
                        used.push(py_str_of(edge));
                    }
                }
            }
        }
    }
    used = sorted_unique_strs(used.iter().map(String::as_str));

    let mut out = Map::new();
    out.insert("instance".to_string(), Value::String(inst.id.clone()));
    let mut edges_out = Map::new();
    for edge in &used {
        if let Some(pair) = edges.get(edge).and_then(Value::as_array) {
            let mut m = Map::new();
            m.insert("from".to_string(), pair[0].clone());
            m.insert("to".to_string(), pair[1].clone());
            edges_out.insert(edge.clone(), Value::Object(m));
        }
    }
    out.insert("edges".to_string(), Value::Object(edges_out));

    let mut rules_out = Map::new();
    for rule in &rules {
        let mut clauses: Vec<Value> = Vec::new();
        for kind in ["requires", "forbids"] {
            if let Some(clause_list) = rule.get(kind).and_then(Value::as_array) {
                for clause in clause_list {
                    let mut c = Map::new();
                    c.insert("kind".to_string(), Value::String(kind.to_string()));
                    c.insert("edge".to_string(), clause.get("edge").cloned().unwrap_or(Value::Null));
                    c.insert("min".to_string(), clause.get("min").cloned().unwrap_or(Value::Number(1.into())));
                    let where_expanded = expand_where(&inst.data, clause.get("where").and_then(Value::as_str));
                    let where_val = if clause.get("where").and_then(Value::as_str).map(|s| !s.is_empty()).unwrap_or(false) {
                        Value::String(where_expanded)
                    } else {
                        Value::Null
                    };
                    c.insert("where".to_string(), where_val);
                    clauses.push(Value::Object(c));
                }
            }
        }
        clauses.sort_by_key(|c| {
            (
                c.get("kind").and_then(Value::as_str).unwrap_or("").to_string(),
                py_str_of(c.get("edge").unwrap_or(&Value::Null)),
                py_str_of(c.get("where").unwrap_or(&Value::Null)),
            )
        });
        let id = py_str_of(rule.get("id").unwrap_or(&Value::Null));
        let mut r = Map::new();
        r.insert("table".to_string(), rule.get("table").cloned().unwrap_or(Value::Null));
        r.insert("field".to_string(), rule.get("field").cloned().unwrap_or(Value::Null));
        r.insert("value".to_string(), rule.get("value").cloned().unwrap_or(Value::Null));
        r.insert("clauses".to_string(), Value::Array(clauses));
        if let Some(mode) = independence_of(rule) {
            r.insert("independent".to_string(), Value::Bool(true));
            let default_mode = {
                let mut m = Map::new();
                m.insert("between".to_string(), Value::String("credential".to_string()));
                m.insert("own_evidence".to_string(), Value::Bool(false));
                Value::Object(m)
            };
            if mode != default_mode {
                r.insert("independence".to_string(), mode);
            }
        }
        rules_out.insert(id, Value::Object(r));
    }
    out.insert("rules".to_string(), Value::Object(rules_out));

    if inst.promotion_log_enabled() {
        out.insert("promotion_log".to_string(), Value::Bool(true));
    }
    let unique = inst.unique_fields();
    if !unique.is_empty() {
        // `gates.py:1235`: `{table: sorted(fields) for table, fields in sorted(inst.unique_fields().items())}`
        // -- unique_fields() itself is declared order (config.py:479); spec() sorts both axes here.
        let mut tables: Vec<&String> = unique.keys().collect();
        tables.sort();
        let mut unique_out = Map::new();
        for table in tables {
            let mut fields: Vec<String> =
                unique.get(table).and_then(Value::as_array).map(|a| a.iter().map(py_str_of).collect()).unwrap_or_default();
            fields.sort();
            unique_out.insert(table.clone(), Value::Array(fields.into_iter().map(Value::String).collect()));
        }
        out.insert("unique".to_string(), Value::Object(unique_out));
    }
    Value::Object(out)
}

// ---- round-2 review BLOCKING 3 (GATE DEPTH): every const allow/deny table pinned ------------------
//
// The `--config` differential corpus only PROBES these tables through instances that happen to use
// one of their members; it does not compare the table's full contents against the oracle. The
// review planted 8 wrong loaders that each widened or narrowed exactly one such constant and the
// corpus caught only 2 of them -- and 2 of round-1's own twelve mutations (`LENS_KEYS +'notes'`,
// a `REQUIRED_TOP` fidelity<->status swap) are STILL green for the same reason. A unit test in the
// same module as the constant (not an integration test, which can only see `pub` items) that
// compares it, byte for byte, against the literal in `groundworks/config.py` closes that gap for
// good: any edit to a table -- widen, narrow, or reorder where order is observable (message text,
// `REQUIRED_TOP`'s declared-order refusal listing) -- fails this test by name before it can reach
// the differential at all.
#[cfg(test)]
mod const_table_pins {
    use super::*;
    use std::collections::BTreeSet;

    fn set<'a>(items: &'a [&'a str]) -> BTreeSet<&'a str> {
        items.iter().copied().collect()
    }

    // ---- ordered (a Python tuple/list; order is observable in a message or an iteration) ---------

    #[test]
    fn built_in_node_tables_pin() {
        // config.py:42 + :84 (`+ ("document",)`)
        assert_eq!(
            BUILT_IN_NODE_TABLES,
            ["unit", "label", "fact", "conclusion", "authority", "critique", "answer", "document"]
        );
    }

    #[test]
    fn built_in_edges_pin() {
        // config.py:43-52
        assert_eq!(
            BUILT_IN_EDGES,
            [
                ("cites", "fact", "unit"),
                ("supports", "fact", "conclusion"),
                ("undercuts", "fact", "conclusion"),
                ("attacks", "critique", "conclusion"),
                ("answers", "answer", "critique"),
                ("bears_on", "label", "conclusion"),
                ("relies_on", "conclusion", "authority"),
            ]
        );
    }

    #[test]
    fn reserved_tables_pin() {
        // config.py:53
        assert_eq!(RESERVED_TABLES, ["agent"]);
    }

    #[test]
    fn built_in_fields_pin() {
        // config.py:59-79 + :85-88 (`document` added, `unit.document` added)
        let expected: &[(&str, &[(&str, &str)])] = &[
            (
                "unit",
                &[
                    ("type", "string"), ("text", "string"), ("locator", "object"), ("status", "string"),
                    ("grade", "string"), ("hash", "string"), ("meta", "object"), ("document", "record"),
                ],
            ),
            (
                "label",
                &[
                    ("unit", "record"), ("tier", "int"), ("dir", "string"), ("issues", "array"),
                    ("targets", "array"), ("role", "string"), ("reason", "string"), ("quote", "string"),
                    ("grade", "string"), ("chunk", "string"), ("run", "string"),
                ],
            ),
            (
                "fact",
                &[
                    ("statement", "string"), ("status", "string"), ("note", "string"), ("tried", "string"),
                    ("evidence", "string"), ("reasoning_type", "string"), ("author", "string"),
                    ("lens", "string"), ("learned_at", "datetime"),
                ],
            ),
            ("conclusion", &[("statement", "string"), ("ready", "bool"), ("rating", "string"), ("author", "string")]),
            ("authority", &[("citation", "string"), ("verified", "bool"), ("source_grade", "string")]),
            (
                "critique",
                &[
                    ("lens", "string"), ("head", "string"), ("verdict", "string"), ("text", "string"),
                    ("author", "string"), ("answered", "bool"),
                ],
            ),
            ("answer", &[("text", "string"), ("author", "string")]),
            (
                "document",
                &[
                    ("sha256", "string"), ("media_type", "string"), ("bytes", "int"), ("capture", "string"),
                    ("source_uri", "string"), ("retrieved_at", "datetime"), ("original_name", "string"),
                    ("integrity", "string"), ("verified_at", "datetime"),
                ],
            ),
        ];
        assert_eq!(BUILT_IN_FIELDS, expected);
    }

    #[test]
    fn independence_between_pin() {
        // config.py:113
        assert_eq!(INDEPENDENCE_BETWEEN, ["credential", "principal"]);
    }

    #[test]
    fn audit_tables_pin() {
        // config.py:115
        assert_eq!(AUDIT_TABLES, ["row_writer", "link_creator", "promotion_log", "promotion_head"]);
    }

    #[test]
    fn field_types_pin() {
        // config.py:129 -- FIELD_TYPES.join(", ") appears verbatim in a refusal message, so both
        // membership AND order are observable here.
        assert_eq!(FIELD_TYPES, ["string", "int", "float", "bool", "datetime"]);
    }

    #[test]
    fn edge_reserved_fields_pin() {
        // config.py:133
        assert_eq!(EDGE_RESERVED_FIELDS, ["id", "in", "out"]);
    }

    #[test]
    fn required_top_pin() {
        // config.py:31-32 -- iterated in this order to name the FIRST missing section, so a swap
        // (round-1's still-green fidelity<->status mutant) changes which key a refusal names.
        assert_eq!(
            REQUIRED_TOP,
            ["instance", "units", "fidelity", "status", "vocabulary", "gates", "evaluator", "human_owned", "privacy"]
        );
    }

    #[test]
    fn must_be_human_owned_pin() {
        // config.py:33
        assert_eq!(MUST_BE_HUMAN_OWNED, ["gates", "fidelity", "status", "evaluator", "privacy"]);
    }

    #[test]
    fn human_owned_if_present_pin() {
        // config.py:35 + :81 (`+ ["originals"]`) + :83 (`+ ["labels"]`)
        assert_eq!(HUMAN_OWNED_IF_PRESENT, ["verdicts", "unique", "assertions", "promotion_log", "originals", "labels"]);
    }

    #[test]
    fn lens_kinds_pin() {
        // config.py:211 -- LENS_KINDS.join(", ") appears verbatim in a refusal message.
        assert_eq!(LENS_KINDS, ["perspective", "discipline", "framework"]);
    }

    #[test]
    fn lens_backings_pin() {
        // config.py:212 -- weakest first, and LENS_BACKINGS.join(", ") appears in a refusal message.
        assert_eq!(LENS_BACKINGS, ["prompt", "source", "graph", "calibrated"]);
    }

    #[test]
    fn lens_standard_keys_pin() {
        // config.py:214
        assert_eq!(LENS_STANDARD_KEYS, ["status", "grade"]);
    }

    #[test]
    fn lens_filter_fields_pin() {
        // config.py:215 -- label fields only.
        assert_eq!(LENS_FILTER_FIELDS, ["tier", "dir", "issues", "targets", "role", "chunk"]);
    }

    #[test]
    fn lens_filter_words_pin() {
        // config.py:216-217
        assert_eq!(
            LENS_FILTER_WORDS,
            ["AND", "OR", "NOT", "IN", "CONTAINS", "CONTAINSANY", "CONTAINSALL", "CONTAINSNOT", "NONE", "true", "false"]
        );
    }

    // ---- unordered (a Python `{...}` set; membership only) -----------------------------------------

    #[test]
    fn independence_keys_pin() {
        // config.py:114
        assert_eq!(set(INDEPENDENCE_KEYS), set(&["between", "own_evidence"]));
    }

    #[test]
    fn table_keys_pin() {
        // config.py:130
        assert_eq!(set(TABLE_KEYS), set(&["fields", "doc"]));
    }

    #[test]
    fn edge_keys_pin() {
        // config.py:131
        assert_eq!(set(EDGE_KEYS), set(&["from", "to", "fields", "doc"]));
    }

    #[test]
    fn field_keys_pin() {
        // config.py:132
        assert_eq!(set(FIELD_KEYS), set(&["type", "one_of", "optional", "doc"]));
    }

    #[test]
    fn top_keys_pin() {
        // config.py:27-30 + :80 (`| {"originals"}`)
        assert_eq!(
            set(TOP_KEYS),
            set(&[
                "instance", "units", "fidelity", "status", "vocabulary", "gates", "lenses", "roles",
                "evaluator", "human_owned", "privacy", "panel", "present", "embed", "access", "authority",
                "staleness", "discovery", "tables", "edges", "verdicts", "unique", "assertions",
                "promotion_log", "labels", "originals",
            ])
        );
    }

    #[test]
    fn lens_keys_pin() {
        // config.py:213 -- round-1's still-green `LENS_KEYS +'notes'` mutant, whose own test pinned
        // only the literal key `"extra_thing"` rather than the table's full contents.
        assert_eq!(
            set(LENS_KEYS),
            set(&[
                "name", "kind", "charter", "standard", "filter", "backing", "frame", "crosswalk", "signed",
                "order",
            ])
        );
    }
}
