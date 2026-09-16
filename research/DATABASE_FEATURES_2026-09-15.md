# Database features that could optimize, automate or enhance the Scholar (2026-09-15)

Three research passes read official docs, repos and release notes (SurrealDB 3.x; HelixDB, Neo4j, Memgraph, FalkorDB, Kuzu; TerminusDB, XTDB, Datomic, TypeDB, RDF/SHACL/PROV, Postgres, DuckPGQ, CozoDB). Tags are the researchers' (CONFIRMED = read on an official page). Not independently re-read in this session. One SHACL shape was executed with pySHACL (both ways).

## Headline

**No other database examined enforces conditional, relationship-dependent gates on raw writes** ("status = confirmed requires a cites edge to record-grade evidence"). HelixDB, Neo4j (including Enterprise GRAPH TYPE), Memgraph and FalkorDB offer type, existence, uniqueness or key constraints only; conditional rules need triggers whose abort behaviour is undocumented (UNKNOWN until tested). **SurrealDB stays the system of record.** Others are derived stores at most.

## SurrealDB: features we do not use yet

The first instance runs 3.1.4 on RocksDB and uses ASSERT, DEFINE EVENT and COMPUTED only (no indexes, no changefeeds, no permissions, no functions; lens views computed in Python).

| Feature | Use in the Scholar | Maturity / caveat |
|---|---|---|
| **Upgrade to at least 3.1.6** | 3.1.5 fixed an arbitrary file read via the analyzer `mapper()` filter; 3.1.6 fixed lost LIVE notifications and concurrent index builds orphaned after restart | Re-run gate probes after upgrading |
| **DEFINE INDEX** (plain, COUNT, CONCURRENTLY) | Hook lookups by locator; constant-time health counts; re-index without blocking loaders | GA |
| **REFERENCE ... ON DELETE REJECT / THEN** | Evidence cited by a fact cannot be deleted; THEN can write the archive snapshot | GA since 2.2; top-level record fields only |
| **DEFINE EVENT ... ASYNC RETRY MAXDEPTH** | Truth-maintenance cascades off the loader's transaction, with retries and bounded depth | 3.0+; runs about every 5 s, so **promotion gates stay synchronous ASSERTs**; default MAXDEPTH 3, check against chain depth |
| **CHANGEFEED + SHOW CHANGES** | Durable, resumable work queue: staleness re-checks, archive, audit trail, panel re-prompts | GA; retention must exceed any worker outage; store the last versionstamp; delete before-images only from 3.2 |
| **LIVE SELECT** | Wake workers without polling | Single node; not a queue |
| **FULLTEXT BM25 + HNSW/DiskANN + `search::rrf`** | Hybrid search entirely in the database (roadmap B5) | GA; SEARCH renamed FULLTEXT in 3.0 |
| **DEFINE FUNCTION / DEFINE PARAM** | Gate logic in one place (`fn::can_confirm`); human-owned thresholds as params | GA |
| **DEFINE API** | One low-latency lookup endpoint for hooks | 3.0+ |
| **PERMISSIONS + DEFINE ACCESS (RECORD/JWT/BEARER)** | Per-viewer lenses; per-agent revocable bearer credentials | GA; 3.1 fixed edge-permission bypasses |
| **Recursive paths `.{1..n}` with `+path`, `+collect`, `+shortest`** | "Why is this confirmed" and cascade-reach queries | GA since 2.2 |
| **Table views `AS SELECT`** | Lens roll-ups, health counts, incrementally maintained | Only the FROM table triggers refresh; joined tables do not |
| **VERSION clause** | "What did the graph say on date X" | Needs `surrealkv+versioned` (beta); RocksDB UNKNOWN. **Do not use as the legal audit record yet** |
| **Embedded mode (Python `mem://`)** | Fast gate-probe and panel fixtures | GA |
| Surrealism (WASM), DEFINE BUCKET | Hashing/validators in the database; source files beside units | **Experimental flags; not for core stages** |

**Traps:** loader upsert-then-sweep fires UPDATE/DELETE events and changefeed entries even when nothing changed (guard with `WHEN $before != $after` or hash compare); imports skip events and views (a restored backup does not recompute derived state); COMPUTED bodies can write on 3.1.4 (read-only enforced only from 3.3).

## Patterns to copy from other systems

| Pattern | From | Use | Note |
|---|---|---|---|
| **Bitemporal columns:** valid time (true in the record) and system time (when we learned it); as-of queries | XTDB v2 (MPL-2.0) | "What did we know when the brief was drafted"; supersession history | Copy the pattern into SurrealDB rows |
| **SHACL shapes as the portable gate spec**, validated on exports by Jena (Apache-2.0) or RDF4J ShaclSail (BSD-3) as a drift check | W3C SHACL Rec | Gates and lens Standards in a declarative standard; catches drift between spec and datastore | Validates a state; no cascades; SHACL-SPARQL optional; SHACL 1.2 is a draft. Shape below tested |
| **PROV-O and nanopublication vocabulary** (`wasDerivedFrom`, `wasAttributedTo`, `qualifiedDerivation`/`hadPlan`, `wasInvalidatedBy`; assertion/provenance/pubinfo graphs; content-hashed ids) | W3C PROV-O; nanopub guidelines | Provenance field names and export format; `wasInvalidatedBy` = demotion | Export format, not a store |
| **Branch, diff, apply or discard** | TerminusDB (Apache-2.0) | Improvement-loop candidates as branches with a field-level diff against the baseline | Copy as snapshot tables plus JSON diff |
| **Restrictive (AND) row policies** | Postgres RLS | Tenant isolation model for the product | Owners and superusers bypass unless FORCE |

Tested SHACL Core shape (rejects a confirmed fact citing only agent-grade units or nothing; accepts record-grade citation and non-confirmed facts):
```turtle
ex:ConfirmedFactGate a sh:NodeShape ; sh:targetClass ex:Fact ;
  sh:or (
    [ sh:not [ sh:property [ sh:path ex:status ; sh:hasValue "confirmed" ] ] ]
    [ sh:property [ sh:path ex:cites ;
        sh:qualifiedValueShape [ sh:class ex:Unit ;
          sh:property [ sh:path ex:grade ; sh:in ("page_image" "record_text") ] ] ;
        sh:qualifiedMinCount 1 ] ] ) .
```

## Derived analytics for discovery (Track D)

| Option | Algorithms | License / limits |
|---|---|---|
| Neo4j Graph Data Science, offline batch | Leiden, Louvain, nodeSimilarity, filtered KNN, FastRP, node2vec, Adamic-Adar link prediction | OpenGDS GPLv3; community build max 4 cores, 3 models; results written back as hypotheses only |
| LadybugDB (maintained MIT fork of archived Kuzu) | Embedded columnar Cypher, vector, full-text | MIT; sidecar |
| FalkorDB GraphRAG-SDK | Ontology, extraction, `MENTIONED_IN` provenance edges, local models | SDK Apache-2.0 (adaptable); the database is SSPL |

## Avoid

| System | Why |
|---|---|
| Memgraph | BSL 1.1: internal business use only; cannot be shipped to or exposed to third parties |
| FalkorDB server | SSPLv1: offering it as a service triggers source disclosure of the service stack |
| Kuzu | Archived 2025-10-10 |
| CozoDB | Unmaintained since 2024; open silent-corruption bug in time travel |
| DuckPGQ | Self-described research prototype |
| HelixDB as record | Young (2026 rewrite, v0.3 SDKs); gates only inside application-written queries |
| Replacing SurrealDB for gates | Nothing examined enforces conditional relationship gates natively |

## Measured in use (SurrealDB 3.1.6, 2026-09-15, derived database of 7,995 units)
- FULLTEXT BM25 and HNSW (cosine, 1,024 dimensions) indexes built on 7,995 rows; a hybrid query with `search::rrf` works. Vector top-10 matched the offline ranking 10 of 10 on 5 of 5 queries; full-text top-10 matched 1 to 8 of 10 (different BM25 implementation and analyzer); hybrid 5 to 9 of 10.
- `@1@` matches only when **all** query terms match; a sentence-length query returns nothing. Use `@1,OR@` for any-term matching.
- HTTP request bodies of about 2 MB are rejected (`413 length limit exceeded`); load in batches under about 256 KB.
- Array slice syntax `$vs[0:10]` is a parse error in 3.1.6; slice client-side or use `array::slice`.

## Open (needs a live test)
- Can a Neo4j APOC `before` trigger or Memgraph BEFORE COMMIT trigger abort a write?
- Does VERSION work on RocksDB in 3.1.x?
- Does HelixDB currently embed text itself?

## Sources (official)
SurrealDB: surrealdb.com/docs/surrealql/statements/define/{table,event,field,indexes,analyzer,function,param,api,access,bucket,module,sequence}; /statements/{show,live,select}; /datamodel/{idioms,references}; /functions/database/search; /surrealdb/security/capabilities; /surrealdb/extensions; surrealdb.com/releases/3.0 to 3.3.
HelixDB: github.com/HelixDB/helix-db; docs.helix-db.com (data model, text and vector indexes, advanced, MCP, release notes).
Neo4j: neo4j.com/docs/cypher-manual/current/schema/constraints, /schema/graph-types, /indexes/semantic-indexes; neo4j.com/docs/graph-data-science/current; neo4j.com/docs/apoc/current/background-operations/triggers; neo4j.com/docs/cdc/current.
Memgraph: memgraph.com/docs/fundamentals/{triggers,constraints,transactions}; /advanced-algorithms; /querying/{vector-search,text-search}; licenses/BSL.txt.
FalkorDB: github.com/FalkorDB/FalkorDB; docs.falkordb.com; github.com/FalkorDB/GraphRAG-SDK; github.com/FalkorDB/FalkorDB-MCPServer.
Kuzu / LadybugDB: github.com/kuzudb/kuzu; github.com/LadybugDB/ladybug.
TerminusDB: terminusdb.org/docs/branch-howto, /diff-and-patch-operations, /schema-reference-guide.
XTDB: docs.xtdb.com/quickstart/sql-overview.html. Datomic: docs.datomic.com/reference/filters.html.
TypeDB: typedb.com/docs/typeql-reference/functions/functions-vs-rules; /reference/typeql/annotations.
W3C: w3.org/TR/shacl; w3.org/TR/shacl12-core; w3.org/TR/prov-o; nanopub.net/guidelines/working_draft.
Jena: jena.apache.org/documentation/shacl. RDF4J: rdf4j.org/documentation/programming/shacl.
Postgres: postgresql.org/docs/current/ddl-constraints.html, /ddl-rowsecurity.html, /docs/18/release-18.html; github.com/apache/age; github.com/pgvector/pgvector.
DuckPGQ: duckpgq.org. CozoDB: github.com/cozodb/cozo; docs.cozodb.org.
