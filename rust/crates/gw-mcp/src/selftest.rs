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
        "a passed packet is final",
        err && text.contains("already passed"),
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
        err && text.contains("not found word for word")
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
