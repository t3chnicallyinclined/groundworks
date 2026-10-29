-- scope: global
-- Hook telemetry (2026-10-29, decision 1itizcerhaj5x1mjfyj2; shape per team/security TELEMETRY-POLICY.md):
-- did a session read the journal, did it write, what did the hooks decide, and what did the store refuse.
-- Counts, codes, ids and timestamps ONLY. No command text, no path (not cwd, not transcript_path), no tool
-- input, no error text, no transcript text: every agent on this machine can read these rows and the nightly
-- export carries them. Where a field could only be filled by copying text an agent or a shell produced, it
-- is a code from the fixed list in its ASSERT, so the gate lives in the datastore and can be probed both ways.
-- Every table is append-only for members: never updated, never deleted (a session's end is the Stop row that
-- approved it in hook_event). Root prunes by age in the supervisor (hook_event and session 90 days,
-- rejection 365).
-- 3.1.6 trap (0001): tables are IF NOT EXISTS with ALTER TABLE for permissions; fields use OVERWRITE.

-- One row per Claude Code session the hook saw, registered project or not. Id = the client's session id.
-- `cwd_class` says where it started without saying the path: a `local-only` folder (scan.local_only) is
-- recorded as that class and nothing else.
DEFINE TABLE IF NOT EXISTS session TYPE NORMAL SCHEMAFULL;
ALTER TABLE session PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE project ON session TYPE option<string> READONLY
  ASSERT $value = NONE OR $value = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
DEFINE FIELD OVERWRITE machine ON session TYPE string READONLY ASSERT string::len($value) <= 80;
DEFINE FIELD OVERWRITE client ON session TYPE string READONLY
  ASSERT $value IN ['claude-code', 'copilot', 'vscode', 'cli', 'other'];
DEFINE FIELD OVERWRITE agent ON session TYPE string DEFAULT 'main' READONLY
  ASSERT string::len($value) <= 40 AND $value = /^[A-Za-z0-9 _-]+$/;
DEFINE FIELD OVERWRITE started_at ON session TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE cwd_class ON session TYPE string READONLY
  ASSERT $value IN ['registered', 'workspace-unregistered', 'outside-workspace', 'local-only', 'home'];
DEFINE FIELD OVERWRITE injected_entries ON session TYPE int DEFAULT 0 READONLY;
DEFINE FIELD OVERWRITE store_ok ON session TYPE bool DEFAULT true READONLY;
DEFINE FIELD OVERWRITE hook_latency_ms ON session TYPE int DEFAULT 0 READONLY;
DEFINE INDEX OVERWRITE session_project_started ON session FIELDS project, started_at;

-- One row per hook run. `decision` is what the hook answered; `rule_id` names why (a deny.toml id,
-- 'no-writes', 'store-down', 'not-registered', 'rules-unloadable', 'hook-selfedit-file'); `tool_uses` are
-- counts of tool_use blocks by name in the transcript since the session started; `error_code` is the
-- hook's own failure as a code, with the child's exit status and the length of the text NOT stored.
DEFINE TABLE IF NOT EXISTS hook_event TYPE NORMAL SCHEMAFULL;
ALTER TABLE hook_event PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE session ON hook_event TYPE string READONLY ASSERT string::len($value) <= 80;
DEFINE FIELD OVERWRITE project ON hook_event TYPE option<string> READONLY
  ASSERT $value = NONE OR $value = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
DEFINE FIELD OVERWRITE agent ON hook_event TYPE string DEFAULT 'main' READONLY
  ASSERT string::len($value) <= 40 AND $value = /^[A-Za-z0-9 _-]+$/;
DEFINE FIELD OVERWRITE hook ON hook_event TYPE string READONLY
  ASSERT $value IN ['SessionStart', 'PreToolUse', 'Stop', 'SubagentStop'];
DEFINE FIELD OVERWRITE at ON hook_event TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE decision ON hook_event TYPE string READONLY
  ASSERT $value IN ['allow', 'warn', 'deny', 'block', 'approve', 'exempt', 'error', 'noop'];
DEFINE FIELD OVERWRITE rule_id ON hook_event TYPE option<string> READONLY
  ASSERT $value = NONE OR $value = /^[a-z0-9-]{1,60}$/;
DEFINE FIELD OVERWRITE tool_uses ON hook_event TYPE object DEFAULT {} READONLY;
DEFINE FIELD OVERWRITE tool_uses.edit ON hook_event TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE tool_uses.write ON hook_event TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE tool_uses.bash ON hook_event TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE tool_uses.run ON hook_event TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE tool_uses.mcp_write ON hook_event TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE journal_writes_since_start ON hook_event TYPE int DEFAULT 0 READONLY;
DEFINE FIELD OVERWRITE latency_ms ON hook_event TYPE int DEFAULT 0 READONLY;
DEFINE FIELD OVERWRITE error_code ON hook_event TYPE option<string> READONLY
  ASSERT $value = NONE OR $value IN ['store_down', 'binary_missing', 'timeout', 'bad_hook_input', 'regex_compile',
    'rules_file_missing', 'transcript_unreadable', 'show_failed', 'env_file_unwritable', 'refused_secret_shaped',
    'other'];
DEFINE FIELD OVERWRITE error_exit ON hook_event TYPE option<int> READONLY;
DEFINE FIELD OVERWRITE error_len ON hook_event TYPE option<int> READONLY;
DEFINE INDEX OVERWRITE hook_event_at ON hook_event FIELDS at;
DEFINE INDEX OVERWRITE hook_event_session ON hook_event FIELDS session;

-- A write the store refused, by surface. `reason` is a code: a JOURNAL-REFUSALS pattern id (pem-private-key,
-- jwt, kv-secret, ...) or a store rule (confirmed-needs-evidence, quote-not-found, isolated-project); never the
-- refused text. `field`, `offset` and `match_len` locate a refused span for its writer and reveal nothing.
DEFINE TABLE IF NOT EXISTS rejection TYPE NORMAL SCHEMAFULL;
ALTER TABLE rejection PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE at ON rejection TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE surface ON rejection TYPE string READONLY
  ASSERT $value IN ['journal_add', 'note', 'import_journals', 'ask_record', 'hook_event.error', 'panel_submit'];
DEFINE FIELD OVERWRITE project ON rejection TYPE option<string> READONLY
  ASSERT $value = NONE OR $value = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
DEFINE FIELD OVERWRITE by ON rejection TYPE option<string> READONLY
  ASSERT $value = NONE OR (string::len($value) <= 40 AND $value = /^[A-Za-z0-9 _-]+$/);
DEFINE FIELD OVERWRITE reason ON rejection TYPE string READONLY ASSERT $value = /^[a-z0-9-]{1,40}$/;
DEFINE FIELD OVERWRITE field ON rejection TYPE option<string> READONLY
  ASSERT $value = NONE OR $value IN ['text', 'source', 'owner_statement', 'quote', 'by', 'lens'];
DEFINE FIELD OVERWRITE offset ON rejection TYPE option<int> READONLY;
DEFINE FIELD OVERWRITE match_len ON rejection TYPE option<int> READONLY;
DEFINE INDEX OVERWRITE rejection_at ON rejection FIELDS at;
