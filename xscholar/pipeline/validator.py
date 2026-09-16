# -*- coding: utf-8 -*-
"""validator.py -- slice + labels -> Report. The anti-hallucination check.

Rules (docs/MODULES.md labels contract):
  coverage    every unit in the slice labelled exactly once; no labels for units not in the slice
  vocabulary  tier, dir, issues, targets, role all from the instance's allowed sets
  tier rules  tier >= 2 requires reason, dir, at least one issue and a quote
  verbatim    the quote appears word for word in that unit's text (whitespace collapsed)
  original    and, where the unit links to an original and the instance declares how its locator cuts a
              span out of that original (`originals.spans`), word for word in the original at that span:
              the extraction is checked against what it claims to have been extracted from. The original
              is read through the store, so a missing or corrupt original is an error naming it. A unit
              whose original is binary, or whose type declares no span, is counted as not checkable --
              never as checked.

Every rule above is a loop over the slice, so a slice of 0 units satisfies all of them without
running any. An empty slice is therefore reported as an error, not a PASS (the same call intake.py
makes about a corpus of 0 units), and the PASS/FAIL line carries the unit count next to it.

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


class _Originals:
    """Quote checks against originals, with every unit that could not be checked counted by reason."""

    def __init__(self, inst, store):
        from xscholar import originals
        self.originals, self.inst, self.spans = originals, inst, originals.spans(inst)
        self._store, self._cache = store, {}
        self.checked, self.not_checkable = 0, {}

    def _skip(self, reason):
        self.not_checkable[reason] = self.not_checkable.get(reason, 0) + 1

    def store(self):
        if self._store is None:
            from xscholar import store as storemod
            self._store = storemod.for_instance(self.inst)
        return self._store

    def check(self, unit, quote):
        """None when the quote is found in the original's span or the unit is not checkable; else the error."""
        from xscholar import store as storemod
        if not unit.get("document"):
            return self._skip("no original")
        spec = self.spans.get(unit.get("type"))
        if not spec:
            return self._skip("no span declared for unit type %s" % unit.get("type"))
        sha = unit["document"]
        if not storemod.HEX.match(str(sha)):
            return self._skip("original never captured")
        try:
            if sha not in self._cache:
                self._cache[sha] = self.store().get(sha)
            data = self._cache[sha]
        except storemod.StoreError as exc:
            return "cannot check the quote against the original: %s" % exc
        if not self.originals.is_text({"media_type": "text/plain"}, data):
            return self._skip("binary original")
        try:
            span = self.originals.span_text(data, unit.get("locator"), spec)
        except ValueError as exc:
            return "the locator does not point into the original: %s" % exc
        self.checked += 1
        if quote not in _norm(span):
            return "quote not found word for word in the original %s at %s" % (sha[:12], json.dumps(unit.get("locator")))
        return None


def validate(slice_path, labels_path, inst, store=None):
    units = {u["id"]: u for u in read_units(slice_path)}
    labels = read_units(labels_path)
    vocab = inst.allowed_vocabulary()
    errors = []
    seen = {}
    against = _Originals(inst, store)
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
            else:
                problem = against.check(units[uid], quote)
                if problem:
                    errors.append({"unit": uid, "field": "original", "error": problem})
    for uid in units:
        if uid not in seen:
            errors.append({"unit": uid, "field": "id", "error": "unit has no label"})
    if not units:
        # intake.py already refuses a corpus of 0 units for the same reason: every check above is
        # a loop over `units` or `labels`, so an empty slice passes all of them without running any.
        errors.append({"unit": None, "field": "slice",
                       "error": "this slice holds 0 units; an empty slice is a failure, not a result "
                                "-- every check below is a loop that ran zero times"})
    return {"ok": not errors, "units": len(units), "labels": len(labels), "errors": errors,
            "checks_run": 0 if not units else len(units),
            "original_checked": against.checked, "original_not_checkable": dict(against.not_checkable)}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print("usage: validator.py SLICE.jsonl LABELS.jsonl INSTANCE.yaml")
        return 2
    from xscholar import config
    report = validate(argv[0], argv[1], config.load(argv[2]))
    for err in report["errors"]:
        print("FAIL %-24s %-8s %s" % (err["unit"], err["field"], err["error"]))
    print("%s: %d units, %d labels, %d error(s); %d quote(s) checked against originals, not checkable: %s"
          % ("PASS" if report["ok"] else "FAIL", report["units"], report["labels"], len(report["errors"]),
             report["original_checked"], report["original_not_checkable"] or "none"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
