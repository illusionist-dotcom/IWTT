# SEARCH_AND_QUERY.md

**Audit 3 — Search / query** and **Audit 6 — Graph traversal / path queries.**
Verified against `graphify/serve.py` (3,007 LOC) at commit
`10130e79ed9caf8f60d998073e097531eaa1a395`.

> The mission brief says: *do not infer from buzzwords — verify from code.*
> This file is written to that instruction. Every claim below is anchored to a
> named function or constant in the source.

---

## 1. The headline verification: what "no vector store" means in implementation

`README.md` line 38 claims: **"Not a vector index. No embeddings, no vector store:
a real graph you traverse."** Verified literally, by grep over `graphify/*.py`:

| Term | Files matching | Meaning |
|---|---|---|
| `bm25` | **0** | No BM25 lexical ranking |
| `fts5` | **0** | No SQLite FTS / full-text index |
| `vector_store` / `vector store` | **0** | No vector store |
| `embedding` | 5 (all false positives: YAML escaping, Go interface "embedding", HTML attribute embedding) | **No embeddings** |
| `cosine` | **0** | No vector similarity |
| `tf-idf` / `tfidf` | **0** | No classic TF-IDF library |
| `inverted index` | **0** | No inverted index (a trigram postings map exists instead) |
| `pagerank` | 1 (a *display-field* lookup in `callflow_html.py:985`) | **No PageRank computation** |

So the claim is **true, and stronger than it sounds**: there is no statistical
retrieval at all — no BM25, no FTS, no vectors. Retrieval is (a) hand-weighted
lexical matching over a candidate set produced by a trigram map, then (b) graph
expansion.

The only acknowledgment of embeddings in the whole query path is a comment stating
an external ranker can supply **explicit start nodes** — i.e. embeddings are a
possible *seed generator*, never a stored index. That is a design invitation IWTT
can use: keep the graph traversal canonical, treat any semantic ranker as a
replaceable, optional seeder.

## 2. The retrieval pipeline, stage by stage

```
question
  → _query_terms()          tokenize, drop stopwords (EN + DE + Romance), segment CJK
  → _score_query()          score every candidate node on tiered lexical evidence
      ├ _compute_idf()      IDF weight per term, cached on the graph object
      └ _trigram_candidates() candidate prefilter (with whole-graph fallback)
  → _pick_seeds()           choose ≤3 BFS seeds; guarantee a seed per matched term
  → _bfs() / _dfs()         expand with a hub-suppression threshold
  → _complete_induced_edges() close the induced subgraph
  → _subgraph_to_text()     render under a hard character budget
  → text result to the agent
```

### 2.1 Tokenization (`_search_tokens`, `_query_terms`)

- Diacritics folded (`NFKD` + drop combining marks), lowercase, `[^\W_]+`.
- **`_` is a separator**, the same as `-`, so `snake_case`, `kebab-case` and
  `snake case` all tokenize identically. The docstring records the bug this fixed:
  the graph could not find the underscore spelling of its own `local_id`.
- Question/filler words removed via `_QUERY_STOPWORDS` (English plus common
  German/Romance fillers), with a fallback to unfiltered terms when the query is
  entirely stopwords — so a bare "how does it work" still seeds on something.
- CJK: jieba segmentation when installed; a bigram fallback otherwise, always
  keeping the original term for exact matching. `_is_searchable()` keeps any
  non-ASCII term regardless of length but requires >2 chars for pure-ASCII.

**Transferable immediately.** This is a small, complete, dependency-light
tokenizer with three hard-won properties (separator unification, stopword
fallback, CJK handling) that IWTT's lexical layer will need on day one.

### 2.2 Scoring (`_score_query`, the core of the system)

Every candidate node is scored on the *same single pass*, accumulating tiers:

| Tier | Constant | Applied to |
|---|---|---|
| Full-query exact/prefix | `×10` of the per-token tier, weighted by the rarest term's IDF | whole query vs `norm_label` / label-tokens / node id |
| Exact token | `_EXACT_MATCH_BONUS = 1000.0` | `t == norm_label` or bare label |
| Prefix token | `_PREFIX_MATCH_BONUS = 100.0` | label starts with `t` |
| Substring token | `_SUBSTRING_MATCH_BONUS = 1.0` | `t in norm_label` |
| Source path | `_SOURCE_MATCH_BONUS = 0.5` | `t in source_file` |
| Rationale prose | `_RATIONALE_MATCH_BONUS = 0.75` | `t in rationale` |
| Attributes | `_ATTRIBUTE_MATCH_BONUS = 0.75` | `t` in flattened attributes |

Then two corrections that make it behave on real queries:

1. **IDF weighting**: `w = log(1 + N/(1 + df))` where `df` counts nodes whose
   `norm_label` contains the term (`_compute_idf`, cached in
   `G.graph['_idf_cache']`). Common words like `error` get cheap; rare
   identifiers get expensive.
2. **Coverage scaling**: `score += tiered * (matched / n_terms) ** 2`. Squared
   deliberately — the docstring explains that at *linear* coverage a 1-of-10-terms
   exact match still outscored a 3-of-10 prefix+substring match. Only
   label-matching counts toward coverage; source-path hits deliberately do not
   (a colliding leaf whose directory happens to share query words must not win
   back the exact tier).

Terms are de-duplicated order-preservingly, and only the strongest tier per term
counts (exact > prefix > substring) so one term cannot double-count.

**Why this matters for IWTT's Recall goal.** The user's target experience —
*"I remember thinking something about enemies learning how I fight"* — is exactly
the case this scorer is tuned for: multiple ordinary content words, no rare
identifier, and the answer may live in prose (`rationale`) rather than a title.
The rationale and attribute tiers are the mechanism that makes a fuzzy prose
memory retrievable when the label doesn't contain the remembered words.

### 2.3 Candidate prefilter (`_get_trigram_index`, `_trigram_candidates`)

- Lazily builds `{trigram → postings array}` over every node's search text
  (label + rationale + attributes + source), cached on `G.graph` so it dies with
  the graph object.
- Returns a **superset** of matches; the exact predicates re-score afterwards.
- **Falls back to a full scan** (`return None`) when a needle is too short to
  trigram, or when its rarest trigram still covers more than `guard_frac = 0.10`
  (10%) of nodes. The docstring calls this the "never-worse contract": the index
  can only speed things up, never change results.
- Per-trigram id-sets are memoized across queries within one graph generation.

**Transferable immediately**, including the guard. For a personal corpus the
guard will usually trigger (small N), which is the correct behaviour: no index
overhead when the index isn't selective.

### 2.4 Seed selection (`_pick_seeds`)

- `max_k = 3`, `gap_ratio = 0.2`: stops adding seeds when a candidate's score
  falls more than 20% below the top score.
- A documented failure mode (#1445): one incidental exact match (score ~1000)
  outscores every substring match (~1.0) by 1000×, so the gap cutoff silently
  discarded all other seeds and the BFS explored only the wrong neighbourhood.
  The fix **guarantees at least one seed per distinct query term that matched
  anything at all**, using per-term winners computed in the same scoring pass.
- Seeds are deduplicated by *normalized label* so dozens of identically-labelled
  nodes (e.g. many `handler`) contribute one seed, not all three slots (#1766).
- Tie-break: `(-singleton_score, -degree, label_len, node_id)` — fully
  deterministic.

**Transferable immediately.** Both failure modes are generic to keyword seeding
over personal data: rare-word hijack, and duplicate-label flooding.

### 2.5 Traversal (Audit 6) — `_bfs`, `_dfs`, `_complete_induced_edges`

- Depth-limited: MCP tool exposes `depth` 1–6, default 3.
- **Hub suppression**: `hub_threshold = max(50, p99(degree))`. Nodes at or above
  that degree are *visited* but **not expanded as transit**, unless they are
  seeds. This is what stops a single omnipresent hub (a diary index, a
  "misc" tag, a shared project node) from swallowing every neighbourhood.
- BFS returns `(visited_set, edges_seen)`, then `_complete_induced_edges()` adds
  edges between already-visited nodes so the returned subgraph is not an
  arbitrary tree.
- `_dfs` mirrors BFS with the same hub rule, for "trace one path" queries.

**Path queries**: `_shortest_path_text()` on top of `_path_search_graph(G,
undirected)`, which follows stored arc direction by default with an explicit
`undirected=true` opt-out. Endpoint resolution is where this gets interesting:
`_pick_scored_endpoint()`, `_resolve_path_endpoint()`,
`_resolve_path_scoped_symbol()`, and — critically — **ambiguity refusal**:
`_tier_refusal()`, `_ambiguity_message()`, `_rivals_across_files()` return an
explanation of the rival candidates instead of silently picking one.

**Transferable pattern:** *refuse with an explanation rather than guess.* For
IWTT's "what did this thought become?" queries, a wrong confident path is worse
than "I found three candidates for this thought — which did you mean?".

### 2.6 Context assembly under budget (`_subgraph_to_text`, `_cut_lines_to_budget`)

- `char_budget = token_budget * 3` (default 2000 tokens ≈ 6000 chars).
- **Ordering is by information value, not by discovery order**: seeds first, then
  nodes sorted by `(hop distance from seeds, -degree, id)`. The comment explains
  why: BFS discovery order was discarded upstream, and without hop-distance
  ranking the node that actually answers the query got pushed past the budget cut
  by incidental high-degree hubs.
- Each node renders as one line: label, `src=`, `loc=`, `community=`, plus
  optional `learning=<status>[:stale]` and `attrs={...}` (≤10 pairs, ≤200 chars).
- **Every LLM- or corpus-derived field passes through `sanitize_label()`** before
  concatenation (F-010): a hostile document could otherwise inject ANSI escapes,
  fake log lines, or prompt markup into the model's context.
- `_cut_lines_to_budget()` truncates deterministically and appends a narrowing
  hint rather than silently dropping content.

**Transferable immediately.** A retrieval layer that returns unbounded context is
unusable in practice; the hop-distance ordering rule and the fixed per-node
rendering format are both directly portable to IWTT's recall output.

## 3. What is NOT there

| Not present | Consequence for a prospective adopter |
|---|---|
| Stemming / lemmatization | "fights" vs "fight" only match via substring tiers; there is no morphological normalization |
| Spelling correction / fuzzy matching in the query path | No edit-distance fallback at query time (`rapidfuzz` is used in *dedup*, not in search) |
| Synonymy / semantic expansion | Nothing bridges "enemies" and "adversaries" |
| Query-language features | No boolean operators, no field scoping beyond the `context_filter` edge tag, no phrase queries |
| Multi-hop path semantics | `shortest_path` finds *a* shortest path; nothing reasons about path meaning |
| Temporal reasoning | Graphify's temporal suite measures *rebuild history*; the query layer has no time filters |
| Result fusion (RRF etc.) | Single scoring function; the "hybrid RRF" numbers in BENCHMARKS.md are a *baseline*, not a graphify feature |
| PageRank / centrality in ranking | Centrality exists only inside `analyze.py` for report suggestions (betweenness, k-sampled, seed 42); it never influences query ranking |
| Vector/hybrid retrieval | Absent by design |

These gaps are precisely where IWTT's Recall goal needs *more* than Graphify —
particularly morphology, spelling tolerance, and synonymy, because a human
remembering a thought uses different words, not different identifiers.

## 4. Ranking and centrality, precisely

- **Query ranking**: the tier scorer above. Degree is used only as a *tie-break*
  (and as the hub-suppression/stop rule in traversal).
- **God nodes** (`analyze.py::god_nodes`): degree-based, with
  `exclude_hubs_percentile` to suppress staging/utility super-hubs, and with
  documented exclusions (file nodes, JSON key nodes, leaves like `label()`
  with degree ≤ 1).
- **Surprising connections** (`_surprise_score`): a composite heuristic —
  cross-community bonus, peripheral→hub bonus, cross-language bonus, cross-file
  handling, minus hub penalties — producing a scored list with generated `why`
  strings.
- **Betweenness centrality**: used *only* in `suggest_questions()` as a fallback
  bridge detector, computed with `k=` sampling and `seed=42`, then explicitly
  filtered so "a single high-betweenness god node" cannot dominate all results.
- **PageRank**: not computed anywhere (the single `pagerank` hit is a
  display-field lookup in the HTML call-flow exporter).

**For IWTT:** the useful transferable idea is not "use centrality" but
"centrality is for *suggestions and navigation*, never for *retrieval truth*".
Ranking what the user asked for must remain lexically/structurally honest.

## 5. Implications for IWTT (summary)

**Adopt/adapt (see matrix):** the tokenizer; the tier + IDF + coverage scorer;
the trigram prefilter with fallback; per-term seed guarantees; hub-suppressed
BFS/DFS; hop-distance-ordered, budgeted, sanitized rendering; ambiguity refusal;
deterministic tie-breaks everywhere.

**Design around:** no morphology, no fuzzy query matching, no synonym bridge, no
temporal filters — IWTT's Recall pipeline (`IWTT_RECALL_PIPELINE.md`) treats these
as additions layered over this skeleton, not as things Graphify already solves.

**Ignore:** PR tools, code-symbol-specific resolution tiers (e.g.
`_resolve_python_qualified`, path-scoped symbol resolution), and any tier weight
calibrated for short identifier-like labels. IWTT labels are prose; the weights
must be re-derived and, importantly, re-*tested* against a personal corpus.

## 6. Evidence anchors

`graphify/serve.py`: `_search_tokens` · `_query_terms` · `_QUERY_STOPWORDS` ·
`_has_chinese` · `_segment_chinese` · `_is_searchable` · `_EXACT_MATCH_BONUS`
`_PREFIX_MATCH_BONUS` `_SUBSTRING_MATCH_BONUS` `_SOURCE_MATCH_BONUS`
`_RATIONALE_MATCH_BONUS` `_ATTRIBUTE_MATCH_BONUS` · `_compute_idf` ·
`_trigrams` · `_node_rationale_text` · `_node_attributes_text` ·
`_node_search_text` · `_get_trigram_index` · `_trigram_candidates` ·
`_score_query` · `_score_nodes` · `_pick_seeds` · `_pick_scored_endpoint` ·
`_bfs` · `_dfs` · `_complete_induced_edges` · `_subgraph_to_text` ·
`_cut_lines_to_budget` · `_traversal_view` · `_path_search_graph` ·
`_shortest_path_text` · `_resolve_path_endpoint` · `_resolve_path_scoped_symbol` ·
`_tier_refusal` · `_ambiguity_message` · `_rivals_across_files` ·
tool schemas in `list_tools()`.
`graphify/analyze.py`: `god_nodes` · `_surprise_score` · `_cross_file_surprises` ·
`_cross_community_surprises` · `suggest_questions` · `find_import_cycles` ·
`graph_diff`. `README.md` line 38. `BENCHMARKS.md` (vendor baseline table).
