-- scope: workspace
-- The registry and cross-project memory of one workspace: what exists and where, what was decided,
-- what was tried and failed, and what it cost. Namespace `workspace`, database = model.DB_NAME.
--
-- 3.1.6 trap (xscholar/gates.py table_definition): DEFINE TABLE OVERWRITE resets PERMISSIONS and drops
-- CHANGEFEED. Tables are DEFINE ... IF NOT EXISTS and their permissions are set with ALTER TABLE.
-- Fields and events use OVERWRITE: redefining a field does not touch the table's permissions.

-- ---- credentials --------------------------------------------------------------------------------------
-- A member signs in through DEFINE ACCESS ... TYPE RECORD; $auth is its row. Measured on 3.1.6: with
-- PERMISSIONS NONE on this table, $auth.role reads NONE inside every ASSERT, so a member may SELECT its
-- own row (and nothing else). Nobody but root writes it: a member cannot change its own role.
DEFINE TABLE IF NOT EXISTS member TYPE NORMAL SCHEMAFULL;
ALTER TABLE member PERMISSIONS FOR select WHERE id = $auth.id FOR create, update, delete NONE;
DEFINE FIELD OVERWRITE role ON member TYPE string ASSERT $value IN ['lead', 'worker', 'reviewer'];
DEFINE FIELD OVERWRITE pass ON member TYPE string PERMISSIONS FOR select NONE;
DEFINE FIELD OVERWRITE enabled ON member TYPE bool DEFAULT true;
DEFINE ACCESS IF NOT EXISTS member ON DATABASE TYPE RECORD
  SIGNIN (SELECT * FROM member WHERE id = type::record('member', $id) AND enabled = true AND crypto::argon2::compare(pass, $pass))
  DURATION FOR TOKEN 8h, FOR SESSION 8h;

-- ---- registry: lead writes, every role reads ------------------------------------------------------------
DEFINE TABLE IF NOT EXISTS project TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE slug ON project TYPE string ASSERT $value = /^[a-z0-9_]{1,40}$/;
DEFINE FIELD OVERWRITE name ON project TYPE string;
DEFINE FIELD OVERWRITE root ON project TYPE string;
DEFINE FIELD OVERWRITE namespace ON project TYPE string VALUE 'proj_' + slug;
DEFINE FIELD OVERWRITE active ON project TYPE bool DEFAULT true;
DEFINE INDEX OVERWRITE project_slug ON project FIELDS slug UNIQUE;

DEFINE TABLE IF NOT EXISTS repo TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE project ON repo TYPE record<project>;
DEFINE FIELD OVERWRITE path ON repo TYPE string;
DEFINE FIELD OVERWRITE remote ON repo TYPE option<string>;
DEFINE FIELD OVERWRITE seen_at_commit ON repo TYPE option<string>;
DEFINE INDEX OVERWRITE repo_path ON repo FIELDS path UNIQUE;

DEFINE TABLE IF NOT EXISTS worktree TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE repo ON worktree TYPE record<repo>;
DEFINE FIELD OVERWRITE path ON worktree TYPE string;
DEFINE FIELD OVERWRITE branch ON worktree TYPE option<string>;
DEFINE INDEX OVERWRITE worktree_path ON worktree FIELDS path UNIQUE;

DEFINE TABLE IF NOT EXISTS branch TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE repo ON branch TYPE record<repo>;
DEFINE FIELD OVERWRITE name ON branch TYPE string;
DEFINE FIELD OVERWRITE head ON branch TYPE option<string>;
DEFINE INDEX OVERWRITE branch_name ON branch FIELDS repo, name UNIQUE;

DEFINE TABLE IF NOT EXISTS service TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE name ON service TYPE string;
DEFINE FIELD OVERWRITE kind ON service TYPE string;
DEFINE FIELD OVERWRITE host ON service TYPE option<string>;
DEFINE FIELD OVERWRITE project ON service TYPE option<record<project>>;

DEFINE TABLE IF NOT EXISTS gate_def TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE project ON gate_def TYPE record<project>;
DEFINE FIELD OVERWRITE command ON gate_def TYPE string;
DEFINE FIELD OVERWRITE confirmed ON gate_def TYPE bool DEFAULT false;

DEFINE TABLE IF NOT EXISTS area TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE project ON area TYPE record<project>;
DEFINE FIELD OVERWRITE name ON area TYPE string;
DEFINE FIELD OVERWRITE paths ON area TYPE array<string> DEFAULT [];
DEFINE FIELD OVERWRITE symbols ON area TYPE array<string> DEFAULT [];

DEFINE TABLE IF NOT EXISTS expert TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE name ON expert TYPE string;
DEFINE FIELD OVERWRITE areas ON expert TYPE array<record<area>> DEFAULT [];
DEFINE FIELD OVERWRITE file ON expert TYPE option<string>;

DEFINE TABLE IF NOT EXISTS decision TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE title ON decision TYPE string;
DEFINE FIELD OVERWRITE why ON decision TYPE string;
DEFINE FIELD OVERWRITE project ON decision TYPE option<record<project>>;
DEFINE FIELD OVERWRITE decided_at ON decision TYPE datetime DEFAULT time::now();

ALTER TABLE project PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR create, update WHERE $auth.role = 'lead' FOR delete NONE;
ALTER TABLE repo PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR create, update WHERE $auth.role = 'lead' FOR delete NONE;
ALTER TABLE worktree PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR create, update WHERE $auth.role = 'lead' FOR delete NONE;
ALTER TABLE branch PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR create, update WHERE $auth.role = 'lead' FOR delete NONE;
ALTER TABLE service PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR create, update WHERE $auth.role = 'lead' FOR delete NONE;
ALTER TABLE gate_def PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR create, update WHERE $auth.role = 'lead' FOR delete NONE;
ALTER TABLE area PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR create, update WHERE $auth.role = 'lead' FOR delete NONE;
ALTER TABLE expert PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR create, update WHERE $auth.role = 'lead' FOR delete NONE;
ALTER TABLE decision PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR create, update WHERE $auth.role = 'lead' FOR delete NONE;

-- ---- proposals any role may file; only the lead accepts ------------------------------------------------
-- Cross-project dead ends (the project-local ones live in proj_<slug>).
DEFINE TABLE IF NOT EXISTS dead_end TYPE NORMAL SCHEMAFULL;
ALTER TABLE dead_end PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer']
  FOR create WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR update WHERE $auth.role = 'lead' FOR delete NONE;
DEFINE FIELD OVERWRITE title ON dead_end TYPE string;
DEFINE FIELD OVERWRITE why ON dead_end TYPE string;
DEFINE FIELD OVERWRITE projects ON dead_end TYPE array<string> DEFAULT [];
DEFINE FIELD OVERWRITE source ON dead_end TYPE option<string>;
DEFINE FIELD OVERWRITE status ON dead_end TYPE string DEFAULT 'proposed'
  ASSERT $value IN ['proposed', 'accepted', 'rejected'] AND ($value = 'proposed' OR $auth = NONE OR $auth.role = 'lead');

DEFINE TABLE IF NOT EXISTS cost_row TYPE NORMAL SCHEMAFULL;
ALTER TABLE cost_row PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer']
  FOR create WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE at ON cost_row TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE project ON cost_row TYPE option<string>;
DEFINE FIELD OVERWRITE ticket ON cost_row TYPE option<string>;
DEFINE FIELD OVERWRITE model ON cost_row TYPE string;
DEFINE FIELD OVERWRITE input_tokens ON cost_row TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE output_tokens ON cost_row TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE usd ON cost_row TYPE float DEFAULT 0.0;
