"""Cross-memory reasoning: derived findings, original-memory citations, approval.

These tests exercise the Thinking Partner's analytical engine with and without
an AI provider.  The baseline must work with no provider, no key and no network;
a provider may add a labelled proposal but must never introduce an invented
memory or write a canonical relationship.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from iwtt.ai import AIProvider, MockProvider, NoAIProvider
from iwtt.recall.embeddings import EmbeddingIndex
from iwtt.recall.reasoning import (
    MODES,
    REASONING_ARTIFACT_TYPES,
    ReasoningEngine,
    citation,
)
from iwtt.store import Vault


class _BrokenProvider(AIProvider):
    name = "BrokenProvider"
    available = True
    source = "mock"

    def assess_links(self, thought, candidates):
        raise RuntimeError("the model refused the request")

    def interpret(self, thought):
        raise RuntimeError("the endpoint timed out")


class _InvalidProvider(AIProvider):
    """Returns malformed responses that must be filtered, never trusted."""

    name = "InvalidProvider"
    available = True
    source = "mock"
    model = "invalid-model"

    def assess_links(self, thought, candidates):
        return [
            {"targetId": "not-a-real-thought", "linked": True, "type": "RELATED_TO",
             "reason": "an invented memory"},
            {"targetId": candidates[0]["id"] if candidates else "x", "linked": "yes",
             "type": "RELATED_TO", "reason": "bad boolean"},
            {"targetId": candidates[-1]["id"] if candidates else "x", "linked": True,
             "type": "NOT_IN_VOCABULARY", "reason": "a reviewable reason"},
        ]


class _RecordingProvider(MockProvider):
    def __init__(self):
        self.received_source: list[str] = []
        self.received_candidates: list[str] = []

    def assess_links(self, thought, candidates):
        self.received_source.append(str(thought.get("currentText") or ""))
        self.received_candidates.extend(str(item.get("currentText") or "") for item in candidates)
        return super().assess_links(thought, candidates)


class ReasoningFixture:
    def build(self, provider: Any | None = None) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self.temp_dir.name) / "vault.sqlite3",
                           ai_provider=provider or NoAIProvider())
        self.vault.initialize()
        self.a = self.vault.create_thought(
            "The enemy should always remember the last fight so the strategy adapts.",
            "THOUGHT", "Enemy memory", ["combat", "memory"],
        )
        self.b = self.vault.create_thought(
            "The enemy should never remember the last fight; a new strategy should replace it.",
            "THOUGHT", "Enemy rewrite", ["combat", "memory"],
        )
        self.c = self.vault.create_thought(
            "A bread timer senses dough temperature before the oven bakes it.",
            "PROJECT", "Proofing timer", ["baking"],
        )
        self.d = self.vault.create_thought(
            "A bread clock tracks dough proofing time for the oven and the crumb.",
            "THOUGHT", "Proofing clock", ["baking"],
        )
        self.e = self.vault.create_thought(
            "I keep a bread timer and dough notes beside the oven every morning.",
            "THOUGHT", "Kitchen notes", ["baking"],
        )
        self.vault.update_thought(self.a["id"], current_text=(
            "The enemy should always remember the last fight so the strategy adapts. "
            "Later I doubted whether remembering should be automatic."
        ))
        self.engine = ReasoningEngine(self.vault, provider)

    def cleanup(self) -> None:
        self.temp_dir.cleanup()


def _original_hashes(vault: Vault) -> dict[str, str]:
    return {item["id"]: hashlib.sha256(
        (item["originalText"] + item["currentText"]).encode("utf-8")
    ).hexdigest() for item in vault.list_thoughts(status="ALL")}


class ReasoningBaselineTestCase(unittest.TestCase):
    """All six modes must work with no provider at all."""

    def setUp(self) -> None:
        self.f = ReasoningFixture()
        self.f.build()

    def tearDown(self) -> None:
        self.f.cleanup()

    def test_every_mode_is_available_without_an_ai_provider(self):
        self.assertEqual(len(MODES), 6)
        for mode in MODES:
            with self.subTest(mode=mode):
                result = self.f.engine.run(mode, thought_id=self.f.c["id"], query="bread timer")
                self.assertFalse(result["canonical"])
                self.assertEqual(result["authority"], "DERIVED_REASONING")
                self.assertIn("Local deterministic reasoning", result["provider"])
                self.assertIn("findings", result)
                self.assertIn("citedThoughtIds", result)

    def test_similarity_ranks_shared_distinctive_terms_and_skips_source(self):
        result = self.f.engine.run("similar", thought_id=self.f.c["id"], limit=3)
        ids = [item["thoughtId"] for item in result["findings"]]
        self.assertNotIn(self.f.c["id"], ids)
        self.assertTrue(ids)
        self.assertTrue({self.f.d["id"], self.f.e["id"]} & set(ids))
        scores = [item["score"] for item in result["findings"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for item in result["findings"]:
            self.assertTrue(item["sharedTerms"])
            self.assertEqual(item["citation"]["thoughtId"], item["thoughtId"])

    def test_similarity_has_no_semantic_section_until_an_index_exists(self):
        result = self.f.engine.run("similar", thought_id=self.f.c["id"])
        self.assertNotIn("semanticNeighbours", result)

    def test_semantic_neighbours_are_labelled_and_never_change_the_lexical_ranking(self):
        self.f.vault.ai_provider = MockProvider()
        self.f.vault.rebuild_recall_indexes(full=True)
        engine = ReasoningEngine(self.f.vault, MockProvider())
        build = EmbeddingIndex(self.f.vault).build(MockProvider(), limit=100)
        self.assertGreater(build["built"], 0)
        baseline = self.f.engine.run("similar", thought_id=self.f.c["id"])
        with_semantics = engine.run("similar", thought_id=self.f.c["id"])
        self.assertEqual(baseline["findings"], with_semantics["findings"])
        section = with_semantics["semanticNeighbours"]
        self.assertTrue(section["available"])
        self.assertNotIn("score", section["results"][0])
        self.assertIn("similarity", section["results"][0])

    def test_connection_proposals_require_approval_and_write_nothing(self):
        before = _original_hashes(self.f.vault)
        result = self.f.engine.run("connections", thought_id=self.f.c["id"], limit=5)
        self.assertGreater(result["examinedCount"], 0)
        for proposal in result["findings"]:
            self.assertTrue(proposal["requiresApproval"])
            self.assertEqual(proposal["status"], "SUGGESTED")
            self.assertEqual(proposal["sourceId"], self.f.c["id"])
            self.assertEqual(proposal["target"]["thoughtId"], proposal["targetId"])
        with self.f.vault._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0], 0)
        self.assertEqual(_original_hashes(self.f.vault), before)

    def test_connection_screen_reports_rejections_instead_of_silence(self):
        result = self.f.engine.run("connections", thought_id=self.f.c["id"], limit=20)
        candidates = self.f.vault.link_candidates(self.f.c["id"], limit=16)
        self.assertEqual(result["examinedCount"], len(candidates))
        self.assertGreaterEqual(result["notLinkedCount"], 0)
        self.assertIn("Examined", result["note"])

    def test_themes_are_derived_labels_backed_by_separate_captures(self):
        result = self.f.engine.run("themes", limit=6)
        labels = [item["label"] for item in result["findings"]]
        self.assertTrue(labels)
        baking = {self.f.c["id"], self.f.d["id"], self.f.e["id"]}
        self.assertTrue(any(baking & set(item["thoughtIds"]) for item in result["findings"]),
                        "the recurring baking captures must form at least one theme")
        for theme in result["findings"]:
            self.assertEqual(theme["authority"], "AUTOMATICALLY_DISCOVERED_THEME")
            self.assertGreaterEqual(len(theme["thoughtIds"]), 2)
            self.assertTrue(theme["citations"])

    def test_contradiction_detection_cites_both_original_captures(self):
        result = self.f.engine.run("contradictions", limit=8)
        pairs = [set(item["thoughtIds"]) for item in result["findings"]]
        self.assertIn({self.f.a["id"], self.f.b["id"]}, pairs)
        for item in result["findings"]:
            self.assertIn("possible tension", item["caveat"].lower())
            self.assertEqual(len(item["thoughtIds"]), 2)

    def test_evolution_includes_originals_revisions_and_events_in_order(self):
        result = self.f.engine.run("evolution", query="enemy strategy", limit=5)
        kinds = {item["kind"] for item in result["findings"]}
        self.assertIn("ORIGINAL_CAPTURE", kinds)
        self.assertIn("REVISION", kinds)
        self.assertIn("EVENT", kinds)
        originals = [item for item in result["findings"] if item["kind"] == "ORIGINAL_CAPTURE"]
        self.assertEqual({item["authority"] for item in originals}, {"CANONICAL_ORIGINAL"})
        timestamps = [item["timestamp"] for item in result["findings"] if item["timestamp"]]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_evolution_requires_a_topic_or_a_thought(self):
        with self.assertRaises(ValueError):
            self.f.engine.run("evolution", query="")

    def test_contextual_explanation_only_grounds_itself_in_real_memories(self):
        result = self.f.engine.run("explain", thought_ids=[self.f.a["id"], self.f.b["id"]],
                                   use_provider=False)
        ids = result["citedThoughtIds"]
        self.assertEqual(set(ids), {self.f.a["id"], self.f.b["id"]})
        valid = {item["id"] for item in self.f.vault.list_thoughts(status="ALL")}
        for point in result["findings"]:
            for evidence in point["evidence"]:
                self.assertIn(evidence["thoughtId"], valid)
                self.assertTrue(evidence["excerpt"])
                self.assertIn("uncertainty", point)

    def test_explanation_by_query_falls_back_to_no_answer_rather_than_inventing(self):
        result = self.f.engine.run("explain", query="xylophone kumquat oblivion")
        self.assertEqual(result["findings"], [])
        self.assertIn("no original thoughts", result["summary"].lower())

    def test_reasoning_never_changes_canonical_bytes(self):
        before = _original_hashes(self.f.vault)
        for mode in MODES:
            self.f.engine.run(mode, thought_id=self.f.c["id"], query="bread timer")
        self.assertEqual(_original_hashes(self.f.vault), before)

    def test_unknown_mode_and_out_of_range_limit_are_refused(self):
        with self.assertRaises(ValueError):
            self.f.engine.run("invent", query="bread")
        with self.assertRaises(ValueError):
            self.f.engine.run("themes", limit=0)
        with self.assertRaises(ValueError):
            self.f.engine.run("themes", limit=21)

    def test_citations_are_bounded_reads_from_the_canonical_entry(self):
        ref = citation(self.f.c)
        self.assertEqual(ref["thoughtId"], self.f.c["id"])
        self.assertLessEqual(len(ref["excerpt"]), 221)


class ReasoningProviderTestCase(unittest.TestCase):
    """Provider independence, failures and malformed responses."""

    def setUp(self) -> None:
        self.f = ReasoningFixture()
        self.f.build()

    def tearDown(self) -> None:
        self.f.cleanup()

    def test_mock_provider_adds_a_labelled_view_without_losing_the_baseline(self):
        provider = MockProvider()
        engine = ReasoningEngine(self.f.vault, provider)
        baseline = self.f.engine.run("explain", thought_ids=[self.f.a["id"]])
        enriched = engine.run("explain", thought_ids=[self.f.a["id"]], use_provider=True)
        self.assertEqual(baseline["findings"], enriched["findings"])
        view = enriched["providerView"]
        self.assertEqual(view["authority"], "AI_INTERPRETATION_PROPOSAL")
        self.assertEqual(view["thoughtId"], self.f.a["id"])
        self.assertIn("proposal", view["caveat"].lower())

    def test_switching_the_provider_back_to_none_restores_the_baseline(self):
        provider = MockProvider()
        enriched = ReasoningEngine(self.f.vault, provider).run("explain", thought_ids=[self.f.a["id"]])
        self.assertIn("providerView", enriched)
        self.f.vault.ai_provider = NoAIProvider()
        baseline = ReasoningEngine(self.f.vault, self.f.vault.ai_provider).run(
            "explain", thought_ids=[self.f.a["id"]])
        self.assertNotIn("providerView", baseline)
        self.assertIn("Local deterministic", baseline["provider"])

    def test_missing_credentials_and_no_provider_are_not_errors(self):
        engine = ReasoningEngine(self.f.vault, None)
        result = engine.run("connections", thought_id=self.f.c["id"])
        self.assertEqual(result["warnings"], [])
        self.assertTrue(result["findings"])

    def test_provider_failure_degrades_to_the_baseline_with_a_warning(self):
        engine = ReasoningEngine(self.f.vault, _BrokenProvider())
        for mode, kwargs in (("connections", {"thought_id": self.f.c["id"]}),
                             ("explain", {"thought_ids": [self.f.a["id"]]})):
            with self.subTest(mode=mode):
                result = engine.run(mode, use_provider=True, **kwargs)
                self.assertTrue(result["warnings"])
                self.assertTrue(result["findings"])

    def test_invalid_provider_ids_and_types_are_filtered_out(self):
        candidates = self.f.vault.link_candidates(self.f.c["id"], limit=8)
        engine = ReasoningEngine(self.f.vault, _InvalidProvider())
        result = engine.run("connections", thought_id=self.f.c["id"], limit=8)
        valid = {item["id"] for item in self.f.vault.list_thoughts(status="ALL")}
        # The invented id and the non-boolean verdict are dropped; only the
        # supplied target with an out-of-vocabulary type survives, normalised.
        self.assertEqual(len(result["findings"]), 1)
        proposal = result["findings"][0]
        self.assertEqual(proposal["targetId"], candidates[-1]["id"])
        self.assertIn(proposal["targetId"], valid)
        self.assertNotEqual(proposal["sourceId"], proposal["targetId"])
        self.assertEqual(proposal["type"], "RELATED_TO")
        self.assertEqual(proposal["provenance"], "AI_SUGGESTED")
        self.assertEqual(proposal["basis"], "provider assessment")
        self.assertTrue(proposal["requiresApproval"])

    def test_the_provider_receives_only_the_source_and_supplied_candidates(self):
        provider = _RecordingProvider()
        engine = ReasoningEngine(self.f.vault, provider)
        engine.run("connections", thought_id=self.f.c["id"], use_provider=True)
        self.assertEqual(len(provider.received_source), 1)
        self.assertIn("bread timer", provider.received_source[0])
        self.assertLessEqual(len(provider.received_candidates), 16)

    def test_provider_never_writes_a_relationship(self):
        engine = ReasoningEngine(self.f.vault, MockProvider())
        engine.run("connections", thought_id=self.f.c["id"], limit=8)
        with self.f.vault._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0], 0)


class ReasoningHttpAndApprovalTestCase(unittest.TestCase):
    """HTTP routes, explicit saves, and the existing approval workflow."""

    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _SilentHandler)
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        from iwtt.server import IWTTHTTPServer, RequestHandler

        self.f = ReasoningFixture()
        self.f.build()
        self.server = IWTTHTTPServer(("127.0.0.1", 0), RequestHandler, self.f.vault)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.f.cleanup()

    def _request(self, path, payload=None, method="POST"):
        data = json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_status_route_publishes_modes_and_boundary(self):
        status, body = self._request("/api/reasoning", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(body["modes"], list(MODES))
        self.assertIn("never writes a relationship", body["boundary"])
        self.assertFalse(body["providerAvailable"])

    def test_reasoning_route_works_over_http_without_a_provider(self):
        status, body = self._request("/api/reasoning", {"mode": "themes", "limit": 5})
        self.assertEqual(status, 200)
        self.assertTrue(body["findings"])
        self.assertFalse(body["canonical"])

    def test_unknown_mode_and_malformed_payload_are_validation_errors(self):
        self.assertEqual(self._request("/api/reasoning", {"mode": "invent"})[0], 400)
        self.assertEqual(self._request("/api/reasoning", {"mode": "themes", "thoughtIds": "x"})[0], 400)

    def test_saving_a_finding_creates_a_derived_artifact_only(self):
        _, result = self._request("/api/reasoning", {"mode": "themes", "limit": 4})
        status, body = self._request("/api/reasoning/save", {"mode": "themes", "finding": result})
        self.assertEqual(status, 201)
        artifact = body["artifact"]
        self.assertEqual(artifact["artifactType"], REASONING_ARTIFACT_TYPES["themes"])
        self.assertFalse(body["canonical"])
        with self.f.vault._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM derived_artifacts").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0], 0)
        thought = self.f.vault.get_thought(artifact["thoughtId"])
        self.assertEqual(thought["derivedArtifacts"][0]["artifactType"],
                         REASONING_ARTIFACT_TYPES["themes"])

    def test_save_requires_a_real_mode_and_an_original_thought(self):
        self.assertEqual(self._request("/api/reasoning/save", {
            "mode": "invent", "finding": {"citedThoughtIds": [self.f.a["id"]]}
        })[0], 400)
        self.assertEqual(self._request("/api/reasoning/save", {
            "mode": "themes", "finding": {"citedThoughtIds": []}
        })[0], 400)

    def test_a_saved_finding_is_erased_with_all_derived_data(self):
        _, result = self._request("/api/reasoning", {"mode": "themes", "limit": 3})
        self._request("/api/reasoning/save", {"mode": "themes", "finding": result})
        before = _original_hashes(self.f.vault)
        self.f.vault.delete_all_derived_intelligence()
        with self.f.vault._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM derived_artifacts").fetchone()[0], 0)
        self.assertEqual(_original_hashes(self.f.vault), before)

    def test_a_connection_proposal_is_only_saved_and_confirmed_by_a_person(self):
        _, result = self._request("/api/reasoning", {
            "mode": "connections", "thoughtId": self.f.c["id"], "limit": 8,
        })
        self.assertTrue(result["findings"])
        proposal = result["findings"][0]
        status, saved = self._request("/api/relationships", {
            "sourceId": proposal["sourceId"], "targetId": proposal["targetId"],
            "type": proposal["type"], "provenance": proposal["provenance"],
        })
        self.assertEqual(status, 201)
        relationship = saved["relationship"]
        self.assertEqual(relationship["status"], "SUGGESTED")
        self.assertEqual(proposal["provenance"], "AI_SUGGESTED")
        self.assertEqual(relationship["provenance"], proposal["provenance"])
        confirmed = self.f.vault.confirm_relationship(relationship["id"])
        self.assertEqual(confirmed["status"], "CONFIRMED")
        self.assertEqual(confirmed["confirmedBy"], "USER")


class _SilentHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass


if __name__ == "__main__":
    unittest.main()
