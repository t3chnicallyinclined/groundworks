# Mapping the SUPERGUN family of projects onto the Scholar

**Date:** 2026-09-15. **Author:** scholar-architect. **Status:** design note, nothing built.
**Scope:** how the owner's expert knowledge across SUPERGUN, the two reverse-engineering lanes, the
D3D11 capture / WebGPU replayer, MapleCast (retired) and RETRO RECEIPTS becomes self-updating project
experts on this engine.

**Rule for this file (CLAUDE.md 1).** This repo holds no matter data. Everything below is design and
measurement *about* an instance that will live in its own project; no finding, no game data and no
source text from those projects is copied here.

Every claim is tagged CONFIRMED (read in a file I opened, or measured by a command I ran) / INFERRED
(reasoned from confirmed facts) / UNKNOWN. The engine was read at v0.1.0b1. I did not run the test
suite and did not touch ports 8001/8002/8003 (CLAUDE.md rule 10), so every re_kb number below comes
from the seed files and Python sources on disk, never from the live database.

---

## 0. What I measured today

| # | Measurement | Value | How |
|---|---|---|---|
| M1 | supergun worktrees | **33** checkouts on **33 distinct branches**, all sharing one object store `C:/Users/trist/projects/supergun/.git` and one origin | `git -C <d> rev-parse --abbrev-ref HEAD` / `--git-common-dir` over `projects/supergun*` |
| M2 | worktree churn | **26 of 33** have a commit dated 2026-09-14 or 2026-09-15 | same listing |
| M3 | the rc3 baseline | `integrate/rc3` = `1a5b0a2`; **122** `.rs` files, **4,095** `fn` definitions, **97,314** LOC, **153** tracked `.md` | `git ls-files` + `grep -cE '^\s*(pub )?(async )?(unsafe )?fn '` in `supergun-integrate-rc3` |
| M4 | doc section density | 337 tracked `.md` across supergun / maplecast-flycast / mvc-live-skins-quarters / RetroReceipts-agent hold **4,693** H2–H4 headings = **13.9 sections per file** | `grep -cE '^#{2,4} '` per tracked file |
| M5 | MapleCast repo vs product | `maplecast-flycast` has **101 commits since 2026-08-29**, but **322 of ~375** path touches are under `tools/` (re_kb seeds); **69 of 93** tracked `docs/**.md` were last touched **before** 2026-08-29 | `git log --since --name-only`; per-file `git log -1 --format=%cs` |
| M6 | re_kb ladder drift | **301** explicit `strength=` literals across the 321 seed files; **21 (7.0%)** name a grade absent from `ladder.py`'s `ORDER`: `runtime` 6, `gate` 5, `measurement` 4, `doc` 3, `decompile` 2, `bytes` 1 | `grep -rhoE "strength\s*[:=]\s*'[a-z_]+'" *.surql \| sort \| uniq -c` |
| M7 | template config does not load | `python -m groundworks.config instances/_template/instance.yaml` raises `YamlError` on a flow collection; `instances/demo-incident/instance.yaml` loads: `OK demo_incident: 3 unit type(s), 2 gate rule(s)` | ran both |

All CONFIRMED. M4 extrapolated to the stated 734 unique docs gives **≈10,200 doc-section units**
(INFERRED — the four sampled repos may not represent the other lanes).

---

## 1. One instance or several? — **TWO**

### The decision

| Instance | Goal, in one line | What is in it |
|---|---|---|
| **A. `supergun`** — *the machine* | Establish how the Collection's own frame function behaves and what the shell around it must do, so every shipped target produces byte-identical frames at the stated latency | SUPERGUN Rust shell (all 33 worktrees, one repo), the wasm lift, Pi 4 / A7S / METAL targets, release + installer, **Steam RE** (Ghidra on `mvc_dump.bin`, the x64 recompile, the arena carve), **MvC2 SH4/NAOMI RE** (`marvelous2`, DC/NAOMI layout), **the d3dcap pixel gate**, and MapleCast **as archive** |
| **B. `retro_receipts`** — *the money and the receipt* | Establish that the money engine never leaks, strands or double-pays, and that a receipt replays what happened | RetroReceipts-agent, RetroReceipts-server, the ledger/wager/rail flows, the tape/replay **product** (not its pixel gate) |

Not one. Not six.

### Why not one

CONFIRMED: `config.py:305` requires a single `instance.goal`, and `docs/METHOD.md:106` defines
`direction` (for_goal / against_goal) **relative to one holder**. "Make the shell byte-exact" and
"never double-pay a wager" have different holders; in one graph "against the goal" stops meaning
anything and the adversary lens degenerates. `docs/ARCHITECTURE.md:85` states the rule outright:
one instance, one goal, one corpus, one set of rules.

### Why not six (one per lane) — the decisive argument

The owner named the hard case: netcode rests on Steam RE facts, and **a retraction must cascade**.

CONFIRMED: the engine's cascade is `groundworks/queue.py` — a change-feed worker whose
`stale_recheck` handler demotes a `confirmed` fact when the unit it cites loses its confirming grade
or usable status (`queue.py:400-431`), plus `groundworks/patterns/tms_propagation.surql`. The `Worker`
holds **one** `Db`, and a `Db` is bound to one namespace/database (`queue.py` main, `--ns/--db`).
There is **no cross-instance propagation anywhere in the engine**: `grep -rn "bridge"` over
`groundworks/**.py` returns nothing but a mention in `panel/PROTOCOL.md:7`, and `docs/MODULES.md:55`
marks the `discovery` module (which owns bridges) as **"designed"**, not built.

So: split netcode from Steam RE and retracting an arena-carve fact leaves the netcode claim sitting
at `confirmed` forever. **A seam must never cut a dependency that has to cascade.** That single
constraint collapses five candidate lanes into instance A.

### Where exactly the seam is

Not "RE vs application code" — that cut severs the netcode/Steam-RE dependency. Not "one repo, one
instance" — the files are in the wrong places for that.

**The seam is the artifact under authority: what does a gate run against?**

- Instance A: a claim is settled by running a **frozen input against the Steam image, or against code
  that must reproduce it byte-for-byte**. Byte gates, pixel diffs, decompilation at a PC.
- Instance B: a claim is settled by **the ledger and the deployed service**. Invariant runs over the
  journal, crash-between-legs walks, what rise3 is actually running.

This cuts *through* repositories, and that is correct. `d3dcap` physically lives in
`mvc-live-skins-quarters` (an RR-lineage repo) and belongs to **A**, because its acceptance test is a
pixel diff against the same Steam binary. The tape/receipt product that *consumes* those frames
belongs to **B**. The crossing is exactly one interface — a tape file — which is where the reference
lens sits.

### How a cross-instance reference works today — **partly UNKNOWN, and the gap is the price of the seam**

What the engine supports, CONFIRMED:
- `config.py:35` accepts `backing: graph` as a lens backing, and `docs/METHOD.md:136` defines a
  **reference graph** as "another instance that read that field", used by a *discipline lens* when it
  must supply outside knowledge. `METHOD.md:138` ranks `graph`-backed above `source`-backed.
- Two instances are two namespaces, optionally on one SurrealDB, and two MCP front doors
  (`mcp/instance_adapter.py` reads one `GROUNDWORKS_INSTANCE`). One agent session can hold both.

What it does **not** support, CONFIRMED:
- **A lens never promotes.** `docs/ARCHITECTURE.md:41`: "L5 and L6 never promote anything: a lens
  verdict, a panel result and a rendered marker are all agent-grade opinion about the graph, not
  evidence in it." A fact imported from A into B arrives as attestation and must be
  **re-established locally against B's own units** before anything in B can be confirmed on it.
- **No retraction crosses the boundary.** Nothing in the engine watches another namespace's change
  feed. If A retracts, B's derived claim stays confirmed until a human re-runs B's audit.

So the honest answer: a cross-instance reference today is a **discipline lens with `backing: graph`,
one-directional, agent-grade, with manual re-checking.** That is a real cost. It is affordable
between "the machine" and "the money" because the dependency there is thin (one tape format). It
would be unaffordable between netcode and Steam RE — which is why they share an instance.

**Engine gap to record (roadmap candidate):** a `reference` adapter letting instance B subscribe to
named facts in instance A's change feed and raise a `review` row when one is retracted. Not a bridge
(no promotion) — just a tripwire. Cheap: it reuses `queue.py`'s machinery with a second `Db`.

---

## 2. The eight answers

### Instance A — `supergun`

#### 1. Goal
Establish, for the SUPERGUN owner, how the Steam Fighting Collection's own compiled frame function
behaves and what the shell around it must do, so that every shipped target (win-x64, Pi 4, Radxa
A7S, wasm, METAL x86) produces byte-identical frames within the stated latency budget.
**Holder:** the shell's owner/maintainer. **Success:** a question like "why does the A7S diverge at
k 782" is answered with a live citation and a named gate run. **Off limits:** distributing game
data; any claim about a build we do not own; committing decompiled C.

#### 2. Unit of evidence — five types, every locator program-resolvable

| type | `locator_fields` | text is | chunk_size |
|---|---|---|---|
| `symbol` | `[repo, path, symbol, commit]` | the definition as read at that commit | 60 |
| `routine` | `[image, pc, tool]` (`mvc_dump.bin` / `marvelous2`, an address, `ghidra`/`disasm`) | the decompilation or disassembly excerpt actually read | 40 |
| `field` | `[image, base, offset]` | the observed type/bytes plus the routine that touches it | 120 |
| `gate_run` | `[gate, tape, commit, image_sha, host, run_id]` | the run's verdict line and its counters | 100 |
| `doc_section` | `[repo, path, heading, commit, lineno]` | the section body, verbatim | 45 |

Hash `symbol` on the **normalised definition**, not raw lines, so reformatting and moved code do not
manufacture staleness (`instances/codebase-experts/INSTANCE.md:28`).

**The load-bearing choice: `gate_run` is a UNIT, not a fact.** This project's strongest evidence is
"the w3 gate went green on tape T at commit C on host H, and the control went red." If that is a
*fact*, nothing anchors it and it can simply be asserted. As a *unit* it has a locator a program
resolves (the run log), it carries text a verbatim quote can be checked against
(`validator.py:70`), and a fact can `cites` it under a gate that demands confirming grade.
Everything else in this design follows from that one decision.

#### 3. Fidelity ladder (HUMAN-OWNED), strongest first
```
reproduction > code > metric > recurrence > attestation > agent > recollection
confirming:   [reproduction, code]        agent_grades: [agent]
```
- **reproduction** — a `gate_run`: frozen input, deterministic verdict, **and the control ran**.
- **code** — a `symbol` / `routine` / `field` whose text is the real definition at a named commit or PC.
- **metric** — a measured number from a live session (frame cost, p95). Never confirms: it is not
  frozen. This is re_kb's own discriminator and it is right
  (`instances/reverse-engineering/INSTANCE.md:29`; `kb.py:52`).
- **recurrence** — "it stopped happening after the change."
- **attestation** — a `doc_section`, a commit message, a memory file, an agent charter. **Cannot
  confirm anything.** This is the whole answer to "the docs are attestation-grade."
- **agent / recollection** — model output.

`config.py:337` forbids `confirming` from being every grade (here 2 of 7) and `config.py:342-345`
forbids any agent grade from confirming. Both satisfied.

#### 4. Status axis (HUMAN-OWNED)
```
values: [live, stale, gone, other_build]      usable: [live]
```
Resolver (an adapter, per `instances/_template/instance.yaml:41`):
- `symbol` / `doc_section`: recompute the normalised hash at the **baseline commit**; equal → `live`,
  different → `stale`, absent → `gone`.
- `routine` / `field`: compare the image sha256; a different image → `other_build`.
- `gate_run`: `live` while its commit is an ancestor of the baseline and its `image_sha` matches;
  `other_build` otherwise.

**The baseline is a named commit, never "HEAD".** CONFIRMED (M1, M2): 33 worktrees on 33 branches,
26 touched in the last two days, sharing one object store. There is no canonical HEAD. The instance
names ONE baseline ref in `instance.yaml` — today `integrate/rc3` at `1a5b0a2` — and code on every
other branch is `other_build`: **true, but unusable**, which is exactly the shape `config.py:354-355`
insists must be expressible. This is the largest departure from
`instances/codebase-experts/INSTANCE.md:15`, which assumes one HEAD.

#### 5. Vocabulary — transcribed, not invented
- **Subsystem map:** the baseline's own crate/module tree (`supersoaker/runner/sg_runner`, `sg_net`,
  `sg_broker`, `sg_update`, `tools/wasm`), measured at 122 files / 4,095 functions (M3), plus the RE
  subsystem names already written into `re_kb/01_schema.surql:12-26`.
- **Framework lens frame:** `77_epistemics.surql` lines 110–137, the `UPDATE source SET strength=...
  WHERE kind IN [...]` blocks. That file is already the authoritative ladder, and `ladder.py` already
  parses it rather than re-stating it — exactly the discipline `METHOD.md` asks for. Reuse it as
  `frame.source`.
- `issues`: `[determinism, netcode, render, audio, input, latency, lift, arc_carve, release,
  identity, security, platform]`
- `targets` (become `conclusion` rows via `loader.py:73-77`): the things actually argued about —
  `rc3_ships`, `a7s_byte_exact`, `wasm_lift_complete`, `save_set_is_minimal`, `public_link_safe`.
- `tiers: [0,1,2,3]`, `directions: [for_goal, against_goal, mixed, neutral]`.

**Not the 18 charters.** They are agent-written prose — attestation. The module tree and
`77_epistemics.surql` are authoritative; the charters supply the *question bank*, not the vocabulary.

#### 6. Gates — each with a reject probe and an accept probe

`gates.py:196-253` generates both directions automatically from each rule, plus a sharper
wrong-grade rejection for any `cites` clause carrying a `where` (`gates.py:244-246`). What follows is
the rules; the probes are what the generator emits, written out so they can be checked by eye.

**G1 `confirmed_needs_reproduction_or_code`** — `fact.status = 'confirmed'` requires
`cites → unit WHERE grade IN $confirming AND status IN $usable`, min 1.
- *Reject:* "the A7S shell is byte-exact" confirmed citing only a `doc_section` (grade `attestation`).
- *Reject harder:* citing a `symbol` whose status is `other_build`.
- *Accept:* after citing `gate_run:w3_a7s_<run>` (grade `reproduction`, status `live`).
- The refusal already explains itself: `write.py:83-86` lists every cited unit with its grade and
  status and names what is missing.

**G2 `a_gate_claim_needs_a_red_arm`** — the rule this domain demands and no existing spec has. A
conclusion of type *gate result* is `ready` only with **two** `gate_run` citations: the green run,
and a control run whose `meta` records that it went red.
- *Reject:* mark `w3_byte_exact` ready citing one green run.
- *Accept:* after citing the green run **and** the red control.
- **Engine limit, CONFIRMED:** `gates.py:145-154` builds the gate boolean purely from edge
  traversals; there is no predicate over the row's own fields beyond the gated one. So "this fact is
  about a gate" cannot be expressed as a condition. Model gate results as their own conclusion target
  with two required citations instead of teaching the generator about statement text.

**G3 `a_locator_is_not_a_citation`** — ports `88_promotion_assert.surql:15-20` in spirit. That header
admits the compromise: the routine arm of re_kb's gate "carries no line and no excerpt, so it proves
the claim MENTIONS a PC rather than that it is VISIBLE there", and **43 confirmed findings rest on
it**. On this engine the compromise vanishes by construction: `cites` points at a `unit`, and
`intake.py:45-46` refuses a unit with empty text.
- *Reject:* a `routine` unit with no text is refused at intake, so the citation cannot exist.
- *Accept:* a `routine` unit carrying the decompilation excerpt.

**G4 `stale_cannot_confirm`** — falls out of the status axis, plus automatic demotion.
- *Reject:* confirm a fact citing a `symbol` the resolver marked `stale`.
- *Accept:* after re-intake at the baseline yields a `live` unit and the fact re-cites it.
- **The trap, CONFIRMED, and it must be planted in the regression suite:** `queue.py:408` returns
  early unless `grade` or `status` changed — `if not change.changed("grade", "status"): return`. An
  intake adapter that rewrites only the `hash` demotes **nothing**, and every charter keeps looking
  fresh while being wrong. The adapter must write the status transition.

**G5 `conclusion_ready_needs_a_surviving_attack`** — straight from
`instances/demo-incident/instance.yaml:47-61`: requires `supports → fact` min 1 and `attacks` min 1;
forbids `attacks WHERE verdict = 'fatal'` and `attacks WHERE verdict = 'damaging' AND answered = false`.
- *Reject:* mark `rc3_ships` ready with support and no attack.
- *Accept:* after a red-team critique with verdict `survivable`.

**G6 `negatives_are_never_gated`** — not a gate; the *absence* of one, and it must still be probed.
`write.py:88-93` writes `status='ruled_out'` with no evidence requirement and `gates.py:38` includes
it in `FACT_STATUSES`.
- *Probe:* `rule_out` with zero citations must succeed, and G1 must not fire on it. A run where
  `ruled_out` was refused is a broken build, not a strict one.

#### 7. Roles
- **Adversary:** the existing `senior-re-generalist` charter ("how would we disprove this?") becomes
  the red-team seat. **Decision-maker:** the owner's ship/no-ship seat. **Auditor:** `claim-audit`
  with the seven verdicts (`METHOD.md:108`), run over every generated charter section.
- **Discipline seats,** one per subsystem, seeded from the existing charters: netcode, wasm, steam-re,
  sh4-re, d3d11-capture, native-build, pi-kernel, design, appsec, netsec, crypto.
- **Panel** (`roles[].panel`): options `[survives, damaged, fatal]`; objection categories
  `[evidence_grade, gate_cannot_go_red, wrong_baseline, scope_of_verdict, determinism,
  metric_mistaken_for_reproduction]`.
- **Lenses:** framework `ladder` (frame = `77_epistemics.surql:110-137`); perspective `shipper`
  (`standard: {status: [live]}`); one discipline lens per subsystem; a `stale` lens listing what the
  last baseline move demoted; an `archive` lens for MapleCast.

#### 8. Evaluator (HUMAN-OWNED)
- **Mechanical:** the generated probes, `misbehaved == 0` in both directions (`gates.py:256-285`);
  a staleness-correctness test (change a cited span in a scratch clone, require demotion); the
  project's own byte-exact gates re-run.
- **Regression file — the best asset here, and it already exists in prose.** The project's real past
  errors become planted entries: a gate trusted once without its control; a "confirmed" tape later
  retracted as the wrong image; an erroring PowerShell guard read in bash as "all clear"; `sha256sum`
  on a backslash path prefixing `\`; 3,869 duplicate copies mistaken for corpus.
- **Judged:** a frozen bank of ~60 questions the 18 charters currently answer, with verified cited
  answers, hidden from any writer (`METHOD.md:154`).
- **Outcome metric — a real one exists, which is rare.** A **time split on git history**: freeze the
  graph at commit C, ask which facts the next 30 days of commits invalidated, score against what
  actually changed (`instances/codebase-experts/INSTANCE.md:18`). Runnable here because the history
  is on disk.

### Instance B — `retro_receipts`

| # | Answer |
|---|---|
| 1 **Goal** | Establish that the money engine never leaks, strands or double-pays, and that a receipt replays what happened. Holder: the operator. Off limits: real player identities, escrow key material. |
| 2 **Unit** | `symbol` `[repo, path, symbol, commit]`; `invariant_run` `[audit, dataset, commit, run_id]`; `journal_record` `[store, account, transfer_code, ts]`; `replay_diff` `[tape, frames, metric, run_id]`; `doc_section` |
| 3 **Fidelity** | `invariant_run > code > journal_record > metric > attestation > agent`. confirming `[invariant_run, code]`. Note the difference from A: a journal record is primary evidence of *what happened* but is not a reproduction, so it cannot confirm a *behavioural* claim — only a historical one. |
| 4 **Status** | `[deployed, staged, branch_only, retired]`, usable `[deployed]`. The resolver compares the symbol hash against the commit rise3 is actually running, not a branch tip. Code that is true in a branch and not deployed is "true but unusable". |
| 5 **Vocabulary** | The TigerBeetle transfer-code charter, the house money invariants, the ranked money-risk register and the SSOT/derived-store rules, transcribed from their authoritative files. |
| 6 **Gates** | (a) confirmed needs `invariant_run` or `code`; (b) a money-path conclusion is not ready until a crash-between-legs walk has been recorded as an attack and answered; (c) a claim that a derived store is authoritative is not confirmable at any grade — a forbidden-claim probe; (d) negatives never gated. |
| 7 **Roles** | money expert, stack expert, adversary ("how do I double-pay?"), operator decision-maker, citation auditor. |
| 8 **Evaluator** | Replay the real journal against the invariants (a genuine outcome metric); past money bugs as regressions; a question bank on escrow / settle / refund. |

### MapleCast — archived, not an instance

CONFIRMED (M5): `maplecast-flycast` has 101 commits since 2026-08-29, but 322 of ~375 path touches
are under `tools/` (re_kb seeds), and 69 of 93 tracked `docs/**.md` predate 2026-08-29. **The repo is
alive as the re_kb host and dead as a product.** So: split by path, never by repo. The GSTA client /
flycast render-path docs enter instance A as units with status `gone` and facts marked `superseded`,
never re-read, reachable only through the `archive` lens. The re_kb tooling under `tools/` is live
material and enters normally.

---

## 3. How re_kb maps onto the engine

### Transfers as-is (rename only)

| re_kb | engine | Note |
|---|---|---|
| `finding` | `fact` | Statuses nearly match: re_kb `{open, inferred, confirmed, ruled_out, superseded, resolved}` (`88_promotion_assert.surql:59`) vs `FACT_STATUSES` `{proposed, inferred, open, confirmed, ruled_out, stale, superseded}` (`gates.py:38`). Only `resolved` has no home — map per row to `confirmed` or `superseded`; the 8 status-less findings become `proposed`. |
| `cites` | `cites` | Same name, same direction (`gates.py:29`: `cites: (fact, unit)`). |
| seed 88's ASSERT | generated gate | Structurally identical to what `gates.py:165-185` emits. The rule stops being hand-written and starts being generated from the eight answers, with probes. |

**The migration unblocks what re_kb deliberately would not install.**
`284_effective_status.surql:28-41` names the two blockers for *stored* demotion: (1) `'stale'` is not
in seed 88's status enum, so an event writing it makes the datastore reject the triggering update;
(2) `07_dedup_edges.surql` does `DELETE cites` then re-`RELATE`, which demoted 4 of 4 confirmed
findings in the probe. **Both are already fixed in the engine:** `stale` IS in `FACT_STATUSES`
(`gates.py:38`), and the loader writes edges by stable identity so a re-run updates in place
(`loader.py:8-11`, `patterns/edge_ids.py`). That is the strongest single argument for migrating
rather than adapting: re_kb's read-time `COMPUTED effective_status` is a workaround for a hole this
engine does not have.

### Must be re-derived

**`source` → `unit`. This is the whole migration and the whole cost.** A re_kb source is a pointer:
`{kind, ref, note}` (`01_schema.surql:18`). An engine unit requires non-empty `text`
(`intake.py:45-46`), a `locator` with every declared field present (`intake.py:41-44`), a `status`
and a `grade` (`intake.py:47-50`). So the sources behind the 890 confirmed findings must be resolved
back to **actual text** — the Ghidra decompilation, the disassembly excerpt, the gate-run log line.
Expensive, and the entire point: it converts re_kb's admitted "mentions a PC" (43 findings,
`88_promotion_assert.surql:15-20`) into "shows it at the PC".

**The ladder.** CONFIRMED (M6): 21 of 301 explicit `strength=` literals name a grade outside
`ladder.py`'s `ORDER`. `gate` (5) and `bytes` (1) are this project's *strongest* evidence and today
confirm **nothing**, because `CONFIRMING = {reproduction, code}` (`kb.py:53`, `ladder.py:29`).
Re-derive on the way in: `gate`/`bytes` → `reproduction`; `decompile` → `code`;
`runtime`/`measurement` → `metric`; `doc` → `attestation`. After that `config.py:331-345` enforces
one ladder with no second copy — exactly the defect `ladder.py`'s own docstring describes ("the
kind→strength map existed three times ... twelve source kinds in live use were absent from at least
one of them").

**`about` (1,747 edges, 571 derived today).** The engine has no fact→entity edge (`gates.py:28-36`).
Re-derive as `cites` where the entity has genuinely become a unit with text; **drop** where it was a
topical tag. The 571 machine-derived edges must NOT become citations — `METHOD.md:37`: bulk ingest
never writes conclusions.

**`tried_on` with outcomes — an engine gap that blocks migration.** CONFIRMED: `EDGES`
(`gates.py:28-36`) has no `tried_on`, and the write surface (`write.py`) has `propose / cite /
confirm / rule_out / support / critique / answer / mark_ready` and no `record_attempt`. re_kb has it,
with `masks_only` among the outcomes (`kb.py:268`). This is method non-negotiable **#6, "outcomes
live on edges"** (`METHOD.md:34`) with no implementation. Same for `supersedes` / `corrects` /
`fixes`: `superseded` exists as a *status* (`gates.py:38`) with no edge saying superseded **by what**.
**Build `tried_on` and `supersedes` into the engine before migrating**, or the project loses its
dead-end ledger — the asset it is most proud of.

### Drop

- `steam_routine` (10,880) and `routine` (4,843) **as entity rows**. A routine nobody has read is an
  index entry, not evidence. Keep the index as a **derived store outside the graph**
  (`ARCHITECTURE.md:83`: mirrors and search indexes are derived and never authoritative). A routine
  becomes a unit the moment someone reads it and pastes the text.
- `field` rows with no offset: 1,603 of 2,259 (71%). No offset, no resolvable locator — they fail
  `intake.py:41-44` by construction. This is also the exact sparse-column defect that already broke a
  lookup hook (`docs/TUNING.md:128`). Resolve them or drop them; do not migrate them.
- `address` (956, of which 822 doc-derived). Doc-derived addresses are attestation with no text.
  Derived index.

### Migration, not an adapter

An adapter over the live re_kb graph would translate re_kb's shape at read time — which moves the
rule **out of the datastore and back into a wrapper**. That is precisely the failure
`88_promotion_assert.surql:5-9` was written to end ("the graph shipped a convention wrapper (kb.py)
and called it enforcement ... every rule lived exactly one one-liner away from irrelevance"). Do not
repeat it. The engine's gate is generated onto tables named `fact`/`unit`/`cites` with `grade` and
`status` fields (`gates.py:52-116`) and evaluated by SurrealDB itself; that only works if the data
has that shape.

**But do not delete re_kb.** Run it read-only beside the new instance until the question bank scores
equal or better on the new one. And note the constraint this session worked under: CLAUDE.md rule 10
forbids touching 8001 — migration reads must be an explicit, owner-run export, never an agent
connecting to the live database.

---

## 4. Ingestion plan for the 734 documents

### What the unit inside a document is

**The H2–H4 section.** Locator `{repo, path, heading, commit, lineno}`; text = the section body
verbatim. A whole file is too big for one label (14.0 MB / 734 ≈ 19 KB average); a paragraph is too
small to carry a reason. A heading section is the smallest thing a reader actually cites.

CONFIRMED (M4): 13.9 sections per file over 337 sampled files. INFERRED: **≈10,200 `doc_section`
units** across the 734.

### Order — cheapest and highest-leverage first

1. **The 18 agent charters (261 KB).** They are the current experts; they define what the graph must
   be able to answer. They produce the **question bank**, not facts.
2. **The 188 memory files (1.6 MB).** Same, plus they *are* the dead-end ledger. Every ⚠ RETRACTED /
   PARKED / DEAD END line becomes a `rule_out` row — ungated (`write.py:88`), cheaper to record than
   a fact, exactly as `METHOD.md:33` requires.
3. **re_kb's 890 confirmed findings,** re-cited source by source (section 3).
4. **The baseline's own docs:** 153 tracked `.md` at `integrate/rc3` (M3).
5. **Everything else,** lane by lane, newest-commit-first.
6. **maplecast-flycast docs LAST and as `gone`** (M5: 69 of 93 predate the retirement date).

### Exclusions — logged, never silently dropped

`intake.py:65-68` already writes `excluded.jsonl` with a reason per excluded unit. Use it for:
- **Hash dedupe.** 3,869 duplicate copies, 90.6 MB, across worktrees. **CONFIRMED trap:**
  `intake.py:24-26` hashes `[type, locator, text]` — the locator is *included*, so the same document
  in 33 worktrees produces 33 different hashes and all 33 survive. The adapter must dedupe **before
  yielding**, on normalised text alone, keep the copy whose repo is the baseline, and yield each
  loser with `exclude: "duplicate of <id> (baseline copy) in <worktree>"`. Get this wrong and the
  corpus is 4.6x too big and the coverage number is a lie.
- Third-party trees (`deps/`, `third_party/`, `node_modules/`, the flycast fork's `core/` and `shell/`).
- Generated and machine output: decompiled C, lifted sources. Agent grade at best, enormous, and the
  standing rule is never to commit decompiled C.
- Anything the deny list hits (`scripts/privacy_scan.py`, fails closed).

### That documents cannot confirm anything

This needs no discipline; it is mechanical. A `doc_section` carries `grade: attestation`, which is
not in `fidelity.confirming`, so the generated ASSERT refuses any `confirmed` fact citing only docs,
on raw access too (`gates.py:165-185`).

What the doc pass is *for*:
1. **Coverage** — every section labelled exactly once.
2. **Proposed facts** — `propose` writes `status: 'proposed'` with no evidence requirement
   (`write.py:50-58`).
3. **Dead ends** — `rule_out`, ungated.
4. **The vocabulary and the question bank.**
5. **A reading queue of locators to resolve.** This is the deliverable that matters: it is the bridge
   from 3.5M tokens of prose to the few thousand `routine` / `symbol` / `gate_run` units that can
   actually confirm. Every doc sentence of the form "FUN_140620f10 is the sprite walker" becomes a
   queue entry: *go read that address and paste the text*.

### How coverage is PROVEN

Exactly as the engine already proves it, CONFIRMED:
- `validator.py:72-74` fails if any unit in a slice has no label.
- `validator.py:43-44` fails a second label on the same unit.
- `chunker.py:77-78` fails if `placed != len(units)`.
- `loader.py:52-56` fails a batch that returns fewer results than statements.
- `intake.py:79-80` treats an empty corpus as a failure, not a result.

Report per chunk: `kept + excluded == yielded`, with exclusions counted **by reason**. And report the
unflattering number beside it (CLAUDE.md 5): how many tier ≥ 2 labels had their quote rejected
(`validator.py:70`) and needed repair, and how many sections produced zero proposed facts.

### Cost

10,200 sections × ~810 subagent tokens/unit (`METHOD.md:216`: 7.4M over 9,101 units) ≈ **8.3M tokens**
for a full expert read of the docs alone. Attack it with tiered routing (`METHOD.md:208-209`): a
cheap pass assigns tiers, an expert reads only tier ≥ 2, and a random audit samples the cheap labels.
INFERRED that most sections land tier 0–1 as in instance 1; **this has not been measured here and
must not be quoted as though it were.**

---

## 5. How a project expert is produced

### The split

| Part | Size budget | Owner | Content |
|---|---|---|---|
| **Charter** | 8 KB | **human, signed off** | who this expert is, what they refuse to do, their standards, their known blind spots, their escalation rule (`METHOD.md:129`) |
| **Generated** | 8 KB | the graph, regenerated | module map from the baseline's own tree; confirmed facts with live citations; dead ends with what the source actually shows; the stale list; open questions; the gate table with each gate's last run and whether its control went red — every line carrying a locator and a grade |

Hard cap **16 KB per expert**. CONFIRMED: today's 18 charters total 261 KB, the largest being
supergun-wasm at 37 KB. A 37 KB charter is read by nobody and goes stale silently — which *is* the
stated problem. 37 KB → 16 KB is a 2.3x cut, and nothing is lost: the overflow lives in the graph,
reachable through the MCP front door and the lookup hook
(`instances/codebase-experts/INSTANCE.md:80`).

### The staleness rule, tied to git

On every move of the baseline ref:
1. `git diff <old> <new>` → changed paths.
2. Re-hash affected `symbol` and `doc_section` units.
3. **The adapter writes `status: stale`** on units whose hash changed.
4. `queue.py:407-419` sees a *status* change and demotes every `confirmed` fact citing them to
   `stale`, writing a `review` row under a deterministic id (idempotent, at-least-once).
5. The charter regenerates with its STALE section.
6. A human approves the diff.

**Step 3 is the whole thing.** `queue.py:408` returns early unless `grade` or `status` changed. An
adapter that rewrites only the hash demotes nothing and every charter stays green while going wrong.
The acceptance test for this step must plant exactly that mistake and require it to fail.

### Replace or augment the 18?

**Augment first; replace only on evidence.** Keep all 18 exactly as they are. Add the generated
section to **one** — `supergun-netcode`, because its facts cross into Steam RE and it is the one that
most needs the cascade. Run the question bank against both. Cut the hand-written body only where the
generated expert wins.

### What measurement shows the generated expert is BETTER

Three numbers against a frozen bank of ~60 questions built from what the owner has actually asked:

1. **Citation liveness.** Fraction of an answer's citations that resolve to a unit `live` at the
   baseline. A hand-written charter cannot score this at all — it cites prose. Expect a split;
   measure it, do not assume it.
2. **Claim-audit verdicts** on a human-adjudicated sample: VERIFIED / MISMATCH / NOT_FOUND /
   OUTSIDE_STATUS / MISATTRIBUTED / INTERPOLATED / UNCHECKABLE (`METHOD.md:108`). Report MISMATCH and
   MISATTRIBUTED separately — those are the two that cost days.
3. **Time split on real history.** Freeze both at commit C; take the next 30 days of commits; ask
   each "which of your facts does this invalidate?"; score precision and recall against what actually
   changed. The hand-written charter has no mechanism to answer, so its recall is ~0 by construction
   — which is the honest statement of the problem, and also why this number must never be quoted
   alone as victory.

**And the unflattering number, always** (CLAUDE.md 5): how many questions *both* got wrong, and how
many the generated expert answered UNKNOWN where the hand-written one answered confidently and
correctly. Trading correct confidence for honest ignorance has a real cost and it must be shown.

---

## 6. Order of work

Cheapest first. Each task has one acceptance test that can go red.

**T0 — THE SINGLE FIRST THING TO BUILD: the `doc_section` intake adapter with content dedupe, over
one repo (`supergun-integrate-rc3`, 153 tracked `.md`).**
*Acceptance:* produces `units.jsonl` + `excluded.jsonl`; an independent script re-opens each file at
the recorded commit, seeks the heading and confirms the text matches for **every** unit; a planted
copy of one file in a second worktree appears **once** in `units.jsonl` and **once** in
`excluded.jsonl` naming the survivor; `kept + excluded` equals sections walked. **It must fail if the
dedupe is removed.**
*Why first:* it is the cheapest thing that can be wrong in a way that poisons everything (3,869
duplicates), and `intake.py:24-26` makes the wrong behaviour the default because it hashes the
locator too.

**T1 — `instance.yaml` for `supergun`.** *Acceptance:* `python -m groundworks.config <path>` prints OK
with 5 unit types and ≥6 gate rules. **Start from `instances/demo-incident/instance.yaml`, not from
`instances/_template/`** — CONFIRMED (M7) the template does not parse, and it uses top-level `name:`,
`goal:`, `staleness:`, `discovery:` which are not in `config.py:27-28`'s `TOP_KEYS`; demo-incident
uses `instance: {id, goal}`, which is what the engine actually reads.

**T2 — schema, gates, probes on a throwaway database.** *Acceptance:* `gates.py --apply` reports
`misbehaved: 0` with every rule showing reject **and** accept, including the wrong-grade reject
(`gates.py:244-246`). Then deliberately weaken one gate and confirm its probe goes red.

**T3 — validator self-test on one doc chunk.** *Acceptance:* a planted misquote (`validator.py:70`)
and a missing label (`validator.py:72-74`) both FAIL; the honest chunk passes.

**T4 — the labelling pass on that repo's ~2,100 sections.** *Acceptance:* every chunk
validator-clean, plus a two-labeler agreement sample on 2 chunks with human adjudication. **Report
the agreement rate even if it is bad** — `docs/TUNING.md:31`: validation proves shape, not truth.

**T5 — the `symbol` and `gate_run` adapters.** *Acceptance:* a fact confirmed on a `gate_run` unit is
accepted; the same fact confirmed on a `doc_section` is refused with a message naming the grade
(`write.py:83-86` already produces it).

**T6 — staleness.** Apply the changefeed (`queue.py:74`), run the worker, move the baseline by one
commit in a scratch clone. *Acceptance:* the affected fact goes `stale` with a `review` row; **and**
the negative control — an adapter that rewrites only `hash` demotes nothing — is recorded as a
permanent regression entry.

**T7 — re_kb migration, confirmed findings only (890), source by source.** *Acceptance:* each
migrated fact either has a unit with real text and the gate accepts, or lands as `proposed` with a
reading-queue entry. Report the split; the unflattering number is how many of the 890 could **not**
be re-confirmed.

**T8 — generate one expert (netcode) and run the question bank against it and the hand-written
charter.** *Acceptance:* the three measurements of section 5, with a human adjudicating a sample.

**T9 — engine changes, only if T7/T8 prove them needed:** `tried_on` with outcomes, `supersedes`,
and the cross-instance retraction tripwire. Do not build them speculatively.

---

## 7. Costs and risks

### Token cost of the first full read

| Pass | Units | Subtotal at ~810 tok/unit |
|---|---|---|
| `doc_section` (all 734 docs) | ≈10,200 (M4, extrapolated) | **≈8.3M** |
| `symbol` at the baseline | ≈4,100 (M3: 4,095 fn) | **≈3.3M** |
| `routine` / `field` / `gate_run` re-reads for re_kb | UNKNOWN until T7 reports the split | — |
| **First complete pass** | | **≈12M tokens**, before tiered routing |

Rate source: `METHOD.md:216` (7.4M subagent tokens over 9,101 units, ~25 min wall clock with 77
agents). Wall clock here is **UNKNOWN**: it depends on fan-out this machine has not been measured at.
Per the owner's standing rule, no ETA is given — scope and next step only.

### What goes stale fastest

1. **`symbol` units on the shell.** 26 of 33 worktrees moved in two days (M2). Anything confirmed on
   unmerged code is `other_build` the moment the baseline moves.
2. **Release-state facts** (0.3.2 live, 0.3.3 cut but unpublished) — they change on a human decision
   with no commit to hang a hash on. These need a manual `valid_to` (`temporal.py`).
3. **`gate_run` relevance.** The artifact is immutable, but it stops applying when the image or the
   tape changes. That is `other_build`, **not** `stale` — conflating the two silently destroys the
   reproduction tier.
4. **Doc sections** — slowest, and they were never confirming anything.
5. **Slowest of all: `routine` / `field` on `mvc_dump.bin` and `marvelous2`.** The binary does not
   move. The RE half of the graph is the durable asset; the shell half is the perishable one. That
   asymmetry should drive where the reading budget goes.

### What would make this a net loss

- **Dedupe done wrong.** 4.6x the cost and a coverage number that is a lie. Day one, or not at all.
- **Confirming from docs.** If anyone widens `fidelity.confirming` to include `attestation` to make a
  run pass, the whole thing becomes a slower, costlier wiki. This is `METHOD.md:296`'s "silent
  loosening", and is why `fidelity` is human-owned (`config.py:31`, `MUST_BE_HUMAN_OWNED`).
- **Running intake against a moving tree.** 33 worktrees, 26 touched in two days. Intake must pin an
  explicit commit sha, never a branch name, and never "HEAD". (We already made the moving-tree
  mistake once today, in this repo, with a test run.)
- **Building experts before the gates are probed.** A generated charter full of ungated `confirmed`
  facts is *worse* than the hand-written one, because it looks cited.
- **Migrating re_kb wholesale.** 10,880 `steam_routine` rows as units = 10,880 units with no text and
  no reader: cost with zero evidentiary gain.
- **The cheapest failure of all: doing the full read when the 18 charters are already good.** If the
  question bank shows them scoring as well on correctness and losing only on liveness, the correct
  answer is the staleness machinery alone (T0, T1, T6) and **not** the 12M-token read. T8 exists to
  make that outcome reportable rather than embarrassing.

---

## 8. What I would NOT do, and why

1. **One instance for everything.** `config.py:305` allows one goal and `METHOD.md:106` defines
   direction relative to one holder. Two goals in one graph makes for/against meaningless and the
   adversary lens useless.
2. **Six instances, one per lane.** There is no cross-instance cascade: no bridge code in
   `groundworks/**.py`, `docs/MODULES.md:55` marks `discovery` "designed", and `queue.py`'s worker is
   bound to one namespace. Splitting netcode from Steam RE guarantees a retracted carve fact leaves a
   confirmed netcode claim standing.
3. **An adapter over the live re_kb graph instead of a migration.** It moves the rule out of the
   datastore into a wrapper — the exact failure `88_promotion_assert.surql:5-9` exists to end.
4. **Touching the live re_kb database from agent work.** CLAUDE.md rule 10 forbids 8001/8002/8003.
   Every re_kb number in this note came from files on disk. Migration reads are an owner-run export.
5. **Ingesting decompiled C or lifted sources as units.** Machine output (agent grade at best),
   enormous, and the standing rule is never to commit decompiled C.
6. **Making the 18 charters the vocabulary.** They are agent-written attestation. The framework must
   be transcribed from an authoritative source — the module tree and `77_epistemics.surql:110-137`.
7. **Teaching the gate generator to read statement text** so G2 can say "this fact is about a gate".
   `gates.py:145-154` builds the boolean from edge traversals only; the right fix is to model gate
   results as their own conclusion type with two required citations.
8. **Starting from `instances/_template/`.** M7: it does not parse, and its top-level keys are not in
   `config.py`'s `TOP_KEYS`. Start from `demo-incident`, which loads.
9. **Migrating before `tried_on` exists.** The dead-end ledger with outcomes on edges is method
   non-negotiable #6 (`METHOD.md:34`) and has no engine implementation (`gates.py:28-36`, `write.py`).
   Migrating without it silently discards the project's most expensive knowledge.
10. **Promising the generated expert will win.** It has not been measured. The three measurements in
    section 5 exist precisely so the answer can come back "no".

---

## Open questions for the owner

1. **The baseline ref for instance A.** `integrate/rc3` at `1a5b0a2` is my recommendation. Confirm,
   or name another. Everything downstream — status, staleness, charter regeneration — hangs on it.
2. **Does `retro_receipts` get built at all now,** or is instance A alone the pilot? The cheap answer
   is A only, and B after T8 reports.
3. **The reading budget.** ≈12M tokens for the full first pass. Is the tiered-routing pilot (cheap
   tier pass, expert reads tier ≥ 2 only) worth measuring on one repo first? I think yes — it is the
   only lever that changes the number by an order of magnitude.
4. **Where the instance projects live.** `README.md:31` and `ARCHITECTURE.md:5` are explicit that an
   instance lives in its own project, never in this repo. `C:\Users\trist\projects\supergun-scholar`
   and `C:\Users\trist\projects\rr-scholar` are the obvious homes; `projects/brain` is the precedent.
