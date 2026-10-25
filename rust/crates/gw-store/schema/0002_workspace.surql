-- scope: workspace
-- A group of related projects that share among themselves. Namespace `ws_<name>`. Holds only what a
-- member project shared up on purpose; a project's own knowledge stays in its own namespace.

DEFINE TABLE IF NOT EXISTS member TYPE NORMAL SCHEMAFULL;
ALTER TABLE member PERMISSIONS FOR select WHERE id = $auth.id FOR create, update, delete NONE;
DEFINE FIELD OVERWRITE role ON member TYPE string ASSERT $value IN ['owner', 'agent'];
DEFINE FIELD OVERWRITE pass ON member TYPE string PERMISSIONS FOR select NONE;
DEFINE FIELD OVERWRITE enabled ON member TYPE bool DEFAULT true;
DEFINE ACCESS IF NOT EXISTS member ON DATABASE TYPE RECORD
  SIGNIN (SELECT * FROM member WHERE id = type::record('member', $id) AND enabled = true AND crypto::argon2::compare(pass, $pass))
  DURATION FOR TOKEN 8h, FOR SESSION 8h;

DEFINE TABLE IF NOT EXISTS dead_end TYPE NORMAL SCHEMAFULL;
ALTER TABLE dead_end PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
DEFINE FIELD OVERWRITE title ON dead_end TYPE string;
DEFINE FIELD OVERWRITE why ON dead_end TYPE string;
DEFINE FIELD OVERWRITE from_scope ON dead_end TYPE string;
DEFINE FIELD OVERWRITE source ON dead_end TYPE option<string>;
DEFINE FIELD OVERWRITE status ON dead_end TYPE string DEFAULT 'proposed'
  ASSERT $value IN ['proposed', 'accepted', 'rejected'] AND ($value = 'proposed' OR $auth = NONE OR $auth.role = 'owner');

DEFINE TABLE IF NOT EXISTS decision TYPE NORMAL SCHEMAFULL;
ALTER TABLE decision PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']
  FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;
DEFINE FIELD OVERWRITE title ON decision TYPE string;
DEFINE FIELD OVERWRITE why ON decision TYPE string;
DEFINE FIELD OVERWRITE from_scope ON decision TYPE string;
DEFINE FIELD OVERWRITE decided_at ON decision TYPE datetime DEFAULT time::now();
