# XScholar MCP server (slice 1)

**What it is.** A Model Context Protocol server (stdio) that lets AI agents use an XScholar graph through typed tools: read evidence units by locator, search them, explain what blocks a conclusion, check eliminated claims, add critiques and answers, and run panel rounds. The datastore gates still decide every promotion; the server only carries requests to the instance's write surface and reports what came back.

**One core, three front doors.** The typed functions are the core: the instance adapter (the write surface and audits the instance already has) and engine modules (`panel_io.py` over `xscholar/panel/panel.py`). This server is one front door; hooks and command lines are the others. `server.py` holds no business logic: launch identity, adapter loading, argument shape checks, and the status envelope.

**A new instance only writes an adapter.** One Python module with `create_adapter()` returning an object that implements `adapter.py` (`InstanceAdapter`). It maps the engine's words (unit, locator, claim, conclusion, critique, answer, lens, eliminated claim) onto its own tables and helpers. Nothing in `xscholar/` changes. `test_server.py` runs the same tool tests against two non-legal synthetic instances (an incident postmortem and a literature review) to keep the server free of domain assumptions.

## Tools

Every result is an envelope: `{"status", "tool", "data"}` on success, or `{"status", "tool", "error", "missing"}` otherwise.

| Status | Meaning |
|---|---|
| `ok` | The call ran. An empty list under `data` is a real answer |
| `not_found` | The locator, unit, claim, conclusion or critique does not exist |
| `db_unreachable` | The datastore did not answer. Never reported as an empty result |
| `rejected_by_gate` | A write surface rule, a datastore gate or the panel protocol refused. `error` is verbatim; `missing` names what to supply |
| `invalid_argument` | The request is malformed (bad id shape, limit out of range, empty text) |
| `error` | Anything unexpected, with `error_type`; a traceback goes to stderr |

| Tool | Kind | What it does | Typical statuses |
|---|---|---|---|
| `unit_get(locator, view="data")` | read | Every unit matching a unit id or a citation in the instance's locator scheme: exact text, usable or not, grade, labels, claims citing it, eliminated claims about it | ok, not_found, invalid_argument, db_unreachable |
| `unit_search(query, usable_only=true, limit=20, view="data")` | read | Keyword search over located units (plain words, not a query language) | ok (possibly empty), invalid_argument, db_unreachable |
| `claim_explain(claim_id, view="data")` | read | Status, support with grades, adverse material, critiques, and in words everything that blocks readiness | ok, not_found, db_unreachable |
| `eliminated_claims(about=None, view="data")` | read | Claims investigated and found false, shown as `ELIMINATED CLAIM (false)`, and failed approaches | ok (possibly empty), db_unreachable |
| `health(view="data")` | read | The instance audit, plus the server's identity and tool list | ok, db_unreachable |
| `critique_add(target, head, verdict, lens=None, text=None)` | write | One critique against a conclusion; returns what still blocks it | ok, not_found, rejected_by_gate, invalid_argument |
| `critique_answer(critique, answer)` | write | The answer to a critique; returns what still blocks its target | ok, not_found, invalid_argument |
| `panel_submit(run_dir, round, seat, text)` | write | Saves a seat's answer verbatim to `round1/` or `round2/<seat>.md`; logs `submissions.jsonl` | ok, rejected_by_gate, not_found, invalid_argument |
| `panel_status(run_dir)` | read | Phase and every re-prompt owed (from `panel.evaluate`), with the prompt text | ok, rejected_by_gate, not_found |

`panel_status` after anonymization calls `panel.evaluate`, which may write `counterfactual.json` (the panel's agreement check). Nothing else in the read tools writes.

## Views (reading layer)

The five read tools take `view`: `data` (default) or one of the reading-layer modes `full`, `speedread`, `claims`, `uncertain_first` (`xscholar/present/README.md`).

- The view name is checked before the core runs; an unknown name is `invalid_argument` with `missing: ["view"]`.
- For a mode, the server passes a copy of the tool's result to the adapter's optional `present_rows(tool_name, result)`, which returns rows in the reading layer's row contract, and uses the optional `present_policy()` (or the generic policy). The envelope gains `view`, `rendered` (Markdown) and `rendered_notice`; `data` is unchanged.
- If the adapter supplies no rows for that tool (the default for both optional methods), the call is `invalid_argument` naming the view, with no `data`.
- A failed core call keeps its own status under any view. Malformed rows or policy from the adapter are `error` (`PresentationContractError`), never a partial rendering.
- Write tools and panel tools take no view: a write is never followed by a presentation failure.

## Launch configuration

| Variable | Required | Meaning |
|---|---|---|
| `XSCHOLAR_ADAPTER` | yes | Dotted module path, or a path to a `.py` file, defining `create_adapter()` |
| `XSCHOLAR_AGENT_ID` | yes | Stamped on every write. For role `seat`, the seat id |
| `XSCHOLAR_ROLE` | yes | For example `coordinator`, `critic`, `seat` |
| `XSCHOLAR_LENS` | no | Critiques default to this lens; a different lens is refused |
| `XSCHOLAR_PANEL_ROOT` | no | Panel run directories must be inside it |

Instance adapters may read their own variables (datastore URL, credentials). Missing or malformed identity stops the server before it serves (exit 2, `NOT STARTED`). Check a configuration without serving: `python xscholar/mcp/server.py --list-tools`.

Example `.mcp.json` entry (paths are placeholders):

```json
{
  "mcpServers": {
    "xscholar": {
      "command": "python",
      "args": ["/path/to/xscholar/xscholar/mcp/server.py"],
      "env": {
        "XSCHOLAR_ADAPTER": "/path/to/instance/my_adapter.py",
        "XSCHOLAR_AGENT_ID": "lead_session",
        "XSCHOLAR_ROLE": "coordinator",
        "XSCHOLAR_PANEL_ROOT": "/path/to/instance/panels",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

A client shares one server process across the agents it runs, so every write from that client carries the same identity. A seat or a critic with its own identity needs its own launch entry (slice 2 replaces this with per-agent credentials).

## Design rules

1. **No raw query tool, no delete tool, no tool that edits gates, thresholds, schema or tables.** Required by the method (gates are human-owned) and by SurrealDB's Business Source License boundary (`docs/LICENSING.md` section 1). `test_server.py` introspects the registered tools and fails on any such name or argument.
2. **Provenance comes from launch configuration.** No tool accepts `by`, `author`, `agent_id` or `role`; extra arguments are ignored and the stored author is the launch identity (tested).
3. **Source text is marked untrusted.** It appears only under `untrusted_source_text`, with a notice that it is data, not instructions. The server refuses an adapter result that carries a bare `text` key.
4. **Explicit statuses.** Empty results, missing items, gate refusals and an unreachable datastore are different statuses. A core that returns `None` or a non-dict is reported as `error`, never `ok`.
5. **Errors are loud.** Unexpected exceptions become `error` with their type; nothing is swallowed into an empty success.
6. **Panel answers are never edited.** `panel_submit` writes the exact UTF-8 bytes received, refuses round 1 once `anon_map.json` exists, refuses round 2 before it, refuses an unlocked or changed panel, and refuses a seat that is not the launch identity when `XSCHOLAR_ROLE=seat`.

## Tests

```
python -m unittest discover -s xscholar/mcp -v
```

No datastore needed: fake adapters stand in. Adapter tests for an instance live next to that instance's adapter and run against a throwaway datastore.

## Slice 2 (planned)

- **Vector search** over units alongside keyword search, with the match method reported per result.
- **Eliminated-claim similarity:** warn when a new claim or critique is close to a claim already eliminated.
- **Per-agent datastore credentials and permissions:** bearer tokens per agent, record-level permissions so a seat or critic can write only its own overlay rows, and identity taken from the credential instead of launch variables.
- **Change feeds:** notify agents when a unit, claim or conclusion they read changes status (for example a claim demoted to stale).

Files: `server.py` (front door), `adapter.py` (contract, including the optional presentation methods), `panel_io.py` (panel submit and status), `test_server.py`. Views render through `xscholar/present` (standard library, loaded by path). Dependency: the `mcp` Python SDK (MIT).

## Generic launch: any instance, no hand-written adapter

An instance that runs on the engine's generated schema needs no adapter code. Point the server at the
generic adapter and the instance config:

```json
{"mcpServers": {"xscholar": {
  "command": "python",
  "args": ["-m", "xscholar.mcp.server"],
  "env": {"XSCHOLAR_ADAPTER": "xscholar.mcp.instance_adapter",
          "XSCHOLAR_INSTANCE": "/path/to/instance.yaml",
          "XSCHOLAR_DB_URL": "http://127.0.0.1:8002/sql",
          "XSCHOLAR_NS": "my_instance", "XSCHOLAR_DB": "my_instance",
          "XSCHOLAR_AGENT_ID": "reader_1", "XSCHOLAR_ROLE": "reader"}}}}
```

Tools then available: `unit_get`, `unit_search`, `claim_explain`, `eliminated_claims`, `health`,
`claim_propose`, `claim_confirm`, `claim_rule_out`, `claim_support`, `conclusion_mark_ready`,
`critique_add`, `critique_answer`, `panel_submit`, `panel_status`. Every write is stamped with the
launch identity; every gate refusal comes back as `rejected_by_gate` with `missing` naming what the
datastore wants. A hand-written adapter (for an instance with its own schema) implements the read and
critique methods and may leave the lifecycle methods unimplemented; the server then reports
`invalid_argument` for them, never an empty success.
