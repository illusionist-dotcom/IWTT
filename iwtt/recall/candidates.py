"""Structurally diverse candidate selection for link review.

This module exists because a purely lexical candidate pool cannot answer the
question *"is this thought linked to anything at all?"*.  If the candidates are
chosen by shared words, the AI is only ever shown the neighbours the
deterministic recall engine already found, and the interesting case — two
thoughts about the same problem that happen to use different words — never
reaches the model.

So candidates are drawn from five structurally different sources and merged:

1. **Lexical** — the v0.1 local search over the thought's own text.
2. **Graph** — neighbours within a bounded depth of the thought's stored
   relationships, and the neighbours of those neighbours.
3. **Community** — members of the same automatically discovered cluster.
4. **Tag** — thoughts sharing a tag.
5. **Lifecycle** — thoughts in the same lifecycle state, most recent first.
6. **Vault context** — a deterministic sample of the remaining thoughts.

Source 6 exists for a specific reason. Sources 1-5 all *require* a structural
overlap: a shared word, a shared edge, a shared community, a shared tag, a
shared lifecycle. Two thoughts that are deeply connected but phrase their
connection in entirely different words share **none** of those. Without a
residual pool the model would never be shown the very thoughts the milestone
is about, and the answer to "is this linked to anything?" would be an
artefact of the selection rule rather than a reading of the thoughts.

So the residual pool always tops the candidate list up to the bound, sampled
deterministically (newest first, then the oldest, so the sample spans the
vault rather than clustering at one end of it). It is the weakest signal and
the last resort, and it says so in its reason text.

This module is **derived and AI-free**.  It reads canonical data and the
rebuildable derived indexes; it never calls a provider and never writes
anything.  The AI provider is invoked by the caller, with the candidate pool
this module hands it.

The bound is deliberate: :data:`MAX_CANDIDATES` candidates at most, drawn
round-robin across the sources so that no single source can crowd out the
others.  A larger pool would cost more provider tokens and increase the
chance of a thin, generic proposal.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Sequence

from ..store import Vault, _capture_kind_map, _display_title
from .config import DEFAULT_CONFIG, RecallConfig

#: The hard ceiling on how many candidates a link review may examine.
MAX_CANDIDATES = 16

#: How many candidates a single source may contribute before the sources are
#: interleaved round-robin.
PER_SOURCE_SHARE = 6

#: The candidate sources, in the order they are drawn.
SOURCE_LEXICAL = "LEXICAL"
SOURCE_GRAPH = "GRAPH"
SOURCE_COMMUNITY = "COMMUNITY"
SOURCE_TAG = "TAG"
SOURCE_LIFECYCLE = "LIFECYCLE"
SOURCE_VAULT_CONTEXT = "VAULT_CONTEXT"

#: Human-readable explanation of every source, published with the result so the
#: reviewer knows how a candidate pool was assembled.
SOURCE_LABELS = {
    SOURCE_LEXICAL: "Shares words with the thought",
    SOURCE_GRAPH: "Reachable through a stored connection",
    SOURCE_COMMUNITY: "In the same automatically discovered community",
    SOURCE_TAG: "Shares a tag",
    SOURCE_LIFECYCLE: "In the same lifecycle state",
    SOURCE_VAULT_CONTEXT: "Sample of other thoughts in the vault",
}


def _text_of(thought: dict[str, Any]) -> str:
    return str(thought.get("currentText") or thought.get("originalText") or "")


def _lexical_candidates(vault: Vault, thought: dict[str, Any], limit: int) -> list[tuple[str, str]]:
    """Candidates from the frozen v0.1 local search."""
    query = _text_of(thought)[:400].strip()
    if not query:
        return []
    found: list[tuple[str, str]] = []
    try:
        rows = vault.search(query, include_graveyard=True, limit=limit)
    except Exception:
        return []
    for row in rows:
        thought_id = row.get("id")
        if thought_id:
            found.append((thought_id, SOURCE_LEXICAL))
    return found


def _graph_candidates(vault: Vault, thought_id: str, limit: int) -> list[tuple[str, str]]:
    """Candidates reachable through stored relationships (depth <= 2).

    Depth 2 is included on purpose: a thought two hops away is often the one
    the author never connected explicitly, which is exactly the case a
    lexical-only pool cannot reach.
    """
    try:
        view = vault.thought_neighbourhood(thought_id, depth=2)
    except Exception:
        return []

    found: list[tuple[str, str]] = []
    seen: set[str] = {thought_id}
    for level in view.get("levels", []):
        for neighbour in level:
            other = neighbour.get("thoughtId")
            if not other or other in seen:
                continue
            seen.add(other)
            found.append((other, SOURCE_GRAPH))
            if len(found) >= limit:
                return found
    return found


def _community_candidates(vault: Vault, thought_id: str, limit: int) -> list[tuple[str, str]]:
    """Candidates in the same automatically discovered cluster."""
    try:
        clusters = vault.clusters()
    except Exception:
        return []
    found: list[tuple[str, str]] = []
    for cluster in clusters:
        members = cluster.get("members") or []
        if thought_id not in members:
            continue
        for member in members:
            if member != thought_id:
                found.append((member, SOURCE_COMMUNITY))
            if len(found) >= limit:
                return found
        break
    return found


def _tag_candidates(vault: Vault, thought: dict[str, Any], limit: int) -> list[tuple[str, str]]:
    """Candidates sharing at least one tag."""
    tag_names = {
        str(tag.get("name") or tag.get("tagName") or "").strip().casefold()
        for tag in (thought.get("tags") or [])
    }
    tag_names.discard("")
    if not tag_names:
        return []
    found: list[tuple[str, str]] = []
    try:
        rows = vault.list_thoughts("ALL")
    except Exception:
        return []
    for row in rows:
        row_tags = {
            str(tag.get("name") or tag.get("tagName") or "").strip().casefold()
            for tag in (row.get("tags") or [])
        }
        if tag_names & row_tags:
            found.append((row["id"], SOURCE_TAG))
        if len(found) >= limit:
            break
    return found


def _lifecycle_candidates(vault: Vault, thought: dict[str, Any], limit: int) -> list[tuple[str, str]]:
    """Candidates in the same lifecycle state, most recently updated first."""
    lifecycle = str(thought.get("type") or "THOUGHT").upper()
    found: list[tuple[str, str]] = []
    try:
        rows = vault.list_thoughts("ALL")
    except Exception:
        return []
    for row in rows:
        if str(row.get("type") or "").upper() != lifecycle:
            continue
        found.append((row["id"], SOURCE_LIFECYCLE))
        if len(found) >= limit:
            break
    return found


def _vault_context_candidates(
    vault: Vault,
    excluded: set[str],
    limit: int,
) -> list[tuple[str, str]]:
    """A deterministic residual sample of the rest of the vault.

    The sample spans the vault rather than clustering at one end of it: the
    newest thoughts first, then the oldest, so a young and an old thought are
    both offered a chance to be judged.
    """
    try:
        rows = vault.list_thoughts("ALL")
    except Exception:
        return []
    usable = [row for row in rows if row.get("id") not in excluded]
    usable.sort(key=lambda item: (item.get("updatedAt", ""), item.get("createdAt", "")), reverse=True)
    ordered = usable + sorted(
        usable, key=lambda item: (item.get("createdAt", ""), item.get("id", ""))
    )
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in ordered:
        thought_id = row.get("id")
        if not thought_id or thought_id in seen:
            continue
        seen.add(thought_id)
        found.append((thought_id, SOURCE_VAULT_CONTEXT))
        if len(found) >= limit:
            break
    return found


def _interleave(pools: Sequence[list[tuple[str, str]]], limit: int) -> list[tuple[str, str]]:
    """Merge the per-source pools round-robin.

    Round-robin matters: taking the first pool until it is exhausted would
    let a large lexical result crowd out the structurally diverse candidates
    that the AI review exists to find.
    """
    merged: list[tuple[str, str]] = []
    seen: set[str] = set()
    depth = max((len(pool) for pool in pools), default=0)
    for index in range(depth):
        for pool in pools:
            if index >= len(pool):
                continue
            thought_id, source = pool[index]
            if thought_id in seen:
                continue
            seen.add(thought_id)
            merged.append((thought_id, source))
            if len(merged) >= limit:
                return merged
    return merged


def select_candidates(
    vault: Vault,
    thought: dict[str, Any],
    *,
    limit: int = MAX_CANDIDATES,
    exclude: Sequence[str] = (),
    config: RecallConfig | None = None,
) -> list[dict[str, Any]]:
    """Build a structurally diverse candidate pool for one thought.

    Returns candidate records shaped for the provider: the id, the display
    title, the lifecycle, the capture kind, and the original/current text.
    Each record also carries ``candidateSource`` and ``candidateReason`` so
    the reviewer can see why the candidate was offered.

    Candidates already connected to the thought by a stored relationship are
    excluded: re-proposing an existing link is noise, not a finding.
    """
    limit = max(1, min(int(limit), MAX_CANDIDATES))
    thought_id = str(thought.get("id") or "")
    if not thought_id:
        return []

    excluded = {thought_id}
    excluded.update(str(item) for item in exclude if item)
    for relation in thought.get("relationships") or []:
        if relation.get("status") in {"CONFIRMED", "SUGGESTED"}:
            other = (
                relation.get("targetId") if relation.get("sourceId") == thought_id
                else relation.get("sourceId")
            )
            if other:
                excluded.add(str(other))

    per_source = min(PER_SOURCE_SHARE, limit)
    pools: list[list[tuple[str, str]]] = [
        _lexical_candidates(vault, thought, per_source),
        _graph_candidates(vault, thought_id, per_source),
        _community_candidates(vault, thought_id, per_source),
        _tag_candidates(vault, thought, per_source),
        _lifecycle_candidates(vault, thought, per_source),
    ]
    merged = _interleave(pools, limit)

    # The structural sources only ever offer thoughts that overlap in some
    # way.  Two thoughts that are genuinely connected but phrase it in
    # different words overlap in none of them, so the residual pool tops the
    # list up.  Without this the model would never see the very case the
    # feature exists to find.
    if len(merged) < limit:
        already = {candidate_id for candidate_id, _source in merged}
        for candidate_id, source in _vault_context_candidates(
            vault, excluded | already, limit - len(merged)
        ):
            merged.append((candidate_id, source))

    candidates: list[dict[str, Any]] = []
    for candidate_id, source in merged:
        if candidate_id in excluded:
            continue
        try:
            record = vault.get_thought(candidate_id)
        except KeyError:
            continue
        candidates.append({
            "id": record["id"],
            "displayTitle": record.get("displayTitle") or _display_title(
                record.get("title") or "", record.get("currentText") or record.get("originalText") or ""
            ),
            "title": record.get("title") or "",
            "sourceType": record.get("sourceType", "THOUGHT"),
            "type": record.get("type", "THOUGHT"),
            "status": record.get("status", "ACTIVE"),
            "originalText": record.get("originalText") or "",
            "currentText": record.get("currentText") or "",
            "tags": [tag.get("name") for tag in (record.get("tags") or []) if tag.get("name")],
            "candidateSource": source,
            "candidateReason": SOURCE_LABELS.get(source, source),
        })
    return candidates


def sweep_candidates(
    vault: Vault,
    *,
    limit: int = 8,
    per_thought: int = 6,
) -> list[dict[str, Any]]:
    """A bounded, vault-wide sweep of unlinked thoughts.

    Picks thoughts that have the fewest stored relationships — the ones most
    likely to be missing a link — and builds a candidate pool for each.  The
    sweep is deliberately small and its cost is published with the result:
    it is one provider request per source thought.
    """
    limit = max(1, min(int(limit), 12))
    per_thought = max(1, min(int(per_thought), MAX_CANDIDATES))
    try:
        thoughts = vault.list_thoughts("ALL")
    except Exception:
        return []

    def degree(thought: dict[str, Any]) -> int:
        """How many live relationships this thought already has."""
        count = thought.get("relationshipCount")
        if isinstance(count, int):
            return count
        return sum(
            1 for relation in (thought.get("relationships") or [])
            if relation.get("status") in {"CONFIRMED", "SUGGESTED"}
        )

    ordered = sorted(thoughts, key=lambda item: (degree(item), item.get("updatedAt", ""), item["id"]))
    selected = ordered[:limit]

    sweep: list[dict[str, Any]] = []
    for source in selected:
        candidates = select_candidates(vault, source, limit=per_thought)
        if not candidates:
            continue
        sweep.append({
            "sourceId": source["id"],
            "sourceTitle": source.get("displayTitle") or source.get("title") or "Untitled thought",
            "sourceText": _text_of(source),
            "sourceType": source.get("sourceType", "THOUGHT"),
            "sourceLifecycle": source.get("type", "THOUGHT"),
            "candidates": candidates,
        })
    return sweep
