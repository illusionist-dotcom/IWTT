# IWTT v0.2 Data Model

IDs are UUIDv4 strings generated locally. Timestamps are UTC ISO-8601 strings. SQLite is the canonical store; exported JSON uses camelCase field names. The schema version in a vault export is independent from the human-readable application version.

## Canonical entities

### Thought (`thoughts`)

| Field | Meaning |
|---|---|
| `id` | Durable UUID. |
| `original_text` | First captured user text. Required, preserved, and database-trigger protected against updates. |
| `current_text` | Latest user-authored text. Updates create a revision and `THOUGHT_EDITED` event. |
| `title` | Optional user-authored title. Blank titles get a display-only fallback; no generated title is silently stored. |
| `type` | `THOUGHT`, `EXPERIMENT`, `INVENTION`, or `PROJECT`. Any transition is allowed. |
| `status` | `ACTIVE` or `GRAVEYARD`. |
| `created_at`, `updated_at` | UTC timestamps. |
| `graveyard_at` | Most recent time sent to the Graveyard; retained after resurrection. |
| `resurrected_at` | Most recent resurrection time, or null. |
| `is_demo` | Explicit marker for fictional seed records. |

The original is the first actual user capture. The current text is never substituted into the original field. Content/title/type snapshots are stored in `revisions`.

### Revision (`revisions`)

Each revision has its own UUID, `thought_id`, 1-based continuous `revision_number`, text, title, type, timestamp, and `change_reason`. Revision 1 is the captured original. Later text/title edits and type changes append snapshots. The latest snapshot must match the current thought state when imported.

### Tag and assignment (`tags`, `thought_tags`)

Tags have UUIDs, display `name`, normalized unique name, timestamp, and demo marker. `thought_tags` is a many-to-many link with an assignment timestamp. Adding/removing a tag also appends an event. Tags are user-authored canonical organization, not AI keywords.

### Relationship (`relationships`)

| Field | Meaning |
|---|---|
| `id` | Durable UUID. |
| `source_id`, `target_id` | UUID references to two distinct thoughts. Direction is preserved. |
| `type` | One of `INSPIRED_BY`, `LED_TO`, `RELATED_TO`, `CONTRADICTS`, `SUPPORTS`, `DEPENDS_ON`, `PART_OF`, `SUPERSEDES`, `REVIVES`, `TESTS`, `IMPLEMENTED_BY`. |
| `provenance` | `USER_CONFIRMED` or `AI_SUGGESTED`, preserving origin. |
| `status` | `SUGGESTED`, `CONFIRMED`, or `REMOVED`. |
| `confirmed_at`, `confirmed_by` | Confirmation audit. AI proposals are not confirmed until a user action sets status and records `USER`. |
| `metadata_json` | Optional note/context. |
| timestamps / `is_demo` | Creation/update time and explicit demo marker. |

Removed relationships are retained with `REMOVED` status and history rather than physically deleted.

### Event (`events`)

Events have UUID, target object ID and type, event type, UTC timestamp, JSON metadata, and demo marker. Event records explain how current state was reached; see [EVENT_MODEL.md](EVENT_MODEL.md).

## Derived tables

- `search_documents`: one disposable text projection per thought, containing original/current text, title, and tags. It is not canonical and has no authority to rewrite its source.
- `derived_artifacts`: optional AI output. Each record has UUID, thought ID, artifact type, payload JSON, source thought IDs, provider, model, and generation timestamp. It is excluded from default canonical export and can be deleted independently.

### v0.2 derived tables (`recall_*`, index schema version 3)

All nine are derived, rebuildable from canonical records, erased by
`clear_derived_data()` / `delete_all_derived_intelligence()`, and excluded from the
default canonical export. None stores canonical truth; `recall_docs` stores counts and
timestamps only, never verbatim thought text.

**Per-thought (all `REFERENCES thoughts(id) ON DELETE CASCADE`)**

- `recall_tokens(thought_id, token, field ∈ {title, body, tag}, tf)` — PRIMARY KEY `(thought_id, token, field)`, indexed on `token`. Field weights: title 3.0, tag 2.5, body 1.0.
- `recall_trigrams(trigram, thought_id, tf)` — PRIMARY KEY `(trigram, thought_id)`, indexed on `thought_id`. Padded 3-character grams.
- `recall_docs(thought_id PK, type, status, created_at, updated_at, is_demo, trigram_norm, title_tokens, body_tokens, tag_tokens, degree, indexed_at)` — the drift-detection projection: `ensure_fresh()` compares it against canonical `thoughts`.
- `recall_graph_cache(thought_id PK, degree, in_degree, out_degree, hub_factor, …)` — derived degrees and hub factors, so no public entry point has to count edges on read.
- `recall_clusters(thought_id, cluster_id, cluster_size, label, algorithm, authority, member_fingerprint)` — `algorithm = "components+labelprop"`, `authority = AUTOMATICALLY_DISCOVERED_CLUSTER`. Member titles are read live from `thoughts`, never stored here.
- `recall_query_memory(id PK, query_norm, thought_id, feedback ∈ {USEFUL, NOT_USEFUL, OPENED, CORRECTION}, weight, rank_at_time, score_at_time, note, created_at)` — indexed on `(query_norm, thought_id)`. See [QUERY_MEMORY.md](QUERY_MEMORY.md).

**Corpus-wide (no `thought_id` foreign key, by design)**

- `recall_term_stats(term PK, df)` — document frequencies for IDF. A document frequency belongs to the corpus, not to one thought, so a `thought_id` column would be a category error. Recomputed from `recall_tokens` on every sync, and reconciled whenever the canonical signature moves — otherwise a cascade deletion would leave stale frequencies behind.
- `recall_meta(key PK, value)` — `schema_version`, `sync_signature`, `graph_signature`, `hub_threshold`, `document_count`, build stamps.
- `recall_build_log(id, built_at, scope, …)` — build/sync/reconcile history.

Their contract is identical to the per-thought tables: derived, rebuildable, wiped on
clear, no canonical content, recomputed on refresh.

### Canonical schema in v0.2

**Unchanged.** No new canonical table, no new canonical column, no changed constraint,
no changed trigger. `schema_info.schema_version` remains `'1'`; v0.2 adds
`recall_schema_version`, `app_version`, `migrated_from_app_version` and `migrated_at`
rows to `schema_info`, and nothing else. See
[V0.1_TO_V0.2_MIGRATION.md](V0.1_TO_V0.2_MIGRATION.md).

No embedding, graph coordinate, cluster assignment, or layout coordinate is stored in canonical tables.

## Export shape

```json
{
  "format": "iwtt-vault-export",
  "schemaVersion": 1,
  "appVersion": "0.2.0",
  "exportedAt": "...Z",
  "canonical": {
    "thoughts": [], "revisions": [], "tags": [],
    "thoughtTags": [], "relationships": [], "events": []
  }
}
```

`displayTitle`, search snippets/ranks, and graph positions are UI-derived and are not part of canonical JSON. If requested, AI artifacts are placed under a separate optional top-level `derived` section.

**The canonical export schema version is still 1.** `export_vault(include_derived=True)` adds a separate optional `derived.recall` section holding index statistics — counts and build state, not memory. Recall scores, ranks, explanations, clusters, graph caches and query-outcome memory are never canonical and are never required for a faithful restore.
