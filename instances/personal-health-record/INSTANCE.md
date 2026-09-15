# Personal health record: understand one person's condition over time

**Status:** spec. **Examples are synthetic** (fictional patient P-001). **Not medical advice:** this organizes a person's own records to prepare better questions for clinicians.

## 1. When to use it
Someone with a chronic or complex condition has years of lab results, visit notes, imaging reports and medication changes across providers. They and their clinicians need an accurate, dated picture. Cost of confident error: a wrong trend, a missed interaction, a misremembered history.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | An accurate, dated, cited history and trend picture, with open questions for the care team |
| 2 | Unit | Lab result (value, units, reference range, date); visit note paragraph; medication start or stop; imaging impression |
| 3 | Fidelity | original lab report or portal record > clinician note > patient diary > recollection > agent summary |
| 4 | Status | own record / other person's record (never merged) / uncertain date |
| 5 | Vocabulary | The person's problem list as written by their clinicians; lab and medication names as recorded |
| 6 | Gates | A trend needs at least three dated values in consistent units; a medication history claim needs a prescription or clinician note; nothing is labeled a diagnosis unless a clinician recorded it |
| 7 | Roles | Specialist; primary care clinician; skeptic (lab method changes, unit changes) |
| 8 | Evaluator | Unit consistency check; date ordering; clinician review of the summary (outcome) |

## 3. Unit (synthetic)
```json
{"id":"lab_P001_2026_02_11_TSH","type":"lab_result",
 "locator":{"report":"Portal lab 2026-02-11","test":"TSH"},
 "text":"TSH 5.8 mIU/L (ref 0.4-4.0)",
 "status":"own_record","grade":"original_report"}
```

## 4. Evidence model
Values are normalized to units by a deterministic module; a method or unit change is itself recorded.

## 5. Framework lens
The clinician-written problem list. Matrix: problem by dated results by medication changes.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Trend needs three dated values, consistent units | "TSH rising" from two values | after a third dated value in the same units is linked |
| No self-diagnosis | label "hypothyroidism" with no clinician record | never accepted as diagnosis; kept as a question for the clinician |
| Never merge another person's record | lab from a different patient ID | never accepted |

## 7. Roles and lenses
- **Roles:** skeptic (assay changes); clinician cold bench (what would they ask?).
- **Lenses:** visit prep (one page: changes since last visit, questions); full history; medication timeline.

## 8. Evaluator
Units and dates checks; clinician feedback.

## Privacy
Local-only by default: records stay on the person's machine. Nothing is sent to any service the person has not chosen. No model training on the records.

## 9. Pipeline walkthrough
Intake portal exports and PDFs, label results and notes, normalize units, apply gates, build timelines, then produce visit prep with claim audit.

## 10. How you use it
- "What changed since my last endocrinology visit, with dates?"
- "Questions to ask about the TSH values."

## 11. Worked example (synthetic)
1. TSH values of 3.1, 4.4 and 5.8 over 14 months, in consistent units, form a trend.
2. Skeptic: "the lab changed in 2025". The report shows the same lab, so the head is answered.
3. Output: a dated trend, plus the question "Is follow-up testing needed?", without any diagnosis.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Unit changes | normalization |
| Copied-forward note text | fidelity and date checks |
| Self-diagnosis | gate |

## 13. Module reuse
- **As-is:** core modules, run locally.
- **Adapters:** portal export and lab report parsers; unit normalization.
