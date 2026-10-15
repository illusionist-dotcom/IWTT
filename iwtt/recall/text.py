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
    """v0.1's morphology + alias expansion, exposed under a public name."""
    return _token_variants(token)


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


def word_trigrams(word: str) -> set[str]:
    """Padded character trigrams of one normalized word.

    Padding with two leading spaces and one trailing space makes prefixes and
    suffixes produce distinctive grams and lets short words yield at least one
    gram, so a fragment query like ``"enemy strat"`` still overlaps
    ``"strategy"``.
    """
    normalized = normalize_text(word).replace(" ", "")
    if not normalized:
        return set()
    padded = "  " + normalized + " "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


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
        forms |= token_variants(token)
    return forms


def fuzzy_ratio(a: str, b: str) -> float:
    """``difflib`` similarity over two already-bounded strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, normalize_text(a), normalize_text(b), autojunk=False).ratio()


def edit_distance(a: str, b: str, cap: int = 6) -> int:
    """Bounded Levenshtein distance; returns ``cap`` once the true distance
    reaches it.  Used for typo explanations, never as a bulk ranking signal."""
    left = normalize_text(a)
    right = normalize_text(b)
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
        for form in sorted(token_variants(token)):
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
