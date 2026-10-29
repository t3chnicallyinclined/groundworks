//! Run an eval set with and without Groundworks, grade every answer blind, and report.
//!
//! Each question is asked in a fresh headless Claude Code session (`claude -p`) started in an empty
//! folder, so no project memory or CLAUDE.md reaches it, with the plugin switched off and only the
//! read tools on. The two arms differ in one thing:
//!
//! - `plain`: Read, Grep, Glob, read-only git, over the projects folder.
//! - `groundworks`: the same, plus the Groundworks MCP server's read tools (ask, answer, search, lens,
//!   journal, knowledge) and its output style as the appended system prompt.
//! - `plain-memory`: plain, plus the owner's memory index (MEMORY.md) appended to the system prompt and
//!   the memory folder readable: the owner's everyday setup, without Groundworks.
//! - `agent`: the same tools, with the plugin's `groundworks` agent as the session's main agent
//!   (`--agent`), so its rules are the system prompt rather than an addition to it.
//!
//! The server runs with recording off (`GROUNDWORKS_NO_RECORD`), so no answer can leave a fact that a
//! later question of the same run would find.
//!
//! The prompt is the same in both. A third session, with no tools, grades each answer against the gold
//! answer without knowing which arm wrote it. Every row records tokens, cost, turns and time as Claude
//! Code reports them. Rows are appended as they finish, so an interrupted run resumes where it stopped.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use wait_timeout::ChildExt;

use crate::eval::{Question, load};
use crate::home::Home;
use crate::store::sha256_hex;

pub const ARMS: [&str; 4] = ["plain", "plain-memory", "groundworks", "agent"];
const AGENT: &str = include_str!("../../../../plugin/agents/groundworks.md");
const STYLE: &str = include_str!("../../../../plugin/output-styles/groundworks.md");
const BASE_TOOLS: &str = "Read,Grep,Glob,Bash,ToolSearch";
const GIT_READS: [&str; 5] = [
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(git grep:*)",
    "Bash(git blame:*)",
    "Bash(git ls-files:*)",
];
/// The Groundworks tools that read; nothing that writes the store, runs a command or opens a panel.
const GW_READS: [&str; 11] = [
    "ask",
    "answer",
    "search",
    "lens",
    "project_list",
    "project_show",
    "journal_read",
    "knowledge_get",
    "knowledge_list",
    "knowledge_search",
    "expert_list",
];
const TIMEOUT: Duration = Duration::from_secs(15 * 60);

pub struct Plan<'a> {
    pub set: &'a Path,
    pub out: &'a Path,
    pub only: &'a [String],
    pub arms: &'a [String],
    pub model: &'a str,
    pub grader_model: &'a str,
    /// Per session, passed to `claude --max-budget-usd`.
    pub budget_usd: f64,
    /// Passed to `claude --effort` when set (low, medium, high, xhigh, max).
    pub effort: Option<&'a str>,
    /// A seeding pass: recording stays on, and the prompt asks for `record: true` on a passed answer.
    pub record: bool,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Row {
    pub id: String,
    pub arm: String,
    pub category: String,
    pub project: String,
    pub answer: String,
    pub is_error: bool,
    pub cost_usd: f64,
    pub turns: u64,
    pub duration_ms: u64,
    /// input + cache creation + cache read + output, as reported.
    pub tokens: u64,
    pub output_tokens: u64,
    pub cites_gold: bool,
    /// Tool calls by name, from the session's event stream.
    #[serde(default)]
    pub tools: BTreeMap<String, u64>,
    #[serde(default)]
    pub denials: u64,
    /// Did any tool result name a gold file, and at which call first (0-based)?
    #[serde(default)]
    pub saw_gold: bool,
    #[serde(default)]
    pub first_gold_call: Option<usize>,
    /// A tool result showed a recorded fact (ask puts them first).
    #[serde(default)]
    pub saw_fact: bool,
    /// Every tool call: name, input (cut to 300 chars), size of the result, whether it named a gold file.
    #[serde(default)]
    pub calls: Vec<Value>,
    pub verdict: String,
    pub confidently_wrong: bool,
    pub grade_reason: String,
    pub grade_cost_usd: f64,
    pub at: String,
}

fn prompt(q: &Question, projects: &Path) -> String {
    format!(
        "You are answering a question about the owner's own software projects. They are in {} (one folder per \
         project; private notes in _tickets). Find the answer in the projects' files. Answer in at most 150 words. \
         Cite every claim as path:line. If the projects do not record the answer, or the question's premise is \
         false, say so plainly instead of guessing. If sources disagree, give both and say which is current and \
         why.\n\nProject: {}\nQuestion: {}",
        projects.display(),
        q.project,
        q.question
    )
}

fn style_body() -> &'static str {
    body(STYLE)
}

/// A markdown file's text after its frontmatter.
fn body(text: &'static str) -> &'static str {
    text.strip_prefix("---")
        .and_then(|r| r.split_once("\n---"))
        .map_or(text, |(_, body)| body.trim_start())
}

/// One headless session; returns Claude Code's JSON result. The prompt goes in on stdin (`-p` with
/// no text reads it), because a Windows command line holds at most 32 KB and a packet of candidates
/// does not fit.
pub(crate) fn session(cwd: &Path, prompt: &str, args: &[String], timeout: Duration) -> Result<Value> {
    let mut child = Command::new("claude")
        .current_dir(cwd)
        .arg("-p")
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("starting claude")?;
    if let Some(mut stdin) = child.stdin.take() {
        let text = prompt.to_owned();
        std::thread::spawn(move || {
            let _ = stdin.write_all(text.as_bytes());
        });
    }
    let drain = |pipe: Option<Box<dyn std::io::Read + Send>>| {
        std::thread::spawn(move || {
            let mut s = String::new();
            if let Some(mut p) = pipe {
                let _ = p.read_to_string(&mut s);
            }
            s
        })
    };
    let stdout = drain(
        child
            .stdout
            .take()
            .map(|p| Box::new(p) as Box<dyn std::io::Read + Send>),
    );
    let stderr = drain(
        child
            .stderr
            .take()
            .map(|p| Box::new(p) as Box<dyn std::io::Read + Send>),
    );
    if child.wait_timeout(timeout)?.is_none() {
        let _ = child.kill();
        let _ = child.wait();
        bail!("the session ran past {} s and was stopped", timeout.as_secs());
    }
    let out = stdout.join().unwrap_or_default();
    let err = stderr.join().unwrap_or_default();
    // stream-json: one event per line, the result last. Every tool call is counted into `_tools`, and
    // logged into `_calls` with its input and the full text it returned (matched by tool_use id).
    let mut tools: BTreeMap<String, u64> = BTreeMap::new();
    let mut calls: Vec<Value> = Vec::new();
    let mut by_id: BTreeMap<String, usize> = BTreeMap::new();
    let mut result = None;
    for line in out.lines().filter(|l| l.trim_start().starts_with('{')) {
        let Ok(event) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        let blocks = event["message"]["content"].as_array().cloned().unwrap_or_default();
        match event["type"].as_str() {
            Some("assistant") => {
                for block in blocks.iter().filter(|b| b["type"] == "tool_use") {
                    let name = block["name"].as_str().unwrap_or("?").to_owned();
                    *tools.entry(name.clone()).or_default() += 1;
                    by_id.insert(block["id"].as_str().unwrap_or_default().to_owned(), calls.len());
                    calls.push(json!({"name": name, "input": block["input"], "result": ""}));
                }
            }
            Some("user") => {
                for block in blocks.iter().filter(|b| b["type"] == "tool_result") {
                    let text = match &block["content"] {
                        Value::String(s) => s.clone(),
                        Value::Array(parts) => parts
                            .iter()
                            .filter_map(|p| p["text"].as_str())
                            .collect::<Vec<_>>()
                            .join("\n"),
                        _ => String::new(),
                    };
                    if let Some(i) = block["tool_use_id"].as_str().and_then(|id| by_id.get(id)) {
                        calls[*i]["result"] = json!(text);
                        calls[*i]["is_error"] = block["is_error"].clone();
                    }
                }
            }
            Some("result") => result = Some(event),
            _ => {}
        }
    }
    if let Some(mut r) = result {
        r["_tools"] = json!(tools);
        r["_calls"] = json!(calls);
        return Ok(r);
    }
    serde_json::from_str(out.trim()).with_context(|| {
        format!(
            "claude did not print JSON: {} | stderr: {}",
            &out[..out.len().min(400)],
            &err[..err.len().min(400)]
        )
    })
}

fn tokens(usage: &Value) -> (u64, u64) {
    let n = |k: &str| usage[k].as_u64().unwrap_or(0);
    let out = n("output_tokens");
    (
        n("input_tokens") + n("cache_creation_input_tokens") + n("cache_read_input_tokens") + out,
        out,
    )
}

/// What a session may never read, whichever directories it was given: the home's secrets, the
/// store's files and its exports. Second layer behind the directory list (gw-security, 2026-10-27:
/// `--add-dir` of the whole workspace put the secrets and the exports inside Read/Grep/Glob).
const DENIED: [&str; 6] = [
    "Read(**/.groundworks/secrets/**)",
    "Read(**/.groundworks/db/**)",
    "Read(**/.groundworks/backup/**)",
    "Read(.groundworks/secrets/**)",
    "Read(.groundworks/db/**)",
    "Read(.groundworks/backup/**)",
];
/// Git reads that are writes or execs in disguise: `git log --output=<file>` writes, `git grep -O`
/// opens a program. Prefix rules, so an option placed later in the command still passes; the
/// allowlist stays read-only in intent and these close the two documented forms.
const DENIED_GIT: [&str; 4] = [
    "Bash(git log --output:*)",
    "Bash(git log -o:*)",
    "Bash(git grep -O:*)",
    "Bash(git grep --open-files-in-pager:*)",
];

/// The session's settings: the plugin off, and the deny list.
fn settings_json() -> String {
    let denied: Vec<&str> = DENIED.iter().chain(DENIED_GIT.iter()).copied().collect();
    json!({"enabledPlugins": {"groundworks@groundworks": false}, "permissions": {"deny": denied}}).to_string()
}

fn answer_args(
    arm: &str,
    plan: &Plan<'_>,
    add_dirs: &[PathBuf],
    run: &Path,
    memory: Option<&Path>,
) -> Result<Vec<String>> {
    let mut args: Vec<String> = [
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--model",
        plan.model,
        "--max-budget-usd",
        &plan.budget_usd.to_string(),
        "--settings",
        &settings_json(),
        "--strict-mcp-config",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    // The registered projects' folders, one by one: never the workspace, whose other folders (the
    // home with its secrets and exports, and folders no project registers) are not the sessions'.
    for dir in add_dirs {
        args.push("--add-dir".into());
        args.push(dir.display().to_string());
    }
    args.extend(
        ["--tools", BASE_TOOLS, "--permission-mode", "dontAsk"]
            .iter()
            .map(|s| s.to_string()),
    );
    if let Some(e) = plan.effort {
        args.push("--effort".into());
        args.push(e.to_owned());
    }
    let mut allowed: Vec<String> = ["Read", "Grep", "Glob", "ToolSearch"]
        .iter()
        .map(|s| s.to_string())
        .collect();
    allowed.extend(GIT_READS.iter().map(|s| s.to_string()));
    match arm {
        "plain" | "plain-memory" => {
            args.push("--mcp-config".into());
            args.push(r#"{"mcpServers":{}}"#.into());
            if arm == "plain-memory" {
                let dir = memory.context("the plain-memory arm needs a registered `memory` project")?;
                let index = std::fs::read_to_string(dir.join("MEMORY.md")).context("reading MEMORY.md")?;
                args.push("--add-dir".into());
                args.push(dir.display().to_string());
                args.push("--append-system-prompt".into());
                args.push(format!(
                    "Your notes from earlier sessions (the index; each entry names a file in {}):

{index}",
                    dir.display()
                ));
            }
        }
        "groundworks" | "agent" => {
            let config = run.join("mcp-groundworks.json");
            args.push("--mcp-config".into());
            args.push(config.display().to_string());
            if arm == "groundworks" {
                args.push("--append-system-prompt".into());
                args.push(style_body().to_owned());
            } else {
                args.push("--agents".into());
                args.push(
                    json!({"groundworks": {"description": "The Groundworks agent", "prompt": body(AGENT)}}).to_string(),
                );
                args.push("--agent".into());
                args.push("groundworks".into());
            }
            allowed.extend(GW_READS.iter().map(|t| format!("mcp__groundworks__{t}")));
        }
        other => bail!("unknown arm {other:?}; arms are {}", ARMS.join(", ")),
    }
    args.push("--allowedTools".into());
    args.extend(allowed);
    Ok(args)
}

fn grade_prompt(q: &Question, answer: &str) -> String {
    format!(
        "You grade an answer to a question about a software project against the gold answer its owner \
         approved. Use only what is below; no outside knowledge, no tools.\n\n\
         verdict:\n- correct: states every must-mention fact and nothing that contradicts the gold.\n\
         - partial: some of the gold facts, nothing that contradicts it.\n\
         - wrong: contradicts the gold, or gives the known wrong answer.\n\
         - abstained: says it could not find or does not know, and asserts nothing wrong.\n\
         For a trap (the question's premise is false), correct means it says the premise is false or not \
         recorded; answering as if the premise were true is wrong.\n\
         confidently_wrong: true when the answer asserts something that contradicts the gold without flagging \
         doubt.\n\nReply with ONE JSON object and nothing else: \
         {{\"verdict\": \"correct|partial|wrong|abstained\", \"confidently_wrong\": true|false, \
         \"missing\": [\"must-mention facts it lacks\"], \"reason\": \"one sentence\"}}\n\n\
         TRAP: {}\nQUESTION: {}\nGOLD ANSWER: {}\nMUST MENTION: {}\nKNOWN WRONG ANSWER: {}\n\nANSWER TO GRADE:\n{}",
        q.category == "trap",
        q.question,
        q.gold_answer,
        q.must_mention.join(" | "),
        q.wrong_answer.as_deref().unwrap_or("(none given)"),
        answer
    )
}

fn grade(cwd: &Path, q: &Question, answer: &str, model: &str) -> Result<(Value, f64)> {
    let args: Vec<String> = [
        "--output-format",
        "json",
        "--no-session-persistence",
        "--model",
        model,
        "--settings",
        r#"{"enabledPlugins":{"groundworks@groundworks":false}}"#,
        "--strict-mcp-config",
        "--mcp-config",
        r#"{"mcpServers":{}}"#,
        "--tools",
        "",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    // A grader that prints broken JSON gets one more go; its cost counts both times.
    let mut cost = 0.0;
    let mut last = None;
    for _ in 0..2 {
        let out = session(cwd, &grade_prompt(q, answer), &args, Duration::from_secs(300))?;
        cost += out["total_cost_usd"].as_f64().unwrap_or(0.0);
        let text = out["result"].as_str().unwrap_or_default();
        let parsed = text
            .find('{')
            .and_then(|a| text.rfind('}').map(|b| &text[a..=b]))
            .context("the grader printed no JSON")
            .and_then(|j| serde_json::from_str::<Value>(j).context("the grader's JSON"));
        match parsed {
            Ok(v) => return Ok((v, cost)),
            Err(e) => last = Some(e),
        }
    }
    Err(last.unwrap_or_else(|| anyhow::anyhow!("the grader failed twice")))
}

/// Did the answer cite a gold file at (about) a gold line? Mechanical, so the grader cannot drift on it.
fn cites_gold(q: &Question, answer: &str) -> bool {
    q.gold.iter().any(|g| {
        let name = g.file.rsplit('/').next().unwrap_or(&g.file);
        answer.match_indices(name).any(|(i, _)| {
            let rest = &answer[i + name.len()..];
            let digits: String = rest
                .trim_start_matches([':', '#', 'L'])
                .chars()
                .take_while(char::is_ascii_digit)
                .collect();
            digits.parse::<usize>().is_ok_and(|n| n.abs_diff(g.line) <= 10)
        })
    })
}

fn done(results: &Path) -> Result<BTreeSet<(String, String)>> {
    if !results.exists() {
        return Ok(BTreeSet::new());
    }
    Ok(std::fs::read_to_string(results)?
        .lines()
        .filter_map(|l| serde_json::from_str::<Row>(l).ok())
        .map(|r| (r.id, r.arm))
        .collect())
}

/// Run the plan; returns a progress log.
pub fn run(home: &Home, plan: &Plan<'_>) -> Result<String> {
    let questions: Vec<Question> = load(plan.set)?
        .into_iter()
        .map(|(line, q)| q.map_err(|e| anyhow::anyhow!("line {line}: {e}")))
        .collect::<Result<_>>()?;
    let (report, ok) = crate::eval::check(home, plan.set)?;
    ensure!(ok, "the set fails its own check; fix it first:\n{report}");
    for arm in plan.arms {
        ensure!(
            ARMS.contains(&arm.as_str()),
            "unknown arm {arm:?}; arms are {}",
            ARMS.join(", ")
        );
    }
    std::fs::create_dir_all(plan.out)?;
    let cwd = plan.out.join("empty");
    std::fs::create_dir_all(&cwd)?;
    ensure!(
        std::fs::read_dir(&cwd)?.next().is_none(),
        "{} must stay empty: it is the sessions' working folder",
        cwd.display()
    );
    let home_path = home.root.display().to_string().replace('\\', "/");
    // The sessions' server: the installed `groundworks` unless GROUNDWORKS_EVAL_EXE names a
    // candidate binary, so a change can be measured before it is installed for every session.
    let exe = std::env::var("GROUNDWORKS_EVAL_EXE")
        .map(|p| p.replace('\\', "/"))
        .unwrap_or_else(|_| "groundworks".to_owned());
    std::fs::write(
        plan.out.join("mcp-groundworks.json"),
        serde_json::to_string_pretty(&json!({"mcpServers": {"groundworks": {
            "command": exe, "args": ["serve"],
            // GROUNDWORKS_NO_REFRESH: the server must not re-ingest during a run, or the store moves
            // under the packets being measured (the auto-intake branch honours it).
            "env": if plan.record { json!({"GROUNDWORKS_HOME": home_path, "GROUNDWORKS_NO_REFRESH": "1"}) } else { json!({"GROUNDWORKS_HOME": home_path, "GROUNDWORKS_NO_RECORD": "1", "GROUNDWORKS_NO_REFRESH": "1"}) }}}}))?,
    )?;
    let set_sha = sha256_hex(&[std::fs::read(plan.set)?.as_slice()]);
    let meta = plan.out.join("run.json");
    if !meta.exists() {
        std::fs::write(
            &meta,
            serde_json::to_string_pretty(&json!({
                "set": plan.set.display().to_string(), "set_sha256": set_sha, "model": plan.model, "effort": plan.effort, "record": plan.record,
                "grader_model": plan.grader_model, "arms": plan.arms, "budget_usd": plan.budget_usd,
                "started_at": now(), "machine": crate::home::machine(),
                "claude_version": Command::new("claude").arg("--version").output().ok()
                    .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_owned()),
                "groundworks_exe": exe,
                "groundworks_version": Command::new(&exe).arg("--version").output().ok()
                    .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_owned()),
            }))?,
        )?;
    } else {
        let m: Value = serde_json::from_str(&std::fs::read_to_string(&meta)?)?;
        ensure!(
            m["set_sha256"] == set_sha,
            "the set changed since this run started; start a new run folder"
        );
    }
    let results = plan.out.join("results.jsonl");
    let finished = done(&results)?;
    let projects = home.workspace.clone();
    let registry = crate::registry::Registry::load(home)?;
    let memory = registry.project("memory").ok().map(|p| p.path(&home.workspace));
    let add_dirs: Vec<PathBuf> = registry
        .projects
        .values()
        .map(|p| p.path(&home.workspace))
        .filter(|p| p.is_dir())
        .collect();
    let mut log = String::new();
    for q in questions
        .iter()
        .filter(|q| plan.only.is_empty() || plan.only.contains(&q.id))
    {
        // Which arm goes first alternates by question, so neither always runs on a warm cache.
        let mut arms: Vec<&String> = plan.arms.iter().collect();
        if sha256_hex(&[q.id.as_bytes()]).as_bytes()[0] % 2 == 1 {
            arms.reverse();
        }
        for arm in arms {
            if finished.contains(&(q.id.clone(), arm.clone())) {
                continue;
            }
            let row = one(q, arm, plan, &projects, &add_dirs, &cwd, memory.as_deref())?;
            writeln!(
                log,
                "{} {:<11} {:<9} cw={} cites={} {:>7} tok ${:.2} {} turns {:?}",
                row.id,
                row.arm,
                row.verdict,
                row.confidently_wrong,
                row.cites_gold,
                row.tokens,
                row.cost_usd,
                row.turns,
                row.tools
            )?;
            eprintln!("{}", log.lines().last().unwrap_or_default());
            let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&results)?;
            writeln!(f, "{}", serde_json::to_string(&row)?)?;
        }
    }
    Ok(log)
}

fn one(
    q: &Question,
    arm: &str,
    plan: &Plan<'_>,
    projects: &Path,
    add_dirs: &[PathBuf],
    cwd: &Path,
    memory: Option<&Path>,
) -> Result<Row> {
    let args = answer_args(arm, plan, add_dirs, plan.out, memory)?;
    let mut text = prompt(q, projects);
    if plan.record {
        text.push_str(
            "\n\nWhen your answer passes the check, it should be kept: pass record: true on that answer call.",
        );
    }
    let (out, failed) = match session(cwd, &text, &args, TIMEOUT) {
        Ok(v) => (v, None),
        Err(e) => (Value::Null, Some(format!("{e:#}"))),
    };
    let answer = failed
        .clone()
        .unwrap_or_else(|| out["result"].as_str().unwrap_or_default().to_owned());
    let (tokens, output_tokens) = tokens(&out["usage"]);
    let (g, grade_cost) = match grade(cwd, q, &answer, plan.grader_model) {
        Ok(x) => x,
        Err(e) => (
            json!({"verdict": "ungraded", "confidently_wrong": false, "reason": format!("{e:#}")}),
            0.0,
        ),
    };
    // Exposure: which calls read a gold file or returned text naming one. Calls are kept with their input and the
    // size of what they returned, not the text itself (it can be large and private).
    let names: Vec<&str> = q
        .gold
        .iter()
        .map(|g| g.file.rsplit('/').next().unwrap_or(&g.file))
        .collect();
    let calls: Vec<Value> = out["_calls"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|c| {
            let text = c["result"].as_str().unwrap_or_default();
            let input = c["input"].to_string();
            json!({"name": c["name"], "input": input.chars().take(if c["is_error"] == true { 2000 } else { 300 }).collect::<String>(),
                   "result_chars": text.chars().count(), "is_error": c["is_error"],
                   // A refusal's text is short and is the thing to study; results are otherwise not kept.
                   "refusal": if c["is_error"] == true { json!(text.chars().take(600).collect::<String>()) } else { Value::Null },
                   "gold": !names.is_empty() && names.iter().any(|n| text.contains(n) || input.contains(n))})
        })
        .collect();
    let first_gold_call = calls.iter().position(|c| c["gold"] == true);
    let saw_fact = out["_calls"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|c| c["result"].as_str().is_some_and(|t| t.contains("RECORDED FACT")));
    Ok(Row {
        id: q.id.clone(),
        arm: arm.to_owned(),
        category: q.category.clone(),
        project: q.project.clone(),
        saw_gold: first_gold_call.is_some(),
        saw_fact,
        first_gold_call,
        calls,
        cites_gold: cites_gold(q, &answer),
        tools: serde_json::from_value(out["_tools"].clone()).unwrap_or_default(),
        denials: out["permission_denials"].as_array().map_or(0, |a| a.len() as u64),
        answer,
        is_error: failed.is_some() || out["is_error"].as_bool().unwrap_or(false),
        cost_usd: out["total_cost_usd"].as_f64().unwrap_or(0.0),
        turns: out["num_turns"].as_u64().unwrap_or(0),
        duration_ms: out["duration_ms"].as_u64().unwrap_or(0),
        tokens,
        output_tokens,
        verdict: g["verdict"].as_str().unwrap_or("ungraded").to_owned(),
        confidently_wrong: g["confidently_wrong"].as_bool().unwrap_or(false),
        grade_reason: g["reason"].as_str().unwrap_or_default().to_owned(),
        grade_cost_usd: grade_cost,
        at: now(),
    })
}

fn now() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    format!("unix:{secs}")
}

/// Wilson 95% interval for k of n, as (low, high).
pub fn wilson(k: usize, n: usize) -> (f64, f64) {
    if n == 0 {
        return (0.0, 1.0);
    }
    let (z, n_f, p) = (1.959_964, n as f64, k as f64 / n as f64);
    let denom = 1.0 + z * z / n_f;
    let centre = (p + z * z / (2.0 * n_f)) / denom;
    let half = z * ((p * (1.0 - p) + z * z / (4.0 * n_f)) / n_f).sqrt() / denom;
    ((centre - half).max(0.0), (centre + half).min(1.0))
}

/// Per arm: verdicts, confidently wrong, traps caught, gold cited, tokens and cost, each rate with its
/// Wilson interval; then the paired per-question comparison.
pub fn report(out: &Path) -> Result<String> {
    let rows: Vec<Row> = std::fs::read_to_string(out.join("results.jsonl"))
        .context("no results.jsonl in the run folder")?
        .lines()
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect();
    let mut by_arm: BTreeMap<&str, Vec<&Row>> = BTreeMap::new();
    for r in &rows {
        by_arm.entry(r.arm.as_str()).or_default().push(r);
    }
    let rate = |k: usize, n: usize| {
        let (lo, hi) = wilson(k, n);
        format!(
            "{k}/{n} ({:.0}%, 95% CI {:.0}-{:.0}%)",
            100.0 * k as f64 / n.max(1) as f64,
            lo * 100.0,
            hi * 100.0
        )
    };
    let mut s = String::new();
    for (arm, rs) in &by_arm {
        let n = rs.len();
        let count = |f: &dyn Fn(&Row) -> bool| rs.iter().filter(|r| f(r)).count();
        let traps: Vec<&&Row> = rs.iter().filter(|r| r.category == "trap").collect();
        let mut toks: Vec<u64> = rs.iter().map(|r| r.tokens).collect();
        toks.sort_unstable();
        let correct = count(&|r| r.verdict == "correct");
        let cost: f64 = rs.iter().map(|r| r.cost_usd).sum();
        writeln!(s, "## {arm} (n = {n})")?;
        writeln!(s, "- confidently wrong: {}", rate(count(&|r| r.confidently_wrong), n))?;
        writeln!(s, "- correct: {}", rate(correct, n))?;
        writeln!(
            s,
            "- correct or partial: {}",
            rate(count(&|r| r.verdict == "correct" || r.verdict == "partial"), n)
        )?;
        writeln!(s, "- abstained: {}", rate(count(&|r| r.verdict == "abstained"), n))?;
        writeln!(
            s,
            "- traps caught: {}",
            rate(traps.iter().filter(|r| r.verdict == "correct").count(), traps.len())
        )?;
        writeln!(s, "- cites a gold file and line: {}", rate(count(&|r| r.cites_gold), n))?;
        writeln!(
            s,
            "- errors or ungraded: {}",
            count(&|r| r.is_error || r.verdict == "ungraded")
        )?;
        writeln!(
            s,
            "- a tool returned a gold file (non-trap): {}; saw it but answered wrong or partial: {}; \
             calls before first sight, median {}",
            rate(
                count(&|r| r.category != "trap" && r.saw_gold),
                count(&|r| r.category != "trap")
            ),
            count(&|r| r.saw_gold && r.verdict != "correct"),
            {
                let mut f: Vec<usize> = rs.iter().filter_map(|r| r.first_gold_call).collect();
                f.sort_unstable();
                f.get(f.len() / 2).map_or("-".to_owned(), |x| x.to_string())
            }
        )?;
        writeln!(
            s,
            "- a recorded fact was among what it read: {}",
            rate(count(&|r| r.saw_fact), n)
        )?;
        let mut refusals: BTreeMap<String, usize> = BTreeMap::new();
        for r in rs {
            for c in r
                .calls
                .iter()
                .filter(|c| c["name"] == "mcp__groundworks__answer" && c["is_error"] == true)
            {
                // The first rule named in the refusal, after the header line.
                let why = c["refusal"]
                    .as_str()
                    .unwrap_or("")
                    .lines()
                    .nth(1)
                    .unwrap_or("(no text)")
                    .trim_start_matches("- ")
                    .trim();
                let key: String = why
                    .split(':')
                    .next()
                    .unwrap_or(why)
                    .split(" in ")
                    .next()
                    .unwrap_or(why)
                    .chars()
                    .take(60)
                    .collect();
                *refusals.entry(key).or_default() += 1;
            }
        }
        if !refusals.is_empty() {
            let mut v: Vec<_> = refusals.into_iter().collect();
            v.sort_by(|a, b| b.1.cmp(&a.1));
            writeln!(
                s,
                "- answer refusals by first rule named: {}",
                v.iter()
                    .map(|(k, n)| format!("{n}x {k:?}"))
                    .collect::<Vec<_>>()
                    .join("; ")
            )?;
        }
        writeln!(
            s,
            "- used a Groundworks tool: {}; tool calls {}; permission denials {}",
            rate(
                count(&|r| r.tools.keys().any(|t| t.starts_with("mcp__groundworks__"))),
                n
            ),
            rs.iter().flat_map(|r| r.tools.values()).sum::<u64>(),
            rs.iter().map(|r| r.denials).sum::<u64>()
        )?;
        writeln!(
            s,
            "- tokens per answer: median {}, total {}; cost ${cost:.2} (${:.2} per correct answer); turns median {}\n",
            toks.get(n / 2).copied().unwrap_or(0),
            toks.iter().sum::<u64>(),
            if correct > 0 { cost / correct as f64 } else { f64::NAN },
            {
                let mut t: Vec<u64> = rs.iter().map(|r| r.turns).collect();
                t.sort_unstable();
                t.get(n / 2).copied().unwrap_or(0)
            }
        )?;
    }
    let Some(a) = by_arm.get("plain") else {
        return Ok(s);
    };
    let pa: BTreeMap<&str, &Row> = a.iter().map(|r| (r.id.as_str(), *r)).collect();
    for (arm, b) in by_arm.iter().filter(|(arm, _)| **arm != "plain") {
        let (mut better, mut worse, mut same, mut fewer_tokens, mut pairs) = (0, 0, 0, 0, 0);
        let score = |r: &Row| match r.verdict.as_str() {
            "correct" => 3,
            "partial" => 2,
            "abstained" => 1,
            _ => 0,
        };
        for r in b {
            if let Some(p) = pa.get(r.id.as_str()) {
                pairs += 1;
                match score(r).cmp(&score(p)) {
                    std::cmp::Ordering::Greater => better += 1,
                    std::cmp::Ordering::Less => worse += 1,
                    std::cmp::Ordering::Equal => same += 1,
                }
                fewer_tokens += usize::from(r.tokens < p.tokens);
            }
        }
        writeln!(
            s,
            "## {arm} against plain, by question ({pairs} pairs)\n- {arm} better {better}, worse {worse}, same {same}\n\
             - {arm} used fewer tokens on {fewer_tokens} of {pairs}\n"
        )?;
    }
    Ok(s)
}

/// The default run folder for a set: `<set dir>/runs/<unix seconds>`.
pub fn default_out(set: &Path) -> PathBuf {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    set.parent()
        .unwrap_or(Path::new("."))
        .join("runs")
        .join(secs.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn q() -> Question {
        serde_json::from_value(json!({"id": "x", "project": "p", "category": "lesson", "question": "q",
            "gold_answer": "g", "must_mention": ["m"], "gold": [{"file": "docs/NETCODE-PLAN.md", "line": 120, "quote": "q"}]}))
        .unwrap()
    }

    #[test]
    fn citing_the_gold_file_near_its_line_counts_and_elsewhere_does_not() {
        assert!(cites_gold(&q(), "see supergun/docs/NETCODE-PLAN.md:118 for it"));
        assert!(cites_gold(&q(), "NETCODE-PLAN.md#L125"));
        assert!(!cites_gold(&q(), "NETCODE-PLAN.md:400"));
        assert!(!cites_gold(&q(), "NETCODE-PLAN.md without a line"));
        assert!(!cites_gold(&q(), "OTHER.md:120"));
    }

    #[test]
    fn a_session_gets_only_the_projects_and_never_the_home() {
        let plan = Plan {
            set: Path::new("set.jsonl"),
            out: Path::new("out"),
            only: &[],
            arms: &[],
            model: "opus",
            grader_model: "opus",
            budget_usd: 1.0,
            effort: None,
            record: false,
        };
        let dirs = [PathBuf::from("C:/w/alpha"), PathBuf::from("C:/w/beta")];
        let args = answer_args("plain", &plan, &dirs, Path::new("out"), None).unwrap();
        let added: Vec<&String> = args
            .iter()
            .zip(args.iter().skip(1))
            .filter(|(f, _)| *f == "--add-dir")
            .map(|(_, d)| d)
            .collect();
        assert_eq!(added, ["C:/w/alpha", "C:/w/beta"], "each project, never the workspace");
        let settings = &args[args.iter().position(|a| a == "--settings").unwrap() + 1];
        let v: serde_json::Value = serde_json::from_str(settings).unwrap();
        let deny = v["permissions"]["deny"].as_array().unwrap();
        assert!(deny.iter().any(|d| d == "Read(**/.groundworks/secrets/**)"));
        assert!(deny.iter().any(|d| d == "Bash(git log --output:*)"));
        assert_eq!(v["enabledPlugins"]["groundworks@groundworks"], false);
    }

    #[test]
    fn wilson_matches_the_textbook() {
        let (lo, hi) = wilson(5, 10);
        assert!((lo - 0.2366).abs() < 1e-3 && (hi - 0.7634).abs() < 1e-3, "{lo} {hi}");
        assert_eq!(wilson(0, 0), (0.0, 1.0));
    }

    #[test]
    fn the_style_and_the_agent_say_the_same_thing() {
        assert!(style_body().starts_with("You are the Groundworks agent"));
        assert_eq!(
            style_body(),
            body(AGENT),
            "plugin/output-styles and plugin/agents must carry one text"
        );
    }
}
