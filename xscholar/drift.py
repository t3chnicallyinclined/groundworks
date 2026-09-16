# -*- coding: utf-8 -*-
"""drift.py -- refuse to ship a role skill or a doc that no longer matches the instance config.

Three checks, each naming the file and the item that drifted (ROADMAP C8):

  (a) presence   every lens, seat, panel option, objection category and gate id in `instance.yaml`
                 appears in the generated skill that uses it
  (b) freshness  every generated file still hashes to what a fresh generation produces: a hand edit
                 breaks its own `content-sha256` line, a config change makes the file stale
  (c) docs       in any doc passed, every `lens_<name>` and `seat_<name>` mentioned exists in the config.
                 A doc that mentions none checked nothing, so it is drift, not a pass. The engine's own
                 identifiers that share the prefix (`lens_view`, the read tool; `lens_bound`, an access key)
                 are not lens mentions.

    check(inst_path, skills_dir, docs=()) -> Report      Report.ok is False when anything drifted
    python -m xscholar.drift INSTANCE.yaml SKILLS_DIR [DOC ...]

Exit 0 clean, 1 drift, 2 could not run (NOT EXECUTED). Docs are opt-in per invocation: a doc that
carries worked examples from another instance (`xscholar/panel/PROTOCOL.md`) would fail check (c) by
design, so pass only docs that describe THIS instance. `xscholar test` and CI pass the demo's README and
its generated skill. Until 2026-09-16 they passed none, so check (c) had never run outside its unit tests,
and its first real run failed the engine's own generated skill on `lens_view` (the read tool's name).

Standard library only.
"""
import pathlib
import re
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config, skills  # noqa: E402

DOC_MENTION = re.compile(r"\b(lens|seat)_([a-z0-9_]+)\b")
DOC_EXT = {".md", ".txt"}
# Engine identifiers that begin like a lens or seat mention and are not one: an instance cannot rename them.
ENGINE_NAMES = {"lens_view": "the lens read tool", "lens_bound": "an access role key"}


def names(item, text):
    """True when `text` names `item` as a whole token: 'owner' is not named by 'service_owner'."""
    return re.search(r"(?<![0-9A-Za-z_])%s(?![0-9A-Za-z_])" % re.escape(str(item)), text) is not None


class DriftError(Exception):
    """The check could not run at all. Not a drift: a drift is reported, never raised."""


class Report:
    """What drifted, as (file, item, why). A report with no rows is a pass."""

    def __init__(self, instance, skills_dir):
        self.instance = str(instance)
        self.skills_dir = str(skills_dir)
        self.rows = []
        self.checked = {"skills": 0, "items": 0, "docs": 0, "doc_mentions": 0}

    def add(self, path, item, why):
        self.rows.append({"file": str(path), "item": str(item), "why": why})

    @property
    def ok(self):
        return not self.rows

    def items(self):
        return list(self.rows)

    def lines(self):
        out = ["DRIFT %s: %s -- %s" % (r["file"], r["item"], r["why"]) for r in self.rows]
        out.append("drift check %s: %d skill file(s), %d config item(s), %d doc(s) naming %d lens/seat mention(s), "
                   "%d drift(s)" % ("clean" if self.ok else "FAILED", self.checked["skills"], self.checked["items"],
                                    self.checked["docs"], self.checked["doc_mentions"], len(self.rows)))
        return out

    def __str__(self):
        return "\n".join(self.lines())


# ---- the expectations ----------------------------------------------------------------------------

def expected_items(inst):
    """skill name -> [(item, why it must be there)]: what check (a) looks for in each generated file."""
    gate_ids = [str(rule["id"]) for rule in inst.gate_rules()]
    lens_names = [str(lens["name"]) for lens in inst.lenses]
    out = {}
    for role in inst.roles:
        skill = role.get("skill")
        wanted = []
        for name in lens_names:
            wanted.append((name, "lens %s is defined in the config" % name))
        for name in role.get("lenses") or []:
            wanted.append(("lens_%s" % name, "role %s reads through view lens_%s" % (skill, name)))
            wanted.append(("seat_%s" % name, "role %s seats lens %s" % (skill, name)))
        panel = role.get("panel") or {}
        for option in panel.get("options") or []:
            wanted.append((str(option), "panel option for role %s" % skill))
        for category in panel.get("objection_categories") or []:
            wanted.append((str(category), "objection category for role %s" % skill))
        for gate_id in gate_ids:
            wanted.append((gate_id, "gate %s binds every role" % gate_id))
        seen, unique = set(), []
        for item, why in wanted:            # keep config order, drop repeats
            if item not in seen:
                seen.add(item)
                unique.append((item, why))
        out[skill] = unique
    return out


# ---- the check -----------------------------------------------------------------------------------

def check(inst_path, skills_dir, docs=()):
    """Report every drift between the config, the generated skills, and any docs passed."""
    try:
        inst = config.load(inst_path)
    except config.ConfigError as exc:
        raise DriftError("cannot read instance %s: %s" % (inst_path, exc)) from exc
    skills_dir = pathlib.Path(skills_dir)
    report = Report(getattr(inst, "source", None) or inst.id, skills_dir)
    try:
        targets = skills.expected_files(inst, skills_dir)
    except skills.SkillError as exc:
        raise DriftError(str(exc)) from exc
    wanted = expected_items(inst)

    # (b) freshness, and the files that should or should not exist
    fresh = {}
    for index, role in enumerate(inst.roles):
        skill = role["skill"]
        try:
            fresh[skill] = skills.render(inst, role, index)
        except skills.SkillError as exc:
            raise DriftError(str(exc)) from exc
    for skill, path in targets.items():
        report.checked["skills"] += 1
        if not path.is_file():
            report.add(path, "skill %s" % skill,
                       "missing; run `xscholar skills generate %s %s`" % (report.instance, skills_dir))
            continue
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        stated, actual = skills.stated_hash(text), skills.content_hash(text)
        if stated is None:
            report.add(path, "content-sha256",
                       "the file states no content hash; it was not produced by xscholar.skills")
        elif stated != actual:
            report.add(path, "content-sha256",
                       "edited by hand after generation: the file states %s but its content hashes to %s"
                       % (stated[:12], actual[:12]))
        elif text != fresh[skill]:
            report.add(path, "content-sha256",
                       "stale: the config changed since this file was generated; regenerate it")
    for path in sorted(skills_dir.glob("*/SKILL.md")) if skills_dir.is_dir() else []:
        if path not in set(targets.values()):
            report.add(path, "skill %s" % path.parent.name,
                       "no role in the config generates this skill; delete it or add the role")

    # (a) presence: every config item in the skill that uses it
    for skill, path in targets.items():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for item, why in wanted.get(skill, []):
            report.checked["items"] += 1
            if not names(item, text):
                report.add(path, item, "%s, but the generated skill never names it" % why)

    # (c) docs: every lens and seat a doc mentions must exist
    known = {str(lens["name"]) for lens in inst.lenses}
    for doc in docs:
        doc = pathlib.Path(doc)
        report.checked["docs"] += 1
        if not doc.is_file():
            report.add(doc, "doc", "not a file")
            continue
        if doc.suffix not in DOC_EXT:
            report.add(doc, "doc", "not a prose doc (%s); pass .md or .txt" % (doc.suffix or "no suffix"))
            continue
        text = doc.read_text(encoding="utf-8", errors="replace")
        mentions = sorted({(kind, name) for kind, name in DOC_MENTION.findall(text)
                           if "%s_%s" % (kind, name) not in ENGINE_NAMES or name in known})
        if not mentions:
            report.add(doc, "doc", "names no lens_<name> or seat_<name>, so check (c) checked nothing in it; "
                                   "pass a doc that describes this instance's lenses and seats")
            continue
        report.checked["doc_mentions"] += len(mentions)
        for kind, name in mentions:
            if name not in known:
                report.add(doc, "%s_%s" % (kind, name),
                           "named in this doc, but instance %s defines no lens %r; lenses: %s"
                           % (inst.id, name, ", ".join(sorted(known)) or "none"))
    return report


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or argv[0] in ("-h", "--help"):
        print("usage: python -m xscholar.drift INSTANCE.yaml SKILLS_DIR [DOC ...]")
        return 2
    try:
        report = check(argv[0], argv[1], argv[2:])
    except DriftError as exc:
        print("drift check NOT EXECUTED: %s" % exc)
        return 2
    print(str(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
