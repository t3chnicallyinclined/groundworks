# -*- coding: utf-8 -*-
"""lens.py -- lenses from config (docs/METHOD.md section 7): views in the datastore, framework filings.

From the instance's `lenses:` alone:
  lens_surql(inst)             one `DEFINE TABLE ... AS SELECT` view per perspective/discipline lens over
                               label joined to its unit, applying the lens's Standard (never looser than
                               the base gates; config.py refuses a looser one) and its filter; for a
                               framework lens, one `consideration` row per transcribed frame node and the
                               `falls_under` edge table (label -> consideration)
  apply(db, inst)              run it (a view is rebuilt from scratch: REMOVE then DEFINE)
  build_framework(db, inst)    file every label under the frame nodes its issues cross-walk to; stable
                               edge ids, a run token, a sweep only after every write succeeded
  matrix(db, inst)             per frame node: labels by direction, split usable vs unusable by the
                               unit's status against the lens's standard
  lens_view(db, inst, name)    rows of a lens view with the unit text under the untrusted key, plus a
                               drift count: rows whose unit no longer passes the standard

    python -m groundworks.lens INSTANCE.yaml --emit
    python -m groundworks.lens INSTANCE.yaml --apply --url http://127.0.0.1:8002/sql --ns NS --db DB

View caveat (measured on SurrealDB 3.1.6, research/DATABASE_FEATURES_2026-09-15.md): a view refreshes
only when its FROM table (label) changes. A unit whose status changes afterwards stays in the view
until apply() rebuilds it; lens_view reports such rows as `drift` instead of hiding them.
Standard library only.
"""
import argparse
import json
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks import config, gates  # noqa: E402
from groundworks.mcp.adapter import UNTRUSTED_TEXT_KEY  # noqa: E402
from groundworks.patterns import edge_ids  # noqa: E402
from groundworks.pipeline.loader import q, rid  # noqa: E402

SCOPE = "lens"          # seed of every falls_under edge this module writes
BATCH = 50
NO_DIRECTION = "none"   # bucket for a filed label with dir = NONE
VIEW_FIELDS = "id AS label, unit, unit.status AS status, unit.grade AS grade, tier, dir, issues, targets, role, chunk"


class LensError(Exception):
    pass


# ---- names and predicates ----------------------------------------------------------------------

def view_lenses(inst):
    """Lenses that become a view: every kind but framework."""
    return [l for l in inst.lenses if l.get("kind") != "framework"]


def frame_lenses(inst):
    """Lenses that file labels under a frame: every lens carrying one (a framework lens always does)."""
    return [l for l in inst.lenses if l.get("frame")]


def view_name(lens):
    return "lens_%s" % lens["name"]


def consideration_id(lens, node):
    return rid("consideration", "%s__%s" % (lens["name"], node))


def standard_where(inst, lens, prefix="unit."):
    """The Standard as a predicate over the unit's fields."""
    std = inst.lens_standard(lens)
    parts = ["%sstatus IN %s" % (prefix, gates._lit(std["status"]))]
    if std["grade"]:
        parts.append("%sgrade IN %s" % (prefix, gates._lit(std["grade"])))
    return " AND ".join(parts)


def passes_standard(inst, lens, status, grade):
    std = inst.lens_standard(lens)
    return status in std["status"] and (not std["grade"] or grade in std["grade"])


def view_where(inst, lens):
    where = standard_where(inst, lens)
    if lens.get("filter"):
        problem = config.check_lens_filter(lens["filter"])
        if problem:
            raise LensError("lens %s: %s" % (lens["name"], problem))
        where += " AND (%s)" % lens["filter"]
    return where


# ---- surql -------------------------------------------------------------------------------------

def view_surql(inst, lens):
    name = view_name(lens)
    return ("REMOVE TABLE IF EXISTS %s;\n"
            "DEFINE TABLE OVERWRITE %s AS SELECT %s FROM label WHERE %s;"
            % (name, name, VIEW_FIELDS, view_where(inst, lens)))


def framework_schema_surql():
    return "\n".join([
        "DEFINE TABLE OVERWRITE consideration SCHEMAFULL;",
        "DEFINE FIELD OVERWRITE lens ON consideration TYPE string;",
        "DEFINE FIELD OVERWRITE source ON consideration TYPE string;",
        "DEFINE FIELD OVERWRITE node ON consideration TYPE string;",
        "DEFINE FIELD OVERWRITE transcribed ON consideration TYPE string;",
        "DEFINE INDEX OVERWRITE consideration_lens ON consideration FIELDS lens;",
        "DEFINE TABLE OVERWRITE falls_under TYPE RELATION IN label OUT consideration SCHEMALESS;",
    ])


def frame_surql(inst, lens):
    frame = lens["frame"]
    lines = []
    for node in frame["nodes"]:
        lines.append("UPSERT %s SET lens=%s, source=%s, node=%s, transcribed=%s;"
                     % (consideration_id(lens, node), q(lens["name"]), q(frame["source"]), q(node), q(node)))
    lines.append("DELETE consideration WHERE lens = %s AND node NOT IN %s;" % (q(lens["name"]), q(list(frame["nodes"]))))
    return "\n".join(lines)


def lens_surql(inst):
    lines = ["-- generated by groundworks/lens.py from instance %s; do not edit by hand" % inst.id]
    for lens in view_lenses(inst):
        lines.append("-- lens %s (%s, %s-backed)" % (lens["name"], lens.get("kind"), lens.get("backing") or "prompt"))
        lines.append(view_surql(inst, lens))
    frames = frame_lenses(inst)
    if frames:
        lines.append("-- frame nodes and the falls_under edge table (label -> consideration)")
        lines.append(framework_schema_surql())
        for lens in frames:
            lines.append("-- frame of lens %s, transcribed from: %s" % (lens["name"], lens["frame"]["source"]))
            lines.append(frame_surql(inst, lens))
    return "\n".join(lines) + "\n"


def apply(db, inst):
    """Apply every view and frame. Returns the lens names applied."""
    db.script(lens_surql(inst))
    return [l["name"] for l in inst.lenses]


# ---- framework filings ---------------------------------------------------------------------------

def _run_batches(db, statements, counts, key):
    for start in range(0, len(statements), BATCH):
        batch = statements[start:start + BATCH]
        results = db.sql("; ".join(batch) + ";", expect=len(batch))
        if len(results) != len(batch):
            raise LensError("batch returned %d results for %d statements" % (len(results), len(batch)))
        counts[key] += len(batch)


def build_framework(db, inst, scope=SCOPE):
    """File every label under the frame nodes its issues cross-walk to (one edge per label and node).

    Edge ids are stable (patterns/edge_ids), every edge carries this run's token, and edges of an
    earlier run are swept only after every write of this run succeeded. Labels with an issue that
    no node claims are counted as `unfiled` and reported, never silently dropped.
    """
    token = edge_ids.run_token()
    counts = {"lenses": 0, "labels": 0, "with_issues": 0, "filed": 0, "unfiled": 0, "edges": 0, "swept": 0,
              "run": token, "unfiled_issues": {}}
    frames = frame_lenses(inst)
    if not frames:
        counts["sweep"] = "SKIPPED: no lens carries a frame"
        return counts
    keyer = edge_ids.Keyer()
    labels = db.sql("SELECT id, issues, tier, dir FROM label", expect=1)[0]
    counts["labels"] = len(labels)
    counts["with_issues"] = sum(1 for l in labels if l.get("issues"))
    failed = False
    try:
        for lens in frames:
            counts["lenses"] += 1
            by_issue = {}
            for node, issues in (lens.get("crosswalk") or {}).items():
                for issue in issues:
                    by_issue.setdefault(issue, []).append(node)
            statements = []
            for label in labels:
                issues = label.get("issues") or []
                if not issues:
                    continue
                filed = {}
                for issue in issues:
                    for node in by_issue.get(issue, []):
                        filed.setdefault(node, []).append(issue)
                    if issue not in by_issue:
                        counts["unfiled_issues"][issue] = counts["unfiled_issues"].get(issue, 0) + 1
                if not filed:
                    counts["unfiled"] += 1
                    continue
                counts["filed"] += 1
                lid = str(label["id"])
                for node in lens["frame"]["nodes"]:
                    if node not in filed:
                        continue
                    cid = consideration_id(lens, node)
                    eid = keyer(scope, "falls_under", lid, cid)
                    statements.append(
                        "RELATE %s->falls_under:%s->%s SET seed=%s, run=%s, lens=%s, node=%s, issues=%s, tier=%s, dir=%s"
                        % (lid, eid, cid, q(scope), q(token), q(lens["name"]), q(node), q(filed[node]),
                           q(label.get("tier")), q(label.get("dir"))))
            _run_batches(db, statements, counts, "edges")
    except Exception:
        failed = True
        raise
    finally:
        if failed:
            counts["sweep"] = "SKIPPED: a write failed, previous edges kept"
    before = db.sql("SELECT count() AS n FROM falls_under WHERE seed = %s AND run != %s GROUP ALL" % (q(scope), q(token)),
                    expect=1)[0]
    stale = before[0]["n"] if before else 0
    if stale:
        db.sql("DELETE falls_under WHERE seed = %s AND run != %s" % (q(scope), q(token)), expect=1)
    counts["swept"] = stale
    return counts


def matrix(db, inst, scope=SCOPE):
    """Per frame lens, per node: filed labels by direction, split usable vs unusable by the unit's
    status (and grade) against that lens's standard. Counts are items, not weight."""
    directions = list(inst.vocabulary.get("directions") or []) + [NO_DIRECTION]
    rows = db.sql("SELECT lens, node, in.dir AS dir, in.unit.status AS status, in.unit.grade AS grade "
                  "FROM falls_under WHERE seed = %s" % q(scope), expect=1)[0]
    out = {}
    for lens in frame_lenses(inst):
        nodes = {}
        for node in lens["frame"]["nodes"]:
            nodes[node] = {"usable": {d: 0 for d in directions}, "unusable": {d: 0 for d in directions}, "labels": 0}
        filed = 0
        for row in rows:
            if row.get("lens") != lens["name"] or row.get("node") not in nodes:
                continue
            side = "usable" if passes_standard(inst, lens, row.get("status"), row.get("grade")) else "unusable"
            direction = row.get("dir") if row.get("dir") in directions else NO_DIRECTION
            nodes[row["node"]][side][direction] += 1
            nodes[row["node"]]["labels"] += 1
            filed += 1
        out[lens["name"]] = {"source": lens["frame"]["source"], "standard": inst.lens_standard(lens),
                             "filed": filed, "nodes": nodes}
    return out


def matrix_lines(matrix_out):
    """One printable line per node: usable and unusable counts by direction."""
    lines = []
    for lens_name, entry in matrix_out.items():
        for node, cell in entry["nodes"].items():
            usable = ", ".join("%s %d" % kv for kv in cell["usable"].items() if kv[1])
            unusable = ", ".join("%s %d" % kv for kv in cell["unusable"].items() if kv[1])
            lines.append("%s/%s: %d label(s); usable: %s; unusable: %s"
                         % (lens_name, node, cell["labels"], usable or "-", unusable or "-"))
    return lines


# ---- reading through a lens ------------------------------------------------------------------------

def view_counts(db, inst):
    """Row count per view lens (the view as it stands, drift included)."""
    out = {}
    for lens in view_lenses(inst):
        rows = db.sql("SELECT count() AS n FROM %s GROUP ALL" % view_name(lens), expect=1)[0]
        out[lens["name"]] = rows[0]["n"] if rows else 0
    return out


def lens_view(db, inst, name, limit=50):
    """Rows of one lens view. The rows are exactly what the datastore view holds; `drift` counts rows
    whose unit no longer passes the lens's standard (rebuild with apply). For a framework lens: its matrix."""
    try:
        lens = inst.lens(name)
    except KeyError as exc:
        raise LensError(str(exc)) from exc
    limit = max(1, min(int(limit), 200))
    head = {"lens": lens["name"], "kind": lens.get("kind"), "backing": lens.get("backing") or "prompt",
            "charter": lens.get("charter"), "standard": inst.lens_standard(lens)}
    if lens.get("kind") == "framework":
        entry = matrix(db, inst)[lens["name"]]
        head.update({"source": entry["source"], "filed": entry["filed"], "nodes": entry["nodes"]})
        return head
    view = view_name(lens)
    # `WHERE true`: an unfiltered count() GROUP ALL reads 0 to a record credential (SurrealDB 3.1.6, measured 2026-09-16)
    total = db.sql("SELECT count() AS n FROM %s WHERE true GROUP ALL" % view, expect=1)[0]
    drift = db.sql("SELECT count() AS n FROM %s WHERE NOT (%s) GROUP ALL" % (view, standard_where(inst, lens)), expect=1)[0]
    rows = db.sql("SELECT label, unit, unit.text AS words, unit.locator AS locator, unit.status AS status, "
                  "unit.grade AS grade, tier, dir, issues, targets, role FROM %s ORDER BY tier DESC LIMIT %d"
                  % (view, limit), expect=1)[0]
    head.update({"filter": lens.get("filter"), "count": total[0]["n"] if total else 0, "limit": limit,
                 "drift": drift[0]["n"] if drift else 0,
                 "rows": [{"unit": str(r["unit"]), "label": str(r["label"]), "tier": r.get("tier"), "dir": r.get("dir"),
                           "issues": r.get("issues"), "targets": r.get("targets"), "role": r.get("role"),
                           "locator": r.get("locator"), "status": r.get("status"), "grade": r.get("grade"),
                           UNTRUSTED_TEXT_KEY: r.get("words")} for r in rows]})
    if head["drift"]:
        head["warning"] = ("%d row(s) no longer pass this lens's standard (a unit changed after the view was built): "
                           "rebuild the views with groundworks.lens --apply" % head["drift"])
    return head


def lens_list(inst):
    return [{"name": l["name"], "kind": l.get("kind"), "backing": l.get("backing") or "prompt"} for l in inst.lenses]


# ---- command line ---------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Lens views and framework filings from an instance config.")
    parser.add_argument("instance")
    parser.add_argument("--emit", action="store_true", help="print the generated SurrealQL")
    parser.add_argument("--apply", action="store_true", help="apply the views, build the framework, print the matrix")
    parser.add_argument("--url", default="http://127.0.0.1:8002/sql")
    parser.add_argument("--ns")
    parser.add_argument("--db")
    args = parser.parse_args(argv)
    inst = config.load(args.instance)
    if args.emit:
        print(lens_surql(inst)); return 0
    if args.apply:
        from groundworks import db as dbmod
        conn = dbmod.Db(url=args.url, ns=args.ns or inst.id, db=args.db or inst.id)
        conn.ping()
        apply(conn, inst)
        counts = build_framework(conn, inst)
        print("LENSES applied: %s" % ", ".join("%s=%d rows" % kv for kv in view_counts(conn, inst).items()))
        print("FRAMEWORK: %d label(s), %d with issues, %d filed, %d unfiled, %d edge(s), %d swept"
              % (counts["labels"], counts["with_issues"], counts["filed"], counts["unfiled"], counts["edges"], counts["swept"]))
        for line in matrix_lines(matrix(conn, inst)):
            print("   " + line)
        return 0
    parser.error("choose --emit or --apply")


if __name__ == "__main__":
    sys.exit(main())
