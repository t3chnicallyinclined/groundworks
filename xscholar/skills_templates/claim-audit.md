---
name: {{SKILL}}
description: Verify every claim in a {{INSTANCE_ID}} draft before it is circulated - split it into claims, classify each one, check each against the located source text rather than memory, and give every claim a graded verdict recorded in the graph. Use before anything leaves the instance. Triggers on "cite check", "audit this section", "verify this draft", "is this quote right", "check the claims".
---
{{HEADER}}

# Claim audit in {{INSTANCE_ID}}

**What this instance is establishing:** {{GOAL}}

**Who relies on it:** {{HOLDER}}. Off limits: {{OFF_LIMITS}}.

Every sentence that asserts something is a claim. An audit that reports "looks right" has not been run. A claim you cannot check is `UNCHECKABLE`, and that is a verdict, not a pass.

## Method

1. **Split the draft into claims**, one per assertion. A sentence that asserts two things is two claims.
2. **Classify each claim**: `source_quote` (words presented as the source's own), `source_fact` (a fact attributed to the record), `authority` (a rule or reference asserted to exist and say something), `derived` (a conclusion drawn from other claims), `interpretive` (the author's reading).
3. **Give each a salience**: load-bearing, supporting, or colour. Audit load-bearing claims first and never stop before they are done.
4. **Check against the located text**, always through `unit_get` or `unit_search`, never from memory or from the draft's own earlier sentences. A quote matches only if it matches character for character inside a unit whose status is usable.
5. **Verdicts:** `VERIFIED`, `MISMATCH` (the source says something different), `NOT_FOUND` (the locator does not resolve), `OUTSIDE_RECORD` (asserted but supported by nothing in the corpus), `MISATTRIBUTED` (real, but not from the cited place), `INTERPOLATED` (words added to a quote), `UNCHECKABLE` (no usable source exists).
6. **Record every verdict**, including the clean ones, so the count reconciles: claims checked, by verdict, plus the claims deferred and why.
7. **Check the graph's own drift** with `health` and `claim_explain` on anything the draft treats as established. A claim confirmed without a valid citation is a finding in its own right.
8. **Report** the counts first, then every non-`VERIFIED` claim with the exact source text beside the draft text.

## Your seats

{{SEATS}}

## The panel

{{PANEL}}

## The vocabulary

{{VOCABULARY}}

## The readiness gates

{{GATES}}

## Tools

{{TOOLS}}

## Privacy

{{PRIVACY}}

## Never

- Verify a quote from the draft, a summary, a previous audit, or your own memory.
- Mark a claim `VERIFIED` because a citation exists; existence is not verification.
- Skip a colour claim silently. Report what was not audited and why.
- Repair the draft. You report verdicts; the author decides.
