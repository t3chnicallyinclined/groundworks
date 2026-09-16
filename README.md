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
| `engine/panel/` | Panel module: several lenses judge one target under a program-checked protocol (`PROTOCOL.md`, `panel.py`, tests, `NOTICE.md`) |
| `.claude/skills/panel/` | The skill that runs a panel |
| `engine/mcp/` | MCP front door: domain-free tools over an instance adapter, status envelope, launch-stamped provenance, no raw query or delete tools |
| `engine/embed/` | Local embeddings and a retrieval bake-off (keyword, dense, hybrid) with confidence intervals; models adopted only if they beat keyword search |
| `docs/LICENSING.md`, `THIRD_PARTY_NOTICES.md`, `scripts/license_check.py` | Licensing policy, notices, and the commit-time license check |
| `engine/patterns/` | Proven datastore patterns: truth maintenance, archive bin, stable edge ids |
| `scripts/privacy_scan.py` | Deny-list scan (list kept outside the repo); runs as a pre-commit hook; fails closed |
| `docs/ROADMAP.md` | Tracks for the engine, pilots and product, with acceptance tests |
| `docs/DECISIONS.md` | Method-level decisions and open questions |
| `notes/` | Dated design notes: how the idea developed and why |
| `research/LINEAGE_STUDY.md` | The twelve known techniques inside the method (integrity constraints, write-audit-publish, medallion layers, truth maintenance, evidence grading, provenance, knowledge fusion, argumentation, verified annotation, recursive language models, autoresearch, neuro-symbolic), each with where it lives in our system, study questions and sources |
| `research/READING_LIST.md` | Papers and repos to run the scholar over, with the question each should answer |
| `research/SOURCES_2026-09-15.md` | Market and technology scan, graded by source rank |
| `.claude/agents/scholar-architect.md` | The expert agent for designing and building the system |
| `CLAUDE.md` | Rules for any session working in this repo |

## Try it (five minutes, nothing to download but SurrealDB)

```
python scripts/run_instance.py instances/demo-incident --memory 8025
```

That runs a complete non-legal instance on a throwaway in-memory database: config, generated schema and gates, a reject-and-accept probe per rule, intake, chunking, labelling (a stand-in rule labeller; real instances use agents), the verbatim validator, the loader, and a walkthrough where the datastore refuses each premature promotion and says exactly what is missing. Outputs land in `instances/demo-incident/out/`.

To map your own domain: copy `instances/demo-incident/`, answer the eight questions in `instance.yaml`, write an adapter that yields units with exact locators, and run the same command.

## Using the expert

Open this repo in Claude Code and ask for the `scholar-architect` agent, for example:
- "Design an instance for a systematic review of X: answer the eight questions."
- "Review this gate change for checks that pass by default."
- "Run the reading list's first batch through the methods pipeline and report adoptable methods with their evidence rank."
