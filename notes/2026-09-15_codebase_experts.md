# 2026-09-15: scholar over a codebase, producing self-updating project experts

## The idea

Run the scholar over an entire repository and produce **project experts**. Each expert is an agent that knows how the system actually works, cites exact lines, remembers dead ends, and updates itself as the code changes.

This is the most natural domain for the method, for one reason: in code, the evidence changes and the change is observable. Every fact can be pinned to a span of code at a commit, and a diff says exactly which facts may no longer hold. That gives **automatic demotion** (truth-maintenance propagation), which the re_kb architecture lists as its largest known gap.

Partial versions already exist and are maintained by hand: the re_kb graph plus hand-written agent charters with CONFIRMED/INFERRED tags. This design automates:
- coverage
- staleness detection
- the knowledge sections of the charters

## The eight answers for a codebase instance

| # | Question | Answer |
|---|---|---|
| 1 | Goal | Answer "how does this work and what breaks if I change it" correctly, with citations; change the system safely |
| 2 | Unit | Symbol (function, type, constant), file, test, commit, doc paragraph, issue or review comment, runtime trace |
| 3 | Fidelity ladder | **reproduction** (a test or trace run, a measurement) > **code** at a pinned commit > in-repo docs and comments > commit messages and review discussion > agent summary > recollection. Only the top two confirm |
| 4 | Status axis | **live** (the cited span's content hash matches HEAD) / **stale** (changed since confirmation) / **gone** (span deleted) |
| 5 | Vocabulary | The project's own architecture document and module names, transcribed; a derived module and call map |
| 6 | Gates | Confirmed requires a live pinned span or a passing test; a fact whose spans go stale is demoted automatically; "safe to remove" requires coverage evidence |
| 7 | Roles | Architect, newcomer explainer, security reviewer, performance reviewer, on-call (incidents), migration planner |
| 8 | Evaluator | A frozen question bank with verified answers and citations; past wrong answers as regression entries; time-split tests (given the repo before commit N, does the expert predict which facts a later change invalidates, or which files a later bug fix touches) |

## Locators and staleness

- **Locator:** `path:line_start-line_end@commit` plus a hash of the span's normalized content.
- **Symbol spans, not just line spans.** Hash the parsed definition (function body, type) so reformatting or moved lines do not create false staleness. Line spans are the fallback for non-code files.
- **On every commit:**
  1. `git diff` names the changed spans.
  2. The facts citing them are set to `stale`, with the commit that staled them.
  3. The changed units are queued for incremental re-reading.
- **A stale fact is never shown as confirmed.** The hook shows it as "was true at <commit>; re-check".

## The self-update loop

```
commit / merge
  -> diff -> changed spans (symbol-hash compare)
  -> facts citing them: confirmed -> stale        (automatic, in the datastore)
  -> re-read only changed units (incremental labels, validator-gated)
  -> agent re-verifies each stale fact: re-confirm with new span | rule out with evidence | supersede
  -> regenerate the expert charter's GENERATED sections from the graph
  -> human approves the charter diff              (the fence)
  -> hooks and agents serve the updated knowledge
```

## What an "expert" is, concretely

A charter file (for Claude Code, an agent definition) in two parts:

1. **Human-owned:** role, principles, standards, what never to do, how to verify. Agents may propose edits; a human approves.
2. **Generated** (machine-owned, regenerated from the graph, every line cited):
   - the module map
   - confirmed facts for the areas the expert owns
   - dead ends ("tried X; it does not work because Y")
   - currently stale facts awaiting re-check
   - open questions

   Budgeted to a fixed size, with links to the graph for the rest.

Plus the hooks: when a tool call names a path, symbol or address the graph knows, the expert's known facts and dead ends for it are injected.

## Self-improvement for code experts (inside the fence)

- **Agents may change:** labeling guides, question-bank additions, charter wording proposals, routing, chunking.
- **Humans own:** the gates, the staleness rule, the fidelity ladder, the frozen question bank's existing answers, and secret-handling rules.
- **Every wrong answer an expert gives becomes a new regression entry.** The evaluator only grows.

## Costs and risks

- **Cost:** the first full read costs roughly the size of the repo. After that, cost is proportional to the diff.
- **Churn:** refactors can stale many facts at once. Symbol hashing and "moved, not changed" detection reduce this; re-verification can be batched overnight.
- **Charter bloat:** generated sections need a hard size budget, with the graph as overflow.
- **Secrets:** code and history can contain credentials. Scan units before any text is stored or sent to a model, and redact.
- **False confidence:** a test that passes without exercising the cited path is not reproduction. Coverage of the cited span is required for the reproduction grade.

## Pilot candidate

A repository we own that already has a hand-maintained graph and charters, so the generated experts can be compared against the hand-written ones. Measure:
- question-bank accuracy
- stale-fact detection precision
- charter drift
