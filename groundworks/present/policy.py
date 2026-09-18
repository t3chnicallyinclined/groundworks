# -*- coding: utf-8 -*-
"""policy.py -- instance vocabularies and consumer policies for the reading layer.

A *policy JSON* is supplied by the instance. It says which values of status, grade and verdict
need checking, which mean eliminated, superseded or established, which verdicts are high impact,
how to find locators inside text, which extra words are negations, and the short reason shown to
a reader for each value. The engine owns no domain words; DEFAULT is generic.

    {"needs_check": {"status": [...], "grade": [...], "verdict": [...]},
     "eliminated":  {"status": [...]},
     "superseded":  {"status": [...]},
     "established": {"status": [...], "grade": [...], "verdict": [...]},
     "high_impact": {"verdict": [...]},
     "locator_patterns": [regex, ...], "negations": [word or phrase, ...],
     "check_first_max": int >= 1, "spot_check": {"count": int >= 0, "seed": int|str|null},
     "rules": [{"id": str, "when": {...}, "requires": {...}}, ...],
     "why_labels": {"<field>:<value>": "short reason shown to the reader"}}

rules declare the instance's own promotion rules, re-checked over every rendered row (features.
check_rules). Empty (the default) turns the feature off. A rule matches a row when every field of
"when" matches, and passes when every clause of "requires" holds:

    when      status | grade | verdict | kind (a non-empty list of values) or answered (true/false)
    requires  status | grade | verdict (one of a non-empty list), locators_min | links_min (int >= 0),
              links_prefix (a link starts with one of a non-empty list)

    {"id": "established_needs_source_grade", "when": {"status": ["confirmed", "verified"]},
     "requires": {"grade": ["page_image", "record_text"]}}

Rules never apply to rows in the eliminated or superseded classes: a ruled-out claim is not expected
to satisfy a promotion rule.

Validation is loud: an unknown key, a wrong type, a regex that does not compile or matches the
empty string, an empty negation or one longer than four words, or a value listed in two classes
that contradict each other raises PolicyError.
A missing key (or missing sub-key) takes DEFAULT's value; supply an empty list to mean "none".
Vocabulary matching is case-insensitive.

established.verdict marks a verdict that means "checked and good" (a VERIFIED citation).
high_impact.verdict is also the severity order of the check-first view: most severe first.
check_first_max caps the Check first table; the rest go to "More to check", still before
Established. spot_check draws a deterministic random sample of established rows (seed null: a hash
of the title), shown as a labelled sample and also left in Established.

Consumer policies decide order and cut for one consumer. They never change text and never decide
truth. Human views leave nothing out; budgeted consumers return the ids they excluded.

  human_speedread(doc)                      order for the speedread view; excluded is always []
  hook_budget(doc, max_lines, max_chars)    eliminated, check-first, superseded, then established;
                                            a prefix of that order, with an EXCLUDED footer line
  synthesis_context(doc, max_rows)          same priority, established by relevance; excluded ids

Standard library only.
"""
import copy
import importlib
import json
import pathlib
import re

FIELDS = {
    "needs_check": ("status", "grade", "verdict"),
    "eliminated": ("status",),
    "superseded": ("status",),
    "established": ("status", "grade", "verdict"),
    "high_impact": ("verdict",),
}
LIST_KEYS = ("locator_patterns", "negations")
SCALAR_KEYS = ("check_first_max", "spot_check")
TOP_KEYS = tuple(FIELDS) + LIST_KEYS + SCALAR_KEYS + ("rules", "why_labels")
RULE_KEYS = ("id", "when", "requires")
WHEN_LIST_FIELDS = ("status", "grade", "verdict", "kind")
WHEN_FIELDS = WHEN_LIST_FIELDS + ("answered",)
REQUIRES_LIST_FIELDS = ("status", "grade", "verdict", "links_prefix")
REQUIRES_INT_FIELDS = ("locators_min", "links_min")
REQUIRES_FIELDS = REQUIRES_LIST_FIELDS + REQUIRES_INT_FIELDS
SPOT_CHECK_KEYS = ("count", "seed")
MAX_NEGATION_WORDS = 4
REASON_FIELDS = ("status", "grade", "verdict")
SPECIAL_REASONS = ("unanswered_high_impact", "unclassified")
DEFAULT_WHY = {
    "unanswered_high_impact": "high-impact critique, unanswered",
    "unclassified": "standing unclassified by policy",
}
# (class a, class b, fields): a value may not sit in both
CONFLICTS = (
    ("needs_check", "established", ("status", "grade", "verdict")),
    ("high_impact", "established", ("verdict",)),
    ("eliminated", "established", ("status",)),
    ("eliminated", "needs_check", ("status",)),
    ("superseded", "established", ("status",)),
    ("superseded", "needs_check", ("status",)),
    ("superseded", "eliminated", ("status",)),
)

BASE_NEGATIONS = (
    "not", "no", "never", "none", "nor", "neither", "cannot", "without", "nothing", "nobody", "nowhere",
    "isn't", "aren't", "wasn't", "weren't", "don't", "doesn't", "didn't", "can't", "couldn't", "won't",
    "wouldn't", "shouldn't", "hasn't", "haven't", "hadn't", "mustn't",
)

DEFAULT = {
    "needs_check": {
        "status": ["unverified", "inferred", "open", "stale", "proposed", "disputed", "contradicted", "unknown",
                   "pending"],
        "grade": ["agent", "recollection", "secondary"],
        "verdict": ["mismatch", "not_found", "misattributed", "interpolated", "uncheckable", "outside_status",
                    "contradicted", "fatal", "refutes"],
    },
    "eliminated": {"status": ["eliminated", "ruled_out", "refuted"]},
    "superseded": {"status": ["superseded", "replaced"]},
    "established": {"status": ["confirmed", "verified", "established", "supported", "ready"],
                    "grade": ["primary", "official", "reproduced"],
                    "verdict": []},
    "high_impact": {"verdict": ["fatal", "refutes", "damaging"]},
    "locator_patterns": [r"\b[\w.-]+:\d+(?::\d+)?\b"],
    "negations": [],
    "check_first_max": 25,
    "spot_check": {"count": 5, "seed": None},
    "rules": [],
    "why_labels": {},
}


class PolicyError(ValueError):
    """The policy JSON is malformed. The message names the key."""


def _apos(word):
    return word.replace("’", "'")


def _phrase(word):
    """Normalized negation entry: lowercase, straight apostrophes, single spaces between words."""
    return " ".join(_apos(word).lower().split())


def negation_regex(words):
    """Whole-word (or whole-phrase), case-insensitive; straight and curly apostrophes both match.

    A multi-word phrase matches across any run of whitespace, so "no longer" also matches a line
    break inside the phrase. Longer entries are tried first, so "no longer" wins over "no".
    """
    alts = sorted({_phrase(w) for w in words if _phrase(w)}, key=lambda w: (-len(w), w))
    parts = []
    for phrase in alts:
        parts.append(r"\s+".join("".join("['’]" if ch == "'" else re.escape(ch) for ch in word)
                                 for word in phrase.split(" ")))
    return re.compile(r"(?<![\w'’])(?:%s)(?![\w'’])" % "|".join(parts), re.IGNORECASE)


def _str_list(value, where):
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise PolicyError("%s must be a list of non-empty strings (got %r)" % (where, value))
    return list(value)


def _rule_list(value, where):
    """A non-empty list of non-empty strings. An empty list would be a clause nothing can satisfy."""
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
        raise PolicyError("%s must be a non-empty list of non-empty strings (got %r)" % (where, value))
    return list(value)


def _validate_rules(value):
    """The promotion rules re-checked over every rendered row. Unknown fields and empty lists raise."""
    if not isinstance(value, list):
        raise PolicyError("rules must be a list of rule objects (got %s)" % type(value).__name__)
    out, seen = [], set()
    for i, rule in enumerate(value):
        where = "rules[%d]" % i
        if not isinstance(rule, dict):
            raise PolicyError("%s must be an object with keys %s" % (where, ", ".join(RULE_KEYS)))
        unknown = sorted(set(rule) - set(RULE_KEYS))
        if unknown:
            raise PolicyError("unknown key(s) under %s: %s; allowed: %s"
                              % (where, ", ".join(unknown), ", ".join(RULE_KEYS)))
        missing = [k for k in RULE_KEYS if k not in rule]
        if missing:
            raise PolicyError("%s is missing %s" % (where, ", ".join(missing)))
        rid = rule["id"]
        if not isinstance(rid, str) or not rid.strip() or re.search(r"[\s`|\\]", rid):
            raise PolicyError("%s id must be a non-empty string without whitespace, backticks, pipes or "
                              "backslashes (got %r)" % (where, rid))
        if rid in seen:
            raise PolicyError("%s: duplicate rule id %r" % (where, rid))
        seen.add(rid)
        where = "rule %r" % rid
        for name, part, allowed in (("when", rule["when"], WHEN_FIELDS),
                                    ("requires", rule["requires"], REQUIRES_FIELDS)):
            if not isinstance(part, dict) or not part:
                raise PolicyError("%s: %s must be a non-empty object; fields: %s"
                                  % (where, name, ", ".join(allowed)))
            bad = sorted(set(part) - set(allowed))
            if bad:
                raise PolicyError("%s: unknown %s field(s): %s; allowed: %s"
                                  % (where, name, ", ".join(bad), ", ".join(allowed)))
        when = {}
        for field, val in rule["when"].items():
            if field == "answered":
                if not isinstance(val, bool):
                    raise PolicyError("%s: when.answered must be true or false (got %r)" % (where, val))
                when[field] = val
            else:
                when[field] = _rule_list(val, "%s: when.%s" % (where, field))
        requires = {}
        for field, val in rule["requires"].items():
            if field in REQUIRES_INT_FIELDS:
                if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                    raise PolicyError("%s: requires.%s must be an integer >= 0 (got %r)" % (where, field, val))
                requires[field] = val
            else:
                requires[field] = _rule_list(val, "%s: requires.%s" % (where, field))
        out.append({"id": rid, "when": when, "requires": requires})
    return out


def validate(obj):
    """Return a complete policy dict (defaults filled) or raise PolicyError."""
    if not isinstance(obj, dict):
        raise PolicyError("policy must be a JSON object (got %s)" % type(obj).__name__)
    unknown = sorted(set(obj) - set(TOP_KEYS))
    if unknown:
        raise PolicyError("unknown policy key(s): %s; allowed: %s" % (", ".join(unknown), ", ".join(TOP_KEYS)))
    out = copy.deepcopy(DEFAULT)
    for section, fields in FIELDS.items():
        if section not in obj:
            continue
        val = obj[section]
        if not isinstance(val, dict):
            raise PolicyError("%s must be an object with keys %s" % (section, ", ".join(fields)))
        bad = sorted(set(val) - set(fields))
        if bad:
            raise PolicyError("unknown key(s) under %s: %s; allowed: %s" % (section, ", ".join(bad), ", ".join(fields)))
        for field in fields:
            if field in val:
                out[section][field] = _str_list(val[field], "%s.%s" % (section, field))
    if "locator_patterns" in obj:
        out["locator_patterns"] = _str_list(obj["locator_patterns"], "locator_patterns")
    for pattern in out["locator_patterns"]:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise PolicyError("locator_patterns entry %r does not compile: %s" % (pattern, exc)) from exc
        if rx.fullmatch("") is not None:
            raise PolicyError("locator_patterns entry %r matches the empty string" % pattern)
    if "negations" in obj:
        out["negations"] = _str_list(obj["negations"], "negations")
        for entry in out["negations"]:
            words = _phrase(entry).split(" ")
            if not entry.strip() or words == [""]:
                raise PolicyError("negations entry %r is empty" % entry)
            if len(words) > MAX_NEGATION_WORDS:
                raise PolicyError("negations entry %r has %d words; at most %d"
                                  % (entry, len(words), MAX_NEGATION_WORDS))
            for word in words:
                if not re.fullmatch(r"[\w'’-]+", word):
                    raise PolicyError("negations entry %r must be a word or a phrase of words (got %r)"
                                      % (entry, word))
    if "check_first_max" in obj:
        value = obj["check_first_max"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise PolicyError("check_first_max must be an integer >= 1 (got %r)" % (value,))
        out["check_first_max"] = value
    if "spot_check" in obj:
        spot = obj["spot_check"]
        if not isinstance(spot, dict):
            raise PolicyError("spot_check must be an object with keys %s" % ", ".join(SPOT_CHECK_KEYS))
        bad = sorted(set(spot) - set(SPOT_CHECK_KEYS))
        if bad:
            raise PolicyError("unknown key(s) under spot_check: %s; allowed: %s"
                              % (", ".join(bad), ", ".join(SPOT_CHECK_KEYS)))
        out["spot_check"] = dict(DEFAULT["spot_check"])
        if "count" in spot:
            count = spot["count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise PolicyError("spot_check.count must be an integer >= 0 (got %r)" % (count,))
            out["spot_check"]["count"] = count
        if "seed" in spot:
            seed = spot["seed"]
            if seed is not None and (isinstance(seed, bool) or not isinstance(seed, (int, str))
                                     or (isinstance(seed, str) and not seed.strip())):
                raise PolicyError("spot_check.seed must be an integer, a non-empty string or null (got %r)" % (seed,))
            out["spot_check"]["seed"] = seed
    if "rules" in obj:
        out["rules"] = _validate_rules(obj["rules"])
    if "why_labels" in obj:
        labels = obj["why_labels"]
        if not isinstance(labels, dict):
            raise PolicyError("why_labels must be an object of \"field:value\": \"reason\"")
        clean = {}
        for key, text in labels.items():
            if not isinstance(text, str) or not text.strip():
                raise PolicyError("why_labels[%r] must be a non-empty string" % key)
            field, sep, value = key.partition(":")
            if key not in SPECIAL_REASONS and not (sep and field in REASON_FIELDS and value.strip()):
                raise PolicyError("why_labels key %r must be one of %s or \"<status|grade|verdict>:<value>\""
                                  % (key, ", ".join(SPECIAL_REASONS)))
            clean[key] = text
        out["why_labels"] = clean
    for a, b, fields in CONFLICTS:
        for field in fields:
            both = {v.lower() for v in out[a][field]} & {v.lower() for v in out[b][field]}
            if both:
                raise PolicyError("value(s) %s listed under both %s.%s and %s.%s"
                                  % (", ".join(sorted(both)), a, field, b, field))
    return out


class Policy:
    """A validated policy. Build with load(); do not mutate."""

    def __init__(self, data):
        self.data = data
        self.sets = {section: {f: frozenset(v.lower() for v in data[section][f]) for f in fields}
                     for section, fields in FIELDS.items()}
        self.locator_res = tuple(re.compile(p) for p in data["locator_patterns"])
        self.negation_words = tuple(dict.fromkeys(list(BASE_NEGATIONS) + [_phrase(w) for w in data["negations"]]))
        self.negation_re = negation_regex(self.negation_words)
        self.why_labels = {k.lower(): v for k, v in data["why_labels"].items()}
        self.check_first_max = data["check_first_max"]
        self.spot_check = dict(data["spot_check"])
        self.rules = tuple(copy.deepcopy(r) for r in data["rules"])
        # the same rules with their vocabularies lowercased once, for case-insensitive matching
        self.rule_match = tuple(
            {"id": r["id"],
             "when": {f: (v if f == "answered" else frozenset(x.lower() for x in v)) for f, v in r["when"].items()},
             "requires": {f: (v if f in REQUIRES_INT_FIELDS else frozenset(x.lower() for x in v))
                          for f, v in r["requires"].items()}}
            for r in self.rules)

    def severity(self, verdict):
        """Position of a verdict in high_impact.verdict (most severe first); large when not listed."""
        values = [v.lower() for v in self.data["high_impact"]["verdict"]]
        key = verdict.lower() if isinstance(verdict, str) else None
        return values.index(key) if key in values else len(values)

    def has(self, section, field, value):
        return isinstance(value, str) and value.lower() in self.sets[section][field]

    def first(self, section, field):
        values = self.data[section][field]
        return values[0] if values else None

    def why(self, reason):
        key = reason.lower()
        if key in self.why_labels:
            return self.why_labels[key]
        if reason in DEFAULT_WHY:
            return DEFAULT_WHY[reason]
        field, _, value = reason.partition(":")
        return "%s %s" % (field, value)


def load(source=None):
    """Policy from None (DEFAULT), a dict, a path to a JSON file, or an existing Policy."""
    if isinstance(source, Policy):
        return source
    if source is None:
        return Policy(validate({}))
    if isinstance(source, (str, pathlib.Path)):
        path = pathlib.Path(source)
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PolicyError("cannot read policy %s: %s" % (path, exc)) from exc
        return Policy(validate(obj))
    if isinstance(source, dict):
        return Policy(validate(source))
    raise PolicyError("policy must be None, a dict, a path or a Policy (got %s)" % type(source).__name__)


# ---- consumer policies ---------------------------------------------------------------------------

def _sibling(name):
    if __package__:
        return importlib.import_module("." + name, __package__)
    return importlib.import_module(name)


def _plan(doc, policy):
    features = _sibling("features")
    pol = load(policy)
    doc = features.validate_rows(doc)
    return doc, pol, features.classify(doc, pol)


def human_speedread(doc, policy=None):
    """The speedread order. Human views never leave anything out, so excluded is always []."""
    doc, pol, plan = _plan(doc, policy)
    established = {}
    for rid in plan["established"]:
        established.setdefault(plan["rows"][rid]["group"], []).append(rid)
    return {"consumer": "human_speedread", "seeded_test_copy": doc["seeded_test_copy"],
            "check_first": list(plan["check_first"]), "eliminated": list(plan["eliminated"]),
            "superseded": list(plan["superseded"]),
            "established": {g: established[g] for g in doc["groups"] if g in established},
            "total": len(doc["rows"]), "excluded": []}


def _positive_int(name, value, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("%s must be an integer >= %d (got %r)" % (name, minimum, value))
    return value


def hook_budget(doc, policy=None, max_lines=40, max_chars=4000):
    """Lines for a context hook under a hard budget.

    Order: eliminated, check first, superseded, established. The body is a prefix of that order, so an
    eliminated or check-first row is never cut while an established row is kept. A row line is never
    truncated: a row that does not fit is excluded whole. When anything is excluded the last line is
    an EXCLUDED footer (inside the budget) naming how many and as many ids as fit; the full list is
    returned under "excluded". A seeded test copy starts with the banner line (inside the budget).
    Raises ValueError when the budget cannot hold even the banner and the footer.
    """
    _positive_int("max_lines", max_lines)
    _positive_int("max_chars", max_chars)
    render = _sibling("render")
    doc, pol, plan = _plan(doc, policy)
    order = plan["eliminated"] + plan["check_first"] + plan["superseded"] + plan["established"]
    total = len(order)
    lines = [render.hook_line(plan, pol, rid) for rid in order]
    head = [render.BANNER] if doc["seeded_test_copy"] else []

    def size(body):
        return sum(len(x) for x in body) + max(len(body) - 1, 0)

    n = 0
    while n < total:
        body = head + lines[:n + 1]
        if len(body) > max_lines or size(body) > max_chars:
            break
        n += 1
    footer = None
    while n < total:
        footer = "[EXCLUDED %d of %d rows over budget]" % (total - n, total)
        body = head + lines[:n] + [footer]
        if len(body) <= max_lines and size(body) <= max_chars:
            break
        if n == 0:
            raise ValueError("budget max_lines=%d max_chars=%d cannot hold the exclusion report" % (max_lines, max_chars))
        n -= 1
    excluded = order[n:]
    if excluded:
        ids, shown = [], 0
        for i, rid in enumerate(excluded):
            rest = len(excluded) - i - 1
            trial = "[EXCLUDED %d of %d rows over budget: %s%s]" % (
                len(excluded), total, ", ".join(ids + [rid]), " (+%d more)" % rest if rest else "")
            if size(head + lines[:n] + [trial]) > max_chars:
                break
            ids.append(rid)
            shown = i + 1
        if shown:
            rest = len(excluded) - shown
            footer = "[EXCLUDED %d of %d rows over budget: %s%s]" % (
                len(excluded), total, ", ".join(ids), " (+%d more)" % rest if rest else "")
    body = head + lines[:n] + ([footer] if excluded else [])
    text = "\n".join(body)
    return {"consumer": "hook_budget", "seeded_test_copy": doc["seeded_test_copy"], "text": text,
            "included": order[:n], "excluded": excluded, "total": total, "lines": len(body), "chars": len(text),
            "max_lines": max_lines, "max_chars": max_chars}


def synthesis_context(doc, policy=None, max_rows=50):
    """Rows for a writer's context, by priority, with the excluded ids returned.

    Priority: eliminated, check first (speedread order), superseded, then established by relevance to
    the question (ties in input order). Rows are returned unchanged.
    """
    _positive_int("max_rows", max_rows, minimum=0)
    doc, pol, plan = _plan(doc, policy)
    feats = plan["features"]
    established = sorted(plan["established"], key=lambda rid: (-feats[rid]["relevance"], plan["index"][rid]))
    order = plan["eliminated"] + plan["check_first"] + plan["superseded"] + established
    included, excluded = order[:max_rows], order[max_rows:]
    return {"consumer": "synthesis_context", "seeded_test_copy": doc["seeded_test_copy"],
            "rows": [copy.deepcopy(plan["rows"][rid]) for rid in included],
            "bucket": {rid: feats[rid]["bucket"] for rid in included},
            "check_reasons": {rid: [pol.why(r) for r in feats[rid]["needs_check_reasons"]] for rid in included
                              if feats[rid]["needs_check_reasons"]},
            "included": included, "excluded": excluded, "total": len(order)}
