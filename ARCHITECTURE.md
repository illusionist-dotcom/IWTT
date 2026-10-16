# IWTT v0.2 Architecture

## Overview

IWTT is a small local web application backed by SQLite. Python's standard library provides the HTTP server, SQLite binding, JSON handling, UUIDs, ZIP/backup primitives, and fuzzy string comparison. The browser UI is a same-origin HTML/CSS/JavaScript single-page experience. There is no remote database or required third-party runtime — **v0.2 adds no dependency**.

v0.1 established the durable canonical foundation. v0.2 adds a **derived recall and graph-intelligence layer** beside it: `iwtt/recall/` (8,416 lines across 16 modules) plus nine `recall_*` tables. The canonical schema is unchanged, `schema_info.schema_version` is still `1`, and every v0.1 route, test and behaviour is intact.

```text
Browser (web/)
    │ same-origin JSON / download requests
    ▼
iwtt/server.py  ── route validation, HTTP responses, static assets
    │
    ├── iwtt/ai.py   ── NoAI by default; explicit optional provider boundary
    ▼
iwtt/store.py  ── canonical operations + SQLite transactions
    ├── canonical: thoughts, revisions, tags, thought_tags, relationships, events
    ├── derived (v0.1): search_documents, derived_artifacts
    └── derived (v0.2): iwtt/recall/ ── engine, index, scoring, expansion,
                        paths, graphview, clusters, querymemory, diagnostics,
                        filters, analysis, text, results, config, external
                        └── recall_* tables (9)
    ▼
~/.iwtt/thought-vault.sqlite3 (overridable with IWTT_DATA_DIR)
```

**The dependency direction is one-way and enforced.** `iwtt/recall/*` may import
`iwtt.store` at module level; `iwtt/store.py` imports `iwtt.recall` **lazily, inside
methods only**. So the canonical core has no load-time dependency on the derived layer,
and deleting `iwtt/recall/` entirely would leave a working v0.1.
`tests/test_layering.py::ModuleBoundaryTestCase` proves this with a
filesystem-discovered module list, absolute-import resolution for relative imports,
`sys.stdlib_module_names`, and a forbidden-package list — and proves the classifier
still rejects a real third-party import.

## Storage choice

SQLite is used because it is durable, transactional, portable, easy to back up as a real file, and built into Python. The database uses foreign keys, WAL journaling, constraints, and UUID primary keys. Each operation opens its own short-lived connection; writes use `BEGIN IMMEDIATE` transactions. There is no ORM or package installation requirement.

The default server listens on loopback (`127.0.0.1:8000`). The active process must be running to use the browser interface. Data remains in the SQLite file after a restart.

## Canonical truth vs. derived intelligence

**Canonical truth** is authored or explicitly confirmed information: first-captured text, current user-edited text/title, revisions, lifecycle/status timestamps, tags, relationships and their confirmation state, and events. The database has a trigger that prevents updates to `thoughts.original_text`.

**Derived/rebuildable data** is an index or interpretation: the `search_documents` table and optional `derived_artifacts` such as an AI summary. Deleting derived rows does not delete or rewrite canonical rows. Search rebuilds its local index on demand; there is also an explicit rebuild command. Derived artifacts record provider/model, time, type, and source thought IDs.

**v0.2 adds nine more derived tables and nothing canonical.** Six are per-thought and carry `REFERENCES thoughts(id) ON DELETE CASCADE` (`recall_tokens`, `recall_trigrams`, `recall_docs`, `recall_graph_cache`, `recall_clusters`, `recall_query_memory`). Three are corpus-wide and deliberately have **no** `thought_id` foreign key (`recall_term_stats`, `recall_meta`, `recall_build_log`), because a document frequency belongs to the corpus rather than to one thought — attaching a thought id to it would be a category error. Their contract is the same either way: derived, rebuildable, wiped by `clear_derived_data()`, containing no canonical content, recomputed on refresh. `recall_docs` stores counts and timestamps only — never verbatim thought text.

Because a cascade can remove per-thought rows *before* the incremental sync sees the deletion, `ensure_fresh()` also **reconciles the global aggregates** whenever the canonical signature moved: without that, a deleted thought would leave orphaned or inflated document frequencies behind forever and quietly skew IDF. Regression test: `tests/test_layering.py::test_deleting_a_thought_cannot_leave_stale_corpus_statistics`.

The user interface labels the immutable capture **“What I actually wrote”** and AI output **“Derived · rebuildable.”** Titles are optional user-authored canonical data. If a title is blank, the UI computes a display label from the current text without storing it.

## Recall and graph are views

The local search index is a disposable text projection of original/current text, user title, and tags. Search ranks exact phrases, substrings, tokens, title/tag matches, simple word forms, and a small transparent alias map (`adapt` ↔ change/adjust/learn/evolve; enemy ↔ enemies/foe/opponent; fight ↔ combat/battle; remember ↔ memory/recall; strategy ↔ tactic/behavior). These aliases improve practical recall but are not a semantic model. There is no embedding or remote index.

The Connections screen computes a small SVG layout in the browser from saved relationship records. Node coordinates are ephemeral, are not written to SQLite, and carry no semantic meaning. The future Mind World can replace the layout without changing canonical storage.

**v0.1 search is untouched and frozen.** `Vault.search()`, `_fuzzy_token_score`, `_snippet`, `_ensure_search_index` and `_token_variants` behave exactly as they did, and `tests/test_store.py` still pins them. Recall V2 is a *separate* path that adds word forms only through its own `recall_variants()`, so the two can never disagree about what a word form is.

**Recall V2 never scans the corpus.** Candidate generation is two bounded SQL aggregations (caps 600 lexical + 400 trigram) written as `CROSS JOIN` so SQLite probes the indexes instead of scanning them; the expensive `difflib` pass runs only on the top 120 candidates and only against tokens within ±2 characters. Ranking is a five-key total order (`score → trigram → fuzzy → created_at → thought_id`) published per result, so database row order never decides anything. Graph expansion runs *after* retrieval and can only enrich it: a candidate with no lexical evidence cannot be returned as a strong match, and the ones suppressed this way are counted and named in the outcome's `honesty` block. See [RECALL_ENGINE.md](RECALL_ENGINE.md) and [GRAPH_INTELLIGENCE.md](GRAPH_INTELLIGENCE.md).

**Clusters are derived opinions, labelled as such.** Every row carries `authority = AUTOMATICALLY_DISCOVERED_CLUSTER`, ids are content fingerprints with a stable-identity remap, member titles are read live from canonical `thoughts`, and `GraphViewModel` is a bounded induced subgraph with `layout: {persisted: false, coordinatesIncluded: false}` and an explicit `truncated` flag. Nothing holds the whole graph in memory. Measured bounds are in [BENCHMARKS.md](BENCHMARKS.md).

## Import, export, and backup

The canonical JSON contract is `iwtt-vault-export`, **schema version 1 — unchanged in v0.2**, because v0.2 adds derived intelligence rather than canonical meaning. A v0.1 export imports into v0.2 unchanged and a v0.2 export imports into v0.1, losing only derived data v0.1 never understood. Import validates references, enums, snapshots, IDs, tag uniqueness, and event targets before inserting. Duplicate IDs or conflicting tag names stop the whole transaction; it does not overwrite existing records. Import rebuilds the disposable search index.

Markdown is a readable snapshot, not the reconstruction format. SQLite backup uses the SQLite backup API to create a consistent database copy in the `backups/` directory, then sends that actual file to the browser.

## AI boundary

`iwtt/ai.py` exposes an AI provider interface and `NoAIProvider`. No endpoint is used unless `IWTT_AI_BASE_URL` is explicitly configured. A user action is still required before a thought is sent. The optional implementation supports an OpenAI-compatible chat-completions endpoint; it can point at a local service. See [AI_BOUNDARIES.md](AI_BOUNDARIES.md).

**Recall makes no AI call and no network request, ever.** `embeddings_enabled`, `semantic_stage_enabled`, `ai_required` and `network_required` are all `False`, and every outcome carries an `honesty` block saying so in plain language. `scripts/verify_release_v02.py` asserts the default provider is `NoAIProvider` and that `iwtt/` imports nothing outside the standard library.

## Deliberate non-goals

No spatial-world schema, vector database, account/sync layer, cloud dependency, analytics, telemetry, encryption/key-management system, agent, or autonomous relation confirmation is introduced. v0.1 is the durable brain underneath future presentation layers—not the full Mind World.

**v0.2 keeps every one of those non-goals, and adds its own:** no embeddings or semantic stage, no mandatory embedding infrastructure, no repository/codebase AST ingestion, no PDF or large-scale external ingestion, no persisted graph layout, no whole-file JSON graph store, no in-memory canonical graph, no NetworkX/Leiden/Louvain dependency, no canonical clusters or canonical query-learning, no unrestricted agent access to the vault, no full Mind World, no infinite 2D/2.5D renderer, no avatars, multiplayer, social features, mobile client or voice assistant. External knowledge exists in v0.2 as reserved vocabulary, guards and documentation only — see [EXTERNAL_KNOWLEDGE_ARCHITECTURE.md](EXTERNAL_KNOWLEDGE_ARCHITECTURE.md).
