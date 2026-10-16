"""Recall V2 — the engine.

Pipeline (each stage is separately timed and reported in ``timingsMs``):

    0  ensure the derived index is fresh            (self-healing, bounded)
    1  analyze the query                            (filler strip, terms, hints)
    2  generate candidates                          (lexical ∪ trigram, capped SQL)
    3  apply hard filters                           (type / status / tag / time)
    4  base scoring pass                            (cheap tiers, no difflib)
    5  pick seeds + bounded graph expansion         (never replaces retrieval)
    6  bounded fuzzy pass                           (difflib, capped candidates)
    7  final scoring + temporal/lifecycle/memory
    8  deterministic ranking, states, explanations

GRAPH EXPANSION DOES NOT REPLACE PRIMARY RETRIEVAL.  Stage 5 can only *add*
candidates reachable from stage-4 seeds, and its contribution is hard-capped
below what a direct textual match scores.

NO AI, NO NETWORK, NO EMBEDDINGS.  Stage 5 of the audit's hypothetical pipeline
(optional semantic retrieval) is **not implemented** and the outcome says so
explicitly rather than implying understanding IWTT does not have.
"""
from __future__ import annotations

import dataclasses
import sqlite3
import time as time_module
from datetime import datetime, timezone
from typing import Any, Sequence

from ..store import Vault
from . import querymemory
from .analysis import QueryAnalysis, analyze_query
from .clusters import cluster_summary
from .config import DEFAULT_CONFIG, RecallConfig
from .expansion import expand_neighbourhood, pick_seeds, relationship_context_for
from .filters import (
    RecallFilters,
    TimeWindow,
    filter_report,
    matches_filters,
    parse_time_expression,
    rejection_reason,
    sql_prefilter,
)
from .graphview import build_graph_view_model
from .index import RecallIndex
from .paths import find_path, lineage
from .results import OutcomeState, RecallOutcome, RecallResult, RelationshipContext
from .scoring import (
    Candidate,
    assign_states,
    build_explanations,
    load_candidates,
    make_result,
    score_candidate,
    sort_key,
    term_tier,
)
from .text import normalize_text, sanitize_label

HONESTY_STATEMENT = {
    "embeddings": (
        "NOT IMPLEMENTED. IWTT v0.2 contains no embedding model, no vector index and no "
        "semantic stage. Retrieval is lexical + fuzzy + graph, and says so."
    ),
    "semanticUnderstanding": (
        "None claimed. Matches come from tokens, morphology/aliases, character trigrams, "
        "bounded difflib similarity and stored relationships."
    ),
    "ai": "Not used. Recall ran entirely offline against derived indexes.",
    "network": "None. No request left this process.",
    "probabilisticConfidence": (
        "Scores are deterministic weighted sums, not probabilities. Labels are assigned by "
        "fixed thresholds documented in RECALL_ENGINE.md."
    ),
}


class RecallEngine:
    """Deterministic, explainable recall over the canonical vault."""

    def __init__(
        self,
        vault: Vault,
        config: RecallConfig | None = None,
        index: RecallIndex | None = None,
    ) -> None:
        self.vault = vault
        self.config = config or DEFAULT_CONFIG
        self.index = index or RecallIndex(vault, self.config)

    # ------------------------------------------------------------------
    # main entry point
    # ------------------------------------------------------------------

    def recall(
        self,
        query: str,
        *,
        filters: RecallFilters | None = None,
        limit: int | None = None,
        include_graveyard: bool | None = None,
        types: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        tags: Sequence[str] | None = None,
        tag_mode: str | None = None,
        time: TimeWindow | None = None,
        time_expression: str | None = None,
        time_field: str | None = None,
        include_demo: bool | None = None,
        now: datetime | None = None,
        explain: bool = True,
        record_opened: bool = False,
        mode: str = "recall",
    ) -> RecallOutcome:
        """Run the pipeline.

        ``filters`` is the explicit object form; the keyword arguments are the
        ergonomic form used by ``Vault.recall()`` and the HTTP API.  Keywords win
        over a supplied ``filters`` object only where they are not ``None``, so a
        caller can adjust one dimension of a prebuilt filter set.

        ``include_graveyard=False`` is expressed as ``statuses=("ACTIVE",)`` —
        the Graveyard is a *canonical lifecycle state*, not a filter concept, and
        the engine never silently drops a Graveyard thought when no filter asks
        it to: it annotates it instead.
        """
        timings: dict[str, float] = {}
        started = time_module.perf_counter()
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)

        base = filters or RecallFilters()
        overrides: dict[str, Any] = {}
        if limit is not None:
            overrides["limit"] = int(limit)
        if types is not None:
            overrides["types"] = tuple(types)
        if tags is not None:
            overrides["tags"] = tuple(tags)
        if tag_mode is not None:
            overrides["tag_mode"] = tag_mode
        if time_field is not None:
            overrides["time_field"] = time_field
        if include_demo is not None:
            overrides["include_demo"] = bool(include_demo)
        window = time
        if window is None and time_expression:
            window = parse_time_expression(time_expression, now=moment)
        if window is not None:
            overrides["time"] = window
        resolved_statuses = statuses
        if resolved_statuses is None and include_graveyard is False:
            resolved_statuses = ("ACTIVE",)
        if resolved_statuses is not None:
            overrides["statuses"] = tuple(resolved_statuses)
        if overrides:
            base = dataclasses.replace(base, **overrides)
        filters = base.normalized()

        mark = time_module.perf_counter()
        build_report = self.index.ensure_fresh()
        timings["ensureIndex"] = (time_module.perf_counter() - mark) * 1000

        raw = (query or "").strip()
        outcome = RecallOutcome(
            query=raw,
            normalized_query=normalize_text(raw),
            effective_query="",
            filters=filters.to_dict(),
            honesty=dict(HONESTY_STATEMENT),
        )
        if not raw:
            outcome.message = "Type whatever you remember — a fragment is enough."
            outcome.state = OutcomeState.NO_MATCH
            outcome.timings_ms = timings
            outcome.index_state = self.index.stats()
            return outcome
        if len(raw) > 500:
            from ..store import ValidationError

            raise ValidationError("query must be 500 characters or fewer")

        # --- 1. query understanding ------------------------------------
        mark = time_module.perf_counter()
        analysis = self._analyze(raw, now=moment)
        timings["analyze"] = (time_module.perf_counter() - mark) * 1000
        outcome.effective_query = analysis.effective
        outcome.analysis = analysis.to_dict()

        # --- 2. candidate generation -----------------------------------
        mark = time_module.perf_counter()
        candidate_ids, idf_map, generation_meta = self._generate_candidates(analysis, filters)
        timings["candidates"] = (time_module.perf_counter() - mark) * 1000
        outcome.candidates_examined = len(candidate_ids)
        prefilter_excluded = list(generation_meta.get("prefilterExcluded") or [])

        if not candidate_ids:
            outcome.state = OutcomeState.NO_MATCH
            outcome.timings_ms = timings
            outcome.index_state = self.index.stats()
            outcome.graph = {"expansion": "skipped — no candidates were generated"}
            if prefilter_excluded:
                hidden = filter_report(
                    filters, self._classify_prefilter_excluded(filters, prefilter_excluded)
                )
                outcome.honesty["filteredOut"] = hidden
                outcome.ambiguity = {
                    "ambiguous": False, "bandSize": 0, "topScore": 0.0,
                    "filteredOut": hidden, "hiddenCandidatesExist": True,
                }
                detail = "; ".join(
                    f"{count} by {hidden['labels'][reason]}"
                    for reason, count in sorted(hidden["byReason"].items())
                )
                outcome.message = (
                    f"Nothing matched after your filters, but {hidden['totalExcluded']} candidate(s) "
                    f"did match your words and were excluded by them: {detail}. Remove that filter "
                    f"and they will appear — IWTT would rather say it hid something than let an "
                    f"empty result imply the thought is gone."
                )
            else:
                outcome.honesty["filteredOut"] = filter_report(filters, [])
                outcome.message = (
                    f"Nothing in the vault contains those words. IWTT searched the full text, "
                    f"titles and tags of every indexed thought ({self.index.stats().get('documents', 0)}) "
                    f"for exact, prefix, variant, trigram and fuzzy candidates and found none. "
                    f"That is a statement about these words, not proof the thought is gone — "
                    f"try a different fragment, a related word, or a time/lifecycle filter."
                )
            return outcome

        # --- 3. load canonical text for the bounded set -----------------
        mark = time_module.perf_counter()
        candidates = load_candidates(self.vault, candidate_ids)
        for thought_id, hit in generation_meta.get("lexical", {}).items():
            candidate = candidates.get(thought_id)
            if candidate is None:
                continue
            candidate.lexical_matched_terms = hit.matched_terms
            candidate.lexical_matched_fields = hit.matched_fields
            candidate.lexical_idf_sum = hit.idf_sum
            candidate.lexical_weighted_tf = hit.weighted_tf
        for thought_id, hit in generation_meta.get("trigram", {}).items():
            candidate = candidates.get(thought_id)
            if candidate is None:
                continue
            candidate.trigram_similarity = hit.similarity
            candidate.trigram_shared = hit.shared
            candidate.trigram_dot = hit.dot
        # graph/trigram metadata for ids that came from only one generator
        timings["loadCanonical"] = (time_module.perf_counter() - mark) * 1000

        # --- 3b. hard filters ------------------------------------------
        # Every exclusion is counted and reported.  A filter is allowed to hide a
        # candidate from the RESULT LIST; it is not allowed to hide the FACT that
        # something was excluded, because "no match" and "you filtered it out"
        # are different truths and the user is entitled to know which one happened.
        mark = time_module.perf_counter()
        survivors: list[Any] = []
        rejected: list[tuple[Any, str]] = []
        for candidate in candidates.values():
            reason = rejection_reason(
                filters,
                thought_type=candidate.type,
                status=candidate.status,
                created_at=candidate.created_at,
                updated_at=candidate.updated_at,
                graveyard_at=candidate.graveyard_at,
                resurrected_at=candidate.resurrected_at,
                is_demo=candidate.is_demo,
                tag_names=candidate.tags,
            )
            if reason is None:
                survivors.append(candidate)
            else:
                rejected.append((candidate, reason))
        survivors.sort(key=lambda c: c.thought_id)
        if prefilter_excluded:
            rejected.extend(self._classify_prefilter_excluded(filters, prefilter_excluded))
            rejected.sort(key=lambda pair: pair[0].thought_id)
        excluded_report = filter_report(filters, rejected)
        outcome.honesty["filteredOut"] = excluded_report
        timings["filter"] = (time_module.perf_counter() - mark) * 1000

        if not survivors:
            outcome.state = OutcomeState.NO_MATCH
            if rejected:
                by_reason = excluded_report["byReason"]
                detail = "; ".join(
                    f"{count} by {excluded_report['labels'][reason]}"
                    for reason, count in sorted(by_reason.items())
                )
                outcome.message = (
                    f"Nothing matched after your filters, but {len(rejected)} candidate(s) did "
                    f"match your words and were excluded: {detail}. Remove that filter and they "
                    f"will appear — IWTT would rather tell you it hid something than report an "
                    f"empty result and let you conclude the thought is gone."
                )
                outcome.ambiguity = {
                    "ambiguous": False,
                    "bandSize": 0,
                    "topScore": 0.0,
                    "filteredOut": excluded_report,
                    "hiddenCandidatesExist": True,
                }
            else:
                outcome.message = (
                    "No thought matches that fragment with the filters you set. "
                    "Try removing a filter — the Graveyard and demo records are included by default."
                )
            outcome.timings_ms = timings
            outcome.index_state = self.index.stats()
            outcome.graph = {"expansion": "skipped — no lexical candidates survived filtering"}
            return outcome

        # --- 4. base scoring pass (cheap, no difflib) -------------------
        mark = time_module.perf_counter()
        base: dict[str, float] = {}
        # term -> (tier value, trigram similarity, thought_id): the per-term
        # guarantee needs a deterministic winner, so ties break on trigram
        # similarity and then on the durable id.
        per_term_winners: dict[str, tuple[float, float, str]] = {}
        for candidate in survivors:
            score, _signals = score_candidate(
                analysis, candidate, idf_map=idf_map, config=self.config, allow_fuzzy=False
            )
            base[candidate.thought_id] = score
            candidate.cheap_score = score
            for term in analysis.content_terms or analysis.terms:
                _tier, value, _form = term_tier(
                    term.term, candidate, field_name="all", allow_fuzzy=False, config=self.config
                )
                if value <= 0:
                    continue
                key = (value, candidate.trigram_similarity, candidate.thought_id)
                previous = per_term_winners.get(term.term)
                if previous is None or key > previous:
                    per_term_winners[term.term] = key
        timings["baseScoring"] = (time_module.perf_counter() - mark) * 1000

        signatures = {c.thought_id: c.signature for c in survivors}
        degrees = {c.thought_id: c.degree for c in survivors}
        titles = {c.thought_id: c.display_title for c in survivors}
        winner_ids = {
            term: winner[2] for term, winner in sorted(per_term_winners.items())
        }
        seeds = pick_seeds(
            [
                (c.thought_id, base[c.thought_id], degrees.get(c.thought_id, 0), signatures[c.thought_id])
                for c in survivors
            ],
            # Per-term ids in term order: appended after the score-ordered seeds
            # so the strongest evidence leads but no matched term is left out.
            [winner_ids[term] for term in sorted(winner_ids)],
            signatures=signatures,
            config=self.config,
        )

        # --- 5. bounded graph expansion --------------------------------
        mark = time_module.perf_counter()
        seed_scores = {seed: base[seed] for seed in seeds if seed in base}
        graph_signals, graph_diagnostics = expand_neighbourhood(
            self.vault, seed_scores, config=self.config, hub_threshold=self.index.hub_threshold()
        )
        timings["graphExpansion"] = (time_module.perf_counter() - mark) * 1000
        outcome.graph = {
            "seeds": [
                {"thoughtId": seed, "score": round(base.get(seed, 0.0), 4),
                 "displayTitle": sanitize_label(titles.get(seed, ""), 80)}
                for seed in seeds
            ],
            "perTermWinners": winner_ids,
            "expansion": graph_diagnostics,
        }

        extra_ids = [tid for tid in graph_signals if tid not in candidates]
        if extra_ids:
            extra = load_candidates(self.vault, extra_ids)
            for thought_id, candidate in extra.items():
                if matches_filters(
                    filters,
                    thought_type=candidate.type,
                    status=candidate.status,
                    created_at=candidate.created_at,
                    updated_at=candidate.updated_at,
                    graveyard_at=candidate.graveyard_at,
                    resurrected_at=candidate.resurrected_at,
                    is_demo=candidate.is_demo,
                    tag_names=candidate.tags,
                ):
                    candidates[thought_id] = candidate
                    survivors.append(candidate)
                    base[thought_id] = 0.0
            survivors.sort(key=lambda c: c.thought_id)

        # --- 6. bounded fuzzy pass selection ----------------------------
        mark = time_module.perf_counter()
        fuzzy_ids = self._select_fuzzy_pass(survivors, base)
        timings["fuzzySelection"] = (time_module.perf_counter() - mark) * 1000

        # --- 7. final scoring -------------------------------------------
        mark = time_module.perf_counter()
        memory_values = querymemory.usefulness(self.index, raw, now=moment, config=self.config)
        clusters = self.index.clusters()
        titles = {c.thought_id: c.display_title for c in survivors}
        results: list[RecallResult] = []
        suppressed: list[dict[str, Any]] = []
        for candidate in survivors:
            graph_signal = graph_signals.get(candidate.thought_id)
            temporal_value, temporal_label = self._temporal_signal(
                analysis, filters, candidate, moment
            )
            lifecycle_value = self._lifecycle_signal(analysis, filters, candidate)
            memory_entry = memory_values.get(candidate.thought_id)
            memory_signal = (
                {
                    "value": memory_entry.value,
                    "events": memory_entry.events,
                    "corroborated": memory_entry.corroborated,
                    "detail": tuple(memory_entry.detail),
                }
                if memory_entry
                else None
            )
            score, signals = score_candidate(
                analysis,
                candidate,
                idf_map=idf_map,
                config=self.config,
                allow_fuzzy=candidate.thought_id in fuzzy_ids,
                graph_signal=(
                    {
                        "contribution": graph_signal.contribution,
                        "origin": graph_signal.origin,
                        "depth": graph_signal.depth,
                        "relType": graph_signal.rel_type,
                        "direction": graph_signal.direction,
                        "label": graph_signal.label,
                        "provenance": graph_signal.provenance,
                        "status": graph_signal.status,
                        "hubFactor": graph_signal.hub_factor,
                        "path": graph_signal.path,
                    }
                    if graph_signal
                    else None
                ),
                temporal_signal=temporal_value,
                temporal_label=temporal_label,
                lifecycle_signal=lifecycle_value,
                memory_signal=memory_signal,
            )
            if score <= 0 and not graph_signal:
                # Record what the lexical-evidence gate suppressed.  Refusing to
                # return a coincidence is right; refusing to mention that a
                # coincidence was measured and rejected would be hiding evidence.
                if not signals.lexical_evidence and (
                    signals.gated_fuzzy > 0 or signals.gated_trigram > 0
                ):
                    suppressed.append({
                        "thoughtId": candidate.thought_id,
                        "displayTitle": candidate.display_title,
                        "gatedFuzzy": round(signals.gated_fuzzy, 4),
                        "gatedTrigram": round(signals.gated_trigram, 4),
                        "reason": "NO_LEXICAL_EVIDENCE",
                    })
                continue
            relationship_context = None
            if graph_signal:
                rendered = relationship_context_for(graph_signal, titles)
                relationship_context = RelationshipContext(
                    thought_id=rendered["thoughtId"],
                    display_title=rendered["displayTitle"],
                    rel_type=rendered["relType"],
                    direction=rendered["direction"],
                    label=rendered["label"],
                    provenance=rendered["provenance"],
                    status=rendered["status"],
                    display=rendered["display"],
                    depth=rendered["depth"],
                    via=tuple(rendered["via"]),
                    relationship_id=rendered["relationshipId"],
                )
            explanations = (
                build_explanations(analysis, candidate, signals, score, self.config)
                if explain
                else ()
            )
            results.append(
                make_result(
                    analysis,
                    candidate,
                    score,
                    signals,
                    explanations,
                    relationship_context=relationship_context,
                    cluster=clusters.get(candidate.thought_id),
                )
            )
        timings["finalScoring"] = (time_module.perf_counter() - mark) * 1000

        # --- 8. deterministic ranking + states ---------------------------
        mark = time_module.perf_counter()
        results.sort(key=sort_key)
        def returnable(result: RecallResult) -> bool:
            """The single published rule for what IWTT will hand back.

            Two conditions, both deliberate:

            1. the score clears ``min_score``; and
            2. the candidate has lexical evidence of its own — a word in common
               with the query — unless ``graph_may_surface_without_lexical_evidence``
               is switched on.

            Condition 2 is what keeps graph expansion an enrichment rather than a
            replacement for retrieval: association alone, however strong the edge,
            is never reported as a match.  It is recorded just below instead.
            """
            if result.score < self.config.min_score:
                return False
            return bool(
                result.signals.lexical_evidence
                or self.config.graph_may_surface_without_lexical_evidence
            )

        qualified = [result for result in results if returnable(result)]
        below_threshold = not qualified
        ranked = qualified if qualified else [
            result for result in results if returnable(result) or result.signals.lexical_evidence
        ][: self.config.ambiguity_min_candidates]

        # What graph expansion reached but IWTT declined to return, and why.
        # Expansion enriches retrieval; it never replaces it, so a thought with no
        # word in common is not a match — not even one the user linked by hand.
        # Naming it anyway is the difference between "no match" and "here is the
        # connection you made, which is not the same as a match".
        graph_declined = [
            {
                "thoughtId": result.thought_id,
                "displayTitle": result.display_title,
                "score": round(result.score, 4),
                "floor": self.config.min_score,
                "graphDepth": result.signals.graph_depth,
                "graphProvenance": result.signals.graph_provenance,
                "graphLabel": result.signals.graph_label,
                "graphOnly": not result.signals.lexical_evidence,
                "origin": result.signals.graph_origin,
                "originTitle": result.relationship_context.display_title
                if result.relationship_context else "",
                "reason": (
                    "BOTH"
                    if result.score < self.config.min_score and not result.signals.lexical_evidence
                    else "BELOW_MIN_SCORE"
                    if result.score < self.config.min_score
                    else "NO_LEXICAL_EVIDENCE"
                ),
            }
            for result in results
            if result.signals.graph_depth > 0 and not returnable(result)
        ]
        if graph_declined:
            graph_declined.sort(key=lambda item: (-item["score"], item["thoughtId"]))
            outcome.honesty["graphReachedBelowThreshold"] = graph_declined[:5]
            outcome.honesty["graphReachedBelowThresholdCount"] = len(graph_declined)
            outcome.honesty["graphReachedReturnedCount"] = sum(
                1 for result in ranked if result.signals.graph_depth > 0
            )
        ranked = ranked[: filters.limit]
        for position, result in enumerate(ranked, start=1):
            result.rank = position
        state, message, ambiguity = assign_states(ranked, self.config)
        if below_threshold and ranked:
            state = OutcomeState.NO_MATCH
            ambiguity["belowThreshold"] = True
            message = (
                f"Nothing scored above the {self.config.min_score:.2f} threshold IWTT needs before "
                f"it will claim a match. The {len(ranked)} weakest-but-closest candidates are shown "
                f"anyway — nothing was hidden."
            )
        if excluded_report["totalExcluded"] and state != OutcomeState.NO_MATCH:
            # Results exist, but a filter still hid something that matched the
            # words.  Say so in one sentence rather than only in the honesty
            # record: an omission the user cannot see is an omission that will be
            # read as "that thought is gone".
            by_reason = excluded_report["byReason"]
            detail = ", ".join(
                f"{count} by {excluded_report['labels'][reason]}"
                for reason, count in sorted(by_reason.items())
            )
            message = (
                f"{message} Note: {excluded_report['totalExcluded']} candidate(s) also matched "
                f"your words but were excluded by your filters ({detail})."
            )
            ambiguity["filteredOut"] = excluded_report

        if suppressed:
            suppressed.sort(
                key=lambda item: (-item["gatedFuzzy"], -item["gatedTrigram"], item["thoughtId"])
            )
            outcome.honesty["suppressedNoLexicalEvidence"] = suppressed[:5]
            outcome.honesty["suppressedCount"] = len(suppressed)
            if state == OutcomeState.NO_MATCH:
                worst = suppressed[0]
                message = (
                    f"No word of that query appears in any thought, so there is nothing to match. "
                    f"{len(suppressed)} candidate(s) shared only character patterns — the closest was "
                    f"“{worst['displayTitle']}” at {worst['gatedFuzzy']:.2f} wording similarity — and "
                    f"IWTT suppressed them rather than present a coincidence as a match. It has no "
                    f"semantic stage and will not guess at meaning. Try a word you think you actually "
                    f"used, a tag, a connection, or a time."
                )
                ambiguity["suppressedNoLexicalEvidence"] = suppressed[:5]

        if not ranked and rejected:
            # Candidates matched the words but a filter removed them, and nothing
            # else scored.  Say which of those two things happened.
            state = OutcomeState.NO_MATCH
            detail = "; ".join(
                f"{count} by {excluded_report['labels'][reason]}"
                for reason, count in sorted(excluded_report["byReason"].items())
            )
            message = (
                f"Nothing matched after your filters, but {excluded_report['totalExcluded']} "
                f"candidate(s) did match your words and were excluded by them: {detail}. "
                f"Remove that filter and they will appear."
            )
            ambiguity = {
                "ambiguous": False, "bandSize": 0, "topScore": 0.0,
                "filteredOut": excluded_report, "hiddenCandidatesExist": True,
            }
        outcome.results = ranked
        outcome.state = state
        outcome.message = message
        outcome.ambiguity = ambiguity
        timings["ranking"] = (time_module.perf_counter() - mark) * 1000

        outcome.timings_ms = timings
        outcome.timings_ms["total"] = (time_module.perf_counter() - started) * 1000
        outcome.index_state = self.index.stats()
        # MERGE, never replace: the pipeline stages above recorded what actually
        # happened (candidate generation, trigram guard, fuzzy pass budget, filter
        # exclusions).  Overwriting that with the static statement would delete the
        # evidence the diagnostics view depends on.
        honesty = dict(HONESTY_STATEMENT)
        honesty.update(outcome.honesty)
        honesty["filteredOut"] = excluded_report
        outcome.honesty = honesty
        outcome.honesty.update({
            "mode": mode,
            "candidateGeneration": {
                "lexical": len(generation_meta.get("lexical", {})),
                "trigram": len(generation_meta.get("trigram", {})),
                "union": len(candidate_ids),
                "trigramGuardTripped": generation_meta.get("trigramMeta", {}).get("guardTripped", False),
                "trigramSelective": generation_meta.get("trigramMeta", {}).get("selective", False),
                "sqlPrefilterApplied": generation_meta.get("sqlPrefilterApplied", False),
                "prefilterExcluded": len(prefilter_excluded),
                "prefilterExcludedAccountedFor": bool(excluded_report["totalExcluded"]),
                # The fuzzy budget belongs here as well as at the top level: this
                # is the record a reader consults to ask "what did you examine?".
                "fuzzyPassCandidates": len(fuzzy_ids),
                "fuzzyPassCap": self.config.fuzzy_scoring_cap,
                "lexicalCandidateCap": self.config.token_candidate_cap,
                "trigramCandidateCap": self.config.trigram_candidate_cap,
                "trigramGuardFraction": self.config.trigram_guard_fraction,
            },
            "fuzzyPassCandidates": len(fuzzy_ids),
            "fuzzyPassCap": self.config.fuzzy_scoring_cap,
            "indexRebuilt": build_report.to_dict() if build_report else None,
            "graphReached": len(graph_signals),
        })
        if record_opened and self.config.record_opened_signal and ranked:
            # Weak implicit signal only.  Recorded for the top result; never
            # authoritative, always decayed, always resettable.
            try:
                querymemory.record_feedback(
                    self.index, raw, ranked[0].thought_id, "OPENED",
                    rank=1, score=ranked[0].score, note="implicit open signal",
                )
            except (sqlite3.Error, KeyError, ValueError):
                pass
        return outcome

    # ------------------------------------------------------------------
    # stage helpers
    # ------------------------------------------------------------------

    def _analyze(self, query: str, *, now: datetime) -> QueryAnalysis:
        with self.vault._connection() as conn:
            total_docs = self.index.document_count(conn)
            preliminary = analyze_query(query, now=now)
            wanted = sorted({form for term in preliminary.terms for form in term.variants} |
                            {term.term for term in preliminary.terms})
            df_map = self.index.df_map(conn, wanted)

        def lookup(term: str) -> float:
            return RecallIndex.idf(total_docs, df_map.get(term, 0))

        return analyze_query(query, now=now, idf_lookup=lookup)

    def _classify_prefilter_excluded(
        self, filters: RecallFilters, thought_ids: Sequence[str]
    ) -> list[tuple[Any, str]]:
        """Load the bounded set the SQL prefilter removed and name the rule that
        removed each one.

        The SQL prefilter is a performance optimisation, and an optimisation must
        not change what the user is told.  Without this, a Graveyard thought hidden
        by ``statuses=('ACTIVE',)`` would vanish before the Python filter could
        count it, and the outcome would read "nothing matched" instead of "your
        filter hid one thing that did match".
        """
        excluded = load_candidates(self.vault, list(thought_ids))
        rejected: list[tuple[Any, str]] = []
        for thought_id in thought_ids:
            candidate = excluded.get(thought_id)
            if candidate is None:
                continue
            reason = rejection_reason(
                filters,
                thought_type=candidate.type,
                status=candidate.status,
                created_at=candidate.created_at,
                updated_at=candidate.updated_at,
                graveyard_at=candidate.graveyard_at,
                resurrected_at=candidate.resurrected_at,
                is_demo=candidate.is_demo,
                tag_names=candidate.tags,
            )
            rejected.append((candidate, reason or "unknown"))
        return rejected

    def _generate_candidates(
        self, analysis: QueryAnalysis, filters: RecallFilters
    ) -> tuple[list[str], dict[str, float], dict[str, Any]]:
        meta: dict[str, Any] = {}
        with self.vault._connection() as conn:
            total_docs = self.index.document_count(conn)
            terms = analysis.content_terms or analysis.terms
            wanted = sorted({term.term for term in terms} | {v for term in terms for v in term.variants})
            df_map = self.index.df_map(conn, wanted)
            forms: list[tuple[str, str, float]] = []
            idf_map: dict[str, float] = {}
            for term in terms:
                weight = term.idf or RecallIndex.idf(total_docs, df_map.get(term.term, 0))
                idf_map[term.term] = weight
                for variant in sorted(term.variants):
                    forms.append((term.term, variant, weight))
            lexical = self.index.lexical_candidates(
                conn, forms, limit=self.config.token_candidate_cap
            )
            trigram, trigram_meta = self.index.trigram_candidates(
                conn, list(analysis.trigram_words), limit=self.config.trigram_candidate_cap
            )
            meta["lexical"] = lexical
            meta["trigram"] = trigram
            meta["trigramMeta"] = trigram_meta

            union = sorted(set(lexical) | set(trigram))
            if trigram_meta.get("guardTripped"):
                # The never-worse contract: when the trigram index is not
                # selective it must not narrow the candidate set, so the lexical
                # set is used as-is and the guard is reported honestly.
                union = sorted(set(lexical) | set(trigram))

            where, params = sql_prefilter(filters)
            meta["sqlPrefilterApplied"] = False
            if where and union:
                marks = ",".join("?" for _ in union)
                # ``sql_prefilter`` returns a leading " WHERE "; fold it into an
                # AND so it narrows the candidate id set rather than replacing it.
                extra = where.strip()
                if extra.upper().startswith("WHERE "):
                    extra = extra[len("WHERE "):]
                kept = {
                    row["thought_id"]
                    for row in conn.execute(
                        f"SELECT d.thought_id AS thought_id FROM recall_docs d "
                        f"WHERE d.thought_id IN ({marks}) AND ({extra})",
                        list(union) + params,
                    )
                }
                removed = [thought_id for thought_id in union if thought_id not in kept]
                union = [thought_id for thought_id in union if thought_id in kept]
                meta["sqlPrefilterApplied"] = True
                # Kept so the outcome can report exclusion instead of implying the
                # thoughts do not exist.  The ids are already bounded by the same
                # candidate caps, so this list can never be unbounded.
                meta["prefilterExcluded"] = removed
        return union, idf_map, meta

    def _select_fuzzy_pass(
        self, survivors: Sequence[Candidate], base: dict[str, float]
    ) -> set[str]:
        """Which candidates earn the expensive ``difflib`` pass.

        Deterministic union of the top-N by base score and the top-N by trigram
        similarity (the fuzzy proxy), so a typo-only match cannot be starved of
        the fuzzy pass by having a low lexical score.  Capped at
        ``fuzzy_scoring_cap``.
        """
        cap = max(1, self.config.fuzzy_scoring_cap)
        half = max(1, cap // 2)
        by_base = sorted(
            survivors,
            key=lambda c: (-base.get(c.thought_id, 0.0), -c.trigram_similarity, c.thought_id),
        )[:half]
        by_trigram = sorted(
            survivors, key=lambda c: (-c.trigram_similarity, -base.get(c.thought_id, 0.0), c.thought_id)
        )[:half]
        chosen = {c.thought_id for c in by_base} | {c.thought_id for c in by_trigram if c.trigram_similarity > 0}
        if len(chosen) > cap:
            chosen = {
                c.thought_id
                for c in sorted(
                    (c for c in survivors if c.thought_id in chosen),
                    key=lambda c: (-base.get(c.thought_id, 0.0), -c.trigram_similarity, c.thought_id),
                )[:cap]
            }
        return chosen

    def _temporal_signal(
        self,
        analysis: QueryAnalysis,
        filters: RecallFilters,
        candidate: Candidate,
        moment: datetime,
    ) -> tuple[float, str]:
        """Temporal context.

        An explicit filter is a hard gate applied earlier, so survivors get full
        credit.  A *hint* detected inside the query is a soft boost only: IWTT
        does not fabricate precision the user's phrasing did not carry.
        """
        if filters.time:
            return 1.0, f"inside {filters.time.label}"
        hint = analysis.time_hint
        if hint is None:
            return 0.0, ""
        if hint.contains(candidate.created_at):
            return 1.0, f"inside {hint.label}"
        # Half credit for being in the right neighbourhood of time: a memory of
        # "last month" is often off by a little, and IWTT would rather annotate
        # than exclude.
        created = candidate.created_at[:10]
        start, end = hint.date_bounds()
        if created < start or created > end:
            return 0.25, f"outside {hint.label}"
        return 0.0, ""

    def _lifecycle_signal(
        self, analysis: QueryAnalysis, filters: RecallFilters, candidate: Candidate
    ) -> float:
        if filters.types:
            return 1.0 if candidate.type in filters.types else 0.0
        if analysis.type_hints:
            return 1.0 if candidate.type in analysis.type_hints else 0.0
        return 0.0

    # ------------------------------------------------------------------
    # headline workflow
    # ------------------------------------------------------------------

    def find_my_thought(
        self,
        query: str,
        *,
        filters: RecallFilters | None = None,
        limit: int | None = None,
        include_graveyard: bool | None = None,
        now: datetime | None = None,
        record_opened: bool = False,
    ) -> dict[str, Any]:
        """FIND MY THOUGHT.

        The primary result action is always "what I actually wrote": every
        ``RecallResult`` carries ``original_text`` verbatim from the canonical
        ``thoughts.original_text`` column, which the v0.1 database trigger makes
        immutable.  No rewrite, no summary, no generated title is ever presented
        as the thought.
        """
        outcome = self.recall(
            query,
            filters=filters,
            limit=limit,
            include_graveyard=include_graveyard,
            now=now,
            explain=True,
            record_opened=record_opened,
            mode="find_my_thought",
        )
        return self.find_my_thought_payload(outcome)

    @staticmethod
    def find_my_thought_payload(outcome: RecallOutcome) -> dict[str, Any]:
        """The FIND MY THOUGHT view model: JSON-ready, UI-shaped.

        Deliberately keeps the ORIGINAL and the DERIVED apart:
        ``results[i].originalText`` is canonical and immutable; everything under
        ``explanations`` / ``why`` / ``graph`` / ``cluster`` is derived and is
        labelled as such, so the UI can render them in visually distinct blocks
        and so no generated text can be mistaken for what the user wrote.
        """
        payload = outcome.to_dict()
        payload["view"] = "find_my_thought"
        payload["primaryAction"] = {
            "id": "OPEN_ORIGINAL_TEXT",
            "label": "Open what I actually wrote",
            "canonical": True,
            "note": "Every result carries originalText verbatim from thoughts.original_text.",
        }
        payload["provenance"] = {
            "canonical": ["originalText", "title", "type", "status", "createdAt",
                          "updatedAt", "graveyardAt", "resurrectedAt", "tags"],
            "derived": ["score", "rank", "state", "matchReason", "why", "explanations",
                        "signals", "graph", "cluster", "ambiguity", "timingsMs",
                        "queryMemory", "relationshipContext"],
            "rule": (
                "Canonical fields are the user's knowledge. Derived fields are IWTT's "
                "opinion about how to find it, are rebuildable, and are deleted by "
                "clear_derived_data()."
            ),
        }
        payload["honestyStatement"] = HONESTY_STATEMENT
        payload["results"] = [
            {**result.to_dict(), "rank": result.rank}
            for result in outcome.results
        ]
        if outcome.state == OutcomeState.AMBIGUOUS:
            payload["headline"] = (
                f"I found {len(outcome.results)} likely memories"
            )
        elif outcome.state == OutcomeState.NO_MATCH:
            payload["headline"] = "I could not find that thought"
        elif outcome.state == OutcomeState.STRONG:
            payload["headline"] = "This looks like the thought you meant"
        else:
            payload["headline"] = "This may be the thought you meant"
        return payload

    # ------------------------------------------------------------------
    # delegations
    # ------------------------------------------------------------------

    def diagnose(
        self, query: str, thought_id: str | None = None, *, filters: RecallFilters | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        from .diagnostics import diagnose

        return diagnose(self, query, thought_id=thought_id, filters=filters, now=now)

    def find_path(
        self, source_id: str, target_id: str, *, directed: bool = True
    ) -> Any:
        return find_path(self.vault, source_id, target_id, directed=directed, config=self.config)

    def lineage(self, thought_id: str, *, max_depth: int = 3) -> dict[str, Any]:
        return lineage(self.vault, thought_id, max_depth=max_depth, config=self.config)

    def neighbourhood(self, thought_id: str, *, depth: int = 1) -> dict[str, Any]:
        from .expansion import neighbourhood

        return neighbourhood(self.vault, thought_id, depth=depth, config=self.config)

    def graph_view_model(
        self, *, root_ids: Sequence[str] | None = None, max_nodes: int = 200,
        include_graveyard: bool = True, include_suggested: bool = True,
    ) -> dict[str, Any]:
        self.index.ensure_fresh()
        model = build_graph_view_model(
            self.index,
            root_ids=root_ids,
            max_nodes=max_nodes,
            include_graveyard=include_graveyard,
            include_suggested=include_suggested,
            config=self.config,
        )
        from ..store import now_utc

        model["generatedAt"] = now_utc()
        return model

    def clusters(self) -> list[dict[str, Any]]:
        self.index.ensure_fresh()
        return cluster_summary(self.index)

    def record_feedback(
        self, query: str, thought_id: str, feedback: str, *, rank: int | None = None,
        score: float | None = None, note: str = "", recorded_at: str | None = None,
    ) -> dict[str, Any]:
        self.index.ensure_available()
        return querymemory.record_feedback(
            self.index, query, thought_id, feedback, rank=rank, score=score, note=note,
            recorded_at=recorded_at,
        )

    def reset_learning(self) -> dict[str, Any]:
        return querymemory.reset_learning(self.index)

    def learning_stats(self) -> dict[str, Any]:
        return querymemory.stats(self.index)

    def learning_history(self, limit: int = 100) -> list[dict[str, Any]]:
        return querymemory.history(self.index, limit)

    def learned_queries(self, limit: int = 50) -> list[dict[str, Any]]:
        return querymemory.learned_queries(self.index, limit)

    def rebuild_indexes(
        self, *, full: bool = True, rebuild_clusters: bool = True
    ) -> dict[str, Any]:
        """Rebuild the derived layer.  Canonical data is only ever read."""
        # full=True rebuilds every derived row; full=False performs the same
        # incremental sync ``ensure_fresh()`` would, driven by the canonical
        # signature probe.  Neither path writes to a canonical table.
        report = (
            self.index.rebuild().to_dict() if full else self.index.ensure_fresh().to_dict()
        )
        clusters = 0
        if rebuild_clusters:
            clusters = int(self.index.rebuild_clusters())
        report["clustersRebuilt"] = clusters
        report["scope"] = "rebuild" if full else "sync"
        return report

    def index_stats(self) -> dict[str, Any]:
        stats = self.index.stats()
        stats["drift"] = self.index.drift()
        fingerprint = getattr(self.vault, "canonical_fingerprint", None)
        stats["canonicalFingerprint"] = fingerprint() if callable(fingerprint) else None
        return stats

    def config_dict(self) -> dict[str, Any]:
        """Every weight, cap, threshold and boundary — camelCase, JSON-ready.

        Published at ``/api/recall/config`` and rendered in the UI's diagnostics
        drawer, because a ranking nobody can inspect is a ranking nobody can
        trust.  There are no hidden constants: everything that influences an
        ordering appears here.
        """
        return self.config.to_public_dict()
