# -*- coding: utf-8 -*-
"""temporal.py -- bitemporal claims: when a claim was true in the source, and when the graph knew it.

Two time axes on every claim (roadmap B12, v0.2 item V2-2):
  valid time    `valid_from` / `valid_to` on fact: when the claim held in the source (supplied by the writer)
  transaction   `learned_at` (the moment the row was recorded) plus an append-only history the datastore
                writes itself: `status_history` on fact, `ready_history` on conclusion. An event appends
                {status_before, status_after, at, by} only when the value actually changed, so a loader
                re-write with the same status appends nothing.

  temporal_surql(inst)          statements applied AFTER schema and gates (gates.py is never edited)
  as_of(db, table, ident, when) the status a row held at `when`, rebuilt from its history
  snapshot(db, when)            counts by status as of `when`: "what did the graph say on date X"
  run_probes(inst, db)          checks both ways on a throwaway row set; nothing left behind

    python -m xscholar.temporal INSTANCE.yaml --emit
    python -m xscholar.temporal INSTANCE.yaml --apply --url http://127.0.0.1:8031/sql --ns demo --db incident

Proven on SurrealDB 3.1.6 (notes/2026-09-15_automatic_demotion.md holds the traps that shaped it):
  * a SCHEMAFULL `array<object>` field drops or refuses nested keys unless each key is a defined subfield,
    so every history key is declared (`status_history.*.at` and so on);
  * an event with no change guard re-fires on its own append and recurses until the whole write is
    rejected; the guard `$before.status != $after.status` is what stops both recursion and no-op entries;
  * rows that existed before these fields were defined hold NONE and every later write on them fails
    coercion, so the script backfills `[]` and `learned_at` once;
  * `$before` inside a field ASSERT is the field's previous value, which is what makes the history
    append-only (the new array must start with the old one).
Standard library only.
"""
import argparse
import datetime as _dt
import json
import re
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config  # noqa: E402
from xscholar.pipeline.loader import rid  # noqa: E402

# The tracked value per table: which field, which history array, and the key names in an entry.
TRACKED = {
    "fact": {"field": "status", "history": "status_history", "before": "status_before", "after": "status_after",
             "type": "option<string>", "event": "fact_status_history"},
    "conclusion": {"field": "ready", "history": "ready_history", "before": "ready_before", "after": "ready_after",
                   "type": "option<bool>", "event": "conclusion_ready_history"},
    # A verdict can be downgraded now that re-stating a head replaces it (write.py critique()). That
    # must never be a silent edit: a `fatal` head blocks readiness outright, so who softened it, when,
    # and from what, belong on the record exactly as a fact's status changes do.
    "critique": {"field": "verdict", "history": "verdict_history", "before": "verdict_before",
                 "after": "verdict_after", "type": "option<string>", "event": "critique_verdict_history"},
}

# append-only: the new array must be at least as long as the old one and start with it
_APPEND_ONLY = ("array::len($value) >= array::len($before ?? []) "
                "AND array::slice($value, 0, array::len($before ?? [])) = ($before ?? [])")


class TemporalError(Exception):
    pass


# ---- surql -----------------------------------------------------------------------------------------

def _history_block(table, spec, noop_guard=True):
    field, hist = spec["field"], spec["history"]
    guard = "$before.%s != $after.%s" % (field, field) if noop_guard else "$before.%s = $after.%s" % (hist, hist)
    return [
        "DEFINE FIELD OVERWRITE learned_at ON %s TYPE datetime DEFAULT time::now();" % table,
        "DEFINE FIELD OVERWRITE %s ON %s TYPE array<object> DEFAULT [] ASSERT %s;" % (hist, table, _APPEND_ONLY),
        "DEFINE FIELD OVERWRITE %s.*.%s ON %s TYPE %s;" % (hist, spec["before"], table, spec["type"]),
        "DEFINE FIELD OVERWRITE %s.*.%s ON %s TYPE %s;" % (hist, spec["after"], table, spec["type"]),
        "DEFINE FIELD OVERWRITE %s.*.at ON %s TYPE datetime;" % (hist, table),
        "DEFINE FIELD OVERWRITE %s.*.by ON %s TYPE option<string>;" % (hist, table),
        "DEFINE EVENT OVERWRITE %s ON TABLE %s" % (spec["event"], table),
        "  WHEN $event = 'UPDATE' AND %s" % guard,
        "  THEN {",
        "    UPDATE $after.id SET %s += {%s: $before.%s, %s: $after.%s, at: time::now(), by: $after.author};"
        % (hist, spec["before"], field, spec["after"], field),
        "  };",
        "-- rows that existed before these fields were defined hold NONE and any later write on them would fail",
        "-- coercion; both fields are backfilled in ONE statement (setting one alone trips coercion on the other)",
        "UPDATE %s SET %s = IF %s = NONE { [] } ELSE { %s }, "
        "learned_at = IF learned_at = NONE { time::now() } ELSE { learned_at } "
        "WHERE %s = NONE OR learned_at = NONE;" % (table, hist, hist, hist, hist),
        "",
    ]


def temporal_surql(inst, noop_guard=True):
    """Statements to apply after gates.schema_surql and gates.gate_surql.

    `noop_guard=False` exists only so a test can prove the guard is load-bearing: the event then fires on
    every change to the row (still not on its own append), and a same-status re-write appends an entry.
    Never apply that variant to a real instance."""
    lines = ["-- generated by xscholar/temporal.py from instance %s; do not edit by hand" % inst.id,
             "-- valid time: when the claim held in the source (writer-supplied)",
             "DEFINE FIELD OVERWRITE valid_from ON fact TYPE option<datetime>;",
             "DEFINE FIELD OVERWRITE valid_to ON fact TYPE option<datetime>;",
             "-- transaction time: when the graph recorded it, and every change of the gated value since",
             ""]
    if not noop_guard:
        lines.insert(1, "-- WARNING: no-op guard removed (test variant); same-status re-writes append entries")
    for table, spec in TRACKED.items():
        lines.extend(_history_block(table, spec, noop_guard))
    return "\n".join(lines) + "\n"


# ---- datetimes -------------------------------------------------------------------------------------

_FRACTION = re.compile(r"(\.\d{6})\d+")


def to_datetime(value):
    """A timezone-aware UTC datetime from a datetime, or from SurrealDB's ISO text (nanoseconds allowed)."""
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    if not isinstance(value, str):
        raise TemporalError("not a datetime: %r" % (value,))
    text = _FRACTION.sub(r"\1", value.strip())
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise TemporalError("cannot parse datetime %r" % value) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)


def iso(value):
    return to_datetime(value).astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def literal(value):
    """A SurrealQL datetime literal for a SET clause."""
    return '<datetime> "%s"' % iso(value)


def valid_clause(valid_from=None, valid_to=None):
    """The `, valid_from = ..., valid_to = ...` tail for a SET; empty when neither is given."""
    parts = []
    if valid_from is not None:
        parts.append("valid_from = %s" % literal(valid_from))
    if valid_to is not None:
        parts.append("valid_to = %s" % literal(valid_to))
    return "".join(", " + p for p in parts)


# ---- reconstruction --------------------------------------------------------------------------------

def _value_at(spec, current, history, when):
    """(value held at `when`, the history entry that set it or None for the value held since learned_at)."""
    entries = sorted(history or [], key=lambda e: to_datetime(e["at"]))
    value = entries[0][spec["before"]] if entries else current
    determined = None
    for entry in entries:
        if to_datetime(entry["at"]) <= when:
            value, determined = entry[spec["after"]], entry
        else:
            break
    return value, determined


def _spec(table):
    if table not in TRACKED:
        raise TemporalError("no history is kept on %s; tracked: %s" % (table, ", ".join(TRACKED)))
    return TRACKED[table]


def _record(table, ident):
    ident = str(ident)
    if ident.startswith(table + ":"):
        ident = ident.split(":", 1)[1]
    return rid(table, ident.strip("⟨⟩"))


def as_of(db, table, ident, when):
    """The value of the gated field that `table:ident` held at `when`, with the entry that determined it."""
    spec = _spec(table)
    when = to_datetime(when)
    extra = ", valid_from, valid_to" if table == "fact" else ""
    rows = db.sql("SELECT %s AS value, learned_at, %s AS history%s FROM %s"
                  % (spec["field"], spec["history"], extra, _record(table, ident)), expect=1)[0]
    out = {"table": table, "id": _record(table, ident), "field": spec["field"], "when": iso(when)}
    if not rows:
        out.update({"known": False, "reason": "no such row"})
        return out
    row = rows[0]
    learned = to_datetime(row["learned_at"])
    out["learned_at"] = iso(learned)
    if when < learned:
        out.update({"known": False, "reason": "not yet learned at that time"})
        return out
    history = row.get("history") or []
    value, entry = _value_at(spec, row["value"], history, when)
    last = history[-1][spec["after"]] if history else row["value"]
    out.update({"known": True, "value": value, "current": row["value"], "history_length": len(history),
                "determined_by": entry or {"reason": "value held since learned_at", "at": iso(learned)},
                "consistent": last == row["value"]})
    if table == "fact":
        vf, vt = row.get("valid_from"), row.get("valid_to")
        out["valid_from"] = iso(vf) if vf else None
        out["valid_to"] = iso(vt) if vt else None
        out["valid_then"] = (None if vf is None and vt is None
                             else (vf is None or to_datetime(vf) <= when) and (vt is None or when < to_datetime(vt)))
    return out


def snapshot(db, when):
    """Counts by status as the graph held them at `when`, for facts and for conclusion readiness."""
    when = to_datetime(when)
    out = {"when": iso(when)}
    for table, spec in TRACKED.items():
        rows = db.sql("SELECT %s AS value, learned_at, %s AS history FROM %s"
                      % (spec["field"], spec["history"], table), expect=1)[0]
        counts, unknown = {}, 0
        for row in rows:
            if when < to_datetime(row["learned_at"]):
                unknown += 1
                continue
            value, _ = _value_at(spec, row["value"], row.get("history") or [], when)
            key = value if isinstance(value, str) else ("ready" if value else "not_ready")
            counts[key] = counts.get(key, 0) + 1
        out[table] = {"by_" + spec["field"]: dict(sorted(counts.items())), "known": len(rows) - unknown,
                      "not_yet_known": unknown, "total": len(rows)}
    return out


def history_count(db, table="fact"):
    """How many rows carry at least one history entry (the health line's `temporal` number)."""
    spec = _spec(table)
    rows = db.sql("SELECT count() AS n FROM %s WHERE array::len(%s ?? []) > 0 GROUP ALL"
                  % (table, spec["history"]), expect=1)[0]
    return rows[0]["n"] if rows else 0


# ---- probes ----------------------------------------------------------------------------------------

PROBE = "zz_temporal_probe"


def run_probes(inst, db):
    """Both ways on throwaway rows: entries appear on change, not on re-writes; history cannot shrink;
    as_of and snapshot rebuild the past. Cleans up even on failure."""
    from xscholar import gates
    report = {"instance": inst.id, "checks": [], "passed": 0, "failed": 0}

    def check(name, ok, detail=""):
        report["checks"].append({"check": name, "pass": bool(ok), "detail": detail})
        report["passed" if ok else "failed"] += 1

    def now():
        return to_datetime(db.sql("RETURN time::now()", expect=1)[0])

    def history(table, ident):
        return db.sql("SELECT VALUE %s FROM %s" % (TRACKED[table]["history"], rid(table, ident)), expect=1)[0][0]

    fact, conc = rid("fact", PROBE), rid("conclusion", PROBE)
    ready_probe = next((p for p in gates.probes(inst)
                        if p["rule"] in [r["id"] for r in inst.gate_rules()
                                         if r["table"] == "conclusion" and r["field"] == "ready" and r["value"] is True]),
                       None)
    try:
        t0 = now()
        db.sql("CREATE %s SET statement='temporal probe', status='proposed', author='temporal_probe';" % fact)
        check("create writes no history entry", history("fact", PROBE) == [])
        t1 = now()
        db.sql("UPDATE %s SET status='inferred';" % fact)
        h = history("fact", PROBE)
        check("status change appends one entry", len(h) == 1 and h[0]["status_before"] == "proposed"
              and h[0]["status_after"] == "inferred" and h[0]["by"] == "temporal_probe", h)
        db.sql("UPDATE %s SET status='inferred', note='loader re-write';" % fact)
        check("same-status re-write appends nothing", len(history("fact", PROBE)) == 1)
        ok, msg = db.try_sql("UPDATE %s SET status_history = [];" % fact)
        check("history cannot be emptied", not ok and "status_history" in msg, msg[:160])
        ok, msg = db.try_sql("UPDATE %s SET status_history[0].status_after = 'x';" % fact)
        check("history entry cannot be rewritten", not ok, msg[:160])
        check("as_of before learned_at is not known", as_of(db, "fact", PROBE, t0)["known"] is False)
        check("as_of between create and change says proposed", as_of(db, "fact", PROBE, t1)["value"] == "proposed")
        check("as_of now says inferred", as_of(db, "fact", PROBE, now())["value"] == "inferred")
        s0, s1 = snapshot(db, t0), snapshot(db, now())
        check("snapshot differs between instants", s0["fact"] != s1["fact"], {"before": s0["fact"], "after": s1["fact"]})
        # conclusion: the no-op direction is always checkable; the flip uses the gate's own accept recipe
        db.sql("CREATE %s SET statement='temporal probe', ready=false, author='temporal_probe';" % conc)
        db.sql("UPDATE %s SET ready=false, rating='re-write';" % conc)
        check("conclusion same-ready re-write appends nothing", history("conclusion", PROBE) == [])
        if ready_probe:
            for stmt in ready_probe["setup"] + ready_probe["fix"]:
                db.sql(stmt)
            db.sql(ready_probe["attempt"])
            row = ready_probe["attempt"].split()[1]
            h = db.sql("SELECT VALUE ready_history FROM %s" % row, expect=1)[0][0]
            check("conclusion ready flip appends one entry (gate satisfied via its probe recipe)",
                  len(h) == 1 and h[0]["ready_before"] is False and h[0]["ready_after"] is True, h)
        else:
            db.sql("UPDATE %s SET ready=true;" % conc)
            h = history("conclusion", PROBE)
            check("conclusion ready flip appends one entry", len(h) == 1 and h[0]["ready_after"] is True, h)
    finally:
        if ready_probe:
            for stmt in ready_probe["cleanup"]:
                db.try_sql(stmt)
        for table in ("critique", "conclusion", "fact", "unit"):
            db.try_sql("DELETE %s WHERE record::id(id) CONTAINS '%s';" % (table, PROBE))
    return report


# ---- command line ----------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Bitemporal columns, history events and probes for an instance.")
    parser.add_argument("instance")
    parser.add_argument("--emit", action="store_true", help="print the surql")
    parser.add_argument("--apply", action="store_true", help="apply on top of schema and gates, then probe")
    parser.add_argument("--url", default="http://127.0.0.1:8002/sql")
    parser.add_argument("--ns")
    parser.add_argument("--db")
    args = parser.parse_args(argv)
    inst = config.load(args.instance)
    if args.emit:
        print(temporal_surql(inst))
        return 0
    if args.apply:
        from xscholar import db as dbmod
        conn = dbmod.Db(url=args.url, ns=args.ns or inst.id, db=args.db or inst.id)
        conn.ping()
        conn.script(temporal_surql(inst))
        report = run_probes(inst, conn)
        print(json.dumps(report, indent=2, default=str))
        print("TEMPORAL PROBES: %d passed, %d failed" % (report["passed"], report["failed"]))
        return 1 if report["failed"] else 0
    parser.error("choose --emit or --apply")


if __name__ == "__main__":
    sys.exit(main())
