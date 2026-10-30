//! A project's own Claude Code setup, read at intake with no flag (owner, 2026-10-30: "usually user will
//! already have sessions and agents/skills ... pick it up if they're in the repo"):
//!
//! - **Project experts**: `<project>/.claude/agents/*.md`, parsed like the global ones. They map areas in
//!   their own project only, and win a name collision there; the store's expert directory keeps both,
//!   told apart by `source` (`global` or `project:<name>`). A folder under `.claude/` whose name holds
//!   `.disabled` (`agents.disabled-<date>`) was switched off on purpose: never loaded, only reported.
//! - **Skills, commands and instructions** are units of their own kinds (see `intake::classify`); the ones
//!   git does not list (ignored) are read from disk too, through the journal's secret refusal.
//! - **Auto-memory**: Claude Code keeps per-folder memory in `<claude>/projects/<slug>/memory/*.md`, the
//!   slug being the folder's absolute path with every character that is not an ASCII letter or digit
//!   replaced by `-` (checked against the real folders on 2026-10-30: `.`, `\` and `:` all become `-`).
//!   Each file becomes `memory` units, unless the journal's secret refusal fires on it: then it is skipped
//!   and counted, never stored.
//! - **Session transcripts** (`<claude>/projects/<slug>/*.jsonl`) are counted, never read: what (if
//!   anything) of a transcript may be stored is gw-security's call (see [`transcript_units`]).
//!
//! `[intake] claude_setup = false` in groundworks.toml turns all of it off.

use std::path::{Path, PathBuf};

use anyhow::Result;

use crate::intake::{Expert, load_experts};

/// Memory units are not files of the project: their paths carry this prefix, which [`resolve`] maps
/// back to the memory folder so a quote of one can be re-checked.
pub const MEMORY_PREFIX: &str = "~claude-memory/";
/// Names of a project's own experts or skills the SessionStart brief lists, at most.
pub const BRIEF_NAMES: usize = 12;

/// Claude Code's folder name for a project directory: every character that is not an ASCII letter or
/// digit becomes `-`.
pub fn slug(dir: &Path) -> String {
    let abs = if dir.is_absolute() {
        dir.to_path_buf()
    } else {
        std::env::current_dir()
            .map(|c| c.join(dir))
            .unwrap_or_else(|_| dir.to_path_buf())
    };
    abs.display()
        .to_string()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect()
}

/// `<claude>/projects/<slug>`: where Claude Code keeps this folder's sessions and memory.
pub fn session_dir(claude: &Path, project_dir: &Path) -> PathBuf {
    claude.join("projects").join(slug(project_dir))
}

/// A folder under `.claude/` the owner switched off (`agents.disabled-2026-09-21`), with how many
/// definitions it holds.
#[derive(Debug, Clone)]
pub struct Disabled {
    pub folder: String,
    pub definitions: usize,
}

fn md_files(dir: &Path) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_file() && p.extension().is_some_and(|e| e.eq_ignore_ascii_case("md")))
        .collect();
    files.sort();
    files
}

/// The project's own experts (`.claude/agents/*.md`, tagged `project:<name>`) and the disabled folders
/// beside them, which are not loaded.
pub fn project_experts(project_dir: &Path, project: &str) -> Result<(Vec<Expert>, Vec<Disabled>)> {
    let claude = project_dir.join(".claude");
    let mut experts = load_experts(&[claude.join("agents")])?;
    for e in &mut experts {
        e.source = format!("project:{project}");
    }
    let mut disabled: Vec<Disabled> = std::fs::read_dir(&claude)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.path().is_dir())
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            name.to_lowercase().contains(".disabled").then(|| Disabled {
                folder: format!(".claude/{name}"),
                definitions: md_files(&e.path()).len(),
            })
        })
        .collect();
    disabled.sort_by(|a, b| a.folder.cmp(&b.folder));
    Ok((experts, disabled))
}

/// The experts that map areas in one project: its own, then every global expert it does not override
/// by name.
pub fn merge(global: &[Expert], local: &[Expert]) -> Vec<Expert> {
    let mut out: Vec<Expert> = local.to_vec();
    out.extend(
        global
            .iter()
            .filter(|g| !local.iter().any(|l| l.name == g.name))
            .cloned(),
    );
    out.sort_by(|a, b| a.name.cmp(&b.name));
    out
}

/// Claude Code setup files of a project whether or not git lists them (ignored ones included),
/// relative, forward slashes: the instruction files at the root, agents, skills and commands.
pub fn setup_paths(project_dir: &Path) -> Vec<String> {
    let rel = |p: &Path| {
        p.strip_prefix(project_dir)
            .map(|r| r.display().to_string().replace('\\', "/"))
            .ok()
    };
    let mut out: Vec<String> = Vec::new();
    for entry in std::fs::read_dir(project_dir).into_iter().flatten().flatten() {
        let name = entry.file_name().to_string_lossy().to_lowercase();
        if entry.path().is_file()
            && (name == "claude.md" || name == "agents.md" || name.starts_with("claude.md.disabled"))
        {
            out.extend(rel(&entry.path()));
        }
    }
    let claude = project_dir.join(".claude");
    out.extend(md_files(&claude.join("agents")).iter().filter_map(|p| rel(p)));
    for skill in std::fs::read_dir(claude.join("skills")).into_iter().flatten().flatten() {
        let file = skill.path().join("SKILL.md");
        if file.is_file() {
            out.extend(rel(&file));
        }
    }
    // Commands may sit one folder down (a namespace).
    let commands = claude.join("commands");
    out.extend(md_files(&commands).iter().filter_map(|p| rel(p)));
    for sub in std::fs::read_dir(&commands).into_iter().flatten().flatten() {
        if sub.path().is_dir() {
            out.extend(md_files(&sub.path()).iter().filter_map(|p| rel(p)));
        }
    }
    out.sort();
    out.dedup();
    out
}

/// The auto-memory files Claude Code keeps for this folder (`*.md` directly in `memory/`), sorted.
pub fn memory_files(session_dir: &Path) -> Vec<PathBuf> {
    md_files(&session_dir.join("memory"))
}

/// How many session transcripts Claude Code holds for this folder, and when the newest last changed.
pub fn sessions(session_dir: &Path) -> (usize, Option<String>) {
    let mut count = 0;
    let mut newest: Option<std::fs::Metadata> = None;
    for entry in std::fs::read_dir(session_dir).into_iter().flatten().flatten() {
        let path = entry.path();
        if path.is_file() && path.extension().is_some_and(|e| e == "jsonl") {
            count += 1;
            if let Ok(meta) = entry.metadata() {
                let later = match (&newest, meta.modified()) {
                    (Some(n), Ok(m)) => n.modified().is_ok_and(|nm| m > nm),
                    (None, _) => true,
                    _ => false,
                };
                if later {
                    newest = Some(meta);
                }
            }
        }
    }
    (count, newest.as_ref().and_then(crate::intake::mtime_iso))
}

/// SEAM, deliberately empty: units from session transcripts. A transcript holds everything a session saw
/// (tool output, pasted secrets, private folder names), so nothing of its content is stored until the
/// gw-security transcript review (opened 2026-10-30) decides what, if anything, may be. Intake counts
/// transcripts ([`sessions`]) and calls this, so that decision lands in one place.
pub fn transcript_units(_session_dir: &Path) -> Vec<crate::intake::Unit> {
    Vec::new()
}

/// A unit path as a file on disk: a memory path maps into the folder's memory directory, anything else
/// is relative to the project.
pub fn resolve(project_dir: &Path, path: &str, claude: &Path) -> PathBuf {
    match path.strip_prefix(MEMORY_PREFIX) {
        Some(file) => session_dir(claude, project_dir).join("memory").join(file),
        None => project_dir.join(path),
    }
}

/// A name from a repo's own file, made safe to print in the brief: letters, digits, `.`, `_`, `-`,
/// `:` only, at most 40 characters.
fn plain(name: &str) -> String {
    name.chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-' | ':'))
        .take(40)
        .collect()
}

fn frontmatter_name(file: &Path) -> Option<String> {
    let text = std::fs::read_to_string(file).ok()?.replace("\r\n", "\n");
    let front = text.strip_prefix("---")?.split_once("\n---")?.0.to_owned();
    front
        .lines()
        .find_map(|l| l.trim().strip_prefix("name:"))
        .map(|v| v.trim().trim_matches(['"', '\'']).to_owned())
}

/// The project's own expert and skill names, for the SessionStart brief: plain, sorted, deduplicated.
pub fn names(project_dir: &Path) -> (Vec<String>, Vec<String>) {
    let claude = project_dir.join(".claude");
    let mut experts: Vec<String> = md_files(&claude.join("agents"))
        .iter()
        .filter_map(|f| frontmatter_name(f))
        .map(|n| plain(&n))
        .filter(|n| !n.is_empty())
        .collect();
    let mut skills: Vec<String> = std::fs::read_dir(claude.join("skills"))
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.path().join("SKILL.md").is_file())
        .map(|e| {
            frontmatter_name(&e.path().join("SKILL.md")).unwrap_or_else(|| e.file_name().to_string_lossy().into_owned())
        })
        .map(|n| plain(&n))
        .filter(|n| !n.is_empty())
        .collect();
    for list in [&mut experts, &mut skills] {
        list.retain(|n| crate::refusal::check(n).is_none());
        list.sort();
        list.dedup();
    }
    (experts, skills)
}

/// The brief's one line naming them, capped; None when the project has neither.
pub fn brief_line(project_dir: &Path) -> Option<String> {
    let (experts, skills) = names(project_dir);
    if experts.is_empty() && skills.is_empty() {
        return None;
    }
    let list = |v: &[String]| {
        if v.is_empty() {
            return "none".to_owned();
        }
        let mut s = v.iter().take(BRIEF_NAMES).cloned().collect::<Vec<_>>().join(", ");
        if v.len() > BRIEF_NAMES {
            s.push_str(&format!(" (+{} more)", v.len() - BRIEF_NAMES));
        }
        s
    };
    Some(format!(
        "this repo's own Claude setup (names only): experts {} | skills {}",
        list(&experts),
        list(&skills)
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("gw-claude-setup-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write(path: &Path, text: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }

    #[test]
    fn the_slug_is_claude_codes_folder_name() {
        // The rule read off real folders: `.`, both slashes and the drive colon become `-`.
        let s = slug(Path::new("/srv/work/.groundworks/my_proj"));
        assert!(s.ends_with("-srv-work--groundworks-my-proj"), "{s}");
        assert!(s.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'));
        if cfg!(windows) {
            assert_eq!(slug(Path::new(r"C:\Users\a\projects\x.y")), "C--Users-a-projects-x-y");
        }
    }

    #[test]
    fn project_experts_are_tagged_and_disabled_folders_are_reported_not_loaded() {
        let dir = scratch("experts");
        write(
            &dir.join(".claude/agents/local.md"),
            "---\nname: local\ndescription: Owns `src/`.\n---\n",
        );
        write(
            &dir.join(".claude/agents.disabled-2026-09-21/old.md"),
            "---\nname: old\ndescription: Was switched off.\n---\n",
        );
        let (experts, disabled) = project_experts(&dir, "demo").unwrap();
        assert_eq!(experts.len(), 1);
        assert_eq!(
            (experts[0].name.as_str(), experts[0].source.as_str()),
            ("local", "project:demo")
        );
        assert_eq!(disabled.len(), 1);
        assert_eq!(disabled[0].folder, ".claude/agents.disabled-2026-09-21");
        assert_eq!(disabled[0].definitions, 1);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_project_expert_wins_its_name_and_the_rest_stay() {
        let e = |name: &str, source: &str| Expert {
            name: name.into(),
            summary: String::new(),
            file: PathBuf::from(format!("{source}/{name}.md")),
            text: String::new(),
            source: source.into(),
        };
        let merged = merge(&[e("a", "global"), e("b", "global")], &[e("b", "project:p")]);
        let pairs: Vec<(&str, &str)> = merged.iter().map(|x| (x.name.as_str(), x.source.as_str())).collect();
        assert_eq!(pairs, vec![("a", "global"), ("b", "project:p")]);
    }

    #[test]
    fn setup_paths_find_every_setup_file_and_names_are_plain_and_capped() {
        let dir = scratch("paths");
        write(&dir.join("CLAUDE.md"), "# rules\n");
        write(&dir.join("CLAUDE.md.disabled-2026-09-21"), "# old rules\n");
        write(&dir.join("notes.md"), "not setup\n");
        write(
            &dir.join(".claude/skills/check/SKILL.md"),
            "---\nname: check <script>alert(1)</script>\n---\n",
        );
        write(&dir.join(".claude/skills/check/helper.py"), "x = 1\n");
        write(&dir.join(".claude/commands/ship.md"), "ship it\n");
        write(&dir.join(".claude/commands/ops/restart.md"), "restart\n");
        for i in 0..15 {
            write(
                &dir.join(format!(".claude/agents/a{i:02}.md")),
                &format!("---\nname: a{i:02}\ndescription: d\n---\n"),
            );
        }
        let paths = setup_paths(&dir);
        assert!(paths.contains(&"CLAUDE.md".to_owned()), "{paths:?}");
        assert!(paths.contains(&"CLAUDE.md.disabled-2026-09-21".to_owned()));
        assert!(paths.contains(&".claude/skills/check/SKILL.md".to_owned()));
        assert!(paths.contains(&".claude/commands/ops/restart.md".to_owned()));
        assert!(
            !paths
                .iter()
                .any(|p| p.ends_with("notes.md") || p.ends_with("helper.py"))
        );
        let line = brief_line(&dir).unwrap();
        assert!(line.contains("a00, a01") && line.contains("(+3 more)"), "{line}");
        assert!(line.contains("checkscriptalert1script"), "{line}");
        assert!(!line.contains('<'));
        let empty = scratch("empty");
        assert!(brief_line(&empty).is_none());
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&empty);
    }

    #[test]
    fn transcripts_are_counted_and_memory_paths_resolve() {
        let claude = scratch("claude");
        let project = Path::new("/srv/p");
        let sd = session_dir(&claude, project);
        write(&sd.join("one.jsonl"), "{}\n");
        write(&sd.join("two.jsonl"), "{}\n");
        write(&sd.join("memory/a.md"), "a\n");
        let (n, newest) = sessions(&sd);
        assert_eq!(n, 2);
        assert!(newest.is_some());
        assert!(
            transcript_units(&sd).is_empty(),
            "transcript content waits for the security review"
        );
        assert_eq!(memory_files(&sd).len(), 1);
        assert_eq!(
            resolve(project, "~claude-memory/a.md", &claude),
            sd.join("memory").join("a.md")
        );
        assert_eq!(resolve(project, "src/a.rs", &claude), project.join("src/a.rs"));
        let _ = std::fs::remove_dir_all(&claude);
    }
}
