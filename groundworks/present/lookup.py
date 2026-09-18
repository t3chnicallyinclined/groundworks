# -*- coding: utf-8 -*-
"""lookup.py -- budgeted truth lookups for reviewer experiments.

A reviewer reading a planted rendering may look up the true (clean) row for a few ids. Each successful
lookup prints the true row as JSON and is appended to the log; once the log holds BUDGET successful
lookups, every further request is refused (exit 3) and the refusal is logged too.

    python lookup.py TRUTH_ROWS.json LOG.jsonl ROW_ID --budget N

Exit codes: 0 printed; 3 refused (budget used); 4 no such row (logged, does not use the budget);
2 NOT EXECUTED (unreadable truth or log, a seeded test copy given as truth, bad budget).

Standard library only.
"""
import argparse
import json
import pathlib
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks.present import features


def read_log(path):
    path = pathlib.Path(path)
    if not path.exists():
        return []
    entries = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError as exc:
            raise ValueError("log %s line %d is not JSON: %s" % (path, n, exc)) from exc
        if not isinstance(entry, dict):
            raise ValueError("log %s line %d is not an object" % (path, n))
        entries.append(entry)
    return entries


def _append(path, entry):
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def lookup(truth_path, log_path, row_id, budget, out=sys.stdout, err=sys.stderr):
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        print("lookup NOT EXECUTED: budget must be an integer >= 0", file=err)
        return 2
    try:
        truth = features.validate_rows(json.loads(pathlib.Path(truth_path).read_text(encoding="utf-8")))
        entries = read_log(log_path)
    except (OSError, ValueError) as exc:
        print("lookup NOT EXECUTED: %s" % exc, file=err)
        return 2
    if truth["seeded_test_copy"]:
        print("lookup NOT EXECUTED: %s is a seeded test copy; give the clean truth rows" % truth_path, file=err)
        return 2
    used = sum(1 for e in entries if e.get("result") == "ok")
    base = {"n": len(entries) + 1, "row_id": row_id, "budget": budget}
    if used >= budget:
        _append(log_path, dict(base, result="refused", used=used))
        print("REFUSED: lookup budget of %d already used" % budget, file=err)
        return 3
    row = next((r for r in truth["rows"] if r["id"] == row_id), None)
    if row is None:
        _append(log_path, dict(base, result="not_found", used=used))
        print("NOT FOUND: no row %r (budget unchanged: %d of %d used)" % (row_id, used, budget), file=err)
        return 4
    _append(log_path, dict(base, result="ok", used=used + 1))
    out.write(json.dumps(row, indent=2, ensure_ascii=False) + "\n")
    print("lookup %d of %d" % (used + 1, budget), file=err)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Print one true row, within a lookup budget.")
    ap.add_argument("truth")
    ap.add_argument("log")
    ap.add_argument("row_id")
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args(argv)
    getattr(sys.stdout, "reconfigure", lambda **_: None)(encoding="utf-8")
    return lookup(args.truth, args.log, args.row_id, args.budget)


if __name__ == "__main__":
    sys.exit(main())
