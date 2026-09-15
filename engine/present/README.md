# XScholar reading layer (`engine/present`)

**What it is.** Deterministic renderings of structured results (claims, conclusions, critiques, authorities, citations, units) for fast human verification, plus a kit that measures whether a rendering helps a reviewer catch errors. Standard library only.

## Principles

1. **Presentation only.** A renderer never decides truth, never drops a row and never changes text. Emphasis, order and *how much of a row is shown first* change; content does not. Every row's full text is always in the rendering, and `metrics.preservation` proves it in every mode.
2. **Verification first.** What could be wrong is shown first. Eliminated claims are always shown, labelled `ELIMINATED CLAIM (false)`. Superseded claims are always shown, labelled `SUPERSEDED CLAIM (replaced by X)`.
3. **Salience is a named feature vector** computed at render time. It is never stored on a row and never summed into one number used for truth. Policies decide how features order and emphasize for each consumer.
4. **Domain-free engine.** The vocabulary (which statuses, grades and verdicts need checking, locator regexes, extra negation words, reader-facing reasons) comes from an instance policy JSON. The tests run a non-legal instance (incident postmortem), a legal-shaped one and a bigger design review, all invented (`synthetic.py`).
5. **Seeded copies are unmistakable.** Rows with `"seeded_test_copy": true` print `SEEDED TEST COPY - CONTAINS PLANTED ERRORS` as the first line in every mode.

## Two layers (v1.1)

`speedread` and `uncertain_first` render in two layers.

- **Scan layer.** One line per row: `` `id` [status/verdict/grade; answered; lens L] lead (locators) ``. The **lead** is the row's first sentence or first 160 characters, whichever ends first, cut at a word boundary and marked `…`. A lead is always a *verbatim prefix* of the text, never a paraphrase. Status, verdict, grade and locators are always shown in full — they are what a reader scans for.
- **Details layer.** `## Details: full text of every row (N)` holds every row's full text, keyed by id, in the same order as the scan layer above it.

Why: on the first real instance (3 documents, 163–234 rows) the v1.0 speedread was **longer** than `full` and "Check first" held 45–50% of all rows, so it did not prioritise. The scan layer is what a reader reads; `metrics` measures it separately.

Section order in `speedread`:

| # | Section | What it holds |
|---|---|---|
| 0 | `Fails its own rule (N)` | rows whose labels fail a declared promotion rule, with the rule id and reason (omitted when none) |
| 1 | `Check first (N)` | the table, capped at `check_first_max`; unanswered high-impact critiques collapsed |
| 2 | `Spot check (random, K of M established)` | a labelled random sample of established rows |
| 3 | `Eliminated (K)` / `Superseded (S)` | short, always visible |
| 4 | `More to check (X)` | the check-first overflow, still before Established |
| 5 | `Established (M)` | grouped by group |
| 6 | `Details: full text of every row (T)` | every row, full text |

`Eliminated` and `Superseded` sit above `More to check` on purpose: they are short and high-value, and the overflow can be long. Everything above `Established` is still verification-first material.

**Collapse.** Unanswered high-impact critiques that share a (target link, verdict, lens) become one scan line — `3 unanswered **damaging** critiques · lens X · target Y · ids `a`, `b`, `c`` — ordered most severe first (the order of `high_impact.verdict`). Every id is named on the line and every full critique is in Details, so nothing is hidden; a wall of open critiques just stops crowding out everything else.

**Inline rule re-check (v1.2).** Every row's labels are re-checked against the policy's `rules`, and `rule_check` is `pass`, `fail` or `not_checked` (no rule's `when` matched). Rows that fail come **first**, under `## Fails its own rule (N)`, one scan line each with the failing rule id and a short reason (`status confirmed but grade agent`), and every line of a failing row anywhere in the rendering carries the bold marker `**RULE FAIL: <rule id>**`. Rows that pass carry no marker, so failures stand out. A failing row is still counted once in its own bucket and `Rule fails K` is reported separately in the header, like the spot check; `full` stays the plain baseline and never marks. It exists because six reviewers caught 0 of 12 relabelling errors (a status upgraded past its evidence, a verdict softened or marked answered): nothing in the text contradicts a false label, so no layout can surface it, but a mechanical re-check can.

**What the marker does not mean.** It checks a row's *labels* against the *declared* rules. It is not the datastore gate and it does not prove the underlying evidence: a row can pass every rule and still be wrong, and a fail can mean the rule is too strict for this instance. **Rules never apply to rows in the eliminated or superseded classes** — a ruled-out claim is not expected to satisfy a promotion rule — so those rows are always `not_checked` and are never marked.

**Spot check.** `spot_check` draws a deterministic random sample of *established* rows and shows it right after Check first, labelled as a random sample. Those rows stay in `Established` as well: it is a sample, not a move. It exists because planted errors that land in rows the policy calls established moved *later* in the v1.0 speedread — the hidden-risk case.

## Row contract

```
rows.json = {"title": str, "question": str|null, "groups": [str, ...] (display order), "rows": [row, ...],
             "seeded_test_copy": bool (optional; only plant.py sets it)}
row = {"id": str (unique; no whitespace, backtick, pipe or backslash), "group": str (one of groups),
       "kind": "claim"|"conclusion"|"critique"|"authority"|"citation"|"unit",
       "text": str, "status": str|null, "grade": str|null, "verdict": str|null, "answered": bool|null,
       "locators": [str], "lens": str|null, "reasoning_type": str|null, "direction": str|null,
       "links": [str] (ids of related rows), "meta": {} (free; meta.replaced_by names a superseding row)}
```

`id`, `group`, `kind` and `text` are required; other row keys default to null, `[]` or `{}`. Unknown keys are rejected (`RowsError`).

## Policy JSON

```
{"needs_check": {"status": [...], "grade": [...], "verdict": [...]},
 "eliminated":  {"status": [...]},
 "superseded":  {"status": [...]},
 "established": {"status": [...], "grade": [...], "verdict": [...]},
 "high_impact": {"verdict": [...]},          # also the severity order: most severe first
 "locator_patterns": [regex, ...], "negations": [word or phrase, ...],
 "check_first_max": 25,                      # cap on the Check first table; the rest go to More to check
 "spot_check": {"count": 5, "seed": null},   # null seed: a hash of the title
 "rules": [{"id": str, "when": {...}, "requires": {...}}],   # the promotion rules, re-checked inline
 "why_labels": {"<status|grade|verdict>:<value>" or "unanswered_high_impact" or "unclassified": "reason"}}
```

An unknown key, a wrong type, a regex that does not compile or matches the empty string, an empty negation or one over four words, a `check_first_max` below 1, a `spot_check.count` below 0, a bad `spot_check.seed`, or a value listed in two contradicting classes raises `PolicyError`. A missing key or sub-key takes `policy.DEFAULT`'s value (supply `[]` for none). Matching is case-insensitive.

- **`established.verdict`** marks a verdict that means "checked and good" — a `VERIFIED` citation is established, not unclassified. It may not also appear under `needs_check.verdict` or `high_impact.verdict`.
- **`negations`** extend a built-in English list (not, no, never, none, nor, neither, cannot, without, nothing, nobody, nowhere and the n't contractions). An entry may be a **phrase of up to four words** (`"no longer"`): it matches whole, case-insensitively, across any run of whitespace, and beats the single words inside it.
- **`spot_check.seed`** may be an int or a string. Leave it null for a per-title default, or set it per reviewer session.
- **`rules`** declares the instance's own promotion rules (default `[]`: the feature is off). Each rule is `{"id", "when", "requires"}`. `when` selects rows by `status`, `grade`, `verdict` or `kind` (a non-empty list of values) and `answered` (`true`/`false`); `requires` holds when `status`, `grade` or `verdict` is one of a non-empty list, `locators_min`/`links_min` (integers ≥ 0) are met, or `links_prefix` matches (a link starts with one of a non-empty list). All of `when` must match and all of `requires` must hold. An unknown field, an empty list, a non-integer minimum, a duplicate or unusable `id`, or an empty `when`/`requires` raises `PolicyError`.

```
{"id": "established_needs_source_grade", "when": {"status": ["confirmed", "verified"]},
 "requires": {"grade": ["page_image", "record_text"]}}
```

## Features and buckets (`features.py`)

Per row: `needs_check` and `needs_check_reasons`, `eliminated`, `superseded`, `replaced_by`, `established`, `high_impact`, `unanswered_high_impact` (answered is not true), `has_numbers`, `has_negation`, `locator_count`, `relevance` (share of the question's content words in the text, 0 to 1), `lens`, `bucket`, `rule_check`, `rule_fails` (failing rule ids) and `rule_reason`. `classify` adds `links_eliminated` and `rule_fail` (the failing rows, in input order). `check_rules(row, policy)` is the pure rule re-check; it needs no datastore, because every row already carries its status, grade, verdict, answered and links.

Buckets are exclusive, first match wins: **eliminated** > **superseded** > **check first** (any reason) > **established** (status, grade or verdict established). A row that matches no class gets the reason `unclassified` and is checked first: unknown standing is something to check. So the header counts always add up to the total.

Check-first order: unanswered high impact, then linked to an eliminated row, then number of reasons, then relevance, then input order. The scan layer then orders the unanswered high-impact block by verdict severity and leaves the rest in that order.

## Modes (`render.py`)

| Mode | What it shows |
|---|---|
| `full` | Baseline. Groups in given order, rows in input order, plain, one line per row: ``- `id` [status/verdict/grade; answered; lens L] text (locators)`` |
| `speedread` | Two layers, as above. Header `Check first N · More to check X · Eliminated K · Superseded S · Established M · Total T · Spot check A of B · Rule fails R`; the five buckets add up to T, the sample and the rule fails are counted apart. Bold numbers and dates, locators, negation words and phrases, status tokens |
| `claims` | The **one-layer** v1.0 speedread over claim and conclusion rows only; a header line names every row of other kinds it leaves out. Unchanged in v1.1: it is already a partial view |
| `uncertain_first` | The same two layers, plain: Needs check, Spot check, More to check, the other rows in input order, then Details |

Newlines inside values render as `<br>`; table cells backslash-escape `|` and `\`. `python render.py ROWS.json --mode speedread --policy P.json`.

## Consumer policies (`policy.py`)

| Consumer | Order and cut | Reports what it left out |
|---|---|---|
| `human_speedread(doc)` | The speedread partition | `excluded` is always `[]` |
| `hook_budget(doc, max_lines, max_chars)` | Eliminated, check first, superseded, established; the body is a prefix, so an eliminated or check-first row is never cut while an established row is kept; rows are never truncated (full text, not leads) | Last line `[EXCLUDED n of T rows over budget: ids (+k more)]` inside the budget; full list under `excluded`. A budget too small for the footer raises |
| `synthesis_context(doc, max_rows)` | Same priority; established by relevance | `excluded` ids |

## Evaluation kit

| Tool | Does |
|---|---|
| `plant.py TRUTH OUT --seed S --per-type K` | Seeded, deterministic errors: `status_upgrade`, `locator_shift`, `negation_drop`, `verdict_soften`, `number_change`. Targets alternate between rows that will render as established (the hard case) and rows that will not; `lands_in` records which. One error per row at most. Title prefix `[SEEDED TEST COPY] `, `seeded_test_copy: true`, and a manifest carrying the same banner. A shortfall is recorded and exits 1 |
| `lookup.py TRUTH LOG ROW_ID --budget N` | Prints the true row and logs it; exit 3 once N lookups are used; exit 4 for an unknown id (does not use budget); refuses a seeded copy as truth |
| `score.py MANIFEST FINDINGS [--log LOG]` | Caught (row and field match), missed, false alarms, wrong-field, duplicates, recall and precision, lookups used; split by mutation type and by `lands_in` |
| `metrics.py ROWS [--manifest M]` | Chars and lines, **scan chars and scan lines**, preservation (must be 100%), the first line of each check-first, planted and rule-fail row **measured over the scan layer**, visible within 10, 20, 40 lines, how many planted rows landed in the spot-check sample, `rule_check` counts with the rows each rule flagged, and (with a manifest) `planted_rule_flagged` by mutation type; exit 1 if any mode fails preservation |
| `demo_synthetic.py [--out DIR]` | Full vs speedread for the synthetic incident and the bigger design review, with a planted copy, metrics and a scan-layer-vs-full report |

### What preservation checks

After undoing presentation (bold markers, `<br>`, table escapes), the rendering must hold at least as many occurrences as the input of every **id**, **number**, **negation** word or phrase, **locator** and **full text**, compared as multisets — so a dropped row fails even when its tokens appear in other rows.

In a two-layer mode those five are counted over the **Details layer alone**. Counting the whole rendering would let a Details section drop a short row unnoticed, because a short row's lead is its whole text. Two further checks then cover the scan layer:

- `scan_ids` — every row is named in the scan layer, so nothing is hidden below the fold (a row inside a collapsed group is named on the group line);
- `scan_leads` — every row that has its own scan line shows a lead that is a verbatim prefix of its text, so emphasis cannot quietly eat a `not`.

The claims view is checked over claim and conclusion rows (its header names the rows it leaves out).

### Protocol: does speedread help a reviewer catch errors?

1. Export clean rows for one target (the truth). Keep them and every manifest away from reviewers.
2. For each reviewer session, run `plant.py` with a fresh seed. Arms: `full` (baseline) and `speedread`. Each reviewer does both arms on different seeds (and, where possible, different targets), with arm order alternated across reviewers.
3. Same time box and same lookup budget in both arms. The reviewer may run `lookup.py` against the truth rows; each session has its own log.
4. The reviewer writes findings as JSON `[{row_id, field, note}]`.
5. `score.py` per session. Report recall **on errors that landed in established rows** next to overall recall, false alarms next to catches, and lookups used. A speedread that only helps on rows it already flags has not shown anything.
6. `metrics.py` gives the mechanical side: where planted rows appear in the scan layer, how much text each arm is, and how many planted rows the spot check happened to sample (report that number — a catch inside the sample is not evidence that the ordering works).
7. False alarms are measured against planted errors only; read them, since some may be real errors in the clean rows.

## Where it plugs in

- **MCP view.** Read tools take `view` (`data` default, or a mode). The adapter's optional `present_rows(tool_name, result)` and `present_policy()` supply rows and policy; the envelope gains `rendered` and keeps `data` unchanged (`engine/mcp/README.md`).
- **Hook budget.** A context hook calls `hook_budget` so eliminated and unverified items reach an agent first and what was cut is named.
- **Synthesis context.** A writer's context is built with `synthesis_context`; excluded ids are logged with the output.
- **Lens policies.** A lens ships its own policy JSON (stricter vocabularies, its own reasons); `lens` is a feature, and a lens view is a policy plus a row filter, never a copy.

## Tests

```
python -m unittest discover -s engine/present -v
```

## Limits

- Preservation is a multiset of tokens plus verbatim text after undoing presentation; it cannot see a reordering inside one row's text (renderers never touch text, and tests cover emphasis).
- The lead is cut by a sentence heuristic: `.` after a short token (`p.`, `Tr.`, `Q.`) or inside one (`A.D.3d`) does not end a sentence, and a lowercase next word does not start one. It can still cut early on unusual text. It is only ever a prefix, so a bad cut costs context, never accuracy.
- A collapsed group line shows ids, not leads. The group's rows are read in Details.
- The spot check is a random sample, not a ranking: it says nothing about the rows it did not pick.
- Relevance is plain token overlap. The claims view is deliberately partial; its header names what it leaves out.
- The rule re-check compares a row's labels with the declared rules. It is not the datastore gate, it does not read sources, and it proves nothing about the evidence behind a row. A rule that matches nothing leaves every row `not_checked` and renders no section; a rule that matches everything floods the section. Eliminated and superseded rows are never checked and never marked.
- No reviewer study has been run yet; the kit is built, and v1.1 and v1.2 are measured only on the mechanical side.
