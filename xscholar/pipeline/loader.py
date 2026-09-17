# -*- coding: utf-8 -*-
"""loader.py -- units and passing labels into the datastore. Bulk loads never create claims.

  documents -> one document row per original the units name (xscholar/originals.py), integrity as the
            store finds it at load; loaded before the units, whose `document` link must resolve
  units  -> unit rows (upsert by id), each with its `document` link or NONE
  labels -> label rows at grade 'agent', plus bears_on edges to their targets (conclusion rows,
            one per vocabulary target, created if missing, never marked ready here)

Edges get stable ids from (scope, edge, src, dst, k) so a re-run updates in place instead of
deleting and recreating (which would fire demotion events downstream). After a fully successful
run, edges of this scope not written by this run are swept. A failed write blocks the sweep.
Every batch is counted: fewer result blocks than statements is a failure.

Corrections survive a reload (V2-19). A unit's `status` and `grade` are the evidence axes the gates read,
and a reload used to UPSERT both from source unconditionally, so a human correction was silently put
back on every rebuild. Measured on the demo vocabulary: redacted -> corrected to in_window -> reload ->
redacted, no report. `redacted` is unusable, so that revert can pull the support out from under a
confirmed fact. Now each unit carries `source` (what the source said at the last load, and which run)
and `corrected` (which fields are held against the source, by whom, when). On reload, per field:

  new unit                               take the source value
  not held, still what the source said   follow the source; a real source change still lands, counted
  held (corrected)                       keep the held value; a differing source value is a CONFLICT
  changed since the last load without    a correction made outside correct_unit(): held the same way,
    correct_unit()                         attributed UNATTRIBUTED, reported
  row predates provenance and differs    a correction cannot be told from a source change: held,
    from the source                        reported as a conflict with reason `no_provenance`

Neither side wins silently. Every held value that disagrees with the source is listed in
counts["conflict_rows"] and counted in counts["conflicts"]. The only way to let the source replace a
held value is to name the unit and field in `accept_source`; a pair that holds nothing in this load is
a LoadError before anything is written, so a typo cannot pass as an acceptance.

    counts = load(units_path, labels_dir, db, instance, scope="load", accept_source=[("u17", "status")])
    correct_unit(db, "u17", "status", "in_window", by="reviewer_1", note="why")
Standard library only.
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.pipeline.intake import read_units  # noqa: E402
from xscholar.patterns import edge_ids  # noqa: E402
from xscholar import originals  # noqa: E402
from xscholar import store as storemod  # noqa: E402

BATCH = 50
PLAIN_ID = re.compile(r"^[A-Za-z0-9_]+$")


# The unit fields the gates read as evidence axes: status (usable at all) and grade (fidelity).
HELD_FIELDS = ("status", "grade")
# plan key -> counts key; counts[<key>] is always len(counts[<plan key>_rows])
REPORTED = {"held": "held", "conflict": "conflicts", "accepted": "accepted", "followed": "followed"}


class LoadError(Exception):
    pass


def rid(table, ident):
    """A record id, quoted when the id has characters SurrealQL would misread."""
    ident = str(ident)
    return "%s:%s" % (table, ident) if PLAIN_ID.match(ident) else "%s:⟨%s⟩" % (table, ident)


def q(value):
    """A SurrealQL literal for a Python value. None becomes NONE (absent), never NULL."""
    if value is None:
        return "NONE"
    return json.dumps(value, ensure_ascii=False)


def _run_batches(db, statements, counts, key):
    for start in range(0, len(statements), BATCH):
        batch = statements[start:start + BATCH]
        results = db.sql("; ".join(batch) + ";", expect=len(batch))
        if len(results) != len(batch):
            raise LoadError("batch returned %d results for %d statements" % (len(results), len(batch)))
        counts[key] += len(batch)


def provenance_surql():
    """The loader's own bookkeeping fields on `unit`. load() applies them itself, so every caller has
    them: the unit table is SCHEMAFULL and refuses an undefined field loudly (measured, SurrealDB 3.1.6)."""
    return ("DEFINE FIELD OVERWRITE source ON unit TYPE option<object> FLEXIBLE;\n"
            "DEFINE FIELD OVERWRITE corrected ON unit TYPE option<object> FLEXIBLE;\n")


def correct_unit(db, unit_id, field, value, by, note=None):
    """A correction of a unit's status or grade, held against the source on every later load.
    The schema's ASSERT still decides whether `value` is allowed. Returns the row after the write."""
    if field not in HELD_FIELDS:
        raise LoadError("only %s can be corrected on a unit, not %r" % (", ".join(HELD_FIELDS), field))
    if not str(by or "").strip():
        raise LoadError("a correction needs `by`: an unattributed correction is what this exists to prevent")
    db.script(provenance_surql())
    rows = db.sql("UPDATE %s SET %s = %s, corrected.%s = {by: %s, reason: 'corrected', at: time::now(), note: %s} "
                  "RETURN AFTER" % (rid("unit", unit_id), field, q(value), field, q(str(by)), q(note)), expect=1)[0]
    if not rows:
        raise LoadError("no unit %s to correct" % unit_id)
    return rows[0]


def reconcile(existing, unit, run, accept=frozenset()):
    """Decide what one load writes to one unit's held fields. Pure: no datastore.

    existing  the stored row ({status, grade, source, corrected}), or None for a new unit
    accept    (unit_id, field) pairs a human named so the source replaces a held value
    Returns {"set": {field: value}, "corrected": {field: hold or None to release}, "source": {...},
             "held": [...], "conflict": [...], "accepted": [...], "followed": [...]}."""
    uid = str(unit["id"])
    plan = {"set": {}, "corrected": {}, "source": {"status": unit["status"], "grade": unit["grade"], "run": run},
            "held": [], "conflict": [], "accepted": [], "followed": []}
    if existing is None:
        plan["set"] = {f: unit[f] for f in HELD_FIELDS}
        return plan
    source = existing.get("source") or {}
    corrected = existing.get("corrected") or {}
    for field in HELD_FIELDS:
        current, incoming = existing.get(field), unit[field]
        if corrected.get(field):
            reason, by = corrected[field].get("reason") or "corrected", corrected[field].get("by")
        elif field not in source:
            # No record of what the source said: a differing value may be a correction or a source
            # change, and guessing "source change" is exactly the silent revert. Hold it and say so.
            reason, by = (("no_provenance", "UNKNOWN: row predates load provenance")
                          if current != incoming else (None, None))
        elif current != source[field]:
            reason, by = ("changed_outside_loader",
                          "UNATTRIBUTED: changed without correct_unit() after run %s" % source.get("run"))
        else:
            reason, by = None, None
        if reason is None:                        # nobody corrected it: the source decides
            if current != incoming:
                plan["followed"].append({"unit": uid, "field": field, "was": current, "now": incoming})
            plan["set"][field] = incoming
            continue
        row = {"unit": uid, "field": field, "corrected": current, "source": incoming, "reason": reason, "by": by}
        if (uid, field) in accept:                # a human named this pair: the source replaces the held value
            plan["accepted"].append(row)
            plan["set"][field] = incoming
            plan["corrected"][field] = None
            continue
        plan["held"].append(row)
        if not corrected.get(field):              # first sighting: put the hold on the row
            plan["corrected"][field] = {"by": by, "reason": reason, "detected_run": run}
        if current != incoming:
            plan["conflict"].append(row)
    return plan


def _unit_statements(db, units, token, accept_source, counts):
    """(statement, unit id) per unit, with every held/conflict/accepted/followed row added to counts."""
    accept = {(str(u), f) for u, f in (accept_source or ())}
    bad = sorted({f for _, f in accept if f not in HELD_FIELDS})
    if bad:
        raise LoadError("accept_source names field(s) %s; only %s are held" % (", ".join(bad), ", ".join(HELD_FIELDS)))
    stored = {str(r["key"]): r for r in db.sql(
        "SELECT record::id(id) AS key, status, grade, source, corrected FROM unit", expect=1)[0]}
    statements, used = [], set()
    for unit in units:
        existing = stored.get(str(unit["id"]))
        plan = reconcile(existing, unit, token, accept)
        used.update((a["unit"], a["field"]) for a in plan["accepted"])
        for kind in REPORTED:
            counts[kind + "_rows"].extend(plan[kind])
        sets = ["type=%s" % q(unit["type"]), "text=%s" % q(unit["text"]), "locator=%s" % q(unit["locator"]),
                "hash=%s" % q(unit["hash"]), "meta=%s" % q(unit.get("meta") or {}),
                "document=%s" % (originals.doc_rid(unit["document"]) if unit.get("document") else "NONE")]
        sets += ["%s=%s" % (f, q(v)) for f, v in plan["set"].items()]
        for f, hold in plan["corrected"].items():
            sets.append("corrected.%s=%s" % (f, "NONE" if hold is None else
                                             "{by: %s, reason: %s, detected_run: %s, at: time::now()}"
                                             % (q(hold["by"]), q(hold["reason"]), q(hold["detected_run"]))))
        sets.append("source=%s" % q(plan["source"]))
        if existing is None:
            statements.append(("CREATE %s SET %s" % (rid("unit", unit["id"]), ", ".join(sets)), unit["id"]))
        else:
            # Guarded: a correction that lands between the read above and this write makes the write
            # match nothing, and the load stops loudly instead of overwriting a value it never saw.
            guard = " AND ".join("%s = %s" % (f, q(existing.get(f))) for f in HELD_FIELDS)
            statements.append(("UPDATE %s SET %s WHERE %s" % (rid("unit", unit["id"]), ", ".join(sets), guard),
                               unit["id"]))
    unused = sorted(accept - used)
    if unused:
        raise LoadError("accept_source names %s, which hold(s) no correction in this load; nothing was written"
                        % ", ".join("%s.%s" % pair for pair in unused))
    return statements


def _documents_statements(units, documents_path, store, inst, counts):
    """UPSERT one document row per original the units name, its integrity as the store finds it NOW.

    A unit naming an original that documents.jsonl does not describe is a LoadError before anything is
    written: the datastore would refuse the unit's link anyway (record::exists), but by then half a batch
    could be in. An original that is missing or corrupt in the store is loaded with that integrity, counted
    and named: its units are in the graph and readable, and no gated promotion can rest on them."""
    named = {u["document"] for u in units if u.get("document")}
    counts["units_with_document"] = sum(1 for u in units if u.get("document"))
    counts["units_without_document"] = len(units) - counts["units_with_document"]
    path = pathlib.Path(documents_path)
    manifest = {}
    if path.is_file():
        manifest = {row["id"]: row for row in read_units(path)}
    missing = sorted(named - set(manifest))
    if missing:
        raise LoadError("%d unit original(s) are not described in %s (first: %s); re-run intake so units and "
                        "documents.jsonl come from the same run" % (len(missing), path, ", ".join(missing[:5])))
    statements = []
    for key in sorted(named):
        row = manifest[key]
        if row["capture"] == "none":
            integrity = "not_captured"
        else:
            if store is None:
                try:
                    store = storemod.for_instance(inst)
                except storemod.StoreError as exc:
                    raise LoadError("units name originals, but %s" % exc) from exc
            integrity = store.verify(row["sha256"])
        if integrity != "ok":
            counts["originals_not_ok"].append({"id": key, "name": row.get("original_name"), "integrity": integrity})
        statements.append(
            "UPSERT %s SET sha256=%s, media_type=%s, bytes=%s, capture=%s, source_uri=%s, retrieved_at=%s, "
            "original_name=%s, integrity=%s, verified_at=time::now()"
            % (originals.doc_rid(key), q(row.get("sha256")), q(row["media_type"]), q(row.get("bytes")),
               q(row["capture"]), q(row.get("source_uri")),
               "<datetime> %s" % q(row["retrieved_at"]) if row.get("retrieved_at") else "NONE",
               q(row.get("original_name")), q(integrity)))
    return statements


def load(units_path, labels_dir, db, inst, scope="load", accept_source=(), store=None, documents_path=None):
    """`documents_path` defaults to documents.jsonl beside `units_path` (where intake writes it); `store`
    defaults to the instance's own. Neither is touched when no unit names an original."""
    token = edge_ids.run_token()
    counts = {"units": 0, "labels": 0, "targets": 0, "edges": 0, "swept": 0, "run": token, "documents": 0,
              "originals_not_ok": []}
    for kind, key in REPORTED.items():
        counts[key], counts[kind + "_rows"] = 0, []
    keyer = edge_ids.Keyer()
    failed = False
    try:
        # 1. units: held fields reconciled against corrections, never silently overwritten
        db.script(provenance_surql())
        db.script(originals.ensure_schema_surql())
        units = read_units(units_path)
        # 0. originals first: a unit's link is refused unless its document row exists
        doc_statements = _documents_statements(
            units, documents_path or pathlib.Path(units_path).parent / "documents.jsonl", store, inst, counts)
        _run_batches(db, doc_statements, counts, "documents")
        statements = _unit_statements(db, units, token, accept_source, counts)
        for kind, key in REPORTED.items():
            counts[key] = len(counts[kind + "_rows"])
        for start in range(0, len(statements), BATCH):
            batch = statements[start:start + BATCH]
            results = db.sql("; ".join(s for s, _ in batch) + ";", expect=len(batch))
            if len(results) != len(batch):
                raise LoadError("batch returned %d results for %d statements" % (len(results), len(batch)))
            missed = [str(uid) for (_, uid), rows in zip(batch, results) if not rows]
            if missed:
                raise LoadError("unit(s) %s changed between read and write (a correction mid-load?); not "
                                "overwritten, load stopped: re-run it" % ", ".join(missed))
            counts["units"] += len(batch)
        # 2. targets as conclusion rows (never ready from a load)
        statements = []
        for target in inst.vocabulary.get("targets") or []:
            statements.append("UPSERT %s SET statement = IF statement != NONE { statement } ELSE { %s }"
                              % (rid("conclusion", target), q(target)))
        _run_batches(db, statements, counts, "targets")
        # 3. labels and bears_on edges
        statements, edge_statements = [], []
        labels_root = pathlib.Path(labels_dir)
        if not labels_root.is_dir():
            # glob() on a path that does not exist returns nothing and raises nothing, so a mistyped
            # labels directory used to read as "this run has no labels" -- and then swept every edge.
            raise LoadError("labels directory %s does not exist; a mistyped path is not an empty run"
                            % labels_root)
        label_files = sorted(labels_root.glob("*.jsonl"))
        for labels_file in label_files:
            chunk = labels_file.stem
            for label in read_units(labels_file):
                lid = "%s__%s" % (chunk, label["id"])
                statements.append(
                    "UPSERT %s SET unit=%s, tier=%s, dir=%s, issues=%s, targets=%s, role=%s, reason=%s, "
                    "quote=%s, grade='agent', chunk=%s, run=%s"
                    % (rid("label", lid), rid("unit", label["id"]), q(label["tier"]), q(label.get("dir")),
                       q(label.get("issues") or []), q(label.get("targets") or []), q(label.get("role")),
                       q(label.get("reason")), q(label.get("quote")), q(chunk), q(token)))
                for target in label.get("targets") or []:
                    eid = keyer(scope, "bears_on", lid, target)
                    edge_statements.append(
                        "RELATE %s->bears_on:%s->%s SET seed=%s, run=%s, tier=%s, dir=%s"
                        % (rid("label", lid), eid, rid("conclusion", target), q(scope), q(token),
                           q(label["tier"]), q(label.get("dir"))))
        _run_batches(db, statements, counts, "labels")
        _run_batches(db, edge_statements, counts, "edges")
    except Exception:
        failed = True
        raise
    finally:
        if failed:
            counts["sweep"] = "SKIPPED: a write failed, previous edges kept"
    # 4. sweep only after a fully successful run that actually read labels.
    # A run that read no label files is not evidence that the previous run's edges are stale: it is
    # evidence that this run found nothing. Writing zero statements is not a failed write, so the
    # `failed` flag above never caught it, and the sweep deleted the whole scope on the strength of a
    # run that wrote nothing. Measured: an empty labels directory took bears_on from 3 rows to 0 and
    # returned {'labels': 0, 'edges': 0, 'swept': 3} with no error.
    if not label_files:
        counts["sweep"] = ("SKIPPED: no *.jsonl label files in %s; previous edges kept" % labels_root)
        return counts
    before = db.sql("SELECT count() AS n FROM bears_on WHERE seed = %s AND run != %s GROUP ALL" % (q(scope), q(token)),
                    expect=1)[0]
    stale = before[0]["n"] if before else 0
    if stale:
        db.sql("DELETE bears_on WHERE seed = %s AND run != %s" % (q(scope), q(token)), expect=1)
    counts["swept"] = stale
    return counts
