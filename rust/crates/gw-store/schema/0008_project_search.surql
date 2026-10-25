-- scope: project
-- Full-text search over units, with the analyzer 0007 committed first (see 0007 for why it is separate).
DEFINE INDEX OVERWRITE unit_text ON unit FIELDS text FULLTEXT ANALYZER gw_words BM25 HIGHLIGHTS;
