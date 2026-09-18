# -*- coding: utf-8 -*-
"""The evaluator's regression file: refused when missing or malformed, executed, and able to go red.

No server. The database-backed kinds are exercised with a stand-in Writer through the runner's own seam,
and against a real server by `groundworks test` (the evaluator step), not here: every suite port is taken.

Found 2026-09-16: config required `evaluator.regression_file`, the demo declared `evals/known_errors.json`,
the file did not exist, and no code read the key. The evaluator every improvement must beat had never run.
"""
import ast
import copy
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from groundworks import cli, config, evaluator  # noqa: E402
from groundworks.pipeline.write import GateRefusal  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "instances" / "demo-incident"


def demo_data():
    return json.loads(json.dumps(config.load(DEMO_DIR / "instance.yaml").data))


def a_file(**changes):
    data = {"format": evaluator.FORMAT, "entries": [
        {"id": "KE900", "error": "a planted misquote passed", "found": {"commit": "test"},
         "check": {"kind": "validator",
                   "slice": [{"id": "u1", "type": "log_line", "text": "pool exhausted at noon", "locator": {},
                              "status": "in_window", "grade": "original_log", "hash": "h"}],
                   "labels": [{"id": "u1", "tier": 2, "dir": "for_goal", "issues": ["saturation"], "targets": [],
                               "role": "error", "reason": "r", "quote": "pool drained at noon"}],
                   "control_labels": [{"id": "u1", "tier": 2, "dir": "for_goal", "issues": ["saturation"],
                                       "targets": [], "role": "error", "reason": "r", "quote": "pool exhausted at"}],
                   "expect_errors": [{"unit": "u1", "field": "quote"}]}}]}
    data.update(changes)
    return data


class InstanceDir:
    """A copy of the demo's config in a temporary directory, with whatever regression file a test wants."""

    def __init__(self, regression=None, raw=None, declared="evals/known_errors.json", pins="match"):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        data = demo_data()
        data["evaluator"]["regression_file"] = declared
        (self.root / "instance.json").write_text(json.dumps(data), encoding="utf-8")
        if regression is not None or raw is not None:
            path = self.root / declared
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw if raw is not None else json.dumps(regression).encode("utf-8"))
            if pins == "match" and regression is not None:      # the pinned id list a well-kept file has beside it
                pins = [entry.get("id") for entry in regression.get("entries") or []]
            if isinstance(pins, list):
                evaluator.pinned_path(path).write_text(json.dumps({"ids": pins}), encoding="utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()


class ConfigRefusalTests(unittest.TestCase):

    def test_a_declared_regression_file_that_does_not_exist_is_refused_by_name(self):
        with InstanceDir() as inst_dir:
            with self.assertRaises(config.ConfigError) as raised:
                config.load(inst_dir.root / "instance.json")
        self.assertIn("known_errors.json", str(raised.exception))
        self.assertIn("does not exist", str(raised.exception))

    def test_an_unparseable_or_malformed_regression_file_is_refused_by_name(self):
        for raw in (b"{not json", json.dumps({"format": "something else", "entries": []}).encode("utf-8"),
                    json.dumps({"format": evaluator.FORMAT}).encode("utf-8"), b"\xff\xfe not utf-8"):
            with self.subTest(raw=raw[:30]), InstanceDir(raw=raw) as inst_dir:
                with self.assertRaises(config.ConfigError) as raised:
                    config.load(inst_dir.root / "instance.json")
                self.assertIn("regression_file", str(raised.exception))

    def test_a_valid_file_loads_and_resolves_against_the_config_files_own_directory(self):
        with InstanceDir(regression=a_file(), declared="deep/er/known.json") as inst_dir:
            inst = config.load(inst_dir.root / "instance.json")
            self.assertEqual(config.regression_file_path(inst), (inst_dir.root / "deep/er/known.json").resolve())

    def test_a_config_loaded_from_a_mapping_has_no_directory_and_says_so(self):
        inst = config.load(demo_data())
        with self.assertRaises(config.ConfigError) as raised:
            config.regression_file_path(inst)
        self.assertIn("mapping", str(raised.exception))
        self.assertEqual(config.regression_file_path(inst, base_dir=DEMO_DIR),
                         (DEMO_DIR / "evals" / "known_errors.json").resolve())

    def test_the_template_and_the_demo_say_what_a_relative_regression_file_resolves_against(self):
        """In real use the file resolved against the config's folder while every other path in that config was
        written from the repository root, and nothing beside the key said which base applies (2026-09-16)."""
        for path in (ROOT / "instances" / "_template" / "instance.yaml", DEMO_DIR / "instance.yaml"):
            with self.subTest(instance=path.parent.name):
                lines = path.read_text(encoding="utf-8").splitlines()
                key = next(i for i, ln in enumerate(lines) if ln.strip().startswith("regression_file:"))
                comment = " ".join(ln.strip().lstrip("#").strip() for ln in lines[max(0, key - 4):key]
                                   if ln.strip().startswith("#")).lower()
                self.assertIn("the folder this instance.yaml is in", comment, lines[max(0, key - 4):key + 1])
                with InstanceDir(regression=a_file(), declared="evals/known_errors.json") as inst_dir:
                    inst = config.load(inst_dir.root / "instance.json")
                    self.assertEqual(config.regression_file_path(inst).parent.parent, inst_dir.root.resolve())

    def test_the_demo_and_the_template_declare_files_that_exist_and_parse(self):
        for path in (DEMO_DIR / "instance.yaml", ROOT / "instances" / "_template" / "instance.yaml"):
            with self.subTest(instance=path.parent.name):
                inst = config.load(path)
                data, _ = evaluator.read_file(config.regression_file_path(inst))
                self.assertEqual(data["format"], evaluator.FORMAT)


class FormatTests(unittest.TestCase):

    def refused(self, data, *words):
        with self.assertRaises(evaluator.RegressionFileError) as raised:
            evaluator.parse(json.dumps(data))
        for word in words:
            self.assertIn(word, str(raised.exception))

    def test_a_writes_check_must_show_both_directions(self):
        steps = [{"as": "w", "op": "confirm", "args": ["f"], "expect": "refused"}]
        self.refused(a_file(entries=[{"id": "K", "error": "e", "found": {"x": 1},
                                      "check": {"kind": "writes", "writers": {"w": {"role": "r"}}, "steps": steps}}]),
                     "both directions")

    def test_a_loads_check_that_expects_nothing_is_refused(self):
        self.refused(a_file(entries=[{"id": "K", "error": "e", "found": {"x": 1},
                                      "check": {"kind": "loads", "steps": [{"load": {"labels": "fixture"}}]}}]),
                     "expect_")

    def test_unknown_kinds_ops_and_duplicate_ids_are_refused(self):
        entry = a_file()["entries"][0]
        self.refused(a_file(entries=[entry, copy.deepcopy(entry)]), "duplicate id")
        bad = copy.deepcopy(entry)
        bad["check"]["kind"] = "vibes"
        self.refused(a_file(entries=[bad]), "check kind")

    def test_the_demo_file_is_valid_and_every_entry_names_the_commit_that_found_it(self):
        data, _ = evaluator.read_file(DEMO_DIR / "evals" / "known_errors.json")
        self.assertGreaterEqual(len(data["entries"]), 3)
        for entry in data["entries"]:
            with self.subTest(entry=entry["id"]):
                self.assertRegex(entry["found"].get("commit", ""), r"^[0-9a-f]{7}$")
                self.assertTrue(entry["found"].get("measured"), "an entry with no recorded reproduction is invented")


class RunTests(unittest.TestCase):

    def run_file(self, data, **kwargs):
        with InstanceDir(regression=data) as inst_dir:
            inst = config.load(inst_dir.root / "instance.json")
            return evaluator.run(inst, DEMO_DIR, config.regression_file_path(inst), say=lambda *a: None, **kwargs)[0]

    def test_a_planted_misquote_is_caught_and_a_validator_that_stopped_checking_quotes_regresses(self):
        self.assertEqual([r["verdict"] for r in self.run_file(a_file())], [evaluator.CAUGHT])
        from groundworks.pipeline import validator
        with mock.patch.object(validator, "_norm", side_effect=lambda text: "" if text is None else "x") as norm:
            results = self.run_file(a_file())
        self.assertTrue(norm.called, "the stand-in for a validator that stopped checking quotes never ran")
        self.assertEqual(results[0]["verdict"], evaluator.REGRESSED, results[0]["reason"])

    def test_a_check_that_needs_a_database_with_none_given_is_not_executed_never_caught(self):
        entry = {"id": "K1", "error": "e", "found": {"commit": "x"},
                 "check": {"kind": "loads", "steps": [{"load": {"labels": "fixture"}},
                                                      {"expect_count": {"table": "bears_on", "equals": 1}}]}}
        results = self.run_file(a_file(entries=[entry]), url=None)
        self.assertEqual(results[0]["verdict"], evaluator.NOT_EXECUTED)
        self.assertEqual(evaluator.exit_code(results), 2)

    def test_an_empty_evaluator_is_not_a_pass(self):
        self.assertEqual(evaluator.exit_code([]), 2)

    def test_a_writes_check_classifies_each_step_and_names_the_one_that_regressed(self):
        check = {"kind": "writes", "writers": {"w": {"role": "reader", "lens": None}},
                 "steps": [{"as": "w", "op": "propose", "args": ["f", "s"], "expect": "accepted"},
                           {"as": "w", "op": "confirm", "args": ["f", []], "expect": "refused"},
                           {"as": "w", "op": "confirm", "args": ["f", ["u"]], "expect": "accepted"}]}

        class Gate:
            """Stands in for the datastore: refuses a confirm with no citation, unless `broken`."""
            broken = False

            def propose(self, slug, statement):
                return "fact:" + slug

            def confirm(self, slug, units):
                if not units and not Gate.broken:
                    raise GateRefusal("rule", "needs a citation")
                return "fact:" + slug

        conn = mock.Mock()
        inst = config.load(DEMO_DIR / "instance.yaml")
        with mock.patch.object(evaluator, "apply_schema"):
            verdict, reason, log = evaluator.run_writes(check, inst, conn, writer_factory=lambda n, s: Gate())
            self.assertEqual(verdict, evaluator.CAUGHT, reason)
            Gate.broken = True
            verdict, reason, log = evaluator.run_writes(check, inst, conn, writer_factory=lambda n, s: Gate())
        self.assertEqual(verdict, evaluator.REGRESSED)
        self.assertIn("step 2", reason)
        self.assertIn("expected refused, got accepted", reason)

    def test_a_step_that_raises_something_else_is_never_read_as_accepted_or_refused(self):
        check = {"kind": "writes", "writers": {"w": {"role": "reader"}},
                 "steps": [{"as": "w", "op": "mark_ready", "args": ["c"], "expect": "refused"},
                           {"as": "w", "op": "mark_ready", "args": ["c"], "expect": "accepted"}]}

        class Crashes:
            def mark_ready(self, conclusion):
                raise RuntimeError("database record already exists")

        with mock.patch.object(evaluator, "apply_schema"):
            verdict, reason, _ = evaluator.run_writes(check, config.load(DEMO_DIR / "instance.yaml"), mock.Mock(),
                                                      writer_factory=lambda n, s: Crashes())
        self.assertEqual(verdict, evaluator.REGRESSED)
        self.assertIn("error:RuntimeError", reason)

    def test_a_regression_file_rewritten_during_the_run_is_refused(self):
        with InstanceDir(regression=a_file()) as inst_dir:
            inst = config.load(inst_dir.root / "instance.json")
            path = config.regression_file_path(inst)
            real = evaluator.run_validator

            def tampering(check, inst_, work):
                path.write_text(json.dumps(a_file(entries=[])), encoding="utf-8")
                return real(check, inst_, work)

            with mock.patch.object(evaluator, "run_validator", side_effect=tampering):
                with self.assertRaises(evaluator.RegressionFileError) as raised:
                    evaluator.run(inst, DEMO_DIR, path, say=lambda *a: None)
        self.assertIn("changed while the evaluator ran", str(raised.exception))

    def test_nothing_in_the_evaluator_can_write_the_regression_file(self):
        """Human-owned: the runner opens the file read-only. Writes it makes go to a temporary directory
        or to --json, and --json may not name the regression file."""
        tree = ast.parse((ROOT / "groundworks" / "evaluator.py").read_text(encoding="utf-8"))
        writers = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("write_text", "write_bytes"):
                target = ast.unparse(node.func.value)
                writers.append(target)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
                writers.append("open(%s)" % ast.unparse(node))
        allowed = {"labels_dir / (chunk['id'] + '.jsonl')", "path", "pathlib.Path(args.json_out)"}
        self.assertTrue(set(writers) <= allowed, "unexpected write targets in evaluator.py: %s" % (set(writers) - allowed))


class PinnedIdTests(unittest.TestCase):
    """A known error deleted from the regression file between commits is refused, as a removed mutation id is.
    Measured 2026-09-16 before the fix: a copy of the demo with KE003 deleted ran `2 entries | 2 still caught`,
    exit 0; the runner only checked that the file did not change DURING a run."""

    def two(self):
        second = copy.deepcopy(a_file()["entries"][0])
        second["id"] = "KE901"
        return a_file(entries=[a_file()["entries"][0], second])

    def main(self, inst_dir, *args):
        # main() reads INSTANCE_DIR/instance.yaml: the demo's own, which declares evals/known_errors.json as InstanceDir does
        (inst_dir.root / "instance.yaml").write_bytes((DEMO_DIR / "instance.yaml").read_bytes())
        with mock.patch("builtins.print") as printed:
            code = evaluator.main([str(inst_dir.root)] + list(args))
        return code, " ".join(str(c.args[0]) for c in printed.call_args_list if c.args)

    def test_a_known_error_deleted_from_the_file_is_refused_naming_its_id(self):
        with InstanceDir(regression=a_file(), pins=["KE900", "KE901"]) as inst_dir:
            inst = config.load(inst_dir.root / "instance.json")
            with self.assertRaises(evaluator.RegressionFileError) as raised:
                evaluator.run(inst, DEMO_DIR, config.regression_file_path(inst), say=lambda *a: None)
            self.assertIn("KE901 is pinned", str(raised.exception))
            code, out = self.main(inst_dir, "--check")
            self.assertEqual(code, 1, out)
            self.assertIn("KE901", out)
        with InstanceDir(regression=self.two()) as inst_dir:
            self.assertEqual(self.main(inst_dir, "--check")[0], 0)

    def test_an_entry_that_is_not_pinned_is_refused(self):
        with InstanceDir(regression=self.two(), pins=["KE900"]) as inst_dir:
            code, out = self.main(inst_dir, "--check")
        self.assertEqual(code, 1, out)
        self.assertIn("KE901 is not pinned", out)

    def test_a_regression_file_with_no_pinned_list_is_not_executed(self):
        with InstanceDir(regression=a_file(), pins=None) as inst_dir:
            code, out = self.main(inst_dir, "--check")
        self.assertEqual(code, 2, out)
        self.assertIn("no pinned id list", out)

    def test_the_demo_and_the_template_pin_every_entry(self):
        for inst_dir in (DEMO_DIR, ROOT / "instances" / "_template"):
            with self.subTest(instance=inst_dir.name):
                path = config.regression_file_path(config.load(inst_dir / "instance.yaml"))
                data, _ = evaluator.read_file(path)
                self.assertEqual(sorted(evaluator.check_pins(path, data)), sorted(e["id"] for e in data["entries"]))


class TestCommandWiringTests(unittest.TestCase):
    """`groundworks test` RUNS the evaluator and the mutation lint, and a skipped evaluator is never a pass.
    Behaviour, not text: the commands are captured, not grepped for (D8)."""

    def run_test(self, args, codes):
        calls = []

        def stream(cmd):
            calls.append(cmd)
            joined = " ".join(map(str, cmd))
            for key, (code, out) in codes.items():
                if key in joined:
                    return code, out
            return 0, "Ran 5 tests in 0.1s\n\nOK\n"

        with mock.patch.object(cli, "_stream", side_effect=stream), \
                mock.patch.object(cli, "_module", return_value=0), mock.patch("builtins.print"):
            rc = cli._test(args)
        return rc, calls

    def test_the_evaluator_and_the_mutation_lint_are_run(self):
        rc, calls = self.run_test([], {})
        joined = [" ".join(map(str, c)) for c in calls]
        self.assertTrue(any("groundworks.evaluator" in c and "instances/demo-incident" in c for c in joined), joined)
        self.assertTrue(any("tests.harness.mutate lint" in c for c in joined), joined)
        self.assertEqual(rc, 0)

    def test_an_evaluator_that_could_not_run_fails_under_require_db_and_is_named_otherwise(self):
        codes = {"groundworks.evaluator": (2, "EVALUATOR  NOT EXECUTED: no server\n")}
        self.assertEqual(self.run_test(["--require-db"], codes)[0], 1)
        self.assertEqual(self.run_test([], codes)[0], 0)

    def test_a_regressed_known_error_or_a_stale_mutation_anchor_fails_the_run(self):
        self.assertEqual(self.run_test([], {"groundworks.evaluator": (1, "EVALUATOR ... 1 REGRESSED\n")})[0], 1)
        self.assertEqual(self.run_test([], {"tests.harness.mutate": (1, "MUTATION LINT ... 1 problem(s)\n")})[0], 1)


if __name__ == "__main__":
    unittest.main()
