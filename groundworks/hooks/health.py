# -*- coding: utf-8 -*-
"""health.py -- session-start hook: silence must never look like "nothing known".

Prints one line the agent sees when a session starts. If the instance's database is down, that
line is a WARNING, so a later empty query is read as "the graph is off", not "the graph is empty".

Configured by the same environment as the MCP server (GROUNDWORKS_INSTANCE, GROUNDWORKS_DB_URL, ...).
Claude Code settings example:
    {"hooks": {"SessionStart": [{"hooks": [{"type": "command",
      "command": "python -m groundworks.hooks.health"}]}]}}
Always exits 0: a hook must never block the session.
"""
import json
import os
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks import config  # noqa: E402
from groundworks import db as dbmod  # noqa: E402
from groundworks.mcp import instance_adapter  # noqa: E402


def check(env=None):
    """Return (ok, message). Never raises."""
    env = os.environ if env is None else env
    path = env.get("GROUNDWORKS_INSTANCE")
    if not path:
        return False, "WARNING groundworks: GROUNDWORKS_INSTANCE is not set; no instance graph is attached to this session"
    try:
        inst = config.load(path)
    except config.ConfigError as exc:
        return False, "WARNING groundworks: instance config unusable: %s" % str(exc).splitlines()[0]
    db = dbmod.Db(url=env.get("GROUNDWORKS_DB_URL", "http://127.0.0.1:8002/sql"), ns=env.get("GROUNDWORKS_NS") or inst.id,
                  db=env.get("GROUNDWORKS_DB") or inst.id, user=env.get("GROUNDWORKS_DB_USER", "root"),
                  password=env.get("GROUNDWORKS_DB_PASS", "root"), timeout=float(env.get("GROUNDWORKS_HOOK_TIMEOUT", "3")),
                  access=env.get("GROUNDWORKS_DB_ACCESS"))
    try:
        health = instance_adapter.GenericAdapter(inst, db).health()
    except Exception as exc:  # any failure is reported, never swallowed into silence
        return False, ("WARNING groundworks: the graph for instance %s is NOT reachable at %s (%s). An empty answer "
                       "from now on means the database is off, not that nothing is known." % (inst.id, db.url, str(exc)[:120]))
    drift = health.get("DRIFT_confirmed_without_valid_cite", 0)
    facts = health.get("facts_by_status", {})
    line = ("groundworks %s: %d units, %d labels, facts %s, %d/%d conclusions ready, %d critiques unanswered, drift %d"
            % (inst.id, health.get("units", 0), health.get("labels", 0), json.dumps(facts, sort_keys=True),
               health.get("conclusions_ready", 0), health.get("conclusions", 0),
               health.get("critiques_unanswered", 0), drift))
    if drift:
        line = "WARNING " + line + " (confirmed facts whose evidence no longer satisfies the gate; re-check them)"
    return True, line


def main():
    ok, message = check()
    payload = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": message}}
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
