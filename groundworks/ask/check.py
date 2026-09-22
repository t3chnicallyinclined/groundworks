# -*- coding: utf-8 -*-
"""check.py -- `ask check`: audit a reader's read.jsonl and answer.json against the packet.

    report = check_packet(out_dir, inst=None)      # report["verdict"] in PASS, FAIL, NOT_EXECUTED

Reader files (written by the agent, never by this module):
    read.jsonl    one row per unit read, {"unit": id, "slice": slice id}; a unit read is a unit whose full
                  text the reader took in. Coverage is proven from this file, so it is append-as-you-go.
    answer.json   {"answer": str, "quotes": [{"unit": id, "quote": str, "use": "support" | "note",
                   "why": str}], "gaps": [str], "not_answerable": bool}
                  `support` carries the answer and must come from a usable unit; `note` records that a
                  unit exists and says something, without the answer resting on it (the status axis).

Verdicts:
    NOT_EXECUTED  packet unreadable; read.jsonl missing or empty while the packet had candidates;
                  answer.json missing or not an object. Nothing was checked; exit 3. Never a pass.
    FAIL          at least one error below; exit 1.
    PASS          every check ran and none failed; exit 0. The report still carries not_read, which
                  is corpus_total - read and is normally > 0.

Errors (each names the unit):
    slice S: unit U was not read                      coverage: every unit in every slice, exactly as cut
    read.jsonl names a unit not in this packet        a read the packet cannot account for
    read.jsonl reads unit U twice                     a coverage count that would over-report
    quote N cites unit U outside this packet
    quote N cites unit U that was not read            the answer may not rest on text nobody took in
    quote N has fewer than M words                    labels.quote_min_words from the instance, else 3
    quote N not found word for word in unit U         whitespace-normalised, exact otherwise
    quote N supports from unit U whose status S is not usable      only with an instance
    quote N use must be support or note

With no instance the status axis is NOT checked and the report says so (status_axis: "not checked").
Standard library only.
"""
import json
import pathlib

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks.ask.packet import PacketError, load_packet  # noqa: E402
from groundworks.pipeline.intake import read_units  # noqa: E402
from groundworks.pipeline.validator import QUOTE_MIN_WORDS, _norm, quote_rules  # noqa: E402

USES = ("support", "note")
EXIT = {"PASS": 0, "FAIL": 1, "NOT_EXECUTED": 3}


def _not_executed(out_dir, reason, info=None):
    report = {"verdict": "NOT_EXECUTED", "reason": reason, "corpus_total": (info or {}).get("corpus_total"),
              "retrieved": (info or {}).get("retrieved"), "read": 0, "not_read": None, "quotes": 0,
              "quotes_checked": 0, "errors": []}
    _write(out_dir, report)
    return report


def _write(out_dir, report):
    pathlib.Path(out_dir, "check.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")


def check_packet(out_dir, inst=None):
    out_dir = pathlib.Path(out_dir)
    try:
        info, candidates = load_packet(out_dir)
    except PacketError as exc:
        return _not_executed(out_dir, str(exc))

    read_path = out_dir / "read.jsonl"
    reads = read_units(read_path) if read_path.exists() else []
    if info["retrieved"] and not reads:
        return _not_executed(out_dir, "read.jsonl is missing or empty: nothing was read, so nothing can be checked", info)

    ans_path = out_dir / "answer.json"
    if not ans_path.exists():
        return _not_executed(out_dir, "answer.json is missing", info)
    try:
        answer = json.loads(ans_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return _not_executed(out_dir, "answer.json is not JSON: %s" % exc, info)
    if not isinstance(answer, dict):
        return _not_executed(out_dir, "answer.json is not an object", info)

    errors = []
    # -- coverage: every read names a packet unit once; every slice unit was read
    read_ids = {}
    for row in reads:
        uid = row.get("unit") if isinstance(row, dict) else None
        if uid not in candidates:
            errors.append({"unit": uid, "error": "read.jsonl names a unit not in this packet"})
            continue
        read_ids[uid] = read_ids.get(uid, 0) + 1
        if read_ids[uid] == 2:
            errors.append({"unit": uid, "error": "read.jsonl reads unit %s twice" % uid})
    for sl in info.get("slices") or []:
        for uid in sl["units"]:
            if uid not in read_ids:
                errors.append({"unit": uid, "slice": sl["id"], "error": "slice %s: unit %s was not read" % (sl["id"], uid)})

    # -- quotes
    rules = quote_rules(inst) if inst is not None else {"quote_min_words": QUOTE_MIN_WORDS}
    min_words = rules["quote_min_words"]
    usable = set(inst.usable_statuses()) if inst is not None else None
    confirming = set(inst.confirming_grades()) if inst is not None else None
    quotes = answer.get("quotes") or []
    checked = 0
    quote_rows = []
    for n, q in enumerate(quotes, 1):
        uid = q.get("unit") if isinstance(q, dict) else None
        use = (q.get("use") if isinstance(q, dict) else None) or "support"
        text = _norm(q.get("quote") if isinstance(q, dict) else None)
        row = {"n": n, "unit": uid, "use": use, "found": False, "usable": None, "confirming": None}
        quote_rows.append(row)
        if use not in USES:
            errors.append({"unit": uid, "quote": n, "error": "quote %d use must be support or note, not %r" % (n, use)})
        if uid not in candidates:
            errors.append({"unit": uid, "quote": n, "error": "quote %d cites unit %s outside this packet" % (n, uid)})
            continue
        if uid not in read_ids:
            errors.append({"unit": uid, "quote": n, "error": "quote %d cites unit %s that was not read" % (n, uid)})
        if len(text.split()) < min_words:
            errors.append({"unit": uid, "quote": n, "error": "quote %d has fewer than %d words" % (n, min_words)})
            continue
        checked += 1
        unit = candidates[uid]
        if text not in _norm(unit.get("text")):
            errors.append({"unit": uid, "quote": n, "error": "quote %d not found word for word in unit %s" % (n, uid)})
        else:
            row["found"] = True
        if usable is not None:
            row["usable"] = unit.get("status") in usable
            row["confirming"] = unit.get("grade") in confirming
            if use == "support" and not row["usable"]:
                errors.append({"unit": uid, "quote": n, "error": "quote %d supports from unit %s whose status %r is not usable"
                               % (n, uid, unit.get("status"))})

    report = {
        "verdict": "FAIL" if errors else "PASS",
        "question": info["question"],
        "corpus_total": info["corpus_total"],
        "scope": info.get("scope") or {"types": [], "where": {}, "in_scope": info["corpus_total"], "out_of_scope": 0},
        "retrieved": info["retrieved"],
        "read": len(read_ids),
        "not_read": info["corpus_total"] - len(read_ids),
        "quotes": len(quotes),
        "quotes_checked": checked,
        "quotes_found": sum(1 for r in quote_rows if r["found"]),
        "status_axis": "checked" if usable is not None else "not checked (no instance given)",
        "quote_min_words": min_words,
        "quote_rows": quote_rows,
        "errors": errors,
    }
    _write(out_dir, report)
    return report
