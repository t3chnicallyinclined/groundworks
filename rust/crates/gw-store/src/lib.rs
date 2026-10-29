//! gw-store -- the Groundworks data model in SurrealDB: scopes, versioned migrations, and member
//! credentials.
//!
//! One SurrealDB holds three kinds of namespace, all using database [`DB_NAME`]:
//! `global` (the hub: registry, scope policy, walls, cross-project knowledge), `ws_<name>` (a
//! workspace: what its projects shared among themselves) and `proj_<slug>` (one project's own
//! knowledge). An isolated project gets its own server and store; the schema is the same.
//!
//! Ported from the 2026-09-17 Python `xscholar/product/model.py` (branch `product/data-model`,
//! 3ddbd34): migrations are `schema/NNNN_<name>.surql` with a `-- scope:` line;
//! `schema_version:current` records the product, the schema major and the last file applied; each
//! file and its version bump run in ONE transaction, so applying twice is a no-op and a failed file
//! leaves the version where it was. A database on another schema major, or a server on another
//! SurrealDB major, is refused before any migration runs. The schema is compiled in, so a binary
//! always migrates with the files it was tested against.
//!
//! Roles are `owner` (the human) and `agent`. Root is the migration and loader identity only.

use std::path::PathBuf;
use std::time::Duration;

use gw_core::db::{Db, DbError};
use serde_json::Value;

pub const PRODUCT: &str = "groundworks";
pub const DB_NAME: &str = "gw";
/// Bump only with a migration path; a database on another major is refused.
pub const SCHEMA_MAJOR: i64 = 1;
/// The behaviour the schema relies on is measured on 3.1.6.
pub const SURREAL_MAJOR: u32 = 3;
pub const GLOBAL_NS: &str = "global";
pub const ACCESS: &str = "member";
pub const ROLES: [&str; 2] = ["owner", "agent"];

/// The schema files, compiled in.
pub const SCHEMA: &[(&str, &str)] = &[
    ("0001_global.surql", include_str!("../schema/0001_global.surql")),
    ("0002_workspace.surql", include_str!("../schema/0002_workspace.surql")),
    (
        "0003_project_base.surql",
        include_str!("../schema/0003_project_base.surql"),
    ),
    (
        "0004_project_rules.surql",
        include_str!("../schema/0004_project_rules.surql"),
    ),
    (
        "0005_project_journal.surql",
        include_str!("../schema/0005_project_journal.surql"),
    ),
    (
        "0006_project_provenance.surql",
        include_str!("../schema/0006_project_provenance.surql"),
    ),
    (
        "0007_project_intake.surql",
        include_str!("../schema/0007_project_intake.surql"),
    ),
    (
        "0008_project_search.surql",
        include_str!("../schema/0008_project_search.surql"),
    ),
    ("0009_global_ask.surql", include_str!("../schema/0009_global_ask.surql")),
    (
        "0010_project_lens_panel.surql",
        include_str!("../schema/0010_project_lens_panel.surql"),
    ),
    (
        "0011_project_fact_search.surql",
        include_str!("../schema/0011_project_fact_search.surql"),
    ),
    (
        "0012_project_unit_changed.surql",
        include_str!("../schema/0012_project_unit_changed.surql"),
    ),
    (
        "0013_global_ask_fact.surql",
        include_str!("../schema/0013_global_ask_fact.surql"),
    ),
    (
        "0014_global_telemetry.surql",
        include_str!("../schema/0014_global_telemetry.surql"),
    ),
];

/// Created by the migration engine itself, before the first file, so the version can be read on any database.
const BOOTSTRAP: &str = "DEFINE TABLE IF NOT EXISTS schema_version TYPE NORMAL SCHEMAFULL;\
ALTER TABLE schema_version PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent'] FOR create, update, delete NONE;\
DEFINE FIELD IF NOT EXISTS product ON schema_version TYPE string;\
DEFINE FIELD IF NOT EXISTS scope ON schema_version TYPE string;\
DEFINE FIELD IF NOT EXISTS major ON schema_version TYPE int;\
DEFINE FIELD IF NOT EXISTS applied ON schema_version TYPE int;\
DEFINE FIELD IF NOT EXISTS files ON schema_version TYPE array<string>;\
DEFINE FIELD IF NOT EXISTS updated_at ON schema_version TYPE datetime;";

pub mod scratch;
#[cfg(feature = "testkit")]
pub mod testkit;

#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error(transparent)]
    Db(#[from] DbError),
    #[error("{0}")]
    Model(String),
    #[error("{0}")]
    WrongMajor(String),
}

pub type Result<T> = std::result::Result<T, StoreError>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scope {
    Global,
    Workspace,
    Project,
}

impl Scope {
    pub fn as_str(self) -> &'static str {
        match self {
            Scope::Global => "global",
            Scope::Workspace => "workspace",
            Scope::Project => "project",
        }
    }

    fn parse(s: &str) -> Option<Self> {
        match s {
            "global" => Some(Scope::Global),
            "workspace" => Some(Scope::Workspace),
            "project" => Some(Scope::Project),
            _ => None,
        }
    }
}

fn is_slug(s: &str) -> bool {
    (1..=40).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}

pub fn workspace_ns(name: &str) -> Result<String> {
    if !is_slug(name) {
        return Err(StoreError::Model(format!(
            "workspace name {name:?} must be 1-40 of a-z 0-9 _"
        )));
    }
    Ok(format!("ws_{name}"))
}

pub fn project_ns(slug: &str) -> Result<String> {
    if !is_slug(slug) {
        return Err(StoreError::Model(format!(
            "project slug {slug:?} must be 1-40 of a-z 0-9 _"
        )));
    }
    Ok(format!("proj_{slug}"))
}

/// Which kind of namespace `ns` is; anything else is refused.
pub fn scope_of(ns: &str) -> Result<Scope> {
    if ns == GLOBAL_NS {
        return Ok(Scope::Global);
    }
    if let Some(rest) = ns.strip_prefix("ws_").filter(|r| is_slug(r)) {
        let _ = rest;
        return Ok(Scope::Workspace);
    }
    if ns.strip_prefix("proj_").is_some_and(is_slug) {
        return Ok(Scope::Project);
    }
    Err(StoreError::Model(format!(
        "namespace {ns:?} is not global, ws_<name> or proj_<slug>"
    )))
}

/// Root connection: schema, migrations and loads only.
pub fn connect_root(url: &str, ns: &str, user: &str, password: &str) -> Result<Db> {
    scope_of(ns)?;
    Ok(Db::new(url, ns, DB_NAME, user, password, Duration::from_secs(30), None))
}

/// A member credential signed in through `DEFINE ACCESS member` on (ns, gw): the identity agents
/// and the owner work under, with every PERMISSIONS clause applied.
pub fn connect_member(url: &str, ns: &str, member: &str, password: &str) -> Result<Db> {
    scope_of(ns)?;
    Ok(Db::new(
        url,
        ns,
        DB_NAME,
        member,
        password,
        Duration::from_secs(30),
        Some(ACCESS),
    ))
}

/// `[(number, file name, text)]` for one scope, in order, from `files` (default: the compiled-in
/// schema). A badly named file, a number used twice, or a missing scope line is refused.
pub fn migrations(scope: Scope, files: &[(&str, &str)]) -> Result<Vec<(i64, String, String)>> {
    let mut seen: std::collections::BTreeMap<i64, &str> = Default::default();
    let mut out = Vec::new();
    let mut sorted: Vec<&(&str, &str)> = files.iter().collect();
    sorted.sort_by_key(|(name, _)| *name);
    for (name, text) in sorted {
        let number = name
            .split_once('_')
            .filter(|(n, rest)| n.len() == 4 && rest.ends_with(".surql"))
            .and_then(|(n, _)| n.parse::<i64>().ok())
            .ok_or_else(|| StoreError::Model(format!("migration file {name} is not named NNNN_name.surql")))?;
        if let Some(previous) = seen.insert(number, name) {
            return Err(StoreError::Model(format!(
                "migration number {number:04} used twice: {previous} and {name}"
            )));
        }
        let file_scope = text
            .lines()
            .find_map(|l| {
                l.trim()
                    .strip_prefix("--")
                    .map(str::trim)
                    .and_then(|l| l.strip_prefix("scope:"))
            })
            .map(str::trim)
            .and_then(Scope::parse)
            .ok_or_else(|| {
                StoreError::Model(format!(
                    "migration {name} has no '-- scope: global|workspace|project' line"
                ))
            })?;
        if file_scope == scope {
            out.push((number, (*name).to_owned(), (*text).to_owned()));
        }
    }
    Ok(out)
}

/// Refuse a server whose major is not [`SURREAL_MAJOR`].
pub fn check_server(version: &str) -> Result<()> {
    let major = version
        .trim_start_matches(|c: char| !c.is_ascii_digit())
        .split('.')
        .next()
        .and_then(|m| m.parse::<u32>().ok());
    if major != Some(SURREAL_MAJOR) {
        return Err(StoreError::WrongMajor(format!(
            "SurrealDB {version:?} refused: this data model is measured on {SURREAL_MAJOR}.x only"
        )));
    }
    Ok(())
}

/// Create the namespace and database if missing (root only).
pub fn ensure(root: &Db, ns: &str) -> Result<()> {
    scope_of(ns)?;
    root.sql(
        &format!("DEFINE NAMESPACE IF NOT EXISTS {ns}; USE NS {ns}; DEFINE DATABASE IF NOT EXISTS {DB_NAME};"),
        None,
    )?;
    Ok(())
}

/// The `schema_version:current` row, or None on a database never migrated.
pub fn current_version(root: &Db) -> Result<Option<Value>> {
    root.sql(BOOTSTRAP, None)?;
    let rows = root.sql("SELECT * FROM ONLY schema_version:current;", Some(1))?;
    Ok(rows.into_iter().next().filter(|v| !v.is_null()))
}

/// Apply every migration of `scope` above the recorded one, in order, from `files`. Returns the
/// numbers applied (empty = nothing to do).
pub fn migrate(root: &Db, scope: Scope, files: &[(&str, &str)], check_server_version: bool) -> Result<Vec<i64>> {
    if check_server_version {
        check_server(&root.version()?)?;
    }
    let current = current_version(root)?;
    if let Some(current) = &current {
        if current["product"] != PRODUCT || current["scope"] != scope.as_str() {
            return Err(StoreError::WrongMajor(format!(
                "database holds {}/{}, not {PRODUCT}/{}",
                current["product"],
                current["scope"],
                scope.as_str()
            )));
        }
        if current["major"].as_i64() != Some(SCHEMA_MAJOR) {
            return Err(StoreError::WrongMajor(format!(
                "database is schema major {}; this code is major {SCHEMA_MAJOR}. Refused before any migration ran.",
                current["major"]
            )));
        }
    }
    let mut last = current.as_ref().and_then(|c| c["applied"].as_i64()).unwrap_or(0);
    let mut applied_files: Vec<String> = current
        .as_ref()
        .and_then(|c| c["files"].as_array().cloned())
        .unwrap_or_default()
        .into_iter()
        .filter_map(|v| v.as_str().map(str::to_owned))
        .collect();
    let mut applied = Vec::new();
    for (number, name, text) in migrations(scope, files)? {
        if number <= last {
            continue;
        }
        applied_files.push(name.clone());
        let vars = serde_json::Map::from_iter([
            ("product".to_owned(), Value::from(PRODUCT)),
            ("scope".to_owned(), Value::from(scope.as_str())),
            ("major".to_owned(), Value::from(SCHEMA_MAJOR)),
            ("applied".to_owned(), Value::from(number)),
            ("files".to_owned(), Value::from(applied_files.clone())),
        ]);
        let bump = "UPSERT schema_version:current CONTENT {product: $product, scope: $scope, major: $major, \
                    applied: $applied, files: $files, updated_at: time::now()};";
        root.query(
            &format!("BEGIN TRANSACTION;\n{text}\n;{bump}\nCOMMIT TRANSACTION;"),
            &vars,
            None,
        )
        .map_err(|e| {
            StoreError::Model(format!(
                "migration {name} failed; schema_version stays at {last:04}: {e}"
            ))
        })?;
        last = number;
        applied.push(number);
    }
    Ok(applied)
}

/// Create the namespace and database if needed and migrate it to the compiled-in schema (root only).
///
/// Several sessions may open the same new namespace at once. SurrealDB then refuses all but one
/// with a write conflict (measured 2026-10-25: three of four concurrent opens failed). Each attempt is
/// safe to repeat, because `migrate` re-reads the recorded version, so a conflict is retried with a
/// short randomised back-off; any other failure is returned at once.
pub fn open_namespace(root: &Db, ns: &str) -> Result<Vec<i64>> {
    const ATTEMPTS: u32 = 8;
    let mut attempt = 0;
    loop {
        attempt += 1;
        let result = ensure(root, ns).and_then(|()| migrate(root, scope_of(ns)?, SCHEMA, true));
        match result {
            Err(e) if attempt < ATTEMPTS && is_conflict(&e) => {
                std::thread::sleep(Duration::from_millis(u64::from(attempt) * 40 + jitter_ms()));
            }
            other => return other,
        }
    }
}

/// A transaction that lost a race and may simply be run again.
fn is_conflict(e: &StoreError) -> bool {
    let text = e.to_string();
    ["conflict", "failed transaction", "retry the transaction"]
        .iter()
        .any(|needle| text.contains(needle))
}

/// 0-31 ms from the clock's low bits: enough to break lockstep between processes.
fn jitter_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| u64::from(d.subsec_nanos() % 32))
}

/// The `member` access as every scope's schema defines it, with no KEY clause: SurrealDB generates
/// a fresh key when the statement runs. A test holds the schema files to this text.
pub const MEMBER_ACCESS_BODY: &str = "ON DATABASE TYPE RECORD\n  \
SIGNIN (SELECT * FROM member WHERE id = type::record('member', $id) AND enabled = true AND crypto::argon2::compare(pass, $pass))\n  \
DURATION FOR TOKEN 8h, FOR SESSION 8h;";

/// Define the `member` access again with a fresh key (root only). `surreal export` 3.1.6 writes the
/// access with `KEY '[REDACTED]'`, so a database restored from an export would sign every token with
/// a published string until this runs. The restore drill runs it after each import and then proves it
/// by signing the agent credential in.
pub fn redefine_access(root: &Db) -> Result<()> {
    root.sql(&format!("DEFINE ACCESS OVERWRITE {ACCESS} {MEMBER_ACCESS_BODY}"), None)?;
    Ok(())
}

/// Create or reset one member credential in root's namespace (root only).
pub fn provision(root: &Db, member: &str, role: &str, password: &str) -> Result<()> {
    let plain = !member.is_empty()
        && member.len() <= 80
        && member.chars().next().is_some_and(|c| c.is_ascii_alphanumeric())
        && member
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-'));
    if !plain {
        return Err(StoreError::Model(format!(
            "member id {member:?} is not a plain identifier"
        )));
    }
    if !ROLES.contains(&role) {
        return Err(StoreError::Model(format!("role {role:?} is not one of {ROLES:?}")));
    }
    let vars = serde_json::Map::from_iter([
        ("member".to_owned(), Value::from(member)),
        ("role".to_owned(), Value::from(role)),
        ("pass".to_owned(), Value::from(password)),
    ]);
    root.query(
        "UPSERT type::record('member', $member) SET role = $role, pass = crypto::argon2::generate($pass), enabled = true;",
        &vars,
        Some(1),
    )?;
    Ok(())
}

/// The SurrealDB binary: `GROUNDWORKS_SURREAL`, then `PATH`, then the Windows installer's location.
pub fn find_surreal() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("GROUNDWORKS_SURREAL")
        .map(PathBuf::from)
        .filter(|p| p.exists())
    {
        return Some(path);
    }
    let exe = if cfg!(windows) { "surreal.exe" } else { "surreal" };
    if let Some(found) = std::env::var_os("PATH")
        .iter()
        .flat_map(std::env::split_paths)
        .map(|dir| dir.join(exe))
        .find(|p| p.is_file())
    {
        return Some(found);
    }
    let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME"))?;
    let installed = PathBuf::from(home)
        .join("AppData")
        .join("Local")
        .join("SurrealDB")
        .join("surreal.exe");
    installed.is_file().then_some(installed)
}

/// Whether a SurrealDB answers `/health` at the base of `url` (`.../sql` or the bare origin).
pub fn healthy(url: &str) -> bool {
    let base = url.strip_suffix("/sql").unwrap_or(url.trim_end_matches('/'));
    ureq::get(&format!("{base}/health"))
        .timeout(Duration::from_millis(800))
        .call()
        .is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_schema_defines_the_member_access_exactly_as_redefine_access_does() {
        let squash = |t: &str| t.split_whitespace().collect::<Vec<_>>().join(" ");
        let want = squash(&format!("DEFINE ACCESS IF NOT EXISTS {ACCESS} {MEMBER_ACCESS_BODY}"));
        let defining: Vec<&str> = SCHEMA
            .iter()
            .filter(|(_, text)| text.contains("DEFINE ACCESS"))
            .map(|(name, _)| *name)
            .collect();
        assert_eq!(defining.len(), 3, "one access definition per scope: {defining:?}");
        for (name, text) in SCHEMA.iter().filter(|(_, t)| t.contains("DEFINE ACCESS")) {
            assert!(
                squash(text).contains(&want),
                "{name} defines the member access differently from redefine_access"
            );
        }
    }

    #[test]
    fn namespaces_are_checked() {
        assert_eq!(scope_of("global").unwrap(), Scope::Global);
        assert_eq!(scope_of("ws_fighting_games").unwrap(), Scope::Workspace);
        assert_eq!(scope_of("proj_supergun").unwrap(), Scope::Project);
        assert!(scope_of("workspace").is_err());
        assert!(scope_of("proj_Bad").is_err());
        assert!(project_ns("a-b").is_err());
        assert_eq!(workspace_ns("nobd").unwrap(), "ws_nobd");
    }

    #[test]
    fn every_compiled_file_has_a_scope() {
        let total: usize = [Scope::Global, Scope::Workspace, Scope::Project]
            .iter()
            .map(|s| migrations(*s, SCHEMA).unwrap().len())
            .sum();
        assert_eq!(total, SCHEMA.len());
        assert!(migrations(Scope::Project, &[("0009_x.surql", "DEFINE TABLE x;")]).is_err());
        assert!(migrations(Scope::Project, &[("9_x.surql", "-- scope: project")]).is_err());
        assert!(
            migrations(
                Scope::Project,
                &[
                    ("0001_a.surql", "-- scope: project"),
                    ("0001_b.surql", "-- scope: project")
                ]
            )
            .is_err()
        );
    }

    #[test]
    fn server_major_is_checked() {
        assert!(check_server("surrealdb-3.1.6").is_ok());
        assert!(check_server("3.3.4").is_ok());
        assert!(check_server("surrealdb-2.3.0").is_err());
        assert!(check_server("").is_err());
    }
}
