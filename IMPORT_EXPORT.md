# IWTT Import, Export, and Backup

## Canonical JSON export

Use **Data & privacy → Export canonical JSON**. The downloaded file is an `iwtt-vault-export` document with `schemaVersion: 1`. It contains enough canonical data to reconstruct:

- thoughts (including original/current text, lifecycle, timestamps, and demo marker)
- every revision snapshot
- tag vocabulary and thought/tag assignments
- relationships (IDs, endpoints, type, provenance, status, confirmation metadata)
- events and event metadata

Search documents and AI artifacts are derived and are excluded from the normal export. The API can include derived artifacts separately with `GET /api/data/export.json?include_derived=1`; import remains focused on canonical data and rebuilds its local search index.

The JSON is UTF-8, human-inspectable, and portable. UUIDs are preserved. `exportedAt` is a wrapper timestamp, not a thought modification.

## Import instructions

1. In **Data & privacy**, choose **Import JSON**.
2. Select an IWTT JSON export.
3. Confirm the additive import.
4. IWTT validates the whole document before writing.

Import checks format/schema version, UUIDs, duplicate IDs, thought/revision consistency, enum values, tag name collisions, relationship endpoints, tag assignments, and event targets. Writes happen in one SQLite transaction. If an ID already exists or a tag name collides with a different ID, the import stops without overwriting or partially importing records. Import is not a merge/replace operation. To restore a full SQLite backup, use a copy of the database file while the app is stopped; arbitrary databases are not imported through the JSON endpoint.

## Markdown export

**Export Markdown** downloads one readable file with the original capture, current version, tags, lifecycle state, revision list, and relationships for each thought. It is intended for reading, printing, and independent archival. It is not the canonical round-trip format; use the JSON to reconstruct event metadata and all records.

## SQLite backup

**Backup SQLite** calls SQLite's online backup API to create a consistent `.sqlite3` copy inside `<data-directory>/backups/`, then downloads that actual file to the browser. Keep a copy somewhere outside the machine for disaster recovery. The browser's download destination is controlled by the browser; IWTT cannot verify where you store it.

## Example

`sample-export/` in the companion sample bundle contains fictional JSON and Markdown examples plus this format guidance. No real personal vault data is included.
