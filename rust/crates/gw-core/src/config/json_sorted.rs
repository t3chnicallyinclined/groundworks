//! `json.dumps(value, indent=2, sort_keys=True, default=str)` re-implemented over `serde_json::Value`.
//!
//! `gates.spec(inst)` is compared as "sorted JSON per instance" (RUST_PORT.md s.1.2): the harness's
//! own emitter calls Python's `json.dumps` with those exact keyword arguments, so this candidate's
//! output must match byte for byte, including Python's `ensure_ascii=True` default (non-ASCII
//! characters escaped as `\uXXXX`) and its two-space indent style (no trailing space after a comma,
//! `", "` becomes just a newline).

use serde_json::Value;

pub fn dumps_sorted_indent2(value: &Value) -> String {
    let mut out = String::new();
    write_value(value, 0, &mut out);
    out
}

fn write_value(value: &Value, depth: usize, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => out.push_str(&n.to_string()),
        Value::String(s) => write_string(s, out),
        Value::Array(items) => write_array(items, depth, out),
        Value::Object(map) => write_object(map, depth, out),
    }
}

fn write_array(items: &[Value], depth: usize, out: &mut String) {
    if items.is_empty() {
        out.push_str("[]");
        return;
    }
    out.push('[');
    let inner_indent = "  ".repeat(depth + 1);
    for (i, item) in items.iter().enumerate() {
        out.push('\n');
        out.push_str(&inner_indent);
        write_value(item, depth + 1, out);
        if i + 1 < items.len() {
            out.push(',');
        }
    }
    out.push('\n');
    out.push_str(&"  ".repeat(depth));
    out.push(']');
}

fn write_object(map: &serde_json::Map<String, Value>, depth: usize, out: &mut String) {
    if map.is_empty() {
        out.push_str("{}");
        return;
    }
    let mut keys: Vec<&String> = map.keys().collect();
    keys.sort();
    out.push('{');
    let inner_indent = "  ".repeat(depth + 1);
    for (i, key) in keys.iter().enumerate() {
        out.push('\n');
        out.push_str(&inner_indent);
        write_string(key, out);
        out.push_str(": ");
        write_value(&map[*key], depth + 1, out);
        if i + 1 < keys.len() {
            out.push(',');
        }
    }
    out.push('\n');
    out.push_str(&"  ".repeat(depth));
    out.push('}');
}

/// Python `json.dumps` string escaping with the default `ensure_ascii=True`: control characters and
/// anything outside ASCII become `\uXXXX` (surrogate pairs for astral code points), `"`/`\` are
/// backslash-escaped, and `\n`/`\r`/`\t` use their short forms.
fn write_string(s: &str, out: &mut String) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c if (c as u32) < 0x7f => out.push(c),
            c => {
                let cp = c as u32;
                if cp > 0xFFFF {
                    let v = cp - 0x10000;
                    let high = 0xD800 + (v >> 10);
                    let low = 0xDC00 + (v & 0x3FF);
                    out.push_str(&format!("\\u{high:04x}\\u{low:04x}"));
                } else {
                    out.push_str(&format!("\\u{cp:04x}"));
                }
            }
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn sorts_keys_and_indents_by_two() {
        let v = json!({"b": 1, "a": [1, 2], "c": {}});
        assert_eq!(
            dumps_sorted_indent2(&v),
            "{\n  \"a\": [\n    1,\n    2\n  ],\n  \"b\": 1,\n  \"c\": {}\n}"
        );
    }

    #[test]
    fn escapes_non_ascii() {
        let v = json!("caf\u{e9}");
        assert_eq!(dumps_sorted_indent2(&v), "\"caf\\u00e9\"");
    }
}
