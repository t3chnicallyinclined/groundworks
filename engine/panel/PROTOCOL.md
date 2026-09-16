# Panel protocol

**Status:** built 2026-09-15; tests in `test_panel.py`. Module: `panel` (docs/MODULES.md). Adapted ideas: `NOTICE.md`.

## What a panel is for

A panel is how several lenses judge **one target** (a conclusion, an argument, a bridge, a draft section) without copying each other. Use it for red team, cold bench, and any lens verdict where agreement could be social rather than earned.

A panel never confirms anything. Its output is **lens-keyed overlay rows at agent grade** (critiques, verdicts, objections). The datastore gates still decide what is ready.

## The five rules and what enforces each

| # | Rule | Why | Enforced by the program (`panel.py`) | Left to the coordinator |
|---|---|---|---|---|
| 1 | **Blind first, then anonymized** | Seats that see each other early converge on the first confident voice | `anonymize` writes the bundle with every seat id, lens and alias replaced by `Member A/B/C`, in an order fixed by the lock hash; round-1 answers are hashed and any later edit fails `check` | Round-1 prompts contain no other seat's output |
| 2 | **Anti-conformity** | Seats drift toward the majority without a reason | A round-2 stance that differs from round 1 is refused unless a `FLAW:` line quotes the seat's **own round-1 words verbatim** (3+ words) | Sending the directive in the round-2 prompt |
| 3 | **Locked before answers** | Choosing weights or seats after seeing positions lets the coordinator steer | `lock` hashes `panel.json` and refuses if any answer exists or a lock exists; every later command refuses if the file changed | Choosing seats and the weighted seat on the merits |
| 4 | **Stance line, never inferred** | A tally built from prose impressions is not a count | Exactly one valid `STANCE:` line with an option from the locked list; otherwise a re-prompt is owed | Re-prompting only the named seat |
| 5 | **Forced disagreement** | Easy agreement hides the objection that matters | **Dissent quota:** N seats each contribute an objection category no other counted seat used. **Novelty:** each round-2 answer has a `NEW:` line (5+ words) not found in its round-1 answer. **Agreement check:** above 70% on one option, the two least confident majority seats owe a counterfactual file with objections. `tally` refuses until all pass | Judging whether objections differ in substance, not just in category |

**Result.** Confidence-weighted tally: base weight 1.0 (1.5 for the locked weighted seat) times confidence (high 1.0, med 0.75, low 0.5). The total uses base weights, so a hesitant panel raises its own bar. An option needs 2/3 of the total. Otherwise the result is a **split, returned to the human**; nobody writes a consensus. Dealbreaker dissents go in the minority report either way. Every run reports its enforcement counts.

## Run directory

```
runs/<panel_id>/
  panel.json            written by the coordinator, then locked
  panel.lock.json       sha256 of panel.json
  round1/<seat>.md      blind answers, saved verbatim
  round1_anon.md        what round-2 seats see
  anon_map.json         labels and round-1 hashes (coordinator only; never shown to seats)
  round2/<seat>.md      cross-examination answers, saved verbatim
  round2/<seat>.counterfactual.md   only when the agreement check names the seat
  counterfactual.json   which seats the agreement check chose
  enforcement.jsonl     every re-prompt owed, once per answer version
  tally.json            the result
  verdicts.jsonl        one row per seat, for the instance's write surface
```

## panel.json

```json
{"panel_id": "red_team_point_2",
 "target": "conclusion:point_2",
 "question": "Does point 2 survive the strongest attack from each seat's position?",
 "options": ["survives", "damaged", "fatal"],
 "objection_categories": ["record", "preservation", "standard_of_review", "remedy"],
 "weighted_seat": null,
 "seats": [
   {"id": "seat_opposing_counsel", "lens": "lens_opposing_counsel"},
   {"id": "seat_deferential_judge", "lens": "lens_deferential_judge"},
   {"id": "seat_skeptic", "lens": "lens_skeptic", "aliases": ["the skeptic"]}]}
```

**Leave `thresholds` out** unless a human has decided otherwise; the defaults are exactly consensus 2/3, agreement 0.70, dissent quota 2. Do not write a rounded consensus such as `0.6667`: with three equal seats it raises the bar from 2.0 to 2.0001, so two high-confidence seats (2.0) can never reach consensus.

**What consensus takes with three equal seats** (bar 2.0): three seats at any confidence of med or above, or two seats both at high confidence. Two seats at med (1.5) or high plus med (1.75) is a split, returned to the human. This is intended (a hesitant majority escalates), and it makes splits common in three-seat panels.

- At least 3 seats.
- Seat ids, lenses and aliases must be distinctive strings that never occur as ordinary words in answers; the anonymizer replaces every occurrence.
- `weighted_seat` is the one seat whose lens most directly owns the question, or `null` when that is not clear.
- Thresholds are human-owned (listed under `human_owned` in `instance.yaml`). The program refuses an agreement threshold of 1, which would switch the check off.

## Machine lines

Each on its own line, anywhere in the answer:

```
STANCE: <option|abstain> | CONFIDENCE: high|med|low | DEALBREAKER: yes|no     exactly one, both rounds
OBJECTION: <category> | <objection>                                             zero or more, both rounds
NEW: <claim, risk, test or reading not made in round 1>                         round 2, at least one
FLAW: "<exact words from your own round-1 answer>" | <why that was wrong>     round 2, required if stance changed
```

`DEALBREAKER: yes` means the seat considers the other options harmful, not merely weaker.

## Prompts

**Round 1** (one seat, run all seats in parallel):
```
You are seat {seat_id} on a panel, judging through the {lens} lens.
{lens charter: what this lens is for, what it habitually asks, what it tends to miss}

Question: {question}
Target: {target}
Evidence: {evidence packet from the lens view}. Cite evidence by locator. Use nothing outside the packet.

Answer on your own; you will not see other seats' answers in this round.
Write your analysis in 300 words or less, then the machine lines:
OBJECTION: <{categories}> | <objection>      (zero or more)
STANCE: <{options}|abstain> | CONFIDENCE: high|med|low | DEALBREAKER: yes|no
```

**Round 2** (one seat, all seats in parallel):
```
You are seat {seat_id} ({lens} lens), round 2.

Below are every seat's round-1 answers, labeled Member A, B, C. One of them is yours.
Judge by argument quality, not source. Do not guess who wrote what; refer to answers only by label.

{round1_anon.md}

Your own round-1 answer, for reference:
{round1/<seat>.md}

Anti-conformity rule. If your round-1 position was right, defend it. Do not change because others
disagree, because a majority is forming, or because a point is repeated. Change only for reasoning
that exposes a specific flaw in your earlier argument, and name that flaw by quoting your own
round-1 words. If you cannot name it, do not change.

In 300 words or less:
1. The answer you most disagree with, by label, engaging its specific claims.
2. The answer that most strengthens yours, by label, and how.
3. Your position now.
Then the machine lines:
NEW: <at least one>
OBJECTION: <{categories}> | <objection>      (zero or more)
FLAW: "<exact words from your round-1 answer>" | <why>      (only if your stance changed)
STANCE: <{options}|abstain> | CONFIDENCE: high|med|low | DEALBREAKER: yes|no
```

**Re-prompts** are printed by `check`, per seat and condition. Send each only to the seat it names.

## Sequence

1. Write `panel.json`; `lock`.
2. Round 1, all seats in parallel; save each answer verbatim to `round1/<seat>.md`.
3. `anonymize`.
4. Round 2, all seats in parallel; save verbatim to `round2/<seat>.md`.
5. `check`. Send each owed re-prompt to its seat; save the full revised answer over the old one; repeat.
   **Budget:** at most 2 re-prompts per seat per condition. If a check still fails, stop and report "protocol not satisfied" with the owed list. Never edit an answer and never loosen a threshold.
6. `tally`. Exit 0: write `verdicts.jsonl` rows through the instance's write surface as lens-keyed critiques. Exit 3: present each option with its weight and strongest argument, and the minority report, to the human.

## What the program cannot check (say so in reports)

- That round-1 prompts were really blind (the coordinator's discipline).
- That two objections in different categories differ in substance.
- That a `NEW:` line is new in meaning, not only in wording.
- Anonymization is strongest for peer panels (three judges). In adversary panels a seat's position can reveal its lens from content alone; the masking still removes names and order.
- The weighted tally measures the panel's view. It is advice, never evidence.

## Not adopted from the source

Historical-figure personas (a lens here is a charter with a copied frame, not a famous name); a third crystallization round (two rounds plus enforcement was enough to carry the five rules); a chairman model writing prose synthesis (the tally and verdict rows are the result); routing seats to other model providers (allowed only where the instance's privacy rules name those providers).
