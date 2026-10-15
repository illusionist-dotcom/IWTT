# IWTT_RECALL_PIPELINE.md

**Special question — can Graphify help recall?**

A hypothetical hybrid recall pipeline for IWTT, designed from what Graphify
actually does, compared against embedding-only retrieval, with strengths,
weaknesses and an evaluation plan.

**Design only. Not implemented.** IWTT v0.1 is unmodified.

---

## 1. The target experience, stated precisely

> "I remember thinking something about enemies learning how I fight."

What makes this hard:

| Property of the query | Consequence |
|---|---|
| No rare identifier — all ordinary content words | IDF weighting helps only mildly; nothing is decisive |
| The remembered wording differs from the original | Pure lexical matching will miss |
| The user does not remember *when* | Time filtering cannot narrow the search |
| The user does not remember *which project* | Project filtering cannot narrow the search |
| The user can recognise the thought instantly | One correct hit is enough — this is a *recall* problem, not a ranking-precision problem |
| The original text is sacred | Any processing must not modify it; all matching metadata is derived |
| There may be several candidate thoughts | Ambiguity must be resolvable by the user, not silently guessed |

Two more properties matter and are easy to forget:

- The user may be remembering **a thought that was abandoned and resurrected** —
  so lifecycle state must not filter results out, only annotate them.
- The user may be remembering **a relationship** ("thinking something about X and
  Y"), i.e. the answer may be an implicit connection rather than a single note.

## 2. The proposed pipeline

```
   "I remember thinking something about enemies learning how I fight"
        │
        ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ 0. QUERY UNDERSTANDING (derived, cheap, no model required)           │
 │    · tokenize (diacritic-folded, separator-unified)   ← Graphify     │
 │    · stopword removal incl. memory fillers ("remember", "thinking",  │
 │      "something about")                               ← IWTT adds    │
 │    · detect rare terms / quoted phrases / dates / project hints      │
 │    · expand morphology: fight ⇄ fights ⇄ fighting     ← IWTT adds    │
 └──────────────────────────────────────────────────────────────────────┘
        │  terms: [enemies, learn, fight]   (morphological variants included)
        ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ 1. LEXICAL / FUZZY RETRIEVAL (derived index)                         │
 │    · tiered scoring: exact > prefix > substring, IDF-weighted,        │
 │      coverage-scaled                                  ← Graphify     │
 │    · candidate prefilter via trigram postings (with whole-scan        │
 │      fallback guard)                                  ← Graphify     │
 │    · fuzzy/spelling tolerance (edit distance)         ← IWTT adds    │
 │    · search earned_text AND derived prose (AI rationale/summary) but  │
 │      always return the ORIGINAL text as the result    ← Graphify tier │
 └──────────────────────────────────────────────────────────────────────┘
        │  candidate thoughts with lexical evidence + matched-term info
        ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ 2. GRAPH NEIGHBOURHOOD EXPANSION (derived traversal)                 │
 │    · seeds: ≥1 per matched term, deduped by normalized label ← Graphify│
 │    · BFS to depth 1–2 with hub suppression (max(floor, p99 degree))   │
 │    · induced-edge completion                          ← Graphify     │
 │    · traverse CANONICAL relationships (user-confirmed) and DERIVED    │
 │      suggestions separately, with different weight    ← IWTT adds    │
 └──────────────────────────────────────────────────────────────────────┘
        │  thoughts reachable from the lexical hits
        ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ 3. RELATIONSHIP RANKING (derived scores)                             │
 │    · user-confirmed relationships weigh more than suggestions         │
 │    · hop distance from a seed (nearer = stronger)     ← Graphify      │
 │    · hub suppression as a penalty, not a filter       ← Graphify      │
 │    · corroboration/outcome signals (revisited, promoted, corrected)   │
 │      with time decay                                  ← ADAPT of       │
 │                                                         reflect.py     │
 └──────────────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ 4. TEMPORAL / PROJECT / LIFECYCLE CONTEXT (canonical + derived)      │
 │    · detect and apply explicit time hints ("years ago", "last year")  │
 │    · detect project/tag hints and boost matches                       │
 │    · NEVER filter by lifecycle; instead annotate results with state   │
 │      (active · dormant · graveyard · resurrected)     ← IWTT-original │
 │    · revision proximity: a thought revised near other candidates      │
 │      may be part of the same thread                   ← (dedup.py's    │
 │                                                          community     │
 │                                                          boost, adapted)│
 └──────────────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ 5. OPTIONAL SEMANTIC RETRIEVAL (derived, rebuildable, opt-in)        │
 │    · embeddings of the ORIGINAL text and of derived summaries         │
 │    · used ONLY as an additional candidate generator / seed supplier   │
 │      — never as the sole index       ← Graphify's design invitation   │
 │    · disabled by default in a strict local-first posture; if enabled, │
 │      a local model preferred        ← IWTT policy                     │
 └──────────────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ 6. RANKED ORIGINAL THOUGHTS (return canonical text)                  │
 │    · each result shows: original text · date · project/tags ·         │
 │      lifecycle state · WHY it was returned (matched terms, path,      │
 │      suggested-vs-confirmed) · confidence of any AI-derived match     │
 │    · ambiguity → ask, don't guess                     ← Graphify      │
 │    · token/space budget enforced, ordered by value    ← Graphify      │
 └──────────────────────────────────────────────────────────────────────┘
```

## 3. Worked example

Query: **"I remember thinking something about enemies learning how I fight."**

| Stage | What happens | Notes |
|---|---|---|
| 0 | Drops "remember/thinking/something about" as memory filler → terms `{enemies, learn, fight}`; adds `enemy`, `learns`, `learning`, `fights`, `fighting` by morphology | Graphify's stopword list is question-word based; IWTT must add *memory* fillers — a small but essential delta |
| 1 | Substring/prefix matches on those terms find thoughts containing "enemy", "learn", "fight". A thought phrased "adaptive guards that study player attack patterns" matches **nothing here** | This is where lexical-only recall fails — it is the *expected* failure, not a bug |
| 2 | From the (partial) lexical hits, BFS reaches neighbours: a combat-AI project thought, an experiment note, a design doc that mentions "fight" only in a link title | Graphify's seed guarantee is what prevents one term ("fight") from consuming all seeds; hub suppression prevents the project index note from flooding the neighbourhood |
| 3 | Neighbours that are *user-confirmed* neighbours rank above *suggested* ones; closer hops above farther; corroborated (revisited) above never-revisited | The canonical/derived distinction becomes a ranking signal — this is IWTT-specific and is the reason the distinction must be modelled explicitly |
| 4 | No explicit date hint; the resurrected status of one candidate is displayed, not used to exclude it | A candidate the user abandoned two years ago and resurrected last week is often exactly what they are reaching for |
| 5 | Semantic retrieval (if enabled) bridges "enemies learning how I fight" ↔ "adaptive guards that study player attack patterns", supplying that missing candidate as a *seed*, which then expands through the graph | This is the stage that closes the vocabulary gap — and the reason it belongs *after* the deterministic stages, not as the whole system |
| 6 | Returns ~3 original thoughts, each with the reason it matched, plus "did you mean?" for the two close candidates | The user recognises the right one instantly |

**Key observation:** stage 1 alone would fail this query; stage 5 alone would
plausibly find it but could not explain it, could not distinguish confirmed from
suggested context, and would silently drop lifecycle context. The pipeline's value
is that each stage covers a different failure mode of the others.

## 4. Evidence from Graphify's own benchmark claims

`BENCHMARKS.md` (vendor-authored, harness not published — **claims, not verified
here**) is instructive for one specific reason: it compares retrieval families on a
*conversational memory* dataset (LOCOMO), which is much closer to IWTT's problem
than a code benchmark.

| System | LOCOMO QA | recall@10 |
|---|---|---|
| graphify (graph-expand) | 45.3% | **0.497** |
| hybrid RRF | 43.3% | 0.493 |
| graphify (SurrealDB engine) | 43.3% | 0.485 |
| dense RAG | 41.3% | 0.439 |
| BM25 | 31.3% | 0.362 |
| mem0 | 27.3% | 0.048 |
| supermemory | 49.7% | 0.149 |

And the ablation that matters most for pipeline design: **"A seed-only ablation
(no graph expansion) still scores 42.7%"** — i.e. on this dataset, graph expansion
bought ~2.6 points of QA accuracy over the lexical seeds alone.

Two honest readings:

1. **Lexical seeding carries most of the weight.** A well-built lexical stage over
   good derived text gets you most of the way; the graph is an incremental
   improvement, not a silver bullet. Anyone building IWTT recall should invest in
   stage 1 and 6 first.
2. **Graph expansion's value is real but dataset-dependent.** LOCOMO is
   multi-session conversation with strong topical continuity — the ideal case for
   neighbourhood expansion. A personal thought vault has more idiosyncratic,
   sparsely-linked content, where expansion may help less. **ESTIMATED.** This
   must be measured on IWTT data (see §6), not assumed from these numbers.

Also relevant: dense RAG (41.3% / 0.439) beats BM25 (31.3% / 0.362) but loses to
lexical+graph on both axes here, and the vendor explicitly notes
supermemory's recall figure is "embedder-confounded". No claim in the table should
be treated as settled.

## 5. Hybrid pipeline versus embedding-only retrieval

### 5.1 Strengths of the proposed hybrid

| Strength | Mechanism |
|---|---|
| **Explainability** | Every result can state why it matched (terms, path, hop distance). Embeddings cannot explain a cosine score |
| **Exact-form recovery** | Rare words, names, invented terms, quoted phrases, code identifiers, numbers ("v0.3", "enemy_ai_v2") are matched exactly rather than approximated |
| **Negation and modifiers survive** | "not the enemy AI thing" is expressible in lexical filters; embeddings notoriously smear negation |
| **Deterministic and testable** | Fixed inputs produce fixed rankings; regressions are diffable — impossible with a model-in-the-loop index |
| **No model required** | The whole pipeline runs offline with no dependency beyond stdlib + a graph library; semantic stage is optional |
| **Rebuildable** | Every index is derived; deleting all of it leaves canonical history intact |
| **Structural recall** | Neighbourhood expansion finds thoughts that share *no* words with the query — the one thing lexical search cannot do |
| **Context-aware ranking** | Confirmed vs suggested, lifecycle state, recency, project — all first-class ranking signals |
| **Ambiguity handling** | Multiple candidates are surfaced with an explanation instead of one confident guess |
| **Local-first compatible** | Nothing about stages 0–4 requires network access |

### 5.2 Weaknesses of the proposed hybrid

| Weakness | Mitigation |
|---|---|
| **Vocabulary mismatch** is the core failure (stage 1 miss) | The semantic stage exists precisely for this; morphology and derived-prose indexing reduce but do not eliminate it |
| **Index and pipeline complexity** | Four derived indexes (lexical, trigram, graph projection, optional vectors) to keep coherent; versioning everything (as Graphify does) is mandatory |
| **Tuning surface** | Tier weights, coverage exponent, hub floor, decay half-life, hop penalty — all need calibration and regression tests, or the system silently degrades |
| **Graph quality dependency** | Expansion quality depends entirely on the relationship graph; a sparse vault yields little expansion benefit (Graphify's own ablation hints the gain may be modest) |
| **Cold start** | A new vault has few links and no outcome signals; recall is essentially lexical until the graph matures |
| **Derived-data drift** | AI summaries used for lexical matching can go stale relative to revised thoughts — mitigated by revision-keyed invalidation |
| **Latency at scale** | Measured on Graphify: the lexical scoring pass dominates query time and grows linearly when the prefilter guard trips (~1 s at 100k nodes) |

### 5.3 Embedding-only retrieval, assessed honestly

| Dimension | Embedding-only |
|---|---|
| Vocabulary mismatch | **Strong** — the main reason people adopt it |
| Exact-form recovery | **Weak** — names, invented terms, numbers, rare identifiers get smeared into neighbourhoods |
| Negation / modifiers | **Weak** — well-documented failure mode |
| Explainability | **Weak** — a score, not a reason |
| Determinism | **Weak** — model versions, quantization, and library updates silently change rankings |
| Freshness | Costs a re-embed on every revision; derived index churn |
| Cold start | Works immediately with no graph at all |
| Lifecycle/context awareness | None unless context is embedded; context drifts |
| Ambiguity handling | Top-k list, no explanation |
| Local-first | Requires a model; a local embedding model is feasible but is a real dependency and a rebuild cost |
| Storage | Vectors for every revision (or every thought) |
| Structural recall | **None** — it cannot walk relationships |

### 5.4 Why hybrid, stated as one sentence each

- **Lexical alone** fails when the user's words differ from their past words — the
  exact scenario in the brief.
- **Semantic alone** fails when the user *does* remember a distinctive word
  (a character name, a version number, a coined term) — because embeddings
  deliberately blur the distinctive.
- **Graph alone** cannot start: it needs seeds, and seeds come from matching.
- **The three together** cover each other's blind spots, and — critically for
  IWTT — only the lexical and graph stages can *explain* their results, which is
  what makes a returned memory trustworthy.

**Recommended posture:** stages 0–4 and 6 are the product. Stage 5 is an optional,
strictly additive, always-rebuildable augmenter — exactly the role Graphify leaves
open in its own design (an external ranker may supply start nodes; there is no
vector index in the product).

## 6. How this should be evaluated (methodology borrowed from Graphify's benchmarks)

Graphify's `BENCHMARKS.md` describes a methodology worth copying even though the
harness is unpublished:

1. **A gold set of atomic key facts** per question, with
   `coverage = (covered + 0.5·partial) / total`.
2. **A judge validated against a second independent judge** (they report 90.6%
   agreement, Cohen's κ 0.81) — most retrieval evaluations disclose no judge
   validation at all.
3. **Ablations**: seed-only vs full pipeline; measure what each stage actually buys.
4. **A cost ledger** per run (in IWTT's case: tokens, latency, and derived-data
   size rather than dollars, unless a cloud model is used).

For IWTT, the practical adaptation:

- Build a personal recall set of ~100 remembered-situation queries with known
  target thoughts (the user can author these in an afternoon; they are the ground
  truth that matters).
- Report **recall@k of the original thought** as the primary metric — for a
  personal system, "was the right thought in the top 5" beats any QA-style score.
- Run the **ablation that matters**: stage 1 alone vs 1+2 vs 1+2+4 vs 1+2+4+5.
- Track a **"why did it fail?"** taxonomy per miss (vocabulary gap, missing link,
  stale derived metadata, hub suppression too aggressive, missing match in
  derived prose) — this is what tells you which stage to invest in.

## 7. What Graphify gives this design, and what it does not

**Given (with evidence):** the tokenizer; IDF weighting; tiered lexical scoring;
the trigram prefilter and its never-worse guard; the per-term seed guarantee; seed
dedupe; hub-suppressed BFS/DFS; induced-edge completion; hop-distance-ordered,
budgeted, sanitized output; ambiguity refusal; the design precedent that a semantic
ranker supplies *seeds* rather than replacing the index; corroboration/decay
scoring for outcome signals; and evaluation methodology.

**Not given (IWTT must design):** morphology and spelling tolerance at query time;
memory-filler stopwords; revision-aware indexing; lifecycle-aware ranking that
annotates rather than filters; the confirmed/suggested ranking split; semantic
retrieval as an optional local-first stage; and any temporal query understanding.
These are the parts of recall that are specific to remembering a life rather than
searching a codebase.

## 8. Evidence anchors

`serve.py` (`_search_tokens`, `_query_terms`, `_QUERY_STOPWORDS`,
`_compute_idf`, `_score_query`, `_get_trigram_index`, `_trigram_candidates`,
`_pick_seeds`, `_bfs`, `_dfs`, `_complete_induced_edges`, `_subgraph_to_text`,
`_cut_lines_to_budget`, `_tier_refusal`, `_ambiguity_message`,
`_path_search_graph`, external-ranker comment) ·
`cluster.py::ambiguous_scale` (confidence-weighted structure) ·
`reflect.py` (corroboration, half-life decay, provenance trail) ·
`dedup.py` (`_COMMUNITY_BOOST`, similarity machinery for *surfacing* duplicates) ·
`llm.py::_bind_node_evidence` (verify AI claims; flag, never drop) ·
`BENCHMARKS.md` (LOCOMO/LongMemEval tables, seed-only ablation, judge validation
methodology) · `README.md` line 38 ("Not a vector index").
