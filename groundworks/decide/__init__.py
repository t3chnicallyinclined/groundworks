# -*- coding: utf-8 -*-
"""decide -- typed decisions over units, from a pinned question schema.

`schema` is standard library only and carries the rules that make a sweep reproducible.
`clef` runs a local decision model and needs the `decide` extra (pip install -e .[decide]);
nothing in the engine core imports it, exactly as with embed/.
"""
from groundworks.decide.schema import (  # noqa: F401
    Finding, QuestionSchema, SchemaError, check_question,
)
