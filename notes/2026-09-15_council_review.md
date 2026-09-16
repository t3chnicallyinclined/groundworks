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

## Measured on instance 1 (2026-09-15; reported by that instance's session, no matter content)

**Setup**
- 6 panels of 3 seats: 3 adversary panels and 3 peer-bench panels.
- All seats used one model, with one shared evidence packet per panel.
- Round-1 prompts barred earlier reviews of the same targets.
- A 7th run repeated one adversary panel with round 2 unmasked.

**Tallies:** 3 consensus and 3 splits. Every split was 2-1 at medium confidence: majority weight 1.5 or 1.75 against a bar of 2.0001. The templates had copied a rounded consensus threshold (0.6667) from the protocol example. That example is now fixed, and a test pins the exact 2/3 bar. The rounding did not change these outcomes.

**Enforcement counts**

| Condition | Owed | Notes |
|---|---|---|
| agreement_check | 6 | All on consensus-bound panels. All met on the first re-prompt, with 4 to 6 objection lines each |
| missing_stance | 0 | |
| dissent_quota | 0 | |
| novelty_gate | 0 | |

The program also refused two coordinator mistakes:
- a panel file copied after answers existed (lock refused, 1)
- anonymization on an incomplete round 1 (6, caused by a coordinator parsing bug)

**Round 1 to round 2**
- 0 of 18 stances changed, so no FLAW lines were needed.
- Each panel produced 5 to 8 NEW lines.
- Counterfactuals changed no stance, but one surfaced a new, verifiable source item that both rounds had missed.

**Objection categories:** every panel used the same 4 of the 6 fixed categories. The category-level dissent quota therefore never fired, and overlap in substance was not measured.

**C10, masked vs unmasked** (one adversary panel, identical round-1 answers):
- identical stances
- 0 changes in both runs
- 4 categories in both
- 7 vs 8 NEW lines

No measurable effect, n=1.

**Lessons**
1. Three-seat panels at medium confidence split by design; expect splits to reach the human often.
2. A category-level dissent quota is too coarse when categories are fixed and seats share a model. Roadmap C15 adds a substance-level check.
3. The agreement check produced real value: a new, verifiable item, not only friction.
4. Zero stance changes in 18 could mean stable judgment or anchoring. Seats from different model families, or seats with different evidence packets, would separate the two, within the privacy rules.
5. Record one critique per seat for splits and consensus alike. Record nothing for measurement-only reruns, so seats are not duplicated.

## Open
- Does anonymization change outcomes in our panels? Test: the same target run with and without masking, compare stance changes and objection diversity. Until then it is adopted on the source's cited research, which has not been read here.
- Objection overlap is judged by category only; substance overlap needs a judge or a later embedding check.
