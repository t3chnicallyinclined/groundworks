# -*- coding: utf-8 -*-
"""agreement.py -- how far to trust the labels: two independent labellers, one slice, measured.

Validation proves shape (coverage, vocabulary, verbatim quotes). It does not prove the labels are
right. The only honest check is agreement between independent labellers on the same units, with
disagreements adjudicated by a human against the source. This module computes it.

    report = compare(labels_a_path, labels_b_path)
    python -m groundworks.pipeline.agreement A.jsonl B.jsonl [--out report.json]

Per field: exact agreement; for tier also "within one"; for set fields (issues, targets) mean
Jaccard overlap; Cohen's kappa for tier and direction (chance-corrected: 1.0 perfect, 0 chance,
below 0 worse than chance). Every disagreement is listed with both values so it can be adjudicated.

`adjudicate` is EVERY unit with any disagreement, each once, most consequential first: a relevance split
(one labeller tier 2+, the other not), then a split within the relevant tiers (2 vs 3: load-bearing or merely
relevant), then a direction flip on a unit both rated relevant, then any other tier split, then disagreements on direction, role, issues or targets only. It used to
be the relevance splits plus the first 20 other units, with tier-only splits never listed: measured on another
instance, 90 double-labelled units with 51 needing a human printed "27 unit(s) to adjudicate", and a reader who
reviewed those 27 believed they had reviewed them all. `len(adjudicate) == units_with_any_disagreement` always.

Kappa is None, never 1.0, on a degenerate sample -- one label value across every unit, where chance
agreement is already 1.0. The report then carries `kappa_undefined` naming the field and the reason,
so "20 units, exact 1.00" cannot be read as perfect agreement when nothing was measured.
Standard library only.
"""
import argparse
import json
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks.pipeline.intake import read_units  # noqa: E402

SCALAR_FIELDS = ("tier", "dir", "role")
SET_FIELDS = ("issues", "targets")


class AgreementError(Exception):
    pass


def kappa(pairs):
    """Cohen's kappa for a list of (a, b) category pairs. None when undefined.

    Undefined means chance agreement is already 1.0: one label value covers the whole sample, so
    two labellers who never looked at anything would agree just as often. Returning 1.0 there
    reports a sample that measured nothing as perfect agreement; use kappa_undefined() for the
    reason to print beside it.
    """
    n = len(pairs)
    if n == 0:
        return None
    cats = sorted({str(a) for a, _ in pairs} | {str(b) for _, b in pairs})
    observed = sum(1 for a, b in pairs if str(a) == str(b)) / n
    pa = {c: sum(1 for a, _ in pairs if str(a) == c) / n for c in cats}
    pb = {c: sum(1 for _, b in pairs if str(b) == c) / n for c in cats}
    expected = sum(pa[c] * pb[c] for c in cats)
    if expected >= 1.0:
        return None
    return round((observed - expected) / (1.0 - expected), 4)


def kappa_undefined(pairs):
    """Why kappa is None for these pairs, or "" when it is defined."""
    if not pairs:
        return "no pairs to compare"
    values = sorted({str(a) for a, _ in pairs} | {str(b) for _, b in pairs})
    if len(values) <= 1:
        return ("only one value (%s) occurs across all %d unit(s): chance agreement is already 1.0, "
                "so kappa is undefined and the exact-agreement figure measures nothing"
                % (values[0] if values else "none", len(pairs)))
    if kappa(pairs) is None:
        return ("chance agreement over these %d unit(s) is already 1.0 (values %s), so kappa is "
                "undefined" % (len(pairs), ", ".join(values)))
    return ""


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
            entry["distinct_values"] = len({str(x) for x, _ in pairs} | {str(y) for _, y in pairs})
            entry["kappa_undefined"] = kappa_undefined(pairs)
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
    # the count that disagrees with a high exact-agreement figure: fields where kappa could not be
    # computed at all, because one label value covered the whole sample
    tiers = {i: (a[i].get("tier"), b[i].get("tier")) for i in ids}
    degenerate = {f: fields[f]["kappa_undefined"] for f in ("tier", "dir") if fields[f]["kappa_undefined"]}
    return {"units": n, "fields": fields, "disagreements": disagreements,
            "kappa_undefined": degenerate,
            "units_with_any_disagreement": len({d["unit"] for d in disagreements}),
            "relevance_split": {"count": len(salient), "share": round(len(salient) / n, 4),
                                "meaning": "one labeller rated the unit relevant (tier 2+) and the other did not"},
            "adjudicate": adjudication_order(disagreements, tiers),
            "adjudicate_groups": adjudication_groups(disagreements, tiers)}


ADJUDICATION_GROUPS = ("relevance_split", "within_relevant_tiers", "direction_on_relevant", "other_tier",
                       "other_fields")


def _group(unit_disagreements, tiers=None):
    for d in unit_disagreements:
        if d["field"] != "tier":
            continue
        rel_a = isinstance(d["a"], int) and d["a"] >= 2
        rel_b = isinstance(d["b"], int) and d["b"] >= 2
        if rel_a != rel_b:
            return 0
    for d in unit_disagreements:
        if d["field"] == "tier" and isinstance(d["a"], int) and isinstance(d["b"], int) and d["a"] >= 2 and d["b"] >= 2:
            return 1
    # both labellers rated the unit relevant and read it in opposite directions: what the claim means is in dispute,
    # which matters more than a 0-vs-1 split (suggested by the SUPERGUN instance from its prose units)
    if tiers and all(isinstance(t, int) and t >= 2 for t in tiers) and any(d["field"] == "dir" for d in unit_disagreements):
        return 2
    if any(d["field"] == "tier" for d in unit_disagreements):
        return 3
    return 4


def _by_unit(disagreements):
    units = {}
    for d in disagreements:
        units.setdefault(d["unit"], []).append(d)
    return units


def adjudication_order(disagreements, tiers=None):
    """Every unit with any disagreement, once, ordered by ADJUDICATION_GROUPS then by id. Never capped.
    `tiers` is unit -> (tier in A, tier in B)."""
    units = _by_unit(disagreements)
    tiers = tiers or {}
    return sorted(units, key=lambda u: (_group(units[u], tiers.get(u)), str(u)))


def adjudication_groups(disagreements, tiers=None):
    units = _by_unit(disagreements)
    tiers = tiers or {}
    counts = {name: 0 for name in ADJUDICATION_GROUPS}
    for u in units:
        counts[ADJUDICATION_GROUPS[_group(units[u], tiers.get(u))]] += 1
    return counts


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
    g = report["adjudicate_groups"]
    print("relevance split (one saw tier 2+, the other did not): %d of %d units; %d unit(s) to adjudicate, all "
          "listed in --out (%d relevance split, %d tier 2 vs 3, %d direction on a relevant unit, %d other tier, "
          "%d other fields only)"
          % (report["relevance_split"]["count"], report["units"], len(report["adjudicate"]), g["relevance_split"],
             g["within_relevant_tiers"], g["direction_on_relevant"], g["other_tier"], g["other_fields"]))
    for field, why in sorted(report["kappa_undefined"].items()):
        print("KAPPA NOT MEASURED for %s: %s" % (field, why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
