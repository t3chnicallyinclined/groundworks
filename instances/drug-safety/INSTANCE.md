# Drug safety: detect and assess adverse-event signals

**Status:** spec. **Examples are synthetic** (fictional product "Corvanil").

## 1. When to use it
Safety teams monitor case reports, literature and trial data for new adverse effects. Signals hide in noise and duplicates, and single dramatic reports mislead. Cost of confident error: missed harm, or a false alarm that removes a useful product.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Detect, validate and assess signals for a product-event pair |
| 2 | Unit | Individual case report (with dates, dose, dechallenge and rechallenge fields); literature case paragraph; trial adverse-event table row |
| 3 | Fidelity | source case report with verified fields > structured database record > literature summary > media report > agent summary |
| 4 | Status | valid case (minimum criteria met) / invalid / duplicate |
| 5 | Vocabulary | Standard event terminology used by the organization (for example a medical dictionary's preferred terms); causality categories used by the organization (for example the WHO-UMC scale: certain, probable/likely, possible, unlikely, conditional/unclassified, unassessable/unclassifiable) |
| 6 | Gates | A signal needs more than one valid, non-duplicate case, or a disproportionality statistic from a deterministic module; a causality rating needs time-to-onset and dechallenge fields assessed or marked unknown |
| 7 | Roles | Clinician reviewer; epidemiologist; regulator; manufacturer's counsel |
| 8 | Evaluator | Duplicate detection test; field re-read agreement; comparison with later label changes (outcome) |

## 3. Unit (synthetic)
```json
{"id":"icsr_00931","type":"case_report",
 "locator":{"db":"safety_db","case":"00931","version":3},
 "text":"62F, Corvanil 20 mg daily from 2026-01-10; hepatitis onset 2026-02-02; drug stopped, ALT normalized by 2026-03-01.",
 "status":"valid","grade":"source_verified"}
```

## 4. Evidence model
- Duplicates collapse to one case with linked versions.
- Media reports point; they never count toward a signal.

## 5. Framework lens
Causality criteria as nodes (temporal relationship, dechallenge, rechallenge, alternative causes). Matrix per product-event pair: cases by criterion met, not met, unknown.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Signal needs multiple valid cases or a statistic | signal from icsr_00931 alone | after a second valid non-duplicate case or a disproportionality result is linked |
| Causality needs criteria assessed | "probable" with dechallenge unassessed | after dechallenge is recorded (positive, negative or unknown) |
| Duplicate cannot add weight | two versions of one case counted twice | after dedup links them |

## 7. Roles and lenses
- **Roles:** manufacturer red team (confounders, indication bias); regulator cold bench; epidemiologist (reporting artifacts).
- **Lenses:** safety committee (valid cases only); regulator submission (structured); clinician (narratives).

## 8. Evaluator
Planted duplicate must merge; field extraction double-read; time-split against later official safety communications.

## 9. Pipeline walkthrough
Intake cases, deduplicate, validate, code events, label criteria with quotes from narratives, compute statistics (deterministic), assess signals, red team, and produce the assessment with claim audit.

## 10. How you use it
- "Is there a hepatic signal for Corvanil? Show valid cases by causality criteria."
- "Which cases lack dechallenge information?"

## 11. Worked example (synthetic)
1. Three hepatitis cases arrive; two are versions of one report and merge, leaving two valid cases, which meets the gate.
2. Red team: "both patients on another hepatotoxic drug". Verdict: damaging.
3. The answer records that one case had no concomitant medication, with a quote. The signal is marked "validated for assessment".

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Duplicate reports | status axis plus dedup |
| Stimulated reporting after media coverage | epidemiologist lens |
| Narrative fields contradicting structured fields | validator quote check |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** case report parser; terminology coder; deterministic disproportionality module; dedup.
