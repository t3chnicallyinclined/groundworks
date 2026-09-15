# Lineage study: the known techniques inside the Scholar

**Written 2026-09-15.** There is no single established name for the whole method. Each piece is a known technique. This file records each one so it can be studied in depth later: what it is, where it lives in our system, what studying it should answer, and what to read.

**Source status:**
- `found`: located by search, not yet read
- `read`: read in full
- `memory`: cited from memory; locate and verify before relying on it

As of this writing nothing below has been read in depth, except autoresearch and our own systems.

## Summary

| # | What we do | Known name |
|---|---|---|
| 1 | The database refuses "confirmed" without enough evidence | Integrity constraints (a database check rule) |
| 2 | Data is staged, checked, and only then promoted | Write-audit-publish, a data-engineering pattern |
| 3 | Layers that go raw, then validated, then curated | Medallion architecture (bronze, silver, gold) |
| 4 | Beliefs stored with their reasons; eliminated beliefs kept | Truth maintenance systems (Doyle 1979) |
| 5 | Evidence ranked by source quality | Admiralty Code (intelligence), GRADE (medicine) |
| 6 | Every claim carries where it came from | Provenance (W3C PROV); nanopublications package each assertion with its provenance |
| 7 | Extracting facts from huge text with confidence scores | Knowledge-base construction with knowledge fusion (e.g., Google's Knowledge Vault) |
| 8 | Arguments, supporting evidence, undercutting evidence, attacks | Argumentation frameworks (Toulmin, Dung, Pollock) |
| 9 | Agents label everything and a program checks the quotes | LLM-assisted annotation with verification; related to weak supervision |
| 10 | Reading a corpus too big for context by querying it with code | Recursive Language Models |
| 11 | Keep a change only if a fixed test says it's better | Autoresearch; hill-climbing on a fixed fitness function |
| 12 | Model proposes, rules decide | Neuro-symbolic systems |

**Whole-method status.** I did not find one named method combining all twelve. That rests on searches on 2026-09-15; absence in a search is not proof. Revisit after the reading below.

---

## 1. Integrity constraints

**What it is.** Rules the database enforces on every write (CHECK constraints, assertions, triggers), so invalid states cannot be stored no matter which program writes them.

**Where it lives in ours.** Gate library; `ASSERT` on status, verified and ready fields; probe tests that must show both rejection and acceptance.

**Study questions:**
- Our loader writes rows and links first, then statuses in a second pass. That is manual deferral. How do SQL deferred constraints (checked at commit rather than per statement) compare, and could transactions replace the two-pass loader?
- Assertions over multiple tables (SQL's CREATE ASSERTION) are rarely implemented. What do databases that support cross-row checks do about performance, and what does that mean at millions of units?
- A rule checked at write time does not re-check when its evidence is later demoted. How do triggers or materialized checks handle that, and should re-checking happen at the database level?

**Read:** SQL standard material on CHECK, ASSERTION and deferrable constraints (memory); SurrealDB `DEFINE FIELD ... ASSERT` documentation (found); Postgres constraint and trigger documentation (memory).

## 2. Write-audit-publish (WAP)

**What it is.** A data-engineering pattern. New data is written to a staging area, audited by automated checks, and published to consumers only if the checks pass.

**Where it lives in ours.** Label files are staged, the validator audits them, and the loader publishes only passing chunks. Promotion helpers write, the gate audits, and the status publishes.

**Study questions:**
- Table-format branching (write to a branch, audit, merge) could let whole relabeling runs be staged and rolled back atomically. How would that map onto our graph?
- How do WAP pipelines report partial failure without half-publishing?
- Which audit types (schema, volume, distribution drift) are we missing? For example: an alarm when a new run's tier distribution shifts sharply.

**Read:** write-audit-publish write-ups from data-engineering practice, and table-format branching documentation such as Apache Iceberg (memory).

## 3. Medallion architecture

**What it is.** Data organized in quality layers: bronze (raw, as ingested), silver (cleaned and validated), gold (curated, business-ready).

**Where it lives in ours.** Evidence units are bronze. Validated agent labels are silver. Gated facts, ready conclusions and verified citations are gold. Lenses serve gold and silver with grades shown.

**Study questions:**
- Where should lineage between layers be recorded so any gold item can be traced to bronze in one query?
- How do medallion systems rebuild silver and gold when bronze is corrected? That is the same problem as our supersession.
- Should agent-grade labels sit in a distinct layer from mechanically validated fields?

**Read:** Databricks medallion architecture documentation (memory).

## 4. Truth maintenance systems (TMS)

**What it is.** From AI research: a system that stores each belief together with its justifications and revises beliefs when justifications change. Doyle's justification-based TMS (1979) and de Kleer's assumption-based TMS (1986), which tracks many contexts at once.

**Where it lives in ours.** Facts with `cites`, `supersedes`, `corrects`, `ruled_out` with evidence, and drift reports.

**Known gap, now prototyped (2026-09-15).** Automatic propagation was proven inside the database on SurrealDB 3.1.4, 7 of 7 checks passed:
- a computed effective status
- event rules that demote facts when evidence is downgraded or uncited
- a cascade to conclusions

Pattern: `engine/patterns/tms_propagation.surql`; results and risks: `notes/2026-09-15_automatic_demotion.md`. Not yet installed in any instance. The codebase-experts design extends it with span hashes.

**Study questions:**
- How exactly does a justification-based TMS propagate retraction, and what is the cost at scale?
- Can the assumption-based TMS's many contexts serve as a formal model for lenses (each lens a set of assumptions)?
- How are cycles and nonmonotonic justifications handled ("X unless Y")?

**Read:** Doyle, "A Truth Maintenance System," *Artificial Intelligence* (1979) (memory); de Kleer, "An Assumption-based TMS," *Artificial Intelligence* (1986) (memory); Forbus and de Kleer, *Building Problem Solvers* (1993) (memory).

## 5. Evidence grading: Admiralty Code and GRADE

**What it is.** Two established ranking systems.
- **Admiralty Code (intelligence):** grades source reliability and information credibility separately, reliability A to F and credibility 1 to 6.
- **GRADE (evidence-based medicine):** rates the certainty of a body of evidence (high, moderate, low, very low), downgrading for risk of bias, inconsistency, indirectness, imprecision and publication bias.

**Where it lives in ours.** Fidelity ladder, status axis, authority axis; separate grading of source and claim; certainty downgrade reasons stored on edges.

**Study questions:**
- Our axes are fidelity, status and authority. Should a fourth axis, source reliability (who produced it) separate from information credibility (is this claim plausible), be added?
- GRADE rates a body of evidence, not a single item. How should a conclusion's certainty be computed from its supports and undercuts, instead of counted?
- Can GRADE's upgrade criteria (large effect, dose-response) inspire rules that raise confidence mechanically?

**Read:** NATO intelligence doctrine describing source and information grading (memory; find the current official text); Guyatt et al., "GRADE: an emerging consensus on rating quality of evidence and strength of recommendations," *BMJ* 2008 (memory); the GRADE handbook (memory).

## 6. Provenance: W3C PROV and nanopublications

**What it is.**
- **W3C PROV** is a standard vocabulary for describing where data came from: entities, activities, agents, and derivation.
- **Nanopublications** package the smallest publishable assertion in three parts: the assertion, its provenance, and publication information.

**Where it lives in ours.** `cites`, `in_doc`, `labeled_by`, `via` and `wrote_at` stamps; locators on every unit.

**Study questions:**
- Should graph exports use PROV terms so other systems can read our lineage?
- Is a nanopublication the right unit for sharing a confirmed fact or a verified citation across instances, or with collaborators, without sharing the corpus?
- How do nanopublication systems make identifiers tamper-evident (content-hashed identifiers)?

**Read:** W3C PROV-O Recommendation (2013) (memory); Mons and Velterop on nano-publication (2009) (memory); work on content-hashed identifiers for nanopublications (memory).

## 7. Knowledge-base construction and knowledge fusion

**What it is.** Building large knowledge bases by extracting facts from text at scale and fusing many noisy extractions into probabilities. Examples: Google's Knowledge Vault, and statistical-inference systems such as DeepDive.

**Where it lives in ours.** Stage 1 labeling and loading. **The deliberate difference:** they assign probabilities; we promote only on rules about evidence rank, and keep agent output as artifacts.

**Study questions:**
- Where does probabilistic fusion beat rule-based gating (huge, redundant corpora), and where does it fail (a single decisive page)?
- Could fusion be used only for **candidate ranking** (which labels to adjudicate first), with gates still deciding promotion?
- How did these systems measure extraction precision and recall at scale? That is our label-quality question.

**Read:** Dong et al., "Knowledge Vault: A Web-Scale Approach to Probabilistic Knowledge Fusion," KDD 2014 (memory); the DeepDive papers (memory).

## 8. Argumentation frameworks

**What it is.** Formal and informal models of argument:
- **Toulmin:** claim, grounds, warrant, backing, qualifier, rebuttal.
- **Dung:** abstract argumentation, where arguments attack each other and "acceptable" sets are computed.
- **Pollock:** defeasible reasoning, with rebutting defeaters (evidence against the conclusion) versus undercutting defeaters (evidence against the inference).
- **Structured frameworks** such as ASPIC+ combine these.

**Where it lives in ours.** `argument`, `supports`, `undercuts`, `critique`, `attacks{verdict}`, `answer`, and the readiness gate.

**Study questions:**
- Could readiness be **computed** from the attack graph (a Dung-style acceptability semantics) instead of hand-set verdicts? Which semantics fits "survives every attack that is not itself defeated"?
- Should our schema split `undercuts` (attacks the inference) from a new `rebuts` edge (attacks the conclusion)?
- Should warrants (the rule connecting evidence to conclusion) become explicit nodes, so attacks can target the rule itself?

**Read:** Toulmin, *The Uses of Argument* (1958) (memory); Dung, "On the acceptability of arguments...," *Artificial Intelligence* (1995) (memory); Pollock, "Defeasible Reasoning," *Cognitive Science* (1987) (memory); Modgil and Prakken on ASPIC+ (memory); *Context Graphs for Legal Reasoning and Argumentation*, arXiv 2007.00732 (found).

## 9. LLM-assisted annotation with verification; weak supervision

**What it is.** Language models label data, and humans or programs verify a targeted subset. **Weak supervision** (for example Snorkel) models many noisy labeling sources to estimate true labels without full ground truth.

**Where it lives in ours.** Stage 1 labeling, the verbatim validator, planned double-labeling and adjudication.

**Study questions:**
- How do these methods pick which labels humans should verify first (uncertainty, disagreement, salience)?
- Can weak-supervision label models estimate each agent's accuracy from agreement patterns, giving label quality without adjudicating everything?
- What agreement statistics suit ordinal tiers and multi-label issue codes?

**Read:** Wang et al., "Human-LLM Collaborative Annotation Through Effective Verification of LLM Labels," CHI 2024 (found); Ratner et al., "Snorkel: Rapid Training Data Creation with Weak Supervision," VLDB 2017 (memory); *Fixing FOLIO and MALLS*, arXiv 2606.02837 (found); *LLMs build inconsistent knowledge graphs from one immune-checkpoint corpus*, PMC 13529944 (found).

## 10. Recursive Language Models (RLM)

**What it is.** An inference strategy where the model treats a long input as data in a code environment, examines and splits it programmatically, and calls itself or sub-models on pieces. It handles inputs far beyond the context window.

**Where it lives in ours.** Every labeling agent reads its slice with Python in batches and returns a summary, not the labels. The Forgily Book Scholar used the RLM library directly.

**Study questions:**
- What decomposition strategies did the paper find best, and at what lengths do they fail?
- Should sub-model calls return structured data only, as the Book Scholar did, or keep raw text visible for quote verification? We chose visibility.
- How do RLM costs scale compared with our fixed chunking?

**Read:** Zhang, Kraska and Khattab, *Recursive Language Models*, arXiv 2512.24601 (found); github.com/alexzhang13/rlm (found); the author's blog post (found).

## 11. Autoresearch; hill-climbing on a fixed fitness function

**What it is.** An agent edits one artifact, runs a fixed evaluation it cannot modify, keeps the change if the score improves and discards it otherwise, logs every attempt, and prefers simpler changes. This is hill-climbing against a fixed fitness function.

**Where it lives in ours.** The improvement loop design; the self-improvement fence (humans own the evaluator); `tried_on` outcomes as the attempt log.

**Study questions:**
- Hill-climbing gets stuck in local optima. What do research-agent forks add (restarts, populations, multi-agent)?
- How can a judged metric (a language-model panel) be kept from being gamed? Held-out prompts, multiple judges, mechanical gates as hard constraints.
- What is the right fixed metric for knowledge work with no ground truth?

**Read:** github.com/karpathy/autoresearch README and program.md (read 2026-09-15); its notable forks (found); *AI scientists produce results without reasoning scientifically*, arXiv 2604.18805 (found); *The AI Scientist*, arXiv 2408.06292 (memory).

## 12. Neuro-symbolic systems

**What it is.** Systems that combine neural models (learning, language, perception) with symbolic components (rules, logic, constraints, search). A closely related pattern for language models: the model generates candidates and external verifiers or critics check them, sometimes called LLM-Modulo.

**Where it lives in ours.** Agents propose labels, critiques and drafts. Symbolic rules (validator, datastore gates, citation verdicts, probes) decide what stands.

**Study questions:**
- Which verifier designs give the strongest guarantees for which task types?
- How should verifier feedback be fed back into generation: repair loops, bounded retries?
- Where do symbolic rules become too rigid, and how should exceptions be recorded without weakening gates? Our `preservation_exception` field is one example.

**Read:** Garcez and Lamb on neurosymbolic AI (memory); Kambhampati et al. on LLM-Modulo frameworks (2024) (memory).

---

## How to run this study with the scholar

Treat this file as the goal file for a methods-brain pass (`instances/methods-brain/INSTANCE.md`):
1. Fetch each source to local text.
2. Extract claims that answer the study questions above, each with a verbatim quote and location.
3. Grade the sources and mark `read` here.
4. Record each potential improvement as a candidate for the improvement loop.
5. Adopt nothing without a reproduction in our pipeline.
