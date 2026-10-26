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
* **git is tracking everything in the checkout** — `git status --porcelain -z --ignored=matching
  --untracked-files=all` must come back EMPTY. Untracked *and* ignored: round 1 found that
  `--untracked-files=no` let an untracked `sitecustomize.py` be imported by the child (the checkout is on
  `PYTHONPATH` and is the child's cwd); round 2 found that `--porcelain` alone still does not report
  **gitignored** paths, and the oracle's `.gitignore` ignores `__pycache__/` — which is exactly where CPython
  loads code from. A forged `groundworks/__pycache__/gates.cpython-313.pyc` carrying the real `gates.py`
  mtime+size in its PEP-552 header poisoned all three `gates.surql` goldens while verification passed.

Before that check the harness deletes **only** the data `scripts/run_instance.py` regenerates inside the
checkout (`instances/*/out`, `instances/*/originals` — `git ls-files` confirms nothing is tracked under
either), so the gate can run twice in a row. A destructive guard fails closed: the list is matched component by
component, and **nothing CPython can load is ever deleted** — an ignored `__pycache__` is a REFUSAL, not a
silent cleanup, because every harness child runs with `PYTHONDONTWRITEBYTECODE=1` and so one appearing means
something else ran Python in the oracle.

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
| `transcript_status` | an envelope `status` `invalid_argument`/`rejected_by_gate`/`not_found` -> `ok` | transcript diff, naming the call |
| `transcript_derived_id` | a `critique:` id the oracle DERIVES, suffixed | transcript diff, naming the call |
| `transcript_author` | the launch identity in `by` | transcript diff, naming the call |
| `non_datetime_in_masked_field` | `verified_at` set to `"not-a-datetime"` | the mask must NOT fire -> value difference |
| `mask_count_mismatch` | one masked datetime field deleted | per-rule mask counts differ |
| `bytes_field_lies` | a transcript line's `bytes` off by one | rule (3), before any diff |
| `config_verdict_changed` | a bad-config `REFUSE` -> `ACCEPT` | byte diff on `config/bad_configs.txt` |
| `engine_guard_dropped` | a write call's `rejected_by_gate` -> `ok` (what a candidate with no write-authorization guard would report) | transcript diff, naming the call — added fix round |
| `poisoned_oracle_untracked_file` | an untracked `sitecustomize.py` dropped into the oracle checkout | `verify_oracle` refuses (NOT EXECUTED) — added fix round 1 |
| `poisoned_oracle_gitignored_file` | a real `.pyc` with a matching PEP-552 header in the **gitignored** `groundworks/__pycache__/` | `verify_oracle` refuses (NOT EXECUTED) — added fix round 2 |
| `masks_json_disagrees` | a tampered `masks.json` driven through `promote_transcript`, the same function `--record-transcript` uses | that function must refuse **and write nothing** — added fix round 1, made non-vacuous in round 2 |
| `ambient_allow_unverified` (a PIN arm, inverted) | a real recording made with `GROUNDWORKS_ALLOW_UNVERIFIED=1` exported in the operator's shell | it must be **byte-identical** to the committed golden; a difference is the failure — added fix round 2 |

**Mutation-proven, 2026-09-19** (each arm was shown to go blind when the production code it guards is removed,
then green again on revert — a red arm that cannot detect its own mutation is decoration):

| Mutation | Arm | Result |
|---|---|---|
| `bad = []` in place of `mask_counts_disagreement(...)` inside `promote_transcript` | `masks_json_disagrees` | `GREEN — THE GATE IS BLIND`, self-test exit 1 |
| `_status_entries([])` in place of `--ignored=matching --untracked-files=all` in `verify_oracle` | `poisoned_oracle_gitignored_file` | `GREEN — THE GATE IS BLIND`, self-test exit 1 |
| `GROUNDWORKS_ALLOW_UNVERIFIED` dropped from `FIXED_ENV` **and** the strip loop replaced by round 2's `env.pop("GROUNDWORKS_DB_ACCESS")` | `ambient_allow_unverified` | `LEAKED — THE GATE IS NOT PINNED`, `MASK COUNT engine_code: ambient=1 44 != committed 32`, exit 1 |

Note the middle row's second lesson: dropping the pin **alone** did not leak, because the strip loop
(`PINNED_SERVER_ENV_KEYS`) closes the same hole independently. Two independent controls, both proven.

Other entry points: `--record-goldens`, `--record-transcript [--twice] [--force-masks]`, `--goldens <dir>` (diff a
produced tree against the committed one), `--mcp --candidate <bin>` (replay the same call script against the
Rust front door; NOT EXECUTED until port-09/impl-12 exists).

**Stated plainly (the reviewer's words, review of b2b7f99, kept true after the fix round below): this gate
proves it can go RED on tampered DATA — a byte flipped in a golden, a field changed in the transcript, a mask
count that stops matching. It has never driven a CANDIDATE.** `--goldens <dir>` is usable today for the
goldens/config half. The transcript half's candidate path (`--mcp --candidate <bin>`) stays NOT EXECUTED, and
therefore unexercised, until the Rust front door exists at impl-12/port-09.

### `--split`: the first gate that DOES drive a candidate — and the corpus pattern every port copies

`--split` (port-04, `2026-09-18-groundworks-port-04-db-client`) is the first differential arm that actually
runs a candidate binary (`<candidate> --split-statements`, stdin in / JSON array out) against the oracle's own
`db.py::_split_statements`, one comparison per `(name, text)` entry from `_oracle_split_texts()`.

**The shallow-gate lesson (blocking finding B3, review of 1358151).** At first the gate compared only 4
inputs: the 2 shipped `.surql` files plus test_db.py's 2 `SplitTests` fixtures. None of the four contains a
double-quoted string, a paren, or an unbalanced bracket. A reviewer built 9 plausible WRONG ports of
`_split_statements` and ran them against exactly that fixture set: **4 of 9 passed anyway** (double quotes
not treated as strings; `depth == 0` instead of `depth <= 0`; parens not nested; any quote closing any
quote). A candidate can be RIGHT on the fixtures the gate happens to check and still be WRONG on real input —
the fixtures were never adversarial, just convenient.

**The fix: `_ADVERSARIAL_SPLIT_CASES` in `harness/differential.py`**, ~40 hand-built `(name, text)` cases,
one class of failure per case (not exhaustive fuzzing — each case targets ONE way a splitter can be wrong):
empty input and every line-boundary edge (no/one/many trailing newlines, CRLF, lone CR, mixed endings, only
semicolons); every one of Python's OTHER `str.splitlines()` boundaries (`\x0b`/`\x0c`/`\x1c`-`\x1e`/``/
` `/` `) closing a `--` comment (this is what caught blocking finding B1 for real, not a
constructed example); which characters open a string and what closes it (single vs double quotes, a quote
of one kind inside the other, an unterminated quote, a quote spanning lines); brace/bracket/paren depth
including nesting, unmatched openers/closers, and depth going negative; a backslash before a closing quote
(SurQL has no escaping — a candidate that assumes it does is wrong); and unicode in strings, comments and
astral-plane code points. Two of the cases (`nested_parens_inner_close_then_semicolon`,
`backslash_before_closing_*`) were purpose-built against a specific mutant below because the obvious version
of the case did not actually diverge — see the comments on `_ADVERSARIAL_SPLIT_CASES` for why.

**Mutation-proven, 2026-09-19** (each of the reviewer's 9 wrong splitters implemented as a throwaway patch to
`gw_core::db`, `cargo build --release -p gw`, `--split` run, reverted with `git checkout` — never left in the
tree):

| Mutant | What it gets wrong | `--split` result | Caught by (adversarial cases, named) |
|---|---|---|---|
| M1 only single quotes are strings | `"` never opens/closes a string | RED | `double_quoted_semicolon`, `double_quoted_comment_marker`, `double_quote_spans_lines`, `unterminated_double_quote`, `mixed_quote_types_adjacent`, `double_quote_containing_single_quote` |
| M2 `depth == 0` not `depth <= 0` | an unmatched closer (negative depth) never re-triggers splitting | RED | `unmatched_closer`, `depth_goes_negative_then_more_semicolons` |
| M3 parens don't nest | `(` sets a flag instead of incrementing; an inner `)` looks like the outer one closed | RED | `nested_parens_inner_close_then_semicolon` (also caught for free by the shipped `archive_bin.surql`/`tms_propagation.surql`) |
| M4 comments not stripped before splitting | `--` text can carry a semicolon into a statement | RED | every line-boundary and comment-boundary case (14 of them) |
| M5 quotes not tracked in `strip_comments` | `--` inside a string is treated as a real comment | RED | `double_quoted_comment_marker`, `single_quoted_comment_marker` |
| M6 fully naive `text.split(';')` | no quote/depth/comment tracking at all | RED | 9 cases spanning quotes, nesting and comments |
| M7 any quote closes any quote | a `'` can close a `"` and vice versa | RED | `double_quote_containing_single_quote`, `single_quote_containing_double_quote` |
| M8 `python_splitlines` is `str::split('\n')` | keeps a phantom trailing empty line; misses the other line boundaries | RED | every line-boundary/comment-boundary case (14 of them) |
| M9 backslash escapes the following quote | SurQL has no escaping; a quote followed by content it should have closed stays open | RED | `backslash_before_closing_quote`, `backslash_before_closing_double_quote` (built specifically for this mutant — see the corpus comment) |

All 9 go RED **by name** (a named adversarial case, or in M3's case also a shipped `.surql` file), and the
baseline is GREEN again after every revert (`git status --porcelain` empty on `db.rs` after each `git
checkout`). `cargo gw-test` (18 tests) and `cargo clippy -D warnings` stay clean throughout.

**This is the pattern ports 01-09 copy, not just this file's corpus.** Any port whose gate compares a
candidate against a pure text/data function should: (1) start from the oracle's own fixtures, (2) ask "what
adversarial input would a plausible WRONG port still pass on these fixtures", (3) write one case per failure
class rather than fuzzing blindly, and (4) actually implement 3-10 plausible wrong ports as throwaway
mutations and prove each goes RED by name before trusting the gate. A gate that has never been shown to catch
anything is a gate that has never been tested — see `gate-discipline`.

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

### 2a. The `config/*_support.rs` slivers: one owning ticket each, EXTEND don't re-port

port-01 (this ticket) ported `config.py` whole, plus only the handful of functions/constants four OTHER
oracle files that `config.py` itself imports at validation time. Each sliver file below is the **single
Rust home** for everything `config.py` needed from its oracle file; the ticket named for that oracle
file ports the REST of it into the SAME file, by adding to it, not by starting a second port of the
same source elsewhere in the tree:

| Sliver (this crate) | Oracle file | What it holds today (port-01) | Owning ticket — EXTENDS this file |
|---|---|---|---|
| `config/gates_support.rs` | `groundworks/gates.py` | `CRITIQUE_VERDICTS`, `FACT_STATUSES`, `_value_literal` (`value_literal`), `_split_top_level` (`split_top_level`), `_list_literals`, `_where_terms` (`where_terms`) | **port-02**: the SurQL emitters, `gate_expression`, the traversal builder, the rest of `gates.py` |
| `config/assertions_support.rs` | `groundworks/assertions.py` | `fields_of`, `field_domain`, `validate` (the `assertions:` section) | **port-05**: the write-time check, the SurQL emission, the report |
| `config/originals_support.rs` | `groundworks/originals.py` | `config_problems` (the `originals:` section) | **port-07**: the document schema, the content-addressed store, `verify()` |
| `config/evaluator_support.rs` | `groundworks/evaluator.py` | `FORMAT`, `read_file`/`RegressionFileError` (the regression-file format check) | **port-09**: the rest of the evaluator (recording, comparison, the queue integration) |

A later ticket that needs one more function from `gates.py`/`assertions.py`/`originals.py`/`evaluator.py`
adds it to that file (with the same `oracle_file.py:LINE function_name` citation style already used
there), rather than creating e.g. a second `gates.rs` alongside `gates_support.rs`. If the eventual full
port outgrows the `_support` name, renaming is a one-ticket move, not a fork.

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

Dependencies added at impl-11 / port-01 (the config loader):

| Dependency | Why |
|---|---|
| `serde_json` (workspace dep, already used by fleetview; `preserve_order` feature turned on for `gw-core`/`gw` only) | The parsed config's shape (mappings, lists, scalars) IS `serde_json::Value` -- no separate config AST type. `preserve_order` backs `Value::Object` with an insertion-ordered map so key iteration matches Python dict order, which `validate()`'s problem list depends on (Python dicts preserve insertion order; a sorted map would silently reorder the joined error text). Selective per-package builds (`-p gw-core -p gw`, never `--workspace`) keep this feature out of every other lane's build graph. |
| `regex` (already used by fleetview) | `groundworks/config.py`'s lens-filter tokenizer (`LENS_FILTER_TOKEN`) and the gate `where`-clause parser (`_FIELD_IN`/`_FIELD_EQ`/`_LITERAL` in `gates.py`) are regexes in the oracle; hand-rolling a second implementation risks a second, slightly different one. |
| `thiserror` | The "one `thiserror` enum per crate" decision above was written before any error type existed; `config::ConfigError` is gw-core's first, and derives `thiserror::Error` (`#[error("{message}")]`) rather than hand-implementing `Display`/`std::error::Error` -- a fix-round correction: the dependency was declared but unused (a hand-written impl) until then, which would have made the line above false. |

**Attempted, then reverted: a path dependency on `crates/ops-core`.** `_tickets/notes/2026-09-19-python-semantics-divergence-checklist.md` (§4) correctly found `config/yaml.rs` re-implementing four CPython text/number behaviours (`str.splitlines()`, `str.strip()`'s whitespace class, `int(str)`'s underscore rule, `repr(float)`) that `crates/ops-core/src/py/text.rs` already ports, two of them wrongly (rejected `1_000`, byte- instead of code-point-counted indentation). Taking `ops-core` as a `gw-core` dependency to reuse them broke THIS TICKET'S OWN GATE: `cargo clippy -p gw-core -p gw --all-targets -- -D warnings` still builds every crate in the dependency graph through `clippy-driver` (a workspace path dependency is not `--no-deps`-exempt), and `ops-core` has five pre-existing clippy findings of its own (`board_native.rs:247,542`, `py/re.rs:281`, `py/text.rs:83`, `py/time.rs:193` -- none of them in the `py::text` functions this ticket wanted) that `-D warnings` turns into a hard compile failure. Fixing them is another lane's file, outside `groundworks/`, and this ticket's `gate:` line cannot be changed to add `--no-deps` (the line is the ticket's own contract). The fix round therefore PORTS THE SAME BEHAVIOUR locally instead (`config/yaml.rs`'s `py_splitlines`/`py_lstrip`/`is_space`, `config/mod.rs`'s `py_number_str`/`float_repr`), each citing the `ops-core` function it mirrors so the two can be de-duplicated in one move once `ops-core`'s own clippy findings are cleaned up (that clean-up is that lane's ticket, not this one's). No new dependency was added.

**Not** `serde_yaml`: the config gate's error TEXT is compared (`bad_configs.txt`, byte-exact), and the oracle's `parse_yaml` is a hand-written strict subset (tabs refused, anchors refused, a specific "line N: ..." message per failure) that no general YAML parser reproduces. `groundworks/crates/gw-core/src/config/yaml.rs` is a line-for-line port of `parse_yaml`/`_parse_map`/`_parse_list`/`_scalar`/`_flow` instead.

**The `--config` harness mode** (`groundworks/harness/differential.py --candidate <bin> --config`, this ticket's `gate:` line) did not exist before this ticket -- `--goldens`/`--mcp` were the only candidate-facing modes at impl-10, and `--mcp` was a stub. It runs the candidate's `gw config spec <instance.yaml>` for every instance the SurQL goldens cover and diffs the output against `goldens/config/<instance>.spec.json`; and it drives `gw config check <fixture.json>` (one call per `broken(...)` fixture in the oracle's `tests/test_config.py`, read out via a new child mode `--_config_fixtures` so the AST-eval step never imports the oracle package into the harness's own process and risks writing a `__pycache__` into the pinned checkout) to reproduce `goldens/config/bad_configs.txt`. Both go through the same byte-exact `diff_trees` every other golden tree uses.

**Known limitations carried, not fixed (round-2 review minors)** -- each is cited inline at its site:

* **`float("inf"/"-inf"/"nan")`**: Python's `int()`/`float()` accept these and produce a real, unbounded-magnitude scalar; `serde_json::Number` has no non-finite representation (`Number::from_f64` returns `None`), so `config/yaml.rs`'s scalar parser degrades a non-finite YAML/JSON scalar to a plain `Value::String` instead. No fixture in the current corpus depends on it.
* **A digit-only integer literal past even `u64::MAX`** (e.g. `chunk_size: 10**30`) on the JSON `gw config check` path: `serde_json` parses it as an `f64` indistinguishable from a genuinely fractional/exponent literal of the same magnitude, so `config::py_int_like` (`config/mod.rs`) returns `None` (REFUSED) where the oracle's unbounded Python `int` ACCEPTs. The YAML path (`config/yaml.rs`'s `py_int`) keeps the literal as text and so can clamp it to `i64::MAX` instead of losing its int-ness entirely; `py_int_like` now does the same clamp for the narrower gap of a value that fits `u64` but not `i64` (still digit-only, e.g. `18446744073709551615`), which is the part reachable without a new dependency. Closing the wider gap needs `serde_json`'s `arbitrary_precision` feature to keep the original digits instead of a lossy `f64` -- a new dependency line this ticket's scope does not call for.

---

## 4. The mask list (transcript only)

The SurQL and config goldens are compared with **no normalization at all**. The transcript cannot be, because
the datastore clock cannot be frozen. Every rule below is a **constant in one place**
(`harness/differential.py`, top of the file), is applied to both sides, and **counts its substitutions** into
ONE counts dict with a key per rule; the two sides' counts must be equal, so a rule that silently stops firing
is itself a failure. FIX ROUND (2026-09-19, review of merge b2b7f99, verdict CHANGES): rule 5 used to have no
key in that dict at all, so this sentence was false for it — see rule 5 below.

### 4a. The launch environment is a CLOSED SET, not "whatever was exported"

Everything else about the run is pinned to a constant: the instance (`demo-incident`), the graph (built by
`scripts/run_instance.py` on a **fresh throwaway** SurrealDB), the call script, and every environment variable
the server process can see.

| Variable | Pinned to | Read at |
|---|---|---|
| `GROUNDWORKS_ADAPTER` | `groundworks.mcp.instance_adapter` | `server.py:425` |
| `GROUNDWORKS_AGENT_ID` | `harness_agent` | `server.py:11-24` block |
| `GROUNDWORKS_ROLE` | `coordinator` | `server.py:11-24`, `instance_adapter.py:443` |
| `GROUNDWORKS_LENS` | `lens_security` | `server.py:11-24` |
| `GROUNDWORKS_ALLOW_UNVERIFIED` | **`"0"`** | `code_state.py:133` (`== "1"`) |
| `GROUNDWORKS_DB_USER` / `GROUNDWORKS_DB_PASS` | `root` / `root` | `instance_adapter.py:440` (defaulted — pinned, not defaulted) |
| `GROUNDWORKS_INSTANCE`, `_DB_URL`, `_NS`, `_DB`, `_PANEL_ROOT` | computed per recording | `instance_adapter.py:434-439`, `server.py:435` |
| `GROUNDWORKS_DB_ACCESS` | **absent**, not empty | `instance_adapter.py:441/443` treats set-but-empty differently from unset |
| `TZ` | `UTC` | |
| `PYTHONHASHSEED` / `PYTHONIOENCODING` / `PYTHONDONTWRITEBYTECODE` | `0` / `utf-8` / `1` | `child_env()` |
| `PATH` | an **empty** harness directory — see 4b | |

**Any other `GROUNDWORKS_*` in the operator's shell is stripped before launch** (`PINNED_SERVER_ENV_KEYS`).
Round 2, blocking finding 4: `GROUNDWORKS_ALLOW_UNVERIFIED` had been *dropped* from `FIXED_ENV` rather than
pinned, and `child_env()` is `dict(os.environ)` — so a shell that exported it as `1` launched the server with
"writes ALLOWED BY OVERRIDE", the write calls returned `ok` with real derived ids, and the counts moved to
`engine_code 44 / path 14`. Both the pin and the strip are proven by the `ambient_allow_unverified` arm.

`GROUNDWORKS_SURREAL` (`surreal_bin.py:22`) and `GROUNDWORKS_EVAL_PORT` (`cli.py:128`) are read only by the
**harness child**, never by the server. The datastore binary the first one selects is part of what the gate
compares (`db.py`'s 3.1.x version check), so it is deliberately left to the machine; named here so the audit is
complete.

The code guard is **not** overridden (round-1 finding): every write call goes through it for real and is
refused `rejected_by_gate` — which is exactly what the `engine_guard_dropped` red arm proves the gate can see.

### 4b. The 15-second git hang: diagnosed, and removed from the recording

Round 2, blocking finding 2. The fix round left `verdict` (`"UNKNOWN"`), `problems`, `missing` and the same
sentence inside the `error` of all nine refusal envelopes **compared**, when every one of them existed only
because the engine's git re-read hit `code_state.GIT_TIMEOUT_S = 15` (`code_state.py:51`) — on this machine and
nowhere else. A gate that is green only on one machine is not a gate.

**Diagnosed, not masked** (measured 2026-09-19, Windows 11, `C:/Users/trist/projects/groundworks-oracle`,
CPython 3.13, Git for Windows). `code_state.read_state()` answers in **46–52 ms** standalone and in **160 ms**
at server *startup*, then times out at **15005 ms** on every re-read once the MCP session is live. It is not
the environment, not process nesting, not asyncio, not a worker thread: probes at every one of those layers
answered in under 90 ms. An instrumented copy of the oracle isolated the single cause —

| Variant of the identical `git rev-parse` call, inside the live server | Result |
|---|---|
| as `code_state._git` makes it (`capture_output=True`, **stdin inherited**) | **TimeoutExpired, 15054 ms** |
| same, but `stdin=subprocess.DEVNULL` | **rc 0 in 55.2 ms** |
| same, inheriting the environment too (stdin still inherited) | TimeoutExpired, 20008 ms |
| output to temp files instead of pipes, `stdin=DEVNULL` | rc 0 in 37.6 ms |
| `Popen` + `wait()`, pipes never drained, `stdin=DEVNULL` | rc 0 in 31.8 ms |

**The cause is stdin.** Once the server is being driven over MCP stdio, its fd 0 is the client's asyncio
(Proactor, overlapped) pipe, and `subprocess.run(capture_output=True)` hands that handle straight to `git`,
which then never completes. Passing `stdin=DEVNULL` — a child has no business inheriting the server's stdin —
makes the identical call return in 55 ms. **That is a defect in the ORACLE** (`code_state.py:65`), and this
ticket may not fix it: the oracle is a pinned tag and "no Python engine code is written or changed".

So the harness removes the timing-dependent input instead, at the one place it owns — the launch environment.
`FIXED_ENV` pins **`PATH` to an empty harness-owned directory**, so `read_state` answers the same constant on
every machine: `kind=UNKNOWN`, `reason="git is not installed or not on PATH"`. Nothing else in the server
shells out (checked: no `subprocess` in `db.py`, `originals.py` or the demo adapter) and it launches from an
absolute `sys.executable`, so this costs the server nothing.

*What another machine does:* exactly this. The probe is decided by PATH, not by how the machine's git behaves,
so `verdict`, `problems`, `missing` and the nine refusal strings are the same bytes on Linux, on CI, and on a
machine where git answers in 3 ms. **And the harness refuses rather than trusting it:** `record_transcript`
reads `engine_code.on_disk_now` out of the recording it just made and raises NOT EXECUTED unless it is that
exact constant, so a machine where the pin failed to take cannot write a golden at all.

*What it cost and what it bought.* Cost: the refusal reason is now "the state cannot be read" instead of
"started from branch `HEAD`, not `main`" — the branch arm of the guard is no longer the one exercised (both are
the guard, and writes are still refused `rejected_by_gate`; `engine_guard_dropped` is unchanged). Bought: no
15-second timeout anywhere, no environment in the compared data, and a recording that went from ~200 s to
**36.8 s**, so the self-test now affords three fresh recordings instead of two.

| # | Rule | Fields | The line that justifies it | Count |
|---|---|---|---|---|
| 1 | a **datetime** field, and only after the value parses as a datetime | `learned_at, created_at, updated_at, started_at, ended_at, cited_at, verified_at` | `verified_at` is written `time::now()` by the datastore when an original is re-hashed — `groundworks/originals.py:513` — and `document_get` re-hashes on every read. It is the **only** datetime the v0.2.0 tool surface returns, so without it there is nothing to mask and the transcript is not reproducible twice | **3** |
| 2 | a **randomly generated record id** in a named field | **EMPTY** | The oracle derives every id it writes: critique = `sha1(conclusion\|lens\|head)[-8:]` (`pipeline/write.py:143` + `patterns/edge_ids.py:27-29`), answer = `ans_<critique>` (`write.py:161`), every edge id the same sha1 (`edge_ids.py:27`), and `rand::` / `rand()` appears nowhere in the engine's SurQL at v0.2.0. Derived ids are **COMPARED** — the `transcript_derived_id` red arm is exactly that | 0 |
| 2b | the loader's **run token** | `run` | `patterns/edge_ids.py:32` — `"r%d" % time.time_ns()`, a clock-derived stamp on loader-written edges. Masked only where it appears (it does not appear in this call script: count 0) | 0 |
| 3 | `bytes` at the **top level of a transcript line** (the harness's own field) | `bytes` | Checked equal to that side's own recomputed, UNMASKED envelope length, then dropped — so a truncated envelope cannot pass. `data.bytes` inside an envelope (a document's size) is the **engine's** and is COMPARED. Minor (fix round): this can never fail on a real recording — the harness computes `bytes` itself at record time — so it only catches a hand-edited transcript; it is not candidate coverage | **24** lines checked |
| 4 | the **engine code guard's** VOLATILE LEAVES only, inside `engine_code` | `read_ms, kind, reason, branch, commit, dirty, dirty_count, dirty_files` (nested under `started_from` / `on_disk_now`); `engine_code_warning` and `WARNING` masked whole when present | `groundworks/mcp/code_state.py`. Only these leaves are not engine behaviour: a measured duration (`read_ms`) and a git re-read a Rust candidate has no Python checkout to reproduce at all (round 2: what it reports is no longer environment-dependent either — see 4b). **NARROWED fix round (blocking finding 1):** the original rule masked the WHOLE `engine_code` subtree, hiding `override`, `checked` and the key set — a candidate that dropped the write-authorization guard entirely could still pass. Those three, plus `verdict`/`writes`/`problems`/`missing`, are now COMPARED like everything else | **32** |
| 5 | the **harness's own launch paths**, substituted at RECORD time on both sides | the oracle checkout -> `<ORACLE_DIR>`, the panel root -> `<PANEL_ROOT>` | `panel_submit` / `panel_status` quote the resolved run_dir back in their refusal. Those paths are the harness's INPUTS, not the engine's output, and they differ per machine and per checkout. **FIXED fix round (blocking finding 3 family):** now counted as `path` in the same counts dict as every other rule, by recounting the `<ORACLE_DIR>`/`<PANEL_ROOT>` tokens already in the recorded file, instead of only being printed once at record time | **8** |

**Reproducibility, measured 2026-09-19 on the dev machine (Windows 11, `C:/Users/trist/projects`), fix round:**
`--self-test`'s CONTROL now records the transcript **twice fresh** (a throwaway SurrealDB and a freshly built
demo graph each time) and diffs run1 vs run2 AND run1 vs the committed golden — replacing the earlier version,
which diffed the committed file against **itself** and could not fail (gate-discipline: a control has to be
able to fail). The counts every side must show are the "Count" column above and `goldens/transcript-v0.2.0.masks.json`:
`{"bytes_checked":24,"datetime":3,"engine_code":32,"path":8,"random_id":0,"run_token":0}`. One recording is
36.8 s (Windows 11 dev machine, 2026-09-19); the self-test makes three of them (run1, run2, and the
`ambient_allow_unverified` arm).

**The call script** (order and arguments are part of the gate; 24 recorded lines, line 0 being the tool list):
`health, unit_search` ×3, `unit_get` ×2, `claim_explain` ×2, `eliminated_claims`, `document_get` ×2,
`critique_add` ×5, `claim_explain`, `critique_answer` ×2 (**one against the id the harness predicts the oracle
will derive** — read from a critique the fixture's own build scenario already attacked `rc_pool_exhaustion`
with, not from these calls, since every write below is refused), `panel_submit`, `panel_status`,
`document_get`, `health`. Fix round: with the code guard no longer overridden, all five `critique_add` calls
and both `critique_answer` calls are refused `rejected_by_gate` before any argument reaches the adapter
(`server.py:guarded_call` runs the guard first) — so they no longer distinguish "lands" from "wrong lens" from
"empty head"; that distinction is a follow-up (a `--mcp` candidate run, or a second call script on a branch-`main`
fixture, once one exists). Four statuses appear: `ok, not_found, invalid_argument, rejected_by_gate`
(`db_unreachable` is not exercised — a follow-up, unchanged from before).

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
* **DIAGNOSED AND CLOSED in fix round 2** (was: "the git re-read times out, recorded not chased"). It is a
  defect in the ORACLE at `code_state.py:65` — `subprocess.run(capture_output=True)` hands the child the
  server's own stdin, which under MCP stdio is the client's overlapped asyncio pipe, and `git` then never
  completes; `stdin=DEVNULL` makes the identical call return in 55 ms. Section 4b has the measurements. The
  harness cannot fix the oracle (a pinned tag), so it removes the input instead (PATH pin) and REFUSES to
  record if that pin does not take. **A follow-up for the Python engine, not for the port: `code_state._git`
  should pass `stdin=subprocess.DEVNULL`.**
* **Coverage cost, worth its own ticket** (reviewer, round 2, minor (d)): all nine write calls now return the
  same canned refusal before any argument reaches the adapter, so the transcript's write half has no semantic
  coverage — a candidate that hardcodes one refusal string passes 9 of the 24 calls. A second recording against
  a clone that also carries a local branch `main` at the pinned commit (HEAD and porcelain status unchanged, so
  `verify_oracle` is unaffected) would restore lands / wrong-lens / empty-head without giving up the refusal
  arm. Note it now needs the PATH pin lifted for that second recording too, since an UNKNOWN `on_disk_now`
  refuses writes regardless of branch.
* `--mcp --candidate` is wired to the same call script but exits NOT EXECUTED until the Rust front door exists.

## gw-ops scan (2026-10-24)

`crates/gw-ops/src/scan.rs` ports the 2026-09-17 Python estate scan (`xscholar/scan.py`, branch
`product/estate-scan`, a4136eb). Checked record for record against it on a real projects folder of
~300 projects on Windows: every record both produced was identical, in the same order. The only
differences are the one deliberate deviation stated in the module header: Python's `os.walk` descends
into directory junctions and lists what it finds there a second time under the junction's path; the
port does not (telling a junction from a symlink needs `unsafe` Windows calls, which this workspace
forbids). Each junction target is still scanned once, where it really lives. Local-only globs come from
the home's `groundworks.toml`, never from code.

## gw-store (2026-10-24)

`crates/gw-store` holds the SurrealDB data model: the schema (`schema/0001-0004*.surql`, compiled in),
migrations with a recorded schema version, and member credentials (`owner`, `agent`). Harvested from the
2026-09-17 product data model with tickets and reviews removed, receipts added, and scope policy and walls
added. `cargo store-test` proves each rule both ways against a throwaway in-memory server it starts and
stops itself; with no SurrealDB binary the tests fail as NOT EXECUTED. Measured on 3.1.6 while building it:
in a top-level field ASSERT `$parent` is NONE and `$this` is the record; inside a subquery `$this` is
rebound to the subquery's row (use a graph traversal such as `count($this.id->evidence)`); and a line like
`THIS IS NOT SURREALQL` is a valid expression (it returns `false`), not a syntax error.

## gw-ops store (2026-10-25)

`crates/gw-ops/src/store.rs` connects the MCP server to the gw-store data model. One SurrealDB server
for every session, started on demand (loopback only, rocksdb under the home, no console window, root
login passed through the environment, never the command line). Agents work under the `agent` member;
root is used only to create and migrate a namespace the first time, and to import history with its
original timestamps (an agent can never backdate a fact). Every store call is blocking and runs through
`store::blocking` (`spawn_blocking`), off the async runtime. One signed-in client per namespace is cached.
`cargo mcp-test` covers it against throwaway servers from the `gw-store` `testkit` feature: a receipt
confirms, nothing else does; a quote confirms only word for word; changing the quoted file and quoting it
again marks the old fact stale; scope levels and walls need the owner password; imports keep their time.

## Bound variables over CBOR (2026-10-25)

`gw-core::db::Db::query` runs statements with bound variables through `/rpc` in SurrealDB's CBOR
encoding (`src/cbor.rs`). Nothing in `gw-ops` or `gw-store` builds SQL from values any more; the
escaping helper was deleted. Why CBOR and not JSON: measured on 3.1.6, the JSON form of `/rpc`
re-reads string variables that look like a record id, at any depth (wrapping in an object or array does
not help), and keeps only the record part: `"gate: cd rust && cargo test"` arrived as the record
`gate:cd`. `/sql` query-string variables stay strings but every value is a string and URLs are
length-limited. The `/sql` path the Python oracle matches is unchanged (`cargo gw-test` 113 + 3 CBOR tests).

Also in this pass: facts record the login that wrote them (`written_by`, set by the store), so an owner
statement typed by an agent is shown as reported, not signed; receipts hash the full redacted output
before the display tail is cut and say what the hash covers (`hash_of`); imports keep their original
machine; the parsed registry is cached until a registry file's modification time or size changes.

## Code review fixes and the selftest (2026-10-25)

A review (an independent read of the latest commit, plus a pass over the rest for races and workflow) led
to these, each with a test that failed first or was probed live:
- Concurrent first opens of a namespace: three of four failed with write conflicts; `open_namespace` now
  retries a conflict with randomised back-off (the migration is safe to repeat).
- The plugin's server exited when no home was above the opened folder; it now serves one `status` tool.
- One agent password opened every namespace; each namespace now has its own, derived from the master
  secret, and an isolated project is refused by the shared store.
- A store that stopped was never started again; calls that cannot reach it now restart it once, and the
  server is launched outside the session's job object where Windows allows, so closing a window does not
  stop it. Two sessions starting it at once both get it.
- Imports are idempotent (deterministic ids) and validated before the duplicate check; an empty machine is
  not recorded as a machine.
- The owner can sign a fact (`note --as-owner`), after the password is checked against the owner login.
- Two project names that share one namespace are refused; the registry cache compares file contents.
- `Db::sql` and `Db::query` share one failure rule; one hashing helper; binary UUIDs decode.
- Refuted by test and kept as a regression test: 0006's `written_by` rule does not block updates to older facts.

`groundworks selftest` (and `cargo e2e-test`) builds a throwaway home and store, starts this binary as the MCP
server, and checks 15 behaviours end to end. `crates/gw-store/tests/mutations.rs` loosens each of 10 rules and
checks its protection really disappears. `scripts/license_check.py` now also checks every Cargo dependency.

## Ingestion and search (2026-10-25)

`crates/gw-ops/src/intake.rs` reads a project's tracked files and recent commits into citable units,
organised by kind (context first: CLAUDE.md, CONTEXT.md, AGENTS.md, READMEs, docs, handoffs; then doc,
config, code, commit) and by the experts whose definitions cite the project's paths (each mapping keeps
the agent file and line it came from; a bare name owns nothing when it matches more than three files).
Secret-looking files, binaries, lockfiles and files over 200 KB are skipped and counted. Re-runs skip
unchanged units by hash, mark vanished files, and re-check every quoted span against its file, so a
changed file marks the facts citing it stale. `search` is full text (BM25, stemmed) with expert and kind
filters, across shared projects, naming what it skipped. Measured on the live store: an index built over
existing rows on rocksdb cannot see an analyzer defined in the same transaction, so the index is its own
migration (0008); the testkit now runs rocksdb, like the real store.

## ask (2026-10-25): step 1 of porting the engine

`crates/gw-ops/src/ask.rs` ports the Python engine's `ask` (packet.py, check.py; v0.2.0). The check is ported
rule for rule (coverage of every candidate; quotes only from read candidates in the packet; at least 3 words;
word for word after whitespace normalisation; `support` or `note`) and is one pure function with a unit test per
rule. Stated deviations: a packet is a store record holding locators and hashes, not a folder; ranking is the
store's BM25 over ingested units with any-word matching and summed scores (as Python ranks every unit sharing a
token), stemmed, with kind and expert filters; k defaults to 20 (Python 200) because a Claude session reads every
candidate in full; `support` must come from a unit still present and unchanged since retrieval; a packet is checked
once. `search` and `ask` share one query type and one cross-project read (`fan_out`), and `search` is an excerpting
view of `retrieve`, so there is one ranked read. Measured: SurrealDB's `@@` with several words requires all of
them, so a plain-language question found nothing until retrieval matched any word.

## lens and panel (2026-10-25): step 2 of porting the engine

`crates/gw-ops/src/panel.rs` ports `groundworks/panel/panel.py` (v0.2.0): config validation, the machine
lines (STANCE, OBJECTION, NEW, FLAW), every round-2 check (missing answer or stance, novelty gate, verbatim
FLAW for a changed stance, objection categories, dissent quota by category, agreement check with
counterfactuals that must add an objection), the weighted tally with its minority report, and the
enforcement log, each with a unit test ported from `tests/test_panel.py`. Stated deviations: a panel is a
store record locked when it is opened (config and digest written once, READONLY); the round-2 label order is
sha256(digest, seat), not Python's seeded shuffle; one `panel` call anonymizes round 1 when it is complete and
tallies when nothing is owed; the owner's thresholds come from `registry/panel.toml`; there is no launch
identity, so each answer records who sent it instead of refusing another seat's.

`crates/gw-ops/src/lens.rs` ports the behaviour of `lens.py`: a lens has a charter, a standard never looser
than the store's, and reading through it shows what passes and what no longer does (Python's drift). Every
expert definition is a lens; its checks are the bold lead words under `## What you check`; its standard is
`lens-standard:` (default INFERRED). Findings are facts naming the lens; an attack is a fact that disputes
or supports another (`attacks`, `stance`), and the store refuses an attack on a fact that does not exist.
Not ported: datastore views over labels and the framework crosswalk, because the Rust store has no labels
yet. Migration 0010; four rules each proven able to go red in `tests/mutations.rs`.

### ask, after eval ask-v1 (2026-10-26)
Measured on 47 owner-approved questions (r2): 40% of `answer` calls failed the word-for-word quote check and,
because a checked packet was final, each failure cost a new `ask` (20 full candidates). Deviations from the
Python engine, stated: a packet that FAILED or was NOT_EXECUTED may be answered again (PASS stays final); a
refused quote names the candidate line sharing the most words with it; `ask`/`search` on a project also read
the projects its registry entry lists as `related` (the owner's explicit choice, so a private notes project
can be read with the project it documents). Live recorded facts rank above the files, and candidates carry
file line numbers (a quote copied with them still matches).
