//! Refusing secret-shaped text before it reaches the journal (team/security JOURNAL-REFUSALS.md,
//! 2026-10-29). One choke point, [`check_fact`], runs before any network call in every path that
//! creates a fact (`note`, `journal_add`, `import_journals`, `ask` record) and in `panel_submit`. The
//! patterns and the must-accept samples are the fixture gw-security wrote (`fixtures/refusal_fixture.toml`,
//! compiled in); the must-refuse samples are built at run time by [`must_refuse`] from fragments and
//! fresh random bytes, so no key-shaped string sits in this public repo. The selftest runs every
//! must-refuse and must-accept string through this code.
//!
//! Step 0 masks Groundworks' own secret references (`bw:ITEM#FIELD`, `env:NAME`, `file:/path`) to the
//! literal `REF`, which is the recommended replacement and must pass. Then the named patterns, with
//! the kv-secret post-filter, then the high-entropy rule in code. A refusal names the pattern, the
//! field and the offset; the matched text is never in the message, the rejection row or the log.

use std::collections::BTreeMap;
use std::sync::OnceLock;

use regex::Regex;
use serde::Deserialize;

/// gw-security's fixture: patterns, the entropy threshold and the must-accept samples.
pub const FIXTURE: &str = include_str!("../fixtures/refusal_fixture.toml");

/// The refusal message, exactly as JOURNAL-REFUSALS.md states it (`<field>`, `<id>`, `<offset>` filled in).
const MESSAGE: &str = "refused: {field} looks like it contains a secret ({id} at character {offset}). Nothing was recorded.\n\
Journal entries are injected into every session that opens this project and are exported nightly, so a value here is a value in every transcript.\n\
Store the value in Bitwarden and write the reference instead: bw:<ITEM>#<FIELD> (for example bw:rise3 server#root password). The registry resolves it at run time through `groundworks run`.\n\
If this is a false positive (a hash, an id, a public key), say which pattern fired and gw-security will adjust the fixture; do not paraphrase the value to get it through.";

/// The file as committed: everything but the must-refuse samples.
#[derive(Deserialize)]
struct FixtureFile {
    entropy_threshold: f64,
    must_accept: Vec<String>,
    patterns: BTreeMap<String, String>,
}

pub struct Fixture {
    pub entropy_threshold: f64,
    /// `[pattern id, text]` pairs that must be refused, built fresh by [`must_refuse`].
    pub must_refuse: Vec<(String, String)>,
    pub must_accept: Vec<String>,
    /// Pattern id -> Rust regex, in the order they are tried.
    pub patterns: BTreeMap<String, String>,
}

impl Fixture {
    pub fn load() -> Result<Self, toml::de::Error> {
        let file: FixtureFile = toml::from_str(FIXTURE)?;
        Ok(Self {
            entropy_threshold: file.entropy_threshold,
            must_refuse: must_refuse(),
            must_accept: file.must_accept,
            patterns: file.patterns,
        })
    }
}

const ALNUM: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
const UPPER_DIGIT: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
const BASE64: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/// xorshift64* seeded from the process's random hasher keys: fresh bytes every run, no dependency.
struct Noise(u64);

impl Noise {
    fn new() -> Self {
        use std::hash::BuildHasher;
        Self(std::collections::hash_map::RandomState::new().hash_one(0x6777_u64) | 1)
    }

    fn below(&mut self, n: usize) -> usize {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        (self.0.wrapping_mul(0x2545_F491_4F6C_DD1D) >> 32) as usize % n
    }

    fn draw(&mut self, alphabet: &[u8], n: usize) -> String {
        (0..n).map(|_| alphabet[self.below(alphabet.len())] as char).collect()
    }

    /// `n` characters, no two alike (so the Shannon entropy is exactly log2 n), holding a digit, a
    /// lower- and an upper-case letter, and not starting with the kv post-filter's `ref`.
    fn distinct(&mut self, alphabet: &[u8], n: usize) -> String {
        loop {
            let mut pool = alphabet.to_vec();
            for i in 0..n {
                let j = i + self.below(pool.len() - i);
                pool.swap(i, j);
            }
            let s: String = pool[..n].iter().map(|&b| b as char).collect();
            let has = |f: fn(char) -> bool| s.chars().any(f);
            if has(|c| c.is_ascii_digit())
                && has(|c| c.is_ascii_lowercase())
                && has(|c| c.is_ascii_uppercase())
                && !s.to_ascii_lowercase().starts_with("ref")
            {
                return s;
            }
        }
    }
}

/// Unpadded base64url, for the JWT samples.
fn b64url(bytes: &[u8]) -> String {
    const A: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let n = chunk
            .iter()
            .enumerate()
            .fold(0u32, |n, (i, &b)| n | u32::from(b) << (16 - 8 * i));
        for i in 0..=chunk.len() {
            out.push(A[(n >> (18 - 6 * i) & 63) as usize] as char);
        }
    }
    out
}

/// The must-refuse samples gw-security's generator wrote, in the same shapes and lengths, built here
/// so that none is a literal in the source: every vendor prefix is joined from two fragments and every
/// token body is fresh random bytes. One entry per sample the generator had, plus `age-secret-key`,
/// which it had none for.
pub fn must_refuse() -> Vec<(String, String)> {
    let mut r = Noise::new();
    let cat = |parts: &[&str]| parts.concat();
    let pk = ["PRIVATE", "KEY"].join(" ");
    let jwt = |header: &str, payload: &str, sig: &str| {
        format!("{}.{}.{sig}", b64url(header.as_bytes()), b64url(payload.as_bytes()))
    };
    let jwt_sig = r.draw(ALNUM, 43);
    let samples: Vec<(&str, String)> = vec![
        (
            "pem-private-key",
            format!(
                "-----BEGIN OPENSSH {pk}-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n-----END OPENSSH {pk}-----"
            ),
        ),
        ("pem-private-key", format!("key is -----BEGIN RSA {pk}----- MIIE...")),
        ("pem-private-key", format!("-----BEGIN PGP {pk} BLOCK-----")),
        (
            "stripe-key",
            format!("stripe says {}{}", cat(&["sk_", "live_"]), r.draw(ALNUM, 24)),
        ),
        (
            "stripe-key",
            format!("test key {}{} works", cat(&["sk_", "test_"]), r.draw(ALNUM, 24)),
        ),
        (
            "github-token",
            format!("token {}{}", cat(&["gh", "p_"]), r.draw(ALNUM, 36)),
        ),
        (
            "github-token",
            format!(
                "{}{}_{}",
                cat(&["github_", "pat_"]),
                r.draw(ALNUM, 22),
                r.draw(ALNUM, 59)
            ),
        ),
        (
            "aws-access-key",
            format!("{}{}", cat(&["AK", "IA"]), r.draw(UPPER_DIGIT, 16)),
        ),
        (
            "aws-access-key",
            format!("key id {}{}", cat(&["AS", "IA"]), r.draw(UPPER_DIGIT, 16)),
        ),
        (
            "openai-anthropic-key",
            format!("{}{}", cat(&["sk-", "ant-api03-"]), r.draw(ALNUM, 40)),
        ),
        (
            "openai-anthropic-key",
            format!("OPENAI {}{}", cat(&["sk", "-"]), r.draw(ALNUM, 48)),
        ),
        (
            "slack-token",
            format!("{}{}-{}", cat(&["xo", "xb-"]), r.draw(ALNUM, 12), r.draw(ALNUM, 24)),
        ),
        ("google-api-key", format!("{}{}", cat(&["AI", "za"]), r.draw(ALNUM, 35))),
        ("gitlab-token", format!("{}{}", cat(&["gl", "pat-"]), r.draw(ALNUM, 20))),
        (
            "age-secret-key",
            format!("{}{}", cat(&["AGE-SECRET-", "KEY-1"]), r.draw(UPPER_DIGIT, 58)),
        ),
        (
            "jwt",
            jwt(
                r#"{"alg":"HS256","typ":"JWT"}"#,
                r#"{"sub":"1234567890","name":"John Doe","iat":1516239022}"#,
                &jwt_sig,
            ),
        ),
        (
            "jwt",
            format!("short {}", jwt(r#"{"alg":"none"}"#, r#"{"a":1}"#, "abcdefghij")),
        ),
        ("bearer", format!("Authorization: Bearer {}", r.draw(ALNUM, 40))),
        (
            "bearer",
            format!("curl -H 'Authorization: Bearer v1.0-{}'", r.draw(ALNUM, 30)),
        ),
        ("basic-auth", "Authorization: Basic dXNlcjpQYTU1d29yZA==".to_owned()),
        (
            "url-credential",
            "postgres://app:Pa55w0rd@db.internal:5432/app".to_owned(),
        ),
        (
            "url-credential",
            format!("https://deploy:{}@git.example.com/x.git", r.distinct(ALNUM, 12)),
        ),
        ("kv-secret", "password=hunter22".to_owned()),
        ("kv-secret", format!("GROUNDWORKS_OWNER_PASS={}", r.distinct(ALNUM, 16))),
        ("kv-secret", format!("\"password\": \"{}\"", r.distinct(ALNUM, 10))),
        (
            "kv-secret",
            format!("the root password is: {} (do not share)", r.distinct(ALNUM, 12)),
        ),
        ("kv-secret", format!("CF_API_TOKEN: {}", r.distinct(ALNUM, 40))),
        ("kv-secret", format!("api_key = {}", r.distinct(ALNUM, 24))),
        ("kv-secret", "DB_PASSWORD=changeme2".to_owned()),
        (
            "high-entropy",
            format!(
                "BW_SESSION came back as {}{}",
                r.distinct(BASE64, 64),
                r.draw(BASE64, 24)
            ),
        ),
        (
            "high-entropy",
            format!(
                "the cloudflare token {}-{}",
                r.distinct(ALNUM, 8),
                r.distinct(ALNUM, 31)
            ),
        ),
        ("high-entropy", r.distinct(ALNUM, 40)),
    ];
    samples.into_iter().map(|(id, text)| (id.to_owned(), text)).collect()
}

/// A pattern that fired: which, where (0-based character offset in the field) and how long.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hit {
    pub id: &'static str,
    pub offset: usize,
    pub len: usize,
}

/// The refusal a caller sees. Displays as the exact message; carries the row the rejection table keeps.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refusal {
    pub id: &'static str,
    pub field: &'static str,
    pub offset: usize,
    pub len: usize,
}

impl std::fmt::Display for Refusal {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(
            &MESSAGE
                .replace("{field}", self.field)
                .replace("{id}", self.id)
                .replace("{offset}", &self.offset.to_string()),
        )
    }
}

impl std::error::Error for Refusal {}

struct Compiled {
    patterns: Vec<(&'static str, Regex)>,
    threshold: f64,
}

fn compiled() -> &'static Compiled {
    static C: OnceLock<Compiled> = OnceLock::new();
    C.get_or_init(|| {
        let fixture = Fixture::load().expect("the compiled-in refusal fixture parses");
        // The fixture's table order is the policy's order (a BTreeMap would sort it); kv-secret and
        // the prefixed-key patterns are tried in the order JOURNAL-REFUSALS.md lists them.
        let order = [
            "pem-private-key",
            "stripe-key",
            "github-token",
            "aws-access-key",
            "openai-anthropic-key",
            "slack-token",
            "google-api-key",
            "gitlab-token",
            "age-secret-key",
            "jwt",
            "bearer",
            "basic-auth",
            "url-credential",
            "kv-secret",
        ];
        let mut patterns = Vec::with_capacity(order.len());
        for id in order {
            let re = fixture
                .patterns
                .get(id)
                .expect("every listed pattern is in the fixture");
            patterns.push((
                id,
                Regex::new(re).expect("fixture regexes compile under the regex crate"),
            ));
        }
        for id in fixture.patterns.keys() {
            assert!(
                order.contains(&id.as_str()),
                "fixture pattern {id} is not in the evaluation order"
            );
        }
        Compiled {
            patterns,
            threshold: fixture.entropy_threshold,
        }
    })
}

fn ref_mask() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\b(?:bw|env|file):[^\s,;)\]}]+(?:#[^\s,;)\]}]+)?").expect("ref mask"))
}

fn candidate() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[A-Za-z0-9+/=_-]{32,}").expect("entropy candidate"))
}

const PLACEHOLDERS: [&str; 17] = [
    "redacted",
    "placeholder",
    "unknown",
    "none",
    "null",
    "nil",
    "example",
    "omitted",
    "elided",
    "hidden",
    "masked",
    "secret",
    "changeme",
    "true",
    "false",
    "...",
    "xxxxxx",
];
const PUBKEY_PREFIXES: [&str; 3] = ["AAAAB3NzaC1", "AAAAC3NzaC1", "AAAAE2VjZHNh"];

/// The kv-secret post-filter: a captured value that is a documented placeholder, a masked reference
/// or a run of one character is not a secret.
fn kv_value_ok(value: &str) -> bool {
    let v = value.trim_end_matches(['.', ',', ';', ':', ')']).to_lowercase();
    if PLACEHOLDERS.contains(&v.as_str()) || v.starts_with("ref") {
        return true;
    }
    let mut chars = v.chars();
    let first = chars.next();
    first.is_some() && chars.all(|c| Some(c) == first)
}

/// Shannon entropy in bits per character.
pub fn entropy(s: &str) -> f64 {
    let mut counts: BTreeMap<char, usize> = BTreeMap::new();
    for c in s.chars() {
        *counts.entry(c).or_default() += 1;
    }
    let n = s.chars().count() as f64;
    -counts
        .values()
        .map(|&c| {
            let p = c as f64 / n;
            p * p.log2()
        })
        .sum::<f64>()
}

/// Byte offset -> character offset in `text`.
fn char_offset(text: &str, byte: usize) -> usize {
    text[..byte].chars().count()
}

/// The first pattern that fires on `text`, if any. Offsets are into the masked text, which differs from
/// the original only where a reference was replaced; the field and the id are what the writer needs.
pub fn check(text: &str) -> Option<Hit> {
    let c = compiled();
    let masked = ref_mask().replace_all(text, "REF");
    for (id, re) in &c.patterns {
        for caps in re.captures_iter(&masked) {
            let m = caps.get(0).expect("whole match");
            if *id == "kv-secret" && caps.get(1).is_some_and(|v| kv_value_ok(v.as_str())) {
                continue;
            }
            return Some(Hit {
                id,
                offset: char_offset(&masked, m.start()),
                len: m.as_str().chars().count(),
            });
        }
    }
    for m in candidate().find_iter(&masked) {
        let run = m.as_str();
        let stripped = run.replace('-', "");
        if stripped.chars().all(|c| c.is_ascii_hexdigit()) {
            continue;
        }
        if PUBKEY_PREFIXES.iter().any(|p| run.starts_with(p)) {
            continue;
        }
        let has = |f: fn(char) -> bool| run.chars().any(f);
        if !(has(|c| c.is_ascii_digit()) && has(|c| c.is_ascii_lowercase()) && has(|c| c.is_ascii_uppercase())) {
            continue;
        }
        if entropy(run) >= c.threshold {
            return Some(Hit {
                id: "high-entropy",
                offset: char_offset(&masked, m.start()),
                len: run.chars().count(),
            });
        }
    }
    None
}

/// Refuse a fact whose text, source, owner statement, quote, by or lens looks like it holds a secret.
pub fn check_fact(fact: &crate::store::FactIn) -> Result<(), Refusal> {
    let fields: [(&'static str, Option<&str>); 6] = [
        ("text", Some(fact.text.as_str())),
        ("source", Some(fact.source.as_str())),
        ("owner_statement", fact.owner_statement.as_deref()),
        ("quote", fact.quote.as_ref().map(|q| q.text.as_str())),
        ("by", Some(fact.by.as_str())),
        ("lens", fact.lens.as_deref()),
    ];
    for (field, value) in fields {
        if let Some(value) = value
            && let Some(hit) = check(value)
        {
            return Err(Refusal {
                id: hit.id,
                field,
                offset: hit.offset,
                len: hit.len,
            });
        }
    }
    Ok(())
}

/// Refuse one free-text field (a panel answer).
pub fn check_text(field: &'static str, text: &str) -> Result<(), Refusal> {
    match check(text) {
        Some(hit) => Err(Refusal {
            id: hit.id,
            field,
            offset: hit.offset,
            len: hit.len,
        }),
        None => Ok(()),
    }
}

/// Every fixture sample both ways; the failures, one line each, naming must-refuse samples by index
/// only (so the output can go into a journal). Empty means the implementation matches the contract.
pub fn selfcheck() -> Vec<String> {
    let fixture = match Fixture::load() {
        Ok(f) => f,
        Err(e) => return vec![format!("fixture does not parse: {e}")],
    };
    let mut failures = Vec::new();
    for (i, (want, text)) in fixture.must_refuse.iter().enumerate() {
        match check(text) {
            None => failures.push(format!("must_refuse[{i}] ({want}) was accepted")),
            Some(hit) if hit.id != want => failures.push(format!("must_refuse[{i}] expected {want}, got {}", hit.id)),
            Some(_) => {}
        }
    }
    for (i, text) in fixture.must_accept.iter().enumerate() {
        if let Some(hit) = check(text) {
            failures.push(format!(
                "must_accept[{i}] was refused by {} at {} (len {}): {}",
                hit.id,
                hit.offset,
                hit.len,
                text.chars().take(80).collect::<String>()
            ));
        }
    }
    if fixture.must_refuse.len() < 20 || fixture.must_accept.len() < 20 {
        failures.push("the fixture is too small to prove anything".to_owned());
    }
    for id in fixture.patterns.keys() {
        if !fixture.must_refuse.iter().any(|(want, _)| want == id) {
            failures.push(format!("pattern {id} has no must_refuse sample"));
        }
    }
    failures
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_fixture_proves_the_implementation_both_ways() {
        let failures = selfcheck();
        assert!(failures.is_empty(), "{}", failures.join("\n"));
    }

    #[test]
    fn references_are_masked_and_pass() {
        assert!(check("password: bw:rise3 server#root password").is_none());
        assert!(check("GROUNDWORKS_OWNER_PASS=env:OWNER_PASS").is_none());
        assert!(check("the key file is file:/run/secrets/key.pem").is_none());
        assert!(check("password=hunter22").is_some());
    }

    #[test]
    fn the_refusal_message_names_pattern_field_and_offset_but_never_the_text() {
        let fact = crate::store::FactIn {
            kind: crate::journal::Kind::Status,
            text: "the root password is: lv3xdNS7rQuZ (do not share)".to_owned(),
            confidence: crate::journal::Confidence::Inferred,
            source: "s".to_owned(),
            by: "agent".to_owned(),
            receipt: None,
            owner_statement: None,
            quote: None,
            lens: None,
            attacks: None,
            stance: None,
        };
        let refusal = check_fact(&fact).unwrap_err();
        assert_eq!(refusal.id, "kv-secret");
        assert_eq!(refusal.field, "text");
        let message = refusal.to_string();
        assert!(message.starts_with("refused: text looks like it contains a secret (kv-secret at character "));
        assert!(message.contains("Nothing was recorded."));
        assert!(!message.contains("lv3xdNS7rQuZ"));
        let mut clean = fact.clone();
        clean.text = "the root password is in bw:rise3 server#root password".to_owned();
        assert!(check_fact(&clean).is_ok());
        clean.source = ["AK", "IAIOSFODNN7EXAMPLE"].concat();
        assert_eq!(check_fact(&clean).unwrap_err().field, "source");
    }

    #[test]
    fn entropy_matches_the_policy_measurements() {
        assert!(entropy("aaaa") < 0.01);
        assert!(entropy("LocalPasswordAuthenticationProvider2") < 4.2);
        assert!(entropy(&Noise::new().distinct(BASE64, 64)) > 4.2);
        assert!(kv_value_ok("REDACTED") && kv_value_ok("xxxxxxxx") && kv_value_ok("REF") && !kv_value_ok("hunter22"));
    }

    #[test]
    fn base64url_is_unpadded_rfc4648() {
        assert_eq!(b64url(b"Man"), "TWFu");
        assert_eq!(b64url(b"Ma"), "TWE");
        assert_eq!(b64url(b"M"), "TQ");
        assert_eq!(b64url(&[0xfb, 0xff]), "-_8");
    }

    #[test]
    fn the_built_samples_hold_their_shape_on_many_draws() {
        for _ in 0..200 {
            let failures = selfcheck();
            assert!(failures.is_empty(), "{}", failures.join("\n"));
        }
    }
}
