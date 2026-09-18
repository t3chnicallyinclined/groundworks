# -*- coding: utf-8 -*-
"""Label agreement, both ways: identical files score perfect; planted disagreements are counted,
listed and kappa-corrected; mismatched coverage is refused.

    python -m unittest discover -s tests -p "test_agreement.py" -v
"""
import contextlib
import io
import json
import pathlib
import shutil
import tempfile
import unittest

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks.pipeline import agreement  # noqa: E402

BASE = [
    {"id": "u1", "tier": 3, "dir": "for_goal", "issues": ["saturation"], "targets": ["t1"], "role": "error"},
    {"id": "u2", "tier": 2, "dir": "mixed", "issues": ["deploy"], "targets": ["t2"], "role": "change"},
    {"id": "u3", "tier": 0, "dir": None, "issues": [], "targets": [], "role": "note"},
    {"id": "u4", "tier": 1, "dir": "neutral", "issues": [], "targets": [], "role": "warning"},
    {"id": "u5", "tier": 2, "dir": "against_goal", "issues": ["monitoring", "dependency"], "targets": ["t3"], "role": "warning"},
]


class AgreementTests(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, labels):
        p = self.dir / name
        p.write_text("".join(json.dumps(l) + "\n" for l in labels), encoding="utf-8")
        return p

    def test_identical_files_agree_perfectly(self):
        r = agreement.compare(self.write("a.jsonl", BASE), self.write("b.jsonl", BASE))
        self.assertEqual(r["fields"]["tier"]["exact"], 1.0)
        self.assertEqual(r["fields"]["tier"]["kappa"], 1.0)
        self.assertEqual(r["fields"]["issues"]["mean_jaccard"], 1.0)
        self.assertEqual(r["disagreements"], [])
        self.assertEqual(r["relevance_split"]["count"], 0)

    def test_planted_disagreements_are_counted_listed_and_flagged(self):
        other = json.loads(json.dumps(BASE))
        other[0]["tier"] = 1                       # relevance split: 3 vs 1
        other[1]["dir"] = "for_goal"               # direction disagreement
        other[4]["issues"] = ["monitoring"]        # partial set overlap
        r = agreement.compare(self.write("a.jsonl", BASE), self.write("b.jsonl", other))
        self.assertEqual(r["fields"]["tier"]["exact"], 0.8)
        self.assertEqual(r["fields"]["tier"]["within_one"], 0.8)
        self.assertEqual(r["fields"]["dir"]["exact"], 0.8)
        self.assertAlmostEqual(r["fields"]["issues"]["mean_jaccard"], (4 + 0.5) / 5)
        self.assertEqual(r["relevance_split"]["count"], 1)
        self.assertIn("u1", r["adjudicate"])
        fields = {(d["unit"], d["field"]) for d in r["disagreements"]}
        self.assertEqual(fields, {("u1", "tier"), ("u2", "dir"), ("u5", "issues")})
        self.assertLess(r["fields"]["tier"]["kappa"], 1.0)

    def test_every_unit_with_a_disagreement_is_adjudicated_once_most_consequential_first(self):
        """Reported from another instance: the list was the relevance splits plus the first 20 other units, and
        tier splits that did not cross tier 2 were never listed. 90 units, 51 needing a human, printed 27."""
        a, b = [], []
        for i in range(30):                      # 30 units disagreeing only on direction: more than the old cap
            a.append({"id": "d%02d" % i, "tier": 1, "dir": "neutral", "issues": [], "targets": [], "role": "note"})
            b.append(dict(a[-1], dir="mixed"))
        a.append({"id": "t3v2", "tier": 3, "dir": "for_goal", "issues": ["x"], "targets": [], "role": "note"})
        b.append(dict(a[-1], tier=2))            # load-bearing or merely relevant: never listed before
        a.append({"id": "t0v1", "tier": 0, "dir": None, "issues": [], "targets": [], "role": "note"})
        b.append(dict(a[-1], tier=1))
        a.append({"id": "split", "tier": 3, "dir": "for_goal", "issues": ["x"], "targets": [], "role": "note"})
        b.append(dict(a[-1], tier=0, dir="neutral"))   # a relevance split that also disagrees on direction
        a.append({"id": "flip", "tier": 2, "dir": "for_goal", "issues": ["x"], "targets": [], "role": "note"})
        b.append(dict(a[-1], dir="against_goal"))   # both relevant, opposite readings: ahead of a 0-vs-1 split
        a.append({"id": "same", "tier": 2, "dir": "for_goal", "issues": ["x"], "targets": [], "role": "note"})
        b.append(dict(a[-1]))
        r = agreement.compare(self.write("a.jsonl", a), self.write("b.jsonl", b))
        self.assertEqual(r["units_with_any_disagreement"], 34)
        self.assertEqual(len(r["adjudicate"]), 34, "every unit with a disagreement is listed")
        self.assertEqual(len(set(r["adjudicate"])), len(r["adjudicate"]), "no unit twice")
        self.assertEqual(r["adjudicate"][:4], ["split", "t3v2", "flip", "t0v1"])
        self.assertNotIn("same", r["adjudicate"])
        self.assertEqual(r["adjudicate_groups"], {"relevance_split": 1, "within_relevant_tiers": 1,
                                                  "direction_on_relevant": 1, "other_tier": 1, "other_fields": 30})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            agreement.main([str(self.dir / "a.jsonl"), str(self.dir / "b.jsonl")])
        self.assertIn("34 unit(s) to adjudicate", out.getvalue())

    def test_kappa_is_chance_corrected(self):
        # One value across the whole sample is NOT perfect agreement: chance agreement is already
        # 1.0, so kappa is undefined. See test_kappa_is_undefined_on_a_degenerate_sample_not_perfect.
        self.assertIsNone(agreement.kappa([("a", "a")] * 10))
        # both labellers say "a" 50% of the time independently: observed 0.5, expected 0.5 -> kappa 0
        pairs = [("a", "a"), ("a", "b"), ("b", "a"), ("b", "b")] * 5
        self.assertEqual(agreement.kappa(pairs), 0.0)
        self.assertIsNone(agreement.kappa([]))

    def test_different_coverage_is_refused(self):
        with self.assertRaises(agreement.AgreementError):
            agreement.compare(self.write("a.jsonl", BASE), self.write("b.jsonl", BASE[:4]))

    def test_kappa_is_undefined_on_a_degenerate_sample_not_perfect(self):
        """20 units all on one tier measured nothing; kappa 1.0 said it measured it perfectly."""
        self.assertIsNone(agreement.kappa([(0, 0)] * 20))
        self.assertIn("only one value", agreement.kappa_undefined([(0, 0)] * 20))
        self.assertIsNone(agreement.kappa([(0, 0)]))            # and at n = 1
        self.assertEqual(agreement.kappa_undefined([("a", "a"), ("a", "b"), ("b", "a"), ("b", "b")]), "")

        flat = [{"id": "u%d" % i, "tier": 0, "dir": None, "issues": [], "targets": [], "role": "note"}
                for i in range(20)]
        r = agreement.compare(self.write("a.jsonl", flat), self.write("b.jsonl", flat))
        self.assertEqual(r["fields"]["tier"]["exact"], 1.0)     # the flattering number
        self.assertIsNone(r["fields"]["tier"]["kappa"])         # the one that disagrees with it
        self.assertEqual(r["fields"]["tier"]["distinct_values"], 1)
        self.assertIn("tier", r["kappa_undefined"])
        self.assertIn("dir", r["kappa_undefined"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(agreement.main([str(self.dir / "a.jsonl"), str(self.dir / "b.jsonl")]), 0)
        self.assertIn("KAPPA NOT MEASURED for tier", out.getvalue())
        # a sample with real variation still reports a number, and the legitimate flow is unchanged
        mixed = [dict(l, tier=i % 3) for i, l in enumerate(flat)]
        r2 = agreement.compare(self.write("c.jsonl", mixed), self.write("d.jsonl", mixed))
        self.assertEqual(r2["fields"]["tier"]["kappa"], 1.0)
        self.assertEqual(r2["fields"]["tier"]["kappa_undefined"], "")
        self.assertNotIn("tier", r2["kappa_undefined"])


if __name__ == "__main__":
    unittest.main()
