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
from groundworks import db as dbmod  # noqa: E402


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



class VersionTests(unittest.TestCase):
    """The version check that replaced the pinned container image.

    A container was a way of hoping the database never changes. A version is a fact the server will
    tell you, and this engine's behaviour is measured against 3.1, not assumed from documentation.
    No server needed here: `version()` is stubbed, which is the point -- the decision is pure.
    """

    def at(self, reported):
        conn = dbmod.Db(url="http://127.0.0.1:9/sql")
        conn.version = lambda: reported
        return conn

    def test_the_measured_series_is_accepted(self):
        self.assertIsNone(self.at("surrealdb-3.1.6+20260813.cfbaec4").version_problem())

    def test_another_major_series_is_refused_by_name(self):
        problem = self.at("surrealdb-4.0.0").version_problem()
        self.assertIsNotNone(problem)
        self.assertFalse(problem.startswith("NOTE:"), "a different major series is a refusal, not a note")
        for expected in ("4.0.0", "changefeeds", "re-measure"):
            self.assertIn(expected, problem)

    def test_another_minor_in_the_same_series_warns_rather_than_refusing(self):
        problem = self.at("surrealdb-3.4.0").version_problem()
        self.assertTrue(problem.startswith("NOTE:"), problem)
        self.assertIn("3.4.0", problem)

    def test_a_server_that_reports_no_version_is_a_problem_not_a_pass(self):
        problem = self.at("who knows").version_problem()
        self.assertIsNotNone(problem, "an unparseable version must never read as 'fine'")
        self.assertIn("does not contain one", problem)

    def test_ping_raises_on_a_wrong_major_and_can_be_asked_not_to_check(self):
        conn = self.at("surrealdb-4.0.0")
        conn.sql = lambda *a, **k: [[1]]
        with self.assertRaises(dbmod.DbError):
            conn.ping()
        self.assertTrue(conn.ping(check_version=False), "the check must be skippable for offline use")

    def test_the_version_url_is_derived_from_the_sql_url(self):
        asked = []
        # Port 9 (discard), never a real one: 8001-8003 are live databases on the machine this was
        # written on. `version` is stubbed so nothing connects today -- but a test naming a live
        # database is one careless edit away from reading or writing it, which is how a fixture
        # starts depending on live data.
        conn = dbmod.Db(url="http://127.0.0.1:9/sql")
        conn.version = lambda: asked.append(conn.url) or "surrealdb-3.1.6"
        conn.version_problem()
        self.assertEqual(asked, ["http://127.0.0.1:9/sql"])


if __name__ == "__main__":   # last, so running this file directly runs every class in it
    unittest.main()
