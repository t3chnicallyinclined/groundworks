# Investigative journalism: establish what happened, publishably

**Status:** spec. **Examples are synthetic** (fictional city contract "Riverside paving").

## 1. When to use it
A newsroom has a leak of documents, public records and interviews. Each published claim must survive legal review and the subject's response. Confidential sources must never be exposed. Cost of confident error: defamation liability, a correction, a burned source.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Publishable findings about what happened, each independently sourced |
| 2 | Unit | Document page; public record entry; interview statement (with speaker and attribution terms) |
| 3 | Fidelity | authenticated original document or official record > document of unverified provenance > on-record interview > anonymous interview > secondary report > agent summary |
| 4 | Status | publishable / background only (attribution restrictions) / off-record (never in any draft lens) |
| 5 | Vocabulary | The story's claims list (the reporting memo), each a proposition to prove |
| 6 | Gates | A published claim needs two independent sources, at least one documentary or official; off-record material never enters a draft; every claim about a person needs a recorded right-of-reply attempt |
| 7 | Roles | Subject's lawyer; editor; standards editor; fact-checker |
| 8 | Evaluator | Independence check (sources not derived from each other); off-record leak test; corrections history across stories |

## 3. Unit (synthetic)
```json
{"id":"rec_council_2025_118_p4","type":"public_record_page",
 "locator":{"record":"Council minutes 2025-118","page":4},
 "text":"Motion to award paving contract to Delta Paving without rebid; approved 5-2.",
 "status":"publishable","grade":"official_record"}
```

## 4. Evidence model
- Independence is an explicit edge: a source derived from another (for example a forwarded copy) does not count twice.
- The source-identity field lives in a restricted lens only.

## 5. Framework lens
The reporting memo's claims as nodes. Matrix: claim by independent sources, documentary sources, and reply status.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Two independent sources, one documentary | claim "no rebid" with one anonymous interview | after linking minutes p.4 |
| Off-record never in drafts | draft view containing an off-record statement | never accepted; view tested with a planted unit |
| Right of reply | claim about a named official with no reply attempt | after the reply request and response or non-response is logged |

## 7. Roles and lenses
- **Roles:** subject's lawyer red team (defamation angles, innocent explanations); standards editor cold bench; fact-checker (claim audit).
- **Lenses:** reporters (all but source identities); editors (publishable and background); legal (everything with restrictions shown).

## 8. Evaluator
Independence test (planted forwarded copy must not count twice); leak test; claim audit on the draft.

## 9. Pipeline walkthrough
Intake documents and records, authenticate, label claims with quotes, record interviews with attribution terms, link independence, apply gates, red team, run reply requests, draft, then claim audit.

## 10. How you use it
- "Which claims are publishable today, and which lack a second source?"
- "Red team the no-rebid claim as the contractor's lawyer."

## 11. Worked example (synthetic)
1. "No rebid" is supported by the minutes plus a procurement officer on record, which meets the gate.
2. Red team: "an emergency exemption applied". Verdict: damaging.
3. The records search finds no emergency declaration, so the answer is recorded. The reply from the city is logged, and the claim is ready.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Circular sourcing | independence edge |
| Forged documents | authentication grade |
| Exposing a source through metadata | restricted lens and privacy guard |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** document authentication status; interview attribution terms; restricted source lens.
