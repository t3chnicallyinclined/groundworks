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
pub const TOOLS: [&str; 27] = [
    "answer",
    "ask",
    "bootstrap",
    "doctor",
    "env_show",
    "estate_scan",
    "expert_list",
    "gate",
    "grep",
    "intake",
    "journal_add",
    "journal_read",
    "knowledge_get",
    "knowledge_list",
    "knowledge_search",
    "lens",
    "panel",
    "panel_open",
    "panel_submit",
    "profile",
    "project_list",
    "project_register",
    "project_show",
    "run",
    "search",
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
    std::fs::write(
        workspace.join("demo/README.md"),
        "# Demo\n\nA demo project for the selftest.\n",
    )?;
    std::fs::create_dir_all(workspace.join("demo/docs"))?;
    std::fs::write(
        workspace.join("demo/docs/GUIDE.md"),
        "# Guide\n\nRollback netcode resimulates confirmed frames.\n",
    )?;
    std::fs::write(workspace.join("demo/.env"), "TOKEN=never-ingested\n")?;
    let git = |args: &[&str]| -> Result<()> {
        let ok = Command::new("git")
            .args(["-c", "user.name=selftest", "-c", "user.email=selftest@localhost", "-C"])
            .arg(workspace.join("demo"))
            .args(args)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()?
            .success();
        if !ok {
            bail!("git {} failed in the selftest's demo project", args.join(" "));
        }
        Ok(())
    };
    git(&["init", "-q"])?;
    git(&["add", "-A", "-f"])?;
    git(&["commit", "-q", "-m", "initial import of the demo"])?;
    // Never committed: the doc is read, the code is not.
    std::fs::write(
        workspace.join("demo/docs/LATE-NOTE.md"),
        "# Late note\n\nThe zephyr flag is unsafe; nobody committed this.\n",
    )?;
    std::fs::write(workspace.join("demo/scratch.rs"), "fn zephyr_scratch() {}\n")?;
    std::fs::create_dir_all(base.join("experts"))?;
    std::fs::write(
        base.join("experts/selftest-expert.md"),
        "---\nname: selftest-expert\ndescription: Owns the demo notes and guide.\n---\n\nSee `notes.txt` and `docs/GUIDE.md`.\n",
    )?;
    std::fs::write(
        base.join("experts/selftest-reviewer.md"),
        "---\nname: selftest-reviewer\ndescription: Reviews the demo.\n---\n\n## What you check\n- **Rollback.** What it resimulates.\n",
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
        "name = \"demo\"\ndir = \"demo\"\nsummary = \"green\"\ngate = [\"echo green\"]\nrelated = [\"red\"]\n\n[envs.prod]\nprotected = true\n",
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
    let experts_env = base.join("experts").display().to_string();
    let mut s = Client::start(
        exe,
        &[
            ("GROUNDWORKS_HOME", &home_env),
            ("GROUNDWORKS_MACHINE", "SELFTEST"),
            ("GROUNDWORKS_EXPERTS", &experts_env),
        ],
        &["GROUNDWORKS_START"],
    )?;
    let tools = s.tools()?;
    c.check(
        "the tool list is exactly the 27 tools",
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

    let (err, text) = s.call(
        "journal_read",
        json!({"project": "demo", "since": "2000-01-01T00:00:00Z"}),
    )?;
    c.check(
        "journal_read since a past time returns the entries",
        !err && text.contains("the parser rejects empty input"),
        &text,
    );
    let (err, text) = s.call(
        "journal_read",
        json!({"project": "demo", "since": "2999-01-01T00:00:00Z"}),
    )?;
    c.check(
        "journal_read since a future time returns nothing, and says since when",
        !err && text.contains("no matching journal entries since 2999-01-01T00:00:00Z"),
        &text,
    );
    let (err, text) = s.call("journal_read", json!({"project": "demo", "since": "yesterday"}))?;
    c.check(
        "a since that is not RFC 3339 is refused",
        err && text.contains("RFC 3339"),
        &text,
    );

    let (err, text) = s.call("intake", json!({"project": "demo"}))?;
    c.check(
        "intake reads the project, names the expert whose definition cites it, and skips the secret",
        !err && text.contains("selftest-expert (2)") && text.contains("looks like a secret"),
        &text,
    );
    let (err, text) = s.call("search", json!({"query": "rejects empty", "project": "demo"}))?;
    c.check(
        "search finds a file's text with its locator and expert",
        !err && text.contains("demo:notes.txt#L1-2") && text.contains("selftest-expert"),
        &text,
    );
    let (err, text) = s.call("search", json!({"query": "rollbacks", "expert": "selftest-expert"}))?;
    c.check(
        "search by expert, stemmed, across shared projects, naming what it skipped",
        !err && text.contains("docs/GUIDE.md") && text.contains("secret (isolated)"),
        &text,
    );
    let (err, text) = s.call(
        "search",
        json!({"query": "initial import", "project": "demo", "kinds": ["commit"]}),
    )?;
    c.check(
        "commits are searchable units",
        !err && text.contains("demo:commit:"),
        &text,
    );
    let (err, text) = s.call("search", json!({"query": "never-ingested", "project": "demo"}))?;
    c.check(
        "a secret file's contents are never ingested",
        !err && text.contains("no units match"),
        &text,
    );
    let (err, text) = s.call("grep", json!({"pattern": "ZEPHYR flag", "project": "demo"}))?;
    c.check(
        "grep finds an exact phrase case-insensitively and names its file line",
        !err && text.contains("demo:docs/LATE-NOTE.md:3 (in #L1-3") && text.contains("The zephyr flag is unsafe"),
        &text,
    );
    let (err, text) = s.call("grep", json!({"pattern": "/zeph.r fl[a-z]g/", "project": "demo"}))?;
    c.check("grep takes a /regex/", !err && text.contains("LATE-NOTE.md:3"), &text);
    let (err, text) = s.call("grep", json!({"pattern": "never-ingested", "project": "demo"}))?;
    c.check(
        "grep sees only what was ingested (not the secret file)",
        !err && text.contains("no ingested unit contains"),
        &text,
    );
    let (err, text) = s.call("search", json!({"query": "zephyr", "project": "demo"}))?;
    c.check(
        "an untracked doc is ingested, with no commit; untracked code is not",
        !err && text
            .lines()
            .find(|l| l.contains("demo:docs/LATE-NOTE.md"))
            .is_some_and(|l| !l.contains(" @ "))
            && !text.contains("scratch.rs"),
        &text,
    );

    // ask, then answer: a true quote from a read candidate passes; a made-up one, or a skipped read, fails.
    let (err, text) = s.call(
        "ask",
        json!({"question": "what does rollback resimulate", "project": "demo"}),
    )?;
    let packet = text.split(' ').next().unwrap_or_default().to_owned();
    let n = text.matches("\n[").count();
    c.check(
        "ask retrieves numbered candidates in full into a packet, each dated",
        !err && packet.starts_with("ask_packet:")
            && n >= 1
            && text.contains("3| Rollback netcode resimulates")
            && text.contains("; changed 20"),
        &text,
    );
    c.check(
        "an ask on a project also reaches the projects its entry names as related, and says so",
        text.lines()
            .next()
            .is_some_and(|l| l.contains("demo") && l.contains("red")),
        &text,
    );
    let all: Vec<usize> = (1..=n).collect();
    let (err, text) = s.call(
        "answer",
        json!({"packet": packet, "read": all, "answer": "Rollback resimulates confirmed frames only.", "record": true,
               "quotes": [{"n": 1, "lines": [3, 3], "use": "support"}]}),
    )?;
    c.check(
        "answer passes with a quote given as a line range, says what was not read, and records the fact",
        !err && text.contains("check PASS")
            && text.contains("not read")
            && text.contains("CONFIRMED by docs/GUIDE.md#L3-3"),
        &text,
    );
    let (err, text) = s.call(
        "ask",
        json!({"question": "what does rollback resimulate", "project": "demo"}),
    )?;
    c.check(
        "the next ask puts the recorded fact first, above the files",
        !err && text.contains("[1] RECORDED FACT (CONFIRMED): Rollback resimulates confirmed frames only."),
        &text,
    );
    let (err, text) = s.call("answer", json!({"packet": packet, "read": all, "answer": "again"}))?;
    c.check(
        "a second answer after a pass repeats the verdict instead of failing",
        !err && text.contains("already passed its check; the verdict stands"),
        &text,
    );
    let (_, text) = s.call(
        "ask",
        json!({"question": "what does rollback resimulate", "project": "demo"}),
    )?;
    let packet = text.split(' ').next().unwrap_or_default().to_owned();
    let (err, text) = s.call(
        "answer",
        json!({"packet": packet, "read": [], "answer": "made up",
               "quotes": [{"n": 1, "quote": "rollback predicts the future perfectly", "use": "support"}]}),
    )?;
    c.check(
        "an answer from nothing read is NOT_EXECUTED, never a pass",
        err && text.contains("NOT_EXECUTED"),
        &text,
    );
    let (_, text) = s.call(
        "ask",
        json!({"question": "what does rollback resimulate", "project": "demo"}),
    )?;
    let packet = text.split(' ').next().unwrap_or_default().to_owned();
    // The recorded fact now sits at [1]; the guide is [2].
    let all: Vec<usize> = (1..=text
        .matches(
            "
[",
        )
        .count())
        .collect();
    let (err, text) = s.call(
        "answer",
        json!({"packet": packet, "read": all, "answer": "made up",
               "quotes": [{"n": 2, "quote": "rollback predicts the future perfectly", "use": "support"}]}),
    )?;
    c.check(
        "a made-up quote fails the check and the refusal names the nearest line",
        err && text.contains("not found in candidate")
            && text.contains("nearest line reads: \"Rollback netcode resimulates confirmed frames.\""),
        &text,
    );
    let (err, text) = s.call(
        "answer",
        json!({"packet": packet, "read": all, "answer": "fixed",
               "quotes": [{"n": 2, "quote": "resimulates confirmed frames", "use": "support"}]}),
    )?;
    c.check(
        "a failed packet may be answered again on the same candidates",
        !err && text.contains("check PASS"),
        &text,
    );

    // Lenses: a finding through one lens, attacked by another; both stay on record, side by side.
    let finding = json!({"project": "demo", "kind": "fact", "text": "rollback resimulates every frame",
                         "source": "selftest", "confidence": "INFERRED", "by": "selftest", "lens": "selftest-expert"});
    let (err, text) = s.call("journal_add", finding)?;
    let fact = text
        .split_whitespace()
        .nth(1)
        .unwrap_or_default()
        .trim_end_matches(':')
        .to_owned();
    c.check(
        "a finding is recorded through a lens",
        !err && fact.starts_with("fact:") && text.contains("[lens selftest-expert]"),
        &text,
    );
    let attack = json!({"project": "demo", "kind": "fact", "text": "only confirmed frames are resimulated",
                        "source": "notes.txt", "confidence": "INFERRED", "by": "selftest", "lens": "selftest-reviewer",
                        "attacks": fact, "stance": "disputes"});
    let (err, text) = s.call("journal_add", attack)?;
    c.check(
        "another lens may dispute it",
        !err && text.contains("disputes fact:"),
        &text,
    );
    let (err, text) = s.call("lens", json!({"lens": "selftest-expert", "project": "demo"}))?;
    c.check(
        "reading through the lens shows the finding and the dispute together",
        !err && text.contains("rollback resimulates every frame") && text.contains("DISPUTES by selftest-reviewer"),
        &text,
    );
    let ghost = json!({"project": "demo", "kind": "fact", "text": "x", "source": "s", "confidence": "INFERRED",
                       "by": "selftest", "attacks": "fact:nosuchfact", "stance": "disputes"});
    let (err, text) = s.call("journal_add", ghost)?;
    c.check("an attack on a fact that does not exist is refused", err, &text);
    let (err, text) = s.call("lens", json!({}))?;
    c.check(
        "the lens list reads the checks from the expert file",
        !err && text.contains("selftest-reviewer (standard INFERRED or CONFIRMED): checks Rollback"),
        &text,
    );

    // A panel: locked at open, blind round 1, anonymized round 2, checks before the tally.
    let seats = ["seat_a", "seat_b", "seat_c"];
    let cats = ["record", "risk", "cost"];
    let (err, text) = s.call(
        "panel_open",
        json!({"project": "demo", "panel": {"panel_id": "selftest", "target": "notes.txt",
               "question": "Is the rollback note right?", "options": ["affirm", "reverse"],
               "objection_categories": cats, "seats": seats.iter().map(|id| json!({"id": id})).collect::<Vec<_>>()}}),
    )?;
    let panel = text.split_whitespace().next().unwrap_or_default().to_owned();
    c.check(
        "a panel is locked when it is opened",
        !err && panel.starts_with("panel:") && text.contains("LOCKED"),
        &text,
    );
    let stance = |o: &str| format!("STANCE: {o} | CONFIDENCE: high | DEALBREAKER: no");
    let r1 = |i: usize| if i == 2 { "reverse" } else { "affirm" };
    let submit = |round: &str, seat: &str, text: String| json!({"project": "demo", "panel": panel, "round": round, "seat": seat, "text": text});
    for (i, seat) in seats.iter().enumerate() {
        let text = format!("As {seat}, I read the note on its own terms.\n{}\n", stance(r1(i)));
        s.call("panel_submit", submit("1", seat, text))?;
    }
    let (err, text) = s.call("panel", json!({"project": "demo", "panel": panel}))?;
    c.check(
        "round 1 is anonymized once complete, with every seat id taken out",
        !err && text.contains("Member A") && !seats.iter().any(|s| text.contains(s)),
        &text,
    );
    let (err, text) = s.call("panel_submit", submit("1", "seat_a", stance("affirm")))?;
    c.check(
        "round 1 is closed after anonymizing",
        err && text.contains("round 1 is closed"),
        &text,
    );
    let r2 = |i: usize| {
        format!(
            "Member B overlooks the frames.\nOBJECTION: {} | a concern only this seat raises\n\
             NEW: the note never says which frames are confirmed, seat {i}\n{}\n",
            cats[i],
            stance(r1(i))
        )
    };
    for (i, seat) in seats.iter().enumerate().take(2) {
        s.call("panel_submit", submit("2", seat, r2(i)))?;
    }
    let (err, text) = s.call("panel", json!({"project": "demo", "panel": panel}))?;
    c.check(
        "the tally waits while a re-prompt is owed",
        !err && text.contains("OWED missing_answer seat_c"),
        &text,
    );
    s.call("panel_submit", submit("2", "seat_c", r2(2)))?;
    let (err, text) = s.call("panel", json!({"project": "demo", "panel": panel}))?;
    c.check(
        "once nothing is owed the panel tallies (two of three reach the two-thirds bar)",
        !err && text.contains("CONSENSUS: affirm"),
        &text,
    );
    let (err, text) = s.call("panel_submit", submit("2", "seat_a", r2(0)))?;
    c.check(
        "nothing is accepted after the tally",
        err && text.contains("tallied"),
        &text,
    );

    // The code changes under a confirmed quote: re-ingesting marks the fact citing it stale.
    std::fs::write(
        base.join("work/demo/notes.txt"),
        "first line
the parser now accepts empty input
",
    )?;
    let (err, text) = s.call("intake", json!({"project": "demo"}))?;
    c.check(
        "re-intake re-checks quoted spans against the changed file",
        !err && text.contains("1 quoted span(s) no longer match"),
        &text,
    );
    let (err, text) = s.call("project_show", json!({"project": "demo"}))?;
    c.check(
        "the fact whose quote no longer matches shows as stale",
        !err && text.contains("CONFIRMED (STALE) the parser rejects empty input"),
        &text,
    );

    let (err, text) = s.call(
        "journal_add",
        json!({"project": "demo", "kind": "gotcha", "text": "a green that checked nothing is red", "source": "selftest",
               "confidence": "INFERRED", "by": "selftest"}),
    )?;
    c.check("a gotcha is recorded", !err && text.contains("[gotcha]"), &text);

    let (err, text) = s.call("doctor", json!({}))?;
    c.check(
        "doctor finds no failures, store included",
        !err && text.contains("ok   store"),
        &text,
    );
    drop(s);

    hook_checks(exe, base, &home, &owner, &mut c)?;

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

    // The export: every namespace of the throwaway, as a gzipped file each.
    let backups = base.join("backup");
    let exported = gw_ops::supervise::export(&owner, &backups, 7);
    let namespaces = owner.namespaces().unwrap_or_default();
    c.check(
        "store export writes one gzipped file per namespace",
        exported.as_ref().is_ok_and(|files| {
            files.len() == namespaces.len()
                && !files.is_empty()
                && files.iter().all(|f| std::fs::metadata(f).is_ok_and(|m| m.len() > 20))
        }),
        &format!("{exported:?} for namespaces {namespaces:?}"),
    );

    // The restore drill, both ways: the export restores into a scratch server with every table's
    // count as recorded; a count raised by one in COUNTS.json fails it, and so does a truncated file.
    let drilled = gw_ops::supervise::drill(&owner, &backups, None);
    c.check(
        "the restore drill restores the export into a scratch server, every table count matches, and the agent signs in after access is re-keyed",
        drilled.as_ref().is_ok_and(|d| {
            d.passed
                && d.lines.len() == namespaces.len()
                && d.lines.iter().filter(|l| l.contains("the agent signs in")).count() >= 2
                && d.dir.join(gw_ops::supervise::DRILL_FILE).is_file()
        }),
        &format!("{drilled:?}"),
    );
    if let Ok(d) = &drilled {
        let counts_path = d.dir.join(gw_ops::supervise::COUNTS_FILE);
        let original = std::fs::read_to_string(&counts_path)?;
        let mut counts: serde_json::Value = serde_json::from_str(&original)?;
        let mut bumped = false;
        if let Some(ns) = counts.as_object_mut().and_then(|m| m.values_mut().next())
            && let Some(table) = ns.as_object_mut().and_then(|m| m.values_mut().next())
            && let Some(range) = table.as_array_mut()
        {
            for v in range.iter_mut() {
                *v = json!(v.as_i64().unwrap_or(0) + 1);
            }
            bumped = true;
        }
        std::fs::write(&counts_path, counts.to_string())?;
        let tampered = gw_ops::supervise::drill(&owner, &backups, None);
        c.check(
            "a count raised by one in COUNTS.json fails the drill and names the table",
            bumped
                && tampered.as_ref().is_ok_and(|t| {
                    !t.passed && t.lines.iter().any(|l| l.starts_with("FAIL") && l.contains("restored"))
                }),
            &format!("{tampered:?}"),
        );
        std::fs::write(&counts_path, original)?;
        let gz = std::fs::read_dir(&d.dir)?
            .filter_map(Result::ok)
            .map(|e| e.path())
            .find(|p| p.extension().is_some_and(|e| e == "gz"))
            .context("an export file to truncate")?;
        let bytes = std::fs::read(&gz)?;
        std::fs::write(&gz, &bytes[..bytes.len() / 2])?;
        let truncated = gw_ops::supervise::drill(&owner, &backups, None);
        c.check(
            "a truncated export file fails the drill",
            truncated.as_ref().is_ok_and(|t| !t.passed),
            &format!("{truncated:?}"),
        );
        std::fs::write(&gz, bytes)?;
    }

    supervisor_checks(exe, base, &mut c)?;

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

/// Run one plugin hook as Claude Code would: the event's JSON on stdin, the answer on stdout. The
/// inherited session variables are removed so a selftest run inside a Claude Code session is not
/// confused by that session's own project.
fn hook(
    exe: &Path,
    home: &Path,
    event: &str,
    input: &Value,
    env: &[(&str, &str)],
    rules: Option<&Path>,
) -> Result<Value> {
    let mut cmd = Command::new(exe);
    cmd.args(["hook", event]);
    if let Some(rules) = rules {
        cmd.arg("--rules").arg(rules);
    }
    for key in [
        "GROUNDWORKS_PROJECT",
        "GROUNDWORKS_SESSION_ID",
        "GROUNDWORKS_SESSION_START",
        "GROUNDWORKS_DOWN",
        "GROUNDWORKS_ENFORCE",
        "GROUNDWORKS_DENY_RULES",
        "GROUNDWORKS_START",
        "GROUNDWORKS_CLIENT",
        "CLAUDE_ENV_FILE",
    ] {
        cmd.env_remove(key);
    }
    cmd.env("GROUNDWORKS_HOME", home)
        .env("GROUNDWORKS_MACHINE", "SELFTEST")
        .envs(env.iter().copied())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd.spawn().context("starting the hook")?;
    child
        .stdin
        .take()
        .context("hook stdin")?
        .write_all(input.to_string().as_bytes())?;
    let out = child.wait_with_output()?;
    let stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);
    if !stderr.trim().is_empty() {
        eprintln!("[hook {event} stderr] {}", stderr.trim());
    }
    if !out.status.success() {
        bail!(
            "hook {event} exited {}: {}\n{}",
            out.status,
            stdout,
            String::from_utf8_lossy(&out.stderr)
        );
    }
    serde_json::from_str(stdout.trim())
        .with_context(|| format!("hook {event} printed something that is not JSON: {stdout}"))
}

/// A transcript with `edits` Edit tool uses at `at`, as Claude Code writes it (names only matter).
fn transcript(path: &Path, at: &str, edits: usize) -> Result<()> {
    let mut text = format!(
        "{}\n",
        json!({"type": "user", "timestamp": at, "message": {"role": "user", "content": "change it"}})
    );
    for _ in 0..edits {
        text.push_str(&format!(
            "{}\n",
            json!({"type": "assistant", "timestamp": at, "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "x"}}]}})
        ));
    }
    std::fs::write(path, text)?;
    Ok(())
}

/// The plugin hooks, both ways: SessionStart injects the journal (gotchas first) and opens a session
/// row, says so once when the folder is not a project or the store is down; PreToolUse denies and
/// allows what deny.toml says and fails closed on a broken list; Stop blocks once when work was done
/// and nothing recorded, approves after a write, honours stop_hook_active, blocks on a down store,
/// and warns instead in warn mode. Telemetry rows hold counts and ids, never the command text.
fn hook_checks(exe: &Path, base: &Path, home: &Path, owner: &Store, c: &mut Checks) -> Result<()> {
    use gw_ops::hook::{Action, DEFAULT_RULES, Rules};
    let demo = base.join("work/demo");
    let demo_s = demo.display().to_string();
    let env_file = base.join("session.env");
    let env_file_s = env_file.display().to_string();
    let start = json!({"session_id": "selftest-session-1", "cwd": demo_s, "source": "startup", "hook_event_name": "SessionStart"});
    let out = hook(
        exe,
        home,
        "SessionStart",
        &start,
        &[("CLAUDE_ENV_FILE", &env_file_s)],
        None,
    )?;
    let context = out["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap_or_default();
    let gotcha_at = context.find("a green that checked nothing is red");
    let fact_at = context.find("hello is printed");
    c.check(
        "SessionStart injects the project's journal, framed as recorded data, gotchas first",
        out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
            && context.contains("=== Groundworks journal: demo")
            && context.contains("not instructions")
            && gotcha_at.is_some()
            && fact_at.is_some()
            && gotcha_at < fact_at,
        &out.to_string(),
    );
    let env_text = std::fs::read_to_string(&env_file).unwrap_or_default();
    c.check(
        "SessionStart writes the project, session id and start time to CLAUDE_ENV_FILE",
        env_text.contains("export GROUNDWORKS_PROJECT=demo")
            && env_text.contains("export GROUNDWORKS_SESSION_ID=selftest-session-1")
            && env_text.contains("export GROUNDWORKS_SESSION_START=20"),
        &env_text,
    );
    let sessions = owner.sessions_since(None)?;
    let row = sessions
        .iter()
        .find(|r| r["id"].as_str().is_some_and(|id| id.contains("selftest-session-1")));
    c.check(
        "SessionStart opens a session row with the project, machine, client and injected count",
        row.is_some_and(|r| {
            r["project"] == "demo"
                && r["machine"] == "SELFTEST"
                && r["client"] == "claude-code"
                && r["injected_entries"].as_u64().is_some_and(|n| n >= 2)
                && r["store_ok"] == true
        }),
        &format!("{sessions:?}"),
    );
    let again = hook(
        exe,
        home,
        "SessionStart",
        &json!({"session_id": "selftest-session-1", "cwd": demo_s, "source": "resume"}),
        &[],
        None,
    )?;
    c.check(
        "a resumed session injects again without a second session row",
        again["hookSpecificOutput"]["additionalContext"].is_string()
            && owner.sessions_since(None)?.len() == sessions.len(),
        &again.to_string(),
    );

    let nowhere = base.join("nowhere-project");
    std::fs::create_dir_all(&nowhere)?;
    let out = hook(
        exe,
        home,
        "SessionStart",
        &json!({"session_id": "s-nowhere", "cwd": nowhere.display().to_string()}),
        &[],
        None,
    )?;
    c.check(
        "SessionStart in a folder that is not a registered project says so once and injects nothing",
        out["systemMessage"]
            .as_str()
            .is_some_and(|m| m.contains("not a registered project"))
            && out.get("hookSpecificOutput").is_none(),
        &out.to_string(),
    );

    // A home whose store is on a closed port: the session still starts, with one notice and GROUNDWORKS_DOWN.
    let down_home = base.join("down-home");
    std::fs::create_dir_all(down_home.join("secrets"))?;
    std::fs::create_dir_all(down_home.join("registry"))?;
    copy_dir(&home.join("registry"), &down_home.join("registry"))?;
    std::fs::write(down_home.join("secrets/root.pass"), ROOT_PASS)?;
    std::fs::write(down_home.join("secrets/agent.pass"), AGENT_PASS)?;
    std::fs::write(
        down_home.join("groundworks.toml"),
        format!(
            "workspace = \"{}\"\n\n[store]\nurl = \"http://127.0.0.1:1/sql\"\n",
            base.join("work").display().to_string().replace('\\', "/")
        ),
    )?;
    let down_env = base.join("down.env");
    let down_env_s = down_env.display().to_string();
    let out = hook(
        exe,
        &down_home,
        "SessionStart",
        &start,
        &[("CLAUDE_ENV_FILE", &down_env_s)],
        None,
    )?;
    let down_text = std::fs::read_to_string(&down_env).unwrap_or_default();
    c.check(
        "SessionStart with the store down prints one notice, sets GROUNDWORKS_DOWN and exits 0",
        out["systemMessage"].as_str().is_some_and(|m| m.contains("store down"))
            && out.get("hookSpecificOutput").is_none()
            && down_text.contains("export GROUNDWORKS_DOWN=1"),
        &format!("{out}\n{down_text}"),
    );

    // PreToolUse: the shipped list proves itself in-process, then through the process with --rules.
    let rules = Rules::parse(DEFAULT_RULES)?;
    let failures = rules.selfcheck();
    c.check(
        &format!(
            "every deny.toml sample behaves ({} rules, {} deny samples, {} allow samples)",
            rules.rules.len(),
            rules.rules.iter().map(|r| r.samples_deny.len()).sum::<usize>(),
            rules.rules.iter().map(|r| r.samples_allow.len()).sum::<usize>()
        ),
        failures.is_empty(),
        &failures.join("; "),
    );
    let rules_path = base.join("deny.toml");
    std::fs::write(&rules_path, DEFAULT_RULES)?;
    let bash = |command: &str| json!({"session_id": "selftest-session-1", "cwd": demo_s, "tool_name": "Bash", "tool_input": {"command": command}});
    let mut wrong = Vec::new();
    let mut tried = 0usize;
    for rule in &rules.rules {
        if let Some(sample) = rule.samples_deny.first() {
            tried += 1;
            let out = hook(exe, home, "PreToolUse", &bash(sample), &[], Some(&rules_path))?;
            let decision = out["hookSpecificOutput"]["permissionDecision"].as_str().unwrap_or("");
            let ok = match rule.action {
                Action::Deny => decision == "deny" && out["hookSpecificOutput"]["permissionDecisionReason"].is_string(),
                Action::Warn => {
                    decision == "deny" || out["systemMessage"].as_str().is_some_and(|m| m.contains(&rule.id))
                }
            };
            if !ok {
                wrong.push(format!("{}: {sample:?} -> {out}", rule.id));
            }
        }
        if let Some(sample) = rule.samples_allow.first() {
            tried += 1;
            let out = hook(exe, home, "PreToolUse", &bash(sample), &[], Some(&rules_path))?;
            if out["hookSpecificOutput"]["permissionDecision"] == "deny" {
                wrong.push(format!("{}: allowed {sample:?} -> {out}", rule.id));
            }
        }
    }
    c.check(
        &format!("through the hook, the first deny and allow sample of every rule decide as listed ({tried} commands)"),
        wrong.is_empty() && tried >= 2,
        &wrong.join("; "),
    );
    let out = hook(
        exe,
        home,
        "PreToolUse",
        &bash("git push origin main"),
        &[("GROUNDWORKS_ENFORCE", "warn")],
        Some(&rules_path),
    )?;
    c.check(
        "GROUNDWORKS_ENFORCE=warn turns a deny into a system message",
        out.get("hookSpecificOutput").is_none()
            && out["systemMessage"].as_str().is_some_and(|m| m.contains("git-push")),
        &out.to_string(),
    );
    let out = hook(
        exe,
        home,
        "PreToolUse",
        &bash("bw get password rise3"),
        &[("GROUNDWORKS_ENFORCE", "warn")],
        Some(&rules_path),
    )?;
    c.check(
        "a hard rule stays a deny in warn mode",
        out["hookSpecificOutput"]["permissionDecision"] == "deny",
        &out.to_string(),
    );
    let broken = base.join("broken-deny.toml");
    std::fs::write(
        &broken,
        "[[rule]]\nid = 'x'\naction = 'deny'\nregex = '('\nreason = 'r'\n",
    )?;
    let out = hook(exe, home, "PreToolUse", &bash("ls"), &[], Some(&broken))?;
    c.check(
        "a rule file that does not compile denies every Bash call with a notice (fails closed)",
        out["hookSpecificOutput"]["permissionDecision"] == "deny"
            && out["hookSpecificOutput"]["permissionDecisionReason"]
                .as_str()
                .is_some_and(|r| r.contains("could not be loaded")),
        &out.to_string(),
    );
    let out = hook(
        exe,
        home,
        "PreToolUse",
        &json!({"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": "x"}}),
        &[],
        Some(&rules_path),
    )?;
    c.check(
        "PreToolUse on a tool other than Bash answers nothing",
        out == json!({}),
        &out.to_string(),
    );

    // Stop: three edits and nothing recorded since the start blocks once; a journal write approves.
    std::thread::sleep(std::time::Duration::from_millis(1100));
    let since = gw_ops::pulse::now_iso();
    let transcript_path = base.join("transcript.jsonl");
    transcript(&transcript_path, &since, 3)?;
    let stop_in = json!({"session_id": "selftest-session-1", "cwd": demo_s, "transcript_path": transcript_path.display().to_string(), "stop_hook_active": false});
    let session_env = [
        ("GROUNDWORKS_PROJECT", "demo"),
        ("GROUNDWORKS_SESSION_START", since.as_str()),
    ];
    let out = hook(exe, home, "Stop", &stop_in, &session_env, None)?;
    c.check(
        "Stop blocks when the session edited three files and recorded nothing",
        out["decision"] == "block"
            && out["reason"]
                .as_str()
                .is_some_and(|r| r.contains("Record what you learned")),
        &out.to_string(),
    );
    let mut active = stop_in.clone();
    active["stop_hook_active"] = json!(true);
    let out = hook(exe, home, "Stop", &active, &session_env, None)?;
    c.check(
        "Stop blocks only once: with stop_hook_active it approves",
        out["decision"] == "approve",
        &out.to_string(),
    );
    let out = hook(
        exe,
        home,
        "Stop",
        &stop_in,
        &[session_env[0], session_env[1], ("GROUNDWORKS_ENFORCE", "warn")],
        None,
    )?;
    c.check(
        "in warn mode the Stop block becomes a system message",
        out["decision"] == "approve" && out["systemMessage"].as_str().is_some_and(|m| m.contains("no-writes")),
        &out.to_string(),
    );
    let out = hook(
        exe,
        home,
        "Stop",
        &stop_in,
        &[session_env[0], session_env[1], ("GROUNDWORKS_DOWN", "1")],
        None,
    )?;
    c.check(
        "Stop with GROUNDWORKS_DOWN blocks with the store-down reason",
        out["decision"] == "block" && out["reason"].as_str().is_some_and(|r| r.contains("store down")),
        &out.to_string(),
    );
    let out = hook(exe, &down_home, "Stop", &stop_in, &session_env, None)?;
    c.check(
        "Stop against a store that is down blocks with the store-down reason",
        out["decision"] == "block" && out["reason"].as_str().is_some_and(|r| r.contains("store down")),
        &out.to_string(),
    );
    let empty = base.join("transcript-empty.jsonl");
    transcript(&empty, &since, 0)?;
    let mut read_only = stop_in.clone();
    read_only["transcript_path"] = json!(empty.display().to_string());
    let out = hook(exe, home, "Stop", &read_only, &session_env, None)?;
    c.check(
        "a session that changed nothing is not asked to write",
        out["decision"] == "approve",
        &out.to_string(),
    );
    owner.add_fact(
        &demo,
        "demo",
        &gw_ops::store::FactIn {
            kind: gw_ops::journal::Kind::Status,
            text: "selftest: the edits were recorded".to_owned(),
            confidence: gw_ops::journal::Confidence::Inferred,
            source: "selftest".to_owned(),
            by: "selftest".to_owned(),
            receipt: None,
            owner_statement: None,
            quote: None,
            lens: None,
            attacks: None,
            stance: None,
        },
    )?;
    let out = hook(exe, home, "Stop", &stop_in, &session_env, None)?;
    c.check(
        "after a journal entry since the start, Stop approves (that row is the session's end)",
        out["decision"] == "approve"
            && owner
                .hook_events_since(None)?
                .iter()
                .any(|e| e["hook"] == "Stop" && e["decision"] == "approve" && e["session"] == "selftest-session-1"),
        &out.to_string(),
    );
    let mut sub = stop_in.clone();
    sub["agent_type"] = json!("gw-engineer");
    let out = hook(exe, home, "SubagentStop", &sub, &session_env, None)?;
    let events = owner.hook_events_since(None)?;
    c.check(
        "SubagentStop decides the same way and names the agent on its row",
        out["decision"] == "approve"
            && events
                .iter()
                .any(|e| e["hook"] == "SubagentStop" && e["agent"] == "gw-engineer" && e["decision"] == "approve"),
        &out.to_string(),
    );

    // The telemetry: counts, ids, hashes and timings; never the command or transcript text.
    let blocked = events
        .iter()
        .find(|e| e["hook"] == "Stop" && e["decision"] == "block" && e["rule"] == "no-writes");
    c.check(
        "the blocked Stop is a hook_event row with the edit count, zero writes and a latency",
        blocked.is_some_and(|e| {
            e["tool_uses"]["edit"] == 3
                && e["journal_writes_since_start"] == 0
                && e["latency_ms"].as_i64().is_some_and(|ms| ms >= 0)
                && e["project"] == "demo"
        }),
        &format!("{blocked:?}"),
    );
    let denies: Vec<&Value> = events
        .iter()
        .filter(|e| e["hook"] == "PreToolUse" && e["decision"] == "deny")
        .collect();
    let all_text = serde_json::to_string(&events)?;
    c.check(
        "PreToolUse rows carry the rule id and a 64-hex HMAC, and no row holds a command",
        !denies.is_empty()
            && denies.iter().all(|e| {
                e["rule"].is_string()
                    && e["command_hmac"]
                        .as_str()
                        .is_some_and(|h| h.len() == 64 && h.chars().all(|c| c.is_ascii_hexdigit()))
            })
            && !all_text.contains("git push")
            && !all_text.contains("regcred"),
        &format!(
            "{} deny rows; {}",
            denies.len(),
            denies.first().map(|e| e.to_string()).unwrap_or_default()
        ),
    );
    let rejections = owner.rejections_since(None)?;
    c.check(
        "the CONFIRMED-without-evidence refusal is a rejection row naming the rule, not the text",
        rejections.iter().any(|r| {
            r["surface"] == "journal_add" && r["reason"] == "CONFIRMED needs evidence" && r["project"] == "demo"
        }) && !serde_json::to_string(&rejections)?.contains("unbacked"),
        &format!("{rejections:?}"),
    );
    Ok(())
}

fn copy_dir(from: &Path, to: &Path) -> Result<()> {
    std::fs::create_dir_all(to)?;
    for entry in std::fs::read_dir(from)? {
        let entry = entry?;
        let target = to.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_dir(&entry.path(), &target)?;
        } else {
            std::fs::copy(entry.path(), target)?;
        }
    }
    Ok(())
}

/// The supervisor, on a second throwaway home with no server: it starts one, and when that one is
/// killed it starts another. Every process it started is killed here before returning.
fn supervisor_checks(exe: &Path, base: &Path, c: &mut Checks) -> Result<()> {
    use std::time::{Duration, Instant};
    let home = base.join("sup-home");
    let workspace = base.join("sup-work");
    for dir in [home.join("secrets"), home.join("registry/projects"), workspace.clone()] {
        std::fs::create_dir_all(dir)?;
    }
    let port = std::net::TcpListener::bind("127.0.0.1:0")?.local_addr()?.port();
    let url = format!("http://127.0.0.1:{port}/sql");
    std::fs::write(
        home.join("groundworks.toml"),
        format!(
            "workspace = \"{}\"\n\n[store]\nurl = \"{url}\"\n",
            workspace.display().to_string().replace('\\', "/")
        ),
    )?;
    std::fs::write(home.join("secrets/root.pass"), ROOT_PASS)?;
    std::fs::write(home.join("secrets/agent.pass"), AGENT_PASS)?;
    let log = home.join("db/supervisor.log");
    let mut sup = Command::new(exe)
        .args([
            "store",
            "supervise",
            "--poll-ms",
            "200",
            "--no-export",
            "--exit-after-starts",
            "2",
            "--home",
        ])
        .arg(&home)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .context("starting the supervisor")?;
    let pids = |log: &Path| -> Vec<u32> {
        std::fs::read_to_string(log)
            .unwrap_or_default()
            .lines()
            .filter_map(|l| l.split("started surreal pid ").nth(1))
            .filter_map(|rest| rest.split_whitespace().next()?.parse().ok())
            .collect()
    };
    let wait = |until: &dyn Fn() -> bool, limit: Duration| {
        let deadline = Instant::now() + limit;
        while Instant::now() < deadline {
            if until() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(150));
        }
        until()
    };
    let started = wait(
        &|| gw_store::healthy(&url) && pids(&log).len() == 1,
        Duration::from_secs(25),
    );
    let first = pids(&log);
    c.check(
        "the supervisor starts a store for a home with none",
        started && first.len() == 1,
        &std::fs::read_to_string(&log).unwrap_or_default(),
    );
    let mut restarted = false;
    if let Some(&pid) = first.first() {
        kill_pid(pid);
        let down = wait(&|| !gw_store::healthy(&url), Duration::from_secs(10));
        restarted = down
            && wait(
                &|| gw_store::healthy(&url) && pids(&log).len() == 2,
                Duration::from_secs(25),
            );
    }
    c.check(
        "the supervisor starts it again after it is killed",
        restarted,
        &std::fs::read_to_string(&log).unwrap_or_default(),
    );
    // It exits by itself after the second start; if not, it is killed.
    let exit_deadline = Instant::now() + Duration::from_secs(10);
    while matches!(sup.try_wait(), Ok(None)) && Instant::now() < exit_deadline {
        std::thread::sleep(Duration::from_millis(150));
    }
    if matches!(sup.try_wait(), Ok(None)) {
        let _ = sup.kill();
    }
    let _ = sup.wait();
    for pid in pids(&log) {
        kill_pid(pid);
    }
    wait(&|| !gw_store::healthy(&url), Duration::from_secs(10));
    Ok(())
}

fn kill_pid(pid: u32) {
    let mut cmd = if cfg!(windows) {
        let mut c = Command::new("taskkill");
        c.args(["/PID", &pid.to_string(), "/F"]);
        c
    } else {
        let mut c = Command::new("kill");
        c.args(["-9", &pid.to_string()]);
        c
    };
    let _ = cmd.stdout(Stdio::null()).stderr(Stdio::null()).status();
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
