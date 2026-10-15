# CLUSTERING.md

**Audit 5 — Clustering / communities.** Verified against `graphify/cluster.py`
(507 LOC) at commit `10130e79ed9caf8f60d998073e097531eaa1a395`.

---

## 1. Inputs

| Input | Source | Notes |
|---|---|---|
| `G` | NetworkX `Graph` or `DiGraph` | Directed graphs are converted to undirected internally — Leiden/Louvain are undirected algorithms |
| `resolution` | parameter, default `1.0` | `>1.0` → more, smaller communities; `<1.0` → fewer, larger |
| `exclude_hubs_percentile` | parameter, default `None` | Degree percentile above which nodes are excluded from partitioning (#919) |
| `ambiguous_scale` | parameter, default `0.5`; env `GRAPHIFY_AMBIGUOUS_SCALE` | Multiplier applied to `weight` of edges whose `confidence == "AMBIGUOUS"` (#4199) |
| Edge `weight` | extraction | Defaults to `1.0` |
| Edge `confidence` | extraction | Only `AMBIGUOUS` is treated specially |

The `ambiguous_scale` parameter is the most interesting input for IWTT: **edge
confidence is fed back into community structure**, so weakly-attested
relationships cannot glue unrelated clusters together. At `0.5` (default) an
ambiguous edge contributes half the modularity weight; `0.0` drops it from
partitioning entirely while leaving it visible in the graph and in
`path`/`explain` output.

## 2. Algorithm

Exactly as implemented (`_partition` and `_native_leiden`):

1. **Normalize to a stable undirected graph.** Nodes added in `sorted(key=str)`
   order; edge rows sorted by `(min(endpoint), max(endpoint), json-encoded attrs)`.
   The comment records the concrete bug: edge orientation from adjacency iteration
   follows CPython's per-process string-hash order, so sorting on the raw `(u, v)`
   pair canonicalizes nothing — measured as 70 vs 69 communities on the same
   914-node graph under `PYTHONHASHSEED=1` vs `2`.
2. **Try `graspologic_native.leiden` directly** (bypassing the `graspologic`
   package, whose import pulls in `graspologic.layouts → umap → pynndescent →
   numba`, measured by the authors at 7–19 s of import cost for a layout algorithm
   this path never calls). Parameters: `starting_communities=None`,
   `resolution`, `randomness=0.001`, `iterations=1`, `use_modularity=True`,
   `seed=42`, `trials=1`. Returns `None` (falling through) if the extension is
   absent or the graph shape is unsupported.
3. **Fall back to `graspologic.partition.leiden`**, introspecting its signature
   for `random_seed` / `trials` / `resolution` to stay compatible across versions.
4. **Fall back to `nx.community.louvain_communities(seed=42, threshold=1e-4,
   resolution=..., max_level=10)`** (networkx ≥2.7; `max_level` guarded by
   signature inspection because it prevents hangs on large sparse graphs).
5. **Isolate handling.** Leiden and Louvain drop isolates; Graphify removes them
   first and gives each its own single-node community.
6. **Hub exclusion and reattachment.** When `exclude_hubs_percentile` is set, hubs
   are held out of the partition, then reattached to the community holding the
   majority of their neighbours; ties break by *smallest* community id. Nodes
   whose only neighbours are hubs ("stranded") are held out too, then placed by
   the same majority vote once hubs are placed.
7. **Oversized-community split.** `max_size = max(10, 0.25 × node_count)`; any
   community larger than that is re-partitioned by a second Leiden/Louvain pass on
   its subgraph (edge-free subgraphs split into singletons).
8. **Low-cohesion re-split.** Communities with ≥50 members and
   `cohesion_score < 0.05` are re-split — the documented cause being "doc-hub
   nodes that bridge otherwise-unrelated subsystems (e.g. CLAUDE.md connected to
   everything)".
9. **Stable re-indexing.** `final_communities.sort(key=lambda nodes: (-len(nodes),
   tuple(sorted(map(str, nodes)))))` then enumerated. Community id 0 = largest.
   The tuple tie-break makes this a **total order**, so hundreds of equal-sized
   communities cannot permute between runs (#1090 follow-up).

## 3. Outputs

- `cluster(G, ...) -> {community_id: [sorted node_ids]}` — the graph itself is
  never mutated.
- `score_all(G, communities) -> {cid: cohesion}` via `cohesion_score(G, nodes)` =
  actual intra-community edges ÷ `n(n−1)/2`, with self-loops excluded. (Comment
  records a real bug: recursive `calls` self-edges were kept by the builder, and
  a self-loop added to the numerator without adding to the denominator, pushing a
  two-node community's cohesion to `2.0`.)
- `label_communities_by_hub(G, communities) -> {cid: name}` — the **default,
  LLM-free** labeller: name the community after its highest-degree member, ties
  broken by node id ascending, with a `Community {cid}` fallback. An optional LLM
  naming pass can override it via `.graphify_labels.json`.
- `community_member_sigs(communities) -> {cid: sha256(sorted ids)[:16]}`.
- Persisted onto nodes at export: `community` (int) and `community_name` (str).

## 4. Determinism

| Mechanism | Purpose |
|---|---|
| Pinned `seed=42` in every engine path | same input → same partition |
| Sorted node insertion + canonicalized edge pairs | removes `PYTHONHASHSEED` dependence (measured: 70 vs 69 communities without it) |
| `randomness=0.001`, `iterations=1`, `trials=1` | minimizes Leiden's stochastic component |
| Total-order re-indexing | makes community **ids** stable across runs, not just membership |
| Hash seed pinned at CLI entry (`__main__.py::_HASHSEED_PINNED_COMMANDS`) | determinism starts before the library is even imported |
| LLM-free default labels | names are reproducible without a model |

This is a genuinely rigorous treatment. The authors treat *reproducible community
identity* as a product requirement, because community ids appear in diffs, reports
and per-node annotations.

## 5. Incremental behavior

Community detection is **not incremental**: every run re-partitions the whole
graph. But Graphify stabilizes the *interface* across rebuilds:

- `remap_communities_to_previous(communities, previous_node_community)` — greedy
  one-to-one matching by intersection size, so a re-run's community 7 is the one
  that most overlaps the old 7; unmatched communities get fresh ids in
  deterministic order (size desc, lexical tie-break).
- `community_member_sigs()` — per-community sha256 over sorted member ids
  (16 hex chars). "A cid whose members no longer hash the same is a different
  community" — this is what stops a stale LLM label from being reused after a
  re-scope.
- `label_still_applies(cid, current_members, previous, saved_sig, current_sig)` —
  keeps a saved label when the signature is unchanged, or when the overlap is a
  strict majority of *both* the previous and the current set (so a one-node
  fragment of a former community cannot inherit its name).

**Transferable, and important for IWTT.** If algorithmically-derived clusters are
shown to the user over time (Mind World regions), stable identity across rebuilds
is what makes them feel like *places* rather than random flashes. The
majority-overlap rule and the membership fingerprint are the two mechanisms that
achieve it.

## 6. Performance characteristics

Measured in this audit on synthetic note-shaped corpora, native Leiden
(`graspologic-native 1.3.1`), single machine, 2 vCPU:

| Nodes | Edges | `cluster()` time | Communities | Notes |
|---|---|---|---|---|
| 1,000 | 1,799 | **0.18 s** | 42 | MEASURED |
| 10,000 | 18,469 | **2.69 s** | 456 | MEASURED |
| 100,000 | 183,770 | **33.7–37.9 s** (2 runs) | 2,567 | MEASURED |
| 250,000 | 458,565 | **82.6 s** | 5,386 | MEASURED |

Growth is roughly linear (10k → 100k is 10× the nodes for ~13× the time; 100k →
250k is 2.5× for 2.4×). **ESTIMATED** for 1,000,000 nodes: **~5–7 minutes** of
partitioning per full rebuild on comparable hardware, based on the linear
extrapolation. That is a full-rebuild cost, not per-query: clustering is a
build-time step whose output is stored as a node attribute.

Note these numbers include Graphify's own overhead — the stable-graph
construction, isolate excision, hub reattachment, oversized and cohesion re-splits
(each of which can trigger *additional* Leiden passes on subgraphs).

## 7. What this could support in IWTT

| IWTT need | Graphify mechanism | Canonical or derived? |
|---|---|---|
| Thought clusters | `cluster()` with confidence-scaled edge weights | **DERIVED** — a view, recomputed, never authored |
| Project clusters | Same, with project membership as a strong edge weight | **DERIVED**, but should be *pinned* — user-declared project membership is canonical and should constrain the clustering, not be overridden by it |
| Idea families | Cohesion re-split + hub exclusion | **DERIVED** |
| Mind World regions | Stable ids via `remap_communities_to_previous` + `community_member_sigs` | **DERIVED**; region *names* may be user-authored, and if so, treated as canonical annotations attached to a derived id |
| Regional importance | `god_nodes` degree + hub suppression | **DERIVED** |

**Non-negotiable IWTT constraint:** none of this may write back into canonical
thought records. Graphify's own precedent is instructive and *insufficient* — it
stamps `community` and `community_name` directly onto nodes inside `graph.json`.
For IWTT the equivalent values must live in a sidecar (as Graphify already does
for the learning layer, but not for communities). See
`IWTT_GRAPHIFY_FUTURE_ARCHITECTURE.md` §4.

## 8. Known limitations to carry forward

- **Non-incremental.** Every rebuild re-partitions the whole graph. At IWTT scale
  (thousands, not millions of nodes) this is a non-issue; at 250k+ it is minutes.
- **Resolution is a global scalar.** One `resolution` for the whole graph; there
  is no per-region granularity, so a "zoom level" hierarchy is not produced — the
  oversized-split pass produces a second level, but it is a remediation step, not
  a designed hierarchy.
- **No hierarchical community tree.** Communities are flat with ids; there is no
  parent/child structure to drive zoomable aggregation. Mind World's "zoom-level
  aggregation" therefore needs either recursive calls to `cluster()` on
  subgraphs, or a different mechanism (see the future-architecture file).
- **Labels inherit hub semantics.** Naming a community after its highest-degree
  member works for code modules; for thoughts it may name a cluster after an
  index-like note. The LLM-free deterministic label is attractive for IWTT
  (no model, reproducible), but the *selection rule* should change.
- **Synthetic-corpus timings.** All numbers above come from generated corpora
  with a note-like degree distribution, not real vaults. Treat as order of
  magnitude.

## 9. Evidence anchors

`graphify/cluster.py`: `_suppress_output` · `_native_leiden` · `_partition` ·
`_MAX_COMMUNITY_FRACTION` · `_MIN_SPLIT_SIZE` · `_COHESION_SPLIT_THRESHOLD` ·
`_COHESION_SPLIT_MIN_SIZE` · `label_communities_by_hub` ·
`community_member_sigs` · `cluster` · `_split_community` · `cohesion_score` ·
`score_all` · `remap_communities_to_previous` · `label_still_applies`.
Also `graphify/report.py::generate` (consumes communities + cohesion),
`graphify/export.py::to_json` (stamps `community`/`community_name`),
`graphify/analyze.py` (consumes communities for surprise scoring and question
suggestion), `pyproject.toml` `leiden` extra. Measurements:
`research_instrumentation/measure_graphify.py` → `timings.jsonl`.
