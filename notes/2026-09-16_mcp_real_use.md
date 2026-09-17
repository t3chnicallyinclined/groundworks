# 2026-09-16 The MCP server, from real use

The only real instance on the engine uses the MCP server and config validation, nothing else. Six findings from it, and what was measured while fixing them. Lane `fix/mcp`.

## 1. A server serves whatever engine code it loaded, write tools included

A WIP branch was checked out in the engine checkout a live server launches from. The server served it, writes included, and kept it after the checkout was switched back: a Python process keeps what it imported at startup and imports the rest from whatever is on disk when first needed.

Fix: `xscholar/mcp/code_state.py`; decisions in docs/DECISIONS.md. Measured cost of one state read (`git rev-parse` plus `git status --porcelain -z --untracked-files=all`) on this Windows 11 machine, in this repository: 71-88 ms, averaged over 20 reads. Unthrottled: writes are agent-paced.

Two edges found while writing the reader: a path with a space broke a whitespace split of the `rev-parse` output (it splits lines now); and an engine installed inside another repository (a virtualenv in a project) would have borrowed that repository's commit, so git's top level must be the engine directory or the state is UNKNOWN.

## 2. `health` about 60 KB, the number that mattered buried

`health_summary.py` derives the summary from the detail with no knowledge of keys. The unflattering part: it cannot tell a flattering non-zero from an unflattering one (`units: 500` and `late: 2` both land in `nonzero`). Only NOT_EXECUTED markers, false flags, drift-named numbers and the engine code verdict reach `attention`. A `live_schema.missing: 3` is listed under `nonzero`, not `attention`, because nothing generic says `missing` is bad.

## 3. No way to check a real adapter against the contract

`python -m xscholar.mcp.conformance`. Run over the engine's own generic adapter on the demo data: 7 read checks pass, **6 fail**, in four of the five read methods (listed in DECISIONS, Open). The contract docstring and the engine's generic adapter disagree; which one is right is the owner's call.

## 4. "NOT_EXECUTED, never 0" had never been seen to fire through the tool

The adapter-level NOT_EXECUTED for live-schema and the promotion log did work under an agent credential (tests/test_tamper_evidence.py). Forcing it through the MCP tool found a worse hole: every table count in the generic adapter's health read **0 under any record credential**.

Measured on SurrealDB 3.1.6, one row in each table, credentials of roles `reader` (reads unit and fact) and `loader` (reads unit, not fact):

| Query | root | reader | loader |
|---|---|---|---|
| `SELECT count() AS n FROM unit GROUP ALL` | 1 | **0** | **0** |
| `SELECT count() AS n FROM unit WHERE true GROUP ALL` | 1 | 1 | 1 |
| `RETURN count(SELECT VALUE id FROM fact)` | 1 | 1 | **0** (cannot read fact; no error) |

Every health test ran as root, so none saw it. The fix counts with a WHERE, and uses the credential's access role to report a count over an unreadable table as NOT_EXECUTED.

## 5. `rejected_by_gate` with `missing`, never exercised end to end

Proven through the real tools on a throwaway server for the evidence gate, the readiness gate, an independence refusal, an originals refusal (from the datastore, and from the adapter's store re-hash) and an assertion refusal: tests/test_mcp_db.py. All were already worded correctly; no code change was needed.

## 6. A vacuous test

`all(... for o in owed)` passed on an empty list. It now requires the seat's own entries, and was shown to fail when the seat filter drops them (M224).
