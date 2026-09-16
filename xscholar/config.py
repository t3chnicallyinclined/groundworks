# -*- coding: utf-8 -*-
"""config.py -- the instance config: one file that answers the eight questions.

Everything an instance knows lives here: its units and their locators, its fidelity ladder, its
status axis, its vocabulary, its gate rules, its lenses and seats, its evaluator and what only a
human may change. Engine modules read an `Instance`; no engine module contains a domain word.

    inst = load("instances/demo-incident/instance.yaml")
    inst.confirming_grades()      # grades that may establish a claim
    inst.allowed_vocabulary()     # what the label validator accepts
    inst.gate_rules()             # what the datastore must refuse

Accepts JSON, or a strict YAML subset (mappings, lists, scalars, one-line flow collections,
comments). Anything it does not understand raises rather than guessing.

Standard library only.
"""
import json
import pathlib
import re

SCALAR_TRUE = {"true", "yes", "on"}
SCALAR_FALSE = {"false", "no", "off"}
SCALAR_NULL = {"null", "none", "~", ""}
ID_RE = re.compile(r"^[a-z0-9_]+$")

TOP_KEYS = {"instance", "units", "fidelity", "status", "vocabulary", "gates",
            "lenses", "roles", "evaluator", "human_owned", "privacy", "panel", "present", "embed", "access",
            "authority", "staleness", "discovery"}
REQUIRED_TOP = ["instance", "units", "fidelity", "status", "vocabulary", "gates",
                "evaluator", "human_owned", "privacy"]
MUST_BE_HUMAN_OWNED = ["gates", "fidelity", "status", "evaluator", "privacy"]

# lenses (docs/METHOD.md section 7): kinds, backing grades, and what a lens filter may say
LENS_KINDS = ("perspective", "discipline", "framework")
LENS_BACKINGS = ("prompt", "source", "graph", "calibrated")   # weakest first
LENS_KEYS = {"name", "kind", "charter", "standard", "filter", "backing", "frame", "crosswalk", "signed", "order"}
LENS_STANDARD_KEYS = ("status", "grade")
LENS_FILTER_FIELDS = ("tier", "dir", "issues", "targets", "role", "chunk")   # label fields only
LENS_FILTER_WORDS = ("AND", "OR", "NOT", "IN", "CONTAINS", "CONTAINSANY", "CONTAINSALL", "CONTAINSNOT",
                     "NONE", "true", "false")
LENS_FILTER_TOKEN = re.compile(r"""\s*(?:('(?:[^'\\]|\\.)*')|("(?:[^"\\]|\\.)*")|(-?\d+(?:\.\d+)?)
                                   |(>=|<=|!=|=|>|<|\(|\)|\[|\]|,)|([A-Za-z_][A-Za-z0-9_]*)|(\S))""", re.X)


class ConfigError(Exception):
    """The config is unusable. The message lists every problem found."""


# ---- the YAML subset ---------------------------------------------------------------------------

class YamlError(ConfigError):
    pass


def parse_yaml(text):
    """A strict subset: mappings, lists, scalars, one-line [a, b] and {a: b}, # comments."""
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw.split("#")[0]:
            raise YamlError("line %d: tabs are not allowed for indentation" % number)
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        if stripped.lstrip()[0] in "&*" or stripped.lstrip().startswith("---"):
            raise YamlError("line %d: anchors, aliases and documents are not supported" % number)
        if re.search(r":\s*[|>]\s*$", stripped):
            raise YamlError("line %d: multi-line scalars are not supported" % number)
        lines.append((number, len(stripped) - len(stripped.lstrip()), stripped.strip()))
    value, index = _parse_block(lines, 0, lines[0][1] if lines else 0)
    if index != len(lines):
        raise YamlError("line %d: unexpected indentation" % lines[index][0])
    return value


def _strip_comment(line):
    out, quote = [], None
    for char in line:
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#":
            break
        out.append(char)
    return "".join(out).rstrip()


def _parse_block(lines, index, indent):
    if index >= len(lines):
        return None, index
    if lines[index][2].startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_map(lines, index, indent)


def _parse_map(lines, index, indent):
    out = {}
    while index < len(lines):
        number, level, content = lines[index]
        if level < indent:
            break
        if level > indent:
            raise YamlError("line %d: unexpected indentation" % number)
        if content.startswith("- "):
            break
        if ":" not in content:
            raise YamlError("line %d: expected 'key: value'" % number)
        key, _, rest = content.partition(":")
        key, rest = key.strip(), rest.strip()
        if key in out:
            raise YamlError("line %d: duplicate key %r" % (number, key))
        if rest:
            out[key] = _scalar(rest, number)
            index += 1
        else:
            child_indent = lines[index + 1][1] if index + 1 < len(lines) else level
            if index + 1 >= len(lines) or child_indent <= level:
                out[key] = None
                index += 1
            else:
                out[key], index = _parse_block(lines, index + 1, child_indent)
    return out, index


def _parse_list(lines, index, indent):
    out = []
    while index < len(lines):
        number, level, content = lines[index]
        if level < indent or not content.startswith("- "):
            break
        if level > indent:
            raise YamlError("line %d: unexpected indentation" % number)
        item = content[2:].strip()
        if item and ":" in item and not item.startswith(("{", "[", '"', "'")):
            key, _, rest = item.partition(":")
            entry = {key.strip(): _scalar(rest.strip(), number)} if rest.strip() else {}
            index += 1
            while index < len(lines) and lines[index][1] > level and not lines[index][2].startswith("- "):
                child, index = _parse_map(lines, index, lines[index][1])
                entry.update(child)
            out.append(entry)
            continue
        out.append(_scalar(item, number))
        index += 1
    return out, index


def _scalar(text, number):
    text = text.strip()
    if text.startswith(("[", "{")):
        return _flow(text, number)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    low = text.lower()
    if low in SCALAR_TRUE:
        return True
    if low in SCALAR_FALSE:
        return False
    if low in SCALAR_NULL:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if any(ch in text for ch in "&*|>"):
        raise YamlError("line %d: unsupported characters in scalar %r" % (number, text))
    return text


def _flow(text, number):
    """One-line [a, b] or {a: b, c: [d]}, via JSON after quoting bare words."""
    out, quote, i = [], None, 0
    while i < len(text):
        char = text[i]
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in "'\"":
            quote = '"'
            out.append('"')
            i += 1
            continue
        if char in "[]{},: ":
            out.append(char)
            i += 1
            continue
        word = re.match(r"[^,\[\]{}:]+", text[i:]).group(0)
        bare = word.strip()
        low = bare.lower()
        if low in SCALAR_TRUE:
            out.append("true")
        elif low in SCALAR_FALSE:
            out.append("false")
        elif low in SCALAR_NULL:
            out.append("null")
        elif re.fullmatch(r"-?\d+(\.\d+)?", bare):
            out.append(bare)
        else:
            out.append('"%s"' % bare)
        i += len(word)
    try:
        return json.loads("".join(out))
    except ValueError as exc:
        raise YamlError("line %d: cannot parse %r (%s)" % (number, text, exc)) from exc


# ---- the instance ------------------------------------------------------------------------------

class Instance:
    """A validated config. Read it; do not mutate it."""

    def __init__(self, data, source=None):
        self.data = data
        self.source = str(source) if source else None
        self.id = data["instance"]["id"]
        self.goal = data["instance"]["goal"]
        self.units = data["units"]
        self.fidelity = data["fidelity"]
        self.status = data["status"]
        self.vocabulary = data["vocabulary"]
        self.lenses = data.get("lenses") or []
        self.roles = data.get("roles") or []
        self.evaluator = data["evaluator"]
        self.privacy = data["privacy"]
        self.human_owned = data["human_owned"]

    # accessors used by the other modules
    def confirming_grades(self):
        return list(self.fidelity["confirming"])

    def grades(self):
        return list(self.fidelity["order"])

    def usable_statuses(self):
        return list(self.status["usable"])

    def status_values(self):
        return list(self.status["values"])

    def unit_names(self):
        return [u["name"] for u in self.units]

    def unit_type(self, name):
        for unit in self.units:
            if unit["name"] == name:
                return unit
        raise KeyError("no unit type %r in instance %s" % (name, self.id))

    def allowed_vocabulary(self):
        vocab = {k: list(v) for k, v in self.vocabulary.items()}
        vocab["grades"] = self.grades()
        vocab["statuses"] = self.status_values()
        return vocab

    def gate_rules(self):
        return list(self.data["gates"])

    def access_roles(self):
        """role name -> {read: [tables], write: [tables], lens_bound: bool}; {} when no access: section."""
        section = self.data.get("access") or {}
        return {name: {"read": list(spec.get("read") or []), "write": list(spec.get("write") or []),
                       "lens_bound": bool(spec.get("lens_bound"))}
                for name, spec in (section.get("roles") or {}).items()}

    def lens(self, name):
        for lens in self.lenses:
            if lens["name"] == name:
                return lens
        raise KeyError("no lens %r in instance %s (known: %s)"
                       % (name, self.id, ", ".join(l["name"] for l in self.lenses) or "none"))

    def lens_standard(self, lens):
        """The statuses and grades a lens accepts: its own standard, or the base sets where it names none."""
        standard = lens.get("standard") or {}
        return {"status": list(standard.get("status") or self.usable_statuses()),
                "grade": list(standard.get("grade") or [])}   # [] means every grade of a unit already usable

    def section(self, name, default=None):
        return self.data.get(name, default)


def validate(data):
    """Return the data, or raise ConfigError listing every problem."""
    problems = []
    if not isinstance(data, dict):
        raise ConfigError("config must be a mapping (got %s)" % type(data).__name__)
    unknown = sorted(set(data) - TOP_KEYS)
    if unknown:
        problems.append("unknown top-level key(s): %s; allowed: %s" % (", ".join(unknown), ", ".join(sorted(TOP_KEYS))))
    for key in REQUIRED_TOP:
        if key not in data:
            problems.append("missing required section: %s" % key)
    if problems:
        raise ConfigError("; ".join(problems))

    inst = data["instance"]
    if not isinstance(inst, dict) or not inst.get("id") or not inst.get("goal"):
        problems.append("instance needs an id and a goal")
    elif not ID_RE.match(str(inst["id"])):
        problems.append("instance.id %r must match [a-z0-9_]+" % inst["id"])

    units = data["units"]
    if not isinstance(units, list) or not units:
        problems.append("units must be a non-empty list")
    else:
        names = []
        for unit in units:
            if not isinstance(unit, dict) or not unit.get("name"):
                problems.append("each unit needs a name")
                continue
            names.append(unit["name"])
            if not ID_RE.match(str(unit["name"])):
                problems.append("unit name %r must match [a-z0-9_]+" % unit["name"])
            if not unit.get("locator_fields"):
                problems.append("unit %s needs locator_fields a program can resolve" % unit["name"])
            size = unit.get("chunk_size", 0)
            if not isinstance(size, int) or size <= 0:
                problems.append("unit %s needs a positive chunk_size" % unit["name"])
        if len(set(names)) != len(names):
            problems.append("unit names must be unique")

    fid = data["fidelity"]
    if not isinstance(fid, dict) or not fid.get("order") or not fid.get("confirming"):
        problems.append("fidelity needs order (best first) and confirming")
    else:
        missing = [g for g in fid["confirming"] if g not in fid["order"]]
        if missing:
            problems.append("fidelity.confirming not in order: %s" % ", ".join(map(str, missing)))
        if len(fid["confirming"]) >= len(fid["order"]):
            problems.append("fidelity.confirming cannot be every grade: some grade must be unable to establish")
        if not fid.get("agent_grades"):
            problems.append("fidelity needs agent_grades: the grades that are model output")
        else:
            overlap = sorted(set(fid["confirming"]) & set(fid["agent_grades"]))
            if overlap:
                problems.append("agent output may never confirm: %s is in both confirming and agent_grades"
                                % ", ".join(overlap))

    st = data["status"]
    if not isinstance(st, dict) or not st.get("values") or not st.get("usable"):
        problems.append("status needs values and usable")
    else:
        missing = [s for s in st["usable"] if s not in st["values"]]
        if missing:
            problems.append("status.usable not in values: %s" % ", ".join(map(str, missing)))
        if len(st["usable"]) >= len(st["values"]):
            problems.append("status.usable cannot be every status: 'true but unusable' must be expressible")

    vocab = data["vocabulary"]
    if not isinstance(vocab, dict):
        problems.append("vocabulary must be a mapping")
    else:
        for key in ("tiers", "directions", "issues", "roles"):
            values = vocab.get(key)
            if not values:
                problems.append("vocabulary.%s must be a non-empty list" % key)
            elif len(set(map(str, values))) != len(values):
                problems.append("vocabulary.%s has duplicates" % key)
        tiers = vocab.get("tiers") or []
        if tiers and list(tiers) != sorted(tiers):
            problems.append("vocabulary.tiers must ascend")
        if tiers and tiers[0] != 0:
            problems.append("vocabulary.tiers must start at 0 (noise)")

    gates = data["gates"]
    if not isinstance(gates, list) or not gates:
        problems.append("gates must be a non-empty list: a graph with no gate is not gated")
    else:
        ids = []
        for rule in gates:
            if not isinstance(rule, dict) or not rule.get("id"):
                problems.append("each gate rule needs an id")
                continue
            ids.append(rule["id"])
            for key in ("table", "field", "value"):
                if key not in rule:
                    problems.append("gate %s needs %s" % (rule["id"], key))
            if not rule.get("requires") and not rule.get("forbids"):
                problems.append("gate %s requires or forbids nothing" % rule["id"])
        if len(set(ids)) != len(ids):
            problems.append("gate ids must be unique")

    owned = data["human_owned"] or []
    missing = [k for k in MUST_BE_HUMAN_OWNED if k not in owned]
    if missing:
        problems.append("human_owned must include %s (CLAUDE.md rule 6)" % ", ".join(missing))

    if "access" in data:   # optional: per-agent datastore roles (xscholar/access.py)
        acc = data["access"]
        roles = acc.get("roles") if isinstance(acc, dict) else None
        if not isinstance(roles, dict) or not roles:
            problems.append("access needs roles: a mapping of role name -> {read: [...], write: [...], lens_bound}")
        else:
            from xscholar import access as _access   # lazy: access imports config
            for name, spec in roles.items():
                if not ID_RE.match(str(name)):
                    problems.append("access role %r must match [a-z0-9_]+" % name)
                if not isinstance(spec, dict) or not isinstance(spec.get("read"), list) or not spec["read"]:
                    problems.append("access role %s needs read: a non-empty list of tables" % name)
                    continue
                if "write" in spec and not isinstance(spec["write"], list):
                    problems.append("access role %s: write must be a list of tables" % name)
                    continue
                unknown = sorted(set(map(str, spec["read"] + (spec.get("write") or []))) - set(_access.TABLES))
                if unknown:
                    problems.append("access role %s names unknown table(s) %s; known: %s"
                                    % (name, ", ".join(unknown), ", ".join(_access.TABLES)))
                if "label" in (spec.get("write") or []) and name != "loader":
                    problems.append("access role %s may not write label: only the loader writes labels" % name)
                if spec.get("lens_bound") not in (None, True, False):
                    problems.append("access role %s: lens_bound must be true or false" % name)

    ev = data["evaluator"]
    if not isinstance(ev, dict) or not ev.get("regression_file"):
        problems.append("evaluator needs a regression_file: the errors this project has actually made")

    _validate_lenses(data, problems)

    if problems:
        raise ConfigError("config invalid:\n  - " + "\n  - ".join(problems))
    return data


def check_lens_filter(text):
    """Return None if `text` is a filter over label fields, else the problem. No paths, no $params."""
    if not isinstance(text, str) or not text.strip():
        return "filter must be a non-empty string"
    pos = 0
    while pos < len(text):
        m = LENS_FILTER_TOKEN.match(text, pos)
        if not m or m.end() == pos:
            return "filter %r: cannot read %r" % (text, text[pos:pos + 10])
        word, stray = m.group(5), m.group(6)
        if stray:
            return "filter %r: character %r is not allowed" % (text, stray)
        if word and word not in LENS_FILTER_FIELDS and word.upper() not in LENS_FILTER_WORDS \
                and word not in ("true", "false"):
            return ("filter %r: %r is not a label field (%s) or an operator; unit fields belong in the standard"
                    % (text, word, ", ".join(LENS_FILTER_FIELDS)))
        pos = m.end()
    return None


def _validate_lenses(data, problems):
    """Every lens: name, kind, charter, backing; a standard never looser than the base; a filter over
    label fields; a framework lens with a sourced frame and a crosswalk from frame nodes to issues."""
    lenses = data.get("lenses")
    if lenses is None:
        return
    if not isinstance(lenses, list):
        problems.append("lenses must be a list")
        return
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    fid = data.get("fidelity") if isinstance(data.get("fidelity"), dict) else {}
    vocab = data.get("vocabulary") if isinstance(data.get("vocabulary"), dict) else {}
    issues = list(vocab.get("issues") or [])
    names = []
    for lens in lenses:
        if not isinstance(lens, dict) or not lens.get("name"):
            problems.append("each lens needs a name")
            continue
        name = str(lens["name"])
        names.append(name)
        where = "lens %s" % name
        if not ID_RE.match(name):
            problems.append("%s: name must match [a-z0-9_]+" % where)
        unknown = sorted(set(lens) - LENS_KEYS)
        if unknown:
            problems.append("%s: unknown key(s) %s; allowed: %s" % (where, ", ".join(unknown), ", ".join(sorted(LENS_KEYS))))
        kind = lens.get("kind")
        if kind not in LENS_KINDS:
            problems.append("%s: kind must be one of %s (got %r)" % (where, ", ".join(LENS_KINDS), kind))
        if not isinstance(lens.get("charter"), str) or not lens["charter"].strip():
            problems.append("%s: charter is required (who looks, what they want, what hurts them; human-signed)" % where)
        backing = lens.get("backing")
        if backing is not None and backing not in LENS_BACKINGS:
            problems.append("%s: backing must be one of %s (got %r)" % (where, ", ".join(LENS_BACKINGS), backing))
        signed = lens.get("signed")
        if signed is not None and not isinstance(signed, bool):
            problems.append("%s: signed must be true or false (a human signed this charter, or did not)" % where)
        # A charter nobody has signed is a draft: the lens cannot claim to be better than prompt-backed.
        if signed is not True and backing in ("source", "graph", "calibrated"):
            problems.append("%s: backing %r claims more than prompt-backed, but signed is not true; "
                            "sign the charter or set backing: prompt" % (where, backing))
        if lens.get("order") is not None and not isinstance(lens["order"], str):
            problems.append("%s: order must be a string (an ORDER BY over label fields)" % where)
        # standard: stricter than the base gates, never looser
        standard = lens.get("standard")
        if standard is not None:
            if not isinstance(standard, dict):
                problems.append("%s: standard must be a mapping of status and/or grade lists" % where)
            else:
                bad = sorted(set(standard) - set(LENS_STANDARD_KEYS))
                if bad:
                    problems.append("%s: standard may only name %s (got %s)" % (where, ", ".join(LENS_STANDARD_KEYS), ", ".join(bad)))
                for key, exists, base, base_name in (("status", status.get("values") or [], status.get("usable") or [], "status.usable"),
                                                     ("grade", fid.get("order") or [], fid.get("confirming") or [], "fidelity.confirming")):
                    values = standard.get(key)
                    if values is None:
                        continue
                    if not isinstance(values, list) or not values:
                        problems.append("%s: standard.%s must be a non-empty list" % (where, key))
                        continue
                    missing = [v for v in values if v not in exists]
                    if missing:
                        problems.append("%s: standard.%s names %s, which does not exist" % (where, key, ", ".join(map(str, missing))))
                    looser = [v for v in values if v in exists and v not in base]
                    if looser:
                        problems.append("%s: standard.%s admits %s, outside the base %s: a lens may be stricter, never looser"
                                        % (where, key, ", ".join(map(str, looser)), base_name))
        if lens.get("filter") is not None:
            problem = check_lens_filter(lens["filter"])
            if problem:
                problems.append("%s: %s" % (where, problem))
        # frame and crosswalk: required for a framework lens, checked whenever present
        frame, crosswalk = lens.get("frame"), lens.get("crosswalk")
        if kind == "framework" and (frame is None or crosswalk is None):
            problems.append("%s: a framework lens needs frame (source, nodes) and crosswalk (node -> issues)" % where)
        nodes = []
        if frame is not None:
            if not isinstance(frame, dict) or not isinstance(frame.get("source"), str) or not frame["source"].strip():
                problems.append("%s: frame.source must say where the frame was transcribed from" % where)
            raw_nodes = frame.get("nodes") if isinstance(frame, dict) else None
            if not isinstance(raw_nodes, list) or not raw_nodes:
                problems.append("%s: frame.nodes must be a non-empty list of node ids" % where)
            else:
                nodes = [str(n) for n in raw_nodes]
                if len(set(nodes)) != len(nodes):
                    problems.append("%s: frame.nodes must be unique" % where)
                bad = [n for n in nodes if not ID_RE.match(n)]
                if bad:
                    problems.append("%s: frame node id(s) %s must match [a-z0-9_]+" % (where, ", ".join(bad)))
        if crosswalk is not None:
            if frame is None:
                problems.append("%s: a crosswalk needs a frame to file under" % where)
            if not isinstance(crosswalk, dict):
                problems.append("%s: crosswalk must map frame node -> list of issues" % where)
            else:
                not_nodes = sorted(set(map(str, crosswalk)) - set(nodes))
                if not_nodes and nodes:
                    problems.append("%s: crosswalk key(s) %s are not frame nodes" % (where, ", ".join(not_nodes)))
                uncovered = [n for n in nodes if n not in crosswalk]
                if uncovered:
                    problems.append("%s: crosswalk must name every frame node (missing %s; use [] for none)"
                                    % (where, ", ".join(uncovered)))
                for node, values in crosswalk.items():
                    if not isinstance(values, list):
                        problems.append("%s: crosswalk.%s must be a list of issues" % (where, node))
                        continue
                    bad = [v for v in values if v not in issues]
                    if bad:
                        problems.append("%s: crosswalk.%s names %s, not in vocabulary.issues" % (where, node, ", ".join(map(str, bad))))
    if len(set(names)) != len(names):
        problems.append("lens names must be unique")


def load(source):
    """Instance from a path (.yaml/.yml/.json) or an already-parsed mapping."""
    if isinstance(source, Instance):
        return source
    if isinstance(source, dict):
        return Instance(validate(source))
    path = pathlib.Path(source)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError("cannot read config %s: %s" % (path, exc)) from exc
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ConfigError("%s is not valid JSON: %s" % (path, exc)) from exc
    else:
        data = parse_yaml(text)
    return Instance(validate(data), source=path)


if __name__ == "__main__":
    import sys
    inst = load(sys.argv[1])
    print("OK %s: %d unit type(s), %d gate rule(s), confirming grades %s"
          % (inst.id, len(inst.units), len(inst.gate_rules()), ", ".join(inst.confirming_grades())))
