# -*- coding: utf-8 -*-
"""The MCP server's engine code guard and the health summary, with no datastore.

From real use (2026-09-16): a live server served write tools from a work-in-progress branch, and kept serving
that code after the checkout was switched back; and `health` answered with about 60 KB in which the number that
mattered was buried. Every test here was first run against a named deliberate break (tests/harness/mutations.json,
M200-M249) and failed.

    python -m unittest tests.test_mcp_guard -v
"""
import asyncio
import json
import os
import pathlib
import random
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks.mcp import adapter as contract  # noqa: E402
from groundworks.mcp import code_state, health_summary, server  # noqa: E402
try:  # discovery puts tests/ on the path; `python -m unittest tests.x` does not
    from test_mcp_server import FakeAdapter, INCIDENT, PROV, run_tool  # noqa: E402
except ImportError:  # pragma: no cover
    from tests.test_mcp_server import FakeAdapter, INCIDENT, PROV, run_tool  # noqa: E402

A, B = "a" * 40, "b" * 40
MAIN = code_state.VERIFIED_BRANCH


class Disk:
    """A checkout whose state the test moves, read by the guard exactly as it reads git."""

    def __init__(self, state):
        self.state = state
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return dict(self.state)


def write_calls():
    """One well-formed call per write tool (the args an agent would send)."""
    return {"critique_add": {"target": INCIDENT["target"], "lens": INCIDENT["lenses"][0], "head": "h",
                             "verdict": INCIDENT["blocking_verdict"]},
            "critique_answer": {"critique": "crit:x", "answer": "a"},
            "claim_propose": {"slug": "s", "claim": "c"},
            "claim_confirm": {"slug": "s", "unit_ids": ["u"]},
            "claim_rule_out": {"slug": "s", "claim": "c", "tried": "t", "evidence": "e"},
            "claim_support": {"slug": "s", "conclusion": "c"},
            "conclusion_mark_ready": {"conclusion": "c"},
            "panel_submit": {"run_dir": "nowhere", "round": 1, "seat": "seat_a", "text": "t"}}


class Recording(FakeAdapter):
    """Records every adapter method a tool reaches, so a refusal is shown to happen before the adapter."""

    def __init__(self, ds):
        super().__init__(ds)
        self.reached = []
        for name in ("critique_add", "critique_answer", "claim_propose", "claim_confirm", "claim_rule_out",
                     "claim_support", "conclusion_mark_ready"):
            real = getattr(self, name)
            setattr(self, name, self._record(name, real))

    def _record(self, name, real):
        def wrapper(*a, **k):
            self.reached.append(name)
            return real(*a, **k)
        return wrapper


class GuardTests(unittest.TestCase):
    def serve(self, disk, allow=False):
        self.adapter = Recording(INCIDENT)
        self.guard = code_state.CodeGuard(reader=disk, allow_unverified=allow)
        return server.build_server(self.adapter, PROV, guard=self.guard)

    def refused_everywhere(self, srv, *words):
        for tool, args in write_calls().items():
            with self.subTest(tool=tool):
                out = run_tool(srv, tool, args)
                self.assertEqual(out["status"], "rejected_by_gate", out)
                self.assertTrue(out["missing"] and all(m.strip() for m in out["missing"]), out)
                for word in words:
                    self.assertIn(word, out["error"] + " ".join(out["missing"]))
        self.assertEqual(self.adapter.reached, [], "a refused write reached the adapter")
        self.assertEqual(self.adapter.critiques, {})
        # reads keep working
        self.assertEqual(run_tool(srv, "unit_search", {"query": INCIDENT["hit"]})["status"], "ok")
        self.assertEqual(run_tool(srv, "health", {})["status"], "ok")

    def test_the_write_tools_are_exactly_the_tools_annotated_as_writes(self):
        srv = self.serve(Disk(code_state.git_state(A, MAIN)))
        annotated = {t.name for t in asyncio.run(srv.list_tools()) if not t.annotations.readOnlyHint}
        self.assertEqual(annotated, set(server.WRITE_TOOLS))
        self.assertEqual(set(write_calls()), set(server.WRITE_TOOLS))

    def test_a_verified_unchanged_commit_serves_writes(self):
        disk = Disk(code_state.git_state(A, MAIN))
        srv = self.serve(disk)
        out = run_tool(srv, "critique_add", write_calls()["critique_add"])
        self.assertEqual(out["status"], "ok", out)
        self.assertNotIn("engine_code_warning", out)
        self.assertGreaterEqual(disk.reads, 2, "the state must be re-read at the write, not only at startup")

    def test_a_branch_other_than_main_refuses_every_write_and_reads_still_work(self):
        self.refused_everywhere(self.serve(Disk(code_state.git_state(A, "wip/five-lanes"))),
                                "UNVERIFIED", "wip/five-lanes", "check out branch main")

    def test_uncommitted_changes_at_startup_refuse_every_write(self):
        self.refused_everywhere(self.serve(Disk(code_state.git_state(A, MAIN, ["M groundworks/gates.py"]))),
                                "uncommitted", "groundworks/gates.py", "commit or discard")

    def test_changes_made_after_startup_refuse_writes(self):
        disk = Disk(code_state.git_state(A, MAIN))
        srv = self.serve(disk)
        self.assertEqual(run_tool(srv, "claim_propose", write_calls()["claim_propose"])["status"], "invalid_argument")
        disk.state = code_state.git_state(A, MAIN, ["?? groundworks/new_module.py"])
        self.adapter.reached.clear()
        self.refused_everywhere(srv, "made after this server started", "groundworks/new_module.py")

    def test_the_real_case_a_server_started_on_a_branch_that_was_switched_back_is_stale(self):
        """Started on the WIP branch; the checkout was switched back to main. Main on disk is verified, the process
        is not: it still holds the WIP code. The refusal must say restart."""
        disk = Disk(code_state.git_state(B, "wip/five-lanes"))
        srv = self.serve(disk)
        disk.state = code_state.git_state(A, MAIN)
        self.refused_everywhere(srv, "STALE", "restart this MCP server")
        health = run_tool(srv, "health", {})["data"]["engine_code"]
        self.assertEqual(health["verdict"], "STALE")

    def test_a_new_commit_on_main_after_startup_is_stale(self):
        disk = Disk(code_state.git_state(A, MAIN))
        srv = self.serve(disk)
        disk.state = code_state.git_state(B, MAIN)
        self.refused_everywhere(srv, "STALE", A[:12], B[:12], "restart this MCP server")

    def test_a_branch_switch_at_the_same_commit_is_stale(self):
        disk = Disk(code_state.git_state(A, MAIN))
        srv = self.serve(disk)
        disk.state = code_state.git_state(A, "other")
        self.refused_everywhere(srv, "STALE", "restart this MCP server")

    def test_unknown_at_startup_is_never_verified(self):
        self.refused_everywhere(self.serve(Disk(code_state.unknown("/x", "not a readable git checkout: nope"))),
                                "UNKNOWN", "nope", code_state.ALLOW_ENV)

    def test_unknown_now_refuses(self):
        disk = Disk(code_state.git_state(A, MAIN))
        srv = self.serve(disk)
        disk.state = code_state.unknown("/x", "git is not installed or not on PATH")
        self.refused_everywhere(srv, "UNKNOWN", "git is not installed")

    def test_the_override_lets_writes_through_and_health_says_so_first(self):
        disk = Disk(code_state.git_state(B, "wip/five-lanes"))
        srv = self.serve(disk, allow=True)
        out = run_tool(srv, "critique_add", write_calls()["critique_add"])
        self.assertEqual(out["status"], "ok", out)
        self.assertIn("UNVERIFIED ENGINE CODE", out["engine_code_warning"])
        data = run_tool(srv, "health", {})["data"]
        self.assertEqual(list(data)[0], "WARNING")
        self.assertIn(code_state.ALLOW_ENV, data["WARNING"])
        self.assertIn("wip/five-lanes", data["WARNING"])
        self.assertTrue(data["engine_code"]["override"])
        self.assertIn("ALLOWED BY OVERRIDE", data["engine_code"]["writes"])
        self.assertTrue(data["summary"]["headline"].startswith("ATTENTION"))

    def test_the_override_is_read_from_the_environment_only_as_1(self):
        for value, expected in (("1", True), (" 1 ", True), ("true", False), ("0", False), ("", False)):
            self.assertEqual(code_state.allowed_from_env({code_state.ALLOW_ENV: value}), expected, value)
        self.assertFalse(code_state.allowed_from_env({}))

    def test_health_reports_the_engine_code_state_first_even_when_the_datastore_is_down(self):
        srv = self.serve(Disk(code_state.git_state(A, "wip")))
        data = run_tool(srv, "health", {})["data"]
        self.assertEqual(list(data)[:2], ["engine_code", "summary"])
        self.assertEqual((data["engine_code"]["verdict"], data["engine_code"]["writes"][:7]), ("UNVERIFIED", "REFUSED"))
        self.adapter.down = True
        out = run_tool(srv, "health", {})
        self.assertEqual(out["status"], "db_unreachable")
        self.assertEqual(out["engine_code"]["verdict"], "UNVERIFIED")

    def test_build_server_without_a_guard_reads_the_real_checkout(self):
        wip = code_state.git_state(A, "wip")
        with mock.patch.object(code_state, "read_state", lambda root=None: dict(wip)):
            srv = server.build_server(FakeAdapter(INCIDENT), PROV)
            out = run_tool(srv, "critique_add", write_calls()["critique_add"])
        self.assertEqual(out["status"], "rejected_by_gate", out)
        with mock.patch.object(code_state, "read_state", lambda root=None: code_state.git_state(A, MAIN)):
            srv = server.build_server(FakeAdapter(INCIDENT), PROV)
            self.assertEqual(run_tool(srv, "critique_add", write_calls()["critique_add"])["status"], "ok")


def git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=guard-test", "-c", "user.email=guard@test.invalid"]
                          + list(args), capture_output=True)
    if proc.returncode:
        raise AssertionError("git %s failed: %s" % (" ".join(args), proc.stderr.decode("utf-8", "replace")))
    return proc.stdout.decode("utf-8")


class RealGitTests(unittest.TestCase):
    """read_state against real throwaway repositories: git is required for this suite, never skipped."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        git(self.root, "init", "-q", "-b", MAIN)
        (self.root / "groundworks").mkdir()
        (self.root / "groundworks" / "engine.py").write_bytes(b"X = 1\n")
        (self.root / "README.md").write_bytes(b"readme\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "one")
        self.guard = code_state.CodeGuard(reader=lambda: code_state.read_state(self.root))

    def test_a_clean_main_checkout_is_verified_and_the_read_is_timed(self):
        state = code_state.read_state(self.root)
        self.assertEqual((state["kind"], state["branch"], state["dirty"]), ("git", MAIN, False), state)
        self.assertEqual(state["commit"], git(self.root, "rev-parse", "HEAD").strip())
        self.assertGreater(state["read_ms"], 0)
        self.assertEqual(self.guard.assess()["verdict"], "VERIFIED")
        self.guard.check_write("claim_confirm")

    def test_switching_branch_after_startup_makes_the_process_stale(self):
        git(self.root, "checkout", "-q", "-b", "wip")
        with self.assertRaises(contract.GateRejected) as err:
            self.guard.check_write("claim_confirm")
        self.assertIn("STALE", str(err.exception))
        self.assertIn("restart this MCP server so it loads the code that is on disk", err.exception.missing)

    def test_a_new_commit_after_startup_makes_the_process_stale(self):
        (self.root / "groundworks" / "engine.py").write_bytes(b"X = 2\n")
        git(self.root, "commit", "-q", "-am", "two")
        with self.assertRaises(contract.GateRejected) as err:
            self.guard.check_write("claim_confirm")
        self.assertIn("STALE", str(err.exception))

    def test_uncommitted_changes_count_tracked_anywhere_and_untracked_only_in_the_package(self):
        (self.root / "notes.txt").write_bytes(b"scratch\n")                       # untracked, outside the package
        self.assertFalse(code_state.read_state(self.root)["dirty"])
        (self.root / "groundworks" / "new.py").write_bytes(b"Y = 1\n")               # untracked, in the package
        state = code_state.read_state(self.root)
        self.assertEqual(state["dirty_files"], ["?? groundworks/new.py"])
        (self.root / "groundworks" / "new.py").unlink()
        (self.root / "README.md").write_bytes(b"changed\n")                       # tracked, outside the package
        self.assertEqual(code_state.read_state(self.root)["dirty_files"], ["M README.md"])
        with self.assertRaises(contract.GateRejected) as err:
            self.guard.check_write("claim_confirm")
        self.assertIn("README.md", str(err.exception))

    def test_a_staged_rename_is_listed_once_and_does_not_shift_the_next_entry(self):
        git(self.root, "mv", "README.md", "READ.md")
        (self.root / "groundworks" / "engine.py").write_bytes(b"X = 3\n")
        self.assertEqual(sorted(code_state.read_state(self.root)["dirty_files"]), ["M groundworks/engine.py", "R READ.md"])

    def test_a_directory_that_is_not_a_checkout_is_unknown_and_refused(self):
        plain = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, plain, True)
        state = code_state.read_state(plain)
        self.assertEqual(state["kind"], "UNKNOWN")
        self.assertIsNone(state["commit"])
        guard = code_state.CodeGuard(reader=lambda: code_state.read_state(plain))
        self.assertEqual(guard.assess()["verdict"], "UNKNOWN")
        with self.assertRaises(contract.GateRejected):
            guard.check_write("claim_confirm")

    def test_an_engine_inside_someone_elses_repository_does_not_borrow_its_history(self):
        state = code_state.read_state(self.root / "groundworks")
        self.assertEqual(state["kind"], "UNKNOWN", state)
        self.assertIn("not the top of a git checkout", state["reason"])

    def test_the_engine_root_is_the_directory_holding_the_package(self):
        self.assertTrue((code_state.engine_root() / "groundworks" / "mcp" / "code_state.py").is_file())


# ---- the health summary ----------------------------------------------------------------------------------

class SummaryTests(unittest.TestCase):
    VERIFIED = {"verdict": "VERIFIED", "writes": "allowed", "override": False}

    def test_a_clean_detail_says_nothing_needs_attention_and_still_lists_its_numbers(self):
        s = health_summary.summarize({"units": 12, "drift_x": 0, "gap": 0, "heads": ["h1", "h2"],
                                      "log": {"ok": True, "entries": 3}}, self.VERIFIED)
        self.assertTrue(s["headline"].startswith("nothing needs attention"), s)
        self.assertEqual(s["nonzero"], {"units": 12, "log.entries": 3})
        self.assertEqual(s["drift"], {"drift_x": 0})
        self.assertEqual(s["lists"], {"heads": {"count": 2, "names": ["h1", "h2"]}})
        self.assertEqual(s["zero_counts"], 1)

    def test_every_planted_problem_surfaces_at_any_depth(self):
        rnd = random.Random(20260916)
        for trial in range(60):
            detail, paths = {}, []

            def nest(depth):
                node = detail
                path = []
                for level in range(depth):
                    key = "k%d_%d" % (level, rnd.randint(0, 3))
                    node = node.setdefault(key, {}) if isinstance(node.get(key, {}), dict) else node
                    path.append(key)
                return node, ".".join(path)
            plants = [("key", {"NOT_EXECUTED": "cannot read"}), ("string", "NOT_EXECUTED: no permission"),
                      ("flag", {"ok": False}), ("drift", {"DRIFT_rows": 4}), ("count", {"late": 2})]
            kind, value = plants[trial % len(plants)]
            node, where = nest(rnd.randint(0, 4))
            for filler in range(rnd.randint(0, 6)):
                node.setdefault("filler_%d" % filler, rnd.choice([0, 1, "text", [1, 2], {"ok": True}]))
            leaf = "planted"
            node[leaf] = value
            s = health_summary.summarize(detail, self.VERIFIED)
            full = (where + "." if where else "") + leaf
            with self.subTest(trial=trial, kind=kind, path=full):
                if kind == "key":
                    self.assertIn(full + ".NOT_EXECUTED", [n["path"] for n in s["not_executed"]])
                elif kind == "string":
                    self.assertIn(full, [n["path"] for n in s["not_executed"]])
                elif kind == "flag":
                    self.assertIn(full + ".ok", [f["path"] for f in s["failing"]])
                elif kind == "drift":
                    self.assertEqual(s["drift"].get(full + ".DRIFT_rows"), 4)
                else:
                    self.assertEqual(s["nonzero"].get(full + ".late"), 2)
                if kind != "count":
                    self.assertTrue(s["headline"].startswith("ATTENTION"), s["headline"])

    def test_problems_inside_lists_still_surface(self):
        s = health_summary.summarize({"rules": [{"rule": "r1", "ok": True, "drift": 0},
                                                {"rule": "r2", "ok": False, "drift": 3, "why": "NOT EXECUTED: x"}]},
                                     self.VERIFIED)
        self.assertEqual([f["path"] for f in s["failing"]], ["rules[1].ok"])
        self.assertEqual(s["drift"], {"rules[1].drift": 3})
        self.assertEqual([n["path"] for n in s["not_executed"]], ["rules[1].why"])
        self.assertEqual(s["lists"]["rules"], {"count": 2, "names": ["r1", "r2"]})

    def test_an_engine_code_state_that_is_not_verified_is_the_first_thing_said(self):
        s = health_summary.summarize({"units": 1}, {"verdict": "STALE", "writes": "REFUSED: x", "override": False})
        self.assertTrue(s["headline"].startswith("ATTENTION (1): engine code STALE"), s["headline"])

    def test_detail_false_keeps_the_summary_and_drops_the_detail_and_true_is_the_default(self):
        adapter = FakeAdapter(INCIDENT)
        adapter.health = lambda: {"units": 3, "heads": ["head %d %s" % (i, "x" * 200) for i in range(300)],
                                  "live_schema": {"NOT_EXECUTED": "needs root"}}
        srv = server.build_server(adapter, PROV, guard=code_state.CodeGuard(
            reader=lambda: code_state.git_state(A, MAIN)))
        full = run_tool(srv, "health", {})
        short = run_tool(srv, "health", {"detail": False})
        self.assertEqual(full["data"]["instance"], adapter.health())
        self.assertEqual(full["data"]["summary"], short["data"]["summary"])
        self.assertIsInstance(short["data"]["instance"], str)
        self.assertLess(len(json.dumps(short)) * 10, len(json.dumps(full)))
        self.assertEqual(short["data"]["summary"]["lists"]["heads"]["count"], 300)
        self.assertIn("live_schema.NOT_EXECUTED", [n["path"] for n in short["data"]["summary"]["not_executed"]])


if __name__ == "__main__":
    unittest.main()
