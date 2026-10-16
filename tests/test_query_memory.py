"""Query-outcome memory — derived, decayed, inspectable, resettable, never canonical.

Everything here is IWTT learning from *use*, which is exactly the kind of thing
that must not be allowed to quietly become truth.  The tests therefore check the
learning itself and, more importantly, its limits:

  * it lives only in ``recall_query_memory`` and touches no canonical table;
  * a single observation is dampened until it is corroborated;
  * old observations decay on a documented half-life; the value is clamped both ways;
  * it can re-rank a candidate, but it can never manufacture one;
  * it can be inspected and it can be reset, and resetting changes nothing else.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iwtt.recall.config import DEFAULT_CONFIG
from iwtt.recall.querymemory import FEEDBACK_WEIGHTS, memory_key, signal_for, usefulness
from iwtt.store import Vault


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class QueryMemoryTestCase(unittest.TestCase):
    QUERY = "enemy adaptation memory"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "thought-vault.sqlite3"
        self.vault = Vault(self.path)
        self.vault.initialize()
        self.target = self.vault.create_thought(
            "Enemies should remember how the player fights and adapt over time.",
            "THOUGHT", "Enemy memory", ["combat"],
        )["id"]
        self.rival = self.vault.create_thought(
            "Enemies adapt their difficulty when the player adapts first.",
            "THOUGHT", "Adaptive difficulty",
        )["id"]
        self.bystander = self.vault.create_thought(
            "Procedural dungeon generation needs a stable seed per floor.",
            "THOUGHT", "Seeded dungeons",
        )["id"]
        self.vault.rebuild_recall_indexes(full=True)
        self.index = self.vault.recall_engine.index

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def outcome(self, query: str | None = None):
        return self.vault.recall(query or self.QUERY)

    def rank_of(self, thought_id: str, query: str | None = None) -> int | None:
        for result in self.outcome(query).results:
            if result.thought_id == thought_id:
                return result.rank
        return None

    def score_of(self, thought_id: str, query: str | None = None) -> float | None:
        for result in self.outcome(query).results:
            if result.thought_id == thought_id:
                return result.score
        return None

    def feedback(self, thought_id: str, kind: str, *, query: str | None = None, **kwargs) -> dict:
        return self.vault.record_recall_feedback(
            query=query or self.QUERY, thought_id=thought_id, feedback=kind, **kwargs
        )

    def memory(self, thought_id: str, *, now: datetime | None = None):
        """The full derived record for one thought: value, raw, decay detail."""
        return usefulness(self.index, self.QUERY, now=now).get(thought_id)

    # -- storage boundary ------------------------------------------------
    def test_feedback_is_stored_only_in_the_derived_layer(self) -> None:
        canonical_before = self.vault.canonical_fingerprint()
        events_before = self.vault.timeline()

        self.feedback(self.target, "USEFUL")
        self.feedback(self.rival, "NOT_USEFUL")

        self.assertEqual(self.vault.canonical_fingerprint(), canonical_before,
                         "learning must not move the canonical fingerprint")
        self.assertEqual(self.vault.timeline(), events_before,
                         "an outcome signal is not a lifecycle event and must not enter the event log")

        stats = self.vault.recall_learning_stats()
        self.assertTrue(stats["available"])
        self.assertEqual(stats["events"], 2)
        self.assertEqual(stats["queries"], 1)
        self.assertEqual(stats["thoughts"], 2)
        self.assertEqual(stats["byFeedback"], {"NOT_USEFUL": 1, "USEFUL": 1})
        self.assertFalse(stats["canonical"])
        self.assertTrue(stats["derived"])
        self.assertEqual(stats["halfLifeDays"], DEFAULT_CONFIG.query_memory_half_life_days)

    def test_memory_key_normalises_the_query_but_keeps_distinct_queries_apart(self) -> None:
        self.assertEqual(memory_key("Enemy   Adaptation MEMORY"), memory_key(self.QUERY))
        self.assertEqual(memory_key(f"  {self.QUERY} "), memory_key(self.QUERY))
        self.assertNotEqual(memory_key("enemy adaptation"), memory_key(self.QUERY))
        self.assertTrue(memory_key(self.QUERY))
        self.assertEqual(memory_key(""), "")

    def test_deleting_all_derived_data_deletes_learning_and_nothing_else(self) -> None:
        self.feedback(self.target, "USEFUL")
        self.feedback(self.target, "USEFUL")
        canonical_before = self.vault.canonical_fingerprint()

        report = self.vault.delete_all_derived_intelligence()
        self.assertEqual(self.vault.recall_learning_stats()["events"], 0)
        self.assertEqual(self.vault.learned_queries(), [])
        self.assertEqual(self.vault.canonical_fingerprint(), canonical_before)
        self.assertEqual(report["canonicalTablesTouched"], [])
        self.assertEqual(
            self.vault.get_thought(self.target)["currentText"],
            "Enemies should remember how the player fights and adapt over time.",
        )

    def test_reset_clears_learning_without_disturbing_the_index(self) -> None:
        self.feedback(self.target, "USEFUL")
        self.feedback(self.rival, "NOT_USEFUL")
        before = self.vault.recall_index_stats()

        report = self.vault.reset_recall_learning()
        self.assertEqual(report["deletedEvents"], 2)
        self.assertEqual(report["deletedQueries"], 1)
        self.assertTrue(report["resetAt"].endswith("Z"))
        self.assertFalse(report["canonical"])
        self.assertIn("unchanged", report["note"])

        self.assertEqual(self.vault.learned_queries(), [])
        self.assertEqual(self.vault.recall_learning_stats()["events"], 0)
        after = self.vault.recall_index_stats()
        self.assertEqual(after["tokenRows"], before["tokenRows"],
                         "the lexical index is not learning data and must survive a reset")
        self.assertEqual(after["documents"], before["documents"])
        self.assertEqual(after["trigramRows"], before["trigramRows"])
        self.assertIsNotNone(after["learningResetAt"])

    # -- the learning itself --------------------------------------------
    def test_useful_and_not_useful_move_a_candidate_in_opposite_directions(self) -> None:
        baseline = self.score_of(self.rival)
        self.assertIsNotNone(baseline)

        self.feedback(self.rival, "USEFUL")
        self.assertGreater(self.score_of(self.rival), baseline)

        self.vault.reset_recall_learning()
        self.feedback(self.rival, "NOT_USEFUL")
        self.feedback(self.rival, "NOT_USEFUL")
        self.assertLess(self.score_of(self.rival), baseline,
                        "two negative observations must pull the candidate down")

    def test_a_single_observation_is_dampened_until_corroborated(self) -> None:
        factor = DEFAULT_CONFIG.query_memory_single_observation_factor
        self.feedback(self.rival, "USEFUL")
        single = self.memory(self.rival)
        self.assertFalse(single.corroborated)
        self.assertEqual(single.events, 1)
        self.assertAlmostEqual(single.value, single.raw * factor, places=6,
                               msg="one observation counts for less than it claims")

        signal = signal_for(self.index, self.QUERY, self.rival)
        self.assertFalse(signal["corroborated"])
        self.assertEqual(signal["events"], 1)

        self.feedback(self.rival, "USEFUL")
        double = self.memory(self.rival)
        self.assertTrue(double.corroborated,
                        f"corroboration starts at {DEFAULT_CONFIG.query_memory_min_corroboration}")
        self.assertEqual(double.events, 2)
        self.assertAlmostEqual(
            double.value, min(double.raw, DEFAULT_CONFIG.query_memory_positive_cap), places=6,
            msg="corroborated observations are used at full weight, up to the published cap",
        )
        self.assertGreater(double.value, single.value)

    def test_observations_decay_on_the_documented_half_life(self) -> None:
        half_life = DEFAULT_CONFIG.query_memory_half_life_days
        now = datetime.now(timezone.utc)
        old = _iso(now - timedelta(days=2 * half_life))

        self.feedback(self.rival, "USEFUL", recorded_at=old)
        self.feedback(self.rival, "USEFUL", recorded_at=old)
        aged = self.memory(self.rival, now=now)
        self.assertAlmostEqual(aged.raw, 2.0 * 0.25, places=3,
                               msg="two half-lives must leave a quarter of each weight")
        self.assertTrue(all(abs(item["decay"] - 0.25) < 0.01 for item in aged.detail),
                        "each record shows the decay that was applied to it")
        self.assertTrue(all(abs(item["ageDays"] - 2 * half_life) < 1 for item in aged.detail))

        self.vault.reset_recall_learning()
        self.feedback(self.rival, "USEFUL")
        self.feedback(self.rival, "USEFUL")
        fresh = self.memory(self.rival, now=now)
        self.assertAlmostEqual(fresh.raw, 2.0, places=3)
        self.assertAlmostEqual(fresh.value, DEFAULT_CONFIG.query_memory_positive_cap, places=6)
        self.assertLess(aged.value, fresh.value)

        stats = signal_for(self.index, self.QUERY, self.rival)
        self.assertEqual(stats["events"], 2)

    def test_the_value_is_clamped_in_both_directions(self) -> None:
        for _ in range(12):
            self.feedback(self.rival, "USEFUL")
        positive = self.memory(self.rival)
        self.assertLessEqual(positive.value, DEFAULT_CONFIG.query_memory_positive_cap + 1e-9)
        self.assertGreater(positive.raw, positive.value,
                           "the raw sum exceeds the cap, proving the cap is doing work")

        self.vault.reset_recall_learning()
        for _ in range(12):
            self.feedback(self.rival, "NOT_USEFUL")
        negative = self.memory(self.rival)
        self.assertGreaterEqual(negative.value, DEFAULT_CONFIG.query_memory_negative_cap - 1e-9)
        self.assertLess(negative.value, 0.0)
        self.assertLess(negative.raw, negative.value)

    def test_every_feedback_kind_is_weighted_and_recorded(self) -> None:
        self.assertEqual(set(FEEDBACK_WEIGHTS), {"USEFUL", "NOT_USEFUL", "OPENED", "CORRECTION"})
        self.assertEqual(FEEDBACK_WEIGHTS["USEFUL"], 1.0)
        self.assertEqual(FEEDBACK_WEIGHTS["NOT_USEFUL"], -1.0)
        self.assertLess(FEEDBACK_WEIGHTS["OPENED"], FEEDBACK_WEIGHTS["CORRECTION"],
                        "opening a thought is weaker evidence than correcting IWTT")
        self.assertLess(FEEDBACK_WEIGHTS["CORRECTION"], FEEDBACK_WEIGHTS["USEFUL"])

        for kind in FEEDBACK_WEIGHTS:
            with self.subTest(kind=kind):
                recorded = self.feedback(self.target, kind)
                self.assertEqual(recorded["feedback"], kind)
                self.assertEqual(recorded["queryNorm"], memory_key(self.QUERY))
                self.assertEqual(recorded["thoughtId"], self.target)
                self.assertEqual(recorded["weight"], FEEDBACK_WEIGHTS[kind])
                self.assertTrue(recorded["createdAt"].endswith("Z"))
                self.assertFalse(recorded["canonical"])
                self.assertTrue(recorded["derived"])

        with self.assertRaises(ValueError):
            self.feedback(self.target, "PROBABLY_GOOD")
        with self.assertRaises(ValueError):
            self.feedback(self.target, "USEFUL", query="   ")
        with self.assertRaises(KeyError):
            self.feedback("no-such-thought", "USEFUL")

    def test_learning_is_inspectable_event_by_event_and_query_by_query(self) -> None:
        self.feedback(self.target, "USEFUL", rank=1, score=9.5, note="this was the one")
        self.feedback(self.rival, "NOT_USEFUL", rank=2, score=4.1)

        learned = self.vault.learned_queries()
        self.assertEqual(len(learned), 1)
        self.assertEqual(learned[0]["queryNorm"], memory_key(self.QUERY))
        self.assertEqual(learned[0]["events"], 2)
        self.assertEqual(learned[0]["thoughts"], 2)
        self.assertTrue(learned[0]["lastAt"].endswith("Z"))

        history = self.vault.recall_learning_history(limit=10)
        self.assertEqual(len(history), 2)
        self.assertEqual({item["feedback"] for item in history}, {"USEFUL", "NOT_USEFUL"})
        by_thought = {item["thoughtId"]: item for item in history}
        self.assertEqual(by_thought[self.target]["note"], "this was the one")
        self.assertEqual(by_thought[self.target]["rankAtTime"], 1)
        self.assertEqual(by_thought[self.target]["scoreAtTime"], 9.5)
        self.assertEqual(by_thought[self.target]["displayTitle"], "Enemy memory")
        self.assertEqual(by_thought[self.rival]["rankAtTime"], 2)

        self.assertEqual(len(self.vault.recall_learning_history(limit=1)), 1, "the log is bounded")

    def test_a_long_note_is_truncated_rather_than_rejected(self) -> None:
        recorded = self.feedback(self.target, "CORRECTION", note="x" * 900)
        self.assertTrue(recorded["id"])
        stored = self.vault.recall_learning_history(limit=1)[0]
        self.assertLessEqual(len(stored["note"]), 500)

    # -- the limits of learning -----------------------------------------
    def test_memory_can_never_manufacture_a_candidate(self) -> None:
        """A thought with no word in common stays unreturnable however often it is praised.

        Learning adjusts the ranking of what retrieval found.  It is not a
        retrieval channel of its own, and treating it as one would let repeated
        clicks invent relevance out of nothing.
        """
        for _ in range(20):
            self.feedback(self.bystander, "USEFUL")
        outcome = self.outcome()
        self.assertNotIn(self.bystander, [result.thought_id for result in outcome.results])
        self.assertNotIn(self.bystander, [result.thought_id for result in outcome.all_candidates]
                         if hasattr(outcome, "all_candidates") else [])

        remembered = self.memory(self.bystander)
        self.assertIsNotNone(remembered, "the learning is real and inspectable")
        self.assertEqual(remembered.events, 20)
        self.assertEqual(remembered.value, DEFAULT_CONFIG.query_memory_positive_cap)

        stats = self.vault.recall_index_stats()
        self.assertEqual(stats["queryMemoryEvents"], 20)
        self.assertEqual(stats["queryMemoryQueries"], 1)

    def test_memory_never_rewrites_what_a_thought_says(self) -> None:
        before = self.vault.get_thought(self.target)
        for _ in range(5):
            self.feedback(self.target, "USEFUL")
            self.feedback(self.target, "CORRECTION", note="the wording here is wrong")
        after = self.vault.get_thought(self.target)
        self.assertEqual(len(after["revisions"]), len(before["revisions"]))
        self.assertEqual(after["events"], before["events"])
        for field in ("originalText", "currentText", "title", "type", "status", "updatedAt"):
            self.assertEqual(after[field], before[field], f"{field} must be untouched by learning")

    def test_learning_does_not_confirm_a_suggested_relationship(self) -> None:
        suggested = self.vault.create_relationship(
            self.target, self.bystander, "RELATED_TO", provenance="AI_SUGGESTED"
        )
        for _ in range(4):
            self.feedback(self.target, "CORRECTION")
            self.feedback(self.bystander, "USEFUL")
        relationship = self.vault.get_relationship(suggested["id"])
        self.assertEqual(relationship["provenance"], "AI_SUGGESTED")
        self.assertEqual(relationship["status"], "SUGGESTED")
        self.assertIsNone(relationship["confirmedBy"])
        self.assertIsNone(relationship["confirmedAt"])

    def test_learning_survives_a_restart(self) -> None:
        self.feedback(self.target, "USEFUL")
        self.feedback(self.target, "USEFUL")

        reopened = Vault(self.path)
        self.assertEqual(reopened.recall_learning_stats()["events"], 2)
        entry = usefulness(reopened.recall_engine.index, self.QUERY).get(self.target)
        self.assertTrue(entry.corroborated)
        self.assertEqual(reopened.learned_queries()[0]["events"], 2)
        self.assertGreater(self.score_of(self.target), 0.0)

    def test_the_memory_signal_is_reported_in_the_result_explanation(self) -> None:
        for _ in range(3):
            self.feedback(self.rival, "NOT_USEFUL", rank=1)
        outcome = self.outcome()
        rival = next(r for r in outcome.results if r.thought_id == self.rival)
        self.assertLess(rival.signals.query_memory, 0.0)
        self.assertEqual(rival.signals.query_memory_events, 3)
        self.assertTrue(rival.signals.query_memory_corroborated)
        codes = [item.code for item in rival.explanations]
        self.assertIn("PREVIOUSLY_NOT_USEFUL", codes)
        memory_note = next(
            item for item in rival.explanations if item.code == "PREVIOUSLY_NOT_USEFUL"
        )
        self.assertIn("not useful", memory_note.detail.lower())
        self.assertLess(memory_note.contribution, 0.0)


if __name__ == "__main__":
    unittest.main()
