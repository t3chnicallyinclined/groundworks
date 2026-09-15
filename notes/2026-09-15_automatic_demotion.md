# 2026-09-15: automatic demotion inside the graph database

## Question
The largest known gap in the method: a fact confirmed on some evidence stays confirmed when that evidence is later downgraded or removed. The gate checks at the door, not over time. Can this be enforced by the graph database itself?

## Answer: yes, proven
Tested on SurrealDB 3.1.4, in a throwaway database on the same server (created and removed; no instance data touched).

Pattern file: `engine/patterns/tms_propagation.surql`.

| Check | Result |
|---|---|
| Two facts confirmed on record-grade passages | PASS |
| Downgrading passage p1 automatically sets fact f1 (cited only p1) to `stale`, with reason "evidence demoted: passage:p1" | PASS |
| Fact f2 (cited p1 and p2) stays confirmed: one valid justification is enough | PASS |
| The conclusion resting on f1 automatically becomes not-ready, with reason "support demoted: fact:f1" | PASS |
| A computed `effective_status` field agrees with the event-driven status | PASS |
| Removing f2's remaining valid citation demotes f2 | PASS |
| Restoring p1's grade does **not** re-confirm f1 (demotion is one-way; a human re-confirms) | PASS |

## Two mechanisms, and when to use each

**Computed field (derived on read).** `effective_status` is recomputed from the current evidence every time it is read.
- **Strength:** it can never drift, cannot be bypassed by any write path, and is immune to loaders.
- **Cost:** evaluated at read time, so it needs indexes at scale.
- **Limit:** it has no memory. It does not record when or why a fact became stale.

**Events (triggers on write).** Rules fire when a passage's grade changes, a citation is deleted, or a fact leaves confirmed, and they cascade.
- **Strength:** an audit trail with a timestamp and reason, and one-way demotion that forces human re-confirmation.
- **Risk found by design review:** the instance's seed loader deletes its own edges and recreates them on every re-apply. With these events installed, a routine re-apply would stale every fact mid-load.

**Recommendation: use both, in separate roles.**
- The **computed field is the truth** that hooks, lenses, gates and reports read.
- **Events write history,** appending a demotion record, rather than silently mutating the stored status.
- Where a one-way stored demotion is wanted, loaders must stop deleting and recreating edges. They should upsert edges by identity, or run inside one transaction whose final state is checked.

## This is truth maintenance, and what is still missing

This implements the core of a justification-based truth maintenance system:
- a belief is **in** while at least one of its justifications is valid
- it goes **out** automatically when the last one fails
- the change propagates to beliefs that depend on it

Not yet covered:
- **Every citation type.** The prototype counts passages only. An instance must count every table its gate accepts (decision pages, documents, units, sources for authorities).
- **Authorities.** When a source's strength is downgraded, `verified` should demote by the same pattern.
- **Nonmonotonic justifications** ("X unless Y"). Not modeled.
- **Cycles and cascade depth.** Not tested beyond two levels.
- **Performance at scale.** Events fire on every matching write; needs measurement with bulk loads.
- **Re-promotion policy.** One-way here by choice. An assumption-based design could re-confirm automatically when evidence returns; decide per instance.

## Next steps
1. Add `stale` to the status vocabulary in the gate (a human-owned change).
2. Install the computed field first in any instance, since it carries no loader risk.
3. Fix loaders to upsert edges before installing events.
4. Probe tests in both directions for each event: a demotion that must happen, and a non-demotion that must not.
