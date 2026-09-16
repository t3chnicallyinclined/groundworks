# -*- coding: utf-8 -*-
"""evals -- how stochastic, agent-produced results are measured and remembered (docs/TESTING.md s.7).

    stats.py      Wilson and bootstrap intervals, cluster bootstrap, the two-proportion sample size
    record.py     a result -> one append-only history record, checked against its pre-registration
    prereg/       the hypothesis, arms, metric, unit of analysis and minimum n, written BEFORE the run
    results/      the raw counts a run produced (input to record.py)
    history/      one JSONL file per eval; every run, hash-chained so an edited line breaks the chain

Kept apart from the deterministic tests on purpose: a validation pass is pass/fail and can never be stored
here as a quality score, and an estimate here is never read as pass/fail. Human-owned (METHOD s.15): the
session that ran an experiment does not write history; record.py does, and stamps its own hash.
Nothing under xscholar/ imports this package. Standard library only.
"""
