"""Recall V2 configuration — every weight, threshold and cap in one place.

CALIBRATION PROVENANCE
----------------------
The Graphify audit classifies Graphify's tier weights (exact 1000 / prefix 100 /
substring 1), its coverage exponent (``**2``), its hub threshold
(``max(50, p99)``) and its decay half-life (30 days) as **ADAPT** items: "right
mechanism, wrong calibration", because every one of them was tuned on code graphs
with short identifier-like labels.  IWTT labels are prose sentences and IWTT
graphs are sparse and personal.

So v0.2 adopts the *mechanisms* and re-derives the *constants*, which are all
below and all covered by tests:

  * weights are compressed to roughly 0.5–6 instead of 1–1000, so one literal
    phrase cannot swamp every relevant thought (matrix §3, ADAPT #2);
  * the coverage exponent is 1.5, between Graphify's 2 and linear 1 (ADAPT #4);
  * the hub threshold is ``max(5, ceil(p90(degree)))`` rather than
    ``max(50, p99)``, because a personal vault rarely has any node at degree 50
    (ADAPT #21), and hub suppression is applied as a *penalty* plus a transit
    rule, never as a filter;
  * the usefulness half-life is 180 days rather than 30, because personal
    thoughts decay slower than code facts (ADAPT #14), and it is configurable;
  * the corroboration gate is kept at 2 (ADOPT, matrix #14) so a single click can
    never mint a trusted lesson.

Nothing here is probabilistic.  Identical vault + query + config always produces
identical scores, identical ordering and identical labels.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

#: Bumped whenever the *generator* of derived data changes, so stale derived
#: rows are invalidated by version and not only by input change (matrix #27:
#: "derived data must be invalidated when the generator changes").
RECALL_INDEX_SCHEMA = 3
RECALL_GENERATOR_VERSION = "0.2.0"


@dataclass(frozen=True)
class RecallConfig:
    """Scoring model:

        score(d) = W_EXACT_PHRASE · exact_phrase
                 + W_IDF          · idf_weighted
                 + W_COVERAGE     · coverage
                 + W_TRIGRAM      · trigram
                 + W_FUZZY        · fuzzy
                 + W_TOKEN        · token_overlap
                 + W_TITLE        · title_match
                 + W_TAG          · tag_match
                 + W_GRAPH        · graph_contribution      (pre-capped)
                 + W_TEMPORAL     · temporal_boost
                 + W_LIFECYCLE    · lifecycle_boost
                 + W_QUERY_MEMORY · query_memory_value      (signed, gated)

    Each signal is normalized to 0..1 before weighting (``graph`` and
    ``query_memory`` are pre-scaled and clamped by their producers), so the
    weights are directly comparable and the theoretical maximum is 21.25.
    """

    # --- signal weights ---------------------------------------------------
    w_exact_phrase: float = 6.0
    w_idf: float = 3.0
    w_coverage: float = 2.0
    w_trigram: float = 1.5
    w_fuzzy: float = 1.5
    w_token_overlap: float = 0.75
    w_title: float = 1.0
    w_tag: float = 1.0
    w_graph: float = 1.0
    w_temporal: float = 0.5
    w_lifecycle: float = 0.5
    w_query_memory: float = 1.5

    # --- per-term lexical tiers (compressed re-derivation of Graphify's
    #     1000/100/1; only the strongest tier per term counts, so one term can
    #     never double-count) ---
    tier_exact: float = 1.00
    tier_variant: float = 0.90      # v0.1 morphology / alias map
    tier_prefix: float = 0.75
    tier_substring: float = 0.60
    tier_fuzzy: float = 0.50        # scaled by the observed similarity
    tier_min_token_length: int = 4  # prefix/substring/fuzzy need >= 4 chars
    fuzzy_token_threshold: float = 0.70

    # --- coverage ---------------------------------------------------------
    coverage_exponent: float = 1.5

    # --- candidate generation caps ---------------------------------------
    trigram_candidate_cap: int = 400
    token_candidate_cap: int = 600
    #: "Never-worse" guard: if the rarest query trigram still covers more than
    #: this fraction of the vault, the trigram index is not selective, so the
    #: lexical candidate set is used unfiltered instead.  The index may only
    #: speed things up, never change results.
    trigram_guard_fraction: float = 0.10
    #: Number of candidates that receive the expensive difflib fuzzy pass.
    fuzzy_scoring_cap: int = 120
    fuzzy_min_similarity: float = 0.42
    trigram_min_similarity: float = 0.08

    # --- graph expansion --------------------------------------------------
    graph_seeds_max: int = 6
    graph_seed_gap_ratio: float = 0.20     # stop adding seeds 20% below the top
    graph_min_seed_score: float = 1.5      # don't traverse from weak evidence
    graph_max_depth: int = 2
    graph_max_nodes: int = 40
    graph_edge_base: float = 0.45
    graph_depth_decay: float = 0.50
    graph_max_contribution: float = 2.0    # hard ceiling on graph influence

    # --- provenance x status weighting ------------------------------------
    #: v0.1 separates *origin* (``provenance``, immutable) from *consent*
    #: (``status``).  Confirming an AI suggestion keeps ``provenance`` =
    #: AI_SUGGESTED, so authority is derived from the pair — never from
    #: provenance alone, and never silently rewritten.
    weight_user_confirmed: float = 1.00    # USER_CONFIRMED + CONFIRMED
    weight_ai_confirmed: float = 0.70      # AI_SUGGESTED  + CONFIRMED (user consented)
    weight_ai_suggested: float = 0.35      # AI_SUGGESTED  + SUGGESTED (proposal only)

    # --- hub suppression --------------------------------------------------
    hub_min_degree: int = 5
    hub_percentile: float = 0.90
    hub_floor: float = 0.15                # attenuate, never eliminate

    # --- path discovery ---------------------------------------------------
    path_max_depth: int = 6
    path_max_visited: int = 5000

    # --- clustering -------------------------------------------------------
    cluster_labelprop_iterations: int = 6
    cluster_labelprop_min_size: int = 24   # below this, a component is its own cluster

    # --- result gating ----------------------------------------------------
    max_results: int = 25
    min_score: float = 0.35

    # --- match / outcome states ------------------------------------------
    strong_min_score: float = 6.0
    ambiguity_ratio: float = 0.90
    ambiguity_min_candidates: int = 3
    #: Exact ties at the top are ambiguous even with only two candidates.
    ambiguity_exact_tie_min: int = 2

    # --- query-outcome memory (derived) ----------------------------------
    query_memory_half_life_days: float = 180.0
    query_memory_min_corroboration: int = 2
    #: A single uncorroborated event counts at this fraction of its weight, so
    #: one click can influence but never dominate.
    query_memory_single_observation_factor: float = 0.5
    query_memory_positive_cap: float = 1.0
    query_memory_negative_cap: float = -0.75
    record_opened_signal: bool = True

    # --- determinism ------------------------------------------------------
    #: Final ordering key, applied in this exact order:
    #:   score DESC, trigram DESC, fuzzy DESC, created_at ASC, thought_id ASC
    #: Durable UUIDs make the last component a total order, so database row
    #: order can never decide a ranking.
    tie_break: tuple[str, ...] = ("score", "trigram", "fuzzy", "created_at", "thought_id")

    # --- honesty flags ---------------------------------------------------
    embeddings_enabled: bool = False
    semantic_stage_enabled: bool = False
    ai_required: bool = False
    network_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tie_break"] = list(self.tie_break)
        data["theoretical_max_score"] = self.theoretical_max_score()
        data["recall_index_schema"] = RECALL_INDEX_SCHEMA
        data["recall_generator_version"] = RECALL_GENERATOR_VERSION
        return data

    def to_public_dict(self) -> dict[str, Any]:
        """camelCase view for the HTTP API and the UI.

        v0.1 publishes camelCase in every public dict; the recall config is now
        part of that surface (``/api/recall/config``), so it follows the same
        convention.  The dataclass fields stay snake_case internally.
        """
        def camel(key: str) -> str:
            head, *rest = key.split("_")
            return head + "".join(part[:1].upper() + part[1:] for part in rest)

        data = {camel(key): value for key, value in self.to_dict().items()}
        data["tieBreak"] = list(self.tie_break)
        data["theoreticalMaxScore"] = self.theoretical_max_score()
        data["recallIndexSchema"] = RECALL_INDEX_SCHEMA
        data["recallGeneratorVersion"] = RECALL_GENERATOR_VERSION
        return data

    def theoretical_max_score(self) -> float:
        return round(
            self.w_exact_phrase
            + self.w_idf
            + self.w_coverage
            + self.w_trigram
            + self.w_fuzzy
            + self.w_token_overlap
            + self.w_title
            + self.w_tag
            + self.graph_max_contribution
            + self.w_temporal
            + self.w_lifecycle
            + self.w_query_memory * self.query_memory_positive_cap,
            4,
        )

    def with_overrides(self, **kwargs: Any) -> "RecallConfig":
        unknown = set(kwargs) - set(asdict(self))
        if unknown:
            raise ValueError(f"unknown recall config keys: {', '.join(sorted(unknown))}")
        return replace(self, **kwargs)


DEFAULT_CONFIG = RecallConfig()


def hub_factor_for_degree(degree: int, threshold: int, floor: float = 0.15) -> float:
    """Documented hub-suppression rule.

        hub_factor(d) = 1.0                                if d <= threshold
                      = floor + (1-floor) · threshold / d   otherwise

    with ``threshold = max(hub_min_degree, ceil(p90(degree)))`` and
    ``floor = 0.15``.

    Two properties are deliberate:

    * **It is a penalty, never a filter.** A hub's ``hub_factor`` is always
      ``> 0``, so a genuinely useful hub stays reachable — the audit is explicit
      that hub suppression must attenuate rather than exclude.
    * **It degrades gracefully.** A node at 10× the threshold contributes ~10×
      less per edge, so one omnipresent "misc" note or project index cannot
      swallow every neighbourhood.

    Hub nodes are additionally *not expanded as transit* unless they are seeds
    (Graphify's rule, matrix #7), which is applied in ``expansion.py``.
    """
    if degree <= 0 or threshold <= 0 or degree <= threshold:
        return 1.0
    return floor + (1.0 - floor) * (threshold / degree)

#: Relationship direction semantics for v0.1's 11 relationship types.
#: ``symmetric=True`` means traversal is legal both ways *because the v0.1 model
#: defines it that way* — not because IWTT ignores direction.  Every asymmetric
#: type is traversed with its direction recorded and its reverse reading labelled.
RELATIONSHIP_SEMANTICS: dict[str, dict[str, Any]] = {
    "RELATED_TO":      {"symmetric": True,  "forward": "related to",         "reverse": "related to",         "weight": 0.80},
    "CONTRADICTS":     {"symmetric": True,  "forward": "contradicts",        "reverse": "contradicts",        "weight": 0.70},
    "SUPPORTS":        {"symmetric": True,  "forward": "supports",           "reverse": "supported by",       "weight": 0.90},
    "LED_TO":          {"symmetric": False, "forward": "led to",             "reverse": "was led to by",      "weight": 1.00},
    "INSPIRED_BY":     {"symmetric": False, "forward": "inspired by",        "reverse": "inspired",           "weight": 1.00},
    "IMPLEMENTED_BY":  {"symmetric": False, "forward": "implemented by",     "reverse": "implements",         "weight": 1.00},
    "SUPERSEDES":      {"symmetric": False, "forward": "supersedes",         "reverse": "superseded by",      "weight": 0.90},
    "PART_OF":         {"symmetric": False, "forward": "part of",            "reverse": "contains",           "weight": 0.90},
    "DEPENDS_ON":      {"symmetric": False, "forward": "depends on",         "reverse": "is depended on by",  "weight": 0.90},
    "REVIVES":         {"symmetric": False, "forward": "revives",            "reverse": "was revived by",     "weight": 0.85},
    "TESTS":           {"symmetric": False, "forward": "tests",              "reverse": "is tested by",       "weight": 0.85},
}

#: Types whose meaning implies a build artefact — used for the
#: "Connected to project" explanation.
PROJECT_BEARING_TYPES = frozenset({"IMPLEMENTED_BY", "LED_TO", "PART_OF", "TESTS"})


def relationship_semantics(rel_type: str) -> dict[str, Any]:
    """Unknown types fall back to asymmetric + weak, so a hand-authored or
    future relationship type can never be silently treated as symmetric."""
    return RELATIONSHIP_SEMANTICS.get(
        rel_type,
        {
            "symmetric": False,
            "forward": rel_type.lower().replace("_", " "),
            "reverse": "reverse " + rel_type.lower().replace("_", " "),
            "weight": 0.60,
        },
    )


def is_symmetric(rel_type: str) -> bool:
    return bool(relationship_semantics(rel_type)["symmetric"])


def relationship_weight(rel_type: str) -> float:
    return float(relationship_semantics(rel_type)["weight"])


def direction_label(rel_type: str, direction: str) -> str:
    """Human label for an edge traversed ``OUT`` (source→target) or ``IN``."""
    spec = relationship_semantics(rel_type)
    return spec["forward"] if direction == "OUT" else spec["reverse"]
