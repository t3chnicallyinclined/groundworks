//! The integration checks of A3.1 step 7, in the selftest's throwaway home and store: `pulse --json` prints
//! exactly the line it appends to `metrics/pulse.jsonl` (the schema-2 object of docs/PULSE.md), and
//! `store status --json` reads every registered namespace's `schema_version:current` through the agent login
//! without migrating anything: a registered project whose namespace was never opened reads as unopened, and
//! still does after a second run.

use std::path::Path;
use std::process::{Command, Stdio};

use anyhow::{Context, Result};
use serde_json::Value;

use super::Checks;

fn cli(exe: &Path, home: &Path, args: &[&str]) -> Result<(bool, String)> {
    let out = Command::new(exe)
        .args(args)
        .env("GROUNDWORKS_HOME", home)
        .env("GROUNDWORKS_MACHINE", "SELFTEST")
        .env_remove("GROUNDWORKS_START")
        .stdin(Stdio::null())
        .output()?;
    Ok((out.status.success(), String::from_utf8_lossy(&out.stdout).into_owned()))
}

pub(super) fn status_checks(exe: &Path, base: &Path, home: &Path, c: &mut Checks) -> Result<()> {
    let (ok, out) = cli(exe, home, &["pulse", "--json"])?;
    let recorded = std::fs::read_to_string(home.join("metrics/pulse.jsonl")).unwrap_or_default();
    let last = recorded
        .lines()
        .rev()
        .find(|l| !l.trim().is_empty())
        .unwrap_or_default();
    let v: Value = serde_json::from_str(out.trim()).unwrap_or(Value::Null);
    c.check(
        "pulse --json prints exactly the line it appended: schema 2, read and cost present (null when empty), red a list",
        ok && out.trim_end() == last
            && v["schema"] == 2
            && v.get("read").is_some_and(Value::is_null)
            && v.get("cost").is_some()
            && v["red"].is_array(),
        &out,
    );

    // A registered project whose namespace nothing has opened: status must not create or migrate it.
    std::fs::create_dir_all(base.join("work/unopened"))?;
    let toml = home.join("registry/projects/unopened.toml");
    std::fs::write(
        &toml,
        "name = \"unopened\"\ndir = \"unopened\"\nsummary = \"never opened\"\n",
    )?;
    let (ok1, first) = cli(exe, home, &["store", "status", "--json"])?;
    let (ok2, second) = cli(exe, home, &["store", "status", "--json"])?;
    std::fs::remove_file(&toml)?;
    let parse = |text: &str| serde_json::from_str::<Value>(text).unwrap_or(Value::Null);
    let (a, b) = (parse(&first), parse(&second));
    let ns = |v: &Value, name: &str| -> Value {
        v["namespaces"]
            .as_array()
            .and_then(|all| all.iter().find(|n| n["ns"] == name).cloned())
            .unwrap_or(Value::Null)
    };
    let compiled = |scope: &str| a["compiled"][scope].as_i64().context("compiled number");
    let global = ns(&a, "global");
    let demo = ns(&a, "proj_demo");
    c.check(
        "store status --json reads global and each opened project at this build's number, through the agent login",
        ok1 && global["current"] == true
            && global["schema_version"]["applied"].as_i64() == Some(compiled("global")?)
            && global["schema_version"]["product"] == gw_store::PRODUCT
            && demo["current"] == true
            && demo["schema_version"]["applied"].as_i64() == Some(compiled("project")?),
        &first,
    );
    let unopened = (ns(&a, "proj_unopened"), ns(&b, "proj_unopened"));
    c.check(
        "store status --json never migrates: an unopened namespace reads as not current, twice, with why",
        ok2 && unopened.0["current"] == false
            && unopened.0["schema_version"].is_null()
            && unopened.0["error"].is_string()
            && unopened.1["schema_version"].is_null()
            && unopened.1["current"] == false,
        &second,
    );
    Ok(())
}
