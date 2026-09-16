# -*- coding: utf-8 -*-
"""plant.py -- deterministic, seeded error planting over rows, for evaluating renderings.

Given clean ("truth") rows, produce a planted copy and a manifest of every planted error. Mutation
types, each recorded as {row_id, type, field, original, planted, lands_in, detail}:

  status_upgrade  a needs-check status becomes the policy's first established status
  locator_shift   one number inside one locator changes
  negation_drop   one negation word is removed from the text
  verdict_soften  a high-impact verdict becomes a non-high-impact verdict, or answered false -> true
  number_change   one number in the text changes

Targets alternate between rows that will render as established (the hard case: nothing flags them)
and rows that will not (check first, eliminated or superseded); lands_in records where the planted
row renders. A row is never planted twice. The same rows, policy and seed give the same output.
When a type has fewer applicable rows than asked, the shortfall is recorded (and the command exits 1).

The planted copy is unmistakable: its title is prefixed "[SEEDED TEST COPY] ", it carries
"seeded_test_copy": true (every renderer then prints the banner line), and the manifest carries
"banner" and "seeded_test_copy" too. Keep the manifest away from reviewers.

    python plant.py TRUTH_ROWS.json OUT_DIR [--seed 7] [--per-type 2] [--policy P.json] [--types a,b]
    -> OUT_DIR/planted_rows.json, OUT_DIR/manifest.json

Standard library only.
"""
import argparse
import copy
import json
import pathlib
import random
import re
import sys

try:
    from . import features, policy as policy_mod, render
except ImportError:  # flat module
    import features
    import policy as policy_mod
    import render

TYPES = ("status_upgrade", "locator_shift", "negation_drop", "verdict_soften", "number_change")
TITLE_PREFIX = "[SEEDED TEST COPY] "
DIGITS = re.compile(r"\d+")


def _lands(bucket):
    return "needs_check" if bucket == "check_first" else bucket


def _number_runs(text):
    runs = []
    for tok in features.NUMBER_RE.finditer(text):
        for m in DIGITS.finditer(tok.group()):
            runs.append((tok.start() + m.start(), tok.start() + m.end()))
    return runs


def _soft_verdicts(doc, pol):
    """Non-high-impact verdicts: first those seen in the rows that need no check, then policy needs-check ones."""
    def high(v):
        return pol.has("high_impact", "verdict", v)
    values = [r["verdict"] for r in doc["rows"]
              if r["verdict"] and not high(r["verdict"]) and not pol.has("needs_check", "verdict", r["verdict"])]
    values += [v for v in pol.data["needs_check"]["verdict"] if not high(v)]
    values += [r["verdict"] for r in doc["rows"] if r["verdict"] and not high(r["verdict"])]
    return list(dict.fromkeys(values))


def _specs(kind, row, pol, soft):
    """Every concrete place this mutation type could land in one row (values decided later)."""
    if kind == "status_upgrade":
        target = pol.first("established", "status")
        if target and pol.has("needs_check", "status", row["status"]):
            return [{"field": "status", "new": target}]
    elif kind == "verdict_soften":
        out = []
        if pol.has("high_impact", "verdict", row["verdict"]):
            if soft:
                out.append({"field": "verdict", "new": soft[0]})
            if row["answered"] is False:
                out.append({"field": "answered", "new": True})
        return out
    elif kind == "locator_shift":
        return [{"field": "locators", "index": i, "span": span}
                for i, loc in enumerate(row["locators"]) for span in [(m.start(), m.end()) for m in DIGITS.finditer(loc)]]
    elif kind == "negation_drop":
        return [{"field": "text", "span": (m.start(), m.end())} for m in pol.negation_re.finditer(row["text"])]
    elif kind == "number_change":
        return [{"field": "text", "span": span} for span in _number_runs(row["text"])]
    return []


def _bump(old, rng):
    return str(int(old) + rng.randint(1, 8)).zfill(len(old))


def _apply(row, kind, spec, rng):
    field = spec["field"]
    detail = {}
    if field in ("status", "verdict", "answered"):
        original, planted = row[field], spec["new"]
        row[field] = planted
    elif field == "locators":
        i, (s, e) = spec["index"], spec["span"]
        original = row["locators"][i]
        new = _bump(original[s:e], rng)
        planted = original[:s] + new + original[e:]
        row["locators"][i] = planted
        detail = {"index": i, "from": original[s:e], "to": new}
    elif kind == "negation_drop":
        s, e = spec["span"]
        original = row["text"]
        word = original[s:e]
        if s > 0 and original[s - 1] == " ":
            s -= 1
        elif e < len(original) and original[e] == " ":
            e += 1
        planted = original[:s] + original[e:]
        row["text"] = planted
        detail = {"removed": word}
    else:  # number_change
        s, e = spec["span"]
        original = row["text"]
        new = _bump(original[s:e], rng)
        planted = original[:s] + new + original[e:]
        row["text"] = planted
        detail = {"from": original[s:e], "to": new}
    return {"row_id": row["id"], "type": kind, "field": field, "original": original, "planted": planted,
            "detail": detail}


def plant(doc, policy=None, seed=0, per_type=2, types=TYPES):
    """Return (planted_doc, manifest). doc is not modified."""
    pol = policy_mod.load(policy)
    truth = features.validate_rows(doc)
    if truth["seeded_test_copy"]:
        raise ValueError("these rows are already a seeded test copy; plant from the clean rows")
    if isinstance(per_type, bool) or not isinstance(per_type, int) or per_type < 1:
        raise ValueError("per_type must be an integer >= 1")
    unknown = [t for t in types if t not in TYPES]
    if unknown:
        raise ValueError("unknown mutation type(s): %s; types: %s" % (", ".join(unknown), ", ".join(TYPES)))
    rng = random.Random(seed)
    work = copy.deepcopy(truth)
    rows, question = work["rows"], work["question"]
    soft = _soft_verdicts(truth, pol)
    used, entries, shortfall = set(), [], []
    for kind in types:
        for k in range(per_type):
            options = []
            for i, row in enumerate(rows):
                if row["id"] in used:
                    continue
                for spec in _specs(kind, row, pol, soft):
                    preview = copy.deepcopy(row)
                    if spec["field"] in ("status", "verdict", "answered"):
                        preview[spec["field"]] = spec["new"]
                    lands = _lands(features.row_features(preview, pol, question)["bucket"])
                    options.append((i, spec, lands))
            if not options:
                shortfall.append({"type": kind, "wanted": per_type, "planted": k})
                break
            n_est = sum(e["lands_in"] == "established" for e in entries)
            want_est = n_est <= len(entries) - n_est
            pool = [o for o in options if (o[2] == "established") == want_est] or options
            i = rng.choice(sorted({o[0] for o in pool}))
            _, spec, _ = rng.choice([o for o in pool if o[0] == i])
            entry = _apply(rows[i], kind, spec, rng)
            entry["lands_in"] = _lands(features.row_features(rows[i], pol, question)["bucket"])
            used.add(rows[i]["id"])
            entries.append(entry)
    work["title"] = TITLE_PREFIX + work["title"]
    work["seeded_test_copy"] = True
    counts = {}
    for e in entries:
        counts[e["lands_in"]] = counts.get(e["lands_in"], 0) + 1
    manifest = {"banner": render.BANNER, "seeded_test_copy": True, "seed": seed, "per_type": per_type,
                "types": list(types), "rows_total": len(rows), "planted_total": len(entries),
                "lands_in_counts": counts, "entries": entries, "shortfall": shortfall}
    return work, manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="Plant seeded errors into a copy of rows.json.")
    ap.add_argument("rows")
    ap.add_argument("out_dir")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--per-type", type=int, default=2)
    ap.add_argument("--policy")
    ap.add_argument("--types", default=",".join(TYPES))
    args = ap.parse_args(argv)
    try:
        doc = json.loads(pathlib.Path(args.rows).read_text(encoding="utf-8"))
        planted, manifest = plant(doc, args.policy, args.seed, args.per_type,
                                  [t.strip() for t in args.types.split(",") if t.strip()])
    except (OSError, ValueError) as exc:
        print("plant NOT EXECUTED: %s" % exc, file=sys.stderr)
        return 2
    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "planted_rows.json").write_text(json.dumps(planted, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print("planted %d error(s) in %d rows (seed %d): %s" % (manifest["planted_total"], manifest["rows_total"],
                                                          args.seed, manifest["lands_in_counts"]))
    print("wrote %s and %s" % (out / "planted_rows.json", out / "manifest.json"))
    if manifest["shortfall"]:
        print("SHORTFALL: %s" % manifest["shortfall"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
