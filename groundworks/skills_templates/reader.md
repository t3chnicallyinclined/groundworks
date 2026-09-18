---
name: {{SKILL}}
description: Read a slice of the {{INSTANCE_ID}} corpus unit by unit, label every unit exactly once in the instance vocabulary with a verbatim quote for anything that carries weight, and propose only what the units actually support. Use for intake reading, relabelling, and any pass where coverage must be provable. Triggers on "read this slice", "label these units", "work through the corpus", "what does the record say about".
---
{{HEADER}}

# Read the {{INSTANCE_ID}} corpus

**What this instance is establishing:** {{GOAL}}

**For whom:** {{HOLDER}}. A unit is `for_goal` when it helps that holder's goal and `against_goal` when it hurts it; both are worth the same to you. Off limits: {{OFF_LIMITS}}.

Coverage is the point. A slice is done when every unit in it has exactly one label - not when the interesting ones do.

## Method

1. **Take the slice in order.** Read the unit's own text; do not skim to the units that look relevant.
2. **Label every unit exactly once.** A unit that says nothing useful gets tier 0 and moves on; that is a result, and the validator checks that nothing was skipped.
3. **A quote for anything above tier 1.** Copy it character for character from the unit text. The validator refuses a quote it cannot find in the unit.
4. **A reason in your own words** for anything above tier 1, naming what the unit shows and what it bears on.
5. **Status before substance.** A unit outside the usable statuses is labelled and left there. Never argue from a unit the status axis excludes; say it exists and that it cannot be used.
6. **Propose nothing you cannot cite.** `claim_propose` records a claim at `proposed`; only `claim_confirm` with citations to units of a confirming grade can promote it, and the gate, not you, decides.
7. **Record dead ends** with `claim_rule_out`, saying what was tried and what the source shows. Check `eliminated_claims` before proposing, so a dead claim is not revived.
8. **Report** units read, labels by tier, quotes checked, claims proposed, claims confirmed, claims ruled out, and anything you could not resolve.

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

- Leave a unit unlabelled, or label one twice.
- Paraphrase a quote, or quote across a unit boundary.
- Use a unit whose status is outside the usable set, however true it looks.
- Confirm a claim on an agent-grade source. Agent output never confirms.
