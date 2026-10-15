# IWTT v0.1 Testing and Verification

## Automated tests

Run from the project root:

```bash
python -m unittest discover -s tests -v
```

Tests use temporary directories and SQLite databases; they do not access the user's normal vault. They cover thought creation/UUIDs, original immutability, revision history, lifecycle changes, Graveyard/resurrection, relationship provenance and confirmation, events, local exact/fuzzy search, JSON round-trip and duplicate-import protection, malformed references, SQLite backup integrity, and deletion of all derived data while canonical records survive.

## Release workflow

```bash
python scripts/verify_release.py
```

The verification script performs a multi-step end-to-end workflow in an isolated temporary vault, exports/imports to a fresh vault, compares canonical records, deletes derived rows, restarts the `Vault` object against the same database, and verifies persistence and recall.

## Manual UI smoke test

1. Start `python -m iwtt.server` and open the local URL.
2. Capture a thought; refresh the page and confirm it remains.
3. Edit the thought and change its type; inspect original text and saved snapshots.
4. Add two thoughts and create a confirmed link plus an unconfirmed proposal; confirm the proposal only when ready.
5. Move a thought to the Graveyard and resurrect it.
6. Search by a paraphrased fragment and inspect the preserved original snippet.
7. Open Timeline and Connections.
8. Export JSON and Markdown; import JSON into a fresh data directory.
9. Create/download a backup; delete derived data and confirm canonical counts remain.
10. Restart the server and revisit the vault.

## Scope of verification

The suite proves local persistence and data-layer invariants. It is not a browser automation suite, multi-user security audit, accessibility certification, encryption test, or performance benchmark for very large vaults.
