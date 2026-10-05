# -*- coding: utf-8 -*-
"""clef.py -- run a pinned question schema over units with a local decision model.

A decision model reads a unit and a schema of typed questions and returns one probability per
allowed option of every question in a SINGLE forward pass. There is no generation, so there
are no output tokens, no parsing and no reasoning to audit -- which is the point and also the
limit: what comes back is a number, never a reason.

    runner = ClefRunner()                              # loads on first use
    finding = runner.sweep(units, schema, "qid")       # Finding, never a bare list
    print(finding.describe())                          # states what was asked, of how many angles

Measured on one RTX 3090 (24 GB), model Cloudflare/clef-flash, bfloat16:

    load 22 s | 17.8 GB resident | ~470-580 ms per unit for a 5-10 question schema
    561 units x 10 questions = 5.4 min | 104 units x 7 questions = 1.0 min | 0 output tokens

WHAT THIS IS FOR, AND WHAT IT IS NOT FOR. It decides what to read. It does not decide what is
true. Its published RAGTruth score (detecting unsupported claims) is 35.6 against the larger
variant's 79.4, so it is strong at judging text in front of it and weak at noticing the answer
is absent. Use it to order and widen a candidate set; never to confirm, promote, or close a
question. Nothing it returns is evidence.

The heavy imports are inside the functions that need them, and no core module imports this
one, so the engine keeps `dependencies = []`. Needs the `decide` extra.
"""
import time

DEFAULT_MODEL = "Cloudflare/clef-flash"
DEFAULT_MAX_LENGTH = 16384          # the shipped harness default; 65536 is advertised but untested here


class DecideUnavailable(Exception):
    """Raised when the optional extra is not installed, naming what to do about it."""


def _load(model_id, device, dtype_name):
    """Import torch and the model's own code only when a model is actually wanted."""
    try:
        import sys
        import torch
        from huggingface_hub import snapshot_download
    except ImportError as exc:                      # pragma: no cover - environment dependent
        raise DecideUnavailable(
            "groundworks.decide.clef needs the optional extra: pip install -e .[decide] "
            "(torch, transformers, accelerate, safetensors, pillow, huggingface_hub). "
            "Original error: %s" % exc) from exc
    path = snapshot_download(model_id)
    if path not in sys.path:
        sys.path.insert(0, path)                    # the release ships its own joint_schema_model.py
    from joint_schema_model import load_release_model, systemone
    dtype = getattr(torch, dtype_name)
    model, processor = load_release_model(path, device=device, dtype=dtype)
    return model, processor, systemone


class ClefRunner(object):
    """Holds the loaded model. Loading costs ~22 s and ~18 GB, so hold one and reuse it."""

    def __init__(self, model_id=DEFAULT_MODEL, device="cuda", dtype="bfloat16",
                 max_length=DEFAULT_MAX_LENGTH):
        self.model_id = model_id
        self.device = device
        self.dtype = dtype
        self.max_length = int(max_length)
        self._model = self._processor = self._systemone = None

    def load(self):
        if self._model is None:
            self._model, self._processor, self._systemone = _load(
                self.model_id, self.device, self.dtype)
        return self

    def ask(self, state, schema):
        """One unit, the whole schema, one forward pass. Returns {qid: answer}."""
        self.load()
        request = {"model": self.model_id.split("/")[-1], "state": state,
                   "questions": schema.questions}
        return self._systemone(self._model, self._processor, request,
                               max_length=self.max_length)["answers"]

    def sweep(self, units, schema, qid, union_threshold=0.60, consensus_threshold=0.60,
              progress=None):
        """Score every unit under `schema`, and report question `qid` as a Finding.

        `units` is an iterable of (unit_id, text). The whole schema runs on every unit because
        the marginal cost of another question is near zero and because a score is only
        comparable within the schema it travelled with."""
        from groundworks.decide.schema import Finding

        angles = schema.angles(qid)
        missing = [a for a in angles if a not in schema.questions]
        if missing:
            raise KeyError("schema %s has no question(s) %s" % (schema.digest, ", ".join(missing)))

        scores, started = {}, time.time()
        for n, (uid, text) in enumerate(units):
            answers = self.ask(text, schema)
            scores[uid] = {a: answers[a]["noul"] for a in angles if "noul" in answers[a]}
            if progress and (n + 1) % 50 == 0:
                progress(n + 1, time.time() - started)
        return Finding(scores, schema, qid,
                       union_threshold=union_threshold,
                       consensus_threshold=consensus_threshold)
