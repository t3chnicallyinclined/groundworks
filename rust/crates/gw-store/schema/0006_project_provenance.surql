-- scope: project
-- Provenance the store records itself (2026-10-25). `written_by` is the login that wrote a fact, set by the
-- store, never by the writer: an owner statement typed by an agent stays visibly an agent's report.
-- `hash_of` says what a receipt's output_hash covers, so nobody reads more into it than it proves.
DEFINE FIELD OVERWRITE written_by ON fact TYPE option<record<member>> DEFAULT $auth.id READONLY
  ASSERT $auth = NONE OR $value = $auth.id;
DEFINE FIELD OVERWRITE hash_of ON receipt TYPE string DEFAULT 'unspecified' READONLY;
