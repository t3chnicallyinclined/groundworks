# engine/embed: local embeddings and the keyword-vs-vector bake-off

Two tools over the `units.jsonl` contract (`docs/MODULES.md`):

| File | Does | Needs |
|---|---|---|
| `embed.py` | Embeds units with a named local sentence-transformers model and keeps an incremental vector store | GPU venv (torch, sentence-transformers) to encode; stdlib to read or plan |
| `bakeoff.py` | Measures a random control, BM25, each model (dense), and a BM25+dense RRF hybrid against a judged query set | same; BM25 and metrics are stdlib |
| `test_embed.py` | Metric math, BM25, RRF, and the incremental skip logic, tested both ways on synthetic incident logs | stdlib only (encoder mocked) |

## Rules (decided; not up for per-instance change)

1. **Similarity only proposes candidates. It is never evidence.** A vector hit cannot cite, confirm or promote anything; a human or gated write surface reads the unit before it is used.
2. **Always paired with keyword search.** Never ship a vector-only retrieval path; the bake-off always reports BM25 and the hybrid next to the model.
3. **Local models only.** Unit text never leaves the machine. Only model weights are downloaded.
4. **Every vector records its model id and the sha256 of the exact text**, plus `input_key` (hash of the prompt, max sequence length and normalization). A changed text or prompt re-embeds; an unchanged one does not.
5. **Permissive model licenses only** (`docs/LICENSING.md` s.4): for example Qwen3-Embedding (Apache-2.0), bge-m3 (MIT). Never jina-embeddings-v3 (CC-BY-NC-4.0); `scripts/license_check.py` rejects it.
6. **Loud failures.** A model that fails to load is reported by name and the run exits 1. An encoder returning fewer vectors than texts raises. A query set where no query has a relevant unit is `NOT EXECUTED` (exit 2).

## Contracts

**Input** `units.jsonl`: this module reads `id`, `type`, `text` (other contract fields pass through untouched). Empty text is skipped and counted.

**Store** `<out>/<model_slug>/`:
- `vectors.npy`: float32 matrix, C order, one row per unit
- `vectors.jsonl`: `{"id", "model", "dim", "text_sha256", "input_key", "row"}` one per row, same order

**Eval inputs**: `queries.jsonl` `{"id", "text"}`; `qrels.jsonl` `{"query_id", "unit_id", "relevance"}` (relevance >= 1 relevant; graded gain `2^rel - 1` for nDCG).

**models.json**: a list of specs
```json
[{"model": "Qwen/Qwen3-Embedding-0.6B",
  "query_prompt": "Instruct: <one-sentence task>\nQuery:",
  "doc_prompts": {}, "default_doc_prompt": null,
  "max_seq_length": 1024, "batch_size": 16, "dtype": "float16", "normalize": true},
 {"model": "BAAI/bge-m3", "query_prompt": null, "max_seq_length": 1024}]
```
Qwen3-Embedding is instruction-aware: its model card gives queries the form `Instruct: {task}\nQuery:{query}` and documents no prompt, and recommends writing a task sentence for your use case. bge-m3 needs no instruction.

**Bake-off outputs**: `bakeoff_<name>.json` (counts, per-method metrics with intervals, model timings, failures) and `rankings_<name>.json` (top-20 ids per method per query). Ids and numbers only; never text.

## Method

- **random**: seeded shuffle per query; the floor any method must clear.
- **bm25**: Okapi BM25, k1 = 1.2, b = 0.75, lowercase `\w+` tokens, no stemming or stop words (matches SurrealDB's `BM25(1.2,0.75)` parameters, not its tokenizer).
- **dense:\<model\>**: cosine over normalized vectors, top 1000.
- **rrf:\<model\>**: reciprocal-rank fusion of the bm25 and dense lists (each up to 1000 deep), `1/(60 + rank)`.
- **Metrics**: Recall@5/10/20, MRR@10, nDCG@10; mean over judged queries with a 95% percentile bootstrap over queries (2000 resamples, seeded) and a paired bootstrap of the difference from bm25.
- **Verdict**: a method is **better than bm25** on a metric only if its whole interval lies above the bm25 mean **and** the paired difference interval lies above zero. Otherwise it is "not distinguishable" (or "worse" by the mirror rule). With few queries, most comparisons are not distinguishable; say so rather than reading the point estimates.
- Queries with no relevant unit in the corpus are excluded and counted (`queries_without_relevant`); qrels pointing outside the corpus are counted (`qrels_unit_not_in_corpus`).

## Commands

```sh
PY=~/.xscholar/venv-embed/Scripts/python.exe          # CUDA torch + sentence-transformers
$PY engine/embed/embed.py   --units units.jsonl --out vectors/ --models models.json
$PY engine/embed/bakeoff.py --units units.jsonl --queries queries.jsonl --qrels qrels.jsonl \
    --out vectors/ --models models.json --exclude-types <assertion types> --name <eval name>
python -m unittest discover -s engine/embed -v         # no torch needed
```
`--exclude-types` removes unit types that must not be retrieved (for example the assertions whose statements are the queries).

## Loading into SurrealDB (derived database, never the system of record)

Vectors go into a **separate derived database**, rebuilt from the store; the gated graph is never written. Syntax per SurrealDB 3.x docs (`DEFINE INDEX`, `search::rrf`); FULLTEXT replaced SEARCH in 3.0.

```surql
USE NS <ns> DB <derived_db>;
DEFINE TABLE unit SCHEMALESS;
UPSERT unit:<key> SET uid = '<unit id>', type = '<type>', in_record = '<flag>', text = '<text>',
    model = '<model id>', text_sha256 = '<sha256>', embedding = [<dim floats>];
DEFINE ANALYZER unit_words TOKENIZERS class FILTERS lowercase;
DEFINE INDEX unit_text ON unit FIELDS text FULLTEXT ANALYZER unit_words BM25(1.2,0.75);
DEFINE INDEX unit_vec  ON unit FIELDS embedding HNSW DIMENSION <dim> DIST COSINE TYPE F32 EFC 150 M 12;

LET $vs = SELECT id, uid, vector::distance::knn() AS dist FROM unit WHERE embedding <|100,200|> $qv ORDER BY dist;
LET $ft = SELECT id, uid, search::score(1) AS score FROM unit WHERE text @1@ $qt ORDER BY score DESC LIMIT 100;
RETURN search::rrf([$vs, $ft], 10, 60);
```
The query vector `$qv` is computed locally with the same model and query prompt. Measured behaviour on the first instance is recorded in the section below.

## Measured on instance 1 (2026-09-15; numbers only)

Corpus 7,995 retrievable units (assertion units excluded). Eval A: 92 queries (assertion statements), 240 qrels (the units each cites), 92/92 with a relevant unit. Eval B: 23 queries, 14 with a relevant unit, qrels from a heuristic locator parse (weak).

| Eval A (n=92) | Recall@10 | nDCG@10 | MRR@10 |
|---|---|---|---|
| random | 0.000 | 0.000 | 0.000 |
| bm25 | 0.491 [0.415, 0.570] | 0.470 [0.398, 0.549] | 0.594 [0.507, 0.682] |
| dense Qwen3-Embedding-0.6B | 0.247 [0.181, 0.323] worse | 0.183 [0.129, 0.240] worse | 0.218 [0.149, 0.287] worse |
| dense bge-m3 | 0.305 [0.232, 0.387] worse | 0.255 [0.192, 0.326] worse | 0.323 [0.241, 0.415] worse |
| rrf bm25 + Qwen3 | 0.439 [0.357, 0.521] | 0.365 [0.297, 0.440] worse | 0.441 [0.353, 0.534] worse |
| rrf bm25 + bge-m3 | 0.433 [0.355, 0.521] | 0.376 [0.306, 0.456] worse | 0.464 [0.381, 0.558] worse |

**No model or hybrid beat BM25.** Caveat, measured: 69 of 92 queries quote the source text verbatim, which favours keyword search. On the 23 non-quoting queries, Recall@10 was bm25 0.478, bge-m3 dense 0.407, rrf bge-m3 0.483 (no intervals; n too small to separate). Lesson: build a paraphrase query set before deciding vectors add nothing, and keep BM25 as the default ranker until one does.

GPU (RTX 3090, fp16, max 1024 tokens, 8,120 units, 1.88 M chars): Qwen3-Embedding-0.6B 38.0 s (214 units/s, peak 3.4 GB); bge-m3 18.3 s (443 units/s, peak 1.5 GB). A second run re-embedded 0 units.

SurrealDB 3.1.6 derived database: 7,995 records loaded in 49 s (HTTP bodies must stay small: a ~2 MB request returned 413); FULLTEXT + HNSW (1024-d, cosine) indexes built in about 20 s. Findings: `@1@` requires **every** query term (a long query matches nothing); `@1,OR@` gives any-term BM25. Slicing `$x[0:10]` is a parse error. HNSW KNN top-10 matched the offline exact top-10 in 5/5 queries (10/10 each); FULLTEXT top-10 overlapped the offline BM25 by 1-8 of 10 (different tokenizer and scoring details); `search::rrf` top-10 overlapped the offline hybrid by 5-9 of 10, never the same order. Treat the database hybrid as its own ranker and measure it, not as a copy of the offline one.
