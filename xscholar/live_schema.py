# -*- coding: utf-8 -*-
"""live_schema.py -- does the live datastore still hold the definitions the config generates?

The gate baseline catches a rule leaving the CONFIG. Nothing caught a rule leaving the DATASTORE while the
config still has it: an event removed by hand, an ASSERT redefined looser, a PERMISSIONS clause widened, a
unique index removed, an access method redefined, a system user added. This reads every definition the live
database holds and compares it, by name, with what the config generates.

    check(inst, db, components=None, reference=None, ignore=()) -> Report
    python -m xscholar.live_schema INSTANCE.yaml --url URL --ns NS --db DB [--components a,b] [--reference-url URL]

Exit 0 clean, 1 drift, 2 could not run (NOT EXECUTED).

How the comparison is made (measured on SurrealDB 3.1.6, notes/2026-09-16_live_schema_and_promotion_audit.md):
the server does NOT hand back what it was given. `IN` comes back `INSIDE`, `option<string>` comes back
`none | string`, `CHANGEFEED 7d` comes back `1w`, `PERMISSIONS FULL` is added to every field, sub-fields
(`written_by.*`) appear that were never written, parentheses move, trailing semicolons go, and JWT keys are
`[REDACTED]`. A text comparison of the generated SurrealQL against `INFO FOR ...` would report every single
definition as altered, which trains people to ignore it. Writing a normaliser for that is a second parser that
drifts from the server's own. So the expected side is rendered BY THE SAME SERVER: the generated statements
are applied to an empty scratch database, its `INFO` is read back, and the scratch database is removed. Two
databases given the same statements render byte-identical `INFO` (measured, every table and every
definition), so exact comparison is sound, and any difference is a real one.

The scratch database lives in the live namespace (`zz_xscholar_reference_<random>`), is removed in a
`finally`, and its removal is checked. Pass `reference=` (a root Db on another server) to render elsewhere;
the two servers must then report the same version, because rendering may differ between versions.

Components are the generators this deployment applied, in the order they are applied. The default is what
`scripts/run_instance.py` applies: schema, gates, temporal, access (when the config has roles), the loader's
provenance fields, and the promotion log when it is on. `changefeed` and `lens` are applied by other tools;
name them when this deployment uses them, or their tables are reported as unexpected. That is on purpose:
a definition nobody expects is how a second login or an extra event arrives.

What this does not see, stated plainly: data. A credential row added to the agent table, a log entry edited,
a row promoted while a rule was briefly removed and then put back -- all of that leaves the definitions
exactly as expected. This check is a point in time; a rule removed and restored between two runs is not
seen. The JWT key of an access method is redacted by the server, so a replaced signing key is not seen.
Standard library only.
"""
import argparse
import hashlib
import re
import secrets
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config  # noqa: E402
from xscholar import db as dbmod  # noqa: E402

SCRATCH_PREFIX = "zz_xscholar_reference_"
# INFO FOR DB category -> the kind a report row names. A category this list does not know is still compared
# (under its own name): a future server version adding one must never make it invisible.
DB_KINDS = {"accesses": "access", "analyzers": "analyzer", "apis": "api", "buckets": "bucket",
            "configs": "config", "functions": "function", "models": "model", "modules": "module",
            "params": "param", "sequences": "sequence", "tables": "table", "users": "user"}
TABLE_KINDS = {"events": "event", "fields": "field", "indexes": "index", "tables": "view"}
RUNTIME_ONLY = {"lives"}      # live queries: sessions, not definitions
_PASSHASH = re.compile(r"PASSHASH '([^']*)'")


def scrub(text):
    """INFO FOR DB hands root every user's password hash (measured, 3.1.6). Nothing this module returns, prints
    or stores carries one: it is replaced by a digest of itself, so a changed password still shows as an
    altered definition, and the digest cannot be used to test guesses (the argon2 salt is not in it)."""
    return _PASSHASH.sub(lambda m: "PASSHASH '[sha256:%s]'" % hashlib.sha256(m.group(1).encode("utf-8")).hexdigest()[:16],
                         text)


class LiveSchemaError(Exception):
    """The check could not run. Never a drift: a drift is reported, never raised."""


# ---- what the config generates -------------------------------------------------------------------

def _schema(inst):
    from xscholar import gates
    return gates.schema_surql(inst)


def _gates(inst):
    from xscholar import gates
    return gates.gate_surql(inst)


def _temporal(inst):
    from xscholar import temporal
    return temporal.temporal_surql(inst)


def _access(inst):
    from xscholar import access
    return access.access_surql(inst)


def _provenance(inst):
    from xscholar.pipeline import loader
    return loader.provenance_surql()


def _changefeed(inst):
    from xscholar import queue
    return queue.changefeed_surql(inst)


def _lens(inst):
    from xscholar import lens
    return lens.lens_surql(inst)


def _assertions(inst):
    from xscholar import assertions
    return assertions.surql(inst)


def _originals(inst):
    from xscholar import originals
    return originals.enforcement_surql(inst)


# name -> generator, in the order a deployment applies them (later statements overwrite earlier ones, so the
# order is part of what is expected)
COMPONENTS = {"schema": _schema, "gates": _gates, "temporal": _temporal, "access": _access,
              "provenance": _provenance, "assertions": _assertions, "originals": _originals, "changefeed": _changefeed,
              "lens": _lens}


def default_components(inst):
    names = ["schema", "gates", "temporal"]
    if inst.access_roles():
        names.append("access")
    names.append("provenance")
    if inst.section("assertions"):
        names.append("assertions")
    return names


def deployed_components(inst):
    """What scripts/run_instance.py applies end to end: the defaults, plus originals enforcement (applied to every
    instance after its load) and the lens views when the instance declares lenses. Kept apart from
    default_components, which describes a deployment that stopped before the load."""
    names = default_components(inst) + ["originals"]
    if inst.lenses:
        names.append("lens")
    return names


def expected_script(inst, components):
    unknown = [c for c in components if c not in COMPONENTS]
    if unknown:
        raise LiveSchemaError("unknown component(s) %s; known: %s" % (", ".join(unknown), ", ".join(COMPONENTS)))
    order = [c for c in COMPONENTS if c in components]     # the applied order, whatever order they were named in
    parts = []
    for name in order:
        try:
            parts.append(COMPONENTS[name](inst))
        except Exception as exc:     # a generator that cannot run for this config: say which, never half-compare
            raise LiveSchemaError("component %s could not generate its definitions for instance %s: %s"
                                  % (name, inst.id, exc)) from exc
    return order, "\n".join(parts)


# ---- reading definitions -------------------------------------------------------------------------

def read_definitions(db):
    """{(kind, name): definition text} for everything the database defines, as the server renders it.

    A per-table name is `table.name`. An INFO result that is not a mapping raises: an empty read must
    never be mistaken for a database that defines nothing."""
    info = db.sql("INFO FOR DB;", expect=1)[0]
    if not isinstance(info, dict) or "tables" not in info:
        raise LiveSchemaError("INFO FOR DB on %s/%s did not return a definition map (got %r); is this a root "
                              "connection?" % (db.ns, db.db, type(info).__name__))
    out = {}
    for category, items in info.items():
        if not isinstance(items, dict):
            raise LiveSchemaError("INFO FOR DB category %r is not a mapping: %r" % (category, items))
        kind = DB_KINDS.get(category, category)
        for name, text in items.items():
            out[(kind, str(name))] = scrub(str(text))
    for table in sorted(info["tables"]):
        tinfo = db.sql("INFO FOR TABLE %s;" % _ident(table), expect=1)[0]
        if not isinstance(tinfo, dict):
            raise LiveSchemaError("INFO FOR TABLE %s did not return a definition map (got %r)" % (table, tinfo))
        for category, items in tinfo.items():
            if category in RUNTIME_ONLY:
                continue
            if not isinstance(items, dict):
                raise LiveSchemaError("INFO FOR TABLE %s category %r is not a mapping" % (table, category))
            kind = TABLE_KINDS.get(category, category)
            for name, text in items.items():
                out[(kind, "%s.%s" % (table, name))] = str(text)
    return out


def _ident(name):
    name = str(name)
    return name if name.replace("_", "").isalnum() else "`%s`" % name.replace("`", "\\`")


def render_expected(inst, components, server, reference=None):
    """The definitions the config generates, rendered by a SurrealDB server into a scratch database."""
    order, script = expected_script(inst, components)
    host = reference or server
    if reference is not None:
        live_version, ref_version = server.version(), reference.version()
        if live_version != ref_version:
            raise LiveSchemaError("the reference server is %s and the live server is %s; the server rewrites "
                                  "definitions when it stores them, and two versions may rewrite them differently, "
                                  "so a comparison across them could report drift that is not there or miss drift "
                                  "that is" % (ref_version, live_version))
    scratch = SCRATCH_PREFIX + secrets.token_hex(6)
    if scratch == host.db or scratch == server.db:
        raise LiveSchemaError("refusing: the scratch database name equals a real one")
    conn = dbmod.Db(url=host.url, ns=host.ns, db=scratch, user=host.user, password=host._password,
                    timeout=host.timeout)
    try:
        try:
            conn.script(script)
        except dbmod.DbError as exc:
            raise LiveSchemaError("could not render the expected definitions into scratch database %s/%s (this "
                                  "needs a namespace-level or root login): %s" % (host.ns, scratch, exc)) from exc
        return order, read_definitions(conn)
    finally:
        _remove_scratch(host, scratch)


def _remove_scratch(host, scratch):
    if not scratch.startswith(SCRATCH_PREFIX):
        raise LiveSchemaError("refusing to remove %r: not a scratch database" % scratch)
    admin = dbmod.Db(url=host.url, ns=host.ns, db=host.db, user=host.user, password=host._password,
                     timeout=host.timeout)
    admin.try_sql("REMOVE DATABASE IF EXISTS %s;" % scratch)
    left = (admin.sql("INFO FOR NS;", expect=1)[0] or {}).get("databases") or {}
    if scratch in left:
        raise LiveSchemaError("scratch database %s/%s was not removed; remove it by hand" % (host.ns, scratch))


# ---- comparing ----------------------------------------------------------------------------------

def compare(expected, live, ignore=()):
    """Rows (kind, name, status, expected, live), status one of missing / altered / unexpected.

    `ignore` names definitions (`kind:name`) whose PRESENCE live is not a finding. It never hides a
    missing or an altered definition: nothing the config generates can be waved through."""
    ignore = set(ignore or ())
    rows, ignored = [], []
    for key in sorted(set(expected) | set(live)):
        kind, name = key
        if key not in live:
            rows.append({"kind": kind, "name": name, "status": "missing", "expected": expected[key], "live": None})
        elif key not in expected:
            if "%s:%s" % key in ignore:
                ignored.append("%s:%s" % key)
                continue
            rows.append({"kind": kind, "name": name, "status": "unexpected", "expected": None, "live": live[key]})
        elif expected[key] != live[key]:
            rows.append({"kind": kind, "name": name, "status": "altered", "expected": expected[key],
                         "live": live[key]})
    return rows, ignored


class Report:
    def __init__(self, instance, ns, db, components):
        self.instance, self.ns, self.db, self.components = instance, ns, db, list(components)
        self.rows, self.ignored, self.system_users = [], [], {}
        self.expected_count = self.live_count = 0

    @property
    def ok(self):
        return not self.rows

    def counts(self):
        out = {"expected": self.expected_count, "live": self.live_count, "missing": 0, "altered": 0,
               "unexpected": 0, "ignored": len(self.ignored)}
        for row in self.rows:
            out[row["status"]] += 1
        return out

    def lines(self):
        c = self.counts()
        out = ["LIVE SCHEMA %s/%s against instance %s (components: %s): %d expected, %d live; %d missing, "
               "%d altered, %d unexpected, %d ignored" % (self.ns, self.db, self.instance, ", ".join(self.components),
                                                         c["expected"], c["live"], c["missing"], c["altered"],
                                                         c["unexpected"], c["ignored"])]
        for row in self.rows:
            out.append("  %s %s %s" % (row["status"].upper(), row["kind"], row["name"]))
            if row["expected"] is not None:
                out.append("      expected: %s" % row["expected"])
            if row["live"] is not None:
                out.append("      live:     %s" % scrub(row["live"]))
        for level, users in sorted(self.system_users.items()):
            out.append("  NOTE %s-level system users (bypass every PERMISSIONS clause and can redefine any rule): %s"
                       % (level, (", ".join(users) or "none") if isinstance(users, list) else users))
        return out


def check(inst, db, components=None, reference=None, ignore=()):
    """Compare the live database with what the config generates. Raises LiveSchemaError if it cannot run."""
    components = list(components) if components else default_components(inst)
    if not components:
        raise LiveSchemaError("no components: nothing to compare, and a comparison of nothing would read clean")
    if getattr(db, "access", None):
        raise LiveSchemaError("the connection is an agent credential; reading every definition needs root")
    order, expected = render_expected(inst, components, db, reference)
    live = read_definitions(db)
    if not expected:
        raise LiveSchemaError("the config generated no definitions for %s; refusing to call that clean" % order)
    report = Report(inst.id, db.ns, db.db, order)
    report.expected_count, report.live_count = len(expected), len(live)
    report.rows, report.ignored = compare(expected, live, ignore)
    report.system_users = _system_users(db)
    return report


def _system_users(db):
    """Namespace- and root-level users, reported and never judged: the engine cannot know which are yours.
    Database-level users are definitions and are already compared (the config generates none)."""
    out = {}
    for level, statement in (("namespace", "INFO FOR NS;"), ("root", "INFO FOR ROOT;")):
        try:
            users = (db.sql(statement, expect=1)[0] or {}).get("users") or {}
            out[level] = sorted(users)
        except dbmod.DbError as exc:
            out[level] = "could not read (%s)" % str(exc)[:120]
    return out


# ---- command line --------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare a live datastore's definitions with the instance config.")
    parser.add_argument("instance")
    parser.add_argument("--url", default="http://127.0.0.1:8002/sql")
    parser.add_argument("--ns")
    parser.add_argument("--db")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="root")
    parser.add_argument("--components", help="comma-separated; default: %s" % "schema,gates,temporal,access,provenance")
    parser.add_argument("--reference-url", help="render the expected definitions on this server instead")
    parser.add_argument("--ignore", action="append", default=[], help="kind:name present live and not a finding")
    args = parser.parse_args(argv)
    inst = config.load(args.instance)
    live = dbmod.Db(url=args.url, ns=args.ns or inst.id, db=args.db or inst.id, user=args.user, password=args.password)
    reference = (dbmod.Db(url=args.reference_url, ns=live.ns, db=live.db, user=args.user, password=args.password)
                 if args.reference_url else None)
    try:
        live.ping()
        report = check(inst, live, components=args.components.split(",") if args.components else None,
                       reference=reference, ignore=args.ignore)
    except (LiveSchemaError, dbmod.DbError) as exc:
        print("NOT EXECUTED: %s" % exc)
        return 2
    for line in report.lines():
        print(line)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
