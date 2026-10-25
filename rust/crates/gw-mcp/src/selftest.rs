//! `groundworks selftest`: does it actually work on this machine? Builds a throwaway home and a
//! throwaway in-memory SurrealDB, starts this very binary as the MCP server, and drives a scripted
//! session through every rule an agent relies on. It never touches the real home or store.
//!
//! The same run is the end-to-end test (`cargo e2e-test`, crates/gw-mcp/tests/e2e.rs).

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

use anyhow::{Context, Result, bail};
use gw_ops::store::Store;
use gw_store::testkit::{AGENT_PASS, OWNER_PASS, ROOT_PASS, Throwaway};
use serde_json::{Value, json};

/// The tools a session with a home must see, exactly.
pub const TOOLS: [&str; 17] = [
    "bootstrap",
    "doctor",
    "env_show",
    "estate_scan",
    "gate",
    "journal_add",
    "journal_read",
    "knowledge_get",
    "knowledge_list",
    "knowledge_search",
    "profile",
    "project_list",
    "project_register",
    "project_show",
    "run",
    "stack_list",
    "stack_show",
];

/// A line-delimited JSON-RPC client over the server's stdio.
struct Client {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    next: u64,
}

impl Client {
    fn start(exe: &Path, env: &[(&str, &str)], remove: &[&str]) -> Result<Self> {
        let mut cmd = Command::new(exe);
        cmd.arg("serve")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        for key in remove {
            cmd.env_remove(key);
        }
        cmd.envs(env.iter().copied());
        let mut child = cmd.spawn().with_context(|| format!("starting {}", exe.display()))?;
        let stdin = child.stdin.take().context("no stdin")?;
        let stdout = BufReader::new(child.stdout.take().context("no stdout")?);
        let mut client = Self {
            child,
            stdin,
            stdout,
            next: 1,
        };
        client.request(
            "initialize",
            json!({"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "selftest", "version": "0"}}),
        )?;
        client.send(&json!({"jsonrpc": "2.0", "method": "notifications/initialized"}))?;
        Ok(client)
    }

    fn send(&mut self, message: &Value) -> Result<()> {
        writeln!(self.stdin, "{message}")?;
        self.stdin.flush()?;
        Ok(())
    }

    fn request(&mut self, method: &str, params: Value) -> Result<Value> {
        let id = self.next;
        self.next += 1;
        self.send(&json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}))?;
        let mut line = String::new();
        loop {
            line.clear();
            if self.stdout.read_line(&mut line)? == 0 {
                bail!("the server closed its output before answering {method}");
            }
            let message: Value =
                serde_json::from_str(line.trim()).context("the server wrote a line that is not JSON")?;
            if message["id"] == id {
                return Ok(message);
            }
        }
    }

    fn tools(&mut self) -> Result<Vec<String>> {
        let reply = self.request("tools/list", json!({}))?;
        let mut names: Vec<String> = reply["result"]["tools"]
            .as_array()
            .context("no tool list")?
            .iter()
            .filter_map(|t| t["name"].as_str().map(str::to_owned))
            .collect();
        names.sort();
        Ok(names)
    }

    /// (is_error, text) of one tool call.
    fn call(&mut self, tool: &str, arguments: Value) -> Result<(bool, String)> {
        let reply = self.request("tools/call", json!({"name": tool, "arguments": arguments}))?;
        if let Some(error) = reply.get("error") {
            return Ok((true, error.to_string()));
        }
        let text = reply["result"]["content"][0]["text"]
            .as_str()
            .unwrap_or_default()
            .to_owned();
        Ok((reply["result"]["isError"].as_bool().unwrap_or(false), text))
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

struct Checks {
    lines: Vec<String>,
    failed: usize,
}

impl Checks {
    fn check(&mut self, name: &str, ok: bool, detail: &str) {
        if ok {
            self.lines.push(format!("pass  {name}"));
        } else {
            self.failed += 1;
            let detail: String = detail.chars().take(400).collect();
            self.lines.push(format!("FAIL  {name}\n        {detail}"));
        }
    }
}

/// A throwaway home: three projects (one green, one red, one the owner isolates), a protected
/// env, a file to quote, the store pointed at `url`.
fn build_home(base: &Path, url: &str) -> Result<PathBuf> {
    let home = base.join(".groundworks");
    let workspace = base.join("work");
    for dir in ["registry/projects", "secrets", "knowledge", "journal"] {
        std::fs::create_dir_all(home.join(dir))?;
    }
    for project in ["demo", "red", "secret"] {
        std::fs::create_dir_all(workspace.join(project))?;
    }
    std::fs::write(
        workspace.join("demo/notes.txt"),
        "first line\nthe parser rejects empty input\n",
    )?;
    let toml_path = |p: &Path| p.display().to_string().replace('\\', "/");
    std::fs::write(
        home.join("groundworks.toml"),
        format!(
            "workspace = \"{}\"\n\n[store]\nurl = \"{url}\"\n",
            toml_path(&workspace)
        ),
    )?;
    std::fs::write(home.join("secrets/root.pass"), ROOT_PASS)?;
    std::fs::write(home.join("secrets/agent.pass"), AGENT_PASS)?;
    std::fs::write(home.join("knowledge/principles.md"), "# Principles\n\nTools first.\n")?;
    std::fs::write(
        home.join("registry/projects/demo.toml"),
        "name = \"demo\"\ndir = \"demo\"\nsummary = \"green\"\ngate = [\"echo green\"]\n\n[envs.prod]\nprotected = true\n",
    )?;
    std::fs::write(
        home.join("registry/projects/red.toml"),
        "name = \"red\"\ndir = \"red\"\nsummary = \"red\"\ngate = [\"echo before\", \"exit 3\"]\n",
    )?;
    std::fs::write(
        home.join("registry/projects/secret.toml"),
        "name = \"secret\"\ndir = \"secret\"\nsummary = \"isolated by the owner\"\n",
    )?;
    Ok(home)
}

/// The first `receipt:<id>` in `text` (a reply says "receipt: receipt:<id>").
fn receipt_in(text: &str) -> Option<String> {
    text.match_indices("receipt:").find_map(|(at, _)| {
        let id: String = text[at + "receipt:".len()..]
            .chars()
            .take_while(char::is_ascii_alphanumeric)
            .collect();
        (!id.is_empty()).then(|| format!("receipt:{id}"))
    })
}

/// Run every check; Ok(report) with the number that failed.
pub fn run(exe: &Path) -> Result<(String, usize)> {
    let base = std::env::temp_dir().join(format!("gw-selftest-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base)?;
    let result = run_in(exe, &base);
    let _ = std::fs::remove_dir_all(&base);
    result
}

fn run_in(exe: &Path, base: &Path) -> Result<(String, usize)> {
    let server = Throwaway::start();
    let home = build_home(base, &server.url())?;
    let mut c = Checks {
        lines: vec![
            format!("store: throwaway {}", server.url()),
            format!("home: {}", home.display()),
        ],
        failed: 0,
    };

    // The owner's part, as the CLI does it: an owner login, and one project isolated.
    let owner = Store::connect(&server.url(), ROOT_PASS, AGENT_PASS);
    owner.provision_owner(OWNER_PASS)?;
    owner.set_level(OWNER_PASS, "proj_secret", "isolated", None)?;

    let home_env = home.display().to_string();
    let mut s = Client::start(
        exe,
        &[("GROUNDWORKS_HOME", &home_env), ("GROUNDWORKS_MACHINE", "SELFTEST")],
        &["GROUNDWORKS_START"],
    )?;
    let tools = s.tools()?;
    c.check(
        "the tool list is exactly the 17 tools",
        tools == TOOLS,
        &format!("{tools:?}"),
    );

    let (err, text) = s.call("project_list", json!({}))?;
    c.check(
        "project_list shows the registry",
        !err && text.contains("demo") && text.contains("red"),
        &text,
    );

    let note = |text: &str, confidence: &str| json!({"project": "demo", "kind": "fact", "text": text, "source": "selftest", "confidence": confidence, "by": "selftest"});
    let (err, text) = s.call("journal_add", note("unbacked", "CONFIRMED"))?;
    c.check(
        "CONFIRMED without evidence is refused",
        err && text.contains("CONFIRMED needs evidence"),
        &text,
    );

    let (err, text) = s.call(
        "run",
        json!({"project": "demo", "env": "local", "command": "echo hello"}),
    )?;
    let receipt = receipt_in(&text);
    c.check(
        "run works, names its machine and leaves a receipt",
        !err && text.contains("hello") && text.contains("SELFTEST") && receipt.is_some(),
        &text,
    );

    if let Some(receipt) = &receipt {
        let mut args = note("hello is printed", "CONFIRMED");
        args["receipt"] = json!(receipt);
        let (err, text) = s.call("journal_add", args)?;
        c.check("a receipt confirms a fact", !err && text.contains("CONFIRMED"), &text);
    }

    let mut args = note("the parser rejects empty input", "CONFIRMED");
    args["quote"] = json!({"file": "notes.txt", "line": 2, "text": "the parser rejects empty input"});
    let (err, text) = s.call("journal_add", args)?;
    c.check(
        "a word-for-word quote confirms a fact",
        !err && text.contains("notes.txt#L2-2"),
        &text,
    );

    let mut args = note("made up", "CONFIRMED");
    args["quote"] = json!({"file": "notes.txt", "line": 2, "text": "the parser accepts empty input"});
    let (err, text) = s.call("journal_add", args)?;
    c.check(
        "a made-up quote is refused",
        err && text.contains("not found word for word"),
        &text,
    );

    let (err, text) = s.call("run", json!({"project": "demo", "env": "prod", "command": "echo x"}))?;
    c.check(
        "a protected env refuses without confirm",
        err && text.contains("protected"),
        &text,
    );

    let (err, text) = s.call("gate", json!({"project": "demo"}))?;
    c.check(
        "a green gate passes and leaves a receipt",
        !err && text.contains("GATE GREEN") && receipt_in(&text).is_some(),
        &text,
    );

    let (err, text) = s.call("gate", json!({"project": "red"}))?;
    c.check(
        "a red gate is reported as an error",
        err && text.contains("GATE RED at step 2/2"),
        &text,
    );

    let mut args = note("should never land", "INFERRED");
    args["project"] = json!("secret");
    let (err, text) = s.call("journal_add", args)?;
    c.check(
        "an isolated project is refused by the shared store",
        err && text.contains("isolated"),
        &text,
    );

    let (err, text) = s.call("project_show", json!({"project": "demo"}))?;
    c.check(
        "project_show reads the journal back, with its evidence",
        !err && text.contains("[quote notes.txt#L2-2]") && text.contains("[receipt:"),
        &text,
    );

    let (err, text) = s.call("doctor", json!({}))?;
    c.check(
        "doctor finds no failures, store included",
        !err && text.contains("ok   store"),
        &text,
    );
    drop(s);

    // A session whose folder is under no home still starts, with the status tool only. The folder
    // must not sit under `base`, which holds the throwaway home.
    let empty = std::env::temp_dir().join(format!("gw-selftest-nowhere-{}", std::process::id()));
    std::fs::create_dir_all(&empty)?;
    let empty_env = empty.display().to_string();
    let mut h = Client::start(exe, &[("GROUNDWORKS_START", &empty_env)], &["GROUNDWORKS_HOME"])?;
    let tools = h.tools()?;
    c.check(
        "outside any home the server starts with only the status tool",
        tools == ["status"],
        &format!("{tools:?}"),
    );
    let (err, text) = h.call("status", json!({}))?;
    c.check(
        "the status tool says why",
        !err && text.contains("not active here"),
        &text,
    );
    drop(h);
    let _ = std::fs::remove_dir_all(&empty);

    let total = c
        .lines
        .iter()
        .filter(|l| l.starts_with("pass") || l.starts_with("FAIL"))
        .count();
    let summary = if c.failed == 0 {
        format!("ALL {total} CHECKS PASSED on {}", gw_ops::home::machine())
    } else {
        format!("{} OF {total} CHECKS FAILED on {}", c.failed, gw_ops::home::machine())
    };
    c.lines.push(summary);
    Ok((c.lines.join("\n"), c.failed))
}

#[cfg(test)]
mod tests {
    use super::receipt_in;

    #[test]
    fn finds_the_receipt_id_after_the_label() {
        assert_eq!(
            receipt_in("receipt: receipt:ab12 (cite it with ...)").as_deref(),
            Some("receipt:ab12")
        );
        assert_eq!(receipt_in("receipt: NOT RECORDED"), None);
    }
}
