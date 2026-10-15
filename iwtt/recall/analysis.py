"""Recall V2 — query understanding.

Derived, cheap, deterministic, and model-free.  This is stage 0 of the pipeline
the Graphify audit sketches in ``IWTT_RECALL_PIPELINE.md`` §2, and the specific
part the audit says IWTT must design for itself: **memory-filler stripping**.
Graphify's stopword list is question-word based; a person remembering a thought
writes "I remember thinking something about …", and those words carry no
retrieval information at all.

Two rules keep this honest:

1. Stripping is only accepted when at least one non-stopword token survives, so a
   query that is *entirely* filler still seeds on something (the same fallback
   v0.1's ``tokenize`` already applies).
2. Both the stripped and the raw normalized query are kept as exact-phrase
   candidates.  Stripping widens recall; it never destroys the ability to match
   the literal words the user typed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..store import STOPWORDS, THOUGHT_TYPES, normalize_text, tokenize
from .filters import TimeWindow, detect_time_hint
from .text import strip_memory_filler, token_variants

#: Words that hint at a lifecycle type inside a natural-language query.
TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "EXPERIMENT": ("experiment", "experiments", "experimental", "prototype", "prototypes", "tried", "testing"),
    "INVENTION": ("invention", "inventions", "invented", "device", "gadget", "mechanism"),
    "PROJECT": ("project", "projects", "shipped", "built", "building", "release"),
    "THOUGHT": ("thought", "thoughts", "idea", "ideas", "note", "notes", "musing"),
}

#: Words that hint the user is reaching for the Graveyard.  Per the audit these
#: must ANNOTATE, never silently exclude: an abandoned-and-resurrected thought is
#: often exactly what the user is reaching for.
GRAVEYARD_HINTS = (
    "abandoned", "abondoned", "buried", "dead", "dropped", "gave up",
    "graveyard", "killed", "paused", "quit", "scrapped", "shelved",
)
RESURRECTION_HINTS = ("resurrected", "revived", "returned", "came back", "brought back")

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class QueryTerm:
    """One query term plus everything IWTT knows how to match it with."""

    term: str
    variants: frozenset[str]
    idf: float = 0.0
    is_stopword: bool = False

    @property
    def forms(self) -> tuple[str, ...]:
        return tuple(sorted(self.variants))


@dataclass(frozen=True)
class QueryAnalysis:
    raw: str
    normalized: str
    effective: str
    terms: tuple[QueryTerm, ...]
    #: Content terms (stopwords removed) — what coverage and IDF are measured on.
    content_terms: tuple[QueryTerm, ...]
    #: Normalized phrases to test for exact-phrase matches, most specific first.
    phrase_candidates: tuple[str, ...]
    #: Words used to build the trigram vector.
    trigram_words: tuple[str, ...]
    time_hint: TimeWindow | None = None
    type_hints: tuple[str, ...] = ()
    graveyard_hint: bool = False
    resurrection_hint: bool = False
    filler_stripped: bool = False
    #: Honest record of what IWTT did and did not do to the query.
    notes: tuple[str, ...] = ()

    @property
    def term_strings(self) -> tuple[str, ...]:
        return tuple(term.term for term in self.content_terms) or tuple(
            term.term for term in self.terms
        )

    @property
    def n_terms(self) -> int:
        return len(self.term_strings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "normalized": self.normalized,
            "effective": self.effective,
            "fillerStripped": self.filler_stripped,
            "terms": [
                {"term": t.term, "variants": list(t.forms), "idf": round(t.idf, 4),
                 "stopword": t.is_stopword}
                for t in self.terms
            ],
            "contentTerms": [t.term for t in self.content_terms],
            "phraseCandidates": list(self.phrase_candidates),
            "trigramWords": list(self.trigram_words),
            "timeHint": self.time_hint.to_dict() if self.time_hint else None,
            "typeHints": list(self.type_hints),
            "graveyardHint": self.graveyard_hint,
            "resurrectionHint": self.resurrection_hint,
            "notes": list(self.notes),
            "semanticStage": "disabled — no embeddings, no model, no network",
        }


def _build_terms(text: str) -> tuple[QueryTerm, ...]:
    tokens = tokenize(text, remove_stopwords=False)
    seen: dict[str, QueryTerm] = {}
    for token in tokens:
        if token in seen:
            continue
        seen[token] = QueryTerm(
            term=token,
            variants=frozenset(token_variants(token)),
            is_stopword=token in STOPWORDS,
        )
    return tuple(seen.values())


def analyze_query(
    query: str,
    *,
    now: datetime | None = None,
    idf_lookup: Any = None,
) -> QueryAnalysis:
    """Turn a remembered fragment into a deterministic retrieval plan.

    ``idf_lookup`` is an optional callable ``term -> idf`` supplied by the engine
    once the derived term statistics are known.  Analysis itself never touches
    the database, which keeps it unit-testable and side-effect free.
    """
    raw = (query or "").strip()
    normalized = normalize_text(raw)
    effective = strip_memory_filler(raw)
    filler_stripped = effective != normalized

    notes: list[str] = []
    if filler_stripped:
        notes.append("recall boilerplate removed before matching")
    if not normalized:
        return QueryAnalysis(
            raw=raw,
            normalized="",
            effective="",
            terms=(),
            content_terms=(),
            phrase_candidates=(),
            trigram_words=(),
            notes=tuple(notes) or ("empty query",),
        )

    terms = _build_terms(effective)
    content = tuple(term for term in terms if not term.is_stopword) or terms

    # Exact-phrase candidates: the stripped query first (most likely to be the
    # user's actual remembered wording), then the full normalized query, then the
    # raw normalized text of what they typed.  Deduplicated, order preserved.
    phrases: list[str] = []
    for candidate in (effective, normalized):
        candidate = _WHITESPACE.sub(" ", candidate).strip()
        if candidate and candidate not in phrases:
            phrases.append(candidate)

    trigram_words = tuple(
        token for token in tokenize(effective, remove_stopwords=False) if len(token) >= 2
    )

    lowered = f" {effective} "
    type_hints: list[str] = []
    for thought_type in sorted(THOUGHT_TYPES):
        if any(f" {word} " in lowered for word in TYPE_HINTS.get(thought_type, ())):
            type_hints.append(thought_type)
    graveyard_hint = any(f" {word} " in lowered for word in GRAVEYARD_HINTS)
    resurrection_hint = any(word in lowered for word in RESURRECTION_HINTS)
    if graveyard_hint:
        notes.append("query mentions abandonment — Graveyard items are annotated, not hidden")
    if resurrection_hint:
        notes.append("query mentions revival — resurrection history is used as context")

    time_hint = detect_time_hint(effective, now=now)
    if time_hint:
        notes.append(f"time hint detected: {time_hint.label}")

    if idf_lookup is not None:
        terms = tuple(
            QueryTerm(t.term, t.variants, float(idf_lookup(t.term)), t.is_stopword) for t in terms
        )
        content = tuple(term for term in terms if not term.is_stopword) or terms

    return QueryAnalysis(
        raw=raw,
        normalized=normalized,
        effective=effective,
        terms=terms,
        content_terms=content,
        phrase_candidates=tuple(phrases),
        trigram_words=trigram_words,
        time_hint=time_hint,
        type_hints=tuple(type_hints),
        graveyard_hint=graveyard_hint,
        resurrection_hint=resurrection_hint,
        filler_stripped=filler_stripped,
        notes=tuple(notes),
    )
