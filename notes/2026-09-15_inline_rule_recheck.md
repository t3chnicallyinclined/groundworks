# 2026-09-15: inline rule re-check (C18), measured on the same three documents

Generic; no matter content. Module: `groundworks/present`. Roadmap C18, following C17
(`notes/2026-09-15_reading_layer_evaluation.md`).

## Why
C17 measured six reviewer agents on three real documents: **0 of 12 relabelling errors caught** (a status
upgraded to an established value, a verdict softened or marked answered), in both the `full` and the
`speedread` arm. Nothing in a row's text contradicts a false label, so no layout can surface it. The fix is
mechanical, not presentational: re-run the instance's own promotion rule over every row and show the result
inline.

## What was built
- **Policy key `rules`** (default `[]`, feature off). Each rule is `{"id", "when", "requires"}`. `when`
  selects on status, grade, verdict, kind and answered; `requires` holds on status, grade, verdict,
  `locators_min`, `links_min` or `links_prefix`. Unknown fields and empty lists raise `PolicyError`.
- **`features.check_rules(row, policy)`** — a pure function of row plus policy, no datastore: every row
  already carries the labels and links a rule reads. Each row gets `rule_check` (`pass`, `fail`,
  `not_checked`), `rule_fails` (rule ids) and a short `rule_reason` built from the rule
  (`status confirmed but grade agent`).
- **Rendering** in every mode except `full`: failing rows come first under `## Fails its own rule (N)` with
  the rule id and reason, and every line of a failing row carries the bold `RULE FAIL: <id>` marker. Rows
  that pass are quiet. A failing row stays counted once in its own bucket; the header reports `Rule fails K`
  apart, like the spot check.
- **Exemption:** rules never apply to eliminated or superseded rows. A ruled-out claim is not expected to
  satisfy a promotion rule.
- **Metrics:** `rule_check` counts with the rows each rule flagged, first scan line of each rule-fail row,
  and `planted_rule_flagged` by mutation type.

## Result on the three C17 documents (234, 226 and 163 rows; 5 planted errors each)
Three rules declared, in that instance's own vocabulary: established statuses need a source grade,
a confirmed row needs a locator, an answered attack should link its answer.

| Mutation type | planted | flagged | flagged **because of** the plant |
|---|---|---|---|
| status_upgrade | 3 | 3 | **3** |
| verdict_soften | 3 | 3 | **1** |
| negation_drop | 3 | 2 | 0 |
| number_change | 3 | 2 | 0 |
| locator_shift | 3 | 0 | 0 |

The two numbers differ and the second is the real one: a row can already fail a standing rule before
anything is planted in it. Both verdict softenings that changed a verdict *value* sat on rows that were
already flagged; the one that flipped `answered` false to true was newly flagged. All three status upgrades
were newly flagged. Preservation stayed 100% in every mode with rules on.

## The unflattering number
Precision is entirely a property of the declared rules, not of the mechanism.

| Rule | clean rows flagged | planted |
|---|---|---|
| established needs a locator | 0 / 0 / 0 | 1 / 1 / 1 (exactly the planted upgrade) |
| established needs a source grade | 26 / 22 / 13 | +1 / +1 / +1 |
| an answered attack links its answer | 61 / 64 / 37 | +0 / +1 / +0 |

The section holds 87, 86 and 50 of 234, 226 and 163 rows (31 to 38%), because this instance records no
answer links at all and its verified authorities carry no source grade. A rule the instance cannot satisfy
yet is a standing finding, not a signal: it flags a third of the document every time. A rule that follows
the instance's own promotion path is precise.

## What this changes
- C18 is built and measured. The relabelling classes are now reachable without a lookup, which C17 said no
  layout could do.
- Adoption of the speedread view still needs a reviewer re-run: this is the mechanical side only.
- An instance that wants the answered-link rule to carry signal must record answer links (or drop the rule);
  the counts above are the cost of leaving it declared.
- A rule re-check is not the datastore gate. It compares labels with declared rules and says nothing about
  whether the evidence behind a row is real.

## Tuned rules on the same three documents (2026-09-15, later the same day)

The first rule set flagged 31 to 38% of every document, because two rules asked for things this instance does not record: an answered attack linking its answer (no such links exist) and a grade on authorities (authorities carry no grade). One planted error inside 88 flags is not findable, so the rules were narrowed to what the instance can actually satisfy:

```json
[{"id": "claim_confirmed_needs_source_grade",
  "when": {"status": ["confirmed"], "kind": ["claim"]},
  "requires": {"grade": ["page_image", "record_text"]}},
 {"id": "established_needs_locator",
  "when": {"status": ["confirmed", "verified"]},
  "requires": {"locators_min": 1}}]
```

| Document | Rows | Rule fails, clean | Rule fails, planted | Planted flagged |
|---|---|---|---|---|
| 1 | 234 | **0** | **1** | the status upgrade |
| 2 | 226 | **0** | **1** | the status upgrade |
| 3 | 163 | **0** | **1** | the status upgrade |

Preservation stays 100% in every mode; the scan layer is 40 to 59 KB against 139 to 185 KB for the full rendering.

**What this shows.** The mechanism is only as good as the rules an instance can honestly declare. A rule the data cannot satisfy is noise that buries the signal; a rule the data does satisfy turns an invisible relabelling into one flagged row at the top of the document, with no lookups spent.

**Rule for instances:** declare a rule only where the corpus records what the rule asks for. Where it does not, the honest move is to record the missing link (here, attaching answers to the attacks they answer) rather than to keep a rule that fires everywhere.

**Still open at this point:** whether a reviewer, reading the flagged document, actually catches the relabelled row. The three C17 documents were re-rendered with the tuned rules and given to fresh reviewers under the same 8-lookup budget; results are recorded below when they land.

## Reviewer round on the tuned documents (three arms, same planted errors)

Three fresh reviewers, one per document, same 8-lookup budget as the earlier arms.

| Arm | Caught | Findings | False alarms | Lookups |
|---|---|---|---|---|
| Plain baseline | 4 / 15 | 7 | 3 | 24 |
| Rendered, v1.0 (no rules) | 3 / 15 | 5 | 2 | 24 |
| **Rendered, v1.1 + tuned rules** | **5 / 15** | 5 | **0** | 22 |

By error type (caught / planted):

| Type | Plain | Rendered v1.0 | Rendered + rules |
|---|---|---|---|
| locator_shift | 1/3 | 1/3 | **2/3** |
| negation_drop | 1/3 | 1/3 | 1/3 |
| number_change | 2/3 | 1/3 | 1/3 |
| **status_upgrade** | 0/3 | 0/3 | **0/3** |
| **verdict_soften** | 0/3 | 0/3 | **1/3** |

### The finding that matters

**The rule marker flagged all three status upgrades, at the top of the document, and not one reviewer reported them.** Precision improved (0 false alarms, every finding real) but the flagged rows were skipped. The single relabel that was caught came from a different affordance: a reviewer reconciled the document's stated count of unanswered heads against the rows actually shown, found a mismatch of one, and spent a lookup to isolate it.

Two lessons for the design:

1. **A flag must say what changed and what to do.** "Fails its own rule" reads as housekeeping, and housekeeping is what a reader skips. The marker has to state the claim's label, what the evidence supports, and the one action that resolves it ("this claim says confirmed but its best source is an agent report: re-read the source or lower the status"). Flag wording is now a measurable variable, not a detail.
2. **Counts that reconcile are a real verification tool.** Two reviewers in different arms independently used section counts to find a relabelled row. Counts are cheap, domain-free, and they work where prose does not. Give every section a count the reader can add up, and state the derived totals the document claims.

### Caveats
One reviewer per cell, one model, a different document per reviewer, and agents rather than humans. The uniform result (status upgrades 0 of 9 across all three arms) is the reliable signal; the small differences between arms are not.
