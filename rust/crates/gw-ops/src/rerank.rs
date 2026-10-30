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
    bm25_with(query, docs, B)
}

/// The distinct query terms, in order.
fn terms(query: &[String]) -> Vec<&str> {
    let mut seen: Vec<&str> = Vec::new();
    for t in query {
        if !seen.contains(&t.as_str()) {
            seen.push(t);
        }
    }
    seen
}

/// Document frequency of every word over `docs`, and the inverse document frequency of a term.
fn idf_over(docs: &[Vec<String>]) -> impl Fn(&str) -> f64 + '_ {
    let n = docs.len() as f64;
    let mut df: HashMap<&str, usize> = HashMap::new();
    for d in docs {
        let mut seen: Vec<&str> = Vec::new();
        for w in d {
            if !seen.contains(&w.as_str()) {
                seen.push(w);
                *df.entry(w).or_default() += 1;
            }
        }
    }
    move |t: &str| {
        let n_t = *df.get(t).unwrap_or(&0) as f64;
        ((n - n_t + 0.5) / (n_t + 0.5) + 1.0).ln()
    }
}

/// One bag's BM25 against the terms, given the idf, its length and the average length.
fn score_bag(
    terms: &[&str],
    idf: &dyn Fn(&str) -> f64,
    bag: &HashMap<&str, usize>,
    len: f64,
    avg_len: f64,
    b: f64,
) -> f64 {
    terms
        .iter()
        .filter_map(|t| {
            let tf = *bag.get(t)? as f64;
            let norm = tf + K1 * (1.0 - b + b * len / avg_len.max(1.0));
            Some(idf(t) * tf * (K1 + 1.0) / norm)
        })
        .sum()
}

fn bag_of(doc: &[String]) -> HashMap<&str, usize> {
    let mut bag: HashMap<&str, usize> = HashMap::new();
    for w in doc {
        *bag.entry(w.as_str()).or_default() += 1;
    }
    bag
}

/// BM25 with the length-normalisation constant `b` given (0: none; 1: full).
pub fn bm25_with(query: &[String], docs: &[Vec<String>], b: f64) -> Vec<f64> {
    let n = docs.len();
    if n == 0 {
        return Vec::new();
    }
    let avg_len = docs.iter().map(Vec::len).sum::<usize>() as f64 / n as f64;
    let idf = idf_over(docs);
    let terms = terms(query);
    docs.iter()
        .map(|doc| score_bag(&terms, &idf, &bag_of(doc), doc.len() as f64, avg_len, b))
        .collect()
}

/// Lines of text in a scoring window, and the step between windows.
pub const WINDOW_LINES: usize = 12;

/// The best window of each text: BM25 of every `WINDOW_LINES`-line window (stepping half a
/// window), with document frequencies over the whole texts and the average length over the
/// windows, the text's score being its best window's. A long unit with the question's words
/// scattered through it no longer outranks a short one that answers in one place.
pub fn bm25_window(query: &[String], texts: &[&str]) -> Vec<f64> {
    let docs: Vec<Vec<String>> = texts.iter().map(|t| words(t, false)).collect();
    if docs.is_empty() {
        return Vec::new();
    }
    let idf = idf_over(&docs);
    let terms = terms(query);
    let windows: Vec<Vec<Vec<String>>> = texts
        .iter()
        .map(|t| {
            let lines: Vec<&str> = t.lines().collect();
            let step = (WINDOW_LINES / 2).max(1);
            let starts: Vec<usize> = if lines.len() <= WINDOW_LINES {
                vec![0]
            } else {
                (0..=lines.len() - WINDOW_LINES).step_by(step).collect()
            };
            starts
                .into_iter()
                .map(|s| words(&lines[s..(s + WINDOW_LINES).min(lines.len())].join("\n"), false))
                .collect()
        })
        .collect();
    let (sum, count) = windows
        .iter()
        .flatten()
        .fold((0usize, 0usize), |(s, c), w| (s + w.len(), c + 1));
    let avg_len = sum as f64 / count.max(1) as f64;
    windows
        .iter()
        .map(|ws| {
            ws.iter()
                .map(|w| score_bag(&terms, &idf, &bag_of(w), w.len() as f64, avg_len, B))
                .fold(0.0f64, f64::max)
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

/// Reciprocal rank fusion of score lists over the same indices, where an index counts only in the lists in
/// which it scored above zero, and an index that scored nowhere is left out (a bridge arm must not propose a
/// card that matched nothing). Best first; ties keep index order.
pub fn rrf_positive(scores: &[Vec<f64>], k: f64) -> Vec<usize> {
    let n = scores.first().map_or(0, Vec::len);
    let mut fused = vec![0.0f64; n];
    let mut scored = vec![false; n];
    for list in scores {
        let ranked = order_desc(list).into_iter().filter(|&i| list[i] > 0.0);
        for (rank, i) in ranked.enumerate() {
            fused[i] += 1.0 / (k + rank as f64 + 1.0);
            scored[i] = true;
        }
    }
    order_desc(&fused).into_iter().filter(|&i| scored[i]).collect()
}

/// The arms of the retrieval ladder, in the order they are reported. The first is the baseline the
/// paired tests run against: the store's merged scores, the order ask shipped until 2026-10-26.
pub const ARMS: [&str; 10] = [
    "store",
    "bm25",
    "bm25-b1",
    "bm25-window",
    "bm25-stem",
    "bm25-fields",
    "rrf",
    "rrf-window",
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
    let bm25_b1 = order_desc(&bm25_with(&plain_q, &plain_docs, 1.0));
    let window = order_desc(&bm25_window(&plain_q, texts));
    let bm25_stem = order_desc(&bm25(&stem_q, &stem_docs));
    let bm25_fields = order_desc(&bm25(&stem_q, &field_docs));
    let fused = rrf(&[store.clone(), bm25_stem.clone()], RRF_K);
    let fused_window = rrf(&[bm25_plain.clone(), window.clone()], RRF_K);
    let own = own_first(pool);
    let own_bm25 = rrf(&[own.clone(), bm25_plain.clone()], RRF_K);
    vec![
        ("store", store),
        ("bm25", bm25_plain),
        ("bm25-b1", bm25_b1),
        ("bm25-window", window),
        ("bm25-stem", bm25_stem),
        ("bm25-fields", bm25_fields),
        ("rrf", fused),
        ("rrf-window", fused_window),
        ("own-first", own),
        ("rrf-own-bm25", own_bm25),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rrf_positive_leaves_out_what_scored_nowhere() {
        // 0 leads list one, 2 leads list two, 1 scored nothing: 0 and 2 tie and keep index order.
        assert_eq!(rrf_positive(&[vec![2.0, 0.0, 0.0], vec![0.0, 0.0, 1.0]], RRF_K), [0, 2]);
        assert_eq!(rrf_positive(&[vec![1.0, 2.0, 0.0], vec![0.0, 3.0, 0.0]], RRF_K), [1, 0]);
        assert!(rrf_positive(&[vec![0.0, 0.0]], RRF_K).is_empty());
    }

    #[test]
    fn words_split_lowercase_and_stem_like_the_store() {
        assert_eq!(
            words("Rollback re-simulates Frames!", false),
            ["rollback", "re", "simulates", "frames"]
        );
        assert_eq!(
            words("Rollback re-simulates Frames!", true),
            ["rollback", "re", "simul", "frame"]
        );
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
        assert_eq!(orders[4].1[0], 1, "stemmed too");
        assert_eq!(
            own_first(&pool),
            [1, 0, 2],
            "own project first, then the rest by their own rank"
        );
    }

    #[test]
    fn the_best_window_beats_breadth() {
        // A long unit with the words scattered, against a short one that answers in one place.
        let scattered = format!(
            "the save set\n{}\nrollback\n{}\nbytes restored\n{}\n277,288",
            "filler line\n".repeat(20),
            "filler line\n".repeat(20),
            "filler line\n".repeat(20)
        );
        let texts = [
            scattered.as_str(),
            "the save set is 277,288 bytes, every rollback restores it",
        ];
        let q = words("how many bytes is the save set a rollback restores", false);
        let whole = bm25(&q, &texts.iter().map(|t| words(t, false)).collect::<Vec<_>>());
        let window = bm25_window(&q, &texts);
        assert!(window[1] > window[0], "{window:?}");
        assert!(whole[1] > whole[0] || whole[0] > 0.0, "{whole:?}");
        assert_eq!(bm25_window(&q, &[]), Vec::<f64>::new());
    }
}
