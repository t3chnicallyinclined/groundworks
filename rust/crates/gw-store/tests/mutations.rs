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
        file: "0010_project_lens_panel.surql",
        find: "ASSERT $value = NONE OR record::exists($value);",
        replace: ";",
        scope: Scope::Project,
        setup: "",
        who: Who::Agent,
        probe: "CREATE fact:p SET text = 't', attacks = fact:ghost, stance = 'disputes';",
    },
    Mutation {
        name: "an attack disputes or supports",
        file: "0010_project_lens_panel.surql",
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
