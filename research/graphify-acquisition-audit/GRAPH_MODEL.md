# GRAPH_MODEL.md

**Audit 2 — Graph model.** How Graphify represents nodes, edges, types, metadata,
provenance, confidence and explanations. Verified against the implementation at
commit `10130e79ed9caf8f60d998073e097531eaa1a395`.

---

## 1. The storage model

Graphify's durable artifact is one JSON document, `graphify-out/graph.json`,
written in NetworkX node-link form. It is loaded into an in-memory NetworkX
`Graph` by the query path.

```
graph.json
├── directed: false              ← but see "direction" below
├── multigraph: false
├── graph: { ... }               ← graph-level attrs (hyperedges, learning overlay, …)
├── nodes: [ {...}, ... ]
└── links: [ {...}, ... ]        ← "links" or "edges" depending on writer version
```

**Direction ambiguity is a documented landmine.** `graph.json` declares
`"directed": false`, yet every link is semantically directed (`source` →
`target`), and *arc order carries the direction*. Reloading through a plain
`json_graph.node_link_graph()` can re-order edge endpoints by node-list position
and silently destroy direction; legacy files instead carry `_src`/`_tgt` markers,
which win where present. The supported load path is
`paths.load_node_link_graph()` / `paths.restore_arc_direction()`.

**For IWTT:** this is a real, transferable warning about representing directed
relationships in an undirected container. IWTT's canonical store should never rely
on array position to encode direction — put direction in a field, always.

## 2. Node model

Required fields (`validate.py::REQUIRED_NODE_FIELDS`):
`{id, label, file_type, source_file}`.

`file_type ∈ {code, document, paper, image, rationale, concept}`
(`VALID_FILE_TYPES`). **`rationale` and `concept` are first-class node types** —
interesting, because they are not files at all.

Observed optional/extended fields on nodes across the codebase:

| Field | Purpose |
|---|---|
| `source_location` | `L<line>` (extractors) — the evidence anchor |
| `attributes` | Arbitrary key/value bag; flattened, tokenized, and searched by the query layer |
| `rationale` | Prose "why this exists", searched at its own scoring tier |
| `norm_label` | Diacritic-folded lowercase label, **materialized at export** and used by IDF/trigram scoring |
| `community`, `community_name` | Written onto nodes at export time from the clustering result |
| `file_type` | Routing/type discriminator |
| `verification` | `"unverified"` flag set by the LLM evidence-binder (see PROVENANCE.md) |
| `_callable`, `_callable_class`, `_py_property`, `type_path`, `repo` | Resolver/marker metadata produced by extractors for later passes |

Note the *mixed durability* of these: `norm_label` and `community` are derived
values stamped into the same document as the structural truth. They are
rebuildable, and the build process rewrites them, but nothing structurally
prevents a stale derived value from being read as fact.

**For IWTT:** the `rationale` node/attribute pattern is the closest thing in
Graphify to "why do I believe this?" and is worth adopting as a *derived*
annotation on thoughts and relationships — never as canonical text.

## 3. Edge model

Required fields (`REQUIRED_EDGE_FIELDS`):
`{source, target, relation, confidence, source_file}`.

Canonical emitted shape (`extractors/engine.py::_semantic_reference_edge`):

```python
{
  "source": ..., "target": ...,
  "relation": "references",
  "context": <validated against REFERENCE_CONTEXTS>,
  "confidence": "EXTRACTED",
  "source_file": ...,
  "source_location": "L<n>",
  "weight": 1.0,
}
```

`context` is a **closed vocabulary** — an unknown value raises rather than being
stored, so downstream code can switch on it safely. Observed contexts include
`call`, `field`, `map`.

`confidence_score` appears on inferred relations (0.85 for `indirect_call`) but is
not universally present.

### Relation vocabulary (observed)

`references`, `calls`, `indirect_call`, `imports`, `imports_from`, `inherits`,
`uses`, `contains`, `related_to`, plus extractor-specific relations
(`declares`, `implements`, decorator/annotation references, etc.). Relation names
are **free strings** per extractor — only `context` and `confidence` are closed
vocabularies.

### Hyperedges

N-ary relationships are supported as separate objects (`{nodes: [id, …], …}`)
persisted dual-slot: a top-level `hyperedges` key *and* nested under
`graph.hyperedges`, with member keys coerced from `nodes` / `members` /
`node_ids`. Members that are not hashable scalars are dropped with a warning
rather than crashing the build.

**For IWTT:** hyperedges are the right container for "this set of thoughts,
together, produced this project" or "this cluster of notes is one idea family" —
a relationship that is genuinely n-ary and would be distorted by pairwise edges.
That is a *derived/analytical* structure in IWTT terms; the canonical model should
still be able to express it, but nothing algorithmic should mint one silently.

## 4. Are relationships explicit, inferred, heuristic, deterministic, or AI-generated?

All five, and Graphify distinguishes them **only by a three-value enum**:

| Confidence | Assigned when | Producer |
|---|---|---|
| `EXTRACTED` | Statement is explicit in the source (`import x`, a direct call, a syntactic reference) | deterministic parser |
| `INFERRED` | Reasonable deduction: cross-file resolution, second-pass call graph, `indirect_call` | deterministic resolver *or* LLM semantic pass |
| `AMBIGUOUS` | Uncertain; flagged for human review in `GRAPH_REPORT.md` | resolver or LLM |

Critical observation: **`INFERRED` is overloaded.** It covers both
"deterministic resolver concluded this" and "an LLM proposed this". A consumer
cannot tell from the confidence value alone whether an edge came from code logic
or from a model's guess — though `source_file`/`source_location` and the
`context` tag give partial signals, and the node's `verification="unverified"`
flag covers one LLM fabrication case.

**For IWTT this is the single most important gap.** IWTT requires a first-class
distinction between USER_CONFIRMED and AI_SUGGESTED relationships. A three-value
enum with an overloaded middle cannot express:
user-asserted vs user-confirmed-AI-suggestion vs AI-suggested-unreviewed vs
algorithmically-inferred. The *pattern* to take is "closed vocabulary attached to
every relationship"; the *vocabulary* must be redesigned for IWTT (see
`PROVENANCE.md` §5 and the matrix).

## 5. Metadata and source attribution

- Every required edge carries `source_file`; most carry `source_location` (`L42`).
  That is the *evidence anchor*: where the claim came from.
- Builds record the git commit they were built at (`export._git_head()`,
  `built_at_commit`).
- `repo` tags support multi-repo graphs; cross-repo dedup is actively refused
  (`deduplicate_entities` raises when nodes span repos) — a deliberately
  conservative guard against merging coincidentally-equal names across projects.

**For IWTT:** "refuse to merge across context boundaries unless explicitly told
to" is a good default for a personal system holding several projects. Two
thoughts in different projects with similar wording are not necessarily the same
thought.

## 6. How Graphify explains *why* an edge exists

Short answer: **mostly it doesn't store an explanation — it generates one.**

- Stored on the edge: `relation` (what), `confidence` (how sure), `context`
  (the syntactic situation), `source_file` + `source_location` (where), `weight`,
  and occasionally `confidence_score` (how much).
- Stored **nowhere**: a human-readable reason, a derivation chain, a pointer to
  the user statement that justified it.
- Generated at analysis time by `analyze.py`: `why` strings such as
  `"Edge tagged AMBIGUOUS (relation: {relation}) — confidence is low."`,
  `"High betweenness centrality ({score:.3f}) — this node is a cross-community bridge."`,
  `"{label} has {n} INFERRED edges — model-reasoned connections that need verification."`,
  and `"; ".join(reasons)` for surprising connections.
- One genuine evidence mechanism exists: `llm.py::_bind_node_evidence()` verifies
  that a symbol the model asserted actually occurs in the source text it was
  shown, and marks the node `verification="unverified"` when it does not. It
  **flags, never drops** — explicitly "a reversible flag".

Package-wide grep confirms the sparsity: `"provenance"` 4 hits (all in
`reflect.py`), `"evidence"` 1 hit (a fallback key in the HTML exporter),
`"extractor"` 1, `"origin"` 1.

**Transferable pattern (strong):** confidence + location + context + optional
numeric score + a *separate* verification flag, all closed-vocabulary, plus
"demote, never delete" for suspect AI output.

**Transferable pattern (also strong):** explanations as a *renderable view*
computed on demand, rather than duplicated into every record. Combined with a
provenance trail (which Graphify does have for memory citations — see
`reflect.py::_provenance_for()`, `(question, date, outcome)` capped at 5), this
gives IWTT a clean split: canonical facts in the store, prose explanations in the UI.

## 7. Model separation: what IWTT should copy, and what it must not

**Copy:**
- Closed vocabularies for type, confidence, and context; validation *before* build.
- Every relationship carries a location/source anchor.
- Derived attributes (norm labels, communities, layout) are recomputed, never trusted.
- Sidecar files for derived layers, never sprinkled into canonical records.
- N-ary (hyperedge) relationships as a supported shape.
- Refusal to merge entities across project/context boundaries without explicit intent.

**Do not copy:**
- A single `confidence` enum mixing provenance *kind* with confidence *degree*.
  IWTT needs orthogonal fields (who asserted it, whether the human confirmed it,
  how strong the algorithm's evidence was).
- Stamping derived values (`norm_label`, `community`) into the same flat node
  record as canonical facts, with no namespace separating them.
- Free-string `relation` vocabularies per producer.
- Position-based direction encoding.

## 8. Evidence anchors

`graphify/validate.py` (`VALID_FILE_TYPES`, `VALID_CONFIDENCES`,
`REQUIRED_*_FIELDS`) · `graphify/extractors/engine.py`
(`_semantic_reference_edge`, `_source_location`, `indirect_call` dict) ·
`graphify/paths.py` (`restore_arc_direction`, `load_node_link_graph`) ·
`graphify/build.py` (`build_from_json`, `dedupe_edges`, `_normalize_hyperedge_members`,
`mint_external_stubs_in_data`) · `graphify/export.py` (`to_json` node
post-processing: `community`, `community_name`, `norm_label`) ·
`graphify/serve.py` (`_node_rationale_text`, `_node_attributes_text`) ·
`graphify/ids.py` (`normalize_id`) · `graphify/llm.py` (`_bind_node_evidence`) ·
`graphify/analyze.py` (`why` string sites) · `graphify/dedup.py`
(cross-repo guard) · `ARCHITECTURE.md` (confidence table, arc-direction note).
