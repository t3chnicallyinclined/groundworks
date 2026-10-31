# Pulse JSON contract

The contract below is gw-architect's A3.5 (design ticket DESIGN-2026-10-30-groundworks-first-full-ingest-and-kpis,
amended with A3.8), copied verbatim. gw-architect owns this file: a change to the contract is written here first.

**As built (A3.1 step 7, 2026-10-30):** `schema` is 2 (`gw_ops::pulse::SCHEMA`); `read` is present as `null`
until W3 adds it; `intake` is not in the object yet and arrives with W1's merge as a map (`{}` when empty); `red`
holds the digest lines. The contract test is `pulse::tests::the_pulse_json_contract_holds_for_schema_2_and_schema_1_lines`
over `rust/crates/gw-ops/fixtures/pulse_schema2_golden.json` and `pulse_schema1_first_line.jsonl` (the first line
recorded on THERIGXD, 2026-10-26: counts only, the project names replaced by `p01`..`p18`).

`docs/PULSE.md` is owned by gw-architect, and step 7 writes it.

- **Version.** Every pulse object carries `"schema": 2`. Lines without it are schema 1 and read as today.
- **One object.** `groundworks pulse --json` prints exactly the object appended to `metrics/pulse.jsonl`.
- **Rules.**
  - Within a major: fields are only added; never renamed, retyped or removed.
  - Consumers ignore unknown fields.
  - A missing optional section is `null`, not absent.
  - Removing or changing a field bumps `schema`, and the change is written in PULSE.md first.
- **Top level:**
  - `schema`, `at`, `machine`, `projects`;
  - `totals`, `per_project` (the store numbers);
  - `asks`, `asks_eval`, `asks_passed`, `asks_failed`, `asks_not_executed`, `asks_served_from_knowledge`,
    `recorded_in_window`;
  - `last_export`, `last_drill`;
  - `use` (METRICS.md, now including `hook_p95_ms_by_event`);
  - `cost` (W2's `CostLine`: `at`, `machine`, `window_days`, `totals.window`/`previous_window`/`all_time`, `asks`,
    `red`);
  - `intake`: a map project -> W1's `IntakeLast` as built (423eda3): `{at, commit, dirty, files_read,
    files_tracked_read, files_unchanged_by_blob, units, written, skipped, wall_ms, warm}`, from the newest two
    `intake_run` rows; an empty map `{}` when there are none (amended A3.8: the contract follows the code);
  - `read`: a map project -> `{run, status, coverage, kappa_tier}` (W3);
  - `red` (the digest lines, as text).
- **Test.** A contract test in pulse.rs loads a schema-2 golden object and a schema-1 line from today's
  `pulse.jsonl`, and both parse. The window's health view reads only this.
- W1 and W3 conform when they add their sections. W2 already does (its `cost` is `Option`, so it serialises as null).
