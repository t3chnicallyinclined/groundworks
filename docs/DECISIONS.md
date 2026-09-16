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
| 2026-09-15 | Lens anatomy fixed as six parts (Charter, Frame, Standard, Crosswalk, Overlay, View), two kinds (perspective, discipline) and a backing grade (prompt < source < graph < calibrated); METHOD s.7 | Names to reason with; a persona-only lens must never pass for an expert |
| 2026-09-15 | Adopt five panel rules adapted from Council of High Intelligence (MIT): blind then anonymized rounds; anti-conformity with a verbatim flaw quote; panel locked before answers; stance line never inferred; dissent quota, novelty and agreement checks. Enforced by a program (`engine/panel/panel.py`), not by the coordinator. 18 unit tests pass, each check tested both ways; nine deliberate breakages of the checks were each caught by the tests | Several model seats otherwise converge socially; the source enforces its rules by instruction only, which can pass without doing its work |
| 2026-09-15 | Reasoning method per seat, lens sees and misses with counterweights, a config drift guard, and enforcement counts in every report go on the roadmap (C6 to C9), not built now | Useful, but not needed for the first instance's red team and cold bench |
| 2026-09-15 | Not adopted: historical personas, a vote treated as truth, a chairman's prose synthesis, seats on other providers where privacy rules forbid | A panel result is agent-grade overlay; gates decide |
| 2026-09-15 | Licensing policy by license class (`docs/LICENSING.md` s.6) replaces "adapt only MIT or Apache-2.0"; `THIRD_PARTY_NOTICES.md` plus `scripts/license_check.py` at commit | Owner approved all licensing recommendations; permissive BSD/PostgreSQL dependencies were already needed |
| 2026-09-15 | Licensing never blocks optimization. SurrealDB features are used fully; a commercial SurrealDB license is bought before any feature gives customers schema control or database access | Owner: "if customers need this then we eventually get a license" |
| 2026-09-15 | XScholar is a universal scholar; appeals is the first instance only. Engine names and contracts are domain-free; every engine feature is tested with a non-legal synthetic instance (CLAUDE.md rule 9) | Owner direction |
| 2026-09-15 | Embedding models: permissive only (never jina-embeddings-v3, CC-BY-NC-4.0); the model is chosen by a bake-off on the instance's own data against a fixed retrieval evaluator, not by leaderboard | Method rule: adoption needs a reproduction in our pipeline |
| 2026-09-15 | **Vectors not adopted for search in instance 1 yet.** Bake-off (`engine/embed`) on 7,995 in-record units, 92 confirmed-fact queries: BM25 nDCG@10 .470 [.398,.549]; bge-m3 dense .255; Qwen3-Embedding-0.6B dense .183; RRF hybrid with bge-m3 .376. No model or hybrid cleared the keyword baseline. Keyword search stays the default; bge-m3 vectors are kept in a derived database for the next test | The fixed evaluator decides, not expectation. Caveat: 69 of 92 queries quote the record verbatim, which favors keyword search; on the 23 paraphrased queries recall was indistinguishable. Next test needs a paraphrase query set and the eliminated-claim use case |
| 2026-09-15 | MCP server as a front door over the typed core: generic engine server plus instance adapter; no raw query, delete or schema tools; server stamps provenance | Agents use the full system without bypassing gates or attribution |

## Open

| Question | When |
|---|---|
| Label quality threshold that makes a labeled corpus "trusted as a map" | After first agreement measurement |
| Cheap-model triage: which model, what audit rate | Track B3 |
| Evaluator for domains with no ground-truth outcome | Before the self-improvement loop runs outside law |
| SurrealDB vs Postgres for multi-tenant | Track E6 |
| ~~Whether HelixDB supports constraint-level gates~~ Answered 2026-09-15 from its docs: no (type and uniqueness only; conditions only inside application-written queries). Same for Neo4j, Memgraph, FalkorDB. SurrealDB stays the record (`research/DATABASE_FEATURES_2026-09-15.md`) | Closed; trigger-abort behaviour of Neo4j and Memgraph still untested |
| Final name | Later |
| Should `panel_io` submit and status move from `engine/mcp` into `engine/panel` (core logic outside the front door)? | Before MCP slice 2 |
| Per-agent identity: one MCP launch identity is shared by every agent in a session until slice 2 adds per-agent datastore credentials; interim is one MCP entry per seat | MCP slice 2 (C12) |
| Should roles restrict writes (for example a critic may not answer its own critique)? | Before panels run at scale |
| Superseded claims are shown with the same "ELIMINATED CLAIM (false)" label as ruled-out claims in instance 1; a distinct superseded label is likely needed | Instance 1 owner, with the hook |
