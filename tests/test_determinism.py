"""Determinism — the same vault, query and config must always give the same answer.

This is a hard requirement, not a nicety: recall that changes order between runs
cannot be explained, cannot be tested, and cannot be trusted with somebody's
memory.  Two things are forbidden here and both are checked:

  * ranking that depends on database row order, insertion order or timing;
  * a tie being resolved silently, so that a UUID accident looks like a judgement.

Timings are excluded from every comparison — they are measurements, not results.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from iwtt.store import Vault

QUERIES = [
    "enemies should remember how the player fights",
    "enemy adaptation",
    "enemys adapt to plyer",
    "something about difficulty feeling unfair",
    "procedural dungeon seeds",
    "abandoned turn based combat",
    "game",
    "enemy ai",
    "what did I write about determinism",
    "quantum chromodynamics",
]

CORPUS: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("Enemies should remember how the player fights and adapt over time.",
     "THOUGHT", "Enemy memory", ("combat", "enemy ai")),
    ("A difficulty curve that punishes adaptation feels unfair to the player.",
     "THOUGHT", "Adaptive difficulty", ("balance",)),
    ("Procedural dungeon generation needs a stable seed per floor.",
     "INVENTION", "Seeded dungeons", ("procedural",)),
    ("Turn based combat was abandoned because it slowed the loop down.",
     "THOUGHT", "Turn based combat", ("combat", "graveyard candidate")),
    ("Recall must be deterministic: the same query returns the same order.",
     "PROJECT", "Determinism", ("recall", "architecture")),
    ("Coffee, oats and bananas.", "THOUGHT", "Shopping list", ()),
]


def build(path: Path, corpus: list[tuple[str, str, str, tuple[str, ...]]]) -> Vault:
    vault = Vault(path)
    vault.initialize()
    for text, thought_type, title, tags in corpus:
        vault.create_thought(text, thought_type, title, list(tags))
    vault.rebuild_recall_indexes(full=True)
    return vault


def build_scenario(path: Path, corpus: list[tuple[str, str, str, tuple[str, ...]]]) -> Vault:
    """The full scenario: same corpus, same relationships, same lifecycle history.

    Relationships and the burial are looked up by title rather than by id, so two
    vaults built from the same corpus in different orders end up structurally
    identical.  Without that, a comparison of their rankings would be measuring
    the extra edges rather than the insertion order.
    """
    vault = build(path, corpus)
    ids = {thought["title"]: thought["id"] for thought in vault.list_thoughts()}
    vault.create_relationship(ids["Enemy memory"], ids["Adaptive difficulty"], "LED_TO")
    vault.create_relationship(
        ids["Adaptive difficulty"], ids["Seeded dungeons"], "INSPIRED_BY",
        provenance="AI_SUGGESTED",
    )
    vault.move_to_graveyard(ids["Turn based combat"])
    vault.rebuild_recall_indexes(full=True)
    return vault


def strip_volatile(payload: Any) -> Any:
    """Remove measurements (timings) and generation stamps from a payload."""
    if isinstance(payload, dict):
        return {
            key: strip_volatile(value)
            for key, value in payload.items()
            if key not in ("timingsMs", "timings", "generatedAt", "indexedAt", "builtAt",
                           "lastSyncAt", "durationMs", "elapsedMs")
        }
    if isinstance(payload, list):
        return [strip_volatile(item) for item in payload]
    return payload


def ranking(outcome) -> list[tuple[str, float, str, int]]:
    """The part of an outcome that is a result rather than a measurement."""
    return [
        (result.thought_id, result.score, result.state, result.rank)
        for result in outcome.results
    ]


def digest(payload: Any) -> str:
    return json.dumps(strip_volatile(payload), sort_keys=True, default=str)


class DeterminismTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root / "thought-vault.sqlite3"
        self.vault = build_scenario(self.path, CORPUS)
        self.ids = {
            thought["title"]: thought["id"] for thought in self.vault.list_thoughts()
        }

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- repeated calls --------------------------------------------------
    def test_repeated_calls_return_byte_identical_payloads(self) -> None:
        for query in QUERIES:
            with self.subTest(query=query):
                first = digest(self.vault.recall(query).to_dict())
                for _ in range(4):
                    self.assertEqual(digest(self.vault.recall(query).to_dict()), first)

    def test_repeated_calls_return_identical_scores_to_full_precision(self) -> None:
        for query in QUERIES:
            with self.subTest(query=query):
                baseline = [
                    (result.thought_id, result.score, result.rank, result.state)
                    for result in self.vault.recall(query).results
                ]
                for _ in range(3):
                    again = [
                        (result.thought_id, result.score, result.rank, result.state)
                        for result in self.vault.recall(query).results
                    ]
                    self.assertEqual(again, baseline)

    def test_diagnostics_are_reproducible(self) -> None:
        for query in QUERIES[:6]:
            with self.subTest(query=query):
                first = digest(self.vault.recall_diagnostics(query))
                self.assertEqual(digest(self.vault.recall_diagnostics(query)), first)

    # -- rebuilds, restarts, index churn ---------------------------------
    def test_ordering_survives_a_full_index_rebuild(self) -> None:
        before = {query: digest(self.vault.recall(query).to_dict()) for query in QUERIES}
        report = self.vault.rebuild_recall_indexes(full=True)
        self.assertTrue(report)
        after = {query: digest(self.vault.recall(query).to_dict()) for query in QUERIES}
        self.assertEqual(after, before, "a rebuild must not change a single ranking")

    def test_repeated_rebuilds_produce_the_same_index(self) -> None:
        first = self.vault.recall_index_stats()
        self.vault.rebuild_recall_indexes(full=True)
        second = self.vault.recall_index_stats()
        for key in ("tokenRows", "trigramRows", "terms", "documents", "distinctTrigrams",
                    "clusteredNodes", "distinctClusters", "graphCacheRows", "hubThreshold",
                    "medianDegree", "recallIndexSchema", "canonicalFingerprint"):
            self.assertEqual(second[key], first[key], f"{key} drifted across a rebuild")

    def test_ordering_survives_a_restart(self) -> None:
        before = {query: digest(self.vault.recall(query).to_dict()) for query in QUERIES}
        reopened = Vault(self.path)
        after = {query: digest(reopened.recall(query).to_dict()) for query in QUERIES}
        self.assertEqual(after, before)

    def test_deleting_and_rebuilding_derived_data_restores_the_same_ranking(self) -> None:
        before = {query: digest(self.vault.recall(query).to_dict()) for query in QUERIES}
        self.vault.delete_all_derived_intelligence()
        self.vault.rebuild_recall_indexes(full=True)
        after = {query: digest(self.vault.recall(query).to_dict()) for query in QUERIES}
        self.assertEqual(after, before)

    # -- insertion order and row order -----------------------------------
    def test_insertion_order_does_not_decide_ranking(self) -> None:
        """The same content in a different insertion order must rank identically.

        IDs are UUIDv4 and therefore differ between the two vaults, so the
        comparison is on titles and scores — which is exactly the point: nothing
        about the physical order of rows may reach the user.
        """
        other_path = self.root / "reversed.sqlite3"
        other = build_scenario(other_path, list(reversed(CORPUS)))

        def shape(vault: Vault, query: str) -> list[tuple[str, float, str]]:
            return [
                (result.display_title, round(result.score, 6), result.state)
                for result in vault.recall(query).results
            ]

        for query in QUERIES:
            with self.subTest(query=query):
                self.assertEqual(shape(other, query), shape(self.vault, query))

    def test_unrelated_insertions_do_not_reorder_the_candidates_that_matched(self) -> None:
        """IDF legitimately moves scores when the corpus grows; order must not wobble.

        Adding unrelated thoughts changes document frequency, so absolute scores
        are expected to shift.  What must never happen is the relative order of
        the thoughts that actually matched the query changing because of it.
        """
        query = "enemies should remember how the player fights"
        before = [result.thought_id for result in self.vault.recall(query).results]
        self.assertTrue(before)

        for index in range(6):
            self.vault.create_thought(
                f"An unrelated note about gardening, number {index}, with no shared vocabulary.",
                "THOUGHT", f"Gardening {index}",
            )
        self.vault.rebuild_recall_indexes(full=True)

        after = [result.thought_id for result in self.vault.recall(query).results]
        self.assertEqual([tid for tid in after if tid in set(before)], before,
                         "the matched thoughts keep their relative order")
        self.assertEqual(after[0], before[0], "the winner must not change")

    def test_row_order_is_not_used_as_a_tie_break(self) -> None:
        """Two identical thoughts must be ordered by the published total order.

        ``score DESC → trigram DESC → fuzzy DESC → created_at ASC → thought_id ASC``.
        Nothing in that list mentions the order rows happen to sit in the file.
        """
        twin_a = self.vault.create_thought(
            "Identical twin note about a recurring idea.", "THOUGHT", "Twin A",
        )
        twin_b = self.vault.create_thought(
            "Identical twin note about a recurring idea.", "THOUGHT", "Twin B",
        )
        self.vault.rebuild_recall_indexes(full=True)

        query = "identical twin recurring idea"
        outcome = self.vault.recall(query)
        twins = [r for r in outcome.results if r.thought_id in (twin_a["id"], twin_b["id"])]
        self.assertEqual(len(twins), 2)
        self.assertEqual(twins[0].score, twins[1].score, "identical text must score identically")
        self.assertEqual(twins[0].thought_id, twin_a["id"],
                         "created_at ASC breaks the tie, not the row order and not the UUID")
        self.assertTrue(all(result.tie_break for result in twins),
                        "the tie-break that was applied must be stated, not implied")
        self.assertEqual(outcome.state, "AMBIGUOUS",
                         "an exact tie is reported as ambiguity rather than decided silently")

    def test_the_final_id_tie_break_is_total_and_stable(self) -> None:
        """Force identical scores AND identical timestamps: only the UUID can decide.

        ``created_at`` is canonical and is rewritten here purely to construct the
        degenerate case; no original text is touched, and the vault is discarded.
        """
        path = self.root / "tiebreak.sqlite3"
        vault = build(path, CORPUS[:1])
        first = vault.list_thoughts()[0]["id"]
        # Same text, same type, same title, same tags: the two documents are
        # indistinguishable to every signal, so only the tie-break chain remains.
        second = vault.create_thought(CORPUS[0][0], CORPUS[0][1], CORPUS[0][2],
                                      list(CORPUS[0][3]))["id"]
        with sqlite3.connect(path) as conn:
            stamp = conn.execute(
                "SELECT created_at FROM thoughts WHERE id=?", (first,)
            ).fetchone()[0]
            conn.execute("UPDATE thoughts SET created_at=?, updated_at=? WHERE id=?",
                         (stamp, stamp, second))
            conn.execute("UPDATE revisions SET created_at=? WHERE thought_id=?", (stamp, second))
        vault.rebuild_recall_indexes(full=True)

        expected = sorted([first, second])
        for _ in range(3):
            outcome = vault.recall("enemies remember player fights adapt")
            twins = [r for r in outcome.results if r.thought_id in expected]
            self.assertEqual([r.thought_id for r in twins], expected,
                             "the durable UUID is the last, total tie-break")
            self.assertEqual(twins[0].score, twins[1].score)
            self.assertEqual(outcome.state, "AMBIGUOUS")
            self.assertEqual(outcome.ambiguity["tiedCount"], 2)
            self.assertTrue(outcome.ambiguity["orderingIsTieBreakNotJudgement"],
                            "IWTT must say the order is a tie-break, not a decision")
            # the tie-break tuple is the published total order, in full
            self.assertEqual(len(twins[0].tie_break), 5)
            self.assertEqual(twins[0].tie_break[:4], twins[1].tie_break[:4])
            self.assertEqual(twins[0].tie_break[4], twins[0].thought_id)

    # -- graph artifacts -------------------------------------------------
    def test_graph_artifacts_are_deterministic(self) -> None:
        root = self.ids["Enemy memory"]
        cases = {
            "clusters": lambda: self.vault.clusters(),
            "graphView": lambda: self.vault.graph_view_model(root_ids=[root], max_nodes=25),
            "path": lambda: self.vault.find_path(root, self.ids["Seeded dungeons"]),
            "undirectedPath": lambda: self.vault.find_path(
                self.ids["Seeded dungeons"], root, directed=False
            ),
            "neighbourhood": lambda: self.vault.thought_neighbourhood(root, depth=2),
            "lineage": lambda: self.vault.thought_lineage(self.ids["Adaptive difficulty"]),
            "config": lambda: self.vault.recall_config(),
            "indexStats": lambda: {
                key: value for key, value in self.vault.recall_index_stats().items()
                if key not in ("builtAt", "lastSyncAt")
            },
        }
        for name, produce in cases.items():
            with self.subTest(artifact=name):
                first = digest(produce())
                for _ in range(3):
                    self.assertEqual(digest(produce()), first)

    def test_clusters_are_stable_across_rebuilds_and_restarts(self) -> None:
        first = [
            (cluster["clusterId"], tuple(sorted(cluster["members"])))
            for cluster in self.vault.clusters()
        ]
        self.vault.rebuild_recall_indexes(full=True)
        second = [
            (cluster["clusterId"], tuple(sorted(cluster["members"])))
            for cluster in self.vault.clusters()
        ]
        self.assertEqual(second, first)

        reopened = Vault(self.path)
        third = [
            (cluster["clusterId"], tuple(sorted(cluster["members"])))
            for cluster in reopened.clusters()
        ]
        self.assertEqual(third, first, "cluster identity must survive a restart")

    def test_the_whole_find_my_thought_payload_is_reproducible(self) -> None:
        query = "enemies should remember how the player fights"
        first = digest(self.vault.find_my_thought(query))
        for _ in range(3):
            self.assertEqual(digest(self.vault.find_my_thought(query)), first)

    def test_learning_does_not_introduce_nondeterminism(self) -> None:
        query = "enemy adaptation"
        before_ranking = ranking(self.vault.recall(query))
        self.vault.record_recall_feedback(
            query=query, thought_id=self.ids["Adaptive difficulty"], feedback="USEFUL"
        )
        learned_once = ranking(self.vault.recall(query))
        self.assertNotEqual(learned_once, before_ranking,
                            "the feedback is meant to change the ranking")
        for _ in range(3):
            self.assertEqual(ranking(self.vault.recall(query)), learned_once,
                             "and it must change it the same way every time")

        self.vault.reset_recall_learning()
        restored = ranking(self.vault.recall(query))
        self.assertEqual(restored, before_ranking,
                         "resetting learning restores the exact original ranking")


if __name__ == "__main__":
    unittest.main()
