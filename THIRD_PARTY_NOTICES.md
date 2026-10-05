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
| git | GPL-2.0 | Development tooling (privacy scan, pre-commit hook) | No |

## Planned (license checked, not yet used)

| Component | License | Note |
|---|---|---|
| surrealdb.py, surrealkv | Apache-2.0 | Keep license and NOTICE |
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
| Cloudflare/clef-flash (9B decision model, post-trained from Qwen/Qwen3.5-9B) | Apache-2.0, following the base model | `groundworks/decide` runtime; weights pulled to the local Hugging Face cache, never redistributed |
| **clef-flash's own head implementation, import `joint_schema_model`** -- shipped *inside* the model repository, not published to any package index | Apache-2.0 (same repository LICENSE) | `groundworks/decide/clef.py` adds the snapshot directory to `sys.path` and imports it, because the typed-decision head has no implementation outside that file. **This is third-party code executed from a model download, not a package from an index**: it is pinned by the snapshot revision, and any revision change should be read before it is used |
| huggingface_hub, import `huggingface_hub` | Apache-2.0 | Model download for `groundworks/decide` and `groundworks/embed` |
| accelerate, import `accelerate`; safetensors, import `safetensors` | Apache-2.0 | `decide` runtime |
| torchvision, import `torchvision` | BSD-3-Clause | Required by the clef-flash processor (undocumented upstream); `decide` runtime |
| Pillow, import `PIL` | MIT-CMU | Image inputs to `decide`; optional |

## Ideas only (nothing copied)

| Source | License status | Used for |
|---|---|---|
| karpathy/autoresearch | README says MIT; no LICENSE file | Goal file, fixed evaluator, keep/discard loop |
| Recursive Language Models (Zhang, Kraska, Khattab; alexzhang13/rlm) | MIT per repository page | Corpus queried with code |
| harvard-lil/lawskills-hub | No license grant | Structural vs judged criteria, anti-patterns, null baselines |
| lawve-ai/awesome-legal-skills (the list) | CC BY-NC-ND 4.0 | Pointer to individual skills; per-skill licenses govern |

## Held in the first instance's private repository (must move with their files if brought into the engine)

Four role skills adapted from skills listed in lawve-ai/awesome-legal-skills (premortem, cold bench, red team, claim audit), under Apache-2.0 (one MIT source), each with its own LICENSE and NOTICE. Notice fixes are listed in `docs/LICENSING.md` section 2.
