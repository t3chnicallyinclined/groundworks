# -*- coding: utf-8 -*-
"""render.py -- deterministic Markdown renderings of rows for fast human verification.

Presentation only. A renderer never decides truth, never drops a row and never changes text:
emphasis and order change, content does not. metrics.preservation() checks that every id, number,
negation word, locator and text of the input appears in the output, for every mode.

Modes
  full             baseline: groups in given order, rows in input order, plain, one line per row
                   - `id` [status/verdict/grade; answered|unanswered; lens L] text (locators)
  speedread        two layers. A scan layer of one line per row (a short lead of the text, with the
                   status/verdict/grade and locators always shown), then a Details layer holding
                   every row's full text, keyed by id, in the same order. Sections, in order:
                   Check first (table, capped by policy.check_first_max, unanswered high-impact
                   critiques grouped into one line), Spot check (a labelled random sample of
                   established rows), Eliminated, Superseded, More to check (the check-first
                   overflow), Established (grouped by group), Details.
                   Bold: numbers and dates, locators (policy patterns), negation words, status tokens
  claims           the one-layer speedread of v1.0, restricted to claim and conclusion rows; the
                   header says how many rows of other kinds the view leaves out and names them
  uncertain_first  the same two layers, plain: Needs check, Spot check, More to check, then the
                   other rows in input order, then Details

Every mode except `full` re-checks each row's labels against the policy's own promotion rules
(policy "rules"): rows that fail are listed first, under "## Fails its own rule (N)", with the
failing rule id and a short reason, and every rendered line of a failing row carries the inline bold
marker **RULE FAIL: <rule id>**. Rows that pass carry no marker, so failures stand out. A failing row
is still counted once in its own bucket; the header reports "Rule fails K" separately, like the spot
check. `full` stays the plain baseline.

Every mode labels eliminated rows "ELIMINATED CLAIM (false)" and superseded rows
"SUPERSEDED CLAIM (replaced by X)". A seeded test copy (rows.json "seeded_test_copy": true) starts
with the line SEEDED TEST COPY - CONTAINS PLANTED ERRORS in every mode.

Newlines inside a value render as <br>; in table cells "|" and "\\" are backslash-escaped.

    python render.py ROWS.json [--mode speedread] [--policy POLICY.json]

Standard library only.
"""
import argparse
import hashlib
import json
import random
import re
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks.present import features, policy as policy_mod

MODES = ("full", "speedread", "claims", "uncertain_first")
TWO_LAYER = ("speedread", "uncertain_first")
BANNER = "SEEDED TEST COPY - CONTAINS PLANTED ERRORS"
ELIMINATED_LABEL = "ELIMINATED CLAIM (false)"
RULE_FAIL_LABEL = "RULE FAIL"
RULE_SECTION = "## Fails its own rule"
CLAIM_KINDS = ("claim", "conclusion")
DOT = " · "

# The scan layer shows a lead; the Details layer below holds every row's full text.
DETAILS_PREFIX = "## Details: full text of every row"
LEAD_MAX = 160
ELLIPSIS = " …"
SPOT_NOTE = ("A random sample of established rows, not a ranking: each one is also listed under "
             "Established. Check them against their sources.")

# A sentence ends at . ! ? followed by whitespace (closing quotes allowed in between).
_SENT_END = re.compile(r"[.!?][\"'’”)\]]*(?=\s)")
_WORD_BEFORE = re.compile(r"[^\s]+$")


def oneline(s):
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def cell(s):
    return re.sub(r"([\\|])", r"\\\1", s)


def superseded_label(feats):
    return "SUPERSEDED CLAIM (replaced by %s)" % (feats.get("replaced_by") or "unrecorded")


def _ends_sentence(text, dot):
    """True when the '.' at index dot ends a sentence rather than an abbreviation.

    '!' and '?' always end one. A '.' does not when the word before it is a short token (p., Tr.,
    Ex., Q.) or already contains a period (A.D.3d, e.g.), or when the next word starts lowercase.
    """
    if text[dot] in "!?":
        return True
    before = _WORD_BEFORE.search(text, 0, dot)
    word = (before.group() if before else "").strip("\"'‘’(["
                                                  )
    if "." in word or len(word) < 3:
        return False
    nxt = re.search(r"\S", text[dot + 1:])
    return not (nxt and nxt.group().islower())


def lead(text, limit=LEAD_MAX):
    """(lead, cut): a verbatim prefix of text -- its first sentence or line, at most `limit` chars.

    Longer text is cut at a word boundary. `cut` is True when anything was left out, and the caller
    marks it with an ellipsis. The lead is never rewritten: it is always a prefix of the text, so
    what the scan layer shows can be compared to the row itself character for character.
    """
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not t.strip():
        return t, False
    start = len(t) - len(t.lstrip())
    end = t.find("\n", start)
    end = len(t) if end == -1 else end
    for m in _SENT_END.finditer(t, start, end):
        if _ends_sentence(t, m.start()):
            end = m.end()
            break
    out = t[:end]
    if len(out) > limit:
        window = out[:limit + 1]
        space = window.rfind(" ")
        out = out[:space] if space > 0 else out[:limit]
    out = out.rstrip()
    return out, len(out) < len(t.rstrip())


def _spans(text, pol):
    spans = []
    for rx in (features.NUMBER_RE, features.DATE_WORD_RE) + pol.locator_res + (pol.negation_re,):
        for m in rx.finditer(text):
            s, e = m.start(), m.end()
            while s < e and text[s].isspace():
                s += 1
            while e > s and text[e - 1].isspace():
                e -= 1
            if e > s:
                spans.append((s, e))
    merged = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def emphasize(text, pol):
    """Bold numbers, dates, locator mentions and negation words. Characters are never changed."""
    out, pos = [], 0
    for s, e in _spans(text, pol):
        out += [text[pos:s], "**", text[s:e], "**"]
        pos = e
    out.append(text[pos:])
    return "".join(out)


def _b(value, bold):
    return "**%s**" % value if bold and value and value == value.strip() else value


def tag(row, bold=False):
    values = [_b(row[f], bold) for f in ("status", "verdict", "grade") if row.get(f) is not None]
    parts = ["/".join(values) if values else "-"]
    if row.get("answered") is True:
        parts.append("answered")
    elif row.get("answered") is False:
        parts.append(_b("unanswered", bold))
    if row.get("lens"):
        parts.append("lens %s" % row["lens"])
    return "[%s]" % "; ".join(parts)


def rule_mark(rule_ids):
    """The inline marker a row that fails its own rule carries wherever it is rendered. Always bold."""
    return "**%s: %s**" % (RULE_FAIL_LABEL, ", ".join(rule_ids))


def entry(plan, pol, rid, bold=False, with_locators=True, lead_only=False, mark=False):
    """One line for one row: the full text, or (lead_only) the scan layer's short lead.

    mark adds the RULE FAIL marker when the row fails its own rule; every mode except `full` marks.
    """
    row, feats = plan["rows"][rid], plan["features"][rid]
    prefix = ""
    if feats["bucket"] == "eliminated":
        prefix = ELIMINATED_LABEL
    elif feats["bucket"] == "superseded":
        prefix = superseded_label(feats)
    if prefix:
        prefix = _b(prefix, bold) + ": "
    if mark and feats["rule_fails"]:
        prefix += rule_mark(feats["rule_fails"]) + ": "
    text, cut = lead(row["text"]) if lead_only else (row["text"], False)
    if bold:
        text = emphasize(text, pol)
    if cut:
        text += ELLIPSIS
    s = "%s`%s` %s %s" % (prefix, rid, tag(row, bold), text)
    if with_locators and row["locators"]:
        s += " (%s)" % "; ".join(_b(loc, bold) for loc in row["locators"])
    return oneline(s)


def scan_entry(plan, pol, rid, bold=False, with_locators=True, mark=False):
    return entry(plan, pol, rid, bold, with_locators, lead_only=True, mark=mark)


def rule_fail_ids(doc, plan, scope=None):
    """Rows that fail their own rule, in input order. Each one is still counted in its own bucket."""
    return [r["id"] for r in doc["rows"]
            if plan["features"][r["id"]]["rule_fails"] and (scope is None or r["id"] in scope)]


def rule_fail_section(plan, ids, line_fn):
    """The section that comes first: one scan line per failing row, with its rule id and reason."""
    if not ids:
        return []
    out = ["", "%s (%d)" % (RULE_SECTION, len(ids))]
    for rid in ids:
        feats = plan["features"][rid]
        out.append("- %s - rule %s: %s" % (line_fn(rid), ", ".join(feats["rule_fails"]), feats["rule_reason"]))
    return out


def why(plan, pol, rid):
    feats = plan["features"][rid]
    parts = [pol.why(r) for r in feats["needs_check_reasons"]]
    if feats.get("links_eliminated"):
        parts.append("linked to eliminated: %s" % ", ".join(feats["links_eliminated"]))
    return oneline("; ".join(parts))


def hook_line(plan, pol, rid):
    """One plain line for a context hook (policy.hook_budget)."""
    if plan["features"][rid]["bucket"] == "check_first":
        return "- CHECK %s - check: %s" % (entry(plan, pol, rid), why(plan, pol, rid))
    return "- " + entry(plan, pol, rid)


# ---- the check-first scan layer: collapse, cap, spot check ----------------------------------------

def check_entries(plan, pol):
    """The check-first list as scan entries: {"ids", "collapsed", "verdict", "lens", "target"}.

    Unanswered high-impact critiques on the same (target link, verdict, lens) collapse into one
    entry, so a wall of open critiques cannot crowd out everything else. Entries are ordered by
    severity (the order of policy.high_impact.verdict, most severe first) and otherwise keep the
    check-first order. Every id stays in exactly one entry.
    """
    feats, rows = plan["features"], plan["rows"]
    pos = {rid: i for i, rid in enumerate(plan["check_first"])}
    entries, groups = [], {}
    for rid in plan["check_first"]:
        row, f = rows[rid], feats[rid]
        if row["kind"] == "critique" and f["unanswered_high_impact"]:
            key = ((row["links"] or [None])[0], (row["verdict"] or "").lower(), row["lens"])
            groups.setdefault(key, []).append(rid)
        else:
            entries.append({"ids": [rid], "collapsed": False, "verdict": row["verdict"], "lens": row["lens"],
                            "target": None,
                            "sort": (0, pol.severity(row["verdict"]), pos[rid]) if f["unanswered_high_impact"]
                            else (1, 0, pos[rid])})
    for (target, _verdict, lens), ids in groups.items():
        first = rows[ids[0]]
        entries.append({"ids": list(ids), "collapsed": len(ids) > 1, "verdict": first["verdict"], "lens": lens,
                        "target": target, "sort": (0, pol.severity(first["verdict"]), pos[ids[0]])})
    entries.sort(key=lambda e: e["sort"])
    return entries


def group_line(plan, pol, item, bold=False, mark=False):
    """The one scan line that stands for a collapsed group of critiques. Every id is named."""
    parts = ["%d unanswered %s critiques" % (len(item["ids"]), _b(item["verdict"] or "-", bold))]
    if item["lens"]:
        parts.append("lens %s" % item["lens"])
    if item["target"]:
        parts.append("target %s" % item["target"])
    parts.append("ids " + ", ".join("`%s`" % rid for rid in item["ids"]))
    fails = [rule for rid in item["ids"] for rule in plan["features"][rid]["rule_fails"]]
    if mark and fails:
        parts.append(rule_mark(list(dict.fromkeys(fails))))
    return oneline(DOT.join(parts))


def entry_line(plan, pol, item, bold=False, with_locators=True, mark=False):
    if item["collapsed"]:
        return group_line(plan, pol, item, bold, mark)
    return scan_entry(plan, pol, item["ids"][0], bold, with_locators, mark)


def entry_why(plan, pol, item):
    seen = []
    for rid in item["ids"]:
        text = why(plan, pol, rid)
        if text and text not in seen:
            seen.append(text)
    return "; ".join(seen)


def entry_locators(plan, item, bold=False):
    locs = []
    for rid in item["ids"]:
        for loc in plan["rows"][rid]["locators"]:
            if loc not in locs:
                locs.append(loc)
    return oneline("; ".join(_b(loc, bold) for loc in locs)) if locs else "-"


def spot_seed(doc, pol):
    seed = pol.spot_check["seed"]
    if seed is not None:
        return seed
    return int(hashlib.sha256(doc["title"].encode("utf-8")).hexdigest()[:16], 16)


def spot_sample(doc, pol, plan):
    """A deterministic random sample of established rows, in input order. Never any other bucket."""
    established = list(plan["established"])
    count = pol.spot_check["count"]
    if count <= 0 or not established:
        return []
    picked = set(random.Random(spot_seed(doc, pol)).sample(established, min(count, len(established))))
    return [rid for rid in established if rid in picked]


def layout(doc, pol, plan):
    """What the two-layer views show: the capped check-first table, the overflow, the spot sample.

    check/more are scan entries (a collapsed group counts as one line but keeps all its ids);
    check_rows/more_rows count rows, so check_rows + more_rows + eliminated + superseded +
    established == total, every row counted exactly once. The spot sample is counted separately.
    """
    entries = check_entries(plan, pol)
    check, more = entries[:pol.check_first_max], entries[pol.check_first_max:]
    check_ids = [rid for item in check for rid in item["ids"]]
    more_ids = [rid for item in more for rid in item["ids"]]
    established = [rid for group in doc["groups"] for rid in plan["established"]
                   if plan["rows"][rid]["group"] == group]
    return {"check": check, "more": more, "check_ids": check_ids, "more_ids": more_ids,
            "check_rows": len(check_ids), "more_rows": len(more_ids),
            "eliminated": list(plan["eliminated"]), "superseded": list(plan["superseded"]),
            "established": established, "spot": spot_sample(doc, pol, plan),
            "total": len(doc["rows"])}


def _details(doc, pol, plan, order, bold):
    out = ["", "%s (%d)" % (DETAILS_PREFIX, len(order))]
    out += ["- " + entry(plan, pol, rid, bold, mark=True) for rid in order]
    return out


def _head(doc):
    out = ["# " + oneline(doc["title"])]
    if doc["question"]:
        out.append("Question: " + oneline(doc["question"]))
    return out


def _full(doc, pol, plan):
    out = _head(doc)
    for group in doc["groups"]:
        out += ["", "## " + oneline(group)]
        out += ["- " + entry(plan, pol, r["id"]) for r in doc["rows"] if r["group"] == group]
    return out


def _spot_section(doc, pol, plan, lay, bold):
    if not lay["spot"]:
        return []
    out = ["", "## Spot check (random, %d of %d established)" % (len(lay["spot"]), len(lay["established"])),
           SPOT_NOTE]
    out += ["- " + scan_entry(plan, pol, rid, bold, mark=True) for rid in lay["spot"]]
    return out


def _speedread(doc, pol, plan):
    lay = layout(doc, pol, plan)
    fails = rule_fail_ids(doc, plan)
    out = _head(doc)
    out.append(DOT.join([
        "Check first %d" % lay["check_rows"], "More to check %d" % lay["more_rows"],
        "Eliminated %d" % len(lay["eliminated"]), "Superseded %d" % len(lay["superseded"]),
        "Established %d" % len(lay["established"]), "Total %d" % lay["total"],
        "Spot check %d of %d" % (len(lay["spot"]), len(lay["established"])),
        "Rule fails %d" % len(fails)]))
    out += rule_fail_section(plan, fails, lambda rid: scan_entry(plan, pol, rid, True, mark=True))
    out += ["", "## Check first (%d)" % lay["check_rows"]]
    if lay["check"]:
        out += ["| # | What | Why check | Where |", "|---|---|---|---|"]
        for n, item in enumerate(lay["check"], 1):
            out.append("| %d | %s | %s | %s |" % (n, cell(entry_line(plan, pol, item, True, with_locators=False,
                                                                     mark=True)),
                                                  cell(entry_why(plan, pol, item)),
                                                  cell(entry_locators(plan, item, True))))
    out += _spot_section(doc, pol, plan, lay, True)
    out += ["", "## Eliminated (%d)" % len(lay["eliminated"])]
    out += ["- " + scan_entry(plan, pol, rid, True, mark=True) for rid in lay["eliminated"]]
    out += ["", "## Superseded (%d)" % len(lay["superseded"])]
    out += ["- " + scan_entry(plan, pol, rid, True, mark=True) for rid in lay["superseded"]]
    out += ["", "## More to check (%d)" % lay["more_rows"]]
    out += ["- %s - check: %s" % (entry_line(plan, pol, item, True, mark=True), entry_why(plan, pol, item))
            for item in lay["more"]]
    out += ["", "## Established (%d)" % len(lay["established"])]
    for group in doc["groups"]:
        ids = [rid for rid in lay["established"] if plan["rows"][rid]["group"] == group]
        if ids:
            out += ["### " + oneline(group)] + ["- " + scan_entry(plan, pol, rid, True, mark=True) for rid in ids]
    order = lay["check_ids"] + lay["eliminated"] + lay["superseded"] + lay["more_ids"] + lay["established"]
    return out + _details(doc, pol, plan, order, True)


def _speedread_flat(doc, pol, plan, scope=None, notes=()):
    """The v1.0 one-layer speedread. The claims view keeps it: it is already a partial view."""
    keep = (lambda rid: True) if scope is None else (lambda rid: rid in scope)
    check = [rid for rid in plan["check_first"] if keep(rid)]
    elim = [rid for rid in plan["eliminated"] if keep(rid)]
    sup = [rid for rid in plan["superseded"] if keep(rid)]
    est = [rid for rid in plan["established"] if keep(rid)]
    total = len(check) + len(elim) + len(sup) + len(est)
    fails = rule_fail_ids(doc, plan, scope)
    out = _head(doc)
    out.append(DOT.join(["Check first %d" % len(check), "Eliminated %d" % len(elim), "Superseded %d" % len(sup),
                         "Established %d" % len(est), "Total %d" % total, "Rule fails %d" % len(fails)]))
    out += list(notes)
    out += rule_fail_section(plan, fails, lambda rid: entry(plan, pol, rid, True, mark=True))
    out += ["", "## Check first (%d)" % len(check)]
    if check:
        out += ["| # | What | Why check | Where |", "|---|---|---|---|"]
        for n, rid in enumerate(check, 1):
            locs = plan["rows"][rid]["locators"]
            where = oneline("; ".join(_b(loc, True) for loc in locs)) if locs else "-"
            out.append("| %d | %s | %s | %s |" % (n, cell(entry(plan, pol, rid, True, with_locators=False, mark=True)),
                                                 cell(why(plan, pol, rid)), cell(where)))
    out += ["", "## Eliminated (%d)" % len(elim)] + ["- " + entry(plan, pol, rid, True, mark=True) for rid in elim]
    out += ["", "## Superseded (%d)" % len(sup)] + ["- " + entry(plan, pol, rid, True, mark=True) for rid in sup]
    out += ["", "## Established (%d)" % len(est)]
    for group in doc["groups"]:
        ids = [rid for rid in est if plan["rows"][rid]["group"] == group]
        if ids:
            out += ["### " + oneline(group)] + ["- " + entry(plan, pol, rid, True, mark=True) for rid in ids]
    return out


def _claims(doc, pol, plan):
    scope = {r["id"] for r in doc["rows"] if r["kind"] in CLAIM_KINDS}
    others = [r for r in doc["rows"] if r["id"] not in scope]
    note = "Claims view: %d of %d rows (kinds claim, conclusion)" % (len(scope), len(doc["rows"]))
    if others:
        by_kind = {}
        for r in others:
            by_kind.setdefault(r["kind"], []).append(r["id"])
        note += "; other kinds left out of this view: " + "; ".join(
            "%s %d (%s)" % (kind, len(ids), ", ".join(ids)) for kind, ids in by_kind.items())
    return _speedread_flat(doc, pol, plan, scope=scope, notes=[note])


def _uncertain_first(doc, pol, plan):
    lay = layout(doc, pol, plan)
    check = set(lay["check_ids"] + lay["more_ids"])
    rest = [r["id"] for r in doc["rows"] if r["id"] not in check]
    fails = rule_fail_ids(doc, plan)
    out = _head(doc)
    out.append(DOT.join(["Needs check %d" % lay["check_rows"], "More to check %d" % lay["more_rows"],
                         "Other %d" % len(rest), "Total %d" % lay["total"],
                         "Spot check %d of %d" % (len(lay["spot"]), len(lay["established"])),
                         "Rule fails %d" % len(fails)]))
    out += rule_fail_section(plan, fails, lambda rid: scan_entry(plan, pol, rid, mark=True))
    out += ["", "## Needs check (%d)" % lay["check_rows"]]
    out += ["- %s - check: %s" % (entry_line(plan, pol, item, mark=True), entry_why(plan, pol, item))
            for item in lay["check"]]
    out += _spot_section(doc, pol, plan, lay, False)
    out += ["", "## More to check (%d)" % lay["more_rows"]]
    out += ["- %s - check: %s" % (entry_line(plan, pol, item, mark=True), entry_why(plan, pol, item))
            for item in lay["more"]]
    out += ["", "## Other rows, input order (%d)" % len(rest)]
    out += ["- " + scan_entry(plan, pol, rid, mark=True) for rid in rest]
    return out + _details(doc, pol, plan, lay["check_ids"] + lay["more_ids"] + rest, False)


_RENDERERS = {"full": _full, "speedread": _speedread, "claims": _claims, "uncertain_first": _uncertain_first}


def render(doc, mode="speedread", policy=None):
    """Markdown for one mode. Raises RowsError, PolicyError or ValueError (unknown mode); never partial."""
    if mode not in MODES:
        raise ValueError("unknown mode %r; modes: %s" % (mode, ", ".join(MODES)))
    pol = policy_mod.load(policy)
    doc = features.validate_rows(doc)
    plan = features.classify(doc, pol)
    lines = _RENDERERS[mode](doc, pol, plan)
    if doc["seeded_test_copy"]:
        lines.insert(0, BANNER)
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render rows.json as Markdown.")
    ap.add_argument("rows")
    ap.add_argument("--mode", default="speedread", choices=MODES)
    ap.add_argument("--policy")
    args = ap.parse_args(argv)
    getattr(sys.stdout, "reconfigure", lambda **_: None)(encoding="utf-8")
    try:
        with open(args.rows, encoding="utf-8") as fh:
            doc = json.load(fh)
        sys.stdout.write(render(doc, args.mode, args.policy))
    except (OSError, ValueError) as exc:
        print("render NOT EXECUTED: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
