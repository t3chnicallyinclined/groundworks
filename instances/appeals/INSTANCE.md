# Appeals: find reversible error in a trial record

**Status:** built privately as instance 1 (numbers in `notes/2026-09-15_origin.md`). **Examples here are synthetic** (a fictional commercial bench trial, *Northwind Supply v. Harbor Foods*), not instance 1's matter.

## 1. When to use it
An appellate team inherits a record of hearing transcripts, exhibits and filings, plus a written decision. It must find errors the appellate court will actually correct, prove each from the record, anticipate the other side, and file a brief with no misquote or unverified citation. The cost of confident error is high: sanctions, lost credibility with the panel, a lost appeal.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | A reversible (or remittable) error in the order under review, provable from the record |
| 2 | Unit | Transcript line and speaker turn; exhibit page; filed-paper page; decision page; message inside an exhibit |
| 3 | Fidelity | page image > text extract with page:line > agent report > recollection |
| 4 | Status | In the appellate record (testimony; exhibit received; court file) vs not (marked for identification only; excluded; attachment never offered; post-hearing material) |
| 5 | Vocabulary | The legal test **as the decision stated it**, transcribed from the decision's page images; findings; points on appeal; preservation; standard of review |
| 6 | Gates | Confirmed fact needs record-grade in-record evidence; authority verified only on official text; point ready only if preserved, supported, adverse material recorded, attacked with no fatal head and every damaging head answered, no unverified authority |
| 7 | Roles | Opposing counsel; deferential appellate judge; three-judge cold bench; premortem; citation auditor |
| 8 | Evaluator | Verbatim validator; gate probes; past-error regression suite; label-agreement sample; fixed cold-bench panel |

## 3. Units and locators (synthetic)
```json
{"id":"tu_00412","type":"transcript_turn",
 "locator":{"source":"trial_day2","page_from":88,"line_from":4,"page_to":88,"line_to":11},
 "text":"THE COURT: So the notice letter was never offered? MR. PAYNE: It was marked, Judge, but we did not move it in.",
 "status":"usable","grade":"text_extract","meta":{"speaker":"THE COURT","role":"colloquy"}}
```

## 4. Evidence model
- **Fidelity:** only page image and text extract confirm; filing-grade is page image.
- **Status:** a text-extract quote from an exhibit marked for identification is exact and unusable. Both axes are checked.
- **Authority:** official reporter text verifies; search summaries and secondary sources never do. Existence is not support: the pinpoint page must say what the brief claims.

## 5. Framework lens
Transcribe the test the decision said it applied, verbatim with page. Synthetic example: the decision lists "(1) whether a material breach occurred, (2) whether written notice was given, (3) whether the cure period expired, (4) damages." Each becomes a node; the crosswalk files labels coded `notice`, `cure`, `breach`, `damages` under them. The **factor matrix** shows, per element, record items for and against, split usable and unusable.

## 6. Gates (with probes)
| Rule | Rejected write | Then accepted |
|---|---|---|
| Confirmed fact needs usable record-grade evidence | fact `notice_never_received` set confirmed with no cite | after linking `tu_00412` (text_extract, usable) |
| Point ready needs usable support | point 2 ready with only an exhibit marked for identification as support | after a transcript turn supports it |
| Point ready needs surviving attack | ready with a `fatal` attack standing | after the head is downgraded on the record and answered |
| Authority verified needs official text | case verified on a search summary | after an official-text source is linked |

## 7. Roles and lenses
- **Red team:** opposing counsel, deferential judge, and a third lens that fits the case (in family matters, the child's attorney).
- **Cold bench:** three judges (deference, governing test, remedy).
- **Premortem:** preservation, standard of review, record, remedy and timing, procedure, presentation.
- **Lenses:** panel (usable only, no agent notes); opposing counsel (against-direction, strongest first); client (plain language, blockers).

## 8. Evaluator
- **Mechanical:** validator; probes; regression suite (every past misquote, wrong pin cite, misattributed quotation, misread question); brief citation verdicts.
- **Judged:** cold-bench confidence scores.
- **Outcome:** the appellate decision (too late to tune on a single case; use across matters).

## 9. Pipeline walkthrough
1. Intake transcripts with page:line markers, filings and exhibits with page locators, and exhibit status from the exhibit index.
2. Chunk and label (tier, direction, issues, findings, points, role, reason, quote).
3. Validate, load, build the framework lens.
4. Build the issue matrix from tier-3 labels.
5. Verify authorities.
6. Red team and cold bench; answer heads.
7. Draft; claim-audit every sentence; improvement loop on the lead point; confirm load-bearing cites on page images.

## 10. How you use it
- "What does the record say about notice, usable only?" returns located turns and received exhibits, for and against.
- "Red team point 2." returns heads with verdicts, recorded in the graph.
- "What blocks point 3?" returns, for example: "not preserved; one damaging head unanswered."
- "Cite check section II." returns a verdict per citation, with the source's actual words for any mismatch.

## 11. Worked example (synthetic)
`tu_00412` is labeled tier 3, direction for appellant (Harbor Foods), issue `notice`, finding `finding_04` ("written notice was given").

The crosswalk files it under element (2).
- **Point 2:** the finding that notice was given lacks record support.
- **Red team, deference lens:** "the court could infer notice from the course of dealing (Tr. 61:2-9)." Verdict: damaging.
- **Answer:** "the course-of-dealing testimony concerns invoices, not notice of breach (Tr. 61:4)."
- **Brief citation check:** the brief's cite "Tr. 88:4-11" is VERIFIED against the turn.
- **Readiness:** point 2 becomes ready.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| A quote that answers a different question than the one asked | claim audit reads the question |
| A pin cite one page off | regression suite |
| An exhibit that was marked but not received | status axis |
| A quotation attributed to the wrong case | authority gate |
| An argument never raised below | preservation gate |
| Attacking credibility where the court deferred | deference lens |

## 13. Module reuse
- **As-is:** chunker, validator, loader, gate library, lens builder, hooks, roles, evaluator.
- **Adapters:** transcript page:line parser; exhibit-status resolver from the exhibit index; decision page-image transcription for the framework.
