# Changelog

## 0.1.0b1 (unreleased, 2026-09-15)

First beta: the engine runs a complete instance from one config file, agents work through MCP, and every layer has a fixed test.

### The system
- **Instance config as the single source of truth** (`xscholar/config.py`): the eight answers in one YAML or JSON file, validated with every error listed. A strict YAML subset parser with no third-party dependency.
- **Generated schema, gates and probes** (`xscholar/gates.py`): one datastore ASSERT per rule, and for every rule a write that must be refused plus the same write accepted once the evidence exists. Proven to catch a weakened gate and to refuse raw writes, not only helper calls.
- **Pipeline** (`xscholar/pipeline/`): intake with contract checks and exclusion logging; chunker (every unit in exactly one slice); validator (coverage, vocabulary, tier rules, verbatim quotes); loader with stable edge ids and a sweep that a failed write blocks; the typed write surface that stamps provenance and explains every refusal; the label-agreement harness (kappa, per-field agreement, adjudication list).
- **Panel protocol** (`xscholar/panel/`): blind round, anonymized cross-examination, verbatim flaw quotes, locked panel, stance lines, dissent quota, novelty and agreement checks, owner-held thresholds and confidence weights, weighted tally with splits returned to the human.
- **Reading layer** (`xscholar/present/`): renderings for human verification with 100% preservation, check-first ordering, the inline rule re-check, seeded-error planting and reviewer scoring.
- **MCP front door** (`xscholar/mcp/`): 14 domain-free tools; a generic adapter gives any instance the full set with no adapter code; gate refusals come back as `rejected_by_gate` with what is missing named; source text only under an untrusted key.
- **Hooks** (`xscholar/hooks/`): a session-start line that warns when the graph is off, and a pre-tool lookup that explains unit and claim ids and flags an eliminated claim about to be repeated.
- **Retrieval bake-off** (`xscholar/embed/`): local embeddings against a keyword baseline with confidence intervals.
- **Demo instance** (`instances/demo-incident`): a complete non-legal instance, runnable in one command, labelled by real agents in the first run.

### Measured on the way (generic numbers; details in `notes/`)
- Keyword search beat two embedding models and a hybrid on a 7,995-unit corpus; no model adopted.
- Six real panels: 3 consensus, 3 splits; 0 of 18 stances changed between rounds; anonymization showed no effect at n=1.
- Reading-layer reviewers: relabelled claims were caught 0 of 9 times by reading alone in three layouts; a tuned rule re-check flags them with 0 false flags on clean documents; a flag that says only "fails a rule" was still skipped (wording is the next variable).
- Two agents labelled the demo instance: 66 of 66 validated on the first run; agreement with the intended reading, tier kappa 0.93.

### Licensing
- Proprietary (`LICENSE`); adapted material and dependencies in `THIRD_PARTY_NOTICES.md`; a license check and a privacy scan run at every commit.

### Known gaps (why this is a beta)
- The first private instance still runs on its own schema; moving it onto the engine is scheduled by its owner.
- Role skills and lens views are not yet generated from the config.
- Per-agent datastore credentials, change-feed work queue, and bitemporal columns are designed, not built.
- Label accuracy has been measured only against a rule stub; two independent agents with human adjudication on a real instance is still owed.
