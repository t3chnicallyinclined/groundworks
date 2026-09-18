# -*- coding: utf-8 -*-
"""privacy_scan.py -- refuse to commit matter identifiers.

The deny list lives OUTSIDE the repository (it would itself leak identifiers):
  $GROUNDWORKS_DENYLIST, or ~/.groundworks/denylist.txt
One case-insensitive regular expression per line; blank lines and lines starting with # are ignored.

Fails CLOSED: a missing or empty deny list is exit 2 ("scan NOT EXECUTED"), never a pass.

    python scripts/privacy_scan.py            # scan tracked + staged files
    python scripts/privacy_scan.py --all      # scan every file under the repo (except .git)
Exit 0 clean, 1 identifiers found, 2 could not scan.
"""
import collections
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Everything is scanned EXCEPT these. An allow list of text extensions fails OPEN: the scan used to
# walk straight past .csv, .xml, .sql, .log and .mdx without opening them and still print "clean",
# and every new text format would have been another silent hole. A deny list of binary extensions
# fails CLOSED instead -- an unknown extension is read, not skipped.
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".avif", ".ico", ".tif", ".tiff",
              ".pdf", ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tar", ".jar", ".whl",
              ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".pyc", ".pyd", ".o", ".a",
              ".woff", ".woff2", ".ttf", ".otf", ".eot",
              ".mp3", ".mp4", ".wav", ".flac", ".ogg", ".avi", ".mov", ".mkv", ".webm",
              ".db", ".sqlite", ".sqlite3", ".parquet", ".xlsx", ".xls", ".docx", ".doc", ".pptx"}


def deny_patterns():
    path = pathlib.Path(os.environ.get("GROUNDWORKS_DENYLIST", pathlib.Path.home() / ".groundworks" / "denylist.txt"))
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
    unread = collections.Counter()
    for f in files("--all" in argv):
        if not f.exists():
            continue
        # A file this scan cannot read is not a file this scan cleared. Count it and name it below:
        # a PDF or a spreadsheet is exactly where an identifier hides, and silence about it reads
        # as "clean" to anyone who trusts the last line.
        if f.suffix.lower() in BINARY_EXT:
            unread[f.suffix.lower() or "(no extension)"] += 1
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            unread["unreadable"] += 1
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
    skipped = ", ".join("%d %s" % (n, ext) for ext, n in sorted(unread.items(), key=lambda kv: -kv[1]))
    tail = "; %d file(s) NOT read (%s)" % (sum(unread.values()), skipped) if unread else ""
    if hits:
        print("privacy scan FAILED: %d match(es) in %d files scanned%s" % (len(hits), scanned, tail))
        for h in hits[:50]:
            print("  " + h)
        return 1
    if not scanned:
        # A scan that opened nothing has cleared nothing. It used to print "clean: 0 files".
        print("privacy scan NOT EXECUTED: 0 files were read%s. A scan that opened nothing "
              "has cleared nothing." % tail)
        return 2
    print("privacy scan clean: %d files, %d patterns%s" % (scanned, len(pats), tail))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
