---
name: {{SKILL}}
description: Read a draft section, conclusion or argument from the {{INSTANCE_ID}} instance the way its decider would read it cold, with no prior briefing, and report how it actually lands - what it appears to be about, where it confuses, what is assumed but not shown in the record, and the questions it invites. Use on any draft before it is final. Triggers on "cold read", "how will this land", "first impression", "read this cold", "moot this section".
---
{{HEADER}}

# Cold bench read in {{INSTANCE_ID}}

**What this instance is establishing:** {{GOAL}}

**Who decides:** {{HOLDER}}. You are reading as that decider, cold, for the first time, with no goodwill and no memory of the work behind the draft. Off limits: {{OFF_LIMITS}}.

A cold read is not a critique. It reports how the draft lands, including where it lands well. It is worthless if you use anything you know that the reader would not have.

## Method

1. **Read once, straight through, at reading speed.** Do not look anything up on the first pass.
2. **Write what you took the section to be about**, in one sentence, before checking whether that is what it says.
3. **Mark every place you stopped:** a term you had to hold, a step you could not follow, a number that did not reconcile, a claim you wanted a citation for.
4. **Separate what is shown from what is assumed.** For each load-bearing claim, say whether the draft points you at the record (a locator you could open) or asks you to take it. Check the ones it points at with `unit_get` on the **second** pass only.
5. **Give a confidence** - low, medium or high - that you would rely on this section as written, and say what would move it up one step.
6. **Write the questions you would ask** the author, in the order they occurred to you.
7. Where a seat is asked to record its read, use `critique_add` under that seat's lens. A cold read records concerns, never confirmations.

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

- Repair the draft in your head and then report that it reads well.
- Use knowledge from outside the draft on the first pass.
- Report a confusion without saying which sentence caused it.
- Treat your read as evidence. Gates decide readiness.
