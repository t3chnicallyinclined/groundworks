# Decisions (method level)

Matter-specific decisions live in the private instance repository, not here.

## Decided

| Date | Decision | Why |
|---|---|---|
| 2026-09-14 | Exhaustive, tiered reading instead of retrieval sampling | Coverage must be provable; retrieval cannot say what it missed |
| 2026-09-14 | Agents read the corpus with code in batches (Recursive Language Model style) | Keeps agent context lean; scales past context windows |
| 2026-09-15 | Gates enforced in the datastore, proven by probes in both directions | Wrapper-only rules are conventions; raw access bypasses them |
| 2026-09-15 | SurrealDB as system of record for small instances | ASSERT with subqueries enforces gates on raw access; built-in HNSW and BM25; proven in instance 1 |
| 2026-09-15 | Agent labels are artifacts with an explicit grade, never conclusions | Bulk ingest never writes conclusions |
| 2026-09-15 | Separate evidence axes: fidelity, status, authority | A true statement from an unusable source must be caught |
| 2026-09-15 | Lenses are projections plus lens-keyed overlays; evidence is never edited per viewer | Many perspectives without copies; disagreement preserved |
| 2026-09-15 | The domain's authoritative framework, transcribed from its source, is the primary lens vocabulary | Experts judge against that framework |
| 2026-09-15 | Ruled-out claims are displayed as "ELIMINATED CLAIM (false)" | An agent read a ruled-out statement as asserted |
| 2026-09-15 | Self-improvement allowed only inside the fence (METHOD.md s.15); evaluator, gates, ladders, privacy rules are human-owned | autoresearch's read-only evaluator rule; guards against Goodhart and silent loosening |
| 2026-09-15 | Licensing: adapt MIT or Apache-2.0 only, with NOTICE and LICENSE; ideas only from AGPL, unlicensed, non-commercial | Clean product path |
| 2026-09-15 | Engine and matter separated; this repo holds no client data | Privacy; a sellable engine |
| 2026-09-15 | Working name XScholar; final name deferred | Method first |

## Open

| Question | When |
|---|---|
| Label quality threshold that makes a labeled corpus "trusted as a map" | After first agreement measurement |
| Cheap-model triage: which model, what audit rate | Track B3 |
| Evaluator for domains with no ground-truth outcome | Before the self-improvement loop runs outside law |
| SurrealDB vs Postgres for multi-tenant | Track E6 |
| Whether HelixDB supports constraint-level gates or only endpoint-level | Before using it as anything but a serving index |
| Final name | Later |
