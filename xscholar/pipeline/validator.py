# -*- coding: utf-8 -*-
"""validator.py -- slice + labels -> Report. The anti-hallucination check.

Rules (docs/MODULES.md labels contract):
  coverage    every unit in the slice labelled exactly once; no labels for units not in the slice
  vocabulary  tier, dir, issues, targets, role all from the instance's allowed sets
  tier rules  tier >= 2 requires reason, dir, at least one issue and a quote
  verbatim    the quote appears word for word in that unit's text (whitespace collapsed)

    report = validate(slice_path, labels_path, instance)
    python validator.py SLICE.jsonl LABELS.jsonl INSTANCE.yaml     # exit 1 when not ok
Standard library only.
"""
import json
import pathlib
import re
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.pipeline.intake import read_units

WS = re.compile(r"\s+")


def _norm(text):
    return WS.sub(" ", (text or "")).strip()


def validate(slice_path, labels_path, inst):
    units = {u["id"]: u for u in read_units(slice_path)}
    labels = read_units(labels_path)
    vocab = inst.allowed_vocabulary()
    errors = []
    seen = {}
    for label in labels:
        uid = label.get("id")
        if uid not in units:
            errors.append({"unit": uid, "field": "id", "error": "label for a unit not in this slice"})
            continue
        seen[uid] = seen.get(uid, 0) + 1
        if seen[uid] > 1:
            errors.append({"unit": uid, "field": "id", "error": "labelled more than once"})
            continue
        tier = label.get("tier")
        if tier not in vocab["tiers"]:
            errors.append({"unit": uid, "field": "tier", "error": "tier %r not in %s" % (tier, vocab["tiers"])})
            continue
        if label.get("dir") is not None and label["dir"] not in vocab["directions"]:
            errors.append({"unit": uid, "field": "dir", "error": "direction %r not allowed" % label["dir"]})
        for issue in label.get("issues") or []:
            if issue not in vocab["issues"]:
                errors.append({"unit": uid, "field": "issues", "error": "issue %r not in vocabulary" % issue})
        for target in label.get("targets") or []:
            if target not in vocab.get("targets", []):
                errors.append({"unit": uid, "field": "targets", "error": "target %r not in vocabulary" % target})
        if label.get("role") is not None and label["role"] not in vocab["roles"]:
            errors.append({"unit": uid, "field": "role", "error": "role %r not allowed" % label["role"]})
        if tier >= 2:
            if not _norm(label.get("reason")):
                errors.append({"unit": uid, "field": "reason", "error": "tier %d needs a reason" % tier})
            if not label.get("dir"):
                errors.append({"unit": uid, "field": "dir", "error": "tier %d needs a direction" % tier})
            if not label.get("issues"):
                errors.append({"unit": uid, "field": "issues", "error": "tier %d needs at least one issue" % tier})
            quote = _norm(label.get("quote"))
            if not quote:
                errors.append({"unit": uid, "field": "quote", "error": "tier %d needs a verbatim quote" % tier})
            elif quote not in _norm(units[uid]["text"]):
                errors.append({"unit": uid, "field": "quote", "error": "quote not found word for word in the unit"})
    for uid in units:
        if uid not in seen:
            errors.append({"unit": uid, "field": "id", "error": "unit has no label"})
    return {"ok": not errors, "units": len(units), "labels": len(labels), "errors": errors}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print("usage: validator.py SLICE.jsonl LABELS.jsonl INSTANCE.yaml")
        return 2
    from xscholar import config
    report = validate(argv[0], argv[1], config.load(argv[2]))
    for err in report["errors"]:
        print("FAIL %-24s %-8s %s" % (err["unit"], err["field"], err["error"]))
    print("%s: %d units, %d labels, %d error(s)" % ("PASS" if report["ok"] else "FAIL",
                                                     report["units"], report["labels"], len(report["errors"])))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
