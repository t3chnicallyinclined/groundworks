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
