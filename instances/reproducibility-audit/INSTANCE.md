# Reproducibility audit: establish whether published results hold

**Status:** spec. **Examples are synthetic** (fictional paper "Sparse Adapters Improve Recall").

## 1. When to use it
Reviewers, labs and funders want to know whether the claims in a paper, or a set of papers, are supported by its data and code. Cost of confident error: building on a result that does not replicate.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | For each headline claim: reproduced, partially reproduced, not reproduced, or not checkable |
| 2 | Unit | Claim sentence; figure or table cell; code cell or script; dataset file |
| 3 | Fidelity | our re-run output > authors' released outputs > paper figure or table > paper text > agent summary |
| 4 | Status | artifacts available / partially available / unavailable |
| 5 | Vocabulary | The paper's claims list, extracted verbatim; reproduction outcome categories |
| 6 | Gates | "Reproduced" needs a re-run output within a stated tolerance of the reported number; a figure-to-text mismatch is a finding; "not reproduced" needs the attempted configuration recorded |
| 7 | Roles | Authors' view (defensible deviations); statistician; skeptic |
| 8 | Evaluator | Deterministic comparison of re-run and reported numbers; environment capture |

## 3. Unit (synthetic)
```json
{"id":"tab2_row3_recall","type":"table_cell",
 "locator":{"paper":"Sparse Adapters","table":"2","row":"Ours","col":"Recall@10"},
 "text":"0.742",
 "status":"artifacts_available","grade":"paper_table"}
```

## 4. Evidence model
- A re-run is reproduction.
- The paper's own numbers are claims to be checked, not evidence of themselves.

## 5. Framework lens
Claims list. Matrix: claim by reproduction status by deviation size.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Reproduced needs a re-run within tolerance | "reproduced" citing Table 2 itself | after the re-run gives 0.739 with tolerance 0.01 recorded |
| Not reproduced needs configuration | "not reproduced" with no environment or seed record | after the configuration is recorded |
| Mismatch is a finding | text says 0.76, table says 0.742, ignored | after the mismatch is recorded |

## 7. Roles and lenses
- **Roles:** authors' red team (seeds, preprocessing); statistician cold bench (variance across seeds).
- **Lenses:** reviewer report; lab (configs and scripts).

## 8. Evaluator
Deterministic compare; multiple seeds; environment hash.

## 9. Pipeline walkthrough
Extract claims and numbers, intake code and data, run in a captured environment, compare, label outcomes, red team, then report.

## 10. How you use it
- "Which headline claims reproduce within tolerance?"
- "What differed in the configurations that failed?"

## 11. Worked example (synthetic)
1. The re-run gives 0.739 against the reported 0.742, within tolerance, so the claim is reproduced.
2. The ablation claim "+4 points" re-runs at +1.2 over five seeds, which is outside tolerance: not reproduced, with configuration recorded.
3. Authors' red team: "requires warmup schedule from appendix". The re-run with warmup gives +3.8, so the claim is marked partially reproduced and the dependency is recorded.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Single-seed results | statistician |
| Undocumented preprocessing | configuration record |
| Text and table mismatches | finding gate |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** paper table parser; sandboxed runner with environment capture; numeric comparator.
