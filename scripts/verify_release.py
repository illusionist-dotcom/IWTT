#!/usr/bin/env python3
"""Isolated end-to-end release verification for IWTT v0.1."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iwtt.ai import NoAIProvider
from iwtt.store import ConflictError, Vault


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="iwtt-release-check-") as temp:
        root = Path(temp)
        live_path = root / "live" / "thought-vault.sqlite3"
        vault = Vault(live_path, ai_provider=NoAIProvider())
        check(vault.stats()["totalThoughts"] == 0, "verification must begin from an empty vault")

        adaptive = vault.create_thought(
            "Could game enemies remember how the player fights and change their strategy later?",
        )
        world_memory = vault.create_thought(
            "What if old places kept traces of how the player had used them?",
            title="A world that remembers",
            tags=["world building"],
        )
        paused = vault.create_thought("A prototype for a soundscape that never repeats.")

        edited = vault.update_thought(
            adaptive["id"],
            current_text="Prototype an enemy that notices repeated combat habits and changes its tactics.",
        )
        check(edited["originalText"] == adaptive["originalText"], "edit overwrote the original")
        check(len(edited["revisions"]) == 2, "edit did not preserve a revision")
        for kind in ("EXPERIMENT", "INVENTION", "PROJECT"):
            adaptive = vault.update_thought(adaptive["id"], thought_type=kind)
        check(adaptive["type"] == "PROJECT", "lifecycle promotion did not persist")
        check(len(adaptive["revisions"]) == 5, "type promotions were not snapshotted")

        confirmed = vault.create_relationship(
            adaptive["id"], world_memory["id"], "RELATED_TO", "USER_CONFIRMED",
            {"note": "Both explore memory in a game world."},
        )
        suggested = vault.create_relationship(
            adaptive["id"], paused["id"], "INSPIRED_BY", "AI_SUGGESTED",
            {"note": "Unconfirmed verification proposal."},
        )
        check(confirmed["status"] == "CONFIRMED", "user relationship was not confirmed")
        check(suggested["status"] == "SUGGESTED", "AI proposal became authoritative without confirmation")
        check(suggested["confirmedBy"] is None, "AI proposal incorrectly records user confirmation")
        confirmed_suggestion = vault.confirm_relationship(suggested["id"])
        check(confirmed_suggestion["status"] == "CONFIRMED", "explicit confirmation failed")
        check(confirmed_suggestion["provenance"] == "AI_SUGGESTED", "AI origin provenance was lost")

        buried = vault.move_to_graveyard(paused["id"])
        original_graveyard_time = buried["graveyardAt"]
        restored = vault.resurrect(paused["id"])
        check(restored["status"] == "ACTIVE", "resurrection did not restore active status")
        check(restored["graveyardAt"] == original_graveyard_time, "resurrection erased Graveyard history")
        check(any(e["eventType"] == "RESURRECTED" for e in restored["events"]), "resurrection event missing")

        recall = vault.search("enemy adapting to player")
        check(recall and recall[0]["id"] == adaptive["id"], "paraphrased fuzzy recall failed")
        check("fuzzy" in recall[0]["matchReason"].lower(), "recall was not identified as fuzzy")
        check(recall[0]["snippet"] == adaptive["originalText"], "search did not lead to preserved original text")

        exported = vault.export_vault()
        markdown = vault.export_markdown()
        check("Original capture" in markdown, "Markdown export missing original capture")
        fresh = Vault(root / "fresh" / "thought-vault.sqlite3", ai_provider=NoAIProvider())
        import_result = fresh.import_vault(exported)
        reexported = fresh.export_vault()
        check(reexported["canonical"] == exported["canonical"], "fresh-vault import differs in canonical records")
        try:
            fresh.import_vault(exported)
        except ConflictError:
            pass
        else:
            raise AssertionError("duplicate IDs were not rejected")

        # Simulate an optional derived artifact, then prove the delete-all operation
        # leaves all canonical records byte-for-byte equal at the JSON object level.
        vault.save_derived_artifact(
            adaptive["id"], "AI_SUMMARY", {"summary": "A fictional test interpretation."},
            "VerificationProvider", "test-only", [adaptive["id"]],
        )
        canonical_before = vault.export_vault()["canonical"]
        derived_removed = vault.clear_derived_data()
        canonical_after = vault.export_vault()["canonical"]
        check(canonical_after == canonical_before, "derived-data deletion changed canonical records")
        check(vault.stats()["derivedArtifacts"] == 0, "derived artifact deletion failed")
        check(vault.stats()["searchDocuments"] == 0, "search projection deletion failed")

        # Reopen the same database in a new Vault instance to verify restart persistence.
        restarted = Vault(live_path, ai_provider=NoAIProvider())
        persisted = restarted.get_thought(adaptive["id"])
        check(persisted["originalText"] == adaptive["originalText"], "restart lost original text")
        check(persisted["type"] == "PROJECT", "restart lost lifecycle state")
        check(len(persisted["revisions"]) == 5, "restart lost revision history")
        check(restarted.search("enemy adapting to player")[0]["id"] == adaptive["id"], "search did not rebuild after restart")

        backup_path = restarted.backup()
        with sqlite3.connect(backup_path) as backup_db:
            check(backup_db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "backup integrity check failed")
            backup_thoughts = backup_db.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0]
        check(backup_thoughts == 3, "backup omitted thoughts")

        report = {
            "result": "PASS",
            "startedEmpty": True,
            "thoughtsCreated": 3,
            "editAndTypeRevisions": len(persisted["revisions"]),
            "relationshipProvenance": "AI_SUGGESTED remained unconfirmed until explicit USER confirmation",
            "graveyardResurrection": "PASS; graveyardAt retained",
            "fuzzyRecall": recall[0]["matchReason"],
            "canonicalImportEqualsExport": reexported["canonical"] == exported["canonical"],
            "duplicateImportRejected": True,
            "derivedDeletionCanonicalPreserved": canonical_after == canonical_before,
            "restartPersistence": True,
            "backupIntegrity": "ok",
            "backupPath": str(backup_path),
            "importCounts": import_result["imported"],
        }
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
