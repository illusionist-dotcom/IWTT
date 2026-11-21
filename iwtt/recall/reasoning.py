"""Cross-memory reasoning over a person's own captures.

This is the analytical engine behind the Thinking Partner.  It reads canonical
memories and the rebuildable recall indexes, and returns **proposals**: ranked
neighbours, candidate connections, recurring themes, possible tensions, a
chronology of revisions and a contextual explanation.  Every finding carries
references to the original thoughts it came from, and every excerpt is read
from the canonical entry at the time of the request.

Boundaries, deliberately:

* Nothing here writes to a canonical table.  Relationships are never created,
  confirmed, deleted or changed.  A connection is a suggestion that a person
  must save and review through the existing relationship route.
* It works with **no provider at all**.  The deterministic lexical analysis is
  the baseline.  If a provider is configured, its view is added as a separate,
  clearly labelled proposal; a provider failure degrades to the baseline with a
  warning instead of breaking the answer or inventing a memory.
* Findings are ephemeral unless the user explicitly asks to save one as a
  derived artifact.  Artifacts use the existing ``derived_artifacts`` table, are
  erasable with all other derived data, and never replace the original capture.
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from typing import Any, Iterable, Sequence

from ..store import RELATIONSHIP_TYPES, now_utc
from .embeddings import EmbeddingIndex
from .text import recall_snippet, recall_variants, sanitize_label, tokenize

#: The only modes the HTTP layer may dispatch.  A closed vocabulary keeps a
#: typo from quietly turning into a different kind of analysis.
MODES: tuple[str, ...] = (
    "similar", "connections", "themes", "contradictions", "evolution", "explain",
)

#: Artifact type recorded when a person explicitly saves a finding.  The event
#: names live beside the canonical constants in ``iwtt.store``; no schema or
#: canonical byte changes.
REASONING_ARTIFACT_TYPES: dict[str, str] = {
    "similar": "AI_SIMILARITY_ANALYSIS",
    "connections": "AI_CONNECTION_DISCOVERY",
    "themes": "AI_THEME_ANALYSIS",
    "contradictions": "AI_CONTRADICTION_REVIEW",
    "evolution": "AI_EVOLUTION_REVIEW",
    "explain": "AI_CONTEXT_EXPLANATION",
}

AUTHORITY = "DERIVED_REASONING"
BASELINE_PROVIDER = "Local deterministic reasoning (no provider)"
MAX_CORPUS = 2000
MAX_CITATION_CHARS = 220

_NEGATION = re.compile(
    r"\b(not|never|no longer|don'?t|doesn'?t|isn'?t|aren'?t|wasn'?t|weren'?t|"
    r"won'?t|can'?t|cannot|shouldn'?t|avoid|reject|abandon|stop|drop)\b",
    re.IGNORECASE,
)
_AFFIRMATION = re.compile(
    r"\b(always|must|should|need to|will|keep|continue|start|begin|use|adopt|"
    r"commit|definitely|every time)\b",
    re.IGNORECASE,
)
_CONFLICT_CUE = re.compile(
    r"\b(but|however|whereas|instead of|rather than|conflicts? with|contradicts?|"
    r"on the other hand|although|yet)\b",
    re.IGNORECASE,
)
_EVENT_KINDS = frozenset({
    "THOUGHT_CREATED", "THOUGHT_EDITED", "THOUGHT_RESTORED",
    "RELATIONSHIP_CREATED", "RELATIONSHIP_CONFIRMED", "RELATIONSHIP_REMOVED",
    "AI_SUMMARY_GENERATED", "AI_INTERPRETATION_GENERATED", "AI_LINK_ANALYSIS_GENERATED",
})


def _excerpt(thought: dict[str, Any], text: str | None = None) -> str:
    """A short, truthful quotation from a canonical capture."""
    source = str(text if text is not None else (
        thought.get("currentText") or thought.get("originalText") or ""
    ))
    source = " ".join(source.split())
    return source[:MAX_CITATION_CHARS] + ("…" if len(source) > MAX_CITATION_CHARS else "")


def citation(thought: dict[str, Any], text: str | None = None) -> dict[str, Any]:
    """The smallest reference that always points back to a real memory."""
    return {
        "thoughtId": thought.get("id"),
        "title": thought.get("displayTitle") or thought.get("title") or "Untitled thought",
        "excerpt": _excerpt(thought, text),
        "createdAt": thought.get("createdAt"),
    }


def _tag_name(tag: Any) -> str:
    """Tag rows are dicts in the canonical view and strings in candidate lists."""
    if isinstance(tag, dict):
        return str(tag.get("name") or "")
    return str(tag or "")


def _features(thought: dict[str, Any]) -> Counter[str]:
    """Stable weighted terms for one thought, from title, body and tags.

    Morphological/alias variants are canonicalised to their smallest form, so
    the same text always yields the same counter and ``adapt``/``adjust`` meet.
    """
    counter: Counter[str] = Counter()
    tag_text = " ".join(_tag_name(tag) for tag in thought.get("tags", []))
    for field in (thought.get("title") or "", thought.get("currentText") or "", tag_text):
        for token in tokenize(str(field)):
            variants = recall_variants(token)
            # Prefer the word actually written by the author; aliases/morphology
            # are only a bridge between captures, never a replacement label.
            counter[token if token in variants else min(variants)] += 1
    return counter


def _idf(corpus: Sequence[Counter[str]]) -> dict[str, float]:
    if not corpus:
        return {}
    df: Counter[str] = Counter()
    for features in corpus:
        df.update(features.keys())
    count = len(corpus)
    return {term: math.log((count + 1) / (frequency + 1)) + 1.0
            for term, frequency in df.items()}


def _weighted_cosine(first: Counter[str], second: Counter[str],
                     weights: dict[str, float]) -> float:
    if not first or not second:
        return 0.0
    shared = set(first) & set(second)
    if not shared:
        return 0.0
    dot = 0.0
    for term in shared:
        weight = weights.get(term, 1.0)
        dot += weight * weight * first[term] * second[term]
    norm_a = math.sqrt(sum(weights.get(term, 1.0) ** 2 * value ** 2
                           for term, value in first.items()))
    norm_b = math.sqrt(sum(weights.get(term, 1.0) ** 2 * value ** 2
                           for term, value in second.items()))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def _jaccard(first: set[str], second: set[str]) -> float:
    union = first | second
    return len(first & second) / len(union) if union else 0.0


class ReasoningEngine:
    """Derived reasoning over the vault.  Provider use is optional.

    ``provider`` is passed in by the caller (the vault or the HTTP handler) so
    this module never reaches into runtime configuration and never imports the
    server.  A missing provider is a normal, supported mode of operation.
    """

    def __init__(self, vault: Any, provider: Any | None = None) -> None:
        self.vault = vault
        self.provider = provider if provider is not None and getattr(provider, "available", False) else None

    # ------------------------------------------------------------------ framing --

    def _frame(self, mode: str, *, started: float) -> dict[str, Any]:
        return {
            "mode": mode,
            "authority": AUTHORITY,
            "canonical": False,
            "provider": self.provider.name if self.provider else BASELINE_PROVIDER,
            "model": getattr(self.provider, "model", None) if self.provider else None,
            "warnings": [],
            "generatedAt": now_utc(),
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
            "boundary": (
                "This is a derived reading of your own captures. It never edits, deletes or "
                "confirms a memory or a relationship. Original captures remain the source of truth."
            ),
        }

    def _corpus(self, *, include_graveyard: bool = False) -> list[dict[str, Any]]:
        status = "ALL" if include_graveyard else "ACTIVE"
        return self.vault.list_thoughts(status=status)[:MAX_CORPUS]

    def _cited(self, result: dict[str, Any]) -> list[str]:
        ids: list[str] = []
        for finding in result.get("findings", []):
            for ref in finding.get("thoughtIds", []):
                if ref not in ids:
                    ids.append(ref)
        return ids

    def run(self, mode: str, *, thought_id: str | None = None, query: str = "",
            limit: int = 8, thought_ids: Sequence[str] | None = None,
            include_graveyard: bool = False, use_provider: bool = True) -> dict[str, Any]:
        """Dispatch one explicit reasoning request; validate every input."""
        if mode not in MODES:
            raise ValueError(f"Unknown reasoning mode: {mode}")
        started = time.perf_counter()
        limit = int(limit)
        if limit < 1 or limit > 20:
            raise ValueError("limit must be between 1 and 20")
        kwargs: dict[str, Any] = {
            "query": str(query or "")[:2000],
            "limit": limit,
            "include_graveyard": bool(include_graveyard),
            "use_provider": bool(use_provider) and self.provider is not None,
        }
        if thought_id:
            kwargs["thought_id"] = str(thought_id)
        if thought_ids:
            kwargs["thought_ids"] = [str(item) for item in thought_ids][:20]
        methods = {
            "similar": self.similar,
            "connections": self.connections,
            "themes": self.themes,
            "contradictions": self.contradictions,
            "evolution": self.evolution,
            "explain": self.explain,
        }
        result = methods[mode](**kwargs)
        warnings = result.pop("_warnings", [])
        result.update(self._frame(mode, started=started))
        result["warnings"] = list(dict.fromkeys([*result.get("warnings", []), *warnings]))
        cited = self._cited(result)
        result["citedThoughtIds"] = cited
        result["primaryThoughtId"] = result.get("primaryThoughtId") or (cited[0] if cited else None)
        return result

    # ----------------------------------------------------------------- similar --

    def similar(self, *, thought_id: str, query: str = "", limit: int = 8,
                include_graveyard: bool = False, **_kwargs: Any) -> dict[str, Any]:
        source = self.vault.get_thought(thought_id)
        corpus = [item for item in self._corpus(include_graveyard=include_graveyard)
                  if item["id"] != source["id"]]
        features = [_features(item) for item in corpus]
        source_features = _features(source)
        weights = _idf(features)
        ranked = sorted(
            ((_weighted_cosine(source_features, item_features, weights), index)
             for index, item_features in enumerate(features)),
            key=lambda pair: (-pair[0], corpus[pair[1]]["id"]),
        )
        findings: list[dict[str, Any]] = []
        for score, index in ranked:
            if score <= 0.0:
                break
            target = corpus[index]
            shared = sorted(set(source_features) & set(features[index]))
            findings.append({
                "thoughtId": target["id"],
                "thoughtIds": [target["id"]],
                "title": target.get("displayTitle") or "Untitled thought",
                "score": round(score, 6),
                "sharedTerms": shared[:8],
                "basis": "derived lexical TF-IDF over your captures",
                "citation": citation(target),
            })
            if len(findings) >= limit:
                break
        result: dict[str, Any] = {
            "findings": findings,
            "primaryThoughtId": source["id"],
            "note": (
                f"Ranked {len(corpus)} other captures by shared distinctive terms. A score "
                "is a ranked hint, not a probability or a claim about meaning."
            ),
        }
        semantic = self._semantic_neighbours(source["id"], query or str(source.get("currentText") or ""), limit)
        if semantic is not None:
            result["semanticNeighbours"] = semantic
            if not semantic.get("available"):
                result["_warnings"] = [semantic["reason"]]
        return result

    def _semantic_neighbours(self, source_id: str, text: str, limit: int) -> dict[str, Any] | None:
        """Add an explicitly labelled semantic view when an index exists."""
        if not self.provider:
            return None
        index = EmbeddingIndex(self.vault)
        status = index.status()
        if not status.get("available"):
            return None
        try:
            neighbours = index.similar(self.provider, text, limit=limit + 3)
        except Exception as exc:
            return {"available": False, "reason": str(exc), "results": []}
        for item in neighbours.get("results", []):
            if item["thoughtId"] == source_id:
                item["reason"] = "This is the selected thought."
        neighbours["results"] = [item for item in neighbours.get("results", [])
                                 if item["thoughtId"] != source_id][:limit]
        return neighbours

    # -------------------------------------------------------------- connections --

    def connections(self, *, thought_id: str, limit: int = 8,
                    use_provider: bool = False, **_kwargs: Any) -> dict[str, Any]:
        source = self.vault.get_thought(thought_id)
        candidates = self.vault.link_candidates(thought_id, limit=min(16, max(limit, 6)))
        warnings: list[str] = []
        verdicts: list[dict[str, Any]] = []
        if self.provider and use_provider:
            try:
                verdicts = self._provider_verdicts(source, candidates)
            except Exception as exc:
                warnings.append(f"The provider could not assess connections: {exc}")
                verdicts = []
        if not verdicts:
            verdicts = [self._local_verdict(source, candidate) for candidate in candidates]
        by_id = {item["id"]: item for item in candidates}
        findings: list[dict[str, Any]] = []
        rejected = 0
        for verdict in verdicts:
            target = by_id.get(verdict.get("targetId"))
            if not target:
                continue  # a provider can never introduce a thought that was not supplied
            if not verdict.get("linked"):
                rejected += 1
                continue
            relationship_type = str(verdict.get("type") or "RELATED_TO").upper()
            if relationship_type not in RELATIONSHIP_TYPES:
                relationship_type = "RELATED_TO"
            findings.append({
                "sourceId": source["id"],
                "targetId": target["id"],
                "thoughtIds": [source["id"], target["id"]],
                "type": relationship_type,
                "reason": str(verdict.get("reason") or "")[:400],
                # AI_SUGGESTED is the canonical provenance for any machine-made
                # proposal; ``basis`` says whether it came from a provider or
                # from the local deterministic heuristic.
                "provenance": "AI_SUGGESTED",
                "basis": "provider assessment" if self.provider and use_provider else "local deterministic heuristic",
                "status": "SUGGESTED",
                "requiresApproval": True,
                "source": citation(source),
                "target": citation(target),
            })
            if len(findings) >= limit:
                break
        result: dict[str, Any] = {
            "findings": findings,
            "primaryThoughtId": source["id"],
            "examinedCount": len(candidates),
            "notLinkedCount": rejected,
            "note": (
                f"Examined {len(candidates)} candidate memories. Every proposal is unconfirmed "
                "and requires your explicit save and review; no relationship was written."
            ),
            "_warnings": warnings,
        }
        return result

    def _provider_verdicts(self, source: dict[str, Any],
                           candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strictly validate a provider's linkage response."""
        verdicts = self.provider.assess_links(source, candidates)  # type: ignore[union-attr]
        allowed = {item["id"] for item in candidates}
        clean: list[dict[str, Any]] = []
        for item in verdicts if isinstance(verdicts, list) else []:
            if not isinstance(item, dict) or item.get("targetId") not in allowed:
                continue
            if not isinstance(item.get("linked"), bool):
                continue
            clean.append({
                "targetId": item["targetId"],
                "linked": item["linked"],
                "type": item.get("type"),
                "reason": item.get("reason"),
            })
        return clean

    @staticmethod
    def _local_verdict(source: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        first, second = _features(source), _features(candidate)
        shared = sorted(set(first) & set(second))
        linked = len(shared) >= 2
        reason = (
            "Both captures use the distinctive terms " + ", ".join(shared[:5]) + ". Review whether "
            "that shared subject is a real connection." if linked else
            "These captures share fewer than two distinctive terms; the local baseline found no "
            "defensible connection. A different provider may still see one."
        )
        return {"targetId": candidate["id"], "linked": linked,
                "type": "RELATED_TO" if linked else "", "reason": reason}

    # ------------------------------------------------------------------- themes --

    def themes(self, *, limit: int = 8, include_graveyard: bool = False,
               **_kwargs: Any) -> dict[str, Any]:
        corpus = self._corpus(include_graveyard=include_graveyard)
        features = [_features(item) for item in corpus]
        weights = _idf(features)
        df: Counter[str] = Counter()
        for item in features:
            df.update(item.keys())
        minimum = 2 if len(corpus) >= 4 else 1
        candidates = [term for term, count in df.items()
                      if count >= minimum and count <= max(2, int(len(corpus) * 0.75))]
        candidates.sort(key=lambda term: (-df[term], -weights.get(term, 0.0), term))
        findings: list[dict[str, Any]] = []
        used_members: list[set[str]] = []
        for term in candidates[:120]:
            members = [index for index, item in enumerate(features) if term in item]
            member_ids = {corpus[index]["id"] for index in members}
            if any(_jaccard(member_ids, previous) >= 0.8 for previous in used_members):
                continue
            used_members.append(member_ids)
            tag_labels = [_tag_name(tag)
                          for index in members for tag in corpus[index].get("tags", [])]
            label = term if term in {item.lower() for item in tag_labels} else sanitize_label(term, 80)
            strength = min(1.0, (df[term] / max(1, len(corpus))) * min(2.0, weights.get(term, 1.0)))
            findings.append({
                "themeId": f"theme:{term}",
                "thoughtIds": sorted(member_ids),
                "label": label or "Untitled theme",
                "authority": "AUTOMATICALLY_DISCOVERED_THEME",
                "strength": round(strength, 6),
                "sharedTerms": [term],
                "occurrences": len(members),
                "citations": [citation(corpus[index], f"{term}: " + _excerpt(
                    corpus[index], corpus[index].get("currentText"))) for index in members[:4]],
                "note": "A recurring term across separate captures, not a claim about what any capture means.",
            })
            if len(findings) >= limit:
                break
        return {
            "findings": findings,
            "note": (
                f"Examined {len(corpus)} active captures. A theme is a derived recurrence of a "
                "distinctive term; it is never presented as something you named."
            ),
        }

    # ------------------------------------------------------------- contradictions --

    def contradictions(self, *, thought_id: str | None = None, query: str = "",
                       limit: int = 8, include_graveyard: bool = False,
                       **_kwargs: Any) -> dict[str, Any]:
        corpus = self._corpus(include_graveyard=include_graveyard)
        if thought_id or query:
            corpus = [item for item in self._relevant(query, thought_id, limit + 4)
                      if item["id"] != thought_id]
        features = [_features(item) for item in corpus]
        weights = _idf(features)
        cues: list[tuple[bool, bool, bool]] = []
        for item in corpus:
            text = str(item.get("currentText") or "")
            cues.append((bool(_NEGATION.search(text)), bool(_AFFIRMATION.search(text)),
                         bool(_CONFLICT_CUE.search(text))))
        findings: list[dict[str, Any]] = []
        pairs: list[tuple[float, int, int]] = []
        for first in range(len(corpus)):
            for second in range(first + 1, len(corpus)):
                shared = set(features[first]) & set(features[second])
                if len(shared) < 2:
                    continue
                polarity_conflict = (cues[first][0] and cues[second][1]) or \
                                    (cues[second][0] and cues[first][1])
                explicit = cues[first][2] or cues[second][2]
                if not polarity_conflict and not explicit:
                    continue
                weight = sum(weights.get(term, 1.0) for term in shared)
                pairs.append((weight, first, second))
        pairs.sort(key=lambda item: (-item[0], corpus[item[1]]["id"], corpus[item[2]]["id"]))
        for weight, first, second in pairs:
            left, right = corpus[first], corpus[second]
            shared = sorted(set(features[first]) & set(features[second]))
            findings.append({
                "thoughtIds": [left["id"], right["id"]],
                "tension": round(min(1.0, weight / 10.0), 6),
                "sharedTerms": shared[:8],
                "kind": "possible_contradiction",
                "left": citation(left),
                "right": citation(right),
                "caveat": (
                    "A possible tension detected from opposing cues and shared terms. Read both "
                    "original captures before treating it as a real contradiction."
                ),
            })
            if len(findings) >= limit:
                break
        return {
            "findings": findings,
            "primaryThoughtId": thought_id,
            "note": (
                f"Compared {len(corpus)} captures. A flagged pair is a prompt to reread, never a "
                "verdict about which thought is right."
            ),
        }

    # ----------------------------------------------------------------- evolution --

    def evolution(self, *, thought_id: str | None = None, query: str = "",
                  limit: int = 8, include_graveyard: bool = False,
                  **_kwargs: Any) -> dict[str, Any]:
        if not thought_id and not query.strip():
            raise ValueError("thoughtId or query is required for evolution")
        if thought_id:
            self.vault.get_thought(thought_id)
            related = self._relevant(query or str(self.vault.get_thought(thought_id).get("currentText") or ""),
                                     thought_id, limit)
            ids = [thought_id] + [item["id"] for item in related if item["id"] != thought_id][:limit]
        else:
            ids = [item["id"] for item in self._relevant(query, None, limit)]
        chronology: list[dict[str, Any]] = []
        cited: list[str] = []
        for thought_id_value in ids:
            thought = self.vault.get_thought(thought_id_value)
            cited.append(thought_id_value)
            ref = citation(thought)
            chronology.append({
                "thoughtId": thought_id_value, "kind": "ORIGINAL_CAPTURE",
                "timestamp": thought.get("createdAt"), "title": ref["title"],
                "excerpt": _excerpt(thought, thought.get("originalText") or ""),
                "authority": "CANONICAL_ORIGINAL",
            })
            for revision in sorted(thought.get("revisions", []),
                                   key=lambda item: (item.get("createdAt", ""), item.get("revisionNumber", 0))):
                chronology.append({
                    "thoughtId": thought_id_value, "kind": "REVISION",
                    "timestamp": revision.get("createdAt"),
                    "revisionNumber": revision.get("revisionNumber"),
                    "changeReason": revision.get("changeReason") or "",
                    "title": ref["title"],
                    "excerpt": _excerpt(thought, revision.get("text") or ""),
                    "authority": "CANONICAL_REVISION",
                })
            for event in thought.get("events", []):
                if event.get("eventType") not in _EVENT_KINDS:
                    continue
                chronology.append({
                    "thoughtId": thought_id_value, "kind": "EVENT",
                    "timestamp": event.get("timestamp"),
                    "eventType": event.get("eventType"),
                    "title": ref["title"],
                    "excerpt": "",
                    "authority": "CANONICAL_EVENT",
                })
        chronology.sort(key=lambda item: (
            item.get("timestamp") or "", item.get("thoughtId") or "",
            {"ORIGINAL_CAPTURE": 0, "REVISION": 1, "EVENT": 2}.get(item["kind"], 3),
            item.get("revisionNumber") or 0,
        ))
        findings = [{
            "thoughtIds": cited,
            "timestamp": item.get("timestamp"),
            "kind": item["kind"],
            "title": item["title"],
            "excerpt": item["excerpt"],
            "eventType": item.get("eventType"),
            "revisionNumber": item.get("revisionNumber"),
            "changeReason": item.get("changeReason"),
            "authority": item["authority"],
        } for item in chronology[:max(20, limit * 4)]]
        return {
            "findings": findings,
            "primaryThoughtId": thought_id or (cited[0] if cited else None),
            "chronologyLength": len(chronology),
            "note": (
                "A derived chronology built from recorded captures, revisions and events. The "
                "order reflects timestamps; it does not claim to know your intent."
            ),
        }

    def _relevant(self, query: str, thought_id: str | None, limit: int) -> list[dict[str, Any]]:
        """Lexical candidate set for 'how did this thinking change?' questions."""
        if thought_id:
            source = self.vault.get_thought(thought_id)
            text = " ".join([str(source.get("title") or ""),
                             str(source.get("currentText") or "")[:1200]])
            for tag in source.get("tags", []):
                text += " " + _tag_name(tag)
        else:
            text = str(query or "")
        if not text.strip():
            return []
        outcome = self.vault.recall(text, limit=max(1, min(limit, 20)))
        return [self.vault.get_thought(item.thought_id) for item in outcome.results
                if getattr(item, "thought_id", None)]

    # ------------------------------------------------------------------ explain --

    def explain(self, *, query: str = "", thought_ids: Sequence[str] | None = None,
                limit: int = 8, use_provider: bool = False,
                include_graveyard: bool = False, **_kwargs: Any) -> dict[str, Any]:
        ids = list(dict.fromkeys(str(item) for item in (thought_ids or [])[:20]))
        if not ids:
            ids = [item["id"] for item in self._relevant(query, None, limit)]
        if not ids:
            return {
                "findings": [],
                "summary": "I found no original thoughts to ground an explanation in.",
                "note": "No answer is better than an invented one. Try a different phrase or select thoughts.",
            }
        thoughts = [self.vault.get_thought(item) for item in ids]
        features = [_features(item) for item in thoughts]
        weights = _idf(features)
        common: Counter[str] = Counter()
        for item in features:
            common.update(item.keys())
        shared = sorted((term for term, count in common.items() if count >= 2),
                        key=lambda term: (-common[term], -weights.get(term, 1.0), term))[:8]
        timestamps = sorted(str(item.get("createdAt") or "") for item in thoughts if item.get("createdAt"))
        revision_count = sum(len(item.get("revisions", [])) for item in thoughts)
        observations: list[dict[str, Any]] = [{
            "observation": (
                f"{len(thoughts)} original capture(s) were selected"
                + (f", spanning {timestamps[0]} to {timestamps[-1]}" if len(timestamps) > 1 else "")
                + f", with {revision_count} recorded revision(s)."
            ),
            "evidence": [citation(item) for item in thoughts[:4]],
            "uncertainty": "Counts and dates come from stored records, not from the meaning of the text.",
        }]
        if shared:
            observations.append({
                "observation": "The selected captures repeatedly use: " + ", ".join(shared) + ".",
                "evidence": [citation(item) for item in thoughts[:4]],
                "uncertainty": "Shared words are a clue, not proof that the thoughts agree.",
            })
        findings = [{
            "thoughtIds": [item["id"] for item in thoughts],
            "observation": entry["observation"],
            "evidence": entry["evidence"],
            "uncertainty": entry["uncertainty"],
            "authority": AUTHORITY,
        } for entry in observations]
        result: dict[str, Any] = {
            "findings": findings,
            "primaryThoughtId": thoughts[0]["id"],
            "summary": " ".join(entry["observation"] for entry in observations),
            "note": (
                "Every statement below is tied to a real capture. Interpretations are proposals, "
                "and the original text remains authoritative."
            ),
        }
        if self.provider and use_provider:
            try:
                result["providerView"] = self._provider_view(thoughts[0])
            except Exception as exc:
                result["_warnings"] = [f"The provider could not add an interpretation: {exc}"]
        return result

    def _provider_view(self, thought: dict[str, Any]) -> dict[str, Any]:
        view = self.provider.interpret(thought)  # type: ignore[union-attr]
        if not isinstance(view, dict) or not isinstance(view.get("summary"), str):
            raise ValueError("The provider returned an incomplete interpretation")
        for key in ("concepts", "openQuestions", "possibleNextSteps"):
            value = view.get(key, [])
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"The provider returned invalid {key}")
            view[key] = [item.strip()[:300] for item in value[:8] if item.strip()]
        return {
            "authority": "AI_INTERPRETATION_PROPOSAL",
            "thoughtId": thought["id"],
            "citation": citation(thought),
            "summary": view["summary"].strip()[:2000],
            "concepts": view.get("concepts", []),
            "openQuestions": view.get("openQuestions", []),
            "possibleNextSteps": view.get("possibleNextSteps", []),
            "caveat": (
                "This is one provider's reading of one capture. It is a proposal, not a fact, "
                "and it never changes the original text."
            ),
        }
