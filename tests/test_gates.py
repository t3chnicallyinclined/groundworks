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


def declared_instance(base):
    """The demo config plus a node table and an edge type of its own, and a rule that uses both.

    Nothing here is known to the engine: `event`, `mentions`, `stance` and `kind` appear in no engine
    module. If any of it works, it works because the config declared it."""
    data = json.loads(json.dumps(base.data))
    data["tables"] = {"event": {"fields": {"when": "string",
                                           "kind": {"type": "string", "one_of": ["deploy", "alert", "page"]}}}}
    data["edges"]["mentions"] = {
        "from": "unit", "to": "event",
        "fields": {"stance": {"type": "string", "one_of": ["supports", "disputes", "neutral"]},
                   "speaker": {"type": "string", "optional": True}}}
    data["gates"].append({"id": "event_kind_needs_a_source_grade_mention", "table": "event",
                          "field": "kind", "value": "deploy",
                          "requires": [{"edge": "mentions", "from": ["unit"],
                                        "where": "grade IN $confirming AND status IN $usable", "min": 1}],
                          "forbids": [{"edge": "mentions", "where": "status = 'redacted'"}]})
    data["access"]["roles"]["coordinator"]["read"].append("event")
    data["access"]["roles"]["coordinator"]["write"] += ["event", "mentions"]
    return config.load(data)


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

    def test_every_forbids_clause_gets_a_forbidden_evidence_probe(self):
        """`fix` only satisfies `requires` and `wrong` only fires for `cites`: before this, a
        forbids clause was the one half of a gate expression nothing ever wrote a row for."""
        forbidding = 0
        for rule, probe in zip(self.inst.gate_rules(), gates.probes(self.inst)):
            clauses = rule.get("forbids") or []
            forbidding += len(clauses)
            self.assertEqual(len(probe["forbidden"]), len(clauses), probe["rule"])
            self.assertEqual(len(probe["forbids"]), len(clauses), probe["rule"])
            for plan in probe["forbidden"]:
                self.assertTrue(plan["built"], "%s: %s" % (probe["rule"], plan["reason"]))
                self.assertTrue(plan["create"] and plan["relate"] and plan["remove"], plan)
                self.assertTrue(plan["present"].startswith("RETURN count("), plan["present"])
        self.assertTrue(forbidding, "the demo's readiness rule forbids two things")

    def test_a_declared_table_and_edge_reach_the_schema(self):
        """Declared once in the config; defined in the datastore with their enumerations enforced."""
        text = gates.schema_surql(declared_instance(self.inst))
        self.assertIn("DEFINE TABLE OVERWRITE event SCHEMAFULL;", text)
        self.assertIn("DEFINE FIELD OVERWRITE kind ON event TYPE string ASSERT $value IN "
                      "['deploy', 'alert', 'page'];", text)
        self.assertIn("DEFINE TABLE OVERWRITE mentions TYPE RELATION IN unit OUT event SCHEMALESS;", text)
        self.assertIn("DEFINE FIELD OVERWRITE stance ON mentions TYPE string ASSERT $value IN "
                      "['supports', 'disputes', 'neutral'];", text)
        self.assertIn("DEFINE FIELD OVERWRITE speaker ON mentions TYPE option<string>;", text)
        # the built-ins are untouched by an instance declaring its own
        self.assertIn("DEFINE TABLE OVERWRITE unit SCHEMAFULL;", text)
        self.assertNotIn("event", gates.schema_surql(self.inst))

    def test_a_declared_edge_is_probed_exactly_like_a_built_in_one(self):
        """Same reject/accept pair, same wrong-evidence probe for a `where`, same forbidden-evidence
        probe. A declared edge that generated no probe would be a gate nothing ever tested."""
        inst = declared_instance(self.inst)
        probe = [p for p in gates.probes(inst) if p["rule"] == "event_kind_needs_a_source_grade_mention"][0]
        self.assertIn("CREATE event:zz_probe_event_kind_needs_a_source_grade_mention SET", probe["setup"][1])
        self.assertIn("kind='alert'", probe["setup"][1])      # not 'deploy': the value under test
        self.assertEqual(probe["attempt"],
                         "UPDATE event:zz_probe_event_kind_needs_a_source_grade_mention SET kind = 'deploy';")
        self.assertTrue(probe["constrained"])
        self.assertTrue(all(p["built"] for p in probe["evidence"]), probe["evidence"])
        self.assertTrue(all(p["built"] for p in probe["wrong_plans"]), probe["wrong_plans"])
        self.assertTrue(all(p["built"] for p in probe["forbidden"]), probe["forbidden"])
        self.assertTrue(probe["fix"] and probe["wrong"])
        # the relation carries the field the instance declared required, and not the optional one
        relate = [s for s in probe["fix"] if s.startswith("RELATE")]
        self.assertEqual(len(relate), 1, probe["fix"])
        self.assertIn("->mentions->", relate[0])
        self.assertIn("SET stance='supports'", relate[0])
        self.assertNotIn("speaker", relate[0])

    def test_probe_cleanup_names_every_table_the_instance_has(self):
        """Cleanup built from a fixed list would leave a declared table's rows in the database, and
        the next run would probe against them."""
        inst = declared_instance(self.inst)
        for probe in gates.probes(inst):
            for table in inst.tables():
                self.assertTrue(any(stmt.startswith("DELETE %s " % table) for stmt in probe["cleanup"]),
                                "%s: nothing cleans up %s" % (probe["rule"], table))
            self.assertLess(probe["cleanup"].index("DELETE mentions WHERE record::id(in) CONTAINS "
                                                   "'zz_probe_%s' OR record::id(out) CONTAINS 'zz_probe_%s';"
                                                   % (probe["rule"], probe["rule"])),
                            [i for i, s in enumerate(probe["cleanup"]) if s.startswith("DELETE event ")][0],
                            "edges must go before the rows they join")

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
        self.assertEqual(report["forbids_untested"], 0, report)
        self.assertEqual(report["forbidden_refused_ok"],
                         len([r for r in self.inst.gate_rules() if r.get("forbids")]), report)
        # every node table a probe can write, and every edge table it can relate over: the
        # forbidden-evidence probe writes rows the old cleanup list did not name
        for table in self.inst.node_tables():
            self.assertEqual(conn.count(table), 0, "probe rows left behind in %s" % table)
        for edge in self.inst.edge_map():
            self.assertEqual(conn.count(edge), 0, "probe edges left behind in %s" % edge)

    def test_a_declared_table_and_edge_are_enforced_and_leave_nothing_behind(self):
        """The whole contract on a real datastore: an instance's own table and edge get a schema,
        a gate, probes in every direction, and cleanup -- with no engine edit behind them."""
        inst = declared_instance(self.inst)
        conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t", db="declared")
        conn.script(gates.schema_surql(inst))
        conn.script(gates.gate_surql(inst))
        report = gates.run_probes(inst, conn)
        self.assertEqual(report["misbehaved"], 0, json.dumps(report, indent=2))
        self.assertEqual(report["rejected_ok"], len(inst.gate_rules()))
        self.assertEqual(report["accepted_ok"], len(inst.gate_rules()))
        self.assertEqual(report["forbids_untested"], 0, report)
        self.assertEqual(report["requires_untested"], 0, report)
        entry = [r for r in report["rules"] if r["rule"] == "event_kind_needs_a_source_grade_mention"][0]
        self.assertTrue(entry["wrong_evidence_reject"], entry)
        self.assertTrue(entry["forbidden_evidence_reject"], entry)
        for table in inst.tables():
            self.assertEqual(conn.count(table), 0, "probe rows left behind in %s" % table)
        # the declared enumeration is the datastore's, not a guide's
        conn.sql("CREATE event:e1 SET when='now', kind='alert';")
        ok, _ = conn.try_sql("UPDATE event:e1 SET kind = 'something_else'")
        self.assertFalse(ok, "a value outside the declared one_of was accepted")
        conn.sql("DELETE event:e1")

    def test_v2_8_readiness_needs_preservation_and_a_recorded_exception_is_visible(self):
        """ROADMAP V2-8, expressed in the demo config: unpreserved refused, preserved accepted,
        a recorded exception accepted and still readable afterwards."""
        conn = self.fresh("preservation")
        def conclusion(name):
            conn.sql("CREATE conclusion:%s SET statement='s', ready=false;" % name)
            conn.sql("CREATE fact:%s_f SET statement='s', status='proposed';" % name)
            conn.sql("RELATE fact:%s_f->supports->conclusion:%s;" % (name, name))
            conn.sql("CREATE critique:%s_c SET lens='l', head='h', verdict='survivable', answered=true;" % name)
            conn.sql("RELATE critique:%s_c->attacks->conclusion:%s;" % (name, name))
        conn.sql("CREATE unit:v8u SET type='log_line', text='t', locator={p: 1}, status='in_window', "
                 "grade='original_log', hash='h';")
        conclusion("v8a")
        ok, message = conn.try_sql("UPDATE conclusion:v8a SET ready=true")
        self.assertFalse(ok, "a conclusion nothing preserves was marked ready")
        self.assertIn("preserves", message)
        conn.sql("RELATE unit:v8u->preserves->conclusion:v8a SET how='alert';")
        ok, message = conn.try_sql("UPDATE conclusion:v8a SET ready=true")
        self.assertTrue(ok, message)

        conclusion("v8b")                     # the recorded exception: nothing captured it, and we say so
        conn.sql("RELATE unit:v8u->preserves->conclusion:v8b SET how='not_captured';")
        ok, message = conn.try_sql("UPDATE conclusion:v8b SET ready=true")
        self.assertTrue(ok, "a recorded exception must be accepted: %s" % message)
        self.assertEqual(conn.sql("SELECT VALUE how FROM preserves WHERE out = conclusion:v8b", expect=1)[0],
                         ["not_captured"], "the exception must stay visible, not vanish into a bare edge")
        ok, _ = conn.try_sql("RELATE unit:v8u->preserves->conclusion:v8b SET how='made_up'")
        self.assertFalse(ok, "a `how` outside the declared list was accepted")

    def test_v2_9_the_existence_of_a_citation_never_verifies_an_authority(self):
        """ROADMAP V2-9. The refused citation here is usable AND carries a confirming grade: the only
        thing wrong with it is that it is not the official text."""
        conn = self.fresh("authority_official_text")
        conn.sql("CREATE authority:v9 SET citation='c', verified=false;")
        conn.sql("CREATE unit:v9_log SET type='log_line', text='t', locator={p: 1}, status='in_window', "
                 "grade='original_log', hash='h';")
        conn.sql("RELATE authority:v9->verified_from->unit:v9_log SET read_by='probe';")
        ok, message = conn.try_sql("UPDATE authority:v9 SET verified=true")
        self.assertFalse(ok, "a citation to a non-official-text unit verified an authority")
        self.assertIn("official_text", message)
        conn.sql("CREATE unit:v9_stale SET type='log_line', text='t', locator={p: 2}, "
                 "status='out_of_window', grade='official_text', hash='h';")
        conn.sql("RELATE authority:v9->verified_from->unit:v9_stale SET read_by='probe';")
        ok, _ = conn.try_sql("UPDATE authority:v9 SET verified=true")
        self.assertFalse(ok, "official text that is not usable still verified an authority")
        conn.sql("CREATE unit:v9_src SET type='log_line', text='t', locator={p: 3}, status='in_window', "
                 "grade='official_text', hash='h';")
        conn.sql("RELATE authority:v9->verified_from->unit:v9_src SET read_by='probe';")
        ok, message = conn.try_sql("UPDATE authority:v9 SET verified=true")
        self.assertTrue(ok, message)

    def test_a_setup_that_cannot_write_its_row_is_reported_not_raised(self):
        """Before this, a CREATE the datastore refused ended the whole run with a traceback, so the
        other rules were never probed at all and the run had no report to show for it."""
        conn = self.fresh("bad_setup")
        conn.sql("DEFINE FIELD OVERWRITE statement ON fact TYPE string "
                 "ASSERT string::len($value) > 1000;")     # nothing the probe writes can satisfy this
        report = gates.run_probes(self.inst, conn)
        self.assertEqual(len(report["rules"]), len(self.inst.gate_rules()), "the run stopped early")
        entry = [r for r in report["rules"] if r["rule"] == "confirmed_needs_source_grade"][0]
        self.assertTrue(entry["setup_error"], entry)
        self.assertIn("could not write the rows it needs", entry["setup_error"])
        self.assertFalse(entry["behaved"])
        self.assertGreaterEqual(report["misbehaved"], 1)

    def test_probes_catch_a_weakened_gate(self):
        conn = self.fresh("weak")
        # loosen: confirmed no longer needs any citation (what a careless edit would do)
        conn.sql("DEFINE FIELD OVERWRITE status ON fact TYPE string DEFAULT 'proposed' "
                 "ASSERT $value IN %s;" % gates._lit(gates.FACT_STATUSES))
        # the rule is also an event (it holds for a caller who cannot read the evidence): a weakened gate
        # has lost both, or it has not been weakened at all -- tests/test_tamper_evidence.py covers each alone
        conn.sql("REMOVE EVENT %s ON TABLE fact;" % gates.gate_event_name({"id": "confirmed_needs_source_grade"}))
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

    def test_deleting_a_forbids_clause_is_caught_by_the_probe_run(self):
        """The twin of the deleted-`where` case, and it has the same shape, including the blind spot.

        Deleting the forbids clauses from the config and running THAT config's probes stays green --
        asserted below -- because a suite generated from a config cannot notice the config changed.
        An ordinary run of the weakened config exits 0. The baseline is what catches that.

        What this proves is the other half: the strict instance's probes, run against a datastore
        built from the weakened config, find it promoting a conclusion it should refuse, and say so
        per clause. Before the forbidden-evidence probe existed, they could not.
        """
        data = json.loads(json.dumps(self.inst.data))
        for rule in data["gates"]:
            rule.pop("forbids", None)
        weak = config.load(data)
        conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t", db="no_forbids")
        conn.script(gates.schema_surql(weak))
        conn.script(gates.gate_surql(weak))

        own = gates.run_probes(weak, conn)     # honest about the blind spot: it asks for no such probe
        self.assertEqual(own["misbehaved"], 0, own)
        self.assertEqual(own["forbidden_refused_ok"], 0, own)

        strict = gates.run_probes(self.inst, conn)
        self.assertGreaterEqual(strict["misbehaved"], 1)
        entry = [r for r in strict["rules"] if r["rule"] == "root_cause_needs_support_and_attack"][0]
        self.assertFalse(entry["forbidden_evidence_reject"])
        self.assertTrue(entry["forbidden_evidence"], "no forbidden-evidence result was produced at all")
        for result in entry["forbidden_evidence"]:
            self.assertTrue(result["evidence_present"],
                            "%s: the probe did not even write the row it forbids" % result["clause"])
            self.assertIs(result["reject"], False,
                          "%s: the loosened datastore took the write anyway" % result["clause"])

    def test_the_forbidden_row_really_is_the_row_the_clause_forbids(self):
        """The twin of test_the_wrong_evidence_probe_writes_evidence_that_really_is_wrong. A probe
        that wrote a harmless row and met a refusal for some other reason would prove nothing. So the
        datastore is asked, with the clause's own predicate, whether the row the probe wrote matches
        -- and a row of the same table built from the bare template, carrying none of the clause's
        values, must NOT match, or the predicate is not discriminating anything."""
        conn = self.fresh("forbidden_shape")
        checked = 0
        for rule, probe in zip(self.inst.gate_rules(), gates.probes(self.inst)):
            ids = gates._probe_ids(rule)
            for plan in probe["forbidden"]:
                checked += 1
                try:
                    for stmt in probe["setup"] + plan["create"] + plan["relate"]:
                        conn.sql(stmt)
                    self.assertGreaterEqual(conn.sql(plan["present"], expect=1)[0], plan["count"],
                                            "the row the probe wrote does not match %s" % plan["clause"])
                    bland = "%s:zz_probe_%s_bland" % (plan["table"], rule["id"])
                    conn.sql(gates._create(bland, plan["table"], self.inst, unit_id=ids["ok"]))
                    near, far = ((ids["row"], bland) if rule["table"] == self.inst.edge_map()[plan["edge"]][0]
                                 else (bland, ids["row"]))
                    conn.sql("RELATE %s->%s->%s;" % (near, plan["edge"], far))
                    self.assertEqual(conn.sql(plan["present"], expect=1)[0], plan["count"],
                                     "%s matches a row carrying none of its values" % plan["clause"])
                finally:
                    for stmt in probe["cleanup"]:
                        conn.try_sql(stmt)
        self.assertTrue(checked, "the demo's readiness rule forbids something")

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

    def test_the_cli_applies_originals_enforcement_after_its_probes_and_can_run_again(self):
        """`gates --apply` leaves the database enforcing originals, probed both ways. A second run on the same
        database must still probe the gates -- in a scratch database, because the probe units carry no originals
        and enforcement would refuse every accept for the wrong reason -- and leave the scratch database gone."""
        rc, text = self.cli(db="cli_originals")
        self.assertEqual(rc, 0, text[-3000:])
        self.assertIn("ORIGINALS: enforcement applied on 3 rule(s): 6 refused as required, 6 accepted as required, "
                      "0 misbehaved", text)
        conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t", db="cli_originals")
        events = conn.sql("INFO FOR TABLE fact")[0]["events"]
        self.assertIn("confirmed_needs_source_grade_originals", events)
        rc, text = self.cli(db="cli_originals")
        self.assertEqual(rc, 0, text[-3000:])
        self.assertIn("0 misbehaved (in scratch database cli_originals%s" % gates.SCRATCH_PROBE_SUFFIX, text)
        self.assertNotIn("cli_originals" + gates.SCRATCH_PROBE_SUFFIX, conn.sql("INFO FOR NS")[0]["databases"])

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

    # ---- unique: the defect was two rows carrying an identical citation --------------------------

    def test_a_unique_field_refuses_a_duplicate_and_takes_a_distinct_value(self):
        """The demo declares `unique: {authority: [citation]}`. Raw writes first, then the generated
        probe, which must say both halves happened and name the index that refused."""
        conn = self.fresh("unique_raw")
        conn.sql("CREATE authority:u1 SET citation='Ref 12', verified=false;")
        ok, message = conn.try_sql("CREATE authority:u2 SET citation='Ref 12', verified=false;")
        self.assertFalse(ok, "a second authority with the same citation was accepted")
        self.assertIn("`authority_citation_unique`", message)
        ok, message = conn.try_sql("UPDATE authority:u1 SET citation='Ref 12';")   # its own value: not a duplicate
        self.assertTrue(ok, message)
        ok, message = conn.try_sql("CREATE authority:u2 SET citation='Ref 13', verified=false;")
        self.assertTrue(ok, message)
        ok, _ = conn.try_sql("UPDATE authority:u2 SET citation='Ref 12';")
        self.assertFalse(ok, "an update onto an existing citation was accepted")
        conn.sql("DELETE authority:u1; DELETE authority:u2;")
        report = gates.run_probes(self.inst, conn)
        self.assertEqual([(u["table"], u["field"]) for u in report["unique"]], [("authority", "citation")])
        entry = report["unique"][0]
        self.assertTrue(entry["duplicate_reject"] and entry["distinct_accept"] and entry["behaved"], entry)
        self.assertEqual(report["unique_misbehaved"], 0)
        self.assertEqual(report["misbehaved"], 0, report)
        self.assertEqual(conn.count("authority"), 0, "the unique probe left rows behind")

    def test_the_unique_probe_goes_red_when_the_index_is_missing(self):
        """Green on its own proves nothing: take the index away and the probe must say so."""
        conn = self.fresh("unique_gone")
        conn.sql("REMOVE INDEX authority_citation_unique ON authority;")
        report = gates.run_probes(self.inst, conn)
        entry = report["unique"][0]
        self.assertIs(entry["duplicate_reject"], False, entry)
        self.assertFalse(entry["behaved"])
        self.assertEqual(report["unique_misbehaved"], 1)
        rc, text = self.cli(db="unique_gone")     # the CLI re-applies the schema, so the index is back
        self.assertEqual(rc, 0, text)

    def test_raw_write_is_refused_not_just_helper_calls(self):
        conn = self.fresh("raw")
        conn.sql("CREATE fact:zz_raw SET statement='s', status='proposed'")
        ok, message = conn.try_sql("UPDATE fact:zz_raw SET status = 'confirmed'")
        self.assertFalse(ok)
        self.assertIn("status", message.lower())
        conn.sql("DELETE fact:zz_raw")


    def test_a_gate_whose_evidence_is_itself_gated_is_probed_and_behaves(self):
        """V2-18. Before staged evidence: `1 misbehaved`, MISSING PROBE, `could not write the rows it
        needs`, because no probe can create a confirmed fact in one statement."""
        inst = gated_evidence_instance(self.inst)
        conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t", db="staged")
        conn.script(gates.schema_surql(inst))
        conn.script(gates.gate_surql(inst))
        report = gates.run_probes(inst, conn)
        entry = [r for r in report["rules"] if r["rule"] == StagedEvidenceTests.RULE][0]
        self.assertEqual(report["misbehaved"], 0, entry)
        self.assertTrue(entry["reject"] and entry["accept"], entry)
        self.assertIs(entry["wrong_evidence_reject"], True, "an unconfirmed fact must not verify a contention")
        for table in ("fact", "unit", "contention"):
            left = conn.sql("SELECT id FROM %s WHERE record::id(id) CONTAINS 'zz_probe'" % table)[0]
            self.assertEqual(left, [], "staged evidence left rows behind in %s" % table)

    def test_the_staged_probe_catches_that_gate_being_loosened(self):
        """Green on its own proves nothing: loosen the outer gate and the probe must go red."""
        inst = gated_evidence_instance(self.inst)
        conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t", db="staged_loose")
        conn.script(gates.schema_surql(inst))
        conn.script(gates.gate_surql(inst))
        conn.sql("DEFINE FIELD OVERWRITE disposition ON contention TYPE string "
                 "ASSERT $value IN ['verified', 'partly', 'open', 'not_in_record', 'ruled_out'];")
        conn.sql("REMOVE EVENT %s ON TABLE contention;" % gates.gate_event_name({"id": StagedEvidenceTests.RULE}))
        report = gates.run_probes(inst, conn)
        entry = [r for r in report["rules"] if r["rule"] == StagedEvidenceTests.RULE][0]
        self.assertFalse(entry["behaved"], "a loosened gate on gated evidence reported green: %s" % entry)
        self.assertGreaterEqual(report["misbehaved"], 1)

    def test_a_gated_built_in_field_still_refuses_a_value_outside_its_list(self):
        """V2-22(a) on a real datastore: with a gate on unit.status and on unit.grade applied after the schema, a
        value outside the config's lists is refused, and a value inside them is still taken."""
        inst = built_in_field_gate_instance(self.inst)
        conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t", db="builtin_field_gate")
        conn.script(gates.schema_surql(inst))
        conn.script(gates.gate_surql(inst))
        usable, other = inst.usable_statuses()[0], [s for s in inst.status_values() if s not in inst.usable_statuses()][0]
        conn.sql("CREATE unit:u1 SET type=%s, text='t', locator={}, status=%s, grade=%s, hash='h'"
                 % (json.dumps(inst.unit_names()[0]), json.dumps(other), json.dumps(inst.grades()[1])))
        for field in ("status", "grade"):
            with self.subTest(field=field):
                ok, message = conn.try_sql("UPDATE unit:u1 SET %s = 'not_a_value_the_config_allows'" % field)
                self.assertFalse(ok, "unit.%s took a value outside its list once gated" % field)
        ok, message = conn.try_sql("UPDATE unit:u1 SET grade = %s" % json.dumps(inst.grades()[2]))
        self.assertTrue(ok, "a legal, ungated grade was refused: %s" % message)
        ok, _ = conn.try_sql("UPDATE unit:u1 SET status = %s" % json.dumps(usable))
        self.assertFalse(ok, "the gate itself stopped holding: an uncited unit reached %s" % usable)
        # the demo's own gated fields keep their restrictions
        demo = self.fresh("builtin_field_demo")
        demo.sql("CREATE fact:f1 SET statement='s'; CREATE conclusion:c1 SET statement='s'; "
                 "CREATE authority:a1 SET citation='c'")
        for statement in ("UPDATE fact:f1 SET status = 'not_a_status'", "UPDATE conclusion:c1 SET ready = 'yes'",
                          "UPDATE authority:a1 SET verified = 'yes'"):
            with self.subTest(statement=statement):
                ok, _ = demo.try_sql(statement)
                self.assertFalse(ok, "accepted: %s" % statement)


def built_in_field_gate_instance(base):
    """The demo config plus a gate on unit.status and one on unit.grade: built-in fields whose enumerations come
    from the config (`status.values`, `fidelity.order`), not from the engine. A unit reaches `in_window` or the
    first grade only when a confirmed fact cites it. Nothing about the rule matters here except its field."""
    data = json.loads(json.dumps(base.data))
    for rule_id, field, value in (("unit_status_needs_a_confirmed_citation", "status", base.usable_statuses()[0]),
                                  ("unit_grade_needs_a_confirmed_citation", "grade", base.grades()[0])):
        data["gates"].append({"id": rule_id, "table": "unit", "field": field, "value": value,
                              "requires": [{"edge": "cites", "where": "status = 'confirmed'", "min": 1}]})
    return config.load(data)


def final_field_definition(text, table, field):
    """The LAST `DEFINE FIELD OVERWRITE <field> ON <table>` in applied order: the one the datastore keeps."""
    found = [line for line in text.splitlines()
             if re.match(r"^DEFINE FIELD OVERWRITE %s ON %s TYPE " % (re.escape(field), re.escape(table)), line)]
    return found[-1] if found else None


class BuiltInFieldGateTests(unittest.TestCase):
    """ROADMAP V2-22(a). A gate is a DEFINE FIELD OVERWRITE applied after the schema, so it replaces the field's
    whole definition. For a DECLARED field b52ea90 restated the type and `one_of`; a BUILT-IN field whose list
    comes from the config was restated as a bare `TYPE string`, so a gate on unit.status kept the gate condition
    and lost `$value IN [statuses]` -- the field then took any string."""

    def setUp(self):
        self.demo = config.load(DEMO)
        self.inst = built_in_field_gate_instance(self.demo)
        self.applied = gates.schema_surql(self.inst) + gates.gate_surql(self.inst)

    def test_a_gated_built_in_field_keeps_the_enumeration_the_config_gives_it(self):
        for field, values in (("status", self.inst.status_values()), ("grade", self.inst.grades())):
            with self.subTest(field=field):
                line = final_field_definition(self.applied, "unit", field)
                self.assertIn("_needs_a_confirmed_citation", line, "the gate is not the final definition: %s" % line)
                self.assertIn("$value IN %s" % gates._lit(values), line,
                              "the gate replaced unit.%s and dropped its own list: %s" % (field, line))

    def test_the_demo_gated_fields_keep_their_restrictions(self):
        applied = gates.schema_surql(self.demo) + gates.gate_surql(self.demo)
        fact = final_field_definition(applied, "fact", "status")
        self.assertIn("$value IN %s" % gates._lit(gates.FACT_STATUSES), fact)
        for table, field in (("conclusion", "ready"), ("authority", "verified")):
            with self.subTest(field="%s.%s" % (table, field)):
                line = final_field_definition(applied, table, field)
                self.assertIn(" TYPE bool DEFAULT false ASSERT ", line)

    def test_a_gate_on_a_field_the_engine_cannot_restate_is_refused_by_name(self):
        data = json.loads(json.dumps(self.demo.data))
        data["gates"].append({"id": "unknown_field_rule", "table": "fact", "field": "no_such_field", "value": "x",
                              "requires": [{"edge": "cites", "min": 1}]})
        with self.assertRaises(gates.GateError) as raised:
            gates.gate_surql(config.load(data))
        self.assertIn("unknown_field_rule", str(raised.exception))


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

    def test_a_forbidden_evidence_flag_that_went_from_true_to_false_is_caught(self):
        rules = [r["id"] for r in self.inst.gate_rules()]
        was = {"rules": [{"rule": r, "reject": True, "accept": True, "wrong_evidence_reject": True,
                          "forbidden_evidence_reject": True} for r in rules]}
        baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst), "report": was}
        now = {"rules": [{"rule": r, "reject": True, "accept": True, "wrong_evidence_reject": True,
                          "forbidden_evidence_reject": None} for r in rules]}
        failures = gates.compare_baseline(baseline, now, gates.spec(self.inst))
        self.assertTrue(any("forbidden_evidence_reject was true in the baseline, now None" in f
                            for f in failures), failures)

    def test_a_rule_that_stopped_producing_a_probe_result_is_caught(self):
        """Reading only the entries that arrived cannot notice the one that did not."""
        rules = [r["id"] for r in self.inst.gate_rules()]
        was = {"rules": [{"rule": r, "reject": True, "accept": True, "wrong_evidence_reject": True} for r in rules]}
        baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst), "report": was}
        failures = gates.compare_baseline(baseline, {"rules": []}, gates.spec(self.inst))
        self.assertEqual(len(failures), len(rules), failures)
        self.assertTrue(all("no probe result" in f for f in failures), failures)

    def test_repointing_a_declared_edge_at_another_table_is_a_weakening(self):
        """Every clause reads the same and every minimum is the same; the edge now reaches somewhere
        else, so every rule written on it gates something the baseline never saw."""
        inst = declared_instance(self.inst)
        baseline = {"instance": inst.id, "spec": gates.spec(inst), "report": {"rules": []}}
        data = json.loads(json.dumps(inst.data))
        data["edges"]["preserves"]["from"] = "critique"
        failures = gates.compare_baseline(baseline, None, gates.spec(config.load(data)))
        self.assertTrue(any("edge preserves now joins critique and conclusion" in f for f in failures),
                        failures)

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
        """The wrong-evidence row must carry values the clause EXCLUDES. A row that still matches
        would make the sharper probe a second copy of the accept probe: it would run, report a flag,
        and test nothing. Built per clause now, so it holds for a declared edge too."""
        checked = 0
        for probe in gates.probes(declared_instance(self.inst)):
            for plan in probe["wrong_plans"]:
                checked += 1
                self.assertTrue(plan["built"], "%s: %s" % (probe["rule"], plan["reason"]))
                self.assertEqual(len(plan["create"]), plan["count"], plan)
                terms, _ = gates._where_terms(plan["where"])
                for creation in plan["create"]:
                    written = dict(re.findall(r"(\w+)=('[^']*'|true|false|-?\d+)", creation))
                    outside = [f for f, allowed in terms if written.get(f) not in allowed]
                    self.assertTrue(outside, "%s: %s satisfies every term of %r, so the sharper probe "
                                             "is a second copy of the accept probe"
                                             % (probe["rule"], creation, plan["where"]))
                    # and the fix row for the same clause satisfies it, or the pair means nothing
                    fix = [p for p in probe["evidence"] if p["clause"] == plan["clause"]][0]
                    for good in fix["create"]:
                        kept = dict(re.findall(r"(\w+)=('[^']*'|true|false|-?\d+)", good))
                        self.assertEqual([f for f, allowed in terms if kept.get(f) not in allowed], [], good)
                    self.assertTrue(set(plan["rows"]).isdisjoint(fix["rows"]), (plan["rows"], fix["rows"]))
        self.assertGreaterEqual(checked, 3, "the demo and the declared edge both constrain their evidence")

    def test_a_constraint_the_generator_cannot_turn_around_is_reported_untested(self):
        """A wrong-evidence row exists only where the engine knows another value for the field the
        clause pins. Where it does not -- a predicate it cannot read, or a field with no known set of
        values -- the probe must be REPORTED untested, never treated as not applicable."""
        for where, expected in (("string::len(statement) > 3", "field = literal"),
                                ("author = 'someone'", "knows no other value for author")):
            with self.subTest(where=where):
                data = json.loads(json.dumps(self.inst.data))
                for clause in data["gates"][1]["requires"]:
                    if clause["edge"] == "supports":
                        clause["where"] = where
                moved = config.load(data)
                probe = [p for p in gates.probes(moved)
                         if p["rule"] == "root_cause_needs_support_and_attack"][0]
                self.assertTrue(probe["constrained"], "a requires clause with a where constrains the evidence")
                self.assertEqual(probe["wrong"], [], "a row was written for a clause it cannot fail")
                self.assertFalse(probe["wrong_plans"][0]["built"])
                self.assertIn(expected, probe["wrong_plans"][0]["reason"])
                entry = {"rule": probe["rule"], "reject": True, "accept": True,
                         "wrong_evidence_reject": None,
                         "wrong_evidence": [{"clause": p["clause"], "built": p["built"],
                                             "reason": p["reason"], "really_fails": None}
                                            for p in probe["wrong_plans"]]}
                self.assertFalse(gates.judge(entry, probe))
                self.assertIn(where, entry["missing_probe"])
                self.assertIn(expected, entry["missing_probe"])

    def test_evidence_the_accept_probe_cannot_build_is_reported_untested(self):
        """The accept half of the pair. A requires clause whose evidence cannot be built means the
        accept probe proved nothing -- and a green run over it is the defect this file exists for."""
        data = json.loads(json.dumps(self.inst.data))
        data["gates"][0]["requires"][0]["where"] = "grade = 'a' OR grade = 'b'"
        probe = [p for p in gates.probes(config.load(data))
                 if p["rule"] == "confirmed_needs_source_grade"][0]
        self.assertFalse(probe["evidence"][0]["built"])
        self.assertEqual(probe["fix"], [], "nothing may be written for a clause it cannot satisfy")
        entry = {"rule": probe["rule"], "reject": True, "accept": False, "wrong_evidence_reject": None,
                 "evidence": [{"clause": p["clause"], "built": False, "reason": p["reason"],
                               "satisfied": None} for p in probe["evidence"]]}
        self.assertFalse(gates.judge(entry, probe))
        self.assertIn("field = literal", entry["missing_accept_probe"])

    def test_evidence_the_datastore_says_does_not_match_is_reported_untested(self):
        """The rows were written, the write was accepted -- but the clause does not match them, so
        the gate took the write for some other reason and the accept probe measured nothing."""
        probe = {"constrained": False, "constraints": [], "forbids": []}
        entry = {"rule": "r", "reject": True, "accept": True, "wrong_evidence_reject": None,
                 "evidence": [{"clause": "supports", "built": True, "reason": "", "satisfied": False}]}
        self.assertFalse(gates.judge(entry, probe))
        self.assertIn("do not match the clause", entry["missing_accept_probe"])

    def test_wrong_evidence_that_did_not_actually_fail_the_clause_is_reported(self):
        """The mirror of `evidence_present` on the forbidden probe: a refusal met while the wrong
        row still satisfies the clause is a refusal for some other reason."""
        probe = {"constrained": True, "constraints": ["grade IN ['x']"], "forbids": []}
        entry = {"rule": "r", "reject": True, "accept": True, "wrong_evidence_reject": True,
                 "wrong_evidence": [{"clause": "cites where grade IN ['x']", "built": True,
                                     "reason": "", "really_fails": False}]}
        self.assertFalse(gates.judge(entry, probe))
        self.assertIn("did not fail the clause", entry["missing_probe"])

    def test_a_probe_whose_setup_failed_may_not_report_behaviour(self):
        """Flags from a probe that never wrote the row it promotes describe some other row. The two
        flags are set here on purpose: without the guard this entry reads as a working gate."""
        entry = {"rule": "r", "reject": True, "accept": True, "wrong_evidence_reject": None,
                 "setup_error": "the probe could not write the rows it needs: boom"}
        self.assertFalse(gates.judge(entry, {"constrained": False, "constraints": [], "forbids": []}))
        self.assertFalse(entry["behaved"])


    def test_a_forbids_clause_that_cannot_be_built_is_reported_untested(self):
        """Only a conjunction of equalities can be turned into a row. Anything else must be REPORTED:
        a clause that quietly generated no probe is exactly how the wrong-evidence probe went away."""
        data = json.loads(json.dumps(self.inst.data))
        data["gates"][1]["forbids"] = [{"edge": "attacks",
                                        "where": "verdict = 'fatal' OR verdict = 'damaging'"}]
        probe = [p for p in gates.probes(config.load(data))
                 if p["rule"] == "root_cause_needs_support_and_attack"][0]
        self.assertEqual(len(probe["forbidden"]), 1, "the clause must still produce a plan")
        plan = probe["forbidden"][0]
        self.assertFalse(plan["built"])
        self.assertIn("field = literal", plan["reason"])
        self.assertEqual(plan["create"], [], "nothing may be written for a clause it cannot satisfy")

        entry = {"rule": probe["rule"], "reject": True, "accept": True, "wrong_evidence_reject": None,
                 "forbidden_evidence": [{"clause": plan["clause"], "built": False,
                                         "reason": plan["reason"]}]}
        self.assertFalse(gates.judge(entry, probe), "an unbuilt forbids probe must not report green")
        self.assertIn("field = literal", entry["missing_forbidden_probe"])
        self.assertIsNone(entry["forbidden_evidence_reject"])

    def test_the_where_parser_refuses_what_it_cannot_turn_into_a_row(self):
        """It must never guess. A predicate it half-understands would write the wrong row and then
        read a refusal for the wrong reason."""
        for where, fields in (("verdict = 'fatal'", {"verdict": "'fatal'"}),
                              ("verdict = 'damaging' AND answered = false",
                               {"verdict": "'damaging'", "answered": "false"}),
                              ("grade IN ['a', 'b'] AND status IN ['c']",
                               {"grade": "'a'", "status": "'c'"}),
                              ("", {})):
            self.assertEqual(gates._where_assignments(where), (fields, None), where)
        for where in ("verdict = 'a' OR verdict = 'b'", "tier >= 2", "verdict != 'fatal'",
                      "issues CONTAINS 'config'", "grade IN []", "verdict = other_field",
                      "verdict = 'a' AND verdict = 'b'", "string::len(head) > 3"):
            values, why = gates._where_assignments(where)
            self.assertIsNone(values, "%r was turned into a row it may not describe" % where)
            self.assertTrue(why, where)


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

    def test_a_rule_that_forbids_something_with_no_forbidden_result_is_untested(self):
        """The mirror of the constrained/missing_probe rule, on the other half of the expression."""
        entry = self.entry(forbidden_evidence=[])
        probe = {"constrained": False, "constraints": [], "forbids": ["attacks where verdict = 'fatal'"]}
        self.assertFalse(gates.judge(entry, probe), "a rule that forbids something and was never "
                                                    "probed for it must not report green")
        self.assertIn("verdict = 'fatal'", entry["missing_forbidden_probe"])
        self.assertIsNone(entry["forbidden_evidence_reject"])

    def test_both_directions_of_the_forbidden_probe_are_required(self):
        """Refusing the write while the forbidden row is there proves nothing on its own: a gate that
        refused the promotion under every condition would pass. Taking the row away must let the same
        write through, and the row must have been the row the clause names."""
        probe = {"constrained": False, "constraints": [], "forbids": ["attacks where verdict = 'fatal'"]}
        good = {"clause": "attacks where verdict = 'fatal'", "built": True, "evidence_present": True,
                "reject": True, "cleared": True, "restored_accept": True}
        entry = self.entry(forbidden_evidence=[dict(good)])
        self.assertTrue(gates.judge(entry, probe), entry)
        self.assertTrue(entry["forbidden_evidence_reject"])
        for flag in ("evidence_present", "reject", "cleared", "restored_accept"):
            entry = self.entry(forbidden_evidence=[dict(good, **{flag: False})])
            self.assertFalse(gates.judge(entry, probe), "%s False must not count as behaviour" % flag)
            self.assertFalse(entry["forbidden_evidence_reject"])

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



def gated_evidence_instance(base):
    """A rule whose required evidence is ITSELF behind a gate.

    `contention.disposition = 'verified'` requires a rests_on edge to a CONFIRMED fact -- and `confirmed`
    is gated on the demo's own citation rule. Before staged evidence, no probe could write a confirmed
    fact in one statement, so this rule could not be probed at all (V2-18). Built from a pattern another
    session invented and declared through the tables:/edges: contract.
    """
    data = json.loads(json.dumps(base.data))
    data.setdefault("tables", {})["contention"] = {"fields": {
        "said": "string", "asserts": "string",
        "disposition": {"type": "string", "one_of": ["verified", "partly", "open", "not_in_record", "ruled_out"]},
        "why": {"type": "string", "optional": True}}}
    data["edges"]["rests_on"] = {"from": "contention", "to": "fact"}
    data["gates"].append({"id": "contention_verified_needs_a_confirmed_fact", "table": "contention",
                          "field": "disposition", "value": "verified",
                          "requires": [{"edge": "rests_on", "to": ["fact"], "where": "status = 'confirmed'", "min": 1}]})
    data["access"]["roles"]["coordinator"]["read"].append("contention")
    data["access"]["roles"]["coordinator"]["write"] += ["contention", "rests_on"]
    return config.load(data)


class StagedEvidenceTests(unittest.TestCase):
    """V2-18: a gate whose evidence is itself gated is probed through the real inner gate."""

    RULE = "contention_verified_needs_a_confirmed_fact"

    def setUp(self):
        self.inst = gated_evidence_instance(config.load(DEMO))

    def probe(self):
        return [p for p in gates.probes(self.inst) if p["rule"] == self.RULE][0]

    def test_the_evidence_is_built_instead_of_reported_unbuildable(self):
        plan = self.probe()["evidence"][0]
        self.assertTrue(plan["built"], plan["reason"])

    def test_the_gated_row_is_created_unpromoted_then_promoted_by_the_real_gate(self):
        """The order IS the fix: a fact created already confirmed is refused by its own gate."""
        fix = self.probe()["fix"]
        fact_create = next(i for i, s in enumerate(fix) if s.startswith("CREATE fact:"))
        promote = next(i for i, s in enumerate(fix) if s.startswith("UPDATE fact:") and "'confirmed'" in s)
        cite = next(i for i, s in enumerate(fix) if "->cites->" in s)
        self.assertNotIn("'confirmed'", fix[fact_create], "created already promoted: its own gate refuses it")
        self.assertLess(fact_create, cite)
        self.assertLess(cite, promote, "promoted before the evidence its gate requires existed")
        self.assertLess(promote, next(i for i, s in enumerate(fix) if "->rests_on->" in s))

    def test_nested_rows_carry_the_outer_rule_prefix_so_cleanup_removes_them(self):
        for statement in self.probe()["fix"]:
            if statement.startswith("CREATE "):
                self.assertIn("zz_probe_%s" % self.RULE, statement.split(" SET ")[0])

    def test_a_cycle_of_gated_evidence_is_reported_not_followed_forever(self):
        """Rule A's evidence gated by B, B's by A. Must terminate, and must say so by name."""
        data = json.loads(json.dumps(self.inst.data))
        data["edges"]["backs"] = {"from": "fact", "to": "contention"}
        for rule in data["gates"]:
            if rule["id"] == "confirmed_needs_source_grade":
                rule["requires"] = [{"edge": "backs", "to": ["contention"], "where": "disposition = 'verified'", "min": 1}]
        data["access"]["roles"]["coordinator"]["write"].append("backs")
        cyclic = config.load(data)
        plan = [p for p in gates.probes(cyclic) if p["rule"] == self.RULE][0]["evidence"][0]
        self.assertFalse(plan["built"], "a cycle must not be reported as built evidence")
        self.assertIn("chain", plan["reason"])


def independent_instance(base, rule_id="confirmed_needs_source_grade"):
    """The demo config with one rule requiring an independent promoter. Nothing else changes."""
    data = json.loads(json.dumps(base.data))
    for rule in data["gates"]:
        if rule["id"] == rule_id:
            rule["independent"] = True
    return config.load(data)


class UniqueDeclarationTests(unittest.TestCase):
    """`unique:` in the config, without a database."""

    def setUp(self):
        self.inst = config.load(DEMO)

    def load_with(self, unique, tables=None):
        data = json.loads(json.dumps(self.inst.data))
        data["unique"] = unique
        if tables:
            data["tables"] = tables
        return config.load(data)

    def test_a_unique_field_the_table_does_not_have_is_refused_by_name(self):
        """An index on a field no row carries is accepted by the datastore and enforces nothing."""
        for unique, words in (({"authority": ["citaton"]}, "unique.authority: 'citaton' is not a field of authority"),
                              ({"event": ["summary"]}, "unique.event: 'summary' is not a field of event"),
                              ({"cites": ["author"]}, "unique.cites: 'cites' is an edge type"),
                              ({"nowhere": ["x"]}, "unique.nowhere: unknown table 'nowhere'")):
            with self.subTest(unique=unique):
                with self.assertRaises(config.ConfigError) as err:
                    self.load_with(unique, tables={"event": {"fields": {"when": "string"}}})
                self.assertIn(words, str(err.exception))
        inst = self.load_with({"authority": ["citation"], "event": ["when"]},
                              tables={"event": {"fields": {"when": "string"}}})
        self.assertIn("DEFINE INDEX OVERWRITE event_when_unique ON event FIELDS when UNIQUE;", gates.schema_surql(inst))
        self.assertIn("DEFINE INDEX OVERWRITE authority_citation_unique ON authority FIELDS citation UNIQUE;",
                      gates.schema_surql(inst))

    def test_the_built_in_field_list_matches_the_schema_it_validates_against(self):
        """config.BUILT_IN_FIELDS exists so validation needs no Instance. If it drifts from the schema,
        a real field is refused or a missing one is indexed."""
        emitted = {}
        for line in gates.schema_surql(self.inst).splitlines():
            found = re.match(r"DEFINE FIELD OVERWRITE (\w+) ON (\w+) ", line)
            if found and found.group(2) in config.BUILT_IN_NODE_TABLES:
                emitted.setdefault(found.group(2), set()).add(found.group(1))
        self.assertEqual({t: set(f) for t, f in config.BUILT_IN_FIELDS.items()}, emitted)

    def test_unique_is_human_owned_when_declared(self):
        data = json.loads(json.dumps(self.inst.data))
        data["human_owned"].remove("unique")
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("human_owned must include unique", str(err.exception))

    def test_the_baseline_catches_a_removed_unique_field(self):
        baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst), "report": {"rules": []}}
        self.assertEqual(baseline["spec"]["unique"], {"authority": ["citation"]})
        data = json.loads(json.dumps(self.inst.data))
        data.pop("unique")
        data["human_owned"].remove("unique")
        failures = gates.compare_baseline(baseline, None, gates.spec(config.load(data)))
        self.assertIn("unique authority.citation is in the baseline and gone from the config: duplicates of it "
                      "are no longer refused", failures)

    def test_the_baseline_catches_a_unique_probe_that_went_quiet_or_red(self):
        rules = [{"rule": r["id"], "reject": True, "accept": True, "wrong_evidence_reject": True}
                 for r in self.inst.gate_rules()]
        good = {"table": "authority", "field": "citation", "duplicate_reject": True, "distinct_accept": True}
        baseline = {"instance": self.inst.id, "spec": gates.spec(self.inst),
                    "report": {"rules": rules, "unique": [good]}}
        silent = gates.compare_baseline(baseline, {"rules": rules, "unique": []}, gates.spec(self.inst))
        self.assertIn("unique authority.citation was probed in the baseline and this run produced no probe "
                      "result for it", silent)
        red = gates.compare_baseline(baseline, {"rules": rules, "unique": [dict(good, duplicate_reject=False)]},
                                     gates.spec(self.inst))
        self.assertIn("unique authority.citation: duplicate_reject was true in the baseline, now False", red)

    def test_probe_rows_of_one_table_never_collide_on_its_unique_index(self):
        """A rule needing two evidence rows of a table with a unique field: identical template values
        would be refused by the index, and the probe would report a setup error for the wrong reason."""
        data = json.loads(json.dumps(self.inst.data))
        data["unique"] = {"fact": ["statement"], "critique": ["head"]}
        data["gates"][1]["requires"][0]["min"] = 2          # two supporting facts
        inst = config.load(data)
        probe = [p for p in gates.probes(inst) if p["rule"] == "root_cause_needs_support_and_attack"][0]
        statements = [re.search(r"statement=('[^']*')", s).group(1) for s in probe["fix"] if s.startswith("CREATE fact:")]
        self.assertEqual(len(statements), 2, probe["fix"])
        self.assertEqual(len(set(statements)), 2, "two probe facts share a unique statement: %s" % statements)


class IndependenceDeclarationTests(unittest.TestCase):
    """`independent: true` on a gate rule, without a database."""

    def setUp(self):
        self.inst = config.load(DEMO)

    def test_independence_needs_credentials_that_can_write_the_table(self):
        """Root has no identity, so without `access:` nothing could ever make the change."""
        data = json.loads(json.dumps(independent_instance(self.inst).data))
        data.pop("access")
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("gate confirmed_needs_source_grade: independent: true needs an access: section", str(err.exception))
        data = json.loads(json.dumps(self.inst.data))
        data["gates"][2]["independent"] = True             # authority: no demo role reads or writes it
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("gate authority_verified_needs_official_text: independent: true, but no access role both "
                      "reads and writes authority", str(err.exception))
        data["gates"][2]["independent"] = "yes please"
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("independent must be true or false", str(err.exception))

    def test_independence_is_an_event_over_a_record_the_caller_cannot_write(self):
        text = gates.gate_surql(independent_instance(self.inst))
        self.assertIn("DEFINE FIELD OVERWRITE written_by ON fact TYPE array<record> VALUE array::union($before ?? [], "
                      "IF $auth = NONE { [] } ELSE { [$auth] });", text)
        event = [l for l in text.splitlines() if l.startswith("DEFINE EVENT OVERWRITE confirmed_needs_source_grade_independent")]
        self.assertEqual(len(event), 1, text)
        for part in ("WHEN $after.status = 'confirmed' AND ($before.status ?? NONE) != 'confirmed'",
                     "$auth = NONE", "array::len($before.written_by ?? []) = 0", "$auth IN ($before.written_by ?? [])",
                     "THROW 'independent promotion refused by rule confirmed_needs_source_grade:"):
            self.assertIn(part, event[0])
        self.assertNotIn("written_by", gates.gate_surql(self.inst), "an instance that declares nothing gets nothing")

    def test_an_independence_only_rule_is_allowed_and_asserts_no_clause(self):
        data = json.loads(json.dumps(self.inst.data))
        data["gates"].append({"id": "noted_by_another", "table": "fact", "field": "status", "value": "inferred",
                              "independent": True})
        inst = config.load(data)
        text = gates.gate_surql(inst)
        self.assertIn("noted_by_another_independent", text)
        self.assertNotIn("'inferred' OR", text)

    def test_judge_needs_both_independence_refusals(self):
        """A rule requiring an independent promoter whose writer or root was never refused, or was
        refused for some other reason (recorded as False), must not report green."""
        probe = {"constrained": False, "constraints": [], "forbids": [], "independent": True}
        base = {"rule": "r", "reject": True, "accept": True, "wrong_evidence_reject": None}
        self.assertTrue(gates.judge(dict(base, self_promotion_reject=True, root_promotion_reject=True), probe))
        for flags in ({"self_promotion_reject": False, "root_promotion_reject": True},
                      {"self_promotion_reject": True, "root_promotion_reject": False},
                      {"self_promotion_reject": True}, {}):
            with self.subTest(flags=flags):
                entry = dict(base, **flags)
                self.assertFalse(gates.judge(entry, probe), entry)
        entry = dict(base)
        gates.judge(entry, probe)
        self.assertIn("did not try the promotion as the row's own writer and as root", entry["missing_independence_probe"])

    def test_the_baseline_catches_independence_switched_off_or_its_probe_going_red(self):
        inst = independent_instance(self.inst)
        rules = [{"rule": r["id"], "reject": True, "accept": True, "wrong_evidence_reject": True,
                  "self_promotion_reject": True, "root_promotion_reject": True} for r in inst.gate_rules()]
        baseline = {"instance": inst.id, "spec": gates.spec(inst), "report": {"rules": rules}}
        failures = gates.compare_baseline(baseline, None, gates.spec(self.inst))
        self.assertIn("rule confirmed_needs_source_grade no longer requires an independent promoter: the "
                      "credential that wrote a row may now promote it itself", failures)
        now = [dict(r, self_promotion_reject=None) for r in rules]
        failures = gates.compare_baseline(baseline, {"rules": now}, gates.spec(inst))
        self.assertIn("rule confirmed_needs_source_grade: self_promotion_reject was true in the baseline, now None",
                      failures)
        self.assertNotIn("independent", json.dumps(gates.spec(self.inst)["rules"]),
                         "a baseline taken before independence existed must still read the same")

    def test_staged_evidence_behind_an_independent_rule_is_reported_not_built(self):
        """V2-18 stages a gated evidence row by promoting it on the probe's own connection. Past an
        independence rule that promotion cannot be made, so the plan must say so by name."""
        inst = independent_instance(gated_evidence_instance(self.inst))
        plan = [p for p in gates.probes(inst) if p["rule"] == StagedEvidenceTests.RULE][0]["evidence"][0]
        self.assertFalse(plan["built"], plan)
        self.assertIn("confirmed_needs_source_grade, which needs a promoter independent of the row's writers",
                      plan["reason"])

    def test_the_generated_skill_tells_the_agent_who_may_promote(self):
        from xscholar import skills
        text = skills._gates_block(independent_instance(self.inst))
        self.assertIn("never by its own proposer, never by root", text)
        self.assertNotIn("never by root", skills._gates_block(self.inst))


if __name__ == "__main__":   # last, so running this file directly runs every class in it
    unittest.main()
