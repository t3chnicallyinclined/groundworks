# Plugin skeleton: load proof + per-project MCP config verdict

Ticket: `2026-09-17-product-plugin-skeleton`. Skeleton lives at `plugin/` (placeholder name `xsp`,
held in one place: `plugin/.claude-plugin/plugin.json`'s `name`, `plugin/.mcp.json`'s server key,
and the skill directory `plugin/skills/init/`).

## What it proves

- a skill `xsp:init` (`plugin/skills/init/SKILL.md`) — stub body only;
- a SessionStart hook (`plugin/hooks/session_start.py`, wired by `plugin/hooks/hooks.json`) that
  walks up from cwd to the nearest `.xsp/` and emits `additionalContext` naming the resolved root
  and namespace (`workspace` or `proj_<slug>`), or `"xsp: no .xsp found"`;
- an MCP server (stdio, `plugin/mcp/server.py`, `mcp>=1.26` via FastMCP as
  `xscholar/xscholar/mcp/server.py` does) exposing one tool `where` that resolves the same way.

All three load through `--plugin-dir` with no installation, no marketplace, no writes under the
owner's real `~/.claude`. `plugin/resolve.py` is the single resolution rule both the hook and the
MCP tool call, so they cannot drift from each other.

## Load proof (real run, 2026-09-17)

Run from a temp folder under the session scratchpad (never `C:/Users/trist/projects` itself, never
the owner's real `~/.claude`):

```
mkdir -p /tmp-scratch/xsp-load-proof/.xsp   # marker only; no "workspace" file -> project namespace
cd /tmp-scratch/xsp-load-proof
claude -p --plugin-dir C:/Users/trist/projects/xscholar-plugin/plugin --model haiku \
  --dangerously-skip-permissions \
  "Call the MCP tool named where (from server xsp) with no arguments and report the raw JSON \
   result. Then state, verbatim, any additionalContext you were given about xsp at session start. \
   Do nothing else."
```

Output:

```json
{
  "found": true,
  "root": "...\\scratchpad\\xsp-load-proof",
  "namespace": "proj_xsp_load_proof"
}
```

Confirmed independently with `-d hooks --debug-file hook_debug2.log` (debug log, same run):

```
Read hooks.json for plugin xsp (enabled=true): .../plugin/hooks/hooks.json
Loaded 1 skills from plugin xsp default directory
MCP server "plugin:xsp:xsp": Starting connection with timeout of 30000ms
Hooks: Checking first line for async: {"hookSpecificOutput": {"hookEventName": "SessionStart",
  "additionalContext": "xsp: resolved root ...\\xsp-load-proof, namespace proj_xsp_load_proof"}}
Hook SessionStart (python "${CLAUDE_PLUGIN_ROOT}/hooks/session_start.py") provided
  additionalContext (177 chars)
MCP server "plugin:xsp:xsp": Successfully connected (transport: stdio) in 930ms
MCP server "plugin:xsp:xsp": Connection established with capabilities: {"hasTools":true,...}
```

All three mechanisms load and answer correctly under `--plugin-dir`: skill (1 loaded from
`plugin/skills/`), SessionStart hook (its own `additionalContext`, distinct from the model's
paraphrase above, confirmed in the debug log), MCP server (`plugin:xsp:xsp`, tool `where` called
and returned the same resolution as the hook).

## Per-project MCP config verdict: CONFIRMED

Question: can the MCP server config be per opened project (project `.mcp.json`, cwd-derived args,
or env like `${CLAUDE_PLUGIN_ROOT}`/`${CLAUDE_PROJECT_DIR}`), or is it only plugin-global?

**Two separate mechanisms exist; neither lets a project override the plugin's own server, but one
does make the plugin's server config vary per project.**

### 1. A project's own `.mcp.json` does NOT merge into or override the plugin's server

Experiment: opened `projA/` containing its own `.mcp.json` declaring a server also named `xsp`,
with `args: ["${CLAUDE_PLUGIN_ROOT}/mcp/server.py"]` and an extra `env.XSP_PROJECT_TAG`, while
`--plugin-dir` still pointed at the real plugin.

Debug log (`-d mcp`), same run:

```
MCP server "xsp": Starting connection with timeout of 30000ms
MCP server "plugin:xsp:xsp": Starting connection with timeout of 30000ms
[ERROR] MCP server "xsp" Server stderr: python: can't open file
  '...\projA\${CLAUDE_PLUGIN_ROOT}\mcp\server.py': [Errno 2] No such file or directory
[ERROR] MCP server "xsp" Connection failed (CONNECTION_CLOSED): Connection closed
MCP server "plugin:xsp:xsp": Successfully connected (transport: stdio) in 1007ms
...
ToolSearchTool: selected mcp__plugin_xsp_xsp__where
```

Two distinct servers were loaded under two distinct names: the project's `xsp` (unqualified) and
the plugin's `plugin:xsp:xsp` (namespaced by plugin name). They do not merge or collide by name.
`${CLAUDE_PLUGIN_ROOT}` is a plugin-manifest-only substitution — a project `.mcp.json` that reuses
it literally gets the unexpanded string and the spawn fails. Only the plugin's own
`plugin:xsp:xsp` answered `where`; `_experiment_xsp_project_tag` (set only in the project's failed
server's env) was `null` in the plugin server's result, proving no env leaked across.

**Conclusion:** a project cannot configure or override the plugin-declared MCP server by writing a
same-named `.mcp.json` entry. Project-level and plugin-level MCP servers are separate, additive,
independently namespaced processes.

### 2. `${CLAUDE_PROJECT_DIR}` in the plugin's OWN `.mcp.json` DOES vary per opened project

Experiment: added `env.XSP_PROJECT_DIR_PROBE: "${CLAUDE_PROJECT_DIR}"` to the plugin's own
`plugin/.mcp.json` (not a project file), and had the `where` tool echo it. Ran once each from
`projB` (no project `.mcp.json` at all):

```json
{
  "found": true,
  "root": "...\\xsp-mcp-experiment\\projB",
  "namespace": "proj_projb",
  "_experiment_project_dir_probe": "C:/Users/trist/AppData/.../xsp-mcp-experiment/projB"
}
```

`${CLAUDE_PROJECT_DIR}` resolved to the actual opened folder, not a fixed value baked in at plugin
install time. Combined with `plugin/resolve.py` reading `.xsp/` from the tool's own `path` argument
(default cwd) at call time, the server's *behavior* is already per-project without any project-side
config file at all: the manifest is fixed, but its env/args are re-resolved against whichever
project is open, and the server itself reads project-local state (`.xsp/`) live.

**Net verdict:** CONFIRMED. Per-project MCP *behavior* is achieved via `${CLAUDE_PROJECT_DIR}`
substitution plus the server reading project-local files at call time (as `where` already does) --
never via a project's own `.mcp.json` overriding the plugin's server, which does not happen and
should not be relied on.

(Both experimental probe fields were removed from the committed `plugin/mcp/server.py` and
`plugin/.mcp.json` after the runs above; they are not part of the shipped skeleton.)

## Out of scope (separate tickets)

The SurrealDB model, installer, marketplace registration, moving existing meter/board/hooks/skills,
installing the plugin globally.
