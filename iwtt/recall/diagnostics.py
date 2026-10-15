"""Recall V2 — diagnostics.

A development-facing explanation view.  For a ``(query, result)`` pair it exposes
every intermediate value and the exact arithmetic that produced the final score,
so retrieval is debuggable rather than mysterious.

This is deliberately **not** the normal user surface.  The Recall UI shows a
compact ``why`` line plus a few explanation chips; the full breakdown lives here,
behind a disclosure, and in ``GET /api/recall/diagnostics``.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Sequence

from .config import RecallConfig
from .filters import RecallFilters
from .scoring import TIER_VALUES
from .text import normalize_text, token_variants, word_trigrams


def _round(value: Any, places: int = 6) -> Any:
    return round(value, places) if isinstance(value, float) else value


def diagnose(
    engine: Any,
    query: str,
    thought_id: str | None = None,
    *,
    filters: RecallFilters | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Full scoring breakdown for a query, optionally narrowed to one thought.

    Returns the arithmetic, not a narrative: every weighted term is shown as
    ``weight × signal = contribution``, and the total is the sum of the
    contributions actually applied.  If a stage did not run (for example the
    trigram guard tripped, or the candidate never earned the fuzzy pass), the
    breakdown says so instead of reporting a zero that looks like a measurement.
    """
    config: RecallConfig = engine.config
    filters = (filters or RecallFilters()).normalized()
    started = time.perf_counter()
    outcome = engine.recall(query, filters=filters, now=now, explain=True)

    analysis = outcome.analysis
    generation = outcome.honesty.get("candidateGeneration", {})
    results = outcome.results
    target = None
    if thought_id:
        target = next((r for r in results if r.thought_id == thought_id), None)
    else:
        target = results[0] if results else None

    breakdown: dict[str, Any] = {
        "query": {
            "raw": query,
            "normalized": analysis.get("normalized", normalize_text(query)),
            "effective": analysis.get("effective", ""),
            "memoryFillerStripped": analysis.get("fillerStripped", False),
            "contentTerms": analysis.get("contentTerms", []),
            "phraseCandidates": analysis.get("phraseCandidates", []),
            "trigramWords": analysis.get("trigramWords", []),
            "timeHint": analysis.get("timeHint"),
            "typeHints": analysis.get("typeHints", []),
            "graveyardHint": analysis.get("graveyardHint", False),
            "notes": analysis.get("notes", []),
        },
        "candidateGeneration": {
            "lexicalCandidates": generation.get("lexical", 0),
            "trigramCandidates": generation.get("trigram", 0),
            "unionSize": generation.get("union", 0),
            "examined": outcome.candidates_examined,
            "trigramIndexSelective": generation.get("trigramSelective", False),
            "trigramGuardTripped": generation.get("trigramGuardTripped", False),
            "guardExplanation": (
                "The rarest query trigram covers more than "
                f"{int(config.trigram_guard_fraction * 100)}% of the vault, so the trigram index is "
                "not selective and was NOT used to narrow candidates. The never-worse contract: "
                "the index may only speed retrieval up, never change its results."
                if generation.get("trigramGuardTripped")
                else "The trigram index was selective and narrowed the fuzzy candidate set."
            ),
            "fuzzyPassCandidates": outcome.honesty.get("fuzzyPassCandidates", 0),
            "fuzzyPassCap": outcome.honesty.get("fuzzyPassCap", config.fuzzy_scoring_cap),
            "fuzzyPassExplanation": (
                "difflib is only run on this many candidates, chosen deterministically as the "
                "union of the top half by base score and the top half by trigram similarity. "
                "Candidates outside the pass report fuzzy as NOT MEASURED rather than 0."
            ),
            "sqlPrefilter": outcome.honesty.get("candidateGeneration", {}),
        },
        "graph": outcome.graph,
        "scoringFormula": _formula(config),
        "weights": _weights(config),
        "tieBreak": {
            "order": list(config.tie_break),
            "explanation": (
                "score DESC, then trigram DESC, then fuzzy DESC, then created_at ASC, then the "
                "durable UUID ASC. The UUID makes this a TOTAL order, so no two results can tie "
                "and database row order can never decide a ranking."
            ),
        },
        "states": {
            "outcomeState": outcome.state,
            "message": outcome.message,
            "ambiguity": outcome.ambiguity,
            "thresholds": {
                "minScore": config.min_score,
                "strongMinScore": config.strong_min_score,
                "ambiguityRatio": config.ambiguity_ratio,
                "ambiguityMinCandidates": config.ambiguity_min_candidates,
                "theoreticalMaxScore": config.theoretical_max_score(),
            },
            "rule": (
                "NO_MATCH if no result or top < min_score; AMBIGUOUS if the number of results "
                "scoring >= ambiguity_ratio * top is >= ambiguity_min_candidates; STRONG_MATCH "
                "if top >= strong_min_score; otherwise POSSIBLE_MATCH."
            ),
        },
        "indexState": outcome.index_state,
        "timingsMs": outcome.timings_ms,
        "honesty": outcome.honesty,
        "durationMs": round((time.perf_counter() - started) * 1000, 3),
    }

    if target is not None:
        breakdown["result"] = _result_breakdown(engine, outcome, target, config, filters, now)
    elif thought_id:
        breakdown["result"] = {
            "thoughtId": thought_id,
            "present": False,
            "reason": (
                "This thought was not returned for that query with these filters. Either it never "
                "entered the bounded candidate set, or it scored below the reporting threshold."
            ),
            "candidateSetMembership": _membership_report(engine, outcome, thought_id, config),
        }
    return breakdown


def _formula(config: RecallConfig) -> str:
    parts = [
        f"{config.w_exact_phrase} × exact_phrase",
        f"{config.w_idf} × idf_weighted",
        f"{config.w_coverage} × coverage^? (coverage = matched/terms, exponent {config.coverage_exponent})",
        f"{config.w_trigram} × trigram_cosine",
        f"{config.w_fuzzy} × fuzzy_ratio",
        f"{config.w_token_overlap} × token_overlap",
        f"{config.w_title} × title_tier",
        f"{config.w_tag} × tag_tier",
        f"{config.w_graph} × graph_contribution (capped at {config.graph_max_contribution})",
        f"{config.w_temporal} × temporal",
        f"{config.w_lifecycle} × lifecycle",
        f"{config.w_query_memory} × query_memory (signed, clamped "
        f"[{config.query_memory_negative_cap}, {config.query_memory_positive_cap}])",
    ]
    return "score = " + "\n      + ".join(parts)


def _weights(config: RecallConfig) -> dict[str, Any]:
    return {
        "tierValues": dict(TIER_VALUES),
        "tierValuesNote": (
            "TYPO and DIFFLIB are not constants: TYPO is 0.55 × trigram similarity and "
            "DIFFLIB is min(ratio × 0.84, 0.82), both measured per candidate. The table "
            "shows 0.0 for them because no constant exists."
        ),
        "coverageExponent": config.coverage_exponent,
        "idfFormula": "idf(t) = log(1 + N / (1 + df(t)))",
        "provenanceWeights": {
            "USER_CONFIRMED + CONFIRMED": config.weight_user_confirmed,
            "AI_SUGGESTED + CONFIRMED": config.weight_ai_confirmed,
            "AI_SUGGESTED + SUGGESTED": config.weight_ai_suggested,
        },
        "graphFormula": (
            "contribution = seed_score × graph_edge_base × provenance_weight × type_weight "
            "× hub_factor(target) × reverse_factor × graph_depth_decay^(depth-1), "
            "capped at graph_max_contribution; multiple paths take the MAX, never the sum"
        ),
        "hubFormula": (
            "hub_factor(d) = 1.0 if d <= threshold else floor + (1-floor)·threshold/d, "
            f"threshold = max({config.hub_min_degree}, ceil(p{int(config.hub_percentile * 100)}(degree))), "
            f"floor = {config.hub_floor}"
        ),
        "queryMemoryFormula": (
            "value = clamp(Σ weight·0.5^(age_days/half_life) × gate, negative_cap, positive_cap), "
            f"half_life = {config.query_memory_half_life_days} days, gate = "
            f"{config.query_memory_single_observation_factor} when explicit events < "
            f"{config.query_memory_min_corroboration}"
        ),
    }


def _result_breakdown(
    engine: Any, outcome: Any, result: Any, config: RecallConfig,
    filters: RecallFilters, now: datetime | None,
) -> dict[str, Any]:
    signals = result.signals
    contributions = {
        "exactPhrase": _term(config.w_exact_phrase, signals.exact_phrase),
        "idfWeighted": _term(config.w_idf, signals.idf_weighted),
        "coverage": _term(config.w_coverage, signals.coverage),
        "trigram": _term(config.w_trigram, signals.trigram),
        "fuzzy": _term(config.w_fuzzy, signals.fuzzy),
        "tokenOverlap": _term(config.w_token_overlap, signals.token_overlap),
        "title": _term(config.w_title, signals.title),
        "tag": _term(config.w_tag, signals.tag),
        "graph": _term(config.w_graph, signals.graph),
        "temporal": _term(config.w_temporal, signals.temporal),
        "lifecycle": _term(config.w_lifecycle, signals.lifecycle),
        "queryMemory": _term(config.w_query_memory, signals.query_memory),
    }
    total = round(sum(item["contribution"] for item in contributions.values()), 6)

    # The idf signal is an idf-weighted MEAN of per-term tier values:
    #     idf_weighted = Σ(idf·tier_value) / Σ(idf)
    # so a term's share of that signal is idf·tier_value / total_idf, and the
    # contribution to the final score is w_idf × that share.  Reporting
    # idf·tier_value on its own would not sum to anything real.
    total_idf = sum(idf for _term, idf, _value in signals.idf_terms) or 1.0
    per_term = [
        {
            "term": term,
            "idf": _round(idf),
            "tier": tier,
            "matchedForm": form,
            "tierValue": _round(tier_value),
            "tierValueSource": (
                "measured" if tier in ("TYPO", "DIFFLIB") else "constant table"
            ),
            "weightedNumerator": _round(idf * tier_value),
            "shareOfIdfSignal": _round(idf * tier_value / total_idf),
            "contributionToScore": _round(config.w_idf * idf * tier_value / total_idf),
            "totalIdf": _round(total_idf),
        }
        for term, idf, tier_value in signals.idf_terms
        for tier, form in _tier_for(engine, outcome, result, term)
    ]

    return {
        "thoughtId": result.thought_id,
        "present": True,
        "displayTitle": result.display_title,
        "rank": result.rank,
        "state": result.state,
        "matchReason": result.match_reason,
        "score": result.score,
        "reportedTotal": total,
        "scoreDelta": round(result.score - total, 6),
        "contributions": contributions,
        "signals": signals.to_dict(),
        "perTerm": per_term,
        "idfSignal": {
            "value": _round(signals.idf_weighted),
            "formula": "Σ(idf·tierValue) / Σ(idf)",
            "totalIdf": _round(total_idf),
            "sumOfShares": _round(sum(item["shareOfIdfSignal"] for item in per_term)),
        },
        "coverage": {
            "matched": list(signals.coverage_matched),
            "missing": list(signals.coverage_missing),
            "ratio": _round(signals.coverage_ratio),
            "exponent": config.coverage_exponent,
            "scaled": _round(signals.coverage),
        },
        "trigram": {
            "cosine": _round(signals.trigram),
            "sharedTrigrams": signals.trigram_shared,
            "dot": _round(signals.trigram_dot),
            "minimumAccepted": config.trigram_min_similarity,
            "queryTrigramCount": len(_query_trigrams(outcome.effective_query)),
        },
        "fuzzy": {
            "ratio": _round(signals.fuzzy),
            "window": signals.fuzzy_window,
            "minimumAccepted": config.fuzzy_min_similarity,
            "measured": bool(signals.fuzzy_window) or signals.fuzzy > 0,
            "note": (
                "Measured." if (signals.fuzzy_window or signals.fuzzy > 0)
                else "NOT MEASURED — this candidate did not earn the bounded difflib pass, "
                     "so 0.0 here means 'not attempted', not 'no similarity'."
            ),
        },
        "graph": {
            "contribution": _round(signals.graph),
            "origin": signals.graph_origin,
            "depth": signals.graph_depth,
            "relType": signals.graph_edge_type,
            "direction": signals.graph_direction,
            "label": signals.graph_label,
            "provenance": signals.graph_provenance,
            "status": signals.graph_status,
            "hubFactor": _round(signals.graph_hub_factor),
            "path": list(signals.graph_path),
            "reachedByGraph": bool(signals.graph_origin),
        },
        "queryMemory": {
            "value": _round(signals.query_memory),
            "events": signals.query_memory_events,
            "corroborated": signals.query_memory_corroborated,
            "detail": list(signals.query_memory_detail),
            "halfLifeDays": config.query_memory_half_life_days,
        },
        "explanations": [e.to_dict() for e in result.explanations],
        "tieBreak": {
            "score": result.tie_break[0],
            "trigram": result.tie_break[1],
            "fuzzy": result.tie_break[2],
            "createdAt": result.tie_break[3],
            "thoughtId": result.tie_break[4],
        },
        "rivals": list(result.rivals),
        "canonical": {
            "type": result.type,
            "status": result.status,
            "createdAt": result.created_at,
            "updatedAt": result.updated_at,
            "graveyardAt": result.graveyard_at,
            "resurrectedAt": result.resurrected_at,
            "tags": list(result.tags),
            "degree": result.degree,
        },
    }


def _tier_for(engine: Any, outcome: Any, result: Any, term: str) -> list[tuple[str, str]]:
    tier = next((t for t in result.signals.tier_hits if t[0] == term), None)
    return [(tier[1], tier[2])] if tier else [("NONE", "")]


def _term(weight: float, signal: float) -> dict[str, Any]:
    return {
        "weight": weight,
        "signal": _round(signal),
        "contribution": _round(weight * signal),
    }


def _query_trigrams(text: str) -> set[str]:
    out: set[str] = set()
    for token in normalize_text(text).split(" "):
        if token:
            out |= word_trigrams(token)
    return out


def _membership_report(
    engine: Any, outcome: Any, thought_id: str, config: RecallConfig
) -> dict[str, Any]:
    """Why a specific thought never reached the candidate set."""
    from .analysis import analyze_query
    from .scoring import load_candidates

    analysis = analyze_query(outcome.query)
    loaded = load_candidates(engine.vault, [thought_id])
    candidate = loaded.get(thought_id)
    if candidate is None:
        return {"exists": False, "reason": "No such thought in the canonical vault."}
    index = engine.index
    with engine.vault._connection() as conn:
        indexed = bool(
            conn.execute(
                "SELECT 1 FROM recall_docs WHERE thought_id=?", (thought_id,)
            ).fetchone()
        )
        lexical = index.lexical_candidates(
            conn,
            [
                (term, form, 1.0)
                for term in analysis.term_strings
                for form in sorted(token_variants(term))
            ],
            limit=config.token_candidate_cap,
        )
        trigram, trigram_meta = index.trigram_candidates(
            conn, list(analysis.trigram_words), limit=config.trigram_candidate_cap
        )
    return {
        "exists": True,
        "indexed": indexed,
        "inLexicalCandidateSet": thought_id in lexical,
        "inTrigramCandidateSet": thought_id in trigram,
        "trigramSelective": trigram_meta.get("selective", False),
        "trigramGuardTripped": trigram_meta.get("guardTripped", False),
        "reason": (
            "Not indexed — the derived index is stale or empty."
            if not indexed
            else "Indexed but reached neither the lexical nor the trigram candidate set, so it was "
                 "never scored. With the current filters that is expected behaviour, not a loss: "
                 "the caps are documented constants."
            if (thought_id not in lexical and thought_id not in trigram)
            else "Entered the candidate set but did not survive filtering or scoring."
        ),
    }


def compare(engine: Any, query: str, thought_ids: Sequence[str], **kwargs: Any) -> dict[str, Any]:
    """Side-by-side breakdown for several thoughts — the ambiguity debug view."""
    outcome = engine.recall(query, explain=True, **kwargs)
    by_id = {result.thought_id: result for result in outcome.results}
    rows = []
    for thought_id in thought_ids:
        result = by_id.get(thought_id)
        if result is None:
            rows.append({"thoughtId": thought_id, "returned": False})
            continue
        rows.append({
            "thoughtId": thought_id,
            "returned": True,
            "rank": result.rank,
            "score": result.score,
            "state": result.state,
            "matchReason": result.match_reason,
            "coverage": list(result.signals.coverage_matched),
            "missing": list(result.signals.coverage_missing),
            "trigram": _round(result.signals.trigram),
            "fuzzy": _round(result.signals.fuzzy),
            "graph": _round(result.signals.graph),
            "memory": _round(result.signals.query_memory),
            "why": result.why(),
            "rivals": list(result.rivals),
        })
    return {
        "query": query,
        "state": outcome.state,
        "message": outcome.message,
        "ambiguity": outcome.ambiguity,
        "comparison": rows,
    }
