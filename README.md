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

I'm a cloud engineer, not a software company. I have a lot of research and side projects going, and I use AI
agents (mostly Claude Code) to help with them. I made Groundworks to make my own life easier on that work. It's a
personal project, and I'm sharing it in case it's useful to someone else.

## What I'm trying to fix

- Every new agent session forgets what the last one figured out, so the same lessons get re-learned.
- Agents sometimes say something is done or fixed before anything was actually run.
- Notes end up scattered across files, chats and tickets, and the next session can't find them.

## What it does

Groundworks is a small Rust program plus a Claude Code plugin. It keeps a local database next to my projects and
gives the agent a few tools and habits:

- **A journal per project.** Decisions, gotchas and dead ends get written down. Anything marked as confirmed has
  to point at proof: a command that was run, or a quote from a file. Text that looks like a secret (a key, a
  token) is refused before it's saved.
- **Search over my projects.** Files, docs and commits are indexed so the agent can look things up before
  re-deriving them. When it quotes something, a program checks the quote is really there.
- **Receipts.** Commands and build checks run through Groundworks and leave a record.
- **Hooks.** When a session starts, it gets that project's notes, including in a git worktree of the project.
  Risky shell commands are blocked, and so are edits to Groundworks' own guard files. A session that changed things
  can't stop until it writes down what it learned.
- **Housekeeping.** A supervisor keeps the database running, backs it up nightly and test-restores the backup. A
  short health report (`groundworks pulse`) shows whether things are healthy, including work that happened without
  Groundworks seeing it. `groundworks doctor` checks the install.

Everything stays on my machine. Your notes and data live in your own folder, never in this repo.

## How it works: the whole harness

Groundworks is less one tool and more a harness around each agent session. The pieces fit together like this:

```
  your projects (code, docs, commits)
          |
          |  intake: split into small citable pieces, each with file, lines, hash and date
          v
  +----------------------------- the store (a local SurrealDB graph) -----------------------------+
  |  projects   units (the pieces)   facts (what was learned)   receipts (what was run)           |
  |  experts    decisions, gotchas, dead ends                    sessions and hook events         |
  |                                                                                                |
  |  fact --evidence--> unit            fact --receipt--> the command that proved it               |
  +------------------------------------------------------------------------------------------------+
          ^                          ^                                    ^
          |  ask / search / grep     |  journal (write what was learned)  |  run / gate (leave a receipt)
          |                          |                                    |
  +------------------------------ Claude Code + the plugin ------------------------------+
  |  session start: inject the project's notes       before a command or edit: deny list  |
  |  before stopping: "record what you learned"      agents: architect, engineer, SRE,    |
  |                                                  security, plus any domain experts    |
  +---------------------------------------------------------------------------------------+
          ^
          |  supervisor: keeps the store running, nightly export, restore drill, health report
```

1. **A home folder** next to my projects holds the registry (projects, their environments, and where secrets
   live, by reference only), the expert agents, and the database.
2. **Intake** reads each project into small citable pieces called units.
3. **The store** is a graph. A fact is linked by an `evidence` edge to the exact units behind it, or to the
   receipt of the command that proved it. Each project's data is kept in its own section of the database, so it's
   easy to find, and later to remove.
4. **The agent** works through tools: look things up before re-deriving them, use `run` and `gate` to do and
   check work, and write to the journal to record what it learned.
5. **The hooks** make that happen without counting on the agent to remember: notes in at the start, a guard on
   the shell and on Groundworks' own files, and a stop that waits until something is recorded.
6. **The supervisor** keeps the database running, backs it up nightly, test-restores the backup, and writes a
   short health report.

## A few choices I made

There are plenty of good agent memory tools out there, and I'm not claiming this one is better. I built it for my
own research and projects, and these are the choices that fit how I work:

- **The database holds the line, not the prompt.** Marking a fact as confirmed needs a link to a real piece of
  source, a command receipt, or my say-so. Without one, SurrealDB refuses the write.
- **A program checks quotes.** When an answer cites a file, the quote has to be found word for word in what the
  agent actually read.
- **Dead ends are kept.** Ruled-out approaches are written down with the reason, so they don't get re-tried.
- **Facts link to their proof.** Because it's a graph, I can walk from a fact to the lines or command behind it.
- **Each agent signs in as itself**, so I can see who wrote what.
- **Organised by project.** One home covers all my projects; related ones can see each other, private ones can be
  walled off.
- **Kept simple.** One local program and one local database. No cloud service, and no AI model inside
  Groundworks itself.

## How I use it

Most of my Claude Code sessions run with the plugin on. In practice it's simple: the agent starts with what's
already known, looks things up before guessing, and leaves notes for next time. It's early, it changes often, and
some parts are rough.

## What I'm working on next

- A desktop app I'm building: a private, work-in-progress copy of Nimbalyst with Groundworks built in, where every
  session runs with the plugin.
- Making "check your notes first" automatic: a short "already known" note on each prompt, and a heads-up before a
  web search or handing work to a helper agent.
- A smaller set of tools per session.
- Alerts when the database, the backups or the health report stop working.
- An easy, undoable way to remove a project and its data.
- Trying sandboxed sessions, so the agent's commands run in an isolated environment while Groundworks stays outside.
- A side-by-side check against plain Claude Code on a fresh set of tasks.

## Try it

You need [Rust](https://rustup.rs) (stable, via rustup), [SurrealDB](https://surrealdb.com/install) 3.x on your
PATH (built and tested on 3.1.6), Git, and [Claude Code](https://github.com/anthropics/claude-code).

```bash
# 1. Build the program
cargo install --path rust/crates/gw-mcp --locked       # puts `groundworks` on PATH

# 2. Make a home folder next to your projects
mkdir -p ~/projects/.groundworks
echo 'workspace = "/home/you/projects"' > ~/projects/.groundworks/groundworks.toml

# 3. Start the store and create your owner login, once.
#    Type the password yourself in your own terminal; Groundworks never stores or reads it from a file.
export GROUNDWORKS_OWNER_PASS=...
cd ~/projects && groundworks store init
unset GROUNDWORKS_OWNER_PASS

# 4. Add the plugin to Claude Code
claude plugin marketplace add <path to this repository>
claude plugin install groundworks@groundworks

# 5. Check it
groundworks selftest                                   # every rule, on throwaway data
groundworks doctor                                     # your real setup, including the plugin
```

Then open one of your projects in Claude Code and run `/groundworks:init`. More detail, including PowerShell steps,
is in [plugin/README.md](plugin/README.md).

**If it sounds helpful, give it a fork** and make it fit how you work. Issues and ideas are welcome.

## Thanks

Groundworks stands on a lot of other people's work. Thank you to:

- **[Nimbalyst](https://github.com/Nimbalyst/nimbalyst)** (MIT). The Groundworks desktop app I'm building is a
  copy of Nimbalyst with Groundworks added on top.
- **[Claude Code](https://github.com/anthropics/claude-code)** by Anthropic. Its plugins, hooks and agents are what
  Groundworks plugs into.
- **[Model Context Protocol](https://github.com/modelcontextprotocol/modelcontextprotocol)** and its
  [Rust SDK](https://github.com/modelcontextprotocol/rust-sdk), which the Groundworks tools are served through.
- **[SurrealDB](https://github.com/surrealdb/surrealdb)**, the graph database underneath everything.
- **[Council of High Intelligence](https://github.com/0xNyk/council-of-high-intelligence)** by nyk (MIT). The
  expert panel protocol adapts its ideas.
- **[Copier](https://github.com/copier-org/copier)** and **[uv](https://github.com/astral-sh/uv)**, which run the
  project templates.
- **[autoresearch](https://github.com/karpathy/autoresearch)** by Andrej Karpathy and
  **[Recursive Language Models](https://github.com/alexzhang13/rlm)** by Alex Zhang and co-authors, for ideas I
  learned from.
- The Rust crates listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), which also records every license.

The logo and mascot art were made with Ideogram and Recraft.

## Repository map

| Path | What |
|---|---|
| `rust/` | The Rust code: the `groundworks` program, its store schema and the eval report |
| `rust/templates/` | Project templates used by `/groundworks:bootstrap` (run through Copier) |
| `plugin/` | The Claude Code plugin: hooks, the deny list, the Groundworks agent, the init and bootstrap commands |
| `docs/` | Architecture, decisions, the roadmap, and `METRICS.md` (what the health report measures) |
| `docs/brand/` | Logo, mascot, icon and colours |
| `site/` | The project website |
| `scripts/` | The privacy scan and license check that run before every commit |
| `groundworks/`, `instances/`, `tests/` | The original Python version, kept as the reference the Rust port is checked against ([its README](docs/ENGINE-PYTHON-V0.2.md)) |
| `.claude/` | An agent and two skills for working on the Python version (`scholar-architect`, `design-instance`, `panel`) |
| `research/`, `notes/`, `evals/` | Background reading, dated design notes and eval sets |

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). SurrealDB, which you install
separately, has its own license (Business Source License 1.1).
