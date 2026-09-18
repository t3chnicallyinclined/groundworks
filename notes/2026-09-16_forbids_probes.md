# 2026-09-16: half of every gate expression was never probed

Generic; no matter content. Module: `groundworks/gates.py`. Found by an architect pass reviewing the
probe generator, left as a design decision, then decided and implemented.

## What was measured

A gate rule has two halves: what it `requires` and what it `forbids`. Only the first was ever tested.
`fix` satisfied `requires`; the sharper `wrong` probe fired only for `cites` clauses; nothing anywhere
created the row a `forbids` clause excludes and checked that the datastore refused the write.

Deleting **both** `forbids` clauses from the demo config, applying those gates to a throwaway server,
and running the *strict* instance's probes against it:

| | before this change | after |
|---|---|---|
| `rejected_ok` | 2 | 2 |
| `accepted_ok` | 2 | 2 |
| `misbehaved` | **0** | **1** |
| `forbidden_evidence_reject` on the readiness rule | — | `false` |
| Datastore promotes a conclusion carrying a fatal critique | **yes** | **yes** (and now it is reported) |

Only the committed baseline caught it, and only from the config. The probe run, which is what CI
reads first, said the gates behaved.

## What was built

For every `forbids` clause, a **forbidden-evidence probe** generated from the clause alone:

- `edge` gives the table at the other end and the direction of the `RELATE`.
- `where` -- expanded through `_expand()` exactly as the gate expression is -- gives the field values,
  via a parser that handles a conjunction of `field = literal` and `field IN [literals]` and **nothing
  else**.
- `min` gives how many rows to write.

It then runs both ways: write the row, ask the datastore *with the clause's own predicate* whether
what was written really matches (`evidence_present`), require the promotion to be **refused**, remove
the row, confirm the traversal is empty again (`cleared`), and require the same promotion to be
**accepted** (`restored_accept`). Refusal on its own proves nothing -- a gate that refused the write
under every condition would pass it.

## The rule that matters

A clause the generator cannot build a row for is **reported**, never skipped. That is the whole
lesson of 2026-09-15: the wrong-evidence probe was generated inside an `if`, so deleting the `where`
stopped generating it and nothing failed. `judge()` now fails a rule that forbids something and
produced no forbidden-evidence result, naming the clause and the reason -- the same treatment
`constrained` / `missing_probe` already gives the other half.

## What this does not do

- `forbids` is compiled to `count(...) = 0` regardless of `min`, so one row already trips any clause;
  writing `min` rows is belt-and-braces, not a second semantics.
- The `fix` critique is hard-coded `survivable` / `answered = true`. A config that forbade *that*
  verdict would make the accept step fail -- loudly, as `accept: false`, not silently -- but the
  generator does not yet choose a verdict no clause forbids.
