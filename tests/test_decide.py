# -*- coding: utf-8 -*-
"""The question schema is an instrument: refuse what cannot be scored twice the same way,
pin what a score travelled with, and never let a silence read as an absence.

Standard library only -- these run without the `decide` extra, because the rules they check
are the valuable part and must not be gated behind an 18 GB download.
"""
import json
import pathlib
import tempfile
import unittest

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents
                                 if (d / "pyproject.toml").exists())))
from groundworks.decide import Finding, QuestionSchema, SchemaError, check_question  # noqa: E402

GOOD_NOUL = {
    "type": "noul",
    "instructions": "The court refuses to rescind a previously made order.",
    "criteria": {"true": "The court declines or refuses to rescind an existing order.",
                 "false": "No refusal to rescind appears."},
}
GOOD_NOUL_B = {
    "type": "noul",
    "instructions": "An existing order is left standing over an application to remove it.",
    "criteria": {"true": "An application to undo an order is not granted.",
                 "false": "No such application is decided."},
}


class CheckQuestionTests(unittest.TestCase):
    def test_a_well_formed_question_passes(self):
        self.assertIsNone(check_question(GOOD_NOUL))

    def test_a_noul_without_criteria_is_refused(self):
        """Measured: the generic wording scored 0.41 where explicit criteria scored 0.89."""
        q = {"type": "noul", "instructions": "The court refuses to rescind an order."}
        problem = check_question(q)
        self.assertIsNotNone(problem)
        self.assertIn("criteria", problem)

    def test_a_compound_ask_is_refused(self):
        q = dict(GOOD_NOUL,
                 instructions="Material outside the cited lines answers or qualifies the proposition")
        problem = check_question(q)
        self.assertIsNotNone(problem, "a compound ask must be refused")
        self.assertIn("compound", problem)

    def test_a_vague_judgement_without_criteria_is_refused_and_with_them_is_allowed(self):
        bare = {"type": "noul", "instructions": "This passage is relevant to the appeal."}
        self.assertIn("criteria", check_question(bare) or "")
        withcrit = dict(bare, criteria={"true": "The passage names the order under review.",
                                        "false": "It does not."})
        self.assertIsNone(check_question(withcrit))

    def test_one_angle_is_not_a_result(self):
        self.assertIsNotNone(check_question(dict(GOOD_NOUL, angles=["only_one"])))
        self.assertIsNone(check_question(dict(GOOD_NOUL, angles=["a", "b"])))
        self.assertIsNotNone(check_question(dict(GOOD_NOUL, angles=["a", "a"])))

    def test_choice_and_score_shapes(self):
        self.assertIsNone(check_question({"type": "choice", "instructions": "Who speaks these words?",
                                          "criteria": {"court": "the judge", "witness": "the deponent"}}))
        self.assertIsNotNone(check_question({"type": "choice", "instructions": "Who speaks?",
                                             "criteria": {"court": "the judge"}}))
        self.assertIsNone(check_question({"type": "score", "instructions": "How direct is the support?",
                                          "criteria": ["none", "weak", "strong"]}))
        self.assertIsNotNone(check_question({"type": "score", "instructions": "How direct?",
                                             "criteria": "strong"}))

    def test_an_unknown_type_is_refused(self):
        self.assertIsNotNone(check_question(dict(GOOD_NOUL, type="freetext")))


class SchemaTests(unittest.TestCase):
    def test_digest_is_order_independent_and_change_sensitive(self):
        a = QuestionSchema({"x": GOOD_NOUL, "y": GOOD_NOUL_B})
        b = QuestionSchema({"y": GOOD_NOUL_B, "x": GOOD_NOUL})
        self.assertEqual(a.digest, b.digest, "insertion order must not change the digest")
        moved = dict(GOOD_NOUL_B, instructions=GOOD_NOUL_B["instructions"] + ".")
        c = QuestionSchema({"x": GOOD_NOUL, "y": moved})
        self.assertNotEqual(a.digest, c.digest,
                            "any change to any question must change the digest, because it moves every score")

    def test_a_bad_question_refuses_the_whole_schema(self):
        with self.assertRaises(SchemaError):
            QuestionSchema({"ok": GOOD_NOUL,
                            "bad": {"type": "noul", "instructions": "no criteria here"}})

    def test_empty_and_oversized_schemas_are_refused(self):
        with self.assertRaises(SchemaError):
            QuestionSchema({})
        with self.assertRaises(SchemaError):
            QuestionSchema({"q%d" % i: GOOD_NOUL for i in range(65)})

    def test_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "sweep.json"
            p.write_text(json.dumps({"name": "sweep", "questions": {"x": GOOD_NOUL}}),
                         encoding="utf-8")
            s = QuestionSchema.load(p)
            self.assertEqual((s.name, len(s)), ("sweep", 1))


class FindingTests(unittest.TestCase):
    def setUp(self):
        q = dict(GOOD_NOUL, angles=["reader", "opponent"])
        self.schema = QuestionSchema({"reader": GOOD_NOUL, "opponent": GOOD_NOUL_B, "q": q})

    def make(self, scores):
        return Finding(scores, self.schema, "q")

    def test_union_is_recall_and_consensus_is_precision(self):
        f = self.make({"u1": {"reader": 0.9, "opponent": 0.1},    # one angle only
                       "u2": {"reader": 0.8, "opponent": 0.7},    # both
                       "u3": {"reader": 0.1, "opponent": 0.1}})   # neither
        self.assertEqual(f.union(), ["u1", "u2"])
        self.assertEqual(f.consensus(), ["u2"])
        self.assertNotIn("u3", f.union())

    def test_a_single_angle_hit_survives_the_union_and_fails_consensus(self):
        """The measured case: the best single angle found 20 of the 24 the union found,
        and all angles agreed on 1."""
        f = self.make({"only_one": {"reader": 0.95, "opponent": 0.02}})
        self.assertEqual(f.union(), ["only_one"])
        self.assertEqual(f.consensus(), [])

    def test_an_empty_result_still_states_what_was_asked(self):
        """A silence must read as a silence, never as an absence in the corpus."""
        f = self.make({"u1": {"reader": 0.1, "opponent": 0.1}})
        self.assertEqual(f.union(), [])
        text = f.describe()
        self.assertIn("reader", text)
        self.assertIn("opponent", text)
        self.assertIn(self.schema.digest, text)
        self.assertIn("not what the corpus does not contain", text)

    def test_describe_carries_the_schema_digest_so_scores_are_comparable(self):
        f = self.make({"u1": {"reader": 0.9, "opponent": 0.9}})
        self.assertIn(self.schema.digest, f.describe())


class ExtraIsOptionalTests(unittest.TestCase):
    def test_schema_imports_without_torch(self):
        """The rules must not be gated behind an 18 GB download."""
        import groundworks.decide.schema as s
        self.assertNotIn("torch", getattr(s, "__dict__", {}))

    def test_no_core_module_imports_the_runner(self):
        """decide.clef must stay out of the core, exactly as embed/ does, so the engine
        keeps dependencies = []."""
        root = pathlib.Path(__file__).resolve().parents[1] / "groundworks"
        offenders = []
        for py in root.rglob("*.py"):
            if "decide" in py.parts or "embed" in py.parts:
                continue
            if "decide.clef" in py.read_text(encoding="utf-8", errors="replace"):
                offenders.append(str(py.relative_to(root)))
        self.assertEqual(offenders, [], "core modules must not import the model runner")


if __name__ == "__main__":
    unittest.main()
