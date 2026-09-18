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
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks.pipeline.loader import rid, q  # noqa: E402
from groundworks.patterns import edge_ids  # noqa: E402
from groundworks.temporal import valid_clause  # noqa: E402

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
        _raise_if_not_independent(message, rid("fact", slug))
        _raise_if_not_about_evidence(self.db, self.inst, message, rid("fact", slug))
        cited =self.db.sql("SELECT grade, status, record::id(id) AS unit FROM %s->cites->unit" % rid("fact", slug),
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

    def relate(self, edge, source, target, **fields):
        """One relation of any edge type this instance has, declared or built in.

        The two tables come from the instance's own edge map, so an edge declared in `edges:` is
        writable without an engine change. Nothing here loosens anything: the datastore still holds
        the edge's field definitions, every gate and every per-role permission."""
        edges = self.inst.edge_map()
        if edge not in edges:
            raise ValueError("instance %s has no edge %r; known: %s (declare it under `edges:`)"
                             % (self.inst.id, edge, ", ".join(sorted(edges))))
        src, dst = edges[edge]
        eid = self.keyer("write", edge, str(source), str(target))
        sets = ", ".join(["author=%s" % q(self.author)]
                         + ["%s=%s" % (name, q(value)) for name, value in sorted(fields.items())])
        self.db.sql("RELATE %s->%s:%s->%s SET %s"
                    % (rid(src, source), edge, eid, rid(dst, target), sets), expect=1)
        return eid

    # ---- workbench --------------------------------------------------------------------------

    def critique(self, conclusion, head, verdict, text=None, lens=None):
        """One head of attack. Re-stating the same head under the same lens REPLACES its verdict.

        This is the contract the MCP adapter has always documented, and until 2026-09-16 the code did
        not honour it: the id came from a Keyer that increments on every repeat, so a re-statement
        made a SECOND critique inside one session and collided on the id in a later one. Measured:

            1st  critique:c1__lens_skeptic__476d2269  fatal
            2nd  critique:c1__lens_skeptic__49a218dc  survivable   -> 2 rows, the fatal one still blocking
            new session, same head -> DbError: Database record ... already exists

        Because a `fatal` head has no `answered` escape in the readiness gate, that made every fatal
        verdict permanent and unappealable: the documented cure ("downgraded on the record and then
        answered") had no write path at all. The id is now derived from (conclusion, lens, head)
        alone, and the write is an UPSERT, so the same head is the same row. The change is not silent:
        `verdict_history` on critique is append-only and written by a datastore event
        (groundworks/temporal.py), so a downgrade is on the record with its author and time.
        """
        lens = lens or self.identity.get("lens") or self.identity["role"]
        cid = "%s__%s__%s" % (conclusion, lens, edge_ids.edge_id("critique", "head", conclusion, head)[-8:])
        self.db.sql("UPSERT %s SET lens=%s, head=%s, verdict=%s, text=%s, author=%s, "
                    "answered = IF answered != NONE { answered } ELSE { false }"
                    % (rid("critique", cid), q(lens), q(head), q(verdict), q(text), q(self.author)), expect=1)
        eid = edge_ids.edge_id("write", "attacks", cid, conclusion)
        self.db.sql("RELATE %s->attacks:%s->%s SET author=%s" % (rid("critique", cid), eid, rid("conclusion", conclusion), q(self.author)),
                    expect=1)
        return rid("critique", cid)

    def answer(self, critique_id, text):
        """The answer to a critique. Answering the same critique again REVISES its answer.

        One answer per critique, on a deterministic id: `answer:ans_<critique>`. Until 2026-09-16 this was a
        CREATE, so a second answer to the same critique hard-errored ("record already exists") -- a demo re-run,
        or revising an answer after the head's verdict was downgraded, which is the flow critique() above was
        changed to open (ROADMAP V2-22(f)). It is an UPSERT now, and the `answers` edge id no longer comes from
        the per-session Keyer (which would add a second edge on a repeat in one session). A revision is not
        silent: `text_history` on answer is append-only and written by a datastore event (groundworks/temporal.py),
        naming the author and the time, exactly as a verdict change is."""
        aid = "ans_" + critique_id.split(":", 1)[1].strip("⟨⟩")
        self.db.sql("UPSERT %s SET text=%s, author=%s" % (rid("answer", aid), q(text), q(self.author)), expect=1)
        eid = edge_ids.edge_id("write", "answers", aid, critique_id)
        self.db.sql("RELATE %s->answers:%s->%s; UPDATE %s SET answered=true"
                    % (rid("answer", aid), eid, critique_id, critique_id), expect=2)
        return rid("answer", aid)

    def mark_ready(self, conclusion):
        target = rid("conclusion", conclusion)
        ok, message = self.db.try_sql("UPDATE %s SET ready=true" % target)
        if ok:
            return target
        _raise_if_not_independent(message, target)
        _raise_if_not_about_evidence(self.db, self.inst, message, target)
        problems, counts = readiness_problems(self.db, conclusion, self.inst)
        raise GateRefusal("conclusion_ready", "; ".join(problems) or message, {"counts": counts, "db": message})


def _raise_if_not_independent(message, target):
    """A refusal by an independence rule is about WHO is writing, not about evidence. Explaining it as
    missing citations would send the agent to cite more and try again, which can never succeed."""
    from groundworks import gates            # local: gates imports config, not this module
    rule = gates.independence_rule_in(message)
    if rule:
        reason = gates.independence_reason_in(message)
        if reason == "principal":
            text = ("%s may only be promoted by a credential acting for a different principal than every writer "
                    "of it; another login of the same person or process does not count. Hand it to a reviewer "
                    "provisioned for a different principal" % target)
        elif reason == "own_evidence":
            text = ("%s may only be promoted by a credential that itself created at least one of the evidence "
                    "links the rule counts; check the evidence and link it yourself, then promote" % target)
        else:
            text = ("%s may only be promoted by a signed-in credential that has not written it (not its proposer, "
                    "not root); hand it to another agent to review and promote" % target)
        raise GateRefusal(rule, text, {"db": message})


def _raise_if_not_about_evidence(db, inst, message, target):
    """Two refusals that are not "cite more evidence of the right grade", each recognised by its marker.

    An originals refusal (groundworks/originals.py) means the evidence units are there but their ORIGINALS are
    not verified: citing the same units again, or more units of the same grade, can never pass. An assertion
    refusal (groundworks/assertions.py) means the row would stand promoted while one of its structured
    assertions contradicts the row it is about: no evidence of any grade resolves that, a correction does.
    Explained as a missing grade, either one sends an agent round a retry loop that cannot end."""
    from groundworks import assertions, originals, gates     # local: those import config, not this module
    text = str(message or "")
    found = re.search(re.escape(originals.MARKER) + r" ([A-Za-z0-9_]+):", text)
    if found:
        rule_id = found.group(1)
        rule = next((r for r in inst.gate_rules() if r["id"] == rule_id), None) if inst is not None else None
        linked = []
        for _, clause in (originals.enforced_clauses(rule, inst) if rule else []):
            path, _ = gates._edge_path(rule["table"], clause, inst)
            rows = db.sql("SELECT record::id(id) AS unit, document, document.integrity AS integrity FROM %s%s"
                          % (target, path), expect=1)[0] or []
            for row in rows:
                state = ("NO original" if row.get("document") is None
                         else "original %s" % row["integrity"] if row.get("integrity")
                         else "original not readable under this credential")
                linked.append("%s (%s)" % (row["unit"], state))
        raise GateRefusal(rule_id, (
            "%s is refused by rule %s because its evidence units lack a verified original: a unit counts toward a "
            "promotion only when the exact bytes it was extracted from are in the store and verified "
            "(document.integrity = 'ok'); a unit with no original, or a missing, corrupt or never-captured one, "
            "does not count. Linked now: %s. Citing the same units again, or more units of the same grade, will "
            "not help: restore the original to the store and run `python -m groundworks.originals verify "
            "INSTANCE.yaml`, or cite units whose original is verified"
            % (target, rule_id, ", ".join(linked) or "no units reachable by this rule's clauses")),
            {"db": text, "reason": "originals", "units": linked})
    if assertions.MARKER in text:
        detail = text.split(assertions.MARKER, 1)[1].lstrip(": ").strip()
        raise GateRefusal("assertions", (
            "%s is refused because it would stand promoted while a structured assertion contradicts the row it is "
            "about (%s). This is not missing evidence: no citation of any grade resolves it. Either the assertion "
            "is stale or its target is wrong, and a human decides which. The way through: re-state or retract the "
            "assertion, or have the target corrected, then promote again (docs/PATTERNS.md, Assertions)"
            % (target, detail[:300] or "the datastore named no row")),
            {"db": text, "reason": "assertion"})


# The two edges whose refusal this module words by hand. Every other requires clause on readiness --
# including one on an edge the instance declared -- is described from the clause itself, so a new
# clause is never left explaining itself with the datastore's raw ASSERT text.
DESCRIBED_EDGES = ("supports", "attacks")


def readiness_problems(db, conclusion, inst=None):
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
    problems += _other_clause_problems(db, target, inst)
    return problems, counts


def _other_clause_problems(db, target, inst):
    """Every readiness clause this module has no hand-written words for, asked of the datastore."""
    if inst is None:
        return []
    from groundworks import gates            # local: gates imports config, not this module
    out = []
    for rule in inst.gate_rules():
        if not (rule.get("table") == "conclusion" and rule.get("field") == "ready" and rule.get("value") is True):
            continue
        for clause in rule.get("requires") or []:
            if clause.get("edge") in DESCRIBED_EDGES:
                continue
            need = int(clause.get("min", 1))
            have = db.sql("RETURN " + gates._traversal("conclusion", clause, inst, subject=target), expect=1)[0]
            if have < need:
                out.append("%d of %d `%s` edge(s)%s (rule %s)"
                           % (have, need, clause["edge"],
                              " where %s" % gates._expand(clause.get("where"), inst) if clause.get("where") else "",
                              rule["id"]))
    return out
