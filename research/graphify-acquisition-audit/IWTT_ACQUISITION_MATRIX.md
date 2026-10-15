# IWTT_ACQUISITION_MATRIX.md

Every potentially useful Graphify concept/component, classified:

- **ADOPT** — take it; the technique and its implementation shape both fit IWTT.
- **ADAPT** — the technique is right; Graphify's implementation is calibrated for
  code graphs and must be re-derived for thoughts.
- **STUDY ONLY** — understand and keep in the design vocabulary; do not schedule work.
- **REJECT** — explicitly out of scope (details and rationale in `REJECTED_COMPONENTS.md`).

All references are to commit `10130e79ed9caf8f60d998073e097531eaa1a395`.
"Canonical/derived" states where the result may live in IWTT.

---

## 1. Summary table

| # | Candidate | Graphify location | Class | IWTT layer |
|---|---|---|---|---|
| 1 | Tokenizer + stopwords + CJK segmentation | `serve.py::_search_tokens`, `_query_terms`, `_segment_chinese` | **ADOPT** | Derived |
| 2 | Tiered lexical scorer (exact/prefix/substring/source/rationale/attribute) | `serve.py::_score_query` + `_*_BONUS` constants | **ADAPT** | Derived |
| 3 | IDF term weighting | `serve.py::_compute_idf` | **ADOPT** | Derived |
| 4 | Coverage scaling (multi-term match must beat a lone hit) | `serve.py::_score_query` | **ADAPT** | Derived |
| 5 | Trigram candidate prefilter with whole-scan fallback guard | `serve.py::_get_trigram_index`, `_trigram_candidates` | **ADOPT** | Derived |
| 6 | Per-term seed guarantee + seed dedupe by normalized label | `serve.py::_pick_seeds` | **ADOPT** | Derived |
| 7 | Hub-suppressed BFS/DFS + induced-edge completion | `serve.py::_bfs`, `_dfs`, `_complete_induced_edges` | **ADOPT** | Derived |
| 8 | Hop-distance-ordered, token-budgeted, sanitized rendering | `serve.py::_subgraph_to_text`, `_cut_lines_to_budget` | **ADOPT** | Derived |
| 9 | Ambiguity refusal with rival explanations | `serve.py::_tier_refusal`, `_ambiguity_message`, `_rivals_across_files` | **ADOPT** | Derived |
| 10 | Deterministic total-order tie-breaks everywhere | `serve.py`, `cluster.py` sort keys | **ADOPT** | Derived (enables canonical stability) |
| 11 | Direction-aware path search with `undirected` opt-out | `serve.py::_path_search_graph`, `_shortest_path_text` | **ADOPT** | Derived |
| 12 | Closed-vocabulary confidence + numeric score + location anchor | `validate.py::VALID_CONFIDENCES`, `engine.py::_semantic_reference_edge`, `indirect_call` `confidence_score: 0.85` | **ADAPT** | Canonical (user fields) + derived (AI fields), orthogonal |
| 13 | Evidence binding: verify AI claims against source, flag never drop | `llm.py::_bind_node_evidence` | **ADOPT** | Derived |
| 14 | Corroboration-gated promotion + signed time-decayed scoring | `reflect.py` (`_DEFAULT_MIN_CORROBORATION`, `_DEFAULT_HALF_LIFE_DAYS`) | **ADAPT** | Derived |
| 15 | Derived sidecar, never stamped into the graph | `reflect.py::LEARNING_SIDECAR_NAME`, `write_learning_sidecar` | **ADOPT** | Derived |
| 16 | Capped most-recent-first provenance trail per entity | `reflect.py::_provenance_for`, `_PROVENANCE_CAP` | **ADOPT** | Derived |
| 17 | Outcome signals (`useful`/`dead_end`/`corrected`) for lifecycle learning | `ingest.py::OUTCOMES`, `save_query_result` | **ADAPT** | Canonical events + derived scores |
| 18 | Explanation as a generated view, not stored prose | `analyze.py` `why` sites | **ADAPT** | Derived |
| 19 | Leiden clustering (native binding) with deterministic seed | `cluster.py::_native_leiden`, `_partition` | **ADAPT** | Derived |
| 20 | Confidence-scaled edge weights in clustering | `cluster.py::ambiguous_scale` | **ADOPT** | Derived |
| 21 | Hub exclusion + majority-vote reattachment | `cluster.py` (`exclude_hubs_percentile`) | **ADAPT** | Derived |
| 22 | Oversized-community split + low-cohesion re-split | `cluster.py::_split_community`, cohesion constants | **ADAPT** | Derived |
| 23 | Cohesion metric (intra-cluster density, self-loop safe) | `cluster.py::cohesion_score` | **ADOPT** | Derived |
| 24 | Stable community identity across rebuilds | `cluster.py::remap_communities_to_previous`, `community_member_sigs`, `label_still_applies` | **ADOPT** | Derived |
| 25 | Deterministic LLM-free labelling rule | `cluster.py::label_communities_by_hub` | **ADAPT** | Derived |
| 26 | Near-duplicate detection pipeline (entropy→MinHash/LSH→JW→union-find) | `dedup.py`, `_minhash.py` | **ADAPT** | Derived (surface, never merge thoughts) |
| 27 | Content-hash cache namespaced by producer version | `cache.py::_AST_CACHE_SCHEMA`, `cache_dir` | **ADOPT** | Derived |
| 28 | Stat-signature change detection before hashing | `cache.py::_stat_sig_fresh` | **ADOPT** | Derived |
| 29 | Atomic writes + fail-closed destructive-write guard | `paths.py::write_*_atomic`, `export.py::to_json` guard | **ADOPT** | Both |
| 30 | Pending-change queue so a lockless writer never loses its change set | `watch.py::_queue_pending`, `_drain_pending` | **ADOPT** | Derived |
| 31 | Refusal to merge entities across project boundaries | `dedup.py::deduplicate_entities` cross-repo guard | **ADOPT** | Canonical policy |
| 32 | Hyperedge (n-ary relationship) support | `build.py::_normalize_hyperedge_members`, `attach_hyperedges` | **ADOPT** | Derived (capability is canonical-shaped) |
| 33 | Output sanitization + untrusted-data delimiting + sentinel defanging | `security.py::sanitize_label`, `llm.py` defences | **ADOPT** | Both |
| 34 | SSRF-guarded URL ingestion | `security.py::validate_url`, `safe_fetch` | **STUDY ONLY** | Derived |
| 35 | Opt-in, fail-silent query logging | `querylog.py` | **ADOPT** | Derived |
| 36 | Local-first backend selection, but with inverted defaults | `llm.py::BACKENDS`, README backend order | **ADAPT** | Both |
| 37 | Agent surface: text-under-budget tool contract, read-only tools | `serve.py::list_tools` | **STUDY ONLY** | Derived |
| 38 | Determinism pinned at process entry (hash seed) | `__main__.py::_HASHSEED_PINNED_COMMANDS` | **STUDY ONLY** | Derived |
| 39 | Benchmark methodology (key-fact coverage, judge validation, ablations) | `BENCHMARKS.md` | **STUDY ONLY** | n/a |
| 40 | Canvas export (communities as groups on a spatial surface) | `export.py::to_canvas` | **STUDY ONLY** | Derived |
| 41 | Whole-file JSON as the canonical store | `export.py::to_json`, `paths.py` | **REJECT** | — |
| 42 | AST engine and per-language extractors | `extractors/`, `extract.py` | **REJECT** | — |
| 43 | Node-ID normalization derived from content | `ids.py::normalize_id` | **REJECT** | — |
| 44 | Static bearer token as the only authorisation | `serve.py::_ApiKeyMiddleware` | **REJECT** | — |
| 45 | `project_path` as the only scoping mechanism | `serve.py` tool schemas | **REJECT** | — |
| 46 | Stamp derived values (`community`, `norm_label`) into canonical records | `export.py::to_json` | **REJECT** | — |
| 47 | Array-position relationship direction | `paths.py::restore_arc_direction` workaround | **REJECT** | — |
| 48 | Environment-key-driven cloud backend auto-detection | `llm.py`, README backend order | **REJECT** | — |
| 49 | PR tools, call-flow/tree HTML, wiki export, per-editor installers | `prs.py`, `callflow_html.py`, `tree_html.py`, `wiki.py`, `install.py` | **REJECT** | — |
| 50 | Any agent-facing write path to canonical data | (absent in Graphify — rejected preventively) | **REJECT** | — |
| 51 | Git-hook-driven automatic rebuilds of user data | `hooks.py` | **REJECT** | — |
| 52 | Automatic merging of near-duplicate user thoughts | (adapted from `dedup.py`) | **REJECT** | — |

**Totals: ADOPT 21 · ADAPT 16 · STUDY ONLY 6 · REJECT 12** (counting the four
grouped rows in §1 as their individual members: REJECT 16 rows above collapse to
12 distinct decisions).

---

## 2. ADOPT items — detail

Format per item: *what it does · why IWTT needs it · IWTT use case · layer ·
dependency cost · complexity · license impact · implementation risk*.

### 2.1 Tokenizer + stopwords + CJK segmentation — `serve.py::_search_tokens`, `_query_terms`, `_segment_chinese`, `_QUERY_STOPWORDS`
- **What**: diacritic-folded `[^\W_]+` tokenization with `_` as a separator; question-word removal (English + common German/Romance) with all-stopword fallback; jieba/bigram CJK path keeping the original term for exact matching.
- **Why IWTT needs it**: recall starts with tokenization that matches how people type. The separator unification and stopword fallback are already-debugged behaviours.
- **IWTT use case**: the lexical stage of the Recall pipeline.
- **Layer**: derived. **Dependency cost**: zero (stdlib `re`/`unicodedata`; jieba optional). **Complexity**: low (~60 lines). **License impact**: none if reimplemented; Apache-2.0 NOTICE if copied. **Risk**: low. **Recommendation**: reimplement directly, extend stopword list with IWTT-domain filler ("remember", "thought", "idea" when used as fillers).

### 2.2 IDF term weighting — `serve.py::_compute_idf`
- **What**: `w = log(1 + N/(1+df))` over label matches, cached on the graph object so it dies with a hot reload.
- **Why IWTT needs it**: the single highest-leverage ranking signal for personal corpora, where common words ("project", "game", "note") appear everywhere and rare ones ("enemies learning how I fight") carry the memory.
- **IWTT use case**: down-weight boilerplate vocabulary during recall.
- **Layer**: derived. **Cost**: zero. **Complexity**: low. **License**: none if reimplemented. **Risk**: low — but compute `df` over the *whole* vault, or make it incrementally maintainable. **Recommendation**: adopt, and persist the IDF table alongside the derived index for fast startup.

### 2.3 Trigram candidate prefilter with a "never-worse" fallback — `serve.py::_get_trigram_index`, `_trigram_candidates`, `_trigrams`
- **What**: lazy `trigram → postings` map over each node's searchable text, used only to *narrow* candidates; returns `None` (full scan) when a needle is un-trigrammable or its rarest trigram exceeds 10% of nodes. Results are byte-identical either way.
- **Why IWTT needs it**: keeps recall cheap as the vault grows without ever changing results.
- **IWTT use case**: prefilter for lexical scoring; also naturally reusable for fuzzy/similarity surfacing.
- **Layer**: derived. **Cost**: zero (stdlib `array`). **Complexity**: low–medium (~80 lines incl. guard). **License**: none if reimplemented. **Risk**: low. **Recommendation**: adopt the *guard* as carefully as the index — it is the property that makes the optimisation safe.

### 2.4 Per-term seed guarantee + seed dedupe — `serve.py::_pick_seeds`
- **What**: at least one BFS seed per distinct query term that matched anything (computed inside the same scoring pass), plus dedupe by normalized label and a fully-specified tie-break.
- **Why IWTT needs it**: two generic failures of keyword seeding over personal data — a rare-word hijack dominating the neighbourhood, and duplicate labels flooding the seed slots.
- **IWTT use case**: neighbourhood expansion during recall.
- **Layer**: derived. **Cost**: zero. **Complexity**: low. **License**: none. **Risk**: low. **Recommendation**: adopt; keep the "one seed per matched term" guarantee explicitly, including for temporally-specified queries later.

### 2.5 Hub-suppressed traversal + induced-edge completion — `serve.py::_bfs`, `_dfs`, `_complete_induced_edges`
- **What**: `hub_threshold = max(50, p99(degree))`; hubs are visited but not expanded as transit unless they are seeds; visited-set is completed with induced edges.
- **Why IWTT needs it**: a personal graph has natural super-hubs (a daily-note index, a "misc" tag, an inbox thought, a project node). Without suppression, every query drags the whole vault in.
- **IWTT use case**: "what is near this thought?" expansion for recall and Mind World focus.
- **Layer**: derived. **Cost**: zero. **Complexity**: medium (threshold choice, seed exception, completion). **License**: none. **Risk**: medium — the *threshold rule* must be validated on a real vault; `p99` may be too aggressive for a small vault (hence the `max(50, …)` floor — keep an analogous floor). **Recommendation**: adopt, with a configurable floor and a test that a synthetic hub cannot swallow a query.

### 2.6 Budgeted, ordered, sanitized rendering — `serve.py::_subgraph_to_text`, `_cut_lines_to_budget`
- **What**: `char_budget = tokens × 3`; seeds first, then `(hop distance, −degree, id)`; per-node one-line format; all corpus/AI-derived fields passed through `sanitize_label()`; deterministic truncation with a narrowing hint.
- **Why IWTT needs it**: retrieval is only useful if its output fits a budget, and only trustworthy if untrusted text cannot break the format.
- **IWTT use case**: the returned payload for any assistant, and the "here's what I found" panel.
- **Layer**: derived. **Cost**: zero. **Complexity**: low–medium. **License**: none. **Risk**: low. **Recommendation**: adopt wholesale, and keep the "count optional annotations inside the budget" detail — it is what stops budget overrun.

### 2.7 Ambiguity refusal with rival explanations — `serve.py::_tier_refusal`, `_ambiguity_message`, `_rivals_across_files`, `_no_scoped_match_message`
- **What**: when several nodes match equally, return an explanation of the rivals instead of silently choosing.
- **Why IWTT needs it**: a wrong confident answer about the user's own memory is worse than a question back. This is also the honest behaviour when a remembered phrase genuinely maps to several thoughts.
- **IWTT use case**: recall disambiguation ("I found three thoughts that could match — which one?").
- **Layer**: derived. **Cost**: zero. **Complexity**: low. **License**: none. **Risk**: low. **Recommendation**: adopt, and extend the refusal message to be *actionable* in IWTT terms (dates, lifecycle state, project).

### 2.8 Deterministic tie-breaks end to end — `serve.py` sort keys, `cluster.py` total-order re-index
- **What**: every ranking, selection and id-assignment ends in a total order (`(-score, label_len, nid)`, `(-size, tuple(sorted(ids)))`).
- **Why IWTT needs it**: reproducibility is what makes derived output diffable, testable, and stable enough that the user's mental map does not churn.
- **IWTT use case**: every derived artifact (rankings, cluster ids, suggestions).
- **Layer**: derived. **Cost**: zero. **Complexity**: low (discipline, not code). **License**: none. **Risk**: low. **Recommendation**: adopt as a project-wide rule for derived computation.

### 2.9 Evidence binding: verify AI claims against source, flag never drop — `llm.py::_bind_node_evidence`
- **What**: for AI-produced claims of a particular kind, check that a claimed identifier actually occurs in the source the model saw; if not, mark `verification = "unverified"` — never delete; idempotent; skip already-hedged items; ignore short tokens.
- **Why IWTT needs it**: this is IWTT's AI-suggestion risk, solved in the smallest possible way. It turns an unverifiable AI claim into a labelled one, keeping the audit trail intact.
- **IWTT use case**: any AI-derived title, tag, summary, or suggested relationship: verify against the source thought text, mark unverified if unsupported.
- **Layer**: derived. **Cost**: zero. **Complexity**: low. **License**: none (concept; reimplement). **Risk**: low. **Recommendation**: adopt as a *policy* — "AI output may be flagged, never silently deleted" — and generalise the check beyond substrings (e.g. for embeddings-based suggestions, verify against a retrieved span).

### 2.10 Derived sidecar discipline — `reflect.py::LEARNING_SIDECAR_NAME`, `write_learning_sidecar`, `serve.py` overlay merge
- **What**: the learned layer is written to `.graphify_learning.json` *beside* the graph; the comment states outright that "no learning_* fields are ever stamped into the graph itself"; the query renderer merges it in at display time, inside the output budget.
- **Why IWTT needs it**: it is IWTT's canonical/derived rule, implemented and commented by a third party. This is the strongest structural lesson in the repository.
- **IWTT use case**: AI summaries, embeddings, suggested links, community assignments, scores — all in sidecars keyed by canonical ids.
- **Layer**: derived. **Cost**: zero. **Complexity**: low (but a discipline that must be enforced by tests). **License**: none. **Risk**: low. **Recommendation**: adopt, and add a guard test: "deleting the entire derived directory leaves canonical history intact and rebuildable."

### 2.11 Capped most-recent-first provenance trail — `reflect.py::_provenance_for`, `_PROVENANCE_CAP = 5`
- **What**: per-entity list of `(question, date, outcome)`, most-recent-first, capped, resolved to exactly one canonical id (ambiguous/stale entries skipped), with `last` derived from the newest entry.
- **Why IWTT needs it**: the user must be able to see *why* a derived belief exists, in bounded space.
- **IWTT use case**: "why is this thought promoted / contested / connected?" explanations.
- **Layer**: derived. **Cost**: zero. **Complexity**: low. **License**: none. **Risk**: low. **Recommendation**: adopt; where the trail references a canonical event, store the event id (not a copy) so the canonical log stays the source of truth.

### 2.12 Confidence-scaled clustering — `cluster.py::ambiguous_scale` (+ `GRAPHIFY_AMBIGUOUS_SCALE`)
- **What**: multiply the weight of low-confidence edges (`0.5` default; `0.0` removes them from partitioning only) so uncertain links cannot glue unrelated clusters.
- **Why IWTT needs it**: the Mind World will be full of suggested (unconfirmed) relationships. Letting unconfirmed links shape regions would make the visible world a picture of the AI's guesses rather than the user's thinking. **This is directly applicable and important.**
- **Layer**: derived. **Cost**: zero. **Complexity**: low. **License**: none. **Risk**: low. **Recommendation**: adopt; expose as a user-visible setting ("how much do suggested links shape regions?"), never hard-code silently.

### 2.13 Cohesion metric (self-loop safe) — `cluster.py::cohesion_score`
- **What**: intra-cluster edges ÷ `n(n−1)/2`, explicitly excluding self-loops after a real bug where a recursive self-edge pushed a two-node community's score to 2.0.
- **Why IWTT needs it**: a cheap "is this region actually a family of related thoughts, or a bag?" signal.
- **Layer**: derived. **Cost**: zero. **Complexity**: low. **License**: none. **Risk**: low. **Recommendation**: adopt with the self-loop exclusion (IWTT will have self-referential links, e.g. a thought referencing its own earlier revision).

### 2.14 Stable community identity across rebuilds — `cluster.py::remap_communities_to_previous`, `community_member_sigs`, `label_still_applies`
- **What**: greedy one-to-one overlap matching to reuse old ids; sha256 over sorted members as a membership fingerprint; a saved label survives only if the signature matches or the overlap is a strict majority of both old and new sets (so a one-node fragment cannot inherit a name).
- **Why IWTT needs it**: Mind World regions must feel like *places*. If a rebuild reshuffles ids, any user-facing name or note attached to a region becomes meaningless.
- **IWTT use case**: region identity in the visual world; any user label attached to a region; longitudinal tracking of how clusters evolve.
- **Layer**: derived (ids) + canonical (the user's own region names, attached to a derived id). **Cost**: zero (stdlib hashlib). **Complexity**: medium. **License**: none. **Risk**: medium — the majority rule must be tested against IWTT's sparse graphs, where clusters are small and overlap percentages swing. **Recommendation**: adopt both mechanisms; treat the *rule* as tunable and test it.

### 2.15 Content-hash cache namespaced by producer version — `cache.py::_AST_CACHE_SCHEMA`, `cache_dir`
- **What**: cache keys are file content hashes; the namespace includes package version + cache schema + grammar fingerprint; entries from other namespaces are pruned. The semantic cache is deliberately *not* version-invalidated, with the reason recorded (re-billing an LLM for unchanged files).
- **Why IWTT needs it**: derived data must be invalidated when the *generator* changes, not only when the input changes. This is the missing half of "derived data is rebuildable".
- **IWTT use case**: AI summaries/titles/tags cached per thought revision, invalidated on revision change or generator-version bump.
- **Layer**: derived. **Cost**: zero. **Complexity**: medium. **License**: none. **Risk**: low. **Recommendation**: adopt the naming/versioning scheme verbatim in spirit: `derived/<kind>/v{generator}-s{schema}/`.

### 2.16 Stat-signature short-circuit — `cache.py::_stat_sig_fresh`, `_mtime_granularity_ns`
- **What**: mtime+size signature checked before hashing; mtime granularity probed because filesystems differ.
- **Why IWTT needs it**: re-hashing a large vault on every start is wasted work; the granularity detail is the kind of thing that silently breaks correctness.
- **Layer**: derived. **Cost**: zero. **Complexity**: low. **License**: none. **Risk**: low. **Recommendation**: adopt as a two-stage check (stat, then hash).

### 2.17 Atomic writes + fail-closed destructive-write guard — `paths.py::write_text_atomic`, `write_json_atomic`, `os_replace_with_fallback`; `export.py::to_json` guard
- **What**: crash-safe writes; refuse to overwrite a smaller or semantic-lossy graph; **fail closed** when the existing file is unparseable; `force=True` as the explicit override.
- **Why IWTT needs it**: the canonical store holds irreplaceable user history. "Refuse rather than destroy" is the right default, and the fail-closed detail matters: a corrupt read must not become a licence to overwrite.
- **IWTT use case**: canonical writes (though those should be per-record) and every derived snapshot rewrite.
- **Layer**: both. **Cost**: zero. **Complexity**: low. **License**: none. **Risk**: low. **Recommendation**: adopt, plus a backup-before-protected-overwrite convention (`export.py::backup_if_protected`).

### 2.18 Pending-change queue — `watch.py::_queue_pending`, `_drain_pending`, `_PENDING_DRAIN_MAX_PASSES`
- **What**: a process that cannot take the rebuild lock appends its changed paths to `.pending_changes`; the lock holder drains before and after its rebuild and merges sets; append-mode writes so concurrent writers don't clobber; partial lines are skipped.
- **Why IWTT needs it**: any background indexer must not lose a user's edit because it was busy. This is a small, complete solution to a real concurrency bug.
- **Layer**: derived. **Cost**: zero. **Complexity**: low–medium. **License**: none. **Risk**: low. **Recommendation**: adopt if IWTT gains background indexing.

### 2.19 Refuse to merge across project boundaries — `dedup.py::deduplicate_entities` cross-repo guard
- **What**: raises rather than deduplicating when nodes span multiple repos, because "nodes from different repos share label names by coincidence and must never be merged by string similarity".
- **Why IWTT needs it**: two thoughts in different projects with similar wording are not the same thought. This is a *canonical* policy, not a heuristic.
- **Layer**: canonical policy. **Cost**: zero. **Complexity**: low. **License**: none. **Risk**: low. **Recommendation**: adopt and strengthen: never merge across projects, tags, or lifecycle states without explicit user action.

### 2.20 Hyperedge (n-ary relationship) support — `build.py::_normalize_hyperedge_members`, `_coerce_hyperedge_member_refs`, `attach_hyperedges`; `export.py::attach_hyperedges`
- **What**: relationships with more than two endpoints, member-key variants coerced (`nodes`/`members`/`node_ids`), persisted dual-slot, members rewired when entities merge.
- **Why IWTT needs it**: "these five thoughts together produced this project", "this is one idea family", "this experiment came from these three notes" are genuinely n-ary and get distorted by pairwise edges.
- **IWTT use case**: experiments/inventions/projects drawing on multiple thoughts; idea families; regional membership.
- **Layer**: derived when algorithmic; the underlying *capability* should exist in the canonical model so user-authored groupings can be n-ary too. **Cost**: zero. **Complexity**: medium. **License**: none. **Risk**: medium — every consumer (rendering, clustering, recall) must handle n-ary structures, which is real design work. **Recommendation**: adopt the capability; keep algorithmic hyperedges derived.

### 2.21 Output sanitization + untrusted-data delimiting — `security.py::sanitize_label`, `llm.py` defences
- **What**: strip control chars and cap length on every derived field entering agent context; wrap inbound source text in hash-stamped untrusted-data delimiters; defang chat-template jailbreak sentinels; document the limits honestly.
- **Why IWTT needs it**: IWTT will ingest external documents and (eventually) expose a graph to assistants. Both directions are attack surfaces, and a private vault raises the stakes.
- **Layer**: both. **Cost**: zero. **Complexity**: low (sanitize) to medium (full injection posture). **License**: none. **Risk**: low. **Recommendation**: adopt at minimum the sanitization of anything rendered to an assistant; adopt the delimiter/sentinel approach if external content is ingested.

### 2.22 Opt-in, fail-silent query logging — `querylog.py`
- **What**: off unless explicitly enabled; a disable switch that wins; records question/corpus/counts/duration; full responses only behind a second flag; never raises.
- **Why IWTT needs it**: debugging recall needs query history; a private vault forbids logging by default.
- **Layer**: derived. **Cost**: zero. **Complexity**: low. **License**: none. **Risk**: low. **Recommendation**: adopt the default-off policy, but place any IWTT log **inside the vault**, visible and deletable in-app.

---

## 3. ADAPT items — the essential deltas

| Item | Graphify's version | Required IWTT delta |
|---|---|---|
| Tiered scorer (#2) | weights exact 1000 / prefix 100 / substring 1 | IWTT labels are prose sentences: exact-match dominance would let a literal phrase-quote swamp all semantically-relevant thoughts. Re-derive weights against a real vault; keep tiers, change magnitudes. Must be measured, not guessed |
| Coverage scaling (#4) | `(matched/n_terms)**2` | Re-validate the exponent for natural-language queries; consider also scaling by *term specificity* so a multi-term match of generic words does not outrank a single rare term |
| Provenance vocabulary (#12) | 3-value enum on edges; `verification` flag on nodes | Split into orthogonal fields: assertion (user_asserted/user_confirmed/user_rejected), suggestion source+method+score+evidence, verification state. Canonical fields user-owned; AI fields derived and rebuildable |
| Corroboration + decay (#14) | half-life 30 days; min corroboration 2 | Personal thoughts decay much slower than code facts. Start ~180 days (**ESTIMATED**); make it configurable; "corrected" for a thought means something different than for a code answer — it must reference the revision history, not overwrite text |
| Outcome signals (#17) | `useful`/`dead_end`/`corrected` on saved answers | Map to IWTT events (thought revisited / abandoned / resurrected / linked / revised) recorded in the canonical event log; scores computed as derived |
| Explanation generation (#18) | prose `why` strings at analyse time | Store structured reason codes + evidence refs; render prose in the UI. Prose alone cannot be audited or re-rendered in another language |
| Leiden clustering (#19) | fixed seed 42, resolution 1.0, randomness 0.001 | Same algorithm family; expose resolution as a **user-facing zoom/tightness control**; consider hierarchical/multi-resolution output for Mind World zoom (see future-architecture §5) |
| Hub exclusion (#21) | `exclude_hubs_percentile` + majority reattachment | IWTT hubs are inbox/daily/project nodes — exclusion is right, but "stranded node" handling needs testing on sparse personal graphs where a node's only neighbour *is* a hub |
| Oversized/low-cohesion split (#22) | >25% of graph; cohesion <0.05 **and** ≥50 nodes | Thresholds are calibrated for code graphs with a doc-hub problem. For personal vaults, tune both; the *mechanisms* (split oversized; re-split weak) are right |
| LLM-free labelling (#25) | highest-degree member names the cluster | Keep determinism and no-model; change the rule — highest-degree in a thought graph is often an index-like note. Candidate rules: highest *betweenness* member that is not a hub; most-connected *user-authored* thought; most recent active thought; or a user-typed name attached to the derived id |
| Near-duplicate detection (#26) | entropy gate 2.5 → MinHash/LSH 0.7 (128 perms) → Jaro-Winkler 92.0 (+5 same-community) → union-find merge | **Core delta: never merge.** Use the machinery to *surface* near-duplicates ("possible duplicate / possible evolution") as a derived suggestion. Merging user thoughts would destroy revision and graveyard history. Also: personal prose has different entropy/similarity distributions than identifiers — recalibrate thresholds |
| Local-first backends (#36) | auto-detect from env keys, cloud priority first, Ollama last | Invert: local-only by default; remote requires explicit per-vault opt-in; show exactly what would be sent before sending; never infer consent from an unrelated exported API key |

---

## 4. STUDY ONLY items

| Item | Why study, not adopt |
|---|---|
| SSRF-guarded URL ingestion (#34) | Reusable *only if* IWTT ingests URLs. Keep the threat model (private/loopback/metadata blocking, redirect re-validation, size caps, non-2xx raising) in the design vocabulary |
| Agent tool contract (#37) | The text-under-budget, read-only tool shape is the right starting point, but IWTT's agent surface needs grants, redaction and audit first (`AGENT_INTEGRATION.md` §5). Do not build a server before the permission model exists |
| Determinism at process entry (#38) | Elegant, but only relevant if derived output must be byte-identical across processes. Adopt the *habit* (stable sort keys) rather than the mechanism |
| Benchmark methodology (#39) | Key-fact coverage grading (`(covered + 0.5·partial)/total`), judge validation against a second judge (κ 0.81), ablations (seed-only), and a spend ledger are excellent practices for evaluating IWTT recall. The harness itself is not published, so this is methodology transfer, not code transfer |
| Canvas/spatial export (#40) | Communities-as-groups on a spatial surface is a useful precedent for Mind World, but the layout is a simple grid. Treat as inspiration for the *view contract* (groups, cards, edges), not as an implementation |
| Declarative parser registry (#31 → `LanguageConfig`) | The pattern — a declarative descriptor per source type, one interpreter — is the right shape for any future multi-format external-source ingestion (PDF, HTML, docx, repo). Worth remembering; not needed now |

---

## 5. REJECT items

Grouped here; full rationale in `REJECTED_COMPONENTS.md`.

| Group | Members |
|---|---|
| Code-specific AST machinery | tree-sitter engine, `LanguageConfig`, 37 extractors, cross-file resolution, `indirect_call`, AST cache schema, content-derived node IDs, external stub minting |
| Storage/architecture incompatible with IWTT | whole-file JSON as canonical store; whole-graph-in-memory as an assumption; stamping derived values into canonical records; array-position direction; line numbers as primary anchors; `built_at_commit` |
| Coupled to Graphify's product surface | PR tools; call-flow/tree HTML; wiki export that deletes `wiki/*.md`; 16 skill bodies; per-editor installers; git-hook-driven rebuilds |
| Privacy/permission models incompatible with a private vault | env-key cloud auto-detection; static shared bearer token as the only auth; `project_path` as content scoping; communities as privacy boundaries; logging off by default with no per-agent attribution |
| Semantic corruption of the canonical model | AI-extracted concepts promoted to first-class entities; automatic promotion of suggestions into relationships; automatic merging of near-duplicate thoughts; using centrality to define meaning; persisting layout as truth |

---

## 6. Reading the totals

21 ADOPT · 16 ADAPT · 6 STUDY ONLY · 12 REJECT decisions.

Two observations from the shape of that distribution:

1. **The most valuable transfers are small.** Tokenization, prefilters, seed
   guarantees, budgeted rendering, sidecar discipline, atomic writes, cache
   namespacing, corroboration gating — all are tens-to-hundreds of lines, not
   thousands. The large subsystems (AST engine, LLM layer, installers) are the
   ones IWTT does not want.
2. **The ADAPT set is where the thinking is required.** Every item there is
   "right mechanism, wrong calibration" — and every one is calibrated on code
   graphs. A later mission must re-derive and *measure* these against a real
   IWTT vault rather than importing constants.

---

## 7. Evidence anchors

Per-item locations are given inline. Cross-cutting evidence:
`serve.py` (all retrieval items), `cluster.py` (all clustering items),
`reflect.py` (corroboration, decay, sidecar, provenance trail),
`llm.py::_bind_node_evidence` (evidence binding), `cache.py` (cache namespacing,
stat signatures), `paths.py` + `export.py::to_json` (atomic writes, guards,
derived-field stamping), `watch.py` (pending queue), `dedup.py` + `_minhash.py`
(near-duplicate pipeline, cross-repo guard), `build.py`
(hyperedges, stub minting), `security.py` + `ingest.py` (sanitization, SSRF),
`querylog.py` (query logging), `validate.py` (closed vocabularies),
`__main__.py` (hash-seed pinning), `BENCHMARKS.md` (methodology),
`export.py::to_canvas` (spatial export), `REJECTED_COMPONENTS.md` (rejection detail).
