-- scope: project
-- One project's own knowledge. Namespace `proj_<slug>`. Shapes and role permissions only; the rules that
-- make a row mean something are in 0004_project_rules.surql. DELETE is NONE for every role on every table:
-- nothing a member holds removes evidence. Harvested from the 2026-09-17 0002_project_base.surql, minus
-- tickets and reviews; gate runs become receipts.

DEFINE TABLE IF NOT EXISTS member TYPE NORMAL SCHEMAFULL;
ALTER TABLE member PERMISSIONS FOR select WHERE id = $auth.id FOR create, update, delete NONE;
DEFINE FIELD OVERWRITE role ON member TYPE string ASSERT $value IN ['owner', 'agent'];
DEFINE FIELD OVERWRITE pass ON member TYPE string PERMISSIONS FOR select NONE;
DEFINE FIELD OVERWRITE enabled ON member TYPE bool DEFAULT true;
DEFINE ACCESS IF NOT EXISTS member ON DATABASE TYPE RECORD
  SIGNIN (SELECT * FROM member WHERE id = type::record('member', $id) AND enabled = true AND crypto::argon2::compare(pass, $pass))
  DURATION FOR TOKEN 8h, FOR SESSION 8h;

-- unit: a citable span. locator = where (e.g. "src/a.rs#L10-40"), hash = what it said when last read.
DEFINE TABLE IF NOT EXISTS unit TYPE NORMAL SCHEMAFULL;
ALTER TABLE unit PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
DEFINE FIELD OVERWRITE locator ON unit TYPE string;
DEFINE FIELD OVERWRITE kind ON unit TYPE option<string>;
DEFINE FIELD OVERWRITE hash ON unit TYPE string;
DEFINE FIELD OVERWRITE seen_at_commit ON unit TYPE option<string>;
DEFINE FIELD OVERWRITE read_at ON unit TYPE datetime DEFAULT time::now();
DEFINE INDEX OVERWRITE unit_locator ON unit FIELDS locator UNIQUE;

-- receipt: proof that a command ran. Written once, never edited (rules in 0004).
DEFINE TABLE IF NOT EXISTS receipt TYPE NORMAL SCHEMAFULL;
ALTER TABLE receipt PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE command ON receipt TYPE string;
DEFINE FIELD OVERWRITE machine ON receipt TYPE string;
DEFINE FIELD OVERWRITE location ON receipt TYPE string;
DEFINE FIELD OVERWRITE exit_code ON receipt TYPE option<int>;
DEFINE FIELD OVERWRITE ok ON receipt TYPE bool;
DEFINE FIELD OVERWRITE output_hash ON receipt TYPE string;
DEFINE FIELD OVERWRITE commit ON receipt TYPE option<string>;

-- fact: a claim. status live|stale|gone; grade CONFIRMED|INFERRED|UNKNOWN; kind as in the journal.
DEFINE TABLE IF NOT EXISTS fact TYPE NORMAL SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL;
ALTER TABLE fact PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
DEFINE FIELD OVERWRITE text ON fact TYPE string;
DEFINE FIELD OVERWRITE kind ON fact TYPE string DEFAULT 'fact' ASSERT $value IN ['decision', 'gotcha', 'status', 'fact', 'eliminated'];
DEFINE FIELD OVERWRITE status ON fact TYPE string DEFAULT 'live' ASSERT $value IN ['live', 'stale', 'gone'];
DEFINE FIELD OVERWRITE receipt ON fact TYPE option<record<receipt>>;
DEFINE FIELD OVERWRITE owner_statement ON fact TYPE option<string>;
DEFINE FIELD OVERWRITE verified_at ON fact TYPE option<datetime>;
DEFINE FIELD OVERWRITE stale_since ON fact TYPE option<datetime>;

-- evidence: fact -> unit. ENFORCED: both ends must exist when the link is written.
DEFINE TABLE IF NOT EXISTS evidence TYPE RELATION IN fact OUT unit ENFORCED SCHEMAFULL;
ALTER TABLE evidence PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;
DEFINE FIELD OVERWRITE in ON evidence TYPE record<fact>;
DEFINE FIELD OVERWRITE out ON evidence TYPE record<unit>;
DEFINE FIELD OVERWRITE hash_at_cite ON evidence TYPE option<string>;
DEFINE INDEX OVERWRITE evidence_pair ON evidence FIELDS in, out UNIQUE;

-- dead_end (project-local): any member proposes, only the owner accepts or rejects.
DEFINE TABLE IF NOT EXISTS dead_end TYPE NORMAL SCHEMAFULL;
ALTER TABLE dead_end PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
DEFINE FIELD OVERWRITE title ON dead_end TYPE string;
DEFINE FIELD OVERWRITE why ON dead_end TYPE string;
DEFINE FIELD OVERWRITE source ON dead_end TYPE option<string>;
DEFINE FIELD OVERWRITE status ON dead_end TYPE string DEFAULT 'proposed'
  ASSERT $value IN ['proposed', 'accepted', 'rejected'] AND ($value = 'proposed' OR $auth = NONE OR $auth.role = 'owner');
