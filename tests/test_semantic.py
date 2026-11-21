"""The opt-in semantic (embedding) stage.

These tests pin the promises the semantic stage makes:

* it is **off** until an index is explicitly built, and recall is unchanged
  while it is off;
* it never alters the published scored ranking — its output is a separate,
  labelled section;
* it is derived, erasable, and cannot change a canonical byte;
* it is deterministic: the same index and query always give the same order;
* provider failures are reported, never silently turned into invented matches;
* every semantic hit references a real thought that still exists.

The ``MockProvider`` is used throughout, so the whole stage is exercised
offline with no model server and no API key.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from iwtt.ai import AIUnavailable, MockProvider, NoAIProvider
from iwtt.recall import embeddings as embedding_module
from iwtt.recall.config import DEFAULT_CONFIG
from iwtt.recall.embeddings import (
    EmbeddingIndex,
    cosine_similarity,
    normalize_vector,
    pack_vector,
    text_hash,
    unpack_vector,
)
from iwtt.store import Vault

THOUGHTS = {
    # "timer" lives in the mock's `scheduling` concept group, as do "clock"
    # and "schedule" — so a query using those words retrieves this thought
    # with NO lexical overlap at all.
    "timer": ("The proofing timer needs a humidity sensor.", "Proofing timer"),
    "grief": ("Graveyard visits helped me accept the ending.", "On endings"),
    "dungeon": ("A procedural dungeon needs a stable seed per floor.", "Seeded dungeons"),
    "ranking": ("Deterministic ranking matters more than clever ranking.", "Determinism"),
}


class VectorMathTestCase(unittest.TestCase):
    def test_pack_and_unpack_round_trip_exactly(self):
        vector = [0.25, -1.5, 3.0, 0.0]
        self.assertEqual(unpack_vector(pack_vector(vector)), vector)

    def test_packed_vectors_are_four_bytes_per_dimension(self):
        self.assertEqual(len(pack_vector([1.0] * 7)), 28)

    def test_cosine_of_identical_vectors_is_one(self):
        self.assertAlmostEqual(cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 1.0)

    def test_cosine_of_orthogonal_vectors_is_zero(self):
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_cosine_of_opposite_vectors_is_minus_one(self):
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [-1.0, 0.0]), -1.0)

    def test_mismatched_or_zero_vectors_are_safe(self):
        self.assertEqual(cosine_similarity([1.0], [1.0, 2.0]), 0.0)
        self.assertEqual(cosine_similarity([], []), 0.0)
        self.assertEqual(cosine_similarity([0.0, 0.0], [1.0, 1.0]), 0.0)

    def test_precomputed_norms_do_not_change_the_result(self):
        first, second = [1.0, 2.0, 3.0], [4.0, 5.0, 6.0]
        plain = cosine_similarity(first, second)
        with_norms = cosine_similarity(
            first, second,
            first_norm=math.sqrt(sum(v * v for v in first)),
            second_norm=math.sqrt(sum(v * v for v in second)),
        )
        self.assertAlmostEqual(plain, with_norms)

    def test_normalisation_produces_a_unit_vector(self):
        unit = normalize_vector([3.0, 4.0])
        self.assertAlmostEqual(unit[0], 0.6)
        self.assertAlmostEqual(unit[1], 0.8)
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in unit)), 1.0)

    def test_text_hash_is_stable_and_sensitive_to_both_fields(self):
        self.assertEqual(text_hash("a", "b"), text_hash("a", "b"))
        self.assertNotEqual(text_hash("a", "b"), text_hash("a", "c"))
        self.assertNotEqual(text_hash("a", "b"), text_hash("a!", "b"))


class MockEmbeddingTestCase(unittest.TestCase):
    def setUp(self):
        self.provider = MockProvider()

    def test_vectors_are_deterministic_bounded_and_unit_length(self):
        first = self.provider.embed_texts(["A proofing timer for dough."])
        second = self.provider.embed_texts(["A proofing timer for dough."])
        self.assertEqual(first, second)
        self.assertEqual(len(first[0]), self.provider._MOCK_EMBEDDING_DIM)
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in first[0])), 1.0)

    def test_an_empty_text_embeds_to_a_zero_vector_without_raising(self):
        vector = self.provider.embed_texts([""])[0]
        self.assertEqual(len(vector), self.provider._MOCK_EMBEDDING_DIM)
        self.assertTrue(all(value == 0.0 for value in vector))

    def test_different_words_for_one_concept_are_closer_than_unrelated_text(self):
        timer = self.provider.embed_texts(["The timer rings."])[0]
        clock = self.provider.embed_texts(["A clock on the wall."])[0]
        grief = self.provider.embed_texts(["Mourning an ending."])[0]
        self.assertGreater(cosine_similarity(timer, clock), cosine_similarity(timer, grief))

    def test_the_batch_size_does_not_change_the_vectors(self):
        texts = ["timer", "dough", "dungeon seed"]
        batched = self.provider.embed_texts(texts)
        individually = [self.provider.embed_texts([text])[0] for text in texts]
        self.assertEqual(batched, individually)


class _FixedProvider(MockProvider):
    """A provider that lies in controllable ways, for failure-path tests."""

    def __init__(self, mode):
        self.mode = mode
        self.calls: list[list[str]] = []

    def embed_texts(self, texts):
        self.calls.append(list(texts))
        if self.mode == "wrong-count":
            return [[1.0, 0.0]] * max(0, len(texts) - 1)
        if self.mode == "empty":
            return [[] for _ in texts]
        if self.mode == "inconsistent":
            return [[1.0, 0.0] if index % 2 else [1.0, 0.0, 2.0] for index in range(len(texts))]
        if self.mode == "raises":
            raise RuntimeError("endpoint exploded")
        return super().embed_texts(texts)


class _AlternativeModelProvider(MockProvider):
    model = "some-other-embedding-model"


class SemanticFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self._tmp.name) / "vault.sqlite3", ai_provider=NoAIProvider())
        self.vault.initialize()
        self.ids = {
            key: self.vault.create_thought(text, "THOUGHT", title)["id"]
            for key, (text, title) in THOUGHTS.items()
        }
        self.vault.rebuild_recall_indexes(full=True)
        self.index = EmbeddingIndex(self.vault)

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, provider=None, **kwargs):
        # Configure the provider on the vault too, exactly as the Data &
        # privacy UI does: an index alone cannot answer a query, the query
        # must be embedded with the same provider at recall time.
        provider = provider or MockProvider()
        self.vault.ai_provider = provider
        return self.index.build(provider, **kwargs)


class BuildAndStalenessTestCase(SemanticFixture):
    def test_building_without_a_provider_is_refused_with_a_readable_error(self):
        with self.assertRaises(AIUnavailable) as caught:
            self.index.build(NoAIProvider())
        self.assertIn("provider", str(caught.exception).lower())

    def test_a_build_embeds_every_indexed_thought_and_reports_status(self):
        result = self.build()
        self.assertEqual(result["built"], len(THOUGHTS))
        self.assertTrue(result["upToDate"])
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(result["dimensions"], MockProvider._MOCK_EMBEDDING_DIM)
        status = self.index.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["count"], len(THOUGHTS))
        self.assertEqual(status["model"], MockProvider.name)

    def test_a_second_build_is_a_no_op(self):
        self.build()
        again = self.build()
        self.assertEqual(again["built"], 0)
        self.assertTrue(again["upToDate"])

    def test_editing_a_thought_makes_exactly_that_row_stale(self):
        self.build()
        self.vault.update_thought(self.ids["timer"], "The proofing timer now also logs humidity.")
        result = self.build()
        self.assertEqual(result["built"], 1)
        self.assertEqual(result["total"], len(THOUGHTS))
        self.assertTrue(result["upToDate"])

    def test_switching_embedding_models_marks_every_row_for_rebuild(self):
        self.build()
        result = self.build(provider=_AlternativeModelProvider())
        self.assertEqual(result["built"], len(THOUGHTS))
        self.assertEqual(result["model"], "some-other-embedding-model")

    def test_a_build_limit_bounds_the_work_and_reports_what_remains(self):
        result = self.build(limit=2)
        self.assertEqual(result["built"], 2)
        self.assertEqual(result["remaining"], len(THOUGHTS) - 2)
        self.assertFalse(result["upToDate"])

    def test_the_provider_receives_only_the_texts_it_embeds(self):
        provider = _FixedProvider("normal")
        self.build(provider=provider, limit=2)
        self.assertEqual(sum(len(call) for call in provider.calls), 2)

    def test_wrong_number_of_vectors_is_reported_not_stored(self):
        with self.assertRaises(AIUnavailable):
            self.build(provider=_FixedProvider("wrong-count"))
        self.assertEqual(self.index.status()["count"], 0)

    def test_empty_vectors_are_reported_not_stored(self):
        with self.assertRaises(AIUnavailable):
            self.build(provider=_FixedProvider("empty"))
        self.assertEqual(self.index.status()["count"], 0)

    def test_inconsistent_dimensions_roll_the_whole_batch_back(self):
        with self.assertRaises(ValueError):
            self.build(provider=_FixedProvider("inconsistent"))
        self.assertEqual(self.index.status()["count"], 0)

    def test_a_provider_exception_is_propagated_and_stores_nothing(self):
        with self.assertRaises(RuntimeError):
            self.build(provider=_FixedProvider("raises"))
        self.assertEqual(self.index.status()["count"], 0)

    def test_erasing_removes_every_vector_and_loses_nothing_canonical(self):
        before = self.vault.canonical_fingerprint()
        self.build()
        self.assertEqual(self.index.erase(), len(THOUGHTS))
        self.assertFalse(self.index.status()["available"])
        self.assertEqual(self.index.status()["count"], 0)
        self.assertEqual(self.vault.canonical_fingerprint(), before)

    def test_clearing_derived_data_wipes_the_embedding_table(self):
        self.build()
        self.vault.clear_derived_data()
        self.assertEqual(self.index.status()["count"], 0)

    def test_the_derived_table_stores_no_readable_thought_text(self):
        import sqlite3

        self.build()
        # Open a short-lived raw connection so no handle is left open for
        # tearDown's Windows temp-dir cleanup.
        conn = sqlite3.connect(self.vault.db_path)
        try:
            rows = conn.execute("SELECT vector, text_hash FROM recall_embeddings").fetchall()
        finally:
            conn.close()
        for vector_blob, digest in rows:
            self.assertIsInstance(vector_blob, bytes)
            self.assertEqual(len(digest), 64)
            self.assertNotIn(b"timer", vector_blob.lower())
            self.assertNotIn(b"graveyard", vector_blob.lower())

    def test_the_embedding_table_cascades_from_its_thought(self):
        # Thoughts are never deleted by the app (there is no automatic
        # deletion), but the derived table must still be structurally unable
        # to outlive the memory it describes.
        import sqlite3

        conn = sqlite3.connect(self.vault.db_path)
        try:
            keys = conn.execute("PRAGMA foreign_key_list(recall_embeddings)").fetchall()
        finally:
            conn.close()
        targets = {(row[2], row[3], row[4], row[6]) for row in keys}
        self.assertIn(("thoughts", "thought_id", "id", "CASCADE"), targets)

    def test_the_embedding_table_cascades_from_its_thought(self):
        # Thoughts are never deleted by the app (there is no automatic
        # deletion), but the derived table must still be structurally unable
        # to outlive the memory it describes.
        import sqlite3

        conn = sqlite3.connect(self.vault.db_path)
        try:
            keys = conn.execute("PRAGMA foreign_key_list(recall_embeddings)").fetchall()
        finally:
            conn.close()
        targets = {(row[2], row[3], row[4], row[6]) for row in keys}
        self.assertIn(("thoughts", "thought_id", "id", "CASCADE"), targets)


class SemanticRecallTestCase(SemanticFixture):
    def _ranked_signature(self, outcome):
        return [
            (result.thought_id, round(result.score, 6), result.state)
            for result in outcome.results
        ]

    def test_without_an_index_recall_is_exactly_unchanged(self):
        outcome = self.vault.recall("clock")
        self.assertFalse(outcome.semantic["enabled"])
        self.assertIn("No embedding index", outcome.semantic["reason"])
        self.assertEqual(outcome.results, [])
        self.assertIn("OFF", outcome.honesty["embeddings"])

    def test_a_query_with_no_shared_words_still_finds_the_right_thought(self):
        self.build()
        outcome = self.vault.recall("clock schedule")
        self.assertTrue(outcome.semantic["enabled"])
        ids = [entry["thoughtId"] for entry in outcome.semantic["results"]]
        self.assertIn(self.ids["timer"], ids)
        # Nothing above should have entered the scored ranking.
        self.assertEqual([r.thought_id for r in outcome.results], [])

    def test_semantic_results_are_ordered_by_similarity_then_id(self):
        self.build()
        entries = self.vault.recall("clock schedule").semantic["results"]
        keys = [(-entry["similarity"], entry["thoughtId"]) for entry in entries]
        self.assertEqual(keys, sorted(keys))

    def test_the_semantic_section_never_duplicates_a_scored_result(self):
        self.build()
        outcome = self.vault.recall("proofing timer humidity")
        ranked = {result.thought_id for result in outcome.results}
        semantic = {entry["thoughtId"] for entry in outcome.semantic["results"]}
        self.assertFalse(ranked & semantic)

    def test_the_scored_ranking_is_byte_identical_with_the_stage_on_or_off(self):
        query = "deterministic ranking"
        without = self._ranked_signature(self.vault.recall(query))
        self.build()
        with_stage = self.vault.recall(query)
        self.assertTrue(with_stage.semantic["enabled"])
        self.assertEqual(self._ranked_signature(with_stage), without)

    def test_repeating_a_query_returns_the_identical_semantic_list(self):
        self.build()
        first = self.vault.recall("clock schedule").semantic["results"]
        second = self.vault.recall("clock schedule").semantic["results"]
        self.assertEqual(first, second)

    def test_every_hit_references_a_real_original_thought(self):
        self.build()
        outcome = self.vault.recall("clock schedule")
        for entry in outcome.semantic["results"]:
            thought = self.vault.get_thought(entry["thoughtId"])
            self.assertIsNotNone(thought, "a semantic hit must reference a real thought")
            self.assertTrue(entry["displayTitle"])
            self.assertIn(entry["displayTitle"], (thought["title"], thought["displayTitle"]))

    def test_the_honesty_block_changes_truthfully_when_the_stage_runs(self):
        self.build()
        outcome = self.vault.recall("clock schedule")
        self.assertTrue(outcome.honesty["embeddings"].startswith("ON"))
        self.assertIn("One request", outcome.honesty["network"])
        self.assertIn(MockProvider.name, outcome.honesty["network"])
        self.assertNotIn("Not used", outcome.honesty["ai"])

    def test_an_existing_index_with_no_provider_degrades_with_a_reason(self):
        self.build()
        baseline = self._ranked_signature(self.vault.recall("deterministic ranking"))
        self.vault.ai_provider = NoAIProvider()
        outcome = self.vault.recall("deterministic ranking")
        self.assertFalse(outcome.semantic["enabled"])
        self.assertIn("no ai provider", outcome.semantic["reason"].lower())
        self.assertEqual(outcome.semantic["indexedCount"], len(THOUGHTS))
        self.assertEqual(self._ranked_signature(outcome), baseline)

    def test_a_query_embedding_failure_degrades_without_touching_the_ranking(self):
        self.build()
        baseline = self._ranked_signature(self.vault.recall("deterministic ranking"))
        self.vault.ai_provider = _FixedProvider("raises")
        outcome = self.vault.recall("deterministic ranking")
        self.assertFalse(outcome.semantic["enabled"])
        self.assertIn("could not embed", outcome.semantic["reason"])
        self.assertEqual(self._ranked_signature(outcome), baseline)

    def test_a_model_mismatch_is_counted_rather_than_mixed_silently(self):
        self.build()
        self.vault.ai_provider = _AlternativeModelProvider()
        outcome = self.vault.recall("clock schedule")
        self.assertTrue(outcome.semantic["enabled"])
        self.assertEqual(outcome.semantic["modelMismatchRows"], len(THOUGHTS))
        self.assertEqual(outcome.semantic["results"], [])

    def test_the_similarity_floor_is_reported_and_enforced(self):
        self.build()
        outcome = self.vault.recall("clock schedule")
        floor = outcome.semantic["minSimilarity"]
        self.assertEqual(floor, DEFAULT_CONFIG.embedding_min_similarity)
        self.assertTrue(all(entry["similarity"] >= floor for entry in outcome.semantic["results"]))

    def test_the_semantic_call_exposes_the_model_that_produced_vectors(self):
        self.build()
        outcome = self.vault.recall("clock schedule")
        self.assertEqual(outcome.semantic["model"], MockProvider.name)
        self.assertEqual(outcome.semantic["indexedModel"], MockProvider.name)


class SemanticHttpTestCase(unittest.TestCase):
    def setUp(self):
        from iwtt.server import IWTTHTTPServer, RequestHandler

        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self._tmp.name) / "vault.sqlite3", ai_provider=NoAIProvider())
        self.vault.initialize()
        self.ids = {
            key: self.vault.create_thought(text, "THOUGHT", title)["id"]
            for key, (text, title) in THOUGHTS.items()
        }
        self.vault.rebuild_recall_indexes(full=True)
        self.server = IWTTHTTPServer(("127.0.0.1", 0), RequestHandler, self.vault)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def _request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body,
            headers={"Content-Type": "application/json"}, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_status_starts_unbuilt(self):
        status, body = self._request("GET", "/api/recall/embeddings")
        self.assertEqual(status, 200)
        self.assertFalse(body["available"])
        self.assertEqual(body["count"], 0)
        self.assertIn("DERIVED", body["boundary"])

    def test_building_without_a_provider_is_503_not_500(self):
        status, body = self._request("POST", "/api/recall/embeddings/build", {})
        self.assertEqual(status, 503)
        self.assertIn("error", body)

    def test_build_with_the_mock_then_status_then_erase(self):
        status, _ = self._request("POST", "/api/ai/configure", {"provider": "mock"})
        self.assertEqual(status, 200)
        status, body = self._request("POST", "/api/recall/embeddings/build", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["built"], len(THOUGHTS))
        self.assertIn("boundary", body)
        status, status_body = self._request("GET", "/api/recall/embeddings")
        self.assertEqual(status, 200)
        self.assertEqual(status_body["count"], len(THOUGHTS))
        status, erased = self._request("DELETE", "/api/recall/embeddings")
        self.assertEqual(status, 200)
        self.assertEqual(erased["erased"], len(THOUGHTS))
        self.assertEqual(self.vault.recall_embeddings_status()["count"], 0)

    def test_an_invalid_limit_is_a_validation_error(self):
        self._request("POST", "/api/ai/configure", {"provider": "mock"})
        for bad in (0, -5, "10", 10_001):
            with self.subTest(limit=bad):
                status, _ = self._request("POST", "/api/recall/embeddings/build", {"limit": bad})
                self.assertEqual(status, 400)

    def test_find_my_thought_carries_the_semantic_section_over_http(self):
        self._request("POST", "/api/ai/configure", {"provider": "mock"})
        self._request("POST", "/api/recall/embeddings/build", {})
        status, body = self._request(
            "GET", "/api/recall/find?q=clock%20schedule"
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["semantic"]["enabled"])
        ids = [entry["thoughtId"] for entry in body["semantic"]["results"]]
        self.assertIn(self.ids["timer"], ids)
        self.assertIn("honestyStatement", body)
        self.assertTrue(body["honestyStatement"]["embeddings"].startswith("ON"))

    def test_the_semantic_routes_do_not_change_the_configured_provider(self):
        self._request("POST", "/api/ai/configure", {"provider": "mock"})
        provider = self.vault.ai_provider
        self._request("GET", "/api/recall/embeddings")
        self._request("POST", "/api/recall/embeddings/build", {})
        self.assertIs(self.vault.ai_provider, provider)


if __name__ == "__main__":
    unittest.main()
