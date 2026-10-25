//! `bootstrap`: copy a stack's template into a new project directory,
//! filling `{{name}}`, `{{crate}}`, `{{dependencies}}` and `{{dev_dependencies}}`.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};

use crate::home::{Home, validate_name};
use crate::registry::Stack;

pub fn bootstrap(home: &Home, stack: &Stack, name: &str, target: &Path) -> Result<Vec<PathBuf>> {
    validate_name(name)?;
    let template = stack
        .template
        .as_deref()
        .with_context(|| format!("stack {} has no template to bootstrap from", stack.name))?;
    let source = home.templates().join(template);
    if !source.is_dir() {
        bail!("template {} is missing", source.display());
    }
    if target.exists() && target.read_dir()?.next().is_some() {
        bail!(
            "{} exists and is not empty; refusing to scaffold over it",
            target.display()
        );
    }

    let crate_name = name.replace(['-', '.'], "_");
    let dependencies = stack.dependencies.join("\n");
    let dev_dependencies = stack.dev_dependencies.join("\n");
    let fill = |text: &str| {
        text.replace("{{name}}", name)
            .replace("{{crate}}", &crate_name)
            .replace("{{dependencies}}", &dependencies)
            .replace("{{dev_dependencies}}", &dev_dependencies)
    };

    let mut written = Vec::new();
    let mut pending = vec![source.clone()];
    while let Some(dir) = pending.pop() {
        for entry in std::fs::read_dir(&dir)? {
            let path = entry?.path();
            if path.is_dir() {
                pending.push(path);
                continue;
            }
            let relative = path.strip_prefix(&source)?;
            let dest = target.join(relative);
            if let Some(parent) = dest.parent() {
                std::fs::create_dir_all(parent)?;
            }
            let bytes = std::fs::read(&path)?;
            match String::from_utf8(bytes) {
                Ok(text) => std::fs::write(&dest, fill(&text))?,
                Err(raw) => std::fs::write(&dest, raw.into_bytes())?,
            }
            written.push(relative.to_path_buf());
        }
    }
    written.sort();
    Ok(written)
}
