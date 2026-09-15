# Threat intelligence: attribute and characterize an intrusion

**Status:** spec. **Examples are synthetic** (fictional intrusion set "GLASSHERON").

## 1. When to use it
Defenders collect logs, malware samples and third-party reports. They need what happened, how, and, carefully, who. Cost of confident error: misattribution, wrong response, or public embarrassment.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Characterize the intrusion (techniques, infrastructure, timeline) and assess attribution with stated confidence |
| 2 | Unit | Log event; indicator (hash, domain, IP) with first and last seen; malware function; report paragraph |
| 3 | Fidelity | captured artifact or log from own systems > reverse-engineered behavior (reproduced in a sandbox) > trusted partner report > vendor report > open-source post > agent summary. Source reliability and information credibility graded separately in the Admiralty style (reliability A to F, credibility 1 to 6) |
| 4 | Status | in scope (this intrusion's time and assets) / out of scope / shared-restricted (partner handling rules) |
| 5 | Vocabulary | A technique framework used by the team (for example MITRE ATT&CK tactics and techniques) and an intrusion model (for example the Diamond Model: adversary, capability, infrastructure, victim) |
| 6 | Gates | Attribution needs multiple independent categories of evidence (for example infrastructure plus capability), never one indicator; a technique mapping needs an observed event or reproduced behavior |
| 7 | Roles | Skeptic (false flags, shared tooling); incident commander; legal and communications |
| 8 | Evaluator | Planted shared-tool indicator must not attribute; timeline consistency check; later confirmation from takedowns or indictments (outcome) |

## 3. Unit (synthetic)
```json
{"id":"log_edr_2026_03_02_17_44_09","type":"log_event",
 "locator":{"source":"EDR host WS-114","ts":"2026-03-02T17:44:09Z","event":"proc_create"},
 "text":"rundll32.exe loading C:\\Users\\Public\\glass.dll, parent winword.exe",
 "status":"in_scope","grade":"own_capture"}
```

## 4. Evidence model
- Two axes on every source: reliability and credibility.
- Indicators decay: first and last seen are required, and stale indicators carry lower weight.

## 5. Framework lens
Technique framework nodes. Matrix: technique by observed, reproduced, and reported-only.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Attribution needs multiple evidence categories | "GLASSHERON is group X" from one shared domain | after capability overlap (reproduced) plus infrastructure pattern are linked |
| Technique mapping needs observation | "T-execution via rundll32" from a vendor blog only | after the EDR event is linked |
| Restricted material stays in its lens | partner-restricted indicator in a public report view | never accepted |

## 7. Roles and lenses
- **Roles:** skeptic red team (false flag, commodity malware); leadership cold bench; legal.
- **Lenses:** SOC (all in scope); executive (confidence statements); public disclosure (unrestricted only).

## 8. Evaluator
Planted shared-tool test; indicator freshness check; outcome comparison.

## 9. Pipeline walkthrough
Intake logs and samples, reproduce behavior in a sandbox, label events by technique, build a timeline of events, assemble diamond model nodes, assess attribution, red team, then report.

## 10. How you use it
- "Timeline of the intrusion with observed techniques only."
- "What would have to be true for this to be group X, and what contradicts it?"

## 11. Worked example (synthetic)
1. A shared domain alone tries to set attribution; the gate rejects it.
2. The reproduced loader behavior matches group X's known custom loader, and the infrastructure registration pattern matches. Attribution becomes a candidate at moderate confidence.
3. Skeptic: "loader code leaked publicly in 2025". Verdict: damaging.
4. The answer notes an unleaked variant feature, recorded with the reproduction. Confidence stays moderate, stated in the report.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Commodity tools | skeptic lens |
| Stale indicators | decay |
| Vendor naming collisions | alias edges |
| Report laundering (one source cited by many) | independence edges |

## 13. Module reuse
- **As-is:** core modules.
- **Adapters:** log parsers; sandbox reproduction runner; technique vocabulary; two-axis source grading.
