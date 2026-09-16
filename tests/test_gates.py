# -*- coding: utf-8 -*-
"""Tests for gates.py: generation is checked in pure Python; enforcement is checked against a
throwaway in-memory SurrealDB (never a live one), including proof that the probes catch a
weakened gate.

    python -m unittest discover -s engine -p "test_gates.py" -v
"""
import pathlib
import subprocess
import sys
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

    def test_raw_write_is_refused_not_just_helper_calls(self):
        conn = self.fresh("raw")
        conn.sql("CREATE fact:zz_raw SET statement='s', status='proposed'")
        ok, message = conn.try_sql("UPDATE fact:zz_raw SET status = 'confirmed'")
        self.assertFalse(ok)
        self.assertIn("status", message.lower())
        conn.sql("DELETE fact:zz_raw")


if __name__ == "__main__":
    unittest.main()
