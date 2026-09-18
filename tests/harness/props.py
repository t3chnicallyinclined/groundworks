# -*- coding: utf-8 -*-
"""props.py -- a standard-library property runner: generate, check, shrink, save, replay.

    result = check("kappa_symmetric", prop, generate, n=300, seed=17)
    # result: {"name", "generated", "checked", "discarded", "failure": None | {"case", "shrunk", "message", "saved"}}

  prop(case)      returns None on success; raises AssertionError (or returns a message string) on failure;
                  raises Discard when the case is not one the property is about.
  generate(rng)   returns a JSON-able case from a random.Random.

On failure the case is SHRUNK by greedy deletion -- one list element or one optional mapping key at a time,
kept only while the property still fails and the smaller case is not discarded -- and the minimal case is
written to a temporary file whose path is in the message. Copy it into tests/harness/property_failures/
(`<name>__<anything>.json`, {"case": ...}) to make it permanent: every saved failure there is replayed on every
run (replay_saved), which is the Lessons loop of METHOD s.15 for properties. Nothing is written into the
tree during a run: a test that writes into the tree it runs in leaks state between mutation runs.

A property that discarded every case, or checked fewer than `min_checked`, FAILS: a property over nothing is
the vacuous pass this repo has spent two days removing (D10, D13). Standard library only.
"""
import copy
import json
import pathlib
import random
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SAVED = HERE / "property_failures"


class Discard(Exception):
    """The generated case is outside what this property is about. Counted, never silently dropped."""


def _outcome(prop, case):
    """'pass' | 'discard' | ('fail', message, kind). kind is the exception type: a property that raises
    something other than AssertionError has failed too, but a shrink step may not trade one kind of failure
    for another (deleting a key the property reads raises KeyError, which is not the bug being shrunk)."""
    try:
        message = prop(copy.deepcopy(case))
    except Discard:
        return "discard"
    except AssertionError as exc:
        return ("fail", str(exc) or "AssertionError", "AssertionError")
    except Exception as exc:
        return ("fail", "%s: %s" % (type(exc).__name__, exc), type(exc).__name__)
    if isinstance(message, str) and message:
        return ("fail", message, "AssertionError")
    return "pass"


def _smaller(case):
    """Every case one deletion smaller, largest deletions first (whole lists before single elements)."""
    if isinstance(case, list):
        for i in range(len(case)):
            yield case[:i] + case[i + 1:]
        for i, item in enumerate(case):
            for smaller in _smaller(item):
                yield case[:i] + [smaller] + case[i + 1:]
    elif isinstance(case, dict):
        for key in list(case):
            reduced = dict(case)
            del reduced[key]
            yield reduced
        for key, value in case.items():
            for smaller in _smaller(value):
                reduced = dict(case)
                reduced[key] = smaller
                yield reduced


def size(case):
    if isinstance(case, list):
        return 1 + sum(size(c) for c in case)
    if isinstance(case, dict):
        return 1 + sum(size(v) for v in case.values())
    return 1


def shrink(prop, case, budget=2000):
    """The smallest case found by greedy deletion that still fails the same way. Bounded, so a slow
    property ends."""
    first = _outcome(prop, case)
    kind = first[2] if isinstance(first, tuple) else None
    current, tries = case, 0
    improved = True
    while improved and tries < budget:
        improved = False
        for candidate in _smaller(current):
            tries += 1
            if tries >= budget:
                break
            result = _outcome(prop, candidate)
            if isinstance(result, tuple) and result[2] == kind:
                current, improved = candidate, True
                break
    return current


def check(name, prop, generate, n=200, seed=0):
    rng_seed = random.Random(seed)
    # saved counterexamples first: a failure found once is checked on every run, whatever the seed
    result = {"name": name, "generated": 0, "checked": 0, "discarded": 0, "failure": None,
              "replayed_failures": replay_saved(name, prop)}
    for _ in range(n):
        case = generate(random.Random(rng_seed.getrandbits(64)))
        result["generated"] += 1
        outcome = _outcome(prop, case)
        if outcome == "discard":
            result["discarded"] += 1
            continue
        result["checked"] += 1
        if isinstance(outcome, tuple):
            shrunk = shrink(prop, case)
            final = _outcome(prop, shrunk)
            fd, path = tempfile.mkstemp(prefix="groundworks-property-%s-" % name, suffix=".json")
            with open(fd, "w", encoding="utf-8") as handle:
                json.dump({"property": name, "seed": seed, "case": shrunk, "original_size": size(case)}, handle,
                          indent=1, default=str)
            result["failure"] = {"case": case, "shrunk": shrunk, "saved": path,
                                 "message": final[1] if isinstance(final, tuple) else outcome[1]}
            break
    return result


def replay_saved(name, prop, directory=SAVED):
    """Every saved failure for `name`, replayed. Returns [(file, message)] for those that fail again."""
    failures = []
    for path in sorted(pathlib.Path(directory).glob("%s__*.json" % name)):
        case = json.loads(path.read_text(encoding="utf-8"))["case"]
        outcome = _outcome(prop, case)
        if isinstance(outcome, tuple):
            failures.append((path.name, outcome[1]))
    return failures


def report_line(result):
    return "PROPERTY %s: %d generated, %d checked, %d discarded%s" % (
        result["name"], result["generated"], result["checked"], result["discarded"],
        "" if not result["failure"] else " | FAILED, shrunk to size %d (from %d): %s; saved %s" % (
            size(result["failure"]["shrunk"]), size(result["failure"]["case"]), result["failure"]["message"][:300],
            result["failure"]["saved"]))


def assert_holds(testcase, result, min_checked=1):
    """unittest glue: fail on a counterexample, and fail on a property that checked (almost) nothing."""
    line = report_line(result)
    print(line)
    testcase.assertEqual(result.get("replayed_failures") or [], [],
                         "%s -- a saved counterexample fails again: %s" % (result["name"], result.get("replayed_failures")))
    testcase.assertIsNone(result["failure"], line)
    testcase.assertGreaterEqual(result["checked"], min_checked,
                                "%s -- a property that checked fewer than %d case(s) proved nothing" % (line, min_checked))
