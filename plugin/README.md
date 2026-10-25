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

## The store

Facts, receipts and scope policy live in one SurrealDB server shared by every session, on
`127.0.0.1:8004` (loopback only; `[store]` in `groundworks.toml` overrides it). Set it up once:

```bash
export GROUNDWORKS_OWNER_PASS=...      # from your password manager; never stored by Groundworks
groundworks store init                 # credentials, start the server, owner login, import old journals
groundworks store status
groundworks owner level proj_<slug> isolated          # shared | private | isolated
groundworks owner wall proj_<a> proj_<b> --why "..."
```

The server starts itself when it is not answering. Agents work under an `agent` login and never
root; owner commands need the owner password at the moment they run. A fact may be CONFIRMED only
with a receipt from `run` or `gate`, a quote found word for word in a project file, or an owner
statement: the store refuses it otherwise, for every writer.

## Upgrading on Windows while sessions are open

A running `groundworks.exe` cannot be overwritten but can be renamed. Move it aside
(`groundworks.exe.old-<date>`), then `cargo install` again; open sessions keep the old binary until
they restart.

The server calls no model and holds no API key. Secrets are references (`env:`, `file:`,
`bw:ITEM#FIELD`), resolved only to inject into a command and redacted from its output.
