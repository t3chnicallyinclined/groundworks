# -*- coding: utf-8 -*-
"""conformance.py -- check a REAL instance adapter against the MCP contract (adapter.py), by calling it.

Why (reported from real use, 2026-09-16): an instance's adapter was written against an older contract. "It still
answers, but I don't know whether any tool silently degrades (e.g. a field it no longer fills)." A server test with
fake adapters cannot answer that; only calling the instance's own adapter can.

    python -m xscholar.mcp.conformance                              # the adapter named by XSCHOLAR_ADAPTER
    python -m xscholar.mcp.conformance --query "pool" --unit log_0043 --claim dns_failure
    python -m xscholar.mcp.conformance --write --scratch NS/DB      # ALSO the write methods; scratch databases only
    python -m xscholar.mcp.conformance --json report.json

What it does. Calls every contract method through the server's own envelope (server.call, so a bare `text` key or a
non-JSON value fails exactly as it would for an agent), and checks each result against SHAPES below: every
required field present, of the right type, and not empty where the contract needs a value; and each negative call
(an id that cannot exist, a lens that is not configured) answering the status the contract names (not_found,
invalid_argument), never an empty ok. Every problem is reported by method and field name.

READ-ONLY BY DEFAULT. It is meant to be pointed at live graphs. Without --write it calls only the read methods
(unit_get, unit_search, claim_explain, eliminated_claims, health, lens_view, document_get with include_content
false); tests/test_mcp_conformance.py proves on a throwaway datastore that a run leaves every row unchanged.
--write adds the write methods, and refuses unless --scratch names the adapter's own namespace/database exactly,
so a run cannot write by accident to a graph the operator did not name. Everything it writes has an id starting
`zz_conformance_`.

Verdicts per check: PASS; FAIL (with problems); UNSUPPORTED (an optional method the adapter reports as
unsupported with invalid_argument, which the contract allows); NOT_EXECUTED (no input to call it with -- give
--query/--unit/--claim, or it was a write without --write). The run's verdict: FAIL if anything failed; NOT_EXECUTED
if a READ check could not run; PASS_READ_ONLY if every read passed and the writes were deliberately not called (the
count of NOT_EXECUTED writes is printed next to it); PASS. Exit 0 for PASS and PASS_READ_ONLY, 1 FAIL, 2 NOT_EXECUTED. Standard library only.
"""
import argparse
import contextlib
import json
import os
import secrets
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.mcp import adapter as contract  # noqa: E402

UT = contract.UNTRUSTED_TEXT_KEY
ELIMINATED = "ELIMINATED CLAIM (false)"
PASS, FAIL, UNSUPPORTED, NOT_EXECUTED = "PASS", "FAIL", "UNSUPPORTED", "NOT_EXECUTED"
READ_ONLY_PASS = "PASS_READ_ONLY"      # every read check passed; the write methods were deliberately not called
PREFIX = "zz_conformance_"
READ_METHODS = ("health", "unit_search", "unit_get", "eliminated_claims", "claim_explain", "lens_view", "document_get")
WRITE_METHODS = ("claim_propose", "claim_confirm", "claim_rule_out", "claim_support", "conclusion_mark_ready",
                 "critique_add", "critique_answer")

# field -> (types, must be non-empty). Types are Python types; None in the tuple allows null.
STR, INT, BOOL, LIST, DICT = (str,), (int,), (bool,), (list,), (dict,)
SHAPES = {
    "unit_get": {"locator": (STR, True), "units": (LIST, True)},
    "unit_get.units[]": {"id": (STR, True), "locator": (DICT, False), UT: (STR, False), "status": (STR, True),
                         "grade": (STR, True), "labels": (LIST, False), "claims_citing": (LIST, False),
                         "eliminated_claims": (LIST, False)},
    "unit_search": {"query": (STR, True), "usable_only": (BOOL, False), "count": (INT, False), "units": (LIST, False),
                    "method": (STR, True)},
    "claim_explain": {"id": (STR, True), "kind": (STR, True), "status": (STR, True), "support": (DICT, False),
                      "adverse": (DICT, False), "critiques": (LIST, False), "blocking": (LIST, False),
                      "ready": (BOOL, False)},
    "eliminated_claims": {"about": ((str, type(None)), False), "count": (INT, False),
                          "eliminated_claims": (LIST, False), "failed_approaches": (LIST, False)},
    "eliminated_claims.eliminated_claims[]": {"id": (STR, True), "status": (STR, True), "display": (STR, True),
                                              "claim": (STR, True), "what_was_found": (STR, True)},
    "critique_add": {"critique": (STR, True), "target": (STR, True), "verdict": (STR, True), "by": (STR, True),
                     "blocking_now": (LIST, False)},
    "critique_answer": {"critique": (STR, True), "target": (STR, True), "by": (STR, True), "blocking_now": (LIST, False)},
    "claim_propose": {"fact": (STR, True), "status": (STR, True), "by": (STR, True), "next": (STR, True)},
    "lens_view": {"drift": (INT, False)},
    "document_get": {"document": (DICT, True), "unit": ((str, type(None)), False),
                     "locator": ((dict, type(None)), False), "content": (STR, True)},
    "document_get.document": {"id": (STR, True), "sha256": (STR, True), "media_type": (STR, True), "bytes": (INT, False),
                              "capture": (STR, True), "source_uri": ((str, type(None)), False),
                              "retrieved_at": ((str, type(None)), False), "original_name": ((str, type(None)), False),
                              "integrity": (STR, True), "units": (INT, False)},
}


class ScratchRefused(Exception):
    pass


def _type_name(types):
    return " or ".join("null" if t is type(None) else t.__name__ for t in types)


def check_shape(where, value, shape):
    """Problems, by field name, of one dict against one shape."""
    if not isinstance(value, dict):
        return ["%s: is %s, not an object" % (where, type(value).__name__)]
    problems = []
    for field, (types, nonempty) in shape.items():
        if field not in value:
            problems.append("%s.%s: missing" % (where, field))
            continue
        got = value[field]
        ok = isinstance(got, types) and not (isinstance(got, bool) and bool not in types)
        if not ok:
            problems.append("%s.%s: wrong type %s, contract says %s" % (where, field, type(got).__name__, _type_name(types)))
        elif nonempty and (got is None or (isinstance(got, (str, list, dict)) and not (got.strip() if isinstance(got, str) else got))):
            problems.append("%s.%s: empty" % (where, field))
    return problems


class Checker:
    def __init__(self, adapter, provenance=None, write=False, scratch=None, inputs=None):
        self.adapter = adapter
        self.prov = provenance or contract.Provenance("conformance_check", "conformance")
        self.inputs = dict(inputs or {})
        self.write = bool(write)
        self.checks = []
        self.tag = secrets.token_hex(4)
        if self.write:
            target = self.target()
            if not scratch or target is None or scratch != target:
                raise ScratchRefused(
                    "--write refused: it writes rows, and runs only against a scratch database named exactly with "
                    "--scratch NS/DB. This adapter's database is %s; --scratch was %r"
                    % (target or "not discoverable (no .db with ns and db): writes cannot be pointed at a scratch "
                                 "database", scratch))

    def target(self):
        db = getattr(self.adapter, "db", None)
        ns, name = getattr(db, "ns", None), getattr(db, "db", None)
        return "%s/%s" % (ns, name) if ns and name else None

    # ---- plumbing ------------------------------------------------------------------------------------------------

    def call(self, method, *args):
        from xscholar.mcp import server           # the real envelope, so a contract violation fails as it would live
        fn = getattr(self.adapter, method, None)
        if not callable(fn):
            return {"status": contract.STATUS_ERROR, "error": "adapter has no method %s" % method, "missing": []}
        with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):   # call() prints tracebacks for errors
            return server.call(method, lambda: fn(*args))

    def record(self, call, expected, envelope, problems, verdict=None):
        if verdict is None:
            verdict = FAIL if problems else PASS
        entry = {"call": call, "expected": expected, "status": envelope.get("status") if envelope else None,
                 "verdict": verdict, "problems": problems}
        if envelope and envelope.get("status") != contract.STATUS_OK and envelope.get("error"):
            entry["error"] = str(envelope["error"])[:300]
        self.checks.append(entry)
        return entry

    def skipped(self, call, why):
        return self.record(call, "-", None, [why], NOT_EXECUTED)

    def unsupported(self, envelope):
        return (envelope.get("status") == contract.STATUS_INVALID_ARGUMENT
                and "not supported" in str(envelope.get("error", "")))

    def expect_ok(self, call, method, args, shapes=(), extra=None):
        env = self.call(method, *args)
        if self.unsupported(env):
            self.record(call, "ok", env, [], UNSUPPORTED)
            return None
        if env.get("status") != contract.STATUS_OK:
            self.record(call, "ok", env, ["%s: status %s, contract says ok (%s)"
                                          % (call, env.get("status"), str(env.get("error", ""))[:200])])
            return None
        data = env["data"]
        problems = []
        for where, shape_name, value in shapes(data) if callable(shapes) else []:
            problems += check_shape(where, value, SHAPES[shape_name])
        if extra:
            problems += extra(data)
        self.record(call, "ok", env, problems)
        return data

    def expect_status(self, call, method, args, status, need_missing=False):
        env = self.call(method, *args)
        if self.unsupported(env):
            return self.record(call, status, env, [], UNSUPPORTED)
        problems = []
        if env.get("status") != status:
            problems.append("%s: status %s, contract says %s" % (call, env.get("status"), status))
        if env.get("status") == contract.STATUS_OK:
            problems.append("%s: an empty success where the contract needs a refusal" % call)
        if need_missing and not [m for m in env.get("missing") or [] if str(m).strip()]:
            problems.append("%s: `missing` is empty; a refusal must name what is missing" % call)
        return self.record(call, status + (" with missing" if need_missing else ""), env, problems)

    # ---- the read methods -----------------------------------------------------------------------------------------

    def run(self):
        missing_id = PREFIX + "no_such_" + self.tag
        health = self.expect_ok("health()", "health", (), extra=lambda d: [] if d else ["health(): empty object"])

        search_problems = lambda d: ([] if d.get("count") == len(d.get("units") or []) else
                                     ["unit_search: count %r but %d unit(s) listed" % (d.get("count"), len(d.get("units") or []))])
        units_of = lambda where: (lambda d: [("%s.units[%d]" % (where, i), where + ".units[]", u)
                                             for i, u in enumerate(d.get("units") or [])] if isinstance(d, dict) else [])
        unit_id = self.inputs.get("unit")
        queries = [self.inputs["query"]] if self.inputs.get("query") else ["e", "a", "the"]
        for query in queries:
            found = self.expect_ok("unit_search(%r, usable_only=False)" % query, "unit_search", (query, False, 5),
                                   shapes=lambda d: [("unit_search", "unit_search", d)],
                                   extra=search_problems)
            if found and found.get("units") and not unit_id:
                unit_id = found["units"][0].get("id")
            if found and found.get("units"):
                break
        self.expect_ok("unit_search(no match)", "unit_search", (PREFIX + "nomatch" + self.tag, False, 5),
                       shapes=lambda d: [("unit_search", "unit_search", d)],
                       extra=lambda d: search_problems(d) + ([] if d.get("count") == 0 else
                                                             ["unit_search(no match): count %r, expected 0" % d.get("count")]))

        if unit_id:
            self.expect_ok("unit_get(%r)" % unit_id, "unit_get", (str(unit_id),),
                           shapes=lambda d: [("unit_get", "unit_get", d)] + units_of("unit_get")(d))
            env = self.call("document_get", str(unit_id), False)
            if self.unsupported(env):
                self.record("document_get(%r, include_content=False)" % unit_id, "ok", env, [], UNSUPPORTED)
            elif env.get("status") == contract.STATUS_NOT_FOUND:
                self.record("document_get(%r, include_content=False)" % unit_id, "ok or not_found", env, [])
            else:
                self.expect_ok("document_get(%r, include_content=False)" % unit_id, "document_get", (str(unit_id), False),
                               shapes=lambda d: [("document_get", "document_get", d),
                                                 ("document_get.document", "document_get.document", d.get("document"))])
        else:
            self.skipped("unit_get(<a unit>)", "no unit id: pass --unit, or --query words some unit contains")
            self.skipped("document_get(<a unit>)", "no unit id: pass --unit")
        self.expect_status("unit_get(no such unit)", "unit_get", (missing_id,), contract.STATUS_NOT_FOUND)
        self.expect_status("document_get(no such unit)", "document_get", (missing_id, False), contract.STATUS_NOT_FOUND)

        elim_items = lambda d: [("eliminated_claims.eliminated_claims[%d]" % i, "eliminated_claims.eliminated_claims[]", c)
                                for i, c in enumerate(d.get("eliminated_claims") or [])] if isinstance(d, dict) else []
        display = lambda d: ["eliminated_claims.eliminated_claims[%d].display: %r, contract says %r" % (i, c.get("display"), ELIMINATED)
                             for i, c in enumerate(d.get("eliminated_claims") or [])
                             if isinstance(c, dict) and "display" in c and c.get("display") != ELIMINATED]
        count_matches = lambda d: ([] if d.get("count") == len(d.get("eliminated_claims") or []) else
                                   ["eliminated_claims: count %r but %d listed" % (d.get("count"), len(d.get("eliminated_claims") or []))])
        elim = self.expect_ok("eliminated_claims()", "eliminated_claims", (None,),
                              shapes=lambda d: [("eliminated_claims", "eliminated_claims", d)] + elim_items(d),
                              extra=lambda d: display(d) + count_matches(d))
        self.expect_ok("eliminated_claims(no match)", "eliminated_claims", (PREFIX + "nomatch" + self.tag,),
                       shapes=lambda d: [("eliminated_claims", "eliminated_claims", d)],
                       extra=lambda d: [] if d.get("count") == 0 else ["eliminated_claims(no match): count %r, expected 0" % d.get("count")])

        claim_ids = [c for c in (self.inputs.get("claim"), self.inputs.get("conclusion")) if c]
        if not claim_ids and elim:
            first = next((c for c in elim.get("eliminated_claims") or [] if isinstance(c, dict) and c.get("id")), None)
            claim_ids = [first["id"]] if first else []
        kinds = lambda d: [] if d.get("kind") in ("claim", "conclusion") else \
            ["claim_explain.kind: %r, contract says claim or conclusion" % d.get("kind")]
        for cid in claim_ids:
            self.expect_ok("claim_explain(%r)" % cid, "claim_explain", (str(cid),),
                           shapes=lambda d: [("claim_explain", "claim_explain", d)], extra=kinds)
        if not claim_ids:
            self.skipped("claim_explain(<a claim>)", "no claim id: pass --claim or --conclusion (no eliminated claim to borrow)")
        self.expect_status("claim_explain(no such claim)", "claim_explain", (missing_id,), contract.STATUS_NOT_FOUND)

        lenses = [l for l in (health or {}).get("lenses") or [] if isinstance(l, dict) and l.get("name")] \
            if isinstance(health, dict) else []
        if self.inputs.get("lens"):
            lenses = [{"name": self.inputs["lens"], "kind": None}]
        env = self.expect_status("lens_view(no such lens)", "lens_view", (missing_id, 5), contract.STATUS_INVALID_ARGUMENT)
        if lenses:
            lens = next((l for l in lenses if l.get("kind") != "framework"), lenses[0])
            shapes = (lambda d: [("lens_view", "lens_view", d)]) if lens.get("kind") != "framework" else ()
            self.expect_ok("lens_view(%r)" % lens["name"], "lens_view", (lens["name"], 5), shapes=shapes,
                           extra=lambda d: [] if d else ["lens_view: empty object"])
        elif env["verdict"] != UNSUPPORTED and "not supported" not in str(env.get("error", "")):
            self.skipped("lens_view(<a lens>)", "no lens name: health lists no `lenses` with names; pass --lens")

        if self.write:
            self.run_writes()
        else:
            for method in WRITE_METHODS:
                self.skipped("%s(...)" % method, "a write method: run with --write --scratch NS/DB against a scratch database")
        return self.report()

    # ---- the write methods (scratch only) ------------------------------------------------------------------------

    def run_writes(self):
        slug, gone = PREFIX + "fact_" + self.tag, PREFIX + "no_such_" + self.tag
        by = lambda d: [] if d.get("by") == self.prov.by() else ["by: %r, contract says the launch identity %r" % (d.get("by"), self.prov.by())]
        self.expect_ok("claim_propose(%r)" % slug, "claim_propose", (self.prov, slug, "a conformance check claim", "inference", None),
                       shapes=lambda d: [("claim_propose", "claim_propose", d)],
                       extra=lambda d: by(d) + ([] if d.get("status") == "proposed" else ["claim_propose.status: %r, contract says proposed" % d.get("status")]))
        self.expect_status("claim_confirm(%r, no units)" % slug, "claim_confirm", (self.prov, slug, [], None),
                           contract.STATUS_REJECTED_BY_GATE, need_missing=True)
        self.expect_ok("claim_rule_out", "claim_rule_out", (self.prov, PREFIX + "ruled_out_" + self.tag, "a conformance check",
                                                            "nothing", "nothing"), extra=lambda d: [] if d else ["claim_rule_out: empty object"])
        self.expect_status("claim_support(no such conclusion)", "claim_support", (self.prov, slug, gone), contract.STATUS_NOT_FOUND)
        self.expect_status("conclusion_mark_ready(no such conclusion)", "conclusion_mark_ready", (self.prov, gone),
                           contract.STATUS_NOT_FOUND)
        self.expect_status("critique_add(no such conclusion)", "critique_add",
                           (self.prov, gone, self.inputs.get("lens") or "conformance", "a head", "damaging", None),
                           contract.STATUS_NOT_FOUND)
        self.expect_status("critique_answer(no such critique)", "critique_answer", (self.prov, gone, "an answer"),
                           contract.STATUS_NOT_FOUND)

    def report(self):
        counts = {v: sum(1 for c in self.checks if c["verdict"] == v) for v in (PASS, FAIL, UNSUPPORTED, NOT_EXECUTED)}
        problems = [p for c in self.checks if c["verdict"] == FAIL for p in c["problems"]]
        reads_unexecuted = [c for c in self.checks if c["verdict"] == NOT_EXECUTED
                            and not any(c["call"].startswith(m + "(") for m in WRITE_METHODS)]
        return {"adapter": getattr(self.adapter, "name", type(self.adapter).__name__),
                "mode": "write (scratch %s)" % self.target() if self.write else "read-only",
                "counts": counts, "problems": problems, "checks": self.checks,
                "verdict": FAIL if counts[FAIL] else NOT_EXECUTED if reads_unexecuted else
                READ_ONLY_PASS if counts[NOT_EXECUTED] else PASS}


def lines(report):
    out = ["CONFORMANCE %s  adapter %s  %s" % (report["verdict"], report["adapter"], report["mode"])]
    for c in report["checks"]:
        out.append("  %-12s %s" % (c["verdict"], c["call"]))
        for p in c["problems"] if c["verdict"] in (FAIL, NOT_EXECUTED) else []:
            out.append("               - %s" % p)
    k = report["counts"]
    out.append("CONFORMANCE %s: %d pass, %d FAIL, %d unsupported, %d NOT_EXECUTED"
               % (report["verdict"], k[PASS], k[FAIL], k[UNSUPPORTED], k[NOT_EXECUTED]))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check an instance adapter against the MCP contract (read-only by default).")
    parser.add_argument("--adapter", default=os.environ.get("XSCHOLAR_ADAPTER"), help="as XSCHOLAR_ADAPTER")
    for name in ("query", "unit", "claim", "conclusion", "lens"):
        parser.add_argument("--" + name)
    parser.add_argument("--write", action="store_true", help="also call the write methods (scratch databases only)")
    parser.add_argument("--scratch", help="NS/DB of the scratch database --write may write to; must match the adapter's")
    parser.add_argument("--json", help="write the full report here")
    args = parser.parse_args(argv)
    from xscholar.mcp import server
    try:
        with contextlib.redirect_stdout(sys.stderr):
            adapter = server.load_adapter(args.adapter)
        checker = Checker(adapter, write=args.write, scratch=args.scratch,
                          inputs={k: getattr(args, k) for k in ("query", "unit", "claim", "conclusion", "lens")})
    except (server.ConfigError, ScratchRefused) as exc:
        print("CONFORMANCE NOT EXECUTED: %s" % exc, file=sys.stderr)
        return 2
    report = checker.run()
    if args.json:
        _pathlib.Path(args.json).write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8"))
    for line in lines(report):
        print(line)
    return {PASS: 0, READ_ONLY_PASS: 0, FAIL: 1, NOT_EXECUTED: 2}[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
