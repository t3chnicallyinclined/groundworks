# -*- coding: utf-8 -*-
"""panel.py -- a deliberation panel whose protocol is checked by a program, not by the coordinator's say-so.

A panel is a fixed set of seats, each seat a lens, judging one target in two rounds:
  round 1  blind: each seat sees only the question, its evidence packet and its own lens charter
  round 2  anonymized cross-examination: each seat sees every round-1 answer as Member A, B, C, ...
Models write prose. This program reads only the machine lines in each answer and refuses to tally
until every check passes. Protocol: PROTOCOL.md. Adapted ideas and license: NOTICE.md.

    python panel.py lock      RUN_DIR   freeze panel.json before any answer exists
    python panel.py anonymize RUN_DIR   round1/ -> round1_anon.md (+ anon_map.json, coordinator only)
    python panel.py check     RUN_DIR   checks on round2/; prints each re-prompt owed, by seat
    python panel.py tally     RUN_DIR   weighted tally -> tally.json, verdicts.jsonl

Exit: 0 pass or consensus; 1 protocol failure; 2 could not run (NOT EXECUTED); 3 split, returned to the human.
Standard library only.
"""
import argparse
import collections
import datetime
import hashlib
import json
import pathlib
import random
import re
import sys

CONFIDENCE = {"high": 1.0, "med": 0.75, "low": 0.5}
DEFAULT_THRESHOLDS = {"consensus": 2.0 / 3.0, "agreement": 0.70, "dissent_quota": 2}
WEIGHTED_SEAT_BASE = 1.5
MIN_NEW_WORDS = 5
MIN_FLAW_WORDS = 3

STANCE_RE = re.compile(r"^STANCE:\s*(?P<option>[^|]+?)\s*\|\s*CONFIDENCE:\s*(?P<conf>high|med|low)\s*"
                       r"\|\s*DEALBREAKER:\s*(?P<deal>yes|no)\s*$")
OBJECTION_RE = re.compile(r"^OBJECTION:\s*(?P<cat>[^|]+?)\s*\|\s*(?P<text>\S.*?)\s*$")
NEW_RE = re.compile(r"^NEW:\s*(?P<text>\S.*?)\s*$")
FLAW_RE = re.compile(r'^FLAW:\s*"(?P<quote>[^"]+)"\s*\|\s*(?P<why>\S.*?)\s*$')


class PanelError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ---- small helpers ---------------------------------------------------------------------------

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def words(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def norm(text):
    return " ".join(words(text))


def squash(text):
    return " ".join(text.split())


def identity_terms(seat):
    """Every spelling that would reveal a seat: its id, its lens, its aliases, with _ read as space or hyphen."""
    out = set()
    for term in [seat.get("id"), seat.get("lens")] + list(seat.get("aliases") or []):
        term = (term or "").strip()
        if term:
            out.update({term, term.replace("_", " "), term.replace("_", "-")})
    return out


def read_answer(run, rnd, seat, suffix=".md"):
    path = run / rnd / (seat + suffix)
    return path.read_text(encoding="utf-8") if path.exists() else None


# ---- panel file and lock ---------------------------------------------------------------------

def load_panel(run):
    path = run / "panel.json"
    if not path.exists():
        raise PanelError(2, "no panel.json in %s" % run)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PanelError(2, "panel.json is not valid JSON: %s" % exc)
    errors = []
    for key in ("panel_id", "target", "question"):
        if not cfg.get(key):
            errors.append("missing %s" % key)
    seats = cfg.get("seats") or []
    ids = [s.get("id") for s in seats]
    if len(seats) < 3:
        errors.append("a panel needs at least 3 seats")
    if not all(ids) or len(set(ids)) != len(ids):
        errors.append("seat ids must be present and unique")
    owner = {}
    for seat in seats:
        for term in identity_terms(seat):
            other = owner.setdefault(term.lower(), seat.get("id"))
            if other != seat.get("id"):
                errors.append("identity term %r is shared by %s and %s" % (term, other, seat.get("id")))
    options = [str(o).strip().lower() for o in cfg.get("options") or []]
    if len(options) < 2 or len(set(options)) != len(options):
        errors.append("options needs at least 2 distinct entries")
    if "abstain" in options:
        errors.append("'abstain' is reserved")
    if cfg.get("weighted_seat") is not None and cfg["weighted_seat"] not in ids:
        errors.append("weighted_seat %r is not a seat" % cfg["weighted_seat"])
    th = dict(DEFAULT_THRESHOLDS)
    th.update(cfg.get("thresholds") or {})
    if not 0.5 < th["consensus"] <= 1:
        errors.append("thresholds.consensus must be above 0.5 and at most 1")
    if not 0.5 <= th["agreement"] < 1:
        errors.append("thresholds.agreement must be at least 0.5 and below 1 (1 would switch the check off)")
    if not isinstance(th["dissent_quota"], int) or not 1 <= th["dissent_quota"] <= len(seats):
        errors.append("thresholds.dissent_quota must be an integer from 1 to the number of seats")
    # Objection categories are required. Without them the category check was switched off in silence (any
    # category passed) and dissent was keyed on normalised text, so "the same point al" and "the same point be"
    # counted as two seats dissenting (measured 2026-09-16). A panel whose categories are fewer than its
    # dissent quota could never satisfy the quota, so that is refused here rather than re-prompted forever.
    raw_categories = cfg.get("objection_categories")
    categories = ([str(c).strip().lower() for c in raw_categories]
                  if isinstance(raw_categories, list) and all(isinstance(c, str) for c in raw_categories) else None)
    if not categories or not all(categories) or len(set(categories)) != len(categories):
        errors.append("objection_categories must be a list of distinct, non-empty category names: dissent and the "
                      "agreement check are judged by category, and without the list neither can be checked")
    elif isinstance(th["dissent_quota"], int) and len(categories) < th["dissent_quota"]:
        errors.append("objection_categories has %d categories but thresholds.dissent_quota needs %d distinct ones"
                      % (len(categories), th["dissent_quota"]))
    weights = dict(CONFIDENCE)
    weights.update(cfg.get("confidence_weights") or {})
    if (set(weights) != set(CONFIDENCE)
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in weights.values())
            or not 0 < weights["low"] <= weights["med"] <= weights["high"] <= 1):
        errors.append("confidence_weights must set only high, med, low with 0 < low <= med <= high <= 1")
    if errors:
        raise PanelError(2, "panel.json invalid: " + "; ".join(errors))
    cfg["options"] = options
    cfg["thresholds"] = th
    cfg["categories"] = categories
    cfg["ids"] = ids
    cfg["confidence"] = weights
    return cfg


SETTINGS_KEYS = ("thresholds", "confidence_weights")


def apply_settings(run, settings_path):
    """Copy the owner's settings (thresholds, confidence_weights) into panel.json; refuse a panel that disagrees."""
    settings_path = pathlib.Path(settings_path)
    if not settings_path.exists():
        raise PanelError(2, "settings file not found: %s" % settings_path)
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PanelError(2, "settings file is not valid JSON: %s" % exc)
    path = run / "panel.json"
    if not path.exists():
        raise PanelError(2, "no panel.json in %s" % run)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PanelError(2, "panel.json is not valid JSON: %s" % exc)
    changed = False
    for key in SETTINGS_KEYS:
        if key not in settings:
            continue
        if key in cfg and cfg[key] != settings[key]:
            raise PanelError(1, "panel.json %s differs from the owner's settings file; remove it from panel.json" % key)
        if key not in cfg:
            cfg[key] = settings[key]
            changed = True
    if changed:
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def lock(run, settings=None):
    """Freeze the panel (seats, lenses, options, weighted seat, thresholds, weights) before any answer exists."""
    lock_path = run / "panel.lock.json"
    if lock_path.exists():
        raise PanelError(1, "already locked; a locked panel is never re-locked (start a new run directory)")
    round1 = run / "round1"
    if round1.exists() and any(round1.iterdir()):
        raise PanelError(1, "round1/ already has answers; lock the panel before any answer exists")
    if settings is not None:
        apply_settings(run, settings)
    load_panel(run)
    digest = sha_file(run / "panel.json")
    lock_path.write_text(json.dumps({"sha256": digest, "locked_at": now()}, indent=2), encoding="utf-8")
    return digest


def verify(run):
    cfg = load_panel(run)
    lock_path = run / "panel.lock.json"
    if not lock_path.exists():
        raise PanelError(1, "panel is not locked; run lock before round 1")
    digest = json.loads(lock_path.read_text(encoding="utf-8"))["sha256"]
    if sha_file(run / "panel.json") != digest:
        raise PanelError(1, "panel.json changed after it was locked (TAMPER); start a new run directory")
    return cfg, digest


# ---- machine lines ---------------------------------------------------------------------------

def parse(text):
    found = {"stance": None, "stance_lines": 0, "objections": [], "new": [], "flaws": []}
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("STANCE:"):
            found["stance_lines"] += 1
            m = STANCE_RE.match(line)
            if m:
                found["stance"] = {"option": m.group("option").strip().lower(),
                                   "confidence": m.group("conf"), "dealbreaker": m.group("deal") == "yes"}
        elif line.startswith("OBJECTION:"):
            m = OBJECTION_RE.match(line)
            if m:
                found["objections"].append({"category": m.group("cat").strip().lower(), "text": m.group("text")})
        elif line.startswith("NEW:"):
            m = NEW_RE.match(line)
            if m:
                found["new"].append(m.group("text"))
        elif line.startswith("FLAW:"):
            m = FLAW_RE.match(line)
            if m:
                found["flaws"].append({"quote": m.group("quote"), "why": m.group("why")})
    if found["stance_lines"] != 1:
        found["stance"] = None
    return found


def stance_ok(stance, cfg):
    return stance is not None and (stance["option"] in cfg["options"] or stance["option"] == "abstain")


def owed_prompt(condition, seat, detail, cfg):
    options = "|".join(cfg["options"] + ["abstain"])
    cats = "|".join(cfg["categories"])
    return {
        "missing_answer": "No round-2 answer is on file for this seat. Run round 2 for it.",
        "missing_stance": ("End your answer with exactly one line, and no other STANCE line: "
                           "STANCE: <%s> | CONFIDENCE: high|med|low | DEALBREAKER: yes|no" % options),
        "novelty_gate": ("Your round-2 answer adds nothing your round-1 answer did not already say. Engage the strongest "
                         "answer that disagrees with yours, by its Member label, and add at least one line "
                         "NEW: <a claim, risk, test or reading you did not make in round 1> (%d words or more)."
                         % MIN_NEW_WORDS),
        "flaw_required": ("You changed your stance from round 1. Change only if you can name the flaw in your earlier "
                          "argument: add FLAW: \"<exact words from your round-1 answer>\" | <why that reasoning was "
                          "wrong>. If you cannot name one, restore your round-1 stance."),
        "bad_objection": "Objection categories must be one of: %s. Found: %s." % (cats, detail),
        "dissent_quota": ("The panel has %s. State your strongest objection to the majority position, on a point no "
                          "other seat has raised: OBJECTION: <%s> | <the objection>." % (detail, cats)),
        "agreement_check": ("Most seats back '%s'. Assume that is wrong. Write round2/%s.counterfactual.md: the "
                            "strongest alternative as OBJECTION: <%s> | <text> lines, at least one of them (%d words "
                            "or more) an objection your round-2 answer does not already make, and what evidence "
                            "would flip the decision. This does not change your stance; to change it, revise your "
                            "round-2 answer with a FLAW line." % (detail, seat, cats, MIN_FLAW_WORDS)),
    }[condition]


# ---- round 1 -> anonymized bundle --------------------------------------------------------------

def anonymize(run):
    cfg, digest = verify(run)
    problems, texts = [], {}
    for sid in cfg["ids"]:
        text = read_answer(run, "round1", sid)
        if text is None:
            problems.append("%s: no round-1 answer" % sid)
        elif not stance_ok(parse(text)["stance"], cfg):
            problems.append("%s: %s" % (sid, owed_prompt("missing_stance", sid, "", cfg)))
        else:
            texts[sid] = text
    if problems:
        raise PanelError(1, "round 1 incomplete:\n  " + "\n  ".join(problems))
    order = list(cfg["ids"])
    random.Random(digest).shuffle(order)  # label order comes from the lock, not from panel order
    labels = dict((sid, "Member %s" % chr(ord("A") + i)) for i, sid in enumerate(order))
    term_label = {}
    for seat in cfg["seats"]:
        for term in identity_terms(seat):
            term_label[term.lower()] = labels[seat["id"]]
    pattern = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(t) for t in sorted(term_label, key=len, reverse=True))
                         + r")(?![A-Za-z0-9])", re.I)
    parts = ["# Round 1 answers, anonymized", "", "One of these is your own. Judge each by its argument, not its source.", ""]
    for sid in order:
        body = pattern.sub(lambda m: term_label[m.group(1).lower()], texts[sid]).strip()
        parts += ["## %s" % labels[sid], "", body, ""]
    (run / "round1_anon.md").write_text("\n".join(parts), encoding="utf-8")
    amap = {"labels": labels, "order": order,
            "round1_sha256": dict((sid, sha_file(run / "round1" / (sid + ".md"))) for sid in order)}
    (run / "anon_map.json").write_text(json.dumps(amap, indent=2), encoding="utf-8")
    return len(order)


# ---- round 2 checks ------------------------------------------------------------------------------

def objection_key(objection, cfg):
    """Dissent is counted by category: load_panel refuses a panel without categories."""
    return objection["category"]


def dissent_owed(cfg, r2):
    """At least `dissent_quota` seats must each contribute an objection no other counted seat raised."""
    quota = cfg["thresholds"]["dissent_quota"]
    keys = dict((sid, {k for k in (objection_key(o, cfg) for o in p["objections"]) if k}) for sid, p in r2.items())
    used, dissenters = set(), []
    for sid in sorted(keys, key=lambda s: (len(keys[s]), s)):
        fresh = sorted(keys[sid] - used)
        if fresh:
            used.add(fresh[0])
            dissenters.append(sid)
    if len(dissenters) >= quota:
        return []
    detail = "%d distinct objection(s) from %d seat(s), %d required" % (len(used), len(dissenters), quota)
    quiet = [sid for sid in cfg["ids"] if sid not in dissenters]
    return [("dissent_quota", sid, detail) for sid in quiet[:quota - len(dissenters)]]


def agreement_owed(run, cfg, r2):
    """Above the agreement threshold, the two least confident majority seats owe a counterfactual."""
    stances = dict((sid, p["stance"]) for sid, p in r2.items())
    counts = collections.Counter(s["option"] for s in stances.values() if s["option"] != "abstain")
    if not counts:
        return []
    top, n = counts.most_common(1)[0]
    if n / float(len(cfg["ids"])) <= cfg["thresholds"]["agreement"]:
        return []
    chosen_path = run / "counterfactual.json"
    chosen = None
    if chosen_path.exists():
        saved = json.loads(chosen_path.read_text(encoding="utf-8"))
        if saved.get("option") == top:
            chosen = saved["seats"]
    if chosen is None:
        majority = sorted((sid for sid in cfg["ids"] if stances[sid]["option"] == top),
                          key=lambda sid: (cfg["confidence"][stances[sid]["confidence"]], len(r2[sid]["objections"]), sid))
        chosen = majority[:2]
        chosen_path.write_text(json.dumps({"option": top, "seats": chosen, "chosen_at": now()}, indent=2),
                               encoding="utf-8")
    return [("agreement_check", sid, top) for sid in chosen if not counterfactual_ok(run, cfg, sid)]


def counterfactual_ok(run, cfg, sid):
    """A counterfactual does its work only with an objection the seat has not already made.

    Until 2026-09-16 the mere presence of one OBJECTION line satisfied it: `OBJECTION: vibes | x`, or a copy of
    the seat's own round-2 objection, released the tally (measured). Now every objection must use a panel
    category, and at least one must be MIN_FLAW_WORDS words or more and not already said in the seat's round-2
    answer. Whether it differs in substance is still a judgement the program cannot make (PROTOCOL.md)."""
    objections = parse(read_answer(run, "round2", sid, ".counterfactual.md"))["objections"]
    if not objections or any(o["category"] not in cfg["categories"] for o in objections):
        return False
    own = norm(read_answer(run, "round2", sid) or "")
    return any(len(words(o["text"])) >= MIN_FLAW_WORDS and norm(o["text"]) not in own for o in objections)


def evaluate(run):
    """Every re-prompt owed, as (condition, seat, detail). Empty means the protocol is satisfied."""
    cfg, _ = verify(run)
    map_path = run / "anon_map.json"
    if not map_path.exists():
        raise PanelError(1, "round 1 was not anonymized; run anonymize before round 2")
    amap = json.loads(map_path.read_text(encoding="utf-8"))
    for sid in cfg["ids"]:
        path = run / "round1" / (sid + ".md")
        if not path.exists() or sha_file(path) != amap["round1_sha256"].get(sid):
            raise PanelError(1, "round1/%s.md changed after anonymization (TAMPER)" % sid)
    owed, r2 = [], {}
    for sid in cfg["ids"]:
        t1 = read_answer(run, "round1", sid)
        p1 = parse(t1)
        t2 = read_answer(run, "round2", sid)
        if t2 is None:
            owed.append(("missing_answer", sid, ""))
            continue
        p2 = r2[sid] = parse(t2)
        if not stance_ok(p2["stance"], cfg):
            owed.append(("missing_stance", sid, ""))
        elif p2["stance"]["option"] != p1["stance"]["option"]:
            if not any(len(words(f["quote"])) >= MIN_FLAW_WORDS and squash(f["quote"]) in squash(t1)
                       for f in p2["flaws"]):
                owed.append(("flaw_required", sid, ""))
        if not any(len(words(n)) >= MIN_NEW_WORDS and norm(n) not in norm(t1) for n in p2["new"]):
            owed.append(("novelty_gate", sid, ""))
        bad = sorted({o["category"] for o in p2["objections"]} - set(cfg["categories"]))
        if bad:
            owed.append(("bad_objection", sid, ", ".join(bad)))
    if len(r2) == len(cfg["ids"]):
        owed += dissent_owed(cfg, r2)
        if all(stance_ok(p["stance"], cfg) for p in r2.values()):
            owed += agreement_owed(run, cfg, r2)
    return owed


def log_owed(run, owed):
    """Append each owed re-prompt once per version of the answer it concerns."""
    path = run / "enforcement.jsonl"
    seen = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                seen.add((row["condition"], row["seat"], row["answer_sha256"]))
    with path.open("a", encoding="utf-8") as fh:
        for condition, sid, detail in owed:
            answer = run / "round2" / (sid + (".counterfactual.md" if condition == "agreement_check" else ".md"))
            digest = sha_file(answer) if answer.exists() else "none"
            if (condition, sid, digest) in seen:
                continue
            seen.add((condition, sid, digest))
            fh.write(json.dumps({"condition": condition, "seat": sid, "answer_sha256": digest,
                                 "detail": detail, "at": now()}) + "\n")


def enforcement_counts(run):
    path = run / "enforcement.jsonl"
    counts = collections.Counter()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                counts[json.loads(line)["condition"]] += 1
    return dict(counts)


# ---- tally ---------------------------------------------------------------------------------------

def tally(run):
    cfg, digest = verify(run)
    owed = evaluate(run)
    if owed:
        log_owed(run, owed)
        raise PanelError(1, "NOT TALLIED: %d re-prompt(s) owed; run check" % len(owed))
    seats = dict((s["id"], s) for s in cfg["seats"])
    answers = dict((sid, parse(read_answer(run, "round2", sid))) for sid in cfg["ids"])
    base = dict((sid, WEIGHTED_SEAT_BASE if sid == cfg.get("weighted_seat") else 1.0) for sid in cfg["ids"])
    w_total = sum(base.values())  # base weights: a hesitant panel cannot shrink its own bar
    weights = dict((o, 0.0) for o in cfg["options"])
    backers = dict((o, []) for o in cfg["options"])
    for sid, answer in answers.items():
        stance = answer["stance"]
        if stance["option"] == "abstain":
            continue
        weights[stance["option"]] += base[sid] * cfg["confidence"][stance["confidence"]]
        backers[stance["option"]].append({"seat": sid, "base": base[sid], "confidence": stance["confidence"]})
    bar = cfg["thresholds"]["consensus"] * w_total
    passing = [o for o in cfg["options"] if weights[o] + 1e-9 >= bar]
    verdict = max(passing, key=lambda o: weights[o]) if passing else None
    minority = [{"seat": sid, "lens": seats[sid].get("lens"), "stance": a["stance"]["option"]}
                for sid, a in answers.items() if a["stance"]["dealbreaker"] and a["stance"]["option"] != verdict]
    result = {"panel_id": cfg["panel_id"], "target": cfg["target"], "panel_sha256": digest,
              "result": "consensus" if verdict else "split", "verdict": verdict,
              "w_total": w_total, "bar": round(bar, 4), "weights": dict((o, round(w, 4)) for o, w in weights.items()),
              "backers": backers, "weighted_seat": cfg.get("weighted_seat"), "minority_report": minority,
              "thresholds": cfg["thresholds"], "confidence_weights": cfg["confidence"],
              "enforcement": enforcement_counts(run), "tallied_at": now()}
    (run / "tally.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    with (run / "verdicts.jsonl").open("w", encoding="utf-8") as fh:
        for sid in cfg["ids"]:
            a = answers[sid]
            counter = parse(read_answer(run, "round2", sid, ".counterfactual.md"))
            fh.write(json.dumps({"panel_id": cfg["panel_id"], "target": cfg["target"], "seat": sid,
                                 "lens": seats[sid].get("lens"), "stance": a["stance"]["option"],
                                 "confidence": a["stance"]["confidence"], "dealbreaker": a["stance"]["dealbreaker"],
                                 "objections": a["objections"], "new": a["new"], "flaws": a["flaws"],
                                 "counterfactual_objections": counter["objections"], "grade": "agent",
                                 "panel_sha256": digest,
                                 "answer_sha256": sha_file(run / "round2" / (sid + ".md"))}) + "\n")
    return result


# ---- command line --------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Deliberation panel protocol checker (see PROTOCOL.md).")
    parser.add_argument("command", choices=["lock", "anonymize", "check", "tally"])
    parser.add_argument("run_dir")
    parser.add_argument("--settings", help="lock only: owner settings file (thresholds, confidence_weights) to copy in")
    args = parser.parse_args(argv)
    run = pathlib.Path(args.run_dir)
    try:
        if args.command == "lock":
            print("LOCKED %s sha256=%s" % (run, lock(run, args.settings)))
            return 0
        if args.command == "anonymize":
            print("ANONYMIZED %d round-1 answers -> %s" % (anonymize(run), run / "round1_anon.md"))
            return 0
        if args.command == "check":
            cfg, _ = verify(run)
            owed = evaluate(run)
            log_owed(run, owed)
            for condition, sid, detail in owed:
                print("OWED %-16s %s\n    %s" % (condition, sid, owed_prompt(condition, sid, detail, cfg)))
            print("%s: %d re-prompt(s) owed; enforcement so far %s"
                  % ("FAIL" if owed else "CHECKS PASS", len(owed), json.dumps(enforcement_counts(run), sort_keys=True)))
            return 1 if owed else 0
        result = tally(run)
        print(json.dumps(result, indent=2))
        if result["verdict"] is None:
            print("SPLIT: no option reached the consensus bar (%s of %s). Returned to the human; do not write a "
                  "consensus." % (result["bar"], result["w_total"]))
            return 3
        print("CONSENSUS: %s" % result["verdict"])
        return 0
    except PanelError as exc:
        print(("NOT EXECUTED: " if exc.code == 2 else "FAIL: ") + str(exc))
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
