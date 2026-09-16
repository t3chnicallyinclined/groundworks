# -*- coding: utf-8 -*-
"""Tests for queue.py: feed parsing is checked in pure Python against records measured on SurrealDB
3.1.6; delivery, resume and the two shipped handlers run against a throwaway in-memory server on
port 8033 (never a live one), including proof that the versionstamp discipline is load-bearing.

    python -m unittest discover -s tests -p "test_queue.py" -v
"""
import json
import pathlib
import subprocess
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
from xscholar import queue  # noqa: E402
from xscholar import surreal_bin  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
SURREAL = surreal_bin.find()
PORT = 8033


class ParsingTests(unittest.TestCase):
    """Pure Python, on records copied from SHOW CHANGES output on 3.1.6."""

    def test_text_patch_reverses_to_the_original_string(self):
        # measured: UPDATE fact SET status='open' on a row whose status was 'proposed'
        self.assertEqual(queue.apply_dmp_patch("open", "@@ -1,4 +1,8 @@\n-open\n+proposed\n"), "proposed")
        # keep-context then delete
        self.assertEqual(queue.apply_dmp_patch("t2 longer text here", "@@ -1,19 +1,1 @@\n t\n-2 longer text here\n"), "t")
        # empty <-> filled (length 0 hunks use a 0-based start)
        self.assertEqual(queue.apply_dmp_patch("", "@@ -0,0 +1,2 @@\n+s1\n"), "s1")
        self.assertEqual(queue.apply_dmp_patch("filled again", "@@ -1,12 +0,0 @@\n-filled again\n"), "")
        # percent-encoded newline, %, unicode, <>"; literal space, &, +, -
        original = 'line1\nline2 50% done: ünïcödé & <tag> "q" +plus -minus'
        patch = ("@@ -1,1 +1,54 @@\n-x\n+line1%0Aline2 50%25 done: %C3%BCn%C3%AFc%C3%B6d%C3%A9 & "
                 "%3Ctag%3E %22q%22 +plus -minus\n")
        self.assertEqual(queue.apply_dmp_patch("x", patch), original)
        with self.assertRaises(queue.PatchError):
            queue.apply_dmp_patch("something else", "@@ -1,4 +1,8 @@\n-open\n+proposed\n")

    def test_reverse_patch_rebuilds_the_before_image(self):
        after = {"id": "unit:u1", "grade": "agent_summary", "meta": {"k": 1}, "text": "t2 longer text here"}
        ops = [{"op": "remove", "path": "/meta"},
               {"op": "change", "path": "/grade", "value": "@@ -1,13 +1,12 @@\n-agent_summary\n+original_log\n"},
               {"op": "change", "path": "/text", "value": "@@ -1,19 +1,1 @@\n t\n-2 longer text here\n"}]
        self.assertEqual(queue.reverse_patch(after, ops), {"id": "unit:u1", "grade": "original_log", "text": "t"})
        self.assertEqual(queue.reverse_patch({"ready": True}, [{"op": "replace", "path": "/ready", "value": False}]),
                         {"ready": False})
        self.assertEqual(queue.reverse_patch({"a": 1}, [{"op": "add", "path": "/note", "value": "n1"}]),
                         {"a": 1, "note": "n1"})

    def test_entry_kinds_and_changed_paths(self):
        create = {"versionstamp": 5, "changes": [{"update": {"id": "fact:f1", "status": "proposed"}}]}
        update = {"versionstamp": 6, "changes": [{"current": {"id": "fact:f1", "status": "open"},
                                                  "update": [{"op": "change", "path": "/status",
                                                              "value": "@@ -1,4 +1,8 @@\n-open\n+proposed\n"}]}]}
        noop = {"versionstamp": 7, "changes": [{"current": {"id": "fact:f1", "status": "open"}, "update": []}]}
        delete = {"versionstamp": 8, "changes": [{"delete": {"id": "fact:f1"}}, {"delete": {"id": "fact:f2"}}]}
        schema = {"versionstamp": 9, "changes": [{"define_table": {"name": "fact"}}]}
        c = queue.parse_entry("fact", create)[0]
        self.assertEqual((c.kind, c.id, c.before, c.after["status"]), ("create", "fact:f1", None, "proposed"))
        u = queue.parse_entry("fact", update)[0]
        self.assertEqual((u.kind, u.before["status"], u.after["status"], u.changed_paths), ("update", "proposed", "open", ["/status"]))
        self.assertTrue(u.changed("status", "grade"))
        n = queue.parse_entry("fact", noop)[0]
        self.assertEqual((n.kind, n.changed_paths, n.before), ("update", [], n.after))
        self.assertFalse(n.changed("status", "grade"))
        ds = queue.parse_entry("fact", delete, {"fact:f1": {"id": "fact:f1", "statement": "s"}})
        self.assertEqual([(d.kind, d.id) for d in ds], [("delete", "fact:f1"), ("delete", "fact:f2")])
        self.assertEqual(ds[0].before["statement"], "s")
        self.assertIsNone(ds[1].before)
        self.assertIn("no before-image", ds[1].before_error)
        self.assertEqual(queue.parse_entry("fact", schema), [])

    def test_changefeed_surql_alters_every_watched_table(self):
        text = queue.changefeed_surql(config.load(DEMO), retention="3d")
        for table in queue.TABLES:
            self.assertIn("ALTER TABLE %s CHANGEFEED 3d INCLUDE ORIGINAL;" % table, text)
        self.assertNotIn("DEFINE TABLE OVERWRITE", text)
        self.assertIn("DEFINE TABLE IF NOT EXISTS review", text)
        self.assertIn("DEFINE TABLE IF NOT EXISTS archive", text)

    def test_unknown_handler_kind_is_refused(self):
        with self.assertRaises(queue.QueueError):
            queue.Worker(None, tempfile.mktemp(), {("fact", "upsert"): lambda c, d: None})


class ServerTests(unittest.TestCase):
    """Real feeds on a throwaway server."""

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
        cls.tmp = tempfile.mkdtemp(prefix="xscholar_queue_")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    # -- helpers --

    def fresh(self, name):
        conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t", db=name)
        conn.script(gates.schema_surql(self.inst))
        conn.script(gates.gate_surql(self.inst))
        conn.script(queue.changefeed_surql(self.inst))
        return conn

    def state_path(self, name):
        return pathlib.Path(self.tmp) / (name + ".json")

    def worker(self, conn, name, handlers=None, **kw):
        return queue.Worker(conn, self.state_path(name), handlers if handlers is not None else queue.default_handlers(self.inst), **kw)

    def unit(self, conn, uid, grade="original_log", status="in_window"):
        conn.sql("CREATE unit:%s SET type='log_line', text='t', locator={p: 1}, status='%s', grade='%s', hash='h'"
                 % (uid, status, grade))

    def confirmed_fact(self, conn, fid, *units):
        conn.sql("CREATE fact:%s SET statement='fact %s', status='proposed';" % (fid, fid))
        for uid in units:
            conn.sql("RELATE fact:%s->cites->unit:%s;" % (fid, uid))
        conn.sql("UPDATE fact:%s SET status='confirmed';" % fid)

    def status(self, conn, fid):
        return conn.sql("SELECT VALUE status FROM fact:%s" % fid)[0][0]

    # -- schema --

    def test_changefeed_keeps_schema_gates_and_probes_both_ways(self):
        conn = self.fresh("schema")
        tables = conn.sql("INFO FOR DB")[0]["tables"]
        for table in ("unit", "fact", "conclusion", "critique"):
            self.assertIn("SCHEMAFULL CHANGEFEED 1w INCLUDE ORIGINAL", tables[table], tables[table])
        self.assertIn("TYPE RELATION IN fact OUT unit SCHEMALESS CHANGEFEED 1w INCLUDE ORIGINAL", tables["cites"])
        self.assertIn("ASSERT", conn.sql("INFO FOR TABLE fact")[0]["fields"]["status"])
        report = gates.run_probes(self.inst, conn)
        self.assertEqual(report["misbehaved"], 0, report)
        self.assertEqual(report["rejected_ok"], len(self.inst.gate_rules()))
        self.assertEqual(report["accepted_ok"], len(self.inst.gate_rules()))
        # the other way: the bare re-declare form loses SCHEMAFULL, and re-applying the schema
        # loses the changefeed. Both are the reasons the generator emits ALTER TABLE and must run last.
        conn.sql("DEFINE TABLE OVERWRITE conclusion CHANGEFEED 7d INCLUDE ORIGINAL;")
        self.assertIn("SCHEMALESS", conn.sql("INFO FOR DB")[0]["tables"]["conclusion"])
        conn.sql("DEFINE TABLE OVERWRITE conclusion SCHEMAFULL;")
        self.assertNotIn("CHANGEFEED", conn.sql("INFO FOR DB")[0]["tables"]["conclusion"])
        conn.sql("ALTER TABLE conclusion CHANGEFEED 7d INCLUDE ORIGINAL;")
        self.assertIn("SCHEMAFULL CHANGEFEED", conn.sql("INFO FOR DB")[0]["tables"]["conclusion"])

    # -- stale re-check --

    def test_unit_downgrade_makes_the_citing_fact_stale_once(self):
        conn = self.fresh("stale")
        self.unit(conn, "u1")
        self.confirmed_fact(conn, "f1", "u1")
        w = self.worker(conn, "stale")
        w.run_once()  # first run: starts at the current end of every feed, nothing replayed
        self.assertEqual(self.status(conn, "f1"), "confirmed")
        conn.sql("UPDATE unit:u1 SET grade='agent_summary'")
        counts = w.run_once()
        self.assertEqual(counts["errors"], [])
        self.assertEqual(self.status(conn, "f1"), "stale")
        reviews = conn.sql("SELECT * FROM review")[0]
        self.assertEqual(len(reviews), 1, reviews)
        self.assertEqual((reviews[0]["target"], reviews[0]["cause"], reviews[0]["kind"]),
                         ("fact:f1", "unit:u1", "stale_citation_unit_changed"))
        # the fact's own confirmed->stale entry was already read in the same pass (fact follows unit)
        self.assertEqual(counts["per_table"]["fact"]["changes"], 1)
        self.assertEqual(w.run_once()["entries"], 0)
        self.assertEqual(conn.count("review"), 1)
        # re-deliver the very same change by rewinding the stored versionstamp: still one review
        w.state["versionstamps"]["unit"] = counts["per_table"]["unit"]["from"]
        again = w.run_once()
        self.assertEqual(again["per_table"]["unit"]["changes"], 1)
        self.assertEqual(conn.count("review"), 1)
        self.assertEqual(self.status(conn, "f1"), "stale")

    def test_noop_rewrite_changes_nothing(self):
        conn = self.fresh("noop")
        self.unit(conn, "u1")
        self.confirmed_fact(conn, "f1", "u1")
        w = self.worker(conn, "noop")
        w.run_once()
        # loader churn: UPSERT ... CONTENT with identical content still writes a feed entry (with an
        # empty patch); UPDATE SET with equal values writes none (measured on 3.1.6)
        conn.sql("UPSERT unit:u1 CONTENT {type:'log_line', text:'t', locator:{p: 1}, status:'in_window', "
                 "grade:'original_log', hash:'h'}")
        conn.sql("UPDATE unit:u1 SET grade='original_log', status='in_window'")
        conn.sql("UPSERT unit:u1 MERGE {meta: {seen: true}}")                                # unrelated field
        counts = w.run_once()
        self.assertEqual(counts["per_table"]["unit"]["changes"], 2, "the feed does record churn writes")
        self.assertEqual(counts["errors"], [])
        self.assertEqual(self.status(conn, "f1"), "confirmed")
        self.assertEqual(conn.count("review"), 0)

    def test_second_good_citation_keeps_the_fact_confirmed(self):
        conn = self.fresh("twocites")
        self.unit(conn, "u1")
        self.unit(conn, "u2")
        self.confirmed_fact(conn, "f1", "u1", "u2")
        w = self.worker(conn, "twocites")
        w.run_once()
        conn.sql("UPDATE unit:u1 SET status='redacted'")
        w.run_once()
        self.assertEqual(self.status(conn, "f1"), "confirmed")
        self.assertEqual(conn.count("review"), 0)
        conn.sql("UPDATE unit:u2 SET status='out_of_window'")
        w.run_once()
        self.assertEqual(self.status(conn, "f1"), "stale")
        self.assertEqual(conn.count("review"), 1)

    def test_deleted_cites_edge_makes_the_fact_stale(self):
        conn = self.fresh("edge")
        w = self.worker(conn, "edge")
        w.run_once()
        self.unit(conn, "u1")
        self.confirmed_fact(conn, "f1", "u1")
        w.run_once()  # the worker sees the RELATE, so the edge image is cached for the delete
        edge = conn.sql("SELECT VALUE id FROM cites WHERE in = fact:f1")[0][0]
        conn.sql("DELETE fact:f1->cites")
        counts = w.run_once()
        self.assertEqual(counts["errors"], [])
        self.assertEqual(self.status(conn, "f1"), "stale")
        reviews = conn.sql("SELECT * FROM review")[0]
        self.assertEqual([(r["target"], r["cause"], r["kind"]) for r in reviews],
                         [("fact:f1", edge, "stale_citation_deleted")])

    def test_uncached_edge_delete_falls_back_to_a_sweep(self):
        conn = self.fresh("sweep")
        self.unit(conn, "u1")
        self.confirmed_fact(conn, "f1", "u1")
        w = self.worker(conn, "sweep")
        w.run_once()  # started after the RELATE: no cached edge image
        conn.sql("DELETE fact:f1->cites")
        counts = w.run_once()
        self.assertEqual(counts["errors"], [])
        self.assertEqual(self.status(conn, "f1"), "stale")
        self.assertEqual(conn.count("review"), 1)

    # -- archive --

    def test_delete_writes_an_archive_row_with_the_snapshot(self):
        conn = self.fresh("archive")
        w = self.worker(conn, "archive")
        w.run_once()
        conn.sql("CREATE fact:f1 SET statement='to be deleted', status='proposed', note='n'")
        conn.sql("CREATE critique:k1 SET lens='l', head='h', verdict='survivable'")
        w.run_once()
        conn.sql("DELETE fact:f1; DELETE critique:k1;")
        counts = w.run_once()
        self.assertEqual(counts["errors"], [])
        rows = {r["original_id"]: r for r in conn.sql("SELECT * FROM archive")[0]}
        self.assertEqual(set(rows), {"fact:f1", "critique:k1"})
        self.assertEqual(rows["fact:f1"]["snapshot"]["statement"], "to be deleted")
        self.assertEqual(rows["fact:f1"]["snapshot"]["note"], "n")
        self.assertEqual(rows["fact:f1"]["original_table"], "fact")
        self.assertEqual(rows["fact:f1"]["reason"], "deleted")
        self.assertFalse(rows["fact:f1"]["legal_hold"])
        self.assertEqual(rows["critique:k1"]["snapshot"]["head"], "h")

    def test_delete_of_a_row_never_seen_records_the_missing_snapshot(self):
        conn = self.fresh("unseen")
        conn.sql("CREATE fact:f0 SET statement='before the worker', status='proposed'")
        w = self.worker(conn, "unseen")
        w.run_once()
        conn.sql("DELETE fact:f0")
        w.run_once()
        row = conn.sql("SELECT * FROM archive")[0][0]
        self.assertEqual(row["original_id"], "fact:f0")
        self.assertIsNone(row.get("snapshot"))
        self.assertIn("no before-image", row["reason"])

    # -- resume --

    def test_resume_after_a_stop_handles_new_changes_exactly_once(self):
        conn = self.fresh("resume")
        delivered = []

        def spy(change, db):
            delivered.append((change.table, change.kind, change.id, change.versionstamp))

        handlers = queue.default_handlers(self.inst)
        for table in queue.TABLES:
            for kind in queue.KINDS:
                handlers.setdefault((table, kind), []).append(spy)
        a = self.worker(conn, "resume", handlers)
        a.run_once()
        self.unit(conn, "u1")
        for fid in ("f1", "f2", "f3"):
            self.confirmed_fact(conn, fid, "u1")
        conn.sql("DELETE fact:f1")
        first = a.run_once()
        self.assertEqual(first["errors"], [])
        self.assertEqual(conn.count("archive"), 1)
        seen_by_a = list(delivered)
        del a  # the worker stops; only the state file survives
        # changes while nobody is listening
        conn.sql("DELETE fact:f2")
        conn.sql("UPDATE unit:u1 SET grade='agent_summary'")
        conn.sql("CREATE conclusion:c1 SET statement='c', ready=false")
        b = self.worker(conn, "resume", handlers)
        self.assertEqual(b.versionstamp("fact"), first["per_table"]["fact"]["to"])
        second = b.run_once()
        self.assertEqual(second["errors"], [])
        new = delivered[len(seen_by_a):]
        self.assertEqual(len(delivered), len(set(delivered)), "a change was delivered twice")
        self.assertEqual(sorted((t, k, i) for t, k, i, _ in new),
                         sorted([("fact", "delete", "fact:f2"), ("unit", "update", "unit:u1"),
                                 ("conclusion", "create", "conclusion:c1"),
                                 ("fact", "update", "fact:f3"),              # confirmed -> stale by the handler
                                 ("cites", "delete", [d for d in new if d[0] == "cites"][0][2])]))
        self.assertEqual(conn.count("archive"), 2)                 # f1 (before the stop) + f2 (after)
        self.assertEqual(conn.count("review"), 1)                  # only f3 was still confirmed and citing u1
        self.assertEqual(self.status(conn, "f3"), "stale")
        self.assertEqual(b.run_once()["entries"], 0)               # nothing left: f3's stale entry was in the pass
        self.assertEqual(json.loads(self.state_path("resume").read_text())["versionstamps"]["fact"], b.versionstamp("fact"))

    def test_first_run_can_replay_history_when_asked(self):
        conn = self.fresh("replay")
        conn.sql("CREATE fact:f1 SET statement='old', status='proposed'")
        conn.sql("DELETE fact:f1")
        skip = self.worker(conn, "replay_skip")
        skipped = skip.run_once()
        self.assertEqual(skipped["changes"], 0)
        # the run that threw history away names itself as such
        self.assertIn("fact", skipped["tables_fresh"])
        self.assertEqual(skipped["tables_replayed"], [])
        self.assertGreater(skipped["entries_skipped_by_fresh_start"], 0)
        self.assertIn("FIRST RUN for", queue.report_line(skipped))
        replay = self.worker(conn, "replay_all", replay_from_start=True)
        counts = replay.run_once()
        self.assertEqual(counts["per_table"]["fact"]["changes"], 2)
        self.assertEqual(conn.count("archive"), 1)
        self.assertEqual(conn.sql("SELECT VALUE snapshot.statement FROM archive")[0], ["old"])
        # a replay threw nothing away, so it must NOT be reported as a first run that did
        self.assertEqual(counts["tables_fresh"], [])
        self.assertEqual(sorted(counts["tables_replayed"]), sorted(queue.TABLES))
        self.assertEqual(counts["entries_skipped_by_fresh_start"], 0)
        line = queue.report_line(counts)
        self.assertNotIn("FIRST RUN for", line)
        self.assertIn("5 replayed from the start", line)

    # -- failure handling --

    def test_a_raising_handler_holds_the_versionstamp_and_is_redelivered(self):
        conn = self.fresh("raise")
        calls = {"n": 0, "fail": True}

        def flaky(change, db):
            calls["n"] += 1
            if calls["fail"]:
                raise RuntimeError("handler down")

        w = self.worker(conn, "raise", {("fact", "delete"): [flaky] + list(queue.archive_handlers().values())[:1]})
        w.run_once()
        conn.sql("CREATE fact:f1 SET statement='s', status='proposed'")
        w.run_once()
        before = w.versionstamp("fact")
        conn.sql("DELETE fact:f1")
        counts = w.run_once()
        self.assertEqual(len(counts["errors"]), 1)
        self.assertIn("handler down", counts["errors"][0]["error"])
        self.assertEqual(w.versionstamp("fact"), before, "versionstamp advanced past a failed handler")
        self.assertEqual(json.loads(self.state_path("raise").read_text())["versionstamps"]["fact"], before)
        self.assertEqual(conn.count("archive"), 0)
        calls["fail"] = False
        counts = w.run_once()
        self.assertEqual(counts["errors"], [])
        self.assertEqual(calls["n"], 2, "the change was not re-delivered")
        self.assertEqual(conn.count("archive"), 1)
        self.assertGreater(w.versionstamp("fact"), before)

    def test_deliberate_break_advancing_first_loses_the_change(self):
        """Prove the discipline is load-bearing: advance the versionstamp before dispatch and a
        raising handler loses the change for good. The knob is restored at the end."""
        conn = self.fresh("brk")
        calls = {"n": 0, "fail": True}

        def flaky(change, db):
            calls["n"] += 1
            if calls["fail"]:
                raise RuntimeError("handler down")

        w = self.worker(conn, "brk", {("fact", "delete"): [flaky] + list(queue.archive_handlers().values())[:1]})
        w.run_once()
        conn.sql("CREATE fact:f1 SET statement='s', status='proposed'")
        w.run_once()
        before = w.versionstamp("fact")
        conn.sql("DELETE fact:f1")
        w._advance_before_dispatch = True
        try:
            counts = w.run_once()
            self.assertEqual(len(counts["errors"]), 1)
            self.assertGreater(w.versionstamp("fact"), before, "broken worker did not advance first")
            calls["fail"] = False
            counts = w.run_once()
        finally:
            w._advance_before_dispatch = False
        self.assertEqual(counts["errors"], [])
        self.assertEqual(calls["n"], 1, "with the break in place the change must NOT come back")
        self.assertEqual(conn.count("archive"), 0, "the delete was lost: no archive row, ever")
        # restored: a fresh delete under the normal discipline is archived
        conn.sql("CREATE fact:f2 SET statement='s', status='proposed'")
        w.run_once()
        conn.sql("DELETE fact:f2")
        w.run_once()
        self.assertEqual(conn.count("archive"), 1)

    def test_run_forever_bounded_and_report_line(self):
        conn = self.fresh("forever")
        w = self.worker(conn, "forever")
        lines = []
        # Two passes over an idle feed. The FIRST one is a fresh start and says so; the second is a
        # genuinely quiet resume and says nothing.
        self.assertEqual(queue.run_forever(w, interval_s=0.01, max_passes=2, log=lines.append), 2)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("FIRST RUN for", lines[0])
        del lines[:]
        conn.sql("CREATE fact:f1 SET statement='s', status='proposed'")
        queue.run_forever(w, interval_s=0.01, max_passes=1, log=lines.append)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("QUEUE: 1 entries, 1 changes"), lines[0])
        self.assertNotIn("FIRST RUN", lines[0])

    # -- a pass that read nothing is not a clean pass --

    def test_a_watched_table_with_no_changefeed_is_an_error_not_a_quiet_pass(self):
        """SHOW CHANGES on a table with no changefeed returns [], exactly like "nothing new"."""
        conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="t", db="nofeed")
        conn.script(gates.schema_surql(self.inst))
        conn.script(gates.gate_surql(self.inst))          # note: changefeed_surql deliberately NOT applied
        self.unit(conn, "u1")
        self.confirmed_fact(conn, "f1", "u1")
        self.assertEqual(conn.sql("SHOW CHANGES FOR TABLE unit SINCE 1 LIMIT 10", expect=1)[0], [])
        w = self.worker(conn, "nofeed")
        self.assertEqual(sorted(w.missing_changefeeds()), sorted(queue.TABLES))
        counts = w.run_once()
        self.assertEqual(counts["entries"], 0)
        self.assertEqual(sorted(counts["changefeeds_missing"]), sorted(queue.TABLES))
        self.assertEqual(len(counts["errors"]), len(queue.TABLES))
        self.assertIn("no CHANGEFEED", counts["errors"][0]["error"])
        line = queue.report_line(counts)
        self.assertIn("NO CHANGEFEED on", line)
        # a table with no feed is never given a versionstamp that would hide the gap later
        self.assertEqual(w.state["versionstamps"], {})

        # the other way: restore the changefeed and the same worker reports a real pass
        conn.script(queue.changefeed_surql(self.inst))
        w2 = self.worker(conn, "nofeed")
        self.assertEqual(w2.missing_changefeeds(), [])
        w2.run_once()
        conn.sql("UPDATE unit:u1 SET grade='agent_summary'")
        good = w2.run_once()
        self.assertEqual(good["changefeeds_missing"], [])
        self.assertEqual(good["errors"], [])
        self.assertEqual(self.status(conn, "f1"), "stale")

    def test_a_fresh_start_is_never_reported_as_a_clean_resume(self):
        conn = self.fresh("freshstart")
        self.unit(conn, "u1")
        self.confirmed_fact(conn, "f1", "u1")
        conn.sql("UPDATE unit:u1 SET grade='agent_summary'")   # happened while nobody was listening
        path = self.state_path("freshstart")
        if path.exists():
            path.unlink()
        w = self.worker(conn, "freshstart")
        counts = w.run_once()
        self.assertEqual(counts["entries"], 0)                 # nothing delivered, as documented
        self.assertFalse(counts["state_file"]["existed"])
        self.assertEqual(sorted(counts["tables_fresh"]), sorted(queue.TABLES))
        self.assertEqual(counts["tables_resumed"], [])
        self.assertGreater(counts["entries_skipped_by_fresh_start"], 0)
        self.assertEqual(self.status(conn, "f1"), "confirmed")  # the change really was skipped
        line = queue.report_line(counts)
        self.assertIn("NOT FOUND", line)
        self.assertIn("FIRST RUN for", line)
        self.assertIn("will never be delivered", line)

        # a real resume over the same feed says something different
        conn.sql("UPDATE unit:u1 SET grade='original_log'")
        again = self.worker(conn, "freshstart").run_once()
        self.assertTrue(again["state_file"]["existed"])
        self.assertEqual(again["tables_fresh"], [])
        self.assertEqual(sorted(again["tables_resumed"]), sorted(queue.TABLES))
        self.assertEqual(again["entries_skipped_by_fresh_start"], 0)
        resumed_line = queue.report_line(again)
        self.assertIn("5 table(s) resumed", resumed_line)
        self.assertNotIn("FIRST RUN", resumed_line)

    def test_run_forever_never_stays_silent_about_a_pass_that_read_nothing(self):
        """0 entries and 0 errors is exactly what a fresh start reports, so it must still be logged."""
        conn = self.fresh("silent")
        self.unit(conn, "u1")
        path = self.state_path("silent")
        if path.exists():
            path.unlink()
        lines = []
        queue.run_forever(self.worker(conn, "silent"), interval_s=0.01, max_passes=1, log=lines.append)
        self.assertEqual(len(lines), 1, "a first run that threw history away logged nothing")
        self.assertIn("FIRST RUN for", lines[0])
        self.assertTrue(lines[0].startswith("QUEUE: 0 entries"), lines[0])
        # and a genuinely quiet resume still says nothing
        quiet = []
        queue.run_forever(self.worker(conn, "silent"), interval_s=0.01, max_passes=1, log=quiet.append)
        self.assertEqual(quiet, [])


if __name__ == "__main__":
    unittest.main()
