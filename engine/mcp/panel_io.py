# -*- coding: utf-8 -*-
"""panel_io.py -- save a seat's answer verbatim and report what a panel still owes.

Core functions with no MCP dependency, so a CLI or a hook can call them too. The panel protocol
itself (lock, anonymize, checks, tally) stays in engine/panel/panel.py; this file only adds the two
operations an agent needs while a panel runs:

  submit(provenance, run_dir, round, seat, text)   write round1|round2/<seat>.md exactly as given
  status(provenance, run_dir)                      phase, answers on file, re-prompts owed

Refusals (each raises an adapter.AdapterError subclass, so every front door reports it the same way):
  * the panel is not locked, or panel.json changed after the lock (panel.verify)
  * the seat is not on the panel, or its id is not a safe file name
  * the launch role is "seat" and the seat is not the launch agent id
  * round 1 after anon_map.json exists (round-1 answers are hashed at anonymization)
  * round 2 before anon_map.json exists
  * run_dir outside the panel root, when one is configured
The text is never edited: the bytes written are the UTF-8 bytes of the string received.
Every accepted write appends one line to submissions.jsonl (round, seat, sha256, by, at).
"""
import datetime
import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
PANEL_DIR = HERE.parent / "panel"
if str(PANEL_DIR) not in sys.path:
    sys.path.insert(0, str(PANEL_DIR))

import panel  # noqa: E402
from adapter import GateRejected, InvalidArgument, NotFound  # noqa: E402

ROUNDS = {1: "round1", 2: "round2", "1": "round1", "2": "round2", "round1": "round1", "round2": "round2"}
SAFE_SEAT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
SEAT_ROLE = "seat"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def resolve_run(run_dir, root=None):
    if not run_dir or not str(run_dir).strip():
        raise InvalidArgument("run_dir is required")
    run = pathlib.Path(str(run_dir)).expanduser().resolve()
    if root:
        base = pathlib.Path(str(root)).expanduser().resolve()
        if run != base and base not in run.parents:
            raise GateRejected("run_dir %s is outside the configured panel root %s" % (run, base),
                               missing=["a run_dir under XSCHOLAR_PANEL_ROOT"])
    if not run.is_dir():
        raise NotFound("no run directory at %s" % run, missing=[str(run)])
    if not (run / "panel.json").exists():
        raise NotFound("no panel.json in %s" % run, missing=["panel.json"])
    return run


def _verified(run):
    try:
        return panel.verify(run)
    except panel.PanelError as exc:
        msg = str(exc)
        if "not locked" in msg:
            missing = ["panel.lock.json (run lock before round 1)"]
        elif "TAMPER" in msg:
            missing = ["panel.json unchanged since the lock"]
        else:
            missing = ["a valid panel.json"]
        raise GateRejected("panel protocol: %s" % msg, missing=missing) from None


def _round(rnd):
    key = rnd.strip().lower() if isinstance(rnd, str) else rnd
    if key not in ROUNDS:
        raise InvalidArgument("round must be 1 or 2 (got %r)" % (rnd,))
    return ROUNDS[key]


def submit(provenance, run_dir, rnd, seat, text, root=None):
    folder = _round(rnd)
    if not isinstance(text, str) or not text.strip():
        raise InvalidArgument("text is empty; submit the seat's full answer")
    if not isinstance(seat, str) or not SAFE_SEAT.match(seat):
        raise InvalidArgument("seat must be a plain seat id (letters, digits, _ . -)")
    run = resolve_run(run_dir, root)
    cfg, digest = _verified(run)
    if seat not in cfg["ids"]:
        raise NotFound("seat %r is not on panel %s" % (seat, cfg["panel_id"]), missing=["seat %s in panel.json" % seat])
    if provenance.role == SEAT_ROLE and seat != provenance.agent_id:
        raise GateRejected("launch identity is seat %r; a seat may submit only its own answer (got %r)"
                           % (provenance.agent_id, seat),
                           missing=["XSCHOLAR_AGENT_ID equal to the seat being submitted"])
    anonymized = (run / "anon_map.json").exists()
    if folder == "round1" and anonymized:
        raise GateRejected("round 1 is closed: anon_map.json exists, so round-1 answers are hashed and may not "
                           "be written or replaced. Start a new run directory to redo round 1.", missing=[])
    if folder == "round2" and not anonymized:
        raise GateRejected("round 2 opens only after round 1 is anonymized", missing=["anon_map.json (run anonymize)"])
    path = run / folder / (seat + ".md")
    path.parent.mkdir(exist_ok=True)
    replaced = path.exists()
    data = text.encode("utf-8")
    path.write_bytes(data)
    if path.read_bytes() != data:
        raise OSError("read-back of %s does not match the submitted bytes" % path)
    sha = hashlib.sha256(data).hexdigest()
    with (run / "submissions.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"round": folder, "seat": seat, "sha256": sha, "bytes": len(data), "replaced": replaced,
                             "by": provenance.by(), "panel_sha256": digest, "at": _now()}) + "\n")
    return {"panel_id": cfg["panel_id"], "round": folder, "seat": seat, "path": str(path), "bytes": len(data),
            "sha256": sha, "replaced": replaced, "by": provenance.by()}


def status(provenance, run_dir, root=None):
    run = resolve_run(run_dir, root)
    cfg, digest = _verified(run)
    only = provenance.agent_id if provenance.role == SEAT_ROLE else None
    seats = [s for s in cfg["ids"] if only is None or s == only]
    anonymized = (run / "anon_map.json").exists()
    out = {"panel_id": cfg["panel_id"], "target": cfg["target"], "question": cfg["question"],
           "options": cfg["options"], "panel_sha256": digest, "anonymized": anonymized,
           "tallied": (run / "tally.json").exists(), "filtered_to_seat": only}
    owed = []
    if not anonymized:
        out["phase"] = "round1"
        for sid in seats:
            text = panel.read_answer(run, "round1", sid)
            if text is None:
                owed.append({"condition": "missing_answer", "seat": sid,
                             "prompt": "No round-1 answer is on file for this seat. Run round 1 for it."})
            elif not panel.stance_ok(panel.parse(text)["stance"], cfg):
                owed.append({"condition": "missing_stance", "seat": sid,
                             "prompt": panel.owed_prompt("missing_stance", sid, "", cfg)})
        out["next"] = "anonymize" if not owed and only is None else "round-1 answers owed"
    else:
        out["phase"] = "round2"
        try:
            found = panel.evaluate(run)  # may write counterfactual.json (panel.py's agreement check)
        except panel.PanelError as exc:
            raise GateRejected("panel protocol: %s" % exc, missing=[]) from None
        for condition, sid, detail in found:
            if only is None or sid == only:
                owed.append({"condition": condition, "seat": sid, "detail": detail,
                             "prompt": panel.owed_prompt(condition, sid, detail, cfg)})
        out["next"] = "tally" if not found else "send each re-prompt to the seat it names"
    out["owed"] = owed
    out["owed_count"] = len(owed)
    return out
