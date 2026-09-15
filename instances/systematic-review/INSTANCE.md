# Systematic review: establish whether an intervention works

**Status:** spec. **Examples are synthetic** (fictional drug "zelvastat" for fictional outcome "migraine days").

## 1. When to use it
A clinical, policy or guideline team must answer a focused question (population, intervention, comparator, outcome) from dozens to thousands of studies. The evidence varies in quality, abstracts overstate, and reviews are often redone from scratch. The cost of confident error: harmful or wasteful care.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Certainty-rated answer to one PICO question |
| 2 | Unit | Outcome result (effect estimate with CI) within a study report; risk-of-bias judgment item; eligibility criterion |
| 3 | Fidelity | full-text report or trial registry record > supplementary tables > abstract > press release or secondary citation > agent summary |
| 4 | Status | Included (meets eligibility) / excluded (with reason) / awaiting classification |
| 5 | Vocabulary | The review protocol's PICO and outcomes; GRADE certainty (high, moderate, low, very low) with downgrade domains (risk of bias, inconsistency, indirectness, imprecision, publication bias); a risk-of-bias tool's domains (for randomized trials, e.g. RoB 2: randomization, deviations from intended interventions, missing outcome data, outcome measurement, selection of reported result) |
| 6 | Gates | A pooled conclusion needs included full-text results only; a certainty rating needs a judgment recorded for every downgrade domain; "no effect" cannot be concluded from imprecise estimates |
| 7 | Roles | Methodologist, statistician, clinician, patient representative, skeptic (publication bias) |
| 8 | Evaluator | Re-computation of every extracted number from the source table; dual extraction agreement; comparison to an existing high-quality review where one exists |

## 3. Units and locators (synthetic)
```json
{"id":"res_ZELV2_mdays_12w","type":"outcome_result",
 "locator":{"doc":"ZELV-2 trial report","table":"Table 3","row":"Migraine days/month, week 12"},
 "text":"Mean difference -1.8 (95% CI -2.9 to -0.7), n=412",
 "status":"included","grade":"full_text"}
```

## 4. Evidence model
- Only full text or registry results feed pooled estimates.
- Abstract-only numbers are `abstract` grade and cannot confirm.
- Status comes from the protocol's eligibility criteria, applied by labeled criterion units.

## 5. Framework lens
The protocol's outcomes and GRADE domains, transcribed from the protocol and the GRADE guidance used. Matrix: per outcome, studies included, pooled direction, and the domain judgments driving certainty.

## 6. Gates (with probes)
| Rule | Rejected write | Then accepted |
|---|---|---|
| Pooled estimate uses only full-text included results | pooled MD including an abstract-only study | after replacing it with the full-text result or excluding it with a reason |
| Certainty needs all five domains judged | "moderate certainty" with imprecision unjudged | after the imprecision judgment is recorded with its reason |
| Excluded study needs a reason code | exclusion with no reason | after a reason from the protocol list is recorded |

## 7. Roles and lenses
- **Red team:** skeptic (selective reporting, funding, small-study effects), statistician (heterogeneity, imprecision), clinician (indirectness).
- **Cold bench:** guideline panel.
- **Lenses:** guideline panel (included only, certainty first); patient (absolute effects in plain language); methods (bias judgments).

## 8. Evaluator
- **Mechanical:** every extracted number re-read from its table by a second agent; arithmetic re-computed; eligibility decisions double-coded.
- **Judged:** certainty panel.
- **Outcome:** agreement with a later large trial or an independent review.

## 9. Pipeline walkthrough
1. Intake reports, registries and supplements with table and row locators.
2. Label eligibility criteria units and outcome results.
3. Validate quotes and numbers.
4. Load and apply eligibility.
5. Risk-of-bias labeling per domain.
6. Pool (a deterministic statistics module, not an agent).
7. GRADE judgments with gates.
8. Red team; certainty table; claim audit of the summary.

## 10. How you use it
- "What is the certainty for migraine days at 12 weeks, and what drives it?"
- "Which studies were excluded for population mismatch?"
- "Red team the moderate rating."

## 11. Worked example (synthetic)
1. ZELV-2's abstract reports -2.4 days; the full-text Table 3 reports -1.8. The abstract unit is `abstract` grade, so it is not pooled. The regression suite gains an entry: "abstract effect differs from table."
2. The skeptic lens flags that two small positive trials lack registry outcomes. Verdict: damaging.
3. The answer records publication bias, and certainty is downgraded to low with the reason on the edge.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Abstract numbers differing from tables | fidelity ladder |
| Outcome switching from the registry | registry unit comparison |
| Double-counting a trial reported in two papers | intake dedup by registry ID |
| "No significant difference" read as "no effect" | imprecision gate |

## 13. Module reuse
- **As-is:** most modules.
- **Adapters:** table and row locator parser; registry reader; a deterministic meta-analysis module; GRADE and risk-of-bias vocabularies.
