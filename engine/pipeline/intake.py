# -*- coding: utf-8 -*-
"""intake.py -- an instance's adapter -> units.jsonl, contract-checked, exclusions logged.

An adapter is any object with `units()` yielding dicts in the units contract (docs/MODULES.md):
  id, type, locator, text, status, grade, [meta], [hash]
A unit may instead carry `exclude: "<reason>"` to be logged, not ingested (duplicates, work product).

    counts = write_units(adapter, out_dir, instance)

No interpretation happens here. Nothing is filtered for relevance. Loud on any contract breach.
Standard library only.
"""
import hashlib
import json
import pathlib

ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-:.")


class IntakeError(Exception):
    pass


def unit_hash(unit):
    payload = json.dumps([unit["type"], unit["locator"], unit["text"]], sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def check_unit(unit, inst, seen):
    """Raise IntakeError naming the id and field for the first contract breach."""
    uid = unit.get("id")
    if not uid or not isinstance(uid, str) or set(uid) - ID_CHARS:
        raise IntakeError("unit %r: id missing or has characters outside [A-Za-z0-9_-:.]" % (uid,))
    if uid in seen:
        raise IntakeError("unit %s: duplicate id" % uid)
    if unit.get("type") not in inst.unit_names():
        raise IntakeError("unit %s: type %r not in instance unit types %s" % (uid, unit.get("type"), inst.unit_names()))
    locator = unit.get("locator")
    if not isinstance(locator, dict):
        raise IntakeError("unit %s: locator must be an object" % uid)
    required = inst.unit_type(unit["type"])["locator_fields"]
    missing = [f for f in required if f not in locator or locator[f] in (None, "")]
    if missing:
        raise IntakeError("unit %s: locator missing %s (required for %s)" % (uid, ", ".join(missing), unit["type"]))
    if not isinstance(unit.get("text"), str) or not unit["text"].strip():
        raise IntakeError("unit %s: text must be a non-empty string" % uid)
    if unit.get("status") not in inst.status_values():
        raise IntakeError("unit %s: status %r not in %s" % (uid, unit.get("status"), inst.status_values()))
    if unit.get("grade") not in inst.grades():
        raise IntakeError("unit %s: grade %r not in fidelity ladder %s" % (uid, unit.get("grade"), inst.grades()))
    if "meta" in unit and unit["meta"] is not None and not isinstance(unit["meta"], dict):
        raise IntakeError("unit %s: meta must be an object" % uid)


def write_units(adapter, out_dir, inst):
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {"kept": 0, "excluded": 0, "by_type": {}, "by_status": {}}
    seen = set()
    with (out_dir / "units.jsonl").open("w", encoding="utf-8") as units_file, \
         (out_dir / "excluded.jsonl").open("w", encoding="utf-8") as excluded_file:
        for raw in adapter.units():
            if not isinstance(raw, dict):
                raise IntakeError("adapter yielded a non-object unit: %r" % (raw,))
            if raw.get("exclude"):
                excluded_file.write(json.dumps({"id": raw.get("id"), "type": raw.get("type"),
                                                "reason": raw["exclude"]}, ensure_ascii=False) + "\n")
                counts["excluded"] += 1
                continue
            check_unit(raw, inst, seen)
            unit = {k: raw[k] for k in ("id", "type", "locator", "text", "status", "grade")}
            unit["meta"] = raw.get("meta") or {}
            unit["hash"] = raw.get("hash") or unit_hash(unit)
            seen.add(unit["id"])
            units_file.write(json.dumps(unit, ensure_ascii=False) + "\n")
            counts["kept"] += 1
            counts["by_type"][unit["type"]] = counts["by_type"].get(unit["type"], 0) + 1
            counts["by_status"][unit["status"]] = counts["by_status"].get(unit["status"], 0) + 1
    if counts["kept"] == 0:
        raise IntakeError("intake produced 0 units; an empty corpus is a failure, not a result")
    return counts


def read_units(path):
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
