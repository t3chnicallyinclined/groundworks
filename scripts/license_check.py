# -*- coding: utf-8 -*-
"""license_check.py -- refuse to commit an unlisted third-party import or a banned model.

Two checks over the repository:
  1. every non-standard-library, non-local Python import in *.py must appear in THIRD_PARTY_NOTICES.md
     as a backticked name written right after the word import (for example "Python MCP SDK, import `mcp`").
     Every other backticked token in the notices (a path such as `docs/LICENSING.md`, a directory such as
     `notes`) names nothing importable and allows nothing.
  2. no code or config file (.py .yaml .yml .json .toml .txt .surql .cfg .ini) names a banned item
     (non-commercial models and similar; list below, sourced from docs/LICENSING.md)
  3. every registry dependency in any Cargo.toml (dependencies, dev-, build-, target-specific and
     workspace dependencies) must appear in THIRD_PARTY_NOTICES.md as a backticked name written right
     after the word crate (for example "Rust crate `tokio`"). A path dependency is local. A Cargo.toml
     holding `{{` is a project template, not a manifest, and is skipped. Added 2026-10-25: until then
     no Rust dependency was checked, and none was listed.
Markdown is exempt from check 2 so policy documents can name what is banned.

What counts as local (check 1). Python resolves `import name` from the importing file's own directory
(sys.path[0] when that file runs), from the repository root (where the packages are installed from), and
from any directory the file itself puts on sys.path with a string literal (`sys.path.insert(0, ... "scripts")`).
Only those are local, and only for THAT file. Until 2026-09-16 every *.py stem anywhere in the tree counted
as local for every file, so a stray `scripts/yaml.py` allowed `import yaml` repo-wide (measured: exit 0).

Every run prints how many files it scanned. A tree with no Python file checked nothing, so it is
NOT EXECUTED (exit 2), never clean (measured before the fix: such a tree printed `clean: 0 problem(s)`).

    python scripts/license_check.py            # scan the repository
    python scripts/license_check.py ROOT       # scan another tree (tests)
Exit 0 clean, 1 violations, 2 could not scan (NOT EXECUTED).
"""
import ast
import pathlib
import re
import sys
import tomllib

BANNED = {
    "jina-embeddings-v3": "CC-BY-NC-4.0, non-commercial (docs/LICENSING.md s.4)",
    "jinaai/jina-embeddings-v3": "CC-BY-NC-4.0, non-commercial (docs/LICENSING.md s.4)",
}
CONFIG_EXT = {".py", ".yaml", ".yml", ".json", ".toml", ".txt", ".surql", ".cfg", ".ini"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}
SELF = {"license_check.py", "test_license_check.py"}


def files(root, exts):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in exts and not (set(path.relative_to(root).parts) & SKIP_DIRS):
            yield path


IMPORT_NAME = re.compile(r"\bimport\s+`([A-Za-z_][A-Za-z0-9_]*)`")


def listed_crates(text):
    """The crate names THIRD_PARTY_NOTICES.md allows: a backticked name written right after `crate`."""
    return set(re.findall(r"\bcrate `([^`]+)`", text))


def cargo_dependencies(manifest):
    """[(section, name)] of every registry (non-path) dependency a Cargo.toml declares."""
    data = tomllib.loads(manifest)
    tables = []
    for key in ("dependencies", "dev-dependencies", "build-dependencies"):
        tables.append((key, data.get(key, {})))
    tables.append(("workspace.dependencies", data.get("workspace", {}).get("dependencies", {})))
    for target, spec in data.get("target", {}).items():
        for key in ("dependencies", "dev-dependencies", "build-dependencies"):
            tables.append(("target.%s.%s" % (target, key), spec.get(key, {})))
    found = []
    for section, table in tables:
        for name, spec in table.items():
            if isinstance(spec, dict) and "path" in spec:
                continue
            found.append((section, spec.get("package", name) if isinstance(spec, dict) else name))
    return found


def listed_imports(text):
    """The import names THIRD_PARTY_NOTICES.md allows: a backticked name written right after `import`."""
    return set(IMPORT_NAME.findall(text))


def _modules_in(directory):
    """Top-level names importable from `directory`: its *.py files and its package directories."""
    names = set()
    if directory.is_dir():
        for path in directory.iterdir():
            if path.is_file() and path.suffix == ".py":
                names.add(path.stem)
            elif path.is_dir() and (path / "__init__.py").is_file():
                names.add(path.name)
    return names


def path_dirs(tree, root):
    """Directories inside `root` that a file adds to sys.path by a string literal inside the call itself
    (`sys.path.insert(0, str(... / "scripts"))`). A computed path is not followed."""
    found = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("insert", "append") and ast.unparse(node.func.value) == "sys.path"):
            for arg in node.args:
                for const in ast.walk(arg):
                    if isinstance(const, ast.Constant) and isinstance(const.value, str) and const.value.strip("./\\"):
                        candidate = (root / const.value).resolve()
                        if candidate.is_dir() and candidate != root.resolve():
                            found.add(candidate)
    return found


def local_modules(root, path=None, tree=None):
    """The names local to the file at `path`: the repository root, the file's own directory, and any
    directory the file puts on sys.path by literal. With no path, only the root."""
    names = _modules_in(root)
    if path is not None:
        names |= _modules_in(path.parent)
    if tree is not None:
        for directory in path_dirs(tree, root):
            names |= _modules_in(directory)
    return names


def imports(path, tree=None):
    tree = tree if tree is not None else ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0], node.lineno


def scan(root):
    """(counts, problems); or (None, [why nothing was checked])."""
    notices_path = root / "THIRD_PARTY_NOTICES.md"
    if not notices_path.exists():
        return None, ["THIRD_PARTY_NOTICES.md missing at %s" % root]
    notices = notices_path.read_text(encoding="utf-8")
    listed = listed_imports(notices)
    crates = listed_crates(notices)
    stdlib = set(sys.stdlib_module_names)
    problems = []
    counts = {"python_files": 0, "imports": 0, "third_party": 0, "config_files": 0, "listed": len(listed),
              "manifests": 0, "crates": 0}
    for path in files(root, {".py"}):
        counts["python_files"] += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            problems.append("%s: cannot parse (%s)" % (path.relative_to(root), exc.msg))
            continue
        local = local_modules(root, path, tree)
        for name, line in imports(path, tree):
            counts["imports"] += 1
            if name in stdlib or name in local or name == "__future__":
                continue
            counts["third_party"] += 1
            if name in listed:
                continue
            problems.append("%s:%d: import '%s' is not listed in THIRD_PARTY_NOTICES.md"
                            % (path.relative_to(root), line, name))
    for path in files(root, {".toml"}):
        if path.name != "Cargo.toml" or "target" in path.relative_to(root).parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "{{" in text:
            continue
        counts["manifests"] += 1
        try:
            deps = cargo_dependencies(text)
        except tomllib.TOMLDecodeError as exc:
            problems.append("%s: cannot parse (%s)" % (path.relative_to(root), exc))
            continue
        for section, name in deps:
            counts["crates"] += 1
            if name not in crates:
                problems.append("%s: [%s] crate '%s' is not listed in THIRD_PARTY_NOTICES.md"
                                % (path.relative_to(root), section, name))
    for path in files(root, CONFIG_EXT):
        if path.name in SELF:
            continue
        counts["config_files"] += 1
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for term, why in BANNED.items():
            if term.lower() in text:
                problems.append("%s: names banned item '%s' (%s)" % (path.relative_to(root), term, why))
    if not counts["python_files"] and not problems:     # a banned item found is still a failure
        return None, ["no Python file under %s, so the import check scanned nothing; that is not a clean result "
                      "(%d config file(s) scanned for banned items, %d problem(s))"
                      % (root, counts["config_files"], len(problems))]
    return counts, problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = pathlib.Path(argv[0]).resolve() if argv else pathlib.Path(__file__).resolve().parents[1]
    counts, problems = scan(root)
    if counts is None:
        print("license check NOT EXECUTED: " + problems[0])
        return 2
    for line in problems:
        print("LICENSE " + line)
    print("license check %s: %d python file(s), %d import(s), %d third-party checked against %d listed name(s), "
          "%d Cargo manifest(s) with %d crate dependency(ies), %d config file(s), %d problem(s)"
          % ("clean" if not problems else "FAILED", counts["python_files"], counts["imports"],
             counts["third_party"], counts["listed"], counts["manifests"], counts["crates"],
             counts["config_files"], len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
