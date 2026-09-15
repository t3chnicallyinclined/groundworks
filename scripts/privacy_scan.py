# -*- coding: utf-8 -*-
"""privacy_scan.py -- refuse to commit matter identifiers.

The deny list lives OUTSIDE the repository (it would itself leak identifiers):
  $XSCHOLAR_DENYLIST, or ~/.xscholar/denylist.txt
One case-insensitive regular expression per line; blank lines and lines starting with # are ignored.

Fails CLOSED: a missing or empty deny list is exit 2 ("scan NOT EXECUTED"), never a pass.

    python scripts/privacy_scan.py            # scan tracked + staged files
    python scripts/privacy_scan.py --all      # scan every file under the repo (except .git)
Exit 0 clean, 1 identifiers found, 2 could not scan.
"""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEXT_EXT = {".md", ".txt", ".py", ".yaml", ".yml", ".json", ".jsonl", ".surql", ".toml", ".js", ".ts", ".rs", ".go", ".sh", ".ps1", ".html", ".css", ""}


def deny_patterns():
    path = pathlib.Path(os.environ.get("XSCHOLAR_DENYLIST", pathlib.Path.home() / ".xscholar" / "denylist.txt"))
    if not path.exists():
        print("privacy scan NOT EXECUTED: deny list missing at %s" % path)
        sys.exit(2)
    pats = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    if not pats:
        print("privacy scan NOT EXECUTED: deny list is empty")
        sys.exit(2)
    return [re.compile(p, re.I) for p in pats]


def files(all_files):
    if all_files:
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:
        print("privacy scan NOT EXECUTED: git ls-files failed: %s" % out.stderr.strip())
        sys.exit(2)
    return [ROOT / f for f in out.stdout.splitlines() if f]


def main(argv):
    pats = deny_patterns()
    hits, scanned = [], 0
    for f in files("--all" in argv):
        if f.suffix.lower() not in TEXT_EXT or not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        for n, line in enumerate(text.splitlines(), 1):
            for p in pats:
                if p.search(line):
                    hits.append("%s:%d matches /%s/" % (f.relative_to(ROOT), n, p.pattern))
        # scan the path too
        for p in pats:
            if p.search(str(f.relative_to(ROOT))):
                hits.append("%s (path) matches /%s/" % (f.relative_to(ROOT), p.pattern))
    if hits:
        print("privacy scan FAILED: %d match(es) in %d files scanned" % (len(hits), scanned))
        for h in hits[:50]:
            print("  " + h)
        return 1
    print("privacy scan clean: %d files, %d patterns" % (scanned, len(pats)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
