# XScholar (working name)

A method and engine for making AI agents reliably expert in one large body of material with a stated goal:
- **Agents read all of it, under mechanical checks.** Their judgments go into a knowledge graph whose database refuses to treat anything as established without evidence of sufficient rank.
- **Nothing is lost or taken on faith.** Dead ends are kept. Adversarial roles attack every conclusion. Expert lenses view the same evidence without editing it.
- **Improvements must beat a fixed evaluator,** one the agents cannot edit.

Descriptive term: **evidence-gated knowledge graph built by exhaustive, verified agent reading.**

## Status (2026-09-15)

- **Method written down:** `docs/METHOD.md`.
- **First instance built and running privately:** an appellate case record.
  - 9,101 units read by 77 agents; 77 of 77 chunks passed the verbatim validator.
  - Datastore gates proven by probe tests; lookup and compaction hooks live.
  - Four role skills; a regression suite of past errors; a framework lens with a factor matrix.
- **Not in this repo:** any instance's code or data. **An instance lives in its own project** (its adapters, vocabularies, settings and material); this repo holds the method, the engine and domain specs with synthetic examples. Extracting the still-coupled generic parts from the first instance is roadmap Track C.

## Privacy boundary (non-negotiable)

This repository contains **no client or matter data**, ever:
- no names, record text, labels, or case-specific seeds
- engine code enters only after a scripted scan for matter identifiers
- demos use public corpora only

## Layout

| Path | What |
|---|---|
| `docs/METHOD.md` | The method: what is unique, primitives, layers, the eight configuring questions, evidence model, lenses, improvement loop, self-improvement fence, lineage, technology, optimization, use cases, cross-field discovery design, licensing, privacy |
| `docs/ARCHITECTURE.md` | The system: layers, invariants, what an instance supplies vs what is generated, each layer's fixed test, how to map a new domain, open design decisions |
| `docs/TUNING.md` | Per layer: the knobs, defaults, how to measure a change, failure modes, measured starting points |
| `docs/MODULES.md` | Engine modules, file contracts (units, labels, vocabulary, gate rules), modularity rules |
| `instances/` | 19 domain specs mapping the method (eight answers, evidence model, gates with probes, roles, lenses, evaluator, walkthrough, usage, synthetic worked example, pitfalls, module reuse) plus a template and `instance.yaml` schema |
| `.claude/skills/design-instance/` | The skill that maps the method onto a new domain |
| `xscholar/panel/` | Panel module: several lenses judge one target under a program-checked protocol (`PROTOCOL.md`, `panel.py`, tests, `NOTICE.md`) |
| `.claude/skills/panel/` | The skill that runs a panel |
| `xscholar/mcp/` | MCP front door: domain-free tools over an instance adapter, status envelope, launch-stamped provenance, no raw query or delete tools |
| `xscholar/embed/` | Local embeddings and a retrieval bake-off (keyword, dense, hybrid) with confidence intervals; models adopted only if they beat keyword search |
| `docs/LICENSING.md`, `THIRD_PARTY_NOTICES.md`, `scripts/license_check.py` | Licensing policy, notices, and the commit-time license check |
| `xscholar/patterns/` | Proven datastore patterns: truth maintenance, archive bin, stable edge ids |
| `scripts/privacy_scan.py` | Deny-list scan (list kept outside the repo); runs as a pre-commit hook; fails closed |
| `docs/ROADMAP.md` | Tracks for the engine, pilots and product, with acceptance tests |
| `docs/DECISIONS.md` | Method-level decisions and open questions |
| `notes/` | Dated design notes: how the idea developed and why |
| `research/LINEAGE_STUDY.md` | The twelve known techniques inside the method (integrity constraints, write-audit-publish, medallion layers, truth maintenance, evidence grading, provenance, knowledge fusion, argumentation, verified annotation, recursive language models, autoresearch, neuro-symbolic), each with where it lives in our system, study questions and sources |
| `research/READING_LIST.md` | Papers and repos to run the scholar over, with the question each should answer |
| `research/SOURCES_2026-09-15.md` | Market and technology scan, graded by source rank |
| `.claude/agents/scholar-architect.md` | The expert agent for designing and building the system |
| `CLAUDE.md` | Rules for any session working in this repo |

## Start here

**Prerequisites:** Python 3.11+ and the SurrealDB binary (`https://surrealdb.com/install`, or set `XSCHOLAR_SURREAL` to its path). Nothing else; the core has no third-party dependencies.

```
git clone https://github.com/t3chnicallyinclined/xscholar.git
cd xscholar
python -m xscholar.cli test                                      # every suite + the license check (about 20 s)
python scripts/run_instance.py instances/demo-incident --memory 8025   # a whole instance, end to end
```

The demo runs a complete non-legal instance on a throwaway in-memory database: config, generated schema and gates, a reject-and-accept probe for every rule, intake, chunking, labelling (a stand-in rule labeller; real instances use agents), the verbatim validator, the loader, and a walkthrough in which the datastore refuses each premature promotion and says exactly what is missing. Outputs land in `instances/demo-incident/out/`.

### What you need, and nothing else

Two things. The engine's `dependencies` are `[]` -- Python standard library only.

| | |
|---|---|
| Python | 3.11 or newer |
| SurrealDB | **3.1.x** ([install](https://surrealdb.com/install)), or set `XSCHOLAR_SURREAL` to the binary |

**There is no container, and that is deliberate.** There were a `Dockerfile` and a `compose.yaml` here until 2026-09-16, and they had never once been run. This repo's standing rule is that a path nobody has executed is not a working path, however good it looks in a README, so they were deleted rather than left as decoration. One binary and one command is the whole deployment story; if you later need a container for distribution, write it against a real deployment and measure it. The files are in git history.

**The version is checked, not hoped for.** Pinning a database version was the one real thing the image did, and a version is a fact the server will tell you: `Db.ping()` asks `/version` and refuses a different major series by name, because this engine's behaviour is *measured* against 3.1 -- a schema re-apply silently drops a `CHANGEFEED`, a delete carries no before-image, `DEFINE USER ... ON DATABASE` bypasses table `PERMISSIONS`, and a denied write comes back as an empty result with status OK. Each of those is load-bearing somewhere in this repo. A different 3.x minor is a printed warning, not a refusal.

**Running against a database you keep.** The demo uses `--memory` for a throwaway. For a persistent one, start SurrealDB yourself and point the runner at it:

```
surreal start --bind 127.0.0.1:8002 --user root --pass root rocksdb:/path/to/data
python scripts/run_instance.py instances/demo-incident --url http://127.0.0.1:8002/sql
```

Back it up with `surreal export` on whatever scheduler your machine already has (cron, systemd timer, Task Scheduler). That is one line, it runs on the host, and it fails visibly.

**SurrealDB's license.** SurrealDB is Business Source License 1.1 (`docs/LICENSING.md` s.1). You install it yourself, so this repo distributes none of it -- but if you ever bundle or redistribute it, its `LICENSE` must stay conspicuous and `THIRD_PARTY_NOTICES.md` names it.

### If you are an agent working in this repository

1. Read `CLAUDE.md` first. Its rules are binding: no instance data here, verify before claiming, gates tested both ways, checks that pass by default are the enemy, the evaluator is human-owned, licensing by class.
2. Run `python -m xscholar.cli test` before and after any change. Green is the only definition of done.
3. Read `docs/ARCHITECTURE.md` (what the system is, and who is at the gate), then `docs/TUNING.md` (what each layer's knobs do), then `docs/MODULES.md` (the file contracts you will write against).
4. To add a capability: put generic code under `xscholar/<module>/`, tests under `tests/`, and test it with a non-legal synthetic instance. Domain words never enter engine code.
5. To map a new domain: use the `design-instance` skill, or copy `instances/demo-incident/`, answer the eight questions in `instance.yaml`, write an adapter that yields units with exact locators, and run the same command. The instance lives in **its own project**, never here.
6. Record decisions in `docs/DECISIONS.md`, dated reasoning in `notes/`, and every new dependency or adapted item in `THIRD_PARTY_NOTICES.md` (the pre-commit check enforces it).
7. Report the unflattering number next to the flattering one. Say UNKNOWN rather than guess.

### If you are a person deciding whether to use this

Read `docs/METHOD.md` section 1 (one paragraph) and section 12 (which domains fit), then run the demo above and read its output: each REFUSED line is the method working.

## Layout of the engine

| Path | What |
|---|---|
| `xscholar/config.py` | The instance config: the eight answers, validated, as the single source of truth |
| `xscholar/gates.py` | Generates the datastore schema, the promotion gates, and a reject-and-accept probe per rule |
| `xscholar/db.py` | Minimal SurrealDB client that fails loudly |
| `xscholar/pipeline/` | intake, chunker, validator, loader, and the typed write surface |
| `xscholar/panel/` | Several lenses judge one target under a program-checked protocol |
| `xscholar/present/` | Renderings for human verification, with the inline rule re-check |
| `xscholar/mcp/` | The MCP front door agents use; `instance_adapter.py` gives any `instance.yaml` all tools with no adapter code |
| `xscholar/hooks/` | Session-start health warning and the pre-tool lookup that speaks before a mistake is made |
| `xscholar/embed/` | Local embeddings and the retrieval bake-off |
| `xscholar/patterns/` | Proven datastore patterns |
| `tests/` | Every suite; `python -m xscholar.cli test` runs them all |
| `scripts/` | The instance runner, the privacy scan, the license check |

## License

Copyright (c) 2026 t3chnicallyinclined. All rights reserved (see `LICENSE`). Adapted and depended-upon third-party material is listed in `THIRD_PARTY_NOTICES.md` under its own licenses.

## Using the expert

Open this repo in Claude Code and ask for the `scholar-architect` agent, for example:
- "Design an instance for a systematic review of X: answer the eight questions."
- "Review this gate change for checks that pass by default."
- "Run the reading list's first batch through the methods pipeline and report adoptable methods with their evidence rank."
