"""Static guards for the dedicated Thinking Partner view.

The reasoning engine has its own tests; these check the browser surface that
turns its six closed modes into seven human questions.  The important contract
is not that a button exists, but that selection, citations, uncertainty and
explicit approval are visible and that no canonical write is automatic.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "web" / "app.js"
INDEX_HTML = ROOT / "web" / "index.html"

TOOLS = {
    "ask": "explain",
    "explore": "similar",
    "connect": "connections",
    "compare": "explain",
    "patterns": "themes",
    "evolution": "evolution",
    "reflect": "contradictions",
}
LABELS = (
    "Ask My Vault", "Explore Ideas", "Find Connections", "Compare Thoughts",
    "Identify Patterns", "Track Thinking Evolution", "Review & Reflect",
)


class ThinkingPartnerViewTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_dedicated_nav_item_exists_and_preserves_the_old_nav(self) -> None:
        self.assertIn('data-view="partner"', self.html)
        self.assertIn("Thinking Partner", self.html)
        for view in ("capture", "vault", "recall", "search", "timeline",
                     "graveyard", "connections", "clusters", "import", "data"):
            with self.subTest(view=view):
                self.assertIn(f'data-view="{view}"', self.html)

    def test_router_and_topbar_render_the_partner_view(self) -> None:
        self.assertIn("case 'partner': markup = await renderPartner();", self.source)
        self.assertIn("partner: 'THINKING PARTNER'", self.source)
        self.assertIn("async function renderPartner()", self.source)

    def test_all_seven_human_questions_are_rendered(self) -> None:
        for label in LABELS:
            with self.subTest(label=label):
                self.assertIn(label, self.source)

    def test_questions_map_only_to_the_closed_reasoning_modes(self) -> None:
        mapping = dict(re.findall(r"key: '([a-z]+)', mode: '([a-z]+)'", self.source))
        for key, mode in TOOLS.items():
            with self.subTest(key=key):
                self.assertEqual(mapping.get(key), mode)
        self.assertEqual(set(mapping), set(TOOLS))
        self.assertTrue(set(mapping.values()) <= {
            "similar", "connections", "themes", "contradictions", "evolution", "explain",
        })

    def test_suggested_prompts_are_clickable_and_wired(self) -> None:
        self.assertIn('data-action="partner-prompt"', self.source)
        self.assertIn("action === 'partner-prompt'", self.source)
        self.assertIn("form.id === 'partner-form'", self.source)
        self.assertIn("'/api/reasoning', { method: 'POST', body: payload }", self.source)

    def test_memory_selection_is_explicit_and_bounded(self) -> None:
        self.assertIn('data-action="partner-select"', self.source)
        self.assertIn("action === 'partner-select'", self.source)
        self.assertIn("MAX 20", self.source)
        self.assertIn("selected.length >= 20", self.source)
        self.assertIn('data-action="partner-clear-selection"', self.source)

    def test_every_finding_has_a_clickable_original_memory_reference(self) -> None:
        start = self.source.index("function partnerRefs(item)")
        block = self.source[start:self.source.index("function partnerFinding(", start)]
        self.assertIn('data-open-thought="', block)
        self.assertIn("item.citation", block)
        self.assertIn("item.evidence", block)
        self.assertIn("item.thoughtIds", block)

    def test_uncertainty_is_visible_on_findings_and_provider_views(self) -> None:
        self.assertIn("partner-uncertainty", self.source)
        self.assertIn("A derived clue from your captures, not a claim about your intent.", self.source)
        self.assertIn("not a verdict about which is right.", self.source)
        self.assertIn("PROVIDER VIEW · PROPOSAL ONLY", self.source)
        self.assertIn("No grounded finding", self.source)

    def test_follow_ups_keep_the_selected_context(self) -> None:
        self.assertIn("partner-followup", self.source)
        self.assertIn("keeps your selected memories", self.source)
        self.assertIn("state.partner.selectedIds.length", self.source)
        self.assertIn("data-prompt=", self.source)

    def test_the_view_works_with_no_provider(self) -> None:
        self.assertIn("'/api/reasoning'", self.source)
        self.assertIn("status?.providerAvailable", self.source)
        self.assertIn("No provider configured. The deterministic local baseline is active", self.source)

    def test_saving_is_explicit_and_never_auto_confirms(self) -> None:
        save_finding = self.source[self.source.index("async function savePartnerFinding()"):]
        save_finding = save_finding[:save_finding.index("async function savePartnerConnection(")]
        self.assertIn("'/api/reasoning/save'", save_finding)
        self.assertNotIn("/confirm", save_finding)

        save_connection = self.source[self.source.index("async function savePartnerConnection("):]
        save_connection = save_connection[:save_connection.index("/* ======================= v0.2")]
        self.assertIn("'/api/relationships'", save_connection)
        self.assertIn("provenance: 'AI_SUGGESTED'", save_connection)
        self.assertNotIn("/confirm", save_connection)
        self.assertIn("unconfirmed", save_connection)

        run = self.source[self.source.index("async function runPartner()"):]
        run = run[:run.index("async function savePartnerFinding()")]
        self.assertNotIn("/api/relationships", run)
        self.assertNotIn("/api/thoughts", run)


if __name__ == "__main__":
    unittest.main()
