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

## What this is

I'm a cloud engineer, not a software company. I have a lot of side projects, and I use AI agents (mostly Claude
Code) to help with them. Groundworks is the tooling I built to make that less painful. It's a personal project
that I'm sharing in case it's useful to someone else.

## What I'm trying to fix

- Every new agent session forgets what the last one figured out, so the same lessons get re-learned.
- Agents sometimes say something is done or fixed before anything was actually run.
- Notes end up scattered across files, chats and tickets, and the next session can't find them.

## What it does

Groundworks is a small Rust program plus a Claude Code plugin. It keeps a local database next to my projects and
gives the agent a few tools and habits:

- **A journal per project.** Decisions, gotchas and dead ends get written down. Anything marked as confirmed has
  to point at proof: a command that was run, or a quote from a file.
- **Search over my projects.** Files, docs and commits are indexed so the agent can look things up before
  re-deriving them. When it quotes something, a program checks the quote is really there.
- **Receipts.** Commands and build checks run through Groundworks and leave a record.
- **Hooks.** When a session starts, it gets that project's notes. Risky shell commands get blocked. Before a
  session ends, it's asked to write down what it learned.
- **Housekeeping.** The database is backed up nightly, and a short report shows whether things are healthy.

Everything stays on my machine. Your notes and data live in your own folder, never in this repo.

## How I use it

Most of my Claude Code sessions run with the plugin on. In practice it's simple: the agent starts with what's
already known, looks things up before guessing, and leaves notes for next time. It's early, it changes often, and
some parts are rough.

## What I'm working on next

- A desktop app for it, built on the open-source [Nimbalyst](https://github.com/Nimbalyst/nimbalyst).
- Making the "check your notes first" step automatic instead of something the agent has to remember.
- Simple alerts when backups or the database stop working.
- An easy way to remove a project and all its data.

## Try it

```bash
cargo install --path rust/crates/gw-mcp --locked       # puts `groundworks` on PATH
claude plugin marketplace add <path to this repository>
claude plugin install groundworks@groundworks
groundworks selftest                                   # checks it works, using throwaway data
```

Then open a project in Claude Code and run `/groundworks:init`. More detail is in
[plugin/README.md](plugin/README.md).

**If it sounds helpful, give it a fork** and make it fit how you work. Issues and ideas are welcome.

## Repository map

| Path | What |
|---|---|
| `rust/` | The Rust code: the `groundworks` program and its pieces |
| `plugin/` | The Claude Code plugin: hooks, the agent instructions, two setup commands |
| `docs/` | Design notes, decisions and the roadmap |
| `docs/brand/` | Logo, mascot, icon and colours |
| `groundworks/`, `instances/` | The original Python version, kept as a reference ([its README](docs/ENGINE-PYTHON-V0.2.md)) |

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). SurrealDB, which you install
separately, has its own license (Business Source License 1.1).
