//! Where Groundworks' files live: the home, a `.groundworks/` directory holding the registry,
//! journal and knowledge, and the workspace, the directory that holds the projects (by default
//! the one that contains `.groundworks/`). Found by walking up from the opened folder, like git.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::Deserialize;

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    /// Directory that holds the projects. Defaults to the home's parent.
    #[serde(default)]
    pub workspace: Option<PathBuf>,
    /// Directory of project templates for `bootstrap`. Defaults to `<home>/templates`.
    #[serde(default)]
    pub templates: Option<PathBuf>,
    #[serde(default)]
    pub profile: ProfileConfig,
    #[serde(default)]
    pub scan: ScanConfig,
    #[serde(default)]
    pub store: StoreConfig,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StoreConfig {
    /// The SurrealDB endpoint; loopback only. Default `http://127.0.0.1:8004/sql`.
    #[serde(default)]
    pub url: Option<String>,
    /// Data directory, relative to the home. Default `db`.
    #[serde(default)]
    pub data: Option<PathBuf>,
    /// The `surreal` binary. Default: `GROUNDWORKS_SURREAL`, `PATH`, then the installer's location.
    #[serde(default)]
    pub surreal: Option<PathBuf>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScanConfig {
    /// Globs (fnmatch) for paths the estate scan must record as local-only and never walk.
    #[serde(default)]
    pub local_only: Vec<String>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProfileConfig {
    /// The authoritative record of the owner's work history.
    #[serde(default)]
    pub role_facts: Option<PathBuf>,
}

pub struct Home {
    pub root: PathBuf,
    pub workspace: PathBuf,
    pub config: Config,
}

impl Home {
    /// `GROUNDWORKS_HOME` names the home directly. Otherwise the nearest `.groundworks/` holding a
    /// `groundworks.toml`, walking up from `GROUNDWORKS_START` (the plugin passes the opened
    /// project) or the current directory. `GROUNDWORKS_WORKSPACE` overrides the workspace, so a
    /// container can mount the projects somewhere else.
    pub fn discover() -> Result<Self> {
        let root = match std::env::var_os("GROUNDWORKS_HOME") {
            Some(path) => PathBuf::from(path),
            None => {
                let start = match std::env::var_os("GROUNDWORKS_START") {
                    Some(path) => PathBuf::from(path),
                    None => std::env::current_dir()?,
                };
                find_home(&start).with_context(|| {
                    format!(
                        "no Groundworks home: no .groundworks/groundworks.toml in {} or any parent (set GROUNDWORKS_HOME)",
                        start.display()
                    )
                })?
            }
        };
        Self::open(&root)
    }

    /// The home at `root` (a folder holding `groundworks.toml`), with no discovery.
    pub fn open(root: &Path) -> Result<Self> {
        let root = root.to_path_buf();
        let config_path = root.join("groundworks.toml");
        let text = std::fs::read_to_string(&config_path).with_context(|| {
            format!(
                "no groundworks.toml at {} (set GROUNDWORKS_HOME)",
                config_path.display()
            )
        })?;
        let config: Config = toml::from_str(&text).with_context(|| format!("parsing {}", config_path.display()))?;
        let workspace = std::env::var_os("GROUNDWORKS_WORKSPACE")
            .map(PathBuf::from)
            .or_else(|| config.workspace.clone())
            .unwrap_or_else(|| root.parent().map_or_else(|| root.clone(), Path::to_path_buf));
        Ok(Self {
            root,
            workspace,
            config,
        })
    }

    pub fn templates(&self) -> PathBuf {
        match &self.config.templates {
            Some(dir) => self.root.join(dir),
            None => self.root.join("templates"),
        }
    }

    pub fn path(&self, relative: &str) -> PathBuf {
        self.root.join(relative)
    }

    pub fn role_facts(&self) -> Option<PathBuf> {
        std::env::var_os("GROUNDWORKS_ROLE_FACTS")
            .map(PathBuf::from)
            .or_else(|| self.config.profile.role_facts.clone())
    }
}

/// The nearest `<dir>/.groundworks/` with a `groundworks.toml`, from `start` upward.
pub fn find_home(start: &Path) -> Option<PathBuf> {
    start
        .ancestors()
        .map(|dir| dir.join(".groundworks"))
        .find(|home| home.join("groundworks.toml").is_file())
}

/// The machine a result came from, so no result is ever detached from where it ran.
pub fn machine() -> String {
    ["GROUNDWORKS_MACHINE", "COMPUTERNAME", "HOSTNAME"]
        .iter()
        .find_map(|var| std::env::var(var).ok().filter(|v| !v.is_empty()))
        .unwrap_or_else(|| "unknown".to_owned())
}

/// Names that become file names: letters, digits, `.`, `_`, `-`, not starting with `.`.
pub fn validate_name(name: &str) -> Result<()> {
    let ok = !name.is_empty()
        && !name.starts_with('.')
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'));
    anyhow::ensure!(ok, "{name:?} is not a valid name (use letters, digits, '.', '_', '-')");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_the_nearest_home_walking_up() {
        let base = std::env::temp_dir().join(format!("gw-home-test-{}", std::process::id()));
        let outer = base.join(".groundworks");
        let inner = base.join("a").join(".groundworks");
        let deep = base.join("a").join("b").join("c");
        std::fs::create_dir_all(&outer).unwrap();
        std::fs::create_dir_all(&deep).unwrap();
        std::fs::write(outer.join("groundworks.toml"), "").unwrap();
        assert_eq!(find_home(&deep), Some(outer.clone()));
        // A .groundworks/ without groundworks.toml is not a home.
        std::fs::create_dir_all(&inner).unwrap();
        assert_eq!(find_home(&deep), Some(outer.clone()));
        std::fs::write(inner.join("groundworks.toml"), "").unwrap();
        assert_eq!(find_home(&deep), Some(inner));
        std::fs::remove_dir_all(&base).unwrap();
        assert_eq!(find_home(&deep), None);
    }
}
