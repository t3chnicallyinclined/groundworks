# -*- coding: utf-8 -*-
"""packet.py -- `ask open`: question -> a work packet a reader can be handed and a checker can audit.

    info = open_packet(question, units_path, out_dir, inst=None, k=200, slice_size=40)

Writes into out_dir:
    question.json       the question, the corpus file and its sha256, corpus_total, retrieved, k, the slices
    candidates.jsonl    the retrieved units, full rows, with rank and score, best first
    slices/ask_NN.jsonl the same units cut into slices of at most slice_size, in rank order

Status in question.json is OPEN (there is something to read) or NO_HITS (no unit shares a token with the
question). NO_HITS is written and reported, never raised: an empty result is an answer.

Standard library only. Deterministic for a given corpus, question, k and slice size.
"""
import datetime
import hashlib
import json
import pathlib

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks.retrieve import BM25, tokenize  # noqa: E402
from groundworks.pipeline.intake import read_units  # noqa: E402

DEFAULT_K = 200
DEFAULT_SLICE = 40


class PacketError(Exception):
    pass


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _slice_size(inst, default):
    """The smallest chunk_size the instance declares, so a slice is never wider than the instance's own
    reading unit; the default when there is no instance."""
    if inst is None:
        return default
    sizes = [int(u.get("chunk_size") or 0) for u in getattr(inst, "units", [])]
    sizes = [s for s in sizes if s > 0]
    return min(sizes + [default]) if sizes else default


def in_scope(unit, types=None, where=None):
    """The scope filter: unit types to keep, and `field=value` conditions on the unit's top-level
    fields or its locator. Measured on the first instance (2026-09-22): the eight long decision pages
    ranked 578-2997 for "what reasons did the court give" against 20,000 short messages, with or
    without common query tokens dropped, so a scope is how a reader reaches a unit type BM25's length
    normalisation buries. The packet records the scope and counts what it excluded."""
    if types and unit.get("type") not in types:
        return False
    for key, value in (where or {}).items():
        have = unit.get(key) if key in unit else (unit.get("locator") or {}).get(key)
        if str(have) != str(value):
            return False
    return True


def open_packet(question, units_path, out_dir, inst=None, k=DEFAULT_K, slice_size=None, types=None, where=None):
    question = (question or "").strip()
    if not question:
        raise PacketError("the question is empty")
    if k <= 0:
        raise PacketError("k must be positive")
    units_path = pathlib.Path(units_path)
    out_dir = pathlib.Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise PacketError("out_dir %s is not empty: a packet is never overwritten, open a new directory" % out_dir)
    corpus = read_units(units_path)
    ids = [u.get("id") for u in corpus]
    if len(set(ids)) != len(ids) or any(not i for i in ids):
        raise PacketError("units.jsonl has a missing or duplicate id; refuse to rank a corpus a cite cannot name")
    size = slice_size or _slice_size(inst, DEFAULT_SLICE)
    types = sorted(set(types or []))
    where = dict(where or {})
    units = [u for u in corpus if in_scope(u, types, where)] if (types or where) else corpus

    index = BM25([tokenize(u.get("text")) for u in units])
    ranked = index.ranked(tokenize(question), top=k)
    (out_dir / "slices").mkdir(parents=True, exist_ok=True)

    candidates = []
    with (out_dir / "candidates.jsonl").open("w", encoding="utf-8") as fh:
        for rank, (i, score) in enumerate(ranked, 1):
            row = dict(units[i])
            row["rank"], row["score"] = rank, round(score, 6)
            candidates.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    slices = []
    for n, start in enumerate(range(0, len(candidates), size)):
        piece = candidates[start:start + size]
        sid = "ask_%02d" % n
        path = out_dir / "slices" / (sid + ".jsonl")
        with path.open("w", encoding="utf-8") as fh:
            for row in piece:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        slices.append({"id": sid, "path": "slices/%s.jsonl" % sid, "units": [r["id"] for r in piece]})

    info = {
        "question": question,
        "units_path": str(units_path),
        "units_sha256": _sha256(units_path),
        "corpus_total": len(corpus),
        "scope": {"types": types, "where": where, "in_scope": len(units), "out_of_scope": len(corpus) - len(units)},
        "retrieved": len(candidates),
        "k": k,
        "slice_size": size,
        "ranker": "bm25",
        "instance": getattr(inst, "id", None),
        "status": "OPEN" if candidates else "NO_HITS",
        "slices": slices,
        "created_utc": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
    }
    (out_dir / "question.json").write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
    return info


def load_packet(out_dir):
    """question.json, the candidates by id, and the slices; PacketError when the packet is not whole."""
    out_dir = pathlib.Path(out_dir)
    qpath = out_dir / "question.json"
    if not qpath.exists():
        raise PacketError("no question.json in %s: not an ask packet" % out_dir)
    info = json.loads(qpath.read_text(encoding="utf-8"))
    cpath = out_dir / "candidates.jsonl"
    if not cpath.exists():
        raise PacketError("no candidates.jsonl in %s" % out_dir)
    candidates = {row["id"]: row for row in read_units(cpath)}
    if len(candidates) != info.get("retrieved"):
        raise PacketError("candidates.jsonl holds %d units, question.json says %d retrieved" % (len(candidates), info.get("retrieved")))
    for sl in info.get("slices") or []:
        spath = out_dir / sl["path"]
        if not spath.exists():
            raise PacketError("slice file missing: %s" % sl["path"])
        on_disk = [r["id"] for r in read_units(spath)]
        if on_disk != sl["units"]:
            raise PacketError("slice %s on disk does not match question.json" % sl["id"])
    return info, candidates
