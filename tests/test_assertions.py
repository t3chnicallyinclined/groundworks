# -*- coding: utf-8 -*-
"""A graph that contradicts itself must say so: structured assertions (xscholar/assertions.py).

From beta testing on a real instance, in neutral words: a unit's status was an UNUSABLE value while a
PROMOTED fact in the same graph said, in its text, that the unit was usable. A second, ruled-out fact said
the same. The fact was right, for weeks, and nothing detected the contradiction, because comparing a
status with what a text says means reading the text. The fix makes the claim structured: an assertion
edge from the fact to the unit carrying `field` and `value`. Everything here uses the demo instance's
vocabulary with an assertion edge added in the test (the demo itself is not edited), on a throwaway
in-memory SurrealDB.

    python -m unittest discover -s tests -p "test_assertions.py" -v
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from xscholar import assertions  # noqa: E402
from xscholar import config  # noqa: E402
from xscholar import consistency  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar import gates  # noqa: E402
from xscholar import surreal_bin  # noqa: E402
from xscholar import temporal  # noqa: E402
from xscholar.pipeline import loader  # noqa: E402
from xscholar.pipeline import write  # noqa: E402

PORT = 8038
ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
SURREAL = surreal_bin.find()
EDGE = "asserts_state"

# Demo vocabulary: status usable = in_window; confirming grades include original_log.
BASE = {"u1": ("redacted", "original_log"), "u2": ("in_window", "original_log"), "u3": ("in_window", "original_log")}


def demo_data():
    return config.parse_yaml(DEMO.read_text(encoding="utf-8"))


def with_assertions(enforce=None, fields=("status", "grade"), **spec):
    """The demo config plus one assertion edge from fact to unit. Built here; the demo file is not touched."""
    data = demo_data()
    data["edges"][EDGE] = {"from": "fact", "to": "unit"}
    body = {"fields": list(fields)}
    if enforce is not None:
        body["enforce"] = enforce
    body.update(spec)
    data["assertions"] = {EDGE: body}
    data["human_owned"] = list(data["human_owned"]) + ["assertions"]
    return data


def problems_of(data):
    try:
        config.validate(data)
    except config.ConfigError as exc:
        return str(exc)
    return ""


class ContractTests(unittest.TestCase):
    """The config contract and the legality check, no datastore."""

    def test_a_declared_assertion_edge_loads_and_counts_rows_by_the_gate_rules_on_its_source_table(self):
        inst = config.load(with_assertions())
        spec = assertions.declared(inst)[EDGE]
        self.assertEqual((spec["from"], spec["to"], spec["fields"], spec["enforce"]), ("fact", "unit", ["status", "grade"], False))
        self.assertEqual([r["id"] for r in spec["rules"]],
                         [r["id"] for r in inst.gate_rules() if r["table"] == "fact"])
        self.assertTrue(spec["rules"], "no promoting rule: nothing could ever count")

    def test_every_refusal_names_what_is_wrong(self):
        cases = []
        d = with_assertions(fields=("status", "no_such_field"))
        cases.append((d, "'no_such_field' is not a field of unit"))
        d = with_assertions(fields=("text",))
        cases.append((d, "unit.text has no enumerated values"))
        d = with_assertions(promoted_by=["root_cause_needs_support_and_attack"])
        cases.append((d, "promoted_by names root_cause_needs_support_and_attack, not a gate rule on fact"))
        d = with_assertions()
        d["edges"][EDGE] = {"from": "answer", "to": "unit"}
        cases.append((d, "no gate rule promotes a answer row"))
        d = with_assertions()
        d["assertions"] = {"cites": {"fields": ["status"]}}
        cases.append((d, "'cites' is a built-in edge"))
        d = with_assertions()
        d["assertions"] = {"not_declared": {"fields": ["status"]}}
        cases.append((d, "no edge 'not_declared'"))
        d = with_assertions()
        d["edges"][EDGE]["fields"] = {"value": "string"}
        cases.append((d, "edges.asserts_state declares value, which the engine owns"))
        d = with_assertions(enforce="yes please")
        cases.append((d, "assertions.asserts_state: enforce must be true or false"))
        d = with_assertions()
        d["human_owned"].remove("assertions")
        cases.append((d, "human_owned must include assertions"))
        d = with_assertions(extra=1)
        cases.append((d, "unknown key(s) extra"))
        for data, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, problems_of(data))

    def test_an_assertion_about_a_declared_tables_enumerated_field_is_accepted_and_a_free_one_is_refused(self):
        data = with_assertions()
        data["tables"] = {"item": {"fields": {"state": {"type": "string", "one_of": ["usable", "unusable", "unknown"]},
                                              "note": "string", "flag": "bool"}}}
        data["edges"]["item_claim"] = {"from": "fact", "to": "item"}
        data["assertions"]["item_claim"] = {"fields": ["state", "flag"]}
        self.assertEqual(problems_of(data), "")
        data["assertions"]["item_claim"] = {"fields": ["note"]}
        self.assertIn("item.note has no enumerated values", problems_of(data))

    def test_check_refuses_an_illegal_field_or_value_by_name_and_accepts_a_legal_one(self):
        inst = config.load(with_assertions(fields=("status",)))
        assertions.check(inst, EDGE, "status", "in_window")
        with self.assertRaises(assertions.AssertionContractError) as raised:
            assertions.check(inst, EDGE, "status", "usable-ish")
        self.assertIn("unit.status cannot hold 'usable-ish'", str(raised.exception))
        with self.assertRaises(assertions.AssertionContractError) as raised:
            assertions.check(inst, EDGE, "grade", "original_log")
        self.assertIn("asserts_state may assert status of unit, not 'grade'", str(raised.exception))
        with self.assertRaises(assertions.AssertionContractError):
            assertions.check(inst, "cites", "status", "in_window")

    def test_field_domains_match_the_gate_generators_for_every_field(self):
        data = with_assertions()
        data["tables"] = {"item": {"fields": {"state": {"type": "string", "one_of": ["a", "b"]}, "note": "string",
                                              "flag": "bool", "rank": {"type": "int", "one_of": [1, 2]}}}}
        inst = config.load(data)
        tables = dict(config.BUILT_IN_FIELDS, item={"state": 1, "note": 1, "flag": 1, "rank": 1})
        for table, fields in tables.items():
            for field in fields:
                with self.subTest(table=table, field=field):
                    mine = assertions.field_domain(inst.data, table, field)
                    theirs = gates._field_domain(table, field, inst)
                    self.assertEqual(None if mine is None else [gates._value_literal(v) for v in mine], theirs)

    def test_no_instance_vocabulary_word_appears_in_the_engine_module(self):
        """Modularity rule: which rows count comes from the gate rules, never from a named status."""
        source = pathlib.Path(assertions.__file__).read_text(encoding="utf-8")
        inst = config.load(DEMO)
        words = set(gates.FACT_STATUSES) - {"open", "stale", "superseded"}   # plain English besides
        words |= set(inst.status_values()) | set(inst.grades())
        found = sorted(w for w in words if re.search(r"\b%s\b" % re.escape(w), source))
        self.assertEqual(found, [], "domain words in xscholar/assertions.py")

    def test_tracked_history_is_emitted_exactly_as_temporal_emits_it(self):
        """Both modules may be applied in either order only because they emit the same statements."""
        inst = config.load(with_assertions())
        mine = assertions.surql(inst)
        theirs = temporal.temporal_surql(inst)
        for name in ("unit.status", "unit.grade", "fact"):
            block = "\n".join(temporal._history_block(name, temporal.TRACKED[name]))
            with self.subTest(tracked=name):
                self.assertIn(block, theirs)
                self.assertIn(block, mine)

    def test_an_instance_without_assertions_is_unchanged(self):
        inst = config.load(DEMO)
        self.assertEqual(assertions.declared(inst), {})
        self.assertEqual(assertions.surql(inst).strip().splitlines()[1:], [])


class _Server(unittest.TestCase):
    ENFORCE = None

    @classmethod
    def setUpClass(cls):
        if not SURREAL:
            raise unittest.SkipTest("NOT EXECUTED: " + surreal_bin.MISSING)
        if surreal_bin.port_in_use(PORT):
            raise unittest.SkipTest(surreal_bin.occupied(PORT))
        cls.proc = subprocess.Popen([str(SURREAL), "start", "--bind", "127.0.0.1:%d" % PORT,
                                     "--user", "root", "--pass", "root", "memory"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        else:
            cls.proc.kill()
            raise unittest.SkipTest("NOT EXECUTED: throwaway SurrealDB did not start on %d" % PORT)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def setUp(self):
        self.inst = config.load(with_assertions(enforce=self.ENFORCE))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = pathlib.Path(self.tmp.name)
        (self.work / "labels").mkdir()
        (self.work / "labels" / "c1.jsonl").write_text(
            "".join(json.dumps({"id": uid, "tier": 0}) + "\n" for uid in BASE), encoding="utf-8")
        self.conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="assertions",
                             db=(type(self).__name__ + "_" + self.id().split(".")[-1])[:60])
        self.conn.script(gates.schema_surql(self.inst))
        self.conn.script(gates.gate_surql(self.inst))
        if self.apply_history():
            self.conn.script(assertions.surql(self.inst))
            self.conn.script(temporal.temporal_surql(self.inst))   # after: identical statements, no conflict
        self.writer = write.Writer(self.conn, self.inst, {"agent_id": "tester", "role": "reader", "lens": None})

    def apply_history(self):
        return True

    def source(self, **changes):
        rows = dict(BASE, **changes)
        (self.work / "units.jsonl").write_text("".join(
            json.dumps({"id": uid, "type": "log_line", "text": "line %s" % uid, "locator": {"line": i},
                        "status": st, "grade": gr, "hash": "h%s" % uid}) + "\n"
            for i, (uid, (st, gr)) in enumerate(sorted(rows.items()))), encoding="utf-8")

    def load(self, **kw):
        return loader.load(self.work / "units.jsonl", self.work / "labels", self.conn, self.inst, **kw)

    def claim(self, slug, unit, value, field="status", promote=True):
        """A fact asserting unit.field = value, promoted through its own gate (it cites the usable u2)."""
        self.writer.propose(slug, "a synthetic claim about %s" % unit)
        self.writer.relate(EDGE, slug, unit, field=field, value=value)
        if promote:
            self.writer.confirm(slug, ["u2"])

    def report(self):
        return assertions.report(self.conn, self.inst)


class ReportTests(_Server):

    def test_the_beta_case_is_reported_then_clean_when_fixed_then_reported_again_when_the_target_moves(self):
        self.source()
        self.load()
        # the target stands at an unusable value; a promoted fact asserts it holds a usable one
        self.claim("f_claim", "u1", "in_window")
        rep = self.report()
        self.assertEqual((rep["assertion_rows"], rep["counted"], rep["agree"]), (1, 1, 0))
        [c] = rep["contradictions"]
        self.assertEqual((c["source"], c["target"], c["field"], c["asserted"], c["current"]),
                         ("fact:f_claim", "unit:u1", "status", "in_window", "redacted"))
        self.assertEqual(c["changed_last"], "assertion", "the target was never changed after the claim was made")
        self.assertEqual(c["assertion_last_change"]["what"], "promoted")
        self.assertIn("unchanged since", c["target_last_change"]["reason"])
        # a human corrects the target: clean
        loader.correct_unit(self.conn, "u1", "status", "in_window", by="reviewer_1")
        rep = self.report()
        self.assertEqual((rep["counted"], rep["agree"], rep["contradictions"]), (1, 1, []))
        # the target moves again: the assertion is now the older side, and the history says who moved it
        loader.correct_unit(self.conn, "u1", "status", "out_of_window", by="reviewer_2")
        [c] = self.report()["contradictions"]
        self.assertEqual((c["asserted"], c["current"], c["changed_last"]), ("in_window", "out_of_window", "target"))
        self.assertEqual({k: c["target_last_change"][k] for k in ("before", "after", "by")},
                         {"before": "in_window", "after": "out_of_window", "by": "reviewer_2"})

    def test_an_assertion_from_a_row_that_passed_no_gate_is_not_counted(self):
        self.source()
        self.load()
        self.claim("f_proposed", "u1", "in_window", promote=False)
        self.writer.rule_out("f_ruled", "a synthetic claim about u1", tried="t", evidence="e")
        self.writer.relate(EDGE, "f_ruled", "u1", field="status", value="in_window")
        rep = self.report()
        self.assertEqual((rep["assertion_rows"], rep["counted"], rep["contradictions"]), (2, 0, []))

    def test_counted_assertions_that_disagree_with_each_other_are_listed(self):
        self.source()
        self.load()
        self.claim("f_a", "u3", "in_window")
        self.claim("f_b", "u3", "out_of_window")
        rep = self.report()
        [among] = rep["disagreeing_assertions"]
        self.assertEqual((among["target"], among["field"], len(among["values"])), ("unit:u3", "status", 2))
        self.assertEqual([c["source"] for c in rep["contradictions"]], ["fact:f_b"])

    def test_the_datastore_refuses_an_illegal_field_or_value_on_a_raw_write(self):
        self.source()
        self.load()
        self.writer.propose("f_raw", "a synthetic claim")
        ok, msg = self.conn.try_sql("RELATE fact:f_raw->%s:e1->unit:u1 SET field='status', value='usable-ish'" % EDGE)
        self.assertFalse(ok, "an illegal value was stored")
        ok, msg = self.conn.try_sql("RELATE fact:f_raw->%s:e2->unit:u1 SET field='text', value='x'" % EDGE)
        self.assertFalse(ok, "an assertion about a field the edge may not assert was stored")
        ok, msg = self.conn.try_sql("RELATE fact:f_raw->%s:e3->unit:u1 SET field='grade', value='recollection'" % EDGE)
        self.assertTrue(ok, msg)

    def test_a_load_that_changes_a_target_names_the_assertions_it_contradicts_and_annotates_conflicts(self):
        self.source()
        self.load()
        self.claim("f_u3", "u3", "in_window")
        self.claim("f_u1", "u1", "in_window")
        loader.correct_unit(self.conn, "u1", "status", "in_window", by="reviewer_1")
        self.source(u3=("out_of_window", "original_log"))           # a real source change on u3
        counts = self.load()
        out = assertions.after_load(self.conn, self.inst, counts)
        self.assertEqual([(c["source"], c["target"], c["current"]) for c in out["caused_by_this_load"]],
                         [("fact:f_u3", "unit:u3", "out_of_window")])
        self.assertTrue(out["caused_by_this_load"][0]["target_last_change"]["by"].startswith("loader|run="))
        [conflict] = out["conflicts"]                                 # u1: held in_window, source redacted
        self.assertEqual((conflict["unit"], [a["agrees_with"] for a in conflict["assertions"]]), ("u1", ["held"]))
        self.assertEqual(assertions.for_target(self.conn, self.inst, "unit:u3", "status")[0]["source"], "fact:f_u3")

    def test_the_consistency_report_carries_assertions_only_when_an_instance_declares_them(self):
        self.source()
        self.load()
        self.claim("f_claim", "u1", "in_window")
        self.assertEqual(len(consistency.report(self.conn, self.inst)["assertions"]["contradictions"]), 1)
        self.assertNotIn("assertions", consistency.report(self.conn, config.load(DEMO)))

    def test_an_instance_that_looks_nothing_like_the_demo_is_checked_the_same_way(self):
        """Declared tables on both ends, a declared gate, no built-in field involved: the history the report
        reads is generated for these fields too, so which side moved is known, not UNKNOWN."""
        data = demo_data()
        data["tables"] = {"item": {"fields": {"state": {"type": "string", "one_of": ["usable", "unusable", "unknown"]}}},
                          "claim": {"fields": {"said": "string",
                                               "standing": {"type": "string", "one_of": ["draft", "accepted", "withdrawn"]}}}}
        data["edges"].update({"backed_by": {"from": "claim", "to": "unit"}, "claim_about": {"from": "claim", "to": "item"}})
        data["gates"] = list(data["gates"]) + [{"id": "claim_accepted_needs_backing", "table": "claim", "field": "standing",
                                                "value": "accepted", "requires": [{"edge": "backed_by", "to": ["unit"],
                                                                                  "where": "status IN $usable", "min": 1}]}]
        data["assertions"] = {"claim_about": {"fields": ["state"]}}
        data["human_owned"] = list(data["human_owned"]) + ["assertions"]
        inst = config.load(data)
        conn = dbmod.Db(url=self.conn.url, ns="assertions", db="declared_only")
        for text in (gates.schema_surql(inst), gates.gate_surql(inst), assertions.surql(inst)):
            conn.script(text)
        conn.sql("CREATE unit:u9 SET type='log_line', text='t', locator={}, status='in_window', grade='original_log', hash='h';"
                 "CREATE item:i1 SET state='unknown'; CREATE claim:c1 SET said='a synthetic claim', standing='draft';"
                 "RELATE claim:c1->backed_by->unit:u9; RELATE claim:c1->claim_about:a1->item:i1 SET field='state', value='usable';")
        self.assertEqual(assertions.report(conn, inst)["counted"], 0, "a draft claim was counted")
        conn.sql("UPDATE claim:c1 SET standing='accepted'")
        [c] = assertions.report(conn, inst)["contradictions"]
        self.assertEqual((c["target"], c["asserted"], c["current"], c["changed_last"]),
                         ("item:i1", "usable", "unknown", "assertion"))
        conn.sql("UPDATE item:i1 SET state='usable'")
        self.assertEqual(assertions.report(conn, inst)["contradictions"], [])
        conn.sql("UPDATE item:i1 SET state='unusable'")
        [c] = assertions.report(conn, inst)["contradictions"]
        self.assertEqual((c["current"], c["changed_last"], c["target_last_change"]["before"]), ("unusable", "target", "usable"))

    def test_mcp_health_counts_a_contradicting_assertion(self):
        from xscholar.mcp import instance_adapter
        self.source()
        self.load()
        adapter = instance_adapter.create_adapter_for(self.inst, self.conn)
        self.assertEqual(adapter.health()["assertions"]["contradictions"], 0)
        self.claim("f_claim", "u1", "in_window")        # u1 stands at an unusable status
        health = adapter.health()["assertions"]
        self.assertEqual((health["counted"], health["contradictions"]), (1, 1), health)
        self.assertEqual(health["contradiction_sample"][0]["target"], "unit:u1")

    def test_report_only_is_the_default_nothing_is_refused(self):
        self.source()
        self.load()
        self.claim("f_claim", "u3", "in_window")
        loader.correct_unit(self.conn, "u3", "status", "redacted", by="reviewer_1")   # would contradict
        self.assertEqual(len(self.report()["contradictions"]), 1)


class NoHistoryTests(_Server):
    """Without assertions.surql applied the report still runs, and says what it does not know."""

    def apply_history(self):
        return False

    def test_a_side_with_no_history_is_reported_unknown_not_guessed(self):
        self.source()
        self.load()
        self.claim("f_claim", "u1", "in_window")
        [c] = self.report()["contradictions"]
        self.assertEqual(c["changed_last"], "unknown")
        self.assertIn("UNKNOWN", c["target_last_change"]["reason"])


class EnforceTests(_Server):
    ENFORCE = True

    def refused(self, statement):
        ok, msg = self.conn.try_sql(statement)
        self.assertFalse(ok, "not refused: %s" % statement)
        self.assertIn(assertions.MARKER, msg)
        return msg

    def test_a_target_change_that_contradicts_a_promoted_assertion_is_refused(self):
        self.source()
        self.load()
        self.claim("f_claim", "u3", "in_window")
        msg = self.refused("UPDATE unit:u3 SET status = 'redacted'")
        self.assertIn("fact:f_claim", msg)
        self.assertEqual(self.conn.sql("SELECT VALUE status FROM unit:u3")[0], ["in_window"])
        ok, msg = self.conn.try_sql("UPDATE unit:u3 SET grade = 'exported_metric'")   # nothing asserts grade
        self.assertTrue(ok, msg)

    def test_promoting_a_row_whose_assertion_disagrees_is_refused_and_agreeing_is_accepted(self):
        self.source()
        self.load()
        self.claim("f_claim", "u1", "in_window", promote=False)
        with self.assertRaises(write.GateRefusal) as raised:
            self.writer.confirm("f_claim", ["u2"])
        self.assertIn(assertions.MARKER, str(raised.exception.detail))
        loader.correct_unit(self.conn, "u1", "status", "in_window", by="reviewer_1")
        self.writer.confirm("f_claim", ["u2"])

    def test_the_writer_explains_an_assertion_refusal_as_a_contradiction_not_a_missing_grade(self):
        """The fact cites a usable, source-grade unit, so a missing-grade explanation is false and sends an agent to
        cite again forever. The refusal must say it is a contradiction a correction resolves."""
        self.source()
        self.load()
        self.claim("f_claim", "u1", "in_window", promote=False)
        with self.assertRaises(write.GateRefusal) as raised:
            self.writer.confirm("f_claim", ["u2"])
        self.assertEqual(raised.exception.rule, "assertions")
        self.assertEqual(raised.exception.detail.get("reason"), "assertion", raised.exception.missing)
        self.assertIn("contradicts", raised.exception.missing)
        self.assertIn("fact:f_claim", raised.exception.missing)
        self.assertNotIn("whose grade is in", raised.exception.missing)

    def test_an_assertion_that_disagrees_cannot_be_added_to_a_promoted_row(self):
        self.source()
        self.load()
        self.writer.propose("f_claim", "a synthetic claim")
        self.writer.confirm("f_claim", ["u2"])
        self.refused("RELATE fact:f_claim->%s:e1->unit:u1 SET field='status', value='in_window'" % EDGE)
        ok, msg = self.conn.try_sql("RELATE fact:f_claim->%s:e2->unit:u1 SET field='status', value='redacted'" % EDGE)
        self.assertTrue(ok, msg)

    def test_the_ordering_deadlock_is_broken_by_demoting_the_asserting_row_first(self):
        """A stale assertion blocks a legitimate correction; the way through is on the record."""
        self.source()
        self.load()
        self.claim("f_claim", "u3", "in_window")
        self.refused("UPDATE unit:u3 SET status = 'out_of_window'")                     # blocked
        self.refused("UPDATE %s SET value = 'out_of_window' WHERE in = fact:f_claim" % EDGE)   # and this way too
        self.conn.sql("UPDATE fact:f_claim SET status = 'stale'")                          # 1. demote
        loader.correct_unit(self.conn, "u3", "status", "out_of_window", by="reviewer_1")  # 2. correct
        self.conn.sql("UPDATE %s SET value = 'out_of_window' WHERE in = fact:f_claim" % EDGE)  # 3. re-state
        self.writer.confirm("f_claim")                                                      # 4. promote again
        self.assertEqual(self.report()["contradictions"], [])
        hist = self.conn.sql("SELECT VALUE status_history FROM fact:f_claim")[0][0]
        self.assertEqual([h["status_after"] for h in hist], ["confirmed", "stale", "confirmed"])

    def test_switching_enforcement_on_over_a_standing_contradiction_blocks_only_writes_that_contradict(self):
        """Enforcement applied to a graph that already contradicts itself: the fix must go through, an
        unrelated write must go through, and only a new contradicting value is refused."""
        report_only = config.load(with_assertions(enforce=False))
        self.conn.script(assertions.surql(report_only))           # switching it off removes the events
        self.source()
        self.load()
        self.claim("f_claim", "u1", "in_window")                  # the beta case, while report-only
        self.conn.script(assertions.surql(self.inst))             # now switch enforcement on
        self.assertEqual(len(self.report()["contradictions"]), 1)
        ok, msg = self.conn.try_sql("UPDATE unit:u1 SET grade = 'exported_metric'")
        self.assertTrue(ok, "an unrelated write was blocked by a standing contradiction: " + msg)
        self.refused("UPDATE unit:u1 SET status = 'out_of_window'")
        ok, msg = self.conn.try_sql("UPDATE unit:u1 SET status = 'in_window'")
        self.assertTrue(ok, "the correction that resolves the contradiction was refused: " + msg)
        self.assertEqual(self.report()["contradictions"], [])

    def test_a_reload_that_would_contradict_stops_loudly(self):
        self.source()
        self.load()
        self.claim("f_claim", "u3", "in_window")
        self.source(u3=("redacted", "original_log"))
        with self.assertRaises(Exception) as raised:
            self.load()
        self.assertIn(assertions.MARKER, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
