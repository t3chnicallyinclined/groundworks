# -*- coding: utf-8 -*-
"""Tests for temporal.py: reconstruction is checked in pure Python; the events, the append-only history and
the as-of queries are checked both ways on a throwaway in-memory SurrealDB (never a live one), including
proof that removing the no-op guard makes a same-status re-write append an entry.

    python -m unittest discover -s tests -p "test_temporal.py" -v
"""
import datetime as _dt
import pathlib
import subprocess
import time
import unittest
import urllib.request

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar import gates  # noqa: E402
from xscholar import temporal  # noqa: E402
from xscholar.pipeline import write  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
from xscholar import surreal_bin  # noqa: E402
SURREAL = surreal_bin.find()
PORT = 8031
UTC = _dt.timezone.utc


class PureTests(unittest.TestCase):
    def setUp(self):
        self.inst = config.load(DEMO)

    def test_surql_shape(self):
        text = temporal.temporal_surql(self.inst)
        self.assertIn("WHEN $event = 'UPDATE' AND $before.status != $after.status", text)
        self.assertIn("WHEN $event = 'UPDATE' AND $before.ready != $after.ready", text)
        self.assertIn("DEFINE FIELD OVERWRITE status_history.*.at ON fact TYPE datetime;", text)
        self.assertIn("array::slice($value, 0, array::len($before ?? [])) = ($before ?? [])", text)
        self.assertIn("UPDATE fact SET status_history = IF status_history = NONE { [] } ELSE { status_history }, "
                      "learned_at = IF learned_at = NONE { time::now() } ELSE { learned_at } "
                      "WHERE status_history = NONE OR learned_at = NONE;", text)
        broken = temporal.temporal_surql(self.inst, noop_guard=False)
        self.assertNotIn("$before.status != $after.status", broken)
        self.assertIn("WARNING", broken)

    def test_datetime_parsing_handles_nanoseconds_and_naive_values(self):
        d = temporal.to_datetime("2026-09-16T01:09:35.737895800Z")
        self.assertEqual(d, _dt.datetime(2026, 9, 16, 1, 9, 35, 737895, tzinfo=UTC))
        self.assertEqual(temporal.to_datetime(_dt.datetime(2026, 1, 1)).tzinfo, UTC)
        self.assertEqual(temporal.literal("2026-01-02T00:00:00Z"), '<datetime> "2026-01-02T00:00:00.000000Z"')
        self.assertEqual(temporal.valid_clause(), "")
        self.assertEqual(temporal.valid_clause(valid_to="2026-01-02T00:00:00Z"),
                         ', valid_to = <datetime> "2026-01-02T00:00:00.000000Z"')
        with self.assertRaises(temporal.TemporalError):
            temporal.to_datetime("yesterday")

    def test_value_at_walks_history(self):
        spec = temporal.TRACKED["fact"]
        hist = [{"status_before": "proposed", "status_after": "confirmed", "at": "2026-01-10T00:00:00Z"},
                {"status_before": "confirmed", "status_after": "stale", "at": "2026-01-20T00:00:00Z"}]
        at = lambda s: temporal.to_datetime(s)  # noqa: E731
        self.assertEqual(temporal._value_at(spec, "stale", hist, at("2026-01-05T00:00:00Z")), ("proposed", None))
        self.assertEqual(temporal._value_at(spec, "stale", hist, at("2026-01-15T00:00:00Z"))[0], "confirmed")
        self.assertEqual(temporal._value_at(spec, "stale", hist, at("2026-01-25T00:00:00Z"))[0], "stale")
        self.assertEqual(temporal._value_at(spec, "open", [], at("2026-01-25T00:00:00Z")), ("open", None))
        with self.assertRaises(temporal.TemporalError):
            temporal._spec("unit")


class ServerTests(unittest.TestCase):
    """Real events on a throwaway server."""

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
        conn.script(temporal.temporal_surql(self.inst))
        return conn

    @staticmethod
    def now(conn):
        return temporal.to_datetime(conn.sql("RETURN time::now()", expect=1)[0])

    @staticmethod
    def history(conn, table, ident):
        return conn.sql("SELECT VALUE %s FROM %s:%s" % (temporal.TRACKED[table]["history"], table, ident), expect=1)[0][0]

    def writer(self, conn):
        return write.Writer(conn, self.inst, {"agent_id": "tester", "role": "reader", "lens": None})

    def good_unit(self, conn, ident="u_ok"):
        conn.sql("CREATE unit:%s SET type='%s', text='probe', locator={p: 1}, status='%s', grade='%s', hash='h';"
                 % (ident, self.inst.unit_names()[0], self.inst.usable_statuses()[0], self.inst.confirming_grades()[0]))
        return ident

    # ---- applying on top of the generated schema -------------------------------------------------

    def test_applies_on_generated_schema_and_gate_probes_still_behave(self):
        conn = self.fresh("apply")
        report = gates.run_probes(self.inst, conn)
        self.assertEqual(report["misbehaved"], 0, report)
        self.assertEqual(report["rejected_ok"], len(self.inst.gate_rules()))
        self.assertEqual(report["accepted_ok"], len(self.inst.gate_rules()))
        own = temporal.run_probes(self.inst, conn)
        self.assertEqual(own["failed"], 0, own)
        self.assertGreaterEqual(own["passed"], 10)
        for table in ("unit", "fact", "conclusion", "critique"):
            self.assertEqual(conn.count(table), 0, "probe rows left behind in %s" % table)

    # ---- history entries: on change only ---------------------------------------------------------

    def test_propose_then_confirm_appends_one_entry_and_rewrites_append_nothing(self):
        conn = self.fresh("hist")
        w = self.writer(conn)
        w.propose("f1", "The pool was exhausted", reasoning_type="direct_quote")
        self.assertEqual(self.history(conn, "fact", "f1"), [])
        w.confirm("f1", [self.good_unit(conn)])
        h = self.history(conn, "fact", "f1")
        self.assertEqual(len(h), 1)
        self.assertEqual((h[0]["status_before"], h[0]["status_after"], h[0]["by"]),
                         ("proposed", "confirmed", "tester|role=reader"))
        # loader-style re-writes with the same status: the typed surface and a raw UPDATE that changes another field
        w.propose("f1", "The pool was exhausted", reasoning_type="direct_quote", note="re-labelled on a re-run")
        conn.sql("UPDATE fact:f1 SET status='confirmed', note='raw re-write'")
        self.assertEqual(self.history(conn, "fact", "f1"), h, "a same-status re-write must append nothing")
        self.assertEqual(temporal.history_count(conn, "fact"), 1)

    def test_demotion_appends_and_history_is_append_only(self):
        conn = self.fresh("demote")
        w = self.writer(conn)
        w.propose("f1", "s")
        w.confirm("f1", [self.good_unit(conn)])
        conn.sql("UPDATE fact:f1 SET status='stale'")            # a direct demotion, as a TMS event would do
        h = self.history(conn, "fact", "f1")
        self.assertEqual([(e["status_before"], e["status_after"]) for e in h],
                         [("proposed", "confirmed"), ("confirmed", "stale")])
        for tamper in ("UPDATE fact:f1 SET status_history = []",
                       "UPDATE fact:f1 SET status_history[0].status_after = 'x'",
                       "UPDATE fact:f1 SET status_history = [{status_before: 'a', status_after: 'b', at: time::now()}, "
                       "{status_before: 'a', status_after: 'b', at: time::now()}]"):
            ok, message = conn.try_sql(tamper)
            self.assertFalse(ok, tamper)
            self.assertIn("status_history", message)
        self.assertEqual(self.history(conn, "fact", "f1"), h)
        ok, _ = conn.try_sql("UPDATE fact:f1 SET status='open'")   # a real change is still accepted
        self.assertTrue(ok)
        self.assertEqual(len(self.history(conn, "fact", "f1")), 3)

    def test_conclusion_ready_history_both_ways(self):
        conn = self.fresh("ready")
        w = self.writer(conn)
        conn.sql("CREATE conclusion:c1 SET statement='root cause', ready=false")
        conn.sql("UPDATE conclusion:c1 SET ready=false, rating='re-write'")
        self.assertEqual(self.history(conn, "conclusion", "c1"), [])
        w.propose("f1", "s")
        w.confirm("f1", [self.good_unit(conn)])
        w.support("f1", "c1")
        cid = w.critique("c1", "an alternative cause", "survivable", lens="lens_skeptic")
        t_before_ready = self.now(conn)
        w.mark_ready("c1")
        h = self.history(conn, "conclusion", "c1")
        self.assertEqual([(e["ready_before"], e["ready_after"]) for e in h], [(False, True)])
        conn.sql("UPDATE %s SET verdict='fatal'" % cid)
        conn.sql("UPDATE conclusion:c1 SET ready=false")
        self.assertEqual(len(self.history(conn, "conclusion", "c1")), 2)
        self.assertEqual(temporal.as_of(conn, "conclusion", "c1", t_before_ready)["value"], False)
        self.assertEqual(temporal.as_of(conn, "conclusion", "conclusion:c1", self.now(conn))["value"], False)
        self.assertEqual(temporal.as_of(conn, "conclusion", "c1", self.now(conn))["history_length"], 2)

    # ---- as-of and snapshot ----------------------------------------------------------------------

    def test_as_of_three_instants(self):
        conn = self.fresh("asof")
        w = self.writer(conn)
        t0 = self.now(conn)
        w.propose("f1", "s", valid_from="2026-03-01T00:00:00Z", valid_to="2026-03-05T00:00:00Z")
        t1 = self.now(conn)
        w.confirm("f1", [self.good_unit(conn)])
        t2 = self.now(conn)
        before = temporal.as_of(conn, "fact", "f1", t0)
        self.assertEqual(before["known"], False)
        self.assertEqual(before["reason"], "not yet learned at that time")
        between = temporal.as_of(conn, "fact", "f1", t1)
        self.assertEqual((between["known"], between["value"]), (True, "proposed"))
        self.assertEqual(between["determined_by"]["reason"], "value held since learned_at")
        after = temporal.as_of(conn, "fact", "fact:f1", t2)
        self.assertEqual((after["value"], after["current"], after["consistent"]), ("confirmed", "confirmed", True))
        self.assertEqual((after["determined_by"]["status_before"], after["determined_by"]["status_after"]),
                         ("proposed", "confirmed"))
        self.assertEqual(after["valid_from"], "2026-03-01T00:00:00.000000Z")
        self.assertEqual(after["valid_then"], False)              # September is outside the claim's valid time
        # inside the claim's valid time but before the graph learned it: valid time never fakes knowledge
        inside = temporal.as_of(conn, "fact", "f1", _dt.datetime(2026, 3, 3, tzinfo=UTC))
        self.assertEqual(inside["known"], False)
        self.assertNotIn("valid_then", inside)
        self.assertEqual(temporal.as_of(conn, "fact", "nope", t2)["known"], False)
        row = conn.sql("SELECT valid_from, valid_to FROM fact:f1")[0][0]
        self.assertEqual(temporal.iso(row["valid_to"]), "2026-03-05T00:00:00.000000Z")

    def test_confirm_passes_valid_from_and_signatures_stay_compatible(self):
        conn = self.fresh("valid")
        w = self.writer(conn)
        w.propose("f1", "s")
        w.confirm("f1", [self.good_unit(conn)], note="n", valid_from=_dt.datetime(2026, 2, 1, tzinfo=UTC))
        row = conn.sql("SELECT status, note, valid_from, valid_to FROM fact:f1")[0][0]
        self.assertEqual((row["status"], row["note"], temporal.iso(row["valid_from"]), row["valid_to"]),
                         ("confirmed", "n", "2026-02-01T00:00:00.000000Z", None))
        w.propose("f1", "s", note="re-run")                       # no dates given: the bounds stay
        self.assertEqual(temporal.iso(conn.sql("SELECT valid_from FROM fact:f1")[0][0]["valid_from"]),
                         "2026-02-01T00:00:00.000000Z")
        self.assertEqual(temporal.as_of(conn, "fact", "f1", _dt.datetime(2026, 2, 2, tzinfo=UTC))["known"], False)
        now_view = temporal.as_of(conn, "fact", "f1", self.now(conn))
        self.assertEqual(now_view["valid_then"], True)            # open-ended valid_to

    def test_snapshot_at_three_instants_gives_three_different_counts(self):
        conn = self.fresh("snap")
        w = self.writer(conn)
        t0 = self.now(conn)
        w.propose("f1", "a")
        w.propose("f2", "b")
        t1 = self.now(conn)
        w.confirm("f1", [self.good_unit(conn)])
        conn.sql("UPDATE fact:f2 SET status='ruled_out'")
        t2 = self.now(conn)
        s0, s1, s2 = (temporal.snapshot(conn, t) for t in (t0, t1, t2))
        self.assertEqual(s0["fact"], {"by_status": {}, "known": 0, "not_yet_known": 2, "total": 2})
        self.assertEqual(s1["fact"], {"by_status": {"proposed": 2}, "known": 2, "not_yet_known": 0, "total": 2})
        self.assertEqual(s2["fact"]["by_status"], {"confirmed": 1, "ruled_out": 1})
        self.assertEqual(len({str(s["fact"]) for s in (s0, s1, s2)}), 3)
        self.assertEqual(s2["conclusion"]["total"], 0)
        # cross-check against the live counts: the snapshot "now" must equal what the graph says now
        live = {r["status"]: r["n"] for r in conn.sql("SELECT status, count() AS n FROM fact GROUP BY status")[0]}
        self.assertEqual(temporal.snapshot(conn, self.now(conn))["fact"]["by_status"], live)

    # ---- prove it can fail -----------------------------------------------------------------------

    def test_without_the_noop_guard_a_same_status_rewrite_appends_an_entry(self):
        """Deliberate break: remove the WHEN guard, watch a no-op re-write append, restore, watch it stop."""
        conn = self.fresh("noguard")                             # ("break" is a SurrealQL keyword)
        w = self.writer(conn)
        w.propose("f1", "s")
        w.confirm("f1", [self.good_unit(conn)])
        self.assertEqual(len(self.history(conn, "fact", "f1")), 1)
        # (a) the status guard replaced by a recursion guard only: fires on every change to the row
        conn.script(temporal.temporal_surql(self.inst, noop_guard=False))
        conn.sql("UPDATE fact:f1 SET status='confirmed', note='loader re-write'")
        h = self.history(conn, "fact", "f1")
        self.assertEqual(len(h), 2, "the unguarded event must append on a same-status re-write")
        self.assertEqual((h[1]["status_before"], h[1]["status_after"]), ("confirmed", "confirmed"))
        # (b) no guard at all: on 3.1.6 the event re-fires on its own append until the write is rejected
        conn.sql("DEFINE EVENT OVERWRITE fact_status_history ON TABLE fact WHEN $event = 'UPDATE' THEN { "
                 "UPDATE $after.id SET status_history += {status_before: $before.status, status_after: $after.status, "
                 "at: time::now(), by: $after.author}; };")
        ok, message = conn.try_sql("UPDATE fact:f1 SET note='again'")
        self.assertFalse(ok)
        self.assertIn("event fact_status_history", message)
        # restore: the generated statements put the guard back and the re-write appends nothing
        conn.script(temporal.temporal_surql(self.inst))
        conn.sql("UPDATE fact:f1 SET status='confirmed', note='after restore'")
        self.assertEqual(len(self.history(conn, "fact", "f1")), 2)

    def test_rows_that_predate_the_fields_are_backfilled(self):
        conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t", db="predate")
        conn.script(gates.schema_surql(self.inst))
        conn.script(gates.gate_surql(self.inst))
        conn.sql("CREATE fact:old SET statement='s', status='proposed'; CREATE conclusion:old SET statement='c', ready=false")
        conn.script(temporal.temporal_surql(self.inst))
        self.assertEqual(self.history(conn, "fact", "old"), [])
        self.assertIsNotNone(conn.sql("SELECT VALUE learned_at FROM conclusion:old")[0][0])
        conn.sql("UPDATE fact:old SET status='inferred'")
        self.assertEqual(len(self.history(conn, "fact", "old")), 1)


if __name__ == "__main__":
    unittest.main()
