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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import config  # noqa: E402

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
        self.assertEqual(len(inst.gate_rules()), 2)
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

    def test_errors_are_collected_not_one_at_a_time(self):
        with self.assertRaises(config.ConfigError) as err:
            config.load(broken(human_owned=["gates"], vocabulary__tiers=[1, 2]))
        message = str(err.exception)
        self.assertIn("human_owned", message)
        self.assertIn("tiers", message)


if __name__ == "__main__":
    unittest.main()
