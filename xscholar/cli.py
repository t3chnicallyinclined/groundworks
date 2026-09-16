# -*- coding: utf-8 -*-
"""cli.py -- the `xscholar` command. Thin: every subcommand runs an engine module.

    xscholar validate INSTANCE.yaml
    xscholar gates INSTANCE.yaml --emit schema|gates|probes
    xscholar gates INSTANCE.yaml --apply --url URL --ns NS --db DB
    xscholar lens INSTANCE.yaml --emit | --apply --url URL --ns NS --db DB
    xscholar run INSTANCE_DIR [--memory PORT | --url URL --ns NS --db DB]
    xscholar labels validate SLICE.jsonl LABELS.jsonl INSTANCE.yaml
    xscholar labels agreement A.jsonl B.jsonl [--out REPORT.json]
    xscholar panel lock|anonymize|check|tally RUN_DIR [--settings FILE]
    xscholar present render ROWS.json --mode speedread --policy P.json
    xscholar skills generate INSTANCE.yaml OUT_DIR
    xscholar drift INSTANCE.yaml SKILLS_DIR [DOC ...]
    xscholar test [--require-db] [--require-privacy]
                             every suite, the license check, the drift check and the privacy
                             scan, ending with a summary naming any check that did NOT run
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _module(name, args):
    return subprocess.call([sys.executable, "-m", name] + args, cwd=str(ROOT))


def _labels(args):
    if args[:1] == ["validate"]:
        return _module("xscholar.pipeline.validator", args[1:])
    if args[:1] == ["agreement"]:
        return _module("xscholar.pipeline.agreement", args[1:])
    print("usage: xscholar labels validate SLICE LABELS INSTANCE | xscholar labels agreement A.jsonl B.jsonl [--out R.json]")
    return 2


def _present(args):
    if args[:1] == ["render"]:
        return _module("xscholar.present.render", args[1:])
    print("usage: xscholar present render ROWS.json [--mode M] [--policy P]")
    return 2


def _skills(args):
    if args[:1] == ["generate"]:
        return _module("xscholar.skills", args[1:])
    print("usage: xscholar skills generate INSTANCE.yaml OUT_DIR")
    return 2


DEMO = ("instances/demo-incident/instance.yaml", "instances/demo-incident/skills")


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

    rc_lic, _ = _stream([sys.executable, str(ROOT / "scripts" / "license_check.py")])
    steps.append(("license check", rc_lic == 0, "clean" if rc_lic == 0 else "problems found"))

    # the generated role skills must still match the config they came from (ROADMAP C8)
    rc_drift = _module("xscholar.drift", list(DEMO))
    steps.append(("drift check", rc_drift == 0, "clean" if rc_drift == 0 else "drift found"))

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
    "validate": lambda a: _module("xscholar.config", a),
    "gates": lambda a: _module("xscholar.gates", a),
    "lens": lambda a: _module("xscholar.lens", a),
    "run": lambda a: subprocess.call([sys.executable, str(ROOT / "scripts" / "run_instance.py")] + a, cwd=str(ROOT)),
    "panel": lambda a: _module("xscholar.panel.panel", a),
    "labels": _labels,
    "present": _present,
    "skills": _skills,
    "drift": lambda a: _module("xscholar.drift", a),
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
