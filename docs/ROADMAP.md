# Roadmap

**Written 2026-09-15.** The first instance (a private appellate matter) has priority over everything here until its brief is filed. Tasks in this repo proceed where they serve that instance or do not compete with it.

Each task is done when its acceptance test passes.

## Track 0: First instance (private, not in this repo)
The instance runs in its own private repository. This repo receives only generic lessons and measured numbers from it.

## Track B: Optimize the engine

| # | Task | Acceptance |
|---|---|---|
| B1 | Label quality measurement: independent double-labeling on samples, adjudication, agreement reported in health | Agreement rates printed on every health run |
| B2 | Status-aware skipping: unusable units get status-only labels | Cost per unit reported and lower than a full read |
| B3 | Tiered model routing: cheap or local model for tier-0 triage, random audit | Audit agreement and cost per unit reported |
| B4 | Content hashing and incremental relabel | Adding one document relabels only that document |
| B5 | Local embeddings plus HNSW and BM25 hybrid search | Returns located units; no text leaves the machine |
| B6 | Cost and time accounting per stage | Every run records tokens, minutes, units |
| B7 | Session-start health hook (silence must not look like "nothing known") | A stopped database produces a warning |
| B8 | SurrealDB at 3.1.6 or later in every instance (security and index-build fixes) | **Done in instance 1, 2026-09-15:** all probe suites identical before and after; backups kept |
| B9 | Indexes on every field hooks, lenses and health filter; COUNT indexes for health | Hook round trip measured before and after (instance 1 baseline about 300 ms) |
| B10 | Evidence protected from deletion while cited (`REFERENCE ... ON DELETE REJECT` or `THEN` to archive) | Probe: deleting cited evidence rejected; uncited evidence deletable |
| B11 | Change feed as the durable work queue (staleness re-checks, archive, audit, panel re-prompts); LIVE SELECT only to wake workers | A worker stopped and restarted resumes from its stored versionstamp with nothing missed |
| B12 | Bitemporal columns on claims (valid time in the source vs time learned) | "What did the graph say on date X" answered by query and checked against an export from that date |
| B13 | Embedding bake-off per instance against a fixed retrieval evaluator (`engine/embed`) | Metrics with intervals for keyword, each model, and hybrid; a model is adopted only if it clears the keyword baseline. **First run, instance 1, 2026-09-15: BM25 won; no model adopted** (DECISIONS) |
| B14 | Paraphrase evaluation for vectors: queries that do not reuse source wording (human-written or model-paraphrased with a check that no 5-word run is shared), plus the eliminated-claim warning task (does a reworded claim retrieve its eliminated twin?) | Same metrics and intervals; vectors adopted for a task only where they clear keyword search on it |

## Track C: Extract the engine

| # | Task | Acceptance |
|---|---|---|
| C1 | Instance config file: the eight answers in one file | Schema, validator vocabulary and role skills generated from it |
| C2 | Generic modules: intake, chunker, validator, loader, gate library, lens builder, hooks, eval harness | A toy instance builds from config with no engine edits |
| C3 | Matter-identifier scan in CI | Build fails if an engine file contains an identifier from any private instance's deny-list |
| C4 | Public demo instance (appeals: a public record from a published decision) | End-to-end run with zero client data |
| C5 | Methods brain: pipeline over `research/READING_LIST.md` | Method claims stored with verbatim quotes and source rank; adoption requires primary source plus reproduction |
| C6 | Distinct reasoning method per panel seat (from Council of High Intelligence, `notes/2026-09-15_council_review.md`) | Each seat's lens declares a reasoning method; `lock` fails when two seats share one |
| C7 | Lens charters carry "sees" and "misses" and name a counterweight lens | Validator fails a charter missing either field, or a counterweight that is not reciprocal |
| C8 | Drift guard between `instance.yaml`, generated skills and prose docs (roster-validator pattern) | CI fails when a seat, lens, option or category in one is absent from another |
| C9 | Enforcement counts in every stage's report (validator repairs, gate rejections, re-prompts), as the panel already does | Every run report prints its enforcement counts next to its pass count |
| C10 | Measure whether panel anonymization changes outcomes | Same targets run masked and unmasked; stance changes and objection diversity reported |
| C11 | MCP server slice 1: generic server over the typed core with an instance adapter; read, critique and panel tools; provenance stamped by the server; no raw query, delete or schema tools (`engine/mcp`) | Tool tests pass against a legal and a non-legal synthetic adapter; the first instance registers its adapter |
| C12 | MCP slice 2: hybrid search and eliminated-claim similarity in the datastore, per-agent bearer credentials with permissions (seat isolation makes blind rounds enforceable), change-feed notifications | A seat credential cannot read other seats' round-1 answers before anonymization (probe both ways) |
| C13 | SHACL shapes as the portable, declarative copy of each instance's gates; exports validated as a drift check | Every gate has a shape; a planted drift between shape and datastore gate is reported |
| C14 | Provenance export in PROV-O and nanopublication form | Every derived row exports with author, lens, reasoning type and derivation |

## Track D: Pilots

| # | Task | Acceptance |
|---|---|---|
| D1 | Cross-field discovery on one or two arXiv categories: problem and capability cards; gated bridges | Validator-clean cards; bridges held as hypotheses until tested |
| D2 | Time-split evaluator for D1 | Hit rate against a stated baseline |
| D3 | Second domain instance from config (candidates: systematic review; incident postmortem) | Built without engine edits |
| D4 | Self-improvement loop on labeling guides against the frozen evaluator | Kept changes logged with evaluator deltas; human-approved |
| D5 | Codebase instance producing self-updating project experts (notes/2026-09-15_codebase_experts.md): symbol-hash locators, automatic staleness on commit, incremental re-read, generated charter sections with human-approved diffs | On a repo with hand-written charters: question-bank accuracy, stale-fact detection precision, and charter drift reported against the hand-written baseline |

## Track E: Product

| # | Task |
|---|---|
| E1 | Final name after a search for existing marks and products |
| E2 | Positioning: a professional's verification and record system; the professional signs |
| E3 | Security and privacy posture: data handling, no training on client data, per-viewer scopes |
| E4 | Buyer interviews: appellate boutiques, public defender appellate units; then research groups |
| E5 | License audit of every borrowed artifact |
| E6 | Multi-tenant system of record decision (SurrealDB vs Postgres with extensions) |
