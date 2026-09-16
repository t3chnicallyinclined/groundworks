# -*- coding: utf-8 -*-
"""score.py -- score a reviewer's findings against a planting manifest.

Findings are a JSON list (or {"findings": [...]}) of {row_id, field, note}; field may be omitted.
A finding catches a planted error when row_id matches and, if field is given, the field matches
(locator/locators, answer/answered and a trailing [index] are treated as the same field). Any other
finding is a false alarm; a second finding on an error already caught is a duplicate, not a false
alarm. Recall and precision are reported with their counts, split by mutation type and by where the
planted row rendered (established vs needs_check vs eliminated vs superseded).

False alarms are measured against the planted errors only: a false alarm may be a real problem in
the clean rows. Read them.

    python score.py MANIFEST.json FINDINGS.json [--log LOOKUP_LOG.jsonl]

Exit 0 scored; 2 NOT EXECUTED (unreadable or malformed input).

Standard library only.
"""
import argparse
import json
import pathlib
import re
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.present import lookup as lookup_mod

SYNONYMS = {"locator": "locators", "loc": "locators", "where": "locators", "answer": "answered"}


def canonical_field(field):
    f = re.sub(r"\[.*?\]$", "", str(field)).strip().lower()
    return SYNONYMS.get(f, f)


def _validate_findings(findings):
    if isinstance(findings, dict) and set(findings) == {"findings"}:
        findings = findings["findings"]
    if not isinstance(findings, list):
        raise ValueError("findings must be a list of {row_id, field, note}")
    for i, f in enumerate(findings):
        if not isinstance(f, dict) or not isinstance(f.get("row_id"), str) or not f["row_id"]:
            raise ValueError("findings[%d] needs a non-empty string row_id" % i)
        if f.get("field") is not None and not isinstance(f["field"], str):
            raise ValueError("findings[%d].field must be a string or null" % i)
    return findings


def _ratio(num, den):
    return round(num / den, 4) if den else None


def score(manifest, findings, log_entries=()):
    if not isinstance(manifest, dict) or not isinstance(manifest.get("entries"), list):
        raise ValueError("manifest must be an object with an entries list")
    entries = manifest["entries"]
    findings = _validate_findings(findings)
    caught = {}
    false_alarms, duplicates, wrong_field = [], 0, 0
    for fi, finding in enumerate(findings):
        on_row = [i for i, e in enumerate(entries) if e["row_id"] == finding["row_id"]]
        if not on_row:
            false_alarms.append(finding)
            continue
        field = finding.get("field")
        match = [i for i in on_row if not field or canonical_field(field) == canonical_field(entries[i]["field"])]
        if not match:
            false_alarms.append(finding)
            wrong_field += 1
            continue
        new = [i for i in match if i not in caught]
        if new:
            caught[new[0]] = fi
        else:
            duplicates += 1

    def split(key):
        out = {}
        for i, e in enumerate(entries):
            slot = out.setdefault(e[key], {"planted": 0, "caught": 0, "missed": 0})
            slot["planted"] += 1
            slot["caught" if i in caught else "missed"] += 1
        for slot in out.values():
            slot["recall"] = _ratio(slot["caught"], slot["planted"])
        return out

    log_entries = list(log_entries)
    return {
        "planted": len(entries), "caught": len(caught), "missed": len(entries) - len(caught),
        "false_alarms": len(false_alarms), "right_row_wrong_field": wrong_field, "duplicates": duplicates,
        "findings": len(findings),
        "recall": _ratio(len(caught), len(entries)),
        "precision": _ratio(len(caught), len(caught) + len(false_alarms)),
        "lookups_used": sum(1 for e in log_entries if e.get("result") == "ok"),
        "lookups_refused": sum(1 for e in log_entries if e.get("result") == "refused"),
        "lookups_not_found": sum(1 for e in log_entries if e.get("result") == "not_found"),
        "by_type": split("type"), "by_lands_in": split("lands_in"),
        "caught_ids": [entries[i]["row_id"] for i in sorted(caught)],
        "missed_ids": [e["row_id"] for i, e in enumerate(entries) if i not in caught],
        "false_alarm_findings": false_alarms,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score reviewer findings against a planting manifest.")
    ap.add_argument("manifest")
    ap.add_argument("findings")
    ap.add_argument("--log")
    args = ap.parse_args(argv)
    getattr(sys.stdout, "reconfigure", lambda **_: None)(encoding="utf-8")
    try:
        manifest = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8"))
        findings = json.loads(pathlib.Path(args.findings).read_text(encoding="utf-8"))
        log = lookup_mod.read_log(args.log) if args.log else []
        result = score(manifest, findings, log)
    except (OSError, ValueError, KeyError) as exc:
        print("score NOT EXECUTED: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
