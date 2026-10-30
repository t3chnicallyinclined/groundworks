# Groundworks

**Make AI agents reliably expert in one large body of material, and make them prove it.**

You give Groundworks a corpus (a case record, a codebase, an incident's logs, a stack of papers) and a goal. Agents read all of it. What they conclude goes into a knowledge graph whose **database refuses** to treat anything as established until it cites evidence of sufficient rank, has been attacked by an adversarial role, and has had every damaging attack answered. Dead ends are kept as eliminated claims so nobody re-argues them. Every quote an agent offers is checked word for word against the source by a program, not by another model.

Descriptive term: **an evidence-gated knowledge graph built by exhaustive, verified agent reading.**

- **Python 3.11+ standard library only.** One external binary: SurrealDB 3.1.x.
- **Domain-free engine.** You describe your domain in one `instance.yaml` (the eight questions); the engine generates the schema, the promotion gates, per-role permissions and a reject-and-accept probe for every rule.
- **Built for Claude Code and other MCP clients:** a stdio MCP server gives agents typed tools (read a unit, search, explain what blocks a claim, propose/confirm/rule out, critique, run a panel), with no raw query or delete tool.
- **MIT licensed.**

## What it looks like

The bundled demo is a synthetic incident postmortem: 66 log lines, a commit and two metrics, with the goal "establish the root cause of the checkout outage". Here three agents (a reader, a panel seat and a coordinator) try to promote a root cause. Each line is the **database** answering, not the agent:

```
[reader_1|role=reader] confirm pool_exhausted with no evidence
    REFUSED by gate: needs at least one cited unit whose grade is in ['official_text', 'original_log',
    'exported_metric'] and whose status is in ['in_window']; cited now: nothing
[reader_1|role=reader] confirm with an out-of-window line
    REFUSED by gate: ... cited now: log_0001 (grade original_log, status out_of_window)
[reader_1|role=reader] confirm with an in-window error line and the pool metric
    ACCEPTED
[reader_1|role=reader] rule_out dns_failure
    RECORDED as an eliminated claim (never gated)
[reader_1|role=reader] critique rc_pool_exhaustion as a reader
    DENIED by permissions: UPSERT refused by datastore permissions for agent 'reader_1'
[coordinator_1|role=coordinator] mark rc_pool_exhaustion ready before any attack
    REFUSED by gate: never attacked (run a red team or panel first)
[seat_skeptic|role=seat] critique rc_pool_exhaustion under its own lens
    ACCEPTED: critique:rc_pool_exhaustion__lens_skeptic__9ff895fa
[coordinator_1|role=coordinator] mark ready with a damaging head unanswered
    REFUSED by gate: 1 damaging head(s) unanswered (answer them first)
[coordinator_1|role=coordinator] answer the head and record preservation, then mark ready
    ACCEPTED: rc_pool_exhaustion is ready
[coordinator_1|role=coordinator] delete the critique as coordinator
    DENIED by permissions: DELETE refused by datastore permissions for agent 'coordinator_1'
```

Every REFUSED line is the method working: the conclusion only becomes "ready" once it has qualifying evidence, an attack, and an answer to that attack, and nobody, the coordinator included, can quietly delete the attack.

## Install

```
# 1. SurrealDB 3.1.x: https://surrealdb.com/install
#    (or point GROUNDWORKS_SURREAL at the binary)

# 2. Groundworks
git clone https://github.com/t3chnicallyinclined/groundworks.git
cd groundworks
pip install -e .            # adds the `groundworks` command; no third-party dependencies
pip install -e ".[mcp]"     # only if you want the MCP server for agents
```

Every command below also works as `python -m groundworks.cli <command>` without installing.

## Example 1: run a whole instance end to end

```
python scripts/run_instance.py instances/demo-incident --memory 8025
```

This starts a throwaway in-memory SurrealDB on port 8025 and runs the full pipeline on the demo: config, generated schema and gates, a reject-and-accept probe for every rule (`3 rejected as required, 3 accepted as required, 0 misbehaved`), intake, originals stored and re-hashed, chunking, labelling (a stand-in rule labeller here; real instances use agents), the verbatim quote validator, the load, lenses, and then the scenario above. It takes about 25 seconds. Outputs land in `instances/demo-incident/out/`, including `units.jsonl`, the corpus used in the next example.

To keep the database, start SurrealDB yourself and pass `--url` instead of `--memory`:

```
surreal start --bind 127.0.0.1:8002 --user root --pass root rocksdb:/path/to/data
python scripts/run_instance.py instances/demo-incident --url http://127.0.0.1:8002/sql
```

## Example 2: ask a question of a corpus, with every quote checked

`groundworks ask` needs no database. A program retrieves, an agent (or you) reads and answers, and a program audits the answer.

**1. Open a packet.** BM25 ranks the whole corpus and writes the top K for the reader:

```
$ groundworks ask open "Did the connection pool run out, and did a retry change come before it?" \
    --units instances/demo-incident/out/units.jsonl \
    --instance instances/demo-incident/instance.yaml --out ask1 --k 10
ASK OPEN OPEN | corpus 66 | in scope 66 (types all, where none) | retrieved 10 of k=10 | slices 1 x 40 | ask1
```

**2. Read and answer.** The reader works through `ask1/slices/*.jsonl`, logs each unit it read in `ask1/read.jsonl`, and writes `ask1/answer.json`. The rules for the reader are in [`groundworks/ask/READER.md`](groundworks/ask/READER.md); hand that file to an agent as its instructions. Here the second quote is a paraphrase:

```json
{"answer": "Yes. The retry limit was raised from 3 to 8 at deploy, and the connection pool then ran out.",
 "quotes": [
  {"unit": "chg_0001", "quote": "retry max_attempts 3 -> 8", "use": "support", "why": "the change"},
  {"unit": "log_0040", "quote": "the pool was exhausted after 30 seconds", "use": "support", "why": "the exhaustion"}
 ],
 "gaps": ["The read units do not show that the retries caused the exhaustion, only that one came before the other."],
 "not_answerable": false}
```

**3. Check.** The checker refuses the paraphrase:

```
$ groundworks ask check ask1 --instance instances/demo-incident/instance.yaml
ASK CHECK corpus 66 | retrieved 10 | read 10 | not read 56 | quotes 2 (checked 2, found 1) | status axis checked | check FAIL
  quote 2 not found word for word in unit log_0040
```

Copy the words from the unit (`pool exhausted: waited 30000ms for connection`) and it passes. `render` then writes `ask1/answer.md`, and its first line always says how much was **not** read:

```
$ groundworks ask render ask1 --instance instances/demo-incident/instance.yaml
ASK RENDER corpus 66 | retrieved 10 | read 10 | not read 56 | quotes 2 (checked 2, found 2) | status axis checked | check PASS
```

The checker also refuses a cite to a unit nobody logged as read, a cite outside the packet, a slice with an unread unit, and a `support` quote from a unit whose status is not usable (here, a log line outside the incident window). An empty read log is `NOT EXECUTED` (exit 3), never a pass. An ask answer never writes to the graph; promoting anything from it goes through the gates.

## Example 3: give agents the graph through MCP

`groundworks.mcp.instance_adapter` serves any `instance.yaml` with no adapter code. Add an entry to your client's `.mcp.json` (Claude Code reads it from the project root):

```json
{
  "mcpServers": {
    "groundworks": {
      "command": "python",
      "args": ["/path/to/groundworks/groundworks/mcp/server.py"],
      "env": {
        "GROUNDWORKS_ADAPTER": "groundworks.mcp.instance_adapter",
        "GROUNDWORKS_INSTANCE": "/path/to/my-instance/instance.yaml",
        "GROUNDWORKS_DB_URL": "http://127.0.0.1:8002/sql",
        "GROUNDWORKS_AGENT_ID": "lead_session",
        "GROUNDWORKS_ROLE": "coordinator",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

The agent connects as `root`/`root` unless you give it a provisioned credential: `GROUNDWORKS_DB_USER`, `GROUNDWORKS_DB_PASS` and `GROUNDWORKS_DB_ACCESS=agent` sign it in under its role's table permissions (the demo prints these for each agent it provisions). Check a configuration without serving: `python groundworks/mcp/server.py --list-tools`. Then, in the session, call `health` once before the first write; a reply naming your instance proves the server, the adapter and the database are all connected. The agent gets tools such as:

| Tool | What it does |
|---|---|
| `unit_search`, `unit_get` | Find and read source units by keyword or exact locator, with grade, status and every claim citing them |
| `claim_propose`, `claim_confirm` | Put a claim forward, then confirm it by citing units; the database refuses a confirm without qualifying evidence |
| `claim_support` | Link a confirmed fact to the conclusion it supports |
| `claim_rule_out`, `eliminated_claims` | Record a dead end, and check for one before re-arguing it |
| `claim_explain` | Everything that still blocks a claim, in words |
| `critique_add`, `critique_answer`, `conclusion_mark_ready` | Attack a conclusion, answer the attack, promote when nothing blocks it |
| `document_get`, `lens_view`, `health` | The original a unit came from (re-hashed on every read), a lens's view of the evidence, and the instance's health and drift |
| `panel_submit`, `panel_status` | Run a panel: several lenses judge one target under a program-checked protocol |

Every result carries a status (`ok`, `not_found`, `db_unreachable`, `rejected_by_gate`, `invalid_argument`, `error`). On `rejected_by_gate` the result names what is missing. Writes are stamped with the launch identity, and writes are refused when the engine checkout is not a clean, current commit. Full contract: [`groundworks/mcp/README.md`](groundworks/mcp/README.md).

## Example 4: your own domain

An instance is one `instance.yaml` plus an adapter that yields units with exact locators. It lives in **its own project**, never in this repo. The shape, from the demo:

```yaml
instance:
  id: demo_incident
  goal: "Establish the root cause of the checkout outage, provable from the incident record"

units:                       # what a citable piece of the corpus is, and how it is located
  - name: log_line
    locator_fields: [host, file, line, ts]
  - name: change
    locator_fields: [repo, commit, ts]

fidelity:                    # evidence rank; only the confirming grades can establish a fact
  order: [official_text, original_log, exported_metric, agent_summary, recollection]
  confirming: [official_text, original_log, exported_metric]

status:                      # a unit can be real and still unusable for this goal
  values: [in_window, out_of_window, redacted]
  usable: [in_window]
```

Then:

```
groundworks validate path/to/instance.yaml        # check the config
python scripts/run_instance.py path/to/my-instance --memory 8025
```

To get there faster, copy [`instances/demo-incident/`](instances/demo-incident/), or open this repo in Claude Code and use the `design-instance` skill. [`instances/`](instances/) holds 19 worked domain specs (systematic review, patent prior art, forensic accounting, codebase experts, threat intelligence, reverse engineering and more), each with the eight answers, gates and probes, roles, lenses and a synthetic worked example.

## Status: v0.2.0, plus `ask`

- **Tests:** 763, 0 skipped. `groundworks test` (about 25 minutes; many suites start their own short-lived SurrealDB) runs every suite, the license check, the config/skills drift check, the mutation lint and the privacy scan, and prints each by name, so a check that did NOT run is visible rather than green. Without a deny list the privacy scan reports `NOT EXECUTED`, which is not a failure.
- **Gates are probed in both directions and pinned to a committed baseline**, so a rule that is quietly loosened fails the run instead of reporting `0 misbehaved`.
- **The graph's shape belongs to the instance:** declare node tables and edge types in `instance.yaml` and they get schema, gate traversals, probes, cleanup, per-role permissions and a write path with no engine change.
- **Used for real, privately:** the first instance is an appellate case record, 9,101 units read by 77 agents, 77 of 77 chunks passing the verbatim validator. Its code and data are not here and never will be.
- **Known gaps, plainly:** the only bundled corpus is synthetic; the MCP server has been driven by Claude Code sessions but not by any other client; `ask` retrieves with BM25 only (recall@20 measured at 0.54 on the first instance, which is why K is in the hundreds). Two presentation ideas were measured and **not adopted**; see `notes/`.
- **Rust is the successor.** The Rust port lives in [`rust/`](rust/) (crates `gw-core` and `gw`, run with `cargo gw-test` from `rust/`) and is checked byte for byte against this Python v0.2.0 as its reference (`rust/harness/differential.py`). Python stays as that reference until each piece is ported; new work lands in Rust.
- **Claude Code plugin:** [`plugin/`](plugin/) packages the Rust MCP server (`groundworks serve`, crate `rust/crates/gw-mcp`) with two skills that run only when invoked (`/groundworks:init`, `/groundworks:bootstrap`). Install steps are in [`plugin/README.md`](plugin/README.md).
- **Where it is going:** one harness that makes coding agents experts on a software project as well as a document corpus: a project's code, docs, commits and issues become units; claims about it need evidence (a quote found word for word, a command receipt, a commit); dead ends are kept; work items stay in GitHub issues. There is no ticket board.

## Running it, in detail

Two things. The engine's `dependencies` are `[]` -- Python standard library only.

| | |
|---|---|
| Python | 3.11 or newer |
| SurrealDB | **3.1.x** ([install](https://surrealdb.com/install)), or set `GROUNDWORKS_SURREAL` to the binary |

**There is no container, and that is deliberate.** There were a `Dockerfile` and a `compose.yaml` here until 2026-09-16, and they had never once been run. This repo's standing rule is that a path nobody has executed is not a working path, however good it looks in a README, so they were deleted rather than left as decoration. One binary and one command is the whole deployment story; if you later need a container for distribution, write it against a real deployment and measure it. The files are in git history.

**The version is checked, not hoped for.** Pinning a database version was the one real thing the image did, and a version is a fact the server will tell you: `Db.ping()` asks `/version` and refuses a different major series by name, because this engine's behaviour is *measured* against 3.1 -- a schema re-apply silently drops a `CHANGEFEED`, a delete carries no before-image, `DEFINE USER ... ON DATABASE` bypasses table `PERMISSIONS`, and a denied write comes back as an empty result with status OK. Each of those is load-bearing somewhere in this repo. A different 3.x minor is a printed warning, not a refusal.

**Backups.** Back a kept database up with `surreal export` on whatever scheduler your machine already has (cron, systemd timer, Task Scheduler). That is one line, it runs on the host, and it fails visibly.

**SurrealDB's license.** SurrealDB is Business Source License 1.1 (`docs/LICENSING.md` s.1). You install it yourself, so this repo distributes none of it -- but if you ever bundle or redistribute it, its `LICENSE` must stay conspicuous and `THIRD_PARTY_NOTICES.md` names it.

### If you are an agent upgrading an existing instance

Read **[`UPGRADING.md`](UPGRADING.md)** and work top to bottom. It is written for you, not for a
human, and it is not optional maintenance: v0.2 fixes one data-loss defect (an empty labels
directory deleted every evidence edge in scope and returned success) and several checks that
reported green while doing nothing. It also tells you the two things a schema re-apply silently
drops, and how to prove your regenerated gate baseline can still fail.

### If you are an agent working in this repository

1. Read `CLAUDE.md` first. Its rules are binding: no instance data here, verify before claiming, gates tested both ways, checks that pass by default are the enemy, the evaluator is human-owned, licensing by class.
2. Run `python -m groundworks.cli test` before and after any change. Green is the only definition of done.
3. Read `docs/ARCHITECTURE.md` (what the system is, and who is at the gate), then `docs/TUNING.md` (what each layer's knobs do), then `docs/MODULES.md` (the file contracts you will write against).
4. To add a capability: put generic code under `groundworks/<module>/`, tests under `tests/`, and test it with a non-legal synthetic instance. Domain words never enter engine code.
5. To map a new domain: use the `design-instance` skill, or copy `instances/demo-incident/`, answer the eight questions in `instance.yaml`, write an adapter that yields units with exact locators, and run the same command. The instance lives in **its own project**, never here.
6. Record decisions in `docs/DECISIONS.md`, dated reasoning in `notes/`, and every new dependency or adapted item in `THIRD_PARTY_NOTICES.md` (the pre-commit check enforces it).
7. Report the unflattering number next to the flattering one. Say UNKNOWN rather than guess.

### If you are a person deciding whether to use this

Read `docs/METHOD.md` section 1 (one paragraph) and section 12 (which domains fit), then run the demo above and read its output: each REFUSED line is the method working.


## Privacy boundary (non-negotiable)

This repository contains **no client or matter data**, ever:
- no names, record text, labels, or case-specific seeds
- engine code enters only after a scripted scan for matter identifiers
- demos use public corpora only

## Layout of the repository

| Path | What |
|---|---|
| `docs/METHOD.md` | The method: what is unique, primitives, layers, the eight configuring questions, evidence model, lenses, improvement loop, self-improvement fence, lineage, technology, optimization, use cases, cross-field discovery design, licensing, privacy |
| `docs/ARCHITECTURE.md` | The system: layers, invariants, what an instance supplies vs what is generated, each layer's fixed test, how to map a new domain, open design decisions |
| `docs/TUNING.md` | Per layer: the knobs, defaults, how to measure a change, failure modes, measured starting points |
| `docs/MODULES.md` | Engine modules, file contracts (units, labels, vocabulary, gate rules), modularity rules |
| `docs/PATTERNS.md` | Designs from running a real instance that any instance can declare in config, with what was verified and their limits (first: contentions) |
| `instances/` | 19 domain specs mapping the method (eight answers, evidence model, gates with probes, roles, lenses, evaluator, walkthrough, usage, synthetic worked example, pitfalls, module reuse) plus a template and `instance.yaml` schema |
| `.claude/skills/design-instance/` | The skill that maps the method onto a new domain |
| `groundworks/panel/` | Panel module: several lenses judge one target under a program-checked protocol (`PROTOCOL.md`, `panel.py`, tests, `NOTICE.md`) |
| `.claude/skills/panel/` | The skill that runs a panel |
| `groundworks/mcp/` | MCP front door: domain-free tools over an instance adapter, status envelope, launch-stamped provenance, no raw query or delete tools |
| `groundworks/embed/` | Local embeddings and a retrieval bake-off (keyword, dense, hybrid) with confidence intervals; models adopted only if they beat keyword search |
| `docs/LICENSING.md`, `THIRD_PARTY_NOTICES.md`, `scripts/license_check.py` | Licensing policy, notices, and the commit-time license check |
| `groundworks/patterns/` | Proven datastore patterns: truth maintenance, archive bin, stable edge ids |
| `scripts/privacy_scan.py` | Deny-list scan (list kept outside the repo): staged files (pre-commit), the commit message (commit-msg), every pushed commit message and ref name (pre-push); reports deny-list line numbers, never patterns; fails closed. `scripts/install_hooks.py` installs the three hooks |
| `docs/ROADMAP.md` | Tracks for the engine, pilots and product, with acceptance tests |
| `docs/DECISIONS.md` | Method-level decisions and open questions |
| `notes/` | Dated design notes: how the idea developed and why |
| `research/LINEAGE_STUDY.md` | The twelve known techniques inside the method (integrity constraints, write-audit-publish, medallion layers, truth maintenance, evidence grading, provenance, knowledge fusion, argumentation, verified annotation, recursive language models, autoresearch, neuro-symbolic), each with where it lives in our system, study questions and sources |
| `research/READING_LIST.md` | Papers and repos to run the scholar over, with the question each should answer |
| `research/SOURCES_2026-09-15.md` | Market and technology scan, graded by source rank |
| `.claude/agents/scholar-architect.md` | The expert agent for designing and building the system |
| `CLAUDE.md` | Rules for any session working in this repo |

## Layout of the engine

| Path | What |
|---|---|
| `groundworks/config.py` | The instance config: the eight answers, validated, as the single source of truth |
| `groundworks/gates.py` | Generates the datastore schema, the promotion gates, and a reject-and-accept probe per rule |
| `groundworks/db.py` | Minimal SurrealDB client that fails loudly |
| `groundworks/pipeline/` | intake, chunker, validator, loader, and the typed write surface |
| `groundworks/panel/` | Several lenses judge one target under a program-checked protocol |
| `groundworks/ask/`, `groundworks/retrieve.py` | Ask a corpus a question: BM25 packet, reader rules (`READER.md`), the quote checker, the renderer |
| `groundworks/present/` | Renderings for human verification, with the inline rule re-check |
| `groundworks/mcp/` | The MCP front door agents use; `instance_adapter.py` gives any `instance.yaml` all tools with no adapter code |
| `groundworks/hooks/` | Session-start health warning and the pre-tool lookup that speaks before a mistake is made |
| `groundworks/embed/` | Local embeddings and the retrieval bake-off |
| `groundworks/patterns/` | Proven datastore patterns |
| `tests/` | Every suite; `python -m groundworks.cli test` runs them all |
| `scripts/` | The instance runner, the privacy scan, the license check |

## License

MIT. Copyright (c) 2026 t3chnicallyinclined (see [`LICENSE`](LICENSE)). Adapted and depended-upon third-party material is listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) under its own licenses. SurrealDB, which you install separately, is under the Business Source License 1.1.

## Using the expert

Open this repo in Claude Code and ask for the `scholar-architect` agent, for example:
- "Design an instance for a systematic review of X: answer the eight questions."
- "Review this gate change for checks that pass by default."
- "Run the reading list's first batch through the methods pipeline and report adoptable methods with their evidence rank."
