-- scope: project
-- The rules, enforced in the datastore for every writer. Root ($auth = NONE) is the migration and loader
-- identity: the role clauses let it through, the evidence clauses do not. Each rule is named; the tests in
-- crates/gw-store/tests/rules.rs prove each one both ways (refused without, accepted with).

-- R1 stale_on_hash_change (from 2026-09-17): a unit whose hash changes marks every LIVE fact citing it
-- `stale`. Never deletes. Events run privileged: an agent with no update on fact still triggers it.
DEFINE EVENT OVERWRITE stale_on_hash_change ON TABLE unit
  WHEN $event = 'UPDATE' AND $before.hash != $after.hash
  THEN { UPDATE fact SET status = 'stale', stale_since = time::now() WHERE status = 'live' AND id IN $after.id<-evidence<-fact; };

-- R2 confirmed_needs_evidence: grade CONFIRMED only with a receipt, an owner statement, or at least one
-- evidence link to a unit. The rule sits on the REQUIRED field `grade` (an ASSERT on an optional field left
-- empty never runs; measured 2026-10-24), and a field ASSERT re-runs on every write to the row, so taking
-- the evidence away later is refused while the fact says CONFIRMED. $this is the row being written
-- (measured 2026-10-24 on 3.1.6: in a top-level field ASSERT $parent is NONE and $this is the record; inside a
-- subquery $this is rebound to the subquery's own row, so `WHERE in = $this.id` never matches. Use the graph.)
DEFINE FIELD OVERWRITE grade ON fact TYPE string DEFAULT 'UNKNOWN'
  ASSERT $value IN ['CONFIRMED', 'INFERRED', 'UNKNOWN']
    AND ($value != 'CONFIRMED'
      OR $this.receipt != NONE
      OR $this.owner_statement != NONE
      OR count($this.id->evidence) >= 1);

-- R3 receipts_are_immutable: every field is READONLY, a receipt cannot be dated into the future, and under
-- a member credential `ran_by` is that member.
DEFINE FIELD OVERWRITE command ON receipt TYPE string READONLY;
DEFINE FIELD OVERWRITE machine ON receipt TYPE string READONLY;
DEFINE FIELD OVERWRITE location ON receipt TYPE string READONLY;
DEFINE FIELD OVERWRITE exit_code ON receipt TYPE option<int> READONLY;
DEFINE FIELD OVERWRITE ok ON receipt TYPE bool READONLY;
DEFINE FIELD OVERWRITE output_hash ON receipt TYPE string READONLY;
DEFINE FIELD OVERWRITE commit ON receipt TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE ran_at ON receipt TYPE datetime DEFAULT time::now() READONLY ASSERT $value <= time::now();
DEFINE FIELD OVERWRITE ran_by ON receipt TYPE option<record<member>> DEFAULT $auth.id READONLY
  ASSERT $auth = NONE OR $value = $auth.id;
