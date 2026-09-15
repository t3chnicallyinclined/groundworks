# 2026-09-15: archive bin with retention

## Idea (owner)
Everything that gets destroyed goes to a bin and is kept for a set time.

## Refinement: three retention tiers
Deletion means different things for different data. One timer for everything would either lose knowledge or keep client data too long.

| Tier | What | Retention | Why |
|---|---|---|---|
| **Knowledge** | Facts, ruled-out claims, supersessions, arguments, critiques and answers, decisions | **Never purged.** Prefer a status change over deletion; if deleted, archived with no expiry | Negative knowledge and history are what stop repeat mistakes |
| **Artifacts** | Edges replaced by reloads, old label runs, rebuilt lens filings, resolved review items, caches | **Archived with an expiry** (for example 30 to 180 days per table) | Regenerable or superseded; keeps undo and an audit trail without unbounded growth |
| **Client data on demand** | A matter's corpus, labels and archive entries at engagement end or on a privacy request | **Purged on instruction**, including the archive, with a purge receipt | A client's right to have their material removed; backups need their own policy |

**Legal hold overrides every expiry.** In litigation, destroying material while a matter is pending or reasonably anticipated can be spoliation. An instance for an active matter should start with holds on.

## Proven
Tested on SurrealDB 3.1.4, in a throwaway database. Pattern: `engine/patterns/archive_bin.surql`.

| Check | Result |
|---|---|
| Deleting an edge archives both ends and all its fields | PASS |
| Deleting a label run archives each label with the stated reason ("replaced by run_2") | PASS |
| A deleted fact (knowledge) is archived with no expiry | PASS |
| Artifacts receive an expiry from the per-table policy | PASS |
| Restore recreates the edge from its snapshot | PASS |
| Restore recreates the fact with its status | PASS |
| The purge job removes expired artifacts | PASS |
| A legal hold blocks purge | PASS |
| Knowledge with no expiry is never purged | PASS |

## Design rules
1. **Status over deletion for knowledge.** The archive is a safety net for mistakes, not the normal way to retire a claim. Retiring a claim is `ruled_out` or `superseded`.
2. **Snapshot the whole row.** For edges, both ends. Restore nodes before edges.
3. **Record why.** Deletions carry a reason, so the archive answers "who removed this and why".
4. **Purge is a scheduled, logged job.** No built-in time-to-live was used. The purge runs from a scheduler and reports how many rows it removed; a purge that reports nothing is checked, not assumed.
5. **Same access rules as the live data.** The archive holds the same sensitive material.
6. **Loaders must not churn.** Any loader that deletes and recreates edges on every run floods the archive, the same problem that affects automatic demotion. Loaders upsert edges in place; if one cannot yet, its seeded rows can be excluded from archiving by policy until it is fixed.

## Interaction with automatic demotion
- **Deleting a citation** fires both the demotion rule and the archive rule: the fact is demoted, and the removed citation is kept in the bin. Restoring the citation does not auto-restore the fact's status (demotion is one-way); the review queue records what to re-check.
- **Purging a demotion's cause** from the archive must not delete the review record. Review items are artifacts with their own, longer retention.

## Next steps
1. Decide retention days per table per instance.
2. Default legal hold on for active legal matters.
3. Build the purge-on-demand path with a receipt.
4. Add both-way probes (a delete that must archive; a purge that must not touch held or knowledge rows) to the gate test suite.
