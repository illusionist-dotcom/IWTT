# REJECTED_COMPONENTS.md

**Audit 12 — What NOT to take.** The explicit rejection list, with reasons.

The mission frame: *We are NOT trying to turn IWTT into Graphify.* Everything
here is rejected for a stated reason, so a later mission cannot quietly
reintroduce it.

---

## 1. Code-specific AST machinery

| Rejected | Where it lives | Why |
|---|---|---|
| Tree-sitter parsing engine, `LanguageConfig`, per-language node types | `graphify/extractors/engine.py` (10,054 LOC), `models.py` | A thought is authored, not parsed. There is no grammar for "I wonder whether enemies learn how I fight." Putting a parser between the user and their thought corrupts the canonical model |
| 37 per-language extractors + 20 inline `extract_*` functions | `graphify/extractors/*`, `extract.py` | Zero relevance to personal thoughts; enormous maintenance surface |
| Cross-file symbol resolution, receiver-type tables, `indirect_call`, import resolution | `extractors/resolution.py` (4,232 LOC), `symbol_resolution.py`, `interface_dispatch.py`, `*_dispatch.py` | Answers questions about *programs*. If IWTT ever ingests code, it should consume an external index (SCIP/LSP/Graphify) rather than own this logic |
| AST cache schema versioning tied to grammar versions | `cache.py::_AST_CACHE_SCHEMA = 16` | Meaningless outside the tree-sitter stack |
| Node-ID normalization recipe (casefold→NFKC fixpoint→`[^\w]+ → _`) | `ids.py::normalize_id`, `make_id` | Designed to reconcile three *machine* producers of symbol ids. IWTT thought ids must be opaque, stable, and never derived from content — a content-derived id would change when a thought is edited |
| External stub minting for out-of-corpus imports | `build.py::mint_external_stubs_in_data` | Solves dangling edges in a bounded scan. IWTT's "external knowledge" belongs in an explicit namespace with its own provenance, not as auto-created stubs |
| Type-hierarchy / interface / receiver resolution edge types | `interface_dispatch.py`, `csharp_dispatch.py`, `swift_dispatch.py`, `ruby_resolution.py`, `pascal_resolution.py` | Language semantics, not idea semantics |
| `slnx`/`csproj`/Lazarus project-file extractors | `extract.py` | Build-system parsing |

**Boundary statement:** none of this enters the canonical Thought model. If a
future IWTT ingests repositories, it does so as a separate, optional,
*external-source* subsystem (see `IWTT_GRAPHIFY_FUTURE_ARCHITECTURE.md`), with its
own namespaces and provenance, and with code extraction performed by an external
tool where possible.

## 2. Retrieval tuned for identifiers

| Rejected | Where it lives | Why |
|---|---|---|
| The literal tier weights (exact 1000 / prefix 100 / substring 1.0) | `serve.py` constants | Calibrated for short symbol labels (`FooBarService`) where an exact match is decisive. IWTT labels are prose sentences; applying these weights would make a thought that literally contains a remembered phrase dominate every semantically-relevant thought. **Take the structure, re-derive the weights, and test them against a real vault** |
| Coverage scaling `(matched/n_terms)**2` as-is | `serve.py::_score_query` | The *idea* (a single generic hit must not bury multi-term matches) is good; the exponent was tuned for identifier queries and must be re-validated for natural-language recall |
| `_reason ... code-symbol resolution paths` — `_resolve_python_qualified`, `_resolve_path_scoped_symbol`, `_find_node_tiers` label/file-tier logic | `serve.py` | Disambiguates `module.Class.method` style queries. Irrelevant to thoughts |
| `context_filter` semantics (edge-context tags like `call`/`field`) | `serve.py` tool schema | Tied to the code edge vocabulary. IWTT needs its own filter vocabulary (lifecycle state, project, time, provenance kind) |
| Language-scope assumptions in `_is_searchable` (>2 ASCII chars) | `serve.py` | Reasonable for identifiers; IWTT should keep stopword-aware behaviour but treat 2-character content words as legitimate |
| `norm_label` precomputed for symbol matching | `export.py::to_json` | Fine as derived data, but as a *field stamped into the canonical document* it is exactly the canonical/derived confusion IWTT must avoid |

## 3. Unnecessary dependencies

| Rejected | Pulled by | Why reject for IWTT |
|---|---|---|
| `tree-sitter` + ~28 grammar wheels | core deps | Only needed for AST parsing (rejected §1) |
| `mcp` + `starlette` | `mcp` extra | Only needed for an MCP server. IWTT's agent surface is a later, separate decision; adopt only if that path is chosen |
| `neo4j`, `falkordb` | extras | Graph databases. IWTT is local-first and small; a server-side DB contradicts the premise |
| `psycopg[binary]` (**LGPL-3.0-only**) | `postgres` extra | Copyleft exposure for a capability (live DDL introspection) IWTT does not need |
| `matplotlib` + `pillow` | `svg` extra | Heavyweight rendering chain for SVG export. IWTT's visual world should render client-side (Canvas/WebGL/SVG written directly), not through a plotting library |
| `boto3`, `anthropic`, `openai`, `yt-dlp`, `faster-whisper`, `jieba` | various extras | Each is a *decision*, not an inherited dependency. In particular `yt-dlp` (network fetching) and cloud LLM clients are incompatible with a strict local-first default |
| `graspologic` (full package) | `leiden` extra | Its import chain (umap → pynndescent → numba) costs 7–19 s of import for a layout algorithm the cluster path never calls. Graphify itself works around this by calling `graspologic_native` directly — IWTT should depend only on the native extension, or implement Louvain itself |
| `nuitka`, `patchelf`, `bandit`, `pip-audit`, `hypothesis`, `pyright`, `pre-commit` | dev group | Graphify's release pipeline, not IWTT's |

## 4. Architecture that would violate local-first principles

| Rejected | Why |
|---|---|
| Any hosted/cloud component (the shape of `app.graphify.com`) | IWTT's premise is that the user's thoughts do not leave the machine. A remote "always-on" layer over a private vault is out of scope, not merely unimplemented |
| Environment-key-driven backend auto-detection | Detecting `OPENAI_API_KEY` and silently routing semantic work to a cloud provider is exactly the privacy failure mode IWTT must prevent. Local must be the default; remote must be explicit and per-vault |
| Content sent for semantic extraction without an explicit, per-item user decision | Even with a local model, "extract semantics from every thought automatically" is a different product than IWTT. Any AI pass over canonical thoughts must be opt-in, scoped, and write only to derived layers |
| Multi-repo "global graph" merging (the vendor's cross-repo direction) | IWTT is personal and singular. Merging corpora by string similarity is explicitly guarded against in Graphify (`deduplicate_entities` raises across repos) — that guard should be *strengthened* in IWTT, not relaxed |
| Git-hook-driven automatic rebuilds of user data | Graphify's hooks make sense for a code graph in a repo. An IWTT vault is not a git working tree in general, and automatic background rewrites of derived data must be user-controlled |

## 5. Features tightly coupled to Graphify

| Rejected | Where | Why |
|---|---|---|
| PR integration (`list_prs`, `get_pr_impact`, `triage_prs`) | `serve.py`, `prs.py` | Depends on the `gh` CLI, GitHub, and a code-review workflow |
| Call-flow diagrams, file-tree HTML | `callflow_html.py` (2,051 LOC), `tree_html.py` | Code-structure visualizations |
| Per-community wiki export | `wiki.py` | Useful idea in principle, but the implementation is code/community-shaped and deletes `wiki/*.md` on each run — a destructive-by-default export model that clashes with "never clobber user data" |
| Per-host skill bodies (16 files, ~700 KB of prompts) | `skill*.md`, `skills/` | Graphify's product surface is "tell Claude Code to prefer the graph over grep". IWTT's assistant integration must be its own product decision with its own permission model (see `AGENT_INTEGRATION.md` §5) |
| `install.py` per-editor installers (2,779 LOC) | `install.py` | Editor-integration machinery with no IWTT use case |
| SCIP / PostgreSQL / Cargo introspection | `scip_ingest.py`, `pg_introspect.py`, `cargo_introspect.py` | Code-intelligence sources |
| Video/audio transcription pipeline | `transcribe.py`, `video` extra | An ingestion convenience with a large dependency (`faster-whisper`, `yt-dlp`); a separate decision if IWTT ever wants it |

## 6. Storage choices incompatible with IWTT

| Rejected | Why |
|---|---|
| A single whole-file JSON document as the canonical store | Requires rewriting everything to change one record; a single point of corruption; and makes every build a full-file diff. Canonical IWTT data must be written per-record and append-friendly |
| Whole-graph-in-memory with no incremental clustering | Acceptable at IWTT's realistic scale (measured: 10k nodes → 3 s rebuild), but the *pattern* must not be assumed to scale. Design canonical storage so that the derived projection can be rebuilt incrementally if the vault ever grows |
| Stamping derived values (`norm_label`, `community`, `community_name`) into the same flat record as canonical fields | This is the canonical/derived separation violating itself. In IWTT these belong in sidecars keyed by canonical ids |
| Relying on array position to encode relationship direction | Documented landmine in `graph.json`; IWTT must store direction in a field |
| Line numbers (`L42`) as the primary evidence anchor | An IWTT anchor is a thought revision id. Line offsets are meaningful only for imported external files |
| `built_at_commit` as build provenance | Replace with a vault checkpoint/revision id; an IWTT vault may not be a git repo at all |

## 7. Graph concepts that would corrupt canonical/derived separation

| Rejected | Why |
|---|---|
| Treating algorithmically-derived clusters as places the user has "visited" or "lives in" | Mind World regions are a *view*. If region identity were canonical, a change to the clustering algorithm would rewrite the user's mental map. Region ids may be stored as derived annotations; any name the user gives a region is a separate canonical annotation attached to a derived id |
| Auto-creating "concept" nodes from AI extraction | Graphify's `concept` file_type is reasonable for code docs; in IWTT an AI-extracted concept silently becoming a first-class entity is precisely the contamination to prevent |
| Promoting suggestions into relationships on a schedule | Promotion must be user-visible and reversible; the corroboration-gated promotion in `reflect.py` is a good *mechanism*, but for IWTT a suggestion should become a relationship only through an explicit user action (or an explicit, configured auto-promotion the user chose) |
| Merging near-duplicate thoughts automatically | Graphify's dedup pipeline is for machine-extracted entities that duplicate by accident. Two similar *user thoughts* are not duplicates — they may be an evolution, a contradiction, or a deliberately separate thread. IWTT should surface similarity, never merge silently. Graveyard/resurrection history makes silent merging destructive |
| Using centrality/degree to decide what a thought "means" | Centrality is navigation and suggestion material only (see `SEARCH_AND_QUERY.md` §4) |
| Persisting graph layout as a stored node attribute | Layout is a pure view; storing it invites treating it as truth. Graphify gets this right (layout is client-side only) — keep that property |

## 8. Agent permissions that are too broad

| Rejected | Why |
|---|---|
| A single static bearer token granting full graph read | Answers "may you connect?", not "may you read this?". Unacceptable over a private vault |
| `project_path` as the only scoping mechanism | Scopes *which graph*, not *which content*. Multi-project ≠ multi-tenant |
| Read-verbatim as the default for any in-scope item | An assistant rarely needs the exact private wording to help with "what was I thinking about X?" — titles, rationales, and derived summaries usually suffice. Verbatim is a distinct, explicit capability |
| Using algorithmic communities as privacy boundaries | Communities are clustering artifacts, not user-meaningful access boundaries |
| Query logging off by default with no per-agent attribution | For a private vault, an access log should be available, local, user-visible, and attributable to a specific agent grant |
| Any agent-facing write path to canonical data | Agents may propose; only the user commits. Canonical history must never be edited by an automated caller |

## 9. The one-line summary

**Take the retrieval skeleton, the provenance vocabulary, the corroboration/decay
mechanism, the derived-sidecar discipline, and the determinism habits. Leave the
code intelligence, the dependency tree, the single-file storage, the all-or-nothing
permission model, and anything that would let an algorithm write into canonical
history.**
