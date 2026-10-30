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

pub fn show(
    home: &Home,
    store: Option<&Store>,
    name: &str,
    journal_limit: usize,
    since: Option<&str>,
) -> Result<String> {
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
    match since {
        Some(since) => writeln!(out, "\n## Journal since {since}")?,
        None => out.push_str("\n## Journal\n"),
    }
    match store.map(|s| s.facts_query(name, None, since, journal_limit)) {
        None => out.push_str("(store unavailable: the journal could not be read)\n"),
        Some(Err(e)) => writeln!(out, "(store unavailable: {e:#})")?,
        Some(Ok(facts)) if facts.is_empty() && since.is_some() => out.push_str("(nothing recorded since then)\n"),
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
        related: Vec::new(),
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
    /// Where the write came from (`note`, `journal_add`), named on a rejection row when the store refuses it.
    pub surface: &'a str,
    pub project: &'a str,
    pub kind: Kind,
    pub text: &'a str,
    pub source: &'a str,
    pub confidence: Confidence,
    pub by: &'a str,
    pub receipt: Option<&'a str>,
    pub owner_statement: Option<&'a str>,
    pub quote: Option<Quote>,
    pub lens: Option<&'a str>,
    pub attacks: Option<&'a str>,
    pub stance: Option<&'a str>,
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
        lens: note.lens.map(str::to_owned),
        attacks: note.attacks.map(str::to_owned),
        stance: note.stance.map(str::to_owned),
    };
    let dir = project.path(&home.workspace);
    let written = match note.owner_pass {
        Some(pass) => store.add_fact_as_owner(&dir, &project.name, &fact, pass),
        None => store.add_fact(&dir, &project.name, &fact),
    };
    let id = match written {
        Ok(id) => id,
        Err(e) => {
            // The refusal is counted (pulse: store rejections) as a code: never the text.
            record_refusal(store, note.surface, &project.name, note.by, &e);
            return Err(e);
        }
    };
    let recorded = store
        .facts(&project.name, None, 1)?
        .pop()
        .map_or_else(|| id.clone(), |f| f.render());
    Ok(
        match store::confirm_cap(&project.name, &fact, note.owner_pass.is_some()) {
            Some(why) => format!(
                "recorded {id} as INFERRED, not CONFIRMED: {why}. Quote tracked code, config or docs, cite a receipt, or \
             have the owner sign it (groundworks note --as-owner) to confirm.\n{recorded}"
            ),
            None => format!("recorded {id}: {recorded}"),
        },
    )
}

/// Count a refused write as a rejection row: the pattern id, field, offset and length for a
/// secret-shaped refusal, else the store rule's code. Never the text. A failure to record is printed
/// and swallowed: the caller's refusal is the result that matters.
pub fn record_refusal(store: &Store, surface: &str, project: &str, by: &str, e: &anyhow::Error) {
    let row = match e.downcast_ref::<crate::refusal::Refusal>() {
        Some(r) => store::RejectionIn {
            surface,
            project: Some(project),
            by: Some(by),
            reason: r.id,
            field: Some(r.field),
            offset: Some(r.offset as u64),
            match_len: Some(r.len as u64),
        },
        None => store::RejectionIn {
            surface,
            project: Some(project),
            by: Some(by),
            reason: rejection_code(&format!("{e:#}")),
            ..store::RejectionIn::default()
        },
    };
    if store.record_rejection(&row).is_err() {
        eprintln!("rejection not recorded ({surface})");
    }
}

/// The store rule a refusal came from, as the code a rejection row keeps; nothing of what was refused.
pub fn rejection_code(error: &str) -> &'static str {
    if error.starts_with("CONFIRMED needs evidence") {
        "confirmed-needs-evidence"
    } else if error.starts_with("quote not found word for word") {
        "quote-not-found"
    } else if error.contains(" is isolated") {
        "isolated-project"
    } else if error.contains("needs stance") || error.contains("needs attacks") {
        "attack-needs-stance"
    } else if error.contains("is not a fact id") || error.contains("not a receipt id") {
        "bad-reference"
    } else if error.starts_with("a fact needs text") || error.starts_with("a quote needs text") {
        "empty-text"
    } else {
        "other"
    }
}

/// What the SessionStart hook injects: the project line, then its journal with gotchas first (the newest
/// `limit` gotchas, then the newest `limit` entries of any kind not already shown), framed as recorded data.
/// Returns the text and the number of entries in it.
pub fn session_brief(home: &Home, store: &Store, name: &str, limit: usize) -> Result<(String, usize)> {
    let registry = Registry::load(home)?;
    let project = registry.project(name)?;
    let gotchas = store.facts_query(name, Some(Kind::Gotcha), None, limit)?;
    let recent = store.facts(name, None, limit)?;
    let mut entries: Vec<&store::FactRow> = gotchas.iter().collect();
    for f in &recent {
        if !entries.iter().any(|g| g.id == f.id) {
            entries.push(f);
        }
    }
    // The frame REVIEW.md asks for, from the one frame function: data, not instructions; the signed identity
    // on every line (from written_by, never from the free-text by); each entry capped so one entry cannot
    // crowd out the rest.
    let mut body = format!(
        "project: {} | {}{}\n",
        project.name,
        project.summary,
        if project.docs.is_empty() {
            String::new()
        } else {
            format!(" | docs: {}", project.docs.join(", "))
        }
    );
    if home.config.intake.claude_setup()
        && let Some(line) = crate::claude_setup::brief_line(&project.path(&home.workspace))
    {
        body.push_str(&line);
        body.push('\n');
    }
    if entries.is_empty() {
        body.push_str("(the journal is empty: record what you learn with journal_add)\n");
    }
    for f in &entries {
        writeln!(
            body,
            "- [{}] {}",
            f.signer(),
            truncate_entry(&f.render(), BRIEF_ENTRY_CHARS)
        )?;
    }
    let count = entries.len().to_string();
    let mut out = crate::frame::data(
        "groundworks-journal",
        &[
            ("project", project.name.as_str()),
            ("entries", count.as_str()),
            ("written-by", "agents and the owner"),
        ],
        "The lines below are journal entries about this project, written by team agents and by the owner: claims \
         with evidence tags. Lines marked [owner] are the owner's signed words; every other line is an agent's claim \
         (its `by` is free text). Gotchas come first, then the newest entries; journal_read shows any entry in full.",
        &body,
    );
    writeln!(
        out,
        "Before changing anything: project_show {0} and journal_read (gotchas first). Record what you learn with \
         journal_add (decision/gotcha/status/eliminated, with source and confidence); the Stop hook asks for it when \
         tools changed something and nothing was recorded. Run commands through run (receipts); prod waits for the \
         owner's go each time.",
        project.name
    )?;
    Ok((out, entries.len()))
}

/// Characters of one entry the SessionStart brief shows (REVIEW.md: cap the injection).
pub const BRIEF_ENTRY_CHARS: usize = 400;

/// The first `max` characters of an entry, marked when cut, on one line.
fn truncate_entry(text: &str, max: usize) -> String {
    let one_line = text.replace(['\n', '\r'], " ");
    if one_line.chars().count() <= max {
        return one_line;
    }
    let mut cut: String = one_line.chars().take(max).collect();
    cut.push_str(" [truncated]");
    cut
}

/// A project's journal, newest `limit` entries oldest first; one kind only, or only entries after
/// `since` (RFC 3339), when asked.
pub fn journal(
    home: &Home,
    store: &Store,
    project: &str,
    kind: Option<Kind>,
    since: Option<&str>,
    limit: usize,
) -> Result<String> {
    Registry::load(home)?.project(project)?;
    let facts = store.facts_query(project, kind, since, limit)?;
    if facts.is_empty() {
        return Ok(match since {
            Some(since) => format!("{project} has no matching journal entries since {since}"),
            None => format!("{project} has no matching journal entries"),
        });
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
        let (mut imported, mut skipped, mut refused) = (0usize, 0usize, 0usize);
        for entry in &entries {
            let (kind, mut text) = entry.kind_and_text();
            let owner = entry.source.trim_start().to_lowercase().starts_with("owner");
            let confidence = match entry.confidence.as_str() {
                // Schema 0017: only the owner's own login confirms an owner statement; the loader is root.
                "CONFIRMED" if owner => {
                    text.push_str(
                        " [was CONFIRMED as an owner statement in the old file journal; only the owner's own login \
                         confirms one now]",
                    );
                    Confidence::Inferred
                }
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
                lens: None,
                attacks: None,
                stance: None,
            };
            match store.import_fact(
                &project.path(&home.workspace),
                &project.name,
                &fact,
                &entry.ts,
                Some(entry.machine.as_str()),
            ) {
                Ok(Some(_)) => imported += 1,
                Ok(None) => skipped += 1,
                // An old entry that looks like it holds a secret stays in the file and is counted.
                Err(e) if e.downcast_ref::<crate::refusal::Refusal>().is_some() => {
                    record_refusal(store, "import_journals", &project.name, &entry.by, &e);
                    refused += 1;
                }
                Err(e) => return Err(e),
            }
        }
        let done = path.with_extension("jsonl.imported");
        std::fs::rename(&path, &done)?;
        writeln!(
            out,
            "{}: {imported} imported, {skipped} already there, {refused} refused as secret-shaped (an import can be \
             re-run safely); {} renamed",
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
        "scaffolded {} from {} at template commit {} ({} files):\n",
        target.display(),
        stack.name,
        scaffold::template_version(&target).unwrap_or_else(|| "?".to_owned()),
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
    writeln!(
        out,
        "later: `groundworks upgrade {name}` brings template changes into the project (commit it first)"
    )?;
    Ok(out)
}

/// Bring later template changes into a project scaffolded by `bootstrap`.
pub fn upgrade(home: &Home, project: &str) -> Result<String> {
    let registry = Registry::load(home)?;
    let dir = registry.project(project)?.path(&home.workspace);
    scaffold::upgrade(&dir)
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
    for line in environment_checks(&claude_dir(), vscode_mcp_path().as_deref()) {
        if line.starts_with("FAIL") {
            fails += 1;
        } else if line.starts_with("warn") {
            warns += 1;
        }
        out.push_str(&line);
        out.push('\n');
    }
    let (line, machine) = machine_check(crate::home::machine_known());
    if line.starts_with("FAIL") {
        fails += 1;
    }
    out.push_str(&line);
    writeln!(out, "\n{fails} failure(s), {warns} warning(s) on {machine}")?;
    Ok((out, fails == 0))
}

/// Claude Code's user directory: `GROUNDWORKS_CLAUDE_DIR` (tests point it at a fixture), else `~/.claude`.
pub fn claude_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("GROUNDWORKS_CLAUDE_DIR") {
        return PathBuf::from(dir);
    }
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(|p| PathBuf::from(p).join(".claude"))
        .unwrap_or_else(|| PathBuf::from(".claude"))
}

/// VS Code's user mcp.json: `GROUNDWORKS_VSCODE_MCP`, else `%APPDATA%/Code/User/mcp.json` (None when
/// neither names a location, so the check says it could not look rather than passing).
pub fn vscode_mcp_path() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("GROUNDWORKS_VSCODE_MCP") {
        return Some(PathBuf::from(p));
    }
    std::env::var_os("APPDATA").map(|a| PathBuf::from(a).join("Code").join("User").join("mcp.json"))
}

/// The enforcement's own health (decision 1itizcerhaj5x1mjfyj2, layer 4), as doctor lines: FAIL when the
/// installed plugin has no hooks.json, or any agent lacks the stamp block; warn when VS Code's mcp.json
/// exists without a groundworks server, or when Claude Code is not installed here at all.
pub fn environment_checks(claude: &Path, vscode_mcp: Option<&Path>) -> Vec<String> {
    let mut lines = Vec::new();
    if !claude.is_dir() {
        lines.push(format!(
            "warn claude: {} is not here (Claude Code not installed; hooks cannot run)",
            claude.display()
        ));
    } else {
        let cache = claude
            .join("plugins")
            .join("cache")
            .join("groundworks")
            .join("groundworks");
        let mut versions: Vec<PathBuf> = std::fs::read_dir(&cache)
            .map(|rd| {
                rd.filter_map(Result::ok)
                    .map(|e| e.path())
                    .filter(|p| p.is_dir())
                    .collect()
            })
            .unwrap_or_default();
        versions.sort_by_key(|p| version_key(&p.file_name().unwrap_or_default().to_string_lossy()));
        match versions.last() {
            None => lines.push(format!(
                "FAIL plugin: groundworks is not installed under {} (claude plugin install groundworks@groundworks)",
                cache.display()
            )),
            Some(newest) => {
                let problems = plugin_manifest_problems(newest);
                if problems.is_empty() {
                    lines.push(format!(
                        "ok   manifest: {} lists its agents as .md files",
                        newest.join(".claude-plugin").join("plugin.json").display()
                    ));
                } else {
                    lines.push(format!(
                        "FAIL manifest: {}: {} (claude plugin update groundworks@groundworks)",
                        newest.display(),
                        problems.join("; ")
                    ));
                }
                let hooks = newest.join("hooks").join("hooks.json");
                let rules = newest.join("hooks").join("deny.toml");
                if hooks.is_file() && rules.is_file() {
                    lines.push(format!(
                        "ok   plugin: {} has hooks/hooks.json and hooks/deny.toml",
                        newest.display()
                    ));
                } else {
                    lines.push(format!(
                        "FAIL plugin: {} lacks {} (an older plugin: claude plugin update groundworks@groundworks)",
                        newest.display(),
                        if hooks.is_file() {
                            "hooks/deny.toml"
                        } else {
                            "hooks/hooks.json"
                        }
                    ));
                }
            }
        }
        let agents = claude.join("agents");
        match agent_files(&agents) {
            Err(_) => lines.push(format!("warn agents: no {} (nothing to stamp)", agents.display())),
            Ok(files) if files.is_empty() => lines.push(format!("warn agents: {} holds no agent", agents.display())),
            Ok(files) => {
                let drifting: Vec<String> = files
                    .iter()
                    .filter(|p| {
                        std::fs::read_to_string(p)
                            .ok()
                            .is_none_or(|t| stamp_drift(&t).is_some())
                    })
                    .map(|p| p.file_name().unwrap_or_default().to_string_lossy().into_owned())
                    .collect();
                if drifting.is_empty() {
                    lines.push(format!("ok   agents: all {} carry the Groundworks block", files.len()));
                } else {
                    let shown: Vec<&str> = drifting.iter().take(3).map(String::as_str).collect();
                    lines.push(format!(
                        "FAIL agents: {} of {} lack the Groundworks block or drift from it ({}{}): groundworks install-team --stamp",
                        drifting.len(),
                        files.len(),
                        shown.join(", "),
                        if drifting.len() > 3 { ", ..." } else { "" }
                    ));
                }
            }
        }
    }
    match vscode_mcp {
        None => lines.push("warn vscode: no APPDATA, so mcp.json could not be looked for".to_owned()),
        Some(path) if !path.is_file() => {
            lines.push(format!(
                "ok   vscode: no {} (VS Code MCP not configured here)",
                path.display()
            ));
        }
        Some(path) => {
            let has = std::fs::read_to_string(path)
                .ok()
                .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
                .is_some_and(|v| v["servers"]["groundworks"].is_object() || v["mcpServers"]["groundworks"].is_object());
            if has {
                lines.push(format!("ok   vscode: {} has a groundworks server", path.display()));
            } else {
                lines.push(format!(
                    "warn vscode: {} has no groundworks server (docs/VSCODE-MCP.md); Copilot sessions there are blind",
                    path.display()
                ));
            }
        }
    }
    lines
}

/// What is wrong with the plugin manifest under `plugin_root` (`.claude-plugin/plugin.json`), one line
/// each; empty means every Claude Code we know of loads it. Measured 2026-10-29 (Phase 1 of the window
/// plan): Claude Code 2.1.300 rejects `"agents": "./agents/"` ("must end with .md") and drops the WHOLE
/// plugin without a word (no hooks, no MCP server), while 2.1.312 accepts it. So `agents` must be a
/// `.md` path or a list of them, each present, and every `agents/*.md` on disk must be listed.
pub fn plugin_manifest_problems(plugin_root: &Path) -> Vec<String> {
    let path = plugin_root.join(".claude-plugin").join("plugin.json");
    let Ok(text) = std::fs::read_to_string(&path) else {
        return vec!["no .claude-plugin/plugin.json".to_owned()];
    };
    let manifest: serde_json::Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => return vec![format!("plugin.json does not parse ({e})")],
    };
    let mut problems = Vec::new();
    let listed: Vec<&str> = match &manifest["agents"] {
        serde_json::Value::Null => Vec::new(),
        serde_json::Value::String(one) => vec![one.as_str()],
        serde_json::Value::Array(items) => items.iter().map(|v| v.as_str().unwrap_or("")).collect(),
        other => {
            problems.push(format!("agents is {other}, not a .md path or a list of them"));
            Vec::new()
        }
    };
    for entry in &listed {
        if !entry.ends_with(".md") {
            problems.push(format!(
                "agents entry {entry:?} is not a .md file (the directory form is rejected by Claude Code 2.1.300, which then drops the whole plugin)"
            ));
        } else if !plugin_root.join(entry).is_file() {
            problems.push(format!("agents entry {entry:?} is not in the plugin"));
        }
    }
    let on_disk = agent_files(&plugin_root.join("agents")).unwrap_or_default();
    for file in on_disk {
        let rel = format!("./agents/{}", file.file_name().unwrap_or_default().to_string_lossy());
        if !listed
            .iter()
            .any(|l| l.trim_start_matches("./") == rel.trim_start_matches("./"))
        {
            problems.push(format!(
                "{rel} is in the plugin and not listed under agents (it would not load)"
            ));
        }
    }
    if let Some(hooks) = manifest["hooks"].as_str()
        && !plugin_root.join(hooks).is_file()
    {
        problems.push(format!("hooks {hooks:?} is not in the plugin"));
    }
    problems
}

/// `0.10.2` sorts after `0.9.0`: numeric fields, then the text as a tie-break.
fn version_key(name: &str) -> (Vec<u64>, String) {
    (
        name.split(|c: char| !c.is_ascii_digit())
            .filter(|s| !s.is_empty())
            .filter_map(|s| s.parse().ok())
            .collect(),
        name.to_owned(),
    )
}

/// Doctor's machine line and the name the summary uses. A box that cannot name itself is a failure:
/// every receipt and fact it wrote would carry "unknown", and a result without its machine is not a result.
fn machine_check(known: Option<String>) -> (String, String) {
    match known {
        Some(name) => (format!("ok   machine: {name}\n"), name),
        None => (
            format!(
                "FAIL machine: no name (set GROUNDWORKS_MACHINE, COMPUTERNAME or HOSTNAME, or install `hostname`); \
                 every receipt and fact from here would say \"{}\"\n",
                crate::home::UNKNOWN_MACHINE
            ),
            crate::home::UNKNOWN_MACHINE.to_owned(),
        ),
    }
}

/// The one shared Groundworks block every agent definition carries (decision b312bl7vjqjoeofbs2sc (b),
/// owner go cgilrpv6ynkwmvu3u929): one source, stamped by `install-team --stamp`, checked by `--check`.
pub const STAMP_BEGIN: &str = "<!-- groundworks:begin -->";
pub const STAMP_END: &str = "<!-- groundworks:end -->";
pub const STAMP_BLOCK: &str = "<!-- groundworks:begin -->\n\
## Groundworks (stamped by `groundworks install-team --stamp`; do not edit by hand)\n\
Groundworks is ON for memory. Before answering about a project, call `project_show` and read its journal (gotchas first).\n\
Record what you learn with `journal_add` (decision / gotcha / status / eliminated), with source and confidence; cite `file:line` or say UNKNOWN.\n\
Run commands through `run` (receipts); prod waits for the owner's go each time.\n\
Board / tickets / hindsight / fleetview / orchestrate stay OFF; one agent does the work.\n\
<!-- groundworks:end -->";

/// What replaces the owner's 2026-09-22 sentence; the rest of its paragraph (the 2026-10-18 re_kb and
/// Ghidra update) stays as written.
pub const ON_SENTENCE: &str = "Groundworks is ON for memory (see the Groundworks block in this file); board / tickets / \
                               hindsight / fleetview stay OFF, as do the `orchestrate` / `ticket` skills.";

fn off_sentence() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        regex::Regex::new(
            r"The orchestration methods are OFF: no groundworks / board / tickets / hindsight / fleetview,\s+no `orchestrate` /\s+`ticket` skills\.",
        )
        .expect("the OFF sentence regex")
    })
}

/// `text` with the block inserted or replaced and the OFF sentence replaced; None when nothing changes.
pub fn stamp_text(text: &str) -> Option<String> {
    let mut out = off_sentence().replace_all(text, ON_SENTENCE).into_owned();
    out = match (out.find(STAMP_BEGIN), out.find(STAMP_END)) {
        (Some(a), Some(b)) if b >= a => {
            let end = b + STAMP_END.len();
            format!("{}{}{}", &out[..a], STAMP_BLOCK, &out[end..])
        }
        _ => {
            let mut s = out.trim_end_matches(['\n', '\r']).to_owned();
            s.push_str("\n\n");
            s.push_str(STAMP_BLOCK);
            s.push('\n');
            s
        }
    };
    (out != text).then_some(out)
}

/// Why an agent file drifts from the stamp, or None when it carries the block exactly and no OFF sentence.
pub fn stamp_drift(text: &str) -> Option<&'static str> {
    if off_sentence().is_match(text) {
        return Some("still carries the 2026-09-22 OFF sentence");
    }
    match (text.find(STAMP_BEGIN), text.find(STAMP_END)) {
        (None, _) | (_, None) => Some("no Groundworks block"),
        (Some(a), Some(b)) if b < a => Some("block markers out of order"),
        (Some(a), Some(b)) => {
            (&text[a..b + STAMP_END.len()] != STAMP_BLOCK).then_some("block differs from the template")
        }
    }
}

/// `install-team` options. With neither flag it copies `team/gw-*.md` as before.
#[derive(Debug, Default, Clone)]
pub struct InstallTeam {
    /// Insert or refresh the shared block in every `*.md` of the agents dir, replacing the OFF sentence.
    pub stamp: bool,
    /// Report every agent whose block differs from the template; the result is red when any does.
    pub check: bool,
    /// The agents directory (default `~/.claude/agents`).
    pub agents_dir: Option<PathBuf>,
}

fn agents_dir(opts: &InstallTeam) -> Result<PathBuf> {
    if let Some(dir) = &opts.agents_dir {
        return Ok(dir.clone());
    }
    let profile = std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .context("neither USERPROFILE nor HOME is set")?;
    Ok(PathBuf::from(profile).join(".claude").join("agents"))
}

fn agent_files(dir: &Path) -> Result<Vec<PathBuf>> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(dir)
        .with_context(|| format!("reading {}", dir.display()))?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.is_file() && p.extension().is_some_and(|e| e == "md"))
        .collect();
    files.sort();
    Ok(files)
}

/// Copies `team/gw-*.md` into the agents dir (only files with that prefix are ever written there by the
/// copy), then `--stamp` and/or `--check` over every `*.md` in it. Returns the report and whether it is
/// green (`--check` is red on any drift; `--stamp` is red when a file could not be written).
pub fn install_team(home: &Home, opts: &InstallTeam) -> Result<(String, bool)> {
    let target = agents_dir(opts)?;
    std::fs::create_dir_all(&target)?;
    let mut out = String::new();
    let mut ok = true;
    if !opts.check || opts.stamp {
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
    }
    if opts.stamp {
        let (mut stamped, mut unchanged, mut sentences) = (0usize, 0usize, 0usize);
        for path in agent_files(&target)? {
            let text = std::fs::read_to_string(&path).with_context(|| format!("reading {}", path.display()))?;
            let had_sentence = off_sentence().is_match(&text);
            match stamp_text(&text) {
                Some(new) => {
                    std::fs::write(&path, new).with_context(|| format!("writing {}", path.display()))?;
                    stamped += 1;
                    sentences += usize::from(had_sentence);
                    writeln!(
                        out,
                        "stamped {}{}",
                        path.display(),
                        if had_sentence { " (OFF sentence replaced)" } else { "" }
                    )?;
                }
                None => unchanged += 1,
            }
        }
        writeln!(
            out,
            "stamp: {stamped} file(s) written ({sentences} OFF sentence(s) replaced), {unchanged} already current, in {}",
            target.display()
        )?;
    }
    if opts.check {
        let mut drift = 0usize;
        let files = agent_files(&target)?;
        for path in &files {
            let text = std::fs::read_to_string(path).with_context(|| format!("reading {}", path.display()))?;
            match stamp_drift(&text) {
                None => writeln!(out, "ok    {}", path.display())?,
                Some(why) => {
                    drift += 1;
                    writeln!(out, "DRIFT {}: {why}", path.display())?;
                }
            }
        }
        if files.is_empty() {
            drift += 1;
            writeln!(out, "DRIFT {}: no agent files (nothing checked)", target.display())?;
        }
        writeln!(
            out,
            "check: {drift} of {} agent(s) drift from the Groundworks block{}",
            files.len(),
            if drift > 0 { " (run install-team --stamp)" } else { "" }
        )?;
        ok = drift == 0;
    }
    Ok((out, ok))
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

/// The experts visible in `project` (init design S1): its own `.claude/agents/` (`project:<name>`, unless
/// `[intake] claude_setup = false`), then every global expert it does not override by name, as
/// [`crate::claude_setup::merge`] decides; with the project's setup report (disabled folders, reserved
/// names). With no project, the global experts only, so a project's own expert is visible in its project
/// and in no other.
pub fn visible_experts(
    home: &Home,
    project: Option<&str>,
) -> Result<(Vec<crate::intake::Expert>, crate::claude_setup::Local)> {
    let mut global = crate::intake::load_experts(&expert_dirs())?;
    let Some(name) = project else {
        global.sort_by(|a, b| a.name.cmp(&b.name));
        return Ok((global, Default::default()));
    };
    let registry = Registry::load(home)?;
    let project = registry.project(name)?;
    let dir = project.path(&home.workspace);
    let local = if home.config.intake.claude_setup() && dir.is_dir() {
        crate::claude_setup::project_experts(&dir, &project.name)?
    } else {
        Default::default()
    };
    Ok((crate::claude_setup::merge(&global, &local.experts), local))
}

/// Ingest one registered project: its files and last `max_commits` commits, organised by kind and
/// by the experts whose definitions cite its paths. Also refreshes the expert directory.
pub fn intake(home: &Home, store: &Store, project: &str, max_commits: usize) -> Result<String> {
    let registry = Registry::load(home)?;
    let project = registry.project(project)?;
    let dir = project.path(&home.workspace);
    ensure!(dir.is_dir(), "{} is not on this machine", dir.display());
    let global = crate::intake::load_experts(&expert_dirs())?;
    let setup_on = home.config.intake.claude_setup();
    let setup = if setup_on {
        crate::claude_setup::project_experts(&dir, &project.name)?
    } else {
        Default::default()
    };
    let local = &setup.experts;
    store.upsert_experts(&global)?;
    store.upsert_experts(local)?;
    let experts = crate::claude_setup::merge(&global, local);
    let started = std::time::Instant::now();
    let claude = claude_dir();
    let collected = crate::intake::collect(&dir, &experts, max_commits, setup_on.then_some(claude.as_path()))?;
    let report = store.intake(&project.name, &collected)?;
    let stale_quotes = store.recheck_quotes(&dir, &project.name, &claude)?;
    let mut out = format!(
        "{}: {} files read ({} untracked docs) into {} units ({} written, {} unchanged, {} marked gone) in {:.1}s on {}\n",
        project.name,
        collected.files_read,
        collected.untracked_read,
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
    if !collected.refused.is_empty() {
        writeln!(
            out,
            "  secret-shaped units skipped, never stored, by pattern: {}",
            collected
                .refused_by_pattern()
                .iter()
                .map(|(id, n)| format!("{id} {n}"))
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
                .map(|(e, n)| if local.iter().any(|l| l.name == *e) {
                    format!("{e} [project] ({n})")
                } else {
                    format!("{e} ({n})")
                })
                .collect::<Vec<_>>()
                .join(", ")
        )?;
    }
    out.push_str(&setup_summary(&collected.setup, &setup));
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

/// The intake summary's line on the project's own Claude Code setup.
fn setup_summary(r: &crate::intake::SetupReport, setup: &crate::claude_setup::Local) -> String {
    if !r.on {
        return "  claude setup: off ([intake] claude_setup = false)\n".to_owned();
    }
    let (local, disabled) = (&setup.experts, &setup.disabled);
    let names: Vec<&str> = local.iter().map(|e| e.name.as_str()).collect();
    let experts = if names.is_empty() {
        "0 loaded".to_owned()
    } else {
        format!("{} loaded ({})", names.len(), names.join(", "))
    };
    let disabled = if disabled.is_empty() {
        "0 disabled folders".to_owned()
    } else {
        format!(
            "{} disabled, not loaded ({})",
            disabled.iter().map(|d| d.definitions).sum::<usize>(),
            disabled
                .iter()
                .map(|d| format!("{}: {}", d.folder, d.definitions))
                .collect::<Vec<_>>()
                .join(", ")
        )
    };
    // A project agent that took a team name (gw-*, groundworks) is named, never loaded.
    let disabled = if setup.reserved.is_empty() {
        disabled
    } else {
        format!(
            "{disabled}, {} reserved team name(s) not loaded ({})",
            setup.reserved.len(),
            setup
                .reserved
                .iter()
                .map(|r| format!("{} in {}", r.name, r.file))
                .collect::<Vec<_>>()
                .join(", ")
        )
    };
    let newest = r
        .newest_session
        .as_deref()
        .map_or(String::new(), |d| format!(", newest {}", &d[..d.len().min(10)]));
    format!(
        "  claude setup: project experts {experts}, {disabled}; skills {}; commands {}; instruction files {} ({} disabled); \
         {} setup files read from outside git, {} refused as secret-shaped (tracked or not); memory files {} stored, {} skipped for secrets; \
         sessions {} counted{newest} (content not read)\n",
        r.skills,
        r.commands,
        r.instructions,
        r.instructions_disabled,
        r.off_git,
        r.setup_refused,
        r.memory_stored,
        r.memory_refused,
        r.sessions,
    )
}

/// Search units: excerpts of the best hits, with where each is, and what the search could reach.
pub fn search(home: &Home, store: &Store, q: crate::ask::Query<'_>) -> Result<String> {
    let q = crate::ask::Query {
        limit: q.limit.clamp(1, 50),
        ..q
    };
    let mut found = crate::ask::fan_out(home, store, q)?;
    let mut out = String::new();
    if found.hits.is_empty() && found.facts.is_empty() {
        writeln!(out, "no units match {:?}", q.text)?;
    }
    for h in &found.facts {
        writeln!(
            out,
            "- RECORDED FACT ({}): {}\n    evidence {}:{}",
            h.fact_grade.as_deref().unwrap_or("?"),
            h.fact.as_deref().unwrap_or_default(),
            h.project,
            h.locator
        )?;
    }
    for h in &mut found.hits {
        let marked = h.marked.clone().unwrap_or_else(|| h.text.clone());
        // The file line of the first match, so the hit can be cited as path:line as it stands.
        if let (Some(start), Some(at)) = (store::first_line(&h.locator), marked.find('\u{ab}')) {
            let line = start + marked[..at].matches('\n').count();
            h.locator.push_str(&format!(" (match at line {line})"));
        }
        h.text = store::excerpt(&marked);
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
    writeln!(out, "\n{}", crate::ask::reach(&found))?;
    Ok(out)
}

/// Grep over the ingested files of a project (and its related projects), or of every shared project:
/// exact, case-insensitive, `/re/` for a regex. Each hit names the file line that matched, dated.
pub fn grep(home: &Home, store: &Store, project: Option<&str>, needle: &str, limit: usize) -> Result<String> {
    let needle = needle.trim();
    ensure!(!needle.is_empty(), "grep for something");
    let limit = limit.clamp(1, 100);
    let regex = needle
        .strip_prefix('/')
        .and_then(|r| r.strip_suffix('/'))
        .filter(|r| !r.is_empty())
        .map(|r| regex::RegexBuilder::new(r).case_insensitive(false).build())
        .transpose()
        .context("the regex")?;
    let lower = needle.to_lowercase();
    let matches = |line: &str| match &regex {
        Some(re) => re.is_match(line),
        None => line.to_lowercase().contains(&lower),
    };
    let (names, mut skipped) = crate::ask::reach_names(home, store, project)?;
    let mut out = String::new();
    let mut shown = 0;
    let mut searched = Vec::new();
    for name in &names {
        let hits = match store.grep(name, needle, limit) {
            Ok(h) => h,
            Err(e) => {
                skipped.push(format!("{name} ({e:#})"));
                continue;
            }
        };
        searched.push(name.clone());
        for h in hits {
            if shown >= limit {
                break;
            }
            let start = store::first_line(&h.locator);
            let at = h.text.lines().position(&matches);
            let (where_, line) = match (start, at) {
                (Some(s), Some(i)) => (
                    format!(
                        "{}:{} (in #L{})",
                        h.locator.rsplit_once("#L").map_or("", |x| x.0),
                        s + i,
                        h.locator.rsplit_once("#L").map_or("", |x| x.1)
                    ),
                    h.text.lines().nth(i).unwrap_or_default().trim().to_owned(),
                ),
                _ => (h.locator.clone(), store::excerpt(&h.text)),
            };
            let changed = h
                .changed_at
                .as_deref()
                .map_or(String::new(), |d| format!("; changed {}", &d[..d.len().min(10)]));
            writeln!(
                out,
                "- [{}] {}:{where_}{changed}\n    {line}",
                h.kind.as_deref().unwrap_or("?"),
                h.project
            )?;
            shown += 1;
        }
    }
    if shown == 0 {
        writeln!(out, "no ingested unit contains {needle:?}")?;
    } else if shown >= limit {
        writeln!(out, "(cut at {limit}; narrow the pattern or name a project)")?;
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

/// The expert directory: one line per expert, `<name> [<source>]: <summary, at most 160 characters>`, the
/// source being `global` or `project:<name>`. With a project, the experts visible there
/// ([`visible_experts`]): its own, which win a name, then the global ones; then one line naming any of
/// its agents that took a reserved team name, which are not loaded.
pub fn experts(home: &Home, project: Option<&str>) -> Result<String> {
    let (experts, local) = visible_experts(home, project)?;
    let mut out: String = experts
        .iter()
        .map(|e| {
            format!(
                "{} [{}]: {}\n",
                e.name,
                e.source,
                e.summary.chars().take(160).collect::<String>()
            )
        })
        .collect();
    if let (Some(project), false) = (project, local.reserved.is_empty()) {
        writeln!(
            out,
            "reserved team names in {project}'s .claude/agents, not loaded (the team's definition stands): {}",
            local
                .reserved
                .iter()
                .map(|r| format!("{} ({})", r.name, r.file))
                .collect::<Vec<_>>()
                .join(", ")
        )?;
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_rejection_keeps_a_code_not_the_text() {
        assert_eq!(
            rejection_code("CONFIRMED needs evidence: a receipt from run or gate, a quote found word for word"),
            "confirmed-needs-evidence"
        );
        assert_eq!(
            rejection_code("quote not found word for word at notes.txt:2; the file says: \"secret line\""),
            "quote-not-found"
        );
        assert_eq!(
            rejection_code("proj_secret is isolated: it has a store of its own"),
            "isolated-project"
        );
        assert_eq!(rejection_code("something else entirely"), "other");
        for code in [
            "confirmed-needs-evidence",
            "quote-not-found",
            "isolated-project",
            "other",
        ] {
            assert!(
                code.chars()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
            );
        }
    }

    #[test]
    fn brief_entries_are_cut_at_the_cap_and_say_so() {
        assert_eq!(truncate_entry("short\nline", 400), "short line");
        let long = "x".repeat(500);
        let cut = truncate_entry(&long, 400);
        assert_eq!(cut.chars().count(), 400 + " [truncated]".len());
        assert!(cut.ends_with(" [truncated]"));
    }

    const OFF_PARAGRAPH: &str = "## Tooling note (owner, 2026-09-22)\nThe orchestration methods are OFF: no groundworks / \
board / tickets / hindsight / fleetview, no `orchestrate` /\n`ticket` skills. UPDATE 2026-10-18 (owner: \"turn the \
knowledge base on\"): re_kb is ON again -- SurrealDB on\n127.0.0.1:8001 (ns `re`, db `kb`, root/root). Ghidra \
(GhidraMCP) is also on.\n";

    #[test]
    fn the_stamp_inserts_once_replaces_the_off_sentence_and_keeps_the_rest() {
        let text = format!("---\nname: x\n---\n\nBody.\n\n{OFF_PARAGRAPH}");
        let stamped = stamp_text(&text).expect("changes");
        assert!(stamped.contains(ON_SENTENCE), "{stamped}");
        assert!(!stamped.contains("The orchestration methods are OFF"), "{stamped}");
        assert!(stamped.contains("UPDATE 2026-10-18 (owner: \"turn the knowledge base on\"): re_kb is ON again"));
        assert!(stamped.contains("Ghidra (GhidraMCP) is also on."));
        assert_eq!(stamped.matches(STAMP_BEGIN).count(), 1);
        assert!(stamped.ends_with(&format!("{STAMP_BLOCK}\n")));
        assert!(STAMP_BLOCK.lines().count() <= 8, "the block is at most 8 lines");
        assert_eq!(stamp_drift(&stamped), None);
        assert_eq!(stamp_text(&stamped), None, "a second stamp changes nothing");
        // A hand-edited block is drift and is put back by the next stamp.
        let edited = stamped.replace("one agent does the work", "many agents do the work");
        assert_eq!(stamp_drift(&edited), Some("block differs from the template"));
        assert_eq!(stamp_text(&edited).as_deref(), Some(stamped.as_str()));
        assert_eq!(stamp_drift("# plain agent\n"), Some("no Groundworks block"));
        assert_eq!(
            stamp_drift(&format!("{STAMP_BLOCK}\n{OFF_PARAGRAPH}")),
            Some("still carries the 2026-09-22 OFF sentence")
        );
    }

    #[test]
    fn install_team_stamp_and_check_work_on_a_directory() {
        let base = std::env::temp_dir().join(format!("gw-install-team-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let home_dir = base.join(".groundworks");
        std::fs::create_dir_all(home_dir.join("team")).unwrap();
        std::fs::write(home_dir.join("groundworks.toml"), "").unwrap();
        std::fs::write(
            home_dir.join("team/gw-engineer.md"),
            "---\nname: gw-engineer\n---\nbuilds\n",
        )
        .unwrap();
        let agents = base.join("agents");
        std::fs::create_dir_all(&agents).unwrap();
        std::fs::write(agents.join("old.md"), format!("# old\n\n{OFF_PARAGRAPH}")).unwrap();
        std::fs::write(agents.join("silent.md"), "# silent\n").unwrap();
        std::fs::write(agents.join("notes.txt"), "not an agent").unwrap();
        let home = Home::open(&home_dir).unwrap();
        let check = InstallTeam {
            check: true,
            agents_dir: Some(agents.clone()),
            ..InstallTeam::default()
        };
        let (report, ok) = install_team(&home, &check).unwrap();
        assert!(!ok, "{report}");
        assert_eq!(report.matches("DRIFT").count(), 2, "{report}");
        let stamp = InstallTeam {
            stamp: true,
            check: true,
            agents_dir: Some(agents.clone()),
        };
        let (report, ok) = install_team(&home, &stamp).unwrap();
        assert!(ok, "{report}");
        assert!(report.contains("1 agent(s) installed"), "{report}");
        assert!(
            report.contains("3 file(s) written (1 OFF sentence(s) replaced)"),
            "{report}"
        );
        assert!(report.contains("check: 0 of 3 agent(s) drift"), "{report}");
        assert_eq!(
            std::fs::read_to_string(agents.join("notes.txt")).unwrap(),
            "not an agent"
        );
        let old = std::fs::read_to_string(agents.join("old.md")).unwrap();
        assert!(old.contains(ON_SENTENCE) && old.contains("re_kb is ON again"), "{old}");
        // Hand-edit one block: --check goes red and names it.
        let gw = agents.join("gw-engineer.md");
        let text = std::fs::read_to_string(&gw).unwrap().replace("stay OFF", "stay ON");
        std::fs::write(&gw, text).unwrap();
        let (report, ok) = install_team(&home, &check).unwrap();
        assert!(
            !ok && report.contains("DRIFT") && report.contains("gw-engineer.md: block differs"),
            "{report}"
        );
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn environment_checks_fail_on_a_hookless_plugin_and_an_unstamped_agent_and_warn_on_vscode() {
        let base = std::env::temp_dir().join(format!("gw-doctor-env-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let claude = base.join("claude");
        let cache = claude.join("plugins/cache/groundworks/groundworks");
        std::fs::create_dir_all(cache.join("0.2.0")).unwrap();
        std::fs::create_dir_all(claude.join("agents")).unwrap();
        std::fs::write(claude.join("agents/plain.md"), "# plain\n").unwrap();
        let mcp = base.join("mcp.json");
        std::fs::write(&mcp, r#"{"servers": {"other": {"type": "stdio"}}}"#).unwrap();
        let lines = environment_checks(&claude, Some(&mcp));
        assert!(
            lines
                .iter()
                .any(|l| l.starts_with("FAIL plugin") && l.contains("hooks/hooks.json")),
            "{lines:?}"
        );
        assert!(
            lines
                .iter()
                .any(|l| l.starts_with("FAIL agents: 1 of 1") && l.contains("plain.md")),
            "{lines:?}"
        );
        assert!(lines.iter().any(|l| l.starts_with("warn vscode")), "{lines:?}");
        // Fixed: a newer version with the hooks (0.10.0 sorts after 0.2.0), a stamped agent, a server entry.
        std::fs::create_dir_all(cache.join("0.10.0/hooks")).unwrap();
        std::fs::write(cache.join("0.10.0/hooks/hooks.json"), "{}").unwrap();
        std::fs::write(cache.join("0.10.0/hooks/deny.toml"), "").unwrap();
        std::fs::create_dir_all(cache.join("0.10.0/.claude-plugin")).unwrap();
        std::fs::write(
            cache.join("0.10.0/.claude-plugin/plugin.json"),
            r#"{"name": "groundworks", "hooks": "./hooks/hooks.json"}"#,
        )
        .unwrap();
        std::fs::write(claude.join("agents/plain.md"), stamp_text("# plain\n").unwrap()).unwrap();
        std::fs::write(
            &mcp,
            r#"{"servers": {"groundworks": {"type": "stdio", "command": "groundworks"}}}"#,
        )
        .unwrap();
        let lines = environment_checks(&claude, Some(&mcp));
        assert!(lines.iter().all(|l| l.starts_with("ok   ")), "{lines:?}");
        assert!(lines.iter().any(|l| l.contains("0.10.0")), "{lines:?}");
        // No Claude Code here at all is a warning, not a pass; no APPDATA is a warning too.
        let lines = environment_checks(&base.join("nowhere"), None);
        assert_eq!(lines.iter().filter(|l| l.starts_with("warn")).count(), 2, "{lines:?}");
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn the_shipped_plugin_manifest_loads_and_the_directory_form_is_red() {
        let shipped = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../plugin");
        assert_eq!(plugin_manifest_problems(&shipped), Vec::<String>::new());
        let base = std::env::temp_dir().join(format!("gw-manifest-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(base.join(".claude-plugin")).unwrap();
        std::fs::create_dir_all(base.join("agents")).unwrap();
        std::fs::create_dir_all(base.join("hooks")).unwrap();
        std::fs::write(
            base.join("agents/a.md"),
            "# a
",
        )
        .unwrap();
        std::fs::write(base.join("hooks/hooks.json"), "{}").unwrap();
        let manifest = |agents: &str| {
            std::fs::write(
                base.join(".claude-plugin/plugin.json"),
                format!(r#"{{"name": "groundworks", "agents": {agents}, "hooks": "./hooks/hooks.json"}}"#),
            )
            .unwrap();
            plugin_manifest_problems(&base)
        };
        let red = manifest(r#""./agents/""#);
        assert!(
            red.iter()
                .any(|p| p.contains("not a .md file") && p.contains("2.1.300")),
            "{red:?}"
        );
        assert!(
            red.iter()
                .any(|p| p.contains("./agents/a.md") && p.contains("not listed")),
            "{red:?}"
        );
        assert!(manifest(r#"["./agents/a.md"]"#).is_empty());
        assert!(manifest(r#""./agents/a.md""#).is_empty());
        assert!(
            manifest(r#"["./agents/missing.md", "./agents/a.md"]"#)
                .iter()
                .any(|p| p.contains("missing.md") && p.contains("not in the plugin"))
        );
        std::fs::remove_file(base.join(".claude-plugin/plugin.json")).unwrap();
        assert_eq!(plugin_manifest_problems(&base), ["no .claude-plugin/plugin.json"]);
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn doctor_fails_when_the_machine_has_no_name() {
        let (line, name) = machine_check(None);
        assert!(line.starts_with("FAIL machine"), "{line}");
        assert_eq!(name, crate::home::UNKNOWN_MACHINE);
        let (line, name) = machine_check(Some("THERIGXD".to_owned()));
        assert_eq!(line, "ok   machine: THERIGXD\n");
        assert_eq!(name, "THERIGXD");
    }
}
