# Litigation and e-discovery: chronology and key documents from a production

**Status:** spec. **Examples are synthetic** (fictional *Orion Labs v. Castellan Parts*).

## 1. When to use it
Trial teams receive hundreds of thousands of documents and hours of depositions. They need a sourced chronology, the documents that prove or break each claim element, and a privilege wall that never leaks. Cost of confident error: waived privilege, a fact asserted at trial without a document, a missed hot document.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Prove or defeat each element of each claim and defense; a sourced chronology |
| 2 | Unit | Document (Bates range) and its paragraphs; email in a thread; deposition line |
| 3 | Fidelity | produced native or image with Bates number > extracted text with Bates and page > vendor summary > agent summary |
| 4 | Status | produced and usable / privileged (withheld or logged) / confidential under protective order (lens-restricted) / not produced |
| 5 | Vocabulary | Claim elements from the pleadings and jury instructions, transcribed; chronology events; custodians |
| 6 | Gates | A key fact needs a Bates cite; nothing privileged enters any lens shared outside the privilege team; chronology events need a dated document |
| 7 | Roles | Opposing counsel; judge on a motion; jury (plain reading); privilege reviewer |
| 8 | Evaluator | Validator; privilege leak test (planted privileged document must never appear in a shared view); sample agreement against attorney review |

## 3. Unit (synthetic)
```json
{"id":"doc_OL0004512_p2","type":"document_page",
 "locator":{"bates":"OL0004512","page":2},
 "text":"We knew the batch failed QC on 4/2 and shipped anyway to hit the quarter.",
 "status":"produced","grade":"image_text"}
```

## 4. Evidence model
- A Bates image confirms.
- A vendor summary points.
- Privilege status is set by the privilege reviewer role and enforced in views.

## 5. Framework lens
Claim elements, transcribed from the complaint and pattern jury instructions. Matrix: element by for, against, and privileged-withheld counts.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Key fact needs Bates | "Castellan knew of QC failure" confirmed from a deposition summary | after citing OL0004512 p.2 |
| Privileged never in shared lens | shared chronology view returns a document with privilege status | never accepted; the view filter is tested by a planted document |
| Event needs a dated source | event "shipment 4/5" with an undated note | after the dated shipping record is linked |

## 7. Roles and lenses
- **Roles:** opposing counsel red team per element; judge (summary judgment standard); jury plain-reading lens; privilege team lens.
- **Lenses:** shared case team (no privileged); privilege team (all); expert witness (their subject only, protective-order compliant).

## 8. Evaluator
- **Mechanical:** leak test and validator.
- **Judged:** motion panel.
- **Outcome:** rulings on motions across matters.

## 9. Pipeline walkthrough
Intake the load file with Bates, then thread emails, then label (element, event, custodian, tier, direction, privilege flag), then privilege review, then load, then chronology events with mentions (stance by author), then the element matrix, then red team, then trial exhibit list with claim audit.

## 10. How you use it
- "Chronology of QC failures, shared-safe."
- "Best documents for scienter and the best counter-documents."
- "Did any privileged document reach the expert lens?" This question must always return no, with the test result.

## 11. Worked example (synthetic)
1. OL0004512 is labeled tier 3 for plaintiff, element "knowledge".
2. The event "QC failure 4/2" gets mentions from three custodians. One deponent's testimony denies knowledge (stance: denies), so the event card shows the shift.
3. Red team: "author was a contractor without authority" (damaging).
4. The answer cites the org chart document, and the element becomes ready.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Privilege leakage | status axis plus lens tests |
| Near-duplicate documents counted as independent support | intake dedup |
| Attachments separated from parents | family locator |
| Deposition errata | supersedes edges |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** load file parser with Bates; email threading; privilege status resolver; protective-order view scopes.
