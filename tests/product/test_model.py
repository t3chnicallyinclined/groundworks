# -*- coding: utf-8 -*-
"""The product data model, proven both ways against a throwaway in-memory SurrealDB (never a live one).

    python -m pytest tests/product/test_model.py -q      (or: python tests/product/test_model.py)

Every rule is tested with its refused arm AND the accepted write, on the same fixture, so a refusal cannot
come from a broken setup. XSP_SCHEMA_DIR points the suite at another schema directory (the mutation check
copies the schema, strips one rule and expects the matching tests to go red).
"""
import itertools
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from xscholar import surreal_bin  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar.product import model  # noqa: E402

LIVE_PORTS = [range(8001, 8004), range(8023, 8048), range(8061, 8062)]
SCHEMA_DIR = os.environ.get("XSP_SCHEMA_DIR") or None
# Fixed in the past so ran_at <= time::now() holds whenever the suite runs; only FUTURE must fail it.
COMMIT_AT = "2020-01-01T10:00:00Z"
BEFORE = "2020-01-01T09:00:00Z"
AFTER = "2020-01-01T11:00:00Z"
LATER = "2020-01-01T12:00:00Z"
REWIND = "2019-12-31T23:00:00Z"
FUTURE = "2099-01-01T00:00:00Z"
PASSWORD = "pw-for-a-throwaway-db"
_ids = itertools.count(1)
SERVER = {}


def _port():
    for _ in range(20):
        port = surreal_bin.free_port()
        if not any(port in r for r in LIVE_PORTS):
            return port
    raise RuntimeError("no free port outside the live ranges")


def setUpModule():
    if not surreal_bin.find():
        raise unittest.SkipTest("NOT EXECUTED: " + surreal_bin.MISSING)
    port = _port()
    SERVER["proc"] = surreal_bin.start_memory(port)
    SERVER["url"] = "http://127.0.0.1:%d/sql" % port


def tearDownModule():
    surreal_bin.stop(SERVER.get("proc"))


def open_project(slug):
    root = model.open_namespace(SERVER["url"], model.project_ns(slug), schema_dir=SCHEMA_DIR)
    for member, role in (("lead1", "lead"), ("worker1", "worker"), ("reviewer1", "reviewer")):
        model.provision(root, member, role, PASSWORD)
    return root


def as_member(ns, member):
    return model.connect(SERVER["url"], ns, member=member, password=PASSWORD)


def attempt(db, statement):
    """('ok'|'refused'|'denied', message). refused = an ASSERT/READONLY error; denied = PERMISSIONS."""
    try:
        ok, why = db.try_sql(statement)
    except dbmod.PermissionDenied as exc:
        return "denied", str(exc)
    return ("ok" if ok else "refused"), why


class Checks(unittest.TestCase):
    def assert_refused(self, result, ticket=None):
        kind, why = result
        self.assertNotEqual(kind, "ok", "write was ACCEPTED; the rule did not refuse it")
        if ticket:
            self.assertNotEqual(self.status(ticket), "done", "refused, yet the row says done")
        return why

    def assert_ok(self, result):
        kind, why = result
        self.assertEqual(kind, "ok", why)


class Base(Checks):
    slug = "main"

    @classmethod
    def setUpClass(cls):
        cls.root = open_project(cls.slug)
        cls.ns = cls.root.ns
        cls.worker = as_member(cls.ns, "worker1")
        cls.reviewer = as_member(cls.ns, "reviewer1")
        cls.lead = as_member(cls.ns, "lead1")

    def one(self, statement, db=None):
        rows = (db or self.root).sql(statement)[-1]
        return rows[0] if isinstance(rows, list) and rows else rows

    def ticket(self, status="review", last=COMMIT_AT):
        key = "t%d" % next(_ids)
        self.root.sql("CREATE ticket:%s CONTENT {ticket_id: '%s', title: 'x', status: '%s', last_worker_commit_at: d'%s'}"
                      % (key, key, status, last))
        return key

    def gate_run(self, ticket, verdict="pass", role="reviewer", at=AFTER):
        key = "g%d" % next(_ids)
        self.root.sql("CREATE gate_run:%s CONTENT {ticket: ticket:%s, command: 'gate', verdict: '%s', run_role: '%s', "
                      "ran_at: d'%s'}" % (key, ticket, verdict, role, at))
        return key

    def status(self, ticket):
        return self.one("SELECT VALUE status FROM ticket:%s" % ticket)


# ---- R1 stale propagation ---------------------------------------------------------------------------------
class StalePropagation(Base):
    def unit_with_facts(self):
        n = next(_ids)
        self.root.sql("CREATE unit:u%d SET locator = 'src/a.rs#L%d', hash = 'h1';"
                      "CREATE fact:f%d SET text = 'cites it';"
                      "CREATE fact:o%d SET text = 'cites nothing';"
                      "RELATE fact:f%d->evidence->unit:u%d SET hash_at_cite = 'h1';" % (n, n, n, n, n, n))
        return n

    def statuses(self, n):
        return (self.one("SELECT VALUE status FROM fact:f%d" % n), self.one("SELECT VALUE status FROM fact:o%d" % n))

    def test_same_hash_keeps_fact_live_then_changed_hash_marks_it_stale_never_deletes(self):
        n = self.unit_with_facts()
        facts_before = self.one("RETURN count(SELECT id FROM fact)")
        self.assert_ok(attempt(self.root, "UPDATE unit:u%d SET hash = 'h1', kind = 'code'" % n))
        self.assertEqual(self.statuses(n), ("live", "live"), "a write that did not change the hash staled a fact")
        self.assert_ok(attempt(self.root, "UPDATE unit:u%d SET hash = 'h2'" % n))
        self.assertEqual(self.statuses(n), ("stale", "live"))
        self.assertEqual(self.one("RETURN count(SELECT id FROM fact)"), facts_before, "stale propagation deleted a fact")
        self.assertEqual(self.one("RETURN count(SELECT id FROM evidence WHERE out = unit:u%d)" % n), 1)

    def test_a_worker_changing_the_hash_stales_facts_it_may_not_write_itself(self):
        n = self.unit_with_facts()
        self.assert_ok(attempt(self.worker, "UPDATE unit:u%d SET hash = 'h1'" % n))
        self.assertEqual(self.statuses(n)[0], "live")
        self.assert_ok(attempt(self.worker, "UPSERT unit:u%d SET locator = 'src/a.rs#L%d', hash = 'h9'" % (n, n)))
        self.assertEqual(self.statuses(n), ("stale", "live"))

    def test_no_member_can_delete_a_fact_but_may_update_it(self):
        n = self.unit_with_facts()
        for who in (self.worker, self.reviewer, self.lead):
            self.assertEqual(attempt(who, "DELETE fact:f%d RETURN BEFORE" % n)[0], "denied")
        self.assertEqual(self.one("RETURN count(SELECT id FROM fact:f%d)" % n), 1)
        self.assert_ok(attempt(self.worker, "UPDATE fact:f%d SET grade = 'INFERRED'" % n))


# ---- R2 done needs a fresh reviewer pass -----------------------------------------------------------------
class DoneGate(Base):
    def done(self, ticket, db=None):
        return attempt(db or self.root, "UPDATE ticket:%s SET status = 'done'" % ticket)

    def test_refuses_done_with_no_gate_run_then_accepts_with_fresh_reviewer_pass(self):
        t = self.ticket()
        self.assert_refused(self.done(t), t)
        self.gate_run(t)
        self.assert_ok(self.done(t))
        self.assertEqual(self.status(t), "done")

    def test_refuses_born_done_by_create_upsert_and_insert_then_accepts_normal_create(self):
        for verb in ("CREATE ticket:%s CONTENT", "UPSERT ticket:%s CONTENT", "INSERT INTO ticket"):
            key = "bd%d" % next(_ids)
            body = "{ticket_id: '%s', title: 'x', status: 'done', last_worker_commit_at: d'%s'}" % (key, COMMIT_AT)
            stmt = (verb % key + " " + body) if "%s" in verb else "INSERT INTO ticket {id: ticket:%s, %s" % (key, body[1:])
            self.assert_refused(attempt(self.root, stmt))
            self.assertEqual(self.one("RETURN count(SELECT id FROM ticket:%s)" % key), 0, verb)
        self.ticket(status="ready")

    def test_refuses_worker_pass_then_accepts_reviewer_pass(self):
        t = self.ticket()
        self.gate_run(t, role="worker")
        self.assert_refused(self.done(t), t)
        self.gate_run(t, role="reviewer")
        self.assert_ok(self.done(t))

    def test_refuses_reviewer_fail_then_accepts_reviewer_pass(self):
        t = self.ticket()
        self.gate_run(t, verdict="fail")
        self.assert_refused(self.done(t), t)
        self.gate_run(t, verdict="pass")
        self.assert_ok(self.done(t))

    def test_refuses_stale_and_equal_time_pass_then_accepts_newer(self):
        t = self.ticket()
        self.gate_run(t, at=BEFORE)
        self.gate_run(t, at=COMMIT_AT)          # "newer than", not "not older than"
        self.assert_refused(self.done(t), t)
        self.gate_run(t, at=AFTER)
        self.assert_ok(self.done(t))

    def test_pass_on_another_ticket_does_not_count(self):
        t, other = self.ticket(), self.ticket()
        self.gate_run(other)
        self.assert_refused(self.done(t), t)
        self.gate_run(t)
        self.assert_ok(self.done(t))

    def test_refuses_future_dated_gate_run_then_accepts_past_dated(self):
        t = self.ticket()
        for db in (self.root, self.reviewer):
            result = attempt(db, "CREATE gate_run CONTENT {ticket: ticket:%s, command: 'gate', verdict: 'pass', "
                                 "run_role: 'reviewer', ran_at: d'%s'}" % (t, FUTURE))
            self.assert_refused(result)
        self.assertEqual(self.one("RETURN count(SELECT id FROM gate_run WHERE ticket = ticket:%s)" % t), 0)
        self.assert_refused(self.done(t), t)
        self.assert_ok(attempt(self.reviewer, "CREATE gate_run CONTENT {ticket: ticket:%s, command: 'gate', verdict: 'pass', "
                                              "run_role: 'reviewer', ran_at: d'%s'}" % (t, AFTER)))
        self.assert_ok(self.done(t, self.reviewer))

    def test_refuses_rewinding_last_commit_then_accepts_moving_it_forward(self):
        t = self.ticket()
        self.gate_run(t, at=BEFORE)
        self.assert_refused(attempt(self.root, "UPDATE ticket:%s SET status = 'done', last_worker_commit_at = d'%s'" % (t, REWIND)), t)
        self.assert_refused(attempt(self.worker, "UPDATE ticket:%s SET last_worker_commit_at = d'%s'" % (t, REWIND)))
        self.assert_ok(attempt(self.worker, "UPDATE ticket:%s SET last_worker_commit_at = d'%s'" % (t, AFTER)))

    def test_a_newer_commit_on_a_done_ticket_is_refused_until_it_is_reopened(self):
        t = self.ticket()
        self.gate_run(t, at=AFTER)
        self.assert_ok(self.done(t))
        self.assert_refused(attempt(self.root, "UPDATE ticket:%s SET last_worker_commit_at = d'%s'" % (t, LATER)))
        self.assertEqual(self.status(t), "done")
        self.assert_ok(attempt(self.worker, "UPDATE ticket:%s SET status = 'review', last_worker_commit_at = d'%s'" % (t, LATER)))
        self.assert_refused(self.done(t), t)

    def test_a_gate_run_cannot_be_edited_or_deleted_by_anyone_but_is_created_by_its_role(self):
        t = self.ticket()
        g, other = self.gate_run(t, verdict="fail", role="worker", at=BEFORE), self.ticket()
        for edit in ("verdict = 'pass'", "run_role = 'reviewer'", "ran_at = d'%s'" % AFTER, "ticket = ticket:%s" % other):
            self.assert_refused(attempt(self.root, "UPDATE gate_run:%s SET %s" % (g, edit)))
        for who in (self.worker, self.reviewer, self.lead):
            self.assertEqual(attempt(who, "UPDATE gate_run:%s SET verdict = 'pass'" % g)[0], "denied")
            self.assertEqual(attempt(who, "DELETE gate_run:%s RETURN BEFORE" % g)[0], "denied")
        row = self.one("SELECT verdict, run_role FROM gate_run:%s" % g)
        self.assertEqual((row["verdict"], row["run_role"]), ("fail", "worker"))
        self.assert_refused(self.done(t), t)


# ---- R3 roles are credentials ------------------------------------------------------------------------------
class Roles(Base):
    def write_run(self, db, t, role, verdict="pass", extra=""):
        return attempt(db, "CREATE gate_run CONTENT {ticket: ticket:%s, command: 'gate', verdict: '%s', run_role: '%s'%s}"
                       % (t, verdict, role, extra))

    def test_worker_cannot_write_a_reviewer_gate_run_but_can_write_its_own(self):
        t = self.ticket()
        self.assert_refused(self.write_run(self.worker, t, "reviewer"))
        self.assertEqual(self.one("RETURN count(SELECT id FROM gate_run WHERE ticket = ticket:%s)" % t), 0)
        self.assert_ok(self.write_run(self.worker, t, "worker"))
        row = self.one("SELECT run_role, ran_by FROM gate_run WHERE ticket = ticket:%s" % t)
        self.assertEqual((row["run_role"], row["ran_by"]), ("worker", "member:worker1"))

    def test_reviewer_credential_writes_a_reviewer_run_and_cannot_forge_who_ran_it(self):
        t = self.ticket()
        self.assert_refused(self.write_run(self.reviewer, t, "worker"))
        self.assert_refused(self.write_run(self.reviewer, t, "reviewer", extra=", ran_by: member:worker1"))
        self.assert_ok(self.write_run(self.reviewer, t, "reviewer"))
        self.assertEqual(self.one("SELECT VALUE ran_by FROM gate_run WHERE ticket = ticket:%s" % t), "member:reviewer1")

    def test_lead_cannot_write_gate_runs(self):
        t = self.ticket()
        self.assert_refused(self.write_run(self.lead, t, "reviewer"))
        self.assertEqual(self.one("RETURN count(SELECT id FROM gate_run WHERE ticket = ticket:%s)" % t), 0)

    def test_worker_cannot_set_done_even_with_fresh_reviewer_pass_reviewer_can(self):
        t = self.ticket()
        self.assert_ok(self.write_run(self.reviewer, t, "reviewer"))
        self.assert_refused(attempt(self.worker, "UPDATE ticket:%s SET status = 'done'" % t), t)
        self.assert_ok(attempt(self.reviewer, "UPDATE ticket:%s SET status = 'done'" % t))
        self.assertEqual(self.status(t), "done")

    def test_lead_sets_done_with_evidence(self):
        t = self.ticket()
        self.assert_ok(self.write_run(self.reviewer, t, "reviewer"))
        self.assert_ok(attempt(self.lead, "UPDATE ticket:%s SET status = 'done'" % t))

    def test_worker_cannot_create_tickets_lead_can(self):
        self.assertEqual(attempt(self.worker, "CREATE ticket CONTENT {ticket_id: 'w%d', title: 'x', last_worker_commit_at: d'%s'}"
                                 % (next(_ids), COMMIT_AT))[0], "denied")
        self.assert_ok(attempt(self.lead, "CREATE ticket CONTENT {ticket_id: 'l%d', title: 'x', last_worker_commit_at: d'%s'}"
                               % (next(_ids), COMMIT_AT)))

    def test_member_cannot_promote_itself_or_read_other_members(self):
        self.assertEqual(attempt(self.worker, "UPDATE member:worker1 SET role = 'reviewer'")[0], "denied")
        self.assertEqual(self.one("SELECT VALUE role FROM member:worker1"), "worker")
        self.assertEqual(self.worker.sql("SELECT VALUE record::id(id) FROM member")[0], ["worker1"])
        self.assertEqual(self.worker.sql("SELECT VALUE pass FROM member:worker1")[0], [None])
        # the refusal was the role, not a broken credential: the same member signs in again and reads its row
        self.assertEqual(as_member(self.ns, "worker1").sql("SELECT VALUE role FROM member:worker1")[0], ["worker"])

    def test_only_reviewer_writes_review(self):
        t = self.ticket()
        self.assertEqual(attempt(self.worker, "CREATE review CONTENT {ticket: ticket:%s, verdict: 'approved'}" % t)[0], "denied")
        self.assert_ok(attempt(self.reviewer, "CREATE review CONTENT {ticket: ticket:%s, verdict: 'approved'}" % t))


# ---- namespaces ----------------------------------------------------------------------------------------
class Namespaces(Checks):
    @classmethod
    def setUpClass(cls):
        cls.a, cls.b = open_project("iso_a"), open_project("iso_b")
        cls.b.sql("CREATE unit:secret SET locator = 'b.txt', hash = 'hb'")
        cls.a.sql("CREATE unit:mine SET locator = 'a.txt', hash = 'ha'")
        cls.ws = model.open_namespace(SERVER["url"], model.WORKSPACE_NS, schema_dir=SCHEMA_DIR)
        model.provision(cls.ws, "lead1", "lead", PASSWORD)
        model.provision(cls.ws, "worker1", "worker", PASSWORD)

    def test_proj_a_credential_reads_proj_a_but_not_proj_b(self):
        cred = as_member(self.a.ns, "worker1")
        self.assertEqual(cred.sql("SELECT VALUE locator FROM unit:mine")[0], ["a.txt"])
        blocks = cred._post("USE NS %s DB %s; SELECT * FROM unit:secret;" % (self.b.ns, model.DB_NAME))
        self.assertTrue(any(b.get("status") != "OK" for b in blocks), blocks)
        self.assertNotIn("b.txt", str(blocks))

    def test_proj_a_password_does_not_sign_in_to_proj_b_as_a_member_that_only_exists_in_a(self):
        model.provision(self.a, "only_in_a", "reviewer", PASSWORD)
        self.assertEqual(as_member(self.a.ns, "only_in_a").sql("RETURN 1")[0], 1)
        with self.assertRaises(dbmod.DbError):
            as_member(self.b.ns, "only_in_a").sql("RETURN 1")

    def test_project_tables_are_not_in_the_workspace_and_registry_is_not_in_projects(self):
        ws_tables = set(self.ws.sql("INFO FOR DB")[0]["tables"])
        proj_tables = set(self.a.sql("INFO FOR DB")[0]["tables"])
        self.assertTrue({"project", "repo", "worktree", "branch", "service", "gate_def", "area", "expert",
                         "dead_end", "decision", "cost_row"} <= ws_tables, ws_tables)
        self.assertTrue({"unit", "fact", "evidence", "dead_end", "ticket", "gate_run", "review"} <= proj_tables, proj_tables)
        self.assertFalse({"ticket", "gate_run", "unit", "fact"} & ws_tables)
        self.assertFalse({"project", "repo", "cost_row"} & proj_tables)

    def test_workspace_worker_cannot_register_a_project_lead_can_and_worker_files_cost(self):
        worker, lead = as_member(model.WORKSPACE_NS, "worker1"), as_member(model.WORKSPACE_NS, "lead1")
        self.assertEqual(attempt(worker, "CREATE project CONTENT {slug: 'x1', name: 'x', root: 'C:/x'}")[0], "denied")
        self.assert_ok(attempt(lead, "CREATE project CONTENT {slug: 'x1', name: 'x', root: 'C:/x'}"))
        self.assertEqual(self.ws.sql("SELECT VALUE namespace FROM project WHERE slug = 'x1'")[0], ["proj_x1"])
        self.assert_ok(attempt(worker, "CREATE cost_row CONTENT {model: 'sonnet', usd: 0.5}"))


# ---- migrations --------------------------------------------------------------------------------------------
class Migrations(unittest.TestCase):
    def fresh(self):
        root = model.connect(SERVER["url"], model.project_ns("mig%d" % next(_ids)))
        model.ensure(root)
        return root

    def test_migrate_twice_is_a_no_op(self):
        root = self.fresh()
        first = model.migrate(root, schema_dir=SCHEMA_DIR)
        self.assertEqual(first, [n for n, _, _ in model.migrations("project", SCHEMA_DIR)])
        info, version = root.sql("INFO FOR DB")[0], model.current_version(root)
        self.assertEqual(model.migrate(root, schema_dir=SCHEMA_DIR), [])
        self.assertEqual(root.sql("INFO FOR DB")[0], info)
        self.assertEqual(model.current_version(root), version)
        self.assertEqual(version["major"], model.SCHEMA_MAJOR)
        self.assertEqual(version["applied"], first[-1])

    def test_rerunning_every_file_by_hand_changes_no_definition(self):
        root = self.fresh()
        model.migrate(root, schema_dir=SCHEMA_DIR)
        model.provision(root, "r", "reviewer", PASSWORD)
        info = root.sql("INFO FOR DB")[0]
        for _, _, text in model.migrations("project", SCHEMA_DIR):
            root.sql(text)
        self.assertEqual(root.sql("INFO FOR DB")[0], info, "a re-applied file changed a definition (permissions?)")
        self.assertEqual(as_member(root.ns, "r").sql("SELECT VALUE role FROM member:r")[0], ["reviewer"])

    def test_wrong_schema_major_is_refused_before_any_migration_then_right_major_accepted(self):
        root = self.fresh()
        model.current_version(root)
        root.sql("UPSERT schema_version:current CONTENT {product: '%s', scope: 'project', major: %d, applied: 0, "
                 "files: [], updated_at: time::now()}" % (model.PRODUCT, model.SCHEMA_MAJOR + 1))
        with self.assertRaises(model.WrongMajor):
            model.migrate(root, schema_dir=SCHEMA_DIR)
        self.assertNotIn("ticket", root.sql("INFO FOR DB")[0]["tables"], "a migration ran on a wrong-major database")
        root.sql("UPDATE schema_version:current SET major = %d" % model.SCHEMA_MAJOR)
        self.assertTrue(model.migrate(root, schema_dir=SCHEMA_DIR))
        self.assertIn("ticket", root.sql("INFO FOR DB")[0]["tables"])

    def test_wrong_server_major_is_refused_right_one_accepted(self):
        for text in ("surrealdb-4.0.0", "surrealdb-2.3.1", "no version here"):
            with self.assertRaises(model.WrongMajor):
                model.check_server(text)
        model.check_server("surrealdb-3.1.6+20260813.cfbaec4")
        model.check_server(self.fresh().version())

    def test_workspace_files_refuse_a_project_namespace(self):
        with self.assertRaises(model.ModelError):
            model.migrate(self.fresh(), scope="workspace", schema_dir=SCHEMA_DIR)

    def test_a_failing_migration_leaves_the_version_where_it_was(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        try:
            for _, name, _ in model.migrations("project", SCHEMA_DIR):
                shutil.copy(pathlib.Path(SCHEMA_DIR or model.SCHEMA_DIR) / name, tmp / name)
            (tmp / "0900_broken.surql").write_text("-- scope: project\nDEFINE TABLE IF NOT EXISTS half SCHEMAFULL;\n"
                                                   "THROW 'a runtime failure after a definition';\n", encoding="utf-8")
            root = self.fresh()
            with self.assertRaises(model.ModelError):
                model.migrate(root, schema_dir=tmp)
            version = model.current_version(root)
            self.assertEqual(version["applied"], model.migrations("project", SCHEMA_DIR)[-1][0])
            self.assertNotIn("half", root.sql("INFO FOR DB")[0]["tables"], "a failed migration left part of itself")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
