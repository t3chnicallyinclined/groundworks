# -*- coding: utf-8 -*-
"""metrics.py -- deterministic comparison of renderings of the same rows.

For each mode: characters and lines (whole rendering), scan-layer characters and lines, the
preservation check, the first line on which each check-first row and each planted row appears, and
how many of them are visible within the first K lines. Positions and visibility are measured over
the *scan layer* -- what a reader sees before the Details section -- because that is what a two-layer
rendering asks the reader to scan.

Preservation (must be 100% in every mode): after undoing presentation (bold markers, <br>, table
escapes), the output holds at least as many occurrences as the input of
  ids        every row id (bounded match)
  numbers    every number in text and locators
  negations  every negation word or phrase in text (policy words included)
  locators   every locator string
  texts      every row's full text, verbatim
Counts are compared as multisets, so a dropped row fails even when its tokens occur in other rows.
In a two-layer mode these five are counted over the Details layer alone: the scan layer shows leads,
so counting the whole rendering would let a Details section drop a short row unnoticed. Two further
checks then cover the scan layer:
  scan_ids    every row is named in the scan layer (nothing is hidden below the fold)
  scan_leads  every row that has its own scan line shows a lead that is a verbatim prefix of its
              text. Rows named only inside a collapsed group line have no lead of their own, so
              they are exempt from this one -- scan_ids still requires them to be named.
The claims view is checked over claim and conclusion rows (its header names the rows it leaves out).

Every result names the state of all seven checks: `checks.ran`, `checks.not_applicable` (a one-layer
rendering has no scan layer) and `checks.not_run`. A check in `not_run` forces `ok` false, because a
check that stops being generated is indistinguishable from one that passed. Two ways that happens:
a scope with 0 rows (100% of nothing is not preservation), and a TWO_LAYER mode whose rendering has
no `## Details:` heading (the scan-layer checks have nothing to split).

The inline rule re-check is reported too: rule_check counts pass, fail and not_checked over every
row and names the rows each rule flagged; every mode reports the first scan line of each rule-fail
row and how many are visible within K lines; and with a manifest, planted_rule_flagged says how many
planted rows a rule flagged, split by mutation type. That is the number C18 is measured by.

    python metrics.py ROWS.json [--policy P.json] [--manifest MANIFEST.json]

Exit 0 all modes preserved; 1 a mode failed preservation; 2 NOT EXECUTED.

Standard library only.
"""
import argparse
import collections
import json
import pathlib
import re
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.present import features, policy as policy_mod, render

KS = (10, 20, 40)
CATEGORIES = ("ids", "numbers", "negations", "locators", "texts")
SCAN_CHECKS = ("scan_ids", "scan_leads")


def normalize_input(s):
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("**", "").replace("<br>", "\n")


def normalize(rendered):
    lines = []
    for line in rendered.split("\n"):
        if line.startswith("|"):
            line = re.sub(r"\\([\\|])", r"\1", line)
        lines.append(line)
    return "\n".join(lines).replace("**", "").replace("<br>", "\n")


def split_layers(rendered, mode=None):
    """(scan, details): the text above the Details heading and the text below it.

    details is None when the rendering has no Details section (the one-layer modes). With `mode`
    given the answer follows the mode; without it the heading decides.
    """
    if mode is not None and mode not in render.TWO_LAYER:
        return rendered, None
    lines = rendered.split("\n")
    at = next((i for i, line in enumerate(lines) if line.startswith(render.DETAILS_PREFIX)), None)
    if at is None:
        return rendered, None
    return "\n".join(lines[:at]), "\n".join(lines[at:])


def _neg_key(word):
    return " ".join(word.replace("’", "'").lower().split())


def _id_count(rid, text):
    return len(re.findall(r"(?<!\w)%s(?!\w)" % re.escape(rid), text))


def _tally(expected, found):
    exp = sum(expected.values())
    got = sum(min(n, found[tok]) for tok, n in expected.items())
    missing = sorted(tok for tok, n in expected.items() if found[tok] < n)
    return {"expected": exp, "found": got, "missing": missing}


def preservation(rows, rendered, policy=None, mode=None):
    pol = policy_mod.load(policy)
    scan_text, details_text = split_layers(rendered, mode)
    out = normalize(details_text if details_text is not None else rendered)
    expected = {c: collections.Counter() for c in CATEGORIES}
    for row in rows:
        text = normalize_input(row["text"])
        expected["ids"][row["id"]] += 1
        expected["numbers"].update(features.NUMBER_RE.findall(text))
        expected["negations"].update(_neg_key(w) for w in pol.negation_re.findall(text))
        for loc in row.get("locators") or []:
            loc = normalize_input(loc)
            expected["locators"][loc] += 1
            expected["numbers"].update(features.NUMBER_RE.findall(loc))
        if text:
            expected["texts"][text] += 1
    found = {
        "ids": collections.Counter({rid: _id_count(rid, out) for rid in expected["ids"]}),
        "numbers": collections.Counter(features.NUMBER_RE.findall(out)),
        "negations": collections.Counter(_neg_key(w) for w in pol.negation_re.findall(out)),
        "locators": collections.Counter({loc: out.count(loc) for loc in expected["locators"]}),
        "texts": collections.Counter({t: out.count(t) for t in expected["texts"]}),
    }
    checks = {"declared": list(CATEGORIES) + list(SCAN_CHECKS), "ran": [],
              "not_applicable": {}, "not_run": {}}
    result, total_exp, total_found = {"scope_rows": len(rows), "checks": checks}, 0, 0
    for c in CATEGORIES:
        result[c] = _tally(expected[c], found[c])
        total_exp += result[c]["expected"]
        total_found += result[c]["found"]
        checks["ran"].append(c)
    result["expected_total"], result["found_total"] = total_exp, total_found
    if total_exp:
        result["pct"] = round(100.0 * total_found / total_exp, 3)
        result["ok"] = all(not result[c]["missing"] for c in CATEGORIES)
    else:
        # Nothing was expected, so nothing was checked. 100% of zero is not a preservation result:
        # it is indistinguishable from a renderer that dropped every row in scope.
        result["pct"] = None
        result["ok"] = False
        for c in CATEGORIES:
            checks["ran"].remove(c)
            checks["not_run"][c] = ("0 rows in scope: preservation over 0 expected occurrences "
                                    "measures nothing and is not a pass")
    # The scan-layer checks must never simply stop being generated: say which state they are in.
    two_layer = (mode in render.TWO_LAYER) if mode is not None else (details_text is not None)
    if details_text is None:
        why = ("the rendering has no %r heading, so there is no scan layer to check separately"
               % render.DETAILS_PREFIX)
        for name in SCAN_CHECKS:
            if two_layer:
                checks["not_run"][name] = ("mode %r is two-layer but %s" % (mode, why))
            else:
                checks["not_applicable"][name] = why
        if two_layer:
            result["ok"] = False
    else:
        scan = normalize(scan_text)
        ids = collections.Counter({row["id"]: 1 for row in rows})
        result["scan_ids"] = _tally(ids, collections.Counter(
            {rid: len(re.findall(r"`%s`" % re.escape(rid), scan)) for rid in ids}))
        leads = collections.Counter()
        for row in rows:
            # A row with its own scan line renders as `id` followed by its tag; a row named only
            # inside a collapsed group line does not, and shows no lead to check.
            if not re.search(r"`%s` \[" % re.escape(row["id"]), scan):
                continue
            text, _cut = render.lead(normalize_input(row["text"]))
            if text.strip():
                leads[text] += 1
        result["scan_leads"] = _tally(leads, collections.Counter({t: scan.count(t) for t in leads}))
        checks["ran"] += list(SCAN_CHECKS)
        result["ok"] = result["ok"] and all(not result[c]["missing"] for c in SCAN_CHECKS)
    return result


def first_lines(ids, rendered):
    """1-based line of the row's own entry (`id`), or None when the row is absent."""
    lines = rendered.split("\n")
    out = {}
    for rid in ids:
        mark = "`%s`" % rid
        out[rid] = next((n for n, line in enumerate(lines, 1) if mark in line), None)
    return out


def _within(positions, k):
    return {"visible": sum(1 for p in positions.values() if p is not None and p <= k), "of": len(positions)}


def compare(doc, policy=None, manifest=None, modes=render.MODES, ks=KS):
    pol = policy_mod.load(policy)
    doc = features.validate_rows(doc)
    if not doc["rows"]:
        raise ValueError("this rows document holds 0 rows; an empty document is a failure, not a "
                         "result -- there is nothing for preservation to measure")
    plan = features.classify(doc, pol)
    check_ids = list(plan["check_first"])
    planted_ids = [e["row_id"] for e in (manifest or {}).get("entries", [])]
    sample = render.spot_sample(doc, pol, plan)
    planted_established = [rid for rid in planted_ids if plan["features"].get(rid, {}).get("bucket") == "established"]
    result = {"rows_total": len(doc["rows"]), "check_first_ids": check_ids, "planted_ids": planted_ids,
              "spot_check": {"sample": sample, "of_established": len(plan["established"]),
                             "planted_in_sample": [rid for rid in planted_ids if rid in set(sample)],
                             "planted_established": planted_established},
              "modes": {}}
    feats = plan["features"]
    fail_ids = list(plan["rule_fail"])
    by_rule = {}
    for rid in fail_ids:
        for rule in feats[rid]["rule_fails"]:
            by_rule.setdefault(rule, []).append(rid)
    checks = collections.Counter(feats[r["id"]]["rule_check"] for r in doc["rows"])
    result["rule_check"] = {"rules_declared": [r["id"] for r in pol.rules],
                            "fail": checks["fail"], "pass": checks["pass"], "not_checked": checks["not_checked"],
                            "fail_ids": fail_ids,
                            "by_rule": {rule: {"flagged": len(ids), "ids": ids} for rule, ids in by_rule.items()}}
    if manifest is not None:
        by_type = {}
        for planted in manifest.get("entries", []):
            slot = by_type.setdefault(planted["type"], {"planted": 0, "flagged": 0, "ids": []})
            slot["planted"] += 1
            if feats.get(planted["row_id"], {}).get("rule_fails"):
                slot["flagged"] += 1
                slot["ids"].append(planted["row_id"])
        result["planted_rule_flagged"] = {"planted": len(planted_ids),
                                          "flagged": sum(s["flagged"] for s in by_type.values()),
                                          "by_type": by_type}
    for mode in modes:
        text = render.render(doc, mode, pol)
        scan, _details = split_layers(text, mode)
        scope = [r for r in doc["rows"] if mode != "claims" or r["kind"] in render.CLAIM_KINDS]
        pos_check, pos_planted = first_lines(check_ids, scan), first_lines(planted_ids, scan)
        pos_rule = first_lines(fail_ids, scan)
        result["modes"][mode] = {
            "chars": len(text), "lines": len(text.splitlines()),
            "scan_chars": len(scan), "scan_lines": len(scan.splitlines()), "scope_rows": len(scope),
            "preservation": preservation(scope, text, pol, mode),
            "first_line": {"check_first": pos_check, "planted": pos_planted, "rule_fail": pos_rule},
            "visible_within": {str(k): {"check_first": _within(pos_check, k), "planted": _within(pos_planted, k),
                                        "rule_fail": _within(pos_rule, k)}
                               for k in ks},
        }
    result["all_preserved"] = all(m["preservation"]["ok"] for m in result["modes"].values())
    # the count that disagrees with all_preserved: checks that never ran, named, per mode
    result["preservation_not_run"] = {mode: sorted(m["preservation"]["checks"]["not_run"])
                                      for mode, m in result["modes"].items()
                                      if m["preservation"]["checks"]["not_run"]}
    result["modes_with_no_rows_in_scope"] = [mode for mode, m in result["modes"].items()
                                             if m["scope_rows"] == 0]
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compare renderings of one rows.json.")
    ap.add_argument("rows")
    ap.add_argument("--policy")
    ap.add_argument("--manifest")
    args = ap.parse_args(argv)
    getattr(sys.stdout, "reconfigure", lambda **_: None)(encoding="utf-8")
    try:
        doc = json.loads(pathlib.Path(args.rows).read_text(encoding="utf-8"))
        manifest = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8")) if args.manifest else None
        result = compare(doc, args.policy, manifest)
    except (OSError, ValueError) as exc:
        print("metrics NOT EXECUTED: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    for mode, names in sorted(result["preservation_not_run"].items()):
        print("CHECK NOT RUN in mode %s: %s" % (mode, ", ".join(names)), file=sys.stderr)
    if not result["all_preserved"]:
        print("PRESERVATION FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
