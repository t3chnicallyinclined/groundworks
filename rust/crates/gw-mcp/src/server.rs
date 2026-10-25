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
}

#[derive(Deserialize, JsonSchema)]
pub struct JournalArgs {
    pub project: String,
    pub kind: Option<Kind>,
    /// Newest N entries (default 50).
    pub limit: Option<usize>,
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
        store::blocking(move || ops::show(&home, store.as_deref(), &a.project, a.journal_limit.unwrap_or(20)))
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
                    project: &a.project,
                    kind: a.kind,
                    text: &a.text,
                    source: &a.source,
                    confidence: a.confidence,
                    by: &a.by,
                    receipt: a.receipt.as_deref(),
                    owner_statement: a.owner_statement.as_deref(),
                    quote: a.quote,
                    owner_pass: None,
                },
            )
        })
        .await
        .map_err(fail)
    }

    #[tool(description = "Read a project's journal, optionally one kind only.")]
    async fn journal_read(&self, args: Parameters<JournalArgs>) -> Result<String, String> {
        let a = args.0;
        let (home, store) = (Arc::clone(&self.home), self.store().await?);
        store::blocking(move || ops::journal(&home, &store, &a.project, a.kind, a.limit.unwrap_or(50)))
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
