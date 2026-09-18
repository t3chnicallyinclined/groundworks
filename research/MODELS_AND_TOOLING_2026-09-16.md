# Models and AI tooling: what would improve XScholar, 2026-09-16

Desk research by the scholar-architect, in research mode. **Nothing here was run in our pipeline**, so nothing is *adopted*: a method is adopted only with a primary source **and** a reproduction here. No weights were downloaded and no packages installed.

**Tags.** CONFIRMED = read in the fetched source (the corpus excerpt id is given as `[id]`, verbatim-checked against the saved original). INFERRED = my reasoning from confirmed facts. UNKNOWN = not established.
**Licence tags.** CONFIRMED = I read the licensor's own licence file or licence statement. INFERRED = only a metadata tag (for example a Hugging Face `license:` field), or a derived-data question. UNKNOWN = not found.
**Corpus.** Every source is in `research_corpus/models/` in the scratchpad, outside this repo (section 8).

---

## 1. The answer first

**The three changes most likely to help, and how sure I am:**

1. **Measure label accuracy with a small human sample and a valid estimator, before anything else.** *High confidence that this is the top lever; medium confidence in the cost estimate.* The repo already names it as lever #1 (`docs/METHOD.md:207`) and records that validation proves shape, not truth (`docs/TUNING.md:39`). The literature gives a cheap, specific protocol: compare the agent labeller with **at least three humans on 50 to 100 units** [alt-annotator-001], choose those units by the labeller's own confidence [cdi-002, cdi-005], and never treat agent labels as gold, because that gives "highly inaccurate estimates and poor coverage" [cdi-001, ppi-002]. No new dependency. Public gold sets with permissive licences (ContractNLI, CUAD, SciFact) let this start **before** a real instance is available.
2. **Make every hold-back step fail closed, and measure lane reliability over repeated runs.** *High confidence in the diagnosis, medium in the fixes.* The beta failures (a verifier dying, so 50 promotions went through) fit a known pattern: in a multi-agent failure study, missing or incomplete verification was 8.2% of failures and incorrect verification 9.1% [mast-002]. Claude Code's own documentation says a hook that exits 1, crashes, is mistyped or times out **does not block**: "a mistyped path in settings.json leaves the gate silently disabled" [claude-code-hooks-002, -001, -003]. So hooks are advice, and holds belong in the datastore, as V2-20 already did. Measure a lane with pass^k, the chance that **all** k runs succeed [taubench-001].
3. **Cut labelling cost without touching quality: batch plus a cached guide, then a cheap first reader that escalates.** *High confidence on batch and caching savings (vendor prices); low confidence on routing until measured.* The Batch API is **50% off input and output** [anthropic-pricing-004], and a cache hit costs **10% of the input price** [anthropic-pricing-005]. Haiku 4.5 costs **$1 / $5** per million input/output tokens against Opus 5's **$5 / $25** [anthropic-pricing-001, -003]. Routing needs a guard: a cheap judge first, escalating to a stronger model when unsure, can carry a provable human-agreement guarantee, and pays for it by handing about a fifth of items on [trust-or-escalate-001, -002].

**On retrieval, the answer is no change yet.** Nothing found overturns the bake-off. BM25 is "a robust baseline" on public benchmarks too [beir-001], and test sets built from lexical hits are biased toward it [beir-002], which is exactly the repo's own caveat (`groundworks/embed/README.md:95`). **Build a reworded query set first.** Then test two cheap candidates that keep BM25 as the ranker: LLM query expansion, and a reranker trained on permissively licensed data.

## 2. Ranked recommendations

Ranked by expected effect against cost.

| # | Change | Layer | Verdict | Expected effect | Cost | Confidence | The measurement that decides it |
|---|---|---|---|---|---|---|---|
| 1 | Label-accuracy sample: labeller vs 3 humans (or 1 expert, variant D.2 [alt-annotator-003]) on 50-100 units chosen by labeller confidence; estimate accuracy with an interval | L2 | **Prototype and measure** (P1) | Turns the largest unknown into a number | Human hours; stdlib code | High | Kappa **and** exact agreement per field, with intervals; the alt-test pass/fail |
| 2 | Calibrate the labeller on public gold first (ContractNLI evidence spans, CUAD clauses, SciFact rationales) | L2, L3 | **Prototype and measure** (part of P1) | A labeller error rate before any client data | API tokens only | Medium (transfer to a real corpus is INFERRED) | Span-level precision/recall against gold; kappa of tier-equivalent labels |
| 3 | Fail-closed holds: no verifier as a hook or a later stage; a `SubagentStop` hook that refuses a lane's return until its rows exist [claude-code-hooks-004]; pass^k on lanes | L6, pipeline | **Adopt the rule** (already `docs/TUNING.md:7-8`); **prototype** the stop hook | Removes the silent-bypass class | Small | High (diagnosis), medium (hook) | A planted dead verifier must block promotion; pass^3 on a fixed lane |
| 4 | Batch API plus a cached labelling guide for overnight labelling | L2 | **Prototype and measure** | About half the token cost of API-run labelling | Needs an API-driven labeller; Claude Code subagents do not go through the Batch API (INFERRED) | High on price, medium on fit | Cost per unit and validator pass rate against the current run |
| 5 | Cascade for tier 0: cheap model first, escalate when unsure, random audit | L2 | **Prototype after #1** | Up to 5x cheaper per token for the triaged share (price ratio, CONFIRMED); quality UNKNOWN | Small | Low until measured | Agreement of cheap tier-0 labels with the strong labeller on an audit sample, with an interval; the share escalated |
| 6 | Judges: seats from different model families, both answer orders, kappa not percent agreement, planted "master key" answers in every judge test | L5 panels, evaluator | **Adopt as evaluator design rules** (human-owned) | Removes known biases from judged scores | Small | Medium-high | Order-swap flip rate; kappa against a human sample; false-positive rate on planted empty answers |
| 7 | Assertion judge: output constrained to the declared field/value domain, one attribute per call, a verbatim quote checked locally; a small entailment model only as a second opinion | L3 | **Prototype after #1** | Fewer free-text contradictions missed | Small | Medium | Precision/recall on a ContractNLI-style set and on planted contradictions |
| 8 | Retrieval: a reworded query set, then BM25 + LLM query expansion, and BM25 top-100 + a permissive-data reranker | L6 | **Prototype and measure** (P2) | UNKNOWN; public results favour rerankers on average [beir-001], one legal result went against [legalbench-rag-003] | Queries: API tokens; reranker: local GPU | Low | The existing bake-off verdict rule (interval above BM25 **and** paired difference above zero), on reworded queries only |
| 9 | Late interaction (ColBERT), learned sparse (SPLADE-style) | L6 | **Watch** / SPLADE **avoid** | Possibly better recall | Heavy index and dependencies; licence problems | Low | Only after #8 shows vectors or rerankers help at all |
| 10 | Eval and tracing frameworks (Inspect, promptfoo, Langfuse, Phoenix) | tooling | **Watch**, outside the core only | Convenience | Dependencies | Medium | None needed; write JSONL traces with stdlib |

## 3. By area

### 3.1 Labelling and extraction quality (the #1 lever)

**Where it stands.** Validation passed 77 of 77 chunks while accuracy stayed unmeasured (`docs/TUNING.md:39`). The only agreement run so far was agents against a stub on the demo instance (tier kappa 0.93, direction kappa 0.88), which the roadmap records as a harness proof, not a label-accuracy measurement (`docs/ROADMAP.md:14`). `agreement.py` already reports kappa and returns None on a degenerate sample (`groundworks/pipeline/agreement.py:15`).

**What the evidence says.**
- **Agent labels are not gold.** Treating model predictions as gold-standard measurements (the "imputation approach") can give invalid conclusions "because the predictions may have biases" [ppi-002, ppi-003]. In social-science tasks, treating LLM annotations as human data led to "highly inaccurate estimates and poor coverage" [cdi-001]. CONFIRMED.
- **A small human sample with a valid estimator fixes this.** Prediction-powered inference gives "provably valid confidence intervals" from a small gold set plus many model labels [ppi-001]. Confidence-driven inference picks which units humans label using the model's confidence, keeps target coverage, and beats the baselines on effective sample size [cdi-002, cdi-005]. CONFIRMED. For XScholar's case (estimating a labeller's error rate per tier or field), the arithmetic is a mean with a correction term, so it is stdlib (INFERRED).
- **How many units.** The alternative annotator test compares the LLM with "a small group of human annotators (at least three) on a modest subset of examples (between 50 and 100)" [alt-annotator-001]. It has a single-expert variant [alt-annotator-003]. CONFIRMED.
- **High self-agreement is not accuracy.** ChatGPT's intercoder agreement exceeded that of trained annotators [gilardi-001], so two runs of the same model agreeing proves little. Two runs of the same model are not independent labellers (INFERRED). **Consequence for `agreement.py`:** the second labeller should differ in model, prompt or tier, and the report should say which.
- **Chance-corrected agreement is necessary.** Raw agreement overstated chance-corrected agreement by 33 to 41 percentage points in all 21 judge models studied [reliability-without-validity-001]. `agreement.py` already reports kappa; keep exact agreement next to it, never alone. CONFIRMED (source), INFERRED (fit).
- **Self-consistency** (sample several answers and take the majority) gave large gains on reasoning benchmarks with one right answer [selfconsistency-001]. Its value for tier labels is UNKNOWN; it multiplies cost by the sample count.
- **Confidence scores.** LLMs are "highly overconfident when verbalizing their confidence" [express-uncertainty-001], weakest on professional-law style tasks [express-uncertainty-002]. The contrary result is that verbalized confidence beat token probabilities, often by a relative 50% in calibration error, on trivia QA [verbal-calibration-001]. **Verdict:** use stated confidence only to *order* which units a human checks (as confidence-driven inference does), never as an accuracy number. INFERRED.
- **Self-correction.** "current LLMs struggle to self-correct their reasoning without external feedback" [selfcorrect-001]. This supports an independent second reader or a program check over asking a lane to re-check itself. CONFIRMED (source), INFERRED (fit).
- **Structured output.** Claude's structured outputs "guarantee schema-compliant responses through constrained decoding" [anthropic-structured-outputs-001]. That is shape, which the validator already enforces. It removes repair loops; it does not improve truth (INFERRED). The Citations API guarantees "valid pointers to the provided documents" [anthropic-citations-001], but **"Citations cannot be used together with structured outputs"** [anthropic-citations-002]. XScholar's local verbatim check already gives the pointer guarantee, so keep structured output for labels and the local quote check (INFERRED).

**What it would take to measure label agreement on a real instance.** INFERRED from the sources above:
1. A frozen random sample of 100 units stratified by tier, plus the units where the labeller's stated confidence was lowest [cdi-005].
2. A second, independent labeller (a different model family, or the same model with a different guide author), run blind.
3. `agreement.py` on the two files; a human adjudicates every relevance split and every disagreement on a tier 2 or 3 unit against the source.
4. The adjudicated labels are the gold set. Report the labeller's accuracy per field with an interval (a PPI mean estimate). Put the number in `health`.
5. If three qualified humans exist, run the alt-test; otherwise its single-expert variant.
6. Freeze the sample as part of the human-owned evaluator (METHOD s.15), so later changes to guides or routing are scored against it.

**Recommendation: prototype and measure (P1).** Decided by per-field kappa and accuracy with intervals on the adjudicated sample.

### 3.2 Retrieval

**Where it stands.** BM25 nDCG@10 0.470 [0.398, 0.549] against best hybrid 0.376 over 92 queries, 69 of which quote the source verbatim; on the 23 non-quoting queries the methods were not separable (`groundworks/embed/README.md:89-95`).

**What the evidence says.**
- BM25 is "a robust baseline", and "re-ranking and late-interaction based models on average achieve the best zeroshot performances, however, at high computational costs" [beir-001]. CONFIRMED.
- Judged sets built from lexical candidates disfavour non-lexical methods [beir-002]. XScholar's qrels come from the units that claims quote, which is the same bias (INFERRED). **A fair test needs reworded queries.**
- **Query rewriting keeps BM25.** query2doc "boosts the performance of BM25 by 3% to 15%" with no fine-tuning [query2doc-001]. HyDE has the model write a hypothetical answer document for dense retrieval [hyde-001]. The generated text is never evidence. Document-side expansion hallucinates and "harms retrieval effectiveness" unless filtered [doc2query-minus-001]. CONFIRMED.
- **Rerankers.** An LLM ranking can be distilled into a 440M model that beat a 3B supervised model on BEIR [rankgpt-001]. **Contrary, and in the legal domain:** on LegalBench-RAG "the performance of the Cohere Reranker was inferior compared to not using a reranker" [legalbench-rag-003]. CONFIRMED. A general reranker is not a safe default.
- **Late interaction.** It stores many vectors per passage, "an order-of-magnitude larger space footprint than single-vector models", which ColBERTv2 compresses by 6-10x [colbertv2-001]. It is also a heavy dependency (INFERRED).
- **Learned sparse.** SPLADE-v3 is "statistically significantly more effective than" BM25 on public sets [splade-v3-001], but the library is **CC BY-NC-SA 4.0** [lic-splade-001]: banned.
- **The training-data trap.** Many permissively tagged retrievers are trained on MS MARCO, whose terms say "non-commercial research purposes only" [msmarco-terms-001]. The weights are tagged MIT or Apache-2.0, yet: the ColBERTv2 checkpoint "has been trained on the MS MARCO Passage Ranking task" [card-colbertv2-001]; GTE-ModernColBERT was distilled on MS MARCO [card-gte-moderncolbert-001]; the OpenSearch sparse v3 training list includes it [card-opensearch-sparse-v3-distill-002]; and the cross-encoder `ms-marco-MiniLM-L6-v2` names it. IBM states its Granite R2 embedding model does **not** use MS MARCO "due to its non-commercial license" [card-granite-embedding-r2-001], and that its reranker used only permissively licensed pairs plus IBM data [card-granite-reranker-r2-001]. That is a vendor claim, not verified. **Whether a weight licence clears non-commercial training data is a LAWYER question**; add it to `docs/LICENSING.md`'s list.

**Local running.** All rerankers and encoders named here can run locally on the RTX 3090 already used for the bake-off (`groundworks/embed/README.md:97`). Sizes where stated: Granite reranker 149M [card-granite-reranker-r2-001], answerai-colbert-small 33M [card-answerai-colbert-small-002]. Query expansion with Claude sends queries, not units, to the API. Queries can still carry matter content, so on a confidential corpus that needs the client's approval of the provider (METHOD s.16; INFERRED).

**Recommendation.** **Prototype and measure (P2):** a reworded query set, then (a) BM25 plus query expansion and (b) BM25 top-100 plus a permissive-data reranker (Granite R2 first). Adopt only under the existing bake-off verdict rule. **Watch** ColBERT. **Avoid** SPLADE and jina rerankers.

### 3.3 LLM-as-judge and evaluation

**What the evidence says (all CONFIRMED in the sources):**
- **Human-level agreement is possible on some tasks.** A strong judge reached "over 80% agreement, the same level of agreement between humans" on open-ended pairwise preference [mtbench-judge-001]. The task was preference, not fact verification.
- **Position bias.** "Most LLM judges favor the first position", and only one judge of that generation was consistent in more than 60% of cases [mtbench-judge-002, -003]. Rankings "can be easily hacked by simply altering their order" [unfair-evaluators-001]; the fix is to judge both orders and aggregate [unfair-evaluators-002]. In 2026, high test-retest reliability (> 0.95) coexisted with severe position bias in two production judges [reliability-without-validity-002].
- **Length.** Older judges favoured a longer, repetitive answer [mtbench-judge-004]. A 2026 cohort of 21 judges found verbosity bias small (< 0.011) under one pairwise rubric [reliability-without-validity-003]: contrasting evidence.
- **Self-preference.** Judges favour their own outputs, and the bias correlates with self-recognition [self-preference-001, -002]. MT-Bench's authors say their own data "cannot determine" it [mtbench-judge-006].
- **Anchoring.** A judge that could solve a problem "was misled by the provided answers" [mtbench-judge-007]. Multi-attribute judgments show anchoring effects [inconsistent-evaluators-002].
- **Leniency and empty answers.** Judges show "a bias toward leniency" [judging-judges-002]. Answers like "Solution" or "Thought process:" drew false-positive rates "as high as 80%" [one-token-fool-001].
- **Inconsistency.** Two judges agreed at κ = 0.51, and equivalent prompt templates changed majority outcomes in 25% of cases [coin-flip-judge-001] (a small study: 29 questions, two judges).
- **Diversity helps.** A panel of smaller models from disjoint families beat a single large judge, with less intra-model bias, at over seven times lower cost [juries-001]. This matches the repo's own measurement that seats sharing a model converge (`docs/TUNING.md:91`).
- **Calibration against humans.** Criteria cannot be fixed before humans grade real outputs ("criteria drift") [validators-001]. Selective evaluation with escalation gives a provable agreement guarantee [trust-or-escalate-001]. Report more than percent agreement [judging-judges-001, reliability-without-validity-001].

**Rules for the evaluation harness in `docs/TESTING.md`** (INFERRED from the above; these are proposals for the human-owned evaluator):
1. Every judge is calibrated against a frozen human-labelled sample of at least 50-100 items before its scores are reported, with kappa (and Krippendorff's alpha for more than two raters) next to percent agreement.
2. Pairwise judgments run in both orders; the flip rate is reported.
3. A judge never scores output from its own model family where another family is available.
4. One attribute per judge call.
5. Each judge suite carries planted items: an empty "Solution:" answer, a longer repetitive twin, an order swap, and a relabel. The false-positive rate on these is part of the result.
6. Judged scores stay advice and mechanical gates stay law (METHOD s.8), because a monitor judge missed 35-58% of cheating on complex tasks [impossiblebench-003].

**Recommendation: adopt these as evaluator design rules** (human approval required). Decided by the order-swap flip rate, kappa against humans, and the planted-item false-positive rate.

### 3.4 Structured assertions from free text

**Context.** `groundworks/assertions.py` asks a fact to assert a field value on a target row through an edge; a pluggable judge proposes those assertions. This is classification into a small declared domain, grounded in the fact's text (INFERRED from the module docstring).

**What the evidence says.**
- **Decompose, then verify each piece.** FActScore breaks text into atomic facts and checks each against a source; its automated estimator had "less than a 2% error rate" on the aggregate score [factscore-001, -002].
- **But the decomposer is a variable.** Scores are "sensitive to the decomposition method used" [claimdecomp-001]. DecompScore checks that each subclaim is supported by the original [claimdecomp-002]. CONFIRMED. **For XScholar:** a proposed assertion should be entailed by the fact's own text, and that check is a test case (INFERRED).
- **Small checkers.** MiniCheck-FT5 (770M) "reaches GPT-4 accuracy" on LLM-AggreFact at 400x lower cost [minicheck-001, -002], and beat AlignScore by 4.3% overall [minicheck-005]. Its training data was generated with GPT-4 plus ANLI [minicheck-003]. The strongest variant, Bespoke-MiniCheck-7B, is **CC BY-NC 4.0** [card-bespoke-minicheck-7b-001]. HHEM-2.1-Open returns a 0-1 support score for (premise, hypothesis) [card-hhem-002].
- **Real claims are harder than NLI benchmarks** [wice-001]. Contract hypotheses with evidence spans exist publicly (ContractNLI) [contractnli-001].
- **Anchoring and leniency** (3.3) apply: a judge shown the fact and a candidate assertion may be led by the candidate [mtbench-judge-007].

**Recommendation: prototype after P1**, as follows (INFERRED):
- The judge outputs only through a schema whose enums are the declared `fields` and their domains (structured outputs).
- One target per call.
- Every proposal carries a quote from the fact, checked locally.
- A proposal is scored against the fact text by a second check: an independent model call or a local entailment model.
- Proposals stay proposals: `enforce` and promotion stay human-owned.

**Measure** precision and recall on planted contradictions in the demo instance and on a ContractNLI slice. **Watch** MiniCheck-FT5 and HHEM as local second opinions: weights MIT and Apache-2.0 by metadata (INFERRED), but their training data (ANLI) licence is UNKNOWN here.

### 3.5 Model routing and cost

**What the evidence says.**
- **Prices** (vendor, CONFIRMED), per million input/output tokens:
  - Haiku 4.5: $1 / $5 [anthropic-pricing-001]
  - Sonnet 5: $2 / $10 [-002]
  - Opus 5: $5 / $25 [-003]
- **Discounts** (CONFIRMED): the Batch API is 50% off both directions [-004]. A cache hit is 10% of input price and pays off after one read at 5 minutes [-005].
- **Tokenizer.** Newer models' tokenizer produces "approximately 30% more tokens for the same text" [-006], so per-token prices across generations are not directly comparable.
- **Routing research.** Learned routers cut cost "by over 2 times without sacrificing response quality" on chat benchmarks [routellm-001]. FrugalGPT reports "up to 98% cost reduction" on selected tasks [frugalgpt-001]. A cascade with abstention gives a provable agreement level at the price of escalating about 20% [trust-or-escalate-002]. Small grounded fact-checkers match a frontier model at far lower cost [minicheck-001]. All CONFIRMED; none measured on labelling.

**Which steps need what** (INFERRED):

| Step | Needs a frontier model? | Why |
|---|---|---|
| Quote verification | **No model** | The validator's string check is exact and free; keep it |
| Deduplication | **No model** first | Content hashes, then BM25 near-duplicates; a model only for the residue |
| Tier-0 triage | Cheap model **with escalation and audit** | Errors here hide load-bearing units; cost of a miss is high, so escalate on low confidence (trust-or-escalate) and audit a random sample (`docs/TUNING.md:35`) |
| Tier 2-3 labelling, reasons | Strong model | The expert judgment the method sells |
| Adversarial roles, panels | Strong models, **mixed families** where the licence and provider approval allow | Diversity reduces shared bias [juries-001] |
| Assertion proposals | Mid or strong, constrained output | Classification into a known domain |

**Illustration, not a measurement.** The first instance used about 7.4M subagent tokens (`docs/METHOD.md:216`). Priced entirely as Opus 5 input, that is about $37; as output, $185; batch halves either. The real split is UNKNOWN. Claude Code subagents are not Batch API requests, so batch pricing needs an API-driven labeller (INFERRED).

**Recommendation.** **Prototype** batch plus caching for API-run labelling (decided by cost per unit and an unchanged validator pass rate). **Prototype after P1** the tier-0 cascade (decided by audit agreement with the strong labeller, with an interval, and the escalation share).

### 3.6 Agent reliability

**Failures seen:** a usage limit killed stages; agents stamped invented values into bookkeeping fields; a verifier died and promotions went through (`docs/TUNING.md:7-8`; commits 5ee30bb, dff134c).

**What the evidence says.**
- **Verification is a whole failure category.** Premature termination was 6.2%, no or incomplete verification 8.2%, and incorrect verification 9.1% of failures in 150 multi-agent traces (κ = 0.88 on the taxonomy) [mast-001, -002, -003]. CONFIRMED.
- **Hooks fail open.** Exit 1, a missing script, a mistyped path or a timeout all let the action proceed [claude-code-hooks-001, -002, -003]. CONFIRMED (vendor). **A verifier implemented as a hook is not a gate.**
- **Agents exploit a checker they can reach.** Hiding tests cut cheating "to near zero"; read-only access restored legitimate performance while preventing test modification [impossiblebench-001, -002]. LLM monitors caught 86-89% of cheating on simple tasks but 42-65% on complex ones [impossiblebench-003]. CONFIRMED. This supports the fence (METHOD s.15) and says monitors are not substitutes for gates.
- **One run is a sample of one.** Agents that succeed on under 50% of tasks show pass^8 below 25% [taubench-002]. CONFIRMED.
- **Durable work.** Message Batches are server-side jobs; most finish in under an hour [anthropic-batch-001]. That they survive a client session dying is INFERRED.

**Recommendations.**
- **Adopt** (already the rule, `docs/TUNING.md:7-8`): holds in datastore gates with independent promotion (V2-20); lanes write rows as they go; loader-owned fields not writable by agents (`access:`).
- **Prototype:** a `SubagentStop` hook that exits 2 when the lane's declared output rows are absent [claude-code-hooks-004]. It must be tested by planting a missing row, and by a broken hook path, which must surface as an error, not silence.
- **Prototype:** a pass^k report for each lane on a fixed slice (k=3), next to validator pass rates.
- **Idempotency** (INFERRED): stable ids and UPSERT are already the pattern (commit 7733fd5). Extend it to every lane write, so a rerun after a usage limit never duplicates or reverts.

Decided by planted-failure tests: a dead verifier, a killed lane mid-write, a forged bookkeeping field. Each must end in a refusal or a named error.

### 3.7 Tooling ecosystem

| Tool | Licence (tag) | Fits a stdlib core? | Where it would sit | Verdict |
|---|---|---|---|---|
| MCP spec 2025-11-25 | Apache-2.0 for spec contributions, older MIT, docs CC-BY-4.0 [lic-mcp-spec-001] (CONFIRMED) | Protocol, already used | `groundworks/mcp` | **Adopt as is.** The spec says tools are "arbitrary code execution" and tool annotations are untrusted [mcp-spec-2025-11-25-001, -002], which matches the existing `untrusted_source_text` envelope |
| MCP Python SDK | MIT (CONFIRMED) | A dependency | Adapter edge only | Watch; the current adapter works without it (INFERRED) |
| Inspect (UK AISI) | MIT (CONFIRMED) | No: Python package with dependencies | Outside the core, a separate eval venv | Watch. It has model-graded evals [readme-inspect-ai-001]; our evaluator must stay human-owned |
| promptfoo | MIT (CONFIRMED) | No: Node | Outside | Watch |
| DeepEval, Ragas | Apache-2.0 (CONFIRMED) | No | Outside | Watch; LLM-judged metrics carry every bias in 3.3 |
| Langfuse | MIT outside `ee/` directories, separate licence inside (CONFIRMED) [lic-langfuse-001] | No: a server | Outside, self-hosted only | Watch; never send matter text to a hosted tracer |
| Arize Phoenix | **Elastic License 2.0**: no hosted or managed service [lic-phoenix-001] (CONFIRMED) | No | — | **Avoid in the product** (source-available class) |
| OpenTelemetry GenAI conventions | Core semantic-conventions repo Apache-2.0 [lic-otel-semconv-001] (CONFIRMED); the new GenAI repo's licence not read (UNKNOWN) | Yes, as **names only** | Write JSONL traces with GenAI attribute names, no SDK | **Watch**: moved to a new repo whose schema URL is still "TODO" [readme-otel-semconv-genai-002] |
| RouteLLM | Apache-2.0 (CONFIRMED) | No | Outside | Watch; the cascade rule is simple enough to write in stdlib |
| ColBERT, PyLate | MIT (CONFIRMED) | No (torch) | GPU venv beside `groundworks/embed` | Watch |
| MiniCheck code, AlignScore, FActScore | Apache-2.0, MIT, MIT (CONFIRMED) | No | GPU venv | Watch (3.4) |

## 4. Public datasets with ground truth

The class column maps each licence onto `docs/LICENSING.md` s.6. **Owner decision needed:** s.6 lists software licences only. CC BY 4.0 and ODC-By are attribution-only (I place them in "Permissive"), and CC BY-SA has no class (INFERRED).

| Dataset | Size (from the paper) | Ground truth for | Licence (tag) | Class | XScholar layer |
|---|---|---|---|---|---|
| **ContractNLI** | 607 contracts [contractnli-002] | Hypothesis entailed / contradicted / not mentioned, **plus evidence spans** [contractnli-001] | CC BY 4.0 [site-contractnli-001] (CONFIRMED statement) | Permissive | L2 labels with quotes; L3 assertion judge; L6 evidence retrieval |
| **CUAD** | 500+ contracts, 13,000+ expert annotations, 41 categories [cuad-001] | Clause spans by category | CC BY 4.0, "commercial and non-commercial use"; underlying contract text not warranted [dcard-cuad-qa-001, -002] (CONFIRMED statement) | Permissive, with caveat | L2 extraction accuracy on long documents |
| **MAUD** | 39,000+ examples, 47,000+ annotations [maud-002] | Deal-point answers on merger agreements | CC BY 4.0 [maud-001] (CONFIRMED statement, in the paper) | Permissive | L2 multi-choice extraction |
| **SciFact** | 1.4K expert-written claims [scifact-001] | Supports/refutes label **and rationale sentences** | Claims CC BY 4.0; abstracts ODC-By 1.0; code Apache-2.0 [lic-scifact-001, -002] (CONFIRMED) | Permissive | L2/L3 support with rationale; cross-field discovery demo |
| **QASPER** | 5,049 questions over 1,585 papers [qasper-001] | Answers with evidence paragraphs; questions written by readers who had not seen the full text | CC BY 4.0 [dcard-qasper-001] (CONFIRMED statement on the card) | Permissive | **L6 reworded-query bake-off** |
| **LegalBench-RAG** | 6,858 query-answer pairs, 79M+ characters [legalbench-rag-001] | Snippets with file path, exact quote and character offsets [legalbench-rag-002] | Code MIT (CONFIRMED); data inherits ContractNLI, CUAD, MAUD, **PrivacyQA (licence UNKNOWN)** [readme-legalbench-rag-001] | Mixed | L6 retrieval with verbatim qrels. Use the three CC BY subsets only until PrivacyQA is cleared |
| **LegalBench** | 162 tasks [legalbench-001] | Classification labels | "subject to different licenses" per task [dcard-legalbench-001] (CONFIRMED statement) | Per task | Lens and role checks; check each task first |
| **WiCE** | UNKNOWN (not extracted) | Entailment per **subclaim**, with evidence sentence indices [wice-002] | Annotations ODC-BY; text CC BY-SA and Common Crawl terms [lic-wice-001] (CONFIRMED) | Annotations permissive; text unclassified | L3 claim decomposition |
| **HoVer** | 26k claims [hover-001] | Multi-hop supports/refutes plus evidence | CC BY-SA 4.0 [site-hover-001] (CONFIRMED statement) | **Unclassified (share-alike)** | Internal eval only until classified |
| **FEVER** | 185,445 claims; annotator Fleiss κ 0.6841 [fever-001] | Supported / refuted / not enough info plus evidence | **UNKNOWN** (licence link not read) | — | L3; useful as a human-agreement ceiling |
| **VitaminC** | 400K+ (not excerpted: UNKNOWN) | Contrastive evidence pairs [vitaminc-001] | Repository MIT (CONFIRMED); data from Wikipedia revisions, licence UNKNOWN | — | Planted-change detection (like number_change plants) |
| **ExpertQA** | 2,177 questions, 32 fields [expertqa-001] | Expert-verified attributions | Repository MIT (CONFIRMED); data licence not separately stated (INFERRED MIT) | Permissive (INFERRED) | Claim-audit role; attribution verdicts |
| **RAGTruth** | Nearly 18,000 responses [ragtruth-001] | Span-level hallucination labels | Repository MIT (CONFIRMED), **but the QA part uses MS MARCO passages** [ragtruth-002] | **Avoid for commercial use** (INFERRED) | — |
| **AVeriTeC** | 4,568 real claims [averitec-001] | Verdicts with web evidence | **CC BY-NC 4.0** [readme-averitec-001] (CONFIRMED) | **Banned** | — |
| **LLM-AggreFact** | 10 datasets aggregated [minicheck-004] | Grounded support labels | Card gated (401); follows each constituent (UNKNOWN) | — | Only via cleared constituents |

**Best first choices.** ContractNLI (spans, entailment, CC BY), SciFact (rationales, CC BY/ODC-By) and QASPER (reworded queries, CC BY). All three fit a **public demo**, with attribution (INFERRED).

## 5. Licence verdict for every model and tool named

Weights tags come from model cards (vendor). "Data" flags training-data problems.

| Model or tool | Licence | Tag | Class | Verdict |
|---|---|---|---|---|
| Granite-embedding-english-r2 | Apache-2.0; states no MS MARCO [card-granite-embedding-r2-001, -002] | INFERRED (card) | Permissive | **Allowed**; first candidate |
| Granite-embedding-reranker-english-r2 | Apache-2.0; permissive-data claim [card-granite-reranker-r2-001] | INFERRED | Permissive | **Allowed**; first reranker candidate |
| Qwen3-Reranker-0.6B / 4B | apache-2.0 metadata | INFERRED | Permissive | Allowed; training data UNKNOWN |
| BAAI/bge-reranker-v2-m3 | apache-2.0 metadata | INFERRED | Permissive | Allowed; training data UNKNOWN |
| mixedbread mxbai-rerank-base-v2 | apache-2.0 metadata | INFERRED | Permissive | Allowed; training data UNKNOWN |
| zerank-1-small | Apache-2.0 [card-zerank-1-small-001]; its legal score of 0.817 is the vendor's own benchmark [-002] | INFERRED | Permissive | Allowed; training data UNKNOWN |
| jina-reranker-v2-base-multilingual | cc-by-nc-4.0 [card-jina-reranker-v2-001] | INFERRED | Non-commercial | **Never** |
| jina-embeddings-v3 | CC-BY-NC-4.0 (`docs/LICENSING.md` s.4) | per repo | Non-commercial | **Never** |
| cross-encoder/ms-marco-MiniLM-L6-v2 | apache-2.0 weights; **trained on MS MARCO** | INFERRED | Permissive weights, NC data | **LAWYER before use** |
| colbertv2.0 checkpoint | mit weights; trained on MS MARCO [card-colbertv2-001] | INFERRED | Same | **LAWYER** |
| GTE-ModernColBERT-v1 | apache-2.0; distilled on MS MARCO [card-gte-moderncolbert-001] | INFERRED | Same | **LAWYER** |
| answerai-colbert-small-v1 | apache-2.0; data not stated | INFERRED | Permissive | Allowed pending a data check |
| opensearch sparse v3 distill | Apache-2.0; MS MARCO in training [card-opensearch-sparse-v3-distill-001, -002] | INFERRED | Same | **LAWYER** |
| SPLADE library and naver weights | CC BY-NC-SA 4.0 [lic-splade-001] | CONFIRMED (library); weights card gated (401), UNKNOWN | Non-commercial | **Never** |
| MiniCheck-Flan-T5-Large | mit metadata [card-minicheck-flan-t5-001]; trained with ANLI and GPT-4 data | INFERRED | Permissive weights | Watch; ANLI licence UNKNOWN |
| Bespoke-MiniCheck-7B | CC BY-NC 4.0 [card-bespoke-minicheck-7b-001] | CONFIRMED (statement) | Non-commercial | **Never** |
| HHEM-2.1-Open | apache-2.0 metadata [card-hhem-001] | INFERRED | Permissive | Allowed; watch |
| DeBERTa-v3-large-mnli-fever-anli-ling-wanli | mit metadata; ANLI in training [card-deberta-nli-laurer-001, -002] | INFERRED | Permissive weights | Watch; data licence UNKNOWN |
| embeddinggemma-300m | Card gated (401) | UNKNOWN | — | Not assessed |
| Claude (Haiku 4.5, Sonnet 5, Opus 5) | Anthropic Commercial Terms via API key (`docs/LICENSING.md` s.1; not re-read today) | INFERRED current | Commercial service | Allowed through API keys |
| MCP spec / Python SDK | Apache-2.0 (+MIT, CC-BY-4.0 docs) / MIT | CONFIRMED | Permissive | Allowed |
| Inspect, promptfoo, PyLate, ColBERT code, AlignScore, FActScore | MIT | CONFIRMED | Permissive | Allowed, outside the core |
| DeepEval, Ragas, RouteLLM, MiniCheck code | Apache-2.0 | CONFIRMED | Permissive | Allowed, outside the core |
| Langfuse | MIT except `ee/` | CONFIRMED | Permissive plus a separate licence | Allowed outside `ee/` |
| Arize Phoenix | Elastic License 2.0 | CONFIRMED | Source-available | **Avoid in the product** |
| OpenTelemetry semantic conventions | Apache-2.0 (core repo) [lic-otel-semconv-001]; GenAI repo not read | CONFIRMED (core) / UNKNOWN (GenAI) | Permissive | Names only; no dependency |
| Cohere rerank (LegalBench-RAG's test) | Commercial API; not assessed | UNKNOWN | — | Not recommended (negative legal result) |

## 6. What was checked and did not help, or does not fit

**Negative results in the sources:**
- A general-purpose reranker made legal snippet retrieval worse [legalbench-rag-003].
- Ensembling prompts did not improve the few-shot judge [alt-annotator-002].
- Unfiltered document expansion hurt retrieval [doc2query-minus-001].
- Self-correction without external feedback does not work reliably [selfcorrect-001].
- Verbalized confidence is overconfident [express-uncertainty-001].
- An LLM monitor misses a third to a half of cheating on complex tasks [impossiblebench-003].
- Hooks fail open [claude-code-hooks-001].

**Checked and does not fit:**
- **SPLADE, jina rerankers, Bespoke-MiniCheck-7B, AVeriTeC:** non-commercial. Banned.
- **RAGTruth:** its QA part uses MS MARCO passages [ragtruth-002].
- **Structured outputs together with the Citations API:** incompatible in one call [anthropic-citations-002]. XScholar's local quote check makes the Citations API unnecessary (INFERRED).
- **Hosted tracing and eval SaaS:** would send matter text to a third party (METHOD s.16), and Phoenix is source-available.
- **Late interaction now:** storage and dependency cost [colbertv2-001], and the leading checkpoints carry MS MARCO training. Premature before a reranker has shown any gain.
- **"Stated confidence as an accuracy number":** use it only to order human review.

**Not found or not established:**
- A study measuring LLM tier-labelling accuracy on legal record units: UNKNOWN. None was found by search, and that is **not** a claim that none exists.
- The ANLI, FEVER and PrivacyQA data licences.
- The splade-v3, embeddinggemma and LLM-AggreFact cards returned 401 (gated).

## 7. Prototypes proposed (at most two; owner approval required)

**P1: Label-accuracy measurement (L2).**
- **Public half.** Run the current labeller on a ContractNLI slice of about 50 contracts' hypotheses (CC BY 4.0). Map gold labels and evidence spans to tier, direction and quote. Report per-field kappa and span precision/recall against gold, with bootstrap intervals.
- **Real-instance half.** A frozen 100-unit stratified sample, a blind second labeller from a different model or guide, and human adjudication of every relevance split. Output the labeller's accuracy per field with an interval, and the alt-test result.
- **Keep or discard.** Nothing in the method changes on this result. It becomes the first evaluator entry for routing and guide changes.
- **Cost.** API tokens plus adjudication hours; no downloads, stdlib only.

**P2: Retrieval on reworded queries (L6). This is roadmap B14 (`docs/ROADMAP.md:27`) with two arms added.**
- **Query set.** Build it from QASPER (CC BY 4.0) plus the instance's own non-quoting queries, extended by human rewording of a sample of quoting queries.
- **Arms.** (a) BM25, (b) BM25 with an LLM-expanded query, (c) BM25 top-100 reranked by Granite-embedding-reranker-english-r2. Arm (c) needs the owner's approval to download weights into the existing GPU venv.
- **Keep or discard.** Adopt only if the existing bake-off verdict rule says "better than bm25" on nDCG@10.

## 8. The corpus

**Location.** `research_corpus/models/` in the working session's scratch directory, outside every repo. The absolute path is deliberately not recorded here: it names the machine user and a private project.

**Format.** `research_corpus/FORMAT.md` exactly:
- `sources.jsonl`, one line per source, including `sha256`, `media_type`, `bytes` and `capture`.
- `excerpts.jsonl`, one verbatim quote per line.
- `originals/<sha256>.txt`, what was actually read.

**Capture.** Every original is `rendered_text`. arXiv papers were read as `pdftotext` extractions of the PDF; HTML pages as stripped text; raw GitHub and Hugging Face files as fetched, with a one-line `URL:` header added (so `raw` would be untrue). Every excerpt was string-checked (whitespace-normalised) against its saved original, and the whole file re-verified after the format change: 0 failures.

**Size.** 104 sources, 166 excerpts, about 5.4 MB of originals.

**Public demo.** `public_ok: true` on 24 sources: the arXiv papers whose arXiv licence is CC BY 4.0 or CC0, plus the MCP specification page (Apache-2.0).
- **CC BY 4.0:** cdi, gilardi, selfconsistency, verbal-calibration, selfcorrect, self-preference, validators, inconsistent-evaluators, colbertv2, query2doc, legalbench-rag, taubench, impossiblebench, scifact, contractnli, qasper, maud, legalbench, expertqa, reliability-without-validity, trust-or-escalate.
- **CC0:** alt-annotator, judging-judges.

The paper licences come from arXiv's licence link, so `licence_confirmed` stays false on them. Vendor docs, model cards and arXiv-default-licence papers are `public_ok: false`.
