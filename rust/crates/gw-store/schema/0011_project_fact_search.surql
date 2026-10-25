-- scope: project
-- Full-text search over facts (2026-10-25), so `ask` can put a recorded, checked answer above the raw files.
-- Same analyzer as units (0007).
DEFINE INDEX OVERWRITE fact_text ON fact FIELDS text FULLTEXT ANALYZER gw_words BM25 HIGHLIGHTS;
