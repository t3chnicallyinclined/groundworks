# 2026-09-15: the demo instance labelled by agents, and the first agreement number

Synthetic incident-postmortem instance (`instances/demo-incident`), 66 units in 3 slices. Two reading agents (a mid-tier model), one slice of 63 log lines and one agent for the 3 change and metric units, each given the instance config's vocabulary and the labelling contract, and told to run the validator until it passes.

## Validation
- 66 of 66 units labelled exactly once; every tier-2+ quote found word for word. Both agents passed on the **first** validator run.
- The engine then built the graph from the agents' labels with the same runner (`--labels`), gates probed both ways, walkthrough refused and accepted as before.

## Agreement: agents vs the rule-based stub (`xscholar labels agreement`)

| Slice (n) | tier exact | tier within one | tier kappa | dir exact | dir kappa | issues Jaccard | targets Jaccard | role |
|---|---|---|---|---|---|---|---|---|
| log lines (63) | 0.95 | 1.00 | 0.93 | 0.95 | 0.88 | 0.75 | 0.96 | 1.00 |
| change (1) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| metrics (2) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.25 | 0.50 | 1.00 |

Relevance split (one labeller saw tier 2+, the other did not): **2 of 63** log lines. Both are judgment calls, not errors: the second deploy line (context vs relevant) and the DNS cache warning (the stub treated it as a competing lead; the agent, reading only the unit's own text, called it context and said why).

## What this is and is not
- It **is** the harness working end to end on real agent output: coverage, verbatim quotes, and a chance-corrected agreement number with a named adjudication list.
- It is **not** a label-accuracy measurement. The second "labeller" is a keyword stub written by the same person who wrote the corpus; agreement with it measures how far the agents reproduce the intended reading, not how right either is. The real measurement is two independent agents on the same slice with a human adjudicating the splits, and that is still owed on a real instance.
- Issues overlap (0.75) is the weakest field: the stub attaches fixed issue lists per keyword, the agents chose fewer, more specific issues. That is a vocabulary-guide question, not a correctness one.

## Lesson for the tuning guide
Agents pass the verbatim validator easily when told the rule up front, so validation cost is low. The expensive part is adjudicating the relevance splits, and those are few (about 3% here). Budget for adjudication, not for validation.
