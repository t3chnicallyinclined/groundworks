# -*- coding: utf-8 -*-
"""session_start.py -- SessionStart hook: walk up from cwd to the nearest `.xsp/`, emit additionalContext
naming the resolved root + namespace (or "no .xsp found"). Stub: no real graph attach, no writes.

Reads the SessionStart JSON payload on stdin (fields include "cwd" per the Claude Code hooks
reference); falls back to os.getcwd() if stdin is empty, not JSON, or has no "cwd". Always exits 0
-- a hook that could error out of a session start is worse than one that reports "no .xsp found".

Output shape (Claude Code hook JSON output convention, matching xscholar/hooks/lookup.py's
PreToolUse usage):
    {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<text>"}}
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "plugin"))
from resolve import resolve  # noqa: E402


def _cwd_from_stdin(stdin_text):
    try:
        payload = json.loads(stdin_text) if stdin_text else {}
    except ValueError:
        payload = {}
    if isinstance(payload, dict) and payload.get("cwd"):
        return str(payload["cwd"])
    return os.getcwd()


def context_for(stdin_text):
    start = _cwd_from_stdin(stdin_text)
    result = resolve(start)
    if result["found"]:
        return "xsp: resolved root %s, namespace %s" % (result["root"], result["namespace"])
    return "xsp: %s" % result["message"]


def main():
    stdin_text = sys.stdin.read() if not sys.stdin.isatty() else ""
    context = context_for(stdin_text)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
