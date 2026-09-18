# -*- coding: utf-8 -*-
"""Principals, the promoter's own evidence, the promotion log and the live-schema comparison, without a
database: config validation, the baseline, the judge, refusal parsing, chain verification and comparison.
The datastore half is tests/test_tamper_evidence.py.

    python -m unittest tests.test_independence_modes -v
"""
import json
import pathlib
import tempfile
import unittest

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks import config, gates, live_schema, promotion_log, skills  # noqa: E402
from groundworks import db as dbmod  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
RULE = "confirmed_needs_source_grade"


def mode_instance(mode, log=False, base=None):
    """The demo config with `independent:` set to `mode` on the fact confirmation rule, and the promotion log
    switched on when `log`. Built here; the demo's own file is never edited."""
    data = json.loads(json.dumps((base or config.load(DEMO)).data))
    for rule in data["gates"]:
        if rule["id"] == RULE:
            rule["independent"] = mode
    if log:
        data["promotion_log"] = True
        data["human_owned"].append("promotion_log")
    return config.load(data)


class ModeConfigTests(unittest.TestCase):
    def setUp(self):
        self.inst = config.load(DEMO)

    def test_every_reader_sees_a_mapping_rule_as_independent(self):
        """The hazard: a mapping is not `is True`, so any reader left on the old test would switch the rule OFF
        silently -- no event, no probe, no baseline pin, no skill line."""
        inst = mode_instance({"between": "principal", "own_evidence": True})
        self.assertEqual([r["id"] for r in inst.independent_rules()], [RULE])
        self.assertEqual([r["id"] for r in inst.principal_rules()], [RULE])
        text = gates.gate_surql(inst)
        self.assertIn("DEFINE EVENT OVERWRITE %s_independent ON TABLE fact" % RULE, text)
        self.assertIn("DEFINE EVENT OVERWRITE %s_independent_mode ON TABLE fact" % RULE, text)
        probe = [p for p in gates.probes(inst) if p["rule"] == RULE][0]
        self.assertTrue(probe["independent"])
        self.assertEqual(probe["mode"], {"between": "principal", "own_evidence": True})
        self.assertEqual(gates.spec(inst)["rules"][RULE]["independence"], {"between": "principal", "own_evidence": True})
        block = skills._gates_block(inst)
        self.assertIn("different principal", block)
        self.assertIn("created at least one of the evidence links", block)

    def test_true_still_means_exactly_what_it_meant(self):
        inst = mode_instance(True)
        self.assertNotIn("_independent_mode", gates.gate_surql(inst))
        self.assertNotIn("row_writer", gates.gate_surql(inst))
        self.assertNotIn("independence", gates.spec(inst)["rules"][RULE], "an older baseline must still read the same")

    def test_malformed_modes_are_refused_by_name(self):
        data = json.loads(json.dumps(self.inst.data))
        data["gates"][0]["independent"] = {"between": "people", "own_evidence": "yes", "quorum": 2}
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        text = str(err.exception)
        self.assertIn("gate %s: independent has unknown key(s) quorum" % RULE, text)
        self.assertIn("independent.between must be one of credential, principal (got 'people')", text)
        self.assertIn("independent.own_evidence must be true or false (got 'yes')", text)

    def test_own_evidence_needs_links_to_count_and_a_role_that_can_create_one(self):
        data = json.loads(json.dumps(self.inst.data))
        data["gates"].append({"id": "noted_by_another", "table": "fact", "field": "status", "value": "inferred",
                              "independent": {"own_evidence": True}})
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("gate noted_by_another: independent.own_evidence needs requires clauses", str(err.exception))
        data = json.loads(json.dumps(self.inst.data))
        data["gates"][0]["independent"] = {"own_evidence": True}
        for role in data["access"]["roles"].values():
            role["write"] = [t for t in role.get("write") or [] if t != "cites"]
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("gate %s: independent.own_evidence, but no access role both reads and writes fact and writes "
                      "one of its evidence edges (cites)" % RULE, str(err.exception))

    def test_the_engine_audit_tables_cannot_be_declared_and_the_log_is_human_owned(self):
        data = json.loads(json.dumps(self.inst.data))
        data["tables"] = {"promotion_log": {"fields": {"x": "string"}}}
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("tables.promotion_log: 'promotion_log' is owned by the engine", str(err.exception))
        data = json.loads(json.dumps(self.inst.data))
        data["promotion_log"] = True
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("human_owned must include promotion_log", str(err.exception))


class BaselineAndJudgeTests(unittest.TestCase):
    def test_the_baseline_catches_a_mode_downgraded_or_the_log_switched_off(self):
        strong = mode_instance({"between": "principal", "own_evidence": True}, log=True)
        baseline = {"instance": strong.id, "spec": gates.spec(strong), "report": None}
        for weaker, expected in (
                (mode_instance(True, log=True),
                 ["rule %s no longer requires a promoter from a different principal: two credentials held by one "
                  "person or process may now propose and promote" % RULE,
                  "rule %s no longer requires the promoter's own evidence: a promoter may now pass on links someone "
                  "else created" % RULE]),
                (mode_instance({"between": "principal", "own_evidence": True}),
                 ["the promotion log is in the baseline and switched off in the config: promotions are no longer "
                  "recorded in the hash chain"])):
            with self.subTest(expected=expected[0][:40]):
                failures = gates.compare_baseline(baseline, None, gates.spec(weaker))
                for line in expected:
                    self.assertIn(line, failures)
        self.assertEqual(gates.compare_baseline(baseline, None, gates.spec(strong)), [])

    def test_judge_needs_the_sibling_and_the_borrowed_evidence_refusals(self):
        probe = {"constrained": False, "constraints": [], "forbids": [], "independent": True,
                 "mode": {"between": "principal", "own_evidence": True}}
        base = {"rule": "r", "reject": True, "accept": True, "wrong_evidence_reject": None,
                "self_promotion_reject": True, "root_promotion_reject": True}
        self.assertTrue(gates.judge(dict(base, sibling_promotion_reject=True, borrowed_evidence_reject=True), probe))
        for flags in ({"sibling_promotion_reject": True}, {"borrowed_evidence_reject": True},
                      {"sibling_promotion_reject": False, "borrowed_evidence_reject": True},
                      {"sibling_promotion_reject": True, "borrowed_evidence_reject": False}):
            with self.subTest(flags=flags):
                entry = dict(base, **flags)
                self.assertFalse(gates.judge(entry, probe), entry)
        entry = dict(base)
        gates.judge(entry, probe)
        self.assertIn("a second credential bound to the proposer's principal was never tried",
                      entry["missing_independence_probe"])
        self.assertIn("a promoter holding only evidence someone else created was never tried",
                      entry["missing_independence_probe"])

    def test_each_refusal_says_why(self):
        """write.py tells an agent what to do next from this; a principal refusal read as "you wrote it" would
        send it to a colleague of the same principal, which can never succeed."""
        inst = mode_instance({"between": "principal", "own_evidence": True})
        text = gates.gate_surql(inst)
        for reason, words in gates.INDEPENDENCE_REASONS.items():
            with self.subTest(reason=reason):
                self.assertIn(words, text)
                message = ("Error while processing event x: An error occurred: %s %s: fact.status may become "
                           "confirmed %s; refused fact:f1 by agent:a" % (gates.INDEPENDENCE_MARKER, RULE, words))
                self.assertEqual(gates.independence_reason_in(message), reason)
                self.assertEqual(gates.independence_rule_in(message), RULE)
        self.assertIsNone(gates.independence_reason_in("only by a credential bound to a declared principal"),
                          "words without the marker are not a refusal")


class UnpassableRoleTests(unittest.TestCase):
    def test_a_role_that_writes_a_gated_table_it_cannot_read_the_evidence_for_is_named(self):
        """The demo's loader creates conclusions and reads neither facts nor critiques: it could never make one
        ready. That is fine for a loader, so it is named, not refused."""
        notes = gates.unpassable_roles(config.load(DEMO))
        self.assertIn({"role": "loader", "rule": "root_cause_needs_support_and_attack", "table": "conclusion",
                       "field": "ready", "value": True, "unreadable": ["attacks", "critique", "fact", "supports"]},
                      notes)
        self.assertEqual([n for n in notes if n["role"] in ("reader", "coordinator")], [],
                         "a role that reads everything a rule counts was named")


class ChainTests(unittest.TestCase):
    """verify() over rows handed to it, so every way a chain breaks is named without a database."""

    class FakeDb:
        access = None

        def __init__(self, entries, head):
            self.entries, self.head = entries, head

        def sql(self, statement, expect=None):
            return [self.entries] if statement.startswith("SELECT * FROM promotion_log") else [self.head]

    @staticmethod
    def chain(n):
        entries, prev = [], promotion_log.GENESIS
        for seq in range(1, n + 1):
            entry = {"id": "promotion_log:%d" % seq, "seq": seq, "prev": prev, "rule": "r", "on_table": "t",
                     "row": "t:\u27e8r\u00e9 %d\u27e9" % seq, "field": "f", "before": "false", "after": "true",
                     "credential": "agent:a", "principal": "p", "at": "2026-09-16T00:00:0%dZ" % seq,
                     "provisioned_at": "none", "late": "no"}
            entry["hash"] = promotion_log.entry_hash(entry)
            prev = entry["hash"]
            entries.append(entry)
        return entries, {"seq": n, "hash": prev}

    def verify(self, entries, head, anchor=None):
        return promotion_log.verify(self.FakeDb(entries, head), anchor=anchor)

    def test_an_intact_chain_verifies_and_every_break_is_named_at_its_link(self):
        entries, head = self.chain(4)
        self.assertTrue(self.verify(entries, head)["ok"])
        cases = []
        edited = json.loads(json.dumps(entries)); edited[2]["after"] = "false"
        cases.append((edited, head, 3, "content does not match its hash"))
        gap = entries[:1] + entries[2:]
        cases.append((gap, head, 2, "entry 2 is missing"))
        relinked = json.loads(json.dumps(entries)); relinked[1]["prev"] = promotion_log.GENESIS
        relinked[1]["hash"] = promotion_log.entry_hash(relinked[1])
        cases.append((relinked, head, 2, "entry 2 does not link to entry 1"))
        cases.append((entries[:3], head, 4, "the head says seq 4"))
        for rows, hd, seq, why in cases:
            with self.subTest(why=why):
                result = self.verify(rows, hd)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["first_break"]["seq"], seq, result)
                self.assertIn(why, result["first_break"]["why"])

    def test_a_chain_rewritten_or_truncated_from_the_start_verifies_alone_and_fails_its_anchor(self):
        """The limit, asserted rather than hidden: root rebuilds the chain and it verifies. Only the anchor
        taken earlier, kept outside the database, catches it."""
        entries, head = self.chain(3)
        anchor = {"seq": 3, "hash": head["hash"]}
        forged = json.loads(json.dumps(entries)); forged[1]["after"] = "false"
        prev = promotion_log.GENESIS
        for entry in forged:
            entry["prev"] = prev
            entry["hash"] = promotion_log.entry_hash(entry)
            prev = entry["hash"]
        forged_head = {"seq": 3, "hash": prev}
        self.assertTrue(self.verify(forged, forged_head)["ok"], "a full rewrite verifies without an anchor")
        result = self.verify(forged, forged_head, anchor=anchor)
        self.assertFalse(result["ok"])
        self.assertIn("the chain up to the anchor was rewritten", result["first_break"]["why"])
        truncated, truncated_head = entries[:2], {"seq": 2, "hash": entries[1]["hash"]}
        self.assertTrue(self.verify(truncated, truncated_head)["ok"], "a truncation with the head rewritten verifies alone")
        result = self.verify(truncated, truncated_head, anchor=anchor)
        self.assertFalse(result["ok"])
        self.assertIn("it was truncated", result["first_break"]["why"])

    def test_export_refuses_a_broken_chain_and_an_anchor_the_chain_no_longer_holds(self):
        entries, head = self.chain(2)
        inst = mode_instance(False, log=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "anchor.json"
            db = self.FakeDb(entries, head); db.ns, db.db = "t", "d"
            anchor = promotion_log.export_anchor(db, inst, path)
            self.assertEqual((anchor["seq"], anchor["hash"]), (2, head["hash"]))
            rewritten, _ = self.chain(1)
            rewritten[0]["after"] = "x"; rewritten[0]["hash"] = promotion_log.entry_hash(rewritten[0])
            db = self.FakeDb(rewritten, {"seq": 1, "hash": rewritten[0]["hash"]}); db.ns, db.db = "t", "d"
            with self.assertRaises(promotion_log.LogError) as err:
                promotion_log.export_anchor(db, inst, path)
            self.assertIn("truncated", str(err.exception))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["seq"], 2, "the old anchor was overwritten")


class CheckpointTests(unittest.TestCase):
    def test_an_anchor_kept_as_a_checkpoint_body_catches_a_rewrite(self):
        entries, head = ChainTests.chain(3)
        inst = mode_instance(False, log=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "head.checkpoint"
            db = ChainTests.FakeDb(entries, head); db.ns, db.db = "t", "d"
            promotion_log.export_anchor(db, inst, path)
            text = path.read_text(encoding="utf-8")
            lines = text.split("\n")
            self.assertEqual(lines[0], "groundworks/promotion_log/%s/t/d sha256-chain" % inst.id)
            self.assertEqual(lines[1], "3")
            self.assertEqual(lines[3:], ["", ""], "a checkpoint body ends with a blank line")
            anchor = promotion_log.load_anchor(path)
            self.assertEqual((anchor["seq"], anchor["hash"]), (3, head["hash"]))
            forged = json.loads(json.dumps(entries)); forged[0]["after"] = "false"
            prev = promotion_log.GENESIS
            for entry in forged:
                entry["prev"] = prev
                entry["hash"] = promotion_log.entry_hash(entry)
                prev = entry["hash"]
            result = promotion_log.verify(ChainTests.FakeDb(forged, {"seq": 3, "hash": prev}), anchor=anchor)
            self.assertFalse(result["ok"], result)
            self.assertIn("rewritten", result["first_break"]["why"])


class CompareTests(unittest.TestCase):
    def test_ignore_never_hides_a_missing_or_altered_definition(self):
        expected = {("event", "t.e"): "E", ("field", "t.f"): "F"}
        live = {("field", "t.f"): "F2", ("user", "u"): "U"}
        rows, ignored = live_schema.compare(expected, live, ignore=["event:t.e", "field:t.f", "user:u"])
        self.assertEqual(sorted((r["status"], r["kind"], r["name"]) for r in rows),
                         [("altered", "field", "t.f"), ("missing", "event", "t.e")])
        self.assertEqual(ignored, ["user:u"])

    def test_a_reference_server_of_another_version_is_refused(self):
        class Fake:
            def __init__(self, version):
                self._v, self.ns, self.db, self.url, self.user, self._password, self.timeout = version, "n", "d", "u", "r", "r", 1

            def version(self):
                return self._v
        with self.assertRaises(live_schema.LiveSchemaError) as err:
            live_schema.render_expected(config.load(DEMO), ["schema"], Fake("surrealdb-3.1.6"), Fake("surrealdb-3.2.0"))
        self.assertIn("two versions may rewrite them differently", str(err.exception))

    def test_an_info_read_that_is_not_a_definition_map_raises(self):
        class Fake:
            ns, db = "n", "d"

            def sql(self, statement, expect=None):
                return [None]
        with self.assertRaises(live_schema.LiveSchemaError):
            live_schema.read_definitions(Fake())

    def test_a_report_line_never_carries_a_password_hash(self):
        report = live_schema.Report("i", "n", "d", ["schema"])
        report.rows = [{"kind": "user", "name": "u", "status": "unexpected", "expected": None,
                        "live": "DEFINE USER u ON DATABASE PASSHASH '$argon2id$v=19$abc' ROLES EDITOR"}]
        text = "\n".join(report.lines())
        self.assertNotIn("argon2id", text)
        self.assertIn("PASSHASH '[sha256:", text)


if __name__ == "__main__":
    unittest.main()
