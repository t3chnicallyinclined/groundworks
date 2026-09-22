# -*- coding: utf-8 -*-
"""cli.py -- the `groundworks` command. Thin: every subcommand runs an engine module.

    groundworks validate INSTANCE.yaml
    groundworks gates INSTANCE.yaml --emit schema|gates|probes
    groundworks gates INSTANCE.yaml --apply --url URL --ns NS --db DB
    groundworks lens INSTANCE.yaml --emit | --apply --url URL --ns NS --db DB
    groundworks run INSTANCE_DIR [--memory PORT | --url URL --ns NS --db DB]
    groundworks labels validate SLICE.jsonl LABELS.jsonl INSTANCE.yaml
    groundworks labels agreement A.jsonl B.jsonl [--out REPORT.json]
    groundworks panel lock|anonymize|check|tally RUN_DIR [--settings FILE]
    groundworks present render ROWS.json --mode speedread --policy P.json
    groundworks skills generate INSTANCE.yaml OUT_DIR
    groundworks drift INSTANCE.yaml SKILLS_DIR [DOC ...]
    groundworks evaluate INSTANCE_DIR --memory PORT | --url URL --ns NS | --check
                             the instance's regression file: every error it has made, checked again
    groundworks ask open "QUESTION" --units UNITS.jsonl --out DIR [--instance I.yaml] [--k 200]
    groundworks ask check DIR [--instance I.yaml]     a reader's read.jsonl + answer.json audited: every quote
    groundworks ask render DIR [--instance I.yaml]    found word for word, every slice unit read; else red
    groundworks mutate lint | run [--no-db] [--ids ..] [--changed-since REV] | canaries
                             the mutation registry: one named break per guard (tests/harness/mutate.py)
    groundworks test [--require-db] [--require-privacy]
                             every suite, the license check, the drift check, the mutation-registry
                             lint, the demo's evaluator and the privacy scan, ending with a summary
                             naming any check that did NOT run
"""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _module(name, args):
    return subprocess.call([sys.executable, "-m", name] + args, cwd=str(ROOT))


def _labels(args):
    if args[:1] == ["validate"]:
        return _module("groundworks.pipeline.validator", args[1:])
    if args[:1] == ["agreement"]:
        return _module("groundworks.pipeline.agreement", args[1:])
    print("usage: groundworks labels validate SLICE LABELS INSTANCE | groundworks labels agreement A.jsonl B.jsonl [--out R.json]")
    return 2


def _present(args):
    if args[:1] == ["render"]:
        return _module("groundworks.present.render", args[1:])
    print("usage: groundworks present render ROWS.json [--mode M] [--policy P]")
    return 2


def _skills(args):
    if args[:1] == ["generate"]:
        return _module("groundworks.skills", args[1:])
    print("usage: groundworks skills generate INSTANCE.yaml OUT_DIR")
    return 2


DEMO = ("instances/demo-incident/instance.yaml", "instances/demo-incident/skills")
DEMO_DIR = "instances/demo-incident"
# The docs the drift check's docs arm (check c) reads. Until 2026-09-16 none was passed, so that arm had never
# run outside its unit tests; its first real run failed the generated skill on the read tool's name.
DEMO_DOCS = ("instances/demo-incident/README.md", "instances/demo-incident/skills/red-team/SKILL.md")


def _stream(cmd):
    """Run a command, show its output as it arrives, and keep it for inspection."""
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, errors="replace")
    lines = []
    for line in proc.stdout:
        sys.stdout.write(line)
        lines.append(line)
    proc.wait()
    return proc.returncode, "".join(lines)


def _last_line(out):
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return lines[-1][:160] if lines else "(no output)"


def _test(args):
    """Every check, and a summary that names any one that did NOT run.

    `unittest` exits 0 when a suite skips, so a machine (or a CI job) with no SurrealDB runs none of
    the database tests and still prints OK. Measured: `Ran 0 tests ... OK (skipped=2)`, exit 0. The
    suites are honest -- they say NOT EXECUTED -- but they say it through a green exit code, and the
    line a human reads is the last one. So the last line here names what ran and what did not.
    """
    strict_db = "--require-db" in args
    strict_privacy = "--require-privacy" in args
    passthrough = [a for a in args if a not in ("--require-db", "--require-privacy")]

    steps = []          # (name, ok, detail)  ok: True pass, False fail, None did not run
    rc, out = _stream([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]
                      + passthrough)
    ran = re.search(r"^Ran (\d+) tests?", out, re.M)
    skipped = re.search(r"skipped=(\d+)", out)
    n_ran, n_skipped = int(ran.group(1)) if ran else 0, int(skipped.group(1)) if skipped else 0
    steps.append(("tests", rc == 0, "%d ran, %d skipped" % (n_ran, n_skipped)))
    if n_skipped and strict_db:
        steps.append(("--require-db", False,
                      "%d test(s) skipped; this run was told every suite must execute" % n_skipped))
    elif n_skipped:
        steps.append(("skipped suites", None,
                      "%d test(s) skipped -- read the NOT EXECUTED lines above; pass --require-db "
                      "to make that a failure" % n_skipped))

    # its last line counts the files and imports it scanned; a tree it scanned nothing in exits 2, a failure here
    rc_lic, out_lic = _stream([sys.executable, str(ROOT / "scripts" / "license_check.py")])
    steps.append(("license check", rc_lic == 0, _last_line(out_lic)))

    # the generated role skills, and the docs that describe the demo, must still match its config (ROADMAP C8)
    rc_drift, out_drift = _stream([sys.executable, "-m", "groundworks.drift"] + list(DEMO) + list(DEMO_DOCS))
    steps.append(("drift check", rc_drift == 0, _last_line(out_drift)))

    # The mutation registry (docs/TESTING.md s.2), without running a break: every anchor still matches
    # exactly once and outside comments, every named test still exists above its file's __main__ block,
    # and no pinned id was removed. A stale anchor is a guard nobody is checking any more.
    rc_lint, out_lint = _stream([sys.executable, "-m", "tests.harness.mutate", "lint", "--from", "worktree"])
    steps.append(("mutation lint", rc_lint == 0, _last_line(out_lint)))

    # The evaluator the config declares (docs/METHOD.md s.15, docs/TESTING.md s.3). Until 2026-09-16 it was
    # required by config, declared by the demo, and never run. It starts its own throwaway server on
    # GROUNDWORKS_EVAL_PORT, or an OS-assigned free port; exit 2 is NOT EXECUTED (no server), never a pass.
    port = os.environ.get("GROUNDWORKS_EVAL_PORT", "0")
    rc_eval, out_eval = _stream([sys.executable, "-m", "groundworks.evaluator", DEMO_DIR, "--memory", port])
    if rc_eval == 2:
        steps.append(("evaluator", False if strict_db else None,
                      "NOT EXECUTED: %s%s" % (_last_line(out_eval),
                                              "; this run was told every suite must execute" if strict_db else "")))
    else:
        steps.append(("evaluator", rc_eval == 0, _last_line(out_eval)))

    # The privacy scan had no automated caller at all until now. Its deny list lives outside the
    # repository by design, so on a machine without one it cannot run -- and that must be visible,
    # not silent. Exit 2 is "NOT EXECUTED", which is not a pass and not a failure.
    rc_priv, _ = _stream([sys.executable, str(ROOT / "scripts" / "privacy_scan.py")])
    if rc_priv == 2:
        steps.append(("privacy scan", False if strict_privacy else None,
                      "NOT EXECUTED (see the line above)" +
                      ("; this run was told it must run" if strict_privacy else
                       "; pass --require-privacy on a machine that holds matter data")))
    else:
        steps.append(("privacy scan", rc_priv == 0, "clean" if rc_priv == 0 else "identifiers found"))

    print("\n%-16s %s" % ("CHECK", "RESULT"))
    for name, ok, detail in steps:
        print("%-16s %-14s %s" % (name, {True: "passed", False: "FAILED", None: "NOT EXECUTED"}[ok], detail))
    return 1 if any(ok is False for _, ok, _ in steps) else 0


COMMANDS = {
    "validate": lambda a: _module("groundworks.config", a),
    "gates": lambda a: _module("groundworks.gates", a),
    "lens": lambda a: _module("groundworks.lens", a),
    "run": lambda a: subprocess.call([sys.executable, str(ROOT / "scripts" / "run_instance.py")] + a, cwd=str(ROOT)),
    "panel": lambda a: _module("groundworks.panel.panel", a),
    "labels": _labels,
    "present": _present,
    "skills": _skills,
    "drift": lambda a: _module("groundworks.drift", a),
    "evaluate": lambda a: _module("groundworks.evaluator", a),
    "ask": lambda a: _module("groundworks.ask", a),
    "mutate": lambda a: _module("tests.harness.mutate", a),
    "test": _test,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in COMMANDS:
        print(__doc__)
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main())
