# GRAPHIFY_ARCHITECTURE.md

**Audit 1 — Architecture** and **Audit 4 — Deterministic knowledge extraction**.

Pinned commit: `10130e79ed9caf8f60d998073e097531eaa1a395` (branch `v8`, release 0.9.91).

---

## 1. What Graphify actually is

A Python package (~221k LOC including tests; 95 modules in the `graphify/`
package; 427 test files) that turns a folder of code, documents, papers, images
and video into a queryable knowledge graph, exposed through a CLI, an MCP server,
and per-editor "skill" instruction files.

Two halves matter for IWTT:

- a **deterministic ingestion + graph layer** (detect → extract → build →
  cluster → analyze → report/export), and
- an **agent-facing query layer** (`serve.py`), which is a retrieval engine in its
  own right (see `SEARCH_AND_QUERY.md`).

The repository's own `ARCHITECTURE.md` states the pipeline and enforces it with a
test (`tests/test_architecture_doc.py` imports every symbol the doc names), which
is a documentation-integrity practice worth copying independently of anything
else here.

## 2. Component diagram

```
                     ┌──────────────────────────── INPUT ────────────────────────────┐
                     │ filesystem tree · URLs (ingest) · SCIP index · live Postgres   │
                     │ Google Workspace · video/audio (transcribe)                    │
                     └───────────────────────────────┬───────────────────────────────┘
                                                     │
   ┌───────────────────────┐                         ▼
   │ detect.py (~200 code  │              ┌──────────────────────────┐
   │ suffixes + doc/paper/ │─────────────▶│  collect_files()         │
   │ image/office/video    │  scan summary│  (dispatch by suffix)    │
   └───────────────────────┘              └────────────┬─────────────┘
                                                       │
        ┌──────────────────────────────────────────────┴───────────────────────┐
        │                                                                      │
        ▼  CODE (deterministic, no LLM)                    DOCS/PDF/IMG/VIDEO ▼ (semantic, LLM)
   ┌──────────────────────────────────┐            ┌──────────────────────────────────┐
   │ extractors/engine.py (~10k LOC)  │            │ llm.py (BACKENDS: ollama,        │
   │ LanguageConfig per language      │            │ claude, kimi, openai, gemini,    │
   │ tree-sitter parse → walk →       │            │ bedrock, deepseek, azure,        │
   │ collect nodes/edges → call-graph │            │ claude-cli)                      │
   │ second pass                      │            │ + _bind_node_evidence() guard    │
   │ extractors/resolution.py         │            │ + prompt-injection defences      │
   │ (cross-file symbol resolution)   │            └───────────────┬──────────────────┘
   └───────────────┬──────────────────┘                            │
                   │                                               │
                   ▼       cache.py: per-file content-hash cache   ▼
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │ validate.py — schema check: {id,label,file_type,source_file} / {source,target,│
   │ relation,confidence,source_file}; confidence ∈ EXTRACTED|INFERRED|AMBIGUOUS   │
   └───────────────────────────────┬───────────────────────────────────────────────┘
                                   ▼
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │ build.py — NetworkX graph. dedupe (dedup.py + _minhash.py), ID normalization  │
   │ (ids.py), external stub minting, hyperedge fold, AST-vs-semantic merge,       │
   │ shrink guard                                                                    │
   └───────────────────────────────┬───────────────────────────────────────────────┘
                                   ▼
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │ cluster.py — Leiden (graspologic_native) → graspologic → Louvain fallback;     │
   │ isolate handling, hub exclusion, oversized + low-cohesion re-split, stable IDs │
   └───────────────────────────────┬───────────────────────────────────────────────┘
                                   ▼
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │ analyze.py — god_nodes · surprising_connections (why-strings) ·                │
   │ suggest_questions · find_import_cycles · graph_diff                            │
   │ report.py — GRAPH_REPORT.md      wiki.py — per-community markdown              │
   └───────────────────────────────┬───────────────────────────────────────────────┘
                                   ▼
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │ export.py / exporters / callflow_html / tree_html                              │
   │ graph.json · graph.html (vis-network) · obsidian/ · canvas · graphml · svg ·   │
   │ cypher · mermaid call-flow · file tree                                         │
   └───────────────────────────────┬───────────────────────────────────────────────┘
                                   ▼
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │ serve.py — MCP stdio + HTTP: query_graph / get_node / get_neighbors /          │
   │ get_community / god_nodes / graph_stats / shortest_path / PR tools;            │
   │ lexical scorer + trigram prefilter + BFS/DFS + budgeted text rendering         │
   └───────────────────────────────────────────────────────────────────────────────┘
```

Side layers: `cache.py` (AST + semantic caches), `watch.py` (debounced
incremental rebuild + pending-change queue), `hooks.py` (git hooks),
`install.py` (per-editor installers), `reflect.py` (work-memory/lessons),
`querylog.py` (opt-in query log), `paths.py` (output dir + atomic writes),
`security.py` (URL/path/label validation), `ids.py` (node-ID normalization).

## 3. Actual data flow

**SOURCE → INGEST.** `detect(root)` produces a scan summary
(`{files by category, total_files, total_words, scan_root, warning}`).
`extract.collect_files(target)` expands a directory into the path list that
`extract()` consumes. `detect.py` classifies by extension into code (~200
suffixes), document (`.md/.mdx/.qmd/.skill/.txt/.rst/.html/.yaml/.yml/.j2/.jinja`),
paper (`.pdf`), image (`.png/.jpg/.jpeg/.gif/.webp/.svg`), office
(`.docx/.xlsx`), and video/audio.

**INGEST → PARSE → EXTRACT.** `extract(paths, cache_root=None, *, root=None,
parallel=True, max_workers=None, resolution_context_nodes=None,
resolution_context_edges=None)` runs two passes:

1. per-file structural extraction (classes, functions, imports, calls);
2. cross-file import resolution, which promotes file-level imports into
   class/symbol-level edges.

Concurrency: above `_PARALLEL_THRESHOLD = 20` uncached files it switches to a
`ProcessPoolExecutor` (worker count `cpu_count` or `GRAPHIFY_MAX_WORKERS`). The
`resolution_context_*` parameters are the incremental-rebuild hook: indexes from
files *not* being re-extracted are supplied read-only so a changed caller can
still bind to an unchanged callee — only edges sourced by re-extracted files are
emitted. Unchanged files are served from the content-hash-keyed AST cache.

**EXTRACT → GRAPH.** `build_from_json()` validates first, then materializes
NetworkX: `G.add_node(node["id"], **attrs)` and `G.add_edge(src, tgt, **attrs)`.
Before that it normalizes IDs (`ids.normalize_id` — casefold+NFKC to a fixpoint,
then non-word runs to `_`), mints external stubs for out-of-corpus imports,
folds hyperedges, and reconciles AST-vs-semantic duplicates. `dedup.py` runs
near-duplicate entity resolution (entropy gate 2.5 bits/char → MinHash/LSH
blocking at threshold 0.7 with 128 permutations → Jaro-Winkler verification at
92.0 → union-find merge; optional LLM tiebreak only for the 75–92 band).

**GRAPH → INDEX.** Indexes are lazy and live on the graph object:
`G.graph['_idf_cache']`, `G.graph['_trigram_index']`, plus community attributes
written onto nodes at export (`community`, `community_name`, `norm_label`).
No external index engine is used.

**GRAPH → QUERY → RESULT.** `serve.py` scores nodes lexically, picks seeds,
expands via BFS/DFS with hub suppression, and renders a token-budgeted text
subgraph. Details in `SEARCH_AND_QUERY.md`.

**GRAPH → PERSIST.** `export.to_json()` rewrites `graph.json` whole, after a
guard that refuses to shrink the graph or drop saved semantic nodes unless forced,
and fails *closed* if the existing file cannot be parsed.

## 4. Design decisions worth naming (independent of IWTT fit)

| Decision | Implementation | Why it matters generally |
|---|---|---|
| Determinism as a product feature | hash seed pinned at CLI entry for `update/extract/cluster-only/label`; sorted node/edge iteration under the partitioner; total-order tie-breaks on community IDs | Reproducible outputs make diffs reviewable and bugs bisectable |
| Canonical-vs-derived separation by sidecar | `.graphify_learning.json` is written *beside* `graph.json`; "no learning_* fields are ever stamped into the graph itself" | Exactly IWTT's derived-data rule, in miniature |
| Fail-closed destructive-write guard | refuse to overwrite a smaller or semantic-lossy graph; refuse when unparseable | Protects irreplaceable user data from a partial rebuild |
| Cache namespacing by producer version | `cache/ast/v{version}-s{schema}-g{grammars}/` | Stale caches from an older extractor cannot masquerade as current truth |
| Cheap change detection before hashing | mtime/size stat signature short-circuit | Avoids re-reading an unchanged corpus |
| Atomic writes | write-temp + `os.replace` with a Windows fallback | Crash-safe persistence |
| Closed vocabularies | `context` validated against `REFERENCE_CONTEXTS`; `confidence` against three values; `file_type` against six | Makes downstream logic total and queryable, instead of parsing prose |
| Explanations generated, not stored | `analyze.py` builds `why` strings at analysis time | Keeps the durable model small; see PROVENANCE.md for the caveat |

## 5. Audit 4 — Deterministic knowledge extraction

### What it does

- **Tree-sitter parsing**, not regex-in-the-large: each language is described by a
  declarative `LanguageConfig` (node-type sets for classes/functions/imports/calls,
  `name_field`, `body_field`, `call_function_field`, `call_accessor_*`).
- **37 extractor modules** under `graphify/extractors/` (apex, bash, blade, cobol,
  commonlisp, csharp, dart, dm, elixir, erlang, fortran, go, json_config, julia,
  markdown, objc, ocaml, pascal, pascal_forms, powershell, r, razor, robot, rust,
  sln, solidity, sql, terraform, vbnet, verilog, zig) plus dedicated functions in
  `extract.py` for python, js/ts, svelte, astro, ejs, vue, java, groovy, c, cpp,
  ruby, csharp, kotlin, scala, php, lua, swift, Lazarus packages, `.slnx`, `.csproj`.
  `BENCHMARKS.md` rounds this to "about 40 languages".
- **Symbol / reference / dependency / call extraction**, then a **cross-file
  resolution pass** (`extractors/resolution.py`, 4,232 LOC, plus per-language
  resolvers: `interface_dispatch.py`, `csharp_dispatch.py`, `swift_dispatch.py`,
  `ruby_resolution.py`, `pascal_resolution.py`, `markdown_resolution.py`).
  Cross-file bindings are emitted as **INFERRED**; direct syntactic ones as
  **EXTRACTED**.
- **Honest confidence on inference**: `indirect_call` carries
  `confidence_score: 0.85` with a comment explaining that the symbol link is
  direct but invocation is the inference — hence below the 0.95 tier.
- **Deterministic extraction never calls a model.** `llm.py` states plainly that
  code files are handled by the AST engine and never reach the model; only
  documents, papers and images take the semantic pass.
- **Optional non-AST sources** exist (`scip_ingest.py`, `pg_introspect.py`,
  `cargo_introspect.py`) — evidence the ingest layer is source-agnostic by design
  rather than code-only by architecture.

### Architecture shape applicable to a non-code corpus

Stripping language specifics, the reusable skeleton is:

1. a **classifier** that routes an item to a parser by type;
2. **per-type parsers** producing one normalized `{nodes, edges}` shape;
3. a **schema validator** that rejects malformed extractions before they reach the graph;
4. a **deterministic second pass** that resolves references *between* items into
   explicitly lower-confidence edges;
5. a **builder** that normalizes identity, merges duplicates and mints stubs;
6. a **content-hash cache** keyed by parser version so re-ingest is cheap;
7. **confidence labels on every derived edge**, with the extraction-time reason
   (syntactic vs inferred) preserved.

For IWTT this skeleton is relevant only for *external knowledge sources* — never
for thoughts. A thought is authored, not extracted. Putting an AST-shaped
"extraction" pipeline in front of the canonical Thought model would be a category
error (see `REJECTED_COMPONENTS.md` §1).

### What is NOT transferable

- The 10,054-LOC `engine.py`, `resolution.py`, and the per-language resolvers are
  ~95% language-specific logic. There is no smaller "core" worth lifting.
- The AST cache schema (`_AST_CACHE_SCHEMA = 16`) is versioned against
  tree-sitter grammar versions — meaningless outside that stack.
- The call-graph second pass, `indirect_call`, `mint_external_stubs_in_data`,
  receiver-type tables, and import-resolution algorithms answer questions about
  programs, not about ideas.

### Estimated cost if IWTT ever ingests repositories

**ESTIMATED (not measured here).** A code-ingestion subsystem of this shape means
at minimum: `tree-sitter` runtime + one grammar wheel per language, a
parser-per-type registry, a validator, a second resolution pass, a cache, and a
substantial test corpus. On the Graphify evidence, the *scaffolding* is a few
hundred lines; the *quality* lives in per-language resolution logic that grows
without bound. Recommendation: if IWTT ever needs source-code knowledge, consume
an external index (Graphify, SCIP, LSP output) as a **derived external-source
graph**, rather than re-implementing extraction inside IWTT.

## 6. Incremental behavior

- **Per-file AST cache**: content hash → cached extraction, namespaced by package
  version + cache schema + tree-sitter grammar fingerprint; entries from other
  namespaces are pruned on first use.
- **Semantic cache**: deliberately *not* version-invalidated, because
  invalidating it would re-bill the LLM for unchanged files.
- **Stat short-circuit**: mtime/size signature checked before hashing.
- **Incremental extract**: `resolution_context_nodes` / `resolution_context_edges`
  let a rebuild resolve against an unchanged corpus without re-parsing it.
- **Watch mode**: debounced rebuilds with a lock; a process that cannot take the
  lock (e.g. a git hook) queues its change set to `.pending_changes`, which the
  lock holder drains — a lost-update fix worth remembering for any
  IWTT background indexer.
- **Everything downstream is rebuilt whole**: clustering, analysis, report and
  `graph.json` are recomputed per build. There is no incremental graph delta
  format. (See `SCALING.md`.)

## 7. Caching, persistence, export

| Concern | Reality |
|---|---|
| Persistence | `graphify-out/graph.json` (node-link JSON, `"directed": false`, arc order = direction, `_src`/`_tgt` legacy markers), plus `.graphify_analysis.json`, `.graphify_labels.json`, `.graphify_learning.json`, `GRAPH_REPORT.md`, `wiki/`, `obsidian/`, `canvas`, `graph.svg`, `graph.html`, `.graphml`, `.cypher`, `reflections/LESSONS.md` |
| Output override | `GRAPHIFY_OUT` env var; refuses an override pointing at the project or a parent |
| Export formats | json, html (vis-network, `forceAtlas2Based`), obsidian vault, canvas (communities as groups), graphml, svg (matplotlib spring layout), cypher, mermaid call-flow, file-tree html, per-community wiki |
| Layout persistence | **None.** Interactive layout is client-side; nothing spatial is canonical. Relevant to IWTT's "graph layout must never become canonical truth". |

## 8. Verification notes

- Pipeline stage names and signatures cross-checked against `ARCHITECTURE.md` and
  the modules it names.
- `extract()` signature and parameter semantics read from the live function
  docstring, not the doc file.
- Concurrency threshold read directly from the module constant.
- The "code never reaches the model" claim is a code comment in `llm.py` and is
  consistent with the backend routing code.
- Language counts cross-checked between `graphify/extractors/` (37 modules),
  `detect.py`'s `CODE_EXTENSIONS`, and `BENCHMARKS.md`'s "about 40".
