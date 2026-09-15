# Customer research: establish why users behave as they do

**Status:** spec. **Examples are synthetic** (fictional app "Tallyo").

## 1. When to use it
Product teams have interviews, support tickets, reviews and usage data. Decks tend to generalize from memorable quotes. Cost of confident error: building the wrong thing for the wrong segment.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Evidence-backed reasons for a behavior (for example churn), by segment |
| 2 | Unit | Interview statement (user id, segment); ticket; review; usage metric window |
| 3 | Fidelity | behavioral data (usage events) > verbatim statement with user id > note-taker paraphrase > team recollection > agent summary |
| 4 | Status | in study population / out of population / consent-restricted |
| 5 | Vocabulary | The research plan's questions and hypotheses; a jobs or needs framework the team uses |
| 6 | Gates | "Users want X" needs statements from multiple distinct users in the segment plus a behavioral signal or an explicit caveat; one quote is illustrative only |
| 7 | Roles | Skeptic (sampling bias); sales (revenue view); support |
| 8 | Evaluator | Distinct-user count check; segment balance check; outcome: whether shipped changes moved the metric |

## 3. Unit (synthetic)
```json
{"id":"int_U042_q7","type":"interview_statement",
 "locator":{"interview":"U042","segment":"small_business","minute":"12:40"},
 "text":"I stopped using it because exporting to my accountant took three steps every month.",
 "status":"in_population","grade":"verbatim"}
```

## 4. Evidence model
- Paraphrase cannot confirm; verbatim can.
- Behavior outranks statements when they conflict. The conflict itself is recorded as an event with stances.

## 5. Framework lens
Hypotheses as nodes. Matrix: hypothesis by segment by distinct users for and against, plus behavioral signal.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Multiple distinct users | "SMB churn driven by export friction" from U042 alone | after statements from several distinct SMB users are linked, plus export funnel drop-off data |
| Consent respected | consent-restricted quote in the shared deck lens | never accepted |

## 7. Roles and lenses
- **Roles:** skeptic red team (who was not interviewed); leadership cold bench.
- **Lenses:** by segment; executive summary; design team (verbatims with consent).

## 8. Evaluator
Distinct-user counts; balance; post-launch metric change.

## 9. Pipeline walkthrough
Intake transcripts, tickets and metrics, label statements by hypothesis and segment, link behavior, apply gates, red team, then write the deck with claim audit.

## 10. How you use it
- "Top churn reasons for SMB users, with counts and behavior evidence."
- "Which claims in this deck rest on one user?"

## 11. Worked example (synthetic)
1. The export friction hypothesis has distinct SMB users and a funnel drop-off at the export step, which meets the gate.
2. Skeptic: "only churned users interviewed". Verdict: damaging.
3. The answer adds retained-user interviews showing the same friction but a workaround; the claim is narrowed and marked ready.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Vivid single quotes | gate |
| Survivorship | skeptic |
| Paraphrase drift | ladder |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** transcript and ticket parsers with user ids; metric reader; consent status.
