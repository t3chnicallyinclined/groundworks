# -*- coding: utf-8 -*-
"""The generic MCP adapter on a throwaway in-memory SurrealDB: every instance.yaml gets the full
tool set with no hand-written adapter. Tested both ways, through the server's envelope too.

    python -m unittest tests.test_instance_adapter -v
"""
import json
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.request

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config, gates, surreal_bin  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar.mcp import adapter as contract, instance_adapter, server  # noqa: E402
from xscholar.pipeline import chunker, intake, loader  # noqa: E402
try:  # discovery puts tests/ on the path; `python -m unittest tests.x` does not
    from test_pipeline import Adapter, CORPUS  # noqa: E402
    from test_pipeline_db import LABELS  # noqa: E402
except ImportError:  # pragma: no cover
    from tests.test_pipeline import Adapter, CORPUS  # noqa: E402
    from tests.test_pipeline_db import LABELS  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DEMO = HERE.parent / "instances" / "demo-incident" / "instance.yaml"
SURREAL = surreal_bin.find()
PORT = 8026
PROV = contract.Provenance("agent_7", "reader", None)
SEAT = contract.Provenance("seat_2", "seat", "lens_skeptic")


class GenericAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SURREAL:
            raise unittest.SkipTest("NOT EXECUTED: " + surreal_bin.MISSING)
        cls.proc = subprocess.Popen([SURREAL, "start", "--bind", "127.0.0.1:%d" % PORT, "--user", "root",
                                     "--pass", "root", "memory"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        else:
            cls.proc.kill()
            raise unittest.SkipTest("NOT EXECUTED: throwaway SurrealDB did not start")
        cls.inst = config.load(DEMO)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def setUp(self):
        type(self).n += 1
        self.db = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="a%d" % int(time.time() * 1000), db="d%d" % self.n)
        self.db.script(gates.schema_surql(self.inst))
        self.db.script(gates.gate_surql(self.inst))
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        intake.write_units(Adapter(CORPUS), tmp, self.inst)
        chunker.chunk(tmp / "units.jsonl", tmp, self.inst)
        (tmp / "labels").mkdir()
        (tmp / "labels" / "log_line_0000.jsonl").write_text("".join(json.dumps(l) + "\n" for l in LABELS), encoding="utf-8")
        loader.load(tmp / "units.jsonl", tmp / "labels", self.db, self.inst)
        self.ad = instance_adapter.create_adapter_for(self.inst, self.db)

    def test_reads(self):
        unit = self.ad.unit_get("log_001")
        self.assertIn("pool exhausted", unit[contract.UNTRUSTED_TEXT_KEY])
        self.assertNotIn("text", unit)
        self.assertTrue(unit["usable"] and unit["confirming"])
        self.assertEqual(unit["labels"][0]["tier"], 3)
        with self.assertRaises(contract.NotFound):
            self.ad.unit_get("log_999")
        by_locator = self.ad.unit_get(json.dumps({"host": "api-1", "line": 2}))
        self.assertEqual(by_locator["id"], "unit:log_002")
        hits = self.ad.unit_search("retrying upstream")
        self.assertEqual(hits["count"], 0)                    # log_004 is out_of_window
        hits = self.ad.unit_search("retrying upstream", usable_only=False)
        self.assertEqual(hits["count"], 1)
        with self.assertRaises(contract.InvalidArgument):
            self.ad.unit_search("   ")
        health = self.ad.health()
        self.assertEqual((health["units"], health["labels"], health["conclusions"]), (4, 4, 3))

    def test_lifecycle_refused_then_accepted_with_missing_named(self):
        out = self.ad.claim_propose(PROV, "pool", "The pool was exhausted", reasoning_type="direct_quote")
        self.assertEqual(out["status"], "proposed")
        with self.assertRaises(contract.InvalidArgument):
            self.ad.claim_propose(PROV, "x", "y", reasoning_type="vibes")
        with self.assertRaises(contract.GateRejected) as err:
            self.ad.claim_confirm(PROV, "pool")
        self.assertIn("cited now: nothing", err.exception.missing[0])
        with self.assertRaises(contract.GateRejected) as err:
            self.ad.claim_confirm(PROV, "pool", ["log_004"])
        self.assertIn("out_of_window", err.exception.missing[0])
        with self.assertRaises(contract.NotFound):
            self.ad.claim_confirm(PROV, "pool", ["log_999"])
        self.assertEqual(self.ad.claim_confirm(PROV, "pool", ["log_001"])["status"], "confirmed")
        explained = self.ad.claim_explain("pool")
        self.assertEqual((explained["status"], explained["sufficient_cites"], explained["blocking_now"]), ("confirmed", 1, []))
        self.assertEqual(explained["author"], "agent_7|role=reader")

        self.ad.claim_rule_out(PROV, "dns", "DNS failed", "resolver logs", "no failures in the window")
        elim = self.ad.eliminated_claims()
        self.assertEqual(elim["count"], 1)
        self.assertEqual(elim["claims"][0]["label"], instance_adapter.ELIMINATED)
        self.assertIn(instance_adapter.ELIMINATED, self.ad.claim_explain("fact:dns")["statement"])

        out = self.ad.claim_support(PROV, "pool", "rc_pool_exhaustion")
        self.assertIn("never attacked", "; ".join(out["blocking_now"]))
        with self.assertRaises(contract.GateRejected) as err:
            self.ad.conclusion_mark_ready(PROV, "rc_pool_exhaustion")
        self.assertIn("never attacked", err.exception.missing[0])
        crit = self.ad.critique_add(SEAT, "rc_pool_exhaustion", "lens_skeptic", "a slow query could explain it", "damaging")
        self.assertIn("1 damaging head(s) unanswered", "; ".join(crit["blocking_now"]))
        with self.assertRaises(contract.InvalidArgument):
            self.ad.critique_add(SEAT, "rc_pool_exhaustion", "lens_skeptic", "x", "catastrophic")
        with self.assertRaises(contract.NotFound):
            self.ad.critique_add(SEAT, "rc_nope", "lens_skeptic", "x", "damaging")
        ans = self.ad.critique_answer(PROV, crit["critique"], "p99 stayed flat; the pool filled from retries")
        self.assertEqual(ans["blocking_now"], [])
        self.assertTrue(self.ad.conclusion_mark_ready(PROV, "rc_pool_exhaustion")["ready"])
        con = self.ad.claim_explain("conclusion:rc_pool_exhaustion")
        self.assertTrue(con["ready"])
        self.assertEqual(con["attacks"][0]["answered"], True)
        self.assertEqual(self.ad.health()["DRIFT_confirmed_without_valid_cite"], 0)

    def test_envelope_reports_gate_refusals_not_empty_success(self):
        self.ad.claim_propose(PROV, "pool", "The pool was exhausted")
        out = server.call("claim_confirm", lambda: self.ad.claim_confirm(PROV, "pool"))
        self.assertEqual(out["status"], "rejected_by_gate")
        self.assertTrue(out["missing"])
        out = server.call("unit_get", lambda: self.ad.unit_get("log_001"))
        self.assertEqual(out["status"], "ok")
        self.assertIn(contract.UNTRUSTED_TEXT_KEY, out["data"])
        down = instance_adapter.create_adapter_for(self.inst, dbmod.Db(url="http://127.0.0.1:9/sql", ns="x", db="y", timeout=2))
        out = server.call("health", down.health)
        self.assertEqual(out["status"], "db_unreachable")

    def test_base_adapter_reports_unsupported_lifecycle_tools(self):
        class Minimal(contract.InstanceAdapter):
            name = "minimal"
            def unit_get(self, locator): return {}
            def unit_search(self, query, usable_only=True, limit=20): return {}
            def claim_explain(self, claim_id): return {}
            def eliminated_claims(self, about=None): return {}
            def health(self): return {}
            def critique_add(self, provenance, target, lens, head, verdict, text=None): return {}
            def critique_answer(self, provenance, critique, answer): return {}
        out = server.call("claim_propose", lambda: Minimal().claim_propose(PROV, "a", "b"))
        self.assertEqual(out["status"], "invalid_argument")
        self.assertIn("not supported", out["error"])


if __name__ == "__main__":
    unittest.main()
