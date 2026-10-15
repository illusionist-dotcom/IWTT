"""Recall V2 — derived clustering / community organisation.

AUTOMATICALLY DISCOVERED CLUSTERS ARE DERIVED DATA.  They are labelled
``AUTOMATICALLY_DISCOVERED_CLUSTER`` everywhere they surface, they can be deleted
and rebuilt at will, and they never become canonical truth.  A name a user gives
a cluster would be a separate canonical annotation attached to a derived id —
that is future work, not v0.2.

ALGORITHM CHOICE, AND WHY IT IS NOT LEIDEN
------------------------------------------
The audit records that Graphify reaches Leiden through
``graspologic_native`` → ``graspologic.partition.leiden`` →
``nx.community.louvain_communities``, i.e. through a native extension or
NetworkX.  The mission forbids adding a massive dependency solely for
clustering, and IWTT v0.1 has **zero** third-party dependencies.  So v0.2
implements a modest, fully deterministic two-stage method in the standard
library:

  1. **connected components** over the undirected projection of LIVE
     relationships — the honest answer for a sparse personal graph, where most
     threads simply are not connected to each other;
  2. **synchronous label propagation** inside components larger than
     ``cluster_labelprop_min_size``, with a fixed iteration cap and a
     smallest-label-string tie-break, so the partition is reproducible.

Mechanisms adopted from the audit (concepts, re-implemented):
  * confidence/provenance-scaled edge weights (matrix #20) — suggested edges pull
    less strongly than confirmed ones;
  * hub exclusion from *labelling* (matrix #21, ADAPT #25) — the highest-degree
    member of a thought cluster is usually an index-like note, so hubs are
    skipped when choosing the cluster's display label;
  * a self-loop-safe cohesion metric (matrix #23);
  * **stable cluster identity across rebuilds** (matrix #24) — a sha256 membership
    fingerprint plus greedy majority-overlap remapping, so a cluster id does not
    reshuffle every time the index is rebuilt.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Sequence

from ..store import Vault, _display_title
from .config import RecallConfig
from .expansion import provenance_weight
from .index import RecallIndex
from .text import sanitize_label

ALGORITHM = "components+labelprop"
AUTHORITY = "AUTOMATICALLY_DISCOVERED_CLUSTER"


def _member_fingerprint(members: Sequence[str]) -> str:
    """sha256 over the sorted member ids — the audit's membership signature."""
    digest = hashlib.sha256()
    for member in sorted(members):
        digest.update(member.encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def connected_components(adjacency: dict[str, set[str]]) -> dict[str, list[str]]:
    """Deterministic connected components.

    Seeds are visited in sorted id order and each component's member list is
    sorted, so component keys and contents are identical across runs and across
    rebuilds of an unchanged graph.
    """
    seen: set[str] = set()
    components: dict[str, list[str]] = {}
    for seed in sorted(adjacency):
        if seed in seen:
            continue
        stack = [seed]
        seen.add(seed)
        members: list[str] = []
        while stack:
            node = stack.pop()
            members.append(node)
            for neighbour in sorted(adjacency.get(node, ())):
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        members.sort()
        components[members[0]] = members
    return components


def label_propagation(
    members: Sequence[str],
    weighted_adjacency: dict[str, list[tuple[str, float]]],
    *,
    iterations: int,
) -> dict[str, str]:
    """Synchronous label propagation with a deterministic tie-break.

    Synchronous (all labels update from the previous round) so the result does
    not depend on visit order inside a round; ties are broken on the smallest
    label string, so the partition is reproducible run to run.
    """
    labels = {member: member for member in members}
    member_set = set(members)
    for _round in range(max(1, iterations)):
        proposed: dict[str, str] = {}
        for member in sorted(members):
            totals: dict[str, float] = {}
            for neighbour, weight in sorted(weighted_adjacency.get(member, ())):
                if neighbour in member_set and neighbour in labels:
                    totals[labels[neighbour]] = totals.get(labels[neighbour], 0.0) + weight
            if not totals:
                proposed[member] = labels[member]
                continue
            best = max(totals.values())
            winners = sorted(label for label, value in totals.items() if value == best)
            own = labels[member]
            proposed[member] = own if own in winners else winners[0]
        if all(proposed[member] == labels[member] for member in proposed):
            return proposed
        labels = proposed
    return labels


def cohesion_score(member_count: int, internal_edges: int) -> float:
    """Intra-cluster edge density in ``0..1``, self-loop safe.

    ``cohesion = internal_edges / (n·(n-1)/2)`` for ``n >= 2``; a single-member
    cluster is trivially cohesive (1.0).  Self-loops cannot exist — v0.1 has
    ``CHECK(source_id <> target_id)`` — and parallel edges between the same pair
    are counted once per relationship row, which is what the denominator's
    "possible pairs" reading expects.
    """
    if member_count <= 1:
        return 1.0
    possible = member_count * (member_count - 1) / 2
    return max(0.0, min(1.0, internal_edges / possible)) if possible else 0.0


def rebuild_clusters(index: RecallIndex, config: RecallConfig | None = None) -> int:
    """Recompute derived clusters.  Returns the number of distinct clusters."""
    config = config or index.config
    vault: Vault = index.vault
    with vault._connection() as conn:
        try:
            previous = {
                row["cluster_id"]: row["member_fingerprint"]
                for row in conn.execute(
                    "SELECT cluster_id, member_fingerprint FROM recall_clusters"
                )
            }
        except sqlite3.OperationalError:
            previous = {}
        previous_members: dict[str, set[str]] = {}
        try:
            for row in conn.execute("SELECT cluster_id, thought_id FROM recall_clusters"):
                previous_members.setdefault(row["cluster_id"], set()).add(row["thought_id"])
        except sqlite3.OperationalError:
            pass

        edges = index.live_edges(conn)
        thought_ids = [row["id"] for row in conn.execute("SELECT id FROM thoughts ORDER BY id")]
        degrees = {
            row["thought_id"]: (int(row["degree"]), float(row["hub_factor"]))
            for row in conn.execute(
                "SELECT thought_id, degree, hub_factor FROM recall_graph_cache"
            )
        } if _table_exists(conn, "recall_graph_cache") else {}
        titles = {
            row["id"]: _display_title(row["title"] or "", row["current_text"] or row["original_text"])
            for row in conn.execute(
                "SELECT id, title, current_text, original_text FROM thoughts"
            )
        }
        created = {
            row["id"]: row["created_at"] for row in conn.execute("SELECT id, created_at FROM thoughts")
        }

    adjacency: dict[str, set[str]] = {thought_id: set() for thought_id in thought_ids}
    weighted: dict[str, list[tuple[str, float]]] = {thought_id: [] for thought_id in thought_ids}
    pair_weights: dict[tuple[str, str], float] = {}
    for _rel_id, source, target, rel_type, status, provenance in (
        (edge[0], edge[1], edge[2], edge[3], edge[4], edge[5]) for edge in edges
    ):
        weight = provenance_weight(provenance, status, config)
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)
        weighted.setdefault(source, []).append((target, weight))
        weighted.setdefault(target, []).append((source, weight))
        key = (source, target) if source < target else (target, source)
        pair_weights[key] = pair_weights.get(key, 0.0) + weight

    components = connected_components(adjacency)

    assignments: dict[str, str] = {}
    for component_key, members in components.items():
        if len(members) <= config.cluster_labelprop_min_size:
            for member in members:
                assignments[member] = component_key
            continue
        sub_labels = label_propagation(
            members, weighted, iterations=config.cluster_labelprop_iterations
        )
        for member, label in sub_labels.items():
            assignments[member] = label

    grouped: dict[str, list[str]] = {}
    for member, label in assignments.items():
        grouped.setdefault(label, []).append(member)

    # --- stable identity across rebuilds ---------------------------------
    # Greedy one-to-one overlap matching against the previous partition, then a
    # membership fingerprint check.  A one-node fragment cannot inherit a name:
    # reuse requires a strict majority of BOTH the old and the new member sets.
    remapped: dict[str, str] = {}
    used: set[str] = set()
    candidates = sorted(
        ((label, sorted(members)) for label, members in grouped.items()),
        key=lambda item: (-len(item[1]), item[0]),
    )
    for label, members in candidates:
        fingerprint = _member_fingerprint(members)
        new_set = set(members)
        best_old: str | None = None
        best_overlap = 0
        for old_id, old_members in sorted(previous_members.items()):
            if old_id in used or not old_members:
                continue
            overlap = len(new_set & old_members)
            if overlap <= best_overlap:
                continue
            majority_of_new = overlap * 2 > len(new_set)
            majority_of_old = overlap * 2 > len(old_members)
            if overlap == len(new_set) and fingerprint == previous.get(old_id):
                best_old, best_overlap = old_id, overlap
                break
            if majority_of_new and majority_of_old:
                best_old, best_overlap = old_id, overlap
        if best_old:
            remapped[label] = best_old
            used.add(best_old)
        else:
            remapped[label] = "c-" + fingerprint[:12]

    rows: list[tuple] = []
    summaries: list[dict[str, Any]] = []
    for label in sorted(grouped):
        members = sorted(grouped[label])
        cluster_id = remapped[label]
        internal_edges = sum(
            1
            for (a, b) in pair_weights
            if a in set(members) and b in set(members)
        )
        cohesion = cohesion_score(len(members), internal_edges)
        display = choose_cluster_label(
            members, degrees=degrees, titles=titles, created=created
        )
        fingerprint = _member_fingerprint(members)
        for member in members:
            rows.append(
                (member, cluster_id, len(members), display, ALGORITHM, AUTHORITY, fingerprint)
            )
        summaries.append({
            "clusterId": cluster_id,
            "size": len(members),
            "label": display,
            "cohesion": round(cohesion, 4),
            "internalEdges": internal_edges,
            "authority": AUTHORITY,
            "memberFingerprint": fingerprint,
            "members": members,
        })

    with vault._transaction() as conn:
        index.ensure_schema(conn)
        conn.execute("DELETE FROM recall_clusters")
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO recall_clusters(thought_id,cluster_id,cluster_size,"
                "label,algorithm,authority,member_fingerprint) VALUES(?,?,?,?,?,?,?)",
                rows,
            )
    summaries.sort(key=lambda item: (-item["size"], item["clusterId"]))
    return len({row[1] for row in rows}) if rows else 0


def choose_cluster_label(
    members: Sequence[str],
    *,
    degrees: dict[str, tuple[int, float]],
    titles: dict[str, str],
    created: dict[str, str],
) -> str:
    """Deterministic, model-free cluster label.

    ADAPTATION (audit ADAPT #25): Graphify names a community after its
    highest-degree member, which in a *thought* graph is usually an index-like
    note rather than the idea the cluster is about.  v0.2 therefore prefers the
    most-connected **non-hub** member, breaking ties on the most recently created
    thought and then on the durable id.  Only if every member is a hub does the
    plain degree winner get used.
    """
    if not members:
        return "Automatically discovered cluster"

    def key(member: str, hubs_excluded: bool) -> tuple:
        degree, hub_factor = degrees.get(member, (0, 1.0))
        if hubs_excluded and hub_factor < 1.0:
            return (1, 0, "", member)          # hubs sort last
        return (0, -degree, _invert(created.get(member, "")), member)

    ordered = sorted(members, key=lambda member: key(member, True))
    winner = ordered[0]
    if degrees.get(winner, (0, 1.0))[1] < 1.0 and len(members) > 1:
        # every member is a hub — fall back to plain degree ordering
        winner = sorted(members, key=lambda member: key(member, False))[0]
    title = sanitize_label(titles.get(winner, winner), 70)
    return f"Automatically discovered cluster: {title}"


def _invert(timestamp: str) -> str:
    """Sort key that orders ISO timestamps most-recent-first, deterministically."""
    return "".join(chr(0x10FFFF - ord(char)) if char.isdigit() else char for char in timestamp)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def cluster_summary(index: RecallIndex) -> list[dict[str, Any]]:
    """Derived cluster listing for the UI, with cohesion and authority labels."""
    vault = index.vault
    with vault._connection() as conn:
        if not _table_exists(conn, "recall_clusters"):
            return []
        rows = conn.execute(
            "SELECT cluster_id, cluster_size, label, algorithm, authority, member_fingerprint, "
            "thought_id FROM recall_clusters ORDER BY cluster_size DESC, cluster_id, thought_id"
        ).fetchall()
        edges = index.live_edges(conn)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = grouped.setdefault(row["cluster_id"], {
            "clusterId": row["cluster_id"],
            "size": int(row["cluster_size"]),
            "label": row["label"],
            "algorithm": row["algorithm"],
            "authority": row["authority"],
            "memberFingerprint": row["member_fingerprint"],
            "members": [],
            "derived": True,
        })
        entry["members"].append(row["thought_id"])
    for entry in grouped.values():
        member_set = set(entry["members"])
        internal = sum(
            1 for _id, source, target, _t, _s, _p in edges
            if source in member_set and target in member_set
        )
        entry["internalEdges"] = internal
        entry["cohesion"] = round(cohesion_score(len(member_set), internal), 4)
    return sorted(grouped.values(), key=lambda item: (-item["size"], item["clusterId"]))
