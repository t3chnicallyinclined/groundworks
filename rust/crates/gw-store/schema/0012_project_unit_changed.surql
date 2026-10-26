-- scope: project
-- When a unit's file last changed (2026-10-26): the date of the last commit touching it, or an untracked
-- file's mtime, as ISO 8601 text (sorts as time). Shown on every ask candidate, so a reader can tell an
-- older figure from the current one; the date never decides by itself (a source of record, a retraction or
-- a "superseded" line can outrank it).
DEFINE FIELD OVERWRITE changed_at ON unit TYPE option<string>;
