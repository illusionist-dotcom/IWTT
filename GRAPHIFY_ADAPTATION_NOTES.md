# GRAPHIFY ADAPTATION NOTES (IWTT v0.2)

Provenance accounting for the Graphify research dossier at
`research/graphify-acquisition-audit/` (16 documents plus
`research_instrumentation/`, read in full before any v0.2 code was written).

This document exists so that nobody — a reviewer, a future maintainer, a license
auditor — has to guess what came from where. It records what was **adapted**, what was
**independently implemented**, what was **rejected**, and **why**.

---

## 1. License posture

**Posture B from `LICENSE_AUDIT.md`: study the techniques, reimplement them
independently, copy no code.**

That is what happened. Concretely:

* **No Graphify source file, function, class or module was copied into this
  repository.** Verify with:

  ```
  grep -rniE "graspologic|leiden|louvain|networkx|numpy|scipy|matplotlib|boto3|openai" \
       --include=*.py iwtt scripts tests
  ```

  The only matches are *prose* in `iwtt/recall/clusters.py` explaining why Leiden was
  not used. There is no import of any third-party package anywhere in `iwtt/`,
  `scripts/` or `tests/` — asserted at runtime by
  `tests/test_layering.py::ModuleBoundaryTestCase` (a filesystem-discovered module
  list, `sys.stdlib_module_names`, and a forbidden-package list) and by
  `scripts/verify_release_v02.py` (`offlineNoAI.thirdPartyImports == []`).
* `requirements.txt` still says *"IWTT v0.1 uses only the Python standard library. No
  pip install required."* That sentence remains true of v0.2.
* **Consequence for Apache-2.0 obligations:** because no licensed code was
  incorporated, no NOTICE file, attribution header, or license text is required in
  this repository. If that ever changes — if a single function is copied rather than
  reimplemented — the obligation is immediate: identify the exact file, component and
  license, add the NOTICE, and stop for a review. Nothing in v0.2 comes close.
* Graphify is referenced by name in 22 places in `iwtt/`, all of them comments and
  docstrings that say which technique was adapted and where the dossier discusses it.
  Hiding the provenance would be worse than the provenance itself.
* The dossier is **retained unchanged** in `research/graphify-acquisition-audit/`.
  It is research input, not a dependency, and it is not deleted, edited or vendored
  into the Python package.

`SOURCE_EVIDENCE.md` and `PROVENANCE.md` also record that Graphify's *marketed*
capabilities are stronger than its verified ones (a 3-valued `confidence` field,
`source_file` / `source_location`, and explanations that are generated prose). IWTT
therefore did not inherit claims, only mechanisms it could verify and re-derive.

---

## 2. The fifteen authorized concepts

Every one of the fifteen concepts the mission authorized was **studied in the dossier
and independently implemented**. "Adapted" below always means: the mechanism was
understood from the dossier, then written from scratch against IWTT's canonical
SQLite model, with constants re-derived for a personal thought vault.

| # | Concept | Dossier source | Status | IWTT implementation | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | Deterministic lexical retrieval | `IWTT_RECALL_PIPELINE.md`, `SEARCH_AND_QUERY.md` | **Adapted** | `iwtt/recall/scoring.py` (`term_tier`, `score_candidate`), `index.py` (`lexical_candidates`) | Tiered matching, strongest tier per term only. Tier values re-derived (§3.1) |
| 2 | IDF weighting | `SEARCH_AND_QUERY.md`, matrix #3 | **Adapted, independently implemented** | `RecallIndex.idf()` | Same functional form `log(1 + N/(1+df))`, written from scratch. The `1+` guards keep an unseen term finite and a ubiquitous term non-zero (`log 2 ≈ 0.69`) |
| 3 | Coverage bonuses | `SEARCH_AND_QUERY.md` | **Adapted** | `scoring.coverage_signal()` | `(matched/terms) ** 1.5`. Graphify's squared coverage was re-derived to exponent 1.5 so a 2-of-3 match is not punished as harshly in short personal queries |
| 4 | Trigram candidate filtering | `SEARCH_AND_QUERY.md` | **Adapted** | `index.trigram_candidates()`, `text.word_trigrams()` | Padded 3-char grams, cosine against a stored document norm, plus IWTT's own **never-worse guard** and **ubiquitous-trigram cap** (§4) |
| 5 | Deterministic seed selection | `IWTT_RECALL_PIPELINE.md` | **Adapted** | `expansion.pick_seeds()` | ≤6 seeds, min seed score, gap ratio 0.20, dedupe by normalized label, tie-break `(-score, -degree, label_len, id)` |
| 6 | Bounded graph-neighbourhood expansion | `GRAPH_MODEL.md`, `SCALING.md` | **Adapted** | `expansion.expand_neighbourhood()` | depth ≤2, ≤40 nodes, contribution `seed × 0.45 × edge_weight × 0.5**depth`, capped at 2.0 |
| 7 | BFS / DFS traversal | `GRAPH_MODEL.md` | **Adapted** | `paths.find_path()` (BFS), `expansion` (bounded hybrid), `clusters.connected_components()` | `path_max_depth = 6`, `path_max_visited = 5000` |
| 8 | Hub suppression | `CLUSTERING.md`, `GRAPH_MODEL.md` | **Mechanism adopted, constants re-derived** | `index.hub_threshold()`, expansion penalty + transit rule | Graphify's `max(50, p99)` floor was **rejected**; IWTT uses `max(5, ceil(p90))` with `hub_floor = 0.15`, because 50 relationships is already extraordinary in a personal vault. Suppression is a penalty and a no-transit rule, **never a filter** |
| 9 | Direction-aware traversal | `GRAPH_MODEL.md` | **Adapted** | `graph.RELATIONSHIP_SEMANTICS`, `REVERSE_TRAVERSAL_FACTOR = 0.85` | Per-type symmetry and phrasing for all 11 v0.1 relationship types; reversing an asymmetric edge costs 15% |
| 10 | Deterministic tie-breaking | `IWTT_RECALL_PIPELINE.md` | **Adapted and strengthened** | `results.py` total order, `config.tie_break` | Five-key order `score → trigram → fuzzy → created_at → thought_id`, published per result as `tieBreak`, with `orderingIsTieBreakNotJudgement: true`. v0.1's `search()` sorted on `(score, updatedAt)` and was non-deterministic on ties; recall fixes that without touching the frozen v0.1 path |
| 11 | Derived query-outcome learning | `SEARCH_AND_QUERY.md`, `AGENT_INTEGRATION.md` | **Adapted, storage model rejected** | `querymemory.py`, table `recall_query_memory` | The dossier's ADAPT #17 recommended putting outcome signals in the **canonical event log**. **Rejected** — see §5. Weights, half-life and corroboration re-derived (§3.2) |
| 12 | Time decay | `AGENT_INTEGRATION.md` | **Mechanism adopted, constant re-derived** | `querymemory.usefulness()` | `0.5 ** (age_days / 180)`. Graphify's 30-day half-life suits code facts; the dossier's own ADAPT #14 notes personal thoughts decay far more slowly and recommends ~180 days. Configurable, and labelled as an estimate wherever surfaced |
| 13 | Ambiguity refusal | `SEARCH_AND_QUERY.md` | **Adapted and strengthened** | `scoring.assign_states()`, `results.OutcomeState.AMBIGUOUS` | ≥3 within 0.90× top **or** ≥2 exactly tied tops. IWTT additionally publishes per-candidate `differences` (type, lifecycle, date, distinctive tags) so the refusal is *useful*, not merely honest |
| 14 | Derived graph / community organization | `CLUSTERING.md` | **Mechanism adapted, algorithm replaced** | `clusters.py` | Leiden/Louvain **rejected** (§3.3): connected components + synchronous label propagation (6 iterations, min component 24), standard library only. Stable identity via sha256 fingerprints + greedy majority-overlap remap, per the dossier |
| 15 | Explicit result explanations | `SEARCH_AND_QUERY.md`, `PROVENANCE.md` | **Adapted and strengthened** | `results.EXPLANATION_LABELS` (29 codes), `diagnostics.py` | The dossier records that Graphify's explanations are *generated prose*. IWTT's are structured `{code, label, detail, contribution, evidence}` tuples with fixed labels and the arithmetic that produced them — reproducible, testable, and never a sentence invented to sound convincing |

---

## 3. Constants re-derived rather than copied

The dossier's numbers were tuned for a code-intelligence product over repositories.
IWTT is a personal memory vault. Every constant below was re-derived, and the reason
is recorded next to it in the code.

### 3.1 Tier values

Graphify's retrieval skeleton uses tier weights on the order of **1000 / 100 / 1**
(exact ≫ prefix ≫ substring). IWTT compresses these to **1.00 / 0.90 / 0.86 / 0.70 /
0.55×similarity / min(ratio×0.84, 0.82)** (`scoring.TIER_VALUES`).

Why: a 1000:1 ratio makes the exact tier an absolute veto, which is right when the
corpus is a million symbols and wrong when it is a few thousand sentences a person
wrote, where a morphology match ("adaptation" for "adapt") is very often *the* answer.
The compressed ladder keeps exact-on-top while letting variant, prefix and substring
matches stay competitive — and it composes with the other eleven signals instead of
drowning them.

The tier values are **not configurable**. An earlier draft of `RecallConfig` carried
five `tier_*` fields that no code read and whose numbers contradicted `TIER_VALUES`;
they were deleted rather than documented, because a published config that disagrees
with the code is a lie with a type signature.

### 3.2 Query memory

| | Graphify | IWTT v0.2 | Why |
| --- | --- | --- | --- |
| Half-life | 30 days | **180 days** | Personal memories are not code churn |
| Corroboration | `_DEFAULT_MIN_CORROBORATION = 2` | **2** | Adopted: "a single save cannot mint a trusted lesson" |
| Single-observation factor | 0.5 | **0.5** | Adopted |
| Signal weights | `useful` / `dead_end` / `corrected` | `USEFUL +1.0`, `NOT_USEFUL −1.0`, `CORRECTION +0.6`, `OPENED +0.15` | Re-derived and extended: `OPENED` is an implicit, non-corroborating nudge |
| Clamping | — | `[−0.75, +1.0]` | IWTT-original: **negative feedback demotes, never hides** |
| Storage | derived sidecar `.graphify_learning.json` | derived table `recall_query_memory` | IWTT's canonical backbone is SQLite; a JSON sidecar would be a second source of truth |

### 3.3 Clustering

Graphify reaches Leiden through `graspologic_native` →
`graspologic.partition.leiden` → `nx.community.louvain_communities`, i.e. through a
native extension or NetworkX, with seed 42, `ambiguous_scale = 0.5`, hub exclusion
plus majority reattachment, re-splits for oversized (>25%) or low-cohesion (<0.05)
communities, and stable identity via sha256 fingerprints with greedy
majority-overlap remapping.

IWTT **keeps the identity mechanism and replaces the algorithm**: connected components
first, then synchronous label propagation (6 iterations) inside components of ≥24
members, cohesion `internal/(n(n−1)/2)`, cluster ids `c-` + sha256(sorted
members)[:12], and the same greedy majority remap requiring a strict majority of
**both** the old and new member sets.

Why: adding `graspologic`, `leiden` or `networkx` to a standard-library-only,
local-first, offline product to cluster a few thousand personal thoughts is a
massive dependency for a marginal gain, and it would make IWTT uninstallable-by-copy
— the whole point of a single-file vault. The dossier's own `REJECTED_COMPONENTS.md`
anticipates this trade.

Honest limitation, documented in the code: synchronous label propagation **oscillates**
on 2-cycles and 3-paths. Partitioning by component first is what makes the output
stable, and the iteration count is a documented bound, not a convergence claim.

**Rejected from the dossier's clustering design:** hub exclusion as a *removal* (IWTT
penalises and re-attaches by majority instead), Leiden's `ambiguous_scale`, and the
oversized/low-cohesion re-split heuristics (IWTT reports `cohesion` and lets the user
see it rather than silently re-partitioning).

---

## 4. IWTT-original work (not from the dossier)

Substantial parts of v0.2 have no Graphify analogue, because Graphify is a
code-intelligence tool and IWTT is a memory vault:

* **The never-worse trigram guard** with honest reporting
  (`trigramGuardTripped`) — a prefilter may only speed things up, never change
  results.
* **The ubiquitous-trigram postings cap** with a calibrated floor so small vaults are
  provably unaffected, and skipped trigrams named in the payload.
* **`CROSS JOIN` join-order control** to stop SQLite scanning the whole index per
  query (measured: 71,872 rows scanned at 1,000 thoughts before, index probes after).
* **The lexical-evidence gate** (`require_lexical_evidence`,
  `graph_may_surface_without_lexical_evidence = False`, `returnable()`): graph
  expansion enriches candidates and can never replace primary retrieval. This is the
  mission's rule, implemented as a gate rather than a score floor, because a strong
  seed can push a depth-2 suggestion above any floor.
* **`NO_LEXICAL_EVIDENCE` suppression reporting** — suppressed candidates are counted
  and named in `honesty`, never silently dropped.
* **Time-window precision** (`EXPLICIT` vs `ESTIMATED_RANGE`, with `estimated` and a
  `note`): IWTT refuses to fabricate precision it inferred.
* **Global-aggregate reconciliation** after a cascade deletion, so a deleted thought
  cannot leave orphaned or inflated document frequencies behind (`_reconcile_global_aggregates`).
* **`graph_signature`**, which lets an incremental sync skip reclustering when only
  text changed (measured: 45.4 s → 0.98 s for 50 new thoughts at 10,000).
* **Memoization of pure text/tier functions** keyed by their exact inputs, with
  bounded LRU sizes — determinism preserved because a hit and a miss return the same
  value (measured: p95 491 ms → 118 ms at 1,000 thoughts).
* **The `honesty` block** on every outcome: five plain-language statements plus
  machine-readable fields, so no caller has to guess what ran.
* **The external-knowledge boundary** (`EXTERNAL_KNOWLEDGE_ARCHITECTURE.md`): entity
  kinds, a closed 10-type vocabulary, a *separate* external provenance vocabulary,
  guards that raise, and a cross-domain design whose deferred edges each name their
  blocker.
* **`canonical_fingerprint()`** and the destructive-verification harness that makes
  "canonical survives total derived deletion" a tested property rather than a promise.
* **Query-memory clamping** so negative feedback demotes but never hides.
* **The `whatIActuallyWrote` payload split**, which keeps the original text visually
  and structurally separate from every derived explanation of it.

---

## 5. Explicitly rejected

From `REJECTED_COMPONENTS.md` and the mission's own exclusions. Each entry says why.

| Rejected | Why |
| --- | --- |
| Whole-file JSON graph storage | IWTT's canonical backbone is SQLite. A JSON graph file is a second source of truth that cannot be queried, indexed or transactionally updated |
| Requiring the entire graph in memory | Fails the bounded-performance requirement; `GraphViewModel` is an induced subgraph with a node cap and a `truncated` flag instead |
| NetworkX as canonical state | A dependency, an in-memory copy, and a canonical-truth candidate — all three at once |
| Code AST in the Thought schema | Would make `original_text` stop meaning "text a human wrote". Reserved as external vocabulary only |
| Canonical clusters / canonical query-learning | Derived intelligence must be deletable without touching memory. Clusters carry `authority = AUTOMATICALLY_DISCOVERED_CLUSTER`; learning lives in `recall_query_memory` |
| **ADAPT #17: outcome signals in the canonical event log** | The event log is the record of what the user did to their memories. A search outcome is not that, and putting it there would make derived opinion indistinguishable from canonical history |
| Mandatory embeddings | Not implemented at all, and said so in every payload (`honesty.embeddings` = "NOT IMPLEMENTED…") |
| Unrestricted agent access to the whole vault | No agent write path exists; `assert_not_thought` guards the boundary that would make one dangerous |
| Graphify's `1000/100/1` tier weights | See §3.1 |
| Graphify's `max(50, p99)` hub floor | See §2 concept 8 |
| Matrix #19: Leiden via `graspologic_native` | See §3.3 |
| Matrix #12: a canonical 3-value `confidence` enum | v0.1's `provenance × status × confirmed_by × confirmed_at` is strictly stronger: it records *who* asserted a connection and *who* consented to it, not a vague certainty level. Collapsing that into `high/medium/low` would weaken the provenance model |
| `norm_label` / `community` columns on canonical records | Derived values must not live on canonical rows |
| Environment-key cloud auto-detection | v0.1's AI provider requires an explicit `IWTT_AI_BASE_URL`; nothing is inferred from the environment |
| Auto-merging near-duplicate thoughts | Would rewrite canonical memory |
| Persisted graph layout / coordinates | `layout: {persisted: false, coordinatesIncluded: false}` |
| `graspologic`, `leiden`, `networkx`, `matplotlib`, `boto3`, `openai` | Standard library only; asserted by tests and by the release gate |
| Reproducing Graphify's `BENCHMARKS.md` numbers | Those are vendor claims measured by their harness on their corpora. `BENCHMARKS.md` in this repository contains only numbers measured here, by `scripts/benchmark_recall.py`, on this machine |

Also out of scope by direction, not deferral: full Mind World, infinite 2D/2.5D
renderer, avatars, multiplayer, social features, cloud sync, mobile app, voice
assistant, autonomous agent, repository/AST ingestion, PDF ingestion,
Chimera/Scape World integration, commercial subscriptions.

---

## 6. Graphify's measured scale, for context only

`SCALING.md` records Graphify's own harness results: 1k load 0.04 s; 10k 0.55 s
(clustering 2.7 s); 100k 4.17 s / 88 MB (clustering 37.9 s, query 1.45 s); 250k
11.8 s / 216 MB; **500k and 1M failed (out of memory)**.

These are *their* numbers, on *their* in-memory graph model, and they are the reason
IWTT does not adopt that model: a design that runs out of memory at 500k nodes is a
design that keeps everything in RAM. IWTT's own measured numbers are in
`BENCHMARKS.md`, and the architectural difference shows up in the shape of the curve —
IWTT's candidate set is bounded by an absolute cap, so the examined fraction of the
vault *falls* as the vault grows (73.6% at 1,000 → 9.6% at 10,000).

---

## 7. Future work inherited from the dossier

Deliberately **not** done in v0.2, recorded so the next version starts from a
decision rather than a blank page:

1. **External-knowledge registry** — implement the design in
   `EXTERNAL_KNOWLEDGE_ARCHITECTURE.md` §9: own tables, own id namespace, cross-kind
   edge table with `(entity_kind, entity_id)` endpoints, guards on every write path.
2. **Cross-kind edge #7 (Fix → New Thought)** — the loop-closing edge, which must be
   user-confirmed and may only be machine-proposed as `AI_SUGGESTED`/`SUGGESTED`.
3. **Optional embedding stage** — as an *additional* signal, off by default, behind
   `embeddings_enabled`, never allowed to become the primary retrieval path, and never
   allowed to make recall depend on a network or a model. The lexical-evidence gate
   already exists to make that structurally hard to get wrong.
4. **Community re-splitting** — Graphify's oversized/low-cohesion re-splits, if
   label propagation proves too coarse on large real vaults. IWTT currently reports
   `cohesion` instead of acting on it.
5. **Cluster quality evaluation** — there is no ground truth for personal memories;
   any evaluation has to be user-facing (does this grouping help you?) rather than a
   modularity score.
6. **Query-memory corroboration tuning** — 180 days and 2 observations are reasoned
   defaults, not fitted parameters. Fitting them would require real usage data that
   IWTT does not collect and must not.
