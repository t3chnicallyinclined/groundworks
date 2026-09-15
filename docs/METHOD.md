# The Scholar method

**Working name:** XScholar (naming deferred; see DECISIONS.md).
**Status:** written 2026-09-15.
**Contents:** a general, case-free description of the method. The first instance, a New York appeal, is documented separately in `09_appeal/kb/ARCHITECTURE_APPEALS_FIRM.md` and is the source of every measured number below.
**Rule for this file:** no client data. Examples from the first instance are described generically.

---

## 1. What it is, in one paragraph

A system that lets AI agents become reliably expert in one large, long-running body of material with a stated goal. The goal might be to establish an error in a court record, to establish whether a treatment works, or to find where one research field solves another's problem.

- Agents read **all** of the material, not a retrieved sample, under mechanical checks.
- Their judgments go into a knowledge graph where **the database itself refuses** to treat anything as established unless it is backed by evidence of sufficient rank.
- Dead ends and failed attempts are kept.
- Adversarial roles attack every conclusion, and their verdicts are stored.
- The same evidence is viewed through different expert **lenses** without ever being edited.
- Improvements are kept only when a **fixed evaluator the agents cannot edit** says they are better.

Descriptive term: an **evidence-gated knowledge graph** built by **exhaustive, verified agent reading**.

---

## 2. What is unique (and what is not)

**Not new:** every component has a lineage (section 9). **New:** the combination, and the discipline that makes the combination hold under volume. Specifically:

1. **Exhaustive reading with proof of coverage.** Every unit of the corpus is labeled exactly once, and a validator proves it. Retrieval systems cannot say what they did not read; this can.
2. **Verbatim-anchored judgment.** Every important label carries a quote that a program confirms appears word for word in that unit. Fluent misquotation, the dominant failure of AI writing about sources, is rejected mechanically.
3. **Promotion enforced by the datastore, on every path.** "Confirmed," "verified," "ready" are written only if the database finds evidence of sufficient rank linked to the row at the moment of writing. Raw database access does not bypass it. Proven by probe tests that must see both rejections and acceptances.
4. **Several independent evidence axes.** Fidelity (are these the exact words?), status (may this be used at all?) and authority (does it bind or merely inform?) are graded separately, so a true statement from an unusable source is caught.
5. **Negative knowledge is first-class and cheaper to record than positive.** Eliminated claims are stored with what the source actually shows, and are pushed to agents automatically before they repeat them.
6. **Outcomes live on edges.** Every framing tried against a conclusion, and every attack on it, carries its verdict and scope on the link, not as a property of the conclusion.
7. **Adversarial roles are part of the pipeline, not an afterthought.** An argument cannot be marked ready until it has been attacked, with no fatal head standing and every damaging head answered.
8. **Lenses are projections, never copies.** Any number of expert perspectives over one immutable evidence layer; disagreement between lenses is preserved as data.
9. **Bulk ingest never writes conclusions.** Machine output lands as artifacts with an explicit agent grade. Promotion to a conclusion is one row at a time, with evidence.
10. **The system audits itself.** Regression suites built from the project's own past mistakes; health reports that print the unflattering number next to the flattering one; context hooks that make the graph speak at the moment a mistake would be made.

---

## 3. The six primitives

| # | Primitive | What it does | Origin |
|---|---|---|---|
| 1 | **Goal file** | Plain-language program: what we are trying to establish, what counts as progress, what is off limits. Humans edit it; agents do not. | karpathy/autoresearch `program.md` |
| 2 | **Corpus as a variable** | Source material is data the agent queries with code in batches, never pasted wholesale into context. | Recursive Language Models (Zhang, Kraska, Khattab); Forgily Book Scholar |
| 3 | **Curriculum and quality gates** | Phases with fixed deliverables; a validator fails incomplete or unsupported output and the agent repairs until it passes. | Forgily Book Scholar |
| 4 | **Graded belief store** | Ranked evidence, datastore-enforced promotion, recorded dead ends, outcomes on edges, no conclusions from bulk ingest. | SUPERGUN `re_kb` |
| 5 | **Fixed evaluator and keep/discard loop** | Keep a change only if a fixed evaluation, which the agent cannot edit, says it is better; log every attempt; simpler wins ties. | karpathy/autoresearch |
| 6 | **Lenses** | Expert perspectives as views and lens-keyed overlays over immutable evidence. | This project |

---

## 4. The architecture, layer by layer

```
 L6  Instruments   hooks (inject known facts/dead ends at tool time; carry state across compaction)
                   skills (roles: red team, cold bench, premortem, claim audit, ...)
                   evals  (regression suite of past errors; gate probe tests; health reports)
 L5  Lenses        computed views + lens-keyed edges (filing under an expert framework, per-reader views)
 L4  Workbench     critiques + attacks{verdict}, answers, events + mentions{stance}, output citations{verdict}
                   GATE: conclusion "ready" only if supported, preserved/eligible, attacked, answered, authorities verified
 L3  Assertions    facts{status}, conclusions/arguments{rating}, authorities{verified}, approaches + tried_on{outcome}
                   GATE: confirmed needs top-grade evidence; verified needs official text; ruled_out never gated
 L2  Labels        one label per unit: tier, direction, issue codes, targets, role, reason, verbatim quote, grade=agent
                   GATE: validator (coverage, vocabulary, reason, verbatim quote, status wording)
 L1  Evidence      immutable units with exact locators (page:line, message id, page), status (usable or not), hashes
 L0  Intake        raw files -> text with locators; duplicates and work product excluded and logged
```

**Flow.** L0 to L1 is deterministic code. L1 to L2 is agents in parallel, one chunk each, gated by the validator. L2 loads into the graph as artifacts. L3 and L4 are written one row at a time through typed helpers and datastore gates. L5 projects. L6 watches everything.

**The unit of work for agents is a chunk:** a slice of units small enough for one agent (about 220 transcript turns, or whole message days up to about 900 messages, or about 45 pages). The agent reads its slice with code, labels in batches of about 25, writes a label file, and runs the validator until it passes. It returns a short summary, never the labels themselves, so the orchestrator's context stays small.

---

## 5. The eight questions that configure an instance

Change these answers and the machinery serves a new domain.

| # | Question | First instance's answer (generic form) |
|---|---|---|
| 1 | **Goal:** what are we trying to establish? | A reversible error in a trial court's order |
| 2 | **Unit of evidence:** smallest citable thing | Transcript line or turn; message; record page |
| 3 | **Fidelity ladder:** what counts as read from the source? | page image > text extract with locator > agent report > recollection |
| 4 | **Status axis:** may it be used at all? | In the appellate record (testimony, received exhibit, court file) vs not |
| 5 | **Vocabulary:** the expert's own categories | The legal test's factors as the court listed them; issues; findings; preservation; standard of review |
| 6 | **Gates:** what the database refuses | Unbacked confirmed facts; unverified authorities; arguments not preserved, unsupported, unattacked or unanswered |
| 7 | **Roles:** who attacks, judges, audits | Opposing counsel, child's attorney, deferential judge, three-judge cold bench, premortem, citation auditor |
| 8 | **Evaluator:** the fixed test agents cannot edit | Verbatim quote checks, citation verdicts, past-error regression suite, fixed judge panel |

---

## 6. Evidence model

**Fidelity ladder** (generic): original artifact read directly (page image, raw log, dataset) > faithful extract with exact locator > secondary account > agent report > recollection. Only the top two may confirm.

**Status axis** (generic): usable / restricted / unusable, with the domain's definition (in the record; admissible; peer-reviewed; licensed for use; in scope).

**Authority axis** (generic): binding or definitive > persuasive or authoritative > supporting > pointer-only. Verified only when read in official or primary text; existence is never authority.

**Label tiers:** 0 procedural/noise, 1 context, 2 relevant (needs reason and quote), 3 load-bearing (needs reason and quote).

**Direction:** relative to the goal holder: for / against / mixed / neutral. Against-direction material is labeled as carefully as for-direction material; the opposing side's best evidence is what the output must answer.

**Graded output verdicts** (for any citation in a deliverable): VERIFIED, MISMATCH (resolves but differs; print what the source says), NOT_FOUND (not proof of falsity), OUTSIDE_STATUS (true but unusable), MISATTRIBUTED (right words, wrong source), INTERPOLATED (source partly supports; added detail named), UNCHECKABLE (say what is needed). Two ways to fail are kept apart: passing a false citation, and condemning a true one because one source lacks it.

---

## 7. Lenses

**Principle.** Evidence is append-only. Every perspective is a query plus that perspective's own overlays (rows and edges that name their author and lens). A "version for a reader" is never a copy.

**Mechanisms.** Computed views in the database; lens-keyed edges (a critique carries its lens; a label carries its labeler); access scopes per viewer in a product.

**Kinds of lens.**
- *Framework lens:* files every item under the domain's authoritative framework in its own words (first instance: the factors the trial court said it weighed, transcribed from the order's page images). Produces a factor matrix: for and against, usable and unusable.
- *Adversary lens:* everything against the goal, strongest first.
- *Decision-maker lens:* only usable material, no agent-grade notes.
- *Client or lay lens:* plain-language cards and what blocks each conclusion.
- *Discipline lens* (research): statistician, domain expert, translator between vocabularies, skeptic.

**Rule.** Lens filings derived by crosswalk are agent-grade filings, not proof; counts are items, not weight.

---

## 8. The improvement loop

Maps autoresearch onto knowledge work:

| autoresearch | Scholar |
|---|---|
| `program.md`, human-edited | Goal file: theory, remedy or target, do-not-argue list |
| `train.py`, agent-edited | One unit of output (one argument, one section) |
| `prepare.py` evaluation, read-only | Mechanical gates (any failure = crash) plus a fixed judged panel whose prompts the writer never sees |
| Metric | Panel score, **only among variants that pass every mechanical gate** |
| `results.tsv` keep/discard/crash | `tried_on` edges with outcome effective / ineffective / backfires / rhetoric_only |
| Simplicity criterion | Fewer heads, shorter text, no new unverified authority; ties go to shorter |
| Never stop | Bounded variants per unit, then a human reads the kept version |

**Cautions.** Judged metrics can be gamed by fluency (Goodhart); mechanical gates are law, judged scores are advice. Where a real outcome exists later (a time-split in research, a decision in litigation), use it to score the method itself.

---

## 9. Lineage (established names for each piece)

| Piece | Established name |
|---|---|
| Reading a huge corpus by querying it with code | Recursive Language Models (arXiv 2512.24601) |
| Agents label, quotes checked | LLM-assisted annotation with verification (e.g. CHI 2024); weak supervision |
| Beliefs with justifications, revision, retracted beliefs kept | Truth maintenance systems (Doyle 1979; de Kleer ATMS) |
| Ranked evidence | Admiralty Code; GRADE |
| Provenance on every node | W3C PROV; named graphs |
| Datastore refuses unsupported promotion | Integrity constraints |
| Claims, grounds, undercutting evidence, attacks | Argumentation frameworks (Toulmin; Dung; Pollock's undercutting defeaters) |
| Immutable evidence with projections | Event sourcing; database views |
| Attack before commit | Red teaming; premortem (Klein) |
| Keep only what a fixed evaluator says is better | autoresearch; hill-climbing on a fixed fitness function |
| Model proposes, rules dispose | Neuro-symbolic systems |
| Linking disjoint literatures | Literature-based discovery (Swanson ABC model); HypER; AGATHA |
| Stage data, check it, only then publish | Write-audit-publish (data engineering) |
| Raw, then validated, then curated layers | Medallion architecture (bronze, silver, gold) |
| Each assertion packaged with its provenance | Nanopublications (assertion, provenance, publication info) |
| Facts extracted at scale with confidence | Knowledge-base construction with knowledge fusion (e.g. Google's Knowledge Vault): confidence is a probability there; here promotion is a rule on evidence rank |
| A claim's epistemic standing shown with it | Epistemic status labels; claim verification / fact-checking pipelines |

**How it differs from RAG.** RAG retrieves a sample at question time from a store that accepts anything. This reads everything up front with proof of coverage, stores graded judgments and negatives, enforces write rules in the datastore, and runs adversarial roles. Retrieval (exact, graph, vector, lens) is one way of *using* the graph, not the method.

---

## 10. Technology

**Principle.** Split by job; one system of record; derived stores never become authoritative.

| Layer | Job | Small instance (tens of thousands of units) | Large instance (millions of units) |
|---|---|---|---|
| Evidence | Immutable text, images, hashes | Files on disk | Parquet in object storage, content-hashed; DuckDB for analysis |
| Gated graph (record) | Labels, assertions, edges, gates | SurrealDB (ASSERT with subqueries enforces gates on raw access; proven) | SurrealDB, or Postgres with graph and vector extensions for mature row-level security and compliance tooling |
| Search | Vector and keyword | SurrealDB HNSW vector index and BM25 full-text, hybrid rank fusion | Same, or HelixDB as a serving index (Rust graph-vector-BM25 database; compiled queries; single writer; confirm constraint support before using it as record) |
| Lenses | Views, overlays, scopes | Computed views plus lens-keyed edges | Same plus per-viewer permissions |
| Queue and cache | Fan-out, hot lookups | Not needed | Redis streams or NATS; Redis cache (never the record) |
| Models | Reading, judging, embedding | Frontier model for experts; local GPU for embeddings and triage | Tiered routing; local batch embedding |

---

## 11. Optimization levers (ranked)

1. **Measure label quality first:** independent double-labeling on samples, expert adjudication, agreement rate reported in health. Unmeasured accuracy is the largest unknown.
2. **Tiered model routing:** a cheap or local model labels obvious tier-0 units; experts read only what may matter; random audit of cheap labels.
3. **Skip what cannot be used:** units whose status makes them unusable get a status-only label, not an expert read.
4. **Incremental runs:** content hashes; only new or changed units are relabeled.
5. **Cached shared instructions; batch APIs** for overnight runs.
6. **Local embeddings** for candidate retrieval, duplicate detection and "find passages like this" without sending text off the machine.
7. **Indexes** on every field the hooks and lenses filter by.
8. **Cost accounting per stage:** tokens and minutes per unit, reported with results.

First-instance baseline: Stage 1 read 9,101 units with 77 agents, about 7.4 million subagent tokens, about 25 minutes wall clock; 77 of 77 chunks passed the validator; load had 0 failed statements.

---

## 12. Where it fits

Needs a corpus too large to read by hand, a goal, and a high cost of confident error.

| Domain | Goal | Unit | Top grade | Example gate | Lenses |
|---|---|---|---|---|---|
| Appeals | Reversible error | Transcript line, exhibit page | Page image | No ready argument without preservation, usable support, surviving attack | Panel, opposing counsel, child's attorney, client |
| Trial prep, e-discovery | Chronology, hot documents | Email, deposition line | Produced document with Bates number | No key fact without a Bates cite | Each party, judge, jury |
| Patent invalidity, prior art | Kill or defend a claim | Claim element, prior-art paragraph | Dated publication | Anticipation needs every element matched verbatim, pre-priority | Examiner, infringer, patentee |
| M&A due diligence | Deal-breakers | Clause, financial line | Signed data-room document | No "clean" without coverage proof | Buyer, lender, regulator |
| Systematic review | Does X work? | Trial outcome | Randomized trial full text | No pooled conclusion from abstracts | Clinician, statistician, patient, regulator |
| Drug safety | Signals | Case report | Confirmed case with dates | No signal from one report | Safety officer, maker, regulator |
| Investigative journalism | What happened | Document, interview line | Authenticated document plus second source | Nothing publishable on one source | Editor, lawyer, subject |
| Forensic accounting | Trace the money | Transaction | Bank record | No diversion finding without a full trail | Auditor, prosecutor, defense |
| Threat intelligence | Attribution | Indicator, log line | Captured artifact | No attribution on one indicator | Defender, analyst, legal |
| Incident postmortem | Root cause | Log line, change | Timestamped log | No root cause without reproduction | Service, security, management |
| Reverse engineering | How it works | Address, disassembly line | Disassembly or reproduction | Confirmed only with code or reproduction | Emulator author, designer |
| Codebase migration | What breaks | Function, call site, test | Passing test | No safe removal without coverage | Owner, security, performance |
| **Self-updating project experts** | Correct, cited answers about how a codebase works, kept current | Symbol, file, test, commit, doc paragraph | Reproduction (test or trace) > code at pinned commit | Confirmed needs a live pinned span; facts auto-demote to stale when their span's hash changes | Architect, newcomer, security, performance, on-call |
| Legislative history | Intent | Bill text, hearing line | Official record | No intent from commentary | Proponent, opponent, court |
| Customer research | Why users leave | Interview quote, ticket | Verbatim with user id | No "users want" from one quote | Product, support, sales |
| Reproducibility audit | Do results hold? | Figure, table, code cell | Re-run output | "Replicates" only after a run | Reviewer, authors, statistician |
| Personal medical history | Understand a condition | Lab value, visit note | Lab report | No trend without dated values | Patient, specialist, insurer |
| Fiction and lore | Consistent world | Passage | The text | Canon only with a passage cite | Author, reader, game designer |
| **Cross-field research discovery** | Where field X solves field Y | Problem, capability, assumption, limitation cards | Reproduction > full text > abstract | Bridge is a hypothesis until both sides quoted, assumptions checked, novelty checked, test run | Statistician, domain expert, translator, skeptic |

---

## 13. Design: cross-field discovery (literature-based discovery, gated)

- **Units:** cards, not papers. *Problem* ("could not do X because Y"), *capability* ("achieves X under assumptions A"), *assumption*, *limitation*, each with verbatim sentence and location.
- **Ladder:** reproduction > full text > abstract > citation context > agent summary.
- **Bridge gate:** hypothesis until (1) both cards quoted verbatim, (2) assumptions matched against the target setting, (3) novelty check (the papers, and papers citing both, have not made the link), (4) a concrete test is written. "Supported" only after the test runs.
- **Negatives:** bridges that fail on an assumption are recorded with the mismatch.
- **Lenses:** statistician, target-field expert, vocabulary translator, skeptic, each with its own verdict.
- **Evaluator:** time split. Build with papers before a cutoff; score bridges by whether later papers made them. This is the uneditable metric that lets the method be tuned honestly.
- **Scale:** embeddings and a small model match cards cheaply; expert agents read only top candidates; gates and the time split control false positives.
- **Terms:** respect the corpus's bulk-access and per-document licenses.

---

## 14. Borrowed material and licensing policy

- Own projects (Forgily Book Scholar, SUPERGUN re_kb): code and methods reused.
- MIT and Apache-2.0 skills: adapted with NOTICE and LICENSE files stating changes.
- AGPL and unlicensed skills: read for ideas; nothing copied.
- Curated lists under non-commercial or no-derivatives terms: not copied; per-item licenses govern.
- Ideas (not text) from any source may inform design; attribution is still recorded in the architecture document.

## 15. Self-improvement, inside a fence

**Should the system improve itself?** Yes, in three loops. But it must never be able to change what it is judged by.

| Loop | What improves | How | Who may promote |
|---|---|---|---|
| **Knowledge** | The graph: new facts, corrections, supersessions, dead ends | Normal operation under the gates | Datastore gates; one row at a time |
| **Lessons** | The evaluator grows. Every error caught (a misquote, a misattribution, a misread question) becomes a permanent regression test | Add the planted error and its truth to the regression suite the moment it is found | Automatic addition, human review |
| **Method** | Guides, prompts, skills, crosswalks, chunk sizes, model routing | autoresearch loop: one change at a time, run the fixed evaluator (regression suite, gate probes, label-agreement sample, time-split where one exists), keep or discard, log every attempt | Human approves any kept change before it becomes the default |

**Inside the fence (agents may change):** labeling guides, prompts, skill wording, crosswalks, batch and chunk sizes, model routing, retrieval settings, candidate-generation heuristics.

**Outside the fence (humans only):**
- the gates and their probe tests
- the evidence ladders and status definitions
- the regression suite's existing entries (entries may be added, never removed or loosened)
- privacy rules
- the evaluator itself

This is autoresearch's `prepare.py` rule applied to knowledge work.

**Failure modes this fence exists for:**

| Failure mode | What it looks like | The guard |
|---|---|---|
| Goodhart | Optimizing a judged score by fluency | Mechanical gates are hard constraints |
| Silent loosening | A gate relaxed "to make the run pass" | Probes must show both rejection and acceptance, and gate files are human-owned |
| Drift | Many small kept changes that each pass but together degrade | Periodic re-run of a frozen benchmark from the start of the project |
| Leakage | Case-specific material migrating into generic guides or the engine | Scripted scan for matter identifiers in engine files |
| Training on client data | Fine-tuning on a client's corpus | Not done; improvement is to method and prompts, never to model weights on client material |

## 16. Privacy boundary

- Engine and matter are separate. Matter data never enters demos, marketing or public artifacts.
- A demo instance uses a public corpus (for appeals: a public record from a published decision).
- Client material goes only to the model provider the client has approved; no other third parties.
