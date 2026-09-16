# -*- coding: utf-8 -*-
"""lookup.py -- pre-tool-call hook: make the graph speak at the moment a mistake would be made.

Reads the tool call JSON on stdin. If the call names a unit id, a claim id, or words that match an
eliminated claim, it returns what the graph already knows: the unit's status and grade, its labels,
and any ELIMINATED CLAIM that the text is about to repeat. Advisory only; never blocks; always exit 0.
No output means the graph was consulted and holds nothing relevant; a hook that could not consult it
(no XSCHOLAR_INSTANCE, input that is not a JSON object, a database that does not answer) says so.

Claude Code settings example:
    {"hooks": {"PreToolUse": [{"matcher": "Bash|Edit|Write|Grep|Read", "hooks": [{"type": "command",
      "command": "python -m xscholar.hooks.lookup", "timeout": 10}]}]}}
Environment: the same as the MCP server (XSCHOLAR_INSTANCE, XSCHOLAR_DB_URL, ...).
"""
import json
import os
import re
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar.mcp import adapter as contract, instance_adapter  # noqa: E402

MAX_LINES = 14
MAX_CHARS = 2800
ID_RE = re.compile(r"\b(unit|fact|conclusion):([A-Za-z0-9_.-]+)")
WORD_RE = re.compile(r"[a-z][a-z0-9_]{3,}")


def _text_of(tool_input):
    if isinstance(tool_input, dict):
        return " ".join(_text_of(v) for v in tool_input.values())
    if isinstance(tool_input, list):
        return " ".join(_text_of(v) for v in tool_input)
    return str(tool_input) if tool_input is not None else ""


def lookup(text, adapter, unit_id_re=None):
    """Lines the agent should see before running a tool call that contains `text`."""
    lines = []
    seen = set()
    ids = ID_RE.findall(text)
    if unit_id_re:
        ids += [("unit", m) for m in re.findall(unit_id_re, text)]
    for table, ident in ids:
        key = (table, ident)
        if key in seen:
            continue
        seen.add(key)
        try:
            if table == "unit":
                u = adapter.unit_get(ident)
                tiers = ", ".join("tier %s %s" % (l.get("tier"), l.get("dir") or "") for l in u.get("labels", [])) or "no labels"
                lines.append("  unit %s: status %s (%s), grade %s (%s); %s; cited by %d fact(s)"
                             % (ident, u["status"], "usable" if u["usable"] else "NOT usable", u["grade"],
                                "confirming" if u["confirming"] else "cannot confirm", tiers, len(u.get("cited_by", []))))
            else:
                c = adapter.claim_explain("%s:%s" % (table, ident))
                if c["kind"] == "fact":
                    lines.append("  %s: %s [%s]%s" % (c["id"], c["statement"][:160], c["status"],
                                                      "; blocked: " + "; ".join(c["blocking_now"]) if c["blocking_now"] else ""))
                else:
                    lines.append("  %s: ready=%s%s" % (c["id"], c["ready"],
                                                        "; blocked: " + "; ".join(c["blocking_now"]) if c["blocking_now"] else ""))
        except contract.NotFound:
            continue
    # eliminated claims whose statement shares distinctive words with the text
    words = set(WORD_RE.findall(text.lower()))
    if words:
        for claim in adapter.eliminated_claims()["claims"]:
            claim_words = set(WORD_RE.findall((claim.get("statement") or "").lower()))
            overlap = words & claim_words
            if len(overlap) >= 3 or (claim_words and len(overlap) >= max(2, len(claim_words) // 2)):
                lines.append("  [%s] %s -- what the source shows: %s"
                             % (claim["label"], (claim.get("statement") or "")[:140], (claim.get("what_the_source_shows") or "")[:160]))
    if not lines:
        return ""
    head = "xscholar already records these (check before quoting or arguing from them):"
    out = [head] + lines[:MAX_LINES]
    text_out = "\n".join(out)
    return text_out[:MAX_CHARS]


NOT_RUN = "WARNING xscholar lookup hook did NOT consult the graph for this tool call: %s"


def context_for(stdin_text, env):
    """What the hook tells the agent. Empty ONLY when the graph was consulted and knows nothing relevant.

    A hook that could not look is never silent: before 2026-09-16, XSCHOLAR_INSTANCE unset or a payload
    that was not JSON returned 0 with no output at all -- exactly what "nothing relevant found" looks like,
    so the one message meant to arrive at the moment of error was switched off invisibly (health.py already
    warned in the same case; this hook did not)."""
    try:
        payload = json.loads(stdin_text)
    except ValueError as exc:
        return NOT_RUN % ("the hook input was not JSON (%s)" % str(exc)[:80])
    if not isinstance(payload, dict):
        return NOT_RUN % ("the hook input was JSON %s, not an object with tool_input" % type(payload).__name__)
    path = env.get("XSCHOLAR_INSTANCE")
    if not path:
        return NOT_RUN % "XSCHOLAR_INSTANCE is not set, so no instance graph is attached to this session"
    try:
        inst = config.load(path)
        db = dbmod.Db(url=env.get("XSCHOLAR_DB_URL", "http://127.0.0.1:8002/sql"), ns=env.get("XSCHOLAR_NS") or inst.id,
                      db=env.get("XSCHOLAR_DB") or inst.id, timeout=float(env.get("XSCHOLAR_HOOK_TIMEOUT", "2.5")))
        adapter = instance_adapter.GenericAdapter(inst, db)
        text = _text_of(payload.get("tool_input"))
        return lookup(text, adapter, env.get("XSCHOLAR_UNIT_ID_RE") or None)
    except Exception as exc:  # advisory: a broken hook must not block the session, but must not be silent
        return "xscholar lookup hook could not run (%s): the graph may be off" % str(exc)[:100]


def main():
    context = context_for(sys.stdin.read(), os.environ)
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
