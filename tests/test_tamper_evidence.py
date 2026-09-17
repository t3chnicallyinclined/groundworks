# -*- coding: utf-8 -*-
"""On a throwaway SurrealDB (never a live one): the live datastore compared with its config, independence
between principals, the promoter's own evidence, and the hash-chained promotion log -- each proven to catch
the hand edit or the laundering it exists for.

    python -m unittest tests.test_tamper_evidence -v
"""
import json
import pathlib
import subprocess
import threading
import time
import unittest
import urllib.request

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import access, config, gates, live_schema, promotion_log, surreal_bin, temporal  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar.pipeline import loader  # noqa: E402
from tests.test_independence_modes import RULE, mode_instance  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
SURREAL = surreal_bin.find()
PORT = surreal_bin.free_port()   # OS-assigned (was 8035): suites no longer share a registry of fixed ports
URL = "http://127.0.0.1:%d/sql" % PORT
UNIT = ("CREATE unit:%s SET type='log_line', text='ok', locator={p: 1}, status='in_window', grade='%s', hash='h';")
PROC = None


def setUpModule():
    global PROC
    if not SURREAL:
        raise unittest.SkipTest("NOT EXECUTED: " + surreal_bin.MISSING)
    if surreal_bin.port_in_use(PORT):
        raise unittest.SkipTest(surreal_bin.occupied(PORT))
    PROC = subprocess.Popen([str(SURREAL), "start", "--bind", "127.0.0.1:%d" % PORT, "--user", "root", "--pass", "root",
                             "memory"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
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


_N = [0]


def deploy(inst, components=("schema", "gates", "temporal", "access", "provenance")):
    """A fresh database, applied the way scripts/run_instance.py and the loader apply it -- by calling each
    generator directly, not through live_schema, so the check is compared with a deployment it did not make."""
    _N[0] += 1
    root = dbmod.Db(url=URL, ns="t", db="d%d" % _N[0])
    steps = {"schema": gates.schema_surql, "gates": gates.gate_surql, "temporal": temporal.temporal_surql,
             "access": access.access_surql, "provenance": lambda i: loader.provenance_surql()}
    for name in components:
        root.script(steps[name](inst))
    return root


def agent(root, inst, agent_id, role="reader", principal=None):
    return access.connect(access.provision(root, inst, agent_id, role, principal=principal, provisioned_by="test"))


def rows(result):
    return sorted((r["status"], r["kind"], r["name"]) for r in result.rows)


class LiveSchemaTests(unittest.TestCase):
    """A rule removed or loosened by hand in the live database, while the config still has it."""

    def setUp(self):
        self.inst = mode_instance(True, log=True)       # independence and the log: both have events to lose

    def test_a_deployment_nobody_touched_reads_clean(self):
        root = deploy(self.inst)
        report = live_schema.check(self.inst, root)
        self.assertEqual(rows(report), [], "\n".join(report.lines()))
        self.assertGreater(report.expected_count, 100)
        self.assertEqual(report.expected_count, report.live_count)
        left = root.sql("INFO FOR NS;")[0]["databases"]
        self.assertEqual([d for d in left if d.startswith(live_schema.SCRATCH_PREFIX)], [], "scratch database left")

    def test_every_hand_edit_is_reported_by_name(self):
        edits = [
            ("REMOVE EVENT %s_independent ON TABLE fact;" % RULE, [("missing", "event", "fact.%s_independent" % RULE)]),
            ("DEFINE FIELD OVERWRITE verified ON authority TYPE bool DEFAULT false;",
             [("altered", "field", "authority.verified")]),
            ("REMOVE INDEX authority_citation_unique ON TABLE authority;",
             [("missing", "index", "authority.authority_citation_unique")]),
            ("ALTER TABLE fact PERMISSIONS FOR select FULL FOR create FULL FOR update FULL FOR delete NONE;",
             [("altered", "table", "fact")]),
            ("REMOVE EVENT promotion_log_append_only ON TABLE promotion_log;",
             [("missing", "event", "promotion_log.promotion_log_append_only")]),
            ("DEFINE USER second_login ON DATABASE PASSWORD 'x' ROLES EDITOR;", [("unexpected", "user", "second_login")]),
            ("DEFINE EVENT extra ON TABLE fact WHEN true THEN {};", [("unexpected", "event", "fact.extra")]),
            ("DEFINE PARAM $usable VALUE ['in_window', 'redacted'];", [("unexpected", "param", "usable")]),
            ("REMOVE EVENT root_cause_needs_support_and_attack_gate ON TABLE conclusion;",
             [("missing", "event", "conclusion.root_cause_needs_support_and_attack_gate")]),
        ]
        for statement, expected in edits:
            with self.subTest(edit=statement[:50]):
                root = deploy(self.inst)
                root.sql(statement)
                report = live_schema.check(self.inst, root)
                self.assertEqual(rows(report), expected, "\n".join(report.lines()))
                self.assertFalse(report.ok)

    def test_the_schema_reapplied_after_access_is_reported_table_by_table(self):
        """Reported by the coordinating session: DEFINE TABLE OVERWRITE in the schema reset PERMISSIONS to NONE,
        so re-applying the schema after access silently locked every agent out. Nothing said so. The generator
        now re-shapes tables with ALTER TABLE and a re-apply changes nothing (tests/test_gates.py
        GateFixServerTests); the check must still name every table such a statement resets, so the old generator's
        statements are replayed here by hand."""
        root = deploy(self.inst)
        self.assertEqual(rows(live_schema.check(self.inst, root)), [])
        # The schema alone restates every built-in field, so on its own it strips the gate ASSERTs until gate_surql
        # runs again; every applier (gates --apply, run_instance, the evaluator) runs the two together, and the check
        # names exactly the gated fields when someone does not.
        root.script(gates.schema_surql(self.inst))
        self.assertEqual({(s, k) for s, k, _ in rows(live_schema.check(self.inst, root))}, {("altered", "field")})
        self.assertEqual({n for _, _, n in rows(live_schema.check(self.inst, root))},
                         {"%s.%s" % (r["table"], r["field"]) for r in self.inst.gate_rules()
                          if r.get("requires") or r.get("forbids")})
        root.script(gates.gate_surql(self.inst))
        self.assertEqual(rows(live_schema.check(self.inst, root)), [],
                         "re-applying the schema and the gates, as every applier does, must change nothing")
        root.script("DEFINE TABLE OVERWRITE fact SCHEMAFULL; DEFINE TABLE OVERWRITE critique SCHEMAFULL;")
        report = live_schema.check(self.inst, root)
        altered = {name for status, kind, name in rows(report) if status == "altered" and kind == "table"}
        self.assertIn("fact", altered, "\n".join(report.lines()))
        self.assertIn("critique", altered)
        self.assertEqual({status for status, _, _ in rows(report)}, {"altered"}, "\n".join(report.lines()))
        fact = [r for r in report.rows if r["name"] == "fact"][0]
        self.assertIn("PERMISSIONS FOR select WHERE", fact["expected"])
        self.assertIn("PERMISSIONS NONE", fact["live"])

    def test_no_password_hash_leaves_the_datastore_through_the_check(self):
        root = deploy(self.inst)
        root.sql("DEFINE USER second_login ON DATABASE PASSWORD 'x' ROLES EDITOR;")
        defined = live_schema.read_definitions(root)
        self.assertNotIn("$argon2", json.dumps({"%s:%s" % k: v for k, v in defined.items()}))
        before = defined[("user", "second_login")]
        root.sql("DEFINE USER OVERWRITE second_login ON DATABASE PASSWORD 'y' ROLES EDITOR;")
        self.assertNotEqual(live_schema.read_definitions(root)[("user", "second_login")], before,
                            "a changed password must still read as a changed definition")

    def test_the_command_line_exits_one_on_drift_and_names_it(self):
        root = deploy(self.inst)
        root.sql("REMOVE INDEX authority_citation_unique ON TABLE authority;")
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            # the CLI loads the config from a file, so write this instance's config next to nothing else
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                path = pathlib.Path(tmp) / "instance.json"
                path.write_text(json.dumps(self.inst.data), encoding="utf-8")
                evals = pathlib.Path(tmp) / self.inst.data["evaluator"]["regression_file"]
                evals.parent.mkdir(parents=True)
                evals.write_bytes((DEMO.parent / self.inst.data["evaluator"]["regression_file"]).read_bytes())
                rc = live_schema.main([str(path), "--url", URL, "--ns", "t", "--db", root.db])
        self.assertEqual(rc, 1, out.getvalue())
        self.assertIn("MISSING index authority.authority_citation_unique", out.getvalue())


def blind_instance():
    """The demo's readiness rule keeping one requires clause a role CAN see (a `preserves` edge from a unit) and
    both forbids clauses it CANNOT see (critiques), and that role, which may write a conclusion. Declared here
    from the demo config; the demo itself is untouched."""
    data = json.loads(json.dumps(config.load(DEMO).data))
    for rule in data["gates"]:
        if rule["id"] == "root_cause_needs_support_and_attack":
            rule["requires"] = [c for c in rule["requires"] if c["edge"] == "preserves"]
    data["access"]["roles"]["blind"] = {"read": ["conclusion", "unit"], "write": ["conclusion"]}
    return config.load(data)


class BlindCallerTests(unittest.TestCase):
    """A gate ASSERT counts only the rows the caller may read. The rule must hold for a caller who reads none."""

    RULE = "root_cause_needs_support_and_attack"

    def setUp(self):
        self.inst = blind_instance()
        self.root = deploy(self.inst, ("schema", "gates", "access"))
        self.root.sql(UNIT % ("u1", "original_log") + "CREATE conclusion:c1 SET statement='c'; "
                      "RELATE unit:u1->preserves->conclusion:c1 SET how='manual_note'; CREATE critique:k1 SET "
                      "lens='x', head='h', verdict='fatal'; RELATE critique:k1->attacks->conclusion:c1;", expect=5)
        self.blind = agent(self.root, self.inst, "blind_1", role="blind")
        self.sees = agent(self.root, self.inst, "coord_1", role="coordinator")

    def test_a_caller_who_cannot_read_the_forbidden_row_is_refused_like_everyone_else(self):
        for who, conn in (("root", self.root), ("a caller who reads critiques", self.sees), ("a blind caller", self.blind)):
            ok, message = conn.try_sql("UPDATE conclusion:c1 SET ready = true")
            self.assertFalse(ok, "%s promoted a conclusion a fatal critique attacks" % who)
        self.assertEqual(self.root.sql("SELECT VALUE ready FROM conclusion:c1")[0], [False])
        self.root.sql("DELETE attacks; DELETE critique;", expect=2)
        ok, message = self.blind.try_sql("UPDATE conclusion:c1 SET ready = true")
        self.assertTrue(ok, "with nothing forbidden left, the blind caller was still refused: %s" % message)

    def test_loosening_only_the_assert_by_hand_leaves_the_rule_holding_and_is_reported(self):
        self.root.sql("DEFINE FIELD OVERWRITE ready ON conclusion TYPE bool DEFAULT false;")
        ok, message = self.sees.try_sql("UPDATE conclusion:c1 SET ready = true")
        self.assertFalse(ok, "with the ASSERT gone the rule no longer held")
        self.assertIn(gates.GATE_EVENT_MARKER, message)
        drift = live_schema.check(self.inst, self.root, components=["schema", "gates", "access"])
        self.assertEqual(rows(drift), [("altered", "field", "conclusion.ready")], "\n".join(drift.lines()))

    def test_the_generated_probe_tries_the_blind_caller_and_goes_red_without_the_event(self):
        self.root.sql("DELETE attacks; DELETE critique;", expect=2)
        report = gates.run_probes(self.inst, self.root)
        entry = [e for e in report["rules"] if e["rule"] == self.RULE][0]
        self.assertTrue(entry["behaved"], entry)
        self.assertIn("role blind", entry.get("blind_probe") or "", entry)
        self.assertEqual(len(entry["blind_forbidden"]), 2, entry)
        self.assertTrue(all(r["blind_reject"] is True for r in entry["blind_forbidden"]), entry)
        self.root.sql("REMOVE EVENT %s ON TABLE conclusion;" % gates.gate_event_name({"id": self.RULE}))
        report = gates.run_probes(self.inst, self.root)
        entry = [e for e in report["rules"] if e["rule"] == self.RULE][0]
        self.assertFalse(entry["behaved"], "the blind caller got past the rule and the probe stayed green: %s" % entry)
        self.assertIs(entry["forbidden_evidence_reject"], False)


    def test_the_blind_probe_starts_from_an_unpromoted_row(self):
        """A rule that only forbids: nothing refuses the probe's first attempt, so the row is already promoted when
        the blind caller comes. Re-sending the value it holds changes nothing and fires no event (measured), so
        the probe must put the row back first, or the blind refusal it reports is about nothing."""
        data = json.loads(json.dumps(self.inst.data))
        for rule in data["gates"]:
            if rule["id"] == self.RULE:
                rule.pop("requires")
        inst = config.load(data)
        root = deploy(inst, ("schema", "gates", "access"))
        report = gates.run_probes(inst, root)
        entry = [e for e in report["rules"] if e["rule"] == self.RULE][0]
        self.assertEqual(len(entry.get("blind_forbidden") or []), 2, entry)
        for result in entry["blind_forbidden"]:
            self.assertIs(result["blind_reject"], True, result)
            self.assertIn(gates.GATE_EVENT_MARKER, result["blind_error"], result)


class PrincipalTests(unittest.TestCase):
    """Two logins for one principal are one reviewer."""

    def setUp(self):
        self.inst = mode_instance({"between": "principal"})
        self.root = deploy(self.inst, ("schema", "gates", "access"))
        self.root.sql(UNIT % ("u_ok", "original_log"))

    def test_a_second_login_of_the_same_principal_is_refused_and_another_principal_is_not(self):
        first = agent(self.root, self.inst, "first_1", principal="alice")
        first.sql("CREATE fact:f1 SET statement='s'; RELATE fact:f1->cites->unit:u_ok;")
        sibling = agent(self.root, self.inst, "first_2", principal="alice")
        ok, message = sibling.try_sql("UPDATE fact:f1 SET status='confirmed'")
        self.assertFalse(ok, "a second credential of the proposer's own principal promoted the row")
        self.assertEqual(gates.independence_reason_in(message), "principal", message)
        other = agent(self.root, self.inst, "second_1", principal="bob")
        ok, message = other.try_sql("UPDATE fact:f1 SET status='confirmed'")
        self.assertTrue(ok, message)

    def test_who_wrote_the_row_is_fixed_when_it_was_written(self):
        """Principals are copied when the row is written. Read from the agent row at promotion time instead, a
        proposer re-provisioned under another principal after writing would let its own second login promote,
        and a revoked proposer's row could never be promoted by anyone."""
        first = agent(self.root, self.inst, "first_1", principal="alice")
        first.sql("CREATE fact:f1 SET statement='s'; RELATE fact:f1->cites->unit:u_ok;")
        agent(self.root, self.inst, "first_1", principal="bob")          # re-provisioned under another principal
        sibling = agent(self.root, self.inst, "first_2", principal="alice")
        ok, message = sibling.try_sql("UPDATE fact:f1 SET status='confirmed'")
        self.assertFalse(ok, "re-provisioning the proposer under another principal let its own principal promote")
        self.assertEqual(gates.independence_reason_in(message), "principal", message)
        access.revoke(self.root, "first_1")
        other = agent(self.root, self.inst, "second_1", principal="bob")
        ok, message = other.try_sql("UPDATE fact:f1 SET status='confirmed'")
        self.assertTrue(ok, "after the proposer was revoked nobody could promote its row: %s" % message)

    def test_a_credential_without_a_principal_can_neither_be_provisioned_nor_promote(self):
        with self.assertRaises(access.AccessError) as err:
            access.provision(self.root, self.inst, "nobody", "reader")
        self.assertIn("compare principals: provision nobody with a principal", str(err.exception))
        first = agent(self.root, self.inst, "first_1", principal="alice")
        first.sql("CREATE fact:f1 SET statement='s'; RELATE fact:f1->cites->unit:u_ok;")
        self.root.sql("UPSERT agent:legacy SET role='reader', pass=crypto::argon2::generate('p'), enabled=true;")
        legacy = dbmod.Db(url=URL, ns="t", db=self.root.db, user="legacy", password="p", access=access.ACCESS_NAME)
        ok, message = legacy.try_sql("UPDATE fact:f1 SET status='confirmed'")
        self.assertFalse(ok, "a credential with no declared principal was accepted as independent")
        self.assertEqual(gates.independence_reason_in(message), "principal", message)

    def test_provisioning_is_recorded_and_shared_principals_are_reported(self):
        agent(self.root, self.inst, "first_1", principal="alice")
        agent(self.root, self.inst, "first_2", principal="alice")
        report = access.credentials_report(self.root)
        self.assertEqual(report["shared_principals"], {"alice": ["first_1", "first_2"]})
        row = [r for r in report["credentials"] if r["agent"] == "first_1"][0]
        self.assertEqual(row["provisioned_by"], "test")
        self.assertTrue(row["provisioned_at"])

    def test_the_generated_probe_proves_the_principal_rule_both_ways(self):
        report = gates.run_probes(self.inst, self.root)
        entry = [e for e in report["rules"] if e["rule"] == RULE][0]
        self.assertTrue(entry["behaved"], entry)
        self.assertIs(entry["sibling_promotion_reject"], True, entry)
        self.assertEqual(self.root.sql("SELECT VALUE id FROM agent WHERE record::id(id) CONTAINS 'zz_probe'")[0], [])
        self.assertEqual(self.root.sql("SELECT VALUE id FROM row_writer")[0], [], "probe writer records left behind")


class OwnEvidenceTests(unittest.TestCase):
    """The promoter must have created a counted evidence link itself."""

    def setUp(self):
        self.inst = mode_instance({"own_evidence": True})
        self.root = deploy(self.inst, ("schema", "gates", "access"))
        self.root.sql(UNIT % ("u_ok", "original_log") + UNIT % ("u_weak", "agent_summary"), expect=2)
        self.proposer = agent(self.root, self.inst, "a_1")
        self.promoter = agent(self.root, self.inst, "b_1")
        self.proposer.sql("CREATE fact:f1 SET statement='s'; RELATE fact:f1->cites->unit:u_ok;")

    def test_touching_someone_elses_link_or_linking_weak_evidence_does_not_count(self):
        """Measured before this was built: with the check on who WROTE an edge, `UPDATE cites SET x = 1` put
        the promoter on the proposer's edge and the promotion went through. Only creation counts."""
        self.promoter.sql("UPDATE cites SET note = 'looked at it' WHERE in = fact:f1;")
        ok, message = self.promoter.try_sql("UPDATE fact:f1 SET status='confirmed'")
        self.assertFalse(ok, "touching the proposer's link counted as the promoter's own evidence")
        self.assertEqual(gates.independence_reason_in(message), "own_evidence", message)
        self.promoter.sql("RELATE fact:f1->cites->unit:u_weak;")
        ok, message = self.promoter.try_sql("UPDATE fact:f1 SET status='confirmed'")
        self.assertFalse(ok, "a link to evidence the rule does not count was accepted as own evidence")
        self.assertEqual(gates.independence_reason_in(message), "own_evidence", message)
        self.promoter.sql("RELATE fact:f1->cites->unit:u_ok;")
        ok, message = self.promoter.try_sql("UPDATE fact:f1 SET status='confirmed'")
        self.assertTrue(ok, message)

    def test_the_link_creator_record_cannot_be_forged_by_a_credential(self):
        """Claim the proposer's link as the promoter's own by writing its creator record directly."""
        edge = self.root.sql("SELECT VALUE id FROM cites WHERE in = fact:f1")[0][0]
        forge = "UPSERT type::record('link_creator', [%s]) SET credential = agent:b_1, link = %s" % (edge, edge)
        try:
            self.promoter.sql(forge)
        except dbmod.PermissionDenied:
            pass
        creator = self.root.sql("SELECT VALUE credential FROM type::record('link_creator', [%s])" % edge)[0]
        self.assertEqual(creator, ["agent:a_1"], "a credential rewrote who created a link")
        ok, message = self.promoter.try_sql("UPDATE fact:f1 SET status='confirmed'")
        self.assertFalse(ok, "a forged creator record counted as the promoter's own evidence")

    def test_the_generated_probe_proves_the_own_evidence_rule_both_ways(self):
        report = gates.run_probes(self.inst, self.root)
        entry = [e for e in report["rules"] if e["rule"] == RULE][0]
        self.assertTrue(entry["behaved"], entry)
        self.assertIs(entry["borrowed_evidence_reject"], True, entry)
        self.assertEqual(self.root.sql("SELECT VALUE id FROM link_creator WHERE record::id(link_in) CONTAINS 'zz_probe'")[0], [])

    def test_the_probe_goes_red_when_the_own_evidence_check_is_removed_by_hand(self):
        self.root.sql("REMOVE EVENT %s_independent_mode ON TABLE fact;" % RULE)
        report = gates.run_probes(self.inst, self.root)
        entry = [e for e in report["rules"] if e["rule"] == RULE][0]
        self.assertIs(entry["borrowed_evidence_reject"], False, entry)
        self.assertFalse(entry["behaved"])
        # the summary accounts for every independent rule: reported from a real instance, a failed rule that was
        # neither "refused as required" nor "untested" vanished from the line
        self.assertEqual((report["independence_rules"], report["independence_refused_ok"],
                          report["independence_untested"], report["independence_failed"]), (1, 0, 0, 1), report)

    def test_the_probe_uses_a_role_that_can_see_the_evidence(self):
        """Reported from a real instance: the first role in the config wrote the table but could not read what the
        rule counts, the probe chose it, and failed for a reason that had nothing to do with independence."""
        data = json.loads(json.dumps(self.inst.data))
        roles = {"aaa_writer": {"read": ["fact"], "write": ["fact", "cites"]}}
        roles.update(data["access"]["roles"])
        data["access"]["roles"] = roles
        inst = config.load(data)
        root = deploy(inst, ("schema", "gates", "access"))
        report = gates.run_probes(inst, root)
        entry = [e for e in report["rules"] if e["rule"] == RULE][0]
        self.assertTrue(entry["behaved"], entry)
        self.assertIn({"role": "aaa_writer", "rule": RULE, "table": "fact", "field": "status", "value": "confirmed",
                       "unreadable": ["cites", "unit"]}, report["unpassable_roles"])


class PromotionLogTests(unittest.TestCase):
    def setUp(self):
        self.inst = mode_instance(True, log=True)
        self.root = deploy(self.inst, ("schema", "gates", "access"))
        self.root.sql(UNIT % ("u_ok", "original_log"))

    def promote(self, name, proposer, promoter):
        proposer.sql("CREATE fact:%s SET statement='s'; RELATE fact:%s->cites->unit:u_ok;" % (name, name))
        promoter.sql("UPDATE fact:%s SET status='confirmed'" % name)

    def test_promotions_chain_a_refused_one_leaves_no_entry_and_an_edit_is_found_at_its_link(self):
        a, b = agent(self.root, self.inst, "a_1"), agent(self.root, self.inst, "b_1")
        for name in ("f1", "f2", "f3"):
            self.promote(name, a, b)
        a.sql("CREATE fact:f4 SET statement='s'; RELATE fact:f4->cites->unit:u_ok;")
        ok, _ = a.try_sql("UPDATE fact:f4 SET status='confirmed'")
        self.assertFalse(ok)
        result = promotion_log.verify(self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["entries"], 3, "a refused promotion left an entry")
        entry = self.root.sql("SELECT * FROM promotion_log:2")[0][0]
        self.assertEqual((entry["credential"], entry["before"], entry["after"], entry["row"]),
                         ("agent:b_1", "proposed", "confirmed", "fact:f2"))
        # the accident guard: root's own UPDATE and DELETE are refused while the guard stands
        for statement in ("UPDATE promotion_log:2 SET after = 'proposed'", "DELETE promotion_log:2"):
            ok, message = self.root.try_sql(statement)
            self.assertFalse(ok, statement)
        # the deliberate owner: remove the guard and the READONLY field, then edit
        self.root.sql("REMOVE EVENT promotion_log_append_only ON TABLE promotion_log; "
                      "DEFINE FIELD OVERWRITE after ON promotion_log TYPE string; "
                      "UPDATE promotion_log:2 SET after = 'proposed';", expect=3)
        result = promotion_log.verify(self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_break"]["seq"], 2, result)
        self.assertIn("content does not match its hash", result["first_break"]["why"])
        drift = live_schema.check(self.inst, self.root, components=["schema", "gates", "access"])
        self.assertIn(("missing", "event", "promotion_log.promotion_log_append_only"), rows(drift))
        self.assertIn(("altered", "field", "promotion_log.after"), rows(drift))

    def test_truncation_behind_a_rewritten_head_is_caught_only_by_the_anchor(self):
        a, b = agent(self.root, self.inst, "a_1"), agent(self.root, self.inst, "b_1")
        for name in ("f1", "f2", "f3"):
            self.promote(name, a, b)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "anchor.json"
            anchor = promotion_log.export_anchor(self.root, self.inst, path)
            self.assertEqual(anchor["seq"], 3)
            head2 = self.root.sql("SELECT VALUE hash FROM promotion_log:2")[0][0]
            self.root.sql("REMOVE EVENT promotion_log_append_only ON TABLE promotion_log; DELETE promotion_log:3; "
                          "UPSERT promotion_head:head SET seq = 2, hash = '%s';" % head2, expect=3)
            self.assertTrue(promotion_log.verify(self.root)["ok"], "the limit: without the anchor this verifies")
            result = promotion_log.verify(self.root, anchor=promotion_log.load_anchor(path))
            self.assertFalse(result["ok"])
            self.assertIn("it was truncated", result["first_break"]["why"])

    def test_a_late_provisioned_promoter_is_flagged_and_an_earlier_one_is_not(self):
        early = agent(self.root, self.inst, "early_1")
        proposer = agent(self.root, self.inst, "a_1")
        proposer.sql("CREATE fact:f1 SET statement='s'; RELATE fact:f1->cites->unit:u_ok;")
        proposer.sql("CREATE fact:f2 SET statement='s'; RELATE fact:f2->cites->unit:u_ok;")
        early.sql("UPDATE fact:f1 SET status='confirmed'")
        late = agent(self.root, self.inst, "late_1")          # the "make a second login" signature
        late.sql("UPDATE fact:f2 SET status='confirmed'")
        flagged = [(e["row"], e["credential"]) for e in promotion_log.late_promotions(self.root)]
        self.assertEqual(flagged, [("fact:f2", "agent:late_1")])
        entry = self.root.sql("SELECT late FROM promotion_log:1")[0][0]
        self.assertEqual(entry["late"], "no")

    def test_concurrent_promotions_never_fork_the_chain(self):
        """Every concurrent promotion reads the same head. The entry id is the sequence number, so only one can
        commit; the others are refused whole, as retryable conflicts, and nothing is half-written."""
        a, b = agent(self.root, self.inst, "a_1"), agent(self.root, self.inst, "b_1")
        count = 24
        a.sql("; ".join("CREATE fact:c%d SET statement='s'; RELATE fact:c%d->cites->unit:u_ok" % (i, i)
                        for i in range(count)) + ";")
        refused = []
        conns = []
        for _ in range(count):        # sign in first, so the promotions themselves start together
            conn = access.connect({"XSCHOLAR_DB_URL": URL, "XSCHOLAR_NS": "t", "XSCHOLAR_DB": self.root.db,
                                   "XSCHOLAR_DB_USER": "b_1", "XSCHOLAR_DB_PASS": b._password})
            conn.ping(check_version=False)
            conns.append(conn)
        barrier = threading.Barrier(count)

        def promote(i):
            barrier.wait()
            ok, message = conns[i].try_sql("UPDATE fact:c%d SET status='confirmed'" % i)
            if not ok:
                refused.append(message)
        threads = [threading.Thread(target=promote, args=(i,)) for i in range(count)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        promoted = self.root.sql("SELECT count() AS n FROM fact WHERE status = 'confirmed' GROUP ALL")[0]
        promoted = promoted[0]["n"] if promoted else 0
        result = promotion_log.verify(self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["entries"], promoted, "log entries and promoted rows disagree")
        self.assertEqual(promoted + len(refused), count)
        self.assertTrue(refused, "no promotion collided, so this run did not exercise concurrency at all")
        for message in refused:
            self.assertIn("can be retried", message)


class McpHealthTests(unittest.TestCase):
    """The MCP `health` tool carries the tamper-evidence counts, and each is one that can show a problem: planted
    drift, a late promotion and a unit with no original each move their number, and a check the server's connection
    cannot run says NOT_EXECUTED instead of reading 0."""

    def setUp(self):
        from xscholar import lens, originals
        from xscholar.mcp import instance_adapter
        self.inst = mode_instance(True, log=True)
        self.root = deploy(self.inst, ("schema", "gates", "temporal", "access", "provenance"))
        self.root.script(originals.enforcement_surql(self.inst))
        self.root.script(lens.lens_surql(self.inst))
        self.root.sql(UNIT % ("u_ok", "original_log"))
        self.adapter = instance_adapter.create_adapter_for(self.inst, self.root)
        self.agent_adapter = instance_adapter.create_adapter_for(self.inst, agent(self.root, self.inst, "reader_1"))

    def test_each_count_moves_when_its_problem_is_planted(self):
        health = self.adapter.health()
        schema, log = health["live_schema"], health["promotion_log"]
        self.assertEqual((schema["missing"], schema["altered"], schema["unexpected"]), (0, 0, 0), schema)
        self.assertGreater(schema["expected"], 100)
        self.assertEqual((log["ok"], log["entries"], log["late"]), (True, 0, 0), log)
        self.assertEqual(health["originals"]["gap_units_without_verified_original"], 1)
        self.assertIn("none declared", health["assertions"])
        # a promotion by a credential provisioned after the proposer wrote the row: `late`
        proposer = agent(self.root, self.inst, "a_1")
        proposer.sql("CREATE fact:f1 SET statement='s'; RELATE fact:f1->cites->unit:u_ok;")
        self.root.sql("CREATE document:%s SET sha256 = '%s', media_type = 'text/plain', bytes = 2, capture = 'raw', "
                      "integrity = 'ok'; UPDATE unit:u_ok SET document = document:%s;" % ("a" * 64, "a" * 64, "a" * 64),
                      expect=2)
        agent(self.root, self.inst, "late_1").sql("UPDATE fact:f1 SET status='confirmed'")
        self.root.sql("REMOVE EVENT %s_gate ON TABLE fact;" % RULE)
        health = self.adapter.health()
        self.assertEqual((health["promotion_log"]["entries"], health["promotion_log"]["late"]), (1, 1),
                         health["promotion_log"])
        self.assertEqual(health["originals"]["gap_units_without_verified_original"], 0)
        self.assertEqual(health["live_schema"]["missing"], 1, health["live_schema"])
        self.assertIn("missing event fact.%s_gate" % RULE, health["live_schema"]["drift_sample"])
        # a broken chain
        self.root.sql("REMOVE EVENT promotion_log_append_only ON TABLE promotion_log; "
                      "DEFINE FIELD OVERWRITE after ON promotion_log TYPE string; "
                      "UPDATE promotion_log:1 SET after = 'proposed';", expect=3)
        log = self.adapter.health()["promotion_log"]
        self.assertFalse(log["ok"])
        self.assertEqual(log["first_break"]["seq"], 1)

    def test_a_check_this_connection_cannot_run_says_not_executed_never_zero(self):
        health = self.agent_adapter.health()
        self.assertIn("NOT_EXECUTED", health["live_schema"])
        self.assertIn("NOT_EXECUTED", health["promotion_log"])
        self.assertNotIn("missing", health["live_schema"])
        # INFO FOR TABLE is refused to a record credential: the whole health call used to fail on it
        self.assertIn("NOT_EXECUTED", health["originals"]["enforcement"])


if __name__ == "__main__":
    unittest.main()
