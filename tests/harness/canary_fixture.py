# -*- coding: utf-8 -*-
"""Fixture tests for the mutation runner's canaries (canaries.json). Not collected by the suite: the
file name does not start with test_. The runner copies this file into its tree copy and registers
known mutations against these tests, each with the verdict it MUST give.

Do not "fix" the weak tests here. They are weak on purpose: a runner that reports a mutation KILLED by
a test that asserts nothing is the defect the whole harness exists to catch.
"""
import unittest

from groundworks.pipeline.agreement import kappa


class CanaryTests(unittest.TestCase):

    def test_asserts_nothing(self):
        """Calls the code and checks nothing: every break of kappa must SURVIVE this test."""
        kappa([(0, 0)] * 20)

    def test_kappa_is_undefined_on_one_value(self):
        """A real check (D11): kappa over a single-valued sample is undefined, never 1.0."""
        self.assertIsNone(kappa([(0, 0)] * 20))

    def test_always_skips(self):
        raise unittest.SkipTest("a canary that never runs: any verdict over it is NOT EXECUTED")

    def test_already_failing(self):
        self.assertEqual(kappa([("a", "b"), ("b", "a")]), 1.0, "fails on the unmutated tree, on purpose")


if __name__ == "__main__":
    unittest.main()
