# Reading list for the methods brain

See also `LINEAGE_STUDY.md`: the twelve known techniques the method is built from, with study questions and sources for each.

Run these through the scholar pipeline (methods ladder: reproduction > primary read in full > independent reporting > vendor > aggregator). Each entry says what question the reading must answer, so extraction is goal-directed.

**Status:** `found` = located by search on 2026-09-15, not yet read; `read` = read in full; `memory` = citation from memory, locate and verify before use. Nothing below has been read in full yet unless marked.

## 1. Reading long corpora with agents

| Item | Where | Question for the scholar | Status |
|---|---|---|---|
| Zhang, Kraska, Khattab. *Recursive Language Models* | arXiv 2512.24601; github.com/alexzhang13/rlm | Which decomposition and sub-call patterns scale best? What failure modes do they report at long lengths? | found (repo concept used) |
| karpathy/autoresearch | github.com/karpathy/autoresearch | Read in full 2026-09-15 (README, program.md): fixed evaluator, keep/discard log, simplicity criterion. What do forks add for multi-agent research orgs? | read |
| *Memory in the Age of AI Agents* | arXiv 2512.13564 | How do agent memory designs handle retraction, provenance and contradiction? | found |
| Shao et al. *STORM* (Wikipedia-like articles from scratch) | arXiv 2402.14207 | Multi-perspective question asking as a lens mechanism? | memory |

## 2. Graph retrieval with provenance

| Item | Where | Question | Status |
|---|---|---|---|
| *Why Neighborhoods Matter: Traversal Context and Provenance in Agentic GraphRAG* | arXiv 2605.15109 | How do they represent provenance on traversal? Anything for our hook lookups? | found |
| *Is GraphRAG Needed? From Basic RAG to Graph-/Agentic Solutions with Context Optimization* | arXiv 2606.25656 | When does graph structure beat simple retrieval? Where would our exact lookup lose? | found |
| *A-RAG: Scaling Agentic RAG via Hierarchical Retrieval Interfaces* | arXiv 2602.03442 | Hierarchical interfaces vs our chunk/batch design | found |
| *GraphSearch: An Agentic Deep Searching Workflow for Graph RAG* | arXiv 2509.22009 | Deep search patterns over a graph; applicable to bridge finding? | found |
| *A Systematic Review of Key RAG Systems* | arXiv 2507.18910 | Taxonomy of gaps; which gaps does gating address? | found |

## 3. Verification, annotation, truth maintenance

| Item | Where | Question | Status |
|---|---|---|---|
| *Human-LLM Collaborative Annotation Through Effective Verification of LLM Labels* (CHI 2024) | dl.acm.org/doi/10.1145/3613904.3641960 | How do they choose which labels humans verify? Apply to our tier-3 adjudication | found |
| *GradeAgentOps: A Verification-First Framework for Evidence-Anchored LLM Exam Grading* | doi.org/10.3390/ai7060198 | Grading contracts, deterministic verification, bounded repair; compare to our validator | found |
| *Autonomous Evaluation of LLMs for Truth Maintenance and Reasoning Tasks* | arXiv 2410.08437 | Formal verification of outputs; any piece usable as a mechanical gate? | found |
| *Fixing FOLIO and MALLS: Verified Annotations and an LLM-assisted Framework to Focus Human Relabeling* | arXiv 2606.02837 | Targeting human relabeling; label quality measurement design | found |
| *LLMs build inconsistent knowledge graphs from one immune-checkpoint corpus* | PMC 13529944 | Cross-model disagreement as a quality signal; consensus protocol | found |
| *An LLM and Embeddings-Based Multi-agentic System for Knowledge Graph Construction and Verification* | ResearchGate 398694134 | Multi-agent verification of graph facts | found |
| *BioKGBench* | arXiv 2407.00466 | Benchmarking agents that check knowledge graph facts | found |
| *AI scientists produce results without reasoning scientifically* | arXiv 2604.18805 | Failure modes of autonomous research agents; guardrails for our loop | found |
| Doyle, *A Truth Maintenance System* (1979) | Artificial Intelligence journal | Justification-based revision; design for automatic demotion (our known gap) | memory |
| de Kleer, *An Assumption-based TMS* (1986) | Artificial Intelligence journal | Multiple contexts at once; relation to lenses | memory |
| Ratner et al., *Snorkel* (weak supervision) | VLDB 2017 | Modeling noisy labelers; estimating label accuracy without full ground truth | memory |

## 4. Argumentation and adversarial review

| Item | Where | Question | Status |
|---|---|---|---|
| Dung, *On the acceptability of arguments* (1995) | Artificial Intelligence journal | Formal acceptability from attack relations; compute "ready" from the attack graph? | memory |
| Pollock, work on defeasible reasoning (rebutting vs undercutting defeaters) | Cognitive Science and later books | Distinguish rebut and undercut edges in our schema | memory |
| Toulmin, *The Uses of Argument* (1958) | book | Warrant and backing as explicit nodes? | memory |
| Klein, *Performing a Project Premortem* (2007) | Harvard Business Review | Premortem protocol details | memory |
| Legal skills: Opposing Counsel Review, Sustainable Opposing Counsel Review, Judicial First Impression, Proposition Audit, Pre-Motion, uk-citation-verification | github.com/lawve-ai/awesome-legal-skills (per-skill licenses) | Read in full 2026-09-15; adapted where MIT or Apache-2.0 | read |

## 5. Literature-based discovery and hypothesis generation

| Item | Where | Question | Status |
|---|---|---|---|
| Swanson, fish oil and Raynaud's (1986) | Perspectives in Biology and Medicine | The original ABC model; what made the link verifiable | memory |
| *HypER: Literature-grounded Hypothesis Generation and Distillation with Provenance* | arXiv 2506.12937 | Provenance design for hypotheses; compare to our bridge gate | found |
| *AGATHA: Automatic Graph-mining And Transformer based Hypothesis generation* | arXiv 2002.05635 | Time-split validation setup; baselines for D2 | found |
| *Are Abstracts Enough for Hypothesis Generation?* | arXiv 1804.05942 | Full text vs abstract; sets the ladder for discovery cards | found |
| *Drug Repurposing for COVID-19 via Knowledge Graph Completion* | arXiv 2010.09600 | Graph completion as candidate generation | found |
| *Literature-Based Discovery (LBD): Towards Hypothesis Generation and Knowledge Discovery in Biomedical Text Mining* | ResearchGate 392857600 | Survey of evaluation methods | found |
| *Enriching plausible new hypothesis generation in PubMed* | PMC 5498031 | Plausibility filtering | found |
| *Enriched knowledge representation in biological fields: LBD in Alzheimer's disease* | PMC 11924609 | Representation beyond co-occurrence | found |
| Lu et al., *The AI Scientist* | arXiv 2408.06292 | End-to-end autonomous research loop; what evaluation keeps it honest | memory |
| Gottweis et al., *Towards an AI co-scientist* | arXiv 2502.18864 | Multi-agent hypothesis tournaments; relation to our red team and lenses | memory |

## 6. Legal AI and knowledge graphs (domain of instance 1)

| Item | Where | Question | Status |
|---|---|---|---|
| *Capturing Legal Reasoning Paths from Facts to Law in Court Judgments using Knowledge Graphs* | arXiv 2508.17340 | Fact-to-law path representation; mapping to findings and factors | found |
| *Context Graphs for Legal Reasoning and Argumentation* | arXiv 2007.00732 | Argument graph structure for legal reasoning | found |
| *Bridging Legal Knowledge and AI: RAG with Vector Stores, Knowledge Graphs, and Hierarchical NMF* | arXiv 2502.20364 | Hybrid store design | found |
| *A comprehensive framework for legal dispute analysis integrating prompt engineering and multi-dimensional knowledge graphs* | PMC 12780046 | Multi-dimensional legal KG; compare to our dimensions | found |
| ABA, *Life of an Appeal in the Age of AI* | americanbar.org (Appellate Issues, Winter 2026) | Practitioner expectations; where verification is demanded | found |

## 7. Tools referenced by other skills (verify existence first)

| Item | Where | Question | Status |
|---|---|---|---|
| Verity (in-generation hallucination minimization MCP) | github.com/johnnyryan/Verity (as referenced in the Proposition Audit skill) | Cross-model disagreement signal at generation time | memory |
| claim-audit (citation-fidelity scoring) | github.com/mhalle/claim-audit (as referenced in the Proposition Audit skill) | Scoring whether a cited source supports a proposition | memory |
