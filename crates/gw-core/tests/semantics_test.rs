//! Fixtures beyond `tests/test_config.py`'s own corpus: the port-01 fix round's GATE DEPTH work.
//!
//! Two sources drove these, both cited inline per case:
//!   - the top-tier review at the end of the port-01 ticket Log: it planted 12 plausible-but-wrong
//!     config loaders and found the `--config` differential corpus caught only 3 (chunk_size
//!     positivity, fidelity.confirming-cannot-be-every-grade, clause min default -- those three
//!     already have a `tests/test_config.py` case, ported in `test_config.rs`). The other 9 had NO
//!     existing oracle test at all (the oracle's own `tests/test_config.py` never exercises
//!     `lenses:`, `unique:`, or the `independent:` mapping form's default), so each gets a NEW test
//!     here rather than a port.
//!   - `_tickets/notes/2026-09-19-python-semantics-divergence-checklist.md`: the three CONFIRMED
//!     divergences already fixed get a regression test each, plus the new-evidence items in this
//!     ticket's own scope (`config/` -- not gates.py/db.py/pipeline/*, which are other tickets').

mod common;

use common::*;
use gw_core::config;
use serde_json::json;

// ---- the review's 3 CONFIRMED accept/refuse divergences (regression tests) ---------------------

#[test]
fn chunk_size_true_is_accepted_bool_is_int() {
    // `config.py:553-555`: `isinstance(True, int)` is True, so `chunk_size: true` == 1.
    let data = broken1("units", json!([{"name": "line", "locator_fields": ["f"], "chunk_size": true}]));
    assert_accepted(data);
    // and `false` == 0, still refused as non-positive.
    let data = broken1("units", json!([{"name": "line", "locator_fields": ["f"], "chunk_size": false}]));
    assert_refused(data, "positive chunk_size");
}

#[test]
fn originals_store_null_is_accepted() {
    // `originals.py:102-104`: `store is not None`, not "is present" -- an explicit `store: null`
    // passes the guard exactly like a missing key.
    let data = broken(&[
        ("human_owned", Some(json!(["gates", "fidelity", "status", "evaluator", "privacy", "originals"]))),
        ("originals", Some(json!({"mode": "required", "store": null}))),
    ]);
    assert_accepted(data);
}

#[test]
fn human_owned_as_a_string_is_substring_membership() {
    // `config.py:648-649`: `k not in owned` where `owned` is a STRING checks substring
    // containment, not element equality.
    let data = broken1("human_owned", json!("gates and fidelity and status and evaluator and privacy"));
    assert_accepted(data);
    // a human_owned string missing one required word is still refused, naming it.
    let data = broken1("human_owned", json!("gates and fidelity and status and privacy"));
    assert_refused(data, "human_owned must include evaluator");
}

// ---- the review's 9 previously-uncaught mutations: no oracle test existed, so these are NEW -----

#[test]
fn lens_names_must_be_unique() {
    let lens = |name: &str| {
        json!({"name": name, "kind": "perspective", "charter": "a charter long enough to mean something"})
    };
    let data = broken1("lenses", json!([lens("dup"), lens("dup")]));
    assert_refused(data, "lens names must be unique");
}

#[test]
fn only_the_loader_may_write_label() {
    let data = broken1(
        "access",
        json!({"roles": {"reviewer": {"read": ["unit", "label"], "write": ["label"]}}}),
    );
    assert_refused(data, "only the loader writes labels");
    // the loader itself may.
    let data = broken1(
        "access",
        json!({"roles": {"loader": {"read": ["unit", "label"], "write": ["label"]}}}),
    );
    assert_accepted(data);
}

#[test]
fn required_top_missing_sections_are_named_in_declared_order() {
    // A `REQUIRED_TOP` reorder is invisible unless a case checks the ORDER two missing sections
    // are named in, not just that each is named. `config.py:31` declares
    // `["instance", "units", "fidelity", "status", "vocabulary", "gates", "evaluator",
    // "human_owned", "privacy"]` -- "units" precedes "gates", so removing both must report "units"
    // first.
    let data = broken(&[("units", None), ("gates", None)]);
    match config::load_from_mapping(data) {
        Ok(_) => panic!("expected a refusal"),
        Err(e) => {
            let msg = e.to_string();
            let units_at = msg.find("missing required section: units").expect("units named");
            let gates_at = msg.find("missing required section: gates").expect("gates named");
            assert!(units_at < gates_at, "expected units before gates in: {msg}");
        }
    }
}

#[test]
fn lens_rejects_an_unknown_key() {
    let data = broken1(
        "lenses",
        json!([{"name": "l1", "kind": "perspective", "charter": "a charter long enough to mean something",
                "extra_thing": true}]),
    );
    assert_refused(data, "unknown key(s) extra_thing");
}

#[test]
fn unique_lists_a_field_twice() {
    let data = broken1("unique", json!({"fact": ["status", "status"]}));
    assert_refused(data, "unique.fact: lists a field twice");
}

#[test]
fn is_id_rejects_uppercase() {
    // `ID_RE = re.compile(r"^[a-z0-9_]+$")` -- lowercase only. A widened `is_id` that also
    // accepted uppercase would silently let "Toy" through.
    assert_refused(broken1("instance__id", json!("TOY")), "must match");
    assert_refused(broken1("instance__id", json!("Toy")), "must match");
}

#[test]
fn independent_between_defaults_to_credential_not_principal() {
    // `independence_of` (`config.py:118`): `value.get("between", "credential")` -- the mapping
    // form's default, with NO `between` key at all, must be "credential". A flipped default
    // (`"principal"`) is invisible to any test that always spells `between` explicitly.
    let rule = json!({"id": "g1", "independent": {"own_evidence": true}});
    let mode = config::independence_of(&rule).expect("a mapping form with content is Some");
    assert_eq!(mode["between"], "credential");
}

// ---- new-evidence checklist items in this ticket's scope (config/ only) -------------------------

#[test]
fn field_optional_accepts_bool_is_int() {
    let data = broken1(
        "tables",
        json!({"event": {"fields": {"when": {"type": "string", "optional": 1}}}}),
    );
    let inst = assert_accepted(data);
    assert_eq!(inst.table_fields("event")["when"]["optional"], json!(true));
    let data = broken1(
        "tables",
        json!({"event": {"fields": {"when": {"type": "string", "optional": 2}}}}),
    );
    assert_refused(data, "optional must be true or false");
}

#[test]
fn access_role_lens_bound_accepts_bool_is_int_and_null() {
    let data = broken1(
        "access",
        json!({"roles": {"reader": {"read": ["unit"], "lens_bound": 1}}}),
    );
    assert_accepted(data);
    let data = broken1(
        "access",
        json!({"roles": {"reader": {"read": ["unit"], "lens_bound": null}}}),
    );
    assert_accepted(data);
    let data = broken1(
        "access",
        json!({"roles": {"reader": {"read": ["unit"], "lens_bound": "yes"}}}),
    );
    assert_refused(data, "lens_bound must be true or false");
}

#[test]
fn vocabulary_tiers_zero_accepts_bool_and_float() {
    assert_accepted(broken1("vocabulary__tiers", json!([false, 1, 2, 3])));
    assert_accepted(broken1("vocabulary__tiers", json!([0.0, 1, 2, 3])));
}

#[test]
fn vocabulary_tiers_mixed_types_is_a_type_error_not_a_refusal() {
    // checklist rule 14: `sorted([0, "a"])` raises TypeError out of the oracle's `validate()` --
    // its own verdict, not a `ConfigError` refusal. The candidate must be able to express the same
    // third outcome, not silently accept (treating incomparable as "already sorted") or refuse
    // with the wrong kind.
    let data = broken1("vocabulary__tiers", json!([0, "a"]));
    match config::load_from_mapping(data) {
        Ok(_) => panic!("expected a TypeError-kind ConfigError, got ACCEPT"),
        Err(e) => {
            assert_eq!(e.kind, "TypeError", "expected the TypeError kind, got: {e}");
            assert!(e.to_string().contains("not supported between instances of"), "got: {e}");
        }
    }
}

#[test]
fn instance_id_accepts_one_trailing_newline() {
    // checklist rule 10: Python's `$` (no re.MULTILINE) also matches just before ONE trailing
    // `\n`. `re.match(r"^[a-z0-9_]+$", "demo\n")` succeeds.
    assert_accepted(broken1("instance__id", json!("toy\n")));
    // but not two, nor an embedded one.
    assert_refused(broken1("instance__id", json!("toy\n\n")), "must match");
    assert_refused(broken1("instance__id", json!("to\ny")), "must match");
}

#[test]
fn chunk_size_accepts_underscore_grouped_int_in_yaml() {
    // checklist rule 11: `int("1_000")` == 1000 in Python. Only reachable through the YAML
    // scalar parser (a JSON-built fixture can't carry an un-quoted `1_000` distinctly from the
    // string `"1_000"`), so this one goes through `parse_yaml`, not `broken()`.
    let text = "a: 1_000\n";
    let got = config::parse_yaml(text).unwrap();
    assert_eq!(got["a"], 1000);
}

#[test]
fn expand_where_stringifies_a_bool_the_python_way() {
    // checklist rule 5: `str(True) == "True"`, not `"true"`. `expand_where` reads raw config data,
    // not a validated `Instance`, so this needs no full valid instance to exercise.
    let data = broken1("fidelity__confirming", json!([true]));
    let where_ = config::expand_where(&data, Some("grade IN $confirming"));
    assert_eq!(where_, "grade IN ['True']");
}

#[test]
fn unit_names_dedup_follows_raw_set_bool_is_int() {
    // checklist rule 6 / config.py:556: `set(names)` on the RAW values -- `1` and `true` hash-equal
    // in Python, so `["a", true]` and `["a", 1]` are as much "one name repeated" as `["a", "a"]`
    // would be, from `set()`'s point of view. This unit's `name` field, though, is compared through
    // `unit["name"] == name` elsewhere as a plain value, and `is_id()`/`ID_RE` require a string
    // shape besides -- so the practical, gate-relevant case is two unit dicts whose `name` is the
    // identical NON-string value, which the port's `py_str_of`-based list-uniqueness check must
    // still flag as a duplicate the same way `[1, true]` fails Python's `set()` (both stringify to
    // legal-looking-but-colliding text and neither is a valid `[a-z0-9_]+` name regardless).
    let data = broken1(
        "units",
        json!([
            {"name": true, "locator_fields": ["f"], "chunk_size": 1},
            {"name": 1, "locator_fields": ["f"], "chunk_size": 1},
        ]),
    );
    // Both are refused for their shape (`must match [a-z0-9_]+`) before uniqueness is even
    // meaningful; the two problems together prove neither name silently normalized to something
    // legal-looking, which would have hidden the dedup question entirely.
    assert_refused(data, "must match");
}
