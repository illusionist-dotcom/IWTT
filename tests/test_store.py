import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from iwtt.ai import AIUnavailable, NoAIProvider
from iwtt.store import ConflictError, ValidationError, Vault


class VaultTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.vault = Vault(self.root / "vault.sqlite3", ai_provider=NoAIProvider())

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_thought_creation_generates_durable_uuid_revision_and_event(self):
        thought = self.vault.create_thought("A small idea worth keeping.")
        self.assertRegex(thought["id"], r"^[0-9a-f]{8}-[0-9a-f-]{27}$")
        self.assertEqual(thought["originalText"], "A small idea worth keeping.")
        self.assertEqual(thought["currentText"], thought["originalText"])
        self.assertEqual(thought["type"], "THOUGHT")
        self.assertEqual(len(thought["revisions"]), 1)
        self.assertEqual(thought["events"][0]["eventType"], "THOUGHT_CREATED")
        reloaded = Vault(self.root / "vault.sqlite3").get_thought(thought["id"])
        self.assertEqual(reloaded["id"], thought["id"])

    def test_original_capture_is_database_protected_from_overwrite(self):
        thought = self.vault.create_thought("Do not alter this original.")
        self.vault.update_thought(thought["id"], current_text="A later, revised version.")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.vault._connection() as conn:
                conn.execute(
                    "UPDATE thoughts SET original_text=? WHERE id=?",
                    ("silently changed", thought["id"]),
                )
        saved = self.vault.get_thought(thought["id"])
        self.assertEqual(saved["originalText"], "Do not alter this original.")
        self.assertEqual(saved["currentText"], "A later, revised version.")

    def test_edit_preserves_revision_snapshots_and_original(self):
        thought = self.vault.create_thought("Could a garden remember the rain?")
        edited = self.vault.update_thought(
            thought["id"],
            current_text="Could a small garden keep a trace of the last rainfall?",
            title="Rain memory",
        )
        self.assertEqual(edited["originalText"], "Could a garden remember the rain?")
        self.assertEqual(edited["currentText"], "Could a small garden keep a trace of the last rainfall?")
        self.assertEqual(edited["title"], "Rain memory")
        self.assertEqual(len(edited["revisions"]), 2)
        self.assertEqual(edited["revisions"][0]["revisionNumber"], 2)
        self.assertEqual(edited["revisions"][1]["text"], thought["originalText"])
        self.assertIn("THOUGHT_EDITED", [event["eventType"] for event in edited["events"]])

    def test_type_promotion_is_flexible_and_preserves_history(self):
        thought = self.vault.create_thought("A thought that may become a project.")
        experiment = self.vault.update_thought(thought["id"], thought_type="EXPERIMENT")
        project = self.vault.update_thought(thought["id"], thought_type="PROJECT")
        self.assertEqual(experiment["type"], "EXPERIMENT")
        self.assertEqual(project["type"], "PROJECT")
        self.assertEqual(project["originalText"], thought["originalText"])
        self.assertEqual(len(project["revisions"]), 3)
        event_types = [event["eventType"] for event in project["events"]]
        self.assertIn("TYPE_CHANGED", event_types)
        self.assertIn("PROJECT_PROMOTED", event_types)
        self.assertEqual(project["revisions"][0]["type"], "PROJECT")

    def test_graveyard_and_resurrection_preserve_timestamps_and_events(self):
        thought = self.vault.create_thought("A paused thought.")
        buried = self.vault.move_to_graveyard(thought["id"])
        self.assertEqual(buried["status"], "GRAVEYARD")
        graveyard_time = buried["graveyardAt"]
        restored = self.vault.resurrect(thought["id"])
        self.assertEqual(restored["status"], "ACTIVE")
        self.assertEqual(restored["graveyardAt"], graveyard_time)
        self.assertIsNotNone(restored["resurrectedAt"])
        event_types = [event["eventType"] for event in restored["events"]]
        self.assertIn("MOVED_TO_GRAVEYARD", event_types)
        self.assertIn("RESURRECTED", event_types)

    def test_relationship_provenance_requires_explicit_confirmation(self):
        a = self.vault.create_thought("A memory mechanic.")
        b = self.vault.create_thought("A world that remembers its visitors.")
        suggestion = self.vault.create_relationship(
            a["id"], b["id"], "RELATED_TO", "AI_SUGGESTED", {"reason": "possible shared idea"}
        )
        self.assertEqual(suggestion["provenance"], "AI_SUGGESTED")
        self.assertEqual(suggestion["status"], "SUGGESTED")
        self.assertIsNone(suggestion["confirmedAt"])
        self.assertIsNone(suggestion["confirmedBy"])
        confirmed = self.vault.confirm_relationship(suggestion["id"])
        self.assertEqual(confirmed["provenance"], "AI_SUGGESTED")
        self.assertEqual(confirmed["status"], "CONFIRMED")
        self.assertEqual(confirmed["confirmedBy"], "USER")
        self.assertIn("RELATIONSHIP_CONFIRMED", [e["eventType"] for e in self.vault.timeline()])
        direct = self.vault.create_relationship(a["id"], b["id"], "SUPPORTS", "USER_CONFIRMED")
        self.assertEqual(direct["status"], "CONFIRMED")
        self.assertEqual(direct["provenance"], "USER_CONFIRMED")

    def test_tags_and_event_creation(self):
        thought = self.vault.create_thought("An idea with a label.", tags=["World Building"])
        self.assertEqual([tag["name"] for tag in thought["tags"]], ["World Building"])
        tagged = self.vault.add_tag(thought["id"], "memory")
        self.assertEqual({tag["name"] for tag in tagged["tags"]}, {"World Building", "memory"})
        events = [item["eventType"] for item in tagged["events"]]
        self.assertIn("TAG_ADDED", events)
        self.vault.remove_tag(thought["id"], tagged["tags"][0]["id"])
        self.assertIn("TAG_REMOVED", [item["eventType"] for item in self.vault.get_thought(thought["id"])["events"]])

    def test_exact_fuzzy_keyword_and_tag_search_lead_to_original(self):
        thought = self.vault.create_thought(
            "Could game enemies remember how the player fights and change their strategy later?"
        )
        self.vault.create_thought("The player hears a soft melody in a moonlit garden.")
        exact = self.vault.search("game enemies remember how the player fights")
        self.assertEqual(exact[0]["id"], thought["id"])
        self.assertEqual(exact[0]["matchReason"], "Exact text")
        fuzzy = self.vault.search("enemy adapting to player")
        self.assertTrue(fuzzy)
        self.assertEqual(fuzzy[0]["id"], thought["id"])
        self.assertEqual([item["id"] for item in fuzzy], [thought["id"]])
        self.assertIn("fuzzy", fuzzy[0]["matchReason"].lower())
        self.assertEqual(fuzzy[0]["snippet"], thought["originalText"])
        tagged = self.vault.add_tag(thought["id"], "adaptive gameplay")
        tag_hits = self.vault.search("adaptive gameplay")
        self.assertEqual(tag_hits[0]["id"], tagged["id"])
        self.assertEqual(tag_hits[0]["matchReason"], "Tag match")

    def test_json_export_import_round_trip_preserves_canonical_records(self):
        a = self.vault.create_thought("A first thought.", tags=["seed"])
        b = self.vault.create_thought("A second thought.", thought_type="EXPERIMENT")
        self.vault.update_thought(a["id"], current_text="A revised first thought.", thought_type="PROJECT")
        self.vault.create_relationship(a["id"], b["id"], "LED_TO", "USER_CONFIRMED")
        self.vault.move_to_graveyard(b["id"])
        export = self.vault.export_vault()
        self.assertNotIn("derived", export)
        self.assertNotIn("displayTitle", export["canonical"]["thoughts"][0])
        destination = Vault(self.root / "fresh" / "vault.sqlite3")
        imported = destination.import_vault(export)
        self.assertEqual(imported["imported"]["thoughts"], 2)
        self.assertEqual(destination.export_vault()["canonical"], export["canonical"])
        self.assertIn("Original capture", destination.export_markdown())

    def test_duplicate_import_fails_atomically_without_overwriting(self):
        thought = self.vault.create_thought("Existing record.", tags=["existing"])
        payload = self.vault.export_vault()
        before = self.vault.export_vault()["canonical"]
        with self.assertRaises(ConflictError):
            self.vault.import_vault(payload)
        after = self.vault.export_vault()["canonical"]
        self.assertEqual(after, before)
        self.assertEqual(self.vault.get_thought(thought["id"])["originalText"], "Existing record.")

    def test_malformed_relationship_reference_is_rejected_without_partial_import(self):
        source = Vault(self.root / "source" / "vault.sqlite3")
        a = source.create_thought("A.")
        b = source.create_thought("B.")
        source.create_relationship(a["id"], b["id"], "RELATED_TO", "USER_CONFIRMED")
        payload = source.export_vault()
        payload["canonical"]["relationships"][0]["targetId"] = "00000000-0000-4000-8000-000000000001"
        before = self.vault.stats()["totalThoughts"]
        with self.assertRaises(ValidationError):
            self.vault.import_vault(payload)
        self.assertEqual(self.vault.stats()["totalThoughts"], before)

    def test_sqlite_backup_is_a_real_consistent_file(self):
        self.vault.create_thought("Backup me.", tags=["portable"])
        backup_path = self.vault.backup()
        self.assertTrue(backup_path.is_file())
        self.assertGreater(backup_path.stat().st_size, 0)
        with sqlite3.connect(backup_path) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0], 1)

    def test_delete_all_derived_data_keeps_every_canonical_record(self):
        a = self.vault.create_thought("An old idea that might return.", tags=["memory", "old"])
        b = self.vault.create_thought("A second idea that supports it.", tags=["memory"])
        self.vault.update_thought(a["id"], current_text="A revised old idea.", thought_type="PROJECT")
        self.vault.create_relationship(a["id"], b["id"], "SUPPORTS", "USER_CONFIRMED")
        self.vault.move_to_graveyard(b["id"])
        self.vault.resurrect(b["id"])
        self.vault.save_derived_artifact(
            a["id"], "AI_SUMMARY", {"summary": "A derived interpretation."}, "TestProvider", "test-model", [a["id"]]
        )
        before = self.vault.export_vault()["canonical"]
        self.assertGreater(self.vault.stats()["searchDocuments"], 0)
        self.assertEqual(self.vault.stats()["derivedArtifacts"], 1)
        deleted = self.vault.clear_derived_data()
        self.assertEqual(deleted["derivedArtifactsDeleted"], 1)
        self.assertEqual(deleted["searchDocumentsDeleted"], 2)
        after = self.vault.export_vault()["canonical"]
        self.assertEqual(after, before)
        stats = self.vault.stats()
        self.assertEqual(stats["totalThoughts"], 2)
        self.assertGreaterEqual(stats["revisions"], 4)
        self.assertEqual(stats["relationships"], 1)
        self.assertGreater(stats["events"], 0)
        self.assertEqual(stats["tags"], 2)
        self.assertIsNotNone(self.vault.get_thought(b["id"])["graveyardAt"])
        # A later search rebuilds only the disposable projection.
        hits = self.vault.search("revised old idea")
        self.assertEqual(hits[0]["id"], a["id"])
        self.assertEqual(self.vault.stats()["searchDocuments"], 2)
        self.assertEqual(self.vault.stats()["derivedArtifacts"], 0)

    def test_demo_records_are_explicit_and_removable(self):
        real = self.vault.create_thought("Real user thought.")
        seeded = self.vault.seed_demo()
        self.assertTrue(seeded["seeded"])
        all_thoughts = self.vault.list_thoughts("ALL")
        self.assertEqual(sum(item["isDemo"] for item in all_thoughts), 7)
        removed = self.vault.remove_demo()
        self.assertEqual(removed["thoughtsRemoved"], 7)
        self.assertEqual(self.vault.stats()["totalThoughts"], 1)
        self.assertEqual(self.vault.get_thought(real["id"])["originalText"], "Real user thought.")

    def test_no_ai_fallback_is_explicit_and_available_core_remains(self):
        self.assertFalse(self.vault.ai_provider.available)
        thought = self.vault.create_thought("No AI required for capture.")
        self.assertEqual(self.vault.get_thought(thought["id"])["originalText"], "No AI required for capture.")
        with self.assertRaises(AIUnavailable):
            self.vault.ai_provider.summarize(thought)


if __name__ == "__main__":
    unittest.main()
