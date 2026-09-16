# -*- coding: utf-8 -*-
"""XScholar pipeline: raw sources -> units -> slices -> labels -> the graph.

Five deterministic modules, each with a file contract from docs/MODULES.md and a loud failure:

    intake.py     an instance's adapter -> units.jsonl (+ excluded.jsonl), contract-checked
    chunker.py    units.jsonl -> chunks.json + slices/<chunk>.jsonl, every unit in exactly one slice
    validator.py  slice + labels -> Report: coverage, vocabulary, tier rules, VERBATIM quote, wording
    loader.py     units + passing labels -> the datastore, stable edge ids, counted batches, safe sweep
    write.py      the typed write surface: propose/confirm/rule_out/record_attempt/critique/answer/cite

No domain words live here. Everything an instance knows (its unit types, its status axis, its fidelity
ladder, its vocabulary and its gate rules) arrives as an `Instance` from `engine.config`; the datastore
arrives as a `Db` from `engine.db`. Both are duck-typed at the seams so a test can supply a fake.

Modules work both as this package and as flat modules (tests and command lines), like engine/present.
Standard library only.
"""
