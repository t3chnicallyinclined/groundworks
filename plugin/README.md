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

The server calls no model and holds no API key. Secrets are references (`env:`, `file:`,
`bw:ITEM#FIELD`), resolved only to inject into a command and redacted from its output.
