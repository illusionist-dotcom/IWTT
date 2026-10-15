# Future Mind World Compatibility

IWTT v0.1 deliberately stores ideas as durable records and relationships, not as locations in a rendered world. Thought IDs, revisions, events, lifecycle state, tags, and relationships are presentation-independent. This can support a future 2D, 2.5D, zoomable, clustered, animated, or spatial Mind World without changing canonical meaning.

## What v0.1 provides underneath

- Stable UUIDs for thoughts, revisions, relationships, and events.
- Lifecycle type and status suitable for visual differentiation.
- Relationship types and provenance, including unconfirmed proposals.
- Creation/edit/promotion/Graveyard/resurrection chronology.
- Search independent of any visual layout.
- A small read-only SVG graph whose layout is computed in the client and discarded.

## What remains disposable

Node coordinates, edge routing, cluster layouts, camera/zoom state, spatial regions, animation, color/shape encodings, and layout caches must remain derived view state unless the user explicitly creates canonical semantics (such as a named cluster or a user-confirmed relationship). Presentation geometry should not be used to infer a thought's meaning.

## Future data additions

A later version could add separately versioned, rebuildable projections for clusters, semantic embeddings, time layers, or spatial layout. Those additions should reference canonical UUIDs and retain provenance/model/version metadata. A future cluster that the user adopts as knowledge should become an explicit canonical object/relationship with history—not a hidden graph coordinate.

## Explicit v0.1 boundary

No infinite world, 3D environment, avatar, multiplayer/social system, spatial editing, animation engine, or full Mind World is included. The small Connections view is an inspection aid only.
