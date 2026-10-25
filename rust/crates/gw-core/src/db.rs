//! db.rs -- a small, loud SurrealDB HTTP client.
//!
//! Ported from the Python oracle `groundworks/db.py` (tag `v0.2.0`, commit 94ad5dc). Every failure is
//! an error naming the statement that failed. Nothing is swallowed into an empty result: silence must
//! never look like "nothing known" (the oracle's CLAUDE.md rule 5).
//!
//! Two ways in. Root (the default) is a system user over HTTP Basic auth; it bypasses table
//! permissions and is for schema, gates, loads and archive work. A provisioned agent credential
//! passes `access`: the client signs in through `DEFINE ACCESS ... TYPE RECORD`, carries the token as
//! Bearer, and the datastore applies the instance's PERMISSIONS to every statement. SurrealDB answers
//! a denied CREATE/UPSERT/UPDATE/RELATE/DELETE/INSERT with an EMPTY result and status OK, which would
//! look like success; under record auth this client turns that into `DbError::PermissionDenied`
//! (topology trap T8, same rule 5).
//!
//! Transport headers (Basic vs Bearer, the exact auth scheme) are free to differ from the oracle: the
//! ticket that scoped this file (`2026-09-18-groundworks-port-04-db-client`) notes the gate compares
//! statements and DB effects, not HTTP framing.
//!
//! ONE connection per `Db` (no pool -- that is impl-01), reconnect on a dropped socket: `ureq`'s
//! agent already redials on a fresh request, so "reconnect" here means the client raises a named
//! `DbError` instead of hanging, and a caller's next call against a server that came back succeeds.

use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Value};

/// The SurrealDB series this engine's behaviour is measured against (db.py `MEASURED_AGAINST`).
/// A schema re-apply silently drops a CHANGEFEED; a delete carries no before-image; `DEFINE USER
/// ... ON DATABASE` is a system user and bypasses table PERMISSIONS; a denied write returns an
/// empty result with status OK; `TYPE object FLEXIBLE` is the accepted spelling. Each is a
/// load-bearing assumption somewhere in this repo.
pub const MEASURED_MAJOR: u32 = 3;
pub const MEASURED_MINOR: u32 = 1;

const WRITE_VERBS: [&str; 6] = ["CREATE", "UPSERT", "UPDATE", "RELATE", "DELETE", "INSERT"];

/// A statement failed, the server could not be reached, or a write under an agent credential was
/// refused by the datastore's PERMISSIONS (or named a record that does not exist; SurrealDB does not
/// distinguish the two). `PermissionDenied` is reported by a caller as `rejected_by_gate`, with
/// `missing` naming the credential needed -- mirrors `groundworks.db.PermissionDenied`.
#[derive(Debug, thiserror::Error)]
pub enum DbError {
    #[error("{0}")]
    Db(String),
    #[error("{message}")]
    PermissionDenied { message: String, missing: Vec<String> },
}

impl DbError {
    fn db(message: impl Into<String>) -> Self {
        DbError::Db(message.into())
    }
}

/// Take at most `max_chars` **characters** (never bytes) off the front of `text`, so a truncated
/// error message can never panic on a multi-byte UTF-8 boundary the way a raw byte slice could.
fn truncate(text: &str, max_chars: usize) -> String {
    text.chars().take(max_chars).collect()
}

/// The leading write verb of a statement (case-insensitive, on a word boundary), or `None`. Ports
/// `db.py`'s `WRITE_RE = re.compile(r"^\s*(CREATE|UPSERT|UPDATE|RELATE|DELETE|INSERT)\b", re.I)`.
///
/// Char-safe, not byte-safe: an earlier version used `str::split_at(verb.len())`, a BYTE index,
/// which panics ("byte index N is not a char boundary") whenever a multi-byte UTF-8 character sits
/// at that offset -- reachable from `denied()` on the very first non-ASCII status-OK empty-result
/// block it sees under record auth (`2026-09-18-groundworks-port-04-db-client`, blocking finding
/// B2). All six verbs are ASCII, so comparing by `char` (not `u8`) is both correct and never a
/// narrower match than the byte version was.
fn write_verb(statement: &str) -> Option<&'static str> {
    let trimmed = statement.trim_start();
    let chars: Vec<char> = trimmed.chars().collect();
    for verb in WRITE_VERBS {
        let verb_len = verb.len(); // every WRITE_VERBS entry is pure ASCII: chars == bytes
        if chars.len() < verb_len {
            continue;
        }
        let head_matches = chars[..verb_len]
            .iter()
            .zip(verb.chars())
            .all(|(c, v)| c.eq_ignore_ascii_case(&v));
        if head_matches {
            let boundary = chars
                .get(verb_len)
                .map(|c| !(c.is_alphanumeric() || *c == '_'))
                .unwrap_or(true);
            if boundary {
                return Some(verb);
            }
        }
    }
    None
}

/// Ports Python's `str.splitlines()` for SurQL text: `\n`, `\r\n` and lone `\r` are each ONE line
/// break, and a trailing terminator does not produce a phantom empty final line the way
/// `str::split('\n')` would (`"a\n".splitlines() == ["a"]`, not `["a", ""]`). `_strip_comments`
/// below relies on exactly this: it is what makes the oracle drop one trailing newline rather than
/// keep it (measured against `groundworks/patterns/*.surql`, which all end in `\n`).
///
/// Every OTHER line boundary `str.splitlines()` recognises is handled too (`\x0b` vertical tab,
/// `\x0c` form feed, `\x1c`-`\x1e` file/group/record separator, `\u{85}` NEL, `\u{2028}` line
/// separator, `\u{2029}` paragraph separator) -- an earlier version claimed "SurQL text does not
/// contain them" and skipped them, which is not safe: a line break is what ENDS a `--` comment.
/// Proven on the built candidate (`2026-09-18-groundworks-port-04-db-client`, blocking finding B1):
/// `"SELECT 1; -- c\x0cSELECT 2;\n"` split the oracle's way keeps `SELECT 2`, but treating `\x0c` as
/// ordinary text swallows the rest of the script into the comment and `script()` reports SUCCESS
/// having sent nothing -- silent statement loss, rule 5.
fn python_splitlines(text: &str) -> Vec<String> {
    let mut lines = Vec::new();
    let mut current = String::new();
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        match ch {
            '\r' => {
                if chars.peek() == Some(&'\n') {
                    chars.next();
                }
                lines.push(std::mem::take(&mut current));
            }
            '\n' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}'
            | '\u{2029}' => lines.push(std::mem::take(&mut current)),
            _ => current.push(ch),
        }
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

/// Remove `--` comments (outside quotes) line by line, so they can never be sent as statements.
/// Ports `db.py::_strip_comments` character for character.
pub fn strip_comments(text: &str) -> String {
    let mut out_lines = Vec::new();
    for line in python_splitlines(text) {
        let chars: Vec<char> = line.chars().collect();
        let mut out = String::new();
        let mut quote: Option<char> = None;
        let mut i = 0;
        while i < chars.len() {
            let ch = chars[i];
            if let Some(q) = quote {
                if ch == q {
                    quote = None;
                }
            } else if ch == '\'' || ch == '"' {
                quote = Some(ch);
            } else if ch == '-' && chars.get(i + 1) == Some(&'-') {
                break;
            }
            out.push(ch);
            i += 1;
        }
        out_lines.push(out);
    }
    out_lines.join("\n")
}

/// Split on semicolons that are not inside quotes, braces or brackets. Ports
/// `db.py::_split_statements` character for character, including the depth counter going negative
/// on an unmatched closer (the oracle does not guard against that either).
pub fn split_statements(text: &str) -> Vec<String> {
    let stripped = strip_comments(text);
    let mut out = Vec::new();
    let mut depth: i64 = 0;
    let mut quote: Option<char> = None;
    let mut buf = String::new();
    for ch in stripped.chars() {
        if let Some(q) = quote {
            if ch == q {
                quote = None;
            }
        } else if ch == '\'' || ch == '"' {
            quote = Some(ch);
        } else if ch == '{' || ch == '[' || ch == '(' {
            depth += 1;
        } else if ch == '}' || ch == ']' || ch == ')' {
            depth -= 1;
        } else if ch == ';' && depth <= 0 {
            out.push(std::mem::take(&mut buf));
            continue;
        }
        buf.push(ch);
    }
    out.push(buf);
    out
}

/// The first `major.minor.patch` found in `text` (a plain scan, ports `db.py`'s
/// `VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")` via `re.search`).
fn find_version(text: &str) -> Option<(u32, u32, u32)> {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let mut i = 0;
    while i < n {
        if chars[i].is_ascii_digit() {
            let (major, mut j) = take_digits(&chars, i);
            if let Some(major) = major {
                if chars.get(j) == Some(&'.') {
                    j += 1;
                    let (minor, mut k) = take_digits(&chars, j);
                    if let Some(minor) = minor {
                        if chars.get(k) == Some(&'.') {
                            k += 1;
                            let (patch, _l) = take_digits(&chars, k);
                            if let Some(patch) = patch {
                                return Some((major, minor, patch));
                            }
                        }
                    }
                }
            }
        }
        i += 1;
    }
    None
}

fn take_digits(chars: &[char], start: usize) -> (Option<u32>, usize) {
    let mut i = start;
    while i < chars.len() && chars[i].is_ascii_digit() {
        i += 1;
    }
    if i == start {
        (None, i)
    } else {
        let s: String = chars[start..i].iter().collect();
        (s.parse::<u32>().ok(), i)
    }
}

/// What `db.py::version_problem` returns, given the server's own reported version text, as a pure
/// function of `(url, reported)` -- factored out so it is testable without a real server, the way the
/// oracle's `VersionTests` stub `version()` and test `version_problem()` in isolation.
pub fn version_problem_for(url: &str, reported: &str) -> Option<String> {
    let found = find_version(reported);
    match found {
        None => Some(format!(
            "the server at {} reports its version as {}, which does not contain one",
            url,
            python_repr_str(reported)
        )),
        Some((major, minor, _patch)) => {
            if major != MEASURED_MAJOR {
                Some(format!(
                    "this engine's behaviour is measured against SurrealDB {}.{}; the server at {} is {}. \
                     A different major series may differ in ways this repo relies on (changefeeds, \
                     before-images on delete, how a denied write is reported). Point it at {}.x or \
                     re-measure first.",
                    MEASURED_MAJOR, MEASURED_MINOR, url, reported, MEASURED_MAJOR
                ))
            } else if minor != MEASURED_MINOR {
                Some(format!(
                    "NOTE: measured against SurrealDB {}.{}, talking to {}. Same major series, so this \
                     is a warning, not a refusal.",
                    MEASURED_MAJOR, MEASURED_MINOR, reported
                ))
            } else {
                None
            }
        }
    }
}

/// One connection to one SurrealDB `/sql` endpoint. No pool, no registry (impl-01): a caller that
/// wants more than one connection makes more than one `Db`.
pub struct Db {
    url: String,
    ns: String,
    db: String,
    user: String,
    password: String,
    access: Option<String>,
    basic: String,
    token: Mutex<Option<String>>,
    agent: ureq::Agent,
}

impl Db {
    /// `access` is `None` for root (HTTP Basic, bypasses table PERMISSIONS); `Some(name)` signs in
    /// through `DEFINE ACCESS <name> ... TYPE RECORD` and carries a Bearer token instead (db.py:60,
    /// `(access or "").strip() or None`).
    pub fn new(
        url: &str,
        ns: &str,
        db: &str,
        user: &str,
        password: &str,
        timeout: Duration,
        access: Option<&str>,
    ) -> Self {
        let agent = ureq::AgentBuilder::new().timeout(timeout).build();
        let basic = base64_encode(format!("{}:{}", user, password).as_bytes());
        let access = access.map(str::trim).filter(|s| !s.is_empty()).map(str::to_string);
        Db {
            url: url.to_string(),
            ns: ns.to_string(),
            db: db.to_string(),
            user: user.to_string(),
            password: password.to_string(),
            access,
            basic,
            token: Mutex::new(None),
            agent,
        }
    }

    /// Root, `http://127.0.0.1:8002/sql`, `ns=test db=test`, matching `Db()`'s Python defaults.
    pub fn root_default() -> Self {
        Db::new(
            "http://127.0.0.1:8002/sql",
            "test",
            "test",
            "root",
            "root",
            Duration::from_secs(30),
            None,
        )
    }

    pub fn url(&self) -> &str {
        &self.url
    }

    /// Ports db.py:69's `base = self.url[:-4] if self.url.endswith("/sql") else self.url.rstrip("/")`
    /// exactly: the trailing-slash trim applies ONLY on the else branch. An earlier version always
    /// trimmed a trailing slash, which is harmless for every URL this repo actually uses (nothing
    /// ends `/sql/`) but diverges from the oracle on that input (minor m5).
    fn base_url(&self) -> String {
        match self.url.strip_suffix("/sql") {
            Some(trimmed) => trimmed.to_string(),
            None => self.url.trim_end_matches('/').to_string(),
        }
    }

    // ---- plumbing --------------------------------------------------------------------------

    /// Exchange the agent credential for a token through `DEFINE ACCESS ... TYPE RECORD`.
    fn signin(&self) -> Result<String, DbError> {
        let base = self.base_url();
        let access = self.access.as_deref().unwrap_or("");
        let body = json!({
            "ns": self.ns, "db": self.db, "ac": access, "id": self.user, "pass": self.password,
        })
        .to_string();
        let result = self
            .agent
            .post(&format!("{}/signin", base))
            .set("Accept", "application/json")
            .set("Content-Type", "application/json")
            .send_string(&body);
        let raw = match result {
            Ok(response) => response
                .into_string()
                .map_err(|e| DbError::db(format!("signin response from {} was not valid UTF-8: {}", base, e)))?,
            Err(ureq::Error::Status(code, response)) => {
                let detail = truncate(&response.into_string().unwrap_or_default(), 300);
                return Err(DbError::db(format!(
                    "signin refused for agent {} via access {} on {}/{} (HTTP {}: {}); the credential \
                     may be revoked, expired or mistyped",
                    python_repr_str(&self.user),
                    python_repr_opt_str(&self.access),
                    self.ns,
                    self.db,
                    code,
                    detail
                )));
            }
            Err(ureq::Error::Transport(t)) => {
                return Err(DbError::db(format!(
                    "cannot reach {} ({}); the database may be stopped",
                    self.url, t
                )));
            }
        };
        let token = serde_json::from_str::<Value>(&raw)
            .ok()
            .and_then(|v| v.get("token").and_then(Value::as_str).map(str::to_string))
            .filter(|t| !t.is_empty());
        match token {
            Some(t) => {
                *self.token.lock().unwrap() = Some(t.clone());
                Ok(t)
            }
            None => Err(DbError::db(format!(
                "signin for agent {} returned no token: {}",
                python_repr_str(&self.user),
                truncate(&raw, 300)
            ))),
        }
    }

    fn auth_header(&self) -> Result<String, DbError> {
        if self.access.is_some() {
            let existing = self.token.lock().unwrap().clone();
            let token = match existing {
                Some(t) => t,
                None => self.signin()?,
            };
            Ok(format!("Bearer {}", token))
        } else {
            Ok(format!("Basic {}", self.basic))
        }
    }

    fn post(&self, body: &str, retry: bool) -> Result<Value, DbError> {
        self.post_to(&self.url, "text/plain", body, retry)
    }

    fn post_to(&self, url: &str, content_type: &str, body: &str, retry: bool) -> Result<Value, DbError> {
        let bytes = self.send(url, content_type, "application/json", body.as_bytes(), retry)?;
        let raw = String::from_utf8(bytes)
            .map_err(|e| DbError::db(format!("response from {} was not valid UTF-8: {}", url, e)))?;
        serde_json::from_str::<Value>(&raw)
            .map_err(|_| DbError::db(format!("server did not return JSON: {}", truncate(&raw, 300))))
    }

    /// POST `body` and return the response bytes. Shared by the `/sql` (JSON) and `/rpc` (CBOR)
    /// paths so token refresh and the one reconnect behave the same on both.
    fn send(&self, url: &str, content_type: &str, accept: &str, body: &[u8], retry: bool) -> Result<Vec<u8>, DbError> {
        let auth = self.auth_header()?;
        let result = self
            .agent
            .post(url)
            .set("Accept", accept)
            .set("Content-Type", content_type)
            .set("Authorization", &auth)
            .set("surreal-ns", &self.ns)
            .set("surreal-db", &self.db)
            .send_bytes(body);
        match result {
            Ok(response) => {
                let mut bytes = Vec::new();
                std::io::Read::read_to_end(&mut response.into_reader(), &mut bytes)
                    .map_err(|e| DbError::db(format!("could not read the response from {}: {}", url, e)))?;
                Ok(bytes)
            }
            Err(ureq::Error::Status(401, _response)) if self.access.is_some() && retry => {
                // token expired: sign in once more (db.py:106-108)
                *self.token.lock().unwrap() = None;
                self.send(url, content_type, accept, body, false)
            }
            Err(ureq::Error::Status(code, response)) => {
                let detail = truncate(&response.into_string().unwrap_or_default(), 500);
                Err(DbError::db(format!("HTTP {} from {}: {}", code, url, detail)))
            }
            Err(ureq::Error::Transport(t)) if retry => {
                // ONE connection per `Db`, no pool -- but `ureq`'s agent keeps an idle keep-alive
                // connection under the hood, and a dropped socket (the server restarted, or simply
                // closed an idle connection) surfaces here as a transport error on the NEXT request,
                // not as a clean signal that reconnecting would help. db.py never hits this: plain
                // `urllib` opens a fresh connection every call. One retry on a fresh connection
                // restores that behaviour without adding a pool of our own.
                let _ = t;
                self.send(url, content_type, accept, body, false)
            }
            Err(ureq::Error::Transport(t)) => Err(DbError::db(format!(
                "cannot reach {} ({}); the database may be stopped",
                url, t
            ))),
        }
    }

    fn run(&self, statement: &str, expect: Option<usize>) -> Result<Vec<Value>, DbError> {
        let body = format!("USE NS {} DB {}; {}", self.ns, self.db, statement);
        let value = self.post(&body, true)?;
        let blocks = value
            .as_array()
            .ok_or_else(|| DbError::db(format!("unexpected response shape: {}", python_type_name(&value))))?
            .clone();
        let first_is_use_ok = blocks
            .first()
            .and_then(|b| b.get("status"))
            .and_then(Value::as_str)
            == Some("OK");
        let blocks = if first_is_use_ok && blocks.len() > 1 {
            blocks[1..].to_vec()
        } else {
            blocks
        };
        if let Some(expect) = expect {
            if blocks.len() != expect {
                return Err(DbError::db(format!(
                    "expected {} result block(s), got {}, for: {}",
                    expect,
                    blocks.len(),
                    truncate(statement, 200)
                )));
            }
        }
        Ok(blocks)
    }

    /// Under record auth, a write whose result is `[]` was refused by PERMISSIONS. Root never is.
    /// Ports `db.py::_denied`.
    fn denied(&self, statement: &str, blocks: &[Value]) -> Option<String> {
        self.access.as_ref()?;
        let parts: Vec<String> = split_statements(statement)
            .into_iter()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        let pairs: Vec<(&str, &Value)> = if parts.len() == blocks.len() {
            parts.iter().map(String::as_str).zip(blocks.iter()).collect()
        } else {
            blocks.iter().map(|b| (statement, b)).collect()
        };
        for (text, block) in pairs {
            let is_ok = block.get("status").and_then(Value::as_str) == Some("OK");
            let is_empty = block.get("result").and_then(Value::as_array).map(Vec::is_empty).unwrap_or(false);
            if is_ok && is_empty {
                if let Some(verb) = write_verb(text) {
                    return Some(format!(
                        "{} refused by datastore permissions for agent {} (or the record does not exist): {}",
                        verb,
                        python_repr_str(&self.user),
                        truncate(text, 200)
                    ));
                }
            }
        }
        None
    }

    // ---- public ------------------------------------------------------------------------------

    /// Run one or more statements. Raise on the first failure, naming it.
    pub fn sql(&self, statement: &str, expect: Option<usize>) -> Result<Vec<Value>, DbError> {
        let blocks = self.run(statement, expect)?;
        self.finish(statement, blocks)
    }

    /// The one rule for when a batch failed, shared by [`Db::sql`] and [`Db::query`]: any block
    /// that is not OK is an error naming it, and under record auth an empty write is
    /// `PermissionDenied`, never an empty success (rule 5).
    fn finish(&self, statement: &str, blocks: Vec<Value>) -> Result<Vec<Value>, DbError> {
        for (index, block) in blocks.iter().enumerate() {
            if block.get("status").and_then(Value::as_str) != Some("OK") {
                return Err(DbError::db(format!(
                    "statement {} failed: {}\n  statement: {}",
                    index + 1,
                    python_str_value(&block.get("result").cloned().unwrap_or(Value::Null)),
                    truncate(statement, 300)
                )));
            }
        }
        if let Some(denied) = self.denied(statement, &blocks) {
            return Err(DbError::PermissionDenied {
                message: denied,
                missing: vec!["a launch credential whose role may perform this write".to_string()],
            });
        }
        Ok(blocks.into_iter().map(|b| b.get("result").cloned().unwrap_or(Value::Null)).collect())
    }

    /// Run statements with BOUND variables (`$name` in the text, values in `vars`), through the
    /// RPC endpoint in SurrealDB's typed CBOR encoding (see `cbor.rs` for why not JSON). Values are
    /// never spliced into the text, so a value can never change what a statement does, and a string
    /// always arrives as that string. Same failure rules as [`Db::sql`]: any failed block is an error
    /// naming it, and a refused write under record auth is `PermissionDenied`.
    ///
    /// Not in the Python oracle (which has no parameterised path); its own tests are its gate.
    pub fn query(
        &self,
        statement: &str,
        vars: &serde_json::Map<String, Value>,
        expect: Option<usize>,
    ) -> Result<Vec<Value>, DbError> {
        let request = json!({
            "id": 1,
            "method": "query",
            "params": [format!("USE NS {} DB {}; {}", self.ns, self.db, statement), vars],
        });
        let body = crate::cbor::encode(&request).map_err(DbError::db)?;
        let url = format!("{}/rpc", self.base_url());
        let bytes = self.send(&url, "application/cbor", "application/cbor", &body, true)?;
        let value = crate::cbor::decode(&bytes).map_err(DbError::db)?;
        if let Some(error) = value.get("error") {
            return Err(DbError::db(format!(
                "query failed: {}\n  statement: {}",
                error.get("message").and_then(Value::as_str).map_or_else(|| error.to_string(), str::to_string),
                truncate(statement, 300)
            )));
        }
        let mut blocks = value
            .get("result")
            .and_then(Value::as_array)
            .cloned()
            .ok_or_else(|| DbError::db(format!("unexpected RPC response: {}", truncate(&value.to_string(), 300))))?;
        if blocks.first().and_then(|b| b.get("status")).and_then(Value::as_str) == Some("OK") && blocks.len() > 1 {
            blocks.remove(0); // the USE statement
        }
        if let Some(expect) = expect {
            if blocks.len() != expect {
                return Err(DbError::db(format!(
                    "expected {} result block(s), got {}, for: {}",
                    expect,
                    blocks.len(),
                    truncate(statement, 200)
                )));
            }
        }
        self.finish(statement, blocks)
    }

    /// Run a statement that a GATE is allowed to refuse. Returns `(ok, message)`. A PERMISSIONS
    /// refusal is not a gate answer: under record auth it still raises `PermissionDenied` so a caller
    /// never explains a denied write as missing evidence.
    pub fn try_sql(&self, statement: &str) -> Result<(bool, String), DbError> {
        let blocks = match self.run(statement, None) {
            Ok(b) => b,
            Err(DbError::Db(message)) => return Ok((false, message)),
            Err(err) => return Err(err),
        };
        for block in &blocks {
            if block.get("status").and_then(Value::as_str) != Some("OK") {
                let message = python_str_value(&block.get("result").cloned().unwrap_or(Value::Null));
                return Ok((false, message));
            }
        }
        if let Some(denied) = self.denied(statement, &blocks) {
            return Err(DbError::PermissionDenied {
                message: denied,
                missing: vec!["a launch credential whose role may perform this write".to_string()],
            });
        }
        Ok((true, String::new()))
    }

    /// Run a multi-statement script. Return a per-statement report; raise if any failed.
    pub fn script(&self, text: &str) -> Result<Vec<Value>, DbError> {
        let statements: Vec<String> = split_statements(text)
            .into_iter()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        let mut results = Vec::new();
        let mut failed = Vec::new();
        for statement in &statements {
            let (ok, message) = self.try_sql(statement)?;
            let entry = json!({"statement": truncate(statement, 120), "ok": ok, "error": message});
            results.push(entry.clone());
            if !ok {
                failed.push(entry);
            }
        }
        if !failed.is_empty() {
            let detail: Vec<String> = failed
                .iter()
                .take(8)
                .map(|f| {
                    format!(
                        "  [{}] {}",
                        f["statement"].as_str().unwrap_or_default(),
                        truncate(f["error"].as_str().unwrap_or_default(), 300)
                    )
                })
                .collect();
            return Err(DbError::db(format!(
                "{} of {} statements failed:\n{}",
                failed.len(),
                statements.len(),
                detail.join("\n")
            )));
        }
        Ok(results)
    }

    /// What the server says it is, e.g. `"surrealdb-3.1.6+20260813.cfbaec4"`.
    pub fn version(&self) -> Result<String, DbError> {
        let base = self.base_url();
        match self.agent.get(&format!("{}/version", base)).call() {
            Ok(response) => {
                let text = response
                    .into_string()
                    .map_err(|e| DbError::db(format!("could not reach {} to ask its version: {}", base, e)))?;
                Ok(text.trim().to_string())
            }
            Err(ureq::Error::Status(code, _response)) => Err(DbError::db(format!(
                "could not reach {} to ask its version: HTTP {}",
                base, code
            ))),
            Err(ureq::Error::Transport(t)) => {
                Err(DbError::db(format!("could not reach {} to ask its version: {}", base, t)))
            }
        }
    }

    /// `None` if this server is one the engine's behaviour was measured against, else why not.
    pub fn version_problem(&self) -> Result<Option<String>, DbError> {
        let text = self.version()?;
        Ok(version_problem_for(&self.url, &text))
    }

    pub fn ping(&self, check_version: bool) -> Result<bool, DbError> {
        self.sql("RETURN 1", Some(1))?;
        if check_version {
            if let Some(problem) = self.version_problem()? {
                if problem.starts_with("NOTE:") {
                    println!("{}", problem);
                } else {
                    return Err(DbError::db(problem));
                }
            }
        }
        Ok(true)
    }

    /// Ports db.py:227's `rows[0]["n"] if rows else 0`: `0` ONLY when the query returned no row at
    /// all (an empty table with `GROUP ALL` still returns one row in this repo's measured series, so
    /// that is the "nothing to count" case, not "something went wrong"). If a row came back but has
    /// no readable `n`, Python's dict indexing would raise `KeyError`; the earlier `.unwrap_or(0)`
    /// instead reported "0", the exact silence-as-knowledge this file forbids (minor m3) -- an
    /// unreadable count and an empty table must not look the same.
    pub fn count(&self, table: &str) -> Result<i64, DbError> {
        let blocks = self.sql(&format!("SELECT count() AS n FROM {} GROUP ALL", table), Some(1))?;
        let rows = blocks[0].as_array().cloned().unwrap_or_default();
        match rows.first() {
            None => Ok(0),
            Some(row) => row.get("n").and_then(Value::as_i64).ok_or_else(|| {
                DbError::db(format!("count() on {:?} got a row with no readable n: {}", table, row))
            }),
        }
    }
}

/// `type(x).__name__` for the JSON-decoded shapes `_run` can see, matching Python's own names
/// (db.py:120 `type(blocks).__name__`) rather than serde's -- `dict`/`str`/`list`, not
/// `object`/`string`/`array` (minor m4).
fn python_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) => {
            if n.is_i64() || n.is_u64() {
                "int"
            } else {
                "float"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Python's `repr()` for a `str`: single-quoted, unless the string holds a `'` and no `"` (then
/// double-quoted instead, exactly as CPython's `unicode_repr` picks its quote), with `\`, the chosen
/// quote character, and the common control escapes backslash-escaped (minor m1: `db.py` uses `%r`
/// for the agent/access identifiers and the reported version string; Rust's `{:?}` renders
/// `"agent"`, not Python's `'agent'`).
fn python_repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || (c as u32) == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Python's `%r` on `Optional[str]` (`self.access`): `None` bare, a string quoted per
/// `python_repr_str` (minor m1).
fn python_repr_opt_str(value: &Option<String>) -> String {
    match value {
        Some(s) => python_repr_str(s),
        None => "None".to_string(),
    }
}

/// Python's `repr()` on a JSON-decoded value -- used for elements nested inside a list/dict, the way
/// Python's own `list.__repr__`/`dict.__repr__` call `repr()` on every element.
fn python_repr_value(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(b) => if *b { "True" } else { "False" }.to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => python_repr_str(s),
        Value::Array(items) => {
            let parts: Vec<String> = items.iter().map(python_repr_value).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Object(map) => {
            let parts: Vec<String> =
                map.iter().map(|(k, v)| format!("{}: {}", python_repr_str(k), python_repr_value(v))).collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

/// Python's `str()` on a JSON-decoded value: a top-level string renders bare (no quotes), everything
/// else renders the way `repr()` would (`None`, `True`/`False`, a bare number, or a `list`/`dict`
/// literal with `repr()`'d elements) -- ports the `%s` formatting `db.py` uses for a failed block's
/// `result` in `sql`/`try_sql` (minor m2: rendering it via `serde_json::Value`'s `Display` instead
/// gives a JSON string double-quoted where Python's `%s` gives it bare).
fn python_str_value(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => python_repr_value(other),
    }
}

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/// Standard base64 (with padding), so `Db` needs no extra crate just for the Basic-auth header.
fn base64_encode(data: &[u8]) -> String {
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0];
        let b1 = *chunk.get(1).unwrap_or(&0);
        let b2 = *chunk.get(2).unwrap_or(&0);
        let n = ((b0 as u32) << 16) | ((b1 as u32) << 8) | (b2 as u32);
        out.push(B64[((n >> 18) & 0x3f) as usize] as char);
        out.push(B64[((n >> 12) & 0x3f) as usize] as char);
        out.push(if chunk.len() > 1 { B64[((n >> 6) & 0x3f) as usize] as char } else { '=' });
        out.push(if chunk.len() > 2 { B64[(n & 0x3f) as usize] as char } else { '=' });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- SplitTests (db.py's SplitTests, ported 1:1) --------------------------------------------

    #[test]
    fn comments_never_become_statements() {
        let text = "-- header; with a semicolon\n\
                     DEFINE TABLE a SCHEMAFULL;  -- trailing; comment\n\
                     CREATE a:1 SET s = 'semi;colon -- not a comment';\n";
        let parts: Vec<String> = split_statements(text)
            .into_iter()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        assert_eq!(
            parts,
            vec![
                "DEFINE TABLE a SCHEMAFULL".to_string(),
                "CREATE a:1 SET s = 'semi;colon -- not a comment'".to_string(),
            ]
        );
    }

    #[test]
    fn semicolons_inside_braces_and_quotes_do_not_split() {
        let text = "UPDATE x SET f = {a: 'b;c', n: [1;2]}; SELECT 1;";
        let parts: Vec<String> = split_statements(text)
            .into_iter()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        assert_eq!(parts.len(), 2);
    }

    #[test]
    fn a_dash_dash_comment_ends_at_every_python_line_boundary_not_just_lf_crlf_cr() {
        // Regression for blocking finding B1: a line break other than \n/\r\n/\r ALSO ends a `--`
        // comment (it is one of Python's other str.splitlines() boundaries), so the statement after
        // it must survive, not be swallowed into the comment.
        for boundary in ['\u{0b}', '\u{0c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}', '\u{2029}'] {
            let text = format!("SELECT 1; -- c{boundary}SELECT 2;\n");
            let parts: Vec<String> = split_statements(&text)
                .into_iter()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect();
            assert_eq!(
                parts,
                vec!["SELECT 1".to_string(), "SELECT 2".to_string()],
                "boundary {:?} must end the comment, not swallow the next statement",
                boundary
            );
        }
    }

    // ---- UnreachableTests -------------------------------------------------------------------

    #[test]
    fn unreachable_server_is_an_error_not_an_empty_result() {
        let db = Db::new(
            "http://127.0.0.1:9/sql",
            "x",
            "y",
            "root",
            "root",
            Duration::from_secs(2),
            None,
        );
        let err = db.sql("RETURN 1", None).unwrap_err();
        assert!(
            err.to_string().contains("cannot reach"),
            "expected 'cannot reach' in {}",
            err
        );
    }

    // ---- VersionTests (db.py stubs `version()`; here `version_problem_for` is the pure function
    //      under test, since Rust has no method to monkeypatch) ------------------------------------

    const URL: &str = "http://127.0.0.1:9/sql";

    #[test]
    fn the_measured_series_is_accepted() {
        assert_eq!(version_problem_for(URL, "surrealdb-3.1.6+20260813.cfbaec4"), None);
    }

    #[test]
    fn another_major_series_is_refused_by_name() {
        let problem = version_problem_for(URL, "surrealdb-4.0.0").expect("must be a problem");
        assert!(!problem.starts_with("NOTE:"), "a different major series is a refusal, not a note");
        for expected in ["4.0.0", "changefeeds", "re-measure"] {
            assert!(problem.contains(expected), "missing {:?} in {}", expected, problem);
        }
    }

    #[test]
    fn another_minor_in_the_same_series_warns_rather_than_refusing() {
        let problem = version_problem_for(URL, "surrealdb-3.4.0").expect("must warn");
        assert!(problem.starts_with("NOTE:"), "{}", problem);
        assert!(problem.contains("3.4.0"));
    }

    #[test]
    fn a_server_that_reports_no_version_is_a_problem_not_a_pass() {
        let problem = version_problem_for(URL, "who knows").expect("must be a problem");
        assert!(problem.contains("does not contain one"));
    }

    #[test]
    fn the_version_url_is_derived_from_the_sql_url() {
        // db.py's test proves version_problem() asks version() for the URL the client was built
        // with; version_problem_for takes that URL directly, so the same fact is that it appears
        // verbatim in every message it produces.
        let problem = version_problem_for(URL, "surrealdb-4.0.0").unwrap();
        assert!(problem.contains(URL));
    }

    // ---- write_verb / _denied plumbing -------------------------------------------------------

    #[test]
    fn write_verb_matches_case_insensitively_on_a_word_boundary() {
        assert_eq!(write_verb("  create a:1 SET x=1"), Some("CREATE"));
        assert_eq!(write_verb("CREATED_AT x=1"), None, "must not match a prefix of a longer word");
        assert_eq!(write_verb("SELECT * FROM a"), None);
        assert_eq!(write_verb("RELATE a->b->c"), Some("RELATE"));
    }

    #[test]
    fn write_verb_never_panics_on_a_multi_byte_character_at_the_verb_boundary() {
        // Regression for blocking finding B2: the byte offset right after a 6-byte verb ("CREATE",
        // "UPDATE", ...) used to be sliced with a raw `str::split_at`, which panics
        // ("byte index N is not a char boundary") whenever that offset lands mid-codepoint.
        let text = format!("{}{}{}", "aaaaa", '\u{e9}', "bbb"); // e-acute is 2 bytes in UTF-8
        assert_eq!(write_verb(&text), None);
        // Same hazard one character earlier, right where a verb WOULD match.
        let text2 = format!("CREAT{}bbb", '\u{e9}');
        assert_eq!(write_verb(&text2), None);
    }

    #[test]
    fn strip_comments_drops_exactly_one_trailing_newline_like_python_splitlines() {
        // Regression: an earlier port used `text.split('\n')` (which keeps a phantom trailing empty
        // element) instead of Python's `splitlines()` (which does not), so a candidate agreed with
        // the oracle everywhere except the very last line of any text ending in "\n" -- caught by
        // `harness/differential.py --split` against `groundworks/patterns/archive_bin.surql`.
        assert_eq!(strip_comments("SELECT 1;\n"), "SELECT 1;");
        assert_eq!(strip_comments("SELECT 1;\n\n"), "SELECT 1;\n");
        assert_eq!(strip_comments("SELECT 1;"), "SELECT 1;");
    }

    #[test]
    fn strip_comments_leaves_quoted_dashes_alone() {
        let text = "SELECT '--not a comment' FROM a; -- real comment\nSELECT 1;";
        let stripped = strip_comments(text);
        assert!(stripped.contains("'--not a comment'"));
        assert!(!stripped.contains("real comment"));
    }
}
