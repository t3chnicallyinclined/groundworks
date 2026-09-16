# -*- coding: utf-8 -*-
"""Tests for panel.py. Each check is tested both ways: it rejects what it should, then accepts once fixed.

    python -m unittest discover -s engine/panel -v
"""
import contextlib
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.panel import panel  # noqa: E402

SEATS = [{"id": "seat_alpha", "lens": "lens_deference"},
         {"id": "seat_beta", "lens": "lens_test"},
         {"id": "seat_gamma", "lens": "lens_remedy"}]
CATS = {"seat_alpha": "record", "seat_beta": "preservation", "seat_gamma": "remedy"}
R1_BODY = dict((sid, "Round one reasoning for %s: the finding rests on testimony about the commute and nothing else."
                % sid) for sid in CATS)


def answer(option, conf="high", deal="no", objections=(), new=(), flaw=None, body="Analysis of the target."):
    lines = [body, ""]
    lines += ["OBJECTION: %s | %s" % o for o in objections]
    lines += ["NEW: %s" % n for n in new]
    if flaw:
        lines.append('FLAW: "%s" | %s' % flaw)
    lines.append("STANCE: %s | CONFIDENCE: %s | DEALBREAKER: %s" % (option, conf, deal))
    return "\n".join(lines) + "\n"


def cli(*args):
    with contextlib.redirect_stdout(io.StringIO()):
        return panel.main(list(args))


class PanelCase(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.write_panel()

    def write_panel(self, **overrides):
        cfg = {"panel_id": "synthetic_panel", "target": "conclusion:point_x", "question": "Does point X survive?",
               "options": ["affirm", "reverse", "remit"], "objection_categories": ["record", "preservation", "remedy"],
               "weighted_seat": "seat_alpha", "seats": SEATS}
        cfg.update(overrides)
        (self.dir / "panel.json").write_text(json.dumps(cfg), encoding="utf-8")

    def put(self, rnd, seat, text, suffix=".md"):
        folder = self.dir / rnd
        folder.mkdir(exist_ok=True)
        (folder / (seat + suffix)).write_text(text, encoding="utf-8")

    def round1(self, stances=None):
        self.r1 = stances or {"seat_alpha": "affirm", "seat_beta": "affirm", "seat_gamma": "reverse"}
        for sid, option in self.r1.items():
            self.put("round1", sid, answer(option, body=R1_BODY[sid]))

    def ready_for_round2(self, stances=None):
        panel.lock(self.dir)
        self.round1(stances)
        panel.anonymize(self.dir)

    def good_r2(self, sid, option=None, conf="high", deal="no", **kw):
        kw.setdefault("objections", [(CATS[sid], "A distinct objection raised by this seat only.")])
        kw.setdefault("new", ["A new risk about the remedy that round one never raised for %s" % sid])
        self.put("round2", sid, answer(option or self.r1[sid], conf, deal, **kw))

    def all_good(self):
        for sid in CATS:
            self.good_r2(sid)

    def conditions(self):
        return sorted((c, s) for c, s, _ in panel.evaluate(self.dir))


class LockTests(PanelCase):
    def test_lock_accepts_clean_run_then_refuses_relock(self):
        self.assertEqual(len(panel.lock(self.dir)), 64)
        with self.assertRaises(panel.PanelError) as err:
            panel.lock(self.dir)
        self.assertEqual(err.exception.code, 1)

    def test_lock_refuses_answers_that_exist_before_lock(self):
        self.round1()
        with self.assertRaises(panel.PanelError) as err:
            panel.lock(self.dir)
        self.assertEqual(err.exception.code, 1)

    def test_edit_after_lock_is_tamper(self):
        panel.lock(self.dir)
        panel.verify(self.dir)
        self.write_panel(weighted_seat="seat_gamma")
        with self.assertRaises(panel.PanelError) as err:
            panel.verify(self.dir)
        self.assertIn("TAMPER", str(err.exception))

    def test_invalid_panels_are_not_executed(self):
        for bad in ({"seats": SEATS[:2]},
                    {"seats": SEATS[:2] + [{"id": "seat_gamma", "lens": "lens_test"}]},
                    {"thresholds": {"agreement": 1.0}},
                    {"weighted_seat": "seat_nobody"},
                    {"options": ["affirm", "abstain"]},
                    {"confidence_weights": {"med": 1.2}},
                    {"confidence_weights": {"high": 0.5, "med": 0.9}},
                    {"confidence_weights": {"unsure": 0.3}}):
            self.write_panel(**bad)
            with self.assertRaises(panel.PanelError) as err:
                panel.lock(self.dir)
            self.assertEqual(err.exception.code, 2, bad)
        self.write_panel()
        panel.lock(self.dir)


class SettingsTests(PanelCase):
    def settings(self, data):
        path = self.dir / "owner_settings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_owner_settings_are_copied_in_at_lock(self):
        path = self.settings({"thresholds": {"consensus": 0.6}, "confidence_weights": {"med": 0.8}, "notes": "x"})
        panel.lock(self.dir, path)
        cfg, _ = panel.verify(self.dir)
        self.assertEqual(cfg["thresholds"]["consensus"], 0.6)
        self.assertEqual(cfg["confidence"]["med"], 0.8)

    def test_panel_that_disagrees_with_owner_settings_is_refused_and_not_locked(self):
        self.write_panel(thresholds={"consensus": 0.6667})
        path = self.settings({"thresholds": {"consensus": 0.6666666666666666}})
        with self.assertRaises(panel.PanelError) as err:
            panel.lock(self.dir, path)
        self.assertEqual(err.exception.code, 1)
        self.assertFalse((self.dir / "panel.lock.json").exists())
        self.write_panel()
        panel.lock(self.dir, path)

    def test_missing_settings_file_is_not_executed(self):
        with self.assertRaises(panel.PanelError) as err:
            panel.lock(self.dir, self.dir / "nope.json")
        self.assertEqual(err.exception.code, 2)


class AnonymizeTests(PanelCase):
    def test_requires_a_valid_round1_stance_then_accepts(self):
        panel.lock(self.dir)
        self.round1()
        self.put("round1", "seat_beta", "Prose only, no stance line.\n")
        with self.assertRaises(panel.PanelError) as err:
            panel.anonymize(self.dir)
        self.assertEqual(err.exception.code, 1)
        self.put("round1", "seat_beta", answer("affirm", body=R1_BODY["seat_beta"]))
        self.assertEqual(panel.anonymize(self.dir), 3)

    def test_strips_identities_with_order_fixed_by_the_lock(self):
        self.ready_for_round2()
        bundle = (self.dir / "round1_anon.md").read_text(encoding="utf-8").lower()
        for seat in SEATS:
            for term in panel.identity_terms(seat):
                self.assertNotIn(term.lower(), bundle)
        for label in ("member a", "member b", "member c"):
            self.assertIn(label, bundle)
        first = json.loads((self.dir / "anon_map.json").read_text(encoding="utf-8"))["order"]
        panel.anonymize(self.dir)
        self.assertEqual(json.loads((self.dir / "anon_map.json").read_text(encoding="utf-8"))["order"], first)

    def test_round1_edit_after_anonymization_is_tamper(self):
        self.ready_for_round2()
        self.all_good()
        self.assertEqual(self.conditions(), [])
        self.put("round1", "seat_alpha", answer("reverse", body="Rewritten after seeing the others."))
        with self.assertRaises(panel.PanelError) as err:
            panel.evaluate(self.dir)
        self.assertIn("TAMPER", str(err.exception))


class CheckTests(PanelCase):
    def setUp(self):
        super().setUp()
        self.ready_for_round2()

    def test_round2_requires_anonymization(self):
        (self.dir / "anon_map.json").unlink()
        with self.assertRaises(panel.PanelError) as err:
            panel.evaluate(self.dir)
        self.assertEqual(err.exception.code, 1)

    def test_stance_line_is_required_never_inferred(self):
        self.good_r2("seat_alpha")
        self.good_r2("seat_beta")
        self.assertIn(("missing_answer", "seat_gamma"), self.conditions())
        self.put("round2", "seat_gamma", "I lean clearly toward reversal.\n")
        self.assertIn(("missing_stance", "seat_gamma"), self.conditions())
        self.good_r2("seat_gamma", option="maybe")
        self.assertIn(("missing_stance", "seat_gamma"), self.conditions())
        self.put("round2", "seat_gamma", answer("reverse", objections=[("remedy", "x")], new=["one two three four five six"])
                 + "STANCE: affirm | CONFIDENCE: high | DEALBREAKER: no\n")
        self.assertIn(("missing_stance", "seat_gamma"), self.conditions())
        self.good_r2("seat_gamma")
        self.assertEqual(self.conditions(), [])

    def test_novelty_gate_both_ways(self):
        self.all_good()
        self.good_r2("seat_beta", new=["the finding rests on testimony about the commute and nothing else"])
        self.assertIn(("novelty_gate", "seat_beta"), self.conditions())
        self.good_r2("seat_beta", new=["too short"])
        self.assertIn(("novelty_gate", "seat_beta"), self.conditions())
        self.good_r2("seat_beta")
        self.assertEqual(self.conditions(), [])

    def test_changed_stance_needs_a_verbatim_flaw(self):
        self.all_good()
        self.good_r2("seat_gamma", option="affirm")
        self.assertIn(("flaw_required", "seat_gamma"), self.conditions())
        self.good_r2("seat_gamma", option="affirm", flaw=("the finding rests on invented words", "misread"))
        self.assertIn(("flaw_required", "seat_gamma"), self.conditions())
        self.good_r2("seat_gamma", option="affirm",
                     flaw=("the finding rests on testimony about the commute", "one witness is not the whole record"))
        self.assertNotIn(("flaw_required", "seat_gamma"), self.conditions())

    def test_objection_categories_come_from_the_panel(self):
        self.all_good()
        self.good_r2("seat_alpha", objections=[("vibes", "not a category")])
        self.assertIn(("bad_objection", "seat_alpha"), self.conditions())
        self.good_r2("seat_alpha")
        self.assertEqual(self.conditions(), [])

    def test_dissent_quota_both_ways(self):
        for sid in CATS:
            self.good_r2(sid, objections=[("record", "the same point")])
        owed = [c for c, _ in self.conditions() if c == "dissent_quota"]
        self.assertEqual(len(owed), 1)
        self.good_r2("seat_gamma", objections=[("remedy", "a different point")])
        self.assertEqual(self.conditions(), [])


class AgreementTests(PanelCase):
    def setUp(self):
        super().setUp()
        self.ready_for_round2({"seat_alpha": "affirm", "seat_beta": "affirm", "seat_gamma": "affirm"})

    def test_unanimity_owes_counterfactuals_from_the_least_confident_and_blocks_tally(self):
        self.good_r2("seat_alpha", conf="high")
        self.good_r2("seat_beta", conf="low")
        self.good_r2("seat_gamma", conf="med")
        owed = sorted(s for c, s in self.conditions() if c == "agreement_check")
        self.assertEqual(owed, ["seat_beta", "seat_gamma"])
        with self.assertRaises(panel.PanelError):
            panel.tally(self.dir)
        self.put("round2", "seat_beta", "An alternative, with no objection line.\n", ".counterfactual.md")
        self.assertIn(("agreement_check", "seat_beta"), self.conditions())
        self.put("round2", "seat_beta", "Alternative.\nOBJECTION: record | the record may not carry it\n",
                 ".counterfactual.md")
        self.put("round2", "seat_gamma", "Alternative.\nOBJECTION: remedy | remittal may fit better\n",
                 ".counterfactual.md")
        self.assertEqual(self.conditions(), [])
        self.assertEqual(panel.tally(self.dir)["verdict"], "affirm")


class TallyTests(PanelCase):
    def setUp(self):
        super().setUp()
        self.ready_for_round2()

    def test_weighted_consensus_writes_lens_keyed_verdicts(self):
        self.all_good()
        result = panel.tally(self.dir)
        self.assertEqual(result["w_total"], 3.5)
        self.assertAlmostEqual(result["weights"]["affirm"], 2.5)
        self.assertEqual(result["verdict"], "affirm")
        rows = [json.loads(line) for line in (self.dir / "verdicts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["lens"] and r["grade"] == "agent" and r["panel_sha256"] for r in rows))

    def test_hesitant_majority_is_a_split_returned_to_the_human(self):
        self.good_r2("seat_alpha", conf="low")
        self.good_r2("seat_beta")
        self.good_r2("seat_gamma", deal="yes")
        result = panel.tally(self.dir)
        self.assertIsNone(result["verdict"])
        self.assertEqual([m["seat"] for m in result["minority_report"]], ["seat_gamma"])
        self.assertEqual(cli("tally", str(self.dir)), 3)

    def relock(self, **overrides):
        """Rewrite the panel, then redo lock, round 1 and anonymization, with good round-2 answers."""
        self.write_panel(**overrides)
        shutil.rmtree(self.dir / "round1")
        for name in ("panel.lock.json", "anon_map.json", "round1_anon.md"):
            (self.dir / name).unlink()
        self.ready_for_round2()
        self.all_good()

    def test_two_high_seats_of_three_equal_seats_reach_the_exact_two_thirds_bar(self):
        self.relock(weighted_seat=None)
        result = panel.tally(self.dir)
        self.assertEqual(result["w_total"], 3.0)
        self.assertEqual(result["verdict"], "affirm")

    def test_rounded_consensus_threshold_turns_the_same_votes_into_a_split(self):
        self.relock(weighted_seat=None, thresholds={"consensus": 0.6667})
        self.assertIsNone(panel.tally(self.dir)["verdict"])

    def test_two_medium_seats_carry_only_when_the_owner_raises_the_medium_weight(self):
        self.relock(weighted_seat=None)
        self.good_r2("seat_alpha", conf="med")
        self.good_r2("seat_beta", conf="med")
        self.assertIsNone(panel.tally(self.dir)["verdict"])  # 1.5 of 3.0 is exactly half: no bar above 0.5 admits it
        self.relock(weighted_seat=None, thresholds={"consensus": 0.55}, confidence_weights={"med": 0.85})
        self.good_r2("seat_alpha", conf="med")
        self.good_r2("seat_beta", conf="med")
        result = panel.tally(self.dir)
        self.assertAlmostEqual(result["weights"]["affirm"], 1.7)
        self.assertEqual(result["verdict"], "affirm")
        self.assertEqual(result["confidence_weights"]["med"], 0.85)

    def test_tally_refuses_while_reprompts_are_owed(self):
        self.good_r2("seat_alpha")
        self.good_r2("seat_beta")
        with self.assertRaises(panel.PanelError) as err:
            panel.tally(self.dir)
        self.assertEqual(err.exception.code, 1)
        self.assertEqual(cli("check", str(self.dir)), 1)
        self.good_r2("seat_gamma")
        self.assertEqual(cli("check", str(self.dir)), 0)


class EnforcementLogTests(PanelCase):
    def test_counts_each_owed_prompt_once_per_answer_version(self):
        self.ready_for_round2()
        self.good_r2("seat_alpha")
        self.good_r2("seat_beta")
        self.put("round2", "seat_gamma", "prose\n")
        cli("check", str(self.dir))
        cli("check", str(self.dir))
        self.assertEqual(panel.enforcement_counts(self.dir)["missing_stance"], 1)
        self.put("round2", "seat_gamma", "revised prose, still no stance\n")
        cli("check", str(self.dir))
        self.assertEqual(panel.enforcement_counts(self.dir)["missing_stance"], 2)


if __name__ == "__main__":
    unittest.main()
