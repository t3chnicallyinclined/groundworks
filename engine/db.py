# -*- coding: utf-8 -*-
"""db.py -- a small, loud SurrealDB HTTP client.

Every failure is an exception naming the statement that failed. Nothing is swallowed into an
empty result: silence must never look like "nothing known" (CLAUDE.md rule 5).

    db = Db(url="http://127.0.0.1:8021/sql", ns="demo", db="incident")
    db.sql("SELECT * FROM fact WHERE status = 'confirmed'")
    db.script(open("schema.surql", encoding="utf-8").read())

Standard library only.
"""
import base64
import json
import urllib.error
import urllib.request


class DbError(Exception):
    """A statement failed, or the server could not be reached."""


class Db:
    def __init__(self, url="http://127.0.0.1:8002/sql", ns="test", db="test",
                 user="root", password="root", timeout=30):
        self.url = url
        self.ns = ns
        self.db = db
        self.timeout = timeout
        self._auth = base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")

    # ---- plumbing ----------------------------------------------------------------------------

    def _post(self, body):
        request = urllib.request.Request(
            self.url, data=body.encode("utf-8"), method="POST",
            headers={"Accept": "application/json", "Content-Type": "text/plain",
                     "Authorization": "Basic " + self._auth,
                     "surreal-ns": self.ns, "surreal-db": self.db})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
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

    # ---- public ------------------------------------------------------------------------------

    def sql(self, statement, expect=None):
        """Run one or more statements. Raise on the first failure, naming it."""
        blocks = self._run(statement, expect)
        for index, block in enumerate(blocks):
            if block.get("status") != "OK":
                raise DbError("statement %d failed: %s\n  statement: %s"
                              % (index + 1, block.get("result"), statement[:300]))
        return [block.get("result") for block in blocks]

    def try_sql(self, statement):
        """Run a statement that is allowed to fail. Return (ok, message). Used by gate probes."""
        try:
            blocks = self._run(statement)
        except DbError as exc:
            return False, str(exc)
        for block in blocks:
            if block.get("status") != "OK":
                return False, str(block.get("result"))
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

    def ping(self):
        self.sql("RETURN 1", expect=1)
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
