# Codebase experts: keep project experts correct as the code changes

**Status:** spec (roadmap D5; design note `notes/2026-09-15_codebase_experts.md`). **Examples are synthetic** (a fictional service, `ledgerd`).

## 1. When to use it
A repository is too large for any one person or context window. Its knowledge lives in scattered docs, commit history and people's heads. Hand-written expert notes go stale silently, and agents re-derive facts or repeat approaches that already failed. The cost of confident error: broken production, wasted days, security holes.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Correct, cited answers to "how does this work" and "what breaks if I change it", kept current |
| 2 | Unit | Symbol (function, type, constant); file; test; commit; doc paragraph; review comment; runtime trace |
| 3 | Fidelity | reproduction (test or trace exercising the cited path) > code at a pinned commit > in-repo docs and comments > commit messages and review threads > agent summary > recollection |
| 4 | Status | live (cited span's content hash matches HEAD) / stale (changed since confirmation) / gone |
| 5 | Vocabulary | The project's own architecture document and module names, transcribed; derived module and call map |
| 6 | Gates | Confirmed needs a live pinned span or a covering test; facts auto-demote to stale when a cited span changes; "safe to remove" needs coverage evidence |
| 7 | Roles | Architect; newcomer explainer; security reviewer; performance reviewer; on-call; migration planner |
| 8 | Evaluator | Frozen question bank with verified cited answers; past wrong answers as regressions; time split (predict which facts a later commit invalidates, which files a later fix touches) |

## 3. Units and locators (synthetic)
```json
{"id":"sym_ledgerd_apply_batch","type":"symbol",
 "locator":{"path":"src/ledger/apply.rs","symbol":"apply_batch","lines":"118-164","commit":"a1b2c3d"},
 "text":"pub fn apply_batch(...) { ... if batch.len() > MAX_BATCH { return Err(TooLarge) } ... }",
 "status":"live","grade":"code","hash":"sha256:ast:9f..."}
```
Hash the parsed definition, not raw lines, so formatting and moved code do not create false staleness.

## 4. Evidence model
- **Reproduction** confirms only if coverage shows the test actually executed the cited span.
- **Code** confirms at its commit; its status must be `live` at HEAD.
- **Docs and commit messages** are attestation: they point, they do not confirm.

## 5. Framework lens
The project's architecture doc items, transcribed. Synthetic example: "Ingest", "Validation", "Apply", "Settlement", "Reporting". The crosswalk comes from path prefixes and labels (`src/ledger/apply*` files under Apply). Matrix: facts per component (confirmed, stale, open) and dead ends per component.

## 6. Gates (with probes)
| Rule | Rejected write | Then accepted |
|---|---|---|
| Confirmed needs a live span or covering test | fact "apply_batch rejects batches over MAX_BATCH" confirmed citing only a README line | after citing `sym_ledgerd_apply_batch` (live) |
| Stale facts cannot be confirmed | re-confirming after `apply.rs` changed, still citing the old hash | after re-citing the new span |
| Safe-to-remove needs coverage evidence | `remove_ok` on `legacy_settle()` with no call-graph or coverage cite | after linking the call-graph query result and a coverage report showing zero hits |

## 7. Roles and lenses
- **Roles:** architect (design questions), newcomer (explanations with citations), security (trust boundaries, input handling), performance (hot paths with measurements), on-call (incident questions with log locators), migration planner (dependency impact).
- **Lenses:** by component, by owner, by risk (security-relevant symbols), and a "stale" lens listing what must be re-checked.

## 8. Evaluator
- **Mechanical:** probes; staleness correctness (change a cited span in a test repo and require demotion); question bank answers must cite live spans.
- **Judged:** answer quality on the question bank.
- **Outcome:** time split on real history.

## 9. Pipeline walkthrough
1. Intake: parse the repo at a commit into symbols, files, tests, docs, commits.
2. Chunk by module and label (component, role, risks, invariants, reason, quote).
3. Validate and load.
4. Expert charters are generated. The human-owned part holds principles and standards; the generated part holds the module map, confirmed facts, dead ends, stale list and open questions, each cited and size-budgeted.
5. Hooks inject facts when a tool call names a path or symbol.
6. On each commit: diff, then span hashes, then demote affected facts, then re-read changed units, then re-verify, then regenerate charter sections, then a human approves the diff.

## 10. How you use it
- "What happens when a batch is too large?" returns the answer with `src/ledger/apply.rs:131@HEAD` and the test that proves it.
- "What breaks if I delete `legacy_settle`?" returns the callers, coverage, and the dead end "tried removing in commit e4f5; reporting job failed."
- "What went stale this week?" returns the facts demoted by recent commits.

## 11. Worked example (synthetic)
1. Fact F1 "apply_batch rejects batches over MAX_BATCH (1000)" is confirmed on `sym_ledgerd_apply_batch@a1b2c3d` plus test `apply_rejects_oversize`.
2. Commit `d9e8f7a` changes MAX_BATCH to 5000. The span hash changes, so F1 is demoted to stale by that commit.
3. The re-read agent sees the new constant, supersedes F1 with F2 "... over MAX_BATCH (5000)", and re-links the test.
4. The generated charter section updates; the human approves.
5. The hook now returns F2, and shows F1 only in history.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Docs that describe intended rather than actual behavior | ladder: docs cannot confirm |
| Tests that pass without touching the cited code | coverage requirement for reproduction |
| Refactors that stale everything | symbol hashing; moved-not-changed detection |
| Secrets in code or history | privacy guard before storing or sending |
| Charter bloat | size budget with graph overflow |

## 13. Module reuse
- **As-is:** chunker, validator, loader, gate library, write surface, lens builder, hooks, roles, evaluator, improvement loop.
- **Adapters:** language parsers for symbol extraction and hashing; git diff to span mapping; coverage report reader.
- **Optional module:** staleness.
