# -*- coding: utf-8 -*-
"""schema.py -- a question schema is a versioned instrument, not a prompt.

A decision model answers a schema of typed questions about one unit in a single forward
pass. Three measured facts shape everything here, and each is enforced rather than advised:

  1. ANSWERS ARE NOT INDEPENDENT OF THE SCHEMA. The model scores every question jointly, so
     the identical question on the identical unit scored 0.4073 and 0.3372 with different
     companion questions. A score is therefore a property of (unit, question, THE WHOLE
     SCHEMA IT TRAVELLED WITH). `QuestionSchema.digest` pins that third term; a stored score
     without it cannot be compared with anything.

  2. A VAGUE QUESTION RETURNS MUSH. Measured three times out of three: a compound evaluative
     instruction scored 0.41 where the same question with explicit true/false criteria scored
     0.89, and a loosely worded one flagged 38% of everything put to it. `check_question`
     refuses them, in the spirit of config.check_lens_filter.

  3. ONE ANGLE IS NOT A RESULT. Over 104 cites with three vantage points, the best single
     angle found 20, the union found 24, and all three agreed on 1. Union is recall and
     consensus is precision; they answer different questions and are never collapsed. A
     question declaring fewer than two angles is refused.

And one thing that follows from all three: `Finding` has no way to say "nothing found". It
reports what was asked, of how many angles, under which schema -- so that a silence is read
as a silence. A decision model is weak at noticing that an answer is absent (its published
RAGTruth score is 35.6 against the larger model's 79.4), which is exactly when a bare "no"
would be believed and should not be.

    schema = QuestionSchema.load(path)      # or QuestionSchema(questions)
    problem = check_question(q)             # None, or why it is refused
    finding = Finding(scores, schema)       # .union(), .consensus(), never "[]" alone

Standard library only. The model runner lives in clef.py and needs the `decide` extra.
"""
import hashlib
import json
import pathlib

TYPES = ("noul", "choice", "score")

#: A question must be answerable from the unit in front of it. These words signal a judgement
#: that depends on something the unit cannot contain.
VAGUE_WORDS = ("important", "significant", "relevant", "interesting", "noteworthy",
               "problematic", "concerning", "appropriate", "reasonable", "good", "bad")

MIN_ANGLES = 2


class SchemaError(Exception):
    pass


# ---- validation ---------------------------------------------------------------------------------

def check_question(q):
    """Return None if `q` is a question a model can answer the same way twice, else the problem.

    The refusals are the measured failure modes, not taste."""
    if not isinstance(q, dict):
        return "question must be a mapping"
    kind = q.get("type")
    if kind not in TYPES:
        return "type must be one of %s, not %r" % (", ".join(TYPES), kind)

    instr = q.get("instructions")
    if not isinstance(instr, str) or not instr.strip():
        return "instructions must be a non-empty string"
    low = instr.lower()

    # (2) a compound ask cannot be scored consistently: the model does not know which half to answer
    if " or " in low and "?" not in low:
        return ("instructions %r: a compound ask ('... or ...') is scored inconsistently; "
                "split it into one question per angle" % instr)
    vague = [w for w in VAGUE_WORDS if w in low.split() or (" %s " % w) in low]
    if vague and not q.get("criteria"):
        return ("instructions %r: %r is a judgement the unit cannot settle on its own; "
                "give explicit criteria saying what true and false look like"
                % (instr, vague[0]))

    crit = q.get("criteria")
    if kind == "noul":
        # (2) measured +0.55 from naming true and false
        if not isinstance(crit, dict) or set(crit) != {"true", "false"}:
            return ("a noul must carry criteria with exactly 'true' and 'false' descriptions; "
                    "without them the generic wording scored 0.41 where the explicit one scored 0.89")
        for k in ("true", "false"):
            if not isinstance(crit[k], str) or not crit[k].strip():
                return "noul criteria[%r] must be a non-empty description" % k
    elif kind == "choice":
        if not isinstance(crit, dict) or len(crit) < 2:
            return "a choice must carry criteria mapping at least two option ids to descriptions"
        if any(not isinstance(v, str) or not v.strip() for v in crit.values()):
            return "every choice option needs a description"
    else:  # score
        if not isinstance(crit, (list, tuple)) or len(crit) < 2:
            return "a score must carry criteria as an ordered list of at least two level labels"
        if any(not isinstance(v, str) or not v.strip() for v in crit):
            return "every score level needs a label"

    # (3) one angle is not a result
    angles = q.get("angles")
    if angles is not None:
        if not isinstance(angles, (list, tuple)) or len(angles) < MIN_ANGLES:
            return ("angles must name at least %d vantage points; over 104 cites the best single "
                    "angle found 20 where the union of three found 24" % MIN_ANGLES)
        if len(set(angles)) != len(angles):
            return "angles must be distinct"
    return None


# ---- the schema ---------------------------------------------------------------------------------

class QuestionSchema(object):
    """A frozen set of questions and the digest that identifies it.

    Two schemas with the same questions have the same digest regardless of insertion order;
    any change to any question changes it, because any change moves every score."""

    def __init__(self, questions, name=None):
        if not isinstance(questions, dict) or not questions:
            raise SchemaError("a schema needs at least one question")
        if len(questions) > 64:
            raise SchemaError("at most 64 questions per schema, got %d" % len(questions))
        problems = []
        for qid, q in sorted(questions.items()):
            problem = check_question(q)
            if problem:
                problems.append("%s: %s" % (qid, problem))
        if problems:
            raise SchemaError("; ".join(problems))
        self.name = name
        self.questions = {k: dict(v) for k, v in questions.items()}

    @property
    def canonical(self):
        return json.dumps(self.questions, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def digest(self):
        """Stable short id. Store it beside every score; a score without it is uncomparable."""
        return hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()[:16]

    def angles(self, qid):
        return list(self.questions[qid].get("angles") or [qid])

    @classmethod
    def load(cls, path):
        p = pathlib.Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(data.get("questions", data), name=data.get("name") or p.stem)

    def __len__(self):
        return len(self.questions)

    def __repr__(self):
        return "QuestionSchema(%r, %d questions, digest=%s)" % (self.name, len(self), self.digest)


# ---- the result ---------------------------------------------------------------------------------

class Finding(object):
    """What a sweep returns. It cannot report a bare "nothing found".

    `scores` maps unit id -> {angle -> probability}. `union` is recall, `consensus` is
    precision, and `describe` always states what was asked and of how many angles, so an
    empty result reads as "nothing above T from these angles under this schema" rather than
    as an absence in the corpus."""

    def __init__(self, scores, schema, qid, union_threshold=0.60, consensus_threshold=0.60):
        self.scores = dict(scores)
        self.schema = schema
        self.qid = qid
        self.union_threshold = float(union_threshold)
        self.consensus_threshold = float(consensus_threshold)
        self.angles = schema.angles(qid)

    def union(self):
        """Any angle above threshold. Recall: what must not be ignored."""
        return sorted((u for u, a in self.scores.items()
                       if any(v >= self.union_threshold for v in a.values())),
                      key=lambda u: -max(self.scores[u].values()))

    def consensus(self):
        """Every angle above threshold. Precision: what to read first."""
        return sorted((u for u, a in self.scores.items()
                       if a and all(v >= self.consensus_threshold for v in a.values())),
                      key=lambda u: -min(self.scores[u].values()))

    def describe(self):
        """The sentence a caller must print instead of "no hits"."""
        return ("%d of %d unit(s) above %.2f from angle(s) %s, %d with every angle above %.2f; "
                "question %r under schema %s. A negative here is what these angles did not "
                "surface, not what the corpus does not contain."
                % (len(self.union()), len(self.scores), self.union_threshold,
                   ", ".join(self.angles), len(self.consensus()), self.consensus_threshold,
                   self.qid, self.schema.digest))

    def __repr__(self):
        return "Finding(%s)" % self.describe()
