# -*- coding: utf-8 -*-
"""The eval harness (evals/) reports intervals, refuses verdicts it has not earned, and keeps every run.

No server, no agents. Proves the rules of docs/TESTING.md s.7 both ways: an underpowered run reports no
verdict, a prereg written after the run is refused, a broken eval (oracle below n/n, null arm away from
chance) is named, a well-powered clear effect IS adopted, and an edited history line breaks the chain.
"""
import copy
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from evals import record, stats  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

PREREG = {"eval_id": "toy", "written_at": "2026-01-01T00:00:00+00:00", "hypothesis": "B catches more than A",
          "arms": ["A", "B"], "baseline_arm": "A", "primary_metric": "recall", "unit_of_analysis": "planted error",
          "smallest_effect": {"p_baseline": 0.10, "p_arm": 0.40}, "min_n_per_arm": 20, "direction": "higher",
          "decision_rule": "B's interval wholly above A's at n >= minimum"}
MATERIALS = {"sha256": "ab" * 32, "utf8_roundtrip": True}


def result(a=(0, 8), b=(2, 8), **changes):
    data = {"eval_id": "toy", "run": {"started_at": "2026-02-01T00:00:00+00:00", "materials": dict(MATERIALS)},
            "arms": {"A": {"k": a[0], "n": a[1]}, "B": {"k": b[0], "n": b[1]}},
            "anchors": {"oracle": {"k": 10, "n": 10}, "null": {"k": 5, "n": 10, "chance": 0.5}}}
    data.update(changes)
    return data


def build(res, prereg=PREREG, **kw):
    return record.build(prereg, "sha", "prereg.json", res, **kw)


class StatsTests(unittest.TestCase):

    def test_wilson_matches_the_published_values_this_repo_quotes(self):
        """TESTING s.7: 0 of 8 is [0.00, 0.32] and 2 of 8 is [0.07, 0.59]."""
        lo, hi = stats.wilson(0, 8)
        self.assertEqual((round(lo, 2), round(hi, 2)), (0.0, 0.32))
        lo, hi = stats.wilson(2, 8)
        self.assertEqual((round(lo, 2), round(hi, 2)), (0.07, 0.59))
        with self.assertRaises(ValueError):
            stats.wilson(0, 0)

    def test_the_two_proportion_sample_size_matches_the_documented_figures(self):
        self.assertEqual(stats.power_two_proportions(0.10, 0.40), 32)
        self.assertTrue(560 <= stats.power_two_proportions(0.20, 0.27) <= 590)

    def test_a_cluster_bootstrap_is_wider_than_pretending_every_catch_is_independent(self):
        clustered = stats.cluster_proportion([(4, 4), (0, 4), (0, 4), (0, 4)], seed=1)
        spread = stats.cluster_proportion([(1, 4), (1, 4), (1, 4), (1, 4)], seed=1)
        self.assertEqual(clustered["k"], spread["k"])
        width = lambda s: s["cluster_interval"][1] - s["cluster_interval"][0]
        self.assertGreater(width(clustered), width(spread))


class RecordRuleTests(unittest.TestCase):

    def test_an_underpowered_run_reports_no_verdict_and_records_no_evidence(self):
        rec = build(result())
        comparison = rec["comparisons"][0]
        self.assertTrue(comparison["underpowered"])
        self.assertEqual(rec["decision"], "no evidence")
        self.assertTrue(comparison["verdict_line"].startswith("NO VERDICT (underpowered: n=8 per arm < 32"),
                        comparison["verdict_line"])
        self.assertIn("0/8 [0.00, 0.32] vs 2/8 [0.07, 0.59]", comparison["verdict_line"])

    def test_a_clear_effect_at_enough_n_is_adopted_and_overlap_is_no_evidence_of_change(self):
        self.assertEqual(build(result(a=(4, 40), b=(30, 40)))["decision"], "adopted")
        rec = build(result(a=(10, 40), b=(14, 40)))
        self.assertEqual(rec["decision"], "no evidence")
        self.assertIn("NO EVIDENCE OF CHANGE", rec["comparisons"][0]["verdict_line"])
        self.assertEqual(build(result(a=(30, 40), b=(4, 40)))["decision"], "not adopted")

    def test_a_prereg_written_after_the_run_is_refused_and_a_retrofit_may_never_adopt(self):
        late = dict(PREREG, written_at="2026-03-01T00:00:00+00:00")
        with self.assertRaises(record.RecordError) as raised:
            build(result(a=(4, 40), b=(30, 40)), prereg=late)
        self.assertIn("not a pre-registration", str(raised.exception))
        rec = build(result(a=(4, 40), b=(30, 40)), prereg=late, retrofit=True)
        self.assertTrue(rec["retrofit"])
        self.assertEqual(rec["decision"], "no evidence")

    def test_materials_that_failed_the_utf8_round_trip_do_not_count(self):
        res = result(a=(4, 40), b=(30, 40))
        res["run"]["materials"] = {"sha256": None, "utf8_roundtrip": False, "note": "mojibake"}
        rec = build(res)
        self.assertFalse(rec["counts"])
        self.assertEqual(rec["decision"], "no evidence")

    def test_an_oracle_below_n_of_n_or_a_null_arm_away_from_chance_marks_the_eval_broken(self):
        res = result(a=(4, 40), b=(30, 40))
        res["anchors"] = {"oracle": {"k": 9, "n": 10}, "null": {"k": 10, "n": 10, "chance": 0.5}}
        rec = build(res)
        self.assertEqual(len(rec["eval_broken"]), 2, rec["eval_broken"])
        self.assertEqual(rec["decision"], "no evidence")
        self.assertIn("eval broken", rec["comparisons"][0]["verdict_line"])

    def test_arms_that_do_not_match_the_prereg_or_count_nothing_are_refused(self):
        res = result()
        res["arms"]["C"] = {"k": 1, "n": 8}
        with self.assertRaises(record.RecordError):
            build(res)
        res = result(a=(0, 0))
        with self.assertRaises(record.RecordError):
            build(res)


class HistoryTests(unittest.TestCase):

    def test_every_run_is_kept_and_an_edited_earlier_line_breaks_the_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            record.append(build(result()), tmp, recorded_at="t1")
            record.append(build(result(a=(1, 8))), tmp, recorded_at="t2")
            self.assertEqual(record.verify("toy", tmp), [])
            path = record.history_path("toy", tmp)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            first["decision"] = "adopted"                       # somebody improves the past
            path.write_text("\n".join([json.dumps(first, sort_keys=True)] + lines[1:]) + "\n", encoding="utf-8")
            problems = record.verify("toy", tmp)
            self.assertTrue(problems and "line 2" in problems[0], problems)
            with self.assertRaises(record.RecordError):
                record.append(build(result()), tmp)

    def test_a_missing_history_is_a_problem_not_an_intact_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(record.verify("nothing", tmp))


class SeparationTests(unittest.TestCase):

    def test_the_engine_imports_neither_the_evals_nor_the_test_harness(self):
        """TESTING s.3 rule 1: an engine that can read its evaluator or its generator can be fitted to them."""
        import ast
        offenders = []
        for path in sorted((ROOT / "xscholar").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                offenders.extend("%s imports %s" % (path.relative_to(ROOT), n) for n in names
                                 if n.split(".")[0] in ("evals", "tests"))
        self.assertEqual(offenders, [])


class MigratedExampleTests(unittest.TestCase):
    """The V2-7 marker-wording measurement, migrated from notes/2026-09-16_marker_wording.md."""

    def test_the_migrated_record_rebuilds_identically_from_its_prereg_and_result(self):
        eval_id = "V2-7-marker-wording"
        history = record.history_path(eval_id)
        self.assertEqual(record.verify(eval_id), [], "the committed history chain is broken")
        stored = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
        prereg, sha = record.load_prereg(ROOT / "evals" / "prereg" / ("%s.json" % eval_id))
        res = json.loads((ROOT / "evals" / "results" / ("%s.json" % eval_id)).read_text(encoding="utf-8"))
        rebuilt = record.build(prereg, sha, stored[0]["prereg"]["path"], res, retrofit=True,
                               scorer_sha=stored[0]["scorer_sha256"])
        for key in ("arms", "comparisons", "decision", "min_n_per_arm", "counts", "retrofit"):
            self.assertEqual(rebuilt[key], stored[0][key], key)
        self.assertEqual(stored[0]["decision"], "no evidence")
        self.assertEqual(stored[0]["arms"]["A_control"]["wilson"], [0.0, 0.3244])
        self.assertEqual(stored[0]["prereg"]["sha256"], sha, "the prereg changed after the record was made")


if __name__ == "__main__":
    unittest.main()
