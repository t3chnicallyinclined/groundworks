# -*- coding: utf-8 -*-
"""gen.py -- a seed-reproducible generator of varied, VALID instance configs, corpora and labels.

Standard library only: `random.Random(seed)` and nothing else. No Hypothesis (docs/TESTING.md s.6).

Why. Every test in this repo used a config a human wrote, almost always the demo, and two engine defects
(D9: a probe keyed on an edge literally named `cites`; D14: a gate whose evidence is itself gated) were
found only when someone wrote a DIFFERENT config. This turns "someone writes a different config" into
something every test run does, with names that are never the demo's.

INTERFACE (stable; the seeded-instance generator, TESTING s.3 / build item #6, is meant to reuse it):

    rng = random.Random(seed)
    cfg    = gen_config(rng, **knobs)          -> dict: a config config.load() accepts (never a file)
    units  = gen_units(rng, cfg, n)            -> [unit dict]: id, type, locator, text, status, grade, hash
    labels = gen_labels(rng, cfg, units)       -> [label dict]: oracle labels the validator accepts
    case   = gen_case(seed, **knobs)           -> {"seed", "config", "units", "labels"}  (all JSON-able)

  - Every function takes the rng (or a seed) and nothing global: the same seed gives byte-identical
    output (tests/test_invariants.py asserts it by hashing two generations).
  - Output is plain JSON-able data. Nothing here touches a database, a file or the engine's modules,
    so a generator can never be fitted to the engine it tests (TESTING s.3 rule 1: the engine never
    imports this).
  - Vocabulary is generated (`Names`): table, edge, field, grade, status, tier-label and target names are
    neutral syllables, so an engine assumption that relies on a built-in or demo name fails on most seeds.
  - Knobs (all optional): rules=(1, 5), declared_tables=(0, 2), chained=None (random), forbids=None,
    unique=None, statuses=(2, 4), grades=(3, 6), unit_types=(1, 3). A knob of None means "let the seed
    decide"; a bool forces it.
  - A generated config the loader refuses is a GENERATOR bug, never a silently dropped case:
    `acceptance(seeds)` reports how many were refused and why, and the invariant suite fails above 5%.

What is NOT here yet, on purpose (held with #6): per-category planted truth (truth.json), write scripts,
access roles and `independent:` rules, and the reference gate model. They extend this interface; they do
not change it.
"""
import hashlib
import json
import random

SYLLABLES = ["zor", "vak", "tel", "upri", "mon", "dra", "kel", "sif", "oru", "bex", "nal", "qui", "tam", "yor",
             "fen", "lus", "gri", "hab", "cor", "wem", "pli", "sato", "ven", "idu", "rok", "mira", "ost", "jal"]
BUILT_IN_WORDS = {"unit", "label", "fact", "conclusion", "authority", "critique", "answer", "cites", "supports",
                  "undercuts", "attacks", "answers", "bears_on", "relies_on", "agent", "document", "written_by",
                  "in", "out", "id", "and", "or", "not", "is", "none", "true", "false", "value", "status", "grade",
                  "row_writer", "link_creator", "promotion_log", "promotion_head", "select", "from", "where"}
FACT_STATUS_CONFIRMED = "confirmed"      # the engine's own fact status names are an engine constant


class Names:
    """Fresh neutral identifiers, never a built-in word, never repeated within one generation."""

    def __init__(self, rng):
        self.rng = rng
        self.used = set(BUILT_IN_WORDS)

    def __call__(self, parts=2, sep="_"):
        for _ in range(1000):
            name = sep.join(self.rng.choice(SYLLABLES) + self.rng.choice(SYLLABLES)[:2]
                            for _ in range(self.rng.randint(1, parts)))
            if name not in self.used:
                self.used.add(name)
                return name
        raise RuntimeError("Names ran out of fresh identifiers")

    def many(self, k, parts=2):
        return [self(parts) for _ in range(k)]


def _span(rng, knob, default):
    lo, hi = knob if knob is not None else default
    return rng.randint(lo, hi)


def gen_config(rng, rules=None, declared_tables=None, chained=None, forbids=None, unique=None,
               statuses=None, grades=None, unit_types=None):
    name = Names(rng)
    unit_names = name.many(_span(rng, unit_types, (1, 3)))
    units = []
    for unit_name in unit_names:
        unit = {"name": unit_name, "locator_fields": name.many(rng.randint(1, 3), parts=1),
                "chunk_size": rng.choice([1, 2, 3, 5, 8, 13, 50])}
        if rng.random() < 0.4:
            unit["chunk_by"] = rng.choice(unit["locator_fields"])
        units.append(unit)
    order = name.many(_span(rng, grades, (3, 6)))
    n_confirming = rng.randint(1, len(order) - 2)
    confirming = order[:n_confirming]
    agent_grades = [order[-1]]
    status_values = name.many(_span(rng, statuses, (2, 4)))
    usable = status_values[:rng.randint(1, len(status_values) - 1)]
    tiers = list(range(rng.randint(3, 5)))
    vocabulary = {"tiers": tiers, "directions": name.many(rng.randint(2, 4)),
                  "issues": name.many(rng.randint(1, 5)), "roles": name.many(rng.randint(1, 4)),
                  "targets": name.many(rng.randint(1, 4))}

    data = {"instance": {"id": name(), "goal": "establish %s from the record" % name(), "holder": "a team",
                         "off_limits": []},
            "units": units,
            "fidelity": {"order": order, "confirming": confirming, "agent_grades": agent_grades},
            "status": {"values": status_values, "usable": usable},
            "vocabulary": vocabulary,
            "gates": [],
            "evaluator": {"regression_file": "evals/known_errors.json"},
            "human_owned": ["gates", "fidelity", "status", "evaluator", "privacy"],
            "privacy": {"deny_list": "~/.xscholar/denylist.txt", "providers_allowed": ["approved"]}}

    # declared node tables, each with one enumerated field a rule can gate, and an edge from it
    tables, edges = {}, {}
    for _ in range(_span(rng, declared_tables, (0, 2))):
        table, field = name(), name(parts=1)
        values = name.many(3, parts=1)
        tables[table] = {"fields": {field: {"type": "string", "one_of": values}}}
        edges[name()] = {"from": table, "to": "unit"}
        tables[table]["_gate"] = (field, values[-1])          # removed below; the rule template reads it
    chain_edges = []
    if tables and (chained if chained is not None else rng.random() < 0.5):
        # a declared table whose promotion rests on a CONFIRMED fact: evidence that is itself gated (D14)
        table = rng.choice(sorted(tables))
        edge = name()
        edges[edge] = {"from": table, "to": "fact"}
        chain_edges.append((table, edge))

    templates = ["fact_confirmed", "conclusion_ready"] + ["declared_%s" % t for t in sorted(tables)]
    n_rules = _span(rng, rules, (1, 5))
    used = set()
    for _ in range(n_rules):
        choices = [t for t in templates if t not in used]
        if not choices:
            break
        template = rng.choice(choices)
        used.add(template)
        rid = name()
        if template == "fact_confirmed":
            rule = {"id": rid, "table": "fact", "field": "status", "value": FACT_STATUS_CONFIRMED,
                    "requires": [{"edge": "cites", "to": ["unit"], "min": rng.randint(1, 2),
                                  "where": rng.choice(["grade IN $confirming AND status IN $usable",
                                                       "grade IN $confirming", "status IN $usable"])}]}
        elif template == "conclusion_ready":
            rule = {"id": rid, "table": "conclusion", "field": "ready", "value": True,
                    "requires": [{"edge": "supports", "from": ["fact"], "min": 1}]}
            if rng.random() < 0.7:
                rule["requires"].append({"edge": "attacks", "min": 1})
            if forbids if forbids is not None else rng.random() < 0.6:
                rule["forbids"] = [{"edge": "attacks", "where": "verdict = 'fatal'"}]
                if rng.random() < 0.5:
                    rule["forbids"].append({"edge": "attacks", "where": "verdict = 'damaging' AND answered = false"})
        else:
            table = template[len("declared_"):]
            field, value = tables[table]["_gate"]
            edge = next(e for e, body in edges.items() if body["from"] == table and body["to"] == "unit")
            rule = {"id": rid, "table": table, "field": field, "value": value,
                    "requires": [{"edge": edge, "to": ["unit"], "min": 1,
                                  "where": rng.choice(["grade IN $confirming", "status IN $usable",
                                                       "grade IN $confirming AND status IN $usable"])}]}
            for chain_table, chain_edge in chain_edges:
                if chain_table == table:
                    rule["requires"].append({"edge": chain_edge, "to": ["fact"], "min": 1,
                                             "where": "status = '%s'" % FACT_STATUS_CONFIRMED})
        data["gates"].append(rule)
    for body in tables.values():
        body.pop("_gate", None)
    if tables:
        data["tables"] = tables
    if edges:
        data["edges"] = edges
    if unique if unique is not None else rng.random() < 0.3:
        data["unique"] = {"authority": ["citation"]}
        data["human_owned"].append("unique")
    return data


def gen_units(rng, cfg, n):
    """n units spread over the config's unit types; statuses and grades drawn from the config."""
    words = [rng.choice(SYLLABLES) + rng.choice(SYLLABLES) for _ in range(40)]
    units = []
    for i in range(n):
        utype = rng.choice(cfg["units"])
        text = " ".join(rng.choice(words) for _ in range(rng.randint(3, 12)))
        locator = {f: (i if rng.random() < 0.5 else "%s_%d" % (f, rng.randint(0, 3))) for f in utype["locator_fields"]}
        unit = {"id": "u%04d" % i, "type": utype["name"], "locator": locator, "text": text,
                "status": rng.choice(cfg["status"]["values"]), "grade": rng.choice(cfg["fidelity"]["order"])}
        unit["hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        units.append(unit)
    return units


def gen_labels(rng, cfg, units):
    """One oracle label per unit that the validator must accept: vocabulary from the config, and a quote
    that is a contiguous run of the unit's own words for tier 2 and above."""
    vocab = cfg["vocabulary"]
    labels = []
    for unit in units:
        tier = rng.choice(vocab["tiers"])
        label = {"id": unit["id"], "tier": tier, "dir": None, "issues": [], "targets": [],
                 "role": rng.choice(vocab["roles"])}
        if tier >= 1 and rng.random() < 0.7:
            label["targets"] = rng.sample(vocab["targets"], rng.randint(1, len(vocab["targets"])))
        if tier >= 2:
            words = unit["text"].split()
            start = rng.randint(0, len(words) - 1)
            label.update({"dir": rng.choice(vocab["directions"]),
                          "issues": rng.sample(vocab["issues"], rng.randint(1, len(vocab["issues"]))),
                          "reason": "because %s" % rng.choice(words),
                          "quote": " ".join(words[start:start + rng.randint(1, 4)])})
            if len(label["quote"].split()) < 3:   # validator: a quote under 3 words must be the whole unit
                label["quote"] = unit["text"]
        labels.append(label)
    return labels


def gen_case(seed, n_units=None, **knobs):
    rng = random.Random(seed)
    cfg = gen_config(rng, **knobs)
    units = gen_units(rng, cfg, n_units if n_units is not None else rng.randint(1, 40))
    return {"seed": seed, "config": cfg, "units": units, "labels": gen_labels(rng, cfg, units)}


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def acceptance(seeds, loader):
    """How many generated configs `loader` (config.load) accepts. A refusal is counted with its reason,
    never dropped: a generator that emits invalid configs would otherwise test the loader's refusals only."""
    refused = []
    for seed in seeds:
        cfg = gen_config(random.Random(seed))
        try:
            loader(cfg)
        except Exception as exc:
            refused.append((seed, str(exc).splitlines()[-1][:200]))
    return {"generated": len(seeds), "accepted": len(seeds) - len(refused), "refused": refused}
