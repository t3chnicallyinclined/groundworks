-- scope: global
-- Hook telemetry (2026-10-29, decision 1itizcerhaj5x1mjfyj2): did a session read the journal, did it write,
-- what did the hooks decide, and what did the store refuse. Counts, ids, timings and rule ids only: no command
-- text, no tool input, no file contents, no transcript text. A command is identified, when at all, by an HMAC
-- keyed per home (<home>/secrets/hook_hmac.key), never by a bare hash. Every table is append-only for members:
-- never updated, never deleted (a session's end is the Stop row that approved it in hook_event).
-- 3.1.6 trap (0001): tables are IF NOT EXISTS with ALTER TABLE for permissions; fields use OVERWRITE.

-- One row per Claude Code session that started under a registered project. Id = the client's session id.
DEFINE TABLE IF NOT EXISTS session TYPE NORMAL SCHEMAFULL;
ALTER TABLE session PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE project ON session TYPE string READONLY;
DEFINE FIELD OVERWRITE machine ON session TYPE string READONLY;
DEFINE FIELD OVERWRITE client ON session TYPE string READONLY;
DEFINE FIELD OVERWRITE agent ON session TYPE string DEFAULT 'main' READONLY;
DEFINE FIELD OVERWRITE started_at ON session TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE cwd ON session TYPE string READONLY;
DEFINE FIELD OVERWRITE injected_entries ON session TYPE int DEFAULT 0 READONLY;
DEFINE FIELD OVERWRITE store_ok ON session TYPE bool DEFAULT true READONLY;
DEFINE FIELD OVERWRITE hook_latency_ms ON session TYPE int DEFAULT 0 READONLY;
DEFINE INDEX OVERWRITE session_project_started ON session FIELDS project, started_at;

-- One row per hook run. `decision` is what the hook answered; `rule` names why (a deny.toml id, 'no-writes',
-- 'store-down', 'not-registered'); `tool_uses` are counts of tool_use blocks in the transcript since the
-- session started; `error` is the hook's own failure, redacted and cut at 200 characters.
DEFINE TABLE IF NOT EXISTS hook_event TYPE NORMAL SCHEMAFULL;
ALTER TABLE hook_event PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE session ON hook_event TYPE string READONLY;
DEFINE FIELD OVERWRITE project ON hook_event TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE agent ON hook_event TYPE string DEFAULT 'main' READONLY;
DEFINE FIELD OVERWRITE hook ON hook_event TYPE string READONLY
  ASSERT $value IN ['SessionStart', 'PreToolUse', 'Stop', 'SubagentStop'];
DEFINE FIELD OVERWRITE at ON hook_event TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE decision ON hook_event TYPE string READONLY
  ASSERT $value IN ['allow', 'deny', 'warn', 'approve', 'block', 'noop'];
DEFINE FIELD OVERWRITE rule ON hook_event TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE tool_uses ON hook_event TYPE object DEFAULT {} READONLY;
DEFINE FIELD OVERWRITE tool_uses.edit ON hook_event TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE tool_uses.write ON hook_event TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE tool_uses.bash ON hook_event TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE tool_uses.run ON hook_event TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE tool_uses.mcp_write ON hook_event TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE journal_writes_since_start ON hook_event TYPE int DEFAULT 0 READONLY;
DEFINE FIELD OVERWRITE latency_ms ON hook_event TYPE int DEFAULT 0 READONLY;
DEFINE FIELD OVERWRITE error ON hook_event TYPE option<string> READONLY
  ASSERT $value = NONE OR string::len($value) <= 200;
DEFINE FIELD OVERWRITE command_hmac ON hook_event TYPE option<string> READONLY
  ASSERT $value = NONE OR $value = /^[0-9a-f]{64}$/;
DEFINE INDEX OVERWRITE hook_event_at ON hook_event FIELDS at;
DEFINE INDEX OVERWRITE hook_event_session ON hook_event FIELDS session;

-- A write the store refused (CONFIRMED without evidence, a made-up quote, an isolated project, ...), by surface.
DEFINE TABLE IF NOT EXISTS rejection TYPE NORMAL SCHEMAFULL;
ALTER TABLE rejection PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE at ON rejection TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE surface ON rejection TYPE string READONLY;
DEFINE FIELD OVERWRITE project ON rejection TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE by ON rejection TYPE option<string> READONLY;
DEFINE FIELD OVERWRITE reason ON rejection TYPE string READONLY ASSERT string::len($value) <= 400;
DEFINE INDEX OVERWRITE rejection_at ON rejection FIELDS at;
