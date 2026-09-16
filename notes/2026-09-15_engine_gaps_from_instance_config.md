# 2026-09-15: engine gaps exposed by configuring a real instance

Generic; no matter content. Source: a private instance's Phase 0 configuration (its eight answers, adapter and probes) written against the v0.1 engine plus the v0.2 lens shape. The config loaded, the probes behaved (2 rules, 2 refused, 2 accepted, 0 misbehaved), and 28,206 units were ingested with 5,867 exclusions logged. The gaps below are constructs the instance needs that the generic engine has no table, edge or rule for yet. Until the engine grows them, an instance carries them as its own tables beside the generic ones.

| Gap | What the instance needs | Proposed generic form |
|---|---|---|
| **Preservation** | Whether an issue was raised in time and how (objection, application, ruling, offer of proof), as a gate input for readiness | A `preserves` edge from a unit to a conclusion with a `how` value; a config rule `conclusion_ready requires preserves min 1` (with an `unpreserved` exception recorded on the conclusion) |
| **Authorities verified from official text** | A citation is "verified" only if read in official text; existence is never authority | An `authority` table exists; add `source` units of grade `official_text` and a rule `authority.verified requires cites -> unit WHERE grade = 'official_text'` |
| **Output citations with verdicts** | Every citation in a deliverable gets VERIFIED / MISMATCH / NOT_FOUND / ...; a demoted claim marks its citations for recheck | A `citation` table (draft, text, kind, verdict, salience) with a `resolves` edge to the unit or claim; truth maintenance sets `NEEDS_RECHECK` |
| **Events and mentions with stance** | A timeline of events, each with the units that mention it and each speaker's stance | `event` rows and a `mentions` edge (unit -> event) carrying `stance` and `speaker` |
| **Outcome-bearing edges** | Contradictions typed by source, rulings with outcomes, framings tried with outcomes (effective / ineffective / backfires) | `contradicts{source_type}`, `rules_on{outcome}`, `tried_on{outcome}` edges and an `approach` table; "outcomes live on edges" is already a method invariant, the tables are not generic yet |
| **Entities** | Parties, documents, exhibits as first-class rows units point at | `person`, `document`, `exhibit` tables with `in_doc`, `spoken_by`, `received_as` edges |
| **Two target kinds** | Findings (what a decision-maker stated) distinct from conclusions (what we argue) | A `target_kind` on the conclusion table, or a separate `finding` table that labels bear on and conclusions attack |
| **Authority rank** | Binding vs persuasive vs supporting vs pointer | `court_rank` / `authority.rank` from the config's authority axis (the template already declares the axis; the schema does not use it) |
| **Framework node text and locator** | Each framework node carries the transcribed text and where it was transcribed from | `frame.nodes` as objects `{id, text, locator}` rather than bare ids; `consideration` rows carry both |
| **Lens ordering** | A lens view has an order (strongest first) | `order:` on a lens entry, applied in `lens_view` |
| **Day-grouped chunking** | Messages chunk by whole days, not fixed counts | A `chunk_by: locator_field` option per unit type |
| **Filing grade** | Confirmed with a page-image citation vs confirmed with a text extract | `filing_grade` computed on fact from its best citation grade; already reported by the instance's health, not by the engine |
| **Order text absent** | The primary decision under review existed only as scanned images with no text extract | Intake must accept `grade: page_image` units with an image path and empty text plus an OCR step, or refuse loudly; today it refuses on empty text, which is correct but leaves the most important document out |

Also confirmed by this configuration: the config validator refused nothing it should have accepted; the sealed-material exclusion works as a fail-closed pattern file outside the repo; the status-only labelling of 20,585 unusable units passed validation on a sample.

**Roadmap:** these become v0.2/v0.3 rows under "Extract the engine". None blocks the instance's rebuild, which carries them instance-side meanwhile.
