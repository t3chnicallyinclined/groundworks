# -*- coding: utf-8 -*-
"""scan.py -- stage 1 of the bootstrap (HANDOFF-2026-09-17-product-design.md): walk a projects root
and write one registry record per project. NO AI, NO command execution -- every fact here comes from
`git -C <dir> rev-parse/branch/remote/log -1` (read-only) or a filename/static-config match. Candidate
gates are DERIVED and NEVER RUN.

    python -m xscholar.scan C:/Users/trist/projects --out estate.json
    python -m xscholar.scan C:/Users/trist/projects --out estate.json --local-only "*Family court*"

Kept deliberately self-contained: stdlib only, no import from any other xscholar module, so the
Agent Groundwork rename (see xscholar-one-engine-decision memory, 2026-09-17) can move this file
without touching an import graph.

Design decisions a reviewer should know about (they are not obvious from the ticket alone):

  * PROJECT BOUNDARY = a directory holding a `.git` entry (file or directory), found either as one of
    the root's immediate children or nested anywhere beneath one. Once a boundary is found, the walk
    that discovered it stops descending into it and a fresh, independent walk is started rooted there
    (`scan_tree`'s own os.walk calls its `dirnames[:] = []` right after recursing) -- this is why a
    "nested repo" gets its own record without being double-walked: every file under the root is visited
    by exactly one of the (possibly many) os.walk calls, never both the parent's and the child's.
  * A plain folder (no `.git` anywhere in it) is still one record per immediate child of the scanned
    root; it is not searched for nested projects beneath arbitrary depth beyond the same walk, so a
    repo several plain folders deep is still found (the walk does not stop at "plain" -- only at a
    project boundary).
  * `local_only` reads "no remote" as "no remote configured on this GIT project" -- it does not fire
    for a plain folder just because plain folders have no concept of a remote at all. Firing it there
    would make the local-only branch (path-only, no docs/build-files) the ONLY behavior a plain folder
    could ever get, which defeats the point of cataloguing plain folders in the first place. A plain
    folder is local-only only by explicit `--local-only` glob match.
  * `*key*` (from the ticket's secret-flag list) is matched literally, so it will flag files that only
    coincidentally contain "key" (e.g. `monkey_patch.py`). That is intentional over-flagging: this is a
    FLAG for a human to glance at, not a classifier, and the instruction was explicit never to read
    contents to disambiguate.
  * A local-only project boundary (matched by `--local-only` glob, or a git project with no remote)
    is never walked at all -- `scan_tree` decides local-only BEFORE its os.walk, and if so returns
    just that one minimal record. This is deliberate: a local-only folder's insides (e.g. a private
    folder's own nested git worktrees) must not leak into the registry even as bare paths, since the
    whole point of local-only is "an outsider reading this JSON should learn nothing about what's in
    here beyond that something git-tracked exists at this path."
  * All git reads go through `--no-optional-locks` (see `_run_git`) because a plain `git status`
    opportunistically rewrites `.git/index`, which is itself a write this scanner is not allowed to
    make.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {"node_modules", "target", ".venv", "__pycache__", "dist", "build", ".history"}
# glob patterns (fnmatch, not exact names) for directories to skip -- e.g. GP2040-CE's build_w6100/
SKIP_DIR_PATTERNS = ("build_*",)
DEFAULT_LOCAL_ONLY_GLOBS = ["*Family court*"]

_SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*key*", "credentials*", "id_rsa*")

_REMOTE_URL_RE = re.compile(
    r"^(?:[a-zA-Z][a-zA-Z0-9+.\-]*://)?(?:[^@/]+@)?([^:/]+)[:/](.+?)(?:\.git)?/?$"
)

_GIT_TIMEOUT = 15


def _run_git(directory: Path, args):
    """`git --no-optional-locks -C directory <args>`, read-only. Returns stdout (stripped) or None
    on any failure.

    `--no-optional-locks` matters here: plain `git status` opportunistically refreshes and
    rewrites `.git/index` (and briefly takes `index.lock`) even though it only *reports* status --
    that write is exactly what "read-only, without touching any file" forbids, and it can also
    collide with a live agent working in the same worktree. `--no-optional-locks` makes git skip
    that refresh and report from the index as it last sat on disk (see
    test_git_status_does_not_touch_index in tests/scan/test_scan.py).
    """
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(directory)] + list(args),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def host_path_from_remote(url: str):
    """`git@github.com:user/repo.git`, `https://github.com/user/repo.git`, `ssh://git@h/p` -> "host/path".

    Returns None for a URL this cannot parse (kept permissive; an unparsed remote is dropped, not
    guessed at).
    """
    url = (url or "").strip()
    match = _REMOTE_URL_RE.match(url)
    if not match:
        return None
    host, path = match.group(1), match.group(2).strip("/")
    if not host or not path:
        return None
    return "%s/%s" % (host, path)


def git_info(directory: Path):
    """Read-only git facts for `directory`, which must already be known to hold a `.git` entry.

    Returns a dict with kind ("git repo" | "git worktree" | "git submodule"), main_repo (worktree/
    submodule only), branch, head_sha, dirty, remotes (deduped "host/path" strings), last_commit_time.
    Any field git could not produce (not a real repo, no commits yet, git missing) is None; `remotes`
    is `[]` rather than None.

    A `.git` FILE (rather than directory) means either a linked worktree ("gitdir: .../.git/
    worktrees/<name>") or a submodule checkout ("gitdir: .../.git/modules/<path>") -- both point
    somewhere else, but they are different relationships (a worktree shares its main repo's remotes
    and history; a submodule is a separate repo pinned by its superproject). Measured on this estate:
    C++ vendor trees (`.../core/deps/*`) are submodules, not worktrees, and were misclassified as
    "git worktree" with a lost main_repo before this distinction was added.
    """
    git_entry = directory / ".git"
    info = {
        "kind": "git repo",
        "main_repo": None,
        "branch": None,
        "head_sha": None,
        "dirty": None,
        "remotes": [],
        "last_commit_time": None,
    }
    if git_entry.is_file():
        try:
            gitdir_line = git_entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            gitdir_line = ""
        is_worktree = re.search(r"[\\/]\.git[\\/]worktrees[\\/][^\\/]+\s*$", gitdir_line) is not None
        if is_worktree:
            info["kind"] = "git worktree"
            common_dir = _run_git(directory, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
            if common_dir:
                info["main_repo"] = str(Path(common_dir).parent)
            else:
                # Fall back to parsing the pointer ourselves -- a git-internal file, not project
                # content, so reading its text is fine.
                found = re.search(r"gitdir:\s*(.+?)[\\/]\.git[\\/]worktrees[\\/][^\\/]+\s*$", gitdir_line)
                if found:
                    info["main_repo"] = found.group(1).strip()
        else:
            info["kind"] = "git submodule"
            superproject = _run_git(directory, ["rev-parse", "--show-superproject-working-tree"])
            info["main_repo"] = superproject or None

    branch = _run_git(directory, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch:
        info["branch"] = branch
    sha = _run_git(directory, ["rev-parse", "HEAD"])
    if sha:
        info["head_sha"] = sha
    status = _run_git(directory, ["status", "--porcelain"])
    if status is not None:
        info["dirty"] = bool(status)
    remote_out = _run_git(directory, ["remote", "-v"])
    if remote_out:
        seen = []
        for line in remote_out.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            hp = host_path_from_remote(parts[1])
            if hp and hp not in seen:
                seen.append(hp)
        info["remotes"] = seen
    commit_time = _run_git(directory, ["log", "-1", "--format=%cI"])
    if commit_time:
        info["last_commit_time"] = commit_time
    return info


# ---------------------------------------------------------------------------
# build files / candidate gates

def _npm_test_script_is_real(package_json: Path):
    try:
        data = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return False
    script = (data.get("scripts") or {}).get("test", "")
    if not script:
        return False
    # npm's own placeholder for "no test specified" -- not a real candidate gate.
    return "no test specified" not in script.lower()


def _make_has_test_target(makefile: Path):
    try:
        text = makefile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return re.search(r"(?m)^test\s*:", text) is not None


BUILD_FILE_MATCHERS = {
    "pyproject": lambda name: name == "pyproject.toml",
    "cargo": lambda name: name == "Cargo.toml",
    "npm": lambda name: name == "package.json",
    "go": lambda name: name == "go.mod",
    "makefile": lambda name: name in ("Makefile", "makefile", "GNUmakefile"),
    "cmake": lambda name: name == "CMakeLists.txt",
    "sln": lambda name: name.endswith(".sln"),
    "docker_compose": lambda name: fnmatch.fnmatch(name.lower(), "docker-compose*.yml")
    or fnmatch.fnmatch(name.lower(), "docker-compose*.yaml"),
}


def _is_k8s_yaml(rel_parts, name):
    """Unverified: this is a filename/path-shape heuristic (a kustomization file, or any yaml under
    a `k8s`/`kubernetes` directory), never checked against a real manifest in this estate. It only
    populates `build_files["k8s"]` -- there is deliberately no derived candidate gate for it (unlike
    pyproject/cargo/etc above): "apply this to a cluster" is not a safe thing to even suggest running
    the way `pytest` or `cargo test` is, so k8s presence is surfaced as a flag for a human, not a
    candidate command."""
    if not (name.endswith(".yaml") or name.endswith(".yml")):
        return False
    if name.lower() in ("kustomization.yaml", "kustomization.yml"):
        return True
    return any(p.lower() in ("k8s", "kubernetes") for p in rel_parts)


class _ProjectFiles:
    """Accumulates docs/build-files/secret-flags for ONE project boundary during its os.walk pass."""

    def __init__(self):
        self.build_files = {}  # type -> [relpath, ...]
        self.docs = []  # [{"path": relpath, "size": bytes}, ...]
        self.secret_flags = []  # [relpath, ...]

    def add_build_file(self, kind, relpath):
        self.build_files.setdefault(kind, []).append(relpath)

    def add_doc(self, path: Path, relpath: str):
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        self.docs.append({"path": relpath, "size": size})

    def add_secret(self, relpath):
        self.secret_flags.append(relpath)


def _collect_in_dir(project_root: Path, cur: Path, filenames, files: _ProjectFiles):
    rel_dir = cur.relative_to(project_root)
    rel_parts = rel_dir.parts
    for name in filenames:
        relpath = str((rel_dir / name)) if rel_parts else name
        for kind, matcher in BUILD_FILE_MATCHERS.items():
            if matcher(name):
                files.add_build_file(kind, relpath)
        if _is_k8s_yaml(rel_parts, name):
            files.add_build_file("k8s", relpath)

        lname = name.lower()
        if lname.startswith("readme") or lname in ("claude.md", "agents.md"):
            files.add_doc(cur / name, relpath)
        elif name.endswith(".md") and "docs" in (p.lower() for p in rel_parts):
            files.add_doc(cur / name, relpath)

        if any(fnmatch.fnmatch(lname, pat) for pat in _SECRET_PATTERNS):
            files.add_secret(relpath)


def candidate_gates(build_files: dict, project_root: Path):
    """Candidate gate commands derived from build files found -- listed, never run."""
    candidates = []
    if "pyproject" in build_files:
        candidates.append("pytest")
    if "cargo" in build_files:
        candidates.append("cargo test")
    if "npm" in build_files:
        for relpath in build_files["npm"]:
            if _npm_test_script_is_real(project_root / relpath):
                candidates.append("npm test")
                break
    if "go" in build_files:
        candidates.append("go test ./...")
    if "makefile" in build_files:
        for relpath in build_files["makefile"]:
            if _make_has_test_target(project_root / relpath):
                candidates.append("make test")
                break
    if "cmake" in build_files:
        candidates.append("ctest")
    if "sln" in build_files:
        # Unverified: unlike npm/make above, this is never checked against the .sln's actual
        # content (test project present, dotnet vs MSBuild-only solution, etc). Review flagged
        # this heuristic as untested against a real .sln in this estate -- rather than guess at a
        # verification rule, it is labelled so a human treats it as a hint, not a confirmed gate.
        candidates.append("dotnet test (unverified)")
    return candidates


# ---------------------------------------------------------------------------
# the walk

def _has_git_entry(directory: Path):
    return (directory / ".git").exists()


def _should_skip_dir(name):
    if name == ".git":
        return True
    if name in SKIP_DIRS:
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in SKIP_DIR_PATTERNS)


def scan_tree(project_root: Path, local_only_globs):
    """One record for `project_root`, followed by records for every nested project boundary inside
    it -- UNLESS `project_root` itself is local-only, in which case this returns just its own
    minimal record and never walks its contents at all (no file names, no nested-project search;
    a local-only folder's insides -- e.g. a private folder's own nested worktrees -- must not leak
    into the registry even as bare paths). Every file under a non-local-only `project_root` is visited
    by exactly one os.walk call in this recursion (see the module docstring's "PROJECT BOUNDARY"
    note).
    """
    git_data = git_info(project_root) if _has_git_entry(project_root) else None
    kind = git_data["kind"] if git_data else "plain folder"
    remotes = git_data["remotes"] if git_data else []
    if _is_local_only(project_root, kind, remotes, local_only_globs):
        return [_minimal_record(project_root, kind, git_data)]

    files = _ProjectFiles()
    nested_records = []

    for dirpath, dirnames, filenames in _walk(project_root):
        cur = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        if cur != project_root and _has_git_entry(cur):
            nested_records.extend(scan_tree(cur, local_only_globs))
            dirnames[:] = []  # this subtree is the nested project's own walk now, not ours
            continue
        _collect_in_dir(project_root, cur, filenames, files)

    own = _build_record(project_root, files, git_data)
    return [own] + nested_records


def _walk(project_root: Path):
    import os

    for dirpath, dirnames, filenames in os.walk(project_root):
        yield dirpath, dirnames, filenames


def _build_record(project_root: Path, files: _ProjectFiles, git_data):
    record = {
        "path": str(project_root),
        "kind": "plain folder",
        "main_repo": None,
        "branch": None,
        "head_sha": None,
        "dirty": None,
        "remotes": [],
        "last_commit_time": None,
        "build_files": files.build_files,
        "candidate_gates": candidate_gates(files.build_files, project_root),
        "docs": files.docs,
        "has_claude_agents": (project_root / ".claude" / "agents").is_dir(),
        "has_claude_skills": (project_root / ".claude" / "skills").is_dir(),
        "secret_flags": files.secret_flags,
        "local_only": False,
    }
    if git_data is not None:
        record.update(git_data)
    return record


def _is_local_only(path: Path, kind, remotes, local_only_globs):
    if any(fnmatch.fnmatch(str(path), glob) for glob in local_only_globs):
        return True
    if kind in ("git repo", "git worktree", "git submodule") and not remotes:
        return True
    return False


def _minimal_record(path: Path, kind, git_data):
    return {
        "path": str(path),
        "kind": kind,
        "head_sha": git_data["head_sha"] if git_data else None,
        "local_only": True,
    }


def scan_root(root: Path, local_only_globs=None):
    """Every project record under `root` (its immediate children plus anything nested beneath them,
    except beneath a local-only child -- see scan_tree)."""
    local_only_globs = list(local_only_globs or DEFAULT_LOCAL_ONLY_GLOBS)
    records = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or _should_skip_dir(child.name):
            continue
        records.extend(scan_tree(child, local_only_globs))
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m xscholar.scan",
        description="Read-only estate scan: one registry record per project under ROOT.",
    )
    parser.add_argument("root", help="projects root to scan")
    parser.add_argument("--out", required=True, help="path to write the registry JSON")
    parser.add_argument(
        "--local-only",
        action="append",
        default=None,
        help="glob (repeatable) marking a path local-only; default: %r" % (DEFAULT_LOCAL_ONLY_GLOBS,),
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print("not a directory: %s" % root, file=sys.stderr)
        return 2

    records = scan_root(root, args.local_only)
    Path(args.out).write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
    print("wrote %d record(s) to %s" % (len(records), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
