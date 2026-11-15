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
import re
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


if __name__ == "__main__":
    unittest.main()
