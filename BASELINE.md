# BASELINE — the engine's pre-change truth (W0)

**Read this first if you have none of the context.** This file is the BEFORE picture for the
Groundwork workstream (`_tickets/WORKSTREAM-groundwork.md`, "THE ADDITIVE RULE"). The owner's binding
constraint is *"make sure we don't lose our engine/methods — this should be additive features."*
That is only enforceable against a recorded baseline, so every later phase (W1 merge, W2 rename, the
correctness fixes, the Rust work) re-runs the commands below and compares to the numbers below.
A phase that cannot reproduce them is a regression and stops the workstream.

**Tree this baseline describes:** worktree `C:/Users/trist/projects/xscholar-w0`, branch `w0/baseline`,
HEAD `0af87fec06b28a6d32c96ba8e38642389c468b42` (the pre-rename commit; the primary checkout's `main` has
since moved to the rename commit, which is name-only, so these numbers remain the comparison point).
Raw command output for every row is under `C:/Users/trist/projects/_tickets/baseline/W0/*.txt`
(`_tickets` is not a git repo — local only).

**Every command was run TWICE.** Where two runs differ only in an embedded elapsed-time number, that is
recorded as identical. Where they differ in anything else, that is a FINDING and it is named in section 2.

---

## 0. What the real gate is (and what it is not)

This project does **not** use pytest. CI (`.github/workflows/ci.yml`) is the gate, and it has six
substantive steps. Run them in this order, from the repo root, with a SurrealDB binary on PATH.

> **pytest-only quirk, do not "fix" it:** `python -m pytest -q` stops at collection with 4 errors,
> all cascading from the single unqualified sibling import at `tests/test_pipeline_db.py:27`
> (`from test_pipeline import ...`), because pytest puts the repo root on `sys.path` rather than
> `tests/`. Under the project's own runner (`unittest discover -s tests`) `tests/` IS on the path and
> all 752 tests import and run. This is a pytest artifact, not an engine defect. Do not switch the
> project to pytest and do not change that line as part of a Groundwork phase.
> Raw: `xscholar_full_run{1,2}.txt` (4 errors, identical set, both runs).

## 1. The six CI steps — exact command, exact expected result, how to tell it regressed

| # | ci.yml | Exact command (repo root) | Run 1 | Run 2 | Run 3 | How to tell it regressed |
|---|---|---|---|---|---|---|
| S1 | `:48` | `python -m xscholar.cli test --require-db` | **FAILED**, `Ran 752 tests in 2056.688s`, `FAILED (errors=4)`, exit **1** | **OK**, `Ran 752 tests in 1752.159s`, `752 ran, 0 skipped`, exit **0** | **OK**, `Ran 752 tests in 1122.588s`, `752 ran, 0 skipped`, exit **0** | `752 ran` must not drop, `0 skipped` must stay 0 (a skip under `--require-db` is a failure by design), and the final CHECK/RESULT table must show `license check`, `drift check`, `mutation lint`, `evaluator`, `privacy scan` all `passed`. **The 4 errors in run 1 are the named flake in section 2, not a baseline failure.** |
| S2 | `:59`/`:62` | `python -m tests.harness.mutate run --from HEAD` (CI's push/PR arm adds `--changed-since "$BASE"`) exit **0**, 985 s: `preflight 223 test(s) on the unmutated copy`, `canaries 8 behaved`, `MUTATIONS 243 declared | 243 selected | 243 killed | 0 killed-by-timeout (expected) | 0 SURVIVED | 0 INVALID | 0 STILLBORN | 0 PREFLIGHT FAILED | 0 TIMEOUT | 0 NOT EXECUTED` | exit **0**, 1042 s, **same verdict line, same 243 killed** | — | `243 declared` must not drop, and `SURVIVED / INVALID / STILLBORN / PREFLIGHT FAILED / TIMEOUT / NOT EXECUTED` must all stay `0`. A registered break that stops going red means the guard it pins is gone. The 8 canaries must all report `behaved`. |
| S3 | `:67` | `python -m xscholar.drift instances/demo-incident/instance.yaml instances/demo-incident/skills instances/demo-incident/README.md instances/demo-incident/skills/red-team/SKILL.md` | `drift check clean: 1 skill file(s), 22 config item(s), 2 doc(s) naming 13 lens/seat mention(s), 0 drift(s)`, exit 0 | identical, byte-for-byte | — | Any non-zero drift count, or a *smaller* count of files/items/mentions checked — a step that checks less is the exact failure mode this step was written against. |
| S4 | `:69-83` | `surreal start --bind 127.0.0.1:8040 --user root --pass root memory &` then `python -m xscholar.gates instances/demo-incident/instance.yaml --apply --url http://127.0.0.1:8040/sql --baseline instances/demo-incident/gate_baseline.json` | exit 0, `BASELINE: unchanged or stronger (0 difference(s) that make a gate weaker)` | identical, byte-for-byte | — | **This step IS the additive rule, mechanized.** Any `difference(s) that make a gate weaker`, or any non-zero in `PROBES ... misbehaved` / `FORBIDS ... left untested` / `UNIQUE ... misbehaved` / `ORIGINALS ... misbehaved`. |
| S5 | `:85` | `python scripts/run_instance.py instances/demo-incident --memory 8025` | exit 0 | identical, byte-for-byte | — | `scenario 16 step(s) reported, 19 call(s) through the typed write surface, 5 refused by a gate, 3 refused by permissions`; `live schema 176 definition(s) expected, 176 live, 0 drift row(s)`; health `units 66, labels 66, units_with_verified_original 66, gap_units_without_verified_original 0`, every `DRIFT_*` key `0`. **Fewer refusals is a regression, not an improvement.** |
| S6 | `:106` | `python -m tests.harness.mutate run --from HEAD` (the nightly `mutations` job — the same command as S2, in a separate job with no `--changed-since`) | see S2 | see S2 | — | same as S2. |

**Byte-identity evidence (sha256 of the raw output files, run 1 vs run 2):**

| Step | sha256 of run1 | run2 | identical? |
|---|---|---|---|
| S3 drift (`drift_ci_run{1,2}.txt`) | `fe01ec5f1fa26e05c6dee7c189358f9ff2ef6194972948edd865d8ca72bb24c7` | same | **yes** |
| S4 gates/baseline (`gates_baseline_ci_run{1,2}.txt`) | `02d5cbe88b7bdb1844f8b76cd4192069931c2044666bcd2e4d30334463caeb10` | same | **yes** |
| S5 run_instance (`run_instance_ci_run{1,2}.txt`) | `6114468085cc4b7e628723341b1f9ba974a2131b9c271597e46608a848d91ebd` | same | **yes** |
| S2/S6 mutation registry (`mutate_full_run{1,2}.txt`) | — | — | **verdict-identical**, see below |
| S1 suite (`cli_test_run{1,2,3}.txt`) | — | — | **NO — see section 2. Run 1 red, runs 2 and 3 green; 2 of 3.** |

**Two harmless differences between S1 runs, so nobody chases them as regressions:**
- The `license check` line counts **132 config file(s)** in runs 1-2 and **124** in run 3. Runs 1-2 were
  taken in the primary checkout, run 3 in this worktree at the same commit; the extra 8 are generated
  instance output (`instances/demo-incident/out`) left behind by earlier S5 runs, not tracked code. The
  numbers that must not move are `107 python file(s)`, `1075 import(s)`, `7 third-party ... against 5
  listed name(s)`, `0 problem(s)`.
- `MUTATION LINT ... tree worktree (<hash>)` prints a per-tree hash that differs between checkouts. The
  part that must not move is `243 entries | 243 pinned | 0 problem(s)`.

**S2/S6 is not byte-identical and cannot be** — it prints a fresh temp directory per break. The two runs
differ on exactly **17 lines**, every one of them a per-run temp path (`…\Temp\xscholar-mutate-<random>`,
`…\Temp\tmp<random>`), an ephemeral port in a "port already in use" assertion message, a random property
seed digest, or a synthetic git commit sha inside a throwaway repo. **Every one of the 243 verdicts, the 8
canary outcomes and the `preflight 223 test(s)` count are identical.** Compare S2 by its summary line and
its verdict list, never by file hash.

**The additive rule's own input:** `instances/demo-incident/gate_baseline.json`
sha256 `74ae0bee20f5220b07cc06ca9d9a0b560d453dbe65948dd4952780547ceea2cc`.
A phase that edits this file has edited the ruler, not the thing being measured — it must say so
explicitly, or S4's "unchanged or stronger" verdict means nothing.

## 2. The one unstable thing in this baseline — NAMED, QUARANTINED, not fixed

**Finding: the throwaway SurrealDB that DB test classes start for themselves sometimes stops answering
mid-class on this Windows box.** It is one flake with one cause and it surfaces in more than one file, so
do not chase it per-file.

- **S1 run 1** ended `FAILED (errors=4)`, all four in `test_gates.GateFixServerTests`:
  `test_reapplying_the_schema_keeps_permissions_and_changefeeds`,
  `test_staged_evidence_behind_independence_is_probed_on_real_credentials` (twice: `mode=True` and
  `mode={'between': 'principal', 'own_evidence': True}`), and
  `test_staged_evidence_without_credentials_to_sign_in_is_reported_by_name`.
  Cause, quoted from the raw output: `cannot reach http://127.0.0.1:56446/sql ([WinError 10061] No
  connection could be made because the target machine actively refused it); the database may be stopped`.
  **Runs 2 and 3 of the identical command passed all 752** (1752.159 s and 1122.588 s), so the score is
  2 green of 3. Run 3 was added by this round precisely because runs 1 and 2 disagreed; it is the
  tiebreak, and it was taken on a quieter box. Raw: `cli_test_run{1,2,3}.txt`.
- The 2026-09-17 20:56 review measured the **same** failure shape in `tests/test_originals_db.py`
  (one run FAILED `errors=4` at 318 s with `cannot reach http://127.0.0.1:<port>/sql ... actively
  refused`; a second run OK at 161 s).
- Under the **project runner** `tests/test_originals_db.py` passed twice here:
  `python -m unittest discover -s tests -p "test_originals_db.py"` gave `Ran 22 tests`, `OK`, exit 0, at
  195.067 s and 172.827 s. Raw: `test_originals_db_unittest_run{1,2}.txt` — the two files differ in the
  elapsed-time line only.

**QUARANTINE — binding on every later phase.** `tests/test_originals_db.py` and
`tests/test_gates.py::GateFixServerTests` are **EXCLUDED from the regression signal** until the
throwaway-server death is root-caused. That root-cause is NOT in W0's scope: W0 records, it does not fix.

- A later phase must **not** treat one green run of these as proof, and must **not** report
  `cannot reach ... actively refused` as a new regression — this baseline already recorded it.
- The usable S1 signal is therefore: **`Ran 752 tests`, `0 skipped`, and every non-quarantined test
  passing.** If S1 goes red, read the error text first. If every error is a
  `cannot reach http://127.0.0.1:<port>/sql` against a class-local server, that is this flake: re-run
  once. Any other failure is a real regression.
- The round-1 pytest-based raw files for this file
  (`xscholar_test_originals_db_run{1,2}_verbose.txt`, stopping at 12/18/20 of 22) are explained by this
  same cause **plus** a 150 s kill applied to a file that needs 161-318 s. They are kept only as the
  trail of that mistake; do not read a count out of them.

## 3. The four invariants — the command that proves each, with its measured result

From `docs/ARCHITECTURE.md` section 2 "The four invariants" (lines 45-52). Counts below come from the
project runner, `python -m unittest discover -s tests -p "<file>.py"`, each file run twice; both runs
agreed on every count (elapsed time aside). Raw: `invariant_map_nodb.txt`.

| # | Invariant (ARCHITECTURE.md) | Proving command(s) | Measured result (run 1 / run 2) | Coverage verdict |
|---|---|---|---|---|
| 1 | **The datastore decides** — `confirmed`/`verified`/`ready` are written only when the required evidence is linked at that moment, on raw access too | `python -m unittest discover -s tests -p "test_gates.py"` · `-p "test_db.py"` · `-p "test_access.py"` · `-p "test_mcp_db.py"` · `-p "test_tamper_evidence.py"` · `-p "test_gate_baseline.py"` — plus **S4**, which probes every rule in both directions against a live server | `test_gate_baseline.py`: **5 tests, OK** (0.030 s / 0.026 s). `test_gates.py`: **96 tests, OK in ~200 s** (measured by the 20:56 reviewer); its `GateFixServerTests` class is quarantined per section 2. S4, both runs: `PROBES: 3 rejected as required, 3 accepted as required, 0 misbehaved`; `ORIGINALS: enforcement applied on 3 rule(s): 6 refused as required, 6 accepted as required, 0 misbehaved`; `BASELINE: unchanged or stronger` | **Covered, and S4 is the strongest single proof** — it asks the live datastore, not Python. Partly quarantined (section 2). |
| 2 | **Coverage is provable** — every unit labelled exactly once; every important label's quote found word for word in its unit | `python -m unittest discover -s tests -p "test_invariants.py"` · `-p "test_originals.py"` · `-p "test_loader_sweep.py"` | `test_invariants.py`: **15 tests, OK** (7.665 s / 7.450 s), including `test_every_unit_lands_in_exactly_one_slice_no_larger_than_its_chunk_size` and `test_oracle_labels_pass_and_every_planted_defect_names_its_unit`, and the property run `chunk_exactly_once: 150 generated, 150 checked, 0 discarded`. `test_originals.py`: **21 tests, OK** (0.355 s / 0.362 s) | **Covered, with numbers.** |
| 3 | **Nothing is dropped** — eliminated claims, adverse material, withheld items and dissents stay visible; a budgeted view names what it left out | `python -m unittest discover -s tests -p "test_present.py"` · `-p "test_invariants.py"` (`test_generated_configs_load_and_a_refusal_is_counted_not_dropped`, `test_a_held_field_is_never_overwritten_unless_named_and_every_disagreement_is_reported`) — plus **S5**, whose scenario line counts the refusals | `test_present.py`: **59 tests, OK** (1.342 s / 1.317 s). `test_invariants.py`: 15 OK, as above, including the property `reconcile: 450 generated, 450 checked, 0 discarded`. S5, both runs: `5 refused by a gate, 3 refused by permissions` | **Covered, with numbers.** |
| 4 | **Improvement must beat a fixed test** that agents cannot edit | `python -m unittest discover -s tests -p "test_evaluator.py"` · `-p "test_eval_harness.py"` · `-p "test_run_verdict.py"` — plus the evaluator step inside **S1** | `test_evaluator.py`: **24 tests, OK** (0.410 s / 0.266 s). `test_eval_harness.py`: **13 tests, OK** (0.413 s / 0.297 s). S1, both runs: `EVALUATOR demo_incident ... known_errors.json (sha256 b1395f1c012b) | 3 entries | 3 still caught | 0 REGRESSED | 0 NOT EXECUTED` | **Covered, with numbers.** The `sha256 b1395f1c012b` on the human-owned evaluator file is itself the "agents cannot edit it" check: if that hash changes, an agent edited the ruler. |

**Named gap for section 3:** the four invariants have **no single top-level command** that proves all four
and nothing else; they are proved by the union of S1 + S4 + S5. If a later phase wants one command, S1 is
the closest — but S1's green depends on the section 2 flake, so S4 is the better additive-rule gate.

## 4. The six front-door invariants H1-H6 — command and result

From `_tickets/REVIEW-2026-09-17-architect-instruments.md:37-42`. `WORKSTREAM-groundwork.md:17-18` names
these as must-preserve, so each needs a named proving command.

| # | Front-door invariant | Code | Proving command | Measured result | Verdict |
|---|---|---|---|---|---|
| H1 | **The engine-code write guard** — every write tool is refused unless the engine checkout is branch `main`, clean and unchanged since startup; `UNKNOWN` is never read as verified; the check re-reads git before every write | `xscholar/mcp/code_state.py:1-35`, especially `:25-27` "Not a git checkout -> UNKNOWN -> writes refused"; `server.py:245-256, 275-282` | `python -m unittest discover -s tests -p "test_mcp_guard.py"` | **27 tests, OK** (3.841 s / 4.823 s) | **COVERED — this corrects the 20:56 review, which recorded H1 as having no command.** The refuse-when-not-a-git-checkout case specifically is `tests/test_mcp_guard.py::test_unknown_at_startup_is_never_verified` (`:152`, built from `code_state.unknown(...)`) and `::test_unknown_now_refuses`; the re-read-before-every-write property is `::test_a_verified_unchanged_commit_serves_writes` (asserts at least 2 disk reads) and `::test_changes_made_after_startup_refuse_writes`. **A live METHOD gap remains for the Rust work:** a shipped static binary has no checkout, so this guard refuses it by design. The replacement must be named before any Rust write tool ships. That is a design gap, not a test gap. |
| H2 | **Provenance from launch identity, never from request content** | `server.py:84-97`, `:8`; DB backstop only on `gate_run.run_role`/`ran_by` (`0003_project_rules.surql:39-42`) and `review.reviewer` (`0002_project_base.surql:95`) | `python -m unittest discover -s tests -p "test_mcp_server.py"` | **56 tests, OK** (2.109 s / 1.761 s) | **COVERED on the Python side only.** The review's finding stands: there is **no equivalent ASSERT on `fact`, `unit`, `dead_end` or `evidence`**, so for those tables H2 is Python-only and **no DB-level command can prove it. GAP.** |
| H3 | **Lens forgery refusal** — a server launched with one lens refuses a critique under another | `server.py:222-230`; DB backstop for `lens_bound` roles only (`access.py:25`, `:94-105`, `:53`, `:67-71`) | `python -m unittest discover -s tests -p "test_mcp_server.py"` (Python arm) · `-p "test_access.py"` (DB arm, `lens_bound` roles only) | `test_mcp_server.py`: **56 tests, OK**, as above. `test_access.py` is DB-backed and runs inside S1's 752 | **COVERED for `lens_bound` roles; GAP for non-`lens_bound` roles**, where the rule is Python-only by construction. |
| H4 | **The untrusted-text contract** — an adapter that returns `text` instead of `untrusted_source_text` fails the call loudly, and the notice is attached | `server.py:164-168`, `:174-177` | `python -m unittest discover -s tests -p "test_originals_db.py"` — the only test file that names `untrusted_source_text` | `Ran 22 tests`, `OK`, exit 0, 195.067 s / 172.827 s | **COVERED — but the only file covering it is the QUARANTINED one (section 2).** That makes H4 the weakest-protected front-door invariant: a later phase that broke the notice would be caught only by a file this baseline says not to trust. **Named gap: H4 needs a no-DB test.** |
| H5 | **Originals re-hashed on every read** — `document_get` re-hashes the bytes on each read; a missing or corrupt original is `status error` naming it, never served | `server.py:335-342` | `python -m unittest discover -s tests -p "test_mcp_server.py"` · `-p "test_mcp_conformance.py"` · `-p "test_mcp_db.py"` | `test_mcp_server.py`: **56 tests, OK**. S5, both runs, shows it end to end: `sha256 10a12ce989db4703..., re-hashed on read` | **COVERED, and also proved end to end by S5** (the `original ..., re-hashed on read` line). |
| H6 | **Generated-artifact freshness** — every generated file carries a `content-sha256` line and a hand edit breaks it | `xscholar/drift.py:8`, `xscholar/skills.py` | `python -m unittest discover -s tests -p "test_skills_drift.py"` — plus **S3** | `test_skills_drift.py`: **40 tests, OK** (2.343 s / 1.917 s). S3, both runs, byte-identical, `0 drift(s)` | **COVERED, with numbers.** |

**H-gaps to carry into W1/W2, in priority order:** (1) H1's replacement for a binary with no checkout — a
design decision that must be named before any Rust write tool ships; (2) H4 is proved only by a
quarantined file; (3) H2 has no DB-level ASSERT on `fact`/`unit`/`dead_end`/`evidence`; (4) H3 is
Python-only for non-`lens_bound` roles.

## 5. The sealed family-court instance's gate — NOT EXERCISED, and why that matters

**No record text, name, DOB, path, credential or sealed-interview content appears in this file or in any
raw file this ticket produced.** The instance is a local-only private repo outside this engine; its
identity is deliberately not written here. Its database credential is referred to only generically, and
its environment-variable names are written as **`<INSTANCE>KB_USER` / `<INSTANCE>KB_PASS`** because the
real names carry the client's identity and this repo has a GitHub origin that a later phase pushes.

- The two gate scripts live at `<private repo>/09_appeal/kb/test_gate.py` (`02_gate.surql`) and
  `<private repo>/09_appeal/kb/test_appellate_gate.py` (`03_appellate.surql`).
- Commands, run from that `09_appeal/kb` directory:
  `PYTHONIOENCODING=utf-8 python test_gate.py` and `PYTHONIOENCODING=utf-8 python test_appellate_gate.py`.
- What this session got: **exit 1 on both scripts, on both runs, byte-identical output.** The cause is not
  a gate defect — it is the documented fail-closed behaviour added for defect B-027: `<INSTANCE>KB_PASS`
  was not set in this session, so `kb.py:95` refuses to connect at all. For `test_gate.py`, 14 of 23
  probes reported the refusal; the remaining probes need no live write and passed.
  - `test_gate.py` output sha256 `0b293b54be478df62eabbb2d7aa38fbee21837f331ec631c816961b353ec667a`
  - `test_appellate_gate.py` output sha256 `86c0ab665089d9b12d0efbe7e72c735195d53eeadd74a253e8ae17314b99f135`
  - Raw, reduced to exit code + counts + hash with the tracebacks stripped:
    `family_court_test_gate_run{1,2}.txt`, `family_court_test_appellate_gate_run{1,2}.txt`.

> ### This hash is NOT a regression proxy. Do not start W2 against it.
> The refusal is raised at `kb.py:95`, **before any connection is made**. Zero gate rules, zero schema and
> zero query text execute. **A rename that broke every single rule in this instance would produce the
> byte-identical hash.** Recorded honestly: *the family-court instance's gate is NOT EXERCISED by this
> baseline.*
>
> **OWNER ACTION, required before W2 starts.** Run these two commands once, from
> `<private repo>/09_appeal/kb`, in a shell where the instance's database credential is set — its two
> environment variables, the user and the password, whose real names are withheld here; see
> `<private repo>/09_appeal/kb/kb_conn.py:52-61`. Then record in this file: exit code, the probe pass/fail
> counts, and the sha256 of each full output. **Counts and hashes only, never record text.**
>
> ```
> PYTHONIOENCODING=utf-8 python test_gate.py
> PYTHONIOENCODING=utf-8 python test_appellate_gate.py
> ```
>
> The hashes above **will** change once the probes reach the database, because more probes will read PASS.
> That is expected and is not a regression. The regression signal afterwards is: a probe that read PASS
> with credentials reading FAIL, or a change in the probe counts.

## 6. Environment and machine stamp

Per `~/.claude/projects/c--Users-trist-projects/memory/feedback-every-result-carries-its-machine.md`: no
timing number above means anything without this block.

- Engine HEAD: `0af87fec06b28a6d32c96ba8e38642389c468b42`, branch `w0/baseline`, worktree
  `C:/Users/trist/projects/xscholar-w0` (the only untracked file was this `BASELINE.md`).
- `surreal version`: `3.1.6+20260813.cfbaec4 for windows on x86_64`.
- Python `3.13.5` (`C:\Python313\python.exe`).
- OS: Microsoft Windows 11 Pro `10.0.26200` (Build 26200).
- Hardware: **AMD Ryzen 9 5900XT** (16 cores / 32 threads), 65,461 MB RAM, MSI MS-7C56, NVIDIA RTX 3090.
  **This is the 5900XT desktop — never attribute a timing above to other hardware.**
- Power plan: High performance (`8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c`).
- **Background load, and why it matters:** 336 OS processes, CPU load 69%, 12 `python.exe` and
  **2 `surreal.exe`** (the long-lived `re_kb` / brain servers) at the time this file was written. The test
  suites start their own throwaway servers on ports 8023-8034 and on ephemeral ports, and refuse a port
  already in use. The 21:00-22:03 raw runs were taken while **two other full suites were running
  concurrently on the same box**; that is the load under which the section 2 flake appears and is the most
  likely contributing factor. A later phase comparing timings on a quiet box should expect *faster*, not
  different, results — and should count `surreal.exe` before blaming a change.
- **The load actually explains the spread.** S1 took 2056.7 s (red) and 1752.2 s with two other full
  suites running alongside, and **1122.6 s (green) with none**. The full mutation registry took 985 s and
  1042 s on the quieter box. So: budget roughly 20 min for S1 and 17 min for S2 on an idle 5900XT, and
  **at least double that** if anything else is running. Never use a 120 s timeout on either.
- Raw-output hashes for the runs this round added: `cli_test_run3.txt`
  sha256 `6a9e9162fcefabee7f1398db361f593619c7bc13388934ec12ada7530129eedc`.

## 7. Scope confirmation and follow-ups (not in W0)

W0 changed no engine code, merged no branch, performed no rename and fixed nothing. The only writes are
this file and `_tickets/baseline/W0/*.txt`.

Follow-ups this baseline found, each for a later owner:

1. **Root-cause the throwaway-SurrealDB death** (section 2). Until then S1 needs a re-run rule and two
   test classes are quarantined.
2. **Name H1's replacement for a binary with no git checkout** (`code_state.py:25-27`) before any Rust
   write tool ships — the largest unaddressed method question in the workstream.
3. **H4 needs a no-DB test.** Today it is proved only by the quarantined `test_originals_db.py`.
4. **H2 needs DB-level ASSERTs** on `fact`, `unit`, `dead_end` and `evidence` — or an explicit decision
   that Python-only enforcement is acceptable there.
5. **`tests/test_pipeline_db.py:27`** is the single line that makes pytest unable to collect. Leave it
   alone unless someone decides the project should support pytest; that is a decision, not a fix.
6. **"The 31 DB tests"** in `WORKSTREAM-groundwork.md:18` does not name a command on this tree:
   `tests/db` does not exist. The 31 traces to the unmerged `product/data-model` branch's own
   `tests/product/test_model.py` (exactly 31 `def test_` at `3ddbd34`, confirmed read-only, not merged).
   W1 must either land that path or restate the phrase. This baseline does not guess.
7. **`scripts/privacy_scan.py`'s deny list had a gap** that let the identifier-bearing environment-variable
   names through as CLEAN. It was closed by ticket `2026-09-18-privacy-denylist-gap`, and the scanner is
   now RED on that shape — verified against this file's previous revision before it was rewritten. The
   deny list lives outside this repo, so a fresh checkout cannot be assumed to have it: a later phase must
   not read a CLEAN scan as proof without first confirming the deny list is present.
