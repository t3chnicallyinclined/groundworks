# Architecture

**What this is.** XScholar makes AI agents reliably expert in one large body of material with a stated goal. It is domain-free: the same engine serves an incident timeline, a body of trials, a codebase, a research literature or a legal record. A domain becomes an **instance**: a config, a few adapters, and its own vocabularies. Nothing about a domain belongs in engine code.

**Where instances live.** An instance belongs to **its own project**, with its data, adapters, vocabularies and settings. This repository holds the method, the engine and domain **specs** with synthetic examples only. Measurements that come back from a private instance are recorded here as generic numbers and lessons, never as its material.

Read with: `METHOD.md` (why), `MODULES.md` (module and file contracts), `TUNING.md` (what to turn and how to know), `instances/` (19 worked domain specs).

---

## 1. The layers

```
                 INSTANCE CONFIG  the eight answers, vocabularies, gate rules,
                       │          lens charters, seats, thresholds, policies
        generates      │
   ┌───────────────────┴───────────────────────────────────────────────┐
   ▼                                                                   ▼
 L0 INTAKE      raw sources -> units with exact locators and hashes; exclusions logged
   │
   ▼
 L1 EVIDENCE    immutable units: text, locator, status (usable?), grade (how well read)
   │
   ▼
 L2 READINGS    one label per unit: tier, direction, issue, target, reason, verbatim quote
   │            VALIDATOR: coverage, vocabulary, quote found word for word
   ▼
 L3 CLAIMS      facts, conclusions, authorities, eliminated claims, attempts
   │            GATES IN THE DATASTORE: promotion requires evidence of sufficient rank
   ▼
 L4 WORKBENCH   attacks with verdicts, answers, events, output citations with verdicts
   │            GATE: ready only if supported, attacked, answered, authorities verified
   ▼
 L5 LENSES      projections: query + that lens's own overlay rows. Evidence is never edited
   │            PANELS: several lenses judge one target under a checked protocol
   ▼
 L6 INSTRUMENTS hooks (speak at the moment of error) · MCP front door (agents act through
                typed tools) · reading layer (render for human verification) · evaluators
```

**Direction of trust.** Every layer may propose. Only L3 and L4 gates may establish. L5 and L6 never promote anything: a lens verdict, a panel result and a rendered marker are all agent-grade opinion about the graph, not evidence in it.

---

## 2. The four invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | **The datastore decides.** "Confirmed", "verified" and "ready" are written only when the required evidence is linked at that moment, on raw access too | Datastore constraints, probed in both directions |
| 2 | **Coverage is provable.** Every unit is labelled exactly once, and every important label carries a quote a program finds word for word in its unit | Validator, run until it passes |
| 3 | **Nothing is dropped.** Eliminated claims, adverse material, withheld items and dissents stay visible; a budgeted view names what it left out | Preservation tests; budget consumers report exclusions |
| 4 | **Improvement must beat a fixed test** that agents cannot edit | Human-owned evaluator files; improvement loop |

---

## 2b. Who is at the gate, and what separates what

**Nobody is at the gate.** The gate is a rule in the datastore. Experts propose; the rule disposes. No agent, lens or person can wave a claim through, and the rule checks the evidence at the moment of writing, on raw access too.

**Where domain knowledge enters**

| Step | Domain expert needed? | What they do |
|---|---|---|
| Design the instance | **yes** | choose the unit, the locator scheme, the status axis, and the vocabulary, transcribed from an authoritative source rather than invented |
| Intake | no | code: text, locator, hash, status, grade; no interpretation |
| Read and label | **yes, embodied** | agents read with that domain's guide and vocabulary; labels are agent-grade notes |
| Promote a claim | no | the datastore rule decides |
| Lenses and panels | **yes, several** | different experts judge the same graph; their disagreement is kept |
| Calibrate | **yes, human** | an expert reviews a sample, which is what moves a lens beyond persona |

**One set of gate rules per instance, differing by claim type, not by expert.** A fact needs a source-grade citation; an authority needs official text; a conclusion needs support and a surviving attack. A lens may hold itself to a **stricter** standard for its own view, never a looser one. If two experts want different standards for the same claim type, that is one unresolved decision for the instance owner, not two gates.

**Separate by instance, never by expert.** Splitting experts into their own stores would destroy the reason the graph exists: contradictions must collide where they can be seen.

| What must stay apart | Mechanism |
|---|---|
| Different goals, corpora, clients | **separate instance = separate namespace/database** |
| Different perspectives | **lenses**: views plus lens-keyed overlay rows |
| Different quality of knowing | **grades**: source-grade vs agent report |
| Who said it | **provenance** on every row: agent, lens, reasoning type |
| Who may read or write | **credentials and permissions** |
| Different vocabularies | **the validator**: labels must use the instance's approved vocabulary |
| Search indexes and mirrors | a **derived** store that is never authoritative |

**Rule of thumb: one instance, one graph.** One goal, one corpus, one set of rules.

**When two domains genuinely meet,** keep two instances and add a **bridge** with its own gate: both sides quoted, assumptions matched, novelty checked, a test run. Never merge the graphs.

**The mixing risk is real, and namespaces are not its guard.** It is held off by the approved vocabulary, the status axis, the grades, and eliminated claims kept visible so they cannot creep back in.

## 3. What an instance supplies, and what is generated

| You supply | Example |
|---|---|
| **The eight answers** (goal, unit, fidelity ladder, status axis, vocabulary, gates, roles, evaluator) | `instances/_template/instance.yaml` |
| **Intake adapter** | parser that turns your sources into units with locators a program can resolve |
| **Vocabularies** transcribed from an authoritative source | the framework a decision-maker says they apply; a reporting checklist; a module map |
| **Gate rules** | "a fact may be confirmed only with a citation to a source-grade, usable unit" |
| **Lens charters and seats** | who attacks, who judges, who audits, and what each habitually asks |
| **Evaluator entries** | the errors this project has actually made, as regression tests |

| Generated or engine-owned | Notes |
|---|---|
| Schema, datastore gates, and a reject and accept probe per rule | from the gate rules (roadmap C1/C2) |
| Labelling guide and allowed vocabulary | from the config |
| Role skills, panel files, lens views | from charters and seats |
| Rendering rules for the reading layer | should be generated from the same gate rules, so the two cannot drift (open decision 2) |

---

## 4. Control flow

```
read  ──►  label  ──►  validate  ──►  load  ──►  gate  ──►  project  ──►  judge  ──►  present  ──►  improve
 code      agents      program       code      datastore    views       panels      renderer     fixed test
```

- **Deterministic:** intake, chunking, validation, loading, gates, projections, rendering, scoring.
- **Agents:** reading and labelling, proposing claims, attacking, answering, drafting.
- **Human:** goal, gates, ladders, thresholds, privacy rules, evaluator entries, and every split a panel returns.

Agents act through the **MCP front door**: typed tools over the write surface, provenance stamped from the launch identity, no raw query, delete or schema tools. Hooks push what is already known into the moment a mistake would be made; the front door is pull.

---

## 5. Each layer's fixed test

| Layer | Its uneditable test | Measured once, on one private instance (2026-09-15) |
|---|---|---|
| L1 intake | locators resolve back to exact text; duplicates and work product excluded and logged | 9,101 units |
| L2 readings | coverage and verbatim validator; double-labelled agreement sample | 77 of 77 chunks passed; agreement not yet measured |
| L3/L4 gates | probe suite: each rule rejects what it should and accepts what it should | all probes behaved, before and after a database upgrade |
| L5 lenses | a lens view returns nothing its own standard excludes | built for one framework lens |
| L5 panels | protocol checks (stance, flaw quote, dissent, novelty, agreement) plus enforcement counts | 6 panels; 3 consensus, 3 splits; 0 of 18 stance changes |
| L6 retrieval | bake-off against keyword search on the instance's own queries | keyword search won; no model adopted |
| L6 reading layer | preservation 100%, and planted-error catch rate against the plain baseline | catch 4/15 plain vs 3/15 rendered; relabels 0 of 12 by eye |
| Whole | regression suite built from this project's real past errors | 16 pass, 0 fail |

**Design rule this produced:** a layer without its own fixed test is a layer that will drift. Add the test before the feature.

---

## 6. Lessons that shaped the design (measured, generic)

1. **Presentation is not a safeguard.** Reordering and emphasis moved items needing checks about three times earlier and changed catch rate not at all. Views help people find what they already know to look for.
2. **Some errors are invisible to reading.** A claim relabelled "confirmed" is not contradicted by any text. No layout catches it; only a re-check of the labels against the rules does.
3. **A rule may only assert what the corpus records.** Rules demanding links the instance never captured flagged a third of every document, burying the one real flag. Narrow rules to what the data holds, then fix the data.
4. **Machine flags need to say what changed.** A marker that says "fails a rule" reads as housekeeping; readers skip it.
5. **Measure retrieval per instance.** Keyword search beat two strong embedding models on a corpus whose claims quote their sources.
6. **Hesitant majorities should escalate.** A panel that cannot reach its bar hands the split to the human rather than manufacturing consensus.
7. **Bulk churn is dangerous near triggers.** Loaders that delete and recreate edges demote everything downstream; write edges by stable identity instead.

---

## 7. Mapping a new domain

| Layer | Appeal | Incident review | Systematic review | Codebase |
|---|---|---|---|---|
| Unit | transcript line, exhibit page | log line, change, alert | trial outcome, table row | symbol, test, commit |
| Top grade | page image | timestamped log | full text | reproduction |
| Status axis | in the record or not | in the incident window | included by protocol | in the shipped build |
| Vocabulary | the test the decision states | service and failure taxonomy | the review protocol | architecture doc and module map |
| Gate | no confirmed fact without a record-grade citation | no root cause without a reproduction | no pooled result from abstracts | no "safe to remove" without coverage |
| Lenses | opposing counsel, judge, client | service owner, security, management | clinician, statistician, patient | architect, security, on-call |
| Fixed test | past misquotes and bad citations | past postmortems' misattributions | protocol deviations | past regressions |

Same engine. Only the answers change.

---

## 8. Open design decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | One config or several? Gates, panel settings and rendering rules live in separate files today | **One `instance.yaml`**; generate the rest |
| 2 | The datastore gates and the rendering rules state the same requirements twice and drifted apart within hours | **Generate the rendering rules from the gate rules** |
| 3 | The write surface, validator and loader still live inside one private instance's own repository | Move the generic parts into the engine; that instance keeps only its adapter and its data (C1, C2) |
| 4 | Identity: one launch identity per session today | Per-agent credentials so the datastore enforces which lens or seat may read and write (C12) |
| 5 | Time: no separate record of when a claim was true versus when it was learned | Two dates on every claim (B12) |
| 6 | Presentation and re-check are one module today | Keep the re-check separate: it is a check, not a view |
