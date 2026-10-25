-- scope: project
-- Lenses and panels (2026-10-25, ported from the Python engine's lens.py and panel/panel.py).
--
-- A lens is an expert's view: a finding is a fact that names the lens it was made through, and another lens
-- may attack it (a fact that disputes or supports it). Nothing is deleted, so a disagreement stays on record.
-- An attack must name a fact that exists, and say which way it goes.
DEFINE FIELD OVERWRITE lens ON fact TYPE option<string>;
DEFINE FIELD OVERWRITE attacks ON fact TYPE option<record<fact>>
  ASSERT $value = NONE OR record::exists($value);
DEFINE FIELD OVERWRITE stance ON fact TYPE option<string>
  ASSERT $value = NONE OR $value IN ['disputes', 'supports'];
DEFINE INDEX OVERWRITE fact_lens ON fact FIELDS lens;
DEFINE INDEX OVERWRITE fact_attacks ON fact FIELDS attacks;

-- A panel: seats judge one question in two rounds (blind, then anonymized), and a program checks the
-- protocol before it tallies. The validated config is kept as the exact JSON it was locked with, and its
-- digest, both written once: a panel is locked when it is opened, before any answer exists.
DEFINE TABLE IF NOT EXISTS panel TYPE NORMAL SCHEMAFULL;
ALTER TABLE panel PERMISSIONS FOR select, create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
DEFINE FIELD OVERWRITE config ON panel TYPE string READONLY;
DEFINE FIELD OVERWRITE digest ON panel TYPE string READONLY;
DEFINE FIELD OVERWRITE at ON panel TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE by ON panel TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE status ON panel TYPE string DEFAULT 'round1'
  ASSERT $value IN ['round1', 'round2', 'consensus', 'split'];
-- JSON text written by the program: the anonymized round 1 (labels, order, round-1 hashes, the bundle), the
-- seats chosen for the agreement check, and the tally.
DEFINE FIELD OVERWRITE anon ON panel TYPE option<string>;
DEFINE FIELD OVERWRITE counterfactual ON panel TYPE option<string>;
DEFINE FIELD OVERWRITE tally ON panel TYPE option<string>;
DEFINE FIELD OVERWRITE verdict ON panel TYPE option<string>;

-- Every answer a seat submits, verbatim, written once. A newer answer for the same round and seat replaces it
-- for the checks; the older one stays (the Python submissions.jsonl).
DEFINE TABLE IF NOT EXISTS panel_answer TYPE NORMAL SCHEMAFULL;
ALTER TABLE panel_answer PERMISSIONS FOR select, create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE panel ON panel_answer TYPE record<panel> READONLY ASSERT record::exists($value);
DEFINE FIELD OVERWRITE round ON panel_answer TYPE string READONLY
  ASSERT $value IN ['round1', 'round2', 'counterfactual'];
DEFINE FIELD OVERWRITE seat ON panel_answer TYPE string READONLY;
DEFINE FIELD OVERWRITE text ON panel_answer TYPE string READONLY;
DEFINE FIELD OVERWRITE sha256 ON panel_answer TYPE string READONLY;
DEFINE FIELD OVERWRITE by ON panel_answer TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE at ON panel_answer TYPE datetime DEFAULT time::now() READONLY;
DEFINE INDEX OVERWRITE panel_answer_panel ON panel_answer FIELDS panel;

-- Each re-prompt the checks owed, once per version of the answer it concerns (the Python enforcement.jsonl).
DEFINE TABLE IF NOT EXISTS panel_owed TYPE NORMAL SCHEMAFULL;
ALTER TABLE panel_owed PERMISSIONS FOR select, create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE panel ON panel_owed TYPE record<panel> READONLY;
DEFINE FIELD OVERWRITE condition ON panel_owed TYPE string READONLY;
DEFINE FIELD OVERWRITE seat ON panel_owed TYPE string READONLY;
DEFINE FIELD OVERWRITE answer_sha256 ON panel_owed TYPE string READONLY;
DEFINE FIELD OVERWRITE detail ON panel_owed TYPE string READONLY;
DEFINE FIELD OVERWRITE at ON panel_owed TYPE datetime DEFAULT time::now() READONLY;
DEFINE INDEX OVERWRITE panel_owed_panel ON panel_owed FIELDS panel;
