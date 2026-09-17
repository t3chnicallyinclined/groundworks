# -*- coding: utf-8 -*-
"""code_state.py -- which engine code this server process is serving, and whether it may write with it.

Why (found in real use, 2026-09-16). A work-in-progress branch was checked out in the engine checkout a live
MCP server launches from. The server served that unverified engine, write tools included, against a live
graph. The checkout was switched back, and the running processes kept the unverified code anyway: a Python
process keeps what it imported at startup, and imports what it has not yet imported from whatever is on disk
when it first needs it. Nothing said so.

What this module does:
  read_state(root)   the engine checkout's commit, branch and uncommitted changes, read from git; or
                     kind UNKNOWN with the reason (not a git checkout, git missing, the engine sits inside
                     some other repository, git refused). UNKNOWN is never read as verified.
  CodeGuard          records the state at startup, and before every write re-reads it and refuses the write
                     (GateRejected, so the envelope says rejected_by_gate with `missing` naming what to do) when
                       * the server started on a branch other than `main`;
                       * the server started with uncommitted changes, or the checkout has them now;
                       * the checkout's commit or branch changed since the server started (the process is
                         stale against disk: restart it);
                       * the state could not be read, at startup or now (UNKNOWN).
                     Reads are never refused. XSCHOLAR_ALLOW_UNVERIFIED=1, set at launch, lets writes through;
                     `report()` (health) then says so first, in capitals.

Decisions (docs/DECISIONS.md, 2026-09-16 "The MCP server refuses writes from unverified engine code"):
  * Not a git checkout -> UNKNOWN -> writes refused. The guard exists to show the code is a reviewed commit;
    an installed copy with no history cannot show that. A deployment that is deliberately not a checkout
    launches with the override and carries the warning in health.
  * Uncommitted changes = any change to a tracked file, anywhere in the checkout, plus untracked files under
    the engine package directory (`xscholar/`). An untracked note elsewhere is not code this server imports;
    an untracked module in the package is.
  * The stale check re-reads git before EVERY write, unthrottled. Measured on the development machine
    (Windows 11, this repository): about 50-120 ms per read (two git processes); `read_ms` in health carries
    the figure for the machine it runs on. Writes are agent-paced and each already costs a datastore round
    trip; a throttle would reopen exactly the window this closes.

Standard library only.
"""
import os
import pathlib
import subprocess
import time

from xscholar.mcp import adapter as contract

ALLOW_ENV = "XSCHOLAR_ALLOW_UNVERIFIED"
VERIFIED_BRANCH = "main"
PACKAGE_DIR = "xscholar/"
KIND_GIT, KIND_UNKNOWN = "git", "UNKNOWN"
VERIFIED, UNVERIFIED, STALE = "VERIFIED", "UNVERIFIED", "STALE"
SAMPLE = 20
GIT_TIMEOUT_S = 15


def engine_root():
    """The directory holding the `xscholar` package this process imported (the checkout, when it is one)."""
    return pathlib.Path(__file__).resolve().parents[2]


def _same_path(a, b):
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


def _git(root, args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}   # no GIT_DIR/GIT_WORK_TREE redirection
    proc = subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, timeout=GIT_TIMEOUT_S, env=env)
    if proc.returncode != 0:
        raise RuntimeError("git %s exited %d: %s" % (" ".join(args), proc.returncode,
                                                     proc.stderr.decode("utf-8", "replace").strip()[:300]))
    return proc.stdout.decode("utf-8", "replace")


def unknown(root, reason, started=None):
    return {"kind": KIND_UNKNOWN, "root": str(root), "reason": reason, "commit": None, "branch": None,
            "dirty": None, "dirty_count": None, "dirty_files": [],
            "read_ms": round((time.perf_counter() - started) * 1000, 1) if started else None}


def read_state(root=None):
    """The engine checkout's state, from git. Never raises: a state that cannot be read is kind UNKNOWN."""
    root = pathlib.Path(root) if root is not None else engine_root()
    started = time.perf_counter()
    try:
        top, commit, branch = _git(root, ["rev-parse", "--show-toplevel", "HEAD", "--abbrev-ref", "HEAD"]).splitlines()[:3]
    except FileNotFoundError:
        return unknown(root, "git is not installed or not on PATH", started)
    except subprocess.TimeoutExpired:
        return unknown(root, "git did not answer within %ds" % GIT_TIMEOUT_S, started)
    except (RuntimeError, ValueError, OSError) as exc:
        return unknown(root, "not a readable git checkout: %s" % exc, started)
    if not _same_path(top, root):
        # an installed copy inside some other repository (a virtualenv in a project) must not borrow its history
        return unknown(root, "the engine directory %s is not the top of a git checkout (git's top is %s)"
                       % (root, top), started)
    try:
        listed = _git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
        return unknown(root, "git status failed: %s" % exc, started)
    dirty = []
    entries = listed.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":
            i += 1                      # -z puts a rename's source path in the next field
        if code == "??" and not path.startswith(PACKAGE_DIR):
            continue
        dirty.append("%s %s" % (code.strip(), path))
    return {"kind": KIND_GIT, "root": str(root), "reason": None, "commit": commit, "branch": branch,
            "dirty": bool(dirty), "dirty_count": len(dirty), "dirty_files": dirty[:SAMPLE],
            "read_ms": round((time.perf_counter() - started) * 1000, 1)}


def git_state(commit, branch, dirty_files=(), root="(given)"):
    """A state as read_state returns it, for a caller that already knows it (tests, a supervisor)."""
    dirty = list(dirty_files)
    return {"kind": KIND_GIT, "root": str(root), "reason": None, "commit": commit, "branch": branch,
            "dirty": bool(dirty), "dirty_count": len(dirty), "dirty_files": dirty[:SAMPLE], "read_ms": None}


def _where(state):
    if state["kind"] != KIND_GIT:
        return "UNKNOWN (%s)" % state["reason"]
    return "commit %s on branch %s%s" % ((state["commit"] or "?")[:12], state["branch"],
                                         ", with %d uncommitted change(s)" % state["dirty_count"] if state["dirty"] else "")


def allowed_from_env(env=None):
    env = os.environ if env is None else env
    return (env.get(ALLOW_ENV) or "").strip() == "1"


class CodeGuard:
    """The engine code state at startup, and the write refusal when it is not a verified, current commit."""

    def __init__(self, reader=None, allow_unverified=False, branch=VERIFIED_BRANCH):
        self._reader = reader or read_state
        self.branch = branch
        self.allow_unverified = bool(allow_unverified)
        self.at_start = self._reader()

    @classmethod
    def from_environment(cls, env=None, root=None):
        return cls(lambda: read_state(root), allow_unverified=allowed_from_env(env))

    def assess(self, now=None):
        """Problems with serving writes from this process, each with what would cure it. Re-reads git."""
        now = self._reader() if now is None else now
        start = self.at_start
        problems, missing = [], []
        if start["kind"] != KIND_GIT:
            problems.append("UNKNOWN: the engine code state could not be read when this server started: %s"
                            % start["reason"])
            missing.append("launch this server from a git checkout of the engine on branch %s with no uncommitted "
                           "changes (or a human sets %s=1 at launch)" % (self.branch, ALLOW_ENV))
        else:
            if start["branch"] != self.branch:
                problems.append("UNVERIFIED: this server started from branch %r, not %r" % (start["branch"], self.branch))
                missing.append("check out branch %s in %s, then restart this server" % (self.branch, start["root"]))
            if start["dirty"]:
                problems.append("UNVERIFIED: this server started with %d uncommitted change(s) in the engine checkout: %s"
                                % (start["dirty_count"], ", ".join(start["dirty_files"])))
                missing.append("commit or discard the uncommitted changes, then restart this server")
            if now["kind"] != KIND_GIT:
                problems.append("UNKNOWN: the engine code state cannot be read now: %s" % now["reason"])
                missing.append("restore the engine checkout at %s, then restart this server" % start["root"])
            else:
                if now["commit"] != start["commit"] or now["branch"] != start["branch"]:
                    problems.append("STALE: this server process started at %s, and the checkout on disk is now %s. "
                                    "The running process still serves the code it loaded at startup"
                                    % (_where(start), _where(now)))
                    missing.append("restart this MCP server so it loads the code that is on disk")
                if now["dirty"] and not start["dirty"]:
                    problems.append("UNVERIFIED: the engine checkout has %d uncommitted change(s) made after this server "
                                    "started: %s" % (now["dirty_count"], ", ".join(now["dirty_files"])))
                    missing.append("commit or discard the uncommitted changes, then restart this server")
        if any(p.startswith("STALE") for p in problems):
            verdict = STALE
        elif any(p.startswith("UNKNOWN") for p in problems):
            verdict = KIND_UNKNOWN
        elif problems:
            verdict = UNVERIFIED
        else:
            verdict = VERIFIED
        return {"verdict": verdict, "problems": problems, "missing": missing, "now": now}

    def check_write(self, tool):
        """Raise GateRejected unless writes may be served. Returns the assessment when they may."""
        found = self.assess()
        if found["problems"] and not self.allow_unverified:
            raise contract.GateRejected(
                "%s refused: this server will not write with engine code it cannot show is a verified, current "
                "commit. %s. Reads still work." % (tool, "; ".join(found["problems"])), missing=found["missing"])
        return found

    def override_warning(self, found):
        if not self.allow_unverified:
            return None
        if found["problems"]:
            return ("WRITES ARE ALLOWED FROM UNVERIFIED ENGINE CODE: %s=1 was set at launch, and: %s"
                    % (ALLOW_ENV, "; ".join(found["problems"])))
        return ("%s=1 was set at launch: writes would be allowed from unverified engine code. The code is verified "
                "right now." % ALLOW_ENV)

    def report(self):
        """For health, first thing: the verdict, whether writes are served, and both states."""
        found = self.assess()
        if not found["problems"]:
            writes = "allowed: verified commit on branch %s, unchanged since startup" % self.branch
        elif self.allow_unverified:
            writes = "ALLOWED BY OVERRIDE (%s=1) despite the problems below" % ALLOW_ENV
        else:
            writes = "REFUSED: every write tool answers rejected_by_gate until the problems below are cured"
        out = {}
        warning = self.override_warning(found)
        if warning:
            out["WARNING"] = warning
        out.update({"verdict": found["verdict"], "writes": writes, "override": self.allow_unverified,
                    "problems": found["problems"], "missing": found["missing"],
                    "started_from": self.at_start, "on_disk_now": found["now"],
                    "checked": "git is re-read before every write and on every health call (read_ms is the cost)"})
        return out
