# -*- coding: utf-8 -*-
"""labeler_stub.py -- a stand-in for the reading agents, so the demo runs with no model calls.

THIS IS NOT THE METHOD. Real instances have agents read every unit with the labelling guide and
write labels with verbatim quotes; the validator then checks them. This stub applies keyword rules
to show the pipeline shape. It obeys the same contract: one label per unit, a verbatim quote for
tier 2 and above, and vocabulary from the instance config only.
"""

RULES = [
    # (phrase that must appear verbatim, tier, direction, issues, targets, role)
    ("pool exhausted", 3, "for_goal", ["saturation", "capacity"], ["rc_pool_exhaustion"], "error"),
    ("retry max_attempts 3 -> 8", 3, "for_goal", ["config", "deploy"], ["rc_pool_exhaustion", "rc_bad_deploy"], "change"),
    ("retry policy: max_attempts 3 -> 8", 3, "for_goal", ["config", "deploy"], ["rc_bad_deploy"], "change"),
    ("db.pool.in_use = 50", 3, "for_goal", ["saturation"], ["rc_pool_exhaustion"], "metric"),
    ("retrying upstream call", 2, "for_goal", ["dependency"], ["rc_upstream_timeout", "rc_pool_exhaustion"], "warning"),
    ("dns resolver cache refresh", 2, "against_goal", ["dependency", "monitoring"], ["rc_upstream_timeout"], "warning"),
    ("db.query.p99_ms", 2, "against_goal", ["monitoring"], ["rc_pool_exhaustion"], "metric"),
    ("rollback to v2.13.9", 2, "mixed", ["deploy"], ["rc_bad_deploy"], "change"),
    ("deploy v2.14.0 complete", 1, "neutral", [], [], "change"),
]


def label(unit):
    text = unit["text"]
    for phrase, tier, direction, issues, targets, role in RULES:
        if phrase in text:
            return {"id": unit["id"], "tier": tier, "dir": direction, "issues": issues, "targets": targets,
                    "role": role, "reason": "matched rule %r" % phrase,
                    # a phrase under three words is quoted with its whole line: the validator refuses a short
                    # fragment of a longer unit (labels.quote_min_words), which anchors no reason
                    "quote": phrase if len(phrase.split()) >= 3 else text}
    return {"id": unit["id"], "tier": 0, "dir": None, "issues": [], "targets": [], "role": "note"}
