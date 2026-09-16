# -*- coding: utf-8 -*-
"""run_instance.py -- drive an instance end to end and print what happened at every gate.

    python scripts/run_instance.py instances/demo-incident --memory 8025
    python scripts/run_instance.py INSTANCE_DIR --url http://127.0.0.1:8002/sql --ns NS --db DB

Steps: config -> schema, gates, temporal, assertions -> probes (both ways) -> access (per-agent PERMISSIONS,
       applied as root) -> the probes again under access, so the blind-caller probe actually runs -> intake (originals into the instance's store)
       -> chunk -> label -> validate (quotes against unit text and against the original at the locator)
       -> load (document rows, then units linked to them) -> originals: enforcement applied and probed both
       ways, the store and graph verified -> lens views and framework filings (matrix per node, row count per lens)
       -> the adapter's scenario through the typed write surface, each step under a provisioned agent
       credential, never root -> the live schema compared with the config -> health.
Any failed check stops the run with a non-zero exit. Outputs go to INSTANCE_DIR/out/.
"""
import argparse
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import access  # noqa: E402
from xscholar import assertions  # noqa: E402
from xscholar import config  # noqa: E402
from xscholar import consistency  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar import gates  # noqa: E402
from xscholar import lens as lensmod  # noqa: E402
from xscholar import live_schema  # noqa: E402
from xscholar import originals  # noqa: E402
from xscholar import store as storemod  # noqa: E402
from xscholar import temporal  # noqa: E402
from xscholar.pipeline import intake  # noqa: E402
from xscholar.pipeline import chunker  # noqa: E402
from xscholar.pipeline import validator  # noqa: E402
from xscholar.pipeline import loader  # noqa: E402
from xscholar.pipeline import write  # noqa: E402

from xscholar import surreal_bin  # noqa: E402
SURREAL = surreal_bin.find()


def load_module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def start_memory(port):
    """A throwaway in-memory SurrealDB this run owns, or SystemExit saying NOT EXECUTED. The guards live in
    xscholar/surreal_bin.py (one implementation, shared with the evaluator); `SURREAL` is read at call time."""
    try:
        return surreal_bin.start_memory(port, binary=SURREAL)
    except surreal_bin.NotStarted as exc:
        raise SystemExit(str(exc))


def say(step, detail=""):
    print("%-12s %s" % (step, detail))


class Counted:
    """A Writer that keeps count of what was actually attempted and what the datastore refused.

    The walkthrough's verdict has to come from the engine, not from the adapter's prose: the adapter
    is the code under test, and it chooses every word the old check read.
    """

    def __init__(self, writer):
        self._writer = writer
        self.calls, self.refusals, self.denials = 0, 0, 0

    def __getattr__(self, name):
        attr = getattr(self._writer, name)
        if not callable(attr):
            return attr

        def counted(*args, **kwargs):
            self.calls += 1
            try:
                return attr(*args, **kwargs)
            except write.GateRefusal:
                self.refusals += 1
                raise
            except dbmod.PermissionDenied:
                self.denials += 1
                raise
        return counted

    @staticmethod
    def tally(writers):
        out = {"calls": 0, "refusals": 0, "denials": 0}
        for w in writers:
            for key in out:
                out[key] += getattr(w, key)
        return out


def scenario_problems(lines, tally, conn):
    """Everything that means the walkthrough did not actually happen. Empty list means it did."""
    problems = []
    if not lines:
        problems.append("the adapter's scenario reported no steps at all")
    if not tally["calls"]:
        problems.append("nothing went through the typed write surface; the scenario wrote nothing")
    if not tally["refusals"] and not tally["denials"]:
        problems.append("the datastore refused nothing. A walkthrough that is never refused has not "
                        "shown a gate working -- it has shown writes that happened to be allowed")
    if conn.count("fact") == 0:
        problems.append("no fact rows exist after the scenario; there is nothing for the gates to have "
                        "gated (this is the state that used to print 'done')")
    return problems


def health_consistency(report):
    """consistency.report() split into the two things it holds, for the health line: (rows whose own gate no longer
    holds, structured assertions that contradict their target). They are different findings with different cures,
    so an assertion count is never printed under DRIFT_gate_no_longer_holds."""
    drift = {k: v for k, v in report.items() if k not in ("rules", "assertions")}
    if "assertions" not in report:
        return drift, "none declared"
    return drift, {k: (len(v) if isinstance(v, list) else v) for k, v in report["assertions"].items()}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_dir")
    parser.add_argument("--memory", type=int, help="start a throwaway in-memory SurrealDB on this port")
    parser.add_argument("--url", default=None)
    parser.add_argument("--ns")
    parser.add_argument("--db")
    parser.add_argument("--labels", help="use these already-written label files (for example from agents) instead of the stub")
    parser.add_argument("--accept-source", action="append", default=[], metavar="UNIT.FIELD",
                        help="let the source replace a corrected unit status or grade; name each pair a human decided "
                             "(repeatable). Without it a reload keeps every correction and reports each disagreement")
    args = parser.parse_args(argv)
    accept_source = []
    for pair in args.accept_source:
        unit_id, _, field = pair.rpartition(".")
        if not unit_id or not field:
            parser.error("--accept-source takes UNIT.FIELD, got %r" % pair)
        accept_source.append((unit_id, field))
    inst_dir = pathlib.Path(args.instance_dir)
    inst = config.load(inst_dir / "instance.yaml")
    out = inst_dir / "out"
    out.mkdir(exist_ok=True)
    proc = None
    if args.memory:
        proc = start_memory(args.memory)
        url = "http://127.0.0.1:%d/sql" % args.memory
    else:
        url = args.url or "http://127.0.0.1:8002/sql"
    try:
        conn = dbmod.Db(url=url, ns=args.ns or inst.id, db=args.db or inst.id)
        conn.ping()
        say("instance", "%s: %s" % (inst.id, inst.goal))

        conn.script(gates.schema_surql(inst))
        conn.script(gates.gate_surql(inst))
        conn.script(temporal.temporal_surql(inst))
        say("temporal", "valid_from/valid_to, learned_at and an append-only status_history on fact; "
                        "learned_at and ready_history on conclusion; verdict_history on critique; text_history on "
                        "answer; status_history and grade_history on unit")
        conn.script(assertions.surql(inst))
        declared_assertions = assertions.declared(inst)
        say("assertions", "%d assertion edge(s) declared%s" % (len(declared_assertions), "".join(
            "; %s: %s asserts %s of %s, %s" % (edge, spec["from"], ", ".join(spec["fields"]), spec["to"],
                                               "ENFORCED" if spec["enforce"] else "report only")
            for edge, spec in declared_assertions.items()) or " (nothing for the contradiction check to count)"))
        report, where = gates.run_probes_where_they_can_run(inst, conn)
        say("gates", "%d rule(s): %d rejected as required, %d accepted as required, %d misbehaved%s"
            % (len(report["rules"]), report["rejected_ok"], report["accepted_ok"], report["misbehaved"], where))
        if report["misbehaved"]:
            print(json.dumps(report, indent=2))
            return 1

        if inst.access_roles():
            applied = conn.script(access.access_surql(inst))
            say("access", "%d role(s) -> PERMISSIONS on %d table(s), applied as root (%d statements)"
                % (len(inst.access_roles()), len(access.tables(inst)), len(applied)))
            # The probes above ran before any record credential existed, so every writer was a system user that
            # reads every row, and the blind-caller probe could only report "not needed on this database". Run
            # them again now that PERMISSIONS are in force: a caller who may write a gated table but cannot read
            # what its forbids clauses count is provisioned and must be refused (ROADMAP V2-21).
            report, where = gates.run_probes_where_they_can_run(inst, conn)
            say("gates", "again under access: %d rule(s): %d rejected as required, %d accepted as required, "
                         "%d misbehaved%s" % (len(report["rules"]), report["rejected_ok"], report["accepted_ok"],
                                              report["misbehaved"], where))
            for line in gates.blind_lines(report):
                print("   " + line)
            if report["misbehaved"]:
                print(json.dumps(report, indent=2))
                return 1
        else:
            say("access", "no access: section; every scenario step will run as root (nothing separates identities)")

        adapter = load_module(inst_dir / "adapter.py")
        store = storemod.for_instance(inst)
        counts = intake.write_units(adapter.Adapter(), out, inst, store=store)
        say("intake", "%d units kept (%s), %d excluded and logged"
            % (counts["kept"], ", ".join("%s %d" % kv for kv in counts["by_type"].items()), counts["excluded"]))
        mode = originals.mode(inst)
        say("originals", "mode %s; %d original(s) in %s (%d stored once for several keys, %d never captured); "
                         "%d unit(s) linked to a retrievable original, %d WITHOUT one%s"
            % (mode if mode != originals.UNDECLARED else "NOT DECLARED (treated as transition)", counts["documents"],
               store.describe(), counts["documents_deduplicated"], counts["documents_not_captured"],
               counts["units_with_original"], counts["units_without_original"],
               "; first: %s (all in %s)" % (", ".join(counts["without_original"]), out / "without_original.jsonl")
               if counts["units_without_original"] else ""))

        chunks = chunker.chunk(out / "units.jsonl", out, inst)
        say("chunk", "%d slice(s): %s" % (len(chunks), ", ".join("%s=%d" % (c["id"], c["count"]) for c in chunks)))

        if args.labels:
            labels_dir = pathlib.Path(args.labels)
            source = "labels supplied from %s" % labels_dir
        else:
            labeler_path = inst_dir / "labeler_stub.py"
            if not labeler_path.exists():
                print("no labels: pass --labels DIR with agent-written labels, or provide labeler_stub.py for a demo")
                return 1
            labeler = load_module(labeler_path)
            labels_dir = out / "labels"
            labels_dir.mkdir(exist_ok=True)
            source = "stand-in labeler, not agents"
        failures = 0
        tiers = {}
        against, not_checkable = 0, {}
        for chunk in chunks:
            slice_path = out / "slices" / (chunk["id"] + ".jsonl")
            labels_path = labels_dir / (chunk["id"] + ".jsonl")
            if not args.labels:
                labels = [labeler.label(u) for u in intake.read_units(slice_path)]
                labels_path.write_text("".join(json.dumps(l) + "\n" for l in labels), encoding="utf-8")
            if not labels_path.exists():
                print("   missing labels for %s at %s" % (chunk["id"], labels_path))
                return 1
            labels = intake.read_units(labels_path)
            rep = validator.validate(slice_path, labels_path, inst, store=store)
            failures += len(rep["errors"])
            against += rep["original_checked"]
            for reason, n in rep["original_not_checkable"].items():
                not_checkable[reason] = not_checkable.get(reason, 0) + n
            for l in labels:
                tiers[l["tier"]] = tiers.get(l["tier"], 0) + 1
            for err in rep["errors"]:
                print("   validator: %s %s %s" % (err["unit"], err["field"], err["error"]))
        say("label", "%d labels by tier %s (%s)" % (sum(tiers.values()), dict(sorted(tiers.items())), source))
        say("validate", "%s" % ("PASS: every unit once, vocabulary ok, quotes verbatim" if not failures
                                 else "FAIL: %d error(s)" % failures))
        say("", "%d quote(s) also found verbatim in the original at the unit's locator; not checkable against "
                "an original: %s" % (against, ", ".join("%d %s" % (n, r) for r, n in sorted(not_checkable.items()))
                                     or "none"))
        if failures:
            return 1

        counts = loader.load(out / "units.jsonl", labels_dir, conn, inst, accept_source=accept_source, store=store)
        say("load", "%d document(s), %d units (%d linked to a document), %d labels (grade agent), %d targets, "
                    "%d bears_on edges, %d swept"
            % (counts["documents"], counts["units"], counts["units_with_document"], counts["labels"],
               counts["targets"], counts["edges"], counts["swept"]))
        counts["assertions"] = assertions.after_load(conn, inst, counts)
        say("", "assertions after the load: %d counted, %d contradiction(s) standing, %d caused by this load, "
                "%d held-value conflict(s) annotated"
            % (counts["assertions"]["counted"], len(counts["assertions"]["standing"]),
               len(counts["assertions"]["caused_by_this_load"]), len(counts["assertions"]["conflicts"])))
        for row in counts["assertions"]["caused_by_this_load"]:
            print("   CONTRADICTION caused by this load: %s says %s.%s = %r, it holds %r (changed last: %s)"
                  % (row.get("edge"), row["target"], row["field"], row["asserted"], row["current"], row["changed_last"]))
        for row in counts["originals_not_ok"]:
            print("   ORIGINAL %s %s (%s): loaded, and no gated promotion can rest on it"
                  % (row["integrity"].upper(), row["id"], row["name"]))

        conn.script(originals.enforcement_surql(inst))
        oprobes = originals.run_probes(inst, conn)
        say("originals", "enforcement on %d rule(s): %d refused as required (no original; original not verified), "
                         "%d accepted as required (verified; verified again), %d misbehaved"
            % (len(oprobes["rules"]), oprobes["rejected_ok"], oprobes["accepted_ok"], oprobes["misbehaved"]))
        if oprobes["misbehaved"] or not oprobes["rules"]:
            print(json.dumps(oprobes, indent=2))
            return 1
        verified = originals.verify(conn, store, inst)
        for line in originals.verify_lines(verified):
            print(line)
        (out / "originals_verify.json").write_text(json.dumps(verified, indent=2, default=str), encoding="utf-8")
        if not verified["ok"]:
            print("ORIGINALS FAILED: see the lines above")
            return 1
        say("corrections", "%d unit field(s) followed a source change, %d held against the source, "
                           "%d CONFLICT(S) with the source, %d accepted from source by name"
            % (counts["followed"], counts["held"], counts["conflicts"], counts["accepted"]))
        for row in counts["conflict_rows"]:
            print("   CONFLICT unit %s %s: kept %r (%s, by %s); source says %r -- to take the source, "
                  "re-run with --accept-source %s.%s"
                  % (row["unit"], row["field"], row["corrected"], row["reason"], row["by"], row["source"],
                     row["unit"], row["field"]))

        lensmod.apply(conn, inst)
        filed = lensmod.build_framework(conn, inst)
        say("lenses", "%d lens(es) applied; %s" % (len(inst.lenses), ", ".join(
            "%s (%s) %d row(s)" % (name, inst.lens(name).get("kind"), n) for name, n in lensmod.view_counts(conn, inst).items()) or "no views"))
        say("framework", "%d label(s) with issues, %d filed, %d unfiled, %d falls_under edge(s), %d swept"
            % (filed["with_issues"], filed["filed"], filed["unfiled"], filed["edges"], filed["swept"]))
        for line in lensmod.matrix_lines(lensmod.matrix(conn, inst)):
            print("   " + line)

        identities = [("reader", "reader_1", None), ("seat", "seat_skeptic", "lens_skeptic"),
                      ("coordinator", "coordinator_1", None)]
        writers = {}
        for role, agent_id, lens in identities:
            if inst.access_roles():
                creds = access.provision(conn, inst, agent_id, role, lens=lens)
                agent_db = access.connect(creds)
                agent_db.ping()
                say("provision", "%s as %s%s -> XSCHOLAR_DB_USER=%s XSCHOLAR_DB_ACCESS=%s (signed in, not root)"
                    % (agent_id, role, " lens " + lens if lens else "", agent_id, creds["XSCHOLAR_DB_ACCESS"]))
            else:
                agent_db = conn
            writers[role] = Counted(write.Writer(agent_db, inst, {"agent_id": agent_id, "role": role, "lens": lens}))
        lines = []
        adapter.scenario(writers, lambda who, step, outcome: lines.append((who, step, outcome)))
        print("scenario     the typed write surface, with the datastore answering; [who did it]:")
        for who, step, outcome in lines:
            print("   - [%s] %s\n       %s" % (who, step, outcome))
        tally = Counted.tally(writers.values())
        say("scenario", "%d step(s) reported, %d call(s) through the typed write surface, "
                        "%d refused by a gate, %d refused by permissions"
            % (len(lines), tally["calls"], tally["refusals"], tally["denials"]))
        # The verdict used to be this substring alone -- a phrase chosen by the instance's own
        # adapter, in the code under test. An adapter that reported nothing gave `any([]) is False`,
        # and the run printed "done" over a graph with zero facts. Measured: exit 0, "facts": [].
        # So the checks below count what the ENGINE saw, which an adapter cannot talk its way out of.
        if any("would be a bug" in outcome for _, _, outcome in lines):
            print("SCENARIO FAILED: a write that must be refused was accepted")
            return 1
        for problem in scenario_problems(lines, tally, conn):
            print("SCENARIO NOT EXECUTED: %s" % problem)
            return 1

        cited = conn.sql("SELECT VALUE record::id(out) FROM cites WHERE in.status = 'confirmed' ORDER BY out LIMIT 1")[0]
        if cited:
            found = originals.original_for_unit(conn, cited[0], store)
            doc = found["document"]
            say("original", "unit %s, cited by a confirmed fact -> %s (%s, %s, %d bytes, sha256 %s..., re-hashed on read)"
                % (found["unit"], doc["original_name"], doc["media_type"], doc["capture"], len(found["bytes"]),
                   doc["sha256"][:16]))
            unit_type = conn.sql("SELECT VALUE type FROM unit WHERE record::id(id) = %s" % json.dumps(found["unit"]))[0][0]
            spec = originals.spans(inst).get(unit_type)
            if spec and originals.is_text(doc, found["bytes"]):
                print("   at %s: %s" % (json.dumps(found["locator"]),
                                         " ".join(originals.span_text(found["bytes"], found["locator"], spec).split())[:160]))

        # The live schema, after every apply step (schema, gates, temporal, assertions, access, the loader's
        # provenance, originals enforcement, lenses): each definition the database holds, compared by name with
        # what the config generates, rendered by this same server. A drift here is a generator that disagrees
        # with what was applied, or a hand edit; either way the run is not what the config says.
        components = live_schema.deployed_components(inst)
        try:
            drift = live_schema.check(inst, conn, components=components)
        except live_schema.LiveSchemaError as exc:
            print("LIVE SCHEMA NOT EXECUTED: %s" % exc)
            return 1
        for line in drift.lines():
            print("   " + line)
        say("live schema", "%d definition(s) expected, %d live, %d drift row(s) (%s)"
            % (drift.expected_count, drift.live_count, len(drift.rows), ", ".join(components)))
        if not drift.ok:
            print("LIVE SCHEMA DRIFT: the database does not hold what the config generates")
            return 1

        consistency_report = consistency.report(conn, inst)
        health = {
            "units": conn.count("unit"), "labels": conn.count("label"),
            "facts": conn.sql("SELECT status, count() AS n FROM fact GROUP BY status")[0],
            "conclusions_ready": conn.sql("SELECT count() AS n FROM conclusion WHERE ready = true GROUP ALL")[0],
            "critiques_unanswered": conn.sql("SELECT count() AS n FROM critique WHERE answered = false GROUP ALL")[0],
            "temporal": temporal.history_count(conn, "fact"),   # facts whose status has changed since learned
            # every gate rule, not only the one DRIFT_confirmed_without_valid_cite hard-codes: rows at a gated
            # value whose own gate no longer holds, next to how many rows stand at a gated value at all
            "DRIFT_gate_no_longer_holds": health_consistency(consistency_report)[0],
            "assertions": health_consistency(consistency_report)[1],
            "live_schema_drift": len(drift.rows),
            "load_conflicts": counts["conflicts"],
            "originals": originals.health(conn, inst),
            "DRIFT_confirmed_without_valid_cite": conn.sql(
                "SELECT count() AS n FROM fact WHERE status = 'confirmed' AND "
                "count(SELECT VALUE id FROM $parent->cites->unit WHERE grade IN %s AND status IN %s) = 0 GROUP ALL"
                % (gates._lit(inst.confirming_grades()), gates._lit(inst.usable_statuses())))[0],
        }
        (out / "health.json").write_text(json.dumps(health, indent=2, default=str), encoding="utf-8")
        say("health", json.dumps(health, default=str))
        say("done", "outputs in %s" % out)
        return 0
    finally:
        surreal_bin.stop(proc)


if __name__ == "__main__":
    sys.exit(main())
