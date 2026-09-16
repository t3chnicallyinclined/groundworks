# 2026-09-15: review of an outside proposal for expert lenses as query contexts and discovery instruments

Source: a ChatGPT session the user pasted (not a primary source; its two cited references were not linked or read here). Generic; no matter data.

## The proposal, in short
1. Separate knowledge from perspective: evidence, verified claims, canonical knowledge, mechanisms, then expert lenses (physicist, biologist, ML engineer) on top.
2. Ask questions through a lens; ask where lenses disagree; ask which lens sees something the others do not.
3. A lens carries its own ontology, vocabulary, causal primitives, reasoning methods, evidence standards, known mechanisms and habitual questions, backed by a domain Scholar that has ingested that field (not "pretend you are a physicist").
4. Lenses as discovery instruments: project two unrelated domains into a shared abstraction (for example control theory: feedback, saturation, instability, damping) and detect structural similarity invisible in native vocabulary. Framed as an extension of Swanson ABC literature-based discovery.
5. A hierarchy of domain Scholars (physics, biology, ..., narrowing to specialties) recruited dynamically per question; each proposes transfers, the others attack.
6. Provenance on derived reasoning: hypothesis generated_by a lens, derived_from mechanisms, reasoning_type, supporting and contradicting evidence, per-lens review verdicts. W3C PROV as groundwork.
7. New backbone: Evidence, Consolidation, Abstraction, Reasoning, Expert Lenses, RLM Query, Discovery, Cross-Domain Transfer, Hypothesis/Falsification, Self-Improvement.

## Already in the method (not new to us)
- Lenses as projections over immutable evidence, disagreement preserved (METHOD s.7, DECISIONS 2026-09-15).
- Discipline lenses including a vocabulary translator and skeptic; problem/capability/assumption/limitation cards; bridge gate; time-split evaluator (METHOD s.13, instances/cross-field-discovery).
- Adversarial review before promotion; outcomes on edges; W3C PROV in the lineage (METHOD s.2, s.9).

## Adopt (with the guard each needs)

| # | Idea | Where it goes | Guard |
|---|---|---|---|
| A1 | Lens as a query interface: "answer through lens L", "where do lenses disagree", "what does only lens L see" | lens builder: disagreement view over lens-keyed critiques and filings | Disagreement is computed from stored verdicts, not generated fresh at question time |
| A2 | Richer lens definition in `instance.yaml`: vocabulary, evidence standard, accepted reasoning types, habitual questions, framework source | `lenses:` contract in the template | **Backing grade** on every lens: `graph` (backed by an instance that ingested the field) vs `prompt` (persona only). Output of a prompt-backed lens is agent grade and can never support promotion |
| A3 | `reasoning_type` on every derived row (direct quote, inference, structural analogy, statistical, expert judgment) plus `generated_by` lens and activity | write surface; provenance fields | **Status ceiling by reasoning type:** structural analogy can create a hypothesis, never a confirmed fact or a supported bridge. Enforced as a datastore gate with probes both ways |
| A4 | Abstraction projection for discovery: map both cards into a shared mechanism vocabulary | discovery module: `mechanism` cards and `maps_to` edges | **Element-by-element mapping with a verbatim quote per element on both sides** (like claim-element matching in patent anticipation). A mapping with any unquoted element is rejected |
| A5 | Per-lens review verdicts on a hypothesis (support / weak / challenge) | attacks edge with lens key (exists); add a summary view | Verdicts are advice; mechanical gates still decide |

## Adopt only with a new control
- **Null baseline for projections (new, required before A4 is trusted).** Abstract vocabularies make everything look alike: nearly any system can be described as "feedback with saturation." Measure the lens's false-match rate by projecting shuffled or random card pairs; report matches against that baseline. A lens that matches random pairs at a similar rate is not a discovery instrument. Fits the time-split evaluator in METHOD s.13.

## Do not adopt as written
| Idea | Problem |
|---|---|
| A "canonical knowledge" layer that yields a consolidated consensus answer | Consensus among agents is not evidence, and it flattens the disagreement the method keeps as data. Answer shape stays: confirmed (with evidence), contested (with each side), eliminated |
| Lenses only at the top of the stack | Lenses differ in evidence standards, so they must be able to project at every layer, including the evidence layer (a statistician lens rejects an abstract-grade effect size) |
| "The physics Scholar has ingested physics" | Not buildable as stated; a lens is graph-backed only for the narrow corpus actually ingested and validated. Otherwise it is prompt-backed (A2) |
| Replacing the roadmap backbone now | Consolidation and abstraction are optional modules on the existing layers, not a restructure. The first instance has priority |

## Deferred
- Federated hierarchy of domain Scholars with dynamic recruitment: needs C2 (engine from config) and at least two graph-backed instances first. Open question: how a router picks lenses without its choice becoming an unevaluated judgment.

## Unverified pointers from the proposal
- "Context-based ABC literature discovery" paper: not identified or read. Add to READING_LIST only once located.
- W3C PROV primer: already in lineage; entity/activity/agent/derivation model to be read in the primary text before designing fields around it.
