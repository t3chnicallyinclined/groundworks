# -*- coding: utf-8 -*-
"""cli.py -- the `xscholar` command. Thin: every subcommand runs an engine module.

    xscholar validate INSTANCE.yaml
    xscholar gates INSTANCE.yaml --emit schema|gates|probes
    xscholar gates INSTANCE.yaml --apply --url URL --ns NS --db DB
    xscholar run INSTANCE_DIR [--memory PORT | --url URL --ns NS --db DB]
    xscholar labels validate SLICE.jsonl LABELS.jsonl INSTANCE.yaml
    xscholar labels agreement A.jsonl B.jsonl [--out REPORT.json]
    xscholar panel lock|anonymize|check|tally RUN_DIR [--settings FILE]
    xscholar present render ROWS.json --mode speedread --policy P.json
    xscholar test            run every suite and the license check
"""
import pathlib
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


def _test(args):
    rc = subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"] + args,
                         cwd=str(ROOT))
    return rc or subprocess.call([sys.executable, str(ROOT / "scripts" / "license_check.py")], cwd=str(ROOT))


COMMANDS = {
    "validate": lambda a: _module("xscholar.config", a),
    "gates": lambda a: _module("xscholar.gates", a),
    "run": lambda a: subprocess.call([sys.executable, str(ROOT / "scripts" / "run_instance.py")] + a, cwd=str(ROOT)),
    "panel": lambda a: _module("xscholar.panel.panel", a),
    "labels": _labels,
    "present": _present,
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
