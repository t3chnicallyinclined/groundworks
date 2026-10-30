# METRICS.md: every number in `groundworks pulse`, and when it is red

Owner: the Groundworks team (gw-engineer wrote it 2026-10-29 for decision `1itizcerhaj5x1mjfyj2`, layer 5,
owner go `cgilrpv6ynkwmvu3u929`). The pulse reads the store with no model and appends one JSON line to
`<home>/metrics/pulse.jsonl`; `pulse --history` prints the trend; `pulse --digest` prints one line per red
condition and nothing else. The supervisor takes a pulse after each nightly export.

A number without the box it came from is not a result: every pulse line carries `machine`, and a machine
that cannot name itself is itself red.

## Where the numbers come from

| Source | Table | Written by |
|---|---|---|
| Sessions | `session` (global) | the `SessionStart` hook: one row per Claude Code session the hook saw, registered project or not (`cwd_class` says which kind of folder, never the path) |
| Hook decisions | `hook_event` (global) | every hook run: `hook`, `decision`, `rule_id`, `tool_uses` counts, `journal_writes_since_start`, `latency_ms`, `error_code` |
| Refused writes | `rejection` (global) | the store, whenever `note`, `journal_add`, `import_journals`, `ask` record or `panel_submit` is refused: a code, never the text |
| Journal | `fact` (per project) | `journal_add`, `note`, `answer` (recorded facts) |
| Receipts | `receipt` (per project) | `run` and `gate` |
| Commits | the project's local git repository | `git rev-list --count --all --since=<span start>` |

Telemetry holds codes, counts, ids and timestamps only (team/security `TELEMETRY-POLICY.md`). Rows are
append-only for members; root prunes `session` and `hook_event` after 90 days and `rejection` after 365, so
"all time" means since the oldest surviving session row (`use.telemetry_since`).

## Spans and scopes

Every use number is computed four ways: for the **window** (`use.window_days`, 7 days ending at the
pulse) and for **all time**, per project (`use.per_project.<name>.window` / `.all_time`) and in total
(`use.window` / `use.all_time`). The total counts sessions that started outside any registered project
too (`project` null); the per-project numbers do not.

## The `use` numbers

| Field | Definition | Red when |
|---|---|---|
| `sessions` | `session` rows whose `started_at` is in the span and whose `project` is the scope's | — (a project with projects registered and `sessions = 0` all time means the hooks never ran there; see `commits_without_sessions`) |
| `sessions_that_wrote` | sessions with at least one `Stop` or `SubagentStop` row whose `journal_writes_since_start > 0` | — |
| `write_rate` | `sessions_that_wrote / sessions`; 0 when `sessions = 0` | **< 0.5 for a project with ≥ 3 sessions in the window**: sessions start and record nothing |
| `blocks_at_stop` | `Stop`/`SubagentStop` rows with `decision = block` and `rule_id = no-writes` | — (informational: how often the gate had to ask) |
| `blocks_converted` | blocks followed, in the same session, by a later Stop row with `journal_writes_since_start > 0` | — (a low share means agents end sessions without recording even when asked; read with `blocks_at_stop`) |
| `denies` | `PreToolUse` rows with `decision = deny` (every rule, including `rules-unloadable`, `store-down` and `hook-selfedit-file`) | — |
| `denies_converted` | denies followed within 10 minutes by a `run` receipt in the same project: the agent took the sanctioned path | — |
| `hook_errors` | hook rows with an `error_code` | **> 0 in the window**: a hook failed internally; `error_code` names the kind (`store_down`, `regex_compile`, `transcript_unreadable`, ...) |
| `hook_latency_p50_ms`, `hook_latency_p95_ms` | nearest-rank percentiles of `latency_ms` over the scope's hook rows (measured inside the hook: process start, about 40 ms on Windows, is not in it) | — (watch p95: hooks run on every Bash call; the window plan's limit is 300 ms wall) |
| `journal_writes` | `fact` rows of every kind recorded in the span | — |
| `writes_by_kind` | the same, by `decision` / `gotcha` / `status` / `fact` / `eliminated` | — |
| `writes_by_confidence` | the same, by `CONFIRMED` / `INFERRED` / `UNKNOWN` | — |
| `writes_by_agent` | the same, by the free-text `by` (not a signed identity; `[owner]`/`[agent]` in the injected brief is) | — |
| `confirmed_share` | CONFIRMED writes / `journal_writes` | — (should rise over weeks; a receipt or quote is what makes it CONFIRMED) |
| `store_rejections` | `rejection` rows in the span | **rising week on week**: `use.window.store_rejections > use.rejections_previous_week` (and > 0). A burst from one agent is a prompt-injection or confused-agent signal |
| `commits` | git commits on every branch of the project's local repository in the span (`git log --all --format=%ct`, committer time); `null` when there is no repository here | — |
| `commits_without_sessions` | `commits` when the span had commits and **no session** for that project, else 0; `null` with `commits` | **> 0 in the window**: work happened with no hook running: a Copilot or VS Code session (no hooks there; only the store's own rules and this cross-check apply), or the plugin off |
| `commits_unseen` | commits with **no `hook_event` row and no `run`/`gate` receipt of the same project within 15 minutes** either side of the commit's time (`COMMIT_COVER_SECS`); `null` with `commits`. A commit made through a hooked session's Bash call has its PreToolUse row seconds before it | **> 0 in the window**: a session beside the hooked ones did work the hooks never saw (the plugin off in one window, a client without hooks, a terminal). This is the red arm `commits_without_sessions` lacked: the 2026-10-29 window probe ran a session with the plugin disabled beside hooked sessions of the same project; it left 0 rows, its commit fell in a week that had sessions, and the pulse stayed green. The digest names a project once: by `commits_without_sessions` when it had no session at all, else by this |

Other red conditions in the digest:

| Condition | Red when |
|---|---|
| `machine` | equals `unknown` (doctor fails on the same) |
| no sessions | `use.all_time.sessions = 0`: the hooks have never run against this store (install the plugin, open a session, run `doctor`) |

## Reading it

- `write_rate` is the number the design asked for ("are we using it in project X"): sessions that start
  under a project and end without a journal entry. The Stop hook blocks once for exactly this, so a
  low rate with `blocks_converted` near `blocks_at_stop` means the gate works and agents comply; a low
  rate with `blocks_converted` far below `blocks_at_stop` means they end anyway (warn mode, or sessions
  killed), and the hook_event rows say which.
- `commits_without_sessions` and `commits_unseen` are the deadman: the only signals for sessions the hooks
  cannot see. Their limits, stated: work that is never committed is not seen at all; a hookless session that
  commits within 15 minutes of a hooked session's row in the same project is counted as seen; commits made
  on another machine and fetched here count as unseen on this one (`--all` includes remote-tracking refs).
- `denies` by `rule_id` (query `hook_event` directly) says which bypasses agents reach for; a rule that
  never fires costs nothing, a rule that fires daily is either doing its job or needs a sanctioned path.
- `store_rejections` by `reason` says what the store refused: `confirmed-needs-evidence` is Layer 0
  working; `kv-secret`, `jwt`, `pem-private-key`, ... are values that almost reached the journal.

## Deadman and the other clients

Copilot CLI and VS Code sessions have no hooks. For them the store's own rules (no CONFIRMED without
evidence, no secret-shaped text) and `commits_without_sessions` are the whole measurement, which is why
`docs/VSCODE-MCP.md` wires the same `groundworks serve` into VS Code: a session that at least has the
tools can read the journal and write to it, and its writes show in `journal_writes` and `writes_by_agent`.

## Retention and history

`pulse.jsonl` keeps every pulse line, so counters survive the 90-day pruning of the raw rows; `pulse
--history` adds `sessions`, `wrote`, `blocks` and `denies` columns for the window. A pulse taken before
2026-10-29 has no `use` section and reads as zeros.
