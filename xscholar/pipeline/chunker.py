# -*- coding: utf-8 -*-
"""chunker.py -- units.jsonl -> chunks.json + slices/<chunk>.jsonl, every unit in exactly one slice.

Slices are cut per unit type at the instance's chunk_size, in input order, deterministically.
    chunks = chunk(units_path, out_dir, instance)
Standard library only.
"""
import json
import pathlib

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.pipeline.intake import read_units


class ChunkError(Exception):
    pass


def _pieces(rows, size, chunk_by=None):
    """Cut rows into slices of at most `size`. With `chunk_by` (a locator field), units that share a
    value of that field stay in one slice: a slice is closed only at a value boundary, unless one
    value alone exceeds `size`, in which case that value is split at `size` (and reported as such).

    Groups are formed over the WHOLE input, not over runs of adjacent rows: values appear in the order
    their first unit appears, and units keep input order within their value. Until 2026-09-16 only
    consecutive units were grouped, so d1, d2, d1 put day d1 in two slices without a word (measured).
    Grouping rather than refusing unsorted input: adapters yield units in source order (per file, per
    host), and a corpus interleaved across sources is ordinary, not an error; the promise is "one value,
    one slice", and input order carries no meaning the labeller loses by grouping."""
    if not chunk_by:
        for start in range(0, len(rows), size):
            yield rows[start:start + size]
        return
    groups = {}                                    # insertion-ordered: first appearance of each value
    for row in rows:
        key = str((row.get("locator") or {}).get(chunk_by))
        groups.setdefault(key, []).append(row)
    piece = []
    for group in groups.values():
        if len(group) > size:
            if piece:
                yield piece
                piece = []
            for start in range(0, len(group), size):
                yield group[start:start + size]
            continue
        if piece and len(piece) + len(group) > size:
            yield piece
            piece = []
        piece.extend(group)
    if piece:
        yield piece


def chunk(units_path, out_dir, inst):
    out_dir = pathlib.Path(out_dir)
    slices_dir = out_dir / "slices"
    slices_dir.mkdir(parents=True, exist_ok=True)
    units = read_units(units_path)
    by_type = {}
    for unit in units:
        by_type.setdefault(unit["type"], []).append(unit)
    chunks = []
    placed = 0
    for utype in inst.unit_names():
        rows = by_type.pop(utype, [])
        spec = inst.unit_type(utype)
        size = spec["chunk_size"]
        for n, piece in enumerate(_pieces(rows, size, spec.get("chunk_by"))):
            cid = "%s_%04d" % (utype, n)
            with (slices_dir / (cid + ".jsonl")).open("w", encoding="utf-8") as fh:
                for unit in piece:
                    fh.write(json.dumps(unit, ensure_ascii=False) + "\n")
            chunks.append({"id": cid, "type": utype, "units": [u["id"] for u in piece], "count": len(piece),
                           "chunk_by": spec.get("chunk_by")})
            placed += len(piece)
    if by_type:
        raise ChunkError("units of unknown type: %s" % ", ".join(sorted(by_type)))
    if placed != len(units):
        raise ChunkError("placed %d of %d units" % (placed, len(units)))
    (out_dir / "chunks.json").write_text(json.dumps(chunks, indent=2), encoding="utf-8")
    return chunks
