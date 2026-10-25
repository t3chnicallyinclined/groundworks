-- scope: project
-- What the journal needs on a fact (2026-10-24): when it was recorded, by whom, on which machine, and the
-- free-text source it named. `at` is written once. A unit may keep the exact text it quoted, so a brief can
-- cite it without reopening the file.
DEFINE FIELD OVERWRITE at ON fact TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE source ON fact TYPE option<string>;
DEFINE FIELD OVERWRITE by ON fact TYPE option<string>;
DEFINE FIELD OVERWRITE machine ON fact TYPE option<string>;
DEFINE INDEX OVERWRITE fact_at ON fact FIELDS at;
DEFINE FIELD OVERWRITE text ON unit TYPE option<string>;
