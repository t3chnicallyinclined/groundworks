# -*- coding: utf-8 -*-
"""intake.py -- an instance's adapter -> originals in the store, documents.jsonl, units.jsonl; contract-checked,
exclusions logged.

An adapter is any object with `units()` yielding dicts in the units contract (docs/MODULES.md):
  id, type, locator, text, status, grade, [meta], [hash], [document]
A unit may instead carry `exclude: "<reason>"` to be logged, not ingested (duplicates, work product).

Originals. An adapter supplies the documents its units were extracted from with `documents()`, yielding
  key            the adapter's own name for it, which units use in `document`
  bytes | path   the exact bytes, or a file to stream them from (never both)
  media_type     e.g. text/plain, application/pdf, image/png
  capture        raw (the bytes as obtained) | rendered_text (a text rendering captured at retrieval) |
                 none (no bytes could be captured: recorded as a named gap, never as an original)
  [source_uri] [retrieved_at] [original_name]
Each unit then names its original in `document` (an adapter key, or `sha256:<hex>`), and its locator points
INTO that original. Bytes go to the instance's content-addressed store (xscholar/store.py): the same bytes
under two keys are stored once and listed once in documents.jsonl, with both keys.

`originals:` in instance.yaml decides what a unit without an original is:
  required    an IntakeError naming the unit
  transition  kept, counted, and named in without_original.jsonl (also when the section is absent)
A unit whose original has no extractable text (a scan, an image) may carry empty text; a unit whose original
is text, or that has no original, may not.

    counts = write_units(adapter, out_dir, instance, store=None)   # store: default the instance's own

No interpretation happens here. Nothing is filtered for relevance. Loud on any contract breach.
Standard library only.
"""
import hashlib
import json
import pathlib

ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-:.")
DOCUMENT_KEYS = {"key", "bytes", "path", "media_type", "capture", "source_uri", "retrieved_at", "original_name"}


class IntakeError(Exception):
    pass


def unit_hash(unit):
    """What the extraction says, and -- when the unit names one -- which original it says it came from. A
    unit with no original hashes exactly as before, so existing hashes do not move."""
    parts = [unit["type"], unit["locator"], unit["text"]]
    if unit.get("document"):
        parts.append(unit["document"])
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def check_unit(unit, inst, seen, documents=None):
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
    doc = (documents or {}).get(unit.get("document")) if unit.get("document") else None
    if not isinstance(unit.get("text"), str):
        raise IntakeError("unit %s: text must be a string" % uid)
    if not unit["text"].strip() and not (doc and doc["capture"] != "none" and not _texty(doc["media_type"])):
        raise IntakeError("unit %s: text must be a non-empty string (empty text is allowed only for a unit whose "
                          "original has no extractable text, such as a scan or an image)" % uid)
    if unit.get("status") not in inst.status_values():
        raise IntakeError("unit %s: status %r not in %s" % (uid, unit.get("status"), inst.status_values()))
    if unit.get("grade") not in inst.grades():
        raise IntakeError("unit %s: grade %r not in fidelity ladder %s" % (uid, unit.get("grade"), inst.grades()))
    if "meta" in unit and unit["meta"] is not None and not isinstance(unit["meta"], dict):
        raise IntakeError("unit %s: meta must be an object" % uid)


def _texty(media_type):
    from xscholar import originals
    return originals.is_text({"media_type": media_type}, b"")


def _store_documents(adapter, inst, store, counts):
    """key -> manifest row, and the manifest rows by document id, in first-seen order."""
    from xscholar import originals, store as storemod
    by_key, rows = {}, {}
    supplier = getattr(adapter, "documents", None)
    if not callable(supplier):
        return by_key, rows
    for raw in supplier():
        if not isinstance(raw, dict):
            raise IntakeError("adapter yielded a non-object document: %r" % (raw,))
        key = raw.get("key")
        where = "document %r" % (key,)
        if not isinstance(key, str) or not key.strip():
            raise IntakeError("a document needs a non-empty string key, which its units name in `document`")
        unknown = sorted(set(raw) - DOCUMENT_KEYS)
        if unknown:
            raise IntakeError("%s: unknown field(s) %s; allowed: %s" % (where, ", ".join(unknown), ", ".join(sorted(DOCUMENT_KEYS))))
        if key in by_key:
            raise IntakeError("%s: duplicate key" % where)
        if not isinstance(raw.get("media_type"), str) or "/" not in raw["media_type"]:
            raise IntakeError("%s: media_type must be a type/subtype string such as text/plain or application/pdf" % where)
        has_bytes, has_path = raw.get("bytes") is not None, raw.get("path") is not None
        capture = raw.get("capture") or ("raw" if has_bytes or has_path else None)
        if capture not in originals.CAPTURES:
            raise IntakeError("%s: capture must be one of %s (got %r)" % (where, ", ".join(originals.CAPTURES), capture))
        if capture == "none":
            if has_bytes or has_path:
                raise IntakeError("%s: capture none means no bytes were captured, but bytes or a path were given" % where)
            ident = originals.none_key(raw.get("source_uri") or raw.get("original_name") or key)
            sha, size = None, None
        else:
            if has_bytes == has_path:
                raise IntakeError("%s: give exactly one of bytes or path for a captured original" % where)
            if store is None:
                raise IntakeError("%s: no originals store to put it in" % where)
            try:
                if has_bytes:
                    sha = store.put(raw["bytes"])
                    size = len(raw["bytes"])
                else:
                    sha = store.put_file(raw["path"])
                    size = pathlib.Path(raw["path"]).stat().st_size
            except storemod.StoreError as exc:
                raise IntakeError("%s: %s" % (where, exc)) from exc
            ident = sha
        if ident in rows:
            counts["documents_deduplicated"] += 1
            rows[ident]["keys"].append(key)
        else:
            rows[ident] = {"id": ident, "sha256": sha, "bytes": size, "media_type": raw["media_type"],
                           "capture": capture, "source_uri": raw.get("source_uri"),
                           "retrieved_at": raw.get("retrieved_at"), "original_name": raw.get("original_name") or key,
                           "keys": [key]}
        by_key[key] = rows[ident]
    return by_key, rows


def write_units(adapter, out_dir, inst, store=None):
    from xscholar import originals, store as storemod
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = originals.mode(inst)
    counts = {"kept": 0, "excluded": 0, "by_type": {}, "by_status": {}, "originals_mode": mode, "documents": 0,
              "documents_deduplicated": 0, "documents_not_captured": 0, "units_with_original": 0,
              "units_without_original": 0, "without_original": []}
    if store is None and callable(getattr(adapter, "documents", None)):
        try:
            store = storemod.for_instance(inst)
        except storemod.StoreError as exc:
            raise IntakeError("adapter supplies originals but %s" % exc) from exc
    by_key, rows = _store_documents(adapter, inst, store, counts)
    counts["store"] = store.describe() if store is not None else None
    seen, used = set(), set()
    with (out_dir / "units.jsonl").open("w", encoding="utf-8") as units_file, \
         (out_dir / "excluded.jsonl").open("w", encoding="utf-8") as excluded_file, \
         (out_dir / "without_original.jsonl").open("w", encoding="utf-8") as gap_file:
        for raw in adapter.units():
            if not isinstance(raw, dict):
                raise IntakeError("adapter yielded a non-object unit: %r" % (raw,))
            if raw.get("exclude"):
                excluded_file.write(json.dumps({"id": raw.get("id"), "type": raw.get("type"),
                                                "reason": raw["exclude"]}, ensure_ascii=False) + "\n")
                counts["excluded"] += 1
                continue
            named = raw.get("document")
            doc = None
            if named is not None:
                doc = by_key.get(named)
                if doc is None and isinstance(named, str) and named.startswith("sha256:"):
                    doc = rows.get(named[len("sha256:"):])
                if doc is None:
                    raise IntakeError("unit %s: document %r is not an original this adapter supplied (known keys: %s)"
                                      % (raw.get("id"), named, ", ".join(sorted(by_key)[:10]) or "none"))
            check_unit(dict(raw, document=named), inst, seen, {named: doc} if doc else None)
            unit = {k: raw[k] for k in ("id", "type", "locator", "text", "status", "grade")}
            unit["meta"] = raw.get("meta") or {}
            retrievable = doc is not None and doc["capture"] != "none"
            if doc is not None:
                unit["document"] = doc["id"]
                used.add(doc["id"])
            if not retrievable:
                why = "no original named" if doc is None else "its original was never captured (%s)" % doc["original_name"]
                if mode == "required":
                    raise IntakeError("unit %s: %s, and this instance's originals are required: every unit must link to "
                                      "a retrievable original (supply it from the adapter's documents())" % (unit["id"], why))
                gap_file.write(json.dumps({"id": unit["id"], "type": unit["type"], "reason": why}, ensure_ascii=False) + "\n")
                counts["units_without_original"] += 1
                if len(counts["without_original"]) < 20:
                    counts["without_original"].append(unit["id"])
            else:
                counts["units_with_original"] += 1
            unit["hash"] = raw.get("hash") or unit_hash(unit)
            seen.add(unit["id"])
            units_file.write(json.dumps(unit, ensure_ascii=False) + "\n")
            counts["kept"] += 1
            counts["by_type"][unit["type"]] = counts["by_type"].get(unit["type"], 0) + 1
            counts["by_status"][unit["status"]] = counts["by_status"].get(unit["status"], 0) + 1
    if counts["kept"] == 0:
        raise IntakeError("intake produced 0 units; an empty corpus is a failure, not a result")
    with (out_dir / "documents.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows.values():
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts["documents"] = len(rows)
    counts["documents_not_captured"] = sum(1 for r in rows.values() if r["capture"] == "none")
    counts["documents_unused"] = sorted(set(rows) - used)
    return counts


def read_units(path):
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
