# 2026-09-15: reading layer v1.0, measured on a real instance

Generic; no matter content. Module: `groundworks/present`. Roadmap C17.

## What was tested
Three real documents from the first instance (163, 226 and 234 rows; 139 to 185 KB in full mode). Five seeded errors per document, one of each type, planted 3 in rows that render as **established** and 2 in rows that render as **needs check**. Two renderings per document: `full` (the plain baseline) and `speedread` (check-first ordering, bold on numbers, locators, negations, statuses and verdicts). Six reviewer agents, one per document per rendering, each with a budget of 8 source lookups.

## Deterministic results

| Measure | full | speedread |
|---|---|---|
| Preservation (ids, numbers, negations, locators, texts) | 100% | 100% |
| Size (largest document) | 185 KB | **202 KB (+9%)** |
| Median first line of a check-first row (3 docs) | 176 / 169 / 133 | **61 / 64 / 46** |
| Check-first rows visible in the first 40 lines (largest doc) | 4 of 105 | **32 of 105** |
| Check-first share of all rows | n/a | **45 to 50%** |
| Median line of planted errors | 188 / 207 / 169 | 206 / 201 / 107 |

## Reviewer results (6 agents, 8 lookups each, all 48 lookups used)

| | full | speedread |
|---|---|---|
| Caught | **4 of 15 (0.27)** | **3 of 15 (0.20)** |
| Findings | 7 | 5 |
| False alarms | 3 | 2 |

By error type (caught / planted, both arms):

| Type | full | speedread |
|---|---|---|
| number_change | 2/3 | 1/3 |
| locator_shift | 1/3 | 1/3 |
| negation_drop | 1/3 | 1/3 |
| **status_upgrade** | **0/3** | **0/3** |
| **verdict_soften** | **0/3** | **0/3** |

By where the error lands: established full 3/9, speedread 2/9; needs-check full 1/6, speedread 1/6.

## Findings

1. **Speed-read did not improve catching.** 0.27 vs 0.20 with one reviewer per cell is noise. The claimed benefit (check items appear about three times earlier) did not convert into more errors found.
2. **Relabelling is invisible to reading.** Not one reviewer in either arm caught a status upgraded to "confirmed" or a verdict softened from damaging (0 of 12 across both arms). Nothing in the text contradicts a wrong label, so no layout can surface it. **These errors belong to a mechanical re-check, not to presentation**: re-run the promotion rule over every row and show the result inline. The first instance already has the bulk version of this check in its health output.
3. **Reviewers verify by internal contradiction.** All six read the whole document and hunted for the text disagreeing with itself (the same quote with two dates, a mileage figure that differs from the source, a holding that reads backwards). Lookups were spent confirming suspicions, not finding them. A rendering that groups repeated citations of the same source would support this behaviour directly.
4. **Counts are a verification tool.** The speed-read header's counts let one reviewer find a mismatch between a claimed number of unanswered heads and the number of rows shown, which implied a softened verdict; the reviewer could not isolate the row within budget. Section counts that can be checked by hand are worth keeping and extending.
5. **The real obstacle is volume, not layout.** A single document is 160 to 234 rows. Neither rendering is skimmable, and speed-read is larger because it drops nothing. v1.1 answers this with a scan layer plus details, collapsed critiques, a cap with visible overflow, and a random spot-check sample of established rows.
6. **Errors hidden in "established" rows are the risk, as predicted.** Catch rates there were 3/9 and 2/9. The spot-check sample in v1.1 exists for exactly this, and should be measured next.

## Caveats
- One reviewer per cell, one model, six agents in total. These are agent skim-verifiers, not human readers.
- Both arms saw the same seeded copies, so plant quality is shared; the type-level result (0 of 12 on relabels) is the strongest signal because it is uniform.
- False alarms included genuine inconsistencies in the data that were not planted, so precision here is a lower bound.

## What this changes
- The speed-read view is **not adopted as the default**. It stays available as `view=speedread`.
- Adoption is blocked on a re-run with v1.1 **and** an inline "does this still pass the promotion rule today" marker, which is what the missed error classes actually need (roadmap C18).
