# -*- coding: utf-8 -*-
"""render.py -- `ask render`: check first, then write answer.md with the counts in its header.

    text = render(out_dir, inst=None)     # None when check did not PASS (nothing is written then)

The header is the honest part: `corpus N | retrieved K | read R | not read N-R`. A green answer that
read 200 of 28,000 units says so on its first line. A packet that retrieved nothing renders a one-line
answer saying 0 hits. Standard library only.
"""
import json
import pathlib

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks.ask.check import check_packet  # noqa: E402
from groundworks.ask.packet import load_packet  # noqa: E402


def locator_text(unit):
    """A locator a reader can follow: page:line spans when the fields are there, else key=value pairs."""
    loc = unit.get("locator") or {}
    if "page_from" in loc and "line_from" in loc:
        span = "p.%s:%s" % (loc.get("page_from"), loc.get("line_from"))
        if loc.get("page_to") is not None:
            span += "-%s:%s" % (loc.get("page_to"), loc.get("line_to"))
        lead = [str(loc[k]) for k in ("hearing_date", "date", "document") if loc.get(k)]
        return " ".join(lead + [span])
    return " ".join("%s=%s" % (k, v) for k, v in loc.items())


def header(report):
    if report["verdict"] == "NOT_EXECUTED":
        return "check NOT EXECUTED: %s" % report.get("reason")
    return ("corpus %d | retrieved %d | read %d | not read %d | quotes %d (checked %d, found %d) | status axis %s | check %s"
            % (report["corpus_total"], report["retrieved"], report["read"], report["not_read"], report["quotes"],
               report["quotes_checked"], report["quotes_found"], report["status_axis"], report["verdict"]))


def render(out_dir, inst=None):
    out_dir = pathlib.Path(out_dir)
    report = check_packet(out_dir, inst)
    if report["verdict"] != "PASS":
        return None
    info, candidates = load_packet(out_dir)
    answer = json.loads((out_dir / "answer.json").read_text(encoding="utf-8"))
    lines = ["# Answer", "", "**Question:** %s" % info["question"], "", "`%s`" % header(report), ""]
    if info["retrieved"] == 0:
        lines += ["**0 hits.** No unit of the corpus shares a word with the question. Nothing was read; there is no answer to give.", ""]
    else:
        lines += [(answer.get("answer") or "").strip(), ""]
        if answer.get("not_answerable"):
            lines += ["**Not answerable from what was read.**", ""]
        lines += ["## Quotes (each found word for word in its unit)", ""]
        for q in answer.get("quotes") or []:
            unit = candidates[q["unit"]]
            tag = "" if (q.get("use") or "support") == "support" else " (note: not relied on)"
            lines.append("- `%s` %s [%s / %s]%s: \"%s\"" % (q["unit"], locator_text(unit), unit.get("status"), unit.get("grade"), tag, q["quote"]))
            if q.get("why"):
                lines.append("  %s" % q["why"])
        gaps = answer.get("gaps") or []
        if gaps:
            lines += ["", "## Gaps the reader named", ""] + ["- %s" % g for g in gaps]
    scope = report.get("scope") or {}
    lines += ["", "## Not read", "",
              "%d of %d units were never read for this question: retrieval was BM25 top %d, and this answer says nothing about the rest."
              % (report["not_read"], report["corpus_total"], info["k"])]
    if scope.get("types") or scope.get("where"):
        lines.append("Scope: types %s, where %s; %d units in scope, %d excluded by the scope before ranking."
                     % (scope.get("types") or "all", scope.get("where") or "none", scope.get("in_scope"), scope.get("out_of_scope")))
    lines.append("")
    text = "\n".join(lines)
    (out_dir / "answer.md").write_text(text, encoding="utf-8")
    return text
