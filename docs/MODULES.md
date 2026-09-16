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
         [lens builder] -----------+---- [hooks] ---- [roles/skills + panel]        |
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
| **validator** | Coverage, vocabulary, reason, verbatim quote, status wording; **a slice of 0 units is an error, not a PASS** (every rule is a loop over the slice) | slice + labels + allowed | pass/fail with named errors, `units`, `checks_run`, exit code | status wording rules | yes |
| **loader** | Evidence and passing labels into the graph; batch results counted | units, passing labels | evidence tables, label fields, `bears_on` edges | table names per unit type | yes (0 failed statements) |
| **schema + gate library** (`xscholar/gates.py`) | Generate datastore schema and ASSERT gates from rules; generate a reject and an accept probe per rule, a wrong-evidence probe per constrained clause and a forbidden-evidence probe per `forbids` clause. **The graph shape comes from the instance**: the engine ships `config.BUILT_IN_NODE_TABLES` and `config.BUILT_IN_EDGES`, and everything here reads `inst.node_tables()` / `inst.edge_map()`, which are those plus whatever `tables:` and `edges:` declare. Anything a probe cannot build is reported with its reason and fails `behaved`; nothing is skipped | `instance.yaml` `gates:`, `tables:`, `edges:` | `.surql` files, probe plans, a probe report, `spec()` for the baseline | rule list; declared tables and edge types | built (`xscholar/gates.py`); probed both ways on SurrealDB 3.1.6, declared tables and edges on the same code path as built-ins |
| **write surface** | Typed helpers; each promotion helper checks the rule and names the reason | graph | graph writes | none | yes (`kb.py`, `kb_appellate.py`) |
| **lens builder** (`xscholar/lens.py`) | One datastore view `lens_<name>` per perspective or discipline lens (label joined to its unit; the lens's Standard, never looser than the base gates, plus its filter); for a framework lens, `consideration` nodes transcribed from the source and `falls_under` filings by crosswalk with stable edge ids and a sweep only after success; the per-node matrix (by direction, usable vs unusable); `lens_view` read tool; lens names, kinds and backing in `health` | `instance.yaml` `lenses:` (below), labels | `.surql` views, `consideration` rows, `falls_under` edges, matrix, view rows with `drift` | charter, kind, backing, standard, filter; frame source and nodes; crosswalk | built from config 2026-09-15 (`tests/test_lens.py`, both ways on the demo instance); a view refreshes only from `label`, so a unit change needs a rebuild and is reported as `drift` until then |
| **hooks** | Inject known facts, dead ends, authority status when a tool call names a locator; carry state across compaction | tool-call JSON, graph | additional context | locator regexes | yes |
| **roles** (`xscholar/skills.py`) | One `SKILL.md` per `roles:` entry, rendered from `xscholar/skills_templates/<skill>.md` and filled entirely from the instance: goal and holder, the vocabulary with confirming grades and usable statuses, each seat's lens charter with its `backing` and `signed` (a prompt-backed or unsigned charter says it may never support promotion), panel options, objection categories and weighted seat, the `panel.py` sequence with `--settings`, every gate rule in plain words, the MCP tools the role may call filtered by `access:`, and the privacy rules. Two header lines and a `content-sha256` per file; a role whose skill has no template is a named error | `instance.yaml` `roles:`, `lenses:`, `gates:`, `access:`, `privacy:` | `OUT_DIR/<skill>/SKILL.md`, byte-identical on regeneration | role list, panel settings, and a template per skill | built from config 2026-09-15 (`tests/test_skills_drift.py`); five templates (`red-team`, `cold-bench`, `premortem`, `claim-audit`, `reader`); demo skills in `instances/demo-incident/skills/` |
| **drift guard** (`xscholar/drift.py`) | Refuses a generated skill or a prose doc that no longer matches the config: (a) every lens, seat, panel option, objection category and gate id appears in the skill that uses it, matched as a whole token; (b) every file still hashes to a fresh generation, separating a hand edit from a stale file; (c) in any doc passed, every `lens_<name>`/`seat_<name>` exists in the config. Missing and orphan skill files are drift. Every drift names the file and the item | `instance.yaml`, a skills dir, optional docs | a `Report`; exit 0 clean, 1 drift, 2 could not run | nothing | built 2026-09-15; runs in CI and as the last step of `xscholar test`. Docs are opt-in per invocation: a doc carrying another instance's worked examples (`xscholar/panel/PROTOCOL.md`) fails check (c) by design |
| **mcp front door** | Typed MCP tools over the instance's write surface, audits and panel (`unit_get`, `unit_search`, `claim_explain`, `eliminated_claims`, `health`, `critique_add`, `critique_answer`, `panel_submit`, `panel_status`); status envelope (`ok`, `not_found`, `db_unreachable`, `rejected_by_gate`, `invalid_argument`, `error`); source text only under `untrusted_source_text`; provenance stamped from launch identity; no raw query, delete or schema tools | tool calls | envelopes | an adapter module (`create_adapter`) implementing `xscholar/mcp/adapter.py` | built (slice 1, `xscholar/mcp/`); tested against two non-legal fake instances and instance 1's adapter |
| **panel** | Several lenses judge one target: lock, blind round, anonymize, cross-examination, program checks (stance line, verbatim flaw on a changed stance, novelty, dissent quota, agreement counterfactuals), weighted tally, split to human | `panel.json`, round answers | `tally.json`, `verdicts.jsonl` (below), `enforcement.jsonl` | seats, options and objection categories per role (`roles[].panel`) | built, unit-tested (`xscholar/panel/`); not yet run on an instance |
| **evaluator** | Regression suite from past errors; gate probes; label-agreement sampler; judged panel; outcome metric | frozen files (human-owned) | scores, pass/fail | regression entries, panel prompts, outcome definition | partly (regression + probes) |
| **improvement loop** | One change at a time, evaluator, keep/discard, log | evaluator, a changeable artifact | kept change + attempt log | which artifacts are inside the fence | designed |
| **staleness** (optional) | For mutable corpora: span hashes, auto-demote facts when cited spans change, queue re-reads | diffs, graph | fact status changes, re-read queue | span hashing method | designed |
| **discovery** (optional) | Problem and capability cards, candidate bridges, bridge gate, time-split scoring | cards | bridges, verdicts, scores | card types, novelty check | designed |
| **temporal** | Bitemporal claims: `valid_from`/`valid_to` (when the claim held in the source, writer-supplied) and `learned_at` plus an append-only `status_history` on fact, `learned_at` plus `ready_history` on conclusion, written by datastore events only when the value changed (a same-status re-write appends nothing; history cannot be emptied or rewritten); `as_of` rebuilds the status a row held at a date, `snapshot` gives counts by status as of a date | schema + gates already applied | `.surql` applied after gates, `as_of` and `snapshot` dicts, `temporal` count in health | nothing (dates are optional arguments to `propose` and `confirm`) | built (`xscholar/temporal.py`), probed both ways on SurrealDB 3.1.6 |
| **truth maintenance** (optional, recommended) | Computed effective status; events that demote on lost justification, cascade through chains, flag weakened dependents, flag conclusions whose adverse material was debunked, mark output citations for recheck; review queue | graph writes | status changes, `review` rows | confirming grades; citation tables counted | prototype proven (`xscholar/patterns/tms_propagation.surql`) |
| **work queue** (`xscholar/queue.py`) | Durable, resumable consumer of the datastore change feed: per-table versionstamp stored only after every handler succeeds (at-least-once, idempotent handlers); shipped consumers `stale_recheck` (a confirmed fact whose citation lost its confirming grade or usable status, or whose cites edge was deleted, goes `stale` with a `review` row; loader churn that changed nothing is ignored) and `archive_on_delete` (deleted claim rows become `archive` rows with the last seen image) | `CHANGEFEED` on unit, fact, conclusion, critique, cites (applied after schema and gates); a state JSON path | `stale` statuses, `review` rows, `archive` rows, a run report with error counts, and the counts that disagree with `0 entries, 0 errors`: whether the state file existed, which tables resumed vs started fresh vs replayed, how many feed entries a fresh start skipped, and any watched table whose CHANGEFEED is gone (an error, not a quiet pass) | confirming grades and usable statuses (from config) | built and tested both ways on SurrealDB 3.1.6 (`tests/test_queue.py`), including resume after a stop and a deliberate break of the versionstamp discipline |
| **archive** (optional, recommended) | Snapshot on delete with reason; per-table retention tiers (knowledge never purged, artifacts expire, client data purged on demand); restore; legal hold; scheduled purge with a logged count | deletes | `archive` rows, purge log | retention days per table; hold defaults | prototype proven (`xscholar/patterns/archive_bin.surql`) |
| **privacy guard** | Deny-list scan before commit or send; provider policy | text, deny list (outside the repo) | pass/fail; redactions | deny list path, provider allow-list | yes (scan) |
| **access** | Per-agent datastore credentials: one record access over an `agent` table and a PERMISSIONS clause on every generated table, so the datastore decides who may read and write what (a lens-bound seat writes critiques only under its lens; only the loader writes labels; DELETE is root-only; gates stay in force for every credential); provision and revoke; `Db` treats a silently denied write as a refusal | `instance.yaml` `access.roles` | `.surql` (DEFINE ACCESS + ALTER TABLE PERMISSIONS), launch env per agent (`XSCHOLAR_DB_USER/PASS/ACCESS`) | roles: `read`, `write`, `lens_bound` | built (`xscholar/access.py`); probed both ways per role on the demo instance, SurrealDB 3.1.6 |

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

### Lenses (in `instance.yaml`)
```yaml
lenses:
  - name: service_owner
    kind: perspective              # perspective | discipline | framework
    charter: "Who is looking, what they want, what hurts them, the questions they always ask"   # human-signed
    standard: {status: [in_window]}   # statuses/grades accepted; STRICTER than status.usable / fidelity.confirming, never looser
    filter: "tier >= 2"               # over label fields only: tier, dir, issues, targets, role, chunk
    backing: source                   # prompt | source | graph | calibrated (prompt-backed never supports promotion)
  - name: incident_framework
    kind: framework
    charter: "Files every labelled unit under the review template's headings"
    frame: {source: "Incident review template v3, section 2", nodes: [trigger, propagation, detection, mitigation, prevention]}
    crosswalk: {trigger: [deploy, config], propagation: [saturation, dependency, capacity], detection: [monitoring], mitigation: [], prevention: [config, monitoring]}
```
The validator refuses a `kind` or `backing` outside the lists, an empty charter, a standard naming a status or grade that does not exist or lies outside the base usable/confirming sets, a filter that names anything but label fields and operators, a framework lens without `frame.source`, unique `frame.nodes` and a `crosswalk` whose keys are every frame node and whose values are issues from the vocabulary. The lens builder turns a perspective or discipline lens into `DEFINE TABLE lens_<name> AS SELECT ... FROM label WHERE unit.status IN <standard> AND (<filter>)`, and a framework lens into `consideration` rows (one per node, with the source) and `falls_under` edges from every label whose issues cross-walk to the node. `lens_view(name)` returns the view's rows with the unit text under `untrusted_source_text` and a `drift` count (rows whose unit no longer passes the standard; rebuild with `xscholar lens INSTANCE.yaml --apply`); for a framework lens it returns the matrix. Filings are agent-grade; counts are items, not weight.

### `verdicts.jsonl`: one panel seat's verdict per line
```json
{"panel_id": "red_team_point_2", "target": "conclusion:point_2",
 "seat": "seat_deferential_judge", "lens": "lens_deferential_judge",
 "stance": "damaged", "confidence": "med", "dealbreaker": false,
 "objections": [{"category": "standard_of_review", "text": "The finding rests on credibility, reviewed with deference."}],
 "new": ["..."], "flaws": [], "counterfactual_objections": [],
 "grade": "agent", "panel_sha256": "...", "answer_sha256": "..."}
```
The write surface records each row as a lens-keyed critique of `target`. Protocol and full layout: `xscholar/panel/PROTOCOL.md`.

## Rules for keeping it modular

1. Modules communicate through the contracts above and the graph, never through each other's internals.
2. Domain words live in `instance.yaml`, the guide, the framework file and role templates, never in engine code.
3. Every module's failure is loud: non-zero exit, named errors, counts compared to expectations.
4. Human-owned files (gates, ladders, evaluator entries, privacy rules) are listed in `instance.yaml` under `human_owned` and the improvement loop refuses to modify them.
5. A new domain that needs an engine change is a design review item: fix the contract, not the instance.
