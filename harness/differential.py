# -*- coding: utf-8 -*-
"""differential.py -- the owner's gate for the whole Groundworks Rust port.

The Python engine at tag **v0.2.0** (commit 94ad5dc) is the ORACLE. Nothing else is. This harness
pins it, records what it emits, and diffs a candidate against it BYTE FOR BYTE. It is built before
any Rust engine code exists on purpose: the gate is what every port ticket is judged by, so it must
be able to go RED before anything can be called green.

    python groundworks/harness/differential.py --ensure-oracle
    python groundworks/harness/differential.py --oracle v0.2.0 --record-goldens
    python groundworks/harness/differential.py --oracle v0.2.0 --record-transcript [--twice]
    python groundworks/harness/differential.py --oracle v0.2.0 --self-test        # THE GATE
    python groundworks/harness/differential.py --oracle v0.2.0 --mcp --candidate <bin>

What it compares
----------------
1. **SurQL goldens, no normalization ever.** One file per (instance, component) for every emitter the
   oracle has, for every instance in `instances/`, plus the instance-independent emitters under
   `_global/`. Bytes are compared as written; nothing is sorted, trimmed or pretty-printed.
2. **The config surface.** `gates.spec(inst)` as sorted JSON per instance, and the accept/refuse
   verdict plus the verbatim error text for every bad-config fixture in the oracle's
   `tests/test_config.py` (read out of the test source by AST, so a new fixture is picked up).
3. **A seeded single-agent MCP transcript.** One oracle front-door process over stdio, driven by the
   official `mcp` Python client through a FIXED call script over the v0.2.0 tool surface, against a
   throwaway SurrealDB this harness starts and stops BY PID.

The mask list (the only normalization that exists, and only for the transcript)
------------------------------------------------------------------------------
The database clock cannot be frozen, so a SHORT, NAMED list of rules -- and no others -- is applied
to a recorded envelope before it is diffed. Every rule counts its substitutions, and the two sides'
counts must be equal, so a rule that silently stops firing is itself a failure. `docs/RUST_PORT.md`
carries this list with the line that justifies each. Rules (4) and (5) were added on 2026-09-19 from
the first recording, each with its reason below; overturning one needs a logged reason.

  (1) DATETIME_FIELDS, by name only: learned_at, created_at, updated_at, started_at, ended_at,
      cited_at, verified_at -> "<datetime>", and ONLY after the value parses as a datetime. A
      non-datetime in one of those fields is left alone, so it shows up as a difference instead of
      being hidden.
  (2) RANDOM_ID_FIELDS, by name only, and ONLY where the oracle generates the id randomly.
      It is EMPTY: the oracle derives every record id it writes. CITED: the critique id comes from
      sha1(conclusion|lens|head) (`groundworks/pipeline/write.py:143` + `patterns/edge_ids.py:27-29`),
      the answer id is "ans_<critique>" (`write.py:161`), every edge id is the same sha1
      (`patterns/edge_ids.py:27`), and `rand::`/`rand()` appears nowhere in the engine's SurQL
      (checked over `groundworks/**` at v0.2.0). `run` is the one clock-derived token
      (`patterns/edge_ids.py:32`, `time.time_ns()`); it is a loader stamp on edges, is listed in
      RUN_TOKEN_FIELDS, and is masked under this rule only where it appears.
  (3) `bytes` at the TOP LEVEL of a transcript line is the harness's own length of that line's
      UNMASKED envelope. It is checked equal to that side's own recomputed length and then dropped.
      `data.bytes` inside an envelope (a document's size) is the engine's and is COMPARED.
  (4) ENGINE_CODE_VOLATILE_LEAVES, ONLY inside `engine_code` (and the free-text `engine_code_warning` /
      `WARNING` strings, masked whole): `read_ms`, `kind`, `reason`, `branch`, `commit`, `dirty`,
      `dirty_count`, `dirty_files` -- the two nested state dicts (`started_from`, `on_disk_now`) that
      `code_state.read_state` produces. These, and only these, are a measured duration and a
      git-timeout-dependent re-read a Rust candidate cannot reproduce. `verdict`, `writes`, `override`,
      `problems`, `missing`, `checked` and the key set of `engine_code` itself are DETERMINISTIC engine
      behaviour (this checkout is always the same clean commit) and are COMPARED, not masked. REVISED
      2026-09-19 (fix round): the whole-subtree mask hid `override`/`checked` and let a candidate that
      dropped the write-authorization guard entirely pass; narrowed to leaves.
  (5) LAUNCH PATHS, at RECORD time on both sides: the two absolute paths the HARNESS chose (the
      oracle checkout and the panel root) become <ORACLE_DIR> / <PANEL_ROOT> where a refusal text
      quotes them back. They are the harness's inputs, not the engine's output. Counted as `path` in
      the same counts dict as every other rule (fix round: it used to count only at record time and
      print, with no comparable key -- `mask_transcript` now recounts the token occurrences it finds).

What is NOT normalized, and is instead PINNED (fix round 2)
-----------------------------------------------------------
* The oracle checkout must have NOTHING git is not tracking -- untracked OR ignored. `__pycache__/` is
  ignored and is where CPython loads code from; a forged .pyc there poisons every emitter silently.
* The server's whole launch environment is a closed set (`FIXED_ENV` + `PINNED_SERVER_ENV_KEYS`), including
  `PATH`, which is an EMPTY directory so the engine's git probe answers the same constant on every machine
  instead of this one's 15-second timeout. `record_transcript` REFUSES to write a golden that says otherwise.
  The diagnosis and the measurements are in docs/RUST_PORT.md section 4b.

Exit codes: 0 green, 1 a difference or a red arm that failed to go red, 2 not executed (the oracle
is not pinned, a tool is missing, the datastore would not start).
"""
import argparse
import ast
import copy
import datetime as _dt
import hashlib
from fnmatch import fnmatchcase as _fnmatch
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent          # groundworks-rs/groundworks/harness
GW = HERE.parent                                        # groundworks-rs/groundworks
GOLDENS = GW / "goldens"
ORACLE_DIR = pathlib.Path(os.environ.get("GROUNDWORKS_ORACLE_DIR", "C:/Users/trist/projects/groundworks-oracle"))
ORACLE_URL = "https://github.com/t3chnicallyinclined/groundworks"
ORACLE_TAG = "v0.2.0"
ORACLE_COMMIT = "94ad5dca0882121c71245dd644fe533743788655"

TRANSCRIPT = GOLDENS / ("transcript-%s.jsonl" % ORACLE_TAG)
TRANSCRIPT_MASKS = GOLDENS / ("transcript-%s.masks.json" % ORACLE_TAG)
MANIFEST = GOLDENS / "MANIFEST.sha256"

DATETIME_FIELDS = ("learned_at", "created_at", "updated_at", "started_at", "ended_at", "cited_at",
                   # ADDED 2026-09-19 from the first recording, with its line: `verified_at` is written
                   # `time::now()` by the datastore when an original is re-hashed
                   # (groundworks/originals.py:513), and document_get re-hashes on every read. It is the
                   # ONE datetime the v0.2.0 tool surface actually returns, so without it the mask list
                   # covers nothing and the transcript is not reproducible twice.
                   "verified_at")
RANDOM_ID_FIELDS = ()          # see rule (2) above: the oracle generates none
RUN_TOKEN_FIELDS = ("run",)    # patterns/edge_ids.py:32, "r%d" % time.time_ns()
# Rule (4), ADDED 2026-09-19, NARROWED 2026-09-19 (fix round, blocking finding 1). `engine_code` /
# `engine_code_warning` (and the `WARNING` that repeats it) report the identity and freshness of the
# PYTHON ENGINE CHECKOUT this server started from (groundworks/mcp/code_state.py). Only the LEAVES
# below are not engine behaviour -- a measured duration (`read_ms`) and the state a `git` re-read
# produces, which can flip when it exceeds code_state.GIT_TIMEOUT_S=15 (seen on this machine) -- and a
# Rust candidate has no Python checkout to report at all. `verdict`, `writes`, `override`, `problems`,
# `missing`, `checked` and the key set stay COMPARED: they are deterministic for this pinned checkout,
# and masking them whole (the original rule) let a candidate silently drop the write-authorization
# guard and still pass. `engine_code_warning` and `WARNING` are free-text strings that embed the same
# volatile detail, so those two are still masked whole when present.
ENGINE_CODE_CONTAINERS = ("engine_code",)
ENGINE_CODE_WHOLE_STRING_FIELDS = ("engine_code_warning",)
ENGINE_CODE_VOLATILE_LEAVES = ("read_ms", "kind", "reason", "branch", "commit",
                               "dirty", "dirty_count", "dirty_files")
ENGINE_CODE_WARNING_PREFIX = "WRITES ARE ALLOWED FROM UNVERIFIED ENGINE CODE"


# ---------------------------------------------------------------------------------------------
# the oracle: a fresh CLONE at the tag, never "whatever main is"
# ---------------------------------------------------------------------------------------------

class NotExecuted(Exception):
    """The comparison did not run. The message begins NOT EXECUTED."""


def _git(args, cwd, check=True):
    out = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    if check and out.returncode != 0:
        raise NotExecuted("NOT EXECUTED: git %s in %s: %s" % (" ".join(args), cwd, out.stderr.strip()))
    return out.stdout.strip()


def ensure_oracle(tag=ORACLE_TAG):
    """A fresh clone of the Python engine at `tag`, or NOT EXECUTED. Never a worktree of a working
    checkout: a worktree follows whatever that checkout does next."""
    if not ORACLE_DIR.exists():
        print("cloning %s -> %s" % (ORACLE_URL, ORACLE_DIR))
        out = subprocess.run(["git", "clone", "--quiet", ORACLE_URL + ".git", str(ORACLE_DIR)],
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise NotExecuted("NOT EXECUTED: could not clone the oracle: %s" % out.stderr.strip())
        _git(["checkout", "--quiet", "tags/" + tag], ORACLE_DIR)
    return verify_oracle(tag)


# The ONLY paths inside the oracle checkout the harness is allowed to delete: the DATA that
# `scripts/run_instance.py` writes while the transcript is being recorded. They are ignored by the oracle's
# .gitignore, they reappear on every recording (so refusing on them would stop the gate running twice), and
# deleting them first is strictly better than tolerating them -- `document_get` re-hashes originals on every
# read, so a planted original would change the transcript. NOTHING that CPython can load is on this list:
# an ignored `__pycache__/` is a REFUSAL, never a silent deletion. A destructive guard fails closed, so the
# list is matched component by component and nothing outside it is ever removed.
PURGEABLE_GLOBS = ("instances/*/out", "instances/*/originals")   # git ls-files: nothing tracked under either


def _status_entries(flags):
    """(code, path) for every line of `git status --porcelain -z <flags>` in the oracle checkout."""
    out = subprocess.run(["git", "-C", str(ORACLE_DIR), "status", "--porcelain", "-z"] + list(flags),
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise NotExecuted("NOT EXECUTED: git status in %s: %s" % (ORACLE_DIR, out.stderr.strip()))
    return [(entry[:2], entry[3:]) for entry in out.stdout.split("\0") if len(entry) >= 4]


def _purgeable_ancestor(rel):
    """The path the harness may delete to make `rel` go away, or None if `rel` is not the harness's to
    delete. `rel` is a posix path relative to ORACLE_DIR."""
    parts = pathlib.PurePosixPath(rel.rstrip("/")).parts
    for i in range(1, len(parts) + 1):
        prefix = "/".join(parts[:i])
        if any(_fnmatch(prefix, g) for g in PURGEABLE_GLOBS):
            return prefix
    return None


def purge_generated():
    """Delete the harness's OWN leftovers inside the oracle checkout, so verify_oracle can then demand that
    NOTHING untracked or ignored remains at all.

    ROUND-2 BLOCKING FINDING 1. `git status --porcelain` does not report GITIGNORED files, and the oracle's
    .gitignore ignores `__pycache__/` -- which is exactly where CPython loads code from. The reviewer forged
    a poisoned `groundworks/__pycache__/gates.cpython-313.pyc` carrying the real gates.py mtime+size in its
    PEP-552 header; verification passed and all three gates.surql goldens came back poisoned. A
    `__pycache__` is therefore NOT purged, it is REFUSED: the harness's own children all run with
    PYTHONDONTWRITEBYTECODE=1 (child_env), so one appearing means something else ran Python in the oracle
    checkout, and that is worth stopping the gate for rather than quietly deleting.

    Returns the list of paths that are NOT the harness's to delete (verify_oracle refuses on them)."""
    doomed, refuse = [], []
    for code, path in _status_entries(["--ignored=matching", "--untracked-files=all"]):
        if code not in ("!!", "??"):
            refuse.append("%s %s" % (code.strip() or code, path))
            continue
        ancestor = _purgeable_ancestor(path)
        if ancestor is None:
            refuse.append("%s %s" % (code, path))
        elif ancestor not in doomed:
            doomed.append(ancestor)
    for rel in doomed:
        target = ORACLE_DIR / rel
        if not str(target.resolve()).startswith(str(ORACLE_DIR.resolve())):   # fail closed
            raise NotExecuted("NOT EXECUTED: refusing to delete %s: it is outside %s" % (target, ORACLE_DIR))
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)
    return refuse


def verify_oracle(tag=ORACLE_TAG):
    """Refuse to compare against anything but the pinned tag, with NOTHING in the checkout that git is not
    tracking -- untracked (round-1 finding) or IGNORED (round-2 finding)."""
    if not (ORACLE_DIR / ".git").exists():
        raise NotExecuted("NOT EXECUTED: %s is not a git checkout. Run --ensure-oracle." % ORACLE_DIR)
    described = _git(["describe", "--exact-match"], ORACLE_DIR, check=False)
    if described != tag:
        raise NotExecuted("NOT EXECUTED: the oracle at %s is at %r, not %s. The gate compares against the "
                          "tag and nothing else." % (ORACLE_DIR, described or "no exact tag", tag))
    head = _git(["rev-parse", "HEAD"], ORACLE_DIR)
    if tag == ORACLE_TAG and head != ORACLE_COMMIT:
        raise NotExecuted("NOT EXECUTED: %s in %s is %s, not the pinned %s" % (tag, ORACLE_DIR, head, ORACLE_COMMIT))
    # round 1: --untracked-files=no let an untracked sitecustomize.py sit in the oracle checkout (which is
    # on PYTHONPATH and is the child's cwd) and be imported without this check ever seeing it.
    # round 2: --porcelain alone still does not see GITIGNORED paths, and __pycache__/ is ignored. The
    # harness deletes its own leftovers first, then demands an EMPTY untracked-and-ignored status.
    refuse = purge_generated()
    remaining = _status_entries(["--ignored=matching", "--untracked-files=all"])
    if refuse or remaining:
        listed = sorted(set(refuse) | {"%s %s" % (c, p) for c, p in remaining})
        raise NotExecuted("NOT EXECUTED: the oracle checkout has changes git is not tracking (uncommitted, "
                          "untracked or IGNORED -- an ignored path such as __pycache__/ is loadable code):\n%s"
                          % "\n".join("  " + line for line in listed))
    return head


# ---------------------------------------------------------------------------------------------
# goldens: emitted in a CHILD process that has the oracle on PYTHONPATH and a fixed hash seed
# ---------------------------------------------------------------------------------------------

PER_INSTANCE = [
    ("schema", "gates", "schema_surql"),
    ("unique", "gates", "unique_surql"),
    ("dangling", "gates", "dangling_edges_surql"),
    ("gates", "gates", "gate_surql"),
    ("gate_events", "gates", "gate_event_surql"),
    ("independence", "gates", "independence_surql"),
    ("independence_mode", "gates", "independence_mode_surql"),
    ("writer_record", "gates", "writer_record_surql"),
    ("access", "access", "access_surql"),
    ("lens", "lens", "lens_surql"),
    ("temporal", "temporal", "temporal_surql"),
    ("originals_enforcement", "originals", "enforcement_surql"),
    ("promotion_log", "promotion_log", "log_surql"),
]
GLOBAL = [
    ("provisioning", "access", "provisioning_fields_surql"),
    ("originals_schema", "originals", "ensure_schema_surql"),
]


def render(value, depth=0):
    """An emitter's return value as bytes on disk, with its SHAPE named so two shapes can never look
    the same. A str is written as it came; a list is one entry per line; a mapping keeps the
    emitter's OWN key order (never sorted -- the order is part of what the port must reproduce)."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ("-- list of %d\n" % len(value)) + "".join(render(v, depth + 1).rstrip("\n") + "\n" for v in value)
    if isinstance(value, dict):
        out = ["-- map of %d" % len(value)]
        for key, sub in value.items():
            out.append("-- [%s]" % key)
            out.append(render(sub, depth + 1).rstrip("\n"))
        return "\n".join(out) + "\n"
    return "-- %s\n%r\n" % (type(value).__name__, value)


def _emit_goldens(out_dir):
    """Runs INSIDE the child, with the oracle importable. Writes every golden under `out_dir`."""
    out_dir = pathlib.Path(out_dir)
    from groundworks import access, config, gates, lens, originals, promotion_log, temporal  # noqa: F401
    mods = {"gates": gates, "access": access, "lens": lens, "temporal": temporal,
            "originals": originals, "promotion_log": promotion_log}

    def write(rel, text):
        text = render(text)
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def call(fn, *args):
        """The emitter's output, or its refusal recorded verbatim. A candidate must refuse the same
        way for the same instance, so a raise is a golden too -- never a skipped file."""
        try:
            return fn(*args)
        except Exception as exc:                                    # noqa: BLE001 -- recorded, not swallowed
            return "!! RAISED %s: %s\n" % (type(exc).__name__, exc)

    instances = sorted(p for p in (ORACLE_DIR / "instances").iterdir() if (p / "instance.yaml").exists())
    # fixture instances shipped under tests/ (there are none at v0.2.0; a later tag may add some)
    instances += sorted(p.parent for p in (ORACLE_DIR / "tests").rglob("instance.yaml"))
    # and this harness's own fixtures. CONFIRMED at v0.2.0: only `_template` and `demo-incident` carry an
    # instance.yaml (the other 19 dirs under instances/ are INSTANCE.md briefs), and for BOTH of them
    # independence_surql, independence_mode_surql, writer_record_surql and log_surql return an empty list.
    # `fixtures/audit-on` is demo-incident with those two features on, so the gate is not blind to them.
    instances += sorted(p.parent for p in (HERE / "fixtures").rglob("instance.yaml"))
    names = []
    for inst_dir in instances:
        inst = config.load(inst_dir / "instance.yaml")
        names.append(inst_dir.name)
        for component, module, fname in PER_INSTANCE:
            write("surql/%s/%s.surql" % (inst_dir.name, component), call(getattr(mods[module], fname), inst))
        write("config/%s.spec.json" % inst_dir.name,
              json.dumps(call(gates.spec, inst), indent=2, sort_keys=True, default=str) + "\n")

    for component, module, fname in GLOBAL:
        write("surql/_global/%s.surql" % component, call(getattr(mods[module], fname)))
    write("surql/_global/originals_lines.surql", "\n".join(originals.schema_lines()) + "\n")
    # `patterns` is shipped SurQL, not a generated string: the port must reproduce these files too.
    pats = sorted((ORACLE_DIR / "groundworks" / "patterns").glob("*.surql"))
    write("surql/_global/patterns.surql",
          "".join("-- %s\n%s" % (p.name, p.read_text(encoding="utf-8")) for p in pats))
    write("surql/_global/INSTANCES.txt", "".join(n + "\n" for n in names))

    write("config/bad_configs.txt", _bad_config_verdicts())
    return names


def _bad_config_verdicts():
    """Every bad-config fixture in the oracle's tests/test_config.py, with its verdict and error text.

    The fixtures are `broken(...)` calls in the test source. They are read out by AST, so the golden
    follows the test file instead of a copy of it that would drift."""
    from groundworks import config
    src_path = ORACLE_DIR / "tests" / "test_config.py"
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    ns = {"config": config, "__file__": str(src_path), "__name__": "gw_oracle_test_config"}
    exec(compile(ast.parse(source.split("class ", 1)[0]), str(src_path), "exec"), ns)  # MINIMAL + broken()
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "broken":
            calls.append((node.lineno, ast.get_source_segment(source, node)))
    lines = ["# tests/test_config.py bad-config fixtures at %s: verdict and verbatim error" % ORACLE_TAG]
    for lineno, text in sorted(set(calls)):
        try:
            data = eval(text, dict(ns))                             # noqa: S307 -- the oracle's own test source
        except Exception as exc:                                    # noqa: BLE001
            lines.append("%s:%d  FIXTURE-RAISED %s: %s" % (src_path.name, lineno, type(exc).__name__, exc))
            continue
        one = " ".join(text.split())
        try:
            config.load(data)
            lines.append("%s:%d  ACCEPT  %s" % (src_path.name, lineno, one))
        except Exception as exc:                                    # noqa: BLE001
            lines.append("%s:%d  REFUSE  %s  ->  %s: %s" % (src_path.name, lineno, one, type(exc).__name__, exc))
    return "\n".join(lines) + "\n"


def _emit_config_fixtures(out_path):
    """Runs INSIDE the child (oracle importable): every `broken(...)` fixture in tests/test_config.py,
    as JSON-safe data (or the exception raised building it). `--config`'s parent process reads this so
    it can drive a CANDIDATE through `gw config check` the same way `_bad_config_verdicts` drives the
    oracle through `config.load` -- without importing the oracle's `groundworks` package into the
    parent process, which would risk writing a `__pycache__` into the pinned checkout (exactly the
    poisoning class `verify_oracle` exists to catch; see docs/RUST_PORT.md section 1)."""
    from groundworks import config
    src_path = ORACLE_DIR / "tests" / "test_config.py"
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    ns = {"config": config, "__file__": str(src_path), "__name__": "gw_oracle_test_config"}
    exec(compile(ast.parse(source.split("class ", 1)[0]), str(src_path), "exec"), ns)  # MINIMAL + broken()
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "broken":
            calls.append((node.lineno, ast.get_source_segment(source, node)))
    fixtures = []
    for lineno, text in sorted(set(calls)):
        one = " ".join(text.split())
        try:
            data = eval(text, dict(ns))                             # noqa: S307 -- the oracle's own test source
        except Exception as exc:                                    # noqa: BLE001
            fixtures.append({"lineno": lineno, "one": one, "raised": "%s: %s" % (type(exc).__name__, exc)})
            continue
        fixtures.append({"lineno": lineno, "one": one, "data": data})
    payload = {"src_name": src_path.name, "fixtures": fixtures}
    pathlib.Path(out_path).write_text(json.dumps(payload), encoding="utf-8")


def _config_instance_dirs():
    """Every instance directory the config golden covers, in the same order `_emit_goldens` sees them
    (CONFIRMED at v0.2.0: only `_template` and `demo-incident` carry an `instance.yaml` under
    `instances/`; `fixtures/audit-on` is this harness's own)."""
    instances = sorted(p for p in (ORACLE_DIR / "instances").iterdir() if (p / "instance.yaml").exists())
    instances += sorted(p.parent for p in (ORACLE_DIR / "tests").rglob("instance.yaml"))
    instances += sorted(p.parent for p in (HERE / "fixtures").rglob("instance.yaml"))
    return instances


def _candidate_bad_config_text(candidate):
    """`goldens/config/bad_configs.txt`'s text, produced by running each `tests/test_config.py`
    fixture through the CANDIDATE's `gw config check` instead of the oracle's `config.load`."""
    work = pathlib.Path(tempfile.mkdtemp(prefix="gw_config_fixtures_"))
    try:
        out_path = work / "fixtures.json"
        run = subprocess.run([sys.executable, str(HERE / "differential.py"), "--_config_fixtures", str(out_path)],
                             env=child_env(), capture_output=True, text=True, cwd=str(ORACLE_DIR))
        if run.returncode != 0:
            raise NotExecuted("NOT EXECUTED: reading bad-config fixtures failed:\n%s\n%s"
                              % (run.stdout[-3000:], run.stderr[-3000:]))
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        lines = ["# tests/test_config.py bad-config fixtures at %s: verdict and verbatim error" % ORACLE_TAG]
        for row in payload["fixtures"]:
            lineno, one = row["lineno"], row["one"]
            if "raised" in row:
                lines.append("%s:%d  FIXTURE-RAISED %s" % (payload["src_name"], lineno, row["raised"]))
                continue
            data_path = work / ("fixture_%d.json" % lineno)
            data_path.write_text(json.dumps(row["data"]), encoding="utf-8")
            cand = subprocess.run([candidate, "config", "check", str(data_path)], capture_output=True, text=True)
            if cand.returncode != 0:
                raise NotExecuted("NOT EXECUTED: candidate `config check` failed on line %d:\n%s\n%s"
                                  % (lineno, cand.stdout[-2000:], cand.stderr[-2000:]))
            verdict = cand.stdout.rstrip("\n")
            if verdict == "ACCEPT":
                lines.append("%s:%d  ACCEPT  %s" % (payload["src_name"], lineno, one))
            elif verdict.startswith("REFUSE"):
                lines.append("%s:%d  REFUSE  %s%s" % (payload["src_name"], lineno, one, verdict[len("REFUSE"):]))
            else:
                raise NotExecuted("NOT EXECUTED: candidate produced an unrecognised verdict on line %d: %r"
                                  % (lineno, verdict))
        return "\n".join(lines) + "\n"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _resolve_candidate(candidate):
    """The ticket's own gate line spells the candidate without an extension (`target/release/gw`),
    which `subprocess.Popen` on Windows will not find by itself (no PATHEXT search on a relative/absolute
    path with `shell=False`) -- and as a RELATIVE path it must also resolve against THIS PROCESS's cwd,
    same as any other relative path on the command line. Absolute, `.exe`-suffixed on Windows if that is
    what exists; otherwise returned unchanged so a real "no such candidate" still surfaces as NOT EXECUTED
    rather than a silent wrong path."""
    path = pathlib.Path(candidate)
    if not path.is_absolute():
        path = pathlib.Path.cwd() / path
    if os.name == "nt" and not path.exists():
        with_exe = path.with_name(path.name + ".exe")
        if with_exe.exists():
            path = with_exe
    return str(path)


def diff_config(candidate):
    """`--config`: the candidate's `gw config spec <instance>` against every committed
    `goldens/config/<instance>.spec.json`, plus `gw config check` against every `tests/test_config.py`
    bad-config fixture. Byte-exact, via the same `diff_trees` every other golden tree uses."""
    verify_oracle()
    candidate = _resolve_candidate(candidate)
    if not pathlib.Path(candidate).is_file():
        raise NotExecuted("NOT EXECUTED: no candidate binary at %s" % candidate)
    work = pathlib.Path(tempfile.mkdtemp(prefix="gw_config_out_"))
    try:
        cfg_dir = work / "config"
        cfg_dir.mkdir()
        problems = []
        for inst_dir in _config_instance_dirs():
            yaml_path = inst_dir / "instance.yaml"
            run = subprocess.run([candidate, "config", "spec", str(yaml_path)], capture_output=True, text=True)
            if run.returncode != 0:
                problems.append("CANDIDATE FAILED `config spec` on %s: rc=%s\n%s\n%s"
                               % (inst_dir.name, run.returncode, run.stdout[-1000:], run.stderr[-1000:]))
                continue
            text = run.stdout if run.stdout.endswith("\n") else run.stdout + "\n"
            with open(cfg_dir / ("%s.spec.json" % inst_dir.name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        with open(cfg_dir / "bad_configs.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_candidate_bad_config_text(candidate))
        return problems + diff_trees(GOLDENS / "config", cfg_dir, "committed", "candidate")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def child_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ORACLE_DIR)
    env["PYTHONHASHSEED"] = "0"          # sets never reorder between two recordings
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def record_goldens(out_dir):
    """Emit every golden into `out_dir` through a child process on the oracle.

    Verifies the oracle itself (fix round, blocking finding 2): every path that emits goldens -- the
    CLI, and the CONTROL recordings inside --self-test -- checks the pinned tag, not just the first
    caller in the chain."""
    verify_oracle()
    out_dir = pathlib.Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    run = subprocess.run([sys.executable, str(HERE / "differential.py"), "--_emit", str(out_dir)],
                         env=child_env(), capture_output=True, text=True, cwd=str(ORACLE_DIR))
    if run.returncode != 0:
        raise NotExecuted("NOT EXECUTED: emitting goldens failed:\n%s\n%s" % (run.stdout[-3000:], run.stderr[-3000:]))
    write_manifest(out_dir)
    return out_dir


def golden_files(root):
    root = pathlib.Path(root)
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256"
                  and not p.name.startswith("transcript-"))


def write_manifest(root):
    root = pathlib.Path(root)
    lines = ["%s  %s" % (hashlib.sha256(p.read_bytes()).hexdigest(), p.relative_to(root).as_posix())
             for p in golden_files(root)]
    with open(root / "MANIFEST.sha256", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def verify_manifest(root):
    """MANIFEST.sha256 against the golden files actually on disk under `root`. Minor finding (fix
    round): the manifest was written and committed but never checked -- only its existence -- so it
    looked like an integrity control and was not one. Returns a list of differences, empty if none."""
    root = pathlib.Path(root)
    manifest = root / "MANIFEST.sha256"
    if not manifest.exists():
        return ["%s is missing" % manifest]
    recorded = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        recorded[rel] = digest
    actual = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in golden_files(root)}
    out = []
    for rel in sorted(set(recorded) - set(actual)):
        out.append("MANIFEST lists missing file: %s" % rel)
    for rel in sorted(set(actual) - set(recorded)):
        out.append("MANIFEST is missing a file that exists: %s" % rel)
    for rel in sorted(set(recorded) & set(actual)):
        if recorded[rel] != actual[rel]:
            out.append("MANIFEST hash mismatch for %s: manifest %s != actual %s" % (rel, recorded[rel], actual[rel]))
    return out


def diff_trees(left, right, left_name="oracle", right_name="candidate"):
    """Byte-for-byte. Returns a list of differences, each naming the file and the first bad byte."""
    left, right = pathlib.Path(left), pathlib.Path(right)
    lrel = {p.relative_to(left).as_posix() for p in golden_files(left)}
    rrel = {p.relative_to(right).as_posix() for p in golden_files(right)}
    out = []
    for rel in sorted(lrel - rrel):
        out.append("MISSING in %s: %s" % (right_name, rel))
    for rel in sorted(rrel - lrel):
        out.append("EXTRA in %s: %s" % (right_name, rel))
    for rel in sorted(lrel & rrel):
        a, b = (left / rel).read_bytes(), (right / rel).read_bytes()
        if a == b:
            continue
        i = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
        line = a[:i].count(b"\n") + 1
        out.append("DIFFERS %s at byte %d (line %d): %s %r != %s %r"
                   % (rel, i, line, left_name, a[i:i + 24], right_name, b[i:i + 24]))
    return out


# ---------------------------------------------------------------------------------------------
# --split: db.py's `_split_statements`, byte for byte, against a Rust candidate's own splitter
# ---------------------------------------------------------------------------------------------
# port-04 (`_tickets/2026-09-18-groundworks-port-04-db-client.md`) ports db.py's text functions
# (`_strip_comments` / `_split_statements`) to `gw_core::db`. This is the one piece of that port the
# owner's gate checks directly, on real input: every `.surql` file the oracle ships, plus every
# fixture `tests/test_db.py`'s `SplitTests` feeds to `_split_statements` (read out by AST, so a new
# fixture is picked up without a copy that could drift).

# ADVERSARIAL SPLIT CORPUS (fix round on `2026-09-18-groundworks-port-04-db-client`, blocking finding
# B3). The 4 fixtures above (2 shipped .surql files + 2 test_db.py SplitTests cases) contain no
# double-quoted string, no paren, and no unbalanced bracket -- shallow enough that 4 of 9 plausible
# WRONG ports of `_split_statements` pass the gate anyway (a reviewer built and ran them: "only
# single quotes are strings", "depth == 0 instead of depth <= 0", "parens do not nest", "any quote
# closes any quote"). Every case below targets one class of thing a candidate splitter can get wrong.
# This corpus is the PRECEDENT for ports 01-09 (see groundworks/docs/RUST_PORT.md section 5): copy
# the pattern -- deepen with real adversarial inputs, then prove each class of wrong implementation
# actually goes RED, not just "the case exists".
#
# Every text here is run through the SAME oracle `_split_statements` as the shipped fixtures (via
# `_emit_split` in the child process below), so "what the right answer is" is never hand-computed --
# only the INPUTS are hand-picked to stress a specific failure mode.
_ADVERSARIAL_SPLIT_CASES = [
    # -- empty / line-boundary edge cases ------------------------------------------------------
    ("adversarial::empty", ""),
    ("adversarial::single_newline", "\n"),
    ("adversarial::no_trailing_newline", "SELECT 1; SELECT 2"),
    ("adversarial::trailing_newline", "SELECT 1;\nSELECT 2;\n"),
    ("adversarial::double_trailing_newline", "SELECT 1;\nSELECT 2;\n\n"),
    ("adversarial::crlf", "SELECT 1;\r\nSELECT 2;\r\n"),
    ("adversarial::lone_cr", "SELECT 1;\rSELECT 2;\r"),
    ("adversarial::mixed_line_endings", "SELECT 1;\nSELECT 2;\r\nSELECT 3;\rSELECT 4;\n"),
    ("adversarial::only_semicolons", ";;;"),
    ("adversarial::empty_statements_between_semicolons", ";;SELECT 1;;;SELECT 2;;"),
    # -- Python's other str.splitlines() boundaries (B1: each one ENDS a `--` comment) ---------
    ("adversarial::comment_closed_by_vertical_tab", "SELECT 1; -- c\x0bSELECT 2;\n"),
    ("adversarial::comment_closed_by_form_feed", "SELECT 1; -- c\x0cSELECT 2;\n"),
    ("adversarial::comment_closed_by_file_separator", "SELECT 1; -- c\x1cSELECT 2;\n"),
    ("adversarial::comment_closed_by_group_separator", "SELECT 1; -- c\x1dSELECT 2;\n"),
    ("adversarial::comment_closed_by_record_separator", "SELECT 1; -- c\x1eSELECT 2;\n"),
    ("adversarial::comment_closed_by_nel", "SELECT 1; -- cSELECT 2;\n"),
    ("adversarial::comment_closed_by_line_separator", "SELECT 1; -- c SELECT 2;\n"),
    ("adversarial::comment_closed_by_paragraph_separator", "SELECT 1; -- c SELECT 2;\n"),
    ("adversarial::comment_at_end_no_newline", "SELECT 1;\n--"),
    # -- quotes: which characters are strings, and what closes them ---------------------------
    ("adversarial::double_quoted_semicolon", 'SELECT "a;b" FROM x; SELECT 2;'),
    ("adversarial::double_quoted_comment_marker", 'SELECT "a--b" FROM x; SELECT 2;'),
    ("adversarial::single_quoted_comment_marker", "SELECT 'a--b' FROM x; SELECT 2;"),
    ("adversarial::unterminated_single_quote", "SELECT 'a;b FROM x; SELECT 2;"),
    ("adversarial::unterminated_double_quote", 'SELECT "a;b FROM x; SELECT 2;'),
    ("adversarial::quote_spans_lines", "SELECT 'a\nb;c' FROM x; SELECT 2;"),
    ("adversarial::double_quote_spans_lines", 'SELECT "a\nb;c" FROM x; SELECT 2;'),
    ("adversarial::mixed_quote_types_adjacent", "SELECT 'a', \"b;c\", 'd' FROM x; SELECT 2;"),
    ("adversarial::single_quote_containing_double_quote", "SELECT 'it\"s fine'; SELECT 2;"),
    ("adversarial::double_quote_containing_single_quote", 'SELECT "it\'s fine"; SELECT 2;'),
    ("adversarial::dash_not_comment", "SELECT 1 - 2; SELECT 3;"),
    ("adversarial::single_dash_before_quote", "SELECT 1 -'a'; SELECT 2;"),
    # SurQL text has no backslash-escaping (db.py's quote tracking is naive: ANY occurrence of the
    # open quote character closes it, backslash or no); a candidate that assumes a backslash escapes
    # the following quote (a plausible habit carried over from a language that does) keeps the quote
    # open past where the oracle closes it. That is only visible once something AFTER the
    # backslash-quote depends on quote state -- here, a `--` on the same line that the oracle treats
    # as a real comment (the quote already closed) but the mutant still thinks is inside the string
    # (so it survives into the statement text instead of being stripped).
    ("adversarial::backslash_before_closing_quote", "SELECT 'a\\' -- comment\nSELECT 2;"),
    ("adversarial::backslash_before_closing_double_quote", 'SELECT "a\\" -- comment\nSELECT 2;'),
    # -- brace/bracket/paren depth: nesting, unmatched, and negative depth ---------------------
    ("adversarial::nested_parens", "SELECT (1, (2, (3;4), 5)); SELECT 2;"),
    # A wrong splitter that treats parens as a single non-nesting flag (rather than counting) closes
    # the OUTER paren as soon as the INNER one closes; the semicolon right after that inner close then
    # looks like it is at depth 0 (so it wrongly splits) when the real depth is still 1 (still inside
    # the outer paren, so the oracle does not split there).
    ("adversarial::nested_parens_inner_close_then_semicolon", "SELECT (1, (2), 3;4); SELECT 5;"),
    ("adversarial::nested_mixed_brackets", "UPDATE x SET f = {a: [1, (2;3), {b: 4}], c: 'd;e'}; SELECT 1;"),
    ("adversarial::unmatched_closer", "SELECT 1); SELECT 2;"),
    ("adversarial::unmatched_opener", "SELECT (1; SELECT 2;"),
    ("adversarial::depth_goes_negative_then_more_semicolons", "SELECT 1)); SELECT 2; SELECT 3;"),
    ("adversarial::bracket_then_paren_unbalanced", "SELECT [1); SELECT 2;"),
    # -- unicode content, in strings, comments and identifiers ---------------------------------
    ("adversarial::unicode_in_string", "SELECT 'héllo wörld ☺' FROM x; SELECT 2;"),
    ("adversarial::unicode_in_comment_before_semicolon",
     "SELECT 1; -- ünïcödé café \U0001f389 comment; with a semicolon\nSELECT 2;"),
    ("adversarial::astral_plane_emoji", "SELECT '\U0001f600\U0001f389'; SELECT 2;"),
]


def _oracle_split_texts():
    """[(name, text), ...] for every input the split gate compares: one entry per `.surql` file the
    oracle ships (there are two at v0.2.0, both under `groundworks/patterns/`), one entry per
    `text = ...` fixture inside `tests/test_db.py`'s `SplitTests` class, and the adversarial corpus
    above (empty/line-boundary edges, every quote/comment interaction, brace/bracket/paren depth
    including unmatched and negative, and unicode) that a shallow candidate splitter can get right on
    the shipped fixtures alone while still being wrong -- see the comment on `_ADVERSARIAL_SPLIT_CASES`."""
    entries = []
    for path in sorted(ORACLE_DIR.rglob("*.surql")):
        entries.append((path.relative_to(ORACLE_DIR).as_posix(), path.read_text(encoding="utf-8")))
    src_path = ORACLE_DIR / "tests" / "test_db.py"
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SplitTests":
            for item in node.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                for stmt in item.body:
                    if isinstance(stmt, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "text" for t in stmt.targets
                    ):
                        entries.append(("tests/test_db.py::%s" % item.name, ast.literal_eval(stmt.value)))
    entries.extend(_ADVERSARIAL_SPLIT_CASES)
    if not entries:
        raise NotExecuted("NOT EXECUTED: found no .surql files and no SplitTests fixtures to split")
    return entries


def _emit_split(work_dir):
    """Runs INSIDE the child, with the oracle importable: writes `out.json`, a {name: [statements]}
    map from db.py's own `_split_statements`, for the entries `run_split` wrote to `in.json`."""
    from groundworks import db as dbmod
    work_dir = pathlib.Path(work_dir)
    entries = json.loads((work_dir / "in.json").read_text(encoding="utf-8"))
    result = {name: dbmod._split_statements(text) for name, text in entries}
    (work_dir / "out.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def _resolve_candidate(candidate):
    """A candidate path that `subprocess.run` can actually launch. On Windows, CreateProcess does not
    search PATH or append an extension for a relative path with no suffix (`target/release/gw`, the
    cargo build output and this ticket's own gate command) -- `.exe` is tried explicitly, absolute or
    not, before giving up."""
    path = pathlib.Path(candidate)
    if path.exists():
        return str(path.resolve())
    if os.name == "nt" and path.suffix == "":
        with_exe = path.with_suffix(".exe")
        if with_exe.exists():
            return str(with_exe.resolve())
    raise NotExecuted("NOT EXECUTED: candidate binary not found: %s" % candidate)


def run_split(candidate):
    """Byte-identical statement splitting: the oracle's `_split_statements` (run in a child on the
    pinned checkout) against `<candidate> --split-statements` (fed each text on stdin, expected to
    print a JSON array of strings on stdout) -- one comparison per entry from `_oracle_split_texts`.
    Returns a list of differences, empty if none."""
    candidate = _resolve_candidate(candidate)
    entries = _oracle_split_texts()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gw_split_"))
    try:
        (tmp / "in.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        run = subprocess.run([sys.executable, str(HERE / "differential.py"), "--_split", str(tmp)],
                             env=child_env(), cwd=str(ORACLE_DIR), capture_output=True, text=True)
        if run.returncode != 0:
            raise NotExecuted("NOT EXECUTED: the oracle's own split failed:\n%s\n%s"
                              % (run.stdout[-2000:], run.stderr[-2000:]))
        oracle_result = json.loads((tmp / "out.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    out = []
    for name, text in entries:
        # bytes in, bytes out: `text=True` would have subprocess re-translate "\n" to the platform
        # line separator on the way into the candidate's stdin (os.linesep == "\r\n" on Windows),
        # which is not what `dbmod._split_statements` ever saw in-process -- it silently added a
        # byte the oracle side never had. Measured: it turned identical splits into a false MISSING
        # on every .surql fixture (an extra "\r" in the first statement of both shipped patterns).
        proc = subprocess.run([candidate, "--split-statements"], input=text.encode("utf-8"),
                              capture_output=True)
        if proc.returncode != 0:
            out.append("%s: candidate exited %d: %s"
                       % (name, proc.returncode, proc.stderr.decode("utf-8", "replace")[-500:]))
            continue
        try:
            candidate_parts = json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            out.append("%s: candidate did not print a JSON array of statements: %r (%s)"
                       % (name, proc.stdout[:300], exc))
            continue
        oracle_parts = oracle_result[name]
        if candidate_parts == oracle_parts:
            continue
        i = next((i for i in range(min(len(candidate_parts), len(oracle_parts)))
                 if candidate_parts[i] != oracle_parts[i]), min(len(candidate_parts), len(oracle_parts)))
        oracle_at = oracle_parts[i] if i < len(oracle_parts) else "<missing>"
        candidate_at = candidate_parts[i] if i < len(candidate_parts) else "<missing>"
        out.append("%s: statement %d of %d/%d differs\n  oracle:    %r\n  candidate: %r"
                   % (name, i, len(oracle_parts), len(candidate_parts), oracle_at, candidate_at))
    return out


# ---------------------------------------------------------------------------------------------
# the transcript: one seeded single-agent MCP session against the oracle front door
# ---------------------------------------------------------------------------------------------

HARNESS_LENS = "lens_security"          # one of the demo instance's lenses (instance.yaml `lenses:`)
PANEL_ROOT = pathlib.Path(tempfile.gettempdir()) / "gw_harness_panel_root"   # FIXED, never mkdtemp

# ROUND-2 BLOCKING FINDING 2, the fix. An EMPTY directory that the harness puts on the server's PATH in
# place of the machine's, so the engine's git probe (`code_state.read_state`) answers the same constant on
# every machine instead of a 15-second timeout that only this one produces. See PINNED_ENGINE_STATE below
# and docs/RUST_PORT.md section 4 for the measurement that justifies it.
NO_TOOLS_DIR = pathlib.Path(tempfile.gettempdir()) / "gw_harness_no_tools"

# What `code_state.read_state` MUST report for the server process, on any machine, given the PATH pin.
# `record_transcript` refuses to write a golden that says anything else, so a machine where the pin fails
# cannot bake its own environment into the gate.
PINNED_ENGINE_STATE = ("UNKNOWN", "git is not installed or not on PATH")

FIXED_ENV = {
    # Every launch variable server.py reads (server.py:11-24) PINNED TO A CONSTANT -- pinned, never
    # dropped. Round-2 blocking finding 4: GROUNDWORKS_ALLOW_UNVERIFIED was *removed* from this dict
    # instead of being pinned, and `child_env()` is `dict(os.environ)`, so an operator shell that
    # happened to export GROUNDWORKS_ALLOW_UNVERIFIED=1 launched the server with writes ALLOWED BY
    # OVERRIDE and the recording changed under the gate. code_state.py:133 reads it as == "1".
    "GROUNDWORKS_ADAPTER": "groundworks.mcp.instance_adapter",
    "GROUNDWORKS_AGENT_ID": "harness_agent",
    "GROUNDWORKS_ROLE": "coordinator",
    "GROUNDWORKS_LENS": HARNESS_LENS,
    "GROUNDWORKS_ALLOW_UNVERIFIED": "0",
    "GROUNDWORKS_DB_USER": "root",           # instance_adapter.py:440, defaulted -- pinned, not defaulted
    "GROUNDWORKS_DB_PASS": "root",           # instance_adapter.py:440
    "TZ": "UTC",
    # The PATH pin (blocking finding 2). Nothing the server does shells out except code_state's git probe
    # (checked: no `subprocess` in db.py, originals.py or the demo adapter), and it launches from an
    # absolute sys.executable, so an empty PATH costs the server nothing and removes the one timing-
    # dependent input from the recording.
    "PATH": str(NO_TOOLS_DIR),
    "Path": str(NO_TOOLS_DIR),               # Windows spells it either way; pin both spellings
}

# Every GROUNDWORKS_* the SERVER PROCESS is allowed to see. Anything else inherited from the operator's
# shell is stripped before launch (blocking finding 4's class, not just its one instance): the launch
# environment is an INPUT to the gate, so it is a closed set, not "whatever was exported".
PINNED_SERVER_ENV_KEYS = frozenset(FIXED_ENV) | {
    "GROUNDWORKS_INSTANCE", "GROUNDWORKS_DB_URL", "GROUNDWORKS_NS", "GROUNDWORKS_DB", "GROUNDWORKS_PANEL_ROOT",
}
# GROUNDWORKS_DB_ACCESS is deliberately ABSENT, not empty: instance_adapter.py:441/443 reads `.get()` and
# treats a set-but-empty value differently from unset (it selects the provisioned-agent credential path).
# GROUNDWORKS_SURREAL (surreal_bin.py:22) and GROUNDWORKS_EVAL_PORT (cli.py:128) are read only by the
# HARNESS CHILD, never by the server; the datastore binary they select is part of what the gate compares
# (db.py's 3.1.x version check), so they are left to the machine and named here so the audit is complete.

# Rule (5), ADDED 2026-09-19 with its reason: absolute paths the HARNESS chose at launch (the oracle
# checkout and the panel root) appear verbatim in refusal texts. They are the harness's own inputs, not
# the engine's output, and they differ between machines and checkouts, so they are substituted at RECORD
# time -- identically on both sides -- and counted.
def path_tokens():
    return [(str(ORACLE_DIR), "<ORACLE_DIR>"), (str(PANEL_ROOT), "<PANEL_ROOT>")]


def detokenize_paths(text):
    """Both spellings of each path (forward and backslashes, and the JSON-escaped backslash form)."""
    count = 0
    for real, token in path_tokens():
        for spelling in (real, real.replace("/", "\\"), real.replace("\\", "/"),
                         real.replace("/", "\\").replace("\\", "\\\\")):
            if spelling and spelling in text:
                count += text.count(spelling)
                text = text.replace(spelling, token)
    return text, count


# The write tools this call script exercises (server.py WRITE_TOOLS is a superset; these are the ones
# the CALL_SCRIPT below actually calls). Used by the `engine_guard_dropped` red arm.
WRITE_TOOLS_IN_SCRIPT = ("critique_add", "critique_answer", "panel_submit")

# The fixed call script over the v0.2.0 tool surface. Order and arguments are part of the gate.
CALL_SCRIPT = [
    ("health", {"detail": False}),
    ("unit_search", {"query": "pool", "limit": 5}),
    ("unit_search", {"query": "zzzz_no_such_words", "limit": 5}),
    ("unit_search", {"query": "pool", "limit": 0}),
    ("unit_get", {"locator": "log_0043"}),
    ("unit_get", {"locator": "no_such_unit_0001"}),
    ("claim_explain", {"claim_id": "rc_pool_exhaustion"}),
    ("claim_explain", {"claim_id": "no_such_claim"}),
    ("eliminated_claims", {}),
    ("document_get", {"ref": "chg_0001"}),
    ("document_get", {"ref": "chg_0001", "include_content": False}),
    ("critique_add", {"target": "rc_pool_exhaustion", "head": "harness head one",
                      "verdict": "survivable", "text": "a fixed critique from the differential harness"}),
    ("critique_add", {"target": "rc_pool_exhaustion", "head": "harness head one",
                      "verdict": "fatal", "text": "the same head restated: one row, new verdict"}),
    ("critique_add", {"target": "rc_pool_exhaustion", "head": "harness head two", "verdict": "fatal",
                      "lens": "lens_management", "text": "a lens other than the launch lens is refused"}),
    ("critique_add", {"target": "no_such_conclusion", "head": "h", "verdict": "fatal"}),
    ("critique_add", {"target": "rc_pool_exhaustion", "head": "", "verdict": "fatal"}),
    ("claim_explain", {"claim_id": "rc_pool_exhaustion"}),
    # <CRITIQUE_ID> is filled at record time with the id the ORACLE would derive for the critique
    # added above (write.py:143 via patterns/edge_ids.py:27), computed offline the same way the
    # engine does, independent of whether the critique_add call above landed. Fix round: with the code
    # guard no longer overridden, this critique_add is refused rejected_by_gate (round 1: the checkout is a
    # detached-HEAD clone; round 2 and onwards: the PATH pin makes the engine state UNKNOWN, which refuses
    # writes for a reason that is the same on every machine), so this call is refused too, before the id is
    # even looked up
    # -- it is NOT the derived-id check any more. That check now lives in the `transcript_derived_id`
    # red arm, against the critique the fixture's OWN build scenario (a read, never gated) already
    # attacked rc_pool_exhaustion with (instances/demo-incident/adapter.py:157).
    ("critique_answer", {"critique": "<CRITIQUE_ID>", "answer": "the harness answers its own head"}),
    ("critique_answer", {"critique": "critique:no_such_critique", "answer": "x"}),
    ("panel_submit", {"run_dir": "no_such_run", "round": 1, "seat": "seat_security", "text": "x"}),
    ("panel_status", {"run_dir": "no_such_run"}),
    ("document_get", {"ref": "chg_0001"}),
    ("health", {"detail": False}),
]


def canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _record_transcript(out_path):
    """Runs INSIDE the child: throwaway DB -> build the demo graph -> drive the front door -> JSONL.

    The datastore is started and stopped BY PID (`surreal_bin.start_memory` / `.stop`); nothing is ever
    killed by name."""
    import asyncio
    from groundworks import surreal_bin
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    binary = surreal_bin.find()
    if not binary:
        raise NotExecuted("NOT EXECUTED: " + surreal_bin.MISSING)
    port = surreal_bin.free_port()
    proc = surreal_bin.start_memory(port, binary=binary)
    print("surreal pid=%d port=%d (this harness owns it and stops it by PID)" % (proc.pid, port))
    url = "http://127.0.0.1:%d/sql" % port
    try:
        build = subprocess.run([sys.executable, "scripts/run_instance.py", "instances/demo-incident",
                                "--url", url, "--ns", "demo", "--db", "demo"],
                               cwd=str(ORACLE_DIR), env=child_env(), capture_output=True, text=True, timeout=1800)
        if build.returncode != 0:
            raise NotExecuted("NOT EXECUTED: the demo graph did not build:\n%s\n%s"
                              % (build.stdout[-3000:], build.stderr[-2000:]))

        from groundworks.patterns import edge_ids
        critique_id = "critique:rc_pool_exhaustion__%s__%s" % (
            HARNESS_LENS, edge_ids.edge_id("critique", "head", "rc_pool_exhaustion", "harness head one")[-8:])

        panel_root = PANEL_ROOT
        shutil.rmtree(panel_root, ignore_errors=True)
        panel_root.mkdir(parents=True, exist_ok=True)
        env = child_env()
        env.update(FIXED_ENV)
        env["GROUNDWORKS_INSTANCE"] = str(ORACLE_DIR / "instances" / "demo-incident" / "instance.yaml")
        env["GROUNDWORKS_DB_URL"] = url
        env["GROUNDWORKS_NS"] = "demo"
        env["GROUNDWORKS_DB"] = "demo"
        env["GROUNDWORKS_PANEL_ROOT"] = str(panel_root)
        # blocking finding 4: pin the closed set, then STRIP every other GROUNDWORKS_* the operator's
        # shell may have exported (GROUNDWORKS_DB_ACCESS was the only one handled before, by name).
        for key in [k for k in env if k.startswith("GROUNDWORKS_") and k not in PINNED_SERVER_ENV_KEYS]:
            env.pop(key)
        NO_TOOLS_DIR.mkdir(parents=True, exist_ok=True)     # empty, and stays empty: see the PATH pin

        async def drive():
            params = StdioServerParameters(command=sys.executable, args=["-m", "groundworks.mcp.server"],
                                           env=env, cwd=str(ORACLE_DIR))
            rows = []
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    rows.append({"n": 0, "tool": "_tools", "args": {},
                                 "envelope": {"tools": sorted(t.name for t in listed.tools)}})
                    for n, (tool, args) in enumerate(CALL_SCRIPT, start=1):
                        args = {k: (critique_id if v == "<CRITIQUE_ID>" else v) for k, v in args.items()}
                        result = await session.call_tool(tool, args)
                        texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
                        try:
                            envelope = json.loads(texts[0]) if texts else {"_no_content": True}
                        except json.JSONDecodeError:
                            envelope = {"_unparsed": texts[0] if texts else ""}
                        rows.append({"n": n, "tool": tool, "args": args, "envelope": envelope})
            return rows

        rows = asyncio.run(drive())
        substituted = 0
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                blob, n_paths = detokenize_paths(canon(row["envelope"]))     # rule (5), at RECORD time
                substituted += n_paths
                row["envelope"] = json.loads(blob)
                row["bytes"] = len(canon(row["envelope"]).encode("utf-8"))
                fh.write(canon({"n": row["n"], "tool": row["tool"], "args": row["args"],
                                "bytes": row["bytes"], "envelope": row["envelope"]}) + "\n")
        print("launch paths substituted (rule 5): %d" % substituted)
        shutil.rmtree(panel_root, ignore_errors=True)
    finally:
        surreal_bin.stop(proc)
        alive = proc.poll() is None
        print("surreal pid=%d stopped=%s still_listening=%s" % (proc.pid, not alive, surreal_bin.port_in_use(port)))
        if alive or surreal_bin.port_in_use(port):
            raise NotExecuted("NOT EXECUTED: the harness left a surreal running (pid %d)" % proc.pid)


def engine_state_in(path):
    """(kind, reason) of `engine_code.on_disk_now` as the recorded health call reports it."""
    for raw in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        state = (row.get("envelope") or {}).get("data", {}).get("engine_code", {}).get("on_disk_now")
        if isinstance(state, dict):
            return state.get("kind"), state.get("reason")
    return None, None


def record_transcript(out_path):
    """Same verification requirement as `record_goldens` (fix round, blocking finding 2), and -- round-2
    blocking finding 2 -- a REFUSAL rather than a golden when the engine state the recording captured is not
    the pinned constant. Without this a machine where the git probe behaves differently (this one times out
    at code_state.GIT_TIMEOUT_S=15 inside the server; see docs/RUST_PORT.md section 4) silently bakes its own
    environment into `verdict`, `problems`, `missing` and the nine refusal strings, and the next machine's
    CONTROL goes red for a reason that has nothing to do with a candidate."""
    verify_oracle()
    run = subprocess.run([sys.executable, str(HERE / "differential.py"), "--_transcript", str(out_path)],
                         env=child_env(), cwd=str(ORACLE_DIR))
    if run.returncode != 0:
        raise NotExecuted("NOT EXECUTED: recording the transcript failed (see output above)")
    kind, reason = engine_state_in(out_path)
    if (kind, reason) != PINNED_ENGINE_STATE:
        raise NotExecuted("NOT EXECUTED: the recording captured engine_code.on_disk_now = %r/%r, not the "
                          "pinned %r/%r. The PATH pin (FIXED_ENV) did not take effect, so `verdict`, "
                          "`problems`, `missing` and every refusal string in this recording describe THIS "
                          "machine. Refusing to write it as a golden." % ((kind, reason) + PINNED_ENGINE_STATE))
    return pathlib.Path(out_path)


# ---------------------------------------------------------------------------------------------
# the mask (transcript only): three rules, each counted
# ---------------------------------------------------------------------------------------------

def _is_datetime(value):
    if not isinstance(value, str):
        return False
    text = value.strip().replace("Z", "+00:00")
    try:
        _dt.datetime.fromisoformat(text)
        return True
    except ValueError:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2})?", value.strip()))


def mask_envelope(node, counts, in_engine_code=False):
    """Rules (1), (2) and the narrowed (4), applied by FIELD NAME. `in_engine_code` is true only while
    recursing INSIDE an `engine_code` subtree, so rule 4's leaf names cannot shadow an unrelated field
    with the same name elsewhere in the envelope (fix round, blocking finding 1: the whole subtree used
    to be replaced by one token, which also hid `override`/`checked`/the key set). Returns a new
    object."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key in DATETIME_FIELDS and _is_datetime(value):
                counts["datetime"] = counts.get("datetime", 0) + 1
                out[key] = "<datetime>"
            elif key in RANDOM_ID_FIELDS and isinstance(value, str):
                counts["random_id"] = counts.get("random_id", 0) + 1
                out[key] = "<id>"
            elif key in RUN_TOKEN_FIELDS and isinstance(value, str) and re.fullmatch(r"r\d{10,}", value):
                counts["run_token"] = counts.get("run_token", 0) + 1
                out[key] = "<run>"
            elif key in ENGINE_CODE_WHOLE_STRING_FIELDS or (key == "WARNING" and isinstance(value, str)
                                               and value.startswith(ENGINE_CODE_WARNING_PREFIX)):
                counts["engine_code"] = counts.get("engine_code", 0) + 1
                out[key] = "<engine_code>"
            elif in_engine_code and key in ENGINE_CODE_VOLATILE_LEAVES:
                counts["engine_code"] = counts.get("engine_code", 0) + 1
                out[key] = "<engine_code>"
            elif key in ENGINE_CODE_CONTAINERS:
                out[key] = mask_envelope(value, counts, in_engine_code=True)
            else:
                out[key] = mask_envelope(value, counts, in_engine_code=in_engine_code)
        return out
    if isinstance(node, list):
        return [mask_envelope(item, counts, in_engine_code=in_engine_code) for item in node]
    return node


class MaskError(Exception):
    pass


def mask_transcript(path):
    """(masked lines, per-rule counts). Rule (3) is enforced here: `bytes` must equal this side's own
    unmasked envelope length, and is then dropped.

    Rule (5) is counted here too (fix round, blocking finding 3 family): both sides already had their
    launch paths replaced by `<ORACLE_DIR>` / `<PANEL_ROOT>` at RECORD time, so recounting those tokens
    in the file gives a comparable `path` key in the same counts dict as every other rule -- previously
    `path` had no key at all (RUST_PORT.md:126's "the two sides' counts must be equal" was false for it)."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    counts = {"datetime": 0, "random_id": 0, "run_token": 0, "engine_code": 0, "bytes_checked": 0,
             "path": text.count("<ORACLE_DIR>") + text.count("<PANEL_ROOT>")}
    lines = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        declared = row.pop("bytes", None)
        actual = len(canon(row["envelope"]).encode("utf-8"))
        if declared != actual:
            raise MaskError("%s line %d (call %s/%s): bytes says %r, the unmasked envelope is %d"
                            % (pathlib.Path(path).name, lineno, row.get("n"), row.get("tool"), declared, actual))
        counts["bytes_checked"] += 1
        row["envelope"] = mask_envelope(row["envelope"], counts)
        lines.append(canon(row))
    return lines, counts


def mask_counts_disagreement(measured, committed):
    """THE comparison of measured mask counts against the committed masks.json, in one place.

    ROUND-2 BLOCKING FINDING 3: the `masks_json_disagrees` red arm used to tamper a copy of the counts dict
    and then RETURN A HAND-WRITTEN FAILURE STRING -- it called no production code, so deleting the real
    check left the arm still printing "RED (good)". The CONTROL, `--record-transcript`'s refusal and that
    arm now all go through this function and `promote_transcript` below, so deleting either use turns the
    arm GREEN. That is what the arm is for."""
    if committed is None or measured == committed:
        return []
    return ["recorded mask counts %s != committed %s" % (canon(measured), canon(committed))]


def promote_transcript(recorded, dest, masks_path, force=False):
    """Move a freshly recorded transcript and its counts into place -- or REFUSE, writing NOTHING.

    Returns (counts, failures). ROUND-2 MINOR (b): the refusal used to fire after `record_transcript` had
    already overwritten the committed transcript, leaving a new transcript beside an old masks.json that
    needed a `git checkout` to recover. Recording now happens to a temporary path and is promoted only when
    the counts agree."""
    recorded, dest, masks_path = pathlib.Path(recorded), pathlib.Path(dest), pathlib.Path(masks_path)
    _, counts = mask_transcript(recorded)
    committed = json.loads(masks_path.read_text(encoding="utf-8")) if masks_path.exists() else None
    bad = [] if force else mask_counts_disagreement(counts, committed)
    if bad:
        return counts, ["REFUSED: %s (%s). Nothing was written. Re-run with --force-masks only after "
                        "confirming the disagreement is expected." % (bad[0], masks_path.name)]
    shutil.copyfile(str(recorded), str(dest))
    with open(masks_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(counts, indent=2, sort_keys=True) + "\n")
    return counts, []


def diff_transcripts(left, right, left_name="oracle", right_name="candidate"):
    """Differences after the mask. The first differing line names its tool call."""
    lmask, lcounts = mask_transcript(left)
    rmask, rcounts = mask_transcript(right)
    out = []
    for rule in sorted(set(lcounts) | set(rcounts)):
        if lcounts.get(rule, 0) != rcounts.get(rule, 0):
            out.append("MASK COUNT %s: %s %d != %s %d"
                       % (rule, left_name, lcounts.get(rule, 0), right_name, rcounts.get(rule, 0)))
    for i in range(max(len(lmask), len(rmask))):
        a = lmask[i] if i < len(lmask) else None
        b = rmask[i] if i < len(rmask) else None
        if a == b:
            continue
        who = json.loads(a or b)
        out.append("TRANSCRIPT line %d differs, call %s %s\n  %s: %s\n  %s: %s"
                   % (i + 1, who.get("n"), who.get("tool"), left_name, (a or "<missing>")[:600],
                      right_name, (b or "<missing>")[:600]))
        break
    return out, lcounts, rcounts


# ---------------------------------------------------------------------------------------------
# the self-test: CONTROL first, twice; then every red arm, by name
# ---------------------------------------------------------------------------------------------

def _plant(path, find, replace):
    text = path.read_text(encoding="utf-8")
    if find not in text:
        raise NotExecuted("NOT EXECUTED: the red arm could not plant %r in %s" % (find, path.name))
    path.write_text(text.replace(find, replace, 1), encoding="utf-8", newline="\n")


def self_test(tag=ORACLE_TAG):
    head = verify_oracle(tag)
    print("ORACLE  %s = %s at %s" % (tag, head[:12], ORACLE_DIR))
    if not MANIFEST.exists():
        raise NotExecuted("NOT EXECUTED: no goldens are committed. Run --record-goldens first.")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gw_diff_"))
    failures, arms = [], []
    try:
        # ---- CONTROL, first and twice: the oracle against itself -------------------------------
        first = record_goldens(tmp / "first")
        second = record_goldens(tmp / "second")
        n = len(golden_files(first))
        for name, other in (("run1 vs run2", second), ("run1 vs committed", GOLDENS)):
            bad = diff_trees(first, other, "run1", name.split(" vs ")[1])
            print("CONTROL goldens %-18s %d file(s): %s" % (name, n, "IDENTICAL" if not bad else "DIFFER"))
            failures += ["CONTROL %s: %s" % (name, d) for d in bad[:5]]
        manifest_bad = verify_manifest(GOLDENS)
        print("CONTROL manifest    %s" % ("VERIFIED" if not manifest_bad else "MISMATCH"))
        failures += ["CONTROL manifest: %s" % d for d in manifest_bad[:5]]
        if not TRANSCRIPT.exists():
            raise NotExecuted("NOT EXECUTED: %s is missing. Run --record-transcript." % TRANSCRIPT.name)
        # blocking finding 3 (fix round): this used to diff the committed file against ITSELF, twice,
        # which cannot fail and proves nothing about reproducibility (gate-discipline: run the CONTROL
        # first, but a control has to be able to fail). Two FRESH recordings, each a throwaway DB and a
        # freshly built demo graph, mirror the goldens CONTROL above: run1 vs run2, and run1 vs the
        # committed golden.
        run1 = record_transcript(tmp / "transcript_run1.jsonl")
        run2 = record_transcript(tmp / "transcript_run2.jsonl")
        lc = rc = None
        for name, other in (("run1 vs run2", run2), ("run1 vs committed", TRANSCRIPT)):
            bad, lc, rc = diff_transcripts(run1, other, "run1", name.split(" vs ")[1])
            print("CONTROL transcript %-18s %s, masks %s vs %s"
                 % (name, "IDENTICAL" if not bad else "DIFFER", canon(lc), canon(rc)))
            failures += ["CONTROL transcript %s: %s" % (name, d) for d in bad]
        committed = json.loads(TRANSCRIPT_MASKS.read_text(encoding="utf-8")) if TRANSCRIPT_MASKS.exists() else None
        failures += ["CONTROL transcript: %s" % d for d in mask_counts_disagreement(lc, committed)]

        # ---- RED ARMS: each one must make the gate fail, by name -------------------------------
        def arm(name, run):
            bad = run()
            arms.append((name, bool(bad), (bad[0] if bad else "")))
            print("RED ARM %-28s %s  %s" % (name, "RED (good)" if bad else "GREEN -- THE GATE IS BLIND",
                                            (bad[0].splitlines()[0][:150] if bad else "")))
            if not bad:
                failures.append("RED ARM %s did not go red: the gate cannot see this class of change" % name)

        def surql_one_byte():
            broken = tmp / "arm_surql"
            shutil.copytree(first, broken)
            target = broken / "surql" / "demo-incident" / "gates.surql"
            data = bytearray(target.read_bytes())
            i = next(i for i, ch in enumerate(data) if ch in b"0123456789")
            data[i] = ord("0") if data[i] != ord("0") else ord("1")
            target.write_bytes(bytes(data))
            write_manifest(broken)
            return diff_trees(first, broken)
        arm("surql_one_byte", surql_one_byte)

        def spoil_first_key(name, key, change, where=lambda value: True):
            """Rewrite the transcript, changing the FIRST value of `key` that `where` accepts, and
            nothing else. Returns the broken file and the call it landed on, so the arm can say which
            field it planted rather than "something differs"."""
            state = {"done": False, "call": None, "was": None}

            def walk(node, row):
                if isinstance(node, dict):
                    out = {}
                    for k, v in node.items():
                        if not state["done"] and k == key and where(v):
                            state.update(done=True, call="%s/%s" % (row["n"], row["tool"]), was=v)
                            out[k] = change(v)
                        else:
                            out[k] = walk(v, row)
                    return out
                if isinstance(node, list):
                    return [walk(i, row) for i in node]
                return node

            rows = []
            for raw in TRANSCRIPT.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                row = json.loads(raw)
                row["envelope"] = walk(row["envelope"], row)
                rows.append(canon({"n": row["n"], "tool": row["tool"], "args": row["args"],
                                   # a candidate that is merely WRONG, not lying: its `bytes` matches
                                   # its own envelope, so rule (3) cannot be what catches this
                                   "bytes": len(canon(row["envelope"]).encode("utf-8")),
                                   "envelope": row["envelope"]}))
            if not state["done"]:
                raise NotExecuted("NOT EXECUTED: red arm %s found no %r to plant in the transcript; the "
                                  "call script no longer exercises it" % (name, key))
            broken = tmp / ("arm_%s.jsonl" % name)
            broken.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
            return broken, state

        def transcript_status():
            broken, state = spoil_first_key("transcript_status", "status", lambda v: "ok",
                                            where=lambda v: v in ("not_found", "rejected_by_gate", "invalid_argument"))
            bad = diff_transcripts(TRANSCRIPT, broken)[0]
            return ["(call %s: status %r -> 'ok') %s" % (state["call"], state["was"], d)
                    for d in bad if d.startswith("TRANSCRIPT")]
        arm("transcript_status", transcript_status)

        def transcript_derived_id():
            broken, state = spoil_first_key("transcript_derived_id", "critique", lambda v: v + "_TAMPERED",
                                            where=lambda v: isinstance(v, str) and v.startswith("critique:"))
            bad = diff_transcripts(TRANSCRIPT, broken)[0]
            return ["(call %s: the id the oracle DERIVES, %s) %s" % (state["call"], state["was"], d)
                    for d in bad if d.startswith("TRANSCRIPT")]
        arm("transcript_derived_id", transcript_derived_id)

        def transcript_author():
            broken, state = spoil_first_key("transcript_author", "by", lambda v: "someone_else|role=coordinator",
                                            where=lambda v: isinstance(v, str) and v.startswith("harness_agent"))
            bad = diff_transcripts(TRANSCRIPT, broken)[0]
            return ["(call %s: author %r) %s" % (state["call"], state["was"], d)
                    for d in bad if d.startswith("TRANSCRIPT")]
        arm("transcript_author", transcript_author)

        def spoil_first_datetime(how):
            """Rewrite the transcript, changing the FIRST masked datetime field only.
            how="text" replaces it with a non-datetime; how="drop" removes the field."""
            def walk(node, state):
                if isinstance(node, dict):
                    out = {}
                    for key, value in node.items():
                        if not state["done"] and key in DATETIME_FIELDS and _is_datetime(value):
                            state["done"] = True
                            state["field"] = key
                            if how == "drop":
                                continue
                            out[key] = "not-a-datetime"
                        else:
                            out[key] = walk(value, state)
                    return out
                if isinstance(node, list):
                    return [walk(item, state) for item in node]
                return node

            state = {"done": False, "field": None}
            rows = []
            for raw in TRANSCRIPT.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                row = json.loads(raw)
                row["envelope"] = walk(row["envelope"], state)
                rows.append(canon({"n": row["n"], "tool": row["tool"], "args": row["args"],
                                   "bytes": len(canon(row["envelope"]).encode("utf-8")),
                                   "envelope": row["envelope"]}))
            if not state["done"]:
                raise NotExecuted("NOT EXECUTED: no masked datetime field appears in the transcript, so this "
                                  "arm has nothing to plant. The mask list needs re-deriving.")
            broken = tmp / ("arm_datetime_%s.jsonl" % how)
            broken.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
            return broken, state["field"]

        def non_datetime_in_masked_field():
            # The mask must NOT fire on a value that is not a datetime, so the planted text has to
            # show up as a VALUE difference (the count moving is a second, separate signal).
            broken, field = spoil_first_datetime("text")
            bad = diff_transcripts(TRANSCRIPT, broken)[0]
            return ["(field %s left unmasked, as it must be) %s" % (field, d)
                    for d in bad if d.startswith("TRANSCRIPT")]
        arm("non_datetime_in_masked_field", non_datetime_in_masked_field)

        def mask_count_mismatch():
            broken, field = spoil_first_datetime("drop")
            bad = diff_transcripts(TRANSCRIPT, broken)[0]
            return ["(field %s) %s" % (field, d) for d in bad if d.startswith("MASK COUNT")]
        arm("mask_count_mismatch", mask_count_mismatch)

        def bytes_field_lies():
            broken = tmp / "arm_bytes.jsonl"
            rows = []
            for i, raw in enumerate(TRANSCRIPT.read_text(encoding="utf-8").splitlines()):
                if not raw.strip():
                    continue
                row = json.loads(raw)
                if i == 1:
                    row["bytes"] = row["bytes"] + 1
                rows.append(canon(row))
            broken.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
            try:
                diff_transcripts(TRANSCRIPT, broken)
            except MaskError as exc:
                return ["BYTES %s" % exc]
            return []
        arm("bytes_field_lies", bytes_field_lies)

        def config_verdict_changed():
            broken = tmp / "arm_config"
            shutil.copytree(first, broken)
            target = broken / "config" / "bad_configs.txt"
            _plant(target, "REFUSE", "ACCEPT")
            write_manifest(broken)
            return diff_trees(first, broken)
        arm("config_verdict_changed", config_verdict_changed)

        # ---- fix round: the three NEW arms the reviewer's blocking findings demanded --------------

        def engine_guard_dropped():
            """A candidate that never implemented the write-authorization guard: its first write call
            LANDS (status ok) instead of being refused `rejected_by_gate`. With rule 4 narrowed to
            leaves (blocking finding 1), the whole `engine_code` shape -- including `override` and
            `checked`, which used to be hidden inside the old whole-subtree mask -- is compared, so
            this must be caught even without inspecting `status` at all; we still change `status` too
            because that IS what "the write landed" means on the wire."""
            broken, state = spoil_first_key("engine_guard_dropped", "status", lambda v: "ok",
                                            where=lambda v: v == "rejected_by_gate")
            # round-2 minor (a): this used to check only that SOME write tool appeared ANYWHERE in the
            # transcript, while planting on the first `rejected_by_gate` it found. A read that started
            # answering rejected_by_gate would have turned this silently into a read arm. Assert the row
            # the plant actually landed on is a write call.
            planted_tool = (state["call"] or "/").split("/", 1)[1]
            if planted_tool not in WRITE_TOOLS_IN_SCRIPT:
                raise NotExecuted("NOT EXECUTED: this arm planted on call %s, which is not one of the write "
                                  "tools %s -- the call script no longer exercises the guard"
                                  % (state["call"], ", ".join(WRITE_TOOLS_IN_SCRIPT)))
            bad = diff_transcripts(TRANSCRIPT, broken)[0]
            return ["(call %s: a write tool's rejected_by_gate -> ok, as a guardless candidate would "
                    "report) %s" % (state["call"], d) for d in bad if d.startswith("TRANSCRIPT")]
        arm("engine_guard_dropped", engine_guard_dropped)

        def poisoned_oracle_untracked_file():
            """Blocking finding 2's proof, kept as a permanent arm: an untracked file dropped into the
            oracle checkout (on PYTHONPATH, the child's cwd) must make verify_oracle refuse, not just
            get caught later by a byte diff against the committed tree."""
            plant = ORACLE_DIR / "sitecustomize.py"
            if plant.exists():
                raise NotExecuted("NOT EXECUTED: %s already exists; the arm cannot tell its own plant "
                                  "from a real file" % plant)
            plant.write_text("# planted by the poisoned_oracle_untracked_file red arm\n", encoding="utf-8")
            try:
                verify_oracle()
                return []                                    # verify_oracle did NOT refuse: BAD
            except NotExecuted as exc:
                return [str(exc)]
            finally:
                plant.unlink(missing_ok=True)
        arm("poisoned_oracle_untracked_file", poisoned_oracle_untracked_file)

        def masks_json_disagrees():
            """ROUND-2 BLOCKING FINDING 3. This arm used to tamper a copy of the counts dict and then
            return a hand-written failure string, calling no production code -- deleting the real check
            left it printing RED. It now drives `promote_transcript`, the SAME function
            `--record-transcript` uses (:main), against a real recording and a tampered masks.json, and
            asserts both that it refuses AND that it wrote nothing. Delete the
            `mask_counts_disagreement` call inside `promote_transcript` and this arm goes GREEN: that is
            the mutation this arm exists to detect."""
            dest = tmp / "promote_dest.jsonl"
            masks = tmp / "promote_masks.json"
            dest.write_text("SENTINEL: promote_transcript must not overwrite me\n", encoding="utf-8")
            tampered = dict(lc)
            key = sorted(tampered)[0]
            tampered[key] = tampered[key] + 1
            masks.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            counts, bad = promote_transcript(run1, dest, masks, force=False)
            if counts != lc:
                raise NotExecuted("NOT EXECUTED: promote_transcript re-measured %s, not the CONTROL's %s"
                                  % (canon(counts), canon(lc)))
            untouched = (dest.read_text(encoding="utf-8").startswith("SENTINEL")
                         and json.loads(masks.read_text(encoding="utf-8")) == tampered)
            if not untouched:
                return []            # it wrote anyway: a "refusal" that still re-baselines is not a refusal
            return bad
        arm("masks_json_disagrees", masks_json_disagrees)

        def poisoned_oracle_gitignored_file():
            """ROUND-2 BLOCKING FINDING 1, kept as a permanent arm. `git status --porcelain` does not report
            GITIGNORED paths, and `__pycache__/` is ignored -- and is where CPython loads code from. The
            reviewer forged a .pyc with a matching PEP-552 header there and poisoned all three gates.surql
            goldens while verification passed. The plant here is a real .pyc in a real __pycache__ directory,
            in the same ignored location; verify_oracle must REFUSE."""
            cache = ORACLE_DIR / "groundworks" / "__pycache__"
            plant = cache / ("gates.cpython-%d%d.pyc" % sys.version_info[:2])
            if plant.exists():
                raise NotExecuted("NOT EXECUTED: %s already exists; the arm cannot tell its own plant from "
                                  "a real file" % plant)
            cache.mkdir(parents=True, exist_ok=True)
            # a PEP-552 header carrying the REAL gates.py mtime+size, exactly as the reviewer's attack did,
            # so nothing but "git does not track this" can be what catches it
            import importlib.util as _ilu
            src = (ORACLE_DIR / "groundworks" / "gates.py").stat()
            plant.write_bytes(_ilu.MAGIC_NUMBER + (0).to_bytes(4, "little")
                              + int(src.st_mtime).to_bytes(4, "little") + (src.st_size & 0xFFFFFFFF).to_bytes(4, "little")
                              + b"\x00" * 16)
            try:
                verify_oracle()
                return []                                # verify_oracle did NOT refuse: BAD
            except NotExecuted as exc:
                return [str(exc)]
            finally:
                plant.unlink(missing_ok=True)
                shutil.rmtree(cache, ignore_errors=True)
        arm("poisoned_oracle_gitignored_file", poisoned_oracle_gitignored_file)

        def ambient_allow_unverified():
            """ROUND-2 BLOCKING FINDING 4. With GROUNDWORKS_ALLOW_UNVERIFIED=1 exported in the operator's
            shell, `child_env()` (which is `dict(os.environ)`) used to carry it into the server launch: the
            banner said "writes ALLOWED BY OVERRIDE", the write calls returned status ok with real derived
            ids, and the counts moved. FIXED_ENV now PINS it to "0", so this recording -- made with the
            ambient value set to 1 -- must be BYTE-IDENTICAL to the committed transcript after the mask.

            This arm is inverted: the "difference" it reports is the pin FAILING. It is listed with the red
            arms because it is the same question -- can the gate still tell the truth under this input."""
            was = os.environ.get("GROUNDWORKS_ALLOW_UNVERIFIED")
            os.environ["GROUNDWORKS_ALLOW_UNVERIFIED"] = "1"
            try:
                ambient = record_transcript(tmp / "transcript_ambient.jsonl")
            finally:
                if was is None:
                    os.environ.pop("GROUNDWORKS_ALLOW_UNVERIFIED", None)
                else:
                    os.environ["GROUNDWORKS_ALLOW_UNVERIFIED"] = was
            bad, ac, _ = diff_transcripts(ambient, TRANSCRIPT, "ambient=1", "committed")
            if bad:
                failures.append("AMBIENT GROUNDWORKS_ALLOW_UNVERIFIED=1 changed the recording -- the pin in "
                                "FIXED_ENV is not holding: %s" % bad[0].splitlines()[0])
            print("PIN ARM %-28s %s  masks %s"
                  % ("ambient_allow_unverified", "HELD (good)" if not bad else "LEAKED -- THE GATE IS NOT PINNED",
                     canon(ac)))
            return bad
        ambient_allow_unverified()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d red arm(s), %d went red" % (len(arms), sum(1 for _, red, _ in arms if red)))
    if failures:
        print("\nSELF-TEST FAILED:")
        for line in failures:
            print("  " + line)
        return 1
    print("SELF-TEST GREEN: the control is identical twice and every red arm goes red.")
    return 0


# ---------------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--oracle", default=ORACLE_TAG, help="the oracle tag; only %s is supported" % ORACLE_TAG)
    parser.add_argument("--ensure-oracle", action="store_true")
    parser.add_argument("--record-goldens", action="store_true")
    parser.add_argument("--record-transcript", action="store_true")
    parser.add_argument("--twice", action="store_true", help="with --record-transcript: record twice on a fresh "
                                                             "DB each time and diff after the mask")
    parser.add_argument("--force-masks", action="store_true",
                       help="with --record-transcript: overwrite a committed masks.json that disagrees, "
                            "instead of refusing")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--mcp", action="store_true", help="replay the call script against a candidate")
    parser.add_argument("--candidate", help="the candidate binary (with --mcp)")
    parser.add_argument("--goldens", help="compare this tree against the committed goldens")
    parser.add_argument("--config", action="store_true",
                       help="port-01: diff a candidate's `gw config spec`/`config check` against the "
                            "committed config goldens (needs --candidate)")
    parser.add_argument("--split", action="store_true",
                       help="byte-identical statement splitting (db.py's _split_statements) between "
                            "--candidate and the oracle, on every .surql file the oracle ships and "
                            "the test_db.py SplitTests fixtures")
    parser.add_argument("--_emit", help=argparse.SUPPRESS)
    parser.add_argument("--_transcript", help=argparse.SUPPRESS)
    parser.add_argument("--_config_fixtures", help=argparse.SUPPRESS)
    parser.add_argument("--_split", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args._emit:                                   # child mode
        _emit_goldens(args._emit)
        return 0
    if args._transcript:                             # child mode
        _record_transcript(args._transcript)
        return 0
    if args._config_fixtures:                        # child mode
        _emit_config_fixtures(args._config_fixtures)
        return 0
    if args._split:                                  # child mode
        _emit_split(args._split)
        return 0

    if args.oracle != ORACLE_TAG:
        print("NOT EXECUTED: the oracle is %s (owner ruling 2026-09-18); %r was asked for."
              % (ORACLE_TAG, args.oracle))
        return 2
    try:
        if args.ensure_oracle:
            print("ORACLE %s = %s at %s" % (ORACLE_TAG, ensure_oracle(args.oracle)[:12], ORACLE_DIR))
            return 0
        if args.record_goldens:
            verify_oracle(args.oracle)
            record_goldens(GOLDENS)
            print("wrote %d golden file(s) to %s" % (len(golden_files(GOLDENS)), GOLDENS))
            return 0
        if args.record_transcript:
            verify_oracle(args.oracle)
            # round-1 blocking finding 3 + round-2 minor (b): this used to record straight over the
            # committed transcript and only THEN decide whether the counts agreed, leaving a new
            # transcript beside an old masks.json on a refusal. Record to a temporary path; promote only
            # when the counts agree (or --force-masks). `promote_transcript` is the same function the
            # `masks_json_disagrees` red arm drives.
            staging = pathlib.Path(tempfile.mkdtemp(prefix="gw_record_"))
            try:
                recorded = record_transcript(staging / "transcript.jsonl")
                counts, bad = promote_transcript(recorded, TRANSCRIPT, TRANSCRIPT_MASKS, force=args.force_masks)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            if bad:
                for line in bad:
                    print(line)
                return 1
            print("wrote %s (mask counts %s)" % (TRANSCRIPT.name, canon(counts)))
            if args.twice:
                again = TRANSCRIPT.with_suffix(".second.jsonl")
                record_transcript(again)
                bad, lc, rc = diff_transcripts(TRANSCRIPT, again, "first", "second")
                print("TWICE: masks %s vs %s -> %s" % (canon(lc), canon(rc), "IDENTICAL" if not bad else "DIFFER"))
                for line in bad:
                    print("  " + line)
                again.unlink()
                return 1 if bad else 0
            return 0
        if args.goldens:
            verify_oracle(args.oracle)
            bad = diff_trees(GOLDENS, args.goldens, "committed", "candidate")
            for line in bad:
                print(line)
            return 1 if bad else 0
        if args.config:
            if not args.candidate:
                print("NOT EXECUTED: --config needs --candidate <bin>")
                return 2
            bad = diff_config(args.candidate)
            for line in bad:
                print(line)
            return 1 if bad else 0
        if args.split:
            verify_oracle(args.oracle)
            if not args.candidate:
                print("NOT EXECUTED: --split needs --candidate <bin>")
                return 2
            bad = run_split(args.candidate)
            for line in bad:
                print(line)
            if not bad:
                print("SPLIT GREEN: candidate matches db.py's _split_statements on every fixture")
            return 1 if bad else 0
        if args.mcp:
            verify_oracle(args.oracle)
            if not args.candidate:
                print("NOT EXECUTED: --mcp needs --candidate <bin>")
                return 2
            print("NOT EXECUTED: the candidate front door does not exist yet (impl-10 builds the gate "
                  "first; the Rust MCP server lands at port-09). Candidate asked for: %s" % args.candidate)
            return 2
        if args.self_test:
            return self_test(args.oracle)
    except NotExecuted as exc:
        print(exc)
        return 2
    except MaskError as exc:
        print("MASK: %s" % exc)
        return 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
