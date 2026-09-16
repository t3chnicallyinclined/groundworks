# -*- coding: utf-8 -*-
"""Tests for config.py: the YAML subset, and every rule the instance config must satisfy.

    python -m unittest discover -s engine -p "test_config.py" -v
"""
import copy
import json
import pathlib
import sys
import tempfile
import unittest

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config  # noqa: E402

DEMO = pathlib.Path(__file__).resolve().parents[1] / "instances" / "demo-incident" / "instance.yaml"

MINIMAL = {
    "instance": {"id": "toy", "goal": "establish the cause", "holder": "team", "off_limits": []},
    "units": [{"name": "line", "locator_fields": ["file", "line"], "chunk_size": 50}],
    "fidelity": {"order": ["source", "extract", "agent"], "confirming": ["source", "extract"],
                 "agent_grades": ["agent"]},
    "status": {"values": ["in", "out"], "usable": ["in"]},
    "vocabulary": {"tiers": [0, 1, 2, 3], "directions": ["for_goal", "against_goal"],
                   "issues": ["one", "two"], "roles": ["note"], "targets": ["t1"]},
    "gates": [{"id": "confirmed_needs_source", "table": "fact", "field": "status", "value": "confirmed",
               "requires": [{"edge": "cites", "to": ["unit"], "min": 1}]}],
    "evaluator": {"regression_file": "evals/known_errors.json"},
    "human_owned": ["gates", "fidelity", "status", "evaluator", "privacy"],
    "privacy": {"deny_list": "~/.x/deny.txt", "providers_allowed": ["approved"]},
}


def broken(**changes):
    data = copy.deepcopy(MINIMAL)
    for dotted, value in changes.items():
        target, _, leaf = dotted.rpartition("__")
        node = data
        for part in target.split("__") if target else []:
            node = node[part]
        if value is config.ConfigError:
            del node[leaf]
        else:
            node[leaf] = value
    return data


class YamlSubsetTests(unittest.TestCase):
    def test_parses_maps_lists_flows_comments_and_quotes(self):
        text = (
            "# a comment\n"
            "a: 1\n"
            "b: text with spaces   # trailing comment\n"
            "c: \"quoted: colon\"\n"
            "d: [x, y, 3]\n"
            "e: {k: v, n: [1, 2]}\n"
            "f:\n"
            "  g: true\n"
            "  h: null\n"
            "items:\n"
            "  - name: one\n"
            "    size: 2\n"
            "  - name: two\n"
            "    nested:\n"
            "      deep: 3.5\n"
            "plain:\n"
            "  - a\n"
            "  - b\n")
        got = config.parse_yaml(text)
        self.assertEqual(got["a"], 1)
        self.assertEqual(got["b"], "text with spaces")
        self.assertEqual(got["c"], "quoted: colon")
        self.assertEqual(got["d"], ["x", "y", 3])
        self.assertEqual(got["e"], {"k": "v", "n": [1, 2]})
        self.assertEqual(got["f"], {"g": True, "h": None})
        self.assertEqual(got["items"][0], {"name": "one", "size": 2})
        self.assertEqual(got["items"][1]["nested"], {"deep": 3.5})
        self.assertEqual(got["plain"], ["a", "b"])

    def test_rejects_what_it_cannot_parse(self):
        for text in ("a:\n\tb: 1\n",              # tab indent
                     "a: &anchor 1\n",            # anchor
                     "a: |\n  block\n",           # multi-line scalar
                     "a: 1\na: 2\n",              # duplicate key
                     "a: 1\n   b: 2\n",           # unexpected indentation
                     "no colon here\n"):          # not key: value
            with self.assertRaises(config.ConfigError, msg=text):
                config.parse_yaml(text)


class DemoInstanceTests(unittest.TestCase):
    def test_demo_instance_loads_and_answers_the_eight_questions(self):
        inst = config.load(DEMO)
        self.assertEqual(inst.id, "demo_incident")
        self.assertIn("original_log", inst.confirming_grades())
        self.assertNotIn("agent_summary", inst.confirming_grades())
        self.assertEqual(inst.usable_statuses(), ["in_window"])
        self.assertEqual(sorted(inst.unit_names()), ["change", "log_line", "metric_point"])
        self.assertEqual(inst.unit_type("log_line")["chunk_size"], 200)
        self.assertEqual(len(inst.gate_rules()), 3)
        self.assertEqual(sorted(inst.declared_edges()), ["preserves", "verified_from"])
        self.assertEqual(inst.edge_map()["preserves"], ("unit", "conclusion"))
        vocab = inst.allowed_vocabulary()
        self.assertIn("saturation", vocab["issues"])
        self.assertIn("original_log", vocab["grades"])

    def test_json_config_loads_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "instance.json"
            path.write_text(json.dumps(MINIMAL), encoding="utf-8")
            self.assertEqual(config.load(path).id, "toy")


class ValidationTests(unittest.TestCase):
    def test_minimal_config_is_valid(self):
        self.assertEqual(config.load(MINIMAL).id, "toy")

    def assertRefused(self, data, phrase):
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn(phrase, str(err.exception).lower())

    def test_each_rule_is_enforced(self):
        self.assertRefused(broken(surprise="x"), "unknown top-level")
        self.assertRefused(broken(gates=config.ConfigError), "missing required section: gates")
        self.assertRefused(broken(instance__id="Toy Instance"), "must match")
        self.assertRefused(broken(units=[]), "non-empty list")
        self.assertRefused(broken(units=[{"name": "line", "locator_fields": ["f"], "chunk_size": 0}]),
                           "positive chunk_size")
        self.assertRefused(broken(units=[{"name": "line", "chunk_size": 5}]), "locator_fields")
        self.assertRefused(broken(fidelity__confirming=["source", "extract", "agent"]),
                           "cannot be every grade")
        self.assertRefused(broken(fidelity__confirming=["source", "agent"]), "agent output may never confirm")
        self.assertRefused(broken(fidelity__confirming=["nope"]), "not in order")
        self.assertRefused(broken(status__usable=["in", "out"]), "cannot be every status")
        self.assertRefused(broken(status__usable=["elsewhere"]), "not in values")
        self.assertRefused(broken(vocabulary__tiers=[1, 2, 3]), "must start at 0")
        self.assertRefused(broken(vocabulary__tiers=[0, 3, 2]), "must ascend")
        self.assertRefused(broken(vocabulary__issues=[]), "non-empty list")
        self.assertRefused(broken(gates=[]), "not gated")
        self.assertRefused(broken(gates=[{"id": "g1", "table": "fact", "field": "status", "value": "confirmed"}]),
                           "requires or forbids nothing")
        self.assertRefused(broken(human_owned=["gates"]), "human_owned must include")
        self.assertRefused(broken(evaluator={}), "regression_file")

    def test_an_instance_that_declares_nothing_has_exactly_the_built_in_graph(self):
        """`tables:` and `edges:` are optional. Every config written before them must be untouched."""
        inst = config.load(MINIMAL)
        self.assertEqual(inst.declared_tables(), {})
        self.assertEqual(inst.declared_edges(), {})
        self.assertEqual(tuple(inst.node_tables()), config.BUILT_IN_NODE_TABLES)
        self.assertEqual(inst.edge_map(), dict(config.BUILT_IN_EDGES))

    def test_a_declared_table_or_edge_may_not_take_a_name_the_engine_owns(self):
        """A collision would OVERWRITE the engine's own table, and every gate written against it
        would quietly start meaning something else. The message names the name."""
        for section, name, phrase in (("tables", "unit", "built-in node table"),
                                      ("tables", "cites", "already a built-in table name"),
                                      ("tables", "agent", "reserved by the engine"),
                                      ("edges", "cites", "built-in edge type"),
                                      ("edges", "unit", "already a built-in table name")):
            with self.subTest(section=section, name=name):
                body = ({"fields": {"a": "string"}} if section == "tables"
                        else {"from": "unit", "to": "fact", "fields": {"a": "string"}})
                with self.assertRaises(config.ConfigError) as err:
                    config.load(broken(**{section: {name: body}}))
                self.assertIn(repr(name), str(err.exception))
                self.assertIn(phrase, str(err.exception))

    def test_a_name_may_not_be_a_table_and_an_edge_at_once(self):
        self.assertRefused(broken(tables={"event": {"fields": {"a": "string"}}},
                                  edges={"event": {"from": "unit", "to": "fact"}}),
                           "already declared under tables")

    def test_an_edge_naming_an_unknown_table_is_refused_naming_both(self):
        with self.assertRaises(config.ConfigError) as err:
            config.load(broken(edges={"mentions": {"from": "unit", "to": "evnt"}}))
        message = str(err.exception)
        self.assertIn("edges.mentions", message)
        self.assertIn("'evnt'", message)
        self.assertIn("declare it under tables:", message)
        # an end that names an edge type is not a node table either
        self.assertRefused(broken(edges={"mentions": {"from": "cites", "to": "fact"}}),
                           "which is an edge type, not a node table")
        self.assertRefused(broken(edges={"mentions": {"from": "unit"}}), "to must name the table")
        # ...and one that names a table the instance itself declared is fine
        config.load(broken(tables={"event": {"fields": {"when": "string"}}},
                           edges={"mentions": {"from": "unit", "to": "event"}}))

    def test_a_declared_field_must_say_what_it_holds(self):
        def table(fields):
            return broken(tables={"event": {"fields": fields}})
        self.assertRefused(table({"when": "sring"}), "type must be one of")
        self.assertRefused(table({"when": {"one_of": ["a"]}}), "type must be one of")
        self.assertRefused(table({"when": {"type": "string", "one_of": []}}), "non-empty list")
        self.assertRefused(table({"when": {"type": "string", "one_of": ["a", "a"]}}), "duplicates")
        self.assertRefused(table({"when": {"type": "string", "nullable": True}}), "unknown key(s) nullable")
        self.assertRefused(table({"When": "string"}), "must match [a-z0-9_]+")
        self.assertRefused(broken(tables={"event": {"fields": {}}}), "non-empty mapping")
        self.assertRefused(broken(edges={"mentions": {"from": "unit", "to": "fact",
                                                      "fields": {"in": "string"}}}),
                           "owned by the datastore on a relation table")

    def test_an_access_role_may_name_a_declared_table(self):
        """Before this, `access:` was checked against a fixed list, so a role could not be granted a
        table the same config declared."""
        data = broken(tables={"event": {"fields": {"when": "string"}}},
                      edges={"mentions": {"from": "unit", "to": "event"}},
                      access={"roles": {"reader": {"read": ["unit", "event", "mentions"],
                                                   "write": ["event", "mentions"]}}})
        inst = config.load(data)
        self.assertEqual(inst.access_roles()["reader"]["write"], ["event", "mentions"])
        self.assertEqual(inst.table_fields("event"), {"when": {"type": "string", "one_of": None,
                                                              "optional": False}})
        self.assertEqual(inst.edge_fields("mentions"), {})
        self.assertRefused(broken(access={"roles": {"reader": {"read": ["unit", "event"]}}}),
                           "unknown table(s) event")

    def test_errors_are_collected_not_one_at_a_time(self):
        with self.assertRaises(config.ConfigError) as err:
            config.load(broken(human_owned=["gates"], vocabulary__tiers=[1, 2]))
        message = str(err.exception)
        self.assertIn("human_owned", message)
        self.assertIn("tiers", message)


if __name__ == "__main__":
    unittest.main()
