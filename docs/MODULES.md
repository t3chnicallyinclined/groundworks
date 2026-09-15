# Modules

**Principle.** The engine is a set of generic modules that talk to each other only through **file contracts and the graph**. An **instance** is a configuration file (`instance.yaml`, the eight answers) plus a few **adapters** for its domain. Adding a domain should never require editing an engine module; if it does, the module's contract is wrong.

```
                         instance.yaml (the eight answers)
                                     |
      +----------------+-------------+--------------+----------------+-------------+
      |                |             |              |                |             |
 [intake adapter] [vocabulary]  [gate rules]   [role templates] [lens defs]  [evaluator]
      |                |             |              |                |             |
      v                v             v              v                v             v
  units.jsonl ---> [chunker] --> chunks + slices    |                |             |
                       |                            |                |             |
                  [guide builder] -> GUIDE.md, allowed.json          |             |
                       |                            |                |             |
                  [labeler: agents] -> labels/*.jsonl                |             |
                       |                            |                |             |
                  [validator] -- fail -> repair     |                |             |
                       | pass                       |                |             |
                  [loader] -----> GRAPH <-- [schema + gate library] <-+             |
                                   |  ^                                             |
               [write surface] ----+  |                                             |
               (propose/confirm/rule_out/attempt/critique/answer/cite)              |
                                   |                                                |
         [lens builder] -----------+---- [hooks] ---- [roles/skills]                |
                                   |                                                |
      optional: [staleness]  [discovery]                                           |
                                   |                                                |
                        [improvement loop] <---------- keep/discard ---------------+
                                   |
                           [privacy guard] (every module that writes or sends text)
```

## Module catalog

| Module | Responsibility | Input contract | Output contract | Instance supplies | Proven in instance 1 |
|---|---|---|---|---|---|
| **intake** | Raw sources to evidence units with exact locators; exclusions logged | Raw files or API | `units.jsonl` (below), `excluded.jsonl` | **Adapter:** parser and locator scheme | yes (transcripts, messages, filings) |
| **chunker** | Split units into agent-sized slices by type | `units.jsonl`, sizes from config | `chunks.json`, `slices/<chunk>.jsonl` | chunk sizes per unit type | yes |
| **guide builder** | Labeling guide and allowed vocabulary from config | `instance.yaml` | `GUIDE.md`, `allowed.json` | vocabulary, tiers, directions, hard rules | yes (hand-built; generator is Track C) |
| **labeler** | Agents read slices with code in batches and write labels | slice + guide | `labels/<chunk>.jsonl` (below) | role prompt (usually the domain expert) | yes (77 agents) |
| **validator** | Coverage, vocabulary, reason, verbatim quote, status wording | slice + labels + allowed | pass/fail with named errors, exit code | status wording rules | yes |
| **loader** | Evidence and passing labels into the graph; batch results counted | units, passing labels | evidence tables, label fields, `bears_on` edges | table names per unit type | yes (0 failed statements) |
| **schema + gate library** | Generate datastore schema and ASSERT gates from rules; generate a reject and an accept probe per rule | `instance.yaml` gates | `.surql` files, probe test | rule list | yes (hand-written; generator is Track C) |
| **write surface** | Typed helpers; each promotion helper checks the rule and names the reason | graph | graph writes | none | yes (`kb.py`, `kb_appellate.py`) |
| **lens builder** | Framework nodes (verbatim from the authoritative source), crosswalk filings, views, matrices | labels, framework, crosswalk | `consideration` nodes, `falls_under` edges, views | framework text and location; crosswalk | yes |
| **hooks** | Inject known facts, dead ends, authority status when a tool call names a locator; carry state across compaction | tool-call JSON, graph | additional context | locator regexes | yes |
| **roles** | Skills for adversarial, judging, auditing roles; write critiques and verdicts back | graph, role templates | critiques, attacks, answers, citation verdicts | role list and domain wording | yes (four skills) |
| **evaluator** | Regression suite from past errors; gate probes; label-agreement sampler; judged panel; outcome metric | frozen files (human-owned) | scores, pass/fail | regression entries, panel prompts, outcome definition | partly (regression + probes) |
| **improvement loop** | One change at a time, evaluator, keep/discard, log | evaluator, a changeable artifact | kept change + attempt log | which artifacts are inside the fence | designed |
| **staleness** (optional) | For mutable corpora: span hashes, auto-demote facts when cited spans change, queue re-reads | diffs, graph | fact status changes, re-read queue | span hashing method | designed |
| **discovery** (optional) | Problem and capability cards, candidate bridges, bridge gate, time-split scoring | cards | bridges, verdicts, scores | card types, novelty check | designed |
| **truth maintenance** (optional, recommended) | Computed effective status; events that demote on lost justification, cascade through chains, flag weakened dependents, flag conclusions whose adverse material was debunked, mark output citations for recheck; review queue | graph writes | status changes, `review` rows | confirming grades; citation tables counted | prototype proven (`engine/patterns/tms_propagation.surql`) |
| **archive** (optional, recommended) | Snapshot on delete with reason; per-table retention tiers (knowledge never purged, artifacts expire, client data purged on demand); restore; legal hold; scheduled purge with a logged count | deletes | `archive` rows, purge log | retention days per table; hold defaults | prototype proven (`engine/patterns/archive_bin.surql`) |
| **privacy guard** | Deny-list scan before commit or send; provider policy | text, deny list (outside the repo) | pass/fail; redactions | deny list path, provider allow-list | yes (scan) |

## File contracts

### `units.jsonl`: one evidence unit per line
```json
{"id": "u_000123",
 "type": "transcript_turn",
 "locator": {"source": "hearing_2025-03-04", "page_from": 41, "line_from": 3, "page_to": 41, "line_to": 9},
 "text": "Q. When did you first receive the invoice? A. In early March.",
 "status": "usable",
 "grade": "text_extract",
 "meta": {"speaker": "WITNESS", "role": "testimony"},
 "hash": "sha256:..."}
```
Required: `id`, `type`, `locator`, `text`, `status`, `grade`, `hash`. `status` values and `grade` values come from the instance's axes.

### `labels/<chunk>.jsonl`: exactly one label per unit
```json
{"id": "u_000123", "tier": 2, "dir": "against_goal", "issues": ["notice"],
 "targets": ["finding_07"], "conclusions": ["point_2"], "role": "testimony",
 "reason": "Witness places receipt in early March, before the cure period ran.",
 "quote": "In early March", "flags": []}
```
Tier 2-3 require `reason`, `dir`, at least one issue, and a `quote` found verbatim in the unit text. `flags` hold per-subunit labels (for example one message inside a day).

### `allowed.json`
`{"tiers":[0,1,2,3], "dirs":[...], "issues":[...], "targets":[...], "conclusions":[...], "roles":[...]}`

### Gate rule (in `instance.yaml`)
```yaml
- id: confirmed_needs_primary
  table: fact
  field: status
  value: confirmed
  requires:
    - edge: cites
      to: [passage, unit]
      where: "grade IN ['original','text_extract'] AND status != 'unusable'"
      min: 1
```
The gate library turns each rule into an ASSERT, plus two probes: the write is **rejected** without the required edge, and **accepted** once it exists.

## Rules for keeping it modular

1. Modules communicate through the contracts above and the graph, never through each other's internals.
2. Domain words live in `instance.yaml`, the guide, the framework file and role templates, never in engine code.
3. Every module's failure is loud: non-zero exit, named errors, counts compared to expectations.
4. Human-owned files (gates, ladders, evaluator entries, privacy rules) are listed in `instance.yaml` under `human_owned` and the improvement loop refuses to modify them.
5. A new domain that needs an engine change is a design review item: fix the contract, not the instance.
