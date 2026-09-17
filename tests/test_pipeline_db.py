# -*- coding: utf-8 -*-
"""Loader and write-surface tests on a throwaway in-memory SurrealDB (never a live one).

    python -m unittest discover -s engine/pipeline -p "test_pipeline_db.py" -v
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar import gates  # noqa: E402
from xscholar.pipeline import chunker  # noqa: E402
from xscholar.pipeline import intake  # noqa: E402
from xscholar.pipeline import loader  # noqa: E402
from xscholar.pipeline import write  # noqa: E402
from test_pipeline import Adapter, CORPUS  # noqa: E402

DEMO = HERE.parent / "instances" / "demo-incident" / "instance.yaml"
from xscholar import surreal_bin  # noqa: E402
SURREAL = surreal_bin.find()
PORT = surreal_bin.free_port()   # OS-assigned (was 8024): suites no longer share a registry of fixed ports
LABELS = [
    {"id": "log_001", "tier": 3, "dir": "for_goal", "issues": ["saturation"], "targets": ["rc_pool_exhaustion"],
     "role": "error", "reason": "Direct evidence.", "quote": "pool exhausted"},
    {"id": "log_002", "tier": 2, "dir": "mixed", "issues": ["deploy"], "targets": ["rc_bad_deploy"],
     "role": "change", "reason": "Deploy in window.", "quote": "deploy v2.14.0"},
    {"id": "log_003", "tier": 0, "dir": None, "issues": [], "targets": [], "role": "note"},
    {"id": "log_004", "tier": 1, "dir": "neutral", "issues": [], "targets": ["rc_upstream_timeout"], "role": "warning"},
]


class ServerCase(unittest.TestCase):
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
        self.db = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t%d" % int(time.time() * 1000), db="d%d" % self.n)
        self.db.script(gates.schema_surql(self.inst))
        self.db.script(gates.gate_surql(self.inst))
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        intake.write_units(Adapter(CORPUS), self.dir, self.inst)
        chunker.chunk(self.dir / "units.jsonl", self.dir, self.inst)
        self.labels_dir = self.dir / "labels"
        self.labels_dir.mkdir()
        self.write_labels(LABELS)

    def write_labels(self, labels):
        (self.labels_dir / "log_line_0000.jsonl").write_text(
            "".join(json.dumps(l) + "\n" for l in labels), encoding="utf-8")

    def writer(self, agent="tester", role="reader", lens=None):
        return write.Writer(self.db, self.inst, {"agent_id": agent, "role": role, "lens": lens})


class LoaderTests(ServerCase):
    def test_load_counts_and_bulk_never_creates_ready_or_confirmed(self):
        counts = loader.load(self.dir / "units.jsonl", self.labels_dir, self.db, self.inst)
        self.assertEqual((counts["units"], counts["labels"], counts["edges"], counts["swept"]), (4, 4, 3, 0))
        self.assertEqual(self.db.count("unit"), 4)
        self.assertEqual(self.db.count("label"), 4)
        self.assertEqual(self.db.count("bears_on"), 3)
        ready = self.db.sql("SELECT count() AS n FROM conclusion WHERE ready = true GROUP ALL")[0]
        self.assertEqual(ready[0]["n"] if ready else 0, 0)
        self.assertEqual(self.db.count("conclusion"), 3)      # one row per vocabulary target
        self.assertEqual(self.db.count("fact"), 0)
        grades = {r["grade"] for r in self.db.sql("SELECT grade FROM label")[0]}
        self.assertEqual(grades, {"agent"})

    def test_reload_updates_in_place_and_sweeps_only_vanished_edges(self):
        loader.load(self.dir / "units.jsonl", self.labels_dir, self.db, self.inst)
        again = loader.load(self.dir / "units.jsonl", self.labels_dir, self.db, self.inst)
        self.assertEqual(self.db.count("bears_on"), 3)       # no duplicates
        self.assertEqual(again["swept"], 0)
        trimmed = json.loads(json.dumps(LABELS))
        trimmed[3]["targets"] = []                           # one edge disappears from the source
        self.write_labels(trimmed)
        third = loader.load(self.dir / "units.jsonl", self.labels_dir, self.db, self.inst)
        self.assertEqual(third["swept"], 1)
        self.assertEqual(self.db.count("bears_on"), 2)

    def test_failed_write_blocks_the_sweep(self):
        loader.load(self.dir / "units.jsonl", self.labels_dir, self.db, self.inst)
        real_sql = self.db.sql
        state = {"calls": 0}

        def flaky(statement, expect=None):
            state["calls"] += 1
            if "RELATE" in statement:
                raise dbmod.DbError("simulated failure")
            return real_sql(statement, expect)
        self.db.sql = flaky
        with self.assertRaises(dbmod.DbError):
            loader.load(self.dir / "units.jsonl", self.labels_dir, self.db, self.inst)
        self.db.sql = real_sql
        self.assertEqual(self.db.count("bears_on"), 3, "a failed run must not sweep the previous edges")


class WriteTests(ServerCase):
    def setUp(self):
        super().setUp()
        loader.load(self.dir / "units.jsonl", self.labels_dir, self.db, self.inst)

    def test_confirm_refused_then_accepted_with_actionable_reason(self):
        w = self.writer()
        w.propose("pool", "The connection pool was exhausted", reasoning_type="direct_quote")
        with self.assertRaises(write.GateRefusal) as err:
            w.confirm("pool")
        self.assertIn("cited now: nothing", str(err.exception))
        with self.assertRaises(write.GateRefusal) as err:
            w.confirm("pool", ["log_004"])                   # out_of_window: true but unusable
        self.assertIn("log_004 (grade original_log, status out_of_window)", str(err.exception))
        self.assertEqual(w.confirm("pool", ["log_001"]), "fact:pool")
        row = self.db.sql("SELECT status, author, reasoning_type FROM fact:pool")[0][0]
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(row["author"], "tester|role=reader")
        self.assertEqual(row["reasoning_type"], "direct_quote")

    def test_provenance_comes_from_identity_not_arguments(self):
        with self.assertRaises(ValueError):
            write.Writer(self.db, self.inst, {"agent_id": "", "role": "x", "lens": None})
        w = self.writer(agent="seat_9", role="seat", lens="lens_security")
        w.rule_out("dns", "DNS failed", tried="resolver logs", evidence="no failures in window")
        row = self.db.sql("SELECT status, author, lens FROM fact:dns")[0][0]
        self.assertEqual((row["status"], row["author"], row["lens"]), ("ruled_out", "seat_9|role=seat", "lens_security"))
        with self.assertRaises(TypeError):
            w.propose("x", "y", author="someone else")        # no such argument exists

    def test_ready_needs_support_attack_and_no_fatal(self):
        w = self.writer()
        with self.assertRaises(write.GateRefusal) as err:
            w.mark_ready("rc_pool_exhaustion")
        self.assertIn("no confirmed supporting fact", str(err.exception))
        self.assertIn("never attacked", str(err.exception))
        w.propose("pool", "Pool exhausted", "direct_quote")
        w.confirm("pool", ["log_001"])
        w.support("pool", "rc_pool_exhaustion")
        cid = w.critique("rc_pool_exhaustion", "retries, not capacity, caused it", "fatal", lens="lens_skeptic")
        with self.assertRaises(write.GateRefusal) as err:
            w.mark_ready("rc_pool_exhaustion")
        self.assertIn("1 fatal attack(s) standing", str(err.exception))
        self.db.sql("UPDATE %s SET verdict='damaging'" % cid)
        w.answer(cid, "Retry volume is bounded; the pool size is the constraint.")
        with self.assertRaises(write.GateRefusal) as err:     # V2-8: nothing records the observation
            w.mark_ready("rc_pool_exhaustion")
        self.assertIn("`preserves`", str(err.exception))
        w.relate("preserves", "log_001", "rc_pool_exhaustion", how="alert")
        self.assertEqual(w.mark_ready("rc_pool_exhaustion"), "conclusion:rc_pool_exhaustion")


if __name__ == "__main__":
    unittest.main()
