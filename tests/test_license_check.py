# -*- coding: utf-8 -*-
"""Tests for license_check.py, both ways.

    python -m unittest discover -s scripts -p "test_license_check.py" -v
"""
import contextlib
import io
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import license_check  # noqa: E402


def run(root):
    with contextlib.redirect_stdout(io.StringIO()):
        return license_check.main([str(root)])


class LicenseCheckTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "THIRD_PARTY_NOTICES.md").write_text("| MCP SDK, import `mcp` | MIT |\n", encoding="utf-8")
        (self.root / "helper.py").write_text("X = 1\n", encoding="utf-8")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_missing_notices_is_not_executed(self):
        (self.root / "THIRD_PARTY_NOTICES.md").unlink()
        self.assertEqual(run(self.root), 2)

    def test_unlisted_import_fails_then_listing_it_passes(self):
        self.write("engine/a.py", "import json\nimport helper\nimport mcp.server\nimport requests\n")
        self.assertEqual(run(self.root), 1)
        (self.root / "THIRD_PARTY_NOTICES.md").write_text(
            "| MCP SDK, import `mcp` | MIT |\n| Requests, import `requests` | Apache-2.0 |\n", encoding="utf-8")
        self.assertEqual(run(self.root), 0)

    def test_banned_model_in_config_fails_but_markdown_may_name_it(self):
        self.write("docs/policy.md", "Never use jina-embeddings-v3.\n")
        self.assertEqual(run(self.root), 0)
        self.write("instance.yaml", "embedding:\n  model: jinaai/jina-embeddings-v3\n")
        self.assertEqual(run(self.root), 1)
        self.write("instance.yaml", "embedding:\n  model: Qwen/Qwen3-Embedding-0.6B\n")
        self.assertEqual(run(self.root), 0)


def run_out(root):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = license_check.main([str(root)])
    return code, out.getvalue()


class ScannedNothingTests(unittest.TestCase):
    """A check that scanned nothing is NOT EXECUTED, never clean; and every run says how much it scanned.
    Measured 2026-09-16 before the fix: an empty notices file and no .py file printed
    `license check clean: 0 problem(s)` and exited 0."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "THIRD_PARTY_NOTICES.md").write_text("", encoding="utf-8")

    def test_a_tree_with_no_python_file_is_not_executed_and_a_run_prints_its_counts(self):
        (self.root / "config.yaml").write_text("a: 1\n", encoding="utf-8")
        code, out = run_out(self.root)
        self.assertEqual(code, 2, out)
        self.assertIn("NOT EXECUTED", out)
        self.assertNotIn("license check clean", out)
        (self.root / "a.py").write_text("import json\n", encoding="utf-8")
        code, out = run_out(self.root)
        self.assertEqual(code, 0, out)
        self.assertIn("clean: 1 python file(s), 1 import(s), 0 third-party", out)
        self.assertIn("2 config file(s)", out)   # config.yaml and a.py (.py is scanned for banned items too)


class AllowlistTests(unittest.TestCase):
    """What allows an import: a notices entry written `import `name``, or a module Python would really load
    for that file. Before the fix any backticked token allowed its name, and any *.py stem anywhere in the
    tree allowed its name everywhere (measured: `scripts/yaml.py` let `engine/a.py` import yaml, exit 0)."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.notices("| MCP SDK, import `mcp` | MIT | see `docs/LICENSING.md` and `yaml` |\n")

    def notices(self, text):
        (self.root / "THIRD_PARTY_NOTICES.md").write_text(text, encoding="utf-8")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_a_backticked_token_that_is_not_an_import_entry_allows_nothing(self):
        self.write("engine/a.py", "import mcp\nimport yaml\n")
        code, out = run_out(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("import 'yaml' is not listed", out)
        self.assertNotIn("import 'mcp'", out)
        self.notices("| MCP SDK, import `mcp` | MIT |\n| PyYAML, import `yaml` | MIT |\n")
        self.assertEqual(run_out(self.root)[0], 0)

    def test_a_module_elsewhere_in_the_tree_does_not_allow_a_third_party_import(self):
        self.write("scripts/yaml.py", "X = 1\n")
        self.write("engine/a.py", "import yaml\n")
        code, out = run_out(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("engine", out)
        self.assertIn("import 'yaml' is not listed", out)
        # the same import beside its module really is local: Python loads scripts/yaml.py for scripts/b.py
        (self.root / "engine" / "a.py").unlink()
        self.write("scripts/b.py", "import yaml\n")
        self.assertEqual(run_out(self.root)[0], 0)

    def test_a_directory_a_file_puts_on_sys_path_is_local_for_that_file_only(self):
        self.write("tools/helper_mod.py", "X = 1\n")
        self.write("tests/t.py", "import pathlib, sys\n"
                                 "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / \"tools\"))\n"
                                 "import helper_mod\n")
        self.assertEqual(run_out(self.root)[0], 0, run_out(self.root)[1])
        self.write("tests/u.py", "import helper_mod\n")
        code, out = run_out(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("u.py:1: import 'helper_mod'", out)


if __name__ == "__main__":
    unittest.main()
