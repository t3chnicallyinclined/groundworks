//! Every rule can go red. Each case loosens one rule in a copy of the schema, migrates a fresh
//! namespace with it, and checks that a write the real schema refuses is now accepted. A rule whose
//! mutant still refuses would mean the probe tests something else, and this file fails.

use gw_core::db::{Db, DbError};
use gw_store::testkit::{AGENT_PASS, OWNER_PASS, Throwaway};
use gw_store::{SCHEMA, Scope, connect_member, ensure, migrate, provision};

#[derive(Clone, Copy)]
enum Who {
    Root,
    Agent,
    Owner,
}

struct Mutation {
    name: &'static str,
    file: &'static str,
    find: &'static str,
    replace: &'static str,
    scope: Scope,
    setup: &'static str,
    who: Who,
    probe: &'static str,
}

const MUTATIONS: &[Mutation] = &[
    // R2 lives in 0017 since 2026-10-30 (it redefines `grade`; loosening 0004's copy no longer loosens it).
    Mutation {
        name: "R2 CONFIRMED needs evidence",
        file: "0017_project_promotion.surql",
        find: "AND ($value != 'CONFIRMED'",
        replace: "AND (true OR $value != 'CONFIRMED'",
        scope: Scope::Project,
        setup: "",
        who: Who::Agent,
        probe: "CREATE fact:p SET text = 't', grade = 'CONFIRMED';",
    },
    Mutation {
        name: "R2 an owner statement confirms only on the owner's own row",
        file: "0017_project_promotion.surql",
        find: "($this.signed = true OR $auth.role = 'owner')",
        replace: "true",
        scope: Scope::Project,
        setup: "",
        who: Who::Agent,
        probe: "CREATE fact:p SET text = 't', grade = 'CONFIRMED', owner_statement = 'owner said';",
    },
    Mutation {
        name: "R2 a doc confirms only a decision (owner, 2026-10-30)",
        file: "0017_project_promotion.surql",
        find: "OR ($doc AND ($kind != 'decision' OR session::ns() = 'proj_supergun'))",
        replace: "",
        scope: Scope::Project,
        setup: "CREATE unit:u SET locator = 'docs/DESIGN.md#L1-3', hash = 'h'; \
                CREATE fact:p SET text = 't', kind = 'gotcha', grade = 'INFERRED'; RELATE fact:p->evidence->unit:u;",
        who: Who::Agent,
        probe: "UPDATE fact:p SET grade = 'CONFIRMED';",
    },
    Mutation {
        name: "R2 setup, memory and report units never confirm",
        file: "0017_project_promotion.surql",
        find: "RETURN !(",
        replace: "RETURN true OR !(",
        scope: Scope::Project,
        setup: "CREATE unit:u SET locator = 'CLAUDE.md#L1-3', hash = 'h'; \
                CREATE fact:p SET text = 't', grade = 'INFERRED'; RELATE fact:p->evidence->unit:u;",
        who: Who::Agent,
        probe: "UPDATE fact:p SET grade = 'CONFIRMED';",
    },
    Mutation {
        name: "R3 receipts are read-only, even for root",
        file: "0004_project_rules.surql",
        find: "DEFINE FIELD OVERWRITE ok ON receipt TYPE bool READONLY;",
        replace: "DEFINE FIELD OVERWRITE ok ON receipt TYPE bool;",
        scope: Scope::Project,
        setup: "CREATE receipt:r SET command = 'c', machine = 'M', location = '.', ok = false, output_hash = 'x';",
        who: Who::Root,
        probe: "UPDATE receipt:r SET ok = true;",
    },
    Mutation {
        name: "R3 receipts cannot be dated into the future",
        file: "0004_project_rules.surql",
        find: "READONLY ASSERT $value <= time::now();",
        replace: "READONLY;",
        scope: Scope::Project,
        setup: "",
        who: Who::Agent,
        probe: "CREATE receipt:f SET command = 'c', machine = 'M', location = '.', ok = true, output_hash = 'x', ran_at = d'2099-01-01T00:00:00Z';",
    },
    Mutation {
        name: "R3 a receipt's ran_by is its writer",
        file: "0004_project_rules.surql",
        find: "DEFAULT $auth.id READONLY\n  ASSERT $auth = NONE OR $value = $auth.id;",
        replace: "DEFAULT $auth.id READONLY;",
        scope: Scope::Project,
        setup: "",
        who: Who::Agent,
        probe: "CREATE receipt:g SET command = 'c', machine = 'M', location = '.', ok = true, output_hash = 'x', ran_by = member:owner1;",
    },
    Mutation {
        name: "0006 a fact's written_by is its writer",
        file: "0006_project_provenance.surql",
        find: "READONLY\n  ASSERT $auth = NONE OR $value = $auth.id;",
        replace: "READONLY;",
        scope: Scope::Project,
        setup: "",
        who: Who::Agent,
        probe: "CREATE fact:w SET text = 't', grade = 'INFERRED', written_by = member:owner1;",
    },
    Mutation {
        name: "facts cannot be deleted",
        file: "0003_project_base.surql",
        find: "ALTER TABLE fact PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']\n  FOR create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;",
        replace: "ALTER TABLE fact PERMISSIONS FULL;",
        scope: Scope::Project,
        setup: "CREATE fact:d SET text = 't', grade = 'INFERRED';",
        who: Who::Agent,
        probe: "DELETE fact:d RETURN BEFORE;",
    },
    Mutation {
        name: "only the owner settles a dead end",
        file: "0003_project_base.surql",
        find: "AND ($value = 'proposed' OR $auth = NONE OR $auth.role = 'owner');",
        replace: ";",
        scope: Scope::Project,
        setup: "",
        who: Who::Agent,
        probe: "CREATE dead_end:x SET title = 't', why = 'w', status = 'accepted';",
    },
    Mutation {
        name: "only the owner sets a scope policy",
        file: "0001_global.surql",
        find: "ALTER TABLE scope_policy PERMISSIONS FOR select WHERE $auth.role IN ['owner', 'agent']\n  FOR create, update WHERE $auth.role = 'owner' FOR delete NONE;",
        replace: "ALTER TABLE scope_policy PERMISSIONS FULL;",
        scope: Scope::Global,
        setup: "",
        who: Who::Agent,
        probe: "CREATE scope_policy:proj_a SET name = 'proj_a', kind = 'project', level = 'shared';",
    },
    Mutation {
        name: "a wall is stored once, in order",
        file: "0001_global.surql",
        find: "DEFINE FIELD OVERWRITE b ON wall TYPE string ASSERT $value > $this.a;",
        replace: "DEFINE FIELD OVERWRITE b ON wall TYPE string;",
        scope: Scope::Global,
        setup: "",
        who: Who::Owner,
        probe: "CREATE wall SET a = 'proj_b', b = 'proj_a', why = 'reversed';",
    },
    Mutation {
        name: "only the owner confirms a gate",
        file: "0001_global.surql",
        find: "ASSERT $value = false OR $auth = NONE OR $auth.role = 'owner';",
        replace: ";",
        scope: Scope::Global,
        setup: "CREATE project:a SET slug = 'a', name = 'A', root = 'C:/a';",
        who: Who::Agent,
        probe: "CREATE gate_def:g SET project = project:a, command = 'cargo test', confirmed = true;",
    },
    Mutation {
        name: "an attack names a fact that exists",
        file: "0018_project_attack.surql",
        find: "ASSERT $value = NONE OR record::exists($value);",
        replace: ";",
        scope: Scope::Project,
        setup: "",
        who: Who::Agent,
        // A support: a dispute's edge (0018's event) would refuse the ghost by itself.
        probe: "CREATE fact:p SET text = 't', attacks = fact:ghost, stance = 'supports';",
    },
    Mutation {
        name: "an attack disputes or supports",
        file: "0018_project_attack.surql",
        find: "ASSERT $value = NONE OR $value IN ['disputes', 'supports'];",
        replace: ";",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target';",
        who: Who::Agent,
        probe: "CREATE fact:p SET text = 't', attacks = fact:t, stance = 'whatever';",
    },
    Mutation {
        name: "a panel's locked config is read-only",
        file: "0010_project_lens_panel.surql",
        find: "DEFINE FIELD OVERWRITE config ON panel TYPE string READONLY;",
        replace: "DEFINE FIELD OVERWRITE config ON panel TYPE string;",
        scope: Scope::Project,
        setup: "CREATE panel:p SET config = '{}', digest = 'd';",
        who: Who::Agent,
        probe: "UPDATE panel:p SET config = '{\"consensus\": 0.51}';",
    },
    Mutation {
        name: "a panel answer is never deleted (its fields are read-only besides)",
        file: "0010_project_lens_panel.surql",
        find: "ALTER TABLE panel_answer PERMISSIONS FOR select, create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;",
        replace: "ALTER TABLE panel_answer PERMISSIONS FULL;",
        scope: Scope::Project,
        setup: "CREATE panel:p SET config = '{}', digest = 'd'; CREATE panel_answer:a SET panel = panel:p, round = 'round1', seat = 's', text = 'x', sha256 = 'h';",
        who: Who::Agent,
        probe: "DELETE panel_answer:a RETURN BEFORE;",
    },
    Mutation {
        name: "0018 no withdrawal: a fact's attacks is read-only",
        file: "0018_project_attack.surql",
        find: "DEFINE FIELD OVERWRITE attacks ON fact TYPE option<record<fact>> READONLY",
        replace: "DEFINE FIELD OVERWRITE attacks ON fact TYPE option<record<fact>>",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target'; CREATE fact:p SET text = 'p', attacks = fact:t, stance = 'disputes';",
        who: Who::Agent,
        probe: "UPDATE fact:p SET attacks = NONE;",
    },
    Mutation {
        name: "0018 no withdrawal: a fact's stance is read-only",
        file: "0018_project_attack.surql",
        find: "DEFINE FIELD OVERWRITE stance ON fact TYPE option<string> READONLY",
        replace: "DEFINE FIELD OVERWRITE stance ON fact TYPE option<string>",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target'; CREATE fact:p SET text = 'p', attacks = fact:t, stance = 'disputes';",
        who: Who::Agent,
        probe: "UPDATE fact:p SET stance = 'supports';",
    },
    Mutation {
        name: "0018 every dispute is an attack edge",
        file: "0018_project_attack.surql",
        find: "WHEN $event = 'CREATE' AND $after.stance = 'disputes'",
        replace: "WHEN false AND $after.stance = 'disputes'",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target'; CREATE fact:p SET text = 'p', attacks = fact:t, stance = 'disputes';",
        who: Who::Agent,
        probe: "RELATE fact:p->attack->fact:t SET verdict = 'unjudged', why = 'a second edge for one dispute';",
    },
    Mutation {
        name: "0018 an attack's verdict is required",
        file: "0018_project_attack.surql",
        find: "DEFINE FIELD OVERWRITE verdict ON attack TYPE string\n",
        replace: "DEFINE FIELD OVERWRITE verdict ON attack TYPE string DEFAULT 'unjudged'\n",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target'; CREATE fact:a SET text = 'attack';",
        who: Who::Agent,
        probe: "RELATE fact:a->attack->fact:t SET why = 'no verdict given';",
    },
    Mutation {
        name: "0018 a judged verdict comes with its ruling",
        file: "0018_project_attack.surql",
        find: "AND IF $value = 'unjudged' THEN $this.ruling = NONE\n      ELSE $this.ruling != NONE AND string::len(string::trim($this.ruling)) > 0 END;",
        replace: ";",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target'; CREATE fact:a SET text = 'attack';",
        who: Who::Agent,
        probe: "RELATE fact:a->attack->fact:t SET verdict = 'fatal', why = 'no ruling';",
    },
    Mutation {
        name: "0018 a verdict is set once, even by root",
        file: "0018_project_attack.surql",
        find: "AND ($before = NONE OR $before = 'unjudged' OR $before = $value)",
        replace: "AND true",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target', grade = 'INFERRED'; CREATE fact:a SET text = 'attack', grade = 'INFERRED'; RELATE fact:a->attack->fact:t SET verdict = 'fatal', why = 'w', ruling = 'r';",
        who: Who::Root,
        probe: "UPDATE attack SET verdict = 'survivable' WHERE in = fact:a;",
    },
    Mutation {
        name: "0018 a ruling is written once",
        file: "0018_project_attack.surql",
        find: "DEFINE FIELD OVERWRITE ruling ON attack TYPE option<string> ASSERT $before = NONE OR $before = $value;",
        replace: "DEFINE FIELD OVERWRITE ruling ON attack TYPE option<string>;",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target', grade = 'INFERRED'; CREATE fact:a SET text = 'attack', grade = 'INFERRED'; RELATE fact:a->attack->fact:t SET verdict = 'fatal', why = 'w', ruling = 'r';",
        who: Who::Agent,
        probe: "UPDATE attack SET ruling = 'softer words' WHERE in = fact:a;",
    },
    Mutation {
        name: "0018 an attack's reason is read-only",
        file: "0018_project_attack.surql",
        find: "DEFINE FIELD OVERWRITE why ON attack TYPE string READONLY",
        replace: "DEFINE FIELD OVERWRITE why ON attack TYPE string",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target', grade = 'INFERRED'; CREATE fact:a SET text = 'attack', grade = 'INFERRED'; RELATE fact:a->attack->fact:t SET verdict = 'fatal', why = 'w', ruling = 'r';",
        who: Who::Agent,
        probe: "UPDATE attack SET why = 'other words' WHERE in = fact:a;",
    },
    Mutation {
        name: "0018 nobody deletes an attack",
        file: "0018_project_attack.surql",
        find: "ALTER TABLE attack PERMISSIONS FOR select, create, update WHERE $auth.role IN ['owner', 'agent'] FOR delete NONE;",
        replace: "ALTER TABLE attack PERMISSIONS FULL;",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target', grade = 'INFERRED'; CREATE fact:a SET text = 'attack', grade = 'INFERRED'; RELATE fact:a->attack->fact:t SET verdict = 'fatal', why = 'w', ruling = 'r';",
        who: Who::Owner,
        probe: "DELETE attack WHERE in = fact:a RETURN BEFORE;",
    },
    Mutation {
        name: "0018 an attack's writer is its writer",
        file: "0018_project_attack.surql",
        find: "DEFINE FIELD OVERWRITE written_by ON attack TYPE option<record<member>> DEFAULT $auth.id READONLY\n  ASSERT $auth = NONE OR $value = $auth.id;",
        replace: "DEFINE FIELD OVERWRITE written_by ON attack TYPE option<record<member>> DEFAULT $auth.id READONLY;",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target'; CREATE fact:a SET text = 'attack';",
        who: Who::Agent,
        probe: "RELATE fact:a->attack->fact:t SET verdict = 'unjudged', why = 'w', written_by = member:owner1;",
    },
    Mutation {
        name: "0018 nobody deletes an answer",
        file: "0018_project_attack.surql",
        find: "ALTER TABLE answer PERMISSIONS FOR select, create WHERE $auth.role IN ['owner', 'agent'] FOR update, delete NONE;",
        replace: "ALTER TABLE answer PERMISSIONS FULL;",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target', grade = 'INFERRED'; CREATE fact:a SET text = 'attack', grade = 'INFERRED'; RELATE fact:a->attack->fact:t SET verdict = 'fatal', why = 'w', ruling = 'r'; CREATE fact:b SET text = 'bare', grade = 'INFERRED'; LET $k = (SELECT VALUE id FROM ONLY attack WHERE in = fact:a LIMIT 1); RELATE fact:b->answer->$k SET outcome = 'conceded';",
        who: Who::Owner,
        probe: "DELETE answer WHERE in = fact:b RETURN BEFORE;",
    },
    Mutation {
        name: "0018 an answer is written once, even by root",
        file: "0018_project_attack.surql",
        find: "DEFINE FIELD OVERWRITE replaced_by ON answer TYPE option<record<fact>> READONLY;",
        replace: "DEFINE FIELD OVERWRITE replaced_by ON answer TYPE option<record<fact>>;",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target', grade = 'INFERRED'; CREATE fact:a SET text = 'attack', grade = 'INFERRED'; RELATE fact:a->attack->fact:t SET verdict = 'fatal', why = 'w', ruling = 'r'; CREATE fact:b SET text = 'bare', grade = 'INFERRED'; LET $k = (SELECT VALUE id FROM ONLY attack WHERE in = fact:a LIMIT 1); RELATE fact:b->answer->$k SET outcome = 'conceded'; CREATE fact:n SET text = 'narrower';",
        who: Who::Root,
        probe: "UPDATE answer SET replaced_by = fact:n WHERE in = fact:b;",
    },
    Mutation {
        name: "0018 rebutted needs evidence",
        file: "0018_project_attack.surql",
        find: "($value = 'rebutted' AND count($this.in->evidence) >= 1)",
        replace: "$value = 'rebutted'",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target', grade = 'INFERRED'; CREATE fact:a SET text = 'attack', grade = 'INFERRED'; RELATE fact:a->attack->fact:t SET verdict = 'fatal', why = 'w', ruling = 'r'; CREATE fact:b SET text = 'bare', grade = 'INFERRED'; CREATE receipt:red SET command = 'c', machine = 'M', location = '.', ok = false, output_hash = 'x'; CREATE fact:tried SET text = 'tried', grade = 'INFERRED', receipt = receipt:red;",
        who: Who::Agent,
        probe: "RELATE fact:b->answer->(SELECT VALUE id FROM ONLY attack WHERE in = fact:a LIMIT 1) SET outcome = 'rebutted';",
    },
    Mutation {
        name: "0018 fixed needs a green receipt",
        file: "0018_project_attack.surql",
        find: "$this.in.receipt != NONE AND $this.in.receipt.ok = true",
        replace: "true",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target', grade = 'INFERRED'; CREATE fact:a SET text = 'attack', grade = 'INFERRED'; RELATE fact:a->attack->fact:t SET verdict = 'fatal', why = 'w', ruling = 'r'; CREATE fact:b SET text = 'bare', grade = 'INFERRED'; CREATE receipt:red SET command = 'c', machine = 'M', location = '.', ok = false, output_hash = 'x'; CREATE fact:tried SET text = 'tried', grade = 'INFERRED', receipt = receipt:red;",
        who: Who::Agent,
        probe: "RELATE fact:tried->answer->(SELECT VALUE id FROM ONLY attack WHERE in = fact:a LIMIT 1) SET outcome = 'fixed';",
    },
    Mutation {
        name: "0018 narrowed names the narrower claim",
        file: "0018_project_attack.surql",
        find: "($value = 'narrowed' AND $this.replaced_by != NONE",
        replace: "($value = 'narrowed' OR $this.replaced_by != NONE",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target', grade = 'INFERRED'; CREATE fact:a SET text = 'attack', grade = 'INFERRED'; RELATE fact:a->attack->fact:t SET verdict = 'fatal', why = 'w', ruling = 'r'; CREATE fact:b SET text = 'bare', grade = 'INFERRED'; CREATE receipt:red SET command = 'c', machine = 'M', location = '.', ok = false, output_hash = 'x'; CREATE fact:tried SET text = 'tried', grade = 'INFERRED', receipt = receipt:red;",
        who: Who::Agent,
        probe: "RELATE fact:b->answer->(SELECT VALUE id FROM ONLY attack WHERE in = fact:a LIMIT 1) SET outcome = 'narrowed';",
    },
    Mutation {
        name: "0018 the attacker does not answer its own attack",
        file: "0018_project_attack.surql",
        find: "AND $this.in != $this.out.in\n",
        replace: "\n",
        scope: Scope::Project,
        setup: "CREATE fact:t SET text = 'target', grade = 'INFERRED'; CREATE fact:a SET text = 'attack', grade = 'INFERRED'; RELATE fact:a->attack->fact:t SET verdict = 'fatal', why = 'w', ruling = 'r'; CREATE fact:b SET text = 'bare', grade = 'INFERRED'; CREATE receipt:red SET command = 'c', machine = 'M', location = '.', ok = false, output_hash = 'x'; CREATE fact:tried SET text = 'tried', grade = 'INFERRED', receipt = receipt:red;",
        who: Who::Agent,
        probe: "RELATE fact:a->answer->(SELECT VALUE id FROM ONLY attack WHERE in = fact:a LIMIT 1) SET outcome = 'conceded';",
    },
];

fn ns_for(scope: Scope, n: usize) -> String {
    match scope {
        Scope::Global => "global".to_owned(),
        _ => format!("proj_m{n}"),
    }
}

/// Migrate `ns` with `files`, add both members, run `setup` as root, and report whether the probe
/// was accepted.
fn accepted(server: &Throwaway, ns: &str, scope: Scope, files: &[(&str, &str)], m: &Mutation) -> bool {
    let root = server.root(ns);
    ensure(&root, ns).unwrap();
    migrate(&root, scope, files, true).unwrap();
    provision(&root, "owner1", "owner", OWNER_PASS).unwrap();
    provision(&root, "agent1", "agent", AGENT_PASS).unwrap();
    if !m.setup.is_empty() {
        root.sql(m.setup, None).unwrap();
    }
    let db: Db = match m.who {
        Who::Root => root,
        Who::Agent => connect_member(&server.url(), ns, "agent1", AGENT_PASS).unwrap(),
        Who::Owner => connect_member(&server.url(), ns, "owner1", OWNER_PASS).unwrap(),
    };
    match db.try_sql(m.probe) {
        Ok((ok, _)) => ok,
        Err(DbError::PermissionDenied { .. }) => false,
        Err(e) => panic!("{}: unexpected failure: {e}", m.name),
    }
}

#[test]
fn every_rule_can_go_red() {
    let mut failures = Vec::new();
    for (n, m) in MUTATIONS.iter().enumerate() {
        // Line endings depend on the checkout; SurrealQL does not care, the patterns do.
        let original = SCHEMA
            .iter()
            .find(|(f, _)| *f == m.file)
            .expect("file")
            .1
            .replace('\r', "");
        assert!(
            original.contains(m.find),
            "{}: the rule text to loosen was not found in {}",
            m.name,
            m.file
        );
        let mutated_text = original.replacen(m.find, m.replace, 1);
        assert!(
            !original.contains(&mutated_text),
            "{}: the loosening changed nothing",
            m.name
        );
        let mutated: Vec<(&str, &str)> = SCHEMA
            .iter()
            .map(|(f, t)| {
                if *f == m.file {
                    (*f, mutated_text.as_str())
                } else {
                    (*f, *t)
                }
            })
            .collect();
        // Global has one fixed namespace name, so each side gets its own server.
        let real_server = Throwaway::start();
        let real = accepted(&real_server, &ns_for(m.scope, n), m.scope, SCHEMA, m);
        let mutant_server = Throwaway::start();
        let mutant = accepted(&mutant_server, &ns_for(m.scope, n), m.scope, &mutated, m);
        if real || !mutant {
            failures.push(format!(
                "{}: real schema accepted = {real}, loosened schema accepted = {mutant}",
                m.name
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "rules that cannot go red:\n  {}",
        failures.join("\n  ")
    );
}
