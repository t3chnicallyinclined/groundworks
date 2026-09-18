# -*- coding: utf-8 -*-
"""Invariants that must hold for ANY valid config and corpus, over generated ones (docs/TESTING.md s.6).

No server, standard library only. The configs come from tests/harness/gen.py with generated vocabulary, so
none of these can pass because the demo happens to use a particular name. Each property prints one line:
`PROPERTY name: n generated, n checked, n discarded`; a property that checked nothing fails.

    python -m unittest tests.test_invariants -v
"""
import collections
import copy
import json
import pathlib
import random
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.harness import gen, props  # noqa: E402
from groundworks import config, gates  # noqa: E402
from groundworks.pipeline import agreement, chunker, loader, validator  # noqa: E402

N = 150          # generated cases per property; every one is seed-reproducible
SEED = 20260916


def _config(rng):
    return config.load(gen.gen_config(rng))


class GeneratorTests(unittest.TestCase):
    """The generator is itself checked: an invalid or irreproducible generator tests nothing."""

    def test_the_same_seed_gives_byte_identical_cases_and_different_seeds_differ(self):
        self.assertEqual(gen.digest(gen.gen_case(17)), gen.digest(gen.gen_case(17)))
        self.assertNotEqual(gen.digest(gen.gen_case(17)), gen.digest(gen.gen_case(18)))

    def test_generated_configs_load_and_a_refusal_is_counted_not_dropped(self):
        result = gen.acceptance(range(SEED, SEED + 300), config.load)
        refused = len(result["refused"])
        print("GENERATOR configs: %d generated, %d accepted, %d refused%s" % (
            result["generated"], result["accepted"], refused,
            "" if not refused else " (first: seed %s: %s)" % result["refused"][0]))
        self.assertLessEqual(refused, result["generated"] * 0.05,
                             "more than 5%% of generated configs are refused, so the generator is broken: %s"
                             % result["refused"][:3])

    def test_the_generated_configs_are_varied_and_use_none_of_the_demo_names(self):
        demo = config.load(pathlib.Path(__file__).resolve().parents[1] / "instances" / "demo-incident" / "instance.yaml")
        demo_names = set(demo.status_values()) | set(demo.grades()) | set(demo.unit_names()) | \
            {r["id"] for r in demo.gate_rules()} | set(demo.data.get("edges") or {})
        seen = collections.Counter()
        for seed in range(SEED, SEED + 200):
            cfg = gen.gen_config(random.Random(seed))
            names = set(cfg["status"]["values"]) | set(cfg["fidelity"]["order"]) | \
                {u["name"] for u in cfg["units"]} | {r["id"] for r in cfg["gates"]} | set(cfg.get("edges") or {})
            self.assertFalse(names & demo_names, "seed %d reused demo names %s" % (seed, names & demo_names))
            seen["declared tables"] += bool(cfg.get("tables"))
            seen["forbids"] += any(r.get("forbids") for r in cfg["gates"])
            seen["chained evidence"] += any(c.get("to") == ["fact"] for r in cfg["gates"] for c in r["requires"])
            seen["unique"] += "unique" in cfg
            seen["chunk_by"] += any(u.get("chunk_by") for u in cfg["units"])
        for feature in ("declared tables", "forbids", "chained evidence", "unique", "chunk_by"):
            self.assertGreater(seen[feature], 10, "only %d of 200 configs exercise %s" % (seen[feature], feature))


class ChunkerInvariantTests(unittest.TestCase):

    def test_every_unit_lands_in_exactly_one_slice_no_larger_than_its_chunk_size(self):
        def prop(case):
            inst = config.load(case["config"])
            if not case["units"]:
                raise props.Discard()
            with tempfile.TemporaryDirectory() as tmp:
                work = pathlib.Path(tmp)
                (work / "units.jsonl").write_text("".join(json.dumps(u) + "\n" for u in case["units"]),
                                                  encoding="utf-8")
                chunks = chunker.chunk(work / "units.jsonl", work, inst)
                placed = collections.Counter()
                for chunk in chunks:
                    rows = [json.loads(line) for line in (work / "slices" / (chunk["id"] + ".jsonl"))
                            .read_text(encoding="utf-8").splitlines()]
                    placed.update(r["id"] for r in rows)
                    size = inst.unit_type(chunk["type"])["chunk_size"]
                    assert len(rows) <= size, "slice %s holds %d > chunk_size %d" % (chunk["id"], len(rows), size)
                    assert all(r["type"] == chunk["type"] for r in rows), "a slice mixes unit types"
            ids = collections.Counter(u["id"] for u in case["units"])
            assert placed == ids, "placed %s, expected each of %d units once" % (
                dict((placed - ids) + (ids - placed)), len(ids))

        props.assert_holds(self, props.check("chunk_exactly_once", prop, lambda rng: gen.gen_case(rng.getrandbits(32)),
                                             n=N, seed=SEED), min_checked=N // 2)

    def test_a_chunk_by_value_spans_two_slices_only_when_it_alone_exceeds_the_chunk_size(self):
        """Units sharing the chunk_by value stay in one slice wherever they sit in the input (2026-09-16:
        only adjacent units were grouped, so an interleaved value was split without a word)."""
        def prop(case):
            inst = config.load(case["config"])
            grouped = [u for u in inst.units if u.get("chunk_by")]
            if not case["units"] or not grouped:
                raise props.Discard()
            with tempfile.TemporaryDirectory() as tmp:
                work = pathlib.Path(tmp)
                (work / "units.jsonl").write_text("".join(json.dumps(u) + "\n" for u in case["units"]),
                                                  encoding="utf-8")
                chunks = chunker.chunk(work / "units.jsonl", work, inst)
            by_id = {u["id"]: u for u in case["units"]}
            for utype in grouped:
                field, size = utype["chunk_by"], utype["chunk_size"]
                slices, members = collections.defaultdict(set), collections.Counter()
                for chunk in chunks:
                    if chunk["type"] != utype["name"]:
                        continue
                    for uid in chunk["units"]:
                        value = str(by_id[uid]["locator"].get(field))
                        slices[value].add(chunk["id"])
                        members[value] += 1
                for value, ids in slices.items():
                    assert len(ids) == 1 or members[value] > size, (
                        "%s value %r (%d units, chunk_size %d) is split across %s"
                        % (utype["name"], value, members[value], size, sorted(ids)))

        props.assert_holds(self, props.check("chunk_by_one_slice_per_value", prop,
                                             lambda rng: gen.gen_case(rng.getrandbits(32)), n=N, seed=SEED),
                           min_checked=N // 5)


class ValidatorInvariantTests(unittest.TestCase):
    """Oracle labels pass; each single planted defect yields an error naming that unit (D13)."""

    PLANTS = ("misquote", "missing", "duplicate", "bad_tier")

    @staticmethod
    def _validate(inst, units, labels):
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            (work / "slice.jsonl").write_text("".join(json.dumps(u) + "\n" for u in units), encoding="utf-8")
            (work / "labels.jsonl").write_text("".join(json.dumps(l) + "\n" for l in labels), encoding="utf-8")
            return validator.validate(work / "slice.jsonl", work / "labels.jsonl", inst)

    def test_oracle_labels_pass_and_every_planted_defect_names_its_unit(self):
        def generate(rng):
            case = gen.gen_case(rng.getrandbits(32))
            case["plant"] = rng.choice(self.PLANTS)
            case["target"] = rng.randrange(max(1, len(case["units"])))
            return case

        def prop(case):
            inst = config.load(case["config"])
            units, labels = case["units"], case["labels"]
            if not units or len(labels) != len(units) or case["target"] >= len(units):
                raise props.Discard()
            clean = self._validate(inst, units, labels)
            assert clean["errors"] == [], "oracle labels refused: %s" % clean["errors"][:3]
            broken = copy.deepcopy(labels)
            target = broken[case["target"]]
            uid = target["id"]
            if case["plant"] == "misquote":
                if target["tier"] < 2:
                    raise props.Discard()
                target["quote"] = target["quote"] + " zzqx"
            elif case["plant"] == "missing":
                broken.pop(case["target"])
            elif case["plant"] == "duplicate":
                broken.append(copy.deepcopy(target))
            elif case["plant"] == "bad_tier":
                target["tier"] = max(inst.vocabulary["tiers"]) + 1
            errors = self._validate(inst, units, broken)["errors"]
            assert any(e.get("unit") == uid for e in errors), \
                "planted %s on %s produced no error naming it: %s" % (case["plant"], uid, errors[:3])

        props.assert_holds(self, props.check("validator_plants", prop, generate, n=N, seed=SEED), min_checked=N // 2)


class BaselineInvariantTests(unittest.TestCase):
    """compare_baseline: unchanged is clean, every weakening is caught, an addition is never a failure (D1, D2)."""

    WEAKENINGS = ("drop_rule", "drop_clause", "drop_where", "lower_min", "repoint_value", "drop_unique")

    @staticmethod
    def weaken(cfg, how, rng):
        """The weakened config, or None when this config has nothing of that kind to weaken."""
        data = copy.deepcopy(cfg)
        rules = data["gates"]
        if how == "drop_rule":
            if len(rules) < 2:
                return None
            rules.pop(rng.randrange(len(rules)))
        elif how == "drop_clause":
            options = [(r, k, i) for r in rules for k in ("requires", "forbids") for i in range(len(r.get(k) or []))
                       if len(r.get("requires") or []) + len(r.get("forbids") or []) > 1]
            if not options:
                return None
            rule, kind, index = rng.choice(options)
            rule[kind].pop(index)
            if not rule[kind]:
                del rule[kind]
        elif how == "drop_where":
            options = [c for r in rules for k in ("requires", "forbids") for c in (r.get(k) or []) if c.get("where")]
            if not options:
                return None
            del rng.choice(options)["where"]
        elif how == "lower_min":
            options = [c for r in rules for c in (r.get("requires") or []) if c.get("min", 1) > 1]
            if not options:
                return None
            rng.choice(options)["min"] -= 1
        elif how == "repoint_value":
            rule = rng.choice(rules)
            if isinstance(rule["value"], bool):
                rule["value"] = not rule["value"]
            elif rule["table"] == "fact":
                rule["value"] = "ruled_out"
            else:
                field = data["tables"][rule["table"]]["fields"][rule["field"]]
                others = [v for v in field["one_of"] if v != rule["value"]]
                rule["value"] = others[0]
        elif how == "drop_unique":
            if "unique" not in data:
                return None
            del data["unique"]
            data["human_owned"].remove("unique")
        return data

    def test_unchanged_is_clean_every_weakening_is_caught_and_an_addition_is_not_a_failure(self):
        def generate(rng):
            return {"config": gen.gen_config(rng), "how": rng.choice(self.WEAKENINGS), "pick": rng.getrandbits(16)}

        exercised = collections.Counter()

        def prop(case):
            try:
                inst = config.load(case["config"])
            except config.ConfigError:
                raise props.Discard()
            base = {"spec": gates.spec(inst), "report": {"rules": []}}
            same = gates.compare_baseline(base, None, gates.spec(inst))
            assert same == [], "an unchanged config compared as weaker: %s" % same
            weak = self.weaken(case["config"], case["how"], random.Random(case["pick"]))
            if weak is None:
                raise props.Discard()
            try:
                weak_inst = config.load(weak)
            except config.ConfigError:
                raise props.Discard()
            failures = gates.compare_baseline(base, None, gates.spec(weak_inst))
            assert failures, "weakening %s went unseen by compare_baseline" % case["how"]
            exercised[case["how"]] += 1
            stronger = copy.deepcopy(case["config"])
            stronger["gates"][0].setdefault("requires", []).append({"edge": "supports", "from": ["fact"], "min": 1}
                                                                   if stronger["gates"][0]["table"] == "conclusion"
                                                                   else stronger["gates"][0]["requires"][0])
            try:
                strong_inst = config.load(stronger)
            except config.ConfigError:
                return
            added = gates.compare_baseline(base, None, gates.spec(strong_inst))
            assert added == [], "adding a clause was reported as a weakening: %s" % added

        props.assert_holds(self, props.check("baseline_weakening", prop, generate, n=N * 2, seed=SEED),
                           min_checked=N // 2)
        print("PROPERTY baseline_weakening exercised: %s" % dict(exercised))
        for how in self.WEAKENINGS:     # a weakening never generated is a blind spot this property does not cover
            self.assertGreaterEqual(exercised[how], 5, "weakening %s was checked only %d time(s)" % (how, exercised[how]))


class ReconcileInvariantTests(unittest.TestCase):
    """loader.reconcile, the V2-19 decision (D16), over every combination of stored, corrected and incoming."""

    @staticmethod
    def generate(rng):
        values = {"status": ["s1", "s2", "s3"], "grade": ["g1", "g2", "g3"]}
        unit = {"id": "u1", "status": rng.choice(values["status"]), "grade": rng.choice(values["grade"])}
        if rng.random() < 0.15:
            return {"existing": None, "unit": unit, "accept": []}
        existing = {"status": rng.choice(values["status"]), "grade": rng.choice(values["grade"])}
        if rng.random() < 0.8:
            existing["source"] = {f: rng.choice(values[f]) if rng.random() < 0.4 else existing[f]
                                  for f in ("status", "grade")}
            existing["source"]["run"] = "r0"
        existing["corrected"] = {f: {"by": "reviewer", "reason": "corrected"} for f in ("status", "grade")
                                 if rng.random() < 0.4} or None
        accept = [["u1", f] for f in ("status", "grade") if rng.random() < 0.25]
        return {"existing": existing, "unit": unit, "accept": accept}

    @staticmethod
    def held(existing, field, incoming):
        if existing is None:
            return False
        source, corrected = existing.get("source") or {}, existing.get("corrected") or {}
        if corrected.get(field):
            return True
        if field not in source:
            return existing[field] != incoming
        return existing[field] != source[field]

    def test_a_held_field_is_never_overwritten_unless_named_and_every_disagreement_is_reported(self):
        def prop(case):
            existing, unit = case["existing"], case["unit"]
            accept = {tuple(p) for p in case["accept"]}
            plan = loader.reconcile(existing, unit, "r1", accept)
            conflicts = {(c["unit"], c["field"]) for c in plan["conflict"]}
            followed = {(c["unit"], c["field"]) for c in plan["followed"]}
            for field in ("status", "grade"):
                incoming = unit[field]
                if existing is None:
                    assert plan["set"][field] == incoming, "a new unit must take the source value"
                    continue
                if self.held(existing, field, incoming):
                    if ("u1", field) in accept:
                        assert plan["set"].get(field) == incoming, "a named acceptance did not take the source"
                    else:
                        assert field not in plan["set"], "held %s was overwritten without being named" % field
                        if existing[field] != incoming:
                            assert ("u1", field) in conflicts, "held %s differs from the source, no conflict" % field
                else:
                    assert plan["set"].get(field) == incoming, "an unheld %s did not follow the source" % field
                    assert (("u1", field) in followed) == (existing[field] != incoming), \
                        "followed does not list exactly the unheld changes"
            # applying the plan and reconciling again with the same source changes nothing further
            if existing is not None:
                after = dict(existing)
                after.update(plan["set"])
                after["source"] = plan["source"]
                corrected = dict(existing.get("corrected") or {})
                for field, hold in plan["corrected"].items():
                    if hold is None:
                        corrected.pop(field, None)
                    else:
                        corrected[field] = hold
                after["corrected"] = corrected or None
                again = loader.reconcile(after, unit, "r2", frozenset())
                assert all(after[f] == v for f, v in again["set"].items()), "a second identical load changed %s" % again["set"]
                assert again["followed"] == [], "a second identical load followed %s" % again["followed"]

        props.assert_holds(self, props.check("reconcile", prop, self.generate, n=N * 3, seed=SEED), min_checked=N)


class KappaInvariantTests(unittest.TestCase):
    """agreement.kappa (D11): symmetric, bounded or None, None exactly when chance agreement is 1."""

    @staticmethod
    def generate(rng):
        values = ["a", "b", "c", "d"][:rng.randint(1, 4)]
        return {"pairs": [[rng.choice(values), rng.choice(values)] for _ in range(rng.randint(0, 30))],
                "order": rng.getrandbits(16)}

    @staticmethod
    def chance(pairs):
        n = len(pairs)
        cats = {str(a) for a, _ in pairs} | {str(b) for _, b in pairs}
        return sum((sum(1 for a, _ in pairs if str(a) == c) / n) * (sum(1 for _, b in pairs if str(b) == c) / n)
                   for c in cats)

    def test_kappa_is_symmetric_bounded_order_free_and_undefined_exactly_when_chance_is_one(self):
        def prop(case):
            pairs = [tuple(p) for p in case["pairs"]]
            k = agreement.kappa(pairs)
            if not pairs:
                assert k is None, "kappa over no pairs must be None, got %r" % k
                return
            assert k == agreement.kappa([(b, a) for a, b in pairs]), "kappa is not symmetric"
            shuffled = list(pairs)
            random.Random(case["order"]).shuffle(shuffled)
            assert k == agreement.kappa(shuffled), "kappa depends on the order of the units"
            assert (k is None) == (self.chance(pairs) >= 1.0 - 1e-12), \
                "kappa %r but chance agreement %r" % (k, self.chance(pairs))
            if k is not None:
                assert -1.0 <= k <= 1.0, "kappa %r out of [-1, 1]" % k
            values = {a for a, _ in pairs}
            if len(values) >= 2:
                assert agreement.kappa([(a, a) for a, _ in pairs]) == 1.0, "identical labels over 2+ values must be 1.0"

        props.assert_holds(self, props.check("kappa", prop, self.generate, n=N * 3, seed=SEED), min_checked=N)


class GateShapeInvariantTests(unittest.TestCase):
    """Generated gate text and probe plans name only this instance's own tables and edges (D9), and every
    constrained or forbidding clause gets a probe or a named reason (D1, D9)."""

    def test_gate_expressions_name_only_the_instances_own_edges_and_tables(self):
        import re

        def prop(case):
            inst = config.load(case)
            # Membership alone is not enough: the first version of this property only asked whether the
            # table reached was SOME table of this instance, and a traversal hard-coded to reach `unit`
            # survived it (registry M114). Each hop must reach the end its edge really joins.
            edges = inst.edge_map()
            for rule in inst.gate_rules():
                text = gates.gate_expression(rule, inst)
                hops = re.findall(r"\$this(->|<-)(\w+)(?:->|<-)(\w+)", text)
                assert len(hops) == len(rule.get("requires") or []) + len(rule.get("forbids") or []), \
                    "rule %s: %d traversal(s) for %d clause(s)" % (rule["id"], len(hops),
                                                                   len(rule.get("requires") or []) + len(rule.get("forbids") or []))
                for arrow, edge, table in hops:
                    assert edge in edges, "rule %s traverses %r, not an edge of this instance" % (rule["id"], edge)
                    src, dst = edges[edge]
                    assert (arrow, table) == (("->", dst) if rule["table"] == src else ("<-", src)), \
                        "rule %s on %s: %s%s reaches %r, but %s joins %s to %s" % (
                            rule["id"], rule["table"], arrow, edge, table, edge, src, dst)

        props.assert_holds(self, props.check("gate_expression_names", prop, gen.gen_config, n=N, seed=SEED),
                           min_checked=N)

    def test_every_constrained_or_forbidding_clause_gets_a_probe_or_a_named_reason(self):
        def prop(case):
            inst = config.load(case)
            for rule, probe in zip(inst.gate_rules(), gates.probes(inst)):
                constrained = [c for c in rule.get("requires") or [] if c.get("where")]
                assert len(probe["wrong_plans"]) == len(constrained), \
                    "rule %s: %d constrained clause(s), %d wrong-evidence plan(s)" % (
                        rule["id"], len(constrained), len(probe["wrong_plans"]))
                assert len(probe["forbidden"]) == len(rule.get("forbids") or []), \
                    "rule %s: forbids clauses and forbidden plans differ in number" % rule["id"]
                for plan in list(probe["wrong_plans"]) + list(probe["forbidden"]):
                    assert plan.get("built") or plan.get("reason"), \
                        "rule %s: a plan was neither built nor given a reason: %s" % (rule["id"], plan.get("clause"))

        props.assert_holds(self, props.check("probe_plans_complete", prop, gen.gen_config, n=N, seed=SEED),
                           min_checked=N)


class PropertyRunnerTests(unittest.TestCase):
    """The runner proves it can fail (TESTING s.9): a planted bug is found and shrunk, and a property over
    nothing fails."""

    def test_a_planted_kappa_that_returns_one_on_a_single_value_is_found_and_shrunk(self):
        def buggy_kappa(pairs):
            if pairs and len({str(a) for a, _ in pairs} | {str(b) for _, b in pairs}) == 1:
                return 1.0                          # D11 as shipped before 277eded
            return agreement.kappa(pairs)

        def prop(case):
            pairs = [tuple(p) for p in case["pairs"]]
            if not pairs:
                raise props.Discard()
            assert (buggy_kappa(pairs) is None) == (KappaInvariantTests.chance(pairs) >= 1.0 - 1e-12), \
                "kappa defined over a single-valued sample"

        result = props.check("planted_kappa", prop, KappaInvariantTests.generate, n=400, seed=SEED)
        self.assertIsNotNone(result["failure"], props.report_line(result))
        shrunk = result["failure"]["shrunk"]["pairs"]
        self.assertLessEqual(len(shrunk), 1, "shrinking stopped at %s" % shrunk)
        self.assertEqual(len(shrunk), 1, "the minimal counterexample is one single-valued pair")

    def test_a_planted_reconcile_that_overwrites_held_values_is_found(self):
        real = loader.reconcile

        def overwriting(existing, unit, run, accept=frozenset()):
            plan = real(existing, unit, run, accept)
            plan["set"].update({f: unit[f] for f in ("status", "grade")})        # D16 as shipped before a330b34
            return plan

        loader.reconcile = overwriting
        try:
            test = ReconcileInvariantTests("test_a_held_field_is_never_overwritten_unless_named_and_every_disagreement_is_reported")
            outcome = unittest.TestResult()
            test.run(outcome)
        finally:
            loader.reconcile = real
        self.assertEqual(len(outcome.failures), 1, "the reconcile property did not catch values being overwritten")
        self.assertIn("overwritten", outcome.failures[0][1])

    def test_a_property_that_discards_every_case_fails(self):
        def prop(case):
            raise props.Discard()

        result = props.check("discards_everything", prop, lambda rng: {}, n=20, seed=1)
        self.assertEqual(result["checked"], 0)
        outcome = unittest.TestResult()

        class Probe(unittest.TestCase):
            def runTest(inner):
                props.assert_holds(inner, result)

        Probe().run(outcome)
        self.assertEqual(len(outcome.failures), 1, "a property that checked nothing was reported as holding")

    def test_a_saved_failure_is_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "kappa__example.json").write_text(json.dumps({"case": {"pairs": [["a", "a"]]}}),
                                                                   encoding="utf-8")

            def prop(case):
                assert agreement.kappa([tuple(p) for p in case["pairs"]]) is None, "defined"

            self.assertEqual(props.replay_saved("kappa", prop, tmp), [])

            def broken(case):
                assert False, "still failing"

            self.assertEqual(props.replay_saved("kappa", broken, tmp), [("kappa__example.json", "still failing")])


if __name__ == "__main__":
    unittest.main()
