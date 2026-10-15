"""IWTT v0.2 — Recall & Graph Intelligence.

An **additive** subsystem.  ``Vault.search()`` (v0.1) is frozen and untouched;
Recall V2 is reached through ``Vault.recall()`` and ``/api/recall``.

Layering, enforced by ``tests/v02/test_layering.py``:

    iwtt.store          canonical truth + the frozen v0.1 search
    iwtt.recall.index   derived indexes (recall_* tables) — rebuildable, deletable
    iwtt.recall.*       query understanding, scoring, graph, paths, clusters,
                        query-outcome memory, explanations, diagnostics
    iwtt.recall.external  entity-boundary interfaces (no ingestion, no schema change)

Nothing in this package writes to a canonical table.  The one canonical-adjacent
write it performs is *reading*; the only rows it creates live in ``recall_*``
tables that ``Vault.clear_derived_data()`` deletes.

Requires no AI, no network and no embeddings.  If those are ever added they
arrive as an optional extra candidate generator, never as a dependency.
"""
from __future__ import annotations

from .analysis import QueryAnalysis, analyze_query
from .clusters import cluster_summary, cohesion_score, connected_components, label_propagation
from .config import (
    DEFAULT_CONFIG,
    RECALL_GENERATOR_VERSION,
    RECALL_INDEX_SCHEMA,
    RELATIONSHIP_SEMANTICS,
    RecallConfig,
    direction_label,
    hub_factor_for_degree,
    is_symmetric,
    relationship_semantics,
    relationship_weight,
)
from .engine import HONESTY_STATEMENT, RecallEngine
from .expansion import (
    GraphSignal,
    connection_display,
    expand_neighbourhood,
    neighbourhood,
    pick_seeds,
    provenance_weight,
)
from .external import (
    CAPABILITY_MANIFEST,
    CROSS_DOMAIN_DESIGN,
    ENTITY_EXTERNAL,
    ENTITY_THOUGHT,
    EXTERNAL_ENTITY_TYPES,
    EntityBoundaryError,
    ExternalEntityRef,
    assert_not_thought,
    capability_manifest,
    cross_domain_design,
    validate_candidate_entity,
)
from .filters import (
    RecallFilters,
    TimeWindow,
    detect_time_hint,
    matches_filters,
    parse_time_expression,
    window_for,
)
from .graphview import build_graph_view_model
from .index import RECALL_DERIVED_TABLES, RECALL_SCHEMA_SQL, BuildReport, RecallIndex
from .paths import PathResult, find_path, lineage
from .querymemory import FEEDBACK_WEIGHTS, memory_key, record_feedback, reset_learning, usefulness
from .results import (
    EXPLANATION_LABELS,
    Explanation,
    MatchState,
    OutcomeState,
    RecallOutcome,
    RecallResult,
    RelationshipContext,
    SignalScores,
)
from .scoring import (
    TIER_VALUES,
    assign_states,
    build_explanations,
    load_candidates,
    match_reason,
    score_candidate,
    sort_key,
    term_tier,
)

__all__ = [
    "RecallEngine",
    "HONESTY_STATEMENT",
    "RecallConfig",
    "DEFAULT_CONFIG",
    "RECALL_INDEX_SCHEMA",
    "RECALL_GENERATOR_VERSION",
    "RELATIONSHIP_SEMANTICS",
    "relationship_semantics",
    "relationship_weight",
    "is_symmetric",
    "direction_label",
    "hub_factor_for_degree",
    "QueryAnalysis",
    "analyze_query",
    "RecallFilters",
    "TimeWindow",
    "window_for",
    "parse_time_expression",
    "detect_time_hint",
    "matches_filters",
    "RecallIndex",
    "BuildReport",
    "RECALL_DERIVED_TABLES",
    "RECALL_SCHEMA_SQL",
    "GraphSignal",
    "pick_seeds",
    "expand_neighbourhood",
    "neighbourhood",
    "provenance_weight",
    "connection_display",
    "PathResult",
    "find_path",
    "lineage",
    "build_graph_view_model",
    "cluster_summary",
    "connected_components",
    "label_propagation",
    "cohesion_score",
    "FEEDBACK_WEIGHTS",
    "memory_key",
    "record_feedback",
    "usefulness",
    "reset_learning",
    "Explanation",
    "EXPLANATION_LABELS",
    "MatchState",
    "OutcomeState",
    "RecallOutcome",
    "RecallResult",
    "RelationshipContext",
    "SignalScores",
    "TIER_VALUES",
    "term_tier",
    "score_candidate",
    "assign_states",
    "build_explanations",
    "match_reason",
    "sort_key",
    "load_candidates",
    "ENTITY_THOUGHT",
    "ENTITY_EXTERNAL",
    "EXTERNAL_ENTITY_TYPES",
    "ExternalEntityRef",
    "EntityBoundaryError",
    "assert_not_thought",
    "CROSS_DOMAIN_DESIGN",
    "cross_domain_design",
    "CAPABILITY_MANIFEST",
    "capability_manifest",
    "validate_candidate_entity",
]
