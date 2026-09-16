# -*- coding: utf-8 -*-
"""stats.py -- the intervals and sample sizes an agent-quality claim must carry. Standard library only.

    wilson(k, n)                         95% Wilson score interval for k successes of n
    bootstrap(values, stat=mean, ...)    percentile bootstrap interval, optionally resampling whole clusters
    cluster_proportion(clusters)         k/n with a cluster bootstrap over (k_i, n_i) groups (e.g. per reviewer)
    power_two_proportions(p1, p2)        n per arm to detect p1 vs p2 (two-sided alpha 0.05, power 0.80)

Why Wilson and not the normal approximation: at the n this repo's reviewer studies run (8 per arm), the
normal interval for 0 of 8 is [0, 0] -- a certainty measured from nothing. Wilson gives [0.00, 0.32].
"""
import math
import random
import statistics

Z95 = statistics.NormalDist().inv_cdf(0.975)


def wilson(k, n, z=Z95):
    """(low, high) for k of n. n = 0 raises: an interval over nothing is not an interval."""
    if n <= 0:
        raise ValueError("wilson: n must be positive (got %r); a proportion of nothing measures nothing" % n)
    if not 0 <= k <= n:
        raise ValueError("wilson: k=%r must lie in [0, n=%r]" % (k, n))
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def bootstrap(values, stat=statistics.fmean, reps=4000, seed=0, clusters=None, level=0.95):
    """Percentile bootstrap. With `clusters` (one label per value), whole clusters are resampled, so values
    that are not independent -- catches by one reviewer, labels in one slice -- are not counted as if they
    were."""
    if not values:
        raise ValueError("bootstrap over no values")
    rng = random.Random(seed)
    if clusters is None:
        groups = [[v] for v in values]
    else:
        if len(clusters) != len(values):
            raise ValueError("bootstrap: one cluster label per value")
        by = {}
        for value, label in zip(values, clusters):
            by.setdefault(label, []).append(value)
        groups = list(by.values())
    estimates = []
    for _ in range(reps):
        sample = [v for _ in groups for v in rng.choice(groups)]
        estimates.append(stat(sample))
    estimates.sort()
    lo = estimates[int((1 - level) / 2 * reps)]
    hi = estimates[min(reps - 1, int((1 + level) / 2 * reps))]
    return lo, hi


def cluster_proportion(clusters, reps=4000, seed=0):
    """clusters: [(k_i, n_i)]. Returns {"k", "n", "p", "cluster_interval"}; the interval resamples clusters."""
    if not clusters or sum(n for _, n in clusters) == 0:
        raise ValueError("cluster_proportion over no observations")
    values, labels = [], []
    for index, (k, n) in enumerate(clusters):
        values.extend([1] * k + [0] * (n - k))
        labels.extend([index] * n)
    k, n = sum(values), len(values)
    return {"k": k, "n": n, "p": k / n,
            "cluster_interval": list(bootstrap(values, reps=reps, seed=seed, clusters=labels))}


def power_two_proportions(p1, p2, alpha=0.05, power=0.80):
    """Per-arm n for a two-sided two-proportion z test to detect p1 against p2. The standard formula:
    n = (z_{1-a/2} sqrt(2 pbar qbar) + z_{power} sqrt(p1 q1 + p2 q2))^2 / (p1 - p2)^2, rounded up."""
    if p1 == p2:
        raise ValueError("power_two_proportions: p1 == p2 has no effect to detect")
    za = statistics.NormalDist().inv_cdf(1 - alpha / 2)
    zb = statistics.NormalDist().inv_cdf(power)
    pbar = (p1 + p2) / 2
    n = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2 / (p1 - p2) ** 2
    return math.ceil(n)


def overlap(a, b):
    return a[0] <= b[1] and b[0] <= a[1]
