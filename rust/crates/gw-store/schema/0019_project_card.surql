-- scope: project
-- Idea cards (2026-10-30, idea-bridges design section 2, slice B1). A card is one problem, capability,
-- assumption or limitation of this project, in facets an agent writes (x the goal, y the blocker of a
-- problem, a the assumptions and mech the mechanism of a capability), held to a quote the program copied
-- word for word: a span of an ingested unit, or words of a recorded fact. No model makes or reads a card.
--
-- A card is written once: every facet, the quote and the grade are READONLY; only its status moves, from live
-- to stale when the quoted unit's text changes or leaves, or the quoted fact stops being live. Nothing deletes
-- a card. Measured on 3.1.6 (throwaway server, 2026-10-30): in a field ASSERT $this already holds the other
-- fields of the same write, $this.<link>.<field> follows a record link, and $before is the field's old value
-- (NONE on create). A rule that reads a linked row is checked on create only (`$before != NONE OR ...`): a field
-- ASSERT re-runs on every write, and the linked row may change later (a unit reclassified), which must never
-- refuse the stale event, and with it the intake that fired it.
DEFINE TABLE IF NOT EXISTS card TYPE NORMAL SCHEMAFULL;
ALTER TABLE card PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
DEFINE FIELD OVERWRITE kind ON card TYPE string READONLY
  ASSERT $value IN ['problem', 'capability', 'assumption', 'limitation'];
-- A problem names its blocker (the ABC chain matches blockers across projects); nothing else has one. The rule
-- sits on the required field x: an ASSERT on an optional field left empty never runs (0004, measured).
DEFINE FIELD OVERWRITE x ON card TYPE string READONLY
  ASSERT string::len(string::trim($value)) > 0 AND string::len($value) <= 300
    AND ($this.kind = 'problem') = ($this.y != NONE);
DEFINE FIELD OVERWRITE y ON card TYPE option<string> READONLY ASSERT string::len($value) <= 300;
-- A capability states at least one assumption (a bridge's gate checks each one in the target; none would make
-- that gate vacuous); nothing else has any.
DEFINE FIELD OVERWRITE a ON card TYPE array<string> READONLY DEFAULT []
  ASSERT ($this.kind = 'capability' AND array::len($value) >= 1) OR ($this.kind != 'capability' AND array::len($value) = 0);
DEFINE FIELD OVERWRITE a.* ON card TYPE string ASSERT string::len($value) <= 300;
DEFINE FIELD OVERWRITE mech ON card TYPE option<string> READONLY
  ASSERT $this.kind = 'capability' AND string::len($value) <= 300;
-- The problem of this project a capability got past (the C of Swanson's ABC chain).
DEFINE FIELD OVERWRITE solves ON card TYPE option<record<card>> READONLY
  ASSERT $before != NONE OR $value = NONE OR ($this.kind = 'capability' AND $value.kind = 'problem');

-- The quote: a unit's lines, or words of a fact. Exactly one of unit and fact.
DEFINE FIELD OVERWRITE unit ON card TYPE option<record<unit>> READONLY;
DEFINE FIELD OVERWRITE fact ON card TYPE option<record<fact>> READONLY;
DEFINE FIELD OVERWRITE lines ON card TYPE option<array<int>> READONLY;
DEFINE FIELD OVERWRITE quote ON card TYPE string READONLY
  ASSERT string::len(string::trim($value)) > 0
    AND ($this.fact = NONE) != ($this.unit = NONE)
    AND ($this.lines = NONE) = ($this.unit = NONE);
DEFINE FIELD OVERWRITE quote_hash ON card TYPE string READONLY;
-- What the quote is, for the grade rule: the unit's kind, or for a journal quote unit (kind `quote`) the kind
-- of the file it copies, or `fact`. The program sets it; for any unit but a quote unit it must be the unit's
-- own kind, so it cannot be used to lift a memory note.
DEFINE FIELD OVERWRITE source_kind ON card TYPE string READONLY
  ASSERT $before != NONE OR ($this.fact != NONE AND $value = 'fact')
    OR ($this.fact = NONE AND ($this.unit.kind = 'quote' OR $value = $this.unit.kind));
-- The card's own hash over its facets and quote hash: a bridge holds a source card as project/key/hash.
DEFINE FIELD OVERWRITE hash ON card TYPE string READONLY ASSERT $value = /^[0-9a-f]{64}$/;

-- S0's rule on cards: text from memory, instructions, agent, skill or command files is an agent's
-- recollection or a prompt, never CONFIRMED; a card from a fact is CONFIRMED only when the fact is.
DEFINE FIELD OVERWRITE grade ON card TYPE string READONLY DEFAULT 'INFERRED'
  ASSERT $value IN ['CONFIRMED', 'INFERRED', 'UNKNOWN']
    AND ($value != 'CONFIRMED' OR $before != NONE
      OR ($this.fact != NONE AND $this.fact.grade = 'CONFIRMED')
      OR ($this.unit != NONE AND $this.source_kind NOT IN ['memory', 'instructions', 'agent', 'skill', 'command', 'quote', 'fact']));
DEFINE FIELD OVERWRITE status ON card TYPE string DEFAULT 'live' ASSERT $value IN ['live', 'stale'];
DEFINE FIELD OVERWRITE stale_since ON card TYPE option<datetime>;
DEFINE FIELD OVERWRITE at ON card TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE by ON card TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE machine ON card TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE written_by ON card TYPE option<record<member>> DEFAULT $auth.id READONLY
  ASSERT $auth = NONE OR $value = $auth.id;

DEFINE INDEX OVERWRITE card_unit ON card FIELDS unit;
DEFINE INDEX OVERWRITE card_fact ON card FIELDS fact;
DEFINE INDEX OVERWRITE card_kind ON card FIELDS kind, status;
DEFINE INDEX OVERWRITE card_x ON card FIELDS x FULLTEXT ANALYZER gw_words BM25;
DEFINE INDEX OVERWRITE card_y ON card FIELDS y FULLTEXT ANALYZER gw_words BM25;
DEFINE INDEX OVERWRITE card_a ON card FIELDS a FULLTEXT ANALYZER gw_words BM25;
DEFINE INDEX OVERWRITE card_mech ON card FIELDS mech FULLTEXT ANALYZER gw_words BM25;

-- R1 for cards: a quoted unit whose text changes or leaves, or a quoted fact that stops being live, marks every
-- live card on it stale. Never deletes.
DEFINE EVENT OVERWRITE stale_cards_on_unit_change ON TABLE unit
  WHEN $event = 'UPDATE' AND ($before.hash != $after.hash OR ($before.present = true AND $after.present = false))
  THEN { UPDATE card SET status = 'stale', stale_since = time::now() WHERE status = 'live' AND unit = $after.id; };
DEFINE EVENT OVERWRITE stale_cards_on_fact_change ON TABLE fact
  WHEN $event = 'UPDATE' AND $before.status = 'live' AND $after.status != 'live'
  THEN { UPDATE card SET status = 'stale', stale_since = time::now() WHERE status = 'live' AND fact = $after.id; };
