# Legislative history: establish what a legislature meant or knew

**Status:** spec. **Examples are synthetic** (fictional "Clean Harbors Act").

## 1. When to use it
Lawyers, courts and policy analysts ask what a provision was meant to do, what problem it addressed, or what drafters knew. The record spans bill versions, committee reports, hearings and floor debate, and quotes get pulled from the wrong stage or speaker. Cost of confident error: an argument built on a statement about a different version of the text.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | A sourced account of purpose, scope or knowledge for one provision |
| 2 | Unit | Bill text section at a version; committee report paragraph; hearing transcript statement; floor statement; amendment |
| 3 | Fidelity | official record (enrolled text, official report, official journal) > official transcript > unofficial transcript > advocacy summary > commentary > agent summary |
| 4 | Status | same provision text as enacted / earlier version with different text / unrelated provision |
| 5 | Vocabulary | The provision's elements as enacted, transcribed; legislative stages |
| 6 | Gates | An intent claim needs official-record units about the enacted version of the text (or an explicit link showing the text did not change); a statement is attributed to its speaker, role and stage; commentary cannot confirm |
| 7 | Roles | Opposing interpretation; court (skeptical of legislative history use); historian |
| 8 | Evaluator | Version-match check (quoted provision text equals the version discussed); attribution check |

## 3. Unit (synthetic)
```json
{"id":"crpt_HR210_p14_para3","type":"committee_report_para",
 "locator":{"report":"Committee Report 210","page":14,"para":3,"bill_version":"reported"},
 "text":"Section 5 is intended to reach discharges from vessels at anchor, not only in transit.",
 "status":"same_text_as_enacted","grade":"official_record"}
```

## 4. Evidence model
Status depends on a text-diff between the version discussed and the enacted version. This is computed, not assumed.

## 5. Framework lens
The provision's elements. Matrix: element by stage by statements for each reading.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Intent needs official record on the same text | "Congress meant vessels at anchor" from a floor statement about the introduced version, whose Section 5 differed | after the committee report on the reported version (same text as enacted) is linked |
| Attribution required | quote without speaker and stage | after speaker, role and stage are recorded |
| Commentary cannot confirm | law review article as sole support | never accepted as confirming |

## 7. Roles and lenses
- **Roles:** opposing-reading red team; skeptical court cold bench.
- **Lenses:** by stage; by speaker; enacted-text-only.

## 8. Evaluator
Version diff correctness; attribution completeness; claim audit on the memo.

## 9. Pipeline walkthrough
Intake versions and records, diff versions, label statements by element, stage and speaker, apply gates, red team, then write the memo.

## 10. How you use it
- "What did the official record say about vessels at anchor, for the enacted text only?"
- "Which quotes in this memo discuss a different version?"

## 11. Worked example (synthetic)
1. A floor quote favors the "in transit only" reading. The version diff shows the quote concerned the introduced text, before the anchor language was added, so its status is earlier version and it is unusable for enacted meaning.
2. The committee report on the reported text supports the anchor reading, and the claim is ready.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Quotes about superseded text | status diff |
| Unattributed statements | attribution gate |
| Advocacy summaries treated as record | ladder |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** bill version parser and differ; official record parsers.
