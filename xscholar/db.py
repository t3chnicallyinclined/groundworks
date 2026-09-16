# -*- coding: utf-8 -*-
"""db.py -- a small, loud SurrealDB HTTP client.

Every failure is an exception naming the statement that failed. Nothing is swallowed into an
empty result: silence must never look like "nothing known" (CLAUDE.md rule 5).

    db = Db(url="http://127.0.0.1:8021/sql", ns="demo", db="incident")
    db.sql("SELECT * FROM fact WHERE status = 'confirmed'")
    db.script(open("schema.surql", encoding="utf-8").read())

Two ways in. Root (the default) is a system user over HTTP Basic auth; it bypasses table permissions
and is for schema, gates, loads and archive work. A provisioned agent credential (xscholar/access.py)
passes `access=`: the client signs in through DEFINE ACCESS ... TYPE RECORD, carries the token as
Bearer, and the datastore applies the instance's PERMISSIONS to every statement. SurrealDB answers a
denied CREATE/UPDATE/RELATE/DELETE with an EMPTY result and status OK, which would look like success;
under record auth this client turns that into PermissionDenied (CLAUDE.md rule 5).

Standard library only.
"""
import base64
import json
import re
import urllib.error
import urllib.request

WRITE_RE = re.compile(r"^\s*(CREATE|UPSERT|UPDATE|RELATE|DELETE|INSERT)\b", re.I)

# The engine's behaviour is measured against this series, not assumed from the documentation. Things
# that are true of 3.1 and may not be true elsewhere: a schema re-apply silently drops a CHANGEFEED;
# a delete carries no before-image; `DEFINE USER ... ON DATABASE` is a system user and bypasses table
# PERMISSIONS; a denied write returns an empty result with status OK; `TYPE object FLEXIBLE` is the
# accepted spelling. Each of those is a load-bearing assumption somewhere in this repo.
MEASURED_AGAINST = (3, 1)
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class DbError(Exception):
    """A statement failed, or the server could not be reached."""


class PermissionDenied(DbError):
    """A write under an agent credential was refused by the datastore's PERMISSIONS (or named a
    record that does not exist; SurrealDB does not distinguish). The MCP envelope reports it as
    rejected_by_gate with `missing` naming the credential needed."""
    status = "rejected_by_gate"

    def __init__(self, message, missing=None):
        super().__init__(message)
        self.missing = list(missing or [])


class Db:
    def __init__(self, url="http://127.0.0.1:8002/sql", ns="test", db="test",
                 user="root", password="root", timeout=30, access=None):
        self.url = url
        self.ns = ns
        self.db = db
        self.timeout = timeout
        self.user = user
        self.access = (access or "").strip() or None   # DEFINE ACCESS name: record auth instead of root
        self._password = password
        self._basic = base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")
        self._token = None

    # ---- plumbing ----------------------------------------------------------------------------

    def _signin(self):
        """Exchange the agent credential for a token through DEFINE ACCESS ... TYPE RECORD."""
        base = self.url[:-4] if self.url.endswith("/sql") else self.url.rstrip("/")
        body = json.dumps({"ns": self.ns, "db": self.db, "ac": self.access, "id": self.user, "pass": self._password})
        request = urllib.request.Request(base + "/signin", data=body.encode("utf-8"), method="POST",
                                         headers={"Accept": "application/json", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise DbError("signin refused for agent %r via access %r on %s/%s (HTTP %s: %s); the credential may "
                          "be revoked, expired or mistyped" % (self.user, self.access, self.ns, self.db, exc.code, detail)) from exc
        except urllib.error.URLError as exc:
            raise DbError("cannot reach %s (%s); the database may be stopped" % (self.url, exc.reason)) from exc
        try:
            token = json.loads(raw).get("token")
        except ValueError:
            token = None
        if not token:
            raise DbError("signin for agent %r returned no token: %s" % (self.user, raw[:300]))
        self._token = token
        return token

    def _headers(self):
        if self.access:
            auth = "Bearer " + (self._token or self._signin())
        else:
            auth = "Basic " + self._basic
        return {"Accept": "application/json", "Content-Type": "text/plain", "Authorization": auth,
                "surreal-ns": self.ns, "surreal-db": self.db}

    def _post(self, body, _retry=True):
        request = urllib.request.Request(self.url, data=body.encode("utf-8"), method="POST", headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code == 401 and self.access and _retry:   # token expired: sign in once more
                self._token = None
                return self._post(body, _retry=False)
            raise DbError("HTTP %s from %s: %s" % (exc.code, self.url, detail)) from exc
        except urllib.error.URLError as exc:
            raise DbError("cannot reach %s (%s); the database may be stopped" % (self.url, exc.reason)) from exc
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise DbError("server did not return JSON: %s" % raw[:300]) from exc

    def _run(self, statement, expect=None):
        blocks = self._post("USE NS %s DB %s; %s" % (self.ns, self.db, statement))
        if not isinstance(blocks, list):
            raise DbError("unexpected response shape: %s" % type(blocks).__name__)
        blocks = blocks[1:] if blocks and blocks[0].get("status") == "OK" and len(blocks) > 1 else blocks
        if expect is not None and len(blocks) != expect:
            raise DbError("expected %d result block(s), got %d, for: %s" % (expect, len(blocks), statement[:200]))
        return blocks

    def _denied(self, statement, blocks):
        """Under record auth, a write whose result is [] was refused by PERMISSIONS. Root never is."""
        if not self.access:
            return None
        parts = [s.strip() for s in _split_statements(statement) if s.strip()]
        pairs = zip(parts, blocks) if len(parts) == len(blocks) else [(statement, b) for b in blocks]
        for text, block in pairs:
            if block.get("status") == "OK" and block.get("result") == [] and WRITE_RE.match(text):
                verb = WRITE_RE.match(text).group(1).upper()
                return ("%s refused by datastore permissions for agent %r (or the record does not exist): %s"
                        % (verb, self.user, text[:200]))
        return None

    # ---- public ------------------------------------------------------------------------------

    def sql(self, statement, expect=None):
        """Run one or more statements. Raise on the first failure, naming it."""
        blocks = self._run(statement, expect)
        for index, block in enumerate(blocks):
            if block.get("status") != "OK":
                raise DbError("statement %d failed: %s\n  statement: %s"
                              % (index + 1, block.get("result"), statement[:300]))
        denied = self._denied(statement, blocks)
        if denied:
            raise PermissionDenied(denied, missing=["a launch credential whose role may perform this write"])
        return [block.get("result") for block in blocks]

    def try_sql(self, statement):
        """Run a statement that a GATE is allowed to refuse. Return (ok, message). Used by gate probes
        and promotion helpers. A PERMISSIONS refusal is not a gate answer: under record auth it raises
        PermissionDenied so a caller never explains a denied write as missing evidence."""
        try:
            blocks = self._run(statement)
        except DbError as exc:
            return False, str(exc)
        for block in blocks:
            if block.get("status") != "OK":
                return False, str(block.get("result"))
        denied = self._denied(statement, blocks)
        if denied:
            raise PermissionDenied(denied, missing=["a launch credential whose role may perform this write"])
        return True, ""

    def script(self, text):
        """Run a multi-statement script. Return a per-statement report; raise if any failed."""
        statements = [s.strip() for s in _split_statements(text) if s.strip()]
        results, failed = [], []
        for statement in statements:
            ok, message = self.try_sql(statement)
            results.append({"statement": statement[:120], "ok": ok, "error": message})
            if not ok:
                failed.append(results[-1])
        if failed:
            detail = "\n".join("  [%s] %s" % (f["statement"], f["error"][:300]) for f in failed[:8])
            raise DbError("%d of %d statements failed:\n%s" % (len(failed), len(statements), detail))
        return results

    def version(self):
        """What the server says it is, e.g. 'surrealdb-3.1.6+20260813.cfbaec4'."""
        url = self.url[:-len("/sql")] if self.url.endswith("/sql") else self.url.rstrip("/")
        request = urllib.request.Request(url.rstrip("/") + "/version", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", "replace").strip()
        except urllib.error.URLError as exc:
            raise DbError("could not reach %s to ask its version: %s" % (url, exc))

    def version_problem(self):
        """None if this server is one the engine's behaviour was measured against, else why not.

        This is what the pinned container image was really for. A version is a fact the server will
        tell you; a container is a way of hoping it never changes.
        """
        text = self.version()
        found = VERSION_RE.search(text)
        if not found:
            return "the server at %s reports its version as %r, which does not contain one" % (self.url, text)
        series = (int(found.group(1)), int(found.group(2)))
        if series[0] != MEASURED_AGAINST[0]:
            return ("this engine's behaviour is measured against SurrealDB %d.%d; the server at %s is %s. "
                    "A different major series may differ in ways this repo relies on (changefeeds, "
                    "before-images on delete, how a denied write is reported). Point it at %d.x or "
                    "re-measure first." % (MEASURED_AGAINST[0], MEASURED_AGAINST[1], self.url, text,
                                           MEASURED_AGAINST[0]))
        if series[1] != MEASURED_AGAINST[1]:
            return ("NOTE: measured against SurrealDB %d.%d, talking to %s. Same major series, so this "
                    "is a warning, not a refusal." % (MEASURED_AGAINST[0], MEASURED_AGAINST[1], text))
        return None

    def ping(self, check_version=True):
        self.sql("RETURN 1", expect=1)
        if check_version:
            problem = self.version_problem()
            if problem and problem.startswith("NOTE:"):
                print(problem)
            elif problem:
                raise DbError(problem)
        return True

    def count(self, table):
        rows = self.sql("SELECT count() AS n FROM %s GROUP ALL" % table, expect=1)[0]
        return rows[0]["n"] if rows else 0


def _strip_comments(text):
    """Remove -- comments (outside quotes) so they can never be sent as statements."""
    kept = []
    for line in text.splitlines():
        out, quote, i = [], None, 0
        while i < len(line):
            char = line[i]
            if quote:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "-" and line[i:i + 2] == "--":
                break
            out.append(char)
            i += 1
        kept.append("".join(out))
    return "\n".join(kept)


def _split_statements(text):
    """Split on semicolons that are not inside quotes, braces or brackets."""
    out, depth, quote, buf = [], 0, None, []
    for char in _strip_comments(text):
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == ";" and depth <= 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    out.append("".join(buf))
    return out
