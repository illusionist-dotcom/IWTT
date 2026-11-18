"""Tests for the provider layer: mock provider, Ollama preset, model listing.

The mock provider exists so the whole AI pipeline can be exercised
deterministically and offline.  The Ollama preset exists so the zero-cost
local path needs no documentation archaeology.  Model listing lets the UI
fill a dropdown from the configured endpoint without sending any thought
content anywhere.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from iwtt.ai import (
    AIConfigurationError,
    AIUnavailable,
    MockProvider,
    NoAIProvider,
    OLLAMA_BASE_URL,
    OpenAICompatibleProvider,
    provider_from_environment,
)
from iwtt.store import Vault


class _FakeEndpointHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible endpoint for the model-listing tests."""

    models_payload = {"data": [{"id": "zeta"}, {"id": "alpha"}, {"id": "zeta"}]}
    chat_payload = {"choices": [{"message": {"content": "ok"}}]}

    def do_GET(self):
        if self.path.endswith("/models"):
            body = json.dumps(self.models_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps(self.chat_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _thought(text: str, title: str = "") -> dict:
    return {
        "id": "thought-1",
        "title": title,
        "displayTitle": title,
        "currentText": text,
        "originalText": text,
        "sourceType": "THOUGHT",
        "type": "THOUGHT",
    }


class MockProviderTestCase(unittest.TestCase):
    """The mock is deterministic, offline, and shaped like a real provider."""

    def setUp(self):
        self.provider = MockProvider()

    def test_the_mock_is_available_but_never_touches_a_network(self):
        self.assertTrue(self.provider.available)
        self.assertEqual(self.provider.source, "mock")
        self.assertIn("Mock", self.provider.name)

    def test_the_same_input_produces_the_same_output_every_time(self):
        thought = _thought("A timer that senses dough temperature before baking.", "Proofing timer")
        self.assertEqual(self.provider.summarize(thought), self.provider.summarize(thought))
        self.assertEqual(self.provider.interpret(thought), self.provider.interpret(thought))
        self.assertEqual(self.provider.suggest_tags(thought), self.provider.suggest_tags(thought))

    def test_interpret_reports_its_fields_and_bounds(self):
        result = self.provider.interpret(_thought("Scaling the proofing window by humidity."))
        self.assertIsInstance(result["summary"], str) and self.assertTrue(result["summary"])
        self.assertTrue(all(isinstance(item, str) for item in result["concepts"]))
        self.assertFalse(result["sourceTruncated"])
        self.assertGreater(result["processedCharacters"], 0)

    def test_long_input_is_reported_as_truncated_and_short_output_is_bounded(self):
        thought = _thought("word " * 40_000)
        result = self.provider.interpret(thought)
        self.assertTrue(result["sourceTruncated"])
        self.assertLessEqual(result["processedCharacters"], 60_000)
        self.assertLessEqual(len(self.provider.summarize(thought)), 600)

    def test_the_link_verdict_is_positive_only_with_shared_distinctive_terms(self):
        linked = self.provider.compare_thoughts(
            _thought("The proofing timer needs a humidity sensor.", "Timer"),
            _thought("A humidity sensor drifts over months and needs recalibration.", "Sensor"),
        )
        self.assertTrue(linked["linked"])
        self.assertIn("humidity", linked["reason"])
        self.assertEqual(linked["type"], "RELATED_TO")

    def test_the_link_verdict_reports_a_negative_with_a_reason(self):
        unrelated = self.provider.compare_thoughts(
            _thought("The proofing timer needs a humidity sensor.", "Timer"),
            _thought("Graveyard visits helped me accept the ending.", "Ending"),
        )
        self.assertFalse(unrelated["linked"])
        self.assertEqual(unrelated["type"], "")
        self.assertTrue(unrelated["reason"])

    def test_assess_links_returns_one_verdict_per_candidate(self):
        candidates = [
            _thought("A humidity sensor drifts over months.", "Sensor"),
            _thought("Graveyard visits helped me accept the ending.", "Ending"),
        ]
        verdicts = self.provider.assess_links(
            _thought("The proofing timer needs a humidity sensor.", "Timer"), candidates
        )
        self.assertEqual([verdict["targetId"] for verdict in verdicts], ["thought-1", "thought-1"])
        self.assertEqual([verdict["linked"] for verdict in verdicts], [True, False])
        self.assertEqual(verdicts[1]["targetId"], candidates[1]["id"])

    def test_the_mock_never_raises_when_fields_are_missing(self):
        self.assertTrue(self.provider.summarize({}))
        self.assertEqual(self.provider.suggest_tags({}), [])


class ProviderSelectionTestCase(unittest.TestCase):
    """Environment selection: no AI stays the default; presets are explicit."""

    def _with_environment(self, **values):
        names = ("IWTT_AI_PROVIDER", "IWTT_AI_BASE_URL", "IWTT_AI_MODEL")
        saved = {name: os.environ.get(name) for name in names}
        try:
            for name in names:
                os.environ.pop(name, None)
            os.environ.update({key: value for key, value in values.items() if value is not None})
            return provider_from_environment()
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_with_nothing_set_there_is_still_no_provider(self):
        provider = self._with_environment()
        self.assertIsInstance(provider, NoAIProvider)
        self.assertFalse(provider.available)

    def test_an_explicit_mock_provider_is_deterministic_and_offline(self):
        provider = self._with_environment(IWTT_AI_PROVIDER="mock")
        self.assertIsInstance(provider, MockProvider)
        self.assertTrue(provider.available)

    def test_the_ollama_preset_points_at_the_local_server(self):
        provider = self._with_environment(IWTT_AI_PROVIDER="ollama", IWTT_AI_MODEL="llama3.1")
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(provider.base_url, OLLAMA_BASE_URL)

    def test_an_ollama_base_url_override_is_respected(self):
        provider = self._with_environment(
            IWTT_AI_PROVIDER="ollama", IWTT_AI_BASE_URL="http://127.0.0.1:9999/v1"
        )
        self.assertEqual(provider.base_url, "http://127.0.0.1:9999/v1")

    def test_a_hosted_endpoint_still_requires_https(self):
        with self.assertRaises(AIConfigurationError):
            OpenAICompatibleProvider("http://api.example.com/v1", "m")


class ModelListingTestCase(unittest.TestCase):
    """list_models speaks the OpenAI-compatible /v1/models surface."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeEndpointHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def _provider(self, base_url=None):
        return OpenAICompatibleProvider(base_url or f"http://127.0.0.1:{self.port}/v1", "any")

    def test_models_are_listed_sorted_and_deduplicated(self):
        self.assertEqual(self._provider().list_models(), ["alpha", "zeta"])

    def test_a_bare_base_url_gains_the_v1_models_suffix(self):
        base = f"http://127.0.0.1:{self.port}"
        self.assertEqual(self._provider(base).list_models(), ["alpha", "zeta"])

    def test_an_unreachable_endpoint_raises_a_readable_runtime_error(self):
        with self.assertRaises(RuntimeError):
            self._provider("http://127.0.0.1:9/v1").list_models()

    def test_a_hosted_non_loopback_http_endpoint_is_rejected_before_any_request(self):
        with self.assertRaises(AIConfigurationError):
            self._provider("http://api.example.com/v1").list_models()

    def test_the_mock_provider_has_no_model_listing(self):
        self.assertFalse(hasattr(MockProvider(), "list_models"))


class ProviderHttpSurfaceTestCase(unittest.TestCase):
    """The configure/models routes over real HTTP, including the mock preset."""

    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _FakeEndpointHandler)
        cls.upstream_port = cls.upstream.server_address[1]
        cls.upstream_thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.upstream_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.upstream_thread.join(timeout=5)

    def setUp(self):
        from iwtt.server import IWTTHTTPServer, RequestHandler

        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self.temp_dir.name) / "vault.sqlite3", ai_provider=NoAIProvider())
        self.vault.initialize()
        self.a = self.vault.create_thought("Enemies should remember the last fight.", "THOUGHT", "Enemy memory")
        self.b = self.vault.create_thought("A timer that senses dough temperature.", "PROJECT", "Proofing timer")
        self.server = IWTTHTTPServer(("127.0.0.1", 0), RequestHandler, self.vault)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp_dir.cleanup()

    def _post(self, path, payload=None):
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_configuring_the_mock_preset_enables_ai_without_a_network(self):
        status, payload = self._post("/api/ai/configure", {"provider": "mock"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["available"])
        self.assertIn("Mock", payload["provider"])
        self.assertEqual(self.vault.ai_provider.source, "mock")

    def test_an_ai_action_works_after_the_mock_is_configured(self):
        self._post("/api/ai/configure", {"provider": "mock"})
        status, payload = self._post("/api/ai/link-check/pair", {
            "sourceId": self.a["id"], "targetId": self.b["id"],
        })
        self.assertEqual(status, 200)
        self.assertIn("verdict", payload)
        from sqlite_helpers import connect_sqlite
        with connect_sqlite(str(self.vault.db_path)) as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM relationships"
            ).fetchone()[0]
        self.assertEqual(rows, 0, "the mock must not create relationships either")

    def test_the_ollama_preset_configures_the_local_default(self):
        status, payload = self._post("/api/ai/configure", {"provider": "ollama", "model": "llama3.1"})
        self.assertEqual(status, 200)
        self.assertEqual(self.vault.ai_provider.base_url, OLLAMA_BASE_URL)

    def test_the_ollama_preset_requires_a_model_choice(self):
        status, _ = self._post("/api/ai/configure", {"provider": "ollama"})
        self.assertEqual(status, 400)

    def test_model_listing_does_not_change_the_configured_provider(self):
        status, payload = self._post("/api/ai/models", {
            "baseUrl": f"http://127.0.0.1:{self.upstream_port}/v1",
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["models"], ["alpha", "zeta"])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(self.vault.ai_provider.source, "disabled")

    def test_model_listing_for_ollama_uses_the_preset_when_no_url_is_given(self):
        # Whether a local Ollama server is installed is environmental.  The
        # contract is: the preset URL is used, a live server answers with
        # models, an absent one degrades to 503 — never a 500, and the
        # configured provider is never changed.
        status, body = self._post("/api/ai/models", {"provider": "ollama"})
        self.assertIn(status, (200, 503))
        if status == 200:
            self.assertGreaterEqual(body["count"], 0)
        self.assertEqual(self.vault.ai_provider.source, "disabled")

    def test_model_listing_with_an_invalid_endpoint_is_a_validation_error(self):
        status, _ = self._post("/api/ai/models", {"baseUrl": "http://api.example.com/v1"})
        self.assertEqual(status, 400)

    def test_disabling_ai_still_works_after_the_mock(self):
        self._post("/api/ai/configure", {"provider": "mock"})
        status, payload = self._post("/api/ai/configure", {"enabled": False})
        self.assertEqual(status, 200)
        self.assertFalse(payload["available"])
        self.assertIsInstance(self.vault.ai_provider, NoAIProvider)


if __name__ == "__main__":
    unittest.main()
