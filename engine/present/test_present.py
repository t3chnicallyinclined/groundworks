# -*- coding: utf-8 -*-
"""Tests for the reading layer (engine/present), on a non-legal and a legal-shaped synthetic instance.

Every gate is tested both ways: preservation passes on every mode and fails when a renderer is broken;
policy and row validation accept good input and reject bad input; the lookup budget serves and refuses.

    python -m unittest discover -s engine/present -v
"""
import contextlib
import copy
import io
import json
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import features  # noqa: E402
import lookup  # noqa: E402
import metrics  # noqa: E402
import plant  # noqa: E402
import policy  # noqa: E402
import render  # noqa: E402
import score  # noqa: E402
import synthetic  # noqa: E402

DATASETS = {"incident": synthetic.incident, "legal": synthetic.legal}
# Every dataset, including the big one that exercises collapse, overflow and the spot sample.
ALL = dict(DATASETS, review=synthetic.review)
# The one-layer header the claims view keeps (v1.0).
HEADER = re.compile(r"^Check first (\d+) \u00b7 Eliminated (\d+) \u00b7 Superseded (\d+) \u00b7 Established (\d+) "
                    r"\u00b7 Total (\d+)$", re.M)
# The two-layer header (v1.1): the five buckets still add up to the total; spot check is a sample.
SCAN_HEADER = re.compile(r"^Check first (\d+) \u00b7 More to check (\d+) \u00b7 Eliminated (\d+) \u00b7 "
                         r"Superseded (\d+) \u00b7 Established (\d+) \u00b7 Total (\d+) \u00b7 "
                         r"Spot check (\d+) of (\d+)$", re.M)


def scope_rows(doc, mode):
    return [r for r in features.validate_rows(doc)["rows"] if mode != "claims" or r["kind"] in render.CLAIM_KINDS]


def plan_for(doc, pol):
    return features.classify(features.validate_rows(doc), policy.load(pol))


def section(text, heading):
    lines = text.split("\n")
    start = lines.index(heading) + 1
    out = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        out.append(line)
    return out


def entry_ids(lines):
    ids = []
    for line in lines:
        if line.startswith("- ") or re.match(r"^\| \d+ \|", line):
            m = re.search(r"`([^`]+)`", line)
            ids.append(m.group(1) if m else None)
    return ids


def row_ids(lines):
    """Every row id on entry lines, expanding a collapsed group line ("... ids `a`, `b`, `c`")."""
    ids = []
    for line in lines:
        if not (line.startswith("- ") or re.match(r"^\| \d+ \|", line)):
            continue
        head, sep, tail = line.partition(" ids `")
        ids += re.findall(r"`([^`]+)`", sep + tail) if sep else re.findall(r"`([^`]+)`", head)[:1]
    return ids


def layers(doc, pol, mode):
    """(rendered, scan layer, details layer) for one mode."""
    text = render.render(doc, mode, pol)
    scan, details = metrics.split_layers(text, mode)
    return text, scan, details


class Preservation(unittest.TestCase):
    def assertPreserved(self, rows, text, pol, msg=None):
        res = metrics.preservation(rows, text, pol)
        self.assertTrue(res["ok"], (msg, {c: res[c]["missing"] for c in metrics.CATEGORIES}))
        self.assertEqual(res["pct"], 100.0)

    def test_every_mode_preserves_ids_numbers_negations_locators_and_text(self):
        for name, make in ALL.items():
            doc, pol = make()
            for mode in render.MODES:
                with self.subTest(dataset=name, mode=mode):
                    self.assertPreserved(scope_rows(doc, mode), render.render(doc, mode, pol), pol)

    def test_generic_default_policy_preserves_too(self):
        for name, make in ALL.items():
            doc, _ = make()
            for mode in render.MODES:
                with self.subTest(dataset=name, mode=mode):
                    self.assertPreserved(scope_rows(doc, mode), render.render(doc, mode, None), None)

    def test_planted_copies_preserve(self):
        for name, make in ALL.items():
            doc, pol = make()
            planted, _ = plant.plant(doc, pol, seed=3, per_type=1)
            for mode in render.MODES:
                with self.subTest(dataset=name, mode=mode):
                    self.assertPreserved(scope_rows(planted, mode), render.render(planted, mode, pol), pol)

    def test_preservation_fails_when_a_renderer_drops_a_row(self):
        doc, pol = synthetic.incident()
        kinds = {r["id"]: r["kind"] for r in doc["rows"]}
        real_entry = render.entry
        # one victim per bucket: superseded, established, eliminated, check first
        for victim in ("c6", "u1", "c3", "k1"):
            def broken(plan, pol_, rid, *args, _victim=victim, **kwargs):
                return "" if rid == _victim else real_entry(plan, pol_, rid, *args, **kwargs)
            with mock.patch.object(render, "entry", broken):
                for mode in render.MODES:
                    if mode == "claims" and kinds[victim] not in render.CLAIM_KINDS:
                        continue
                    with self.subTest(victim=victim, mode=mode):
                        res = metrics.preservation(scope_rows(doc, mode), render.render(doc, mode, pol), pol)
                        self.assertFalse(res["ok"])
                        victim_text = next(r["text"] for r in doc["rows"] if r["id"] == victim)
                        self.assertTrue(victim in res["ids"]["missing"]
                                        or metrics.normalize_input(victim_text) in res["texts"]["missing"])
        self.assertPreserved(scope_rows(doc, "full"), render.render(doc, "full", pol), pol, "restored")

    def test_preservation_fails_when_intake_drops_a_row(self):
        doc, pol = synthetic.legal()
        real = features.validate_rows

        def dropping(d):
            out = real(d)
            out["rows"] = out["rows"][:-1]
            return out
        with mock.patch.object(features, "validate_rows", dropping):
            texts = {mode: render.render(doc, mode, pol) for mode in render.MODES}
        for mode, text in texts.items():
            res = metrics.preservation(scope_rows(doc, mode), text, pol)
            if mode == "claims":  # the dropped row is a unit, outside the claims view
                continue
            self.assertFalse(res["ok"], mode)
            self.assertIn(doc["rows"][-1]["id"], res["ids"]["missing"])

    def test_preservation_fails_when_emphasis_eats_a_negation(self):
        doc, pol = synthetic.incident()
        real = render.emphasize
        with mock.patch.object(render, "emphasize", lambda text, p: real(text.replace(" not ", " "), p)):
            full, speed = render.render(doc, "full", pol), render.render(doc, "speedread", pol)
        self.assertPreserved(scope_rows(doc, "full"), full, pol, "full does not use emphasis")
        res = metrics.preservation(scope_rows(doc, "speedread"), speed, pol)
        self.assertFalse(res["ok"])
        self.assertEqual(res["negations"]["missing"], ["not"])

    def test_claims_header_counts_add_up_and_match_sections(self):
        for name, make in ALL.items():
            doc, pol = make()
            text = render.render(doc, "claims", pol)
            n, k, s, m, t = map(int, HEADER.search(text).groups())
            self.assertEqual(n + k + s + m, t)
            self.assertEqual(t, len(scope_rows(doc, "claims")))
            self.assertEqual(len(entry_ids(section(text, "## Check first (%d)" % n))), n)
            self.assertEqual(len(entry_ids(section(text, "## Eliminated (%d)" % k))), k)
            self.assertEqual(len(entry_ids(section(text, "## Superseded (%d)" % s))), s)
            self.assertEqual(len(entry_ids(section(text, "## Established (%d)" % m))), m)

    def test_two_layer_header_counts_every_row_once_and_spot_check_separately(self):
        for name, make in ALL.items():
            doc, pol = make()
            text = render.render(doc, "speedread", pol)
            n, x, k, s, m, t, a, b = map(int, SCAN_HEADER.search(text).groups())
            with self.subTest(dataset=name):
                self.assertEqual(n + x + k + s + m, t)  # every row in exactly one bucket
                self.assertEqual(t, len(doc["rows"]))
                self.assertEqual(b, m)  # the sample is drawn from Established and counted apart
                self.assertLessEqual(a, b)
                if a:
                    shown = entry_ids(section(text, "## Spot check (random, %d of %d established)" % (a, b)))
                    self.assertEqual(len(shown), a)
                details = entry_ids(metrics.split_layers(text, "speedread")[1].split("\n"))
                self.assertEqual(sorted(details), sorted(r["id"] for r in doc["rows"]))
                self.assertEqual(len(row_ids(section(text, "## Check first (%d)" % n))), n)
                self.assertEqual(len(row_ids(section(text, "## More to check (%d)" % x))), x)
                self.assertEqual(len(entry_ids(section(text, "## Eliminated (%d)" % k))), k)
                self.assertEqual(len(entry_ids(section(text, "## Superseded (%d)" % s))), s)
                self.assertEqual(len(entry_ids(section(text, "## Established (%d)" % m))), m)

    def test_render_is_deterministic_and_stores_nothing_on_rows(self):
        doc, pol = synthetic.incident()
        before = copy.deepcopy(doc)
        for mode in render.MODES:
            self.assertEqual(render.render(doc, mode, pol), render.render(doc, mode, pol))
        self.assertEqual(doc, before)
        policy.hook_budget(doc, pol, 5, 500)
        policy.synthesis_context(doc, pol, 3)
        self.assertEqual(doc, before)


class Emphasis(unittest.TestCase):
    def test_negation_words_render_bold_whole_word_any_case(self):
        doc = {"title": "t", "question": None, "groups": ["g"], "rows": [
            {"id": "a", "group": "g", "kind": "claim", "text": "The valve was NOT closed; a knot formed", "status": "confirmed"},
            {"id": "b", "group": "g", "kind": "claim", "text": "It was not seen", "status": "inferred"}]}
        text = render.render(doc, "speedread")
        self.assertIn("**NOT**", text)
        self.assertIn("**not**", text)
        self.assertNotIn("k**not**", text)
        self.assertNotIn("**knot**", text)
        planted = render.render(synthetic.incident()[0], "speedread", synthetic.INCIDENT_POLICY)
        c2 = next(line for line in planted.split("\n") if "`c2`" in line)
        self.assertIn("**not**", c2)
        self.assertIn("**didn\u2019t**", planted)
        self.assertIn("**missing**", planted)  # instance-supplied negation word

    def test_numbers_locators_and_status_tokens_bold_in_speedread_only(self):
        doc, pol = synthetic.incident()
        speed = render.render(doc, "speedread", pol)
        for token in ("**312**", "**50/50**", "**api-3:app.log:1207**", "[**inferred**/**agent**]", "**INC-4411**"):
            self.assertIn(token, speed)
        for mode in ("full", "uncertain_first"):
            self.assertNotIn("**", render.render(doc, mode, pol), mode)

    def test_table_cells_escape_pipes_and_backslashes(self):
        doc, pol = synthetic.incident()
        a1 = next(r for r in doc["rows"] if r["id"] == "a1")
        a1["status"], a1["grade"] = "unverified", None  # move the row with | and \ into the table
        text = render.render(doc, "speedread", pol)
        line = next(x for x in text.split("\n") if "`a1`" in x)
        self.assertTrue(line.startswith("| "))
        self.assertIn(r"\|", line)
        self.assertIn(r"C:\\runbooks\\db", line)
        cells = re.split(r"(?<!\\)\|", re.sub(r"\\\\", "", line))
        self.assertEqual(len(cells), 6)  # empty, #, What, Why check, Where, empty
        self.assertTrue(metrics.preservation(doc["rows"], text, pol)["ok"])

    def test_newlines_stay_on_one_line(self):
        doc, pol = synthetic.incident()
        for mode in render.MODES:
            text, scan, details = layers(doc, pol, mode)
            body = details if details is not None else text
            line = next(x for x in body.split("\n") if "`c4`" in x)
            self.assertIn("<br>", line)
            self.assertIn("INC-4411", line)
            self.assertNotIn("\n", render.oneline(line))  # no raw newline inside a value
        # the scan layer cuts c4 at its first line and marks the cut; the full text is in Details
        _text, scan, _details = layers(doc, pol, "speedread")
        scan_line = next(x for x in scan.split("\n") if "`c4`" in x)
        self.assertNotIn("<br>", scan_line)
        self.assertTrue(scan_line.rstrip().endswith("(**pagerduty:INC-4411**)"))
        self.assertIn(render.ELLIPSIS.strip(), scan_line)


class Sections(unittest.TestCase):
    def test_eliminated_always_shown_and_labelled_in_every_mode(self):
        for name, make in DATASETS.items():
            doc, pol = make()
            elim = plan_for(doc, pol)["eliminated"]
            self.assertTrue(elim)
            for mode in render.MODES:
                text = render.render(doc, mode, pol)
                for rid in elim:
                    line = next(x for x in text.split("\n") if "`%s`" % rid in x and "- " in x[:2])
                    self.assertIn("ELIMINATED CLAIM (false)", line, (name, mode))

    def test_superseded_is_its_own_class_with_replacement_named(self):
        cases = {"incident": ("c6", "c4"), "legal": ("f4", "f1")}  # meta.replaced_by, then first link
        for name, make in DATASETS.items():
            doc, pol = make()
            rid, replacement = cases[name]
            plan = plan_for(doc, pol)
            f = plan["features"][rid]
            self.assertEqual((f["bucket"], f["superseded"], f["eliminated"], f["established"]),
                             ("superseded", True, False, False))
            self.assertNotIn(rid, plan["eliminated"] + plan["established"] + plan["check_first"])
            label = "SUPERSEDED CLAIM (replaced by %s)" % replacement
            for mode in render.MODES:
                text = render.render(doc, mode, pol)
                line = next(x for x in text.split("\n") if "`%s`" % rid in x)
                self.assertIn(label, line, mode)
            speed = render.render(doc, "speedread", pol)
            self.assertIn("\u00b7 Superseded 1 \u00b7", speed)
            self.assertEqual(entry_ids(section(speed, "## Superseded (1)")), [rid])
            self.assertLess(speed.index("## Eliminated"), speed.index("## Superseded"))
            self.assertLess(speed.index("## Superseded"), speed.index("## Established"))
        bare = {"title": "t", "question": None, "groups": ["g"],
                "rows": [{"id": "s", "group": "g", "kind": "claim", "text": "old", "status": "superseded"}]}
        self.assertIn("SUPERSEDED CLAIM (replaced by unrecorded)", render.render(bare, "full"))

    def test_check_first_order_uses_named_features(self):
        doc, pol = synthetic.incident()
        text = render.render(doc, "speedread", pol)
        order = entry_ids(section(text, "## Check first (6)"))
        self.assertEqual(order[:2], ["k1", "r1"])  # unanswered high impact, then linked to eliminated
        self.assertEqual(order, plan_for(doc, pol)["check_first"])
        legal_doc, legal_pol = synthetic.legal()
        self.assertEqual(plan_for(legal_doc, legal_pol)["check_first"][:2], ["x1", "g1"])
        self.assertIn("ci1", plan_for(legal_doc, legal_pol)["check_first"])  # verdict MISMATCH, case-insensitive

    def test_full_keeps_group_and_input_order(self):
        doc, pol = synthetic.incident()
        expected = [r["id"] for g in doc["groups"] for r in doc["rows"] if r["group"] == g]
        self.assertEqual(entry_ids(render.render(doc, "full", pol).split("\n")), expected)

    def test_uncertain_first_puts_check_rows_first_then_input_order(self):
        for name, make in DATASETS.items():
            doc, pol = make()
            plan = plan_for(doc, pol)
            text = render.render(doc, "uncertain_first", pol)
            lines = text.split("\n")
            head = next(x for x in lines if x.startswith("## Needs check"))
            rest = next(x for x in lines if x.startswith("## Other rows"))
            self.assertEqual(entry_ids(section(text, head)), plan["check_first"])
            self.assertEqual(entry_ids(section(text, rest)),
                             [r["id"] for r in doc["rows"] if r["id"] not in plan["check_first"]])

    def test_claims_view_names_what_it_leaves_out(self):
        doc, pol = synthetic.incident()
        text = render.render(doc, "claims", pol)
        note = next(x for x in text.split("\n") if x.startswith("Claims view:"))
        others = [r["id"] for r in doc["rows"] if r["kind"] not in render.CLAIM_KINDS]
        self.assertIn("%d of %d rows" % (len(doc["rows"]) - len(others), len(doc["rows"])), note)
        for rid in others:
            self.assertRegex(note, r"(?<!\w)%s(?!\w)" % re.escape(rid))
            self.assertNotIn("`%s`" % rid, text)


class Banner(unittest.TestCase):
    def test_seeded_copies_carry_the_banner_in_every_mode_and_clean_rows_never(self):
        for name, make in DATASETS.items():
            doc, pol = make()
            planted, manifest = plant.plant(doc, pol, seed=5, per_type=1)
            self.assertTrue(planted["title"].startswith(plant.TITLE_PREFIX))
            self.assertIs(planted["seeded_test_copy"], True)
            self.assertEqual((manifest["banner"], manifest["seeded_test_copy"]), (render.BANNER, True))
            for mode in render.MODES:
                self.assertEqual(render.render(planted, mode, pol).split("\n")[0], render.BANNER, mode)
                self.assertNotIn(render.BANNER, render.render(doc, mode, pol), mode)
            self.assertTrue(policy.hook_budget(planted, pol, 50, 10000)["text"].startswith(render.BANNER + "\n"))
            self.assertNotIn(render.BANNER, policy.hook_budget(doc, pol, 50, 10000)["text"])
            self.assertIs(policy.synthesis_context(planted, pol, 5)["seeded_test_copy"], True)
            with self.assertRaises(ValueError):
                plant.plant(planted, pol)

    def test_planted_rows_file_renders_with_banner(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        doc, pol = synthetic.incident()
        (tmp / "rows.json").write_text(json.dumps(doc), encoding="utf-8")
        (tmp / "policy.json").write_text(json.dumps(pol), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(plant.main([str(tmp / "rows.json"), str(tmp / "out"), "--seed", "2", "--per-type", "1",
                                         "--policy", str(tmp / "policy.json")]), 0)
        manifest = json.loads((tmp / "out" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["banner"], render.BANNER)
        for rows_file, banner in ((tmp / "out" / "planted_rows.json", True), (tmp / "rows.json", False)):
            for mode in render.MODES:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(render.main([str(rows_file), "--mode", mode, "--policy", str(tmp / "policy.json")]), 0)
                self.assertEqual(buf.getvalue().startswith(render.BANNER + "\n"), banner, (rows_file.name, mode))


class ConsumerPolicies(unittest.TestCase):
    def test_human_speedread_leaves_nothing_out(self):
        for name, make in DATASETS.items():
            doc, pol = make()
            view = policy.human_speedread(doc, pol)
            ids = view["check_first"] + view["eliminated"] + view["superseded"] + sum(view["established"].values(), [])
            self.assertEqual(sorted(ids), sorted(r["id"] for r in doc["rows"]))
            self.assertEqual(view["excluded"], [])

    def test_hook_budget_cuts_established_first_and_reports_exclusions(self):
        for name, make in DATASETS.items():
            doc, pol = make()
            pol_obj = policy.load(pol)
            plan = plan_for(doc, pol)
            order = plan["eliminated"] + plan["check_first"] + plan["superseded"] + plan["established"]
            priority, established = set(plan["eliminated"] + plan["check_first"]), set(plan["established"])
            raised = hard_case = 0
            for max_lines in range(1, len(order) + 3):
                for max_chars in (20, 60, 150, 300, 700, 1500, 10000):
                    try:
                        res = policy.hook_budget(doc, pol, max_lines, max_chars)
                    except ValueError:
                        raised += 1
                        continue
                    inc, exc, text = res["included"], res["excluded"], res["text"]
                    self.assertEqual(inc + exc, order)
                    if set(inc) & established:
                        self.assertTrue(priority <= set(inc), (max_lines, max_chars))
                    if priority <= set(inc) and set(exc) & established:
                        hard_case += 1
                    self.assertLessEqual(len(text.split("\n")), max_lines)
                    self.assertLessEqual(len(text), max_chars)
                    if exc:
                        self.assertTrue(text.split("\n")[-1].startswith("[EXCLUDED %d of %d rows" % (len(exc), len(order))))
                    else:
                        self.assertNotIn("[EXCLUDED", text)
                    for rid in inc:  # whole lines, never truncated
                        self.assertIn(render.hook_line(plan, pol_obj, rid), text.split("\n"))
            self.assertGreater(raised, 0)
            self.assertGreater(hard_case, 0)
            self.assertEqual(policy.hook_budget(doc, pol, 1000, 10 ** 6)["excluded"], [])
            with self.assertRaises(ValueError):
                policy.hook_budget(doc, pol, 0, 100)

    def test_synthesis_context_lists_excluded_ids(self):
        for name, make in DATASETS.items():
            doc, pol = make()
            plan = plan_for(doc, pol)
            res = policy.synthesis_context(doc, pol, max_rows=3)
            self.assertEqual(len(res["included"]), 3)
            self.assertEqual(sorted(res["included"] + res["excluded"]), sorted(r["id"] for r in doc["rows"]))
            self.assertEqual(res["included"], (plan["eliminated"] + plan["check_first"])[:3])
            truth = {r["id"]: r for r in features.validate_rows(doc)["rows"]}
            self.assertEqual(res["rows"], [truth[rid] for rid in res["included"]])
            none = policy.synthesis_context(doc, pol, max_rows=0)
            self.assertEqual((none["included"], len(none["excluded"])), ([], len(doc["rows"])))


class Validation(unittest.TestCase):
    def test_policy_rejects_malformed(self):
        bad = [
            {"needs_checks": {}},
            {"needs_check": {"state": ["x"]}},
            {"needs_check": {"status": "inferred"}},
            {"superseded": {"grade": ["x"]}},
            {"locator_patterns": ["("]},
            {"locator_patterns": ["a*"]},
            {"needs_check": {"status": ["Open"]}, "established": {"status": ["open"]}},
            {"eliminated": {"status": ["gone"]}, "superseded": {"status": ["gone"]}},
            {"why_labels": {"colour:red": "x"}},
            {"why_labels": {"status:open": ""}},
            {"negations": ["   "]},
            {"negations": ["nowhere near any of these"]},  # more than four words
            {"negations": ["no long!er"]},
            {"needs_check": {"verdict": ["fatal"]}, "established": {"verdict": ["FATAL"]}},
            {"established": {"verdict": ["damaging"]}},  # also a default high_impact verdict
            {"check_first_max": 0},
            {"check_first_max": True},
            {"spot_check": {"count": -1}},
            {"spot_check": {"seed": {}}},
            {"spot_check": {"n": 3}},
            {"spot_check": 5},
            ["not", "an", "object"],
        ]
        for obj in bad:
            with self.subTest(policy=obj):
                with self.assertRaises(policy.PolicyError):
                    policy.load(obj)

    def test_policy_accepts_and_defaults(self):
        self.assertEqual(policy.validate({}), policy.DEFAULT)
        partial = policy.validate({"needs_check": {"status": ["x"]}})
        self.assertEqual(partial["needs_check"]["status"], ["x"])
        self.assertEqual(partial["needs_check"]["grade"], policy.DEFAULT["needs_check"]["grade"])
        self.assertEqual(policy.validate({"superseded": {"status": []}})["superseded"]["status"], [])
        for _, make in DATASETS.items():
            policy.load(make()[1])
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "p.json").write_text(json.dumps(synthetic.LEGAL_POLICY), encoding="utf-8")
        pol = policy.load(str(tmp / "p.json"))
        self.assertTrue(pol.has("needs_check", "verdict", "MISMATCH"))
        self.assertFalse(pol.has("needs_check", "verdict", "verified"))
        (tmp / "bad.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(policy.PolicyError):
            policy.load(str(tmp / "bad.json"))

    def test_rows_contract_both_ways(self):
        good = {"title": "t", "question": None, "groups": ["g"],
                "rows": [{"id": "a", "group": "g", "kind": "unit", "text": "x"}]}
        norm = features.validate_rows(good)
        self.assertEqual(norm["rows"][0]["locators"], [])
        self.assertIs(norm["seeded_test_copy"], False)
        broken = [
            lambda d: d["rows"][0].update(colour="red"),
            lambda d: d.update(extra=1),
            lambda d: d["rows"].append(dict(d["rows"][0])),
            lambda d: d["rows"][0].update(group="h"),
            lambda d: d["rows"][0].update(kind="opinion"),
            lambda d: d["rows"][0].update(answered="yes"),
            lambda d: d["rows"][0].update(id="a b"),
            lambda d: d["rows"][0].update(locators="p. 1"),
            lambda d: d.update(seeded_test_copy="yes"),
            lambda d: d.pop("groups"),
        ]
        for i, breaker in enumerate(broken):
            doc = copy.deepcopy(good)
            breaker(doc)
            with self.subTest(case=i):
                with self.assertRaises(features.RowsError):
                    features.validate_rows(doc)
        with self.assertRaises(ValueError):
            render.render(good, "fancy")


class Features(unittest.TestCase):
    def test_named_features_are_pure_and_bounded(self):
        doc, pol = synthetic.incident()
        pol = policy.load(pol)
        names = {"needs_check", "needs_check_reasons", "eliminated", "superseded", "replaced_by", "established",
                 "high_impact", "unanswered_high_impact", "has_numbers", "has_negation", "locator_count",
                 "relevance", "lens", "bucket"}
        for row in features.validate_rows(doc)["rows"]:
            a = features.row_features(row, pol, doc["question"])
            self.assertEqual(a, features.row_features(row, pol, doc["question"]))
            self.assertEqual(set(a), names)
            self.assertTrue(0.0 <= a["relevance"] <= 1.0)
            self.assertEqual(features.row_features(row, pol, None)["relevance"], 0.0)
        k1 = next(r for r in doc["rows"] if r["id"] == "k1")
        f = features.row_features(k1, pol)
        self.assertEqual((f["high_impact"], f["unanswered_high_impact"], f["needs_check_reasons"][0]),
                         (True, True, "unanswered_high_impact"))
        n1 = next(r for r in doc["rows"] if r["id"] == "n1")
        self.assertEqual(features.row_features(n1, pol)["needs_check_reasons"], ["unclassified"])


class Plant(unittest.TestCase):
    def test_deterministic_per_seed(self):
        doc, pol = synthetic.incident()
        self.assertEqual(plant.plant(doc, pol, seed=7), plant.plant(doc, pol, seed=7))
        base = plant.plant(doc, pol, seed=7)[1]["entries"]
        self.assertTrue(any(plant.plant(doc, pol, seed=s)[1]["entries"] != base for s in range(1, 10)))

    def test_manifest_matches_planted_rows(self):
        for name, make in DATASETS.items():
            doc, pol = make()
            pol_obj = policy.load(pol)
            truth = features.validate_rows(doc)
            planted, manifest = plant.plant(doc, pol, seed=11, per_type=1)
            self.assertEqual(doc, make()[0])  # input untouched
            self.assertEqual(manifest["shortfall"], [])
            entries = manifest["entries"]
            ids = [e["row_id"] for e in entries]
            self.assertEqual(len(ids), len(set(ids)))  # never twice in one row
            self.assertEqual(manifest["planted_total"], len(entries))
            self.assertEqual({e["type"] for e in entries}, set(plant.TYPES))
            t_rows = {r["id"]: r for r in truth["rows"]}
            p_rows = {r["id"]: r for r in planted["rows"]}
            plan = plan_for(planted, pol)
            for e in entries:
                t, p = t_rows[e["row_id"]], p_rows[e["row_id"]]
                if e["field"] == "locators":
                    i = e["detail"]["index"]
                    self.assertEqual((t["locators"][i], p["locators"][i]), (e["original"], e["planted"]))
                else:
                    self.assertEqual((t[e["field"]], p[e["field"]]), (e["original"], e["planted"]))
                self.assertNotEqual(e["original"], e["planted"])
                self.assertEqual([k for k in t if t[k] != p[k]], [e["field"]])
                bucket = plan["features"][e["row_id"]]["bucket"]
                self.assertEqual(e["lands_in"], "needs_check" if bucket == "check_first" else bucket)
                if e["type"] == "status_upgrade":
                    self.assertTrue(pol_obj.has("needs_check", "status", e["original"]))
                    self.assertTrue(pol_obj.has("established", "status", e["planted"]))
                elif e["type"] == "verdict_soften":
                    if e["field"] == "verdict":
                        self.assertFalse(pol_obj.has("high_impact", "verdict", e["planted"]))
                    else:
                        self.assertEqual((e["original"], e["planted"]), (False, True))
                elif e["type"] == "negation_drop":
                    self.assertEqual(len(pol_obj.negation_re.findall(e["planted"])),
                                     len(pol_obj.negation_re.findall(e["original"])) - 1)
                elif e["type"] == "number_change":
                    self.assertNotEqual(features.NUMBER_RE.findall(e["original"]), features.NUMBER_RE.findall(e["planted"]))
            for rid in t_rows:
                if rid not in ids:
                    self.assertEqual(t_rows[rid], p_rows[rid])
            lands = {e["lands_in"] for e in entries}
            self.assertIn("established", lands)  # the hard case
            self.assertTrue(lands - {"established"})
            result = metrics.compare(planted, pol, manifest)
            self.assertTrue(result["all_preserved"])
            self.assertTrue(all(v is not None for v in result["modes"]["speedread"]["first_line"]["planted"].values()))

    def test_shortfall_is_recorded_and_fails_the_command(self):
        doc, pol = synthetic.incident()
        _, manifest = plant.plant(doc, pol, seed=1, per_type=5)
        self.assertTrue(manifest["shortfall"])
        self.assertTrue(all(s["planted"] < s["wanted"] for s in manifest["shortfall"]))
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "rows.json").write_text(json.dumps(doc), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(plant.main([str(tmp / "rows.json"), str(tmp / "out"), "--per-type", "5"]), 1)
        self.assertTrue((tmp / "out" / "manifest.json").exists())
        with self.assertRaises(ValueError):
            plant.plant(doc, pol, types=["typo"])


class Lookup(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.doc, _ = synthetic.incident()
        self.truth = self.tmp / "truth.json"
        self.truth.write_text(json.dumps(self.doc), encoding="utf-8")
        self.log = self.tmp / "log.jsonl"

    def run_lookup(self, rid, budget, truth=None):
        out, err = io.StringIO(), io.StringIO()
        code = lookup.lookup(str(truth or self.truth), str(self.log), rid, budget, out=out, err=err)
        return code, out.getvalue()

    def test_budget_is_enforced(self):
        self.assertEqual(self.run_lookup("nope", 2)[0], 4)
        code, printed = self.run_lookup("u1", 2)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed), features.validate_rows(self.doc)["rows"][0])
        self.assertEqual(self.run_lookup("c2", 2)[0], 0)
        self.assertEqual(self.run_lookup("k1", 2), (3, ""))
        self.assertEqual(self.run_lookup("k1", 2), (3, ""))
        results = [e["result"] for e in lookup.read_log(self.log)]
        self.assertEqual(results, ["not_found", "ok", "ok", "refused", "refused"])
        self.assertEqual(score.score({"entries": []}, [], lookup.read_log(self.log))["lookups_used"], 2)

    def test_zero_budget_refuses_and_bad_inputs_are_not_executed(self):
        self.assertEqual(self.run_lookup("u1", 0)[0], 3)
        planted, _ = plant.plant(self.doc, synthetic.INCIDENT_POLICY, seed=1, per_type=1)
        seeded = self.tmp / "seeded.json"
        seeded.write_text(json.dumps(planted), encoding="utf-8")
        self.assertEqual(self.run_lookup("u1", 5, truth=seeded)[0], 2)
        self.log.write_text("{broken\n", encoding="utf-8")
        self.assertEqual(self.run_lookup("u1", 5)[0], 2)
        self.assertEqual(self.run_lookup("u1", -1)[0], 2)


class Score(unittest.TestCase):
    MANIFEST = {"entries": [
        {"row_id": "a", "type": "status_upgrade", "field": "status", "lands_in": "established"},
        {"row_id": "b", "type": "locator_shift", "field": "locators", "lands_in": "needs_check"},
        {"row_id": "c", "type": "negation_drop", "field": "text", "lands_in": "established"},
        {"row_id": "d", "type": "number_change", "field": "text", "lands_in": "needs_check"},
    ]}

    def test_score_math(self):
        findings = [
            {"row_id": "a", "field": "status", "note": "status looks upgraded"},
            {"row_id": "a", "field": "Status", "note": "again"},
            {"row_id": "b", "field": "locator[0]", "note": "page shifted"},
            {"row_id": "c", "field": "status", "note": "wrong field on a planted row"},
            {"row_id": "z", "field": None, "note": "nothing planted here"},
            {"row_id": "d", "note": "no field given"},
        ]
        log = [{"result": "ok"}, {"result": "ok"}, {"result": "refused"}, {"result": "not_found"}]
        res = score.score(self.MANIFEST, findings, log)
        self.assertEqual((res["planted"], res["caught"], res["missed"]), (4, 3, 1))
        self.assertEqual((res["false_alarms"], res["right_row_wrong_field"], res["duplicates"]), (2, 1, 1))
        self.assertEqual((res["recall"], res["precision"]), (0.75, 0.6))
        self.assertEqual((res["lookups_used"], res["lookups_refused"], res["lookups_not_found"]), (2, 1, 1))
        self.assertEqual(res["by_type"]["negation_drop"], {"planted": 1, "caught": 0, "missed": 1, "recall": 0.0})
        self.assertEqual(res["by_lands_in"]["established"], {"planted": 2, "caught": 1, "missed": 1, "recall": 0.5})
        self.assertEqual(res["by_lands_in"]["needs_check"]["recall"], 1.0)
        self.assertEqual((res["caught_ids"], res["missed_ids"]), (["a", "b", "d"], ["c"]))

    def test_empty_and_malformed(self):
        res = score.score(self.MANIFEST, {"findings": []})
        self.assertEqual((res["caught"], res["recall"], res["precision"]), (0, 0.0, None))
        for bad in ([{"field": "status"}], [{"row_id": "a", "field": 3}], "nope"):
            with self.assertRaises(ValueError):
                score.score(self.MANIFEST, bad)


class Metrics(unittest.TestCase):
    def test_compare_reports_every_mode(self):
        doc, pol = synthetic.incident()
        res = metrics.compare(doc, pol)
        self.assertTrue(res["all_preserved"])
        self.assertEqual(set(res["modes"]), set(render.MODES))
        for mode, m in res["modes"].items():
            self.assertEqual(set(m["visible_within"]), {"10", "20", "40"})
            self.assertGreater(m["chars"], 0)
        speed = res["modes"]["speedread"]["first_line"]["check_first"]
        positions = [speed[rid] for rid in res["check_first_ids"]]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(res["modes"]["speedread"]["visible_within"]["20"]["check_first"],
                         {"visible": len(positions), "of": len(positions)})
        claims = res["modes"]["claims"]["first_line"]["check_first"]
        self.assertIsNone(claims["k1"])  # a critique is outside the claims view


class TwoLayer(unittest.TestCase):
    """The v1.1 scan layer and its Details layer: what is shown first, and what is never lost."""

    def test_details_layer_holds_every_row_once_and_the_scan_layer_names_them_all(self):
        for name, make in ALL.items():
            doc, pol = make()
            for mode in render.TWO_LAYER:
                text, scan, details = layers(doc, pol, mode)
                with self.subTest(dataset=name, mode=mode):
                    self.assertIsNotNone(details)
                    self.assertEqual(sorted(entry_ids(details.split("\n"))), sorted(r["id"] for r in doc["rows"]))
                    body = metrics.normalize(details)
                    for row in doc["rows"]:
                        self.assertIn("`%s`" % row["id"], scan)  # nothing is hidden below the fold
                        self.assertIn(metrics.normalize_input(row["text"]), body)
                    res = metrics.preservation(scope_rows(doc, mode), text, pol, mode)
                    self.assertTrue(res["ok"], {c: res[c]["missing"] for c in metrics.CATEGORIES})
                    self.assertEqual(res["pct"], 100.0)
                    self.assertLess(len(scan), len(text))

    def test_preservation_fails_when_the_details_section_drops_a_row(self):
        doc, pol = synthetic.review()
        real = render._details
        dropped = []

        for position in (0, -1):
            def broken(doc_, pol_, plan, order, bold, _at=position):
                cut = list(order)
                dropped.append(cut.pop(_at))
                return real(doc_, pol_, plan, cut, bold)
            with mock.patch.object(render, "_details", broken):
                for mode in render.TWO_LAYER:
                    del dropped[:]
                    text = render.render(doc, mode, pol)
                    res = metrics.preservation(scope_rows(doc, mode), text, pol, mode)
                    with self.subTest(position=position, mode=mode):
                        self.assertFalse(res["ok"])
                        self.assertIn(dropped[0], res["ids"]["missing"])
                        self.assertLess(res["pct"], 100.0)
        for mode in render.TWO_LAYER:  # restored
            res = metrics.preservation(scope_rows(doc, mode), render.render(doc, mode, pol), pol, mode)
            self.assertTrue(res["ok"], mode)

    def test_collapse_groups_open_critiques_and_keeps_every_id(self):
        doc, pol = synthetic.review()
        pol_obj = policy.load(pol)
        plan = plan_for(doc, pol)
        entries = render.check_entries(plan, pol_obj)
        ids = [rid for e in entries for rid in e["ids"]]
        self.assertEqual(sorted(ids), sorted(plan["check_first"]))  # every check-first row, exactly once
        self.assertEqual(len(ids), len(set(ids)))
        collapsed = [e for e in entries if e["collapsed"]]
        self.assertTrue(collapsed)
        self.assertLess(len(entries), len(ids))  # the list really is shorter than the rows
        for item in collapsed:
            rows = [plan["rows"][rid] for rid in item["ids"]]
            self.assertEqual({(r["verdict"] or "").lower() for r in rows}, {(item["verdict"] or "").lower()})
            self.assertEqual({r["lens"] for r in rows}, {item["lens"]})
            self.assertEqual({(r["links"] or [None])[0] for r in rows}, {item["target"]})
            for row in rows:
                self.assertEqual(row["kind"], "critique")
                self.assertTrue(plan["features"][row["id"]]["unanswered_high_impact"])
        severities = [pol_obj.severity(e["verdict"]) for e in entries
                      if plan["features"][e["ids"][0]]["unanswered_high_impact"]]
        self.assertEqual(severities, sorted(severities))  # fatal first
        _text, scan, _details = layers(doc, pol, "speedread")
        for rid in ids:
            self.assertIn("`%s`" % rid, scan)
        line = next(x for x in scan.split("\n") if "`%s`" % collapsed[0]["ids"][0] in x)
        self.assertIn("unanswered", line)
        self.assertIn("critiques", line)
        for rid in collapsed[0]["ids"]:
            self.assertIn("`%s`" % rid, line)

    def test_check_first_max_overflows_into_more_to_check_and_never_drops_a_row(self):
        doc, pol = synthetic.review()
        for cap in (1, 2, 6, 1000):
            settings = dict(pol, check_first_max=cap)
            pol_obj = policy.load(settings)
            valid = features.validate_rows(doc)
            plan = features.classify(valid, pol_obj)
            lay = render.layout(valid, pol_obj, plan)
            text = render.render(doc, "speedread", settings)
            scan, _details = metrics.split_layers(text, "speedread")
            n, x, k, s, m, t, _a, _b = map(int, SCAN_HEADER.search(text).groups())
            with self.subTest(cap=cap):
                self.assertLessEqual(len(lay["check"]), cap)
                self.assertEqual(sorted(lay["check_ids"] + lay["more_ids"]), sorted(plan["check_first"]))
                self.assertEqual(set(lay["check_ids"]) & set(lay["more_ids"]), set())
                self.assertEqual((n, x), (lay["check_rows"], lay["more_rows"]))
                self.assertEqual(n + x + k + s + m, t)
                self.assertEqual(len(row_ids(section(text, "## More to check (%d)" % x))), x)
                self.assertLess(scan.index("## More to check"), scan.index("## Established"))
                for rid in plan["check_first"]:
                    self.assertIn("`%s`" % rid, scan)  # over budget, never out of sight
                self.assertTrue(metrics.preservation(scope_rows(doc, "speedread"), text, settings, "speedread")["ok"])
        self.assertGreater(render.layout(features.validate_rows(doc), policy.load(dict(pol, check_first_max=1)),
                                         plan_for(doc, pol))["more_rows"], 0)

    def test_spot_check_samples_established_rows_deterministically(self):
        doc, pol = synthetic.review()
        valid = features.validate_rows(doc)
        pol_obj = policy.load(pol)
        plan = features.classify(valid, pol_obj)
        sample = render.spot_sample(valid, pol_obj, plan)
        self.assertEqual(sample, render.spot_sample(valid, policy.load(pol), plan))  # same seed, same sample
        self.assertEqual(len(sample), pol_obj.spot_check["count"])
        self.assertEqual(len(set(sample)), len(sample))
        for rid in sample:
            self.assertEqual(plan["features"][rid]["bucket"], "established")  # only ever established rows
        seeded = {tuple(render.spot_sample(valid, policy.load(dict(pol, spot_check={"count": 4, "seed": s})), plan))
                  for s in range(8)}
        self.assertGreater(len(seeded), 1)  # the seed decides which rows
        titled = {tuple(render.spot_sample(features.validate_rows(dict(copy.deepcopy(doc), title=t)),
                                           policy.load(dict(pol, spot_check={"count": 4})), plan))
                  for t in ("one", "two", "three", "four", "five")}
        self.assertGreater(len(titled), 1)  # the default seed follows the title, not the run
        everything = render.spot_sample(valid, policy.load(dict(pol, spot_check={"count": 999})), plan)
        self.assertEqual(everything, plan["established"])
        text = render.render(doc, "speedread", pol)
        n, x, k, s, m, t, a, b = map(int, SCAN_HEADER.search(text).groups())
        self.assertEqual((a, b), (len(sample), m))
        self.assertEqual(n + x + k + s + m, t)  # the sample is counted apart; the buckets still reconcile
        head = "## Spot check (random, %d of %d established)" % (a, b)
        self.assertEqual(entry_ids(section(text, head)), sample)
        established = entry_ids(section(text, "## Established (%d)" % m))
        for rid in sample:
            self.assertIn(rid, established)  # a sample, not a move
        self.assertIn("random sample", text)
        self.assertLess(text.index(head), text.index("## Established (%d)" % m))
        off = dict(pol, spot_check={"count": 0})
        zero = render.render(doc, "speedread", off)
        self.assertNotIn("## Spot check", zero)
        self.assertIn("Spot check 0 of %d" % m, zero)
        self.assertTrue(metrics.preservation(scope_rows(doc, "speedread"), zero, off, "speedread")["ok"])

    def test_metrics_report_the_scan_layer_and_the_planted_rows_in_the_sample(self):
        doc, pol = synthetic.review()
        planted, manifest = plant.plant(doc, pol, seed=4, per_type=1)
        res = metrics.compare(planted, pol, manifest)
        self.assertTrue(res["all_preserved"])
        spot = res["spot_check"]
        self.assertEqual(len(spot["sample"]), policy.load(pol).spot_check["count"])
        self.assertTrue(set(spot["planted_in_sample"]) <= set(spot["sample"]) & set(res["planted_ids"]))
        self.assertTrue(set(spot["planted_established"]) <= set(res["planted_ids"]))
        for mode in render.TWO_LAYER:
            m = res["modes"][mode]
            self.assertLess(m["scan_chars"], m["chars"])
            self.assertLess(m["scan_lines"], m["lines"])
            for rid, line in m["first_line"]["check_first"].items():
                self.assertTrue(line is None or line <= m["scan_lines"])  # measured over the scan layer
        for mode in ("full", "claims"):
            self.assertEqual(res["modes"][mode]["scan_chars"], res["modes"][mode]["chars"])

    def test_lead_is_a_verbatim_prefix_cut_at_a_word_boundary(self):
        cases = ["Witness A testified that the school was not contacted before March 3, 2025",
                 "Q. Did anyone call the school? A. No, nobody did.",
                 "Brief p. 12 quotes Tr. 45:14 as x. Second sentence here.",
                 "Alert fired at 10:14:30Z\nPager acknowledged at 10:19 on INC-4411",
                 "word " * 60, "", "   "]
        for text in cases:
            out, cut = render.lead(text)
            with self.subTest(text=text[:32]):
                self.assertTrue(text.startswith(out))  # never rewritten
                self.assertLessEqual(len(out), render.LEAD_MAX)
                self.assertEqual(cut, len(out) < len(text.rstrip()))
                if cut and out:
                    nxt = text[len(out):len(out) + 1]
                    self.assertTrue(nxt.isspace() or out[-1] in ".!?" or " " not in out)
        self.assertEqual(render.lead("x" * 400)[0], "x" * render.LEAD_MAX)
        for name, make in ALL.items():
            doc, _pol = make()
            for row in doc["rows"]:
                out, _cut = render.lead(row["text"])
                self.assertTrue(row["text"].startswith(out), (name, row["id"]))

    def test_multi_word_negation_phrase_is_bolded_whole(self):
        doc = {"title": "t", "question": None, "groups": ["g"], "rows": [
            {"id": "a", "group": "g", "kind": "claim", "text": "The alarm is no longer wired to the pager",
             "status": "confirmed"},
            {"id": "b", "group": "g", "kind": "claim", "text": "There is no pager", "status": "confirmed"}]}
        pol = {"negations": ["no longer"]}
        text = render.render(doc, "speedread", pol)
        self.assertIn("**no longer**", text)
        self.assertNotIn("**no** longer", text)
        self.assertIn("**no** pager", text)  # the one-word negation still matches alone
        pol_obj = policy.load(pol)
        self.assertTrue(pol_obj.negation_re.search("the alarm is NO   longer wired"))  # case and whitespace
        self.assertEqual(pol_obj.negation_re.findall("no longer and no longer"), ["no longer", "no longer"])
        self.assertTrue(metrics.preservation(features.validate_rows(doc)["rows"], text, pol, "speedread")["ok"])
        review, review_pol = synthetic.review()
        self.assertIn("**no longer**", render.render(review, "speedread", review_pol))

    def test_established_verdict_is_accepted_and_contradictions_are_rejected(self):
        pol = policy.load({"established": {"verdict": ["verified"]}})
        self.assertTrue(pol.has("established", "verdict", "VERIFIED"))
        doc = {"title": "t", "question": None, "groups": ["g"], "rows": [
            {"id": "c", "group": "g", "kind": "citation", "text": "the quote matches the page", "verdict": "VERIFIED"}]}
        row = features.validate_rows(doc)["rows"][0]
        feats = features.row_features(row, pol)
        self.assertEqual((feats["established"], feats["bucket"], feats["needs_check_reasons"]),
                         (True, "established", []))
        self.assertEqual(features.row_features(row, policy.load({}))["bucket"], "check_first")  # default unchanged
        self.assertEqual(plan_for(*synthetic.review())["features"]["cit1"]["bucket"], "established")
        for bad in ({"needs_check": {"verdict": ["fatal"]}, "established": {"verdict": ["FATAL"]}},
                    {"high_impact": {"verdict": ["blocking"]}, "established": {"verdict": ["Blocking"]}}):
            with self.subTest(policy=bad):
                with self.assertRaises(policy.PolicyError):
                    policy.load(bad)


if __name__ == "__main__":
    unittest.main()
