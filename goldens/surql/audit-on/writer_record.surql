-- list of 12
DEFINE FIELD OVERWRITE written_by ON authority TYPE array<record> VALUE array::union($before ?? [], IF $auth = NONE { [] } ELSE { [$auth] });
DEFINE FIELD OVERWRITE written_by ON fact TYPE array<record> VALUE array::union($before ?? [], IF $auth = NONE { [] } ELSE { [$auth] });
-- row_writer: who first wrote each row, copied from the credential at that moment; no credential can write it
DEFINE TABLE OVERWRITE row_writer SCHEMAFULL PERMISSIONS NONE;
DEFINE FIELD OVERWRITE row ON row_writer TYPE record;
DEFINE FIELD OVERWRITE credential ON row_writer TYPE record;
DEFINE FIELD OVERWRITE principal ON row_writer TYPE option<string>;
DEFINE FIELD OVERWRITE first_at ON row_writer TYPE datetime;
DEFINE FIELD OVERWRITE provisioned_at ON row_writer TYPE option<datetime>;
DEFINE EVENT OVERWRITE authority_row_writer ON TABLE authority WHEN $auth != NONE AND $event != 'DELETE' THEN { IF type::record('row_writer', [$after.id, $auth]).first_at = NONE { CREATE type::record('row_writer', [$after.id, $auth]) SET row = $after.id, credential = $auth, principal = $auth.principal, first_at = time::now(), provisioned_at = $auth.provisioned_at; }; };
DEFINE EVENT OVERWRITE conclusion_row_writer ON TABLE conclusion WHEN $auth != NONE AND $event != 'DELETE' THEN { IF type::record('row_writer', [$after.id, $auth]).first_at = NONE { CREATE type::record('row_writer', [$after.id, $auth]) SET row = $after.id, credential = $auth, principal = $auth.principal, first_at = time::now(), provisioned_at = $auth.provisioned_at; }; };
DEFINE EVENT OVERWRITE fact_row_writer ON TABLE fact WHEN $auth != NONE AND $event != 'DELETE' THEN { IF type::record('row_writer', [$after.id, $auth]).first_at = NONE { CREATE type::record('row_writer', [$after.id, $auth]) SET row = $after.id, credential = $auth, principal = $auth.principal, first_at = time::now(), provisioned_at = $auth.provisioned_at; }; };
