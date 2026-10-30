-- scope: global
-- A project's own experts (2026-10-30): intake also reads `<project>/.claude/agents/*.md`. Each expert row says
-- where its definition came from, `global` (the user's agents folder) or `project:<name>`, and a name is unique
-- per source rather than overall, so a project expert that shares a global expert's name is kept beside it.
DEFINE FIELD OVERWRITE source ON expert TYPE string DEFAULT 'global';
UPDATE expert SET source = 'global' WHERE source = NONE RETURN NONE;
REMOVE INDEX IF EXISTS expert_name ON expert;
DEFINE INDEX OVERWRITE expert_name_source ON expert FIELDS name, source UNIQUE;
