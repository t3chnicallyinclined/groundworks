# Reverse engineering: establish how a binary works

**Status:** pattern proven in a private project (a reverse-engineering knowledge graph with ranked evidence, datastore gate, dead ends, outcomes on edges, lookup hooks). **Examples are synthetic** (a fictional console game binary).

## 1. When to use it
A team is reconstructing a program's behavior from disassembly, emulator traces and memory captures. The same questions get re-answered for months, plans get recorded as facts, and dead ends get re-walked. Cost of confident error: weeks lost on a false premise.

## 2. The eight answers
| # | Question | Answer |
|---|---|---|
| 1 | Goal | Correct, reproducible understanding of routines, data structures and state machines |
| 2 | Unit | Routine at an address; data field at an offset; trace capture; memory dump region |
| 3 | Fidelity | reproduction (frozen input plus pass/fail differ) > code (disassembly or decompilation at a PC) > metric (a measured number from a live session) > recurrence (stopped happening after a change) > attestation (a doc or memory) |
| 4 | Status | current (binary version matches) / superseded / applies to another build |
| 5 | Vocabulary | The project's subsystem map (render, input, state, audio) and known structure names |
| 6 | Gates | Confirmed needs reproduction or code; a locator (mentions a PC) is not a citation (shows it at the PC); bulk ingest never writes findings |
| 7 | Roles | Senior generalist (how would we disprove this?); domain experts per subsystem |
| 8 | Evaluator | Deterministic byte-exact gates on frozen inputs; probe tests for the promotion gate; drift report (confirmed facts that would fail the gate today) |

## 3. Unit (synthetic)
```json
{"id":"routine_8c1a2f40","type":"routine",
 "locator":{"binary":"game_v1.02","pc":"0x8C1A2F40"},
 "text":"loads slot table pointer from 0x8C2895E0, iterates count at +0x08",
 "status":"current","grade":"code"}
```

## 4. Evidence model
The key discriminator: **frozen input plus pass/fail is a reproduction; a census over a live session is a metric.** Outcomes of attempts live on edges: effective, ineffective, masks_only (right output, mechanism not understood), unproven.

## 5. Framework lens
The subsystem map. Matrix: subsystem by confirmed, inferred, ruled out, and superseded.

## 6. Gates (with probes)
| Rule | Rejected | Accepted |
|---|---|---|
| Confirmed needs code or reproduction | "renderer iterates 6 structs" confirmed on a doc note | after a differ run with frozen input passes |
| Masks-only is not a fix | attempt outcome "effective" for a tuned constant | recorded as masks_only |
| Bulk ingest never writes findings | doc scrape writing findings | lands as docnote artifacts |

## 7. Roles and lenses
- **Roles:** skeptic generalist; per-subsystem experts; the emulator author's view.
- **Lenses:** by subsystem; by address; dead ends.

## 8. Evaluator
Byte-exact gates; probe suites; drift audit.

## 9. Pipeline walkthrough
Intake disassembly and symbol tables, run traces, label routines and fields, promote through gates, record attempts with outcomes, and let hooks inject knowledge when an address is named.

## 10. How you use it
- "What is known about 0x8C2895E0, including what was ruled out?"
- "Which confirmed findings rest only on a locator?"

## 11. Worked example (synthetic)
1. A hypothesis says the renderer walks all character structs.
2. A differ run on frozen input shows it renders only nodes present in the slot table. The hypothesis is ruled out; the replacement is confirmed with reproduction.
3. The hook surfaces both whenever the address is named.

## 12. Domain pitfalls
| Pitfall | Caught by |
|---|---|
| Plans becoming facts | gate |
| Checks that pass by default | probes that must go red |
| Loose pattern counts | strict re-measurement |
| Same code, different seed, different verdict | scope on the verdict edge |

## 13. Module reuse
- **As-is:** core modules and hooks.
- **Adapters:** disassembly, trace and dump readers; differ harness.
