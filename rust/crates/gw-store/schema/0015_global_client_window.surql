-- scope: global
-- The Groundworks window (2026-10-29): sessions it runs name themselves `gw-window` (GROUNDWORKS_CLIENT in
-- the window's config dir), so the pulse can tell them from VS Code and the CLI. Before this a window
-- session was recorded as `other`. Same field, same rules as 0014, one more name on the fixed list.
DEFINE FIELD OVERWRITE client ON session TYPE string READONLY
  ASSERT $value IN ['claude-code', 'copilot', 'vscode', 'cli', 'gw-window', 'other'];

-- Stop no longer counts a tool call the PreToolUse hook denied as work (it changed nothing): the count
-- of such calls is kept beside the others, and the Stop hook finds a session's deny rows by its id.
DEFINE FIELD OVERWRITE tool_uses.denied ON hook_event TYPE int DEFAULT 0;
DEFINE INDEX OVERWRITE hook_event_session ON hook_event FIELDS session, hook;
