//! Reordering a read's candidates on this side of the store. The store ranks each project's units
//! with its own BM25 (an index per namespace), so scores from two projects are not on one scale, and
//! a merged read is sorted by numbers that were never comparable. These functions rank one pool of
//! candidates against one question with one vocabulary: plain BM25 over the pool (its own document
//! frequencies), with or without the store's stemming, with the locator's path words as a second
//! field, and reciprocal rank fusion of two orders. Pure functions over text; the retrieval ladder
//! (`eval::ladder`) measures them before any of them is put in front of an agent.

use std::collections::HashMap;

/// BM25's usual constants; fixed, never tuned on a question set.
pub const K1: f64 = 1.2;
pub const B: f64 = 0.75;
/// Reciprocal rank fusion's constant, the value its paper used.
pub const RRF_K: f64 = 60.0;
/// How many times a locator's path words count beside the text (the field boost).
pub const PATH_WEIGHT: usize = 2;

/// The words of `text`: runs of letters, digits and `_`, lowercased, Snowball-stemmed (English) when
/// `stem` is set, which is what the store's analyzer does (`gw_words`: class tokenizer, lowercase,
/// snowball(english)).
pub fn words(text: &str, stem: bool) -> Vec<String> {
    let stemmer = stem.then(|| rust_stemmers::Stemmer::create(rust_stemmers::Algorithm::English));
    text.split(|c: char| !(c.is_alphanumeric() || c == '_'))
        .filter(|w| !w.is_empty())
        .map(|w| {
            let lower = w.to_lowercase();
            match &stemmer {
                Some(s) => s.stem(&lower).into_owned(),
                None => lower,
            }
        })
        .collect()
}

/// The words of a locator's path (`src/ask.rs#L10-20` gives `src`, `ask`, `rs`), without the line range.
pub fn path_words(locator: &str, stem: bool) -> Vec<String> {
    let path = locator.rsplit_once('#').map_or(locator, |(p, _)| p);
    words(&path.replace(['/', '\\', '.', '-'], " "), stem)
}

/// BM25 of each document against the query, with document frequencies taken over these documents
/// alone. A document is its bag of words.
pub fn bm25(query: &[String], docs: &[Vec<String>]) -> Vec<f64> {
    let n = docs.len();
    if n == 0 {
        return Vec::new();
    }
    let avg_len = docs.iter().map(Vec::len).sum::<usize>() as f64 / n as f64;
    let mut df: HashMap<&str, usize> = HashMap::new();
    let bags: Vec<HashMap<&str, usize>> = docs
        .iter()
        .map(|d| {
            let mut bag: HashMap<&str, usize> = HashMap::new();
            for w in d {
                *bag.entry(w.as_str()).or_default() += 1;
            }
            for w in bag.keys() {
                *df.entry(w).or_default() += 1;
            }
            bag
        })
        .collect();
    let mut seen: Vec<&str> = Vec::new();
    let terms: Vec<&str> = query
        .iter()
        .map(String::as_str)
        .filter(|t| {
            if seen.contains(t) {
                false
            } else {
                seen.push(t);
                true
            }
        })
        .collect();
    bags.iter()
        .zip(docs)
        .map(|(bag, doc)| {
            let len = doc.len() as f64;
            terms
                .iter()
                .filter_map(|t| {
                    let tf = *bag.get(t)? as f64;
                    let n_t = *df.get(t).unwrap_or(&0) as f64;
                    let idf = ((n as f64 - n_t + 0.5) / (n_t + 0.5) + 1.0).ln();
                    let norm = tf + K1 * (1.0 - B + B * len / avg_len.max(1.0));
                    Some(idf * tf * (K1 + 1.0) / norm)
                })
                .sum()
        })
        .collect()
}

/// Indices in descending score order; equal scores keep their original order, so an arm that
/// scores nothing (every score 0) reproduces the order it was given.
pub fn order_desc(scores: &[f64]) -> Vec<usize> {
    let mut idx: Vec<usize> = (0..scores.len()).collect();
    idx.sort_by(|&a, &b| scores[b].total_cmp(&scores[a]).then(a.cmp(&b)));
    idx
}

/// Reciprocal rank fusion of several orders of the same indices: each index scores the sum over
/// the orders of 1 / (k + its rank), best first.
pub fn rrf(orders: &[Vec<usize>], k: f64) -> Vec<usize> {
    let n = orders.first().map_or(0, Vec::len);
    let mut score = vec![0.0f64; n];
    for order in orders {
        for (rank, &i) in order.iter().enumerate() {
            score[i] += 1.0 / (k + rank as f64 + 1.0);
        }
    }
    order_desc(&score)
}

/// The arms of the retrieval ladder, in the order they are reported.
pub const ARMS: [&str; 7] = [
    "store",
    "bm25",
    "bm25-stem",
    "bm25-fields",
    "rrf",
    "own-first",
    "rrf-own-bm25",
];

/// A read's candidates as the arms see them: text and locator, which project each came from, its
/// rank inside that project's own read (1 = that project's best), and the question's own project.
pub struct Pool<'a> {
    pub texts: &'a [&'a str],
    pub locators: &'a [&'a str],
    pub projects: &'a [&'a str],
    pub project_rank: &'a [usize],
    pub own: &'a str,
}

/// The question's own project's read in its own order, then the related projects' reads merged by
/// their within-project rank: the one order that never compares scores from two indexes.
pub fn own_first(pool: &Pool<'_>) -> Vec<usize> {
    let mut idx: Vec<usize> = (0..pool.texts.len()).collect();
    idx.sort_by_key(|&i| (pool.projects[i] != pool.own, pool.project_rank[i], i));
    idx
}

/// Every arm's order of the pool against `question`, as indices into it, keyed by arm name.
/// `store` is the order given.
pub fn ladder_orders(question: &str, pool: &Pool<'_>) -> Vec<(&'static str, Vec<usize>)> {
    let (texts, locators) = (pool.texts, pool.locators);
    let plain_q = words(question, false);
    let stem_q = words(question, true);
    let plain_docs: Vec<Vec<String>> = texts.iter().map(|t| words(t, false)).collect();
    let stem_docs: Vec<Vec<String>> = texts.iter().map(|t| words(t, true)).collect();
    let field_docs: Vec<Vec<String>> = stem_docs
        .iter()
        .zip(locators)
        .map(|(d, l)| {
            let mut doc = d.clone();
            let path = path_words(l, true);
            for _ in 0..PATH_WEIGHT {
                doc.extend(path.iter().cloned());
            }
            doc
        })
        .collect();
    let store: Vec<usize> = (0..texts.len()).collect();
    let bm25_plain = order_desc(&bm25(&plain_q, &plain_docs));
    let bm25_stem = order_desc(&bm25(&stem_q, &stem_docs));
    let bm25_fields = order_desc(&bm25(&stem_q, &field_docs));
    let fused = rrf(&[store.clone(), bm25_stem.clone()], RRF_K);
    let own = own_first(pool);
    let own_bm25 = rrf(&[own.clone(), bm25_plain.clone()], RRF_K);
    vec![
        ("store", store),
        ("bm25", bm25_plain),
        ("bm25-stem", bm25_stem),
        ("bm25-fields", bm25_fields),
        ("rrf", fused),
        ("own-first", own),
        ("rrf-own-bm25", own_bm25),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn words_split_lowercase_and_stem_like_the_store() {
        assert_eq!(words("Rollback re-simulates Frames!", false), ["rollback", "re", "simulates", "frames"]);
        assert_eq!(words("Rollback re-simulates Frames!", true), ["rollback", "re", "simul", "frame"]);
        assert_eq!(path_words("src/net_sim.rs#L10-20", false), ["src", "net_sim", "rs"]);
    }

    #[test]
    fn bm25_prefers_the_rarer_matching_word_and_order_is_stable() {
        let docs: Vec<Vec<String>> = ["the cat sat", "the dog sat", "the cat and the dog"]
            .iter()
            .map(|d| words(d, false))
            .collect();
        let q = words("dog", false);
        let scores = bm25(&q, &docs);
        assert_eq!(scores[0], 0.0);
        assert!(scores[1] > 0.0 && scores[2] > 0.0);
        assert!(scores[1] > scores[2], "the shorter document ranks first: {scores:?}");
        assert_eq!(order_desc(&[0.0, 0.0, 0.0]), [0, 1, 2]);
        assert_eq!(order_desc(&scores), [1, 2, 0]);
    }

    #[test]
    fn rrf_fuses_two_orders() {
        // 2 is first in one order and second in the other; nothing else does as well in both.
        assert_eq!(rrf(&[vec![0, 2, 1], vec![2, 1, 0]], 60.0), [2, 0, 1]);
        // 1/(k + rank) is convex: a first and a last place outscore two second places.
        assert_eq!(rrf(&[vec![0, 2, 1], vec![1, 2, 0]], 60.0)[0], 0);
    }

    #[test]
    fn the_ladder_reports_every_arm_over_the_given_pool() {
        let texts = ["nothing here", "the parser rejects empty input", "parsers and inputs"];
        let locators = ["a/b.md#L1-1", "notes.txt#L2-2", "parser/input.rs#L1-1"];
        let projects = ["big", "notes", "big"];
        let pool = Pool {
            texts: &texts,
            locators: &locators,
            projects: &projects,
            project_rank: &[1, 1, 2],
            own: "notes",
        };
        let orders = ladder_orders("why does the parser reject empty input", &pool);
        assert_eq!(orders.len(), ARMS.len());
        for ((arm, order), name) in orders.iter().zip(ARMS) {
            assert_eq!(order.len(), 3, "{arm}");
            assert_eq!(*arm, name);
        }
        assert_eq!(orders[0].1, [0, 1, 2], "store keeps the given order");
        assert_eq!(orders[1].1[0], 1, "plain bm25 finds the exact words");
        assert_eq!(orders[2].1[0], 1, "stemmed too");
        assert_eq!(own_first(&pool), [1, 0, 2], "own project first, then the rest by their own rank");
    }
}
