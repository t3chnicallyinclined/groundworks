# -*- coding: utf-8 -*-
"""adapter.py -- the demo instance's intake adapter and its walkthrough scenario.

SYNTHETIC DATA. No real service, host, person or customer. The corpus is generated here so the
demo runs anywhere with nothing to download.

Contract (docs/MODULES.md): `Adapter().documents()` yields the originals -- the exported log file, the
commit record, the metrics export -- as exact bytes; `Adapter().units()` yields units with exact locators
INTO those originals (a log line's `line` is its line number in app.log). Intake stores every original in
the instance's content-addressed store, so any unit can be traced back to the bytes it was cut from.
Nothing here interprets anything; the labels come later, from agents (or from labeler_stub.py in the demo).
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


CHANGE_TEXT = "config: HTTP client retry max_attempts 3 -> 8; backoff unchanged"
RETRIEVED = "2026-03-04T12:30:00Z"


def app_log():
    """The exported log file, one line per entry: line N of this file is unit log_N."""
    return "".join("%s %s %s %s\n" % (ts, host, level, text) for host, _, ts, level, text, _ in _lines()).encode("utf-8")


def commit_record():
    return ("commit a1b2c3d\nrepo: checkout\nauthor-role: deployer\ndate: %s\n\n    %s\n"
            % (_ts(31, 5), CHANGE_TEXT)).encode("utf-8")


def metrics_csv():
    return ("ts,series,value,max,baseline\n%s,db.pool.in_use,50,50,\n%s,db.query.p99_ms,41,,38\n"
            % (_ts(40), _ts(40))).encode("utf-8")


class Adapter:
    def documents(self):
        yield {"key": "app.log", "bytes": app_log(), "media_type": "text/plain", "capture": "raw",
               "source_uri": "synthetic://demo-incident/checkout/app.log", "retrieved_at": RETRIEVED,
               "original_name": "app.log"}
        yield {"key": "commit:a1b2c3d", "bytes": commit_record(), "media_type": "text/plain", "capture": "raw",
               "source_uri": "synthetic://demo-incident/checkout/commit/a1b2c3d", "retrieved_at": RETRIEVED,
               "original_name": "commit-a1b2c3d.txt"}
        yield {"key": "metrics.csv", "bytes": metrics_csv(), "media_type": "text/csv", "capture": "raw",
               "source_uri": "synthetic://demo-incident/metrics/export-1140.csv", "retrieved_at": RETRIEVED,
               "original_name": "metrics-export-1140.csv"}

    def units(self):
        for host, line, ts, level, text, status in _lines():
            yield {"id": "log_%04d" % line, "type": "log_line",
                   "locator": {"host": host, "file": "app.log", "line": line, "ts": ts},
                   "text": text, "status": status, "grade": "original_log", "document": "app.log",
                   "meta": {"level": level, "service": "checkout"}}
        yield {"id": "chg_0001", "type": "change",
               "locator": {"repo": "checkout", "commit": "a1b2c3d", "ts": _ts(31, 5)},
               "text": CHANGE_TEXT, "document": "commit:a1b2c3d",
               "status": "in_window", "grade": "original_log", "meta": {"author_role": "deployer"}}
        # A metric unit is a rendering of a row of the export, not a quotation of it: its original is the
        # export, and no span is declared for its type, so the validator counts its quotes as not checkable.
        yield {"id": "met_0001", "type": "metric_point",
               "locator": {"series": "db.pool.in_use", "ts": _ts(40)}, "document": "metrics.csv",
               "text": "db.pool.in_use = 50 (max 50) at 11:40", "status": "in_window", "grade": "exported_metric"}
        yield {"id": "met_0002", "type": "metric_point",
               "locator": {"series": "db.query.p99_ms", "ts": _ts(40)}, "document": "metrics.csv",
               "text": "db.query.p99_ms = 41 at 11:40 (baseline 38)", "status": "in_window", "grade": "exported_metric"}
        # something the adapter excludes and logs: a duplicate export of the same log
        yield {"id": "log_dup_0001", "type": "log_line", "exclude": "duplicate export of app.log lines 1-40"}


def scenario(writers, report):
    """The walkthrough: what agents would do, with the datastore answering each step.

    `writers` maps the launch identities reader, seat and coordinator to a Writer, each on its own
    provisioned credential (or all on root when the instance has no access: section).
    `report(who, step, outcome)` records each line for the runner. Every refusal here is the point:
    a GateRefusal is the gate; a PermissionDenied is the datastore's per-agent permissions."""
    from groundworks.pipeline.write import GateRefusal
    from groundworks.db import PermissionDenied

    reader, seat, coord = writers["reader"], writers["seat"], writers["coordinator"]

    def attempt(writer, step, fn, bug="ACCEPTED (this would be a bug)"):
        try:
            fn()
            report(writer.author, step, bug)
        except GateRefusal as refusal:
            report(writer.author, step, "REFUSED by gate: " + refusal.missing)
        except PermissionDenied as denied:
            report(writer.author, step, "DENIED by permissions: " + str(denied).split(":")[0])

    reader.propose("pool_exhausted", "The database connection pool was exhausted from 11:40",
                   reasoning_type="direct_quote")
    attempt(reader, "confirm pool_exhausted with no evidence", lambda: reader.confirm("pool_exhausted"))
    attempt(reader, "confirm with an out-of-window line", lambda: reader.confirm("pool_exhausted", ["log_0001"]))
    reader.confirm("pool_exhausted", ["log_0043", "met_0001"])
    report(reader.author, "confirm with an in-window error line and the pool metric", "ACCEPTED")

    reader.propose("retries_raised", "The deploy raised retry attempts from 3 to 8", reasoning_type="direct_quote")
    reader.confirm("retries_raised", ["chg_0001", "log_0011"])
    report(reader.author, "confirm retries_raised on the change record and the deploy line", "ACCEPTED")

    reader.rule_out("dns_failure", "DNS resolution failed during the incident",
                    tried="searched every in-window line for resolver errors",
                    evidence="one 900ms cache refresh warning (log_0067); no failures; p99 query time flat (met_0002)")
    report(reader.author, "rule_out dns_failure", "RECORDED as an eliminated claim (never gated)")

    reader.support("pool_exhausted", "rc_pool_exhaustion")
    reader.support("retries_raised", "rc_pool_exhaustion")
    report(reader.author, "support rc_pool_exhaustion with both facts", "ACCEPTED")

    attempt(reader, "critique rc_pool_exhaustion as a reader",
            lambda: reader.critique("rc_pool_exhaustion", "a reader trying to attack", "survivable", lens="lens_skeptic"))
    attempt(coord, "mark rc_pool_exhaustion ready before any attack", lambda: coord.mark_ready("rc_pool_exhaustion"))

    attempt(seat, "critique under another seat's lens (management)",
            lambda: seat.critique("rc_pool_exhaustion", "borrowed lens", "survivable", lens="management"))
    attempt(seat, "confirm a fact as a seat", lambda: seat.confirm("retries_raised"))
    cid = seat.critique("rc_pool_exhaustion", "a slow query, not retry volume, could have filled the pool", "damaging")
    report(seat.author, "critique rc_pool_exhaustion under its own lens", "ACCEPTED: %s" % cid)

    attempt(coord, "mark ready with a damaging head unanswered", lambda: coord.mark_ready("rc_pool_exhaustion"))
    coord.answer(cid, "Query p99 stayed at 41ms against a 38ms baseline (met_0002); the pool filled from "
                      "retry volume, not slow queries.")
    attempt(coord, "mark ready with nothing recording how the observation was captured",
            lambda: coord.mark_ready("rc_pool_exhaustion"))
    coord.relate("preserves", "log_0043", "rc_pool_exhaustion", how="alert")
    report(coord.author, "record that log_0043 captured the pool exhaustion at the time (how=alert)",
           "ACCEPTED")
    coord.mark_ready("rc_pool_exhaustion")
    report(coord.author, "answer the head and record preservation, then mark ready",
           "ACCEPTED: rc_pool_exhaustion is ready")
    attempt(coord, "delete the critique as coordinator", lambda: coord.db.sql("DELETE %s" % cid, expect=1),
            bug="ACCEPTED (this would be a bug: deletes are root-only, through the archive)")
