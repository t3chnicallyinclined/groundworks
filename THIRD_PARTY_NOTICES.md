# Third-party notices

Every third-party item this repository adapts, depends on, or credits for ideas. Keep current: a new dependency or adaptation is added here in the same change. Policy and reasoning: `docs/LICENSING.md`.

## Adapted (notice and license copy required)

| Item | Author | Source | License | Our files | License copy | Changes |
|---|---|---|---|---|---|---|
| Council of High Intelligence (panel protocol ideas, stance line format, tally arithmetic, two instruction phrases) | nyk | https://github.com/0xNyk/council-of-high-intelligence @ `94f9a90d62cb5963a7ee64340206465e1c86c146` | MIT, Copyright (c) 2026 nyk | `groundworks/panel/` | `groundworks/panel/NOTICE.md` | Stated in the NOTICE |

## Runtime and development dependencies (used, not modified)

| Component | License | How used | Distributed? |
|---|---|---|---|
| SurrealDB server | Business Source License 1.1 (Change Date 2030-01-01, Apache-2.0). Not to be offered as a "Database Service" letting third parties create, manage or control schemas or tables | System of record, separate process over HTTP | Not today. If shipped self-hosted, include its LICENSE conspicuously |
| Python and standard library | PSF License | All scripts | No |
| Copier (copier-org/copier) | MIT | Renders project templates and brings later template changes into generated projects (`groundworks bootstrap` / `upgrade`, through `uvx copier`); not modified, not bundled | No |
| uv (astral-sh/uv) | Apache-2.0 OR MIT | Runs Copier on demand (`uvx copier`); not modified, not bundled | No |
| git | GPL-2.0 | Development tooling (privacy scan, pre-commit hook) | No |
| Rust crate `anyhow` | MIT OR Apache-2.0 | Errors in gw-ops and gw-mcp | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `chrono` | MIT OR Apache-2.0 | Local time in the store supervisor's log and daily export (gw-ops) | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `ciborium` | Apache-2.0 | CBOR encoding for bound query variables (gw-core) | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `clap` | MIT OR Apache-2.0 | The groundworks command line | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `flate2` | MIT OR Apache-2.0 | Gzip of the store's daily exports (gw-ops) | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `fs2` | MIT OR Apache-2.0 | Free space of the store's volume, read by the supervisor (gw-ops) | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `getrandom` | MIT OR Apache-2.0 | Store credentials from OS randomness | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `regex` | MIT OR Apache-2.0 | Config parsing (gw-core), estate scan globs (gw-ops) | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `rmcp` | Apache-2.0 | The Rust MCP SDK: the groundworks MCP server | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `rust-stemmers` | MIT OR BSD-3-Clause | Snowball English stemming in the retrieval ladder's rerank arms (gw-ops), matching the store's analyzer | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `schemars` | MIT | JSON schemas for MCP tool arguments | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `serde` | MIT OR Apache-2.0 | Serialization | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `serde_json` | MIT OR Apache-2.0 | JSON | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `sha2` | MIT OR Apache-2.0 | Hashes: question schemas, quotes, receipts, registry cache | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `thiserror` | MIT OR Apache-2.0 | Library error types | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `tokio` | MIT | Async runtime for the MCP server and commands | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `toml` | MIT OR Apache-2.0 | The registry and groundworks.toml | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `tracing` | MIT | Logging to stderr | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `tracing-subscriber` | MIT | Logging to stderr | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `ureq` | MIT OR Apache-2.0 | HTTP client for SurrealDB | Compiled into binaries built from `rust/`; no binary is distributed |
| Rust crate `wait-timeout` | MIT OR Apache-2.0 | Timeouts on read-only git queries in the estate scan | Compiled into binaries built from `rust/`; no binary is distributed |

## Planned (license checked, not yet used)

| Component | License | Note |
|---|---|---|
| surrealdb.py, surrealkv | Apache-2.0 | Keep license and NOTICE |
| Nimbalyst (Nimbalyst/nimbalyst) | MIT | The Groundworks desktop app is a private copy of it with Groundworks added; none of its code is in this repository. Its LICENSE and notices travel with that copy |
| Other permissive embedding candidates: nomic-embed-text (Apache-2.0), arctic-embed-l-v2.0 (Apache-2.0), mxbai-embed-large-v1 (Apache-2.0) | as listed | **Never jina-embeddings-v3 (CC-BY-NC-4.0)** |

## Model weights used (local only, not distributed)

| Model | License | Use |
|---|---|---|
| BAAI/bge-m3 | MIT | `groundworks/embed` bake-off; vectors in instance 1's derived database |
| Qwen/Qwen3-Embedding-0.6B | Apache-2.0 | `groundworks/embed` bake-off candidate |
| pySHACL, Apache Jena, sentence-transformers, transformers | Apache-2.0 | |
| RDF4J | BSD-3-Clause | |
| FAISS, LadybugDB | MIT | |
| Neo4j Graph Data Science | GPLv3 or commercial | Hosted analytics only; never shipped without a lawyer's review |
| Python MCP SDK, import `mcp` | MIT (package metadata, version 1.26.0) | Used by `groundworks/mcp` |
| PyTorch, import `torch` | BSD-3-Clause | Embedding runtime (separate environment) |
| sentence-transformers, import `sentence_transformers`; transformers, import `transformers` | Apache-2.0 | Embedding runtime |
| NumPy, import `numpy` | BSD-3-Clause | Embedding runtime |

## Ideas only (nothing copied)

| Source | License status | Used for |
|---|---|---|
| karpathy/autoresearch | README says MIT; no LICENSE file | Goal file, fixed evaluator, keep/discard loop |
| Recursive Language Models (Zhang, Kraska, Khattab; alexzhang13/rlm) | MIT per repository page | Corpus queried with code |
| harvard-lil/lawskills-hub | No license grant | Structural vs judged criteria, anti-patterns, null baselines |
| lawve-ai/awesome-legal-skills (the list) | CC BY-NC-ND 4.0 | Pointer to individual skills; per-skill licenses govern |

## Held in the first instance's private repository (must move with their files if brought into the engine)

Four role skills adapted from skills listed in lawve-ai/awesome-legal-skills (premortem, cold bench, red team, claim audit), under Apache-2.0 (one MIT source), each with its own LICENSE and NOTICE. Notice fixes are listed in `docs/LICENSING.md` section 2.
