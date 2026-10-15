# IWTT v0.1 Event and History Model

IWTT does not infer history from only the current thought row. Mutations that change meaningful state are recorded as append-style event rows with:

- `id`: UUID
- `target_id`: UUID of the thought or relationship
- `target_type`: `THOUGHT`, `RELATIONSHIP`, or reserved `VAULT`
- `event_type`: stable uppercase identifier
- `timestamp`: UTC ISO-8601
- `metadata_json`: event-specific context
- `is_demo`: explicit fictional-record marker

The event row is canonical. Events are not silently rewritten when a later action occurs. Revisions complement events: revisions preserve text/title/type snapshots; events describe actions and transitions.

## Implemented event types

- `THOUGHT_CREATED` — initial type and first revision ID.
- `THOUGHT_EDITED` — changed fields and new revision ID.
- `TYPE_CHANGED` — `fromType`, `toType`, and snapshot ID.
- `PROJECT_PROMOTED` — extra milestone event when type becomes `PROJECT`.
- `TAG_ADDED`, `TAG_REMOVED` — tag UUID and name.
- `RELATIONSHIP_CREATED` — endpoints, relationship type, provenance, and initial status.
- `RELATIONSHIP_CONFIRMED` — explicit user confirmation and original provenance.
- `RELATIONSHIP_REMOVED` — previous status/endpoints; relationship row remains.
- `MOVED_TO_GRAVEYARD` — status transition.
- `RESURRECTED` — return to Active, with previous Graveyard timestamp.
- `AI_SUMMARY_GENERATED` — derived artifact ID/type and provider/model.

The event vocabulary can grow without changing the thought's durable ID or replacing its original text.

## Timeline behavior

Timeline is a chronological view over event rows. Thought events open the thought; relationship events carry endpoint context and open the source thought. The thought-detail history includes events targeting that thought plus events targeting its relationships. Revision snapshots are displayed separately so the user can inspect previous content.

A thought that was created as `THOUGHT`, promoted through other lifecycle types, sent to the Graveyard, and later resurrected remains one thought ID with a traceable event/revision sequence. The Graveyard timestamp is retained after resurrection.

## Scope note

There is no general-purpose event-sourcing replay engine in v0.1. The current state is stored for fast UI queries, while events/revisions preserve the audit/history trail. Import validates that revisions agree with current state; the richer future timeline can be built over the preserved records.
