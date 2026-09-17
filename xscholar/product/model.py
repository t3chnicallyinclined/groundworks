# -*- coding: utf-8 -*-
"""model.py -- the product's SurrealDB data model: namespaces, versioned migrations, version checks, members.

One SurrealDB holds a workspace. Namespace `workspace` = registry + cross-project memory; namespace
`proj_<slug>` = one project's knowledge and work. Both use the database named DB_NAME.

    root = connect(url, WORKSPACE_NS)                  # root: schema and loads
    ensure(root); migrate(root, "workspace")
    proj = connect(url, project_ns("demo")); ensure(proj); migrate(proj, "project")
    provision(proj, "w1", "worker", "secret")
    as_worker = connect(url, project_ns("demo"), member="w1", password="secret")

Migrations are `schema/NNNN_<name>.surql`; the first line `-- scope: workspace|project` says which namespace
kind a file belongs to. `schema_version:current` records the product, the schema MAJOR and the last migration
number applied. migrate() applies only files numbered above it, each file and its version bump in ONE
transaction, so applying twice is a no-op and a failed file leaves the version where it was. A database whose
recorded major is not SCHEMA_MAJOR, or a server whose major is not SURREAL_MAJOR, is refused before any migration runs
(the only earlier write is the idempotent schema_version table definition).

Standard library only (plus xscholar.db, itself standard library only).
"""
import pathlib
import re

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import db as dbmod  # noqa: E402

# The product's name lives HERE and nowhere else (placeholder until the owner picks one).
PRODUCT = "xsp"
DB_NAME = PRODUCT

SCHEMA_MAJOR = 1          # bump only with a migration path; an older/newer major database is refused
SURREAL_MAJOR = 3         # behaviour below is measured on 3.1.6
WORKSPACE_NS = "workspace"
PROJECT_PREFIX = "proj_"
ROLES = ("lead", "worker", "reviewer")
ACCESS = "member"
SCOPES = ("workspace", "project")

SCHEMA_DIR = pathlib.Path(__file__).resolve().parent / "schema"
FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.surql$")
SCOPE_RE = re.compile(r"^--\s*scope:\s*(\w+)\s*$", re.M)
SLUG_RE = re.compile(r"^[a-z0-9_]{1,40}$")
MEMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

# Created by the migration engine itself, before the first file, so the version can be read on any database.
BOOTSTRAP = (
    "DEFINE TABLE IF NOT EXISTS schema_version TYPE NORMAL SCHEMAFULL;"
    "ALTER TABLE schema_version PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer'] "
    "FOR create, update, delete NONE;"
    "DEFINE FIELD IF NOT EXISTS product ON schema_version TYPE string;"
    "DEFINE FIELD IF NOT EXISTS scope ON schema_version TYPE string;"
    "DEFINE FIELD IF NOT EXISTS major ON schema_version TYPE int;"
    "DEFINE FIELD IF NOT EXISTS applied ON schema_version TYPE int;"
    "DEFINE FIELD IF NOT EXISTS files ON schema_version TYPE array<string>;"
    "DEFINE FIELD IF NOT EXISTS updated_at ON schema_version TYPE datetime;"
)


class ModelError(Exception):
    pass


class WrongMajor(ModelError):
    """The database or the server is a major version this code was not written for."""


def project_ns(slug):
    if not SLUG_RE.match(str(slug or "")):
        raise ModelError("project slug %r must be 1-40 of a-z 0-9 _" % slug)
    return PROJECT_PREFIX + slug


def scope_of(ns):
    if ns == WORKSPACE_NS:
        return "workspace"
    if ns.startswith(PROJECT_PREFIX) and SLUG_RE.match(ns[len(PROJECT_PREFIX):]):
        return "project"
    raise ModelError("namespace %r is neither %r nor %s<slug>" % (ns, WORKSPACE_NS, PROJECT_PREFIX))


def connect(url, ns, member=None, password=None, root_user="root", root_pass="root", timeout=30):
    """Root (member=None) or a member credential signed in through DEFINE ACCESS member on (ns, DB_NAME)."""
    scope_of(ns)
    if member is None:
        return dbmod.Db(url=url, ns=ns, db=DB_NAME, user=root_user, password=root_pass, timeout=timeout)
    return dbmod.Db(url=url, ns=ns, db=DB_NAME, user=member, password=password, access=ACCESS, timeout=timeout)


def migrations(scope, schema_dir=None):
    """[(number, filename, text)] for one scope, in order. Duplicate numbers or a missing scope line refuse."""
    if scope not in SCOPES:
        raise ModelError("scope %r is not one of %s" % (scope, SCOPES))
    seen, out = {}, []
    for path in sorted(pathlib.Path(schema_dir or SCHEMA_DIR).glob("*.surql")):
        m = FILE_RE.match(path.name)
        if not m:
            raise ModelError("migration file %s is not named NNNN_name.surql" % path.name)
        number = int(m.group(1))
        if number in seen:
            raise ModelError("migration number %04d used twice: %s and %s" % (number, seen[number], path.name))
        seen[number] = path.name
        text = path.read_text(encoding="utf-8")
        found = SCOPE_RE.search(text)
        if not found or found.group(1) not in SCOPES:
            raise ModelError("migration %s has no '-- scope: workspace|project' line" % path.name)
        if found.group(1) == scope:
            out.append((number, path.name, text))
    return out


def check_server(version_text):
    """None if the server major is SURREAL_MAJOR; raise WrongMajor otherwise."""
    found = VERSION_RE.search(version_text or "")
    if not found or int(found.group(1)) != SURREAL_MAJOR:
        raise WrongMajor("SurrealDB %r refused: this data model is measured on %d.x only" % (version_text, SURREAL_MAJOR))


def ensure(root):
    """Create the namespace and database if missing (root only)."""
    blocks = root._post("DEFINE NAMESPACE IF NOT EXISTS %s; USE NS %s; DEFINE DATABASE IF NOT EXISTS %s;"
                        % (root.ns, root.ns, root.db))
    bad = [b for b in blocks if b.get("status") != "OK"]
    if bad:
        raise ModelError("could not create %s/%s: %s" % (root.ns, root.db, bad[0].get("result")))


def current_version(root):
    """The schema_version:current row, or None on a database never migrated."""
    root.sql(BOOTSTRAP)
    rows = root.sql("SELECT * FROM ONLY schema_version:current;", expect=1)[0]
    return rows or None


def migrate(root, scope=None, schema_dir=None, check_server_version=True):
    """Apply every migration above the recorded one, in order. Returns the numbers applied ([] = no-op)."""
    scope = scope or scope_of(root.ns)
    if scope != scope_of(root.ns):
        raise ModelError("scope %r does not belong in namespace %r" % (scope, root.ns))
    if check_server_version:
        check_server(root.version())
    current = current_version(root)
    if current is not None:
        if current.get("product") != PRODUCT or current.get("scope") != scope:
            raise WrongMajor("database %s/%s holds %s/%s, not %s/%s"
                             % (root.ns, root.db, current.get("product"), current.get("scope"), PRODUCT, scope))
        if current.get("major") != SCHEMA_MAJOR:
            raise WrongMajor("database %s/%s is schema major %s; this code is major %d. Refused before any migration ran "
                             "(a major change needs its own migration path)." % (root.ns, root.db, current.get("major"), SCHEMA_MAJOR))
    last = current["applied"] if current else 0
    files = list(current["files"]) if current else []
    applied = []
    for number, name, text in migrations(scope, schema_dir):
        if number <= last:
            continue
        files.append(name)
        bump = ("UPSERT schema_version:current CONTENT {product: %s, scope: %s, major: %d, applied: %d, files: %s, "
                "updated_at: time::now()};" % (_lit(PRODUCT), _lit(scope), SCHEMA_MAJOR, number,
                                               "[" + ", ".join(_lit(f) for f in files) + "]"))
        try:
            root.sql("BEGIN TRANSACTION;\n%s\n;%s\nCOMMIT TRANSACTION;" % (text, bump))
        except dbmod.DbError as exc:
            raise ModelError("migration %s failed; schema_version stays at %04d: %s" % (name, last, exc)) from exc
        last = number
        applied.append(number)
    return applied


def open_namespace(url, ns, schema_dir=None, **kw):
    """Root connection to ns with its namespace/database created and migrated."""
    root = connect(url, ns, **kw)
    ensure(root)
    migrate(root, scope_of(ns), schema_dir=schema_dir)
    return root


def provision(root, member, role, password):
    """Create or reset one member credential in root's namespace (root only)."""
    if not MEMBER_RE.match(str(member or "")):
        raise ModelError("member id %r is not a plain identifier" % member)
    if role not in ROLES:
        raise ModelError("role %r is not one of %s" % (role, ROLES))
    root.sql("UPSERT type::record('member', %s) SET role = %s, pass = crypto::argon2::generate(%s), enabled = true;"
             % (_lit(member), _lit(role), _lit(password)), expect=1)
    return member


def _lit(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"
