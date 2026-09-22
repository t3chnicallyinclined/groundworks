# -*- coding: utf-8 -*-
"""retrieve.py -- the one keyword ranker (Okapi BM25) every reader of a corpus shares.

Moved here from embed/bakeoff.py (2026-09-22) so that `ask` and the bake-off rank with the same code:
the bake-off measured BM25 beating both local embedding models and the hybrid on this engine's first
instance (nDCG@10 .470 vs .255/.183/.376), and Recall@20 of only 0.54 -- which is why `ask` retrieves
WIDE and prints what it did not read rather than trusting a short list.

    index = BM25([tokenize(u["text"]) for u in units])
    order = index.rank(tokenize(question), top=200)     # row indexes, best first; ties by index

Standard library only. Deterministic: same corpus and query, same order.
"""
import math
import re

TOKEN = re.compile(r"\w+", re.UNICODE)
POOL = 1000          # default depth of a ranked list


def tokenize(text):
    return TOKEN.findall((text or "").lower())


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
        """Row indexes with a non-zero score, best first. A query no document matches ranks NOTHING:
        an empty list is the answer, never the first `top` rows of the corpus."""
        acc = self.scores(query_tokens)
        return [i for i, _ in sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]

    def ranked(self, query_tokens, top=POOL):
        """(index, score) pairs, best first."""
        acc = self.scores(query_tokens)
        return sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
