# -*- coding: utf-8 -*-
"""demo_synthetic.py -- full vs speedread, side by side, for two non-legal synthetic instances.

Renders the invented incident postmortem and the bigger design review (synthetic.py) in every mode,
plants seeded errors into a copy of the incident, and writes the files below into OUT (default: a
new temporary directory). Prints the paths and the scan-layer size next to the full rendering: the
scan layer is what a reader reads before the Details section, so it is the size that matters.

    python demo_synthetic.py [--out DIR]

  rows.json, policy.json                  the clean incident rows and the instance policy
  full.md, speedread.md, claims.md, uncertain_first.md
  side_by_side.html, side_by_side.txt     full (left) vs speedread (right), with metrics
  planted_rows.json, manifest.json        seeded test copy (seed 7) and its manifest
  planted_full.md, planted_speedread.md   what a reviewer would read in each arm
  metrics.json                            metrics.compare for the clean and the planted rows
  review_rows.json, review_policy.json    the design review: long texts, a wall of open critiques
  review_full.md, review_speedread.md     where collapse, the overflow, the spot check and a rule fail show
  review_metrics.json, scan_vs_full.txt   the scan layer measured against the full rendering

Standard library only.
"""
import argparse
import html
import itertools
import json
import pathlib
import sys
import tempfile
import textwrap

HERE = pathlib.Path(__file__).resolve().parent

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar.present import metrics  # noqa: E402
from xscholar.present import plant  # noqa: E402
from xscholar.present import render  # noqa: E402
from xscholar.present import synthetic  # noqa: E402

WIDTH = 78


def _metrics_rows(result):
    head = ("| mode | chars | scan chars | lines | scan lines | preserved | "
            "check-first rows in first 10 / 20 / 40 scan lines |")
    out = [head, "|---|---|---|---|---|---|---|"]
    for mode, m in result["modes"].items():
        vis = m["visible_within"]
        out.append("| %s | %d | %d | %d | %d | %s%% | %s |" % (
            mode, m["chars"], m["scan_chars"], m["lines"], m["scan_lines"], m["preservation"]["pct"],
            " / ".join("%d of %d" % (vis[k]["check_first"]["visible"], vis[k]["check_first"]["of"])
                       for k in ("10", "20", "40"))))
    return out


def _scan_vs_full(name, result):
    """The size a reader actually reads (the scan layer) next to the full rendering."""
    full = result["modes"]["full"]["chars"]
    spot = result["spot_check"]
    out = ["%s: %d rows" % (name, result["rows_total"]),
           "  full                          %6d chars  %4d lines" % (full, result["modes"]["full"]["lines"])]
    for mode in ("speedread", "uncertain_first"):
        m = result["modes"][mode]
        out.append("  %-15s scan layer %6d chars  %4d lines  (%d%% of full)  whole file %6d chars"
                   % (mode, m["scan_chars"], m["scan_lines"], round(100.0 * m["scan_chars"] / full), m["chars"]))
    out.append("  spot check: %d of %d established rows sampled" % (len(spot["sample"]), spot["of_established"]))
    rules = result.get("rule_check") or {}
    if rules.get("rules_declared"):
        out.append("  rule re-check: %d fail, %d pass, %d not checked (rules: %s)"
                   % (rules["fail"], rules["pass"], rules["not_checked"], ", ".join(rules["rules_declared"])))
        for rule, hit in sorted(rules["by_rule"].items()):
            out.append("    %-34s flags %d row(s): %s" % (rule, hit["flagged"], ", ".join(hit["ids"])))
    if result["planted_ids"]:
        out.append("  planted rows: %d, of which %d render as established and %d landed in the spot-check sample"
                   % (len(result["planted_ids"]), len(spot["planted_established"]), len(spot["planted_in_sample"])))
    return out


def _columns(left, right):
    def wrap(text):
        lines = []
        for line in text.split("\n"):
            lines += textwrap.wrap(line, WIDTH, break_long_words=True, replace_whitespace=False) or [""]
        return lines
    rows = itertools.zip_longest(wrap(left), wrap(right), fillvalue="")
    return "\n".join("%-*s | %s" % (WIDTH, a, b) for a, b in rows) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    out = pathlib.Path(args.out) if args.out else pathlib.Path(tempfile.mkdtemp(prefix="xscholar_present_demo_"))
    out.mkdir(parents=True, exist_ok=True)
    doc, pol = synthetic.incident()

    def write(name, text):
        path = out / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    write("rows.json", json.dumps(doc, indent=2, ensure_ascii=False))
    write("policy.json", json.dumps(pol, indent=2, ensure_ascii=False))
    texts = {mode: render.render(doc, mode, pol) for mode in render.MODES}
    for mode, text in texts.items():
        write("%s.md" % mode, text)
    planted, manifest = plant.plant(doc, pol, seed=7, per_type=1)
    write("planted_rows.json", json.dumps(planted, indent=2, ensure_ascii=False))
    write("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    write("planted_full.md", render.render(planted, "full", pol))
    write("planted_speedread.md", render.render(planted, "speedread", pol))
    clean_m, planted_m = metrics.compare(doc, pol), metrics.compare(planted, pol, manifest)
    write("metrics.json", json.dumps({"clean": clean_m, "planted": planted_m}, indent=2, ensure_ascii=False))

    review, review_pol = synthetic.review()
    write("review_rows.json", json.dumps(review, indent=2, ensure_ascii=False))
    write("review_policy.json", json.dumps(review_pol, indent=2, ensure_ascii=False))
    write("review_full.md", render.render(review, "full", review_pol))
    write("review_speedread.md", render.render(review, "speedread", review_pol))
    review_m = metrics.compare(review, review_pol)
    write("review_metrics.json", json.dumps(review_m, indent=2, ensure_ascii=False))

    table = "\n".join(_metrics_rows(clean_m))
    scan_report = "\n".join(_scan_vs_full("incident postmortem", clean_m) + [""]
                            + _scan_vs_full("design review", review_m))
    write("scan_vs_full.txt", scan_report + "\n")
    write("side_by_side.txt", table + "\n\n" + _columns("FULL (baseline)\n\n" + texts["full"],
                                                        "SPEEDREAD\n\n" + texts["speedread"]))
    write("side_by_side.html", (
        "<!doctype html><meta charset=\"utf-8\"><title>Full vs speedread</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:16px}td{vertical-align:top;width:50%%}"
        "pre{white-space:pre-wrap;font-size:13px}</style>"
        "<h1>Full vs speedread (synthetic incident postmortem)</h1><pre>%s</pre>"
        "<table><tr><th>full</th><th>speedread</th></tr><tr><td><pre>%s</pre></td><td><pre>%s</pre></td></tr></table>"
        % (html.escape(table), html.escape(texts["full"]), html.escape(texts["speedread"]))))

    print("demo written to %s" % out)
    for name in sorted(p.name for p in out.iterdir()):
        print("  %s" % (out / name))
    print()
    print(scan_report)
    ok = clean_m["all_preserved"] and planted_m["all_preserved"] and review_m["all_preserved"]
    print("preservation: %s" % ("100% in every mode (clean, planted and review)" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
