-- scope: global
-- `ask` (2026-10-25, ported from the Python engine's ask): a question, the units retrieved for it, and the
-- checked answer. A packet holds locators and hashes only, never unit text, so it can sit in global even when
-- a private project was asked by name. The candidates are fixed when the packet opens; only the verdict, the
-- answer and its report are written later, once.
DEFINE TABLE IF NOT EXISTS ask_packet TYPE NORMAL SCHEMAFULL;
ALTER TABLE ask_packet PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
DEFINE FIELD OVERWRITE question ON ask_packet TYPE string READONLY;
DEFINE FIELD OVERWRITE at ON ask_packet TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE by ON ask_packet TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE searched ON ask_packet TYPE array<string> READONLY;
DEFINE FIELD OVERWRITE skipped ON ask_packet TYPE array<string> READONLY;
DEFINE FIELD OVERWRITE corpus_total ON ask_packet TYPE int READONLY;
DEFINE FIELD OVERWRITE candidates ON ask_packet TYPE array<object> READONLY;
DEFINE FIELD OVERWRITE candidates.*.n ON ask_packet TYPE int;
DEFINE FIELD OVERWRITE candidates.*.project ON ask_packet TYPE string;
DEFINE FIELD OVERWRITE candidates.*.key ON ask_packet TYPE string;
DEFINE FIELD OVERWRITE candidates.*.locator ON ask_packet TYPE string;
DEFINE FIELD OVERWRITE candidates.*.hash ON ask_packet TYPE string;
DEFINE FIELD OVERWRITE candidates.*.kind ON ask_packet TYPE option<string>;
DEFINE FIELD OVERWRITE candidates.*.score ON ask_packet TYPE option<number>;
DEFINE FIELD OVERWRITE status ON ask_packet TYPE string DEFAULT 'OPEN'
  ASSERT $value IN ['OPEN', 'NO_HITS', 'PASS', 'FAIL', 'NOT_EXECUTED'];
DEFINE FIELD OVERWRITE answer ON ask_packet TYPE option<object> FLEXIBLE;
DEFINE FIELD OVERWRITE report ON ask_packet TYPE option<object> FLEXIBLE;
