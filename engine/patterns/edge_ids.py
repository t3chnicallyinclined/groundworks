# -*- coding: utf-8 -*-
"""edge_ids.py -- deterministic edge ids, so loaders update edges IN PLACE.

WHY
---
Loaders used to delete their own edges and recreate them on every run. With
truth maintenance (demote when a citation is deleted) and the archive bin
(snapshot every delete) installed, that churn would demote facts mid-load and
flood the archive with copies of unchanged edges.

Measured on SurrealDB 3.1.4, 2026-09-15: RELATE with an explicit edge id that
already exists UPDATES that edge (no duplicate, no DELETE event, an UPDATE event).

PATTERN
-------
  1. every loader-written edge gets a stable id from (scope, edge, src, dst, k)
  2. every write stamps run=<token for this run>
  3. after the whole load succeeds, sweep: DELETE <edge> WHERE seed=<scope> AND run != <token>
     -- only edges that truly disappeared from the source are deleted (and archived)
  4. if any write in the run failed, DO NOT sweep: a failed write would otherwise
     lose the previous copy of that edge
"""
import hashlib
import time


def edge_id(scope, edge, src, dst, k=0):
    h = hashlib.sha1(("%s|%s|%s|%s|%d" % (scope, edge, src, dst, k)).encode("utf-8")).hexdigest()[:24]
    return "e%s_%s" % (str(scope).lower(), h)


def run_token():
    return "r%d" % time.time_ns()


class Keyer:
    """Stable ids that stay distinct when the same (edge, src, dst) repeats within one scope."""

    def __init__(self):
        self.seen = {}

    def __call__(self, scope, edge, src, dst):
        key = (edge, src, dst)
        k = self.seen.get(key, 0)
        self.seen[key] = k + 1
        return edge_id(scope, edge, src, dst, k)
