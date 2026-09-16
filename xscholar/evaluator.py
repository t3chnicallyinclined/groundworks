# -*- coding: utf-8 -*-
"""evaluator.py -- run an instance's regression file: every error it has actually made, checked again.

    python -m xscholar.evaluator INSTANCE_DIR --memory PORT      (PORT 0: an OS-assigned free port)
    python -m xscholar.evaluator INSTANCE_DIR --url URL --ns NS  (a server you run; each entry gets its own
                                                                 fresh database, refused if not empty)
    python -m xscholar.evaluator INSTANCE_DIR --check            (the file's format only; no database)

Why. `evaluator.regression_file` has been required by config since the first commit, and the demo declared
`evals/known_errors.json` -- but the file did not exist and no code read the key. The fixed test every
improvement must beat (docs/METHOD.md s.15, the Lessons loop) had never run once: a check that did nothing,
in the evaluator itself (docs/TESTING.md s.3).

THE FILE (human-owned; entries may be added, never removed or loosened):

    {"format": "xscholar.known_errors/1",
     "entries": [{"id": "KE001",
                  "error":  "what went wrong, in plain words",
                  "found":  {"commit": "...", "how": "...", "measured": "the reproduction, as recorded"},
                  "added":  "YYYY-MM-DD",
                  "check":  {"kind": "writes" | "loads" | "validator", ...}}]}

Each check states what must happen now that the error is fixed, IN BOTH DIRECTIONS (CLAUDE.md rule 4): a
check with only refusals, or only acceptances, is refused as a format error.

  writes     Typed writes through xscholar.pipeline.write.Writer on a fresh database with the instance's
             schema, gates, temporal history and (when declared) per-role PERMISSIONS applied. `setup` is
             SurrealQL run as root first. Each step names a writer, an op, args, and what must happen:
             accepted | refused (a gate) | denied (a permission). `save` keeps a return value for a later
             "$name" argument. Needs >= 1 accepted step and >= 1 refused or denied step.
  loads      The instance's own corpus (its adapter.py) and labels (labeler_stub.py, or --labels DIR) loaded
             by xscholar.pipeline.loader, then steps: count / load / correct / expect_count / expect_unit /
             expect_load. Needs >= 1 expectation and >= 1 load.
  validator  No database. A slice and labels with the planted error must produce every expected (unit, field)
             error, and `control_labels` for the same slice must pass.

VERDICTS: CAUGHT (the error is still caught), REGRESSED (it is back, named by step), NOT EXECUTED (the check
could not run: no server, a fixture that did not build). Exit 0 only when every entry is CAUGHT; 1 on any
REGRESSED or a malformed file; 2 on any NOT EXECUTED or a file with no entries. The summary is the last line.

The runner never writes the regression file: it opens it read-only and checks its SHA-256 is unchanged at
the end, so nothing a check runs can rewrite the evaluator it is judged by. Standard library only.
"""
import argparse
import contextlib
import hashlib
import json
import pathlib
import secrets
import shutil
import socket
import sys
import tempfile
import time

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config  # noqa: E402

FORMAT = "xscholar.known_errors/1"
KINDS = ("writes", "loads", "validator")
ENTRY_KEYS = {"id", "error", "found", "added", "check", "note"}
WRITE_OPS = ("propose", "cite", "confirm", "rule_out", "support", "relate", "critique", "answer", "mark_ready")
WRITE_OUTCOMES = ("accepted", "refused", "denied")
LOAD_LABELS = ("fixture", "empty_dir", "missing_dir", "wrong_extension")
LOAD_STEPS = ("count", "load", "correct", "expect_count", "expect_unit", "expect_load")

CAUGHT, REGRESSED, NOT_EXECUTED = "CAUGHT", "REGRESSED", "NOT EXECUTED"


class RegressionFileError(Exception):
    pass


class NotExecuted(Exception):
    pass


# ---- the file -------------------------------------------------------------------------------------

def parse(text, where="regression file"):
    """The parsed file, or RegressionFileError naming every problem. Used by config.load too, so the
    format has one implementation."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RegressionFileError("%s is not valid JSON: %s" % (where, exc)) from exc
    problems = []
    if not isinstance(data, dict):
        raise RegressionFileError("%s must be a JSON object with format and entries" % where)
    if data.get("format") != FORMAT:
        problems.append("format must be %r (got %r)" % (FORMAT, data.get("format")))
    entries = data.get("entries")
    if not isinstance(entries, list):
        problems.append("entries must be a list (it may be empty only until the first error is recorded)")
        entries = []
    seen = set()
    for i, entry in enumerate(entries):
        label = "entry %d" % i
        if not isinstance(entry, dict):
            problems.append("%s is not an object" % label)
            continue
        label = "entry %s" % entry.get("id", i)
        unknown = sorted(set(entry) - ENTRY_KEYS)
        if unknown:
            problems.append("%s has unknown key(s) %s" % (label, ", ".join(unknown)))
        for key in ("id", "error", "found", "check"):
            if not entry.get(key):
                problems.append("%s needs %s" % (label, key))
        if entry.get("id") in seen:
            problems.append("%s: duplicate id" % label)
        seen.add(entry.get("id"))
        check = entry.get("check")
        if isinstance(check, dict):
            problems.extend("%s: %s" % (label, p) for p in _check_problems(check))
        elif check is not None:
            problems.append("%s: check must be an object" % label)
    if problems:
        raise RegressionFileError("%s is not a valid regression file:\n  - %s" % (where, "\n  - ".join(problems)))
    return data


def _check_problems(check):
    kind = check.get("kind")
    if kind not in KINDS:
        return ["check kind must be one of %s (got %r)" % (", ".join(KINDS), kind)]
    problems = []
    if kind == "writes":
        writers = check.get("writers")
        if not isinstance(writers, dict) or not writers:
            problems.append("a writes check needs writers: {name: {role, lens}}")
            writers = {}
        steps = check.get("steps") if isinstance(check.get("steps"), list) else []
        if not steps:
            problems.append("a writes check needs steps")
        for n, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                problems.append("step %d is not an object" % n)
                continue
            if step.get("as") not in writers:
                problems.append("step %d: as %r is not one of the declared writers" % (n, step.get("as")))
            if step.get("op") not in WRITE_OPS:
                problems.append("step %d: op must be one of %s" % (n, ", ".join(WRITE_OPS)))
            if step.get("expect") not in WRITE_OUTCOMES:
                problems.append("step %d: expect must be one of %s" % (n, ", ".join(WRITE_OUTCOMES)))
        expects = {s.get("expect") for s in steps if isinstance(s, dict)}
        if steps and ("accepted" not in expects or not expects & {"refused", "denied"}):
            problems.append("a writes check must show both directions: at least one step accepted and at "
                            "least one refused or denied (a check that only refuses passes a gate that "
                            "refuses everything)")
        if not isinstance(check.get("setup", []), list):
            problems.append("setup must be a list of SurrealQL statements")
    elif kind == "loads":
        steps = check.get("steps") if isinstance(check.get("steps"), list) else []
        names = [next(iter(s)) if isinstance(s, dict) and len(s) == 1 else None for s in steps]
        for n, (step, name) in enumerate(zip(steps, names), 1):
            if name not in LOAD_STEPS:
                problems.append("step %d must be an object with exactly one of %s" % (n, ", ".join(LOAD_STEPS)))
            elif name == "load" and (step["load"] or {}).get("labels", "fixture") not in LOAD_LABELS:
                problems.append("step %d: load labels must be one of %s" % (n, ", ".join(LOAD_LABELS)))
        if not any(n and n.startswith("expect") for n in names):
            problems.append("a loads check needs at least one expect_ step: a check that expects nothing "
                            "passes whatever happens")
        if "load" not in names:
            problems.append("a loads check needs at least one load step")
    elif kind == "validator":
        for key in ("slice", "labels", "control_labels", "expect_errors"):
            if not isinstance(check.get(key), list) or not check.get(key):
                problems.append("a validator check needs a non-empty %s list" % key)
        for pair in check.get("expect_errors") or []:
            if not (isinstance(pair, dict) and pair.get("unit") and pair.get("field")):
                problems.append("expect_errors entries are {unit, field}")
    return problems


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def read_file(path):
    """(data, sha256). Read-only."""
    path = pathlib.Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RegressionFileError("regression file %s cannot be read: %s" % (path, exc)) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RegressionFileError("regression file %s is not UTF-8: %s" % (path, exc)) from exc
    return parse(text, str(path)), hashlib.sha256(raw).hexdigest()


# ---- a throwaway server ------------------------------------------------------------------------------

def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(port):
    """A throwaway in-memory SurrealDB this run owns. Refuses a port already in use; never borrows a server.
    Returns (proc, url). Stop it with stop_server(proc) -- by our own handle, never by name. The guards are
    xscholar/surreal_bin.py start_memory's, the one implementation scripts/run_instance.py uses too."""
    from xscholar import surreal_bin
    port = port or _free_port()
    try:
        proc = surreal_bin.start_memory(port)
    except surreal_bin.NotStarted as exc:
        raise NotExecuted(str(exc).replace("NOT EXECUTED: ", "", 1)) from exc
    return proc, "http://127.0.0.1:%d/sql" % port


def stop_server(proc):
    from xscholar import surreal_bin
    surreal_bin.stop(proc)


# ---- one fresh database per entry ---------------------------------------------------------------

def fresh_db(url, ns, name):
    """A Db on a database nothing has written. A database that already holds tables is refused: the
    evaluator writes rows, and it must never write them into an instance's real graph."""
    from xscholar import db as dbmod
    conn = dbmod.Db(url=url, ns=ns, db=name)
    conn.ping(check_version=False)
    info = conn.sql("INFO FOR DB", expect=1)[0] or {}
    if info.get("tables"):
        raise NotExecuted("database %s/%s already holds table(s) %s; the evaluator only writes into a database "
                          "it created" % (ns, name, ", ".join(sorted(info["tables"]))[:200]))
    return conn


def apply_schema(conn, inst):
    from xscholar import access, gates, temporal
    conn.script(gates.schema_surql(inst))
    conn.script(gates.gate_surql(inst))
    conn.script(temporal.temporal_surql(inst))
    if inst.access_roles():
        conn.script(access.access_surql(inst))


# ---- the checks --------------------------------------------------------------------------------------

def _resolve(value, saved):
    if isinstance(value, str) and value.startswith("$"):
        if value[1:] not in saved:
            raise KeyError("no saved value %r (steps save with `save`)" % value[1:])
        return saved[value[1:]]
    if isinstance(value, list):
        return [_resolve(v, saved) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, saved) for k, v in value.items()}
    return value


def run_writes(check, inst, conn, writer_factory=None):
    """(verdict, reason, steps). `writer_factory(name, spec)` is a seam for tests; the default provisions a
    credential per writer when the instance declares access roles, and uses the root connection otherwise."""
    from xscholar.db import PermissionDenied
    from xscholar.pipeline.write import GateRefusal, Writer
    try:
        apply_schema(conn, inst)
        for statement in check.get("setup") or []:
            conn.sql(statement)
        writers = {}
        for name, spec in check["writers"].items():
            if writer_factory is not None:
                writers[name] = writer_factory(name, spec)
                continue
            agent_id = "ke_%s" % name
            db = conn
            if inst.access_roles():
                from xscholar import access
                db = access.connect(access.provision(conn, inst, agent_id, spec["role"], lens=spec.get("lens")))
            writers[name] = Writer(db, inst, {"agent_id": agent_id, "role": spec["role"], "lens": spec.get("lens")})
    except Exception as exc:
        return NOT_EXECUTED, "the check's fixture did not build: %s: %s" % (type(exc).__name__, exc), []
    saved, log = {}, []
    for n, step in enumerate(check["steps"], 1):
        try:
            args = _resolve(step.get("args") or [], saved)
            kwargs = _resolve(step.get("kwargs") or {}, saved)
        except KeyError as exc:
            return NOT_EXECUTED, "step %d: %s" % (n, exc), log
        try:
            value = getattr(writers[step["as"]], step["op"])(*args, **kwargs)
            got, detail = "accepted", ""
            if step.get("save"):
                saved[step["save"]] = value
        except GateRefusal as refusal:
            got, detail = "refused", str(refusal)
        except PermissionDenied as denied:
            got, detail = "denied", str(denied)
        except Exception as exc:          # neither accepted nor a named refusal: never read as either
            got, detail = "error:%s" % type(exc).__name__, str(exc)
        log.append({"step": n, "as": step["as"], "op": step["op"], "expect": step["expect"], "got": got,
                    "detail": detail[:300]})
        if got != step["expect"]:
            return REGRESSED, "step %d (%s %s %s): expected %s, got %s%s" % (
                n, step["as"], step["op"], json.dumps(args)[:120], step["expect"], got,
                (": " + detail[:200]) if detail else ""), log
    return CAUGHT, "%d step(s) behaved in both directions" % len(log), log


def build_corpus(inst, inst_dir, work, labels_dir=None):
    """The instance's own units (adapter.py) and labels, validated, in `work`. Returns (units_path, labels_dir)."""
    import importlib.util
    from xscholar.pipeline import chunker, intake, validator
    adapter_path = pathlib.Path(inst_dir) / "adapter.py"
    if not adapter_path.exists():
        raise NotExecuted("instance has no adapter.py to read its corpus from")
    spec = importlib.util.spec_from_file_location("instance_adapter_%s" % inst.id, adapter_path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    intake.write_units(adapter.Adapter(), work, inst)
    chunks = chunker.chunk(work / "units.jsonl", work, inst)
    if labels_dir is None:
        stub_path = pathlib.Path(inst_dir) / "labeler_stub.py"
        if not stub_path.exists():
            raise NotExecuted("no labels: pass --labels DIR, or provide labeler_stub.py")
        stub_spec = importlib.util.spec_from_file_location("instance_stub_%s" % inst.id, stub_path)
        stub = importlib.util.module_from_spec(stub_spec)
        stub_spec.loader.exec_module(stub)
        labels_dir = work / "labels"
        labels_dir.mkdir(exist_ok=True)
        for chunk in chunks:
            slice_path = work / "slices" / (chunk["id"] + ".jsonl")
            (labels_dir / (chunk["id"] + ".jsonl")).write_text(
                "".join(json.dumps(stub.label(u)) + "\n" for u in intake.read_units(slice_path)), encoding="utf-8")
    errors = []
    for chunk in chunks:
        rep = validator.validate(work / "slices" / (chunk["id"] + ".jsonl"),
                                 pathlib.Path(labels_dir) / (chunk["id"] + ".jsonl"), inst)
        errors.extend(rep["errors"])
    if errors:
        raise NotExecuted("the fixture's labels do not validate (%d error(s), first: %s)" % (len(errors), errors[0]))
    return work / "units.jsonl", pathlib.Path(labels_dir)


def run_loads(check, inst, inst_dir, conn, work, labels_dir=None):
    from xscholar.pipeline import loader
    try:
        apply_schema(conn, inst)
        units_path, fixture_labels = build_corpus(inst, inst_dir, work, labels_dir)
    except NotExecuted as exc:
        return NOT_EXECUTED, str(exc), []
    except Exception as exc:
        return NOT_EXECUTED, "the check's fixture did not build: %s: %s" % (type(exc).__name__, exc), []
    variants = {"fixture": fixture_labels, "empty_dir": work / "labels_empty",
                "missing_dir": work / "labels_that_do_not_exist", "wrong_extension": work / "labels_json_only"}
    variants["empty_dir"].mkdir(exist_ok=True)
    variants["wrong_extension"].mkdir(exist_ok=True)
    for path in fixture_labels.glob("*.jsonl"):
        shutil.copyfile(path, variants["wrong_extension"] / (path.stem + ".json"))
    saved, log, last = {}, [], None
    for n, step in enumerate(check["steps"], 1):
        (name, body), = step.items()
        body = body if isinstance(body, dict) else {"table": body}
        try:
            if name == "count":
                value = conn.count(body["table"])
                saved[body.get("as", body["table"])] = value
                log.append({"step": n, "count": body["table"], "value": value})
                if value < body.get("require_min", 0):
                    return NOT_EXECUTED, ("step %d: %s holds %d row(s), fewer than the %d this check needs to show "
                                          "anything (a loss from nothing cannot be seen)"
                                          % (n, body["table"], value, body["require_min"])), log
            elif name == "load":
                target = variants[body.get("labels", "fixture")]
                accept = [tuple(pair) for pair in body.get("accept_source") or []]
                want = body.get("raises")
                try:
                    last = loader.load(units_path, target, conn, inst, accept_source=accept)
                    got = "ok"
                except loader.LoadError as exc:
                    got = "LoadError"
                    last = {"error": str(exc)}
                log.append({"step": n, "load": body.get("labels", "fixture"), "got": got})
                if (want or "ok") != got:
                    return REGRESSED, "step %d: load with labels %s: expected %s, got %s %s" % (
                        n, body.get("labels", "fixture"), want or "ok", got, last.get("error", "")[:200]), log
            elif name == "correct":
                loader.correct_unit(conn, body["unit"], body["field"], body["value"], by=body.get("by", "evaluator"))
                log.append({"step": n, "correct": body})
            elif name == "expect_count":
                want = _resolve(body["equals"], saved)
                value = conn.count(body["table"])
                log.append({"step": n, "expect_count": body["table"], "want": want, "got": value})
                if value != want:
                    return REGRESSED, "step %d: %s holds %d row(s), expected %d" % (n, body["table"], value, want), log
            elif name == "expect_unit":
                from xscholar.pipeline.loader import rid
                rows = conn.sql("SELECT %s FROM %s" % (body["field"], rid("unit", body["unit"])), expect=1)[0]
                value = rows[0].get(body["field"]) if rows else None
                log.append({"step": n, "expect_unit": body["unit"], "want": body["equals"], "got": value})
                if value != body["equals"]:
                    return REGRESSED, "step %d: unit %s %s is %r, expected %r" % (
                        n, body["unit"], body["field"], value, body["equals"]), log
            elif name == "expect_load":
                value = (last or {}).get(body["key"])
                log.append({"step": n, "expect_load": body["key"], "want": body["equals"], "got": value})
                if value != body["equals"]:
                    return REGRESSED, "step %d: the last load reported %s = %r, expected %r" % (
                        n, body["key"], value, body["equals"]), log
        except Exception as exc:
            return REGRESSED, "step %d (%s) raised %s: %s" % (n, name, type(exc).__name__, str(exc)[:300]), log
    return CAUGHT, "%d step(s) held" % len(log), log


def run_validator(check, inst, work):
    from xscholar.pipeline import validator
    slice_path, labels_path, control_path = work / "slice.jsonl", work / "labels.jsonl", work / "control.jsonl"
    for path, rows in ((slice_path, check["slice"]), (labels_path, check["labels"]),
                       (control_path, check["control_labels"])):
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    try:
        control = validator.validate(slice_path, control_path, inst)
        planted = validator.validate(slice_path, labels_path, inst)
    except Exception as exc:
        return REGRESSED, "the validator raised %s: %s" % (type(exc).__name__, exc), []
    if control["errors"]:
        return REGRESSED, "the control labels, which are correct, were refused: %s" % control["errors"][:3], []
    found = {(e.get("unit"), e.get("field")) for e in planted["errors"]}
    missing = [(p["unit"], p["field"]) for p in check["expect_errors"] if (p["unit"], p["field"]) not in found]
    if missing:
        return REGRESSED, "the planted error(s) %s were not reported (reported: %s)" % (missing, sorted(found)), []
    return CAUGHT, "planted error(s) reported and the control passed", []


# ---- the run ------------------------------------------------------------------------------------------

def run(inst, inst_dir, path, url=None, ns="xscholar_eval", labels_dir=None, say=print, writer_factory=None):
    """Judge every entry. Returns (results, sha256). The file is read once, and its hash re-checked at the end."""
    data, digest = read_file(path)
    results = []
    with tempfile.TemporaryDirectory(prefix="xscholar-eval-") as tmp:
        for entry in data["entries"]:
            check = entry["check"]
            work = pathlib.Path(tmp) / entry["id"]
            work.mkdir()
            started = time.time()
            if check["kind"] == "validator":
                verdict, reason, log = run_validator(check, inst, work)
            elif url is None:
                verdict, reason, log = NOT_EXECUTED, "this check needs a database and none was given", []
            else:
                name = "%s_%s" % (entry["id"].lower(), secrets.token_hex(3))
                try:
                    conn = fresh_db(url, ns, name)
                except NotExecuted as exc:
                    verdict, reason, log = NOT_EXECUTED, str(exc), []
                except Exception as exc:
                    verdict, reason, log = NOT_EXECUTED, "cannot reach the database: %s: %s" % (
                        type(exc).__name__, exc), []
                else:
                    try:
                        if check["kind"] == "writes":
                            verdict, reason, log = run_writes(check, inst, conn, writer_factory)
                        else:
                            verdict, reason, log = run_loads(check, inst, inst_dir, conn, work, labels_dir)
                    finally:
                        with contextlib.suppress(Exception):
                            conn.sql("REMOVE DATABASE IF EXISTS %s" % name)
            results.append({"id": entry["id"], "error": entry["error"], "kind": check["kind"], "verdict": verdict,
                            "reason": reason, "steps": log, "seconds": round(time.time() - started, 2)})
            say("%-13s %-6s %s\n              %s" % (verdict, entry["id"], entry["error"][:100], reason[:400]))
    if sha256(path) != digest:
        raise RegressionFileError("the regression file %s changed while the evaluator ran; nothing a check runs may "
                                  "rewrite the evaluator it is judged by" % path)
    return results, digest


def summary_line(inst_id, path, digest, results):
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in (CAUGHT, REGRESSED, NOT_EXECUTED)}
    return ("EVALUATOR  %s  %s (sha256 %s) | %d entries | %d still caught | %d REGRESSED | %d NOT EXECUTED"
            % (inst_id, path, digest[:12], len(results), counts[CAUGHT], counts[REGRESSED], counts[NOT_EXECUTED]))


def exit_code(results):
    if not results:
        return 2
    if any(r["verdict"] == REGRESSED for r in results):
        return 1
    if any(r["verdict"] == NOT_EXECUTED for r in results):
        return 2
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m xscholar.evaluator")
    parser.add_argument("instance_dir")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--memory", type=int, metavar="PORT",
                       help="start a throwaway in-memory SurrealDB on PORT (0: an OS-assigned free port)")
    where.add_argument("--url", help="a server you run; every entry gets its own new database under --ns")
    where.add_argument("--check", action="store_true", help="check the file's format only; run nothing")
    parser.add_argument("--ns", default="xscholar_eval")
    parser.add_argument("--labels", help="labels for loads checks instead of labeler_stub.py")
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args(argv)
    inst_dir = pathlib.Path(args.instance_dir)
    try:
        inst = config.load(inst_dir / "instance.yaml")      # refuses a missing or unparseable regression file
        path = config.regression_file_path(inst)
        if args.check:
            data, digest = read_file(path)
            print("EVALUATOR  %s  %s (sha256 %s) | %d entries | format ok | NOT EXECUTED (--check runs nothing)"
                  % (inst.id, path, digest[:12], len(data["entries"])))
            return 0 if data["entries"] else 2
        proc = None
        try:
            if args.memory is not None:
                proc, url = start_server(args.memory)
            else:
                url = args.url
            results, digest = run(inst, inst_dir, path, url=url, ns=args.ns,
                                  labels_dir=pathlib.Path(args.labels) if args.labels else None)
        finally:
            stop_server(proc)
    except (config.ConfigError, RegressionFileError) as exc:
        print(str(exc))
        print("EVALUATOR  FAILED: %s" % str(exc).splitlines()[0])
        return 1
    except NotExecuted as exc:
        print("EVALUATOR  NOT EXECUTED: %s" % exc)
        return 2
    if args.json_out:
        if pathlib.Path(args.json_out).resolve() == pathlib.Path(path).resolve():
            print("EVALUATOR  FAILED: --json names the regression file itself; the evaluator never writes it")
            return 1
        pathlib.Path(args.json_out).write_text(json.dumps({"instance": inst.id, "file": str(path), "sha256": digest,
                                                           "results": results}, indent=1, default=str),
                                               encoding="utf-8")
    if not results:
        print("EVALUATOR  %s  %s | 0 entries | NOT EXECUTED: an evaluator with no known errors checks nothing"
              % (inst.id, path))
        return 2
    print(summary_line(inst.id, path, digest, results))
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
