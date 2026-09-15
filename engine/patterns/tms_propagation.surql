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
