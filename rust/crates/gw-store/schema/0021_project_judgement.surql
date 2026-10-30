-- scope: project
-- The judgement cache (2026-10-30, idea-bridges design, amendment A6.3, slice 14): the gate funnel's record of a
-- check an agent answered about one pair of cards, so the pair is not asked again until either quote changes.
-- Its id is the cache key, sha256 over the two quote hashes (sorted), the check and the prompt version, plus the
-- round (`f` first look, `s` second look), so a changed quote is a different key and is asked again. It holds
-- hashes and card keys only, never quoted text (gw-security: a cache must not carry another project's text).
-- Written once, never updated or deleted. A judgement is an agent's reading and never above INFERRED: a cache hit
-- can never raise a grade; it still has to be written onto the bridge row, where 0020's gate checks it.
DEFINE TABLE IF NOT EXISTS judgement TYPE NORMAL SCHEMAFULL;
ALTER TABLE judgement PERMISSIONS FOR select, create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE check ON judgement TYPE string READONLY ASSERT string::len($value) > 0;
DEFINE FIELD OVERWRITE prompt_version ON judgement TYPE string READONLY;
DEFINE FIELD OVERWRITE quote_hash_a ON judgement TYPE string READONLY ASSERT $value = /^[0-9a-f]{64}$/;
DEFINE FIELD OVERWRITE quote_hash_b ON judgement TYPE string READONLY
  ASSERT $value = /^[0-9a-f]{64}$/ AND $value >= $this.quote_hash_a;
DEFINE FIELD OVERWRITE round ON judgement TYPE string READONLY ASSERT $value IN ['first', 'second'];
DEFINE FIELD OVERWRITE verdict ON judgement TYPE string READONLY
  ASSERT $value IN ['holds', 'fails', 'unknown', 'made', 'not_made'];
DEFINE FIELD OVERWRITE confidence ON judgement TYPE float READONLY ASSERT $value >= 0 AND $value <= 1;
DEFINE FIELD OVERWRITE borderline ON judgement TYPE bool READONLY;
DEFINE FIELD OVERWRITE why ON judgement TYPE string READONLY ASSERT string::len($value) <= 2000;
DEFINE FIELD OVERWRITE grade ON judgement TYPE string READONLY
  ASSERT $value IN ['INFERRED', 'UNKNOWN'] AND ($this.verdict != 'unknown' OR $value = 'UNKNOWN');
DEFINE FIELD OVERWRITE target_card ON judgement TYPE string READONLY;
DEFINE FIELD OVERWRITE source_project ON judgement TYPE string READONLY;
DEFINE FIELD OVERWRITE source_card ON judgement TYPE string READONLY;
DEFINE FIELD OVERWRITE at ON judgement TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE by ON judgement TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE written_by ON judgement TYPE option<record<member>> DEFAULT $auth.id READONLY
  ASSERT $before != NONE OR $auth = NONE OR $value = $auth.id;
