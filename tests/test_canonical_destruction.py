"""MANDATORY v0.2 test — complete deletion of all derived intelligence.

This is the test the sacred contract is judged by.  It proves, rather than
asserts, that canonical human memory and derived intelligence are separate:

  1. create real canonical data (thoughts, edits, type changes, tags, both
     relationship provenances, a Graveyard burial and resurrection, events,
     and an AI-style derived artifact);
  2. build every v0.2 derived structure and exercise it — recall, fuzzy recall,
     graph expansion, path discovery, neighbourhoods, clusters, query memory;
  3. snapshot the canonical export and its fingerprint;
  4. DELETE ALL derived data (v0.1 search documents + AI artifacts + every
     v0.2 ``recall_*`` table, including query-outcome memory);
  5. verify the canonical vault is bit-for-bit unchanged and still protected;
  6. rebuild, and verify recall works again and returns the SAME answer.

If any step of v0.2 ever wrote canonical truth into a derived table, or read
canonical meaning back out of one, step 5 or step 6 fails here.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from iwtt.recall.index import RECALL_DERIVED_TABLES
from iwtt.store import Vault

RECALL_QUERY = "enemies should remember how the player fights"
FUZZY_QUERY = "enemys adapt to plyer"


class DerivedDestructionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "thought-vault.sqlite3"
        self.vault = Vault(self.db_path)
        self.vault.initialize()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # fixture: a canonical vault with real history and real provenance
    # ------------------------------------------------------------------

    def build_canonical(self) -> dict[str, str]:
        vault = self.vault
        enemy = vault.create_thought(
            "Enemies should remember how the player fights and adapt after three encounters.",
            "THOUGHT", "Enemy memory", ["enemy ai", "combat"],
        )
        difficulty = vault.create_thought(
            "A difficulty curve that punishes adaptation feels unfair to the player.",
            "THOUGHT", "Adaptive difficulty", ["difficulty"],
        )
        dungeon = vault.create_thought(
            "Procedural dungeon generation needs a stable seed per floor.",
            "THOUGHT", "Seeded dungeons", ["procedural"],
        )
        buried = vault.create_thought(
            "Abandoned idea: turn-based combat where every action costs stamina.",
            "THOUGHT", "Turn-based combat", ["abandoned"],
        )

        # real revision history: an edit and two type changes
        vault.update_thought(enemy["id"], current_text=enemy["originalText"] + " Counter once, then telegraph.")
        vault.update_thought(difficulty["id"], thought_type="EXPERIMENT")
        vault.update_thought(difficulty["id"], thought_type="THOUGHT")

        # both provenances, one confirmed after being suggested
        confirmed = vault.create_relationship(enemy["id"], difficulty["id"], "LED_TO", provenance="USER_CONFIRMED")
        suggested = vault.create_relationship(enemy["id"], dungeon["id"], "INSPIRED_BY", provenance="AI_SUGGESTED")

        # lifecycle: buried, then brought back — graveyardAt must survive
        vault.move_to_graveyard(buried["id"])
        vault.resurrect(buried["id"])

        # a v0.1-style derived artifact (an AI summary would look like this)
        vault.save_derived_artifact(
            enemy["id"], "AI_SUMMARY", {"summary": "A generated summary that is NOT the thought."},
            "FakeProvider", "fake-model", [enemy["id"]],
        )
        return {
            "enemy": enemy["id"],
            "difficulty": difficulty["id"],
            "dungeon": dungeon["id"],
            "buried": buried["id"],
            "confirmed": confirmed["id"],
            "suggested": suggested["id"],
        }

    def exercise_derived(self, ids: dict[str, str]) -> dict[str, object]:
        """Build and use every derived structure v0.2 owns."""
        vault = self.vault
        vault.rebuild_recall_indexes(full=True)

        exact = vault.recall(RECALL_QUERY)
        fuzzy = vault.recall(FUZZY_QUERY)
        graveyard = vault.recall("abandoned turn based combat")
        path = vault.find_path(ids["enemy"], ids["dungeon"])
        neighbourhood = vault.thought_neighbourhood(ids["enemy"])
        lineage = vault.thought_lineage(ids["enemy"])
        model = vault.graph_view_model(root_ids=[ids["enemy"]])
        clusters = vault.clusters()

        # derived query-outcome learning, including a weak implicit signal
        vault.record_recall_feedback(query=RECALL_QUERY, thought_id=ids["enemy"], feedback="USEFUL", rank=1)
        vault.record_recall_feedback(query=RECALL_QUERY, thought_id=ids["enemy"], feedback="USEFUL", rank=1)
        vault.record_recall_feedback(query=RECALL_QUERY, thought_id=ids["dungeon"], feedback="NOT_USEFUL", rank=3)
        boosted = vault.recall(RECALL_QUERY)

        self.assertTrue(exact.results, "baseline recall must return something before destruction")
        self.assertEqual(exact.results[0].thought_id, ids["enemy"])
        self.assertEqual(fuzzy.results[0].thought_id, ids["enemy"])
        self.assertEqual(graveyard.results[0].thought_id, ids["buried"])
        self.assertTrue(path["found"])
        self.assertTrue(clusters)
        self.assertGreater(
            boosted.results[0].score, exact.results[0].score,
            "corroborated USEFUL feedback must raise the score of the thought it names",
        )
        return {
            "exactTop": exact.results[0].thought_id,
            "exactScore": round(exact.results[0].score, 6),
            "fuzzyTop": fuzzy.results[0].thought_id,
            "order": [result.thought_id for result in exact.results],
            "neighbourhood": neighbourhood["degree"],
            "lineage": len(lineage["downstream"]),
            "nodes": model["nodeCount"],
            "clusters": len(clusters),
        }

    def table_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self.vault._connection() as conn:
            for table in RECALL_DERIVED_TABLES:
                try:
                    counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except sqlite3.OperationalError:
                    counts[table] = -1
            counts["search_documents"] = int(conn.execute("SELECT COUNT(*) FROM search_documents").fetchone()[0])
            counts["derived_artifacts"] = int(conn.execute("SELECT COUNT(*) FROM derived_artifacts").fetchone()[0])
        return counts

    # ------------------------------------------------------------------
    # the test
    # ------------------------------------------------------------------

    def test_delete_all_derived_intelligence_preserves_canonical_history(self) -> None:
        vault = self.vault
        ids = self.build_canonical()

        before_export = vault.export_vault()
        before_fingerprint = vault.canonical_fingerprint()
        before_stats = vault.stats()
        before_search = vault.search("enemy adapting player")

        derived = self.exercise_derived(ids)
        populated = self.table_counts()
        self.assertGreater(populated["recall_tokens"], 0)
        self.assertGreater(populated["recall_trigrams"], 0)
        self.assertGreater(populated["recall_docs"], 0)
        self.assertGreater(populated["recall_clusters"], 0)
        self.assertGreater(populated["recall_graph_cache"], 0)
        self.assertGreater(populated["recall_query_memory"], 0)
        self.assertEqual(populated["derived_artifacts"], 1)

        # ---- 4. DELETE EVERYTHING DERIVED --------------------------------
        report = vault.delete_all_derived_intelligence()

        # ---- 5. canonical truth is untouched -----------------------------
        after_export = vault.export_vault()
        self.assertEqual(
            before_export["canonical"], after_export["canonical"],
            "the canonical export must be identical after deleting all derived data",
        )
        self.assertEqual(before_fingerprint, vault.canonical_fingerprint())
        self.assertNotIn("derived", vault.export_vault(), "derived data is excluded from export by default")

        counts = vault.stats()
        for key in ("totalThoughts", "revisions", "events", "tags", "relationships",
                    "suggestedRelationships", "graveyardThoughts"):
            self.assertEqual(before_stats[key], counts[key], f"canonical {key} changed")
        self.assertEqual(counts["derivedArtifacts"], 0)
        self.assertEqual(counts["searchDocuments"], 0)
        self.assertEqual(counts["recall"]["queryMemoryEvents"], 0)
        self.assertEqual(counts["recall"]["indexedThoughts"], 0)
        self.assertEqual(counts["recall"]["clusters"], 0)

        emptied = self.table_counts()
        for table in RECALL_DERIVED_TABLES:
            self.assertEqual(emptied[table], 0, f"{table} still holds derived rows")
        self.assertGreater(report["recallRowsDeleted"], 0)
        self.assertEqual(report["canonicalTablesTouched"], [])
        self.assertEqual(report["derivedArtifactsDeleted"], 1)

        # every individual canonical record, field by field
        enemy = vault.get_thought(ids["enemy"])
        self.assertEqual(
            enemy["originalText"],
            "Enemies should remember how the player fights and adapt after three encounters.",
        )
        self.assertNotEqual(enemy["currentText"], enemy["originalText"], "the edit must still be there")
        self.assertGreaterEqual(len(enemy["revisions"]), 2)
        self.assertEqual([tag["name"] for tag in enemy["tags"]], ["combat", "enemy ai"])

        buried = vault.get_thought(ids["buried"])
        self.assertTrue(buried["graveyardAt"], "graveyardAt must survive resurrection")
        self.assertTrue(buried["resurrectedAt"])
        self.assertEqual(buried["status"], "ACTIVE")

        suggested = vault.get_relationship(ids["suggested"])
        self.assertEqual(suggested["provenance"], "AI_SUGGESTED")
        self.assertEqual(suggested["status"], "SUGGESTED", "deleting derived data must not confirm anything")
        self.assertIsNone(suggested["confirmedBy"])
        confirmed = vault.get_relationship(ids["confirmed"])
        self.assertEqual(confirmed["provenance"], "USER_CONFIRMED")
        self.assertEqual(confirmed["status"], "CONFIRMED")

        # the immutability trigger still fires after all derived data is gone
        with self.assertRaises(sqlite3.IntegrityError):
            with vault._transaction() as conn:
                conn.execute("UPDATE thoughts SET original_text='rewritten' WHERE id=?", (ids["enemy"],))

        # v0.1 search still works (it rebuilds its own projection on demand)
        self.assertTrue(vault.search("enemy adapting player"))
        self.assertEqual(
            [(row["id"], row["matchReason"]) for row in before_search],
            [(row["id"], row["matchReason"]) for row in vault.search("enemy adapting player")],
            "v0.1 search behaviour must be identical before and after destruction",
        )

        # ---- 6. rebuild: recall works again and agrees with itself -------
        vault.rebuild_recall_indexes(full=True)
        rebuilt = vault.recall(RECALL_QUERY)
        self.assertEqual(rebuilt.state, "STRONG_MATCH")
        self.assertEqual(rebuilt.results[0].thought_id, derived["exactTop"])
        self.assertEqual([result.thought_id for result in rebuilt.results], derived["order"])
        self.assertEqual(round(rebuilt.results[0].score, 6), derived["exactScore"],
                         "the same vault, query and configuration must produce the same score")
        self.assertEqual(vault.recall(FUZZY_QUERY).results[0].thought_id, derived["fuzzyTop"])
        self.assertTrue(vault.find_path(ids["enemy"], ids["dungeon"])["found"])
        self.assertEqual(vault.thought_neighbourhood(ids["enemy"])["degree"], derived["neighbourhood"])
        self.assertEqual(len(vault.clusters()), derived["clusters"])
        self.assertEqual(vault.graph_view_model(root_ids=[ids["enemy"]])["nodeCount"], derived["nodes"])

        # query memory was derived too: it is gone, and recall is unaffected
        self.assertEqual(vault.recall_learning_stats()["events"], 0)
        self.assertEqual(vault.canonical_fingerprint(), before_fingerprint)

    def test_reset_learning_alone_leaves_indexes_and_canonical_data_alone(self) -> None:
        ids = self.build_canonical()
        self.vault.rebuild_recall_indexes(full=True)
        self.vault.record_recall_feedback(query=RECALL_QUERY, thought_id=ids["enemy"], feedback="USEFUL")
        fingerprint = self.vault.canonical_fingerprint()
        tokens_before = self.table_counts()["recall_tokens"]

        result = self.vault.reset_recall_learning()

        self.assertEqual(self.vault.recall_learning_stats()["events"], 0)
        self.assertEqual(result["deletedEvents"], 1)
        self.assertEqual(self.table_counts()["recall_tokens"], tokens_before,
                         "resetting learning must not touch the lexical index")
        self.assertEqual(self.vault.canonical_fingerprint(), fingerprint)
        self.assertEqual(self.vault.recall(RECALL_QUERY).results[0].thought_id, ids["enemy"])

    def test_removing_demo_data_cascades_the_derived_layer(self) -> None:
        """``recall_*`` tables reference thoughts(id) ON DELETE CASCADE, so
        removing the seven demo records must not leave derived orphans."""
        self.vault.seed_demo()
        self.vault.rebuild_recall_indexes(full=True)
        self.assertGreater(self.table_counts()["recall_docs"], 0)
        before = self.vault.remove_demo()
        self.assertEqual(before["thoughtsRemoved"], 7)
        after = self.table_counts()
        self.assertEqual(after["recall_docs"], 0)
        self.assertEqual(after["recall_tokens"], 0)
        self.assertEqual(after["recall_trigrams"], 0)
        self.assertEqual(after["recall_clusters"], 0)
        self.assertEqual(after["recall_graph_cache"], 0)
        self.assertEqual(self.vault.stats()["totalThoughts"], 0)


if __name__ == "__main__":
    unittest.main()
