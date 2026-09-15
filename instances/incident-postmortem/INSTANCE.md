# Incident postmortem: establish root cause and contributing factors

**Status:** spec. **Examples are synthetic** (fictional outage of "checkout-api").

## 1. When to use it
After an outage, teams reconstruct events from logs, metrics, deploys, chat and pages. Postmortems often settle on the first plausible cause and forget what was ruled out. Cost of confident error: the outage recurs.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Root cause, contributing factors and effective remediations, blamelessly |
| 2 | Unit | Log line; metric sample window; deploy or config change; alert; chat message; runbook step |
| 3 | Fidelity | reproduction (the failure recreated in staging or by replay) > system record (log, metric, deploy record) > chat during the incident > recollection in the review > agent summary |
| 4 | Status | inside the incident window / outside / clock-skew uncertain |
| 5 | Vocabulary | The organization's postmortem template sections (impact, timeline, root cause, contributing factors, detection, response, action items) |
| 6 | Gates | "Root cause" needs reproduction or a system-record causal chain with no unexplained gap; ruled-out causes are recorded with evidence; an action item must link to the factor it addresses |
| 7 | Roles | Service owner; SRE; security; skeptic ("what else changed?") |
| 8 | Evaluator | Timeline consistency (ordering, clock skew); recurrence of the same failure (outcome); replay test |

## 3. Unit (synthetic)
```json
{"id":"deploy_checkout_2026_05_10_1402","type":"deploy",
 "locator":{"system":"deploys","id":"dep-7781","ts":"2026-05-10T14:02:11Z"},
 "text":"checkout-api v412: connection pool max 200 -> 50 (config)",
 "status":"in_window","grade":"system_record"}
```

## 4. Evidence model
- Chat claims ("DB was slow") are attestation until a metric or log confirms.
- Reproduction outranks everything.

## 5. Framework lens
Template sections plus a causal chain view (event, effect, effect).

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Root cause needs reproduction or a gapless system-record chain | "root cause: DB slowness" from chat | after pool exhaustion is reproduced with pool=50 under recorded load |
| Ruled-out causes kept | alternative cause deleted from the doc | ruled_out is always accepted and never deleted |
| Action item linked | "add alerting" with no factor | after linking it to "detection delay 19 min" |

## 7. Roles and lenses
- **Roles:** skeptic red team (other changes in the window); SRE cold bench; security (was it an attack?).
- **Lenses:** engineering (full); leadership (impact, cause, actions); customer communication (impact only).

## 8. Evaluator
Timeline ordering check; replay; recurrence tracking over months.

## 9. Pipeline walkthrough
Intake logs, metrics, deploys and chat for the window, normalize clocks, label events, build a causal chain, attempt reproduction, rule out alternatives, red team, then write the document with claim audit.

## 10. How you use it
- "What changed in the 30 minutes before errors rose?"
- "What was ruled out and why?"
- "Is the root cause reproduced?"

## 11. Worked example (synthetic)
1. Candidates: the pool config change (deploy dep-7781) and a DB failover at 14:05.
2. Failover is ruled out: errors began at 14:03 and the DB latency metric was flat until 14:06, recorded with evidence.
3. Replay at pool=50 reproduces exhaustion; the root cause is confirmed.
4. Skeptic: "traffic spike?" The metrics show normal load; the head is answered.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| First plausible cause | gate |
| Clock skew | status axis |
| Hindsight blame | blameless wording rule in the guide |
| Forgotten alternatives | ruled_out records |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** log, metric and deploy readers; clock normalization; replay harness.
