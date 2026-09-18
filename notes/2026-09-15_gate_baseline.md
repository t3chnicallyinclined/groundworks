# 2026-09-15: a green probe report does not mean a strong gate

Generic; no matter content. Module: `groundworks/gates.py`. Found by a second session using the engine, not by the suite.

## What was measured

Deleting one `where` clause from a promotion rule in an instance config. Before and after:

| | with the clause | clause deleted |
|---|---|---|
| `rejected_ok` | 2 | 2 |
| `accepted_ok` | 2 | 2 |
| `misbehaved` | **0** | **0** |
| `wrong_evidence_reject` | `true` | `null` |
| Datastore accepts a fact confirmed on a document-only citation | no | **yes** |

The only visible difference was a flag going from `true` to `null`, which read as "not applicable".

## Why

`probes()` generated the sharper wrong-evidence probe only inside the branch that requires a clause to carry a `where`. Remove the `where` and the probe is never generated, so nothing fails. The report was answering **"does each gate match its own current spec?"** while being read as **"is the spec still strong?"** Those are different questions, and only the second one is worth anything.

## Fixes

1. **`wrong_evidence_reject: null` on a constrained rule is a failure**, naming the constraint the suite stopped testing. `judge()` was extracted from `run_probes` so the test calls the shipped decision rather than a copy of it.
2. **A committed baseline** (`instances/*/gate_baseline.json`) pinning what each rule claims. Fails on a removed rule, a dropped clause, a lowered `min`, or a flag that was `true` and is not. Adding is never a failure. The spec half runs with no database, so it is in the ordinary suite.

## What this generalises to

A suite generated from the same config it tests can only prove internal consistency. To prove *strength* it needs a reference outside the config: either a human-reviewed baseline, or a probe set generated from an older spec. This is the same shape as the relabelling result (`2026-09-15_reading_layer_evaluation.md`, finding 2): nothing inside the artifact contradicts a weakened label, so the check has to come from outside it.

**Verified both ways.** With the clause: green. Without it: the weakened config's own probes still report `misbehaved: 0` (the blind spot, kept in the test on purpose and asserted), the strict probe set catches the datastore accepting wrong evidence, and the baseline catches the edit from the config alone with no server -- naming all three lost clauses.
