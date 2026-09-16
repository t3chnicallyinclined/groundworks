# -*- coding: utf-8 -*-
"""The MCP tools end to end on a throwaway in-memory SurrealDB (never a live one), with generated gates.

Two promises nobody had seen kept through the real tools:
  * health's "a check that cannot run says NOT_EXECUTED, never 0" -- forced here with an agent credential that
    cannot read what a count needs, and cannot read table definitions at all. Measured while writing this: every
    unfiltered `count() GROUP ALL` in health read 0 to ANY record credential, even one that can read the rows
    (SurrealDB 3.1.6), so an agent's health said units 0, critiques_unanswered 0 over a graph that had both.
  * a refused write comes back rejected_by_gate with a `missing` that names what the gate needs -- for an evidence
    gate, the readiness gate, an independence refusal, an originals refusal and an assertion refusal. A refusal
    whose `missing` is empty or names the wrong thing sends an agent into a retry loop that cannot end.

Every test here was first run against a named deliberate break (tests/harness/mutations.json) and failed.

    python -m unittest discover -s tests -p "test_mcp_db.py" -v
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
from xscholar import access, assertions, config, gates, lens, originals, surreal_bin, temporal  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar import store as storemod  # noqa: E402
from xscholar.mcp import adapter as contract  # noqa: E402
from xscholar.mcp import code_state, instance_adapter, server  # noqa: E402
from xscholar.pipeline import loader, write  # noqa: E402
try:  # discovery puts tests/ on the path; `python -m unittest tests.x` does not
    from test_independence_modes import mode_instance  # noqa: E402
    from test_assertions import EDGE, with_assertions  # noqa: E402
    from test_mcp_server import run_tool  # noqa: E402
except ImportError:  # pragma: no cover
    from tests.test_independence_modes import mode_instance  # noqa: E402
    from tests.test_assertions import EDGE, with_assertions  # noqa: E402
    from tests.test_mcp_server import run_tool  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
SURREAL = surreal_bin.find()
PORT = surreal_bin.free_port()   # OS-assigned, like every suite's port
URL = "http://127.0.0.1:%d/sql" % PORT
UNIT = "CREATE unit:%s SET type='log_line', text='%s', locator={line: %d}, status='%s', grade='original_log', hash='h%s';"
PROC = None
_N = [0]


def setUpModule():
    global PROC
    if not SURREAL:
        raise unittest.SkipTest("NOT EXECUTED: " + surreal_bin.MISSING)
    if surreal_bin.port_in_use(PORT):
        raise unittest.SkipTest(surreal_bin.occupied(PORT))
    PROC = subprocess.Popen([str(SURREAL), "start", "--bind", "127.0.0.1:%d" % PORT, "--user", "root", "--pass", "root",
                             "memory"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=1)
            return
        except Exception:
            time.sleep(0.25)
    PROC.kill()
    raise unittest.SkipTest("NOT EXECUTED: throwaway SurrealDB did not start on %d" % PORT)


def tearDownModule():
    if PROC is not None:
        PROC.terminate()          # this process, by its handle: never by name
        try:
            PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROC.kill()


def fresh(inst, *scripts):
    _N[0] += 1
    root = dbmod.Db(url=URL, ns="mcp", db="d%d_%d" % (_N[0], int(time.time() * 1000)))
    for script in scripts:
        root.script(script(inst))
    return root


def verified():
    return code_state.CodeGuard(reader=lambda: code_state.git_state("c" * 40, code_state.VERIFIED_BRANCH))


def serve(adapter, agent_id="mcp_test", role="reader", lens_name=None):
    return server.build_server(adapter, contract.Provenance(agent_id, role, lens_name), guard=verified())


def agent_db(root, inst, agent_id, role, principal=None, lens_name=None):
    return access.connect(access.provision(root, inst, agent_id, role, lens=lens_name, principal=principal,
                                           provisioned_by="test"))


class HealthNotExecutedTests(unittest.TestCase):
    """health through the MCP tool, under credentials that cannot run what a count needs."""

    def setUp(self):
        self.inst = config.load(DEMO)
        self.root = fresh(self.inst, gates.schema_surql, gates.gate_surql, access.access_surql, lens.lens_surql)
        self.root.sql(UNIT % ("u1", "pool exhausted", 1, "in_window", "u1") + UNIT % ("u2", "retry", 2, "in_window", "u2"))
        self.root.sql("CREATE fact:f1 SET statement='s', status='proposed'; "
                      "CREATE conclusion:rc_pool_exhaustion SET statement='c', ready=false; "
                      "CREATE critique:k1 SET head='h', verdict='damaging', answered=false, lens='management', text='t';",
                      expect=3)

    def health(self, adapter):
        out = run_tool(serve(adapter), "health", {})
        self.assertEqual(out["status"], "ok", out)
        return out["data"]

    def test_root_reads_true_numbers_and_nothing_not_executed_in_the_counts(self):
        data = self.health(instance_adapter.create_adapter_for(self.inst, self.root))
        inst = data["instance"]
        self.assertEqual((inst["units"], inst["critiques_unanswered"], inst["conclusions"], inst["conclusions_ready"]),
                         (2, 1, 1, 0))
        self.assertEqual(inst["facts_by_status"], {"proposed": 1})
        self.assertEqual(inst["DRIFT_confirmed_without_valid_cite"], 0)
        self.assertEqual(inst["originals"]["units"], 2)
        counts = ("units", "labels", "facts_by_status", "conclusions", "conclusions_ready", "critiques_unanswered",
                  "DRIFT_confirmed_without_valid_cite")
        self.assertNotIn("NOT_EXECUTED", json.dumps({k: inst[k] for k in counts}))

    def test_a_credential_that_cannot_read_a_table_gets_not_executed_never_zero(self):
        """`loader` reads unit, document, label and conclusion; not fact, critique or cites."""
        adapter = instance_adapter.create_adapter_for(self.inst, agent_db(self.root, self.inst, "loader_1", "loader"),
                                                      role="loader")
        data = self.health(adapter)
        inst = data["instance"]
        for key in ("facts_by_status", "critiques_unanswered", "DRIFT_confirmed_without_valid_cite"):
            with self.subTest(count=key):
                self.assertIsInstance(inst[key], str, "%s read %r under a credential that cannot read it" % (key, inst[key]))
                self.assertTrue(inst[key].startswith("NOT_EXECUTED"), inst[key])
                self.assertIn("loader", inst[key])
        # a table the credential CAN read is counted truly: the unfiltered count read 0 here
        self.assertEqual((inst["units"], inst["conclusions"]), (2, 1))
        self.assertEqual(inst["originals"]["units"], 2)
        # checks that need root say so
        self.assertIn("NOT_EXECUTED", inst["live_schema"])
        self.assertNotIn("missing", inst["live_schema"])
        self.assertIn("NOT_EXECUTED", inst["originals"]["enforcement"])
        # and the summary puts every one of them in front
        paths = [n["path"] for n in data["summary"]["not_executed"]]
        for path in ("facts_by_status", "critiques_unanswered", "DRIFT_confirmed_without_valid_cite",
                     "live_schema.NOT_EXECUTED", "originals.enforcement.NOT_EXECUTED"):
            self.assertIn(path, paths)
        self.assertTrue(data["summary"]["headline"].startswith("ATTENTION"), data["summary"]["headline"])

    def test_a_record_credential_whose_role_is_unknown_never_reads_zero_as_clean(self):
        adapter = instance_adapter.create_adapter_for(self.inst, agent_db(self.root, self.inst, "loader_2", "loader"))
        inst = self.health(adapter)["instance"]
        self.assertTrue(str(inst["critiques_unanswered"]).startswith("NOT_EXECUTED"), inst["critiques_unanswered"])
        self.assertIn("XSCHOLAR_ROLE", inst["critiques_unanswered"])
        self.assertEqual(inst["units"], 2)                    # rows it can see are a real count

    def test_the_launch_environment_gives_the_generic_adapter_its_credential_role(self):
        creds = access.provision(self.root, self.inst, "loader_3", "loader", provisioned_by="test")
        env = dict(creds, XSCHOLAR_INSTANCE=str(DEMO))
        saved = {k: instance_adapter.os.environ.get(k) for k in env}
        try:
            instance_adapter.os.environ.update(env)
            adapter = instance_adapter.create_adapter()
        finally:
            for k, v in saved.items():
                if v is None:
                    instance_adapter.os.environ.pop(k, None)
                else:
                    instance_adapter.os.environ[k] = v
        self.assertEqual(adapter.role, "loader")
        self.assertTrue(str(adapter.health()["critiques_unanswered"]).startswith("NOT_EXECUTED: this server's credential"))


class GateRefusalThroughToolsTests(unittest.TestCase):
    """Refused writes through the real MCP write tools: status rejected_by_gate and a `missing` naming the need."""

    def assertRefused(self, out, *needs, absent=()):
        self.assertEqual(out["status"], "rejected_by_gate", out)
        self.assertTrue(out["missing"], "empty `missing`: %s" % out)
        self.assertTrue(all(isinstance(m, str) and m.strip() for m in out["missing"]), out["missing"])
        text = " ".join(out["missing"])
        for need in needs:
            self.assertIn(need, text)
        for wrong in absent:
            self.assertNotIn(wrong, text)
        return text

    def test_the_evidence_gate_names_the_grade_and_status_and_what_is_cited_now(self):
        inst = config.load(DEMO)
        root = fresh(inst, gates.schema_surql, gates.gate_surql)
        root.sql(UNIT % ("u_in", "pool exhausted", 1, "in_window", "a") + UNIT % ("u_out", "early", 2, "out_of_window", "b"))
        srv = serve(instance_adapter.create_adapter_for(inst, root))
        self.assertEqual(run_tool(srv, "claim_propose", {"slug": "pool", "claim": "the pool was exhausted"})["status"], "ok")
        self.assertRefused(run_tool(srv, "claim_confirm", {"slug": "pool"}), "cited now: nothing", "in_window", "original_log")
        self.assertRefused(run_tool(srv, "claim_confirm", {"slug": "pool", "unit_ids": ["u_out"]}),
                           "u_out (grade original_log, status out_of_window)")
        self.assertEqual(run_tool(srv, "claim_confirm", {"slug": "pool", "unit_ids": ["u_in"]})["status"], "ok")
        # the readiness gate
        root.sql("CREATE conclusion:rc_pool_exhaustion SET statement='c', ready=false;")
        self.assertEqual(run_tool(srv, "claim_support", {"slug": "pool", "conclusion": "rc_pool_exhaustion"})["status"], "ok")
        self.assertRefused(run_tool(srv, "conclusion_mark_ready", {"conclusion": "rc_pool_exhaustion"}),
                           "never attacked", "preserves")

    def test_an_independence_refusal_names_who_may_promote_not_more_evidence(self):
        inst = mode_instance(True)
        root = fresh(inst, gates.schema_surql, gates.gate_surql, access.access_surql)
        root.sql(UNIT % ("u_ok", "pool exhausted", 1, "in_window", "a"))
        proposer = serve(instance_adapter.create_adapter_for(inst, agent_db(root, inst, "proposer_1", "reader")), "proposer_1")
        self.assertEqual(run_tool(proposer, "claim_propose", {"slug": "f1", "claim": "s"})["status"], "ok")
        self.assertRefused(run_tool(proposer, "claim_confirm", {"slug": "f1", "unit_ids": ["u_ok"]}),
                           "has not written it", "hand it to another agent", absent=("whose grade is in",))
        reviewer = serve(instance_adapter.create_adapter_for(inst, agent_db(root, inst, "reviewer_1", "reader")), "reviewer_1")
        out = run_tool(reviewer, "claim_confirm", {"slug": "f1"})
        self.assertEqual(out["status"], "ok", out)

    def test_an_originals_refusal_names_the_original_not_a_grade(self):
        inst = config.load(DEMO)
        root = fresh(inst, gates.schema_surql, gates.gate_surql, temporal.temporal_surql, originals.enforcement_surql)
        root.sql(UNIT % ("u_bare", "pool exhausted", 1, "in_window", "a"))
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        srv = serve(instance_adapter.create_adapter_for(inst, root, store=storemod.FileStore(tmp / "originals")))
        run_tool(srv, "claim_propose", {"slug": "pool", "claim": "the pool was exhausted"})
        self.assertRefused(run_tool(srv, "claim_confirm", {"slug": "pool", "unit_ids": ["u_bare"]}),
                           "u_bare (NO original)", "xscholar.originals verify", absent=("whose grade is in",))

    def test_an_originals_refusal_before_the_datastore_names_the_original_to_restore(self):
        """The datastore still says integrity ok; the file is gone from the store. The adapter re-hashes first."""
        inst = config.load(DEMO)
        root = fresh(inst, gates.schema_surql, gates.gate_surql)
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        store = storemod.FileStore(tmp / "originals")
        data = b"pool exhausted\n"
        sha = store.put(data)
        root.sql("CREATE %s SET sha256 = %s, media_type = 'text/plain', bytes = %d, capture = 'raw', integrity = 'ok', "
                 "original_name = 'app.log'" % (originals.doc_rid(sha), json.dumps(sha), len(data)))
        root.sql(UNIT % ("u_doc", "pool exhausted", 1, "in_window", "a"))
        root.sql("UPDATE unit:u_doc SET document = %s" % originals.doc_rid(sha))
        for path in (tmp / "originals").rglob(sha):
            path.unlink()
        srv = serve(instance_adapter.create_adapter_for(inst, root, store=store))
        run_tool(srv, "claim_propose", {"slug": "pool", "claim": "the pool was exhausted"})
        self.assertRefused(run_tool(srv, "claim_confirm", {"slug": "pool", "unit_ids": ["u_doc"]}),
                           "unit u_doc's original, restored to the store and verified")

    def test_an_assertion_refusal_names_the_contradiction_not_a_grade(self):
        inst = config.load(with_assertions(enforce=True))
        root = fresh(inst, gates.schema_surql, gates.gate_surql, assertions.surql, temporal.temporal_surql)
        work = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        (work / "labels").mkdir()
        rows = {"u1": "redacted", "u2": "in_window"}
        (work / "labels" / "c1.jsonl").write_text("".join(json.dumps({"id": u, "tier": 0}) + "\n" for u in rows),
                                                   encoding="utf-8", newline="\n")
        (work / "units.jsonl").write_text("".join(
            json.dumps({"id": u, "type": "log_line", "text": "line %s" % u, "locator": {"line": i}, "status": st,
                        "grade": "original_log", "hash": "h%s" % u}) + "\n"
            for i, (u, st) in enumerate(sorted(rows.items()))), encoding="utf-8", newline="\n")
        loader.load(work / "units.jsonl", work / "labels", root, inst)
        setup = write.Writer(root, inst, {"agent_id": "setup", "role": "reader", "lens": None})
        setup.propose("f_claim", "a synthetic claim about u1")
        setup.relate(EDGE, "f_claim", "u1", field="status", value="in_window")     # u1 stands redacted
        srv = serve(instance_adapter.create_adapter_for(inst, root))
        text = self.assertRefused(run_tool(srv, "claim_confirm", {"slug": "f_claim", "unit_ids": ["u2"]}),
                                  "contradicts", "fact:f_claim", absent=("whose grade is in",))
        self.assertIn("correct", text)


if __name__ == "__main__":
    unittest.main()
