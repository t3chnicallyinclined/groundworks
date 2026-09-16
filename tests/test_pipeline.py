# -*- coding: utf-8 -*-
"""Tests for intake, chunker and validator, both ways, on a synthetic incident corpus.

    python -m unittest discover -s engine/pipeline -v
"""
import contextlib
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config  # noqa: E402
from xscholar.pipeline import chunker  # noqa: E402
from xscholar.pipeline import intake  # noqa: E402
from xscholar.pipeline import validator  # noqa: E402

DEMO = HERE.parent / "instances" / "demo-incident" / "instance.yaml"


def log(i, text, status="in_window", grade="original_log", **extra):
    unit = {"id": "log_%03d" % i, "type": "log_line",
            "locator": {"host": "api-1", "file": "app.log", "line": i, "ts": "2026-03-04T11:%02d:00Z" % (i % 60)},
            "text": text, "status": status, "grade": grade}
    unit.update(extra)
    return unit


class Adapter:
    """The demo instance's originals are required (instance.yaml `originals:`), so this fixture supplies
    them: one synthetic log file per `locator.file`, whose line N is the text of the unit at line N. A unit
    that names its own `document` keeps it."""

    def __init__(self, units):
        self._units = units

    def documents(self):
        files = {}
        for unit in self._units:
            if "exclude" in unit or not isinstance(unit.get("locator"), dict):
                continue
            file_lines = files.setdefault(str(unit["locator"].get("file") or "fixture.log"), {})
            file_lines.setdefault(unit["locator"].get("line") or 1, str(unit.get("text")))
        for name, by_line in files.items():
            last = max([n for n in by_line if isinstance(n, int)] or [0])
            body = "".join("%s\n" % by_line.get(n, "") for n in range(1, last + 1))
            yield {"key": name, "bytes": body.encode("utf-8"), "media_type": "text/plain", "capture": "raw"}

    def units(self):
        for unit in self._units:
            if "exclude" in unit or "document" in unit or not isinstance(unit.get("locator"), dict):
                yield unit
            else:
                yield dict(unit, document=str(unit["locator"].get("file") or "fixture.log"))


CORPUS = [log(1, "pool exhausted: waited 30000ms for connection"),
          log(2, "deploy v2.14.0 started"),
          log(3, "health check ok"),
          log(4, "retrying upstream call (attempt 3)", status="out_of_window")]


class Base(unittest.TestCase):
    def setUp(self):
        self.inst = config.load(DEMO)
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)


class IntakeTests(Base):
    def test_valid_corpus_is_written_with_hashes_and_counts(self):
        counts = intake.write_units(Adapter(CORPUS + [{"id": "dup", "type": "log_line", "exclude": "duplicate of log_001"}]),
                                    self.dir, self.inst)
        self.assertEqual(counts["kept"], 4)
        self.assertEqual(counts["excluded"], 1)
        self.assertEqual(counts["by_status"], {"in_window": 3, "out_of_window": 1})
        rows = intake.read_units(self.dir / "units.jsonl")
        self.assertTrue(all(r["hash"].startswith("sha256:") for r in rows))
        self.assertEqual(rows[0]["hash"], intake.unit_hash(rows[0]))
        self.assertEqual((self.dir / "excluded.jsonl").read_text(encoding="utf-8").count("\n"), 1)

    def test_each_contract_breach_is_named(self):
        cases = [
            (log(1, "x", type="nope"), "type"),
            ({**log(1, "x"), "locator": {"host": "h"}}, "locator missing"),
            (log(1, "   "), "text"),
            (log(1, "x", status="elsewhere"), "status"),
            (log(1, "x", grade="hearsay"), "grade"),
            ({**log(1, "x"), "id": "bad id"}, "id"),
        ]
        for unit, phrase in cases:
            with self.assertRaises(intake.IntakeError, msg=phrase) as err:
                intake.write_units(Adapter([unit]), self.dir, self.inst)
            self.assertIn(phrase, str(err.exception))
        with self.assertRaises(intake.IntakeError) as err:
            intake.write_units(Adapter([log(1, "a"), log(1, "b")]), self.dir, self.inst)
        self.assertIn("duplicate", str(err.exception))
        with self.assertRaises(intake.IntakeError) as err:
            intake.write_units(Adapter([]), self.dir, self.inst)
        self.assertIn("0 units", str(err.exception))


class ChunkerTests(Base):
    def test_every_unit_lands_in_exactly_one_slice_deterministically(self):
        many = [log(i, "line %d" % i) for i in range(1, 451)]  # chunk_size for log_line is 200
        intake.write_units(Adapter(many), self.dir, self.inst)
        chunks = chunker.chunk(self.dir / "units.jsonl", self.dir, self.inst)
        self.assertEqual([c["count"] for c in chunks], [200, 200, 50])
        placed = [u for c in chunks for u in c["units"]]
        self.assertEqual(len(placed), 450)
        self.assertEqual(len(set(placed)), 450)
        again = chunker.chunk(self.dir / "units.jsonl", self.dir, self.inst)
        self.assertEqual(chunks, again)
        first = intake.read_units(self.dir / "slices" / "log_line_0000.jsonl")
        self.assertEqual(first[0]["id"], "log_001")


class ChunkByTests(Base):
    def test_units_sharing_a_locator_field_stay_together(self):
        rows = [dict(log(i, "l%d" % i), locator={"host": "h", "file": "f", "line": i, "ts": "d%d" % (i // 7)})
                for i in range(1, 31)]  # 30 units; i // 7 gives days of 6, 7, 7, 7, 3
        pieces = list(chunker._pieces(rows, 10, "ts"))
        self.assertEqual([len(p) for p in pieces], [6, 7, 7, 10])      # never splits a day; fills to 10 max
        days = [{r["locator"]["ts"] for r in p} for p in pieces]
        self.assertTrue(all(d.isdisjoint(e) for i, d in enumerate(days) for e in days[i + 1:]))
        self.assertEqual(sum(len(p) for p in pieces), 30)

    def test_units_sharing_a_value_stay_together_when_the_input_interleaves_them(self):
        days = ["d1", "d2", "d1", "d3", "d2", "d1"]
        rows = [dict(log(i + 1, "l%d" % i), locator={"host": "h", "file": "f", "line": i + 1, "ts": d})
                for i, d in enumerate(days)]
        pieces = list(chunker._pieces(rows, 3, "ts"))
        self.assertEqual([[r["locator"]["ts"] for r in p] for p in pieces], [["d1", "d1", "d1"], ["d2", "d2", "d3"]])
        self.assertEqual([r["locator"]["line"] for r in pieces[0]], [1, 3, 6])     # input order kept within a day

    def test_a_day_larger_than_the_size_is_split_not_dropped(self):
        rows = [dict(log(i, "x"), locator={"host": "h", "file": "f", "line": i, "ts": "same"}) for i in range(1, 24)]
        pieces = list(chunker._pieces(rows, 10, "ts"))
        self.assertEqual([len(p) for p in pieces], [10, 10, 3])

    def test_without_chunk_by_behaviour_is_unchanged(self):
        rows = [log(i, "x") for i in range(1, 26)]
        self.assertEqual([len(p) for p in chunker._pieces(rows, 10)], [10, 10, 5])


class ValidatorTests(Base):
    def setUp(self):
        super().setUp()
        intake.write_units(Adapter(CORPUS), self.dir, self.inst)
        chunker.chunk(self.dir / "units.jsonl", self.dir, self.inst)
        self.slice = self.dir / "slices" / "log_line_0000.jsonl"
        self.good = [
            {"id": "log_001", "tier": 3, "dir": "for_goal", "issues": ["saturation"], "targets": ["rc_pool_exhaustion"],
             "role": "error", "reason": "Direct evidence of pool exhaustion.", "quote": "pool exhausted"},
            {"id": "log_002", "tier": 2, "dir": "mixed", "issues": ["deploy"], "targets": [], "role": "change",
             "reason": "A deploy in the window.", "quote": "deploy   v2.14.0"},
            {"id": "log_003", "tier": 0, "dir": None, "issues": [], "targets": [], "role": "note"},
            {"id": "log_004", "tier": 1, "dir": "neutral", "issues": [], "targets": [], "role": "warning"},
        ]

    def write(self, labels):
        path = self.dir / "labels.jsonl"
        path.write_text("".join(json.dumps(l) + "\n" for l in labels), encoding="utf-8")
        return path

    def run_check(self, labels):
        return validator.validate(self.slice, self.write(labels), self.inst)

    def test_clean_labels_pass(self):
        report = self.run_check(self.good)
        self.assertTrue(report["ok"], report["errors"])

    def test_planted_misquote_fails(self):
        bad = json.loads(json.dumps(self.good))
        bad[0]["quote"] = "pool was fine"
        report = self.run_check(bad)
        self.assertFalse(report["ok"])
        self.assertEqual(report["errors"][0]["field"], "quote")

    def test_missing_extra_and_duplicate_labels_fail(self):
        self.assertIn("no label", self.run_check(self.good[:3])["errors"][0]["error"])
        extra = self.good + [{**self.good[2], "id": "log_999"}]
        self.assertIn("not in this slice", self.run_check(extra)["errors"][0]["error"])
        dup = self.good + [self.good[2]]
        self.assertIn("more than once", self.run_check(dup)["errors"][0]["error"])

    def test_vocabulary_and_tier_rules_fail(self):
        bad = json.loads(json.dumps(self.good))
        bad[0]["issues"] = ["vibes"]
        self.assertEqual(self.run_check(bad)["errors"][0]["field"], "issues")
        bad = json.loads(json.dumps(self.good))
        bad[1]["quote"] = ""
        self.assertEqual(self.run_check(bad)["errors"][0]["field"], "quote")
        bad = json.loads(json.dumps(self.good))
        bad[1]["reason"] = " "
        self.assertEqual(self.run_check(bad)["errors"][0]["field"], "reason")
        bad = json.loads(json.dumps(self.good))
        bad[0]["tier"] = 7
        self.assertEqual(self.run_check(bad)["errors"][0]["field"], "tier")

    def test_an_empty_slice_is_a_failure_not_a_pass(self):
        """Every rule is a loop over the slice, so 0 units satisfies all of them without running any."""
        empty_slice = self.dir / "empty.jsonl"
        empty_slice.write_text("", encoding="utf-8")
        labels_path = self.write([])
        report = validator.validate(empty_slice, labels_path, self.inst)
        self.assertFalse(report["ok"], "PASS: 0 units, 0 labels, 0 errors is a green run over nothing")
        self.assertEqual((report["units"], report["labels"], report["checks_run"]), (0, 0, 0))
        self.assertEqual([e["field"] for e in report["errors"]], ["slice"])
        self.assertIn("0 units", report["errors"][0]["error"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = validator.main([str(empty_slice), str(labels_path), str(DEMO)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL: 0 units", out.getvalue())
        # the populated slice is unaffected: the real flow still passes
        good = self.run_check(self.good)
        self.assertTrue(good["ok"], good["errors"])
        self.assertEqual(good["checks_run"], good["units"])


if __name__ == "__main__":
    unittest.main()
