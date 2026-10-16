"""Recall Engine V2 — retrieval behaviour.

Each test names the behaviour it pins, so a failure says which promise broke.
The fixture is deliberately small but shaped so the signals are separable: a
common word appears everywhere (so IDF can be observed), rare words appear once
(so coverage and rarity can be observed), and titles/tags are distinct from body
text (so field weighting can be observed).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iwtt.recall.config import DEFAULT_CONFIG
from iwtt.recall.text import normalize_text, recall_variants, token_variants, word_trigrams
from iwtt.store import Vault


class RecallFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self._tmp.name) / "thought-vault.sqlite3")
        self.vault.initialize()
        self.ids: dict[str, str] = {}

        def add(key: str, text: str, title: str = "", kind: str = "THOUGHT", tags=()):
            thought = self.vault.create_thought(text, kind, title, list(tags))
            self.ids[key] = thought["id"]
            return thought

        # "game" appears in almost everything, so it carries almost no information.
        add("enemy_memory",
            "Enemies should remember how the player fights and adapt after three encounters "
            "in the game.", "Enemy memory and adaptation", tags=["enemy ai", "combat"])
        add("difficulty",
            "A difficulty curve that punishes adaptation feels unfair in any game.",
            "Adaptive difficulty can feel unfair", tags=["difficulty"])
        add("dungeon",
            "Procedural dungeon generation needs a stable seed per floor so rooms repeat.",
            "Seeded procedural dungeons", tags=["procedural"])
        add("saves",
            "Save games should record the seed and the adaptation counters, not just position.",
            "Persistence for learned behaviour", tags=["saves"])
        add("bury",
            "Abandoned idea: turn-based combat prototype where every action costs stamina.",
            "Turn-based stamina combat", tags=["abandoned"])
        add("determinism",
            "Deterministic ranking matters more than clever ranking in a recall system.",
            "Determinism over cleverness", tags=["recall"])
        add("refusal",
            "Refusing to answer is a feature: three equal matches means say three.",
            "Ambiguity refusal as a feature", tags=["recall"])
        add("trigrams",
            "Character trigrams catch typos that stemming cannot, like plyer for player.",
            "Trigrams catch what stemming misses", tags=["recall"])
        add("hubs",
            "Hub suppression: a note connected to everything is connected to nothing.",
            "Hubs are noise, not signal", tags=["graph"])
        add("shopping",
            "Coffee, oats, bananas and the good olive oil.", "Shopping list", tags=["personal"])
        add("project_recall",
            "Project: a local-first recall system with no cloud and no embeddings.",
            "Local-first recall system", kind="PROJECT", tags=["project", "recall"])
        add("experiment_memory",
            "Experiment: give one enemy type a three-encounter memory and measure notice rates.",
            "Three-encounter memory experiment", kind="EXPERIMENT", tags=["experiment", "enemy ai"])

        self.vault.create_relationship(self.ids["enemy_memory"], self.ids["difficulty"], "LED_TO")
        self.vault.create_relationship(self.ids["enemy_memory"], self.ids["saves"], "DEPENDS_ON")
        self.vault.create_relationship(
            self.ids["dungeon"], self.ids["project_recall"], "RELATED_TO", provenance="AI_SUGGESTED"
        )
        self.vault.create_relationship(
            self.ids["enemy_memory"], self.ids["experiment_memory"], "TESTS"
        )
        self.vault.move_to_graveyard(self.ids["bury"])
        self.vault.rebuild_recall_indexes(full=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def recall(self, query: str, **kwargs):
        return self.vault.recall(query, **kwargs)

    def top(self, query: str, **kwargs):
        outcome = self.recall(query, **kwargs)
        self.assertTrue(outcome.results, f"expected at least one result for {query!r}")
        return outcome.results[0]


class LexicalSignalTestCase(RecallFixture):
    def test_exact_phrase_returns_the_original_thought_verbatim(self) -> None:
        result = self.top("Enemies should remember how the player fights")
        self.assertEqual(result.thought_id, self.ids["enemy_memory"])
        self.assertEqual(
            result.original_text,
            "Enemies should remember how the player fights and adapt after three encounters "
            "in the game.",
            "the result must carry the canonical original text, byte for byte",
        )
        self.assertIn("EXACT_PHRASE", [item.code for item in result.explanations])
        self.assertEqual(result.match_reason, "Exact text")

    def test_normalization_ignores_case_punctuation_and_diacritics(self) -> None:
        canonical = self.top("enemy adaptation").thought_id
        for variant in ("ENEMY ADAPTATION", "enemy   adaptation!", "Enemy — Adaptation?", "ÉNEMY ADAPTATION"):
            self.assertEqual(self.top(variant).thought_id, canonical, f"{variant!r} must normalize identically")
        self.assertEqual(normalize_text("Crème BRÛLÉE — cafÄ"), "creme brulee cafa")

    def test_word_forms_are_matched_through_morphology(self) -> None:
        self.assertEqual(self.top("adapting enemies").thought_id, self.ids["enemy_memory"])
        self.assertEqual(self.top("adaptations").thought_id, self.ids["enemy_memory"])
        self.assertIn("adapt", token_variants("adapting"))
        self.assertIn("enemy", token_variants("enemies"), "ies→y must be stemmed before the es/s rules")

    def test_recall_morphology_extends_without_changing_the_v01_rules(self) -> None:
        """v0.1's variant function is frozen — recall may add forms, never alter it.

        ``Vault.search()`` and its pinned tests depend on the exact v0.1 output,
        so the closure lives in ``recall_variants`` and is a strict superset.
        """
        for token in ("adaptations", "enemies", "playing", "remembering", "movement"):
            self.assertTrue(
                token_variants(token) <= recall_variants(token),
                f"recall variants must include every v0.1 variant of {token!r}",
            )
        self.assertIn("adapt", recall_variants("adaptations"), "iterative closure: -tions → -tion → verb")
        self.assertIn("move", recall_variants("movement"))
        self.assertIn("aware", recall_variants("awareness"))
        self.assertNotIn("adapte", recall_variants("adaptation"),
                         "silent-e restoration must not invent non-words")
        self.assertIn("adaptation", recall_variants("adaptation"), "the token itself is always a form")

    def test_typos_are_recovered_by_the_bounded_fuzzy_pass(self) -> None:
        result = self.top("enemys adapt to plyer")
        self.assertEqual(result.thought_id, self.ids["enemy_memory"])
        self.assertGreater(result.signals.fuzzy, 0.0)
        codes = [item.code for item in result.explanations]
        self.assertTrue({"TYPO_TOLERATED", "FUZZY_TEXT", "MORPHOLOGY_MATCH"} & set(codes))

    def test_character_trigrams_find_near_misspellings(self) -> None:
        grams = word_trigrams("player")
        self.assertIn("pla", grams)
        self.assertIn("yer", grams)
        self.assertTrue(all(len(gram) == 3 for gram in grams), "trigrams are exactly 3 characters")
        self.assertTrue(grams & word_trigrams("plyer"), "a typo must still share trigrams")
        result = self.top("determenistic ranking")
        self.assertEqual(result.thought_id, self.ids["determinism"])

    def test_rare_terms_outweigh_common_terms_through_idf(self) -> None:
        # "game" is in most thoughts; "stamina" is in exactly one.  A query holding
        # both must be decided by the rare term.
        result = self.top("game stamina")
        self.assertEqual(result.thought_id, self.ids["bury"])
        idf = dict((term, weight) for term, weight, _tier in result.signals.idf_terms)
        self.assertGreater(idf["stamina"], idf["game"], "the rare term must carry more weight")

    def test_coverage_rewards_matching_more_of_the_query(self) -> None:
        """Same query, two thoughts: the one covering more of it must score higher.

        Comparing a 5-term query against a 1-term query would not isolate
        coverage at all — the exact-phrase bonus alone outweighs it.
        """
        query = "procedural dungeon generation stable seed floor"
        outcome = self.recall(query)
        by_id = {result.thought_id: result for result in outcome.results}
        narrow = by_id[self.ids["dungeon"]]
        self.assertIn(narrow.state, ("STRONG_MATCH", "POSSIBLE_MATCH"))
        self.assertGreaterEqual(narrow.signals.coverage_ratio, 0.6)

        partial = by_id.get(self.ids["saves"])
        self.assertIsNotNone(partial, "'seed' also appears in the persistence thought")
        self.assertGreater(
            len(narrow.signals.coverage_matched), len(partial.signals.coverage_matched)
        )
        self.assertGreater(narrow.signals.coverage, partial.signals.coverage)
        self.assertGreater(narrow.score, partial.score)
        self.assertIn("QUERY_COVERAGE", [item.code for item in narrow.explanations])

    def test_title_and_tag_are_separate_weighted_signals(self) -> None:
        by_title = self.top("Seeded procedural dungeons")
        self.assertEqual(by_title.thought_id, self.ids["dungeon"])
        self.assertGreater(by_title.signals.title, 0.0)

        by_tag = self.top("enemy ai")
        self.assertEqual(by_tag.thought_id, self.ids["enemy_memory"])
        self.assertGreater(by_tag.signals.tag, 0.0)
        self.assertIn("TAG_MATCH", [item.code for item in by_tag.explanations])

    def test_query_with_no_word_in_common_is_refused_not_guessed(self) -> None:
        outcome = self.recall("quantum chromodynamics renormalization")
        self.assertEqual(outcome.state, "NO_MATCH")
        self.assertFalse(outcome.results)
        self.assertIn("no word", outcome.message.lower())
        self.assertIn("no semantic stage", outcome.message.lower())
        suppressed = outcome.honesty.get("suppressedNoLexicalEvidence") or []
        for entry in suppressed:
            self.assertGreater(entry["gatedFuzzy"] + entry["gatedTrigram"], 0.0,
                               "a suppressed candidate must show the similarity that was rejected")

    def test_results_are_capped_and_never_unbounded(self) -> None:
        outcome = self.recall("the", limit=3)
        self.assertLessEqual(len(outcome.results), 3)
        self.assertLessEqual(len(self.recall("game").results), DEFAULT_CONFIG.max_results)


class LifecycleAndTimeTestCase(RecallFixture):
    def test_graveyard_thoughts_are_annotated_not_silently_excluded(self) -> None:
        result = self.top("abandoned turn based combat stamina")
        self.assertEqual(result.thought_id, self.ids["bury"])
        self.assertEqual(result.status, "GRAVEYARD")
        self.assertIn("GRAVEYARD", [item.code for item in result.explanations])
        self.assertTrue(result.graveyard_at)

    def test_excluding_the_graveyard_reports_what_it_hid(self) -> None:
        outcome = self.recall("abandoned turn based combat stamina", include_graveyard=False)
        excluded = outcome.honesty["filteredOut"]
        self.assertEqual(excluded["totalExcluded"], 1)
        self.assertEqual(excluded["byReason"], {"status": 1})
        self.assertIn("lifecycle status", excluded["labels"]["status"])
        self.assertIn("excluded", outcome.message.lower())
        self.assertNotIn(self.ids["bury"], [result.thought_id for result in outcome.results])

    def test_time_window_filter_narrows_by_canonical_timestamp(self) -> None:
        moment = datetime.now(timezone.utc)
        old = self.vault.create_thought(
            "An old note about enemy behaviour written long ago.", "THOUGHT", "Old enemy note",
        )
        with self.vault._transaction() as conn:
            stamp = (moment - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
            conn.execute("UPDATE thoughts SET created_at=?, updated_at=? WHERE id=?",
                         (stamp, stamp, old["id"]))
        self.vault.rebuild_recall_indexes(full=True)

        recent = self.recall("enemy behaviour note", time_expression="last 30 days")
        self.assertNotIn(old["id"], [result.thought_id for result in recent.results])
        self.assertEqual(recent.honesty["filteredOut"]["byReason"].get("time"), 1)

        everything = self.recall("enemy behaviour note")
        self.assertIn(old["id"], [result.thought_id for result in everything.results])

    def test_a_time_hint_in_the_query_never_fabricates_precision(self) -> None:
        outcome = self.recall("what did I write last week about recall")
        hint = outcome.analysis.get("timeHint")
        self.assertTrue(hint, "the phrase 'last week' must be recognised as a range")
        self.assertEqual(hint["source"], "query-hint")
        self.assertIn(hint["label"], ("last week", "week"))
        self.assertTrue(hint["start"] and hint["end"], "a hint is a range, never a single instant")
        self.assertEqual(hint["precision"], "ESTIMATED_RANGE")
        self.assertTrue(hint["estimated"])
        self.assertIn("not a timestamp", hint["note"].lower())

    def test_type_filter_uses_the_canonical_type(self) -> None:
        projects = self.recall("recall system", types=["PROJECT"])
        self.assertTrue(projects.results)
        for result in projects.results:
            self.assertEqual(result.type, "PROJECT")
        self.assertEqual(projects.results[0].thought_id, self.ids["project_recall"])


class ExplanationTestCase(RecallFixture):
    def test_every_result_explains_itself_with_evidence(self) -> None:
        outcome = self.recall("enemys adapt to plyer")
        for result in outcome.results:
            self.assertTrue(result.explanations, "every result must carry at least one reason")
            self.assertTrue(result.why())
            for explanation in result.explanations:
                self.assertTrue(explanation.code)
                self.assertTrue(explanation.label)
                self.assertIsInstance(explanation.evidence, dict)

    def test_explanations_are_structured_codes_not_generated_prose(self) -> None:
        result = self.top("Enemies should remember how the player fights")
        payload = result.to_dict()
        codes = [item["code"] for item in payload["explanations"]]
        self.assertIn("EXACT_PHRASE", codes)
        for item in payload["explanations"]:
            self.assertEqual(sorted(item), ["code", "contribution", "detail", "evidence", "label"])

    def test_scoring_breakdown_exposes_the_actual_arithmetic(self) -> None:
        breakdown = self.vault.recall_diagnostics("enemys adapt to plyer")
        result = breakdown["result"]
        self.assertTrue(result["present"])
        total = sum(item["contribution"] for item in result["contributions"].values())
        self.assertAlmostEqual(total, result["score"], places=3)
        self.assertAlmostEqual(
            sum(item["shareOfIdfSignal"] for item in result["perTerm"]),
            result["idfSignal"]["value"], places=3,
        )
        self.assertIn("idf", breakdown["scoringFormula"])
        self.assertEqual(breakdown["tieBreak"]["order"], list(DEFAULT_CONFIG.tie_break))

    def test_breakdown_explains_a_thought_that_was_not_returned(self) -> None:
        breakdown = self.vault.recall_diagnostics("coffee oats bananas", thought_id=self.ids["hubs"])
        self.assertFalse(breakdown["result"]["present"])
        self.assertIn("reason", breakdown["result"])

    def test_honesty_records_say_what_was_and_was_not_used(self) -> None:
        outcome = self.recall("enemy adaptation")
        honesty = outcome.honesty
        self.assertIn("NOT IMPLEMENTED", honesty["embeddings"])
        self.assertIn("not used", honesty["ai"].lower())
        self.assertIn("none", honesty["network"].lower())
        self.assertIn("deterministic", honesty["probabilisticConfidence"].lower())
        self.assertIn("candidateGeneration", honesty)
        self.assertIn("filteredOut", honesty)
        generation = honesty["candidateGeneration"]
        self.assertLessEqual(generation["fuzzyPassCandidates"], DEFAULT_CONFIG.fuzzy_scoring_cap)
        self.assertEqual(generation["fuzzyPassCap"], DEFAULT_CONFIG.fuzzy_scoring_cap)
        self.assertEqual(generation["lexicalCandidateCap"], DEFAULT_CONFIG.token_candidate_cap)


class AmbiguityTestCase(RecallFixture):
    def test_several_near_equal_candidates_produce_a_refusal_not_a_guess(self) -> None:
        """Three near-duplicate memories must be reported as three, not as one.

        This is the case the mission singles out: "I found three likely memories"
        beats a confident wrong answer.  The three thoughts differ only in their
        final word, so no signal can legitimately separate them.
        """
        for label, verb in (("A", "flank"), ("B", "ambush"), ("C", "encircle")):
            self.vault.create_thought(
                f"Adaptive enemies should {verb} the player once the game teaches the pattern.",
                "THOUGHT", f"Enemy tactic {label}", ["balance", "enemy ai"],
            )
        self.vault.rebuild_recall_indexes(full=True)

        outcome = self.recall("adaptive enemies should the player once the game teaches the pattern")
        self.assertEqual(outcome.state, "AMBIGUOUS")
        self.assertTrue(outcome.ambiguity["ambiguous"])
        self.assertGreaterEqual(outcome.ambiguity["bandSize"], 3)
        self.assertIn("will not pick one", outcome.message.lower())
        band = outcome.ambiguity["bandIds"]
        self.assertGreaterEqual(len(band), 3)
        # refusing to guess is not refusing to help: every candidate is returned
        self.assertTrue(set(band) <= {result.thought_id for result in outcome.results})
        for result in outcome.results:
            if result.thought_id in band:
                self.assertEqual(result.state, "AMBIGUOUS")
                self.assertTrue(result.rivals, "each ambiguous candidate must say what distinguishes it")
        self.assertTrue(outcome.ambiguity["differences"],
                        "the refusal must be accompanied by the differences between candidates")

    def test_exact_ties_are_reported_as_ambiguous_even_with_two_candidates(self) -> None:
        outcome = self.recall("recall")
        tied = [r for r in outcome.results if abs(r.score - outcome.results[0].score) <= 1e-9]
        if len(tied) >= 2:
            self.assertEqual(outcome.state, "AMBIGUOUS")
            self.assertEqual(outcome.ambiguity["trigger"], "EXACT_TIE")
            self.assertTrue(outcome.ambiguity["orderingIsTieBreakNotJudgement"])

    def test_no_match_still_shows_the_closest_candidates(self) -> None:
        outcome = self.recall("xylophone zeppelin marzipan")
        self.assertEqual(outcome.state, "NO_MATCH")
        self.assertTrue(outcome.ambiguity.get("belowThreshold") or not outcome.results)
        if outcome.results:
            self.assertIn("nothing was hidden", outcome.message.lower())



if __name__ == "__main__":
    unittest.main()
