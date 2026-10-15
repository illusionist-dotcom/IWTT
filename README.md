# IWTT v0.1 — Thought Vault

**Illusionist World of Thoughts & Tricks (IWTT)** is a local-first memory system for original thoughts, ideas, experiments, inventions, and projects. v0.1 establishes the durable data foundation; it is deliberately not the full visual Mind World.

> **Core rule:** the author's original capture is immutable. User edits are versioned. AI output, if explicitly enabled, is stored separately as derived data.

## Run locally

Requirements: Python 3.10+ (tested with the Python standard library only). No package installation, cloud account, or internet connection is needed for core use.

```bash
cd IWTT-v0.1-Thought-Vault
python -m iwtt.server
```

Open <http://127.0.0.1:8000>. The default bind is loopback-only. Stop with Ctrl+C.

Optional data directory override:

```bash
IWTT_DATA_DIR="$HOME/my-iwtt-vault" python -m iwtt.server
```

Default database: `~/.iwtt/thought-vault.sqlite3` (SQLite, WAL mode). Backups are kept in `~/.iwtt/backups/` and are also downloaded by the browser. The database is created on first run. See [PRIVACY.md](PRIVACY.md) before changing the bind address.

## What works in v0.1

- Fast thought capture with a durable UUID, UTC timestamps, initial revision, and `THOUGHT_CREATED` event.
- Immutable original text plus editable current text/title, version snapshots, and a separate lifecycle type (`THOUGHT`, `EXPERIMENT`, `INVENTION`, `PROJECT`). Any type can move to any other type.
- Tags; a non-destructive Graveyard and resurrection; persistent relationships with user-confirmed vs. AI-suggested provenance and explicit confirmation.
- Local exact, substring, token, title/tag, fuzzy-word-form, and small transparent alias search. Results show the original text snippet. No embedding model is required.
- Event timeline, revision history, a small derived SVG relationship graph, vault JSON export/import, Markdown export, and actual SQLite backup files.
- Optional fictional demo records that can be removed separately.
- Rebuildable local search documents and a `Delete all derived data` safety operation.
- Optional AI provider boundary. No AI is configured by default. An OpenAI-compatible endpoint is contacted only after explicit configuration and an explicit AI action.

## Use the app

- **Capture:** type and save first; title/tags/type are optional.
- **Vault / Search:** browse active items or search a fragment. Search includes the Graveyard by default.
- **Thought detail:** inspect the original and current text separately; edit, promote, tag, connect, or inspect revisions/events.
- **Graveyard:** items remain recoverable; resurrecting records a new event and retains the previous Graveyard timestamp.
- **Connections:** browse saved relationships. Suggested links are visually marked and are not treated as confirmed until you press **Confirm**.
- **Timeline:** explore dated lifecycle, edit, tag, and relationship events.
- **Data & privacy:** export/import, backup, demo data, rebuild/delete derived data, and inspect local storage path.

A keyboard shortcut **N** opens Capture; **Ctrl/Cmd+K** focuses global search.

## Data boundaries

Canonical truth lives in SQLite tables for thoughts, revisions, tags, tag assignments, relationships, and events. `search_documents` and `derived_artifacts` can be rebuilt or deleted. Search coordinates and graph positions are never stored as thought meaning. Details are in [ARCHITECTURE.md](ARCHITECTURE.md), [DATA_MODEL.md](DATA_MODEL.md), and [EVENT_MODEL.md](EVENT_MODEL.md).

## Tests

```bash
python -m unittest discover -s tests -v
python scripts/verify_release.py
```

The tests use temporary SQLite files and leave your personal vault untouched.

## Project files

- `iwtt/store.py` — SQLite schema, canonical operations, local recall, import/export, backup, demo records.
- `iwtt/server.py` — standard-library HTTP API and static web server.
- `iwtt/ai.py` — no-AI fallback and opt-in OpenAI-compatible provider interface.
- `web/` — self-contained browser interface; no external CSS, JS, fonts, or image dependencies.
- `tests/` — automated unit and data-safety tests.
- `docs/` — schema, event, privacy, AI, export, and future-world documentation.

## Honest v0.1 limits

No embeddings/semantic model, sync, encryption-at-rest layer, user accounts, multi-user access control, mobile client, full infinite/3D Mind World, or direct database restore/import from arbitrary SQLite files. Search is local lexical/fuzzy recall plus a small curated word-alias list—not AI understanding. The server has no authentication; keep it bound to `127.0.0.1` unless you deliberately add network access controls. See each architecture and privacy document for more.
