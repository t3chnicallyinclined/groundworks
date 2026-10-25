//! The registry is the single source of truth for projects, their environments
//! and the stacks they are built on. It is plain TOML under `registry/`, read
//! fresh on every call so a hand edit is visible at once.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};

use crate::home::Home;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Project {
    pub name: String,
    /// Directory relative to the workspace.
    pub dir: String,
    pub summary: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stack: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub repo: Option<String>,
    /// Docs worth reading first, relative to the project.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub docs: Vec<String>,
    /// The project's own gate, overriding its stack's (e.g. lane aliases in a shared workspace).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub gate: Vec<String>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub envs: BTreeMap<String, Env>,
}

impl Project {
    pub fn path(&self, workspace: &Path) -> PathBuf {
        workspace.join(&self.dir)
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Env {
    /// Run over ssh on this host alias instead of locally.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ssh: Option<String>,
    /// Working directory: on the remote host for ssh envs, else relative to the workspace.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dir: Option<String>,
    /// Commands need `confirm = "<project>/<env>"`.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub protected: bool,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub vars: BTreeMap<String, String>,
    /// Name -> secret reference (`env:`, `file:`, `bw:`). Never a value.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub secrets: BTreeMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Stack {
    pub name: String,
    pub summary: String,
    /// Directory under `templates/` that `bootstrap` copies.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub template: Option<String>,
    /// Commands run in order by `gate`; the first failure stops it.
    #[serde(default)]
    pub gate: Vec<String>,
    #[serde(default)]
    pub dependencies: Vec<String>,
    #[serde(default)]
    pub dev_dependencies: Vec<String>,
    #[serde(default)]
    pub notes: Vec<String>,
}

/// Variables and secrets every local env gets unless it sets its own.
#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Shared {
    #[serde(default)]
    vars: BTreeMap<String, String>,
    #[serde(default)]
    secrets: BTreeMap<String, String>,
}

pub struct Registry {
    pub projects: BTreeMap<String, Project>,
    pub stacks: BTreeMap<String, Stack>,
    shared: Shared,
}

fn load_dir<T: DeserializeOwned>(dir: &Path) -> Result<Vec<(String, T)>> {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e).with_context(|| format!("reading {}", dir.display())),
    };
    let mut out = Vec::new();
    for entry in entries {
        let path = entry?.path();
        if path.extension().is_some_and(|ext| ext == "toml") {
            let stem = path.file_stem().unwrap_or_default().to_string_lossy().into_owned();
            let text = std::fs::read_to_string(&path)?;
            let value = toml::from_str(&text).with_context(|| format!("parsing {}", path.display()))?;
            out.push((stem, value));
        }
    }
    Ok(out)
}

impl Registry {
    pub fn load(home: &Home) -> Result<Self> {
        let mut stacks = BTreeMap::new();
        for (stem, stack) in load_dir::<Stack>(&home.path("registry/stacks"))? {
            if stem != stack.name {
                bail!(
                    "registry/stacks/{stem}.toml declares name = {:?}; they must match",
                    stack.name
                );
            }
            stacks.insert(stack.name.clone(), stack);
        }
        let mut projects = BTreeMap::new();
        for (stem, project) in load_dir::<Project>(&home.path("registry/projects"))? {
            if stem != project.name {
                bail!(
                    "registry/projects/{stem}.toml declares name = {:?}; they must match",
                    project.name
                );
            }
            if let Some(stack) = &project.stack
                && !stacks.contains_key(stack)
            {
                bail!("project {} names unknown stack {stack:?}", project.name);
            }
            projects.insert(project.name.clone(), project);
        }
        let shared_path = home.path("registry/shared.toml");
        let shared = match std::fs::read_to_string(&shared_path) {
            Ok(text) => toml::from_str(&text).with_context(|| format!("parsing {}", shared_path.display()))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Shared::default(),
            Err(e) => return Err(e).with_context(|| format!("reading {}", shared_path.display())),
        };
        Ok(Self {
            projects,
            stacks,
            shared,
        })
    }

    pub fn project(&self, name: &str) -> Result<&Project> {
        self.projects
            .get(name)
            .with_context(|| format!("unknown project {name:?}; registered: {}", join_keys(&self.projects)))
    }

    pub fn stack(&self, name: &str) -> Result<&Stack> {
        self.stacks
            .get(name)
            .with_context(|| format!("unknown stack {name:?}; known: {}", join_keys(&self.stacks)))
    }

    /// The env with shared values filled in. `local` exists even when not declared.
    /// Shared secrets only reach local envs: nothing ships a secret over ssh.
    pub fn env(&self, project: &Project, name: &str) -> Result<Env> {
        let mut env = match project.envs.get(name) {
            Some(env) => env.clone(),
            None if name == "local" => Env::default(),
            None => bail!(
                "project {} has no env {name:?}; envs: local{}",
                project.name,
                project
                    .envs
                    .keys()
                    .filter(|k| *k != "local")
                    .map(|k| format!(", {k}"))
                    .collect::<String>()
            ),
        };
        for (key, value) in &self.shared.vars {
            env.vars.entry(key.clone()).or_insert_with(|| value.clone());
        }
        if env.ssh.is_none() {
            for (key, value) in &self.shared.secrets {
                env.secrets.entry(key.clone()).or_insert_with(|| value.clone());
            }
        }
        Ok(env)
    }
}

fn join_keys<V>(map: &BTreeMap<String, V>) -> String {
    if map.is_empty() {
        return "(none)".to_owned();
    }
    map.keys().map(String::as_str).collect::<Vec<_>>().join(", ")
}
