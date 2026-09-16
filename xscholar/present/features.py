# -*- coding: utf-8 -*-
"""features.py -- the row contract and the named salience features of each row.

Row contract (fixed; exporters write exactly this shape):

  rows.json = {"title": str, "question": str|null, "groups": [str, ...] (display order),
               "rows": [row, ...], "seeded_test_copy": bool (optional; set only by plant.py)}
  row = {"id": str (unique), "group": str (one of groups),
         "kind": "claim"|"conclusion"|"critique"|"authority"|"citation"|"unit",
         "text": str, "status": str|null, "grade": str|null, "verdict": str|null, "answered": bool|null,
         "locators": [str], "lens": str|null, "reasoning_type": str|null, "direction": str|null,
         "links": [str] (ids of related rows), "meta": {} (free)}

id, group, kind and text are required; the other row keys default to null, [] or {}. Unknown keys
are rejected loudly (meta is the free place).

Features are computed at render time from one row, the policy and the question. They are a named
vector, never stored on the row and never summed into one number used for truth:

  needs_check, needs_check_reasons, eliminated, superseded, replaced_by, established, high_impact,
  unanswered_high_impact, has_numbers, has_negation, locator_count, relevance (0-1), lens, bucket,
  rule_check ("pass"|"fail"|"not_checked"), rule_fails (failing rule ids), rule_reason

rule_check re-runs the policy's own promotion rules (policy "rules") over the row's labels; see
check_rules(). Eliminated and superseded rows are exempt and always "not_checked".

classify() adds one cross-row feature, links_eliminated (linked ids that are eliminated), and the
partition every view uses. Buckets are exclusive, first match wins:
  eliminated (status in policy.eliminated) > superseded (status in policy.superseded)
  > check_first (any needs-check reason) > established (status, grade or verdict established).
A row that matches nothing gets the reason "unclassified" and lands in check_first: unknown standing
is something to check.

Standard library only.
"""
import copy
import re

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.present import policy as policy_mod

KINDS = ("claim", "conclusion", "critique", "authority", "citation", "unit")
DOC_KEYS = ("title", "question", "groups", "rows", "seeded_test_copy")
ROW_KEYS = ("id", "group", "kind", "text", "status", "grade", "verdict", "answered", "locators", "lens",
            "reasoning_type", "direction", "links", "meta")
OPTIONAL_STR = ("status", "grade", "verdict", "lens", "reasoning_type", "direction")
BUCKETS = ("check_first", "eliminated", "superseded", "established")

# numbers and numeric dates/times: 312, 50/50, 10:14:07, 2026-03-04, 1,207, 3.5
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:[.,:/-]\d+)*")
DATE_WORD_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{1,2}(?:, \d{4})?\b")
WORD_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset("the and for was were are with that this from did does has have had who what when where why "
                      "how which into onto its their there than then been being any all but not".split())


class RowsError(ValueError):
    """rows.json does not match the row contract. The message names the row and key."""


def validate_rows(doc):
    """Return a normalized deep copy of doc (defaults filled) or raise RowsError."""
    if not isinstance(doc, dict):
        raise RowsError("rows document must be an object (got %s)" % type(doc).__name__)
    unknown = sorted(set(doc) - set(DOC_KEYS))
    if unknown:
        raise RowsError("unknown top-level key(s): %s; allowed: %s" % (", ".join(unknown), ", ".join(DOC_KEYS)))
    for key in ("title", "groups", "rows"):
        if key not in doc:
            raise RowsError("missing top-level key %r" % key)
    if not isinstance(doc["title"], str):
        raise RowsError("title must be a string")
    question = doc.get("question")
    if question is not None and not isinstance(question, str):
        raise RowsError("question must be a string or null")
    seeded = doc.get("seeded_test_copy", False)
    if not isinstance(seeded, bool):
        raise RowsError("seeded_test_copy must be true or false")
    groups = doc["groups"]
    if not isinstance(groups, list) or not all(isinstance(g, str) and g for g in groups):
        raise RowsError("groups must be a list of non-empty strings")
    if len(set(groups)) != len(groups):
        raise RowsError("groups has duplicates")
    if not isinstance(doc["rows"], list):
        raise RowsError("rows must be a list")
    seen, rows = set(), []
    for i, row in enumerate(doc["rows"]):
        where = "rows[%d]" % i
        if not isinstance(row, dict):
            raise RowsError("%s must be an object" % where)
        bad = sorted(set(row) - set(ROW_KEYS))
        if bad:
            raise RowsError("%s has unknown key(s): %s (put free data under meta)" % (where, ", ".join(bad)))
        rid = row.get("id")
        if not isinstance(rid, str) or not rid.strip() or re.search(r"[\s`|\\]", rid):
            raise RowsError("%s id must be a non-empty string without whitespace, backticks, pipes or backslashes (got %r)"
                            % (where, rid))
        where = "row %r" % rid
        if rid in seen:
            raise RowsError("%s: duplicate id" % where)
        seen.add(rid)
        if row.get("group") not in groups:
            raise RowsError("%s: group %r is not in groups" % (where, row.get("group")))
        if row.get("kind") not in KINDS:
            raise RowsError("%s: kind %r is not one of %s" % (where, row.get("kind"), ", ".join(KINDS)))
        if not isinstance(row.get("text"), str):
            raise RowsError("%s: text must be a string" % where)
        out = {"id": rid, "group": row["group"], "kind": row["kind"], "text": row["text"]}
        for key in OPTIONAL_STR:
            value = row.get(key)
            if value is not None and not isinstance(value, str):
                raise RowsError("%s: %s must be a string or null" % (where, key))
            out[key] = value
        answered = row.get("answered")
        if answered is not None and not isinstance(answered, bool):
            raise RowsError("%s: answered must be true, false or null" % where)
        out["answered"] = answered
        for key in ("locators", "links"):
            value = row.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                raise RowsError("%s: %s must be a list of non-empty strings" % (where, key))
            out[key] = list(value)
        meta = row.get("meta", {})
        if not isinstance(meta, dict):
            raise RowsError("%s: meta must be an object" % where)
        out["meta"] = copy.deepcopy(meta)
        rows.append(out)
    return {"title": doc["title"], "question": question, "groups": list(groups), "rows": rows,
            "seeded_test_copy": seeded}


def tokens(text):
    return {w for w in WORD_RE.findall((text or "").lower()) if len(w) >= 3 and w not in STOPWORDS}


def relevance(text, question):
    """Share of the question's content words found in the text, 0-1 (0 when there is no question)."""
    q = tokens(question)
    if not q:
        return 0.0
    return round(len(q & tokens(text)) / len(q), 3)


WHEN_ORDER = ("status", "grade", "verdict", "answered", "kind")
REQUIRES_ORDER = ("status", "grade", "verdict", "locators_min", "links_min", "links_prefix")
RULE_CHECKS = ("pass", "fail", "not_checked")


def _when_matches(row, when):
    """True when every declared "when" field matches this row (case-insensitive)."""
    for field, want in when.items():
        if field == "answered":
            if row.get("answered") is not want:
                return False
            continue
        value = row.get(field)
        if not isinstance(value, str) or value.lower() not in want:
            return False
    return True


def _when_reads(row, when):
    """The matched side of the reason, in the row's own words: "status confirmed", "answered true"."""
    parts = []
    for field in WHEN_ORDER:
        if field not in when:
            continue
        if field == "answered":
            parts.append("answered %s" % ("true" if when[field] else "false"))
        else:
            parts.append("%s %s" % (field, row.get(field)))
    return ", ".join(parts)


def _requires_failures(row, requires):
    """Short reader-facing descriptions of the clauses this row does not satisfy (empty: it passes)."""
    out = []
    for field in REQUIRES_ORDER:
        if field not in requires:
            continue
        want = requires[field]
        if field in ("status", "grade", "verdict"):
            value = row.get(field)
            if not (isinstance(value, str) and value.lower() in want):
                out.append("%s %s" % (field, value if isinstance(value, str) and value.strip() else "none"))
        elif field in ("locators_min", "links_min"):
            key = "locators" if field == "locators_min" else "links"
            count = len(row.get(key) or [])
            if count < want:
                out.append("%d %s, needs %d" % (count, key if count != 1 else key[:-1], want))
        else:  # links_prefix
            links = [x.lower() for x in (row.get("links") or [])]
            if not any(link.startswith(prefix) for link in links for prefix in want):
                out.append("no link starting with %s" % " or ".join(sorted(want)))
    return out


def check_rules(row, policy, exempt=False):
    """Re-check one row's labels against the policy's promotion rules. Pure function of row + policy.

    Returns (rule_check, failing rule ids, one short reason). rule_check is "fail" when a rule's
    "when" matched this row and its "requires" did not hold, "pass" when at least one rule matched
    and every matched rule held, and "not_checked" when no rule matched -- or when the row is exempt,
    because a row in the eliminated or superseded class is never expected to satisfy a promotion rule.

    This checks a row's labels against the declared rules. It is not the datastore gate, and it says
    nothing about whether the underlying evidence is real.
    """
    pol = policy_mod.load(policy)
    if exempt:
        return "not_checked", [], ""
    fails, reasons, matched = [], [], False
    for rule in pol.rule_match:
        if not _when_matches(row, rule["when"]):
            continue
        matched = True
        missing = _requires_failures(row, rule["requires"])
        if missing:
            fails.append(rule["id"])
            reasons.append("%s but %s" % (_when_reads(row, rule["when"]), " and ".join(missing)))
    if fails:
        return "fail", fails, "; ".join(reasons)
    return ("pass" if matched else "not_checked"), [], ""


def row_features(row, policy, question=None):
    """Named features of one row. Pure function of row, policy and question."""
    pol = policy_mod.load(policy)
    reasons = []
    for field in policy_mod.REASON_FIELDS:
        if pol.has("needs_check", field, row.get(field)):
            reasons.append("%s:%s" % (field, row[field]))
    high = pol.has("high_impact", "verdict", row.get("verdict"))
    unanswered_high = high and row.get("answered") is not True
    if unanswered_high:
        reasons.insert(0, "unanswered_high_impact")
    eliminated = pol.has("eliminated", "status", row.get("status"))
    superseded = not eliminated and pol.has("superseded", "status", row.get("status"))
    marked = (pol.has("established", "status", row.get("status"))
              or pol.has("established", "grade", row.get("grade"))
              or pol.has("established", "verdict", row.get("verdict")))
    established = marked and not reasons and not eliminated and not superseded
    if not (eliminated or superseded or established or reasons):
        reasons.append("unclassified")
    if eliminated:
        bucket = "eliminated"
    elif superseded:
        bucket = "superseded"
    elif reasons:
        bucket = "check_first"
    else:
        bucket = "established"
    replaced_by = None
    if superseded:
        meta_rb = (row.get("meta") or {}).get("replaced_by")
        replaced_by = meta_rb if isinstance(meta_rb, str) and meta_rb else ((row.get("links") or [None])[0])
    rule_check, rule_fails, rule_reason = check_rules(row, pol, exempt=eliminated or superseded)
    text = row.get("text") or ""
    return {
        "needs_check": bool(reasons),
        "needs_check_reasons": reasons,
        "eliminated": eliminated,
        "superseded": superseded,
        "replaced_by": replaced_by,
        "established": established,
        "high_impact": high,
        "unanswered_high_impact": unanswered_high,
        "has_numbers": bool(NUMBER_RE.search(text)),
        "has_negation": bool(pol.negation_re.search(text)),
        "locator_count": len(row.get("locators") or []),
        "relevance": relevance(text, question),
        "lens": row.get("lens"),
        "bucket": bucket,
        "rule_check": rule_check,
        "rule_fails": rule_fails,
        "rule_reason": rule_reason,
    }


def check_first_key(feats, index):
    """Sort key for the check-first list: named features, never one summed score."""
    return (not feats["unanswered_high_impact"], not feats["links_eliminated"],
            -len(feats["needs_check_reasons"]), -feats["relevance"], index)


def classify(doc, policy):
    """Features for every row and the exclusive partition. doc must already be validated."""
    pol = policy_mod.load(policy)
    rows = {r["id"]: r for r in doc["rows"]}
    index = {r["id"]: i for i, r in enumerate(doc["rows"])}
    feats = {rid: row_features(r, pol, doc.get("question")) for rid, r in rows.items()}
    eliminated_ids = {rid for rid, f in feats.items() if f["eliminated"]}
    for rid, r in rows.items():
        feats[rid]["links_eliminated"] = [x for x in r["links"] if x in eliminated_ids and x != rid]
    plan = {b: [rid for rid in rows if feats[rid]["bucket"] == b] for b in BUCKETS}
    plan["check_first"].sort(key=lambda rid: check_first_key(feats[rid], index[rid]))
    # rows that fail their own rule, in input order; they stay in their bucket as well
    plan["rule_fail"] = [r["id"] for r in doc["rows"] if feats[r["id"]]["rule_fails"]]
    plan.update({"features": feats, "index": index, "rows": rows})
    return plan
