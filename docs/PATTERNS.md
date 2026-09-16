# Patterns

Designs that came out of running a real instance and that any instance can adopt. **None of them needs an engine change.** Each one is declared in `instance.yaml` under `tables:`, `edges:` and `gates:`, so it gets schema, gate traversals, both-way probes, cleanup and per-role permissions from the same code as the built-in tables.

Each pattern gives the problem it solves, the config, what was verified, and its limits.

---

## Contentions: what someone asserts, and what the record answered

### The problem

An evidence-gated graph that holds only evidence **loses the question the evidence was gathered to answer**.

On the first real instance the owner kept saying *"we figured these out already, why did we lose them"*. It was treated as a memory problem for weeks. It was structural. Every argument began as something the owner had said. That statement got worked into a report, the report was superseded, and the statement went with it. The graph held facts, arguments and dead ends, but **nothing held "what this person asserts, and what the record answered"**.

The result was the same ground dug over and over, because nothing recorded that it had already been dug.

### The config

```yaml
tables:
  contention:
    fields:
      said:        string        # their own words, verbatim and short, so the row is provably theirs
      asserts:     string        # the same point restated in the record's terms
      disposition: {type: string, one_of: [verified, partly, open, not_in_record, ruled_out]}
      why:         {type: string, optional: true}   # what the record actually showed
      use_for:     {type: string, optional: true}   # what this row may be used to support
      never_for:   {type: string, optional: true}   # what it must never be used to support

edges:
  rests_on:   {from: contention, to: fact}         # the evidence behind a disposition
  raised_for: {from: contention, to: conclusion}   # the argument it was raised for

gates:
  - id: contention_verified_needs_a_confirmed_fact
    table: contention
    field: disposition
    value: verified
    requires: [{edge: rests_on, to: [fact], where: "status = 'confirmed'", min: 1}]
```

Grant `contention`, `rests_on` and `raised_for` to the roles that may write them in `access:`.

### Why each field is there

- **`said` is verbatim and short.** A paraphrase is ours, not theirs. Keeping the words short keeps them checkable.
- **`not_in_record` is a first-class result, not a failure.** A row that says `not_in_record` and records in `why` what was searched **stops the same ground being dug again**. It needs no evidence, which is correct: absence is the finding.
- **`not_in_record` and `ruled_out` are different findings.** One says the record is silent. The other says the record answers, and answers against. Merging them loses exactly the distinction this table exists to make.
- **`use_for` / `never_for` are what make a `partly` row usable.** This is measured, see below: `partly` is where most rows land. A partly-supported assertion is a hazard unless it says what it may and may not be relied on for.

### What was verified

On a throwaway SurrealDB 3.1.6, with the demo instance's vocabulary:

| Case | Result |
|---|---|
| Rests on a **proposed** fact → `verified` | **refused** |
| Rests on a **confirmed** fact → `verified` | accepted |
| A disposition outside the declared list | **refused** |
| `not_in_record` with a `why` and no evidence | written, correctly |
| Generated probes for the rule (`gates --apply`) | **4 refused, 4 accepted, 0 misbehaved**, and **red** when the gate is loosened |

The generated probe is only possible because of V2-18 (staged evidence, `docs/ROADMAP.md`). The rule's evidence, a *confirmed* fact, is itself behind a gate. Before that fix no probe could create it, and the rule was guarded by the committed baseline and hand-written probes alone.

### What it measured, the first time it was run

A harvest over **423 messages** from one person produced 174 raw contentions, merged to 124 canonical ones. Of the 106 written before the run was cut short:

| Disposition | Count |
|---|---|
| verified | 21 |
| **partly** | **70** |
| open | 6 |
| not_in_record | 4 |
| ruled_out | 5 |

About one in five of what the person insisted on was fully borne out. **Two thirds were borne out in part.** Fewer than one in ten were absent from the record or contradicted by it. `partly` does most of the work, which is why `use_for` / `never_for` belong in the pattern and not only in that instance.

**Caveat on those numbers.** At least one `ruled_out` row rested partly on an item being "not shown received" in evidence. That turned out to be wrong for thousands of items, because a rebuild had silently reverted a correction -- a defect this engine had too, see `docs/ROADMAP.md`. The `ruled_out` count is likely a slight overstatement. Treat it as measured on a graph that was then found to carry a defect.

### Limits

- A contention's `said` is only as good as whoever extracted it. The table records a claim; it does not validate the extraction. A harvest benefits from the same label-agreement measurement as any other labelling (`xscholar labels agreement`).
- `partly` covers a wide range. Consider whether your instance needs it split.
