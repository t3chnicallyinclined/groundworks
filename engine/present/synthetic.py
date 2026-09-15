# -*- coding: utf-8 -*-
"""synthetic.py -- invented rows and policies for tests and the demo. No real matter data.

INCIDENT: a non-legal incident postmortem (log-line locators, root-cause claims).
LEGAL:    a legal-shaped instance with invented parties, pages and a fictional authority.
REVIEW:   a bigger non-legal design review, shaped like the first real instance: long row texts and
          a wall of unanswered high-impact critiques on a few targets. It is the dataset that
          exercises v1.1 -- critique collapse, the check_first_max overflow, the spot-check sample,
          an established verdict and a multi-word negation.

All include the awkward cases: a pipe and a backslash in text, a newline, a curly-apostrophe
contraction, dates and times, an eliminated claim another row links to, a superseded claim, an
unanswered high-impact critique and a value no policy class covers (unclassified).
"""
import copy

INCIDENT_POLICY = {
    "needs_check": {"status": ["unverified", "inferred", "proposed", "stale"],
                    "grade": ["agent", "recollection"],
                    "verdict": ["refutes"]},
    "eliminated": {"status": ["eliminated"]},
    "superseded": {"status": ["superseded"]},
    "established": {"status": ["confirmed", "verified", "answered", "raw"],
                    "grade": ["raw_log", "timestamped_log", "official"]},
    "high_impact": {"verdict": ["refutes"]},
    "locator_patterns": [r"\b[\w-]+:[\w.-]+:\d+\b", r"\b[\w-]+\.log:\d+(?:-\d+)?\b", r"\bINC-\d+\b", r"\bRB-\d+\b"],
    "negations": ["missing", "absent"],
    "why_labels": {"status:inferred": "inferred from other facts", "grade:agent": "agent grade (a model wrote it)",
                   "grade:recollection": "someone's memory", "status:stale": "source changed since read",
                   "unanswered_high_impact": "refuting critique still open"},
}

INCIDENT = {
    "title": "Checkout API timeouts: root cause review",
    "question": "Why did the checkout API time out on 2026-03-04?",
    "groups": ["Timeline", "Root cause", "Critiques", "Evidence"],
    "rows": [
        {"id": "u1", "group": "Evidence", "kind": "unit", "text": "10:14:07Z pool exhausted: 50/50 connections in use, 312 waiters",
         "status": "raw", "grade": "raw_log", "verdict": None, "answered": None, "locators": ["api-3:app.log:1207"],
         "lens": None, "reasoning_type": None, "direction": "for", "links": [], "meta": {}},
        {"id": "u2", "group": "Evidence", "kind": "unit", "text": "10:14:08Z retry storm: client retries tripled within 30s",
         "status": "raw", "grade": "raw_log", "verdict": None, "answered": None, "locators": ["api-3:app.log:1208"],
         "lens": None, "reasoning_type": None, "direction": "for", "links": [], "meta": {}},
        {"id": "u3", "group": "Evidence", "kind": "unit", "text": "I don't think the deploy at 09:58 touched the pool config",
         "status": "unverified", "grade": "recollection", "verdict": None, "answered": None, "locators": ["chat:incident:42"],
         "lens": None, "reasoning_type": None, "direction": "against", "links": [], "meta": {}},
        {"id": "c1", "group": "Timeline", "kind": "claim",
         "text": "The retry budget change deployed at 09:58, 16 minutes before the first alert",
         "status": "confirmed", "grade": "timestamped_log", "verdict": None, "answered": None, "locators": ["deploy.log:88"],
         "lens": None, "reasoning_type": "observed", "direction": "for", "links": ["u2"], "meta": {}},
        {"id": "c2", "group": "Timeline", "kind": "claim", "text": "Connection pool size was not changed in the last 30 days",
         "status": "inferred", "grade": "agent", "verdict": None, "answered": None, "locators": ["git:config/pool.yaml@a1b2c3"],
         "lens": None, "reasoning_type": "inferred", "direction": "for", "links": [], "meta": {}},
        {"id": "c3", "group": "Root cause", "kind": "claim", "text": "DNS resolution failed for the database host",
         "status": "eliminated", "grade": None, "verdict": None, "answered": None, "locators": ["resolver.log:5501-5620"],
         "lens": None, "reasoning_type": None, "direction": None, "links": ["u3"],
         "meta": {"what_was_found": "every lookup in the window succeeded"}},
        {"id": "r1", "group": "Root cause", "kind": "conclusion",
         "text": "The pool (max 50) was too small for the tripled retry load; no single slow query caused it",
         "status": "proposed", "grade": "agent", "verdict": None, "answered": None,
         "locators": ["api-3:app.log:1207", "api-3:app.log:1208"], "lens": None, "reasoning_type": "inferred",
         "direction": "for", "links": ["c3", "u1", "u2"], "meta": {}},
        {"id": "k1", "group": "Critiques", "kind": "critique", "text": "The load test never reproduced exhaustion below 3x traffic",
         "status": None, "grade": None, "verdict": "refutes", "answered": False, "locators": ["loadtest.log:220"],
         "lens": "skeptic", "reasoning_type": None, "direction": "against", "links": ["r1"], "meta": {}},
        {"id": "k2", "group": "Critiques", "kind": "critique",
         "text": "Pool metrics are sampled every 60s, so a 5s spike could be missing from the graph",
         "status": "answered", "grade": None, "verdict": "weakens", "answered": True, "locators": [],
         "lens": "sre_oncall", "reasoning_type": None, "direction": "against", "links": ["r1"], "meta": {}},
        {"id": "a1", "group": "Evidence", "kind": "authority",
         "text": "Runbook RB-7: pool size must be at least 2x peak waiters | owner: db team, path C:\\runbooks\\db",
         "status": "verified", "grade": "official", "verdict": None, "answered": None, "locators": ["runbook:RB-7 s.2"],
         "lens": None, "reasoning_type": None, "direction": None, "links": [], "meta": {}},
        {"id": "c4", "group": "Timeline", "kind": "claim", "text": "Alert fired at 10:14:30Z\nPager acknowledged at 10:19 on INC-4411",
         "status": "confirmed", "grade": "timestamped_log", "verdict": None, "answered": None, "locators": ["pagerduty:INC-4411"],
         "lens": None, "reasoning_type": "observed", "direction": None, "links": [], "meta": {}},
        {"id": "c5", "group": "Timeline", "kind": "claim", "text": "Cache hit rate didn\u2019t drop before 10:14",
         "status": "stale", "grade": "raw_log", "verdict": None, "answered": None, "locators": ["grafana:cache/panel-3"],
         "lens": None, "reasoning_type": "observed", "direction": "for", "links": [], "meta": {}},
        {"id": "c6", "group": "Timeline", "kind": "claim", "text": "The first alert fired at 10:15",
         "status": "superseded", "grade": None, "verdict": None, "answered": None, "locators": ["chat:incident:7"],
         "lens": None, "reasoning_type": None, "direction": None, "links": ["c4"], "meta": {"replaced_by": "c4"}},
        {"id": "n1", "group": "Evidence", "kind": "citation", "text": "Postmortem draft cites api-3:app.log:1270 for the pool peak",
         "status": "draft", "grade": None, "verdict": None, "answered": None, "locators": ["draft.md:14"],
         "lens": None, "reasoning_type": None, "direction": None, "links": ["u1"], "meta": {}},
    ],
}

LEGAL_POLICY = {
    "needs_check": {"status": ["unverified", "inferred", "proposed", "open"], "grade": ["agent"],
                    "verdict": ["mismatch", "not_found", "misattributed", "interpolated", "uncheckable", "fatal"]},
    "eliminated": {"status": ["eliminated"]},
    "superseded": {"status": ["superseded"]},
    "established": {"status": ["confirmed", "verified", "in_record"], "grade": ["page_image", "official"]},
    "high_impact": {"verdict": ["fatal", "damaging"]},
    "locator_patterns": [r"\bTr\. \d+:\d+(?:-\d+)?", r"\b(?:Order|Brief|Ex\. \d+) p\. \d+", r"\b\d+ A\.D\.3d \d+(?:, \d+)?"],
    "negations": ["nobody"],
    "why_labels": {"verdict:mismatch": "quote differs from the page", "grade:agent": "agent grade",
                   "status:unverified": "page not read yet"},
}

LEGAL = {
    "title": "Point I: finding without record support (invented example)",
    "question": "Did the order rest on a travel-time finding outside the record?",
    "groups": ["Findings", "Arguments", "Attacks", "Authorities"],
    "rows": [
        {"id": "f1", "group": "Findings", "kind": "claim",
         "text": "Witness A testified that the school was not contacted before March 3, 2025",
         "status": "confirmed", "grade": "page_image", "verdict": None, "answered": None, "locators": ["Tr. 45:12-19"],
         "lens": None, "reasoning_type": None, "direction": "for", "links": ["u1"], "meta": {}},
        {"id": "f2", "group": "Findings", "kind": "claim", "text": "The order states the move would add 40 minutes of travel",
         "status": "unverified", "grade": "agent", "verdict": None, "answered": None, "locators": ["Order p. 7"],
         "lens": None, "reasoning_type": None, "direction": "for", "links": [], "meta": {}},
        {"id": "f3", "group": "Findings", "kind": "claim", "text": "Party B never received the notice",
         "status": "eliminated", "grade": None, "verdict": None, "answered": None, "locators": ["Ex. 4 p. 2"],
         "lens": None, "reasoning_type": None, "direction": "for", "links": [], "meta": {}},
        {"id": "f4", "group": "Findings", "kind": "claim", "text": "Witness A said the school was told in April",
         "status": "superseded", "grade": None, "verdict": None, "answered": None, "locators": ["Tr. 44:2"],
         "lens": None, "reasoning_type": None, "direction": None, "links": ["f1"], "meta": {}},
        {"id": "g1", "group": "Arguments", "kind": "conclusion",
         "text": "The travel-time finding has no support in the record | see Order p. 7",
         "status": "proposed", "grade": None, "verdict": None, "answered": None, "locators": ["Order p. 7", "Tr. 88:3"],
         "lens": None, "reasoning_type": "inferred", "direction": "for", "links": ["f2", "f3"], "meta": {}},
        {"id": "x1", "group": "Attacks", "kind": "critique", "text": "The 40-minute figure appears in a received exhibit",
         "status": None, "grade": None, "verdict": "fatal", "answered": False, "locators": ["Ex. 9 p. 1"],
         "lens": "respondent", "reasoning_type": None, "direction": "against", "links": ["g1"], "meta": {}},
        {"id": "x2", "group": "Attacks", "kind": "critique", "text": "Harmless error:\nother factors suffice",
         "status": None, "grade": None, "verdict": "damaging", "answered": True, "locators": [],
         "lens": "panel", "reasoning_type": None, "direction": "against", "links": ["g1"], "meta": {}},
        {"id": "au1", "group": "Authorities", "kind": "authority",
         "text": "A finding must rest on evidence in the record (Example v Sample, 1 A.D.3d 100 [2d Dept 2003])",
         "status": "verified", "grade": "official", "verdict": None, "answered": None, "locators": ["1 A.D.3d 100, 102"],
         "lens": None, "reasoning_type": None, "direction": "for", "links": [], "meta": {}},
        {"id": "ci1", "group": "Authorities", "kind": "citation",
         "text": "Brief p. 12 quotes Tr. 45:14 as \u2018the school wasn\u2019t told\u2019",
         "status": None, "grade": None, "verdict": "MISMATCH", "answered": None, "locators": ["Brief p. 12", "Tr. 45:14"],
         "lens": None, "reasoning_type": None, "direction": None, "links": ["f1"], "meta": {}},
        {"id": "u1", "group": "Findings", "kind": "unit", "text": "Q. Did anyone call the school? A. No, nobody did.",
         "status": "in_record", "grade": "page_image", "verdict": None, "answered": None, "locators": ["Tr. 45:12"],
         "lens": None, "reasoning_type": None, "direction": "for", "links": [], "meta": {}},
    ],
}


REVIEW_POLICY = {
    "needs_check": {"status": ["open", "inferred", "draft", "stale"],
                    "grade": ["agent", "estimate"],
                    "verdict": ["mismatch", "not_found", "uncheckable"]},
    "eliminated": {"status": ["ruled_out"]},
    "superseded": {"status": ["superseded"]},
    "established": {"status": ["confirmed", "measured", "answered"],
                    "grade": ["primary", "benchmark"],
                    "verdict": ["verified"]},
    "high_impact": {"verdict": ["fatal", "damaging"]},
    "locator_patterns": [r"\badr-\d+:\d+\b", r"\bbench:run-\d+:\d+\b", r"\bRFC-\d+\b"],
    "negations": ["no longer", "missing"],
    "check_first_max": 6,
    "spot_check": {"count": 4, "seed": 20260915},
    # This instance's own promotion rules, re-checked over every rendered row (roadmap C18).
    "rules": [
        {"id": "established_needs_source_grade", "when": {"status": ["confirmed", "measured"]},
         "requires": {"grade": ["primary", "benchmark"]}},
        {"id": "established_needs_locator", "when": {"status": ["confirmed"]},
         "requires": {"locators_min": 1}},
        {"id": "answered_needs_answer_link", "when": {"answered": True},
         "requires": {"links_prefix": ["answer:"]}},
        {"id": "verified_citation_needs_locator", "when": {"kind": ["citation"], "verdict": ["verified"]},
         "requires": {"locators_min": 1}},
    ],
    "why_labels": {"status:open": "open decision, nothing checked yet",
                   "status:inferred": "inferred from other rows", "status:draft": "draft, not reviewed",
                   "status:stale": "source changed since read", "grade:agent": "agent grade (a model wrote it)",
                   "grade:estimate": "an estimate, not a measurement",
                   "verdict:mismatch": "quoted figure differs from the source",
                   "unanswered_high_impact": "blocking objection still open"},
}

# (count, target, lens, verdict, head) -- each block collapses into one scan line in v1.1.
_REVIEW_ATTACKS = [
    (10, "d1", "security", "fatal",
     "the shared token is readable by every worker in the pool, so one compromised worker reads every "
     "tenant's queue; the design note does not say how a token is scoped, rotated or revoked once it "
     "has been handed out"),
    (6, "d1", "ops", "damaging",
     "the rollback path was never exercised under load, and the runbook still assumes the old single-writer "
     "topology, so an operator following it during an incident would take the cluster down a second time"),
    (5, "d2", "cost", "damaging",
     "the per-request cost estimate counts only compute and leaves out cross-zone egress, which on the "
     "measured traffic shape is the larger half of the bill and grows with every added replica"),
    (3, "d3", "security", "fatal",
     "audit records are written on the same path they describe, so a failure that loses the write also "
     "loses its own evidence, and nothing in the design detects that the record is missing"),
]

_REVIEW_EVIDENCE = [
    ("measured", "benchmark", "bench:run-7:114",
     "Run 7 held 4,200 requests per second for 30 minutes at p99 118ms, with the connection pool at 60/64 and "
     "no errors; the same shape at 5,000 requests per second failed the p99 budget on every repeat"),
    ("measured", "benchmark", "bench:run-8:211",
     "Run 8 repeated run 7 with the cache disabled: throughput fell to 1,900 requests per second and p99 rose to "
     "540ms, which is the number the cost estimate should use for a cold region"),
    ("confirmed", "primary", "adr-12:3",
     "ADR-12 records the decision to keep one writer per shard, and the queue depth alarm is no longer wired to "
     "the pager after the 2026-02-11 alerting change, so nobody is paged when a shard falls behind"),
    ("confirmed", "primary", "adr-14:2",
     "ADR-14 fixes the retention window at 30 days for audit records and 7 days for request traces | owner: "
     "platform team, archive path C:\\archive\\traces"),
    ("measured", "benchmark", "bench:run-9:40",
     "Run 9 measured cross-zone egress at 1.8 TB per day under the run 7 traffic shape, which is 41% of the "
     "projected monthly bill and is not in the current estimate"),
    ("confirmed", "primary", "RFC-221",
     "RFC-221 requires that a token be scoped to one tenant and rotated every 24 hours; the draft design hands "
     "one token to the whole worker pool"),
    ("measured", "benchmark", "bench:run-10:17",
     "Run 10 killed the primary writer mid-batch: recovery took 96 seconds and replayed 3 batches, two of which "
     "were already durable, so the replay is not idempotent"),
    ("confirmed", "primary", "adr-12:9",
     "ADR-12 section 9 states that the rollback plan must be rehearsed once per quarter; the last rehearsal "
     "recorded was 2025-11-04, before the topology changed"),
]


def _review_doc():
    rows = [
        {"id": "d1", "group": "Decisions", "kind": "conclusion",
         "text": "Decision: give the worker pool one shared credential and scope access at the queue instead of at "
                 "the worker, because per-worker credentials were judged too slow to rotate at this fleet size",
         "status": "open", "grade": None, "verdict": None, "answered": None,
         "locators": ["adr-12:3"], "lens": None, "reasoning_type": "inferred", "direction": "for",
         "links": ["e1"], "meta": {}},
        {"id": "d2", "group": "Decisions", "kind": "conclusion",
         "text": "Decision: run three replicas per region rather than two, on the estimate that the added compute "
                 "cost is under 8% of the monthly bill and buys a full zone of headroom",
         "status": "open", "grade": "estimate", "verdict": None, "answered": None,
         "locators": ["bench:run-9:40"], "lens": None, "reasoning_type": "inferred", "direction": "for",
         "links": ["e5"], "meta": {}},
        {"id": "d3", "group": "Decisions", "kind": "conclusion",
         "text": "Decision: write audit records through the same queue as request traces, keeping one delivery path "
                 "to operate; the alternative of a second path was dropped as duplicated work",
         "status": "open", "grade": None, "verdict": None, "answered": None,
         "locators": ["adr-14:2"], "lens": None, "reasoning_type": "inferred", "direction": "for",
         "links": ["x0", "e8"], "meta": {}},
    ]
    n = 0
    for count, target, lens, verdict, head in _REVIEW_ATTACKS:
        for k in range(count):
            rows.append({
                "id": "a%d" % n, "group": "Attacks", "kind": "critique",
                "text": "On %s, %s (case %d of %d raised in review)" % (target, head, k + 1, count),
                "status": None, "grade": None, "verdict": verdict, "answered": False,
                "locators": ["adr-12:%d" % (10 + n)], "lens": lens, "reasoning_type": None,
                "direction": "against", "links": [target], "meta": {}})
            n += 1
    rows += [
        {"id": "a_ok1", "group": "Attacks", "kind": "critique",
         "text": "On d2, the replica count was called wasteful; the owner answered with run 9, which shows the third "
                 "replica absorbing a zone failure without breaching the p99 budget",
         "status": "answered", "grade": None, "verdict": "survivable", "answered": True,
         "locators": ["bench:run-9:40"], "lens": "cost", "reasoning_type": None, "direction": "against",
         "links": ["d2"], "meta": {}},
        {"id": "a_ok2", "group": "Attacks", "kind": "critique",
         "text": "On d1, the rotation window was called too long; the owner answered that RFC-221 allows 24 hours and "
                 "the fleet rotates in 6",
         "status": "answered", "grade": None, "verdict": "survivable", "answered": True,
         "locators": ["RFC-221"], "lens": "security", "reasoning_type": None, "direction": "against",
         "links": ["d1"], "meta": {}},
    ]
    for i, (status, grade, locator, text) in enumerate(_REVIEW_EVIDENCE, 1):
        rows.append({"id": "e%d" % i, "group": "Evidence", "kind": "unit", "text": text,
                     "status": status, "grade": grade, "verdict": None, "answered": None,
                     "locators": [locator], "lens": None, "reasoning_type": "observed", "direction": "for",
                     "links": [], "meta": {}})
    rows += [
        {"id": "e9", "group": "Evidence", "kind": "unit",
         "text": "Operator note, 2026-03-02: the standby region didn\u2019t take traffic during the drill\nthe failover "
                 "script exited 0 without moving anything",
         "status": "confirmed", "grade": "primary", "verdict": None, "answered": None,
         "locators": ["bench:run-10:17"], "lens": None, "reasoning_type": "observed", "direction": "against",
         "links": [], "meta": {}},
        {"id": "cit1", "group": "Citations", "kind": "citation",
         "text": "The design note quotes run 7 as 4,200 requests per second at p99 118ms, which matches the recorded "
                 "run exactly",
         "status": None, "grade": None, "verdict": "verified", "answered": None,
         "locators": ["bench:run-7:114"], "lens": None, "reasoning_type": None, "direction": None,
         "links": ["e1"], "meta": {}},
        {"id": "cit2", "group": "Citations", "kind": "citation",
         "text": "The cost section quotes egress as 0.9 TB per day, citing run 9, which recorded 1.8 TB per day",
         "status": None, "grade": None, "verdict": "MISMATCH", "answered": None,
         "locators": ["bench:run-9:40"], "lens": None, "reasoning_type": None, "direction": None,
         "links": ["e5"], "meta": {}},
        {"id": "cit3", "group": "Citations", "kind": "citation",
         "text": "A slide deck cites adr-12:3 for the rotation interval, but no interval appears on that page",
         "status": None, "grade": None, "verdict": None, "answered": None,
         "locators": ["adr-12:3"], "lens": None, "reasoning_type": None, "direction": None,
         "links": ["e3"], "meta": {}},
        {"id": "x0", "group": "Dropped", "kind": "claim",
         "text": "A second delivery path for audit records would double the operational surface and was ruled out on "
                 "cost grounds",
         "status": "ruled_out", "grade": None, "verdict": None, "answered": None,
         "locators": ["adr-14:2"], "lens": None, "reasoning_type": None, "direction": None,
         "links": [], "meta": {"what_was_found": "the cost figure it rested on came from the estimate, not run 9"}},
        {"id": "x1", "group": "Dropped", "kind": "claim",
         "text": "Egress was estimated at 0.9 TB per day",
         "status": "superseded", "grade": None, "verdict": None, "answered": None,
         "locators": ["bench:run-9:40"], "lens": None, "reasoning_type": None, "direction": None,
         "links": ["e5"], "meta": {"replaced_by": "e5"}},
    ]
    return {"title": "Payments rewrite: design review", "question": "Is this design ready to build?",
            "groups": ["Decisions", "Attacks", "Evidence", "Citations", "Dropped"], "rows": rows}


REVIEW = _review_doc()


def incident():
    return copy.deepcopy(INCIDENT), copy.deepcopy(INCIDENT_POLICY)


def review():
    return copy.deepcopy(REVIEW), copy.deepcopy(REVIEW_POLICY)


def legal():
    return copy.deepcopy(LEGAL), copy.deepcopy(LEGAL_POLICY)
