# 2026-09-16: marker wording (C20), measured on the demo instance

Generic; no matter content. Module: `xscholar/present`. Roadmap V2-7, following C17
(`notes/2026-09-15_reading_layer_evaluation.md`) and C18 (`notes/2026-09-15_inline_rule_recheck.md`).

## The answer first

**No. Rewording the flag did not move the status-upgrade catch rate out of noise.**
Control 0 of 8; C20 wording 2 of 8; C20 wording plus hand-checkable counts 2 of 8. Those four catches
come from **two reviewers out of eight**, both reading the same seeded copy, and one of the two reached
them by copying the whole flag section into his findings, which is also where the experiment's only 2
false alarms came from. Overall catch rate across all five error types was 23/40, 24/40 and 21/40 — flat.
**Neither
variant is adopted; no engine code was changed.**

The one thing that did separate the arms is upstream of reporting: **no control reviewer spent a lookup
on a relabelled row, and three of eight C20 reviewers did** (one of those three looked, saw the true
status, and still did not report it). If the wording does anything, it buys a *lookup*, not a *report*.
At n = 12 that is a hypothesis, not a result.

## What was tested

Material: the demo corpus `synthetic.REVIEW` ("Payments rewrite: design review", 43 rows, 5 groups),
rendered `speedread` with that corpus's own four declared `rules` (`xscholar/present/synthetic.py:145`).
Two seeded copies, `plant.py --per-type 2`, seeds 7 and 23: **10 planted errors each, 2 of every
mutation type, 20 planted errors in total**. The rule re-check's behaviour is identical in all three
arms — wording was the only variable.

**Arm A, control** — the shipped wording (`xscholar/present/render.py:57`, `:175`, `:216`):

```
## Fails its own rule (5)
- **RULE FAIL: established_needs_source_grade**: `d2` [**confirmed**/**estimate**] Decision: run three
  replicas … (**bench:run-9:40**) - rule established_needs_source_grade: status confirmed but grade estimate
```

**Arm B, C20 wording** — same rows, same order, same counts; the marker states the claim's label, what
its own evidence supports, and the one action that resolves it, all generated mechanically from the
rule's `when` and `requires`:

```
## Labels this document has not earned (5)
Nothing in the text below will contradict these labels - a wrong label reads exactly like a right one.
This section is the only place they surface. Each line says what the row is labelled, what its own
evidence supports, and the one action that settles it.
- LABEL SAYS: status `confirmed`. EVIDENCE SUPPORTS: less than that - its best evidence is grade
  `estimate`, and this instance counts only `primary`, `benchmark` as established evidence. DO THIS ONE
  THING: re-read the source and record a `primary`, `benchmark` grade, or put the status back below
  established. (rule established_needs_source_grade)
  **LABEL NOT EARNED - fix or downgrade it; see the top of this document**: `d2` [**confirmed**/…
```

Inline, everywhere else in the document, the marker is
`**LABEL NOT EARNED - fix or downgrade it; see the top of this document**` in place of
`**RULE FAIL: <rule id>**`.

**Arm C, C20 wording plus hand-checkable counts** — arm B, plus a block above every section giving
counts a reader can add up by hand: the sections reconciled against the total, the rule fails, how many
of the established rows fail their own rule, and, per group, a tally by status, by verdict and by
`answered` with ids named for any bucket of five or fewer.

```
## Counts you can check by hand
Every number here can be counted in the sections below. If one does not add up, a label has been changed.
- Sections add up: Check first 24 + More to check 4 + Eliminated 1 + Superseded 1 + Established 13 = 43 …
- Of the 13 rows this document calls established, 3 fail their own rule: `d3`, `a_ok1`, `a_ok2`.
- Group Decisions: 3 rows.  - by status: confirmed 2 (`d2`, `d3`); open 1 (`d1`)
- Group Attacks: 26 rows.   - answered 3 (`a3`, `a_ok1`, `a_ok2`); still open 23 - these two must add to 26
```

**Why each was expected to help.** Arm B answers the C17/C18 diagnosis directly: "Fails its own rule"
reads as housekeeping, and housekeeping is what a reader skips, so name the defect and the remedy. Arm C
adds the one affordance that had actually worked twice in the earlier rounds — a reviewer reconciling a
stated count against the rows shown.

## Deterministic results

Identical in both seeded copies (copy A / copy B):

| | A control | B C20 wording | C B + counts |
|---|---|---|---|
| Preservation, `speedread` | **100%, 7 of 7 checks ran** | **100%, 7 of 7** | **100%, 7 of 7** |
| Characters | 20,800 / 20,811 | 22,643 / 22,654 (**+8.9%**) | 23,739 / 23,750 (**+14.1%**) |
| Lines | 104 | 110 | 128 |
| Scan-layer lines | 59 | 65 | 83 |
| First scan line of `d2` / `d3` (the status upgrades) | 7 / 8 | 9 / 11 | 13 / 11 |

What the rule re-check flagged (the same in every arm — wording changes nothing mechanical):

| Mutation type | planted per copy | flagged | flagged **because of** the plant |
|---|---|---|---|
| status_upgrade | 2 | 2 | **2** |
| verdict_soften | 2 | 1 | **1** (the `answered` false → true flip; a softened verdict *value* is not flagged) |
| negation_drop | 2 | 1 (copy A) | 0 |
| locator_shift | 2 | 1 (copy A) | 0 |
| number_change | 2 | 0 | 0 |

The clean corpus already fails `answered_needs_answer_link` on `a_ok1` and `a_ok2` — 2 standing fails out
of 43 rows, which is why the copy-A negation_drop and locator_shift landed on already-flagged rows
without the flag having anything to do with the plant.

## Reviewer results (12 sessions, 4 per arm, 8 lookups each)

Two seeded copies × three arms × two reviewers. Every reviewer got the rendering, the 8-lookup budget and
nothing else; none saw the manifest, the plant count, the arm it was in, or this experiment.

| | A control | B C20 wording | C B + counts |
|---|---|---|---|
| Caught | 23 / 40 | 24 / 40 | 21 / 40 |
| Findings written | 23 | 26 | 21 |
| **False alarms** | **0** | **2** | **0** |
| of which: right row, wrong field | 0 | 2 | 0 |
| Lookups used (of 32) | 31 | 30 | 32 |

By error type (caught / planted, 8 per type per arm):

| Type | A control | B C20 | C B+counts |
|---|---|---|---|
| **status_upgrade** | **0/8** | **2/8** | **2/8** |
| **verdict_soften** | **4/8** | 3/8 | **0/8** |
| locator_shift | 8/8 | 7/8 | 8/8 |
| negation_drop | 5/8 | 6/8 | 5/8 |
| number_change | 6/8 | 6/8 | 6/8 |

By where the error landed: established 11/20, 12/20, 13/20; needs-check 12/20, 12/20, 8/20.

Reviewer-level, which is the honest unit here because a reviewer either engaged with the flag section or
did not: **0 of 4 control reviewers reported a status upgrade; 1 of 4 in arm B; 1 of 4 in arm C.** Both
were reading copy A. All four copy-B sessions in the C20 arms reported none.

Lookup behaviour, the one place the arms separate:

| | A control | B C20 | C B+counts |
|---|---|---|---|
| Sessions that spent a lookup on a status-upgraded row | **0 of 4** | 1 of 4 | 2 of 4 |
| Lookups spent on `d2`/`d3` | **0 of 31** | 1 of 30 | 3 of 32 |

## Findings

1. **The wording did not move the catch rate out of noise.** 0/8 → 2/8 → 2/8 on status upgrades, from two
   reviewers out of eight on one of the two copies. At reviewer level it is 0 of 4 against 1 of 4 and
   1 of 4. Overall catch rate 23 / 24 / 21 of 40. Nothing here is a result.

2. **Reviewers read a flagged row as the document's own correct self-audit, and deliberately left it
   out.** This is the strongest signal in the run. The *behaviour* is uniform — every arm missed most or
   all status upgrades — but the *stated reason* appears only in the two C20 arms, where seven of the
   eight reviewers volunteered it unprompted, in their own words: *"I treated those five as already-caught
   issues, not hidden errors to re-report"*; *"already disclosed by the document itself as known
   rule-fails, so I treated those five as given"*; *"legitimate self-flagged content rather than hidden
   errors, since the document already explains and justifies each one."* No control reviewer discussed the
   flag section at all. One arm-C reviewer spent a lookup on `d2`, saw the true status was `open`, and
   still did not report it, because *"it matched what the document's own disclosure section had already
   said — so it wasn't a new finding."* **The obstacle is not that the flag is unread. It is that a flag
   the document raises about itself does not count, to the reader, as an error the reader found — and the
   C20 wording, which explains and justifies each flag at length, gives that reading more to hold on to,
   not less.** CONFIRMED as behaviour; the causal half is INFERRED from 8 sessions' self-reports.

3. **C20's "one action" phrasing may push the wrong way, and it is the arm that bought false alarms.**
   "DO THIS ONE THING: re-read the source and record a grade" is an instruction to the author, not an
   assertion that the row is wrong; it fits the "already handled" reading of finding 2. Arm B's only two
   false alarms — both of them right-row/wrong-field, one pair of findings, not two — came from the one
   reviewer who did the opposite and transcribed the entire flagged section into findings: 2 of that
   section's 5 rows were planted status upgrades, 1 was the planted `answered` flip, and 2 were the
   corpus's standing fails, which he reported on the flag's word alone. **That reviewer supplies half of
   the whole experiment's status-upgrade catches, and he verified only one of the two.** Of the four
   status-upgrade catches across both C20 arms, three were preceded by a lookup on that row and one
   was not.

4. **Pre-computed counts removed the arithmetic that used to catch the relabel.** Arm C caught **0 of 8
   verdict softenings against the control's 4 of 8**, and its needs-check recall was 8/20 against 12/20.
   In the earlier rounds two reviewers in different arms found a relabelled row by reconciling a stated
   count against the rows shown. In arm C the reconciliation is done for them: four of the four arm-C
   reviewers checked the block, found it internally consistent — it always is, because it is derived from
   the same rows — and moved on. INFERRED, on 4 sessions: giving a reader a reconciled total is not the
   same affordance as giving him totals that he has to reconcile. C20's count clause needs a different
   design before it is worth re-testing.

5. **The rule re-check itself is unchanged and still does its mechanical job.** 2 of 2 status upgrades
   and the `answered` flip flagged in both copies, 100% preservation and all 7 checks running in all
   three arms. The gap between "flagged" and "reported" is the whole problem, and it is a reader
   problem, not a renderer problem.

## Caveats, and what is measured versus inferred

- **n = 12 reviewer sessions, 4 per arm, 2 per cell.** Better than the one-per-cell of the two earlier
  rounds, and still far too small. Differences of one or two reviewers are noise and are reported as
  noise.
- **One document.** `synthetic.REVIEW` is 43 rows; the documents behind the 0-of-9 baseline were 163 to
  234. A reviewer can read this one end to end, and every reviewer did. **The 0-of-9 baseline is
  therefore not directly comparable** — the comparison that counts is my own control arm, which
  re-established 0 of 8 on this document. Only 3 rows in this corpus can carry a status upgrade at all,
  which caps the design.
- **The two copies are not independent for status upgrades.** `plant.py`'s targeting is deterministic
  given the policy, so both seeds upgraded the same two rows (`d2`, `d3`). The seeds differ for the other
  four types.
- **Agents, one model** (Claude Sonnet subagents), not human readers. They are skim-verifiers.
- Reviewers were instructed to open nothing but their own rendering and `look.py`. Budget logs show
  30 to 32 of 32 lookups used per arm and no session reported more than 10 findings, which is consistent
  with compliance; it does not prove it. UNKNOWN.
- **A defect in my own harness, in the material the reviewers read.** The first render pass loaded the
  rows with the platform default encoding, so the curly apostrophe in row `e9` reached every reviewer's
  document as `didnâ€™t`. It is byte-identical in all three arms, `e9` was not a planted row in either
  copy, and no reviewer reported it — but seven of the twelve spent a lookup on `e9` (for an unrelated
  suspicion about a shared locator) and it cost them. The renderings kept in the workspace were
  regenerated correctly afterwards and the deterministic table above is measured on those; they differ
  from what the reviewers saw by two bytes in one row.
- False alarms are scored against planted errors only. Arm B's two are reports of the corpus's *standing*
  rule fails (`a_ok1`, `a_ok2` on `answered`) — real rule failures, not invented ones, but not errors a
  reviewer found either.

## What this changes

- **Nothing is adopted.** Neither the C20 marker wording nor the hand-checkable counts block goes into
  `xscholar/present`. `render.py`, `features.py` and `policy.py` are untouched; the variants live only in
  the experiment harness, which is not committed.
- **The C20 row of `docs/TUNING.md` is corrected.** The recommendation "say what the label claims, what
  the evidence supports and the single action that resolves it" was written from the earlier rounds as a
  reasonable inference. It has now been tried and did not carry. So has "give every section a count a
  reader can add up", which cost every verdict-soften catch in its arm.
- **The next hypothesis is about the reader's task, not the flag's prose.** The measured obstacle is that
  a self-raised flag is read as a disclosure and excluded from findings. Two things worth testing, in
  this order:
  1. Tell the reviewer, in the task, that the document's own flags are *claims to check*, not
     conclusions — and measure whether that alone closes the gap, with no change to the rendering.
     If it does, the wording was never the variable.
  2. Word the marker as an open question rather than a fix: *"this row's status may have been raised
     without evidence — the only way to know is to re-read the source"*, with no action clause. The
     lookup numbers (0 of 4 control sessions against 3 of 8 C20 sessions spending a lookup on a
     relabelled row) are the thin thread suggesting the marker can at least move attention.
- **Do not re-run this on a 43-row document.** The measurement needs a corpus where the reader cannot
  read everything, and one that can carry more than three status upgrades.
