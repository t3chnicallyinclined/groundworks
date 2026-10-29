//! `bootstrap` and `upgrade`: a stack's template rendered into a new project through Copier, which
//! records the template repository's commit in the project (`.copier-answers.yml`), so `upgrade` can
//! bring later template changes as a three-way merge (`copier update`). Adopted 2026-10-29 over the
//! earlier one-shot copy (research/FULLSTACK_AI_SURVEY_2026-10-28.md, docs/DECISIONS.md): a template
//! without an upgrade path should never get a second user, and Copier (MIT) already solves it.
//!
//! Copier runs through `uvx copier` (or the program named by `GROUNDWORKS_COPIER`), always at the
//! template repository's committed HEAD (`--vcs-ref=HEAD`). Measured 2026-10-29 (copier 9.19.1):
//! rendering a repository's working copy ignored `_subdirectory` and copied the whole repository; a
//! relative `_src_path` made `update` fail with "Unknown revision"; and under Git Bash `PATHEXT` is
//! unset, so Copier could not find `git`. Each is handled below.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use anyhow::{Context, Result, bail, ensure};

use crate::home::{Home, validate_name};
use crate::registry::Stack;

/// The file Copier leaves in a project: its answers and the template commit (`_commit`).
pub const ANSWERS_FILE: &str = ".copier-answers.yml";

/// Render `stack`'s template into `target` (which must not exist or must be empty) and return the
/// files written, relative to `target`.
pub fn bootstrap(home: &Home, stack: &Stack, name: &str, target: &Path) -> Result<Vec<PathBuf>> {
    validate_name(name)?;
    let template = stack
        .template
        .as_deref()
        .with_context(|| format!("stack {} has no template to bootstrap from", stack.name))?;
    let templates = home.templates();
    let source = templates.join(template);
    if !source.is_dir() {
        bail!("template {} is missing", source.display());
    }
    if target.exists() && target.read_dir()?.next().is_some() {
        bail!(
            "{} exists and is not empty; refusing to scaffold over it",
            target.display()
        );
    }
    let (root, rel) = template_repo(&templates)?;
    let crate_name = name.replace(['-', '.'], "_");
    render(
        &root,
        &rel,
        template,
        &Data {
            name,
            crate_name: &crate_name,
            dependencies: &stack.dependencies.join("\n"),
            dev_dependencies: &stack.dev_dependencies.join("\n"),
        },
        target,
    )?;
    let mut written = Vec::new();
    let mut pending = vec![target.to_path_buf()];
    while let Some(dir) = pending.pop() {
        for entry in std::fs::read_dir(&dir)? {
            let path = entry?.path();
            if path.is_dir() {
                pending.push(path);
            } else {
                written.push(path.strip_prefix(target)?.to_path_buf());
            }
        }
    }
    written.sort();
    Ok(written)
}

/// What a template is rendered with.
pub struct Data<'a> {
    pub name: &'a str,
    pub crate_name: &'a str,
    pub dependencies: &'a str,
    pub dev_dependencies: &'a str,
}

/// `copier copy` of `<root>/<rel>/<stack>` at the repository's HEAD into `target`.
pub fn render(root: &Path, rel: &str, stack: &str, data: &Data<'_>, target: &Path) -> Result<()> {
    ensure!(
        root.join("copier.yml").is_file(),
        "the template repository {} has no copier.yml at its root",
        root.display()
    );
    let mut cmd = copier()?;
    cmd.args(["copy", "--defaults", "--quiet", "--vcs-ref=HEAD"])
        .arg(format!("-dtemplates_dir={rel}"))
        .arg(format!("-dstack={stack}"))
        .arg(format!("-dname={}", data.name))
        .arg(format!("-dcrate={}", data.crate_name))
        .arg(format!("-ddependencies={}", data.dependencies))
        .arg(format!("-ddev_dependencies={}", data.dev_dependencies))
        .arg(root)
        .arg(target);
    run(cmd, "copier copy")?;
    ensure!(
        target.join(ANSWERS_FILE).is_file(),
        "copier wrote no {ANSWERS_FILE}: the template {stack} must contain a `{{{{ _copier_conf.answers_file }}}}` file"
    );
    ensure!(
        template_version(target).is_some(),
        "{ANSWERS_FILE} records no template commit: the template repository must be a git repository with a commit"
    );
    Ok(())
}

/// `copier update` in a project scaffolded by [`bootstrap`]: later template changes are merged in,
/// conflicts marked inline. Copier itself refuses a destination with uncommitted changes.
pub fn upgrade(project_dir: &Path) -> Result<String> {
    let before = template_version(project_dir).with_context(|| {
        format!(
            "{} was not scaffolded from a versioned template (no {ANSWERS_FILE}); nothing to upgrade",
            project_dir.display()
        )
    })?;
    let mut cmd = copier()?;
    cmd.args([
        "update",
        "--defaults",
        "--quiet",
        "--vcs-ref=HEAD",
        "--conflict",
        "inline",
    ])
    .current_dir(project_dir);
    run(cmd, "copier update")?;
    let after = template_version(project_dir).unwrap_or_else(|| "?".to_owned());
    let changed = Command::new("git")
        .args(["--no-optional-locks", "status", "--short"])
        .current_dir(project_dir)
        .stdin(Stdio::null())
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim_end().to_owned())
        .unwrap_or_default();
    Ok(if before == after && changed.is_empty() {
        format!("template {before}: already current, nothing changed")
    } else {
        format!(
            "template {before} -> {after}\nchanged:\n{}\nconflicts, if any, are marked inline (<<<<<<<): resolve them, then run `gate`",
            if changed.is_empty() {
                "  (nothing)".to_owned()
            } else {
                changed
            }
        )
    })
}

/// The template commit a project was rendered from (`_commit` in its answers file).
pub fn template_version(project_dir: &Path) -> Option<String> {
    let text = std::fs::read_to_string(project_dir.join(ANSWERS_FILE)).ok()?;
    text.lines()
        .find_map(|l| l.strip_prefix("_commit:"))
        .map(|v| v.trim().trim_matches(['\'', '"']).to_owned())
        .filter(|v| !v.is_empty())
}

/// The git repository root that holds `templates`, and `templates` relative to it with `/`.
fn template_repo(templates: &Path) -> Result<(PathBuf, String)> {
    let out = Command::new("git")
        .arg("-C")
        .arg(templates)
        .args(["--no-optional-locks", "rev-parse", "--show-toplevel"])
        .stdin(Stdio::null())
        .output()
        .context("running git")?;
    ensure!(
        out.status.success(),
        "{} is not inside a git repository, and a template is versioned by its repository's commits",
        templates.display()
    );
    let root = PathBuf::from(String::from_utf8_lossy(&out.stdout).trim());
    let canon = |p: &Path| std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    let rel = canon(templates)
        .strip_prefix(canon(&root))
        .map(|p| p.to_string_lossy().replace('\\', "/"))
        .with_context(|| format!("{} is not under {}", templates.display(), root.display()))?;
    Ok((root, rel))
}

/// The Copier command: the program named by `GROUNDWORKS_COPIER`, else `uvx copier`.
fn copier() -> Result<Command> {
    let mut cmd = if let Some(program) = std::env::var_os("GROUNDWORKS_COPIER") {
        Command::new(program)
    } else {
        let uvx = if cfg!(windows) { "uvx.exe" } else { "uvx" };
        ensure!(
            on_path(uvx),
            "Copier not found: install uv (https://docs.astral.sh/uv/) so `uvx copier` runs, or set GROUNDWORKS_COPIER to a copier executable"
        );
        let mut c = Command::new(uvx);
        c.arg("copier");
        c
    };
    if cfg!(windows) && std::env::var_os("PATHEXT").is_none_or(|v| v.is_empty()) {
        cmd.env("PATHEXT", ".COM;.EXE;.BAT;.CMD");
    }
    cmd.stdin(Stdio::null());
    Ok(cmd)
}

fn on_path(exe: &str) -> bool {
    std::env::var_os("PATH")
        .iter()
        .flat_map(std::env::split_paths)
        .any(|dir| dir.join(exe).is_file())
}

fn run(mut cmd: Command, what: &str) -> Result<()> {
    let output = cmd.output().with_context(|| format!("running {what}"))?;
    if !output.status.success() {
        let err = String::from_utf8_lossy(&output.stderr);
        let last = err.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("").trim();
        bail!("{what} failed: {last}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn git(dir: &Path, args: &[&str]) {
        let out = Command::new("git")
            .arg("-C")
            .arg(dir)
            .args(["-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.autocrlf=false"])
            .args(args)
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "git {args:?}: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }

    /// The real copier against a small template repository: render records the commit, and an
    /// update after a template change reaches the project while a local edit survives. Fails as
    /// NOT EXECUTED when copier cannot run, rather than passing over nothing.
    #[test]
    fn render_records_the_template_commit_and_upgrade_merges_later_changes() {
        copier().expect("NOT EXECUTED: copier is not available (install uv or set GROUNDWORKS_COPIER)");
        let base = std::env::temp_dir().join(format!("gw-scaffold-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let repo = base.join("repo");
        let template = repo.join("tpl").join("tiny");
        std::fs::create_dir_all(&template).unwrap();
        let root_yml = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../copier.yml");
        let yml = std::fs::read_to_string(&root_yml)
            .unwrap()
            .replace("choices: [rust-cli, rust-service]", "choices: [tiny]");
        std::fs::write(repo.join("copier.yml"), yml).unwrap();
        std::fs::write(
            template.join("{{ _copier_conf.answers_file }}"),
            "{{ _copier_answers|to_nice_yaml -}}\n",
        )
        .unwrap();
        std::fs::write(template.join("README.md"), "# {{name}} ({{crate}})\nline one\n").unwrap();
        std::fs::write(template.join("Cargo.toml"), "[dependencies]\n{{dependencies}}\n").unwrap();
        git(&repo, &["init", "-q"]);
        git(&repo, &["add", "-A"]);
        git(&repo, &["commit", "-qm", "template"]);

        let project = base.join("proj");
        let data = Data {
            name: "my-tool",
            crate_name: "my_tool",
            dependencies: "anyhow = \"1\"",
            dev_dependencies: "",
        };
        render(&repo, "tpl", "tiny", &data, &project).unwrap();
        assert_eq!(
            std::fs::read_to_string(project.join("README.md")).unwrap(),
            "# my-tool (my_tool)\nline one\n"
        );
        assert_eq!(
            std::fs::read_to_string(project.join("Cargo.toml")).unwrap(),
            "[dependencies]\nanyhow = \"1\"\n"
        );
        let v1 = template_version(&project).expect("a template commit is recorded");

        git(&project, &["init", "-q"]);
        git(&project, &["add", "-A"]);
        git(&project, &["commit", "-qm", "init"]);
        std::fs::write(project.join("local.txt"), "mine\n").unwrap();
        git(&project, &["add", "-A"]);
        git(&project, &["commit", "-qm", "local"]);

        std::fs::write(
            template.join("README.md"),
            "# {{name}} ({{crate}})\nline one\nline two\n",
        )
        .unwrap();
        git(&repo, &["commit", "-qam", "template: line two"]);
        let report = upgrade(&project).unwrap();
        let v2 = template_version(&project).unwrap();
        assert_ne!(v1, v2, "{report}");
        assert_eq!(
            std::fs::read_to_string(project.join("README.md")).unwrap(),
            "# my-tool (my_tool)\nline one\nline two\n",
            "{report}"
        );
        assert!(project.join("local.txt").is_file(), "a local file survives the upgrade");
        assert!(report.contains("README.md"), "{report}");

        let again = upgrade(&project);
        assert!(
            again.is_err(),
            "copier refuses a destination with uncommitted changes: {again:?}"
        );
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn the_template_version_is_read_from_the_answers_file() {
        let dir = std::env::temp_dir().join(format!("gw-answers-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(ANSWERS_FILE), "_commit: v1-3-gabc\n_src_path: x\nname: n\n").unwrap();
        assert_eq!(template_version(&dir).as_deref(), Some("v1-3-gabc"));
        std::fs::write(dir.join(ANSWERS_FILE), "_src_path: x\nname: n\n").unwrap();
        assert_eq!(template_version(&dir), None);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
