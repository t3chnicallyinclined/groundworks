# Fiction and lore: build a consistent world from a canon

**Status:** spec. Lineage: the Forgily Book Scholar (phase curriculum and quality gates over thousands of books). **Examples are synthetic** (fictional saga "The Ember Crown").

## 1. When to use it
Writers, game studios and adaptation teams need a world bible that is true to canon: characters, places, powers, timelines, relationships, with contradictions surfaced rather than smoothed over. Cost of confident error: continuity breaks and angry readers, or generated content that contradicts canon.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | A canon-accurate world model with contradictions and open questions recorded |
| 2 | Unit | Passage (book, chapter, paragraph); glossary entry; author statement |
| 3 | Fidelity | primary text passage > author's published statement > official companion material > fan wiki > agent summary |
| 4 | Status | canon tier (primary / secondary / non-canon) as the project defines it |
| 5 | Vocabulary | Entity types the project needs (character, place, faction, power system, event) and relationship types |
| 6 | Gates | A canon fact needs a primary passage; a power system rule needs both its capability and its limit cited; a contradiction between primary passages is recorded as an event with both mentions, never resolved silently |
| 7 | Roles | Continuity editor; lore skeptic; adaptation writer |
| 8 | Evaluator | Quote validator; contradiction detection on planted inconsistencies; entity dedup test (aliases) |

## 3. Unit (synthetic)
```json
{"id":"ember_b2_c14_p9","type":"passage",
 "locator":{"book":2,"chapter":14,"para":9},
 "text":"Only those born under the red comet can wake the crown, and waking it burns a year from their life.",
 "status":"primary","grade":"primary_text"}
```

## 4. Evidence model
- Alias resolution happens at extraction time, as the Book Scholar found.
- Wikis point; they never confirm.

## 5. Framework lens
Entity types and the power system rules. Matrix: rule by capability citation, limit citation, and contradictions.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Canon fact needs a primary passage | "the crown can be woken by anyone of royal blood" from a wiki | never confirmed; the primary passage b2 c14 p9 contradicts it and is linked instead |
| Rule needs capability and limit | "crown grants command of fire" with no cost cited | after the limit passage is linked |
| Contradictions recorded | book 4 says "born under any comet"; silently overwritten | after the event "comet requirement" records both mentions |

## 7. Roles and lenses
- **Roles:** skeptic red team (translations, retcons); continuity editor cold bench.
- **Lenses:** writers' bible (primary canon); game design (rules with limits); open contradictions.

## 8. Evaluator
Planted contradictions; alias dedup; quote validator.

## 9. Pipeline walkthrough
Phase curriculum: survey, entities, visual descriptions, relationships, power systems, events, themes. Each phase is validator-gated, then load, then contradiction events, then the bible with claim audit.

## 10. How you use it
- "Everything canon about the Ember Crown, with limits and contradictions."
- "Is this generated scene consistent with canon?"

## 11. Worked example (synthetic)
1. A rule is confirmed from b2 c14 p9.
2. Book 4 contradicts it, so the event records both mentions, and the lore skeptic finds an author statement calling book 4's line a character's error.
3. The answer is recorded, the rule stands, and the contradiction is kept with its resolution.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Aliases splitting entities | extraction-time dedup |
| Wiki errors | ladder |
| Unreliable narrators | the mentions carry the speaker |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** book parser with chapter and paragraph locators; alias resolver.
