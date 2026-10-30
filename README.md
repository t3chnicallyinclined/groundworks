<p align="center">
  <img src="docs/brand/groundworks-logo.png" alt="Groundworks: a groundhog in a hard hat coming up out of a taped-off hole, drawn as a blueprint" width="720">
</p>

<p align="center"><b>The memory, rules and tools AI coding agents use to work on every project, and to prove what they claim.</b></p>

<p align="center">
  <a href="https://t3chnicallyinclined.github.io/groundworks/">Site</a> ·
  <a href="plugin/README.md">Claude Code plugin</a> ·
  <a href="rust/docs/EVAL-2026-10.md">Eval report</a> ·
  <a href="docs/ROADMAP.md">Roadmap</a> ·
  MIT
</p>

---

## Why we built it

One person runs about twenty software and research projects with AI agents doing most of the typing. The agents
are good. What kept going wrong was not their skill. It was everything around a single session:

- **They forget.** Each session starts cold. A lesson learned on Monday is re-derived on Thursday, sometimes
  wrongly, and the mistake that cost a day costs it again.
- **They claim without proof.** "Done" and "fixed" arrive before anything was run. A confident wrong answer looks
  exactly like a right one.
- **Knowledge leaks into the wrong place.** Notes, tickets and chat logs pile up in different tools, and the next
  agent cannot find what the last one knew.
- **Nobody checks that the checks ran.** A green result over nothing is the most expensive bug there is.

Groundworks started as an engine for reading one very large record exhaustively, where a database refused any
conclusion that did not cite evidence ([the Python engine](docs/ENGINE-PYTHON-V0.2.md)). The same idea turned out
to be what every project needed: **a place where what agents learn is kept, cited and checked, and where the
rules are enforced by the machine instead of remembered by the model.**

The mascot is the foreman: he digs, keeps the ledger of what was found, tapes off dead ends so nobody falls in
twice, and never calls a job done until he has seen the proof.

## What it is now

A Rust workspace (`rust/`) that ships as one binary, `groundworks`, and a Claude Code plugin around it. Your data
lives in a private home folder (`.groundworks/`) next to your projects, never in this repository.

| Piece | What it does |
|---|---|
| **Registry** | Every project, its stack, its environments and where its secrets live (references only, never values). |
| **Store** | A local SurrealDB, started and supervised for you: restarted if it dies, exported nightly, and restore-drilled. Each agent signs in as its own user, never as root. |
| **Intake** | Reads each project's files, docs and commits into citable units with file, lines, hash, date and the expert area they belong to. |
| **Journal** | Decisions, gotchas, status and ruled-out approaches, per project. The store refuses CONFIRMED without evidence: a command receipt, a quote found word for word in a file, or an owner statement. It refuses text that looks like a secret. |
| **Ask and answer** | `ask` builds a packet of the best candidates for a question, recorded facts first. `answer` checks every quote in the reply against what the agent actually read. A passed answer is recorded, so the next session gets it first. |
| **Search and grep** | Ranked search across every shared project, and exact or regex match over the ingested files, scoped and dated. |
| **Run and gate** | Commands run through a project's registered environment and leave a receipt. `gate` runs the project's own build and test proof. |
| **Team** | Role agents (we run an architect, an engineer, an SRE and a security engineer) plus any domain experts you keep. They live in your home and `install-team` stamps one shared Groundworks block into each. An expert directory routes questions to the right one; lenses and panels let several experts judge one question. |
| **Hooks** | They run on their own. Session start injects the project's journal, gotchas first. Before a shell command, a deny list blocks the dangerous ones and fails closed. At stop, a session that changed things and recorded nothing is asked once to record what it learned. |
| **Pulse** | A few numbers from the store, no model: is knowledge growing, is it being used, did the backups and drills pass, did work happen without a session. `--digest` prints only what is red. |
| **Proof it works** | `groundworks selftest` builds a throwaway home and store and checks every rule both ways, red and green (105 checks). `groundworks doctor` checks your real setup. |
| **Templates** | `bootstrap` starts a new project from a stack template (Rust CLI, Rust service) and proves it builds. |

The MCP server exposes 27 tools. Hook overhead is about 100 ms at the 95th percentile.

## How we use it

Every Claude Code session, in VS Code and in the terminal, runs with the plugin on. The loop is the same in every
project:

1. **Session start.** The hook names the project from the folder and injects what is already known about it.
2. **Ask before deriving.** The agent asks Groundworks before it researches or rebuilds a conclusion. Answers come
   back with quotes a program has checked.
3. **Do the work through receipts.** Commands go through `run`, changes are proved with `gate`, and both leave a
   receipt in the store.
4. **Record before stopping.** Decisions, gotchas and dead ends go into the journal, tagged CONFIRMED, INFERRED or
   UNKNOWN. If nothing was recorded after real work, the stop hook asks.
5. **Unattended.** The supervisor keeps the store up, exports it every night, drills a restore, and takes a pulse.

As of 2026-10-30, on the owner's machine: 18 projects registered, about 49,000 units ingested, 155 live journal
facts (half CONFIRMED), the nightly export and restore drill passing. The hooks went live on 2026-10-29, so the
usage numbers are days old, and the pulse's first digest is honest about it: most commits in the last week
happened in sessions the hooks did not see.

## What the evals showed

Twelve pre-registered runs over two question sets, graded blind against owner-approved answers
([full report](rust/docs/EVAL-2026-10.md)).

| Question set | Plain grep | Groundworks agent |
|---|---|---|
| 47 questions whose answers are written plainly in the repos | 35 to 39 correct | 36 to 39 correct |
| 20 lessons that exist only in the owner's notes | 8 correct, 8 confidently wrong | 18 correct, 1 confidently wrong, about half the tokens |

On text grep can already find, Groundworks ties it. On lessons that live only in notes, it separates, with
intervals that do not overlap. From the sixth run of the second set on, no answer was graded wrong and none was
confidently wrong. Whether knowledge accumulates in real daily use is not measured yet. That is the next test.

## What is planned

- **A Groundworks desktop window.** A private build on [Nimbalyst](https://github.com/Nimbalyst/nimbalyst) (MIT):
  sessions on the left, chat in the middle, changes and a Groundworks health tab on the right. Every session in it
  runs with the plugin and hooks pinned on. A hook probe in the stock build comes first, then security fixes, then a
  trim to one knowledge store.
- **Consult-first, forced.** A hint on every prompt with what is already known, a store lookup before web research
  or delegation, and a counter for lessons re-derived.
- **A machine that checks itself.** New red conditions (store down, stale export, failed drill, low disk), sent to
  a desktop notification and a webhook, with a heartbeat on a second machine so a dead laptop is noticed too.
- **Growth without fear.** The store is allowed to grow. Everything carries its project, and
  `groundworks project prune` removes one project and all its data, dry run first, exported and drilled before it
  deletes.
- **A fresh eval from real questions.** The next ranking or packet change waits for a new question set drawn from
  real asks, and the window is compared against VS Code on it.

The engine roadmap (gates, promotion, panels, the evidence-gated graph) is in [docs/ROADMAP.md](docs/ROADMAP.md).

## Install

```bash
cargo install --path rust/crates/gw-mcp --locked       # puts `groundworks` on PATH
claude plugin marketplace add <path to this repository>
claude plugin install groundworks@groundworks
groundworks selftest                                   # proves it on this machine, never touches your data
```

Then open a project in Claude Code and run `/groundworks:init`. Details, every command and the store setup are in
[plugin/README.md](plugin/README.md).

## Repository map

| Path | What |
|---|---|
| `rust/` | The workspace: `gw-core`, `gw-store`, `gw-ops`, `gw-mcp` (the `groundworks` binary), templates, the eval report |
| `plugin/` | The Claude Code plugin: hooks, deny list, the Groundworks agent, the init and bootstrap skills |
| `docs/` | Architecture, decisions, metrics, roadmap, and the [Python engine's README](docs/ENGINE-PYTHON-V0.2.md) |
| `docs/brand/` | Logo, mascot, app icon and colour tokens |
| `groundworks/`, `instances/` | The Python v0.2.0 engine, frozen as the reference the Rust port is checked against |

## Rules this repository keeps

- No private data, ever. A pre-commit scan checks every commit against a deny list kept outside the repo.
- A check that cannot fail is not a check. Every gate is shown red before it is trusted green.
- Report the unflattering number next to the flattering one. Say UNKNOWN rather than guess.

## License

MIT. Copyright (c) 2026 t3chnicallyinclined. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). SurrealDB, which you install separately, is under the Business
Source License 1.1.
