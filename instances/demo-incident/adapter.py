# -*- coding: utf-8 -*-
"""adapter.py -- the demo instance's intake adapter and its walkthrough scenario.

SYNTHETIC DATA. No real service, host, person or customer. The corpus is generated here so the
demo runs anywhere with nothing to download.

Contract (docs/MODULES.md): `Adapter().units()` yields units with exact locators. Nothing here
interprets anything; the labels come later, from agents (or from labeler_stub.py in the demo).
"""
import random

WINDOW_START = "2026-03-04T11:30:00Z"


def _ts(minute, second=0):
    return "2026-03-04T11:%02d:%02dZ" % (minute, second)


def _lines():
    rnd = random.Random(20260304)
    hosts = ["api-1", "api-2", "api-3"]
    rows = []
    n = 0
    # quiet period before the window (out_of_window)
    for minute in range(20, 30):
        n += 1
        rows.append(("api-1", n, _ts(minute), "INFO", "health check ok", "out_of_window"))
    # the deploy
    n += 1; rows.append(("api-1", n, _ts(31, 5), "INFO", "deploy v2.14.0 started (retry policy: max_attempts 3 -> 8)", "in_window"))
    n += 1; rows.append(("api-1", n, _ts(31, 40), "INFO", "deploy v2.14.0 complete", "in_window"))
    # rising retries then pool exhaustion
    for minute in range(33, 42):
        for host in hosts:
            n += 1
            attempt = min(8, 2 + (minute - 33))
            rows.append((host, n, _ts(minute, rnd.randint(0, 59)), "WARN",
                         "retrying upstream call to inventory (attempt %d of 8)" % attempt, "in_window"))
    for minute in range(40, 46):
        for host in hosts:
            n += 1
            rows.append((host, n, _ts(minute, rnd.randint(0, 59)), "ERROR",
                         "pool exhausted: waited 30000ms for connection (pool max 50)", "in_window"))
    # red herring: a DNS warning that turns out to be routine
    n += 1; rows.append(("api-2", n, _ts(41, 12), "WARN", "dns resolver cache refresh took 900ms", "in_window"))
    # recovery
    n += 1; rows.append(("api-1", n, _ts(47, 3), "INFO", "rollback to v2.13.9 complete", "in_window"))
    for minute in range(48, 52):
        n += 1
        rows.append(("api-1", n, _ts(minute), "INFO", "health check ok", "in_window"))
    return rows


class Adapter:
    def units(self):
        for host, line, ts, level, text, status in _lines():
            yield {"id": "log_%04d" % line, "type": "log_line",
                   "locator": {"host": host, "file": "app.log", "line": line, "ts": ts},
                   "text": text, "status": status, "grade": "original_log",
                   "meta": {"level": level, "service": "checkout"}}
        yield {"id": "chg_0001", "type": "change",
               "locator": {"repo": "checkout", "commit": "a1b2c3d", "ts": _ts(31, 5)},
               "text": "config: HTTP client retry max_attempts 3 -> 8; backoff unchanged",
               "status": "in_window", "grade": "original_log", "meta": {"author_role": "deployer"}}
        yield {"id": "met_0001", "type": "metric_point",
               "locator": {"series": "db.pool.in_use", "ts": _ts(40)},
               "text": "db.pool.in_use = 50 (max 50) at 11:40", "status": "in_window", "grade": "exported_metric"}
        yield {"id": "met_0002", "type": "metric_point",
               "locator": {"series": "db.query.p99_ms", "ts": _ts(40)},
               "text": "db.query.p99_ms = 41 at 11:40 (baseline 38)", "status": "in_window", "grade": "exported_metric"}
        # something the adapter excludes and logs: a duplicate export of the same log
        yield {"id": "log_dup_0001", "type": "log_line", "exclude": "duplicate export of app.log lines 1-40"}


def scenario(writer, report):
    """The walkthrough: what an agent would do, with the gate answering each step.

    `report(step, outcome)` records each line for the runner. Every refusal here is the point."""
    from xscholar.pipeline.write import GateRefusal

    writer.propose("pool_exhausted", "The database connection pool was exhausted from 11:40",
                   reasoning_type="direct_quote")
    try:
        writer.confirm("pool_exhausted")
        report("confirm pool_exhausted with no evidence", "ACCEPTED (this would be a bug)")
    except GateRefusal as refusal:
        report("confirm pool_exhausted with no evidence", "REFUSED: " + refusal.missing)
    try:
        writer.confirm("pool_exhausted", ["log_0001"])   # true text, but out of the window
        report("confirm with an out-of-window line", "ACCEPTED (this would be a bug)")
    except GateRefusal as refusal:
        report("confirm with an out-of-window line", "REFUSED: " + refusal.missing)
    writer.confirm("pool_exhausted", ["log_0043", "met_0001"])
    report("confirm with an in-window error line and the pool metric", "ACCEPTED")

    writer.propose("retries_raised", "The deploy raised retry attempts from 3 to 8", reasoning_type="direct_quote")
    writer.confirm("retries_raised", ["chg_0001", "log_0011"])
    report("confirm retries_raised on the change record and the deploy line", "ACCEPTED")

    writer.rule_out("dns_failure", "DNS resolution failed during the incident",
                    tried="searched every in-window line for resolver errors",
                    evidence="one 900ms cache refresh warning (log_0067); no failures; p99 query time flat (met_0002)")
    report("rule_out dns_failure", "RECORDED as an eliminated claim (never gated)")

    writer.support("pool_exhausted", "rc_pool_exhaustion")
    writer.support("retries_raised", "rc_pool_exhaustion")
    try:
        writer.mark_ready("rc_pool_exhaustion")
        report("mark rc_pool_exhaustion ready before any attack", "ACCEPTED (this would be a bug)")
    except GateRefusal as refusal:
        report("mark rc_pool_exhaustion ready before any attack", "REFUSED: " + refusal.missing)
    cid = writer.critique("rc_pool_exhaustion", "a slow query, not retry volume, could have filled the pool",
                          "damaging", lens="lens_skeptic")
    try:
        writer.mark_ready("rc_pool_exhaustion")
        report("mark ready with a damaging head unanswered", "ACCEPTED (this would be a bug)")
    except GateRefusal as refusal:
        report("mark ready with a damaging head unanswered", "REFUSED: " + refusal.missing)
    writer.answer(cid, "Query p99 stayed at 41ms against a 38ms baseline (met_0002); the pool filled from "
                       "retry volume, not slow queries.")
    writer.mark_ready("rc_pool_exhaustion")
    report("answer the head, then mark ready", "ACCEPTED: rc_pool_exhaustion is ready")
