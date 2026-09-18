# -*- coding: utf-8 -*-
"""The checks that stop a green result meaning nothing: the demo's verdict, the privacy scan, CI.

All three were found by an architect audit asking the charter's first question -- "can this check
pass without doing its work?" -- and all three answered yes:

  - `run_instance.py` judged the whole typed-write walkthrough by a substring of a phrase the
    INSTANCE'S OWN adapter chooses. Measured on the committed code with a neutered `scenario()`:
    exit 0, `"facts": []`, and the word "done".
  - `privacy_scan.py` used an allow list of text extensions, so it walked past .csv, .xml and .sql
    without opening them. Measured: "privacy scan clean: 2 files" over three files that each held
    the identifier. It also printed "clean" having read nothing at all.
  - CI's install step could not fail (`curl | sh` exits 0 under `bash -e` without pipefail), and
    `unittest` exits 0 when every database suite skips: "Ran 0 tests ... OK (skipped=2)".

No SurrealDB needed here: these are the parts that can be checked without one.
"""
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_run_instance():
    spec = importlib.util.spec_from_file_location("run_instance", ROOT / "scripts" / "run_instance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeConn:
    def __init__(self, facts):
        self._facts = facts

    def count(self, table):
        return self._facts if table == "fact" else 0


HEALTHY = {"calls": 17, "refusals": 4, "denials": 3}
STEPS = [("who", "step", "ACCEPTED")]


class ScenarioVerdictTests(unittest.TestCase):
    """The verdict must come from what the engine saw, not from what the adapter said."""

    def setUp(self):
        self.run_instance = load_run_instance()

    def problems(self, lines=STEPS, tally=None, facts=3):
        return self.run_instance.scenario_problems(lines, dict(tally or HEALTHY), FakeConn(facts))

    def test_a_walkthrough_that_really_happened_has_no_problems(self):
        self.assertEqual(self.problems(), [])

    def test_an_adapter_that_reports_nothing_is_not_a_passing_run(self):
        problems = self.problems(lines=[])
        self.assertTrue(any("reported no steps" in p for p in problems), problems)

    def test_a_scenario_that_wrote_nothing_is_not_a_passing_run(self):
        problems = self.problems(tally={"calls": 0, "refusals": 0, "denials": 0})
        self.assertTrue(any("wrote nothing" in p for p in problems), problems)

    def test_a_scenario_the_datastore_never_refused_has_not_shown_a_gate_working(self):
        problems = self.problems(tally={"calls": 9, "refusals": 0, "denials": 0})
        self.assertTrue(any("refused nothing" in p for p in problems), problems)

    def test_either_kind_of_refusal_counts(self):
        """A gate refusal or a permission denial both prove the datastore said no."""
        self.assertEqual(self.problems(tally={"calls": 9, "refusals": 1, "denials": 0}), [])
        self.assertEqual(self.problems(tally={"calls": 9, "refusals": 0, "denials": 1}), [])

    def test_an_assertion_contradiction_is_never_printed_as_a_gate_that_no_longer_holds(self):
        """consistency.report() gained an `assertions` key. Copied whole under DRIFT_gate_no_longer_holds, a
        contradicting assertion read as a promoted row whose gate had stopped holding: a different finding, cured
        a different way."""
        report = {"rules": [{"rule": "r"}], "rows_at_gated_value": 3, "contradictions": 0,
                  "assertions": {"assertion_rows": 2, "counted": 1, "agree": 0,
                                 "contradictions": [{"target": "unit:u1"}], "disagreeing_assertions": []}}
        drift, found = self.run_instance.health_consistency(report)
        self.assertEqual(drift, {"rows_at_gated_value": 3, "contradictions": 0})
        self.assertEqual(found, {"assertion_rows": 2, "counted": 1, "agree": 0, "contradictions": 1,
                                 "disagreeing_assertions": 0})
        self.assertEqual(self.run_instance.health_consistency({"contradictions": 0})[1], "none declared")

    def test_an_empty_graph_is_not_a_passing_run(self):
        problems = self.problems(facts=0)
        self.assertTrue(any("no fact rows" in p for p in problems), problems)

    def test_the_counter_records_calls_and_re_raises_what_the_datastore_refused(self):
        run_instance = self.run_instance

        class Boom:
            def confirm(self, *a):
                raise run_instance.write.GateRefusal("r", ["evidence"])

            def propose(self, *a):
                return "ok"

            author = "someone"

        counted = run_instance.Counted(Boom())
        self.assertEqual(counted.propose("x"), "ok")
        self.assertEqual(counted.author, "someone", "non-callable attributes pass straight through")
        with self.assertRaises(run_instance.write.GateRefusal):
            counted.confirm("x")
        self.assertEqual((counted.calls, counted.refusals), (2, 1))
        self.assertEqual(run_instance.Counted.tally([counted]), {"calls": 2, "refusals": 1, "denials": 0})


class PrivacyScanTests(unittest.TestCase):
    """A scan that did not open a file has not cleared it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = pathlib.Path(self.tmp.name)
        self.deny = self.work / "deny.txt"
        self.deny.write_text("ZZQUUX_FAKE_IDENTIFIER\n", encoding="utf-8")

    def scan(self, files, args=()):
        repo = self.work / "repo"
        (repo / "scripts").mkdir(parents=True, exist_ok=True)
        (repo / "scripts" / "privacy_scan.py").write_text(
            (ROOT / "scripts" / "privacy_scan.py").read_text(encoding="utf-8"), encoding="utf-8")
        for name, body in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        env = dict(os.environ, GROUNDWORKS_DENYLIST=str(self.deny))
        return subprocess.run([sys.executable, "scripts/privacy_scan.py"] + list(args),
                              cwd=repo, capture_output=True, text=True, env=env)

    def test_formats_the_old_allow_list_walked_past_are_read_now(self):
        for name in ("data/matter.csv", "data/feed.xml", "data/dump.sql", "notes/page.mdx", "run.log"):
            with self.subTest(file=name):
                out = self.scan({name: "x,ZZQUUX_FAKE_IDENTIFIER\n"})
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertIn("FAILED", out.stdout)

    def test_an_extension_nobody_thought_of_is_read_rather_than_skipped(self):
        out = self.scan({"data/whatever.somenewformat": "ZZQUUX_FAKE_IDENTIFIER\n"})
        self.assertEqual(out.returncode, 1, "an unknown extension must be read, not skipped")

    def test_a_scan_that_read_nothing_is_not_clean(self):
        """In process, because any real repo contains the scanner itself and so always reads one file."""
        spec = importlib.util.spec_from_file_location("privacy_scan", ROOT / "scripts" / "privacy_scan.py")
        scan = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scan)
        binaries = [self.work / "logo.png", self.work / "report.pdf"]
        for b in binaries:
            b.write_text("x", encoding="utf-8")
        scan.ROOT = self.work
        scan.files = lambda _all: binaries
        os.environ["GROUNDWORKS_DENYLIST"] = str(self.deny)
        buf, sys.stdout = sys.stdout, io.StringIO()
        try:
            rc, printed = scan.main([]), sys.stdout.getvalue()
        finally:
            sys.stdout = buf
        self.assertEqual(rc, 2, printed)
        self.assertIn("NOT EXECUTED", printed)
        self.assertIn("has cleared nothing", printed)
        self.assertIn(".pdf", printed)

    def test_files_it_could_not_read_are_counted_and_named(self):
        out = self.scan({"readme.md": "hello", "doc/scan.pdf": "%PDF", "art/logo.png": "x"})
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("NOT read", out.stdout)
        self.assertIn(".pdf", out.stdout)
        self.assertIn(".png", out.stdout)

    def test_a_missing_or_empty_deny_list_still_fails_closed(self):
        self.deny.write_text("\n# only a comment\n", encoding="utf-8")
        self.assertEqual(self.scan({"readme.md": "hello"}).returncode, 2)
        self.deny.unlink()
        self.assertEqual(self.scan({"readme.md": "hello"}).returncode, 2)


class CiTests(unittest.TestCase):
    """CI must be able to go red. Each assertion here is a way it silently could not."""

    def setUp(self):
        raw = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        # Comments are stripped first. The first version of these tests matched the WORD "pipefail"
        # in the comment that explains the defect, so deleting the setting left them green -- this
        # file's own subject matter, in this file. Only mutation found it.
        self.lines = [ln.split("#", 1)[0].rstrip() for ln in raw.splitlines()]
        self.ci = "\n".join(ln for ln in self.lines if ln.strip())

    def run_lines(self):
        return [ln.strip() for ln in self.lines if ln.strip()]

    def test_a_failing_command_inside_a_pipe_fails_the_step(self):
        shell = [ln for ln in self.run_lines() if ln.startswith("shell:")]
        self.assertTrue(shell, "no shell: is declared, so steps run under bash -e without pipefail")
        self.assertTrue(all("pipefail" in ln for ln in shell),
                        "without pipefail `curl -sSf ... | sh` exits 0 when curl fails and the "
                        "database suites then skip behind a green tick: %s" % shell)

    def test_the_install_is_verified_rather_than_assumed(self):
        self.assertIn("command -v surreal", self.ci)

    def test_a_skipped_suite_fails_the_build(self):
        invocations = [ln for ln in self.run_lines() if "groundworks.cli test" in ln]
        self.assertTrue(invocations, "CI does not run the test command at all")
        self.assertTrue(all("--require-db" in ln for ln in invocations),
                        "unittest exits 0 on a skipped suite ('Ran 0 tests ... OK (skipped=2)'), so "
                        "every CI invocation must require the database suites: %s" % invocations)

    def test_the_backgrounded_server_is_checked_for_being_alive(self):
        self.assertIn("kill -0", self.ci, "`&` throws the exit status away")

    def test_the_privacy_scan_has_an_automated_caller(self):
        cli = (ROOT / "groundworks" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("privacy_scan.py", cli,
                      "the repo's most important guard ran only when a human remembered")


if __name__ == "__main__":
    unittest.main()
