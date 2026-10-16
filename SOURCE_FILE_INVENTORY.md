# Source file inventory

Every file in the repository and what it is for. Documentation lives at the
**repository root** — there is no `docs/` directory.

Line counts are for the v0.2 tree (8,416 lines in `iwtt/recall/` alone).

## Root metadata

- `.gitignore` — excludes runtime databases, caches, backups, and secrets from source snapshots.
- `VERSION` — application version (`0.2.0`), canonical export schema version (`1`, **unchanged from v0.1**), canonical `schema_info.schema_version` (`1`), recall derived index schema version (`3`), and the Checkpoint 001 baseline commit hash.
- `README.md` — local setup, v0.2 feature guide, honest limits, documentation index, and test commands.
- `Makefile` — `run`, `test`, and `verify` shortcuts.
- `requirements.txt` — documents the zero-third-party-dependency runtime. True in v0.2: recall adds no package.

## Documentation — v0.1 baseline

- `ARCHITECTURE.md` — modules, SQLite storage, canonical/derived boundary (updated for v0.2).
- `DATA_MODEL.md` — canonical and derived schema (updated with the nine `recall_*` tables).
- `EVENT_MODEL.md` — provenance and event/history contract. **Unchanged:** v0.2 writes no canonical events.
- `IMPORT_EXPORT.md` — JSON, Markdown, import validation, and backup instructions (updated: export schema version is still `1`).
- `AI_BOUNDARIES.md` — opt-in provider, provenance, and no-AI behavior (updated: recall never uses AI).
- `FUTURE_MIND_WORLD.md` — presentation-independent future compatibility.
- `PRIVACY.md` — data location, local networking, AI, and security limitations (updated for derived recall data).
- `TESTING.md` — automated and manual verification procedures (updated for the v0.2 suite).

## Documentation — v0.2

- `RECALL_ENGINE.md` — the recall pipeline: twelve signals and their weights, per-term tiers, morphology, bounded candidate generation, the never-worse guard, the ubiquitous-trigram cap, ranking and the five-key tie-break, outcome states, ambiguity refusal, honest absence, time/lifecycle recall and window precision, the 29 explanation codes, the `honesty` block, derived storage and self-healing, the HTTP surface, measured performance, and what is not implemented.
- `GRAPH_INTELLIGENCE.md` — edge authority from the provenance × status pair, per-type edge semantics and direction, seed selection, bounded expansion, the "enrich never replace" gate, hub suppression, paths/lineage/neighbourhoods, the `GraphViewModel` boundary, derived clustering and its stable identity, and what is deliberately absent.
- `QUERY_MEMORY.md` — derived query-outcome learning: the four feedback types and weights, the decay formula and its constants, corroboration gating, clamping (negative demotes, never hides), explanation codes, Python and HTTP API, UI presentation, and the boundary guarantees.
- `EXTERNAL_KNOWLEDGE_ARCHITECTURE.md` — foundation only: two entity kinds, ten reserved external types, a separate external provenance vocabulary, `ExternalEntityRef`, three boundary guards, the seven-segment cross-domain chain with a named blocker per deferred edge, the capability manifest, and the constraints a future implementation inherits.
- `V0.1_TO_V0.2_MIGRATION.md` — what migration does, the long list of what it does not change, backup instructions, two rollback procedures, verification evidence, and the destructive guarantee.
- `GRAPHIFY_ADAPTATION_NOTES.md` — provenance accounting: the license posture, all fifteen authorized concepts with their status and IWTT location, constants re-derived rather than copied, IWTT-original work, everything explicitly rejected and why, Graphify's own scale numbers marked as vendor claims, and inherited future work.
- `BENCHMARKS.md` — measured performance at 1,000 and 10,000 thoughts: environment, corpus, results, the sublinear-scaling proof, budgets, the four measured fixes and their before/after numbers, and what is honestly *not* benchmarked.
- `BASELINE_AUDIT.md` — audit of the authoritative v0.1 baseline (Checkpoint 001, commit `4124dc2`): verification results, the real v0.1 API and schema, reconciliation with earlier reconstructed work, and the dossier recommendations that were rejected.

## Python package — canonical core

- `iwtt/__init__.py` — package version.
- `iwtt/store.py` — SQLite schema and constraints, canonical operations, v0.1 local search (frozen), events, import/export, backup, demo fixture, `canonical_fingerprint()`, `clear_derived_data()`, `schema_versions()` / `schema_info_rows()`, and the v0.2 delegate surface. **Every v0.2 import is lazy, inside a method**, so `iwtt.recall` is never imported at module load and the canonical core has no reverse dependency on the derived layer.
- `iwtt/server.py` — standard-library local HTTP API and static asset server. All v0.1 routes unchanged; sixteen v0.2 routes added.
- `iwtt/ai.py` — `NoAIProvider` and the explicit OpenAI-compatible adapter. **Not used by recall.**

## Python package — `iwtt/recall/` (v0.2, all derived)

- `__init__.py` (163) — the package's public surface and its exported vocabulary.
- `config.py` (332) — `RecallConfig` with every weight, cap, threshold and bound; `RELATIONSHIP_SEMANTICS`; `theoretical_max_score()`; `to_public_dict()`.
- `text.py` (442) — normalization, tokenization, bounded morphology closure, character trigrams, memory-filler stripping, time-expression parsing with window precision, bounded fuzzy comparison. Memoized pure functions.
- `analysis.py` (215) — query → terms, variants, trigrams, time hints, lifecycle hints.
- `index.py` (1,301) — the nine derived tables and their DDL, build/sync/reconcile, `canonical_signature()`, `graph_signature()`, drift detection, IDF, df maps, lexical and trigram candidate generation, degrees and hub factors, clearing.
- `scoring.py` (1,087) — `Candidate`, per-term tiers, the twelve signals, the fuzzy pass, state assignment, explanations, total ordering.
- `filters.py` (727) — type/status/tag/time/demo filters, the SQL prefilter, and rejection reporting (an exclusion is always visible).
- `expansion.py` (515) — seed selection, bounded neighbourhood expansion, provenance and relationship weights, hub penalty, relationship context.
- `paths.py` (376) — bounded BFS path finding, `PathResult`, lineage.
- `graphview.py` (289) — the bounded, derived `GraphViewModel` with `entityType`, canonical/derived field declarations, and truncation bounds.
- `clusters.py` (397) — connected components, synchronous label propagation, cohesion, stable cluster identity with greedy majority remapping, cluster labels.
- `querymemory.py` (385) — derived query-outcome events, decay, corroboration, clamping, stats, history, reset.
- `diagnostics.py` (432) — the full per-term scoring breakdown behind the secondary details view.
- `results.py` (342) — `RecallResult`, `SignalScores`, `RelationshipContext`, `Explanation`, `MatchState`, `OutcomeState`, `EXPLANATION_LABELS`, camelCase public dictionaries.
- `engine.py` (1,044) — `RecallEngine` and `RecallOutcome`: the pipeline, the lexical-evidence gate, `returnable()`, ambiguity, honesty, timings, FIND MY THOUGHT.
- `external.py` (369) — entity kinds, reserved vocabulary, `ExternalEntityRef`, boundary guards, cross-domain design, capability manifest, `foundation_payload()`.

## Web interface

- `web/index.html` — app shell and navigation, including the v0.2 FIND MY THOUGHT, Connection Explorer and diagnostics views.
- `web/style.css` — responsive local styling with no remote assets; keeps original text visually distinct from derived explanation.
- `web/app.js` — capture, browsing, search, detail/history, Graveyard, graph, and data workflows, plus v0.2 recall rendering, explanation chips, breakdown panel, clustering, path finding, neighbourhoods and feedback.

## Tests (131 tests)

- `tests/test_store.py` — **15 v0.1 tests, frozen and unchanged.** The canonical contract: search behaviour, immutability, revisions, lifecycle, provenance, export/import, backup integrity, derived deletion.
- `tests/test_recall_engine.py` — 24 tests: signals, tiers, morphology, coverage, IDF, trigrams, fuzzy, time windows and their precision, lifecycle, states, ambiguity, honest absence, explanations, the lexical-evidence gate.
- `tests/test_graph_intelligence.py` — 22 tests: edge authority, provenance preservation, direction, hub suppression, expansion bounds, paths, lineage, neighbourhoods, `GraphViewModel`, clusters and their stable identity.
- `tests/test_query_memory.py` — 16 tests: weights, decay, corroboration, clamping, reset, inspection, and the guarantee that learning never touches canonical data or confirms a suggestion.
- `tests/test_determinism.py` — 15 tests: identical payloads across repeats, rebuilds, restarts and derived deletion; insertion-order independence; published tie-breaks; stable clusters; reproducible feedback effects.
- `tests/test_layering.py` — 29 tests: module boundaries and the dependency classifier, per-thought vs global derived-table contracts, canonical deletion cannot contaminate derived aggregates, the production deletion path (`remove_demo`), capability-manifest honesty, cross-domain documentation, camelCase public payloads, local-first and no-AI guarantees.
- `tests/test_canonical_destruction.py` — 3 tests: the mandatory destructive verification — create canonical memory, build every derived artefact, exercise recall and the graph, delete all of it, prove canonical survival byte-for-byte, rebuild, and show recall working again.
- `tests/test_migration_v01_to_v02.py` — 7 tests: a byte-faithful v0.1-format file opens unchanged, canonical rows are identical, ids and original text are preserved, v0.1 search still behaves identically, recall works on migrated data, rollback leaves a file v0.1 can open, migration is idempotent, and a v0.1-shaped export still imports. This module also owns `v01_vault()` and `raw_snapshot()`, the single definition of "a v0.1-format file" in the repository, which `scripts/verify_release_v02.py` imports rather than duplicating.

## Scripts

- `scripts/verify_release.py` — **v0.1's** end-to-end release workflow in a temporary vault. Unchanged and still passing.
- `scripts/verify_release_v02.py` — the v0.2 release gate: 21 checks covering derived deletion and canonical survival, rebuild with identical ranking, determinism across repeats and restart, ambiguity refusal, honest absence, provenance preservation, path honesty, query-memory boundaries, the `GraphViewModel` boundary, export/import at schema version 1, migration from a genuine v0.1 file, offline/no-AI/stdlib-only, the external-foundation manifest, and the live HTTP surface including a camelCase audit of every payload. Prints `PASS`/`FAIL` and exits non-zero on failure.
- `scripts/benchmark_recall.py` — bounded performance check at any size, with a `--suite` mode that runs 1,000 and 10,000 thoughts and asserts sublinear scaling. Declares budgets and fails when one is exceeded.
- `scripts/build_sample_vault.py` — builds a demonstration vault (28 thoughts, 19 relationships, one Graveyard item, tags, revisions, events) for manual inspection, with the queries that exercise each recall behaviour.

## Research

- `research/graphify-acquisition-audit/` — the Graphify dossier v0.2 was designed from: `README.md`, `MANIFEST.txt`, `PROVENANCE.md`, `LICENSE_AUDIT.md`, `SOURCE_EVIDENCE.md`, `GRAPHIFY_ARCHITECTURE.md`, `GRAPH_MODEL.md`, `SEARCH_AND_QUERY.md`, `IWTT_RECALL_PIPELINE.md`, `IWTT_ACQUISITION_MATRIX.md`, `IWTT_GRAPHIFY_FUTURE_ARCHITECTURE.md`, `CLUSTERING.md`, `SCALING.md`, `AGENT_INTEGRATION.md`, `LOCAL_FIRST_PRIVACY.md`, `REJECTED_COMPONENTS.md`, plus `research_instrumentation/`. **Retained unchanged.** It is research input, not a dependency; nothing in `iwtt/` imports from it, and no code was copied from the project it documents (see `GRAPHIFY_ADAPTATION_NOTES.md`).

## Not in the repository

- No `.scratch/`, no caches, no databases, no backups, no sample vaults, no private data, no API keys. Development scratch files were audited and moved out of the tree before the v0.2 completion commit.
- No third-party packages, no `node_modules`, no lockfiles, no build output.
