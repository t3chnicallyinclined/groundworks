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


if __name__ == "__main__":
    unittest.main()
