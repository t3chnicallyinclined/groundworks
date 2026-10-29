mod homeless;
mod selftest;
mod server;

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;

use anyhow::Result;
use clap::{Parser, Subcommand, ValueEnum};
use rmcp::ServiceExt;

use gw_ops::home::Home;
use gw_ops::journal::{Confidence, Kind};
use gw_ops::ops;
use gw_ops::store::{self, Quote, Store};

/// Groundworks: the registry, journal, knowledge and tools agents use to work on every project.
#[derive(Parser)]
#[command(name = "groundworks", version)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Serve the MCP tools over stdio.
    Serve,
    /// List registered projects.
    Projects,
    /// Show a project's entry and journal.
    Show {
        project: String,
        #[arg(long, default_value_t = 20)]
        limit: usize,
    },
    /// Show an env's vars and secret references.
    Env {
        project: String,
        #[arg(default_value = "local")]
        env: String,
    },
    /// Run a command in a project's env.
    Run {
        project: String,
        env: String,
        #[arg(long, default_value_t = 300)]
        timeout: u64,
        #[arg(long)]
        confirm: Option<String>,
        #[arg(trailing_var_arg = true, required = true)]
        command: Vec<String>,
    },
    /// Run the project's stack gate.
    Gate {
        project: String,
        #[arg(default_value = "local")]
        env: String,
        #[arg(long, default_value_t = 900)]
        timeout: u64,
    },
    /// Scaffold a new project from a stack's template.
    Bootstrap {
        stack: String,
        name: String,
        #[arg(long)]
        dir: Option<PathBuf>,
        #[arg(long)]
        no_register: bool,
    },
    /// Record a fact in a project's journal (the store refuses CONFIRMED without evidence).
    Note {
        project: String,
        #[arg(value_enum)]
        kind: Kind,
        text: String,
        #[arg(long, default_value = "")]
        source: String,
        #[arg(long, value_enum, default_value = "inferred")]
        confidence: Confidence,
        #[arg(long, default_value = "owner")]
        by: String,
        /// A receipt id from run or gate.
        #[arg(long)]
        receipt: Option<String>,
        /// Write it as the owner, signed by the owner login (needs GROUNDWORKS_OWNER_PASS).
        #[arg(long)]
        as_owner: bool,
        /// What the owner said, with the date.
        #[arg(long)]
        owner_statement: Option<String>,
        /// A file (relative to the project) holding the quote.
        #[arg(long, requires_all = ["quote_line", "quote_text"])]
        quote_file: Option<String>,
        #[arg(long)]
        quote_line: Option<usize>,
        /// The exact text; may start with `-` (e.g. a SQL or shell comment).
        #[arg(long, allow_hyphen_values = true)]
        quote_text: Option<String>,
    },
    /// The store: create credentials and start it, report on it, import old journals.
    Store {
        #[command(subcommand)]
        action: StoreAction,
    },
    /// Owner-only changes. The owner password comes from GROUNDWORKS_OWNER_PASS (any length; empty works with a
    /// warning) and is never stored.
    Owner {
        #[command(subcommand)]
        action: OwnerAction,
    },
    /// Read-only estate scan of a folder (default: the workspace) into estate.json.
    Scan {
        root: Option<PathBuf>,
        #[arg(long)]
        out: Option<PathBuf>,
    },
    /// Check registry, templates, secrets and profile.
    Doctor,
    /// Install team/gw-*.md into ~/.claude/agents.
    InstallTeam,
    /// Ingest a project (or --all) into the store, by kind and by expert area.
    Intake {
        project: Option<String>,
        #[arg(long)]
        all: bool,
        #[arg(long, default_value_t = 300)]
        max_commits: usize,
    },
    /// Search ingested units across shared projects, or one project.
    Search {
        query: String,
        #[arg(long)]
        project: Option<String>,
        #[arg(long)]
        expert: Option<String>,
        /// Only these kinds (repeatable): context, doc, code, config, commit, quote.
        #[arg(long = "kind")]
        kinds: Vec<String>,
        #[arg(long, default_value_t = 10)]
        limit: usize,
    },
    /// Grep over the ingested files: exact, case-insensitive, or /regex/.
    Grep {
        pattern: String,
        #[arg(long)]
        project: Option<String>,
        #[arg(long, default_value_t = 20)]
        limit: usize,
    },
    /// The expert directory.
    Experts,
    /// Open an ask packet: the best candidates for a question, in full, numbered.
    Ask {
        question: String,
        #[arg(long)]
        project: Option<String>,
        #[arg(long)]
        expert: Option<String>,
        #[arg(long = "kind")]
        kinds: Vec<String>,
        #[arg(long, default_value_t = gw_ops::ask::DEFAULT_K)]
        k: usize,
    },
    /// The pulse: is the knowledge growing, used, and holding up? A few store numbers, no model; one line
    /// appended to metrics/pulse.jsonl so the trend is a file. `--history` prints the trend.
    Pulse {
        #[arg(long)]
        history: bool,
    },
    /// Check an eval question set (JSON Lines): every gold quote found word for word in a tracked
    /// file of its project, every trap's premise absent.
    EvalCheck { file: std::path::PathBuf },
    /// Draw a question-set skeleton from real asks: the passed answers of the last --days, their
    /// questions and support quotes, one JSON line each with category TODO. The owner approves or
    /// rewrites every line; then eval-check proves the set. Nothing drawn is gold until then.
    EvalDraw {
        #[arg(long, default_value_t = 30)]
        days: u64,
        /// Authors to leave out (the eval runner's own sessions answer as `agent`).
        #[arg(long, value_delimiter = ',', default_value = "agent")]
        exclude_by: Vec<String>,
        #[arg(long)]
        out: Option<std::path::PathBuf>,
    },
    /// Run an eval set: each question with and without Groundworks in fresh headless sessions, each
    /// answer graded blind. Resumes an interrupted run given the same --out.
    EvalRun {
        set: std::path::PathBuf,
        #[arg(long)]
        out: Option<std::path::PathBuf>,
        /// Only these question ids (comma separated).
        #[arg(long, value_delimiter = ',')]
        only: Vec<String>,
        #[arg(long, value_delimiter = ',', default_value = "plain,groundworks")]
        arms: Vec<String>,
        #[arg(long, default_value = "opus")]
        model: String,
        #[arg(long, default_value = "opus")]
        grader_model: String,
        /// Dollar cap per answering session.
        #[arg(long, default_value_t = 5.0)]
        budget_usd: f64,
        /// Claude effort level for the answering sessions (low, medium, high, xhigh, max).
        #[arg(long)]
        effort: Option<String>,
        /// Seeding pass: leave recording on and ask for record: true on passed answers.
        #[arg(long)]
        record: bool,
    },
    /// Retrieval alone, no model: is every gold line ingested, and where does the ranked read put it?
    /// With --ladder, the pre-registered rerank ladder over one or more sets (report and rows to --out).
    EvalRetrieval {
        #[arg(required = true)]
        files: Vec<std::path::PathBuf>,
        #[arg(long)]
        ladder: bool,
        /// Candidates in the pool each arm reorders.
        #[arg(long, default_value_t = 60)]
        depth: usize,
        #[arg(long)]
        out: Option<std::path::PathBuf>,
    },
    /// Router test, offline: can a cheap judge tell from the candidates alone that a question is
    /// answerable? Labels come from the gold; no agent sessions.
    EvalRouter {
        set: std::path::PathBuf,
        /// Also run the small-model (haiku) router, one call per question.
        #[arg(long)]
        haiku: bool,
    },
    /// Seed the journal from an approved set: gold answers become CONFIRMED facts with their gold quotes.
    EvalSeed { set: std::path::PathBuf },
    /// Summarise an eval run folder: rates with 95% intervals per arm, and the paired comparison.
    EvalReport { dir: std::path::PathBuf },
    /// Prove it works on this machine: a throwaway home and store, this binary as the MCP server,
    /// every rule an agent relies on. Never touches the real home or store.
    Selftest,
}

#[derive(Subcommand)]
enum StoreAction {
    /// Create the store credentials if missing, start the store, create the owner login from
    /// GROUNDWORKS_OWNER_PASS, and import the old file journals.
    Init {
        /// Skip creating the owner login (owner commands will not work until it exists).
        #[arg(long)]
        no_owner: bool,
    },
    /// Is the store up, which version, which namespaces.
    Status,
    /// Import any remaining file journals.
    Import,
    /// Keep the store answering: adopt one already up, start one when none is, start it again when it
    /// stops, and export every namespace once a day. Runs until killed; `store install` runs it at logon.
    Supervise {
        /// The home to serve (default: discovered from the current directory).
        #[arg(long)]
        home: Option<PathBuf>,
        /// The SurrealDB binary (default: the home's setting, then PATH, then the installer's location).
        #[arg(long)]
        surreal: Option<PathBuf>,
        #[arg(long, default_value_t = 5000)]
        poll_ms: u64,
        /// Local time of the daily export, HH:MM.
        #[arg(long, default_value = "03:30")]
        export_at: String,
        #[arg(long)]
        no_export: bool,
        /// Days of exports to keep under <home>/backup.
        #[arg(long, default_value_t = 7)]
        keep: usize,
        /// Stop after this many starts (the selftest uses it).
        #[arg(long)]
        exit_after_starts: Option<usize>,
        /// Skip the restore drill that follows the daily export.
        #[arg(long)]
        no_drill: bool,
    },
    /// Export every namespace now, as gzipped SurrealQL under <home>/backup/<date>/.
    Export {
        #[arg(long)]
        home: Option<PathBuf>,
        #[arg(long, default_value_t = 7)]
        keep: usize,
    },
    /// Restore drill: import a day's export (the newest by default) into a scratch SurrealDB started
    /// for the purpose and compare every table's row count with the counts taken at export time.
    /// Writes DRILL beside the export; exits non-zero on FAIL. Never touches the live store.
    Drill {
        #[arg(long)]
        home: Option<PathBuf>,
        /// The export's day folder (YYYY-MM-DD).
        #[arg(long)]
        day: Option<String>,
    },
    /// Windows: register `store supervise` as a scheduled task for this user that runs at logon with no
    /// window and no stored password, and start it now.
    Install {
        #[arg(long)]
        home: Option<PathBuf>,
    },
    /// Windows: remove that task. The store it started keeps running until it stops.
    Uninstall,
}

/// The supervisor's commands take their home from `--home`, not the current directory, because a
/// scheduled task has no session to discover it from.
fn service_home(home: Option<PathBuf>) -> Result<Home> {
    match home {
        Some(root) => Home::open(&root),
        None => Home::discover(),
    }
}

/// `store supervise|export|install|uninstall`, which never go through the shared home discovery.
async fn store_service(action: &StoreAction) -> Result<Option<String>> {
    Ok(Some(match action {
        StoreAction::Supervise {
            home,
            surreal,
            poll_ms,
            export_at,
            no_export,
            keep,
            exit_after_starts,
            no_drill,
        } => {
            let home = service_home(home.clone())?;
            let store = gw_ops::supervise::open_store(&home, surreal.clone())?;
            let opts = gw_ops::supervise::Options {
                poll: std::time::Duration::from_millis(*poll_ms),
                export_at: if *no_export {
                    None
                } else {
                    Some(gw_ops::supervise::parse_time(export_at)?)
                },
                keep: *keep,
                exit_after_starts: *exit_after_starts,
                log: gw_ops::supervise::log_path(&home, &store),
                backups: gw_ops::supervise::backups_dir(&home),
                drill: !*no_drill,
                home_root: Some(home.root.clone()),
            };
            store::blocking(move || gw_ops::supervise::run(&store, &opts)).await?;
            "supervisor stopped".to_owned()
        }
        StoreAction::Drill { home, day } => {
            let home = service_home(home.clone())?;
            let store = gw_ops::supervise::open_store(&home, None)?;
            let (dir, day) = (gw_ops::supervise::backups_dir(&home), day.clone());
            let d = store::blocking(move || gw_ops::supervise::drill(&store, &dir, day.as_deref())).await?;
            let mut out = format!(
                "{} restore drill of {} in {:.1}s\n",
                if d.passed { "PASS" } else { "FAIL" },
                d.dir.display(),
                d.seconds
            );
            for line in &d.lines {
                out.push_str(line);
                out.push('\n');
            }
            anyhow::ensure!(d.passed, "{out}");
            out
        }
        StoreAction::Export { home, keep } => {
            let home = service_home(home.clone())?;
            let store = gw_ops::supervise::open_store(&home, None)?;
            let (dir, keep) = (gw_ops::supervise::backups_dir(&home), *keep);
            let files = store::blocking(move || gw_ops::supervise::export(&store, &dir, keep)).await?;
            let mut out = String::new();
            for f in &files {
                let size = std::fs::metadata(f).map_or(0, |m| m.len());
                out.push_str(&format!("{} ({} KB)\n", f.display(), size / 1024));
            }
            out
        }
        StoreAction::Install { home } => {
            let home = service_home(home.clone())?;
            let store = gw_ops::supervise::open_store(&home, None)?;
            gw_ops::supervise::install_task(&home, &store, &std::env::current_exe()?)?
        }
        StoreAction::Uninstall => gw_ops::supervise::uninstall_task()?,
        _ => return Ok(None),
    }))
}

#[derive(Subcommand)]
enum OwnerAction {
    /// Set a scope's isolation level (global, ws_<name> or proj_<slug>).
    Level {
        scope: String,
        #[arg(value_enum)]
        level: Level,
        /// The workspace a project belongs to (ws_<name>).
        #[arg(long)]
        workspace: Option<String>,
    },
    /// Wall two scopes off from each other.
    Wall {
        a: String,
        b: String,
        #[arg(long)]
        why: String,
    },
}

#[derive(Clone, Copy, ValueEnum)]
enum Level {
    Shared,
    Private,
    Isolated,
}

impl Level {
    fn as_str(self) -> &'static str {
        match self {
            Level::Shared => "shared",
            Level::Private => "private",
            Level::Isolated => "isolated",
        }
    }
}

/// The owner password from `GROUNDWORKS_OWNER_PASS`, typed by the owner for this one command.
/// Any length is accepted. With none set the owner login has an empty password, which works but
/// means any agent can act as the owner, so it says so on stderr every time.
fn owner_pass() -> String {
    let pass = std::env::var("GROUNDWORKS_OWNER_PASS").unwrap_or_default();
    if pass.is_empty() {
        eprintln!(
            "warning: no owner password (GROUNDWORKS_OWNER_PASS is empty or not set). The owner login has an \
             empty password, so any agent on this machine can change scope levels and walls or sign as the owner."
        );
    }
    pass
}

#[tokio::main]
async fn main() -> ExitCode {
    // stdout is the MCP channel; logs go to stderr only.
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_env("GROUNDWORKS_LOG"))
        .with_writer(std::io::stderr)
        .with_ansi(false)
        .init();
    match dispatch(Cli::parse().command).await {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::FAILURE,
        Err(e) => {
            eprintln!("error: {e:#}");
            ExitCode::FAILURE
        }
    }
}

/// Ok(false) means the operation ran and reported a failure (a red gate, a non-zero exit).
async fn open_store(home: &Arc<Home>) -> Result<Arc<Store>> {
    let home = Arc::clone(home);
    Ok(Arc::new(store::blocking(move || Store::open(&home)).await?))
}

async fn dispatch(command: Command) -> Result<bool> {
    if matches!(command, Command::Selftest) {
        let (report, failed) = store::blocking(|| selftest::run(&std::env::current_exe()?)).await?;
        println!("{report}");
        return Ok(failed == 0);
    }
    if matches!(command, Command::Serve) {
        // The plugin starts this in every session; outside any home it must still start.
        match Home::discover() {
            Ok(home) => {
                tracing::info!(home = %home.root.display(), "serving MCP over stdio");
                server::Groundworks::new(home)
                    .serve(rmcp::transport::stdio())
                    .await?
                    .waiting()
                    .await?;
            }
            Err(e) => {
                tracing::info!(reason = %format!("{e:#}"), "no home here; serving the status tool only");
                homeless::Homeless::new(format!("{e:#}"))
                    .serve(rmcp::transport::stdio())
                    .await?
                    .waiting()
                    .await?;
            }
        }
        return Ok(true);
    }
    if let Command::Store { action } = &command
        && let Some(text) = store_service(action).await?
    {
        println!("{}", text.trim_end());
        return Ok(true);
    }
    let home = Arc::new(Home::discover()?);
    let (text, ok) = match command {
        Command::Serve | Command::Selftest => unreachable!("handled above"),
        Command::Projects => (ops::projects(&home)?, true),
        Command::Show { project, limit } => {
            let store = open_store(&home).await.ok();
            let home = Arc::clone(&home);
            let text = store::blocking(move || ops::show(&home, store.as_deref(), &project, limit)).await?;
            (text, true)
        }
        Command::Env { project, env } => (ops::env_show(&home, &project, &env).await?, true),
        Command::Run {
            project,
            env,
            timeout,
            confirm,
            command,
        } => {
            let store = open_store(&home).await.ok();
            ops::run(
                &home,
                store,
                &project,
                &env,
                &command.join(" "),
                timeout,
                confirm.as_deref(),
            )
            .await?
        }
        Command::Gate { project, env, timeout } => {
            let store = open_store(&home).await.ok();
            ops::gate(&home, store, &project, &env, timeout).await?
        }
        Command::Bootstrap {
            stack,
            name,
            dir,
            no_register,
        } => (
            ops::bootstrap(&home, &stack, &name, dir.as_deref(), !no_register)?,
            true,
        ),
        Command::Note {
            project,
            kind,
            text,
            source,
            confidence,
            by,
            receipt,
            as_owner,
            owner_statement,
            quote_file,
            quote_line,
            quote_text,
        } => {
            let quote = match (quote_file, quote_line, quote_text) {
                (Some(file), Some(line), Some(text)) => Some(Quote { file, line, text }),
                _ => None,
            };
            let owner = if as_owner { Some(owner_pass()) } else { None };
            let store = open_store(&home).await?;
            let home = Arc::clone(&home);
            let text = store::blocking(move || {
                ops::note(
                    &home,
                    &store,
                    ops::Note {
                        project: &project,
                        kind,
                        text: &text,
                        source: &source,
                        confidence,
                        by: &by,
                        receipt: receipt.as_deref(),
                        owner_statement: owner_statement.as_deref(),
                        quote,
                        lens: None,
                        attacks: None,
                        stance: None,
                        owner_pass: owner.as_deref(),
                    },
                )
            })
            .await?;
            (text, true)
        }
        Command::Store { action } => (store_action(&home, action).await?, true),
        Command::Owner { action } => {
            let pass = owner_pass();
            let store = open_store(&home).await?;
            let text = store::blocking(move || match action {
                OwnerAction::Level {
                    scope,
                    level,
                    workspace,
                } => {
                    store.set_level(&pass, &scope, level.as_str(), workspace.as_deref())?;
                    Ok(format!("{scope} is now {}", level.as_str()))
                }
                OwnerAction::Wall { a, b, why } => {
                    store.add_wall(&pass, &a, &b, &why)?;
                    Ok(format!("wall between {a} and {b} recorded"))
                }
            })
            .await?;
            (text, true)
        }
        Command::Scan { root, out } => {
            let home = Arc::clone(&home);
            let text = store::blocking(move || ops::estate_scan(&home, root.as_deref(), out.as_deref())).await?;
            (text, true)
        }
        Command::Doctor => {
            let store = open_store(&home).await.map_err(|e| format!("{e:#}"));
            ops::doctor(&home, store).await?
        }
        Command::InstallTeam => (ops::install_team(&home)?, true),
        Command::Intake {
            project,
            all,
            max_commits,
        } => {
            let store = open_store(&home).await?;
            let home = Arc::clone(&home);
            let text = store::blocking(move || {
                let names: Vec<String> = match (project, all) {
                    (Some(p), false) => vec![p],
                    (None, true) => gw_ops::registry::Registry::load(&home)?
                        .projects
                        .keys()
                        .cloned()
                        .collect(),
                    _ => anyhow::bail!("name one project, or pass --all"),
                };
                let mut out = String::new();
                for name in names {
                    match ops::intake(&home, &store, &name, max_commits) {
                        Ok(text) => out.push_str(&text),
                        Err(e) => out.push_str(&format!("{name}: NOT INGESTED: {e:#}\n")),
                    }
                }
                Ok(out)
            })
            .await?;
            (text, true)
        }
        Command::Search {
            query,
            project,
            expert,
            kinds,
            limit,
        } => {
            let store = open_store(&home).await?;
            let home = Arc::clone(&home);
            let text = store::blocking(move || {
                ops::search(
                    &home,
                    &store,
                    gw_ops::ask::Query {
                        text: &query,
                        project: project.as_deref(),
                        expert: expert.as_deref(),
                        kinds: &kinds,
                        limit,
                    },
                )
            })
            .await?;
            (text, true)
        }
        Command::Experts => (ops::experts()?, true),
        Command::Grep {
            pattern,
            project,
            limit,
        } => {
            let store = open_store(&home).await?;
            let home = Arc::clone(&home);
            (
                store::blocking(move || ops::grep(&home, &store, project.as_deref(), &pattern, limit)).await?,
                true,
            )
        }
        Command::Pulse { history } => {
            if history {
                (gw_ops::pulse::history(&home)?, true)
            } else {
                let store = open_store(&home).await?;
                let home2 = Arc::clone(&home);
                let text = store::blocking(move || {
                    let p = gw_ops::pulse::take(&home2, &store)?;
                    let path = gw_ops::pulse::record(&home2, &p)?;
                    Ok(format!("{}recorded in {}", gw_ops::pulse::render(&p), path.display()))
                })
                .await?;
                (text, true)
            }
        }
        Command::EvalCheck { file } => gw_ops::eval::check(&home, &file)?,
        Command::EvalDraw { days, exclude_by, out } => {
            let store = open_store(&home).await?;
            let (text, n) = store::blocking(move || gw_ops::eval::draw(&store, days, &exclude_by)).await?;
            let tail = format!("{n} question(s) drawn (category TODO: approve or rewrite each, then eval-check)");
            match out {
                Some(path) => {
                    std::fs::write(&path, &text)?;
                    (format!("{tail} into {}", path.display()), n > 0)
                }
                None => (format!("{text}{tail}"), n > 0),
            }
        }
        Command::EvalRun {
            set,
            out,
            only,
            arms,
            model,
            grader_model,
            budget_usd,
            effort,
            record,
        } => {
            let out = out.unwrap_or_else(|| gw_ops::eval_run::default_out(&set));
            // The sessions run in a folder inside the run folder, so every path they get is absolute.
            std::fs::create_dir_all(&out)?;
            let out = std::path::absolute(&out)?;
            let log = gw_ops::eval_run::run(
                &home,
                &gw_ops::eval_run::Plan {
                    set: &set,
                    out: &out,
                    only: &only,
                    arms: &arms,
                    model: &model,
                    grader_model: &grader_model,
                    budget_usd,
                    effort: effort.as_deref(),
                    record,
                },
            )?;
            (
                format!(
                    "{log}\nrun folder: {}\n{}",
                    out.display(),
                    gw_ops::eval_run::report(&out)?
                ),
                true,
            )
        }
        Command::EvalReport { dir } => (gw_ops::eval_run::report(&dir)?, true),
        Command::EvalRouter { set, haiku } => {
            let store = open_store(&home).await?;
            let home = Arc::clone(&home);
            let out = gw_ops::eval_run::default_out(&set).with_file_name(format!(
                "router-{}",
                gw_ops::eval_run::default_out(&set)
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
            ));
            std::fs::create_dir_all(out.join("empty"))?;
            let out = std::path::absolute(&out)?;
            let cwd = out.join("empty");
            let (text, rows) =
                store::blocking(move || gw_ops::eval_router::run(&home, &store, &set, haiku, &cwd)).await?;
            let mut f = String::new();
            for r in &rows {
                f.push_str(&serde_json::to_string(r)?);
                f.push('\n');
            }
            std::fs::write(out.join("router.jsonl"), f)?;
            (format!("{text}\nrows: {}", out.join("router.jsonl").display()), true)
        }
        Command::EvalSeed { set } => {
            let store = open_store(&home).await?;
            let home = Arc::clone(&home);
            (
                store::blocking(move || gw_ops::eval::seed(&home, &store, &set)).await?,
                true,
            )
        }
        Command::EvalRetrieval {
            files,
            ladder,
            depth,
            out,
        } => {
            let store = open_store(&home).await?;
            if ladder {
                let home = Arc::clone(&home);
                let (mut text, rows) =
                    store::blocking(move || gw_ops::eval::ladder(&home, &store, &files, depth)).await?;
                if let Some(out) = out {
                    std::fs::create_dir_all(&out)?;
                    let mut f = String::new();
                    for r in &rows {
                        f.push_str(&serde_json::to_string(r)?);
                        f.push('\n');
                    }
                    std::fs::write(out.join("rows.jsonl"), f)?;
                    std::fs::write(out.join("REPORT.md"), &text)?;
                    text.push_str(&format!("\nwritten: {}\n", out.display()));
                }
                (text, true)
            } else {
                let file = files[0].clone();
                (
                    store::blocking(move || gw_ops::eval::retrieval(&store, &file)).await?,
                    true,
                )
            }
        }
        Command::Ask {
            question,
            project,
            expert,
            kinds,
            k,
        } => {
            let store = open_store(&home).await?;
            let home = Arc::clone(&home);
            let text = store::blocking(move || {
                gw_ops::ask::open(
                    &home,
                    &store,
                    gw_ops::ask::Query {
                        text: &question,
                        project: project.as_deref(),
                        expert: expert.as_deref(),
                        kinds: &kinds,
                        limit: k,
                    },
                    "owner",
                )
            })
            .await?;
            (text, true)
        }
    };
    println!("{}", text.trim_end());
    Ok(ok)
}

async fn store_action(home: &Arc<Home>, action: StoreAction) -> Result<String> {
    match action {
        StoreAction::Init { no_owner } => {
            let owner = if no_owner { None } else { Some(owner_pass()) };
            let created = store::create_secrets(home)?;
            let store = open_store(home).await?;
            let home = Arc::clone(home);
            store::blocking(move || {
                let mut out = String::new();
                for path in &created {
                    out.push_str(&format!("created {}\n", path.display()));
                }
                store.agent(gw_store::GLOBAL_NS)?;
                if let Some(pass) = &owner {
                    store.provision_owner(pass)?;
                    out.push_str("owner login created in global\n");
                }
                out.push_str(&store.status()?);
                out.push('\n');
                out.push_str(&ops::import_journals(&home, &store)?);
                Ok(out)
            })
            .await
        }
        StoreAction::Status => {
            let store = open_store(home).await?;
            store::blocking(move || store.status()).await
        }
        StoreAction::Import => {
            let store = open_store(home).await?;
            let home = Arc::clone(home);
            store::blocking(move || ops::import_journals(&home, &store)).await
        }
        StoreAction::Supervise { .. }
        | StoreAction::Export { .. }
        | StoreAction::Drill { .. }
        | StoreAction::Install { .. }
        | StoreAction::Uninstall => {
            unreachable!("handled by store_service before home discovery")
        }
    }
}
