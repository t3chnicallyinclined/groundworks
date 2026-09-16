# -*- coding: utf-8 -*-
"""Tests for db.py that need no server, plus the unreachable-server case.

    python -m unittest discover -s engine -p "test_db.py" -v
"""
import pathlib
import sys
import unittest

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import db as dbmod  # noqa: E402


class SplitTests(unittest.TestCase):
    def test_comments_never_become_statements(self):
        text = ("-- header; with a semicolon\n"
                "DEFINE TABLE a SCHEMAFULL;  -- trailing; comment\n"
                "CREATE a:1 SET s = 'semi;colon -- not a comment';\n")
        parts = [p.strip() for p in dbmod._split_statements(text) if p.strip()]
        self.assertEqual(parts, ["DEFINE TABLE a SCHEMAFULL", "CREATE a:1 SET s = 'semi;colon -- not a comment'"])

    def test_semicolons_inside_braces_and_quotes_do_not_split(self):
        text = "UPDATE x SET f = {a: 'b;c', n: [1;2]}; SELECT 1;"
        parts = [p.strip() for p in dbmod._split_statements(text) if p.strip()]
        self.assertEqual(len(parts), 2)


class UnreachableTests(unittest.TestCase):
    def test_unreachable_server_is_an_error_not_an_empty_result(self):
        conn = dbmod.Db(url="http://127.0.0.1:9/sql", ns="x", db="y", timeout=2)
        with self.assertRaises(dbmod.DbError) as err:
            conn.sql("RETURN 1")
        self.assertIn("cannot reach", str(err.exception))


if __name__ == "__main__":
    unittest.main()
