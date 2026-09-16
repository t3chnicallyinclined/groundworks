# -*- coding: utf-8 -*-
"""The suite itself, checked for tests that do not run.

Measured here: `tests/test_gates.py` carried its `if __name__ == "__main__": unittest.main()` in the
middle of the file, above four classes. Run the documented way -- `python tests/test_gates.py`, which
its own docstring tells you to do -- it reported `Ran 7 tests ... OK` while silently never defining,
let alone running, the four written for the defect it exists to catch. `unittest discover` ran 11.
A suite that reports OK for a subset it does not name is the same defect class as a gate that
passes without doing its work.
"""
import ast
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent


def suites():
    return sorted(ROOT.glob("tests/test_*.py"))


class SuiteHygieneTests(unittest.TestCase):

    def test_running_a_suite_directly_runs_every_test_in_it(self):
        """unittest.main() collects what is defined when it runs, so anything below it is invisible."""
        for path in suites():
            body = ast.parse(path.read_text(encoding="utf-8")).body
            guards = [i for i, node in enumerate(body) if isinstance(node, ast.If)
                      and ast.dump(node.test).find("__main__") >= 0]
            with self.subTest(suite=path.name):
                self.assertTrue(guards, "%s cannot be run directly" % path.name)
                # the FIRST guard is the one that runs: collection stops where it sits
                after = [getattr(node, "name", type(node).__name__) for node in body[guards[0] + 1:]]
                self.assertEqual(after, [], "%s defines %s below its __main__ block, so running the "
                                           "file directly never sees them" % (path.name, ", ".join(after)))

    def test_every_suite_file_holds_at_least_one_test(self):
        """An import error or an empty file is a green run that measured nothing."""
        for path in suites():
            body = ast.parse(path.read_text(encoding="utf-8")).body
            cases = [n.name for n in body if isinstance(n, ast.ClassDef)
                     and any("TestCase" in ast.dump(base) for base in n.bases)]
            methods = [m.name for n in body if isinstance(n, ast.ClassDef) for m in n.body
                       if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test")]
            with self.subTest(suite=path.name):
                self.assertTrue(cases, "%s contains no TestCase" % path.name)
                self.assertTrue(methods, "%s contains no test method" % path.name)


if __name__ == "__main__":
    unittest.main()
