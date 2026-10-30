-- scope: project
-- Attack verdicts and answers (2026-10-30, idea-bridges design, amendment A2, slice 10; number to be retaken
-- at merge). Until now an attack was two fields on the attacking fact (0010: `attacks`, `stance`), editable
-- by the agent role, so `UPDATE fact SET attacks = NONE` withdrew an attack without deleting anything, and an
-- attack had no outcome. Now:
--   * `fact.attacks` and `fact.stance` are READONLY: set when the fact is written, never changed;
--   * an attack is an edge, attacking fact -> attacked fact, carrying its verdict (METHOD s2.6: the outcome
--     lives on the edge). The verdict is required, and moves once, from `unjudged` to fatal, damaging or
--     survivable; after that the edge is frozen. Nobody deletes an attack, owner and agent alike;
--   * an answer is an edge, answering fact -> attack, written once (rebutted, fixed, narrowed, conceded),
--     each outcome with what it needs to stand;
--   * every dispute becomes an attack: the existing ones by the backfill below, new ones by an event, so a
--     dispute recorded through journal_add is on the edge as `unjudged` the moment it is written.
-- Root ($auth = NONE) can still delete a row, as it can any row; it is the migration and loader identity
-- only. The ready gate that reads these edges is slice 11. Every rule is proven both ways in
-- crates/gw-store/tests/attacks.rs, and goes red in crates/gw-store/tests/mutations.rs.

-- No withdrawal: the attack fields are written once, with the fact.
DEFINE FIELD OVERWRITE attacks ON fact TYPE option<record<fact>> READONLY
  ASSERT $value = NONE OR record::exists($value);
DEFINE FIELD OVERWRITE stance ON fact TYPE option<string> READONLY
  ASSERT $value = NONE OR $value IN ['disputes', 'supports'];

-- attack: in = the attacking fact, out = the fact it attacks; `why` says why it is an attack. A field ASSERT
-- re-runs on every write and `$before` is that field's old value (NONE on create). The update permission is
-- read on the row as it will be, not as it was (measured on 3.1.6, 2026-10-30), so the freeze is in the fields:
-- the verdict moves once, from `unjudged`, and must come with its `ruling` (the reason for the verdict),
-- which is written once; every other field is READONLY or pinned by its VALUE. That holds root too.
DEFINE TABLE IF NOT EXISTS attack TYPE RELATION IN fact OUT fact ENFORCED SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL;
ALTER TABLE attack PERMISSIONS FOR select, create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
DEFINE FIELD OVERWRITE in ON attack TYPE record<fact> READONLY;
DEFINE FIELD OVERWRITE out ON attack TYPE record<fact> READONLY ASSERT $value != $this.in;
DEFINE FIELD OVERWRITE why ON attack TYPE string READONLY ASSERT string::len(string::trim($value)) > 0;
DEFINE FIELD OVERWRITE lens ON attack TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE ruling ON attack TYPE option<string> ASSERT $before = NONE OR $before = $value;
DEFINE FIELD OVERWRITE verdict ON attack TYPE string
  ASSERT $value IN ['fatal', 'damaging', 'survivable', 'unjudged']
    AND ($before = NONE OR $before = 'unjudged' OR $before = $value)
    AND IF $value = 'unjudged' THEN $this.ruling = NONE
      ELSE $this.ruling != NONE AND string::len(string::trim($this.ruling)) > 0 END;
DEFINE FIELD OVERWRITE at ON attack TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE written_by ON attack TYPE option<record<member>> DEFAULT $auth.id READONLY
  ASSERT $auth = NONE OR $value = $auth.id;
DEFINE FIELD OVERWRITE judged_at ON attack TYPE option<datetime>
  VALUE IF $before != NONE THEN $before ELSE IF $this.verdict != 'unjudged' THEN time::now() ELSE NONE END;
DEFINE FIELD OVERWRITE judged_by ON attack TYPE option<record<member>>
  VALUE IF $before != NONE THEN $before ELSE IF $this.verdict != 'unjudged' THEN $auth.id ELSE NONE END;
DEFINE INDEX OVERWRITE attack_pair ON attack FIELDS in, out UNIQUE;
DEFINE INDEX OVERWRITE attack_out ON attack FIELDS out;

-- answer: in = the answering fact, out = the attack. Written once: every field READONLY, update and delete
-- NONE. The outcome's need is checked on create, against the answering fact as it is then:
--   rebutted  -> the answering fact has at least one evidence link (slice 9 narrows this to a confirming rung);
--   fixed     -> the answering fact has a receipt that ran green;
--   narrowed  -> replaced_by names a live fact, not the attacked one, holding the narrowed claim;
--   conceded  -> nothing.
-- An answer is a fact, so it can be attacked in its turn. The attacking fact cannot answer its own attack.
DEFINE TABLE IF NOT EXISTS answer TYPE RELATION IN fact OUT attack ENFORCED SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL;
ALTER TABLE answer PERMISSIONS FOR select, create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE in ON answer TYPE record<fact> READONLY;
DEFINE FIELD OVERWRITE out ON answer TYPE record<attack> READONLY;
DEFINE FIELD OVERWRITE replaced_by ON answer TYPE option<record<fact>> READONLY;
DEFINE FIELD OVERWRITE outcome ON answer TYPE string READONLY
  ASSERT $value IN ['rebutted', 'fixed', 'narrowed', 'conceded']
    AND $this.in != $this.out.in
    AND $this.in.status = 'live'
    AND (($value = 'rebutted' AND count($this.in->evidence) >= 1)
      OR ($value = 'fixed' AND $this.in.receipt != NONE AND $this.in.receipt.ok = true)
      OR ($value = 'narrowed' AND $this.replaced_by != NONE AND record::exists($this.replaced_by)
        AND $this.replaced_by != $this.out.out AND $this.replaced_by.status = 'live')
      OR ($value = 'conceded' AND $this.replaced_by = NONE));
DEFINE FIELD OVERWRITE at ON answer TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE written_by ON answer TYPE option<record<member>> DEFAULT $auth.id READONLY
  ASSERT $auth = NONE OR $value = $auth.id;
DEFINE INDEX OVERWRITE answer_pair ON answer FIELDS in, out UNIQUE;
DEFINE INDEX OVERWRITE answer_out ON answer FIELDS out;

-- Every dispute is an attack. Events run privileged, so the edge lands whatever the writer's table rights.
DEFINE EVENT OVERWRITE dispute_is_an_attack ON TABLE fact
  WHEN $event = 'CREATE' AND $after.stance = 'disputes' AND $after.attacks != NONE
  THEN {
    -- RELATE takes a variable or a literal record, not a field path.
    LET $from = $after.id;
    LET $to = $after.attacks;
    RELATE $from->attack->$to SET verdict = 'unjudged', lens = $after.lens,
      why = 'a dispute, recorded with the fact (stance disputes)';
  };

-- Backfill: each dispute already recorded becomes an unjudged attack. Supports stay as they are: a support is
-- not an attack.
-- The table is new in this file, so no dispute has an edge yet.
INSERT RELATION INTO attack (
  SELECT id AS in, attacks AS out, 'unjudged' AS verdict, lens,
    'a dispute recorded before attacks were edges (backfill, 0018)' AS why
  FROM fact WHERE stance = 'disputes' AND attacks != NONE
);
