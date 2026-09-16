# -*- coding: utf-8 -*-
"""run_instance.py -- drive an instance end to end and print what happened at every gate.

    python scripts/run_instance.py instances/demo-incident --memory 8025
    python scripts/run_instance.py INSTANCE_DIR --url http://127.0.0.1:8002/sql --ns NS --db DB

Steps: config -> schema and gates -> probes (both ways) -> intake -> chunk -> label -> validate
       -> load -> lens views and framework filings (matrix per node, row count per lens)
       -> access (per-agent PERMISSIONS, applied as root) -> the adapter's scenario through the typed
       write surface, each step under a provisioned agent credential, never root -> health.
Any failed check stops the run with a non-zero exit. Outputs go to INSTANCE_DIR/out/.
"""
import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import access  # noqa: E402
from xscholar import config  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar import gates  # noqa: E402
from xscholar import lens as lensmod  # noqa: E402
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
    if not SURREAL:
        raise SystemExit("NOT EXECUTED: " + surreal_bin.MISSING)
    # Check the port BEFORE starting. Without this, our own `surreal start` fails to bind, the health
    # poll below succeeds against whatever is already there, and the whole demo runs -- and reports
    # success -- against a stranger's database, leaving their server running at the end. Measured:
    # another session held 8025, the port this script's own README example uses.
    if surreal_bin.port_in_use(port):
        raise SystemExit(surreal_bin.occupied(port))
    proc = subprocess.Popen([str(SURREAL), "start", "--bind", "127.0.0.1:%d" % port, "--user", "root",
                             "--pass", "root", "memory"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        if proc.poll() is not None:
            raise SystemExit("NOT EXECUTED: throwaway SurrealDB on %d exited at once (exit %s); "
                             "something else may hold the port" % (port, proc.returncode))
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=1)
            return proc
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise SystemExit("NOT EXECUTED: throwaway SurrealDB did not start on %d" % port)


def say(step, detail=""):
    print("%-12s %s" % (step, detail))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_dir")
    parser.add_argument("--memory", type=int, help="start a throwaway in-memory SurrealDB on this port")
    parser.add_argument("--url", default=None)
    parser.add_argument("--ns")
    parser.add_argument("--db")
    parser.add_argument("--labels", help="use these already-written label files (for example from agents) instead of the stub")
    args = parser.parse_args(argv)
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
                        "learned_at and ready_history on conclusion")
        report = gates.run_probes(inst, conn)
        say("gates", "%d rule(s): %d rejected as required, %d accepted as required, %d misbehaved"
            % (len(report["rules"]), report["rejected_ok"], report["accepted_ok"], report["misbehaved"]))
        if report["misbehaved"]:
            print(json.dumps(report, indent=2))
            return 1

        if inst.access_roles():
            applied = conn.script(access.access_surql(inst))
            say("access", "%d role(s) -> PERMISSIONS on %d table(s), applied as root (%d statements)"
                % (len(inst.access_roles()), len(access.TABLES), len(applied)))
        else:
            say("access", "no access: section; every scenario step will run as root (nothing separates identities)")

        adapter = load_module(inst_dir / "adapter.py")
        counts = intake.write_units(adapter.Adapter(), out, inst)
        say("intake", "%d units kept (%s), %d excluded and logged"
            % (counts["kept"], ", ".join("%s %d" % kv for kv in counts["by_type"].items()), counts["excluded"]))

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
            rep = validator.validate(slice_path, labels_path, inst)
            failures += len(rep["errors"])
            for l in labels:
                tiers[l["tier"]] = tiers.get(l["tier"], 0) + 1
            for err in rep["errors"]:
                print("   validator: %s %s %s" % (err["unit"], err["field"], err["error"]))
        say("label", "%d labels by tier %s (%s)" % (sum(tiers.values()), dict(sorted(tiers.items())), source))
        say("validate", "%s" % ("PASS: every unit once, vocabulary ok, quotes verbatim" if not failures
                                 else "FAIL: %d error(s)" % failures))
        if failures:
            return 1

        counts = loader.load(out / "units.jsonl", labels_dir, conn, inst)
        say("load", "%d units, %d labels (grade agent), %d targets, %d bears_on edges, %d swept"
            % (counts["units"], counts["labels"], counts["targets"], counts["edges"], counts["swept"]))

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
            writers[role] = write.Writer(agent_db, inst, {"agent_id": agent_id, "role": role, "lens": lens})
        lines = []
        adapter.scenario(writers, lambda who, step, outcome: lines.append((who, step, outcome)))
        print("scenario     the typed write surface, with the datastore answering; [who did it]:")
        for who, step, outcome in lines:
            print("   - [%s] %s\n       %s" % (who, step, outcome))
        if any("would be a bug" in outcome for _, _, outcome in lines):
            print("SCENARIO FAILED: a write that must be refused was accepted")
            return 1

        health = {
            "units": conn.count("unit"), "labels": conn.count("label"),
            "facts": conn.sql("SELECT status, count() AS n FROM fact GROUP BY status")[0],
            "conclusions_ready": conn.sql("SELECT count() AS n FROM conclusion WHERE ready = true GROUP ALL")[0],
            "critiques_unanswered": conn.sql("SELECT count() AS n FROM critique WHERE answered = false GROUP ALL")[0],
            "temporal": temporal.history_count(conn, "fact"),   # facts whose status has changed since learned
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
        if proc:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
