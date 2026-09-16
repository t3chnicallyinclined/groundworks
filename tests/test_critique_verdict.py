# -*- coding: utf-8 -*-
"""Re-stating a head replaces its verdict, and the change is on the record.

The MCP adapter has always documented this: "Re-adding the same head from the same agent against the
same target replaces its verdict" (xscholar/mcp/adapter.py). Until 2026-09-16 the code did not do it,
and no test covered it. Measured on the shipped code:

    1st  critique:c1__lens_skeptic__476d2269  fatal
    2nd  critique:c1__lens_skeptic__49a218dc  survivable   -> TWO rows, the fatal one still blocking
    new session, same head -> DbError: Database record `critique:c1__lens_skeptic__476d2269` already exists

The id came from a Keyer that increments on every repeat, so a re-statement made a second critique
inside one session and collided on the id in a later one.

Why it mattered more than a duplicate row: the readiness gate forbids `verdict = 'fatal'` with no
`answered` escape, while a `damaging` head clears once answered. So a fatal verdict was permanent and
unappealable -- the documented cure, "downgraded on the record and then answered", had no write path
at all. A session running a real instance reported 3 of 3 conclusions blocked by fatal heads and asked
whether its method was miscalibrated; this defect alone is sufficient to produce that.
"""
import pathlib
import subprocess
import sys
import time
import unittest
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from xscholar import config  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar import gates  # noqa: E402
from xscholar import surreal_bin  # noqa: E402
from xscholar import temporal  # noqa: E402
from xscholar.pipeline import write  # noqa: E402

PORT = 8029
ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
SURREAL = surreal_bin.find()


class VerdictTests(unittest.TestCase):

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
        self.conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="verdict",
                             db=self.id().split(".")[-1][:60])
        self.conn.script(gates.schema_surql(self.inst))
        self.conn.script(gates.gate_surql(self.inst))
        self.conn.script(temporal.temporal_surql(self.inst))
        self.conn.sql("CREATE conclusion:c1 SET statement='s', ready=false")

    def writer(self, agent="a1", lens="lens_skeptic"):
        """A fresh Writer is a fresh session: its Keyer starts empty, as it would next launch."""
        return write.Writer(self.conn, self.inst, {"agent_id": agent, "role": "seat", "lens": lens})

    def critiques(self):
        return self.conn.sql("SELECT record::id(id) AS id, verdict, answered FROM critique ORDER BY id")[0]

    def test_re_stating_a_head_replaces_its_verdict_instead_of_adding_a_second(self):
        w = self.writer()
        first = w.critique("c1", "the head", "fatal", text="t")
        second = w.critique("c1", "the head", "survivable", text="t")
        self.assertEqual(first, second, "the same head under the same lens must be the same row")
        rows = self.critiques()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["verdict"], "survivable")
        self.assertEqual(self.conn.sql("SELECT id FROM critique WHERE verdict = 'fatal'")[0], [],
                         "the superseded fatal verdict is still blocking readiness")

    def test_a_later_session_can_re_state_the_head_at_all(self):
        """A fresh Writer used to collide on the id and raise, so the cure was unreachable twice over."""
        first = self.writer().critique("c1", "the head", "fatal", text="t")
        again = self.writer().critique("c1", "the head", "damaging", text="t")
        self.assertEqual(first, again)
        self.assertEqual(len(self.critiques()), 1)

    def test_a_downgrade_is_on_the_record_with_its_author_and_time(self):
        w = self.writer()
        cid = w.critique("c1", "the head", "fatal", text="t")
        w.critique("c1", "the head", "survivable", text="t")
        history = self.conn.sql("SELECT verdict_history FROM %s" % cid)[0][0]["verdict_history"]
        self.assertEqual(len(history), 1, "a verdict change must never be a silent edit: %s" % history)
        entry = history[0]
        self.assertEqual((entry["verdict_before"], entry["verdict_after"]), ("fatal", "survivable"))
        self.assertIn("a1", entry["by"])
        self.assertTrue(entry["at"])

    def test_re_stating_the_same_verdict_appends_nothing(self):
        w = self.writer()
        cid = w.critique("c1", "the head", "fatal", text="t")
        w.critique("c1", "the head", "fatal", text="t")
        self.assertEqual(self.conn.sql("SELECT verdict_history FROM %s" % cid)[0][0]["verdict_history"] or [], [],
                         "a re-write that changes nothing must not manufacture history")

    def test_the_verdict_history_cannot_be_emptied_or_rewritten(self):
        w = self.writer()
        cid = w.critique("c1", "the head", "fatal", text="t")
        w.critique("c1", "the head", "survivable", text="t")
        for attempt in ("UPDATE %s SET verdict_history = []" % cid,
                        "UPDATE %s SET verdict_history = [{at: time::now(), by: 'x', "
                        "verdict_before: 'survivable', verdict_after: 'survivable'}]" % cid):
            ok, _ = self.conn.try_sql(attempt)
            self.assertFalse(ok, "history is append-only: %s" % attempt)

    def test_different_heads_and_different_lenses_stay_separate_rows(self):
        """Replacing by head must not collapse genuinely distinct critiques into one."""
        w = self.writer()
        w.critique("c1", "head one", "damaging", text="t")
        w.critique("c1", "head two", "damaging", text="t")
        self.writer(lens="lens_other").critique("c1", "head one", "damaging", text="t")
        self.assertEqual(len(self.critiques()), 3, self.critiques())

    def test_answering_survives_a_re_statement(self):
        """A head answered, then re-stated at the same verdict, must not silently become unanswered."""
        w = self.writer()
        cid = w.critique("c1", "the head", "damaging", text="t")
        w.answer(cid, "the reply")
        self.assertTrue(self.critiques()[0]["answered"])
        w.critique("c1", "the head", "damaging", text="t")
        self.assertTrue(self.critiques()[0]["answered"], "re-stating a head silently unanswered it")

    def test_a_downgraded_head_lets_the_conclusion_become_ready(self):
        """The whole point. A fatal head blocks with no `answered` escape; downgrading must release it."""
        w = self.writer()
        self.conn.sql("CREATE fact:f1 SET statement='s', status='proposed'")
        self.conn.sql("RELATE fact:f1->supports:s1->conclusion:c1")
        usable, utype = self.inst.usable_statuses()[0], self.inst.unit_names()[0]
        self.conn.sql("CREATE unit:u1 SET type='%s', text='t', locator={}, status='%s', grade='%s', hash='h1'"
                      % (utype, usable, self.inst.confirming_grades()[1]))
        self.conn.sql("RELATE unit:u1->preserves:p1->conclusion:c1 SET how='alert'")
        cid = w.critique("c1", "the head", "fatal", text="t")

        blocked, _ = self.conn.try_sql("UPDATE conclusion:c1 SET ready = true")
        self.assertFalse(blocked, "a fatal head must block readiness")

        w.critique("c1", "the head", "damaging", text="t")
        w.answer(cid, "the reply")
        released, message = self.conn.try_sql("UPDATE conclusion:c1 SET ready = true")
        self.assertTrue(released, "downgraded on the record and answered, and still blocked: %s" % message)

    # ---- ROADMAP V2-22(f): answering the same critique again ------------------------------------------
    # critique() became an UPSERT on a deterministic id (7733fd5); answer() stayed a CREATE of
    # `answer:ans_<critique>`, so a second answer to the same critique hard-errored with "record already exists":
    # a demo re-run, or revising an answer after the head's verdict is downgraded -- the flow 7733fd5 opened.

    def answers(self, cid):
        return self.conn.sql("SELECT record::id(id) AS id, text, author FROM answer; "
                             "SELECT VALUE record::id(id) FROM answers WHERE out = %s" % cid)

    def test_answering_the_same_critique_again_revises_the_answer_in_the_same_and_a_later_session(self):
        w = self.writer()
        cid = w.critique("c1", "the head", "damaging", text="t")
        first = w.answer(cid, "the first reply")
        second = w.answer(cid, "the revised reply")                                   # same session
        third = self.writer(agent="a2").answer(cid, "a later session's reply")        # a fresh Keyer
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        rows, edges = self.answers(cid)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["text"], "a later session's reply")
        self.assertEqual(len(edges), 1, "a repeat added a second answers edge: %s" % edges)
        self.assertTrue(self.critiques()[0]["answered"])

    def test_an_answer_revision_is_on_the_record_and_its_history_cannot_be_rewritten(self):
        w = self.writer()
        cid = w.critique("c1", "the head", "damaging", text="t")
        aid = w.answer(cid, "the first reply")
        self.writer(agent="a2").answer(cid, "the revised reply")
        w.answer(cid, "the revised reply")          # the same text again: nothing to record
        history = self.conn.sql("SELECT VALUE text_history FROM %s" % aid)[0][0]
        self.assertEqual(len(history), 1, "a revision must never be a silent edit, and a repeat is not one: %s" % history)
        self.assertEqual((history[0]["text_before"], history[0]["text_after"]), ("the first reply", "the revised reply"))
        self.assertIn("a2", history[0]["by"])
        ok, _ = self.conn.try_sql("UPDATE %s SET text_history = []" % aid)
        self.assertFalse(ok, "answer history is append-only")

    def test_a_downgraded_then_revised_answer_releases_the_conclusion(self):
        """The flow end to end: fatal head answered, downgraded to damaging, answer revised, conclusion ready."""
        self.conn.sql("CREATE fact:f1 SET statement='s', status='proposed'")
        self.conn.sql("RELATE fact:f1->supports:s1->conclusion:c1")
        usable, utype = self.inst.usable_statuses()[0], self.inst.unit_names()[0]
        self.conn.sql("CREATE unit:u1 SET type='%s', text='t', locator={}, status='%s', grade='%s', hash='h1'"
                      % (utype, usable, self.inst.confirming_grades()[1]))
        self.conn.sql("RELATE unit:u1->preserves:p1->conclusion:c1 SET how='alert'")
        w = self.writer()
        cid = w.critique("c1", "the head", "fatal", text="t")
        w.answer(cid, "a reply to the fatal head")
        w.critique("c1", "the head", "damaging", text="t")
        w.answer(cid, "the reply, revised for the downgraded head")
        released, message = self.conn.try_sql("UPDATE conclusion:c1 SET ready = true")
        self.assertTrue(released, "downgraded, answer revised, and still blocked: %s" % message)


if __name__ == "__main__":   # last, so running this file directly runs every class in it
    unittest.main()
