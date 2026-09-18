# Testing and evaluation strategy

**Written 2026-09-16** by the scholar-architect, at HEAD `3c2112f`, for the engineers and agents who will build and run it. **Design only: nothing in this document is built yet** except where a file is named as existing. Every claim is tagged **CONFIRMED** (read in the code, the commit log or a note, or measured), **INFERRED** (reasoned from confirmed facts) or **UNKNOWN**.

## The answer first

Three things would most improve how well this system is tested:

1. **Make "can this check fail?" a program, not a habit.** The repo's defects since 2026-09-15 were nearly all checks that ran and did nothing, and they were found by someone deliberately breaking code (CONFIRMED: `8a73fa4`, `6c54f25`, `89f7b78`, `277eded`, `fa0c668`, `7733fd5`, `a330b34`, `dff134c` each report hand-made mutations). A mutation registry — one named break per guard, tied to the test that must go red — turns that habit into a check that runs every night and fails when a guard stops being guarded. It also finishes the job of the six throwaway scripts, which today live outside the repo and edit the live tree in place.
2. **A seeded instance with planted answers, scored end to end.** Today the only mock is `instances/demo-incident`, and nothing scores what the whole pipeline *produced* against what it *should* have produced. Four of the defects in section 0 ("done" on an empty graph, the sweep deleting every edge, a correction reverted, a fatal verdict with no way out) are wrong **outputs**. A per-category diff against planted truth catches them whatever the reason. Plant the truth, including the instance's own table, status and grade names, from a seed the engine never sees.
3. **Replay the defect record.** Every fixed defect in the log has a pre-fix commit and a reproduction written in its commit message. Rebuilding those trees and requiring each harness to go **red** on them is the strongest proof that the harness works. It is also the cheapest source of test cases this repo has.

Performance has never been measured (CONFIRMED before this document). A first baseline is in section 4. The headline: **loading is linear at about 440 units/s, and a reload of an unchanged corpus costs as much as the first load** (151 s and 160 s at 66,000 units). The provenance read added in `a330b34` is not the cost: 0.6 s of that 160 s. **Probe time grows linearly with the graph the probes run on**: about 2 s empty and 18.4 s at 66,000 units. Unchanged code moved medians by up to 17% between two sittings, so any perf gate must allow for that.

---

## 0. The defect record this design is built from

Every section below points back to this table. The commit messages are the evidence (`git log --since="2026-09-15 12:00"`).

| # | Defect (CONFIRMED in the commit message) | Commit | Kind | How it was found |
|---|---|---|---|---|
| D1 | Gate probes `misbehaved: 0` on a gate whose `where` was deleted | `d636282` | check that reports on its own spec, not on the spec's strength | use, by a second session |
| D2 | `compare_baseline` blind to four weakenings (value repointed, empty baseline, collapsed duplicate clauses, missing probe result) | `8a73fa4` | check blind to a class of change | architect audit + mutation |
| D3 | Server suites used whatever was already listening on their port | `d636282`, `49b3c99` | environment borrowed | use (leftover server on 8031; another session on 8025) |
| D4 | `loader.load()` on an empty labels directory swept every `bears_on` edge and returned success | `34960cf` | empty input read as a result; **data loss** | audit |
| D5 | CI green with every database suite skipped (`curl \| sh` without pipefail) | `6c54f25` | skip reported as pass | audit |
| D6 | Demo verdict was a substring chosen by the adapter under test; "done" over zero facts | `6c54f25` | oracle inside the code under test | audit + neutered adapter |
| D7 | Privacy scan used an allow list of extensions, skipped `.csv/.xml/.sql` and reported clean | `6c54f25` | check narrower than its claim | audit + fixture |
| D8 | Two tests matched the word "pipefail" in a **comment** and stayed green when the setting was deleted | `6c54f25` | test asserting on text, not behaviour | mutation |
| D9 | Accept probe generated zero fix statements for an unrecognised table and still said "behaved"; wrong-evidence probe keyed on an edge named `cites` | `b52ea90` | domain word in engine; silent empty generation | a declared (non-built-in) table |
| D10 | Preservation 100% over zero rows; two of seven checks silently not generated | `277eded` | vacuous truth | audit |
| D11 | Kappa 1.0 on a sample with one label value (an existing test asserted the defect) | `277eded` | degenerate statistic | audit |
| D12 | Queue worker clean forever with no changefeed; a fresh start indistinguishable from a resume | `277eded` | silent schema drop | audit |
| D13 | Validator "PASS: 0 units"; `plant.py` wrote a seeded copy with no seeds | `277eded` | vacuous truth | audit |
| D14 | Gate whose evidence is itself gated could not be probed | `5cfa61b`, `fa0c668` | generator incomplete (was loud) | use |
| D15 | `fatal` verdict could never be downgraded: critique id incremented, CREATE not UPSERT | `7733fd5` | unreachable state transition | real instance (3 of 3 points blocked) |
| D16 | A reload silently reverted a human correction | `a330b34` | lost write; no conflict reported | real instance |
| D17 | A lane promoted its own findings; 50 authorities verified on one reading after the verifier died on a usage limit | `dff134c`, `5ee30bb` | failure mode (stage killed) + missing independence | real instance |
| D18 | `test_gates.py`'s `__main__` block sat above four classes: "Ran 7 tests ... OK" | `8a73fa4` | harness ran a subset and did not say so | audit |
| D19 | A patch "failed silently at its second match"; the commit described a flag that did not exist | `2fb2928` | anchor did not match; nothing changed | noticed next commit |
| D20 | A test fixture built on port 8002, a live database | `5ee30bb` | environment hazard | beta report sweep |
| D21 | Experiment harness loaded rows in the platform encoding; mojibake reached every reviewer | `aa284c1` | encoding fault in the evaluator itself | reading the output |
| D22 | Docker packaging documented as working, never once run | `9957070` | path never executed | question from the owner |

**What the table says (CONFIRMED by counting it):**
- 17 of 22 are "a check or output that looked successful while doing nothing": D1, D2, D4 to D13, D16, D18, D19, D21, D22.
- 7 were found by use on a real or second instance: D1, D3, D9, D14, D15, D16, D17.
- The rest were found by an agent auditing or deliberately breaking code.
- **None was found by the ordinary test suite failing.**

---

## 1. Two kinds of testing, and why they must not be mixed

| | Deterministic engine correctness | Stochastic agent and model quality |
|---|---|---|
| What | gates, probes, baseline, loader, reconcile, schema, permissions, queue, temporal history, validator, chunker, renderers, metrics, CLI exit codes | labelling (tier, direction, targets), extraction, verdict calibration, reviewer catch rate, retrieval ranking, panel behaviour |
| A result is | **pass or fail**, reproducible bit for bit from a seed | **an estimate**, with n, a confidence interval, and the conditions (model, guide hash, date) |
| Right techniques | unit tests on pure functions; tests on a real throwaway SurrealDB; **mutation registry** (s.2); **seeded instance, deterministic categories** (s.3); property and invariant tests (s.6); fault injection (s.5); defect replay (s.9) | fixed held-out eval sets; repeated runs; Wilson or bootstrap intervals; paired designs; pre-registered decision rules; results history with regression gates (s.7) |
| Wrong techniques | retries on failure (a flake here has been a port race every time: D3); mocks of SurrealDB for behaviour (D12 and the "denied write returns `[]` with status OK" rule in `DECISIONS.md` line 14 are datastore facts a mock would have got wrong) | a single run read as pass or fail; a threshold on a point estimate; an LLM judge nobody calibrated; tuning on the eval set |
| A green result means | this exact input produced this exact output | "no evidence of a difference at this n", unless the interval says more |

**Where people get this wrong, with this repo's own examples:**
- **Reading one agent run as a result.** The reading-layer note reported 4/15 against 3/15 with one reviewer per cell (CONFIRMED, `notes/2026-09-15_reading_layer_evaluation.md` line 41). The Wilson 95% intervals are **[0.11, 0.52]** and **[0.07, 0.45]**. To detect 0.20 against 0.27 at 80% power, a two-proportion test needs about **575 reviews per arm** (computed with the standard formula, α 0.05). The note called it noise, which was right. A harness should compute that and print it, instead of relying on the author to know.
- **Reading a deterministic check as an estimate.** "Preservation 100%" is a deterministic claim. When it was measured over zero rows (D10), the fault was a pass/fail check with no guard against a vacuous input. A confidence interval would not have caught it. A refusal on n = 0 did.
- **Mixing the two in one number.** "Demo end to end: 3 rules 0 misbehaved" (`dff134c`) is deterministic. "Two agents labelled 66 of 66 and both passed validation" (`8cfa578`) is deterministic about **shape** and says nothing about **truth**. `TUNING.md` line 36 already says so: "validation proves shape, not truth". The eval store (s.7) must keep the two in separate record types, so a validation pass can never be displayed as a quality score.
- **Using the stochastic layer to test the deterministic one.** The rule re-check flagged 2 of 2 status upgrades in every arm (`notes/2026-09-16_marker_wording.md` line 92). That is a deterministic fact, and it belongs in a unit test. It does not belong in a reviewer study, where it only adds noise.

**Where standard practice does not fit this system (INFERRED from the table in s.0):**
- **Line or branch coverage is nearly worthless here.** Every line of `judge()` ran while D1 let a weakened gate through, and every line of the sweep ran while D4 deleted 79 edges. The defects were executed code that checked the wrong thing. Replace the coverage number with a **guard mutation score**: of the named guards, how many have a mutation that a test kills (s.2).
- **Generic mutation tools** (random operator flips across the codebase) would produce thousands of mutants, most irrelevant or equivalent, and they are not standard library. This repo needs **curated, guard-directed mutations**: one per promise the code makes, each named after the defect it stands for.
- **Golden-file or snapshot tests bless defects when they are regenerated.** The gate baseline works only because regeneration is human-reviewed and "additions only" (`0a40ee1`: "A regenerated baseline that no longer catches anything is worse than none"). Any snapshot this harness keeps follows the same rule, and a regenerated snapshot must be shown to still fail on a mutation.
- **The test pyramid is upside down here.** The costly defects lived in the interaction with a real SurrealDB 3.1.6: a changefeed dropped on re-apply, a denied write returning `[]` with status OK, `$auth` being NONE for root. The expensive end-to-end layer on a real server is where most of the value is, so mock only fault *shapes* that were first measured on a real server.
- **Auto-retrying flaky tests** would have hidden D3 permanently. Never retry. A test that fails once is a finding.

---

## 2. Systematic mutation testing

### What it replaces (CONFIRMED)

The hand scripts from 2026-09-16 still sit in a session scratchpad outside the repo. `mutate.py`:
- patches one exact string;
- refuses an anchor that does not occur exactly once;
- runs named tests;
- restores the file and asserts the SHA-256 matches.

`breaks.py` holds B1 to B22 for `dff134c`. Both **edit the live working tree** (`ROOT = C:/Users/trist/projects/groundworks`). Both treat any non-zero exit as "red", so a mutant that fails to import counts as killed. Neither checks that the named test passed *before* the mutation. And both would report a **skipped** database test (port occupied) as "NOT RED", which is a false survivor.

### The tool

```
tests/harness/mutations.json      the registry (human-owned; entries added, never removed or loosened)
tests/harness/mutate.py           the runner (standard library)
tests/harness/mutations_ids.json  the pinned id list, checked like gate_baseline.json

python -m tests.harness.mutate run   [--ids M012,M013] [--files groundworks/loader.py] [--from HEAD|worktree] [--json OUT]
python -m tests.harness.mutate lint                      # no tests run: every anchor still valid, every test id exists
python -m tests.harness.mutate replay --commit 34960cf^  # s.9
```

**A registry entry:**

```json
{
  "id": "M014",
  "guard": "a run that read no label files does not sweep",
  "defect": "D4", "commit": "34960cf",
  "file": "groundworks/pipeline/loader.py",
  "anchor": "    if not label_files:\n        counts[\"sweep\"] = (",
  "replacement": "    if False:\n        counts[\"sweep\"] = (",
  "must_fail": ["tests.test_loader_sweep.SweepTests.test_an_empty_labels_directory_sweeps_nothing"],
  "needs_db": true,
  "timeout_s": 180
}
```

The test id above is illustrative. `lint` verifies each real id exists.

**The run, per mutation:**

1. **Isolated tree.**
   - Build it with `git archive <rev> | tar -x` into a temporary directory, or with `--from worktree` as a copy of `git ls-files` plus modified files.
   - The live tree is **never written**, so there is nothing to restore in it and no way to corrupt another lane's edits.
   - The tree's content hash is recorded in the report. Mutations then apply to a fresh copy of that tree, or to the same copy restored and hash-verified, whichever is faster.
   - *Trap it closes:* another lane mid-edit in the same tree. CONFIRMED hazard: `a330b34` and `6c54f25` both deferred full-suite runs because "another lane is mid-edit in the same tree", and during this very design session `groundworks/config.py` was modified and `groundworks/assertions.py` created by another lane at 14:47.
2. **Anchor validation, before anything runs.**
   - The anchor must occur **exactly once** (D19).
   - It must not lie wholly inside a `#` comment or a docstring. Check with `tokenize`: every character of the match must fall in non-COMMENT tokens. A STRING token is allowed only when it is not a bare expression statement, because SurrealQL lives in strings here (B16 and B17 mutate SQL text).
   - For YAML, workflow and other non-Python files, strip `#` comments line by line before matching. That is the same rule `tests/test_run_verdict.py:182-186` adopted after D8.
3. **Semantic change check.**
   - After applying the replacement, `ast.dump(ast.parse(before)) != ast.dump(ast.parse(after))` for Python.
   - A comment-only or whitespace-only change is reported **INVALID (no semantic change)**, not survived and not killed.
   - The mutant must also compile. A mutant that does not compile is **STILLBORN**, and that is a defect in the registry entry.
4. **Pre-flight on the unmutated tree.** Every `must_fail` test must **pass, not skip**. A skip (occupied port, missing binary) makes the whole run **NOT EXECUTED**, exit 2. D5 was exactly a skip read as a pass.
5. **Run the named tests in a subprocess with a JSON result reporter.** Use a small `unittest.TestResult` subclass that writes one line per test: `pass | fail | error:<ExceptionType> | skip`. Do not parse the text "OK".
6. **Classify.**
   - **KILLED:** every `must_fail` test reports `fail`, or `error` with an exception that is not ImportError, SyntaxError, SkipTest or a port refusal.
   - **KILLED-BY-TIMEOUT:** reported separately. Registry entries may declare it expected; `fa0c668`'s no-depth-limit mutation raised RecursionError.
   - **SURVIVED:** any `must_fail` test passed. That is a failure of the run, exit 1.
   - **NOT EXECUTED:** a test skipped under the mutant. Also a failure of the run.
7. **Verify the source file's bytes match the pre-mutation hash** before the next mutation, as `mutate.py` already does.

**Summary line (last line, because it is the one a human reads; `cli.py:115-118` precedent):**

```
MUTATIONS  61 declared | 58 killed | 1 killed-by-timeout (expected) | 0 SURVIVED | 0 INVALID | 0 STILLBORN | 2 NOT EXECUTED (port 8028 in use)
```

Any SURVIVED, INVALID, STILLBORN or NOT EXECUTED makes the exit non-zero.

**Ports.** The database suites hard-code their ports (CONFIRMED, `tests/test_*.py` `PORT =` lines: 8023, 8024, 8026 to 8034). So a mutation run and an ordinary suite run on the same machine collide. The runner:
- takes an OS-level lock on a file under the temp directory;
- checks the needed ports up front;
- runs database mutations **serially**;
- refuses (NOT EXECUTED) rather than borrowing a port.

A later, separate change should let suites take their port from an environment variable, so a mutation run can use 8050 to 8059. That is a test-infrastructure change, and it is not made here.

**Seed the registry from what already exists (CONFIRMED to exist as prose or scratch code):**

| Source | Entries |
|---|---|
| B1 to B22 in `breaks.py` (`dff134c`) | 22 |
| `a330b34` "Eleven deliberate breaks" | 11 |
| `fa0c668` five mutations | 5 |
| `7733fd5` four mutations | 4 |
| `8a73fa4` four baseline blind spots and the port guards | about 6 |
| `6c54f25` CI pipefail, `--require-db`, privacy extensions, demo verdict | about 6 |
| `277eded` one per V2-16 fix | 7 |
| `89f7b78` forbids probe | about 3 |

That is about 64 before anything new is written (INFERRED count from the messages).

**CI or on demand.** Both, split by cost (timing UNKNOWN until built; B1 to B22 took a session by hand):
- **CI, every push:** `mutate lint` (no tests run: anchors still match exactly once, still outside comments, test ids exist, no id removed from `mutations_ids.json`). Also `mutate run --files <changed files>`: only the mutations whose file the diff touched. This is what stops **anchor rot**. When code changes and an anchor stops matching, the entry is stale, and a stale entry is a guard nobody is checking.
- **Nightly and before any release:** the full registry.
- **Why not the full registry in CI:** database mutations run serially on fixed ports, and each spins up a server. Tens of minutes on every push would push people toward skipping it. Once a skipped check is normal, the whole exercise stops meaning anything.

**What it catches that today's suite does not.** A guard whose test stopped testing it: D8 caught mechanically instead of by luck, D18's hidden classes (a `must_fail` test that is never collected reports as missing), D2's blind spots (each is a mutation of `compare_baseline`), and D19's non-matching patch.

**Human-owned.** The registry is evaluator (`METHOD.md` s.15). Agents may **add** entries. Removing or loosening one requires a human, and `mutations_ids.json` makes a removal fail `lint`, the same way `gate_baseline.json` makes a removed clause fail.

---

## 3. A seeded instance with known answers

### What exists and what is missing (CONFIRMED)

- `instances/demo-incident` has one fixed corpus (66 units after one exclusion), a keyword stand-in labeller (`labeler_stub.py`), and a scenario whose expected outcomes are written in prose inside `adapter.py`.
- `groundworks/present/plant.py` plants five error types into **rendered rows** for reviewer studies. Its targeting is deterministic given the policy, so two seeds upgraded the same two rows (`notes/2026-09-16_marker_wording.md` line 193).
- Nothing plants truth into the **corpus and the graph**, and nothing scores the pipeline's output against it.
- `evaluator.regression_file` is required by `config.py:624`. But the demo's `evals/known_errors.json` **does not exist**, and no module reads that key or `evaluator.label_agreement` (CONFIRMED by grep: the only references are `config.py:624-625` and `tests/test_config.py`). **The Lessons loop in METHOD s.15 is declared and never executed.** That is D22's class, in the evaluator itself.

### The generator

```
evals/seeded/generate.py      python -m evals.seeded.generate --seed 17 --scale 1 --out DIR
evals/seeded/score.py         python -m evals.seeded.score DIR/truth.json DIR/observed.json [--history evals/history/seeded.jsonl]
evals/seeded/observe.py       python -m evals.seeded.observe INSTANCE_DIR --url URL --ns NS --db DB --out DIR/observed.json
```

Outputs, all from `random.Random(seed)`, with no domain words:

| File | Content |
|---|---|
| `instance.yaml` | **Generated vocabulary.** Table, edge, status, grade, tier-label and target names are drawn from a neutral syllable generator (`zorvak`, `tel_upri`), not taken from the demo. Includes 1 to 3 declared `tables:` and `edges:`, 2 to 5 gate rules, some chained (rule A's evidence gated by rule B), some with `forbids`, optionally `independent: true` with an `access:` section, and optionally `unique:`. |
| `adapter.py` | units yielded from a generated corpus, plus planted exclusions |
| `labels/*.jsonl` | the **oracle labels** (the truth), plus optionally a **noisy labeller** at a stated error rate |
| `script.json` | the ordered writes a lane would make: proposals, citations, confirmations, eliminations, critiques, verdict changes, answers, preservations, corrections between two loads, a second source version |
| `truth.json` | **the answer key**: for every category below, the exact expected set of ids and counts. **Never read by the engine or by `observe.py`.** |

**Planted categories.** Each is a set of ids with an expected outcome, so each can be diffed exactly.

| Category | What is planted | Expected outcome (truth) | Deterministic? | Defects it stands for |
|---|---|---|---|---|
| exclusions | units carrying `exclude:` | exactly these in `excluded.jsonl`, none in the graph | yes | intake contract |
| coverage | every unit | exactly one label per unit id; every unit in exactly one slice | yes | "every unit labelled exactly once" |
| validator plants | a misquote, a missing label, a duplicate, an out-of-vocabulary tier, in a **separate** broken label set | validator errors naming exactly those units and fields | yes | D13; charter step 3 self-test |
| should confirm | facts cited on a usable unit of a confirming grade | `status = confirmed` accepted | yes | gates |
| should not confirm (unusable) | facts citing only unusable units | refused; status unchanged | yes | gates |
| should not confirm (wrong grade) | usable but non-confirming grade | refused | yes | D1, V2-9 |
| should not confirm (no evidence) | no citation | refused | yes | gates |
| chained evidence | a rule whose evidence row is itself gated | refused until the inner gate passes through its real route | yes | D14 |
| contradictions | a confirmed fact whose cited unit later becomes unusable | listed in `consistency.report` `no_longer_holds` for its rule, and no other row listed | yes | the V2-19 consistency check |
| stale evidence | a unit whose source changes status between load 1 and load 2, with nobody correcting it | `followed` lists it; any fact standing on it appears in the consistency report | yes | D16 (other direction) |
| corrections | a unit corrected by `correct_unit`, then a source that disagrees | value held; one conflict row naming (unit, field, by); `accept_source` on exactly one of them releases only that one | yes | D16 |
| raw corrections | a unit changed by raw UPDATE outside `correct_unit` | held; reported `changed_outside_loader`, UNATTRIBUTED | yes | D16 |
| eliminated claims | `rule_out` rows | recorded, never gated; the lookup hook flags each when a later call repeats its statement | yes | `3b964dc` |
| duplicates | a second row with a `unique:` field value copied exactly, and one differing only in case | exact copy refused; case variant accepted (the measured limit, `dff134c`) | yes | V2-20 |
| independence | a proposer promotes its own row; root promotes; a second credential promotes | refused, refused, accepted | yes | D17 |
| readiness: should reach | supported by a confirmed fact, attacked, damaging heads answered, preserved | `ready = true` accepted | yes | |
| readiness: fatal then downgraded | a fatal head, re-stated as survivable by the same lens, then answered | first refused, then accepted; `verdict_history` has exactly one entry | yes | D15 |
| readiness: should not reach | an unanswered damaging head / an unpreserved conclusion / no attack / supported only by a proposed fact | refused each | yes | |
| recorded exception | `preserves{how: not_captured}` | accepted, and the edge value readable | yes | V2-8 |
| lens membership | every labelled unit | each lens view holds exactly the units its standard and filter select, computed by the generator's own reference filter | yes | V2-1 |
| empty inputs | a second run with an empty labels directory | LoadError or no sweep; edge count unchanged | yes | D4 |
| history | every promotion and verdict change in the script | `status_history` / `verdict_history` lengths equal the script's count of real changes; none decrease | yes | D15, `a330b34` |
| label truth | oracle tier, direction and targets per unit | agent labels scored against it | **no**: an estimate (s.7) | #1 lever, METHOD s.11 |

**Scoring.**
- **Deterministic categories:** pass/fail per category, with the diff printed as **expected-not-observed** and **observed-not-expected** id lists. Also print the category's size, so an empty category can never pass vacuously. A generated category with zero planted items is a **generator error**, exit 2 (D10, D13).
- **Overall deterministic result:** all categories pass. There is no weighted score. A weighted score would let a lost correction be averaged away by 40 correct lens memberships.
- **Stochastic category (label truth):** per field, exact agreement and kappa against the oracle, with a bootstrap interval clustered by slice. The relevance split (oracle tier ≥ 2 and agent < 2, or the reverse) is reported as recall and precision **of relevant units**, each with a Wilson interval. Missing a load-bearing unit is the costly error, so recall of oracle tier-3 units is the headline number.
- **Output shape:** one JSON record per run, `{seed, scale, generator_hash, scorer_hash, engine_commit, categories: {name: {planted, expected, observed_missing, observed_extra, pass}}, label_truth: {...}}`, appended to `evals/history/seeded.jsonl`.

**Domain-free and seed-reproducible.**
- The generator emits its own vocabulary, so any engine code that relies on a built-in name (the `cites`-keyed probe, D9) fails on most seeds. INFERRED: D9's fix-ladder defect would have produced `missing_accept_probe` on any seed whose rule reads from a declared table.
- The same seed gives byte-identical output. A test asserts that by generating twice and comparing hashes.

**How it avoids being a test the engine was fitted to pass.** METHOD s.15's fence applies.
1. **The engine never imports anything under `evals/`.** `license_check`-style AST scan: an `groundworks/` import of `evals` fails the suite.
2. **The answer key is computed by a reference model inside the generator, not by the engine.** It is a deliberately naive Python evaluation of each gate over the planted rows. This is differential testing: engine and reference must agree. When they disagree, a human decides which is wrong. Limit (INFERRED): the reference and the engine can share their author's misunderstanding. So the reference is written by a different lane from the one changing the engine, and five hand-built small cases pin it.
3. **Held-out seeds.**
   - `dev` seeds are in the repo, for developing and debugging.
   - The **gate** run uses seeds derived from a salt kept outside the repo, `~/.groundworks/eval_salt`, the same pattern as the privacy deny list (`instance.yaml` line 187). An agent tuning the engine can make the dev seeds pass, and learns nothing about the held-out ones.
   - The salt is rotated when a held-out failure has been looked at, because a seed that has been debugged is a dev seed.
4. **Human-owned files, pinned.** `evals/seeded/generate.py`, `score.py` and the category list are added to `human_owned` and pinned by content hash in `evals/evaluator_pins.json`. `groundworks test` fails if a pinned file changes without the pin being updated, and a pin update is a reviewed diff. This is the gate-baseline pattern again, and it works (`89f7b78`: "unchanged config exit 0; one clause deleted exit 1").
5. **Results are written by the scorer, not by the session.** The agent that ran the experiment never writes `evals/history/`. The scorer appends the record, and the record carries the scorer's own hash.

**What it catches that today's suite does not.**
- Wrong **outputs** whatever their cause: D4, D6, D15, D16. Each shows up as a category diff.
- Vocabulary-shaped engine assumptions that only a non-demo config exposes: D9, D14.
- Suites today assert on hand-picked scenarios, and every scenario so far used the demo's names (CONFIRMED: `tests/test_loader_sweep.py:44` loads `instances/demo-incident`).

---

## 4. Performance and scale

### Baseline, measured 2026-09-16 (CONFIRMED: measurements taken for this document)

**Conditions.**
- **Machine and software:** AMD Ryzen 9 5900XT (16 cores, 32 threads), 64 GB RAM, Windows 11, Python 3.13.5, SurrealDB `3.1.6+20260813.cfbaec4`, in-memory mode, HTTP on 127.0.0.1.
- **Code:** engine at `3c2112f`.
- **Corpus:** the demo corpus multiplied by copying each unit under a new id. The text is unchanged, so the stand-in labeller labels every copy identically: 66 units at ×1.
- **Access:** root, no `access:` roles applied.
- **Ports and cleanup:** a fresh server per scale and run on ports 8050 to 8055, stopped by PID. After each run the script checked the port was free again, and it was.
- **Script:** a throwaway scratch script, not in the repo; its logic is the sketch for `evals/perf/`.
- **Contamination:** another lane was running test servers (8023, 8080) and editing the tree during the ×10 to ×1000 runs. The numbers carry that load, which is itself the noise problem described below. `n` is the repeat count within one server.

Run A: ×1 to ×100 on clean HEAD, ×1000 started on HEAD while another lane was editing. Run B: ×1 and ×100 again, same code (a `git archive` of `3c2112f`), about 30 minutes later, with another lane's test server running. Times in seconds; "med [min–max]" over n repeats.

| Measure | ×1 (66 units) | ×10 (660) | ×100 (6,600) | ×1000 (66,000) |
|---|---|---|---|---|
| schema + gates + temporal apply (n=1) | 2.05 | 1.95 | 2.09 | 2.18 |
| intake / chunk / stub label / validate (n=1) | 0.003 / 0.012 / 0.017 / 0.017 | 0.026 / 0.020 / 0.042 / 0.043 | 0.13 / 0.09 / 0.31 / 0.35 | 1.48 / 0.86 / 3.11 / 3.39 |
| **first load** (units + labels + edges; n=1) | 0.30 (224 units/s) | 1.74 (379/s) | **14.5 (455/s)** | **150.7 (438/s)** |
| **reload, unchanged source** (n=3) | 0.34 [0.32–0.39] | 2.06 [1.89–2.15] | **15.5 [15.3–16.0]** | **160.4 [156.1–164.3]** |
| provenance read alone, `SELECT ... FROM unit` (n=5) | 0.023 | 0.028 | 0.075 | **0.60** |
| **probe run, empty graph** (3 rules + unique; n=5) | 2.07 [2.04–2.11] | 2.02 [1.99–2.12] | 1.98 [1.92–2.07] | 2.13 [1.89–2.39] |
| **probe run, loaded graph** (n=5) | 2.11 [2.09–2.16] | 2.46 [2.26–2.65] | **3.60 [3.49–3.66]** | **18.37 [17.41–18.82]** |
| probe per rule, loaded (median; fact / conclusion / authority / unique) | 0.56 / 0.88 / 0.54 / 0.14 | 0.61 / 1.03 / 0.64 / 0.16 | 1.00 / 1.34 / 0.98 / 0.25 | 5.51 / 5.69 / 5.60 / 1.25 |
| `lens.apply` / `build_framework` (n=1) | 0.42 / 0.07 | 0.92 / 0.46 | 3.19 / 3.01 | **27.8 / 29.8** |
| `view_counts` / `lens_view` limit 50 / `matrix` (n=5 medians) | 0.059 / 0.064 / 0.028 | 0.074 / 0.085 / 0.041 | 0.063 / 0.117 / 0.201 | 0.077 / **0.65** / **2.67** |
| gate write, refused / accepted (root; n=50 medians [max]) | 0.018 [0.032] / 0.019 [0.033] | 0.023 [0.045] / 0.024 [0.041] | 0.019 [0.032] / 0.023 [0.034] | 0.027 [0.032] / 0.022 [0.039] |
| `consistency.report` (50 promoted rows at every scale; n=5) | 0.116 | 0.139 | 0.120 | 0.167 |
| probe rows left behind | 0 | 0 | 0 | 0 |

**Run-to-run noise, same code (run A → run B, medians):** ×1 probes on an empty graph 2.07 → 2.34 (**+13%**); ×1 reload 0.34 → 0.31 (−7%); ×100 first load 14.5 → 15.0 (+3%); ×100 **reload 15.5 → 18.2 (+17%)**; ×100 probes on a loaded graph 3.60 → 4.09 (**+14%**); ×100 gate write accepted 0.023 → 0.020 (−13%). So **unchanged code moved medians by up to 17% between two sittings** on one machine with other lanes active. That is the number the regression rule below is sized against.

**What the numbers say.**
- **Load throughput is flat at about 440 to 455 units/s from ×10 to ×1000, so load is linear.** Each unit also carries a label and about 1.2 edges. At that rate the first instance's 9,101 units (METHOD s.11) would load in about 20 s. INFERRED: the per-unit cost is dominated by HTTP round trips at 50 statements per batch (`loader.py:49`).
- **A reload of an unchanged corpus costs as much as the first load, or slightly more:** 15.5 s against 14.5 s at 6,600 units, 160 s against 151 s at 66,000.
- **The provenance read `a330b34` added is not that cost:** 0.6 s of a 160 s reload at 66,000 units, under 0.5%. INFERRED: the cost is the per-unit guarded `UPDATE ... WHERE status = .. AND grade = ..` plus re-upserting every label and edge. So an unchanged reload is a full rewrite. Skipping unchanged units by content hash (METHOD s.11 lever 4) is the optimisation to measure if reload time ever matters.
- **Probe time is linear in graph size, and it is the finding that matters most here.** On an empty graph it is about 2 s at every scale. On a loaded graph it is 2.1 s, 2.5 s, 3.6 s and **18.4 s** at 66, 660, 6,600 and 66,000 units, and every rule slows by the same amount (about 5.5 s each at ×1000). The probes run on the instance's live database (`run_instance.py:184`, and CI's baseline step), so **an instance's gate check gets slower in proportion to its corpus**. INFERRED cause: each probe statement or cleanup scans a table with no index on the field it filters. UNKNOWN until profiled. It is not a defect yet; it is the first thing a scale regression would hit.
- **Lens building is linear and already slow at ×1000:** `lens.apply` 27.8 s and `build_framework` 29.8 s. `lens_view` with limit 50 grows from 0.06 s to 0.65 s, and `matrix` from 0.03 s to 2.67 s, so both scan the whole view. `view_counts` stays flat.
- **Schema apply costs about 2 s at every scale.** Gate write latency (one guarded `UPDATE`) is about 20 ms median, accepted or refused, and flat to 66,000 units.
- **`consistency.report` stayed about 0.12 to 0.17 s, but only because the promoted set was 50 rows at every scale.** The scale generator multiplied units, not facts. **The generator in `evals/perf` must scale facts, citations and conclusions too.** Otherwise the consistency query and the readiness checks are never measured at scale, which would be a check reporting "fast" over nothing. This baseline has that gap, and says so.

### The tool

```
evals/perf/scale.py    python -m evals.perf.scale --seed 5 --units 66000 --facts 5000 --out DIR   (uses the s.3 generator, not the demo)
evals/perf/bench.py    python -m evals.perf.bench DIR --port 8050 --repeat 5 --json OUT
evals/perf/compare.py  python -m evals.perf.compare OUT.json --history evals/history/perf.jsonl
```

**Numbers worth tracking, per scale (×1, ×10, ×100, ×1000):**
- schema apply;
- intake, chunk and validate throughput;
- first load (units/s), and reload of an unchanged corpus;
- reload with 1% of units changed at source;
- the provenance read on its own;
- probe run on an empty and a loaded graph, **per rule**;
- gate write latency, accepted and refused, p50 and p95 over n ≥ 50, root **and** a record-access credential (PERMISSIONS add work; unmeasured);
- the independence event's cost;
- `lens.apply`, `build_framework`, `view_counts`, `lens_view` (limit 50), `matrix`;
- `consistency.report` with promoted rows scaled;
- `readiness_problems`;
- the lookup hook's p95 (`TUNING.md` line 133 measured about 300 ms on a second instance);
- queue worker pass time per 1,000 changes;
- server RSS after load.

**What counts as a regression, without false alarms:**
1. **Compare on the same machine class only.** Each record carries a machine fingerprint: CPU model, core count, OS, SurrealDB version, Python version. Records from a different fingerprint are never compared.
2. **Record the load around the run.** Before and after, count other listening `surreal` processes and read CPU load percentage. A run with another lane active is stored, **flagged** `contended`, and never used as the baseline. The baseline above is itself contended, so the first real baseline must be taken on a quiet machine.
3. **Use medians of n ≥ 5 repeats, and run the old and the new code interleaved** (A B A B A) in one sitting. Two runs a week apart differ by more than most regressions.
4. **Flag a regression only when both hold:** the new median exceeds the baseline median by more than **25%** (the A/B noise measured above reached 17%), and the fastest new repeat is slower than the slowest baseline repeat (the ranges do not overlap). Then re-run once. It is reported only if it holds again.
5. **Scaling ratios are the machine-independent check.** `time(×100) / time(×10)` should stay near 10 for anything linear. A ratio above 15 on load, reload or probes is a **complexity regression** whatever the absolute numbers. That ratio is safe to run in CI at small scale (×1 and ×10), where absolute timings on a shared runner are not.
6. **Never gate CI on absolute time.** Nightly on a fixed machine only, with the result appended to `evals/history/perf.jsonl`.

**Meta-test.**
- A `--inject-delay loader:0.002` switch in `bench.py` (a sleep per batch in the benchmark wrapper, not in the engine) must be flagged as a regression.
- Five back-to-back runs of unchanged code must produce **zero** flags. Run that A/A test once per machine and store its false-alarm count.

**What it catches.** Nothing in D1 to D22 was a performance defect. One measured slow path nearly was: `TUNING.md` lines 136-144 record a hook whose p95 of 2,156 ms passed its own timeout and returned **silence**, answering on 23% of calls. On this system, **a timeout becomes a silent empty answer**, so a slowdown is a correctness risk and must be tested as one. Section 5 turns a timeout into an injected fault.

---

## 5. Fault injection

**Principle.** Every injected fault has a **loudness oracle**: the run must end in one of
- (a) a named exception type;
- (b) a non-zero exit with a line that names the fault;
- (c) a report whose `ok` or equivalent is false, and that names what did not happen.

And **no** success-shaped output may appear: "done", "PASS", "OK", `misbehaved: 0`, `clean`. The state must be either unchanged or explicitly reported as partial. Without the fault, the same scenario must succeed. Otherwise the test proves nothing (s.9).

```
tests/harness/faults.py        the injectors (standard library)
tests/test_faults.py           one test per row below, each with its no-fault control
```

**Injectors, sketched against today's seams (CONFIRMED seams, nothing in the engine changes):**

| Injector | How | Seam |
|---|---|---|
| `FaultyDb(db, plan)` | a proxy over `groundworks.db.Db`. Per statement index or regex it can raise `DbError`, return fewer result blocks, return `[]` with status OK (the measured denied-write shape), sleep past `timeout`, or run a callback **between** two statements | every engine function takes `db` as a parameter (`loader.load`, `gates.run_probes`, `consistency.report`, `lens.*`) |
| `KillServer(proc, after=...)` | `proc.terminate()` on **our own** Popen handle after N statements or seconds. By PID, never by name | test-owned server |
| `StrangerOnPort(port, when=...)` | a plain `socket` listener bound before the check, or bound in the window between `port_in_use` and `surreal start` (a thread races it) | `run_instance.start_memory`, suite `setUpClass` |
| `FsFault(dir, kind)` | missing directory; empty directory; `.json` instead of `.jsonl`; truncated last line; UTF-8 BOM; a file written in cp1252 (D21); a zero-byte file; a directory where a file is expected | intake, loader, validator, plant, score, metrics |
| `SchemaReapply(db, inst)` | re-apply `schema_surql` with `DEFINE TABLE OVERWRITE`, which drops the CHANGEFEED on 3.1.6 (`9957070`) | queue worker |
| `LaneKill(after_rows=k)` | a scripted lane over `Writer` that raises `SystemExit` after k writes, standing in for a usage limit | `pipeline.write.Writer` |
| `NeuteredAdapter` | an adapter whose `scenario()` does nothing or reports steps without calling the writer | `run_instance.scenario_problems` |

**The scenarios, each tied to a defect:**

| Fault | Expected loud outcome | Defect |
|---|---|---|
| labels directory missing / empty / only `.json` | `LoadError`, or `counts["sweep"]` starts `SKIPPED`; `bears_on` count unchanged | D4 |
| `FaultyDb` raises on batch k of the edge writes | exception propagates; sweep `SKIPPED`; earlier edges intact | loader contract |
| `FaultyDb` returns fewer blocks than statements | `LoadError("batch returned ...")` | CLAUDE.md rule 5 |
| **correction lands mid-load**: callback runs `correct_unit` after the provenance read and before the first unit `UPDATE` | `LoadError` naming the unit, "changed between read and write"; the correction survives | D16 |
| stranger binds the port before the check | `NOT EXECUTED: port N is already in use`; the stranger's server still running afterwards | D3 |
| stranger binds **between** check and start | `start_memory` refuses after `SETTLE`. The residual window is recorded as UNKNOWN width (`8a73fa4`). The test measures how often a 1 ms race still slips through over 200 trials, and **reports the rate**, not pass/fail. **The suites' own `setUpClass` has no settle check** (CONFIRMED: `tests/test_loader_sweep.py:51-67` polls health and never checks `proc.poll()` after it, and no suite under `tests/` calls `poll()` except `test_port_guard.py`, which tests `start_memory` itself), so this scenario is expected to find that gap | D3 |
| server killed mid-load | `DbError` "cannot reach"; no success line; next load reports what it found | new |
| server killed mid-probe | run reports `setup_error` for that rule and `misbehaved > 0`; probe rows from the killed run are reported by the next run's leftover check | `b52ea90` item 3 |
| schema re-applied; queue worker pass | `missing_changefeeds` names the table as a counted error; no versionstamp stored | D12 |
| queue state file deleted | report says `started fresh` and how many entries were jumped | D12 |
| lane killed after k of n writes (usage limit) | exactly k rows persisted; **no** row promoted by the killed verifier stage; with `independent: true`, the proposer's own promotion refused | D17 |
| neutered adapter | `SCENARIO NOT EXECUTED`, exit 1 | D6 |
| hook query exceeds its timeout | the hook prints a warning, never an empty "nothing known" (`TUNING.md` line 134) | hooks |
| database stopped at session start | `health.py` prints WARNING | `3b964dc` |
| a label file in cp1252 with a curly apostrophe | validator reports the quote as not verbatim, or intake refuses to decode; never silently passes mojibake | D21 |
| privacy scan over a tree holding the identifier in every extension the scan knows, plus an unknown one | named in the result; exit non-zero | D7 |
| the SurrealDB binary absent in a suite run with `--require-db` | FAILED, not OK | D5 |

**What it catches that today's suite does not.** Today each failure mode has at most one hand-written test, added after the defect bit. The harness makes "what happens when this dies halfway" a standing question for every write path. The mid-probe kill, the mid-load kill and the suites' settle gap are **not tested anywhere today** (CONFIRMED by reading `tests/`; INFERRED for the first two being loud).

---

## 6. Property-based and invariant testing (standard library only)

**Generator.** `tests/harness/gen.py`, built on `random.Random(seed)`, shares its config and corpus grammar with s.3's generator so there is one generator to trust. Strategies:
- `gen_config(rng)`: a valid `instance.yaml` dict with random names, 1 to 5 rules, optional chains, `forbids`, `unique` and `independent`. A generated config is **checked by `config.load` before use**, and a generated config that the loader refuses is counted and reported, not dropped. If more than 5% are refused, the generator is broken.
- `gen_corpus(rng, cfg)`, `gen_labels(rng, corpus, noise)`, `gen_ops(rng, cfg, k)`: a random sequence of Writer and raw-SQL operations, including corrections and reloads.

**Runner.** `check(prop, gen, n=200, seed=S)`:
- a failing case is **shrunk** by greedy deletion, removing one list element, clause, rule or op at a time while the property still fails;
- the minimal case is written to `tests/harness/property_failures/<prop>_<seed>.json`;
- **a saved failure is replayed on every run forever.** That is the Lessons loop of METHOD s.15, and a failure found once becomes a permanent regression entry.
- Each run prints `PROPERTY name: n generated, n checked, 0 discarded`. A property that discarded everything is a failure (D10).

**Invariants: pure, no server (fast; n = 200 to 1000 each):**

| Invariant | Function | Defect class |
|---|---|---|
| every unit lands in exactly one slice, for any chunk size and `chunk_by` | `chunker.chunk` | coverage |
| oracle labels validate clean; each single planted defect yields at least one error naming that unit | `validator.validate` | D13 |
| `compare_baseline(spec(c), report, spec(c)) == []` for any valid c; **for every weakening w (drop a rule, a clause, a `where`, lower `min`, repoint `value`, drop `unique` or `independent`), `compare_baseline(spec(c), ..., spec(w(c)))` is non-empty**; adding a rule or clause is never a failure | `gates.spec`, `compare_baseline` | D1, D2 |
| `reconcile`: a held field is never overwritten unless its pair is in `accept`; every held value that differs from the source is in `conflict`; an unheld field always follows the source and a change is in `followed`; applying the plan and reconciling again with the same source is a no-op | `loader.reconcile` | D16 |
| `kappa` is symmetric, in [-1, 1] or None; None iff chance agreement is 1; identical labels with at least 2 values give 1.0; permuting unit order changes nothing | `agreement.kappa` | D11 |
| `preservation(rows, render(rows, mode))` is 100% with `checks.ran == checks.declared` for full modes; **zero rows raises or reports `ok False`** | `present.metrics` | D10 |
| `score(manifest, manifest-as-findings)` recall and precision 1.0; `score(manifest, [])` recall 0; every planted row differs from truth in exactly its manifest field | `present.plant`, `score` | D13 |
| `gate_expression` never references an edge or table name not in `inst.edge_map()` / `inst.node_tables()` | `gates` | D9 |
| every `probes(inst)` rule with a constrained clause yields a wrong-evidence plan or a named reason; every `forbids` clause yields a forbidden plan or a named reason | `gates.probes` | D1, D9 |

**Invariants on a real server (slower; n = 10 to 30 generated configs per nightly run):**

| Invariant | Defect class |
|---|---|
| `run_probes` on any generated config: `misbehaved == 0`, and **row counts of every table are equal before and after** ("probes leave no rows behind") | D1, D14, `fa0c668` |
| for every generated weakening, `run_probes` plus `compare_baseline` flags it | D1 |
| **no promoted row without its gate's evidence**: after any op sequence, every row at a gated value either satisfies its gate or is listed by `consistency.report`, and the listed set equals the reference model's | gates, V2-19 |
| **reachability (liveness)**: for every gated value there is an engine-exposed write sequence that reaches it, and every blocking critique verdict has an engine-exposed path to non-blocking | **D15**, which no safety property can catch: the defect was an *unreachable* state |
| every unit labelled exactly once after load (`count(label) group by unit == 1`) | coverage |
| a reload with an unchanged source: `followed == conflicts == swept == 0` and edge count unchanged (idempotence) | D4, D16 |
| after `correct_unit` and any number of reloads with any source values, the corrected field is unchanged unless named in `accept_source` | D16 |
| `status_history`, `verdict_history` and unit histories never shrink across any op sequence, and `by` is never the row's `author` unless that author made the change | `a330b34`, `7733fd5` |
| with `independent: true`, no op sequence lets a credential on `written_by`, or root, set the gated value | D17 |
| lens view membership equals the reference filter for any config | V2-1 |

**What it catches that today's suite does not.** Today every test uses a config a human wrote, almost always the demo. D9 and D14 were found only when someone wrote a *different* config. Random configs turn "someone writes a different config" into a nightly event. The weakening property generalises the four baseline blind spots of D2 from four hand-written cases to every weakening the grammar can express. The reachability property is the only test design here that would have found D15.

---

## 7. Agent and LLM evaluation done properly

**One harness, one store, three evals.** Today agreement (`groundworks/pipeline/agreement.py`), planted-error review (`groundworks/present/plant.py` + `score.py`) and retrieval (`groundworks/embed/bakeoff.py`) are separate tools with no shared history (CONFIRMED). Keep each tool. Add:

```
evals/stats.py            wilson(k, n), bootstrap(values, clusters=None), paired_bootstrap, kappa_ci, power_two_proportions
evals/prereg/<id>.json    written BEFORE the run: hypothesis, arms, primary metric, unit of analysis, n per arm, decision rule
evals/record.py           python -m evals.record --prereg evals/prereg/V2-7b.json --result score.json
evals/history/*.jsonl     append-only; one record per run
evals/gate.py             python -m evals.gate --metric reviewer.status_upgrade.recall --baseline evals/baselines.json
```

**A record carries:** eval id, prereg hash, eval-set hash, engine commit, guide or prompt hash, model id, date, n (and the unit of analysis: reviewer, unit, query), point estimate, 95% interval, the unflattering counts (false alarms, `kappa_undefined`, queries without a relevant unit), `contended` flag, the scorer's hash, and `decision: adopted | not adopted | no evidence`.

**Rules the harness enforces, not the author:**
1. **No record without a prereg.** `evals.record` refuses a result whose prereg file is newer than the run's first output.
2. **Always report the interval.**
   - Proportions: Wilson, e.g. 0 of 8 is [0.00, 0.32] and 2 of 8 is [0.07, 0.59]. Those overlap almost entirely, which is the V2-7 conclusion computed rather than argued.
   - Means: bootstrap. `bakeoff.py:153` already does this for retrieval, and its "better" rule is the right shape: interval above the baseline **and** paired difference above zero (`bakeoff.py:252-253`).
3. **Name the unit of analysis, and cluster the interval by it.** `marker_wording.md` line 127 says "reviewer-level ... is the honest unit". Catches within one reviewer are not independent, so the interval is a cluster bootstrap over reviewers, and over slices for labels.
4. **Minimum n before a claim counts.** The prereg states the smallest effect worth acting on, and `evals.stats.power_two_proportions` gives n. For the status-upgrade question (0.10 against 0.40), that is about **32 reviews per arm**. V2-7 had 8. A run below its own prereg n is recorded as `underpowered`, and its decision is forced to `no evidence`.
5. **Repeated runs.** A claim about an agent is never made from one run of one seed. At least two seeded copies, with targets that differ between copies. `plant.py`'s deterministic targeting made both copies upgrade `d2` and `d3` (`marker_wording.md` line 193), so the planter needs a seed-dependent target choice. That is a change to `plant.py`, and it is recorded here, not made.
6. **Regression gate on quality.**
   - For each tracked metric, a baseline record is pinned in `evals/baselines.json` (human-owned).
   - `evals.gate` fails **only** when the new interval's upper bound is below the baseline interval's lower bound.
   - Overlapping intervals print `NO EVIDENCE OF CHANGE (n=..)`, never PASS.
   - For a change meant to be neutral (a cheaper model, a shorter prompt), the prereg states a non-inferiority margin, and the gate checks the new lower bound against baseline minus margin.
7. **A/A and anchors.** Each eval set carries:
   - an **oracle arm** (a reviewer or labeller given the manifest or truth), which must score recall 1.0;
   - a **null arm** (random or empty findings), which must score at chance;
   - once per new eval set, an **A/A** run (two identical arms), which must not report a difference.

   If any of these fails, the eval is broken, not the system.
8. **Guarding against gaming.**
   - Eval sets are held out, as in s.3: dev copies in the repo, gate copies seeded from the out-of-repo salt.
   - The manifest never reaches a reviewer. `plant.py` already says "Keep the manifest away from reviewers", and it must be enforced: the reviewer's workspace is built from a file list that excludes it, and the lookup log proves which files were opened (`lookup.py`).
   - The prompt-tuning session cannot write `evals/`, which is added to `human_owned`.
   - Any metric that a judged model scores sits **behind** a mechanical gate (METHOD s.8: "mechanical gates are law, judged scores are advice").
9. **The evaluator's own inputs are checked.** D21 put mojibake into every arm. `evals.record` requires the rendered materials' hash and a UTF-8 decode-and-re-encode check before a run counts.

**The two negative results are the model, and the harness should make them the default output.** Both notes state n, call a two-reviewer difference noise, name the defects in their own harness, and adopt nothing (`notes/2026-09-15_reading_layer_evaluation.md` lines 48-55; `notes/2026-09-16_marker_wording.md` lines 183-231). The record format above forces the same fields.

### Label agreement on a real instance (owed; METHOD s.11 lever 1)

**Status.** `agreement.py` exists and is tested. The only measurement so far is agents against the keyword stub on 63 demo lines (tier kappa 0.93), recorded explicitly as "a harness proof, not a label-accuracy measurement" (CONFIRMED, `8cfa578`). The config key `evaluator.label_agreement` is never read (CONFIRMED, s.3).

**Procedure.** It runs **inside the private instance's own repo**. Only aggregate numbers and the method come back here, never units, labels or text (CLAUDE.md rule 1).
1. **Sample.**
   - Stratified by unit type and by the first labelling's tier.
   - Tier-0 units dominate a corpus. A uniform sample measures agreement on noise and produces a high kappa that says nothing about load-bearing reading. So tier ≥ 2 units are oversampled, and the weights are reported.
   - Size: kappa's standard error is about `sqrt(p_o(1 - p_o) / (n (1 - p_e)^2))`. At p_o ≈ 0.9 and p_e ≈ 0.6, a ±0.1 half-width needs **about 220 units**. The demo config's `sample_chunks: 2` is not a sample size, so the key should become `sample_units` with a stated target interval.
2. **Two independent labellings.**
   - Separate sessions, no shared context.
   - The same guide version, pinned by hash in the record.
   - The labeller that did the production pass must not be one of the two, or else be recorded as such.
   - Same-model labellers agree on shared mistakes. Where possible use a second model, and report both pairings.
3. **Compute** with `groundworks labels agreement A.jsonl B.jsonl --out`: exact agreement, kappa with a cluster-bootstrap CI over slices, `kappa_undefined`, and the relevance split.
4. **Adjudication measures truth, not agreement.**
   - A human adjudicates every relevance-split unit **and a random 10% of agreed units** against the source.
   - Report **accuracy of each labeller against adjudication**, with Wilson intervals.
   - Agreement without accuracy is two agents being wrong together.
5. **Record** the result with the prereg, the guide hash, model ids and n. Then answer the open question in `DECISIONS.md` line 47 ("Label quality threshold that makes a labeled corpus 'trusted as a map'") with the number, owner-signed.
6. **Re-run when the guide or the model changes,** and gate on it like any other quality metric.

---

## 8. Mock and public data

**Order of preference.**
1. **Synthetic, generated (s.3).** No licence question, no privacy question, infinite seeds.
2. **Public corpora with ground truth, under a licence the policy allows,** for the stochastic evals, where synthetic text is too easy.
3. **Never** client or matter data. The first real instance is private and is not a fixture, not a sample and not an eval set in this repo (CLAUDE.md rule 1; METHOD s.16).

**A gap in the policy, stated first.** `docs/LICENSING.md` s.6's class table names software licences only: MIT, Apache, BSD, ISC, PostgreSQL, public domain, MPL, LGPL, GPL, AGPL, BSL, SSPL, Elastic, CC BY-NC, "TBD" and no licence (CONFIRMED). It does not classify **CC BY 4.0**, **CC BY-SA** or **ODC-By**, which is what datasets use.

Recommendation for the owner to decide:
- **CC BY 4.0 and ODC-By 1.0:** treat as the permissive class, with attribution in `THIRD_PARTY_NOTICES.md`.
- **CC BY-SA:** treat as copyleft data. Usable as an **external, downloaded, uncommitted** evaluation fixture; never committed into this proprietary repo and never shipped. Its share-alike term could reach anything distributed as an adaptation.

Until the owner decides, every dataset below is **"usable pending owner decision"** at best, and nothing is committed; an adapter downloads into a gitignored cache. INFERRED reading of CC terms; LAWYER if a dataset is ever shipped.

| Corpus | What it exercises | Licence as read | Tag | Verdict |
|---|---|---|---|---|
| **SciFact** (allenai/scifact) | scientific claim against abstract: SUPPORTS / REFUTES / no evidence, with **rationale sentences**, a direct map to unit, fact, cites and confirmed / ruled_out, plus the verbatim quote check | repo `LICENSE.md`: claims **CC BY 4.0**, abstracts **ODC-By 1.0**, code Apache 2.0 | CONFIRMED (read the repo's LICENSE.md) | **Usable** (permissive class, pending the owner's CC BY decision). Best first public eval |
| **ContractNLI** (Stanford) | document-level entailment / contradiction / not-mentioned per hypothesis, with **evidence spans**, which tests negative knowledge ("not mentioned" against "contradicted", the `not_in_record` / `ruled_out` distinction in `PATTERNS.md`) | dataset page: "Our dataset is released under CC BY 4.0" | CONFIRMED (read the page) | **Usable** (permissive pending decision). Legal-domain text: fine as public data, but it must be run as an **instance** whose domain words stay in its `instance.yaml` (CLAUDE.md rule 9) |
| **CUAD** (Atticus Project) | clause extraction over 510 contracts, 41 labelled categories: exhaustive reading and coverage recall | atticusprojectai.org/cuad: "CC BY 4.0", plus "use ... is subject to our privacy policy and disclaimer" | CONFIRMED for CC BY 4.0; the privacy policy and disclaimer **not read** (UNKNOWN whether they add terms) | **Usable pending** a read of the disclaimer |
| **QASPER** (allenai) | questions over full papers with **evidence paragraphs** and unanswerable questions: retrieval bake-off and "silence is not no" | Hugging Face card licence field: CC BY 4.0 | CONFIRMED (read the card) | **Usable** (permissive pending decision). A good second retrieval set with paraphrased queries, which `DECISIONS.md` line 31 says the bake-off needs |
| **FEVER** (fever.ai) | claim verification against Wikipedia with evidence sentences; large; three-way labels | licence page: annotations under "the license terms described on the applicable Wikipedia article pages, or ... Creative Commons Attribution-ShareAlike License (version 3.0)" | CONFIRMED (read the licence page) | **Share-alike: external, uncommitted fixture only**, pending the owner's decision |
| **HoVer** | many-hop verification (2 to 4 evidence documents): chained evidence, like the chained gates in D14 | project page: "distributed under a CC BY-SA 4.0 License" | CONFIRMED (read the page) | **Share-alike: external only**, pending decision |
| **VitaminC** | contrastive claim pairs from Wikipedia **revisions**: stale and superseded evidence, the temporal axis | Hugging Face card licence field: cc-by-sa-3.0; the GitHub README states no data licence | CONFIRMED (card), with the README silent | **Share-alike: external only**, pending decision. The strongest public test of stale evidence |
| Climate-FEVER | claim verification, climate domain | Hugging Face card licence field: "unknown"; the GitHub README states none | CONFIRMED that no licence is stated | **Not usable** (no licence = ideas only, LICENSING s.6) |
| Loghub (log datasets) | real system logs, which would suit an incident-style instance | README: "freely available for research or academic work" | CONFIRMED (read the README) | **Not usable** (research-only is the non-commercial class) |
| Enron email corpus (CMU) | a large real corpus for scale tests | CMU page: distributed "as a resource for researchers"; no licence text; messages about real, uninvolved people | CONFIRMED that no licence is stated | **Not usable** (no licence), and a privacy hazard regardless |
| MS MARCO, AVeriTeC | retrieval; real-world claim verification | not read in this session | UNKNOWN (believed non-commercial; not verified) | **Do not use until the licence is read** |

**How a public corpus enters.**
- It enters as an instance under `instances/public-<name>/`, with an adapter that downloads to a gitignored cache, checks the file hash, and refuses to run without it (NOT EXECUTED, never an empty corpus).
- Its ground truth converts to the s.3 `truth.json` shape for the categories it can express: confirmed, ruled out, not in record, and evidence ids.
- `scripts/license_check.py` gains a dataset list, so an adapter naming an unlisted or banned corpus fails at commit, the same way a banned model does today.

---

## 9. The meta-test: how do we know the harness works?

**Rule.** Every harness component ships with a **known-bad input it must go red on**, and a **known-good input it must go green on**. Both run whenever the component runs, and the component's own summary line reports both. A harness whose canaries did not run reports NOT EXECUTED.

| Component | Must go RED on | Must go GREEN on | Must say NOT EXECUTED on |
|---|---|---|---|
| mutation runner | a **canary survivor**: a fixture test that asserts nothing, registered against a real mutation, must be reported SURVIVED; a comment-only replacement must be reported INVALID; an anchor occurring twice must be refused | a canary mutation of `kappa` returning 1.0 on one value, killed by the existing test (D11) | an occupied port; a `must_fail` test that skips unmutated |
| seeded scorer | observed == truth with one item removed from **each** category in turn: exactly that category fails; an observed file with every category empty: every category fails | observed == truth | a truth file with an empty category (generator error) |
| perf compare | an injected per-batch delay | five A/A runs | a record from a different machine fingerprint |
| fault harness | the **pre-fix** tree for each defect it covers (below) | the same scenario with the fault switched off | a control that fails without the fault |
| property runner | a planted buggy function (kappa 1.0 on degenerate input; `reconcile` that overwrites held values) found within n and shrunk to one or two units | the real functions | a generator whose cases were all discarded |
| eval harness | a null arm that scores above chance; an oracle arm below 1.0; a prereg dated after the run | the oracle arm at 1.0 | a run below its prereg n (recorded `underpowered`) |

### Defect replay: the commit log as the harness's own test set

```
python -m tests.harness.replay --defect D4    # builds the tree at 34960cf^ with git archive into a temp dir, runs the D4 scenario, requires RED
python -m tests.harness.replay --all          # every defect with a replay entry, at its parent commit (RED) and at HEAD (GREEN)
```

- **Registry entry:** `{defect, fixed_in, scenario, component}`. The component is `fault`, `seeded`, `property` or `mutation`.
- **How the tree is built:** `git archive`, **not** `git checkout` or `git stash`, so the live tree and index are never touched.
- **Candidates with written reproductions** (CONFIRMED that each commit message states the reproduction):

| Defect | Pre-fix tree | Replay scenario |
|---|---|---|
| D4 | `34960cf^` | empty labels directory |
| D5 | `6c54f25^` | CI without pipefail; lint on the workflow |
| D6 | `6c54f25^` | neutered adapter |
| D7 | `6c54f25^` | privacy fixture in `.csv` |
| D10, D11, D13 | `277eded^` | the empty-input properties |
| D14 | `fa0c668^` | chained-gate config |
| D15 | `7733fd5^` | reachability property |
| D16 | `a330b34^` | correct, then reload |
| D17 | `dff134c^` | self-promotion |
| D18 | `8a73fa4^` | hygiene check |

- **If a harness goes GREEN on a pre-fix tree, it would not have caught that defect.** That is the measurement that decides whether the harness earns its place. It is also the only honest answer to "how many past defects would this have caught?", which this document otherwise answers by inference.
- **Limit (INFERRED):** replay proves the harness catches defects **already found**. It cannot prove it catches the next class. The mutation registry and random configs are the parts aimed at defects no one has found yet.

---

## 10. Prioritised build plan

**How the plan is ordered.**
- **Effort** is engineer-days, INFERRED from the size of comparable commits.
- **Caught** means defects from s.0 the item would have caught, INFERRED until the item's replay (s.9) runs. **After building, replace every INFERRED count with the replay result.**
- **Now** means buildable today. **Depends** names what must exist first.

| # | Item | Effort | Caught (INFERRED) | The one test that shows it works | When |
|---|---|---|---|---|---|
| 1 | **Mutation runner + registry**, seeded with the ~64 mutations already described in commit messages and `breaks.py`; `lint` in CI, changed-file subset in CI, full set nightly | 2 d | D2, D8, D18, D19 directly. Every future guard stays guarded: the method already relies on this; it is only automated here | the canary survivor (a test asserting nothing) is reported **SURVIVED** and the run exits 1 | **Now** |
| 2 | **Wire the declared evaluator**: a runner for `evaluator.regression_file` (a known-errors file of planted misquotes and past errors, each with its expected refusal); create the demo's `evals/known_errors.json`; config refuses a regression file that does not exist | 0.5 d | D22's class in the evaluator itself: METHOD's Lessons loop runs for the first time | delete the demo's regression file: `groundworks test` goes FAILED, naming it | **Now** |
| 3 | **Fault harness, loader and environment half**: `FaultyDb`, `FsFault`, `StrangerOnPort`, correction-mid-load, server-kill-mid-load; **plus a test that every suite's `setUpClass` checks its own process after the health poll** | 2 d | D3, D4, D16 (mid-load), D20, D21; likely finds the suites' missing settle check (s.5) | a stranger bound between the port check and `surreal start` in a **suite** setUp is reported, not borrowed | **Now** |
| 4 | **Defect replay** for the ten reproducible defects in s.9, using items 1 and 3 as scenarios | 1.5 d | proves the others: the result is a measured count, not an inferred one | the D4 scenario is RED at `34960cf^` and GREEN at HEAD | Depends on 1 and 3 |
| 5 | **Pure-function properties** (s.6, no server): `compare_baseline` weakening, `reconcile`, `kappa`, `chunk`, `preservation`, `plant`/`score`, `probes` plan completeness; shrinker; saved failures replayed | 2 d | D1, D2, D9, D10, D11, D13, D16 (logic half) | planted `kappa` returning 1.0 on one value is found and shrunk to a single-valued pair list | **Now** |
| 6 | **Seeded instance generator + deterministic scorer** (s.3, every category but label truth); random vocabulary; reference gate model; evaluator pins; held-out salt | 4 d | D4, D6, D9, D14, D15, D16, D17 as output diffs | observed == truth minus one row of `corrections`: **only** `corrections` fails, naming the unit | Depends on nothing; shares a grammar with 5, so build 5's generator first |
| 7 | **Server invariants** (s.6 second table), including **reachability** and "probes leave no rows" over generated configs, nightly | 2 d | D1, D14, D15, D17 | replay at `7733fd5^`: reachability reports "critique verdict fatal has no path to non-blocking" | Depends on 6 |
| 8 | **Eval harness**: `evals/stats.py`, prereg, history store, quality gate, oracle/null/A-A arms; retrofit V2-7 and the bake-off as the first two records | 2 d | not an engine defect class. Stops the next reviewer study reading 2 of 8 as a result, and makes an underpowered run say so | the recorded V2-7 status-upgrade comparison prints `NO EVIDENCE OF CHANGE (n=8, 0/8 [0.00,0.32] vs 2/8 [0.07,0.59]; prereg n for Δ=0.3 is 32)` | **Now** |
| 9 | **Perf harness**: scale generator scaling facts and conclusions too, bench, compare with fingerprint and `contended` flag, A/A false-alarm check, nightly on a fixed machine; scaling-ratio check at ×1 and ×10 in CI | 1.5 d | no past defect. Guards the "timeout becomes silence" hook failure (`TUNING.md` lines 136-144) and the probe-time growth measured above | injected per-batch delay flagged; five A/A runs flag nothing | Depends on 6's generator for fact scaling; a unit-only version is **now** (the scratch script) |
| 10 | **Fault harness, lanes and queue half**: lane killed after k writes, schema re-apply dropping the changefeed, queue state loss, mid-probe server kill | 1.5 d | D12, D17 (stage death) | lane killed before the verifier stage: no row reaches the promoted value, and the proposer's own attempt is refused by the independence rule | Depends on 3; the independence part needs a generated config with `access:` (6) |
| 11 | **Owner decision on dataset licence classes** (CC BY, ODC-By, CC BY-SA) in LICENSING s.6; then SciFact and QASPER adapters with a download cache and dataset entries in `license_check` | 0.5 d owner + 2 d | none of D1 to D22. Gives the stochastic evals text that is not trivially keyword-matchable | an adapter run with the cache absent says NOT EXECUTED, never a 0-unit corpus | **Depends on the owner** |
| 12 | **Label agreement on the real instance** (s.7 procedure), run privately; aggregate result recorded here | 1 d of lane time + human adjudication | answers `DECISIONS.md` line 47 and METHOD s.11 lever 1 | the adjudicated sample's accuracy interval is reported beside kappa; a sample with one tier value reports `kappa_undefined`, not 1.0 | **Depends on the instance owner, a pinned guide, and a human adjudicator** |
| 13 | Seeded-instance **label-truth** category scored against real agent labellers, on held-out seeds | 1 d | label quality on synthetic text: an upper bound on real accuracy, stated as such | the noisy labeller at a known 15% tier error rate is measured within its interval | Depends on 6 and 8 |

**What is deliberately not in the plan.**
- A coverage target (s.1).
- Generic mutation tools, for the reason in s.1.
- Docker-based test environments (`9957070`).
- Mocks of datastore behaviour that has not been measured on a real server.
- Anything that lets an agent change the evaluator, the registry, the pins or the baselines without a human (METHOD s.15).

**Ownership.** Items 1, 2, 4, 6, 8, 12 and their data files join `human_owned`. The rule for all of them is the rule that already governs `gate_baseline.json` and the regression suite: **entries may be added by anyone; removing or loosening one takes a human; every regenerated file is proven to still fail before it is committed.**
