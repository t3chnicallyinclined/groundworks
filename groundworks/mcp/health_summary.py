# -*- coding: utf-8 -*-
"""health_summary.py -- the numbers that matter from a health result, before its detail.

Why (found in real use, 2026-09-16). `health` returned about 60 KB of JSON on a real instance, mostly whole lists
of critique heads, and the one number the pre-write check needed was buried in it. A summary helps only if it can
never hide what the detail says, so this is derived mechanically from whatever the adapter returned -- no adapter
cooperation, no list of known keys:

  not_executed  every key named NOT_EXECUTED and every string saying NOT_EXECUTED / NOT EXECUTED, by path,
                anywhere in the detail (inside lists too)
  failing       every `ok` / `passed` / `verified` / `clean` / `valid` that is false, by path, anywhere
  drift         every number whose path says drift, zero or not (so a zero is visibly a checked zero)
  nonzero       every other non-zero number outside a list, by path (flattering and unflattering side by side)
  lists         every non-empty list: its length and up to five names
  zero_counts   how many numbers were 0 (named in the detail)
  attention     one line per thing a reader must look at: the engine code verdict when it is not VERIFIED,
                each NOT_EXECUTED, each failing flag, each non-zero drift number

`headline` is one sentence built from `attention`. A summary that reports "nothing needs attention" while the
detail holds a NOT_EXECUTED, a false `ok` or a non-zero drift is a bug; tests/test_mcp_guard.py plants each at
random depths to prove it cannot happen. Standard library only.
"""
import json
import re

NOT_EXECUTED = re.compile(r"NOT[_ ]EXECUTED")
FLAG_KEYS = ("ok", "passed", "verified", "clean", "valid")
NAME_KEYS = ("name", "id", "head", "rule", "row", "unit", "critique", "target", "path")
NAMES = 5
REASON_CHARS = 200


def _join(path, key):
    return "%s.%s" % (path, key) if path else str(key)


def _name(item):
    if isinstance(item, str):
        return item[:80]
    if isinstance(item, dict):
        for key in NAME_KEYS:
            if isinstance(item.get(key), (str, int)) and not isinstance(item.get(key), bool):
                return str(item[key])[:80]
    return json.dumps(item, default=str)[:80]


def summarize(detail, engine_code=None):
    out = {"not_executed": [], "failing": [], "drift": {}, "nonzero": {}, "lists": {}, "zero_counts": 0}

    def walk(obj, path, in_list):
        if isinstance(obj, dict):
            for key, value in obj.items():
                sub = _join(path, key)
                if NOT_EXECUTED.search(str(key)):
                    out["not_executed"].append({"path": sub, "reason": str(value)[:REASON_CHARS]})
                    if isinstance(value, str):
                        continue          # its reason is already recorded; do not count it twice
                if isinstance(value, bool) and value is False and str(key).lower() in FLAG_KEYS:
                    out["failing"].append({"path": sub, "value": False})
                walk(value, sub, in_list)
        elif isinstance(obj, (list, tuple)):
            if obj and not in_list:
                out["lists"][path or "(root)"] = {"count": len(obj), "names": [_name(i) for i in obj[:NAMES]]}
            for i, item in enumerate(obj):
                walk(item, "%s[%d]" % (path, i), True)
        elif isinstance(obj, str):
            if NOT_EXECUTED.search(obj):
                out["not_executed"].append({"path": path, "reason": obj[:REASON_CHARS]})
        elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
            if "drift" in path.lower():
                if obj or not in_list:        # a non-zero drift number is surfaced wherever it sits
                    out["drift"][path] = obj
            elif obj == 0:
                out["zero_counts"] += 1
            elif not in_list:
                out["nonzero"][path] = obj

    walk(detail, "", False)
    attention = []
    if engine_code is not None and engine_code.get("verdict") != "VERIFIED":
        attention.append("engine code %s: writes %s" % (engine_code.get("verdict"), engine_code.get("writes")))
    if engine_code is not None and engine_code.get("override"):
        attention.append("override set: %s" % engine_code.get("WARNING"))
    attention += ["NOT_EXECUTED at %s: %s" % (n["path"], n["reason"]) for n in out["not_executed"]]
    attention += ["failing: %s is false" % f["path"] for f in out["failing"]]
    attention += ["drift: %s = %s" % (p, v) for p, v in out["drift"].items() if v]
    if attention:
        headline = "ATTENTION (%d): %s" % (len(attention), attention[0])
    else:
        headline = ("nothing needs attention: %sno check NOT_EXECUTED, no failing flag, every drift count 0 "
                    "(%d drift count(s), %d non-zero count(s) below)"
                    % ("engine code verified, " if engine_code is not None else "", len(out["drift"]),
                       len(out["nonzero"])))
    return dict({"headline": headline, "attention": attention}, **out)
