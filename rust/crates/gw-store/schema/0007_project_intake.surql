-- scope: project
-- Ingestion (2026-10-25): a project's files and commits become citable units, organised by kind (context,
-- doc, code, config, commit, quote) and by the experts whose area covers them, and searchable in full text.
-- A unit's id comes from its locator, so a re-intake updates the same unit, and a changed hash marks every
-- fact citing it stale (0004 R1). A file that vanished keeps its units, marked not present.
DEFINE FIELD OVERWRITE path ON unit TYPE option<string>;
DEFINE FIELD OVERWRITE line_start ON unit TYPE option<int>;
DEFINE FIELD OVERWRITE line_end ON unit TYPE option<int>;
DEFINE FIELD OVERWRITE commit ON unit TYPE option<string>;
DEFINE FIELD OVERWRITE experts ON unit TYPE array<string> DEFAULT [];
DEFINE FIELD OVERWRITE present ON unit TYPE bool DEFAULT true;
-- The full-text index that uses this analyzer is in 0008: on rocksdb an index built over existing rows
-- cannot see an analyzer defined in the same transaction (measured 2026-10-25 on the live store).
DEFINE ANALYZER OVERWRITE gw_words TOKENIZERS class FILTERS lowercase, snowball(english);
DEFINE INDEX OVERWRITE unit_path ON unit FIELDS path;
DEFINE INDEX OVERWRITE unit_kind ON unit FIELDS kind;

-- One row per intake: what was read, at which commit, on which machine. Written once.
DEFINE TABLE IF NOT EXISTS intake_run TYPE NORMAL SCHEMAFULL;
ALTER TABLE intake_run PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE at ON intake_run TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE commit ON intake_run TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE dirty ON intake_run TYPE option<bool> READONLY;
DEFINE FIELD OVERWRITE machine ON intake_run TYPE string READONLY;
DEFINE FIELD OVERWRITE files ON intake_run TYPE int READONLY;
DEFINE FIELD OVERWRITE units ON intake_run TYPE int READONLY;
DEFINE FIELD OVERWRITE written ON intake_run TYPE int READONLY;
DEFINE FIELD OVERWRITE gone ON intake_run TYPE int READONLY;
DEFINE FIELD OVERWRITE skipped ON intake_run TYPE int READONLY;
