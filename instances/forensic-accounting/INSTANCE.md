# Forensic accounting: trace funds and establish misstatement or diversion

**Status:** spec. **Examples are synthetic** (fictional nonprofit "Harborlight Foundation").

## 1. When to use it
Investigators, auditors and litigators must trace money through accounts and entities and show where it went, with every hop documented. Cost of confident error: accusing someone on a broken trail, or missing the diversion.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | A complete, documented trail for each questioned flow; quantified misstatement or diversion |
| 2 | Unit | Bank transaction line; ledger entry; invoice; contract; approval record |
| 3 | Fidelity | bank statement or bank-produced record > general ledger export > management schedule > interview > agent summary |
| 4 | Status | reconciled (ledger matches bank) / unreconciled / unsupported (no source document) |
| 5 | Vocabulary | The engagement's allegation list and a fraud classification used by the team (for example corruption, asset misappropriation, financial statement fraud) |
| 6 | Gates | "Diverted" needs an unbroken chain of bank-grade hops from source to destination; every amount re-computed by a deterministic module; an unsupported entry is a finding, not support |
| 7 | Roles | Defense accountant; prosecutor or plaintiff; auditor; judge |
| 8 | Evaluator | Arithmetic re-computation; chain completeness check; planted broken hop must fail |

## 3. Unit (synthetic)
```json
{"id":"bank_ACC1_2025_07_14_003","type":"bank_line",
 "locator":{"account":"ACC-1","statement":"2025-07","line":3},
 "text":"2025-07-14 OUTGOING WIRE 48,500.00 TO ACC-9 REF 'consulting'",
 "status":"reconciled","grade":"bank_record"}
```

## 4. Evidence model
- A trail is a path of `flows_to` edges. Each hop must cite bank-grade units on both sides, with matching amounts and dates within tolerance.
- Ledger entries alone cannot close a hop.

## 5. Framework lens
Allegations and fraud categories. Matrix: allegation by traced amount, broken hops, and unsupported entries.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Diverted needs a complete bank-grade chain | "48,500 diverted to the director" with the ACC-9 to personal-account hop citing a ledger only | after the ACC-9 bank statement line is linked |
| Amounts re-computed | total "212,000" entered by an agent | after the deterministic sum of linked lines equals it |
| Unsupported is a finding | ledger entry used as support with no invoice | after it is recorded as "unsupported" |

## 7. Roles and lenses
- **Roles:** defense red team (legitimate consulting, board approval); auditor cold bench; judge (burden).
- **Lenses:** investigation (all); regulator (traced amounts only); board (plain summary).

## 8. Evaluator
Re-computation; chain completeness with a planted gap; agreement with a manual trace sample.

## 9. Pipeline walkthrough
Intake statements and ledgers, reconcile, label lines by allegation, build flow edges, run chain checks, red team (look for approvals and contracts), then report with claim audit and re-computed schedules.

## 10. How you use it
- "Trace all outflows to ACC-9 and where they ended."
- "Which hops are not bank-supported?"

## 11. Worked example (synthetic)
1. Flow: ACC-1 to ACC-9 (48,500 on 7/14) to personal account (48,000 on 7/16). Both hops are bank-grade, so the trail is complete.
2. Red team: "board approved consulting". Verdict: damaging.
3. Minutes show no approval, and there is no consulting contract; the answer is recorded, and the finding is ready.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Round-tripping | cycle detection |
| Timing differences | date tolerance |
| Management schedules that do not tie out | re-computation |
| Commingled accounts | allocation method recorded as an assumption |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** statement and ledger parsers; reconciliation; deterministic arithmetic; flow graph checks.
