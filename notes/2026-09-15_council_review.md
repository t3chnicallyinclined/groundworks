# 2026-09-15: review of Council of High Intelligence, and the panel module

Generic; no matter data.

## What was reviewed
`0xNyk/council-of-high-intelligence`, MIT, commit `94f9a90`, read in full (README, SKILL.md, agent files, routing config, roster validator, changelog). A `/council` skill: 2 to 18 historical-figure personas answer one question in rounds (restate, blind analysis, anonymized cross-examination, enforcement scan, final stance), a weighted vote, and a chairman's verdict with minority report.

## How it relates to the Scholar
Different job. The council deliberates over one question with no corpus and no persistent state; its checks are instructions to the coordinator model, and FACT labels are self-assigned. The Scholar reads a corpus into a gated graph. The council is further along on one thing the Scholar was thin on: **how several model seats disagree without converging socially.** That belongs in L6 roles.

## Decision (owner, 2026-09-15)
- **Adopted now (module `engine/panel`):** 1 blind then anonymized; 2 anti-conformity; 3 locked before answers; 4 stance line never inferred; 5 dissent quota, novelty, agreement check.
- **Roadmap:** 6 distinct reasoning method per seat, validated; 7 lens charters with sees and misses plus counterweight lenses; 8 drift guard between config, generated skills and prose; 9 enforcement counts in every stage's report.
- **Not adopted:** personas, deciding by vote as truth, cross-provider seats where the instance's privacy rules forbid them, self-labeled facts.

## The design choice that matters
The council enforces its protocol by asking the coordinator to check itself. That is a check that can pass without doing its work. Here the same five rules are read from machine lines by a program that refuses to tally, and two of them gained the Scholar's signature move: **verbatim anchoring** (a changed stance must quote the seat's own round-1 words) and **hashing** (panel locked before answers; round-1 answers hashed at anonymization).

## Measured
`test_panel.py`: each check tested both ways (reject, then accept once fixed). Result recorded in DECISIONS.md.

## Open
- Does anonymization change outcomes in our panels? Test: the same target run with and without masking, compare stance changes and objection diversity. Until then it is adopted on the source's cited research, which has not been read here.
- Objection overlap is judged by category only; substance overlap needs a judge or a later embedding check.
