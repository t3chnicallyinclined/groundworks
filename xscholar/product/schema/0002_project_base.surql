-- scope: project
-- One project's knowledge and work. Namespace proj_<slug>, database = model.DB_NAME.
-- Shapes and role permissions only; the rules that make a row mean something are in 0003_project_rules.surql.
-- Tables: DEFINE IF NOT EXISTS + ALTER TABLE (DEFINE TABLE OVERWRITE resets PERMISSIONS on 3.1.6).
-- DELETE is NONE for every role on every table: nothing a member holds removes evidence.

-- ---- credentials (same shape as the workspace; a credential belongs to one namespace) --------------------
DEFINE TABLE IF NOT EXISTS member TYPE NORMAL SCHEMAFULL;
ALTER TABLE member PERMISSIONS FOR select WHERE id = $auth.id FOR create, update, delete NONE;
DEFINE FIELD OVERWRITE role ON member TYPE string ASSERT $value IN ['lead', 'worker', 'reviewer'];
DEFINE FIELD OVERWRITE pass ON member TYPE string PERMISSIONS FOR select NONE;
DEFINE FIELD OVERWRITE enabled ON member TYPE bool DEFAULT true;
DEFINE ACCESS IF NOT EXISTS member ON DATABASE TYPE RECORD
  SIGNIN (SELECT * FROM member WHERE id = type::record('member', $id) AND enabled = true AND crypto::argon2::compare(pass, $pass))
  DURATION FOR TOKEN 8h, FOR SESSION 8h;

-- ---- knowledge ----------------------------------------------------------------------------------------
-- unit: a citable span. locator = where (e.g. "src/a.rs#L10-40"), hash = what it said when last read.
DEFINE TABLE IF NOT EXISTS unit TYPE NORMAL SCHEMAFULL;
ALTER TABLE unit PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer']
  FOR create, update WHERE $auth.role IN ['lead', 'worker'] FOR delete NONE;
DEFINE FIELD OVERWRITE locator ON unit TYPE string;
DEFINE FIELD OVERWRITE kind ON unit TYPE option<string>;
DEFINE FIELD OVERWRITE hash ON unit TYPE string;
DEFINE FIELD OVERWRITE seen_at_commit ON unit TYPE option<string>;
DEFINE FIELD OVERWRITE read_at ON unit TYPE datetime DEFAULT time::now();
DEFINE INDEX OVERWRITE unit_locator ON unit FIELDS locator UNIQUE;

-- fact: a claim. status live|stale|gone, grade CONFIRMED|INFERRED|UNKNOWN.
DEFINE TABLE IF NOT EXISTS fact TYPE NORMAL SCHEMAFULL;
ALTER TABLE fact PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer']
  FOR create, update WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR delete NONE;
DEFINE FIELD OVERWRITE text ON fact TYPE string;
DEFINE FIELD OVERWRITE status ON fact TYPE string DEFAULT 'live' ASSERT $value IN ['live', 'stale', 'gone'];
DEFINE FIELD OVERWRITE grade ON fact TYPE string DEFAULT 'UNKNOWN' ASSERT $value IN ['CONFIRMED', 'INFERRED', 'UNKNOWN'];
DEFINE FIELD OVERWRITE verified_at ON fact TYPE option<datetime>;
DEFINE FIELD OVERWRITE stale_since ON fact TYPE option<datetime>;

-- evidence: fact -> unit. ENFORCED: both ends must exist when the link is written (measured, gates.py).
DEFINE TABLE IF NOT EXISTS evidence TYPE RELATION IN fact OUT unit ENFORCED SCHEMAFULL;
ALTER TABLE evidence PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer']
  FOR create WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE in ON evidence TYPE record<fact>;
DEFINE FIELD OVERWRITE out ON evidence TYPE record<unit>;
DEFINE FIELD OVERWRITE hash_at_cite ON evidence TYPE option<string>;

-- dead_end (project-local): proposed by anyone, accepted or rejected by the lead.
DEFINE TABLE IF NOT EXISTS dead_end TYPE NORMAL SCHEMAFULL;
ALTER TABLE dead_end PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer']
  FOR create WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR update WHERE $auth.role = 'lead' FOR delete NONE;
DEFINE FIELD OVERWRITE title ON dead_end TYPE string;
DEFINE FIELD OVERWRITE why ON dead_end TYPE string;
DEFINE FIELD OVERWRITE source ON dead_end TYPE option<string>;
DEFINE FIELD OVERWRITE status ON dead_end TYPE string DEFAULT 'proposed'
  ASSERT $value IN ['proposed', 'accepted', 'rejected'] AND ($value = 'proposed' OR $auth = NONE OR $auth.role = 'lead');

-- ---- work ---------------------------------------------------------------------------------------------
DEFINE TABLE IF NOT EXISTS ticket TYPE NORMAL SCHEMAFULL;
ALTER TABLE ticket PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer']
  FOR create WHERE $auth.role = 'lead' FOR update WHERE $auth.role IN ['lead', 'worker', 'reviewer'] FOR delete NONE;
DEFINE FIELD OVERWRITE ticket_id ON ticket TYPE string;
DEFINE FIELD OVERWRITE title ON ticket TYPE string;
DEFINE FIELD OVERWRITE repo ON ticket TYPE option<string>;
DEFINE FIELD OVERWRITE branch ON ticket TYPE option<string>;
DEFINE FIELD OVERWRITE gate ON ticket TYPE option<string>;
DEFINE FIELD OVERWRITE tier ON ticket TYPE string DEFAULT 'unknown' ASSERT $value IN ['haiku', 'sonnet', 'opus', 'unknown'];
DEFINE FIELD OVERWRITE status ON ticket TYPE string DEFAULT 'ready'
  ASSERT $value IN ['ready', 'in-progress', 'escalated', 'blocked', 'review', 'done', 'dropped'];
DEFINE FIELD OVERWRITE created ON ticket TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE last_worker_commit_at ON ticket TYPE datetime;
DEFINE FIELD OVERWRITE last_worker_commit ON ticket TYPE option<string>;
DEFINE INDEX OVERWRITE ticket_ticket_id ON ticket FIELDS ticket_id UNIQUE;

-- gate_run: a gate command's result for one ticket. Workers and reviewers write them; nobody edits one.
DEFINE TABLE IF NOT EXISTS gate_run TYPE NORMAL SCHEMAFULL;
ALTER TABLE gate_run PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer']
  FOR create WHERE $auth.role IN ['worker', 'reviewer'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE ticket ON gate_run TYPE record<ticket>;
DEFINE FIELD OVERWRITE command ON gate_run TYPE string;
DEFINE FIELD OVERWRITE verdict ON gate_run TYPE string ASSERT $value IN ['pass', 'fail'];
DEFINE FIELD OVERWRITE run_role ON gate_run TYPE string ASSERT $value IN ['worker', 'reviewer'];
DEFINE FIELD OVERWRITE ran_at ON gate_run TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE ran_by ON gate_run TYPE option<record<member>>;
DEFINE FIELD OVERWRITE commit ON gate_run TYPE option<string>;
DEFINE FIELD OVERWRITE output_tail ON gate_run TYPE option<string>;

DEFINE TABLE IF NOT EXISTS review TYPE NORMAL SCHEMAFULL;
ALTER TABLE review PERMISSIONS FOR select WHERE $auth.role IN ['lead', 'worker', 'reviewer']
  FOR create WHERE $auth.role = 'reviewer' FOR update, delete NONE;
DEFINE FIELD OVERWRITE ticket ON review TYPE record<ticket>;
DEFINE FIELD OVERWRITE verdict ON review TYPE string ASSERT $value IN ['approved', 'changes'];
DEFINE FIELD OVERWRITE blocking ON review TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE minor ON review TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE notes ON review TYPE option<string>;
DEFINE FIELD OVERWRITE reviewer ON review TYPE option<record<member>> DEFAULT $auth.id READONLY ASSERT $auth = NONE OR $value = $auth.id;
DEFINE FIELD OVERWRITE at ON review TYPE datetime DEFAULT time::now() READONLY ASSERT $value <= time::now();
