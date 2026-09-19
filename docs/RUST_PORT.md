# Groundworks: the Rust port

Written by impl-10 (`_tickets/2026-09-17-groundworks-impl-10-rust-differential-harness.md`), which builds the
owner's gate BEFORE any engine code exists. Finished by port-11 (measurements, with machine stamps).

OWNER RULING 2026-09-18 (binding): *"I don't want to do the work twice -- get it over into Rust and we test
once it's complete."* Python **v0.2.0** is the ORACLE; every feature lands in Rust only; the one owner test is
impl-13.

---

## 1. The oracle, and the gate

**The oracle is a tag, never "whatever main is".**
`differential.py --ensure-oracle` makes a **fresh clone** of `https://github.com/t3chnicallyinclined/groundworks`
at tag `v0.2.0` into `C:/Users/trist/projects/groundworks-oracle`, and every comparison refuses to run unless

* `git describe --exact-match` in that checkout says `v0.2.0`,
* `HEAD` is `94ad5dca0882121c71245dd644fe533743788655` (the tag's commit; the release commit, child of 6917236 —
  only version/changelog/readme differ), and
* the checkout has no uncommitted changes.

A clone, not a worktree: a worktree of a working checkout follows whatever that checkout does next.

**The gate** (this ticket's `gate:` line, and every port ticket's):

```
cargo gw-test
cargo clippy -p gw-core -p gw --all-targets -- -D warnings
python groundworks/harness/differential.py --oracle v0.2.0 --self-test
```

`--self-test` runs the CONTROL FIRST and twice (the oracle against itself), then plants a fault for every class
of difference the gate claims to see and requires each one to go RED **by name**. A green self-test means the
gate works; it is not a claim about any candidate. The arms today:

| Arm | What it plants | What must catch it |
|---|---|---|
| `surql_one_byte` | one digit changed in `surql/demo-incident/gates.surql` | byte diff, naming file + byte + line |
| `transcript_status` | an envelope `status` `invalid_argument` -> `ok` | transcript diff, naming the call |
| `transcript_derived_id` | a `critique:` id the oracle DERIVES, suffixed | transcript diff, naming the call |
| `transcript_author` | the launch identity in `by` | transcript diff, naming the call |
| `non_datetime_in_masked_field` | `verified_at` set to `"not-a-datetime"` | the mask must NOT fire -> value difference |
| `mask_count_mismatch` | one masked datetime field deleted | per-rule mask counts differ |
| `bytes_field_lies` | a transcript line's `bytes` off by one | rule (3), before any diff |
| `config_verdict_changed` | a bad-config `REFUSE` -> `ACCEPT` | byte diff on `config/bad_configs.txt` |

Other entry points: `--record-goldens`, `--record-transcript [--twice]`, `--goldens <dir>` (diff a produced
tree against the committed one), `--mcp --candidate <bin>` (replay the same call script against the Rust front
door; NOT EXECUTED until port-09/impl-12 exists).

### What is compared

1. **SurQL goldens, no normalization, ever** — `goldens/surql/<instance>/<component>.surql`, one file per
   (instance, component), plus instance-independent emitters under `goldens/surql/_global/`. An emitter that
   raises has its refusal recorded verbatim (`!! RAISED <Type>: ...`); a raise is a golden too, never a skipped
   file. Emitters: `gates.schema_surql, unique_surql, dangling_edges_surql, gate_surql, gate_event_surql,
   independence_surql, independence_mode_surql, writer_record_surql`; `access.access_surql,
   provisioning_fields_surql`; `lens.lens_surql`; `temporal.temporal_surql`; `originals.enforcement_surql,
   schema_lines, ensure_schema_surql`; `promotion_log.log_surql`; and the shipped `groundworks/patterns/*.surql`.
2. **The config surface** — `gates.spec(inst)` as sorted JSON per instance, and the accept/refuse verdict plus
   the verbatim error for every bad-config fixture in the oracle's `tests/test_config.py`. The fixtures are read
   out of the test source by AST, so the golden follows that file instead of a copy that would drift.
3. **A seeded single-agent MCP transcript** — `goldens/transcript-v0.2.0.jsonl`, plus its per-rule mask counts
   in `goldens/transcript-v0.2.0.masks.json`.

`goldens/MANIFEST.sha256` carries a sha256 per golden file.

### Instances the goldens cover (CONFIRMED at v0.2.0)

`instances/` has 21 directories, but only **`_template`** and **`demo-incident`** carry an `instance.yaml`; the
other 19 are `INSTANCE.md` briefs. There are **no** fixture instances under `tests/` at this tag (the harness
sweeps `tests/**/instance.yaml` anyway, so a later tag is picked up).

For BOTH shipped instances, `independence_surql`, `independence_mode_surql`, `writer_record_surql` and
`promotion_log.log_surql` return an **empty list** — four emitters the gate would be blind to. So the harness
ships one fixture of its own: **`harness/fixtures/audit-on/`**, which is `demo-incident`'s config with exactly
three lines changed (the instance id, `independent: {between: principal, own_evidence: false}` on the conclusion
rule, and `promotion_log: true` with `promotion_log` added to `human_owned`). Those four emitters then produce
864 / 896 / 1897 / 8827 bytes instead of nothing.

---

## 2. Crate layout

One workspace (`docs/WORKSPACE.md`, owner ruling 2026-09-19 "(a)"). Groundworks lives under `groundworks/`:

```
groundworks/crates/gw-core        lib   the engine (empty at impl-10; filled from impl-11 / port-01 on)
groundworks/crates/gw             bin   the command line, the differential candidate
groundworks/crates/gw-board-store lib   ops-core <- store trait impl                        (G3, not yet)
groundworks/harness/              Python drivers (evaluators), fixtures/, differential.py
groundworks/goldens/              what the oracle emits: surql/, config/, transcript, MANIFEST
groundworks/docs/RUST_PORT.md     this file
```

* Both crates take `lints.workspace = true`, so `unsafe_code = "forbid"` from the workspace root applies.
* Lane alias `cargo gw-test` = `test -p gw-core -p gw` (`.cargo/config.toml`). **A lane gate never uses
  `--workspace`**: that would merge Cargo features across lanes (`docs/WORKSPACE.md`).
* **gw holds NO ticket, claim or lane code.** That is `ops-core`, reached through `gw-board-store` (G3).
* Dependency direction: `ops-core <- gw-board-store -> gw-core <- gw`. All path dependencies.

---

## 3. Dependencies, and the decisions fixed here

A later ticket may overturn one **only with a logged reason**.

| Decision | Why |
|---|---|
| **SurrealDB over HTTP `/sql`** with a blocking client (`ureq`), **not** the official `surrealdb` crate | It is what `db.py` does, so the 3.1.x version check and the `_denied` detection stay identical. The crate brings a large dependency tree and an async runtime the hooks must not pay for |
| **MCP over stdio** via `rmcp` **or** a hand-written JSON-RPC loop — decided by **which passes the transcript with fewer dependencies** | The transcript is the arbiter, not taste (decided at impl-12 / port-09) |
| `#![forbid(unsafe_code)]` (via the workspace lint) | No engine path here needs it |
| One `thiserror` enum per crate | One error type per boundary, named |
| No `unwrap` outside tests | A refusal is a value the front door reports, never a panic |
| Every dependency justified in this file | The Python engine is standard-library-only except the `mcp` SDK; the Rust port does not get to be heavier by accident |

Dependencies at impl-10: **none.** `gw-core` and `gw` have empty `[dependencies]`; `gw` takes `gw-core` by path.

---

## 4. The mask list (transcript only)

The SurQL and config goldens are compared with **no normalization at all**. The transcript cannot be, because
the datastore clock cannot be frozen. Every rule below is a **constant in one place**
(`harness/differential.py`, top of the file), is applied to both sides, and **counts its substitutions**; the
two sides' counts must be equal, so a rule that silently stops firing is itself a failure.

Everything else about the run is pinned to a constant: the launch environment (`GROUNDWORKS_ADAPTER`,
`GROUNDWORKS_AGENT_ID=harness_agent`, `GROUNDWORKS_ROLE=coordinator`, `GROUNDWORKS_LENS=lens_security`,
`GROUNDWORKS_ALLOW_UNVERIFIED=1`, `GROUNDWORKS_PANEL_ROOT`, `TZ=UTC`, `PYTHONHASHSEED=0`), the instance
(`demo-incident`), the graph (built by `scripts/run_instance.py` on a **fresh throwaway** SurrealDB), and the
call script itself.

| # | Rule | Fields | The line that justifies it | Count |
|---|---|---|---|---|
| 1 | a **datetime** field, and only after the value parses as a datetime | `learned_at, created_at, updated_at, started_at, ended_at, cited_at, verified_at` | `verified_at` is written `time::now()` by the datastore when an original is re-hashed — `groundworks/originals.py:513` — and `document_get` re-hashes on every read. It is the **only** datetime the v0.2.0 tool surface returns, so without it there is nothing to mask and the transcript is not reproducible twice | 3 |
| 2 | a **randomly generated record id** in a named field | **EMPTY** | The oracle derives every id it writes: critique = `sha1(conclusion\|lens\|head)[-8:]` (`pipeline/write.py:143` + `patterns/edge_ids.py:27-29`), answer = `ans_<critique>` (`write.py:161`), every edge id the same sha1 (`edge_ids.py:27`), and `rand::` / `rand()` appears nowhere in the engine's SurQL at v0.2.0. Derived ids are **COMPARED** — the `transcript_derived_id` red arm is exactly that | 0 |
| 2b | the loader's **run token** | `run` | `patterns/edge_ids.py:32` — `"r%d" % time.time_ns()`, a clock-derived stamp on loader-written edges. Masked only where it appears (it does not appear in this call script: count 0) | 0 |
| 3 | `bytes` at the **top level of a transcript line** (the harness's own field) | `bytes` | Checked equal to that side's own recomputed, UNMASKED envelope length, then dropped — so a truncated envelope cannot pass. `data.bytes` inside an envelope (a document's size, 149) is the **engine's** and is COMPARED | 24 |
| 4 | the **engine code guard's** report | `engine_code`, `engine_code_warning`, and the `WARNING` that repeats it | `groundworks/mcp/code_state.py`. It reports the identity and freshness of the PYTHON checkout the server started from: a measured duration (`read_ms`), a verdict that flips when a `git` re-read exceeds `GIT_TIMEOUT_S = 15` (observed on this machine), and absolute paths. A Rust candidate has no Python checkout to report. ADDED 2026-09-19 with this reason | 12 |
| 5 | the **harness's own launch paths**, substituted at RECORD time on both sides | the oracle checkout -> `<ORACLE_DIR>`, the panel root -> `<PANEL_ROOT>` | `panel_submit` / `panel_status` quote the resolved run_dir back in their refusal. Those paths are the harness's INPUTS, not the engine's output, and they differ per machine and per checkout. ADDED 2026-09-19 with this reason | 14 |

**Reproducibility, measured 2026-09-19 on the dev machine (Windows 11, `C:/Users/trist/projects`):**
`--record-transcript --twice` — two recordings, a fresh throwaway SurrealDB and a freshly built demo graph each
time — are **IDENTICAL after the mask**, with identical per-rule counts
(`{"bytes_checked":24,"datetime":3,"engine_code":12,"random_id":0,"run_token":0}`).

**The call script** (order and arguments are part of the gate; 24 recorded lines, line 0 being the tool list):
`health, unit_search` ×3, `unit_get` ×2, `claim_explain` ×2, `eliminated_claims`, `document_get` ×2,
`critique_add` ×5 (a write that lands, the same head restated, another lens refused, an unknown conclusion, an
empty head), `claim_explain`, `critique_answer` ×2 (**one against the id the harness predicts the oracle will
derive** — it lands only if the candidate derives the same id), `panel_submit`, `panel_status`, `document_get`,
`health`. Five statuses appear: `ok, not_found, invalid_argument, rejected_by_gate` (and `db_unreachable` is
not exercised — a follow-up).

**No datastore is left running.** The harness starts SurrealDB with `surreal_bin.start_memory` on an OS-chosen
free port, stops it with `surreal_bin.stop(proc)` **by PID** (never by name), and then refuses to finish unless
`proc.poll()` is not None AND the port has stopped answering.

---

## 5. The unit order (from the umbrella, `_tickets/2026-09-17-groundworks-rust-port-plan.md`)

1. **impl-10** — this ticket: the workspace's Groundworks half + the differential gate.
2. impl-11 — port-01, the config loader.
3. port-02 SurQL schema/gates · 4. port-03 SurQL access/lens/temporal · 5. port-04 db client (needs only
   impl-10) · 6. port-06 pipeline files · 7. port-08 panel/skills/drift — parallel, each in its own worktree.
8. port-05 probes/baseline/live_schema (after 3,4,5) · 9. port-07 loader/write/originals (after 3,4,5,6) ·
   10. port-09 queue/promotion/temporal/evaluator (after 8,9).
11. impl-12 — the v0.2.0 MCP surface in Rust (after 7,9,10) · 12. port-10 hooks + CLI ·
    13. **port-11 PORT COMPLETE**: the full differential twice, the test census, measurements with machine
    stamps, tag `v0.2.0-rs`, Python FROZEN.
14. Then features, Rust only: impl-01 pool/registry, impl-02 timestamps, impl-04 Hindsight refs (parallel) ->
    impl-03 run registry -> impl-05 tripwire -> impl-06 multibrain front door -> impl-07 paging -> impl-08
    orient -> rs-load-harness -> **impl-13, the one owner test** -> port-12 present/embed leftovers.

From impl-02 on, the schema moves away from v0.2.0 and the gate becomes "oracle + a reviewed, named patch"
rather than "byte-identical". Every departure is named in the patch, and this file records it.

---

## 6. Follow-ups this ticket found (not done here)

* `db_unreachable` is the one envelope status the call script never produces. Add an arm that points the
  adapter at a dead port once the candidate exists.
* The oracle's `code_state` git re-read timed out (15 s) on every write call during recording on this machine,
  while the same `git` commands answer in ~30 ms from a shell. It is masked (rule 4) and costs ~90 s per
  recording; it is a Python-oracle quirk, not a port question, so it is recorded, not chased.
* `--mcp --candidate` is wired to the same call script but exits NOT EXECUTED until the Rust front door exists.
