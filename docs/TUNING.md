# Tuning guide

Every layer has knobs. This is what each one does, where to start, how to tell whether a change helped, and how it fails. **Rule for all of them: change one knob, run that layer's fixed test, keep or discard, and log the attempt.** Numbers marked *measured* come from real runs; treat them as starting points, not laws.

**Two rules for every agent lane, whatever the layer.** Both come from a real instance in beta.

1. **A lane saves its result before it returns.** It writes each row as it finds it, not at the end of the lane and not by a later stage. *Measured: three workflows lost whole stages to a usage limit on one day. Work survived only where agents had written rows as they went. A result held in an agent's reply is lost when the agent dies, and the stages after it never run.*
2. **A stage whose job is to hold something back cannot sit after the point where the thing is written.** If a verifier is a later stage and it dies, nothing holds the promotion back. *Measured: a research lane wrote `verified` on authorities it had read once, meaning an adversarial verifier to run later. The verifier died on a usage limit, and 50 authorities went verified on a single reading.* The fix is structural, not a stronger prompt. The promotion belongs to a gate the datastore enforces, and the proposer must not be the promoter.
3. **One rule, one implementation. A second copy of a rule drifts, and a correction to one is invisible to the other.** When an adapter re-derives a status that another module already computes, correcting the original leaves the copy wrong, and nothing notices. *Measured: a real instance corrected an exhibit's status in its corpus builder. The rebuild adapter had re-derived the same status from its own list of evidence sets, and would have loaded all 2,974 corrected rows as not received.* Two further lessons from the same fix:
   - **Share the data, not necessarily the policy.** The adapter could not simply call the other function: it deliberately treated one date range more strictly, and calling it would have silently loosened that. Read every membership set from the one source; keep a deliberate difference in policy explicit.
   - **Reconcile, and refuse to load on disagreement.** Compare every row against the other implementation, allow only documented differences, and stop first load on anything else. *Measured: the check found the derived corpus file had never been regenerated after the code was corrected, and found 19 rows where the two rules still genuinely disagreed, which nobody knew about.*

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

**Fixed test:** two probes per rule (a write that must be refused, and the same write accepted once the requirement exists), *plus* a third for any rule whose `requires` clause carries a `where`: the same write with the wrong kind of evidence. **A rule with only one probe is not tested.**

**Write a baseline for your instance and commit it:**

```
python -m xscholar.gates instances/<you>/instance.yaml --apply --url http://127.0.0.1:8040/sql --write-baseline instances/<you>/gate_baseline.json
```

Then every later run takes `--baseline instances/<you>/gate_baseline.json`, and `xscholar test` checks the config half with no server. Without it, a probe report can say `0 misbehaved` while the gate it is testing has been loosened -- the report only says each gate matches *its own current spec*. Re-write the baseline when you change a gate on purpose, and review the diff; that review is the whole point of the file.
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

**Two knobs were expected to matter more than layout. Both were then tried, and neither carried** (*measured 2026-09-16, `notes/2026-09-16_marker_wording.md`: 12 reviewer sessions, 4 per arm, 8 lookups each, over the same two seeded copies*):
- **Flag wording.** Saying what the label claims, what the evidence supports, and the single action that resolves it moved status-upgrade catches from **0 of 8 to 2 of 8** — two reviewers out of eight, one of whom simply transcribed the whole flag section and supplied the arm's **only 2 false alarms**. Noise. Keep the wording honest, but do not expect it to buy catches.
- **Reconcilable counts.** Printing counts a reader can add up made things **worse**: that arm caught **0 of 8 verdict softenings against the control's 4 of 8**. Counts that are already reconciled for the reader are not the affordance that worked earlier; totals the reader must reconcile himself are. Unsolved.
- **What was measured instead:** 7 of the 8 reviewers in the reworded arms said they left the flagged rows out *because the document already discloses them* — one after looking the row up and seeing the true label. A flag a document raises about itself does not read as an error the reader found. The next thing to tune is the reviewer's task, not the marker's prose.
**Failure modes:** a view longer than the baseline (nothing is dropped, so markup is pure cost); a rule the corpus cannot satisfy flagging a third of every document (*measured: 31 to 38%*), which buries the one real flag; a marker whose wording is tuned in place of measuring why it is skipped (*measured: rewording it moved 0 of 8 to 2 of 8, inside noise, and bought false alarms*).

---

## L6 Hooks

| Knob | Default | Notes |
|---|---|---|
| Triggers | locator patterns and names your agents write | |
| Budget | a small number of lines and characters | eliminated claims and known errors fill it first |
| Latency | keep it small (*measured: about 300 ms, roughly a third interpreter start-up*) | index the fields the hook filters before optimising anything else |
| Silence | must never look like "nothing known" | a stopped database prints a warning |

**Two silent failures measured on a second instance (2026-09-15), both of which made a hook answer nothing while looking healthy:**
1. **A string function over a column that is NONE on some rows errors**, and the error block is then read as "no rows". The hook answered nothing for months. Guard: never wrap a nullable column in a function; filter `IS NOT NONE` first, or match an indexed column exactly.
2. **Wrapping a column in a function prevents index use.** p95 reached 2,156 ms, past the hook's own timeout, and a timed-out hook returns silence. Fix: exact-spelling matching against an indexed column, or a FULLTEXT index with a lowercase analyzer, rather than `string::lowercase(col) CONTAINS ...`.

Both failures look identical from the outside: the agent simply is not told anything. **Test a hook by planting a fact it must surface, not by checking that it runs.**

**Sparse columns, not just nullable ones.** In the measured case the column's declared type was not the problem: 1,603 of 2,259 rows simply had no value, and one such row made the whole statement return an error, which the caller read as "no rows". Any sparsely populated column does this.

**What the fix was worth** (same instance, 120 real tool calls, the same sample both runs): p95 fell from **2,156 ms to 277 ms**, and the hook answered on **55% of locator-bearing calls against 23% before**. Generated spellings matched against plain indexes on the filtered columns.

**Measuring a hook A/B: snapshot the sample.** An A/B that re-harvests its sample from live transcripts compares different calls between runs, because the transcripts grow while you work. Snapshotting the sample to a file was the difference between a comparison that meant something and one that did not.

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
