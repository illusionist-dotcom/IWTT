"""The opt-in semantic (embedding) stage.

This module owns the derived ``recall_embeddings`` table and the deterministic
arithmetic over it.  The design rules it inherits from the rest of Recall V2:

* **Derived and erasable.**  Every row is recomputable from canonical text via
  a provider; ``clear_derived_data()`` wipes the table and nothing canonical
  is touched.  A thought's row dies with the thought (``ON DELETE CASCADE``).
* **Off until built.**  Nothing in recall changes until the user explicitly
  builds an embedding index against a configured provider.  With no rows,
  recall is byte-identical to a purely lexical+fuzzy+graph run.
* **Never a scored signal.**  Similarity does not enter the published scoring
  formula.  It produces a separate, explicitly labelled result section so the
  two kinds of evidence are never confused.
* **Deterministic.**  Given the same table contents and query vector, the
  reported neighbours, their order and their similarity values are identical
  across repeats, restarts and rebuilds.
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
import struct
import time
from dataclasses import dataclass
from typing import Any, Sequence

from ..ai import AIUnavailable
from ..store import now_utc
from .config import DEFAULT_CONFIG, RecallConfig

#: Version marker stored in ``recall_meta`` so a change in the embedding
#: pipeline itself (not just the model) can invalidate stale rows.
EMBEDDING_PIPELINE_VERSION = "1"


def text_hash(title: str, body: str) -> str:
    """Stable fingerprint of the text an embedding was computed from."""
    material = f"{(title or '').strip()}\n{(body or '').strip()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def pack_vector(vector: Sequence[float]) -> bytes:
    """Little-endian float32 packing — compact and byte-stable."""
    return struct.pack(f"<{len(vector)}f", *(float(value) for value in vector))


def unpack_vector(blob: bytes) -> list[float]:
    values = struct.unpack(f"<{len(blob) // 4}f", blob)
    return [float(value) for value in values]


def cosine_similarity(first: Sequence[float], second: Sequence[float],
                      *, first_norm: float | None = None,
                      second_norm: float | None = None) -> float:
    """Cosine similarity with optional precomputed norms.

    Float arithmetic is done in a fixed order (left to right, dimension
    order), so the same two vectors always produce the same value.
    """
    if len(first) != len(second) or not first:
        return 0.0
    a_norm = first_norm if first_norm is not None else math.sqrt(sum(v * v for v in first))
    b_norm = second_norm if second_norm is not None else math.sqrt(sum(v * v for v in second))
    if a_norm <= 0.0 or b_norm <= 0.0:
        return 0.0
    dot = 0.0
    for index in range(len(first)):
        dot += first[index] * second[index]
    similarity = dot / (a_norm * b_norm)
    # Clamp away float drift; cosine is defined on [-1, 1].
    return max(-1.0, min(1.0, similarity))


def normalize_vector(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0.0:
        return [0.0 for _ in vector]
    return [float(value) / norm for value in vector]


def vector_norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


@dataclass
class SemanticNeighbour:
    thought_id: str
    similarity: float
    model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "thoughtId": self.thought_id,
            "similarity": round(self.similarity, 6),
            "model": self.model,
        }


def _provider_label(provider: Any) -> str:
    return str(getattr(provider, "model", None) or getattr(provider, "name", "unknown"))


class EmbeddingIndex:
    """Read/write access to the derived ``recall_embeddings`` table.

    Every connection is taken from the vault through its private
    ``_transaction`` helper, so the derived writes share the same transaction
    discipline as the rest of the derived layer: one unit of work, committed
    or rolled back as a whole.
    """

    def __init__(self, vault: Any, config: RecallConfig | None = None):
        self.vault = vault
        self.config = config or DEFAULT_CONFIG

    # ------------------------------------------------------------------ status --

    def status(self) -> dict[str, Any]:
        with self.vault._connection() as conn:
            return self._status(conn)

    def _status(self, conn: sqlite3.Connection) -> dict[str, Any]:
        try:
            row = conn.execute(
                "SELECT model, dimensions, COUNT(*), MAX(built_at) FROM recall_embeddings"
            ).fetchone()
        except sqlite3.OperationalError:
            row = (None, None, 0, None)
        try:
            pipeline = conn.execute(
                "SELECT value FROM recall_meta WHERE key='embedding_pipeline_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            pipeline = None
        return {
            "available": int(row[2] or 0) > 0,
            "count": int(row[2] or 0),
            "model": row[0],
            "dimensions": row[1],
            "builtAt": row[3],
            "pipelineVersion": pipeline[0] if pipeline else None,
        }

    # ------------------------------------------------------------------- build --

    #: SQL that lists every indexed thought together with its stored vector,
    #: so staleness can be judged in one pass instead of N round trips.
    _PENDING_SQL = """
        SELECT t.id, t.title, t.current_text,
               e.text_hash, e.model, e.vector, e.norm, e.dimensions
        FROM thoughts t
        JOIN recall_docs d ON d.thought_id = t.id
        LEFT JOIN recall_embeddings e ON e.thought_id = t.id
        ORDER BY t.created_at, t.id
    """

    @staticmethod
    def _is_stale(row: Sequence[Any], provider_label: str) -> bool:
        _thought_id, title, current_text, stored_hash, stored_model, _blob, _norm, _dim = row
        return (
            stored_hash is None
            or stored_hash != text_hash(title or "", current_text or "")
            or stored_model != provider_label
        )

    @staticmethod
    def _embed(provider: Any, texts: list[str], *, config: RecallConfig) -> list[list[float]]:
        batch_size = max(1, int(config.embedding_batch_size))
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(provider.embed_texts(texts[start:start + batch_size]))
        if len(vectors) != len(texts):
            raise AIUnavailable("The provider returned a different number of vectors than requested.")
        return vectors

    def build(self, provider: Any, *, limit: int | None = None) -> dict[str, Any]:
        """Bring the table up to date for up to ``limit`` thoughts.

        An explicit user action.  Requires an available provider; the provider
        receives the indexed text of the thoughts it embeds and nothing else.
        Rows are stale when the text changed or when the provider's label (its
        model name) differs from the one that produced them, so switching
        models marks every row for a rebuild.  Returns what it did, including
        how much work remains for the next call.
        """
        if not getattr(provider, "available", False):
            raise AIUnavailable(
                "Configure an AI provider in Data & privacy before building the semantic index."
            )
        config = self.config
        limit = max(1, int(limit or config.embedding_build_limit))
        char_cap = int(config.embedding_text_char_cap)
        provider_label = _provider_label(provider)
        started = time.perf_counter()
        with self.vault._connection() as conn:
            rows = conn.execute(self._PENDING_SQL).fetchall()
            total_indexed = len(rows)
            pending = [row for row in rows if self._is_stale(row, provider_label)]
            if not pending:
                return {
                    "built": 0,
                    "remaining": 0,
                    "total": total_indexed,
                    "model": provider_label,
                    "dimensions": None,
                    "durationMs": round((time.perf_counter() - started) * 1000, 2),
                    "upToDate": True,
                }
            pending = pending[:limit]
            texts = [f"{row[1] or ''}\n{row[2] or ''}"[:char_cap] for row in pending]
            vectors = self._embed(provider, texts, config=config)
            dimensions = len(vectors[0]) if vectors else 0
            if dimensions <= 0:
                raise AIUnavailable("The provider returned empty vectors.")
            with self.vault._transaction() as write_conn:
                for row, vector in zip(pending, vectors):
                    if len(vector) != dimensions:
                        raise ValueError("The provider returned inconsistent vector dimensions")
                    unit = normalize_vector([float(value) for value in vector])
                    write_conn.execute(
                        """
                        INSERT INTO recall_embeddings
                            (thought_id, model, dimensions, vector, norm, text_hash, built_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(thought_id) DO UPDATE SET
                            model=excluded.model, dimensions=excluded.dimensions,
                            vector=excluded.vector, norm=excluded.norm,
                            text_hash=excluded.text_hash, built_at=excluded.built_at
                        """,
                        (row[0], provider_label, dimensions, pack_vector(unit),
                         vector_norm(unit), text_hash(row[1] or "", row[2] or ""), now_utc()),
                    )
                write_conn.execute(
                    "INSERT OR REPLACE INTO recall_meta(key, value) "
                    "VALUES('embedding_pipeline_version', ?)",
                    (EMBEDDING_PIPELINE_VERSION,),
                )
        with self.vault._connection() as check:
            remaining = sum(1 for row in check.execute(self._PENDING_SQL).fetchall()
                            if self._is_stale(row, provider_label))
            total = int(check.execute("SELECT COUNT(*) FROM recall_embeddings").fetchone()[0])
        return {
            "built": len(pending),
            "remaining": remaining,
            "total": total,
            "model": provider_label,
            "dimensions": dimensions,
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
            "upToDate": remaining == 0,
        }

    # ------------------------------------------------------------------- erase --

    def erase(self) -> int:
        with self.vault._transaction() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM recall_embeddings").fetchone()[0])
            conn.execute("DELETE FROM recall_embeddings")
            conn.execute("DELETE FROM recall_meta WHERE key='embedding_pipeline_version'")
        return count

    # --------------------------------------------------------------- retrieval --

    def _query_vector(self, provider: Any, query: str) -> tuple[list[float], str]:
        text = str(query or "")[: self.config.embedding_query_char_cap]
        vectors = provider.embed_texts([text])
        if not vectors or not vectors[0]:
            raise ValueError("The provider returned no vector for the query")
        return normalize_vector([float(value) for value in vectors[0]]), _provider_label(provider)

    def similar(self, provider: Any, query: str, *, limit: int | None = None,
                min_similarity: float | None = None,
                statuses: Sequence[str] | None = None) -> dict[str, Any]:
        """The deterministic semantic neighbours of a query.

        Scans at most ``embedding_scan_cap`` rows (ordered by thought id, so
        the bound is stable), ranks by cosine similarity, and applies the
        configured floor.  A provider that cannot embed the query degrades the
        stage to ``available: False`` with a reason — it never invents a
        neighbour and never touches the scored ranking.
        """
        config = self.config
        limit = int(limit or config.embedding_results)
        min_similarity = float(
            config.embedding_min_similarity if min_similarity is None else min_similarity
        )
        started = time.perf_counter()
        try:
            query_vector, model = self._query_vector(provider, query)
        except Exception as exc:  # provider/network failures must not break recall
            return {
                "available": False,
                "reason": f"The semantic stage could not embed the query: {exc}",
                "results": [],
                "scanned": 0,
                "model": None,
                "durationMs": round((time.perf_counter() - started) * 1000, 2),
            }
        with self.vault._connection() as conn:
            sql = "SELECT e.thought_id, e.vector, e.norm, e.model FROM recall_embeddings e"
            params: list[Any] = []
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                sql += " JOIN recall_docs d ON d.thought_id = e.thought_id" \
                       f" AND d.status IN ({placeholders})"
                params.extend(statuses)
            sql += " ORDER BY e.thought_id LIMIT ?"
            params.append(int(config.embedding_scan_cap))
            rows = conn.execute(sql, params).fetchall()
        neighbours: list[SemanticNeighbour] = []
        model_mismatch = 0
        for thought_id, blob, stored_norm, row_model in rows:
            if row_model != model:
                model_mismatch += 1
                continue
            similarity = cosine_similarity(
                query_vector, unpack_vector(blob),
                first_norm=1.0,
                second_norm=float(stored_norm) if stored_norm else None,
            )
            if similarity >= min_similarity:
                neighbours.append(SemanticNeighbour(thought_id, similarity, row_model))
        neighbours.sort(key=lambda item: (-item.similarity, item.thought_id))
        return {
            "available": True,
            "reason": None,
            "results": [item.to_dict() for item in neighbours[:limit]],
            "scanned": len(rows),
            "model": model,
            "modelMismatchRows": model_mismatch,
            "minSimilarity": min_similarity,
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
        }