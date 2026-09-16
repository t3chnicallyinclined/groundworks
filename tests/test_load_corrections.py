"""A correction must survive a reload, and a reload that disagrees with a correction must say so.

Found on a live instance, then measured on this engine with the demo's own vocabulary: `loader.load()`
UPSERTed every unit's status and grade from source unconditionally, so a routine rebuild silently put a
human correction back.

    after first load       : {'status': 'redacted'}
    after a human corrects : {'status': 'in_window'}
    after a routine reload : {'status': 'redacted'}      <-- correction silently reverted

`redacted` is unusable, so the revert can pull the only valid citation out from under a confirmed fact,
and nothing reported it. The opposite bug is as bad: a fix that holds every value would silently ignore
a real change in the source. Both directions are tested here, on a throwaway in-memory SurrealDB.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from xscholar import config  # noqa: E402
from xscholar import consistency  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar import gates  # noqa: E402
from xscholar import surreal_bin  # noqa: E402
from xscholar import temporal  # noqa: E402
from xscholar.pipeline import loader  # noqa: E402
from xscholar.pipeline import write  # noqa: E402

PORT = 8030
ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
SURREAL = surreal_bin.find()

# Synthetic units in the demo's vocabulary. usable: in_window; confirming grades include original_log.
BASE = {"u1": ("redacted", "original_log"), "u2": ("in_window", "original_log"), "u3": ("in_window", "original_log")}


class ReconcileTests(unittest.TestCase):
    """The decision alone, no datastore."""

    def unit(self, status, grade="original_log"):
        return {"id": "u1", "status": status, "grade": grade}

    def test_an_uncorrected_field_follows_a_source_change(self):
        stored = {"status": "in_window", "grade": "original_log",
                  "source": {"status": "in_window", "grade": "original_log", "run": "r0"}, "corrected": None}
        plan = loader.reconcile(stored, self.unit("redacted"), "r1")
        self.assertEqual(plan["set"]["status"], "redacted")
        self.assertEqual(plan["followed"], [{"unit": "u1", "field": "status", "was": "in_window", "now": "redacted"}])
        self.assertEqual(plan["conflict"], [])

    def test_a_held_field_is_not_set_and_its_disagreement_is_a_conflict(self):
        stored = {"status": "in_window", "grade": "original_log",
                  "source": {"status": "redacted", "grade": "original_log", "run": "r0"},
                  "corrected": {"status": {"by": "reviewer_1", "reason": "corrected"}}}
        plan = loader.reconcile(stored, self.unit("redacted"), "r1")
        self.assertNotIn("status", plan["set"])
        self.assertEqual(len(plan["conflict"]), 1)
        self.assertEqual(plan["conflict"][0]["by"], "reviewer_1")


class CorrectionServerTests(unittest.TestCase):

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

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = pathlib.Path(self.tmp.name)
        (self.work / "labels").mkdir()
        (self.work / "labels" / "c1.jsonl").write_text(
            "".join(json.dumps({"id": uid, "tier": 0}) + "\n" for uid in BASE), encoding="utf-8")
        self.conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="corrections", db=self.id().split(".")[-1][:60])
        self.conn.script(gates.schema_surql(self.inst))
        self.conn.script(gates.gate_surql(self.inst))
        self.conn.script(temporal.temporal_surql(self.inst))

    def source(self, **changes):
        """Write units.jsonl: BASE, with `uid=(status, grade)` overrides."""
        rows = dict(BASE, **changes)
        (self.work / "units.jsonl").write_text("".join(
            json.dumps({"id": uid, "type": "log_line", "text": "line %s" % uid, "locator": {"line": i},
                        "status": st, "grade": gr, "hash": "h%s" % uid}) + "\n"
            for i, (uid, (st, gr)) in enumerate(sorted(rows.items()))), encoding="utf-8")

    def load(self, **kw):
        return loader.load(self.work / "units.jsonl", self.work / "labels", self.conn, self.inst, **kw)

    def row(self, uid):
        return self.conn.sql("SELECT status, grade, corrected, status_history, grade_history FROM unit:%s" % uid,
                             expect=1)[0][0]

    # ---- the defect --------------------------------------------------------------------------------

    def test_a_correction_survives_a_reload(self):
        self.source()
        self.load()
        self.assertEqual(self.row("u1")["status"], "redacted")
        loader.correct_unit(self.conn, "u1", "status", "in_window", by="reviewer_1", note="checked the original")
        self.load()
        self.assertEqual(self.row("u1")["status"], "in_window", "a routine reload reverted a correction")

    def test_a_reload_that_disagrees_reports_the_conflict_by_name_and_count_and_does_not_overwrite(self):
        self.source()
        self.load()
        loader.correct_unit(self.conn, "u1", "status", "in_window", by="reviewer_1")
        counts = self.load()
        self.assertEqual(counts["conflicts"], 1)
        self.assertEqual(counts["conflict_rows"], [{"unit": "u1", "field": "status", "corrected": "in_window",
                                                    "source": "redacted", "reason": "corrected", "by": "reviewer_1"}])
        self.assertEqual(counts["held"], 1)
        self.assertEqual(self.row("u1")["status"], "in_window")
        # a held value the source later agrees with is still held, but is not a conflict
        self.source(u1=("in_window", "original_log"))
        again = self.load()
        self.assertEqual((again["held"], again["conflicts"]), (1, 0))

    def test_accept_source_overwrites_only_the_pair_a_human_named(self):
        self.source()
        self.load()
        loader.correct_unit(self.conn, "u1", "status", "in_window", by="reviewer_1")
        loader.correct_unit(self.conn, "u2", "status", "out_of_window", by="reviewer_1")
        self.load()                                            # not asked: nothing accepted
        self.assertEqual((self.row("u1")["status"], self.row("u2")["status"]), ("in_window", "out_of_window"))
        counts = self.load(accept_source=[("u1", "status")])
        self.assertEqual(counts["accepted"], 1)
        self.assertEqual(counts["accepted_rows"][0]["unit"], "u1")
        self.assertEqual(self.row("u1")["status"], "redacted", "the named acceptance did not take the source value")
        self.assertFalse((self.row("u1")["corrected"] or {}).get("status"), "the hold was not released")
        self.assertEqual(self.row("u2")["status"], "out_of_window", "an acceptance spread to a pair nobody named")
        self.assertEqual(counts["conflicts"], 1)
        # and once released, u1 follows the source again
        self.source(u1=("out_of_window", "original_log"))
        self.load()
        self.assertEqual(self.row("u1")["status"], "out_of_window")

    def test_an_acceptance_that_matches_no_hold_is_refused_before_anything_is_written(self):
        self.source()
        self.load()
        self.source(u3=("redacted", "original_log"))
        with self.assertRaises(loader.LoadError) as raised:
            self.load(accept_source=[("u2", "status")])        # u2 holds nothing: a typo, not an acceptance
        self.assertIn("u2.status", str(raised.exception))
        self.assertEqual(self.row("u3")["status"], "in_window", "a refused load still wrote units")

    def test_a_unit_nobody_corrected_still_updates_from_source(self):
        self.source()
        self.load()
        loader.correct_unit(self.conn, "u1", "status", "in_window", by="reviewer_1")
        self.source(u2=("out_of_window", "exported_metric"))
        counts = self.load()
        self.assertEqual((self.row("u2")["status"], self.row("u2")["grade"]), ("out_of_window", "exported_metric"),
                         "the fix froze a unit nobody corrected")
        self.assertEqual(counts["followed"], 2)
        self.assertEqual({(r["unit"], r["field"]) for r in counts["followed_rows"]}, {("u2", "status"), ("u2", "grade")})

    def test_a_grade_correction_survives_and_its_conflict_is_reported(self):
        self.source()
        self.load()
        loader.correct_unit(self.conn, "u3", "grade", "recollection", by="reviewer_1")
        counts = self.load()
        self.assertEqual(self.row("u3")["grade"], "recollection", "a reload reverted a grade correction")
        self.assertEqual([(r["unit"], r["field"], r["corrected"], r["source"]) for r in counts["conflict_rows"]],
                         [("u3", "grade", "recollection", "original_log")])

    # ---- corrections that did not come through correct_unit() -------------------------------------

    def test_a_raw_edit_between_loads_is_held_and_reported_as_unattributed(self):
        self.source()
        self.load()
        self.conn.sql("UPDATE unit:u1 SET status = 'in_window'")
        counts = self.load()
        self.assertEqual(self.row("u1")["status"], "in_window")
        self.assertEqual(counts["conflict_rows"][0]["reason"], "changed_outside_loader")
        self.assertIn("UNATTRIBUTED", counts["conflict_rows"][0]["by"])
        self.assertEqual(self.load()["conflicts"], 1, "the hold did not persist to the next load")

    def test_a_row_loaded_before_provenance_existed_is_held_not_reverted(self):
        """The upgrade path: a live instance whose rows carry no `source` yet."""
        self.source()
        self.load()
        self.conn.sql("UPDATE unit:u1 SET status = 'in_window', source = NONE; UPDATE unit:u2 SET source = NONE;")
        counts = self.load()
        self.assertEqual(self.row("u1")["status"], "in_window")
        self.assertEqual([(r["unit"], r["reason"]) for r in counts["conflict_rows"]], [("u1", "no_provenance")])
        self.assertEqual(counts["held"], 1, "a row that agrees with the source was held for no reason")

    def test_a_correction_landing_mid_load_stops_the_load_instead_of_being_overwritten(self):
        self.source()
        self.load()
        conn = self.conn

        class CorrectsAfterRead(dbmod.Db):
            def sql(inner, statement, expect=None):
                result = conn.sql(statement, expect)
                if statement.startswith("SELECT record::id(id) AS key"):
                    conn.sql("UPDATE unit:u2 SET status = 'redacted'")
                return result

            def script(inner, text):
                return conn.script(text)

        self.source(u2=("out_of_window", "original_log"))
        racing = CorrectsAfterRead(url=conn.url, ns=conn.ns, db=conn.db)
        with self.assertRaises(loader.LoadError) as raised:
            loader.load(self.work / "units.jsonl", self.work / "labels", racing, self.inst)
        self.assertIn("u2", str(raised.exception))
        self.assertEqual(self.row("u2")["status"], "redacted", "the load overwrote a correction it never read")

    # ---- the record --------------------------------------------------------------------------------

    def test_history_names_the_corrector_the_loader_and_an_unattributed_edit(self):
        self.source()
        self.load()
        loader.correct_unit(self.conn, "u1", "status", "in_window", by="reviewer_1")
        self.load()                                            # held: no change, no entry
        self.load(accept_source=[("u1", "status")])
        self.conn.sql("UPDATE unit:u1 SET status = 'out_of_window'")
        loader.correct_unit(self.conn, "u2", "grade", "recollection", by="reviewer_2")
        hist = self.row("u1")["status_history"]
        self.assertEqual([(h["status_before"], h["status_after"]) for h in hist],
                         [("redacted", "in_window"), ("in_window", "redacted"), ("redacted", "out_of_window")])
        self.assertEqual(hist[0]["by"], "reviewer_1")
        self.assertTrue(hist[1]["by"].startswith("loader|run="), hist[1]["by"])
        self.assertIn("UNATTRIBUTED", hist[2]["by"])
        self.assertEqual([h["by"] for h in self.row("u2")["grade_history"]], ["reviewer_2"])

    # ---- the generic consistency check ---------------------------------------------------------------

    def test_a_confirmed_fact_whose_cited_unit_became_unusable_is_reported(self):
        """A promoted row whose own gate no longer holds over the evidence it rests on."""
        self.source()
        self.load()
        writer = write.Writer(self.conn, self.inst, {"agent_id": "tester", "role": "reader", "lens": None})
        writer.propose("f_ok", "a synthetic claim")
        writer.confirm("f_ok", ["u2"])
        before = consistency.report(self.conn, self.inst)
        self.assertGreaterEqual(before["rows_at_gated_value"], 1, "nothing promoted: the check would prove nothing")
        self.assertEqual(before["contradictions"], 0)
        loader.correct_unit(self.conn, "u2", "status", "redacted", by="reviewer_1")
        after = consistency.report(self.conn, self.inst)
        rule = next(r for r in after["rules"] if r["rule"] == "confirmed_needs_source_grade")
        self.assertEqual(rule["no_longer_holds"], ["fact:f_ok"])
        self.assertEqual(after["contradictions"], 1)


if __name__ == "__main__":
    unittest.main()
