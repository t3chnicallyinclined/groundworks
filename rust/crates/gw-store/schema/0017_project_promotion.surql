-- scope: project
-- R2, promotion decided by the database (2026-10-30; scholar-architect review, owner go). Replaces the R2 of
-- 0004 on the same REQUIRED field, so it re-runs on every write to the row. CONFIRMED needs one of:
--   * a receipt (a command that ran), as before;
--   * an owner statement on a row the OWNER's login wrote: `signed` is set by the store from the writer's
--     role and cannot be set otherwise, so an agent typing `owner_statement` no longer confirms anything,
--     and neither does the loader (root);
--   * an evidence link to a unit that may confirm a fact of this kind (`fn::confirming_locator`):
--       - never in the memory project (proj_memory: the owner's agent memory, every unit of it recollection;
--         it stays shared, owner 2026-10-30), and never a commit, the project's Claude setup (CLAUDE.md,
--         AGENTS.md and their disabled copies, anything under .claude/), its auto-memory (~claude-memory/),
--         or an agent report (a handoff or workstream note): those record what an agent was told, remembered
--         or reported, and quoting one proves the line exists, not that it holds;
--       - a tracked doc (.md, .txt, .rst, a README) confirms only a DECISION, and on supergun no doc
--         confirms anything: byte gates are its proof (owner statement, 2026-10-30);
--       - code and config confirm as before.
--     Unit rows are member-writable, so the rule judges the locator's path, the one thing a unit cannot be
--     about without saying so.

-- Whether a unit locator may confirm a fact of `kind` (the client's mirror is gw_ops::intake::path_confirms).
DEFINE FUNCTION OVERWRITE fn::confirming_locator($locator: string, $kind: string) -> bool {
    LET $path = string::lowercase(string::replace(array::first(string::split($locator, '#L')), '\\', '/'));
    LET $name = array::last(string::split($path, '/'));
    LET $doc = string::ends_with($name, '.md')
        OR string::ends_with($name, '.txt')
        OR string::ends_with($name, '.rst')
        OR string::starts_with($name, 'readme');
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
        OR ($doc AND ($kind != 'decision' OR session::ns() = 'proj_supergun'))
    );
} PERMISSIONS FULL;

-- Whether any of a fact's evidence locators may confirm it.
DEFINE FUNCTION OVERWRITE fn::confirmed_by_evidence($locators: array, $kind: string) -> bool {
    RETURN array::len(array::filter($locators, |$l| fn::confirming_locator($l, $kind))) > 0;
} PERMISSIONS FULL;

-- Rows written under the old R2 first, while it still holds: each fact's `signed` from the login that wrote
-- it, then every CONFIRMED row the new rule would refuse is set INFERRED with a note saying why. Otherwise
-- the new ASSERT, which re-runs on every write, would refuse the next write to such a row, the stale
-- event's included.
DEFINE FIELD OVERWRITE signed ON fact TYPE option<bool>;
DEFINE FIELD OVERWRITE regraded ON fact TYPE option<string>;
UPDATE fact SET signed = (written_by.role = 'owner') RETURN NONE;
UPDATE fact SET grade = 'INFERRED',
    regraded = '2026-10-30, schema 0017: was CONFIRMED by an owner statement an agent recorded, or by evidence that no longer confirms it (setup, memory, an agent report, or a doc for a kind other than decision, or any doc on supergun); only a receipt, the owner login or confirming evidence confirms now'
  WHERE grade = 'CONFIRMED'
    AND receipt = NONE
    AND !(owner_statement != NONE AND signed = true)
    AND !fn::confirmed_by_evidence(->evidence->unit.locator, kind)
  RETURN NONE;

-- Who signed: true only when the writer's login has the owner role. Set at create, never changed.
DEFINE FIELD OVERWRITE signed ON fact TYPE bool DEFAULT ($auth.role = 'owner') READONLY
  ASSERT $value = false OR $auth.role = 'owner';

DEFINE FIELD OVERWRITE grade ON fact TYPE string DEFAULT 'UNKNOWN'
  ASSERT $value IN ['CONFIRMED', 'INFERRED', 'UNKNOWN']
    AND ($value != 'CONFIRMED'
      OR $this.receipt != NONE
      OR ($this.owner_statement != NONE AND ($this.signed = true OR $auth.role = 'owner'))
      OR fn::confirmed_by_evidence($this.id->evidence->unit.locator, $this.kind));
