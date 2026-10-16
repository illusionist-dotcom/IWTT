"""v0.1 → v0.2 migration, tested against actual v0.1-format data.

``v01_vault()`` below writes a database file that is a genuine v0.1 vault: the
v0.1 canonical DDL (which v0.2 does not modify), ``schema_info.schema_version =
'1'``, canonical rows inserted the way v0.1 inserts them, v0.1's
``search_documents`` projection, and **no** ``recall_*`` table at all.  Opening
that file with the v0.2 ``Vault`` is therefore a real migration, not a simulation.

What must hold:

  * every canonical ID survives — nothing is regenerated;
  * every ``original_text`` survives byte-for-byte;
  * ``schema_info.schema_version`` stays ``'1'`` (the canonical contract did not
    change), while ``recall_schema_version`` is added alongside it;
  * v0.1 ``search()`` returns exactly what it returned before the migration;
  * the export is unchanged by the migration;
  * rollback works: dropping the ``recall_*`` tables leaves a file v0.1 can open,
    because v0.1 never reads them.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iwtt.recall.index import RECALL_DERIVED_TABLES
from iwtt.store import SCHEMA_SQL, CANONICAL_SCHEMA_VERSION, Vault


def _uuid() -> str:
    return str(uuid.uuid4())


def _stamp(offset_seconds: int) -> str:
    moment = datetime(2026, 3, 14, 9, 30, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def v01_vault(path: Path) -> dict[str, object]:
    """Create a byte-faithful v0.1 vault file and return the ids it contains.

    Every row satisfies v0.1's own invariants — including the one ``get_thought``
    enforces, that the latest revision matches the thought's current text, title
    and type — so this is a file v0.1 would actually have produced.
    """
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(SCHEMA_SQL)          # identical in v0.1 and v0.2
    connection.execute(
        "INSERT OR REPLACE INTO schema_info(key, value) VALUES('schema_version', '1')"
    )

    # (id, original_text, current_text, title, type, status, offset_seconds)
    enemy_id, difficulty_id, buried_id = _uuid(), _uuid(), _uuid()
    enemy_original = "Enemies should remember how the player fights and adapt."
    difficulty_original = "A difficulty curve that punishes adaptation feels unfair."
    buried_original = "Abandoned: turn-based combat prototype."
    thoughts = [
        # No title on the first thought, so the v0.1 fuzzy path is the one exercised
        # by the "enemy adapting player" contract assertion below.
        (enemy_id, enemy_original, enemy_original, "", "THOUGHT", "ACTIVE", 0, 1),
        (difficulty_id, difficulty_original, difficulty_original + " Updated in v0.1.",
         "Adaptive difficulty", "THOUGHT", "ACTIVE", 60, 2),
        (buried_id, buried_original, buried_original, "Turn-based", "THOUGHT", "GRAVEYARD", 120, 1),
    ]
    # v0.1 writes tag names into the projection, so the fixture must too.
    tag_names = {enemy_id: "combat design"}
    for thought_id, original, current, title, kind, status, offset, revision_count in thoughts:
        created = _stamp(offset)
        connection.execute(
            "INSERT INTO thoughts(id, original_text, current_text, title, type, status, created_at, "
            "updated_at, graveyard_at, resurrected_at, is_demo) VALUES(?,?,?,?,?,?,?,?,?,?,0)",
            (thought_id, original, current, title, kind, status, created, created,
             created if status == "GRAVEYARD" else None, None),
        )
        # revision 1 is the capture; a second revision exists exactly where v0.1
        # would have written one, and the LAST revision matches current_text.
        texts = [original] if revision_count == 1 else [original, current]
        for number, text in enumerate(texts, start=1):
            connection.execute(
                "INSERT INTO revisions(id, thought_id, revision_number, text, title, type, "
                "created_at, change_reason) VALUES(?,?,?,?,?,?,?,?)",
                (_uuid(), thought_id, number, text, title, kind, _stamp(offset + number),
                 "CAPTURED" if number == 1 else "EDITED"),
            )
        connection.execute(
            "INSERT INTO events(id, target_id, target_type, event_type, timestamp, metadata_json, is_demo) "
            "VALUES(?,?,?,?,?,?,0)",
            (_uuid(), thought_id, "THOUGHT", "THOUGHT_CAPTURED", created, json.dumps({})),
        )
        # v0.1 search projection: "\n".join(original, current, title, tag names)
        content = "\n".join((original, current, title, tag_names.get(thought_id, "")))
        connection.execute(
            "INSERT INTO search_documents(thought_id, content, indexed_at) VALUES(?,?,?)",
            (thought_id, content, created),
        )

    tag_id = _uuid()
    connection.execute(
        "INSERT INTO tags(id, name, normalized_name, created_at, is_demo) VALUES(?,?,?,?,0)",
        (tag_id, "combat design", "combat design", _stamp(0)),
    )
    connection.execute(
        "INSERT INTO thought_tags(thought_id, tag_id, created_at) VALUES(?,?,?)",
        (enemy_id, tag_id, _stamp(0)),
    )
    relationship_id = _uuid()
    connection.execute(
        "INSERT INTO relationships(id, source_id, target_id, type, provenance, status, created_at, "
        "updated_at, confirmed_at, confirmed_by, metadata_json, is_demo) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)",
        (relationship_id, enemy_id, difficulty_id, "LED_TO", "AI_SUGGESTED", "SUGGESTED",
         _stamp(30), _stamp(30), None, None, json.dumps({})),
    )
    connection.commit()
    connection.close()
    return {
        "thoughtIds": [enemy_id, difficulty_id, buried_id],
        "originals": [enemy_original, difficulty_original, buried_original],
        "tagId": tag_id,
        "relationshipId": relationship_id,
    }


CANONICAL_PROJECTIONS = (
    ("thoughts", "SELECT * FROM thoughts ORDER BY id"),
    ("revisions", "SELECT * FROM revisions ORDER BY id"),
    ("tags", "SELECT * FROM tags ORDER BY id"),
    ("thought_tags", "SELECT * FROM thought_tags ORDER BY thought_id, tag_id"),
    ("relationships", "SELECT * FROM relationships ORDER BY id"),
    ("events", "SELECT * FROM events ORDER BY id"),
)


def raw_snapshot(path: Path) -> dict[str, object]:
    """Read the file with raw sqlite3 — no IWTT code involved.

    ``Vault.__init__`` migrates on construction, so the only way to observe a
    genuine pre-migration v0.1 file is to bypass the library entirely.  This
    snapshot is the evidence the migration is compared against.
    """
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        schema_info = {
            row["key"]: row["value"]
            for row in connection.execute("SELECT key, value FROM schema_info")
        }
        canonical = {
            name: [tuple(row) for row in connection.execute(sql)]
            for name, sql in CANONICAL_PROJECTIONS
        }
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        search_documents = connection.execute(
            "SELECT COUNT(*) FROM search_documents"
        ).fetchone()[0]
    finally:
        connection.close()
    return {
        "tables": tables,
        "schemaInfo": schema_info,
        "canonical": canonical,
        "integrity": integrity,
        "searchDocuments": int(search_documents),
    }


class MigrationV01ToV02TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "thought-vault.sqlite3"
        self.fixture = v01_vault(self.db_path)
        # Pre-migration evidence, captured before any IWTT code opens the file.
        self.before = raw_snapshot(self.db_path)
        # Constructing the Vault performs the migration.
        self.vault = Vault(self.db_path)
        self.after = raw_snapshot(self.db_path)
        self.before_export = None  # filled per-test from a migrated copy

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_v01_file_starts_without_any_recall_table(self) -> None:
        self.assertEqual(self.before["schemaInfo"].get("schema_version"), "1")
        self.assertNotIn("recall_schema_version", self.before["schemaInfo"])
        for table in RECALL_DERIVED_TABLES:
            self.assertNotIn(table, self.before["tables"], "the fixture must be a pure v0.1 file")
        self.assertIn("search_documents", self.before["tables"])
        self.assertIn("thoughts", self.before["tables"])
        self.assertEqual(self.before["integrity"], "ok")
        self.assertEqual(self.before["searchDocuments"], 3)

    def test_opening_with_v02_preserves_every_canonical_record(self) -> None:
        # The migration already happened when the Vault was constructed.
        versions = self.vault.schema_info_rows()

        self.assertEqual(versions["schema_version"], CANONICAL_SCHEMA_VERSION,
                         "the canonical schema version must not change in v0.2")
        self.assertIn("recall_schema_version", versions)
        self.assertEqual(versions.get("migrated_from_app_version"), "0.1.x")
        self.assertTrue(versions.get("migrated_at"))

        # The canonical rows, read raw, are identical before and after.
        self.assertEqual(self.after["canonical"], self.before["canonical"],
                         "no canonical row may change during migration")
        self.assertEqual(self.after["schemaInfo"]["schema_version"], "1")
        self.assertEqual(self.after["searchDocuments"], self.before["searchDocuments"])

        for thought_id, original in zip(self.fixture["thoughtIds"], self.fixture["originals"]):
            thought = self.vault.get_thought(thought_id)
            self.assertEqual(thought["id"], thought_id, "IDs must survive migration")
            self.assertEqual(thought["originalText"], original, "original text must survive byte-for-byte")
        buried = self.vault.get_thought(self.fixture["thoughtIds"][2])
        self.assertEqual(buried["status"], "GRAVEYARD")
        self.assertTrue(buried["graveyardAt"])

        relationship = self.vault.get_relationship(self.fixture["relationshipId"])
        self.assertEqual(relationship["provenance"], "AI_SUGGESTED")
        self.assertEqual(relationship["status"], "SUGGESTED",
                         "migration must not confirm an AI suggestion")

        stats = self.vault.stats()
        self.assertEqual(stats["totalThoughts"], 3)
        self.assertEqual(stats["revisions"], 4)
        self.assertEqual(stats["tags"], 1)
        # v0.1's stats["relationships"] counts CONFIRMED rows only; the fixture's
        # single relationship is an unconfirmed AI suggestion.
        self.assertEqual(stats["relationships"], 0)
        self.assertEqual(stats["suggestedRelationships"], 1)

    def test_v01_search_behaviour_is_unchanged_by_migration(self) -> None:
        """Asserted against the documented v0.1 contract (the same expectations
        ``tests/test_store.py`` pins), on migrated v0.1 data."""
        results = self.vault.search("enemy adapting player")
        self.assertTrue(results, "v0.1 search must still find the thought")
        self.assertEqual(results[0]["id"], self.fixture["thoughtIds"][0])
        self.assertIn("fuzzy", results[0]["matchReason"].lower())
        self.assertEqual(results[0]["snippet"], self.fixture["originals"][0],
                         "the v0.1 snippet must still come from original_text")

        exact = self.vault.search("Enemies should remember how the player fights and adapt.")
        self.assertEqual(exact[0]["matchReason"], "Exact text")
        self.assertEqual(exact[0]["snippet"], self.fixture["originals"][0])

        # The tag deliberately shares no token with the fuzzy query above, so each
        # v0.1 reason is exercised independently — exactly as test_store.py does.
        tagged = self.vault.search("combat design")
        self.assertEqual(tagged[0]["matchReason"], "Tag match")
        self.assertEqual(tagged[0]["id"], self.fixture["thoughtIds"][0])

    def test_recall_works_on_migrated_data_without_rewriting_it(self) -> None:
        fingerprint = self.vault.canonical_fingerprint()

        outcome = self.vault.recall("enemys adapt to plyer")
        # A three-document vault carries almost no IDF signal, so the state is not
        # the interesting assertion here; finding the right thought and returning
        # its verbatim original text is.
        self.assertIn(outcome.state, ("STRONG_MATCH", "POSSIBLE_MATCH"))
        self.assertTrue(outcome.results)
        self.assertEqual(outcome.results[0].thought_id, self.fixture["thoughtIds"][0])
        self.assertEqual(
            outcome.results[0].original_text, self.fixture["originals"][0],
            "recall must lead back to the verbatim original capture",
        )
        self.assertEqual(self.vault.canonical_fingerprint(), fingerprint)

        # derived structures now exist, and are still deletable
        with self.vault._connection() as conn:
            existing = {row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in RECALL_DERIVED_TABLES:
            self.assertIn(table, existing)
        self.vault.delete_all_derived_intelligence()
        self.assertEqual(self.vault.canonical_fingerprint(), fingerprint)
        self.assertEqual(raw_snapshot(self.db_path)["canonical"], self.before["canonical"])

    def test_rollback_leaves_a_file_v01_can_still_open(self) -> None:
        """Rollback = drop the derived layer.  v0.1 never reads those tables, so a
        v0.1 build opens the rolled-back file and sees exactly its own data."""
        self.vault.rebuild_recall_indexes(full=True)
        self.vault.record_recall_feedback(
            query="enemy memory", thought_id=self.fixture["thoughtIds"][0], feedback="USEFUL"
        )

        with self.vault._transaction() as conn:
            for table in RECALL_DERIVED_TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute("DELETE FROM schema_info WHERE key IN "
                         "('recall_schema_version','migrated_from_app_version','migrated_at','app_version')")
        fingerprint_before_rollback = self.vault.canonical_fingerprint()

        rolled_back = raw_snapshot(self.db_path)
        self.assertEqual(rolled_back["schemaInfo"].get("schema_version"), "1")
        self.assertNotIn("recall_schema_version", rolled_back["schemaInfo"])
        self.assertEqual(rolled_back["canonical"], self.before["canonical"])
        self.assertEqual(rolled_back["integrity"], "ok")
        for table in RECALL_DERIVED_TABLES:
            self.assertNotIn(table, rolled_back["tables"], "rollback must remove the derived layer")
        self.assertIn("thoughts", rolled_back["tables"])
        self.assertIn("search_documents", rolled_back["tables"])

        reopened = Vault(self.db_path)
        self.assertEqual(reopened.canonical_fingerprint(), fingerprint_before_rollback)
        self.assertTrue(reopened.search("enemy adapting player"))

    def test_migration_is_idempotent(self) -> None:
        first = self.vault.canonical_fingerprint()
        first_versions = self.vault.schema_info_rows()
        for _ in range(3):
            self.vault.initialize()
        self.assertEqual(self.vault.canonical_fingerprint(), first)
        self.assertEqual(self.vault.schema_info_rows()["schema_version"],
                         first_versions["schema_version"])
        self.assertEqual(self.vault.schema_info_rows()["recall_schema_version"],
                         first_versions["recall_schema_version"])
        self.assertEqual(self.vault.stats()["totalThoughts"], 3)

    def test_v01_json_export_still_imports_into_v02(self) -> None:
        """A v0.1-shaped export payload (schemaVersion 1) must import unchanged."""
        export_from_v01_data = self.vault.export_vault()
        self.assertEqual(raw_snapshot(self.db_path)["canonical"], self.before["canonical"])
        payload = json.loads(json.dumps(export_from_v01_data))
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertNotIn("derived", payload)

        destination = Vault(Path(self._tmp.name) / "imported.sqlite3")
        result = destination.import_vault(payload)
        self.assertEqual(result["imported"]["thoughts"], 3)
        self.assertEqual(destination.export_vault()["canonical"], payload["canonical"])
        self.assertEqual(destination.canonical_fingerprint(), self.vault.canonical_fingerprint())


if __name__ == "__main__":
    unittest.main()
