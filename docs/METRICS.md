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

## The `cost` numbers (`groundworks cost`; the pulse shows the newest line)

`groundworks cost` (gw-ops `meter.rs`, a port of tokenmeter.py starting from fleet-core rs-02) reads Claude
Code's transcripts under `<claude dir>/projects` for **counts only**: `message.usage` numbers, the model, tool_use
names, timestamps, tool_use ids, and the working directory each line records, which it maps to a registered
project (its worktrees included) or to a class (`(root)` = the workspace itself, `(workspace)` = an unregistered
folder in it, `(home)`, `(outside)`), never written as a path. A transcript folder whose name, dashes read as
spaces, matches a `scan.local_only` glob, or names an isolated project, is never opened; a transcript whose
recorded working directory falls under either is dropped whole. One line per run goes to
`<home>/metrics/cost.jsonl`; `<home>/metrics/cost-cache.jsonl` (counts only, keyed by session, size, mtime and
byte offset) makes the next run parse only what changed. `pulse` reads the newest `cost.jsonl` line and never a
transcript. Each line names its machine and the scan's wall ms. Window: 7 days; previous window: the 7 before.

| Field (per span: `window`, `previous_window`, `all_time`; per project: `window`, `all_time`, `before`, `after`) | Definition |
|---|---|
| `turns` | API responses, deduplicated on (message.id, requestId) across every transcript, first occurrence wins |
| `input`, `cache_read`, `cache_write`, `output`, `tokens` | the usage numbers summed; `tokens` is all four |
| `sessions`, `contexts` | distinct session ids (a main session and its subagents are one), transcript files |
| `human_turns` | user lines whose content is typed text, in main sessions (tokenmeter's `prompts`) |
| `tokens_per_session`, `tokens_per_human_turn` | `tokens` / `sessions`, `tokens` / `human_turns` |
| `main_ctx_p90` | p90 of input + cache read + cache write per main-session turn (tokenmeter's index rule) |
| `usd`, `unpriced_turns` | derived from `prices.toml` (data); a model without a row is counted as unpriced, never as $0 |
| `gw_calls`, `asks`, `ask_latency_p50_ms`/`p95` | Groundworks tool calls (`mcp__plugin_groundworks_groundworks__*`, `mcp__groundworks__*`); latency is tool_use to tool_result time |
| `gw_result_bytes`, `brief_bytes`, `gw_tokens_est`, `overhead_share`, `packet_bytes_p50`/`p95` | **only with `--overhead`** (off until gw-security signs the read): byte lengths of Groundworks tool results and of the SessionStart brief (an attachment carrying the `groundworks-journal` frame); `overhead_share` = Groundworks bytes carried in context (until a compaction) / 2.98 bytes per token / all input tokens, an estimate |
| `rereads_per_session_median`, `reread_calls`, `reread_bytes`, `search_bytes_before_ask`/`after_ask` | **only with `--rereads`** (off until signed): Read's file_path is hashed in memory and dropped; a (file, offset, limit) read 3+ times in a context is a re-read; Read/Grep/Glob result bytes before and after the context's first ask |
| `commits`, `gate_receipts`, `work_per_mtok` | git commits on every branch, and `gate: ` receipts, of registered projects; (commits + gates) per million `tokens`; None outside registered projects |
| `first_seen`, `before`, `after` | the earliest hook session row or Groundworks call for the project; all time split there. **A trend, confounded** (model mix, task mix), never proof: docs/EVAL-HELPS-OR-HURTS.md is the proof |
| `asks.asks`, `passed`, `previous_*`, `gw_tokens_per_passed` | ask packets in the store (eval sessions apart) this week and last; ask result bytes / 2.98 / passed (with `--overhead`) |

Red (in `cost.red` and the pulse digest): overhead share above 10% of input in the window (an inference, to be
reset after the first measurement with `--overhead`); tokens per human turn up more than 20% week on week with no
model's share of turns moving more than 10 points; asks passed share falling week on week. The pulse adds: the
Stop hook's p95 (`hook p95` line, Stop on its own) above 300 ms.

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
