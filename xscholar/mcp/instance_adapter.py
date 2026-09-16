# -*- coding: utf-8 -*-
"""instance_adapter.py -- the generic MCP adapter: any instance.yaml gets the full tool set.

No hand-written adapter is needed for an instance that runs on the engine's generated schema.
Launch the server with:

    XSCHOLAR_ADAPTER=xscholar.mcp.instance_adapter
    XSCHOLAR_INSTANCE=/path/to/instance.yaml
    XSCHOLAR_DB_URL=http://127.0.0.1:8002/sql   (default)   XSCHOLAR_NS / XSCHOLAR_DB (default: instance id)
    XSCHOLAR_AGENT_ID, XSCHOLAR_ROLE, [XSCHOLAR_LENS]        the launch identity, stamped on every write

Reads return source text only under UNTRUSTED_TEXT_KEY. Writes go through the typed write surface;
a datastore refusal comes back as GateRejected with what is missing named.

Originals (xscholar/originals.py): document_get returns the original any unit was extracted from, re-hashed
on every read; health carries the originals gap by name; claim_confirm re-hashes every cited unit's original
in the store before asking the datastore, so a file that went missing since the last verify is refused now.
"""
import json
import os
import pathlib as _pathlib
import sys as _sys

if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config, gates, lens as lensmod
from xscholar import db as dbmod
from xscholar import originals
from xscholar import store as storemod
from xscholar.mcp import adapter as contract
from xscholar.pipeline import write
from xscholar.pipeline.loader import rid, q

UT = contract.UNTRUSTED_TEXT_KEY
ELIMINATED = "ELIMINATED CLAIM (false)"
SUPERSEDED = "SUPERSEDED CLAIM"


def _unreachable(exc):
    return isinstance(exc, dbmod.DbError) and "cannot reach" in str(exc)


class OriginalUnavailable(contract.AdapterError):
    """The original exists in the graph but its bytes are missing or corrupt in the store. Loud: status error."""
    status = contract.STATUS_ERROR


MAX_DOCUMENT_CHARS = 200000


class GenericAdapter(contract.InstanceAdapter):
    def __init__(self, inst, db, store=None, role=None):
        """`role`: the `access:` role of the credential `db` signs in with, when it is a record credential. Only
        health uses it, to tell a table this credential cannot read from an empty one (see _count)."""
        self.inst = inst
        self.db = db
        self.name = inst.id
        self._store = store
        self.role = role

    def _originals_store(self):
        """The instance's store, or None when the instance was not loaded from a file and none was given."""
        if self._store is None and getattr(self.inst, "source", None):
            self._store = storemod.for_instance(self.inst)
        return self._store

    # ---- plumbing --------------------------------------------------------------------------

    def _sql(self, statement, expect=None):
        try:
            return self.db.sql(statement, expect=expect)
        except dbmod.DbError as exc:
            if _unreachable(exc):
                raise contract.DbUnreachable(str(exc)) from exc
            raise

    def _writer(self, provenance):
        return write.Writer(self.db, self.inst,
                            {"agent_id": provenance.agent_id, "role": provenance.role, "lens": provenance.lens})

    def _refusal(self, exc):
        return contract.GateRejected("%s: %s" % (exc.rule, exc.missing), missing=[exc.missing])

    def _unit_row(self, row):
        return {"id": row["id"] if isinstance(row["id"], str) else str(row["id"]),
                "locator": row.get("locator"), UT: row.get("text"), "status": row.get("status"),
                "usable": row.get("status") in self.inst.usable_statuses(), "grade": row.get("grade"),
                "confirming": row.get("grade") in self.inst.confirming_grades()}

    # ---- read -------------------------------------------------------------------------------

    def unit_get(self, locator):
        ident = locator.split(":", 1)[1] if locator.startswith("unit:") else locator
        rows = self._sql("SELECT * FROM %s" % rid("unit", ident), expect=1)[0]
        if not rows and locator.strip().startswith("{"):
            try:
                fields = json.loads(locator)
            except ValueError:
                raise contract.InvalidArgument("locator must be a unit id or a JSON object of locator fields")
            where = " AND ".join("locator.%s = %s" % (k, q(v)) for k, v in fields.items())
            rows = self._sql("SELECT * FROM unit WHERE %s LIMIT 1" % where, expect=1)[0]
        if not rows:
            raise contract.NotFound("no unit %r" % locator)
        unit = self._unit_row(rows[0])
        uid = rows[0]["id"]
        labels = self._sql("SELECT tier, dir, issues, targets, role, reason, quote, chunk FROM label WHERE unit = %s"
                           % uid, expect=1)[0]
        cited_by = self._sql("SELECT VALUE string::concat(<string> in) FROM cites WHERE out = %s" % uid, expect=1)[0]
        unit["labels"] = [{**l, "grade": "agent"} for l in labels]
        unit["cited_by"] = cited_by
        return unit

    def unit_search(self, query, usable_only=True, limit=20):
        terms = [t for t in query.lower().split() if t]
        if not terms:
            raise contract.InvalidArgument("query must contain at least one word")
        where = " AND ".join("string::lowercase(text) CONTAINS %s" % q(t) for t in terms)
        if usable_only:
            where += " AND status IN %s" % gates._lit(self.inst.usable_statuses())
        rows = self._sql("SELECT * FROM unit WHERE %s LIMIT %d" % (where, max(1, min(int(limit), 200))), expect=1)[0]
        return {"query": query, "usable_only": bool(usable_only), "count": len(rows),
                "units": [self._unit_row(r) for r in rows]}

    def claim_explain(self, claim_id):
        table, _, ident = claim_id.partition(":")
        candidates = [(table, ident)] if ident and table in ("fact", "conclusion") else [("fact", claim_id), ("conclusion", claim_id)]
        for table, ident in candidates:
            rows = self._sql("SELECT * FROM %s" % rid(table, ident), expect=1)[0]
            if rows:
                return self._explain_fact(rows[0]) if table == "fact" else self._explain_conclusion(rows[0], ident)
        raise contract.NotFound("no fact or conclusion %r" % claim_id)

    def _explain_fact(self, row):
        fid = row["id"]
        cites = self._sql("SELECT grade, status, string::concat(<string> id) AS unit FROM %s->cites->unit" % fid, expect=1)[0]
        supports = self._sql("SELECT VALUE string::concat(<string> out) FROM supports WHERE in = %s" % fid, expect=1)[0]
        good = [c for c in cites if c["grade"] in self.inst.confirming_grades() and c["status"] in self.inst.usable_statuses()]
        blocking = []
        if row.get("status") not in ("confirmed", "ruled_out", "superseded") and not good:
            blocking.append("no cited unit with grade in %s and status in %s"
                            % (self.inst.confirming_grades(), self.inst.usable_statuses()))
        display = row.get("statement")
        if row.get("status") == "ruled_out":
            display = "%s: %s" % (ELIMINATED, row.get("statement"))
        elif row.get("status") == "superseded":
            display = "%s: %s" % (SUPERSEDED, row.get("statement"))
        return {"id": str(fid), "kind": "fact", "statement": display, "status": row.get("status"),
                "reasoning_type": row.get("reasoning_type"), "author": row.get("author"), "lens": row.get("lens"),
                "tried": row.get("tried"), "evidence": row.get("evidence"),
                "cites": cites, "sufficient_cites": len(good), "supports": supports, "blocking_now": blocking}

    def _explain_conclusion(self, row, ident):
        cid = row["id"]
        supporters = self._sql("SELECT status, string::concat(<string> id) AS fact FROM %s<-supports<-fact" % cid, expect=1)[0]
        attacks = self._sql("SELECT verdict, answered, lens, head, string::concat(<string> id) AS critique FROM %s<-attacks<-critique" % cid,
                            expect=1)[0]
        problems, counts = write.readiness_problems(self.db, ident, self.inst)
        return {"id": str(cid), "kind": "conclusion", "statement": row.get("statement"), "ready": bool(row.get("ready")),
                "supporting_facts": supporters, "attacks": attacks, "counts": counts, "blocking_now": problems}

    def eliminated_claims(self, about=None):
        where = "status IN ['ruled_out', 'superseded']"
        if about:
            where += " AND string::lowercase(statement) CONTAINS %s" % q(about.lower())
        rows = self._sql("SELECT id, statement, status, tried, evidence FROM fact WHERE %s" % where, expect=1)[0]
        return {"count": len(rows), "claims": [
            {"id": str(r["id"]), "label": ELIMINATED if r["status"] == "ruled_out" else SUPERSEDED,
             "statement": r["statement"], "tried": r.get("tried"), "what_the_source_shows": r.get("evidence")}
            for r in rows]}

    # ---- health counts that can never read 0 for "could not look" ----------------------------------------------
    #
    # Measured on SurrealDB 3.1.6 (2026-09-16), under a record-access credential that CAN read the table:
    #     SELECT count() AS n FROM unit GROUP ALL             -> n: 0      (one row in the table)
    #     SELECT count() AS n FROM unit WHERE true GROUP ALL  -> n: 1
    # and under a credential that cannot read the table, every form answers 0 without an error. Root sees the true
    # count either way, which is why no health test caught it: every one ran as root. So every count here carries
    # a WHERE, and a count over a table this credential cannot read is NOT_EXECUTED, not 0.

    def _readable(self, *tables):
        """True, False, or None when it cannot be known (a record credential whose access role this adapter was
        not told). Root and system users are not subject to table permissions."""
        if not getattr(self.db, "access", None):
            return True
        role = (self.inst.access_roles() or {}).get(self.role) if self.role else None
        if role is None:
            return None
        from xscholar import access
        edges = self.inst.edge_map()
        return all(access._can_read(role, table, edges) for table in tables)

    def _blind(self, tables):
        return ("NOT_EXECUTED: this server's credential (access role %s) cannot read %s, so the count is not known; "
                "a 0 here would read as clean" % (self.role, " and ".join(tables)))

    def _count(self, table, where="true", reads=()):
        tables = (table,) + tuple(reads)
        readable = self._readable(*tables)
        if readable is False:
            return self._blind(tables)
        rows = self._sql("SELECT count() AS n FROM %s WHERE %s GROUP ALL" % (table, where), expect=1)[0]
        value = rows[0]["n"] if rows else 0
        if value == 0 and readable is None:
            return ("NOT_EXECUTED: 0 rows of %s are visible to this record credential, and its access role is not "
                    "known to this server (launch with XSCHOLAR_ROLE set to it), so an unreadable table cannot be "
                    "told from an empty one" % " and ".join(tables))
        return value

    def health(self):
        n = self._count
        facts_readable = self._readable("fact")
        if facts_readable is False:
            facts_by_status = self._blind(("fact",))
        else:
            facts = self._sql("SELECT status, count() AS n FROM fact WHERE true GROUP BY status", expect=1)[0]
            facts_by_status = {r["status"]: r["n"] for r in facts}
            if not facts_by_status and facts_readable is None:
                facts_by_status = n("fact")          # the same NOT_EXECUTED wording when nothing is visible
        drift = n("fact", "status = 'confirmed' AND count(SELECT VALUE id FROM $parent->cites->unit WHERE grade IN %s "
                          "AND status IN %s) = 0" % (gates._lit(self.inst.confirming_grades()),
                                                     gates._lit(self.inst.usable_statuses())),
                  reads=("cites", "unit"))
        return {"instance": self.inst.id, "units": n("unit"), "labels": n("label"),
                "facts_by_status": facts_by_status,
                "conclusions_ready": n("conclusion", "ready = true"), "conclusions": n("conclusion"),
                "critiques_unanswered": n("critique", "answered = false"),
                "DRIFT_confirmed_without_valid_cite": drift,
                "lenses": lensmod.lens_list(self.inst),
                "originals": originals.health(self.db, self.inst),
                "live_schema": self._live_schema_health(),
                "promotion_log": self._promotion_log_health(),
                "assertions": self._assertions_health()}

    # Every count below is one that can show a problem. Where a check cannot run under this server's connection,
    # or the instance has switched the feature off, the entry says so in words: a 0 there would read as "clean".

    def _live_schema_health(self):
        """Definitions missing, altered or unexpected against what the config generates (xscholar/live_schema.py),
        over the components scripts/run_instance.py applies. Needs root: INFO FOR DB is not an agent's to read."""
        from xscholar import live_schema
        components = live_schema.deployed_components(self.inst)
        try:
            report = live_schema.check(self.inst, self.db, components=components)
        except live_schema.LiveSchemaError as exc:
            return {"NOT_EXECUTED": str(exc), "components": components}
        except dbmod.DbError as exc:
            if _unreachable(exc):
                raise contract.DbUnreachable(str(exc)) from exc
            return {"NOT_EXECUTED": "the check could not read or render the definitions: %s" % str(exc)[:300],
                    "components": components}
        counts = report.counts()
        return {"missing": counts["missing"], "altered": counts["altered"], "unexpected": counts["unexpected"],
                "expected": counts["expected"], "live": counts["live"], "components": components,
                "drift_sample": ["%s %s %s" % (r["status"], r["kind"], r["name"]) for r in report.rows[:20]]}

    def _promotion_log_health(self):
        """The hash chain verified outside the datastore, and how many promotions carry `late` (a promoter
        provisioned after a writer first wrote the row: a heuristic, never a finding)."""
        from xscholar import promotion_log
        if not self.inst.promotion_log_enabled():
            return "off: this instance does not declare promotion_log: true"
        try:
            result = promotion_log.verify(self.db)
        except promotion_log.LogError as exc:
            return {"NOT_EXECUTED": str(exc)}
        return {"ok": result["ok"], "entries": result["entries"], "first_break": result["first_break"],
                "late": result["late"],
                "limit": "verified without an anchor: a chain rebuilt whole by root still verifies; only "
                         "`python -m xscholar.promotion_log verify --anchor` against an anchor kept off this host "
                         "catches that"}

    def _assertions_health(self):
        """Promoted structured assertions that contradict the row they are about (xscholar/assertions.py)."""
        from xscholar import assertions
        if not assertions.declared(self.inst):
            return "none declared: this instance has no assertions: section, so nothing is compared"
        rep = assertions.report(self.db, self.inst)
        return {"assertion_rows": rep["assertion_rows"], "counted": rep["counted"], "agree": rep["agree"],
                "contradictions": len(rep["contradictions"]),
                "disagreeing_assertions": len(rep["disagreeing_assertions"]),
                "contradiction_sample": [{k: c[k] for k in ("source", "target", "field", "asserted", "current",
                                                             "changed_last")} for c in rep["contradictions"][:20]]}

    # ---- originals ------------------------------------------------------------------------------

    def document_get(self, ref, include_content=True):
        ref = str(ref or "").strip()
        store = self._originals_store()
        if store is None:
            raise contract.InvalidArgument("this server has no originals store for instance %s (it was not loaded "
                                           "from an instance.yaml)" % self.inst.id)
        unit, locator = None, None
        try:
            if originals.document_key(ref):
                meta = originals.document_meta(self.db, ref)
                if meta["capture"] != "none" or include_content:   # every retrieval re-hashes the bytes
                    found = originals.original_for_document(self.db, ref, store)
                    meta = found["document"]
                else:
                    found = None                                      # metadata of a never-captured original
            else:
                found = originals.original_for_unit(self.db, ref, store)
                meta, unit, locator = found["document"], found["unit"], found["locator"]
        except originals.NoOriginal as exc:
            raise contract.NotFound(str(exc)) from exc
        except (storemod.MissingOriginal, storemod.CorruptOriginal) as exc:
            raise OriginalUnavailable(str(exc), missing=["run: python -m xscholar.originals verify INSTANCE.yaml, "
                                                         "then restore the original from its source"]) from exc
        except storemod.StoreError as exc:
            raise contract.InvalidArgument(str(exc)) from exc
        except dbmod.DbError as exc:
            if _unreachable(exc):
                raise contract.DbUnreachable(str(exc)) from exc
            raise
        if "units" not in meta:
            meta = dict(meta, units=originals.document_meta(self.db, meta["id"])["units"])
        out = {"document": meta, "unit": unit, "locator": locator, "content": "not requested"}
        if not include_content:
            return out
        data = found["bytes"]
        if not originals.is_text(meta, data):
            raise contract.InvalidArgument(
                "original %s (%s, %s, %d bytes) is binary content and is not returned as text; call document_get "
                "with include_content=false for its metadata" % (meta["id"], meta.get("original_name"),
                                                                 meta.get("media_type"), len(data)),
                missing=["include_content=false"])
        text = data.decode("utf-8")
        if len(text) > MAX_DOCUMENT_CHARS:
            raise contract.InvalidArgument(
                "original %s is %d characters, over this tool's %d; read its units with unit_get, or call with "
                "include_content=false" % (meta["id"], len(text), MAX_DOCUMENT_CHARS), missing=["include_content=false"])
        out[UT] = text
        out["content"] = "returned"
        return out

    # ---- lenses -------------------------------------------------------------------------------

    def lens_view(self, name, limit=50):
        try:
            return lensmod.lens_view(self.db, self.inst, name, limit)
        except lensmod.LensError as exc:
            raise contract.InvalidArgument(str(exc), missing=["lens in %s" % [l["name"] for l in self.inst.lenses]])
        except dbmod.DbError as exc:
            if _unreachable(exc):
                raise contract.DbUnreachable(str(exc)) from exc
            raise

    # ---- write --------------------------------------------------------------------------------

    def _exists(self, table, ident):
        if not self._sql("SELECT VALUE id FROM %s" % rid(table, ident), expect=1)[0]:
            raise contract.NotFound("no %s %r" % (table, ident))

    def critique_add(self, provenance, target, lens, head, verdict, text=None):
        self._exists("conclusion", target)
        if verdict not in gates.CRITIQUE_VERDICTS:
            raise contract.InvalidArgument("verdict must be one of %s" % ", ".join(gates.CRITIQUE_VERDICTS))
        cid = self._writer(provenance).critique(target, head, verdict, text=text, lens=lens)
        problems, _ = write.readiness_problems(self.db, target, self.inst)
        return {"critique": cid, "target": rid("conclusion", target), "verdict": verdict, "by": provenance.by(),
                "blocking_now": problems}

    def critique_answer(self, provenance, critique, answer):
        cid = critique if critique.startswith("critique:") else "critique:" + critique
        rows = self._sql("SELECT VALUE string::concat(<string> out) FROM attacks WHERE in = %s" % cid, expect=1)[0]
        if not rows:
            raise contract.NotFound("no critique %r (or it attacks nothing)" % critique)
        aid = self._writer(provenance).answer(cid, answer)
        target = rows[0].split(":", 1)[1]
        problems, _ = write.readiness_problems(self.db, target, self.inst)
        return {"answer": aid, "critique": cid, "target": rows[0], "by": provenance.by(), "blocking_now": problems}

    def claim_propose(self, provenance, slug, statement, reasoning_type="inference", note=None):
        try:
            fid = self._writer(provenance).propose(slug, statement, reasoning_type=reasoning_type, note=note)
        except ValueError as exc:
            raise contract.InvalidArgument(str(exc))
        return {"fact": fid, "status": "proposed", "by": provenance.by(),
                "next": "cite units and call claim_confirm, or claim_rule_out with what the source shows"}

    def claim_confirm(self, provenance, slug, unit_ids=None, note=None):
        self._exists("fact", slug)
        for uid in unit_ids or []:
            self._exists("unit", uid)
        self._check_cited_originals(unit_ids or [])
        try:
            fid = self._writer(provenance).confirm(slug, unit_ids or [], note=note)
        except write.GateRefusal as exc:
            raise self._refusal(exc)
        return {"fact": fid, "status": "confirmed", "cited": list(unit_ids or []), "by": provenance.by()}

    def _check_cited_originals(self, unit_ids):
        """Re-hash the original of every cited unit that links to one. The datastore's `integrity` is what the last
        load or verify found; the store is what is true now. A unit with no original is left to the datastore,
        whose enforcement (when applied) refuses to count it."""
        store = self._originals_store()
        if store is None:
            return
        for uid in unit_ids:
            try:
                originals.original_for_unit(self.db, uid, store)
            except originals.NoOriginal:
                continue
            except (storemod.MissingOriginal, storemod.CorruptOriginal) as exc:
                raise contract.GateRejected("unit %s cannot support a claim: %s" % (uid, exc),
                                            missing=["unit %s's original, restored to the store and verified" % uid])

    def claim_rule_out(self, provenance, slug, statement, tried, evidence):
        fid = self._writer(provenance).rule_out(slug, statement, tried, evidence)
        return {"fact": fid, "status": "ruled_out", "label": ELIMINATED, "by": provenance.by()}

    def claim_support(self, provenance, slug, conclusion):
        self._exists("fact", slug)
        self._exists("conclusion", conclusion)
        self._writer(provenance).support(slug, conclusion)
        problems, _ = write.readiness_problems(self.db, conclusion, self.inst)
        return {"fact": rid("fact", slug), "conclusion": rid("conclusion", conclusion), "by": provenance.by(),
                "blocking_now": problems}

    def conclusion_mark_ready(self, provenance, conclusion):
        self._exists("conclusion", conclusion)
        try:
            cid = self._writer(provenance).mark_ready(conclusion)
        except write.GateRefusal as exc:
            raise self._refusal(exc)
        return {"conclusion": cid, "ready": True, "by": provenance.by()}


def create_adapter_for(inst, db, store=None, role=None):
    return GenericAdapter(inst, db, store, role)


def create_adapter():
    path = os.environ.get("XSCHOLAR_INSTANCE")
    if not path:
        raise contract.AdapterError("XSCHOLAR_INSTANCE is required: the path to the instance.yaml")
    inst = config.load(path)
    db = dbmod.Db(url=os.environ.get("XSCHOLAR_DB_URL", "http://127.0.0.1:8002/sql"),
                  ns=os.environ.get("XSCHOLAR_NS") or inst.id, db=os.environ.get("XSCHOLAR_DB") or inst.id,
                  user=os.environ.get("XSCHOLAR_DB_USER", "root"), password=os.environ.get("XSCHOLAR_DB_PASS", "root"),
                  access=os.environ.get("XSCHOLAR_DB_ACCESS"))  # set: a provisioned agent credential, not root
    # the credential's access role, for health's readability check; provision() prints it as XSCHOLAR_ROLE
    role = os.environ.get("XSCHOLAR_ROLE") if os.environ.get("XSCHOLAR_DB_ACCESS") else None
    return GenericAdapter(inst, db, role=role)
