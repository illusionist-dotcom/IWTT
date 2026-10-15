"""Recall V2 — derived query-outcome memory.

This is the "work memory" concept the Graphify audit identifies as the strongest
IWTT-shaped subsystem in that repository (``README.md`` conclusion 2):
outcome signals aggregated with a half-life, **corroboration-gated** so a single
save cannot mint a trusted lesson, written to a *derived sidecar* that never
contaminates canonical data, and merged into output at display time.

WHAT THIS IS NOT
----------------
It is **not** canonical.  It never modifies a thought, a revision, a tag, a
relationship, an event, or a Graveyard record.  It writes to exactly one table,
``recall_query_memory``, which ``Vault.clear_derived_data()`` deletes and
``reset_learning()`` empties.  Deleting it cannot damage the vault.

DELIBERATE DEPARTURE FROM THE DOSSIER
-------------------------------------
The audit's matrix item #17 suggests mapping outcome signals onto *canonical
events*.  v0.2 **rejects** that.  The mission states "DO NOT make query-learning
outcomes canonical", and writing retrieval feedback into the canonical event log
would make derived ranking behaviour part of the user's permanent history and
would survive a "reset recall learning" operation.  Canonical events stay
reserved for things the user did to their *thoughts*.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..store import Vault, new_id, now_utc
from .config import DEFAULT_CONFIG, RecallConfig
from .index import RecallIndex
from .text import normalize_text, strip_memory_filler

#: Feedback vocabulary and raw weights.  Explicit feedback outranks implicit
#: behaviour: opening a result is a *weak* signal and is never authoritative.
FEEDBACK_WEIGHTS: dict[str, float] = {
    "USEFUL": 1.0,
    "NOT_USEFUL": -1.0,
    "CORRECTION": 0.6,
    "OPENED": 0.15,
}

#: Feedback that counts as an explicit user judgement (for corroboration).
EXPLICIT_FEEDBACK = frozenset({"USEFUL", "NOT_USEFUL", "CORRECTION"})


def memory_key(query: str) -> str:
    """Normalized, memory-filler-stripped query used as the learning key.

    "I remember thinking something about adaptive enemies" and "adaptive enemies"
    therefore share one memory, which is what makes the learning useful at all.
    """
    return strip_memory_filler(query) or normalize_text(query)


@dataclass
class MemoryValue:
    thought_id: str
    value: float                 # clamped, decayed, gate-adjusted
    raw: float                   # decayed sum before clamping/gating
    events: int
    explicit_events: int
    corroborated: bool
    detail: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "thoughtId": self.thought_id,
            "value": round(self.value, 6),
            "raw": round(self.raw, 6),
            "events": self.events,
            "explicitEvents": self.explicit_events,
            "corroborated": self.corroborated,
            "detail": self.detail,
        }


def _ensure(conn: sqlite3.Connection, index: RecallIndex) -> None:
    index.ensure_schema(conn)


def _parse(timestamp: str) -> datetime | None:
    if not timestamp:
        return None
    text = timestamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def record_feedback(
    index: RecallIndex,
    query: str,
    thought_id: str,
    feedback: str,
    *,
    rank: int | None = None,
    score: float | None = None,
    note: str = "",
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Store one derived feedback event.

    Raises ``ValueError`` for an unknown feedback kind, ``KeyError`` for an
    unknown thought.  Writes **only** to ``recall_query_memory``.
    """
    kind = (feedback or "").upper().strip()
    if kind not in FEEDBACK_WEIGHTS:
        raise ValueError(
            f"feedback must be one of: {', '.join(sorted(FEEDBACK_WEIGHTS))}"
        )
    key = memory_key(query)
    if not key:
        raise ValueError("feedback requires a non-empty query")
    vault: Vault = index.vault
    # ``recorded_at`` exists so decay can be tested deterministically and so a
    # caller can record an event that genuinely happened earlier.  It is still a
    # derived row; canonical timestamps are never involved.
    timestamp = (recorded_at or "").strip() or now_utc()
    record_id = new_id()
    with vault._transaction() as conn:
        _ensure(conn, index)
        if not conn.execute("SELECT 1 FROM thoughts WHERE id=?", (thought_id,)).fetchone():
            raise KeyError(f"Thought not found: {thought_id}")
        conn.execute(
            "INSERT INTO recall_query_memory(id,query_norm,thought_id,feedback,weight,"
            "rank_at_time,score_at_time,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (record_id, key, thought_id, kind, FEEDBACK_WEIGHTS[kind], rank, score,
             (note or "")[:500], timestamp),
        )
    return {
        "id": record_id,
        "queryNorm": key,
        "thoughtId": thought_id,
        "feedback": kind,
        "weight": FEEDBACK_WEIGHTS[kind],
        "createdAt": timestamp,
        "canonical": False,
        "derived": True,
    }


def usefulness(
    index: RecallIndex,
    query: str,
    *,
    now: datetime | None = None,
    config: RecallConfig = DEFAULT_CONFIG,
) -> dict[str, MemoryValue]:
    """Decayed, corroboration-gated usefulness per thought for one query.

    DECAY MODEL (documented, deterministic)

        decay(event) = 0.5 ** (age_days / HALF_LIFE_DAYS)
        raw(t)       = Σ  weight(event) · decay(event)
        gated(t)     = raw(t) · SINGLE_OBSERVATION_FACTOR   if explicit events < MIN_CORROBORATION
                       raw(t)                               otherwise
        value(t)     = clamp(gated(t), NEGATIVE_CAP, POSITIVE_CAP)

    with ``HALF_LIFE_DAYS = 180`` (the audit's ADAPT #14 notes personal thoughts
    decay much slower than code facts and recommends ~180 days over Graphify's
    30 — configurable), ``MIN_CORROBORATION = 2`` and
    ``SINGLE_OBSERVATION_FACTOR = 0.5`` (audit ADOPT: "a single save cannot mint
    a trusted lesson").

    Consequences that the tests assert:

    * one click moves ranking a little, never decisively;
    * old feedback stays informative without dominating forever;
    * negative feedback demotes but never hides — a demoted thought is still
      returned, still explained, and still opens to what you actually wrote.
    """
    key = memory_key(query)
    if not key:
        return {}
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    vault: Vault = index.vault
    half_life = float(config.query_memory_half_life_days or 0.0)
    with vault._connection() as conn:
        try:
            rows = conn.execute(
                "SELECT thought_id, feedback, weight, created_at, rank_at_time, score_at_time "
                "FROM recall_query_memory WHERE query_norm=? ORDER BY created_at, id",
                (key,),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}

    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    explicit: dict[str, int] = {}
    detail: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        thought_id = row["thought_id"]
        created = _parse(row["created_at"])
        age_days = max(0.0, (moment - created).total_seconds() / 86400.0) if created else 0.0
        decay = 0.5 ** (age_days / half_life) if half_life > 0 else 1.0
        weight = float(row["weight"]) * decay
        totals[thought_id] = totals.get(thought_id, 0.0) + weight
        counts[thought_id] = counts.get(thought_id, 0) + 1
        if row["feedback"] in EXPLICIT_FEEDBACK:
            explicit[thought_id] = explicit.get(thought_id, 0) + 1
        detail.setdefault(thought_id, []).append({
            "feedback": row["feedback"],
            "rawWeight": float(row["weight"]),
            "ageDays": round(age_days, 4),
            "decay": round(decay, 6),
            "decayedWeight": round(weight, 6),
            "createdAt": row["created_at"],
            "rankAtTime": row["rank_at_time"],
            "scoreAtTime": row["score_at_time"],
        })

    out: dict[str, MemoryValue] = {}
    for thought_id in sorted(totals):
        raw = totals[thought_id]
        explicit_count = explicit.get(thought_id, 0)
        corroborated = explicit_count >= max(1, config.query_memory_min_corroboration)
        gated = raw if corroborated else raw * config.query_memory_single_observation_factor
        value = max(config.query_memory_negative_cap, min(config.query_memory_positive_cap, gated))
        out[thought_id] = MemoryValue(
            thought_id=thought_id,
            value=value,
            raw=raw,
            events=counts.get(thought_id, 0),
            explicit_events=explicit_count,
            corroborated=corroborated,
            detail=detail.get(thought_id, []),
        )
    return out


def signal_for(
    index: RecallIndex,
    query: str,
    thought_id: str,
    *,
    now: datetime | None = None,
    config: RecallConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """The contribution dict the scorer consumes for one (query, thought) pair."""
    values = usefulness(index, query, now=now, config=config)
    entry = values.get(thought_id)
    if entry is None:
        return {"value": 0.0, "events": 0, "corroborated": False, "detail": ()}
    return {
        "value": entry.value,
        "events": entry.events,
        "corroborated": entry.corroborated,
        "detail": tuple(entry.detail),
    }


def reset_learning(index: RecallIndex) -> dict[str, Any]:
    """RESET RECALL LEARNING.

    Deletes every derived query-memory row and nothing else.  Thoughts,
    revisions, tags, relationships, events, lifecycle state and Graveyard
    history are untouched; the base lexical/trigram indexes are left in place, so
    search keeps working immediately (and rebuilds itself if it was also wiped).
    """
    vault: Vault = index.vault
    timestamp = now_utc()
    with vault._transaction() as conn:
        _ensure(conn, index)
        removed = int(conn.execute("SELECT COUNT(*) FROM recall_query_memory").fetchone()[0])
        distinct = int(
            conn.execute("SELECT COUNT(DISTINCT query_norm) FROM recall_query_memory").fetchone()[0]
        )
        conn.execute("DELETE FROM recall_query_memory")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='recall_query_memory'")
        index._set_meta(conn, "learning_reset_at", timestamp)
    return {
        "deletedEvents": removed,
        "deletedQueries": distinct,
        "resetAt": timestamp,
        "canonical": False,
        "derived": True,
        "note": (
            "Derived query memory deleted. Thoughts, revisions, tags, relationships, events, "
            "lifecycle state and Graveyard history are unchanged."
        ),
    }


def stats(index: RecallIndex) -> dict[str, Any]:
    vault: Vault = index.vault
    with vault._connection() as conn:
        try:
            total = int(conn.execute("SELECT COUNT(*) FROM recall_query_memory").fetchone()[0])
            queries = int(
                conn.execute("SELECT COUNT(DISTINCT query_norm) FROM recall_query_memory").fetchone()[0]
            )
            by_feedback = {
                row["feedback"]: int(row["n"])
                for row in conn.execute(
                    "SELECT feedback, COUNT(*) AS n FROM recall_query_memory GROUP BY feedback "
                    "ORDER BY feedback"
                )
            }
            thoughts = int(
                conn.execute("SELECT COUNT(DISTINCT thought_id) FROM recall_query_memory").fetchone()[0]
            )
            reset_at = index._get_meta(conn, "learning_reset_at")
        except sqlite3.OperationalError:
            return {"events": 0, "queries": 0, "thoughts": 0, "byFeedback": {},
                    "learningResetAt": None, "available": False}
    return {
        "available": True,
        "events": total,
        "queries": queries,
        "thoughts": thoughts,
        "byFeedback": by_feedback,
        "learningResetAt": reset_at,
        "halfLifeDays": index.config.query_memory_half_life_days,
        "minCorroboration": index.config.query_memory_min_corroboration,
        "singleObservationFactor": index.config.query_memory_single_observation_factor,
        "canonical": False,
        "derived": True,
    }


def history(index: RecallIndex, limit: int = 100) -> list[dict[str, Any]]:
    """Inspectable log of what has been learned.  Derived, and deletable."""
    vault: Vault = index.vault
    with vault._connection() as conn:
        try:
            rows = conn.execute(
                "SELECT m.*, COALESCE(t.title,'') AS title, COALESCE(t.current_text,'') AS current_text, "
                "COALESCE(t.original_text,'') AS original_text "
                "FROM recall_query_memory m LEFT JOIN thoughts t ON t.id = m.thought_id "
                "ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    from ..store import _display_title

    out = []
    for row in rows:
        out.append({
            "id": row["id"],
            "queryNorm": row["query_norm"],
            "thoughtId": row["thought_id"],
            "displayTitle": _display_title(row["title"] or "", row["current_text"] or row["original_text"]),
            "feedback": row["feedback"],
            "weight": float(row["weight"]),
            "rankAtTime": row["rank_at_time"],
            "scoreAtTime": row["score_at_time"],
            "note": row["note"],
            "createdAt": row["created_at"],
        })
    return out


def learned_queries(index: RecallIndex, limit: int = 50) -> list[dict[str, Any]]:
    vault: Vault = index.vault
    with vault._connection() as conn:
        try:
            rows = conn.execute(
                "SELECT query_norm, COUNT(*) AS events, COUNT(DISTINCT thought_id) AS thoughts, "
                "MAX(created_at) AS last_at FROM recall_query_memory "
                "GROUP BY query_norm ORDER BY events DESC, query_norm ASC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [
        {
            "queryNorm": row["query_norm"],
            "events": int(row["events"]),
            "thoughts": int(row["thoughts"]),
            "lastAt": row["last_at"],
        }
        for row in rows
    ]
