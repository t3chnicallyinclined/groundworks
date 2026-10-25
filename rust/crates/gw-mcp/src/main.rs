mod server;

use std::path::PathBuf;
use std::process::ExitCode;

use anyhow::Result;
use clap::{Parser, Subcommand};
use rmcp::ServiceExt;

use gw_ops::home::Home;
use gw_ops::journal::{Confidence, Kind};
use gw_ops::ops;

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
    /// Append to a project's journal.
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
    },
    /// Check registry, templates, secrets and profile.
    Doctor,
    /// Install team/gw-*.md into ~/.claude/agents.
    InstallTeam,
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
async fn dispatch(command: Command) -> Result<bool> {
    let home = Home::discover()?;
    let (text, ok) = match command {
        Command::Serve => {
            tracing::info!(home = %home.root.display(), "serving MCP over stdio");
            let service = server::Groundworks::new(home).serve(rmcp::transport::stdio()).await?;
            service.waiting().await?;
            return Ok(true);
        }
        Command::Projects => (ops::projects(&home)?, true),
        Command::Show { project, limit } => (ops::show(&home, &project, limit)?, true),
        Command::Env { project, env } => (ops::env_show(&home, &project, &env).await?, true),
        Command::Run {
            project,
            env,
            timeout,
            confirm,
            command,
        } => ops::run(&home, &project, &env, &command.join(" "), timeout, confirm.as_deref()).await?,
        Command::Gate { project, env, timeout } => ops::gate(&home, &project, &env, timeout).await?,
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
        } => (ops::note(&home, &project, kind, &text, &source, confidence, &by)?, true),
        Command::Doctor => ops::doctor(&home).await?,
        Command::InstallTeam => (ops::install_team(&home)?, true),
    };
    println!("{}", text.trim_end());
    Ok(ok)
}
