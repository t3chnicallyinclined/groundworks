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
- **Not in this repo:** the first instance's code and data. The instance code is still coupled to its matter; extracting a generic engine is roadmap Track C.

## Privacy boundary (non-negotiable)

This repository contains **no client or matter data**, ever:
- no names, record text, labels, or case-specific seeds
- engine code enters only after a scripted scan for matter identifiers
- demos use public corpora only

## Layout

| Path | What |
|---|---|
| `docs/METHOD.md` | The method: what is unique, primitives, layers, the eight configuring questions, evidence model, lenses, improvement loop, self-improvement fence, lineage, technology, optimization, use cases, cross-field discovery design, licensing, privacy |
| `docs/MODULES.md` | Engine modules, file contracts (units, labels, vocabulary, gate rules), modularity rules |
| `instances/` | 19 domain specs mapping the method (eight answers, evidence model, gates with probes, roles, lenses, evaluator, walkthrough, usage, synthetic worked example, pitfalls, module reuse) plus a template and `instance.yaml` schema |
| `.claude/skills/design-instance/` | The skill that maps the method onto a new domain |
| `scripts/privacy_scan.py` | Deny-list scan (list kept outside the repo); runs as a pre-commit hook; fails closed |
| `docs/ROADMAP.md` | Tracks for the engine, pilots and product, with acceptance tests |
| `docs/DECISIONS.md` | Method-level decisions and open questions |
| `notes/` | Dated design notes: how the idea developed and why |
| `research/READING_LIST.md` | Papers and repos to run the scholar over, with the question each should answer |
| `research/SOURCES_2026-09-15.md` | Market and technology scan, graded by source rank |
| `.claude/agents/scholar-architect.md` | The expert agent for designing and building the system |
| `CLAUDE.md` | Rules for any session working in this repo |

## Using the expert

Open this repo in Claude Code and ask for the `scholar-architect` agent, for example:
- "Design an instance for a systematic review of X: answer the eight questions."
- "Review this gate change for checks that pass by default."
- "Run the reading list's first batch through the methods pipeline and report adoptable methods with their evidence rank."
