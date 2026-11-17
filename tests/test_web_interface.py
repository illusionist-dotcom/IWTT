"""Static guards on the browser interface.

These tests exist because a dead button is invisible in every other gate: the
server has no idea a control was rendered, the unit tests never touch the
DOM, and the release verifiers exercise HTTP routes rather than click
handlers.  Two such buttons shipped in this project before these guards
existed — ``ai-find-connections`` and ``save-ai-relationship`` were rendered
into the page but had no handler, so the feature they belonged to could not
be reached by clicking it.

The checks are static on purpose: they parse the shipped source rather than
driving a browser, so they run offline, deterministically, and in a second.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_JS = REPO_ROOT / "web" / "app.js"
INDEX_HTML = REPO_ROOT / "web" / "index.html"


class WebInterfaceWiringTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")

    def test_every_rendered_action_has_a_handler(self):
        """A `data-action` in the markup must be handled somewhere.

        Handlers may be dispatched either by comparing ``action === '...'`` or
        by matching the attribute directly, so both forms are accepted.
        """
        rendered = set(re.findall(r'data-action="([a-z0-9-]+)"', self.source))
        compared = set(re.findall(r"action === '([a-z0-9-]+)'", self.source))
        matched = set(re.findall(r"data-action=\\?\"([a-z0-9-]+)\\?\"\]", self.source))
        handled = compared | matched
        unhandled = sorted(rendered - handled)
        self.assertEqual(
            unhandled, [],
            f"these controls are rendered but do nothing: {unhandled}",
        )

    def test_every_rendered_form_has_a_submit_handler(self):
        """A form in the markup must be handled on submit."""
        rendered = set(re.findall(r'id="([a-z0-9-]+)"[^>]*class="[^"]*\bform\b', self.source))
        handled = set(re.findall(r"form\.id === '([a-z0-9-]+)'", self.source))
        self.assertEqual(
            sorted(rendered - handled), [],
            "these forms are rendered but never submitted",
        )

    def test_the_ai_link_actions_are_wired(self):
        """The AI link-review controls must reach the server.

        This is the specific regression that shipped twice: the panels were
        rendered, the buttons existed, and nothing happened when they were
        pressed.
        """
        for action in ("ai-find-connections", "save-ai-relationship", "save-sweep-relationship"):
            with self.subTest(action=action):
                self.assertIn(f"action === '{action}'", self.source)

    def test_the_link_review_endpoints_are_called_from_the_interface(self):
        for route in ("/api/ai/link-check/pair", "/api/ai/link-check/sweep"):
            with self.subTest(route=route):
                self.assertIn(route, self.source)

    def test_the_ai_link_forms_are_wired(self):
        for form_id in ("ai-pair-form", "ai-sweep-form"):
            with self.subTest(form_id=form_id):
                self.assertIn(f"form.id === '{form_id}'", self.source)

    def test_the_interface_never_hardcodes_a_remote_host(self):
        """The UI must talk only to this app's own local server.

        A remote host may appear in a ``placeholder`` — that is text the user
        reads and replaces with their own endpoint — but never in a URL the
        interface actually requests.
        """
        without_placeholders = re.sub(r'placeholder="[^"]*"', "", self.source)
        hosts = set(re.findall(r"https?://([a-z0-9.-]+)", without_placeholders))
        self.assertTrue(
            hosts <= {"127.0.0.1", "localhost"},
            f"the interface must not reference remote hosts: {sorted(hosts)}",
        )

    def test_the_shell_declares_every_view_the_router_can_render(self):
        """A nav item that points at a view with no markup is a dead link."""
        html = INDEX_HTML.read_text(encoding="utf-8")
        nav_views = set(re.findall(r'data-view="([a-z0-9-]+)"', html))
        for view in nav_views:
            with self.subTest(view=view):
                self.assertIn(f"'{view}'", self.source)

    def test_every_artifact_type_has_a_rendering(self):
        """A new artifact type must be given a readable rendering.

        The original detail view rendered any unrecognised artifact as
        ``JSON.stringify(payload)``, which is how an ``AI_LINK_ANALYSIS``
        artifact first reached the screen as a raw JSON blob.  Every type the
        server can store must therefore be handled by name.
        """
        from iwtt.store import ARTIFACT_EVENT_TYPES

        for artifact_type in ARTIFACT_EVENT_TYPES:
            with self.subTest(artifactType=artifact_type):
                self.assertIn(
                    f"'{artifact_type}'", self.source,
                    f"{artifact_type} has no explicit rendering in the detail view",
                )

    def test_no_artifact_renders_as_a_raw_json_blob(self):
        """The detail view must never show a raw JSON payload.

        A machine-readable blob in the interface is unreadable to the person
        who wrote the thought, and it can leak internal structure into the
        screen in a form nobody chose.
        """
        renderer = self.source[self.source.index("function renderDerivedArtifact("):]
        renderer = renderer[:renderer.index("\n  const ") if "\n  const " in renderer else len(renderer)]
        self.assertNotIn(
            "JSON.stringify(a.payload)", self.source,
            "the detail view still stringifies an artifact payload directly",
        )
        self.assertNotIn(
            "JSON.stringify(payload))", renderer,
            "an artifact payload is still rendered as a raw JSON string",
        )
        self.assertNotIn(
            "JSON.stringify(value)", renderer,
            "a derived value is still rendered as a raw JSON string",
        )

    def test_the_derived_view_uses_the_shared_artifact_renderer(self):
        """The detail view must not inline its own artifact markup."""
        self.assertIn("renderDerivedArtifact(a, t)", self.source)
        self.assertIn("function renderDerivedArtifact(", self.source)


class ArtifactRenderingTestCase(unittest.TestCase):
    """Executes the artifact renderer against real payloads.

    The renderer is plain JavaScript with no dependencies, so it can be run
    under Node when Node is available and skipped when it is not.  Running it
    proves the output is readable HTML rather than only proving the source
    contains the right strings.
    """

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node is not available")

    def _render(self, artifact: dict) -> str:
        script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const start = src.indexOf('  function renderDerivedArtifact(artifact, thought) {');
const end = src.indexOf('\n  async function renderDetail()');
const block = src.slice(start, end);
const shim = `
  const esc = v => String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  const attr = v => esc(v);
  const date = v => String(v);
  const CANDIDATE_SOURCE_NAMES = { LEXICAL:'shared words', GRAPH:'a stored connection', COMMUNITY:'the same community', TAG:'a shared tag', LIFECYCLE:'the same lifecycle', VAULT_CONTEXT:'vault context' };
`;
const fn = new Function(shim + block + '\nreturn renderDerivedArtifact;')();
const artifact = JSON.parse(process.argv[3]);
process.stdout.write(fn(artifact, { id: 'thought-1' }));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write(script)
            path = handle.name
        try:
            result = subprocess.run(
                [self.node, path, str(APP_JS), json.dumps(artifact)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        finally:
            os.unlink(path)

    def _artifact(self, artifact_type: str, payload: dict) -> dict:
        return {
            "artifactType": artifact_type,
            "provider": "TestProvider",
            "model": "test-model",
            "generatedAt": "2026-01-01T00:00:00Z",
            "sourceThoughtIds": ["a", "b"],
            "payload": payload,
        }

    def test_a_link_analysis_renders_readably(self):
        html = self._render(self._artifact("AI_LINK_ANALYSIS", {
            "examinedCount": 5,
            "linkedCount": 1,
            "notLinkedCount": 4,
            "candidateSources": {"LEXICAL": 2, "VAULT_CONTEXT": 3},
            "suggestions": [{
                "targetId": "x", "targetTitle": "Proofing timer",
                "type": "RELATED_TO", "reason": "shares a hidden state",
            }],
            "boundary": "A screening result, not proof.",
        }))
        self.assertIn("1 possible link among 5 candidates read", html)
        self.assertIn("shared words (2)", html)
        self.assertIn("vault context (3)", html)
        self.assertIn("Proofing timer", html)
        self.assertIn("shares a hidden state", html)
        self.assertIn("not proof", html)

    def test_an_unknown_artifact_type_never_renders_as_raw_json(self):
        html = self._render(self._artifact("AI_FUTURE_UNKNOWN", {
            "alpha": "one", "beta": ["x", "y"], "gamma": {"k": "v"},
        }))
        self.assertNotIn('{"', html)
        self.assertNotIn('["', html)
        self.assertIn("alpha", html)
        self.assertIn("x, y", html)
        self.assertIn("k: v", html)

    def test_a_summary_renders_its_summary(self):
        html = self._render(self._artifact("AI_SUMMARY", {"summary": "A short summary."}))
        self.assertIn("A short summary.", html)

    def test_an_interpretation_renders_its_columns(self):
        html = self._render(self._artifact("AI_INTERPRETATION", {
            "summary": "An interpretation.",
            "concepts": ["alpha", "beta"],
            "openQuestions": ["why?"],
            "possibleNextSteps": ["try it"],
        }))
        self.assertIn("An interpretation.", html)
        self.assertIn("Concepts", html)
        self.assertIn("Open questions", html)
        self.assertIn("Possible next steps", html)
        self.assertIn("Use as an editable idea draft", html)


if __name__ == "__main__":
    unittest.main()
