"""Tests for the AI link-detection surface.

These cover the whole explicit-AI path:

* candidate selection is structurally diverse, not lexical-only;
* the provider boundary is validated and honest;
* an AI proposal never becomes canonical truth;
* the analysis is persisted as a DERIVED artifact and is erasable;
* every endpoint refuses to run without a provider.

No test contacts a network.  The provider is a local fake that returns
canned verdicts, which is the only way to test an optional hosted feature
deterministically and offline.
"""
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from iwtt.ai import (
    AIConfigurationError,
    AIUnavailable,
    NoAIProvider,
    OpenAICompatibleProvider,
    validate_endpoint,
)
from iwtt.recall.candidates import (
    MAX_CANDIDATES,
    SOURCE_COMMUNITY,
    SOURCE_GRAPH,
    SOURCE_LEXICAL,
    SOURCE_LIFECYCLE,
    SOURCE_TAG,
    SOURCE_VAULT_CONTEXT,
)
from iwtt.store import Vault
from sqlite_helpers import connect_sqlite


class FakeProvider(NoAIProvider):
    """A provider that answers from a canned table instead of a network."""

    name = "FakeProvider"
    available = True
    source = "runtime"

    def __init__(self, verdicts=None, comparison=None):
        self.verdicts = verdicts or []
        self.comparison = comparison or {"linked": False, "type": "", "reason": "no link"}
        self.calls = []

    def assess_links(self, thought, candidates):
        self.calls.append(("assess", thought["id"], [item["id"] for item in candidates]))
        return [dict(verdict) for verdict in self.verdicts if verdict["targetId"] in
                {item["id"] for item in candidates}]

    def compare_thoughts(self, first, second):
        self.calls.append(("compare", first["id"], second["id"]))
        return dict(self.comparison)


class LinkCandidateSelectionTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self.temp_dir.name) / "vault.sqlite3", ai_provider=NoAIProvider())
        self.vault.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _vault_with_related_thoughts(self):
        """A small vault whose thoughts share words, tags, lifecycle and links."""
        enemy = self.vault.create_thought(
            "Enemies remember how the player fought them.", "THOUGHT", "Enemy memory", ["combat"]
        )
        difficulty = self.vault.create_thought(
            "Difficulty should ramp when the player improves.", "EXPERIMENT", "Adaptive difficulty", ["combat"]
        )
        # Deliberately shares NO vocabulary with the enemy thought.
        kitchen = self.vault.create_thought(
            "A bread proofing timer that senses ambient warmth.", "PROJECT", "Sourdough scheduler", ["baking"]
        )
        # A sibling in the same lifecycle state, so the lifecycle source can fire.
        sibling = self.vault.create_thought(
            "A quiet place to work without interruptions.", "THOUGHT", "Focus room", ["space"]
        )
        self.vault.create_relationship(enemy["id"], difficulty["id"], "INSPIRED_BY", provenance="USER_CONFIRMED")
        self.vault.rebuild_recall_indexes(full=True)
        return enemy, difficulty, kitchen, sibling

    def test_the_candidate_pool_is_not_lexical_only(self):
        """The whole point: a thought with no shared words must still be reachable."""
        enemy, _difficulty, kitchen, _sibling = self._vault_with_related_thoughts()
        candidates = self.vault.link_candidates(enemy["id"], limit=16)
        candidate_ids = {item["id"] for item in candidates}
        self.assertIn(
            kitchen["id"], candidate_ids,
            "a semantically related thought with no shared vocabulary must be offered",
        )

    def test_candidates_come_from_more_than_one_structural_source(self):
        enemy, _difficulty, _kitchen, _sibling = self._vault_with_related_thoughts()
        candidates = self.vault.link_candidates(enemy["id"], limit=16)
        sources = {item["candidateSource"] for item in candidates}
        self.assertGreaterEqual(
            len(sources), 2,
            f"the pool must be structurally diverse, got only {sorted(sources)}",
        )
        self.assertTrue(sources <= {
            SOURCE_LEXICAL, SOURCE_GRAPH, SOURCE_COMMUNITY, SOURCE_TAG,
            SOURCE_LIFECYCLE, SOURCE_VAULT_CONTEXT,
        })

    def test_every_candidate_explains_why_it_was_offered(self):
        enemy, _difficulty, _kitchen, _sibling = self._vault_with_related_thoughts()
        for candidate in self.vault.link_candidates(enemy["id"], limit=16):
            self.assertTrue(candidate.get("candidateReason"))
            self.assertTrue(candidate.get("candidateSource"))

    def test_already_connected_thoughts_are_not_re_offered(self):
        enemy, difficulty, _kitchen, _sibling = self._vault_with_related_thoughts()
        candidate_ids = {item["id"] for item in self.vault.link_candidates(enemy["id"], limit=16)}
        self.assertNotIn(difficulty["id"], candidate_ids)

    def test_the_pool_is_bounded(self):
        enemy, _difficulty, _kitchen, _sibling = self._vault_with_related_thoughts()
        for index in range(30):
            self.vault.create_thought(f"Unrelated musing number {index} about pottery glaze.")
        candidates = self.vault.link_candidates(enemy["id"], limit=999)
        self.assertLessEqual(len(candidates), MAX_CANDIDATES)

    def test_the_sweep_prefers_the_least_connected_thoughts(self):
        _enemy, _difficulty, kitchen, _sibling = self._vault_with_related_thoughts()
        sweep = self.vault.link_sweep(limit=5, per_thought=4)
        self.assertTrue(sweep)
        self.assertLessEqual(len(sweep), 5)
        for entry in sweep:
            self.assertTrue(entry["candidates"])
            self.assertLessEqual(len(entry["candidates"]), 4)
        source_ids = {entry["sourceId"] for entry in sweep}
        self.assertIn(kitchen["id"], source_ids)


class ProviderBoundaryTestCase(unittest.TestCase):
    def test_no_ai_remains_the_default(self):
        provider = NoAIProvider()
        self.assertFalse(provider.available)
        self.assertEqual(provider.source, "disabled")
        with self.assertRaises(AIUnavailable):
            provider.assess_links({"id": "x"}, [])
        with self.assertRaises(AIUnavailable):
            provider.compare_thoughts({"id": "x"}, {"id": "y"})

    def test_hosted_endpoints_require_https(self):
        with self.assertRaises(AIConfigurationError):
            validate_endpoint("http://api.example.com/v1")
        self.assertEqual(validate_endpoint("https://api.example.com/v1"), "https://api.example.com/v1")

    def test_loopback_http_is_allowed(self):
        for url in ("http://127.0.0.1:1234/v1", "http://localhost:1234/v1"):
            self.assertEqual(validate_endpoint(url), url)

    def test_endpoint_urls_may_not_carry_credentials_or_queries(self):
        for url in (
            "https://user:pass@api.example.com/v1",
            "https://api.example.com/v1?key=secret",
            "https://api.example.com/v1#frag",
        ):
            with self.assertRaises(AIConfigurationError):
                validate_endpoint(url)

    def test_the_environment_provider_rejects_a_bad_endpoint_instead_of_raising(self):
        import os

        saved = os.environ.get("IWTT_AI_BASE_URL")
        try:
            os.environ["IWTT_AI_BASE_URL"] = "http://api.example.com/v1"
            from iwtt.ai import provider_from_environment

            provider = provider_from_environment()
            self.assertIsInstance(provider, NoAIProvider)
            self.assertFalse(provider.available)
            self.assertEqual(provider.source, "disabled")
        finally:
            if saved is None:
                os.environ.pop("IWTT_AI_BASE_URL", None)
            else:
                os.environ["IWTT_AI_BASE_URL"] = saved


class VerdictParsingTestCase(unittest.TestCase):
    """The provider output is untrusted input and is re-validated."""

    def _provider(self):
        return OpenAICompatibleProvider("http://127.0.0.1:9/v1", "test-model", source="runtime")

    def test_a_verdict_for_an_unsupplied_id_is_dropped(self):
        provider = self._provider()
        raw = json.dumps([{"targetId": "not-offered", "linked": True, "type": "RELATED_TO", "reason": "because"}])
        verdicts = provider._verdicts(raw, {"offered"}, max_items=16)
        self.assertEqual(verdicts, [])

    def test_an_unknown_relationship_type_falls_back_to_related_to(self):
        provider = self._provider()
        raw = json.dumps([{"targetId": "offered", "linked": True, "type": "MADE_UP", "reason": "some reason here"}])
        verdicts = provider._verdicts(raw, {"offered"}, max_items=16)
        self.assertEqual(verdicts[0]["type"], "RELATED_TO")

    def test_a_link_without_a_reason_is_dropped(self):
        provider = self._provider()
        raw = json.dumps([{"targetId": "offered", "linked": True, "type": "RELATED_TO", "reason": "x"}])
        self.assertEqual(provider._verdicts(raw, {"offered"}, max_items=16), [])

    def test_a_negative_verdict_keeps_its_explanation(self):
        provider = self._provider()
        raw = json.dumps([{"targetId": "offered", "linked": False, "type": "", "reason": "unrelated subjects"}])
        verdicts = provider._verdicts(raw, {"offered"}, max_items=16)
        self.assertFalse(verdicts[0]["linked"])
        self.assertEqual(verdicts[0]["type"], "")
        self.assertEqual(verdicts[0]["reason"], "unrelated subjects")

    def test_a_duplicate_target_is_kept_only_once(self):
        provider = self._provider()
        raw = json.dumps([
            {"targetId": "offered", "linked": True, "type": "RELATED_TO", "reason": "first reason here"},
            {"targetId": "offered", "linked": True, "type": "LED_TO", "reason": "second reason here"},
        ])
        verdicts = provider._verdicts(raw, {"offered"}, max_items=16)
        self.assertEqual(len(verdicts), 1)


class LinkAnalysisArtifactTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self.temp_dir.name) / "vault.sqlite3", ai_provider=NoAIProvider())
        self.vault.initialize()
        self.a = self.vault.create_thought("Enemies should remember the last fight.", "THOUGHT", "Enemy memory")
        self.b = self.vault.create_thought("A timer that senses dough temperature.", "PROJECT", "Proofing timer")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_the_analysis_is_stored_as_a_derived_artifact_not_as_a_relationship(self):
        artifact = self.vault.save_derived_artifact(
            self.a["id"], "AI_LINK_ANALYSIS",
            {"examinedCount": 2, "linkedCount": 1, "suggestions": []},
            "FakeProvider", "test-model", [self.a["id"], self.b["id"]],
        )
        self.assertEqual(artifact["artifactType"], "AI_LINK_ANALYSIS")
        self.assertEqual(artifact["sourceThoughtIds"], [self.a["id"], self.b["id"]])
        with connect_sqlite(str(self.vault.db_path)) as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM relationships WHERE source_id IN (?,?) OR target_id IN (?,?)",
                (self.a["id"], self.b["id"], self.a["id"], self.b["id"]),
            ).fetchone()[0]
            self.assertEqual(rows, 0, "an analysis must not create a relationship")

    def test_the_analysis_writes_a_canonical_event_that_records_the_action(self):
        self.vault.save_derived_artifact(
            self.a["id"], "AI_LINK_ANALYSIS", {"linkedCount": 0},
            "FakeProvider", "test-model", [self.a["id"]],
        )
        events = self.vault.get_thought(self.a["id"])["events"]
        types = [event["eventType"] for event in events]
        self.assertIn("AI_LINK_ANALYSIS_GENERATED", types)

    def test_the_analysis_is_erasable_with_the_rest_of_the_derived_data(self):
        self.vault.save_derived_artifact(
            self.a["id"], "AI_LINK_ANALYSIS", {"linkedCount": 0},
            "FakeProvider", "test-model", [self.a["id"]],
        )
        self.vault.clear_derived_data()
        self.assertEqual(self.vault.get_thought(self.a["id"])["derivedArtifacts"], [])
        self.assertEqual(self.vault.get_thought(self.a["id"])["originalText"],
                         "Enemies should remember the last fight.")

    def test_a_saved_ai_proposal_stays_suggested_until_a_human_confirms(self):
        relationship = self.vault.create_relationship(
            self.a["id"], self.b["id"], "RELATED_TO", provenance="AI_SUGGESTED",
            metadata={"note": "the provider suggested this"},
        )
        self.assertEqual(relationship["provenance"], "AI_SUGGESTED")
        self.assertEqual(relationship["status"], "SUGGESTED")
        self.assertIsNone(relationship["confirmedBy"])
        confirmed = self.vault.confirm_relationship(relationship["id"])
        self.assertEqual(confirmed["status"], "CONFIRMED")
        self.assertEqual(confirmed["provenance"], "AI_SUGGESTED")
        self.assertEqual(confirmed["confirmedBy"], "USER")


class HttpSurfaceTestCase(unittest.TestCase):
    """Exercises the new endpoints over real HTTP against a local fake provider."""

    def setUp(self):
        import threading

        from iwtt.server import IWTTHTTPServer, RequestHandler

        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self.temp_dir.name) / "vault.sqlite3", ai_provider=FakeProvider())
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

    def test_the_link_check_endpoint_returns_verdicts_and_persists_an_artifact(self):
        self.vault.ai_provider.verdicts = [{
            "targetId": self.b["id"], "linked": True, "type": "RELATED_TO",
            "reason": "both are about tracking a hidden state over time",
        }]
        status, payload = self._post(f"/api/thoughts/{self.a['id']}/ai/relationships")
        self.assertEqual(status, 200)
        self.assertEqual(payload["examinedCount"], 1)
        self.assertEqual(len(payload["suggestions"]), 1)
        self.assertEqual(payload["suggestions"][0]["targetId"], self.b["id"])
        self.assertEqual(payload["suggestions"][0]["type"], "RELATED_TO")
        self.assertIn("artifact", payload)
        artifacts = self.vault.get_thought(self.a["id"])["derivedArtifacts"]
        self.assertEqual(artifacts[0]["artifactType"], "AI_LINK_ANALYSIS")

    def test_the_link_check_endpoint_reports_when_nothing_is_linked(self):
        self.vault.ai_provider.verdicts = [{
            "targetId": self.b["id"], "linked": False, "type": "",
            "reason": "the two thoughts have nothing in common",
        }]
        status, payload = self._post(f"/api/thoughts/{self.a['id']}/ai/relationships")
        self.assertEqual(status, 200)
        self.assertEqual(payload["suggestions"], [])
        self.assertEqual(payload["notLinkedCount"], 1)
        self.assertIn("not proof", payload["note"])

    def test_the_pairwise_endpoint_judges_exactly_two_thoughts(self):
        self.vault.ai_provider.comparison = {
            "linked": True, "type": "INSPIRED_BY", "reason": "the timer idea informed the enemy design",
        }
        status, payload = self._post("/api/ai/link-check/pair", {
            "sourceId": self.a["id"], "targetId": self.b["id"],
        })
        self.assertEqual(status, 200)
        verdict = payload["verdict"]
        self.assertTrue(verdict["linked"])
        self.assertEqual(verdict["type"], "INSPIRED_BY")
        self.assertEqual(verdict["provenance"], "AI_SUGGESTED")
        self.assertEqual(verdict["status"], "SUGGESTED")

    def test_the_pairwise_endpoint_refuses_a_self_comparison(self):
        status, payload = self._post("/api/ai/link-check/pair", {
            "sourceId": self.a["id"], "targetId": self.a["id"],
        })
        self.assertEqual(status, 400)
        self.assertIn("itself", payload["error"])

    def test_the_pairwise_endpoint_never_creates_a_relationship(self):
        self.vault.ai_provider.comparison = {
            "linked": True, "type": "RELATED_TO", "reason": "a defensible shared idea",
        }
        self._post("/api/ai/link-check/pair", {
            "sourceId": self.a["id"], "targetId": self.b["id"],
        })
        self.assertEqual(self.vault.list_relationships("LIVE"), [])

    def test_the_sweep_endpoint_is_bounded_and_reports_its_cost(self):
        status, payload = self._post("/api/ai/link-check/sweep", {"limit": 3, "perThought": 3})
        self.assertEqual(status, 200)
        self.assertLessEqual(payload["sourceCount"], 3)
        self.assertIn("provider request", payload["note"])

    def test_every_ai_endpoint_refuses_to_run_without_a_provider(self):
        self.vault.ai_provider = NoAIProvider()
        for path, payload in (
            (f"/api/thoughts/{self.a['id']}/ai/relationships", {}),
            ("/api/ai/link-check/pair", {"sourceId": self.a["id"], "targetId": self.b["id"]}),
            ("/api/ai/link-check/sweep", {}),
        ):
            with self.subTest(path=path):
                status, body = self._post(path, payload)
                self.assertEqual(status, 503)
                self.assertIn("provider", body["error"].lower())

    def test_the_ai_status_route_reports_the_configured_source(self):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/ai/status")
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["available"])
        self.assertEqual(payload["source"], "runtime")


if __name__ == "__main__":
    unittest.main()
