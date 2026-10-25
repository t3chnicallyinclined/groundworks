//! Every operation, shared by the MCP tools and the CLI. Each returns the text a
//! reader sees; operations that run something also say whether it passed.

use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result, bail, ensure};

use crate::home::{Home, validate_name};
use crate::journal::{self, Confidence, Kind};
use crate::registry::{Project, Registry};
use crate::secrets::SecretRef;
use crate::store::{self, FactIn, Quote, ReceiptIn, Store};
use crate::{exec, knowledge, scaffold};

pub const MAX_TIMEOUT_SECS: u64 = 3600;

/// What a gate receipt's `output_hash` covers.
pub const GATE_HASH_OF: &str = "sha256 of each step's output hash (see run), one per line, in step order";

pub fn projects(home: &Home) -> Result<String> {
    let registry = Registry::load(home)?;
    if registry.projects.is_empty() {
        return Ok("No projects registered. Add one with project_register.".to_owned());
    }
    let mut out = String::new();
    for p in registry.projects.values() {
        let envs: Vec<&str> = std::iter::once("local")
            .chain(p.envs.keys().map(String::as_str).filter(|k| *k != "local"))
            .collect();
        writeln!(
            out,
            "{} [{}] envs: {}\n    {}",
            p.name,
            p.stack.as_deref().unwrap_or("no stack"),
            envs.join(", "),
            p.summary
        )?;
    }
    Ok(out)
}

pub fn show(home: &Home, store: Option<&Store>, name: &str, journal_limit: usize) -> Result<String> {
    let registry = Registry::load(home)?;
    let project = registry.project(name)?;
    let path = project.path(&home.workspace);
    let mut out = format!(
        "# {}\npath: {} ({})\n\n{}\n",
        project.name,
        path.display(),
        if path.is_dir() {
            "exists"
        } else {
            "MISSING on this machine"
        },
        toml::to_string_pretty(project)?
    );
    if !project.gate.is_empty() {
        writeln!(out, "gate (project): {}", project.gate.join(" -> "))?;
    } else if let Some(stack) = &project.stack {
        let stack = registry.stack(stack)?;
        writeln!(out, "gate ({}): {}", stack.name, stack.gate.join(" -> "))?;
    }
    for doc in &project.docs {
        let exists = path.join(doc).is_file();
        writeln!(out, "doc: {doc}{}", if exists { "" } else { " (MISSING)" })?;
    }
    out.push_str("\n## Journal\n");
    match store.map(|s| s.facts(name, None, journal_limit)) {
        None => out.push_str("(store unavailable: the journal could not be read)\n"),
        Some(Err(e)) => writeln!(out, "(store unavailable: {e:#})")?,
        Some(Ok(facts)) if facts.is_empty() => out.push_str("(empty: record what you learn with journal_add)\n"),
        Some(Ok(facts)) => {
            for fact in &facts {
                writeln!(out, "- {}", fact.render())?;
            }
        }
    }
    Ok(out)
}

pub fn register(home: &Home, name: &str, dir: Option<&str>, summary: &str, stack: Option<&str>) -> Result<String> {
    validate_name(name)?;
    ensure!(!summary.trim().is_empty(), "a project needs a one-line summary");
    let registry = Registry::load(home)?;
    if let Some(stack) = stack {
        registry.stack(stack)?;
    }
    let path = home.path("registry/projects").join(format!("{name}.toml"));
    if path.exists() || registry.projects.contains_key(name) {
        bail!(
            "project {name} is already registered ({}); edit that file instead",
            path.display()
        );
    }
    let project = Project {
        name: name.to_owned(),
        dir: dir.unwrap_or(name).to_owned(),
        summary: summary.trim().to_owned(),
        stack: stack.map(str::to_owned),
        repo: None,
        docs: Vec::new(),
        gate: Vec::new(),
        envs: Default::default(),
    };
    std::fs::create_dir_all(home.path("registry/projects"))?;
    std::fs::write(&path, toml::to_string_pretty(&project)?)?;
    let exists = project.path(&home.workspace).is_dir();
    Ok(format!(
        "registered {name} -> {}{}",
        path.display(),
        if exists {
            ""
        } else {
            " (its directory does not exist yet)"
        }
    ))
}

pub struct Note<'a> {
    pub project: &'a str,
    pub kind: Kind,
    pub text: &'a str,
    pub source: &'a str,
    pub confidence: Confidence,
    pub by: &'a str,
    pub receipt: Option<&'a str>,
    pub owner_statement: Option<&'a str>,
    pub quote: Option<Quote>,
    /// Write as the owner, signed by the owner's login (CLI only; never from the MCP server).
    pub owner_pass: Option<&'a str>,
}

/// Record a fact in the project's store. The store refuses CONFIRMED without evidence.
pub fn note(home: &Home, store: &Store, note: Note<'_>) -> Result<String> {
    let registry = Registry::load(home)?;
    let project = registry.project(note.project)?;
    let fact = FactIn {
        kind: note.kind,
        text: note.text.to_owned(),
        confidence: note.confidence,
        source: note.source.to_owned(),
        by: note.by.to_owned(),
        receipt: note.receipt.map(str::to_owned),
        owner_statement: note.owner_statement.map(str::to_owned),
        quote: note.quote,
    };
    let dir = project.path(&home.workspace);
    let id = match note.owner_pass {
        Some(pass) => store.add_fact_as_owner(&dir, &project.name, &fact, pass)?,
        None => store.add_fact(&dir, &project.name, &fact)?,
    };
    let recorded = store
        .facts(&project.name, None, 1)?
        .pop()
        .map_or_else(|| id.clone(), |f| f.render());
    Ok(format!("recorded {id}: {recorded}"))
}

pub fn journal(home: &Home, store: &Store, project: &str, kind: Option<Kind>, limit: usize) -> Result<String> {
    Registry::load(home)?.project(project)?;
    let facts = store.facts(project, kind, limit)?;
    if facts.is_empty() {
        return Ok(format!("{project} has no matching journal entries"));
    }
    Ok(facts.iter().map(|f| format!("- {}\n", f.render())).collect())
}

/// Move every old file journal into the store, once. An entry that was CONFIRMED with a source the
/// store cannot verify is imported as INFERRED and says so; the file is renamed `.imported`.
pub fn import_journals(home: &Home, store: &Store) -> Result<String> {
    let registry = Registry::load(home)?;
    let mut out = String::new();
    for project in registry.projects.values() {
        let path = journal::legacy_path(&home.root, &project.name);
        let entries = journal::read_legacy(&path)?;
        if entries.is_empty() {
            continue;
        }
        let (mut imported, mut skipped) = (0usize, 0usize);
        for entry in &entries {
            let (kind, mut text) = entry.kind_and_text();
            let owner = entry.source.trim_start().to_lowercase().starts_with("owner");
            let confidence = match entry.confidence.as_str() {
                "CONFIRMED" if owner => Confidence::Confirmed,
                "CONFIRMED" => {
                    text.push_str(" [was CONFIRMED in the old file journal; the store cannot verify that source]");
                    Confidence::Inferred
                }
                "UNKNOWN" => Confidence::Unknown,
                _ => Confidence::Inferred,
            };
            let fact = FactIn {
                kind,
                text,
                confidence,
                source: format!("{} (imported)", entry.source),
                by: entry.by.clone(),
                receipt: None,
                owner_statement: owner.then(|| entry.source.clone()),
                quote: None,
            };
            match store.import_fact(
                &project.path(&home.workspace),
                &project.name,
                &fact,
                &entry.ts,
                Some(entry.machine.as_str()),
            )? {
                Some(_) => imported += 1,
                None => skipped += 1,
            }
        }
        let done = path.with_extension("jsonl.imported");
        std::fs::rename(&path, &done)?;
        writeln!(
            out,
            "{}: {imported} imported, {skipped} already there (an import can be re-run safely); {} renamed",
            project.name,
            done.display()
        )?;
    }
    if out.is_empty() {
        out.push_str("no file journals to import\n");
    }
    Ok(out)
}

pub fn stacks(home: &Home) -> Result<String> {
    let registry = Registry::load(home)?;
    Ok(registry
        .stacks
        .values()
        .map(|s| {
            let template = s
                .template
                .as_deref()
                .map_or(String::new(), |t| format!(" (template: {t})"));
            format!("{}{template}\n    {}\n", s.name, s.summary)
        })
        .collect())
}

pub fn stack(home: &Home, name: &str) -> Result<String> {
    let registry = Registry::load(home)?;
    Ok(toml::to_string_pretty(registry.stack(name)?)?)
}

/// Variables and secret references; each secret is resolved to prove it works,
/// and the value is dropped unread.
pub async fn env_show(home: &Home, project: &str, env_name: &str) -> Result<String> {
    let registry = Registry::load(home)?;
    let project = registry.project(project)?;
    let env = registry.env(project, env_name)?;
    let mut out = format!("# {}/{env_name}\n", project.name);
    match &env.ssh {
        Some(host) => writeln!(out, "runs on: ssh {host} in {}", env.dir.as_deref().unwrap_or("~"))?,
        None => writeln!(
            out,
            "runs on: this machine in {}",
            project.path(&home.workspace).display()
        )?,
    }
    if env.protected {
        writeln!(out, "protected: run needs confirm = \"{}/{env_name}\"", project.name)?;
    }
    for (key, value) in &env.vars {
        writeln!(out, "var {key} = {value}")?;
    }
    for (key, raw) in &env.secrets {
        let status = match raw.parse::<SecretRef>() {
            Ok(secret) => match secret.resolve().await {
                Ok(_) => "resolves".to_owned(),
                Err(e) => format!("DOES NOT RESOLVE: {e:#}"),
            },
            Err(e) => format!("BAD REF: {e:#}"),
        };
        writeln!(out, "secret {key} <- {raw} ({status})")?;
    }
    Ok(out)
}

pub async fn run(
    home: &Home,
    store: Option<Arc<Store>>,
    project: &str,
    env_name: &str,
    command: &str,
    timeout_secs: u64,
    confirm: Option<&str>,
) -> Result<(String, bool)> {
    ensure!(!command.trim().is_empty(), "no command given");
    ensure!(
        (1..=MAX_TIMEOUT_SECS).contains(&timeout_secs),
        "timeout must be 1..={MAX_TIMEOUT_SECS} seconds"
    );
    let registry = Registry::load(home)?;
    let project = registry.project(project)?;
    let env = registry.env(project, env_name)?;
    let target = format!("{}/{env_name}", project.name);
    if env.protected && confirm != Some(target.as_str()) {
        bail!("{target} is protected: pass confirm = \"{target}\" once the owner has said go");
    }
    let outcome = exec::run(home, project, &env, command, Duration::from_secs(timeout_secs)).await?;
    let receipt = ReceiptIn {
        command: command.to_owned(),
        machine: outcome.machine.clone(),
        location: outcome.location.clone(),
        exit_code: outcome.code,
        ok: outcome.ok(),
        output_hash: outcome.output_hash.clone(),
        hash_of: exec::RUN_HASH_OF,
        commit: None,
    };
    let line = record_receipt(
        store,
        &project.name,
        env.ssh.is_none().then(|| outcome.location.clone()),
        receipt,
    )
    .await;
    Ok((format!("{}\n{line}", outcome.render()), outcome.ok()))
}

/// Record a receipt and say so in one line, or say plainly that it was not recorded.
async fn record_receipt(
    store: Option<Arc<Store>>,
    project: &str,
    local_dir: Option<String>,
    mut receipt: ReceiptIn,
) -> String {
    let Some(store) = store else {
        return "receipt: NOT RECORDED (store unavailable)".to_owned();
    };
    let project = project.to_owned();
    let result = store::blocking(move || {
        receipt.commit = local_dir.as_deref().and_then(head_commit);
        store.record_receipt(&project, &receipt)
    })
    .await;
    match result {
        Ok(id) => format!("receipt: {id} (cite it with journal_add receipt = \"{id}\")"),
        Err(e) => format!("receipt: NOT RECORDED ({e:#})"),
    }
}

/// `git rev-parse HEAD` in `dir`, if it is a repository.
fn head_commit(dir: &str) -> Option<String> {
    let out = std::process::Command::new("git")
        .args(["--no-optional-locks", "-C", dir, "rev-parse", "HEAD"])
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim().to_owned())
        .filter(|s| !s.is_empty())
}

/// The project's gate if it declares one, else its stack's; step by step. A gate with no
/// steps is an error, not a pass.
pub async fn gate(
    home: &Home,
    store: Option<Arc<Store>>,
    project: &str,
    env_name: &str,
    timeout_secs: u64,
) -> Result<(String, bool)> {
    let registry = Registry::load(home)?;
    let project = registry.project(project)?;
    let (source, steps) = if project.gate.is_empty() {
        let stack_name = project
            .stack
            .as_deref()
            .with_context(|| format!("{} has no gate and no stack, so it has no gate", project.name))?;
        (format!("stack {stack_name}"), registry.stack(stack_name)?.gate.clone())
    } else {
        (format!("project {}", project.name), project.gate.clone())
    };
    ensure!(
        !steps.is_empty(),
        "{source} has no gate steps; a gate that runs nothing is not a pass"
    );
    let env = registry.env(project, env_name)?;
    if env.protected {
        bail!(
            "{}/{env_name} is protected; gates run against unprotected envs only",
            project.name
        );
    }
    let total = steps.len();
    let mut out = format!("gate from {source}\n");
    let mut green = true;
    let mut last: Option<exec::Outcome> = None;
    let mut step_hashes = Vec::with_capacity(total);
    for (i, step) in steps.iter().enumerate() {
        let outcome = exec::run(home, project, &env, step, Duration::from_secs(timeout_secs)).await?;
        step_hashes.push(outcome.output_hash.clone());
        let state = if outcome.ok() { "pass" } else { "FAIL" };
        writeln!(
            out,
            "step {}/{total} {state} ({:.1}s): {step}",
            i + 1,
            outcome.elapsed.as_secs_f64()
        )?;
        if !outcome.ok() {
            writeln!(out, "\nGATE RED at step {}/{total}\n\n{}", i + 1, outcome.render())?;
            green = false;
            last = Some(outcome);
            break;
        }
        last = Some(outcome);
    }
    let last = last.expect("a gate with steps ran at least one");
    if green {
        writeln!(
            out,
            "\nGATE GREEN {total}/{total} on {} in {}",
            last.machine, last.location
        )?;
    }
    let receipt = ReceiptIn {
        command: format!("gate: {}", steps.join(" && ")),
        machine: last.machine.clone(),
        location: last.location.clone(),
        exit_code: last.code,
        ok: green,
        output_hash: store::sha256_hex(&[step_hashes.join("\n").as_bytes()]),
        hash_of: GATE_HASH_OF,
        commit: None,
    };
    let line = record_receipt(
        store,
        &project.name,
        env.ssh.is_none().then(|| last.location.clone()),
        receipt,
    )
    .await;
    writeln!(out, "{line}")?;
    Ok((out, green))
}

pub fn bootstrap(home: &Home, stack: &str, name: &str, dir: Option<&Path>, register: bool) -> Result<String> {
    let registry = Registry::load(home)?;
    let stack = registry.stack(stack)?;
    if register && registry.projects.contains_key(name) {
        bail!("project {name} is already registered");
    }
    let target: PathBuf = dir.map_or_else(|| home.workspace.join(name), Path::to_path_buf);
    let files = scaffold::bootstrap(home, stack, name, &target)?;
    let mut out = format!(
        "scaffolded {} from {} ({} files):\n",
        target.display(),
        stack.name,
        files.len()
    );
    for file in &files {
        writeln!(out, "  {}", file.display())?;
    }
    if register {
        let dir = target
            .strip_prefix(&home.workspace)
            .unwrap_or(&target)
            .to_string_lossy()
            .replace('\\', "/");
        let summary = format!("New {} project (edit this summary)", stack.name);
        writeln!(
            out,
            "{}",
            self::register(home, name, Some(&dir), &summary, Some(&stack.name))?
        )?;
    }
    writeln!(out, "next: run `gate` on {name} to prove the scaffold builds")?;
    Ok(out)
}

pub fn knowledge_list(home: &Home) -> Result<String> {
    Ok(knowledge::list(home)?
        .into_iter()
        .map(|page| format!("{}: {}\n", page.topic, page.title))
        .collect())
}

pub fn knowledge_get(home: &Home, topic: &str) -> Result<String> {
    knowledge::get(home, topic)
}

pub fn knowledge_search(home: &Home, query: &str) -> Result<String> {
    ensure!(query.trim().len() >= 2, "search for at least two characters");
    let hits = knowledge::search(home, query.trim(), 60)?;
    if hits.is_empty() {
        return Ok(format!("no knowledge page mentions {query:?}"));
    }
    Ok(hits.join("\n"))
}

pub fn profile(home: &Home) -> Result<String> {
    let path = home
        .role_facts()
        .context("no profile.role_facts in groundworks.toml (or GROUNDWORKS_ROLE_FACTS)")?;
    let text = std::fs::read_to_string(&path).with_context(|| format!("reading {}", path.display()))?;
    Ok(format!(
        "AUTHORITATIVE work history ({}). State nothing about the owner's experience beyond this file; \
         never draft job-application answers from it.\n\n{text}",
        path.display()
    ))
}

/// Checks everything that can be checked from here. Missing project
/// directories are warnings (the registry spans machines); anything else is a failure.
pub async fn doctor(home: &Home, store: Result<Arc<Store>, String>) -> Result<(String, bool)> {
    let mut out = format!(
        "home: {}\nworkspace: {}\n",
        home.root.display(),
        home.workspace.display()
    );
    let (mut fails, mut warns) = (0usize, 0usize);
    let registry = match Registry::load(home) {
        Ok(registry) => registry,
        Err(e) => return Ok((format!("{out}FAIL registry: {e:#}\n"), false)),
    };
    writeln!(
        out,
        "ok   registry: {} projects, {} stacks",
        registry.projects.len(),
        registry.stacks.len()
    )?;
    for stack in registry.stacks.values() {
        if let Some(t) = &stack.template
            && !home.templates().join(t).is_dir()
        {
            fails += 1;
            writeln!(out, "FAIL stack {}: template {t} is missing", stack.name)?;
        }
        if stack.gate.is_empty() {
            warns += 1;
            writeln!(out, "warn stack {}: no gate steps", stack.name)?;
        }
    }
    let mut secrets_checked = 0usize;
    for project in registry.projects.values() {
        if !project.path(&home.workspace).is_dir() {
            warns += 1;
            writeln!(out, "warn {}: {} not on this machine", project.name, project.dir)?;
        }
        let env_names = std::iter::once("local".to_owned()).chain(project.envs.keys().cloned());
        for env_name in env_names.collect::<std::collections::BTreeSet<_>>() {
            let env = registry.env(project, &env_name)?;
            for (key, raw) in &env.secrets {
                secrets_checked += 1;
                let result = match raw.parse::<SecretRef>() {
                    Ok(secret) => secret.resolve().await.map(drop),
                    Err(e) => Err(e),
                };
                if let Err(e) = result {
                    fails += 1;
                    writeln!(out, "FAIL {}/{env_name} secret {key} <- {raw}: {e:#}", project.name)?;
                }
            }
        }
    }
    writeln!(out, "ok   {secrets_checked} secret reference(s) checked")?;
    match home.role_facts() {
        Some(path) if path.is_file() => writeln!(out, "ok   profile: {}", path.display())?,
        Some(path) => {
            fails += 1;
            writeln!(out, "FAIL profile: {} not found", path.display())?;
        }
        None => {
            warns += 1;
            writeln!(out, "warn profile: not configured")?;
        }
    }
    match knowledge::list(home) {
        Ok(pages) => writeln!(out, "ok   knowledge: {} pages", pages.len())?,
        Err(e) => {
            fails += 1;
            writeln!(out, "FAIL knowledge: {e:#}")?;
        }
    }
    match store {
        Ok(store) => match store::blocking(move || store.status()).await {
            Ok(status) => writeln!(out, "ok   {status}")?,
            Err(e) => {
                fails += 1;
                writeln!(out, "FAIL store: {e:#}")?;
            }
        },
        Err(e) => {
            fails += 1;
            writeln!(out, "FAIL store: {e}")?;
        }
    }
    writeln!(
        out,
        "\n{fails} failure(s), {warns} warning(s) on {}",
        crate::home::machine()
    )?;
    Ok((out, fails == 0))
}

/// Copies `team/gw-*.md` into `~/.claude/agents`. Only files with that
/// prefix are ever written, so nothing else in the directory is touched.
pub fn install_team(home: &Home) -> Result<String> {
    let profile = std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .context("neither USERPROFILE nor HOME is set")?;
    let target = PathBuf::from(profile).join(".claude").join("agents");
    std::fs::create_dir_all(&target)?;
    let mut out = String::new();
    let mut count = 0usize;
    for entry in std::fs::read_dir(home.path("team"))? {
        let path = entry?.path();
        let file_name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
        if file_name.starts_with("gw-") && file_name.ends_with(".md") {
            std::fs::copy(&path, target.join(&file_name))?;
            count += 1;
            writeln!(out, "installed {}", target.join(&file_name).display())?;
        }
    }
    ensure!(count > 0, "no team/gw-*.md files found; nothing installed");
    writeln!(out, "{count} agent(s) installed")?;
    Ok(out)
}

/// The read-only estate scan (bootstrap stage 1): one record per project under `root` (default:
/// the workspace), written to `out` (default: `<home>/estate.json`). Returns a summary of counts;
/// the records themselves stay in the file.
pub fn estate_scan(home: &Home, root: Option<&Path>, out: Option<&Path>) -> Result<String> {
    let root = root.map_or_else(|| home.workspace.clone(), Path::to_path_buf);
    ensure!(root.is_dir(), "{} is not a directory", root.display());
    let out = out.map_or_else(|| home.path("estate.json"), Path::to_path_buf);
    let started = std::time::Instant::now();
    let records = crate::scan::scan_root(&root, &home.config.scan.local_only)
        .with_context(|| format!("scanning {}", root.display()))?;
    std::fs::write(&out, serde_json::to_string_pretty(&records)?)?;
    let mut kinds: std::collections::BTreeMap<&str, usize> = Default::default();
    let (mut local_only, mut with_secret_flags, mut with_gates) = (0usize, 0usize, 0usize);
    for r in &records {
        if r["local_only"] == serde_json::json!(true) {
            local_only += 1;
        }
        if r["secret_flags"].as_array().is_some_and(|a| !a.is_empty()) {
            with_secret_flags += 1;
        }
        if r["candidate_gates"].as_array().is_some_and(|a| !a.is_empty()) {
            with_gates += 1;
        }
        *kinds.entry(r["kind"].as_str().unwrap_or("?")).or_default() += 1;
    }
    let mut text = format!(
        "scanned {} on {} in {:.1}s: {} record(s) -> {}\n",
        root.display(),
        crate::home::machine(),
        started.elapsed().as_secs_f64(),
        records.len(),
        out.display()
    );
    for (kind, n) in &kinds {
        writeln!(text, "  {kind}: {n}")?;
    }
    writeln!(
        text,
        "  local-only (not walked): {local_only}; with candidate gates: {with_gates}; with secret-looking file names: {with_secret_flags}"
    )?;
    Ok(text)
}

/// Where expert definitions are read from: the user's Claude Code agents.
pub fn expert_dirs() -> Vec<PathBuf> {
    if let Some(dir) = std::env::var_os("GROUNDWORKS_EXPERTS") {
        return vec![PathBuf::from(dir)];
    }
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(|home| vec![PathBuf::from(home).join(".claude").join("agents")])
        .unwrap_or_default()
}

/// Ingest one registered project: its files and last `max_commits` commits, organised by kind and
/// by the experts whose definitions cite its paths. Also refreshes the expert directory.
pub fn intake(home: &Home, store: &Store, project: &str, max_commits: usize) -> Result<String> {
    let registry = Registry::load(home)?;
    let project = registry.project(project)?;
    let dir = project.path(&home.workspace);
    ensure!(dir.is_dir(), "{} is not on this machine", dir.display());
    let experts = crate::intake::load_experts(&expert_dirs())?;
    store.upsert_experts(&experts)?;
    let started = std::time::Instant::now();
    let collected = crate::intake::collect(&dir, &experts, max_commits)?;
    let report = store.intake(&project.name, &collected)?;
    let stale_quotes = store.recheck_quotes(&dir, &project.name)?;
    let mut out = format!(
        "{}: {} files read into {} units ({} written, {} unchanged, {} marked gone) in {:.1}s on {}\n",
        project.name,
        collected.files_read,
        report.units,
        report.written,
        report.unchanged,
        report.gone,
        started.elapsed().as_secs_f64(),
        crate::home::machine()
    );
    if stale_quotes > 0 {
        writeln!(
            out,
            "  {stale_quotes} quoted span(s) no longer match their file: the facts citing them are now stale"
        )?;
    }
    let mut kinds: std::collections::BTreeMap<&str, usize> = Default::default();
    for u in &collected.units {
        *kinds.entry(u.kind).or_default() += 1;
    }
    writeln!(
        out,
        "  by kind: {}",
        kinds
            .iter()
            .map(|(k, n)| format!("{k} {n}"))
            .collect::<Vec<_>>()
            .join(", ")
    )?;
    if !collected.skipped.is_empty() {
        writeln!(
            out,
            "  skipped: {}",
            collected
                .skipped
                .iter()
                .map(|(k, n)| format!("{n} {k}"))
                .collect::<Vec<_>>()
                .join(", ")
        )?;
    }
    let mut by_expert: std::collections::BTreeMap<&str, usize> = Default::default();
    for a in &collected.areas {
        *by_expert.entry(a.expert.as_str()).or_default() += 1;
    }
    if by_expert.is_empty() {
        out.push_str("  experts: none of the expert definitions cite a path in this project\n");
    } else {
        writeln!(
            out,
            "  experts whose definitions cite paths here: {}",
            by_expert
                .iter()
                .map(|(e, n)| format!("{e} ({n})"))
                .collect::<Vec<_>>()
                .join(", ")
        )?;
    }
    if let Some(head) = &collected.head {
        writeln!(
            out,
            "  at commit {}{}",
            &head[..head.len().min(12)],
            if collected.dirty == Some(true) {
                " (with uncommitted changes)"
            } else {
                ""
            }
        )?;
    }
    Ok(out)
}

/// Search units. With `project`, that project only (private ones too, never an isolated one).
/// Without it, every registered project whose level is shared, saying which were skipped and why.
pub fn search(
    home: &Home,
    store: &Store,
    query: &str,
    project: Option<&str>,
    expert: Option<&str>,
    kinds: &[String],
    limit: usize,
) -> Result<String> {
    let registry = Registry::load(home)?;
    let limit = limit.clamp(1, 50);
    let mut hits = Vec::new();
    let mut searched = Vec::new();
    let mut skipped = Vec::new();
    let names: Vec<String> = match project {
        Some(p) => vec![registry.project(p)?.name.clone()],
        None => registry.projects.keys().cloned().collect(),
    };
    for name in &names {
        let ns = gw_store::project_ns(&store::slug(name)?)?;
        if project.is_none() {
            let level = store.level(&ns)?;
            if level != "shared" {
                skipped.push(format!("{name} ({level})"));
                continue;
            }
        }
        match store.search(name, query, expert, kinds, limit) {
            Ok(found) => {
                searched.push(name.clone());
                hits.extend(found);
            }
            Err(e) => skipped.push(format!("{name} ({e:#})")),
        }
    }
    hits.sort_by(|a, b| b.score.unwrap_or(0.0).total_cmp(&a.score.unwrap_or(0.0)));
    hits.truncate(limit);
    let mut out = String::new();
    if hits.is_empty() {
        writeln!(out, "no units match {query:?}")?;
    }
    for h in &hits {
        let experts = if h.experts.is_empty() {
            String::new()
        } else {
            format!(" experts: {}", h.experts.join(", "))
        };
        let commit = h
            .commit
            .as_deref()
            .map_or(String::new(), |c| format!(" @ {}", &c[..c.len().min(10)]));
        writeln!(
            out,
            "- [{}] {}:{}{commit}{experts}\n    {}",
            h.kind.as_deref().unwrap_or("?"),
            h.project,
            h.locator,
            h.text
        )?;
    }
    writeln!(
        out,
        "\nsearched: {}{}",
        if searched.is_empty() {
            "nothing".to_owned()
        } else {
            searched.join(", ")
        },
        if skipped.is_empty() {
            String::new()
        } else {
            format!("; skipped: {}", skipped.join(", "))
        }
    )?;
    Ok(out)
}

/// The expert directory: every expert with its summary.
pub fn experts() -> Result<String> {
    let experts = crate::intake::load_experts(&expert_dirs())?;
    Ok(experts
        .iter()
        .map(|e| format!("{}: {}\n", e.name, e.summary.chars().take(160).collect::<String>()))
        .collect())
}
