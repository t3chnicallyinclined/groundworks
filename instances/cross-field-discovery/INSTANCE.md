# Cross-field discovery: find where one field's method solves another field's problem

**Status:** spec (roadmap D1-D2). Lineage: literature-based discovery (Swanson's ABC model). **Examples are synthetic.**

## 1. When to use it
A research group wants hypotheses no one has written down: a technique from field A that fits an open problem in field B. The literature is too large to read. Most automated "connections" are noise. The cost of confident error: months spent chasing a bridge that breaks on an assumption nobody checked.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Testable bridges: method in paper X plausibly solves stated problem in paper Y, not already known |
| 2 | Unit | Cards: **problem** ("could not do X because Y"), **capability** ("achieves X under assumptions A"), **assumption**, **limitation**, each a sentence with location |
| 3 | Fidelity | reproduction (we ran the test) > full text > abstract > citation context in another paper > agent summary |
| 4 | Status | Card is in-scope (category, date window) / out-of-scope; bridge is hypothesis / tested-supported / tested-refuted |
| 5 | Vocabulary | Each field's own problem taxonomy where one exists (for example, arXiv category descriptions or a survey's taxonomy, transcribed); a translator glossary mapping terms across fields |
| 6 | Gates | A bridge becomes a candidate only with verbatim problem and capability cards; it becomes testable only after assumptions are matched and a novelty check passes; "supported" only after a test runs |
| 7 | Roles | Target-field expert; source-field expert; statistician; translator; skeptic |
| 8 | Evaluator | **Time split:** build the graph from papers before a cutoff, score proposed bridges against links made by later papers; a baseline (random or co-citation) must be beaten |

## 3. Units and locators (synthetic)
```json
{"id":"card_P_1234_problem","type":"problem",
 "locator":{"paper":"arXiv:0000.00001","section":"5 Limitations","para":2},
 "text":"Our estimator fails when the sensor noise is heavy-tailed, which is common in field deployments.",
 "status":"in_scope","grade":"full_text"}
{"id":"card_C_5678_capability","type":"capability",
 "locator":{"paper":"arXiv:0000.00002","section":"3 Method","para":4},
 "text":"The median-of-means filter retains its error bound under heavy-tailed noise with finite variance.",
 "status":"in_scope","grade":"full_text"}
```

## 4. Evidence model
- Cards from abstracts cannot make a bridge testable; full text is required.
- A bridge's own status is separate from its cards' grades.
- Reproduction (a notebook result) outranks everything.

## 5. Framework lens
Each field's taxonomy nodes; problem cards filed under the target taxonomy, capability cards under the source taxonomy. The discovery matrix counts, per target problem class, candidate capabilities, tested bridges and refuted bridges.

## 6. Gates (with probes)
| Rule | Rejected write | Then accepted |
|---|---|---|
| Candidate needs verbatim cards on both sides | bridge citing only an agent summary of paper X | after linking the capability card's full-text sentence |
| Testable needs assumption match and novelty check | testable with the "finite variance" assumption unchecked | after the assumption card is matched to the target setting and the novelty search is recorded |
| Supported needs a run | supported with a written argument only | after a test result is linked |

## 7. Roles and lenses
- **Roles:** the target expert judges whether the problem matters; the source expert judges whether the method is being used within its assumptions; the statistician checks the test design; the translator maps vocabulary; the skeptic searches for prior combinations.
- **Lenses:** by target field, by source field, "refuted and why" (the most reusable output).

## 8. Evaluator
- **Mechanical:** card validator (verbatim sentence); novelty-check logging.
- **Outcome:** time-split hit rate at k against a co-citation baseline. This is the fixed metric the improvement loop tunes against.

## 9. Pipeline walkthrough
1. Intake full texts in chosen categories with section and paragraph locators.
2. Extract cards (agents, validator-gated).
3. Embed cards locally and generate candidates by problem-capability similarity plus assumption compatibility.
4. Expert agents read top candidates in full and write bridges.
5. Gates control promotion.
6. Tests run on the best bridges.
7. The time-split score is reported for the whole run.

## 10. How you use it
- "What in the source field handles heavy-tailed noise, and who needs that?"
- "Show refuted bridges for problem class X and the assumption that broke."
- "Run the time-split evaluation for this configuration."

## 11. Worked example (synthetic)
1. Problem card P (target field, heavy-tailed noise) and capability card C (source field, robust filter) match on embeddings.
2. The source expert notes C's assumption "finite variance"; the target paper's data is Cauchy-like (infinite variance). The bridge is ruled out, and the mismatch is recorded.
3. A second capability with no variance assumption passes; the novelty search finds no paper combining them. The bridge is marked testable.
4. A notebook replicates the target setting, and the error drops. The bridge is marked supported, with the run linked.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Surface vocabulary matches that are unrelated in substance | assumption gate; experts |
| Rediscovering known links | novelty gate |
| Abstracts overselling | fidelity ladder |
| Combinatorial noise | candidate tiering; time-split score |
| Licensing of bulk text | intake respects per-document terms |

## 13. Module reuse
- **As-is:** core modules.
- **Optional module:** discovery (cards, bridges, time split).
- **Adapters:** paper full-text parser with section locators; bulk access within terms; local embedding runner.
