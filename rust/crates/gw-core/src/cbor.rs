//! cbor.rs -- SurrealDB's typed RPC encoding, for [`crate::db::Db::query`].
//!
//! Why CBOR: measured on SurrealDB 3.1.6, the JSON form of `/rpc` re-reads string variables that
//! look like a record id, at any depth, and keeps only the record part: the variable
//! `"gate: cd rust && cargo test"` arrived as the record `gate:cd`. In CBOR a text string is
//! always a string. Requests are encoded from `serde_json::Value`; responses are decoded back to
//! `serde_json::Value`, turning SurrealDB's custom tags into plain JSON (a record id becomes
//! `"table:id"`, a datetime becomes RFC 3339 text, NONE becomes null).
//!
//! Not in the Python oracle; its own tests are its gate.

use ciborium::value::{Integer, Value as Cbor};
use serde_json::{Map, Number, Value};

// SurrealDB's CBOR tags (protocol spec).
const TAG_DATETIME_TEXT: u64 = 0;
const TAG_NONE: u64 = 6;
const TAG_TABLE: u64 = 7;
const TAG_RECORD_ID: u64 = 8;
const TAG_UUID_TEXT: u64 = 9;
const TAG_DECIMAL_TEXT: u64 = 10;
const TAG_DATETIME_COMPACT: u64 = 12;
const TAG_DURATION_TEXT: u64 = 13;
const TAG_DURATION_COMPACT: u64 = 14;

/// Encode a JSON value as CBOR: text stays text, numbers stay numbers.
pub fn encode(value: &Value) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    ciborium::into_writer(value, &mut out).map_err(|e| format!("could not encode a CBOR request: {e}"))?;
    Ok(out)
}

/// Decode a CBOR response into JSON, flattening SurrealDB's tags.
pub fn decode(bytes: &[u8]) -> Result<Value, String> {
    let value: Cbor = ciborium::from_reader(bytes).map_err(|e| format!("server did not return CBOR: {e}"))?;
    Ok(to_json(value))
}

fn to_json(value: Cbor) -> Value {
    match value {
        Cbor::Null => Value::Null,
        Cbor::Bool(b) => Value::Bool(b),
        Cbor::Integer(i) => integer(i),
        Cbor::Float(f) => Number::from_f64(f).map_or(Value::Null, Value::Number),
        Cbor::Text(s) => Value::String(s),
        Cbor::Bytes(b) => Value::Array(b.into_iter().map(Value::from).collect()),
        Cbor::Array(items) => Value::Array(items.into_iter().map(to_json).collect()),
        Cbor::Map(pairs) => {
            let mut map = Map::with_capacity(pairs.len());
            for (k, v) in pairs {
                let key = match to_json(k) {
                    Value::String(s) => s,
                    other => other.to_string(),
                };
                map.insert(key, to_json(v));
            }
            Value::Object(map)
        }
        Cbor::Tag(tag, inner) => tagged(tag, *inner),
        _ => Value::Null,
    }
}

fn integer(i: Integer) -> Value {
    let wide = i128::from(i);
    if let Ok(n) = i64::try_from(wide) {
        Value::from(n)
    } else if let Ok(n) = u64::try_from(wide) {
        Value::from(n)
    } else {
        Value::String(wide.to_string())
    }
}

fn tagged(tag: u64, inner: Cbor) -> Value {
    match (tag, inner) {
        (TAG_NONE, _) => Value::Null,
        (TAG_RECORD_ID, Cbor::Array(parts)) if parts.len() == 2 => {
            let mut parts = parts.into_iter();
            let table = match to_json(parts.next().expect("two parts")) {
                Value::String(s) => s,
                other => other.to_string(),
            };
            let id = match to_json(parts.next().expect("two parts")) {
                Value::String(s) => s,
                other => other.to_string(),
            };
            Value::String(format!("{table}:{id}"))
        }
        (TAG_DATETIME_COMPACT, Cbor::Array(parts)) => {
            let mut parts = parts.into_iter().map(to_json);
            let secs = parts.next().and_then(|v| v.as_i64()).unwrap_or(0);
            let nanos = parts.next().and_then(|v| v.as_u64()).unwrap_or(0);
            Value::String(rfc3339(secs, nanos))
        }
        (TAG_DURATION_COMPACT, Cbor::Array(parts)) => {
            let mut parts = parts.into_iter().map(to_json);
            let secs = parts.next().and_then(|v| v.as_u64()).unwrap_or(0);
            let nanos = parts.next().and_then(|v| v.as_u64()).unwrap_or(0);
            Value::String(if nanos == 0 { format!("{secs}s") } else { format!("{secs}s{nanos}ns") })
        }
        (TAG_DATETIME_TEXT | TAG_TABLE | TAG_RECORD_ID | TAG_UUID_TEXT | TAG_DECIMAL_TEXT | TAG_DURATION_TEXT, inner) => {
            to_json(inner)
        }
        (_, inner) => to_json(inner),
    }
}

/// Seconds and nanoseconds since the Unix epoch as `YYYY-MM-DDTHH:MM:SS[.fraction]Z`.
fn rfc3339(secs: i64, nanos: u64) -> String {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    let mut out = format!("{year:04}-{month:02}-{day:02}T{h:02}:{m:02}:{s:02}");
    if nanos > 0 {
        let fraction = format!("{nanos:09}");
        out.push('.');
        out.push_str(fraction.trim_end_matches('0'));
    }
    out.push('Z');
    out
}

/// Howard Hinnant's days-to-civil: days since 1970-01-01 to (year, month, day), proleptic Gregorian.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn round_trip_tag(tag: u64, inner: Cbor) -> Value {
        let mut bytes = Vec::new();
        ciborium::into_writer(&Cbor::Tag(tag, Box::new(inner)), &mut bytes).unwrap();
        decode(&bytes).unwrap()
    }

    #[test]
    fn text_that_looks_like_a_record_stays_text() {
        let request = json!({"command": "gate: cd rust && cargo test", "n": 5, "ok": true});
        let back = decode(&encode(&request).unwrap()).unwrap();
        assert_eq!(back, request);
    }

    #[test]
    fn surreal_tags_become_plain_json() {
        assert_eq!(
            round_trip_tag(TAG_RECORD_ID, Cbor::Array(vec![Cbor::Text("receipt".into()), Cbor::Text("ab12".into())])),
            json!("receipt:ab12")
        );
        assert_eq!(round_trip_tag(TAG_NONE, Cbor::Null), Value::Null);
        assert_eq!(
            round_trip_tag(
                TAG_DATETIME_COMPACT,
                Cbor::Array(vec![Cbor::Integer(1_761_284_828i64.into()), Cbor::Integer(500_000_000u64.into())])
            ),
            json!("2025-10-24T05:47:08.5Z")
        );
        assert_eq!(round_trip_tag(TAG_DATETIME_TEXT, Cbor::Text("2026-10-24T05:47:08Z".into())), json!("2026-10-24T05:47:08Z"));
    }

    #[test]
    fn the_calendar_is_right_at_its_edges() {
        assert_eq!(rfc3339(0, 0), "1970-01-01T00:00:00Z");
        assert_eq!(rfc3339(951_782_400, 0), "2000-02-29T00:00:00Z");
        assert_eq!(rfc3339(-1, 0), "1969-12-31T23:59:59Z");
    }
}
