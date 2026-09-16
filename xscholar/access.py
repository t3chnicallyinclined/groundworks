# -*- coding: utf-8 -*-
"""access.py -- per-agent datastore credentials: the datastore, not the server, decides who writes what.

From the instance config's `access:` section:
  access_surql(inst)     an `agent` table, one DEFINE ACCESS ... TYPE RECORD, and a PERMISSIONS clause on
                         EVERY table the schema generator emits (nodes and edges)
  provision(...)         create one agent record; return the launch environment for that agent
  revoke(...)            delete the agent record; its token then reads and writes nothing
  connect(creds)         a Db that signs in with a provisioned credential

    python -m xscholar.access INSTANCE.yaml --emit
    python -m xscholar.access INSTANCE.yaml --apply --url http://127.0.0.1:8002/sql --ns NS --db DB
    python -m xscholar.access INSTANCE.yaml --provision AGENT_ID ROLE [--lens L] --url ... --ns ... --db ...
    python -m xscholar.access INSTANCE.yaml --revoke AGENT_ID --url ... --ns ... --db ...

Mechanism (measured on SurrealDB 3.1.6, 2026-09-15): a DEFINE USER ... ON DATABASE is a system user
and bypasses table PERMISSIONS, so it cannot carry row rules. A record access does: the agent signs in
through `DEFINE ACCESS agent ON DATABASE TYPE RECORD SIGNIN (...)`, `$auth` is its `agent` row, and every
PERMISSIONS clause reads `$auth.role` and `$auth.lens`. Root still bypasses everything: schema, gates,
loads, the archive and this module's own provisioning run as root.

Rules the generated permissions hold, whatever the config says:
  * a role SELECTs only the tables in `read` (an edge is readable when both its ends are);
  * a role CREATEs/UPDATEs (and so RELATEs) only the tables in `write`;
  * a `lens_bound` role writes lens-carrying rows only where `lens = $auth.lens`;
  * only a role named `loader` may write `label` rows (config.py refuses anything else);
  * DELETE is NONE for every role: deletes go through the archive, by a human, as root;
  * the gates (ASSERTs from gates.py) stay in force for every credential: permissions never loosen a gate.

Apply order: schema, gates, then access. Re-running the schema after access resets no permissions
(gates.py uses DEFINE FIELD, and ALTER TABLE ... PERMISSIONS is separate), but re-run access anyway
after any schema change so a new table is never left with default permissions.
Standard library only.
"""
import argparse
import json
import re
import secrets
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config, gates  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar.pipeline.loader import rid, q  # noqa: E402

NODE_TABLES = config.BUILT_IN_NODE_TABLES
TABLES = NODE_TABLES + tuple(gates.EDGES)   # the ENGINE's tables; an instance may declare more
LENS_TABLES = ("critique", "fact")          # built-in tables whose rows carry a lens field
ACCESS_NAME = "agent"                       # the DEFINE ACCESS name; XSCHOLAR_DB_ACCESS at launch
AGENT_TABLE = "agent"
TOKEN_DURATION = "8h"
AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")   # same shape server.py accepts
LENS_ID = re.compile(r"^[A-Za-z0-9_]{1,80}$")


def tables(inst):
    """Every table this instance has, in the order the schema generator emits them. A declared table
    left out here would keep SurrealDB's default permissions, which is every role reading it."""
    return tuple(inst.tables())


def lens_tables(inst):
    """Tables whose rows carry a lens, so a lens_bound role may only write its own. A declared table
    that declares a `lens` field is one of them: the rule follows the field, not a hard-coded list."""
    return tuple(LENS_TABLES) + tuple(name for name, fields in inst.declared_tables().items()
                                      if "lens" in fields)


class AccessError(Exception):
    pass


def _roles(inst):
    roles = inst.access_roles()
    if not roles:
        raise AccessError("instance %s has no access: section; nothing to generate" % inst.id)
    return roles


def _can_read(role, table, edges):
    if table in role["read"]:
        return True
    if table in edges:
        src, dst = edges[table]
        return src in role["read"] and dst in role["read"]
    return False


def _clause(names, lens_bound_names, table, lens_carrying=LENS_TABLES):
    """One boolean over $auth for a set of roles, or NONE when no role qualifies."""
    parts = []
    plain = [n for n in names if n not in lens_bound_names or table not in lens_carrying]
    if plain:
        parts.append("$auth.role IN %s" % gates._lit(plain))
    for name in names:
        if name in lens_bound_names and table in lens_carrying:
            parts.append("($auth.role = %s AND lens = $auth.lens)" % gates._lit([name])[1:-1])
    if not parts:
        return "NONE"
    return "WHERE " + " OR ".join(parts)


def access_surql(inst):
    roles = _roles(inst)
    lens_bound = {n for n, r in roles.items() if r["lens_bound"]}
    lines = [
        "-- generated by xscholar/access.py from instance %s; do not edit by hand" % inst.id,
        "DEFINE TABLE OVERWRITE %s SCHEMAFULL PERMISSIONS NONE;" % AGENT_TABLE,
        "DEFINE FIELD OVERWRITE role ON %s TYPE string ASSERT $value IN %s;" % (AGENT_TABLE, gates._lit(sorted(roles))),
        "DEFINE FIELD OVERWRITE lens ON %s TYPE option<string>;" % AGENT_TABLE,
        "DEFINE FIELD OVERWRITE pass ON %s TYPE string;" % AGENT_TABLE,
        "DEFINE FIELD OVERWRITE enabled ON %s TYPE bool DEFAULT true;" % AGENT_TABLE,
        "DEFINE FIELD OVERWRITE created ON %s TYPE datetime DEFAULT time::now();" % AGENT_TABLE,
        "DEFINE ACCESS OVERWRITE %s ON DATABASE TYPE RECORD SIGNIN ("
        " SELECT * FROM %s WHERE id = type::record('%s', $id) AND enabled = true AND crypto::argon2::compare(pass, $pass)"
        " ) DURATION FOR TOKEN %s, FOR SESSION %s;" % (ACCESS_NAME, AGENT_TABLE, AGENT_TABLE, TOKEN_DURATION, TOKEN_DURATION),
        "",
    ]
    edges, lens_carrying = inst.edge_map(), lens_tables(inst)
    for table in tables(inst):
        readers = [n for n, r in roles.items() if _can_read(r, table, edges)]
        writers = [n for n, r in roles.items() if table in r["write"]]
        select = "WHERE $auth.role IN %s" % gates._lit(readers) if readers else "NONE"
        write = _clause(writers, lens_bound, table, lens_carrying)
        lines.append("ALTER TABLE %s PERMISSIONS FOR select %s FOR create %s FOR update %s FOR delete NONE;"
                     % (table, select, write, write))
    return "\n".join(lines) + "\n"


# ---- credentials ---------------------------------------------------------------------------------

def provision(db_root, inst, agent_id, role, lens=None, password=None):
    """Create (or reset) one agent record as root. Returns the launch environment for that agent."""
    roles = _roles(inst)
    if not AGENT_ID.match(str(agent_id or "")):
        raise AccessError("agent id %r is not a plain identifier (letters, digits, _ . : -)" % agent_id)
    if role not in roles:
        raise AccessError("role %r is not in the instance's access roles: %s" % (role, ", ".join(sorted(roles))))
    if roles[role]["lens_bound"] and not lens:
        raise AccessError("role %s is lens_bound: a lens is required" % role)
    if lens and not LENS_ID.match(lens):
        raise AccessError("lens %r is not a plain identifier" % lens)
    password = password or secrets.token_urlsafe(24)
    rows = db_root.sql("UPSERT %s SET role=%s, lens=%s, pass=crypto::argon2::generate(%s), enabled=true"
                       % (rid(AGENT_TABLE, agent_id), q(role), q(lens), q(password)), expect=1)[0]
    if not rows:
        raise AccessError("provisioning %s wrote nothing (is this connection root, and is access applied?)" % agent_id)
    creds = {"XSCHOLAR_AGENT_ID": agent_id, "XSCHOLAR_ROLE": role,
             "XSCHOLAR_DB_URL": db_root.url, "XSCHOLAR_NS": db_root.ns, "XSCHOLAR_DB": db_root.db,
             "XSCHOLAR_DB_ACCESS": ACCESS_NAME, "XSCHOLAR_DB_USER": agent_id, "XSCHOLAR_DB_PASS": password}
    if lens:
        creds["XSCHOLAR_LENS"] = lens
    return creds


def revoke(db_root, agent_id):
    """Delete the agent record. An unknown agent is an error, never a silent pass."""
    target = rid(AGENT_TABLE, agent_id)
    if not db_root.sql("SELECT VALUE id FROM %s" % target, expect=1)[0]:
        raise AccessError("no agent %r to revoke" % agent_id)
    db_root.sql("DELETE %s" % target, expect=1)
    if db_root.sql("SELECT VALUE id FROM %s" % target, expect=1)[0]:
        raise AccessError("agent %r still exists after DELETE" % agent_id)
    return agent_id


def connect(creds, timeout=30):
    """A Db signed in as a provisioned agent (from provision() or the same keys in os.environ)."""
    return dbmod.Db(url=creds["XSCHOLAR_DB_URL"], ns=creds["XSCHOLAR_NS"], db=creds["XSCHOLAR_DB"],
                    user=creds["XSCHOLAR_DB_USER"], password=creds["XSCHOLAR_DB_PASS"],
                    access=creds.get("XSCHOLAR_DB_ACCESS") or ACCESS_NAME, timeout=timeout)


# ---- command line --------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Per-agent datastore credentials from an instance config.")
    parser.add_argument("instance")
    parser.add_argument("--emit", action="store_true", help="print the generated SurrealQL")
    parser.add_argument("--apply", action="store_true", help="apply it as root")
    parser.add_argument("--provision", nargs=2, metavar=("AGENT_ID", "ROLE"), help="create a credential; prints env lines")
    parser.add_argument("--lens")
    parser.add_argument("--password", help="use this password instead of a generated one")
    parser.add_argument("--revoke", metavar="AGENT_ID")
    parser.add_argument("--url", default="http://127.0.0.1:8002/sql")
    parser.add_argument("--ns")
    parser.add_argument("--db")
    parser.add_argument("--root-user", default="root")
    parser.add_argument("--root-pass", default="root")
    args = parser.parse_args(argv)
    inst = config.load(args.instance)
    if args.emit:
        print(access_surql(inst))
        return 0
    if not (args.apply or args.provision or args.revoke):
        parser.error("choose --emit, --apply, --provision or --revoke")
    root = dbmod.Db(url=args.url, ns=args.ns or inst.id, db=args.db or inst.id, user=args.root_user, password=args.root_pass)
    root.ping()
    if args.apply:
        report = root.script(access_surql(inst))
        print("ACCESS applied: %d statement(s), %d role(s), %d table(s)" % (len(report), len(inst.access_roles()), len(tables(inst))))
    if args.provision:
        creds = provision(root, inst, args.provision[0], args.provision[1], lens=args.lens, password=args.password)
        for key, value in creds.items():
            print("%s=%s" % (key, value))
    if args.revoke:
        print("REVOKED %s" % revoke(root, args.revoke))
    return 0


if __name__ == "__main__":
    sys.exit(main())
