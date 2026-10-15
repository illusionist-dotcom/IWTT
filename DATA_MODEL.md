# IWTT v0.1 Data Model

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

No embedding, graph coordinate, cluster assignment, or layout coordinate is stored in canonical tables.

## Export shape

```json
{
  "format": "iwtt-vault-export",
  "schemaVersion": 1,
  "appVersion": "0.1.0",
  "exportedAt": "...Z",
  "canonical": {
    "thoughts": [], "revisions": [], "tags": [],
    "thoughtTags": [], "relationships": [], "events": []
  }
}
```

`displayTitle`, search snippets/ranks, and graph positions are UI-derived and are not part of canonical JSON. If requested, AI artifacts are placed under a separate optional top-level `derived` section.
