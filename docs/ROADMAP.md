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

## Track C: Extract the engine

| # | Task | Acceptance |
|---|---|---|
| C1 | Instance config file: the eight answers in one file | Schema, validator vocabulary and role skills generated from it |
| C2 | Generic modules: intake, chunker, validator, loader, gate library, lens builder, hooks, eval harness | A toy instance builds from config with no engine edits |
| C3 | Matter-identifier scan in CI | Build fails if an engine file contains an identifier from any private instance's deny-list |
| C4 | Public demo instance (appeals: a public record from a published decision) | End-to-end run with zero client data |
| C5 | Methods brain: pipeline over `research/READING_LIST.md` | Method claims stored with verbatim quotes and source rank; adoption requires primary source plus reproduction |

## Track D: Pilots

| # | Task | Acceptance |
|---|---|---|
| D1 | Cross-field discovery on one or two arXiv categories: problem and capability cards; gated bridges | Validator-clean cards; bridges held as hypotheses until tested |
| D2 | Time-split evaluator for D1 | Hit rate against a stated baseline |
| D3 | Second domain instance from config (candidates: systematic review; incident postmortem) | Built without engine edits |
| D4 | Self-improvement loop on labeling guides against the frozen evaluator | Kept changes logged with evaluator deltas; human-approved |

## Track E: Product

| # | Task |
|---|---|
| E1 | Final name after a search for existing marks and products |
| E2 | Positioning: a professional's verification and record system; the professional signs |
| E3 | Security and privacy posture: data handling, no training on client data, per-viewer scopes |
| E4 | Buyer interviews: appellate boutiques, public defender appellate units; then research groups |
| E5 | License audit of every borrowed artifact |
| E6 | Multi-tenant system of record decision (SurrealDB vs Postgres with extensions) |
