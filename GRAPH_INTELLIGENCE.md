# GRAPH INTELLIGENCE (IWTT v0.2)

How IWTT uses the relationships **you** stored to enrich recall, explore connections,
find paths, and discover groupings — without ever letting a derived graph opinion
overwrite what a relationship actually is.

Code: `iwtt/recall/graph.py` (shared edge semantics), `expansion.py`, `paths.py`,
`graphview.py`, `clusters.py`. Derived storage: `recall_graph_cache`,
`recall_clusters`. Canonical storage: `relationships` — **unchanged from v0.1**.

---

## 1. The invariant

v0.1 separates *origin* from *consent* with two fields, and v0.2 preserves that
separation exactly:

| `provenance` | `status` | Meaning | Edge authority |
| --- | --- | --- | --- |
| `USER_CONFIRMED` | `CONFIRMED` | You stated this connection | **1.00** |
| `AI_SUGGESTED` | `CONFIRMED` | A machine suggested it and **you** confirmed it | **0.70** |
| `AI_SUGGESTED` | `SUGGESTED` | Suggested, never confirmed | **0.35** |
| `USER_CONFIRMED` | `SUGGESTED` | Not producible by v0.1; handled anyway | 0.35 |
| anything | `REMOVED` | You deleted it | **0.00 — never traversed** |

`provenance_weight(provenance, status, config)` implements this table. Two rules
follow from it and are enforced everywhere:

1. **Authority cannot be read from `provenance` alone.** Confirming an AI suggestion
   sets `status='CONFIRMED'` and `confirmed_by='USER'` while *keeping*
   `provenance='AI_SUGGESTED'`. v0.2 does not weaken that model, and does not
   collapse it into a single confidence number (the Graphify dossier's 3-value
   `confidence` enum was **rejected** for exactly this reason — see
   `GRAPHIFY_ADAPTATION_NOTES.md`).
2. **Search use never confirms anything.** Recording query feedback, running recall,
   expanding a neighbourhood, or rendering a graph writes nothing to `relationships`.
   `scripts/verify_release_v02.py` asserts `suggestedNeverBecameConfirmedByUse`
   (`confirmedBy` stays `null`) after exercising all of them.

`REMOVED` edges are excluded by the edge queries *and* return 0.0 from
`provenance_weight`, so a caller that forgets the filter still cannot traverse a
relationship the user deleted.

---

## 2. Edge semantics

`config.RELATIONSHIP_SEMANTICS` gives each of v0.1's 11 relationship types a
direction, a weight, and human phrasing for both directions:

| Type | Symmetric | Weight | Forward / reverse phrasing |
| --- | --- | --- | --- |
| `LED_TO` | no | 1.00 | "led to" / "was led to by" |
| `INSPIRED_BY` | no | 1.00 | "inspired by" / "inspired" |
| `IMPLEMENTED_BY` | no | 1.00 | "implemented by" / "implements" |
| `SUPPORTS` | yes | 0.90 | "supports" / "supported by" |
| `SUPERSEDES` | no | 0.90 | "supersedes" / "superseded by" |
| `PART_OF` | no | 0.90 | "part of" / "contains" |
| `DEPENDS_ON` | no | 0.90 | "depends on" / "is depended on by" |
| `REVIVES` | no | 0.85 | "revives" / "was revived by" |
| `TESTS` | no | 0.85 | "tests" / "is tested by" |
| `RELATED_TO` | yes | 0.80 | "related to" / "related to" |
| `CONTRADICTS` | yes | 0.70 | "contradicts" / "contradicts" |

Symmetry does **not** mean equal weight in both directions: an asymmetric edge
traversed against its stored direction is multiplied by
`REVERSE_TRAVERSAL_FACTOR = 0.85`. Walking backwards along "A led to B" is real
evidence, but weaker evidence than walking forwards.

The phrasing is used to build human sentences ("*inspired by* → Enemy memory"), and
labels are passed through `sanitize_label()` before they reach a payload.

---

## 3. Enriching recall (never replacing it)

Graph expansion runs **after** lexical candidate generation and can only add to a
candidate set that already exists.

**Seed selection** — `pick_seeds()` is deterministic:

* at most `graph_seeds_max = 6` seeds;
* a seed must score at least `graph_min_seed_score = 1.5`;
* a candidate is dropped if it scores within `graph_seed_gap_ratio = 0.20` of an
  already-chosen seed's score *and* shares its normalized label (dedupe by label);
* ties break on `(-score, -degree, label_length, thought_id)`.

**Expansion** — `expand_neighbourhood()` is a bounded BFS/DFS hybrid:

* depth ≤ `graph_max_depth = 2`, nodes ≤ `graph_max_nodes = 40`;
* contribution `= seed_score × graph_edge_base (0.45) × edge_weight × graph_depth_decay (0.5) ** depth`,
  capped at `graph_max_contribution = 2.0`;
* a hub is **penalised and not transited through**, never filtered out (see §4);
* a node already visited is not re-expanded unless it is itself a seed;
* the seed population is keyed on `signal.origin == seed_id`, so a seed enriched by a
  *different* seed is not mistaken for its own origin.

**The hard rule.** Because a strong seed can push a depth-2 suggestion above the
score floor, a floor alone cannot enforce "graph enriches, never replaces". So the
engine has `returnable()`: a result is returnable only if it scores ≥ `min_score`
**and** carries lexical evidence, since
`graph_may_surface_without_lexical_evidence = False`. Candidates reached only by
graph hops are reported in `honesty.graphReached`,
`honesty.graphReachedBelowThreshold` / `…Count` / `graphReachedReturnedCount`, and
carry the explanation `NO_LEXICAL_EVIDENCE` ("No word in common — similarity
suppressed"). They can never be a `STRONG_MATCH`.

Explanations distinguish provenance: `RELATED_CONFIRMED` ("Related through a
confirmed connection") versus `RELATED_SUGGESTED` ("Related through a suggested
connection"), plus `MULTI_HOP`, `CONNECTED_TO_PROJECT`, `HISTORICAL`.

---

## 4. Hub suppression

A thought connected to everything is a poor bridge: routing through it makes
unrelated memories look related. `hub_threshold` is
`max(hub_min_degree = 5, ceil(p90 of degrees))`, computed at index build time and
stored in `recall_meta`; `hub_floor = 0.15` bounds how far a hub factor can fall.

Suppression is a **penalty plus a transit rule, never a filter**. A hub can still be
returned as a result if it earns its place lexically; it just stops being a
shortcut, and its `hubFactor` is published in diagnostics so the penalty is visible
rather than mysterious.

The Graphify dossier's `max(50, p99)` hub floor was **not** copied: the mechanism is
adopted, the constants were re-derived for a personal thought vault, where a thought
with 50 relationships is already extraordinary.

---

## 5. Paths, lineage, neighbourhoods

**`find_path(source, target, directed=True)`** — bounded breadth-first search:

* depth ≤ `path_max_depth = 6`, visited ≤ `path_max_visited = 5000`;
* `REMOVED` edges are never traversed;
* `AI_SUGGESTED` edges **are** traversable (they are real stored relationships) but
  carry their lower authority, and every edge in the result reports its provenance
  and status;
* **no edge is ever invented to close a gap.** When no path exists, the result is
  `found: false` with the endpoints in `nodes`, `edges: []`, and the reason:

  > No known relationship path connects these two thoughts within 6 hops following
  > stored directions. IWTT will not invent an edge to close the gap.

* `PathResult` exposes `.found`, `.reason`, `.narrative()` and `.to_dict()`. Edges
  use the key `relType` (not `type`).

**`thought_lineage(id, max_depth=3)`** — directional: what led to this thought, and
what this thought led to, following stored directions.

**`thought_neighbourhood(id, depth=1)`** — the induced subgraph around one thought,
bounded, with provenance on every edge.

---

## 6. GraphViewModel boundary

`build_graph_view_model(root_ids=None, max_nodes=200, include_graveyard=True,
include_suggested=True)` returns a **bounded, derived view** — never the whole vault
in memory, and never a rendering instruction:

* `nodes[]` each carry `entityType` (`THOUGHT_ENTITY` in v0.2; the vocabulary already
  reserves `EXTERNAL_KNOWLEDGE_ENTITY` so a future renderer can mix entity kinds
  without reinterpreting a Thought — see `EXTERNAL_KNOWLEDGE_ARCHITECTURE.md`);
* `edges[]` carry `relType`, `provenance`, `status`, `direction`, `weight`;
* `layout: {persisted: false, coordinatesIncluded: false}` — IWTT stores no
  positions and computes no permanent layout. Persisting a layout would make a
  derived artefact look like canonical state, and re-deriving coordinates is cheap;
* `canonicalFields` / `derivedFields` declare, per field, which side of the sacred
  contract it comes from;
* `bounds: {requestedNodes, reachableNodes, returnedNodes, truncated, note}` — when
  the view is truncated, the payload says so instead of implying completeness;
* the node set is an **induced subgraph**: `_select_by_roots` returns
  `(selected, reachable)`, so the model can report what was reachable but not
  returned.

The v0.1 client-side SVG graph in `web/app.js` continues to work unchanged;
v0.2's Connection Explorer V2 consumes this model.

---

## 7. Derived clustering

`rebuild_clusters()` produces groupings that are **derived, labelled as derived, and
deletable**:

* `algorithm = "components+labelprop"` — connected components first, then
  synchronous label propagation (`cluster_labelprop_iterations = 6`) inside
  components of at least `cluster_labelprop_min_size = 24` members. No Leiden, no
  Louvain, no `graspologic`, no NetworkX: standard library only. (Synchronous label
  propagation can oscillate on 2-cycles and 3-paths; partitioning by component first
  is what keeps the result stable, and the iteration count is a documented bound
  rather than a convergence claim.)
* `cohesion = internal_edges / (n·(n-1)/2)`, 1.0 for a single member. Internal edge
  counts are computed in one pass over the edges using each endpoint's label;
  counting per cluster by rescanning every pair was O(clusters × edges × members)
  and dominated rebuild time at 10,000 thoughts.
* **Stable identity:** `cluster_id = "c-" + sha256(sorted member ids)[:12]`, then a
  greedy one-to-one remap against the previous partition. Reuse requires a strict
  majority of **both** the old and the new member sets, so a one-node fragment cannot
  inherit a name. The remap uses an inverted member→old-cluster index: only old
  clusters that actually share a member can win, which replaces an all-pairs scan
  (~57 million intersections at 10,000 thoughts) with work proportional to the
  membership rows. Same winner, same deterministic order.
* **Labels** come from `choose_cluster_label()`: the most-connected **non-hub**
  member, ties broken by most recently created and then by durable id. Graphify names
  a community after its highest-degree member, which in a thought graph is usually an
  index-like note rather than the idea the cluster is about. Only if every member is
  a hub does the plain degree winner get used.
* `authority = "AUTOMATICALLY_DISCOVERED_CLUSTER"` on every row. Member titles are
  read live from canonical `thoughts`, so a cluster label can never outlive a rename.
* Cluster membership is never canonical, never exported by default, and erased by
  `clear_derived_data()` / `delete_all_derived_intelligence()`.
* Explanation code: `IN_CLUSTER` ("In the same automatically discovered cluster").

**Reclustering is skipped when the graph did not change.** Clusters are a function of
the live relationship graph and of which thoughts participate in it — not of what any
thought says. `RecallIndex.graph_signature()` (edge count, max edge `updated_at`,
count of distinct participating thoughts) is stored in `recall_meta`; an incremental
sync that only touched text does not pay for a reclustering. Measured effect: adding
50 thoughts to a 10,000-thought vault and running a recall went from 45.4 s to
0.79 s.

---

## 8. What is deliberately not here

* No whole-file JSON graph store, no in-memory canonical graph, no NetworkX as state.
* No AI-invented edges. Nothing in this package can create a relationship; only
  `Vault.create_relationship()` and `Vault.confirm_relationship()` can, and only a
  user action reaches them.
* No community detection presented as truth: clusters carry an authority string that
  says they were automatically discovered.
* No persisted layout, no coordinates, no 2D/2.5D renderer, no Mind World.
* No code/repository AST ingestion. `Source File`, `Function`, `Bug` and `Fix` exist
  in v0.2 only as reserved vocabulary and documented design
  (`EXTERNAL_KNOWLEDGE_ARCHITECTURE.md`).
