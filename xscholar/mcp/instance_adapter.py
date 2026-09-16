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
"""
import json
import os
import pathlib as _pathlib
import sys as _sys

if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config, gates, lens as lensmod
from xscholar import db as dbmod
from xscholar.mcp import adapter as contract
from xscholar.pipeline import write
from xscholar.pipeline.loader import rid, q

UT = contract.UNTRUSTED_TEXT_KEY
ELIMINATED = "ELIMINATED CLAIM (false)"
SUPERSEDED = "SUPERSEDED CLAIM"


def _unreachable(exc):
    return isinstance(exc, dbmod.DbError) and "cannot reach" in str(exc)


class GenericAdapter(contract.InstanceAdapter):
    def __init__(self, inst, db):
        self.inst = inst
        self.db = db
        self.name = inst.id

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

    def health(self):
        def n(table, where=""):
            rows = self._sql("SELECT count() AS n FROM %s %s GROUP ALL" % (table, where), expect=1)[0]
            return rows[0]["n"] if rows else 0
        facts = self._sql("SELECT status, count() AS n FROM fact GROUP BY status", expect=1)[0]
        drift = n("fact", "WHERE status = 'confirmed' AND count(SELECT VALUE id FROM $parent->cites->unit WHERE grade IN %s AND status IN %s) = 0"
                  % (gates._lit(self.inst.confirming_grades()), gates._lit(self.inst.usable_statuses())))
        return {"instance": self.inst.id, "units": n("unit"), "labels": n("label"),
                "facts_by_status": {r["status"]: r["n"] for r in facts},
                "conclusions_ready": n("conclusion", "WHERE ready = true"), "conclusions": n("conclusion"),
                "critiques_unanswered": n("critique", "WHERE answered = false"),
                "DRIFT_confirmed_without_valid_cite": drift,
                "lenses": lensmod.lens_list(self.inst)}

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
        try:
            fid = self._writer(provenance).confirm(slug, unit_ids or [], note=note)
        except write.GateRefusal as exc:
            raise self._refusal(exc)
        return {"fact": fid, "status": "confirmed", "cited": list(unit_ids or []), "by": provenance.by()}

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


def create_adapter_for(inst, db):
    return GenericAdapter(inst, db)


def create_adapter():
    path = os.environ.get("XSCHOLAR_INSTANCE")
    if not path:
        raise contract.AdapterError("XSCHOLAR_INSTANCE is required: the path to the instance.yaml")
    inst = config.load(path)
    db = dbmod.Db(url=os.environ.get("XSCHOLAR_DB_URL", "http://127.0.0.1:8002/sql"),
                  ns=os.environ.get("XSCHOLAR_NS") or inst.id, db=os.environ.get("XSCHOLAR_DB") or inst.id,
                  user=os.environ.get("XSCHOLAR_DB_USER", "root"), password=os.environ.get("XSCHOLAR_DB_PASS", "root"),
                  access=os.environ.get("XSCHOLAR_DB_ACCESS"))  # set: a provisioned agent credential, not root
    return GenericAdapter(inst, db)
