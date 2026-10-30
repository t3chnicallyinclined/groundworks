# -*- coding: utf-8 -*-
"""install_hooks.py -- install the privacy hooks into this repository's git hooks folder.

    python scripts/install_hooks.py            # install pre-commit, commit-msg and pre-push
    python scripts/install_hooks.py --check    # exit 1 if any is missing or differs

pre-commit runs the file scan (staged content) and the license check; commit-msg scans the message;
pre-push scans every commit message in the pushed range and every pushed ref name. Each hook is written
with the Python this script runs under, so a hook never depends on PATH. The hooks folder is shared by
every worktree of the repository: install once a branch carrying this scanner is the one checked out
wherever commits are made, since an older scanner ignores --message and --push.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRE_COMMIT = ('#!/bin/sh\n"{py}" scripts/privacy_scan.py || exit $?\n'
              'exec "{py}" scripts/license_check.py\n')


def hooks_dir():
    out = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("not a git repository: %s" % out.stderr.strip())
    common = pathlib.Path(out.stdout.strip())
    return (common if common.is_absolute() else ROOT / common) / "hooks"


def wanted():
    py = pathlib.Path(sys.executable).as_posix()
    src = ROOT / "scripts" / "hooks"
    return {
        "pre-commit": PRE_COMMIT.format(py=py),
        "commit-msg": (src / "commit-msg").read_text(encoding="utf-8").replace("${GROUNDWORKS_PYTHON:-python}", py),
        "pre-push": (src / "pre-push").read_text(encoding="utf-8").replace("${GROUNDWORKS_PYTHON:-python}", py),
    }


def main(argv):
    target = hooks_dir()
    hooks = wanted()
    if "--check" in argv:
        bad = [n for n, body in hooks.items()
               if not (target / n).exists() or (target / n).read_text(encoding="utf-8") != body]
        print("hooks %s: %s" % ("DRIFT" if bad else "ok", ", ".join(bad) or "pre-commit, commit-msg, pre-push"))
        return 1 if bad else 0
    target.mkdir(parents=True, exist_ok=True)
    for name, body in hooks.items():
        path = target / name
        path.write_text(body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    print("installed %s into %s" % (", ".join(hooks), target))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
