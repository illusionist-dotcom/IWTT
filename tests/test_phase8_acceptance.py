"""Phase 8 acceptance gate — the twelve required guarantees, end to end.

Each test is named after one acceptance category and drives the real ``Vault``,
``ReasoningEngine``, recall engine and HTTP routes. Only the *optional* AI
provider is stubbed: the central promise is that every capability works with no
provider, no key and no network, and that a provider can only ever add a
labelled proposal.

Run with::

    python -m unittest discover -s tests -p "test_phase8_acceptance.py"
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

from iwtt.ai import AIProvider, MockProvider, NoAIProvider, OpenAICompatibleProvider
from iwtt.recall.config import DEFAULT_CONFIG
from iwtt.recall.embeddings import EmbeddingIndex
from iwtt.recall.reasoning import MODES, ReasoningEngine
from iwtt.store import CANONICAL_SCHEMA_VERSION, Vault


class _BrokenProvider(AIProvider):
    name = "BrokenProvider"
    available = True
    source = "mock"

    def assess_links(self, thought, candidates):
        raise RuntimeError("endpoint timed out")

    def interpret(self, thought):
        raise RuntimeError("the model refused the request")


class _InvalidProvider(AIProvider):
    """Returns invented ids and malformed verdicts that must be discarded."""

    name = "InvalidProvider"
    available = True
    source = "mock"

    def assess_links(self, thought, candidates):
        return [
            {"targetId": "invented-memory", "linked": True, "type": "RELATED_TO",
             "reason": "an invented memory"},
            {"targetId": candidates[0]["id"] if candidates else "x", "linked": "yes",
             "type": "RELATED_TO", "reason": "bad boolean"},
        ]

    def interpret(self, thought):
        return {"summary": "ok", "concepts": ["invented"], "openQuestions": [],
                "possibleNextSteps": [], "citedThoughtIds": ["invented-memory"]}


class AcceptanceFixture:
    """A small vault with a contradiction pair, a similarity pair and a theme."""

    def build(self, provider: Any | None = None) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self.temp_dir.name) / "vault.sqlite3",
                           ai_provider=provider or NoAIProvider())
        self.vault.initialize()
        self.always = self.vault.create_thought(
            "The enemy should always remember the last fight so the strategy adapts.",
            "THOUGHT", "Enemy memory", ["combat", "memory"],
        )
        self.never = self.vault.create_thought(
            "The enemy should never remember the last fight; a new strategy should replace it.",
            "THOUGHT", "Enemy rewrite", ["combat", "memory"],
        )
        self.timer = self.vault.create_thought(
            "The proofing timer needs a humidity sensor.",
            "PROJECT", "Proofing timer", ["baking"],
        )
        self.clock = self.vault.create_thought(
            "A bread clock tracks dough proofing time for the oven and the crumb.",
            "THOUGHT", "Proofing clock", ["baking"],
        )
        self.notes = self.vault.create_thought(
            "I keep a bread timer and dough notes beside the oven every morning.",
            "THOUGHT", "Kitchen notes", ["baking"],
        )
        self.vault.update_thought(self.always["id"], current_text=(
            "The enemy should always remember the last fight so the strategy adapts. "
            "Later I doubted whether remembering should be automatic."
        ))
        self.vault.rebuild_recall_indexes(full=True)
        self.engine = ReasoningEngine(self.vault, provider)

    def cleanup(self) -> None:
        self.temp_dir.cleanup()


def _canonical_state(vault: Vault) -> tuple[dict[str, str], dict[str, Any]]:
    originals = {
        item["id"]: hashlib.sha256(
            (item["originalText"] + "|" + item["currentText"]).encode("utf-8")
        ).hexdigest()
        for item in vault.list_thoughts(status="ALL")
    }
    return originals, vault.schema_versions()


class Phase8AcceptanceTestCase(unittest.TestCase):
    """The deterministic baseline, providers and preservation guarantees."""

    def setUp(self) -> None:
        self.f = AcceptanceFixture()
        self.f.build()

    def tearDown(self) -> None:
        self.f.cleanup()

    def test_01_semantic_retrieval_finds_different_wording(self) -> None:
        """A 'clock' query reaches a 'timer' memory with zero lexical overlap."""
        before = self.f.vault.recall("clock schedule")
        self.assertFalse(before.semantic["enabled"])
        self.f.vault.ai_provider = MockProvider()
        index = EmbeddingIndex(self.f.vault)
        self.assertGreater(index.build(MockProvider())["built"], 0)
        outcome = self.f.vault.recall("clock schedule")
        self.assertTrue(outcome.semantic["enabled"])
        ids = [entry["thoughtId"] for entry in outcome.semantic["results"]]
        self.assertIn(self.f.timer["id"], ids)
        # Every hit references a real, still-present original entry.
        for entry in outcome.semantic["results"]:
            self.assertIsNotNone(self.f.vault.get_thought(entry["thoughtId"]))

    def test_02_similarity_ranking_is_ordered_and_cited(self) -> None:
        result = self.f.engine.run("similar", thought_id=self.f.clock["id"], limit=5)
        scores = [item["score"] for item in result["findings"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertNotIn(self.f.clock["id"], [item["thoughtId"] for item in result["findings"]])
        for item in result["findings"]:
            self.assertEqual(item["citation"]["thoughtId"], item["thoughtId"])

    def test_03_connection_discovery_proposes_without_writing(self) -> None:
        before, _ = _canonical_state(self.f.vault)
        result = self.f.engine.run("connections", thought_id=self.f.timer["id"], limit=6)
        self.assertGreater(result["examinedCount"], 0)
        for proposal in result["findings"]:
            self.assertTrue(proposal["requiresApproval"])
            self.assertEqual(proposal["status"], "SUGGESTED")
        with self.f.vault._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0], 0)
        self.assertEqual(_canonical_state(self.f.vault)[0], before)

    def test_04_cross_memory_reasoning_cites_originals(self) -> None:
        themes = self.f.engine.run("themes", limit=6)
        self.assertTrue(themes["findings"])
        valid = {item["id"] for item in self.f.vault.list_thoughts(status="ALL")}
        for theme in themes["findings"]:
            self.assertGreaterEqual(len(theme["thoughtIds"]), 2)
            self.assertTrue(theme["citations"])
            self.assertTrue(set(theme["thoughtIds"]) <= valid)
        explain = self.f.engine.run("explain", thought_ids=[self.f.always["id"], self.f.never["id"]],
                                    use_provider=False)
        self.assertEqual(set(explain["citedThoughtIds"]), {self.f.always["id"], self.f.never["id"]})

    def test_05_contradiction_detection_cites_both_originals(self) -> None:
        result = self.f.engine.run("contradictions", limit=8)
        pairs = [set(item["thoughtIds"]) for item in result["findings"]]
        self.assertIn({self.f.always["id"], self.f.never["id"]}, pairs)
        for item in result["findings"]:
            self.assertIn("possible tension", item["caveat"].lower())
            self.assertEqual(item["left"]["thoughtId"], item["thoughtIds"][0])
            self.assertEqual(item["right"]["thoughtId"], item["thoughtIds"][1])

    def test_06_historical_retrieval_keeps_chronological_order(self) -> None:
        result = self.f.engine.run("evolution", query="enemy strategy", limit=6)
        kinds = {item["kind"] for item in result["findings"]}
        self.assertTrue({"ORIGINAL_CAPTURE", "REVISION", "EVENT"} <= kinds)
        originals = [item for item in result["findings"] if item["kind"] == "ORIGINAL_CAPTURE"]
        self.assertEqual({item["authority"] for item in originals}, {"CANONICAL_ORIGINAL"})
        timestamps = [item["timestamp"] for item in result["findings"] if item["timestamp"]]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_07_provider_switching_restores_the_baseline(self) -> None:
        args = {"thought_ids": [self.f.always["id"]]}
        baseline = self.f.engine.run("explain", use_provider=False, **args)
        enriched = ReasoningEngine(self.f.vault, MockProvider()).run("explain", use_provider=True, **args)
        self.assertEqual(baseline["findings"], enriched["findings"])
        self.assertIn("providerView", enriched)
        self.f.vault.ai_provider = NoAIProvider()
        restored = ReasoningEngine(self.f.vault, self.f.vault.ai_provider).run(
            "explain", use_provider=True, **args)
        self.assertNotIn("providerView", restored)
        self.assertIn("Local deterministic", restored["provider"])

    def test_08_missing_credentials_are_not_an_error(self) -> None:
        self.assertFalse(DEFAULT_CONFIG.ai_required)
        self.assertFalse(DEFAULT_CONFIG.network_required)
        self.assertFalse(DEFAULT_CONFIG.embeddings_enabled)
        engine = ReasoningEngine(self.f.vault, None)
        for mode in MODES:
            with self.subTest(mode=mode):
                result = engine.run(mode, thought_id=self.f.timer["id"], query="bread")
                self.assertIn("findings", result)
                self.assertFalse(result["canonical"])
                self.assertNotIn("providerView", result)

    def test_09_invalid_provider_responses_are_filtered_or_degrade(self) -> None:
        candidates = self.f.vault.link_candidates(self.f.timer["id"], limit=8)
        invalid = ReasoningEngine(self.f.vault, _InvalidProvider()).run(
            "connections", thought_id=self.f.timer["id"], limit=8)
        valid_ids = {item["id"] for item in self.f.vault.list_thoughts(status="ALL")}
        for proposal in invalid["findings"]:
            self.assertIn(proposal["targetId"], valid_ids)
            self.assertNotEqual(proposal["sourceId"], proposal["targetId"])
        self.assertFalse(any(item["targetId"] == "invented-memory" for item in invalid["findings"]))
        broken = ReasoningEngine(self.f.vault, _BrokenProvider()).run(
            "explain", thought_ids=[self.f.always["id"]], use_provider=True)
        self.assertTrue(broken["findings"])
        self.assertTrue(broken["warnings"])

    def test_10_original_memories_survive_every_mode_and_erasure(self) -> None:
        before, versions = _canonical_state(self.f.vault)
        self.assertEqual(versions["canonicalSchemaVersion"], CANONICAL_SCHEMA_VERSION)
        for mode in MODES:
            ReasoningEngine(self.f.vault, MockProvider()).run(
                mode, thought_id=self.f.timer["id"], query="bread", use_provider=True)
        self.f.vault.ai_provider = MockProvider()
        EmbeddingIndex(self.f.vault).build(MockProvider())
        self.f.vault.delete_all_derived_intelligence()
        self.assertEqual(_canonical_state(self.f.vault)[0], before)
        self.assertEqual(_canonical_state(self.f.vault)[1]["canonicalSchemaVersion"],
                         CANONICAL_SCHEMA_VERSION)

    def test_11_provider_urls_are_permission_checked_before_a_request(self) -> None:
        """A hosted, non-loopback HTTP endpoint is refused before any network call."""
        with self.assertRaises(ValueError):
            OpenAICompatibleProvider("http://example.com/v1", "model")
        with self.assertRaises(ValueError):
            OpenAICompatibleProvider("http://192.168.1.10:11434/v1", "model")
        # Local endpoints, including the preferred Ollama preset, are allowed.
        for url in ("http://127.0.0.1:11434/v1", "http://localhost:11434/v1"):
            with self.subTest(url=url):
                self.assertIsNotNone(OpenAICompatibleProvider(url, "llama3.2"))


class Phase8HttpAcceptanceTestCase(unittest.TestCase):
    """Approval workflow and permission boundaries over the real HTTP routes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _SilentHandler)
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        from iwtt.server import IWTTHTTPServer, RequestHandler

        self.f = AcceptanceFixture()
        self.f.build()
        self.server = IWTTHTTPServer(("127.0.0.1", 0), RequestHandler, self.f.vault)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.f.cleanup()

    def _request(self, path: str, payload: dict | None = None, method: str = "POST"):
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

    def test_12_approval_workflow_never_auto_confirms(self) -> None:
        """A proposal is saved unconfirmed and only a person can confirm it."""
        _, result = self._request("/api/reasoning", {
            "mode": "connections", "thoughtId": self.f.timer["id"], "limit": 8,
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
        self.assertEqual(relationship["provenance"], "AI_SUGGESTED")
        confirmed = self.f.vault.confirm_relationship(relationship["id"])
        self.assertEqual(confirmed["status"], "CONFIRMED")
        self.assertEqual(confirmed["confirmedBy"], "USER")

        # The server binds to loopback only and rejects malformed writes.
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self._request("/api/reasoning/save", {
            "mode": "invent", "finding": {"citedThoughtIds": [self.f.always["id"]]}
        })[0], 400)


class _SilentHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass


if __name__ == "__main__":
    unittest.main()
