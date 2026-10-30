-- scope: project
-- R2, promotion decided by the database (2026-10-30; scholar-architect review, owner go). Replaces the R2 of
-- 0004 on the same REQUIRED field, so it re-runs on every write to the row. CONFIRMED needs one of:
--   * a receipt (a command that ran), as before;
--   * an owner statement on a row the OWNER's login wrote: `signed` is set by the store from the writer's
--     role and cannot be set otherwise, so an agent typing `owner_statement` no longer confirms anything,
--     and neither does the loader (root);
--   * an evidence link to a unit, outside the memory project (proj_memory: the owner's agent memory, every
--     unit of it recollection; it stays shared, owner 2026-10-30), whose locator is not the project's Claude
--     setup (CLAUDE.md, AGENTS.md and
--     their disabled copies, anything under .claude/), its auto-memory (~claude-memory/), or an agent report
--     (a handoff or workstream note). Those record what an agent was told, remembered or reported; quoting
--     one proves the line exists, not that it holds. Unit rows are member-writable, so the rule judges the
--     locator's path, the one thing a unit cannot be about without saying so.

-- Whether a unit locator may confirm a fact (the client's mirror is gw_ops::intake::CONFIRMING_KINDS).
DEFINE FUNCTION OVERWRITE fn::confirming_locator($locator: string) -> bool {
    LET $path = string::lowercase(string::replace(array::first(string::split($locator, '#L')), '\\', '/'));
    LET $name = array::last(string::split($path, '/'));
    RETURN !(
        session::ns() = 'proj_memory'
        OR string::starts_with($path, 'commit:')
        OR string::starts_with($path, '~claude-memory/')
        OR $name = 'claude.md'
        OR $name = 'agents.md'
        OR string::starts_with($name, 'claude.md.disabled')
        OR string::starts_with($path, '.claude/')
        OR string::contains($path, '/.claude/')
        OR string::contains($name, 'handoff')
        OR string::contains($name, 'workstream')
    );
} PERMISSIONS FULL;

-- Rows written under the old R2 first, while it still holds: each fact's `signed` from the login that wrote
-- it, then every CONFIRMED row the new rule would refuse is set INFERRED (an agent-typed owner statement, or
-- evidence that is only setup, memory or a report). Otherwise the new ASSERT, which re-runs on every write,
-- would refuse the next write to such a row, the stale event's included.
DEFINE FIELD OVERWRITE signed ON fact TYPE option<bool>;
DEFINE FIELD OVERWRITE regraded ON fact TYPE option<string>;
UPDATE fact SET signed = (written_by.role = 'owner') RETURN NONE;
UPDATE fact SET grade = 'INFERRED',
    regraded = '2026-10-30, schema 0017: was CONFIRMED by an owner statement an agent recorded, or only by setup, memory or agent-report evidence; only a receipt, the owner login or confirming evidence confirms now'
  WHERE grade = 'CONFIRMED'
    AND receipt = NONE
    AND !(owner_statement != NONE AND signed = true)
    AND count(->evidence->unit[WHERE fn::confirming_locator(locator)]) = 0
  RETURN NONE;

-- Who signed: true only when the writer's login has the owner role. Set at create, never changed.
DEFINE FIELD OVERWRITE signed ON fact TYPE bool DEFAULT ($auth.role = 'owner') READONLY
  ASSERT $value = false OR $auth.role = 'owner';

DEFINE FIELD OVERWRITE grade ON fact TYPE string DEFAULT 'UNKNOWN'
  ASSERT $value IN ['CONFIRMED', 'INFERRED', 'UNKNOWN']
    AND ($value != 'CONFIRMED'
      OR $this.receipt != NONE
      OR ($this.owner_statement != NONE AND ($this.signed = true OR $auth.role = 'owner'))
      OR count($this.id->evidence->unit[WHERE fn::confirming_locator(locator)]) >= 1);
