# -*- coding: utf-8 -*-
"""The adapter conformance checker (groundworks/mcp/conformance.py): it must catch a degraded adapter by name, and it
must never write unless told to, against a database named as scratch.

Fake adapters need no datastore; the real generic adapter runs on a throwaway in-memory SurrealDB (never a live
one), where a read-only run is shown to leave every row unchanged. Every test here was first run against a named
deliberate break (tests/harness/mutations.json) and failed.

    python -m unittest discover -s tests -p "test_mcp_conformance.py" -v
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.request

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks import config, gates, lens, originals, surreal_bin, temporal  # noqa: E402
from groundworks import db as dbmod  # noqa: E402
from groundworks import store as storemod  # noqa: E402
from groundworks.mcp import adapter as contract  # noqa: E402
from groundworks.mcp import conformance, instance_adapter  # noqa: E402
from groundworks.pipeline import intake, loader, write  # noqa: E402
try:  # discovery puts tests/ on the path; `python -m unittest tests.x` does not
    from test_mcp_server import FakeAdapter, INCIDENT  # noqa: E402
    from test_originals_db import DEMO_ADAPTER  # noqa: E402
except ImportError:  # pragma: no cover
    from tests.test_mcp_server import FakeAdapter, INCIDENT  # noqa: E402
    from tests.test_originals_db import DEMO_ADAPTER  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
INPUTS = {"query": "pool exhausted", "claim": "rc:pool_undersized"}


def check(adapter, **kw):
    kw.setdefault("inputs", INPUTS)
    return conformance.Checker(adapter, **kw).run()


def verdicts(report):
    return {c["call"]: c["verdict"] for c in report["checks"]}


class DroppedGrade(FakeAdapter):
    """Still answers; silently no longer fills a field."""

    def unit_get(self, locator):
        out = super().unit_get(locator)
        for unit in out["units"]:
            unit.pop("grade")
        return out


class EmptiedAndMistyped(FakeAdapter):
    def eliminated_claims(self, about=None):
        out = super().eliminated_claims(about)
        for claim in out["eliminated_claims"]:
            claim["what_was_found"] = "  "
        return out

    def unit_search(self, query, usable_only=True, limit=20):
        out = super().unit_search(query, usable_only, limit)
        out["count"] = str(out["count"])
        return out


class WrongStatuses(FakeAdapter):
    """An unknown claim is an empty success; an unknown unit is 'invalid' instead of 'not found'."""

    def claim_explain(self, claim_id):
        if claim_id not in self.claims:
            return {}
        return super().claim_explain(claim_id)

    def unit_get(self, locator):
        if locator.startswith(conformance.PREFIX):
            raise contract.InvalidArgument("cannot parse %r" % locator)
        return super().unit_get(locator)


class MiscountedSearch(FakeAdapter):
    def unit_search(self, query, usable_only=True, limit=20):
        out = super().unit_search(query, usable_only, limit)
        out["count"] = len(out["units"]) + 1
        return out


class BareText(FakeAdapter):
    def unit_get(self, locator):
        out = super().unit_get(locator)
        out["units"][0]["text"] = out["units"][0][contract.UNTRUSTED_TEXT_KEY]
        return out


class NothingToRead(FakeAdapter):
    def unit_search(self, query, usable_only=True, limit=20):
        return {"query": query, "usable_only": usable_only, "count": 0, "units": [], "method": "none"}


class WriteTrap(FakeAdapter):
    def __init__(self, ds):
        super().__init__(ds)
        self.writes = []
        for name in conformance.WRITE_METHODS:
            setattr(self, name, self._trap(name))

    def _trap(self, name):
        def trapped(*a, **k):
            self.writes.append(name)
            raise AssertionError("the checker called write method %s" % name)
        return trapped


class FakeAdapterTests(unittest.TestCase):
    def test_a_conforming_adapter_passes_every_read_check(self):
        report = check(FakeAdapter(INCIDENT))
        self.assertEqual(report["counts"]["FAIL"], 0, report["problems"])
        self.assertEqual(report["verdict"], conformance.READ_ONLY_PASS)
        v = verdicts(report)
        for call in ("health()", "unit_get('log:api3_1207')", "unit_get(no such unit)", "eliminated_claims()",
                     "claim_explain('rc:pool_undersized')", "claim_explain(no such claim)"):
            self.assertEqual(v[call], "PASS", call)
        self.assertEqual(v["document_get(no such unit)"], "UNSUPPORTED")

    def test_a_field_the_adapter_silently_stopped_filling_is_reported_by_name(self):
        report = check(DroppedGrade(INCIDENT))
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("unit_get.units[0].grade: missing", report["problems"])

    def test_an_emptied_field_and_a_wrong_type_are_reported_by_name(self):
        report = check(EmptiedAndMistyped(INCIDENT))
        self.assertIn("eliminated_claims.eliminated_claims[0].what_was_found: empty", report["problems"])
        self.assertIn("unit_search.count: wrong type str, contract says int", report["problems"])

    def test_a_wrong_status_is_reported_by_call(self):
        report = check(WrongStatuses(INCIDENT))
        self.assertIn("claim_explain(no such claim): status ok, contract says not_found", report["problems"])
        self.assertIn("unit_get(no such unit): status invalid_argument, contract says not_found", report["problems"])

    def test_a_count_that_disagrees_with_its_list_is_reported(self):
        report = check(MiscountedSearch(INCIDENT))
        self.assertTrue(any(re.match(r"unit_search: count \d+ but \d+ unit\(s\) listed", p) for p in report["problems"]),
                        report["problems"])

    def test_results_go_through_the_real_envelope(self):
        report = check(BareText(INCIDENT))
        self.assertTrue(any("status error" in p and "unit_get" in p for p in report["problems"]), report["problems"])

    def test_a_read_it_had_nothing_to_call_with_is_not_executed_never_a_pass(self):
        report = check(NothingToRead(INCIDENT), inputs={})
        self.assertEqual(verdicts(report)["unit_get(<a unit>)"], "NOT_EXECUTED")
        self.assertEqual(report["verdict"], "NOT_EXECUTED")

    def test_read_only_by_default_calls_no_write_method(self):
        adapter = WriteTrap(INCIDENT)
        report = check(adapter)
        self.assertEqual(adapter.writes, [])
        for method in conformance.WRITE_METHODS:
            self.assertEqual(verdicts(report)["%s(...)" % method], "NOT_EXECUTED")

    def test_write_mode_is_refused_unless_scratch_names_the_adapters_own_database(self):
        adapter = WriteTrap(INCIDENT)
        with self.assertRaises(conformance.ScratchRefused) as refused:
            conformance.Checker(adapter, write=True, scratch="any/thing")         # no discoverable database
        # the refusal names exactly what to add (asked for by an adapter author who hit it, 2026-09-16)
        self.assertIn("adapter.db.ns and adapter.db.db", str(refused.exception))
        adapter.db = dbmod.Db(url="http://127.0.0.1:9/sql", ns="live_ns", db="live_db")
        for scratch in (None, "", "live_ns/other", "other/live_db"):
            with self.subTest(scratch=scratch):
                with self.assertRaises(conformance.ScratchRefused):
                    conformance.Checker(adapter, write=True, scratch=scratch)
        conformance.Checker(adapter, write=True, scratch="live_ns/live_db")
        self.assertEqual(adapter.writes, [])

    def test_the_contract_says_a_bare_unit_id_is_parseable(self):
        """An adapter author read "InvalidArgument when the locator cannot be parsed" as covering a bare id with no
        table prefix, and conformance (which asks for one) failed it. The contract now says so in words."""
        doc = contract.InstanceAdapter.unit_get.__doc__
        self.assertIn("IS parseable", doc)
        self.assertIn("NotFound, never InvalidArgument", doc)

    def test_every_shape_field_is_named_in_the_contract_docstring(self):
        """The checker's shapes and adapter.py's docstrings cannot drift apart silently."""
        for name, shape in conformance.SHAPES.items():
            method = name.split(".")[0]
            doc = getattr(contract.InstanceAdapter, method).__doc__ or ""
            for field in shape:
                word = "UNTRUSTED_TEXT_KEY" if field == contract.UNTRUSTED_TEXT_KEY else field
                with self.subTest(method=method, field=field):
                    self.assertRegex(doc, r"\b%s\b" % re.escape(word))

    def test_the_command_line_refuses_a_missing_adapter_as_not_executed(self):
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(conformance.main(["--adapter", ""]), 2)
        self.assertIn("NOT EXECUTED", err.getvalue())


# ---- the real generic adapter on a throwaway datastore ---------------------------------------------------------

SURREAL = surreal_bin.find()
PORT = surreal_bin.free_port()   # OS-assigned, like every suite's port


class GenericAdapterConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SURREAL:
            raise unittest.SkipTest("NOT EXECUTED: " + surreal_bin.MISSING)
        if surreal_bin.port_in_use(PORT):
            raise unittest.SkipTest(surreal_bin.occupied(PORT))
        cls.proc = subprocess.Popen([str(SURREAL), "start", "--bind", "127.0.0.1:%d" % PORT, "--user", "root",
                                     "--pass", "root", "memory"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        else:
            cls.proc.kill()
            raise unittest.SkipTest("NOT EXECUTED: throwaway SurrealDB did not start")
        cls.inst = config.load(DEMO)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()          # this process, by its handle: never by name
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def setUp(self):
        self.db = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="conf", db="d%d" % int(time.time() * 1000))
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        store = storemod.FileStore(self.dir / "originals")
        for script in (gates.schema_surql, gates.gate_surql, temporal.temporal_surql):
            self.db.script(script(self.inst))
        intake.write_units(DEMO_ADAPTER.Adapter(), self.dir, self.inst, store=store)
        (self.dir / "labels").mkdir()
        (self.dir / "labels" / "log_line_0000.jsonl").write_bytes(
            b'{"id": "log_0043", "tier": 3, "dir": "for_goal", "issues": ["saturation"], "targets": ["rc_pool_exhaustion"], '
            b'"role": "error", "reason": "the pool is exhausted", "quote": "pool exhausted"}\n')
        loader.load(self.dir / "units.jsonl", self.dir / "labels", self.db, self.inst, store=store)
        self.db.script(originals.enforcement_surql(self.inst))
        lens.apply(self.db, self.inst)
        writer = write.Writer(self.db, self.inst, {"agent_id": "seed", "role": "reader", "lens": None})
        writer.propose("pool_exhausted", "The pool was exhausted", reasoning_type="direct_quote")
        writer.rule_out("dns_failure", "DNS failed", tried="every in-window line", evidence="no resolver errors")
        self.adapter = instance_adapter.create_adapter_for(self.inst, self.db, store=store)

    def snapshot(self):
        tables = sorted((self.db.sql("INFO FOR DB", expect=1)[0] or {}).get("tables") or {})
        self.assertIn("unit", tables)
        return {t: sorted(json.dumps(r, sort_keys=True) for r in (self.db.sql("SELECT * FROM %s" % t, expect=1)[0] or []))
                for t in tables}

    def test_a_read_only_run_calls_every_read_method_and_changes_no_row(self):
        before = self.snapshot()
        self.assertTrue(before["unit"] and before["fact"], "the fixture must hold rows, or unchanged proves nothing")
        report = check(self.adapter, inputs={"query": "pool exhausted", "claim": "pool_exhausted"})
        self.assertEqual(self.snapshot(), before)
        v = verdicts(report)
        not_run = [call for call, verdict in v.items() if verdict == "NOT_EXECUTED"]
        self.assertEqual(sorted(not_run), sorted("%s(...)" % m for m in conformance.WRITE_METHODS))
        for method in conformance.READ_METHODS:
            self.assertTrue(any(call.startswith(method + "(") and verdict in ("PASS", "FAIL") for call, verdict in v.items()),
                            "%s was never called: %s" % (method, v))

    def test_write_mode_against_the_named_scratch_database_runs_the_write_checks(self):
        scratch = "%s/%s" % (self.db.ns, self.db.db)
        report = check(self.adapter, write=True, scratch=scratch, inputs={"query": "pool exhausted"})
        v = verdicts(report)
        tag = next(c["call"] for c in report["checks"] if c["call"].startswith("claim_propose("))
        for call in (tag, "claim_support(no such conclusion)", "conclusion_mark_ready(no such conclusion)",
                     "critique_add(no such conclusion)", "critique_answer(no such critique)", "claim_rule_out"):
            self.assertEqual(v[call], "PASS", (call, report["problems"]))
        confirm = next(c for c in report["checks"] if c["call"].startswith("claim_confirm("))
        self.assertEqual((confirm["verdict"], confirm["status"]), ("PASS", "rejected_by_gate"), confirm)
        written = self.db.sql("SELECT VALUE record::id(id) FROM fact WHERE string::starts_with(record::id(id), "
                              "'zz_conformance_')", expect=1)[0]
        self.assertEqual(len(written), 2, written)


if __name__ == "__main__":
    unittest.main()
