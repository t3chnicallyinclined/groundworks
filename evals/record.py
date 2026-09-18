# -*- coding: utf-8 -*-
"""record.py -- turn one run's counts into one history record, judged against its pre-registration.

    python -m evals.record add    --prereg evals/prereg/ID.json --result evals/results/ID.json [--retrofit]
    python -m evals.record show   ID            every stored run of one eval, oldest first
    python -m evals.record verify ID            the history's hash chain is intact

The rules are the harness's, not the author's (docs/TESTING.md s.7):

  1. No record without a prereg, and the prereg must predate the run (`written_at` < `run.started_at`).
     A measurement made before its hypothesis was written can only be recorded with --retrofit, which is
     stamped on the record and forbids the decision `adopted`: an effect seen after the fact is a
     hypothesis for the next prereg, never a result.
  2. Every arm carries its 95% Wilson interval; clustered arms carry a cluster bootstrap too, and the unit of
     analysis is named.
  3. Minimum n. The prereg states the smallest effect worth acting on; the n that detects it is computed
     here (two proportions, alpha 0.05, power 0.80) and compared with the prereg's own minimum. A compared
     arm below it is UNDERPOWERED: no verdict is reported, and the decision is forced to `no evidence`.
  4. Overlapping intervals are NO EVIDENCE OF CHANGE, never a pass.
  5. The evaluator's own inputs are checked: a run whose materials failed the UTF-8 round trip (D21), or that
     states no materials hash, is stored with `counts: false` and cannot decide anything.
  6. Anchors, when the result carries them: the oracle arm must score n of n and the null arm's interval must
     contain chance. If either fails, the EVAL is broken, not the system, and the decision is `no evidence`.
  7. History is append-only: each record carries the SHA-256 of the history file before it was appended
     (`prev_sha256`), so an edited or deleted earlier line breaks `verify`.

Exit: 0 recorded (whatever the decision); 1 refused, or `verify` found a broken chain. Standard library only.
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):
    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))
from evals import stats  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
HISTORY = HERE / "history"
RECORD_FORMAT = "groundworks.eval_record/1"
PREREG_KEYS = ("eval_id", "written_at", "hypothesis", "arms", "baseline_arm", "primary_metric", "unit_of_analysis",
               "smallest_effect", "min_n_per_arm", "direction", "decision_rule")
DECISIONS = ("adopted", "not adopted", "no evidence")


class RecordError(Exception):
    pass


def _time(text, where):
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecordError("%s is not an ISO time: %r" % (where, text)) from exc


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load_prereg(path):
    raw = pathlib.Path(path).read_bytes()
    data = json.loads(raw.decode("utf-8"))
    missing = [k for k in PREREG_KEYS if k not in data]
    if missing:
        raise RecordError("prereg %s lacks %s" % (path, ", ".join(missing)))
    if data["direction"] not in ("higher", "lower", "no worse"):
        raise RecordError("prereg direction must be higher, lower or no worse")
    effect = data["smallest_effect"]
    if not (isinstance(effect, dict) and "p_baseline" in effect and "p_arm" in effect):
        raise RecordError("prereg smallest_effect needs p_baseline and p_arm (the difference worth acting on)")
    return data, sha256_bytes(raw)


def build(prereg, prereg_sha, prereg_path, result, retrofit=False, scorer_sha=None):
    """The record for one run. Pure: no file is written here."""
    if result.get("eval_id") != prereg["eval_id"]:
        raise RecordError("result eval_id %r does not match prereg %r" % (result.get("eval_id"), prereg["eval_id"]))
    run = result.get("run") or {}
    started = _time(run.get("started_at"), "run.started_at")
    written = _time(prereg["written_at"], "prereg.written_at")
    before = written < started
    if not before and not retrofit:
        raise RecordError("the prereg was written at %s, after the run started at %s: a hypothesis written after "
                          "the measurement is not a pre-registration. Record it with --retrofit, which forbids "
                          "`adopted`." % (prereg["written_at"], run.get("started_at")))
    arms_in = result.get("arms") or {}
    wanted = list(prereg["arms"])
    if sorted(arms_in) != sorted(wanted):
        raise RecordError("result arms %s do not match the prereg's %s" % (sorted(arms_in), sorted(wanted)))
    arms = {}
    for name in wanted:
        arm = arms_in[name]
        if "clusters" in arm:
            summary = stats.cluster_proportion([tuple(c) for c in arm["clusters"]], seed=0)
            k, n = summary["k"], summary["n"]
            if ("k" in arm and arm["k"] != k) or ("n" in arm and arm["n"] != n):
                raise RecordError("arm %s: k/n %s/%s disagree with its clusters (%s/%s)"
                                  % (name, arm.get("k"), arm.get("n"), k, n))
        else:
            k, n, summary = arm.get("k"), arm.get("n"), None
        if not isinstance(k, int) or not isinstance(n, int) or n <= 0:
            raise RecordError("arm %s needs integer k and n > 0: a quality record over nothing is refused (a "
                              "deterministic pass/fail belongs in the test suite, not here)" % name)
        entry = {"k": k, "n": n, "p": round(k / n, 4), "wilson": [round(x, 4) for x in stats.wilson(k, n)]}
        if summary:
            entry["clusters"] = arm["clusters"]
            entry["cluster_bootstrap"] = [round(x, 4) for x in summary["cluster_interval"]]
        arms[name] = entry

    effect = prereg["smallest_effect"]
    needed = stats.power_two_proportions(effect["p_baseline"], effect["p_arm"])
    minimum = max(int(prereg["min_n_per_arm"]), needed)
    base = arms[prereg["baseline_arm"]]
    comparisons, problems = [], []
    materials = run.get("materials") or {}
    counts = True
    if not materials.get("sha256") or materials.get("utf8_roundtrip") is not True:
        counts = False
        problems.append("materials: %s" % (materials.get("note") or "no materials hash or a failed UTF-8 round trip"))
    anchors = result.get("anchors")
    eval_broken = []
    if anchors:
        oracle, null = anchors.get("oracle"), anchors.get("null")
        if oracle and oracle["k"] != oracle["n"]:
            eval_broken.append("the oracle arm scored %d of %d, not all" % (oracle["k"], oracle["n"]))
        if null:
            lo, hi = stats.wilson(null["k"], null["n"])
            if not lo <= null["chance"] <= hi:
                eval_broken.append("the null arm's interval [%.2f, %.2f] does not contain chance %.2f"
                                   % (lo, hi, null["chance"]))
    else:
        problems.append("no oracle or null arm was run, so the eval set itself is unverified")
    for name in wanted:
        if name == prereg["baseline_arm"]:
            continue
        arm = arms[name]
        underpowered = min(arm["n"], base["n"]) < minimum
        overlapping = stats.overlap(arm["wilson"], base["wilson"])
        higher = arm["wilson"][0] > base["wilson"][1]
        lower = arm["wilson"][1] < base["wilson"][0]
        if underpowered or eval_broken or not counts:
            decision = "no evidence"
        elif prereg["direction"] == "higher":
            decision = "adopted" if higher else ("not adopted" if lower else "no evidence")
        elif prereg["direction"] == "lower":
            decision = "adopted" if lower else ("not adopted" if higher else "no evidence")
        else:                                         # no worse: the new arm may not fall below the margin
            margin = float(prereg.get("margin", 0.0))
            decision = "adopted" if arm["wilson"][0] >= base["p"] - margin else "no evidence"
        if retrofit and decision == "adopted":
            decision = "no evidence"
            problems.append("retrofit: %s would have been `adopted`; a retrofit may not adopt anything" % name)
        detail = "%d/%d [%.2f, %.2f] vs %d/%d [%.2f, %.2f]" % (
            base["k"], base["n"], base["wilson"][0], base["wilson"][1],
            arm["k"], arm["n"], arm["wilson"][0], arm["wilson"][1])
        delta = abs(effect["p_arm"] - effect["p_baseline"])
        blocked = []                                  # every reason there is no verdict, not only the first
        if eval_broken:
            blocked.append("eval broken: %s" % "; ".join(eval_broken))
        if not counts:
            blocked.append("does not count: the materials failed the UTF-8 round trip or carry no hash")
        if underpowered:
            blocked.append("underpowered: n=%d per arm < %d needed for delta=%.2f"
                           % (min(arm["n"], base["n"]), minimum, delta))
        if blocked:
            line = "NO VERDICT (%s): %s" % ("; ".join(blocked), detail)
        elif decision == "no evidence":
            line = "NO EVIDENCE OF CHANGE (n=%d, %s; prereg n for delta=%.2f is %d)" % (
                min(arm["n"], base["n"]), detail, delta, minimum)
        else:
            line = "%s (n=%d, %s)" % (decision.upper(), min(arm["n"], base["n"]), detail)
        comparisons.append({"arm": name, "baseline": prereg["baseline_arm"], "decision": decision,
                            "underpowered": underpowered, "intervals_overlap": overlapping, "verdict_line": line})
    decision = ("adopted" if comparisons and all(c["decision"] == "adopted" for c in comparisons)
                else "not adopted" if comparisons and all(c["decision"] == "not adopted" for c in comparisons)
                else "no evidence")
    return {"format": RECORD_FORMAT, "record_type": "estimate", "eval_id": prereg["eval_id"],
            "prereg": {"path": str(prereg_path), "sha256": prereg_sha, "written_at": prereg["written_at"],
                       "written_before_run": before},
            "retrofit": bool(retrofit),
            "run": run, "metric": prereg["primary_metric"], "unit_of_analysis": prereg["unit_of_analysis"],
            "min_n_per_arm": minimum, "n_for_smallest_effect": needed, "arms": arms,
            "unflattering": result.get("unflattering") or {}, "anchors": anchors, "counts": counts,
            "eval_broken": eval_broken, "problems": problems, "comparisons": comparisons, "decision": decision,
            "scorer_sha256": scorer_sha, "source": result.get("source")}


def history_path(eval_id, directory=HISTORY):
    return pathlib.Path(directory) / ("%s.jsonl" % eval_id)


def append(record, directory=HISTORY, recorded_at=None):
    path = history_path(record["eval_id"], directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    problems = verify(record["eval_id"], directory) if path.exists() else []
    if problems:
        raise RecordError("history %s is already broken, refusing to append: %s" % (path, problems[0]))
    before = path.read_bytes() if path.exists() else b""
    record = dict(record, prev_sha256=sha256_bytes(before),
                  recorded_at=recorded_at or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
    with open(path, "ab") as handle:            # append only: an earlier line is never rewritten
        handle.write((json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    return record


def verify(eval_id, directory=HISTORY):
    """Problems with the chain, [] when intact. A history that does not exist is a problem, not a pass."""
    path = history_path(eval_id, directory)
    if not path.exists():
        return ["no history for %s at %s" % (eval_id, path)]
    raw = path.read_bytes()
    problems, offset = [], 0
    lines = raw.split(b"\n")
    for number, line in enumerate(lines[:-1], 1):
        try:
            record = json.loads(line.decode("utf-8"))
        except ValueError:
            problems.append("line %d is not JSON" % number)
            break
        if record.get("prev_sha256") != sha256_bytes(raw[:offset]):
            problems.append("line %d: prev_sha256 does not match the file before it; an earlier line was edited, "
                            "reordered or removed" % number)
        offset += len(line) + 1
    if lines[-1] != b"":
        problems.append("the history does not end with a newline: a partial append")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m evals.record")
    sub = parser.add_subparsers(dest="command", required=True)
    p_add = sub.add_parser("add")
    p_add.add_argument("--prereg", required=True)
    p_add.add_argument("--result", required=True)
    p_add.add_argument("--retrofit", action="store_true")
    p_add.add_argument("--history", default=str(HISTORY))
    p_show = sub.add_parser("show")
    p_show.add_argument("eval_id")
    p_show.add_argument("--history", default=str(HISTORY))
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("eval_id")
    p_verify.add_argument("--history", default=str(HISTORY))
    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            prereg, prereg_sha = load_prereg(args.prereg)
            result = json.loads(pathlib.Path(args.result).read_text(encoding="utf-8"))
            record = build(prereg, prereg_sha, args.prereg, result, retrofit=args.retrofit,
                           scorer_sha=sha256_bytes(pathlib.Path(__file__).read_bytes()))
            record = append(record, args.history)
            for problem in record["problems"]:
                print("NOTE     " + problem)
            for comparison in record["comparisons"]:
                print("%-8s %s" % (comparison["arm"], comparison["verdict_line"]))
            print("RECORDED %s -> %s | decision: %s%s" % (record["eval_id"], history_path(record["eval_id"], args.history),
                                                          record["decision"], " | RETROFIT" if record["retrofit"] else ""))
            return 0
        if args.command == "show":
            path = history_path(args.eval_id, args.history)
            if not path.exists():
                print("NO HISTORY for %s" % args.eval_id)
                return 1
            for line in path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                print("%s  %-12s  %s" % (record["recorded_at"], record["decision"],
                                         " | ".join(c["verdict_line"] for c in record["comparisons"])))
            return 0
        problems = verify(args.eval_id, args.history)
        for problem in problems:
            print("BROKEN   " + problem)
        print("HISTORY  %s: %s" % (args.eval_id, "chain intact" if not problems else "%d problem(s)" % len(problems)))
        return 1 if problems else 0
    except (RecordError, OSError, ValueError) as exc:
        print("REFUSED  %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
