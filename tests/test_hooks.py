# -*- coding: utf-8 -*-
"""Hooks: the session-start health line (a stopped database must WARN, never stay silent) and the
pre-tool lookup (unit ids and eliminated claims surface before the call runs). Throwaway server.

    python -m unittest discover -s tests -p "test_hooks.py" -v
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
from xscholar.hooks import health, lookup  # noqa: E402
from xscholar.mcp import adapter as contract, instance_adapter  # noqa: E402
from xscholar.pipeline import chunker, intake, loader  # noqa: E402
try:
    from test_pipeline import Adapter, CORPUS  # noqa: E402
    from test_pipeline_db import LABELS  # noqa: E402
except ImportError:  # pragma: no cover
    from tests.test_pipeline import Adapter, CORPUS  # noqa: E402
    from tests.test_pipeline_db import LABELS  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DEMO = HERE.parent / "instances" / "demo-incident" / "instance.yaml"
SURREAL = surreal_bin.find()
PORT = 8027
PROV = contract.Provenance("agent_7", "reader", None)


class HealthHookTests(unittest.TestCase):
    def test_missing_instance_and_unreachable_db_both_warn(self):
        ok, line = health.check({})
        self.assertFalse(ok)
        self.assertTrue(line.startswith("WARNING"))
        ok, line = health.check({"XSCHOLAR_INSTANCE": str(DEMO), "XSCHOLAR_DB_URL": "http://127.0.0.1:9/sql",
                                 "XSCHOLAR_HOOK_TIMEOUT": "1"})
        self.assertFalse(ok)
        self.assertIn("NOT reachable", line)
        self.assertIn("database is off, not that nothing is known", line)
        ok, line = health.check({"XSCHOLAR_INSTANCE": str(HERE / "nope.yaml")})
        self.assertFalse(ok)
        self.assertIn("unusable", line)


class LookupHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SURREAL:
            raise unittest.SkipTest("NOT EXECUTED: " + surreal_bin.MISSING)
        if surreal_bin.port_in_use(PORT):
            raise unittest.SkipTest(surreal_bin.occupied(PORT))
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
        cls.db = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="h%d" % int(time.time() * 1000), db="d")
        cls.db.script(gates.schema_surql(cls.inst))
        cls.db.script(gates.gate_surql(cls.inst))
        tmp = pathlib.Path(tempfile.mkdtemp())
        intake.write_units(Adapter(CORPUS), tmp, cls.inst)
        chunker.chunk(tmp / "units.jsonl", tmp, cls.inst)
        (tmp / "labels").mkdir()
        (tmp / "labels" / "log_line_0000.jsonl").write_text("".join(json.dumps(l) + "\n" for l in LABELS), encoding="utf-8")
        loader.load(tmp / "units.jsonl", tmp / "labels", cls.db, cls.inst)
        shutil.rmtree(tmp, True)
        cls.ad = instance_adapter.GenericAdapter(cls.inst, cls.db)
        cls.ad.claim_rule_out(PROV, "dns", "DNS resolution failed during the incident window",
                              "searched resolver logs", "one slow cache refresh; no failures")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def test_health_line_when_up(self):
        ok, line = health.check({"XSCHOLAR_INSTANCE": str(DEMO), "XSCHOLAR_DB_URL": self.db.url,
                                 "XSCHOLAR_NS": self.db.ns, "XSCHOLAR_DB": self.db.db})
        self.assertTrue(ok, line)
        self.assertIn("4 units", line)
        self.assertNotIn("WARNING", line)

    def test_unit_id_in_a_tool_call_is_explained(self):
        out = lookup.lookup("Edit: cite unit:log_004 for the retry claim", self.ad)
        self.assertIn("unit log_004", out)
        self.assertIn("NOT usable", out)
        self.assertEqual(lookup.lookup("nothing relevant here", self.ad), "")

    def test_repeating_an_eliminated_claim_is_flagged(self):
        out = lookup.lookup("Write: argue that DNS resolution failed during the incident window", self.ad)
        self.assertIn(instance_adapter.ELIMINATED, out)
        self.assertIn("what the source shows", out)

    def test_hook_output_is_valid_json_and_bounded(self):
        out = lookup.lookup("unit:log_001 " * 50, self.ad)
        self.assertLessEqual(len(out), lookup.MAX_CHARS)
        self.assertLessEqual(out.count("\n"), lookup.MAX_LINES)


if __name__ == "__main__":
    unittest.main()
