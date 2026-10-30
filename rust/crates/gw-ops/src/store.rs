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

use std::collections::{BTreeMap, HashMap};
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
/// Days a hook_event or session row is kept (TELEMETRY-POLICY.md); the pulse folds them into counters.
pub const TELEMETRY_DAYS: u64 = 90;
/// Days a rejection row is kept: the record of what almost leaked.
pub const REJECTION_DAYS: u64 = 365;
pub(crate) const AGENT: &str = "agent";
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

    /// The home's store only if its server answers now: None when it is down. The hooks use this so a
    /// session never waits on a server start and a down store is reported, not hidden.
    pub fn open_if_up(home: &Home) -> Result<Option<Self>> {
        let url = store_url(home);
        if !gw_store::healthy(&url) {
            return Ok(None);
        }
        Ok(Some(Self::connect(
            &url,
            &read_secret(home, "root")?,
            &read_secret(home, "agent")?,
        )))
    }

    /// A store that starts its server through `launcher` now if needed, and again if it stops.
    pub fn with_launcher(launcher: Launcher, agent_master: &str) -> Result<Self> {
        launcher.ensure()?;
        let mut store = Self::connect(&launcher.url, &launcher.root_pass, agent_master);
        store.launcher = Some(launcher);
        Ok(store)
    }

    /// As [`Store::with_launcher`], without starting the server now: the supervisor starts it itself
    /// and counts the starts.
    pub fn with_launcher_lazy(launcher: Launcher, agent_master: &str) -> Self {
        let mut store = Self::connect(&launcher.url, &launcher.root_pass, agent_master);
        store.launcher = Some(launcher);
        store
    }

    /// The launcher this store restarts its server with, if any.
    pub fn launcher(&self) -> Option<&Launcher> {
        self.launcher.as_ref()
    }

    pub fn url(&self) -> &str {
        &self.url
    }

    /// The root password, for the export command's environment (never a command-line argument).
    pub(crate) fn root_pass(&self) -> &str {
        &self.root_pass
    }

    /// Every namespace the server holds, sorted.
    pub fn namespaces(&self) -> Result<Vec<String>> {
        let root = self.root(GLOBAL_NS)?;
        let info = root.sql("INFO FOR ROOT;", Some(1))?;
        let mut names: Vec<String> = info[0]["namespaces"]
            .as_object()
            .map(|m| m.keys().cloned().collect())
            .unwrap_or_default();
        names.sort();
        Ok(names)
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
        // Fast path: the agent login already works and the namespace holds every file, so the root
        // round trips (each a password hash on the server) and the re-provision are skipped. Measured
        // 2026-10-29: they were most of a hook's 200-320 ms. Any failure falls through to the full path,
        // which creates, migrates and re-provisions exactly as before.
        let member = connect_member(&self.url, ns, AGENT, &self.agent_pass(ns))?;
        if gw_store::is_current(&member, ns).unwrap_or(false) {
            return Ok(Arc::clone(
                self.agents
                    .lock()
                    .expect("agent cache poisoned")
                    .entry(ns.to_owned())
                    .or_insert(Arc::new(member)),
            ));
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
        // Before any network call: secret-shaped text never leaves this process (JOURNAL-REFUSALS.md).
        crate::refusal::check_fact(fact)?;
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
        crate::refusal::check_fact(fact)?;
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
        crate::refusal::check_fact(fact)?;
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
        crate::refusal::check_fact(fact)?;
        let quote = fact.quote.as_ref().map(|q| q.verify(project_dir)).transpose()?;
        needs_evidence(fact)?;
        let grade = match quote_cap(fact) {
            Some(_) => grade(Confidence::Inferred),
            None => grade(fact.confidence),
        };
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
        if let Some(lens) = fact.lens.as_deref().map(str::trim).filter(|l| !l.is_empty()) {
            create.push_str(", lens = $lens");
            vars = vars.set("lens", lens);
        }
        if let Some(target) = &fact.attacks {
            create.push_str(", attacks = type::record('fact', $attacks_key), stance = $stance");
            vars = vars
                .set("attacks_key", fact_key(target)?)
                .set("stance", fact.stance.as_deref().unwrap_or_default());
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
        self.facts_query(project, kind_filter, None, limit)
    }

    /// Every fact recorded after `since` (RFC 3339), oldest first. The Stop hook asks this to learn
    /// whether a session wrote anything; the pulse asks it per window.
    pub fn facts_since(&self, project: &str, since: &str) -> Result<Vec<FactRow>> {
        self.facts_query(project, None, Some(since), 10_000)
    }

    /// The newest `limit` facts, oldest first, optionally of one kind and optionally after `since`.
    pub fn facts_query(
        &self,
        project: &str,
        kind_filter: Option<Kind>,
        since: Option<&str>,
        limit: usize,
    ) -> Result<Vec<FactRow>> {
        let mut vars = Vars::default();
        let mut clauses: Vec<&str> = Vec::with_capacity(2);
        if let Some(k) = kind_filter {
            vars = vars.set("kind", kind(k));
            clauses.push("kind = $kind");
        }
        if let Some(since) = since {
            vars = vars.set("since", rfc3339(since)?);
            clauses.push("at > <datetime>$since");
        }
        let filter = if clauses.is_empty() {
            String::new()
        } else {
            format!("WHERE {}", clauses.join(" AND "))
        };
        self.fact_rows(project, &filter, vars, limit)
    }

    /// The findings made through a lens, and every attack on them, oldest first.
    pub fn lens_findings(&self, project: &str, lens: &str, limit: usize) -> Result<Vec<FactRow>> {
        let vars = Vars::default().set("lens", lens);
        self.fact_rows(project, "WHERE lens = $lens OR attacks.lens = $lens", vars, limit)
    }

    /// The newest `limit` facts matching `filter`, oldest first.
    fn fact_rows(&self, project: &str, filter: &str, vars: Vars, limit: usize) -> Result<Vec<FactRow>> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let vars = vars.set("limit", limit);
        let sql = format!(
            "SELECT <string> id AS id, <string> at AS at, kind, text, grade, status, source, by, machine, \
             owner_statement, <string> written_by AS written_by, <string> receipt AS receipt, lens, \
             <string> attacks AS attacks, stance, ->evidence->unit.locator AS quotes \
             FROM fact {filter} ORDER BY at DESC LIMIT $limit;"
        );
        let rows = self.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?))?;
        let mut facts: Vec<FactRow> = serde_json::from_value(rows[0].clone()).context("reading facts")?;
        facts.reverse();
        // `<string> NONE` reads "NONE".
        for f in &mut facts {
            f.attacks = f.attacks.take().filter(|a| a != "NONE");
        }
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

    /// Write an intake: new and changed units in batches (unchanged ones are skipped; a hash
    /// changed one marks the facts citing it stale), units of vanished files marked not present, and
    /// one intake_run row.
    pub fn intake(&self, project: &str, c: &crate::intake::Collected) -> Result<IntakeReport> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let existing = self.retrying(|| {
            Ok(db.query(
                "SELECT VALUE [record::id(id), [hash, changed_at, kind, experts, present]] FROM unit WHERE kind != 'quote';",
                &serde_json::Map::new(),
                Some(1),
            )?)
        })?;
        // A unit is rewritten when anything intake decides about it differs from what is stored: its text, its
        // file's last-change date, its kind (classification changes: CLAUDE.md went from context to
        // instructions on 2026-10-30), its experts (a new or edited definition, a project's own expert) or its
        // presence (a file that comes back, or a setup turned off and on again).
        let known: HashMap<String, Value> = existing[0]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|row| Some((row[0].as_str()?.to_owned(), row[1].clone())))
            .collect();
        let changed: Vec<&crate::intake::Unit> = c
            .units
            .iter()
            .filter(|u| {
                let now = serde_json::json!([u.hash, u.changed_at, u.kind, u.experts, true]);
                known.get(&u.key).is_none_or(|stored| *stored != now)
            })
            .collect();
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
        let mut file_kinds = vec!["context", "doc", "code", "config"];
        file_kinds.extend(crate::intake::SETUP_KINDS);
        let vars = Vars::default()
            .set("paths", c.paths.clone())
            .set("file_kinds", file_kinds);
        let gone = self.retrying(|| {
            Ok(db.query(
                "LET $gone = (SELECT VALUE id FROM unit WHERE kind IN $file_kinds \
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
    pub fn recheck_quotes(&self, project_dir: &Path, project: &str, claude: &Path) -> Result<usize> {
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
            let now = current_span_hash(project_dir, locator, claude);
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
    /// The ranked read every reader shares (`search` excerpts it, `ask` reads it in full): units still
    /// present that match `query` in full text, best first, each with its full text and hash.
    pub fn retrieve(
        &self,
        project: &str,
        query: &str,
        expert: Option<&str>,
        kinds: &[String],
        limit: usize,
    ) -> Result<Vec<Hit>> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let AnyWord {
            score,
            marks,
            matches,
            vars,
        } = any_word(query)?;
        let mut sql = format!(
            "SELECT record::id(id) AS key, locator, kind, commit, experts, hash, text, changed_at, ({score}) AS score, \
             [{marks}] AS marks FROM unit WHERE ({matches}) AND present = true"
        );
        let mut vars = vars.set("limit", limit);
        if let Some(expert) = expert {
            sql.push_str(" AND $expert IN experts");
            vars = vars.set("expert", expert);
        }
        if !kinds.is_empty() {
            sql.push_str(" AND kind IN $kinds");
            vars = vars.set("kinds", crate::intake::expand_kinds(kinds));
        }
        sql.push_str(" ORDER BY score DESC LIMIT $limit;");
        let rows = self.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?))?;
        let mut hits: Vec<Hit> = serde_json::from_value(rows[0].clone()).context("reading search hits")?;
        for hit in &mut hits {
            hit.project = project.to_owned();
            hit.marked = hit
                .marks
                .iter()
                .filter_map(Value::as_str)
                .find(|m| m.contains('\u{ab}'))
                .map(str::to_owned);
        }
        Ok(hits)
    }

    /// Live facts matching `query`, best first, each as the unit that is its evidence (with the fact's text
    /// and grade on the hit). A fact with no present evidence unit is skipped: it cannot be quoted.
    pub fn retrieve_facts(&self, project: &str, query: &str, limit: usize) -> Result<Vec<Hit>> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let AnyWord {
            score, matches, vars, ..
        } = any_word(query)?;
        let sql = format!(
            "SELECT text AS fact, grade AS fact_grade, ({score}) AS score, \
             (SELECT record::id(id) AS key, locator, kind, commit, experts, hash, text, changed_at FROM $parent->evidence->unit \
              WHERE present = true) AS units \
             FROM fact WHERE ({matches}) AND status = 'live' ORDER BY score DESC LIMIT $limit;"
        );
        let vars = vars.set("limit", limit);
        let rows = self.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?))?;
        let mut hits = Vec::new();
        for row in rows[0].as_array().into_iter().flatten() {
            for unit in row["units"].as_array().into_iter().flatten() {
                let mut hit: Hit = serde_json::from_value(unit.clone()).context("reading a fact's evidence")?;
                hit.project = project.to_owned();
                hit.score = row["score"].as_f64();
                hit.fact = row["fact"].as_str().map(str::to_owned);
                hit.fact_grade = row["fact_grade"].as_str().map(str::to_owned);
                hits.push(hit);
            }
        }
        Ok(hits)
    }

    /// Grep over what was ingested: present units whose text contains `needle` (case-insensitive), or
    /// matches it as a regex when written `/.../`. Exact, where `retrieve` is by words.
    pub fn grep(&self, project: &str, needle: &str, limit: usize) -> Result<Vec<Hit>> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let (test, vars) = match needle.strip_prefix('/').and_then(|r| r.strip_suffix('/')) {
            Some(re) if !re.is_empty() => ("string::matches(text, $q)", Vars::default().set("q", re)),
            _ => (
                "string::contains(string::lowercase(text), $q)",
                Vars::default().set("q", needle.to_lowercase()),
            ),
        };
        let sql = format!(
            "SELECT record::id(id) AS key, locator, kind, commit, experts, hash, text, changed_at FROM unit \
             WHERE present = true AND {test} ORDER BY locator LIMIT $limit;"
        );
        let vars = vars.set("limit", limit);
        let rows = self.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?))?;
        let mut hits: Vec<Hit> = serde_json::from_value(rows[0].clone()).context("reading grep hits")?;
        for hit in &mut hits {
            hit.project = project.to_owned();
        }
        Ok(hits)
    }

    /// The keys of present units whose span covers `line` of `path`: where a cited line was ingested.
    pub fn units_at(&self, project: &str, path: &str, line: usize) -> Result<Vec<String>> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let vars = Vars::default().set("path", path).set("line", line);
        let rows = self.retrying(|| {
            Ok(db.query(
                "SELECT VALUE record::id(id) FROM unit WHERE path = $path AND present = true \
                 AND line_start <= $line AND line_end >= $line;",
                &vars.0,
                Some(1),
            )?)
        })?;
        Ok(rows[0]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|v| v.as_str().map(str::to_owned))
            .collect())
    }

    /// How many units of the project are present: the corpus an answer is measured against.
    pub fn corpus_size(&self, project: &str) -> Result<usize> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let rows = self.retrying(|| {
            Ok(db.query(
                "RETURN count(SELECT VALUE id FROM unit WHERE present = true);",
                &serde_json::Map::new(),
                Some(1),
            )?)
        })?;
        Ok(rows[0].as_u64().unwrap_or(0) as usize)
    }

    /// The current hash, text and presence of units by key: what a check compares a packet against.
    pub fn units_now(&self, project: &str, keys: &[String]) -> Result<HashMap<String, (String, String, bool)>> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let vars = Vars::default().set("keys", keys.to_vec());
        let rows = self.retrying(|| {
            Ok(db.query(
                "SELECT record::id(id) AS key, hash, text, present FROM $keys.map(|$k| type::record('unit', $k));",
                &vars.0,
                Some(1),
            )?)
        })?;
        Ok(rows[0]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|r| {
                Some((
                    r["key"].as_str()?.to_owned(),
                    (
                        r["hash"].as_str()?.to_owned(),
                        r["text"].as_str().unwrap_or_default().to_owned(),
                        r["present"].as_bool().unwrap_or(true),
                    ),
                ))
            })
            .collect())
    }

    /// Open an ask packet in `global`; returns its id.
    pub fn create_packet(&self, packet: &Value) -> Result<String> {
        let global = self.agent(GLOBAL_NS)?;
        let vars = Vars::default().set("p", packet.clone());
        let rows =
            self.retrying(|| Ok(global.query("CREATE ask_packet CONTENT $p RETURN VALUE id;", &vars.0, Some(1))?))?;
        record_id(&rows[0]).context("CREATE ask_packet returned no id")
    }

    /// One packet by id (`ask_packet:...`).
    pub fn packet(&self, id: &str) -> Result<Value> {
        let key = id.strip_prefix("ask_packet:").unwrap_or(id);
        ensure!(
            !key.is_empty() && key.chars().all(|c| c.is_ascii_alphanumeric()),
            "{id:?} is not an ask packet id"
        );
        let global = self.agent(GLOBAL_NS)?;
        let vars = Vars::default().set("key", key);
        let rows = self.retrying(|| {
            Ok(global.query("SELECT * FROM ONLY type::record('ask_packet', $key);", &vars.0, Some(1))?)
        })?;
        ensure!(!rows[0].is_null(), "no ask packet {id}");
        Ok(rows[0].clone())
    }

    /// Record a packet's verdict, answer and report. A packet that passed is final; one that failed or
    /// was not executed may be answered again (the candidates have not changed), so a slipped quote does
    /// not cost a new ask.
    pub fn finish_packet(&self, id: &str, status: &str, answer: &Value, report: &Value) -> Result<()> {
        let key = id.strip_prefix("ask_packet:").unwrap_or(id);
        let global = self.agent(GLOBAL_NS)?;
        let vars = Vars::default()
            .set("key", key)
            .set("status", status)
            .set("answer", answer.clone())
            .set("report", report.clone());
        let rows = self.retrying(|| {
            Ok(global.query(
                "UPDATE type::record('ask_packet', $key) SET status = $status, answer = $answer, report = $report \
                 WHERE status IN ['OPEN', 'NOT_EXECUTED', 'FAIL'] RETURN VALUE id;",
                &vars.0,
                Some(1),
            ))
        })?;
        match rows {
            Ok(r) if r[0].as_array().is_some_and(|a| !a.is_empty()) => Ok(()),
            _ => bail!("{id} already passed its check; open a new ask to answer again"),
        }
    }

    /// Create a locked panel in the project's store; returns its id.
    pub fn panel_create(&self, project: &str, config: &str, digest: &str, by: &str) -> Result<String> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let vars = Vars::default()
            .set("config", config)
            .set("digest", digest)
            .set("by", by);
        let rows = self.retrying(|| {
            Ok(db.query(
                "CREATE panel SET config = $config, digest = $digest, by = $by RETURN VALUE id;",
                &vars.0,
                Some(1),
            )?)
        })?;
        record_id(&rows[0]).context("CREATE panel returned no id")
    }

    /// A panel and its answers, oldest first.
    pub fn panel_get(&self, project: &str, id: &str) -> Result<(Value, Vec<Value>)> {
        let key = panel_key(id)?;
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let vars = Vars::default().set("key", key);
        let rows = self.retrying(|| {
            Ok(db.query(
                "LET $p = type::record('panel', $key);\
                 SELECT * FROM ONLY $p;\
                 SELECT round, seat, text, sha256, at FROM panel_answer WHERE panel = $p ORDER BY at ASC;",
                &vars.0,
                Some(3),
            )?)
        })?;
        ensure!(!rows[1].is_null(), "no panel {id} in {project}");
        Ok((rows[1].clone(), rows[2].as_array().cloned().unwrap_or_default()))
    }

    #[allow(clippy::too_many_arguments)]
    pub fn panel_answer(
        &self,
        project: &str,
        id: &str,
        round: &str,
        seat: &str,
        text: &str,
        sha: &str,
        by: &str,
    ) -> Result<()> {
        crate::refusal::check_text("text", text)?;
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let vars = Vars::default()
            .set("key", panel_key(id)?)
            .set("round", round)
            .set("seat", seat)
            .set("text", text)
            .set("sha", sha)
            .set("by", by);
        self.retrying(|| {
            Ok(db.query(
                "CREATE panel_answer SET panel = type::record('panel', $key), round = $round, seat = $seat, \
                 text = $text, sha256 = $sha, by = $by RETURN VALUE id;",
                &vars.0,
                Some(1),
            )?)
        })?;
        Ok(())
    }

    /// Write one of the panel's program fields (anon, counterfactual). With `once`, only while it is unset.
    pub fn panel_set(
        &self,
        project: &str,
        id: &str,
        field: &str,
        value: &str,
        status: Option<&str>,
        once: bool,
    ) -> Result<()> {
        ensure!(
            matches!(field, "anon" | "counterfactual"),
            "panel field {field} is not written here"
        );
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let mut vars = Vars::default().set("key", panel_key(id)?).set("value", value);
        let mut set = format!("{field} = $value");
        if let Some(status) = status {
            set.push_str(", status = $status");
            vars = vars.set("status", status);
        }
        let guard = if once {
            format!(" WHERE {field} = NONE")
        } else {
            String::new()
        };
        let sql = format!("UPDATE type::record('panel', $key) SET {set}{guard} RETURN VALUE id;");
        let rows = self.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))))?;
        match rows {
            Ok(r) if r[0].as_array().is_some_and(|a| !a.is_empty()) => Ok(()),
            _ if once => bail!("{id}: {field} was already written"),
            Ok(_) => Ok(()),
            Err(e) => Err(e.into()),
        }
    }

    /// Log owed re-prompts, each once per version of the answer it concerns.
    pub fn panel_owed(&self, project: &str, id: &str, rows: &[Value]) -> Result<()> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let vars = Vars::default().set("key", panel_key(id)?).set("rows", rows.to_vec());
        self.retrying(|| {
            Ok(db.query(
                "FOR $r IN $rows { IF !record::exists(type::record('panel_owed', $r.key)) { \
                 CREATE type::record('panel_owed', $r.key) SET panel = type::record('panel', $key), \
                 condition = $r.condition, seat = $r.seat, answer_sha256 = $r.answer_sha256, detail = $r.detail; }; };",
                &vars.0,
                None,
            )?)
        })?;
        Ok(())
    }

    /// How many re-prompts of each condition the panel has owed so far.
    pub fn panel_enforcement(&self, project: &str, id: &str) -> Result<BTreeMap<String, u64>> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let vars = Vars::default().set("key", panel_key(id)?);
        let rows = self.retrying(|| {
            Ok(db.query(
                "SELECT condition, count() AS n FROM panel_owed WHERE panel = type::record('panel', $key) GROUP BY condition;",
                &vars.0,
                Some(1),
            )?)
        })?;
        Ok(rows[0]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|r| Some((r["condition"].as_str()?.to_owned(), r["n"].as_u64()?)))
            .collect())
    }

    /// Record the tally, once.
    pub fn panel_tally(&self, project: &str, id: &str, tally: &str, status: &str, verdict: Option<&str>) -> Result<()> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let mut vars = Vars::default()
            .set("key", panel_key(id)?)
            .set("tally", tally)
            .set("status", status);
        let mut set = String::from("tally = $tally, status = $status");
        if let Some(v) = verdict {
            set.push_str(", verdict = $verdict");
            vars = vars.set("verdict", v);
        }
        let sql = format!("UPDATE type::record('panel', $key) SET {set} WHERE tally = NONE RETURN VALUE id;");
        let rows = self.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))))?;
        match rows {
            Ok(r) if r[0].as_array().is_some_and(|a| !a.is_empty()) => Ok(()),
            _ => bail!("{id} was already tallied"),
        }
    }

    /// The numbers a pulse takes from one project (no model, a few aggregate queries).
    pub fn pulse_project(&self, project: &str) -> Result<crate::pulse::ProjectPulse> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let rows = self.retrying(|| {
            Ok(db.query(
                "RETURN count(SELECT VALUE id FROM unit WHERE present = true AND kind != 'quote');\
                 RETURN count(SELECT VALUE id FROM unit WHERE present = false);\
                 RETURN count(SELECT VALUE id FROM fact WHERE status = 'live');\
                 RETURN count(SELECT VALUE id FROM fact WHERE status = 'stale');\
                 RETURN count(SELECT VALUE id FROM fact WHERE status != 'gone' AND grade = 'CONFIRMED');\
                 RETURN count(SELECT VALUE id FROM fact WHERE status != 'gone' AND string::starts_with(source ?? '', 'ask_packet:'));\
                 RETURN count(SELECT VALUE id FROM fact WHERE status != 'gone' AND attacks != NONE);\
                 RETURN count(SELECT VALUE id FROM evidence);",
                &serde_json::Map::new(),
                Some(8),
            )?)
        })?;
        let at = |i: usize| rows.get(i).and_then(Value::as_u64).unwrap_or(0);
        Ok(crate::pulse::ProjectPulse {
            units: at(0),
            units_gone: at(1),
            facts_live: at(2),
            facts_stale: at(3),
            facts_confirmed: at(4),
            facts_from_asks: at(5),
            attacks: at(6),
            evidence_links: at(7),
        })
    }

    /// When each receipt of a project was taken (RFC 3339), in the last `days` (all when None), oldest
    /// first. The pulse pairs them with denies: a `run` soon after a deny is the sanctioned path taken.
    pub fn receipt_times_since(&self, project: &str, days: Option<u64>) -> Result<Vec<String>> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let (filter, vars) = match days {
            Some(d) => (
                " WHERE ran_at > time::now() - <duration>$d",
                Vars::default().set("d", format!("{d}d")),
            ),
            None => ("", Vars::default()),
        };
        let sql = format!("SELECT VALUE <string> ran_at FROM receipt{filter} ORDER BY ran_at;");
        let rows = self.retrying(|| Ok(db.query(&sql, &vars.0, Some(1))?))?;
        Ok(rows[0]
            .as_array()
            .map(|a| a.iter().filter_map(|v| v.as_str().map(seconds)).collect())
            .unwrap_or_default())
    }

    /// Facts recorded from asks in the last `days`.
    pub fn facts_from_asks_since(&self, project: &str, days: u64) -> Result<u64> {
        let db = self.agent(&project_ns(&slug(project)?)?)?;
        let vars = Vars::default().set("d", format!("{days}d"));
        let rows = self.retrying(|| {
            Ok(db.query(
                "RETURN count(SELECT VALUE id FROM fact WHERE string::starts_with(source ?? '', 'ask_packet:') \
                 AND at > time::now() - <duration>$d);",
                &vars.0,
                Some(1),
            )?)
        })?;
        Ok(rows[0].as_u64().unwrap_or(0))
    }

    /// Ask packets opened in the last `days`: status and candidates (fact or not).
    pub fn ask_packets_since(&self, days: u64) -> Result<Vec<Value>> {
        let global = self.agent(GLOBAL_NS)?;
        let vars = Vars::default().set("d", format!("{days}d"));
        let rows = self.retrying(|| {
            Ok(global.query(
                "SELECT status, by, candidates.*.fact AS facts, candidates FROM ask_packet WHERE at > time::now() - <duration>$d;",
                &vars.0,
                Some(1),
            )?)
        })?;
        Ok(rows[0].as_array().cloned().unwrap_or_default())
    }

    /// Ask packets answered and passed in the last `days`, oldest first: question, author, time,
    /// projects searched, candidates and the answer (for drawing an eval set from real asks).
    pub fn passed_packets_since(&self, days: u64) -> Result<Vec<Value>> {
        let global = self.agent(GLOBAL_NS)?;
        let vars = Vars::default().set("d", format!("{days}d"));
        let rows = self.retrying(|| {
            Ok(global.query(
                "SELECT id, question, by, at, searched, candidates, answer FROM ask_packet                  WHERE status = 'PASS' AND at > time::now() - <duration>$d ORDER BY at;",
                &vars.0,
                Some(1),
            )?)
        })?;
        Ok(rows[0].as_array().cloned().unwrap_or_default())
    }

    // ---- hook telemetry (global; counts, ids and timings only; see schema 0014) ----------------------

    /// Whether a session row with this id exists (a resumed session starts again without a new row).
    pub fn session_exists(&self, id: &str) -> Result<bool> {
        let global = self.agent(GLOBAL_NS)?;
        let vars = Vars::default().set("id", id);
        let rows = self.retrying(|| {
            Ok(global.query(
                "SELECT VALUE id FROM ONLY type::record('session', $id);",
                &vars.0,
                Some(1),
            )?)
        })?;
        Ok(!rows[0].is_null())
    }

    /// Open a session row (SessionStart). The id is the client's session id.
    pub fn session_start(&self, s: &SessionIn) -> Result<()> {
        let global = self.agent(GLOBAL_NS)?;
        let mut sql = String::from(
            "CREATE type::record('session', $id) SET machine = $machine, client = $client, agent = $agent, \
             started_at = <datetime>$started_at, cwd_class = $cwd_class, injected_entries = $injected_entries, \
             store_ok = $store_ok, hook_latency_ms = $hook_latency_ms",
        );
        let mut vars = Vars::default()
            .set("id", s.id.as_str())
            .set("machine", s.machine.as_str())
            .set("client", s.client.as_str())
            .set("agent", s.agent.as_str())
            .set("started_at", rfc3339(&s.started_at)?)
            .set("cwd_class", s.cwd_class.as_str())
            .set("injected_entries", s.injected_entries)
            .set("store_ok", s.store_ok)
            .set("hook_latency_ms", s.hook_latency_ms);
        if let Some(p) = &s.project {
            sql.push_str(", project = $project");
            vars = vars.set("project", p.as_str());
        }
        sql.push_str(" RETURN VALUE id;");
        self.retrying(|| {
            global.query(&sql, &vars.0, Some(1))?;
            Ok(())
        })
    }

    /// When a session row started, if there is one.
    pub fn session_started_at(&self, id: &str) -> Result<Option<String>> {
        let global = self.agent(GLOBAL_NS)?;
        let vars = Vars::default().set("id", id);
        let rows = self.retrying(|| {
            Ok(global.query(
                "SELECT VALUE <string> started_at FROM ONLY type::record('session', $id);",
                &vars.0,
                Some(1),
            )?)
        })?;
        Ok(rows[0].as_str().map(seconds))
    }

    /// One hook run. Append-only.
    pub fn hook_event(&self, e: &HookEventIn) -> Result<()> {
        let global = self.agent(GLOBAL_NS)?;
        let mut sql = String::from(
            "CREATE hook_event SET session = $sid, agent = $agent, hook = $hook, decision = $decision, \
             tool_uses = $tool_uses, journal_writes_since_start = $writes, latency_ms = $latency_ms",
        );
        // `$session` is a variable SurrealDB reserves; the field keeps its name, the binding does not.
        let mut vars = Vars::default()
            .set("sid", e.session.as_str())
            .set("agent", e.agent.as_str())
            .set("hook", e.hook)
            .set("decision", e.decision)
            .set("tool_uses", e.tool_uses.clone())
            .set("writes", e.journal_writes_since_start)
            .set("latency_ms", e.latency_ms);
        if let Some(p) = &e.project {
            sql.push_str(", project = $project");
            vars = vars.set("project", p.as_str());
        }
        if let Some(r) = &e.rule_id {
            sql.push_str(", rule_id = $rule_id");
            vars = vars.set("rule_id", r.as_str());
        }
        if let Some(code) = e.error_code {
            sql.push_str(", error_code = $error_code");
            vars = vars.set("error_code", code);
        }
        if let Some(exit) = e.error_exit {
            sql.push_str(", error_exit = $error_exit");
            vars = vars.set("error_exit", exit);
        }
        if let Some(len) = e.error_len {
            sql.push_str(", error_len = $error_len");
            vars = vars.set("error_len", len);
        }
        // RETURN VALUE id, not RETURN NONE: an empty result reads as a refusal (gw-core db.rs).
        sql.push_str(" RETURN VALUE id;");
        self.retrying(|| {
            global.query(&sql, &vars.0, Some(1))?;
            Ok(())
        })
    }

    /// A write the store refused, by surface (`note`, `journal_add`, ...). Append-only; the reason is
    /// a code (pattern id or store rule), never the text that was refused. The schema refuses anything
    /// else, so a caller that slipped text in would be refused here too.
    pub fn record_rejection(&self, r: &RejectionIn<'_>) -> Result<()> {
        let global = self.agent(GLOBAL_NS)?;
        let mut sql = String::from("CREATE rejection SET surface = $surface, reason = $reason");
        let mut vars = Vars::default().set("surface", r.surface).set("reason", r.reason);
        if let Some(p) = r.project {
            sql.push_str(", project = $project");
            vars = vars.set("project", p);
        }
        if let Some(b) = r.by {
            sql.push_str(", by = $by");
            vars = vars.set("by", crate::hook::plain_name(b));
        }
        if let Some(f) = r.field {
            sql.push_str(", field = $field");
            vars = vars.set("field", f);
        }
        if let Some(o) = r.offset {
            sql.push_str(", offset = $offset");
            vars = vars.set("offset", o);
        }
        if let Some(n) = r.match_len {
            sql.push_str(", match_len = $match_len");
            vars = vars.set("match_len", n);
        }
        // RETURN VALUE id, not RETURN NONE: an empty result reads as a refusal (gw-core db.rs).
        sql.push_str(" RETURN VALUE id;");
        self.retrying(|| {
            global.query(&sql, &vars.0, Some(1))?;
            Ok(())
        })
    }

    /// Session rows started in the last `days` (all when None), oldest first.
    pub fn sessions_since(&self, days: Option<u64>) -> Result<Vec<Value>> {
        self.telemetry_rows(
            "SELECT <string> id AS id, project, machine, client, agent, <string> started_at AS started_at, \
             cwd_class, injected_entries, store_ok, hook_latency_ms FROM session",
            "started_at",
            days,
        )
    }

    /// PreToolUse calls the hooks denied in session `session` at or after `since` (RFC 3339): the tool
    /// calls that changed nothing, which the Stop hook does not count as work.
    pub fn session_denies_since(&self, session: &str, since: &str) -> Result<u64> {
        let global = self.agent(GLOBAL_NS)?;
        let vars = Vars::default().set("sid", session).set("since", rfc3339(since)?);
        let rows = self.retrying(|| {
            Ok(global.query(
                "SELECT VALUE count() FROM hook_event WHERE session = $sid AND hook = 'PreToolUse' \
                 AND decision = 'deny' AND at >= <datetime>$since GROUP ALL;",
                &vars.0,
                Some(1),
            )?)
        })?;
        Ok(rows[0]
            .as_array()
            .and_then(|a| a.first())
            .or(Some(&rows[0]))
            .and_then(Value::as_u64)
            .unwrap_or(0))
    }

    /// Hook events in the last `days` (all when None), oldest first.
    pub fn hook_events_since(&self, days: Option<u64>) -> Result<Vec<Value>> {
        self.telemetry_rows(
            "SELECT session, project, agent, hook, <string> at AS at, decision, rule_id, tool_uses, \
             journal_writes_since_start, latency_ms, error_code, error_exit, error_len FROM hook_event",
            "at",
            days,
        )
    }

    /// Store rejections in the last `days` (all when None), oldest first.
    pub fn rejections_since(&self, days: Option<u64>) -> Result<Vec<Value>> {
        self.telemetry_rows(
            "SELECT <string> at AS at, surface, project, by, reason, field, offset, match_len FROM rejection",
            "at",
            days,
        )
    }

    /// Retention (TELEMETRY-POLICY.md): root deletes hook_event and session rows older than
    /// [`TELEMETRY_DAYS`] and rejection rows older than [`REJECTION_DAYS`]. Members cannot delete, so this
    /// runs in the supervisor only. Returns the rows removed per table (hook_event, session, rejection).
    pub fn prune_telemetry(&self) -> Result<[u64; 3]> {
        let root = self.root(GLOBAL_NS)?;
        let vars = Vars::default()
            .set("t", format!("{TELEMETRY_DAYS}d"))
            .set("r", format!("{REJECTION_DAYS}d"));
        let rows = self.retrying(|| {
            Ok(root.query(
                "RETURN count(SELECT VALUE id FROM hook_event WHERE at < time::now() - <duration>$t);\
                 RETURN count(SELECT VALUE id FROM session WHERE started_at < time::now() - <duration>$t);\
                 RETURN count(SELECT VALUE id FROM rejection WHERE at < time::now() - <duration>$r);\
                 DELETE hook_event WHERE at < time::now() - <duration>$t RETURN NONE;\
                 DELETE session WHERE started_at < time::now() - <duration>$t RETURN NONE;\
                 DELETE rejection WHERE at < time::now() - <duration>$r RETURN NONE;",
                &vars.0,
                Some(6),
            )?)
        })?;
        let at = |i: usize| rows.get(i).and_then(Value::as_u64).unwrap_or(0);
        Ok([at(0), at(1), at(2)])
    }

    fn telemetry_rows(&self, select: &str, time_field: &str, days: Option<u64>) -> Result<Vec<Value>> {
        let global = self.agent(GLOBAL_NS)?;
        let (filter, vars) = match days {
            Some(d) => (
                format!(" WHERE {time_field} > time::now() - <duration>$d"),
                Vars::default().set("d", format!("{d}d")),
            ),
            None => (String::new(), Vars::default()),
        };
        let sql = format!("{select}{filter} ORDER BY {time_field};");
        let rows = self.retrying(|| Ok(global.query(&sql, &vars.0, Some(1))?))?;
        Ok(rows[0].as_array().cloned().unwrap_or_default())
    }

    /// Record the expert directory in `global`: each expert's name, summary and definition file.
    pub fn upsert_experts(&self, experts: &[crate::intake::Expert]) -> Result<()> {
        let global = self.agent(GLOBAL_NS)?;
        let rows: Vec<Value> = experts
            .iter()
            .map(|e| {
                // A global expert keeps its name as its key; a project's own is keyed by name and project,
                // so the directory keeps both when the names collide.
                let key = if e.source == "global" {
                    e.name.clone()
                } else {
                    format!("{}@{}", e.name, e.source)
                };
                serde_json::json!({"key": key, "v": {"name": e.name, "summary": e.summary,
                    "file": e.file.display().to_string(), "source": e.source}})
            })
            .collect();
        if rows.is_empty() {
            return Ok(());
        }
        let vars = Vars::default().set("rows", rows);
        self.retrying(|| {
            Ok(global.query(
                "FOR $e IN $rows { UPSERT type::record('expert', $e.key) MERGE $e.v; };",
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
    #[serde(default)]
    pub key: String,
    #[serde(default)]
    pub hash: String,
    pub locator: String,
    pub kind: Option<String>,
    pub commit: Option<String>,
    #[serde(default)]
    pub experts: Vec<String>,
    pub score: Option<f64>,
    pub text: String,
    /// When the unit's file last changed, ISO 8601, if known.
    #[serde(default)]
    pub changed_at: Option<String>,
    /// The text with matched words between « » (the first highlight that matched).
    #[serde(default, skip_serializing)]
    pub marked: Option<String>,
    /// One highlight per query word, as the store returns them.
    #[serde(default, skip_serializing)]
    pub marks: Vec<Value>,
    /// When the hit is a recorded fact's evidence: the fact and its grade.
    #[serde(default)]
    pub fact: Option<String>,
    #[serde(default)]
    pub fact_grade: Option<String>,
}

/// The first line of a `path#Lstart-end` locator.
pub fn first_line(locator: &str) -> Option<usize> {
    line_range(locator).map(|r| r.0)
}

/// The `(start, end)` of a `path#Lstart-end` locator.
pub fn line_range(locator: &str) -> Option<(usize, usize)> {
    let (a, b) = locator.rsplit_once("#L")?.1.split_once('-')?;
    Some((a.parse().ok()?, b.parse().ok()?))
}

/// A unit's text with its file line numbers (`120| ...`), so a reader can cite path:line without reopening
/// the file. Text without lines (a commit) is returned as it is.
pub fn numbered(text: &str, locator: &str) -> String {
    match first_line(locator) {
        Some(start) => text
            .lines()
            .enumerate()
            .map(|(i, l)| format!("{}| {l}", start + i))
            .collect::<Vec<_>>()
            .join("\n"),
        None => text.to_owned(),
    }
}

struct AnyWord {
    score: String,
    marks: String,
    matches: String,
    vars: Vars,
}

/// Any word matches (Python's BM25 ranks every unit sharing a token); scores add up, so a row matching
/// more, rarer words ranks higher. SurrealDB's `@@` with several words would require all of them.
fn any_word(query: &str) -> Result<AnyWord> {
    let words = query_words(query);
    ensure!(!words.is_empty(), "search for something");
    let refs: Vec<usize> = (1..=words.len()).collect();
    let join = |f: &dyn Fn(&usize) -> String, sep: &str| refs.iter().map(f).collect::<Vec<_>>().join(sep);
    let mut vars = Vars::default();
    for (n, word) in refs.iter().zip(&words) {
        vars = vars.set(&format!("w{n}"), word.as_str());
    }
    Ok(AnyWord {
        score: join(&|n| format!("search::score({n})"), " + "),
        marks: join(&|n| format!("search::highlight('\u{ab}', '\u{bb}', {n})"), ", "),
        matches: join(&|n| format!("text @{n}@ $w{n}"), " OR "),
        vars,
    })
}

/// The hash a quote's span (`path#Lstart-end`) has on disk now, or None if the file or lines are gone.
fn current_span_hash(project_dir: &Path, locator: &str, claude: &Path) -> Option<String> {
    let (path, lines) = locator.rsplit_once("#L")?;
    let (start, end) = lines.split_once('-')?;
    let (start, end): (usize, usize) = (start.parse().ok()?, end.parse().ok()?);
    let file = crate::claude_setup::resolve(project_dir, path, claude);
    let content = std::fs::read_to_string(file).ok()?.replace('\r', "");
    let all: Vec<&str> = content.lines().collect();
    if start == 0 || end > all.len() || start > end {
        return None;
    }
    Some(sha256_hex(&[all[start - 1..end].join("\n").as_bytes()]))
}

/// The distinct words of a query, lowercased, the way Python's `retrieve.tokenize` splits (`\w+`), at
/// most 16 so one query stays one statement.
pub fn query_words(query: &str) -> Vec<String> {
    let mut words: Vec<String> = Vec::new();
    for w in query
        .split(|c: char| !(c.is_alphanumeric() || c == '_'))
        .filter(|w| !w.is_empty())
        .map(str::to_lowercase)
    {
        if !words.contains(&w) && words.len() < 16 {
            words.push(w);
        }
    }
    words
}

/// About 300 characters around the first highlighted match (the whole text if there is none).
pub fn excerpt(text: &str) -> String {
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

/// A session row (schema 0014): who started where (as a class, never a path), and what the
/// SessionStart hook did for it.
#[derive(Debug, Clone)]
pub struct SessionIn {
    pub id: String,
    pub project: Option<String>,
    pub machine: String,
    pub client: String,
    pub agent: String,
    pub started_at: String,
    pub cwd_class: crate::hook::CwdClass,
    pub injected_entries: u64,
    pub store_ok: bool,
    pub hook_latency_ms: i64,
}

/// One hook run (schema 0014). Codes, counts and ids only.
#[derive(Debug, Clone)]
pub struct HookEventIn {
    pub session: String,
    pub project: Option<String>,
    pub agent: String,
    pub hook: &'static str,
    pub decision: &'static str,
    pub rule_id: Option<String>,
    pub tool_uses: Value,
    pub journal_writes_since_start: u64,
    pub latency_ms: i64,
    pub error_code: Option<&'static str>,
    pub error_exit: Option<i64>,
    pub error_len: Option<u64>,
}

/// A refused write, as the rejection table keeps it: codes and numbers, never the text.
#[derive(Debug, Clone, Default)]
pub struct RejectionIn<'a> {
    pub surface: &'a str,
    pub project: Option<&'a str>,
    pub by: Option<&'a str>,
    /// A JOURNAL-REFUSALS pattern id or a store rule code (`[a-z0-9-]{1,40}`).
    pub reason: &'a str,
    pub field: Option<&'a str>,
    pub offset: Option<u64>,
    pub match_len: Option<u64>,
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
    /// The lens (expert view) the finding was made through.
    pub lens: Option<String>,
    /// Another fact this one disputes or supports (`fact:...`), with `stance`.
    pub attacks: Option<String>,
    pub stance: Option<String>,
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
        // A memory path maps into Claude Code's memory folder for the project, as `recheck_quotes` reads it;
        // before 2026-10-30 it was joined to the project, so a memory quote could never be kept at all.
        let path = crate::claude_setup::resolve(project_dir, &self.file, &crate::ops::claude_dir());
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
    #[serde(default)]
    pub lens: Option<String>,
    #[serde(default)]
    pub attacks: Option<String>,
    #[serde(default)]
    pub stance: Option<String>,
}

impl FactRow {
    /// Who signed the row: `owner` (the owner's login), `agent` (the shared agent login) or `imported`
    /// (the loader, for entries that predate the store). From `written_by`, never from the free-text `by`.
    pub fn signer(&self) -> &'static str {
        match self.written_by.as_deref() {
            Some("member:owner") => "owner",
            Some(_) => "agent",
            None => "imported",
        }
    }

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
            " ({source}; by {} on {}; signed {})",
            self.by.as_deref().unwrap_or("?"),
            self.machine.as_deref().unwrap_or("?"),
            self.signer()
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
        if let Some(lens) = &self.lens {
            let _ = write!(line, " [lens {lens}]");
        }
        if let Some(target) = self.attacks.as_deref().filter(|t| *t != "NONE") {
            let _ = write!(line, " [{} {target}]", self.stance.as_deref().unwrap_or("attacks"));
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
    match (&fact.attacks, fact.stance.as_deref()) {
        (Some(_), Some("disputes" | "supports")) | (None, None) => {}
        (Some(_), _) => bail!("an attack needs stance disputes or supports"),
        (None, Some(_)) => bail!("a stance needs attacks: the fact it disputes or supports"),
    }
    if fact.confidence == Confidence::Confirmed
        && fact.receipt.is_none()
        && fact.owner_statement.is_none()
        && fact.quote.is_none()
    {
        bail!(
            "CONFIRMED needs evidence: a receipt from run or gate, a quote found word for word in a file, or an owner \
             statement. Record it as INFERRED otherwise."
        );
    }
    Ok(())
}

/// When a CONFIRMED fact's only evidence is a quote of the project's Claude setup or memory (kinds
/// `instructions`, `agent`, `skill`, `command`, `memory`), the kind that caps it at INFERRED: such a file
/// records what an agent was told or remembered, so quoting it proves the line exists, not that it holds
/// (docs/DECISIONS.md 2026-10-30). A receipt or an owner statement is evidence of its own and lifts the cap.
pub fn quote_cap(fact: &FactIn) -> Option<&'static str> {
    if fact.confidence != Confidence::Confirmed || fact.receipt.is_some() || fact.owner_statement.is_some() {
        return None;
    }
    let kind = crate::intake::kind_of_path(&fact.quote.as_ref()?.file)?;
    crate::intake::SETUP_KINDS.contains(&kind).then_some(kind)
}

/// The key of `fact:<key>`: letters, digits and _ only.
fn fact_key(id: &str) -> Result<String> {
    let key = id.strip_prefix("fact:").unwrap_or(id);
    ensure!(
        !key.is_empty() && key.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'),
        "{id:?} is not a fact id (fact:<letters and digits>)"
    );
    Ok(key.to_owned())
}

/// The key of `panel:<key>`: letters and digits only.
fn panel_key(id: &str) -> Result<String> {
    let key = id.strip_prefix("panel:").unwrap_or(id);
    ensure!(
        !key.is_empty() && key.chars().all(|c| c.is_ascii_alphanumeric()),
        "{id:?} is not a panel id (panel:<letters and digits>)"
    );
    Ok(key.to_owned())
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

pub(crate) fn read_secret(home: &Home, name: &str) -> Result<String> {
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

    /// The same launcher with the SurrealDB binary named explicitly (the supervisor's task has no
    /// environment to find it through).
    pub fn with_binary(mut self, binary: Option<PathBuf>) -> Self {
        if binary.is_some() {
            self.binary = binary;
        }
        self
    }

    /// The data directory (the rocksdb store and the server's log live under it).
    pub fn data(&self) -> &Path {
        &self.data
    }

    /// The configured SurrealDB binary, if the home names one.
    pub fn binary(&self) -> Option<&Path> {
        self.binary.as_deref()
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
    fn a_quote_of_setup_or_memory_alone_caps_confirmed_at_inferred() {
        let fact = |file: &str| FactIn {
            kind: Kind::Fact,
            text: "x".into(),
            confidence: Confidence::Confirmed,
            source: "s".into(),
            by: "b".into(),
            receipt: None,
            owner_statement: None,
            quote: Some(Quote {
                file: file.into(),
                line: 1,
                text: "x".into(),
            }),
            lens: None,
            attacks: None,
            stance: None,
        };
        assert_eq!(quote_cap(&fact("CLAUDE.md")), Some("instructions"));
        assert_eq!(quote_cap(&fact("~claude-memory/note.md")), Some("memory"));
        assert_eq!(quote_cap(&fact(".claude/commands/ship.md")), Some("command"));
        assert_eq!(quote_cap(&fact("notes.txt")), None);
        assert_eq!(quote_cap(&fact("src/a.rs")), None);
        let mut with_receipt = fact("CLAUDE.md");
        with_receipt.receipt = Some("receipt:abc".into());
        assert_eq!(quote_cap(&with_receipt), None, "a receipt is evidence of its own");
        let mut inferred = fact("CLAUDE.md");
        inferred.confidence = Confidence::Inferred;
        assert_eq!(quote_cap(&inferred), None);
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
