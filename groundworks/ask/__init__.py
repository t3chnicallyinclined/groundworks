# -*- coding: utf-8 -*-
"""ask -- post a corpus, ask a question, get an answer whose every quote a program has checked.

    python -m groundworks.ask open   "QUESTION" --units UNITS.jsonl --out DIR [--instance I.yaml] [--k 200] [--slice 40]
    python -m groundworks.ask check  DIR [--instance I.yaml]
    python -m groundworks.ask render DIR [--instance I.yaml]

The loop, and who does each step:

  open    (program)  ranks every unit of the corpus against the question with BM25 (retrieve.py), takes the
                     top K WIDE (recall@20 measured at 0.54 on the first instance, so K is hundreds, not ten),
                     writes the packet: question.json, candidates.jsonl (full text), slices/*.jsonl.
  read    (agent)    reads EVERY unit of every slice in full, appends one row per unit to read.jsonl, and
                     writes answer.json: the answer, and quotes copied character for character from units.
  check   (program)  refuses: a quote not found word for word in its unit; a cite to a unit that was not read;
                     a cite to a unit outside the packet; a slice with a unit nobody read; a `support` quote
                     from a unit outside the usable statuses. An empty or missing read.jsonl is NOT EXECUTED
                     (exit 3), never a pass. Writes check.json.
  render  (program)  runs check first; on PASS writes answer.md whose header states corpus / retrieved /
                     read / not read, so a green answer still says how much of the corpus it never saw.

An answer never becomes a fact. Nothing here writes to a graph. What `ask` produces is an answer file
with checked quotes; promoting any of it is a separate act through the write surface and its gates.
"""
