# -*- coding: utf-8 -*-
"""mutate.py -- the mutation runner: one named break per guard, and the test that must go red on it.

    python -m tests.harness.mutate lint  [--from worktree|HEAD|REV]
    python -m tests.harness.mutate run   [--ids M001,M002] [--files groundworks/gates.py] [--changed-since REV]
                                         [--no-db] [--from HEAD|worktree|REV] [--json OUT]
    python -m tests.harness.mutate canaries

Why (docs/TESTING.md s.0 and s.2). Of 22 defects since 2026-09-15, 17 were checks or outputs that looked
successful while doing nothing, and none was found by the test suite failing. They were found by someone
deliberately breaking the code and watching a test go red, by hand, with throwaway scripts that edited the
live tree. This makes that a program, and closes every trap those scripts had:

  - The live tree is NEVER written. Every mutation is applied to a copy (`git archive REV`, or a copy of
    `git ls-files` for --from worktree), restored from memory after each run and hash-verified.
  - An anchor must occur exactly once (a patch that "failed silently at its second match", 2fb2928), and
    must not lie wholly inside a comment or a docstring, checked with `tokenize` (two tests once matched
    the word "pipefail" in the comment explaining the defect and stayed green when it was deleted, 6c54f25).
  - A break that leaves the AST identical is INVALID, not caught. A break that does not compile, or stops
    a named test's module importing, is STILLBORN, not caught.
  - Every named test must PASS on the unmutated copy first. A skip there, or after the break, is
    NOT EXECUTED -- never a catch (CI once went green with every database suite skipped, D5).
  - A break whose named test still passes SURVIVED: a failure of the run, reported by name.
  - Tests run in a subprocess with a JSON reporter, the tree copy first on sys.path, and every imported
    module checked to come from inside the copy (groundworks is installed editable on the development
    machine, so a plain import from elsewhere reads the live, unmutated tree).
  - Bytecode is never written in the copy, so a restored file can never be shadowed by a mutant's .pyc
    written in the same second at the same size.
  - Server tests: each suite takes an OS-assigned port (a fixed one is checked first), runs are serial, and an OS lock stops two
    mutation runs on one machine at once. An occupied port is NOT EXECUTED, never borrowed.

Exit: 0 every selected break killed; 1 any SURVIVED / INVALID / STILLBORN / PREFLIGHT FAILED / TIMEOUT;
2 NOT EXECUTED (nothing could be judged, a canary misbehaved, or the lock is held). The summary is the
last line, because it is the line a human reads.

The registry (mutations.json) and the pinned id list (mutations_ids.json) are human-owned evaluator
files (METHOD s.15): entries may be added; removing or loosening one takes a human, and a removed id
fails `lint`. Standard library only.
"""
import argparse
import ast
import contextlib
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import tokenize

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REGISTRY = HERE / "mutations.json"
PINNED = HERE / "mutations_ids.json"
CANARIES = HERE / "canaries.json"
REPORTER = HERE / "_testrun.py"

ENTRY_KEYS = {"id", "guard", "defect", "commit", "source", "file", "anchor", "replacement", "edits",
              "must_fail", "needs_db", "timeout_s", "expect", "note"}
EXPECTS = ("killed", "killed-by-timeout")
# An error of one of these types under the mutant is not a catch: the code did not run at all.
NOT_A_CATCH = ("ImportError", "ModuleNotFoundError", "SyntaxError", "IndentationError", "SkipTest")
DEFAULT_TIMEOUT = 600

KILLED, KILLED_TIMEOUT = "KILLED", "KILLED-BY-TIMEOUT"
SURVIVED, INVALID, STILLBORN = "SURVIVED", "INVALID", "STILLBORN"
NOT_EXECUTED, PREFLIGHT_FAILED, TIMEOUT = "NOT EXECUTED", "PREFLIGHT FAILED", "TIMEOUT"
ORDER = [KILLED, KILLED_TIMEOUT, SURVIVED, INVALID, STILLBORN, PREFLIGHT_FAILED, TIMEOUT, NOT_EXECUTED]


class RegistryError(Exception):
    pass


# ---- the registry ------------------------------------------------------------------------------

def load_registry(path=REGISTRY):
    """The entries, each checked for shape. A malformed registry refuses; it never runs a subset."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError("cannot read registry %s: %s" % (path, exc)) from exc
    entries = data.get("mutations") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise RegistryError("registry %s holds no mutations: a registry of nothing guards nothing" % path)
    problems, seen = [], set()
    for i, entry in enumerate(entries):
        where = "entry %d (%s)" % (i, entry.get("id") if isinstance(entry, dict) else "?")
        if not isinstance(entry, dict):
            problems.append("%s is not an object" % where)
            continue
        unknown = sorted(set(entry) - ENTRY_KEYS)
        if unknown:
            problems.append("%s has unknown key(s) %s" % (where, ", ".join(unknown)))
        for key in ("id", "guard", "file", "must_fail"):
            if not entry.get(key):
                problems.append("%s needs %s" % (where, key))
        if entry.get("id") in seen:
            problems.append("%s: duplicate id" % where)
        seen.add(entry.get("id"))
        has_single = "anchor" in entry or "replacement" in entry
        if has_single == ("edits" in entry):
            problems.append("%s needs either anchor+replacement or edits, not both and not neither" % where)
        for edit in edits_of(entry) if not problems or has_single or "edits" in entry else []:
            if not isinstance(edit.get("anchor"), str) or not edit.get("anchor") or \
                    not isinstance(edit.get("replacement"), str):
                problems.append("%s: every edit needs a non-empty anchor and a replacement string" % where)
        if not isinstance(entry.get("must_fail"), list) or not all(
                isinstance(t, str) and t.count(".") >= 3 for t in entry.get("must_fail") or []):
            problems.append("%s: must_fail is a list of full test ids (tests.module.Class.test)" % where)
        if entry.get("expect", "killed") not in EXPECTS:
            problems.append("%s: expect must be one of %s" % (where, ", ".join(EXPECTS)))
        if not isinstance(entry.get("needs_db", False), bool):
            problems.append("%s: needs_db must be true or false" % where)
    if problems:
        raise RegistryError("registry %s invalid:\n  - %s" % (path, "\n  - ".join(problems)))
    return entries


def edits_of(entry):
    if "edits" in entry:
        return [e if isinstance(e, dict) else {} for e in (entry.get("edits") or [])]
    return [{"anchor": entry.get("anchor"), "replacement": entry.get("replacement")}]


# ---- anchors: exactly once, and in code ---------------------------------------------------------

def _line_offsets(text):
    offsets, total = [0], 0
    for line in io.StringIO(text, newline="").readlines():     # the same line split tokenize uses
        total += len(line)
        offsets.append(total)
    return offsets


def python_code_mask(text):
    """A list of booleans, one per character: True where the character belongs to a code token.

    Comments, docstrings (a string that is a bare expression statement) and whitespace are False.
    A string that is NOT a bare statement is code: SurrealQL lives in strings in this engine, and
    several proven breaks mutate SQL text (dff134c B16, B17)."""
    mask = [False] * len(text)
    offsets = _line_offsets(text)
    lines = io.StringIO(text, newline="").readlines()
    tree = ast.parse(text)
    doc_starts = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) \
                and isinstance(node.value.value, str):
            line = lines[node.lineno - 1]
            col = len(line.encode("utf-8")[:node.col_offset].decode("utf-8", errors="replace"))
            doc_starts.add((node.lineno, col))
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
            tokenize.ENDMARKER, tokenize.ENCODING}
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type in skip:
            continue
        if tok.type == tokenize.STRING and tok.start in doc_starts:
            continue
        start = offsets[tok.start[0] - 1] + tok.start[1]
        end = offsets[tok.end[0] - 1] + tok.end[1]
        for i in range(start, min(end, len(mask))):
            mask[i] = True
    return mask


def strip_hash_comments(text):
    """Non-Python text (YAML, workflows, shell): drop a `#` comment outside quotes, line by line.
    The same rule tests/test_run_verdict.py adopted after D8, made quote-aware."""
    out = []
    for line in text.splitlines(keepends=True):
        quote, cut = None, None
        for i, ch in enumerate(line):
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
            elif ch == "#" and (i == 0 or line[i - 1].isspace()):
                cut = i
                break
        if cut is None:
            out.append(line)
        else:
            out.append(line[:cut].rstrip() + ("\n" if line.endswith("\n") else ""))
    return "".join(out)


def hash_comment_mask(text):
    stripped_lines = strip_hash_comments(text).splitlines(keepends=True)
    mask = []
    for raw, kept in zip(text.splitlines(keepends=True), stripped_lines):
        code_len = len(kept.rstrip("\n"))
        mask.extend((i < code_len and not raw[i].isspace()) for i in range(len(raw)))
    mask.extend([False] * (len(text) - len(mask)))
    return mask


def is_python(path):
    return str(path).endswith(".py")


def apply_edits(text, entry, path):
    """(mutated_text, problem). problem is None or (verdict, reason)."""
    current = text
    for n, edit in enumerate(edits_of(entry), 1):
        anchor, replacement = edit["anchor"], edit["replacement"]
        count = current.count(anchor)
        label = "edit %d" % n if len(edits_of(entry)) > 1 else "anchor"
        if count != 1:
            return None, (INVALID, "%s occurs %d time(s) in %s; it must occur exactly once"
                          % (label, count, path))
        start = current.index(anchor)
        try:
            mask = python_code_mask(current) if is_python(path) else hash_comment_mask(current)
        except (SyntaxError, tokenize.TokenError, ValueError) as exc:
            return None, (INVALID, "%s: the unmutated file does not parse (%s)" % (path, exc))
        if not any(mask[start:start + len(anchor)]):
            return None, (INVALID, "%s lies wholly inside a comment, a docstring or whitespace in %s: "
                                   "a break there changes nothing a test could see" % (label, path))
        current = current[:start] + replacement + current[start + len(anchor):]
    return current, None


def semantic_problem(before, after, path):
    """None when the break changes behaviour-bearing content; else (verdict, reason)."""
    if is_python(path):
        try:
            tree = ast.parse(after)
            compile(after, str(path), "exec")
        except (SyntaxError, ValueError) as exc:
            return STILLBORN, "the mutant does not compile: %s" % exc
        if ast.dump(ast.parse(before)) == ast.dump(tree):
            return INVALID, "no semantic change: the AST is identical before and after the break"
        return None
    if str(path).endswith(".json"):
        try:
            same = json.loads(before) == json.loads(after)
        except ValueError as exc:
            return STILLBORN, "the mutant is not valid JSON: %s" % exc
        return (INVALID, "no semantic change: the parsed JSON is identical") if same else None
    if strip_hash_comments(before) == strip_hash_comments(after):
        return INVALID, "no semantic change: only comments or nothing changed"
    return None


# ---- test ids, statically ------------------------------------------------------------------------

def test_id_problem(root, test_id):
    """None when tests.module.Class.method exists in the tree's source; else why not. No import."""
    parts = test_id.split(".")
    module, cls, method = parts[:-2], parts[-2], parts[-1]
    path = pathlib.Path(root, *module).with_suffix(".py")
    if not path.exists():
        return "%s: no file %s" % (test_id, path.relative_to(root) if path.is_relative_to(root) else path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main_at = next((i for i, n in enumerate(tree.body) if isinstance(n, ast.If)
                    and "__main__" in ast.dump(n.test)), None)
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            if not any(isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == method
                       for m in node.body):
                return "%s: class %s has no method %s" % (test_id, cls, method)
            if main_at is not None and i > main_at:
                return "%s: class %s sits below the __main__ block, so running the file never sees it" \
                       % (test_id, cls)
            return None
    return "%s: no class %s in %s" % (test_id, cls, path.name)


OS_ASSIGNED = "os-assigned"


def suite_port(root, test_id):
    """The PORT a server suite declares, read from its source; None when it declares none.

    An int for a fixed port, or OS_ASSIGNED when the suite takes `surreal_bin.free_port()`. Every server
    suite now takes an OS-assigned port (tests/test_port_guard.py enforces it), so there is nothing to
    check in advance: the port is chosen when the suite imports, and a port taken by then makes
    surreal_bin.start_memory refuse and the suite skip, which this runner reports as NOT EXECUTED."""
    parts = test_id.split(".")
    path = pathlib.Path(root, *parts[:-2]).with_suffix(".py")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "PORT" for t in node.targets):
            value = getattr(node, "value", None)
            if isinstance(value, ast.Constant):
                return value.value
            if isinstance(value, ast.Call) and getattr(value.func, "attr", getattr(value.func, "id", None)) == "free_port":
                return OS_ASSIGNED
    return None


# ---- the tree copy -----------------------------------------------------------------------------

def _git(args, cwd=ROOT):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, check=True).stdout


def build_tree(source, dest):
    """Copy the tree at `source` (HEAD, a revision, or "worktree") into dest. Never writes the live tree."""
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if source == "worktree":
        listed = _git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"]).decode("utf-8")
        for rel in sorted(set(filter(None, listed.split("\0")))):
            src = ROOT / rel
            if not src.is_file():
                continue          # tracked but deleted in the worktree
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    else:
        blob = _git(["archive", "--format=tar", source])
        with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
            tar.extractall(dest, filter="data")
    return tree_hash(dest)


def tree_snapshot(root):
    """{relative path: sha256} for every file in the copy, bytecode caches excluded."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(pathlib.Path(root).rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def tree_hash(root):
    digest = hashlib.sha256()
    for rel, file_sha in sorted(tree_snapshot(root).items()):
        digest.update(rel.encode("utf-8"))
        digest.update(bytes.fromhex(file_sha))
    return digest.hexdigest()


def snapshot_drift(before, after, targets):
    """(restore_failures, written_by_tests). A registry file that differs after the run is a restore that
    went wrong, and nothing judged after it can be trusted. Any other file that appeared or changed was
    written by a test into the tree it ran in: reported by name, because a test that writes into its
    own tree can leak state from one mutation's run into the next."""
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return [c for c in changed if c in targets], [c for c in changed if c not in targets]


def sha(data):
    return hashlib.sha256(data).hexdigest()


# ---- running tests -----------------------------------------------------------------------------

def _kill_tree(proc):
    """Stop a timed-out test run and everything it started, by PID only -- never by name."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, 9)
    with contextlib.suppress(Exception):
        proc.wait(timeout=10)


def run_tests(tree, test_ids, timeout):
    """{"tests": {id: outcome}, "detail": {...}, "timed_out": bool, "harness_error": str|None}."""
    fd, out_name = tempfile.mkstemp(prefix="groundworks-mutate-result-", suffix=".json")
    os.close(fd)
    out_path = pathlib.Path(out_name)
    out_path.unlink()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(tree)
    env.pop("PYTHONPYCACHEPREFIX", None)
    kwargs = {"cwd": str(tree), "env": env, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    started = time.time()
    proc = subprocess.Popen([sys.executable, "-B", str(REPORTER), str(tree), str(out_path)] + list(test_ids),
                            **kwargs)
    try:
        output, _ = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        output, timed_out = b"", True
    result = {"tests": {}, "detail": {}, "timed_out": timed_out, "harness_error": None,
              "seconds": round(time.time() - started, 1)}
    if timed_out:
        return result
    try:
        report = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tail = (output or b"").decode("utf-8", errors="replace").strip().splitlines()[-5:]
        result["harness_error"] = "the test reporter wrote no result (exit %s): %s" % (proc.returncode,
                                                                                      " | ".join(tail))
        return result
    finally:
        with contextlib.suppress(OSError):
            out_path.unlink()
    if report.get("imported_outside_root"):
        result["harness_error"] = ("modules were imported from OUTSIDE the tree copy, so the break was never "
                                   "tested: %s" % "; ".join(report["imported_outside_root"]))
        return result
    result["tests"], result["detail"] = report["tests"], report.get("detail", {})
    return result


def classify(entry, run):
    """One verdict for one mutated run of an entry's must_fail tests, and the reason."""
    if run["harness_error"]:
        return NOT_EXECUTED, run["harness_error"]
    if run["timed_out"]:
        if entry.get("expect") == "killed-by-timeout":
            return KILLED_TIMEOUT, "timed out after %ss, as the entry expects" % entry.get("timeout_s")
        return TIMEOUT, "timed out after %ss and the entry does not expect a timeout" % entry.get("timeout_s")
    outcomes = run["tests"]
    passed = [t for t in entry["must_fail"] if outcomes.get(t) == "pass"]
    if passed:
        return SURVIVED, "still passing under the break: %s" % ", ".join(passed)
    skipped = [t for t in entry["must_fail"] if outcomes.get(t) in ("skip", "not_run", None)]
    if skipped:
        return NOT_EXECUTED, "skipped under the break (never a catch): %s -- %s" % (
            ", ".join(skipped), "; ".join(run["detail"].get(t, "") for t in skipped if run["detail"].get(t)))
    missing = [t for t in entry["must_fail"] if outcomes.get(t) == "missing"]
    if missing:
        return STILLBORN, "test(s) not found under the break: %s" % ", ".join(missing)
    dead = [t for t in entry["must_fail"] if outcomes.get(t, "").startswith("import:")
            or outcomes.get(t, "").split(":", 1)[-1] in NOT_A_CATCH]
    if dead:
        return STILLBORN, "the break stopped the code importing, which is not a catch: %s" % "; ".join(
            "%s (%s)" % (t, run["detail"].get(t, outcomes.get(t))) for t in dead)
    return KILLED, "; ".join("%s %s: %s" % (t.rsplit(".", 1)[-1], outcomes[t], run["detail"].get(t, ""))
                             for t in entry["must_fail"])


# ---- locking and ports --------------------------------------------------------------------------

@contextlib.contextmanager
def machine_lock():
    """One mutation run per machine: server suites use fixed ports, so two runs would collide."""
    path = pathlib.Path(tempfile.gettempdir()) / "groundworks-mutate.lock"
    handle = open(path, "a+b")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RegistryError("NOT EXECUTED: another mutation run holds %s" % path)
        yield
    finally:
        with contextlib.suppress(OSError):
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def surreal_available():
    candidates = [os.environ.get("GROUNDWORKS_SURREAL"), shutil.which("surreal"),
                  str(pathlib.Path.home() / "AppData" / "Local" / "SurrealDB" / "surreal.exe")]
    return any(p and pathlib.Path(p).exists() for p in candidates)


# ---- lint ----------------------------------------------------------------------------------------

def lint(tree, entries, pinned_ids=None):
    """Every problem that makes an entry a guard nobody is checking. No tests run."""
    problems = []
    ids = [e["id"] for e in entries]
    if pinned_ids is not None:
        for gone in sorted(set(pinned_ids) - set(ids)):
            problems.append("%s is pinned in mutations_ids.json but no longer in the registry: removing a "
                            "guard's mutation takes a human" % gone)
        for new in sorted(set(ids) - set(pinned_ids)):
            problems.append("%s is not pinned in mutations_ids.json: add it there in the same change" % new)
    for entry in entries:
        path = pathlib.Path(tree) / entry["file"]
        if not path.is_file():
            problems.append("%s: file %s does not exist" % (entry["id"], entry["file"]))
            continue
        before = path.read_text(encoding="utf-8")
        after, problem = apply_edits(before, entry, entry["file"])
        if problem is None:
            problem = semantic_problem(before, after, entry["file"])
        if problem:
            problems.append("%s %s: %s" % (entry["id"], problem[0], problem[1]))
        for test_id in entry["must_fail"]:
            why = test_id_problem(tree, test_id)
            if why:
                problems.append("%s: %s" % (entry["id"], why))
        if entry.get("needs_db") and not any(suite_port(tree, t) for t in entry["must_fail"]):
            problems.append("%s: needs_db is true but none of its test modules declares a PORT" % entry["id"])
    return problems


# ---- run ----------------------------------------------------------------------------------------

def select(entries, ids=None, files=None, no_db=False):
    chosen = entries
    if ids:
        unknown = sorted(set(ids) - {e["id"] for e in entries})
        if unknown:
            raise RegistryError("no registry entry with id(s) %s" % ", ".join(unknown))
        chosen = [e for e in chosen if e["id"] in ids]
    if files is not None:
        wanted = {pathlib.PurePosixPath(f.replace("\\", "/")).as_posix() for f in files}
        chosen = [e for e in chosen if e["file"] in wanted]
    if no_db:
        chosen = [e for e in chosen if not e.get("needs_db")]
    return chosen


def run(tree, entries, say=print):
    """Judge each entry on the tree copy. Returns a list of result dicts, one per entry."""
    results = []
    by_id = {}
    for entry in entries:
        by_id[entry["id"]] = {"id": entry["id"], "guard": entry["guard"], "file": entry["file"],
                              "verdict": None, "reason": ""}
    # 1. static validity first: nothing runs for an entry whose anchor or tests are wrong
    runnable = []
    for entry in entries:
        rec = by_id[entry["id"]]
        path = pathlib.Path(tree) / entry["file"]
        if not path.is_file():
            rec["verdict"], rec["reason"] = INVALID, "file %s does not exist" % entry["file"]
            continue
        before = path.read_text(encoding="utf-8")
        after, problem = apply_edits(before, entry, entry["file"])
        if problem is None:
            problem = semantic_problem(before, after, entry["file"])
        missing = [why for why in (test_id_problem(tree, t) for t in entry["must_fail"]) if why]
        if problem:
            rec["verdict"], rec["reason"] = problem
        elif missing:
            rec["verdict"], rec["reason"] = PREFLIGHT_FAILED, "; ".join(missing)
        else:
            runnable.append((entry, before, after))
    # 2. environment: a server test needs the binary and its own port free, or it is NOT EXECUTED
    ready = []
    for entry, before, after in runnable:
        rec = by_id[entry["id"]]
        if entry.get("needs_db"):
            if not surreal_available():
                rec["verdict"], rec["reason"] = NOT_EXECUTED, "SurrealDB binary not found"
                continue
            # only a FIXED port can be checked before the run; an OS-assigned one is chosen at import
            busy = sorted({p for p in (suite_port(tree, t) for t in entry["must_fail"])
                           if isinstance(p, int) and port_in_use(p)})
            if busy:
                rec["verdict"], rec["reason"] = NOT_EXECUTED, "port(s) %s in use; a server suite will not borrow " \
                    "a server it did not start" % ", ".join(map(str, busy))
                continue
        ready.append((entry, before, after))
    # 3. pre-flight: every named test must PASS on the unmutated copy (one run per group of tests)
    preflight_ids = sorted({t for entry, _, _ in ready for t in entry["must_fail"]})
    baseline = {}
    if preflight_ids:
        say("preflight    %d test(s) on the unmutated copy" % len(preflight_ids))
        timeout = max([e.get("timeout_s", DEFAULT_TIMEOUT) for e, _, _ in ready] + [DEFAULT_TIMEOUT]) * 2
        pre = run_tests(tree, preflight_ids, timeout)
        if pre["harness_error"] or pre["timed_out"]:
            for entry, _, _ in ready:
                by_id[entry["id"]]["verdict"] = NOT_EXECUTED
                by_id[entry["id"]]["reason"] = "pre-flight could not run: %s" % (
                    pre["harness_error"] or "timed out")
            ready = []
        baseline = pre["tests"]
    for entry, before, after in ready:
        rec = by_id[entry["id"]]
        outcomes = {t: baseline.get(t) for t in entry["must_fail"]}
        skipped = [t for t, o in outcomes.items() if o in ("skip", "not_run", None)]
        failing = [t for t, o in outcomes.items() if o not in ("pass", "skip", "not_run", None)]
        if skipped:
            rec["verdict"], rec["reason"] = NOT_EXECUTED, "skipped on the UNMUTATED tree, so nothing can be " \
                "judged: %s" % ", ".join(skipped)
            continue
        if failing:
            rec["verdict"], rec["reason"] = PREFLIGHT_FAILED, "already failing before the break: %s" % ", ".join(
                "%s (%s)" % (t, outcomes[t]) for t in failing)
            continue
        # 4. the break, the run, the restore, the hash
        path = pathlib.Path(tree) / entry["file"]
        original = path.read_bytes()
        say("mutation     %-6s %s" % (entry["id"], entry["guard"][:90]))
        try:
            path.write_bytes(after.encode("utf-8"))
            mutated = run_tests(tree, entry["must_fail"], entry.get("timeout_s", DEFAULT_TIMEOUT))
        finally:
            path.write_bytes(original)
        if sha(path.read_bytes()) != sha(original):
            raise RegistryError("RESTORE FAILED for %s in the tree copy: stopping before the next break"
                                % entry["id"])
        rec["verdict"], rec["reason"] = classify(entry, mutated)
        rec["seconds"] = mutated.get("seconds")
        say("             %s: %s" % (rec["verdict"], rec["reason"][:300]))
    results = [by_id[e["id"]] for e in entries]
    return results


def summary_line(results, declared=None):
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in ORDER}
    ports = sorted({m for r in results if r["verdict"] == NOT_EXECUTED
                    for m in re.findall(r"port\(s\) ([\d, ]+) in use", r["reason"])})
    return ("MUTATIONS  %d declared | %d selected | %d killed | %d killed-by-timeout (expected) | %d SURVIVED | "
            "%d INVALID | %d STILLBORN | %d PREFLIGHT FAILED | %d TIMEOUT | %d NOT EXECUTED%s"
            % (declared if declared is not None else len(results), len(results), counts[KILLED],
               counts[KILLED_TIMEOUT], counts[SURVIVED], counts[INVALID], counts[STILLBORN],
               counts[PREFLIGHT_FAILED], counts[TIMEOUT], counts[NOT_EXECUTED],
               (" (port %s in use)" % ", ".join(ports)) if ports else ""))


def exit_code(results):
    if any(r["verdict"] in (SURVIVED, INVALID, STILLBORN, PREFLIGHT_FAILED, TIMEOUT) for r in results):
        return 1
    if any(r["verdict"] == NOT_EXECUTED for r in results):
        return 2
    return 0


# ---- canaries: the runner proves it can fail -------------------------------------------------

def run_canaries(tree, say=print):
    """Each canary is a registry entry with the verdict the runner MUST give it. A runner that calls a
    do-nothing test's mutation KILLED, or a comment-only break anything but INVALID, is broken, and
    every verdict it gives is worthless. Returns (behaved, problems)."""
    spec = json.loads(CANARIES.read_text(encoding="utf-8"))
    prior = {}
    for rel, source in spec.get("inject", {}).items():
        target = pathlib.Path(tree) / rel
        prior[rel] = target.read_bytes() if target.is_file() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, target)
    canaries = spec["canaries"]
    results = run(tree, [c["entry"] for c in canaries], say=lambda *_: None)
    problems = []
    for canary, result in zip(canaries, results):
        ok = result["verdict"] == canary["must_be"]
        say("canary       %-9s %-24s must be %-10s was %-10s %s" % (
            "behaved" if ok else "MISBEHAVED", canary["entry"]["id"], canary["must_be"], result["verdict"],
            canary["why"][:80]))
        if not ok:
            problems.append("canary %s must be %s, was %s (%s)" % (canary["entry"]["id"], canary["must_be"],
                                                                    result["verdict"], result["reason"]))
    for rel, was in prior.items():          # leave the copy exactly as it was: its hash is checked at the end
        target = pathlib.Path(tree) / rel
        if was is None:
            with contextlib.suppress(OSError):
                target.unlink()
        else:
            target.write_bytes(was)
    return len(canaries) - len(problems), problems


# ---- command line ------------------------------------------------------------------------------

def changed_files(since):
    out = _git(["diff", "--name-only", "%s...HEAD" % since]).decode("utf-8")
    return [line.strip() for line in out.splitlines() if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m tests.harness.mutate")
    sub = parser.add_subparsers(dest="command", required=True)
    p_lint = sub.add_parser("lint", help="no tests run: anchors valid, tests exist, no pinned id removed")
    p_lint.add_argument("--from", dest="source", default="worktree")
    p_run = sub.add_parser("run")
    p_run.add_argument("--ids")
    p_run.add_argument("--files", nargs="*")
    p_run.add_argument("--changed-since", metavar="REV")
    p_run.add_argument("--no-db", action="store_true", help="only breaks whose tests need no server")
    p_run.add_argument("--from", dest="source", default="HEAD")
    p_run.add_argument("--json", dest="json_out")
    p_run.add_argument("--registry", default=str(REGISTRY))
    p_run.add_argument("--skip-canaries", action="store_true",
                       help="for the harness's own tests only; a real run always runs its canaries")
    sub.add_parser("canaries")
    args = parser.parse_args(argv)

    try:
        if args.command == "lint":
            entries = load_registry()
            pinned = json.loads(PINNED.read_text(encoding="utf-8"))["ids"]
            with tempfile.TemporaryDirectory(prefix="groundworks-mutate-lint-") as tmp:
                tree = pathlib.Path(tmp) / "tree"
                digest = build_tree(args.source, tree)
                problems = lint(tree, entries, pinned)
            for problem in problems:
                print("LINT  " + problem)
            print("MUTATION LINT  %d entries | %d pinned | %d problem(s) | tree %s (%s)"
                  % (len(entries), len(pinned), len(problems), args.source, digest[:12]))
            return 1 if problems else 0

        if args.command == "canaries":
            with machine_lock(), tempfile.TemporaryDirectory(prefix="groundworks-mutate-") as tmp:
                tree = pathlib.Path(tmp) / "tree"
                build_tree("worktree", tree)
                behaved, problems = run_canaries(tree)
            print("CANARIES  %d behaved | %d MISBEHAVED" % (behaved, len(problems)))
            return 0 if not problems else 2

        entries = load_registry(args.registry)
        files = args.files
        if args.changed_since:
            files = (files or []) + changed_files(args.changed_since)
        chosen = select(entries, ids=args.ids.split(",") if args.ids else None, files=files, no_db=args.no_db)
        if not chosen:
            if files is not None:
                print("MUTATIONS  %d declared | 0 selected: no changed file carries a registered guard (%s)"
                      % (len(entries), ", ".join(files) or "no files changed"))
                return 0
            print("MUTATIONS  %d declared | 0 selected | NOT EXECUTED: the selection matched nothing" % len(entries))
            return 2
        with machine_lock(), tempfile.TemporaryDirectory(prefix="groundworks-mutate-") as tmp:
            tree = pathlib.Path(tmp) / "tree"
            digest = build_tree(args.source, tree)
            before = tree_snapshot(tree)
            print("tree         %s copied to a temporary directory, sha256 %s (the live tree is never written)"
                  % (args.source, digest[:16]))
            canary_note = "skipped (--skip-canaries)"
            if not args.skip_canaries:
                behaved, problems = run_canaries(tree)
                if problems:
                    for problem in problems:
                        print("CANARY  " + problem)
                    print("MUTATIONS  %d declared | NOT EXECUTED: the runner's canaries misbehaved, so no verdict "
                          "it gives can be trusted" % len(entries))
                    return 2
                canary_note = "%d behaved" % behaved
            results = run(tree, chosen)
            restore_failures, written = snapshot_drift(before, tree_snapshot(tree), {e["file"] for e in chosen})
            if restore_failures:
                print("MUTATIONS  NOT EXECUTED: mutated file(s) differ from the copy after the run, so a restore "
                      "went wrong: %s" % ", ".join(restore_failures))
                return 2
            if written:
                print("WARNING      the tests wrote %d file(s) into the tree they ran in (state can leak between "
                      "mutation runs): %s" % (len(written), ", ".join(written[:10])))
        for r in results:
            if r["verdict"] != KILLED:
                print("%-18s %-6s %s\n                   %s" % (r["verdict"], r["id"], r["guard"], r["reason"][:600]))
        if args.json_out:
            pathlib.Path(args.json_out).write_text(json.dumps(
                {"source": args.source, "tree_sha256": digest, "canaries": canary_note, "results": results},
                indent=1), encoding="utf-8")
        print("canaries     %s" % canary_note)
        print(summary_line(results, declared=len(entries)))
        return exit_code(results)
    except RegistryError as exc:
        print(str(exc))
        print("MUTATIONS  NOT EXECUTED: %s" % str(exc).splitlines()[0])
        return 2


if __name__ == "__main__":
    sys.exit(main())
