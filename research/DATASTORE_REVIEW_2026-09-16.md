# Datastore review (2026-09-16)

**Question.** Would another datastore or tool fit XScholar better than SurrealDB, or make it run better?

**Method.** Requirements come first, taken from the engine at HEAD `8c6bdcb`. Six research lanes read official sources. The engine's own gate, access and temporal patterns were measured on the installed SurrealDB 3.1.6 (15 transcripts; throwaway servers on ports 8090 to 8098). SQLite was measured through the Python standard library (1 transcript). **No other database was installed.**

Evidence tags:
- **CONFIRMED**: read on a saved page, or measured here.
- **INFERRED**: reasoned from evidence, not read or measured.
- **UNKNOWN**: not established.

Every cited id (`surrealdb-docs-…`, `repro-surrealdb-m3-…`) is an excerpt in the research corpus. The corpus is described in section 10.

---

## 1. Verdict

**Keep SurrealDB as the system of record. Do not switch, and do not add a second database now.** Confidence: **moderate to high**.

Two findings change the work, not the database:

1. **The engine has a measured gate bypass.**
   - A `forbids` clause fails open for any credential that cannot read the rows it forbids.
   - Re-applying the schema after access silently resets permissions on 13 tables and drops 5 change feeds.
   - Both are fixable inside SurrealDB, and both are tested for below.
2. **No database examined lets XScholar hold its own history against its owner.** That includes the verifiable ledgers.
   - What does is a **digest anchored outside the owner's control**, ideally cosigned by an independent witness.
   - That is a small protocol, not a store.

**For versioned schemas and diffs (U3):**
- Dolt is the only genuine candidate, and only as a side store.
- Try the cheaper route first: SurrealDB's `INFO … STRUCTURE` output (measured stable) plus the `VERSION` clause, which is measured working on RocksDB 3.1.6.

**What would change the verdict:**
- A measured case where SurrealDB cannot express a gate that PostgreSQL triggers can. Postgres is the only mature alternative that meets R1.
- The owner deciding that **self-hosted multi-tenant** delivery is the product. Then both the BSL "Database Service" clause and Postgres's role model weigh more.
- A 3.2.x upgrade breaking a measured behaviour the engine relies on, with no workaround.
- A requirement for a third party to verify history without trusting the owner, at a cadence an anchoring protocol cannot meet. Then a transparency log with witnesses becomes a real component.

### The three findings that most change what XScholar should do

| # | Finding | Evidence | Action |
|---|---|---|---|
| 1 | **A `forbids` gate fails open under hidden rows.** A gate ASSERT's subquery runs with the caller's row permissions. `ready = true` was **accepted** from a credential that cannot read `critique`, despite a fatal critique. Root and a credential that can read critiques were refused. The same rule written as a synchronous EVENT refused all three. A `requires` clause fails the other way: it refuses even when the evidence exists (m6 c). | `repro-surrealdb-m7-forbids-visibility-001`, `repro-surrealdb-m7b-forbids-event-001`, `repro-surrealdb-m6-semantics-004` | Refuse at config load any role that writes a gated table without reading every table its clauses traverse, **or** generate `forbids` clauses as events. Add a probe that runs as such a role. The demo is not exposed: its only `forbids` rule also requires an `attacks` edge through the same table, so a blind role fails the `requires` first (INFERRED from `instances/demo-incident/instance.yaml:89-106`, `:193-198`). |
| 2 | **Two silent ways to lose datastore rules.** (a) Re-applying `gates.schema_surql` after `access.access_surql` reset PERMISSIONS on **13 of 19 tables** to NONE and dropped the CHANGEFEED on **5**. This was measured with the engine's own generators on the demo config. It contradicts `xscholar/access.py:30-31`. It fails closed for agents, but it is silent. (b) `POST /import` and `OPTION IMPORT` wrote a confirmed fact with no citation and rewrote a row protected by an append-only THROW event. | `repro-surrealdb-m4-pipeline-001`, `repro-surrealdb-m3b-followup-004`, `repro-surrealdb-m6-semantics-003`; docs `surrealdb-docs-define-table-005` | Emit `DEFINE TABLE … OVERWRITE` with PERMISSIONS and CHANGEFEED inline, or re-apply access and changefeed as part of every schema apply. Compare `INFO FOR DB STRUCTURE` against config after every apply (this is the drift check being built). Treat every import or restore as a gate bypass: re-run the probes and the consistency report afterwards. |
| 3 | **The prior review's headline was wrong, and "independence against root" has no database answer.** PostgreSQL triggers, Dolt (MySQL) triggers, Gel triggers, and Neo4j APOC `before` and Memgraph `BEFORE COMMIT` triggers can all refuse a write conditionally on related rows (per their sources). Every product lets its owner remove the rule. immudb's proofs detect a rewrite only for a verifier that kept an earlier digest. | Section 4; `trillian-tlog-cosignature-001`, `immudb-docs-management-state-005` | Keep SurrealDB for the combination it offers, not for uniqueness. Build the promotion log as a hash chain in SurrealDB (feasible, measured) with its head anchored outside. See section 6. |

---

## 2. Requirements matrix

"Load-bearing" means the engine breaks or loses a guarantee without it.

| ID | Requirement | Where the engine depends on it | Kind |
|---|---|---|---|
| R1 | Conditional gates on raw writes that traverse relations, for every client | `xscholar/gates.py:199-218` (traversal count), `:229-262` (one ASSERT per gated field); DECISIONS 2026-09-15 "Gates enforced in the datastore" | **Load-bearing** |
| R2 | Events that see `$before`, `$after` and `$auth`, and abort with THROW | `xscholar/gates.py:265-306` (independent promotion); `xscholar/temporal.py:88-111` (history events); commit `dff134c` | **Load-bearing** |
| R3 | Server-stamped fields the caller cannot set | `xscholar/gates.py:291-292` (`written_by` VALUE over `$auth`); `xscholar/temporal.py:72-74` (append-only ASSERT) | **Load-bearing** |
| R4 | Per-principal auth: record access, per-table PERMISSIONS, revocation, expiry; system users bypass | `xscholar/access.py:105-129` (DEFINE ACCESS RECORD, ALTER TABLE PERMISSIONS), `:158-166` (revoke), `:53` (8 h tokens); `xscholar/db.py:126-137` (denied write = `[]`) | **Load-bearing** |
| R5 | Unique indexes (exact comparison) | `xscholar/gates.py:138-150`; commit `dff134c` | Load-bearing where declared |
| R6 | Change feed as a resumable work queue | `xscholar/queue.py:65`, `:81-91`, `:282`, `:301-310` (missing feed = error) | **Load-bearing** for re-checks and archive |
| R7 | Typed edges carrying fields; traversal inside gates | `xscholar/gates.py:131` (TYPE RELATION), `:179-206` | **Load-bearing** |
| R8 | Append-only, bitemporal history ("what did the graph say on date X") | `xscholar/temporal.py:43-70`, `:201` (`as_of`), `:234` (`snapshot`) | Load-bearing (currently built on R2 and R3) |
| R9 | Full-text BM25; vectors optional | `xscholar/embed/bakeoff.py:54` (BM25 in Python); DECISIONS 2026-09-15 (BM25 nDCG@10 .470 beat bge-m3 .255) | Nice to have in the datastore (search runs outside today) |
| R10 | Schema read-back for drift detection | `xscholar/gates.py:1076`, `xscholar/queue.py:309` (INFO FOR DB) | **Load-bearing** for the drift check being built |
| U1 | Tamper-evident log the owner cannot rewrite undetected | Not built; hash-chained promotion log being designed | Unmet |
| U2 | Independence that holds against root | Stated limit: `notes/2026-09-16_independent_promotion_and_unique.md:63` | Unmet |
| U3 | Versioned schemas and data diffs | Baseline pins config only (`xscholar/gates.py:767-880`) | Unmet |
| U4 | One binary, no Docker, small footprint, standard-library engine | `pyproject.toml:13` (`dependencies = []`); commit `9957070` (Docker removed) | **Load-bearing** (owner direction) |

---

## 3. Comparison

**Key:** S = supported · P = partly · N = not supported · U = unknown.

A candidate that fails R1 cannot be the system of record.

| Req | SurrealDB 3.1.6 | PostgreSQL 18.6 | SpacetimeDB 2.10.1 | Dolt 2.3.5 | immudb 1.11.2 | SQLite 3.49 (stdlib) | Gel 7.0 | Neo4j CE + APOC | HelixDB 3.2.0 |
|---|---|---|---|---|---|---|---|---|---|
| R1 | **S** (measured) · caveat: `forbids` fails open under hidden rows (m7) | **S** for non-owner roles | **N**: owner raw SQL skips reducers | **S** via triggers + SIGNAL; merges bypass | **N** | P: trigger works (measured); no principals | S for roles without `ddl` | P: `before` trigger aborts (source only) | N |
| R2 | S (measured) | S | P: reducers only | P: `$auth` equivalent UNKNOWN | N | P: no principal | P: triggers run after the write | P: no principal | N |
| R3 | S (measured) | S via trigger | P | P | P: tx metadata | N | S | P | N |
| R4 | S: record access + PERMISSIONS (measured); system users bypass (measured) | S: RLS; owner/superuser bypass | P: public/private; RLS experimental | P: table grants only | P: per-database | N | P: 7.0+ only | N in CE | P: Enterprise key scopes |
| R5 | S (measured, incl. composite) | S | S | S | S | S | S | S | S (nodes) |
| R6 | S (measured) | S: logical decoding | P: no public cursor found | P: binlog | P: TxScan | N | N | N in CE | U |
| R7 | **S** (measured) | N in 18 (SQL/PGQ in 19 beta) | N | N | N | N | **S** | S | S |
| R8 | S: events (built) + VERSION on RocksDB (measured) | P: triggers | P: replay the commitlog | **S**: AS OF | **S** | P | P | U | U |
| R9 | S: FULLTEXT BM25 | N: ranking is not BM25 | N | P: no BM25 | N | **S**: FTS5 bm25() (measured) | U | U | S |
| R10 | S: INFO STRUCTURE (measured stable) | S | S | S | S | S | S | S | U |
| U1 | N (Enterprise audit log only) | N | N: CRC only | P: digests, but owner rewrites | P: proofs for verifiers that kept state | N | N | N | N |
| U2 | N | N | N | N | N | N | N | N | N |
| U3 | P: INFO snapshots + VERSION | N | N | **S** | P: `DIFF OF` | P: session changesets | N | N | N |
| U4 | **S**: single executable, HTTP | N: server + no stdlib driver | P: 3 binaries, RAM-bound | P: single binary, MySQL wire (no stdlib client) | P: single binary, own REST | **S** | N: WSL on Windows | N: JVM | P |
| Licence (§7) | source-available, written boundary | permissive | source-available + 1-instance cap | permissive | source-available | public domain | permissive, **orphaned** | strong copyleft | permissive |

**Cited cells:**

**SurrealDB**
- R1, R2, R3 measured under record and bearer credentials: `repro-surrealdb-m1c-ws-002`. THROW rollback covers side writes (`repro-surrealdb-m6-semantics-001`). ASYNC events cannot abort (`repro-surrealdb-m3-features-004`).
- R4: system users bypass PERMISSIONS (`repro-surrealdb-m1b-probe-002`, `surrealdb-docs-permissions-rls-001`).
- R8: `VERSION` on `rocksdb://…?versioned=true` (`repro-surrealdb-m2e-version-rocksdb-001`).
- R10: `repro-surrealdb-m3b-followup-003`. The docs call STRUCTURE output "subject to change without notice" (`surrealdb-docs-info-001`).
- U1: the hash-chained audit log is Enterprise only: "Community builds never reads them" (`surrealdb-docs-audit-logging-001`).
- U4: "packaged as a single executable file" (`surrealdb-docs-install-windows-001`).

**PostgreSQL**
- "does not support CHECK constraints that reference table data other than the new or updated row" and "a custom trigger can be used to implement that" (`postgres-docs-ddl-constraints-001`, `-002`).
- A trigger "might still abort the entire operation by raising an error" (`postgres-docs-plpgsql-trigger-001`).
- "Superusers and roles with the BYPASSRLS attribute always bypass the row security system" (`postgres-docs-ddl-rowsecurity-001`).
- Replica mode skips triggers but needs superuser or SET privilege (`postgres-docs-runtime-config-client-001`).
- Ranking "do not use any global information" (`postgres-docs-textsearch-controls-002`).
- The Python stdlib has no Postgres driver (`sqlite-python-stdlib-persistence-001`).

**SpacetimeDB**
- "Clients can see public table data but can only modify it by calling reducers" (`spacetimedb-docs-access-permissions-002`).
- The owner's SQL DML path skips reducers: "An extra layer of auth is required for DML" (`spacetimedb-src-sql-execute-001`). That it skips reducers is the lane's reading of the source (INFERRED).
- "By default SpacetimeDB is completely open so that anyone can publish to it" (`spacetimedb-docs-self-hosting-001`).
- The commitlog carries a CRC32C only (`spacetimedb-docs-commitlog-005`).

**Dolt**
- Triggers are "most commonly used to enforce complex constraints" (`dolt-docs-triggers-003`).
- "you can always access the database as a superuser by using `dolt sql`" (`dolt-docs-access-management-001`).
- History can be "reordered, squashed, or dropped" (`dolt-docs-procedures-004`).
- `DOLT_SCHEMA_DIFF()` works between "any two commits" (`dolt-docs-sql-functions-001`).
- `DOLT_HASHOF_DB` covers "all tables (schema and data)… stored procedures and triggers" (`dolt-docs-sql-functions-003`).

**immudb**
- "Application logic is responsible for ensuring data consistency and foreign key constraints" (`immudb-docs-develop-sql-tablescreate-002`).
- A new client takes "a fresh state from immudb server when the client is being initialized for the first time" (`immudb-docs-management-state-005`).

**SQLite**
- Measured: gate both ways, then `DROP TRIGGER` lets anything through; `bm25()` present (`repro-sqlite-m5-trigger-gate-001`, `-002`).
- "no need for an administrator… to assign access permissions to users" (`sqlite-docs-different-001`).

**Gel**
- "Triggers may also be used for validation by calling assert" (`gel-docs-triggers-001`).
- "Gel Data Inc. is shutting down" (`gel-blog-joins-vercel-001`).

**Neo4j**
- APOC rethrows trigger errors inside `beforeCommit` (`neo4j-apoc-src-triggerhandler-001`).
- The current docs prefer `afterAsync` and no longer show a refusing trigger (`neo4j-apoc-triggers-004`).

**HelixDB**
- Now Apache-2.0 (`helixdb-licence-001`); no triggers or conditional constraints found (lane report).

**Also examined, and not a fit for the record:**
- **Memgraph**: BSL; `BEFORE COMMIT` aborts per source (`memgraph-src-interpreter-001`); no Community auth.
- **FalkorDB**: SSPL; unique and mandatory constraints only.
- **TypeDB 3.13**: MPL-2.0; `@card` on `plays` is relationship-dependent but cannot be conditional (`typedb-docs-card-001`).
- **XTDB 2**: `ASSERT` is supplied by the writer (`xtdb-docs-sql-txs-001`).
- **Datomic**: "enforcement is never automatic" (`datomic-docs-schema-constraints-003`).
- **TerminusDB**: schema checks only; high single-maintainer concentration.
- **Doltgres**: 1.0.
- **LadybugDB**: MIT, single-author risk.
- **CozoDB**: unmaintained; open corruption issue.

**Why SurrealDB still wins.** It is the only candidate that has all seven load-bearing properties together in one executable reachable from a standard-library HTTP client:
- R1 gates on a graph traversal
- R2 events with `$auth` that can abort
- R3 stamped writer identity
- R4 record-level principals
- R6 change feeds
- R7 typed edges
- R10 read-back

PostgreSQL matches R1 to R6 and R10 but fails R7 (no graph until 19) and U4. It would also move the engine off `dependencies = []`.

---

## 4. Re-verification of the prior review's claims

The prior document said of itself "Not independently re-read in this session."

| Claim (research/DATABASE_FEATURES_2026-09-15.md) | Now | Evidence |
|---|---|---|
| **Headline:** no other database examined enforces conditional, relationship-dependent gates on raw writes | **Wrong as stated.** Postgres and Gel enforce them through triggers for non-owner roles. Dolt's triggers are MySQL-exact. Neo4j APOC `before` and Memgraph `BEFORE COMMIT` triggers abort the transaction, confirmed in source but not in current docs. What holds is narrower: no graph database has a *declarative* conditional constraint, and none of the alternatives matches SurrealDB's combination. | `postgres-docs-ddl-constraints-002`, `gel-docs-triggers-001`, `dolt-docs-triggers-003`, `neo4j-apoc-src-triggerhandler-001`, `memgraph-src-interpreter-001` |
| "conditional rules need triggers whose abort behaviour is undocumented (UNKNOWN until tested)" | **Answered from source** for Neo4j APOC and Memgraph: they abort. Still undocumented in current Neo4j docs. Not executed here. | as above; `neo4j-apoc41-triggers-001` |
| Upgrade to at least 3.1.6 | **Done, but no longer enough.** Latest stable is **3.2.4**. 3.1.6 is inside two advisory ranges, `< 3.2.0`: GHSA-66r2 (writes inside PERMISSIONS unchecked) and GHSA-848m (DEFINE API scope, High). INFO leaked Argon2 hashes until 3.2.1. | `surrealdb-releases-page-001`, `surrealdb-ghsa-66r2-5gwj-gxm2-001`, `surrealdb-ghsa-848m-r628-vrxw-001`, `surrealdb-releases-3-2-006` |
| REFERENCE … ON DELETE REJECT: "top-level record fields only" | **Holds for record fields; does not protect edges.** REJECT on a record field refused the delete. REJECT defined on a relation's `out` was accepted by the schema, yet deleting the cited unit succeeded and removed the edge. The edge-table delete guard stays necessary. | `repro-surrealdb-m3-features-001`, `repro-surrealdb-m3b-followup-001` |
| DEFINE EVENT ASYNC: promotion gates stay synchronous | **Holds.** An ASYNC event's THROW did not abort; the row existed 7 s later. | `repro-surrealdb-m3-features-004` |
| CHANGEFEED; delete before-images only from 3.2 | **Holds.** | `surrealdb-releases-3-2-001` |
| DEFINE FUNCTION / DEFINE PARAM GA | **Holds, with a new hazard.** A function-plus-param gate works both ways, but `DEFINE PARAM OVERWRITE` loosened the live gate without touching the field definition. The baseline pins neither. | `repro-surrealdb-m3b-followup-002` |
| DEFINE API 3.0+ | **Holds and works** (measured 200/403/500). But it is inside GHSA-848m on 3.1.6, and `$request.body` arrives as bytes. | `repro-surrealdb-m4-pipeline-002`, `repro-surrealdb-m4b-api-errors-001` |
| PERMISSIONS + DEFINE ACCESS (RECORD/JWT/BEARER) GA | **Partly wrong.** The `ACCESS` grant statement "is an experimental feature… Do not rely on this feature in production". A bearer-for-record JWT is **refused on HTTP `/sql` and `/rpc`** on 3.1.6; it works only inside the WebSocket session that signed in. | `surrealdb-docs-statement-access-001`, `repro-surrealdb-m1-bearer-001`, `repro-surrealdb-m1c-ws-001` |
| VERSION needs `surrealkv+versioned` (beta); RocksDB UNKNOWN | **Changed / wrong.** On 3.1.6 `surrealkv+versioned://` refuses to start. The spelling is `?versioned=true`. **RocksDB with `?versioned=true` works**, including as-of graph traversal. Memory, plain surrealkv and plain rocksdb refuse VERSION queries. | `repro-surrealdb-m2-version-001`, `repro-surrealdb-m2b-version-skv-001`, `repro-surrealdb-m2e-version-rocksdb-001`, `surrealdb-docs-cli-start-003` |
| Imports skip events and views | **Holds, and is worse.** Imports also skip ASSERT gates. | `repro-surrealdb-m6-semantics-003`, `surrealdb-docs-define-table-005` |
| COMPUTED can write on 3.1.4; read-only from 3.3 | Not re-measured. **New:** `COMPUTED $auth` is evaluated at read time (it shows the reader), so it cannot replace `written_by`. | `repro-surrealdb-m3-features-003` |
| Table views `AS SELECT` | Not re-measured. **New per docs:** direct writes into a view are accepted on 3.1.x; read-only from 3.2. | `surrealdb-releases-3-2-002` |
| Memgraph BSL: internal use only | **Holds** (wording loose). Change date printed as the invalid "2030-14-09". | `memgraph-licence-bsl-002` |
| FalkorDB SSPLv1 | **Holds** | `falkordb-licence-001` |
| Kuzu archived 2025-10-10; LadybugDB maintained MIT fork | **Holds.** LadybugDB is a copy, not a fork, with one dominant author. | `kuzu-repo-api-001`, `ladybugdb-readme-001` |
| CozoDB unmaintained; open corruption bug | **Holds** (issue #312) | `cozodb-issue-312-001` |
| HelixDB young; gates only in application queries; v0.3 SDKs | **Changed.** v3.2.0; **Apache-2.0** (was AGPL); compiled queries dropped; clients post ad-hoc query trees. | `helixdb-licence-001`, `helixdb-repo-licence-commits-001`, `helixdb-readme-001` |
| Neo4j GDS GPLv3; 4 cores, 3 models | **Holds** | `neo4j-gds-licence-001`, `neo4j-gds-intro-001` |
| TypeDB: functions, annotations only | **Holds but incomplete.** `@card` on `plays` is relationship-dependent. | `typedb-docs-card-001` |
| DuckPGQ research prototype; embedded Python `mem://` | **Not re-verified** (not load-bearing) | none |
| `docs/LICENSING.md`: SurrealDB LICENSE identical at main and v3.1.6 | **Holds** (apart from CRLF line endings) | `surrealdb-licence-v3-1-6-001`, `surrealdb-licence-main` |
| `xscholar/db.py:28-32`: re-apply drops CHANGEFEED; no delete before-image; DB users bypass PERMISSIONS | **All hold** (measured). The re-apply drop happens only with `DEFINE TABLE OVERWRITE` lacking `CHANGEFEED`; FIELD, EVENT and INDEX OVERWRITE and ALTER PERMISSIONS keep it. | `repro-surrealdb-m3-features-007`, `repro-surrealdb-m1b-probe-002` |
| `xscholar/access.py:30-31`: "Re-running the schema after access resets no permissions" | **Wrong** (measured in the engine's own generator) | `repro-surrealdb-m4-pipeline-001` |
| `notes/2026-09-15_per_agent_credentials.md`: bearer grants UNKNOWN whether experimental-flagged | **Answered.** No flag is needed; the docs mark `ACCESS` experimental; unusable from the stdlib HTTP client. | `repro-surrealdb-m1c-ws-001`, `surrealdb-docs-statement-access-001` |

---

## 5. SurrealDB features worth adopting

Each row names the engine code it would replace, with a measured result on 3.1.6.

| Feature | Adopt? | Replaces or closes | Measured |
|---|---|---|---|
| **Gate `forbids` clauses as synchronous EVENTs** (or refuse blind writer roles at config load) | **Yes, now** | The fail-open half of `xscholar/gates.py:209-218` / `:229-262` | Blind role accepted with ASSERT, refused with EVENT (`repro-surrealdb-m7-…-001`, `m7b-…-001`) |
| **Inline PERMISSIONS and CHANGEFEED in `DEFINE TABLE OVERWRITE`**, or re-apply access and feed on every schema apply | **Yes, now** | The false claim at `xscholar/access.py:30-31`; the silent-drop warning at `xscholar/db.py:28-29`; the queue guard at `xscholar/queue.py:301-310` stays as a backstop | 13 tables lost permissions, 5 feeds dropped; OVERWRITE with CHANGEFEED kept the feed (`m4-pipeline-001`, `m3-features-007`) |
| **`INFO FOR DB/TABLE … STRUCTURE`** as the drift source | **Yes** (for the drift check being built) | Hand-parsing DEFINE strings at `xscholar/queue.py:309`, `xscholar/gates.py:1076` | Structured JSON with assert, events, permissions and params; identical across reads (`m3b-followup-003`). Docs: format "subject to change", so pin it to the measured series. On 3.1.6 scrub password hashes before storing (`surrealdb-releases-3-2-006`). |
| **Upgrade to 3.2.4**, then re-measure | **Yes, soon** | Closes GHSA-66r2 and GHSA-848m and the INFO hash leak; delete pre-images would let `xscholar/queue.py`'s image cache (docstring lines 34-45) be dropped | Not measured here. `db.py` would warn, not refuse, on 3.2. **Re-run every measured assumption first:** 3.2 banned writes inside permission clauses and made views read-only. |
| **VERSION on `rocksdb://…?versioned=true`** | **Prototype as a cross-check, not a replacement** | Could verify or replace `xscholar/temporal.py:201` (`as_of`) and `:234` (`snapshot`), and answer as-of for *edges*, which temporal.py does not track | As-of status and traversal correct; survives restart and `REMOVE TABLE`; SurrealQL back-dating did not forge history (`m2e-…-001`, `m2c-forge-001`, `m2d-forge-record-001`). **Not adopted:** retention, storage growth, 3.2 behaviour and bulk-load cost are unmeasured, and root still owns the files. |
| **Composite UNIQUE** | Yes, when a real instance needs it | "Single-field only; composite keys are not built" (DECISIONS 2026-09-16) | Duplicate pair refused, different pair accepted (`m3-features-006`) |
| **Hash chain written by a synchronous event** (`crypto::sha256`, key-sorted object strings) | **Yes, as the promotion log's inner half** | Nothing yet; the hash-chained log being designed | Chain links correctly; `<string>` of an object sorts its keys; a THROW-protected log refuses root's UPDATE and DELETE; `REMOVE EVENT` and import defeat it (`m3-features-005`, `-002`; `m6-semantics-003`). Concurrent writers **not measured**. |
| **Event bodies write tables the caller cannot touch** | Yes (the log table can be `PERMISSIONS NONE`) | n/a | Record user's event wrote a NONE table (`m6-semantics-002`). The docs contradict themselves (`surrealdb-docs-define-event-003` vs `surrealdb-docs-define-table-008`); pin it with a probe. |
| **`--deny-arbitrary-query=record` + DEFINE API as the only door for agents** | **Later, after 3.2** (GHSA-848m) | The MCP front door's "no raw query" promise is enforced only in the server, not the datastore | `/sql` 403 for record users; API 200 for the right role, 403 otherwise; gate refusal surfaces as HTTP 500 (`m4-pipeline-002`, `m4b-api-errors-001`) |
| DEFINE PARAM / FUNCTION for gate logic | **Only if the baseline pins them** | Duplicated clause text in `xscholar/gates.py:209-218` | Works both ways; a PARAM overwrite loosens a live gate (`m3b-followup-002`) |
| **TYPE BEARER FOR RECORD keys** | **No** (for now) | Would replace per-agent passwords in `xscholar/access.py:134-155` | Inside a WebSocket session: $auth, PERMISSIONS, gates, `written_by` and independence all behave. **Over HTTP the token is refused (401)**, and the engine client is HTTP-only (`xscholar/db.py:99-115`). Revocation blocks new sign-ins but did not end an open session. A deleted subject still got a token (`m1-bearer-001/-002`, `m1c-ws-001…004`). |
| REFERENCE ON DELETE REJECT/THEN | Only on record *fields* | Not the edge delete guard | Protects fields; does **not** protect edges (`m3b-followup-001`) |
| ASYNC events | Only for off-transaction cascades | n/a | Cannot abort (`m3-features-004`) |
| Built-in FULLTEXT BM25 | Keep in a derived database, as today | `xscholar/embed/bakeoff.py:54` | Prior measurement (7,995 units); not repeated |

---

## 6. Unmet requirements: does any technology answer them?

### U1: a log the owner cannot rewrite undetected

**No database does this alone.**

| Design | Owner holding all keys caught? | What the verifier must keep | Evidence |
|---|---|---|---|
| Hash chain in SurrealDB, no anchor | No: root can `REMOVE EVENT`, import, or rewrite and re-hash | n/a | `repro-surrealdb-m3-features-002`, `m6-semantics-003` |
| immudb (signed state, Merkle proofs) | Only by a client or auditor that kept an earlier state digest (`Alh`) | An earlier digest | `immudb-security-proofs-003`, `immudb-docs-management-state-005`. The owner holds the signing key (`immudb-security-proofs-004`). |
| Dolt commit hashes / `DOLT_HASHOF_DB` | Only against an external copy of a hash; the owner can rebase, squash and set dates | An earlier hash | `dolt-docs-procedures-004`, `dolt-docs-sql-functions-003` |
| SurrealDB Enterprise audit log | Not a fit: Enterprise only, unsigned, unanchored (INFERRED) | n/a | `surrealdb-docs-audit-logging-001`, `-003` |
| **Transparency-log checkpoint cosigned by independent witnesses (C2SP)** | **Yes, including forks** | Only the witness public keys | `trillian-tlog-cosignature-001`. The public witness network has no production tier yet (`trillian-witness-network-participate-001`). |
| Public Rekor, signed digest | Yes, for what was logged; entries are permanent and public | Sigstore roots plus the entry | `rekor-tiles-clients-001` (signed `hashedrekord` only) |
| OpenTimestamps / RFC 3161 | Stops backdating; **not** two forks stamped at once (INFERRED) | Proof file held by someone other than the owner | `opentimestamps-site-001` |

**Recommendation (INFERRED design, nothing adopted):**
- Write the promotion log as a hash chain by synchronous event (section 5).
- On a schedule, publish its head digest to a party the owner does not control:
  - the client
  - a second machine running a witness
  - an RFC 3161 authority or OpenTimestamps, with proof files handed out
- Borrow the C2SP checkpoint format so a witness can be added later.
- A salted head digest reveals nothing about content.

immudb would add efficient proofs, not independence. It also brings a BSL licence and a second server, so it is not recommended.

### U2: independence against root

- **Not achievable inside any datastore examined.** Every product lets its owner remove the rule:
  - SurrealDB: `REMOVE EVENT`, import
  - Postgres: table owner or superuser
  - Dolt: `dolt sql` superuser
  - SpacetimeDB: owner SQL and republish
- The only thing that holds against root is **a commitment root cannot recall**: an anchored digest (U1), or a second credential held by a person or machine root does not control.
- The engine's current limit statement (`notes/2026-09-16_independent_promotion_and_unique.md:63`) is correct and should stay.

### U3: versioned schemas and data diffs

- **Dolt genuinely answers it** (`DOLT_SCHEMA_DIFF`, `dolt_commit_diff_*`, `AS OF`).
- Its costs:
  - a second server
  - the MySQL wire protocol, with no stdlib client
  - a sync process
- The cheaper candidate inside the current stack:
  - `INFO … STRUCTURE` snapshots diffed against config and against the last snapshot (measured stable)
  - plus VERSION as-of reads for data (measured on RocksDB)
- **Decide by prototype** (section 8).

### U4: operational simplicity

**SurrealDB already meets it:** one executable, HTTP from the standard library. Only SQLite matches it; SQLite fails R1 and R4. Everything else adds a JVM, WSL, a MySQL or Postgres client library, or several binaries.

---

## 7. Licence verdicts

Classified against `docs/LICENSING.md` section 6.

| Product | Licence | Text read? | Class | Verdict for XScholar |
|---|---|---|---|---|
| SurrealDB 3.1.6 / main | BSL 1.1, Change Date 2030-01-01; no "Database Service" letting third parties "create, manage, or control schemas or tables" | **CONFIRMED** (`surrealdb-licence-v3-1-6-001…004`) | Source-available | **Allowed** under the existing written boundary |
| SpacetimeDB 2.10.1 | BSL 1.1; grant limited to "no more than one SpacetimeDB instance in production" and no Database Service; Change Date 2031-09-08 to AGPL-3.0 with a linking exception. Docs folder Apache-2.0 | **CONFIRMED** (`spacetimedb-licence-002`, `spacetimedb-licence-docs`) | Source-available | **Not allowed** without a commercial licence: one production instance conflicts with "many projects" (INFERRED) |
| immudb ≥ 1.9DOM.2 | BSL 1.1; no hosted or embedded use "to compete with Codenotary's paid version(s)" | **CONFIRMED** (`immudb-licence-001`, `-002`) | Source-available | Needs a written boundary; not recommended |
| Dolt / TerminusDB / HelixDB / Gel / Trillian Tessera | Apache-2.0 | **CONFIRMED** (`dolt-licence-001`, `terminusdb-licence`, `helixdb-licence-001`, `gel-licence`, `trillian-tessera-licence`) | Permissive | Allowed with notices |
| Doltgres | Apache-2.0 | INFERRED (GitHub API only) | Permissive | Allowed; read the text before use |
| PostgreSQL | PostgreSQL License | **CONFIRMED** (`postgres-licence-002`) | Permissive | Allowed |
| SQLite | Public domain | **CONFIRMED** (`sqlite-docs-copyright-002`) | Public domain | Allowed (already a runtime per LICENSING.md) |
| XTDB / TypeDB | MPL-2.0 | **CONFIRMED** (`xtdb-licence`, `typedb-licence`) | Weak copyleft | Dependency only, unmodified |
| OpenTimestamps libraries | LGPL-3.0 | **CONFIRMED** (`opentimestamps-python-licence`, `-client-licence`) | Weak copyleft | Dependency only, unmodified; the stdlib engine would reimplement the protocol rather than import it (INFERRED) |
| Neo4j CE, GDS | GPL-3.0 | **CONFIRMED** (`neo4j-community-licence`, `neo4j-gds-licence`) | Strong copyleft | Hosted only, after review |
| Memgraph | BSL 1.1 (internal business use) | **CONFIRMED** (`memgraph-licence-bsl-001…003`) | Source-available | Avoid |
| FalkorDB | SSPL v1 | **CONFIRMED** (`falkordb-licence-001`) | Source-available | Avoid |
| Datomic | Binaries Apache-2.0 per vendor FAQ; closed source | INFERRED (no LICENSE file read) | Permissive binaries | Not needed |
| LadybugDB (MIT), CozoDB (MPL-2.0), APOC (Apache-2.0) | as stated | INFERRED (GitHub API only) | n/a | Not needed |

---

## 8. Hands-on prototypes (at most two; the owner approves any install)

| # | Prototype | Install needed | The measurement that decides it |
|---|---|---|---|
| 1 | **SurrealDB 3.2.4 on `rocksdb://…?versioned=true`**, running the full suite, gate probes and baseline, plus VERSION as-of against `temporal.as_of` | A second SurrealDB binary kept apart from the 3.1.6 install; nothing else | **Adopt the upgrade** if the probes stay at 0 misbehaved and the baseline is unchanged, and each `xscholar/db.py:28-32` assumption is re-measured with its result recorded. **Adopt VERSION as a cross-check** if as-of status agrees with `temporal.as_of` on every tracked row of the demo after a scripted history of 1,000 or more changes, and storage growth and load time stay within a stated bound (the owner sets the bound). Also re-run m7 and m4 on 3.2.4. |
| 2 | **Dolt 2.3.x as a U3 side store**: nightly export of the gate schema, `INFO … STRUCTURE` and gated rows into Dolt commits; diff two baselines | The Dolt single binary, plus a MySQL client for the sync. It is not stdlib, so the sync runs outside the engine. | **Adopt** only if, on three real schema and gate changes, Dolt's diff names a weakening that the committed gate baseline plus an `INFO STRUCTURE` JSON diff does **not**, or does so with materially less code. If the JSON diff catches all three, Dolt is not worth a second server. |

**Not recommended for a prototype:**
- PostgreSQL: R7 and U4 are known failures, and a switch has no measured problem to solve.
- SpacetimeDB: R1 and licence.
- immudb: adds proofs, not independence.

The witness or anchor design for U1 needs no install to start. A stdlib SHA-256 chain head, handed to a second machine, is enough to test the protocol.

---

## 9. Checked and did not help

- **Bearer keys over HTTP.** A bearer-for-record JWT was refused on `/sql`, on HTTP `/rpc`, and via WebSocket `authenticate` on a new session. Signing in fresh inside each WebSocket session works, but the engine client is HTTP (`repro-surrealdb-m1-bearer-001`, `m1b-probe-001`, `repro-surrealdb-m1c-ws` transcript).
- **REFERENCE ON DELETE REJECT on a relation's `out`.** Accepted by the schema, enforced nothing (`m3b-followup-001`).
- **`COMPUTED $auth` as writer stamp.** Evaluates as the reader (`m3-features-003`).
- **ASYNC event as a gate.** Cannot abort (`m3-features-004`).
- **`surrealkv+versioned://`.** Server refuses to start on 3.1.6 (`m2-version-001`).
- **Back-dating writes with `CREATE … VERSION`.** Accepted but invisible in the past. `INSERT` over an existing id is refused; `UPDATE`/`UPSERT … VERSION` are parse errors. This was tried as an attack, and no forgery resulted (`m2d-forge-record-001`).
- **A THROW-protected append-only table as tamper evidence.** Stops SurrealQL edits, including root's, but not `REMOVE EVENT` or import (`m3-features-002`, `m6-semantics-003`).
- **`--deny-arbitrary-query=record` with functions over WebSocket `run`.** "Method not allowed". Only DEFINE API remained open (`repro-surrealdb-m3b-followup` transcript, `m4-pipeline-002`).
- **SQLite as a record store.** The trigger gate works both ways, but `DROP TRIGGER` from any connection removes it (`repro-sqlite-m5-trigger-gate-001`).
- **Candidates considered and set aside at the reading stage:**
  - SpacetimeDB: R1 via owner raw SQL; licence
  - HelixDB: no gates
  - Gel: orphaned; WSL
  - XTDB and Datomic: opt-in checks
  - Irmin, lakeFS, Nessie: wrong kind of store
  - Amazon QLDB: support ended 2025-07-31
  - Azure confidential ledger and SQL Server ledger: cloud-only or not a single binary

**Not measured, and still open:**
- concurrency of an event-written hash chain
- whether 3.1.6 has the savepoint atomicity bug fixed in 3.3.0-beta.1 (`surrealdb-releases-3-3-002`); the one shape tested rolled back cleanly (`m6-semantics-001`)
- why an open session's `SELECT count()` returned 0 after its grant was revoked
- Neo4j and Memgraph trigger aborts, which were read in source, not run

---

## 10. The corpus

**Location.** `research_corpus/datastores/`, in the working session's scratch directory, **outside every repository**. Format: `research_corpus/FORMAT.md`. Nothing from it is committed here.

**Files:**
- `sources.jsonl`: 337 sources
  - by grade: 281 vendor, 37 primary, 16 reproduction, 3 independent
  - by capture: 168 `raw`, 168 `rendered_text`
- `excerpts.jsonl`: 686 verbatim excerpts, each with locator, topics and a separate `reads_as`. Reproduction excerpts carry `measured`.
- `originals/<sha256>.txt`: 333 files, about 7.6 MB. Every quote was re-checked against its original (0 failures), and every original against its hash.

**Public use.**
- 64 sources are `public_ok`. They are pages under a licence that allows quoting: Apache-2.0 repo docs (SpacetimeDB `docs/`, HelixDB, Dolt, TerminusDB, Tessera, Rekor), MIT, and MPL-2.0.
- The 16 reproduction transcripts are this project's own and could be published once any credentials in them are removed. The throwaway keys and hashes in them are dead.
- **Everything else is vendor documentation with no stated licence and is private research only.** That includes all SurrealDB docs: the docs repo declares no licence (`surrealdb-docs-repo-metadata-001`).
- **Caveat:** `public_ok` for repo docs assumes the repository licence covers its docs folder. SpacetimeDB's docs licence was read; the others were not individually checked.
