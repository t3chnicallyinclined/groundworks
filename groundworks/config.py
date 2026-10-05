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
            "authority", "staleness", "discovery", "tables", "edges", "verdicts", "unique",
            "assertions", "promotion_log", "labels"}
REQUIRED_TOP = ["instance", "units", "fidelity", "status", "vocabulary", "gates",
                "evaluator", "human_owned", "privacy"]
MUST_BE_HUMAN_OWNED = ["gates", "fidelity", "status", "evaluator", "privacy"]
# optional sections that, WHEN PRESENT, are a bar the owner sets and no role may turn
HUMAN_OWNED_IF_PRESENT = ["verdicts", "unique", "assertions", "promotion_log"]

# ---- the graph shape ---------------------------------------------------------------------------
#
# The node tables and edge types the engine ships with. An instance adds its own under `tables:` and
# `edges:`; everything downstream (schema, gates, probes, PERMISSIONS, cleanup) reads the combined
# maps below rather than these constants, so a new domain needs no engine edit.
BUILT_IN_NODE_TABLES = ("unit", "label", "fact", "conclusion", "authority", "critique", "answer")
BUILT_IN_EDGES = {
    "cites": ("fact", "unit"),
    "supports": ("fact", "conclusion"),
    "undercuts": ("fact", "conclusion"),
    "attacks": ("critique", "conclusion"),
    "answers": ("answer", "critique"),
    "bears_on": ("label", "conclusion"),   # a label bears on a target; targets are conclusion rows
    "relies_on": ("conclusion", "authority"),
}
# Tables the engine owns for its own purposes; an instance may not take these names either.
RESERVED_TABLES = ("agent",)              # groundworks/access.py AGENT_TABLE

# The fields the engine's schema gives each built-in node table (groundworks/gates.py schema_surql), by
# type. Kept here so validation can refuse a `unique:` declaration on a field a table does not have
# without needing an Instance; tests/test_gates.py checks it against the emitted schema so the two
# cannot drift.
BUILT_IN_FIELDS = {
    "unit": {"type": "string", "text": "string", "locator": "object", "status": "string", "grade": "string",
             "hash": "string", "meta": "object"},
    "label": {"unit": "record", "tier": "int", "dir": "string", "issues": "array", "targets": "array",
              "role": "string", "reason": "string", "quote": "string", "grade": "string", "chunk": "string",
              "run": "string"},
    "fact": {"statement": "string", "status": "string", "note": "string", "tried": "string",
             "evidence": "string", "reasoning_type": "string", "author": "string", "lens": "string",
             "learned_at": "datetime"},
    "conclusion": {"statement": "string", "ready": "bool", "rating": "string", "author": "string"},
    "authority": {"citation": "string", "verified": "bool", "source_grade": "string"},
    "critique": {"lens": "string", "head": "string", "verdict": "string", "text": "string", "author": "string",
                 "answered": "bool"},
    "answer": {"text": "string", "author": "string"},
}

# ---- originals (groundworks/originals.py, groundworks/store.py) -- one self-contained block -------------------
# Every unit links to one original document, kept byte-exact in the instance's content-addressed store.
# `document` is a built-in node table (its schema comes from originals.schema_lines() via gates.schema_surql);
# `unit.document` is the link. `originals:` (mode, store, spans) is human-owned when present: whether units
# must carry an original is a bar the owner sets.
TOP_KEYS = TOP_KEYS | {"originals"}
HUMAN_OWNED_IF_PRESENT = HUMAN_OWNED_IF_PRESENT + ["originals"]
# `labels:` (pipeline/validator.py): how much a label's quote must say. A bar the owner sets.
HUMAN_OWNED_IF_PRESENT = HUMAN_OWNED_IF_PRESENT + ["labels"]
BUILT_IN_NODE_TABLES = BUILT_IN_NODE_TABLES + ("document",)
BUILT_IN_FIELDS["unit"]["document"] = "record"
BUILT_IN_FIELDS["document"] = {"sha256": "string", "media_type": "string", "bytes": "int", "capture": "string",
                               "source_uri": "string", "retrieved_at": "datetime", "original_name": "string",
                               "integrity": "string", "verified_at": "datetime"}


def _validate_originals(data, problems):
    """`originals: required | transition` or `{mode, store, spans}` (groundworks/originals.py holds the checks)."""
    if "originals" not in data:
        return
    from groundworks import originals
    originals.config_problems(data, problems)
# ---- end originals block --------------------------------------------------------------------------------

# Independent promotion (a gate rule with `independent: true`). The datastore -- never the caller --
# records on this field every signed-in credential (`$auth`) that has written the row, and refuses the
# gated change from a credential already on it, from root, and on a row no credential has written.
# Engine-owned: a declared table may not declare a field of this name.
INDEPENDENCE_FIELD = "written_by"

# How independent the promoter must be (`independent:` on a gate rule). `true` is the original rule:
# a credential that has not written the row. The mapping form tightens it along two axes:
#   between: principal   the promoter's declared principal (a person or an independent process, bound
#                        to the credential when it is provisioned) must differ from every writer's
#   own_evidence: true   the promoter must itself have CREATED at least one of the evidence links the
#                        rule's `requires` clauses count
# Engine-owned tables that record who wrote what, and the promotion log (`promotion_log: true`). An
# instance may not declare a table or an edge of these names.
INDEPENDENCE_BETWEEN = ("credential", "principal")
INDEPENDENCE_KEYS = {"between", "own_evidence"}
AUDIT_TABLES = ("row_writer", "link_creator", "promotion_log", "promotion_head")


def independence_of(rule):
    """{"between": "credential"|"principal", "own_evidence": bool} for a rule that requires an independent
    promoter, or None. Every reader goes through this, so `independent: true` and the mapping form can
    never be read two different ways (a mapping read as "not `is True`" would switch the rule off)."""
    value = (rule or {}).get("independent", False)
    if value is True:
        return {"between": "credential", "own_evidence": False}
    if isinstance(value, dict) and value:
        return {"between": value.get("between", "credential"), "own_evidence": value.get("own_evidence") is True}
    return None

FIELD_TYPES = ("string", "int", "float", "bool", "datetime")
TABLE_KEYS = {"fields", "doc"}
EDGE_KEYS = {"from", "to", "fields", "doc"}
FIELD_KEYS = {"type", "one_of", "optional", "doc"}
EDGE_RESERVED_FIELDS = ("id", "in", "out")   # SurrealDB owns these on a RELATION table


def _field_spec(value):
    """A declared field, from `name: string` or `name: {type: string, one_of: [...]}`.

    Returns (spec, problem). The spec is always a dict with type/one_of/optional so that every
    reader sees the same shape and nothing downstream has to guess what a bare string meant."""
    if isinstance(value, str):
        value = {"type": value}
    if not isinstance(value, dict):
        return None, "must be a type name or a mapping with a type"
    unknown = sorted(set(value) - FIELD_KEYS)
    if unknown:
        return None, "unknown key(s) %s; allowed: %s" % (", ".join(unknown), ", ".join(sorted(FIELD_KEYS)))
    kind = value.get("type")
    if kind not in FIELD_TYPES:
        return None, "type must be one of %s (got %r)" % (", ".join(FIELD_TYPES), kind)
    one_of = value.get("one_of")
    if one_of is not None:
        if not isinstance(one_of, list) or not one_of:
            return None, "one_of must be a non-empty list of values"
        if len(set(map(str, one_of))) != len(one_of):
            return None, "one_of has duplicates"
    optional = value.get("optional", False)
    if optional not in (True, False):
        return None, "optional must be true or false"
    return {"type": kind, "one_of": list(one_of) if one_of else None, "optional": bool(optional)}, None


def _declared(data, section):
    """The raw `tables:`/`edges:` mapping, or {} when the instance declares none."""
    value = (data or {}).get(section)
    return value if isinstance(value, dict) else {}


def declared_tables_of(data):
    """name -> {field: spec} for every node table the instance declares. Unvalidated input is skipped;
    validate() is what reports it, so this never raises for a caller holding a validated config."""
    out = {}
    for name, body in _declared(data, "tables").items():
        fields = {}
        for field, value in ((body or {}).get("fields") or {}).items():
            spec, _ = _field_spec(value)
            if spec:
                fields[str(field)] = spec
        out[str(name)] = fields
    return out


def declared_edges_of(data):
    """name -> {"from": table, "to": table, "fields": {field: spec}} for every declared edge type."""
    out = {}
    for name, body in _declared(data, "edges").items():
        body = body or {}
        fields = {}
        for field, value in (body.get("fields") or {}).items():
            spec, _ = _field_spec(value)
            if spec:
                fields[str(field)] = spec
        out[str(name)] = {"from": body.get("from"), "to": body.get("to"), "fields": fields}
    return out


def node_tables_of(data):
    """Every node table this instance has: the built-ins first, then the ones it declared."""
    return list(BUILT_IN_NODE_TABLES) + [n for n in declared_tables_of(data) if n not in BUILT_IN_NODE_TABLES]


def edge_map_of(data):
    """edge name -> (source table, target table), built-ins plus the instance's own."""
    out = dict(BUILT_IN_EDGES)
    for name, body in declared_edges_of(data).items():
        if name not in BUILT_IN_EDGES and body["from"] and body["to"]:
            out[name] = (str(body["from"]), str(body["to"]))
    return out

# lenses (docs/METHOD.md section 7): kinds, backing grades, and what a lens filter may say
LENS_KINDS = ("perspective", "discipline", "framework")
LENS_BACKINGS = ("prompt", "source", "graph", "calibrated")   # weakest first
LENS_KEYS = {"name", "kind", "charter", "standard", "filter", "backing", "frame", "crosswalk", "signed", "order"}
LENS_STANDARD_KEYS = ("status", "grade")
LENS_FILTER_FIELDS = ("tier", "dir", "issues", "targets", "role", "chunk", "run")   # label fields only
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

    def verdict_meanings(self):
        """What each critique verdict MEANS here, in the owner's words. Empty when undefined.

        The verdict names are an engine constant; what separates one from another is a judgement
        about this instance's risk, and it belongs to the owner. Measured on the first real instance:
        of 53 `fatal` verdicts, 32 came from one role mapping its own three options onto the three
        verdicts by position -- a reviewer saying "not on this draft" was recorded as permanently
        terminal. A role handed three buttons with no labels presses the top one.
        """
        return dict(self.data.get("verdicts") or {})

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

    # the graph shape: built-ins plus whatever this instance declared
    def declared_tables(self):
        return declared_tables_of(self.data)

    def declared_edges(self):
        return declared_edges_of(self.data)

    def node_tables(self):
        return node_tables_of(self.data)

    def edge_map(self):
        return edge_map_of(self.data)

    def tables(self):
        """Every table the schema generator emits: node tables first, then edge tables."""
        return self.node_tables() + list(self.edge_map())

    def table_fields(self, table):
        """The declared fields of a declared node table; {} for a built-in (its fields are the engine's)."""
        return dict(self.declared_tables().get(table) or {})

    def edge_fields(self, edge):
        """The declared fields of a declared edge type; {} for a built-in (they carry none)."""
        return dict((self.declared_edges().get(edge) or {}).get("fields") or {})

    def unique_fields(self):
        """table -> [field, ...] for every `unique:` declaration; {} when the instance declares none."""
        return {str(table): [str(f) for f in fields] for table, fields in (self.data.get("unique") or {}).items()}

    def independent_rules(self):
        """The gate rules whose change only a credential that has not written the row may make."""
        return [rule for rule in self.gate_rules() if independence_of(rule)]

    def promotion_log_enabled(self):
        """True when every gated promotion is written to the hash-chained promotion log."""
        return self.data.get("promotion_log") is True

    def principal_rules(self):
        """Independent rules whose promoter must be a different declared principal, not only a different credential."""
        return [rule for rule in self.independent_rules() if independence_of(rule)["between"] == "principal"]

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

    _validate_graph_shape(data, problems)

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
            independent = _independence_value(rule, problems)
            if not rule.get("requires") and not rule.get("forbids") and not independent:
                problems.append("gate %s requires or forbids nothing" % rule["id"])
            _validate_gate_domains(data, rule, problems)
            if independent:
                _validate_independent(data, rule, problems)
                _validate_independence_mode(data, rule, independent, problems)
        if len(set(ids)) != len(ids):
            problems.append("gate ids must be unique")

    if "verdicts" in data:
        meanings = data["verdicts"]
        if not isinstance(meanings, dict) or not meanings:
            problems.append("verdicts must be a mapping of verdict name -> what it means here")
        else:
            from groundworks.gates import CRITIQUE_VERDICTS
            unknown = sorted(set(meanings) - set(CRITIQUE_VERDICTS))
            if unknown:
                problems.append("verdicts: unknown verdict(s) %s; the engine's are %s"
                                % (", ".join(unknown), ", ".join(CRITIQUE_VERDICTS)))
            undefined = [v for v in CRITIQUE_VERDICTS if v not in meanings]
            if undefined:
                problems.append("verdicts: %s left undefined. Define every verdict or none: a partly "
                                "defined set is the case where a role reaches for the one with no "
                                "meaning attached" % ", ".join(undefined))
            for name, text in meanings.items():
                if not isinstance(text, str) or len(text.strip()) < 10:
                    problems.append("verdicts.%s: say in a sentence what it means here; %r is not a "
                                    "definition a role can act on" % (name, text))

    owned = data["human_owned"] or []
    missing = [k for k in MUST_BE_HUMAN_OWNED if k not in owned]
    missing += [k for k in HUMAN_OWNED_IF_PRESENT if k in data and k not in owned]
    if missing:
        problems.append("human_owned must include %s (CLAUDE.md rule 6)" % ", ".join(missing))

    if "access" in data:   # optional: per-agent datastore roles (groundworks/access.py)
        acc = data["access"]
        roles = acc.get("roles") if isinstance(acc, dict) else None
        if not isinstance(roles, dict) or not roles:
            problems.append("access needs roles: a mapping of role name -> {read: [...], write: [...], lens_bound}")
        else:
            known_tables = node_tables_of(data) + list(edge_map_of(data))
            for name, spec in roles.items():
                if not ID_RE.match(str(name)):
                    problems.append("access role %r must match [a-z0-9_]+" % name)
                if not isinstance(spec, dict) or not isinstance(spec.get("read"), list) or not spec["read"]:
                    problems.append("access role %s needs read: a non-empty list of tables" % name)
                    continue
                if "write" in spec and not isinstance(spec["write"], list):
                    problems.append("access role %s: write must be a list of tables" % name)
                    continue
                unknown = sorted(set(map(str, spec["read"] + (spec.get("write") or []))) - set(known_tables))
                if unknown:
                    problems.append("access role %s names unknown table(s) %s; known: %s"
                                    % (name, ", ".join(unknown), ", ".join(known_tables)))
                if "label" in (spec.get("write") or []) and name != "loader":
                    problems.append("access role %s may not write label: only the loader writes labels" % name)
                if spec.get("lens_bound") not in (None, True, False):
                    problems.append("access role %s: lens_bound must be true or false" % name)

    _validate_unique(data, problems)
    _validate_assertions(data, problems)
    _validate_audit_tables(data, problems)
    _validate_originals(data, problems)
    _validate_labels(data, problems)

    ev = data["evaluator"]
    if not isinstance(ev, dict) or not ev.get("regression_file"):
        problems.append("evaluator needs a regression_file: the errors this project has actually made")

    _validate_lenses(data, problems)

    if problems:
        raise ConfigError("config invalid:\n  - " + "\n  - ".join(problems))
    return data


def _whole_number(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _validate_labels(data, problems):
    """`labels: {quote_min_words, quote_distinct_units}` -- how much a label's quote must say (pipeline/validator.py)."""
    if "labels" not in data:
        return
    section = data["labels"]
    if not isinstance(section, dict):
        problems.append("labels must be a mapping: {quote_min_words: <int>, quote_distinct_units: {<unit type>: <int>}}")
        return
    unknown = sorted(set(section) - {"quote_min_words", "quote_distinct_units"})
    if unknown:
        problems.append("labels: unknown key(s) %s; allowed: quote_min_words, quote_distinct_units" % ", ".join(unknown))
    if "quote_min_words" in section and not _whole_number(section["quote_min_words"]):
        problems.append("labels.quote_min_words must be a whole number of at least 1, not %r" % section["quote_min_words"])
    distinct = section.get("quote_distinct_units")
    if distinct is not None:
        if not isinstance(distinct, dict):
            problems.append("labels.quote_distinct_units must map a unit type to a whole number")
        else:
            types = [u.get("name") for u in data.get("units") or [] if isinstance(u, dict)]
            for name, limit in distinct.items():
                if name not in types:
                    problems.append("labels.quote_distinct_units names unit type %r; known: %s"
                                    % (name, ", ".join(map(str, types))))
                if not _whole_number(limit):
                    problems.append("labels.quote_distinct_units.%s must be a whole number of at least 1, not %r"
                                    % (name, limit))


def _validate_independent(data, rule, problems):
    """A rule that requires an independent promoter needs identities the datastore can tell apart.

    Root has none (`$auth` is NONE for a system user, measured on 3.1.6), so an instance with no
    `access:` section could never make the change at all, and neither could one where no role may both
    read and write the table. Both are refused here, by name, rather than shipping a gate that refuses
    everything or one no probe could ever exercise."""
    where = "gate %s" % rule["id"]
    table = str(rule.get("table"))
    if table not in node_tables_of(data):
        problems.append("%s: independent promotion is recorded on a node table's rows; %r is not one"
                        % (where, table))
        return
    if INDEPENDENCE_FIELD in (declared_tables_of(data).get(table) or {}):
        problems.append("%s: tables.%s declares a field %r, which the engine owns for independent promotion"
                        % (where, table, INDEPENDENCE_FIELD))
    access = data.get("access") if isinstance(data.get("access"), dict) else {}
    roles = access.get("roles") if isinstance(access.get("roles"), dict) else {}
    if not roles:
        problems.append("%s: independent: true needs an access: section. Without record credentials every "
                        "write comes from root, which has no identity to tell apart, so nothing could ever "
                        "make this change" % where)
        return
    writers = [name for name, spec in roles.items() if isinstance(spec, dict)
               and table in (spec.get("write") or []) and table in (spec.get("read") or [])]
    if not writers:
        problems.append("%s: independent: true, but no access role both reads and writes %s, so no "
                        "credential could propose or promote it" % (where, table))


def expand_where(data, where):
    """A clause's `where` with $confirming and $usable replaced by the config's own lists. The one expansion:
    gates._expand calls this, so what validation reads and what the datastore enforces cannot differ."""
    def lit(values):
        return "[" + ", ".join("'%s'" % str(v).replace("'", "\\'") for v in values) + "]"
    fidelity = data.get("fidelity") if isinstance(data.get("fidelity"), dict) else {}
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    return (where or "").replace("$confirming", lit(fidelity.get("confirming") or [])) \
                        .replace("$usable", lit(status.get("usable") or []))


def _validate_gate_domains(data, rule, problems):
    """Every value a gate reads must come from an enumerated domain, or a misspelling slips past it.

    Measured on a real instance (ROADMAP B-014): a status field that decided whether each of 21,332 records was
    in evidence had no list of allowed values. A misspelt value (a transposed pair of letters) would have been
    stored without a word, and the record would have dropped out of evidence: a typo never equals the value a
    rule compares with. A gate on such a field is close to meaningless, so it is refused here, by name, and so is
    a clause `where` that compares a field with no domain. That half is worse for `forbids`: a forbidden row
    whose value is misspelt is not counted, and the gate lets the promotion through.

    A domain is a vocabulary list the engine enforces (unit.type/status/grade, fact.status, critique.verdict,
    label.tier/dir/role), a declared field's `one_of`, or a boolean. A value outside the domain is refused too:
    a rule on `verified = ture` would never fire. Only `field = literal` and `field IN [literals]` terms are read;
    anything else in a `where` is left to the probes, which report it untested by name."""
    from groundworks import assertions, gates
    if not all(key in rule for key in ("table", "field", "value")):
        return
    where = "gate %s" % rule["id"]
    table, field = str(rule["table"]), str(rule["field"])
    domain = assertions.field_domain(data, table, field)
    if domain is None:
        problems.append("%s: %s.%s has no enumerated domain (no vocabulary list, no one_of, not a bool), so the "
                        "datastore stores a misspelt value without a word and this gate never sees it; declare the "
                        "field with one_of, or gate a field that has one" % (where, table, field))
    elif gates._value_literal(rule["value"]) not in [gates._value_literal(v) for v in domain]:
        problems.append("%s: value %r is not one of %s.%s's values (%s), so the gate could never fire"
                        % (where, rule["value"], table, field, ", ".join(map(str, domain))))
    edges = edge_map_of(data)
    for kind in ("requires", "forbids"):
        for clause in rule.get(kind) or []:
            if not isinstance(clause, dict) or not clause.get("where") or clause.get("edge") not in edges:
                continue
            src, dst = edges[clause["edge"]]
            other = dst if table == src else src if table == dst else None
            terms, _ = gates._where_terms(expand_where(data, clause["where"]))
            if other is None or terms is None:
                continue
            for term_field, literals in terms:
                values = assertions.field_domain(data, other, term_field)
                said = "%s: a %s clause on %s compares %s.%s" % (where, kind, clause["edge"], other, term_field)
                if values is None:
                    problems.append("%s, which has no enumerated domain, so a misspelt value there is never counted%s; "
                                    "declare the field with one_of" % (said, " and the forbidden row is let through"
                                                                       if kind == "forbids" else ""))
                    continue
                allowed = [gates._value_literal(v) for v in values]
                for literal in literals:
                    text = literal.strip()
                    if text[:1] == '"' and text[-1:] == '"':
                        text = gates._value_literal(text[1:-1])
                    elif text.lower() in ("true", "false"):
                        text = text.lower()
                    if text not in allowed:
                        problems.append("%s with %s, which is not one of its values (%s), so the clause never matches"
                                        % (said, literal, ", ".join(map(str, values))))


def _independence_value(rule, problems):
    """The rule's `independent:` read through independence_of(), with anything else refused by name."""
    where = "gate %s" % rule["id"]
    value = rule.get("independent", False)
    if value is True or value is False:
        return independence_of(rule)
    if not isinstance(value, dict) or not value:
        problems.append("%s: independent must be true or false, or a mapping with between and/or own_evidence "
                        "(got %r)" % (where, value))
        return None
    unknown = sorted(set(map(str, value)) - INDEPENDENCE_KEYS)
    if unknown:
        problems.append("%s: independent has unknown key(s) %s; allowed: %s"
                        % (where, ", ".join(unknown), ", ".join(sorted(INDEPENDENCE_KEYS))))
    if value.get("between", "credential") not in INDEPENDENCE_BETWEEN:
        problems.append("%s: independent.between must be one of %s (got %r)"
                        % (where, ", ".join(INDEPENDENCE_BETWEEN), value.get("between")))
    if not isinstance(value.get("own_evidence", False), bool):
        problems.append("%s: independent.own_evidence must be true or false (got %r)" % (where, value.get("own_evidence")))
    return independence_of(rule)


def _validate_independence_mode(data, rule, mode, problems):
    """What the mapping form of `independent:` needs beyond what `true` needs.

    own_evidence: the rule must count evidence links (an independence-only rule has none to contribute),
    and some role must be able to write the row's table AND one of those links -- otherwise no single
    credential could ever both be independent and have created its own evidence, and the rule would
    refuse every promotion."""
    if not mode["own_evidence"]:
        return
    where = "gate %s" % rule["id"]
    edges = [str(c.get("edge")) for c in (rule.get("requires") or []) if isinstance(c, dict)]
    if not edges:
        problems.append("%s: independent.own_evidence needs requires clauses: the promoter's own evidence is "
                        "one of the links they count, and this rule counts none" % where)
        return
    table = str(rule.get("table"))
    access = data.get("access") if isinstance(data.get("access"), dict) else {}
    roles = access.get("roles") if isinstance(access.get("roles"), dict) else {}
    able = [name for name, spec in roles.items() if isinstance(spec, dict)
            and table in (spec.get("write") or []) and table in (spec.get("read") or [])
            and set(edges) & set(spec.get("write") or [])]
    if roles and not able:
        problems.append("%s: independent.own_evidence, but no access role both reads and writes %s and writes "
                        "one of its evidence edges (%s), so no credential could promote it"
                        % (where, table, ", ".join(sorted(set(edges)))))


def _validate_audit_tables(data, problems):
    """`promotion_log:` and the engine-owned audit table names."""
    for section in ("tables", "edges"):
        for name in _declared(data, section):
            if str(name) in AUDIT_TABLES:
                problems.append("%s.%s: %r is owned by the engine (who wrote what, and the promotion log); "
                                "choose another name" % (section, name, str(name)))
    if "promotion_log" not in data:
        return
    if data["promotion_log"] not in (True, False) or not isinstance(data["promotion_log"], bool):
        problems.append("promotion_log must be true or false (got %r)" % (data["promotion_log"],))
        return
    if data["promotion_log"] is True:
        declared = declared_tables_of(data)
        for rule in data.get("gates") or []:
            if isinstance(rule, dict) and INDEPENDENCE_FIELD in (declared.get(str(rule.get("table"))) or {}):
                problems.append("promotion_log: tables.%s declares a field %r, which the engine owns for "
                                "recording who wrote a gated row" % (rule.get("table"), INDEPENDENCE_FIELD))


def _validate_unique(data, problems):
    """`unique: {table: [field, ...]}` -- one unique index per field, on a built-in or declared node table.

    A field the table does not have is refused by name: an index on a field no row carries would be
    accepted by the datastore and would enforce nothing."""
    if "unique" not in data:
        return
    section = data["unique"]
    if not isinstance(section, dict) or not section:
        problems.append("unique must be a mapping of table -> [field, ...]")
        return
    declared = declared_tables_of(data)
    edges = edge_map_of(data)
    for table, fields in section.items():
        table = str(table)
        where = "unique.%s" % table
        if table in edges:
            problems.append("%s: %r is an edge type; unique applies to node tables" % (where, table))
            continue
        if table in BUILT_IN_FIELDS:
            known = dict(BUILT_IN_FIELDS[table])
        elif table in declared:
            known = {name: spec["type"] for name, spec in declared[table].items()}
        else:
            problems.append("%s: unknown table %r; known node tables: %s"
                            % (where, table, ", ".join(node_tables_of(data))))
            continue
        if not isinstance(fields, list) or not fields:
            problems.append("%s: must be a non-empty list of field names" % where)
            continue
        if len(set(map(str, fields))) != len(fields):
            problems.append("%s: lists a field twice" % where)
        for field in fields:
            if str(field) not in known:
                problems.append("%s: %r is not a field of %s; its fields are %s"
                                % (where, field, table, ", ".join(sorted(known))))


def _validate_assertions(data, problems):
    """`assertions: {edge: {fields, promoted_by, enforce}}` -- structured claims one row makes about another
    row's field (groundworks/assertions.py holds the contract and its checks). Imported here, not at the top:
    that module reads this one's graph-shape helpers."""
    if "assertions" not in data:
        return
    from groundworks import assertions
    assertions.validate(data, problems)


def _validate_graph_shape(data, problems):
    """`tables:` and `edges:` -- the node tables and edge types this instance adds to the built-ins.

    Both are optional; an instance that declares neither is exactly what it was before. A declared
    name that collides with a built-in is refused by name, because the schema generator would
    OVERWRITE the engine's own table and every gate written against it would then mean something
    else. An edge whose ends are not tables is refused naming the edge and the missing table, because
    the traversal it generates would silently reach nothing."""
    tables, edges = data.get("tables"), data.get("edges")
    for section, value in (("tables", tables), ("edges", edges)):
        if value is not None and not isinstance(value, dict):
            problems.append("%s must be a mapping of name -> definition" % section)
    tables = tables if isinstance(tables, dict) else {}
    edges = edges if isinstance(edges, dict) else {}

    taken = {}                                      # declared name -> the section that used it
    for section, value, built_in, other in (("tables", tables, BUILT_IN_NODE_TABLES, "node table"),
                                            ("edges", edges, tuple(BUILT_IN_EDGES), "edge type")):
        for name in value:
            name = str(name)
            where = "%s.%s" % (section, name)
            if not ID_RE.match(name):
                problems.append("%s: name must match [a-z0-9_]+" % where)
            if name in built_in:
                problems.append("%s: %r is a built-in %s and may not be redeclared; choose another name"
                                % (where, name, other))
            elif name in BUILT_IN_NODE_TABLES or name in BUILT_IN_EDGES:
                problems.append("%s: %r is already a built-in table name; choose another name" % (where, name))
            elif name in RESERVED_TABLES:
                problems.append("%s: %r is reserved by the engine; choose another name" % (where, name))
            elif name in taken:
                problems.append("%s: %r is already declared under %s; a name is one thing or the other"
                                % (where, name, taken[name]))
            else:
                taken[name] = section

    def check_fields(where, body, reserved=()):
        fields = (body or {}).get("fields")
        if not isinstance(fields, dict) or not fields:
            problems.append("%s: fields must be a non-empty mapping of name -> type" % where)
            return
        for field, value in fields.items():
            field = str(field)
            if not ID_RE.match(field):
                problems.append("%s: field name %r must match [a-z0-9_]+" % (where, field))
            if field in reserved:
                problems.append("%s: field %r is owned by the datastore on a relation table" % (where, field))
            spec, problem = _field_spec(value)
            if problem:
                problems.append("%s.%s: %s" % (where, field, problem))

    for name, body in tables.items():
        where = "tables.%s" % name
        if not isinstance(body, dict):
            problems.append("%s: must be a mapping with fields" % where)
            continue
        unknown = sorted(set(body) - TABLE_KEYS)
        if unknown:
            problems.append("%s: unknown key(s) %s; allowed: %s"
                            % (where, ", ".join(unknown), ", ".join(sorted(TABLE_KEYS))))
        check_fields(where, body)

    known_tables = set(node_tables_of(data))
    for name, body in edges.items():
        where = "edges.%s" % name
        if not isinstance(body, dict):
            problems.append("%s: must be a mapping with from, to and fields" % where)
            continue
        unknown = sorted(set(body) - EDGE_KEYS)
        if unknown:
            problems.append("%s: unknown key(s) %s; allowed: %s"
                            % (where, ", ".join(unknown), ", ".join(sorted(EDGE_KEYS))))
        for end in ("from", "to"):
            table = body.get(end)
            if not table:
                problems.append("%s: %s must name the table this edge %s" % (where, end, end == "from" and "starts at" or "ends at"))
            elif str(table) in BUILT_IN_EDGES or str(table) in edges:
                problems.append("%s: %s names %r, which is an edge type, not a node table; known tables: %s"
                                % (where, end, str(table), ", ".join(sorted(known_tables))))
            elif str(table) not in known_tables:
                problems.append("%s: %s names unknown table %r; declare it under tables: or name one of %s"
                                % (where, end, str(table), ", ".join(sorted(known_tables))))
        if body.get("fields") is not None:
            check_fields(where, body, reserved=EDGE_RESERVED_FIELDS)


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
    inst = Instance(validate(data), source=path)
    refuse_unusable_regression_file(inst)       # the evaluator's file must exist and parse (block below)
    return inst


# ---- the evaluator's regression file (groundworks/evaluator.py; docs/TESTING.md s.3) ---------------------
#
# `evaluator.regression_file` was required from the first commit, and the demo declared a file that did not
# exist while no code read the key: the fixed test every improvement must beat had never run. A config
# loaded from a FILE now refuses a declared regression file that is missing, unreadable or not in the
# format, by name. The path is resolved against the directory the config file is in (an absolute path is
# taken as it is). A config loaded from a mapping has no directory, so load() cannot check it there;
# regression_file_path() then needs `base_dir` and refuses by name without one, rather than guessing the
# current working directory.

def regression_file_path(inst, base_dir=None):
    """The regression file an instance declares, as an absolute path. Does not check that it exists."""
    declared = pathlib.Path(str(inst.evaluator["regression_file"])).expanduser()
    if declared.is_absolute():
        return declared
    if base_dir is None:
        if not inst.source:
            raise ConfigError("instance %s was loaded from a mapping, so its evaluator.regression_file %r has no "
                              "directory to resolve against; load it from its instance.yaml or pass base_dir"
                              % (inst.id, str(declared)))
        base_dir = pathlib.Path(inst.source).resolve().parent
    return (pathlib.Path(base_dir) / declared).resolve()


def refuse_unusable_regression_file(inst, base_dir=None):
    """ConfigError naming the file when the declared regression file is missing or not a valid regression
    file. The format has one implementation, in groundworks/evaluator.py."""
    from groundworks import evaluator          # local: evaluator imports this module
    path = regression_file_path(inst, base_dir)
    if not path.is_file():
        raise ConfigError("config invalid:\n  - evaluator.regression_file %s does not exist: the evaluator this "
                          "config declares has never been written, so it can never run. Create it (format %s; "
                          "an empty entries list until the first real error is recorded)"
                          % (path, evaluator.FORMAT))
    try:
        evaluator.read_file(path)
    except evaluator.RegressionFileError as exc:
        raise ConfigError("config invalid:\n  - evaluator.regression_file: %s" % exc) from exc
    return path


if __name__ == "__main__":
    import sys
    inst = load(sys.argv[1])
    print("OK %s: %d unit type(s), %d gate rule(s), confirming grades %s"
          % (inst.id, len(inst.units), len(inst.gate_rules()), ", ".join(inst.confirming_grades())))
