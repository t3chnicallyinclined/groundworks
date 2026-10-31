//! `groundworks store status --json` (A3.1 step 7 (b); the window reads it): every namespace the registry names,
//! with its `schema_version:current` row and the number this build would migrate it to. Read-only by
//! construction: each namespace is read through its own agent (member) login, never root; nothing is
//! bootstrapped, provisioned or migrated, and a store that is down is reported, not started. A namespace
//! the agent cannot log in to (never opened, or isolated with a store of its own) is listed with the error.

use gw_store::{GLOBAL_NS, SCHEMA, Scope, connect_member, project_ns};
use serde::Serialize;
use serde_json::Value;

use crate::registry::Registry;
use crate::store::{AGENT, Store, slug};

/// The status of the store's schema, per namespace.
#[derive(Debug, Serialize)]
pub struct SchemaStatus {
    pub store: String,
    pub version: Option<String>,
    /// The highest migration number this build holds, per scope (`global`, `project`).
    pub compiled: std::collections::BTreeMap<&'static str, i64>,
    pub namespaces: Vec<NamespaceStatus>,
}

#[derive(Debug, Serialize)]
pub struct NamespaceStatus {
    pub ns: String,
    /// The registered project this namespace belongs to; `null` for `global`.
    pub project: Option<String>,
    /// `schema_version:current` as `{product, major, applied}`; `null` when it could not be read.
    pub schema_version: Option<SchemaVersion>,
    /// `applied` equals this build's highest number for the scope.
    pub current: bool,
    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct SchemaVersion {
    pub product: Value,
    pub major: Value,
    pub applied: Value,
}

fn latest(scope: Scope) -> i64 {
    gw_store::migrations(scope, SCHEMA)
        .ok()
        .and_then(|m| m.last().map(|(n, _, _)| *n))
        .unwrap_or(0)
}

impl Store {
    /// Read every registered namespace's schema version through its agent login (see the module doc).
    pub fn schema_status(&self, registry: &Registry) -> SchemaStatus {
        let mut targets: Vec<(anyhow::Result<String>, Option<String>, Scope)> =
            vec![(Ok(GLOBAL_NS.to_owned()), None, Scope::Global)];
        for name in registry.projects.keys() {
            let ns = slug(name).and_then(|s| Ok(project_ns(&s)?));
            targets.push((ns, Some(name.clone()), Scope::Project));
        }
        let mut version = None;
        let mut namespaces = Vec::with_capacity(targets.len());
        for (ns, project, scope) in targets {
            let ns = match ns {
                Ok(ns) => ns,
                Err(e) => {
                    namespaces.push(NamespaceStatus {
                        ns: String::new(),
                        project,
                        schema_version: None,
                        current: false,
                        error: Some(format!("{e:#}")),
                    });
                    continue;
                }
            };
            let read = || -> anyhow::Result<(Option<Value>, Option<String>)> {
                let db = connect_member(self.url(), &ns, AGENT, &self.agent_pass(&ns))?;
                let rows = db.sql("SELECT * FROM ONLY schema_version:current;", Some(1))?;
                Ok((rows.into_iter().next().filter(|v| !v.is_null()), db.version().ok()))
            };
            let (row, error) = match read() {
                Ok((row, v)) => {
                    version = version.or(v);
                    (row, None)
                }
                Err(e) => (None, Some(format!("{e:#}"))),
            };
            let current = row.as_ref().is_some_and(|v| {
                v["product"] == gw_store::PRODUCT
                    && v["major"].as_i64() == Some(gw_store::SCHEMA_MAJOR)
                    && v["applied"].as_i64() == Some(latest(scope))
            });
            namespaces.push(NamespaceStatus {
                ns,
                project,
                schema_version: row.map(|v| SchemaVersion {
                    product: v["product"].clone(),
                    major: v["major"].clone(),
                    applied: v["applied"].clone(),
                }),
                current,
                error,
            });
        }
        SchemaStatus {
            store: self.url().to_owned(),
            version,
            compiled: [("global", latest(Scope::Global)), ("project", latest(Scope::Project))].into(),
            namespaces,
        }
    }
}
