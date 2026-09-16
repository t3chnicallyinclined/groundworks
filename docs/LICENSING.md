# Licensing and commercial compliance

**Written 2026-09-15** from three research passes that read primary texts (license files, vendor terms, statutes, a U.S. Supreme Court opinion, the Copyright Office report, ABA Formal Opinion 512, 22 NYCRR Part 161). **This is not legal advice.** Items tagged LAWYER need a technology-transactions lawyer before launch; the list for that meeting is at the end.

## Verdict

Nothing found blocks selling XScholar to law firms. There are conditions, and a few fixes are owed.

| | Status |
|---|---|
| Our code and method | Ours. Protect with trade secret and contract; copyright in AI-written code is thin (below) |
| SurrealDB | Allowed in our product; a commercial license is bought if customers ever get schema control or raw database access |
| Claude | Allowed through **API keys under Anthropic's Commercial Terms**; not through Pro/Max credentials |
| Borrowed skills and ideas | Allowed; notice fixes owed (below) |
| Legal data | Court opinions free; CourtListener API needs a commercial agreement; **no automated Westlaw or Lexis access, ever** |
| Embedding models | Most are fine; **jina-embeddings-v3 is non-commercial: never use** |
| Selling to lawyers vs the public | Attorneys who sign: lower risk. Self-represented litigants: unauthorized-practice exposure (LAWYER) |

## 1. Core stack

**SurrealDB: Business Source License 1.1** (LICENSE at `main` and `v3.1.6`, identical). Change Date 2030-01-01 to Apache-2.0.
> "you may not use the Licensed Work as a Database Service. 'Database Service' means any product, service, platform, or commercial offering in which the Licensed Work is used to provide database functionality to third parties ... that enables third parties to create, manage, or control schemas or tables."

- Hosted product: permitted if tenants cannot shape schemas or tables (INFERRED; LAWYER, or written confirmation from SurrealDB sales).
- **Owner's stance (2026-09-15): licensing does not limit optimization.** The default product keeps schema control and raw query access out of tenants' hands because that is also good design (gates, provenance). If customers ever need schema control, raw query access or a managed database, **buy SurrealDB's commercial license before that feature ships.**
- Self-hosted at the customer: redistribution allowed; ship the BSL text conspicuously (INFERRED; LAWYER on flow-down).
- We run it and sell work product: internal use, permitted.
- A commercial license exists (surrealdb.com/license, /pricing). SDK `surrealdb.py` and `surrealkv`: Apache-2.0.

**Anthropic** (Commercial Terms effective 2025-06-17; Consumer Terms 2025-10-08; Usage Policy 2025-09-15; Claude Code legal and data-usage pages).
- API use may "power products and services Customer makes available to its own customers"; customer owns outputs; "Anthropic may not train models on Customer Content"; 30-day standard retention, zero data retention available for qualified organizations.
- Customers must be told "factual assertions in Outputs should not be relied upon without independently checking their accuracy" (D.3).
- No competing product or resale of the Services (D.4).
- Consumer plans: no access "through automated or non-human means" except via an API key; Claude Code docs: products and services must use API key authentication, not Free/Pro/Max credentials. Consumer data is used for training when the setting is on (5-year retention).
- Usage Policy lists legal use cases as high-risk **for consumer-facing** uses: qualified professional review before dissemination, and disclosure of AI at session start. A B2B tool where attorneys review output appears to meet this (INFERRED; LAWYER).

**Copyright in AI-generated material** (U.S. Copyright Office, Part 2, 2025-01-29): "Copyright does not extend to purely AI-generated material, or material where there is insufficient human control over the expressive elements"; "prompts do not alone provide sufficient control." Human selection, arrangement and modification can be protected.
- Protect the product with **trade secret** (access control, no publication of prompts, gates or guides), **contract** (customer terms forbidding reverse engineering and disclosure), and a **record of human design decisions** (DECISIONS.md, notes/). Self-hosted shipping weakens trade secret (LAWYER).

## 2. Borrowed material (audit of both repositories)

| Item | License | Status | Fix owed |
|---|---|---|---|
| Council of High Intelligence (panel ideas) | MIT | NOTICE with MIT text | Done (wording corrected 2026-09-15) |
| Four role skills in the first instance (premortem, cold bench, red team, claim audit), adapted from skills listed in lawve-ai/awesome-legal-skills | Apache-2.0 (one MIT source) | NOTICE and LICENSE present | Premortem: wrong LICENSE file (source is Apache-2.0), fix. Three NOTICEs license **our** changes as Apache-2.0: reword to "Modifications Copyright 2026 [owner]. All rights reserved; the original work is Apache-2.0." Add upstream copyright lines, derivative notices verbatim, source URL and commit |
| awesome-legal-skills list itself | CC BY-NC-ND 4.0 | Nothing copied from the list | None |
| harvard-lil/lawskills-hub | No license grant | Ideas only | Keep ideas-only; text not compared (UNKNOWN) |
| karpathy/autoresearch | README says MIT; **no LICENSE file** | Ideas only | Never copy code unless a LICENSE file appears |
| Recursive Language Models (rlm) | MIT per repo page | Ideas only | Name the license correctly in the instance architecture doc |

Runtime: Python standard library (PSF), SQLite (public domain), SurrealDB (BSL 1.1), git (GPL-2.0, development tooling only, not distributed). No other packages are used today.

## 3. Legal data sources

| Source | Verdict | Basis |
|---|---|---|
| Court opinion text | **Free to use** | *Georgia v. Public.Resource.Org* (2020): judges "cannot be the authors of ... the works they create in the course of their official duties"; *Banks v. Manchester* (1888) |
| NY Official Reports: opinion text | Free | Same doctrine |
| NY Official Reports: headnotes, statements of facts, notes | **Copyrighted** (Judiciary Law §438, vested in the Secretary of State) | Do not copy; pagination LAWYER |
| CourtListener bulk data | Free, commercial use (Public Domain Mark) | Bulk data page |
| CourtListener API | **Commercial agreement required** for a product; agent calls count as the account holder's | Terms 2026-08-05 |
| Westlaw | **Red for automation**: no mining, scraping, automated access, connecting to other software, or AI development | Thomson Reuters General Terms v5.1 |
| Lexis | **Red for automation**: no automated or agentic access; limited "incidental" use in a compliant AI tool | LexisNexis US General Terms §1.1(e), §1.2 |

Design consequence: authority verification runs automatically only against court-issued text and CourtListener (bulk now, API after an agreement). Westlaw and Lexis checks are done by a licensed human and logged as a separate verdict type.

## 4. Models and libraries

**Embedding models (commercial use allowed):** BAAI/bge-m3 (MIT); nomic-embed-text v1.5 and v2 (Apache-2.0); Snowflake arctic-embed-l-v2.0 (Apache-2.0); Qwen3-Embedding 0.6B/4B/8B (Apache-2.0); mxbai-embed-large-v1 (Apache-2.0); multilingual-e5-large (MIT in metadata; no LICENSE file; confirm). **Never:** jina-embeddings-v3 (CC-BY-NC-4.0).

**A model's own licence is not the whole licence (added 2026-09-16, `research/MODELS_AND_TOOLING_2026-09-16.md`).** Many retrievers tagged MIT or Apache-2.0 were *trained* on **MS MARCO**, whose terms say "non-commercial research purposes only". The tag on the weights says nothing about that. Named in the review: the ColBERTv2 checkpoint, GTE-ModernColBERT, the OpenSearch sparse model, and the ms-marco cross-encoder family. **Until a lawyer answers question 8, treat a model trained on MS MARCO as not cleared for the product, whatever its tag says.** `scripts/license_check.py` reads licence files and imports, and cannot see training data, so this has to be checked by hand for every model. **Also never, non-commercial:** jina-reranker-v2, Bespoke-MiniCheck-7B, SPLADE (CC BY-NC-SA). **Avoid in the product:** Arize Phoenix (Elastic License 2.0).

**Libraries:** pySHACL, Apache Jena, sentence-transformers, transformers (Apache-2.0); RDF4J (BSD-3); FAISS, LadybugDB (MIT). Keep license and NOTICE files.
**Neo4j GDS (OpenGDS): GPLv3.** Hosted use is not "conveying"; shipping it self-hosted requires offering corresponding source (LAWYER on whether our calling code is covered). Community edition: 4 cores, 3 models.

## 5. Selling to the legal market

- **ABA Formal Opinion 512 (2024):** lawyers need informed client consent before putting client information into self-learning tools; must read vendor terms; must know whether the vendor retains data or claims rights in it; must verify output. **Product requirements:** no training on client data, zero or stated retention, deletion on request, a verification log, clear terms a lawyer can read.
- **New York, 22 NYCRR Part 161:** attorneys are not required to disclose AI use; courts may adopt a model rule under which signing a paper certifies review for fabricated citations. A verification log supports that certification.
- **Unauthorized practice (Judiciary Law §§478, 484, 476-a):** compensation for preparing pleadings by a non-attorney is prohibited, and the Attorney General can act against corporations. Selling to admitted attorneys who sign is lower risk; selling brief drafting to self-represented litigants needs a lawyer's opinion first.

## 6. Licensing policy (adopted by the owner 2026-09-15; METHOD s.14, CLAUDE.md rule 7)

| Class | Examples | Use as dependency | Adapt or copy code |
|---|---|---|---|
| Permissive | MIT, Apache-2.0, BSD, ISC, PostgreSQL, public domain | Yes, with notices | Yes, with notices and changes stated |
| Weak copyleft | MPL-2.0, LGPL | Yes, unmodified | No |
| Strong copyleft | GPL, AGPL | Hosted only, after review; never shipped without a lawyer | No |
| Source-available | BSL, SSPL, Elastic | Only with a written boundary (SurrealDB: no tenant schema control) or a commercial license | No |
| Non-commercial or no license | CC BY-NC, "TBD", no LICENSE file | No | No (ideas only) |

## 7. Actions

| # | Action | Owner | When |
|---|---|---|---|
| 1 | Turn off "help improve Claude" on any consumer plan that has touched client material; move automated runs to an API key | Owner | Now |
| 2 | Add a proprietary LICENSE and copyright headers to this repository | Needs owner's legal name or entity | Now |
| 3 | Fix the four role-skill NOTICE and LICENSE files as in section 2 | Claude, with owner name | Now |
| 4 | Keep `THIRD_PARTY_NOTICES.md` current; add a build check that fails on a non-commercial model or unlisted dependency | Claude | Before any customer |
| 5 | Default product keeps tenant schema control and raw queries out; price SurrealDB's commercial license, and buy it before any feature that gives customers schema control or database access | Owner | Before that feature ships |
| 6 | Automated verification against court text and CourtListener bulk only; Free Law Project agreement before using the API in the product; Westlaw and Lexis human-only | Design | Before launch |
| 7 | Customer terms: attorney-only, output must be independently verified (Anthropic D.3), no reverse engineering, confidentiality, data retention and deletion, no training on client data | Lawyer | Before launch |
| 8 | Business entity, name search and trademark (roadmap E1) | Owner, lawyer | Before launch |

## Questions for the lawyer
1. Does a hosted, multi-tenant legal-analysis product on SurrealDB fall outside BSL 1.1's "Database Service" if tenants never control schemas or tables? Flow-down for self-hosted installs?
2. Is work product sold to clients that was produced interactively on a consumer Claude plan compliant, or must all client work run under commercial terms?
3. How much protection do our code and prompts have given the Copyright Office's position, and what contract and trade-secret measures should customer terms include? Effect of self-hosted delivery?
4. Can we copy NY Official Reports pagination or cite formats? Is a lawyer pasting Lexis text into the product "incidental" use of a compliant AI tool?
5. Does our code become a GPLv3 covered work if a self-hosted edition calls Neo4j GDS?
6. Unauthorized-practice exposure: attorney-only terms sufficient? Any path to serve self-represented litigants?
7. Does Anthropic's Usage Policy high-risk requirement apply to a B2B tool used by attorneys, and what disclosure should the UI show?
8. A model released under MIT or Apache-2.0 but trained on data with non-commercial terms (for example MS MARCO): may its weights be used in a commercial product, hosted or self-hosted? Does the answer change for evaluation-only use?
9. **Data licences for test and evaluation corpora.** Section 6 classes software licences only. Where do CC BY 4.0, ODC-By and CC BY-SA sit? Specifically: may we (a) download them for private evaluation, (b) commit derived fixtures to this repo, (c) publish evaluation results, and (d) ship them in a public demo? CC BY-SA's share-alike clause is the case most likely to differ between those four.

## Sources
SurrealDB: raw.githubusercontent.com/surrealdb/surrealdb/main/LICENSE; /v3.1.6/LICENSE; surrealdb.com/license; surrealdb.com/pricing.
Anthropic: anthropic.com/legal/commercial-terms; anthropic.com/legal/consumer-terms; anthropic.com/legal/aup; code.claude.com/docs/en/legal-and-compliance; code.claude.com/docs/en/data-usage.
Copyright Office: copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf.
Cases: supremecourt.gov/opinions/19pdf/18-1150_7m58.pdf; law.cornell.edu/supremecourt/text/128/244.
New York: nycourts.gov/reporter/Regulations.shtml, /Operations.shtml, /decisions.shtml; nycourts.gov/rules/part-161-use-artificial-intelligence-technology; nysenate.gov/legislation/laws/JUD/478, /484, /476-A.
CourtListener: courtlistener.com/terms; wiki.free.law (REST v4 overview, citation lookup, bulk data).
Thomson Reuters General Terms v5.1 (US); lexisnexis.com/en-us/terms/general/default.page.
ABA Formal Opinion 512: americanbar.org (ethics opinions PDF).
Models: huggingface.co model cards named in section 4. GPLv3: gnu.org/licenses/gpl-3.0.txt. GDS: neo4j.com/docs/graph-data-science/current/introduction.
