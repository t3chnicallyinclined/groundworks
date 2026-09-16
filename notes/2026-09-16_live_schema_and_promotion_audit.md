# 2026-09-16: live-schema drift, principals, the promoter's own evidence, and the promotion log

The owner named two limits of independent promotion (V2-20):

1. Someone with full database access can get around the rule on purpose: make a second login, or remove the check.
2. "Independent" means a different login, not a different person. One process holding two logins can still do it.

**Neither can be fully prevented.** Whoever controls a database can change anything inside it. A database cannot know what is behind a credential. So this work makes tampering **detectable**, and makes faked independence **expensive and visible**. Every limit is listed at the end.

All measurements: SurrealDB 3.1.6, throwaway servers on ports 8070-8079 (memory; for the chain, also `rocksdb://` and `surrealkv://`), each stopped by PID.

## A. Live-schema drift (`xscholar/live_schema.py`)

### What the server does to a definition (measured)

`INFO FOR DB` and `INFO FOR TABLE` do not return the statement that was sent:
- `IN` comes back as `INSIDE`.
- `option<string>` comes back as `none | string`.
- `CHANGEFEED 7d` comes back as `1w`.
- Every field gains `PERMISSIONS FULL`.
- Sub-fields appear that nobody wrote (`written_by.*`, `status_history.*`).
- Parentheses move: `count(...)` becomes `count((...))`, and some are dropped.
- Trailing semicolons are removed.
- JWT keys come back `[REDACTED]`.
- A password hash comes back **in full** (`PASSHASH '$argon2id$...'`).
- Closures gain types: `|$c|` becomes `|$c: any|`.

A text comparison against the generated SurrealQL would flag every definition. Writing a normaliser would mean a second parser that drifts from the server's.

**Measured:** two databases given the same statements return byte-identical `INFO`, for every table and every definition. So the expected side is **rendered by the same server**:
1. Apply the generated statements to an empty scratch database (`zz_xscholar_reference_<random>`, in the live namespace).
2. Read its `INFO`.
3. Remove the scratch database in a `finally`, and check that it is gone.

The comparison is then exact, by `(kind, name)`:
- **missing**: in the config, not in the database.
- **altered**: present in both, different text.
- **unexpected**: in the database, not in the config.

`ignore` can suppress an unexpected definition only. It never hides a missing or altered one.

- A reference server on another host is accepted only if it reports the same version string.
- Password hashes are replaced at read time by a 16-hex-digit sha256 of the hash, so a changed password still shows as altered. The argon2 salt is not in the digest, so the digest cannot be used to test guesses.
- Namespace- and root-level users are listed, never judged.

**Components** are the generators a deployment applied, in apply order: `schema`, `gates`, `temporal`, `access`, `provenance`, `assertions`, `changefeed`, `lens`. The default is what `scripts/run_instance.py` plus the loader apply. A deployment that also runs the queue or lenses must name them, or their tables show as unexpected. That is deliberate.

### Proven both ways (tests/test_tamper_evidence.py)

An untouched deployment reads clean: about 180 definitions, expected count equal to live count, and no scratch database left behind. Each hand edit below is reported by name and nothing else is:

| Hand edit | Reported as |
|---|---|
| `REMOVE EVENT <rule>_independent` | missing event |
| `DEFINE FIELD` without its ASSERT | altered field |
| `REMOVE INDEX <t>_<f>_unique` | missing index |
| `ALTER TABLE ... PERMISSIONS FULL` | altered table |
| guard event removed from the promotion log | missing event |
| `DEFINE USER ... ON DATABASE` (a second login) | unexpected user |
| an extra event | unexpected event |
| `DEFINE PARAM` | unexpected param |
| the privileged gate event removed | missing event |

It also reports the schema re-applied after access (from the coordinating session): `DEFINE TABLE OVERWRITE` resets PERMISSIONS, and every such table is reported as altered.

### What it does not see

- **Data.** It does not see a credential row added, a log entry edited, or a restore through `POST /import` / `OPTION IMPORT`. The coordinating session measured that an import bypasses ASSERTs and events.
- **Anything between runs.** A rule removed, used, and restored between two runs is not seen.
- **Signing keys.** A replaced JWT signing key is redacted by the server, so it is not seen.

## B. Principals and the promoter's own evidence

### Declared as

```yaml
independent: true                                           # unchanged: a different credential
independent: {between: principal}                           # a different declared principal
independent: {own_evidence: true}                           # the promoter created a counted evidence link
independent: {between: principal, own_evidence: true}
```

`config.independence_of(rule)` is the only reader. That matters: a mapping is not `is True`, and every old reader would have switched the rule off silently. Those readers were `independent_rules`, `probes`, `_staged_create`, `spec`, and `skills._gates_block`. Config refuses:
- an unknown key or value;
- `own_evidence` on a rule with no `requires` clause;
- `own_evidence` where no role writes both the table and one of its evidence edges;
- a declared table or edge named `row_writer`, `link_creator`, `promotion_log` or `promotion_head`.

### Measured before designing

- **A VALUE clause runs with the writer's permissions.** `$auth.principal` read there is NONE, because the agent table is `PERMISSIONS NONE`. **An EVENT runs privileged:** it reads `$auth.principal` and `$auth.provisioned_at`, reads any row, and writes tables whose PERMISSIONS are NONE.
- **Computed fields and `$auth` behave the same on relation tables as on node tables.** A `written_by` VALUE field on an edge accumulated credentials, and a caller's SET of it was discarded.
- **Because of that, `written_by` on an edge is not evidence of work.** The promoter ran `UPDATE cites SET x = 1` on the proposer's edge, landed on its `written_by`, and a written_by-based own-evidence check let the promotion through. **What counts is who created the link.**
- `in` and `out` of a relation cannot be changed by UPDATE: the write succeeds and they keep their values. A created link cannot be re-pointed.
- Several events on one table ran in name order (observed once), and a THROW in any of them aborts the whole write, including what earlier events wrote.
- A write that changes nothing fires no event.

### Built

- **`row_writer:[row, credential]`**: the first write of each row by each credential. It records the credential's `principal` and `provisioned_at`, copied at that moment by a privileged event. It exists on tables under a principal rule, and on every gated table when the promotion log is on. No credential can write it.
- **`link_creator:[edge]`**: which credential and principal created each evidence link, written on the CREATE event of edges that `own_evidence` rules count.
- **`<rule>_independent_mode`**: one event that runs the principal check first, then the own-evidence check. The credential event of `independent: true` still runs as well, unchanged.
- **Principal check:** refuses when the promoter has no declared principal, when any writer of the row has no recorded principal, or when the promoter's principal is among the writers'.
- **Own-evidence check:** refuses unless the promoter created at least one link that a `requires` clause counts, including that clause's `where`. Between principals, a link created by any credential of the promoter's principal counts.
- **Refusal reasons:** each refusal carries its reason. `gates.independence_reason_in` returns `writer`, `principal` or `own_evidence`, and `write.py` tells the agent what to do for each.
- **Provisioning** now records `principal`, `provisioned_at` (the datastore clock) and `provisioned_by` (as declared). `provision` refuses a credential without a principal when a rule compares principals. `access.credentials_report` lists principals that hold more than one credential.

Why principals are copied at write time: suppose they were read from the agent row at promotion time. A proposer re-provisioned under another principal after writing would then let its own second login promote. Also, a revoked proposer's row could never be promoted by anyone. Both cases are tested.

**Probes** (`gates.run_probes`):
- Principal rules add a third credential bound to the proposer's principal (`sibling_promotion_reject`).
- Own-evidence rules add a promoter that holds only root-created evidence (`borrowed_evidence_reject`).
- Every probe credential then creates a link of its own, so the self and sibling refusals can only be about who they are.
- Both flags are required by `judge` and pinned by the baseline. The baseline also fails a mode downgrade and the log being switched off.

Bearer grants (`DEFINE ACCESS ... TYPE BEARER FOR RECORD`) were measured as a provisioning primitive. The grant is issued, `/signin` with the key returns a token, and revocation works. But the token is refused on HTTP `/sql` with "The access method cannot be used in the requested operation". The engine's client is HTTP. **Not adopted.**

## The same rule, whoever is calling (reported by the coordinating session, reproduced)

A field ASSERT's subqueries run under the **caller's** row permissions. Take the readiness rule reduced to its forbids clauses:
- root is refused;
- a credential that reads critiques is refused;
- **a credential that may write `conclusion` but cannot read `critique` is ACCEPTED, with a fatal critique standing.**

The same expression inside an event refused all three.

Every rule with clauses is now **also** emitted as a privileged event, `<rule>_gate`. It fires on every write that leaves the field at the gated value, as the ASSERT does. The ASSERT stays, with its message, for every caller that sees the rows.

- **Probe:** when a role writes the rule's table but cannot read what a forbids clause counts (`gates.blind_role`), the probe provisions it. Before promotion, with the forbidden row present, that credential must be refused (`blind_forbidden`). This is done before promotion because a re-sent value changes nothing and fires no event.
- **When no probe runs:** if no such role exists, or access is not applied, the report says so. Nothing can be blind in that case: without record access every writer is a system user.
- **Still true:** a `requires` clause refuses a caller who cannot see the evidence, because the ASSERT counts 0. That fails closed: a blocked legitimate write, never a hole. Removing the requires/forbids part from the ASSERT would fix it and would change every instance's gate text. Left for the owner.
- **Pre-existing, not changed:** a forbids-only rule is judged misbehaved by the probes, because with no `requires` there is no "refused without evidence" step.
- **Probe role selection** (from the first non-demo instance): a probe credential of a role that cannot read what a rule counts can never pass that rule, so an independence probe built on one fails for a reason unrelated to independence. `_probe_identities` now uses only roles that can read every clause. When none can, it says so by name.
- **`gates.unpassable_roles(inst)`** names every role that writes a gated table but could never make its gated change; the probe report and `gates --apply` print them. They are **named, not refused**: the demo's `loader` creates conclusions it never promotes, which is ordinary. Refusing them at config load would refuse the demo.
- **The INDEPENDENCE summary** now accounts for every rule: `N require an independent promoter: X passed, Y untested, Z FAILED`. Before, a rule that failed without being untested appeared in neither count.
- **Two tests changed:** `test_gates.py` has two tests that weakened a gate by redefining only its ASSERT. Those tests now also remove the gate event: a gate that still has its event has not been weakened.

## C. The promotion log (`xscholar/promotion_log.py`, `promotion_log: true`)

### Measured

- `crypto::sha256` works.
- An event can compute a hash over the previous entry, read through a record link to a single head record.
- Two log events in one write chain correctly.
- A THROW elsewhere in the write removes the entry.
- A Python verifier reproduces every hash, including a row id with non-ASCII characters. `string::len` counts code points, as Python `len` does.
- **Concurrency.** 60 concurrent promotions on memory, rocksdb and surrealkv:
  - 18 to 26 were refused with "Transaction conflict ... can be retried", and those did not happen at all;
  - there was never a fork or a gap;
  - log entries always equalled promoted rows.

  Under a break that removed the single head record, the concurrency test went red. Linearity rests on that one contended record, with the entry id equal to its sequence number as a second guard.

### Built

**Entries.** One event per gate rule appends `promotion_log:<seq>`. It holds:
- the rule, table, row, field, and the value before and after;
- the credential (or `root`), with its principal and its `provisioned_at`;
- the datastore clock;
- `late`;
- `prev` and `hash`.

The hash is taken over every field, length-prefixed. The head is `promotion_head:head`. The log fields are READONLY, and an event refuses UPDATE and DELETE even from root. Removing that guard is a schema change the drift check reports.

**`late`** (a heuristic, and named as one): `yes` when the promoting credential was provisioned after some writer of the row first wrote it. That is the signature of "make a second login to get past the rule". It is also what an honest reviewer who joined later looks like. It is reported, never blocking.

**`verify`** recomputes the chain and names the first broken link by sequence number:
- an edited field;
- a removed entry;
- a relinked entry;
- a head that disagrees with the last entry.

**`export_anchor`** writes the verified head outside the database. It refuses a broken chain, and it refuses to replace an anchor that the chain no longer contains. A path ending `.checkpoint` gets the body of a C2SP tlog-checkpoint note (origin, size, base64 hash), so a witness or timestamping service can hold it. It is unsigned, and the hash is a chain head, not an RFC 6962 Merkle root: a witness can keep and countersign it but cannot check a consistency proof.

**Limit, asserted in a test.** Root can rebuild the whole chain from the first entry, or truncate it and rewrite the head, and `verify` alone passes. Only a verification against an anchor taken earlier and kept off the database host fails. Entries after the last anchor are only as trustworthy as the database.

**Probe promotions are logged and never removed.** Every probe run on a log-enabled instance adds a few entries.

## What remains possible, stated plainly

- **Root** can do all of the following. Each is a deliberate act by the datastore's owner:
  - provision any number of credentials under any principal;
  - redefine or remove any event, field, index or permission;
  - edit `row_writer`, `link_creator` and agent rows;
  - rewrite the log;
  - restore an export that skips every ASSERT and event.
- **What is now detectable:**
  - a schema change, if `live_schema` runs while the change is in place;
  - a log rewrite behind an exported anchor;
  - a credential provisioned after a row was written and then used to promote it (`late`, heuristic).
- **A principal is what the provisioner declared.** One person provisioned under two principal names is two principals.
- **Own evidence proves the promoter created a counted link.** It does not prove the promoter read the source behind it. Faking independence now needs two principals, and the second must link counted evidence itself.
- **After promotion, the proposer can still edit the row's other content.** That was not closed here.
- **Everything above was measured on 3.1.6 only.** It depends on the following 3.1 behaviours:
  - VALUE clauses under the writer's permissions, events privileged;
  - ASSERT subqueries under the caller's permissions;
  - no event on a no-op write;
  - `INFO` rendering, including the full password hash;
  - transaction conflicts on a contended record;
  - bearer tokens refused over HTTP.

  3.1.6 falls within two published advisory ranges (GHSA-66r2, GHSA-848m). Upgrading means re-measuring each of these.
