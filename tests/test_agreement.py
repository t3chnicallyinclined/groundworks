# -*- coding: utf-8 -*-
"""Label agreement, both ways: identical files score perfect; planted disagreements are counted,
listed and kappa-corrected; mismatched coverage is refused.

    python -m unittest discover -s tests -p "test_agreement.py" -v
"""
import json
import pathlib
import shutil
import tempfile
import unittest

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.pipeline import agreement  # noqa: E402

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

    def test_kappa_is_chance_corrected(self):
        self.assertEqual(agreement.kappa([("a", "a")] * 10), 1.0)
        # both labellers say "a" 50% of the time independently: observed 0.5, expected 0.5 -> kappa 0
        pairs = [("a", "a"), ("a", "b"), ("b", "a"), ("b", "b")] * 5
        self.assertEqual(agreement.kappa(pairs), 0.0)
        self.assertIsNone(agreement.kappa([]))

    def test_different_coverage_is_refused(self):
        with self.assertRaises(agreement.AgreementError):
            agreement.compare(self.write("a.jsonl", BASE), self.write("b.jsonl", BASE[:4]))


if __name__ == "__main__":
    unittest.main()
