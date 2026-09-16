# -*- coding: utf-8 -*-
"""embed.py -- evidence units to vectors, incrementally, with a LOCAL model.

Rules (docs/LICENSING.md s.4, engine/embed/README.md):
  * similarity only PROPOSES candidates; it is never evidence and never promotes anything
  * always paired with keyword search (bakeoff.py measures both and their fusion)
  * local models only: unit text never leaves the machine; only model weights are downloaded
  * every vector row stores the model id, the sha256 of the text, and a key over everything else that
    changes the vector (prompt, max sequence length, normalization)
  * permissive model licenses only

Input:  units.jsonl (docs/MODULES.md contract; this module needs id, type, text)
Output: <out>/<model_slug>/vectors.npy     float32 matrix, one row per embedded unit
        <out>/<model_slug>/vectors.jsonl   one row per vector: id, model, dim, text_sha256, input_key, row

Incremental: a unit whose (model, text_sha256, input_key) already has a row is reused, not re-encoded.
Units with empty text are skipped and counted. Units no longer present are dropped and counted.

    python engine/embed/embed.py --units units.jsonl --out vectors/ --model BAAI/bge-m3
    python engine/embed/embed.py --units units.jsonl --out vectors/ --models models.json

models.json: [{"model": "...", "query_prompt": "...", "doc_prompts": {"<type>": "..."},
               "default_doc_prompt": null, "max_seq_length": 1024, "batch_size": 16,
               "dtype": "float16", "normalize": true}]
Exit 0 ok, 1 error (named), 2 NOT EXECUTED (bad arguments or unreadable input).

The .npy reader and writer are stdlib so the incremental logic is testable without numpy or torch.
"""
import argparse
import ast
import hashlib
import json
import os
import pathlib
import re
import struct
import sys
import time
from array import array

REQUIRED = ("id", "type", "text")
SPEC_DEFAULTS = {"query_prompt": None, "doc_prompts": {}, "default_doc_prompt": None, "max_seq_length": 1024,
                 "batch_size": 16, "dtype": "float16", "normalize": True}


class ContractError(ValueError):
    """An input or stored file breaks its contract."""


# ---------------------------------------------------------------- files

def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_jsonl(path, required=(), unique="id"):
    path = pathlib.Path(path)
    rows, seen = [], set()
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError("%s:%d: not JSON (%s)" % (path.name, n, exc.msg)) from None
            missing = [k for k in required if k not in row]
            if missing:
                raise ContractError("%s:%d: missing required field(s) %s" % (path.name, n, ", ".join(missing)))
            if unique:
                if row[unique] in seen:
                    raise ContractError("%s:%d: duplicate %s %r" % (path.name, n, unique, row[unique]))
                seen.add(row[unique])
            rows.append(row)
    return rows


def load_units(path):
    units = load_jsonl(path, REQUIRED)
    for u in units:
        if not isinstance(u["text"], str):
            raise ContractError("unit %r: text is not a string" % u["id"])
    return units


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def write_npy(path, flat, n, dim):
    """Write a float32 (n, dim) matrix in .npy format 1.0 from a flat array('f')."""
    if len(flat) != n * dim:
        raise ContractError("write_npy: %d values for shape (%d, %d)" % (len(flat), n, dim))
    header = "{'descr': '<f4', 'fortran_order': False, 'shape': (%d, %d), }" % (n, dim)
    pad = 64 - (10 + len(header) + 1) % 64
    header = header + " " * pad + "\n"
    data = array("f", flat)
    if sys.byteorder == "big":
        data.byteswap()
    with open(path, "wb") as f:
        f.write(b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin1"))
        f.write(data.tobytes())


def read_npy(path):
    """Read a float32 2-D .npy (format 1.x or 2.x). Returns (flat array('f'), n, dim)."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:6] != b"\x93NUMPY":
        raise ContractError("%s: not an .npy file" % path)
    major = raw[6]
    if major == 1:
        hlen, start = struct.unpack("<H", raw[8:10])[0], 10
    else:
        hlen, start = struct.unpack("<I", raw[8:12])[0], 12
    header = ast.literal_eval(raw[start:start + hlen].decode("latin1"))
    if header.get("descr") not in ("<f4", "|f4") or header.get("fortran_order") or len(header.get("shape", ())) != 2:
        raise ContractError("%s: expected a C-order float32 2-D matrix, got %r" % (path, header))
    n, dim = header["shape"]
    data = array("f")
    data.frombytes(raw[start + hlen:])
    if sys.byteorder == "big":
        data.byteswap()
    if len(data) != n * dim:
        raise ContractError("%s: %d values for shape (%d, %d)" % (path, len(data), n, dim))
    return data, n, dim


# ---------------------------------------------------------------- specs and keys

def normalize_spec(spec):
    if isinstance(spec, str):
        spec = {"model": spec}
    if not spec.get("model"):
        raise ContractError("model spec without a model name: %r" % spec)
    out = dict(SPEC_DEFAULTS)
    out.update(spec)
    out["doc_prompts"] = dict(out.get("doc_prompts") or {})
    return out


def model_slug(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "__", name)


def doc_prompt(spec, unit_type):
    return spec["doc_prompts"].get(unit_type, spec["default_doc_prompt"])


def input_key(spec, prompt):
    """Everything other than the text that changes the vector."""
    blob = json.dumps({"model": spec["model"], "prompt": prompt, "max_seq_length": spec["max_seq_length"],
                       "normalize": bool(spec["normalize"])}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- encoder

class SentenceTransformerEncoder:
    """A local sentence-transformers model. Imports torch only when constructed."""

    def __init__(self, spec):
        import torch
        from sentence_transformers import SentenceTransformer
        self.torch = torch
        self.spec = spec
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = getattr(torch, spec["dtype"]) if spec.get("dtype") and self.device == "cuda" else torch.float32
        t = time.time()
        self.model = SentenceTransformer(spec["model"], device=self.device, model_kwargs={"dtype": dtype},
                                         local_files_only=bool(spec.get("local_files_only", False)))
        if spec.get("max_seq_length"):
            self.model.max_seq_length = int(spec["max_seq_length"])
        self.load_seconds = time.time() - t
        if self.device == "cuda":
            torch.cuda.reset_peak_memory_stats()

    def encode(self, texts, prompt):
        vecs = self.model.encode(list(texts), prompt=prompt, batch_size=int(self.spec["batch_size"]),
                                 normalize_embeddings=bool(self.spec["normalize"]), convert_to_numpy=True,
                                 show_progress_bar=False)
        vecs = vecs.astype("<f4", copy=False)
        flat = array("f")
        flat.frombytes(vecs.tobytes())
        return flat, int(vecs.shape[1])

    def stats(self):
        out = {"device": self.device, "load_seconds": round(self.load_seconds, 2)}
        if self.device == "cuda":
            out["gpu"] = self.torch.cuda.get_device_name(0)
            out["peak_gpu_mb"] = round(self.torch.cuda.max_memory_allocated() / 2 ** 20)
        return out


class LazyEncoder:
    """Construct the encoder only when something must actually be encoded."""

    def __init__(self, spec, factory):
        self.spec, self.factory, self.encoder = spec, factory, None

    def get(self):
        if self.encoder is None:
            self.encoder = self.factory(self.spec)
        return self.encoder


def encode_checked(encoder, texts, prompt):
    flat, dim = encoder.encode(texts, prompt)
    if dim <= 0 or len(flat) != len(texts) * dim:
        raise ContractError("encoder returned %d values of dim %d for %d texts" % (len(flat), dim, len(texts)))
    return flat, dim


# ---------------------------------------------------------------- store

def store_paths(out_dir, model):
    d = pathlib.Path(out_dir) / model_slug(model)
    return d, d / "vectors.npy", d / "vectors.jsonl"


def load_store(out_dir, model):
    """Existing vectors for a model: (rows, flat, dim). Empty when there is no store yet. Corruption raises."""
    d, npy, idx = store_paths(out_dir, model)
    if not npy.exists() and not idx.exists():
        return [], array("f"), 0
    if not (npy.exists() and idx.exists()):
        raise ContractError("%s: one of vectors.npy / vectors.jsonl is missing" % d)
    rows = load_jsonl(idx, ("id", "model", "dim", "text_sha256", "input_key", "row"))
    flat, n, dim = read_npy(npy)
    if n != len(rows) or any(r["row"] != i or r["dim"] != dim or r["model"] != model for i, r in enumerate(rows)):
        raise ContractError("%s: vectors.jsonl does not match vectors.npy (%d rows vs %d vectors)" % (d, len(rows), n))
    return rows, flat, dim


def vector_of(flat, dim, row):
    return flat[row * dim:(row + 1) * dim]


def embed_units(units, spec, out_dir, encoder):
    """Bring the store for spec['model'] up to date with units. `encoder` is a LazyEncoder.

    Returns stats. Raises ContractError on a corrupt store, a changed output dimension, or an encoder that
    returns fewer vectors than texts."""
    spec = normalize_spec(spec)
    model = spec["model"]
    old_rows, old_flat, old_dim = load_store(out_dir, model)
    old = {r["id"]: r for r in old_rows}
    plan, todo, skipped_empty = [], {}, 0          # plan: (id, text_sha256, input_key, old_row or None)
    for u in units:
        if not u["text"].strip():
            skipped_empty += 1
            continue
        prompt = doc_prompt(spec, u["type"])
        key, tsha = input_key(spec, prompt), sha256_text(u["text"])
        prev = old.get(u["id"])
        reuse = prev["row"] if prev and prev["text_sha256"] == tsha and prev["input_key"] == key else None
        if reuse is None:
            todo.setdefault(prompt, []).append((len(plan), u["text"]))
        plan.append((u["id"], tsha, key, reuse))
    embedded, chars, dim, new_vecs = 0, 0, old_dim, {}
    t0 = time.time()
    for prompt, items in todo.items():
        texts = [t for _, t in items]
        flat, d = encode_checked(encoder.get(), texts, prompt)
        if dim and d != dim:
            raise ContractError("model %s returned dim %d but the store holds dim %d; delete %s to rebuild"
                                % (model, d, dim, store_paths(out_dir, model)[0]))
        dim = d
        for j, (pos, _) in enumerate(items):
            new_vecs[pos] = vector_of(flat, d, j)
        embedded += len(items)
        chars += sum(len(t) for t in texts)
    seconds = time.time() - t0
    kept = set(p[0] for p in plan)
    dropped = sum(1 for r in old_rows if r["id"] not in kept)
    changed = embedded or dropped or len(plan) != len(old_rows) or any(
        p[3] != n for n, p in enumerate(plan))
    if changed or not store_paths(out_dir, model)[1].exists():
        flat, rows = array("f"), []
        for n, (uid, tsha, key, reuse) in enumerate(plan):
            flat.extend(new_vecs[n] if reuse is None else vector_of(old_flat, old_dim, reuse))
            rows.append({"id": uid, "model": model, "dim": dim, "text_sha256": tsha, "input_key": key, "row": n})
        _write_store(out_dir, model, rows, flat, dim)
    stats = {"model": model, "units": len(units), "vectors": len(plan), "reused": len(plan) - embedded,
             "embedded": embedded, "dropped": dropped, "skipped_empty": skipped_empty, "dim": dim,
             "encode_seconds": round(seconds, 2), "chars": chars,
             "units_per_second": round(embedded / seconds, 1) if embedded and seconds > 0 else None}
    if encoder.encoder is not None and hasattr(encoder.encoder, "stats"):
        stats.update(encoder.encoder.stats())
    return stats


def _write_store(out_dir, model, rows, flat, dim):
    d, npy, idx = store_paths(out_dir, model)
    d.mkdir(parents=True, exist_ok=True)
    tmp_npy, tmp_idx = npy.with_suffix(".npy.tmp"), idx.with_suffix(".jsonl.tmp")
    write_npy(tmp_npy, flat, len(rows), dim if rows else 0)
    write_jsonl(tmp_idx, rows)
    os.replace(tmp_npy, npy)
    os.replace(tmp_idx, idx)


def load_matrix(out_dir, model, ids):
    """Vectors for ids in the given order, as a flat array('f'). A missing id raises."""
    rows, flat, dim = load_store(out_dir, model)
    where = {r["id"]: r["row"] for r in rows}
    out = array("f")
    for uid in ids:
        if uid not in where:
            raise ContractError("no vector for unit %r under model %s" % (uid, model))
        out.extend(vector_of(flat, dim, where[uid]))
    return out, dim


# ---------------------------------------------------------------- CLI

def main(argv=None, encoder_factory=SentenceTransformerEncoder):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--units", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", action="append", default=[])
    ap.add_argument("--models", help="models.json (list of specs)")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        print("embed NOT EXECUTED: bad arguments")
        return 2
    specs = [normalize_spec(m) for m in args.model]
    if args.models:
        specs += [normalize_spec(s) for s in json.loads(pathlib.Path(args.models).read_text(encoding="utf-8"))]
    if not specs:
        print("embed NOT EXECUTED: no model given")
        return 2
    try:
        units = load_units(args.units)
    except (OSError, ContractError) as exc:
        print("embed NOT EXECUTED: %s" % exc)
        return 2
    failed = 0
    for spec in specs:
        try:
            stats = embed_units(units, spec, args.out, LazyEncoder(spec, encoder_factory))
            print(json.dumps(stats, sort_keys=True))
        except Exception as exc:  # a model that fails is reported by name, never skipped silently
            failed += 1
            print("EMBED FAILED %s: %s: %s" % (spec["model"], type(exc).__name__, str(exc)[:500]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
