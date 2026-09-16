"""The committed baselines still describe the gates that are in the configs.

This runs without a database on purpose: the half of the check that catches a weakened gate
(a rule deleted, a clause dropped, a minimum lowered) reads the config alone, so it runs
everywhere the suite runs. The flag half (`wrong_evidence_reject` was true, now is not) needs a
live probe run and happens in `xscholar.gates --apply --baseline`, which CI runs.

Deliberate change to a gate: re-run
    python -m xscholar.gates <instance> --apply --write-baseline <baseline>
and review the diff in the pull request. That review is the point of the file.
"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from xscholar import config, gates  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def baselines():
    return sorted(ROOT.glob("instances/*/gate_baseline.json"))


class BaselineTests(unittest.TestCase):

    def test_at_least_the_demo_instance_ships_a_baseline(self):
        names = [p.parent.name for p in baselines()]
        self.assertIn("demo-incident", names,
                      "the demo instance is the worked example; it must show what a baseline looks like")

    def test_no_shipped_gate_is_weaker_than_its_baseline(self):
        for path in baselines():
            with self.subTest(instance=path.parent.name):
                baseline = json.loads(path.read_text(encoding="utf-8"))
                inst = config.load(path.parent / "instance.yaml")
                failures = gates.compare_baseline(baseline, None, gates.spec(inst))
                self.assertEqual(failures, [], "%s: %s" % (path, "; ".join(failures)))

    def test_the_baseline_records_a_probe_run_that_behaved(self):
        for path in baselines():
            with self.subTest(instance=path.parent.name):
                report = json.loads(path.read_text(encoding="utf-8")).get("report") or {}
                self.assertEqual(report.get("misbehaved"), 0,
                                 "%s was written from a run that already misbehaved" % path)
                self.assertTrue(report.get("rules"), "%s has no probe results to compare against" % path)


    def test_nothing_automated_can_rewrite_a_baseline(self):
        """The file is the check. A pipeline that regenerates it before comparing would report
        "unchanged or stronger" for every future weakening, and the diff a human is meant to read
        would never appear in a pull request. Only the tool that takes the flag may name it."""
        watched = (sorted(ROOT.glob(".github/workflows/*.yml")) + sorted(ROOT.glob("scripts/*.py"))
                   + [ROOT / "xscholar" / "cli.py"])
        self.assertTrue(watched)
        for path in watched:
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("--write-baseline", text,
                                 "%s regenerates a gate baseline; only a human at a terminal may" % path)
                self.assertNotIn("write_baseline", text, "%s regenerates a gate baseline" % path)

    def test_ci_compares_against_a_baseline_that_is_committed(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("--baseline", ci, "CI does not compare the gates against any baseline")
        named = [word for word in ci.split() if word.endswith("gate_baseline.json")]
        self.assertTrue(named, "CI names no baseline file")
        for name in named:
            self.assertTrue((ROOT / name).exists(), "CI compares against %s, which is not committed" % name)


if __name__ == "__main__":
    unittest.main()
