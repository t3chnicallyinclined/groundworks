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
            "lenses", "roles", "evaluator", "human_owned", "privacy", "panel", "present", "embed"}
REQUIRED_TOP = ["instance", "units", "fidelity", "status", "vocabulary", "gates",
                "evaluator", "human_owned", "privacy"]
MUST_BE_HUMAN_OWNED = ["gates", "fidelity", "status", "evaluator", "privacy"]


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

    ev = data["evaluator"]
    if not isinstance(ev, dict) or not ev.get("regression_file"):
        problems.append("evaluator needs a regression_file: the errors this project has actually made")

    if problems:
        raise ConfigError("config invalid:\n  - " + "\n  - ".join(problems))
    return data


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
