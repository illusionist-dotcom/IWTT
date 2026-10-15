# IWTT — Graphify Acquisition Audit

**A research/audit mission.** This bundle studies the public Graphify repository
and asks one question: *which architectural ideas, algorithms, data structures,
query techniques, graph techniques, search techniques, and Apache-2.0-licensed
implementation components could legitimately improve IWTT?*

It is not a fork, not a merge, not a redesign of Graphify, and not IWTT v0.2.

## What was inspected

| Field | Value |
|---|---|
| Repository | https://github.com/Graphify-Labs/graphify.git |
| Branch | `v8` |
| Commit SHA | `10130e79ed9caf8f60d998073e097531eaa1a395` (`chore(release): 0.9.91`, 2026-10-14) |
| License | Apache License 2.0 (`LICENSE`), with `LICENSE-MIT` retained for pre-relicensing contributions and a `NOTICE` file |
| Method | Local read-only clone; code reading first, README claims second; static inspection plus measurement on synthetic corpora |

Nothing in Graphify was modified. No fork, commit, branch, PR, or issue was
created. IWTT was not modified, read, migrated, or integrated with. All research
artifacts live outside the Graphify clone.

## What's in this bundle

| File | Covers |
|---|---|
| `README.md` | This document — scope, method, headline findings, how to read the rest |
| `GRAPHIFY_ARCHITECTURE.md` | Audit 1 (architecture, component diagram, real data flow) and Audit 4 (deterministic extraction) |
| `GRAPH_MODEL.md` | Audit 2 (nodes, edges, types, metadata, confidence) |
| `SEARCH_AND_QUERY.md` | Audit 3 (retrieval: what it actually is, line by line) and Audit 6 (traversal) |
| `CLUSTERING.md` | Audit 5 (Leiden/Louvain, cohesion, stability, incremental behavior) |
| `PROVENANCE.md` | Audit 7 (how edges are explained — and what is missing) |
| `SCALING.md` | Audit 8 (measured 1k → 100k, plus 250k/500k/1M findings) |
| `LOCAL_FIRST_PRIVACY.md` | Audit 9 (data locality, network boundaries, telemetry) |
| `AGENT_INTEGRATION.md` | Audit 10 (MCP surface, hosts, permissions, security) |
| `LICENSE_AUDIT.md` | Audit 11 (license obligations for four different reuse postures) |
| `REJECTED_COMPONENTS.md` | Audit 12 (the explicit rejection list) |
| `IWTT_ACQUISITION_MATRIX.md` | Every candidate classified ADOPT / ADAPT / STUDY ONLY / REJECT |
| `IWTT_GRAPHIFY_FUTURE_ARCHITECTURE.md` | External-knowledge sources beside canonical Thought (design only) + Mind World |
| `IWTT_RECALL_PIPELINE.md` | Hypothetical hybrid recall pipeline, versus embedding-only retrieval |
| `SOURCE_EVIDENCE.md` | Mandatory evidence index: path, symbol, commit SHA, what the code proves |

Supporting material outside the bundle: `research_instrumentation/` holds the
measurement harness and raw JSONL results (research-only, not part of Graphify
or IWTT).

## The five conclusions that matter

1. **Graphify's retrieval is not vector search, and not BM25 either.** It is a
   hand-weighted lexical scorer (exact ≫ prefix ≫ substring, IDF-weighted,
   coverage-scaled) over node labels, source paths, prose `rationale` attributes
   and arbitrary `attributes`, prefiltered by a lazily built character-trigram
   index, followed by BFS/DFS neighbourhood expansion rendered under a hard token
   budget. README's "no embeddings, no vector store" is literally true in the
   shipped code (`grep` for `bm25`, `fts5`, `vector_store`, `cosine`, `tfidf` →
   zero hits). *This is the single most transferable thing in the repository for
   IWTT.*
2. **The strongest IWTT-shaped subsystem is Graphify's "work memory"**
   (`reflect.py` + `querylog.py` + `ingest.save_query_result`): outcome signals
   (`useful` / `dead_end` / `corrected`) aggregated with a 30-day half-life,
   corroboration-gated so a single save cannot mint a trusted lesson, written to
   a *derived sidecar* (`.graphify_learning.json`) that never contaminates the
   canonical graph, and merged into query output at display time. That is IWTT's
   canonical/derived split, already implemented — worth studying closely.
3. **Provenance is weaker than the marketing.** Graphify advertises that every
   edge is explained; the code carries a three-value `confidence` enum
   (`EXTRACTED` / `INFERRED` / `AMBIGUOUS`), a `source_file`, a `source_location`
   (`L42`), an optional `context` tag and (on one relation) a numeric
   `confidence_score`. There is **no per-edge evidence object** — `provenance`
   appears in 4 lines package-wide, all in `reflect.py`. Explanations are
   *generated* as prose `why` strings by `analyze.py` for suggested questions,
   not stored per edge. IWTT needs more than this for USER_CONFIRMED vs
   AI_SUGGESTED relationships; the transferable pattern is the *shape*
   (closed-vocabulary confidence + location + context + numeric score), plus
   `llm.py`'s `verification="unverified"` flag: provenance demotes, it never deletes.
4. **Storage is the weak point at scale.** Whole-file JSON, whole-graph-in-memory
   NetworkX, whole-file rewrite per build. Measured here: 1,000 nodes load in
   0.04 s; 10,000 in 0.5 s; 100,000 (46 MB JSON) in 4.2 s / 88 MB peak; clustering
   100,000 nodes with real Leiden takes ~34–38 s. Extending the curve, a 250,000-node
   corpus takes ~12 s to load / ~216 MB peak. A 469 MB `graph.json` (~1M nodes)
   could not even be read-into-text + JSON-parsed inside 1.6 GB on the test
   machine (read alone peaked at 926 MB). **ESTIMATED: ~1M nodes needs roughly
   3–5 GB of RAM with this design.** For an IWTT "mind world" that is fine for a
   decade of personal use; it is a hard ceiling to know about.
5. **The Apache-2.0 situation is clean but not uniform.** Main license is verbatim
   Apache-2.0; MIT contributions are retained in `LICENSE-MIT` and acknowledged in
   `NOTICE`; no submodules, no vendored trees, no file-level headers (0/95 files).
   Dependencies are permissive except `psycopg` (LGPL-3.0-only, optional
   `postgres` extra) and `hypothesis` (MPL-2.0, dev only). The bundled-Graphify
   obligation is: keep `LICENSE` + `NOTICE`, mark modified files, retain any
   attribution surfaced in the UI. Nothing here is unsolvable, but the analysis is
   not legal advice — see `LICENSE_AUDIT.md` for the explicit "get a lawyer" flags.

## How to read the matrix

`IWTT_ACQUISITION_MATRIX.md` classifies candidates as:

- **ADOPT** — take the idea and the implementation shape essentially as-is
  (adapted to IWTT types).
- **ADAPT** — the technique is right, the Graphify implementation is coupled to
  code graphs; re-derive it for thoughts.
- **STUDY ONLY** — understand it, keep it in the design vocabulary, do not plan
  an implementation.
- **REJECT** — explicitly out of scope; do not let it leak into IWTT.

Every row states whether the result lands in IWTT's **canonical** store or a
**derived** layer. Default for algorithmically generated organisation is DERIVED.

## Verification performed

- Repository cloned and pinned to the commit above; working tree unmodified
  (`git status` clean, verified after all instrumentation).
- Claims verified by grep/read against the actual files listed in
  `SOURCE_EVIDENCE.md`; no claim rests on README prose alone.
- `graphify.serve` and `graphify.cluster` imported and exercised in a sandbox
  with only their declared dependencies, plus the optional Leiden extra.
- Ten scale measurements taken (1k, 10k, 100k node corpora end-to-end; 250k/500k
  load at minimum; 1M load attempted under a memory cap and observed to fail).
- `BENCHMARKS.md` claims recorded as **vendor claims, not reproduced** — the
  harness is not published in the repository.
- No IWTT artifact was opened, created, or modified during this mission.

**Scope statement:** this is research. No integration is authorized or performed.
