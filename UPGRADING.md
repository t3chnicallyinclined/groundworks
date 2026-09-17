# Upgrading to v0.2

**Read this if you have an instance running on an older XScholar.** It is written for the agent doing the upgrade. Work top to bottom; each step says what breaks if you skip it.

Nothing here is optional maintenance. Two of the changes fix defects that let a run **report success while doing nothing**, and one fixes **data loss**. If you are on anything before `34960cf`, upgrade before your next load.

---

## 0. Before you touch anything

```
cd <your xscholar checkout>
git log --oneline -1          # write this down: it is what you are on now
git status --short            # commit or stash your own changes first
```

If you have a running database, **back it up now**: `surreal export --conn <url> --user <u> --pass <p> --ns <ns> --db <db> backup.surql`. Steps 4 and 5 re-apply schema against it.

```
git pull
pip install -e .              # dependencies are still [] -- this only refreshes the console script
python -m xscholar.cli test   # expect: 424 ran, 0 skipped, four checks passed
```

If `xscholar test` does not end with a summary table naming every check, your pull did not land.

---

## 1. Things that will now REFUSE what they used to accept

These are deliberate. Each one used to return success while doing nothing, so if one fires on your instance it is telling you about a real hole, not about the upgrade.

| What | Used to | Now | What to do |
|---|---|---|---|
| `loader.load()` with a missing labels directory | Returned success and **deleted every `bears_on` edge in the scope** | `LoadError` naming the path | Check the path. If a past run hit this, your edges may already be gone — compare against a backup. |
| `loader.load()` with a directory holding no `*.jsonl` | Same silent wipe | Keeps every edge; `counts["sweep"]` says `SKIPPED` | Nothing, unless you relied on the wipe. |
| `validator` on an empty slice | `PASS: 0 units, 0 labels, 0 errors` | Error: an empty slice is a failure, not a result | Only reachable if you call it directly; the chunker never emits one. |
| `plant.py --types ""` | Wrote a `[SEEDED TEST COPY]` with nothing seeded, exit 0 | Refuses before writing any file | Pass real types. |
| `agreement.kappa()` on one label value | `1.0` | `None`, with `kappa_undefined()` giving the reason | **If you stored a kappa of 1.0, re-check it.** One value across a sample measured nothing. |
| `metrics.preservation()` over zero rows | `pct 100.0, ok True` | `pct None, ok False`, categories in `checks.not_run` | A mode with no rows in scope is now visible instead of a fake 100%. |
| `queue` worker on a table with no changefeed | `0 entries, 0 errors` forever | Names the table as a counted error | See step 4 — this is probably you. |
| `Db.ping()` | Any server | Refuses a different **major** SurrealDB series by name | Run 3.1.x, or pass `ping(check_version=False)`. |
| Any suite or `run_instance.py --memory` on a port in use | **Used the stranger's database** and reported success | Refuses, naming the port | Stop that process **by PID**, never by name. |

---

## 2. If you used Docker

**The `Dockerfile`, `compose.yaml`, `.dockerignore`, `.env.example`, `compose_check.sh` and `backup_loop.sh` are deleted.** They had never been run once and the README described them as working. They are in git history at `9957070~1` if you want them.

Replace with:

```
surreal start --bind 127.0.0.1:8002 --user root --pass root rocksdb:/path/to/data
python scripts/run_instance.py <your instance dir> --url http://127.0.0.1:8002/sql
```

Back up with `surreal export` on whatever scheduler the host already has. The database version is now checked at `ping()` instead of pinned by an image.

---

## 3. Re-apply the schema, then the two things a schema re-apply silently drops

Order matters. Run all three against your database, in this order:

```
python -m xscholar.gates <your instance.yaml> --apply --url <url> --ns <ns> --db <db>
```

Then, in your own runner (or copy what `scripts/run_instance.py` does):

1. `temporal.temporal_surql(inst)` — **required.** `critique.verdict` is now tracked, so critiques get `verdict_history`. Without this a verdict downgrade is silent.
2. `queue.changefeed_surql(inst)` — **required if you use the change-feed worker.** On SurrealDB 3.1.6, `DEFINE TABLE OVERWRITE ... SCHEMAFULL` (which the schema emits) **silently drops a CHANGEFEED**. Re-apply after every schema re-apply, always.

The worker now tells you when you have forgotten: a watched table with no changefeed is a named, counted error rather than a clean pass. If it fires after this upgrade, you skipped 3.2.

---

## 4. Regenerate your gate baseline — and prove it can still fail

Your `gate_baseline.json` is now stale: the report half gained `forbids`, `forbidden_evidence`, `forbidden_evidence_reject`, `forbidden_refused_ok` and `forbids_untested`, and the spec half gained an `edges` section pinning what each edge a rule uses joins.

Until you regenerate, **your runs still pass** — a missing key is never read as a weakening — but your baseline cannot catch a regression in the new probes.

```
python -m xscholar.gates <your instance.yaml> --apply --url <url> --write-baseline <your dir>/gate_baseline.json
```

Read the diff. Nothing should be removed or lowered. Then **prove it can still fail**, because a baseline that no longer catches anything is worse than none:

```
# delete one clause from a COPY of your config, then:
python -m xscholar.gates <the copy> --apply --url <url> --db scratch --baseline <your dir>/gate_baseline.json
# expect exit 1, naming the clause you removed
```

**A new warning you may see:** `MISSING PROBE` naming a `forbids` clause. Every `forbids` clause is now probed — the generator writes the row the clause excludes, requires the write to be refused, removes it, and requires the write to succeed. If a clause's `where` is not a conjunction of `field = literal` / `field IN [literals]`, the generator cannot build that row and says so instead of silently generating nothing. **That is a real finding: the clause is untested.** Either simplify the `where` or accept that the baseline is its only guard.

Keep in mind what the probes do and do not catch: deleting a clause **from your config** still leaves an ordinary run green, because a suite generated from a config cannot notice the config changed. The baseline is what catches that.

---

## 5. Define your verdicts

`fatal`, `damaging` and `survivable` are engine constants, but **what separates them is a judgement about your risk**, and until now it was written nowhere. Measured on the first real instance: of 53 `fatal` verdicts, **32 came from one role mapping its own three review options onto these three by position** — a reviewer's "not on this draft" recorded as permanently terminal, because under the default readiness gate `fatal` has no `answered` escape.

Add to your `instance.yaml` (optional, but every role skill will otherwise tell its seat that nobody has defined them):

```yaml
verdicts:
  fatal: "Nothing could cure this: no rewrite, no further evidence. It ends the conclusion and has no answered escape."
  damaging: "A real problem that blocks readiness until answered on the record. Anything curable belongs here."
  survivable: "Recorded so the next reader sees it was considered; it does not block."
human_owned: [gates, fidelity, status, evaluator, privacy, verdicts]
```

Define all three or none. `human_owned` must list `verdicts` if you define them. Then regenerate your role skills:

```
python -m xscholar.skills <your instance.yaml> <your skills dir>
python -m xscholar.drift  <your instance.yaml> <your skills dir>
```

**Check your own role skills for a positional mapping.** If a skill offers a reviewer three options and hands them to three verdicts in order, that is the defect above. The three-ness is a coincidence, not a translation.

---

## 6. If you record critiques through `Writer` or MCP

Re-stating the same head under the same lens now **replaces** its verdict instead of creating a second critique — which is what `critique_add` always documented and never did. Before this, a second call made another blocking row inside one session and **collided on the id in a later one**, so a `fatal` head could never be downgraded and the conclusion could never be marked ready.

If your own code writes critiques with its own deterministic id, you never had the defect and nothing changes. If you relied on repeat calls creating separate rows, they no longer do — give genuinely distinct heads distinct `head` text.

---

## 7. Things you can now do that you could not

Not required, but this is what the release is for.

- **Declare your own tables and edges** in `instance.yaml` under `tables:` and `edges:`. They get schema, gate traversals, all three probe types, cleanup, PERMISSIONS and `Writer.relate()` with no engine change. `instances/demo-incident/instance.yaml` is the worked example; `instances/_template/instance.yaml` documents the shape.
- `xscholar test --require-db` — a skipped suite fails instead of printing `OK (skipped=N)`. Use it in CI.
- `xscholar test --require-privacy` — on a machine holding real data, a privacy scan that cannot run is a failure.
- The privacy scan now reads **every** extension except known binaries, and names the files it could not read. It used to walk past `.csv`, `.xml`, `.sql` and `.log` and still print "clean".

---

## 7b. Your first reload after upgrading will report conflicts. That is expected, and you must read them

**A reload no longer overwrites a corrected unit status or grade.** Before this, `loader.load()` ran an unconditional `UPSERT`. A human correction was silently put back to the source value on the next routine reload, and a unit could quietly move from usable to unusable. A real instance lost a correction this way for weeks.

Now each unit remembers what the source said at the last load, and which fields someone corrected. **But every row you loaded before upgrading has no such record yet.** Guessing "the source changed" for those rows would repeat the silent revert. So on your first reload, **any unit whose status or grade differs from the source is kept and reported as `no_provenance`**, and the load report shows it:

```
corrections  0 followed a source change, 0 held, 12 CONFLICT(S) with the source, 0 accepted from source by name
   CONFLICT unit u_0042 status: kept 'in_window' (no_provenance, by UNATTRIBUTED); source says 'redacted' -- to take the source, ...
```

**For each conflict, decide which value is right.** Do not accept them all in bulk. That would repeat the original defect by hand.
- **The kept value is a real correction:** do nothing. It is now held, and later reloads keep it.
- **The source is right:** name that unit and field and reload:
  ```
  python scripts/run_instance.py <your instance> --url <url> --accept-source u_0042.status
  ```
  Naming a unit and field with no correction to accept is refused before anything is written, so a typo cannot pass silently.

From then on, record corrections with `loader.correct_unit(db, unit_id, field, value, by, note)` so each one is attributed. A raw `UPDATE` is still caught, but it is recorded as `UNATTRIBUTED`.

**If you had facts confirmed on a unit that a past reload reverted to unusable, re-check them.** The reload that undid the correction may also have removed their support without saying so. `xscholar/consistency.py` reports every row that stands at a gated value its gate no longer supports.

---

## 7c. Two new rules you may declare: independent promotion and unique fields

**Independent promotion.** Add `independent: true` to a gate rule, and the database refuses the promotion from whoever proposed the row. **It refuses root too.** A different signed-in credential must promote it. This exists because a research lane on a real instance marked **50 authorities verified on a single reading each**: the verifier that was meant to check them was a later stage, and it died on a usage limit.
- It needs an `access:` section with a role that both reads and writes the table. Without one, loading the config fails and names the rule.
- A row written only by root cannot be promoted by anyone, because nobody is recorded as its proposer. Write rows through agent credentials.
- If your own code confirms rows as root or under a single identity, turning this on will break that code. That is the point of the rule. Fix the code, don't remove the rule.

**Unique fields.** Add `unique: {authority: [citation]}` at the top level, and add `unique` to `human_owned`.
- **If your database already holds duplicates, applying the schema fails** and names the first duplicate pair. Merge the duplicates first.
- **The comparison is exact.** `'Ref 1'` and `'ref 1 '` are different values. It stops copied rows, not rows typed differently.

Regenerate your gate baseline after declaring either rule (step 4), then prove it can still fail.

---

## 7d. Your config may now refuse to load: the evaluator's regression file must exist

`evaluator.regression_file` has been required since the first commit, and nothing ever read it. The demo declared a file that did not exist. **Now `config.load(<instance.yaml>)` stops, naming the path, if the declared file is missing, unreadable, or not in the format.** Every tool that loads your config stops with it: the runner, `gates --apply`, the MCP server, `skills`. **A relative `regression_file` resolves against the folder your `instance.yaml` is in**, not the repository root or the working directory, even when every other path in your config was written relative to the repository root; the error names the absolute path it looked at. **Since 2026-09-16 the evaluator also needs `known_errors.ids.json` beside the regression file** (`{"ids": [every entry id]}`): without it `python -m xscholar.evaluator` reports NOT EXECUTED (exit 2), an id pinned there and missing from the file is refused (exit 1), and so is an entry not pinned. Create it once with your current ids; after that, an entry is added to both files in the same change, and removing one takes a human.

- The path is resolved against the directory `instance.yaml` is in, not the current directory.
- If you have no known errors yet, create the file with an empty list. That is honest: it says the evaluator checks nothing yet.
  ```json
  {"format": "xscholar.known_errors/1", "entries": []}
  ```
- `xscholar test` now runs the demo's evaluator. An evaluator with no entries reports `NOT EXECUTED`, never a pass.
- Tests that build a config file in a temporary directory need the file beside it too (`tests/test_config.py` and `tests/test_skills_drift.py` show how).

---

## 7e. Originals: every unit keeps the exact bytes it came from

A unit used to carry only extracted text. **Now every unit links to its original**: the exact bytes it was cut from, kept in a content-addressed store beside `instance.yaml` (`originals/`, git-ignored by the store itself). Every read re-hashes them.

**What your adapter must add.** A `documents()` method that yields the originals, and a `document` key on each unit naming one:

```python
def documents(self):
    yield {"key": "app.log", "bytes": b"...",            # or "path": "/exports/app.log"; never both
           "media_type": "text/plain", "capture": "raw",   # raw | rendered_text | none (a named gap)
           "source_uri": "https://...", "retrieved_at": "2026-03-04T12:30:00Z", "original_name": "app.log"}

def units(self):
    yield {"id": "log_0001", ..., "document": "app.log"}   # the key above, or "sha256:<hex>"
```

The full contract is in `docs/MODULES.md` (intake, adapter `documents()`, `documents.jsonl`).

**Choose a mode.** Declare it, and add `originals` to `human_owned`:

```yaml
originals:
  mode: transition        # or required
  store: originals
  spans:
    log_line: {line: line}    # line: <field> | lines: [from, to] | chars: [start, end] | whole: true
human_owned: [gates, fidelity, status, evaluator, privacy, verdicts, originals]
```

| Mode | A unit with no original | Use it when |
|---|---|---|
| `required` | **Intake refuses it, by name.** | A new instance. `instances/_template` now defaults to this. |
| `transition` | Kept, and named in every health report. | An existing corpus you are still collecting originals for. |
| not declared | Behaves as `transition`, and every report says `NOT DECLARED`. | Never on purpose. |

**In every mode, nothing without a verified original can be promoted.** `scripts/run_instance.py` and `gates --apply` apply `originals.enforcement_surql` after their probes. Once it is applied, a gated promotion counts only evidence units whose original is `document.integrity = 'ok'`. So:

- **Your first run after upgrading will refuse promotions on units that have no original yet.** The refusal now says so (`GateRefusal.detail["reason"] == "originals"`, naming each unit as `NO original`, or `original missing` / `corrupt`). Citing the same unit again will never pass. Supply the original.
- In `transition` mode the units stay loaded and readable. They simply cannot support a promotion.
- `spans` makes the validator check a label's quote against the original at the unit's locator, as well as against the extracted text. A unit type with no span is counted as `not checkable`, never as checked.

**After any restore or import, run verify:**

```
python -m xscholar.originals verify <your instance.yaml> --url <url> --ns <ns> --db <db>
```

`document.integrity` is what the last load or verify found. A restore brings back the rows and says nothing about the store. Verify re-hashes every file, records what it finds, and names every missing or corrupt original.

---

## 7f. Assertions: a promoted row can say what another row's field holds, and the graph checks it

Optional. Use it if your instance has ever confirmed a fact that says, in its text, that some item is usable, while the item's own status says it is not. Nothing caught that before. Now a fact can carry a structured **assertion edge** ("unit u1's status is in_window"), and the check is mechanical.

```yaml
edges:
  asserts_state: {from: fact, to: unit}
assertions:
  asserts_state: {fields: [status], enforce: false}
human_owned: [..., assertions]
```

- Apply `assertions.surql(inst)` after the schema and gates. `scripts/run_instance.py` does.
- **Report only by default.** `consistency.report` gains an `assertions` key. The runner prints it under its own `assertions` label, never under `DRIFT_gate_no_longer_holds`, because it is a different finding with a different cure. The MCP `health` tool counts contradictions.
- After every load, the runner prints how many contradictions the load itself caused (`assertions.after_load`).
- `enforce: true` makes the datastore refuse the write that would create a contradiction. The refusal names the assertion, and the Writer now explains it as a contradiction (`detail["reason"] == "assertion"`), not as missing evidence. **The way through a stale assertion is written in `docs/PATTERNS.md`**: demote the asserting row, correct the target, re-state or retract the assertion, promote again.

---

## 7g. Independence can now require a different principal, or the promoter's own evidence

`independent: true` means "a different credential". That is unchanged. Two stricter forms exist:

```yaml
independent: {between: principal}                     # a different declared person or process
independent: {own_evidence: true}                     # the promoter created a counted evidence link itself
independent: {between: principal, own_evidence: true}
```

- **`between: principal` changes provisioning.** Every credential must be provisioned with a principal (`access.provision(..., principal="reviewer_b")`). Without one, provisioning refuses by name. A principal is what the provisioner declares; one person provisioned under two principal names is two principals.
- **`own_evidence`** needs `requires` clauses, and a role that writes both the table and one of its evidence edges. Config refuses the rule otherwise. Links created by someone else do not count, even if the promoter later touched them.
- `true` is read exactly as before. The mapping form is read through `config.independence_of(rule)`. If your own code reads `rule.get("independent") is True`, it will treat the mapping form as **off**. Change it.
- Regenerate the gate baseline. It pins the mode, and a downgrade fails it.
- Known gap (ROADMAP V2-22(g)): the originals-enforcement probes promote as root, so a rule requiring an independent promoter is reported `not_built` by them, and `gates --apply` exits 1 on it.

---

## 7h. Every gate rule now also generates an event

Each rule with clauses is emitted twice: as the field `ASSERT` it always was, and as a privileged event named `<rule>_gate`.

**Why.** An `ASSERT` subquery runs under the caller's row permissions. A credential that may write `conclusion` but cannot read `critique` counted 0 fatal critiques and was accepted (ROADMAP V2-21). The event counts every row, whoever is calling.

**What changes for you:**

- **Hand-loosening a gate now takes two statements.** Redefining the field without its `ASSERT` no longer lets the write through: the event still refuses it. A test or a script that weakened a gate that way has to `REMOVE EVENT <rule>_gate` too. The live-schema check (7i) reports either edit.
- **A `requires` clause still refuses a caller who cannot read the evidence**, because the `ASSERT` still counts 0 for them. That fails closed: a legitimate write blocked, never a hole. Removing the clauses from the `ASSERT` would fix it and would change every instance's gate text. That is left for the owner.
- The probes try a **blind caller** (a role that writes the table but cannot read what a `forbids` clause counts), but only where access is applied. `scripts/run_instance.py` now probes a second time after applying access, and prints one `blind caller` line per clause, saying what refused it. **On the demo that is the field ASSERT**: the blind role (`loader`) cannot read the `requires` evidence either. The event itself is proven by `tests/test_tamper_evidence.py`, not by the demo.

---

## 7i. Live-schema drift: the database compared with the config

`python -m xscholar.live_schema <instance.yaml> --url <url> --ns <ns> --db <db>` compares every definition the database holds with what the config generates: tables, fields, events, indexes, accesses, users and params. It reports each by name as missing, altered or unexpected. The expected side is rendered by the same server into a scratch database that is removed afterwards.

- **`scripts/run_instance.py` now runs it after every apply step and stops on any drift** (exit 1, `LIVE SCHEMA DRIFT`). A definition nobody generated survives every re-apply, because `OVERWRITE` replaces only what the config names. So a stray `DEFINE PARAM`, a second `DEFINE USER`, or a hand-added event stops your next run, named. Remove it or put it in the config.
- It needs a root (or namespace-level) login. Under an agent credential it says `NOT EXECUTED`, and so does the MCP `health` tool.
- The runner compares the components it applied: schema, gates, temporal, access, provenance, assertions, originals and lens. If your own deployment also applies the change-feed worker, pass `--components` including `changefeed`, or its definitions show as unexpected. That is on purpose.
- **It sees schema only.** It does not see data, an import, or a rule removed and put back between two runs.

---

## 7j. The promotion log: a hash chain of every promotion, and its limit

Optional. `promotion_log: true`, and add `promotion_log` to `human_owned`. An event appends one hash-chained entry per gated promotion: the rule, the row, the value before and after, the credential and its principal, and the time.

```
python -m xscholar.promotion_log <instance.yaml> --verify --url <url> --ns <ns> --db <db>
python -m xscholar.promotion_log <instance.yaml> --export-anchor anchor.checkpoint --url ... # after each batch
python -m xscholar.promotion_log <instance.yaml> --verify --anchor anchor.checkpoint --url ...
python -m xscholar.promotion_log <instance.yaml> --late --url ...
```

- **The limit, stated plainly: root can rebuild the whole chain, or truncate it and rewrite the head, and `--verify` alone still passes.** Only a verify against an anchor you exported earlier, and kept where root on the database host cannot reach, catches that. Entries after your last anchor are only as trustworthy as the database. A test asserts this limit.
- `late` flags a promotion by a credential provisioned after a writer first wrote the row. It is a heuristic: it is what "make a second login to get past the rule" looks like, and also what an honest reviewer who joined later looks like. It never blocks.
- **Promotions are serialised.** Two promotions at the same instant: one commits, the other gets a retryable "Transaction conflict" and does not happen. Retry it.
- Every probe run on a log-enabled instance adds a few entries, and they are never removed.

---

## 7k. A restore through `/import` bypasses every gate and every event

Measured on SurrealDB 3.1.6: `POST /import` and `OPTION IMPORT` write rows **without running any ASSERT or any event**. A backup restored that way can hold a confirmed fact with no citation, a unit pointing at a document row that does not exist, and a log entry rewritten past its append-only guard. Nothing refuses it, and nothing says so.

**After every restore or import, run all of these, as root:**

```
python -m xscholar.originals verify <instance.yaml> --url <url> --ns <ns> --db <db>
python -m xscholar.live_schema <instance.yaml> --url <url> --ns <ns> --db <db>
python -m xscholar.promotion_log <instance.yaml> --verify --anchor <your last anchor> --url ...   # if the log is on
python -m xscholar.gates <instance.yaml> --apply --url <url> --ns <ns> --db <db> --baseline <your baseline>
```

Then read `consistency.report` (every row standing at a gated value its gate no longer supports) and the assertions report. `gates --apply` re-probes the gates in a scratch database when originals enforcement is already applied, and says so, because its probe units carry no originals.

---

## 7l. Smaller behaviour changes

- **Answering the same critique again revises the answer** instead of failing with "record already exists". One answer row per critique, one `answers` edge. The revision is on the record: `answer.text_history` is append-only, written by an event, and names who changed it and when. Apply `temporal.temporal_surql(inst)` again to get it. If your code relied on a second `answer()` raising, it no longer does.
- **The Writer words two more refusals correctly.** An originals refusal and an assertion refusal used to be explained as "needs at least one cited unit whose grade is in ...", which sent agents round an endless retry. `GateRefusal.detail["reason"]` is now `originals` or `assertion`.
- **A gate on a built-in field keeps that field's own list.** A gate on `unit.status` or `unit.grade` used to replace the field's definition and drop `$value IN [...]`, so the field accepted any string once gated (ROADMAP V2-22(a)). Re-apply your gates if you gate either. A gate on a field the engine cannot restate (neither built in nor declared) now refuses to generate, by name.
- **MCP:** a new read tool, `document_get`, returns a unit's original, re-hashed. `health` gains `live_schema`, `promotion_log`, `assertions` and `originals.gap_units_without_verified_original`. A check the server's connection cannot run says `NOT_EXECUTED`, never 0. Regenerate your role skills: the tool table changed.
- **One throwaway-server start.** `xscholar/surreal_bin.py start_memory` is the only implementation. `scripts/run_instance.py` and the evaluator both call it. If you copied `start_memory` into your own scripts, call this one instead.

---

## 7m. The MCP server refuses writes from unverified engine code, and `health` changed shape

From real use: a live MCP server served write tools from a work-in-progress branch someone had checked out in the engine checkout, and kept serving that code after the checkout was switched back (a process keeps what it imported).

| What | Used to | Now | What to do |
|---|---|---|---|
| Any write tool (`critique_add`, `critique_answer`, `claim_propose`, `claim_confirm`, `claim_rule_out`, `claim_support`, `conclusion_mark_ready`, `panel_submit`) | Ran from whatever engine code the server loaded | **`rejected_by_gate`** unless the server started from branch `main` with no uncommitted changes, and the checkout is still that commit and branch with none now. `missing` names the cure | Launch the server from a clean `main` checkout of the engine. **If writes are refused with `STALE`, restart the server**: the checkout moved under it |
| An engine that is not a git checkout (an installed copy, an archive) | Served writes | `engine_code.verdict` `UNKNOWN`, **writes refused** | Run from a checkout, or a human sets `XSCHOLAR_ALLOW_UNVERIFIED=1` in the launch entry; `health` then leads with a `WARNING` and every write result carries `engine_code_warning` |
| `health` result | `{"instance", "server"}` | `{["WARNING"], "engine_code", "summary", "instance", "server"}`, in that order. `instance` is unchanged; `server` gains `write_tools` | A caller that reads `data["instance"]` keeps working. Read `data["summary"]["headline"]` and `attention` first |
| `health(detail=false)` | (did not exist) | Same, with `instance` replaced by a one-line note; the summary is still derived from the full detail | Use it for the pre-write check |
| `health` when the datastore is down | `db_unreachable` and nothing else | Also carries `engine_code` | Nothing |
| Generic adapter `health` counts under a provisioned (record) credential | **Read 0**: an unfiltered `count() GROUP ALL` returns 0 to a record credential even when it can read the rows (SurrealDB 3.1.6, measured), and a table it cannot read also counted 0 | True counts; a count over a table the credential's role cannot read is the string `NOT_EXECUTED: ...`. **A count may now be a string** | Launch agent servers with `XSCHOLAR_ROLE` equal to the credential's access role (what `--provision` prints). Without it a zero count under a record credential reads `NOT_EXECUTED` |
| `xscholar.mcp.server.build_server(adapter, provenance, panel_root)` | No guard | Takes `guard=`; without one it reads the real engine checkout | Tests that exercise writes pass `guard=code_state.CodeGuard(reader=lambda: code_state.git_state(<sha>, "main"))` |

**Check your own adapter against the contract** (read-only, safe against a live graph): `python -m xscholar.mcp.conformance --query "<words a unit contains>" --claim <an id>`, with your launch environment set. It names every missing, empty or mistyped field and every wrong status. `--write --scratch NS/DB` adds the write methods, only against the database named. Measured on the engine's own generic adapter over the demo: it FAILS the contract docstring on `unit_search` (no `method`), `unit_get` (returns one unit, not `{locator, units}`), `eliminated_claims` (`claims`, not `eliminated_claims`; no `about` / `failed_approaches`) and `claim_explain` (`blocking_now`, not `blocking`; no `support` / `adverse` / `critiques` / `ready`; kind `fact`). Not changed in this release: it is recorded in `docs/DECISIONS.md` as open.

---

## 7n. Links to missing records are refused, gated fields need a list, and a schema re-apply keeps permissions

Measured on SurrealDB 3.1.6; `notes/2026-09-16_gates_lane_enforced_edges_and_domains.md` has the tables.

- **Every relation table is now `ENFORCED`.** `RELATE` to a record that does not exist is refused (`The record
  'x:y' does not exist`) instead of storing a link to nothing. **ENFORCED does not check links already stored.** After
  re-applying the schema, `gates --apply` prints `DANGLING: <n> <edge> edge(s)` for each edge table holding links to a
  missing record and **exits 1**. Repair or delete them, then run it again. A load or a `Writer.cite`/`relate` that names
  a record not written yet now fails loudly; write the endpoint first.
- **Re-applying the schema is now safe after access and the change feed.** Tables are `DEFINE TABLE IF NOT EXISTS`
  then `ALTER TABLE`, so PERMISSIONS and CHANGEFEED survive (before: 14 of 20 demo tables reset to PERMISSIONS NONE, 5
  changefeeds dropped). Step 3's "re-apply the changefeed after every schema re-apply" is no longer needed for that
  reason, and harmless. The flip side: a schema re-apply no longer resets a hand-widened PERMISSIONS clause; re-apply
  access for that, and run `python -m xscholar.live_schema`.
- **Your config may refuse to load:** a gate rule whose field has no enumerated domain (no vocabulary list, no
  `one_of`, not a bool), a gate `value` outside the field's values, or a clause `where` term (`field = literal`,
  `field IN [...]`) on such a field or with a value outside its list. Each is named. Give the field `one_of`. A
  misspelt value there was being stored and silently dropped the row out of evidence, or, in a `forbids` clause,
  let the forbidden row through.
- **A rule that only forbids no longer reports as misbehaving** when it is correct. If you had learnt to ignore that
  line, stop: it now means something.
- **Rules that used to report MISSING PROBE now run:** staged evidence behind an independence rule, and the originals
  probes of a rule that needs an independent promoter, run on provisioned probe credentials. They need access applied
  (they say so by name when it is not). Your baseline's report may gain flags; regenerate and review it (step 4).

---

## 7o. A label's quote must say enough to carry its reason

Reported from two instances on 2026-09-16. A tier-3 label quoting just `the` passed the validator with 0 errors: the
quote was verbatim, and `the` is in almost every unit. Verbatim proves the words exist, not that they carry the reason.

| What | Used to | Now | What to do |
|---|---|---|---|
| A tier >= 2 quote of fewer than 3 words **cut from a longer unit** (`the`, `A. Yes.` out of a Q-and-A turn) | PASS | `FAIL <unit> quote ... under 3 words a quote must be the whole unit` | Quote the words that carry the reason, or the whole unit |
| A short quote that **is the whole unit** (a witness's whole answer `Yes.`, whitespace collapsed) | PASS | PASS, unchanged | Nothing |
| `labels:` in instance.yaml | Refused (unknown key) | Optional, **human-owned when present**: `quote_min_words: <n>` (default 3; `1` is the old rule) and `quote_distinct_units: {<unit type>: <n>}` | See below |
| `quote_distinct_units` (off unless declared) | (did not exist) | For a declared unit type, a quote found in more than n units of the slice is refused, unless it is the whole unit | Declare it for prose units (document sections). **Do not** declare it for transcript turns: `Yes.` is in every slice |
| Validator report | | Gains `quote_rules`: the rules it applied | Nothing |

**Why not a length floor or distinctiveness by default:** measured on the appeal instance's 2,471 tier >= 2 labels,
331 of the 340 quotes under four words were a witness's entire sworn answer, some of the most important evidence
there. Either rule would have refused them. A short fragment of a longer unit is the degenerate case; a short whole
unit is not.

**Before you upgrade, measure it:** run the validator over your existing labels. Every new `quote` error names its
unit. Fix the quote, or, if the owner decides your short fragments are sound, set `labels: {quote_min_words: 2}` (or
`1`) and add `labels` to `human_owned`. What cannot be caught: a real, long, distinctive quote pasted under the wrong
reason. That still takes a reader.

---

## 8. What this release does NOT give you

- **No container.** Deliberate, see step 2.
- **No public demo instance.** `demo-incident` is synthetic. Nothing here shows the system working on real material that a stranger can inspect.
- **The MCP server has never been driven by a real external application** — only by its tests and by agents in this repo. If you are the first to point a real app at it, expect to find things, and say what you find.
- **Presentation is not worth your time.** Two measurements, no adoption: the speed-read view did not improve error catching (4/15 vs 3/15, and **0 of 12** relabelling errors caught in either arm), and rewording the rule-check marker did not either (0/8 → 2/8, inside noise, and adding hand-checkable counts **lost every verdict-softening catch**, 0/8 against a control's 4/8). Both are written up in `notes/`.

---

## Verifying the upgrade landed

```
python -m xscholar.cli test --require-db        # 424 ran, 0 skipped, four checks passed
python scripts/run_instance.py <your instance> --memory <a free port>
```

The demo run should end with a scenario line counting what the engine actually saw — steps, calls through the typed write surface, gate refusals, permission denials — and a health line with `DRIFT_confirmed_without_valid_cite: 0`. If the scenario line says `0 refused by a gate`, the walkthrough did not exercise a gate and the run now tells you so instead of printing "done".
