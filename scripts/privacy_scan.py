# -*- coding: utf-8 -*-
"""privacy_scan.py -- refuse to commit or push matter identifiers.

The deny list lives OUTSIDE the repository (it would itself leak identifiers):
  $GROUNDWORKS_DENYLIST, or ~/.groundworks/denylist.txt
One case-insensitive regular expression per line; blank lines and lines starting with # are ignored.
A match is reported by the deny list's LINE NUMBER, never by the pattern: the pattern is the identifier.

Fails CLOSED: a missing or empty deny list is exit 2 ("scan NOT EXECUTED"), never a pass.

    python scripts/privacy_scan.py                  # tracked files as STAGED (the index), plus untracked files
    python scripts/privacy_scan.py --all            # every file under the repo as it is on disk (except .git)
    python scripts/privacy_scan.py --message FILE   # a commit message (the commit-msg hook)
    python scripts/privacy_scan.py --push [REMOTE URL] < refs   # every commit message in the pushed range
                                                    # and every pushed ref name (the pre-push hook)
Exit 0 clean, 1 identifiers found, 2 could not scan.

The hooks that call it are in scripts/hooks/ and are installed by scripts/install_hooks.py.
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
ZERO = "0" * 40


def deny_patterns():
    """(deny-list line number, compiled pattern) for every pattern line."""
    path = pathlib.Path(os.environ.get("GROUNDWORKS_DENYLIST", pathlib.Path.home() / ".groundworks" / "denylist.txt"))
    if not path.exists():
        print("privacy scan NOT EXECUTED: deny list missing at %s" % path)
        sys.exit(2)
    pats = [(n, ln.strip()) for n, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if ln.strip() and not ln.startswith("#")]
    if not pats:
        print("privacy scan NOT EXECUTED: deny list is empty")
        sys.exit(2)
    return [(n, re.compile(p, re.I)) for n, p in pats]


def matches(pats, text):
    """Deny-list line numbers whose pattern matches `text`."""
    return [n for n, p in pats if p.search(text)]


def git(args, stdin=None):
    out = subprocess.run(["git"] + args, cwd=ROOT, input=stdin, capture_output=True)
    return out.returncode, out.stdout, out.stderr.decode("utf-8", "replace").strip()


def files(all_files):
    if all_files:
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]
    rc, out, err = git(["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    if rc != 0:
        print("privacy scan NOT EXECUTED: git ls-files failed: %s" % err)
        sys.exit(2)
    return [ROOT / f for f in out.decode("utf-8", "replace").split("\0") if f]


_index = None


def staged():
    """Every index entry's content (path -> bytes), read once with `git cat-file --batch`: what a commit
    would contain, not what the working tree happens to hold."""
    global _index
    if _index is not None:
        return _index
    rc, out, err = git(["ls-files", "--cached", "-z"])
    if rc != 0:
        print("privacy scan NOT EXECUTED: git ls-files --cached failed: %s" % err)
        sys.exit(2)
    paths = [p for p in out.decode("utf-8", "replace").split("\0") if p and "\n" not in p]
    rc, out, err = git(["cat-file", "--batch"], stdin="".join(":%s\n" % p for p in paths).encode("utf-8"))
    if rc != 0:
        print("privacy scan NOT EXECUTED: git cat-file failed: %s" % err)
        sys.exit(2)
    _index, pos = {}, 0
    for p in paths:
        nl = out.index(b"\n", pos)
        head = out[pos:nl].split()
        pos = nl + 1
        if len(head) == 3 and head[1] == b"blob":
            size = int(head[2])
            _index[p] = out[pos:pos + size]
            pos += size + 1
    return _index


def scan_files(argv, pats):
    all_files = "--all" in argv
    hits, scanned = [], 0
    unread = collections.Counter()
    for f in files(all_files):
        rel = f.relative_to(ROOT).as_posix()
        # A file this scan cannot read is not a file this scan cleared. Count it and name it below:
        # a PDF or a spreadsheet is exactly where an identifier hides, and silence about it reads
        # as "clean" to anyone who trusts the last line.
        if f.suffix.lower() in BINARY_EXT:
            if all_files or f.exists() or rel in staged():
                unread[f.suffix.lower() or "(no extension)"] += 1
            continue
        blob = None if all_files else staged().get(rel)
        if blob is not None:
            text = blob.decode("utf-8", "replace")
        else:
            if not f.exists():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                unread["unreadable"] += 1
                continue
        scanned += 1
        for n, line in enumerate(text.splitlines(), 1):
            for d in matches(pats, line):
                hits.append("%s:%d matches deny-list line %d" % (rel, n, d))
        # scan the path too
        for d in matches(pats, rel):
            hits.append("%s (path) matches deny-list line %d" % (rel, d))
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


def scan_message(path, pats):
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print("privacy scan NOT EXECUTED: commit message unreadable (%s)" % e.strerror)
        return 2
    hits = ["commit message line %d matches deny-list line %d" % (n, d)
            for n, line in enumerate(text.splitlines(), 1) for d in matches(pats, line)]
    if hits:
        print("privacy scan FAILED: the commit message matches the deny list; nothing was committed")
        for h in hits:
            print("  " + h)
        return 1
    print("privacy scan clean: commit message, %d patterns" % len(pats))
    return 0


def scan_push(pats, stdin):
    """Pre-push: stdin lines `<local ref> <local sha> <remote ref> <remote sha>`. Every ref name, and every
    commit message the push would publish (commits not already on the remote), is scanned."""
    hits, commits, refs = [], 0, 0
    for n, line in enumerate(stdin.splitlines(), 1):
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        refs += 1
        for side, name in (("local", local_ref), ("remote", remote_ref)):
            for d in matches(pats, name):
                hits.append("pushed ref %d (%s name) matches deny-list line %d" % (n, side, d))
        if local_sha == ZERO:
            continue  # a delete publishes no commit
        rng = [local_sha, "--not", "--remotes"] if remote_sha == ZERO else ["%s..%s" % (remote_sha, local_sha)]
        rc, out, err = git(["rev-list"] + rng)
        if rc != 0:
            print("privacy scan NOT EXECUTED: could not list the pushed commits of ref %d: %s" % (n, err))
            return 2
        for sha in out.decode().split():
            rc, msg, err = git(["log", "-1", "--format=%B", sha])
            if rc != 0:
                print("privacy scan NOT EXECUTED: could not read commit %s: %s" % (sha[:12], err))
                return 2
            commits += 1
            for ln, text in enumerate(msg.decode("utf-8", "replace").splitlines(), 1):
                for d in matches(pats, text):
                    hits.append("commit %s message line %d matches deny-list line %d" % (sha[:12], ln, d))
    if hits:
        print("privacy scan FAILED: the push would publish deny-listed text; nothing was pushed")
        for h in hits[:50]:
            print("  " + h)
        return 1
    print("privacy scan clean: %d ref(s), %d commit message(s), %d patterns" % (refs, commits, len(pats)))
    return 0


def main(argv):
    pats = deny_patterns()
    if "--message" in argv:
        i = argv.index("--message")
        if i + 1 >= len(argv):
            print("privacy scan NOT EXECUTED: --message needs the message file")
            return 2
        return scan_message(argv[i + 1], pats)
    if "--push" in argv:
        return scan_push(pats, sys.stdin.read())
    return scan_files(argv, pats)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
