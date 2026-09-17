-- scope: project
-- The rules, enforced in the datastore for every writer. Root has no member row ($auth = NONE); root is the
-- migration and loader identity, so the role clauses let it through but the evidence clauses do not.
-- Each rule has a name; docs/product/DATA-MODEL.md maps rule -> test.

-- R1 stale_on_hash_change: a unit whose hash changes marks every LIVE fact citing it `stale`. Never deletes.
-- Events run privileged (measured on 3.1.6: a worker with no update on fact still triggers the update).
DEFINE EVENT OVERWRITE stale_on_hash_change ON TABLE unit
  WHEN $event = 'UPDATE' AND $before.hash != $after.hash
  THEN { UPDATE fact SET status = 'stale', stale_since = time::now() WHERE status = 'live' AND id IN $after.id<-evidence<-fact; };

-- R2 done_needs_fresh_reviewer_pass (ported from work-scholar's generated gate + schema_extra.surql):
--   status may be `done` only when a gate_run for THIS ticket has verdict pass, run_role reviewer and ran_at
--   newer than last_worker_commit_at, and only a lead or reviewer credential (or root) writes it.
--   A field ASSERT runs on every CREATE/UPDATE/UPSERT/INSERT of the row, so any later write to a done ticket
--   that makes the evidence stale (a newer commit), or that comes from a worker, is refused.
--   $parent is the ticket being written (measured bound in a field ASSERT on 3.1.6; $this is not).
DEFINE FIELD OVERWRITE status ON ticket TYPE string DEFAULT 'ready'
  ASSERT $value IN ['ready', 'in-progress', 'escalated', 'blocked', 'review', 'done', 'dropped']
    AND ($value != 'done' OR (
      ($auth = NONE OR $auth.role IN ['lead', 'reviewer'])
      AND count(SELECT VALUE id FROM gate_run WHERE ticket = $parent.id AND verdict = 'pass'
                AND run_role = 'reviewer' AND ran_at > $parent.last_worker_commit_at) >= 1));

-- R2a last_worker_commit_at only moves forward: an old pass cannot be made fresh by rewinding it.
DEFINE FIELD OVERWRITE last_worker_commit_at ON ticket TYPE datetime ASSERT $before = NONE OR $value >= $before;

-- R2b a gate_run cannot be edited or dated into the future (work-scholar B1: a pass dated 2099 would
--   outrun every later commit and make done permanent).
DEFINE FIELD OVERWRITE ticket ON gate_run TYPE record<ticket> READONLY;
DEFINE FIELD OVERWRITE command ON gate_run TYPE string READONLY;
DEFINE FIELD OVERWRITE verdict ON gate_run TYPE string READONLY ASSERT $value IN ['pass', 'fail'];
DEFINE FIELD OVERWRITE ran_at ON gate_run TYPE datetime DEFAULT time::now() READONLY ASSERT $value <= time::now();
DEFINE FIELD OVERWRITE commit ON gate_run TYPE option<string> READONLY;

-- R3 run_role_is_the_credential (closes work-scholar M1 "run_role is data, not a credential"):
--   under a member credential, run_role must equal that member's role and ran_by must be that member.
--   A worker cannot write a reviewer gate_run. Only root (the loader) records a run on someone's behalf.
DEFINE FIELD OVERWRITE run_role ON gate_run TYPE string READONLY
  ASSERT $value IN ['worker', 'reviewer'] AND ($auth = NONE OR $value = $auth.role);
DEFINE FIELD OVERWRITE ran_by ON gate_run TYPE option<record<member>> DEFAULT $auth.id READONLY
  ASSERT $auth = NONE OR $value = $auth.id;
