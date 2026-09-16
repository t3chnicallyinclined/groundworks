# -*- coding: utf-8 -*-
"""run_instance.py -- drive an instance end to end and print what happened at every gate.

    python scripts/run_instance.py instances/demo-incident --memory 8025
    python scripts/run_instance.py INSTANCE_DIR --url http://127.0.0.1:8002/sql --ns NS --db DB

Steps: config -> schema and gates -> probes (both ways) -> intake -> chunk -> label -> validate
       -> load -> the adapter's scenario through the typed write surface -> rendered view -> health.
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
for sub in ("engine", "engine/pipeline", "engine/present"):
    sys.path.insert(0, str(ROOT / sub))
import config  # noqa: E402
import db as dbmod  # noqa: E402
import gates  # noqa: E402
import intake  # noqa: E402
import chunker  # noqa: E402
import validator  # noqa: E402
import loader  # noqa: E402
import write  # noqa: E402

SURREAL = pathlib.Path.home() / "AppData" / "Local" / "SurrealDB" / "surreal.exe"


def load_module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def start_memory(port):
    proc = subprocess.Popen([str(SURREAL), "start", "--bind", "127.0.0.1:%d" % port, "--user", "root",
                             "--pass", "root", "memory"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
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
        report = gates.run_probes(inst, conn)
        say("gates", "%d rule(s): %d rejected as required, %d accepted as required, %d misbehaved"
            % (len(report["rules"]), report["rejected_ok"], report["accepted_ok"], report["misbehaved"]))
        if report["misbehaved"]:
            print(json.dumps(report, indent=2))
            return 1

        adapter = load_module(inst_dir / "adapter.py")
        counts = intake.write_units(adapter.Adapter(), out, inst)
        say("intake", "%d units kept (%s), %d excluded and logged"
            % (counts["kept"], ", ".join("%s %d" % kv for kv in counts["by_type"].items()), counts["excluded"]))

        chunks = chunker.chunk(out / "units.jsonl", out, inst)
        say("chunk", "%d slice(s): %s" % (len(chunks), ", ".join("%s=%d" % (c["id"], c["count"]) for c in chunks)))

        labeler_path = inst_dir / "labeler_stub.py"
        if not labeler_path.exists():
            print("no labeler: agents label real instances; the demo needs labeler_stub.py")
            return 1
        labeler = load_module(labeler_path)
        labels_dir = out / "labels"
        labels_dir.mkdir(exist_ok=True)
        failures = 0
        tiers = {}
        for chunk in chunks:
            slice_path = out / "slices" / (chunk["id"] + ".jsonl")
            labels = [labeler.label(u) for u in intake.read_units(slice_path)]
            labels_path = labels_dir / (chunk["id"] + ".jsonl")
            labels_path.write_text("".join(json.dumps(l) + "\n" for l in labels), encoding="utf-8")
            rep = validator.validate(slice_path, labels_path, inst)
            failures += len(rep["errors"])
            for l in labels:
                tiers[l["tier"]] = tiers.get(l["tier"], 0) + 1
            for err in rep["errors"]:
                print("   validator: %s %s %s" % (err["unit"], err["field"], err["error"]))
        say("label", "%d labels by tier %s (stand-in labeler, not agents)"
            % (sum(tiers.values()), dict(sorted(tiers.items()))))
        say("validate", "%s" % ("PASS: every unit once, vocabulary ok, quotes verbatim" if not failures
                                 else "FAIL: %d error(s)" % failures))
        if failures:
            return 1

        counts = loader.load(out / "units.jsonl", labels_dir, conn, inst)
        say("load", "%d units, %d labels (grade agent), %d targets, %d bears_on edges, %d swept"
            % (counts["units"], counts["labels"], counts["targets"], counts["edges"], counts["swept"]))

        writer = write.Writer(conn, inst, {"agent_id": "demo_walkthrough", "role": "reader", "lens": None})
        lines = []
        adapter.scenario(writer, lambda step, outcome: lines.append((step, outcome)))
        print("scenario     the typed write surface, with the datastore answering:")
        for step, outcome in lines:
            print("   - %s\n       %s" % (step, outcome))

        health = {
            "units": conn.count("unit"), "labels": conn.count("label"),
            "facts": conn.sql("SELECT status, count() AS n FROM fact GROUP BY status")[0],
            "conclusions_ready": conn.sql("SELECT count() AS n FROM conclusion WHERE ready = true GROUP ALL")[0],
            "critiques_unanswered": conn.sql("SELECT count() AS n FROM critique WHERE answered = false GROUP ALL")[0],
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
