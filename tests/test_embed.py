# -*- coding: utf-8 -*-
"""Tests for embed.py and bakeoff.py. Stdlib only: the encoder is mocked, torch and numpy are never needed.

Each check is tested both ways (a planted perfect ranking scores 1.0 and a reversed one scores low; an unchanged
unit is not re-embedded and a changed one is). The corpus is synthetic plant incident logs.

    python -m unittest discover -s engine/embed -v
"""
import contextlib
import hashlib
import io
import json
import math
import pathlib
import shutil
import sys
import tempfile
import unittest
from array import array

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks.embed import bakeoff  # noqa: E402
from groundworks.embed import embed  # noqa: E402

INCIDENTS = [
    ("inc_001", "log_entry", "Pump P-3 tripped on low suction pressure at 02:14; operator reset after a valve check."),
    ("inc_002", "log_entry", "Conveyor belt B-7 misaligned; maintenance crew realigned rollers and restarted the line."),
    ("inc_003", "log_entry", "Boiler feedwater temperature alarm cleared after the sensor was recalibrated."),
    ("inc_004", "log_entry", "Forklift collided with rack 12 in the north warehouse; no injuries, rack inspected."),
    ("inc_005", "shift_note", "Night shift reports repeated pump cavitation noise near P-3 suction header."),
    ("inc_006", "shift_note", "Compressor C-2 oil level low; topped up and scheduled a leak inspection."),
    ("inc_007", "log_entry", "Fire door in loading bay propped open; door closed and supervisor notified."),
    ("inc_008", "log_entry", "Power dip tripped the packaging line; UPS held the control room for four minutes."),
    ("inc_009", "shift_note", "Cooling tower fan vibration trending upward; bearing replacement requested."),
    ("inc_010", "log_entry", "Chemical spill of two litres of coolant in bay 4; absorbed and disposed per procedure."),
]
QUERIES = [
    {"id": "q_pump", "text": "why did the suction pump P-3 trip"},
    {"id": "q_spill", "text": "coolant spill cleanup in bay 4"},
    {"id": "q_orphan", "text": "an event nobody logged"},
]
QRELS = [
    {"query_id": "q_pump", "unit_id": "inc_001", "relevance": 2},
    {"query_id": "q_pump", "unit_id": "inc_005", "relevance": 1},
    {"query_id": "q_spill", "unit_id": "inc_010", "relevance": 1},
    {"query_id": "q_orphan", "unit_id": "no_such_unit", "relevance": 1},
]


def units(texts=None):
    texts = texts or {}
    return [{"id": i, "type": t, "text": texts.get(i, x), "locator": {"line": n}, "status": "usable",
             "grade": "original", "hash": "sha256:" + hashlib.sha256(texts.get(i, x).encode()).hexdigest()}
            for n, (i, t, x) in enumerate(INCIDENTS)]


class HashEncoder:
    """Deterministic bag-of-words hashing encoder (dim 64, L2-normalized). Records what it was asked."""
    built = 0

    def __init__(self, spec):
        HashEncoder.built += 1
        self.spec, self.calls = spec, []

    def encode(self, texts, prompt):
        self.calls.append((len(texts), prompt))
        flat = array("f")
        for t in texts:
            v = [0.0] * 64
            for tok in bakeoff.tokenize((prompt or "") + " " + t):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            flat.extend(x / norm for x in v)
        return flat, 64


class ShortEncoder(HashEncoder):
    def encode(self, texts, prompt):
        flat, dim = super().encode(texts, prompt)
        return flat[:-dim], dim


class BrokenEncoder:
    def __init__(self, spec):
        raise OSError("weights not found")


def ids(n):
    return ["d%03d" % i for i in range(n)]


class MetricMath(unittest.TestCase):
    def test_planted_perfect_ranking_scores_one(self):
        ranked = ["a", "b", "c"] + ids(50)
        rel = {"a": 1, "b": 1, "c": 1}
        m = bakeoff.per_query(ranked, rel)
        for k in bakeoff.METRICS:
            self.assertAlmostEqual(m[k], 1.0, msg=k)

    def test_reversed_ranking_scores_low(self):
        ranked = list(reversed(["a", "b", "c"] + ids(97)))
        m = bakeoff.per_query(ranked, {"a": 1, "b": 1, "c": 1})
        for k in bakeoff.METRICS:
            self.assertEqual(m[k], 0.0, msg=k)

    def test_hand_computed_values(self):
        ranked = ["x", "a", "y", "b"]
        m = bakeoff.per_query(ranked, {"a": 1, "b": 1})
        self.assertEqual(m["recall@5"], 1.0)
        self.assertEqual(m["mrr@10"], 0.5)
        expected = (1 / math.log2(3) + 1 / math.log2(5)) / (1 + 1 / math.log2(3))
        self.assertAlmostEqual(m["ndcg@10"], expected)

    def test_relevant_just_outside_cutoffs(self):
        ranked = ids(10) + ["a"] + ids(30)[10:]
        m = bakeoff.per_query(ranked, {"a": 1})
        self.assertEqual(m["recall@10"], 0.0)
        self.assertEqual(m["mrr@10"], 0.0)
        self.assertEqual(m["recall@20"], 1.0)

    def test_graded_ndcg_prefers_the_higher_grade_first(self):
        rel = {"hi": 2, "lo": 1}
        good = bakeoff.ndcg_at(["hi", "lo"], rel)
        bad = bakeoff.ndcg_at(["lo", "hi"], rel)
        self.assertAlmostEqual(good, 1.0)
        self.assertLess(bad, good)

    def test_bootstrap_interval(self):
        flat = bakeoff.bootstrap([0.5] * 20, 500, 1)
        self.assertEqual((flat["lo"], flat["mean"], flat["hi"]), (0.5, 0.5, 0.5))
        vals = [0.0] * 10 + [1.0] * 10
        b1, b2 = bakeoff.bootstrap(vals, 500, 7), bakeoff.bootstrap(vals, 500, 7)
        self.assertEqual(b1, b2)
        self.assertLessEqual(b1["lo"], 0.5)
        self.assertGreaterEqual(b1["hi"], 0.5)
        self.assertLess(b1["lo"], b1["hi"])
        same = bakeoff.bootstrap(vals, 500, 7, baseline=vals)
        self.assertEqual((same["diff_lo"], same["diff_hi"]), (0.0, 0.0))
        up = bakeoff.bootstrap([1.0] * 20, 500, 7, baseline=vals)
        self.assertGreater(up["diff_lo"], 0.0)
        with self.assertRaises(ValueError):
            bakeoff.bootstrap([], 10, 1)

    def test_rrf_hand_computed_order(self):
        # b = 1/61 + 1/62 ; a = 1/61 + 1/63 ; c = 1/62 + 1/63
        self.assertEqual(bakeoff.rrf([["a", "b", "c"], ["b", "c", "a"]]), ["b", "a", "c"])
        self.assertEqual(bakeoff.rrf([["a"], []]), ["a"])

    def test_random_control_is_near_chance(self):
        pool = ids(200)
        recalls = [bakeoff.recall_at(bakeoff.random_rank(pool, "s|%d" % q), {pool[q]}, 20) for q in range(200)]
        self.assertTrue(0.04 < sum(recalls) / len(recalls) < 0.16)


class BM25Case(unittest.TestCase):
    def setUp(self):
        self.docs = [bakeoff.tokenize(t) for _, _, t in INCIDENTS]
        self.index = bakeoff.BM25(self.docs)

    def test_rare_term_ranks_its_document_first(self):
        self.assertEqual(self.index.rank(bakeoff.tokenize("forklift rack"))[0], 3)

    def test_document_without_query_terms_is_not_ranked(self):
        ranked = self.index.rank(bakeoff.tokenize("forklift"))
        self.assertEqual(ranked, [3])
        self.assertEqual(self.index.rank(bakeoff.tokenize("zebra")), [])

    def test_idf_orders_rare_over_common(self):
        s = self.index.scores(bakeoff.tokenize("the cavitation"))
        self.assertIn(4, s)
        self.assertEqual(self.index.rank(bakeoff.tokenize("the cavitation"))[0], 4)


class IncrementalCase(unittest.TestCase):
    SPEC = {"model": "mock/hash-64"}

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        HashEncoder.built = 0

    def embed(self, us, spec=None, factory=HashEncoder):
        spec = embed.normalize_spec(spec or self.SPEC)
        lazy = embed.LazyEncoder(spec, factory)
        return embed.embed_units(us, spec, self.dir, lazy), lazy

    def test_unchanged_units_are_not_reembedded(self):
        s1, _ = self.embed(units())
        self.assertEqual((s1["embedded"], s1["reused"]), (10, 0))
        s2, lazy = self.embed(units())
        self.assertEqual((s2["embedded"], s2["reused"], s2["dropped"]), (0, 10, 0))
        self.assertIsNone(lazy.encoder, "no model should be loaded when nothing changed")

    def test_changed_text_hash_reembeds_only_that_unit(self):
        self.embed(units())
        rows0, flat0, dim = embed.load_store(self.dir, self.SPEC["model"])
        s, lazy = self.embed(units({"inc_004": "Forklift collided with rack 14; operator retrained."}))
        self.assertEqual((s["embedded"], s["reused"]), (1, 9))
        self.assertEqual(lazy.encoder.calls, [(1, None)])
        rows1, flat1, _ = embed.load_store(self.dir, self.SPEC["model"])
        for r0, r1 in zip(rows0, rows1):
            same = embed.vector_of(flat0, dim, r0["row"]) == embed.vector_of(flat1, dim, r1["row"])
            self.assertEqual(same, r0["id"] != "inc_004", r0["id"])
            self.assertEqual(r0["text_sha256"] == r1["text_sha256"], r0["id"] != "inc_004")

    def test_changed_prompt_reembeds_that_type_only(self):
        self.embed(units())
        spec = dict(self.SPEC, doc_prompts={"shift_note": "Represent this shift note: "})
        s, lazy = self.embed(units(), spec)
        self.assertEqual(s["embedded"], 3)
        self.assertEqual(lazy.encoder.calls, [(3, "Represent this shift note: ")])

    def test_rows_carry_model_and_text_hash(self):
        self.embed(units())
        rows, flat, dim = embed.load_store(self.dir, self.SPEC["model"])
        text = {u["id"]: u["text"] for u in units()}
        self.assertEqual(len(flat), 10 * dim)
        for r in rows:
            self.assertEqual(r["model"], "mock/hash-64")
            self.assertEqual(r["dim"], 64)
            self.assertEqual(r["text_sha256"], hashlib.sha256(text[r["id"]].encode("utf-8")).hexdigest())

    def test_removed_unit_is_dropped_and_empty_text_skipped(self):
        self.embed(units())
        us = units({"inc_002": "   "})[:-1]
        s, _ = self.embed(us)
        self.assertEqual((s["dropped"], s["skipped_empty"], s["vectors"]), (2, 1, 8))

    def test_encoder_returning_fewer_vectors_fails_loudly(self):
        with self.assertRaises(embed.ContractError):
            self.embed(units(), factory=ShortEncoder)
        self.assertFalse((self.dir / "mock__hash-64" / "vectors.npy").exists())

    def test_corrupt_store_raises(self):
        self.embed(units())
        idx = self.dir / "mock__hash-64" / "vectors.jsonl"
        idx.write_text("\n".join(idx.read_text(encoding="utf-8").splitlines()[:-1]) + "\n", encoding="utf-8")
        with self.assertRaises(embed.ContractError):
            self.embed(units())

    def test_npy_roundtrip_and_shape_check(self):
        flat = array("f", [float(i) for i in range(12)])
        path = self.dir / "m.npy"
        embed.write_npy(path, flat, 3, 4)
        back, n, dim = embed.read_npy(path)
        self.assertEqual((list(back), n, dim), (list(flat), 3, 4))
        self.assertEqual(path.stat().st_size % 4, 0)
        with self.assertRaises(embed.ContractError):
            embed.write_npy(path, flat, 5, 4)

    def test_units_contract(self):
        p = self.dir / "u.jsonl"
        p.write_text(json.dumps({"id": "x", "type": "log_entry"}) + "\n", encoding="utf-8")
        with self.assertRaises(embed.ContractError):
            embed.load_units(p)
        p.write_text(json.dumps({"id": "x", "type": "t", "text": "a"}) + "\n" +
                     json.dumps({"id": "x", "type": "t", "text": "b"}) + "\n", encoding="utf-8")
        with self.assertRaises(embed.ContractError):
            embed.load_units(p)


class BakeoffEndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        us = units() + [{"id": "summary_1", "type": "summary", "text": "Pump P-3 suction trip summary."}]
        for name, rows in (("units", us), ("queries", QUERIES), ("qrels", QRELS)):
            embed.write_jsonl(self.dir / (name + ".jsonl"), rows)

    def args(self, *extra):
        return ["--units", str(self.dir / "units.jsonl"), "--queries", str(self.dir / "queries.jsonl"),
                "--qrels", str(self.dir / "qrels.jsonl"), "--out", str(self.dir / "out"), "--bootstrap", "200",
                "--exclude-types", "summary"] + list(extra)

    def models(self, *names):
        p = self.dir / "models.json"
        p.write_text(json.dumps([{"model": n, "query_prompt": "Query: "} for n in names]), encoding="utf-8")
        return str(p)

    def test_all_methods_reported_with_counts(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = bakeoff.main(self.args("--models", self.models("mock/hash-64")), encoder_factory=HashEncoder)
        self.assertEqual(code, 0)
        rep = json.loads((self.dir / "out" / "bakeoff_default.json").read_text(encoding="utf-8"))
        self.assertEqual(set(rep["methods"]), {"random", "bm25", "dense:mock/hash-64", "rrf:mock/hash-64"})
        c = rep["counts"]
        self.assertEqual((c["queries_total"], c["queries_with_relevant"], c["corpus_units"]), (3, 2, 10))
        self.assertEqual(c["qrels_unit_not_in_corpus"], 1)
        self.assertEqual(rep["methods"]["bm25"]["recall@5"]["mean"], 1.0)
        for meth, row in rep["methods"].items():
            for m in bakeoff.METRICS:
                self.assertLessEqual(row[m]["lo"], row[m]["mean"] + 1e-12, (meth, m))
                self.assertGreaterEqual(row[m]["hi"], row[m]["mean"] - 1e-12, (meth, m))
        blob = json.dumps(rep) + (self.dir / "out" / "rankings_default.json").read_text(encoding="utf-8")
        for _, _, text in INCIDENTS:
            self.assertNotIn(text, blob)
        rankings = json.loads((self.dir / "out" / "rankings_default.json").read_text(encoding="utf-8"))
        self.assertNotIn("summary_1", sum(rankings["bm25"].values(), []))

    def test_failed_model_is_reported_not_skipped(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = bakeoff.main(self.args("--models", self.models("mock/broken")), encoder_factory=BrokenEncoder)
        self.assertEqual(code, 1)
        self.assertIn("MODEL FAILED mock/broken", out.getvalue())
        rep = json.loads((self.dir / "out" / "bakeoff_default.json").read_text(encoding="utf-8"))
        self.assertEqual([f["model"] for f in rep["failures"]], ["mock/broken"])

    def test_no_judged_query_is_not_executed(self):
        embed.write_jsonl(self.dir / "qrels.jsonl", [QRELS[-1]])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = bakeoff.main(self.args(), encoder_factory=HashEncoder)
        self.assertEqual(code, 2)
        self.assertIn("NOT EXECUTED", out.getvalue())

    def test_numpy_and_stdlib_dense_rank_agree(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy not installed; stdlib path is the one exercised")
        enc = HashEncoder({})
        d, dim = enc.encode([t for _, _, t in INCIDENTS], None)
        q, _ = enc.encode([x["text"] for x in QUERIES], None)
        fast = bakeoff.dense_rank(q, 3, d, 10, dim, top=5)
        saved = sys.modules.get("numpy")
        sys.modules["numpy"] = None
        try:
            slow = bakeoff.dense_rank(q, 3, d, 10, dim, top=5)
        finally:
            sys.modules["numpy"] = saved
        self.assertEqual([set(a) for a in fast], [set(b) for b in slow])


if __name__ == "__main__":
    unittest.main()
