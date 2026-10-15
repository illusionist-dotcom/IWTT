# SOURCE_EVIDENCE.md

Evidence index for the **IWTT–Graphify Acquisition Audit**. Every claim in this
bundle traces to a row below. Evidence was gathered by reading the actual
implementation in a local clone — not from the README.

## Repository identity

| Field | Value |
|---|---|
| Repository URL | https://github.com/Graphify-Labs/graphify.git |
| Branch inspected | `v8` (also `origin/HEAD`) |
| Commit SHA | `10130e79ed9caf8f60d998073e097531eaa1a395` |
| Commit subject | `chore(release): 0.9.91` (2026-10-14) |
| Tags present | `v0.9.89`, `v0.9.90`, `v0.9.91` |
| Package / version | `graphifyy` 0.9.91 (`pyproject.toml`) |
| License declared | `license = "Apache-2.0"`; `license-files = ["LICENSE","LICENSE-MIT","NOTICE"]` |
| Python floor | `requires-python = ">=3.10"` |
| Working tree | Unmodified. No fork, commit, branch, issue, or PR created. |

Clone used: `git clone --depth 50 https://github.com/Graphify-Labs/graphify.git graphify_repo`

## Repository structure (measured)

| Measure | Value |
|---|---|
| Files (excl. `.git`) | 1897 |
| Python files | 541 total; **95** inside the `graphify/` package |
| Markdown files | 368 (mostly per-agent skill bodies) |
| Tests | 427 files under `tests/` |
| Python LOC (incl. tests/tools) | ~221,000 |
| Largest modules | `extract.py` 513 KB / 10,974 LOC · `cli.py` 264 KB / 5,364 LOC · `llm.py` 174 KB · `serve.py` 142 KB / 3,007 LOC · `build.py` 139 KB / 2,806 LOC · `detect.py` 134 KB · `watch.py` 134 KB · `cache.py` 93 KB |
| Lockfile | `uv.lock`, 210 package blocks |

**Absent from the repository:** the benchmark harness directories `memory/`,
`crosstool/`, `graphify_eval/` that `BENCHMARKS.md` and `[tool.pytest.ini_options]
norecursedirs` reference.

```
$ ls -d memory crosstool graphify_eval
ls: cannot access 'memory': No such file or directory
ls: cannot access 'crosstool': No such file or directory
ls: cannot access 'graphify_eval': No such file or directory
```

Consequence: every number in `BENCHMARKS.md` is a **vendor claim that cannot be
reproduced from the public repository alone**.

## Audit 1 — Architecture

| Purpose | Path | Symbol | What the code proves |
|---|---|---|---|
| Declared pipeline | `ARCHITECTURE.md` | prose | `detect() → extract() → build() → cluster() → analyze helpers → report.generate() → export.to_*()` |
| Doc/code drift guard | `ARCHITECTURE.md` | prose | "`tests/test_architecture_doc.py` imports every symbol named here, so this table cannot drift from the code" |
| Extraction entry point | `graphify/extract.py` | `extract()` | Two passes: (1) per-file structural, (2) cross-file import resolution. Parallel `ProcessPoolExecutor` at `_PARALLEL_THRESHOLD = 20` uncached files. `resolution_context_nodes` / `resolution_context_edges` feed an unchanged corpus's indexes into an incremental rebuild (#2406/#2437/#2438) |
| Graph construction | `graphify/build.py` | `build_from_json()` | `G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})`; `G.add_edge(src, tgt, **attrs)` |
| Incremental merge | `graphify/build.py` | `merge_raw_extraction()`, `build_merge()`, `_is_ast_tier()` | AST-tier vs semantic-tier ownership rules |
| External stubs | `graphify/build.py` | `mint_external_stubs_in_data()` | Imports pointing outside the corpus become stub nodes instead of dangling edges |
| Hyperedges | `graphify/build.py` | `_normalize_hyperedge_members()`, `attach_hyperedges()` | N-ary relationship objects, persisted dual-slot (top-level key + `graph.hyperedges`) |
| Schema validation | `graphify/validate.py` | `VALID_CONFIDENCES`, `REQUIRED_EDGE_FIELDS` | `{"EXTRACTED","INFERRED","AMBIGUOUS"}`; edges must carry `source,target,relation,confidence,source_file` |
| Node-ID normalization | `graphify/ids.py` | `normalize_id()`, `make_id()` | casefold+NFKC to a fixpoint, then `[^\w]+ → _`; three producers (AST extractor, LLM, builder) must agree or entities split into ghost nodes |
| Output directory | `graphify/paths.py` | `GRAPHIFY_OUT`, `out_path()`, `_out_from_env()` | `graphify-out/` default, `GRAPHIFY_OUT` override; refuses an override pointing at the project or a parent |
| Atomic writes | `graphify/paths.py` | `write_text_atomic()`, `write_json_atomic()`, `os_replace_with_fallback()` | Crash-safe persistence |
| Determinism pinned at entry | `graphify/__main__.py` | `_HASHSEED_PINNED_COMMANDS = frozenset({"update","extract","cluster-only","label"})` | Hash seed pinned for the commands whose output must be reproducible |
| Exporters | `graphify/export.py` | `to_json`, `to_cypher`, `to_obsidian`, `to_canvas`, `to_graphml`, `to_svg` | Six formats in one module |
| Other output surfaces | `graphify/exporters/html.py`, `graphify/wiki.py`, `graphify/callflow_html.py`, `graphify/tree_html.py` | `to_html`, `to_wiki`, `write_callflow_html`, `write_tree_html` | Interactive HTML, per-community wiki, Mermaid call-flow, file-tree HTML |
| HTML layout | `graphify/exporters/html.py` | `forceAtlas2Based` (vis-network) | Interactive layout is client-side JS — geometry is never persisted |
| CLI/agent surface | `graphify/cli.py`, `graphify/serve.py`, `graphify/hooks.py`, `graphify/install.py` | — | CLI dispatch, MCP server, git hooks, per-host installers |

## Audit 2 — Graph model

| Purpose | Path | Symbol | What the code proves |
|---|---|---|---|
| Node contract | `graphify/validate.py` | `REQUIRED_NODE_FIELDS`, `VALID_FILE_TYPES` | Required `{id,label,file_type,source_file}`; `file_type ∈ {code,document,paper,image,rationale,concept}` |
| Edge contract | `graphify/validate.py` | `REQUIRED_EDGE_FIELDS` | Required `{source,target,relation,confidence,source_file}` |
| Canonical edge shape | `graphify/extractors/engine.py` | `_semantic_reference_edge()` | Emits `{source,target,relation:"references",context,confidence:"EXTRACTED",source_file,source_location:"L<n>",weight:1.0}`; raises on an unknown `context` |
| Numeric confidence | `graphify/extractors/engine.py` | `indirect_call` edge dict | `confidence:"INFERRED"`, `confidence_score: 0.85`; comment: "the symbol link is direct … but that it is ever INVOKED is the inference, which is why this is not the 0.95 tier" |
| Location format | `graphify/extractors/engine.py` | `_source_location()` | `L<line>` |
| Directedness vs storage | `graphify/paths.py` | `restore_arc_direction()` | `graph.json` is written `"directed": false` yet arc order (`source`→`target`) is the direction; legacy `_src`/`_tgt` markers win where present (#2309) |
| Node rationale | `graphify/serve.py` | `_node_rationale_text()` | Nodes carry a prose `rationale` attribute — the stated *why* of a concept |
| Attributes | `graphify/serve.py` | `_node_attributes_text()` | Arbitrary `attributes` dict is flattened and searchable |
| Edge conflict resolution | `graphify/build.py` | `dedupe_edges()`, `_execution_wins()` | Deterministic winner when two producers emit the same pair |

## Audit 3 — Search / query

| Purpose | Path | Symbol | What the code proves |
|---|---|---|---|
| No vectors, no BM25, no FTS | whole package | — | `grep -ril` over `graphify/`: `bm25` 0 files, `fts5` 0, `vector_store` 0, `cosine` 0, `tfidf` 0, `inverted index` 0. README line 38: "**Not a vector index.** No embeddings, no vector store: a real graph you traverse." |
| Tokenizer | `graphify/serve.py` | `_search_tokens()` | Diacritic-folded `[^\W_]+`; underscore is a separator so `snake_case` and `kebab-case` behave alike |
| Query terms | `graphify/serve.py` | `_query_terms()`, `_QUERY_STOPWORDS` | Question/filler words (English + common German/Romance) dropped; falls back to unfiltered terms if the query is all stopwords |
| CJK support | `graphify/serve.py` | `_has_chinese()`, `_segment_chinese()`, `_is_searchable()` | jieba segmentation when installed, bigram fallback otherwise |
| Ranking tiers | `graphify/serve.py` | `_EXACT_MATCH_BONUS=1000.0`, `_PREFIX_MATCH_BONUS=100.0`, `_SUBSTRING_MATCH_BONUS=1.0`, `_SOURCE_MATCH_BONUS=0.5`, `_RATIONALE_MATCH_BONUS=0.75`, `_ATTRIBUTE_MATCH_BONUS=0.75` | Human-authored weights, strongest tier per term (no double counting) |
| IDF weighting | `graphify/serve.py` | `_compute_idf()` | `log(1 + N/(1+df))`, cached on `G.graph['_idf_cache']` so it dies with a hot-reloaded graph |
| Coverage scaling | `graphify/serve.py` | `_score_query()` | `score += tiered * (matched / n_terms) ** 2` (squared) so one generic exact hit cannot bury a multi-term match (#1602) |
| Full-query tier | `graphify/serve.py` | `_score_query()` | Whole-query equality/prefix worth ×10 of the per-token tier, scaled by the rarest constituent term's IDF |
| Rationale/attribute recall | `graphify/serve.py` | `_RATIONALE_MATCH_BONUS`, `_ATTRIBUTE_MATCH_BONUS` | "why" questions whose words live only in prose attributes still retrieve (#2293, #3625); these tiers deliberately do not count toward coverage |
| Candidate prefilter | `graphify/serve.py` | `_get_trigram_index()`, `_trigram_candidates()`, `guard_frac=0.10` | Lazy trigram→postings index cached on the graph; **falls back to a full scan** when a needle's rarest trigram exceeds 10% of nodes — documented "never-worse contract" |
| Ranking determinism | `graphify/serve.py` | `_score_query()` sort | `(-score, len(label), node_id)`; seed tie-break `(-singleton, -degree, label_len, nid)` |
| External rankers | `graphify/serve.py` | comment at ~line 1414 | "an external ranker — embeddings, a learned…" can only supply **explicit start nodes**; there is no embedding index in the product |
| Seed selection | `graphify/serve.py` | `_pick_seeds()` | ≥1 seed per distinct query term that matches; `max_k=3`, `gap_ratio=0.2`; dedupe by normalized label (#1766) |
| Context assembly | `graphify/serve.py` | `_subgraph_to_text()` | `char_budget = token_budget * 3`; seeds first, then hop-distance from seeds, then `(-degree, id)`; ≤10 attribute pairs, ≤200 chars of attrs (Budget discipline in AB.3) |
| Output hardening | `graphify/serve.py` | `sanitize_label()` call sites | Comment cites F-010: corpus- or LLM-derived labels could otherwise inject ANSI escapes or prompt markup into agent context |
| Path queries | `graphify/serve.py` | `_shortest_path_text()`, `_path_search_graph()` | Direction-following by default; `undirected=true` opt-out |
| Budget cut | `graphify/serve.py` | `_cut_lines_to_budget()` | Explicit truncation with a narrowing hint instead of silent loss |

## Audit 4 — Deterministic extraction

| Purpose | Path | Symbol | What the code proves |
|---|---|---|---|
| Declarative language model | `graphify/extractors/models.py` | `LanguageConfig` | `ts_module`, `ts_language_fn`, `class_types`, `function_types`, `import_types`, `call_types`, `name_field`, `body_field`, `call_function_field`, `call_accessor_*`, `helper_fn_names`, `event_listener_properties` |
| Extractor modules | `graphify/extractors/` | 37 modules | apex, bash, blade, cobol, commonlisp, csharp, dart, dm, elixir, erlang, fortran, go, json_config, julia, markdown, objc, ocaml, pascal, pascal_forms, powershell, r, razor, robot, rust, sln, solidity, sql, terraform, vbnet, verilog, zig + base/models/engine/resolution |
| Inline extractors | `graphify/extract.py` | `extract_python`, `extract_js`, `extract_svelte`, `extract_astro`, `extract_ejs`, `extract_vue`, `extract_java`, `extract_groovy`, `extract_c`, `extract_cpp`, `extract_ruby`, `extract_csharp`, `extract_kotlin`, `extract_scala`, `extract_php`, `extract_lua`, `extract_swift`, `extract_lazarus_package`, `extract_slnx`, `extract_csproj` | One pattern per language: tree-sitter parse → walk → collect nodes/edges → second call-resolution pass |
| Cross-file resolution | `graphify/extractors/resolution.py` (4,232 LOC), `symbol_resolution.py`, `interface_dispatch.py`, `csharp_dispatch.py`, `swift_dispatch.py`, `ruby_resolution.py`, `pascal_resolution.py`, `markdown_resolution.py` | — | Import/reference resolution producing INFERRED edges and the colliding-ID disambiguation that keeps node IDs unique |
| Non-AST sources | `graphify/scip_ingest.py`, `pg_introspect.py`, `cargo_introspect.py` | — | Optional ingest of a SCIP index, live PostgreSQL DDL, Cargo metadata |
| File classification | `graphify/detect.py` | `CODE_EXTENSIONS` (~200 suffixes), `DOC_EXTENSIONS`, `PAPER_EXTENSIONS`, `IMAGE_EXTENSIONS`, `OFFICE_EXTENSIONS`, `VIDEO_EXTENSIONS` | `.md/.mdx/.qmd/.skill/.txt/.rst/.html/.yaml/.yml/.j2/.jinja`; `.pdf`; `.png/.jpg/.jpeg/.gif/.webp/.svg`; `.docx/.xlsx`; video/audio |
| URL & media ingestion | `graphify/ingest.py`, `security.py`, `google_workspace.py`, `transcribe.py`, `mcp_ingest.py` | `safe_fetch()`, `safe_fetch_text()`, `validate_url()` | SSRF-guarded fetch of user-supplied URLs, media transcription, Google Workspace ingest |
| Code never reaches the LLM | `graphify/llm.py` | comment above `_bind_node_evidence()` | "The semantic (LLM) extraction runs on documents/papers/images — code files are handled by the deterministic AST engine and never reach the model." |
| Language-scope honesty | `BENCHMARKS.md` §Cost | prose | "extracts with tree-sitter (deterministic, about 40 languages)" — matching the ~37 extractor modules plus inline extractors |

## Audit 5 — Clustering / communities

| Purpose | Path | Symbol | What the code proves |
|---|---|---|---|
| Engine cascade | `graphify/cluster.py` | `_partition()` | `graspologic_native.leiden` → `graspologic.partition.leiden` → `nx.community.louvain_communities` |
| Leiden parameters | `graphify/cluster.py` | `_native_leiden()` | `resolution`, `randomness=0.001`, `iterations=1`, `use_modularity=True`, `seed=42`, `trials=1`; suppresses graspologic's ANSI progress output (#19) |
| Determinism | `graphify/cluster.py` | `_partition()` body | Nodes added `sorted(key=str)`; edge rows sorted with canonicalized endpoint pairs. The comment records a measured artifact: identical input produced **70 vs 69 communities** under `PYTHONHASHSEED=1` vs `2` on a 914-node graph |
| Isolates | `graphify/cluster.py` | `cluster()` | Isolates are removed and each becomes its own single-node community (Leiden drops them silently) |
| Hub suppression | `graphify/cluster.py` | `cluster(exclude_hubs_percentile=…)` | Super-hubs excluded from partitioning, reattached by majority-vote neighbour community; nodes whose only neighbours are hubs ("stranded") are handled explicitly (#919) |
| Low-confidence edges | `graphify/cluster.py` | `cluster(ambiguous_scale=0.5)`, env `GRAPHIFY_AMBIGUOUS_SCALE` | `AMBIGUOUS` edge weights scaled down so uncertain links cannot glue clusters together; `0.0` drops them from partitioning only (#4199) |
| Oversized split | `graphify/cluster.py` | `_MAX_COMMUNITY_FRACTION=0.25`, `_MIN_SPLIT_SIZE=10`, `_split_community()` | Any community >25% of the graph is re-partitioned by a second Leiden pass on its subgraph |
| Cohesion re-split | `graphify/cluster.py` | `_COHESION_SPLIT_THRESHOLD=0.05`, `_COHESION_SPLIT_MIN_SIZE=50` | Large, weakly-connected communities (doc-hub artifacts) are re-split |
| Cohesion metric | `graphify/cluster.py` | `cohesion_score()` | intra-community edges ÷ `n(n-1)/2`, self-loops excluded (a recursive self-edge used to push the ratio past 1.0) |
| Stable IDs | `graphify/cluster.py` | `cluster()` tail | `final_communities.sort(key=lambda nodes: (-len(nodes), tuple(sorted(map(str, nodes)))))` — a total order, so equal-sized communities cannot permute between runs (#1090 follow-up) |
| Incremental identity | `graphify/cluster.py` | `remap_communities_to_previous()`, `community_member_sigs()`, `label_still_applies()` | Greedy one-to-one overlap matching to keep IDs stable; sha256 membership fingerprints (16 hex) prevent a stale LLM label surviving a re-scope (#2298) |
| LLM-free labels | `graphify/cluster.py` | `label_communities_by_hub()` | Default community names come from the highest-degree member, ties broken by node id — explicitly "Deterministic, LLM-free" |
| Cohesion on report | `graphify/report.py` | `generate(G, communities, cohesion_scores, …)` | Communities + cohesion are reported per build |

## Audit 6 — Traversal and path queries

| Purpose | Path | Symbol | What the code proves |
|---|---|---|---|
| Traversal view | `graphify/serve.py` | `_traversal_view()`, `edge_data()`, `edge_datas()` | Direction overlaid onto an undirected storage graph so traversal can honour arc order |
| BFS/DFS | `graphify/serve.py` | `_bfs()`, `_dfs()` | Depth-limited; **hub threshold `max(50, p99(degree))`** — high-degree hubs are visited but not expanded as transit unless they are seeds |
| Induced edges | `graphify/serve.py` | `_complete_induced_edges()` | Edges between already-visited nodes are added so the returned subgraph is not arbitrarily sparse |
| Path search | `graphify/serve.py` | `_path_search_graph()` | Builds the search graph honouring or ignoring stored direction |
| Endpoint resolution | `graphify/serve.py` | `_pick_scored_endpoint()`, `_resolve_path_endpoint()`, `_resolve_path_scoped_symbol()`, `_resolve_python_qualified()` | Query→node resolution for path endpoints |
| Ambiguity refusal | `graphify/serve.py` | `_tier_refusal()`, `_ambiguity_message()`, `_rivals_across_files()`, `_no_scoped_match_message()` | Refuses and explains rather than guessing one of several equally-scored rivals |
| Cycles | `graphify/analyze.py` | `find_import_cycles()` | Circular dependency detection with a generated explanation |
| Diff | `graphify/analyze.py` | `graph_diff()` | Structural comparison of two builds |

## Audit 7 — Provenance

| Purpose | Path | Symbol | What the code proves |
|---|---|---|---|
| Confidence vocabulary | `graphify/validate.py` | `VALID_CONFIDENCES` | Exactly three labels, validated on edges |
| Confidence semantics | `ARCHITECTURE.md` | table | EXTRACTED = explicit in source; INFERRED = reasonable deduction; AMBIGUOUS = uncertain, "flagged for human review in GRAPH_REPORT.md" |
| Numeric confidence | `graphify/extractors/engine.py` | `confidence_score: 0.85` | Present on `indirect_call`; comment: "Previously no score was emitted at all and the edge inherited the 0.5 default the rubric forbids (#2813)" |
| Context tag | `graphify/extractors/engine.py` | `REFERENCE_CONTEXTS` check in `_semantic_reference_edge()` | Unknown context raises — the tag is a closed vocabulary, not free text |
| Sparse provenance fields | package-wide grep | — | `"provenance"` 4 hits (all `reflect.py`), `"evidence"` 1 hit (a fallback key in `callflow_html.py`), `"extractor"` 1, `"origin"` 1. **There is no per-edge evidence object.** |
| Why-strings | `graphify/analyze.py` | `_surprise_score()`, `_cross_community_surprises()`, `suggest_questions()` | Explanations are generated at analysis time, e.g. "Edge tagged AMBIGUOUS (relation: …) — confidence is low", "High betweenness centrality (…) — this node is a cross-community bridge", "… has N INFERRED edges — model-reasoned connections that need verification" |
| LLM fabrication guard | `graphify/llm.py` | `_bind_node_evidence()` | Flags (never drops) a `file_type == "code"` node from a semantic pass when none of its label/id identifiers occur in the dispatched source bytes; sets `verification = "unverified"`; idempotent; skips nodes the model already hedged. Rationale in comment: distinguishes "read" from "inferred" |
| Ops provenance | `graphify/export.py` | `_git_head()`, `backup_if_protected()`, `attach_hyperedges()` | Builds record the commit they were built at; protected outputs are backed up before overwrite |
| Query/answer provenance | `graphify/reflect.py` | `_provenance_for()`, `build_learning_overlay()` | Per-node most-recent-first trail of `(question, date, outcome)` capped at `_PROVENANCE_CAP = 5` |
| Outcome signals | `graphify/ingest.py` | `OUTCOMES`, `save_query_result()` | Memory docs filed back into the graph carry outcome signals (`useful` / `dead_end` / `corrected`) |
| Learning scoring | `graphify/reflect.py` | `_DEFAULT_HALF_LIFE_DAYS=30.0`, `_DEFAULT_MIN_CORROBORATION=2`, `_SCORE_NDIGITS=9` | Citations contribute signed, time-decayed value; a node is only "preferred" once corroborated by ≥2 distinct results — "one save can't mint a trusted lesson" |
| Derived-layer separation | `graphify/reflect.py` | `LEARNING_SIDECAR_NAME = ".graphify_learning.json"`, `_LEARNING_SCHEMA_VERSION = 1` | Comment: "a SIDECAR, kept separate from the durable structural truth in graph.json — no learning_* fields are ever stamped into the graph itself" |
| Sidecar determinism | `graphify/reflect.py` | `write_learning_sidecar()` | "Sorted keys + indent=2 so re-runs on identical input (and a fixed `now`) are byte-identical" |
| Lessons artifact placement | `graphify/reflect.py` | module docstring | Written to `graphify-out/reflections/LESSONS.md`, "rather than inside the wiki because `graphify export wiki` deletes every `wiki/*.md` on each run" |
| Display-time merge | `graphify/serve.py` | `_subgraph_to_text()` overlay lookup | Learning status is merged into query output at display time (`learning=<status>[:stale]`) and counted inside the character budget |

## Audit 8 — Scale

| Purpose | Path | Symbol | What the code proves |
|---|---|---|---|
| Whole graph in memory | `graphify/serve.py` | `_read_graph()`, `_load_graph()`, `_GraphContextCache`, `_max_server_contexts()` | The query path loads the entire graph as a NetworkX object; a bounded cache holds several resident graphs |
| Derived-index lifetime | `graphify/serve.py` | `G.graph['_idf_cache']`, `G.graph['_trigram_index']` | Indexes live on the graph object so a hot-reload invalidates them atomically |
| Persistence | `graphify/export.py` | `to_json()` | Whole-file JSON rewrite of the node-link graph each build |
| Write guard | `graphify/export.py` | `to_json()` pre-write block | Refuses to overwrite with a smaller graph or one missing saved semantic nodes unless `force=True`; **fails closed** when the existing file is unparseable (#479) |
| Read guard | `graphify/security.py` | `check_graph_file_size_cap()` | Size cap on the graph-read path |
| AST cache | `graphify/cache.py` | `_AST_CACHE_SCHEMA = 16`, `cache_dir()`, `load_cached()`, `save_cached()` | Content-hash keyed, namespaced by package version + cache schema + tree-sitter grammar fingerprint, so extractor fixes invalidate stale entries |
| Semantic cache | `graphify/cache.py` | `check_semantic_cache()`, `save_semantic_cache()`, `prune_semantic_cache()` | Deliberately *not* version-invalidated — re-billing the LLM on every release was rejected |
| Stat short-circuit | `graphify/cache.py` | `_stat_sig_fresh()`, `_stat_index_file()`, `_mtime_granularity_ns()` | mtime/size signature avoids re-hashing unchanged files |
| Incremental rebuild | `graphify/watch.py` | `watch()`, `check_update()`, `_queue_pending()`, `_drain_pending()`, `_PENDING_DRAIN_MAX_PASSES`, `_rebuild_lock` | Debounced rebuild; a hook process that cannot take the lock appends to `.pending_changes` so its change set is not lost (#1059) |
| Parallel extraction | `graphify/extract.py` | `_PARALLEL_THRESHOLD = 20`, `GRAPHIFY_MAX_WORKERS` | Multi-process extraction above a floor of uncached files |
| Scale measurement | this audit | `research_instrumentation/measure_graphify.py`, `measure_load_1m.py` | See SCALING.md |

## Audit 9 — Local-first / privacy

| Purpose | Path | Symbol | What the code proves |
|---|---|---|---|
| No telemetry | package-wide grep | — | No matches for `telemetry\|posthog\|segment\|sentry\|analytics` in `graphify/*.py`. README line 421: "**No telemetry**, no usage tracking, no analytics." |
| No cloud coupling in code | package-wide grep | `cli.py:139`, `__main__.py:1069` | Two `print("https://app.graphify.com")` upsell lines; no API calls to the vendor. (Graphify Cloud is a separate commercial product, not required by the OSS tool.) |
| Code path offline | README §Privacy; `graphify/llm.py` | — | "A code-only corpus requires no API key: `graphify extract` runs fully offline"; docs/PDFs/images need a configured backend |
| Local backends | `graphify/llm.py` | `BACKENDS["ollama"]`, `BACKENDS["claude-cli"]`, `_resolve_ollama_base_url()` | `ollama` defaults to `http://localhost:11434/v1` with model `qwen2.5-coder:7b` and pricing `0.0`; accepts `OLLAMA_HOST` in bare-port/`:port` forms |
| Cloud backends | `graphify/llm.py` | `BACKENDS` | `claude`, `kimi` (Moonshot), `openai`, `gemini`, `bedrock`, `deepseek`, `azure` — all opt-in |
| Auto-detection order | README §Privacy | — | Gemini → Kimi → Claude → OpenAI → DeepSeek → Azure → Bedrock → Ollama; README warns Kimi routes to servers in China |
| Local-provider guard | `graphify/llm.py` | `_load_custom_providers()`, `GRAPHIFY_ALLOW_LOCAL_PROVIDERS` | A project-local `./.graphify/providers.json` is ignored unless explicitly allowed, because custom providers control where prompts are sent; non-HTTPS non-loopback endpoints warn |
| Query logging | `graphify/querylog.py` | `_log_path()`, `_log_responses()` | Opt-in only (#1797): off unless `GRAPHIFY_QUERY_LOG=<path>` or `GRAPHIFY_QUERY_LOG_ENABLE=1`; `GRAPHIFY_QUERY_LOG_DISABLE=1` wins; `GRAPHIFY_QUERY_LOG_RESPONSES=1` adds full responses. Comment: "A default-on record of proprietary queries contradicts graphify's on-device, no-telemetry posture" |
| Fail-silent logging | `graphify/querylog.py` | `log_query()` | "Never raises" — logging cannot break a query |
| Network boundaries | `SECURITY.md` | §Security Model | AST local/deterministic; semantic pass may send content; `ingest` is explicit outbound fetch; HTTP MCP is an opt-in listener |
| SSRF defence | `graphify/security.py` | `validate_url()`, `safe_fetch()`, `_SSRFGuardedHTTPHandler`, `_NoFileRedirectHandler`, `_build_opener()` | http/https only; private/loopback/link-local/cloud-metadata blocked; redirects re-validated; 50 MB / 10 MB caps; non-2xx raises rather than being read as content |
| Path traversal | `graphify/security.py` | `validate_graph_path()` | An MCP-served graph must resolve inside `graphify-out/` |
| Prompt-injection defence | `graphify/llm.py` | `<untrusted_source>` delimiter blocks; `_neutralise_injection_sentinels()`; `_read_files()` | Source files are wrapped in hash-stamped inert-data blocks; chat-template jailbreak sentinels are defanged. SECURITY.md: "table-stakes defense … changes it from 'works on first try' to 'requires evasion'" |
| Output sanitization | `graphify/security.py` | `sanitize_label()` | Strips control characters, caps at 256 chars; applied to MCP text output (F-010) |
| Markup/injection hardening | `graphify/ingest.py` | `_yaml_str()` | Escapes backslash, quote, `\n`, `\r`, `\t`, `\0`, U+2028, U+2029 so a hostile fetched page title cannot break out of a YAML scalar and inject keys (F-009/F-019) |
| No credential storage | `SECURITY.md` | §What graphify does NOT do | "Does not store credentials or API keys"; no `shell=True` anywhere; tree-sitter parsing performs no `eval`/`exec` |
| Local-only operations | `SECURITY.md` | §Optional network calls | `ingest` fetches only user-given URLs; PDF extraction is local; watch mode uses local filesystem events only |

## Audit 10 — Agent integration

| Purpose | Path | Symbol | What the code proves |
|---|---|---|---|
| MCP entry point | `pyproject.toml`, `graphify/serve.py` | `graphify-mcp = "graphify.serve:_main"`, `serve()`, `serve_http()` | stdio transport by default; HTTP opt-in |
| Tool inventory | `graphify/serve.py` | `list_tools()` | 10 tools: `query_graph`, `get_node`, `get_neighbors`, `get_community`, `god_nodes`, `graph_stats`, `shortest_path`, `list_prs`, `get_pr_impact`, `triage_prs` |
| Query tool schema | `graphify/serve.py` | `query_graph` inputSchema | `question` (required), `mode` ∈ {bfs,dfs}, `depth` (1–6, default 3), `token_budget` (default 2000), `context_filter` (edge-context list) |
| Resources | `graphify/serve.py` | resource list | `graphify://report`, `://stats`, `://god-nodes`, `://surprises`, `://audit`, `://questions` — all read-only |
| Per-project scoping | `graphify/serve.py` | `_select_graph()`, `_resolve_graph_path()`, `_GraphContextCache`, `_max_server_contexts()` | Every tool accepts an optional `project_path`; the number of resident graphs is bounded |
| Auth | `graphify/serve.py` | `_ApiKeyMiddleware`, `_build_http_app()`, `serve_http()` | `Authorization: Bearer <key>` or `X-API-Key`; `GRAPHIFY_API_KEY` env; blank key treated as absent; warns when binding `0.0.0.0` without a key; docstring notes "OAuth is … localhost" |
| Client compatibility | `graphify/serve.py` | `_node_arg()`, `ToolError` | Accepts `label`/`node_id`/`id` because agent frameworks differ; `ToolError` surfaces as `isError: true` |
| MCP version tolerance | `graphify/serve.py` | `_build_server()` | Supports both mcp 1.x decorators and 2.x `on_*` constructor callbacks; starlette capped below its next major with named CVEs addressed |
| Host skill surfaces | `graphify/skills/<host>/`, `graphify/always_on/`, `graphify/skill*.md` | 14 host dirs + 6 always-on blocks | claude, codex, copilot, opencode, kilo, kiro, amp, trae, vscode, agents, claw, droid, pi, windows |
| Always-on instruction | `graphify/always_on/claude-md.md` | prose | Tells the agent to run `graphify query` **before** its first grep/read, to put that rule in every subagent prompt, and to read raw files only for lines the graph already located |
| Context-economy rationale | `graphify/always_on/*.md`; `graphify/benchmark.py` | `run_benchmark(graph_path)` | "These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output"; the benchmark tool compares corpus vs subgraph token cost |
| Git hook integration | `graphify/hooks.py` | `_HOOK_MARKER`, `_PYTHON_DETECT`, install/uninstall | post-commit / post-checkout / merge hooks launch a detached incremental rebuild; the interpreter path is pinned at install time because GUI git clients and CI runners have minimal PATHs |
| PR awareness | `graphify/prs.py` | `list_prs`/`get_pr_impact`/`triage_prs` handlers | GitHub PR blast radius over graph communities (via the `gh` CLI, which must be present) |
| Agent query telemetry | `graphify/querylog.py` | `log_query(kind, question, corpus, …)` | Records query kind, question text, corpus path, nodes returned, duration — opt-in only |

## Audit 11 — License

| Purpose | Path | Evidence |
|---|---|---|
| Main license | `LICENSE` | Verbatim Apache License 2.0 ("Apache License / Version 2.0, January 2004") |
| Secondary license | `LICENSE-MIT` | MIT License, "Copyright (c) 2026 Safi Shamsi" — retained for pre-relicensing contributions |
| NOTICE | `NOTICE` | "Graphify / Copyright 2026 Safi Shamsi and the Graphify contributors. … Portions of this software were contributed under the MIT License prior to the relicensing and remain available under those terms." |
| Declared metadata | `pyproject.toml` | `license = "Apache-2.0"`; `license-files = ["LICENSE","LICENSE-MIT","NOTICE"]`; keywords include `knowledge-graph`, `rag`, `graphrag`, `community-detection`, `tree-sitter`, `leiden`, `llm` |
| File-level headers | package-wide grep | **0 of 95** `graphify/*.py` files carry an SPDX tag or copyright header |
| Submodules | `git submodule status`, `.gitmodules` | None present |
| Vendored code | package-wide grep | No vendored third-party tree. `graphify/_minhash.py` is a self-described "datasketch-compatible drop-in (no scipy)" reimplementation |
| Core runtime deps | `pyproject.toml` | `networkx>=3.4` (BSD-3-Clause) · `numpy` (BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0) · `rapidfuzz>=3.0` (MIT) · `tree-sitter` runtime + ~28 grammar wheels (MIT) |
| Optional extras (licenses via PyPI metadata) | `pyproject.toml` | `mcp` MIT · `starlette` BSD-3-Clause · `neo4j` "Apache-2.0 AND Python-2.0" · `falkordb` MIT · `pypdf` BSD-3-Clause · `markdownify` MIT · `watchdog` Apache-2.0 · `graspologic`/`graspologic-native` MIT · `matplotlib` PSF · `pillow` MIT-CMU · `python-docx` MIT · `openpyxl` MIT · `boto3` Apache-2.0 · `anthropic` MIT · `openai` Apache-2.0 · `jieba` MIT · `faster-whisper` MIT · `yt-dlp` Unlicense · `tiktoken` MIT · `robotframework` Apache-2.0 |
| Copyleft in extras | PyPI metadata | `psycopg` / `psycopg-binary` = **LGPL-3.0-only** (optional `postgres` extra only) |
| Copyleft in dev group | PyPI metadata | `hypothesis` = MPL-2.0 (dev/test only, not shipped) |

## Audit 12 — Rejection inputs

Load-bearing observations for `REJECTED_COMPONENTS.md`:
`graphify/extractors/engine.py` (10k LOC of language-specific AST logic) ·
`graphify/serve.py` (tier weights calibrated on short symbol-like labels) ·
`graphify/extract.py` (AST-vs-semantic write-ownership rules) ·
`graphify/build.py` (chunk/merge machinery, `_lost_saved_semantic_nodes`,
`mint_external_stubs_in_data`) · `graphify/cache.py` (`_AST_CACHE_SCHEMA` tied to
tree-sitter grammar versions) · `graphify/llm.py` (9 backends, vision handling,
PDF/image routing) · `graphify/hooks.py` (git hook installation) ·
`graphify/prs.py` (`gh` CLI dependency).

## Benchmark claims (vendor-authored; harness not in the repository)

All from `BENCHMARKS.md` (last updated 2026-07-05). Its "Reproducing" section
invokes `memory/runner.py` and `crosstool/run.py`, which do not exist in the clone.

| Claim | Value |
|---|---|
| LOCOMO (n=300) QA accuracy | graphify 45.3% · hybrid RRF 43.3% · dense RAG 41.3% · BM25 31.3% · supermemory 49.7% · mem0 27.3% |
| LOCOMO recall@10 | graphify 0.497 · BM25 0.362 · mem0 0.048 · supermemory 0.149 |
| LongMemEval-S (n=50) | graphify 76% · dense RAG 76% · mem0 70% |
| ERPNext cross-tool key-fact coverage | graphify tool 82.0% vs grep/read baseline 70.8% (~140K tokens/query, n=6) |
| Ingest cost | ~$1.40 vs supermemory $15.67, mem0 $3.48 |
| Seed-only ablation | 42.7% (no graph expansion) |
| Temporal suite | 689 weekly checkpoints 2011→2026: 3,069→22,620 nodes; 2,900→48,710 edges |
| INFERRED cross-file precision | 12/12 on a 2026-10-05 manual FastAPI spot-check, self-described as "a dated manual spot-check, not a continuously-measured metric" |
| Judge | Kimi K2.6; `coverage = (covered + 0.5*partial)/total`; blind-validated 90.6% agreement, Cohen's κ 0.81 |

**Internal inconsistency to flag:** `BENCHMARKS.md` §"Cost and token economics"
says graphify "extracts with tree-sitter … and a **local embedder**", while
`README.md` line 38 says "**No embeddings, no vector store**". The shipped
package contains no embedding code; the discrepancy lives in the (unpublished)
harness's adapters. This is exactly the kind of claim-vs-reality gap the README's
"Not a vector index" line cannot settle on its own.

## Runtime evidence gathered by this audit

Environment: Linux x86_64, 2 vCPU Intel Xeon @ 2.60 GHz, ~1.9 GB RAM visible,
Python 3.13.16. Installed for measurement only: `networkx 3.7`, `numpy 2.3.5`,
`rapidfuzz 3.14.6`, `graspologic-native 1.3.1`.

Import check (the query and cluster paths run with only the declared core deps
plus the optional Leiden extra):

```
$ python3 -c "import graphify.serve, graphify.cluster"   # OK
$ python3 -c "import graspologic_native"                 # OK after installing the 'leiden' extra
```

Full results in SCALING.md; raw JSONL in
`research_instrumentation/timings.jsonl`.

## Methods, limits, and what was NOT done

- **Read-only.** No file in the Graphify clone was modified. Instrumentation
  lives outside the clone, in `research_instrumentation/`.
- **IWTT untouched.** No IWTT source, database, schema, or archive was read,
  modified, or created. IWTT v0.1 was not present in this environment; the audit
  reasons about IWTT from the mission brief's description and treats its
  canonical/derived separation as a hard constraint.
- **No fork, commit, PR, issue, or publication.** None created.
- **Dynamic analysis covers the graph / query / cluster path only.** The
  per-language AST extractors were not run against real multi-language corpora,
  so extraction-quality claims are unverified here.
- **No LLM backend was configured**, so the semantic pass, vision pass, dedup
  LLM tiebreak, and their caching were not exercised. Statements about them come
  from code reading.
- **`BENCHMARKS.md` numbers were not reproduced** (harness unpublished).
- **Timings are single-machine, single-run** on synthetic note-shaped corpora,
  not code graphs, and not a benchmark. They indicate order of magnitude.
