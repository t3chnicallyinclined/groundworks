# Product data model (SurrealDB 3.1.x)

Placeholder product name `xsp`: it lives only in `xscholar/product/model.py` (`PRODUCT`, used as the database
name). Renaming is one edit there.

Files: `xscholar/product/schema/NNNN_*.surql` (migrations), `xscholar/product/model.py` (apply, migrate,
version checks, member credentials; standard library only), `tests/product/test_model.py` (the gate).

## Layout

One SurrealDB per workspace. Every namespace uses database `xsp`.

| Namespace | Holds | Migrations |
|---|---|---|
| `workspace` | registry + cross-project memory | files with `-- scope: workspace` |
| `proj_<slug>` | one project's knowledge and work | files with `-- scope: project` |

`slug` is `[a-z0-9_]{1,40}`; `project.namespace` is computed as `'proj_' + slug`.

### workspace tables (0001_workspace_registry.surql)

| Table | Purpose | Who writes |
|---|---|---|
| `project` | slug, name, root path, computed namespace | lead |
| `repo`, `worktree`, `branch` | where code is, seen-at commit | lead |
| `service` | running things (host, kind) | lead |
| `gate_def` | a project's gate command, `confirmed` | lead |
| `area` | paths/symbols an area owns | lead |
| `expert` | expert name, areas, file | lead |
| `decision` | title, why, when | lead |
| `dead_end` | cross-project dead end; `proposed` by any role, accepted/rejected by lead | all create, lead updates |
| `cost_row` | model, tokens, usd, optional project/ticket | all create, nobody updates |

### proj_<slug> tables (0002_project_base.surql, rules in 0003_project_rules.surql)

| Table | Purpose | Who writes |
|---|---|---|
| `unit` | citable span: `locator` (unique) + content `hash` | lead, worker |
| `fact` | claim: `status` live/stale/gone, `grade` CONFIRMED/INFERRED/UNKNOWN | all roles |
| `evidence` | relation fact -> unit, ENFORCED (both ends must exist), `hash_at_cite` | all create, nobody updates |
| `dead_end` | project-local dead end, same rule as workspace | all create, lead updates |
| `ticket` | ticket_id (unique), status, tier, `last_worker_commit_at` | lead creates, all update |
| `gate_run` | ticket, command, verdict, run_role, ran_at, ran_by | worker, reviewer create; nobody updates |
| `review` | ticket, verdict approved/changes, counts, reviewer | reviewer creates; nobody updates |

Both kinds also hold `member` (credentials) and `schema_version` (migration state).
DELETE is `NONE` for every role on every table. Root bypasses PERMISSIONS (it is the migration and loader
identity) but not ASSERTs.

## Roles (DEFINE ACCESS member ... TYPE RECORD)

A member row has `role` lead|worker|reviewer and an argon2 `pass`. `model.provision(root, id, role, password)`
creates it; `model.connect(url, ns, member=id, password=...)` signs in. A credential exists in ONE namespace.

Measured on 3.1.6: with `PERMISSIONS NONE` on `member`, `$auth.role` reads NONE inside every field ASSERT, so a
role check there silently fails. `member` therefore allows `select WHERE id = $auth.id` (own row only, `pass`
field hidden) and nothing else; a member cannot change its own role.

## Rules and where they are enforced

| Rule | Enforced by | Tests (both arms) |
|---|---|---|
| R1 a unit's hash change marks every LIVE fact citing it `stale`, never deletes | EVENT `stale_on_hash_change` on `unit` (0003). Events run privileged: a worker with no update on `fact` still triggers it (measured) | `StalePropagation.*` |
| R2 `done` needs a gate_run for this ticket with verdict pass, run_role reviewer, `ran_at > last_worker_commit_at` | field ASSERT on `ticket.status` (0003); runs on CREATE/UPDATE/UPSERT/INSERT, so a later write that makes the evidence stale is refused while the row says done | `DoneGate.*` |
| R2 only lead/reviewer (or root) may write `done` | same ASSERT, `$auth.role IN ['lead','reviewer']` | `Roles.test_worker_cannot_set_done_*`, `test_lead_sets_done_with_evidence` |
| R2a `last_worker_commit_at` only moves forward | field ASSERT `$before = NONE OR $value >= $before` | `DoneGate.test_refuses_rewinding_*` |
| R2b gate_run evidence fields are READONLY; `ran_at <= time::now()` (work-scholar B1) | field READONLY + ASSERT (0003); table `update, delete NONE` for roles | `DoneGate.test_refuses_future_dated_*`, `test_a_gate_run_cannot_be_edited_*` |
| R3 `run_role` = the credential's role; `ran_by` = the credential (closes work-scholar M1) | field ASSERTs `$auth = NONE OR $value = $auth.role / $auth.id` | `Roles.test_worker_cannot_write_a_reviewer_gate_run_*`, `test_reviewer_credential_*` |
| Role write permissions (tickets, reviews, members, deletes) | table PERMISSIONS (0001, 0002) | `Roles.*`, `Namespaces.test_workspace_worker_*` |
| Namespace isolation | SurrealDB: a record-access token is bound to its ns/db ("You don't have permission to change to the proj_b namespace") | `Namespaces.test_proj_a_*` |
| Migrations: ordered, twice = no-op, failed file rolls back | `model.migrate`: files above `schema_version.applied`, each file + version bump in one transaction | `Migrations.*` |
| Wrong major refused | `model.migrate` (schema major in `schema_version` != `SCHEMA_MAJOR`), `model.check_server` (server major != 3) | `Migrations.test_wrong_*` |

Differences from work-scholar's rule: evidence is `gate_run.ticket` (a READONLY field), not a `judges` edge,
so a pass cannot be re-linked to another ticket; `judges` does not exist here.

## Migrations

- A file is `NNNN_name.surql`, numbers unique across scopes, first scope line required.
- Tables: `DEFINE TABLE IF NOT EXISTS` then `ALTER TABLE ... PERMISSIONS` (DEFINE TABLE OVERWRITE resets
  PERMISSIONS and drops CHANGEFEED on 3.1.6). Fields, events, indexes: `OVERWRITE`. Re-running every file by hand
  leaves `INFO FOR DB` identical (tested).
- `schema_version:current` = `{product, scope, major, applied, files, updated_at}`; members may read it, nobody
  but root writes it.
- A breaking change bumps `SCHEMA_MAJOR` and needs its own upgrade path; until one exists an older database is
  refused.

## Gate

    python -m pytest tests/product/test_model.py -q

Starts its own in-memory SurrealDB on an OS-assigned port (live ranges 8001-8003, 8023-8047, 8061 refused),
stops it by handle. `XSP_SCHEMA_DIR` points it at a copy of the schema for mutation checks.

## Not built

- Ingestion (scan, docs sections, code symbols, gate-run capture) and anything that computes a unit hash.
- The path from `stale` back to `live` (re-verification). Today any role that may update a fact can set it
  `live` again without new evidence.
- `gone` (a deleted unit): no rule sets it; member DELETE is refused, root DELETE removes the unit's edges.
- MCP tools, hooks, plugin, installer, `doctor`, backup.
- Walk-up folder discovery (`.xsp/` like git) and per-folder server lifecycle.
- Embedded SurrealDB mode from Python (UNKNOWN whether usable).
- Persistent storage: only tested against `memory`.
- Credential rotation/revocation helpers (`enabled=false` blocks sign-in; no test yet).
- Deleting a done ticket's evidence as root does not demote the ticket (same as work-scholar M3/F).
