# 2026-09-15: per-agent credentials (V2-3)

Goal: the datastore, not the MCP server, enforces who may write what. Before this every launch used root.

## What was measured on SurrealDB 3.1.6 (throwaway in-memory server, port 8032)

CONFIRMED:
- `DEFINE ACCESS agent ON DATABASE TYPE RECORD SIGNIN (SELECT * FROM agent WHERE id = type::record('agent', $id) AND enabled = true AND crypto::argon2::compare(pass, $pass))` issues a JWT from `POST /signin` with `{ns, db, ac, id, pass}`; a wrong password is HTTP 404 "No record was returned".
- `ALTER TABLE t PERMISSIONS FOR select ... FOR create ... FOR update ... FOR delete NONE` works on tables the schema generator already defined, on `TYPE RELATION` tables too, and leaves fields and data alone.
- `$auth.role` and `lens = $auth.lens` resolve inside PERMISSIONS clauses (a seat created a critique under its own lens and was refused under another). Oddly `RETURN $auth.role` at top level is null while `RETURN $auth` is the record id; the permission clauses still resolve it.
- Field ASSERTs (the gates) fire for a record user exactly as for root: a reader confirming without a citation got the ASSERT text back. Permissions and gates compose.
- A denied CREATE/UPDATE/RELATE/DELETE returns `[]` with status OK. Only DEFINE is a loud IAM error. `Db` therefore raises `PermissionDenied` when a write under record auth returns `[]`; `try_sql` raises it too so a promotion helper never explains a permission denial as missing evidence.
- Revocation = root deletes the `agent` row. A token issued before that still authenticates but every SELECT and write then returns `[]`; a new signin is refused.
- Root's gate probes are 2 rejected / 2 accepted / 0 misbehaved after access is applied.
- `type::thing` no longer exists on 3.x (`type::record`).

NOT USED (and why):
- `DEFINE USER ... ON DATABASE ROLES ...` is a system user: `$auth` is not a record and table PERMISSIONS do not apply, so it cannot carry row rules.
- `TYPE BEARER FOR RECORD` grants: not needed for the rules asked for and UNKNOWN whether they need an experimental flag on 3.1.6; record signin with a per-agent password covers provision, launch env and revoke.

## Design
- `access:` in `instance.yaml`: roles with `read`, `write`, `lens_bound`. `config.py` refuses unknown tables and any role but `loader` writing `label`.
- An edge is readable when both its ends are readable; `authority` and `relies_on` end up NONE for every demo role, which is correct (no demo role reads authority).
- `DELETE` is NONE everywhere: deletes go through the archive by a human as root. A loader running under its own credential therefore cannot sweep stale `bears_on` edges; the loader stays a root job for now (open).
- Token duration 8h; `Db` re-signs in once on HTTP 401.

## Demo
`run_instance.py` applies access after the gates (root), provisions `reader_1`, `seat_skeptic` (lens_skeptic) and `coordinator_1`, and runs the scenario with each step under its identity, printing who did it. The scenario contains deliberate cross-role attempts (reader critiques, seat confirms, seat borrows a lens, coordinator deletes) and fails the run if any is accepted.

## Open
- The loader under its own credential cannot delete (sweep); either the sweep becomes an archive move or the loader keeps root.
- Per-viewer read scopes (v0.3) would reuse the same `$auth` mechanism with `FOR select WHERE` row filters.
