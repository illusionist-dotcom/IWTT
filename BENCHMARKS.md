# BENCHMARKS (IWTT v0.2)

Every number on this page was **measured in this repository, by
`scripts/benchmark_recall.py`, on the machine described below**. Nothing here is
estimated, extrapolated, or borrowed from anybody else's harness.

This matters because the Graphify dossier's own `BENCHMARKS.md` contains vendor claims
measured by their harness on their corpora. Those numbers are quoted in
`GRAPHIFY_ADAPTATION_NOTES.md` §6 **as their claims, for context only**, and are never
presented as IWTT results.

Reproduce with:

```bash
python3 scripts/benchmark_recall.py --suite --queries 200 --seed 20261016
python3 scripts/benchmark_recall.py --count 1000   --queries 200   # single size
python3 scripts/benchmark_recall.py --count 10000  --queries 200 --keep --data-dir /tmp/vault
```

The script prints a JSON report and **exits non-zero if a published budget is
exceeded**, so it works as a release gate. Budgets are declared in `BUDGETS` at the
top of the script.

---

## 1. Environment

| | |
| --- | --- |
| Machine | Single sandboxed container, one core, no GPU |
| Python | 3.13.16 |
| SQLite | 3.46.1 (FTS5 available, **not used**) |
| Dependencies | Standard library only — no numpy, no networkx, no model |
| Date measured | 2026-10-16 |
| Corpus | Synthetic, generated from a fixed seed (`--seed 20261016`) |
| Queries | 200 per size, mixed: exact, partial, misspelled, vague, quoted, and deliberately absent |

**The corpus is adversarial on purpose.** It is assembled from a small vocabulary (18
subjects × 14 verbs × 16 objects × 10 qualifiers × 12 tags), so query terms are common
and lexical selectivity is poor. A real vault of 10,000 diverse personal memories has
far more distinctive vocabulary, and a far smaller fraction of it matches any one
query. These numbers are a worst case, not a typical case.

Relationships are sparse: one edge per four thoughts (`RELATED_TO`), so 249 edges at
1,000 thoughts and 2,499 at 10,000.

---

## 2. Results

| | **1,000 thoughts** | **10,000 thoughts** |
| --- | --- | --- |
| Result | **PASS** | **PASS** |
| Vault file | 17.6 MB | 168.2 MB |
| Relationships / tags / revisions / events | 249 / 12 / 1,000 / 2,263 | 2,499 / 12 / 10,000 / 22,546 |
| Insert 1,000 / 10,000 thoughts | 2.00 s | 23.30 s |
| **Full index build** | **0.58 s** | **11.36 s** |
| Index rows: tokens / trigrams / terms | 16,723 / 71,106 / 1,502 | 167,523 / 713,849 / 13,611 |
| **Recall p50** | **84.5 ms** | **236.2 ms** |
| **Recall p95** | **154.5 ms** | **440.9 ms** |
| Recall p99 | 183.9 ms | 574.3 ms |
| Recall max | 197.1 ms | 661.1 ms |
| 200 queries, total | 17.36 s | 49.54 s |
| **Candidates examined, mean** | **421** | **619** |
| **Candidates examined, max** | **736** | **964** |
| …as a fraction of the vault | 73.6 % | **9.6 %** |
| Graph expansion p95 | 2.6 ms | 2.5 ms |
| Results returned, mean | 19.6 | 19.6 |
| **Add 50 thoughts + 1 recall** | **0.34 s** | **0.79 s** |
| Cluster rebuild | 0.014 s | 1.24 s |
| `graph_view_model(max_nodes=200)` | 0.023 s (truncated) | 0.637 s (truncated) |
| **Peak RSS** | **89 MB** | **279 MB** |
| RSS growth over baseline | 65 MB | 190 MB |
| **Ranking mismatches on repeat** | **0 / 50** | **0 / 50** |

Outcome-state distribution over the 200 queries (1,000 / 10,000): `AMBIGUOUS` 118 /
126, `NO_MATCH` 43 / 43, `STRONG_MATCH` 35 / 28, `POSSIBLE_MATCH` 4 / 3. The 43
`NO_MATCH` results are the deliberately absent queries ("quantum chromodynamics
renormalisation", "medieval tax law in Burgundy", "sourdough hydration percentages"):
**every one of them correctly returned nothing**, at both sizes.

---

## 3. The scaling claim, and its proof

Candidate generation is bounded by an **absolute cap** — `token_candidate_cap = 600`
plus `trigram_candidate_cap = 400` — not by the size of the vault. So the interesting
question is not "how fast is it" but "does the work grow with the vault". It does not:

```
vaultGrowth:                    10.0×     (1,000 → 10,000 thoughts)
candidateGrowth:                 1.31×    (736 → 964 candidates examined)
candidateFractionSmallVault:    73.6 %
candidateFractionLargeVault:     9.6 %
sublinear:                      true
```

`--suite` mode asserts this and fails if either property breaks: candidates must grow
more slowly than the vault, and the examined fraction must shrink. The 73.6% figure at
1,000 thoughts is the cap doing its job on a tiny, low-diversity corpus — it is not a
corpus scan, because the same cap yields 9.6% at 10,000 and would yield under 1% at
100,000.

**No giant in-memory canonical graph.** Peak RSS is 279 MB at 10,000 thoughts, and
that figure is `ru_maxrss` for the whole process including the Python interpreter, the
SQLite page cache, and the 200-query benchmark harness itself. The graph lives in
`recall_graph_cache` (a SQLite table); `GraphViewModel` is an induced subgraph bounded
by `max_nodes`, and reports `bounds.truncated = true` when it stops short. Nothing in
the recall path materialises all thoughts.

---

## 4. Budgets

Declared in the script, checked on every run:

| | 1,000 | 10,000 | Measured 1,000 | Measured 10,000 |
| --- | --- | --- | --- | --- |
| Index build | ≤ 60 s | ≤ 300 s | 0.58 s | 11.36 s |
| Recall p95 | ≤ 250 ms | ≤ 600 ms | 154.5 ms | 440.9 ms |
| Recall max | ≤ 1,500 ms | ≤ 4,000 ms | 197.1 ms | 661.1 ms |
| Peak RSS | ≤ 600 MB | ≤ 1,200 MB | 89 MB | 279 MB |
| Candidates examined | ≤ 1,000 (absolute cap) | ≤ 1,000 | 736 | 964 |
| Ranking mismatches | 0 | 0 | 0 | 0 |

---

## 5. What was measured, and what it cost to get there

These numbers are the result of four measured fixes, each found by profiling rather
than guessed. Before them, recall at 1,000 thoughts was **p50 292 ms / p95 491 ms**,
and adding 50 thoughts to a 10,000-thought vault took **45.4 s**.

| Fix | Where | Before → after (1,000 thoughts) |
| --- | --- | --- |
| Memoize pure text analysis (`morphology_forms`, `recall_variants`, `word_trigrams`, `cached_normalize`) — bounded LRU, keyed by exact input, so a hit and a miss return identical values | `recall/text.py` | ~1,000,000 trigram recomputations per 40 queries → 0 |
| Memoize pure per-candidate work (`token_forms`, `variant_forms`, `length_buckets`) and refactor `term_tier` into a cached pure core | `recall/scoring.py` | p50 292 → 172 ms |
| Cache `fuzzy_ratio` / `best_window`; `min()` instead of `sorted()[0]`; batch the per-edge degree/title lookups that were an N+1 | `recall/text.py`, `scoring.py`, `expansion.py` | p50 172 → 74 ms |
| `CROSS JOIN` so SQLite probes the index instead of scanning it; skip ubiquitous trigrams above a calibrated postings cap | `recall/index.py` | p95 665 → 441 ms at 10,000 |
| De-quadratise clustering (inverted-index remap, single-pass internal-edge counts) and skip reclustering when `graph_signature` is unchanged | `recall/clusters.py`, `index.py` | cluster rebuild 30 s → 1.24 s at 10,000; incremental sync 45.4 s → 0.79 s |

Query plans, verified with `EXPLAIN QUERY PLAN`:

```
before:  SCAN t USING INDEX recall_trigrams_thought_idx      (all 71,872 rows)
         SCAN t USING AUTOMATIC COVERING INDEX               (all 16,896 rows)
after:   SCAN q                                              (~40 query rows)
         SEARCH t USING INDEX sqlite_autoindex_recall_trigrams_1 (trigram=?)
         SEARCH t USING INDEX recall_tokens_token_idx        (token=?)
```

Same rows, same order, cost proportional to the matches instead of to the corpus.

---

## 6. What is *not* benchmarked here, honestly

* **No million-node benchmark.** The dossier records that Graphify's own harness
  failed with out-of-memory errors at 500k and 1M nodes. IWTT was not run at those
  sizes, and no claim is made about them.
* **No concurrent-user benchmark.** IWTT is single-user and local-first; the server is
  a `ThreadingHTTPServer` over one SQLite file with `busy_timeout = 15000`.
* **No cold-cache vs warm-cache distinction in the headline numbers.** The reported
  figures include the process's memoization caches in whatever state the query
  sequence left them. Measured separately at 1,000 thoughts: cold p50 74 ms / p95
  121 ms, warm p50 60 ms / p95 81 ms — i.e. the caches help repeated queries, and the
  published numbers are not the flattering ones.
* **No network latency**, because there is no network. Recall makes no requests.
* **Index build time excludes clustering** in the `durationMs` field of the build
  report; the `indexBuildSeconds` figure in the table includes it. At 10,000 thoughts
  the build report says 11,085 ms and clustering adds ~1.2 s.
* **Insert time is IWTT's canonical write path** (one transaction per thought, with
  revisions, events and search-document maintenance), not a bulk loader. It is
  reported so the total cost of the experiment is visible, not because it is a recall
  number.
