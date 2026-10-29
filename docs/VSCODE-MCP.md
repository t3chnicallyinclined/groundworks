# VSCODE-MCP.md: the Groundworks server in VS Code (Copilot agent mode)

VS Code and Copilot sessions have none of the Claude Code plugin's hooks (no journal injected at start, no
deny list, no Stop gate). What they can have is the same tools: the `groundworks serve` MCP server over
stdio, so a session there can read a project's journal, record what it learned and run commands with
receipts. The store's own rules apply unchanged (no CONFIRMED without evidence, no secret-shaped text), and
`pulse` counts their writes; `commits_without_sessions` in the pulse is the deadman for the rest
(`docs/METRICS.md`).

## The entry

User-level file: `%APPDATA%\Code\User\mcp.json` on Windows (`~/.config/Code/User/mcp.json` on Linux,
`~/Library/Application Support/Code/User/mcp.json` on macOS). Add the server under `servers`:

```json
{
  "servers": {
    "groundworks": {
      "type": "stdio",
      "command": "groundworks",
      "args": ["serve"],
      "env": {
        "GROUNDWORKS_START": "${workspaceFolder}"
      }
    }
  }
}
```

`groundworks` must be on `PATH` (`cargo install --path rust/crates/gw-mcp --locked` puts it in
`~/.cargo/bin`). `GROUNDWORKS_START` tells the server where to look for the home: it walks up from the
opened folder to the nearest `.groundworks/groundworks.toml`, exactly as the Claude Code plugin does with
`${CLAUDE_PROJECT_DIR}`. Outside any home the server starts with a single `status` tool that says so.

## Check it

- `groundworks doctor` warns when `mcp.json` exists and has no `groundworks` server (it never fails on it:
  VS Code is optional; the hooks are not).
- In VS Code: Command Palette, "MCP: List Servers", `groundworks` should be running; ask the agent to call
  `project_list`.
- A session that records anything shows up in `groundworks pulse` under `journal_writes` and
  `writes_by_agent`; a session that only commits shows up as `commits_without_sessions`.

## What it does not give you

No hooks: nothing injects the journal at the start of a VS Code session, nothing refuses `git push` or
`kubectl apply`, nothing asks for a journal entry at the end. The agent instructions that the Claude Code
plugin carries (`plugin/agents/groundworks.md`) can be pasted into the workspace's Copilot instructions
file, but that is guidance, not a gate.
