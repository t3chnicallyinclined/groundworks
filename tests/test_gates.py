# -*- coding: utf-8 -*-
"""Tests for gates.py: generation is checked in pure Python; enforcement is checked against a
throwaway in-memory SurrealDB (never a live one), including proof that the probes catch a
weakened gate.

    python -m unittest discover -s engine -p "test_gates.py" -v
"""
import contextlib
import io
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar import gates  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
from xscholar import surreal_bin  # noqa: E402
SURREAL = surreal_bin.find()
PORT = 8023


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.inst = config.load(DEMO)

    def test_gate_text_expands_the_config_vocabularies(self):
        text = gates.gate_surql(self.inst)
        self.assertIn("'original_log', 'exported_metric'", text)
        self.assertIn("'in_window'", text)
        self.assertNotIn("$confirming", text)
        self.assertIn("$this->cites->unit", text)          # fact is the source of cites
        self.assertIn("$this<-supports<-fact", text)       # conclusion is the target of supports
        self.assertIn("verdict = 'fatal') = 0", text)      # forbids clause

    def test_every_rule_gets_a_reject_and_an_accept_probe(self):
        for probe in gates.probes(self.inst):
            self.assertTrue(probe["attempt"])
            self.assertTrue(probe["fix"], probe["rule"])
            self.assertTrue(probe["cleanup"])

    def test_unknown_edge_and_wrong_direction_are_refused(self):
        rule = {"id": "x", "table": "fact", "field": "status", "value": "confirmed",
                "requires": [{"edge": "nope", "min": 1}]}
        with self.assertRaises(gates.GateError):
            gates.gate_expression(rule, self.inst)
        rule["requires"] = [{"edge": "attacks", "min": 1}]  # attacks joins critique and conclusion, not fact
        with self.assertRaises(gates.GateError):
            gates.gate_expression(rule, self.inst)


class ServerTests(unittest.TestCase):
    """Real enforcement on a throwaway server."""

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
        cls.inst = config.load(DEMO)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def fresh(self, name):
        conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t", db=name)
        conn.script(gates.schema_surql(self.inst))
        conn.script(gates.gate_surql(self.inst))
        return conn

    def test_all_probes_behave_on_generated_gates(self):
        conn = self.fresh("good")
        report = gates.run_probes(self.inst, conn)
        self.assertEqual(report["misbehaved"], 0, report)
        self.assertEqual(report["rejected_ok"], len(self.inst.gate_rules()))
        self.assertEqual(report["accepted_ok"], len(self.inst.gate_rules()))
        for table in ("unit", "fact", "conclusion", "critique"):
            self.assertEqual(conn.count(table), 0, "probe rows left behind in %s" % table)

    def test_probes_catch_a_weakened_gate(self):
        conn = self.fresh("weak")
        # loosen: confirmed no longer needs any citation (what a careless edit would do)
        conn.sql("DEFINE FIELD OVERWRITE status ON fact TYPE string DEFAULT 'proposed' "
                 "ASSERT $value IN %s;" % gates._lit(gates.FACT_STATUSES))
        report = gates.run_probes(self.inst, conn)
        bad = [r for r in report["rules"] if r["rule"] == "confirmed_needs_source_grade"][0]
        self.assertFalse(bad["reject"], "the weakened gate accepted a write it should refuse")
        self.assertFalse(bad["behaved"])
        self.assertGreaterEqual(report["misbehaved"], 1)

    def test_deleting_a_where_clause_is_caught_end_to_end(self):
        """The defect as measured elsewhere: delete the evidence constraint from the config and the
        run still said misbehaved 0, because the config no longer asks for the sharper probe."""
        data = json.loads(json.dumps(self.inst.data))
        for rule in data["gates"]:
            for clause in rule.get("requires") or []:
                clause.pop("where", None)
        weak = config.load(data)

        conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t", db="loosened")
        conn.script(gates.schema_surql(weak))
        conn.script(gates.gate_surql(weak))

        # 1. Its own probes still pass: this is the blind spot, and it is why the baseline exists.
        own = gates.run_probes(weak, conn)
        self.assertEqual(own["misbehaved"], 0, "reproduces the blind spot: a gate can weaken and stay green")

        # 2. The strong instance's probes prove the datastore now accepts evidence it should refuse.
        strict = gates.run_probes(self.inst, conn)
        self.assertGreaterEqual(strict["misbehaved"], 1)
        loose = [r for r in strict["rules"] if r["wrong_evidence_reject"] is False]
        self.assertTrue(loose, "the loosened datastore accepted a fact on the wrong kind of evidence")

        # 3. The baseline catches it from the config alone, with no database at all.
        baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst), "report": own}
        failures = gates.compare_baseline(baseline, own, gates.spec(weak))
        self.assertTrue(any("lost a requires clause" in f for f in failures), failures)

    # ---- the command line, which is what CI actually runs -------------------------------------

    def cli(self, *extra, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = gates.main([str(DEMO), "--apply", "--url", "http://127.0.0.1:%d/sql" % PORT,
                             "--ns", "t", "--db", kw.get("db", "cli")] + list(extra))
        return rc, out.getvalue()

    def test_the_cli_exits_2_and_says_not_executed_when_the_baseline_is_missing(self):
        """A missing baseline must never read as "nothing to compare, carry on"."""
        missing = pathlib.Path(ROOT / "instances" / "demo-incident" / "no_such_baseline.json")
        self.assertFalse(missing.exists())
        rc, text = self.cli("--baseline", str(missing), db="cli_missing")
        self.assertEqual(rc, 2, text)
        self.assertIn("NOT EXECUTED", text)

    def test_the_cli_exits_0_against_the_committed_baseline(self):
        """The exact comparison CI runs; if this cannot pass, the mechanism is not usable."""
        rc, text = self.cli("--baseline", str(ROOT / "instances" / "demo-incident" / "gate_baseline.json"),
                            db="cli_ok")
        self.assertEqual(rc, 0, text)
        self.assertIn("BASELINE: unchanged or stronger", text)
        self.assertNotIn("WEAKENED", text)

    def test_the_cli_exits_1_and_names_every_weakening(self):
        baseline = json.loads((ROOT / "instances" / "demo-incident" / "gate_baseline.json")
                              .read_text(encoding="utf-8"))
        baseline["spec"]["rules"]["a_rule_someone_deleted"] = {
            "table": "fact", "field": "status", "value": "confirmed",
            "clauses": [{"kind": "requires", "edge": "cites", "min": 1, "where": None}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "gate_baseline.json"
            path.write_text(json.dumps(baseline), encoding="utf-8")
            rc, text = self.cli("--baseline", str(path), db="cli_weak")
        self.assertEqual(rc, 1, text)
        self.assertIn("WEAKENED: rule a_rule_someone_deleted is in the baseline and gone from the config", text)
        self.assertIn("BASELINE: FAILED", text)

    def test_raw_write_is_refused_not_just_helper_calls(self):
        conn = self.fresh("raw")
        conn.sql("CREATE fact:zz_raw SET statement='s', status='proposed'")
        ok, message = conn.try_sql("UPDATE fact:zz_raw SET status = 'confirmed'")
        self.assertFalse(ok)
        self.assertIn("status", message.lower())
        conn.sql("DELETE fact:zz_raw")


class LooseningTests(unittest.TestCase):
    """A weakened rule must not report green. Measured on a second instance: deleting a `where`
    silently stopped the sharper probe being generated, and the suite still said misbehaved 0."""

    def setUp(self):
        self.inst = config.load(DEMO)

    def weakened(self):
        """The demo config with the evidence constraint deleted from the promotion rule."""
        data = json.loads(json.dumps(self.inst.data))
        for rule in data["gates"]:
            for clause in rule.get("requires") or []:
                clause.pop("where", None)
        return config.load(data)

    def test_a_constrained_rule_without_its_sharper_probe_is_misbehaviour(self):
        strong = [p for p in gates.probes(self.inst) if p["constrained"]]
        self.assertTrue(strong, "the demo's promotion rule constrains its evidence")
        self.assertTrue(all(p["wrong"] for p in strong))
        entry = {"rule": "r", "reject": True, "accept": True, "wrong_evidence_reject": None}
        probe = {"constrained": True, "constraints": ["grade IN ['x']"]}
        self.assertFalse(gates.judge(entry, probe),
                         "a constrained rule with no wrong-evidence result must not count as behaved")
        self.assertIn("grade IN ['x']", entry["missing_probe"])
        # the same result on an unconstrained rule is genuinely not applicable
        entry = {"rule": "r", "reject": True, "accept": True, "wrong_evidence_reject": None}
        self.assertTrue(gates.judge(entry, {"constrained": False, "constraints": []}))

    def test_the_baseline_catches_a_deleted_where_clause(self):
        report = {"rules": [{"rule": r["id"], "reject": True, "accept": True, "wrong_evidence_reject": True}
                            for r in self.inst.gate_rules()]}
        baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst), "report": report}
        self.assertEqual(gates.compare_baseline(baseline, report, gates.spec(self.inst)), [])

        weak = self.weakened()
        weak_report = {"rules": [{"rule": r["id"], "reject": True, "accept": True, "wrong_evidence_reject": None}
                                 for r in weak.gate_rules()]}
        failures = gates.compare_baseline(baseline, weak_report, gates.spec(weak))
        self.assertTrue(any("lost a requires clause" in f for f in failures), failures)
        self.assertTrue(any("wrong_evidence_reject was true in the baseline, now None" in f for f in failures), failures)

    def test_the_baseline_catches_a_removed_rule_and_a_lowered_minimum(self):
        baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst), "report": {"rules": []}}
        data = json.loads(json.dumps(self.inst.data))
        dropped = data["gates"].pop()["id"]
        failures = gates.compare_baseline(baseline, {"rules": []}, gates.spec(config.load(data)))
        self.assertIn("rule %s is in the baseline and gone from the config" % dropped, failures)

        data = json.loads(json.dumps(self.inst.data))
        for clause in data["gates"][1].get("requires") or []:
            clause["min"] = 0
        failures = gates.compare_baseline(baseline, {"rules": []}, gates.spec(config.load(data)))
        self.assertTrue(any("lowered min" in f for f in failures), failures)

    def test_adding_a_rule_or_a_clause_is_not_a_failure(self):
        baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst), "report": {"rules": []}}
        data = json.loads(json.dumps(self.inst.data))
        data["gates"].append({"id": "extra_rule", "table": "fact", "field": "status", "value": "confirmed",
                              "requires": [{"edge": "cites", "to": ["unit"], "min": 2}]})
        self.assertEqual(gates.compare_baseline(baseline, {"rules": []}, gates.spec(config.load(data))), [])


class BaselineBlindSpotTests(unittest.TestCase):
    """Ways a gate gets weaker that the clause-by-clause diff did not look at.

    Every case here was measured against the shipped compare_baseline before it was written: the
    first four returned [] -- "unchanged or stronger" -- on a config that no longer gates the write
    the baseline was taken for.
    """

    def setUp(self):
        self.inst = config.load(DEMO)
        self.baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst), "report": {"rules": []}}

    def mutated(self, change):
        data = json.loads(json.dumps(self.inst.data))
        change(data)
        return gates.spec(config.load(data))

    def failures(self, change):
        return gates.compare_baseline(self.baseline, None, self.mutated(change))

    def test_pointing_a_rule_at_another_value_is_a_weakening(self):
        """The clauses are untouched; they just no longer apply to the promotion they were written
        for. `confirmed` becomes reachable with no citation at all."""
        def change(data):
            data["gates"][0]["value"] = "ruled_out"
        failures = self.failures(change)
        self.assertTrue(any("value" in f and "confirmed_needs_source_grade" in f for f in failures), failures)

    def test_moving_a_rule_to_another_field_or_table_is_a_weakening(self):
        def field(data):
            data["gates"][0]["field"] = "note"
        self.assertTrue(any("field" in f for f in self.failures(field)), "a rule moved to another field")

        def table(data):
            data["gates"][0].update({"table": "authority", "field": "verified", "value": True})
        failures = self.failures(table)
        self.assertTrue(any("table" in f for f in failures), failures)

    def test_a_baseline_that_pins_nothing_is_refused_not_read_as_unchanged(self):
        """An empty or truncated baseline agrees with every config, including one with no gates."""
        for empty in ({}, {"spec": {}}, {"spec": {"rules": {}}}, {"instance": "x", "report": {"rules": []}}):
            with self.subTest(baseline=empty):
                failures = gates.compare_baseline(empty, None, gates.spec(self.inst))
                self.assertTrue(failures, "an empty baseline was read as unchanged or stronger")
                self.assertIn("pins no rules", failures[0])

    def test_two_clauses_that_read_the_same_are_two_requirements(self):
        """Keyed by (kind, edge, where), a duplicate pair collapses to one and dropping one of them
        is invisible. Two `supports` clauses mean two supporting facts, not one."""
        def twice(data):
            data["gates"][1]["requires"] = [{"edge": "supports", "to": ["fact"], "min": 1},
                                            {"edge": "supports", "to": ["fact"], "min": 1},
                                            {"edge": "attacks", "min": 1}]
        baseline = {"instance": self.inst.id, "spec": self.mutated(twice), "report": {"rules": []}}

        def once(data):
            data["gates"][1]["requires"] = [{"edge": "supports", "to": ["fact"], "min": 1},
                                            {"edge": "attacks", "min": 1}]
        failures = gates.compare_baseline(baseline, None, self.mutated(once))
        self.assertTrue(any("lost a requires clause on supports" in f for f in failures), failures)

    def test_a_renamed_rule_is_reported_even_though_an_identical_rule_arrived(self):
        """Renaming is how a weakening hides: the id in the baseline is gone, so say so, and let a
        human re-write the baseline rather than guess which new rule replaced it."""
        def rename(data):
            data["gates"][0]["id"] = "confirmed_needs_source_grade_v2"
        failures = self.failures(rename)
        self.assertEqual(failures, ["rule confirmed_needs_source_grade is in the baseline and gone from the config"])

    def test_a_requires_turned_into_a_forbids_is_not_the_same_clause(self):
        def to_requires(data):
            rule = data["gates"][1]
            rule["requires"].append(rule["forbids"].pop(0))
        self.assertTrue(any("lost a forbids clause" in f for f in self.failures(to_requires)))

        def to_forbids(data):
            rule = data["gates"][1]
            rule["forbids"].append(rule["requires"].pop(0))
        self.assertTrue(any("lost a requires clause" in f for f in self.failures(to_forbids)))

    def test_an_equivalent_rewrite_of_a_where_clause_still_fails_closed(self):
        """Reordering a conjunction changes nothing about what the gate enforces -- and it still
        fails. Nothing here can prove two SurrealQL predicates equivalent, so a rewrite asks for the
        baseline to be re-written and reviewed. Making this case pass quietly is how a real change
        gets through wearing the clothes of a reformat."""
        def rewrite(data):
            data["gates"][0]["requires"][0]["where"] = "status IN $usable AND grade IN $confirming"
        self.assertTrue(any("lost a requires clause on cites" in f for f in self.failures(rewrite)))

    def test_raising_one_minimum_does_not_hide_dropping_another_clause(self):
        def change(data):
            rule = data["gates"][1]
            rule["requires"][0]["min"] = 5
            rule["requires"].pop(1)
        failures = self.failures(change)
        self.assertTrue(any("lost a requires clause on attacks" in f for f in failures), failures)

    def test_lowering_one_minimum_is_caught_while_another_rises(self):
        def baseline_two(data):
            data["gates"][1]["requires"] = [{"edge": "supports", "to": ["fact"], "min": 3},
                                            {"edge": "attacks", "min": 1}]
        baseline = {"instance": self.inst.id, "spec": self.mutated(baseline_two), "report": {"rules": []}}

        def swapped(data):
            data["gates"][1]["requires"] = [{"edge": "supports", "to": ["fact"], "min": 1},
                                            {"edge": "attacks", "min": 3}]
        failures = gates.compare_baseline(baseline, None, self.mutated(swapped))
        self.assertEqual(failures, ["rule root_cause_needs_support_and_attack lowered min on supports from 3 to 1"])

    def test_a_probe_flag_that_went_from_true_to_false_is_caught(self):
        rules = [r["id"] for r in self.inst.gate_rules()]
        was = {"rules": [{"rule": r, "reject": True, "accept": True, "wrong_evidence_reject": True} for r in rules]}
        baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst), "report": was}
        now = {"rules": [{"rule": r, "reject": False, "accept": True, "wrong_evidence_reject": False} for r in rules]}
        failures = gates.compare_baseline(baseline, now, gates.spec(self.inst))
        self.assertTrue(any("reject was true in the baseline, now False" in f for f in failures), failures)
        self.assertTrue(any("wrong_evidence_reject was true in the baseline, now False" in f for f in failures), failures)

    def test_a_rule_that_stopped_producing_a_probe_result_is_caught(self):
        """Reading only the entries that arrived cannot notice the one that did not."""
        rules = [r["id"] for r in self.inst.gate_rules()]
        was = {"rules": [{"rule": r, "reject": True, "accept": True, "wrong_evidence_reject": True} for r in rules]}
        baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst), "report": was}
        failures = gates.compare_baseline(baseline, {"rules": []}, gates.spec(self.inst))
        self.assertEqual(len(failures), len(rules), failures)
        self.assertTrue(all("no probe result" in f for f in failures), failures)

    def test_the_spec_half_still_works_with_no_report_at_all(self):
        self.assertEqual(gates.compare_baseline(self.baseline, None, gates.spec(self.inst)), [])


class SpecStabilityTests(unittest.TestCase):
    """An innocent reshuffle of the config must not read as a change, and sorting must not hide one."""

    def setUp(self):
        self.inst = config.load(DEMO)

    def reshuffled(self):
        data = json.loads(json.dumps(self.inst.data))
        data["gates"].reverse()
        for rule in data["gates"]:
            for kind in ("requires", "forbids"):
                if rule.get(kind):
                    rule[kind].reverse()
        return config.load(data)

    def test_reordering_rules_and_clauses_is_not_a_change(self):
        self.assertEqual(gates.spec(self.reshuffled()), gates.spec(self.inst))
        baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst), "report": {"rules": []}}
        self.assertEqual(gates.compare_baseline(baseline, None, gates.spec(self.reshuffled())), [])

    def test_the_sort_is_by_what_the_clause_says_not_by_how_strong_it_is(self):
        """Sorting on min would let two clauses swap strengths without moving in the list."""
        clauses = gates.spec(self.inst)["rules"]["root_cause_needs_support_and_attack"]["clauses"]
        self.assertEqual([(c["kind"], c["edge"]) for c in clauses],
                         sorted((c["kind"], c["edge"]) for c in clauses))


class ProbeShapeTests(unittest.TestCase):
    """A probe that runs and tests nothing is the failure mode these methods exist to catch."""

    def setUp(self):
        self.inst = config.load(DEMO)

    def test_the_wrong_evidence_probe_writes_evidence_that_really_is_wrong(self):
        """`wrong` must cite the row built to be unusable. Citing the good row would make the sharper
        probe a second copy of the accept probe: it would run, report a flag, and test nothing."""
        confirming, usable = self.inst.confirming_grades(), self.inst.usable_statuses()
        checked = 0
        for rule, probe in zip(self.inst.gate_rules(), gates.probes(self.inst)):
            if not probe["constrained"]:
                continue
            checked += 1
            ids = gates._probe_ids(rule)
            self.assertTrue(probe["wrong"], probe["rule"])
            for stmt in probe["wrong"]:
                self.assertIn(ids["bad"], stmt)
                self.assertNotIn(ids["ok"], stmt)
            creation = [s for s in probe["setup"] if s.startswith("CREATE %s " % ids["bad"])]
            self.assertEqual(len(creation), 1, probe["setup"])
            grade = re.search(r"grade='([^']*)'", creation[0]).group(1)
            status = re.search(r"status='([^']*)'", creation[0]).group(1)
            self.assertNotIn(grade, confirming, "the wrong-evidence row carries a confirming grade")
            self.assertNotIn(status, usable, "the wrong-evidence row carries a usable status")
        self.assertTrue(checked, "the demo's promotion rule constrains its evidence")

    def test_a_constraint_on_an_edge_with_no_sharper_probe_is_reported_untested(self):
        """Only `cites` clauses get a wrong-evidence probe. A constraint anywhere else must be
        reported as untested, not silently treated as not applicable."""
        data = json.loads(json.dumps(self.inst.data))
        for clause in data["gates"][1]["requires"]:
            if clause["edge"] == "supports":
                clause["where"] = "status = 'confirmed'"
        moved = config.load(data)
        probe = [p for p in gates.probes(moved) if p["rule"] == "root_cause_needs_support_and_attack"][0]
        self.assertTrue(probe["constrained"], "a requires clause with a where constrains the evidence")
        self.assertEqual(probe["wrong"], [], "no wrong-evidence probe exists for a non-cites edge")
        entry = {"rule": probe["rule"], "reject": True, "accept": True, "wrong_evidence_reject": None}
        self.assertFalse(gates.judge(entry, probe))
        self.assertIn("status = 'confirmed'", entry["missing_probe"])


class JudgeTests(unittest.TestCase):
    """judge() is the shipped decision: every branch of it says whether a run may report green."""

    def entry(self, **kw):
        base = {"rule": "r", "reject": True, "accept": True, "wrong_evidence_reject": None}
        base.update(kw)
        return base

    def test_wrong_evidence_that_was_accepted_is_never_behaviour(self):
        entry = self.entry(wrong_evidence_reject=False)
        self.assertFalse(gates.judge(entry, {"constrained": True, "constraints": ["grade IN ['x']"]}),
                         "the datastore took a fact on evidence the rule excludes")
        self.assertFalse(entry["behaved"])

    def test_a_rule_that_only_rejects_or_only_accepts_is_not_behaviour(self):
        probe = {"constrained": False, "constraints": []}
        self.assertFalse(gates.judge(self.entry(reject=False), probe), "a gate that refuses nothing")
        self.assertFalse(gates.judge(self.entry(accept=False), probe), "a gate that permits nothing")
        self.assertFalse(gates.judge(self.entry(reject=None, accept=None), probe), "a probe that did not run")

    def test_judge_records_what_it_judged_against(self):
        """The constraints are what the baseline pins; an entry that does not carry them lets the
        next run compare against nothing."""
        entry = self.entry(wrong_evidence_reject=True)
        probe = {"constrained": True, "constraints": ["grade IN ['a']", "status IN ['b']"]}
        self.assertTrue(gates.judge(entry, probe))
        self.assertTrue(entry["constrained"])
        self.assertEqual(entry["constraints"], ["grade IN ['a']", "status IN ['b']"])


class CliBaselineTests(unittest.TestCase):
    """The flags, without a database: the one refusal that has to happen before anything runs."""

    def test_writing_and_comparing_the_same_file_in_one_run_is_refused(self):
        """Otherwise the run grades itself against what it has just written and passes whatever the
        gates now say -- the baseline mechanism, defeated by one command line."""
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "gate_baseline.json"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = gates.main([str(DEMO), "--apply", "--url", "http://127.0.0.1:1/sql",
                                 "--write-baseline", str(path), "--baseline", str(path)])
            self.assertEqual(rc, 2, out.getvalue())
            self.assertIn("NOT EXECUTED", out.getvalue())
            self.assertFalse(path.exists(), "nothing was written, and nothing reached a database")


if __name__ == "__main__":   # last, so running this file directly runs every class in it
    unittest.main()
