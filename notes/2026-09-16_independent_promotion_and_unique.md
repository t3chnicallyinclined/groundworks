# 2026-09-16: independent promotion and unique fields

Two defects reported from beta-testing the engine on a real instance, both present in this engine.

- **A.** A research workflow marked authorities verified after reading each one once. The adversarial verifier meant to run afterwards died on a usage limit, and nothing held the promotion back: 50 authorities went verified on a single reading. Here, the demo's `reader` role may write `fact` and `cites`, so one identity can propose, cite and confirm.
- **B.** Two authority rows carried an identical citation string. Nothing let an instance declare a field unique.

## Measured on SurrealDB 3.1.6 (throwaway in-memory server, port 8070)

Unique index (`DEFINE INDEX ... FIELDS f UNIQUE`):
- A duplicate on CREATE, UPDATE or UPSERT is refused: ``Database index `authority_citation_unique` already contains 'X 1', with record `authority:a` ``. Updating a row to its own current value is not a duplicate.
- The comparison is exact. `'x 1'` and `'X 1 '` were both accepted next to `'X 1'`. The index stops copied rows, not differently typed ones.
- Two rows with NONE in an optional unique field were both accepted.
- `CREATE f; CREATE g` in one request with the same value: `f` was written and `g` refused. Without BEGIN/COMMIT, statements are not atomic.
- Defining the index over rows that already collide is refused with the same message, and no index is left behind. Applying the schema to a graph that already holds duplicates therefore fails loudly and names the first duplicate.
- After `REMOVE INDEX`, duplicates are accepted again.

Independence:
- Under root over HTTP Basic, `RETURN $auth` is NONE and `$session.ac`/`rd` are NONE. A record-access sign-in gives `$auth = agent:<id>`.
- `DEFINE FIELD written_by ... VALUE array::union($before ?? [], IF $auth = NONE { [] } ELSE { [$auth] })` is recomputed on every write. `SET written_by = []` and `SET written_by = [agent:other]` are discarded, both for a record user and for root.
- With `READONLY` added, the value froze at creation and later writers were silently not recorded. That is why the field is not READONLY.
- `THROW` inside `DEFINE EVENT` aborts the write with an error status: `Error while processing event <name>: An error occurred: <text>`. The row is unchanged, and `CREATE` at the gated value leaves no row.
- Inside an event, `$before` is the whole previous row and `$auth` is the credential that made the outer write. A nested event (the history append) sees the same `$auth`.

## Design

`independent: true` on a gate rule. The generated statements are emitted with the gates, not with access, so an instance that skips the access step still enforces it:
- The rule's table gets `written_by`, as above.
- An event per rule: `WHEN $after.f = V AND ($before.f ?? NONE) != V`, `THROW` if `$auth = NONE`, or `written_by` was empty, or `$auth` is already on it.

What that means:
- A proposer never promotes its own row.
- Root never promotes.
- A row only root has written names no proposer, so nobody can show they are independent of one. It is refused until a credential writes it.
- Re-promotion after a demotion needs a credential that has never written the row. The earlier promoter is on the record now.

Why the check is not a gate clause: `gate_expression` counts neighbours of `$this` and cannot read a field of the row under test.

Why it is not a field ASSERT: inside an ASSERT, `$before` is that field's previous value, not the previous row.

Config refuses `independent: true` in three cases, by name:
- the instance has no `access:` section;
- no role both reads and writes the table;
- a declared table defines `written_by` itself.

Probes use two credentials of one role, provisioned through `access.provision`:
- The proposer writes the row.
- The promoter makes every evidence attempt, so a refusal can only be about evidence.
- With the evidence in place, the proposer and root must each be refused by that rule's own THROW. The rule id is read back out of the message.
- The promoter must then be accepted.

If the credentials cannot be built (access not applied, only lens-bound roles, or a non-root probe connection), the rule is `missing_independence_probe` with the reason and is judged misbehaved.

`write.py` reports an independence refusal as "who is writing". Before this change it described it as missing citations, which would send an agent to cite more and retry forever.

`unique: {table: [field]}`:
- One index per field.
- Probe rows get a distinct value for every unique field of their table, so probe rows never collide with each other.
- The unique probe writes a first row, then a duplicate, which must be refused by that field's own index with no row left behind, then a distinct value, which must be accepted.

## Limits, stated plainly

- Root owns the datastore. It can provision a second credential, or `REMOVE EVENT`. No rule inside the datastore stops its owner. The baseline catches the rule leaving the config, not a hand edit of the live schema.
- Independence means a different credential, not a different reader. Two credentials held by one process can still launder a promotion. What this closes is the case where one lane, or root alone, promotes what it wrote.
- After an independent promotion, the proposer may still edit the row's content (for example `statement`) while it stays promoted. That hole already existed for every evidence gate, and it is not closed here.
- A rule whose evidence is itself behind an independence rule (V2-18 staged evidence) is reported unbuilt, not built with credentials.
- `temporal.run_probes` promotes a conclusion as root through the readiness probe recipe. On an instance with `independent: true` on `conclusion.ready`, that check would fail. `temporal.py` belongs to another lane.
- The demo does not switch independence on. `adapter.py` and several suites confirm facts as root or under one identity, and the owner decides the demo's gates.
