# -*- coding: utf-8 -*-
"""gates.py -- generate the datastore schema, the promotion gates, and a probe pair per gate.

From the instance config alone:
  schema_surql(inst)   tables and fields, with enumerated values enforced
  gate_surql(inst)     one ASSERT per gate rule, expanded from the config, holding on raw writes
  probes(inst)         for EVERY rule: a write that must be refused, then the same write accepted,
                       and for EVERY forbids clause the row it forbids, built from the clause itself
  run_probes(inst, db) run them, clean up even on failure, report both counts

    python engine/gates.py INSTANCE.yaml --emit schema|gates|probes
    python engine/gates.py INSTANCE.yaml --apply --url http://127.0.0.1:8021/sql --ns demo --db incident

A gate with only one probe is a bug: a rule that only rejects, or only accepts, is broken.
Standard library only.
"""
import argparse
import json
import pathlib
import re
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config  # noqa: E402

# The edge tables the ENGINE ships with, as (source table, target table). An instance adds its own
# under `edges:`; every function here reads `inst.edge_map()` -- these plus the instance's -- so a
# domain that needs a new edge type needs no edit to this file.
EDGES = config.BUILT_IN_EDGES

FACT_STATUSES = ["proposed", "inferred", "open", "confirmed", "ruled_out", "stale", "superseded"]
CRITIQUE_VERDICTS = ["fatal", "damaging", "survivable"]


# The words every independence refusal starts with, then the rule id and a colon. write.py and the
# probes read the rule back out of the datastore's message with independence_rule_in().
INDEPENDENCE_MARKER = "independent promotion refused by rule"
_INDEPENDENCE_RULE = re.compile(re.escape(INDEPENDENCE_MARKER) + r" ([A-Za-z0-9_]+):")


class GateError(Exception):
    pass


def independence_rule_in(message):
    """The id of the independence rule a datastore refusal names, or None when it names none."""
    found = _INDEPENDENCE_RULE.search(str(message or ""))
    return found.group(1) if found else None


def unique_index_name(table, field):
    return "%s_%s_unique" % (table, field)


def _lit(values):
    return "[" + ", ".join("'%s'" % str(v).replace("'", "\\'") for v in values) + "]"


# ---- schema --------------------------------------------------------------------------------------

def builtin_schema(inst):
    """The built-in tables' definitions as data, in schema order: ("table", name), ("field", table, field,
    type, default, assert), ("index", statement) and ("blank",). One source for two readers: schema_surql renders
    it, and gate_surql restates a gated built-in field from it -- a gate is a DEFINE FIELD OVERWRITE applied
    after the schema, so a restatement that dropped the field's own ASSERT would leave the field accepting any
    value (ROADMAP V2-22(a): a gate on unit.status kept the gate and lost the status list)."""
    v = inst.allowed_vocabulary()
    return [
        ("table", "unit"),
        ("field", "unit", "type", "string", None, "$value IN %s" % _lit(inst.unit_names())),
        ("field", "unit", "text", "string", None, None),
        ("field", "unit", "locator", "object FLEXIBLE", None, None),
        ("field", "unit", "status", "string", None, "$value IN %s" % _lit(inst.status_values())),
        ("field", "unit", "grade", "string", None, "$value IN %s" % _lit(inst.grades())),
        ("field", "unit", "hash", "string", None, None),
        ("field", "unit", "meta", "option<object> FLEXIBLE", None, None),
        ("index", "DEFINE INDEX OVERWRITE unit_type_status ON unit FIELDS type, status;"),
        ("blank",),
        ("table", "label"),
        ("field", "label", "unit", "record<unit>", None, None),
        ("field", "label", "tier", "int", None, "$value IN %s" % json.dumps(list(v["tiers"]))),
        ("field", "label", "dir", "option<string>", None, "$value = NONE OR $value IN %s" % _lit(v["directions"])),
        ("field", "label", "issues", "array<string>", None, "array::all($value, |$i| $i IN %s)" % _lit(v["issues"])),
        ("field", "label", "targets", "array<string>", None, None),
        ("field", "label", "role", "option<string>", None, "$value = NONE OR $value IN %s" % _lit(v["roles"])),
        ("field", "label", "reason", "option<string>", None, None),
        ("field", "label", "quote", "option<string>", None, None),
        ("field", "label", "grade", "string", "'agent'", "$value = 'agent'"),
        ("field", "label", "chunk", "string", None, None),
        ("field", "label", "run", "string", None, None),
        ("blank",),
        ("table", "fact"),
        ("field", "fact", "statement", "string", None, None),
        ("field", "fact", "status", "string", "'proposed'", None),
        ("field", "fact", "note", "option<string>", None, None),
        ("field", "fact", "tried", "option<string>", None, None),
        ("field", "fact", "evidence", "option<string>", None, None),
        ("field", "fact", "reasoning_type", "option<string>", None, None),
        ("field", "fact", "author", "option<string>", None, None),
        ("field", "fact", "lens", "option<string>", None, None),
        ("field", "fact", "learned_at", "datetime", "time::now()", None),
        ("blank",),
        ("table", "conclusion"),
        ("field", "conclusion", "statement", "string", None, None),
        ("field", "conclusion", "ready", "bool", "false", None),
        ("field", "conclusion", "rating", "option<string>", None, None),
        ("field", "conclusion", "author", "option<string>", None, None),
        ("blank",),
        ("table", "authority"),
        ("field", "authority", "citation", "string", None, None),
        ("field", "authority", "verified", "bool", "false", None),
        ("field", "authority", "source_grade", "option<string>", None, None),
        ("blank",),
        ("table", "critique"),
        ("field", "critique", "lens", "string", None, None),
        ("field", "critique", "head", "string", None, None),
        ("field", "critique", "verdict", "string", None, "$value IN %s" % _lit(CRITIQUE_VERDICTS)),
        ("field", "critique", "text", "option<string>", None, None),
        ("field", "critique", "author", "option<string>", None, None),
        ("field", "critique", "answered", "bool", "false", None),
        ("blank",),
        ("table", "answer"),
        ("field", "answer", "text", "string", None, None),
        ("field", "answer", "author", "option<string>", None, None),
        ("blank",),
    ]


def builtin_field(inst, table, field):
    """(type, default, assert) of a built-in field, or None when the engine defines no such field."""
    for entry in builtin_schema(inst):
        if entry[0] == "field" and entry[1] == table and entry[2] == field:
            return entry[3], entry[4], entry[5]
    return None


def _render_builtin(entry):
    kind = entry[0]
    if kind == "table":
        return table_definition(entry[1])
    if kind == "field":
        _, table, field, typ, default, assert_ = entry
        return "DEFINE FIELD OVERWRITE %s ON %s TYPE %s%s%s;" % (
            field, table, typ, " DEFAULT %s" % default if default is not None else "",
            " ASSERT %s" % assert_ if assert_ is not None else "")
    if kind == "index":
        return entry[1]
    return ""


# ---- a table definition that a re-apply cannot strip ----------------------------------------------------
#
# Measured on SurrealDB 3.1.6 (2026-09-16): `DEFINE TABLE OVERWRITE t SCHEMAFULL` replaces the WHOLE table
# definition, so re-applying the schema after access.access_surql reset PERMISSIONS to NONE on 13 of 19 tables
# (every agent locked out, without a word) and dropped the CHANGEFEED queue.changefeed_surql had put on 5 (the
# queue then reads nothing, forever). `ALTER TABLE` changes only the clauses it names: measured, `ALTER TABLE t
# TYPE NORMAL SCHEMAFULL` and `ALTER TABLE e TYPE RELATION IN a OUT b ENFORCED SCHEMALESS` keep PERMISSIONS and
# CHANGEFEED as they were, and still turn a hand-made SCHEMALESS back into SCHEMAFULL. So every table is
# created if it is not there, then altered to the shape the config says: a first apply and a re-apply end in
# the same definition, and nothing another generator added to the table is lost.
#
# ENFORCED (ROADMAP B-011, measured on 3.1.6): a plain relation table accepts `RELATE a:1->e->b:missing` and
# stores a link to nothing. With ENFORCED the same RELATE is refused ("The record 'b:missing' does not exist"),
# for root and for a record credential alike, and for an existing endpoint the credential cannot read (the
# existence check is not filtered by PERMISSIONS). INSERT RELATION is refused the same way. Deleting an endpoint
# afterwards still removes its edges. What ENFORCED does NOT do: check edges already stored -- a table altered
# to ENFORCED keeps every dangling edge it already held (measured), so an existing graph must be swept once
# (dangling_edges_surql below).

def table_definition(table):
    """A node table, SCHEMAFULL, created or re-shaped without touching its PERMISSIONS or CHANGEFEED."""
    return ("DEFINE TABLE IF NOT EXISTS %s TYPE NORMAL SCHEMAFULL;\nALTER TABLE %s TYPE NORMAL SCHEMAFULL;"
            % (table, table))


def relation_definition(edge, src, dst):
    """An edge table whose both ends must exist when a link is written, created or re-shaped the same way."""
    shape = "TYPE RELATION IN %s OUT %s ENFORCED SCHEMALESS" % (src, dst)
    return "DEFINE TABLE IF NOT EXISTS %s %s;\nALTER TABLE %s %s;" % (edge, shape, edge, shape)


def dangling_edges_surql(inst):
    """edge -> one query counting that edge table's links whose `in` or `out` record is gone. ENFORCED stops new
    ones; this finds the ones stored before it was applied."""
    return {edge: "RETURN count(SELECT VALUE id FROM %s WHERE record::exists(in) = false OR record::exists(out) = false);"
            % edge for edge in inst.edge_map()}


def count_dangling_edges(db, inst):
    """edge -> how many stored links point at a missing record, for every edge table that has any."""
    out = {}
    for edge, statement in dangling_edges_surql(inst).items():
        n = db.sql(statement, expect=1)[0]
        if n:
            out[edge] = n
    return out


def schema_surql(inst):
    lines = ["-- generated by engine/gates.py from instance %s; do not edit by hand" % inst.id]
    lines += [_render_builtin(entry) for entry in builtin_schema(inst)]
    for table, fields in inst.declared_tables().items():
        lines.append(table_definition(table))
        for field, spec in fields.items():
            lines.append(_field_surql(table, field, spec))
        lines.append("")
    for edge, (src, dst) in inst.edge_map().items():
        lines.append(relation_definition(edge, src, dst))
        for field, spec in inst.edge_fields(edge).items():
            lines.append(_field_surql(edge, field, spec))
    lines.extend(originals_schema_lines())
    lines.extend(unique_surql(inst))
    return "\n".join(lines) + "\n"


# ---- originals -- one self-contained block (xscholar/originals.py owns the definitions) --------------------
def originals_schema_lines():
    """The built-in `document` table and `unit.document` (REFERENCE ON DELETE REJECT). Imported here, not at
    the top: originals.py reads this module's clause helpers."""
    from xscholar import originals
    return [""] + originals.schema_lines() + [""]
# ---- end originals block ------------------------------------------------------------------------------------


def unique_surql(inst):
    """One UNIQUE index per `unique:` field. Measured on SurrealDB 3.1.6: a duplicate CREATE, UPDATE or
    UPSERT is refused with "Database index `<name>` already contains <value>, with record <id>", for
    root and record users alike; the comparison is exact (case and whitespace count); any number of rows
    may hold NONE in an optional unique field; and defining the index over rows that already collide is
    refused and leaves no index behind -- so applying this to a graph that already holds duplicates
    fails loudly, naming the first duplicate, instead of enforcing nothing."""
    lines = []
    for table, fields in inst.unique_fields().items():
        for field in fields:
            lines.append("DEFINE INDEX OVERWRITE %s ON %s FIELDS %s UNIQUE;"
                         % (unique_index_name(table, field), table, field))
    return lines


def _field_surql(table, field, spec):
    """One declared field. An enumerated field enforces its list in the datastore, not in a guide."""
    line = "DEFINE FIELD OVERWRITE %s ON %s TYPE %s" % (
        field, table, "option<%s>" % spec["type"] if spec["optional"] else spec["type"])
    if spec["one_of"]:
        values = _lit(spec["one_of"]) if spec["type"] == "string" else json.dumps(list(spec["one_of"]))
        line += " ASSERT %s$value IN %s" % ("$value = NONE OR " if spec["optional"] else "", values)
    return line + ";"


def _default_value(spec):
    """The value a probe row carries for a declared field: the first enumerated value, or a blank of
    the right type. It is never chosen to satisfy a gate -- clauses supply their own values."""
    if spec["one_of"]:
        return _value_literal(spec["one_of"][0])
    return {"string": "'probe'", "int": "0", "float": "0.0", "bool": "false",
            "datetime": "time::now()"}[spec["type"]]


# ---- gates -----------------------------------------------------------------------------------------

def _expand(where, inst):
    return config.expand_where(inst.data, where)


def _edge_path(rule_table, clause, inst):
    """(traversal path, table at the other end) for one requires/forbids clause."""
    edges = inst.edge_map()
    edge = clause.get("edge")
    if edge not in edges:
        raise GateError("gate %s: unknown edge %r; known: %s (declare more under `edges:`)"
                        % (rule_table, edge, ", ".join(edges)))
    src, dst = edges[edge]
    if rule_table == src:
        path, other = "->%s->%s" % (edge, dst), dst
    elif rule_table == dst:
        path, other = "<-%s<-%s" % (edge, src), src
    else:
        raise GateError("edge %s joins %s and %s; it does not touch table %s" % (edge, src, dst, rule_table))
    declared = clause.get("to") or clause.get("from")
    if declared and other not in declared:
        raise GateError("edge %s from %s reaches %s, not %s" % (edge, rule_table, other, declared))
    return path, other


def _traversal(rule_table, clause, inst, subject="$this"):
    """The subquery counting matching neighbours of `subject` for one requires/forbids clause.

    `subject` is `$this` inside a gate ASSERT and a concrete record id when a probe has to ask the
    datastore whether the evidence it just wrote really does match the clause."""
    path, _ = _edge_path(rule_table, clause, inst)
    where = _expand(clause.get("where"), inst)
    return "count(SELECT VALUE id FROM %s%s%s)" % (subject, path, " WHERE " + where if where else "")


def gate_expression(rule, inst):
    """The boolean that must hold when rule.field takes rule.value."""
    parts = []
    for clause in rule.get("requires") or []:
        parts.append("%s >= %d" % (_traversal(rule["table"], clause, inst), int(clause.get("min", 1))))
    for clause in rule.get("forbids") or []:
        parts.append("%s = 0" % _traversal(rule["table"], clause, inst))
    if not parts:
        raise GateError("gate %s requires or forbids nothing" % rule["id"])
    return " AND ".join(parts)


def _value_literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'%s'" % str(value).replace("'", "\\'")


# ---- the same rule, whoever is calling ------------------------------------------------------------------
#
# Measured on SurrealDB 3.1.6 (reproduced from a report by the coordinating session): a field ASSERT's
# subqueries run under the CALLER's row permissions. With a rule that only forbids, a credential that may
# write the gated table but cannot read the forbidden rows counts 0 of them and the write is ACCEPTED --
# root and a credential that can read them are refused. An EVENT runs privileged: the same expression,
# evaluated in an event, refused all three. So every rule with clauses is ALSO emitted as an event.
# The ASSERT stays: it is the refusal every caller that sees the rows has always met, with the message
# write.py words. What the event does not change: a `requires` clause still refuses a caller who cannot
# see the evidence (the ASSERT counts 0), which fails closed -- a blocked legitimate write, never a hole.

GATE_EVENT_MARKER = "gate refused by rule"


def gate_event_name(rule):
    return "%s_gate" % re.sub(r"[^A-Za-z0-9_]", "_", str(rule["id"]))


def gate_event_expression(rule, inst):
    """gate_expression over `$after.id` instead of `$this`, for use inside an event."""
    parts = []
    for clause in rule.get("requires") or []:
        parts.append("%s >= %d" % (_traversal(rule["table"], clause, inst, subject="$after.id"), int(clause.get("min", 1))))
    for clause in rule.get("forbids") or []:
        parts.append("%s = 0" % _traversal(rule["table"], clause, inst, subject="$after.id"))
    if not parts:
        raise GateError("gate %s requires or forbids nothing" % rule["id"])
    return " AND ".join(parts)


def gate_event_surql(inst):
    """One privileged event per rule with clauses. It fires on every write that leaves the field at the gated
    value, exactly as the ASSERT does, so a row already promoted cannot be edited past a rule either."""
    lines = []
    for rule in inst.gate_rules():
        if not rule.get("requires") and not rule.get("forbids"):
            continue
        lines.append(
            "DEFINE EVENT OVERWRITE %s ON TABLE %s WHEN $event != 'DELETE' AND $after.%s = %s THEN { "
            "IF !(%s) { THROW %s + <string> $after.id + ' by ' + <string> ($auth ?? 'root'); }; };  -- %s"
            % (gate_event_name(rule), rule["table"], rule["field"], _value_literal(rule["value"]),
               gate_event_expression(rule, inst),
               _value_literal("%s %s: %s.%s may become %s only when every clause of the rule holds, counted over "
                              "every row whoever is writing; refused " % (GATE_EVENT_MARKER, rule["id"], rule["table"],
                                                                          rule["field"], str(rule["value"]))),
               rule["id"]))
    return lines


def _unreadable(role, rule, inst, kinds=("requires", "forbids")):
    """What a rule's clauses count that `role` cannot read: the edge, or the table at its far end."""
    from xscholar import access
    edges, out = inst.edge_map(), []
    for kind in kinds:
        for clause in rule.get(kind) or []:
            _, other = _edge_path(rule["table"], clause, inst)
            if not access._can_read(role, clause["edge"], edges):
                out.append(clause["edge"])
            if other not in role["read"]:
                out.append(other)
    return sorted(set(out))


def unpassable_roles(inst):
    """Roles that may write a gated table but could never make its gated change, because a `requires` clause
    counts rows they cannot read (the ASSERT counts under the caller's permissions, so it counts 0 for them).
    Reported, never refused: a role that writes a table without ever promoting it (a loader creating the rows
    others promote) is ordinary. A role that is MEANT to promote and appears here is misconfigured."""
    out = []
    for rule in inst.gate_rules():
        for name, role in inst.access_roles().items():
            if rule["table"] not in role["write"]:
                continue
            unreadable = _unreadable(role, rule, inst, kinds=("requires",))
            if unreadable:
                out.append({"role": name, "rule": rule["id"], "table": rule["table"], "field": rule["field"],
                            "value": rule["value"], "unreadable": unreadable})
    return out


def blind_role(rule, inst):
    """A role that may write the rule's row but cannot read what one of its `forbids` clauses counts (the edge
    or the row at its far end), or None. Only such a caller could slip past an ASSERT, so it is the caller
    the blind probe uses."""
    from xscholar import access
    edges = inst.edge_map()
    lens_carrying = access.lens_tables(inst)
    for name, role in sorted(inst.access_roles().items()):
        if rule["table"] not in role["write"] or rule["table"] not in role["read"]:
            continue
        if role["lens_bound"] and rule["table"] in lens_carrying:
            continue
        for clause in rule.get("forbids") or []:
            _, other = _edge_path(rule["table"], clause, inst)
            if not access._can_read(role, clause["edge"], edges) or other not in role["read"]:
                return name
    return None


def gate_surql(inst):
    """One ASSERT per rule. Rules on the same field are combined into one field definition."""
    by_field = {}
    for rule in inst.gate_rules():
        if not rule.get("requires") and not rule.get("forbids"):
            continue          # an independence-only rule has no clause to assert; its event is below
        by_field.setdefault((rule["table"], rule["field"]), []).append(rule)
    lines = ["-- generated by engine/gates.py from instance %s; human-owned rules, do not edit by hand" % inst.id]
    for (table, field), rules in by_field.items():
        conditions = []
        for rule in rules:
            conditions.append("($value != %s OR (%s))" % (_value_literal(rule["value"]), gate_expression(rule, inst)))
        # This DEFINE FIELD OVERWRITEs whatever the schema said about the field, so it has to restate
        # the field's own type and enumeration. A declared field whose `one_of` were dropped here
        # would be enumerated in the config, in the schema, and in nothing the datastore enforces.
        spec = inst.table_fields(table).get(field)
        if spec:
            typ = "option<%s>" % spec["type"] if spec["optional"] else spec["type"]
            enum, default = "", ""
            if spec["one_of"]:
                values = _lit(spec["one_of"]) if spec["type"] == "string" else json.dumps(list(spec["one_of"]))
                enum = " AND (%s$value IN %s)" % ("$value = NONE OR " if spec["optional"] else "", values)
        elif table == "fact" and field == "status":
            # the schema gives fact.status no list of its own; the engine's statuses are enforced here
            typ, enum = "string", " AND $value IN %s" % _lit(FACT_STATUSES)
            default = " DEFAULT 'proposed'"
        elif builtin_field(inst, table, field):
            # A built-in field: restate its type, default AND its own ASSERT (unit.status and unit.grade take
            # their lists from the config). Without the ASSERT the field accepted any string once gated.
            typ, default_value, own_assert = builtin_field(inst, table, field)
            default = " DEFAULT %s" % default_value if default_value is not None else ""
            enum = " AND (%s)" % own_assert if own_assert is not None else ""
        else:
            raise GateError("gate %s: %s.%s is neither a built-in field nor a declared one, so its type and "
                            "enumeration cannot be restated" % (", ".join(r["id"] for r in rules), table, field))
        lines.append("DEFINE FIELD OVERWRITE %s ON %s TYPE %s%s ASSERT %s%s;  -- %s"
                     % (field, table, typ, default, " AND ".join(conditions), enum,
                        ", ".join(r["id"] for r in rules)))
    lines.extend(gate_event_surql(inst))
    lines.extend(independence_surql(inst))
    lines.extend(writer_record_surql(inst))
    lines.extend(independence_mode_surql(inst))
    from xscholar import promotion_log
    lines.extend(promotion_log.log_surql(inst))
    return "\n".join(lines) + "\n"


def independence_surql(inst):
    """For every rule with `independent: true`: who wrote the row, recorded by the datastore, and an
    event that refuses the gated change unless it comes from a credential not on that record.

    Why not a gate clause: "the promoter is not the proposer" compares the writing identity with a
    field of the row under test, and no clause can read one (`gate_expression` only counts neighbours
    of $this). Why not a field ASSERT: it sees the field's own previous value, not the previous row, so
    it cannot see who wrote the row before this write. An event sees `$before` (the whole row), `$after`
    and `$auth`, and a THROW inside it aborts the write with an error status.

    Why the record cannot be forged: `written_by` is a VALUE field, recomputed on every write from its
    own previous value plus `$auth`, so a caller's SET of it is discarded -- measured for a record user
    and for root, both removing an identity and inserting another. `$auth` is set by the datastore at
    sign-in; the `author` string write.py stamps is supplied by the caller and proves nothing.

    What root can and cannot do: root's `$auth` is NONE, so root never appears on the record and root
    can never make the change. A row only root has written has no recorded proposer and is refused for
    every credential until a signed-in credential has written it. Root CAN provision credentials and
    redefine this event; that is a deliberate act by the owner of the datastore, which no rule inside
    the datastore can prevent."""
    rules = inst.independent_rules()
    if not rules:
        return []
    field_name = config.INDEPENDENCE_FIELD
    lines = ["-- independent promotion: the datastore records who wrote each row; the caller cannot"]
    for table in sorted({rule["table"] for rule in rules}):
        lines.append("DEFINE FIELD OVERWRITE %s ON %s TYPE array<record> VALUE array::union($before ?? [], "
                     "IF $auth = NONE { [] } ELSE { [$auth] });" % (field_name, table))
    for rule in rules:
        value = _value_literal(rule["value"])
        said = "%s.%s may become %s only by a signed-in credential that has not written the row" % (
            rule["table"], rule["field"], str(rule["value"]))
        lines.append(
            "DEFINE EVENT OVERWRITE %s ON TABLE %s WHEN $after.%s = %s AND ($before.%s ?? NONE) != %s THEN { "
            "IF $auth = NONE OR array::len($before.%s ?? []) = 0 OR $auth IN ($before.%s ?? []) { "
            "THROW %s + <string> $after.id + ' by ' + <string> ($auth ?? 'root (no identity)') "
            "+ '; written by ' + <string> ($before.%s ?? []); }; };  -- %s"
            % (_independence_event(rule), rule["table"], rule["field"], value, rule["field"], value,
               field_name, field_name,
               _value_literal("%s %s: %s; refused " % (INDEPENDENCE_MARKER, rule["id"], said)),
               field_name, rule["id"]))
    return lines


def _independence_event(rule):
    return "%s_independent" % re.sub(r"[^A-Za-z0-9_]", "_", str(rule["id"]))


# ---- who wrote what, principals, and the promoter's own evidence ----------------------------------------
#
# Measured on SurrealDB 3.1.6 (notes/2026-09-16_live_schema_and_promotion_audit.md):
#   * a VALUE clause runs with the WRITER's permissions: `$auth.principal` read there is NONE, because the
#     agent table is PERMISSIONS NONE. An EVENT runs privileged: it reads `$auth.principal`, reads other
#     rows, and writes tables whose PERMISSIONS are NONE. So everything that needs the agent row is an event.
#   * `written_by` on an EDGE is not evidence of work: a credential that merely touches someone else's edge
#     (`UPDATE cites:x SET note = 1`) lands on it. Measured: a promoter got past a written_by-based own-evidence
#     check that way. What counts is who CREATED the link, recorded once, on the CREATE event, in a table
#     no credential can write.
#   * `in` and `out` of a relation cannot be changed by UPDATE (silently kept), so a created link cannot be
#     re-pointed at other evidence afterwards.
#   * several events on one table run in name order, and a THROW in any of them aborts the whole write,
#     including what earlier events wrote.

ROW_WRITER = "row_writer"
LINK_CREATOR = "link_creator"
# What each independence refusal says right after "<marker> <rule>: ...", so a caller can tell WHY.
INDEPENDENCE_REASONS = {
    "writer": "only by a signed-in credential that has not written the row",
    "principal": "only by a credential bound to a declared principal that has written no part of the row",
    "own_evidence": "only by a credential that itself created at least one of the evidence links the rule counts",
}


def independence_reason_in(message):
    """'writer', 'principal' or 'own_evidence' for an independence refusal, or None."""
    text = str(message or "")
    if not independence_rule_in(text):
        return None
    for reason, words in INDEPENDENCE_REASONS.items():
        if words in text:
            return reason
    return None


def _writer_tables(inst):
    """Tables whose writers are recorded in row_writer: every table under a principal rule, and every gated
    table when the promotion log is on (its `late` flag reads when each writer first wrote the row)."""
    tables = {rule["table"] for rule in inst.principal_rules()}
    if inst.promotion_log_enabled():
        tables |= {rule["table"] for rule in inst.gate_rules()}
    return sorted(tables)


def _own_evidence_edges(inst):
    return sorted({clause["edge"] for rule in inst.independent_rules() if config.independence_of(rule)["own_evidence"]
                   for clause in (rule.get("requires") or [])})


def writer_record_surql(inst):
    """row_writer: the first write of each row by each credential, with the credential's principal and
    provisioning time copied at that moment (so a later revocation or re-provisioning cannot rewrite who
    wrote it). link_creator: which credential, and which principal, created each evidence link."""
    lines = []
    covered = {rule["table"] for rule in inst.independent_rules()}      # independence_surql defined written_by
    tables = _writer_tables(inst)
    if inst.promotion_log_enabled():
        for table in sorted({rule["table"] for rule in inst.gate_rules()} - covered):
            lines.append("DEFINE FIELD OVERWRITE %s ON %s TYPE array<record> VALUE array::union($before ?? [], "
                         "IF $auth = NONE { [] } ELSE { [$auth] });" % (config.INDEPENDENCE_FIELD, table))
    if tables:
        lines += ["-- row_writer: who first wrote each row, copied from the credential at that moment; no credential can write it",
                  "DEFINE TABLE OVERWRITE %s SCHEMAFULL PERMISSIONS NONE;" % ROW_WRITER,
                  "DEFINE FIELD OVERWRITE row ON %s TYPE record;" % ROW_WRITER,
                  "DEFINE FIELD OVERWRITE credential ON %s TYPE record;" % ROW_WRITER,
                  "DEFINE FIELD OVERWRITE principal ON %s TYPE option<string>;" % ROW_WRITER,
                  "DEFINE FIELD OVERWRITE first_at ON %s TYPE datetime;" % ROW_WRITER,
                  "DEFINE FIELD OVERWRITE provisioned_at ON %s TYPE option<datetime>;" % ROW_WRITER]
        for table in tables:
            lines.append(
                "DEFINE EVENT OVERWRITE %s_%s ON TABLE %s WHEN $auth != NONE AND $event != 'DELETE' THEN { "
                "IF type::record('%s', [$after.id, $auth]).first_at = NONE { "
                "CREATE type::record('%s', [$after.id, $auth]) SET row = $after.id, credential = $auth, "
                "principal = $auth.principal, first_at = time::now(), provisioned_at = $auth.provisioned_at; }; };"
                % (table, ROW_WRITER, table, ROW_WRITER, ROW_WRITER))
    edges = _own_evidence_edges(inst)
    if edges:
        lines += ["-- link_creator: who created each evidence link, recorded once on CREATE; touching a link later changes nothing",
                  "DEFINE TABLE OVERWRITE %s SCHEMAFULL PERMISSIONS NONE;" % LINK_CREATOR,
                  "DEFINE FIELD OVERWRITE link ON %s TYPE record;" % LINK_CREATOR,
                  "DEFINE FIELD OVERWRITE link_in ON %s TYPE record;" % LINK_CREATOR,
                  "DEFINE FIELD OVERWRITE link_out ON %s TYPE record;" % LINK_CREATOR,
                  "DEFINE FIELD OVERWRITE credential ON %s TYPE record;" % LINK_CREATOR,
                  "DEFINE FIELD OVERWRITE principal ON %s TYPE option<string>;" % LINK_CREATOR,
                  "DEFINE FIELD OVERWRITE at ON %s TYPE datetime;" % LINK_CREATOR]
        for edge in edges:
            lines.append(
                "DEFINE EVENT OVERWRITE %s_%s ON TABLE %s WHEN $event = 'CREATE' AND $auth != NONE THEN { "
                "UPSERT type::record('%s', [$after.id]) SET link = $after.id, link_in = $after.in, link_out = $after.out, "
                "credential = $auth, principal = $auth.principal, at = time::now(); };"
                % (edge, LINK_CREATOR, edge, LINK_CREATOR))
    return lines


def _own_evidence_count(rule, inst, mode):
    """Evidence links the rule counts that the promoting credential (or, between principals, the promoting
    principal) created itself, summed over every requires clause."""
    edges = inst.edge_map()
    creator = ("type::record('%s', [id]).principal = $auth.principal" % LINK_CREATOR if mode["between"] == "principal"
               else "type::record('%s', [id]).credential = $auth" % LINK_CREATOR)
    counts = []
    for clause in rule.get("requires") or []:
        _edge_path(rule["table"], clause, inst)                 # validates the clause, raising by name
        src, dst = edges[clause["edge"]]
        path = ("->(%s WHERE %s)->%s" % (clause["edge"], creator, dst) if rule["table"] == src
                else "<-(%s WHERE %s)<-%s" % (clause["edge"], creator, src))
        where = _expand(clause.get("where"), inst)
        counts.append("count(SELECT VALUE id FROM $after.id%s%s)" % (path, " WHERE " + where if where else ""))
    return " + ".join(counts)


def independence_mode_surql(inst):
    """For every rule whose `independent:` is a mapping that asks for more than a different credential: one
    event holding the principal check, then the own-evidence check, in that order. The credential check of
    `independent: true` still runs as well (independence_surql), unchanged."""
    lines = []
    for rule in inst.independent_rules():
        mode = config.independence_of(rule)
        if mode["between"] != "principal" and not mode["own_evidence"]:
            continue
        value = _value_literal(rule["value"])
        target = "%s.%s may become %s " % (rule["table"], rule["field"], str(rule["value"]))
        body = []
        if mode["between"] == "principal":
            body.append(
                "LET $principals = array::map($before.%s ?? [], |$c| type::record('%s', [$after.id, $c]).principal); "
                "IF $auth = NONE OR $auth.principal = NONE OR array::len($principals) = 0 OR NONE INSIDE $principals "
                "OR $auth.principal INSIDE $principals { THROW %s + <string> $after.id + ' by ' + "
                "<string> ($auth ?? 'root (no identity)') + ' (principal ' + <string> ($auth.principal ?? 'none declared') "
                "+ '); principals of the row\\'s writers: ' + <string> $principals; };"
                % (config.INDEPENDENCE_FIELD, ROW_WRITER,
                   _value_literal("%s %s: %s%s; refused " % (INDEPENDENCE_MARKER, rule["id"], target,
                                                             INDEPENDENCE_REASONS["principal"]))))
        if mode["own_evidence"]:
            body.append(
                "LET $own = IF $auth = NONE { 0 } ELSE { %s }; "
                "IF $own < 1 { THROW %s + <string> $after.id + ' by ' + <string> ($auth ?? 'root (no identity)') "
                "+ '; evidence links it created that the rule counts: ' + <string> $own; };"
                % (_own_evidence_count(rule, inst, mode),
                   _value_literal("%s %s: %s%s; refused " % (INDEPENDENCE_MARKER, rule["id"], target,
                                                             INDEPENDENCE_REASONS["own_evidence"]))))
        lines.append("DEFINE EVENT OVERWRITE %s_mode ON TABLE %s WHEN $after.%s = %s AND ($before.%s ?? NONE) != %s "
                     "THEN { %s };  -- %s"
                     % (_independence_event(rule), rule["table"], rule["field"], value, rule["field"], value,
                        " ".join(body), rule["id"]))
    return lines


# ---- building the row a clause describes ----------------------------------------------------------
#
# A `forbids` clause says a row must NOT exist. To probe it the generator has to build exactly that
# row from the clause alone -- no domain words, no per-instance special cases -- and then prove to
# the datastore that what it built really does match the clause. Only a conjunction of equalities is
# buildable; anything else is REPORTED, never skipped, because a silently skipped probe is the whole
# defect class this file exists to catch.

_FIELD_IN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+IN\s+(.+)$", re.I | re.S)
_FIELD_EQ = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", re.S)
_LITERAL = re.compile(r"^(?:'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|true|false|-?\d+(?:\.\d+)?)$", re.I)


def _split_top_level(text, separator=None):
    """Split on a top-level AND (or on commas when `separator` is ','), outside quotes and brackets."""
    parts, buf, quote, depth, i = [], [], None, 0, 0
    while i < len(text):
        char = text[i]
        if quote:
            buf.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0 and separator == "," and char == ",":
            parts.append("".join(buf)); buf = []; i += 1
            continue
        elif depth == 0 and separator is None and text[i:i + 3].upper() == "AND" \
                and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")) \
                and (i + 3 >= len(text) or not (text[i + 3].isalnum() or text[i + 3] == "_")):
            parts.append("".join(buf)); buf = []; i += 3
            continue
        buf.append(char)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts]


def _list_literals(text):
    """['a', 'b'] -> ["'a'", "'b'"]; None if it is not a non-empty list of literals."""
    text = text.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return None
    inner = text[1:-1].strip()
    if not inner:
        return None
    items = _split_top_level(inner, separator=",")
    return items if all(_LITERAL.match(item) for item in items) else None


def _where_terms(where):
    """`where` as [(field, [the literals that field may take])], or (None, why not).

    One place reads the predicate, so "what satisfies it" and "what fails it" can never drift."""
    where = (where or "").strip()
    if not where:
        return [], None          # no constraint: any neighbour of the right table matches
    terms = []
    for part in _split_top_level(where):
        if not part:
            return None, "cannot build a row for %r: it has an empty term" % where
        match = _FIELD_IN.match(part)
        if match:
            items = _list_literals(match.group(2))
            if not items:
                return None, ("cannot build a row for %r: the right side of IN is not a non-empty list "
                              "of literals" % part)
            terms.append((match.group(1), items))
            continue
        match = _FIELD_EQ.match(part)
        if not match or not _LITERAL.match(match.group(2).strip()):
            return None, ("cannot build a row for %r: only `field = literal` and `field IN [literals]` "
                          "joined by AND can be turned into evidence" % part)
        terms.append((match.group(1), [match.group(2).strip()]))
    return terms, None


def _where_assignments(where):
    """The field values a row must carry to satisfy `where`.

    Returns (assignments, None) when the row can be built, or (None, why not). `where` has already
    been through _expand(), so $confirming/$usable are real lists by the time it arrives here."""
    terms, why = _where_terms(where)
    if terms is None:
        return None, why
    assignments = {}
    for field, items in terms:
        value = items[0]
        if field in assignments and assignments[field] != value:
            return None, ("cannot build a row for %r: %s would have to be both %s and %s"
                          % (where, field, assignments[field], value))
        assignments[field] = value
    return assignments, None


def _field_domain(table, field, inst):
    """Every value `table.field` may take, as datastore literals, or None when the engine cannot say.

    This is what makes a wrong-evidence row possible: to write a row that FAILS a clause you need a
    value the clause excludes, and only a field with a known domain has one. A field whose domain is
    unknown is reported untested, never guessed at."""
    declared = inst.table_fields(table)
    if declared:
        spec = declared.get(field)
        if not spec:
            return None
        if spec["one_of"]:
            return [_value_literal(v) for v in spec["one_of"]]
        return ["true", "false"] if spec["type"] == "bool" else None
    vocab = inst.allowed_vocabulary()
    known = {("unit", "type"): inst.unit_names(),
             ("unit", "status"): inst.status_values(),
             ("unit", "grade"): inst.grades(),
             ("fact", "status"): FACT_STATUSES,
             ("critique", "verdict"): CRITIQUE_VERDICTS,
             ("critique", "answered"): [True, False],
             ("conclusion", "ready"): [True, False],
             ("authority", "verified"): [True, False],
             ("label", "tier"): list(vocab["tiers"]),
             ("label", "dir"): list(vocab["directions"]),
             ("label", "role"): list(vocab["roles"])}
    values = known.get((table, field))
    return [_value_literal(v) for v in values] if values else None


# built-in fields whose values come from a vocabulary (see _field_domain): no free distinct value exists
_KNOWN_DOMAINS = {("unit", "type"), ("unit", "status"), ("unit", "grade"), ("fact", "status"),
                  ("critique", "verdict"), ("critique", "answered"), ("conclusion", "ready"),
                  ("authority", "verified"), ("label", "tier"), ("label", "dir"), ("label", "role"),
                  ("label", "grade")}


def _violating_assignments(table, where, inst):
    """The field values a row must carry to FAIL `where` while staying a valid row of `table`.

    Every term whose field has a known domain is turned around; a `where` where none can be is
    reported, because a row built from it would quietly still match and the probe would be a second
    copy of the accept probe wearing the name of a refusal."""
    terms, why = _where_terms(where)
    if terms is None:
        return None, why
    if not terms:
        return None, "the clause constrains nothing, so no evidence can fail it"
    assignments, turned = {}, []
    for field, items in terms:
        other = next((v for v in _field_domain(table, field, inst) or [] if v not in items), None)
        if other is None:
            assignments.setdefault(field, items[0])
        else:
            assignments[field] = other
            turned.append(field)
    if not turned:
        return None, ("cannot build evidence that fails %r: the engine knows no other value for %s on %s"
                      % (where, ", ".join(sorted({f for f, _ in terms})), table))
    return assignments, None


def _row_template(table, inst, unit_id=None):
    """The smallest valid row of `table`, with every value taken from the instance's vocabularies."""
    if table == "unit":
        return {"type": _value_literal(inst.unit_names()[0]), "text": "'probe'", "locator": "{p: 3}",
                "status": _value_literal(inst.usable_statuses()[0]),
                "grade": _value_literal(inst.confirming_grades()[0]), "hash": "'h'"}
    if table == "label":
        if not unit_id:
            raise GateError("a label row needs a unit to hang on")
        tiers = list(inst.allowed_vocabulary()["tiers"])
        return {"unit": unit_id, "tier": str(tiers[0]), "issues": "[]", "targets": "[]",
                "chunk": "'probe'", "run": "'probe'"}
    if table == "fact":
        return {"statement": "'probe evidence'", "status": "'proposed'"}
    if table == "conclusion":
        return {"statement": "'probe evidence'", "ready": "false"}
    if table == "authority":
        return {"citation": "'probe evidence'", "verified": "false"}
    if table == "critique":
        return {"lens": "'probe'", "head": "'probe head'", "verdict": _value_literal(CRITIQUE_VERDICTS[-1]),
                "answered": "true"}
    if table == "answer":
        return {"text": "'probe answer'"}
    declared = inst.table_fields(table)
    if declared:   # a table the instance declared: every required field, at its blandest value
        return {name: _default_value(spec) for name, spec in declared.items() if not spec["optional"]}
    raise GateError("no row template for table %s" % table)


def _field_type(table, field, inst):
    """(type, enumerated values or None) for a field, built-in or declared; (None, None) if unknown."""
    declared = inst.table_fields(table)
    if field in declared:
        return declared[field]["type"], declared[field]["one_of"]
    kind = (config.BUILT_IN_FIELDS.get(table) or {}).get(field)
    return kind, None


def _distinct_literal(table, field, inst, seed):
    """A value of `table.field` no other probe row shares, derived from `seed`; None when the engine
    cannot make one (an enumerated, boolean, record, array or object field)."""
    kind, one_of = _field_type(table, field, inst)
    if one_of or (table, field) in _KNOWN_DOMAINS:
        return None
    digest = int.from_bytes(__import__("hashlib").sha1(str(seed).encode("utf-8")).digest()[:4], "big")
    if kind == "string":
        return _value_literal("zz_probe %s" % seed)
    if kind == "int":
        return str(1000000000 + digest)
    if kind == "float":
        return "%d.5" % (1000000000 + digest)
    if kind == "datetime":
        return "<datetime> '2000-01-01T00:00:00Z' + <duration> '%ds'" % digest
    return None


def _unique_overrides(table, row_id, inst):
    """A distinct value for every unique field of `table`, so two probe rows never collide on an index
    that has nothing to do with what they are probing."""
    out = {}
    for field in inst.unique_fields().get(table, []):
        literal = _distinct_literal(table, field, inst, row_id)
        if literal is not None:
            out[field] = literal
    return out


def _create(row_id, table, inst, overrides=None, unit_id=None):
    fields = _row_template(table, inst, unit_id=unit_id)
    fields.update(_unique_overrides(table, row_id, inst))
    fields.update(overrides or {})
    return "CREATE %s SET %s;" % (row_id, ", ".join("%s=%s" % (k, v) for k, v in fields.items()))


def _relate(near, edge, far, inst):
    """One relation, carrying whatever fields the instance declared the edge must have. An edge with
    a required field is refused by the datastore without it, so the probe supplies them."""
    values = {f: _default_value(s) for f, s in inst.edge_fields(edge).items() if not s["optional"]}
    return "RELATE %s->%s->%s%s;" % (near, edge, far,
                                     (" SET " + ", ".join("%s=%s" % kv for kv in values.items()))
                                     if values else "")


# ---- probes --------------------------------------------------------------------------------------

def _probe_ids(rule):
    base = "zz_probe_%s" % rule["id"]
    return {"row": "%s:%s" % (rule["table"], base), "ok": "unit:%s_ok" % base}


def _rule_row(rule, inst, ids):
    """The row the probe will try to promote: a valid row of the rule's table that has NOT been
    promoted yet. A row already carrying the gated value would make the refusal meaningless."""
    table, field = rule["table"], rule["field"]
    fields = _row_template(table, inst, unit_id=ids["ok"])
    fields.update(_unique_overrides(table, ids["row"], inst))
    target = _value_literal(rule["value"])
    if fields.get(field) == target:
        other = next((v for v in _field_domain(table, field, inst) or [] if v != target), None)
        if other is None:
            raise GateError("gate %s: %s.%s has no value other than %s, so no probe can start from a "
                            "row that has not been promoted" % (rule["id"], table, field, target))
        fields[field] = other
    return "CREATE %s SET %s;" % (ids["row"], ", ".join("%s=%s" % kv for kv in fields.items()))


def probes(inst):
    """For every rule: setup, a write that must be REFUSED, the fix, the same write ACCEPTED, cleanup.

    Nothing below names a table, an edge or a field: every row and every relation is built from the
    clause and from the instance's own declarations, so an edge or a table an instance declared is
    probed exactly as a built-in one is. What cannot be built is recorded with its reason and judged
    as untested; it is never left out."""
    out = []
    for rule in inst.gate_rules():
        ids = _probe_ids(rule)
        table, field, value = rule["table"], rule["field"], _value_literal(rule["value"])
        cleanup = []
        # a unit anything may hang on, then the row whose promotion is under test
        setup = [_create(ids["ok"], "unit", inst), _rule_row(rule, inst, ids)]
        attempt = "UPDATE %s SET %s = %s;" % (ids["row"], field, value)
        # the fix: for every requires clause, `min` neighbours that really do match it
        evidence = [_evidence_plan(rule, index, clause, inst, ids)
                    for index, clause in enumerate(rule.get("requires") or [])]
        fix = [stmt for plan in evidence if plan["built"] for stmt in plan["create"] + plan["relate"]]
        # a second, sharper refusal: neighbours of the right table that FAIL the clause must not count
        constrained = [c for c in (rule.get("requires") or []) if c.get("where")]
        wrong_plans = [_wrong_plan(rule, index, clause, inst, ids)
                       for index, clause in enumerate(rule.get("requires") or []) if clause.get("where")]
        # all or nothing: a half-built wrong-evidence set could be refused for the clause it did
        # manage to violate and report green for the one it did not
        wrong = ([stmt for plan in wrong_plans for stmt in plan["create"] + plan["relate"]]
                 if wrong_plans and all(p["built"] for p in wrong_plans) else [])
        # a third direction: for every forbids clause, the row it forbids, built from the clause
        forbids, forbidden = [], []
        for index, clause in enumerate(rule.get("forbids") or []):
            forbids.append(_clause_text(clause, inst))
            forbidden.append(_forbidden_plan(rule, index, clause, inst, ids))
        for edge in inst.edge_map():          # edges first: a relation outlives neither endpoint here
            cleanup.append("DELETE %s WHERE record::id(in) CONTAINS 'zz_probe_%s' "
                           "OR record::id(out) CONTAINS 'zz_probe_%s';" % (edge, rule["id"], rule["id"]))
        for tab in reversed(inst.node_tables()):   # unit last: everything else may point at it
            cleanup.append("DELETE %s WHERE record::id(id) CONTAINS 'zz_probe_%s';" % (tab, rule["id"]))
        mode = config.independence_of(rule)
        independent = bool(mode)
        staged = [stmt for plan in evidence + wrong_plans + forbidden for stmt in plan["create"]
                  if isinstance(stmt, CredentialStatement)]
        if independent or staged or (rule.get("forbids") and blind_role(rule, inst)):   # probe credentials
            cleanup.append("DELETE agent WHERE record::id(id) CONTAINS 'zz_probe_%s';" % rule["id"])
        cleanup.extend(_writer_record_cleanup(rule, inst))
        unpromoted = re.search(r"\b%s=([^,]+?)(?:, |;$)" % re.escape(field), setup[1])
        out.append({"rule": rule["id"], "independent": independent, "mode": mode,
                    # A rule that only forbids has nothing to be missing: with no forbidden row present the write
                    # is SUPPOSED to go through. Its refusal is the forbidden-evidence probe's, not the first attempt's.
                    "forbids_only": bool(rule.get("forbids")) and not rule.get("requires"),
                    "unpromote": ("UPDATE %s SET %s = %s;" % (ids["row"], field, unpromoted.group(1))
                                  if unpromoted else None),
                    "setup": setup, "attempt": attempt, "wrong": wrong, "fix": fix,
                    "cleanup": cleanup, "constrained": bool(constrained),
                    "constraints": sorted(_expand(c.get("where"), inst) for c in constrained),
                    "evidence": evidence, "wrong_plans": wrong_plans,
                    "forbids": forbids, "forbidden": forbidden})
    return out


def _writer_record_cleanup(rule, inst):
    """Probe rows' entries in row_writer and link_creator. Never the promotion log: a probe's promotion
    happened, and removing its entry is exactly the edit the chain exists to expose."""
    out = []
    if _writer_tables(inst):
        out.append("DELETE %s WHERE record::id(row) CONTAINS 'zz_probe_%s';" % (ROW_WRITER, rule["id"]))
    if _own_evidence_edges(inst):
        out.append("DELETE %s WHERE record::id(link_in) CONTAINS 'zz_probe_%s' OR record::id(link_out) CONTAINS "
                   "'zz_probe_%s';" % (LINK_CREATOR, rule["id"], rule["id"]))
    return out


def _clause_text(clause, inst):
    where = _expand(clause.get("where"), inst)
    return "%s%s" % (clause.get("edge"), " where %s" % where if where else "")


MAX_EVIDENCE_CHAIN = 4   # rule A's evidence gated by rule B's, gated by C's ... a cycle must end somewhere


def _staged_create(row, table, inst, assignments, ids, depth):
    """Create `row` carrying `assignments`, even when one of them is itself behind a gate.

    Returns (statements, removals, problem). The naive CREATE writes every assignment at once, so a row
    whose value is gated -- a fact created already `confirmed` -- is refused by its OWN gate, because the
    evidence that gate requires does not exist yet. Found by building another session's pattern through
    the tables:/edges: contract: `contention.disposition = 'verified' requires rests_on -> fact WHERE
    status = 'confirmed'` could not be probed at all, since no probe can write a confirmed fact in one
    statement. So a gated assignment is STAGED: create the row at an ungated value, build the evidence
    its own gate requires (recursively, through this same planner), then promote it. The promotion is
    therefore made by the real gate, not around it -- a probe that bypassed the inner gate would prove
    the outer rule against evidence the datastore would never let exist.
    """
    assignments = dict(assignments or {})
    gating = [r for r in inst.gate_rules()
              if r["table"] == table and r["field"] in assignments
              and _value_literal(r["value"]) == assignments[r["field"]]]
    if not gating:
        return [_create(row, table, inst, assignments, unit_id=ids["ok"])], [], None
    independent = [r for r in gating if config.independence_of(r)]
    if len(independent) > 1:
        return None, None, ("%s would have to be promoted past rules %s, each needing a promoter independent of the "
                            "row's writers; one proposer and one promoter per staged row is all the probe builds, "
                            "so this evidence is not built" % (row, ", ".join(r["id"] for r in independent)))
    inner = independent[0] if independent else None
    if inner is not None:
        role, why = _probe_role(inst, inner)
        if role is None:
            return None, None, ("%s would have to be promoted past rule %s, which needs a promoter independent of "
                                "the row's writers, and no probe credential could make that promotion (%s), so this "
                                "evidence is not built" % (row, inner["id"], why))
    if depth >= MAX_EVIDENCE_CHAIN:
        return None, None, ("the evidence chain is deeper than %d gated rules (%s); a cycle, or a chain "
                            "too long to probe" % (MAX_EVIDENCE_CHAIN, " -> ".join(r["id"] for r in gating)))
    promote = {}
    for r in gating:
        promote[r["field"]] = assignments.pop(r["field"])
    template = _row_template(table, inst, unit_id=ids["ok"])
    for field, gated in promote.items():
        if template.get(field) not in (None, gated):
            continue                          # the blandest value is already ungated: leave it
        other = next((v for v in _field_domain(table, field, inst) or [] if _value_literal(v) != gated), None)
        if other is None:
            return None, None, ("%s.%s has no value other than %s to start from, so %s cannot be "
                                "created unpromoted" % (table, field, gated, row))
        assignments[field] = _value_literal(other)
    statements = [_create(row, table, inst, assignments, unit_id=ids["ok"])]
    removals = []
    local = row.split(":", 1)[1]
    links = []
    for r in gating:
        for index, clause in enumerate(r.get("requires") or []):
            sub = _rows_plan(r, index, clause, inst, {"row": row, "ok": ids["ok"]}, "via",
                             *_where_assignments(_expand(clause.get("where"), inst)),
                             prefix=local, depth=depth + 1)
            if not sub["built"]:
                return None, None, ("to promote %s past rule %s it needs %s, and that could not be built: %s"
                                    % (row, r["id"], sub["clause"], sub["reason"]))
            statements += sub["create"] + sub["relate"]
            removals += sub["remove"]
            links += [(sub["edge"], stmt) for stmt in sub["relate"]]
    promotion = "UPDATE %s SET %s;" % (row, ", ".join("%s = %s" % kv for kv in promote.items()))
    if inner is None:
        statements.append(promotion)
        return statements, removals, None
    # Behind an independence rule the promotion is made the way a real one must be: the row is written by one
    # provisioned credential and promoted by another (between principals, of another principal), and when the rule
    # asks for the promoter's own evidence, the promoter first creates one of the links it counts itself.
    stem = "%s_s%s" % (ids["ok"].split(":", 1)[1][:-len("_ok")],
                       __import__("hashlib").sha1(str(inner["id"]).encode("utf-8")).hexdigest()[:6])
    statements[0] = CredentialStatement(statements[0], inner, "proposer", stem)
    if config.independence_of(inner)["own_evidence"]:
        writes = inst.access_roles()[role]["write"]
        own = next((stmt for edge, stmt in links if edge in writes), None)
        if own is None:
            return None, None, ("%s would have to be promoted past rule %s, which needs the promoter's own evidence, "
                                "and role %s writes none of the links its evidence plan relates" % (row, inner["id"], role))
        statements.append(CredentialStatement(own, inner, "promoter", stem))
    statements.append(CredentialStatement(promotion, inner, "promoter", stem))
    return statements, removals, None


class CredentialStatement(str):
    """A probe statement that a provisioned credential must run, not root: `key` ("proposer" or "promoter") of the
    probe credentials for independence rule `rule`, named under `stem`.

    It is still the statement's text, so a reader that runs it as root anyway is refused by the independence
    event (root has no identity) and the probe reports a setup error: it can fail, never pass by mistake."""

    def __new__(cls, text, rule, key, stem):
        obj = str.__new__(cls, text)
        obj.rule, obj.key, obj.stem = rule, key, stem
        return obj


class ProbeRunner:
    """Runs probe statements, each on the connection it names: root for a plain statement, a provisioned probe
    credential for a CredentialStatement. `prepare` provisions every credential a list of statements needs before
    any of them runs, and returns why not when it cannot."""

    def __init__(self, inst, db):
        self.inst, self.db, self.identities = inst, db, {}

    def prepare(self, statements):
        for stmt in statements:
            if not isinstance(stmt, CredentialStatement) or stmt.stem in self.identities:
                continue
            identities, why = _probe_identities(self.inst, self.db, stmt.rule, stem=stmt.stem)
            if identities is None:
                return ("staged evidence behind rule %s needs a row written by one probe credential and promoted by "
                        "another, and they could not be provisioned: %s" % (stmt.rule["id"], why))
            self.identities[stmt.stem] = identities
        return None

    def sql(self, stmt):
        if isinstance(stmt, CredentialStatement):
            if stmt.stem not in self.identities:
                why = self.prepare([stmt])
                if why:
                    from xscholar import db as dbmod
                    raise dbmod.DbError(why)
            return self.identities[stmt.stem][stmt.key].sql(stmt)
        return self.db.sql(stmt)

    def try_sql(self, stmt):
        return self.db.try_sql(stmt)


def credential_statements(probe):
    """Every statement in a probe plan that a provisioned credential must run."""
    plans = list(probe.get("evidence") or []) + list(probe.get("wrong_plans") or []) + list(probe.get("forbidden") or [])
    return [stmt for plan in plans for stmt in plan.get("create") or [] if isinstance(stmt, CredentialStatement)]


def _rows_plan(rule, index, clause, inst, ids, tag, assignments, why, prefix=None, depth=0):
    """How to make `min` neighbours of the rule's row for one clause, and how to take them away.

    Everything comes from the clause: the edge decides the table and the direction of the RELATE,
    `assignments` decides the field values, `min` decides how many rows. A clause this cannot build
    carries `built: False` and the reason, which judge() turns into a failure -- a clause that
    quietly generated nothing is exactly how the wrong-evidence probe stopped running.

    `prefix` and `depth` are for staged evidence (_staged_create): a nested row's id must still carry
    the outer rule's `zz_probe_<id>` so the outer cleanup removes it."""
    table = rule["table"]
    where = _expand(clause.get("where"), inst)
    _, other = _edge_path(table, clause, inst)
    count = max(1, int(clause.get("min", 1)))
    plan = {"clause": _clause_text(clause, inst), "where": where or None, "edge": clause["edge"],
            "table": other, "count": count, "built": False, "reason": "", "rows": [],
            "create": [], "relate": [], "remove": [],
            "present": "RETURN " + _traversal(table, clause, inst, subject=ids["row"])}
    if other == "label":
        raise GateError("gate %s: labels never gate promotion" % rule["id"])
    if assignments is None:
        plan["reason"] = why
        return plan
    try:
        stem = prefix or "zz_probe_%s" % rule["id"]
        rows = ["%s:%s_%s%d_%d" % (other, stem, tag, index, k) for k in range(count)]
        creates, nested = [], []
        for row in rows:
            statements, removals, problem = _staged_create(row, other, inst, assignments, ids, depth)
            if problem:
                plan["reason"] = problem
                return plan
            creates += statements
            nested += removals
    except GateError as exc:
        plan["reason"] = "cannot build a %s row for %s: %s" % (other, plan["clause"], exc)
        return plan
    drop_edges, drop_rows = [], nested
    for row in rows:
        near, far = (ids["row"], row) if table == inst.edge_map()[clause["edge"]][0] else (row, ids["row"])
        plan["relate"].append(_relate(near, clause["edge"], far, inst))
        drop_edges.append("DELETE %s WHERE in = %s AND out = %s;" % (clause["edge"], near, far))
        drop_rows.append("DELETE %s;" % row)
    plan.update({"rows": rows, "create": creates, "remove": drop_edges + drop_rows, "built": True})
    return plan


def _forbidden_plan(rule, index, clause, inst, ids):
    """The row one `forbids` clause excludes: it must match the clause, and it must be removable."""
    where = _expand(clause.get("where"), inst)
    return _rows_plan(rule, index, clause, inst, ids, "forbid", *_where_assignments(where))


def _evidence_plan(rule, index, clause, inst, ids):
    """The rows one `requires` clause is satisfied by. `present` is then asked of the datastore after
    the fix: evidence the clause does not actually match would make the accept probe prove nothing."""
    where = _expand(clause.get("where"), inst)
    return _rows_plan(rule, index, clause, inst, ids, "fix", *_where_assignments(where))


def _wrong_plan(rule, index, clause, inst, ids):
    """Rows of the right table that FAIL the clause. The gate must still refuse the write with them
    in place, which is what proves the constraint is enforced and not merely written down."""
    where = _expand(clause.get("where"), inst)
    return _rows_plan(rule, index, clause, inst, ids, "wrong",
                      *_violating_assignments(_edge_path(rule["table"], clause, inst)[1], where, inst))


def spec(inst):
    """What the rules claim, independent of any database: the shape a baseline pins down."""
    out = {"instance": inst.id, "rules": {}, "edges": {}}
    edges = inst.edge_map()
    used = sorted({clause.get("edge") for rule in inst.gate_rules() for kind in ("requires", "forbids")
                   for clause in (rule.get(kind) or [])})
    # What an edge JOINS is half of what a clause means: leave it out of the baseline and an instance
    # can repoint its own edge at another table and every rule using it silently changes target.
    out["edges"] = {edge: {"from": edges[edge][0], "to": edges[edge][1]} for edge in used if edge in edges}
    for rule in inst.gate_rules():
        clauses = []
        for kind in ("requires", "forbids"):
            for clause in rule.get(kind) or []:
                clauses.append({"kind": kind, "edge": clause.get("edge"), "min": clause.get("min", 1),
                                "where": _expand(clause.get("where"), inst) or None})
        out["rules"][rule["id"]] = {"table": rule["table"], "field": rule["field"],
                                    "value": rule["value"], "clauses": sorted(
                                        clauses, key=lambda c: (c["kind"], str(c["edge"]), str(c["where"])))}
        mode = config.independence_of(rule)
        if mode:                                # written only when declared: older baselines stay valid
            out["rules"][rule["id"]]["independent"] = True
            if mode != {"between": "credential", "own_evidence": False}:
                out["rules"][rule["id"]]["independence"] = dict(mode)
    if inst.promotion_log_enabled():
        out["promotion_log"] = True
    if inst.unique_fields():
        out["unique"] = {table: sorted(fields) for table, fields in sorted(inst.unique_fields().items())}
    return out


def _independence_mode_weakened(rule_id, was, now):
    """A rule that asked for a different principal, or for the promoter's own evidence, and no longer does."""
    failures = []
    was_mode = was.get("independence") or {}
    now_mode = now.get("independence") or ({"between": "credential", "own_evidence": False}
                                           if now.get("independent") is True else {})
    if was_mode.get("between") == "principal" and now_mode.get("between") != "principal":
        failures.append("rule %s no longer requires a promoter from a different principal: two credentials held "
                        "by one person or process may now propose and promote" % rule_id)
    if was_mode.get("own_evidence") is True and now_mode.get("own_evidence") is not True:
        failures.append("rule %s no longer requires the promoter's own evidence: a promoter may now pass on links "
                        "someone else created" % rule_id)
    return failures


def _clause_key(clause):
    return (clause["kind"], clause["edge"], clause["where"])


def compare_baseline(baseline, report, current_spec):
    """Every way a gate can get weaker between runs. Returns a list of plain-language failures."""
    failures = []
    old_rules = (baseline.get("spec") or {}).get("rules") or {}
    new_rules = current_spec["rules"]
    if not old_rules:
        # A baseline that pins nothing agrees with every config, including one with no gates at all.
        # Read as "unchanged or stronger" it is worse than no baseline, because it looks like a check.
        return ["the baseline pins no rules at all, so it cannot fail and is not a baseline; "
                "write one with --write-baseline and commit it"]
    for rule_id, was in old_rules.items():
        now = new_rules.get(rule_id)
        if now is None:
            failures.append("rule %s is in the baseline and gone from the config" % rule_id)
            continue
        # The clauses can be untouched while the rule stops guarding the write it was written for:
        # move it to another table or field, or point it at another value, and nothing it requires
        # applies to the promotion any more.
        for part in ("table", "field", "value"):
            if part in was and was[part] != now.get(part):
                failures.append("rule %s now gates a different write: %s was %r, is now %r"
                                % (rule_id, part, was[part], now.get(part)))
        # Clauses are matched one for one, not by set membership. Two clauses that read the same are
        # two requirements, and dropping one of them is a weakening a set would hide.
        unmatched = list(now["clauses"])
        for clause in was["clauses"]:
            same = [c for c in unmatched if _clause_key(c) == _clause_key(clause)]
            if not same:
                kind, edge, where = _clause_key(clause)
                failures.append("rule %s lost a %s clause on %s%s"
                                % (rule_id, kind, edge, " where %s" % where if where else ""))
                continue
            best = max(same, key=lambda c: c["min"])
            unmatched.remove(best)
            if best["min"] < clause["min"]:
                failures.append("rule %s lowered min on %s from %s to %s"
                                % (rule_id, clause["edge"], clause["min"], best["min"]))
        if was.get("independent") is True and now.get("independent") is not True:
            failures.append("rule %s no longer requires an independent promoter: the credential that wrote "
                            "a row may now promote it itself" % rule_id)
        failures.extend(_independence_mode_weakened(rule_id, was, now))
    if (baseline.get("spec") or {}).get("promotion_log") is True and current_spec.get("promotion_log") is not True:
        failures.append("the promotion log is in the baseline and switched off in the config: promotions are no "
                        "longer recorded in the hash chain")
    for table, fields in ((baseline.get("spec") or {}).get("unique") or {}).items():
        now_fields = (current_spec.get("unique") or {}).get(table) or []
        for field in fields:
            if field not in now_fields:
                failures.append("unique %s.%s is in the baseline and gone from the config: duplicates of it "
                                "are no longer refused" % (table, field))
    for edge, was in ((baseline.get("spec") or {}).get("edges") or {}).items():
        now = (current_spec.get("edges") or {}).get(edge)
        if now and now != was:
            failures.append("edge %s now joins %s and %s; the baseline was taken when it joined %s and %s, "
                            "so every clause on it points somewhere else now"
                            % (edge, now["from"], now["to"], was["from"], was["to"]))
    old_entries = {e["rule"]: e for e in (baseline.get("report") or {}).get("rules") or []}
    if report is not None:   # report is optional: the spec half needs no database
        now_entries = {e["rule"]: e for e in report.get("rules") or []}
        for entry in now_entries.values():
            was = old_entries.get(entry["rule"])
            if not was:
                continue
            for flag in ("reject", "wrong_evidence_reject", "accept", "forbidden_evidence_reject",
                         "self_promotion_reject", "root_promotion_reject", "sibling_promotion_reject",
                         "borrowed_evidence_reject"):
                if was.get(flag) is True and entry.get(flag) is not True:
                    failures.append("rule %s: %s was true in the baseline, now %r"
                                    % (entry["rule"], flag, entry.get(flag)))
        # A rule that was probed and now produces no probe result at all is the same loss as a flag
        # going false, and reading only the entries that arrived would never notice it.
        for rule_id in old_entries:
            if rule_id in new_rules and rule_id not in now_entries:
                failures.append("rule %s was probed in the baseline and this run produced no probe "
                                "result for it" % rule_id)
        old_unique = {(e["table"], e["field"]): e for e in (baseline.get("report") or {}).get("unique") or []}
        now_unique = {(e["table"], e["field"]): e for e in report.get("unique") or []}
        for key, was in old_unique.items():
            now = now_unique.get(key)
            if now is None:
                failures.append("unique %s.%s was probed in the baseline and this run produced no probe "
                                "result for it" % key)
                continue
            for flag in ("duplicate_reject", "distinct_accept"):
                if was.get(flag) is True and now.get(flag) is not True:
                    failures.append("unique %s.%s: %s was true in the baseline, now %r"
                                    % (key[0], key[1], flag, now.get(flag)))
    return failures


def judge(entry, probe):
    """Decide whether one probe result counts as behaviour. Mutates `entry` with the reasons.

    A constrained rule with no wrong-evidence result is a suite that stopped testing the
    constraint, which is indistinguishable from "not applicable" unless we say so here. A rule that
    forbids something and produced no forbidden-evidence result is the same failure on the other
    half of the gate expression: nothing checked that the datastore refuses the write while the
    forbidden row exists, and nothing checked that taking the row away lets it through again.
    """
    entry["constrained"] = probe["constrained"]
    entry["constraints"] = probe["constraints"]
    # The accept half: every requires clause must have produced evidence, and the datastore must
    # agree that what was written really matches the clause. An accept probe whose evidence the
    # clause does not match proves the gate takes the write for some other reason.
    evidence = entry.get("evidence") or []
    unbuilt = [r for r in evidence if not r.get("built")]
    unmatched = [r for r in evidence if r.get("built") and r.get("satisfied") is not True]
    if unbuilt or unmatched:
        entry["missing_accept_probe"] = (
            "the accept probe did not supply evidence for %d of %d requires clause(s) (%s): nothing "
            "checked that the datastore takes the write once the rule is satisfied"
            % (len(unbuilt) + len(unmatched), len(evidence),
               "; ".join([r.get("reason") or "no reason recorded" for r in unbuilt]
                         + ["%s: the rows written do not match the clause" % r["clause"] for r in unmatched])))
    wrong = entry.get("wrong_evidence") or []
    failed_to_fail = [r for r in wrong if r.get("really_fails") is not True]
    if probe["constrained"] and (entry["wrong_evidence_reject"] is None or failed_to_fail):
        reasons = "; ".join(r.get("reason") or "%s: the rows written did not fail the clause" % r["clause"]
                            for r in failed_to_fail)
        entry["missing_probe"] = ("this rule constrains its evidence (%s) but no wrong-evidence probe ran: "
                                  "the suite is not testing the constraint%s"
                                  % ("; ".join(probe["constraints"]), " (%s)" % reasons if reasons else ""))
    entry["forbids"] = list(probe.get("forbids") or [])
    entry.setdefault("forbidden_evidence_reject", None)
    results = entry.get("forbidden_evidence") or []
    if entry["forbids"]:
        built = [r for r in results if r.get("built")]
        if len(built) < len(entry["forbids"]):
            reasons = [r.get("reason") or "no reason recorded" for r in results if not r.get("built")]
            entry["missing_forbidden_probe"] = (
                "this rule forbids %s but %d of %d forbidden-evidence probes did not run (%s): nothing "
                "checked that the datastore refuses the write while the forbidden row exists"
                % ("; ".join(entry["forbids"]), len(entry["forbids"]) - len(built), len(entry["forbids"]),
                   "; ".join(reasons) or "none were generated"))
        if built:
            entry["forbidden_evidence_reject"] = all(
                r.get("evidence_present") and r.get("reject") and r.get("cleared") and r.get("restored_accept")
                for r in built)
            blind = [r for r in entry.get("blind_forbidden") or [] if r.get("built")]
            if entry.get("blind_probe_error") or not all(
                    r.get("evidence_present") and r.get("blind_reject") and r.get("cleared") for r in blind):
                entry["forbidden_evidence_reject"] = False
    if probe.get("independent"):
        # Both refusals are required, and each must be the independence rule's own refusal: a proposer
        # refused for missing evidence proves nothing about who may promote.
        entry["independent"] = True
        entry.setdefault("self_promotion_reject", None)
        entry.setdefault("root_promotion_reject", None)
        if not entry.get("missing_independence_probe") and (entry["self_promotion_reject"] is None
                                                            or entry["root_promotion_reject"] is None):
            entry["missing_independence_probe"] = (
                "this rule requires an independent promoter, but the probe did not try the promotion as "
                "the row's own writer and as root: nothing checked who may make the change")
        _judge_independence_mode(entry, probe)
    if probe.get("forbids_only"):
        # Nothing is required, so nothing can be missing and a write with no forbidden row in place must go through
        # (the accept probe). The refusal this rule exists for is the one made while a forbidden row is present.
        entry["reject"] = entry.get("forbidden_evidence_reject") is True
    behaved = bool(entry["reject"] and entry["accept"]
                   and entry.get("sibling_promotion_reject") in _required_flag(probe, "principal")
                   and entry.get("borrowed_evidence_reject") in _required_flag(probe, "own_evidence")
                   and entry["wrong_evidence_reject"] in (None, True)
                   and entry.get("forbidden_evidence_reject") in (None, True)
                   and entry.get("self_promotion_reject") in ((True,) if probe.get("independent") else (None, True))
                   and entry.get("root_promotion_reject") in ((True,) if probe.get("independent") else (None, True))
                   and not entry.get("missing_independence_probe")
                   and not entry.get("missing_probe")
                   and not entry.get("missing_forbidden_probe")
                   and not entry.get("missing_accept_probe")
                   and not entry.get("setup_error"))
    entry["behaved"] = behaved
    return behaved


def _required_flag(probe, part):
    """The values a mode flag may hold for the rule to count as behaved: True when the rule's mode asks for
    that part, anything that is not a failure otherwise."""
    mode = probe.get("mode") or {}
    asked = mode.get("between") == "principal" if part == "principal" else mode.get("own_evidence") is True
    return (True,) if asked else (None, True)


def _judge_independence_mode(entry, probe):
    """A rule asking for a different principal must have refused a second credential of the proposer's own
    principal; one asking for the promoter's own evidence must have refused a promoter whose only evidence was
    created by someone else. Each by that rule's own refusal, for that reason."""
    missing = []
    if (probe.get("mode") or {}).get("between") == "principal":
        entry.setdefault("sibling_promotion_reject", None)
        if entry["sibling_promotion_reject"] is None:
            missing.append("a second credential bound to the proposer's principal was never tried")
    if (probe.get("mode") or {}).get("own_evidence"):
        entry.setdefault("borrowed_evidence_reject", None)
        if entry["borrowed_evidence_reject"] is None:
            missing.append("a promoter holding only evidence someone else created was never tried")
    if missing and not entry.get("missing_independence_probe"):
        entry["missing_independence_probe"] = ("this rule's independence goes beyond a different credential, but %s: "
                                               "nothing checked that part of the rule" % "; and ".join(missing))


def _own_link(probe, inst, role):
    """One evidence RELATE the probe credentials can make themselves: from the first built evidence plan on an
    edge the probe role writes. None when there is none."""
    writes = (inst.access_roles().get(role) or {}).get("write") or []
    for plan in probe["evidence"]:
        if plan["built"] and plan["edge"] in writes and plan["relate"]:
            return plan["relate"][0]
    return None


def _run_independence_mode(probe, entry, inst, db, identities):
    """After the fix (evidence created by root): the borrowed-evidence refusal, then every probe credential
    creates a link of its own, then the sibling refusal. Credentials create their own links BEFORE the
    self and sibling attempts, so those can only be refused for who they are, not for missing own evidence."""
    mode, rule_id = probe["mode"], probe["rule"]
    promoter = identities["promoter"]
    if mode["own_evidence"]:
        ok, message = promoter.try_sql(probe["attempt"])
        entry["borrowed_evidence_reject"] = (not ok and independence_rule_in(message) == rule_id
                                             and independence_reason_in(message) == "own_evidence")
        entry["borrowed_evidence_error"] = message
        if ok and probe["unpromote"]:
            db.sql(probe["unpromote"])
        link = _own_link(probe, inst, identities["role"])
        if link is None:
            entry["missing_independence_probe"] = ("this rule requires the promoter's own evidence and no evidence "
                                                   "link could be built on an edge role %s writes" % identities["role"])
            return False
        for key in ("proposer", "sibling", "promoter"):
            if identities.get(key) is not None:
                identities[key].sql(link)
    if mode["between"] == "principal":
        ok, message = identities["sibling"].try_sql(probe["attempt"])
        entry["sibling_promotion_reject"] = (not ok and independence_rule_in(message) == rule_id
                                             and independence_reason_in(message) == "principal")
        entry["sibling_promotion_error"] = message
        if ok and probe["unpromote"]:
            db.sql(probe["unpromote"])
    return True


def run_probes(inst, db):
    """Run every probe pair. Return a report; never leave probe rows behind."""
    from xscholar import db as dbmod
    report = {"instance": inst.id, "rules": [], "rejected_ok": 0, "accepted_ok": 0,
              "forbidden_refused_ok": 0, "forbids_untested": 0, "requires_untested": 0,
              "independence_refused_ok": 0, "independence_untested": 0, "misbehaved": 0,
              "independence_rules": 0, "independence_failed": 0, "unpassable_roles": unpassable_roles(inst)}
    rules = {rule["id"]: rule for rule in inst.gate_rules()}
    for probe in probes(inst):
        entry = {"rule": probe["rule"], "reject": None, "wrong_evidence_reject": None, "accept": None,
                 "forbidden_evidence_reject": None, "forbidden_evidence": [], "evidence": [],
                 "wrong_evidence": [], "setup_error": ""}
        # Who writes and who promotes. For an ordinary rule, the probe's own connection does both. For
        # a rule that requires an independent promoter, root can never promote (it has no identity),
        # so every attempt below is made by a second credential that did not write the row -- which
        # keeps each refusal about the evidence -- and the writer and root are tried separately.
        writer, promoter, identities = db, db, None
        if probe["independent"]:
            entry.update({"self_promotion_reject": None, "root_promotion_reject": None})
            identities, why = _probe_identities(inst, db, rules[probe["rule"]])
            if identities is None:
                entry["missing_independence_probe"] = (
                    "this rule requires an independent promoter and the probe could not build the two "
                    "credentials it needs (%s): nothing checked the rule's evidence or who may make the "
                    "change" % why)
            else:
                writer, promoter = identities["proposer"], identities["promoter"]
        run = ProbeRunner(inst, db)      # staged evidence behind an independence rule runs on probe credentials
        try:
            # A write the probe itself cannot make used to end the whole run with a traceback, so the
            # rules after it were never probed and the run had no report to show. It is one rule's
            # probe that did not happen: record it, keep the flags it did get, and carry on.
            try:
                if probe["independent"] and identities is None:
                    raise _NotRun()
                staged_problem = run.prepare(credential_statements(probe))
                if staged_problem:
                    entry["setup_error"] = staged_problem
                    raise _NotRun()
                db.sql(probe["setup"][0])
                writer.sql(probe["setup"][1])          # the row under test, written by its proposer
                for stmt in probe["setup"][2:]:
                    db.sql(stmt)
                if not probe.get("forbids_only"):
                    # judge() takes a forbids-only rule's refusal from its forbidden-evidence probe instead: with
                    # nothing forbidden in place this write is meant to be ACCEPTED, and a promoter that made it
                    # would be on the row's record of writers from then on.
                    ok, message = promoter.try_sql(probe["attempt"])
                    entry["reject"] = not ok and not independence_rule_in(message)
                if probe["wrong"]:
                    for stmt in probe["wrong"]:
                        run.sql(stmt)
                    for plan in probe["wrong_plans"]:
                        entry["wrong_evidence"].append(
                            {"clause": plan["clause"], "built": True, "reason": "",
                             "really_fails": db.sql(plan["present"], expect=1)[0] < plan["count"]})
                    ok, message = promoter.try_sql(probe["attempt"])
                    entry["wrong_evidence_reject"] = not ok and not independence_rule_in(message)
                else:
                    entry["wrong_evidence"] = [
                        {"clause": plan["clause"], "built": plan["built"], "really_fails": None,
                         "reason": plan["reason"] or "not run: another constrained clause on this rule "
                                                     "could not be given evidence that fails it"}
                        for plan in probe["wrong_plans"]]
                for stmt in probe["fix"]:
                    run.sql(stmt)
                if probe["independent"]:
                    # The evidence is in place, so a refusal now can only be about who is writing. The
                    # accept that follows, by the independent credential, proves the evidence was enough.
                    rule_id = probe["rule"]
                    _run_independence_mode(probe, entry, inst, db, identities)
                    for flag, conn in (("self_promotion_reject", writer), ("root_promotion_reject", db)):
                        ok, message = conn.try_sql(probe["attempt"])
                        entry[flag] = not ok and independence_rule_in(message) == rule_id
                        entry[flag.replace("_reject", "_error")] = message
                        if ok and probe["unpromote"]:    # it went through: put the row back, keep testing
                            db.sql(probe["unpromote"])
                if probe.get("forbidden"):   # a caller who cannot read what a forbids clause counts
                    blind = _blind_credential(inst, db, rules[probe["rule"]], entry)
                    if blind is not None:
                        entry["blind_forbidden"] = [_run_blind(db, probe, plan, blind, run)
                                                    for plan in probe["forbidden"]]
                ok, message = promoter.try_sql(probe["attempt"])
                entry["accept"] = ok
                entry["accept_error"] = "" if ok else message
                for plan in probe["evidence"]:
                    entry["evidence"].append(
                        {"clause": plan["clause"], "built": plan["built"], "reason": plan["reason"],
                         "satisfied": (db.sql(plan["present"], expect=1)[0] >= plan["count"])
                                      if plan["built"] else None})
                for plan in probe.get("forbidden") or []:
                    entry["forbidden_evidence"].append(_run_forbidden(db, probe, plan, promoter, run))
            except _NotRun:
                pass
            except dbmod.DbError as exc:
                entry["setup_error"] = "the probe could not write the rows it needs: %s" % exc
        finally:
            for stmt in probe["cleanup"]:
                db.try_sql(stmt)
        behaved = judge(entry, probe)
        independence_ok = bool(entry.get("self_promotion_reject") and entry.get("root_promotion_reject")
                               and entry.get("sibling_promotion_reject") in _required_flag(probe, "principal")
                               and entry.get("borrowed_evidence_reject") in _required_flag(probe, "own_evidence")
                               and entry.get("accept"))
        report["independence_refused_ok"] += 1 if independence_ok else 0
        report["independence_untested"] += 1 if entry.get("missing_independence_probe") else 0
        # Every independent rule lands in exactly one of the three counts, so the three always add up to the
        # rules declared: reported from a real instance, "3 shown, 0 untested" for 4 rules hid the fourth.
        report["independence_rules"] = report.get("independence_rules", 0) + (1 if probe["independent"] else 0)
        report["independence_failed"] = report.get("independence_failed", 0) + (
            1 if probe["independent"] and not independence_ok and not entry.get("missing_independence_probe") else 0)
        report["rejected_ok"] += 1 if entry["reject"] else 0
        report["accepted_ok"] += 1 if entry["accept"] else 0
        report["forbidden_refused_ok"] += 1 if entry["forbidden_evidence_reject"] else 0
        report["forbids_untested"] += 1 if entry.get("missing_forbidden_probe") else 0
        report["requires_untested"] += 1 if entry.get("missing_accept_probe") else 0
        report["misbehaved"] += 0 if behaved else 1
        report["rules"].append(entry)
    report["unique"] = [_run_unique(db, plan) for plan in unique_probes(inst)]
    report["unique_misbehaved"] = sum(0 if u["behaved"] else 1 for u in report["unique"])
    return report


SCRATCH_PROBE_SUFFIX = "__gate_probes"


def run_probes_where_they_can_run(inst, db):
    """(report, where): run_probes on `db`, or -- when originals enforcement is already applied there -- on a
    scratch database on the same server given the same generated definitions, removed afterwards.

    Why: the probe rows this library writes carry no originals, so on a graph that already enforces originals
    (xscholar/originals.py) every accept probe would be refused by that enforcement rather than by the rule under
    test, and the run would report the gates misbehaving when they are fine. The scratch database gets schema,
    gates, temporal and assertions, and access when access is applied on `db` (so the blind-caller probe runs
    there too). `where` is "" on `db` itself, else a note naming the scratch database, for the caller to print.
    One implementation for scripts/run_instance.py and `gates --apply`."""
    from xscholar import access, assertions, originals, temporal
    from xscholar import db as dbmod
    if not originals.enforcement_status(db, inst)["present"]:
        return run_probes(inst, db), ""
    accesses = (db.sql("INFO FOR DB;", expect=1)[0] or {}).get("accesses") or {}
    scratch = dbmod.Db(url=db.url, ns=db.ns, db=db.db + SCRATCH_PROBE_SUFFIX, user=db.user, password=db._password)
    db.sql("REMOVE DATABASE IF EXISTS %s" % scratch.db)
    try:
        for text in (schema_surql(inst), gate_surql(inst), temporal.temporal_surql(inst), assertions.surql(inst)):
            scratch.script(text)
        if access.ACCESS_NAME in accesses:
            scratch.script(access.access_surql(inst))
        report = run_probes(inst, scratch)
    finally:
        db.sql("REMOVE DATABASE IF EXISTS %s" % scratch.db)
    return report, " (in scratch database %s: originals enforcement is already applied here)" % scratch.db


def blind_lines(report):
    """One line per rule with forbids clauses: whether a caller blind to what they count was tried, and how it
    was refused. `event` means the privileged gate event refused it (ROADMAP V2-21); `assert` means the field
    ASSERT did, which for a blind caller is a `requires` clause counting 0 (fails closed, and says nothing
    about the event)."""
    out = []
    for entry in report.get("rules") or []:
        if "blind_forbidden" in entry:
            for result in entry["blind_forbidden"]:
                message = str(result.get("blind_error") or "")
                how = ("the gate event (%s)" % GATE_EVENT_MARKER if GATE_EVENT_MARKER in message
                       else "a permission denial" if "permission" in message.lower() or "not allowed" in message.lower()
                       else "the field ASSERT" if message else "nothing: it was ACCEPTED")
                out.append("blind caller %s on %s [%s]: evidence present %s, refused %s, by %s%s"
                           % (entry.get("blind_probe", "?").split(",")[0], entry["rule"], result["clause"],
                              result["evidence_present"], result["blind_reject"], how,
                              (": %s" % " ".join(message.split())[:160]) if message else ""))
        elif entry.get("blind_probe_error"):
            out.append("blind caller NOT RUN on %s: %s" % (entry["rule"], entry["blind_probe_error"]))
        elif entry.get("blind_probe"):
            out.append("blind caller on %s: %s" % (entry["rule"], entry["blind_probe"]))
    return out


class _NotRun(Exception):
    """A probe that must not run at all; the reason is already on the entry."""


def _probe_identities(inst, db, rule, stem=None, extra=()):
    """Two credentials for an independence probe -- one proposes the row, the other promotes it --
    as ({"proposer": Db, "promoter": Db, "role": name}, None), or (None, why not).

    They are real record-access sign-ins, provisioned through xscholar/access.py exactly as a launch
    credential is, so the refusal the probe meets is the one a real agent would meet.

    `stem` names the credentials (default `zz_probe_<rule>`): staged evidence provisions them under the OUTER
    rule's stem so the outer cleanup removes them. `extra` adds more promoters, each with a principal of its
    own: a promoter that has made the change is on the row's record of writers from then on, so a probe that
    must promote the same row twice needs a second one (the originals probes do)."""
    from xscholar import access
    from xscholar import db as dbmod
    if getattr(db, "access", None):
        return None, "the probe connection is itself an agent credential; only root can provision probe credentials"
    role, why = _probe_role(inst, rule)
    if role is None:
        return None, why
    try:
        accesses = (db.sql("INFO FOR DB;", expect=1)[0] or {}).get("accesses") or {}
    except dbmod.DbError as exc:
        return None, "could not read the database's access definitions: %s" % exc
    if access.ACCESS_NAME not in accesses:
        return None, ("access is not applied to this database (no DEFINE ACCESS %s), so no credential can "
                      "sign in; apply `python -m xscholar.access INSTANCE --apply` first" % access.ACCESS_NAME)
    mode = config.independence_of(rule) or {"between": "credential", "own_evidence": False}
    stem = stem or "zz_probe_%s" % rule["id"]
    out = {"role": role}
    # Between principals: the proposer and a SIBLING credential share one principal, the promoter has another.
    keys = ("proposer", "sibling", "promoter") if mode["between"] == "principal" else ("proposer", "promoter")
    principals = {"proposer": "%s_first" % stem, "sibling": "%s_first" % stem, "promoter": "%s_second" % stem}
    for key in extra:
        principals[key] = "%s_%s" % (stem, key)
    for key in tuple(keys) + tuple(extra):
        agent_id = "%s_%s" % (stem, key)
        if not access.AGENT_ID.match(agent_id) or not access.PRINCIPAL_ID.match(principals[key]):
            return None, "rule id %r makes a probe credential id longer than an agent id may be" % rule["id"]
        try:
            conn = access.connect(access.provision(db, inst, agent_id, role, principal=principals[key],
                                                   provisioned_by="xscholar gate probe"))
            conn.ping(check_version=False)
        except (access.AccessError, dbmod.DbError) as exc:
            return None, "could not provision probe credential %s: %s" % (agent_id, exc)
        out[key] = conn
    return out, None


def _probe_role(inst, rule):
    """(role, None): the access role independence probe credentials for `rule` are provisioned under, or
    (None, why not). Needs no database, so a plan can say in advance that a promotion cannot be probed."""
    from xscholar import access
    table = rule["table"]
    roles = inst.access_roles()
    lens_carrying = access.lens_tables(inst)
    usable = [name for name, r in roles.items() if table in r["write"] and table in r["read"]
              and not (r["lens_bound"] and table in lens_carrying)]
    mode = config.independence_of(rule) or {"between": "credential", "own_evidence": False}
    if mode["own_evidence"]:      # the probe credentials must be able to create an evidence link themselves
        edges = {clause.get("edge") for clause in rule.get("requires") or []}
        usable = [name for name in usable if edges & set(roles[name]["write"])]
    # A credential that cannot read what the rule counts can never pass it (its ASSERT counts 0), so a probe
    # built on one fails for a reason that has nothing to do with independence. Reported from a real instance:
    # the first role in the config was such a role. Only roles that can read every clause are used.
    blind = {name: _unreadable(roles[name], rule, inst) for name in usable}
    if usable and not [name for name in usable if not blind[name]]:
        return None, ("every access role that could propose and promote %s cannot read what rule %s counts (%s), so "
                      "no credential could ever pass it; grant one of them read on those tables"
                      % (table, rule["id"], "; ".join("%s: %s" % (n, ", ".join(blind[n])) for n in usable)))
    usable = [name for name in usable if not blind[name]]
    if not usable:
        return None, ("no access role both reads and writes %s without a lens binding%s, so no probe credential "
                      "could propose and promote it" % (table, " and writes one of its evidence edges"
                                                        if mode["own_evidence"] else ""))
    return usable[0], None


def unique_probes(inst):
    """For every `unique:` field: a first row, a second row carrying the SAME value (must be refused,
    by that field's own index), then the second row with a different value (must be accepted).

    Every other unique field on the table gets a value of its own in each row, so the refusal can only
    come from the index under test; the refusal message must also name that index."""
    out = []
    for table, fields in inst.unique_fields().items():
        for field in fields:
            base = "zz_probe_unique_%s_%s" % (table, field)
            plan = {"table": table, "field": field, "index": unique_index_name(table, field), "built": False,
                    "reason": "", "cleanup": ["DELETE %s WHERE record::id(id) CONTAINS '%s';" % (table, base)]}
            if table != "unit":
                plan["cleanup"].append("DELETE unit WHERE record::id(id) CONTAINS '%s';" % base)
            values, why = _two_distinct_values(table, field, inst)
            if values is None:
                plan["reason"] = why
                out.append(plan)
                continue
            unit_id = "unit:%s_ok" % base
            first, second = "%s:%s_first" % (table, base), "%s:%s_second" % (table, base)
            try:
                plan["setup"] = ([] if table in ("unit",) else [_create(unit_id, "unit", inst)])
                plan["first"] = _create(first, table, inst, dict(_ungated(table, inst), **{field: values[0]}),
                                        unit_id=unit_id)
                plan["duplicate"] = _create(second, table, inst, dict(_ungated(table, inst), **{field: values[0]}),
                                            unit_id=unit_id)
                plan["distinct"] = _create(second, table, inst, dict(_ungated(table, inst), **{field: values[1]}),
                                           unit_id=unit_id)
            except GateError as exc:
                plan["reason"] = "cannot build a %s row: %s" % (table, exc)
                out.append(plan)
                continue
            plan.update({"built": True, "second": second})
            out.append(plan)
    return out


def _ungated(table, inst):
    """Field values that keep a probe row of `table` below every gate on it: a template value that is
    itself a gated value is swapped for another value of the field's domain."""
    template, out = _row_template(table, inst, unit_id="unit:x"), {}
    for rule in inst.gate_rules():
        if rule["table"] != table:
            continue
        gated = _value_literal(rule["value"])
        if template.get(rule["field"]) == gated:
            other = next((v for v in _field_domain(table, rule["field"], inst) or [] if v != gated), None)
            if other is not None:
                out[rule["field"]] = other
    return out


def _two_distinct_values(table, field, inst):
    """Two different values the field may hold outside any gated value, or (None, why not)."""
    gated = {_value_literal(r["value"]) for r in inst.gate_rules() if r["table"] == table and r["field"] == field}
    domain = _field_domain(table, field, inst)
    if domain is not None:
        free = [v for v in domain if v not in gated]
        if len(free) < 2:
            return None, ("%s.%s can hold %d value(s) outside its gates, so no probe can write two rows that "
                          "differ in it" % (table, field, len(free)))
        return free[:2], None
    one, two = _distinct_literal(table, field, inst, "a"), _distinct_literal(table, field, inst, "b")
    if one is None or two is None:
        kind, _ = _field_type(table, field, inst)
        return None, "the engine cannot build a value of type %s for %s.%s" % (kind, table, field)
    return [one, two], None


def _run_unique(db, plan):
    """One unique field, both ways. Refused means refused BY THIS INDEX, and the duplicate row absent."""
    from xscholar import db as dbmod
    result = {"table": plan["table"], "field": plan["field"], "index": plan["index"], "built": plan["built"],
              "reason": plan["reason"], "duplicate_reject": None, "distinct_accept": None,
              "duplicate_error": "", "distinct_error": "", "setup_error": ""}
    try:
        if plan["built"]:
            try:
                for stmt in plan["setup"] + [plan["first"]]:
                    db.sql(stmt)
                ok, message = db.try_sql(plan["duplicate"])
                absent = db.sql("SELECT VALUE id FROM %s" % plan["second"], expect=1)[0] == []
                result["duplicate_reject"] = (not ok) and ("`%s`" % plan["index"] in message) and absent
                result["duplicate_error"] = message
                if not absent:     # the duplicate went in: take it out, or the next CREATE collides on its id
                    db.sql("DELETE %s;" % plan["second"])
                ok, message = db.try_sql(plan["distinct"])
                result["distinct_accept"] = ok
                result["distinct_error"] = "" if ok else message
            except dbmod.DbError as exc:
                result["setup_error"] = "the probe could not write the rows it needs: %s" % exc
    finally:
        for stmt in plan["cleanup"]:
            db.try_sql(stmt)
    if not plan["built"]:
        result["missing_probe"] = ("unique %s.%s was not probed: %s"
                                   % (plan["table"], plan["field"], plan["reason"]))
    result["behaved"] = bool(result["duplicate_reject"] is True and result["distinct_accept"] is True
                             and not result["setup_error"] and not result.get("missing_probe"))
    return result


def _blind_credential(inst, db, rule, entry):
    """A signed-in credential of blind_role(rule), or None. Recorded on the entry either way:
    `blind_probe` says what ran or why nothing needed to; `blind_probe_error` is a probe that should have
    run and could not, which judge() counts against the rule."""
    from xscholar import access
    from xscholar import db as dbmod
    role = blind_role(rule, inst)
    if role is None:
        entry["blind_probe"] = ("not needed: every role that writes %s can read what its forbids clauses count"
                                % rule["table"]) if inst.access_roles() else "not needed: no access roles"
        return None
    if getattr(db, "access", None):
        entry["blind_probe_error"] = "role %s cannot read a forbidden row, and this probe connection cannot provision" % role
        return None
    try:
        accesses = (db.sql("INFO FOR DB;", expect=1)[0] or {}).get("accesses") or {}
    except dbmod.DbError as exc:
        entry["blind_probe_error"] = "could not read the database's access definitions: %s" % exc
        return None
    if access.ACCESS_NAME not in accesses:
        entry["blind_probe"] = ("not needed on this database: access is not applied, so every writer is a system "
                                "user that reads every row")
        return None
    agent_id = "zz_probe_%s_blind" % rule["id"]
    try:
        conn = access.connect(access.provision(db, inst, agent_id, role, principal="zz_probe_%s_blind" % rule["id"],
                                               provisioned_by="xscholar gate probe"))
        conn.ping(check_version=False)
    except (access.AccessError, dbmod.DbError) as exc:
        entry["blind_probe_error"] = "could not provision blind probe credential %s: %s" % (agent_id, exc)
        return None
    entry["blind_probe"] = "role %s, which cannot read what a forbids clause counts" % role
    return conn


def _run_blind(db, probe, plan, blind, run=None):
    """One forbids clause, tried by a caller who cannot read what it counts, on the row BEFORE it is promoted.

    Before, not after: a write that changes nothing fires no event (measured, 3.1.6), so re-sending the gated
    value to a row that already holds it proves nothing about the event. The forbidden row is written, the
    blind caller attempts the promotion and must be refused, and the forbidden row is taken away again."""
    from xscholar import db as dbmod
    result = {"clause": plan["clause"], "built": plan["built"], "evidence_present": None, "blind_reject": None,
              "cleared": None, "blind_error": ""}
    if not plan["built"]:
        return result
    try:
        if probe.get("unpromote"):                  # start from an unpromoted row, or the attempt changes nothing
            db.sql(probe["unpromote"])
        for stmt in plan["create"] + plan["relate"]:
            (run or db).sql(stmt)
        result["evidence_present"] = db.sql(plan["present"], expect=1)[0] >= plan["count"]
        try:
            ok, message = blind.try_sql(probe["attempt"])
        except dbmod.PermissionDenied as exc:      # refused before the gate: still refused
            ok, message = False, str(exc)
        result["blind_reject"] = not ok
        result["blind_error"] = message
        if ok and probe.get("unpromote"):           # it got through: put the row back so the probe can go on
            db.sql(probe["unpromote"])
    finally:
        for stmt in plan["remove"]:
            db.try_sql(stmt)
    result["cleared"] = db.sql(plan["present"], expect=1)[0] == 0
    return result


def _run_forbidden(db, probe, plan, promoter=None, run=None):
    """One forbids clause, both ways: with the forbidden row present the write must be REFUSED, and
    with it gone the same write must be ACCEPTED again.

    `evidence_present` is not bookkeeping. It asks the datastore, with the clause's own predicate,
    whether the row that was just written really is the row the clause forbids. Without it a probe
    that wrote a harmless row and met a refusal for some other reason would report a pass."""
    result = {"clause": plan["clause"], "edge": plan["edge"], "table": plan["table"],
              "built": plan["built"], "reason": plan["reason"], "evidence_present": None,
              "reject": None, "cleared": None, "restored_accept": None, "restore_error": ""}
    if not plan["built"]:
        return result
    from xscholar import db as dbmod
    try:
        for stmt in plan["create"] + plan["relate"]:
            (run or db).sql(stmt)
    except dbmod.DbError as exc:
        result["built"] = False
        result["reason"] = "could not write the forbidden evidence for %s: %s" % (plan["clause"], exc)
        for stmt in plan["remove"]:
            db.try_sql(stmt)
        return result
    promoter = promoter or db
    result["evidence_present"] = db.sql(plan["present"], expect=1)[0] >= plan["count"]
    ok, _ = promoter.try_sql(probe["attempt"])
    result["reject"] = not ok
    for stmt in plan["remove"]:
        db.sql(stmt)
    result["cleared"] = db.sql(plan["present"], expect=1)[0] == 0
    ok, message = promoter.try_sql(probe["attempt"])
    result["restored_accept"] = ok
    result["restore_error"] = "" if ok else message
    return result


# ---- command line --------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate schema, gates and probes from an instance config.")
    parser.add_argument("instance")
    parser.add_argument("--emit", choices=["schema", "gates", "probes"])
    parser.add_argument("--apply", action="store_true", help="apply schema and gates, then run the probes")
    parser.add_argument("--url", default="http://127.0.0.1:8002/sql")
    parser.add_argument("--ns")
    parser.add_argument("--db")
    parser.add_argument("--baseline", help="compare this run against a committed baseline; a gate that got weaker fails")
    parser.add_argument("--write-baseline", help="write the baseline file (human-owned: commit it and review every change)")
    args = parser.parse_args(argv)
    same_file = (args.write_baseline and args.baseline
                 and pathlib.Path(args.write_baseline).resolve() == pathlib.Path(args.baseline).resolve())
    if same_file:
        print("NOT EXECUTED: --write-baseline and --baseline name the same file (%s). The run would "
              "grade itself against what it had just written, and would pass whatever the gates say. "
              "Write it, review the diff, then compare against it." % args.baseline)
        return 2
    inst = config.load(args.instance)
    if args.emit == "schema":
        print(schema_surql(inst)); return 0
    if args.emit == "gates":
        print(gate_surql(inst)); return 0
    if args.emit == "probes":
        print(json.dumps(probes(inst), indent=2)); return 0
    if args.apply:
        from xscholar import db as dbmod
        conn = dbmod.Db(url=args.url, ns=args.ns or inst.id, db=args.db or inst.id)
        conn.ping()
        conn.script(schema_surql(inst))
        dangling = count_dangling_edges(conn, inst)
        for edge, n in sorted(dangling.items()):
            print("DANGLING: %d %s edge(s) point at a record that does not exist; ENFORCED refuses new ones, these were "
                  "stored before it (count them with: %s)" % (n, edge, dangling_edges_surql(inst)[edge]))
        conn.script(gate_surql(inst))
        if inst.independent_rules():
            from xscholar import access
            conn.script(access.access_surql(inst))
            print("ACCESS applied: %d rule(s) require an independent promoter, and only record credentials "
                  "carry an identity to be independent of" % len(inst.independent_rules()))
        # A database that already enforces originals would refuse every accept probe for the wrong reason, so the
        # gates are probed in a scratch database there (run_probes_where_they_can_run says which, and why).
        report, where = run_probes_where_they_can_run(inst, conn)
        print(json.dumps(report, indent=2))
        print("PROBES: %d rejected as required, %d accepted as required, %d misbehaved%s"
              % (report["rejected_ok"], report["accepted_ok"], report["misbehaved"], where))
        print("FORBIDS: %d rule(s) refused their forbidden evidence and took the write once it was "
              "removed, %d left untested" % (report["forbidden_refused_ok"], report["forbids_untested"]))
        print("REQUIRES: %d rule(s) whose accept probe could not build the evidence its own clauses "
              "ask for" % report["requires_untested"])
        print("INDEPENDENCE: %d rule(s) require an independent promoter: %d refused the promotion from the row's own "
              "writer and from root and took it from an independent credential, %d left untested, %d FAILED"
              % (report.get("independence_rules", 0), report["independence_refused_ok"],
                 report["independence_untested"], report.get("independence_failed", 0)))
        for entry in report["rules"]:
            if entry.get("independent") and not entry.get("missing_independence_probe") and not entry["behaved"]:
                print("MISBEHAVED independence %s: %s" % (entry["rule"], ", ".join(
                    "%s=%r" % (flag, entry.get(flag)) for flag in ("self_promotion_reject", "root_promotion_reject",
                                                                  "sibling_promotion_reject", "borrowed_evidence_reject",
                                                                  "accept"))))
        for note in report.get("unpassable_roles") or []:
            print("NOTE role %s writes %s but can never make %s.%s = %r under rule %s: it cannot read %s"
                  % (note["role"], note["table"], note["table"], note["field"], note["value"], note["rule"],
                     ", ".join(note["unreadable"])))
        print("UNIQUE: %d field(s) refused a duplicate and took a distinct value, %d misbehaved or untested"
              % (len(report["unique"]) - report["unique_misbehaved"], report["unique_misbehaved"]))
        for entry in report["rules"]:
            for key in ("missing_probe", "missing_forbidden_probe", "missing_accept_probe",
                        "missing_independence_probe", "blind_probe_error", "setup_error"):
                if entry.get(key):
                    print("MISSING PROBE %s: %s" % (entry["rule"], entry[key]))
        for entry in report["unique"]:
            for key in ("missing_probe", "setup_error"):
                if entry.get(key):
                    print("MISSING PROBE unique %s.%s: %s" % (entry["table"], entry["field"], entry[key]))
            if entry["built"] and not entry["behaved"] and not entry.get("setup_error"):
                print("MISBEHAVED unique %s.%s: duplicate_reject=%r distinct_accept=%r (%s)"
                      % (entry["table"], entry["field"], entry["duplicate_reject"], entry["distinct_accept"],
                         entry["duplicate_error"] or entry["distinct_error"]))
        for line in blind_lines(report):
            print("BLIND: " + line)
        # Originals enforcement, applied AFTER the probes above (whose probe units carry no originals), and then
        # probed both ways itself: a promotion counts only units whose original is verified.
        from xscholar import originals
        conn.script(originals.enforcement_surql(inst))
        oprobes = originals.run_probes(inst, conn)
        print("ORIGINALS: enforcement applied on %d rule(s): %d refused as required, %d accepted as required, "
              "%d misbehaved%s" % (len(oprobes["rules"]), oprobes["rejected_ok"], oprobes["accepted_ok"],
                                   oprobes["misbehaved"], " (%s)" % oprobes["note"] if oprobes.get("note") else ""))
        for entry in oprobes["rules"]:
            if not entry["behaved"]:
                print("MISBEHAVED originals %s: %s" % (entry["rule"], entry.get("not_built") or entry.get("error")
                                                      or entry.get("cleanup_errors") or {
                    f: entry.get(f) for f in ("accept", "reject_without_original", "reject_original_not_verified",
                                              "restored_accept")}))
        failures = []
        if args.write_baseline:
            pathlib.Path(args.write_baseline).write_text(
                json.dumps({"instance": inst.id, "spec": spec(inst), "report": report}, indent=2), encoding="utf-8")
            print("BASELINE written to %s; commit it and review every future change to it" % args.write_baseline)
        if args.baseline:
            path = pathlib.Path(args.baseline)
            if not path.exists():
                print("NOT EXECUTED: baseline %s does not exist; write one with --write-baseline" % path)
                return 2
            failures = compare_baseline(json.loads(path.read_text(encoding="utf-8")), report, spec(inst))
            for line in failures:
                print("WEAKENED: %s" % line)
            print("BASELINE: %s (%d difference(s) that make a gate weaker)"
                  % ("unchanged or stronger" if not failures else "FAILED", len(failures)))
        return 1 if (report["misbehaved"] or report["unique_misbehaved"] or oprobes["misbehaved"] or failures
                     or dangling) else 0
    parser.error("choose --emit or --apply")


if __name__ == "__main__":
    sys.exit(main())
