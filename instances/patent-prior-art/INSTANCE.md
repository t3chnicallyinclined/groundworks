# Patent prior art: invalidate or defend a claim

**Status:** spec. **Examples are synthetic** (fictional claim 1 of a fictional patent on a "self-draining planter").

## 1. When to use it
Challengers and patent owners must compare each claim element against large prior-art collections under strict date rules. A single missing element defeats anticipation. Cost of confident error: a failed challenge, or a defense built on a reference that predates priority.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Show each claim element disclosed in the prior art (or show an element absent) |
| 2 | Unit | Claim element (parsed claim limitation); prior-art paragraph or figure with locator |
| 3 | Fidelity | published document text or figure with publication data > machine translation with original > examiner or third-party summary > agent summary |
| 4 | Status | prior art (publication date before the priority date under the applicable rules) / not prior art / date uncertain |
| 5 | Vocabulary | The claim's elements, transcribed from the issued claim; claim construction from the record if any |
| 6 | Gates | "Anticipates" needs every element mapped to a verbatim passage of a single prior-art reference; "renders obvious" needs mapped elements plus a recorded reason to combine; any reference dated on or after priority is unusable |
| 7 | Roles | Patentee's counsel; examiner or tribunal; technical expert of ordinary skill |
| 8 | Evaluator | Element coverage check (programmatic); date check; agreement with a claim chart reviewed by counsel |

## 3. Unit (synthetic)
```json
{"id":"ref_US0000001_p0042","type":"art_paragraph",
 "locator":{"ref":"US0000001A","para":42,"pub_date":"2014-06-03"},
 "text":"A drainage channel below the soil tray directs excess water to a removable reservoir.",
 "status":"prior_art","grade":"published_text"}
```

## 4. Evidence model
- Publication text confirms; a translation needs the original linked.
- Status comes from a date resolver against the priority date (the applicable date rules are configured, not assumed).

## 5. Framework lens
Claim 1's elements as nodes, for example "1[a] a soil tray", "1[b] a drainage channel below the tray", "1[c] a removable reservoir". The claim chart is the matrix: element by reference by passage.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Anticipation needs every element in one reference | anticipates with 1[c] unmapped | after mapping 1[c] to para 42 of the same reference |
| Obviousness needs a reason to combine | obvious from two references, no reason recorded | after a cited motivation passage is linked |
| Not prior art cannot support | mapping from a reference published after priority | never accepted |

## 7. Roles and lenses
- **Roles:** patentee red team ("the channel in para 42 is not below the tray"); tribunal cold bench; person of ordinary skill.
- **Lenses:** challenger chart, patentee rebuttal chart, tribunal (usable art only).

## 8. Evaluator
- **Mechanical:** every element covered; dates verified.
- **Judged:** tribunal panel.
- **Outcome:** decisions across matters.

## 9. Pipeline walkthrough
Parse claims into elements, intake art with dates, label paragraphs by element with quotes, resolve dates, build chart edges, red team per element, audit the chart.

## 10. How you use it
- "Best single reference for claim 1, and which element is weakest."
- "Show every mapping the patentee will attack."
- "Is any cited art after priority?"

## 11. Worked example (synthetic)
1. Reference A maps 1[a], 1[b] and 1[c]; anticipation is marked candidate.
2. Patentee lens: "A's channel is beside the tray (fig. 3)". Verdict: damaging.
3. The expert reads figure 3: the channel is below. The answer is recorded with the figure locator, and the chart is ready.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Mapping an element to a different element's passage | per-element quotes |
| Date traps | status resolver |
| Figures misread from text | figure units |
| Translation drift | original linked |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** claim parser; patent document parser with paragraph and figure locators; date resolver.
