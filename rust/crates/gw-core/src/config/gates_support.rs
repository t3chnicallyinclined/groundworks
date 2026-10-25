//! The slivers of `groundworks/gates.py` that `config.py` itself imports. Everything else in
//! gates.py (SurQL emission, the baseline comparison, the traversal builder) is port-02/03/-09
//! scope; this crate ports only what the config loader's own validation needs, so a config-only
//! ticket does not pull in the whole gate engine.

use regex::Regex;
use std::sync::OnceLock;

/// `groundworks/gates.py:34`.
pub const CRITIQUE_VERDICTS: &[&str] = &["fatal", "damaging", "survivable"];

/// `groundworks/gates.py:33`.
pub const FACT_STATUSES: &[&str] = &[
    "proposed",
    "inferred",
    "open",
    "confirmed",
    "ruled_out",
    "stale",
    "superseded",
];

/// `groundworks/gates.py:309 _value_literal`.
pub fn value_literal(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::Bool(b) => (if *b { "true" } else { "false" }).to_string(),
        serde_json::Value::Number(n) => n.to_string(),
        other => format!("'{}'", py_str(other).replace('\'', "\\'")),
    }
}

fn py_str(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::Null => "None".to_string(),
        serde_json::Value::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        serde_json::Value::Number(n) => n.to_string(),
        serde_json::Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn field_in_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?is)^([A-Za-z_][A-Za-z0-9_]*)\s+IN\s+(.+)$").unwrap())
}
fn field_eq_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?s)^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$").unwrap())
}
fn literal_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r#"(?is)^(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|true|false|-?\d+(?:\.\d+)?)$"#)
            .unwrap()
    })
}

/// `groundworks/gates.py:673 _split_top_level`: split on a top-level `AND` (or on commas when
/// `separator` is `,`), outside quotes and brackets.
pub fn split_top_level(text: &str, comma: bool) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let mut parts = Vec::new();
    let mut buf = String::new();
    let mut quote: Option<char> = None;
    let mut depth: i32 = 0;
    let mut i = 0usize;
    let is_word = |c: char| c.is_alphanumeric() || c == '_';
    while i < chars.len() {
        let ch = chars[i];
        if let Some(q) = quote {
            buf.push(ch);
            if ch == q {
                quote = None;
            }
            i += 1;
            continue;
        }
        if ch == '\'' || ch == '"' {
            quote = Some(ch);
        } else if ch == '(' || ch == '[' || ch == '{' {
            depth += 1;
        } else if ch == ')' || ch == ']' || ch == '}' {
            depth -= 1;
        } else if depth == 0 && comma && ch == ',' {
            parts.push(std::mem::take(&mut buf));
            i += 1;
            continue;
        } else if depth == 0
            && !comma
            && i + 3 <= chars.len()
            && chars[i..i + 3].iter().collect::<String>().to_uppercase() == "AND"
            && (i == 0 || !is_word(chars[i - 1]))
            && (i + 3 >= chars.len() || !is_word(chars[i + 3]))
        {
            parts.push(std::mem::take(&mut buf));
            i += 3;
            continue;
        }
        buf.push(ch);
        i += 1;
    }
    parts.push(buf);
    // `gates.py:695`: `return [p.strip() for p in parts]` -- every part is stripped, not just the
    // whole text. Without this, `where_terms`'s empty-term check and the literal text in its error
    // messages both diverge from the oracle on leading/trailing whitespace around a term.
    parts.into_iter().map(|p| p.trim().to_string()).collect()
}

/// `groundworks/gates.py:704 _list_literals`.
fn list_literals(text: &str) -> Option<Vec<String>> {
    let text = text.trim();
    if !(text.starts_with('[') && text.ends_with(']')) {
        return None;
    }
    let inner = text[1..text.len() - 1].trim();
    if inner.is_empty() {
        return None;
    }
    let items = split_top_level(inner, true);
    if items.iter().all(|item| literal_re().is_match(item.trim())) {
        Some(items)
    } else {
        None
    }
}

/// `groundworks/gates.py:716 _where_terms`: `where` as `[(field, [literals])]`, or an error string.
pub fn where_terms(where_clause: &str) -> Result<Vec<(String, Vec<String>)>, String> {
    let where_clause = where_clause.trim();
    if where_clause.is_empty() {
        return Ok(Vec::new());
    }
    let mut terms = Vec::new();
    for part in split_top_level(where_clause, false) {
        if part.is_empty() {
            return Err(format!(
                "cannot build a row for '{where_clause}': it has an empty term"
            ));
        }
        if let Some(caps) = field_in_re().captures(&part) {
            let field = caps[1].to_string();
            let items = list_literals(&caps[2]);
            let Some(items) = items else {
                return Err(format!(
                    "cannot build a row for '{}': the right side of IN is not a non-empty list of literals",
                    part.trim()
                ));
            };
            terms.push((field, items));
            continue;
        }
        let eq = field_eq_re().captures(&part);
        let value_ok = eq
            .as_ref()
            .map(|c| literal_re().is_match(c[2].trim()))
            .unwrap_or(false);
        if eq.is_none() || !value_ok {
            return Err(format!(
                "cannot build a row for '{}': only `field = literal` and `field IN [literals]` joined by AND \
can be turned into evidence",
                part.trim()
            ));
        }
        let caps = eq.unwrap();
        terms.push((caps[1].to_string(), vec![caps[2].trim().to_string()]));
    }
    Ok(terms)
}
