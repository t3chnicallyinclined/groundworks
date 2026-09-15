# Due diligence: find deal-breakers in a data room

**Status:** spec. **Examples are synthetic** (fictional acquisition of "Brightline Logistics").

## 1. When to use it
A buyer, lender or investor gets thousands of contracts and financial files under deadline. The worst outcome is a "clean" report that simply never read the problem document. Cost of confident error: an unpriced liability.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Identify and quantify risks that change price, structure or go/no-go |
| 2 | Unit | Contract clause; schedule line; financial statement line; board minute paragraph |
| 3 | Fidelity | executed document in the data room > draft or unsigned version > management summary > agent summary |
| 4 | Status | executed and in force / expired / draft / missing (listed in index but absent) |
| 5 | Vocabulary | The deal's diligence request list and risk categories, transcribed (for example change of control, assignment, exclusivity, most-favored pricing, indemnities, litigation, tax, data protection) |
| 6 | Gates | A red flag needs an executed clause cite; "no issue found" in a category needs coverage proof (every document of that type labeled); a missing document is itself a finding |
| 7 | Roles | Seller's counsel; lender; regulator; integration lead |
| 8 | Evaluator | Coverage check per category; planted-clause test (a seeded change-of-control clause must surface); agreement with counsel's sample review |

## 3. Unit (synthetic)
```json
{"id":"k_MSA_Acme_s14_2","type":"clause",
 "locator":{"doc":"DR-3.2.17 Master Services Agreement (Acme)","section":"14.2","executed":true},
 "text":"Either party may terminate on 30 days' notice following a Change of Control of the other party.",
 "status":"in_force","grade":"executed"}
```

## 4. Evidence model
- Executed documents confirm; drafts point.
- "Missing" is a status that produces a finding rather than silence.

## 5. Framework lens
The request list and risk categories. Matrix: category by documents reviewed, flags, and missing. A category with unreviewed documents cannot be reported clean.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Flag needs an executed clause | "Acme can terminate on sale" from a management memo | after citing MSA 14.2 (executed) |
| Clean needs coverage | category "change of control" marked clean with 12 of 40 contracts unlabeled | after all 40 are labeled |
| Missing is a finding | index lists DR-3.2.18, file absent, no finding | after a "missing document" finding is recorded |

## 7. Roles and lenses
- **Roles:** seller's counsel red team (materiality, waivers obtained); lender lens (covenants); regulator lens (filings, data).
- **Lenses:** deal team summary (quantified flags); lender pack (financing-relevant only); integration (operational contracts).

## 8. Evaluator
- **Mechanical:** coverage; planted clause; revenue figures re-computed from source.
- **Judged:** deal committee panel.
- **Outcome:** post-closing claims across deals.

## 9. Pipeline walkthrough
Intake the data room index and files, label clauses by category, quantify (revenue at risk from linked financial lines), apply gates, red team, produce the report with claim audit.

## 10. How you use it
- "Revenue at risk from change-of-control terminations, with clause cites."
- "Which categories are not yet provably clean?"
- "What is missing from the data room?"

## 11. Worked example (synthetic)
1. MSA 14.2 is flagged, and linked to Acme revenue (12% of total, from the revenue schedule).
2. Seller red team: "consent already obtained". Verdict: damaging.
3. No consent letter exists in the data room, so the answer "consent not in data room; request it" is recorded, and the flag stays open with a request.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Drafts mistaken for executed contracts | ladder |
| Amendments superseding clauses | supersedes edges |
| Silence mistaken for clean | coverage gate |
| Numbers in summaries not matching schedules | re-computation |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** data room index parser; clause segmenter; financial schedule reader.
