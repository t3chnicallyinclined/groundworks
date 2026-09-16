# -*- coding: utf-8 -*-
"""Lenses from config (xscholar/lens.py), tested both ways on a throwaway in-memory SurrealDB:
the config refuses a looser standard and a crosswalk outside the vocabulary; a lens view holds only
what its standard and filter admit; the framework files every labelled issue and its matrix reconciles;
rebuilding is idempotent; the server envelope carries the untrusted key; and the view is shown to leak
when its standard is dropped, then restored.

    python -m unittest tests.test_lens -v
"""
import copy
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
from xscholar import config, gates, lens, surreal_bin  # noqa: E402
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
PORT = surreal_bin.free_port()   # OS-assigned (was 8034): suites no longer share a registry of fixed ports
UT = contract.UNTRUSTED_TEXT_KEY

# the probe: an out_of_window unit (log_004) labelled tier 3 with an issue; it must never reach service_owner
PROBE = {"id": "log_004", "tier": 3, "dir": "against_goal", "issues": ["dependency"], "targets": ["rc_upstream_timeout"],
         "role": "warning", "reason": "Retries against the upstream.", "quote": "retrying upstream"}


def demo_data():
    return copy.deepcopy(config.load(DEMO).data)


class ConfigTests(unittest.TestCase):
    def refused(self, data, phrase):
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn(phrase, str(err.exception))

    def test_demo_lenses_load_with_kinds_and_backing(self):
        inst = config.load(DEMO)
        self.assertEqual([l["kind"] for l in inst.lenses], ["perspective", "discipline", "perspective", "framework"])
        self.assertEqual(inst.lens("service_owner")["backing"], "prompt")   # charter unsigned: cannot claim more
        self.assertEqual(inst.lens_standard(inst.lens("service_owner")), {"status": ["in_window"], "grade": []})
        self.assertEqual(inst.lens_standard(inst.lens("management")), {"status": ["in_window"], "grade": []})  # base
        with self.assertRaises(KeyError):
            inst.lens("nobody")

    def test_standard_looser_than_the_base_is_refused_and_stricter_accepted(self):
        data = demo_data()
        data["lenses"][0]["standard"] = {"status": ["in_window", "out_of_window"]}
        self.refused(data, "never looser")
        data["lenses"][0]["standard"] = {"status": ["out_of_window"]}
        self.refused(data, "never looser")
        data["lenses"][0]["standard"] = {"grade": ["agent_summary"]}
        self.refused(data, "outside the base fidelity.confirming")
        data["lenses"][0]["standard"] = {"status": ["nowhere"]}
        self.refused(data, "does not exist")
        data["lenses"][0]["standard"] = {"tier": [3]}
        self.refused(data, "standard may only name")
        data["lenses"][0]["standard"] = {"status": ["in_window"], "grade": ["original_log"]}   # stricter
        self.assertEqual(config.load(data).lens("service_owner")["standard"]["grade"], ["original_log"])

    def test_crosswalk_and_frame_are_checked_against_the_vocabulary(self):
        data = demo_data()
        fw = data["lenses"][3]
        fw["crosswalk"]["trigger"] = ["deploy", "gremlins"]
        self.refused(data, "gremlins, not in vocabulary.issues")
        fw["crosswalk"] = {"trigger": [], "propagation": [], "detection": [], "mitigation": [], "prevention": [], "extra": []}
        self.refused(data, "not frame nodes")
        fw["crosswalk"] = {"trigger": []}
        self.refused(data, "must name every frame node")
        fw["crosswalk"] = {n: [] for n in fw["frame"]["nodes"]}
        fw["frame"]["nodes"] = ["trigger", "trigger", "detection", "propagation", "mitigation", "prevention"]
        self.refused(data, "must be unique")
        fw["frame"] = {"nodes": ["a"]}
        self.refused(data, "frame.source")
        data = demo_data()
        del data["lenses"][3]["frame"]
        self.refused(data, "framework lens needs frame")

    def test_an_unsigned_charter_cannot_claim_more_than_prompt_backed(self):
        data = demo_data()
        data["lenses"][0]["backing"] = "source"          # signed is false in the demo config
        self.refused(data, "signed is not true")
        data["lenses"][0]["signed"] = True               # a human signed it: now the claim is allowed
        config.load(data)
        data["lenses"][0]["signed"] = "yes"
        self.refused(data, "signed must be true or false")

    def test_kind_backing_charter_filter_and_names_are_checked(self):
        data = demo_data()
        data["lenses"][0]["kind"] = "vibe"
        self.refused(data, "kind must be one of")
        data = demo_data()
        data["lenses"][0]["backing"] = "trust me"
        self.refused(data, "backing must be one of")
        data = demo_data()
        data["lenses"][0]["charter"] = ""
        self.refused(data, "charter is required")
        data = demo_data()
        data["lenses"][0]["filter"] = "status IN $usable"        # a unit field and a parameter: not a label filter
        self.refused(data, "not a label field")
        data = demo_data()
        data["lenses"][0]["filter"] = "tier >= 2; DELETE label"
        self.refused(data, "not allowed")
        data = demo_data()
        data["lenses"][1]["name"] = "service_owner"
        self.refused(data, "lens names must be unique")
        data = demo_data()
        data["lenses"][0]["surprise"] = 1
        self.refused(data, "unknown key")
        self.assertIsNone(config.check_lens_filter("issues CONTAINSANY ['config', 'deploy'] AND (tier >= 2 OR dir = 'mixed')"))


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SURREAL:
            raise unittest.SkipTest("NOT EXECUTED: " + surreal_bin.MISSING)
        if surreal_bin.port_in_use(PORT):
            raise unittest.SkipTest(surreal_bin.occupied(PORT))
        cls.proc = subprocess.Popen([str(SURREAL), "start", "--bind", "127.0.0.1:%d" % PORT, "--user", "root",
                                     "--pass", "root", "memory"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        self.db = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="l%d" % int(time.time() * 1000), db="d%d" % self.n)
        self.db.script(gates.schema_surql(self.inst))
        self.db.script(gates.gate_surql(self.inst))
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        intake.write_units(Adapter(CORPUS), self.dir, self.inst)
        chunker.chunk(self.dir / "units.jsonl", self.dir, self.inst)
        (self.dir / "labels").mkdir()
        labels = [l for l in LABELS if l["id"] != "log_004"] + [PROBE]   # the probe replaces log_004's tier-1 label
        (self.dir / "labels" / "log_line_0000.jsonl").write_text("".join(json.dumps(l) + "\n" for l in labels), encoding="utf-8")
        loader.load(self.dir / "units.jsonl", self.dir / "labels", self.db, self.inst)
        lens.apply(self.db, self.inst)

    def view_rows(self, name):
        return self.db.sql("SELECT unit, status, tier FROM lens_%s ORDER BY unit" % name, expect=1)[0]


class ViewTests(ServerCase):
    def test_service_owner_holds_only_in_window_tier_2_plus_and_never_the_probe(self):
        out = lens.lens_view(self.db, self.inst, "service_owner")
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["drift"], 0)
        self.assertEqual({r["unit"] for r in out["rows"]}, {"unit:log_001", "unit:log_002"})
        for row in out["rows"]:
            self.assertEqual(row["status"], "in_window")
            self.assertGreaterEqual(row["tier"], 2)
            self.assertIn(UT, row)
            self.assertNotIn("text", row)
        self.assertNotIn("unit:log_004", {r["unit"] for r in out["rows"]})   # the out_of_window tier-3 probe
        self.assertEqual(out["rows"][0]["tier"], 3)                          # ordered strongest first
        self.assertEqual(out["rows"][0]["locator"]["line"], 1)
        # the probe is a real tier-3 label in the graph; only the standard keeps it out
        self.assertEqual(self.db.sql("SELECT count() AS n FROM label WHERE unit = unit:log_004 AND tier = 3 GROUP ALL")[0][0]["n"], 1)
        self.assertEqual({r["unit"] for r in self.view_rows("management")}, {"unit:log_001", "unit:log_002"})
        self.assertEqual({r["unit"] for r in self.view_rows("security")}, set())   # config/dependency only in_window: none

    def test_view_follows_new_labels_and_reports_drift_after_a_unit_changes(self):
        self.db.sql("CREATE label:late SET unit=unit:log_003, tier=2, dir='neutral', issues=['config'], targets=[], chunk='c', run='r'")
        self.assertIn("unit:log_003", {r["unit"] for r in self.view_rows("service_owner")})
        self.assertIn("unit:log_003", {r["unit"] for r in self.view_rows("security")})
        self.db.sql("UPDATE unit:log_003 SET status = 'out_of_window'")       # the view caveat: no refresh from unit
        out = lens.lens_view(self.db, self.inst, "service_owner")
        self.assertEqual(out["drift"], 1)
        self.assertIn("rebuild", out["warning"])
        lens.apply(self.db, self.inst)                                          # rebuild recomputes from label
        out = lens.lens_view(self.db, self.inst, "service_owner")
        self.assertEqual((out["drift"], out["count"]), (0, 2))
        self.assertNotIn("unit:log_003", {r["unit"] for r in out["rows"]})

    def test_unknown_lens_and_bad_filter_are_loud(self):
        with self.assertRaises(lens.LensError):
            lens.lens_view(self.db, self.inst, "nobody")
        bad = copy.deepcopy(self.inst.lens("service_owner"))
        bad["filter"] = "tier >= 2 OR unit.status = 'out_of_window'"
        with self.assertRaises(lens.LensError):
            lens.view_surql(self.inst, bad)

    def test_dropping_the_standard_leaks_the_probe_and_restoring_removes_it(self):
        """Prove the check can fail: the same view without its standard admits the out_of_window unit."""
        good = lens.view_surql(self.inst, self.inst.lens("service_owner"))
        self.assertIn("unit.status IN ['in_window']", good)
        broken = good.replace("WHERE unit.status IN ['in_window'] AND ", "WHERE ")
        self.assertNotIn("WHERE unit.status", broken)
        self.assertIn("WHERE (tier >= 2)", broken)
        self.db.script(broken)
        leaked = lens.lens_view(self.db, self.inst, "service_owner")
        self.assertIn("unit:log_004", {r["unit"] for r in leaked["rows"]}, "the broken view must leak the probe")
        self.assertEqual(leaked["drift"], 1, "drift must count the leaked row")
        self.assertEqual(leaked["count"], 3)
        self.db.script(good)                                                    # restore
        restored = lens.lens_view(self.db, self.inst, "service_owner")
        self.assertNotIn("unit:log_004", {r["unit"] for r in restored["rows"]})
        self.assertEqual((restored["count"], restored["drift"]), (2, 0))


class FrameworkTests(ServerCase):
    def test_every_label_with_an_issue_is_filed_and_the_matrix_reconciles(self):
        counts = lens.build_framework(self.db, self.inst)
        self.assertEqual(counts["with_issues"], 3)            # log_001 saturation, log_002 deploy, log_004 dependency
        self.assertEqual((counts["filed"], counts["unfiled"], counts["swept"]), (3, 0, 0))
        self.assertEqual(counts["unfiled_issues"], {})
        self.assertEqual(self.db.count("consideration"), 5)
        filed = self.db.sql("SELECT node, in.unit AS unit FROM falls_under ORDER BY node")[0]
        by_node = {}
        for row in filed:
            by_node.setdefault(row["node"], set()).add(str(row["unit"]))
        self.assertEqual(by_node, {"trigger": {"unit:log_002"}, "propagation": {"unit:log_001", "unit:log_004"}})
        m = lens.matrix(self.db, self.inst)["incident_framework"]
        self.assertEqual(m["filed"], counts["edges"])
        self.assertEqual(sum(cell["labels"] for cell in m["nodes"].values()), counts["edges"])
        prop = m["nodes"]["propagation"]
        self.assertEqual((prop["usable"]["for_goal"], prop["unusable"]["against_goal"], prop["labels"]), (1, 1, 2))
        self.assertEqual((m["nodes"]["trigger"]["usable"]["mixed"], m["nodes"]["mitigation"]["labels"]), (1, 0))
        for node, cell in m["nodes"].items():
            self.assertEqual(sum(cell["usable"].values()) + sum(cell["unusable"].values()), cell["labels"], node)
        lines = lens.matrix_lines({"incident_framework": m})
        self.assertEqual(len(lines), 5)
        self.assertIn("propagation: 2 label(s); usable: for_goal 1; unusable: against_goal 1", lines[1])
        view = lens.lens_view(self.db, self.inst, "incident_framework")
        self.assertEqual((view["kind"], view["filed"], view["nodes"]["trigger"]["labels"]), ("framework", 3, 1))

    def test_rebuild_is_idempotent_and_sweeps_only_vanished_filings(self):
        first = lens.build_framework(self.db, self.inst)
        ids_first = sorted(str(r["id"]) for r in self.db.sql("SELECT id FROM falls_under")[0])
        second = lens.build_framework(self.db, self.inst)
        ids_second = sorted(str(r["id"]) for r in self.db.sql("SELECT id FROM falls_under")[0])
        self.assertEqual(ids_first, ids_second)
        self.assertEqual(len(ids_second), len(set(ids_second)))
        self.assertEqual((first["edges"], second["edges"], second["swept"]), (3, 3, 0))
        self.db.sql("UPDATE label SET issues = [] WHERE unit = unit:log_002")   # one filing disappears from the source
        third = lens.build_framework(self.db, self.inst)
        self.assertEqual((third["filed"], third["swept"]), (2, 1))
        self.assertEqual(self.db.count("falls_under"), 2)

    def test_failed_write_blocks_the_sweep(self):
        lens.build_framework(self.db, self.inst)
        real_sql = self.db.sql

        def flaky(statement, expect=None):
            if "RELATE" in statement:
                raise dbmod.DbError("simulated failure")
            return real_sql(statement, expect)
        self.db.sql = flaky
        with self.assertRaises(dbmod.DbError):
            lens.build_framework(self.db, self.inst)
        self.db.sql = real_sql
        self.assertEqual(self.db.count("falls_under"), 3, "a failed run must not sweep the previous filings")

    def test_issue_no_node_claims_is_reported_not_dropped(self):
        data = demo_data()
        data["lenses"][3]["crosswalk"]["propagation"] = ["capacity"]           # saturation and dependency now unclaimed
        inst = config.load(data)
        counts = lens.build_framework(self.db, inst)
        self.assertEqual((counts["filed"], counts["unfiled"]), (1, 2))
        self.assertEqual(counts["unfiled_issues"], {"saturation": 1, "dependency": 1})


class EnvelopeTests(ServerCase):
    def test_lens_view_through_the_server_envelope(self):
        ad = instance_adapter.create_adapter_for(self.inst, self.db)
        out = server.call("lens_view", lambda: ad.lens_view("service_owner", 10))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["notice"], contract.UNTRUSTED_NOTICE)
        self.assertEqual(out["data"]["count"], 2)
        self.assertIn(UT, out["data"]["rows"][0])
        unknown = server.call("lens_view", lambda: ad.lens_view("nobody"))
        self.assertEqual(unknown["status"], "invalid_argument")
        self.assertIn("service_owner", unknown["missing"][0])
        health = server.call("health", ad.health)
        self.assertEqual([l["name"] for l in health["data"]["lenses"]],
                         ["service_owner", "security", "management", "incident_framework"])
        self.assertEqual(health["data"]["lenses"][3], {"name": "incident_framework", "kind": "framework", "backing": "prompt"})
        down = instance_adapter.create_adapter_for(self.inst, dbmod.Db(url="http://127.0.0.1:9/sql", ns="x", db="y", timeout=2))
        self.assertEqual(server.call("lens_view", lambda: down.lens_view("service_owner"))["status"], "db_unreachable")

    def test_mcp_tool_is_registered_and_answers(self):
        try:
            import mcp  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("NOT EXECUTED: the mcp package is not installed")
        import asyncio
        ad = instance_adapter.create_adapter_for(self.inst, self.db)
        srv = server.build_server(ad, contract.Provenance("agent_lens", "reader"))
        self.assertIn("lens_view", {t.name for t in asyncio.run(srv.list_tools())})
        res = asyncio.run(srv.call_tool("lens_view", {"name": "service_owner", "limit": 5}))
        content = res[0] if isinstance(res, tuple) else res
        out = json.loads(content[0].text)
        self.assertEqual((out["status"], out["data"]["count"]), ("ok", 2))
        self.assertEqual({r["status"] for r in out["data"]["rows"]}, {"in_window"})


if __name__ == "__main__":
    unittest.main()
