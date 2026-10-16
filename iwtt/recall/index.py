"""Recall V2 derived indexes.

Every table created here is **derived**.  All of them live in the same SQLite
file as the canonical tables — the same convention v0.1 already uses for
``search_documents`` and ``derived_artifacts`` — and all of them are cleared by
``Vault.clear_derived_data()``.  No canonical table is created, altered or
written by this module.

A deliberate property worth stating plainly: **the derived index stores no
verbatim copy of your original thought.**  ``recall_tokens`` holds tokens,
``recall_trigrams`` holds character-trigram counts, and ``recall_docs`` holds
only metadata (type, status, timestamps, a vector norm).  Original text is read
from the canonical ``thoughts`` table, and only for the bounded candidate set.
Deleting every row here cannot lose a word of what you wrote.

PERFORMANCE POSTURE
-------------------
Ordinary recall never materialises the corpus or the graph in Python.  Candidate
generation is one bounded SQL aggregation over an attached index; only the capped
candidate set is scored in Python.  v0.1's ``search()`` loads every row per query
(``fetchall()`` over the whole join); Recall V2 does not.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ..store import now_utc
from .config import (
    DEFAULT_CONFIG,
    RECALL_GENERATOR_VERSION,
    RECALL_INDEX_SCHEMA,
    RecallConfig,
    hub_factor_for_degree,
)
from .text import recall_variants, tokenize, trigram_counts, vector_norm

#: Derived tables owned by Recall V2, split by what they describe.  The split is
#: published here — rather than inferred by callers — because the two groups have
#: genuinely different integrity contracts.
#:
#: **Per-thought** tables hold state about ONE thought.  Every one of them carries
#: ``thought_id REFERENCES thoughts(id) ON DELETE CASCADE``, so deleting a thought
#: cannot leave its derived intelligence behind, and none of them can outlive the
#: memory they describe.
RECALL_PER_THOUGHT_TABLES: tuple[str, ...] = (
    "recall_tokens",
    "recall_trigrams",
    "recall_docs",
    "recall_graph_cache",
    "recall_clusters",
    "recall_query_memory",
)

#: **Global aggregate** tables hold corpus-wide state that belongs to no single
#: thought: term document frequencies, vault-level bookkeeping, build history.
#: A ``thought_id`` foreign key on these would be architecturally wrong — a
#: document frequency is a property of the whole corpus, and pinning it to one
#: thought would make it meaningless.  Their contract is different and is enforced
#: instead by: (a) being derived and rebuildable from canonical data alone;
#: (b) being wiped by ``clear_derived_data()``; (c) never containing canonical
#: content; and (d) being recomputed on the next index refresh after any canonical
#: change, so a deleted thought cannot leave a stale frequency behind.
RECALL_GLOBAL_TABLES: tuple[str, ...] = (
    "recall_term_stats",
    "recall_meta",
    "recall_build_log",
)

#: Listed explicitly so ``clear_derived_data()`` can wipe them without introspection.
RECALL_DERIVED_TABLES: tuple[str, ...] = RECALL_PER_THOUGHT_TABLES + RECALL_GLOBAL_TABLES

RECALL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS recall_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recall_tokens (
    thought_id TEXT NOT NULL REFERENCES thoughts(id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    field TEXT NOT NULL CHECK(field IN ('title','body','tag')),
    tf INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(thought_id, token, field)
);
CREATE INDEX IF NOT EXISTS recall_tokens_token_idx ON recall_tokens(token);
CREATE TABLE IF NOT EXISTS recall_term_stats (
    term TEXT PRIMARY KEY,
    df INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recall_trigrams (
    trigram TEXT NOT NULL,
    thought_id TEXT NOT NULL REFERENCES thoughts(id) ON DELETE CASCADE,
    tf INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(trigram, thought_id)
);
CREATE INDEX IF NOT EXISTS recall_trigrams_thought_idx ON recall_trigrams(thought_id);
CREATE TABLE IF NOT EXISTS recall_docs (
    thought_id TEXT PRIMARY KEY REFERENCES thoughts(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0,
    trigram_norm REAL NOT NULL DEFAULT 0,
    title_tokens INTEGER NOT NULL DEFAULT 0,
    body_tokens INTEGER NOT NULL DEFAULT 0,
    tag_tokens INTEGER NOT NULL DEFAULT 0,
    degree INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recall_docs_status_idx ON recall_docs(status, created_at);
CREATE INDEX IF NOT EXISTS recall_docs_type_idx ON recall_docs(type);
CREATE TABLE IF NOT EXISTS recall_graph_cache (
    thought_id TEXT PRIMARY KEY REFERENCES thoughts(id) ON DELETE CASCADE,
    degree INTEGER NOT NULL DEFAULT 0,
    in_degree INTEGER NOT NULL DEFAULT 0,
    out_degree INTEGER NOT NULL DEFAULT 0,
    confirmed_degree INTEGER NOT NULL DEFAULT 0,
    suggested_degree INTEGER NOT NULL DEFAULT 0,
    hub_factor REAL NOT NULL DEFAULT 1,
    importance REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recall_clusters (
    thought_id TEXT PRIMARY KEY REFERENCES thoughts(id) ON DELETE CASCADE,
    cluster_id TEXT NOT NULL,
    cluster_size INTEGER NOT NULL DEFAULT 1,
    label TEXT NOT NULL DEFAULT '',
    algorithm TEXT NOT NULL DEFAULT 'components+labelprop',
    authority TEXT NOT NULL DEFAULT 'AUTOMATICALLY_DISCOVERED_CLUSTER',
    member_fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS recall_clusters_cluster_idx ON recall_clusters(cluster_id);
CREATE TABLE IF NOT EXISTS recall_query_memory (
    id TEXT PRIMARY KEY,
    query_norm TEXT NOT NULL,
    thought_id TEXT NOT NULL REFERENCES thoughts(id) ON DELETE CASCADE,
    feedback TEXT NOT NULL CHECK(feedback IN ('USEFUL','NOT_USEFUL','OPENED','CORRECTION')),
    weight REAL NOT NULL,
    rank_at_time INTEGER,
    score_at_time REAL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recall_query_memory_query_idx
    ON recall_query_memory(query_norm, thought_id);
CREATE TABLE IF NOT EXISTS recall_build_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    built_at TEXT NOT NULL,
    scope TEXT NOT NULL,
    doc_count INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
"""

#: Field weights for the lexical index.  Title and tag are boosted because they
#: are short, user-authored, and highly indicative; body text is the baseline.
FIELD_WEIGHTS = {"title": 3.0, "tag": 2.5, "body": 1.0}

#: Body text indexed for trigrams, per thought (characters).  Bounds index size
#: and build time on very long thoughts without hiding the beginning of anything.
TRIGRAM_BODY_CHAR_CAP = 4000

#: Live relationships are those the v0.1 model treats as present.
LIVE_RELATIONSHIP_STATUSES = ("CONFIRMED", "SUGGESTED")


@dataclass
class LexicalHit:
    thought_id: str
    matched_terms: tuple[str, ...]
    matched_fields: tuple[str, ...]
    weighted_tf: float
    idf_sum: float


@dataclass
class TrigramHit:
    thought_id: str
    dot: float
    shared: int
    similarity: float


@dataclass
class DegreeInfo:
    thought_id: str
    degree: int = 0
    in_degree: int = 0
    out_degree: int = 0
    confirmed_degree: int = 0
    suggested_degree: int = 0
    hub_factor: float = 1.0
    importance: float = 0.0


@dataclass
class BuildReport:
    scope: str
    documents: int = 0
    token_rows: int = 0
    trigram_rows: int = 0
    terms: int = 0
    clusters: int = 0
    duration_ms: float = 0.0
    phases: dict[str, float] | None = None
    built_at: str = ""
    reindexed: int = 0
    removed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "documents": self.documents,
            "reindexed": self.reindexed,
            "removed": self.removed,
            "tokenRows": self.token_rows,
            "trigramRows": self.trigram_rows,
            "terms": self.terms,
            "clusters": self.clusters,
            "durationMs": round(self.duration_ms, 3),
            "phases": {k: round(v, 3) for k, v in (self.phases or {}).items()},
            "builtAt": self.built_at,
            "recallIndexSchema": RECALL_INDEX_SCHEMA,
            "recallGeneratorVersion": RECALL_GENERATOR_VERSION,
        }


def _placeholders(items: Sequence[Any]) -> str:
    return ",".join("?" for _ in items)


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class RecallIndex:
    """Builds, syncs, queries and destroys the derived recall indexes."""

    def __init__(self, vault: Any, config: RecallConfig | None = None) -> None:
        self.vault = vault
        self.config = config or DEFAULT_CONFIG

    # ------------------------------------------------------------------
    # schema / metadata
    # ------------------------------------------------------------------

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(RECALL_SCHEMA_SQL)
        self._set_meta(
            conn, "recall_index_schema", str(RECALL_INDEX_SCHEMA)
        )
        self._set_meta(
            conn, "recall_generator_version", RECALL_GENERATOR_VERSION
        )

    @staticmethod
    def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO recall_meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    @staticmethod
    def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM recall_meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def schema_is_current(self, conn: sqlite3.Connection) -> bool:
        try:
            stored = self._get_meta(conn, "recall_index_schema")
            generator = self._get_meta(conn, "recall_generator_version")
        except sqlite3.OperationalError:
            return False
        return stored == str(RECALL_INDEX_SCHEMA) and generator == RECALL_GENERATOR_VERSION

    def ensure_available(self) -> None:
        """Create the derived tables if they are missing — without building them.

        Used before writing query memory, so feedback can be recorded even
        immediately after ``clear_derived_data()`` (the base indexes then rebuild
        themselves lazily on the next recall).
        """
        with self.vault._transaction() as conn:
            self.ensure_schema(conn)

    def is_built(self) -> bool:
        with self.vault._connection() as conn:
            try:
                return bool(self._get_meta(conn, "built_at")) and self.schema_is_current(conn)
            except sqlite3.OperationalError:
                return False

    # ------------------------------------------------------------------
    # build / sync
    # ------------------------------------------------------------------

    def graph_signature(self, conn: sqlite3.Connection) -> str:
        """Cheap probe for "did anything the clusters depend on change?".

        Clusters are a function of the live relationship graph and of which
        thoughts participate in it — not of what any thought says.  Editing or
        adding text therefore cannot change a partition, and paying for a full
        reclustering when only text moved is what made an incremental sync as
        expensive as a rebuild.
        """
        edges = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(MAX(updated_at),'') AS stamp FROM relationships "
            "WHERE status IN ('CONFIRMED','SUGGESTED')"
        ).fetchone()
        connected = conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT id FROM ("
            "  SELECT source_id AS id FROM relationships WHERE status IN ('CONFIRMED','SUGGESTED')"
            "  UNION SELECT target_id AS id FROM relationships WHERE status IN ('CONFIRMED','SUGGESTED')"
            "))"
        ).fetchone()[0]
        return f"{int(edges['n'])}|{edges['stamp']}|{int(connected)}"

    def canonical_signature(self, conn: sqlite3.Connection) -> str:
        """Cheap drift probe: six aggregates, no row materialisation."""
        thoughts = conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0]
        thought_stamp = conn.execute("SELECT COALESCE(MAX(updated_at),'') FROM thoughts").fetchone()[0]
        relationships = conn.execute(
            "SELECT COUNT(*) FROM relationships WHERE status IN ('CONFIRMED','SUGGESTED')"
        ).fetchone()[0]
        relationship_stamp = conn.execute(
            "SELECT COALESCE(MAX(updated_at),'') FROM relationships"
        ).fetchone()[0]
        tag_links = conn.execute("SELECT COUNT(*) FROM thought_tags").fetchone()[0]
        tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        return "|".join(
            str(part)
            for part in (
                thoughts,
                thought_stamp,
                relationships,
                relationship_stamp,
                tag_links,
                tags,
                RECALL_INDEX_SCHEMA,
                RECALL_GENERATOR_VERSION,
            )
        )

    def build(self, scope: str = "full") -> BuildReport:
        """Full rebuild from canonical records.  Deterministic and repeatable."""
        started = time.perf_counter()
        built_at = now_utc()
        phases: dict[str, float] = {}
        with self.vault._transaction() as conn:
            self.ensure_schema(conn)
            mark = time.perf_counter()
            for table in ("recall_tokens", "recall_trigrams", "recall_docs", "recall_term_stats"):
                conn.execute(f"DELETE FROM {table}")
            phases["clear"] = (time.perf_counter() - mark) * 1000

            mark = time.perf_counter()
            rows = self._canonical_docs(conn)
            phases["read_canonical"] = (time.perf_counter() - mark) * 1000

            mark = time.perf_counter()
            token_rows, trigram_rows = self._index_rows(conn, rows)
            phases["index"] = (time.perf_counter() - mark) * 1000

            mark = time.perf_counter()
            self._recompute_term_stats(conn)
            phases["term_stats"] = (time.perf_counter() - mark) * 1000

            mark = time.perf_counter()
            self._build_graph_cache(conn)
            phases["graph_cache"] = (time.perf_counter() - mark) * 1000

            terms = int(conn.execute("SELECT COUNT(*) FROM recall_term_stats").fetchone()[0])
            self._set_meta(conn, "built_at", built_at)
            self._set_meta(conn, "build_scope", scope)
            self._set_meta(conn, "sync_signature", self.canonical_signature(conn))
            self._set_meta(conn, "graph_signature", self.graph_signature(conn))
            self._set_meta(conn, "document_count", str(len(rows)))

        # Clustering runs in its own transaction: it is a batch job over the
        # edge list and must not hold a write lock while it iterates.
        mark = time.perf_counter()
        clusters = self.rebuild_clusters()
        phases["clusters"] = (time.perf_counter() - mark) * 1000

        duration = (time.perf_counter() - started) * 1000
        report = BuildReport(
            scope=scope,
            documents=len(rows),
            # A full build reindexes every document; reporting 0 here would make
            # the build log claim it did nothing.
            reindexed=len(rows),
            token_rows=token_rows,
            trigram_rows=trigram_rows,
            terms=terms,
            clusters=clusters,
            duration_ms=duration,
            phases=phases,
            built_at=built_at,
        )
        self._log_build(report)
        return report

    def rebuild(self) -> BuildReport:
        return self.build(scope="rebuild")

    def _log_build(self, report: BuildReport) -> None:
        with self.vault._transaction() as conn:
            conn.execute(
                "INSERT INTO recall_build_log(built_at,scope,doc_count,duration_ms,detail_json) "
                "VALUES(?,?,?,?,?)",
                (
                    report.built_at,
                    report.scope,
                    report.documents,
                    report.duration_ms,
                    json.dumps(report.to_dict()["phases"], sort_keys=True),
                ),
            )

    def ensure_fresh(self) -> BuildReport | None:
        """Self-healing sync, mirroring v0.1's ``_ensure_search_index``.

        Returns ``None`` when nothing needed doing.  Otherwise returns the report
        for the incremental sync or the full rebuild it performed.  This is what
        makes recall work after a restart, after ``clear_derived_data()``, and
        after an import — with no background job and no user action.
        """
        with self.vault._connection() as conn:
            try:
                tables = {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            except sqlite3.OperationalError:
                tables = set()
            if not set(RECALL_DERIVED_TABLES) <= tables:
                return self.build(scope="bootstrap")
            if not self.schema_is_current(conn):
                return self.build(scope="schema-upgrade")
            signature = self.canonical_signature(conn)
            if self._get_meta(conn, "sync_signature") == signature and self._get_meta(
                conn, "built_at"
            ):
                return None
            stale, removed = self._diff(conn)
        if not stale and not removed:
            # The signature moved, so canonical data changed — but every per-thought
            # row is already correct.  That is the signature of a deletion the
            # ``ON DELETE CASCADE`` foreign keys handled before this sync saw it.
            #
            # A cascade cannot reach the GLOBAL aggregates (``recall_term_stats``,
            # ``recall_graph_cache``), because a document frequency belongs to the
            # corpus rather than to one thought.  Left alone, the deleted thought's
            # frequencies would survive it forever and quietly skew IDF — the
            # signature would already have been re-stamped, so nothing would ever
            # reconcile them.  Recomputing here is bounded (one aggregate pass over
            # the token index) and only happens when something actually changed.
            with self.vault._transaction() as conn:
                report = self._reconcile_global_aggregates(conn, signature)
            return report
        return self.sync(stale_ids=stale, removed_ids=removed)

    def _reconcile_global_aggregates(
        self, conn: sqlite3.Connection, signature: str
    ) -> BuildReport | None:
        """Recompute corpus-wide derived state from the per-thought index.

        Returns a report only when something was actually out of step, so a caller
        can tell "checked and consistent" from "found drift and repaired it".
        """
        started = time.perf_counter()
        stale_terms = int(conn.execute(
            """
            SELECT COUNT(*) FROM recall_term_stats ts
            LEFT JOIN (SELECT token, COUNT(DISTINCT thought_id) AS df FROM recall_tokens
                       GROUP BY token) live ON live.token = ts.term
            WHERE live.token IS NULL OR live.df <> ts.df
            """
        ).fetchone()[0])
        degrees_drifted = int(conn.execute(
            """
            SELECT COUNT(*) FROM recall_graph_cache g
            LEFT JOIN (
                SELECT id, (
                    SELECT COUNT(*) FROM relationships r
                    WHERE r.status IN ('CONFIRMED','SUGGESTED')
                      AND (r.source_id = t.id OR r.target_id = t.id)
                ) AS degree
                FROM thoughts t
            ) live ON live.id = g.thought_id
            WHERE live.id IS NULL OR live.degree <> g.degree
            """
        ).fetchone()[0])
        if not stale_terms and not degrees_drifted:
            self._set_meta(conn, "sync_signature", signature)
            return None

        if stale_terms:
            self._recompute_term_stats(conn)
        if degrees_drifted:
            self._build_graph_cache(conn)
        self._set_meta(conn, "sync_signature", signature)
        self._set_meta(conn, "last_sync_at", now_utc())
        self._set_meta(conn, "build_scope", "reconcile")
        self._set_meta(conn, "last_reconcile_at", now_utc())
        report = BuildReport(
            scope="reconcile",
            documents=int(conn.execute("SELECT COUNT(*) FROM recall_docs").fetchone()[0]),
            token_rows=int(conn.execute("SELECT COUNT(*) FROM recall_tokens").fetchone()[0]),
            trigram_rows=int(conn.execute("SELECT COUNT(*) FROM recall_trigrams").fetchone()[0]),
            terms=int(conn.execute("SELECT COUNT(*) FROM recall_term_stats").fetchone()[0]),
            clusters=0,
            duration_ms=(time.perf_counter() - started) * 1000,
            phases={"staleTerms": stale_terms, "degreesDrifted": degrees_drifted},
            built_at=now_utc(),
            reindexed=0,
            removed=stale_terms + degrees_drifted,
        )
        return report

    def _diff(self, conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
        """Thoughts needing (re)indexing, and index rows whose thought is gone."""
        stale = [
            row["id"]
            for row in conn.execute(
                """
                SELECT t.id AS id FROM thoughts t
                LEFT JOIN recall_docs d ON d.thought_id = t.id
                WHERE d.thought_id IS NULL
                   OR d.updated_at <> t.updated_at
                   OR d.type      <> t.type
                   OR d.status    <> t.status
                   OR d.is_demo   <> t.is_demo
                ORDER BY t.id
                """
            )
        ]
        removed = [
            row["thought_id"]
            for row in conn.execute(
                """
                SELECT d.thought_id AS thought_id FROM recall_docs d
                LEFT JOIN thoughts t ON t.id = d.thought_id
                WHERE t.id IS NULL ORDER BY d.thought_id
                """
            )
        ]
        return stale, removed

    def sync(self, stale_ids: Sequence[str] = (), removed_ids: Sequence[str] = ()) -> BuildReport:
        """Incremental (re)index of specific thoughts.  Bounded work."""
        started = time.perf_counter()
        built_at = now_utc()
        with self.vault._transaction() as conn:
            self.ensure_schema(conn)
            for thought_id in removed_ids:
                self._forget_thought(conn, thought_id)
            rows = self._canonical_docs(conn, thought_ids=list(stale_ids))
            for row in rows:
                self._forget_thought(conn, row["id"])
            token_rows, trigram_rows = self._index_rows(conn, rows)
            self._build_graph_cache(conn)
            # ``_forget_thought`` keeps df accurate for removals IT performs.  When a
            # cascade got there first there is nothing for it to adjust, so the
            # aggregate is reconciled from the token index on every sync.
            self._recompute_term_stats(conn)
            self._set_meta(conn, "built_at", self._get_meta(conn, "built_at") or built_at)
            self._set_meta(conn, "last_sync_at", built_at)
            self._set_meta(conn, "build_scope", "sync")
            self._set_meta(conn, "sync_signature", self.canonical_signature(conn))
            self._set_meta(
                conn,
                "document_count",
                str(int(conn.execute("SELECT COUNT(*) FROM recall_docs").fetchone()[0])),
            )
            terms = int(conn.execute("SELECT COUNT(*) FROM recall_term_stats").fetchone()[0])
            graph_signature = self.graph_signature(conn)
            graph_changed = self._get_meta(conn, "graph_signature") != graph_signature
            cluster_rows = int(conn.execute("SELECT COUNT(*) FROM recall_clusters").fetchone()[0])
            thought_rows = int(conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0])
            needs_clusters = graph_changed or (cluster_rows == 0 and thought_rows > 0)
            if not needs_clusters:
                self._set_meta(conn, "graph_signature", graph_signature)
        if needs_clusters:
            clusters = self.rebuild_clusters()
            with self.vault._transaction() as conn:
                self._set_meta(conn, "graph_signature", graph_signature)
        else:
            with self.vault._connection() as conn:
                clusters = int(conn.execute(
                    "SELECT COUNT(DISTINCT cluster_id) FROM recall_clusters"
                ).fetchone()[0])
        report = BuildReport(
            scope="sync",
            documents=len(rows),
            token_rows=token_rows,
            trigram_rows=trigram_rows,
            terms=terms,
            clusters=clusters,
            duration_ms=(time.perf_counter() - started) * 1000,
            phases={},
            built_at=built_at,
            reindexed=len(rows),
            removed=len(removed_ids),
        )
        with self.vault._connection() as conn:
            report.documents = int(conn.execute("SELECT COUNT(*) FROM recall_docs").fetchone()[0])
        self._log_build(report)
        return report

    def _forget_thought(self, conn: sqlite3.Connection, thought_id: str) -> None:
        """Remove one thought from the derived index, adjusting df exactly."""
        previous = {
            row["token"]
            for row in conn.execute(
                "SELECT DISTINCT token FROM recall_tokens WHERE thought_id=?", (thought_id,)
            )
        }
        if previous:
            conn.executemany(
                "INSERT INTO recall_term_stats(term,df) VALUES(?,0) "
                "ON CONFLICT(term) DO UPDATE SET df = MAX(0, df - 1)",
                [(term,) for term in sorted(previous)],
            )
        conn.execute("DELETE FROM recall_tokens WHERE thought_id=?", (thought_id,))
        conn.execute("DELETE FROM recall_trigrams WHERE thought_id=?", (thought_id,))
        conn.execute("DELETE FROM recall_docs WHERE thought_id=?", (thought_id,))
        conn.execute("DELETE FROM recall_clusters WHERE thought_id=?", (thought_id,))
        conn.execute("DELETE FROM recall_term_stats WHERE df <= 0")

    # ------------------------------------------------------------------
    # indexing internals
    # ------------------------------------------------------------------

    def _canonical_docs(
        self, conn: sqlite3.Connection, thought_ids: Sequence[str] | None = None
    ) -> list[sqlite3.Row]:
        sql = "SELECT id, original_text, current_text, title, type, status, created_at, updated_at, is_demo FROM thoughts"
        params: list[Any] = []
        if thought_ids is not None:
            ids = list(thought_ids)
            if not ids:
                return []
            sql += f" WHERE id IN ({_placeholders(ids)})"
            params = ids
        sql += " ORDER BY id"
        return list(conn.execute(sql, params))

    def _tags_by_thought(self, conn: sqlite3.Connection) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for row in conn.execute(
            "SELECT tt.thought_id AS thought_id, t.name AS name FROM thought_tags tt "
            "JOIN tags t ON t.id = tt.tag_id ORDER BY tt.thought_id, t.normalized_name"
        ):
            out.setdefault(row["thought_id"], []).append(row["name"])
        return out

    def _index_rows(self, conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> tuple[int, int]:
        if not rows:
            return 0, 0
        wanted = {row["id"] for row in rows}
        tags_by_thought = {
            thought_id: names
            for thought_id, names in self._tags_by_thought(conn).items()
            if thought_id in wanted
        }
        token_batch: list[tuple] = []
        trigram_batch: list[tuple] = []
        doc_batch: list[tuple] = []
        added_terms: dict[str, int] = {}
        indexed_at = now_utc()

        for row in rows:
            thought_id = row["id"]
            tag_names = tags_by_thought.get(thought_id, [])
            body = f"{row['original_text']}\n{row['current_text']}"[:TRIGRAM_BODY_CHAR_CAP * 2]
            fields = {
                "title": row["title"] or "",
                "body": body,
                "tag": " ".join(tag_names),
            }
            doc_forms: set[str] = set()
            field_token_counts: dict[str, int] = {}
            for field, value in fields.items():
                tokens = tokenize(value, remove_stopwords=False)
                field_token_counts[field] = len(tokens)
                if not tokens:
                    continue
                counts: dict[str, int] = {}
                for token in tokens:
                    # Index the raw token AND its v0.1 morphological/alias
                    # variants, so "fighting" matches "fight" while a literal
                    # "fighting" query still scores at full strength.
                    for form in recall_variants(token):
                        if form:
                            counts[form] = counts.get(form, 0) + 1
                            doc_forms.add(form)
                for form, count in sorted(counts.items()):
                    token_batch.append((thought_id, form, field, count))

            for form in doc_forms:
                added_terms[form] = added_terms.get(form, 0) + 1

            trigrams: dict[str, int] = {}
            for token in tokenize(row["title"] or "", remove_stopwords=False):
                for gram, count in trigram_counts([token]).items():
                    trigrams[gram] = trigrams.get(gram, 0) + count * 2
            for token in tokenize(body[:TRIGRAM_BODY_CHAR_CAP], remove_stopwords=False):
                for gram, count in trigram_counts([token]).items():
                    trigrams[gram] = trigrams.get(gram, 0) + count
            for token in tokenize(" ".join(tag_names), remove_stopwords=False):
                for gram, count in trigram_counts([token]).items():
                    trigrams[gram] = trigrams.get(gram, 0) + count * 3
            norm = vector_norm(trigrams)
            for gram, count in sorted(trigrams.items()):
                trigram_batch.append((gram, thought_id, count))

            doc_batch.append(
                (
                    thought_id,
                    row["type"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    int(row["is_demo"]),
                    norm,
                    field_token_counts.get("title", 0),
                    field_token_counts.get("body", 0),
                    field_token_counts.get("tag", 0),
                    0,
                    indexed_at,
                )
            )

        conn.executemany(
            "INSERT OR REPLACE INTO recall_tokens(thought_id,token,field,tf) VALUES(?,?,?,?)",
            token_batch,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO recall_trigrams(trigram,thought_id,tf) VALUES(?,?,?)",
            trigram_batch,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO recall_docs(thought_id,type,status,created_at,updated_at,"
            "is_demo,trigram_norm,title_tokens,body_tokens,tag_tokens,degree,indexed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            doc_batch,
        )
        if added_terms:
            conn.executemany(
                "INSERT INTO recall_term_stats(term,df) VALUES(?,?) "
                "ON CONFLICT(term) DO UPDATE SET df = df + excluded.df",
                sorted(added_terms.items()),
            )
        return len(token_batch), len(trigram_batch)

    def _recompute_term_stats(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM recall_term_stats")
        conn.execute(
            "INSERT INTO recall_term_stats(term,df) "
            "SELECT token, COUNT(DISTINCT thought_id) FROM recall_tokens GROUP BY token"
        )

    # ------------------------------------------------------------------
    # graph cache (degrees + hub factors) — derived
    # ------------------------------------------------------------------

    def live_edges(self, conn: sqlite3.Connection) -> list[tuple[str, str, str, str, str]]:
        """``(id, source, target, type, status, provenance)`` for live edges.

        Deterministic ordering: type, then created_at, then the durable id.
        Traversal never depends on physical row order.
        """
        return [
            (row["id"], row["source_id"], row["target_id"], row["type"], row["status"], row["provenance"])
            for row in conn.execute(
                "SELECT id,source_id,target_id,type,status,provenance,created_at FROM relationships "
                "WHERE status IN ('CONFIRMED','SUGGESTED') "
                "ORDER BY type, created_at, id"
            )
        ]

    def _build_graph_cache(self, conn: sqlite3.Connection) -> None:
        degrees: dict[str, DegreeInfo] = {}

        def slot(thought_id: str) -> DegreeInfo:
            info = degrees.get(thought_id)
            if info is None:
                info = DegreeInfo(thought_id)
                degrees[thought_id] = info
            return info

        for row in conn.execute("SELECT id FROM thoughts ORDER BY id"):
            slot(row["id"])

        for _id, source, target, _type, status, _provenance in self.live_edges(conn):
            src = slot(source)
            src.degree += 1
            src.out_degree += 1
            if status == "CONFIRMED":
                src.confirmed_degree += 1
            else:
                src.suggested_degree += 1
            dst = slot(target)
            dst.degree += 1
            dst.in_degree += 1
            if status == "CONFIRMED":
                dst.confirmed_degree += 1
            else:
                dst.suggested_degree += 1

        nonzero = sorted(info.degree for info in degrees.values() if info.degree > 0)
        threshold = hub_degree_threshold(nonzero, self.config)

        batch = []
        for thought_id in sorted(degrees):
            info = degrees[thought_id]
            info.hub_factor = hub_factor_for_degree(info.degree, threshold, self.config.hub_floor)
            confirmed_ratio = (info.confirmed_degree / info.degree) if info.degree else 0.0
            info.importance = math.log1p(info.degree) * (0.5 + 0.5 * confirmed_ratio)
            batch.append(
                (
                    thought_id,
                    info.degree,
                    info.in_degree,
                    info.out_degree,
                    info.confirmed_degree,
                    info.suggested_degree,
                    info.hub_factor,
                    info.importance,
                )
            )
        conn.execute("DELETE FROM recall_graph_cache")
        conn.executemany(
            "INSERT OR REPLACE INTO recall_graph_cache(thought_id,degree,in_degree,out_degree,"
            "confirmed_degree,suggested_degree,hub_factor,importance) VALUES(?,?,?,?,?,?,?,?)",
            batch,
        )
        conn.execute(
            "UPDATE recall_docs SET degree = COALESCE("
            "(SELECT g.degree FROM recall_graph_cache g WHERE g.thought_id = recall_docs.thought_id), 0)"
        )
        self._set_meta(conn, "hub_threshold", str(threshold))
        self._set_meta(conn, "median_degree", str(nonzero[len(nonzero) // 2] if nonzero else 0))

    def hub_threshold(self) -> int:
        with self.vault._connection() as conn:
            try:
                value = self._get_meta(conn, "hub_threshold")
            except sqlite3.OperationalError:
                return self.config.hub_min_degree
        return int(value) if value else self.config.hub_min_degree

    def degrees(self) -> dict[str, DegreeInfo]:
        out: dict[str, DegreeInfo] = {}
        with self.vault._connection() as conn:
            try:
                rows = conn.execute("SELECT * FROM recall_graph_cache ORDER BY thought_id").fetchall()
            except sqlite3.OperationalError:
                return out
        for row in rows:
            out[row["thought_id"]] = DegreeInfo(
                thought_id=row["thought_id"],
                degree=int(row["degree"]),
                in_degree=int(row["in_degree"]),
                out_degree=int(row["out_degree"]),
                confirmed_degree=int(row["confirmed_degree"]),
                suggested_degree=int(row["suggested_degree"]),
                hub_factor=float(row["hub_factor"]),
                importance=float(row["importance"]),
            )
        return out

    def degree_of(self, thought_id: str) -> DegreeInfo:
        with self.vault._connection() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM recall_graph_cache WHERE thought_id=?", (thought_id,)
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
        if row is None:
            return DegreeInfo(thought_id)
        return DegreeInfo(
            thought_id=row["thought_id"],
            degree=int(row["degree"]),
            in_degree=int(row["in_degree"]),
            out_degree=int(row["out_degree"]),
            confirmed_degree=int(row["confirmed_degree"]),
            suggested_degree=int(row["suggested_degree"]),
            hub_factor=float(row["hub_factor"]),
            importance=float(row["importance"]),
        )

    # ------------------------------------------------------------------
    # term statistics / IDF
    # ------------------------------------------------------------------

    def document_count(self, conn: sqlite3.Connection) -> int:
        try:
            return int(conn.execute("SELECT COUNT(*) FROM recall_docs").fetchone()[0])
        except sqlite3.OperationalError:
            return 0

    def df_map(self, conn: sqlite3.Connection, terms: Iterable[str]) -> dict[str, int]:
        wanted = sorted(set(terms))
        if not wanted:
            return {}
        out: dict[str, int] = {}
        for chunk in _chunks(wanted, 500):
            for row in conn.execute(
                f"SELECT term, df FROM recall_term_stats WHERE term IN ({_placeholders(chunk)})",
                list(chunk),
            ):
                out[row["term"]] = int(row["df"])
        return out

    @staticmethod
    def idf(total_docs: int, df: int) -> float:
        """``idf(t) = log(1 + N / (1 + df(t)))``.

        The same functional form the audit verified in Graphify's
        ``_compute_idf`` (matrix #3, ADOPT), independently implemented.  The
        ``1 +`` guards keep the value finite and non-negative: an unseen term
        scores ``log(1 + N)`` (most distinctive), and a term in every document
        still scores ``log(2) ≈ 0.69`` rather than 0, so it contributes a little.
        """
        return math.log(1.0 + max(0, total_docs) / (1.0 + max(0, df)))

    def rarest_terms(self, limit: int = 25) -> list[tuple[str, int]]:
        with self.vault._connection() as conn:
            try:
                return [
                    (row["term"], int(row["df"]))
                    for row in conn.execute(
                        "SELECT term, df FROM recall_term_stats ORDER BY df ASC, term ASC LIMIT ?",
                        (int(limit),),
                    )
                ]
            except sqlite3.OperationalError:
                return []

    # ------------------------------------------------------------------
    # candidate generation (bounded SQL)
    # ------------------------------------------------------------------

    def lexical_candidates(
        self,
        conn: sqlite3.Connection,
        query_forms: Sequence[tuple[str, str, float]],
        *,
        limit: int | None = None,
    ) -> dict[str, LexicalHit]:
        """One bounded aggregation.  ``query_forms`` = ``(term, indexed_form, idf)``."""
        if not query_forms:
            return {}
        limit = int(limit or self.config.token_candidate_cap)
        conn.execute("DROP TABLE IF EXISTS temp.rq_tok")
        conn.execute(
            "CREATE TEMP TABLE rq_tok(qtoken TEXT, token TEXT, qw REAL, PRIMARY KEY(qtoken, token))"
        )
        conn.executemany(
            "INSERT OR IGNORE INTO rq_tok(qtoken,token,qw) VALUES(?,?,?)",
            [(term, form, weight) for term, form, weight in query_forms],
        )
        # The join below is written ``rq_tok CROSS JOIN recall_tokens`` on purpose.
        # With a plain JOIN the planner scans the whole token table and probes the
        # tiny query table (measured: SCAN of 16,896 rows at 1,000 thoughts, so the
        # cost grows with the vault).  CROSS JOIN forbids reordering, so SQLite
        # walks the handful of query forms and SEARCHes recall_tokens through
        # recall_tokens_token_idx instead — cost proportional to the matches, not to
        # the corpus.  Same rows, same order, different plan.
        rows = conn.execute(
            """
            SELECT t.thought_id                        AS thought_id,
                   COUNT(DISTINCT q.qtoken)            AS matched_terms,
                   SUM(t.tf * q.qw)                    AS idf_sum,
                   SUM(t.tf * CASE t.field
                              WHEN 'title' THEN ?
                              WHEN 'tag'   THEN ?
                              ELSE ? END)              AS weighted_tf,
                   GROUP_CONCAT(DISTINCT q.qtoken)     AS qtokens,
                   GROUP_CONCAT(DISTINCT t.field)      AS fields
            FROM rq_tok q CROSS JOIN recall_tokens t
            WHERE t.token = q.token
            GROUP BY t.thought_id
            ORDER BY idf_sum DESC, matched_terms DESC, weighted_tf DESC, t.thought_id ASC
            LIMIT ?
            """,
            (
                FIELD_WEIGHTS["title"],
                FIELD_WEIGHTS["tag"],
                FIELD_WEIGHTS["body"],
                limit,
            ),
        ).fetchall()
        conn.execute("DROP TABLE IF EXISTS temp.rq_tok")
        out: dict[str, LexicalHit] = {}
        for row in rows:
            out[row["thought_id"]] = LexicalHit(
                thought_id=row["thought_id"],
                matched_terms=tuple(sorted(set((row["qtokens"] or "").split(",")) - {""})),
                matched_fields=tuple(sorted(set((row["fields"] or "").split(",")) - {""})),
                weighted_tf=float(row["weighted_tf"] or 0.0),
                idf_sum=float(row["idf_sum"] or 0.0),
            )
        return out

    def trigram_is_selective(
        self, conn: sqlite3.Connection, trigrams: Sequence[str], total_docs: int
    ) -> tuple[bool, dict[str, int]]:
        """The "never-worse" guard (matrix #5, ADOPT).

        If the rarest query trigram still covers more than
        ``trigram_guard_fraction`` of the vault, the index is not selective and
        must not be used to narrow candidates — because narrowing with a
        non-selective index can only change results, never speed them up
        meaningfully.  Returns ``(selective, postings_per_trigram)``.
        """
        if not trigrams or total_docs <= 0:
            return False, {}
        postings: dict[str, int] = {}
        for chunk in _chunks(sorted(set(trigrams)), 400):
            for row in conn.execute(
                f"SELECT trigram, COUNT(*) AS n FROM recall_trigrams "
                f"WHERE trigram IN ({_placeholders(chunk)}) GROUP BY trigram",
                list(chunk),
            ):
                postings[row["trigram"]] = int(row["n"])
        if not postings:
            return False, postings
        rarest = min(postings.values())
        return (rarest / total_docs) <= self.config.trigram_guard_fraction, postings

    def trigram_candidates(
        self,
        conn: sqlite3.Connection,
        query_words: Sequence[str],
        *,
        limit: int | None = None,
    ) -> tuple[dict[str, TrigramHit], dict[str, Any]]:
        """Fuzzy candidate prefilter.

        Stage 1 (SQL): join the query trigram vector against ``recall_trigrams``
        and aggregate ``dot``/``shared`` per thought.  Stage 2 (Python): turn
        that into a cosine using the stored document norm.  The expensive
        ``difflib`` pass is then applied only to this capped set.

        Returns ``(hits, meta)`` where ``meta`` records whether the never-worse
        guard tripped, so diagnostics can state honestly which path ran.
        """
        counts = trigram_counts(query_words)
        meta: dict[str, Any] = {
            "queryTrigrams": len(counts),
            "guardTripped": False,
            "selective": False,
            "candidates": 0,
        }
        if not counts:
            return {}, meta
        total_docs = self.document_count(conn)
        selective, postings = self.trigram_is_selective(conn, list(counts), total_docs)
        meta["selective"] = selective
        meta["rarestPostings"] = min(postings.values()) if postings else 0
        meta["totalDocs"] = total_docs
        if not selective:
            meta["guardTripped"] = True
            return {}, meta

        # Drop character fragments that appear (almost) everywhere before paying to
        # read their postings.  Deterministic, and reported rather than silent.
        postings_cap = max(
            int(self.config.trigram_postings_floor),
            int(total_docs * self.config.trigram_postings_fraction),
        )
        skipped = sorted(
            gram for gram, occurrences in postings.items() if occurrences > postings_cap
        )
        meta["postingsCap"] = postings_cap
        meta["trigramsSkippedTooCommon"] = skipped
        meta["trigramsUsed"] = len(counts) - len(skipped)
        if skipped:
            drop = set(skipped)
            counts = {gram: tf for gram, tf in counts.items() if gram not in drop}
        if not counts:
            # Every query trigram was ubiquitous, so none of them can narrow
            # anything: the never-worse contract applies and the lexical set runs
            # unfiltered.
            meta["guardTripped"] = True
            return {}, meta

        limit = int(limit or self.config.trigram_candidate_cap)
        query_norm = vector_norm(counts)
        conn.execute("DROP TABLE IF EXISTS temp.rq_tri")
        conn.execute("CREATE TEMP TABLE rq_tri(trigram TEXT PRIMARY KEY, qtf INTEGER)")
        conn.executemany(
            "INSERT OR IGNORE INTO rq_tri(trigram,qtf) VALUES(?,?)", sorted(counts.items())
        )
        # ``rq_tri CROSS JOIN recall_trigrams`` for the same reason as the lexical
        # query: without it SQLite scans every trigram row in the vault (71,872 at
        # 1,000 thoughts) per query, which is precisely the unbounded cost v0.2 is
        # supposed to eliminate.  With it, the query's ~40 trigrams each SEARCH the
        # (trigram, thought_id) primary key.
        rows = conn.execute(
            """
            SELECT t.thought_id            AS thought_id,
                   SUM(t.tf * q.qtf)       AS dot,
                   COUNT(*)                AS shared
            FROM rq_tri q CROSS JOIN recall_trigrams t
            WHERE t.trigram = q.trigram
            GROUP BY t.thought_id
            ORDER BY shared DESC, dot DESC, t.thought_id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.execute("DROP TABLE IF EXISTS temp.rq_tri")
        if not rows:
            return {}, meta
        ids = [row["thought_id"] for row in rows]
        norms: dict[str, float] = {}
        for chunk in _chunks(ids, 500):
            for row in conn.execute(
                f"SELECT thought_id, trigram_norm FROM recall_docs "
                f"WHERE thought_id IN ({_placeholders(chunk)})",
                list(chunk),
            ):
                norms[row["thought_id"]] = float(row["trigram_norm"] or 0.0)
        out: dict[str, TrigramHit] = {}
        for row in rows:
            dot = float(row["dot"] or 0.0)
            doc_norm = norms.get(row["thought_id"], 0.0)
            similarity = dot / (query_norm * doc_norm) if (query_norm and doc_norm) else 0.0
            out[row["thought_id"]] = TrigramHit(
                thought_id=row["thought_id"],
                dot=dot,
                shared=int(row["shared"] or 0),
                similarity=max(0.0, min(1.0, similarity)),
            )
        meta["candidates"] = len(out)
        return out, meta

    def fetch_doc_meta(
        self, conn: sqlite3.Connection, thought_ids: Sequence[str]
    ) -> dict[str, sqlite3.Row]:
        ids = list(dict.fromkeys(thought_ids))
        out: dict[str, sqlite3.Row] = {}
        for chunk in _chunks(ids, 500):
            for row in conn.execute(
                f"SELECT * FROM recall_docs WHERE thought_id IN ({_placeholders(chunk)})",
                list(chunk),
            ):
                out[row["thought_id"]] = row
        return out

    # ------------------------------------------------------------------
    # clusters — derived, explicitly labelled automatic
    # ------------------------------------------------------------------

    def rebuild_clusters(self) -> int:
        from .clusters import rebuild_clusters

        return rebuild_clusters(self)

    def clusters(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        with self.vault._connection() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM recall_clusters ORDER BY cluster_id, thought_id"
                ).fetchall()
            except sqlite3.OperationalError:
                return out
        for row in rows:
            out[row["thought_id"]] = {
                "clusterId": row["cluster_id"],
                "clusterSize": int(row["cluster_size"]),
                "label": row["label"],
                "algorithm": row["algorithm"],
                "authority": row["authority"],
                "derived": True,
            }
        return out

    # ------------------------------------------------------------------
    # destruction / stats
    # ------------------------------------------------------------------

    def clear(self, conn: sqlite3.Connection) -> dict[str, int]:
        """Delete every derived Recall V2 row.  Called by
        ``Vault.clear_derived_data()``.  Returns per-table row counts so the
        destructive test can assert what was actually removed."""
        removed: dict[str, int] = {}
        for table in RECALL_DERIVED_TABLES:
            try:
                count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.OperationalError:
                count = 0
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass
            removed[table] = count
        return removed

    def stats(self) -> dict[str, Any]:
        with self.vault._connection() as conn:
            try:
                count = lambda sql: int(conn.execute(sql).fetchone()[0])  # noqa: E731
                return {
                    "built": bool(self._get_meta(conn, "built_at"))
                    and self.schema_is_current(conn),
                    "builtAt": self._get_meta(conn, "built_at"),
                    "lastSyncAt": self._get_meta(conn, "last_sync_at"),
                    "buildScope": self._get_meta(conn, "build_scope"),
                    "recallIndexSchema": RECALL_INDEX_SCHEMA,
                    "recallGeneratorVersion": RECALL_GENERATOR_VERSION,
                    "documents": count("SELECT COUNT(*) FROM recall_docs"),
                    "tokenRows": count("SELECT COUNT(*) FROM recall_tokens"),
                    "trigramRows": count("SELECT COUNT(*) FROM recall_trigrams"),
                    "distinctTrigrams": count(
                        "SELECT COUNT(*) FROM (SELECT DISTINCT trigram FROM recall_trigrams)"
                    ),
                    "terms": count("SELECT COUNT(*) FROM recall_term_stats"),
                    "graphCacheRows": count("SELECT COUNT(*) FROM recall_graph_cache"),
                    "clusteredNodes": count("SELECT COUNT(*) FROM recall_clusters"),
                    "distinctClusters": count("SELECT COUNT(DISTINCT cluster_id) FROM recall_clusters"),
                    "queryMemoryEvents": count("SELECT COUNT(*) FROM recall_query_memory"),
                    "queryMemoryQueries": count(
                        "SELECT COUNT(DISTINCT query_norm) FROM recall_query_memory"
                    ),
                    "hubThreshold": self._get_meta(conn, "hub_threshold"),
                    "medianDegree": self._get_meta(conn, "median_degree"),
                    "learningResetAt": self._get_meta(conn, "learning_reset_at"),
                }
            except sqlite3.OperationalError:
                return {
                    "built": False,
                    "recallIndexSchema": RECALL_INDEX_SCHEMA,
                    "recallGeneratorVersion": RECALL_GENERATOR_VERSION,
                }

    def build_log(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.vault._connection() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM recall_build_log ORDER BY id DESC LIMIT ?", (int(limit),)
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        out = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(item.pop("detail_json") or "{}")
            out.append(item)
        return out

    def drift(self) -> dict[str, Any]:
        """Consistency probe used by the diagnostics view and the tests."""
        with self.vault._connection() as conn:
            canonical = int(conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0])
            try:
                indexed = int(conn.execute("SELECT COUNT(*) FROM recall_docs").fetchone()[0])
                signature = self.canonical_signature(conn)
                stored = self._get_meta(conn, "sync_signature")
                current = self.schema_is_current(conn)
            except sqlite3.OperationalError:
                return {"canonicalThoughts": canonical, "indexedDocuments": 0, "stale": True,
                        "schemaCurrent": False}
        return {
            "canonicalThoughts": canonical,
            "indexedDocuments": indexed,
            "schemaCurrent": current,
            "signatureMatches": signature == stored,
            "stale": (canonical != indexed) or (signature != stored) or not current,
        }


def hub_degree_threshold(nonzero_degrees: Sequence[int], config: RecallConfig) -> int:
    """``max(hub_min_degree, ceil(percentile(degrees)))``.

    ADAPTATION, documented: Graphify uses ``max(50, p99(degree))`` — sensible for
    a code graph with file and staging super-hubs, useless for a personal thought
    vault where almost nothing reaches degree 50.  v0.2 keeps the *shape* (a
    percentile with an absolute floor) and re-derives the constants for sparse
    personal graphs, per the audit's ADAPT #21.
    """
    if not nonzero_degrees:
        return config.hub_min_degree
    ordered = sorted(nonzero_degrees)
    position = min(len(ordered) - 1, max(0, int(math.ceil(config.hub_percentile * len(ordered))) - 1))
    return max(config.hub_min_degree, int(math.ceil(ordered[position])))
