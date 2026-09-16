# -*- coding: utf-8 -*-
"""The mutation runner (tests/harness/mutate.py) closes every trap the hand scripts had, and can fail.

No server. Every run here is on a temporary tree -- a synthetic one, or a copy of this repository -- and a
test asserts the live tree's bytes are unchanged afterwards. The runner's verdicts are proven both ways:
a real break is KILLED, a do-nothing test lets it SURVIVE, a comment-only break is INVALID, a skip is NOT
EXECUTED (docs/TESTING.md s.2 and s.9).
"""
import hashlib
import json
import pathlib
import socket
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.harness import mutate  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def write_tree(root, files):
    for rel, text in files.items():
        path = pathlib.Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


TOY = {
    "toypkg/__init__.py": "",
    "toypkg/limits.py": '''
        """Toy code under mutation. The word refuse in this docstring is not code."""
        LIMIT = 3


        def allowed(n):
            # refuse anything over the limit
            if n > LIMIT:
                return False
            return True
        ''',
    "tests/__init__.py": "",
    "tests/test_toy.py": '''
        import unittest
        from toypkg.limits import allowed


        class ToyTests(unittest.TestCase):
            def test_over_the_limit_is_refused(self):
                self.assertFalse(allowed(4))

            def test_asserts_nothing(self):
                allowed(4)

            def test_skips(self):
                raise unittest.SkipTest("never runs")


        if __name__ == "__main__":
            unittest.main()
        ''',
    "tests/test_server_toy.py": '''
        import unittest
        from toypkg.limits import allowed
        PORT = {port}


        class ServerToyTests(unittest.TestCase):
            def test_over_the_limit_is_refused(self):
                self.assertFalse(allowed(4))


        if __name__ == "__main__":
            unittest.main()
        ''',
}


def entry(**kw):
    base = {"id": "T1", "guard": "over the limit is refused", "file": "toypkg/limits.py",
            "anchor": "    if n > LIMIT:\n", "replacement": "    if False:\n",
            "must_fail": ["tests.test_toy.ToyTests.test_over_the_limit_is_refused"]}
    base.update(kw)
    return base


def a_free_port():
    """A port to occupy with a plain listening socket: 8110-8119 when one is free, else OS-assigned."""
    for port in range(8110, 8120):
        if not mutate.port_in_use(port):
            return port
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class AnchorTests(unittest.TestCase):

    def test_an_anchor_must_occur_exactly_once(self):
        text = "x = 1\nx = 1\n"
        _, problem = mutate.apply_edits(text, {"anchor": "x = 1", "replacement": "x = 2"}, "a.py")
        self.assertEqual(problem[0], mutate.INVALID)
        self.assertIn("2 time(s)", problem[1])
        _, problem = mutate.apply_edits(text, {"anchor": "y = 1", "replacement": "x = 2"}, "a.py")
        self.assertIn("0 time(s)", problem[1])

    def test_an_anchor_inside_a_comment_or_a_docstring_is_refused_and_one_in_a_sql_string_is_not(self):
        """D8: two tests matched the word pipefail in the comment explaining the defect."""
        text = textwrap.dedent('''
            def f():
                """uses pipefail"""
                # pipefail matters here
                return "SELECT * FROM t WHERE NOT x"
            ''')
        for anchor in ("uses pipefail", "pipefail matters"):
            with self.subTest(anchor=anchor):
                _, problem = mutate.apply_edits(text, {"anchor": anchor, "replacement": "z"}, "a.py")
                self.assertEqual(problem[0], mutate.INVALID)
                self.assertIn("comment, a docstring", problem[1])
        mutated, problem = mutate.apply_edits(text, {"anchor": "WHERE NOT x", "replacement": "WHERE x"}, "a.py")
        self.assertIsNone(problem)
        self.assertIsNone(mutate.semantic_problem(text, mutated, "a.py"))

    def test_a_yaml_anchor_inside_a_comment_is_refused(self):
        text = "shell: bash -euo pipefail {0}   # pipefail: a failing pipe fails the step\n"
        _, problem = mutate.apply_edits(text, {"anchor": "pipefail: a failing", "replacement": "x"}, "ci.yml")
        self.assertEqual(problem[0], mutate.INVALID)
        mutated, problem = mutate.apply_edits(text, {"anchor": "-euo pipefail", "replacement": "-e"}, "ci.yml")
        self.assertIsNone(problem)
        self.assertIsNone(mutate.semantic_problem(text, mutated, "ci.yml"))

    def test_a_break_that_leaves_the_ast_identical_is_invalid_and_one_that_does_not_compile_is_stillborn(self):
        before = "x = 1\n"
        self.assertEqual(mutate.semantic_problem(before, "x = 1  # now different\n", "a.py")[0], mutate.INVALID)
        self.assertEqual(mutate.semantic_problem(before, "x = (1\n", "a.py")[0], mutate.STILLBORN)
        self.assertEqual(mutate.semantic_problem("a: 1\n", "a: 1  # hi\n", "c.yml")[0], mutate.INVALID)


class ClassifyTests(unittest.TestCase):
    E = {"must_fail": ["tests.t.C.test_a", "tests.t.C.test_b"], "timeout_s": 5}

    def verdict(self, outcomes, **run):
        base = {"tests": outcomes, "detail": {}, "timed_out": False, "harness_error": None}
        base.update(run)
        return mutate.classify(dict(self.E, **run.pop("entry", {})), base)[0]

    def test_every_outcome_maps_to_exactly_one_verdict(self):
        a, b = self.E["must_fail"]
        self.assertEqual(self.verdict({a: "fail", b: "error:KeyError"}), mutate.KILLED)
        self.assertEqual(self.verdict({a: "fail", b: "pass"}), mutate.SURVIVED)
        self.assertEqual(self.verdict({a: "fail", b: "skip"}), mutate.NOT_EXECUTED)
        self.assertEqual(self.verdict({a: "fail", b: "import:ImportError"}), mutate.STILLBORN)
        self.assertEqual(self.verdict({a: "fail", b: "error:SkipTest"}), mutate.STILLBORN)
        self.assertEqual(self.verdict({a: "fail", b: "missing"}), mutate.STILLBORN)
        self.assertEqual(self.verdict({}, harness_error="imported outside"), mutate.NOT_EXECUTED)
        self.assertEqual(self.verdict({}, timed_out=True), mutate.TIMEOUT)
        self.assertEqual(mutate.classify(dict(self.E, expect="killed-by-timeout"),
                                         {"tests": {}, "detail": {}, "timed_out": True, "harness_error": None})[0],
                         mutate.KILLED_TIMEOUT)

    def test_exit_codes_fail_on_anything_but_killed(self):
        self.assertEqual(mutate.exit_code([{"verdict": mutate.KILLED}]), 0)
        for bad in (mutate.SURVIVED, mutate.INVALID, mutate.STILLBORN, mutate.PREFLIGHT_FAILED, mutate.TIMEOUT):
            self.assertEqual(mutate.exit_code([{"verdict": mutate.KILLED}, {"verdict": bad}]), 1, bad)
        self.assertEqual(mutate.exit_code([{"verdict": mutate.KILLED}, {"verdict": mutate.NOT_EXECUTED}]), 2)


class RunOnAToyTreeTests(unittest.TestCase):
    """The whole runner, subprocess and reporter included, on a tree small enough to read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = pathlib.Path(self.tmp.name) / "tree"
        self.port = a_free_port()
        files = dict(TOY)
        files["tests/test_server_toy.py"] = TOY["tests/test_server_toy.py"].replace("{port}", str(self.port))
        write_tree(self.tree, files)

    def tearDown(self):
        self.tmp.cleanup()

    def run_entries(self, *entries):
        return {r["id"]: r for r in mutate.run(self.tree, list(entries), say=lambda *a: None)}

    def test_a_real_break_is_killed_and_the_same_break_under_a_test_that_asserts_nothing_survives(self):
        before = hashlib.sha256((self.tree / "toypkg/limits.py").read_bytes()).hexdigest()
        results = self.run_entries(entry(), entry(id="T2", must_fail=["tests.test_toy.ToyTests.test_asserts_nothing"]))
        self.assertEqual(results["T1"]["verdict"], mutate.KILLED, results["T1"]["reason"])
        self.assertEqual(results["T2"]["verdict"], mutate.SURVIVED, results["T2"]["reason"])
        self.assertIn("test_asserts_nothing", results["T2"]["reason"])
        self.assertEqual(mutate.exit_code(list(results.values())), 1)
        self.assertEqual(hashlib.sha256((self.tree / "toypkg/limits.py").read_bytes()).hexdigest(), before,
                         "the mutated file was not restored byte for byte")

    def test_a_test_that_skips_on_the_unmutated_tree_is_not_executed(self):
        result = self.run_entries(entry(must_fail=["tests.test_toy.ToyTests.test_skips"]))["T1"]
        self.assertEqual(result["verdict"], mutate.NOT_EXECUTED)
        self.assertIn("UNMUTATED", result["reason"])

    def test_a_named_test_that_does_not_exist_is_refused_before_anything_runs(self):
        result = self.run_entries(entry(must_fail=["tests.test_toy.ToyTests.test_renamed_away"]))["T1"]
        self.assertEqual(result["verdict"], mutate.PREFLIGHT_FAILED)
        self.assertIn("has no method test_renamed_away", result["reason"])

    def test_a_server_suite_whose_port_is_taken_is_not_executed_and_not_run(self):
        e = entry(needs_db=True, must_fail=["tests.test_server_toy.ServerToyTests.test_over_the_limit_is_refused"])
        with mock.patch.object(mutate, "surreal_available", return_value=True):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", self.port))
                sock.listen(1)
                with mock.patch.object(mutate, "run_tests", side_effect=AssertionError("must not run")):
                    result = self.run_entries(e)["T1"]
        self.assertEqual(result["verdict"], mutate.NOT_EXECUTED)
        self.assertIn("port(s) %d in use" % self.port, result["reason"])
        with mock.patch.object(mutate, "surreal_available", return_value=False):
            result = self.run_entries(e)["T1"]
        self.assertEqual(result["verdict"], mutate.NOT_EXECUTED)
        self.assertIn("binary not found", result["reason"])

    def test_a_break_that_stops_the_test_module_importing_is_stillborn(self):
        result = self.run_entries(entry(anchor="def allowed(n):", replacement="def permitted(n):"))["T1"]
        self.assertEqual(result["verdict"], mutate.STILLBORN, result["reason"])

    def test_a_named_test_below_the_main_block_is_refused_by_name(self):
        """D18: a class below `if __name__ == "__main__":` never runs when the file is run directly."""
        path = self.tree / "tests" / "test_toy.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n\nclass Late(unittest.TestCase):\n"
                        "    def test_x(self):\n        self.assertFalse(True)\n", encoding="utf-8")
        why = mutate.test_id_problem(self.tree, "tests.test_toy.Late.test_x")
        self.assertIn("below the __main__ block", why)


class ReporterTests(unittest.TestCase):

    def test_a_module_imported_from_outside_the_tree_copy_is_reported_so_the_break_is_never_judged(self):
        """Measured on the development machine: xscholar is installed editable, so an import that does not
        find the copy's own package silently tests the LIVE, unmutated code."""
        import os
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            outside, tree = pathlib.Path(tmp) / "outside", pathlib.Path(tmp) / "tree"
            write_tree(outside, {"xscholar/__init__.py": "VALUE = 1"})
            write_tree(tree, {"tests/__init__.py": "", "tests/test_x.py": """
                import unittest
                import xscholar


                class X(unittest.TestCase):
                    def test_x(self):
                        self.assertEqual(xscholar.VALUE, 1)


                if __name__ == "__main__":
                    unittest.main()
                """})
            out = pathlib.Path(tmp) / "out.json"
            subprocess.run([sys.executable, str(mutate.REPORTER), str(tree), str(out), "tests.test_x.X.test_x"],
                           cwd=str(tree), env=dict(os.environ, PYTHONPATH=str(outside)), check=True,
                           capture_output=True)
            report = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(any(entry.startswith("xscholar from") for entry in report["imported_outside_root"]), report)
        self.assertEqual(report["tests"]["tests.test_x.X.test_x"], "not_run", "a test run against code outside the copy")


class RegistryTests(unittest.TestCase):

    def test_the_registry_is_seeded_and_pinned_and_every_entry_names_its_source(self):
        entries = mutate.load_registry()
        pinned = json.loads(mutate.PINNED.read_text(encoding="utf-8"))["ids"]
        self.assertGreaterEqual(len(entries), 60)
        self.assertEqual(sorted(pinned), sorted(e["id"] for e in entries))
        for e in entries:
            with self.subTest(entry=e["id"]):
                self.assertTrue(e.get("source") and e.get("commit"), "an entry must say where the break was proven")

    def test_lint_fails_a_removed_pinned_id_and_an_unpinned_new_one(self):
        entries = mutate.load_registry()
        pinned = [e["id"] for e in entries]
        problems = mutate.lint(ROOT, entries[1:], pinned)
        self.assertTrue(any("%s is pinned" % entries[0]["id"] in p and "takes a human" in p for p in problems), problems[:3])
        problems = mutate.lint(ROOT, entries[:1] + [dict(entries[0], id="M999")], pinned[:1])
        self.assertTrue(any("M999 is not pinned" in p for p in problems), problems[:3])

    def test_a_registry_of_nothing_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "r.json"
            path.write_text(json.dumps({"mutations": []}), encoding="utf-8")
            with self.assertRaises(mutate.RegistryError):
                mutate.load_registry(path)
            path.write_text(json.dumps({"mutations": [{"id": "X", "guard": "g", "file": "f",
                                                        "must_fail": ["tests.a.B.test_c"]}]}), encoding="utf-8")
            with self.assertRaises(mutate.RegistryError) as raised:
                mutate.load_registry(path)
            self.assertIn("anchor+replacement or edits", str(raised.exception))

    def test_a_real_registered_break_is_killed_on_a_copy_and_the_live_tree_is_untouched(self):
        entries = [e for e in mutate.load_registry() if e["file"] == "xscholar/pipeline/agreement.py"]
        self.assertTrue(entries, "the registry lost its kappa break (D11)")
        live = ROOT / entries[0]["file"]
        before = hashlib.sha256(live.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            tree = pathlib.Path(tmp) / "tree"
            mutate.build_tree("worktree", tree)
            snapshot = mutate.tree_snapshot(tree)
            results = mutate.run(tree, entries[:1], say=lambda *a: None)
            restore_failures, _ = mutate.snapshot_drift(snapshot, mutate.tree_snapshot(tree), {entries[0]["file"]})
        self.assertEqual(results[0]["verdict"], mutate.KILLED, results[0]["reason"])
        self.assertEqual(restore_failures, [])
        self.assertEqual(hashlib.sha256(live.read_bytes()).hexdigest(), before, "the LIVE tree was written")

    def test_the_canaries_all_behave(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = pathlib.Path(tmp) / "tree"
            mutate.build_tree("worktree", tree)
            behaved, problems = mutate.run_canaries(tree, say=lambda *a: None)
        self.assertEqual(problems, [])
        self.assertGreaterEqual(behaved, 8)

    def test_snapshot_drift_tells_a_failed_restore_from_a_file_a_test_wrote(self):
        before = {"a.py": "1", "b.py": "2"}
        after = {"a.py": "9", "b.py": "2", "out/x.json": "3"}
        self.assertEqual(mutate.snapshot_drift(before, after, {"a.py"}), (["a.py"], ["out/x.json"]))


class CiWiringTests(unittest.TestCase):
    """CI runs the breaks for changed files on every push, and the full registry on a schedule. Read with
    comments stripped, because D8 was a test that matched the comment explaining the defect."""

    def setUp(self):
        raw = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.lines = [mutate.strip_hash_comments(line).strip() for line in raw.splitlines()]

    def test_every_push_runs_the_breaks_for_the_files_it_changed_and_a_schedule_runs_them_all(self):
        runs = [ln for ln in self.lines if ln.startswith("python -m tests.harness.mutate run")]
        self.assertTrue(any("--changed-since" in ln for ln in runs), runs)
        self.assertTrue(any(ln.endswith("run --from HEAD") for ln in runs), "no full-registry run: %s" % runs)
        self.assertIn("schedule:", self.lines)
        self.assertIn("fetch-depth: 0", self.lines, "without history --changed-since has no base to diff against")


if __name__ == "__main__":
    unittest.main()
