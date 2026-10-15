# IWTT v0.1 Architecture

## Overview

IWTT v0.1 is a small local web application backed by SQLite. Python's standard library provides the HTTP server, SQLite binding, JSON handling, UUIDs, ZIP/backup primitives, and fuzzy string comparison. The browser UI is a same-origin HTML/CSS/JavaScript single-page experience. There is no remote database or required third-party runtime.

```text
Browser (web/)
    │ same-origin JSON / download requests
    ▼
iwtt/server.py  ── route validation, HTTP responses, static assets
    │
    ├── iwtt/ai.py   ── NoAI by default; explicit optional provider boundary
    ▼
iwtt/store.py  ── canonical operations + SQLite transactions
    ├── canonical: thoughts, revisions, tags, thought_tags, relationships, events
    └── derived: search_documents, derived_artifacts
    ▼
~/.iwtt/thought-vault.sqlite3 (overridable with IWTT_DATA_DIR)
```

## Storage choice

SQLite is used because it is durable, transactional, portable, easy to back up as a real file, and built into Python. The database uses foreign keys, WAL journaling, constraints, and UUID primary keys. Each operation opens its own short-lived connection; writes use `BEGIN IMMEDIATE` transactions. There is no ORM or package installation requirement.

The default server listens on loopback (`127.0.0.1:8000`). The active process must be running to use the browser interface. Data remains in the SQLite file after a restart.

## Canonical truth vs. derived intelligence

**Canonical truth** is authored or explicitly confirmed information: first-captured text, current user-edited text/title, revisions, lifecycle/status timestamps, tags, relationships and their confirmation state, and events. The database has a trigger that prevents updates to `thoughts.original_text`.

**Derived/rebuildable data** is an index or interpretation: the `search_documents` table and optional `derived_artifacts` such as an AI summary. Deleting derived rows does not delete or rewrite canonical rows. Search rebuilds its local index on demand; there is also an explicit rebuild command. Derived artifacts record provider/model, time, type, and source thought IDs.

The user interface labels the immutable capture **“What I actually wrote”** and AI output **“Derived · rebuildable.”** Titles are optional user-authored canonical data. If a title is blank, the UI computes a display label from the current text without storing it.

## Search and graph are views

The local search index is a disposable text projection of original/current text, user title, and tags. Search ranks exact phrases, substrings, tokens, title/tag matches, simple word forms, and a small transparent alias map (`adapt` ↔ change/adjust/learn/evolve; enemy ↔ enemies/foe/opponent; fight ↔ combat/battle; remember ↔ memory/recall; strategy ↔ tactic/behavior). These aliases improve practical recall but are not a semantic model. There is no embedding or remote index.

The Connections screen computes a small SVG layout in the browser from saved relationship records. Node coordinates are ephemeral, are not written to SQLite, and carry no semantic meaning. The future Mind World can replace the layout without changing canonical storage.

## Import, export, and backup

The canonical JSON contract is `iwtt-vault-export`, schema version 1. Import validates references, enums, snapshots, IDs, tag uniqueness, and event targets before inserting. Duplicate IDs or conflicting tag names stop the whole transaction; it does not overwrite existing records. Import rebuilds the disposable search index.

Markdown is a readable snapshot, not the reconstruction format. SQLite backup uses the SQLite backup API to create a consistent database copy in the `backups/` directory, then sends that actual file to the browser.

## AI boundary

`iwtt/ai.py` exposes an AI provider interface and `NoAIProvider`. No endpoint is used unless `IWTT_AI_BASE_URL` is explicitly configured. A user action is still required before a thought is sent. The optional implementation supports an OpenAI-compatible chat-completions endpoint; it can point at a local service. See [AI_BOUNDARIES.md](AI_BOUNDARIES.md).

## Deliberate non-goals

No spatial-world schema, vector database, account/sync layer, cloud dependency, analytics, telemetry, encryption/key-management system, agent, or autonomous relation confirmation is introduced. v0.1 is the durable brain underneath future presentation layers—not the full Mind World.
