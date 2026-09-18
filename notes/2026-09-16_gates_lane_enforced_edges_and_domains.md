# 2026-09-16: links to nothing, gates on free text, and a schema re-apply that locked everyone out

Generic; no matter content. Modules: `groundworks/gates.py`, `groundworks/config.py`, `groundworks/originals.py`.
Two defects were found in real use (edges to records that did not exist; a gated status field with no list of
values). The rest came from the previous lane's review. All measurements on SurrealDB 3.1.6, on throwaway
in-memory servers.

## 1. Edges to records that do not exist (B-011)

| Measured | Result |
|---|---|
| Demo schema: relation tables generated `ENFORCED` before | **0 of 9** |
| Plain relation table, `RELATE a:1->e->b:missing` | stored |
| `... TYPE RELATION IN a OUT b ENFORCED`, same RELATE | refused: `The record 'b:missing' does not exist` |
| Same, `in` missing / `INSERT RELATION` with a missing end | refused, same message |
| Record credential that cannot SELECT the target table, target exists | accepted (the existence check ignores PERMISSIONS) |
| Table altered to ENFORCED while it already holds a dangling edge | the edge **stays** |
| Endpoint deleted after the link was made | the edge is removed with it |

So every generated relation table is now `ENFORCED`, and `gates --apply` counts, per edge table, the links an
existing graph stored before enforcement and exits 1 when there are any (`gates.count_dangling_edges`).

Writers checked for ordering (each relates only rows it has already written or read):
- loader: upserts every `vocabulary.targets` conclusion before any `bears_on`; a label naming a target outside
  the vocabulary (which the validator already refuses, `groundworks/pipeline/validator.py`) would now make the load
  **fail** instead of storing a link to nothing (INFERRED from the code, not separately tested).
- write.py: `cite`/`support`/`relate`/`critique`/`answer` relate to rows the caller names. A cite of a unit not
  loaded yet now raises `DbError` (tested through `Writer.cite` and `Writer.relate`).
- probes: every plan CREATEs a row before it RELATEs it; staged evidence likewise.
- originals, assertions, the promotion log, temporal and the change-feed queue: write no edges.
- `lens.py` creates `falls_under` (label -> consideration) outside the schema generator; it is NOT enforced
  here (outside this lane's files) and is listed as undone.

Side effect worth knowing: an ENFORCED refusal tells a caller whether a record id exists even in a table it
cannot read. Ids are not secrets in this engine; recorded, not acted on.

## 2. A gate that reads a field with no list of values (B-014)

Refused at config load, by name: a gate rule whose `table.field` has no enumerated domain (a vocabulary list the
engine enforces, a declared `one_of`, or a bool), a gate value outside the domain, and a clause `where` term
(`field = literal` / `field IN [...]`) comparing a field with no domain or a value outside it. The `where` half
is a judgement beyond the brief: a misspelt value in a `forbids` clause fails **open** (the forbidden row is not
counted). Other `where` shapes are left to the probes, which already report them untested.

Decided against: a warning for a declared categorical field no gate reads. The engine cannot tell a
categorical string from free text without guessing from field names (domain words in engine code), and a
warning on every free string (`summary`, `when`) trains people to ignore warnings. A field starts to matter
when a rule reads it, and that is exactly where it is now refused.

## 3. A rule that only forbids was always reported misbehaving

With nothing required, nothing refuses the first attempt. Now the first attempt is skipped for such a rule and
its refusal is the forbidden-evidence probe's (refused while the row exists, accepted once removed). Skipping
it also matters for independence: a promoter whose attempt went through is on the row's writers from then on.
Tested plain and independent, correct (behaves) and with its ASSERT and event removed (misbehaves).

## 4. Rules that could not be probed

- Staged evidence behind an independence rule is written by a provisioned proposer credential and promoted by a
  provisioned promoter (a different principal under `between: principal`; under `own_evidence` the promoter
  first relates a link of its own). `CredentialStatement` marks those statements; one run as root is refused by
  the independence event, so a reader that forgets fails, never passes. When no access role could make the
  promotion the plan says so by name; when credentials cannot be provisioned at run time the rule gets a
  named `setup_error`.
- Originals probes of a rule that needs an independent promoter now run: proposer writes, `promoter` makes the
  first accepted promotion, `promoter_b` (a principal of its own) makes every later attempt, because the first
  promoter is a writer after its change went through.

## 5. A schema re-apply wiped PERMISSIONS and CHANGEFEED

Demo, applied schema, gates, temporal, access, changefeed, then the schema again:

| | generator at c7d16fc | now |
|---|---|---|
| tables reset to PERMISSIONS NONE | **14 of 20** | 0 |
| changefeeds dropped | **5** | 0 |
| table definitions changed at all | 14 | 0 |

`DEFINE TABLE OVERWRITE` replaces the whole definition. `ALTER TABLE t TYPE NORMAL SCHEMAFULL` and `ALTER TABLE e
TYPE RELATION IN a OUT b ENFORCED SCHEMALESS` change only what they name (measured: PERMISSIONS and CHANGEFEED
kept; a hand-made SCHEMALESS restored). Each table is now `DEFINE TABLE IF NOT EXISTS` then `ALTER TABLE`.
Chosen over writing PERMISSIONS and CHANGEFEED into the definition because the schema cannot know the queue's
retention or whether the queue is used, and a second copy of access.py's permissions would drift. Consequence:
a schema re-apply no longer resets a hand-widened PERMISSIONS clause either; re-applying access does, and the
live-schema check names it.

## 6. DEFINE PARAM

The engine emits no `DEFINE PARAM` and reads none: `$confirming`/`$usable` are expanded in Python, and every other
`$name` in generated SurrealQL is SurrealDB's own (`$value`, `$this`, `$before`, `$after`, `$event`, `$auth`),
a LET or closure binding, or a record-access SIGNIN variable (`$id`, `$pass`). Measured: a database param named
`$auth`, `$value`, `$before`, `$pass` or `$id` does not replace the built-in (an ASSERT, an event and a sign-in
with a wrong password were all still refused); a gate that reads its own param IS loosened by `DEFINE PARAM
OVERWRITE` (that is the reviewer's measurement). So nothing to pin in the baseline; `ParamTests` goes red if a
generator ever reads a param, and the live-schema check reports a hand-added one (`UNEXPECTED param`).

## For UPGRADING

- Re-apply the schema (`gates --apply`, or the runner). It is now safe after access and the queue.
- Count dangling edges (`gates --apply` prints `DANGLING:` and exits 1); delete or repair them. ENFORCED does
  not look at what is already stored.
- A config with a gate or clause on a field without `one_of` no longer loads. Give the field `one_of`.
- Loads and writes that relate to a missing record now fail loudly instead of storing a link to nothing.
