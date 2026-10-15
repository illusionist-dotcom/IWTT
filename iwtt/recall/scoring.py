"""Recall V2 — candidate loading, per-signal scoring, deterministic ranking.

RELATIONSHIP TO v0.1
--------------------
``Vault.search()`` is **frozen**: its tiers, thresholds, ``matchReason``
vocabulary and ordering are pinned by ``tests/test_store.py`` and
``scripts/verify_release.py``.  Recall V2 reuses v0.1's *definitions* —
``normalize_text``, ``tokenize``, ``_token_variants`` (morphology + the
transparent alias map) and the tier magnitudes of ``_fuzzy_token_score`` — and
adds what the audit says IWTT must design itself: IDF weighting, coverage
scaling, trigram similarity, a bounded fuzzy pass, graph/temporal/lifecycle/
query-memory signals, and a total-order tie-break.

One deliberate change from v0.1's implementation: ``_fuzzy_token_score`` compares
a query token against **every** token of a document with ``difflib``, and
``search()`` loads **every** document per query.  That is O(vault × tokens) with
an expensive inner loop.  Recall V2 bounds both:

  * candidates come from a capped SQL aggregation (``index.py``), never a full
    scan of the corpus;
  * the ``difflib`` pass runs only on the top ``fuzzy_scoring_cap`` candidates,
    and only against document tokens whose length is within ±2 of the query
    token, at most ``MAX_FUZZY_TOKEN_COMPARISONS`` per term.

Results are identical for anything the bounds admit; the bounds are documented
constants, not heuristics that silently change answers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Sequence

from ..store import Vault, _display_title, normalize_text, tokenize
from .config import DEFAULT_CONFIG, RecallConfig
from .results import Explanation, MatchState, OutcomeState, RecallResult, SignalScores
from .text import (
    best_window,
    fuzzy_ratio,
    recall_snippet,
    sanitize_label,
    signature_of,
    token_variants,
    word_trigrams,
)

#: Tier names, in descending strength.  Only the strongest tier per term counts,
#: so one term can never double-count (audit matrix #2).
TIER_EXACT = "EXACT"
TIER_VARIANT = "VARIANT"
TIER_PREFIX = "PREFIX"
TIER_DIFFLIB = "DIFFLIB"
TIER_SUBSTRING = "SUBSTRING"
TIER_TYPO = "TYPO"
TIER_NONE = "NONE"

#: Tier magnitudes.  The first four are v0.1's own values from
#: ``Vault._fuzzy_token_score`` (1.0 exact, 0.90 variant, 0.86 prefix,
#: ``min(ratio*0.84, 0.82)`` difflib).  The last two are Recall V2 additions:
#: Graphify has a substring tier that v0.1 lacks, and a typo tier makes
#: single-character spelling differences explorable without difflib.
TIER_VALUES = {
    TIER_EXACT: 1.00,
    TIER_VARIANT: 0.90,
    TIER_PREFIX: 0.86,
    TIER_SUBSTRING: 0.70,
    TIER_TYPO: 0.55,
    TIER_NONE: 0.0,
}

#: Bounds on the expensive fuzzy pass.
MAX_FUZZY_TOKEN_COMPARISONS = 32
FUZZY_LENGTH_TOLERANCE = 2

#: v0.1's ``matchReason`` vocabulary, preserved so the UI stays coherent across
#: the frozen ``search()`` path and the new recall path.
REASON_EXACT = "Exact text"
REASON_TITLE = "Title match"
REASON_TAG = "Tag match"
REASON_FUZZY = "Fuzzy recall"
REASON_KEYWORD_FUZZY = "Keyword + fuzzy"
REASON_KEYWORD = "Keyword match"
REASON_CONNECTION = "Connected thought"
REASON_NONE = "No match"


@dataclass
class Candidate:
    """One bounded candidate: derived metadata + canonical text + token sets."""

    thought_id: str
    title: str = ""
    original_text: str = ""
    current_text: str = ""
    type: str = "THOUGHT"
    status: str = "ACTIVE"
    created_at: str = ""
    updated_at: str = ""
    graveyard_at: str | None = None
    resurrected_at: str | None = None
    is_demo: bool = False
    degree: int = 0
    tags: tuple[str, ...] = ()
    trigram_similarity: float = 0.0
    trigram_shared: int = 0
    trigram_dot: float = 0.0
    lexical_matched_terms: tuple[str, ...] = ()
    lexical_matched_fields: tuple[str, ...] = ()
    lexical_idf_sum: float = 0.0
    lexical_weighted_tf: float = 0.0
    cheap_score: float = 0.0
    _tokens: dict[str, Any] = field(default_factory=dict, repr=False)

    # --- lazily computed token sets -----------------------------------
    @property
    def display_title(self) -> str:
        return _display_title(self.title, self.current_text or self.original_text)

    @property
    def signature(self) -> str:
        return signature_of(self.title, self.current_text or self.original_text)

    def token_set(self, field_name: str) -> frozenset[str]:
        cached = self._tokens.get(field_name)
        if cached is None:
            source = {
                "title": self.title,
                "body": f"{self.original_text}\n{self.current_text}",
                "tag": " ".join(self.tags),
                "all": f"{self.title}\n{self.original_text}\n{self.current_text}\n{' '.join(self.tags)}",
            }[field_name]
            forms: set[str] = set()
            for token in tokenize(source, remove_stopwords=False):
                forms.add(token)
            cached = frozenset(forms)
            self._tokens[field_name] = cached
        return cached

    def variant_set(self, field_name: str) -> frozenset[str]:
        key = f"{field_name}:variants"
        cached = self._tokens.get(key)
        if cached is None:
            forms: set[str] = set()
            for token in self.token_set(field_name):
                forms |= token_variants(token)
            cached = frozenset(forms)
            self._tokens[key] = cached
        return cached

    def tokens_by_length(self, field_name: str) -> dict[int, tuple[str, ...]]:
        key = f"{field_name}:bylen"
        cached = self._tokens.get(key)
        if cached is None:
            buckets: dict[int, list[str]] = {}
            for token in sorted(self.token_set(field_name)):
                buckets.setdefault(len(token), []).append(token)
            cached = {length: tuple(sorted(tokens)) for length, tokens in buckets.items()}
            self._tokens[key] = cached
        return cached

    @property
    def original_norm(self) -> str:
        cached = self._tokens.get("original_norm")
        if cached is None:
            cached = normalize_text(self.original_text)
            self._tokens["original_norm"] = cached
        return cached

    @property
    def current_norm(self) -> str:
        cached = self._tokens.get("current_norm")
        if cached is None:
            cached = normalize_text(self.current_text)
            self._tokens["current_norm"] = cached
        return cached

    @property
    def title_norm(self) -> str:
        cached = self._tokens.get("title_norm")
        if cached is None:
            cached = normalize_text(self.title)
            self._tokens["title_norm"] = cached
        return cached


def load_candidates(vault: Vault, thought_ids: Sequence[str]) -> dict[str, Candidate]:
    """Read canonical text for a **bounded** id set.  No full-corpus scan."""
    ids = list(dict.fromkeys(thought_ids))
    if not ids:
        return {}
    out: dict[str, Candidate] = {}
    with vault._connection() as conn:
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            for row in conn.execute(
                f"SELECT t.*, COALESCE(d.degree,0) AS degree, COALESCE(d.trigram_norm,0) AS tnorm "
                f"FROM thoughts t LEFT JOIN recall_docs d ON d.thought_id = t.id "
                f"WHERE t.id IN ({marks})",
                chunk,
            ):
                out[row["id"]] = Candidate(
                    thought_id=row["id"],
                    title=row["title"] or "",
                    original_text=row["original_text"],
                    current_text=row["current_text"],
                    type=row["type"],
                    status=row["status"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    graveyard_at=row["graveyard_at"],
                    resurrected_at=row["resurrected_at"],
                    is_demo=bool(row["is_demo"]),
                    degree=int(row["degree"] or 0),
                )
            for row in conn.execute(
                f"SELECT tt.thought_id AS thought_id, t.name AS name FROM thought_tags tt "
                f"JOIN tags t ON t.id = tt.tag_id WHERE tt.thought_id IN ({marks}) "
                f"ORDER BY tt.thought_id, t.normalized_name",
                chunk,
            ):
                candidate = out.get(row["thought_id"])
                if candidate is not None:
                    candidate.tags = candidate.tags + (row["name"],)
    return out


# --------------------------------------------------------------------------
# tier evaluation
# --------------------------------------------------------------------------


def term_tier(
    term: str,
    candidate: Candidate,
    *,
    field_name: str = "all",
    allow_fuzzy: bool = False,
    config: RecallConfig = DEFAULT_CONFIG,
) -> tuple[str, float, str]:
    """Strongest lexical tier at which ``term`` matches a document field.

    Returns ``(tier_name, tier_value, matched_form)``.  Deterministic: the cheap
    set-based tiers are tried first, and the ``difflib`` tier is only attempted
    when ``allow_fuzzy`` is true (the engine sets that only for the bounded
    fuzzy-pass candidate set).
    """
    tokens = candidate.token_set(field_name)
    if not tokens or not term:
        return TIER_NONE, 0.0, ""

    if term in tokens:
        return TIER_EXACT, TIER_VALUES[TIER_EXACT], term

    variants = token_variants(term)
    variant_hit = sorted((variants - {term}) & tokens)
    if variant_hit:
        return TIER_VARIANT, TIER_VALUES[TIER_VARIANT], variant_hit[0]

    variants_sorted = sorted(variants)
    if len(term) >= config.tier_min_token_length:
        prefixes = sorted(
            token
            for token in tokens
            if len(token) >= config.tier_min_token_length
            and (token.startswith(term) or term.startswith(token))
        )
        if prefixes:
            return TIER_PREFIX, TIER_VALUES[TIER_PREFIX], prefixes[0]
        substrings = sorted(
            token
            for token in tokens
            if len(token) >= config.tier_min_token_length
            and (term in token or token in term)
        )
        if substrings:
            return TIER_SUBSTRING, TIER_VALUES[TIER_SUBSTRING], substrings[0]

    # Typo tier: character-trigram overlap with a single document token.  Cheap,
    # bounded, and it is what makes "adaptng enemies" find "adapting enemies".
    if len(term) >= 5:
        query_grams = word_trigrams(term)
        best_form = ""
        best_similarity = 0.0
        buckets = candidate.tokens_by_length(field_name)
        compared = 0
        for length in range(
            max(config.tier_min_token_length, len(term) - FUZZY_LENGTH_TOLERANCE),
            len(term) + FUZZY_LENGTH_TOLERANCE + 1,
        ):
            for token in buckets.get(length, ()):
                compared += 1
                if compared > MAX_FUZZY_TOKEN_COMPARISONS:
                    break
                grams = word_trigrams(token)
                union = len(query_grams | grams)
                similarity = len(query_grams & grams) / union if union else 0.0
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_form = token
            if compared > MAX_FUZZY_TOKEN_COMPARISONS:
                break
        if best_similarity >= 0.55 and best_form:
            return TIER_TYPO, TIER_VALUES[TIER_TYPO] * best_similarity, best_form

    if allow_fuzzy and len(term) >= config.tier_min_token_length:
        best_form = ""
        best_ratio = 0.0
        buckets = candidate.tokens_by_length(field_name)
        compared = 0
        for variant in variants_sorted:
            if len(variant) < config.tier_min_token_length:
                continue
            for length in range(
                max(config.tier_min_token_length, len(variant) - FUZZY_LENGTH_TOLERANCE),
                len(variant) + FUZZY_LENGTH_TOLERANCE + 1,
            ):
                for token in buckets.get(length, ()):
                    compared += 1
                    if compared > MAX_FUZZY_TOKEN_COMPARISONS:
                        break
                    ratio = SequenceMatcher(None, variant, token, autojunk=False).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_form = token
                if compared > MAX_FUZZY_TOKEN_COMPARISONS:
                    break
            if compared > MAX_FUZZY_TOKEN_COMPARISONS:
                break
        if best_ratio >= config.fuzzy_token_threshold and best_form:
            return TIER_DIFFLIB, min(best_ratio * 0.84, 0.82), best_form

    return TIER_NONE, 0.0, ""


# --------------------------------------------------------------------------
# signal computation
# --------------------------------------------------------------------------


def exact_phrase_signal(analysis: Any, candidate: Candidate) -> tuple[float, str, tuple[str, ...]]:
    """Strongest exact-phrase containment, and where it was found."""
    best = 0.0
    where = ""
    hits: list[str] = []
    for phrase in analysis.phrase_candidates:
        if len(phrase) < 4:
            continue
        if phrase in candidate.original_norm:
            hits.append("original")
            if best < 1.0:
                best, where = 1.0, "original_text"
        if phrase in candidate.current_norm:
            hits.append("current")
            if best < 1.0:
                best, where = 1.0, "current_text"
        if phrase in candidate.title_norm:
            hits.append("title")
            if best < 0.85:
                best, where = 0.85, "title"
        tag_norm = normalize_text(" ".join(candidate.tags))
        if tag_norm and phrase in tag_norm:
            hits.append("tag")
            if best < 0.7:
                best, where = 0.7, "tags"
    return best, where, tuple(dict.fromkeys(hits))


def score_candidate(
    analysis: Any,
    candidate: Candidate,
    *,
    idf_map: dict[str, float],
    config: RecallConfig = DEFAULT_CONFIG,
    allow_fuzzy: bool = False,
    graph_signal: dict[str, Any] | None = None,
    temporal_signal: float = 0.0,
    temporal_label: str = "",
    lifecycle_signal: float = 0.0,
    memory_signal: dict[str, Any] | None = None,
) -> tuple[float, SignalScores]:
    """Compute every signal and the weighted total for one candidate."""
    terms = analysis.content_terms or analysis.terms
    total_idf = sum(idf_map.get(term.term, 0.0) for term in terms) or 1.0

    tier_hits: list[tuple[str, str, str]] = []
    idf_terms: list[tuple[str, float, float]] = []
    matched: list[str] = []
    missing: list[str] = []
    weighted_sum = 0.0
    title_best = 0.0
    tag_best = 0.0
    tag_hits: list[str] = []

    for term in terms:
        tier, value, form = term_tier(
            term.term, candidate, field_name="all", allow_fuzzy=allow_fuzzy, config=config
        )
        weight = idf_map.get(term.term, 0.0)
        idf_terms.append((term.term, weight, value))
        if value > 0:
            matched.append(term.term)
            tier_hits.append((term.term, tier, form))
            weighted_sum += value * weight
        else:
            missing.append(term.term)
        title_tier, title_value, _ = term_tier(
            term.term, candidate, field_name="title", allow_fuzzy=False, config=config
        )
        if title_value > title_best:
            title_best = title_value
        tag_tier, tag_value, tag_form = term_tier(
            term.term, candidate, field_name="tag", allow_fuzzy=False, config=config
        )
        if tag_value > tag_best:
            tag_best = tag_value
        if tag_value > 0:
            tag_hits.append(tag_form or term.term)

    n_terms = max(1, len(terms))
    coverage_ratio = len(matched) / n_terms
    coverage = coverage_ratio ** config.coverage_exponent
    idf_weighted = weighted_sum / total_idf

    phrase_value, phrase_where, phrase_hits = exact_phrase_signal(analysis, candidate)

    trigram = candidate.trigram_similarity
    if trigram < config.trigram_min_similarity:
        trigram = 0.0

    fuzzy = 0.0
    fuzzy_window = ""
    if allow_fuzzy:
        for source in (candidate.original_text, candidate.current_text, candidate.title):
            if not source:
                continue
            window = best_window(source, analysis.effective or analysis.raw)
            if not window:
                continue
            ratio = fuzzy_ratio(analysis.effective or analysis.raw, window)
            if ratio > fuzzy:
                fuzzy = ratio
                fuzzy_window = window
        if fuzzy < config.fuzzy_min_similarity:
            fuzzy = 0.0
            fuzzy_window = ""

    graph_contribution = 0.0
    graph_fields: dict[str, Any] = {}
    if graph_signal:
        graph_contribution = float(graph_signal.get("contribution", 0.0))
        graph_fields = {
            "graph_origin": graph_signal.get("origin", ""),
            "graph_depth": int(graph_signal.get("depth", 0)),
            "graph_edge_type": graph_signal.get("relType", ""),
            "graph_direction": graph_signal.get("direction", ""),
            "graph_label": graph_signal.get("label", ""),
            "graph_provenance": graph_signal.get("provenance", ""),
            "graph_status": graph_signal.get("status", ""),
            "graph_hub_factor": float(graph_signal.get("hubFactor", 1.0)),
            "graph_path": tuple(graph_signal.get("path", ())),
        }

    memory_value = 0.0
    memory_fields: dict[str, Any] = {}
    if memory_signal:
        memory_value = float(memory_signal.get("value", 0.0))
        memory_fields = {
            "query_memory_events": int(memory_signal.get("events", 0)),
            "query_memory_corroborated": bool(memory_signal.get("corroborated", False)),
            "query_memory_detail": tuple(memory_signal.get("detail", ())),
        }

    signals = SignalScores(
        exact_phrase=phrase_value,
        exact_phrase_where=phrase_where,
        token_overlap=coverage_ratio,
        idf_weighted=idf_weighted,
        idf_terms=tuple(idf_terms),
        coverage=coverage,
        coverage_matched=tuple(matched),
        coverage_missing=tuple(missing),
        coverage_ratio=coverage_ratio,
        trigram=trigram,
        trigram_shared=candidate.trigram_shared,
        trigram_dot=candidate.trigram_dot,
        fuzzy=fuzzy,
        fuzzy_window=sanitize_label(fuzzy_window, 240),
        title=title_best,
        tag=tag_best,
        tag_hits=tuple(dict.fromkeys(tag_hits)),
        graph=graph_contribution,
        temporal=temporal_signal,
        temporal_label=temporal_label,
        lifecycle=lifecycle_signal,
        query_memory=memory_value,
        tier_hits=tuple(tier_hits),
        **graph_fields,
        **memory_fields,
    )

    score = (
        config.w_exact_phrase * signals.exact_phrase
        + config.w_idf * signals.idf_weighted
        + config.w_coverage * signals.coverage
        + config.w_trigram * signals.trigram
        + config.w_fuzzy * signals.fuzzy
        + config.w_token_overlap * signals.token_overlap
        + config.w_title * signals.title
        + config.w_tag * signals.tag
        + config.w_graph * signals.graph
        + config.w_temporal * signals.temporal
        + config.w_lifecycle * signals.lifecycle
        + config.w_query_memory * signals.query_memory
    )
    return round(score, 6), signals


def cheap_score(
    analysis: Any, candidate: Candidate, idf_map: dict[str, float], config: RecallConfig
) -> float:
    """Fuzzy-pass selection score: no ``difflib``, no canonical text comparison.

    Used only to decide *which* bounded candidates earn the expensive pass, and
    ordered deterministically.  It never appears in a final score.
    """
    terms = analysis.content_terms or analysis.terms
    total = 0.0
    for term in terms:
        _tier, value, _form = term_tier(
            term.term, candidate, field_name="all", allow_fuzzy=False, config=config
        )
        total += value * idf_map.get(term.term, 1.0)
    return total + candidate.trigram_similarity * 2.0


def match_reason(signals: SignalScores) -> str:
    """v0.1-compatible ``matchReason`` vocabulary, derived from V2 signals."""
    tiers = {tier for _term, tier, _form in signals.tier_hits}
    fuzzy_tiers = {TIER_DIFFLIB, TIER_TYPO, TIER_SUBSTRING}
    if signals.exact_phrase >= 1.0:
        return REASON_EXACT
    if signals.graph > 0 and not tiers:
        return REASON_CONNECTION
    if signals.title >= TIER_VALUES[TIER_VARIANT] and signals.title >= signals.tag:
        return REASON_TITLE
    if signals.tag >= TIER_VALUES[TIER_VARIANT]:
        return REASON_TAG
    if tiers & fuzzy_tiers:
        return REASON_KEYWORD_FUZZY if (tiers & {TIER_EXACT, TIER_VARIANT, TIER_PREFIX}) else REASON_FUZZY
    if tiers:
        return REASON_KEYWORD
    if signals.trigram > 0 or signals.fuzzy > 0:
        return REASON_FUZZY
    return REASON_KEYWORD if signals.coverage_ratio > 0 else REASON_NONE


# --------------------------------------------------------------------------
# explanations
# --------------------------------------------------------------------------


def build_explanations(
    analysis: Any, candidate: Candidate, signals: SignalScores, score: float, config: RecallConfig
) -> tuple[Explanation, ...]:
    """Structured reason codes + evidence, ordered by contribution.

    The audit's ADAPT #18: store reason **codes**, render prose in the UI.  Prose
    alone cannot be audited, tested, or re-rendered.
    """
    out: list[Explanation] = []
    add = out.append

    if signals.exact_phrase >= 1.0:
        add(Explanation(
            "EXACT_PHRASE",
            contribution=config.w_exact_phrase * signals.exact_phrase,
            detail=f"Your phrase appears verbatim in the {signals.exact_phrase_where.replace('_', ' ')}.",
            evidence={"where": signals.exact_phrase_where},
        ))
    elif signals.exact_phrase > 0:
        add(Explanation(
            "EXACT_PHRASE_TITLE" if signals.exact_phrase_where == "title" else "EXACT_PHRASE",
            contribution=config.w_exact_phrase * signals.exact_phrase,
            detail=f"Your phrase appears in the {signals.exact_phrase_where}.",
            evidence={"where": signals.exact_phrase_where},
        ))

    rare = [
        (term, weight, tier)
        for term, weight, tier in signals.idf_terms
        if tier > 0 and weight >= 1.5
    ]
    if rare:
        add(Explanation(
            "RARE_TERM",
            contribution=config.w_idf * signals.idf_weighted,
            detail="Matched on distinctive terms: "
            + ", ".join(f"“{term}”" for term, _w, _t in sorted(rare, key=lambda x: -x[1])[:4])
            + ".",
            evidence={"terms": [[t, round(w, 4)] for t, w, _ in rare]},
        ))
    elif signals.idf_weighted > 0:
        add(Explanation(
            "STRONG_KEYWORD",
            contribution=config.w_idf * signals.idf_weighted,
            detail="Matched on query keywords, weighted by how distinctive each one is.",
            evidence={"weighted": round(signals.idf_weighted, 4)},
        ))

    if signals.coverage_ratio >= 0.75 and len(signals.coverage_matched) > 1:
        add(Explanation(
            "QUERY_COVERAGE",
            contribution=config.w_coverage * signals.coverage,
            detail=f"Covers {len(signals.coverage_matched)}/{analysis.n_terms} of your query terms.",
            evidence={
                "matched": list(signals.coverage_matched),
                "missing": list(signals.coverage_missing),
                "ratio": round(signals.coverage_ratio, 4),
                "exponent": config.coverage_exponent,
            },
        ))
    elif 0 < signals.coverage_ratio < 0.75:
        add(Explanation(
            "PARTIAL_COVERAGE",
            contribution=config.w_coverage * signals.coverage,
            detail=f"Covers {len(signals.coverage_matched)}/{analysis.n_terms} of your query terms"
            + (f"; missing {', '.join(signals.coverage_missing[:4])}." if signals.coverage_missing else "."),
            evidence={
                "matched": list(signals.coverage_matched),
                "missing": list(signals.coverage_missing),
                "ratio": round(signals.coverage_ratio, 4),
            },
        ))

    typo_hits = [(term, form) for term, tier, form in signals.tier_hits if tier in (TIER_TYPO, TIER_DIFFLIB)]
    if typo_hits:
        add(Explanation(
            "TYPO_TOLERATED",
            detail="Matched despite spelling differences: "
            + ", ".join(f"“{term}” ≈ “{form}”" for term, form in typo_hits[:3])
            + ".",
            evidence={"pairs": [[term, form] for term, form in typo_hits]},
        ))
    alias_hits = [(term, form) for term, tier, form in signals.tier_hits if tier == TIER_VARIANT]
    if alias_hits:
        add(Explanation(
            "MORPHOLOGY_MATCH",
            detail="Matched a different word form or a known alias: "
            + ", ".join(f"“{term}” → “{form}”" for term, form in alias_hits[:3])
            + ".",
            evidence={"pairs": [[term, form] for term, form in alias_hits]},
        ))
    if signals.trigram > 0 and not typo_hits:
        add(Explanation(
            "TRIGRAM",
            contribution=config.w_trigram * signals.trigram,
            detail=f"Character-level spelling similarity {signals.trigram:.2f} "
                   f"({signals.trigram_shared} shared trigrams).",
            evidence={"similarity": round(signals.trigram, 4), "shared": signals.trigram_shared},
        ))
    if signals.fuzzy > 0:
        add(Explanation(
            "FUZZY_TEXT",
            contribution=config.w_fuzzy * signals.fuzzy,
            detail=f"Closest wording similarity {signals.fuzzy:.2f} against: "
                   f"“{sanitize_label(signals.fuzzy_window, 120)}”.",
            evidence={"ratio": round(signals.fuzzy, 4), "window": signals.fuzzy_window},
        ))
    if signals.title > 0:
        add(Explanation(
            "TITLE_MATCH",
            contribution=config.w_title * signals.title,
            detail=f"Title matched at strength {signals.title:.2f}.",
            evidence={"strength": round(signals.title, 4)},
        ))
    if signals.tag > 0:
        add(Explanation(
            "TAG_MATCH",
            contribution=config.w_tag * signals.tag,
            detail="Tag match: " + ", ".join(signals.tag_hits[:4]) + ".",
            evidence={"tags": list(signals.tag_hits)},
        ))

    if signals.graph > 0:
        confirmed = signals.graph_status == "CONFIRMED"
        code = "RELATED_CONFIRMED" if confirmed else "RELATED_SUGGESTED"
        if signals.graph_depth > 1:
            code = "MULTI_HOP"
        detail = (
            f"{signals.graph_depth} hop{'s' if signals.graph_depth > 1 else ''} from "
            f"“{sanitize_label(signals.graph_origin, 60)}” — {signals.graph_label} "
            f"({signals.graph_provenance.replace('_', ' ').title()}, "
            f"{signals.graph_status.title()})."
        )
        add(Explanation(
            code,
            contribution=config.w_graph * signals.graph,
            detail=detail,
            evidence={
                "depth": signals.graph_depth,
                "direction": signals.graph_direction,
                "relType": signals.graph_edge_type,
                "provenance": signals.graph_provenance,
                "status": signals.graph_status,
                "hubFactor": round(signals.graph_hub_factor, 4),
                "path": list(signals.graph_path),
            },
        ))
        if signals.graph_edge_type in ("IMPLEMENTED_BY", "LED_TO") and not confirmed:
            pass
        if signals.graph_edge_type == "IMPLEMENTED_BY":
            add(Explanation(
                "CONNECTED_TO_PROJECT",
                detail="This thought is connected to something that was built.",
                evidence={"relType": signals.graph_edge_type},
            ))

    if signals.temporal > 0:
        add(Explanation(
            "TIME_WINDOW" if signals.temporal >= 1.0 else "TIME_HINT",
            contribution=config.w_temporal * signals.temporal,
            detail=f"Created {signals.temporal_label}." if signals.temporal_label
            else "Inside the requested time window.",
            evidence={"label": signals.temporal_label},
        ))
    if signals.lifecycle > 0:
        add(Explanation(
            "LIFECYCLE_MATCH",
            contribution=config.w_lifecycle * signals.lifecycle,
            detail=f"Lifecycle state {candidate.type} matches what you asked for.",
            evidence={"type": candidate.type},
        ))
    if signals.query_memory > 0:
        add(Explanation(
            "PREVIOUSLY_USEFUL",
            contribution=config.w_query_memory * signals.query_memory,
            detail=(
                f"You marked this useful {signals.query_memory_events} time"
                f"{'s' if signals.query_memory_events != 1 else ''} for a similar query"
                + ("" if signals.query_memory_corroborated else " (uncorroborated, so discounted)")
                + ". Decays over time."
            ),
            evidence={
                "events": signals.query_memory_events,
                "corroborated": signals.query_memory_corroborated,
                "detail": list(signals.query_memory_detail),
            },
        ))
    elif signals.query_memory < 0:
        add(Explanation(
            "PREVIOUSLY_NOT_USEFUL",
            contribution=config.w_query_memory * signals.query_memory,
            detail=f"You marked this not useful {signals.query_memory_events} time"
                   f"{'s' if signals.query_memory_events != 1 else ''} for a similar query. "
                   f"It is demoted, never hidden, and the penalty decays.",
            evidence={"events": signals.query_memory_events, "detail": list(signals.query_memory_detail)},
        ))
    if candidate.status == "GRAVEYARD":
        add(Explanation(
            "GRAVEYARD",
            detail="This thought is in the Graveyard. It is annotated, not hidden.",
            evidence={"graveyardAt": candidate.graveyard_at},
        ))
    if candidate.resurrected_at:
        add(Explanation(
            "RESURRECTED",
            detail=f"Buried {candidate.graveyard_at or 'at some point'} and resurrected "
                   f"{candidate.resurrected_at}.",
            evidence={"graveyardAt": candidate.graveyard_at, "resurrectedAt": candidate.resurrected_at},
        ))
    if "original" in signals.exact_phrase_where:
        add(Explanation(
            "ORIGINAL_WORDING",
            detail="Found in your original wording — what you actually wrote.",
            evidence={},
        ))

    out.sort(key=lambda item: (-item.contribution, item.code))
    return tuple(out)


def rival_differences(results: Sequence[RecallResult]) -> dict[str, tuple[str, ...]]:
    """For each ambiguous candidate, the query terms that distinguish it.

    This is the substance of "refuse with an explanation rather than guess":
    when IWTT will not pick one, it says what makes each one different.
    """
    term_sets = {r.thought_id: set(r.signals.coverage_matched) for r in results}
    out: dict[str, tuple[str, ...]] = {}
    for result in results:
        others: set[str] = set()
        for other_id, other_terms in term_sets.items():
            if other_id != result.thought_id:
                others |= other_terms
        unique = sorted(term_sets[result.thought_id] - others)
        descriptors: list[str] = []
        if unique:
            descriptors.append("only match for: " + ", ".join(unique[:4]))
        descriptors.append(result.type.title())
        if result.status == "GRAVEYARD":
            descriptors.append("in Graveyard")
        if result.resurrected_at:
            descriptors.append("resurrected")
        descriptors.append(result.created_at[:10])
        out[result.thought_id] = tuple(descriptors)
    return out


# --------------------------------------------------------------------------
# deterministic ranking
# --------------------------------------------------------------------------


def sort_key(result: RecallResult) -> tuple[Any, ...]:
    """The documented total order.

        score DESC → trigram DESC → fuzzy DESC → created_at ASC → thought_id ASC

    The final component is a durable UUID, so the order is **total**: no two
    results can ever compare equal, and database row order can never decide a
    ranking.  This is the specific v0.1 weakness being fixed — v0.1 sorts by
    ``(score, updatedAt)`` descending and leaves true ties to row order.
    """
    return (
        -result.score,
        -result.signals.trigram,
        -result.signals.fuzzy,
        result.created_at,
        result.thought_id,
    )


def assign_states(
    results: list[RecallResult], config: RecallConfig
) -> tuple[str, str, dict[str, Any]]:
    """Assign per-result and whole-outcome states, plus the ambiguity record.

    Rule (fixed thresholds, no probability claims):

        top     = highest score
        band    = results scoring >= ambiguity_ratio · top
        NO_MATCH      if there are no results, or top < min_score
        AMBIGUOUS     if |band| >= ambiguity_min_candidates
        STRONG_MATCH  if top >= strong_min_score
        POSSIBLE_MATCH otherwise

    An AMBIGUOUS outcome still returns every candidate — refusing to guess is
    not the same as refusing to help.
    """
    if not results:
        return OutcomeState.NO_MATCH, "Nothing in the vault plausibly matches that fragment.", {
            "ambiguous": False, "bandSize": 0, "topScore": 0.0, "ratio": config.ambiguity_ratio,
        }
    top = results[0].score
    if top < config.min_score:
        for result in results:
            result.state = MatchState.POSSIBLE
        return OutcomeState.NO_MATCH, (
            f"The best candidate scored {top:.2f}, below the {config.min_score:.2f} threshold "
            f"IWTT needs before it will claim a match. Nothing was hidden — the candidates are "
            f"listed below."
        ), {
            "ambiguous": False, "bandSize": 0, "topScore": round(top, 4),
            "minScore": config.min_score, "ratio": config.ambiguity_ratio,
            "belowThreshold": True,
        }

    threshold = config.ambiguity_ratio * top
    band = [result for result in results if result.score >= threshold]
    # Two independent triggers, both deterministic:
    #   (a) enough candidates sit inside the ambiguity band; or
    #   (b) the top scores are EXACTLY tied, in which case IWTT genuinely cannot
    #       choose and must say so rather than letting a tie-break UUID masquerade
    #       as confidence.  The tie-break still fixes the display order — it only
    #       stops being reported as a decision.
    tied = [result for result in results if abs(result.score - top) <= 1e-9]
    ambiguous = len(band) >= config.ambiguity_min_candidates or len(tied) >= config.ambiguity_exact_tie_min
    band_ids = {result.thought_id for result in band} | {result.thought_id for result in tied}

    for result in results:
        if ambiguous and result.thought_id in band_ids:
            result.state = MatchState.AMBIGUOUS
        elif result.score >= config.strong_min_score:
            result.state = MatchState.STRONG
        else:
            result.state = MatchState.POSSIBLE

    if ambiguous:
        differences = rival_differences(band)
        for result in band:
            result.rivals = differences.get(result.thought_id, ())
        band_trigger = len(band) >= config.ambiguity_min_candidates
        tie_trigger = len(tied) >= config.ambiguity_exact_tie_min
        if tie_trigger and not band_trigger:
            message = (
                f"I found {len(tied)} equally likely memories — they scored exactly the same "
                f"({top:.2f}). IWTT will not pick one for you, because the order you see is only "
                f"a tie-break, not a judgement. Each candidate below shows what distinguishes it."
            )
            trigger = "EXACT_TIE"
        else:
            message = (
                f"Multiple possible thoughts found — {len(band)} candidates score within "
                f"{int((1 - config.ambiguity_ratio) * 100)}% of the top result. IWTT will not pick "
                f"one for you. Each candidate below shows what distinguishes it."
            )
            trigger = "AMBIGUITY_BAND" if band_trigger else "BOTH"
        return OutcomeState.AMBIGUOUS, message, {
            "ambiguous": True,
            "trigger": trigger,
            "bandSize": len(band),
            "tiedCount": len(tied),
            "bandThreshold": round(threshold, 4),
            "topScore": round(top, 4),
            "secondScore": round(results[1].score, 4) if len(results) > 1 else 0.0,
            "ratio": config.ambiguity_ratio,
            "minCandidates": config.ambiguity_min_candidates,
            "exactTieMin": config.ambiguity_exact_tie_min,
            "bandIds": [result.thought_id for result in band],
            "differences": {key: list(value) for key, value in differences.items()},
            "refusal": "IWTT declines to declare one authoritative answer.",
            "orderingIsTieBreakNotJudgement": tie_trigger and not band_trigger,
        }

    if top >= config.strong_min_score:
        return OutcomeState.STRONG, (
            f"Strong match (score {top:.2f} of a possible {config.theoretical_max_score():.2f}). "
            f"The result below leads to what you actually wrote."
        ), {
            "ambiguous": False, "bandSize": len(band), "topScore": round(top, 4),
            "secondScore": round(results[1].score, 4) if len(results) > 1 else 0.0,
            "ratio": config.ambiguity_ratio, "strongMinScore": config.strong_min_score,
        }

    return OutcomeState.POSSIBLE, (
        f"Possible match (score {top:.2f}). The evidence is real but not strong enough for IWTT "
        f"to call it certain — check the explanation on each result."
    ), {
        "ambiguous": False, "bandSize": len(band), "topScore": round(top, 4),
        "secondScore": round(results[1].score, 4) if len(results) > 1 else 0.0,
        "ratio": config.ambiguity_ratio, "strongMinScore": config.strong_min_score,
    }


def make_result(
    analysis: Any,
    candidate: Candidate,
    score: float,
    signals: SignalScores,
    explanations: Sequence[Explanation],
    *,
    relationship_context: Any = None,
    cluster: dict[str, Any] | None = None,
) -> RecallResult:
    return RecallResult(
        thought_id=candidate.thought_id,
        title=candidate.title,
        display_title=candidate.display_title,
        snippet=recall_snippet(candidate.original_text, analysis.effective or analysis.raw),
        type=candidate.type,
        status=candidate.status,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
        tags=candidate.tags,
        score=round(score, 4),
        state=MatchState.POSSIBLE,
        match_reason=match_reason(signals),
        signals=signals,
        explanations=tuple(explanations),
        relationship_context=relationship_context,
        cluster=cluster,
        original_text=candidate.original_text,
        current_text=candidate.current_text,
        graveyard_at=candidate.graveyard_at,
        resurrected_at=candidate.resurrected_at,
        is_demo=candidate.is_demo,
        degree=candidate.degree,
        tie_break=(
            round(score, 4),
            round(signals.trigram, 6),
            round(signals.fuzzy, 6),
            candidate.created_at,
            candidate.thought_id,
        ),
    )
