//! The MCP surface: one tool per operation in `ops`, over stdio.

use std::path::PathBuf;
use std::sync::Arc;

use rmcp::handler::server::{router::tool::ToolRouter, wrapper::Parameters};
use rmcp::model::{Implementation, ServerCapabilities, ServerConfig};
use rmcp::{ServerHandler, tool, tool_handler, tool_router};
use schemars::JsonSchema;
use serde::Deserialize;

use gw_ops::home::Home;
use gw_ops::journal::{Confidence, Kind};
use gw_ops::ops;
use gw_ops::store::{self, Quote, Store};

const INSTRUCTIONS: &str = "\
Groundworks: the registry, journal, knowledge and tools for every project the owner runs.
The main session leads: start from project_show and the journal, keep both current, report to the owner,
and bring in an expert only when it helps. Rules for everyone using these tools:
1. Tools first. Before answering anything about a project, read it with project_show and check \
the repo with run. Never answer project facts from memory.
2. Evidence on every claim: a file:line, a command and its output, or a journal entry; tag it \
CONFIRMED, INFERRED or UNKNOWN.
3. Secrets are references, never values. Use run to use them. Never print, echo, write down or ask for one.
4. Write it down. A decision, gotcha, status change or ruled-out approach goes into journal_add the moment you
learn it; work items go in GitHub issues. CONFIRMED needs a receipt (from run or gate), a quote found word for
word, or an owner statement: the store refuses it otherwise.
5. Gates must be able to fail. Prove changes with gate; 'it compiles' is not green.
6. Protected envs (prod) need the owner's explicit go for each action; confirm repeats it, it does not replace it.
Read knowledge_get('principles') once per session.";

fn fail(e: anyhow::Error) -> String {
    format!("{e:#}")
}

/// A command result whose non-zero exit is reported as a tool error, so it cannot be missed.
fn passed((text, ok): (String, bool)) -> Result<String, String> {
    if ok { Ok(text) } else { Err(text) }
}

#[derive(Deserialize, JsonSchema)]
pub struct ShowArgs {
    pub project: String,
    /// How many recent journal entries to include (default 20).
    pub journal_limit: Option<usize>,
    /// Only journal entries after this RFC 3339 time (e.g. the session's start).
    pub since: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct RegisterArgs {
    /// Letters, digits, '.', '_', '-'.
    pub name: String,
    /// Directory relative to the workspace (default: the name).
    pub dir: Option<String>,
    /// One line: what the project is.
    pub summary: String,
    /// A stack from stack_list, which gives the project its gate.
    pub stack: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct NoteArgs {
    pub project: String,
    pub kind: Kind,
    /// The fact, decision, gotcha, status or ruled-out approach, in one or two sentences.
    pub text: String,
    /// Where it comes from: file:line, the command and what it printed, or a URL.
    pub source: String,
    pub confidence: Confidence,
    /// Which team member is writing (e.g. gw-sre).
    pub by: String,
    /// A receipt id from run or gate (`receipt:...`) that proves this.
    pub receipt: Option<String>,
    /// Text that must appear word for word in a project file; checked before recording.
    pub quote: Option<Quote>,
    /// What the owner said, with the date, when the owner is the evidence.
    pub owner_statement: Option<String>,
    /// The lens (expert) this finding was made through, e.g. gw-security.
    pub lens: Option<String>,
    /// A fact this one disputes or supports (`fact:...`); needs stance.
    pub attacks: Option<String>,
    /// `disputes` or `supports`, with attacks.
    pub stance: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct LensArgs {
    /// The lens (any expert's name). Without it, every lens is listed.
    pub lens: Option<String>,
    /// The project to read through the lens.
    pub project: Option<String>,
    /// Newest N findings and attacks (default 100).
    pub limit: Option<usize>,
}

#[derive(Deserialize, JsonSchema)]
pub struct PanelOpenArgs {
    pub project: String,
    /// The panel: {panel_id, target, question, options: [..], objection_categories: [..],
    /// seats: [{id, lens, aliases}], weighted_seat?}. At least 3 seats, 2 options. Thresholds and
    /// confidence weights are the owner's (registry/panel.toml); leave them out.
    pub panel: serde_json::Value,
    pub by: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct PanelSubmitArgs {
    pub project: String,
    /// The panel id (panel:...).
    pub panel: String,
    /// 1, 2 or counterfactual.
    pub round: String,
    pub seat: String,
    /// The seat's full answer, verbatim, ending with its STANCE line.
    pub text: String,
    pub by: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct PanelArgs {
    pub project: String,
    /// The panel id (panel:...).
    pub panel: String,
}

#[derive(Deserialize, JsonSchema)]
pub struct JournalArgs {
    pub project: String,
    pub kind: Option<Kind>,
    /// Newest N entries (default 50).
    pub limit: Option<usize>,
    /// Only entries after this RFC 3339 time (e.g. the session's start).
    pub since: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct StackArg {
    pub stack: String,
}

#[derive(Deserialize, JsonSchema)]
pub struct EnvArgs {
    pub project: String,
    /// Environment name; `local` always exists.
    pub env: String,
}

#[derive(Deserialize, JsonSchema)]
pub struct RunArgs {
    pub project: String,
    /// Environment name; `local` always exists.
    pub env: String,
    /// Shell command, run in the env's directory with its vars and secrets.
    pub command: String,
    /// Seconds before the command is killed (default 300, max 3600).
    pub timeout_secs: Option<u64>,
    /// For protected envs only: "<project>/<env>", after the owner has said go.
    pub confirm: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct GateArgs {
    pub project: String,
    /// Environment to run in (default local).
    pub env: Option<String>,
    /// Per-step timeout in seconds (default 900).
    pub timeout_secs: Option<u64>,
}

#[derive(Deserialize, JsonSchema)]
pub struct BootstrapArgs {
    /// A stack with a template (see stack_list).
    pub stack: String,
    pub name: String,
    /// Target directory (default: <workspace>/<name>). Must be empty or absent.
    pub dir: Option<String>,
    /// Register the new project (default true).
    pub register: Option<bool>,
}

#[derive(Deserialize, JsonSchema)]
pub struct ScanArgs {
    /// Folder to scan (default: the workspace).
    pub root: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct IntakeArgs {
    pub project: String,
    /// How many recent commits to read as units (default 300).
    pub max_commits: Option<usize>,
}

#[derive(Deserialize, JsonSchema)]
pub struct SearchArgs {
    /// Words to find (full text, stemmed: "rollbacks" finds "rollback").
    pub query: String,
    /// One project; without it, every shared project (private and isolated ones are skipped and named).
    pub project: Option<String>,
    /// Only units in this expert's area (see expert_list).
    pub expert: Option<String>,
    /// Only these kinds: context, doc, code, config, commit, quote.
    pub kinds: Option<Vec<String>>,
    /// Most results (default 10, at most 50).
    pub limit: Option<usize>,
}

#[derive(Deserialize, JsonSchema)]
pub struct GrepArgs {
    /// Exact text to find, case-insensitive (an identifier, a number, an error string, a phrase); or a
    /// regex written /like this/.
    pub pattern: String,
    /// One project (and its related projects); without it, every shared project.
    pub project: Option<String>,
    /// Most hits (default 20, at most 100).
    pub limit: Option<usize>,
}

#[derive(Deserialize, JsonSchema)]
pub struct AskArgs {
    /// The question, in plain words. With a lens it may be left out: the lens's checks are asked.
    pub question: Option<String>,
    /// Ask through a lens (any expert's name): the packet is tagged with it and the lens's findings follow.
    pub lens: Option<String>,
    /// One project; without it, every shared project.
    pub project: Option<String>,
    /// Only units in this expert's area.
    pub expert: Option<String>,
    /// Only these kinds: context, doc, code, config, commit, quote.
    pub kinds: Option<Vec<String>>,
    /// How many candidates to retrieve and read (default 20, at most 200).
    pub k: Option<usize>,
    /// Who is asking (e.g. gw-architect, or the main session).
    pub by: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
pub struct AnswerArgs {
    /// The packet id `ask` returned (ask_packet:...).
    pub packet: String,
    /// The candidate numbers you read in full: all of them.
    pub read: Vec<usize>,
    /// The answer, its quotes (each `{n, lines: [a, b], use}`; `quote` text only when lines cannot be
    /// given), gaps, and whether the candidates could answer it at all.
    #[serde(flatten)]
    pub answer: gw_ops::ask::AnswerIn,
}

#[derive(Deserialize, JsonSchema)]
pub struct TopicArg {
    pub topic: String,
}

#[derive(Deserialize, JsonSchema)]
pub struct QueryArg {
    pub query: String,
}

#[derive(Clone)]
pub struct Groundworks {
    home: Arc<Home>,
    /// Opened on first use and kept; a failed open is retried on the next call.
    store: Arc<tokio::sync::Mutex<Option<Arc<Store>>>>,
    tool_router: ToolRouter<Self>,
}

impl Groundworks {
    async fn store(&self) -> Result<Arc<Store>, String> {
        let mut slot = self.store.lock().await;
        if let Some(store) = slot.as_ref() {
            return Ok(Arc::clone(store));
        }
        let home = Arc::clone(&self.home);
        let store = Arc::new(store::blocking(move || Store::open(&home)).await.map_err(fail)?);
        *slot = Some(Arc::clone(&store));
        Ok(store)
    }
}

#[tool_router]
impl Groundworks {
    pub fn new(home: Home) -> Self {
        Self {
            home: Arc::new(home),
            store: Arc::default(),
            tool_router: Self::tool_router(),
        }
    }

    #[tool(description = "List every registered project with its stack, envs and one-line summary.")]
    async fn project_list(&self) -> Result<String, String> {
        ops::projects(&self.home).map_err(fail)
    }

    #[tool(
        description = "A project's registry entry, path on this machine, gate, key docs and recent journal. Read this before working on a project."
    )]
    async fn project_show(&self, args: Parameters<ShowArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await.ok());
        store::blocking(move || {
            ops::show(
                &home,
                store.as_deref(),
                &a.project,
                a.journal_limit.unwrap_or(20),
                a.since.as_deref(),
            )
        })
        .await
        .map_err(fail)
    }

    #[tool(description = "Register a project that already exists in the workspace. Refuses a name that is taken.")]
    async fn project_register(&self, args: Parameters<RegisterArgs>) -> Result<String, String> {
        let a = args.0;
        ops::register(&self.home, &a.name, a.dir.as_deref(), &a.summary, a.stack.as_deref()).map_err(fail)
    }

    #[tool(
        description = "Record a decision, gotcha, status, fact or ruled-out approach in a project's journal. CONFIRMED needs a receipt, a quote found word for word, or an owner statement; the store refuses it otherwise."
    )]
    async fn journal_add(&self, args: Parameters<NoteArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            ops::note(
                &home,
                &store,
                ops::Note {
                    surface: "journal_add",
                    project: &a.project,
                    kind: a.kind,
                    text: &a.text,
                    source: &a.source,
                    confidence: a.confidence,
                    by: &a.by,
                    receipt: a.receipt.as_deref(),
                    owner_statement: a.owner_statement.as_deref(),
                    quote: a.quote,
                    lens: a.lens.as_deref(),
                    attacks: a.attacks.as_deref(),
                    stance: a.stance.as_deref(),
                    owner_pass: None,
                },
            )
        })
        .await
        .map_err(fail)
    }

    #[tool(description = "Read a project's journal, optionally one kind only, optionally only entries after a time.")]
    async fn journal_read(&self, args: Parameters<JournalArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            ops::journal(
                &home,
                &store,
                &a.project,
                a.kind,
                a.since.as_deref(),
                a.limit.unwrap_or(50),
            )
        })
        .await
        .map_err(fail)
    }

    #[tool(description = "List the team's standard stacks (crates, packages, gate, template).")]
    async fn stack_list(&self) -> Result<String, String> {
        ops::stacks(&self.home).map_err(fail)
    }

    #[tool(description = "One stack in full: its dependencies, gate commands and notes.")]
    async fn stack_show(&self, args: Parameters<StackArg>) -> Result<String, String> {
        ops::stack(&self.home, &args.0.stack).map_err(fail)
    }

    #[tool(
        description = "An env's variables and secret references, with whether each secret resolves. Never shows a secret value."
    )]
    async fn env_show(&self, args: Parameters<EnvArgs>) -> Result<String, String> {
        let a = args.0;
        ops::env_show(&self.home, &a.project, &a.env).await.map_err(fail)
    }

    #[tool(
        description = "Run a shell command in a project's env, locally (vars and secrets injected) or over ssh (vars only). Output is redacted, names the machine, and leaves a receipt in the store. A non-zero exit is returned as an error."
    )]
    async fn run(&self, args: Parameters<RunArgs>) -> Result<String, String> {
        let a = args.0;
        let timeout = a.timeout_secs.unwrap_or(300);
        ops::run(
            &self.home,
            self.store().await.ok(),
            &a.project,
            &a.env,
            &a.command,
            timeout,
            a.confirm.as_deref(),
        )
        .await
        .map_err(fail)
        .and_then(passed)
    }

    #[tool(
        description = "Run the project's gate (format, lint, test...) in order, stopping at the first failure, and leave one receipt in the store. RED is returned as an error."
    )]
    async fn gate(&self, args: Parameters<GateArgs>) -> Result<String, String> {
        let a = args.0;
        let env = a.env.as_deref().unwrap_or("local");
        ops::gate(
            &self.home,
            self.store().await.ok(),
            &a.project,
            env,
            a.timeout_secs.unwrap_or(900),
        )
        .await
        .map_err(fail)
        .and_then(passed)
    }

    #[tool(
        description = "Create a new project from a stack's template and register it. Refuses a non-empty target directory."
    )]
    async fn bootstrap(&self, args: Parameters<BootstrapArgs>) -> Result<String, String> {
        let a = args.0;
        let dir = a.dir.map(PathBuf::from);
        ops::bootstrap(
            &self.home,
            &a.stack,
            &a.name,
            dir.as_deref(),
            a.register.unwrap_or(true),
        )
        .map_err(fail)
    }

    #[tool(description = "List the team's knowledge pages (principles, stack choices, estate, gotchas).")]
    async fn knowledge_list(&self) -> Result<String, String> {
        ops::knowledge_list(&self.home).map_err(fail)
    }

    #[tool(description = "Read one knowledge page by topic.")]
    async fn knowledge_get(&self, args: Parameters<TopicArg>) -> Result<String, String> {
        ops::knowledge_get(&self.home, &args.0.topic).map_err(fail)
    }

    #[tool(description = "Search every knowledge page; returns topic:line hits.")]
    async fn knowledge_search(&self, args: Parameters<QueryArg>) -> Result<String, String> {
        ops::knowledge_search(&self.home, &args.0.query).map_err(fail)
    }

    #[tool(description = "The owner's authoritative work history. Never state experience beyond it.")]
    async fn profile(&self) -> Result<String, String> {
        ops::profile(&self.home).map_err(fail)
    }

    #[tool(
        description = "Read-only estate scan: one record per project under a folder (git facts, build files, candidate gates never run, docs, secret-looking file names; local-only projects are not walked). Writes estate.json in the home and returns counts."
    )]
    async fn estate_scan(&self, args: Parameters<ScanArgs>) -> Result<String, String> {
        let home = self.home.clone();
        let root = args.0.root.map(PathBuf::from);
        store::blocking(move || ops::estate_scan(&home, root.as_deref(), None))
            .await
            .map_err(fail)
    }

    #[tool(
        description = "Ingest a project into the store: its tracked files and recent commits become citable units, by kind (context first) and by the experts whose definitions cite its paths. Safe to re-run: unchanged units are skipped and changed ones mark the facts citing them stale."
    )]
    async fn intake(&self, args: Parameters<IntakeArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || ops::intake(&home, &store, &a.project, a.max_commits.unwrap_or(300)))
            .await
            .map_err(fail)
    }

    #[tool(
        description = "Search ingested units in full text: returns each hit's project, file and lines (or commit), kind, owning experts and an excerpt with the matched words in « ». Cite hits by locator; quote them word for word to confirm a fact."
    )]
    async fn search(&self, args: Parameters<SearchArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            let kinds = a.kinds.unwrap_or_default();
            ops::search(
                &home,
                &store,
                gw_ops::ask::Query {
                    text: &a.query,
                    project: a.project.as_deref(),
                    expert: a.expert.as_deref(),
                    kinds: &kinds,
                    limit: a.limit.unwrap_or(10),
                },
            )
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "Grep over the ingested files: exact, case-insensitive match (or /regex/) across a project and its related projects, or every shared project. Each hit gives project, file:line and the matching line, dated. Use it for identifiers, numbers, error strings and exact phrases; use ask for a question in words."
    )]
    async fn grep(&self, args: Parameters<GrepArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || ops::grep(&home, &store, a.project.as_deref(), &a.pattern, a.limit.unwrap_or(20)))
            .await
            .map_err(fail)
    }

    #[tool(
        description = "Ask a question of the ingested projects. Retrieves the best candidates (full text, numbered) into an ask packet. Read every candidate in full, then call answer. Searched and skipped projects are named."
    )]
    async fn ask(&self, args: Parameters<AskArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            let kinds = a.kinds.unwrap_or_default();
            let q = gw_ops::ask::Query {
                text: a.question.as_deref().unwrap_or_default(),
                project: a.project.as_deref(),
                expert: a.expert.as_deref(),
                kinds: &kinds,
                limit: a.k.unwrap_or(gw_ops::ask::DEFAULT_K),
            };
            match a.lens.as_deref() {
                Some(lens) => gw_ops::lens::ask(&home, &store, lens, a.question.as_deref(), q),
                None => gw_ops::ask::open(&home, &store, q, a.by.as_deref().unwrap_or("agent")),
            }
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "Answer an ask packet. Give each quote as a line range: {n, lines: [a, b]} using the file line numbers the candidate shows; the program copies the text, so it cannot be mistyped. The check refuses: a candidate not read, lines outside the candidate, a typed quote not found word for word, a quote from a candidate not read or outside the packet, support from a unit that changed or left since retrieval. A FAILED packet may be answered again. A passed answer is kept as a checked fact for the next session unless you pass record: false (a throwaway lookup)."
    )]
    async fn answer(&self, args: Parameters<AnswerArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || gw_ops::ask::answer(&home, &store, &a.packet, &a.read, &a.answer))
            .await
            .map_err(fail)
            .and_then(passed)
    }

    #[tool(
        description = "Lenses: every expert is one. Without a lens, list them with their checks and standard. With a lens and a project, read the project through it: findings that pass its standard, those that no longer do, and every attack (disputes/supports) on them, kept side by side."
    )]
    async fn lens(&self, args: Parameters<LensArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || match (a.lens.as_deref(), a.project.as_deref()) {
            (Some(lens), Some(project)) => gw_ops::lens::view(&home, &store, project, lens, a.limit.unwrap_or(100)),
            (None, None) => gw_ops::lens::list(),
            _ => Err(anyhow::anyhow!(
                "give both lens and project to read through a lens, or neither to list lenses"
            )),
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "Open a deliberation panel on a project question: seats (lenses) answer blind in round 1, then cross-examine anonymized answers in round 2; a program checks the protocol before it tallies. The panel is locked when opened. Returns the panel id and the round-1 brief."
    )]
    async fn panel_open(&self, args: Parameters<PanelOpenArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            let project = gw_ops::registry::Registry::load(&home)?
                .project(&a.project)?
                .name
                .clone();
            let settings = gw_ops::panel::owner_settings(&home)?;
            gw_ops::panel::open(
                &store,
                &project,
                &a.panel,
                settings.as_ref(),
                a.by.as_deref().unwrap_or("agent"),
            )
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "Save one seat's answer to a panel, verbatim: round 1 (blind; closed once anonymized), round 2 (after the anonymized bundle), or counterfactual (when the agreement check asks for one)."
    )]
    async fn panel_submit(&self, args: Parameters<PanelSubmitArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            let project = gw_ops::registry::Registry::load(&home)?
                .project(&a.project)?
                .name
                .clone();
            gw_ops::panel::submit(
                &store,
                &project,
                &a.panel,
                &a.round,
                &a.seat,
                &a.text,
                a.by.as_deref().unwrap_or("agent"),
            )
        })
        .await
        .map_err(fail)
    }

    #[tool(
        description = "Where a panel stands, and its next step: lists every re-prompt owed (by seat, with the exact prompt to send), anonymizes round 1 once complete (returning the bundle for round 2), and tallies once nothing is owed (CONSENSUS, or SPLIT returned to the owner, with any dealbreaker minority kept)."
    )]
    async fn panel(&self, args: Parameters<PanelArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || {
            let project = gw_ops::registry::Registry::load(&home)?
                .project(&a.project)?
                .name
                .clone();
            gw_ops::panel::status(&store, &project, &a.panel).map(|(text, _)| text)
        })
        .await
        .map_err(fail)
    }

    #[tool(description = "The expert directory: every expert agent with a summary of what it is expert in.")]
    async fn expert_list(&self) -> Result<String, String> {
        ops::experts().map_err(fail)
    }

    #[tool(description = "Check the registry, templates, secret references, profile and store from this machine.")]
    async fn doctor(&self) -> Result<String, String> {
        ops::doctor(&self.home, self.store().await)
            .await
            .map_err(fail)
            .and_then(passed)
    }
}

#[tool_handler(router = self.tool_router)]
impl ServerHandler for Groundworks {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new("groundworks", env!("CARGO_PKG_VERSION")))
            .with_instructions(INSTRUCTIONS)
    }
}
