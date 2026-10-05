# decide

Typed decisions over units: a model reads one unit and a schema of typed questions and returns a
probability for every allowed option of every question **in a single forward pass**. No generation,
no output tokens, no parsing.

- `schema.py` — **standard library only.** The question schema, its digest, the validator, and the
  result type. The rules live here so they are usable and testable without the model.
- `clef.py` — the runner. Needs `pip install -e .[decide]`. Imports torch inside the functions that
  need it; **no core module imports it**, so the engine keeps `dependencies = []`.

## What it is for

**It decides what to read. It does not decide what is true.** Use it to widen and order a candidate
set before an agent reads it. Never to confirm a claim, promote a fact, or close a question. Nothing
it returns is evidence, and nothing it returns belongs in `ask check` or the promotion gates.

## Three rules, each from a measurement

**A score belongs to the whole schema it travelled with.** The model scores every question jointly,
so the identical question on the identical unit scored 0.4073 and 0.3372 with different companions.
`QuestionSchema.digest` pins that; store it beside every score.

**A vague question returns mush.** A compound evaluative instruction scored 0.41 where the same
question with explicit true/false criteria scored 0.89 — and explicit criteria cannot coerce a false
proposition, which stayed at 0.006. `check_question` refuses the shapes that failed.

**One angle is not a result.** Over 104 cites with three vantage points: best single angle 20, union
24, all three agreeing 1. `Finding.union()` is recall — what must not be ignored. `Finding.consensus()`
is precision — what to read first. They are never collapsed into one number.

And so `Finding` has no way to report a bare "nothing found". `describe()` always states what was
asked, of how many angles, under which schema. The model is weak at noticing an answer is absent —
published RAGTruth 35.6 against the larger variant's 79.4 — which is exactly when a bare "no" would
be believed and should not be.

## Measured

One RTX 3090 (24 GB), `Cloudflare/clef-flash` (Apache-2.0, 9B), bfloat16:

| | |
|---|---|
| load | 22 s, 17.8 GB resident |
| per unit | ~470–580 ms for a 5–10 question schema |
| 561 units × 10 questions | 5.4 min |
| 104 units × 7 questions | 1.0 min |
| output tokens | **0** |

`torchvision` is a hard requirement of the processor and is not documented upstream; it is included
in the extra. `systemone` defaults to `max_length=16384`, not the 65,536 advertised.
