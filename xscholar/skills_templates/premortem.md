---
name: {{SKILL}}
description: Assume the {{INSTANCE_ID}} instance already failed, and work backwards to find where it actually lost - by failure category, with severity, likelihood and a mitigation for each scenario, recorded as critiques. Use before committing to a lead argument or a lead hypothesis, and after any major discovery in the record. Triggers on "premortem", "how do we lose this", "what goes wrong", "pre-mortem this plan", "stress-test the whole thing".
---
{{HEADER}}

# Premortem for {{INSTANCE_ID}}

**What this instance is establishing:** {{GOAL}}

**Whose loss it is:** {{HOLDER}}. Off limits: {{OFF_LIMITS}}.

A premortem is not a risk list. You are told the outcome - it failed - and asked for the story of how. A scenario with no mechanism is not a scenario.

## Method

1. **Build the strongest version of the current plan first**, in five sentences: what is being established, on what evidence, through which conclusions, and what the decider is being asked to do.
2. **Declare it lost.** Not close, not split - lost.
3. **Work through the failure categories** below, in order. For each, write the scenarios that would produce that loss, each with: the mechanism, the evidence that would have to be missing or wrong, severity, likelihood (low, medium, high), and the one action that removes or reduces it.
4. **Check each scenario against the record** before ranking it. A scenario whose premise the record already contradicts is closed - say so and close it, with the locator. A scenario the record neither supports nor contradicts is open and ranks higher, not lower.
5. **Check the dead ends** with `eliminated_claims` so a closed scenario is not re-opened.
6. **Rank** every open scenario by severity times likelihood and record each as a critique under the lens that owns it.
7. **Report** the ranked list, the closed scenarios with what closed them, and the single change that removes the most weight.

### Failure categories

| Category | The question |
|---|---|
| Evidence | What is this resting on that will not hold: a unit whose status or grade cannot confirm, a citation that does not say what it is cited for, a gap nobody noticed? |
| Alternative explanation | What other account fits the same record at least as well, and what would distinguish them? |
| Reasoning | Which step is an inference dressed as a finding, and what carries the weight if it is removed? |
| Coverage | What part of the corpus was never read, sampled thinly, or excluded, and could the answer live there? |
| Process | What could be refused for how it was done rather than what it says - an unmet gate, an unanswered damaging critique, a missing step? |
| Presentation | Where would the decider stop reading, misread a number, or take a claim as broader than it is? |

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

- List a risk without a mechanism and a locator, or with no action attached.
- Rank by how uncomfortable a scenario is rather than by severity times likelihood.
- Close a scenario on your own judgement; close it on the record, and name the unit.
- Treat the premortem as evidence. Gates decide readiness.
