# -*- coding: utf-8 -*-
"""Originals in the datastore, on a throwaway in-memory SurrealDB (never a live one).

Many units link to one `document`; any unit gets its original back, re-hashed; a document units point at
cannot be deleted; `verify` walks the store and the graph and names every missing or corrupt original; a
unit with no verified original cannot count toward a gated promotion, for root and for a signed-in agent;
the MCP tool returns a text original under untrusted_source_text and refuses binary content; and the demo
runs end to end with originals, twice on the same graph.

Every test here was first run against a named deliberate break of the code and failed.

    python -m unittest tests.test_originals_db -v
"""
import contextlib
import importlib.util
import io
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
sys.path.insert(0, str(HERE.parent))
from groundworks import access  # noqa: E402
from groundworks import config  # noqa: E402
from groundworks import db as dbmod  # noqa: E402
from groundworks import gates  # noqa: E402
from groundworks import originals  # noqa: E402
from groundworks import store as storemod  # noqa: E402
from groundworks import surreal_bin  # noqa: E402
from groundworks import temporal  # noqa: E402
from groundworks.mcp import adapter as contract  # noqa: E402
from groundworks.mcp import instance_adapter  # noqa: E402
from groundworks.pipeline import intake  # noqa: E402
from groundworks.pipeline import loader  # noqa: E402
from groundworks.pipeline import write  # noqa: E402

ROOT = HERE.parent
DEMO_DIR = ROOT / "instances" / "demo-incident"
SURREAL = surreal_bin.find()
PORT = surreal_bin.free_port()   # OS-assigned (was 8039): suites no longer share a registry of fixed ports
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
PROV = contract.Provenance("reader_test", "reader")


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEMO_ADAPTER = load_module(DEMO_DIR / "adapter.py", "demo_incident_adapter_for_originals")


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
        for _ in range(80):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        else:
            cls.proc.kill()
            raise unittest.SkipTest("NOT EXECUTED: throwaway SurrealDB did not start")
        cls.inst = config.load(DEMO_DIR / "instance.yaml")
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
        self.url = "http://127.0.0.1:%d/sql" % PORT
        self.ns, self.dbname = "o%d" % int(time.time() * 1000), "d%d" % self.n
        self.db = dbmod.Db(url=self.url, ns=self.ns, db=self.dbname)
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.store = storemod.FileStore(self.dir / "originals")

    def build(self, enforce=True):
        """The demo's schema, gates, originals and units, loaded; enforcement applied."""
        self.db.script(gates.schema_surql(self.inst))
        self.db.script(gates.gate_surql(self.inst))
        self.db.script(temporal.temporal_surql(self.inst))
        intake.write_units(DEMO_ADAPTER.Adapter(), self.dir, self.inst, store=self.store)
        (self.dir / "labels").mkdir()
        counts = loader.load(self.dir / "units.jsonl", self.dir / "labels", self.db, self.inst, store=self.store)
        if enforce:
            self.db.script(originals.enforcement_surql(self.inst))
        return counts

    def sha_of(self, data):
        return storemod.sha256_bytes(data)

    def file_of(self, data):
        sha = self.sha_of(data)
        return self.dir / "originals" / "sha256" / sha[:2] / sha


class LinkAndRetrieveTests(ServerCase):
    def test_many_units_point_to_one_document_and_each_retrieves_it(self):
        counts = self.build()
        log = DEMO_ADAPTER.app_log()
        sha = self.sha_of(log)
        self.assertEqual(counts["documents"], 3)
        linked = self.db.sql("SELECT VALUE record::id(id) FROM unit WHERE document = %s ORDER BY id"
                             % originals.doc_rid(sha))[0]
        self.assertEqual(len(linked), 63)
        self.assertEqual(originals.document_meta(self.db, sha)["units"], 63)
        self.assertEqual(self.db.sql("SELECT VALUE count(<~unit) FROM %s" % originals.doc_rid(sha))[0], [63])
        lines = log.decode("utf-8").splitlines()
        for uid in linked:
            found = originals.original_for_unit(self.db, uid, self.store)
            self.assertEqual(found["bytes"], log, uid)
            self.assertEqual(found["document"]["sha256"], sha, uid)
            line = found["locator"]["line"]
            text = self.db.sql("SELECT VALUE text FROM unit:%s" % uid)[0][0]
            self.assertIn(text, lines[line - 1], uid)          # the locator points INTO the original
        self.assertEqual(self.db.count("document"), 3)

    def test_a_unit_with_no_original_says_so_rather_than_returning_nothing(self):
        self.build()
        self.db.sql("UPDATE unit:log_0001 SET document = NONE")
        with self.assertRaises(originals.NoOriginal) as err:
            originals.original_for_unit(self.db, "log_0001", self.store)
        self.assertIn("log_0001", str(err.exception))

    def test_a_never_captured_original_can_never_read_as_verified(self):
        self.build()
        key = originals.none_key("synthetic://gone")
        ok, message = self.db.try_sql("CREATE %s SET media_type = 'text/plain', capture = 'none', integrity = 'ok'"
                                      % originals.doc_rid(key))
        self.assertFalse(ok)
        self.assertIn("integrity", message)
        self.db.sql("CREATE %s SET media_type = 'text/plain', capture = 'none', integrity = 'not_captured'"
                    % originals.doc_rid(key))


class DeletionTests(ServerCase):
    def test_a_document_units_point_at_cannot_be_deleted_silently(self):
        self.build()
        sha = self.sha_of(DEMO_ADAPTER.commit_record())
        ok, message = self.db.try_sql("DELETE %s" % originals.doc_rid(sha))
        self.assertFalse(ok, "a document a unit points at was deleted")
        self.assertIn("referenced", message)
        ok, message = self.db.try_sql("DELETE document")
        self.assertFalse(ok, "a table-wide delete removed referenced documents")
        self.assertEqual(self.db.count("document"), 3)
        self.assertEqual(self.db.sql("SELECT VALUE record::id(document) FROM unit:chg_0001")[0], [sha])
        # the other direction: once nothing points at it, the delete is a deliberate act and is taken
        self.db.sql("UPDATE unit:chg_0001 SET document = NONE")
        self.db.sql("DELETE %s" % originals.doc_rid(sha))
        self.assertEqual(self.db.count("document"), 2)

    def test_a_unit_cannot_point_at_a_document_row_that_is_not_there(self):
        self.build()
        ok, message = self.db.try_sql("UPDATE unit:log_0001 SET document = %s" % originals.doc_rid("f" * 64))
        self.assertFalse(ok)
        self.assertIn("record::exists", message)


class VerifyTests(ServerCase):
    def test_verify_finds_a_corrupted_file_and_a_missing_one_and_records_them(self):
        self.build()
        clean = originals.verify(self.db, self.store, self.inst)
        self.assertTrue(clean["ok"], originals.verify_lines(clean))
        self.assertEqual((clean["documents"], clean["units_with_verified_original"], clean["problems"]), (3, 66, 0))
        self.file_of(DEMO_ADAPTER.commit_record()).write_bytes(b"commit a1b2c3d\n    something else entirely\n")
        self.file_of(DEMO_ADAPTER.metrics_csv()).unlink()
        rep = originals.verify(self.db, self.store, self.inst)
        self.assertFalse(rep["ok"])
        self.assertEqual([d["name"] for d in rep["documents_corrupt"]], ["commit-a1b2c3d.txt"])
        self.assertEqual([d["name"] for d in rep["documents_missing"]], ["metrics-export-1140.csv"])
        self.assertEqual(rep["corrupt_files"], [self.sha_of(DEMO_ADAPTER.commit_record())])
        self.assertEqual(rep["problems"], 3)                 # the corrupt file, and the two document rows
        self.assertEqual(rep["units_original_not_ok"], 3)    # chg_0001, met_0001, met_0002
        integrity = {r["key"]: r["integrity"] for r in self.db.sql(
            "SELECT record::id(id) AS key, integrity FROM document")[0]}
        self.assertEqual(integrity[self.sha_of(DEMO_ADAPTER.metrics_csv())], "missing")
        self.assertEqual(integrity[self.sha_of(DEMO_ADAPTER.commit_record())], "corrupt")
        text = "\n".join(originals.verify_lines(rep))
        self.assertIn("CORRUPT", text)
        self.assertIn("MISSING", text)
        self.assertIn("metrics-export-1140.csv", text)

    def test_verify_names_a_unit_whose_document_row_an_import_never_brought(self):
        """Measured on 3.1.6: OPTION IMPORT skips the record::exists ASSERT, so a restore can leave a unit linked to
        a document row that is not there. Only the walk can find it."""
        self.build()
        self.db.sql("OPTION IMPORT; UPDATE unit:log_0007 SET document = %s" % originals.doc_rid("e" * 64))
        rep = originals.verify(self.db, self.store, self.inst)
        self.assertEqual(rep["units_with_dangling_document"], ["log_0007"])
        self.assertFalse(rep["ok"])
        self.assertIn("DANGLING unit log_0007", "\n".join(originals.verify_lines(rep)))

    def test_required_mode_fails_verify_on_a_unit_with_no_original(self):
        self.build()
        self.db.sql("UPDATE unit:log_0005 SET document = NONE")
        rep = originals.verify(self.db, self.store, self.inst)
        self.assertEqual(rep["problems"], 0)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["units_without_original_sample"], ["log_0005"])
        self.assertIn("GAP", "\n".join(originals.verify_lines(rep)))


class EnforcementTests(ServerCase):
    def confirm_as(self, writer, slug, units):
        writer.propose(slug, "The pool was exhausted", reasoning_type="direct_quote")
        return writer.confirm(slug, units)

    def test_a_unit_whose_original_is_missing_is_reported_and_cannot_confirm_a_fact(self):
        self.build()
        root = write.Writer(self.db, self.inst, {"agent_id": "root_test", "role": "reader", "lens": None})
        self.file_of(DEMO_ADAPTER.app_log()).unlink()
        rep = originals.verify(self.db, self.store, self.inst)
        self.assertEqual([d["name"] for d in rep["documents_missing"]], ["app.log"])
        self.assertIn("log_0043", rep["units_original_not_ok_sample"] + [
            u for u in self.db.sql("SELECT VALUE record::id(id) FROM unit WHERE document.integrity = 'missing'")[0]])
        with self.assertRaises(write.GateRefusal) as err:
            self.confirm_as(root, "pool", ["log_0043"])
        self.assertIn(originals.MARKER + " confirmed_needs_source_grade:", err.exception.detail["db"])
        self.assertEqual(self.db.sql("SELECT VALUE status FROM fact:pool")[0], ["proposed"])
        health = originals.health(self.db, self.inst)
        self.assertEqual(health["units_original_not_verified"], 63)
        # the other direction: the original restored and verified, the same confirmation is taken
        self.store.put(DEMO_ADAPTER.app_log())
        self.assertTrue(originals.verify(self.db, self.store, self.inst)["ok"])
        self.assertEqual(root.confirm("pool", ["log_0043"]), "fact:pool")

    def test_a_unit_with_no_original_at_all_cannot_confirm_a_fact(self):
        self.build()
        root = write.Writer(self.db, self.inst, {"agent_id": "root_test", "role": "reader", "lens": None})
        self.db.sql("UPDATE unit:log_0043 SET document = NONE")
        with self.assertRaises(write.GateRefusal) as err:
            self.confirm_as(root, "pool", ["log_0043"])
        self.assertIn(originals.MARKER, err.exception.detail["db"])
        self.assertEqual(originals.health(self.db, self.inst)["units_without_original_sample"], ["log_0043"])

    def test_the_writer_explains_an_originals_refusal_as_a_missing_original_not_a_missing_grade(self):
        """The unit cited is source-grade and usable, so "needs a cited unit whose grade is in ..." is false, and
        an agent told it would cite the same unit again, forever. The refusal must say what is actually wrong."""
        self.build()
        root = write.Writer(self.db, self.inst, {"agent_id": "root_test", "role": "reader", "lens": None})
        self.db.sql("UPDATE unit:log_0043 SET document = NONE")
        with self.assertRaises(write.GateRefusal) as err:
            self.confirm_as(root, "pool", ["log_0043"])
        self.assertEqual(err.exception.rule, "confirmed_needs_source_grade")
        self.assertEqual(err.exception.detail.get("reason"), "originals", err.exception.missing)
        self.assertIn("log_0043 (NO original)", err.exception.missing)
        self.assertIn("groundworks.originals verify", err.exception.missing)
        self.assertNotIn("whose grade is in", err.exception.missing)
        self.db.sql("UPDATE unit:log_0043 SET document = %s" % originals.doc_rid(self.sha_of(DEMO_ADAPTER.app_log())))
        self.db.sql("UPDATE document SET integrity = 'corrupt' WHERE original_name = 'app.log'")
        with self.assertRaises(write.GateRefusal) as err:
            root.confirm("pool", ["log_0043"])
        self.assertIn("log_0043 (original corrupt)", err.exception.missing)

    def test_enforcement_holds_for_a_signed_in_agent_that_cannot_read_documents(self):
        """Measured: the event reads document.integrity whatever the caller may SELECT, so a role grant can
        neither open nor close this rule."""
        data = json.loads(json.dumps(self.inst.data))
        for role in data["access"]["roles"].values():
            role["read"] = [t for t in role["read"] if t != "document"]
        inst = config.load(data)
        self.build()
        self.db.script(access.access_surql(inst))
        agent = access.connect(access.provision(self.db, inst, "reader_nodoc", "reader"))
        self.assertEqual(agent.sql("SELECT VALUE document.integrity FROM unit:log_0043")[0], [None])
        writer = write.Writer(agent, inst, {"agent_id": "reader_nodoc", "role": "reader", "lens": None})
        self.db.sql("UPDATE document SET integrity = 'missing'")
        with self.assertRaises(write.GateRefusal) as err:
            self.confirm_as(writer, "pool", ["log_0043"])
        self.assertIn(originals.MARKER, err.exception.detail["db"])
        self.db.sql("UPDATE document SET integrity = 'ok'")
        self.assertEqual(writer.confirm("pool", ["log_0043"]), "fact:pool")

    def test_enforcement_is_probed_both_ways_for_every_rule_and_leaves_nothing_behind(self):
        self.build()
        report = originals.run_probes(self.inst, self.db)
        self.assertEqual(len(report["rules"]), 3, report)
        self.assertEqual((report["rejected_ok"], report["accepted_ok"], report["misbehaved"]), (6, 6, 0),
                         json.dumps(report, indent=2))
        leftovers = self.db.sql("SELECT VALUE id FROM unit WHERE string::contains(record::id(id), 'zz_probe')")[0]
        self.assertEqual(leftovers, [])
        self.assertEqual(self.db.count("document"), 3)


class McpTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.build()
        self.adapter = instance_adapter.create_adapter_for(self.inst, self.db, store=self.store)

    def srv(self):
        try:
            from groundworks.mcp import server
            return server, server.build_server(self.adapter, PROV)
        except ImportError as exc:
            self.skipTest("NOT EXECUTED: the MCP SDK is not installed (%s)" % exc)

    def tool(self, name, **args):
        import asyncio
        server, srv = self.srv()
        res = asyncio.run(srv.call_tool(name, args))
        content = res[0] if isinstance(res, tuple) else res
        return json.loads(content[0].text)

    def add_binary_original(self):
        sha = self.store.put(PNG)
        self.db.sql("CREATE %s SET sha256 = %s, media_type = 'image/png', bytes = %d, capture = 'raw', integrity = 'ok', "
                    "original_name = 'dashboard.png'" % (originals.doc_rid(sha), json.dumps(sha), len(PNG)))
        self.db.sql("CREATE unit:shot_0001 SET type = 'metric_point', text = '', locator = {series: 'db.pool.in_use', "
                    "ts: '2026-03-04T11:40:00Z'}, status = 'in_window', grade = 'exported_metric', hash = 'h', "
                    "document = %s" % originals.doc_rid(sha))
        return sha

    def test_the_mcp_tool_returns_the_original_under_the_untrusted_key(self):
        out = self.tool("document_get", ref="log_0043")
        self.assertEqual(out["status"], "ok", out)
        data = out["data"]
        self.assertEqual(data[contract.UNTRUSTED_TEXT_KEY], DEMO_ADAPTER.app_log().decode("utf-8"))
        self.assertIn("notice", out)
        self.assertEqual(data["document"]["sha256"], self.sha_of(DEMO_ADAPTER.app_log()))
        self.assertEqual((data["unit"], data["locator"]["line"], data["document"]["units"]), ("log_0043", 43, 63))
        by_sha = self.tool("document_get", ref="sha256:" + self.sha_of(DEMO_ADAPTER.commit_record()))
        self.assertEqual(by_sha["status"], "ok", by_sha)
        self.assertIn("max_attempts 3 -> 8", by_sha["data"][contract.UNTRUSTED_TEXT_KEY])
        self.assertEqual(self.tool("document_get", ref="log_9999")["status"], "not_found")

    def test_the_mcp_tool_refuses_binary_content_and_still_gives_its_metadata(self):
        sha = self.add_binary_original()
        out = self.tool("document_get", ref="shot_0001")
        self.assertEqual(out["status"], "invalid_argument", out)
        self.assertIn("binary", out["error"])
        self.assertEqual(out["missing"], ["include_content=false"])
        meta = self.tool("document_get", ref="shot_0001", include_content=False)
        self.assertEqual(meta["status"], "ok", meta)
        self.assertNotIn(contract.UNTRUSTED_TEXT_KEY, meta["data"])
        self.assertEqual((meta["data"]["document"]["sha256"], meta["data"]["content"]), (sha, "not requested"))

    def test_the_mcp_tool_never_serves_a_corrupt_original(self):
        log = DEMO_ADAPTER.app_log()
        self.file_of(log).write_bytes(log.replace(b"pool exhausted", b"pool refilled!"))   # same length: only the hash tells
        out = self.tool("document_get", ref="log_0043")
        self.assertEqual(out["status"], "error", out)
        self.assertIn("CORRUPT", out["error"])
        self.assertIn("app.log", out["error"])
        self.assertNotIn("data", out)

    def test_claim_confirm_re_hashes_cited_originals_before_the_datastore_is_asked(self):
        """The datastore still says integrity ok (nobody ran verify); the store says the file is gone."""
        self.file_of(DEMO_ADAPTER.app_log()).unlink()
        self.adapter.claim_propose(PROV, "pool", "The pool was exhausted", reasoning_type="direct_quote")
        with self.assertRaises(contract.GateRejected) as err:
            self.adapter.claim_confirm(PROV, "pool", ["log_0043"])
        self.assertIn("MISSING", str(err.exception))
        self.assertEqual(self.db.sql("SELECT VALUE status FROM fact:pool")[0], ["proposed"])

    def test_health_names_the_originals_gap(self):
        self.db.sql("UPDATE unit:log_0002 SET document = NONE")
        health = self.adapter.health()["originals"]
        self.assertEqual((health["units_without_original"], health["units_without_original_sample"]), (1, ["log_0002"]))
        self.assertEqual(health["enforcement"], {"expected": 3, "present": 3, "missing": []})


class DemoTests(ServerCase):
    def run_demo(self, dbname=None):
        run_instance = load_module(ROOT / "scripts" / "run_instance.py", "run_instance_for_originals")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run_instance.main([str(DEMO_DIR), "--url", self.url, "--ns", self.ns, "--db", dbname or self.dbname])
        return code, out.getvalue()

    def test_the_demo_runs_end_to_end_with_originals(self):
        code, printed = self.run_demo()
        self.assertEqual(code, 0, printed[-3000:])
        self.assertIn("66 unit(s) linked to a retrievable original, 0 WITHOUT one", printed)
        self.assertIn("49 quote(s) also found verbatim in the original", printed)
        self.assertIn("3 document(s), 66 units (66 linked to a document)", printed)
        self.assertIn("6 refused as required", printed)
        self.assertIn("0 misbehaved", printed)
        self.assertIn("66 with a verified original", printed)
        self.assertIn("original     unit ", printed)
        self.assertEqual(self.db.sql("SELECT VALUE status FROM fact WHERE status = 'confirmed'")[0], ["confirmed"] * 2)

    def test_the_demo_probes_a_blind_caller_under_access_and_compares_the_live_schema(self):
        """Before access is applied every writer is a system user that reads every row, so the blind-caller probe
        could only say "not needed on this database" and V2-21's fix was never exercised by the demo. The runner
        probes again under access, and ends with the live schema compared with the config."""
        code, printed = self.run_demo()
        self.assertEqual(code, 0, printed[-3000:])
        self.assertIn("gates        again under access: 3 rule(s): 3 rejected as required, 3 accepted as required, "
                      "0 misbehaved", printed)
        blind = [line for line in printed.splitlines() if "blind caller" in line]
        self.assertTrue(blind, printed[-3000:])
        self.assertTrue(any("refused True" in line for line in blind), blind)
        self.assertFalse(any("ACCEPTED" in line or "NOT RUN" in line or "not needed on this database" in line
                             for line in blind), blind)
        self.assertIn("assertions   0 assertion edge(s) declared", printed)
        self.assertRegex(printed, r"live schema +\d+ definition\(s\) expected, \d+ live, 0 drift row\(s\)")

    def test_a_definition_nobody_generated_stops_the_demo(self):
        """A stray definition survives every re-apply (OVERWRITE replaces what the config names, nothing else), so it
        is what a hand edit looks like on a re-run. The run must stop on it, naming it."""
        self.db.sql("DEFINE PARAM $stray_threshold VALUE 3;")
        code, printed = self.run_demo()
        self.assertEqual(code, 1, printed[-3000:])
        self.assertIn("UNEXPECTED param stray_threshold", printed)
        self.assertIn("LIVE SCHEMA DRIFT", printed)

    def test_a_graph_that_already_enforces_originals_still_has_its_gates_probed(self):
        """The gate library's probe units carry no originals, so on a graph where enforcement is already applied
        its accept probes would be refused by enforcement, not by the rule under test. The runner probes the same
        generated gates in a scratch database instead, and says so."""
        self.db.script(gates.schema_surql(self.inst))
        self.db.script(originals.enforcement_surql(self.inst))
        code, printed = self.run_demo()
        self.assertEqual(code, 0, printed[-3000:])
        self.assertIn("3 rejected as required, 3 accepted as required, 0 misbehaved (in scratch database", printed)
        self.assertNotIn(self.dbname + "__gate_probes", self.db.sql("INFO FOR NS")[0].get("databases") or {})


if __name__ == "__main__":
    unittest.main()
