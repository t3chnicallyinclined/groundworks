# Tuning guide

Every layer has knobs. This is what each one does, where to start, how to tell whether a change helped, and how it fails. **Rule for all of them: change one knob, run that layer's fixed test, keep or discard, and log the attempt.** Numbers marked *measured* come from real runs; treat them as starting points, not laws.

---

## L0/L1 Intake and evidence

| Knob | Default | Raise / widen when | Lower / narrow when | How you know |
|---|---|---|---|---|
| Unit size | the smallest thing a conclusion can cite | citations land inside a larger block | units are so small that a claim needs ten of them | every locator resolves back to exact text |
| Status axis | binary usable / not | a middle class exists (restricted, embargoed, marked but not admitted) | nobody ever uses the middle class | a true statement from an unusable source is still caught |
| Fidelity ladder | original > faithful extract > secondary > agent report > recollection | a new source type appears | two rungs never differ in practice | only the top rungs may confirm |

**Failure mode:** locators that a program cannot resolve. Everything downstream inherits the ambiguity.
**Example:** an appeal distinguishes "received in evidence" from "marked for identification"; an incident review distinguishes logs inside the incident window from logs outside it.

---

## L2 Reading and labelling

| Knob | Default | Notes |
|---|---|---|
| Chunk size | one agent's comfortable slice (*measured: about 220 turns, 900 short messages, or 45 pages*) | too large and quality falls; too small and coordination costs dominate |
| Tiers | 0 noise, 1 context, 2 relevant, 3 load-bearing | tier 2 and above require a reason and a verbatim quote |
| Quote requirement | mandatory above tier 1 | this is the anti-hallucination mechanism; do not relax it |
| Model routing | one strong model for everything | cheap triage for tier 0 only, with a random audit of its labels |
| Re-read policy | relabel only changed units, by content hash | |

**Fixed test:** coverage (every unit once), vocabulary, quote found word for word; plus an agreement sample: two independent labellers on the same chunks, disagreements adjudicated.
**Failure mode:** trusting labels whose accuracy was never measured. *Measured: 77 of 77 chunks passed validation, while label accuracy stayed unmeasured; validation proves shape, not truth.*

---

## L3/L4 Gates

| Knob | Default | Notes |
|---|---|---|
| What each status requires | confirmed needs a top-grade, usable citation | the heart of the system; human-owned |
| Readiness | supported, attacked, no fatal attack standing, every damaging attack answered, authorities verified | add a rung only if you can test it |
| Negative claims | never gated | recording an elimination must be cheaper than recording a fact |
| Demotion | one-way: losing evidence demotes; restoring it does not re-promote | a human re-confirms |

**Fixed test:** two probes per rule (a write that must be refused, and the same write accepted once the requirement exists). **A rule with only one probe is not tested.**
**Failure modes:** a gate that only ever rejects (nothing can be built) or only ever accepts (it is decoration). Bulk loaders that delete and recreate edges will demote everything downstream: *measured, 4 of 4 confirmed rows demoted by a delete-and-recreate load; writing edges by stable identity fixed it.*

---

## L5 Lenses

| Knob | Default | Notes |
|---|---|---|
| Backing | prompt-backed | a lens is only as good as its Frame: a persona with no source is agent-grade and may never support promotion |
| Standard | same as the base gates | may be stricter, never looser |
| Overlays | lens-keyed rows | disagreement between lenses is data, not a conflict to resolve |
| Calibration | none | measure agreement with a human expert on a sample before trusting a lens |

**Failure mode:** a lens that edits evidence instead of projecting it. If a reader needs their own version, that is an overlay, never a copy.

---

## L5 Panels

| Knob | Default | Raise when | Lower when |
|---|---|---|---|
| Seats | 3 | one lens owns the question and needs a counterweight | cost matters more than breadth |
| Consensus bar | exactly 2/3 of base weight | you want fewer, firmer decisions | splits are so frequent the panel decides nothing |
| Confidence weights | high 1.0, med 0.75, low 0.5 | a hesitant majority should be able to carry (*with 3 equal seats, two medium seats can never reach any bar above half unless the medium weight rises*) | |
| Agreement check | fires above 70% agreement | | |
| Dissent quota | 2 distinct objections | | seats share a model and categories are fixed, which makes the quota toothless |
| Re-prompt budget | 2 per seat per condition | | |

**Fixed test:** the protocol checks themselves, reported as enforcement counts per run.
**Measured:** 6 panels, 3 consensus and 3 splits; the agreement check fired 6 times and produced one genuinely new finding; **0 of 18 stances changed between rounds**; hiding seat identities changed nothing at n=1.
**Failure modes:** seats that share a model converge and the category-level dissent quota never fires; a rounded threshold (0.6667 instead of exact 2/3) silently makes some majorities unreachable.

---

## L6 Retrieval

| Knob | Default | Notes |
|---|---|---|
| Method | keyword search | **measure before adopting anything else** |
| Embedding model | none until a bake-off says otherwise | permissive licences only; run locally when the corpus is confidential |
| Hybrid fusion | reciprocal rank, k=60 | |
| Query set | the instance's own answered questions, with their sources as the right answers | |

**Fixed test:** recall and ranking with confidence intervals, against a keyword baseline and a random control. Adopt only if the interval clears the baseline.
**Measured:** on a 7,995-unit corpus with 92 real queries, keyword search scored 0.470 and the best hybrid 0.376; neither embedding model cleared the baseline. **Caveat that generalises: if your claims quote their sources, keyword search is strong and vectors must prove themselves on reworded queries.**

---

## L6 Reading layer

| Knob | Default | Notes |
|---|---|---|
| View | plain | rendered views are opt-in until measured on your corpus |
| Check-first cap | 25 | overflow is listed, never hidden |
| Scan lead | 160 characters or the first sentence | full text always in the details section |
| Spot-check sample | 5 | *measured: 5 of 110 caught 0 of 9 hidden errors; sampling is not a safeguard* |
| Re-check rules | none until you can state one honestly | see below |

**Fixed test:** preservation must be 100% in every view, and planted-error catch rate must be measured against the plain baseline with a fixed lookup budget.
**Measured, three arms over the same planted documents (8 lookups per reviewer):** plain 4/15 caught with 3 false alarms; rendered without rules 3/15 with 2; rendered with tuned rules **5/15 with 0 false alarms**. Rendering moved check-first items about three times earlier and changed catch rate little. **Relabelled claims were never reported: 0 of 9 across all three arms, even when the re-check flagged them first in the document.** The one relabel that was caught came from a reader reconciling a stated count against the rows shown.

**Therefore, two knobs matter more than layout:**
- **Flag wording.** Say what the label claims, what the evidence supports, and the single action that resolves it. "Fails its own rule" reads as housekeeping and gets skipped.
- **Reconcilable counts.** Give every section a count, and print the totals the document itself asserts, so a reader can add them up. Two reviewers independently found errors this way.
**Failure modes:** a view longer than the baseline (nothing is dropped, so markup is pure cost); a rule the corpus cannot satisfy flagging a third of every document (*measured: 31 to 38%*), which buries the one real flag; a marker that says only "fails a rule" without saying what the label claims and what the evidence supports.

---

## L6 Hooks

| Knob | Default | Notes |
|---|---|---|
| Triggers | locator patterns and names your agents write | |
| Budget | a small number of lines and characters | eliminated claims and known errors fill it first |
| Latency | keep it small (*measured: about 300 ms, roughly a third interpreter start-up*) | index the fields the hook filters before optimising anything else |
| Silence | must never look like "nothing known" | a stopped database prints a warning |

---

## The improvement loop

| Knob | Default | Notes |
|---|---|---|
| What may change | guides, prompts, skill wording, crosswalks, chunking, routing, retrieval settings | |
| What may not | gates, ladders, evaluator entries, privacy rules, thresholds | human-owned |
| Variants per unit | bounded, then a human reads the kept version | |
| Tie-break | simpler wins | fewer claims, shorter text, no new unverified sources |

**Fixed test:** mechanical gates are law; a judged panel is advice; a real outcome, where one exists, scores the method itself.
**Failure mode:** optimising a judged score by fluency. Keep the mechanical gates as hard constraints, and add every error you find to the regression suite the moment you find it.
