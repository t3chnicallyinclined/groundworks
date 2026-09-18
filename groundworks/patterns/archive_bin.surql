-- ============================================================================
-- Pattern: archive bin with retention, restore, legal hold and purge
--
-- Status: PROTOTYPE, proven on SurrealDB 3.1.4 on 2026-09-15 in a throwaway
-- database. 9 of 9 checks passed. Notes: notes/2026-09-15_archive_bin.md
--
-- Every DELETE on a covered table snapshots the full row (for edges, both ends
-- and all fields) into `archive`, with a reason, a timestamp, an expiry taken
-- from the per-table retention policy, and a legal-hold flag. Knowledge tables
-- have no expiry. A scheduled purge removes expired, un-held archive rows.
-- ============================================================================

-- ---- retention policy: days per table; NONE = never purged ------------------
UPSERT retention:fact     SET days = NONE, tier = 'knowledge';
UPSERT retention:argument SET days = NONE, tier = 'knowledge';
UPSERT retention:critique SET days = NONE, tier = 'knowledge';
UPSERT retention:cites    SET days = 90,   tier = 'artifact';
UPSERT retention:label    SET days = 30,   tier = 'artifact';
UPSERT retention:review   SET days = 180,  tier = 'artifact';

-- ---- archive-on-delete (repeat per covered table; shown for `cites`) --------
DEFINE EVENT OVERWRITE archive_on_delete ON TABLE cites WHEN $event = 'DELETE' THEN {
  LET $days = (SELECT VALUE days FROM ONLY retention:cites);
  CREATE archive SET
    original_id    = $before.id,
    original_table = 'cites',
    snapshot       = $before,
    archived_at    = time::now(),
    reason         = $before.delete_reason ?? 'deleted',
    expires_at     = IF $days = NONE THEN NONE ELSE time::now() + <duration> (<string> $days + 'd') END,
    legal_hold     = false;
};

-- ---- deleting with a reason: set it, then delete ----------------------------
-- UPDATE label SET delete_reason = 'replaced by run_2' WHERE run = 'run_1';
-- DELETE label WHERE run = 'run_1';

-- ---- restore: nodes first, then edges from their snapshots ------------------
-- LET $n = (SELECT VALUE snapshot FROM archive WHERE original_id = fact:f1 LIMIT 1)[0];
-- UPSERT fact:f1 CONTENT $n;
-- LET $e = (SELECT VALUE snapshot FROM archive WHERE original_table = 'cites' AND snapshot.in = fact:f1 LIMIT 1)[0];
-- RELATE ($e.in)->cites->($e.out) SET note = $e.note, restored = true;

-- ---- legal hold (blocks purge) ----------------------------------------------
-- UPDATE archive SET legal_hold = true WHERE original_table = 'label' AND snapshot.run = 'run_1';

-- ---- purge job (run on a schedule; log the count it removed) ----------------
-- DELETE archive WHERE expires_at != NONE AND expires_at < time::now() AND legal_hold = false;
