# -*- coding: utf-8 -*-
"""agreement.py -- how far to trust the labels: two independent labellers, one slice, measured.

Validation proves shape (coverage, vocabulary, verbatim quotes). It does not prove the labels are
right. The only honest check is agreement between independent labellers on the same units, with
disagreements adjudicated by a human against the source. This module computes it.

    report = compare(labels_a_path, labels_b_path)
    python -m xscholar.pipeline.agreement A.jsonl B.jsonl [--out report.json]

Per field: exact agreement; for tier also "within one"; for set fields (issues, targets) mean
Jaccard overlap; Cohen's kappa for tier and direction (chance-corrected: 1.0 perfect, 0 chance,
below 0 worse than chance). Every disagreement is listed with both values so it can be adjudicated.
Standard library only.
"""
import argparse
import json
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.pipeline.intake import read_units  # noqa: E402

SCALAR_FIELDS = ("tier", "dir", "role")
SET_FIELDS = ("issues", "targets")


class AgreementError(Exception):
    pass


def kappa(pairs):
    """Cohen's kappa for a list of (a, b) category pairs. None when undefined."""
    n = len(pairs)
    if n == 0:
        return None
    cats = sorted({str(a) for a, _ in pairs} | {str(b) for _, b in pairs})
    observed = sum(1 for a, b in pairs if str(a) == str(b)) / n
    pa = {c: sum(1 for a, _ in pairs if str(a) == c) / n for c in cats}
    pb = {c: sum(1 for _, b in pairs if str(b) == c) / n for c in cats}
    expected = sum(pa[c] * pb[c] for c in cats)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return round((observed - expected) / (1.0 - expected), 4)


def jaccard(a, b):
    a, b = set(a or []), set(b or [])
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def compare(path_a, path_b):
    a = {l["id"]: l for l in read_units(path_a)}
    b = {l["id"]: l for l in read_units(path_b)}
    if set(a) != set(b):
        only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
        raise AgreementError("the two label files cover different units: only in A %s; only in B %s"
                             % (only_a[:5], only_b[:5]))
    ids = sorted(a)
    n = len(ids)
    if n == 0:
        raise AgreementError("no labels to compare")
    fields = {}
    disagreements = []
    for field in SCALAR_FIELDS:
        pairs = [(a[i].get(field), b[i].get(field)) for i in ids]
        exact = sum(1 for x, y in pairs if x == y)
        entry = {"exact": round(exact / n, 4), "n": n}
        if field == "tier":
            entry["within_one"] = round(sum(1 for x, y in pairs if isinstance(x, int) and isinstance(y, int) and abs(x - y) <= 1) / n, 4)
        if field in ("tier", "dir"):
            entry["kappa"] = kappa(pairs)
        fields[field] = entry
        for i, (x, y) in zip(ids, pairs):
            if x != y:
                disagreements.append({"unit": i, "field": field, "a": x, "b": y})
    for field in SET_FIELDS:
        scores = [jaccard(a[i].get(field), b[i].get(field)) for i in ids]
        fields[field] = {"mean_jaccard": round(sum(scores) / n, 4), "exact": round(sum(1 for s in scores if s == 1.0) / n, 4), "n": n}
        for i, s in zip(ids, scores):
            if s < 1.0:
                disagreements.append({"unit": i, "field": field, "a": a[i].get(field), "b": b[i].get(field)})
    # the disagreements that matter most: one labeller saw load-bearing material, the other did not
    salient = [d for d in disagreements if d["field"] == "tier" and (
        (isinstance(d["a"], int) and d["a"] >= 2) != (isinstance(d["b"], int) and d["b"] >= 2))]
    return {"units": n, "fields": fields, "disagreements": disagreements,
            "units_with_any_disagreement": len({d["unit"] for d in disagreements}),
            "relevance_split": {"count": len(salient), "share": round(len(salient) / n, 4),
                                "meaning": "one labeller rated the unit relevant (tier 2+) and the other did not"},
            "adjudicate": [d["unit"] for d in salient] + sorted({d["unit"] for d in disagreements if d["field"] != "tier"})[:20]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agreement between two independent label files for one slice.")
    parser.add_argument("labels_a")
    parser.add_argument("labels_b")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        report = compare(args.labels_a, args.labels_b)
    except AgreementError as exc:
        print("NOT COMPARED: %s" % exc)
        return 2
    if args.out:
        _pathlib.Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    f = report["fields"]
    print("agreement over %d units: tier exact %.2f (within one %.2f, kappa %s) | dir exact %.2f (kappa %s) | "
          "issues jaccard %.2f | targets jaccard %.2f | role exact %.2f"
          % (report["units"], f["tier"]["exact"], f["tier"]["within_one"], f["tier"]["kappa"],
             f["dir"]["exact"], f["dir"]["kappa"], f["issues"]["mean_jaccard"], f["targets"]["mean_jaccard"], f["role"]["exact"]))
    print("relevance split (one saw tier 2+, the other did not): %d of %d units; %d unit(s) to adjudicate"
          % (report["relevance_split"]["count"], report["units"], len(report["adjudicate"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
