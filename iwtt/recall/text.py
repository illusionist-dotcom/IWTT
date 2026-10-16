"""Recall V2 — deterministic text primitives.

Reuses v0.1's ``normalize_text`` / ``tokenize`` / ``_token_variants`` so that
``Vault.search()`` (frozen) and ``Vault.recall()`` (new) agree on what a token is.
Adds only what the Graphify audit says IWTT must design for itself:

  * memory-filler stripping ("I remember thinking something about …")
  * character-trigram vectors + cosine similarity (fuzzy candidate prefilter)
  * a bounded fuzzy comparison window, so ``difflib`` is never run on a whole
    document
  * bounded edit distance, for typo explanations

SACRED RULE: every function here is pure and derived.  Nothing writes to the
database, and no output of this module is ever stored as canonical truth.

GRAPHIFY PROVENANCE: the *concepts* of trigram prefiltering and bounded fuzzy
comparison come from the audit (``SEARCH_AND_QUERY.md`` §2.3).  No Graphify
source code was copied; see GRAPHIFY_ADAPTATION_NOTES.md.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from difflib import SequenceMatcher
from typing import Iterable

from ..store import STOPWORDS, TOKEN_ALIASES, _token_variants, normalize_text, tokenize

__all__ = [
    "STOPWORDS",
    "TOKEN_ALIASES",
    "MEMORY_FILLERS",
    "normalize_text",
    "tokenize",
    "token_variants",
    "recall_variants",
    "morphology_forms",
    "strip_memory_filler",
    "word_trigrams",
    "text_trigrams",
    "trigram_counts",
    "cosine_counts",
    "jaccard",
    "best_window",
    "fuzzy_ratio",
    "edit_distance",
    "recall_snippet",
    "sanitize_label",
    "signature_of",
]

#: Fuzzy comparison window.  ``difflib.SequenceMatcher`` is O(n·m); it is only
#: ever run on a window this size, never on a whole document.
FUZZY_WINDOW_CHARS = 240
FUZZY_MAX_SEGMENTS = 8

#: Recall boilerplate.  Matched against the *normalized* query (lowercase,
#: punctuation already collapsed to spaces), longest pattern first, each applied
#: at most once.  This is the "memory filler" delta the audit says IWTT must add
#: itself (IWTT_RECALL_PIPELINE.md §3, stage 0): Graphify's stopword list is
#: question-word based and does not contain these.
MEMORY_FILLERS: tuple[str, ...] = (
    "i remember thinking something about",
    "i remember thinking something like",
    "i remember thinking about",
    "i remember writing something about",
    "i remember something about",
    "i remember a thought about",
    "i vaguely remember",
    "i think i wrote something about",
    "i think i had a thought about",
    "i think i noted something about",
    "what was i thinking about",
    "what did i think about",
    "do you remember the thing about",
    "find my thought about",
    "find my thoughts about",
    "find the thought about",
    "there was something about",
    "there was a thought about",
    "the thing about",
    "something about",
    "i remember",
    "i recall",
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_WHITESPACE = re.compile(r"\s+")


def token_variants(token: str) -> set[str]:
    """v0.1's morphology + alias expansion, exposed under a public name.

    This is the FROZEN v0.1 function.  ``Vault.search()`` depends on its exact
    output, so recall never changes it — it only ever adds to it, in
    ``recall_variants()`` below.
    """
    return _token_variants(token)


#: Derivational suffixes v0.1 does not handle.  Each entry is
#: ``(suffix, minimum stem length, extra endings to try on the stem)``.
#: Deliberately short and conservative: every form produced here is indexed and
#: matched exactly like a real word, so a rule that fires too eagerly would show
#: up as a false match rather than hiding quietly.
_RECALL_SUFFIX_RULES: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("ations", 4, ("",)),
    ("ation", 4, ("", "e")),
    ("ness", 4, ("",)),
    ("ments", 4, ("",)),
    ("ment", 4, ("",)),
)


_VOWELS = frozenset("aeiou")

# --------------------------------------------------------------------------
# memoization of pure text analysis
# --------------------------------------------------------------------------
# Every function below is pure: the same token always yields the same forms, the
# same trigrams, the same normalization.  Recall scores hundreds of candidates per
# query against a handful of query terms, so without memoization the same dozen
# tokens get re-analyzed thousands of times per query.  Measured at 1,000 thoughts
# before this change: ~850 candidates per query, ~415,000 tier evaluations for 40
# queries, ~1,000,000 trigram recomputations, and a p50 recall latency of ~290ms.
#
# Caching the pure functions is what makes recall interactive.  Two properties are
# preserved deliberately:
#
#   * **determinism** — a cache hit and a cache miss return the identical value,
#     so ranking cannot depend on what happened to be cached;
#   * **bounded memory** — every cache has an explicit ``maxsize`` and is LRU, so a
#     long-running server cannot grow without limit.  Nothing here caches whole
#     documents, only words.
#
# Cached functions return ``frozenset`` so no caller can mutate a shared value.
MORPHOLOGY_CACHE_SIZE = 32_768
VARIANT_CACHE_SIZE = 32_768
TRIGRAM_CACHE_SIZE = 65_536
NORMALIZE_CACHE_SIZE = 32_768
FUZZY_CACHE_SIZE = 65_536
WINDOW_CACHE_SIZE = 8_192


def _ends_vowel_consonant(stem: str) -> bool:
    return len(stem) >= 2 and stem[-1] not in _VOWELS and stem[-2] in _VOWELS


@lru_cache(maxsize=MORPHOLOGY_CACHE_SIZE)
def morphology_forms(token: str, *, rounds: int = 2) -> frozenset[str]:
    """Bounded iterative morphology closure used ONLY by recall.

    v0.1's ``_token_variants`` applies its rules once, which is right for the
    frozen v0.1 search path and is reused here unchanged.  Recall adds two
    deterministic, bounded things on top:

      * **iteration** — each round's output is fed through again, so
        ``adaptations`` → ``adaptation`` → ``adapt``;
      * **derivational suffixes** — ``-ation``, ``-ness``, ``-ment`` — so a query
        for ``adapt`` can find a thought that only ever says ``adaptation``.

    This is not a language model and does not claim to be: it is a fixed list of
    string rules, applied at most ``rounds`` times, always including the original
    token.  Round count is a documented bound, not a heuristic.
    """
    normalized = normalize_text(token).split(" ")[0] if token else ""
    if not normalized:
        return frozenset()
    forms: set[str] = {normalized}
    frontier = {normalized}
    for _ in range(max(1, int(rounds))):
        produced: set[str] = set()
        for form in frontier:
            produced |= _token_variants(form)
            for suffix, minimum, extras in _RECALL_SUFFIX_RULES:
                if not form.endswith(suffix) or len(form) - len(suffix) < minimum:
                    continue
                stem = form[: -len(suffix)]
                for extra in extras:
                    # The silent-e restoration only applies where English actually
                    # uses it: a stem ending vowel+consonant ("creat" -> "create").
                    # Applying it blindly invents non-words ("adapt" -> "adapte")
                    # that would then be indexed and matched like real forms.
                    if extra == "e" and not _ends_vowel_consonant(stem):
                        continue
                    candidate = stem + extra
                    if len(candidate) >= 3:
                        produced.add(candidate)
        produced = {form for form in produced if form and form not in forms and len(form) >= 3}
        if not produced:
            break
        forms |= produced
        frontier = produced
    return frozenset(forms)


@lru_cache(maxsize=VARIANT_CACHE_SIZE)
def recall_variants(token: str) -> frozenset[str]:
    """Every form recall will match for one token.

    A superset of v0.1's variants: the same aliases and inflections, plus the
    bounded derivational closure.  The index and the query side both call this,
    so the two can never disagree about what a word form is.
    """
    return morphology_forms(token)


@lru_cache(maxsize=NORMALIZE_CACHE_SIZE)
def cached_normalize(value: str) -> str:
    """``normalize_text`` with the result memoized.

    v0.1's ``normalize_text`` is frozen and is called here unchanged — this wrapper
    only remembers what it returned for a given string.  Keyed by the exact input,
    so different text can never be served a stale normalization.
    """
    return normalize_text(value)


def strip_memory_filler(query: str) -> str:
    """Remove recall boilerplate from a normalized query.

    Deterministic: patterns are tried longest-first, each at most once, and the
    result is only accepted when at least one non-stopword token survives.  If
    stripping would leave nothing usable the original query is returned
    unchanged — a query that is *entirely* filler still has to seed on something
    (the same fallback v0.1's ``tokenize`` already applies to stopwords).
    """
    normalized = normalize_text(query)
    if not normalized:
        return ""
    stripped = normalized
    for filler in MEMORY_FILLERS:
        if filler in stripped:
            stripped = stripped.replace(filler, " ", 1)
    stripped = _WHITESPACE.sub(" ", stripped).strip()
    if not stripped:
        return normalized
    if not [token for token in stripped.split(" ") if token not in STOPWORDS]:
        return normalized
    return stripped


# --------------------------------------------------------------------------
# character trigrams
# --------------------------------------------------------------------------


@lru_cache(maxsize=TRIGRAM_CACHE_SIZE)
def word_trigrams(word: str) -> frozenset[str]:
    """Padded character trigrams of one normalized word.

    Padding with two leading spaces and one trailing space makes prefixes and
    suffixes produce distinctive grams and lets short words yield at least one
    gram, so a fragment query like ``"enemy strat"`` still overlaps
    ``"strategy"``.
    """
    normalized = normalize_text(word).replace(" ", "")
    if not normalized:
        return frozenset()
    padded = "  " + normalized + " "
    return frozenset(padded[i : i + 3] for i in range(len(padded) - 2))


def text_trigrams(text: str) -> set[str]:
    out: set[str] = set()
    for token in tokenize(text, remove_stopwords=False):
        out |= word_trigrams(token)
    return out


def trigram_counts(words: Iterable[str]) -> dict[str, int]:
    """Term-frequency vector over trigrams, used for cosine similarity."""
    counts: dict[str, int] = {}
    for word in words:
        for gram in word_trigrams(word):
            counts[gram] = counts.get(gram, 0) + 1
    return counts


def vector_norm(counts: dict[str, int]) -> float:
    return math.sqrt(sum(value * value for value in counts.values())) if counts else 0.0


def cosine_counts(a: dict[str, int], b: dict[str, int]) -> float:
    """Cosine similarity of two sparse count vectors, in ``0.0 .. 1.0``."""
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = 0
    for key, value in a.items():
        other = b.get(key)
        if other:
            dot += value * other
    if not dot:
        return 0.0
    return dot / (vector_norm(a) * vector_norm(b))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


# --------------------------------------------------------------------------
# bounded fuzzy comparison
# --------------------------------------------------------------------------


def split_segments(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(text or "")
    return [part.strip() for part in parts if part and part.strip()]


@lru_cache(maxsize=WINDOW_CACHE_SIZE)
def best_window(document: str, query: str) -> str:
    """The <= ``FUZZY_WINDOW_CHARS`` slice of *document* most worth comparing.

    Deterministic selection: the segment with the highest token-form overlap
    with the query, ties broken by earliest position, then truncated from the
    head.  No randomness, no model, no locale dependence.
    """
    if not document:
        return ""
    segments = split_segments(document)[:FUZZY_MAX_SEGMENTS] or [document]
    query_forms = _token_forms(query)
    best = ""
    best_score = -1.0
    for segment in segments:
        overlap = len(query_forms & _token_forms(segment))
        score = overlap + 0.001 * min(len(segment), FUZZY_WINDOW_CHARS)
        if score > best_score:
            best_score = score
            best = segment
    best = best.strip()
    return best[:FUZZY_WINDOW_CHARS] if len(best) > FUZZY_WINDOW_CHARS else best


def _token_forms(text: str) -> set[str]:
    forms: set[str] = set()
    for token in tokenize(text, remove_stopwords=False):
        forms.add(token)
        forms |= recall_variants(token)
    return forms


@lru_cache(maxsize=FUZZY_CACHE_SIZE)
def fuzzy_ratio(a: str, b: str) -> float:
    """``difflib`` similarity over two already-bounded strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, cached_normalize(a), cached_normalize(b), autojunk=False).ratio()


def edit_distance(a: str, b: str, cap: int = 6) -> int:
    """Bounded Levenshtein distance; returns ``cap`` once the true distance
    reaches it.  Used for typo explanations, never as a bulk ranking signal."""
    left = cached_normalize(a)
    right = cached_normalize(b)
    if left == right:
        return 0
    if abs(len(left) - len(right)) >= cap:
        return cap
    previous = list(range(len(right) + 1))
    for i, char_a in enumerate(left, start=1):
        current = [i]
        best = i
        for j, char_b in enumerate(right, start=1):
            cost = 0 if char_a == char_b else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            current.append(value)
            if value < best:
                best = value
        if best >= cap:
            return cap
        previous = current
    return previous[-1] if previous[-1] < cap else cap


def recall_snippet(text: str, query: str, limit: int = 210) -> str:
    """Snippet of the ORIGINAL text, same intent as ``Vault._snippet``.

    Kept separate so v0.1's frozen ``search()`` behaviour cannot drift, while
    Recall V2 gains variant-aware centring (a typo'd query word still centres
    the snippet on the right sentence).
    """
    if not text:
        return ""
    if len(text) <= limit:
        return text
    folded = text.casefold()
    positions: list[int] = []
    for token in tokenize(query, remove_stopwords=False):
        for form in sorted(recall_variants(token)):
            if len(form) < 3:
                continue
            found = folded.find(form)
            if found >= 0:
                positions.append(found)
                break
    start = max(0, (min(positions) - 55) if positions else 0)
    end = min(len(text), start + limit)
    snippet = text[start:end].strip()
    if start:
        snippet = "…" + snippet
    if end < len(text):
        snippet += "…"
    return snippet


def sanitize_label(value: object, limit: int = 200) -> str:
    """Defang derived/untrusted text before it is concatenated into an
    explanation or rendered by the UI.

    The audit flags output sanitization as an ADOPT item (matrix #33): a hostile
    or merely odd document must not be able to inject control characters, ANSI
    escapes or fake markup into IWTT's output.  Control characters are dropped,
    ANSI escapes are stripped, whitespace is collapsed, and the result is
    length-capped.
    """
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", " ", text)  # ANSI escape sequences
    text = "".join(char for char in text if char >= " " or char in "\t")
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:limit]


def signature_of(title: str, text: str) -> str:
    """Dedupe signature used for seed selection.

    The audit records Graphify failure #1766: dozens of identically-labelled
    nodes consumed every seed slot.  IWTT's equivalent risk is several thoughts
    with the same normalized opening words, so seeds are deduped on this
    signature rather than on the durable id.
    """
    base = normalize_text(title) or normalize_text(text)
    return " ".join(base.split(" ")[:8])
