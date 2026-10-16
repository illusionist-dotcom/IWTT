"""Graph Intelligence — traversal, direction, hubs, paths, clusters, view model.

The rules under test are the ones the mission treats as non-negotiable:

  * graph expansion ENRICHES candidates; it never replaces text retrieval;
  * USER_CONFIRMED and AI_SUGGESTED stay distinguishable everywhere, and using a
    suggested edge never confirms it;
  * traversal is direction-aware, and no edge is invented to close a gap;
  * hubs are penalised, never filtered out;
  * clusters and the GraphViewModel are derived, bounded, and carry no layout.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iwtt.recall.clusters import connected_components, label_propagation
from iwtt.recall.config import DEFAULT_CONFIG, hub_factor_for_degree, is_symmetric, relationship_weight
from iwtt.recall.expansion import provenance_weight
from iwtt.store import Vault


class GraphFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self._tmp.name) / "thought-vault.sqlite3")
        self.vault.initialize()
        self.ids: dict[str, str] = {}

        def add(key: str, text: str, title: str = "", tags=()):
            thought = self.vault.create_thought(text, "THOUGHT", title, list(tags))
            self.ids[key] = thought["id"]

        # A directed chain, kept free of shortcuts so path tests are unambiguous.
        add("seed", "Enemies should remember how the player fights and adapt.", "Enemy memory", ["combat"])
        add("middle", "A difficulty curve that punishes adaptation feels unfair.", "Adaptive difficulty")
        add("far", "Procedural dungeon generation needs a stable seed per floor.", "Seeded dungeons")
        add("unrelated", "Coffee, oats and bananas.", "Shopping list")

        # A real hub: more connections than the hub threshold, on its own spokes so
        # it cannot create a shortcut across the chain above.
        add("hub", "Every design decision in the game traces back to this one note.", "Design hub")
        for index in range(8):
            add(f"spoke{index}", f"Design note {index} hanging off the central hub.", f"Spoke {index}")
            self.vault.create_relationship(self.ids["hub"], self.ids[f"spoke{index}"], "RELATED_TO")

        self.confirmed = self.vault.create_relationship(
            self.ids["seed"], self.ids["middle"], "LED_TO", provenance="USER_CONFIRMED"
        )
        self.suggested = self.vault.create_relationship(
            self.ids["middle"], self.ids["far"], "INSPIRED_BY", provenance="AI_SUGGESTED"
        )
        self.vault.rebuild_recall_indexes(full=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TraversalTestCase(GraphFixture):
    def test_expansion_enriches_but_never_outranks_a_direct_text_match(self) -> None:
        outcome = self.vault.recall("procedural dungeon seed floor")
        direct = next(r for r in outcome.results if r.thought_id == self.ids["far"])
        self.assertEqual(outcome.results[0].thought_id, self.ids["far"])
        self.assertEqual(direct.signals.graph, 0.0, "a seed never receives a graph bonus from itself")

        # "combat" only appears in the seed thought; the hub and neighbours can be
        # reached but must stay below the thought that actually contains the word.
        outcome = self.vault.recall("combat adaptation memory")
        self.assertEqual(outcome.results[0].thought_id, self.ids["seed"])
        for result in outcome.results[1:]:
            self.assertLess(result.score, outcome.results[0].score)
            if result.signals.graph > 0:
                self.assertLessEqual(
                    result.signals.graph, DEFAULT_CONFIG.graph_max_contribution,
                    "graph contribution is hard-capped below a direct textual match",
                )

    def test_graph_reached_candidates_are_labelled_with_their_origin(self) -> None:
        outcome = self.vault.recall("enemy memory adaptation")
        reached = [r for r in outcome.results if r.signals.graph > 0]
        self.assertTrue(reached, "the neighbourhood of a strong match must be reachable")
        for result in reached:
            self.assertTrue(result.signals.graph_origin)
            self.assertGreaterEqual(result.signals.graph_depth, 1)
            self.assertIn(result.signals.graph_direction, ("OUT", "IN"))
            self.assertIn(result.signals.graph_provenance, ("USER_CONFIRMED", "AI_SUGGESTED"))
            self.assertTrue(result.relationship_context,
                            "a graph-reached result must say which connection brought it here")
        by_id = {result.thought_id: result for result in reached}
        middle = by_id[self.ids["middle"]]
        self.assertEqual(middle.signals.graph_origin, self.ids["seed"])
        self.assertEqual(middle.signals.graph_provenance, "USER_CONFIRMED")
        self.assertEqual(middle.signals.graph_depth, 1)
        self.assertTrue(middle.signals.lexical_evidence)
        self.assertIn(middle.state, ("STRONG_MATCH", "POSSIBLE_MATCH"),
                      "the graph link only enriches a thought that matched the words itself")

    def test_a_thought_reachable_only_through_a_suggestion_is_not_returned_as_a_match(self) -> None:
        """Expansion enriches; it never invents relevance.

        ``far`` shares no word with the query and is two hops away along an
        AI-suggested edge.  It must not appear as a match — but it must not vanish
        without a trace either.
        """
        outcome = self.vault.recall("enemy memory")
        self.assertNotIn(self.ids["far"], [r.thought_id for r in outcome.results])
        below = outcome.honesty["graphReachedBelowThreshold"]
        self.assertIn(self.ids["far"], [item["thoughtId"] for item in below])
        entry = next(item for item in below if item["thoughtId"] == self.ids["far"])
        self.assertEqual(entry["graphProvenance"], "AI_SUGGESTED")
        self.assertGreaterEqual(entry["graphDepth"], 2)
        self.assertTrue(entry["graphOnly"], "no word of the query appears in it")
        self.assertIn(entry["reason"], ("BELOW_MIN_SCORE", "NO_LEXICAL_EVIDENCE", "BOTH"))
        self.assertEqual(entry["originTitle"], "Enemy memory",
                         "the record says which thought the connection came from — the strongest "
                         "seed wins, and contribution is a maximum over paths, never a sum")

    def test_a_thought_linked_by_hand_is_reported_but_never_ranked_as_a_match(self) -> None:
        """Association is not evidence — even when the user made the association.

        ``linked`` shares no word with the query.  It is one hop from a strong
        match along an edge the user confirmed personally, which is the strongest
        non-lexical signal IWTT has — and it is still not returned as a match,
        because graph expansion enriches retrieval rather than replacing it.  What
        it does get is a named record, so the connection is visible.
        """
        linked = self.vault.create_thought(
            "Zzz qqq vvv — deliberately no word in common with any query.", "THOUGHT", "Hand linked note"
        )
        self.vault.create_relationship(
            self.ids["seed"], linked["id"], "LED_TO", provenance="USER_CONFIRMED"
        )
        self.vault.rebuild_recall_indexes(full=True)

        outcome = self.vault.recall("enemy memory player fights")
        self.assertNotIn(linked["id"], [result.thought_id for result in outcome.results])
        below = outcome.honesty["graphReachedBelowThreshold"]
        self.assertIn(linked["id"], [item["thoughtId"] for item in below])
        entry = next(item for item in below if item["thoughtId"] == linked["id"])
        self.assertIn(entry["reason"], ("NO_LEXICAL_EVIDENCE", "BOTH"))
        self.assertTrue(entry["graphOnly"])
        self.assertEqual(entry["displayTitle"], "Hand linked note")
        self.assertEqual(entry["graphProvenance"], "USER_CONFIRMED")
        self.assertEqual(entry["graphDepth"], 1)
        self.assertEqual(outcome.honesty["graphReachedBelowThresholdCount"], len(below))

        # and the config states the policy instead of leaving it implicit
        self.assertFalse(self.vault.recall_config()["graphMaySurfaceWithoutLexicalEvidence"])

        # the same shape of link, but only AI-suggested, is treated no better
        suggested_only = self.vault.create_thought(
            "Xyz wxy — again nothing in common with the query.", "THOUGHT", "Suggested only note"
        )
        self.vault.create_relationship(
            self.ids["seed"], suggested_only["id"], "LED_TO", provenance="AI_SUGGESTED"
        )
        self.vault.rebuild_recall_indexes(full=True)
        outcome = self.vault.recall("enemy memory player fights")
        self.assertNotIn(suggested_only["id"], [r.thought_id for r in outcome.results])
        self.assertIn(
            suggested_only["id"],
            [item["thoughtId"] for item in outcome.honesty["graphReachedBelowThreshold"]],
        )

    def test_hubs_are_penalised_but_never_filtered_out(self) -> None:
        threshold = DEFAULT_CONFIG.hub_min_degree
        self.assertEqual(hub_factor_for_degree(1, threshold), 1.0)
        self.assertEqual(hub_factor_for_degree(threshold, threshold), 1.0)
        heavy = hub_factor_for_degree(threshold * 20, threshold)
        self.assertLess(heavy, 1.0)
        self.assertGreater(heavy, 0.0, "a hub factor of zero would be a filter, not a penalty")
        self.assertGreaterEqual(heavy, DEFAULT_CONFIG.hub_floor)
        # monotonic: more connections never means more authority
        self.assertGreater(
            hub_factor_for_degree(threshold * 5, threshold),
            hub_factor_for_degree(threshold * 50, threshold),
        )

    def test_hub_nodes_remain_reachable_in_the_view_model(self) -> None:
        model = self.vault.graph_view_model(max_nodes=50)
        node_ids = {node["id"] for node in model["nodes"]}
        self.assertIn(self.ids["hub"], node_ids, "a hub is down-weighted, never deleted")
        hub_node = next(n for n in model["nodes"] if n["id"] == self.ids["hub"])
        self.assertEqual(hub_node["degree"], 8)
        self.assertLess(hub_node["hubFactor"], 1.0)
        self.assertTrue(hub_node["isHub"])

    def test_removed_relationships_are_not_traversed(self) -> None:
        self.vault.remove_relationship(self.confirmed["id"])
        self.vault.rebuild_recall_indexes(full=True)
        model = self.vault.graph_view_model(root_ids=[self.ids["seed"]], max_nodes=30)
        self.assertNotIn(self.confirmed["id"], {edge["id"] for edge in model["edges"]})
        path = self.vault.find_path(self.ids["seed"], self.ids["middle"])
        self.assertFalse(path["found"])
        self.assertIn("will not invent", path["reason"])
        self.assertEqual(path["edges"], [])
        # the canonical record is retained, exactly as v0.1 does it
        removed = self.vault.get_relationship(self.confirmed["id"])
        self.assertEqual(removed["status"], "REMOVED")


class PathTestCase(GraphFixture):
    def test_directed_path_follows_stored_direction(self) -> None:
        forward = self.vault.find_path(self.ids["seed"], self.ids["far"])
        self.assertTrue(forward["found"])
        self.assertEqual(forward["length"], 2)
        self.assertEqual([node["id"] for node in forward["nodes"]],
                         [self.ids["seed"], self.ids["middle"], self.ids["far"]])
        self.assertTrue(forward["narrative"])

        reverse = self.vault.find_path(self.ids["far"], self.ids["seed"])
        self.assertFalse(reverse["found"], "LED_TO and INSPIRED_BY are asymmetric")
        self.assertIn("will not invent", reverse["reason"])

    def test_undirected_walk_still_reports_the_true_stored_direction(self) -> None:
        result = self.vault.find_path(self.ids["far"], self.ids["seed"], directed=False)
        self.assertTrue(result["found"])
        self.assertFalse(result["directed"])
        types = {edge["relType"] for edge in result["edges"]}
        self.assertEqual(types, {"LED_TO", "INSPIRED_BY"})
        for edge in result["edges"]:
            self.assertIn(edge["direction"], ("OUT", "IN"))
            self.assertTrue(edge["display"])

    def test_symmetric_relationships_traverse_both_ways(self) -> None:
        self.assertTrue(is_symmetric("RELATED_TO"))
        self.assertFalse(is_symmetric("LED_TO"))
        spoke = self.ids["spoke0"]
        hub_to_spoke = self.vault.find_path(self.ids["hub"], spoke)
        spoke_to_hub = self.vault.find_path(spoke, self.ids["hub"])
        self.assertTrue(hub_to_spoke["found"])
        self.assertTrue(spoke_to_hub["found"], "RELATED_TO is symmetric, so both directions are real")
        self.assertEqual(hub_to_spoke["length"], spoke_to_hub["length"])
        self.assertNotEqual(hub_to_spoke["edges"][0]["direction"],
                            spoke_to_hub["edges"][0]["direction"],
                            "the walk is reported from the end you asked for")

    def test_no_path_is_reported_as_no_path(self) -> None:
        lonely = self.vault.create_thought("A note with no connections at all.", "THOUGHT", "Lonely")
        result = self.vault.find_path(self.ids["seed"], lonely["id"])
        self.assertFalse(result["found"])
        self.assertEqual(result["edges"], [], "no walk may be reported when none exists")
        self.assertEqual(result["length"], 0)
        self.assertEqual([node["id"] for node in result["nodes"]],
                         [self.ids["seed"], lonely["id"]],
                         "the endpoints are named so the answer is checkable, but no edge joins them")
        self.assertIn("no known relationship path", result["reason"].lower())
        self.assertLessEqual(result["bounds"]["maxDepth"], DEFAULT_CONFIG.path_max_depth)
        self.assertLessEqual(result["visited"], DEFAULT_CONFIG.path_max_visited)

    def test_lineage_separates_upstream_from_downstream(self) -> None:
        lineage = self.vault.thought_lineage(self.ids["middle"])
        upstream = {item["thoughtId"] for item in lineage["upstream"]}
        downstream = {item["thoughtId"] for item in lineage["downstream"]}
        self.assertIn(self.ids["seed"], upstream)
        self.assertIn(self.ids["far"], downstream)
        self.assertNotIn(self.ids["seed"], downstream)
        self.assertFalse(lineage["canonical"] if "canonical" in lineage else False)

    def test_neighbourhood_is_bounded_and_direction_aware(self) -> None:
        data = self.vault.thought_neighbourhood(self.ids["middle"], depth=1)
        self.assertEqual(data["root"]["id"], self.ids["middle"])
        directions = {item["direction"] for item in data["neighbours"]}
        self.assertEqual(directions, {"IN", "OUT"})
        labels = {item["thoughtId"]: item["label"] for item in data["neighbours"]}
        self.assertNotEqual(labels[self.ids["seed"]], labels[self.ids["far"]],
                            "the two directions of a chain must read differently")
        self.assertLessEqual(len(data["levels"]), DEFAULT_CONFIG.graph_max_depth)

    def test_relationship_weighting_distinguishes_kinds(self) -> None:
        from iwtt.recall.config import RELATIONSHIP_SEMANTICS

        self.assertEqual(len(RELATIONSHIP_SEMANTICS), 11, "all eleven v0.1 types are described")
        self.assertGreater(relationship_weight("LED_TO"), relationship_weight("RELATED_TO"))
        self.assertGreater(relationship_weight("SUPPORTS"), relationship_weight("CONTRADICTS"))
        for rel_type, spec in RELATIONSHIP_SEMANTICS.items():
            self.assertEqual(relationship_weight(rel_type), spec["weight"])
            self.assertEqual(is_symmetric(rel_type), spec["symmetric"])
        unknown = relationship_weight("NOT_A_REAL_TYPE")
        self.assertLessEqual(unknown, min(spec["weight"] for spec in RELATIONSHIP_SEMANTICS.values()),
                             "an unknown type must fall back to the weakest weight, never the strongest")
        self.assertFalse(is_symmetric("NOT_A_REAL_TYPE"),
                         "an unknown type is treated as directional: assuming symmetry would invent meaning")


class ClusterTestCase(GraphFixture):
    def test_clusters_are_derived_and_labelled_as_automatically_discovered(self) -> None:
        clusters = self.vault.clusters()
        self.assertTrue(clusters)
        for cluster in clusters:
            self.assertEqual(cluster["authority"], "AUTOMATICALLY_DISCOVERED_CLUSTER")
            self.assertTrue(cluster["derived"])
            self.assertEqual(cluster["algorithm"], "components+labelprop")
            self.assertTrue(cluster["label"].startswith("Automatically discovered cluster:"))
            self.assertTrue(cluster["clusterId"].startswith("c-"))
            self.assertEqual(cluster["size"], len(cluster["members"]))
            self.assertGreaterEqual(cluster["cohesion"], 0.0)
            self.assertLessEqual(cluster["cohesion"], 1.0)
            for thought_id in cluster["members"]:
                self.assertIn(thought_id, cluster["memberTitles"])

    def test_cluster_identity_is_stable_across_rebuilds(self) -> None:
        first = {cluster["clusterId"]: sorted(cluster["members"]) for cluster in self.vault.clusters()}
        self.vault.rebuild_recall_indexes(full=True)
        second = {cluster["clusterId"]: sorted(cluster["members"]) for cluster in self.vault.clusters()}
        self.assertEqual(first, second, "the same graph must produce the same cluster ids")

    def test_cluster_identity_survives_an_unrelated_insertion(self) -> None:
        before = {cluster["clusterId"]: sorted(cluster["members"]) for cluster in self.vault.clusters()}
        self.vault.create_thought("A completely separate note about cooking rice.", "THOUGHT", "Rice")
        self.vault.rebuild_recall_indexes(full=True)
        after = {cluster["clusterId"]: sorted(cluster["members"]) for cluster in self.vault.clusters()}
        for cluster_id, members in before.items():
            self.assertIn(cluster_id, after, "adding an unrelated thought must not renumber clusters")
            self.assertEqual(after[cluster_id], members)

    def test_deleting_derived_data_removes_clusters_without_touching_edges(self) -> None:
        relationships_before = self.vault.stats()["relationships"]
        self.vault.delete_all_derived_intelligence()
        self.assertEqual(self.vault.stats()["recall"]["clusters"], 0)
        self.assertEqual(self.vault.stats()["relationships"], relationships_before)
        self.assertTrue(self.vault.clusters(), "clusters rebuild themselves on demand")

    def test_component_and_label_propagation_helpers_are_deterministic(self) -> None:
        adjacency = {"a": {"b"}, "b": {"a", "c"}, "c": {"b"}, "x": {"y"}, "y": {"x"}, "z": set()}
        components = connected_components(adjacency)
        self.assertEqual(sorted(len(members) for members in components.values()), [1, 2, 3],
                         "a-b-c, x-y and the isolated z")
        self.assertEqual(components, connected_components(dict(adjacency)),
                         "the same graph must partition identically every time")
        for members in components.values():
            self.assertEqual(members, sorted(members))

        members = ["a", "b", "c", "x", "y"]
        weighted = {
            "a": [("b", 1.0)], "b": [("a", 1.0), ("c", 1.0)], "c": [("b", 1.0)],
            "x": [("y", 1.0)], "y": [("x", 1.0)],
        }
        first = label_propagation(members, weighted, iterations=DEFAULT_CONFIG.cluster_labelprop_iterations)
        second = label_propagation(members, weighted, iterations=DEFAULT_CONFIG.cluster_labelprop_iterations)
        self.assertEqual(first, second, "synchronous propagation must be reproducible")
        self.assertEqual(set(first), set(members))
        for member, label in first.items():
            self.assertIn(label, members, "a label is always a member id, never an invention")
        trio = {first["a"], first["b"], first["c"]}
        pair = {first["x"], first["y"]}
        self.assertFalse(trio & pair, "two components must never share a label")
        # Synchronous propagation on a 2-cycle oscillates (x→y→x) and on a 3-path
        # rotates; that is inherent to the synchronous rule, not a defect, and it is
        # why clusters.py partitions by connected component FIRST and uses these
        # labels only to subdivide a large component.  What must hold is that the
        # outcome is reproducible and that components never bleed into each other.


class GraphViewModelTestCase(GraphFixture):
    def test_view_model_is_an_induced_subgraph_with_no_layout(self) -> None:
        model = self.vault.graph_view_model(root_ids=[self.ids["seed"]], max_nodes=10)
        node_ids = {node["id"] for node in model["nodes"]}
        self.assertLessEqual(len(node_ids), 10)
        for edge in model["edges"]:
            self.assertIn(edge["source"], node_ids)
            self.assertIn(edge["target"], node_ids, "edges must only join selected nodes")
        self.assertFalse(model["layout"]["persisted"])
        self.assertFalse(model["layout"]["coordinatesIncluded"])
        for node in model["nodes"]:
            self.assertNotIn("x", node)
            self.assertNotIn("y", node)
            self.assertEqual(node["entityType"], "THOUGHT_ENTITY")

    def test_view_model_separates_canonical_from_derived_fields(self) -> None:
        model = self.vault.graph_view_model(root_ids=[self.ids["seed"]], max_nodes=10)
        self.assertFalse(model["canonical"], "the view model itself is derived data")
        self.assertTrue(model["derived"])
        for field in ("id", "displayLabel", "status", "createdAt", "edges.provenance"):
            self.assertIn(field, model["canonicalFields"])
        for field in ("degree", "hubFactor", "importance", "clusterId", "edges.weight"):
            self.assertIn(field, model["derivedFields"])
        self.assertFalse(set(model["canonicalFields"]) & set(model["derivedFields"]),
                         "a field must not be claimed as both canonical and derived")
        self.assertIn("THOUGHT_ENTITY", model["entityKinds"])
        self.assertIn("EXTERNAL_KNOWLEDGE_ENTITY", model["entityKinds"])
        self.assertIn("never a Thought", model["entityKinds"]["EXTERNAL_KNOWLEDGE_ENTITY"])

    def test_view_model_respects_the_node_budget(self) -> None:
        model = self.vault.graph_view_model(max_nodes=3)
        self.assertLessEqual(model["nodeCount"], 3)
        self.assertEqual(model["nodeCount"], len(model["nodes"]))
        self.assertTrue(model["bounds"]["truncated"], "13 thoughts cannot fit in a 3-node budget")
        self.assertEqual(model["bounds"]["returnedNodes"], 3)
        self.assertGreater(model["bounds"]["reachableNodes"], 3)
        self.assertIn("bounded view", model["bounds"]["note"])

        whole = self.vault.graph_view_model(max_nodes=500)
        self.assertFalse(whole["bounds"]["truncated"])
        self.assertEqual(whole["bounds"]["reachableNodes"], whole["nodeCount"])


if __name__ == "__main__":
    unittest.main()
