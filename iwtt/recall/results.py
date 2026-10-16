"""Recall V2 result types.

``MatchState`` and ``OutcomeState`` are **labels, not probabilities**.  They are
assigned by fixed thresholds over a deterministic score (see ``config.py``) and
mean exactly what RECALL_ENGINE.md says they mean.  IWTT never reports a
percentage confidence it cannot support, and never claims semantic understanding
it does not have.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class MatchState:
    """Per-result label."""

    STRONG = "STRONG_MATCH"
    POSSIBLE = "POSSIBLE_MATCH"
    AMBIGUOUS = "AMBIGUOUS"

    ALL = (STRONG, POSSIBLE, AMBIGUOUS)

    LABELS = {
        STRONG: "Strong match",
        POSSIBLE: "Possible match",
        AMBIGUOUS: "Ambiguous — one of several close candidates",
    }


class OutcomeState:
    """Whole-query label."""

    STRONG = "STRONG_MATCH"
    POSSIBLE = "POSSIBLE_MATCH"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"

    ALL = (STRONG, POSSIBLE, AMBIGUOUS, NO_MATCH)


#: Explanation vocabulary.  Structured reason **codes** with evidence, rendered
#: as prose by the UI.  The audit's ADAPT #18 is explicit that prose alone cannot
#: be audited or re-rendered, so codes are the stored form and prose is the view.
EXPLANATION_LABELS: dict[str, str] = {
    "EXACT_PHRASE": "Exact phrase",
    "EXACT_PHRASE_TITLE": "Exact phrase in the title",
    "STRONG_KEYWORD": "Strong keyword match",
    "RARE_TERM": "Rare term match",
    "QUERY_COVERAGE": "Covers most of your query",
    "PARTIAL_COVERAGE": "Covers part of your query",
    "FUZZY_TEXT": "Fuzzy text match",
    "NO_LEXICAL_EVIDENCE": "No word in common — similarity suppressed",
    "TRIGRAM": "Similar spelling",
    "TYPO_TOLERATED": "Matched despite a spelling difference",
    "TITLE_MATCH": "Title match",
    "TAG_MATCH": "Tag match",
    "ALIAS_MATCH": "Matched through a word alias",
    "MORPHOLOGY_MATCH": "Matched a different word form",
    "RELATED_CONFIRMED": "Related through a confirmed connection",
    "RELATED_SUGGESTED": "Related through a suggested connection",
    "CONNECTED_TO_PROJECT": "Connected to a project",
    "MULTI_HOP": "Reached by following connections",
    "HISTORICAL": "Historical connection",
    "PREVIOUSLY_USEFUL": "Previously useful for a similar query",
    "PREVIOUSLY_NOT_USEFUL": "Previously marked not useful",
    "TIME_WINDOW": "Created inside the requested period",
    "TIME_HINT": "Created near the period you mentioned",
    "LIFECYCLE_MATCH": "Matches the lifecycle state you asked for",
    "GRAVEYARD": "Found in the Graveyard",
    "RESURRECTED": "Buried and later resurrected",
    "ORIGINAL_WORDING": "Found in your original wording",
    "CURRENT_WORDING": "Found in your current version",
    "IN_CLUSTER": "In the same automatically discovered cluster",
}


@dataclass
class Explanation:
    code: str
    label: str = ""
    detail: str = ""
    contribution: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.label:
            self.label = EXPLANATION_LABELS.get(self.code, self.code.replace("_", " ").title())

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "detail": self.detail,
            "contribution": round(self.contribution, 4),
            "evidence": self.evidence,
        }


def camel_case(name: str) -> str:
    """``exact_phrase`` -> ``exactPhrase``.

    IWTT's public dictionaries are camelCase everywhere (a v0.1 convention the
    web UI and the HTTP API depend on).  Dataclass fields stay snake_case, which
    is the Python convention, so the boundary is crossed here once rather than by
    hand in every ``to_dict``.
    """
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def public_dict(instance: Any) -> dict[str, Any]:
    """``asdict`` with the field names converted to the public convention."""
    return {camel_case(key): value for key, value in asdict(instance).items()}


@dataclass
class SignalScores:
    """Per-signal values BEFORE weighting.  This is the diagnostic surface."""

    exact_phrase: float = 0.0
    exact_phrase_where: str = ""
    token_overlap: float = 0.0
    idf_weighted: float = 0.0
    idf_terms: tuple[tuple[str, float, float], ...] = ()   # (term, idf, tier)
    coverage: float = 0.0
    coverage_matched: tuple[str, ...] = ()
    coverage_missing: tuple[str, ...] = ()
    coverage_ratio: float = 0.0
    #: False when no query term matched this thought at all.  Reported so the UI
    #: and the diagnostics can say "this is a coincidence, not a match".
    lexical_evidence: bool = True
    #: What the fuzzy/trigram signals WOULD have been before the gate zeroed them.
    gated_fuzzy: float = 0.0
    gated_trigram: float = 0.0
    trigram: float = 0.0
    trigram_shared: int = 0
    trigram_dot: float = 0.0
    fuzzy: float = 0.0
    fuzzy_window: str = ""
    title: float = 0.0
    tag: float = 0.0
    tag_hits: tuple[str, ...] = ()
    graph: float = 0.0
    graph_origin: str = ""
    graph_depth: int = 0
    graph_edge_type: str = ""
    graph_direction: str = ""
    graph_label: str = ""
    graph_provenance: str = ""
    graph_status: str = ""
    graph_hub_factor: float = 1.0
    graph_path: tuple[str, ...] = ()
    temporal: float = 0.0
    temporal_label: str = ""
    lifecycle: float = 0.0
    query_memory: float = 0.0
    query_memory_events: int = 0
    query_memory_corroborated: bool = False
    query_memory_detail: tuple[dict[str, Any], ...] = ()
    tier_hits: tuple[tuple[str, str, str], ...] = ()       # (term, tier, matched_form)

    def to_dict(self) -> dict[str, Any]:
        data = public_dict(self)
        for key in ("coverageMatched", "coverageMissing", "tagHits", "graphPath"):
            data[key] = list(data[key])
        data["idfTerms"] = [[t, round(w, 4), round(tier, 4)] for t, w, tier in self.idf_terms]
        data["tierHits"] = [[t, tier, form] for t, tier, form in self.tier_hits]
        data["queryMemoryDetail"] = list(data["queryMemoryDetail"])
        return data


@dataclass
class RelationshipContext:
    """How this result sits in the graph, in words a human can read."""

    thought_id: str
    display_title: str
    rel_type: str
    direction: str                 # OUT (source→target) | IN (target→source)
    label: str                     # "led to" / "was led to by"
    provenance: str                # USER_CONFIRMED | AI_SUGGESTED  (origin, immutable)
    status: str                    # CONFIRMED | SUGGESTED          (user consent)
    display: str                   # "Confirmed connection" | "Suggested connection"
    depth: int = 1
    via: tuple[str, ...] = ()
    relationship_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = public_dict(self)
        data["via"] = list(data["via"])
        return data


@dataclass
class RecallResult:
    thought_id: str
    title: str
    display_title: str
    snippet: str
    type: str
    status: str
    created_at: str
    updated_at: str
    tags: tuple[str, ...] = ()
    score: float = 0.0
    rank: int = 0
    state: str = MatchState.POSSIBLE
    match_reason: str = ""                 # v0.1-compatible vocabulary
    signals: SignalScores = field(default_factory=SignalScores)
    explanations: tuple[Explanation, ...] = ()
    relationship_context: RelationshipContext | None = None
    cluster: dict[str, Any] | None = None
    original_text: str = ""
    current_text: str = ""
    graveyard_at: str | None = None
    resurrected_at: str | None = None
    is_demo: bool = False
    degree: int = 0
    tie_break: tuple[Any, ...] = ()
    rivals: tuple[str, ...] = ()           # what distinguishes this from the others

    @property
    def is_graveyard(self) -> bool:
        return self.status == "GRAVEYARD"

    def why(self) -> str:
        """Compact, human-readable reason.  Never exceeds three clauses."""
        if not self.explanations:
            return "Matched on general text similarity."
        return " · ".join(explanation.label for explanation in self.explanations[:3])

    def to_dict(self, *, include_diagnostics: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.thought_id,
            "thoughtId": self.thought_id,
            "title": self.title,
            "displayTitle": self.display_title,
            "snippet": self.snippet,
            "type": self.type,
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "tags": list(self.tags),
            "score": round(self.score, 4),
            "rank": self.rank,
            "state": self.state,
            "stateLabel": MatchState.LABELS.get(self.state, self.state),
            "matchReason": self.match_reason,
            "why": self.why(),
            "explanations": [e.to_dict() for e in self.explanations],
            "relationshipContext": (
                self.relationship_context.to_dict() if self.relationship_context else None
            ),
            "cluster": self.cluster,
            "graveyardAt": self.graveyard_at,
            "resurrectedAt": self.resurrected_at,
            "isDemo": self.is_demo,
            "degree": self.degree,
            "rivals": list(self.rivals),
            "tieBreak": [str(part) for part in self.tie_break],
            "whatIActuallyWrote": {
                "originalText": self.original_text,
                "currentText": self.current_text,
            },
        }
        if include_diagnostics:
            data["signals"] = self.signals.to_dict()
        return data


@dataclass
class RecallOutcome:
    query: str
    normalized_query: str
    effective_query: str
    state: str = OutcomeState.NO_MATCH
    message: str = ""
    results: list[RecallResult] = field(default_factory=list)
    candidates_examined: int = 0
    ambiguity: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)
    index_state: dict[str, Any] = field(default_factory=dict)
    honesty: dict[str, Any] = field(default_factory=dict)
    graph: dict[str, Any] = field(default_factory=dict)

    @property
    def top(self) -> RecallResult | None:
        return self.results[0] if self.results else None

    def to_dict(self, *, include_diagnostics: bool = False) -> dict[str, Any]:
        return {
            "query": self.query,
            "normalizedQuery": self.normalized_query,
            "effectiveQuery": self.effective_query,
            "state": self.state,
            "stateLabel": {
                OutcomeState.STRONG: "Strong match",
                OutcomeState.POSSIBLE: "Possible match",
                OutcomeState.AMBIGUOUS: "Multiple possible thoughts found",
                OutcomeState.NO_MATCH: "Nothing plausible found",
            }.get(self.state, self.state),
            "message": self.message,
            "results": [r.to_dict(include_diagnostics=include_diagnostics) for r in self.results],
            "resultCount": len(self.results),
            "candidatesExamined": self.candidates_examined,
            "ambiguity": self.ambiguity,
            "filters": self.filters,
            "analysis": self.analysis,
            "graph": self.graph,
            "timingsMs": {k: round(v, 3) for k, v in self.timings_ms.items()},
            "indexState": self.index_state,
            "honesty": self.honesty,
        }


@dataclass
class PathStep:
    thought_id: str
    display_title: str
    type: str
    status: str
    created_at: str


@dataclass
class PathEdge:
    relationship_id: str
    source_id: str
    target_id: str
    rel_type: str
    direction: str           # OUT relative to the walk
    label: str
    provenance: str
    status: str
    display: str
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
