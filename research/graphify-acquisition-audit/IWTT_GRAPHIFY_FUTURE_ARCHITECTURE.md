# IWTT_GRAPHIFY_FUTURE_ARCHITECTURE.md

Two **design-only** special questions:

1. **Graphify + IWTT** — how IWTT could represent human thoughts *and* external
   knowledge sources (source code, documents, PDFs, research, experiments, design
   notes) without contaminating the canonical Thought schema.
2. **Can Graphify help the Mind World?** — clustering, regions, importance,
   relationship visualization, path discovery, simplification, zoom aggregation,
   each marked **canonical** or **derived**.

**Nothing here is implemented. Integration decisions belong to a later mission.**
IWTT v0.1 is unmodified; Graphify is unmodified.

---

# PART 1 — Thoughts and external knowledge side by side

## 1.1 The requirement

A future chain to represent:

```
Original Thought → Experiment → Project → Source File → Function → Bug → Fix → New Thought
```

Constraints that make this hard:

- The **original user-authored thought is sacred**; nothing above may alter its text.
- **AI-derived metadata is rebuildable**; canonical history must survive deleting
  all derived data.
- **External knowledge is not a thought.** A source file is not something the user
  thinks; it is something the user observed.
- The chain must be traversable *in both directions*: from a thought down to the
  line of code it produced, and from a bug back up to the thought that seeded it.
- External sources are **volatile**: code changes, files move, functions are
  renamed, documents are re-issued. Canonical thoughts are not.

## 1.2 The core design move: two planes, one traversal

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ PLANE A — CANONICAL (user-owned, never machine-rewritten)                    │
│                                                                              │
│  Thought ── Revision (append-only)                                          │
│     │                                                                        │
│     ├── Lifecycle events: created · revised · linked · abandoned ·           │
│     │                     graveyarded · resurrected · promoted               │
│     ├── Relationship (user-asserted / user-confirmed)                        │
│     ├── Tag (user)                                                           │
│     └── SourceRef ───────────────┐   "the user says this thought came from   │
│                                  │    that file / that document / that bug" │
│ PLANE B — EXTERNAL (observed, not owned)                                     │
│                                  │                                           │
│  KnowledgeSource (repo · document · pdf · url · dataset · experiment-run)    │
│     └── KnowledgeEntity (file · symbol · function · class · section ·        │
│            │              commit · issue · bug · fix · build)                │
│            └── KnowledgeEntity …                                             │
│                                  │                                           │
│  Anchored by: source_id + immutable locator (commit SHA + path + symbol,     │
│               document hash + page/section, run id + artifact hash)          │
└─────────────────────────────────────────────────────────────────────────────┘
        ▲                                              ▲
        │ derived edges (suggested links, AI metadata) │ derived projection
        │                                              │
┌───────┴──────────────────────────────────────────────────────────────────────┐
│ PLANE C — DERIVED (rebuildable, disposable, versioned)                       │
│  index · clusters/regions · scores · AI summaries/titles/tags · suggested    │
│  relationships · embeddings · layout · recall caches                          │
└──────────────────────────────────────────────────────────────────────────────┘
```

**The rule that prevents contamination:** Plane A never contains a record whose
`id` was minted by an importer, and Plane B never contains a record the user
authored. The only *canonical* edge that crosses planes is a **SourceRef** the
user created (or explicitly confirmed). Everything else crossing planes is
derived, versioned, and deletable.

## 1.3 Why separate entity types, not a widened Thought

The tempting shortcut is to add `type: file` to the Thought table and let
externals sit alongside. Rejected, with reasons:

| Shortcut | Why it fails |
|---|---|
| Reuse the Thought table with a `kind` column | Every canonical invariant ("original text is sacred", "revisions are append-only") becomes conditional. A file revision is not a thought revision; a function being deleted is not a graveyard event |
| Give externals thoughts with `author = ai` | Blurs IWTT's core premise. The user's record of "I thought this" becomes indistinguishable from "a tool read this" |
| Only store SourceRefs as free text | Loses traversal. You cannot ask "which thoughts produced code in this module?" |
| Import externals into canonical, then treat them as immutable | Code is volatile; a canonical record that must be updated when a file changes is not canonical |

Separate entity types keep every canonical invariant **unconditional**, which is
the property IWTT most needs.

## 1.4 Namespaces and identity

```
thought:<uuid>            canonical, opaque, never content-derived
revision:<uuid>           canonical, append-only, parent-linked
event:<uuid>              canonical, append-only
relation:<uuid>           canonical when user-asserted/confirmed
tag:<slug>                canonical (user vocabulary)

source:<uuid>             external, user-registered (a repo, a doc set, a run)
entity:<source>:<locator> external, deterministic from the locator
suggestion:<uuid>         derived, carries generator version + evidence
region:<derived-id>       derived, stable across rebuilds (see Part 2)
metadata:<hash>           derived, keyed by generator version
```

**Identity rules:**

1. Canonical ids are **opaque** and never derived from content (an edited thought
   keeps its id; contrast Graphify's content-derived symbol ids, which is right
   for code and wrong for thoughts).
2. External entity ids are **deterministic functions of an immutable locator** —
   for code: `(repo, commit_sha, path, symbol)`; for documents:
   `(document_hash, section)`; for experiment runs: `(run_id, artifact_hash)`.
   Pin the commit/hash: an external entity without an immutable locator is a
   moving target and must not be referenced canonically.
3. Derived ids are stable-but-recomputable (membership fingerprints, as in
   Graphify's `community_member_sigs`).

## 1.5 Walking the chain

```
Original Thought ──(SourceRef: user, canonical)──▶ Experiment
      ▲                                                │
      │ (derived: "influenced by", suggested)          │ (observed: produced artifacts)
      │                                                ▼
   New Thought ◀──(SourceRef: user)── Fix ◀── Bug ◀── Code entity
```

Traversal semantics per edge:

| Edge | Plane | Asserted by | Rebuildable? |
|---|---|---|---|
| Thought → Experiment | A | user | No (canonical) |
| Thought → Thought ("influenced by") | A if confirmed, C if suggested | user or model | Depends |
| Experiment → Project | A | user | No |
| Project → Source (repo) | A | user | No |
| Source → SourceFile → Symbol → Bug → Fix | B | **observation at a pinned locator** | Yes — re-observed against a new commit |
| Fix → New Thought | A | user | No |

**Key consequence:** the *external* half of the chain is a re-derivable
observation, not a record. Reading "which function implements the idea in thought
X?" against a different commit legitimately yields a different answer — and that
is correct behaviour, provided the answer is labelled with the commit it was
observed at.

## 1.6 Provenance: what a cross-plane edge must carry

Extending the model proposed in `PROVENANCE.md` §5 to cross-plane edges:

```yaml
cross_edge:
  # CANONICAL half (only if the user asserted/confirmed it)
  assertion: user_asserted | user_confirmed | (absent)
  origin_ref: <thought revision id>

  # OBSERVATION half (always present on external edges)
  observation:
    source_id: <registered source>
    locator: { commit: <sha>, path: <p>, symbol: <s> }
    observed_at: <timestamp>
    observed_by: <tool + version>       # e.g. "graphify 0.9.91" — enables invalidation
    confidence: EXTRACTED | INFERRED | AMBIGUOUS   # if Graphify output is consumed

  # DERIVED half (always optional, always rebuildable)
  suggestion:
    source: model | deterministic | import
    method: <closed vocabulary>
    score: 0.0-1.0
    evidence: [ {kind, ref} ]
    generator_version: <string>
```

Three properties make this safe:

1. **An external observation can never be mistaken for a user assertion** — the
   fields live in different blocks and the UI can render them differently.
2. **Every external reference is pinned** (commit/hash), so it is reproducible and
   can be marked *stale* rather than silently wrong when the underlying artifact moves.
3. **Deleting Plane C deletes nothing canonical**, and deleting the derived
   *projection* of Plane B does not delete the user's SourceRefs.

## 1.7 What Graphify contributes here (and what it does not)

**Contributes (evidence-based):**
- The **shape** of an external entity model: nodes with `source_file` +
  `source_location`, edges with closed-vocabulary `confidence`, and an explicit
  `EXTRACTED`/`INFERRED` distinction that maps onto "observed" vs "deduced".
- The **provenance-flagging** principle for AI output
  (`verification="unverified"`, flag-never-drop).
- The **cache/version namespacing** discipline that makes an external projection
  safely rebuildable against a new commit
  (`cache/ast/v{version}-s{schema}-g{grammars}/`).
- **Refusal to merge across context boundaries** (`deduplicate_entities`
  cross-repo guard) — extended to "never merge a thought with an external entity".
- The **ingest-extract-validate-build** skeleton for a *future optional* external
  ingestion path, if IWTT ever parses repositories itself.

**Does not contribute:**
- Any storage model (its whole-file JSON is exactly what Plane A must not be).
- Any permission model (§ `AGENT_INTEGRATION.md`).
- Any lifecycle notion (graveyard, resurrection, revision) — entirely IWTT's own.
- Any answer to "is this the *same* idea?" across planes: that is a judgement call
  the user makes; Graphify would happily merge by string similarity.

## 1.8 Practical constraints worth restating

1. **External ingestion is optional and namespaced.** If a user never registers a
   source, IWTT behaves exactly as v0.1.
2. **External ingestion must be reconstructible from pinned locators**, or it is
   not a projection — it is a copy that will rot.
3. **The canonical store must never require the external store to be readable.**
   A vault whose code repo was deleted must still open.
4. **A thought's text is never rewritten by an importer.** At most, an importer
   proposes a *link*.
5. **Every external artifact must be deletable in one action** ("forget this
   source"), leaving canonical thoughts and their SourceRefs intact (a SourceRef to
   a forgotten source becomes `orphaned`, which is a legitimate, displayable state —
   not an error).

---

# PART 2 — Can Graphify help the Mind World?

Mind World is a visual: thoughts, idea clusters, experiments, inventions,
projects, relationships, graveyard regions, time/history, resurrected concepts —
2D/2.5D, zoomable, spatial, clustered, animated — as a **view over canonical
information**.

The governing rule from the mission brief: *Graph layout must never become
canonical truth.* Every recommendation below is therefore marked **DERIVED**
unless the user authored it.

| # | Mind World need | Graphify mechanism (evidence) | Verdict | Layer |
|---|---|---|---|---|
| 1 | Automatic thought clustering | `cluster()` — confidence-scaled edge weights, deterministic Leiden | **Adopt the mechanism, recalibrate** | DERIVED |
| 2 | Community regions as places | Stable identity: `remap_communities_to_previous`, `community_member_sigs`, `label_still_applies` | **Adopt** | DERIVED (ids) + CANONICAL (a name the user types for a region) |
| 3 | Region naming | `label_communities_by_hub` (deterministic, LLM-free) | **Adapt**: keep determinism, change the selection rule (thoughts' hubs are index notes) | DERIVED |
| 4 | Important-node detection | `god_nodes` (degree + hub suppression); betweenness in `suggest_questions` (k-sampled, seed 42) | **Adapt** | DERIVED — navigation only, never meaning |
| 5 | Relationship visualization | Edge rendering with per-edge `relation` + `confidence` + `source_location`; `_subgraph_to_text`'s fixed one-line-per-node format | **Adapt**: visual encoding must distinguish user-confirmed from suggested relationships | DERIVED view over CANONICAL edges |
| 6 | Path discovery | `_path_search_graph` + `_shortest_path_text`, direction-following with `undirected` opt-out; ambiguity refusal | **Adopt** | DERIVED |
| 7 | Graph simplification | Hub suppression (`max(50, p99(degree))`) as the "don't expand transit hubs" rule; `exclude_hubs_percentile` in clustering | **Adopt the principle** | DERIVED |
| 8 | Zoom-level aggregation | **Nothing adequate exists.** Graphify has flat community ids plus remediation splits; no hierarchy | **Design new**; borrow only the stable-id discipline | DERIVED |
| 9 | Time/history dimension | Nothing in the query/view layer (the temporal benchmark measures rebuilds, not timelines) | **IWTT-original** | CANONICAL events, DERIVED views |
| 10 | Graveyard regions | No lifecycle model at all | **IWTT-original**; borrow the *trail* shape from `_provenance_for` | CANONICAL (state) + DERIVED (region geometry) |
| 11 | Layout persistence | Graphify keeps layout client-side; nothing spatial is stored | **Adopt the discipline** | DERIVED (and preferably unsaved) |

## 2.1 Clustering: what to take, precisely

**Take:**

- Edges carry confidence; **low-confidence edges get reduced weight in
  partitioning** (`ambiguous_scale`, default 0.5, `0.0` = exclude from clustering
  only). In IWTT terms: *AI-suggested links should not be able to define the shape
  of the user's world.* This single mechanism is the most directly valuable item in
  the whole repository for Mind World.
- **Determinism as a requirement, not a nicety**: pinned seed, canonicalized edge
  ordering (Graphify measured 70 vs 69 communities under different hash seeds),
  total-order id assignment.
- **Hub handling**: exclude the region's super-hubs from partitioning, reattach by
  majority vote, and handle nodes whose only neighbour is a hub. For IWTT the
  hubs are inbox/daily-note/project nodes — the same problem, same solution shape.
- **Oversized and low-cohesion re-split**: prevents one mega-region swallowing the
  world (the "CLAUDE.md connected to everything" failure has an exact IWTT
  analogue in "my daily note connected to everything").
- **Cohesion as a quality signal** per region, self-loop safe.

**Recalibrate (do not import constants):** the 25% oversized threshold, the
`cohesion < 0.05` threshold, the ≥50-node floor, and resolution 1.0 are all tuned
for code graphs with hundreds-to-thousands of nodes per cluster. A personal vault
has far sparser graphs and much smaller clusters; every one of these numbers must
be re-derived and tested.

## 2.2 Regions as places: identity over time

The Mind World's usability hinges on a property Graphify treats as a first-class
concern and most graph tools do not: **a region must keep its identity across
rebuilds.** The transferable pair is:

1. **Greedy overlap remapping** to reuse previous ids (`remap_communities_to_previous`).
2. **A membership fingerprint** (sha256 of sorted member ids) so a reused id is not
   mistaken for an unchanged region, plus a **majority-of-both-sets rule** before a
   saved name may be reused (`label_still_applies`).

Without (1) the world reshuffles on every rebuild; without (2) a stale name sticks
to a region that has become something else. Both failure modes are visible to a
user immediately, which is why this machinery is worth its complexity.

**Canonical/derived boundary for this feature:** the *region id* is derived; the
*name the user types* is a canonical annotation that references the derived id and
can be re-attached (or orphaned) when clusters change. IWTT must handle
"your region 'Enemy AI' no longer exists as a cluster — reattach, rename, or
archive the name?" as a first-class, non-destructive prompt.

## 2.3 Importance: useful for navigation, dangerous for meaning

Graphify computes prominence in two ways: degree (`god_nodes`, with hub
suppression and file/leaf exclusions) and betweenness centrality (only inside
`suggest_questions`, k-sampled with `seed=42`, then explicitly clamped so one god
node does not dominate).

For Mind World, the honest framing is:

- **Use** prominence to decide *what to draw large, label, or surface first* —
  a rendering budget problem.
- **Never** let prominence imply the thought matters more. A hub in a personal
  vault is often an index or an inbox, not an insight. Graphify's own
  `exclude_hubs_percentile` is an admission of exactly this failure mode.
- Any "important thought" surfaced to the user must be explainable ("connected to
  12 thoughts across 3 projects") and derived.

## 2.4 Relationship visualization: the one place IWTT must exceed Graphify

Graphify renders an edge with `relation`, `confidence`, `context`,
`source_file:line`. That is enough for code. For IWTT the visual vocabulary must
encode the distinctions the canonical model owns:

| Visual channel | Encodes |
|---|---|
| Line style (solid) | user-asserted / user-confirmed relationship |
| Line style (dashed / dotted, distinct colour) | AI-suggested, not yet confirmed |
| Opacity / weight | strength (derived score) or recency |
| Glyph / colour | lifecycle (active, dormant, graveyard, resurrected) |
| Arrow | direction, stored as a field — **never** array position |

The temptation to let suggested links render identically to confirmed ones must be
resisted: the Mind World would then display the model's guesses as the user's
thinking. Graphify's own cluster weighting (2.1) is evidence that the authors
considered confidence a structural property; IWTT needs it in the *view* too.

## 2.5 Simplification and zoom aggregation: mostly new design

Graphify gives two useful principles and no complete solution:

- **Principle 1 — suppress transit hubs, not hubs as such.** A hub can be a
  destination (drawn, labelled) without being a thoroughfare (expanded). This maps
  directly onto zoom: at low zoom, draw region anchors; at high zoom, expand.
- **Principle 2 — split what is too big or too weakly connected.** Oversized and
  low-cohesion re-splitting is a *remediation* step; it produces a second level
  incidentally, not a designed hierarchy.

What is missing and must be designed for Mind World:

| Need | Why Graphify does not solve it |
|---|---|
| Multi-resolution hierarchy (regions within regions) | Communities are flat with integer ids; no parent/child links |
| Zoom-dependent detail budget | No notion of "what to draw at zoom k" (the token budget is for text) |
| Region anchoring for stable spatial layout | Nothing spatial is stored, by design |
| Time-based regions | No temporal view layer |
| Graveyard/resurrection geography | No lifecycle model |

**Recommended shape (design only):** compute a **hierarchy** by recursive
agglomeration over the derived graph (cluster, then cluster the cluster graph),
storing each level in the derived layer with membership fingerprints, and render
level *k* by drawing each cluster as a single node with an aggregated edge set.
Keep the *user's* names attached to level-0 region ids, and treat higher levels as
purely visual. None of this is canonical.

## 2.6 The canonical/derived ledger for Mind World

| Artifact | Canonical or derived | Notes |
|---|---|---|
| Thought text and revisions | **CANONICAL** | Sacred; never rewritten by the view |
| User-asserted / user-confirmed relationships | **CANONICAL** | |
| Lifecycle state and events (graveyard, resurrection) | **CANONICAL** | |
| User tags, user-typed region names | **CANONICAL** | Names reference derived region ids; orphaning is a displayable state |
| Cluster membership | DERIVED | Rebuilt on every cluster run |
| Region ids | DERIVED | Stable via overlap remapping + fingerprints |
| Prominence / centrality scores | DERIVED | Navigation only |
| AI-suggested relationships | DERIVED | Rendered distinctly; never shapes the world unweighted |
| AI summaries / titles / embeddings | DERIVED | Sidecar, versioned |
| Layout (x/y/zoom/animation state) | DERIVED, preferably unsaved | Cast as ephemeral per session |
| Time/history geometry | DERIVED | Built from canonical events |

**Delete test:** if every DERIVED row disappeared, the user must still see their
thoughts, revisions, tags, confirmed relationships, and lifecycle history — and a
rebuild must restore the world minus any AI suggestions.

## 2.7 Evidence anchors

`cluster.py` (`cluster`, `ambiguous_scale`, `exclude_hubs_percentile`,
`_split_community`, `cohesion_score`, `remap_communities_to_previous`,
`community_member_sigs`, `label_still_applies`, `label_communities_by_hub`,
determinism comments) · `analyze.py` (`god_nodes`, `_surprise_score`,
`suggest_questions` betweenness clamping) · `serve.py` (`_bfs` hub threshold,
`_dfs`, `_complete_induced_edges`, `_subgraph_to_text`,
`_path_search_graph`) · `export.py::to_canvas` and `exporters/html.py`
(`forceAtlas2Based` — client-side layout, nothing persisted) ·
`build.py::_normalize_hyperedge_members` (n-ary grouping) ·
`dedup.py` cross-repo guard (never merge across context) ·
`reflect.py` (`_provenance_for`, sidecar discipline) ·
`cache.py` (version-namespaced derived data).
