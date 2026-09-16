# -*- coding: utf-8 -*-
"""loader.py -- units and passing labels into the datastore. Bulk loads never create claims.

  units  -> unit rows (upsert by id)
  labels -> label rows at grade 'agent', plus bears_on edges to their targets (conclusion rows,
            one per vocabulary target, created if missing, never marked ready here)

Edges get stable ids from (scope, edge, src, dst, k) so a re-run updates in place instead of
deleting and recreating (which would fire demotion events downstream). After a fully successful
run, edges of this scope not written by this run are swept. A failed write blocks the sweep.
Every batch is counted: fewer result blocks than statements is a failure.

    counts = load(units_path, labels_dir, db, instance, scope="load")
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

BATCH = 50
PLAIN_ID = re.compile(r"^[A-Za-z0-9_]+$")


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


def load(units_path, labels_dir, db, inst, scope="load"):
    token = edge_ids.run_token()
    counts = {"units": 0, "labels": 0, "targets": 0, "edges": 0, "swept": 0, "run": token}
    keyer = edge_ids.Keyer()
    failed = False
    try:
        # 1. units
        statements = []
        for unit in read_units(units_path):
            statements.append(
                "UPSERT %s SET type=%s, text=%s, locator=%s, status=%s, grade=%s, hash=%s, meta=%s"
                % (rid("unit", unit["id"]), q(unit["type"]), q(unit["text"]), q(unit["locator"]),
                   q(unit["status"]), q(unit["grade"]), q(unit["hash"]), q(unit.get("meta") or {})))
        _run_batches(db, statements, counts, "units")
        # 2. targets as conclusion rows (never ready from a load)
        statements = []
        for target in inst.vocabulary.get("targets") or []:
            statements.append("UPSERT %s SET statement = IF statement != NONE { statement } ELSE { %s }"
                              % (rid("conclusion", target), q(target)))
        _run_batches(db, statements, counts, "targets")
        # 3. labels and bears_on edges
        statements, edge_statements = [], []
        for labels_file in sorted(pathlib.Path(labels_dir).glob("*.jsonl")):
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
    # 4. sweep only after a fully successful run
    before = db.sql("SELECT count() AS n FROM bears_on WHERE seed = %s AND run != %s GROUP ALL" % (q(scope), q(token)),
                    expect=1)[0]
    stale = before[0]["n"] if before else 0
    if stale:
        db.sql("DELETE bears_on WHERE seed = %s AND run != %s" % (q(scope), q(token)), expect=1)
    counts["swept"] = stale
    return counts
