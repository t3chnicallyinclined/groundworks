//! Stage 1 of the bootstrap: walk a projects root and produce one registry record per project.
//! No model, no command execution beyond read-only git queries; candidate gates are derived and
//! never run. A port of the 2026-09-17 Python `xscholar/scan.py` (branch `product/estate-scan`,
//! a4136eb), checked against it record for record on the owner's estate.
//!
//! Decisions carried over from the Python, unchanged:
//! - A project boundary is a directory holding a `.git` entry. A walk stops at each boundary and a
//!   fresh walk starts there, so every file is visited by exactly one walk.
//! - A local-only project (a configured glob matches its path, or it is a git project with no
//!   remote) is never walked: its record is path, kind and head only, so nothing about its insides
//!   reaches the registry.
//! - Secret flags match file names only (`*key*` over-flags on purpose); contents are never read.
//! - Every git read uses `--no-optional-locks`, because plain `git status` rewrites `.git/index`.
//!
//! One deliberate difference: Python's `os.walk` descends into Windows directory junctions (only
//! true symlinks stop it). This port descends into neither, because telling the two apart needs
//! `unsafe` Windows calls and this workspace forbids `unsafe`. A junction is still listed as a
//! directory; its target is scanned where it really lives.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::LazyLock;
use std::time::Duration;

use regex::Regex;
use serde_json::{Value, json};
use wait_timeout::ChildExt;

const SKIP_DIRS: [&str; 7] = [
    "node_modules",
    "target",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    ".history",
];
const SKIP_DIR_PATTERNS: [&str; 1] = ["build_*"];
const SECRET_PATTERNS: [&str; 6] = [".env", ".env.*", "*.pem", "*key*", "credentials*", "id_rsa*"];
const GIT_TIMEOUT: Duration = Duration::from_secs(15);

static REMOTE_URL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?:[a-zA-Z][a-zA-Z0-9+.\-]*://)?(?:[^@/]+@)?([^:/]+)[:/](.+?)(?:\.git)?/?$").expect("valid")
});
static WORKTREE_POINTER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[\\/]\.git[\\/]worktrees[\\/][^\\/]+\s*$").expect("valid"));
static WORKTREE_MAIN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"gitdir:\s*(.+?)[\\/]\.git[\\/]worktrees[\\/][^\\/]+\s*$").expect("valid"));
static MAKE_TEST_TARGET: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?m)^test\s*:").expect("valid"));
static SECRETS: LazyLock<Vec<Glob>> = LazyLock::new(|| SECRET_PATTERNS.iter().map(|p| Glob::new(p)).collect());
static SKIP_GLOBS: LazyLock<Vec<Glob>> = LazyLock::new(|| SKIP_DIR_PATTERNS.iter().map(|p| Glob::new(p)).collect());
static COMPOSE: LazyLock<[Glob; 2]> =
    LazyLock::new(|| [Glob::new("docker-compose*.yml"), Glob::new("docker-compose*.yaml")]);

/// Python's `fnmatch.fnmatch`: `*`, `?`, `[seq]`, `[!seq]`, whole-string. On Windows it compares
/// case-insensitively with `/` read as `\`, exactly as `os.path.normcase` makes Python do.
pub struct Glob(Regex);

impl Glob {
    pub fn new(pattern: &str) -> Self {
        let pattern = normcase(pattern);
        let chars: Vec<char> = pattern.chars().collect();
        let mut re = String::from("(?s)^");
        let mut i = 0;
        while i < chars.len() {
            let c = chars[i];
            i += 1;
            match c {
                '*' => re.push_str(".*"),
                '?' => re.push('.'),
                '[' => {
                    let mut j = i;
                    if j < chars.len() && chars[j] == '!' {
                        j += 1;
                    }
                    if j < chars.len() && chars[j] == ']' {
                        j += 1;
                    }
                    while j < chars.len() && chars[j] != ']' {
                        j += 1;
                    }
                    if j >= chars.len() {
                        re.push_str(r"\[");
                    } else {
                        let mut set: String = chars[i..j].iter().collect::<String>().replace('\\', r"\\");
                        if let Some(rest) = set.strip_prefix('!') {
                            set = format!("^{rest}");
                        }
                        re.push('[');
                        re.push_str(&set);
                        re.push(']');
                        i = j + 1;
                    }
                }
                other => re.push_str(&regex::escape(&other.to_string())),
            }
        }
        re.push('$');
        Self(Regex::new(&re).unwrap_or_else(|_| Regex::new("$^").expect("valid")))
    }

    pub fn matches(&self, text: &str) -> bool {
        self.0.is_match(&normcase(text))
    }
}

fn normcase(s: &str) -> String {
    if cfg!(windows) {
        s.to_lowercase().replace('/', "\\")
    } else {
        s.to_owned()
    }
}

/// `git --no-optional-locks -C dir <args>`, read-only; trimmed stdout, or None on any failure.
fn git(dir: &Path, args: &[&str]) -> Option<String> {
    let mut child = Command::new("git")
        .arg("--no-optional-locks")
        .arg("-C")
        .arg(dir)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    // Read stdout on a thread so a chatty git cannot fill the pipe and stall past the timeout.
    let mut stdout = child.stdout.take()?;
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        std::io::Read::read_to_end(&mut stdout, &mut buf).ok().map(|_| buf)
    });
    let status = match child.wait_timeout(GIT_TIMEOUT).ok()? {
        Some(status) => status,
        None => {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
    };
    let out = reader.join().ok()??;
    status
        .success()
        .then(|| String::from_utf8_lossy(&out).trim().to_owned())
}

/// `git@github.com:user/repo.git`, `https://github.com/user/repo.git`, `ssh://git@h/p` -> `host/path`.
pub fn host_path_from_remote(url: &str) -> Option<String> {
    let caps = REMOTE_URL.captures(url.trim())?;
    let host = caps.get(1)?.as_str();
    let path = caps.get(2)?.as_str().trim_matches('/');
    (!host.is_empty() && !path.is_empty()).then(|| format!("{host}/{path}"))
}

/// Windows paths printed the way Python's `str(Path(...))` prints them.
fn display(path: &Path) -> String {
    let s = path.to_string_lossy();
    if cfg!(windows) {
        s.replace('/', "\\")
    } else {
        s.into_owned()
    }
}

struct GitInfo {
    kind: &'static str,
    fields: serde_json::Map<String, Value>,
    has_remote: bool,
    head_sha: Value,
}

fn git_info(dir: &Path) -> GitInfo {
    let git_entry = dir.join(".git");
    let mut kind = "git repo";
    let mut main_repo = Value::Null;
    if git_entry.is_file() {
        let pointer = std::fs::read(&git_entry)
            .map(|b| String::from_utf8_lossy(&b).into_owned())
            .unwrap_or_default();
        if WORKTREE_POINTER.is_match(&pointer) {
            kind = "git worktree";
            match git(dir, &["rev-parse", "--path-format=absolute", "--git-common-dir"]).filter(|s| !s.is_empty()) {
                Some(common) => {
                    let common = PathBuf::from(common);
                    main_repo = json!(display(common.parent().unwrap_or(&common)));
                }
                None => {
                    if let Some(caps) = WORKTREE_MAIN.captures(&pointer) {
                        main_repo = json!(caps[1].trim());
                    }
                }
            }
        } else {
            kind = "git submodule";
            main_repo = git(dir, &["rev-parse", "--show-superproject-working-tree"])
                .filter(|s| !s.is_empty())
                .map_or(Value::Null, Value::from);
        }
    }
    let branch = git(dir, &["rev-parse", "--abbrev-ref", "HEAD"]).filter(|s| !s.is_empty());
    let head_sha = git(dir, &["rev-parse", "HEAD"])
        .filter(|s| !s.is_empty())
        .map_or(Value::Null, Value::from);
    let dirty = git(dir, &["status", "--porcelain"]).map_or(Value::Null, |s| json!(!s.is_empty()));
    let mut remotes: Vec<String> = Vec::new();
    if let Some(out) = git(dir, &["remote", "-v"]).filter(|s| !s.is_empty()) {
        for line in out.lines() {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if let Some(hp) = parts.get(1).and_then(|url| host_path_from_remote(url))
                && !remotes.contains(&hp)
            {
                remotes.push(hp);
            }
        }
    }
    let last_commit_time = git(dir, &["log", "-1", "--format=%cI"]).filter(|s| !s.is_empty());
    let mut fields = serde_json::Map::new();
    fields.insert("kind".into(), json!(kind));
    fields.insert("main_repo".into(), main_repo);
    fields.insert("branch".into(), branch.map_or(Value::Null, Value::from));
    fields.insert("head_sha".into(), head_sha.clone());
    fields.insert("dirty".into(), dirty);
    fields.insert("remotes".into(), json!(remotes));
    fields.insert(
        "last_commit_time".into(),
        last_commit_time.map_or(Value::Null, Value::from),
    );
    GitInfo {
        kind,
        fields,
        has_remote: !remotes.is_empty(),
        head_sha,
    }
}

fn npm_test_script_is_real(package_json: &Path) -> bool {
    let Ok(bytes) = std::fs::read(package_json) else {
        return false;
    };
    let Ok(data) = serde_json::from_str::<Value>(&String::from_utf8_lossy(&bytes)) else {
        return false;
    };
    let script = data["scripts"]["test"].as_str().unwrap_or("");
    !script.is_empty() && !script.to_lowercase().contains("no test specified")
}

fn make_has_test_target(makefile: &Path) -> bool {
    std::fs::read(makefile).is_ok_and(|b| MAKE_TEST_TARGET.is_match(&String::from_utf8_lossy(&b)))
}

fn build_kinds(name: &str) -> impl Iterator<Item = &'static str> {
    let checks: [(&'static str, bool); 8] = [
        ("pyproject", name == "pyproject.toml"),
        ("cargo", name == "Cargo.toml"),
        ("npm", name == "package.json"),
        ("go", name == "go.mod"),
        ("makefile", matches!(name, "Makefile" | "makefile" | "GNUmakefile")),
        ("cmake", name == "CMakeLists.txt"),
        ("sln", name.ends_with(".sln")),
        ("docker_compose", {
            let lower = name.to_lowercase();
            COMPOSE.iter().any(|g| g.matches(&lower))
        }),
    ];
    checks.into_iter().filter_map(|(kind, hit)| hit.then_some(kind))
}

fn is_k8s_yaml(rel_parts: &[String], name: &str) -> bool {
    if !(name.ends_with(".yaml") || name.ends_with(".yml")) {
        return false;
    }
    if matches!(name.to_lowercase().as_str(), "kustomization.yaml" | "kustomization.yml") {
        return true;
    }
    rel_parts
        .iter()
        .any(|p| matches!(p.to_lowercase().as_str(), "k8s" | "kubernetes"))
}

#[derive(Default)]
struct ProjectFiles {
    /// kind -> relative paths, in walk order.
    build_files: BTreeMap<String, Vec<String>>,
    docs: Vec<Value>,
    secret_flags: Vec<String>,
}

fn collect_in_dir(root: &Path, cur: &Path, filenames: &[String], files: &mut ProjectFiles) {
    let rel_dir = cur.strip_prefix(root).unwrap_or(Path::new(""));
    let rel_parts: Vec<String> = rel_dir
        .components()
        .map(|c| c.as_os_str().to_string_lossy().into_owned())
        .collect();
    for name in filenames {
        let relpath = if rel_parts.is_empty() {
            name.clone()
        } else {
            display(&rel_dir.join(name))
        };
        for kind in build_kinds(name) {
            files.build_files.entry(kind.into()).or_default().push(relpath.clone());
        }
        if is_k8s_yaml(&rel_parts, name) {
            files.build_files.entry("k8s".into()).or_default().push(relpath.clone());
        }
        let lname = name.to_lowercase();
        let is_doc = lname.starts_with("readme")
            || matches!(lname.as_str(), "claude.md" | "agents.md")
            || (name.ends_with(".md") && rel_parts.iter().any(|p| p.to_lowercase() == "docs"));
        if is_doc {
            let size = std::fs::metadata(cur.join(name)).map_or(Value::Null, |m| json!(m.len()));
            files.docs.push(json!({ "path": relpath.clone(), "size": size }));
        }
        if SECRETS.iter().any(|g| g.matches(&lname)) {
            files.secret_flags.push(relpath);
        }
    }
}

fn candidate_gates(build_files: &BTreeMap<String, Vec<String>>, root: &Path) -> Vec<&'static str> {
    let mut gates = Vec::new();
    let has = |kind: &str| build_files.contains_key(kind);
    if has("pyproject") {
        gates.push("pytest");
    }
    if has("cargo") {
        gates.push("cargo test");
    }
    if build_files
        .get("npm")
        .is_some_and(|paths| paths.iter().any(|p| npm_test_script_is_real(&root.join(p))))
    {
        gates.push("npm test");
    }
    if has("go") {
        gates.push("go test ./...");
    }
    if build_files
        .get("makefile")
        .is_some_and(|paths| paths.iter().any(|p| make_has_test_target(&root.join(p))))
    {
        gates.push("make test");
    }
    if has("cmake") {
        gates.push("ctest");
    }
    if has("sln") {
        gates.push("dotnet test (unverified)");
    }
    gates
}

fn has_git_entry(dir: &Path) -> bool {
    dir.join(".git").exists()
}

fn should_skip_dir(name: &str) -> bool {
    name == ".git" || SKIP_DIRS.contains(&name) || SKIP_GLOBS.iter().any(|g| g.matches(name))
}

/// One directory listing: (dirs, files) in the order the OS returns them, like `os.scandir`.
/// A symlink or junction to a directory is listed as a directory.
fn list_dir(dir: &Path) -> Option<(Vec<String>, Vec<String>)> {
    let entries = std::fs::read_dir(dir).ok()?;
    let (mut dirs, mut files) = (Vec::new(), Vec::new());
    for entry in entries {
        let entry = entry.ok()?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if std::fs::metadata(entry.path()).is_ok_and(|m| m.is_dir()) {
            dirs.push(name);
        } else {
            files.push(name);
        }
    }
    Some((dirs, files))
}

fn is_link(path: &Path) -> bool {
    std::fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_symlink())
}

fn is_local_only(path: &Path, is_git: bool, has_remote: bool, local_only: &[Glob]) -> bool {
    let text = display(path);
    local_only.iter().any(|g| g.matches(&text)) || (is_git && !has_remote)
}

fn scan_tree(root: &Path, local_only: &[Glob], out: &mut Vec<Value>) {
    let info = has_git_entry(root).then(|| git_info(root));
    let kind = info.as_ref().map_or("plain folder", |i| i.kind);
    if is_local_only(
        root,
        info.is_some(),
        info.as_ref().is_some_and(|i| i.has_remote),
        local_only,
    ) {
        out.push(json!({
            "path": display(root),
            "kind": kind,
            "head_sha": info.as_ref().map_or(Value::Null, |i| i.head_sha.clone()),
            "local_only": true,
        }));
        return;
    }
    let mut files = ProjectFiles::default();
    let mut nested = Vec::new();
    // Pre-order, children in listing order: the order `os.walk(topdown=True)` yields.
    let mut stack = vec![root.to_path_buf()];
    while let Some(cur) = stack.pop() {
        let Some((dirs, filenames)) = list_dir(&cur) else {
            continue;
        };
        let dirs: Vec<String> = dirs.into_iter().filter(|d| !should_skip_dir(d)).collect();
        if cur != root && has_git_entry(&cur) {
            scan_tree(&cur, local_only, &mut nested);
            continue;
        }
        collect_in_dir(root, &cur, &filenames, &mut files);
        for d in dirs.iter().rev() {
            let next = cur.join(d);
            if !is_link(&next) {
                stack.push(next);
            }
        }
    }
    let mut record = serde_json::Map::new();
    record.insert("path".into(), json!(display(root)));
    record.insert("kind".into(), json!("plain folder"));
    for key in ["main_repo", "branch", "head_sha", "dirty", "last_commit_time"] {
        record.insert(key.into(), Value::Null);
    }
    record.insert("remotes".into(), json!([]));
    record.insert(
        "candidate_gates".into(),
        json!(candidate_gates(&files.build_files, root)),
    );
    record.insert("build_files".into(), json!(files.build_files));
    record.insert("docs".into(), Value::Array(files.docs));
    record.insert(
        "has_claude_agents".into(),
        json!(root.join(".claude").join("agents").is_dir()),
    );
    record.insert(
        "has_claude_skills".into(),
        json!(root.join(".claude").join("skills").is_dir()),
    );
    record.insert("secret_flags".into(), json!(files.secret_flags));
    record.insert("local_only".into(), json!(false));
    if let Some(info) = info {
        record.extend(info.fields);
    }
    out.push(Value::Object(record));
    out.extend(nested);
}

/// Every project record under `root`: its immediate children (sorted as Python sorts Windows
/// paths, case-insensitively) and every project nested beneath them, except beneath a local-only one.
pub fn scan_root(root: &Path, local_only_globs: &[String]) -> std::io::Result<Vec<Value>> {
    let globs: Vec<Glob> = local_only_globs.iter().map(|g| Glob::new(g)).collect();
    let mut children: Vec<PathBuf> = std::fs::read_dir(root)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .collect();
    children.sort_by_key(|p| normcase(&display(p)));
    let mut records = Vec::new();
    for child in children {
        let name = child.file_name().unwrap_or_default().to_string_lossy().into_owned();
        if !child.is_dir() || should_skip_dir(&name) {
            continue;
        }
        scan_tree(&child, &globs, &mut records);
    }
    Ok(records)
}

/// The scan record of one folder, exactly as `scan_root` makes it for each of its children (a local-only
/// folder gets the short record with `local_only: true` and is not walked). Projects nested beneath it
/// are not returned.
pub fn scan_one(root: &Path, local_only_globs: &[String]) -> Option<Value> {
    let globs: Vec<Glob> = local_only_globs.iter().map(|g| Glob::new(g)).collect();
    let mut records = Vec::new();
    scan_tree(root, &globs, &mut records);
    records.into_iter().next()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn globs_follow_fnmatch() {
        assert!(Glob::new("*key*").matches("monkey_patch.py"));
        assert!(Glob::new(".env.*").matches(".env.local"));
        assert!(!Glob::new(".env.*").matches(".env"));
        assert!(Glob::new("build_*").matches("build_w6100"));
        assert!(Glob::new("[!a]bc").matches("xbc"));
        assert!(!Glob::new("[!a]bc").matches("abc"));
        assert!(Glob::new("file[").matches("file["));
        if cfg!(windows) {
            assert!(Glob::new("*Family*").matches(r"C:\x\family stuff"));
        }
    }

    #[test]
    fn remotes_parse_like_the_python() {
        assert_eq!(
            host_path_from_remote("git@github.com:user/repo.git").as_deref(),
            Some("github.com/user/repo")
        );
        assert_eq!(
            host_path_from_remote("https://github.com/user/repo.git").as_deref(),
            Some("github.com/user/repo")
        );
        assert_eq!(host_path_from_remote("ssh://git@h/p").as_deref(), Some("h/p"));
        assert_eq!(host_path_from_remote("").as_deref(), None);
    }

    #[test]
    fn a_local_only_project_reveals_nothing_inside() {
        let base = std::env::temp_dir().join(format!("gw-scan-test-{}", std::process::id()));
        let secret = base.join("private").join("inner");
        std::fs::create_dir_all(&secret).unwrap();
        std::fs::write(secret.join("Cargo.toml"), "").unwrap();
        std::fs::write(base.join("private").join("README.md"), "x").unwrap();
        let open = base.join("open");
        std::fs::create_dir_all(open.join("docs")).unwrap();
        std::fs::write(open.join("docs").join("guide.md"), "x").unwrap();
        std::fs::write(open.join(".env"), "").unwrap();
        let records = scan_root(&base, &["*private*".to_owned()]).unwrap();
        assert_eq!(records.len(), 2);
        let private = &records[1];
        assert_eq!(private["local_only"], json!(true));
        assert_eq!(
            private.as_object().unwrap().len(),
            4,
            "path, kind, head_sha, local_only only"
        );
        let open_rec = &records[0];
        assert_eq!(open_rec["kind"], json!("plain folder"));
        assert_eq!(open_rec["secret_flags"], json!([".env"]));
        assert_eq!(open_rec["docs"][0]["size"], json!(1));
        std::fs::remove_dir_all(&base).unwrap();
    }
}
