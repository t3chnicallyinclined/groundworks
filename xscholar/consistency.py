# -*- coding: utf-8 -*-
"""consistency.py -- rows standing at a gated value whose gate no longer holds.

A gate is an ASSERT on the gated field, so the datastore checks it only when THAT field is written.
Nothing re-checks it when the evidence underneath moves: a fact is confirmed while the unit it cites is
usable, then the unit's status changes (a reload that reverted a correction, a correction itself, a
source change), and the fact still reads `confirmed` over evidence its own gate would now refuse. That
is the generic form of "a row's categorical state contradicts a confirmed fact about that row": the
contradiction is between a promoted row and the rule that promoted it, and both sides come from the
instance's config, so this module holds no domain words. It reports; it never decides which side is
wrong (the unit's value or the fact's promotion), because that is a human's call.

    report(db, inst) -> {"rules": [{"rule", "table", "field", "value", "at_value", "no_longer_holds": [ids]}],
                         "rows_at_gated_value": N, "contradictions": M}

`rows_at_gated_value` is printed next to `contradictions` so "0 contradictions" over 0 promoted rows
reads as what it is.

What this cannot see is a promoted row whose TEXT says another row holds a value it does not: that needs
reading free text. An instance that makes the claim structured (`assertions:`, xscholar/assertions.py) gets
it checked too, under the `assertions` key, which is present only when the instance declares any.
Standard library only.
"""
import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import gates  # noqa: E402


def rule_query(rule, inst, negate=True):
    """The SELECT for one rule. `negate=False` exists only so a test can prove the NOT is load-bearing."""
    condition = gates.gate_expression(rule, inst)
    return ("SELECT VALUE <string> id FROM %s WHERE %s = %s AND %s(%s)"
            % (rule["table"], rule["field"], gates._value_literal(rule["value"]), "NOT " if negate else "", condition))


def report(db, inst, negate=True):
    out = {"rules": [], "rows_at_gated_value": 0, "contradictions": 0}
    for rule in inst.gate_rules():
        at_value = db.sql("SELECT count() AS n FROM %s WHERE %s = %s GROUP ALL"
                          % (rule["table"], rule["field"], gates._value_literal(rule["value"])), expect=1)[0]
        at_value = at_value[0]["n"] if at_value else 0
        ids = sorted(db.sql(rule_query(rule, inst, negate), expect=1)[0])
        out["rules"].append({"rule": rule["id"], "table": rule["table"], "field": rule["field"],
                             "value": rule["value"], "at_value": at_value, "no_longer_holds": ids})
        out["rows_at_gated_value"] += at_value
        out["contradictions"] += len(ids)
    # The second kind: a promoted row that asserts, in structure, a value another row's field does not hold
    # (xscholar/assertions.py). Present only when the instance declares `assertions:`, and counted apart from
    # `contradictions` above, which keeps meaning "gate no longer holds".
    from xscholar import assertions
    if assertions.declared(inst):
        out["assertions"] = assertions.report(db, inst)
    return out
