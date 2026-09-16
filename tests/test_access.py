# -*- coding: utf-8 -*-
"""Tests for access.py: generation in pure Python; enforcement on a throwaway in-memory SurrealDB
(never a live one), one probe pair per rule (a write refused, the matching write accepted), proof
that a loosened PERMISSIONS clause is caught, and proof that permissions and gates compose.

    python -m unittest tests.test_access -v
"""
import pathlib
import subprocess
import time
import unittest
import urllib.request

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import access, config, gates, surreal_bin  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar.mcp import adapter as contract, instance_adapter, server  # noqa: E402
from xscholar.pipeline import write  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
SURREAL = surreal_bin.find()
PORT = 8032
UNIT_OK = "CREATE unit:u_ok SET type='log_line', text='ok', locator={p: 1}, status='in_window', grade='original_log', hash='h';"


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.inst = config.load(DEMO)
        self.text = access.access_surql(self.inst)

    def test_one_record_access_and_a_permissions_clause_on_every_table(self):
        self.assertIn("DEFINE ACCESS OVERWRITE agent ON DATABASE TYPE RECORD SIGNIN", self.text)
        for table in access.TABLES:
            self.assertIn("ALTER TABLE %s PERMISSIONS" % table, self.text)
        self.assertEqual(self.text.count("FOR delete NONE"), len(access.TABLES))   # deletes are root-only

    def test_rules_from_the_config_are_in_the_clauses(self):
        line = {l.split()[2]: l for l in self.text.splitlines() if l.startswith("ALTER TABLE")}
        self.assertIn("FOR create WHERE $auth.role IN ['loader'] FOR update WHERE $auth.role IN ['loader']", line["label"])
        self.assertIn("($auth.role = 'seat' AND lens = $auth.lens)", line["critique"])
        self.assertIn("FOR create WHERE $auth.role IN ['reader', 'coordinator']", line["fact"])
        self.assertIn("FOR select NONE", line["relies_on"])    # no role reads authority
        self.assertIn("'reader'", line["cites"].split("FOR create")[0])   # reads fact and unit, so reads the edge

    def test_config_refuses_label_writers_other_than_loader_and_unknown_tables(self):
        data = dict(self.inst.data)
        data["access"] = {"roles": {"reader": {"read": ["unit"], "write": ["label"]}}}
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("only the loader writes labels", str(err.exception))
        data["access"] = {"roles": {"reader": {"read": ["unit", "nope"], "write": []}}}
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("unknown table(s) nope", str(err.exception))

    def test_instance_without_access_section_still_loads_and_generates_nothing(self):
        data = {k: v for k, v in self.inst.data.items() if k != "access"}
        inst = config.load(data)
        self.assertEqual(inst.access_roles(), {})
        with self.assertRaises(access.AccessError):
            access.access_surql(inst)


class ServerTests(unittest.TestCase):
    """Real enforcement on a throwaway server: both ways, one probe pair per rule."""

    @classmethod
    def setUpClass(cls):
        if not SURREAL:
            raise unittest.SkipTest("NOT EXECUTED: " + surreal_bin.MISSING)
        if surreal_bin.port_in_use(PORT):
            raise unittest.SkipTest(surreal_bin.occupied(PORT))
        try:   # a foreign server on this port would make every probe run against unknown state
            urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=1)
            raise unittest.SkipTest("NOT EXECUTED: something already listens on %d; refusing to test against it" % PORT)
        except unittest.SkipTest:
            raise
        except Exception:
            pass
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
        cls.n = 0
        cls.ns = "acc%d" % int(time.time() * 1000)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def setUp(self):
        type(self).n += 1
        self.root = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns=self.ns, db="d%d" % self.n)
        self.root.script(gates.schema_surql(self.inst))
        self.root.script(gates.gate_surql(self.inst))
        self.root.script(access.access_surql(self.inst))
        self.root.sql(UNIT_OK + "CREATE conclusion:c1 SET statement='c';", expect=2)
        self.creds = {"reader": access.provision(self.root, self.inst, "reader_1", "reader"),
                      "seat": access.provision(self.root, self.inst, "seat_skeptic", "seat", lens="lens_skeptic"),
                      "loader": access.provision(self.root, self.inst, "loader_1", "loader"),
                      "coordinator": access.provision(self.root, self.inst, "coordinator_1", "coordinator")}
        self.agent = {role: access.connect(c) for role, c in self.creds.items()}

    def denied(self, role, statement):
        with self.assertRaises(dbmod.PermissionDenied, msg="%s: %s was NOT refused" % (role, statement)) as err:
            self.agent[role].sql(statement)
        return str(err.exception)

    def accepted(self, role, statement):
        rows = self.agent[role].sql(statement, expect=1)[0]
        self.assertTrue(rows, "%s: %s returned nothing" % (role, statement))
        return rows

    # ---- one probe pair per rule --------------------------------------------------------------

    def test_reader_proposes_and_cites_but_cannot_critique(self):
        self.accepted("reader", "CREATE fact:f1 SET statement='s'")
        self.accepted("reader", "RELATE fact:f1->cites->unit:u_ok")
        self.denied("reader", "CREATE critique:k SET lens='lens_skeptic', head='h', verdict='survivable'")
        self.assertEqual(self.root.count("critique"), 0)

    def test_seat_critiques_under_its_own_lens_only(self):
        self.accepted("seat", "CREATE critique:k1 SET lens='lens_skeptic', head='h', verdict='survivable'")
        self.denied("seat", "CREATE critique:k2 SET lens='management', head='h', verdict='survivable'")
        self.assertEqual(self.root.count("critique"), 1)

    def test_seat_cannot_confirm_a_fact_and_reader_can(self):
        self.root.sql("CREATE fact:f1 SET statement='s'; RELATE fact:f1->cites->unit:u_ok;", expect=2)
        self.denied("seat", "UPDATE fact:f1 SET status='confirmed'")
        self.assertEqual(self.root.sql("SELECT VALUE status FROM fact:f1")[0], ["proposed"])
        self.accepted("reader", "UPDATE fact:f1 SET status='confirmed'")
        self.assertEqual(self.root.sql("SELECT VALUE status FROM fact:f1")[0], ["confirmed"])

    def test_only_loader_writes_labels(self):
        stmt = "CREATE label:%s SET unit=unit:u_ok, tier=1, issues=[], targets=[], chunk='c', run='r'"
        self.accepted("loader", stmt % "l1")
        self.denied("reader", stmt % "l2")
        self.denied("seat", stmt % "l3")
        self.denied("coordinator", stmt % "l4")
        self.assertEqual(self.root.count("label"), 1)

    def test_nobody_but_root_deletes(self):
        self.root.sql("CREATE fact:f1 SET statement='s'")
        for role in self.agent:
            self.denied(role, "DELETE fact:f1")
        self.assertEqual(self.root.count("fact"), 1)
        self.root.sql("DELETE fact:f1")
        self.assertEqual(self.root.count("fact"), 0)

    def test_a_denied_promotion_is_reported_as_a_permission_not_as_missing_evidence(self):
        self.root.sql("CREATE fact:f1 SET statement='s'; RELATE fact:f1->cites->unit:u_ok;", expect=2)
        seat = write.Writer(self.agent["seat"], self.inst, {"agent_id": "seat_skeptic", "role": "seat", "lens": "lens_skeptic"})
        with self.assertRaises(dbmod.PermissionDenied):
            seat.confirm("f1")
        adapter = instance_adapter.GenericAdapter(self.inst, self.agent["seat"])
        out = server.call("claim_confirm", lambda: adapter.claim_confirm(contract.Provenance("seat_skeptic", "seat", "lens_skeptic"), "f1", []))
        self.assertEqual(out["status"], contract.STATUS_REJECTED_BY_GATE)
        self.assertIn("permissions", out["error"])
        self.assertTrue(out["missing"])

    # ---- composition ---------------------------------------------------------------------------

    def test_permissions_and_gates_compose(self):
        reader = write.Writer(self.agent["reader"], self.inst, {"agent_id": "reader_1", "role": "reader", "lens": None})
        seat = write.Writer(self.agent["seat"], self.inst, {"agent_id": "seat_skeptic", "role": "seat", "lens": "lens_skeptic"})
        coord = write.Writer(self.agent["coordinator"], self.inst, {"agent_id": "coordinator_1", "role": "coordinator", "lens": None})
        reader.propose("f1", "s")
        with self.assertRaises(write.GateRefusal):        # the gate still answers a permitted writer
            reader.confirm("f1")
        reader.confirm("f1", ["u_ok"])
        reader.support("f1", "c1")
        with self.assertRaises(write.GateRefusal) as err:  # permitted to write conclusion, but never attacked
            coord.mark_ready("c1")
        self.assertIn("never attacked", str(err.exception))
        seat.critique("c1", "a head", "survivable")        # the seat's critique, written under its credential
        with self.assertRaises(dbmod.PermissionDenied):   # a seat may not write conclusion even when the gate would pass
            seat.mark_ready("c1")
        coord.mark_ready("c1")                             # counts for the readiness gate
        self.assertEqual(self.root.sql("SELECT VALUE ready FROM conclusion:c1")[0], [True])

    def test_root_still_passes_every_gate_probe_after_access_is_applied(self):
        report = gates.run_probes(self.inst, self.root)
        self.assertEqual(report["misbehaved"], 0, report)
        self.assertEqual(report["rejected_ok"], len(self.inst.gate_rules()))
        self.assertEqual(report["accepted_ok"], len(self.inst.gate_rules()))

    # ---- revocation and the loosened clause ------------------------------------------------------

    def test_revoked_agent_neither_writes_nor_signs_in(self):
        self.agent["seat"].ping()                          # holds a token issued before the revocation
        access.revoke(self.root, "seat_skeptic")
        self.denied("seat", "CREATE critique:k SET lens='lens_skeptic', head='h', verdict='survivable'")
        with self.assertRaises(dbmod.DbError) as err:
            access.connect(self.creds["seat"]).ping()
        self.assertIn("signin refused", str(err.exception))
        with self.assertRaises(access.AccessError):
            access.revoke(self.root, "seat_skeptic")      # revoking nothing is an error, not a pass

    def test_probes_catch_a_loosened_permissions_clause(self):
        """Prove the suite can fail: loosen critique CREATE to any role, the reader's critique succeeds."""
        stmt = "CREATE critique:k SET lens='lens_skeptic', head='h', verdict='survivable'"
        self.denied("reader", stmt)
        self.root.sql("ALTER TABLE critique PERMISSIONS FOR select FULL FOR create FULL FOR update FULL FOR delete NONE")
        self.accepted("reader", stmt)   # the loosened clause lets the denied write through
        self.assertEqual(self.root.count("critique"), 1)
        self.root.sql("DELETE critique:k")
        self.root.script(access.access_surql(self.inst))   # restore
        self.denied("reader", stmt)


if __name__ == "__main__":
    unittest.main()
