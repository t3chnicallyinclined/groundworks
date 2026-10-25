-- scope: global
-- The hub: what exists and where, each scope's isolation policy, the walls between scopes, and the
-- knowledge shared across every project. Namespace `global`.
-- Harvested from the 2026-09-17 product data model (0001_workspace_registry.surql), minus tickets, with
-- scope policy and walls added (owner decision D11, 2026-10-24).
--
-- 3.1.6 trap: DEFINE TABLE OVERWRITE resets PERMISSIONS and drops CHANGEFEED. Tables are
-- DEFINE ... IF NOT EXISTS and their permissions are set with ALTER TABLE; fields use OVERWRITE.
-- Roles: `owner` (the human) and `agent`. Root has no member row ($auth = NONE): it is the migration and
-- loader identity, and never the identity an agent works under.

-- ---- credentials --------------------------------------------------------------------------------------
-- Measured on 3.1.6: with PERMISSIONS NONE here, $auth.role reads NONE inside every ASSERT, so a member may
-- SELECT its own row and nothing else. Nobody but root writes it: a member cannot change its own role.
DEFINE TABLE IF NOT EXISTS member TYPE NORMAL SCHEMAFULL;
ALTER TABLE member PERMISSIONS FOR select WHERE id = $auth.id FOR create, update, delete NONE;
DEFINE FIELD OVERWRITE role ON member TYPE string ASSERT $value IN ['owner', 'agent'];
DEFINE FIELD OVERWRITE pass ON member TYPE string PERMISSIONS FOR select NONE;
DEFINE FIELD OVERWRITE enabled ON member TYPE bool DEFAULT true;
DEFINE ACCESS IF NOT EXISTS member ON DATABASE TYPE RECORD
  SIGNIN (SELECT * FROM member WHERE id = type::record('member', $id) AND enabled = true AND crypto::argon2::compare(pass, $pass))
  DURATION FOR TOKEN 8h, FOR SESSION 8h;

-- ---- scope policy: the owner's alone -------------------------------------------------------------------
-- One row per scope: `global`, `ws_<name>`, `proj_<slug>`. level: shared (reads up, shares up on purpose),
-- private (reads up, never shares up, nothing else reads it), isolated (nothing in, nothing out, own store).
DEFINE TABLE IF NOT EXISTS scope_policy TYPE NORMAL SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL;
ALTER TABLE scope_policy PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create, update WHERE $auth.role = 'owner' FOR delete NONE;
DEFINE FIELD OVERWRITE name ON scope_policy TYPE string ASSERT $value = /^(global|ws_[a-z0-9_]{1,40}|proj_[a-z0-9_]{1,40})$/;
DEFINE FIELD OVERWRITE kind ON scope_policy TYPE string ASSERT $value IN ['global', 'workspace', 'project'];
DEFINE FIELD OVERWRITE level ON scope_policy TYPE string DEFAULT 'shared' ASSERT $value IN ['shared', 'private', 'isolated'];
DEFINE FIELD OVERWRITE workspace ON scope_policy TYPE option<string> ASSERT $value = NONE OR $value = /^ws_[a-z0-9_]{1,40}$/;
DEFINE FIELD OVERWRITE changed_at ON scope_policy TYPE datetime VALUE time::now();
DEFINE INDEX OVERWRITE scope_policy_name ON scope_policy FIELDS name UNIQUE;

-- A wall: two scopes that must never meet (no share across it, no search combining them, no session
-- holding both). Stored once per pair, a < b, so a wall cannot be recorded twice in two orders.
DEFINE TABLE IF NOT EXISTS wall TYPE NORMAL SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL;
ALTER TABLE wall PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create, update WHERE $auth.role = 'owner' FOR delete NONE;
DEFINE FIELD OVERWRITE a ON wall TYPE string;
DEFINE FIELD OVERWRITE b ON wall TYPE string ASSERT $value > $this.a;
DEFINE FIELD OVERWRITE why ON wall TYPE string;
DEFINE INDEX OVERWRITE wall_pair ON wall FIELDS a, b UNIQUE;

-- ---- registry: agents keep it current, everyone reads --------------------------------------------------
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

DEFINE TABLE IF NOT EXISTS service TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE name ON service TYPE string;
DEFINE FIELD OVERWRITE kind ON service TYPE string;
DEFINE FIELD OVERWRITE host ON service TYPE option<string>;
DEFINE FIELD OVERWRITE project ON service TYPE option<record<project>>;

-- A gate command becomes `confirmed` only when the owner says so (bootstrap: ask before running anything).
DEFINE TABLE IF NOT EXISTS gate_def TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE project ON gate_def TYPE record<project>;
DEFINE FIELD OVERWRITE command ON gate_def TYPE string;
DEFINE FIELD OVERWRITE confirmed ON gate_def TYPE bool DEFAULT false
  ASSERT $value = false OR $auth = NONE OR $auth.role = 'owner';

DEFINE TABLE IF NOT EXISTS expert TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE name ON expert TYPE string;
DEFINE FIELD OVERWRITE summary ON expert TYPE string;
DEFINE FIELD OVERWRITE file ON expert TYPE option<string>;
DEFINE INDEX OVERWRITE expert_name ON expert FIELDS name UNIQUE;

DEFINE TABLE IF NOT EXISTS decision TYPE NORMAL SCHEMAFULL;
DEFINE FIELD OVERWRITE title ON decision TYPE string;
DEFINE FIELD OVERWRITE why ON decision TYPE string;
DEFINE FIELD OVERWRITE project ON decision TYPE option<record<project>>;
DEFINE FIELD OVERWRITE decided_at ON decision TYPE datetime DEFAULT time::now();

ALTER TABLE project PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent'] FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
ALTER TABLE repo PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent'] FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
ALTER TABLE worktree PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent'] FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
ALTER TABLE service PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent'] FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
ALTER TABLE gate_def PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent'] FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
ALTER TABLE expert PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent'] FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
ALTER TABLE decision PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent'] FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;

-- ---- dead ends shared across projects: any member proposes, only the owner accepts or rejects ---------
DEFINE TABLE IF NOT EXISTS dead_end TYPE NORMAL SCHEMAFULL;
ALTER TABLE dead_end PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
DEFINE FIELD OVERWRITE title ON dead_end TYPE string;
DEFINE FIELD OVERWRITE why ON dead_end TYPE string;
DEFINE FIELD OVERWRITE from_scope ON dead_end TYPE string;
DEFINE FIELD OVERWRITE source ON dead_end TYPE option<string>;
DEFINE FIELD OVERWRITE status ON dead_end TYPE string DEFAULT 'proposed'
  ASSERT $value IN ['proposed', 'accepted', 'rejected'] AND ($value = 'proposed' OR $auth = NONE OR $auth.role = 'owner');
