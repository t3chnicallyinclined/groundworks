<p align="center">
  <img src="docs/brand/groundworks-logo.png" alt="Groundworks: a groundhog in a hard hat coming up out of a taped-off hole, drawn as a blueprint" width="720">
</p>

<p align="center"><b>A place for my AI coding agents to keep what they learn, so the next session doesn't start from zero.</b></p>

<p align="center">
  <a href="https://t3chnicallyinclined.github.io/groundworks/">Site</a> ·
  <a href="plugin/README.md">Claude Code plugin</a> ·
  MIT
</p>

---

I'm a cloud engineer with a lot of research and side projects, and I use Claude Code to help with them. Agent
sessions kept forgetting what the last one learned, saying "done" before anything ran, and losing notes across
files and chats. Groundworks is a small Rust program and a Claude Code plugin I made to fix that for myself. It's a
personal project, shared in case it's useful to someone else.

## How it fits together

```
your projects --intake--> local SurrealDB graph: units, facts, receipts, experts
                              ^           fact --evidence--> source lines or a command receipt
                              |
Claude Code + plugin: hooks (notes in, guard, record before stop) and tools (ask, run, gate, journal)
supervisor: keeps the store running, nightly export, restore drill, health report
```

## What works today

- **A journal per project.** Decisions, gotchas and dead ends. "Confirmed" needs proof (a command receipt, a quote
  found word for word in a file, or my say-so), and the database refuses it otherwise. Text that looks like a
  secret is refused.
- **Search with checked quotes.** Code, docs and commits are indexed. When an answer quotes a file, a program checks
  the quote is really there.
- **Receipts.** `run` and `gate` run commands and build checks, and leave a record.
- **Hooks.** Each session starts with the project's notes, including in a git worktree. Risky shell commands and
  edits to Groundworks' own files are blocked. A session that changed things can't stop until it records what it
  learned.
- **A supervisor.** It restarts the database if it stops, exports it nightly and test-restores the export.
- **Health checks.** `groundworks pulse` gives a short report, including work Groundworks didn't see.
  `groundworks doctor` checks the install.

Each project's data sits in its own section of the database, and everything runs locally. There's no cloud service
and no AI model inside Groundworks.

## Roadmap

- **Now:** a Groundworks desktop app, private and in progress. It's a copy of Nimbalyst with Groundworks built into
  every session.
- **Next:**
  - "check your notes first" made automatic;
  - a smaller tool set per session;
  - alerts when something stops working;
  - an undoable way to remove a project and its data;
  - picking up a project's own Claude Code agents, skills and notes when it's added.
- **Later:** sandboxed sessions, and a side-by-side check against plain Claude Code.

## Try it

You need Rust (stable), [SurrealDB](https://surrealdb.com/install) 3.x on your PATH (tested on 3.1.6), Git and
[Claude Code](https://github.com/anthropics/claude-code).

```bash
cargo install --path rust/crates/gw-mcp --locked                 # 1. build `groundworks`
mkdir -p ~/projects/.groundworks                                 # 2. a home folder next to your projects
echo 'workspace = "/home/you/projects"' > ~/projects/.groundworks/groundworks.toml
export GROUNDWORKS_OWNER_PASS=...                                # 3. start the store, create your owner login
cd ~/projects && groundworks store init && unset GROUNDWORKS_OWNER_PASS
groundworks store supervise &                                    #    keep it running, with nightly export and restore drill
claude plugin marketplace add <path to this repository>          # 4. add the plugin
claude plugin install groundworks@groundworks
groundworks selftest && groundworks doctor                       # 5. check it
```

On Windows, `groundworks store install` runs the supervisor as a logon task instead. Then open a project in Claude
Code and run `/groundworks:init`. Type the owner password yourself; Groundworks never
stores it. PowerShell steps and every command are in [plugin/README.md](plugin/README.md).

**If it sounds helpful, give it a fork** and make it fit how you work.

## Thanks

- [Nimbalyst](https://github.com/Nimbalyst/nimbalyst) (MIT): the base of the desktop app.
- [Claude Code](https://github.com/anthropics/claude-code): the plugins and hooks Groundworks plugs into.
- [Model Context Protocol](https://github.com/modelcontextprotocol/modelcontextprotocol) and its
  [Rust SDK](https://github.com/modelcontextprotocol/rust-sdk): how the tools are served.
- [SurrealDB](https://github.com/surrealdb/surrealdb): the database underneath.
- [Council of High Intelligence](https://github.com/0xNyk/council-of-high-intelligence) (MIT): ideas for the expert
  panels.
- [Copier](https://github.com/copier-org/copier) and [uv](https://github.com/astral-sh/uv): the project templates.
- [autoresearch](https://github.com/karpathy/autoresearch) and [Recursive Language Models](https://github.com/alexzhang13/rlm):
  ideas I learned from.
- The Rust crates in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The logo was made with Ideogram and Recraft.

## Repository map

| Path | What |
|---|---|
| `rust/` | The `groundworks` program, its store schema and the eval report |
| `rust/templates/` | Project templates for `/groundworks:bootstrap` (run through Copier) |
| `plugin/` | The Claude Code plugin: hooks, deny list, the Groundworks agent, `/groundworks:init` and `/groundworks:bootstrap` |
| `docs/` | Architecture, decisions, roadmap, and `METRICS.md` (what the health report measures) |
| `docs/brand/`, `site/` | Logo, mascot, icon and colours; the project website |
| `scripts/` | The privacy scan and license check that run before every commit |
| `.claude/` | The repo's own agent and skills (instance design, panels) |
| `groundworks/`, `instances/`, `tests/` | The original Python version, kept as a reference ([its README](docs/ENGINE-PYTHON-V0.2.md)) |
| `research/`, `notes/`, `evals/` | Background reading, dated design notes and eval sets |

## License

MIT. See [LICENSE](LICENSE). SurrealDB, installed separately, has its own license (Business Source License 1.1).
