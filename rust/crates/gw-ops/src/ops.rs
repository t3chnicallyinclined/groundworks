//! Every operation, shared by the MCP tools and the CLI. Each returns the text a
//! reader sees; operations that run something also say whether it passed.

use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result, bail, ensure};

use crate::home::{Home, validate_name};
use crate::journal::{self, Confidence, Entry, Kind};
use crate::registry::{Project, Registry};
use crate::secrets::SecretRef;
use crate::{exec, knowledge, scaffold};

pub const MAX_TIMEOUT_SECS: u64 = 3600;

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

pub fn show(home: &Home, name: &str, journal_limit: usize) -> Result<String> {
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
    let entries = journal::read(home, name, None, journal_limit)?;
    writeln!(out, "\n## Journal (newest {} of the project)", entries.len())?;
    if entries.is_empty() {
        out.push_str("(empty: record what you learn with journal_add)\n");
    }
    for entry in &entries {
        writeln!(out, "- {}", entry.render())?;
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

pub fn note(
    home: &Home,
    project: &str,
    kind: Kind,
    text: &str,
    source: &str,
    confidence: Confidence,
    by: &str,
) -> Result<String> {
    Registry::load(home)?.project(project)?;
    let entry = Entry::new(project, kind, text, source, confidence, by)?;
    journal::append(home, &entry)?;
    Ok(format!("recorded: {}", entry.render()))
}

pub fn journal(home: &Home, project: &str, kind: Option<Kind>, limit: usize) -> Result<String> {
    Registry::load(home)?.project(project)?;
    let entries = journal::read(home, project, kind, limit)?;
    if entries.is_empty() {
        return Ok(format!("{project} has no matching journal entries"));
    }
    Ok(entries.iter().map(|e| format!("- {}\n", e.render())).collect())
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
    Ok((outcome.render(), outcome.ok()))
}

/// The project's gate if it declares one, else its stack's; step by step. A gate with no
/// steps is an error, not a pass.
pub async fn gate(home: &Home, project: &str, env_name: &str, timeout_secs: u64) -> Result<(String, bool)> {
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
    for (i, step) in steps.iter().enumerate() {
        let outcome = exec::run(home, project, &env, step, Duration::from_secs(timeout_secs)).await?;
        let state = if outcome.ok() { "pass" } else { "FAIL" };
        writeln!(
            out,
            "step {}/{total} {state} ({:.1}s): {step}",
            i + 1,
            outcome.elapsed.as_secs_f64()
        )?;
        if !outcome.ok() {
            writeln!(out, "\nGATE RED at step {}/{total}\n\n{}", i + 1, outcome.render())?;
            return Ok((out, false));
        }
        if i + 1 == total {
            writeln!(
                out,
                "\nGATE GREEN {total}/{total} on {} in {}",
                outcome.machine, outcome.location
            )?;
        }
    }
    Ok((out, true))
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
pub async fn doctor(home: &Home) -> Result<(String, bool)> {
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
