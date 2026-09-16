---
name: {{SKILL}}
description: Attack a conclusion, claim or draft section in the {{INSTANCE_ID}} instance the way each seated lens would, keeping only the heads of attack that survive a one-sentence reply, and record each survivor as a lens-keyed critique. Use before a conclusion is relied on, before it is written up, and after any new evidence lands. Triggers on "red team this", "attack this", "what survives reply", "stress-test this point", "what will the other side say".
---
{{HEADER}}

# Red team a conclusion in {{INSTANCE_ID}}

**What this instance is establishing:** {{GOAL}}

**For whom:** {{HOLDER}}. "For" and "against" mean for and against that holder's goal; a unit that helps the holder is `for_goal`, one that hurts is `against_goal`. Off limits: {{OFF_LIMITS}}.

Your job is to find the attacks that survive, not to win. A head of attack that dies to one sentence of reply is noise and must not be recorded. A head that survives is recorded even when it is inconvenient.

## Method

1. **Read the target through each of your seats' lens views** (`lens_view`), never from memory. Cite every unit by locator.
2. **Build the strongest version of the target first.** Say in one sentence what it would take for the target to be right. If you cannot state that, you cannot attack it yet.
3. **One head of attack at a time.** For each: name it, state it in one sentence, say which evidence it turns on, then write the best one-sentence reply. Keep it only if the reply fails.
4. **Check the dead ends first** with `eliminated_claims`. An attack that was already investigated and found false is not an attack; repeating one is the error this instance most wants to stop.
5. **Record each survivor** with `critique_add` under the lens that found it, with a verdict from the instance vocabulary (`fatal`, `damaging`, `survivable`). Nothing else you write is evidence.
6. **Report** the surviving heads in order of severity, the ones that died and the reply that killed them, and what you could not check.

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

- Record a head of attack you have not tried to answer.
- Treat a panel verdict, your own critique, or any lens output as evidence. Gates decide readiness.
- Soften a verdict because the target is the holder's own conclusion.
- Quote source text you did not retrieve through a tool in this session.
