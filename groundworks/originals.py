# -*- coding: utf-8 -*-
"""originals.py -- every unit links to one original, any unit gets its original back, and a unit whose
original cannot be retrieved can never do evidential work.

The owner's rule: "We should have a canonical store. Anything ingested we should be able to link to and
grab an original. We can have many nodes/edges linked to one document, but we must be able to pull that
original document." Evidence you cannot retrieve is not evidence.

The pieces
  groundworks/store.py        the bytes: content-addressed by sha256, verified on every read
  `document` node          one row per original: sha256 (its id, and a unique index), media_type, bytes,
                           capture, source_uri, retrieved_at, original_name, integrity, verified_at
  `unit.document`          a record link, exactly one original per unit; the unit's locator points INTO it.
                           REFERENCE ON DELETE REJECT: a document a unit points at cannot be deleted
                           (measured on SurrealDB 3.1.6), and ASSERT record::exists: a unit cannot point
                           at a document row that is not there
  enforcement_surql(inst)  for every gate rule, an event that refuses the gated change unless every
                           `requires` clause reaching `unit` is met by units whose original is verified
                           (`document.integrity = 'ok'`). Probed both ways by run_probes()
  original_for_unit()      one call: bytes plus document metadata, hash-verified
  verify()                 the walk: every stored file re-hashed, every document row checked against the
                           store (and its integrity recorded), every unit whose original is missing named

Modes (instance.yaml `originals:`, human-owned)
  required     every unit names an original; intake refuses one that does not, naming it
  transition   intake keeps units without an original and counts and names them (without_original.jsonl,
               health `originals.units_without_original`); they stay readable and labellable
  (absent)     treated as transition, and every report says "originals: NOT DECLARED"
In EVERY mode, once enforcement_surql is applied, a unit with no verified original cannot count toward a
gated promotion. The transition relaxes where evidence does no work (reading), never where it does
(promotion): that is how "must" is honoured while existing instances migrate.

What the datastore cannot see: the filesystem. `document.integrity` is what the last load or verify()
found; verify() records what it finds (a missing file turns the row to 'missing', and every gated promotion
resting on it is refused from then on), and original_for_unit() re-hashes on every read regardless.

    python -m groundworks.originals verify INSTANCE.yaml [--url URL --ns NS --db DB] [--no-record]
    python -m groundworks.originals apply INSTANCE.yaml --url URL [--ns NS --db DB]   # enforcement + probes
Standard library only.
"""
import argparse
import hashlib
import json
import re
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks import config  # noqa: E402
from groundworks import gates  # noqa: E402
from groundworks import store as storemod  # noqa: E402

MODES = ("required", "transition")
UNDECLARED = "undeclared"
CAPTURES = ("raw", "rendered_text", "none")
INTEGRITY = ("ok", "missing", "corrupt", "not_captured")
SPAN_KINDS = ("line", "lines", "chars", "whole")
SECTION_KEYS = {"mode", "store", "spans"}
MARKER = "originals refused by rule"
OK = "document.integrity = 'ok'"
TEXT_TYPES = ("application/json", "application/xml", "application/x-ndjson", "application/csv",
              "application/yaml", "application/x-yaml")
SAMPLE = 20


class NoOriginal(storemod.StoreError):
    """The unit links to no retrievable original (none linked, or the original was never captured)."""


# ---- config ------------------------------------------------------------------------------------------

def section(inst_or_data):
    data = inst_or_data.data if hasattr(inst_or_data, "data") else (inst_or_data or {})
    value = data.get("originals")
    if value is None:
        return {}
    return {"mode": value} if isinstance(value, str) else dict(value)


def mode(inst):
    """'required', 'transition', or 'undeclared' (behaves as transition, and says so everywhere)."""
    return section(inst).get("mode") or UNDECLARED


def spans(inst):
    return dict(section(inst).get("spans") or {})


def config_problems(data, problems):
    """Called by config.validate when `originals:` is present."""
    value = data.get("originals")
    body = {"mode": value} if isinstance(value, str) else value
    if not isinstance(body, dict):
        problems.append("originals must be a mode (%s) or a mapping with mode, store, spans" % " | ".join(MODES))
        return
    unknown = sorted(set(body) - SECTION_KEYS)
    if unknown:
        problems.append("originals: unknown key(s) %s; allowed: %s" % (", ".join(unknown), ", ".join(sorted(SECTION_KEYS))))
    if body.get("mode") not in MODES:
        problems.append("originals.mode must be one of %s (got %r)" % (", ".join(MODES), body.get("mode")))
    store = body.get("store")
    if store is not None and not isinstance(store, (str, dict)):
        problems.append("originals.store must be a path or a mapping with a backend")
    units = {u.get("name"): u for u in (data.get("units") or []) if isinstance(u, dict)}
    for unit_type, spec in (body.get("spans") or {}).items():
        where = "originals.spans.%s" % unit_type
        if unit_type not in units:
            problems.append("%s: no unit type %r" % (where, unit_type))
            continue
        if not isinstance(spec, dict) or len(spec) != 1 or next(iter(spec)) not in SPAN_KINDS:
            problems.append("%s: must be exactly one of %s" % (where, ", ".join(SPAN_KINDS)))
            continue
        kind, fields = next(iter(spec.items()))
        locator = [str(f) for f in units[unit_type].get("locator_fields") or []]
        if kind == "whole":
            if fields is not True:
                problems.append("%s: whole must be true" % where)
            continue
        names = [fields] if kind == "line" else fields
        if not isinstance(names, list) or len(names) != (1 if kind == "line" else 2):
            problems.append("%s: %s takes %s" % (where, kind, "one locator field" if kind == "line" else "[start_field, end_field]"))
            continue
        for name in names:
            if str(name) not in locator:
                problems.append("%s: %r is not a locator field of %s (%s)" % (where, name, unit_type, ", ".join(locator)))


# ---- schema ------------------------------------------------------------------------------------------

def doc_rid(sha_or_key):
    return "document:⟨%s⟩" % str(sha_or_key).replace("⟩", "")


def none_key(identity):
    """The id of an original that was never captured: there are no bytes to address it by."""
    return "none_" + hashlib.sha256(str(identity).encode("utf-8")).hexdigest()


def schema_lines():
    """The document table and unit.document. Emitted by gates.schema_surql for every instance."""
    return [
        gates.table_definition("document"),
        "DEFINE FIELD OVERWRITE sha256 ON document TYPE option<string> ASSERT $value = NONE OR "
        "(string::matches($value, '^[0-9a-f]{64}$') AND $value = record::id($this.id));",
        "DEFINE FIELD OVERWRITE media_type ON document TYPE string;",
        "DEFINE FIELD OVERWRITE bytes ON document TYPE option<int> ASSERT $value = NONE OR $value >= 0;",
        "DEFINE FIELD OVERWRITE capture ON document TYPE string ASSERT $value IN %s;" % gates._lit(CAPTURES),
        "DEFINE FIELD OVERWRITE source_uri ON document TYPE option<string>;",
        "DEFINE FIELD OVERWRITE retrieved_at ON document TYPE option<datetime>;",
        "DEFINE FIELD OVERWRITE original_name ON document TYPE option<string>;",
        # captured <=> addressed by its bytes; never captured => can never read as verified
        "DEFINE FIELD OVERWRITE integrity ON document TYPE string ASSERT $value IN %s AND "
        "(($this.capture = 'none') = ($this.sha256 = NONE)) AND ($this.capture != 'none' OR $value = 'not_captured');"
        % gates._lit(INTEGRITY),
        "DEFINE FIELD OVERWRITE verified_at ON document TYPE option<datetime>;",
        "DEFINE INDEX OVERWRITE document_sha256_unique ON document FIELDS sha256 UNIQUE;",
        "DEFINE FIELD OVERWRITE document ON unit TYPE option<record<document>> REFERENCE ON DELETE REJECT "
        "ASSERT $value = NONE OR record::exists($value);",
        "DEFINE INDEX OVERWRITE unit_document ON unit FIELDS document;",
    ]


def ensure_schema_surql():
    """What the loader applies so a graph built before this module has the fields it writes. Never
    DEFINE TABLE OVERWRITE: that would reset the PERMISSIONS access.py put on the table. (schema_lines()[0] is
    the IF NOT EXISTS + ALTER pair from gates.table_definition, which keeps them too; it is skipped here only
    because the loader must never re-shape a table.)"""
    return "\n".join(["DEFINE TABLE IF NOT EXISTS document SCHEMAFULL;"]
                     + [line.replace(" OVERWRITE ", " IF NOT EXISTS ", 1) for line in schema_lines()[1:]]) + "\n"


# ---- enforcement -------------------------------------------------------------------------------------

def enforced_clauses(rule, inst):
    """The requires clauses whose far end is `unit`. Forbids clauses are never relaxed or touched."""
    out = []
    for index, clause in enumerate(rule.get("requires") or []):
        try:
            _, other = gates._edge_path(rule["table"], clause, inst)
        except gates.GateError:
            continue
        if other == "unit":
            out.append((index, clause))
    return out


def _strict(clause, inst):
    where = gates._expand(clause.get("where"), inst)
    strict = dict(clause)
    strict["where"] = "(%s) AND %s" % (where, OK) if where else OK
    return strict


def rule_condition(rule, inst, subject):
    parts = ["%s >= %d" % (gates._traversal(rule["table"], _strict(c, inst), inst, subject=subject), int(c.get("min", 1)))
             for _, c in enforced_clauses(rule, inst)]
    return " AND ".join(parts)


def event_name(rule):
    return "%s_originals" % re.sub(r"[^A-Za-z0-9_]", "_", str(rule["id"]))


def enforcement_surql(inst):
    lines = ["-- generated by groundworks/originals.py: a gated promotion counts only units whose original is verified"]
    for rule in inst.gate_rules():
        if not enforced_clauses(rule, inst):
            continue
        value, field = gates._value_literal(rule["value"]), rule["field"]
        said = ("%s %s: %s.%s may become %s only when its evidence units have a retrievable, verified original "
                "(document.integrity = 'ok'); units with no original, or a missing, corrupt or never-captured one, "
                "do not count; refused " % (MARKER, rule["id"], rule["table"], field, json.dumps(rule["value"])))
        lines.append("DEFINE EVENT OVERWRITE %s ON TABLE %s WHEN $after.%s = %s AND ($before.%s ?? NONE) != %s THEN { "
                     "IF !(%s) { THROW %s + <string> $after.id; }; };"
                     % (event_name(rule), rule["table"], field, value, field, value,
                        rule_condition(rule, inst, "$after.id"), gates._value_literal(said)))
    return "\n".join(lines) + "\n"


def enforcement_status(db, inst):
    expected = [(rule["table"], event_name(rule)) for rule in inst.gate_rules() if enforced_clauses(rule, inst)]
    missing = []
    for table, name in expected:
        info = db.sql("INFO FOR TABLE %s" % table, expect=1)[0] or {}
        if name not in (info.get("events") or {}):
            missing.append("%s on %s" % (name, table))
    return {"expected": len(expected), "present": len(expected) - len(missing), "missing": missing}


def refused_by(message, rule_id):
    return ("%s %s:" % (MARKER, rule_id)) in str(message or "")


# ---- probes, both ways ---------------------------------------------------------------------------------

def _probe_sha(rule_id):
    return hashlib.sha256(("groundworks originals probe %s" % rule_id).encode("utf-8")).hexdigest()


def run_probes(inst, db):
    """For every rule with a clause reaching unit, on a graph where enforcement_surql is applied:
      accept                        evidence units with a verified original: the promotion is taken
      reject_without_original       the same units with no original: refused by THIS rule's originals event
      reject_original_not_verified  the original marked missing: refused by it again
      restored_accept               the original verified again: taken again
    Built from the gate library's own probe plan, so the rest of the rule is satisfied and only the original
    changes. What cannot be built is reported with its reason and counts as misbehaved."""
    report = {"rules": [], "rejected_ok": 0, "accepted_ok": 0, "misbehaved": 0}
    plans = {p["rule"]: p for p in gates.probes(inst)}
    for rule in inst.gate_rules():
        if not enforced_clauses(rule, inst):
            continue
        entry = {"rule": rule["id"], "clauses": [c.get("edge") for _, c in enforced_clauses(rule, inst)]}
        plan = plans.get(rule["id"])
        if not plan or not plan.get("unpromote"):
            entry["not_built"] = "the gate library produced no probe plan that can be un-promoted for this rule"
        elif not all(p["built"] for p in plan["evidence"]):
            entry["not_built"] = "the gate library could not build accepting evidence for every requires clause"
        else:
            _probe_rule(db, rule, plan, entry, inst)
        flags = ("accept", "reject_without_original", "reject_original_not_verified", "restored_accept")
        entry["behaved"] = not entry.get("not_built") and not entry.get("error") and not entry.get("cleanup_errors") \
            and all(entry.get(f) is True for f in flags)
        report["rejected_ok"] += sum(1 for f in flags[1:3] if entry.get(f) is True)
        report["accepted_ok"] += sum(1 for f in (flags[0], flags[3]) if entry.get(f) is True)
        report["misbehaved"] += 0 if entry["behaved"] else 1
        report["rules"].append(entry)
    if not report["rules"]:
        report["note"] = "no gate rule has a requires clause reaching unit: nothing for originals to enforce"
    return report


def _probe_rule(db, rule, plan, entry, inst=None):
    from groundworks.pipeline.loader import q
    sha = _probe_sha(rule["id"])
    doc = doc_rid(sha)
    tag = q("zz_probe_%s" % rule["id"])
    attach = "UPDATE unit SET document = %s WHERE string::contains(record::id(id), %s);" % (doc, tag)
    detach = "UPDATE unit SET document = NONE WHERE string::contains(record::id(id), %s);" % tag
    attempt, unpromote = plan["attempt"], plan["unpromote"]
    # Who proposes and who promotes. Root, for an ordinary rule. For a rule that requires an independent promoter
    # root can never make the change (it has no identity), so the row is written by one provisioned credential and
    # promoted by others, exactly as gates.run_probes does. A promoter whose change went through is on the row's
    # record of writers from then on, so the first accept is made by `promoter` and every later attempt by
    # `promoter_b`, a credential (and a principal) of its own.
    writer, first, later = db, db, db
    run = gates.ProbeRunner(inst, db) if inst is not None else db
    if inst is not None and config.independence_of(rule):
        identities, why = gates._probe_identities(inst, db, rule, extra=("promoter_b",))
        if identities is None:
            entry["not_built"] = ("the rule requires an independent promoter and the probe could not provision the "
                                  "credentials that must make the promotion: %s" % why)
            return
        writer, first, later = identities["proposer"], identities["promoter"], identities["promoter_b"]

    def try_attempt(conn):
        ok, message = conn.try_sql(attempt)
        if ok:
            db.sql(unpromote)
        return ok, message

    try:
        if inst is not None:
            problem = run.prepare(gates.credential_statements(plan))
            if problem:
                entry["not_built"] = problem
                return
        db.sql("UPSERT %s SET sha256 = %s, media_type = 'application/octet-stream', bytes = 0, capture = 'raw', "
               "integrity = 'ok', original_name = 'groundworks originals probe';" % (doc, q(sha)))
        db.sql(plan["setup"][0])
        writer.sql(plan["setup"][1])    # the row under test, written by its proposer
        for statement in plan["setup"][2:]:
            db.sql(statement)
        db.sql(attach)
        for statement in plan["fix"]:
            run.sql(statement)
            db.sql(attach)              # a staged promotion later in the fix needs its units' original now
        if first is not db and config.independence_of(rule)["own_evidence"]:
            link = gates._own_link(plan, inst, identities["role"])
            if link is None:
                entry["not_built"] = ("the rule requires the promoter's own evidence and no evidence link could be built "
                                      "on an edge role %s writes" % identities["role"])
                return
            for conn in (first, later):
                conn.sql(link)
        ok, message = try_attempt(first)
        entry["accept"], entry["accept_error"] = ok, message
        db.sql(detach)
        ok, message = try_attempt(later)
        entry["reject_without_original"], entry["without_original_error"] = (not ok and refused_by(message, rule["id"])), message
        db.sql(attach)
        db.sql("UPDATE %s SET integrity = 'missing';" % doc)
        ok, message = try_attempt(later)
        entry["reject_original_not_verified"], entry["not_verified_error"] = (not ok and refused_by(message, rule["id"])), message
        db.sql("UPDATE %s SET integrity = 'ok';" % doc)
        ok, message = try_attempt(later)
        entry["restored_accept"], entry["restored_error"] = ok, message
    except Exception as exc:          # recorded, judged misbehaved; cleanup still runs
        entry["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:500])
    finally:
        # The gate library's cleanup names tables an instance may not have (agent, when no access is applied), so
        # a failed statement proves nothing either way. What is judged is the effect: no probe row left behind.
        for statement in [detach, "DELETE %s;" % doc] + list(plan["cleanup"]):
            db.try_sql(statement)
        leftovers = []
        for table in inst_tables(db):
            n = _count(db, "SELECT count() AS n FROM %s WHERE string::contains(<string> record::id(id), %s) GROUP ALL"
                       % (table, tag))
            if n:
                leftovers.append("%d %s row(s)" % (n, table))
        if _count(db, "SELECT count() AS n FROM %s GROUP ALL" % doc):
            leftovers.append("the probe document %s" % sha)
        if leftovers:
            entry["cleanup_errors"] = ["left behind after cleanup: %s" % ", ".join(leftovers)]


def inst_tables(db):
    """Every table the datastore holds, so a leftover in a table the probe plan did not name is still found."""
    info = db.sql("INFO FOR DB", expect=1)[0] or {}
    return sorted(info.get("tables") or {})


# ---- retrieval -----------------------------------------------------------------------------------------

def _meta(row):
    keys = ("sha256", "media_type", "bytes", "capture", "source_uri", "retrieved_at", "original_name", "integrity",
            "verified_at")
    out = {k: row.get(k) for k in keys}
    out["id"] = str(row.get("key") or row.get("id"))
    return out


def document_key(ref):
    """A document id from 'document:<sha>', 'sha256:<sha>', '<sha>' or 'none_<sha>'."""
    ref = str(ref or "").strip()
    for prefix in ("document:", "sha256:"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
    ref = ref.strip("⟨⟩`")
    if storemod.HEX.match(ref) or (ref.startswith("none_") and storemod.HEX.match(ref[5:])):
        return ref
    return None


def document_meta(db, ref):
    key = document_key(ref)
    if not key:
        raise storemod.StoreError("%r is not a document reference (document:<sha256>, sha256:<sha256> or the hex)" % (ref,))
    rows = db.sql("SELECT *, record::id(id) AS key FROM %s" % doc_rid(key), expect=1)[0]
    if not rows:
        raise NoOriginal("no document row %s" % key)
    meta = _meta(rows[0])
    meta["units"] = (db.sql("SELECT count() AS n FROM unit WHERE document = %s GROUP ALL" % doc_rid(key), expect=1)[0]
                     or [{"n": 0}])[0]["n"]
    return meta


def original_for_document(db, ref, store):
    meta = document_meta(db, ref)
    if meta["capture"] == "none" or not meta["sha256"]:
        raise NoOriginal("document %s (%s) was never captured: there are no bytes to retrieve"
                         % (meta["id"], meta.get("original_name") or meta.get("source_uri") or "unnamed"))
    data = store.get(meta["sha256"], name=meta.get("original_name") or meta.get("source_uri"))
    if meta.get("bytes") is not None and meta["bytes"] != len(data):
        raise storemod.CorruptOriginal(meta["sha256"], "(length %d, recorded %d)" % (len(data), meta["bytes"]),
                                       meta.get("original_name"))
    return {"document": meta, "bytes": data}


def original_for_unit(db, unit_id, store):
    """The original a unit was extracted from: {"unit", "locator", "document": metadata, "bytes"}, re-hashed.
    NoOriginal when the unit links to none; MissingOriginal / CorruptOriginal (naming it) from the store."""
    from groundworks.pipeline.loader import rid
    ident = str(unit_id)
    ident = ident.split(":", 1)[1] if ident.startswith("unit:") else ident
    rows = db.sql("SELECT record::id(id) AS key, locator, <string> document AS doc FROM %s" % rid("unit", ident.strip("⟨⟩")),
                  expect=1)[0]
    if not rows:
        raise NoOriginal("no unit %s" % ident)
    if not document_key(rows[0].get("doc")):
        raise NoOriginal("unit %s links to no original: its text is an extraction nothing can be checked against"
                         % ident)
    found = original_for_document(db, rows[0]["doc"], store)
    found.update({"unit": str(rows[0]["key"]), "locator": rows[0].get("locator")})
    return found


def is_text(meta, data):
    media = str(meta.get("media_type") or "").split(";")[0].strip().lower()
    if not (media.startswith("text/") or media in TEXT_TYPES or media.endswith("+json") or media.endswith("+xml")):
        return False
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def span_text(data, locator, spec):
    """The text of the unit's span inside a text original, or raise ValueError saying why it cannot be cut."""
    text = data.decode("utf-8")
    kind, fields = next(iter(spec.items()))
    if kind == "whole":
        return text
    locator = locator or {}
    names = [fields] if kind == "line" else list(fields)
    values = []
    for name in names:
        value = locator.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("locator field %r is %r, not an integer position" % (name, value))
        values.append(value)
    if kind in ("line", "lines"):
        lines = text.splitlines()
        start, end = values[0], values[-1]
        if not 1 <= start <= end <= len(lines):
            raise ValueError("lines %d-%d are outside the original's %d line(s)" % (start, end, len(lines)))
        return "\n".join(lines[start - 1:end])
    start, end = values
    if not 0 <= start <= end <= len(text):
        raise ValueError("characters %d-%d are outside the original's %d" % (start, end, len(text)))
    return text[start:end]


# ---- verify and health -------------------------------------------------------------------------------

def _count(db, statement):
    rows = db.sql(statement, expect=1)[0]
    return rows[0]["n"] if rows else 0


def verify(db, store, inst=None, record=True):
    """Walk the whole store and the whole graph. Every missing or corrupt original, every unit whose document
    row is gone, every unit with no original, named. With record=True each document's integrity is written as
    found, so the datastore's enforcement refuses promotions on what is no longer retrievable."""
    rep = {"store": store.describe(), "mode": mode(inst) if inst else None, "store_files": 0, "corrupt_files": [],
           "invalid_names": [], "documents": 0, "documents_missing": [], "documents_corrupt": [],
           "documents_not_captured": [], "documents_unreferenced": 0, "store_files_without_document": [],
           "units": 0, "units_with_verified_original": 0, "units_without_original": 0,
           "units_without_original_sample": [], "units_with_dangling_document": [], "units_original_not_ok": 0,
           "units_original_not_ok_sample": [], "integrity_recorded": [], "recorded": bool(record)}
    files = {}
    for name in store.hashes():
        if name.startswith("INVALID_NAME:"):
            rep["invalid_names"].append(name.split(":", 1)[1])
            continue
        files[name] = store.verify(name)
        rep["store_files"] += 1
        if files[name] == "corrupt":
            rep["corrupt_files"].append(name)
    rows = db.sql("SELECT record::id(id) AS key, sha256, capture, integrity, original_name, source_uri FROM document",
                  expect=1)[0]
    rep["documents"] = len(rows)
    known = set()
    for row in rows:
        name = row.get("original_name") or row.get("source_uri") or row["key"]
        if row.get("capture") == "none" or not row.get("sha256"):
            found = "not_captured"
            rep["documents_not_captured"].append({"id": row["key"], "name": name})
        else:
            known.add(row["sha256"])
            found = files.get(row["sha256"]) or store.verify(row["sha256"])
            if found == "missing":
                rep["documents_missing"].append({"id": row["key"], "name": name})
            elif found == "corrupt":
                rep["documents_corrupt"].append({"id": row["key"], "name": name})
        if found != row.get("integrity"):
            rep["integrity_recorded"].append({"id": row["key"], "name": name, "was": row.get("integrity"), "now": found})
            if record:
                db.sql("UPDATE %s SET integrity = %s, verified_at = time::now()" % (doc_rid(row["key"]), json.dumps(found)),
                       expect=1)
        elif record:
            db.sql("UPDATE %s SET verified_at = time::now()" % doc_rid(row["key"]), expect=1)
    rep["store_files_without_document"] = sorted(set(files) - known)
    rep["units"] = _count(db, "SELECT count() AS n FROM unit GROUP ALL")
    rep["units_without_original"] = _count(db, "SELECT count() AS n FROM unit WHERE document = NONE GROUP ALL")
    rep["units_without_original_sample"] = db.sql(
        "SELECT VALUE record::id(id) FROM unit WHERE document = NONE ORDER BY id LIMIT %d" % SAMPLE, expect=1)[0]
    rep["units_with_dangling_document"] = db.sql(
        "SELECT VALUE record::id(id) FROM unit WHERE document != NONE AND record::exists(document) = false", expect=1)[0]
    rep["units_with_verified_original"] = _count(
        db, "SELECT count() AS n FROM unit WHERE document != NONE AND %s GROUP ALL" % OK)
    rep["units_original_not_ok"] = _count(
        db, "SELECT count() AS n FROM unit WHERE document != NONE AND document.integrity != 'ok' GROUP ALL")
    rep["units_original_not_ok_sample"] = db.sql(
        "SELECT VALUE record::id(id) FROM unit WHERE document != NONE AND document.integrity != 'ok' ORDER BY id LIMIT %d"
        % SAMPLE, expect=1)[0]
    rep["documents_unreferenced"] = _count(
        db, "SELECT count() AS n FROM document WHERE count(<~unit) = 0 GROUP ALL")
    rep["problems"] = (len(rep["corrupt_files"]) + len(rep["invalid_names"]) + len(rep["documents_missing"])
                       + len(rep["documents_corrupt"]) + len(rep["units_with_dangling_document"]))
    rep["gap"] = rep["units"] - rep["units_with_verified_original"]
    rep["ok"] = rep["problems"] == 0 and (rep["mode"] != "required" or rep["gap"] == 0)
    return rep


def verify_lines(rep):
    out = ["originals    mode %s; %s" % (rep["mode"] if rep["mode"] != UNDECLARED else "NOT DECLARED (treated as transition)",
                                         rep["store"]),
           "             store: %d file(s), %d CORRUPT, %d with an invalid name; %d not referenced by any document row"
           % (rep["store_files"], len(rep["corrupt_files"]), len(rep["invalid_names"]), len(rep["store_files_without_document"])),
           "             documents: %d, %d MISSING, %d CORRUPT, %d never captured, %d referenced by no unit"
           % (rep["documents"], len(rep["documents_missing"]), len(rep["documents_corrupt"]),
              len(rep["documents_not_captured"]), rep["documents_unreferenced"]),
           "             units: %d, %d with a verified original, %d WITHOUT any original, %d whose original is not "
           "verified, %d pointing at a document row that is gone"
           % (rep["units"], rep["units_with_verified_original"], rep["units_without_original"],
              rep["units_original_not_ok"], len(rep["units_with_dangling_document"]))]
    for key in ("documents_missing", "documents_corrupt"):
        for item in rep[key]:
            out.append("   %s %s (%s)" % (key.split("_")[1].upper(), item["id"], item["name"]))
    for sha in rep["corrupt_files"]:
        out.append("   CORRUPT FILE %s" % sha)
    for uid in rep["units_with_dangling_document"]:
        out.append("   DANGLING unit %s: its document row is gone" % uid)
    for change in rep["integrity_recorded"]:
        out.append("   integrity %s (%s): %s -> %s%s" % (change["id"], change["name"], change["was"], change["now"],
                                                        "" if rep["recorded"] else " (NOT recorded: --no-record)"))
    if rep["gap"]:
        out.append("   GAP: %d unit(s) cannot support a gated promotion (no verified original); first: %s"
                   % (rep["gap"], ", ".join(map(str, (rep["units_without_original_sample"] + rep["units_original_not_ok_sample"])[:SAMPLE]))))
    out.append("             %s" % ("PASS" if rep["ok"] else "FAIL: %d integrity problem(s)%s"
                                   % (rep["problems"], ", and originals are required but %d unit(s) have none verified"
                                      % rep["gap"] if rep["mode"] == "required" and rep["gap"] else "")))
    return out


def _enforcement_or_why_not(db, inst):
    """enforcement_status, or why it could not be read. INFO FOR TABLE is refused to a record credential (measured,
    3.1.6: "IAM error: Not enough permissions"), and an MCP server launched as an agent must still answer health."""
    from groundworks import db as dbmod
    try:
        return enforcement_status(db, inst)
    except dbmod.DbError as exc:
        if getattr(db, "access", None):
            return {"NOT_EXECUTED": "an agent credential cannot read table definitions (%s); run "
                                    "`python -m groundworks.originals verify` as root" % str(exc).splitlines()[0][:160]}
        raise


def health(db, inst):
    """Datastore-only numbers (no store walk): the gap, by name, next to the flattering count."""
    # `WHERE true`: without a WHERE, SurrealDB 3.1.6 answers count() GROUP ALL with 0 to a record credential that can
    # read every row (measured 2026-09-16; root sees the true count), and an agent's health read a gap of 0
    units = _count(db, "SELECT count() AS n FROM unit WHERE true GROUP ALL")
    verified = _count(db, "SELECT count() AS n FROM unit WHERE document != NONE AND %s GROUP ALL" % OK)
    without = _count(db, "SELECT count() AS n FROM unit WHERE document = NONE GROUP ALL")
    by_integrity = {r["integrity"]: r["n"] for r in db.sql(
        "SELECT integrity, count() AS n FROM document GROUP BY integrity", expect=1)[0]}
    by_capture = {r["capture"]: r["n"] for r in db.sql(
        "SELECT capture, count() AS n FROM document GROUP BY capture", expect=1)[0]}
    drift = []
    for rule in inst.gate_rules():
        if not enforced_clauses(rule, inst):
            continue
        ids = db.sql("SELECT VALUE <string> id FROM %s WHERE %s = %s AND NOT (%s)"
                     % (rule["table"], rule["field"], gates._value_literal(rule["value"]),
                        rule_condition(rule, inst, "$this.id")), expect=1)[0]
        drift.extend({"rule": rule["id"], "row": i} for i in ids)
    linked = _count(db, "SELECT count() AS n FROM unit WHERE document != NONE GROUP ALL")
    gap = units - verified
    if linked and not _count(db, "SELECT count() AS n FROM document WHERE true GROUP ALL"):
        # units link originals this connection cannot read, so every one would look unverified: not a gap, a blind spot
        gap = "NOT_EXECUTED: %d unit(s) link an original, and this connection can read no document row" % linked
    return {"mode": mode(inst), "units": units, "units_with_verified_original": verified,
            "units_without_original": without, "units_original_not_verified": units - verified - without,
            # the originals gap: units that cannot support a gated promotion, for either reason
            "gap_units_without_verified_original": gap,
            "units_without_original_sample": db.sql(
                "SELECT VALUE record::id(id) FROM unit WHERE document = NONE ORDER BY id LIMIT %d" % SAMPLE, expect=1)[0],
            "documents_by_integrity": by_integrity, "documents_by_capture": by_capture,
            "enforcement": _enforcement_or_why_not(db, inst),
            "DRIFT_promoted_without_verified_original": len(drift), "drift_rows": drift[:SAMPLE]}


# ---- command -------------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Originals: verify the store and the graph, or apply enforcement.")
    parser.add_argument("command", choices=["verify", "apply"])
    parser.add_argument("instance")
    parser.add_argument("--url", default="http://127.0.0.1:8002/sql")
    parser.add_argument("--ns")
    parser.add_argument("--db")
    parser.add_argument("--no-record", action="store_true", help="verify: report integrity changes without writing them")
    args = parser.parse_args(argv)
    from groundworks import db as dbmod
    inst = config.load(args.instance)
    conn = dbmod.Db(url=args.url, ns=args.ns or inst.id, db=args.db or inst.id)
    conn.ping()
    if args.command == "apply":
        conn.script(enforcement_surql(inst))
        report = run_probes(inst, conn)
        print(json.dumps(report, indent=2))
        print("ORIGINALS PROBES: %d rejected as required, %d accepted as required, %d misbehaved"
              % (report["rejected_ok"], report["accepted_ok"], report["misbehaved"]))
        return 1 if report["misbehaved"] else 0
    rep = verify(conn, storemod.for_instance(inst), inst, record=not args.no_record)
    for line in verify_lines(rep):
        print(line)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
