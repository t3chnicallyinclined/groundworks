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
        size = inst.unit_type(utype)["chunk_size"]
        for n, start in enumerate(range(0, len(rows), size)):
            piece = rows[start:start + size]
            cid = "%s_%04d" % (utype, n)
            with (slices_dir / (cid + ".jsonl")).open("w", encoding="utf-8") as fh:
                for unit in piece:
                    fh.write(json.dumps(unit, ensure_ascii=False) + "\n")
            chunks.append({"id": cid, "type": utype, "units": [u["id"] for u in piece], "count": len(piece)})
            placed += len(piece)
    if by_type:
        raise ChunkError("units of unknown type: %s" % ", ".join(sorted(by_type)))
    if placed != len(units):
        raise ChunkError("placed %d of %d units" % (placed, len(units)))
    (out_dir / "chunks.json").write_text(json.dumps(chunks, indent=2), encoding="utf-8")
    return chunks
