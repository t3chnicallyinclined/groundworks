# -*- coding: utf-8 -*-
"""Role skills from config (xscholar/skills.py) and the drift guard over them (xscholar/drift.py).

Pure Python: no datastore, no server, no network. Both ways throughout -- every guard is shown to
pass on a clean tree and to FAIL, naming the file and the item, on a planted mismatch:

  generation is idempotent (twice, into two trees, byte for byte)
  every template renders, and each config item lands in the skill that uses it
  renaming a seat in the config fails the check, naming that seat
  editing a generated file fails the hash check, naming that file; restoring it passes
  a role whose skill has no template is an error naming the role
  a doc naming a lens the config does not define is drift
  the committed demo skills are current (this is the check CI runs)

    python -m unittest tests.test_skills_drift -v
"""
import copy
import pathlib
import re
import shutil
import tempfile
import unittest

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config, drift, gates, skills  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DEMO = ROOT / "instances" / "demo-incident" / "instance.yaml"
DEMO_SKILLS = ROOT / "instances" / "demo-incident" / "skills"

# One role per template, so every template in the repository is rendered by the suite.
EXTRA_ROLES = [
    {"skill": "cold-bench", "lenses": ["management"],
     "panel": {"options": ["persuasive", "unclear", "unpersuasive"],
               "objection_categories": ["clarity", "support", "scope"], "weighted_seat": "seat_management"}},
    {"skill": "premortem", "lenses": ["service_owner", "security"],
     "panel": {"options": ["holds", "at_risk", "lost"],
               "objection_categories": ["coverage", "reasoning", "process"], "weighted_seat": None}},
    {"skill": "claim-audit", "lenses": ["security"]},
    {"skill": "reader", "lenses": []},
]


def demo_data():
    return copy.deepcopy(config.load(DEMO).data)


def full_data():
    """The demo instance with one role per template."""
    data = demo_data()
    data["roles"] = data["roles"] + copy.deepcopy(EXTRA_ROLES)
    return data


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_instance(self, data, name="instance.json"):
        import json
        path = self.tmp / name
        path.write_text(json.dumps(data), encoding="utf-8")
        # a config loaded from a file must have its declared regression file beside it (xscholar/config.py)
        regression = self.tmp / data["evaluator"]["regression_file"]
        regression.parent.mkdir(parents=True, exist_ok=True)
        regression.write_text('{"format": "xscholar.known_errors/1", "entries": []}', encoding="utf-8")
        return path

    def build(self, data=None, sub="skills"):
        """Write a config and generate its skills. Returns (instance path, skills dir)."""
        path = self.write_instance(data if data is not None else full_data())
        out = self.tmp / sub
        skills.generate(config.load(path), out)
        return path, out

    def drifted(self, report):
        return [(pathlib.Path(r["file"]).name, r["item"]) for r in report.items()]


# ---- generation ------------------------------------------------------------------------------------

class GenerateTests(Base):
    def test_generation_is_idempotent(self):
        path = self.write_instance(full_data())
        first = {p.parent.name: p.read_bytes() for p in skills.generate(config.load(path), self.tmp / "a")}
        second = {p.parent.name: p.read_bytes() for p in skills.generate(config.load(path), self.tmp / "b")}
        self.assertEqual(sorted(first), sorted(second))
        for name in first:
            self.assertEqual(first[name], second[name], "%s differs between two generations" % name)
        # and regenerating over an existing tree changes nothing
        again = {p.parent.name: p.read_bytes() for p in skills.generate(config.load(path), self.tmp / "a")}
        self.assertEqual(first, again)

    def test_every_template_has_a_role_in_this_suite(self):
        covered = {"red-team"} | {r["skill"] for r in EXTRA_ROLES}
        self.assertEqual(set(skills.template_names()), covered,
                         "a template nothing in this suite renders is a template nothing tests")

    def test_every_template_renders_with_the_required_placeholders(self):
        _path, out = self.build()
        for name in skills.template_names():
            text = (out / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("{{", text, "%s still holds an unfilled placeholder" % name)
            self.assertIn(skills.GENERATED_LINE % "demo_incident", text)
            self.assertIsNotNone(skills.stated_hash(text), "%s carries no content hash" % name)
            self.assertGreater(len(text), 3000, "%s rendered suspiciously short" % name)

    def test_header_hash_is_over_the_file_content(self):
        _path, out = self.build()
        text = (out / "red-team" / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(skills.stated_hash(text), skills.content_hash(text))
        self.assertNotEqual(skills.stated_hash(text), skills.content_hash(text + "one more word\n"))

    def test_tool_tables_cover_every_mcp_tool(self):
        """Read TOOL_NAMES out of the server source, so this test needs no MCP SDK installed."""
        source = (ROOT / "xscholar" / "mcp" / "server.py").read_text(encoding="utf-8")
        block = re.search(r"TOOL_NAMES = \((.*?)\)\n", source, re.S).group(1)
        declared = set(re.findall(r'"([a-z_]+)"', block))
        self.assertTrue(declared)
        self.assertEqual(declared, set(skills.TOOL_TABLES),
                         "xscholar/mcp/server.py TOOL_NAMES and skills.TOOL_TABLES disagree")
        self.assertEqual(declared, set(skills.TOOL_PURPOSE))


class ContentTests(Base):
    def setUp(self):
        super().setUp()
        path, self.out = self.build()
        self.inst = config.load(path)
        self.text = {name: (self.out / name / "SKILL.md").read_text(encoding="utf-8")
                     for name in skills.template_names()}

    def test_goal_holder_and_off_limits_land_in_every_skill(self):
        for name, text in self.text.items():
            self.assertIn("Establish the root cause of the checkout outage", text, name)
            self.assertIn("the service team", text, name)
            self.assertIn("customer personal data", text, name)

    def test_vocabulary_lands_in_every_skill(self):
        for name, text in self.text.items():
            for word in ("for_goal", "against_goal", "saturation", "rc_pool_exhaustion", "warning",
                         "original_log", "in_window"):
                self.assertIn(word, text, "%s is missing vocabulary word %s" % (name, word))

    def test_each_role_gets_its_own_panel_options_and_categories(self):
        self.assertIn("`survives`", self.text["red-team"])
        self.assertIn("`blast_radius`", self.text["red-team"])
        self.assertIn("`persuasive`", self.text["cold-bench"])
        self.assertIn("`clarity`", self.text["cold-bench"])
        self.assertNotIn("persuasive", self.text["red-team"])
        self.assertNotIn("blast_radius", self.text["cold-bench"])
        self.assertIn("`seat_management` (base weight 1.5)", self.text["cold-bench"])
        self.assertIn("weighted_seat: null", self.text["premortem"])

    def test_a_role_without_a_panel_says_so_and_carries_no_options(self):
        text = self.text["claim-audit"]
        self.assertIn("has no `panel:` entry", text)
        self.assertNotIn("survives", text)

    def test_each_role_gets_only_its_own_seats(self):
        self.assertIn("`seat_service_owner`", self.text["red-team"])
        self.assertIn("`seat_security`", self.text["red-team"])
        self.assertNotIn("`seat_service_owner`", self.text["cold-bench"])
        self.assertIn("`seat_management`", self.text["cold-bench"])
        self.assertIn("This role seats no lens", self.text["reader"])

    def test_every_lens_is_listed_in_every_skill_even_when_not_seated(self):
        for name, text in self.text.items():
            for lens in ("service_owner", "security", "management", "incident_framework"):
                self.assertIn(lens, text, "%s never names lens %s" % (name, lens))

    def test_a_prompt_backed_lens_says_it_can_never_promote(self):
        text = self.text["red-team"]
        self.assertIn("Prompt-backed lenses", text)
        self.assertIn("never** support the promotion of a claim", text)
        self.assertIn("Charters not signed by a human", text)

    def test_gate_clauses_are_in_plain_words_in_every_skill(self):
        for name, text in self.text.items():
            self.assertIn("confirmed_needs_source_grade", text, name)
            self.assertIn("root_cause_needs_support_and_attack", text, name)
            self.assertIn("`fact.status` may become `confirmed` only when the row has at least 1 "
                          "`cites` edge to a `unit` row where", text, name)
            self.assertIn("`conclusion.ready` may become `true`", text, name)   # true, not Python's True
            self.assertIn("has no `attacks` edge from a `critique` row where `verdict = 'fatal'`", text, name)
            self.assertIn(gates._lit(self.inst.confirming_grades()), text, name)   # $confirming expanded
            # a clause on an edge the CONFIG declared reads like any other, with no engine change
            self.assertIn("has at least 1 `preserves` edge from a `unit` row", text, name)
            self.assertIn("`authority.verified` may become `true` only when the row has at least 1 "
                          "`verified_from` edge to a `unit` row where `grade = 'official_text'", text, name)

    def test_the_panel_command_sequence_is_exact_and_carries_settings(self):
        text = self.text["red-team"]
        self.assertIn("python xscholar/panel/panel.py lock      runs/<panel_id> "
                      "--settings <instance dir>/panel_settings.json", text)
        for command in ("anonymize", "check", "tally"):
            self.assertIn("python xscholar/panel/panel.py %-9s runs/<panel_id>" % command, text)

    def test_tools_are_filtered_by_the_access_grant(self):
        seat = self.text["red-team"]          # access role `seat`: writes critique, attacks, answer, answers
        self.assertIn("Launch this role as the `seat` credential", seat)
        self.assertIn("| `critique_add` |", seat)
        self.assertIn("| `lens_view` |", seat)
        self.assertNotIn("| `claim_confirm` |", seat)
        self.assertIn("Not yours under this credential", seat)
        self.assertIn("`claim_confirm`", seat)                 # named as refused, not as available
        reader = self.text["reader"]          # access role `reader`: writes fact, cites, supports
        self.assertIn("Launch this role as the `reader` credential", reader)
        self.assertIn("| `claim_confirm` |", reader)
        self.assertNotIn("| `critique_add` |", reader)
        coord = self.text["premortem"]        # access role `coordinator`: writes everything but label
        self.assertIn("| `conclusion_mark_ready` |", coord)

    def test_a_config_without_access_grants_every_tool(self):
        data = full_data()
        del data["access"]
        _path, out = self.build(data, sub="noaccess")
        text = (out / "red-team" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("declares no `access:` section", text)
        for tool in skills.TOOL_TABLES:
            self.assertIn("| `%s` |" % tool, text)

    def test_privacy_rules_land_in_every_skill(self):
        for name, text in self.text.items():
            self.assertIn("~/.xscholar/denylist.txt", text, name)
            self.assertIn("the model provider the owner approved", text, name)
            self.assertIn("`privacy` is human-owned", text, name)


# ---- errors ----------------------------------------------------------------------------------------

class ErrorTests(Base):
    def test_a_role_whose_skill_has_no_template_is_an_error_naming_it(self):
        data = demo_data()
        data["roles"] = data["roles"] + [{"skill": "cross-examine", "lenses": ["security"]}]
        path = self.write_instance(data)
        with self.assertRaises(skills.SkillError) as caught:
            skills.generate(config.load(path), self.tmp / "skills")
        message = str(caught.exception)
        self.assertIn("cross-examine", message)
        self.assertIn("roles[1]", message)
        self.assertIn("red-team", message)              # the templates that do exist are named
        self.assertFalse((self.tmp / "skills" / "cross-examine").exists())

    def test_the_drift_check_refuses_to_run_on_an_untemplated_role(self):
        data = demo_data()
        data["roles"] = data["roles"] + [{"skill": "cross-examine", "lenses": ["security"]}]
        path = self.write_instance(data)
        with self.assertRaises(drift.DriftError) as caught:
            drift.check(path, self.tmp / "skills")
        self.assertIn("cross-examine", str(caught.exception))

    def test_a_role_with_no_skill_name_is_an_error(self):
        data = demo_data()
        data["roles"] = [{"lenses": ["security"]}]
        path = self.write_instance(data)
        with self.assertRaises(skills.SkillError) as caught:
            skills.generate(config.load(path), self.tmp / "skills")
        self.assertIn("roles[0]", str(caught.exception))

    def test_an_instance_with_no_roles_is_an_error(self):
        data = demo_data()
        data["roles"] = []
        path = self.write_instance(data)
        with self.assertRaises(skills.SkillError) as caught:
            skills.generate(config.load(path), self.tmp / "skills")
        self.assertIn("no roles", str(caught.exception))

    def test_a_role_naming_an_unknown_lens_is_an_error_naming_it(self):
        data = demo_data()
        data["roles"][0]["lenses"] = ["service_owner", "no_such_lens"]
        path = self.write_instance(data)
        with self.assertRaises(skills.SkillError) as caught:
            skills.generate(config.load(path), self.tmp / "skills")
        self.assertIn("no_such_lens", str(caught.exception))

    def test_a_role_naming_an_unknown_access_role_is_an_error_naming_it(self):
        data = demo_data()
        data["roles"][0]["access_role"] = "auditor"
        path = self.write_instance(data)
        with self.assertRaises(skills.SkillError) as caught:
            skills.generate(config.load(path), self.tmp / "skills")
        self.assertIn("auditor", str(caught.exception))
        self.assertIn("coordinator", str(caught.exception))     # the roles that do exist are named


# ---- the drift guard, both ways ---------------------------------------------------------------------

class DriftTests(Base):
    def test_a_freshly_generated_tree_is_clean(self):
        path, out = self.build()
        report = drift.check(path, out)
        self.assertTrue(report.ok, str(report))
        self.assertEqual(report.checked["skills"], 5)
        self.assertGreater(report.checked["items"], 40)

    def test_renaming_a_seat_in_the_config_fails_the_check_naming_that_seat(self):
        path, out = self.build()
        data = full_data()
        for lens in data["lenses"]:
            if lens["name"] == "service_owner":
                lens["name"] = "service_owner_v2"
        for role in data["roles"]:
            role["lenses"] = ["service_owner_v2" if n == "service_owner" else n for n in role["lenses"]]
        renamed = self.write_instance(data, "renamed.json")
        report = drift.check(renamed, out)
        self.assertFalse(report.ok)
        items = {item for _file, item in self.drifted(report)}
        self.assertIn("seat_service_owner_v2", items)
        self.assertIn("lens_service_owner_v2", items)
        self.assertIn("service_owner_v2", items)
        for row in report.items():
            if row["item"] == "seat_service_owner_v2":
                self.assertIn("red-team", row["file"])
                self.assertIn("never names it", row["why"])
                break
        else:                                             # pragma: no cover - guarded by the assertIn above
            self.fail("no row named the renamed seat")
        # and the guard passes again once the skills are regenerated from the renamed config
        skills.generate(config.load(renamed), out)
        self.assertTrue(drift.check(renamed, out).ok)

    def test_renaming_a_panel_option_fails_the_check_naming_the_option(self):
        path, out = self.build()
        data = full_data()
        data["roles"][0]["panel"]["options"] = ["survives", "damaged", "destroyed"]
        changed = self.write_instance(data, "changed.json")
        report = drift.check(changed, out)
        self.assertIn("destroyed", {item for _f, item in self.drifted(report)})

    def test_editing_a_generated_file_fails_the_hash_check_naming_the_file(self):
        path, out = self.build()
        target = out / "premortem" / "SKILL.md"
        original = target.read_text(encoding="utf-8")
        target.write_text(original.replace("Rank** every open scenario", "Rank** the comfortable scenarios"),
                          encoding="utf-8", newline="\n")
        report = drift.check(path, out)
        self.assertFalse(report.ok)
        rows = [r for r in report.items() if r["item"] == "content-sha256"]
        self.assertEqual(len(rows), 1, str(report))
        self.assertIn("premortem", rows[0]["file"])
        self.assertIn("edited by hand", rows[0]["why"])
        # restoring the file restores the pass
        target.write_text(original, encoding="utf-8", newline="\n")
        self.assertTrue(drift.check(path, out).ok, str(drift.check(path, out)))

    def test_a_stale_file_whose_hash_is_self_consistent_is_still_drift(self):
        path, out = self.build()
        data = full_data()
        data["instance"]["holder"] = "the platform team"
        changed = self.write_instance(data, "holder.json")
        report = drift.check(changed, out)
        self.assertFalse(report.ok)
        whys = [r["why"] for r in report.items() if r["item"] == "content-sha256"]
        self.assertEqual(len(whys), 5)
        self.assertTrue(all("stale" in w for w in whys), whys)

    def test_a_missing_skill_file_is_drift_naming_it(self):
        path, out = self.build()
        (out / "reader" / "SKILL.md").unlink()
        report = drift.check(path, out)
        rows = [r for r in report.items() if r["item"] == "skill reader"]
        self.assertEqual(len(rows), 1, str(report))
        self.assertIn("missing", rows[0]["why"])

    def test_a_skill_file_no_role_generates_is_drift(self):
        path, out = self.build()
        orphan = out / "cross-examine"
        orphan.mkdir()
        (orphan / "SKILL.md").write_text("stale skill\n", encoding="utf-8", newline="\n")
        report = drift.check(path, out)
        rows = [r for r in report.items() if r["item"] == "skill cross-examine"]
        self.assertEqual(len(rows), 1, str(report))
        self.assertIn("no role in the config generates this skill", rows[0]["why"])

    def test_a_file_with_no_hash_line_is_drift(self):
        path, out = self.build()
        target = out / "reader" / "SKILL.md"
        text = target.read_text(encoding="utf-8")
        target.write_text(skills.HASH_RE.sub("", text, count=1), encoding="utf-8", newline="\n")
        report = drift.check(path, out)
        self.assertIn("states no content hash",
                      " ".join(r["why"] for r in report.items() if r["item"] == "content-sha256"))


class DocTests(Base):
    def doc(self, text, name="NOTES.md"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def test_a_doc_naming_only_known_lenses_and_seats_is_clean(self):
        path, out = self.build()
        doc = self.doc("The red team seats seat_security against lens_service_owner and lens_management.\n")
        report = drift.check(path, out, [doc])
        self.assertTrue(report.ok, str(report))
        self.assertEqual(report.checked["docs"], 1)

    def test_a_doc_naming_a_lens_the_config_does_not_define_is_drift(self):
        path, out = self.build()
        doc = self.doc("We also run lens_opposing_counsel and seat_deferential_judge on this.\n")
        report = drift.check(path, out, [doc])
        self.assertFalse(report.ok)
        items = {item for _f, item in self.drifted(report)}
        self.assertEqual(items, {"lens_opposing_counsel", "seat_deferential_judge"})
        for row in report.items():
            self.assertIn("NOTES.md", row["file"])
            self.assertIn("demo_incident", row["why"])

    def test_a_doc_that_is_not_a_file_is_drift(self):
        path, out = self.build()
        report = drift.check(path, out, [self.tmp / "absent.md"])
        self.assertFalse(report.ok)
        self.assertIn("not a file", report.items()[0]["why"])


# ---- the repository's own demo tree -------------------------------------------------------------------

class DemoTreeTests(unittest.TestCase):
    def test_the_committed_demo_skills_are_current(self):
        """The exact check CI and `xscholar test` run."""
        report = drift.check(DEMO, DEMO_SKILLS)
        self.assertTrue(report.ok, str(report))

    def test_the_demo_generates_the_roles_the_config_declares(self):
        inst = config.load(DEMO)
        expected = {role["skill"] for role in inst.roles}
        found = {p.parent.name for p in DEMO_SKILLS.glob("*/SKILL.md")}
        self.assertEqual(expected, found)


if __name__ == "__main__":
    unittest.main()
