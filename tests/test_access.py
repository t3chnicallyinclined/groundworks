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
        for table in access.tables(self.inst):
            self.assertIn("ALTER TABLE %s PERMISSIONS" % table, self.text)
        self.assertEqual(self.text.count("FOR delete NONE"), len(access.tables(self.inst)))   # deletes are root-only

    def test_a_declared_table_and_edge_get_permissions_like_a_built_in(self):
        """A declared table the generator skipped would keep SurrealDB's default permissions, which
        is every signed-in agent reading and writing it -- a hole nothing else in the stack closes."""
        from tests.test_gates import declared_instance
        inst = declared_instance(self.inst)
        text = access.access_surql(inst)
        line = {l.split()[2]: l for l in text.splitlines() if l.startswith("ALTER TABLE")}
        self.assertIn("event", line)
        self.assertIn("mentions", line)
        self.assertIn("FOR create WHERE $auth.role IN ['coordinator']", line["event"])
        self.assertIn("FOR create WHERE $auth.role IN ['coordinator']", line["mentions"])
        # an edge is readable when both its ends are; only the coordinator reads `event`
        self.assertIn("FOR select WHERE $auth.role IN ['coordinator']", line["mentions"])
        self.assertEqual(text.count("FOR delete NONE"), len(access.tables(inst)))
        self.assertEqual(len(access.tables(inst)), len(access.tables(self.inst)) + 2)

    def test_a_declared_table_that_carries_a_lens_binds_a_lens_bound_role(self):
        """The rule follows the field. A declared table with a `lens` field that a lens_bound role
        could write under any lens would let a seat write rows under someone else's lens."""
        import copy
        data = copy.deepcopy(self.inst.data)
        data["tables"] = {"reading": {"fields": {"lens": "string", "text": "string"}}}
        data["access"]["roles"]["seat"]["read"].append("reading")
        data["access"]["roles"]["seat"]["write"].append("reading")
        inst = config.load(data)
        self.assertIn("reading", access.lens_tables(inst))
        line = [l for l in access.access_surql(inst).splitlines() if l.startswith("ALTER TABLE reading ")][0]
        self.assertIn("($auth.role = 'seat' AND lens = $auth.lens)", line)

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
        with self.assertRaises(write.GateRefusal) as err:  # V2-8: nothing records the observation yet
            coord.mark_ready("c1")
        self.assertIn("`preserves`", str(err.exception))
        with self.assertRaises(dbmod.PermissionDenied):   # a declared edge binds like a built-in one
            reader.relate("preserves", "u_ok", "c1", how="alert")
        coord.relate("preserves", "u_ok", "c1", how="alert")
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

    # ---- independent promotion: the defect was a lane promoting what it had read once ----------------

    def independent(self, apply_access=True):
        """A fresh database for the demo with `independent: true` on the fact confirmation rule, and
        three credentials: two readers (same role, different identities) and a coordinator."""
        from tests.test_gates import independent_instance
        inst = independent_instance(self.inst)
        type(self).n += 1
        root = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns=self.ns, db="i%d" % self.n)
        root.script(gates.schema_surql(inst))
        root.script(gates.gate_surql(inst))
        agents = {}
        if apply_access:
            root.script(access.access_surql(inst))
            root.sql(UNIT_OK)
            for agent_id, role in (("reader_1", "reader"), ("reader_2", "reader"), ("coordinator_1", "coordinator")):
                agents[agent_id] = access.connect(access.provision(root, inst, agent_id, role))
        return inst, root, agents

    def test_a_proposer_cannot_promote_its_own_row_and_a_second_credential_can(self):
        """The evidence is in place first, so the only thing a refusal can be about is who is writing."""
        inst, root, agents = self.independent()
        agents["reader_1"].sql("CREATE fact:f1 SET statement='s'")
        agents["reader_1"].sql("RELATE fact:f1->cites->unit:u_ok")
        for who, conn in (("its proposer", agents["reader_1"]), ("root", root)):
            ok, message = conn.try_sql("UPDATE fact:f1 SET status='confirmed'")
            self.assertFalse(ok, "%s promoted the row" % who)
            self.assertEqual(gates.independence_rule_in(message), "confirmed_needs_source_grade", message)
        self.assertEqual(root.sql("SELECT VALUE status FROM fact:f1")[0], ["proposed"])
        ok, message = agents["reader_2"].try_sql("UPDATE fact:f1 SET status='confirmed'")
        self.assertTrue(ok, "a second credential of the same role was refused: %s" % message)
        row = root.sql("SELECT status, written_by FROM fact:f1")[0][0]
        self.assertEqual(row, {"status": "confirmed", "written_by": ["agent:reader_1", "agent:reader_2"]})

    def test_the_record_of_who_wrote_a_row_cannot_be_forged(self):
        """`author` is a string the caller supplies. `written_by` is computed by the datastore on every
        write: clearing it, or naming someone else, is discarded -- for an agent and for root."""
        inst, root, agents = self.independent()
        agents["reader_1"].sql("CREATE fact:f1 SET statement='s', author='coordinator_1|role=coordinator'")
        agents["reader_1"].sql("RELATE fact:f1->cites->unit:u_ok")
        agents["reader_1"].sql("UPDATE fact:f1 SET written_by = [agent:coordinator_1]")
        root.sql("UPDATE fact:f1 SET written_by = []")
        self.assertEqual(root.sql("SELECT VALUE written_by FROM fact:f1")[0], [["agent:reader_1"]])
        ok, message = agents["reader_1"].try_sql("UPDATE fact:f1 SET status='confirmed', written_by=[]")
        self.assertFalse(ok, "the proposer promoted its own row by clearing the record in the same write")
        self.assertEqual(gates.independence_rule_in(message), "confirmed_needs_source_grade", message)

    def test_a_row_only_root_wrote_has_no_proposer_and_no_one_may_promote_it(self):
        """The configuration that let the real lane promote freely: everything written as root. Such
        a row names no proposer, so no credential can show it is independent of one."""
        inst, root, agents = self.independent()
        root.sql("CREATE fact:f1 SET statement='s'; RELATE fact:f1->cites->unit:u_ok;", expect=2)
        for conn in (root, agents["reader_2"], agents["coordinator_1"]):
            ok, message = conn.try_sql("UPDATE fact:f1 SET status='confirmed'")
            self.assertFalse(ok, "a row nobody identifiable wrote was promoted")
            self.assertIn("written by []", message)
        ok, message = root.try_sql("CREATE fact:f2 SET statement='s', status='confirmed'")
        self.assertFalse(ok, "root created a row already promoted")
        agents["reader_1"].sql("UPDATE fact:f1 SET note='read it'")          # a credential takes ownership
        ok, message = agents["coordinator_1"].try_sql("UPDATE fact:f1 SET status='confirmed'")
        self.assertTrue(ok, message)

    def test_the_generated_probe_proves_independence_both_ways_and_leaves_nothing_behind(self):
        inst, root, _ = self.independent()
        report = gates.run_probes(inst, root)
        entry = [e for e in report["rules"] if e["rule"] == "confirmed_needs_source_grade"][0]
        self.assertEqual(report["misbehaved"], 0, entry)
        for flag in ("reject", "wrong_evidence_reject", "self_promotion_reject", "root_promotion_reject", "accept"):
            self.assertIs(entry[flag], True, "%s: %s" % (flag, entry))
        self.assertEqual(report["independence_refused_ok"], 1)
        self.assertEqual(report["independence_untested"], 0)
        self.assertEqual(root.sql("SELECT VALUE id FROM agent WHERE record::id(id) CONTAINS 'zz_probe'")[0], [],
                         "probe credentials left behind")
        self.assertEqual(root.sql("SELECT VALUE id FROM fact WHERE record::id(id) CONTAINS 'zz_probe'")[0], [])

    def test_the_probe_goes_red_when_independence_is_taken_out_of_the_datastore(self):
        inst, root, _ = self.independent()
        root.sql("REMOVE EVENT confirmed_needs_source_grade_independent ON TABLE fact;")
        report = gates.run_probes(inst, root)
        entry = [e for e in report["rules"] if e["rule"] == "confirmed_needs_source_grade"][0]
        self.assertIs(entry["self_promotion_reject"], False, entry)
        self.assertIs(entry["root_promotion_reject"], False, entry)
        self.assertFalse(entry["behaved"])
        self.assertGreaterEqual(report["misbehaved"], 1)

    def test_without_credentials_to_sign_in_the_rule_is_reported_untested_by_name(self):
        inst, root, _ = self.independent(apply_access=False)
        report = gates.run_probes(inst, root)
        entry = [e for e in report["rules"] if e["rule"] == "confirmed_needs_source_grade"][0]
        self.assertFalse(entry["behaved"], entry)
        self.assertIn("access is not applied to this database", entry["missing_independence_probe"])
        self.assertEqual(report["independence_untested"], 1)
        others = [e for e in report["rules"] if e["rule"] != "confirmed_needs_source_grade"]
        self.assertTrue(all(e["behaved"] for e in others), "one untestable rule must not stop the others")

    def test_the_writer_explains_a_refusal_as_who_is_writing_not_as_missing_evidence(self):
        inst, root, agents = self.independent()
        reader = write.Writer(agents["reader_1"], inst, {"agent_id": "reader_1", "role": "reader", "lens": None})
        reader.propose("f1", "s")
        with self.assertRaises(write.GateRefusal) as err:
            reader.confirm("f1", ["u_ok"])        # the citation is good: only the identity is wrong
        self.assertEqual(err.exception.rule, "confirmed_needs_source_grade")
        self.assertIn("not its proposer", err.exception.missing)
        other = write.Writer(agents["reader_2"], inst, {"agent_id": "reader_2", "role": "reader", "lens": None})
        self.assertEqual(other.confirm("f1"), "fact:f1")

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
