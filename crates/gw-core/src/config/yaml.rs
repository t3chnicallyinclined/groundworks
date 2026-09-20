//! The strict YAML subset `groundworks/config.py` accepts, ported line for line (`parse_yaml` and
//! its helpers). Mappings, lists, scalars, one-line `[a, b]` / `{a: b}` flow collections, `#`
//! comments. Anything else raises `YamlError`, naming the line, exactly as the oracle does.
//!
//! Deliberately NOT `serde_yaml`: the oracle's error text is part of the byte-exact gate, and a
//! general YAML parser accepts things this subset refuses (anchors, block scalars, tabs) and would
//! not raise the same message when it does refuse. `serde_json::Value` (workspace dep, `preserve_order`
//! feature) is the parsed shape only, so key order matches Python dict insertion order.

use serde_json::{Map, Value};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct YamlError(pub String);

impl std::fmt::Display for YamlError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}
impl std::error::Error for YamlError {}

type R<T> = Result<T, YamlError>;

const SCALAR_TRUE: &[&str] = &["true", "yes", "on"];
const SCALAR_FALSE: &[&str] = &["false", "no", "off"];
const SCALAR_NULL: &[&str] = &["null", "none", "~", ""];

struct Line {
    number: usize,
    indent: usize,
    content: String,
}

pub fn parse_yaml(text: &str) -> R<Value> {
    let mut lines: Vec<Line> = Vec::new();
    for (i, raw) in text.lines().enumerate() {
        let number = i + 1;
        let before_comment = raw.split('#').next().unwrap_or("");
        if before_comment.contains('\t') {
            return Err(YamlError(format!("line {number}: tabs are not allowed for indentation")));
        }
        let stripped = strip_comment(raw);
        if stripped.trim().is_empty() {
            continue;
        }
        let lstripped = stripped.trim_start();
        if lstripped.starts_with('&') || lstripped.starts_with('*') || lstripped.starts_with("---") {
            return Err(YamlError(format!(
                "line {number}: anchors, aliases and documents are not supported"
            )));
        }
        if ends_with_block_scalar_marker(&stripped) {
            return Err(YamlError(format!("line {number}: multi-line scalars are not supported")));
        }
        let indent = stripped.len() - lstripped.len();
        lines.push(Line { number, indent, content: lstripped.trim_end().to_string() });
    }
    if lines.is_empty() {
        let (value, index) = parse_block(&lines, 0, 0)?;
        if index != lines.len() {
            return Err(YamlError(format!("line {}: unexpected indentation", lines[index].number)));
        }
        return Ok(value);
    }
    let base_indent = lines[0].indent;
    let (value, index) = parse_block(&lines, 0, base_indent)?;
    if index != lines.len() {
        return Err(YamlError(format!("line {}: unexpected indentation", lines[index].number)));
    }
    Ok(value)
}

/// `re.search(r":\s*[|>]\s*$", stripped)` -- a colon, optional whitespace, then `|` or `>`, then only
/// whitespace to end of line, ANYWHERE in the line (not anchored to start).
fn ends_with_block_scalar_marker(stripped: &str) -> bool {
    let bytes: Vec<char> = stripped.chars().collect();
    let n = bytes.len();
    // trailing whitespace run
    let mut end = n;
    while end > 0 && bytes[end - 1].is_whitespace() {
        end -= 1;
    }
    if end == 0 {
        return false;
    }
    let marker = bytes[end - 1];
    if marker != '|' && marker != '>' {
        return false;
    }
    let mut i = end - 1;
    while i > 0 && bytes[i - 1].is_whitespace() {
        i -= 1;
    }
    i > 0 && bytes[i - 1] == ':'
}

fn strip_comment(line: &str) -> String {
    let mut out = String::new();
    let mut quote: Option<char> = None;
    for ch in line.chars() {
        if let Some(q) = quote {
            if ch == q {
                quote = None;
            }
            out.push(ch);
            continue;
        }
        if ch == '\'' || ch == '"' {
            quote = Some(ch);
            out.push(ch);
            continue;
        }
        if ch == '#' {
            break;
        }
        out.push(ch);
    }
    out.trim_end().to_string()
}

fn parse_block(lines: &[Line], index: usize, indent: usize) -> R<(Value, usize)> {
    if index >= lines.len() {
        return Ok((Value::Null, index));
    }
    if lines[index].content.starts_with("- ") {
        parse_list(lines, index, indent)
    } else {
        parse_map(lines, index, indent)
    }
}

fn parse_map(lines: &[Line], mut index: usize, indent: usize) -> R<(Value, usize)> {
    let mut out = Map::new();
    while index < lines.len() {
        let line = &lines[index];
        if line.indent < indent {
            break;
        }
        if line.indent > indent {
            return Err(YamlError(format!("line {}: unexpected indentation", line.number)));
        }
        if line.content.starts_with("- ") {
            break;
        }
        let content = line.content.clone();
        let number = line.number;
        let Some(colon) = content.find(':') else {
            return Err(YamlError(format!("line {number}: expected 'key: value'")));
        };
        let key = content[..colon].trim().to_string();
        let rest = content[colon + 1..].trim().to_string();
        if out.contains_key(&key) {
            return Err(YamlError(format!("line {number}: duplicate key {}", py_repr_str(&key))));
        }
        if !rest.is_empty() {
            out.insert(key, scalar(&rest, number)?);
            index += 1;
        } else {
            let child_indent = if index + 1 < lines.len() { lines[index + 1].indent } else { line.indent };
            if index + 1 >= lines.len() || child_indent <= line.indent {
                out.insert(key, Value::Null);
                index += 1;
            } else {
                let (value, next) = parse_block(lines, index + 1, child_indent)?;
                out.insert(key, value);
                index = next;
            }
        }
    }
    Ok((Value::Object(out), index))
}

fn parse_list(lines: &[Line], mut index: usize, indent: usize) -> R<(Value, usize)> {
    let mut out: Vec<Value> = Vec::new();
    while index < lines.len() {
        let line = &lines[index];
        let is_item = line.content.starts_with("- ");
        if line.indent < indent || !is_item {
            break;
        }
        if line.indent > indent {
            return Err(YamlError(format!("line {}: unexpected indentation", line.number)));
        }
        let number = line.number;
        let item = if line.content == "-" { String::new() } else { line.content[2..].trim().to_string() };
        let starts_flow = item.starts_with('{')
            || item.starts_with('[')
            || item.starts_with('"')
            || item.starts_with('\'');
        if !item.is_empty() && item.contains(':') && !starts_flow {
            let colon = item.find(':').unwrap();
            let key = item[..colon].trim().to_string();
            let rest = item[colon + 1..].trim().to_string();
            let mut entry = Map::new();
            if !rest.is_empty() {
                entry.insert(key, scalar(&rest, number)?);
            }
            index += 1;
            while index < lines.len()
                && lines[index].indent > line.indent
                && !(lines[index].content.starts_with("- "))
            {
                let child_indent = lines[index].indent;
                let (child, next) = parse_map(lines, index, child_indent)?;
                if let Value::Object(child_map) = child {
                    for (k, v) in child_map {
                        entry.insert(k, v);
                    }
                }
                index = next;
            }
            out.push(Value::Object(entry));
            continue;
        }
        out.push(scalar(&item, number)?);
        index += 1;
    }
    Ok((Value::Array(out), index))
}

fn scalar(text: &str, number: usize) -> R<Value> {
    let text = text.trim();
    if text.starts_with('[') || text.starts_with('{') {
        return flow(text, number);
    }
    if text.chars().count() >= 2 {
        let first = text.chars().next().unwrap();
        let last = text.chars().last().unwrap();
        if first == last && (first == '\'' || first == '"') {
            let inner: String = text.chars().skip(1).take(text.chars().count() - 2).collect();
            return Ok(Value::String(inner));
        }
    }
    let low = text.to_lowercase();
    if SCALAR_TRUE.contains(&low.as_str()) {
        return Ok(Value::Bool(true));
    }
    if SCALAR_FALSE.contains(&low.as_str()) {
        return Ok(Value::Bool(false));
    }
    if SCALAR_NULL.contains(&low.as_str()) {
        return Ok(Value::Null);
    }
    if let Some(n) = py_int(text) {
        return Ok(Value::Number(n.into()));
    }
    if let Some(f) = py_float(text) {
        if let Some(num) = serde_json::Number::from_f64(f) {
            return Ok(Value::Number(num));
        }
    }
    if text.chars().any(|c| "&*|>".contains(c)) {
        return Err(YamlError(format!("line {number}: unsupported characters in scalar {}", py_repr_str(text))));
    }
    Ok(Value::String(text.to_string()))
}

/// Python `int(text)`: optional sign, ASCII digits only (no leading/trailing junk, no float syntax).
fn py_int(text: &str) -> Option<i64> {
    let t = text.trim();
    if t.is_empty() {
        return None;
    }
    let body = t.strip_prefix(['+', '-']).unwrap_or(t);
    if body.is_empty() || !body.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    t.parse::<i64>().ok()
}

/// Python `float(text)`, restricted to the forms that matter here: standard decimal/exponent syntax.
/// (Python's `float()` also accepts "inf"/"nan" case-insensitively; matched here too since the
/// scalar has already failed the null/bool checks by this point.)
fn py_float(text: &str) -> Option<f64> {
    let t = text.trim();
    if t.is_empty() {
        return None;
    }
    let lower = t.to_ascii_lowercase();
    let body = lower.strip_prefix(['+', '-']).unwrap_or(&lower);
    if body == "inf" || body == "infinity" || body == "nan" {
        return t.parse::<f64>().ok();
    }
    // Reject things Rust's f64 parser accepts but Python's float() text form here should not need
    // (hex floats aren't part of this subset's inputs); a plain decimal/exponent check is enough.
    if !t.chars().all(|c| c.is_ascii_digit() || "+-.eE".contains(c)) {
        return None;
    }
    if !t.chars().any(|c| c.is_ascii_digit()) {
        return None;
    }
    t.parse::<f64>().ok()
}

/// One-line `[a, b]` or `{a: b, c: [d]}`, via JSON after quoting bare words (`_flow`).
fn flow(text: &str, number: usize) -> R<Value> {
    let mut out = String::new();
    let chars: Vec<char> = text.chars().collect();
    let mut i = 0usize;
    let mut quote: Option<char> = None;
    while i < chars.len() {
        let ch = chars[i];
        if let Some(q) = quote {
            out.push(ch);
            if ch == q {
                quote = None;
            }
            i += 1;
            continue;
        }
        if ch == '\'' || ch == '"' {
            quote = Some('"');
            out.push('"');
            i += 1;
            continue;
        }
        if "[]{}:, ".contains(ch) {
            out.push(ch);
            i += 1;
            continue;
        }
        let start = i;
        while i < chars.len() && !",[]{}: ".contains(chars[i]) {
            i += 1;
        }
        let word: String = chars[start..i].iter().collect();
        let bare = word.trim();
        let low = bare.to_lowercase();
        if SCALAR_TRUE.contains(&low.as_str()) {
            out.push_str("true");
        } else if SCALAR_FALSE.contains(&low.as_str()) {
            out.push_str("false");
        } else if SCALAR_NULL.contains(&low.as_str()) {
            out.push_str("null");
        } else if is_plain_number(bare) {
            out.push_str(bare);
        } else {
            out.push('"');
            out.push_str(&bare.replace('\\', "\\\\").replace('"', "\\\""));
            out.push('"');
        }
    }
    serde_json::from_str::<Value>(&out)
        .map_err(|e| YamlError(format!("line {number}: cannot parse {} ({e})", py_repr_str(text))))
}

fn is_plain_number(text: &str) -> bool {
    if text.is_empty() {
        return false;
    }
    let body = text.strip_prefix('-').unwrap_or(text);
    if body.is_empty() {
        return false;
    }
    let mut parts = body.splitn(2, '.');
    let int_part = parts.next().unwrap_or("");
    let frac_part = parts.next();
    if int_part.is_empty() || !int_part.chars().all(|c| c.is_ascii_digit()) {
        return false;
    }
    match frac_part {
        None => true,
        Some(f) => !f.is_empty() && f.chars().all(|c| c.is_ascii_digit()),
    }
}

/// Python's `%r` on a short ASCII string: `'text'`, with `'` and `\` escaped. Good enough for the
/// handful of places config.py uses `%r` on a key name or short token inside an error message.
pub fn py_repr_str(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('\'');
    for ch in s.chars() {
        if ch == '\'' || ch == '\\' {
            out.push('\\');
        }
        out.push(ch);
    }
    out.push('\'');
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_maps_lists_flows_comments_and_quotes() {
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
        let got = parse_yaml(text).unwrap();
        assert_eq!(got["a"], 1);
        assert_eq!(got["b"], "text with spaces");
        assert_eq!(got["c"], "quoted: colon");
        assert_eq!(got["d"], serde_json::json!(["x", "y", 3]));
        assert_eq!(got["e"], serde_json::json!({"k": "v", "n": [1, 2]}));
        assert_eq!(got["f"], serde_json::json!({"g": true, "h": null}));
        assert_eq!(got["items"][0], serde_json::json!({"name": "one", "size": 2}));
        assert_eq!(got["items"][1]["nested"], serde_json::json!({"deep": 3.5}));
        assert_eq!(got["plain"], serde_json::json!(["a", "b"]));
    }

    #[test]
    fn rejects_what_it_cannot_parse() {
        for text in [
            "a:\n\tb: 1\n",
            "a: &anchor 1\n",
            "a: |\n  block\n",
            "a: 1\na: 2\n",
            "a: 1\n   b: 2\n",
            "no colon here\n",
        ] {
            assert!(parse_yaml(text).is_err(), "expected an error for {text:?}");
        }
    }
}
