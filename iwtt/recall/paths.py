"""Recall V2 — bounded, direction-aware path discovery.

Answers "how did this thought become this project?", "what connects these two
ideas?" and "which earlier thought led here?".

Three rules that are not negotiable:

1. **Only stored relationships are ever traversed.**  No edge is inferred,
   suggested into existence, or invented by a model.  If no known path exists,
   IWTT says so plainly.
2. **Direction is respected.**  By default an asymmetric edge (``LED_TO``,
   ``INSPIRED_BY``, ``IMPLEMENTED_BY``, ``SUPERSEDES``, ``PART_OF``,
   ``DEPENDS_ON``, ``REVIVES``, ``TESTS``) may only be followed source→target.
   Symmetric types (``RELATED_TO``, ``CONTRADICTS``, ``SUPPORTS``) may be
   followed either way, because the v0.1 model *defines* them as mutual.
   ``undirected=True`` is an explicit opt-out, and even then every returned edge
   still reports its true stored direction and label.
3. **Bounded and deterministic.**  ``path_max_depth`` (6), ``path_max_visited``
   (5000), neighbours examined in ``(type, created_at, id)`` order, ``REMOVED``
   relationships never traversed.  No infinite traversal, no recursive query
   re-entry.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..store import Vault, _display_title
from .config import DEFAULT_CONFIG, RecallConfig, direction_label, is_symmetric, relationship_weight
from .expansion import connection_display, provenance_weight
from .text import sanitize_label


@dataclass
class PathResult:
    found: bool
    source_id: str
    target_id: str
    directed: bool
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    length: int = 0
    reason: str = ""
    visited: int = 0
    bounds: dict[str, Any] = field(default_factory=dict)
    provenance_summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "sourceId": self.source_id,
            "targetId": self.target_id,
            "directed": self.directed,
            "nodes": self.nodes,
            "edges": self.edges,
            "length": self.length,
            "reason": self.reason,
            "visited": self.visited,
            "bounds": self.bounds,
            "provenanceSummary": self.provenance_summary,
            "narrative": self.narrative(),
        }

    def narrative(self) -> str:
        """Human-readable walk, e.g.
        ``A —led to→ B —implemented by→ C  (2 hops, 1 suggested)``."""
        if not self.found:
            return self.reason or "No known path."
        parts = [sanitize_label(self.nodes[0].get("displayTitle", ""), 60)]
        for edge, node in zip(self.edges, self.nodes[1:]):
            arrow = f"—{edge['label']}→" if edge["direction"] == "OUT" else f"←{edge['label']}—"
            parts.append(f"{arrow} {sanitize_label(node.get('displayTitle', ''), 60)}")
        suggested = self.provenance_summary.get("SUGGESTED", 0)
        suffix = f"  ({self.length} hop{'s' if self.length != 1 else ''}"
        suffix += f", {suggested} suggested)" if suggested else ")"
        return " ".join(parts) + suffix


def _live_edges_for(conn: sqlite3.Connection, thought_ids: Sequence[str]) -> dict[str, list[tuple]]:
    """Adjacency over LIVE relationships only, in deterministic order."""
    ids = [tid for tid in dict.fromkeys(thought_ids) if tid]
    adjacency: dict[str, list[tuple]] = {tid: [] for tid in ids}
    if not ids:
        return adjacency
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT id, source_id, target_id, type, status, provenance, created_at "
            f"FROM relationships WHERE status IN ('CONFIRMED','SUGGESTED') "
            f"AND (source_id IN ({marks}) OR target_id IN ({marks})) "
            f"ORDER BY type, created_at, id",
            list(chunk) + list(chunk),
        ):
            entry = (
                row["id"], row["source_id"], row["target_id"], row["type"],
                row["status"], row["provenance"], row["created_at"],
            )
            adjacency.setdefault(row["source_id"], []).append(entry)
            adjacency.setdefault(row["target_id"], []).append(entry)
    for key in adjacency:
        adjacency[key].sort(key=lambda item: (item[3], item[6], item[0]))
    return adjacency


def _node_records(conn: sqlite3.Connection, thought_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    ids = list(dict.fromkeys(thought_ids))
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT id, title, current_text, original_text, type, status, created_at, "
            f"graveyard_at, resurrected_at FROM thoughts WHERE id IN ({marks})",
            chunk,
        ):
            out[row["id"]] = {
                "id": row["id"],
                "displayTitle": _display_title(row["title"] or "", row["current_text"] or row["original_text"]),
                "type": row["type"],
                "status": row["status"],
                "createdAt": row["created_at"],
                "graveyardAt": row["graveyard_at"],
                "resurrectedAt": row["resurrected_at"],
            }
    return out


def find_path(
    vault: Vault,
    source_id: str,
    target_id: str,
    *,
    directed: bool = True,
    config: RecallConfig = DEFAULT_CONFIG,
) -> PathResult:
    """Shortest stored-relationship path from ``source_id`` to ``target_id``.

    Breadth-first, so the result is a shortest path; within a level, neighbours
    are examined in ``(type, created_at, id)`` order, so *which* shortest path is
    returned is deterministic rather than incidental.
    """
    bounds = {
        "maxDepth": config.path_max_depth,
        "maxVisited": config.path_max_visited,
        "directed": directed,
        "traversalOrder": "type, created_at, id",
        "relationshipStatuses": ["CONFIRMED", "SUGGESTED"],
    }
    if not source_id or not target_id:
        return PathResult(False, source_id, target_id, directed, reason="Both endpoints are required.",
                          bounds=bounds)
    if source_id == target_id:
        return PathResult(False, source_id, target_id, directed,
                          reason="Source and target are the same thought; no path is needed.",
                          bounds=bounds)

    with vault._connection() as conn:
        for candidate in (source_id, target_id):
            if not conn.execute("SELECT 1 FROM thoughts WHERE id=?", (candidate,)).fetchone():
                return PathResult(False, source_id, target_id, directed,
                                  reason=f"Thought not found: {candidate}", bounds=bounds)

        parent: dict[str, tuple[str, tuple]] = {}
        visited: set[str] = {source_id}
        frontier: list[str] = [source_id]
        visited_count = 1
        found = False
        depth = 0

        while frontier and depth < config.path_max_depth:
            adjacency = _live_edges_for(conn, frontier)
            next_frontier: list[str] = []
            for node in frontier:
                for edge in adjacency.get(node, ()):
                    rel_id, source, target, rel_type, status, provenance, created_at = edge
                    if source == node:
                        other, direction = target, "OUT"
                    else:
                        other, direction = source, "IN"
                        if directed and not is_symmetric(rel_type):
                            # Following an asymmetric edge backwards would assert
                            # the opposite of what the user recorded.
                            continue
                    if other in visited:
                        continue
                    visited.add(other)
                    visited_count += 1
                    parent[other] = (node, edge + (direction,))
                    if other == target_id:
                        found = True
                        next_frontier = []
                        break
                    next_frontier.append(other)
                    if visited_count >= config.path_max_visited:
                        bounds["visitLimitReached"] = True
                        next_frontier = []
                        found = found or False
                        break
                if found or bounds.get("visitLimitReached"):
                    break
            if found or bounds.get("visitLimitReached"):
                break
            frontier = sorted(set(next_frontier))
            depth += 1

        if not found:
            nodes = _node_records(conn, [source_id, target_id])
            reason = (
                f"No known relationship path connects these two thoughts within "
                f"{config.path_max_depth} hop{'s' if config.path_max_depth != 1 else ''}"
                f"{' following stored directions' if directed else ''}. IWTT will not invent an "
                f"edge to close the gap."
            )
            if bounds.get("visitLimitReached"):
                reason = (
                    f"Search stopped after visiting {config.path_max_visited} thoughts without "
                    f"reaching the target. No path is claimed either way."
                )
            result = PathResult(False, source_id, target_id, directed, reason=reason,
                                visited=visited_count, bounds=bounds)
            result.nodes = [nodes.get(source_id, {"id": source_id}), nodes.get(target_id, {"id": target_id})]
            return result

        # Walk the parent chain back to the source.
        chain: list[tuple[str, tuple]] = []
        cursor = target_id
        while cursor in parent:
            previous, edge = parent[cursor]
            chain.append((cursor, edge))
            cursor = previous
            if len(chain) > config.path_max_depth:
                break
        chain.reverse()

        node_ids = [source_id] + [item[0] for item in chain]
        nodes = _node_records(conn, node_ids)
        edges: list[dict[str, Any]] = []
        provenance_summary: dict[str, int] = {}
        for _node_id, edge in chain:
            rel_id, source, target, rel_type, status, provenance, created_at, direction = edge
            edges.append({
                "relationshipId": rel_id,
                "sourceId": source,
                "targetId": target,
                "relType": rel_type,
                "direction": direction,
                "label": direction_label(rel_type, direction),
                "provenance": provenance,
                "status": status,
                "display": connection_display(provenance, status),
                "symmetric": is_symmetric(rel_type),
                "createdAt": created_at,
                "weight": round(
                    provenance_weight(provenance, status, config) * relationship_weight(rel_type), 4
                ),
            })
            provenance_summary[status] = provenance_summary.get(status, 0) + 1

        return PathResult(
            found=True,
            source_id=source_id,
            target_id=target_id,
            directed=directed,
            nodes=[nodes.get(nid, {"id": nid}) for nid in node_ids],
            edges=edges,
            length=len(edges),
            visited=visited_count,
            bounds=bounds,
            provenance_summary=provenance_summary,
        )


def lineage(
    vault: Vault, thought_id: str, *, max_depth: int = 3, config: RecallConfig = DEFAULT_CONFIG
) -> dict[str, Any]:
    """"Which earlier thoughts led here?" and "what did this become?"

    Follows asymmetric edges **with** their direction: ``upstream`` collects
    thoughts that led to / inspired / were implemented as this one, ``downstream``
    collects what this one led to.  Timestamps are canonical, so ordering by
    ``createdAt`` is an ordering of fact, not of inference.
    """
    max_depth = max(1, min(int(max_depth), config.path_max_depth))
    upstream: list[dict[str, Any]] = []
    downstream: list[dict[str, Any]] = []
    with vault._connection() as conn:
        if not conn.execute("SELECT 1 FROM thoughts WHERE id=?", (thought_id,)).fetchone():
            raise KeyError(f"Thought not found: {thought_id}")
        root_nodes = _node_records(conn, [thought_id])

        def walk(direction_wanted: str, sink: list[dict[str, Any]]) -> None:
            seen: set[str] = {thought_id}
            frontier = [thought_id]
            for depth in range(max_depth):
                if not frontier:
                    break
                adjacency = _live_edges_for(conn, frontier)
                records = []
                next_frontier: list[str] = []
                for node in sorted(frontier):
                    for edge in adjacency.get(node, ()):
                        rel_id, source, target, rel_type, status, provenance, created_at = edge
                        if source == node:
                            other, direction = target, "OUT"
                        else:
                            other, direction = source, "IN"
                            if not is_symmetric(rel_type):
                                continue
                        if other in seen:
                            continue
                        seen.add(other)
                        next_frontier.append(other)
                        records.append({
                            "thoughtId": other,
                            "relationshipId": rel_id,
                            "relType": rel_type,
                            "direction": direction,
                            "label": direction_label(rel_type, direction),
                            "provenance": provenance,
                            "status": status,
                            "display": connection_display(provenance, status),
                            "depth": depth + 1,
                            "relationshipCreatedAt": created_at,
                        })
                if records:
                    node_records = _node_records(conn, [item["thoughtId"] for item in records])
                    for record in records:
                        record.update(node_records.get(record["thoughtId"], {}))
                    records.sort(key=lambda item: (item.get("createdAt", ""), item["thoughtId"]))
                    sink.extend(records)
                frontier = next_frontier

        # upstream = edges pointing INTO this thought (things that led to it)
        seen: set[str] = {thought_id}
        frontier = [thought_id]
        for depth in range(max_depth):
            if not frontier:
                break
            adjacency = _live_edges_for(conn, frontier)
            records = []
            next_frontier = []
            for node in sorted(frontier):
                for edge in adjacency.get(node, ()):
                    rel_id, source, target, rel_type, status, provenance, created_at = edge
                    if target == node and source not in seen:
                        seen.add(source)
                        next_frontier.append(source)
                        records.append({
                            "thoughtId": source, "relationshipId": rel_id, "relType": rel_type,
                            "direction": "IN", "label": direction_label(rel_type, "IN"),
                            "provenance": provenance, "status": status,
                            "display": connection_display(provenance, status),
                            "depth": depth + 1, "relationshipCreatedAt": created_at,
                        })
            if records:
                node_records = _node_records(conn, [item["thoughtId"] for item in records])
                for record in records:
                    record.update(node_records.get(record["thoughtId"], {}))
                records.sort(key=lambda item: (item.get("createdAt", ""), item["thoughtId"]))
                upstream.extend(records)
            frontier = next_frontier

        walk("OUT", downstream)

    return {
        "root": root_nodes.get(thought_id, {"id": thought_id}),
        "upstream": upstream,
        "downstream": downstream,
        "bounds": {"maxDepth": max_depth, "traversalOrder": "type, created_at, id"},
        "note": (
            "Every link below is a stored relationship. Nothing was inferred, and no edge "
            "was created to fill a gap."
        ),
    }
