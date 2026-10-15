"""Recall V2 — the derived ``GraphViewModel``.

This is the clean seam a future 2D / 2.5D / zoomable Mind World renderer would
consume.  It is **not** the Mind World: there is no layout engine, no camera, no
spatial region, and above all **no persisted screen coordinates**.

The audit is explicit that Graphify gets one thing right here and IWTT must keep
it: layout is computed client-side and discarded, so presentation geometry can
never be mistaken for the meaning of a thought.  ``FUTURE_MIND_WORLD.md`` in v0.1
already states the same rule; this module is the data contract that honours it.

Everything returned is derived and rebuildable.  Every node carries
``entityType`` so a future renderer can place external-knowledge entities
(repositories, files, functions, documents) beside thoughts **without turning
them into thoughts** — see EXTERNAL_KNOWLEDGE_ARCHITECTURE.md.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Sequence

from ..store import Vault, _display_title
from .clusters import cluster_summary
from .config import RecallConfig, direction_label, is_symmetric, relationship_weight
from .expansion import connection_display, provenance_weight
from .index import RecallIndex
from .text import sanitize_label

#: The only entity kind v0.2 can actually produce.  The vocabulary exists so the
#: boundary is expressible now and testable now, without any ingestion being
#: implemented.
ENTITY_THOUGHT = "THOUGHT_ENTITY"
ENTITY_EXTERNAL = "EXTERNAL_KNOWLEDGE_ENTITY"


def build_graph_view_model(
    index: RecallIndex,
    *,
    root_ids: Sequence[str] | None = None,
    max_nodes: int = 200,
    include_graveyard: bool = True,
    include_suggested: bool = True,
    config: RecallConfig | None = None,
) -> dict[str, Any]:
    """Build a bounded, derived graph view.

    Node selection is deterministic:

    * with ``root_ids`` — the roots plus their LIVE neighbourhood, breadth-first,
      nearest first, capped at ``max_nodes``;
    * without — the highest-``importance`` thoughts, ties broken on the durable
      id, capped at ``max_nodes``.

    Edges are included only when **both** endpoints are in the node set, so the
    model is always a coherent induced subgraph rather than a bag of dangling
    links.
    """

    # Self-healing: node degree, hub factor, importance and cluster membership are
    # all derived, so refresh before reading them.  Canonical fields are read live.
    index.ensure_fresh()
    config = config or index.config
    vault: Vault = index.vault
    max_nodes = max(1, min(int(max_nodes), 2000))

    with vault._connection() as conn:
        cache = {
            row["thought_id"]: (
                int(row["degree"]), float(row["hub_factor"]), float(row["importance"])
            )
            for row in _safe_select(
                conn,
                "SELECT thought_id, degree, hub_factor, importance FROM recall_graph_cache",
            )
        }
        clusters = {
            row["thought_id"]: (row["cluster_id"], row["label"])
            for row in _safe_select(
                conn, "SELECT thought_id, cluster_id, label FROM recall_clusters"
            )
        }
        status_clause = "" if include_graveyard else " WHERE status='ACTIVE'"
        thought_rows = _safe_select(
            conn,
            "SELECT id, title, current_text, original_text, type, status, created_at, is_demo "
            f"FROM thoughts{status_clause} ORDER BY id",
        )
        thoughts = {row["id"]: row for row in thought_rows}

        if root_ids:
            selected = _select_by_roots(
                conn, [rid for rid in root_ids if rid in thoughts], thoughts, cache, max_nodes, config
            )
        else:
            ordered = sorted(
                thoughts.keys(),
                key=lambda tid: (-cache.get(tid, (0, 1.0, 0.0))[2], tid),
            )
            selected = ordered[:max_nodes]

        selected_set = set(selected)
        edges: list[dict[str, Any]] = []
        if selected_set:
            for rel_id, source, target, rel_type, status, provenance in _edge_rows(
                conn, selected_set, include_suggested
            ):
                if source not in selected_set or target not in selected_set:
                    continue
                edges.append({
                    "id": rel_id,
                    "source": source,
                    "target": target,
                    "type": rel_type,
                    "direction": "SOURCE_TO_TARGET",
                    "label": direction_label(rel_type, "OUT"),
                    "reverseLabel": direction_label(rel_type, "IN"),
                    "symmetric": is_symmetric(rel_type),
                    "provenance": provenance,
                    "status": status,
                    "display": connection_display(provenance, status),
                    "weight": round(
                        provenance_weight(provenance, status, config) * relationship_weight(rel_type), 4
                    ),
                })

        nodes: list[dict[str, Any]] = []
        for thought_id in selected:
            row = thoughts.get(thought_id)
            if row is None:
                continue
            degree, hub_factor, importance = cache.get(thought_id, (0, 1.0, 0.0))
            cluster_id, cluster_label = clusters.get(thought_id, ("", ""))
            nodes.append({
                "id": thought_id,
                "displayLabel": sanitize_label(
                    _display_title(row["title"] or "", row["current_text"] or row["original_text"]), 90
                ),
                #: The entity boundary.  v0.2 only ever emits THOUGHT_ENTITY;
                #: the field exists so external knowledge can be added later
                #: without a thought ever being silently reinterpreted.
                "entityType": ENTITY_THOUGHT,
                "lifecycleState": row["type"],
                "status": row["status"],
                "createdAt": row["created_at"],
                "isDemo": bool(row["is_demo"]),
                "degree": degree,
                "hubFactor": round(hub_factor, 4),
                "importance": round(importance, 4),
                "isHub": hub_factor < 1.0,
                "clusterId": cluster_id,
                "clusterLabel": cluster_label,
                "clusterAuthority": "AUTOMATICALLY_DISCOVERED_CLUSTER" if cluster_id else "",
            })

    nodes.sort(key=lambda node: (-node["importance"], node["id"]))
    return {
        "generatedAt": None,           # filled by the caller if it wants a stamp
        "derived": True,
        "canonical": False,
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "nodes": nodes,
        "edges": edges,
        "clusters": cluster_summary(index),
        "layout": {
            "persisted": False,
            "coordinatesIncluded": False,
            "note": (
                "Layout is a client-side, disposable concern. No screen coordinate, camera "
                "state, region or animation value is computed, stored or implied here, and "
                "presentation geometry is never used to infer a thought's meaning."
            ),
        },
        "bounds": {
            "maxNodes": max_nodes,
            "rootIds": list(root_ids or []),
            "includeGraveyard": include_graveyard,
            "includeSuggested": include_suggested,
            "selection": "root-neighbourhood" if root_ids else "importance-desc",
        },
        "entityKinds": {
            ENTITY_THOUGHT: "A human thought. Canonical.",
            ENTITY_EXTERNAL: (
                "Reserved. External knowledge (repository, source file, class, function, "
                "document, pdf section, bug, test, commit, research source). Not produced "
                "by v0.2 and never a Thought."
            ),
        },
    }


def _select_by_roots(
    conn: sqlite3.Connection,
    roots: Sequence[str],
    thoughts: dict[str, sqlite3.Row],
    cache: dict[str, tuple[int, float, float]],
    max_nodes: int,
    config: RecallConfig,
) -> list[str]:
    """Breadth-first neighbourhood of the roots, nearest first."""
    if not roots:
        return []
    selected: list[str] = []
    seen: set[str] = set()
    frontier = [root for root in dict.fromkeys(roots) if root in thoughts]
    for root in frontier:
        if root not in seen:
            seen.add(root)
            selected.append(root)
    while frontier and len(selected) < max_nodes:
        marks = ",".join("?" for _ in frontier)
        neighbours: list[str] = []
        for row in conn.execute(
            f"SELECT source_id, target_id FROM relationships "
            f"WHERE status IN ('CONFIRMED','SUGGESTED') "
            f"AND (source_id IN ({marks}) OR target_id IN ({marks})) "
            f"ORDER BY type, created_at, id",
            list(frontier) + list(frontier),
        ):
            for candidate in (row["source_id"], row["target_id"]):
                if candidate in seen or candidate not in thoughts:
                    continue
                seen.add(candidate)
                neighbours.append(candidate)
        neighbours.sort(key=lambda tid: (-cache.get(tid, (0, 1.0, 0.0))[2], tid))
        for candidate in neighbours:
            if len(selected) >= max_nodes:
                break
            selected.append(candidate)
        frontier = neighbours
    return selected


def _edge_rows(
    conn: sqlite3.Connection, selected: set[str], include_suggested: bool
) -> list[tuple[str, str, str, str, str, str]]:
    ids = sorted(selected)
    out: list[tuple[str, str, str, str, str, str]] = []
    statuses = "('CONFIRMED','SUGGESTED')" if include_suggested else "('CONFIRMED')"
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT id, source_id, target_id, type, status, provenance FROM relationships "
            f"WHERE status IN {statuses} AND source_id IN ({marks}) AND target_id IN ({marks}) "
            f"ORDER BY type, created_at, id",
            chunk + chunk,
        ):
            out.append((row["id"], row["source_id"], row["target_id"], row["type"],
                        row["status"], row["provenance"]))
    return out


def _safe_select(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    try:
        return list(conn.execute(sql))
    except sqlite3.OperationalError:
        return []
