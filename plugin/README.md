# Groundworks plugin for Claude Code

One MCP server (`groundworks serve`) and two skills that run only when you invoke them.

| Skill | What it does |
|---|---|
| `/groundworks:init` | Register the current project: three questions, the registry entry, the answers recorded, the gate proved |
| `/groundworks:bootstrap` | Start a new project from a stack's template and prove it builds |

## Install

```bash
cargo install --path rust/crates/gw-mcp --locked     # puts `groundworks` on PATH
claude plugin marketplace add <path to this repository>
claude plugin install groundworks@groundworks
```

The server finds its home by walking up from the opened folder to the nearest
`.groundworks/groundworks.toml` (the plugin passes `${CLAUDE_PROJECT_DIR}` as `GROUNDWORKS_START`),
or uses `GROUNDWORKS_HOME` if set. The home holds your registry, journal and knowledge; it is yours
and never belongs in this repository.

## Using it

In any Claude Code session opened under a Groundworks home, the agent has the tools; you mostly talk to it.

| You want | Do |
|---|---|
| Check it works on this machine | `groundworks selftest` (a throwaway home and store; never touches yours) |
| Check your real setup | `groundworks doctor` |
| Bring a project in | `/groundworks:init` in the project's folder |
| Start a new project | `/groundworks:bootstrap` |
| See a project and what was learned | ask the agent, or `groundworks show <project>` |
| Prove a project still works | ask the agent to run the gate, or `groundworks gate <project>` |
| Record something yourself, signed as you | `groundworks note <project> decision "..." --as-owner --owner-statement "..."` |
| Isolate or share a project, wall two apart | `groundworks owner level ...`, `groundworks owner wall ...` |

What the agent does with the tools: reads `project_show` before working, runs commands through `run`
(each leaves a receipt), proves changes with `gate`, and records decisions, gotchas and ruled-out
approaches with `journal_add`. It cannot record anything as CONFIRMED without a receipt, a quote found
word for word in a file, or an owner statement. Work items belong in GitHub issues, not the journal.
Outside any home, the server offers only a `status` tool saying why.

## The store

Facts, receipts and scope policy live in one SurrealDB server shared by every session, on
`127.0.0.1:8004` (loopback only; `[store]` in `groundworks.toml` overrides it). Set it up once:

```bash
export GROUNDWORKS_OWNER_PASS=...      # any length; empty works with a warning; never stored by Groundworks
groundworks store init                 # credentials, start the server, owner login, import old journals
groundworks store status
groundworks owner level proj_<slug> isolated          # shared | private | isolated
groundworks owner wall proj_<a> proj_<b> --why "..."
```

The server starts itself when it is not answering, and again if it stops. Agents work under an
`agent` login (a different password in each project's namespace) and never root. Owner commands need
the owner password at the moment they run; Groundworks never reads or keeps it. With an empty owner
password every owner command warns that any agent could act as the owner. In PowerShell:

```powershell
$sec = Read-Host "Owner password" -AsSecureString
$env:GROUNDWORKS_OWNER_PASS = [System.Net.NetworkCredential]::new('', $sec).Password
groundworks owner level proj_<slug> private
Remove-Item Env:GROUNDWORKS_OWNER_PASS
``` A fact may be CONFIRMED only
with a receipt from `run` or `gate`, a quote found word for word in a project file, or an owner
statement: the store refuses it otherwise, for every writer.

## Upgrading on Windows while sessions are open

A running `groundworks.exe` cannot be overwritten but can be renamed. Move it aside
(`groundworks.exe.old-<date>`), then `cargo install` again; open sessions keep the old binary until
they restart.

The server calls no model and holds no API key. Secrets are references (`env:`, `file:`,
`bw:ITEM#FIELD`), resolved only to inject into a command and redacted from its output.
