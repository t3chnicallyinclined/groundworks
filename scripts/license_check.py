# -*- coding: utf-8 -*-
"""license_check.py -- refuse to commit an unlisted third-party import or a banned model.

Two checks over the repository:
  1. every non-standard-library, non-local Python import in *.py must appear in THIRD_PARTY_NOTICES.md
     as a backticked import name (for example `mcp`)
  2. no code or config file (.py .yaml .yml .json .toml .txt .surql .cfg .ini) names a banned item
     (non-commercial models and similar; list below, sourced from docs/LICENSING.md)
Markdown is exempt from check 2 so policy documents can name what is banned.

    python scripts/license_check.py            # scan the repository
    python scripts/license_check.py ROOT       # scan another tree (tests)
Exit 0 clean, 1 violations, 2 could not scan (NOT EXECUTED).
"""
import ast
import pathlib
import re
import sys

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


def local_modules(root):
    names = set()
    for path in root.rglob("*.py"):
        names.add(path.stem)
    for path in root.rglob("__init__.py"):
        names.add(path.parent.name)
    return names


def imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0], node.lineno


def scan(root):
    notices_path = root / "THIRD_PARTY_NOTICES.md"
    if not notices_path.exists():
        return None, ["THIRD_PARTY_NOTICES.md missing at %s" % root]
    listed = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", notices_path.read_text(encoding="utf-8")))
    stdlib = set(sys.stdlib_module_names)
    local = local_modules(root)
    problems = []
    for path in files(root, {".py"}):
        try:
            found = list(imports(path))
        except SyntaxError as exc:
            problems.append("%s: cannot parse (%s)" % (path.relative_to(root), exc.msg))
            continue
        for name, line in found:
            if name in stdlib or name in local or name == "__future__" or name in listed:
                continue
            problems.append("%s:%d: import '%s' is not listed in THIRD_PARTY_NOTICES.md"
                            % (path.relative_to(root), line, name))
    for path in files(root, CONFIG_EXT):
        if path.name in SELF:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for term, why in BANNED.items():
            if term.lower() in text:
                problems.append("%s: names banned item '%s' (%s)" % (path.relative_to(root), term, why))
    return True, problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = pathlib.Path(argv[0]).resolve() if argv else pathlib.Path(__file__).resolve().parents[1]
    ran, problems = scan(root)
    if ran is None:
        print("license check NOT EXECUTED: " + problems[0])
        return 2
    for line in problems:
        print("LICENSE " + line)
    print("license check %s: %d problem(s)" % ("clean" if not problems else "FAILED", len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
