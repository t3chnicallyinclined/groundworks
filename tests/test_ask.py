# -*- coding: utf-8 -*-
"""Tests for groundworks/ask: the packet, the checker and the renderer, with no database.

Each guard here was first shown red by hand against the named mutation in tests/harness/mutations.json
(M364-M369). The corpus is synthetic, in the demo instance's vocabulary (log_line units, statuses
in_window / out_of_window), so the demo instance.yaml supplies the status axis and the quote rules.

    python -m unittest tests.test_ask -v
"""
import json
import pathlib
import tempfile
import unittest

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks import config  # noqa: E402
from groundworks.ask import check as checkmod, packet, render as rendermod  # noqa: E402
from groundworks.ask.__main__ import main as ask_main  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"

TEXTS = [
    ("u01", "in_window", "checkout latency rose after the cache flush at eleven twenty"),
    ("u02", "in_window", "the cache flush was triggered by the deploy of build 4412"),
    ("u03", "in_window", "latency returned to baseline once the cache warmed"),
    ("u04", "out_of_window", "a cache flush the week before caused no latency change"),
    ("u05", "in_window", "payments service reported no errors during the window"),
    ("u06", "in_window", "health check ok"),
    ("u07", "in_window", "the deploy of build 4412 also rotated the cache keys"),
    ("u08", "in_window", "operator noted cache hit ratio fell to twelve percent"),
    ("u09", "in_window", "search service unaffected"),
    ("u10", "in_window", "cache warmed by eleven forty and latency recovered"),
]


def write_units(path, rows=TEXTS):
    with pathlib.Path(path).open("w", encoding="utf-8") as fh:
        for i, (uid, status, text) in enumerate(rows, 1):
            fh.write(json.dumps({"id": uid, "type": "log_line", "locator": {"host": "api-1", "file": "app.log", "line": i, "ts": "t"},
                                 "text": text, "status": status, "grade": "original_log", "meta": {}, "document": None,
                                 "hash": "sha256:x"}) + "\n")


def write_reads(out_dir, info, skip=()):
    with (pathlib.Path(out_dir) / "read.jsonl").open("w", encoding="utf-8") as fh:
        for sl in info["slices"]:
            for uid in sl["units"]:
                if uid not in skip:
                    fh.write(json.dumps({"unit": uid, "slice": sl["id"]}) + "\n")


def write_answer(out_dir, quotes, answer="Latency rose after the cache flush and recovered once the cache warmed.", **extra):
    body = {"answer": answer, "quotes": quotes}
    body.update(extra)
    (pathlib.Path(out_dir) / "answer.json").write_text(json.dumps(body), encoding="utf-8")


class AskCase(unittest.TestCase):
    QUESTION = "why did checkout latency rise after the cache flush"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.units = self.dir / "units.jsonl"
        write_units(self.units)
        self.inst = config.load(DEMO)
        self.out = self.dir / "packet"

    def tearDown(self):
        self.tmp.cleanup()

    def open(self, question=None, k=6, slice_size=4):
        return packet.open_packet(question or self.QUESTION, self.units, self.out, self.inst, k=k, slice_size=slice_size)

    def good_quotes(self):
        return [{"unit": "u01", "quote": "latency rose after the cache flush", "use": "support"},
                {"unit": "u03", "quote": "returned to baseline once the cache warmed", "use": "support"}]

    # ---- the six guards named in the ticket

    def test_planted_misquote_refused_by_unit_id(self):
        info = self.open()
        self.assertIn("u01", [u for sl in info["slices"] for u in sl["units"]])
        write_reads(self.out, info)
        write_answer(self.out, self.good_quotes())
        self.assertEqual(checkmod.check_packet(self.out, self.inst)["verdict"], "PASS")
        bad = self.good_quotes()
        bad[0]["quote"] = "latency fell after the cache flush"      # one word changed
        write_answer(self.out, bad)
        report = checkmod.check_packet(self.out, self.inst)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual([e["unit"] for e in report["errors"]], ["u01"])
        self.assertIn("not found word for word in unit u01", report["errors"][0]["error"])

    def test_cite_to_unread_unit_refused(self):
        info = self.open()
        write_reads(self.out, info, skip=("u03",))
        write_answer(self.out, self.good_quotes())
        report = checkmod.check_packet(self.out, self.inst)
        self.assertEqual(report["verdict"], "FAIL")
        errs = [e["error"] for e in report["errors"]]
        self.assertTrue(any("cites unit u03 that was not read" in e for e in errs), errs)
        self.assertTrue(any("unit u03 was not read" in e and e.startswith("slice ") for e in errs), errs)

    def test_empty_read_is_not_executed(self):
        info = self.open()
        self.assertGreater(info["retrieved"], 0)
        write_answer(self.out, self.good_quotes())
        report = checkmod.check_packet(self.out, self.inst)           # no read.jsonl at all
        self.assertEqual(report["verdict"], "NOT_EXECUTED")
        (self.out / "read.jsonl").write_text("", encoding="utf-8")    # present but empty
        report = checkmod.check_packet(self.out, self.inst)
        self.assertEqual(report["verdict"], "NOT_EXECUTED")
        self.assertIn("nothing was read", report["reason"])
        self.assertEqual(checkmod.EXIT[report["verdict"]], 3)
        self.assertIsNone(rendermod.render(self.out, self.inst))
        self.assertFalse((self.out / "answer.md").exists())

    def test_zero_hits_reported_not_silent(self):
        info = self.open(question="zebra giraffe okapi")
        self.assertEqual(info["status"], "NO_HITS")
        self.assertEqual(info["retrieved"], 0)
        self.assertEqual(info["slices"], [])
        write_answer(self.out, [], answer="", not_answerable=True)
        report = checkmod.check_packet(self.out, self.inst)
        self.assertEqual(report["verdict"], "PASS")
        text = rendermod.render(self.out, self.inst)
        self.assertIn("retrieved 0", text)
        self.assertIn("0 hits", text)

    def test_two_slice_coverage_enforced(self):
        info = self.open(k=6, slice_size=4)
        self.assertEqual(len(info["slices"]), 2)
        second = info["slices"][1]
        write_reads(self.out, info, skip=(second["units"][-1],))
        write_answer(self.out, self.good_quotes())
        report = checkmod.check_packet(self.out, self.inst)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual([e for e in report["errors"] if e.get("slice")],
                         [{"unit": second["units"][-1], "slice": second["id"],
                           "error": "slice %s: unit %s was not read" % (second["id"], second["units"][-1])}])

    def test_header_counts_disagree_with_green(self):
        info = self.open(k=6, slice_size=4)
        write_reads(self.out, info)
        write_answer(self.out, self.good_quotes())
        report = checkmod.check_packet(self.out, self.inst)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["corpus_total"], 10)
        self.assertEqual(report["read"], info["retrieved"])
        self.assertGreater(report["not_read"], 0)
        text = rendermod.render(self.out, self.inst)
        self.assertIn("corpus 10 | retrieved %d | read %d | not read %d" % (info["retrieved"], info["retrieved"], 10 - info["retrieved"]), text)
        self.assertIn("check PASS", text)
        self.assertIn("were never read for this question", text)

    # ---- the status axis and the packet's own integrity

    def test_support_from_unusable_unit_refused_but_a_note_is_allowed(self):
        info = self.open(k=10, slice_size=10)
        self.assertIn("u04", info["slices"][0]["units"])
        write_reads(self.out, info)
        quotes = self.good_quotes() + [{"unit": "u04", "quote": "caused no latency change", "use": "support"}]
        write_answer(self.out, quotes)
        report = checkmod.check_packet(self.out, self.inst)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("supports from unit u04 whose status 'out_of_window' is not usable", report["errors"][0]["error"])
        quotes[-1]["use"] = "note"
        write_answer(self.out, quotes)
        report = checkmod.check_packet(self.out, self.inst)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["quote_rows"][-1]["usable"], False)
        # without an instance the axis is not checked, and the report says so rather than passing quietly
        report = checkmod.check_packet(self.out, None)
        self.assertEqual(report["status_axis"], "not checked (no instance given)")

    def test_a_quote_from_outside_the_packet_and_a_short_quote_are_refused(self):
        info = self.open()
        write_reads(self.out, info)
        write_answer(self.out, [{"unit": "u09", "quote": "search service unaffected"}, {"unit": "u01", "quote": "latency rose"}])
        report = checkmod.check_packet(self.out, self.inst)
        errs = [e["error"] for e in report["errors"]]
        self.assertTrue(any("cites unit u09 outside this packet" in e for e in errs), errs)
        self.assertTrue(any("fewer than 3 words" in e for e in errs), errs)

    def test_a_packet_is_never_overwritten_and_a_tampered_slice_is_refused(self):
        self.open()
        with self.assertRaises(packet.PacketError):
            self.open()
        info = json.loads((self.out / "question.json").read_text(encoding="utf-8"))
        spath = self.out / info["slices"][0]["path"]
        rows = spath.read_text(encoding="utf-8").splitlines()
        spath.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")       # one unit dropped from the slice file
        report = checkmod.check_packet(self.out, self.inst)
        self.assertEqual(report["verdict"], "NOT_EXECUTED")
        self.assertIn("does not match question.json", report["reason"])

    def test_a_scope_ranks_only_units_inside_it_and_counts_what_it_excluded(self):
        rows = list(TEXTS) + [("d01", "in_window", "the cache flush decision: latency rose, and we accept the deploy caused it")]
        write_units(self.units, rows)
        units_types = {"d01": "decision"}
        # give d01 its own type by rewriting the file (write_units fixes type=log_line)
        lines = self.units.read_text(encoding="utf-8").splitlines()
        rewritten = []
        for line in lines:
            row = json.loads(line)
            row["type"] = units_types.get(row["id"], row["type"])
            rewritten.append(json.dumps(row))
        self.units.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        info = packet.open_packet(self.QUESTION, self.units, self.out, self.inst, k=10, slice_size=10, types=["decision"])
        self.assertEqual(info["scope"], {"types": ["decision"], "where": {}, "in_scope": 1, "out_of_scope": 10})
        self.assertEqual([u for sl in info["slices"] for u in sl["units"]], ["d01"])
        self.assertEqual(info["corpus_total"], 11)                    # the whole corpus, not the scope
        write_reads(self.out, info)
        write_answer(self.out, [{"unit": "d01", "quote": "we accept the deploy caused it"}])
        report = checkmod.check_packet(self.out, self.inst)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["not_read"], 10)
        self.assertIn("10 excluded by the scope before ranking", rendermod.render(self.out, self.inst))
        # a where condition on a locator field
        out2 = self.dir / "packet2"
        info = packet.open_packet(self.QUESTION, self.units, out2, self.inst, k=10, slice_size=10, where={"line": "1"})
        self.assertEqual(info["scope"]["in_scope"], 1)
        self.assertEqual([u for sl in info["slices"] for u in sl["units"]], ["u01"])
        # a scope nothing satisfies is NO_HITS, reported, not an error
        out3 = self.dir / "packet3"
        info = packet.open_packet(self.QUESTION, self.units, out3, self.inst, k=10, types=["absent"])
        self.assertEqual((info["status"], info["scope"]["in_scope"], info["retrieved"]), ("NO_HITS", 0, 0))

    def test_the_command_exit_codes(self):
        rc = ask_main(["open", self.QUESTION, "--units", str(self.units), "--out", str(self.out), "--instance", str(DEMO), "--k", "6", "--slice", "4"])
        self.assertEqual(rc, 0)
        self.assertEqual(ask_main(["check", str(self.out), "--instance", str(DEMO)]), 3)
        info = json.loads((self.out / "question.json").read_text(encoding="utf-8"))
        write_reads(self.out, info)
        write_answer(self.out, self.good_quotes())
        self.assertEqual(ask_main(["check", str(self.out), "--instance", str(DEMO)]), 0)
        self.assertEqual(ask_main(["render", str(self.out), "--instance", str(DEMO)]), 0)
        self.assertTrue((self.out / "answer.md").exists())
        self.assertEqual(ask_main(["open", self.QUESTION, "--units", str(self.units), "--out", str(self.out)]), 2)


if __name__ == "__main__":
    unittest.main()
