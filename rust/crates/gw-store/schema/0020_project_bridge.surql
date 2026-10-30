-- scope: project
-- Bridges (2026-10-30, idea-bridges design section 2, slice B2): a hypothesis that a capability card of another
-- project answers a problem card of this one. It lives in the target (problem) project and holds the source
-- only as project, card key and card hash, never its text. A bridge is a hypothesis until it passes its gate:
--   candidate  -> both cards were live when it was written (the program checks the source, which is in another
--                 namespace; this schema checks the target);
--   testable   -> one assumption check per source assumption (each quoting the target), a novelty check (the
--                 search the program ran: query, reach, hits) and a written test;
--   supported  -> all of testable, and a receipt from run or gate with ok = true;
--   refuted    -> a receipt, or an assumption check whose verdict is `fails` (the mismatch, quoted).
-- Refuted bridges stay, as negatives; nothing deletes a bridge. Grade is INFERRED until supported. The rules sit
-- on the required field `status` and re-run on every write (an ASSERT on an optional field left empty never
-- runs, 0004); `$before` is the old status (NONE on create) and `$this` holds the rest of the same write
-- (measured on 3.1.6, 2026-10-30).
DEFINE TABLE IF NOT EXISTS bridge TYPE NORMAL SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL;
ALTER TABLE bridge PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
DEFINE FIELD OVERWRITE target ON bridge TYPE record<card> READONLY
  ASSERT $before != NONE OR ($value.kind = 'problem' AND $value.status = 'live');
-- The source project's registry name (letters, digits, '.', '_', '-').
DEFINE FIELD OVERWRITE source_project ON bridge TYPE string READONLY ASSERT $value = /^[A-Za-z0-9._-]{1,64}$/;
DEFINE FIELD OVERWRITE source_card ON bridge TYPE string READONLY ASSERT $value = /^[A-Za-z0-9_]{1,64}$/;
DEFINE FIELD OVERWRITE source_hash ON bridge TYPE string READONLY ASSERT $value = /^[0-9a-f]{64}$/;
-- How many assumptions the source card states, read by the program when the bridge is written.
DEFINE FIELD OVERWRITE source_a ON bridge TYPE int READONLY ASSERT $value >= 1;
-- The ABC chain's middle card (the source project's problem the capability solved), when it was found that way.
DEFINE FIELD OVERWRITE via_card ON bridge TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE via_hash ON bridge TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE found_by ON bridge TYPE string READONLY;

DEFINE FIELD OVERWRITE assumption_check ON bridge TYPE array<object> DEFAULT [];
DEFINE FIELD OVERWRITE assumption_check.*.assumption ON bridge TYPE string;
DEFINE FIELD OVERWRITE assumption_check.*.verdict ON bridge TYPE string ASSERT $value IN ['holds', 'fails', 'unknown'];
DEFINE FIELD OVERWRITE assumption_check.*.unit ON bridge TYPE string;
DEFINE FIELD OVERWRITE assumption_check.*.cite ON bridge TYPE string;
DEFINE FIELD OVERWRITE assumption_check.*.quote ON bridge TYPE string ASSERT string::len(string::trim($value)) > 0;
DEFINE FIELD OVERWRITE assumption_check.*.quote_hash ON bridge TYPE string;
DEFINE FIELD OVERWRITE novelty_check ON bridge TYPE option<object> FLEXIBLE;
DEFINE FIELD OVERWRITE test ON bridge TYPE option<string>;
DEFINE FIELD OVERWRITE receipt ON bridge TYPE option<record<receipt>>;

DEFINE FIELD OVERWRITE status ON bridge TYPE string DEFAULT 'candidate'
  ASSERT $value IN ['candidate', 'testable', 'supported', 'refuted'] AND (
    ($value = 'candidate' AND ($before = NONE OR $before = 'candidate'))
    OR ($value = 'testable' AND $before IN ['candidate', 'testable']
      AND $this.target.status = 'live'
      AND array::len($this.assumption_check) = $this.source_a
      AND $this.novelty_check != NONE
      AND $this.test != NONE AND string::len(string::trim($this.test)) > 0)
    OR ($value = 'supported' AND $before IN ['testable', 'supported']
      AND $this.target.status = 'live'
      AND array::len($this.assumption_check) = $this.source_a
      AND $this.novelty_check != NONE
      AND $this.test != NONE
      AND $this.receipt != NONE AND $this.receipt.ok = true)
    OR ($value = 'refuted' AND $before IN ['candidate', 'testable', 'refuted']
      AND ($this.receipt != NONE OR 'fails' IN $this.assumption_check.verdict)));
DEFINE FIELD OVERWRITE grade ON bridge TYPE string DEFAULT 'INFERRED'
  ASSERT $value IN ['CONFIRMED', 'INFERRED', 'UNKNOWN'] AND ($value != 'CONFIRMED' OR $this.status = 'supported');
DEFINE FIELD OVERWRITE at ON bridge TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE by ON bridge TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE machine ON bridge TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE written_by ON bridge TYPE option<record<member>> DEFAULT $auth.id READONLY
  ASSERT $auth = NONE OR $value = $auth.id;
DEFINE INDEX OVERWRITE bridge_target ON bridge FIELDS target;
DEFINE INDEX OVERWRITE bridge_source ON bridge FIELDS source_project, source_card;
