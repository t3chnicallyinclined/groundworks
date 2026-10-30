-- scope: project
-- Bridges (2026-10-30, idea-bridges design section 2, slice B2, with the scholar-architect's review against
-- METHOD section 13 the same day): a hypothesis that a capability card of another project answers a problem card
-- of this one. It lives in the target (problem) project and holds the source only as project, card key and card
-- hash, never its text. A bridge is a hypothesis until it passes its gate:
--   candidate  -> both cards were live when it was written (the program checks the source, which is in another
--                 namespace; this schema checks the target). Lens verdicts may be recorded while it is one;
--   testable   -> one assumption check per source assumption (each quoting the target); a novelty verdict
--                 `not_made` with what was searched (query, reach, hits); a skeptic lens verdict `proceed` and no
--                 lens verdict `reject`; and the test registered before it runs: its words, the command and the
--                 expected result, stamped with the time it was registered (`test_at`), never changed after;
--   supported  -> all of testable; a receipt of the registered command run after registration with ok = true;
--                 a red receipt run after registration with ok = false, either the same command run before the green
--                 one or the registered control command (the test was shown able to fail); and written by a second,
--                 independent credential, not the one that proposed the bridge (event below; DECISIONS.md
--                 2026-09-16 'Independent promotion');
--   refuted    -> a run of the registered command after registration that failed, or an assumption check whose
--                 verdict is `fails` (the mismatch, quoted).
-- Refuted bridges stay, as negatives; nothing deletes a bridge. Grade is INFERRED until supported. The rules sit
-- on the required field `status` and re-run on every write (an ASSERT on an optional field left empty never
-- runs, 0004); `$before` is the old value (NONE on create) and `$this` holds the rest of the same write
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
-- Whether the source rests on recollection (a card of the memory project, or one quoting a memory, instructions,
-- agent, skill or command unit), read by the program from the source card: such a bridge never passes INFERRED.
DEFINE FIELD OVERWRITE source_recollection ON bridge TYPE bool READONLY DEFAULT false;
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
-- The novelty check is a verdict: `not_made` (the link was not already made) or `made`, with what was searched.
DEFINE FIELD OVERWRITE novelty_check ON bridge TYPE option<object> FLEXIBLE;
-- METHOD section 13's lenses, each with its own verdict: statistician, target-field expert, vocabulary
-- translator, skeptic. The skeptic is required before testable.
DEFINE FIELD OVERWRITE lens_verdicts ON bridge TYPE array<object> DEFAULT [];
DEFINE FIELD OVERWRITE lens_verdicts.*.lens ON bridge TYPE string
  ASSERT $value IN ['statistician', 'target-field-expert', 'vocabulary-translator', 'skeptic'];
DEFINE FIELD OVERWRITE lens_verdicts.*.verdict ON bridge TYPE string ASSERT $value IN ['proceed', 'reject'];
DEFINE FIELD OVERWRITE lens_verdicts.*.why ON bridge TYPE string ASSERT string::len(string::trim($value)) > 0;
-- The registered test: written once, stamped when registered.
DEFINE FIELD OVERWRITE test ON bridge TYPE option<string> ASSERT $before = NONE OR $before = $value;
DEFINE FIELD OVERWRITE test_command ON bridge TYPE option<string> ASSERT $before = NONE OR $before = $value;
DEFINE FIELD OVERWRITE test_expect ON bridge TYPE option<string> ASSERT $before = NONE OR $before = $value;
DEFINE FIELD OVERWRITE control_command ON bridge TYPE option<string> ASSERT $before = NONE OR $before = $value;
DEFINE FIELD OVERWRITE test_at ON bridge TYPE option<datetime>
  VALUE IF $before != NONE { $before } ELSE IF $this.test_command != NONE { time::now() } ELSE { NONE };
DEFINE FIELD OVERWRITE receipt ON bridge TYPE option<record<receipt>>;
DEFINE FIELD OVERWRITE red_receipt ON bridge TYPE option<record<receipt>>;

DEFINE FIELD OVERWRITE status ON bridge TYPE string DEFAULT 'candidate'
  ASSERT $value IN ['candidate', 'testable', 'supported', 'refuted'] AND (
    ($value = 'candidate' AND ($before = NONE OR $before = 'candidate'))
    OR ($value IN ['testable', 'supported'] AND $before IN ['candidate', 'testable', 'supported']
      AND ($value = 'supported' OR $before != 'supported')
      AND ($value != 'supported' OR $before != 'candidate')
      AND $this.target.status = 'live'
      AND array::len($this.assumption_check) = $this.source_a
      AND $this.novelty_check.verdict = 'not_made'
      AND array::len($this.novelty_check.reach) > 0
      AND array::len($this.lens_verdicts[WHERE lens = 'skeptic' AND verdict = 'proceed']) > 0
      AND array::len($this.lens_verdicts[WHERE verdict = 'reject']) = 0
      AND $this.test != NONE AND string::len(string::trim($this.test)) > 0
      AND $this.test_command != NONE AND $this.test_expect != NONE AND $this.test_at != NONE
      AND ($value = 'testable' OR (
        $this.receipt.ok = true AND $this.receipt.command = $this.test_command AND $this.receipt.ran_at > $this.test_at
        AND $this.red_receipt.ok = false AND $this.red_receipt.ran_at > $this.test_at
        AND (($this.red_receipt.command = $this.test_command AND $this.red_receipt.ran_at < $this.receipt.ran_at)
          OR ($this.control_command != NONE AND $this.red_receipt.command = $this.control_command)))))
    OR ($value = 'refuted' AND $before IN ['candidate', 'testable', 'refuted']
      AND (($this.receipt.ok = false AND $this.receipt.command = $this.test_command AND $this.receipt.ran_at > $this.test_at)
        OR 'fails' IN $this.assumption_check.verdict)));
DEFINE FIELD OVERWRITE grade ON bridge TYPE string DEFAULT 'INFERRED'
  ASSERT $value IN ['CONFIRMED', 'INFERRED', 'UNKNOWN']
    AND ($value != 'CONFIRMED' OR ($this.status = 'supported' AND $this.source_recollection = false
      AND $this.source_project != 'memory'));
DEFINE FIELD OVERWRITE at ON bridge TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE by ON bridge TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE machine ON bridge TYPE option<string> READONLY;
-- The credential that proposed the bridge (checked on create only: a later writer is another credential).
DEFINE FIELD OVERWRITE written_by ON bridge TYPE option<record<member>> DEFAULT $auth.id READONLY
  ASSERT $before != NONE OR $auth = NONE OR $value = $auth.id;
DEFINE INDEX OVERWRITE bridge_target ON bridge FIELDS target;
DEFINE INDEX OVERWRITE bridge_source ON bridge FIELDS source_project, source_card;

-- Independent promotion (DECISIONS.md 2026-09-16; scholar-architect 2026-10-30): the write that makes a bridge
-- supported must come from a signed-in credential other than the one that proposed it. Root has no $auth and
-- can never promote. While every agent session shares one agent login per namespace, that means the owner (or
-- a separately provisioned member) promotes.
DEFINE EVENT OVERWRITE independent_promotion ON TABLE bridge
  WHEN $event = 'UPDATE' AND $after.status = 'supported' AND $before.status != 'supported'
  THEN {
    IF $auth = NONE OR $auth.id = NONE OR $auth.id = $after.written_by {
      THROW 'supported needs a second, independent writer: not the credential that proposed the bridge';
    };
  };
