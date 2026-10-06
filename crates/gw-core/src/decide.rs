//! decide: a question schema is a versioned instrument, not a prompt.
//!
//! A decision model reads one unit and a schema of typed questions and returns a probability for
//! every allowed option of every question in a single forward pass. No generation, no output
//! tokens, no parsing.
//!
//! NOT A PORT, AND THEREFORE NOT GATED BY THE DIFFERENTIAL. Every other unit in this crate lands
//! byte-exact against the Python oracle at `v0.2.0`. This one has no counterpart there: it is new
//! work, landing in Rust because the 2026-09-18 ruling says features land in Rust only. The
//! consequence is that `harness/differential.py` can say nothing about it, so the tests at the foot
//! of this file ARE the gate, and each one names the measurement it encodes.
//!
//! Three measured facts shape the rules, and each is enforced rather than advised:
//!
//! 1. ANSWERS ARE NOT INDEPENDENT OF THE SCHEMA. The model scores every question jointly, so the
//!    identical question on the identical unit scored 0.4073 and 0.3372 with different companion
//!    questions. A score is therefore a property of (unit, question, THE WHOLE SCHEMA IT TRAVELLED
//!    WITH). [`QuestionSchema::digest`] pins that third term; a stored score without it cannot be
//!    compared with anything.
//!
//! 2. A VAGUE QUESTION RETURNS MUSH. Measured three times out of three: a compound evaluative
//!    instruction scored 0.41 where the same question with explicit true/false criteria scored
//!    0.89, and explicit criteria cannot coerce a false proposition, which stayed at 0.006.
//!    [`check_question`] refuses the shapes that failed, in the spirit of `config::check_lens_filter`.
//!
//! 3. ONE ANGLE IS NOT A RESULT. Over 104 cites with three vantage points, the best single angle
//!    found 20, the union found 24, and all three agreed on 1. Union is recall, consensus is
//!    precision; they answer different questions and are never collapsed into one number.
//!
//! And one thing that follows: [`Finding`] has no way to report a bare "nothing found".
//! [`Finding::describe`] always states what was asked, of how many angles, under which schema, so a
//! silence reads as a silence. The model is weak at noticing an answer is absent -- its published
//! RAGTruth score is 35.6 against the larger variant's 79.4 -- which is exactly when a bare "no"
//! would be believed and should not be.
//!
//! WHAT THIS IS FOR. It decides what to read. It does not decide what is true. Nothing it returns
//! is evidence, and nothing it returns belongs in a promotion gate.
//!
//! The model runner is deliberately absent. The typed-decision head ships as Python inside the
//! model repository and has no implementation outside it, so a runner is a process boundary, not a
//! port; it is a later ticket. This module is the part that can be pure logic, and it has no
//! dependency beyond the digest.

use std::collections::BTreeMap;
use std::fmt;

use sha2::{Digest, Sha256};

/// The upper bound the model itself imposes on one forward pass.
pub const MAX_QUESTIONS: usize = 64;

/// Fewer than this many vantage points is not a result (measurement 3).
pub const MIN_ANGLES: usize = 2;

/// Words naming a judgement the unit cannot settle on its own. Allowed only with explicit criteria.
const VAGUE_WORDS: &[&str] = &[
    "important", "significant", "relevant", "interesting", "noteworthy", "problematic",
    "concerning", "appropriate", "reasonable", "good", "bad",
];

#[derive(Debug, thiserror::Error)]
pub enum DecideError {
    #[error("a schema needs at least one question")]
    Empty,
    #[error("at most {MAX_QUESTIONS} questions per schema, got {0}")]
    TooMany(usize),
    #[error("{0}")]
    Question(String),
    #[error("schema has no question(s) {0}")]
    UnknownAngle(String),
}

/// What a question asks the model to return.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Criteria {
    /// `noul`: the probability a statement is true. Both descriptions are required.
    Noul { yes: String, no: String },
    /// `choice`: a named option. At least two, each with a description.
    Choice(BTreeMap<String, String>),
    /// `score`: an ordered scale, indexed from 0. At least two levels.
    Score(Vec<String>),
}

impl Criteria {
    fn kind(&self) -> &'static str {
        match self {
            Criteria::Noul { .. } => "noul",
            Criteria::Choice(_) => "choice",
            Criteria::Score(_) => "score",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Question {
    pub instructions: String,
    pub criteria: Criteria,
    /// The vantage points this question is asked from. Empty means the question stands alone and is
    /// its own single angle; two or more are required when it names any.
    pub angles: Vec<String>,
}

impl Question {
    pub fn noul(instructions: &str, yes: &str, no: &str) -> Self {
        Question {
            instructions: instructions.to_string(),
            criteria: Criteria::Noul { yes: yes.to_string(), no: no.to_string() },
            angles: Vec::new(),
        }
    }

    pub fn with_angles(mut self, angles: &[&str]) -> Self {
        self.angles = angles.iter().map(|a| a.to_string()).collect();
        self
    }
}

/// `None` if `q` is a question a model can answer the same way twice, else the problem.
///
/// The refusals are the measured failure modes, not taste.
pub fn check_question(q: &Question) -> Option<String> {
    let instr = q.instructions.trim();
    if instr.is_empty() {
        return Some("instructions must be a non-empty string".to_string());
    }
    let low = instr.to_lowercase();

    // (2) a compound ask cannot be scored consistently: the model does not know which half to answer
    if low.contains(" or ") && !low.contains('?') {
        return Some(format!(
            "instructions {instr:?}: a compound ask ('... or ...') is scored inconsistently; \
             split it into one question per angle"
        ));
    }

    let has_descriptions = match &q.criteria {
        Criteria::Noul { yes, no } => !yes.trim().is_empty() && !no.trim().is_empty(),
        Criteria::Choice(m) => m.len() >= 2 && m.values().all(|v| !v.trim().is_empty()),
        Criteria::Score(v) => v.len() >= 2 && v.iter().all(|s| !s.trim().is_empty()),
    };

    if let Some(w) = VAGUE_WORDS.iter().find(|w| low.split_whitespace().any(|t| t.trim_matches(|c: char| !c.is_alphanumeric()) == **w)) {
        if !has_descriptions {
            return Some(format!(
                "instructions {instr:?}: {w:?} is a judgement the unit cannot settle on its own; \
                 give explicit criteria saying what true and false look like"
            ));
        }
    }

    match &q.criteria {
        Criteria::Noul { yes, no } => {
            // (2) measured +0.55 from naming true and false
            if yes.trim().is_empty() || no.trim().is_empty() {
                return Some(
                    "a noul must describe both true and false; without them the generic wording \
                     scored 0.41 where the explicit one scored 0.89"
                        .to_string(),
                );
            }
        }
        Criteria::Choice(m) => {
            if m.len() < 2 {
                return Some("a choice needs at least two options".to_string());
            }
            if m.values().any(|v| v.trim().is_empty()) {
                return Some("every choice option needs a description".to_string());
            }
        }
        Criteria::Score(levels) => {
            if levels.len() < 2 {
                return Some("a score needs at least two ordered levels".to_string());
            }
            if levels.iter().any(|s| s.trim().is_empty()) {
                return Some("every score level needs a label".to_string());
            }
        }
    }

    // (3) one angle is not a result
    if !q.angles.is_empty() {
        if q.angles.len() < MIN_ANGLES {
            return Some(format!(
                "angles must name at least {MIN_ANGLES} vantage points; over 104 cites the best \
                 single angle found 20 where the union of three found 24"
            ));
        }
        let mut seen: Vec<&String> = q.angles.iter().collect();
        seen.sort();
        seen.dedup();
        if seen.len() != q.angles.len() {
            return Some("angles must be distinct".to_string());
        }
    }
    None
}

/// A frozen set of questions and the digest that identifies it.
///
/// Two schemas with the same questions have the same digest whatever order they were built in; any
/// change to any question changes it, because any change moves every score.
#[derive(Debug, Clone)]
pub struct QuestionSchema {
    name: String,
    questions: BTreeMap<String, Question>,
}

impl QuestionSchema {
    pub fn new(name: &str, questions: BTreeMap<String, Question>) -> Result<Self, DecideError> {
        if questions.is_empty() {
            return Err(DecideError::Empty);
        }
        if questions.len() > MAX_QUESTIONS {
            return Err(DecideError::TooMany(questions.len()));
        }
        let mut problems = Vec::new();
        for (id, q) in &questions {
            if let Some(p) = check_question(q) {
                problems.push(format!("{id}: {p}"));
            }
        }
        if !problems.is_empty() {
            return Err(DecideError::Question(problems.join("; ")));
        }
        Ok(QuestionSchema { name: name.to_string(), questions })
    }

    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn len(&self) -> usize {
        self.questions.len()
    }

    pub fn is_empty(&self) -> bool {
        self.questions.is_empty()
    }

    pub fn get(&self, id: &str) -> Option<&Question> {
        self.questions.get(id)
    }

    /// The angles a question is asked from: those it names, or itself when it names none.
    pub fn angles(&self, id: &str) -> Vec<String> {
        match self.questions.get(id) {
            Some(q) if !q.angles.is_empty() => q.angles.clone(),
            Some(_) => vec![id.to_string()],
            None => Vec::new(),
        }
    }

    /// Canonical text: every key in sorted order, no insignificant whitespace. `BTreeMap` gives the
    /// ordering, so the form does not depend on how the schema was built.
    pub fn canonical(&self) -> String {
        let mut out = String::from("{");
        for (i, (id, q)) in self.questions.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            out.push_str(&format!("{}:{{", quote(id)));
            out.push_str(&format!("\"angles\":[{}],", q.angles.iter().map(|a| quote(a)).collect::<Vec<_>>().join(",")));
            out.push_str(&format!("\"instructions\":{},", quote(&q.instructions)));
            out.push_str(&format!("\"type\":{},", quote(q.criteria.kind())));
            match &q.criteria {
                Criteria::Noul { yes, no } => {
                    out.push_str(&format!("\"criteria\":{{\"false\":{},\"true\":{}}}", quote(no), quote(yes)));
                }
                Criteria::Choice(m) => {
                    let body: Vec<String> =
                        m.iter().map(|(k, v)| format!("{}:{}", quote(k), quote(v))).collect();
                    out.push_str(&format!("\"criteria\":{{{}}}", body.join(",")));
                }
                Criteria::Score(levels) => {
                    let body: Vec<String> = levels.iter().map(|s| quote(s)).collect();
                    out.push_str(&format!("\"criteria\":[{}]", body.join(",")));
                }
            }
            out.push('}');
        }
        out.push('}');
        out
    }

    /// Stable short id. Store it beside every score; a score without it is uncomparable.
    pub fn digest(&self) -> String {
        let mut h = Sha256::new();
        h.update(self.canonical().as_bytes());
        format!("{:x}", h.finalize())[..16].to_string()
    }
}

fn quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// What a sweep returns. It cannot report a bare "nothing found".
#[derive(Debug, Clone)]
pub struct Finding {
    /// unit id -> angle -> probability.
    pub scores: BTreeMap<String, BTreeMap<String, f64>>,
    pub question_id: String,
    pub schema_digest: String,
    pub angles: Vec<String>,
    pub union_threshold: f64,
    pub consensus_threshold: f64,
}

impl Finding {
    pub fn new(
        scores: BTreeMap<String, BTreeMap<String, f64>>,
        schema: &QuestionSchema,
        question_id: &str,
        union_threshold: f64,
        consensus_threshold: f64,
    ) -> Result<Self, DecideError> {
        let angles = schema.angles(question_id);
        if angles.is_empty() {
            return Err(DecideError::UnknownAngle(question_id.to_string()));
        }
        Ok(Finding {
            scores,
            question_id: question_id.to_string(),
            schema_digest: schema.digest(),
            angles,
            union_threshold,
            consensus_threshold,
        })
    }

    /// Any angle above threshold. Recall: what must not be ignored.
    pub fn union(&self) -> Vec<String> {
        let mut hits: Vec<(&String, f64)> = self
            .scores
            .iter()
            .filter_map(|(u, a)| {
                let best = a.values().copied().fold(f64::NEG_INFINITY, f64::max);
                (best >= self.union_threshold).then_some((u, best))
            })
            .collect();
        hits.sort_by(|x, y| y.1.total_cmp(&x.1).then_with(|| x.0.cmp(y.0)));
        hits.into_iter().map(|(u, _)| u.clone()).collect()
    }

    /// Every angle above threshold. Precision: what to read first.
    pub fn consensus(&self) -> Vec<String> {
        let mut hits: Vec<(&String, f64)> = self
            .scores
            .iter()
            .filter_map(|(u, a)| {
                if a.is_empty() {
                    return None;
                }
                let worst = a.values().copied().fold(f64::INFINITY, f64::min);
                (worst >= self.consensus_threshold).then_some((u, worst))
            })
            .collect();
        hits.sort_by(|x, y| y.1.total_cmp(&x.1).then_with(|| x.0.cmp(y.0)));
        hits.into_iter().map(|(u, _)| u.clone()).collect()
    }

    /// The sentence a caller must print instead of "no hits".
    pub fn describe(&self) -> String {
        format!(
            "{} of {} unit(s) above {:.2} from angle(s) {}, {} with every angle above {:.2}; \
             question {:?} under schema {}. A negative here is what these angles did not surface, \
             not what the corpus does not contain.",
            self.union().len(),
            self.scores.len(),
            self.union_threshold,
            self.angles.join(", "),
            self.consensus().len(),
            self.consensus_threshold,
            self.question_id,
            self.schema_digest,
        )
    }
}

impl fmt::Display for Finding {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.describe())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn good() -> Question {
        Question::noul(
            "The court refuses to rescind a previously made order.",
            "The court declines or refuses to rescind an existing order.",
            "No refusal to rescind appears.",
        )
    }

    fn good_b() -> Question {
        Question::noul(
            "An existing order is left standing over an application to remove it.",
            "An application to undo an order is not granted.",
            "No such application is decided.",
        )
    }

    fn schema_of(pairs: Vec<(&str, Question)>) -> QuestionSchema {
        let map: BTreeMap<String, Question> =
            pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
        QuestionSchema::new("t", map).expect("schema builds")
    }

    #[test]
    fn a_well_formed_question_passes() {
        assert_eq!(check_question(&good()), None);
    }

    #[test]
    fn a_noul_without_both_descriptions_is_refused() {
        // measured: the generic wording scored 0.41 where explicit criteria scored 0.89
        let q = Question::noul("The court refuses to rescind an order.", "", "");
        let problem = check_question(&q).expect("must be refused");
        assert!(problem.contains("0.41"), "the refusal names its measurement: {problem}");
    }

    #[test]
    fn a_compound_ask_is_refused() {
        let mut q = good();
        q.instructions =
            "Material outside the cited lines answers or qualifies the proposition".to_string();
        let problem = check_question(&q).expect("a compound ask must be refused");
        assert!(problem.contains("compound"), "{problem}");
    }

    #[test]
    fn a_vague_judgement_is_refused_bare_and_allowed_with_criteria() {
        let bare = Question {
            instructions: "This passage is relevant to the appeal.".to_string(),
            criteria: Criteria::Noul { yes: String::new(), no: String::new() },
            angles: Vec::new(),
        };
        assert!(check_question(&bare).is_some());
        let with = Question::noul(
            "This passage is relevant to the appeal.",
            "The passage names the order under review.",
            "It does not.",
        );
        assert_eq!(check_question(&with), None);
    }

    #[test]
    fn one_angle_is_not_a_result() {
        assert!(check_question(&good().with_angles(&["only_one"])).is_some());
        assert_eq!(check_question(&good().with_angles(&["a", "b"])), None);
        assert!(check_question(&good().with_angles(&["a", "a"])).is_some());
    }

    #[test]
    fn choice_and_score_shapes_are_checked() {
        let two: BTreeMap<String, String> = [("court", "the judge"), ("witness", "the deponent")]
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        let q = Question {
            instructions: "Who speaks these words?".to_string(),
            criteria: Criteria::Choice(two),
            angles: Vec::new(),
        };
        assert_eq!(check_question(&q), None);

        let one: BTreeMap<String, String> =
            [("court", "the judge")].iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        let q1 = Question { criteria: Criteria::Choice(one), ..q.clone() };
        assert!(check_question(&q1).is_some());

        let s = Question {
            instructions: "How direct is the support?".to_string(),
            criteria: Criteria::Score(vec!["none".into(), "weak".into(), "strong".into()]),
            angles: Vec::new(),
        };
        assert_eq!(check_question(&s), None);
        let s1 = Question { criteria: Criteria::Score(vec!["only".into()]), ..s };
        assert!(check_question(&s1).is_some());
    }

    #[test]
    fn digest_is_build_order_independent_and_change_sensitive() {
        let a = schema_of(vec![("x", good()), ("y", good_b())]);
        let b = schema_of(vec![("y", good_b()), ("x", good())]);
        assert_eq!(a.digest(), b.digest(), "build order must not change the digest");

        let mut moved = good_b();
        moved.instructions.push('.');
        let c = schema_of(vec![("x", good()), ("y", moved)]);
        assert_ne!(
            a.digest(),
            c.digest(),
            "any change to any question must change the digest, because it moves every score"
        );
    }

    #[test]
    fn a_bad_question_refuses_the_whole_schema() {
        let map: BTreeMap<String, Question> = [
            ("ok".to_string(), good()),
            ("bad".to_string(), Question::noul("no descriptions here", "", "")),
        ]
        .into_iter()
        .collect();
        assert!(QuestionSchema::new("t", map).is_err());
    }

    #[test]
    fn empty_and_oversized_schemas_are_refused() {
        assert!(QuestionSchema::new("t", BTreeMap::new()).is_err());
        let big: BTreeMap<String, Question> =
            (0..=MAX_QUESTIONS).map(|i| (format!("q{i}"), good())).collect();
        assert!(QuestionSchema::new("t", big).is_err());
    }

    fn finding_of(scores: Vec<(&str, Vec<(&str, f64)>)>) -> Finding {
        let schema = schema_of(vec![
            ("reader", good()),
            ("opponent", good_b()),
            ("q", good().with_angles(&["reader", "opponent"])),
        ]);
        let s: BTreeMap<String, BTreeMap<String, f64>> = scores
            .into_iter()
            .map(|(u, a)| {
                (u.to_string(), a.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
            })
            .collect();
        Finding::new(s, &schema, "q", 0.60, 0.60).expect("finding builds")
    }

    #[test]
    fn union_is_recall_and_consensus_is_precision() {
        let f = finding_of(vec![
            ("u1", vec![("reader", 0.9), ("opponent", 0.1)]), // one angle only
            ("u2", vec![("reader", 0.8), ("opponent", 0.7)]), // both
            ("u3", vec![("reader", 0.1), ("opponent", 0.1)]), // neither
        ]);
        assert_eq!(f.union(), vec!["u1".to_string(), "u2".to_string()]);
        assert_eq!(f.consensus(), vec!["u2".to_string()]);
    }

    #[test]
    fn a_single_angle_hit_survives_the_union_and_fails_consensus() {
        // the measured case: best single angle 20, union 24, all three agreeing 1
        let f = finding_of(vec![("only_one", vec![("reader", 0.95), ("opponent", 0.02)])]);
        assert_eq!(f.union(), vec!["only_one".to_string()]);
        assert!(f.consensus().is_empty());
    }

    #[test]
    fn an_empty_result_still_states_what_was_asked() {
        // a silence must read as a silence, never as an absence in the corpus
        let f = finding_of(vec![("u1", vec![("reader", 0.1), ("opponent", 0.1)])]);
        assert!(f.union().is_empty());
        let text = f.describe();
        assert!(text.contains("reader"), "{text}");
        assert!(text.contains("opponent"), "{text}");
        assert!(text.contains(&f.schema_digest), "{text}");
        assert!(text.contains("not what the corpus does not contain"), "{text}");
    }

    #[test]
    fn an_unknown_question_id_is_an_error_not_an_empty_finding() {
        let schema = schema_of(vec![("x", good())]);
        assert!(Finding::new(BTreeMap::new(), &schema, "nobody", 0.6, 0.6).is_err());
    }
}
