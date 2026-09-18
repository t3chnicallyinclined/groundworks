# -*- coding: utf-8 -*-
"""assertions.py -- a row may say, in structure, what another row's field holds; the graph checks it.

A fact is free text. When its text says "row R is usable" while R's status says otherwise, the graph
contradicts itself, and nothing can see it without reading the text -- which would put domain words in
engine code. So the claim is made structured instead: an ASSERTION EDGE from the asserting row to the
target row, carrying `field` and `value`, reads "this row asserts that the target's `field` holds
`value`". Comparing that with the target's current value is mechanical and holds no domain word.

The instance declares it (config section `assertions:`, human-owned when present):

    edges:
      asserts_state: {from: fact, to: unit}      # an ordinary declared edge; no fields of its own
    assertions:
      asserts_state:
        fields: [status]              # target fields this edge may assert; each needs a known domain
        promoted_by: [rule_id, ...]   # optional: gate rules on `from` whose value makes a row count;
                                      #   default every gate rule on `from`
        enforce: false                # optional; true refuses a write that would create a contradiction

Which asserting rows count: a row standing at the gated value of a promoting gate rule on its own table
(the rule's `field` = `value`). A row that has not passed a gate is a proposal, and its assertion is not
a contradiction; nothing here names a status.

    surql(inst)                     field/value checks on each assertion edge, append-only history on every
                                    field the report reads, and (enforce: true only) the refusing events
    check(inst, edge, field, value) the same legality check in Python, refusing by name
    report(db, inst)                every counted assertion that disagrees with its target, with which side
                                    changed last and when; never which side is right
    after_load(db, inst, counts)    the report, narrowed to what a load changed, plus the assertions that bear
                                    on each held-value conflict the load reported
    for_target(db, inst, record, field=None)   the report narrowed to one target (after a correction)

Apply `surql(inst)` after gates.schema_surql and gates.gate_surql (it redefines no gated field). The report
works without it; history it cannot find is reported UNKNOWN, never guessed.
Standard library only.
"""
import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from groundworks import config  # noqa: E402

SECTION = "assertions"
SPEC_KEYS = {"fields", "promoted_by", "enforce", "doc"}
# The fields the engine owns on an assertion edge. An instance declaring one of these on the edge would
# have its own definition silently replaced by surql(), so it is refused by name instead.
EDGE_FIELDS = ("field", "value")
EDGE_OWNED = EDGE_FIELDS + tuple(f + "_history" for f in EDGE_FIELDS) + ("learned_at",)
# The words every enforcement refusal starts with, so a caller can tell it from a gate refusal.
MARKER = "refused: contradicts a promoted assertion"


class AssertionContractError(Exception):
    """An assertion that names a field or value the target cannot hold."""


# ---- the contract, from the config ------------------------------------------------------------------

def _gate_rules(data):
    gates = (data or {}).get("gates")
    return [r for r in gates if isinstance(r, dict) and r.get("id")] if isinstance(gates, list) else []


def fields_of(data, table):
    """field -> type for a node table, built-in or declared; None for a table this instance does not have."""
    if table in config.BUILT_IN_FIELDS:
        return dict(config.BUILT_IN_FIELDS[table])
    declared = config.declared_tables_of(data)
    if table in declared:
        return {name: spec["type"] for name, spec in declared[table].items()}
    return None


def field_domain(data, table, field):
    """Every value `table.field` may hold, as Python values, or None when the engine cannot enumerate them.

    Kept equal to gates._field_domain (tests/test_assertions.py checks every field of every table), which
    needs a validated Instance; this reads the raw config so validation can use it."""
    declared = config.declared_tables_of(data)
    if table in declared:
        spec = declared[table].get(field)
        if not spec:
            return None
        if spec["one_of"]:
            return list(spec["one_of"])
        return [True, False] if spec["type"] == "bool" else None
    from groundworks.gates import CRITIQUE_VERDICTS, FACT_STATUSES
    data = data or {}
    vocab = data.get("vocabulary") if isinstance(data.get("vocabulary"), dict) else {}
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    fidelity = data.get("fidelity") if isinstance(data.get("fidelity"), dict) else {}
    units = data.get("units") if isinstance(data.get("units"), list) else []
    known = {("unit", "type"): [u.get("name") for u in units if isinstance(u, dict)],
             ("unit", "status"): status.get("values"),
             ("unit", "grade"): fidelity.get("order"),
             ("fact", "status"): FACT_STATUSES,
             ("critique", "verdict"): CRITIQUE_VERDICTS,
             ("critique", "answered"): [True, False],
             ("conclusion", "ready"): [True, False],
             ("authority", "verified"): [True, False],
             ("label", "tier"): vocab.get("tiers"),
             ("label", "dir"): vocab.get("directions"),
             ("label", "role"): vocab.get("roles")}
    values = known.get((table, field))
    return list(values) if values else None


def declared(inst):
    """edge -> {"from", "to", "fields", "rules" (the promoting gate rules), "enforce"}; {} when none.
    Reads a validated config, so every name in it exists."""
    data = inst.data if hasattr(inst, "data") else inst
    section = (data or {}).get(SECTION) or {}
    edges = config.edge_map_of(data)
    out = {}
    for edge, spec in section.items():
        src, dst = edges[str(edge)]
        wanted = spec.get("promoted_by")
        rules = [r for r in _gate_rules(data) if r.get("table") == src and (not wanted or r["id"] in wanted)]
        out[str(edge)] = {"from": src, "to": dst, "fields": [str(f) for f in spec["fields"]], "rules": rules,
                          "enforce": spec.get("enforce") is True}
    return out


def validate(data, problems):
    """Append every problem with the `assertions:` section to `problems`. Called from config.validate()."""
    if SECTION not in (data or {}):
        return
    section = data[SECTION]
    if not isinstance(section, dict) or not section:
        problems.append("assertions must be a mapping of edge type -> {fields: [...], promoted_by, enforce}")
        return
    edges = config.edge_map_of(data)
    declared_edges = config.declared_edges_of(data)
    rules = _gate_rules(data)
    for edge, spec in section.items():
        edge = str(edge)
        where = "assertions.%s" % edge
        if edge in config.BUILT_IN_EDGES:
            problems.append("%s: %r is a built-in edge with its own meaning; declare an edge for assertions under "
                            "edges:" % (where, edge))
            continue
        if edge not in edges:
            problems.append("%s: no edge %r; declare it under edges: with from and to (known edges: %s)"
                            % (where, edge, ", ".join(sorted(edges))))
            continue
        if not isinstance(spec, dict):
            problems.append("%s: must be a mapping with fields" % where)
            continue
        unknown = sorted(set(spec) - SPEC_KEYS)
        if unknown:
            problems.append("%s: unknown key(s) %s; allowed: %s" % (where, ", ".join(unknown), ", ".join(sorted(SPEC_KEYS))))
        src, dst = edges[edge]
        owned = sorted(set((declared_edges.get(edge) or {}).get("fields") or {}) & set(EDGE_OWNED))
        if owned:
            problems.append("%s: edges.%s declares %s, which the engine owns on an assertion edge"
                            % (where, edge, ", ".join(owned)))
        target_fields = fields_of(data, dst)
        fields = spec.get("fields")
        if not isinstance(fields, list) or not fields:
            problems.append("%s: fields must be a non-empty list of %s fields this edge may assert" % (where, dst))
        else:
            if len(set(map(str, fields))) != len(fields):
                problems.append("%s: fields lists a field twice" % where)
            for field in map(str, fields):
                if target_fields is None or field not in target_fields:
                    problems.append("%s: %r is not a field of %s; its fields are %s"
                                    % (where, field, dst, ", ".join(sorted(target_fields or {})) or "none"))
                elif field_domain(data, dst, field) is None:
                    problems.append("%s: %s.%s has no enumerated values (no vocabulary, no one_of, not bool), so no "
                                    "asserted value could be checked as legal; assert an enumerated field"
                                    % (where, dst, field))
                elif dst in config.declared_tables_of(data) and field + "_history" in (target_fields or {}):
                    problems.append("%s: tables.%s declares %s_history, which the engine uses for %s's history"
                                    % (where, dst, field, field))
        if dst in config.declared_tables_of(data) and target_fields.get("learned_at", "datetime") != "datetime":
            problems.append("%s: tables.%s declares learned_at as %s; history needs it to be a datetime"
                            % (where, dst, target_fields["learned_at"]))
        on_source = [r for r in rules if r.get("table") == src]
        if not on_source:
            problems.append("%s: no gate rule promotes a %s row, so no assertion from %s could ever count; gate %s "
                            "or assert from a gated table" % (where, src, src, src))
        wanted = spec.get("promoted_by")
        if wanted is not None:
            if not isinstance(wanted, list) or not wanted:
                problems.append("%s: promoted_by must be a non-empty list of gate rule ids on %s" % (where, src))
            else:
                ids = {r["id"] for r in on_source}
                bad = [str(w) for w in wanted if w not in ids]
                if bad:
                    problems.append("%s: promoted_by names %s, not a gate rule on %s (rules on %s: %s)"
                                    % (where, ", ".join(bad), src, src, ", ".join(sorted(ids)) or "none"))
        if spec.get("enforce", False) not in (True, False):
            problems.append("%s: enforce must be true or false" % where)


def check(inst, edge, field, value):
    """Refuse, by name, an assertion the datastore would refuse: an edge that is not an assertion edge, a field
    it may not assert, or a value the target field cannot hold."""
    spec = declared(inst).get(edge)
    if spec is None:
        raise AssertionContractError("%r is not an assertion edge in instance %s; declared: %s"
                              % (edge, inst.id, ", ".join(sorted(declared(inst))) or "none"))
    if field not in spec["fields"]:
        raise AssertionContractError("%s may assert %s of %s, not %r" % (edge, ", ".join(spec["fields"]), spec["to"], field))
    domain = field_domain(inst.data, spec["to"], field)
    if not any(value == v and type(value) is type(v) for v in domain):
        raise AssertionContractError("%s.%s cannot hold %r; its values are %s" % (spec["to"], field, value, domain))


# ---- surql ------------------------------------------------------------------------------------------

def _lit(value):
    from groundworks.gates import _value_literal
    return _value_literal(value)


def _list(values):
    return "[" + ", ".join(_lit(v) for v in values) + "]"


def _promoted(spec, row):
    """The expression that is true when `row` (a record path such as `in` or `$after`) counts."""
    return "(" + " OR ".join("%s.%s = %s" % (row, r["field"], _lit(r["value"])) for r in spec["rules"]) + ")"


def _disagrees(spec, edge_row, target):
    """True when the assertion `edge_row` disagrees with `target` for the field it names."""
    return "(" + " OR ".join("(%sfield = %s AND %s.%s != %svalue)" % (edge_row, _lit(f), target, f, edge_row)
                             for f in spec["fields"]) + ")"


def _history_spec(table, field, value_type):
    """The history the report reads for table.field: temporal.py's own entry when it tracks it, so applying
    both emits identical statements, else the same shape under the same naming."""
    from groundworks import temporal
    for name, spec in temporal.TRACKED.items():
        if spec.get("table", name) == table and spec["field"] == field:
            return spec
    return {"table": table, "field": field, "history": field + "_history", "before": field + "_before",
            "after": field + "_after", "type": "any" if value_type == "any" else "option<%s>" % value_type, "event": "%s_%s_history" % (table, field)}


def history_specs(inst):
    """Every (table, field) whose change history the report reads: asserted target fields, the promoting
    fields of asserting tables, and each assertion edge's own field and value."""
    out = {}
    for edge, spec in declared(inst).items():
        for field in spec["fields"]:
            kind = (fields_of(inst.data, spec["to"]) or {}).get(field, "string")
            out.setdefault((spec["to"], field), _history_spec(spec["to"], field, kind))
        for rule in spec["rules"]:
            kind = (fields_of(inst.data, spec["from"]) or {}).get(rule["field"], "string")
            out.setdefault((spec["from"], rule["field"]), _history_spec(spec["from"], rule["field"], kind))
        for field in EDGE_FIELDS:
            out.setdefault((edge, field), _history_spec(edge, field, "string" if field == "field" else "any"))
    return out


def surql(inst):
    """Statements to apply after gates.schema_surql and gates.gate_surql (and temporal_surql, in any order)."""
    from groundworks import temporal
    lines = ["-- generated by groundworks/assertions.py from instance %s; do not edit by hand" % inst.id]
    for edge, spec in declared(inst).items():
        lines.append("-- assertion edge %s: %s asserts %s of %s" % (edge, spec["from"], ", ".join(spec["fields"]), spec["to"]))
        lines.append("DEFINE FIELD OVERWRITE field ON %s TYPE string ASSERT $value IN %s;" % (edge, _list(spec["fields"])))
        legal = " OR ".join("($this.field = %s AND $value IN %s)" % (_lit(f), _list(field_domain(inst.data, spec["to"], f)))
                            for f in spec["fields"])
        lines.append("DEFINE FIELD OVERWRITE value ON %s TYPE any ASSERT %s;" % (edge, legal))
        lines.append("")
    for (table, field), spec in history_specs(inst).items():
        lines.extend(temporal._history_block(table, spec))
    for edge, spec in declared(inst).items():
        if spec["enforce"]:
            lines.extend(enforcement_surql(edge, spec))
        else:
            # OVERWRITE never removes an event, so switching enforcement off has to say so explicitly;
            # otherwise re-applying this after `enforce: false` would leave the refusals in force.
            lines.extend("REMOVE EVENT IF EXISTS %s_enforce_%s ON TABLE %s;" % (edge, name, table)
                         for name, table in (("target", spec["to"]), ("promotion", spec["from"]), ("edge", edge)))
    return "\n".join(lines) + "\n"


def enforcement_surql(edge, spec):
    """Three events, each refusing the one write that would create a contradiction, and nothing else:

      target     a change to an asserted field that disagrees with a counted assertion on it
      promotion  a row reaching a promoting value while one of its assertions disagrees with its target
      edge       an assertion created or re-stated on a counted row that disagrees with its target

    Each fires only on the write that CHANGES the thing it guards, so a contradiction already standing when
    enforcement was switched on blocks no unrelated write; the report still lists it. Moving the asserting
    row off its promoting value is never refused, and that is how the ordering deadlock is broken: demote
    the row, correct the target, re-state or retract the assertion, promote again (through its gate and
    through this check)."""
    changed = " OR ".join("$before.%s != $after.%s" % (f, f) for f in spec["fields"])
    target_bad = " OR ".join("(field = %s AND $before.%s != $after.%s AND value != $after.%s)" % (_lit(f), f, f, f)
                             for f in spec["fields"])
    reached = " OR ".join("($after.%s = %s AND ($before.%s ?? NONE) != %s)"
                          % (r["field"], _lit(r["value"]), r["field"], _lit(r["value"])) for r in spec["rules"])
    how = ("; to change it, move the asserting row off its promoted value first, then correct the target and "
           "re-state or retract the assertion")
    return [
        "DEFINE EVENT OVERWRITE %s_enforce_target ON TABLE %s WHEN $event = 'UPDATE' AND (%s) THEN {" % (edge, spec["to"], changed),
        "  LET $bad = (SELECT VALUE <string> id + ' from ' + <string> in + ' says ' + field + ' = ' + <string> value "
        "FROM %s WHERE out = $after.id AND %s AND (%s));" % (edge, _promoted(spec, "in"), target_bad),
        "  IF array::len($bad) > 0 { THROW %s + ': ' + <string> $after.id + ' by ' + <string> $bad + %s; };"
        % (_lit(MARKER), _lit(how)),
        "};",
        "DEFINE EVENT OVERWRITE %s_enforce_promotion ON TABLE %s WHEN $event IN ['CREATE', 'UPDATE'] AND (%s) THEN {"
        % (edge, spec["from"], reached),
        "  LET $bad = (SELECT VALUE <string> id + ' says ' + <string> out + ' ' + field + ' = ' + <string> value "
        "FROM %s WHERE in = $after.id AND %s);" % (edge, _disagrees(spec, "", "out")),
        "  IF array::len($bad) > 0 { THROW %s + ': promoting ' + <string> $after.id + ' by ' + <string> $bad; };"
        % _lit(MARKER),
        "};",
        "DEFINE EVENT OVERWRITE %s_enforce_edge ON TABLE %s WHEN $event IN ['CREATE', 'UPDATE'] "
        "AND ($event = 'CREATE' OR $before.field != $after.field OR $before.value != $after.value) THEN {" % (edge, edge),
        "  IF %s AND %s { THROW %s + ': ' + <string> $after.id + ' from promoted ' + <string> $after.in + ' says ' "
        "+ $after.field + ' = ' + <string> $after.value + ' of ' + <string> $after.out; };"
        % (_promoted(spec, "$after.in"), _disagrees(spec, "$after.", "$after.out"), _lit(MARKER)),
        "};",
        "",
    ]


# ---- the report -------------------------------------------------------------------------------------

def _to_dt(value):
    from groundworks import temporal
    return temporal.to_datetime(value) if value is not None else None


def _last_change(history, spec):
    """The newest history entry as {before, after, at, by}, or None for an empty history."""
    if not history:
        return None
    entry = max(history, key=lambda e: _to_dt(e["at"]))
    return {"before": entry.get(spec["before"]), "after": entry.get(spec["after"]), "at": entry["at"], "by": entry.get("by")}


def _since(history, learned_at, spec, what):
    """When a value was last set: its newest history entry, else learned_at for a row whose history exists and is
    empty (held since recorded), else UNKNOWN (no history kept here)."""
    if isinstance(history, list):
        last = _last_change(history, spec)
        if last:
            return _to_dt(last["at"]), dict(last, what=what)
        if learned_at is not None:
            return _to_dt(learned_at), {"what": what, "at": learned_at, "reason": "unchanged since the row was recorded"}
    return None, {"what": what, "reason": "UNKNOWN: no history on this field (apply assertions.surql)"}


def _promotion(history, learned_at, spec, value):
    """When the asserting row last reached the promoted value it holds now."""
    if isinstance(history, list):
        reached = [e for e in history if e.get(spec["after"]) == value]
        if reached:
            entry = max(reached, key=lambda e: _to_dt(e["at"]))
            return _to_dt(entry["at"]), {"what": "promoted", "before": entry.get(spec["before"]), "after": value,
                                         "at": entry["at"], "by": entry.get("by")}
        if learned_at is not None:
            return _to_dt(learned_at), {"what": "promoted", "at": learned_at, "reason": "created at the promoted value"}
    return None, {"what": "promoted", "reason": "UNKNOWN: no history on the promoting field"}


def counted(db, inst, edge=None, negate=False):
    """Every counted assertion row, with the target's current value and both sides' history.
    `negate=True` exists only so a test can prove the promotion filter is load-bearing."""
    specs = history_specs(inst)
    rows = []
    for name, spec in declared(inst).items():
        if edge and name != edge:
            continue
        for field in spec["fields"]:
            target_hist = specs[(spec["to"], field)]
            promo = ", ".join("in.%s AS promo_%s, in.%s AS promo_%s_history"
                              % (f, f, specs[(spec["from"], f)]["history"], f)
                              for f in sorted({r["field"] for r in spec["rules"]}))
            where = _promoted(spec, "in")
            query = ("SELECT <string> id AS edge, <string> in AS source, <string> out AS target, field, value, "
                     "learned_at AS edge_learned_at, value_history, field_history, out.%s AS current, "
                     "out.%s AS target_history, out.learned_at AS target_learned_at, in.learned_at AS source_learned_at, "
                     "%s FROM %s WHERE field = %s AND %s%s"
                     % (field, target_hist["history"], promo, name, _lit(field), "NOT " if negate else "", where))
            for row in db.sql(query, expect=1)[0]:
                row["edge_type"], row["target_field_spec"] = name, target_hist
                row["rules"] = spec["rules"]
                rows.append(row)
    return rows


def _judge(row, specs):
    """One counted row -> the report entry: both values, and which side changed last. Decides nothing else."""
    edge_type = row["edge_type"]
    target_at, target_change = _since(row.get("target_history"), row.get("target_learned_at"),
                                      row["target_field_spec"], "target changed")
    assertion_times, assertion_changes = [], []
    for field in EDGE_FIELDS:
        at, change = _since(row.get(field + "_history"), row.get("edge_learned_at"), specs[(edge_type, field)],
                            "assertion " + field + " set")
        assertion_times.append(at)
        assertion_changes.append(change)
    source_table = row["source"].split(":", 1)[0]
    for rule in row["rules"]:
        if row.get("promo_" + rule["field"]) == rule["value"]:
            at, change = _promotion(row.get("promo_%s_history" % rule["field"]), row.get("source_learned_at"),
                                    specs[(source_table, rule["field"])], rule["value"])
            assertion_times.append(at)
            assertion_changes.append(dict(change, rule=rule["id"]))
    if assertion_times and all(t is not None for t in assertion_times):
        known, assertion_change = max(zip(assertion_times, assertion_changes), key=lambda pair: pair[0])
    else:
        known = None
        assertion_change = {"reason": "; ".join(c["reason"] for c in assertion_changes
                                                if str(c.get("reason", "")).startswith("UNKNOWN"))}
    if target_at is None or known is None:
        changed_last = "unknown"
    elif target_at > known:
        changed_last = "target"
    elif target_at < known:
        changed_last = "assertion"
    else:
        changed_last = "same instant"
    return {"edge": row["edge"], "edge_type": edge_type, "source": row["source"], "target": row["target"],
            "field": row["field"], "asserted": row["value"], "current": row.get("current"),
            "changed_last": changed_last, "target_last_change": target_change, "assertion_last_change": assertion_change}


def report(db, inst, negate=False):
    """Every counted assertion that disagrees with its target's current value.

        {"assertion_rows": N, "counted": M, "agree": A, "contradictions": [...], "disagreeing_assertions": [...]}

    `assertion_rows` and `counted` are printed next to the contradictions so "0 contradictions" over nothing
    counted reads as what it is. Each contradiction names the asserting row, the target, the field, the
    asserted and current values, and `changed_last`: `target` (the target moved after the assertion was
    made and promoted), `assertion` (the assertion was made or promoted over the target's value), `same
    instant`, or `unknown` (a side has no history). It does not say which side is right.
    `disagreeing_assertions` lists targets on which counted assertions disagree with one another."""
    specs = history_specs(inst)
    total = 0
    for name in declared(inst):
        n = db.sql("SELECT count() AS n FROM %s GROUP ALL" % name, expect=1)[0]
        total += n[0]["n"] if n else 0
    rows = counted(db, inst, negate=negate)
    contradictions = [_judge(r, specs) for r in rows if r.get("current") != r["value"]]
    by_target = {}
    for r in rows:
        by_target.setdefault((r["target"], r["field"]), {}).setdefault(repr(r["value"]), []).append(r["edge"])
    among = [{"target": t, "field": f, "values": {k: sorted(v) for k, v in sorted(vals.items())}}
             for (t, f), vals in sorted(by_target.items()) if len(vals) > 1]
    return {"assertion_rows": total, "counted": len(rows), "agree": len(rows) - len(contradictions),
            "contradictions": sorted(contradictions, key=lambda c: (c["target"], c["field"], c["edge"])),
            "disagreeing_assertions": among}


def for_target(db, inst, record, field=None):
    """The contradictions on one target row (optionally one field): what to show after a correction."""
    rep = report(db, inst)
    return [c for c in rep["contradictions"] if c["target"] == str(record) and (field is None or c["field"] == field)]


def after_load(db, inst, counts, table="unit"):
    """What a load report should carry. `counts` is loader.load()'s return value.

      caused_by_this_load   contradictions on a (row, field) this load changed: followed or accepted
      standing              every contradiction in the graph after the load (the load may not have caused them)
      conflicts             for each held value the load reported as a conflict, the counted assertions on that
                            field and whether each agrees with the held value, the source value, or neither --
                            evidence for the human deciding accept_source, not a decision"""
    from groundworks.pipeline.loader import rid
    rep = report(db, inst)
    changed = {(rid(table, r["unit"]), r["field"]) for r in (counts.get("followed_rows") or [])
               + (counts.get("accepted_rows") or [])}
    rows = counted(db, inst)
    conflicts = []
    for c in counts.get("conflict_rows") or []:
        target = rid(table, c["unit"])
        on = [r for r in rows if r["target"] == target and r["field"] == c["field"]]
        conflicts.append(dict(c, assertions=[
            {"edge": r["edge"], "source": r["source"], "asserted": r["value"],
             "agrees_with": ("held" if r["value"] == c["corrected"] else "source" if r["value"] == c["source"]
                             else "neither")} for r in on]))
    return {"caused_by_this_load": [c for c in rep["contradictions"] if (c["target"], c["field"]) in changed],
            "standing": rep["contradictions"], "counted": rep["counted"], "conflicts": conflicts}
