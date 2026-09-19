-- archive_bin.surql
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
-- tms_propagation.surql
-- ============================================================================
-- Pattern: automatic demotion (truth-maintenance propagation) in the datastore
--
-- Status: PROTOTYPE, proven on SurrealDB 3.1.4 on 2026-09-15 in a throwaway
-- database. 7 of 7 checks passed: demote on grade drop; keep a fact that still
-- has another valid citation; cascade to conclusions; demote on citation
-- removal; computed field agrees; demotion is one-way.
-- Notes: notes/2026-09-15_automatic_demotion.md
--
-- Two mechanisms. Recommended use: COMPUTED for truth, EVENTS for history.
--   * COMPUTED effective status: derived on every read from the current
--     evidence. Cannot drift, cannot be bypassed, immune to loaders that
--     delete and recreate edges.
--   * EVENTS (triggers): fire on writes. Good for an audit trail of when and
--     why something was demoted, and for one-way stored demotion when a human
--     must re-confirm. Dangerous with loaders that delete-then-recreate edges
--     (they would stale everything mid-load); see the note.
--
-- Replace CONFIRMING with the instance's confirming grades, and extend the
-- counts to every citation table the instance's gate accepts.
-- ============================================================================

-- ---- 1. Computed effective status (truth on read) ----------------------------
DEFINE FIELD OVERWRITE effective_status ON fact COMPUTED
  IF status = 'confirmed'
     AND count(->cites->passage[WHERE read_as IN ['page_image','record_text']]) = 0
  THEN 'stale' ELSE status END;

-- ---- 2. Event: evidence grade drops -> demote facts that no longer have a valid cite
DEFINE EVENT OVERWRITE demote_on_grade ON TABLE passage
  WHEN $event = 'UPDATE'
   AND $before.read_as IN ['page_image','record_text']
   AND $after.read_as NOT IN ['page_image','record_text']
  THEN {
    FOR $f IN (SELECT VALUE in FROM cites WHERE out = $after.id) {
      IF $f.status = 'confirmed'
         AND count(SELECT VALUE id FROM $f->cites->passage WHERE read_as IN ['page_image','record_text']) = 0 {
        UPDATE $f SET status = 'stale',
                      stale_reason = 'evidence demoted: ' + <string> $after.id,
                      stale_at = time::now();
      };
    };
  };

-- ---- 3. Event: a citation is removed -> demote if nothing valid remains ------
DEFINE EVENT OVERWRITE demote_on_uncite ON TABLE cites
  WHEN $event = 'DELETE'
  THEN {
    LET $f = $before.in;
    IF $f.status = 'confirmed'
       AND count(SELECT VALUE id FROM $f->cites->passage WHERE read_as IN ['page_image','record_text']) = 0 {
      UPDATE $f SET status = 'stale', stale_reason = 'citation removed', stale_at = time::now();
    };
  };

-- ---- 4. Event: cascade -> a conclusion loses its last confirmed support ------
DEFINE EVENT OVERWRITE cascade_to_conclusions ON TABLE fact
  WHEN $event = 'UPDATE' AND $before.status = 'confirmed' AND $after.status != 'confirmed'
  THEN {
    FOR $a IN (SELECT VALUE out FROM supports WHERE in = $after.id) {
      IF $a.ready = true
         AND count(SELECT VALUE id FROM $a<-supports<-fact WHERE status = 'confirmed') = 0 {
        UPDATE $a SET ready = false, unready_reason = 'support demoted: ' + <string> $after.id;
      };
    };
  };

-- The status enum must include 'stale' (the gate's ASSERT list) before events 2-4
-- can write it.

-- ============================================================================
-- 5. Debunk propagation with a review queue (supersedes events 3-4 for facts)
--
-- PROTOTYPE, proven on SurrealDB 3.1.4 on 2026-09-15 (throwaway database), 10 of
-- 10 checks passed, cascade verified three levels deep. When a confirmed fact
-- is ruled out, demoted or superseded, everything downstream is handled by kind:
--   facts citing it:     demoted if it was their only valid justification,
--                        otherwise flagged "weakened"
--   conclusions it supported: unready if no confirmed support remains,
--                        otherwise flagged "support weakened"
--   conclusions it attacked:  flagged "adverse material debunked" (may help them)
--   output citations to it:   verdict NEEDS_RECHECK and flagged
-- Demotions re-fire this event, so the effect cascades through chains.
-- Every effect is written to `review` with target, cause and kind.
-- ============================================================================
DEFINE EVENT OVERWRITE propagate_out ON TABLE fact
  WHEN $event = 'UPDATE' AND $before.status = 'confirmed'
   AND $after.status IN ['ruled_out','stale','superseded']
  THEN {
    FOR $d IN (SELECT VALUE in FROM cites WHERE out = $after.id) {
      IF $d.status = 'confirmed' {
        IF (count(SELECT VALUE id FROM $d->cites->passage WHERE read_as IN ['page_image','record_text'])
            + count(SELECT VALUE id FROM $d->cites->fact WHERE status = 'confirmed')) = 0 {
          UPDATE $d SET status = 'stale', stale_reason = 'justification lost: ' + <string> $after.id, stale_at = time::now();
          CREATE review SET target = $d, cause = $after.id, kind = 'demoted_sole_justification_lost', at = time::now();
        } ELSE {
          CREATE review SET target = $d, cause = $after.id, kind = 'weakened_one_justification_lost', at = time::now();
        };
      };
    };
    FOR $a IN (SELECT VALUE out FROM supports WHERE in = $after.id) {
      IF $a.ready = true AND count(SELECT VALUE id FROM $a<-supports<-fact WHERE status = 'confirmed') = 0 {
        UPDATE $a SET ready = false, unready_reason = 'support lost: ' + <string> $after.id;
        CREATE review SET target = $a, cause = $after.id, kind = 'conclusion_unready', at = time::now();
      } ELSE {
        CREATE review SET target = $a, cause = $after.id, kind = 'conclusion_support_weakened', at = time::now();
      };
    };
    FOR $a IN (SELECT VALUE out FROM undercuts WHERE in = $after.id) {
      CREATE review SET target = $a, cause = $after.id, kind = 'adverse_material_debunked', at = time::now();
    };
    FOR $b IN (SELECT VALUE in FROM resolves WHERE out = $after.id) {
      UPDATE $b SET verdict = 'NEEDS_RECHECK', recheck_reason = 'target no longer confirmed: ' + <string> $after.id;
      CREATE review SET target = $b, cause = $after.id, kind = 'citation_target_not_confirmed', at = time::now();
    };
  };
-- Note: the output-citation verdict enum must include NEEDS_RECHECK.
