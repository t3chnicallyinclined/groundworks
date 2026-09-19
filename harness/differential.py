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
  (4) ENGINE_CODE_FIELDS (`engine_code`, `engine_code_warning`, and the `WARNING` that repeats it):
      the identity and freshness of the PYTHON checkout the server started from. Not engine
      behaviour, carries a measured duration and a timeout-dependent verdict, and a Rust candidate
      has no Python checkout. Masked whole, and counted.
  (5) LAUNCH PATHS, at RECORD time on both sides: the two absolute paths the HARNESS chose (the
      oracle checkout and the panel root) become <ORACLE_DIR> / <PANEL_ROOT> where a refusal text
      quotes them back. They are the harness's inputs, not the engine's output.

Exit codes: 0 green, 1 a difference or a red arm that failed to go red, 2 not executed (the oracle
is not pinned, a tool is missing, the datastore would not start).
"""
import argparse
import ast
import copy
import datetime as _dt
import hashlib
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
# Rule (4), ADDED 2026-09-19 with its reason. `engine_code` / `engine_code_warning` (and the `WARNING`
# that repeats it) report the identity and freshness of the PYTHON ENGINE CHECKOUT this server started
# from (groundworks/mcp/code_state.py). They are not engine behaviour: they carry a measured duration
# (`read_ms`), a verdict that flips when a `git` re-read exceeds code_state.GIT_TIMEOUT_S=15 (seen on
# this machine), and absolute paths -- and a Rust candidate has no Python checkout to report at all.
# Masked whole, and counted, so a candidate that stops emitting the field fails the count check.
ENGINE_CODE_FIELDS = ("engine_code", "engine_code_warning")
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


def verify_oracle(tag=ORACLE_TAG):
    """Refuse to compare against anything but the pinned tag."""
    if not (ORACLE_DIR / ".git").exists():
        raise NotExecuted("NOT EXECUTED: %s is not a git checkout. Run --ensure-oracle." % ORACLE_DIR)
    described = _git(["describe", "--exact-match"], ORACLE_DIR, check=False)
    if described != tag:
        raise NotExecuted("NOT EXECUTED: the oracle at %s is at %r, not %s. The gate compares against the "
                          "tag and nothing else." % (ORACLE_DIR, described or "no exact tag", tag))
    head = _git(["rev-parse", "HEAD"], ORACLE_DIR)
    if tag == ORACLE_TAG and head != ORACLE_COMMIT:
        raise NotExecuted("NOT EXECUTED: %s in %s is %s, not the pinned %s" % (tag, ORACLE_DIR, head, ORACLE_COMMIT))
    dirty = _git(["status", "--porcelain", "--untracked-files=no"], ORACLE_DIR)
    if dirty:
        raise NotExecuted("NOT EXECUTED: the oracle checkout has uncommitted changes:\n%s" % dirty)
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


def child_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ORACLE_DIR)
    env["PYTHONHASHSEED"] = "0"          # sets never reorder between two recordings
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def record_goldens(out_dir):
    """Emit every golden into `out_dir` through a child process on the oracle."""
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
# the transcript: one seeded single-agent MCP session against the oracle front door
# ---------------------------------------------------------------------------------------------

HARNESS_LENS = "lens_security"          # one of the demo instance's lenses (instance.yaml `lenses:`)
PANEL_ROOT = pathlib.Path(tempfile.gettempdir()) / "gw_harness_panel_root"   # FIXED, never mkdtemp

FIXED_ENV = {
    # every launch variable server.py reads (server.py:11-24), pinned to a constant
    "GROUNDWORKS_ADAPTER": "groundworks.mcp.instance_adapter",
    "GROUNDWORKS_AGENT_ID": "harness_agent",
    "GROUNDWORKS_ROLE": "coordinator",
    "GROUNDWORKS_LENS": HARNESS_LENS,
    # The oracle is a pinned TAG, so the server never starts on branch main and the code guard would
    # refuse every write. The override keeps the guard's verdict out of the gate; `engine_code` is
    # masked (rule 4) for the same reason.
    "GROUNDWORKS_ALLOW_UNVERIFIED": "1",
    "TZ": "UTC",
}

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
    # <CRITIQUE_ID> is filled at record time with the id the ORACLE derives for the critique added
    # above (write.py:143 via patterns/edge_ids.py:27). The answer only lands if the candidate derives
    # the same id, so this call is the derived-id check.
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
        env.pop("GROUNDWORKS_DB_ACCESS", None)

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


def record_transcript(out_path):
    run = subprocess.run([sys.executable, str(HERE / "differential.py"), "--_transcript", str(out_path)],
                         env=child_env(), cwd=str(ORACLE_DIR))
    if run.returncode != 0:
        raise NotExecuted("NOT EXECUTED: recording the transcript failed (see output above)")
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


def mask_envelope(node, counts):
    """Rules (1) and (2), applied by FIELD NAME anywhere in the envelope. Returns a new object."""
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
            elif key in ENGINE_CODE_FIELDS or (key == "WARNING" and isinstance(value, str)
                                               and value.startswith(ENGINE_CODE_WARNING_PREFIX)):
                counts["engine_code"] = counts.get("engine_code", 0) + 1
                out[key] = "<engine_code>"
            else:
                out[key] = mask_envelope(value, counts)
        return out
    if isinstance(node, list):
        return [mask_envelope(item, counts) for item in node]
    return node


class MaskError(Exception):
    pass


def mask_transcript(path):
    """(masked lines, per-rule counts). Rule (3) is enforced here: `bytes` must equal this side's own
    unmasked envelope length, and is then dropped."""
    counts = {"datetime": 0, "random_id": 0, "run_token": 0, "engine_code": 0, "bytes_checked": 0}
    lines = []
    for lineno, raw in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), start=1):
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
        if not TRANSCRIPT.exists():
            raise NotExecuted("NOT EXECUTED: %s is missing. Run --record-transcript." % TRANSCRIPT.name)
        for pas in (1, 2):
            bad, lc, rc = diff_transcripts(TRANSCRIPT, TRANSCRIPT, "transcript", "itself")
            print("CONTROL transcript pass %d: %s, masks %s" % (pas, "IDENTICAL" if not bad else "DIFFER", canon(lc)))
            failures += ["CONTROL transcript: %s" % d for d in bad]
        committed = json.loads(TRANSCRIPT_MASKS.read_text(encoding="utf-8")) if TRANSCRIPT_MASKS.exists() else None
        if committed is not None and committed != lc:
            failures.append("CONTROL transcript: recorded mask counts %s != committed %s" % (canon(lc), canon(committed)))

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
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--mcp", action="store_true", help="replay the call script against a candidate")
    parser.add_argument("--candidate", help="the candidate binary (with --mcp)")
    parser.add_argument("--goldens", help="compare this tree against the committed goldens")
    parser.add_argument("--_emit", help=argparse.SUPPRESS)
    parser.add_argument("--_transcript", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args._emit:                                   # child mode
        _emit_goldens(args._emit)
        return 0
    if args._transcript:                             # child mode
        _record_transcript(args._transcript)
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
            record_transcript(TRANSCRIPT)
            _, counts = mask_transcript(TRANSCRIPT)
            with open(TRANSCRIPT_MASKS, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(counts, indent=2, sort_keys=True) + "\n")
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
