"""Canonical SQLite store for IWTT.

Thought text, revisions, lifecycle state, tags, relationships and events are
canonical.  Search documents, AI artifacts and — since v0.2 — the ``recall_*``
tables (lexical index, IDF term statistics, trigram index, document metadata,
graph cache, automatically discovered clusters, query-outcome memory, build logs)
are rebuildable/derived.

v0.2 changed nothing canonical.  The v0.1 retrieval path (``search()``) is frozen
byte-for-byte; Recall V2 lives beside it and is reached through ``recall()`` /
``find_my_thought()`` and the ``iwtt.recall`` package.
"""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

APP_VERSION = "0.2.0"
EXPORT_FORMAT = "iwtt-vault-export"
#: The canonical export contract is UNCHANGED in v0.2.  A v0.1 export imports into
#: v0.2 and a v0.2 canonical export imports into v0.1, because v0.2 adds no
#: canonical field.  Bumping this number would break that symmetry for no gain.
EXPORT_SCHEMA_VERSION = 1
#: v0.1 canonical schema version, preserved verbatim.
CANONICAL_SCHEMA_VERSION = "1"
#: v0.2 adds a derived recall layer only.  Its version is tracked separately so
#: the canonical schema version can keep meaning exactly what it meant in v0.1.
#: The value is owned by the recall package (a change to the derived DDL must bump
#: it there); storing it here would let the two drift apart silently.
def _recall_schema_version() -> str:
    from .recall.config import RECALL_INDEX_SCHEMA

    return str(RECALL_INDEX_SCHEMA)
THOUGHT_TYPES = {"THOUGHT", "EXPERIMENT", "INVENTION", "PROJECT"}
THOUGHT_STATUSES = {"ACTIVE", "GRAVEYARD"}
RELATIONSHIP_TYPES = {
    "INSPIRED_BY", "LED_TO", "RELATED_TO", "CONTRADICTS", "SUPPORTS",
    "DEPENDS_ON", "PART_OF", "SUPERSEDES", "REVIVES", "TESTS",
    "IMPLEMENTED_BY",
}
RELATIONSHIP_PROVENANCE = {"USER_CONFIRMED", "AI_SUGGESTED"}
RELATIONSHIP_STATUSES = {"SUGGESTED", "CONFIRMED", "REMOVED"}
TOKEN_ALIASES = {
    "adapt": {"adjust", "change", "learn", "evolve"},
    "enemy": {"enemies", "opponent", "foe"},
    "fight": {"combat", "battle", "fighting"},
    "remember": {"memory", "recall", "learn"},
    "strategy": {"tactic", "tactics", "approach", "behavior"},
}
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "could", "did", "do", "does", "for", "from", "had", "has", "have",
    "how", "i", "if", "in", "into", "is", "it", "its", "of", "on",
    "or", "our", "should", "that", "the", "their", "them", "then",
    "there", "these", "this", "those", "to", "was", "we", "were", "what",
    "when", "where", "which", "who", "will", "with", "would", "you", "your",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS thoughts (
    id TEXT PRIMARY KEY,
    original_text TEXT NOT NULL CHECK(length(trim(original_text)) > 0),
    current_text TEXT NOT NULL CHECK(length(trim(current_text)) > 0),
    title TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL CHECK(type IN ('THOUGHT','EXPERIMENT','INVENTION','PROJECT')),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','GRAVEYARD')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    graveyard_at TEXT,
    resurrected_at TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0 CHECK(is_demo IN (0,1))
);
CREATE INDEX IF NOT EXISTS thoughts_status_created_idx ON thoughts(status, created_at DESC);
CREATE INDEX IF NOT EXISTS thoughts_type_idx ON thoughts(type);
CREATE TRIGGER IF NOT EXISTS preserve_original_thought_text
BEFORE UPDATE OF original_text ON thoughts
WHEN NEW.original_text <> OLD.original_text
BEGIN
    SELECT RAISE(ABORT, 'original_text is immutable');
END;
CREATE TABLE IF NOT EXISTS revisions (
    id TEXT PRIMARY KEY,
    thought_id TEXT NOT NULL REFERENCES thoughts(id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL CHECK(revision_number >= 1),
    text TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL CHECK(type IN ('THOUGHT','EXPERIMENT','INVENTION','PROJECT')),
    created_at TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    UNIQUE(thought_id, revision_number)
);
CREATE INDEX IF NOT EXISTS revisions_thought_idx ON revisions(thought_id, revision_number DESC);
CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0 CHECK(is_demo IN (0,1))
);
CREATE TABLE IF NOT EXISTS thought_tags (
    thought_id TEXT NOT NULL REFERENCES thoughts(id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY(thought_id, tag_id)
);
CREATE INDEX IF NOT EXISTS thought_tags_tag_idx ON thought_tags(tag_id);
CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES thoughts(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES thoughts(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK(type IN (
        'INSPIRED_BY','LED_TO','RELATED_TO','CONTRADICTS','SUPPORTS',
        'DEPENDS_ON','PART_OF','SUPERSEDES','REVIVES','TESTS','IMPLEMENTED_BY'
    )),
    provenance TEXT NOT NULL CHECK(provenance IN ('USER_CONFIRMED','AI_SUGGESTED')),
    status TEXT NOT NULL CHECK(status IN ('SUGGESTED','CONFIRMED','REMOVED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    confirmed_at TEXT,
    confirmed_by TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_demo INTEGER NOT NULL DEFAULT 0 CHECK(is_demo IN (0,1)),
    CHECK(source_id <> target_id)
);
CREATE INDEX IF NOT EXISTS relationships_source_idx ON relationships(source_id, status);
CREATE INDEX IF NOT EXISTS relationships_target_idx ON relationships(target_id, status);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type IN ('THOUGHT','RELATIONSHIP','VAULT')),
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_demo INTEGER NOT NULL DEFAULT 0 CHECK(is_demo IN (0,1))
);
CREATE INDEX IF NOT EXISTS events_timestamp_idx ON events(timestamp DESC);
CREATE INDEX IF NOT EXISTS events_target_idx ON events(target_type, target_id, timestamp DESC);
CREATE TABLE IF NOT EXISTS derived_artifacts (
    id TEXT PRIMARY KEY,
    thought_id TEXT NOT NULL REFERENCES thoughts(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_thought_ids_json TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    generated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS derived_artifacts_thought_idx ON derived_artifacts(thought_id, generated_at DESC);
CREATE TABLE IF NOT EXISTS search_documents (
    thought_id TEXT PRIMARY KEY REFERENCES thoughts(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);
"""


class ValidationError(ValueError):
    """Input data is invalid."""


class ConflictError(RuntimeError):
    """An import or state transition conflicts with existing canonical data."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str | None, fallback: Any = None) -> Any:
    if value is None:
        return {} if fallback is None else fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {} if fallback is None else fallback


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"[^\W_]+", plain, flags=re.UNICODE))


def tokenize(value: str, remove_stopwords: bool = True) -> list[str]:
    tokens = normalize_text(value).split()
    if remove_stopwords:
        useful = [token for token in tokens if token not in STOPWORDS]
        if useful:
            return useful
    return tokens


def _token_variants(token: str) -> set[str]:
    """Tiny deterministic morphology/alias layer; not a semantic model."""
    variants = {token}
    if len(token) > 4 and token.endswith("ies"):
        variants.add(token[:-3] + "y")
    if len(token) > 5 and token.endswith("ing"):
        stem = token[:-3]
        variants.add(stem)
        if len(stem) > 2 and stem[-1] == stem[-2]:
            variants.add(stem[:-1])
        if not stem.endswith("e"):
            variants.add(stem + "e")
    if len(token) > 4 and token.endswith("ed"):
        stem = token[:-2]
        variants.add(stem)
        if not stem.endswith("e"):
            variants.add(stem + "e")
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        variants.add(token[:-1])
    expanded = set(variants)
    for variant in list(variants):
        expanded.update(TOKEN_ALIASES.get(variant, set()))
    return expanded


def _valid_timestamp(value: Any, field: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty ISO timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field} must be a valid ISO timestamp") from exc
    return value


def _valid_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a UUID")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"{field} must be a UUID") from exc


def _clean_title(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("title must be text")
    value = value.strip()
    if len(value) > 200:
        raise ValidationError("title must be 200 characters or fewer")
    return value


def _clean_text(value: Any, field: str = "text") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} cannot be empty")
    if len(value) > 200_000:
        raise ValidationError(f"{field} must be 200,000 characters or fewer")
    return value


def _display_title(title: str, text: str) -> str:
    if title.strip():
        return title.strip()
    words = text.strip().split()
    label = " ".join(words[:9])
    if len(words) > 9:
        label += "…"
    return label or "Untitled thought"


class Vault:
    """SQLite-backed canonical thought vault. Each operation owns its connection."""

    def __init__(self, db_path: str | Path, ai_provider: Any = None):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ai_provider = ai_provider
        self.initialize()

    @property
    def data_dir(self) -> Path:
        return self.db_path.parent

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def initialize(self) -> None:
        """Create or migrate the vault.

        MIGRATION v0.1 → v0.2 (see V0.1_TO_V0.2_MIGRATION.md)

        v0.2 makes **no canonical schema change**.  It adds derived ``recall_*``
        tables beside v0.1's existing derived tables (``search_documents``,
        ``derived_artifacts``) and records its own version markers in
        ``schema_info``.  Consequences, all tested:

          * a v0.1 vault opens unchanged, and its canonical fingerprint is
            provably identical before and after;
          * no canonical ID is regenerated, no original text is touched;
          * ``schema_info.schema_version`` stays ``'1'`` — the canonical contract;
          * rollback is "use v0.1 again": the extra tables are ignored by v0.1
            and can be dropped without loss (``clear_derived_data()`` does it).

        The derived DDL is applied inside one transaction, so a failure leaves
        the vault exactly as it was found.
        """
        from .recall.index import RECALL_SCHEMA_SQL

        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            # Both scripts are pure idempotent DDL (CREATE … IF NOT EXISTS) and
            # are run BEFORE the explicit transaction: sqlite3.executescript()
            # issues an implicit COMMIT first, which would otherwise silently end
            # the transaction the version markers are written in.
            conn.executescript(SCHEMA_SQL)
            previous_canonical = self._schema_info_value(conn, "schema_version")
            recall_schema_version = _recall_schema_version()
            previous_recall = self._schema_info_value(conn, "recall_schema_version")
            conn.executescript(RECALL_SCHEMA_SQL)
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO schema_info(key, value) VALUES('schema_version', ?)",
                    (CANONICAL_SCHEMA_VERSION,),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO schema_info(key, value) VALUES('recall_schema_version', ?)",
                    (recall_schema_version,),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO schema_info(key, value) VALUES('app_version', ?)",
                    (APP_VERSION,),
                )
                if previous_recall is None and previous_canonical is not None:
                    # First v0.2 open of an existing v0.1 vault.  Recorded so the
                    # migration is auditable after the fact.
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_info(key, value) VALUES('migrated_from_app_version', ?)",
                        ("0.1.x" if previous_canonical == CANONICAL_SCHEMA_VERSION else previous_canonical,),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_info(key, value) VALUES('migrated_at', ?)",
                        (now_utc(),),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _schema_info_value(conn: sqlite3.Connection, key: str) -> str | None:
        try:
            row = conn.execute("SELECT value FROM schema_info WHERE key=?", (key,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return row["value"] if row else None

    def schema_versions(self) -> dict[str, Any]:
        """Version markers, for the diagnostics view and the migration tests.

        ``schemaVersion`` is the CANONICAL schema version and is deliberately
        still ``"1"`` in v0.2: this release adds derived ``recall_*`` tables and
        no canonical column, so a v0.1 file and a v0.2 file are the same canonical
        schema and a v0.1 export still imports unchanged.  ``appVersion`` and
        ``recallSchemaVersion`` move independently of it.

        Keys are camelCase like every other public dictionary IWTT returns; the
        literal ``schema_info`` rows stay readable with ``schema_info_rows()``, so a
        migration can be audited against the exact stored keys without smuggling a
        snake_case blob into a public payload.
        """
        with self._connection() as conn:
            rows = conn.execute("SELECT key, value FROM schema_info ORDER BY key").fetchall()
        raw = {row["key"]: row["value"] for row in rows}
        return {
            "schemaVersion": raw.get("schema_version"),
            "canonicalSchemaVersion": raw.get("schema_version"),
            "recallSchemaVersion": raw.get("recall_schema_version"),
            "appVersion": raw.get("app_version"),
            "migratedFromAppVersion": raw.get("migrated_from_app_version"),
            "migratedAt": raw.get("migrated_at"),
        }

    def schema_info_rows(self) -> dict[str, Any]:
        """The literal ``schema_info`` table, keys and values exactly as stored.

        Deliberately snake_case, because these ARE the canonical rows: this is an
        audit view of the database, not a public API payload.
        """
        with self._connection() as conn:
            rows = conn.execute("SELECT key, value FROM schema_info ORDER BY key").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        target_type: str,
        target_id: str,
        event_type: str,
        metadata: dict[str, Any] | None = None,
        timestamp: str | None = None,
        is_demo: bool = False,
    ) -> str:
        event_id = new_id()
        conn.execute(
            "INSERT INTO events(id,target_id,target_type,event_type,timestamp,metadata_json,is_demo) "
            "VALUES(?,?,?,?,?,?,?)",
            (event_id, target_id, target_type, event_type, timestamp or now_utc(), _json(metadata or {}), int(is_demo)),
        )
        return event_id

    def _insert_revision(
        self,
        conn: sqlite3.Connection,
        thought_id: str,
        text: str,
        title: str,
        thought_type: str,
        timestamp: str,
        reason: str,
    ) -> str:
        row = conn.execute(
            "SELECT COALESCE(MAX(revision_number),0) AS n FROM revisions WHERE thought_id=?",
            (thought_id,),
        ).fetchone()
        revision_id = new_id()
        conn.execute(
            "INSERT INTO revisions(id,thought_id,revision_number,text,title,type,created_at,change_reason) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (revision_id, thought_id, int(row["n"]) + 1, text, title, thought_type, timestamp, reason),
        )
        return revision_id

    def _ensure_tag(
        self,
        conn: sqlite3.Connection,
        thought_id: str,
        name: str,
        timestamp: str,
        is_demo: bool = False,
    ) -> tuple[str, bool]:
        if not isinstance(name, str):
            raise ValidationError("tag names must be text")
        clean_name = " ".join(name.strip().split())
        if not clean_name or len(clean_name) > 40:
            raise ValidationError("tag names must contain 1–40 characters")
        normalized = normalize_text(clean_name)
        if not normalized:
            raise ValidationError("tag names must contain letters or numbers")
        tag = conn.execute("SELECT id,is_demo FROM tags WHERE normalized_name=?", (normalized,)).fetchone()
        if tag is None:
            tag_id = new_id()
            conn.execute(
                "INSERT INTO tags(id,name,normalized_name,created_at,is_demo) VALUES(?,?,?,?,?)",
                (tag_id, clean_name, normalized, timestamp, int(is_demo)),
            )
        else:
            tag_id = tag["id"]
            if not is_demo and tag["is_demo"]:
                conn.execute("UPDATE tags SET is_demo=0 WHERE id=?", (tag_id,))
        linked = conn.execute(
            "SELECT 1 FROM thought_tags WHERE thought_id=? AND tag_id=?", (thought_id, tag_id)
        ).fetchone()
        if linked:
            return tag_id, False
        conn.execute(
            "INSERT INTO thought_tags(thought_id,tag_id,created_at) VALUES(?,?,?)",
            (thought_id, tag_id, timestamp),
        )
        conn.execute("UPDATE thoughts SET updated_at=? WHERE id=?", (timestamp, thought_id))
        self._insert_event(
            conn, "THOUGHT", thought_id, "TAG_ADDED",
            {"tagId": tag_id, "tagName": clean_name}, timestamp, is_demo,
        )
        return tag_id, True

    def _index_thought(self, conn: sqlite3.Connection, thought_id: str) -> None:
        row = conn.execute(
            "SELECT original_text,current_text,title FROM thoughts WHERE id=?", (thought_id,)
        ).fetchone()
        if row is None:
            conn.execute("DELETE FROM search_documents WHERE thought_id=?", (thought_id,))
            return
        tags = conn.execute(
            "SELECT GROUP_CONCAT(t.name, ' ') AS names FROM tags t "
            "JOIN thought_tags tt ON tt.tag_id=t.id WHERE tt.thought_id=?",
            (thought_id,),
        ).fetchone()["names"] or ""
        content = "\n".join((row["original_text"], row["current_text"], row["title"], tags))
        conn.execute(
            "INSERT INTO search_documents(thought_id,content,indexed_at) VALUES(?,?,?) "
            "ON CONFLICT(thought_id) DO UPDATE SET content=excluded.content,indexed_at=excluded.indexed_at",
            (thought_id, content, now_utc()),
        )

    def _rebuild_search_index_conn(self, conn: sqlite3.Connection) -> int:
        conn.execute("DELETE FROM search_documents")
        ids = [row["id"] for row in conn.execute("SELECT id FROM thoughts")]
        for thought_id in ids:
            self._index_thought(conn, thought_id)
        return len(ids)

    def create_thought(
        self,
        original_text: str,
        thought_type: str = "THOUGHT",
        title: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        original_text = _clean_text(original_text, "originalText")
        thought_type = (thought_type or "THOUGHT").upper()
        if thought_type not in THOUGHT_TYPES:
            raise ValidationError(f"type must be one of: {', '.join(sorted(THOUGHT_TYPES))}")
        title = _clean_title(title)
        tag_names = tags or []
        if not isinstance(tag_names, list):
            raise ValidationError("tags must be a list")
        timestamp = now_utc()
        thought_id = new_id()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO thoughts(id,original_text,current_text,title,type,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'ACTIVE',?,?)",
                (thought_id, original_text, original_text, title, thought_type, timestamp, timestamp),
            )
            revision_id = self._insert_revision(
                conn, thought_id, original_text, title, thought_type, timestamp, "CAPTURED"
            )
            self._insert_event(
                conn, "THOUGHT", thought_id, "THOUGHT_CREATED",
                {"initialType": thought_type, "revisionId": revision_id}, timestamp,
            )
            for name in tag_names:
                self._ensure_tag(conn, thought_id, name, timestamp)
            self._index_thought(conn, thought_id)
        return self.get_thought(thought_id)

    def _fetch_thought_row(self, conn: sqlite3.Connection, thought_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM thoughts WHERE id=?", (thought_id,)).fetchone()
        if row is None:
            raise KeyError(f"Thought not found: {thought_id}")
        return row

    def _thought_record(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "originalText": row["original_text"],
            "currentText": row["current_text"],
            "title": row["title"],
            "displayTitle": _display_title(row["title"], row["current_text"]),
            "type": row["type"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "graveyardAt": row["graveyard_at"],
            "resurrectedAt": row["resurrected_at"],
            "isDemo": bool(row["is_demo"]),
        }

    @staticmethod
    def _revision_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "thoughtId": row["thought_id"],
            "revisionNumber": row["revision_number"], "text": row["text"],
            "title": row["title"], "type": row["type"],
            "createdAt": row["created_at"], "changeReason": row["change_reason"],
        }

    @staticmethod
    def _tag_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "name": row["name"], "createdAt": row["created_at"],
            "isDemo": bool(row["is_demo"]),
        }

    @staticmethod
    def _relationship_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "sourceId": row["source_id"], "targetId": row["target_id"],
            "type": row["type"], "provenance": row["provenance"], "status": row["status"],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "confirmedAt": row["confirmed_at"], "confirmedBy": row["confirmed_by"],
            "metadata": _json_load(row["metadata_json"]), "isDemo": bool(row["is_demo"]),
        }

    @staticmethod
    def _event_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "targetId": row["target_id"], "targetType": row["target_type"],
            "eventType": row["event_type"], "timestamp": row["timestamp"],
            "metadata": _json_load(row["metadata_json"]), "isDemo": bool(row["is_demo"]),
        }

    @staticmethod
    def _artifact_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "thoughtId": row["thought_id"], "artifactType": row["artifact_type"],
            "payload": _json_load(row["payload_json"]),
            "sourceThoughtIds": _json_load(row["source_thought_ids_json"], []),
            "provider": row["provider"], "model": row["model"], "generatedAt": row["generated_at"],
        }

    def _tags_for_thought(self, conn: sqlite3.Connection, thought_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT t.* FROM tags t JOIN thought_tags tt ON tt.tag_id=t.id "
            "WHERE tt.thought_id=? ORDER BY t.normalized_name", (thought_id,),
        ).fetchall()
        return [self._tag_record(row) for row in rows]

    def _relationship_with_endpoints(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        item = self._relationship_record(row)
        endpoints = conn.execute(
            "SELECT id,title,current_text,type,status FROM thoughts WHERE id IN (?,?)",
            (row["source_id"], row["target_id"]),
        ).fetchall()
        by_id = {endpoint["id"]: endpoint for endpoint in endpoints}
        def endpoint_record(endpoint_id: str) -> dict[str, Any] | None:
            endpoint = by_id.get(endpoint_id)
            if endpoint is None:
                return None
            return {
                "id": endpoint["id"],
                "title": _display_title(endpoint["title"], endpoint["current_text"]),
                "type": endpoint["type"], "status": endpoint["status"],
            }
        item["source"] = endpoint_record(row["source_id"])
        item["target"] = endpoint_record(row["target_id"])
        return item

    def get_thought(self, thought_id: str) -> dict[str, Any]:
        thought_id = _valid_uuid(thought_id, "thoughtId")
        with self._connection() as conn:
            row = self._fetch_thought_row(conn, thought_id)
            result = self._thought_record(row)
            result["tags"] = self._tags_for_thought(conn, thought_id)
            revision_rows = conn.execute(
                "SELECT * FROM revisions WHERE thought_id=? ORDER BY revision_number DESC", (thought_id,)
            ).fetchall()
            result["revisions"] = [self._revision_record(item) for item in revision_rows]
            relationship_rows = conn.execute(
                "SELECT * FROM relationships WHERE source_id=? OR target_id=? "
                "ORDER BY created_at DESC", (thought_id, thought_id),
            ).fetchall()
            result["relationships"] = [self._relationship_with_endpoints(conn, item) for item in relationship_rows]
            relationship_ids = [item["id"] for item in relationship_rows]
            if relationship_ids:
                marks = ",".join("?" for _ in relationship_ids)
                event_rows = conn.execute(
                    f"SELECT * FROM events WHERE (target_type='THOUGHT' AND target_id=?) "
                    f"OR (target_type='RELATIONSHIP' AND target_id IN ({marks})) ORDER BY timestamp DESC, rowid DESC",
                    [thought_id, *relationship_ids],
                ).fetchall()
            else:
                event_rows = conn.execute(
                    "SELECT * FROM events WHERE target_type='THOUGHT' AND target_id=? ORDER BY timestamp DESC, rowid DESC",
                    (thought_id,),
                ).fetchall()
            result["events"] = [self._event_record(item) for item in event_rows]
            artifact_rows = conn.execute(
                "SELECT * FROM derived_artifacts WHERE thought_id=? ORDER BY generated_at DESC", (thought_id,)
            ).fetchall()
            result["derivedArtifacts"] = [self._artifact_record(item) for item in artifact_rows]
            return result

    def list_thoughts(self, status: str = "ACTIVE", thought_type: str | None = None) -> list[dict[str, Any]]:
        status = (status or "ACTIVE").upper()
        if status not in THOUGHT_STATUSES | {"ALL"}:
            raise ValidationError("status must be ACTIVE, GRAVEYARD, or ALL")
        clauses: list[str] = []
        params: list[Any] = []
        if status != "ALL":
            clauses.append("status=?")
            params.append(status)
        if thought_type:
            normalized_type = thought_type.upper()
            if normalized_type not in THOUGHT_TYPES:
                raise ValidationError("Unknown thought type")
            clauses.append("type=?")
            params.append(normalized_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM thoughts" + where + " ORDER BY updated_at DESC, created_at DESC", params
            ).fetchall()
            result = []
            for row in rows:
                item = self._thought_record(row)
                item["tags"] = self._tags_for_thought(conn, row["id"])
                item["relationshipCount"] = conn.execute(
                    "SELECT COUNT(*) FROM relationships WHERE status IN ('CONFIRMED','SUGGESTED') "
                    "AND (source_id=? OR target_id=?)", (row["id"], row["id"])
                ).fetchone()[0]
                result.append(item)
            return result

    def update_thought(
        self,
        thought_id: str,
        current_text: str | None = None,
        title: str | None = None,
        thought_type: str | None = None,
    ) -> dict[str, Any]:
        thought_id = _valid_uuid(thought_id, "thoughtId")
        with self._transaction() as conn:
            row = self._fetch_thought_row(conn, thought_id)
            timestamp = now_utc()
            updated_text = row["current_text"] if current_text is None else _clean_text(current_text, "currentText")
            updated_title = row["title"] if title is None else _clean_title(title)
            changed_fields = []
            if updated_text != row["current_text"]:
                changed_fields.append("currentText")
            if updated_title != row["title"]:
                changed_fields.append("title")
            if changed_fields:
                conn.execute(
                    "UPDATE thoughts SET current_text=?,title=?,updated_at=? WHERE id=?",
                    (updated_text, updated_title, timestamp, thought_id),
                )
                revision_id = self._insert_revision(
                    conn, thought_id, updated_text, updated_title, row["type"], timestamp, "EDITED"
                )
                self._insert_event(
                    conn, "THOUGHT", thought_id, "THOUGHT_EDITED",
                    {"changedFields": changed_fields, "revisionId": revision_id}, timestamp, bool(row["is_demo"]),
                )
            if thought_type is not None:
                normalized_type = thought_type.upper()
                if normalized_type not in THOUGHT_TYPES:
                    raise ValidationError(f"type must be one of: {', '.join(sorted(THOUGHT_TYPES))}")
                current = conn.execute("SELECT * FROM thoughts WHERE id=?", (thought_id,)).fetchone()
                if normalized_type != current["type"]:
                    self._change_type_conn(conn, current, normalized_type, now_utc())
            self._index_thought(conn, thought_id)
        return self.get_thought(thought_id)

    def _change_type_conn(
        self, conn: sqlite3.Connection, row: sqlite3.Row, new_type: str, timestamp: str,
    ) -> None:
        old_type = row["type"]
        conn.execute("UPDATE thoughts SET type=?,updated_at=? WHERE id=?", (new_type, timestamp, row["id"]))
        revision_id = self._insert_revision(
            conn, row["id"], row["current_text"], row["title"], new_type, timestamp, "TYPE_CHANGED"
        )
        self._insert_event(
            conn, "THOUGHT", row["id"], "TYPE_CHANGED",
            {"fromType": old_type, "toType": new_type, "revisionId": revision_id}, timestamp, bool(row["is_demo"]),
        )
        if new_type == "PROJECT":
            self._insert_event(
                conn, "THOUGHT", row["id"], "PROJECT_PROMOTED",
                {"fromType": old_type, "toType": "PROJECT", "revisionId": revision_id}, timestamp,
                bool(row["is_demo"]),
            )

    def add_tag(self, thought_id: str, name: str) -> dict[str, Any]:
        thought_id = _valid_uuid(thought_id, "thoughtId")
        with self._transaction() as conn:
            row = self._fetch_thought_row(conn, thought_id)
            self._ensure_tag(conn, thought_id, name, now_utc(), bool(row["is_demo"]))
            self._index_thought(conn, thought_id)
        return self.get_thought(thought_id)

    def remove_tag(self, thought_id: str, tag_id: str) -> dict[str, Any]:
        thought_id = _valid_uuid(thought_id, "thoughtId")
        tag_id = _valid_uuid(tag_id, "tagId")
        with self._transaction() as conn:
            row = self._fetch_thought_row(conn, thought_id)
            tag = conn.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
            if tag is None:
                raise KeyError(f"Tag not found: {tag_id}")
            deleted = conn.execute(
                "DELETE FROM thought_tags WHERE thought_id=? AND tag_id=?", (thought_id, tag_id)
            ).rowcount
            if deleted:
                timestamp = now_utc()
                self._insert_event(
                    conn, "THOUGHT", thought_id, "TAG_REMOVED",
                    {"tagId": tag_id, "tagName": tag["name"]}, timestamp, bool(row["is_demo"]),
                )
                conn.execute(
                    "UPDATE thoughts SET updated_at=? WHERE id=?", (timestamp, thought_id)
                )
                self._index_thought(conn, thought_id)
        return self.get_thought(thought_id)

    def move_to_graveyard(self, thought_id: str) -> dict[str, Any]:
        thought_id = _valid_uuid(thought_id, "thoughtId")
        timestamp = now_utc()
        with self._transaction() as conn:
            row = self._fetch_thought_row(conn, thought_id)
            if row["status"] == "GRAVEYARD":
                return self._thought_record(row)
            conn.execute(
                "UPDATE thoughts SET status='GRAVEYARD',graveyard_at=?,updated_at=? WHERE id=?",
                (timestamp, timestamp, thought_id),
            )
            self._insert_event(
                conn, "THOUGHT", thought_id, "MOVED_TO_GRAVEYARD",
                {"fromStatus": "ACTIVE", "toStatus": "GRAVEYARD"}, timestamp, bool(row["is_demo"]),
            )
        return self.get_thought(thought_id)

    def resurrect(self, thought_id: str) -> dict[str, Any]:
        thought_id = _valid_uuid(thought_id, "thoughtId")
        timestamp = now_utc()
        with self._transaction() as conn:
            row = self._fetch_thought_row(conn, thought_id)
            if row["status"] == "ACTIVE":
                return self._thought_record(row)
            conn.execute(
                "UPDATE thoughts SET status='ACTIVE',resurrected_at=?,updated_at=? WHERE id=?",
                (timestamp, timestamp, thought_id),
            )
            self._insert_event(
                conn, "THOUGHT", thought_id, "RESURRECTED",
                {"fromStatus": "GRAVEYARD", "toStatus": "ACTIVE", "previousGraveyardAt": row["graveyard_at"]},
                timestamp, bool(row["is_demo"]),
            )
        return self.get_thought(thought_id)

    def _create_relationship_conn(
        self,
        conn: sqlite3.Connection,
        source_id: str,
        target_id: str,
        relationship_type: str,
        provenance: str,
        metadata: dict[str, Any] | None = None,
        timestamp: str | None = None,
        is_demo: bool = False,
    ) -> str:
        timestamp = timestamp or now_utc()
        relationship_id = new_id()
        status = "CONFIRMED" if provenance == "USER_CONFIRMED" else "SUGGESTED"
        confirmed_at = timestamp if status == "CONFIRMED" else None
        confirmed_by = "USER" if status == "CONFIRMED" else None
        conn.execute(
            "INSERT INTO relationships(id,source_id,target_id,type,provenance,status,created_at,updated_at,"
            "confirmed_at,confirmed_by,metadata_json,is_demo) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (relationship_id, source_id, target_id, relationship_type, provenance, status,
             timestamp, timestamp, confirmed_at, confirmed_by, _json(metadata or {}), int(is_demo)),
        )
        self._insert_event(
            conn, "RELATIONSHIP", relationship_id, "RELATIONSHIP_CREATED",
            {"sourceId": source_id, "targetId": target_id, "relationshipType": relationship_type,
             "provenance": provenance, "status": status}, timestamp, is_demo,
        )
        return relationship_id

    def create_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str = "RELATED_TO",
        provenance: str = "USER_CONFIRMED",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_id = _valid_uuid(source_id, "sourceId")
        target_id = _valid_uuid(target_id, "targetId")
        relationship_type = (relationship_type or "RELATED_TO").upper()
        provenance = (provenance or "USER_CONFIRMED").upper()
        if source_id == target_id:
            raise ValidationError("A thought cannot be related to itself")
        if relationship_type not in RELATIONSHIP_TYPES:
            raise ValidationError("Unknown relationship type")
        if provenance not in RELATIONSHIP_PROVENANCE:
            raise ValidationError("provenance must be USER_CONFIRMED or AI_SUGGESTED")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValidationError("relationship metadata must be an object")
        with self._transaction() as conn:
            source = self._fetch_thought_row(conn, source_id)
            self._fetch_thought_row(conn, target_id)
            relationship_id = self._create_relationship_conn(
                conn, source_id, target_id, relationship_type, provenance, metadata,
                is_demo=bool(source["is_demo"]),
            )
        return self.get_relationship(relationship_id)

    def get_relationship(self, relationship_id: str) -> dict[str, Any]:
        relationship_id = _valid_uuid(relationship_id, "relationshipId")
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM relationships WHERE id=?", (relationship_id,)).fetchone()
            if row is None:
                raise KeyError(f"Relationship not found: {relationship_id}")
            return self._relationship_with_endpoints(conn, row)

    def list_relationships(self, status: str = "LIVE") -> list[dict[str, Any]]:
        status = (status or "LIVE").upper()
        if status == "LIVE":
            filter_sql = "WHERE r.status IN ('CONFIRMED','SUGGESTED')"
            params: tuple[Any, ...] = ()
        elif status == "ALL":
            filter_sql, params = "", ()
        elif status in RELATIONSHIP_STATUSES:
            filter_sql, params = "WHERE r.status=?", (status,)
        else:
            raise ValidationError("Unknown relationship status filter")
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT r.* FROM relationships r {filter_sql} ORDER BY r.created_at DESC", params
            ).fetchall()
            return [self._relationship_with_endpoints(conn, row) for row in rows]

    def confirm_relationship(self, relationship_id: str) -> dict[str, Any]:
        relationship_id = _valid_uuid(relationship_id, "relationshipId")
        timestamp = now_utc()
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM relationships WHERE id=?", (relationship_id,)).fetchone()
            if row is None:
                raise KeyError(f"Relationship not found: {relationship_id}")
            if row["status"] == "REMOVED":
                raise ConflictError("A removed relationship cannot be confirmed")
            if row["status"] == "SUGGESTED":
                conn.execute(
                    "UPDATE relationships SET status='CONFIRMED',confirmed_at=?,confirmed_by='USER',updated_at=? "
                    "WHERE id=?", (timestamp, timestamp, relationship_id),
                )
                self._insert_event(
                    conn, "RELATIONSHIP", relationship_id, "RELATIONSHIP_CONFIRMED",
                    {"confirmedBy": "USER", "originalProvenance": row["provenance"]}, timestamp,
                    bool(row["is_demo"]),
                )
        return self.get_relationship(relationship_id)

    def remove_relationship(self, relationship_id: str) -> dict[str, Any]:
        relationship_id = _valid_uuid(relationship_id, "relationshipId")
        timestamp = now_utc()
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM relationships WHERE id=?", (relationship_id,)).fetchone()
            if row is None:
                raise KeyError(f"Relationship not found: {relationship_id}")
            if row["status"] != "REMOVED":
                conn.execute(
                    "UPDATE relationships SET status='REMOVED',updated_at=? WHERE id=?",
                    (timestamp, relationship_id),
                )
                self._insert_event(
                    conn, "RELATIONSHIP", relationship_id, "RELATIONSHIP_REMOVED",
                    {"previousStatus": row["status"], "sourceId": row["source_id"], "targetId": row["target_id"]},
                    timestamp, bool(row["is_demo"]),
                )
        return self.get_relationship(relationship_id)

    def timeline(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY timestamp DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
            result = []
            for row in rows:
                item = self._event_record(row)
                if row["target_type"] == "THOUGHT":
                    thought = conn.execute("SELECT * FROM thoughts WHERE id=?", (row["target_id"],)).fetchone()
                    if thought:
                        item["thoughtId"] = thought["id"]
                        item["itemTitle"] = _display_title(thought["title"], thought["current_text"])
                        item["thoughtType"] = thought["type"]
                        item["thoughtStatus"] = thought["status"]
                elif row["target_type"] == "RELATIONSHIP":
                    relationship = conn.execute(
                        "SELECT r.*,s.title AS stitle,s.current_text AS stext,t.title AS ttitle,t.current_text AS ttext "
                        "FROM relationships r LEFT JOIN thoughts s ON s.id=r.source_id "
                        "LEFT JOIN thoughts t ON t.id=r.target_id WHERE r.id=?", (row["target_id"],)
                    ).fetchone()
                    if relationship:
                        item["thoughtId"] = relationship["source_id"]
                        item["itemTitle"] = _display_title(relationship["stitle"] or "", relationship["stext"] or "")
                        item["contextTitle"] = _display_title(relationship["ttitle"] or "", relationship["ttext"] or "")
                        item["thoughtType"] = "RELATIONSHIP"
                item["summary"] = self._event_summary(item)
                result.append(item)
            return result

    @staticmethod
    def _event_summary(event: dict[str, Any]) -> str:
        event_type = event.get("eventType", "").upper()
        metadata = event.get("metadata") or {}
        labels = {
            "THOUGHT_CREATED": "Thought captured",
            "THOUGHT_EDITED": "Thought edited; a new revision was preserved",
            "TYPE_CHANGED": f"Type changed: {metadata.get('fromType', '—')} → {metadata.get('toType', '—')}",
            "PROJECT_PROMOTED": "Promoted to Project",
            "TAG_ADDED": f"Tag added: {metadata.get('tagName', 'tag')}",
            "TAG_REMOVED": f"Tag removed: {metadata.get('tagName', 'tag')}",
            "MOVED_TO_GRAVEYARD": "Moved to the Graveyard",
            "RESURRECTED": "Resurrected from the Graveyard",
            "RELATIONSHIP_CREATED": f"Connection created: {metadata.get('relationshipType', 'RELATED_TO')}",
            "RELATIONSHIP_CONFIRMED": "Suggested connection confirmed by you",
            "RELATIONSHIP_REMOVED": "Connection removed (history retained)",
            "AI_SUMMARY_GENERATED": "AI summary generated (derived)",
        }
        return labels.get(event_type, event_type.replace("_", " ").title())

    def stats(self) -> dict[str, Any]:
        with self._connection() as conn:
            count = lambda sql: int(conn.execute(sql).fetchone()[0])
            types = {
                row["type"]: row["n"]
                for row in conn.execute("SELECT type,COUNT(*) AS n FROM thoughts GROUP BY type")
            }
            return {
                "totalThoughts": count("SELECT COUNT(*) FROM thoughts"),
                "activeThoughts": count("SELECT COUNT(*) FROM thoughts WHERE status='ACTIVE'"),
                "graveyardThoughts": count("SELECT COUNT(*) FROM thoughts WHERE status='GRAVEYARD'"),
                "types": {kind: int(types.get(kind, 0)) for kind in sorted(THOUGHT_TYPES)},
                "relationships": count("SELECT COUNT(*) FROM relationships WHERE status='CONFIRMED'"),
                "suggestedRelationships": count("SELECT COUNT(*) FROM relationships WHERE status='SUGGESTED'"),
                "revisions": count("SELECT COUNT(*) FROM revisions"),
                "events": count("SELECT COUNT(*) FROM events"),
                "tags": count("SELECT COUNT(*) FROM tags"),
                "demoThoughts": count("SELECT COUNT(*) FROM thoughts WHERE is_demo=1"),
                "derivedArtifacts": count("SELECT COUNT(*) FROM derived_artifacts"),
                "searchDocuments": count("SELECT COUNT(*) FROM search_documents"),
                # --- v0.2 derived recall layer (additive keys; the v0.1 keys
                #     above keep their exact original meaning) ---
                "recall": self._recall_stats(conn),
            }

    @staticmethod
    def _recall_stats(conn: sqlite3.Connection) -> dict[str, Any]:
        """Derived-layer counters, read defensively so a vault that has never
        built its recall indexes still reports cleanly."""
        def safe(sql: str) -> int:
            try:
                return int(conn.execute(sql).fetchone()[0])
            except sqlite3.OperationalError:
                return 0

        try:
            built_at = conn.execute(
                "SELECT value FROM recall_meta WHERE key='built_at'"
            ).fetchone()
        except sqlite3.OperationalError:
            built_at = None
        return {
            "indexedThoughts": safe("SELECT COUNT(*) FROM recall_docs"),
            "tokenRows": safe("SELECT COUNT(*) FROM recall_tokens"),
            "trigramRows": safe("SELECT COUNT(*) FROM recall_trigrams"),
            "terms": safe("SELECT COUNT(*) FROM recall_term_stats"),
            "graphCacheRows": safe("SELECT COUNT(*) FROM recall_graph_cache"),
            "clusters": safe("SELECT COUNT(DISTINCT cluster_id) FROM recall_clusters"),
            "clusteredThoughts": safe("SELECT COUNT(*) FROM recall_clusters"),
            "queryMemoryEvents": safe("SELECT COUNT(*) FROM recall_query_memory"),
            "builtAt": built_at["value"] if built_at else None,
            "canonical": False,
        }

    def canonical_fingerprint(self) -> str:
        """Stable digest over every canonical record.

        Used to *prove* — not assert — that migration, derived-data destruction,
        export/import and restart leave the user's knowledge identical.  Derived
        tables are excluded by construction: only ``thoughts``, ``revisions``,
        ``tags``, ``thought_tags``, ``relationships`` and ``events`` are read, in
        a fixed order, so the digest is reproducible across processes.
        """
        import hashlib

        digest = hashlib.sha256()
        projections = (
            (
                "thoughts",
                "SELECT id,original_text,current_text,title,type,status,created_at,updated_at,"
                "graveyard_at,resurrected_at,is_demo FROM thoughts ORDER BY id",
            ),
            (
                "revisions",
                "SELECT id,thought_id,revision_number,text,title,type,created_at,change_reason "
                "FROM revisions ORDER BY thought_id,revision_number",
            ),
            ("tags", "SELECT id,name,normalized_name,created_at,is_demo FROM tags ORDER BY id"),
            (
                "thought_tags",
                "SELECT thought_id,tag_id,created_at FROM thought_tags ORDER BY thought_id,tag_id",
            ),
            (
                "relationships",
                "SELECT id,source_id,target_id,type,provenance,status,created_at,updated_at,"
                "confirmed_at,confirmed_by,metadata_json,is_demo FROM relationships ORDER BY id",
            ),
            (
                "events",
                "SELECT id,target_id,target_type,event_type,timestamp,metadata_json,is_demo "
                "FROM events ORDER BY id",
            ),
        )
        with self._connection() as conn:
            for name, sql in projections:
                digest.update(name.encode("utf-8"))
                digest.update(b"\x1f")
                for row in conn.execute(sql):
                    digest.update(_json(tuple(row)).encode("utf-8"))
                    digest.update(b"\x1e")
        return digest.hexdigest()

    def _ensure_search_index(self, conn: sqlite3.Connection) -> None:
        missing = conn.execute(
            "SELECT 1 FROM thoughts t LEFT JOIN search_documents d ON d.thought_id=t.id "
            "WHERE d.thought_id IS NULL LIMIT 1"
        ).fetchone()
        orphaned = conn.execute(
            "SELECT 1 FROM search_documents d LEFT JOIN thoughts t ON t.id=d.thought_id "
            "WHERE t.id IS NULL LIMIT 1"
        ).fetchone()
        if missing or orphaned:
            self._rebuild_search_index_conn(conn)

    @staticmethod
    def _fuzzy_token_score(query_token: str, candidate_tokens: set[str]) -> tuple[float, bool]:
        query_variants = _token_variants(query_token)
        if query_token in candidate_tokens:
            return 1.0, False
        if (query_variants - {query_token}) & candidate_tokens:
            return 0.90, True
        best = 0.0
        for query_variant in query_variants:
            for token in candidate_tokens:
                if len(query_variant) >= 4 and len(token) >= 4 and (token.startswith(query_variant) or query_variant.startswith(token)):
                    best = max(best, 0.86)
                    continue
                if min(len(query_variant), len(token)) >= 4:
                    ratio = difflib.SequenceMatcher(None, query_variant, token).ratio()
                    if ratio >= 0.70:
                        best = max(best, min(ratio * 0.84, 0.82))
        return best, best > 0

    @staticmethod
    def _snippet(text: str, query_tokens: list[str], limit: int = 210) -> str:
        if len(text) <= limit:
            return text
        folded = text.casefold()
        locations = [folded.find(token) for token in query_tokens if folded.find(token) >= 0]
        start = max(0, (min(locations) - 55) if locations else 0)
        end = min(len(text), start + limit)
        snippet = text[start:end].strip()
        if start:
            snippet = "…" + snippet
        if end < len(text):
            snippet += "…"
        return snippet

    def search(self, query: str, include_graveyard: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(query, str):
            raise ValidationError("query must be text")
        query = query.strip()
        if not query:
            return []
        if len(query) > 500:
            raise ValidationError("query must be 500 characters or fewer")
        q_norm = normalize_text(query)
        q_tokens = tokenize(query)
        if not q_tokens:
            q_tokens = tokenize(query, remove_stopwords=False)
        with self._transaction() as conn:
            self._ensure_search_index(conn)
            sql = (
                "SELECT t.*,d.content FROM thoughts t JOIN search_documents d ON d.thought_id=t.id"
            )
            params: list[Any] = []
            if not include_graveyard:
                sql += " WHERE t.status='ACTIVE'"
            rows = conn.execute(sql, params).fetchall()
            ranked: list[tuple[float, dict[str, Any]]] = []
            for row in rows:
                tags = self._tags_for_thought(conn, row["id"])
                tag_names = [tag["name"] for tag in tags]
                original_norm = normalize_text(row["original_text"])
                current_norm = normalize_text(row["current_text"])
                title_norm = normalize_text(row["title"])
                tags_norm = [normalize_text(tag) for tag in tag_names]
                exact_text = bool(q_norm and (q_norm in original_norm or q_norm in current_norm))
                exact_title = bool(q_norm and q_norm in title_norm)
                exact_tag = any(q_norm and q_norm in tag for tag in tags_norm)
                doc_tokens = set(tokenize(row["content"], remove_stopwords=False))
                title_tokens = set(tokenize(row["title"], remove_stopwords=False))
                tag_tokens = set(token for name in tag_names for token in tokenize(name, remove_stopwords=False))
                score_sum = 0.0
                exact_count = 0
                fuzzy_count = 0
                title_hit = False
                tag_hit = False
                for token in q_tokens:
                    score, fuzzy = self._fuzzy_token_score(token, doc_tokens)
                    if score > 0:
                        score_sum += score
                        if score >= 0.99:
                            exact_count += 1
                        else:
                            fuzzy_count += 1
                    if self._fuzzy_token_score(token, title_tokens)[0] > 0:
                        title_hit = True
                    if self._fuzzy_token_score(token, tag_tokens)[0] > 0:
                        tag_hit = True
                if exact_text:
                    score = 100.0
                    reason = "Exact text"
                else:
                    coverage = score_sum / max(1, len(q_tokens))
                    minimum_recall = 0.65 if len(q_tokens) == 1 else 1.25
                    if score_sum < minimum_recall:
                        continue
                    score = coverage * 72 + exact_count * 4 + fuzzy_count * 2
                    if exact_title or title_hit:
                        score += 12
                    if exact_tag or tag_hit:
                        score += 9
                    if exact_title or title_hit:
                        reason = "Title match"
                    elif exact_tag or tag_hit:
                        reason = "Tag match"
                    elif fuzzy_count:
                        reason = "Fuzzy recall" if exact_count == 0 else "Keyword + fuzzy"
                    else:
                        reason = "Keyword match"
                record = self._thought_record(row)
                record["tags"] = tags
                record["snippet"] = self._snippet(row["original_text"], q_tokens)
                record["matchReason"] = reason
                record["score"] = round(score, 2)
                ranked.append((score, record))
            ranked.sort(key=lambda pair: (pair[0], pair[1]["updatedAt"]), reverse=True)
            return [item for _, item in ranked[:max(1, min(int(limit), 500))]]

    def rebuild_search_index(self) -> dict[str, Any]:
        with self._transaction() as conn:
            indexed = self._rebuild_search_index_conn(conn)
        return {"indexedThoughts": indexed, "rebuiltAt": now_utc()}

    def clear_derived_data(self) -> dict[str, Any]:
        """DELETE ALL DERIVED INTELLIGENCE.

        Removes every rebuildable row v0.1 and v0.2 own:

          * ``derived_artifacts``   — AI summaries and other generated artifacts
          * ``search_documents``    — the v0.1 search projection
          * ``recall_*``            — the v0.2 derived layer: lexical index,
            term statistics/IDF, trigram index, document metadata, graph cache
            (degrees, hub factors), automatically discovered clusters, query
            memory, and build logs

        No canonical table is touched.  ``tests/test_canonical_destruction.py``
        proves the canonical export is byte-identical before and after, that the
        ``original_text`` trigger still fires, and that recall works again once
        the indexes rebuild.

        The two v0.1 return keys are preserved with their original meaning so the
        existing tests and the existing UI keep working; the v0.2 counts are added
        alongside them.
        """
        from .recall.index import RecallIndex

        with self._transaction() as conn:
            artifacts = conn.execute("SELECT COUNT(*) FROM derived_artifacts").fetchone()[0]
            indexes = conn.execute("SELECT COUNT(*) FROM search_documents").fetchone()[0]
            conn.execute("DELETE FROM derived_artifacts")
            conn.execute("DELETE FROM search_documents")
            recall_removed = RecallIndex(self).clear(conn)
        recall_total = sum(recall_removed.values())
        return {
            "derivedArtifactsDeleted": int(artifacts),
            "searchDocumentsDeleted": int(indexes),
            "recallRowsDeleted": int(recall_total),
            "recallTablesDeleted": {
                table: int(count) for table, count in sorted(recall_removed.items())
            },
            "canonicalTablesTouched": [],
            "note": (
                "All derived intelligence deleted. Canonical thoughts, original text, "
                "revisions, tags, relationships, provenance, events, lifecycle state and "
                "Graveyard/resurrection history are unchanged. Indexes rebuild themselves "
                "on the next search or recall."
            ),
        }

    def save_derived_artifact(
        self,
        thought_id: str,
        artifact_type: str,
        payload: dict[str, Any],
        provider: str,
        model: str | None,
        source_thought_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        thought_id = _valid_uuid(thought_id, "thoughtId")
        if not isinstance(payload, dict):
            raise ValidationError("derived payload must be an object")
        timestamp = now_utc()
        artifact_id = new_id()
        sources = source_thought_ids or [thought_id]
        with self._transaction() as conn:
            thought = self._fetch_thought_row(conn, thought_id)
            for source_id in sources:
                self._fetch_thought_row(conn, _valid_uuid(source_id, "sourceThoughtId"))
            conn.execute(
                "INSERT INTO derived_artifacts(id,thought_id,artifact_type,payload_json,source_thought_ids_json,"
                "provider,model,generated_at) VALUES(?,?,?,?,?,?,?,?)",
                (artifact_id, thought_id, artifact_type, _json(payload), _json(sources), provider, model, timestamp),
            )
            self._insert_event(
                conn, "THOUGHT", thought_id, "AI_SUMMARY_GENERATED",
                {"artifactId": artifact_id, "artifactType": artifact_type, "provider": provider, "model": model},
                timestamp, bool(thought["is_demo"]),
            )
        return {
            "id": artifact_id, "thoughtId": thought_id, "artifactType": artifact_type,
            "payload": payload, "sourceThoughtIds": sources, "provider": provider,
            "model": model, "generatedAt": timestamp,
        }

    def export_vault(self, include_derived: bool = False) -> dict[str, Any]:
        with self._connection() as conn:
            thoughts = []
            for row in conn.execute("SELECT * FROM thoughts ORDER BY created_at,id"):
                item = self._thought_record(row)
                item.pop("displayTitle", None)  # UI-only fallback label, never canonical data.
                thoughts.append(item)
            revisions = [self._revision_record(row) for row in conn.execute("SELECT * FROM revisions ORDER BY thought_id,revision_number")]
            tags = [self._tag_record(row) for row in conn.execute("SELECT * FROM tags ORDER BY normalized_name")]
            thought_tags = [
                {"thoughtId": row["thought_id"], "tagId": row["tag_id"], "createdAt": row["created_at"]}
                for row in conn.execute("SELECT * FROM thought_tags ORDER BY thought_id,tag_id")
            ]
            relationships = [
                self._relationship_record(row) for row in conn.execute("SELECT * FROM relationships ORDER BY created_at,id")
            ]
            events = [self._event_record(row) for row in conn.execute("SELECT * FROM events ORDER BY timestamp,rowid")]
            export: dict[str, Any] = {
                "format": EXPORT_FORMAT,
                "schemaVersion": EXPORT_SCHEMA_VERSION,
                "appVersion": APP_VERSION,
                "exportedAt": now_utc(),
                "canonical": {
                    "thoughts": thoughts,
                    "revisions": revisions,
                    "tags": tags,
                    "thoughtTags": thought_tags,
                    "relationships": relationships,
                    "events": events,
                },
            }
            if include_derived:
                export["derived"] = {
                    "artifacts": [self._artifact_record(row) for row in conn.execute(
                        "SELECT * FROM derived_artifacts ORDER BY generated_at,id"
                    )]
                }
                # v0.2: the derived recall layer is exportable too, under its own
                # key, so a move to another machine can carry its shape.  It stays
                # optional, never canonical, and excluded by default — and
                # import_vault() ignores it, because indexes are rebuilt from
                # canonical data rather than trusted from a shipped file.
                export["derived"]["recall"] = self._export_recall_derived(conn)
            return export

    @staticmethod
    def _export_recall_derived(conn: sqlite3.Connection) -> dict[str, Any]:
        """Shape and row counts of the derived recall layer — never its content.

        No verbatim thought text is duplicated into an export: the derived index
        stores tokens, counts and metadata only, and a rebuild regenerates
        everything from canonical data.
        """
        from .recall.index import RECALL_DERIVED_TABLES

        out: dict[str, Any] = {"exported": False, "tables": {}, "builtAt": None}
        try:
            built = conn.execute("SELECT value FROM recall_meta WHERE key='built_at'").fetchone()
        except sqlite3.OperationalError:
            return out
        out["builtAt"] = built["value"] if built else None
        for table in RECALL_DERIVED_TABLES:
            try:
                cursor = conn.execute(f"SELECT * FROM {table} LIMIT 0")
                columns = [d[0] for d in cursor.description]
                row_count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.OperationalError:
                continue
            out["tables"][table] = {"columns": columns, "rowCount": row_count}
        out["exported"] = bool(out["tables"])
        out["note"] = (
            "Row counts and column shapes only. The derived index is rebuilt from "
            "canonical data, never restored from an export."
        )
        return out

    def export_markdown(self) -> str:
        export = self.export_vault()
        canonical = export["canonical"]
        tag_map: dict[str, list[str]] = {}
        for item in canonical["thoughtTags"]:
            tag_map.setdefault(item["thoughtId"], []).append(item["tagId"])
        names = {tag["id"]: tag["name"] for tag in canonical["tags"]}
        thought_map = {
            thought["id"]: {
                **thought,
                "displayTitle": _display_title(thought["title"], thought["currentText"]),
            }
            for thought in canonical["thoughts"]
        }
        lines = [
            "# IWTT Thought Vault — readable Markdown export",
            "",
            f"Exported: {export['exportedAt']}",
            "",
            "> This Markdown file is for reading and portability. Import the companion JSON export to reconstruct canonical records and event history.",
            "",
        ]
        for thought in canonical["thoughts"]:
            title = _display_title(thought["title"], thought["currentText"])
            grave = " · GRAVEYARD" if thought["status"] == "GRAVEYARD" else ""
            demo = " · DEMO" if thought["isDemo"] else ""
            lines.extend([
                f"## {title}",
                "",
                f"- ID: `{thought['id']}`",
                f"- Type: **{thought['type']}**{grave}{demo}",
                f"- Created: {thought['createdAt']}",
                f"- Updated: {thought['updatedAt']}",
                f"- Tags: {', '.join(names[tag_id] for tag_id in tag_map.get(thought['id'], [])) or '—'}",
                "",
                "### Original capture (preserved)",
                "",
                thought["originalText"],
                "",
                "### Current version",
                "",
                thought["currentText"],
                "",
            ])
            if thought["graveyardAt"]:
                lines.extend([f"Graveyard history: entered {thought['graveyardAt']}; last resurrected {thought['resurrectedAt'] or 'never'}.", ""])
            revisions = [item for item in canonical["revisions"] if item["thoughtId"] == thought["id"]]
            if revisions:
                lines.extend(["### Revision history", ""])
                for revision in revisions:
                    lines.append(f"- v{revision['revisionNumber']} · {revision['createdAt']} · {revision['type']} · {revision['changeReason']}")
                lines.append("")
            rels = [item for item in canonical["relationships"] if item["sourceId"] == thought["id"] or item["targetId"] == thought["id"]]
            if rels:
                lines.extend(["### Connections", ""])
                for rel in rels:
                    other_id = rel["targetId"] if rel["sourceId"] == thought["id"] else rel["sourceId"]
                    other = thought_map.get(other_id, {})
                    lines.append(
                        f"- {rel['type']} → {other.get('displayTitle', other_id)} · {rel['status']} · {rel['provenance']} · ID `{rel['id']}`"
                    )
                lines.append("")
            lines.extend(["---", ""])
        return "\n".join(lines)

    def _validate_import(self, payload: Any) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT:
            raise ValidationError(f"Expected a {EXPORT_FORMAT} JSON document")
        if payload.get("schemaVersion") != EXPORT_SCHEMA_VERSION:
            raise ValidationError(f"Unsupported schemaVersion; expected {EXPORT_SCHEMA_VERSION}")
        canonical = payload.get("canonical")
        if not isinstance(canonical, dict):
            raise ValidationError("canonical must be an object")
        collection_names = ("thoughts", "revisions", "tags", "thoughtTags", "relationships", "events")
        collections: dict[str, list[dict[str, Any]]] = {}
        for name in collection_names:
            value = canonical.get(name)
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValidationError(f"canonical.{name} must be an array of objects")
            collections[name] = [dict(item) for item in value]

        all_ids: set[str] = set()
        def unique_id(value: Any, field: str) -> str:
            item_id = _valid_uuid(value, field)
            if item_id in all_ids:
                raise ValidationError(f"Duplicate ID in export: {item_id}")
            all_ids.add(item_id)
            return item_id

        thought_ids: set[str] = set()
        revision_rows: dict[str, list[dict[str, Any]]] = {}
        tag_ids: set[str] = set()
        normalized_tag_names: set[str] = set()
        relationship_ids: set[str] = set()
        event_ids: set[str] = set()
        for item in collections["thoughts"]:
            item["id"] = unique_id(item.get("id"), "thought.id")
            thought_ids.add(item["id"])
            item["originalText"] = _clean_text(item.get("originalText"), "thought.originalText")
            item["currentText"] = _clean_text(item.get("currentText"), "thought.currentText")
            item["title"] = _clean_title(item.get("title", ""))
            item["type"] = str(item.get("type", "")).upper()
            item["status"] = str(item.get("status", "")).upper()
            if item["type"] not in THOUGHT_TYPES or item["status"] not in THOUGHT_STATUSES:
                raise ValidationError("Thought has an unknown type or status")
            item["createdAt"] = _valid_timestamp(item.get("createdAt"), "thought.createdAt")
            item["updatedAt"] = _valid_timestamp(item.get("updatedAt"), "thought.updatedAt")
            item["graveyardAt"] = _valid_timestamp(item.get("graveyardAt"), "thought.graveyardAt", True)
            item["resurrectedAt"] = _valid_timestamp(item.get("resurrectedAt"), "thought.resurrectedAt", True)
            if item["status"] == "GRAVEYARD" and not item["graveyardAt"]:
                raise ValidationError("A Graveyard thought must include graveyardAt")
            item["isDemo"] = bool(item.get("isDemo", False))
        for item in collections["revisions"]:
            item["id"] = unique_id(item.get("id"), "revision.id")
            item["thoughtId"] = _valid_uuid(item.get("thoughtId"), "revision.thoughtId")
            item["revisionNumber"] = item.get("revisionNumber")
            if not isinstance(item["revisionNumber"], int) or item["revisionNumber"] < 1:
                raise ValidationError("revision.revisionNumber must be a positive integer")
            item["text"] = _clean_text(item.get("text"), "revision.text")
            item["title"] = _clean_title(item.get("title", ""))
            item["type"] = str(item.get("type", "")).upper()
            if item["type"] not in THOUGHT_TYPES:
                raise ValidationError("Revision has an unknown type")
            item["createdAt"] = _valid_timestamp(item.get("createdAt"), "revision.createdAt")
            if not isinstance(item.get("changeReason"), str) or not item["changeReason"]:
                raise ValidationError("revision.changeReason is required")
            revision_rows.setdefault(item["thoughtId"], []).append(item)
        if set(revision_rows) != thought_ids:
            raise ValidationError("Every thought must have at least one revision, and revisions must point to a thought")
        for thought in collections["thoughts"]:
            revisions = sorted(revision_rows[thought["id"]], key=lambda item: item["revisionNumber"])
            numbers = [item["revisionNumber"] for item in revisions]
            if numbers != list(range(1, len(numbers) + 1)):
                raise ValidationError(f"Revision numbers for {thought['id']} must be continuous from 1")
            if revisions[0]["text"] != thought["originalText"]:
                raise ValidationError("Revision 1 must preserve the original captured text")
            latest = revisions[-1]
            if (latest["text"], latest["title"], latest["type"]) != (
                thought["currentText"], thought["title"], thought["type"]
            ):
                raise ValidationError("Latest revision must match the thought's current text, title, and type")
        for item in collections["tags"]:
            item["id"] = unique_id(item.get("id"), "tag.id")
            tag_ids.add(item["id"])
            name = item.get("name")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
                raise ValidationError("Tag name must contain 1–40 characters")
            item["name"] = " ".join(name.strip().split())
            item["normalizedName"] = normalize_text(item["name"])
            if item["normalizedName"] in normalized_tag_names:
                raise ValidationError("Export contains duplicate tag names")
            normalized_tag_names.add(item["normalizedName"])
            item["createdAt"] = _valid_timestamp(item.get("createdAt"), "tag.createdAt")
            item["isDemo"] = bool(item.get("isDemo", False))
        seen_pairs: set[tuple[str, str]] = set()
        for item in collections["thoughtTags"]:
            item["thoughtId"] = _valid_uuid(item.get("thoughtId"), "thoughtTag.thoughtId")
            item["tagId"] = _valid_uuid(item.get("tagId"), "thoughtTag.tagId")
            if item["thoughtId"] not in thought_ids or item["tagId"] not in tag_ids:
                raise ValidationError("thoughtTags contains an invalid thought or tag reference")
            pair = (item["thoughtId"], item["tagId"])
            if pair in seen_pairs:
                raise ValidationError("Export contains a duplicate thought/tag link")
            seen_pairs.add(pair)
            item["createdAt"] = _valid_timestamp(item.get("createdAt"), "thoughtTag.createdAt")
        for item in collections["relationships"]:
            item["id"] = unique_id(item.get("id"), "relationship.id")
            relationship_ids.add(item["id"])
            item["sourceId"] = _valid_uuid(item.get("sourceId"), "relationship.sourceId")
            item["targetId"] = _valid_uuid(item.get("targetId"), "relationship.targetId")
            item["type"] = str(item.get("type", "")).upper()
            item["provenance"] = str(item.get("provenance", "")).upper()
            item["status"] = str(item.get("status", "")).upper()
            if item["sourceId"] not in thought_ids or item["targetId"] not in thought_ids or item["sourceId"] == item["targetId"]:
                raise ValidationError("Relationship contains an invalid thought reference")
            if item["type"] not in RELATIONSHIP_TYPES:
                raise ValidationError("Relationship has an unknown type")
            if item["provenance"] not in RELATIONSHIP_PROVENANCE or item["status"] not in RELATIONSHIP_STATUSES:
                raise ValidationError("Relationship has invalid provenance or status")
            item["createdAt"] = _valid_timestamp(item.get("createdAt"), "relationship.createdAt")
            item["updatedAt"] = _valid_timestamp(item.get("updatedAt"), "relationship.updatedAt")
            item["confirmedAt"] = _valid_timestamp(item.get("confirmedAt"), "relationship.confirmedAt", True)
            item["confirmedBy"] = item.get("confirmedBy")
            if item["confirmedBy"] not in (None, "USER"):
                raise ValidationError("confirmedBy must be USER or null")
            if item["status"] == "SUGGESTED" and item["provenance"] != "AI_SUGGESTED":
                raise ValidationError("Only AI_SUGGESTED relationships may remain unconfirmed")
            if item["status"] == "CONFIRMED" and (not item["confirmedAt"] or item["confirmedBy"] != "USER"):
                raise ValidationError("Confirmed relationships must record when and by whom they were confirmed")
            if not isinstance(item.get("metadata", {}), dict):
                raise ValidationError("relationship.metadata must be an object")
            item["isDemo"] = bool(item.get("isDemo", False))
        for item in collections["events"]:
            item["id"] = unique_id(item.get("id"), "event.id")
            event_ids.add(item["id"])
            item["targetType"] = str(item.get("targetType", "")).upper()
            item["targetId"] = _valid_uuid(item.get("targetId"), "event.targetId")
            if item["targetType"] == "THOUGHT" and item["targetId"] not in thought_ids:
                raise ValidationError("Event points to a thought that is not in the export")
            if item["targetType"] == "RELATIONSHIP" and item["targetId"] not in relationship_ids:
                raise ValidationError("Event points to a relationship that is not in the export")
            if item["targetType"] not in {"THOUGHT", "RELATIONSHIP"}:
                raise ValidationError("Only thought and relationship event targets are supported in this export")
            if not isinstance(item.get("eventType"), str) or not item["eventType"].strip():
                raise ValidationError("event.eventType is required")
            item["timestamp"] = _valid_timestamp(item.get("timestamp"), "event.timestamp")
            if not isinstance(item.get("metadata", {}), dict):
                raise ValidationError("event.metadata must be an object")
            item["isDemo"] = bool(item.get("isDemo", False))
        return collections

    def import_vault(self, payload: Any) -> dict[str, Any]:
        collections = self._validate_import(payload)
        ids_by_collection = {
            "thoughts": [item["id"] for item in collections["thoughts"]],
            "revisions": [item["id"] for item in collections["revisions"]],
            "tags": [item["id"] for item in collections["tags"]],
            "relationships": [item["id"] for item in collections["relationships"]],
            "events": [item["id"] for item in collections["events"]],
        }
        with self._transaction() as conn:
            for table, ids in ids_by_collection.items():
                if not ids:
                    continue
                marks = ",".join("?" for _ in ids)
                exists = conn.execute(f"SELECT id FROM {table} WHERE id IN ({marks}) LIMIT 1", ids).fetchone()
                if exists:
                    raise ConflictError(f"Import stopped: ID already exists in {table}: {exists['id']}")
            for tag in collections["tags"]:
                exists = conn.execute("SELECT id FROM tags WHERE normalized_name=?", (tag["normalizedName"],)).fetchone()
                if exists:
                    raise ConflictError(f"Import stopped: tag name already exists with a different ID: {tag['name']}")
            for item in collections["thoughts"]:
                conn.execute(
                    "INSERT INTO thoughts(id,original_text,current_text,title,type,status,created_at,updated_at,"
                    "graveyard_at,resurrected_at,is_demo) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (item["id"], item["originalText"], item["currentText"], item["title"], item["type"], item["status"],
                     item["createdAt"], item["updatedAt"], item["graveyardAt"], item["resurrectedAt"], int(item["isDemo"])),
                )
            for item in collections["tags"]:
                conn.execute(
                    "INSERT INTO tags(id,name,normalized_name,created_at,is_demo) VALUES(?,?,?,?,?)",
                    (item["id"], item["name"], item["normalizedName"], item["createdAt"], int(item["isDemo"])),
                )
            for item in collections["revisions"]:
                conn.execute(
                    "INSERT INTO revisions(id,thought_id,revision_number,text,title,type,created_at,change_reason) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (item["id"], item["thoughtId"], item["revisionNumber"], item["text"], item["title"],
                     item["type"], item["createdAt"], item["changeReason"]),
                )
            for item in collections["thoughtTags"]:
                conn.execute(
                    "INSERT INTO thought_tags(thought_id,tag_id,created_at) VALUES(?,?,?)",
                    (item["thoughtId"], item["tagId"], item["createdAt"]),
                )
            for item in collections["relationships"]:
                conn.execute(
                    "INSERT INTO relationships(id,source_id,target_id,type,provenance,status,created_at,updated_at,"
                    "confirmed_at,confirmed_by,metadata_json,is_demo) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item["id"], item["sourceId"], item["targetId"], item["type"], item["provenance"], item["status"],
                     item["createdAt"], item["updatedAt"], item["confirmedAt"], item["confirmedBy"],
                     _json(item.get("metadata", {})), int(item["isDemo"])),
                )
            for item in collections["events"]:
                conn.execute(
                    "INSERT INTO events(id,target_id,target_type,event_type,timestamp,metadata_json,is_demo) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (item["id"], item["targetId"], item["targetType"], item["eventType"], item["timestamp"],
                     _json(item.get("metadata", {})), int(item["isDemo"])),
                )
            indexed = self._rebuild_search_index_conn(conn)
        return {
            "imported": {name: len(items) for name, items in collections.items()},
            "searchDocumentsRebuilt": indexed,
        }

    def backup(self) -> Path:
        backup_dir = self.data_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = backup_dir / f"iwtt-backup-{stamp}.sqlite3"
        if destination.exists():
            destination = backup_dir / f"iwtt-backup-{stamp}-{new_id()[:8]}.sqlite3"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with self._connection() as source:
            target = sqlite3.connect(str(temporary))
            try:
                source.backup(target)
                target.commit()
            finally:
                target.close()
        temporary.replace(destination)
        return destination

    def demo_status(self) -> dict[str, Any]:
        with self._connection() as conn:
            thoughts = int(conn.execute("SELECT COUNT(*) FROM thoughts WHERE is_demo=1").fetchone()[0])
            relationships = int(conn.execute("SELECT COUNT(*) FROM relationships WHERE is_demo=1").fetchone()[0])
            return {"loaded": thoughts > 0, "thoughts": thoughts, "relationships": relationships}

    def _seed_thought(
        self,
        conn: sqlite3.Connection,
        original_text: str,
        title: str,
        thought_type: str,
        created_at: str,
        tags: list[str],
    ) -> str:
        thought_id = new_id()
        conn.execute(
            "INSERT INTO thoughts(id,original_text,current_text,title,type,status,created_at,updated_at,is_demo) "
            "VALUES(?,?,?,?,?,'ACTIVE',?,?,1)",
            (thought_id, original_text, original_text, title, thought_type, created_at, created_at),
        )
        revision_id = self._insert_revision(
            conn, thought_id, original_text, title, thought_type, created_at, "CAPTURED"
        )
        self._insert_event(
            conn, "THOUGHT", thought_id, "THOUGHT_CREATED",
            {"initialType": thought_type, "revisionId": revision_id}, created_at, True,
        )
        for tag in tags:
            self._ensure_tag(conn, thought_id, tag, created_at, True)
        return thought_id

    def _seed_type_change(self, conn: sqlite3.Connection, thought_id: str, new_type: str, timestamp: str) -> None:
        row = self._fetch_thought_row(conn, thought_id)
        self._change_type_conn(conn, row, new_type, timestamp)

    def _seed_edit(self, conn: sqlite3.Connection, thought_id: str, text: str, timestamp: str) -> None:
        row = self._fetch_thought_row(conn, thought_id)
        conn.execute("UPDATE thoughts SET current_text=?,updated_at=? WHERE id=?", (text, timestamp, thought_id))
        revision_id = self._insert_revision(conn, thought_id, text, row["title"], row["type"], timestamp, "EDITED")
        self._insert_event(
            conn, "THOUGHT", thought_id, "THOUGHT_EDITED",
            {"changedFields": ["currentText"], "revisionId": revision_id}, timestamp, True,
        )

    def _seed_graveyard(self, conn: sqlite3.Connection, thought_id: str, timestamp: str) -> None:
        row = self._fetch_thought_row(conn, thought_id)
        conn.execute(
            "UPDATE thoughts SET status='GRAVEYARD',graveyard_at=?,updated_at=? WHERE id=?",
            (timestamp, timestamp, thought_id),
        )
        self._insert_event(
            conn, "THOUGHT", thought_id, "MOVED_TO_GRAVEYARD",
            {"fromStatus": "ACTIVE", "toStatus": "GRAVEYARD"}, timestamp, bool(row["is_demo"]),
        )

    def _seed_resurrect(self, conn: sqlite3.Connection, thought_id: str, timestamp: str) -> None:
        row = self._fetch_thought_row(conn, thought_id)
        conn.execute(
            "UPDATE thoughts SET status='ACTIVE',resurrected_at=?,updated_at=? WHERE id=?",
            (timestamp, timestamp, thought_id),
        )
        self._insert_event(
            conn, "THOUGHT", thought_id, "RESURRECTED",
            {"fromStatus": "GRAVEYARD", "toStatus": "ACTIVE", "previousGraveyardAt": row["graveyard_at"]},
            timestamp, bool(row["is_demo"]),
        )

    def seed_demo(self) -> dict[str, Any]:
        existing = self.demo_status()
        if existing["loaded"]:
            return {"seeded": False, **existing}
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM thoughts WHERE is_demo=1 LIMIT 1").fetchone():
                return {"seeded": False, **self.demo_status()}
            adaptive = self._seed_thought(
                conn,
                "Could game enemies remember how the player fights and change their strategy later?",
                "An enemy that learns your rhythm", "THOUGHT", "2024-03-15T09:20:00Z",
                ["adaptive gameplay", "enemy behavior"],
            )
            self._seed_type_change(conn, adaptive, "EXPERIMENT", "2024-03-18T11:00:00Z")
            self._seed_edit(
                conn, adaptive,
                "Prototype one enemy that remembers the player's favorite approach, then switches tactics after repeated encounters.",
                "2024-03-23T16:45:00Z",
            )
            self._seed_type_change(conn, adaptive, "INVENTION", "2024-05-06T14:00:00Z")
            self._seed_type_change(conn, adaptive, "PROJECT", "2024-07-01T10:30:00Z")

            experiment = self._seed_thought(
                conn,
                "What if enemies remembered the places where a player hides instead of copying every move?",
                "Memory without mind-reading", "EXPERIMENT", "2025-01-12T13:10:00Z",
                ["adaptive gameplay", "experiments"],
            )
            abandoned = self._seed_thought(
                conn,
                "Build a branching dialogue tree with hundreds of hand-written lines for every possible player choice.",
                "The impossible dialogue tree", "THOUGHT", "2024-09-04T08:00:00Z",
                ["narrative", "scope"],
            )
            self._seed_graveyard(conn, abandoned, "2025-02-20T12:00:00Z")

            revived = self._seed_thought(
                conn,
                "What if familiar places kept faint traces of earlier visits, like a path worn into grass?",
                "A world that remembers visits", "THOUGHT", "2022-11-02T10:15:00Z",
                ["world building", "memory"],
            )
            self._seed_graveyard(conn, revived, "2023-04-11T17:30:00Z")
            self._seed_resurrect(conn, revived, "2025-10-02T09:05:00Z")

            reward_often = self._seed_thought(
                conn,
                "Give the player small rewards often so every session feels lively and generous.",
                "Frequent rewards", "THOUGHT", "2025-05-18T15:25:00Z", ["game feel", "rewards"],
            )
            reward_rare = self._seed_thought(
                conn,
                "Keep rewards rare and surprising; too many prizes can make discovery feel ordinary.",
                "Protect the value of discovery", "THOUGHT", "2025-05-21T11:10:00Z", ["game feel", "rewards"],
            )
            world_sound = self._seed_thought(
                conn,
                "Could the sound of rain slowly become a musical instrument that reflects the player's mood?",
                "Rain that becomes music", "INVENTION", "2025-08-09T19:40:00Z", ["audio", "world building"],
            )

            self._create_relationship_conn(
                conn, adaptive, experiment, "LED_TO", "USER_CONFIRMED",
                {"note": "The prototype grew out of the original question."}, "2025-01-12T13:20:00Z", True,
            )
            self._create_relationship_conn(
                conn, adaptive, revived, "RELATED_TO", "AI_SUGGESTED",
                {"note": "Demo-only unconfirmed proposal. It is not user-confirmed knowledge."},
                "2025-10-03T09:00:00Z", True,
            )
            self._create_relationship_conn(
                conn, reward_often, reward_rare, "CONTRADICTS", "USER_CONFIRMED",
                {"note": "A useful creative tension, not necessarily a mistake."}, "2025-05-22T10:00:00Z", True,
            )
            self._create_relationship_conn(
                conn, revived, world_sound, "INSPIRED_BY", "USER_CONFIRMED",
                {"note": "The world retaining traces could shape its soundscape."}, "2025-10-04T12:00:00Z", True,
            )
            for row in conn.execute("SELECT id FROM thoughts WHERE is_demo=1"):
                self._index_thought(conn, row["id"])
        return {"seeded": True, **self.demo_status()}

    def remove_demo(self) -> dict[str, int]:
        with self._transaction() as conn:
            thought_ids = [row["id"] for row in conn.execute("SELECT id FROM thoughts WHERE is_demo=1")]
            if not thought_ids:
                return {"thoughtsRemoved": 0, "relationshipsRemoved": 0, "eventsRemoved": 0}
            marks = ",".join("?" for _ in thought_ids)
            relationship_ids = [row["id"] for row in conn.execute(
                f"SELECT id FROM relationships WHERE is_demo=1 OR source_id IN ({marks}) OR target_id IN ({marks})",
                [*thought_ids, *thought_ids],
            )]
            rel_marks = ",".join("?" for _ in relationship_ids)
            event_conditions = ["is_demo=1", f"(target_type='THOUGHT' AND target_id IN ({marks}))"]
            event_params: list[Any] = [*thought_ids]
            if relationship_ids:
                event_conditions.append(f"(target_type='RELATIONSHIP' AND target_id IN ({rel_marks}))")
                event_params.extend(relationship_ids)
            events_removed = conn.execute(
                "DELETE FROM events WHERE " + " OR ".join(event_conditions), event_params
            ).rowcount
            conn.execute("DELETE FROM relationships WHERE source_id IN (" + marks + ") OR target_id IN (" + marks + ") OR is_demo=1", [*thought_ids, *thought_ids])
            conn.execute("DELETE FROM thoughts WHERE is_demo=1")
            conn.execute(
                "UPDATE tags SET is_demo=0 WHERE is_demo=1 AND id IN (SELECT tag_id FROM thought_tags)"
            )
            conn.execute("DELETE FROM tags WHERE is_demo=1 AND id NOT IN (SELECT tag_id FROM thought_tags)")
            return {
                "thoughtsRemoved": len(thought_ids),
                "relationshipsRemoved": len(relationship_ids),
                "eventsRemoved": int(events_removed),
            }

    # ---------------------------------------------------------------- v0.2 --
    # Recall & Graph Intelligence — public surface.
    #
    # Everything below is ADDITIVE.  ``search()`` above is the frozen v0.1
    # retrieval path and is unchanged; these methods reach a separate engine that
    # reads the same canonical vault and writes only ``recall_*`` derived tables.
    #
    # The engine is created lazily and ``iwtt.recall`` is imported lazily, so a
    # user who never touches Recall pays nothing and ``store.py`` keeps no
    # module-level dependency on the recall package (the package imports store).
    # ------------------------------------------------------------------

    def _recall_engine(self, config: Any | None = None) -> Any:
        from .recall.engine import RecallEngine

        engine = getattr(self, "_recall_engine_instance", None)
        if engine is None or config is not None:
            engine = RecallEngine(self, config=config)
            if config is None:
                self._recall_engine_instance = engine
        return engine

    @property
    def recall_engine(self) -> Any:
        """The lazily created Recall V2 engine bound to this vault."""
        return self._recall_engine()

    def recall(
        self,
        query: str,
        *,
        limit: int | None = None,
        include_graveyard: bool | None = None,
        types: Sequence[str] | None = None,
        tags: Sequence[str] | None = None,
        tag_mode: str | None = None,
        statuses: Sequence[str] | None = None,
        time_expression: str | None = None,
        time_field: str | None = None,
        include_demo: bool | None = None,
        now: datetime | None = None,
        explain: bool = True,
        record_opened: bool = False,
        config: Any | None = None,
    ) -> Any:
        """Recall V2: ranked, deterministic, explainable retrieval.

        Returns a ``RecallOutcome`` with ``.results``, ``.state``, ``.message``,
        ``.graph``, ``.ambiguity``, ``.timings_ms`` and ``.honesty``.

        ``include_graveyard=False`` narrows to ACTIVE thoughts.  Graveyard
        thoughts are otherwise *annotated*, never silently dropped: a buried
        thought is still the user's thought, and hiding it would be a retrieval
        decision masquerading as a lifecycle one.
        """
        return self._recall_engine(config).recall(
            query,
            limit=limit,
            include_graveyard=include_graveyard,
            types=types,
            tags=tags,
            tag_mode=tag_mode,
            statuses=statuses,
            time_expression=time_expression,
            time_field=time_field,
            include_demo=include_demo,
            now=now,
            explain=explain,
            record_opened=record_opened,
        )

    def find_my_thought(
        self,
        query: str,
        *,
        limit: int | None = None,
        include_graveyard: bool | None = None,
        now: datetime | None = None,
        config: Any | None = None,
    ) -> dict[str, Any]:
        """The FIND MY THOUGHT entry point, returning a JSON-ready payload."""
        return self._recall_engine(config).find_my_thought(
            query, limit=limit, include_graveyard=include_graveyard, now=now
        )

    def recall_diagnostics(
        self, query: str, thought_id: str | None = None, *, now: datetime | None = None,
        config: Any | None = None,
    ) -> dict[str, Any]:
        """Full scoring breakdown — the development/debugging view."""
        from .recall.diagnostics import diagnose

        return diagnose(self._recall_engine(config), query, thought_id=thought_id, now=now)

    def record_recall_feedback(
        self, *, query: str, thought_id: str, feedback: str,
        rank: int | None = None, score: float | None = None, note: str = "",
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        """Record a DERIVED query-outcome signal.

        ``feedback`` ∈ USEFUL / NOT_USEFUL / OPENED / CORRECTION.  This writes one
        row to ``recall_query_memory`` and nothing else: it never touches a
        canonical table, never confirms an AI-suggested relationship, and is
        erased by ``reset_recall_learning()`` or ``clear_derived_data()``.
        """
        return self._recall_engine().record_feedback(
            query, thought_id, feedback, rank=rank, score=score, note=note,
            recorded_at=recorded_at,
        )

    def reset_recall_learning(self) -> Any:
        return self._recall_engine().reset_learning()

    def recall_learning_stats(self) -> dict[str, Any]:
        return self._recall_engine().learning_stats()

    def recall_learning_history(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._recall_engine().learning_history(limit=limit)

    def learned_queries(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._recall_engine().learned_queries(limit=limit)

    def external_foundation(self) -> Any:
        """The external-knowledge foundation: interfaces and documentation only.

        Creates no storage and no entities.  See EXTERNAL_KNOWLEDGE_ARCHITECTURE.md
        for the chain this reserves room for (Thought -> Experiment -> Project ->
        Source File -> Function -> Bug -> Fix -> New Thought) and for why none of it
        is built in v0.2.
        """
        from .recall.external import foundation_payload

        return foundation_payload()

    def rebuild_recall_indexes(self, *, full: bool = True, rebuild_clusters: bool = True) -> dict[str, Any]:
        return self._recall_engine().rebuild_indexes(full=full, rebuild_clusters=rebuild_clusters)

    def recall_index_stats(self) -> dict[str, Any]:
        return self._recall_engine().index_stats()

    def recall_config(self) -> dict[str, Any]:
        """Every scoring weight, cap, threshold and boundary, for transparency."""
        return self._recall_engine().config_dict()

    def find_path(self, source_id: str, target_id: str, *, directed: bool = True) -> dict[str, Any]:
        from .recall.paths import PathResult

        result: PathResult = self._recall_engine().find_path(source_id, target_id, directed=directed)
        return result.to_dict()

    def thought_lineage(self, thought_id: str, *, max_depth: int = 3) -> dict[str, Any]:
        return self._recall_engine().lineage(thought_id, max_depth=max_depth)

    def thought_neighbourhood(self, thought_id: str, *, depth: int = 1) -> dict[str, Any]:
        return self._recall_engine().neighbourhood(thought_id, depth=depth)

    def graph_view_model(
        self, *, root_ids: Sequence[str] | None = None, max_nodes: int = 200,
        include_graveyard: bool = True, include_suggested: bool = True,
    ) -> dict[str, Any]:
        """A bounded, derived GraphViewModel — never the whole vault in memory."""
        return self._recall_engine().graph_view_model(
            root_ids=list(root_ids) if root_ids else None,
            max_nodes=max_nodes,
            include_graveyard=include_graveyard,
            include_suggested=include_suggested,
        )

    def clusters(self) -> list[dict[str, Any]]:
        """Automatically discovered, DERIVED groupings (never canonical)."""
        return self._recall_engine().clusters()

    def delete_all_derived_intelligence(self) -> dict[str, Any]:
        """Explicit, product-facing name for ``clear_derived_data()``.

        The mission's destructive test calls this.  It is the same operation:
        every derived row v0.1 and v0.2 own is deleted, and canonical history is
        untouched.
        """
        return self.clear_derived_data()
