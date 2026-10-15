"""Recall V2 — deterministic seeds and bounded graph-neighbourhood expansion.

GRAPH EXPANSION DOES NOT REPLACE PRIMARY RETRIEVAL.  It enriches an already
scored candidate set.  Seeds come from lexical/trigram evidence; a thought with
no textual evidence at all can enter the results only through a bounded,
explained, provenance-weighted connection to a seed — and even then its
contribution is hard-capped well below what a direct textual match scores.

Adopted from the Graphify audit (concepts, not code — see
GRAPHIFY_ADAPTATION_NOTES.md):

  * per-term seed guarantee + seed dedupe by normalized label
    (fixes Graphify's own failures #1445 and #1766)
  * gap-ratio seed cutoff
  * hub suppression: hubs are *visited* but not *expanded as transit* unless they
    are seeds, and are penalised rather than filtered
  * deterministic traversal ordering — never physical row order
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from ..store import Vault, _display_title
from .config import DEFAULT_CONFIG, RecallConfig, direction_label, is_symmetric, relationship_weight
from .text import sanitize_label

#: Traversing *against* the direction of an asymmetric relationship costs this
#: much.  Symmetric types (RELATED_TO, CONTRADICTS, SUPPORTS) pay nothing,
#: because the v0.1 model defines them as mutual.
REVERSE_TRAVERSAL_FACTOR = 0.85


@dataclass
class GraphSignal:
    thought_id: str
    contribution: float
    origin: str                 # seed thought id
    origin_title: str
    depth: int
    rel_type: str
    direction: str              # OUT | IN
    label: str
    provenance: str
    status: str
    hub_factor: float
    path: tuple[str, ...]
    relationship_id: str = ""
    edge_weight: float = 0.0
    seed_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "contribution": round(self.contribution, 6),
            "origin": self.origin,
            "originTitle": self.origin_title,
            "depth": self.depth,
            "relType": self.rel_type,
            "direction": self.direction,
            "label": self.label,
            "provenance": self.provenance,
            "status": self.status,
            "hubFactor": round(self.hub_factor, 6),
            "edgeWeight": round(self.edge_weight, 6),
            "seedScore": round(self.seed_score, 6),
            "path": list(self.path),
            "relationshipId": self.relationship_id,
        }


def provenance_weight(provenance: str, status: str, config: RecallConfig) -> float:
    """Authority of an edge, from v0.1's *pair* of fields.

    v0.1 separates origin from consent: confirming an AI suggestion sets
    ``status='CONFIRMED'`` and ``confirmed_by='USER'`` while **keeping**
    ``provenance='AI_SUGGESTED'``.  So authority cannot be read from provenance
    alone — and search must never be the thing that upgrades it.

        USER_CONFIRMED + CONFIRMED  → 1.00   the user authored this link
        AI_SUGGESTED   + CONFIRMED  → 0.70   the user consented; origin preserved
        AI_SUGGESTED   + SUGGESTED  → 0.35   a proposal, nothing more
        USER_CONFIRMED + SUGGESTED  → 0.35   (not producible by v0.1, handled anyway)
        anything       + REMOVED    → excluded before this function is called
    """
    if status != "CONFIRMED":
        return config.weight_ai_suggested
    if provenance == "USER_CONFIRMED":
        return config.weight_user_confirmed
    return config.weight_ai_confirmed


def connection_display(provenance: str, status: str) -> str:
    """The wording shown to the user.  A suggestion is never presented as a
    confirmed connection, and confirming never rewrites the origin."""
    if status == "CONFIRMED" and provenance == "USER_CONFIRMED":
        return "Confirmed connection"
    if status == "CONFIRMED":
        return "Confirmed connection (AI-suggested origin retained)"
    return "Suggested connection"


def pick_seeds(
    scored: Sequence[tuple[str, float, int, str]],
    per_term_winners: Sequence[str],
    *,
    signatures: dict[str, str],
    config: RecallConfig = DEFAULT_CONFIG,
) -> list[str]:
    """Deterministic seed selection.

    ``scored`` is ``(thought_id, score, degree, signature)``.  Order is
    ``(-score, -degree, len(signature), thought_id)`` — Graphify's documented
    tie-break, with the durable UUID last so the order is total.

    Two guarantees from the audit are applied:

    * **gap cutoff** — stop adding score-ordered seeds once a candidate falls
      more than ``graph_seed_gap_ratio`` below the top score;
    * **per-term guarantee** — every distinct query term that matched anything
      still contributes its best document as a seed, so one rare word cannot
      hijack the whole neighbourhood (Graphify failure #1445).

    Seeds are deduplicated by *signature* (the normalized opening words), not by
    id, so several near-identical thoughts cannot consume every slot (#1766).
    """
    if not scored:
        return []
    ordered = sorted(
        scored,
        key=lambda item: (-item[1], -item[2], len(item[3]), item[0]),
    )
    top = ordered[0][1]
    if top <= 0:
        return []
    cutoff = top * (1.0 - config.graph_seed_gap_ratio)

    seeds: list[str] = []
    seen_signatures: set[str] = set()

    def offer(thought_id: str) -> bool:
        if thought_id in seeds:
            return False
        signature = signatures.get(thought_id) or thought_id
        if signature in seen_signatures:
            return False
        if len(seeds) >= config.graph_seeds_max:
            return False
        seeds.append(thought_id)
        seen_signatures.add(signature)
        return True

    for thought_id, score, _degree, _signature in ordered:
        if score < cutoff:
            break
        offer(thought_id)

    # Per-term guarantee: appended after the score-ordered seeds so that the
    # strongest evidence still leads, but no matched term is left unrepresented.
    for thought_id in per_term_winners:
        offer(thought_id)

    return seeds


def _fetch_edges(
    conn: sqlite3.Connection, thought_ids: Sequence[str]
) -> list[tuple[str, str, str, str, str, str, str]]:
    """Live edges incident to any of ``thought_ids``, in deterministic order."""
    ids = [tid for tid in dict.fromkeys(thought_ids) if tid]
    if not ids:
        return []
    out: list[tuple[str, str, str, str, str, str, str]] = []
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT id, source_id, target_id, type, status, provenance FROM relationships "
            f"WHERE status IN ('CONFIRMED','SUGGESTED') "
            f"AND (source_id IN ({marks}) OR target_id IN ({marks})) "
            f"ORDER BY type, created_at, id",
            list(chunk) + list(chunk),
        ):
            out.append(
                (row["id"], row["source_id"], row["target_id"], row["type"], row["status"],
                 row["provenance"], "")
            )
    return out


def _fetch_degrees(conn: sqlite3.Connection, thought_ids: Sequence[str]) -> dict[str, tuple[int, float]]:
    ids = [tid for tid in dict.fromkeys(thought_ids) if tid]
    out: dict[str, tuple[int, float]] = {}
    if not ids:
        return out
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ",".join("?" for _ in chunk)
        try:
            for row in conn.execute(
                f"SELECT thought_id, degree, hub_factor FROM recall_graph_cache "
                f"WHERE thought_id IN ({marks})",
                chunk,
            ):
                out[row["thought_id"]] = (int(row["degree"]), float(row["hub_factor"]))
        except sqlite3.OperationalError:
            pass
    return out


def _fetch_titles(conn: sqlite3.Connection, thought_ids: Sequence[str]) -> dict[str, str]:
    ids = [tid for tid in dict.fromkeys(thought_ids) if tid]
    out: dict[str, str] = {}
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT id, title, current_text, original_text FROM thoughts WHERE id IN ({marks})",
            chunk,
        ):
            out[row["id"]] = _display_title(row["title"] or "", row["current_text"] or row["original_text"])
    return out


def expand_neighbourhood(
    vault: Vault,
    seed_scores: dict[str, float],
    *,
    config: RecallConfig = DEFAULT_CONFIG,
    hub_threshold: int | None = None,
) -> tuple[dict[str, GraphSignal], dict[str, Any]]:
    """Bounded BFS from the seeds.

    Bounds, all deterministic and all documented:

    | Bound | Value |
    |---|---|
    | maximum depth | ``graph_max_depth`` (2) |
    | maximum expanded nodes | ``graph_max_nodes`` (40) |
    | edge eligibility | ``status IN (CONFIRMED, SUGGESTED)`` — REMOVED never traversed |
    | seed score threshold | ``graph_min_seed_score`` (1.5) |
    | hub transit | a node above the hub threshold is visited but not expanded, unless it is a seed |
    | contribution ceiling | ``graph_max_contribution`` (2.0) |

    When several paths reach the same thought the **maximum** contribution wins,
    not the sum.  Summing would let a hub accumulate influence from every route
    through it, which is exactly the pollution hub suppression exists to prevent.
    """
    diagnostics: dict[str, Any] = {
        "seedsConsidered": len(seed_scores),
        "seedsUsed": 0,
        "depthLimit": config.graph_max_depth,
        "nodeLimit": config.graph_max_nodes,
        "hubThreshold": hub_threshold,
        "hubsSkippedAsTransit": [],
        "edgesExamined": 0,
        "nodesReached": 0,
        "truncated": False,
        "deterministicOrder": "type, created_at, id",
    }
    eligible = {
        thought_id: score
        for thought_id, score in seed_scores.items()
        if score >= config.graph_min_seed_score
    }
    if not eligible:
        diagnostics["reason"] = "no seed reached the minimum score for traversal"
        return {}, diagnostics
    diagnostics["seedsUsed"] = len(eligible)

    signals: dict[str, GraphSignal] = {}
    titles: dict[str, str] = {}

    with vault._connection() as conn:
        if hub_threshold is None:
            try:
                stored = conn.execute(
                    "SELECT value FROM recall_meta WHERE key='hub_threshold'"
                ).fetchone()
                hub_threshold = int(stored["value"]) if stored else config.hub_min_degree
            except sqlite3.OperationalError:
                hub_threshold = config.hub_min_degree
        diagnostics["hubThreshold"] = hub_threshold
        titles.update(_fetch_titles(conn, list(eligible)))

        # frontier entries: (thought_id, depth, seed_id, seed_score, path)
        frontier: list[tuple[str, int, str, float, tuple[str, ...]]] = [
            (thought_id, 0, thought_id, score, (thought_id,))
            for thought_id, score in sorted(eligible.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        visited: set[str] = {item[0] for item in frontier}
        expanded = 0

        for depth in range(config.graph_max_depth):
            current = [item for item in frontier if item[1] == depth]
            if not current:
                break
            degrees = _fetch_degrees(conn, [item[0] for item in current])
            edges = _fetch_edges(conn, [item[0] for item in current])
            diagnostics["edgesExamined"] += len(edges)

            next_frontier: list[tuple[str, int, str, float, tuple[str, ...]]] = []
            incident: dict[str, list[tuple]] = {}
            for edge in edges:
                incident.setdefault(edge[1], []).append(edge)
                incident.setdefault(edge[2], []).append(edge)

            for node, node_depth, seed_id, seed_score, path in current:
                if node_depth != depth:
                    continue
                node_degree, _node_hub = degrees.get(node, (0, 1.0))
                is_seed = node in eligible
                if node_degree > hub_threshold and not is_seed:
                    # Visited, and still eligible to appear as a result — but not
                    # used as transit.  A penalty plus a transit rule, never a
                    # deletion: the hub itself remains reachable.
                    diagnostics["hubsSkippedAsTransit"].append(node)
                    continue
                for edge in incident.get(node, ()):
                    rel_id, source, target, rel_type, status, provenance, _ = edge
                    other = target if node == source else source
                    direction = "OUT" if node == source else "IN"
                    other_degree, other_hub = degrees.get(other, (0, 1.0))
                    if other not in degrees:
                        fetched = _fetch_degrees(conn, [other])
                        other_degree, other_hub = fetched.get(other, (0, 1.0))
                        degrees[other] = (other_degree, other_hub)

                    edge_weight = (
                        provenance_weight(provenance, status, config)
                        * relationship_weight(rel_type)
                        * other_hub
                        * (1.0 if is_symmetric(rel_type) or direction == "OUT" else REVERSE_TRAVERSAL_FACTOR)
                    )
                    contribution = (
                        seed_score
                        * config.graph_edge_base
                        * edge_weight
                        * (config.graph_depth_decay ** depth)
                    )
                    contribution = min(contribution, config.graph_max_contribution)
                    if contribution <= 0:
                        continue

                    previous = signals.get(other)
                    new_path = path + (other,)
                    if previous is None or contribution > previous.contribution:
                        if other not in titles:
                            titles.update(_fetch_titles(conn, [other]))
                        signals[other] = GraphSignal(
                            thought_id=other,
                            contribution=contribution,
                            origin=seed_id,
                            origin_title=sanitize_label(titles.get(seed_id, seed_id), 80),
                            depth=depth + 1,
                            rel_type=rel_type,
                            direction=direction,
                            label=direction_label(rel_type, direction),
                            provenance=provenance,
                            status=status,
                            hub_factor=other_hub,
                            path=new_path,
                            relationship_id=rel_id,
                            edge_weight=edge_weight,
                            seed_score=seed_score,
                        )
                    if other not in visited and expanded < config.graph_max_nodes:
                        visited.add(other)
                        expanded += 1
                        next_frontier.append((other, depth + 1, seed_id, seed_score, new_path))
                    elif other not in visited:
                        diagnostics["truncated"] = True

            frontier = next_frontier

    # Seeds never receive a graph contribution from themselves.
    for seed_id in eligible:
        signals.pop(seed_id, None)

    diagnostics["nodesReached"] = len(signals)
    diagnostics["expandedNodes"] = expanded
    diagnostics["originTitles"] = {
        seed_id: sanitize_label(titles.get(seed_id, seed_id), 80) for seed_id in sorted(eligible)
    }
    return signals, diagnostics


def relationship_context_for(
    signal: GraphSignal, titles: dict[str, str]
) -> dict[str, Any]:
    """Render a graph signal as the ``RelationshipContext`` the UI shows."""
    return {
        "thoughtId": signal.origin,
        "displayTitle": sanitize_label(signal.origin_title or titles.get(signal.origin, signal.origin), 80),
        "relType": signal.rel_type,
        "direction": signal.direction,
        "label": signal.label,
        "provenance": signal.provenance,
        "status": signal.status,
        "display": connection_display(signal.provenance, signal.status),
        "depth": signal.depth,
        "via": list(signal.path),
        "relationshipId": signal.relationship_id,
    }


def neighbourhood(
    vault: Vault, thought_id: str, *, depth: int = 1, config: RecallConfig = DEFAULT_CONFIG
) -> dict[str, Any]:
    """Connection Explorer V2: immediate neighbours of one node.

    Bounded, direction-aware, provenance-labelled.  Returns the node, its
    neighbours with the direction and wording of each edge, and the derived
    hub statistics — everything the explorer needs to render one level without
    loading the graph.
    """
    depth = max(1, min(int(depth), config.graph_max_depth))
    # Degrees and hub factors are DERIVED.  Refresh them here rather than trusting
    # the caller, so this entry point is self-healing after clear_derived_data()
    # and never reports degree 0 for a node that plainly has edges.
    from .index import RecallIndex

    RecallIndex(vault, config).ensure_fresh()
    with vault._connection() as conn:
        row = conn.execute(
            "SELECT id, title, current_text, original_text, type, status, created_at "
            "FROM thoughts WHERE id=?",
            (thought_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Thought not found: {thought_id}")
        root = {
            "id": row["id"],
            "displayTitle": _display_title(row["title"] or "", row["current_text"] or row["original_text"]),
            "type": row["type"],
            "status": row["status"],
            "createdAt": row["created_at"],
        }
        levels: list[list[dict[str, Any]]] = []
        seen: set[str] = {thought_id}
        frontier = [thought_id]
        for level in range(depth):
            if not frontier:
                break
            edges = _fetch_edges(conn, frontier)
            degrees = _fetch_degrees(conn, list({e[1] for e in edges} | {e[2] for e in edges}))
            titles = _fetch_titles(
                conn, list({e[1] for e in edges} | {e[2] for e in edges})
            )
            rendered: list[dict[str, Any]] = []
            next_frontier: list[str] = []
            for rel_id, source, target, rel_type, status, provenance, _ in edges:
                for node, other, direction in ((source, target, "OUT"), (target, source, "IN")):
                    if node not in frontier:
                        continue
                    degree, hub = degrees.get(other, (0, 1.0))
                    rendered.append({
                        "relationshipId": rel_id,
                        "from": node,
                        "thoughtId": other,
                        "displayTitle": sanitize_label(titles.get(other, other), 90),
                        "relType": rel_type,
                        "direction": direction,
                        "label": direction_label(rel_type, direction),
                        "provenance": provenance,
                        "status": status,
                        "display": connection_display(provenance, status),
                        "symmetric": is_symmetric(rel_type),
                        "degree": degree,
                        "hubFactor": round(hub, 4),
                        "edgeWeight": round(
                            provenance_weight(provenance, status, config) * relationship_weight(rel_type),
                            4,
                        ),
                        "level": level + 1,
                    })
                    if other not in seen:
                        seen.add(other)
                        next_frontier.append(other)
            rendered.sort(key=lambda item: (item["level"], item["relType"], item["thoughtId"]))
            levels.append(rendered)
            frontier = next_frontier
        degree, hub = _fetch_degrees(conn, [thought_id]).get(thought_id, (0, 1.0))
    return {
        "root": root,
        "degree": degree,
        "hubFactor": round(hub, 4),
        "levels": levels,
        "neighbours": [item for level in levels for item in level],
        "bounds": {"depth": depth, "deterministicOrder": "type, created_at, id"},
    }
