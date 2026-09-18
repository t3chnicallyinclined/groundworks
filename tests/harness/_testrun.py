# -*- coding: utf-8 -*-
"""Run named tests inside a tree copy and write one outcome per test as JSON. Used by mutate.py.

    python _testrun.py TREE_ROOT OUT.json tests.test_x.Class.test_method [...]

Outcomes: pass | fail | skip | error:<ExceptionType> | import:<ExceptionType> | missing | not_run

Why a reporter and not the text "OK": unittest prints OK over skipped tests, and the hand scripts this
replaces read any non-zero exit as "red", so a mutant that stopped the module importing counted as a
catch. Here an import failure is its own outcome, and a skip is never a pass.

The tree root goes FIRST on sys.path, and every module that ran is checked to have been imported from
inside that root. Measured on the development machine: groundworks is installed editable, so
`import groundworks` from any other directory resolves to the LIVE tree -- a mutant in the copy would never
be imported and every mutation would "survive" against unmutated code.
"""
import importlib
import json
import pathlib
import sys
import traceback
import unittest


def _outcome_type(err):
    return err[0].__name__ if err and err[0] else "Exception"


class JsonResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.out = {}
        self.detail = {}
        self.class_level = []          # (description, outcome, detail) from setUpClass / module fixtures

    def _set(self, test, outcome, detail=""):
        tid = test.id()
        if tid.startswith("setUpClass") or tid.startswith("setUpModule") or \
                type(test).__name__ == "_ErrorHolder":
            self.class_level.append((str(test), outcome, detail))
            return
        # a sub-test failure marks its parent; never let a later success overwrite a failure
        if self.out.get(tid, "").startswith(("fail", "error")):
            return
        self.out[tid] = outcome
        if detail:
            self.detail[tid] = detail

    def addSuccess(self, test):
        self._set(test, "pass")

    def addFailure(self, test, err):
        self._set(test, "fail", _last_line(err))

    def addError(self, test, err):
        self._set(test, "error:" + _outcome_type(err), _last_line(err))

    def addSkip(self, test, reason):
        self._set(test, "skip", str(reason)[:500])

    def addSubTest(self, test, subtest, err):
        if err is None:
            return
        parent = test
        if issubclass(err[0], test.failureException):
            self._set(parent, "fail", _last_line(err))
        else:
            self._set(parent, "error:" + _outcome_type(err), _last_line(err))

    def addExpectedFailure(self, test, err):
        self._set(test, "pass")

    def addUnexpectedSuccess(self, test):
        self._set(test, "fail", "unexpected success")


def _last_line(err):
    text = "".join(traceback.format_exception_only(err[0], err[1])).strip()
    return text.splitlines()[-1][:800] if text else ""


def main(argv):
    root = pathlib.Path(argv[0]).resolve()
    out_path = pathlib.Path(argv[1])
    ids = argv[2:]
    sys.path.insert(0, str(root))
    report = {"root": str(root), "tests": {}, "detail": {}, "imported_outside_root": []}

    by_class = {}
    for tid in ids:
        module_name, _, rest = tid.rpartition(".")
        module_name, _, class_name = module_name.rpartition(".")
        try:
            module = importlib.import_module(module_name)
        except BaseException as exc:            # the module does not import: not a catch, its own outcome
            report["tests"][tid] = "import:" + type(exc).__name__
            report["detail"][tid] = ("%s: %s" % (type(exc).__name__, exc))[:800]
            continue
        cls = getattr(module, class_name, None)
        if cls is None or not hasattr(cls, rest):
            report["tests"][tid] = "missing"
            continue
        by_class.setdefault((module_name, class_name), []).append(cls(rest))

    for name in ["groundworks"] + sorted({m for m, _ in by_class}):
        module = sys.modules.get(name)
        where = getattr(module, "__file__", None)
        if module is not None and where and root not in pathlib.Path(where).resolve().parents:
            report["imported_outside_root"].append("%s from %s" % (name, where))

    result = JsonResult()
    if not report["imported_outside_root"]:
        # one top-level suite, so setUpClass/tearDownClass run once per class and every server a
        # suite starts is stopped by that suite's own tearDownClass
        unittest.TestSuite([unittest.TestSuite(cases) for _, cases in sorted(by_class.items())]).run(result)
    report["tests"].update(result.out)
    report["detail"].update(result.detail)
    report["class_level"] = result.class_level
    for tid in ids:
        if tid in report["tests"]:
            continue
        cls_path = tid.rsplit(".", 1)[0]
        level = [o for d, o, _ in result.class_level if "(%s)" % cls_path in d or d.endswith(cls_path)]
        report["tests"][tid] = level[0] if level else "not_run"
    out_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
