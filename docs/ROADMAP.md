# Roadmap

**Written 2026-09-15.** The first instance (a private appellate matter) has priority over everything here until its brief is filed. Tasks in this repo proceed where they serve that instance or do not compete with it.

Each task is done when its acceptance test passes.

## Track 0: First instance (private, not in this repo)
The instance runs in its own private repository. This repo receives only generic lessons and measured numbers from it.

## Track B: Optimize the engine

| # | Task | Acceptance |
|---|---|---|
| B1 | Label quality measurement: independent double-labeling on samples, adjudication, agreement reported in health | Agreement rates printed on every health run. **Harness built 2026-09-15 (`xscholar/pipeline/agreement.py`, `xscholar labels agreement A B`): per-field agreement, tier within-one, Cohen's kappa for tier and direction, set overlap for issues and targets, a disagreement list and the relevance-split count for adjudication. First run on the demo instance (notes/2026-09-15_first_agent_run.md): agents vs stub on 63 log lines, tier kappa 0.93, direction kappa 0.88, relevance split 2 of 63. Two independent agents with human adjudication on a real instance is still owed** |
| B2 | Status-aware skipping: unusable units get status-only labels | Cost per unit reported and lower than a full read |
| B3 | Tiered model routing: cheap or local model for tier-0 triage, random audit | Audit agreement and cost per unit reported |
| B4 | Content hashing and incremental relabel | Adding one document relabels only that document |
| B5 | Local embeddings plus HNSW and BM25 hybrid search | Returns located units; no text leaves the machine |
| B6 | Cost and time accounting per stage | Every run records tokens, minutes, units |
| B7 | Session-start health hook (silence must not look like "nothing known") | A stopped database produces a warning. **Done 2026-09-15 (`xscholar/hooks/health.py`, tested): a stopped database, a missing instance and an unusable config each print a WARNING line; a healthy graph prints its counts and drift.** The generic pre-tool lookup hook (`xscholar/hooks/lookup.py`) explains unit and claim ids named in a tool call and flags an eliminated claim about to be repeated |
| B8 | SurrealDB at 3.1.6 or later in every instance (security and index-build fixes) | **Done in instance 1, 2026-09-15:** all probe suites identical before and after; backups kept |
| B9 | Indexes on every field hooks, lenses and health filter; COUNT indexes for health | Hook round trip measured before and after (instance 1 baseline about 300 ms) |
| B10 | Evidence protected from deletion while cited (`REFERENCE ... ON DELETE REJECT` or `THEN` to archive) | Probe: deleting cited evidence rejected; uncited evidence deletable |
| B11 | Change feed as the durable work queue (staleness re-checks, archive, audit, panel re-prompts); LIVE SELECT only to wake workers | A worker stopped and restarted resumes from its stored versionstamp with nothing missed |
| B12 | Bitemporal columns on claims (valid time in the source vs time learned) | "What did the graph say on date X" answered by query and checked against an export from that date |
| B13 | Embedding bake-off per instance against a fixed retrieval evaluator (`xscholar/embed`) | Metrics with intervals for keyword, each model, and hybrid; a model is adopted only if it clears the keyword baseline. **First run, instance 1, 2026-09-15: BM25 won; no model adopted** (DECISIONS) |
| B14 | Paraphrase evaluation for vectors: queries that do not reuse source wording (human-written or model-paraphrased with a check that no 5-word run is shared), plus the eliminated-claim warning task (does a reworded claim retrieve its eliminated twin?) | Same metrics and intervals; vectors adopted for a task only where they clear keyword search on it |

## Track C: Extract the engine

| # | Task | Acceptance |
|---|---|---|
| C1 | Instance config file: the eight answers in one file | Schema, validator vocabulary and role skills generated from it. **Done 2026-09-15: schema, gates, probes and validator vocabulary (`xscholar/config.py`, `xscholar/gates.py`), and role skills with their drift guard (`xscholar/skills.py`, `xscholar/drift.py`, V2-5)** |
| C2 | Generic modules: intake, chunker, validator, loader, gate library, lens builder, hooks, eval harness | A toy instance builds from config with no engine edits. **Done 2026-09-15 except the lens builder: `instances/demo-incident` runs end to end from its config (`scripts/run_instance.py`), with hooks and the agreement harness; lens views are still the case instance's Python** |
| C3 | Matter-identifier scan in CI | Build fails if an engine file contains an identifier from any private instance's deny-list |
| C4 | Public demo instance (appeals: a public record from a published decision) | End-to-end run with zero client data |
| C5 | Methods brain: pipeline over `research/READING_LIST.md` | Method claims stored with verbatim quotes and source rank; adoption requires primary source plus reproduction |
| C6 | Distinct reasoning method per panel seat (from Council of High Intelligence, `notes/2026-09-15_council_review.md`) | Each seat's lens declares a reasoning method; `lock` fails when two seats share one |
| C7 | Lens charters carry "sees" and "misses" and name a counterweight lens | Validator fails a charter missing either field, or a counterweight that is not reciprocal |
| C8 | Drift guard between `instance.yaml`, generated skills and prose docs (roster-validator pattern) | CI fails when a seat, lens, option or category in one is absent from another. **Done 2026-09-15 (`xscholar/drift.py`, V2-5)** |
| C9 | Enforcement counts in every stage's report (validator repairs, gate rejections, re-prompts), as the panel already does | Every run report prints its enforcement counts next to its pass count |
| C10 | Measure whether panel anonymization changes outcomes | Same targets run masked and unmasked; stance changes and objection diversity reported. **First result 2026-09-15: no measurable effect, n=1 adversary panel;** repeat on peer panels and more targets |
| C15 | Substance-level dissent check: objections that differ only in category but say the same thing do not count as distinct (embedding similarity with a validated threshold, or a fixed judge), advisory until validated | On a labeled set of objection pairs, precision and recall reported; category-only quota kept as the floor |
| C17 | Reading layer plus reviewer evaluation (full vs speedread, planted errors, lookup budget) | Preservation 100% in every mode, tested both ways; catch rate for errors planted in established rows reported beside the overall rate, with false alarms and lookups, in both arms. **First result 2026-09-15 (notes/2026-09-15_reading_layer_evaluation.md): preservation 100%; catch 4/15 full vs 3/15 speedread; 0 of 12 relabelling errors caught in either arm. Speedread not adopted as default** |
| C18 | Inline "still passes the promotion rule" marker: re-run each instance's gate over every rendered established row and show pass, fail or not-checkable next to it, with a summary count | On seeded status upgrades and verdict softenings, the marker flags them without any lookup; measured against the C17 baseline of 0 of 12. **First result 2026-09-15 (notes/2026-09-15_inline_rule_recheck.md): built as policy `rules` + `features.check_rules` + a "Fails its own rule" section first; preservation still 100% in every mode. On the three C17 documents all 3 status upgrades and all 3 verdict softenings are flagged, but only 4 of those 6 are flagged *because of* the plant (3 status upgrades, 1 answered-flip); the other 2 were already flagged by a standing rule. Precision depends entirely on the declared rules: the locator rule flagged 0 clean rows and exactly the planted upgrade in each document, while the answered-link rule flagged 31-38% of all rows**. **Reviewer round on the tuned documents: 5/15 caught with 0 false alarms (plain 4/15, rendered-without-rules 3/15), but status upgrades stayed 0/3 although the marker flagged all three. A flag that says only "fails a rule" is skipped; wording is the next variable (C20)** |
| C20 | Marker wording: state the claim's label, what its evidence supports, and the one action that resolves it; and give every section a count the reader can reconcile against the totals the document states | Same planted documents and lookup budget: status-upgrade catch rate reported against the 0/9 baseline across all arms |
| C19 | Group repeated citations of the same source in a rendering (the behaviour reviewers actually use to find errors), with a hand-checkable count per section | On the C17 documents, contradicting repeats are adjacent; measured catch rate for locator and number errors beats the C17 baseline |
| C16 | Anchoring vs stability test for panels: zero stance changes in 18 seats on instance 1 | Same targets with seats given different evidence slices or different permitted models; stance-change rate compared |
| C11 | MCP server slice 1: generic server over the typed core with an instance adapter; read, critique and panel tools; provenance stamped by the server; no raw query, delete or schema tools (`xscholar/mcp`) | Tool tests pass against a legal and a non-legal synthetic adapter; the first instance registers its adapter |
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

## v0.2 (started 2026-09-15; baseline tag v0.1.0b1)

Each item ships with tests both ways, a non-legal synthetic proof, and a line in CHANGELOG.

| # | Item | Acceptance |
|---|---|---|
| V2-1 | **Lens builder from config** (`xscholar/lens/`): each `lenses:` entry becomes a datastore view; a framework lens files labels under transcribed framework nodes; `lens_view` MCP tool | A lens view returns nothing its own standard excludes (probe both ways); the framework matrix counts for and against per node |
| V2-2 | **Bitemporal claims** (B12): `valid_from`/`valid_to` (true in the source) and `learned_at`/`status_history` (when we knew) on facts, written by events; an as-of query helper | "What did the graph say on date X" returns the status held then, checked against a pre-change export |
| V2-3 | **Per-agent credentials** (C12): a datastore user per launch identity with table and row permissions from the config (readers cannot promote; a seat writes critiques only under its lens; nobody but the loader writes labels) | Probes both ways per role; the MCP server launches with a per-agent credential instead of root |
| V2-4 | **Change-feed work queue** (B11): CHANGEFEED on claim and critique tables; a worker that stores its versionstamp and resumes after a stop with nothing missed; first consumers: stale re-check and archive | Stop the worker mid-stream, restart, every change processed exactly once |
| V2-5 | **Role skills and a drift guard generated from config** (C1 remainder, C8): `.claude/skills/<role>/SKILL.md` per role from templates plus the instance vocabulary; CI fails when a seat, lens, option or category in the config is absent from a generated skill or a doc | Regenerate is idempotent; a planted mismatch fails the guard. **Done 2026-09-15 (`xscholar/skills.py`, `xscholar/drift.py`, `xscholar/skills_templates/`, `tests/test_skills_drift.py`):** five templates; every skill filled from the config (goal, holder, vocabulary, seat charters with `backing`/`signed`, panel options and categories, the `panel.py` sequence with `--settings`, gates in plain words, MCP tools filtered by `access:`, privacy); an untemplated role is a named error; two header lines and a `content-sha256` per file; generation byte-identical twice; `xscholar skills generate`, `xscholar drift`, a CI step and the last step of `xscholar test`; demo skills in `instances/demo-incident/skills/`. Prose docs are opt-in per invocation (a doc with another instance's worked examples fails by design) |
| V2-6 | **Compose packaging** (tier 1): `Dockerfile`, `compose.yaml` with the SurrealDB image, health checks, an export cron, an optional GPU profile | A fresh machine runs the demo with one compose command |
| V2-12 | **Gate baselines** (from a defect found in use): treat `wrong_evidence_reject: null` on a rule that constrains its evidence as a failure, and diff every run against a committed `gate_baseline.json` | **Done 2026-09-15 (`xscholar/gates.py`, `instances/demo-incident/gate_baseline.json`, `tests/test_gate_baseline.py`):** `misbehaved: 0` only ever meant "each gate matches its own current spec"; deleting a `where` from a promotion rule stopped the sharper probe being generated and the run stayed green while the datastore began accepting a fact confirmed on a document-only citation. `judge()` now fails a constrained rule with no wrong-evidence result, naming the constraint; `spec()`/`compare_baseline()` fail a removed rule, a dropped clause, a lowered `min` or a flag that was true and is not (adding is never a failure); `--write-baseline` / `--baseline` (exit 1 weakened, 2 baseline missing), the spec half in the ordinary suite with no server and the flag half in CI. Every server suite now refuses a port already in use instead of borrowing a stranger's database |
| V2-13 | **Probe the other half of the gate expression**: a forbidden-evidence probe per `forbids` clause | **Done 2026-09-16 (`xscholar/gates.py`, `tests/test_gates.py`, `notes/2026-09-16_forbids_probes.md`):** built from the clause alone (`edge` gives the table and RELATE direction, `where` the field values, `min` the count); runs both ways -- write the row, verify with the clause's own predicate that it really matches, require refusal, remove it, require the same promotion to be accepted. A clause that cannot be turned into a row is reported untested and fails `behaved`. **Limit, measured:** deleting a `forbids` clause from the config still leaves an ordinary run green (`misbehaved: 0`, exit 0) -- a suite generated from a config cannot notice the config changed; the baseline catches that (exit 1, both clauses named). Not changed: `forbids` ignores `min`; the accept step's critique verdict is hard-coded |
| V2-14 | **Checks that could not fail**: CI, the demo's verdict, the privacy scan | **Done 2026-09-16 (`.github/workflows/ci.yml`, `xscholar/cli.py`, `scripts/privacy_scan.py`, `scripts/run_instance.py`, `tests/test_run_verdict.py`):** CI ran under `bash -e` without pipefail, so `curl | sh` exited 0 on failure, SurrealDB was absent, every database suite skipped and `unittest` printed `Ran 0 tests ... OK`; now pipefail, `command -v surreal`, and `xscholar test --require-db`. The demo judged its whole walkthrough by a substring the instance's own adapter chooses (neutered scenario: `"facts": []`, "done", exit 0); the verdict now counts calls, gate refusals and permission denials through the typed write surface. The privacy scan used an allow list of text extensions and walked past `.csv`/`.xml`/`.sql` ("clean: 2 files" over three files holding the identifier), had no automated caller, and printed clean over 0 files; now a deny list of binary extensions, unreadable files counted and named, 0 files read is exit 2, and it runs in `xscholar test`. 17 tests, each red against a named mutation -- two of which did not go red first time because they matched the word in the comment explaining the defect |
| V2-15 | **A run that read no labels may not sweep** (data loss) | **Done 2026-09-16 (`xscholar/pipeline/loader.py`, `tests/test_loader_sweep.py`):** an empty or mistyped labels directory returned success and deleted every `bears_on` edge in the scope -- measured on the demo's own data, `{'labels': 0, 'edges': 0, 'swept': 79}`, 79 edges to 0, no exception. Writing zero statements is not a failed write, so the existing "a failed write blocks the sweep" guard never saw it. A missing directory is now a LoadError; a run that read no label files does not sweep and says so |
| V2-7 | **Marker wording experiment** (C20), on the first instance with its session's help | Status-upgrade catch rate reported against the 0 of 9 baseline |
| V2-8 | **Preservation edges** as a gate input: `preserves{how}` unit -> conclusion, and a rule clause `requires preserves min 1` with a recorded exception (notes/2026-09-15_engine_gaps_from_instance_config.md) | Probe both ways: unpreserved conclusion refused; preserved accepted; a recorded exception accepted and visible |
| V2-9 | **Authorities verified from official text:** `source` units of grade `official_text`; `authority.verified` gated on a citation to one | Probe both ways; existence of a citation never verifies |
| V2-10 | **Day-grouped chunking** (`chunk_by: <locator field>` per unit type) | **Done 2026-09-15:** units sharing the field stay in one slice; an oversize group is split, never dropped; tested both ways |
| V2-11 | The remaining instance-exposed gaps (output citations with verdicts, events and mentions, outcome-bearing edges, entities, two target kinds, authority rank, framework node text and locator, lens ordering, filing grade, image-only sources needing OCR) | Each with a both-ways probe as it lands |

## Beyond v0.2

| Track | What |
|---|---|
| v0.3 product | Tenants and per-viewer scopes; a human lens UI over the reading layer; Kubernetes when there are tenants; commercial SurrealDB license before any tenant gets schema control |
| Research | Cross-field discovery pilot with the time-split evaluator (D1, D2); methods brain over the reading list (C5); SHACL shapes as the portable gate spec (C13); PROV and nanopublication export (C14); the improvement loop inside its fence (D4) |
| Instances | A public appeals demo from a published decision (C4); a second real domain from config (D3); the codebase-experts pilot (D5) |
