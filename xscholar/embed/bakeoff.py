# -*- coding: utf-8 -*-
"""bakeoff.py -- does an embedding model find the evidence better than keyword search? Measure it.

Inputs:
  units.jsonl    docs/MODULES.md contract (id, type, text, ...)
  queries.jsonl  {"id", "text"}
  qrels.jsonl    {"query_id", "unit_id", "relevance"}   relevance >= 1 is relevant; graded for nDCG
  models.json    list of model specs (see embed.py); optional

Methods:
  random         seeded random ranking (control: what a method that knows nothing scores)
  bm25           keyword baseline (Okapi BM25, k1=1.2, b=0.75, lowercase word tokens; stdlib)
  dense:<model>  cosine similarity of local-model vectors (embed.py store, incremental)
  rrf:<model>    reciprocal-rank fusion of bm25 and dense:<model>, k=60

Metrics per method: Recall@5/10/20, MRR@10, nDCG@10, each a mean over queries with a 95% percentile bootstrap
interval (queries resampled). A method is reported BETTER than bm25 on a metric only if its whole interval lies
above the bm25 point estimate AND the paired bootstrap difference interval is above zero.

Similarity only proposes candidates; nothing here is evidence. Output holds ids and numbers only, never text.

    python engine/embed/bakeoff.py --units u.jsonl --queries q.jsonl --qrels r.jsonl --out DIR [--models m.json]
        [--exclude-types T ...] [--bootstrap 2000] [--seed 13] [--name NAME]
Writes DIR/bakeoff_<name>.json (metrics, counts, timings) and DIR/rankings_<name>.json (top 20 ids per method).
Exit 0 ran, 1 a model failed or no query had a relevant unit, 2 NOT EXECUTED.
"""
import argparse
import json
import math
import pathlib
import random
import re
import sys
import time

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.embed import embed  # noqa: E402

KS = (5, 10, 20)
METRICS = ("recall@5", "recall@10", "recall@20", "mrr@10", "ndcg@10")
RRF_K = 60
POOL = 1000          # depth of each list fed to fusion
TOKEN = re.compile(r"\w+", re.UNICODE)


# ---------------------------------------------------------------- rankers

def tokenize(text):
    return TOKEN.findall(text.lower())


class BM25:
    def __init__(self, docs, k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.lens = [len(d) for d in docs]
        self.avg = (sum(self.lens) / self.n) if self.n else 0.0
        self.post = {}
        for i, toks in enumerate(docs):
            tf = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t, c in tf.items():
                self.post.setdefault(t, []).append((i, c))
        self.idf = {t: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.post.items()}

    def scores(self, query_tokens):
        acc = {}
        for t in set(query_tokens):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, c in self.post[t]:
                norm = self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg) if self.avg else self.k1
                acc[i] = acc.get(i, 0.0) + idf * c * (self.k1 + 1) / (c + norm)
        return acc

    def rank(self, query_tokens, top=POOL):
        acc = self.scores(query_tokens)
        return [i for i, _ in sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]


def rrf(lists, k=RRF_K, top=POOL):
    """Reciprocal-rank fusion: score(d) = sum over lists of 1 / (k + rank), rank from 1."""
    acc = {}
    for lst in lists:
        for r, d in enumerate(lst, 1):
            acc[d] = acc.get(d, 0.0) + 1.0 / (k + r)
    return [d for d, _ in sorted(acc.items(), key=lambda kv: (-kv[1], str(kv[0])))[:top]]


def random_rank(ids, seed, top=POOL):
    order = list(ids)
    random.Random(seed).shuffle(order)
    return order[:top]


def dense_rank(query_flat, nq, doc_flat, nd, dim, top=POOL):
    """Top ids by dot product (vectors are normalized, so cosine). numpy when present, stdlib otherwise."""
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        Q = np.frombuffer(query_flat.tobytes(), dtype="<f4").reshape(nq, dim)
        D = np.frombuffer(doc_flat.tobytes(), dtype="<f4").reshape(nd, dim)
        S = Q @ D.T
        k = min(top, nd)
        out = []
        for row in S:
            idx = np.argpartition(-row, k - 1)[:k] if k < nd else np.arange(nd)
            out.append([int(i) for i in idx[np.lexsort((idx, -row[idx]))]])
        return out
    out = []
    for q in range(nq):
        qv = query_flat[q * dim:(q + 1) * dim]
        sims = [(-sum(a * b for a, b in zip(qv, doc_flat[d * dim:(d + 1) * dim])), d) for d in range(nd)]
        out.append([d for _, d in sorted(sims)[:top]])
    return out


# ---------------------------------------------------------------- metrics

def recall_at(ranked, rel, k):
    return len(set(ranked[:k]) & set(rel)) / len(rel) if rel else 0.0


def mrr_at(ranked, rel, k=10):
    for r, d in enumerate(ranked[:k], 1):
        if d in rel:
            return 1.0 / r
    return 0.0


def ndcg_at(ranked, rel, k=10):
    """rel: {id: grade}. Gain 2^grade - 1, discount log2(rank + 1)."""
    dcg = sum((2 ** rel.get(d, 0) - 1) / math.log2(r + 1) for r, d in enumerate(ranked[:k], 1))
    ideal = sorted((g for g in rel.values() if g > 0), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(r + 1) for r, g in enumerate(ideal, 1))
    return dcg / idcg if idcg else 0.0


def per_query(ranked, rel):
    relset = {d for d, g in rel.items() if g > 0}
    out = {"recall@%d" % k: recall_at(ranked, relset, k) for k in KS}
    out["mrr@10"] = mrr_at(ranked, relset, 10)
    out["ndcg@10"] = ndcg_at(ranked, rel, 10)
    return out


def bootstrap(values, samples=2000, seed=13, baseline=None):
    """Mean and 95% percentile interval; with baseline (same length), also the paired difference interval."""
    n = len(values)
    if n == 0:
        raise ValueError("bootstrap over zero queries")
    rng = random.Random(seed)
    means, diffs = [], []
    for _ in range(samples):
        idx = [rng.randrange(n) for _ in range(n)]
        means.append(sum(values[i] for i in idx) / n)
        if baseline is not None:
            diffs.append(sum(values[i] - baseline[i] for i in idx) / n)
    means.sort()
    lo, hi = means[int(0.025 * samples)], means[min(samples - 1, int(0.975 * samples))]
    out = {"mean": sum(values) / n, "lo": lo, "hi": hi}
    if baseline is not None:
        diffs.sort()
        out["diff_mean"] = (sum(values) - sum(baseline)) / n
        out["diff_lo"], out["diff_hi"] = diffs[int(0.025 * samples)], diffs[min(samples - 1, int(0.975 * samples))]
    return out


# ---------------------------------------------------------------- run

def load_eval(units_path, queries_path, qrels_path, exclude_types=()):
    units = embed.load_units(units_path)
    corpus = [u for u in units if u["type"] not in set(exclude_types) and u["text"].strip()]
    ids = {u["id"] for u in corpus}
    queries = embed.load_jsonl(queries_path, ("id", "text"))
    qrels_rows = embed.load_jsonl(qrels_path, ("query_id", "unit_id", "relevance"), unique=None)
    qids = {q["id"] for q in queries}
    qrels, outside, unknown_q = {}, 0, 0
    for r in qrels_rows:
        if r["query_id"] not in qids:
            unknown_q += 1
            continue
        if r["unit_id"] not in ids:
            outside += 1
            continue
        if r["relevance"] > 0:
            cur = qrels.setdefault(r["query_id"], {})
            cur[r["unit_id"]] = max(cur.get(r["unit_id"], 0), int(r["relevance"]))
    judged = [q for q in queries if q["id"] in qrels]
    counts = {"units_total": len(units), "corpus_units": len(corpus), "queries_total": len(queries),
              "queries_with_relevant": len(judged), "queries_without_relevant": len(queries) - len(judged),
              "qrels_rows": len(qrels_rows), "qrels_used": sum(len(v) for v in qrels.values()),
              "qrels_unit_not_in_corpus": outside, "qrels_unknown_query": unknown_q,
              "corpus_by_type": {}}
    for u in corpus:
        counts["corpus_by_type"][u["type"]] = counts["corpus_by_type"].get(u["type"], 0) + 1
    return units, corpus, judged, qrels, counts


def run(units_path, queries_path, qrels_path, out_dir, specs=(), exclude_types=(), samples=2000, seed=13,
        name="default", encoder_factory=embed.SentenceTransformerEncoder):
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    units, corpus, queries, qrels, counts = load_eval(units_path, queries_path, qrels_path, exclude_types)
    if not queries:
        raise embed.ContractError("no query has a relevant unit in the corpus; nothing can be measured")
    cids = [u["id"] for u in corpus]
    rankings, timings, failures = {}, {}, []

    t = time.time()
    index = BM25([tokenize(u["text"]) for u in corpus])
    rankings["bm25"] = {q["id"]: [cids[i] for i in index.rank(tokenize(q["text"]))] for q in queries}
    timings["bm25_seconds"] = round(time.time() - t, 2)
    rankings["random"] = {q["id"]: random_rank(cids, "%s|%s" % (seed, q["id"])) for q in queries}

    model_stats = []
    for spec in [embed.normalize_spec(s) for s in specs]:
        m = spec["model"]
        try:
            lazy = embed.LazyEncoder(spec, encoder_factory)
            stats = embed.embed_units(units, spec, out_dir, lazy)
            doc_flat, dim = embed.load_matrix(out_dir, m, cids)
            t = time.time()
            q_flat, qdim = embed.encode_checked(lazy.get(), [q["text"] for q in queries], spec["query_prompt"])
            stats["query_encode_seconds"] = round(time.time() - t, 2)
            if qdim != dim:
                raise embed.ContractError("query dim %d != document dim %d" % (qdim, dim))
            dense = dense_rank(q_flat, len(queries), doc_flat, len(cids), dim)
            rankings["dense:" + m] = {q["id"]: [cids[i] for i in dense[j]] for j, q in enumerate(queries)}
            rankings["rrf:" + m] = {q["id"]: rrf([rankings["bm25"][q["id"]], rankings["dense:" + m][q["id"]]])
                                    for q in queries}
            model_stats.append(stats)
        except Exception as exc:  # reported by name; the run still fails at exit
            failures.append({"model": m, "error": "%s: %s" % (type(exc).__name__, str(exc)[:500])})

    per = {meth: {q["id"]: per_query(r[q["id"]], qrels[q["id"]]) for q in queries} for meth, r in rankings.items()}
    qorder = [q["id"] for q in queries]
    table = {}
    for meth in rankings:
        table[meth] = {}
        for metric in METRICS:
            vals = [per[meth][qid][metric] for qid in qorder]
            base = [per["bm25"][qid][metric] for qid in qorder] if meth != "bm25" else None
            b = bootstrap(vals, samples, seed, base)
            if base is not None:
                b["better_than_bm25"] = b["lo"] > table["bm25"][metric]["mean"] and b["diff_lo"] > 0
                b["worse_than_bm25"] = b["hi"] < table["bm25"][metric]["mean"] and b["diff_hi"] < 0
            table[meth][metric] = b
    report = {"name": name, "counts": counts, "methods": table, "models": model_stats, "failures": failures,
              "timings": timings, "bootstrap_samples": samples, "seed": seed, "rrf_k": RRF_K, "pool": POOL,
              "rule": "better = 95% interval entirely above the bm25 mean AND paired difference interval above 0"}
    (out_dir / ("bakeoff_%s.json" % name)).write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    top = {meth: {qid: r[qid][:20] for qid in qorder} for meth, r in rankings.items()}
    (out_dir / ("rankings_%s.json" % name)).write_text(json.dumps(top, sort_keys=True), encoding="utf-8")
    return report


def format_table(report):
    lines = ["queries with a relevant unit: %d of %d; corpus units: %d"
             % (report["counts"]["queries_with_relevant"], report["counts"]["queries_total"],
                report["counts"]["corpus_units"]),
             "%-44s %s" % ("method", "  ".join("%-22s" % m for m in METRICS))]
    for meth, row in report["methods"].items():
        cells = []
        for m in METRICS:
            b = row[m]
            mark = " +" if b.get("better_than_bm25") else (" -" if b.get("worse_than_bm25") else "  ")
            cells.append("%.3f [%.3f,%.3f]%s" % (b["mean"], b["lo"], b["hi"], mark))
        lines.append("%-44s %s" % (meth, "  ".join("%-22s" % c for c in cells)))
    lines.append("+ better than bm25, - worse than bm25 (rule in report); unmarked = not distinguishable")
    for f in report["failures"]:
        lines.append("MODEL FAILED %s: %s" % (f["model"], f["error"]))
    return "\n".join(lines)


def main(argv=None, encoder_factory=embed.SentenceTransformerEncoder):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--units", required=True)
    ap.add_argument("--queries", required=True)
    ap.add_argument("--qrels", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--models")
    ap.add_argument("--exclude-types", nargs="*", default=[])
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--name", default="default")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        print("bakeoff NOT EXECUTED: bad arguments")
        return 2
    try:
        specs = json.loads(pathlib.Path(args.models).read_text(encoding="utf-8")) if args.models else []
        report = run(args.units, args.queries, args.qrels, args.out, specs, args.exclude_types, args.bootstrap,
                     args.seed, args.name, encoder_factory)
    except (OSError, embed.ContractError, json.JSONDecodeError) as exc:
        print("bakeoff NOT EXECUTED: %s" % exc)
        return 2
    print(format_table(report))
    if report["failures"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
