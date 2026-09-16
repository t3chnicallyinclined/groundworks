# -*- coding: utf-8 -*-
"""write.py -- the typed write surface. Agents and the MCP adapter write only through this.

Every write stamps provenance from the Writer's identity (agent id, role, lens); callers cannot
supply an author. Promotion helpers let the datastore decide, and when it refuses they raise
GateRefusal naming the rule and exactly what is missing, in words an agent can act on.

    w = Writer(db, instance, identity={"agent_id": "labeler_7", "role": "reader", "lens": None})
    w.propose("rc_pool", "The pool was exhausted", reasoning_type="direct_quote")
    w.confirm("rc_pool", ["log_001"])          # GateRefusal unless log_001 is source-grade and usable
    w.rule_out("dns", "DNS failed", tried="resolver logs", evidence="resolver.log:5501-5620 shows no failure")
Standard library only.
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.pipeline.loader import rid, q  # noqa: E402
from xscholar.patterns import edge_ids  # noqa: E402
from xscholar.temporal import valid_clause  # noqa: E402

REASONING_TYPES = ["direct_quote", "inference", "structural_analogy", "statistical", "expert_judgment"]
IDENTITY_KEYS = ("agent_id", "role", "lens")


class GateRefusal(Exception):
    def __init__(self, rule, missing, detail=None):
        super().__init__("%s: %s" % (rule, missing))
        self.rule = rule
        self.missing = missing
        self.detail = detail or {}


class Writer:
    def __init__(self, db, inst, identity):
        missing = [k for k in IDENTITY_KEYS if k not in identity]
        if missing or not identity.get("agent_id"):
            raise ValueError("identity needs %s" % ", ".join(IDENTITY_KEYS))
        self.db = db
        self.inst = inst
        self.identity = dict(identity)
        self.author = "%s|role=%s" % (identity["agent_id"], identity["role"])
        self.keyer = edge_ids.Keyer()

    # ---- claims -----------------------------------------------------------------------------

    def propose(self, slug, statement, reasoning_type="inference", note=None, valid_from=None, valid_to=None):
        """`valid_from` / `valid_to` (datetime or ISO text): when the claim held in the source. Optional;
        an omitted bound is left untouched on a re-write. `learned_at` is stamped by the datastore."""
        if reasoning_type not in REASONING_TYPES:
            raise ValueError("reasoning_type must be one of %s" % ", ".join(REASONING_TYPES))
        self.db.sql("UPSERT %s SET statement=%s, status = IF status != NONE { status } ELSE { 'proposed' }, "
                    "note=%s, reasoning_type=%s, author=%s, lens=%s%s"
                    % (rid("fact", slug), q(statement), q(note), q(reasoning_type), q(self.author),
                       q(self.identity.get("lens")), valid_clause(valid_from, valid_to)), expect=1)
        return rid("fact", slug)

    def cite(self, slug, unit_ids):
        for uid in unit_ids:
            eid = self.keyer("write", "cites", slug, uid)
            self.db.sql("RELATE %s->cites:%s->%s SET author=%s" % (rid("fact", slug), eid, rid("unit", uid), q(self.author)),
                        expect=1)

    def confirm(self, slug, unit_ids=(), note=None, valid_from=None):
        """Cite the units, then ask the datastore to confirm. Refusal comes back explained.
        `valid_from` (optional) records when the confirmed claim held in the source."""
        if unit_ids:
            self.cite(slug, unit_ids)
        ok, message = self.db.try_sql("UPDATE %s SET status='confirmed'%s%s"
                                      % (rid("fact", slug), ", note=%s" % q(note) if note else "",
                                         valid_clause(valid_from)))
        if ok:
            return rid("fact", slug)
        cited = self.db.sql("SELECT grade, status, record::id(id) AS unit FROM %s->cites->unit" % rid("fact", slug),
                            expect=1)[0]
        good = [c for c in cited if c["grade"] in self.inst.confirming_grades() and c["status"] in self.inst.usable_statuses()]
        bad = [c for c in cited if c not in good]
        missing = ("needs at least one cited unit whose grade is in %s and whose status is in %s; "
                   "cited now: %s" % (self.inst.confirming_grades(), self.inst.usable_statuses(),
                                      ", ".join("%s (grade %s, status %s)" % (c["unit"], c["grade"], c["status"]) for c in cited)
                                      or "nothing"))
        raise GateRefusal("confirmed_needs_source_grade", missing, {"cited": cited, "insufficient": bad, "db": message})

    def rule_out(self, slug, statement, tried, evidence):
        """Negative knowledge is never gated, and must be cheaper to record than a fact."""
        self.db.sql("UPSERT %s SET statement=%s, status='ruled_out', tried=%s, evidence=%s, author=%s, lens=%s"
                    % (rid("fact", slug), q(statement), q(tried), q(evidence), q(self.author),
                       q(self.identity.get("lens"))), expect=1)
        return rid("fact", slug)

    def support(self, slug, conclusion):
        eid = self.keyer("write", "supports", slug, conclusion)
        self.db.sql("RELATE %s->supports:%s->%s SET author=%s"
                    % (rid("fact", slug), eid, rid("conclusion", conclusion), q(self.author)), expect=1)

    # ---- workbench --------------------------------------------------------------------------

    def critique(self, conclusion, head, verdict, text=None, lens=None):
        lens = lens or self.identity.get("lens") or self.identity["role"]
        cid = "%s__%s__%s" % (conclusion, lens, self.keyer("write", "critique", conclusion, head)[-8:])
        self.db.sql("CREATE %s SET lens=%s, head=%s, verdict=%s, text=%s, author=%s, answered=false"
                    % (rid("critique", cid), q(lens), q(head), q(verdict), q(text), q(self.author)), expect=1)
        eid = self.keyer("write", "attacks", cid, conclusion)
        self.db.sql("RELATE %s->attacks:%s->%s SET author=%s" % (rid("critique", cid), eid, rid("conclusion", conclusion), q(self.author)),
                    expect=1)
        return rid("critique", cid)

    def answer(self, critique_id, text):
        aid = "ans_" + critique_id.split(":", 1)[1].strip("⟨⟩")
        self.db.sql("CREATE %s SET text=%s, author=%s" % (rid("answer", aid), q(text), q(self.author)), expect=1)
        eid = self.keyer("write", "answers", aid, critique_id)
        self.db.sql("RELATE %s->answers:%s->%s; UPDATE %s SET answered=true"
                    % (rid("answer", aid), eid, critique_id, critique_id), expect=2)
        return rid("answer", aid)

    def mark_ready(self, conclusion):
        target = rid("conclusion", conclusion)
        ok, message = self.db.try_sql("UPDATE %s SET ready=true" % target)
        if ok:
            return target
        problems, counts = readiness_problems(self.db, conclusion)
        raise GateRefusal("conclusion_ready", "; ".join(problems) or message, {"counts": counts, "db": message})


def readiness_problems(db, conclusion):
    """What still blocks a conclusion, in words an agent can act on, plus the raw counts."""
    target = rid("conclusion", conclusion)
    counts = db.sql(
        "RETURN {supports: count(SELECT VALUE id FROM %s<-supports<-fact WHERE status = 'confirmed'), "
        "attacks: count(SELECT VALUE id FROM %s<-attacks<-critique), "
        "fatal: count(SELECT VALUE id FROM %s<-attacks<-critique WHERE verdict = 'fatal'), "
        "damaging_open: count(SELECT VALUE id FROM %s<-attacks<-critique WHERE verdict = 'damaging' AND answered = false)}"
        % (target, target, target, target), expect=1)[0]
    problems = []
    if counts["supports"] < 1:
        problems.append("no confirmed supporting fact linked (confirm a fact, then support(fact, conclusion))")
    if counts["attacks"] < 1:
        problems.append("never attacked (run a red team or panel first)")
    if counts["fatal"]:
        problems.append("%d fatal attack(s) standing (answer or narrow the conclusion)" % counts["fatal"])
    if counts["damaging_open"]:
        problems.append("%d damaging head(s) unanswered (answer them first)" % counts["damaging_open"])
    return problems, counts
