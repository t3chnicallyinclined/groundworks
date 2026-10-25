//! The Groundworks store: one SurrealDB server for every session on this machine, reached over
//! loopback only. Agents work under the `agent` member credential, never root; root is used only
//! to create and migrate a namespace the first time this process touches it. The owner's
//! credential is never stored: owner commands read it from `GROUNDWORKS_OWNER_PASS` at the moment
//! they run.
//!
//! All of this is blocking (the client is `ureq`). Async callers go through [`blocking`].
//!
//! Every agent session runs as the same OS user that can read `<home>/secrets/`, so the
//! root/agent split guards against mistakes, not against a hostile agent; the owner/agent split
//! holds because the owner's password is never on disk.

use std::collections::HashMap;
use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail, ensure};
use gw_core::db::Db;
use gw_store::{GLOBAL_NS, connect_member, connect_root, open_namespace, project_ns, provision};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::home::{Home, machine};
use crate::journal::{Confidence, Kind};

pub const DEFAULT_URL: &str = "http://127.0.0.1:8004/sql";
const AGENT: &str = "agent";
const OWNER: &str = "owner";
const START_TIMEOUT: Duration = Duration::from_secs(20);

/// Run blocking store work off the async runtime's worker threads.
pub async fn blocking<T, F>(f: F) -> Result<T>
where
    F: FnOnce() -> Result<T> + Send + 'static,
    T: Send + 'static,
{
    tokio::task::spawn_blocking(f).await.context("store task panicked")?
}

/// Lowercase; runs of anything outside `[a-z0-9]` become one `_`; at most 40 characters.
pub fn slug(name: &str) -> Result<String> {
    let mut out = String::with_capacity(name.len());
    for c in name.chars().flat_map(char::to_lowercase) {
        if c.is_ascii_alphanumeric() {
            out.push(c);
        } else if !out.ends_with('_') {
            out.push('_');
        }
    }
    let out = out.trim_matches('_').to_owned();
    ensure!(
        (1..=40).contains(&out.len()),
        "project name {name:?} does not make a 1-40 character slug"
    );
    Ok(out)
}

pub struct Store {
    url: String,
    root_pass: String,
    /// The secret every namespace's own agent password is derived from (see [`Store::agent_pass`]).
    agent_master: String,
    /// One signed-in agent client per namespace; its token is reused across calls.
    agents: Mutex<HashMap<String, Arc<Db>>>,
    /// How to start the server again if it stops answering. None for [`Store::connect`].
    launcher: Option<Launcher>,
}

impl Store {
    /// A store whose server is already running and whose passwords are known (never restarted).
    pub fn connect(url: &str, root_pass: &str, agent_master: &str) -> Self {
        Self {
            url: url.to_owned(),
            root_pass: root_pass.to_owned(),
            agent_master: agent_master.to_owned(),
            agents: Mutex::new(HashMap::new()),
            launcher: None,
        }
    }

    /// The home's store: passwords from `<home>/secrets/`, server started if it is not answering,
    /// and started again later if it stops.
    pub fn open(home: &Home) -> Result<Self> {
        Self::with_launcher(Launcher::for_home(home)?, &read_secret(home, "agent")?)
    }

    /// A store that starts its server through `launcher` now if needed, and again if it stops.
    pub fn with_launcher(launcher: Launcher, agent_master: &str) -> Result<Self> {
        launcher.ensure()?;
        let mut store = Self::connect(&launcher.url, &launcher.root_pass, agent_master);
        store.launcher = Some(launcher);
        Ok(store)
    }

    /// The launcher this store restarts its server with, if any.
    pub fn launcher(&self) -> Option<&Launcher> {
        self.launcher.as_ref()
    }

    pub fn url(&self) -> &str {
        &self.url
    }

    /// Run `f`; if the server could not be reached, start it again and run `f` once more.
    fn retrying<T>(&self, f: impl Fn() -> Result<T>) -> Result<T> {
        match f() {
            Err(e) if unreachable(&e) => match &self.launcher {
                Some(launcher) => {
                    launcher
                        .ensure()
                        .with_context(|| format!("restarting the store after: {e:#}"))?;
                    f()
                }
                None => Err(e),
            },
            other => other,
        }
    }

    fn root(&self, ns: &str) -> Result<Db> {
        Ok(connect_root(&self.url, ns, "root", &self.root_pass)?)
    }

    /// Each namespace has its own agent password, derived from the master secret, so a token or
    /// password used in one project's namespace opens no other.
    pub fn agent_pass(&self, ns: &str) -> String {
        sha256_hex(&[self.agent_master.as_bytes(), b"\n", ns.as_bytes()])[..40].to_owned()
    }

    /// The agent client for `ns`, creating and migrating the namespace the first time. An isolated
    /// project is refused: it has a store of its own, and nothing here may read or write it.
    pub fn agent(&self, ns: &str) -> Result<Arc<Db>> {
        if let Some(db) = self.agents.lock().expect("agent cache poisoned").get(ns) {
            return Ok(Arc::clone(db));
        }
        if ns != GLOBAL_NS {
            let level = self.level(ns)?;
            ensure!(
                level != "isolated",
                "{ns} is isolated: it has a store of its own, and this store will not open it"
            );
        }
        let db = self.retrying(|| {
            let root = self.root(ns)?;
            open_namespace(&root, ns).with_context(|| format!("migrating {ns}"))?;
            let pass = self.agent_pass(ns);
            provision(&root, AGENT, "agent", &pass)?;
            Ok(Arc::new(connect_member(&self.url, ns, AGENT, &pass)?))
        })?;
        Ok(Arc::clone(
            self.agents
                .lock()
                .expect("agent cache poisoned")
                .entry(ns.to_owned())
                .or_insert(db),
        ))
    }

    /// The scope's isolation level from the owner's policy (`shared` when none is set).
    pub fn level(&self, ns: &str) -> Result<String> {
        let global = self.agent(GLOBAL_NS)?;
        let vars = Vars::default().set("scope", ns);
        let rows = self.retrying(|| {
            Ok(global.query(
                "SELECT VALUE level FROM ONLY type::record('scope_policy', $scope);",
                &vars.0,
                Some(1),
            )?)
        })?;
        Ok(rows[0].as_str().unwrap_or("shared").to_owned())
    }

    /// The owner client for `ns`. The password is first checked against the owner login in
    /// `global`; only then is the owner login (re)created in `ns` with it.
    pub fn owner(&self, ns: &str, owner_pass: &str) -> Result<Db> {
        let global = connect_member(&self.url, GLOBAL_NS, OWNER, owner_pass)?;
        global
            .sql("SELECT * FROM ONLY $auth;", Some(1))
            .context("the owner password was refused by the owner login in global")?;
        if ns == GLOBAL_NS {
            return Ok(global);
        }
        self.agent(ns)?; // creates and migrates the namespace, and refuses an isolated one
        provision(&self.root(ns)?, OWNER, "owner", owner_pass)?;
        Ok(connect_member(&self.url, ns, OWNER, owner_pass)?)
    }

    /// Create or reset the owner credential in `global` (root; run by the owner at init).
    pub fn provision_owner(&self, owner_pass: &str) -> Result<()> {
        self.agent(GLOBAL_NS)?;
        provision(&self.root(GLOBAL_NS)?, OWNER, "owner", owner_pass)?;
        Ok(())
    }

    /// Record proof that a command ran. Returns the receipt id (`receipt:...`).
    pub fn record_receipt(&self, project: &str, receipt: &ReceiptIn) -> Result<String> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let mut sql = String::from(
            "CREATE receipt SET command = $command, machine = $machine, location = $location, ok = $ok, \
             output_hash = $output_hash, hash_of = $hash_of",
        );
        let mut vars = Vars::default()
            .set("command", receipt.command.as_str())
            .set("machine", receipt.machine.as_str())
            .set("location", receipt.location.as_str())
            .set("ok", receipt.ok)
            .set("output_hash", receipt.output_hash.as_str())
            .set("hash_of", receipt.hash_of);
        if let Some(code) = receipt.exit_code {
            sql.push_str(", exit_code = $exit_code");
            vars = vars.set("exit_code", code);
        }
        if let Some(commit) = &receipt.commit {
            sql.push_str(", commit = $commit");
            vars = vars.set("commit", commit.as_str());
        }
        sql.push_str(" RETURN VALUE id;");
        let rows = self.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?))?;
        record_id(&rows[0]).context("CREATE receipt returned no id")
    }

    /// Record a fact as the agent. The store decides whether it may be CONFIRMED: it needs a
    /// receipt, an owner statement, or a quote that was found word for word (checked here first, so
    /// the refusal can say what the file really contains).
    pub fn add_fact(&self, project_dir: &Path, project: &str, fact: &FactIn) -> Result<String> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        self.retrying(|| self.write_fact(&db, project_dir, fact, None))
    }

    /// Record a fact as the owner, signed by the owner's own login.
    pub fn add_fact_as_owner(
        &self,
        project_dir: &Path,
        project: &str,
        fact: &FactIn,
        owner_pass: &str,
    ) -> Result<String> {
        let db = self.owner(&project_ns(&slug(project)?)?, owner_pass)?;
        self.retrying(|| self.write_fact(&db, project_dir, fact, None))
    }

    /// Import a historical fact with the time and machine it was first recorded on, as the
    /// loader (root; an agent cannot set either). Idempotent: the fact's id comes from the project,
    /// time and text, so importing the same entry twice writes it once. Returns None when it was
    /// already there. The evidence rules apply to root exactly as to an agent.
    pub fn import_fact(
        &self,
        project_dir: &Path,
        project: &str,
        fact: &FactIn,
        at: &str,
        machine: Option<&str>,
    ) -> Result<Option<String>> {
        // Refuse bad input before the duplicate check, so a bad entry is never silently "already there".
        ensure!(!fact.text.trim().is_empty(), "a fact needs text");
        needs_evidence(fact)?;
        let ns = project_ns(&slug(project)?)?;
        self.agent(&ns)?;
        let origin = Origin {
            at: rfc3339(at)?,
            machine: machine.map(str::trim).filter(|m| !m.is_empty()).map(str::to_owned),
            key: sha256_hex(&[project.as_bytes(), b"\n", at.as_bytes(), b"\n", fact.text.as_bytes()])[..32].to_owned(),
        };
        self.retrying(|| {
            let root = self.root(&ns)?;
            let vars = Vars::default().set("key", origin.key.as_str());
            let existing = root.query(
                "SELECT VALUE id FROM ONLY type::record('fact', $key);",
                &vars.0,
                Some(1),
            )?;
            if !existing[0].is_null() {
                return Ok(None);
            }
            self.write_fact(&root, project_dir, fact, Some(&origin)).map(Some)
        })
    }

    fn write_fact(&self, db: &Db, project_dir: &Path, fact: &FactIn, origin: Option<&Origin>) -> Result<String> {
        ensure!(!fact.text.trim().is_empty(), "a fact needs text");
        let quote = fact.quote.as_ref().map(|q| q.verify(project_dir)).transpose()?;
        needs_evidence(fact)?;
        let grade = grade(fact.confidence);
        // The machine is what the writer reports; the store cannot verify a machine name.
        let machine_name = match origin {
            Some(o) => o.machine.clone(),
            None => Some(machine()),
        };
        let mut vars = Vars::default()
            .set("text", fact.text.trim())
            .set("kind", kind(fact.kind))
            .set("first_grade", if quote.is_some() { "INFERRED" } else { grade })
            .set("grade", grade)
            .set("source", fact.source.trim())
            .set("by", fact.by.trim());
        let mut create = match origin {
            Some(_) => String::from("CREATE type::record('fact', $fact_key) SET "),
            None => String::from("CREATE fact SET "),
        };
        create.push_str("text = $text, kind = $kind, grade = $first_grade, source = $source, by = $by");
        if let Some(name) = machine_name {
            create.push_str(", machine = $machine");
            vars = vars.set("machine", name);
        }
        if let Some(id) = &fact.receipt {
            create.push_str(", receipt = type::record('receipt', $receipt_key)");
            vars = vars.set("receipt_key", receipt_key(id)?);
        }
        if let Some(said) = &fact.owner_statement {
            create.push_str(", owner_statement = $owner_statement");
            vars = vars.set("owner_statement", said.as_str());
        }
        if let Some(origin) = origin {
            create.push_str(", at = <datetime>$at");
            vars = vars.set("at", origin.at.as_str()).set("fact_key", origin.key.as_str());
        }
        let mut sql = format!("BEGIN TRANSACTION;\nLET $f = ({create} RETURN VALUE id)[0];\n");
        if let Some(q) = &quote {
            // RELATE takes a variable or a literal record, not a function call.
            sql.push_str(
                "LET $u = type::record('unit', $unit_key);\n\
                 UPSERT $u SET locator = $locator, hash = $hash, text = $quote_text, kind = 'quote', read_at = time::now();\n\
                 RELATE $f->evidence->$u SET hash_at_cite = $hash;\n\
                 UPDATE $f SET grade = $grade, verified_at = time::now();\n",
            );
            vars = vars
                .set("unit_key", q.unit_key.as_str())
                .set("locator", q.locator.as_str())
                .set("hash", q.hash.as_str())
                .set("quote_text", q.text.as_str());
        }
        sql.push_str("RETURN $f;\nCOMMIT TRANSACTION;");
        let rows = db.query(&sql, &vars.0, None)?;
        rows.iter()
            .rev()
            .find_map(record_id)
            .context("the fact transaction returned no id")
    }

    /// The newest `limit` facts, oldest first, optionally of one kind.
    pub fn facts(&self, project: &str, kind_filter: Option<Kind>, limit: usize) -> Result<Vec<FactRow>> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let mut vars = Vars::default().set("limit", limit);
        let filter = match kind_filter {
            Some(k) => {
                vars = vars.set("kind", kind(k));
                "WHERE kind = $kind "
            }
            None => "",
        };
        let rows = self.retrying(|| {
            Ok(db.query(
                &format!(
                    "SELECT <string> id AS id, <string> at AS at, kind, text, grade, status, source, by, machine, \
                 owner_statement, <string> written_by AS written_by, <string> receipt AS receipt, \
                 ->evidence->unit.locator AS quotes FROM fact {filter}ORDER BY at DESC LIMIT $limit;"
                ),
                &vars.0,
                Some(1),
            )?)
        })?;
        let mut facts: Vec<FactRow> = serde_json::from_value(rows[0].clone()).context("reading facts")?;
        facts.reverse();
        Ok(facts)
    }

    /// A one-line health and size report.
    pub fn status(&self) -> Result<String> {
        let root = self.root(GLOBAL_NS)?;
        let version = root.version()?;
        let info = root.sql("INFO FOR ROOT;", Some(1))?;
        let namespaces: Vec<String> = info[0]["namespaces"]
            .as_object()
            .map(|m| m.keys().cloned().collect())
            .unwrap_or_default();
        Ok(format!(
            "store {} ({version}): namespaces {}",
            self.url,
            namespaces.join(", ")
        ))
    }

    /// Set a scope's isolation level, as the owner.
    pub fn set_level(&self, owner_pass: &str, scope: &str, level: &str, workspace: Option<&str>) -> Result<()> {
        let kind = gw_store::scope_of(scope)?.as_str();
        let owner = self.owner(GLOBAL_NS, owner_pass)?;
        let mut sql =
            String::from("UPSERT type::record('scope_policy', $scope) SET name = $scope, kind = $kind, level = $level");
        let mut vars = Vars::default()
            .set("scope", scope)
            .set("kind", kind)
            .set("level", level);
        if let Some(ws) = workspace {
            sql.push_str(", workspace = $workspace");
            vars = vars.set("workspace", ws);
        }
        sql.push(';');
        owner.query(&sql, &vars.0, Some(1))?;
        Ok(())
    }

    /// Write an intake: new and changed units in batches (unchanged ones are skipped by hash; a
    /// changed one marks the facts citing it stale), units of vanished files marked not present, and
    /// one intake_run row.
    pub fn intake(&self, project: &str, c: &crate::intake::Collected) -> Result<IntakeReport> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let existing = self.retrying(|| {
            Ok(db.query(
                "SELECT VALUE [record::id(id), hash] FROM unit WHERE kind != 'quote';",
                &serde_json::Map::new(),
                Some(1),
            )?)
        })?;
        let known: HashMap<String, String> = existing[0]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|pair| Some((pair[0].as_str()?.to_owned(), pair[1].as_str()?.to_owned())))
            .collect();
        let changed: Vec<&crate::intake::Unit> =
            c.units.iter().filter(|u| known.get(&u.key) != Some(&u.hash)).collect();
        for batch in changed.chunks(200) {
            let rows: Vec<Value> = batch
                .iter()
                .map(|u| {
                    let mut v = serde_json::to_value(u).expect("a unit serializes");
                    v["present"] = Value::Bool(true);
                    v["read_at"] = Value::Null;
                    if let Some(map) = v.as_object_mut() {
                        map.retain(|_, value| !value.is_null());
                    }
                    serde_json::json!({"key": u.key, "v": v})
                })
                .collect();
            let vars = Vars::default().set("rows", rows);
            self.retrying(|| {
                Ok(db.query(
                    "FOR $r IN $rows { UPSERT type::record('unit', $r.key) MERGE $r.v; };",
                    &vars.0,
                    None,
                )?)
            })?;
        }
        let vars = Vars::default().set("paths", c.paths.clone());
        let gone = self.retrying(|| {
            Ok(db.query(
                "LET $gone = (SELECT VALUE id FROM unit WHERE kind IN ['context', 'doc', 'code', 'config'] \
                 AND present = true AND path NOT IN $paths);\n\
                 IF array::len($gone) > 0 { UPDATE $gone SET present = false RETURN NONE; };\n\
                 RETURN array::len($gone);",
                // (The IF matters: an UPDATE of nothing returns empty, which reads as a refused write.)
                &vars.0,
                None,
            )?)
        })?;
        let gone = gone.last().and_then(Value::as_u64).unwrap_or(0) as usize;
        let skipped: usize = c.skipped.values().sum();
        let mut run = String::from(
            "CREATE intake_run SET machine = $machine, files = $files, units = $units, written = $written, \
             gone = $gone, skipped = $skipped",
        );
        let mut vars = Vars::default()
            .set("machine", machine())
            .set("files", c.files_read)
            .set("units", c.units.len())
            .set("written", changed.len())
            .set("gone", gone)
            .set("skipped", skipped);
        if let Some(head) = &c.head {
            run.push_str(", commit = $commit");
            vars = vars.set("commit", head.as_str());
        }
        if let Some(dirty) = c.dirty {
            run.push_str(", dirty = $dirty");
            vars = vars.set("dirty", dirty);
        }
        run.push(';');
        self.retrying(|| Ok(db.query(&run, &vars.0, Some(1))?))?;
        Ok(IntakeReport {
            units: c.units.len(),
            written: changed.len(),
            unchanged: c.units.len() - changed.len(),
            gone,
        })
    }

    /// Re-check every quoted span against the project's files on disk. A span whose text changed, or
    /// whose file or lines are gone, gets the new hash, which marks every fact citing it stale (R1).
    /// Returns how many spans changed.
    pub fn recheck_quotes(&self, project_dir: &Path, project: &str) -> Result<usize> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let rows = self.retrying(|| {
            Ok(db.query(
                "SELECT VALUE [record::id(id), locator, hash] FROM unit WHERE kind = 'quote';",
                &serde_json::Map::new(),
                Some(1),
            )?)
        })?;
        let mut changed = Vec::new();
        for row in rows[0].as_array().into_iter().flatten() {
            let (Some(key), Some(locator), Some(hash)) = (row[0].as_str(), row[1].as_str(), row[2].as_str()) else {
                continue;
            };
            let now = current_span_hash(project_dir, locator);
            if now.as_deref() != Some(hash) {
                changed.push(serde_json::json!({"key": key, "hash": now.unwrap_or_else(|| "gone".to_owned())}));
            }
        }
        if !changed.is_empty() {
            let n = changed.len();
            let vars = Vars::default().set("rows", changed);
            self.retrying(|| {
                Ok(db.query(
                    "FOR $r IN $rows { UPDATE type::record('unit', $r.key) SET hash = $r.hash; };",
                    &vars.0,
                    None,
                )?)
            })?;
            return Ok(n);
        }
        Ok(0)
    }

    /// Full-text search of one project's units that are still present, best first. `expert` keeps
    /// only units in that expert's area; `kinds` keeps only those kinds.
    pub fn search(
        &self,
        project: &str,
        query: &str,
        expert: Option<&str>,
        kinds: &[String],
        limit: usize,
    ) -> Result<Vec<Hit>> {
        ensure!(!query.trim().is_empty(), "search for something");
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let mut sql = String::from(
            "SELECT locator, kind, commit, experts, search::score(1) AS score, \
             search::highlight('\u{ab}', '\u{bb}', 1) AS text FROM unit WHERE text @1@ $q AND present = true",
        );
        let mut vars = Vars::default().set("q", query.trim()).set("limit", limit);
        if let Some(expert) = expert {
            sql.push_str(" AND $expert IN experts");
            vars = vars.set("expert", expert);
        }
        if !kinds.is_empty() {
            sql.push_str(" AND kind IN $kinds");
            vars = vars.set("kinds", kinds.to_vec());
        }
        sql.push_str(" ORDER BY score DESC LIMIT $limit;");
        let rows = self.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?))?;
        let mut hits: Vec<Hit> = serde_json::from_value(rows[0].clone()).context("reading search hits")?;
        for hit in &mut hits {
            hit.project = project.to_owned();
            hit.text = excerpt(&hit.text);
        }
        Ok(hits)
    }

    /// Record the expert directory in `global`: each expert's name, summary and definition file.
    pub fn upsert_experts(&self, experts: &[crate::intake::Expert]) -> Result<()> {
        let global = self.agent(GLOBAL_NS)?;
        let rows: Vec<Value> = experts
            .iter()
            .map(|e| serde_json::json!({"name": e.name, "summary": e.summary, "file": e.file.display().to_string()}))
            .collect();
        let vars = Vars::default().set("rows", rows);
        self.retrying(|| {
            Ok(global.query(
                "FOR $e IN $rows { UPSERT type::record('expert', $e.name) MERGE $e; };",
                &vars.0,
                None,
            )?)
        })?;
        Ok(())
    }

    /// Wall two scopes off from each other, as the owner.
    pub fn add_wall(&self, owner_pass: &str, a: &str, b: &str, why: &str) -> Result<()> {
        gw_store::scope_of(a)?;
        gw_store::scope_of(b)?;
        ensure!(a != b, "a wall needs two different scopes");
        let (a, b) = if a < b { (a, b) } else { (b, a) };
        let vars = Vars::default().set("a", a).set("b", b).set("why", why);
        self.owner(GLOBAL_NS, owner_pass)?
            .query("CREATE wall SET a = $a, b = $b, why = $why;", &vars.0, Some(1))?;
        Ok(())
    }
}

/// Bound variables for [`Db::query`]: values travel as JSON, never inside the statement text.
#[derive(Default)]
struct Vars(serde_json::Map<String, Value>);

impl Vars {
    fn set(mut self, name: &str, value: impl Into<Value>) -> Self {
        self.0.insert(name.to_owned(), value.into());
        self
    }
}

/// When and where a historical fact was first recorded, and its deterministic key (imports only).
struct Origin {
    at: String,
    machine: Option<String>,
    key: String,
}

#[derive(Debug, Clone, Copy)]
pub struct IntakeReport {
    pub units: usize,
    pub written: usize,
    pub unchanged: usize,
    pub gone: usize,
}

/// One search result: where it is, what kind, which experts own it, and an excerpt around the match
/// (matched words between « »).
#[derive(Debug, Clone, serde::Deserialize, serde::Serialize)]
pub struct Hit {
    #[serde(default)]
    pub project: String,
    pub locator: String,
    pub kind: Option<String>,
    pub commit: Option<String>,
    #[serde(default)]
    pub experts: Vec<String>,
    pub score: Option<f64>,
    pub text: String,
}

/// The hash a quote's span (`path#Lstart-end`) has on disk now, or None if the file or lines are gone.
fn current_span_hash(project_dir: &Path, locator: &str) -> Option<String> {
    let (path, lines) = locator.rsplit_once("#L")?;
    let (start, end) = lines.split_once('-')?;
    let (start, end): (usize, usize) = (start.parse().ok()?, end.parse().ok()?);
    let content = std::fs::read_to_string(project_dir.join(path)).ok()?.replace('\r', "");
    let all: Vec<&str> = content.lines().collect();
    if start == 0 || end > all.len() || start > end {
        return None;
    }
    Some(sha256_hex(&[all[start - 1..end].join("\n").as_bytes()]))
}

/// About 300 characters around the first highlighted match (the whole text if there is none).
fn excerpt(text: &str) -> String {
    let at = text.find('\u{ab}').unwrap_or(0);
    let start = text[..at].char_indices().rev().nth(120).map_or(0, |(i, _)| i);
    let end = text[at..].char_indices().nth(240).map_or(text.len(), |(i, _)| at + i);
    let mut out = text[start..end].replace('\n', " / ");
    if start > 0 {
        out.insert(0, '\u{2026}');
    }
    if end < text.len() {
        out.push('\u{2026}');
    }
    out
}

#[derive(Debug, Clone)]
pub struct ReceiptIn {
    pub command: String,
    pub machine: String,
    pub location: String,
    pub exit_code: Option<i32>,
    pub ok: bool,
    pub output_hash: String,
    /// What `output_hash` covers, in words, so a reader knows what the receipt proves.
    pub hash_of: &'static str,
    pub commit: Option<String>,
}

#[derive(Debug, Clone)]
pub struct FactIn {
    pub kind: Kind,
    pub text: String,
    pub confidence: Confidence,
    pub source: String,
    pub by: String,
    pub receipt: Option<String>,
    pub owner_statement: Option<String>,
    pub quote: Option<Quote>,
}

/// Text that must appear word for word at `line` (1-based) of `file` (relative to the project).
#[derive(Debug, Clone, serde::Deserialize, schemars::JsonSchema)]
pub struct Quote {
    pub file: String,
    pub line: usize,
    pub text: String,
}

struct VerifiedQuote {
    locator: String,
    hash: String,
    text: String,
    unit_key: String,
}

impl Quote {
    fn verify(&self, project_dir: &Path) -> Result<VerifiedQuote> {
        let rel = Path::new(&self.file);
        ensure!(
            rel.is_relative() && !rel.components().any(|c| matches!(c, std::path::Component::ParentDir)),
            "quote file {:?} must be a path inside the project",
            self.file
        );
        ensure!(self.line >= 1, "quote line numbers start at 1");
        let wanted = self.text.replace("\r\n", "\n");
        let wanted = wanted.trim_end_matches('\n');
        ensure!(!wanted.trim().is_empty(), "a quote needs text");
        let path = project_dir.join(rel);
        let content = std::fs::read_to_string(&path)
            .with_context(|| format!("reading {}", path.display()))?
            .replace("\r\n", "\n");
        let count = wanted.lines().count();
        let found: Vec<&str> = content.lines().skip(self.line - 1).take(count).collect();
        let found = found.join("\n");
        if found != wanted {
            let first = found.lines().next().unwrap_or("(past the end of the file)");
            bail!(
                "quote not found word for word at {}:{}; the file says: {first:?}",
                self.file,
                self.line
            );
        }
        let locator = format!(
            "{}#L{}-{}",
            self.file.replace('\\', "/"),
            self.line,
            self.line + count - 1
        );
        Ok(VerifiedQuote {
            hash: sha256_hex(&[wanted.as_bytes()]),
            unit_key: sha256_hex(&[locator.as_bytes()])[..32].to_owned(),
            locator,
            text: wanted.to_owned(),
        })
    }
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct FactRow {
    pub id: String,
    pub at: String,
    pub kind: String,
    pub text: String,
    pub grade: String,
    pub status: String,
    pub source: Option<String>,
    pub by: Option<String>,
    pub machine: Option<String>,
    pub receipt: Option<String>,
    pub owner_statement: Option<String>,
    /// The login that wrote it (`member:owner`, `member:agent`), or NONE for the loader.
    pub written_by: Option<String>,
    #[serde(default)]
    pub quotes: Vec<String>,
}

impl FactRow {
    pub fn render(&self) -> String {
        let mut line = format!(
            "{} [{}] {}{} {}",
            seconds(&self.at),
            self.kind,
            self.grade,
            if self.status == "live" {
                String::new()
            } else {
                format!(" ({})", self.status.to_uppercase())
            },
            self.text
        );
        let source = self.source.as_deref().filter(|s| !s.is_empty()).unwrap_or("no source");
        let _ = write!(
            line,
            " ({source}; by {} on {})",
            self.by.as_deref().unwrap_or("?"),
            self.machine.as_deref().unwrap_or("?")
        );
        if let Some(said) = &self.owner_statement {
            if self.written_by.as_deref() == Some("member:owner") {
                let _ = write!(line, " [owner statement, signed by the owner: {said}]");
            } else {
                let _ = write!(
                    line,
                    " [owner statement as reported by {}, not signed by the owner: {said}]",
                    self.by.as_deref().unwrap_or("?")
                );
            }
        }
        for q in &self.quotes {
            let _ = write!(line, " [quote {q}]");
        }
        if let Some(r) = self.receipt.as_deref().filter(|r| *r != "NONE") {
            let _ = write!(line, " [{r}]");
        }
        line
    }
}

/// `2026-10-25T04:19:34.853088800Z` -> `2026-10-25T04:19:34Z`.
fn seconds(at: &str) -> String {
    let at = at.trim_matches(|c| c == 'd' || c == '\'');
    match (at.get(..19), at.len() > 19) {
        (Some(head), true) => format!("{head}Z"),
        _ => at.to_owned(),
    }
}

/// An RFC 3339 timestamp (`2026-10-24T05:47:08Z`); the store casts it with `<datetime>`.
fn rfc3339(at: &str) -> Result<String> {
    let ok = at.len() >= 20
        && at
            .bytes()
            .all(|b| b.is_ascii_digit() || matches!(b, b'-' | b':' | b'T' | b'Z' | b'.' | b'+'))
        && at.as_bytes()[4] == b'-'
        && at.as_bytes()[10] == b'T';
    ensure!(ok, "{at:?} is not an RFC 3339 timestamp");
    Ok(at.to_owned())
}

fn grade(c: Confidence) -> &'static str {
    match c {
        Confidence::Confirmed => "CONFIRMED",
        Confidence::Inferred => "INFERRED",
        Confidence::Unknown => "UNKNOWN",
    }
}

fn kind(k: Kind) -> &'static str {
    match k {
        Kind::Decision => "decision",
        Kind::Gotcha => "gotcha",
        Kind::Status => "status",
        Kind::Fact => "fact",
        Kind::Eliminated => "eliminated",
    }
}

/// The key of `receipt:<key>`: letters and digits only (a clear error beats a silent miss).
fn receipt_key(id: &str) -> Result<String> {
    let key = id.strip_prefix("receipt:").unwrap_or(id);
    ensure!(
        !key.is_empty() && key.chars().all(|c| c.is_ascii_alphanumeric()),
        "{id:?} is not a receipt id (receipt:<letters and digits>)"
    );
    Ok(key.to_owned())
}

fn record_id(v: &Value) -> Option<String> {
    match v {
        Value::String(s) if s.contains(':') => Some(s.clone()),
        Value::Array(a) => a.iter().find_map(record_id),
        Value::Object(o) => o.get("id").and_then(record_id),
        _ => None,
    }
}

/// CONFIRMED needs a receipt, an owner statement or a quote. The store enforces this too; checking
/// first gives a plain answer instead of a schema error. A quote is verified separately.
fn needs_evidence(fact: &FactIn) -> Result<()> {
    if fact.confidence == Confidence::Confirmed
        && fact.receipt.is_none()
        && fact.owner_statement.is_none()
        && fact.quote.is_none()
    {
        bail!(
            "CONFIRMED needs evidence: a receipt from run or gate, a quote found word for word in a file, or an              owner statement. Record it as INFERRED otherwise."
        );
    }
    Ok(())
}

/// SHA-256 over `parts` in order, as lowercase hex.
pub fn sha256_hex(parts: &[&[u8]]) -> String {
    let mut hasher = Sha256::new();
    for part in parts {
        hasher.update(part);
    }
    hex(&hasher.finalize())
}

/// A store call that failed because the server was not there.
fn unreachable(e: &anyhow::Error) -> bool {
    format!("{e:#}").contains("cannot reach")
}

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().fold(String::with_capacity(bytes.len() * 2), |mut s, b| {
        let _ = write!(s, "{b:02x}");
        s
    })
}

pub fn store_url(home: &Home) -> String {
    home.config.store.url.clone().unwrap_or_else(|| DEFAULT_URL.to_owned())
}

fn secrets_dir(home: &Home) -> PathBuf {
    home.path("secrets")
}

fn read_secret(home: &Home, name: &str) -> Result<String> {
    let path = secrets_dir(home).join(format!("{name}.pass"));
    let text = std::fs::read_to_string(&path).with_context(|| {
        format!(
            "no store credentials at {} (run `groundworks store init`)",
            path.display()
        )
    })?;
    let text = text.trim().to_owned();
    ensure!(text.len() >= 24, "{} is shorter than 24 characters", path.display());
    Ok(text)
}

/// Create `root.pass` and `agent.pass` if missing. Never overwrites: a root password only takes
/// effect the first time a data directory is created.
pub fn create_secrets(home: &Home) -> Result<Vec<PathBuf>> {
    let dir = secrets_dir(home);
    std::fs::create_dir_all(&dir)?;
    let mut created = Vec::new();
    for name in ["root", "agent"] {
        let path = dir.join(format!("{name}.pass"));
        if !path.exists() {
            std::fs::write(&path, random_password()?)?;
            created.push(path);
        }
    }
    Ok(created)
}

fn random_password() -> Result<String> {
    const ALPHABET: &[u8] = b"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| anyhow::anyhow!("no OS randomness: {e}"))?;
    Ok(bytes
        .iter()
        .map(|b| ALPHABET[usize::from(*b) % ALPHABET.len()] as char)
        .collect())
}

/// How to start the home's SurrealDB: loopback only, rocksdb under the home, no console window,
/// root login through the environment (never the command line).
pub struct Launcher {
    pub url: String,
    root_pass: String,
    binary: Option<PathBuf>,
    data: PathBuf,
    /// The process this launcher started last, if any (diagnostics and tests).
    pub last_started: Mutex<Option<u32>>,
}

impl Launcher {
    pub fn new(url: &str, root_pass: &str, binary: Option<PathBuf>, data: PathBuf) -> Self {
        Self {
            url: url.to_owned(),
            root_pass: root_pass.to_owned(),
            binary,
            data,
            last_started: Mutex::new(None),
        }
    }

    pub fn for_home(home: &Home) -> Result<Self> {
        Ok(Self::new(
            &store_url(home),
            &read_secret(home, "root")?,
            home.config.store.surreal.clone(),
            home.root
                .join(home.config.store.data.as_deref().unwrap_or(Path::new("db"))),
        ))
    }

    /// Make sure a server answers at `url`, starting one if none does. Several sessions may race
    /// to start it: a process that loses (its data directory is locked) exits, and this keeps
    /// waiting for the winner until the deadline instead of failing.
    pub fn ensure(&self) -> Result<()> {
        if gw_store::healthy(&self.url) {
            return Ok(());
        }
        let bind = loopback_bind(&self.url)?;
        let binary = self
            .binary
            .clone()
            .or_else(gw_store::find_surreal)
            .context("SurrealDB not found (set [store] surreal in groundworks.toml or GROUNDWORKS_SURREAL)")?;
        std::fs::create_dir_all(&self.data)?;
        let log_path = self.data.join("surreal.log");
        let mut child = spawn_detached(&binary, &bind, &self.data.join("store"), &self.root_pass, &log_path)
            .with_context(|| format!("starting {}", binary.display()))?;
        *self.last_started.lock().expect("launcher poisoned") = Some(child.id());
        let deadline = Instant::now() + START_TIMEOUT;
        let mut exited = None;
        while Instant::now() < deadline {
            if gw_store::healthy(&self.url) {
                return Ok(());
            }
            if exited.is_none() {
                exited = child.try_wait()?;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        match exited {
            Some(status) => bail!(
                "SurrealDB exited ({status}) and nothing answered at {} within {}s; see {}",
                self.url,
                START_TIMEOUT.as_secs(),
                log_path.display()
            ),
            None => bail!(
                "SurrealDB did not answer at {} within {}s",
                self.url,
                START_TIMEOUT.as_secs()
            ),
        }
    }
}

/// Start `surreal` so it outlives the session that started it: no console window, its own process
/// group, and (where the session's job object allows it) outside that job, so closing a Claude Code
/// window does not take the shared store down with it.
fn spawn_detached(
    binary: &Path,
    bind: &str,
    data: &Path,
    root_pass: &str,
    log_path: &Path,
) -> Result<std::process::Child> {
    let command = |breakaway: bool| -> Result<Command> {
        let log = std::fs::OpenOptions::new().create(true).append(true).open(log_path)?;
        let mut cmd = Command::new(binary);
        cmd.args(["start", "--bind", bind, "--log", "warn"])
            .arg(format!("rocksdb:{}", data.display()))
            .env("SURREAL_USER", "root")
            .env("SURREAL_PASS", root_pass)
            .stdin(Stdio::null())
            .stdout(log.try_clone()?)
            .stderr(log);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
            const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
            let flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP;
            cmd.creation_flags(if breakaway {
                flags | CREATE_BREAKAWAY_FROM_JOB
            } else {
                flags
            });
        }
        #[cfg(not(windows))]
        let _ = breakaway;
        Ok(cmd)
    };
    match command(true)?.spawn() {
        Ok(child) => Ok(child),
        // A job object that forbids breakaway refuses the flag; start inside the job instead.
        Err(_) if cfg!(windows) => Ok(command(false)?.spawn()?),
        Err(e) => Err(e.into()),
    }
}

/// `host:port` from the store URL, refusing anything that is not loopback.
fn loopback_bind(url: &str) -> Result<String> {
    let rest = url
        .strip_prefix("http://")
        .context("the store URL must be http:// on loopback")?;
    let hostport = rest.split('/').next().unwrap_or_default();
    let host = hostport.rsplit_once(':').map_or(hostport, |(h, _)| h);
    ensure!(
        matches!(host, "127.0.0.1" | "localhost" | "[::1]"),
        "the store must listen on loopback only, not {host:?}"
    );
    Ok(hostport.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugs_match_the_09_17_rule() {
        assert_eq!(slug("RetroReceipts-server").unwrap(), "retroreceipts_server");
        assert_eq!(slug("GP2040-CE").unwrap(), "gp2040_ce");
        assert_eq!(slug("--a--b--").unwrap(), "a_b");
        assert!(slug("---").is_err());
    }

    #[test]
    fn the_store_listens_on_loopback_only() {
        assert_eq!(loopback_bind("http://127.0.0.1:8004/sql").unwrap(), "127.0.0.1:8004");
        assert!(loopback_bind("http://0.0.0.0:8004/sql").is_err());
        assert!(loopback_bind("http://10.0.0.5:8004/sql").is_err());
        assert!(loopback_bind("https://127.0.0.1:8004/sql").is_err());
    }

    #[test]
    fn receipt_ids_cannot_inject() {
        assert_eq!(receipt_key("receipt:abc123").unwrap(), "abc123");
        assert!(receipt_key("receipt:a; DELETE fact").is_err());
        assert!(receipt_key("").is_err());
    }

    #[test]
    fn quotes_must_match_word_for_word() {
        let dir = std::env::temp_dir().join(format!("gw-quote-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("a.rs"), "fn one() {}\r\nfn two() {}\r\nfn three() {}\r\n").unwrap();
        let q = |line, text: &str| Quote {
            file: "a.rs".into(),
            line,
            text: text.into(),
        };
        let ok = q(2, "fn two() {}\nfn three() {}").verify(&dir).unwrap();
        assert_eq!(ok.locator, "a.rs#L2-3");
        assert!(q(2, "fn two() { }").verify(&dir).is_err());
        assert!(q(9, "fn two() {}").verify(&dir).is_err());
        let outside = Quote {
            file: "../x.rs".into(),
            line: 1,
            text: "x".into(),
        };
        assert!(outside.verify(&dir).is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn passwords_are_long_and_differ() {
        let (a, b) = (random_password().unwrap(), random_password().unwrap());
        assert_eq!(a.len(), 32);
        assert_ne!(a, b);
    }
}
