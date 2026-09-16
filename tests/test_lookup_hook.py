# -*- coding: utf-8 -*-
"""The lookup hook is never silent when it did not consult the graph. No server.

Kept apart from tests/test_hooks.py, whose database fixtures import test_pipeline by a bare name, so that
module does not import under the mutation runner and a break registered against it could never be judged.

    python -m unittest tests.test_lookup_hook -v
"""
import json
import os
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from xscholar.hooks import lookup  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DEMO = HERE.parent / "instances" / "demo-incident" / "instance.yaml"


class LookupHookNeverSilentTests(unittest.TestCase):
    """No output from the lookup hook must mean "consulted, nothing relevant", never "did not look". Measured
    2026-09-16 before the fix: XSCHOLAR_INSTANCE unset, or stdin that is not JSON, exited 0 with no output."""

    GOOD = json.dumps({"tool_input": {"command": "cite unit:log_004"}})

    def test_an_unset_instance_warns_in_the_hook_output(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("XSCHOLAR_")}
        proc = subprocess.run([sys.executable, "-m", "xscholar.hooks.lookup"], input=self.GOOD, text=True,
                              capture_output=True, env=env, cwd=str(HERE.parent), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)          # advisory: never blocks
        self.assertTrue(proc.stdout.strip(), "the hook printed nothing: silence reads as 'nothing known'")
        out = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("did NOT consult the graph", out)
        self.assertIn("XSCHOLAR_INSTANCE is not set", out)

    def test_input_that_is_not_a_json_object_warns(self):
        for raw in ("not json", "[1, 2]", ""):
            with self.subTest(raw=raw):
                out = lookup.context_for(raw, {"XSCHOLAR_INSTANCE": str(DEMO)})
                self.assertIn("did NOT consult the graph", out)

    def test_a_database_that_does_not_answer_is_named_not_silent(self):
        out = lookup.context_for(self.GOOD, {"XSCHOLAR_INSTANCE": str(DEMO), "XSCHOLAR_DB_URL": "http://127.0.0.1:9/sql",
                                             "XSCHOLAR_HOOK_TIMEOUT": "1"})
        self.assertIn("could not run", out)


if __name__ == "__main__":
    unittest.main()
