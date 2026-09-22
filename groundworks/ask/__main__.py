# -*- coding: utf-8 -*-
"""The `groundworks ask` command. See groundworks/ask/__init__.py for the loop."""
import argparse
import json
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks.ask import check as checkmod, packet, render as rendermod  # noqa: E402


def _inst(path):
    if not path:
        return None
    from groundworks import config
    return config.load(path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="groundworks ask", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_open = sub.add_parser("open", help="rank the corpus against the question and write the packet")
    p_open.add_argument("question")
    p_open.add_argument("--units", required=True)
    p_open.add_argument("--out", required=True)
    p_open.add_argument("--instance")
    p_open.add_argument("--k", type=int, default=packet.DEFAULT_K)
    p_open.add_argument("--slice", type=int, default=None, help="units per slice (default: the instance's smallest chunk_size, else %d)" % packet.DEFAULT_SLICE)
    p_open.add_argument("--type", action="append", default=[], help="keep only this unit type (repeatable)")
    p_open.add_argument("--where", action="append", default=[], metavar="FIELD=VALUE",
                        help="keep only units whose top-level or locator field equals VALUE (repeatable)")
    p_check = sub.add_parser("check", help="audit read.jsonl and answer.json against the packet")
    p_check.add_argument("out")
    p_check.add_argument("--instance")
    p_render = sub.add_parser("render", help="check, then write answer.md")
    p_render.add_argument("out")
    p_render.add_argument("--instance")
    args = parser.parse_args(argv)

    if args.command == "open":
        where = {}
        for item in args.where:
            if "=" not in item:
                print("ask open refused: --where takes FIELD=VALUE, got %r" % item, file=sys.stderr)
                return 2
            key, value = item.split("=", 1)
            where[key.strip()] = value.strip()
        try:
            info = packet.open_packet(args.question, args.units, args.out, _inst(args.instance), k=args.k, slice_size=args.slice,
                                      types=args.type, where=where)
        except packet.PacketError as exc:
            print("ask open refused: %s" % exc, file=sys.stderr)
            return 2
        scope = info["scope"]
        print("ASK OPEN %s | corpus %d | in scope %d (types %s, where %s) | retrieved %d of k=%d | slices %d x %d | %s"
              % (info["status"], info["corpus_total"], scope["in_scope"], scope["types"] or "all", scope["where"] or "none",
                 info["retrieved"], info["k"], len(info["slices"]), info["slice_size"], args.out))
        return 0
    if args.command == "check":
        report = checkmod.check_packet(args.out, _inst(args.instance))
        print("ASK CHECK " + rendermod.header(report))
        for err in report.get("errors") or []:
            print("  " + err["error"])
        return checkmod.EXIT[report["verdict"]]
    if args.command == "render":
        text = rendermod.render(args.out, _inst(args.instance))
        report = json.loads((_pathlib.Path(args.out) / "check.json").read_text(encoding="utf-8"))
        print("ASK RENDER " + rendermod.header(report))
        if text is None:
            for err in report.get("errors") or []:
                print("  " + err["error"])
            print("  nothing rendered")
        return checkmod.EXIT[report["verdict"]]
    return 2


if __name__ == "__main__":
    sys.exit(main())
