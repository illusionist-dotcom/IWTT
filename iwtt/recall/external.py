"""External-knowledge foundation — interfaces and boundaries ONLY.

v0.2 does **not** ingest repositories, parse ASTs, read PDFs, or create external
entities.  This module exists so that a future version can represent external
knowledge *without pretending external entities are human thoughts*, and so the
boundary is expressible, documented and **tested now** rather than negotiated
later under schedule pressure.

THE RULE
--------
A Thought is something a person thought.  A source file, a function, a bug, a
commit or a PDF section is not a thought.  Turning one into a Thought would:

  * put machine-extracted records inside the sacred, immutable capture table;
  * make ``original_text`` mean "text a human wrote" and "text a parser emitted"
    at the same time, which destroys the one guarantee IWTT makes;
  * let a rebuild of an ingestion pipeline rewrite the user's memory.

So the boundary is a *namespace* boundary, enforced by validation here, and the
v0.2 canonical schema is **unchanged**.  No column was added, no table was
created, and no migration touches ``thoughts``.

WHY NO SCHEMA CHANGE
--------------------
The mission's instruction is explicit: "Prefer documentation/interfaces over
premature schema expansion."  A future version that does ingest external
knowledge should add a *separate* canonical-adjacent registry (its own table, its
own id namespace, its own provenance rules) rather than widening ``thoughts``.
The shape of that registry is specified below as ``ExternalEntityRef`` so the
decision is already made and reviewable; nothing here writes to the database.

CROSS-DOMAIN RELATIONSHIPS
--------------------------
The chain the mission asks about —

    Thought → Experiment → Project → Source File → Function → Bug → Fix → New Thought

— is expressible with v0.1's existing relationship vocabulary on the
Thought/Experiment/Project segment, and needs only *new relationship types* (never
new meaning for ``Thought``) on the external segment.  ``CROSS_DOMAIN_DESIGN``
below records exactly which existing types carry over, which new types would be
needed, and which direction each edge must point.  It is design, not code: v0.2
creates no such relationship, and ``RELATIONSHIP_TYPES`` in ``store.py`` is
unchanged (its CHECK constraint still admits only the original 11 values).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: The two entity kinds.  ``THOUGHT_ENTITY`` is the only kind IWTT v0.2 can
#: actually create; ``EXTERNAL_KNOWLEDGE_ENTITY`` is reserved.
ENTITY_THOUGHT = "THOUGHT_ENTITY"
ENTITY_EXTERNAL = "EXTERNAL_KNOWLEDGE_ENTITY"
ENTITY_KINDS: tuple[str, ...] = (ENTITY_THOUGHT, ENTITY_EXTERNAL)

#: Reserved external entity types.  Closed vocabulary, deliberately lowercase and
#: deliberately *not* overlapping ``THOUGHT_TYPES``.
EXTERNAL_ENTITY_TYPES: tuple[str, ...] = (
    "repository",
    "source_file",
    "class",
    "function",
    "document",
    "pdf_section",
    "bug",
    "test",
    "commit",
    "research_source",
)

#: External-entity provenance.  Distinct from relationship provenance
#: (``USER_CONFIRMED`` / ``AI_SUGGESTED``), because it answers a different
#: question: not "who asserted this link?" but "where did this record come from?".
EXTERNAL_PROVENANCE: tuple[str, ...] = (
    "USER_IMPORTED",       # the user deliberately added this external reference
    "TOOL_EXTRACTED",      # a parser/ingestor emitted it
    "AI_SUGGESTED",        # a model proposed it; never authoritative
)


class EntityBoundaryError(ValueError):
    """Raised when something tries to cross the Thought/external boundary."""


@dataclass(frozen=True)
class ExternalEntityRef:
    """The *interface* a future external-knowledge registry would store.

    Not persisted in v0.2.  Exists so the shape is reviewable now, and so the
    validation rules below have something to validate.

    ``anchor`` is the IWTT equivalent of Graphify's ``source_location`` (``L42``):
    the audit notes that a line number is only meaningful for imported external
    files, while an IWTT-native anchor is a thought revision id.  Both are
    expressible here without mixing their namespaces.
    """

    entity_type: str
    label: str
    uri: str = ""
    provenance: str = "USER_IMPORTED"
    checksum: str = ""
    anchor: str = ""
    ingested_by: str = ""
    ingested_at: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.entity_type not in EXTERNAL_ENTITY_TYPES:
            raise EntityBoundaryError(
                f"unknown external entity type {self.entity_type!r}; the vocabulary is closed: "
                + ", ".join(EXTERNAL_ENTITY_TYPES)
            )
        if self.provenance not in EXTERNAL_PROVENANCE:
            raise EntityBoundaryError(
                f"unknown external provenance {self.provenance!r}; expected one of "
                + ", ".join(EXTERNAL_PROVENANCE)
            )
        if not self.label.strip():
            raise EntityBoundaryError("an external entity needs a label")

    @property
    def entity_kind(self) -> str:
        return ENTITY_EXTERNAL

    @property
    def is_thought(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["entityKind"] = self.entity_kind
        return data


@dataclass(frozen=True)
class ThoughtRef:
    """The other side of the boundary: a reference to a canonical Thought."""

    thought_id: str
    entity_kind: str = ENTITY_THOUGHT

    @property
    def is_thought(self) -> bool:
        return True


def entity_kind_of(entity: Any) -> str:
    kind = getattr(entity, "entity_kind", None)
    if kind in ENTITY_KINDS:
        return kind
    if isinstance(entity, dict) and entity.get("entityKind") in ENTITY_KINDS:
        return entity["entityKind"]
    return ENTITY_THOUGHT


def assert_not_thought(entity: Any, *, context: str = "") -> None:
    """Guard: refuse to let an external entity be recorded as a Thought.

    This is the enforcement point the boundary depends on.  Any future ingestion
    path must call it before creating a canonical record, and the v0.2 test suite
    asserts that it actually refuses.
    """
    if entity_kind_of(entity) == ENTITY_EXTERNAL:
        raise EntityBoundaryError(
            "refusing to store an external-knowledge entity as a Thought"
            + (f" ({context})" if context else "")
            + ". External entities belong in their own registry with their own id "
              "namespace; a Thought is something a person thought."
        )


def assert_thought(entity: Any, *, context: str = "") -> None:
    if entity_kind_of(entity) != ENTITY_THOUGHT:
        raise EntityBoundaryError(
            "expected a THOUGHT_ENTITY" + (f" ({context})" if context else "")
        )


#: Cross-domain relationship design.  Documented, not implemented.
#:
#: ``segment`` says which part of the chain the edge belongs to; ``implementable``
#: says whether v0.1's existing relationship vocabulary already covers it.
CROSS_DOMAIN_DESIGN: tuple[dict[str, Any], ...] = (
    {
        "segment": "thought",
        "edge": "Thought → Experiment",
        "relationshipType": "LED_TO",
        "direction": "source=thought, target=experiment",
        "implementable": True,
        "note": "Already expressible in v0.1: promote the thought's type, or link two thoughts.",
    },
    {
        "segment": "thought",
        "edge": "Experiment → Project",
        "relationshipType": "LED_TO / IMPLEMENTED_BY",
        "direction": "source=experiment, target=project",
        "implementable": True,
        "note": "Already expressible in v0.1.",
    },
    {
        "segment": "boundary",
        "edge": "Project → Source File",
        "relationshipType": "IMPLEMENTED_BY (cross-kind)",
        "direction": "source=project (THOUGHT_ENTITY), target=source_file (EXTERNAL)",
        "implementable": False,
        "blocker": (
            "v0.1's relationships table has CHECK constraints and foreign keys on "
            "thoughts(id) at BOTH ends. A cross-kind edge needs a new edge table whose "
            "endpoints carry (entity_kind, entity_id) — not a widening of thoughts."
        ),
    },
    {
        "segment": "external",
        "edge": "Source File → Function",
        "relationshipType": "CONTAINS (new)",
        "direction": "source=source_file, target=function",
        "implementable": False,
        "blocker": "External-internal edge; belongs in the external registry, not in IWTT's thought graph.",
    },
    {
        "segment": "external",
        "edge": "Function → Bug",
        "relationshipType": "HAS_BUG (new)",
        "direction": "source=function, target=bug",
        "implementable": False,
        "blocker": "As above.",
    },
    {
        "segment": "boundary",
        "edge": "Bug → Fix",
        "relationshipType": "FIXED_BY (new, external) or RESOLVED_BY",
        "direction": "source=bug, target=commit",
        "implementable": False,
        "blocker": "As above.",
    },
    {
        "segment": "boundary",
        "edge": "Fix → New Thought",
        "relationshipType": "INSPIRED_BY (cross-kind, reversed reading)",
        "direction": "source=thought (THOUGHT_ENTITY), target=commit (EXTERNAL)",
        "implementable": False,
        "blocker": (
            "This is the edge that closes the loop back into human memory. It must be "
            "created by the USER, with provenance USER_CONFIRMED, because a tool cannot "
            "know that a commit inspired a thought. An ingestion pipeline may PROPOSE it "
            "as AI_SUGGESTED and leave it SUGGESTED until the user confirms — exactly the "
            "v0.1 contract that already exists for thought↔thought links."
        ),
    },
)


def cross_domain_design() -> list[dict[str, Any]]:
    return [dict(item) for item in CROSS_DOMAIN_DESIGN]


#: What v0.2 actually provides, and what it deliberately defers.  Surfaced by
#: ``/api/external/foundation`` so the boundary is inspectable rather than
#: implied.
CAPABILITY_MANIFEST: dict[str, Any] = {
    "implementedInV02": [
        "Closed entity-kind vocabulary (THOUGHT_ENTITY, EXTERNAL_KNOWLEDGE_ENTITY)",
        "Closed external entity-type vocabulary (10 reserved types)",
        "External-entity provenance vocabulary, separate from relationship provenance",
        "ExternalEntityRef interface (not persisted)",
        "Boundary guards: assert_not_thought / assert_thought",
        "Documented cross-domain relationship design with named blockers",
        "entityType field on every GraphViewModel node, so a future renderer can "
        "mix entity kinds without reinterpreting a Thought",
    ],
    "explicitlyNotImplementedInV02": [
        "Repository ingestion",
        "AST parsing of any language",
        "PDF or document ingestion",
        "Any external_entities table or canonical schema change",
        "Any cross-kind relationship row",
        "Bug trackers, commits, tests as first-class entities",
    ],
    "canonicalSchemaChanged": False,
    "newCanonicalTables": [],
    "newCanonicalColumns": [],
    "invariant": (
        "No external entity can become a Thought. thoughts.original_text continues to mean "
        "'text a human wrote', protected by the v0.1 BEFORE UPDATE trigger."
    ),
}


def capability_manifest() -> dict[str, Any]:
    return dict(CAPABILITY_MANIFEST)


def foundation_payload() -> dict[str, Any]:
    """The whole external-knowledge foundation, as one inspectable payload.

    Documentation and interfaces only.  Calling this creates no table, writes no
    row and invents no entity — which is the point: the boundary is published so it
    can be reviewed before anything is ever built on it.
    """
    return {
        "implemented": False,
        "canonical": False,
        "persisted": False,
        "entityKinds": list(ENTITY_KINDS),
        "entityKindDefinitions": {
            ENTITY_THOUGHT: "A human thought. Canonical, immutable, authored by the user.",
            ENTITY_EXTERNAL: (
                "Reserved. External knowledge (repository, source file, class, function, "
                "document, pdf section, bug, test, commit, research source). Never a Thought."
            ),
        },
        "externalEntityTypes": list(EXTERNAL_ENTITY_TYPES),
        "externalProvenance": list(EXTERNAL_PROVENANCE),
        "capabilityManifest": capability_manifest(),
        "crossDomainDesign": cross_domain_design(),
        "guards": ["assert_not_thought", "assert_thought", "validate_candidate_entity"],
        "storageCreated": [],
        "note": (
            "Interfaces and documentation only. IWTT v0.2 ingests no repositories, parses no "
            "ASTs and reads no PDFs, and the canonical schema is unchanged. A future "
            "implementation gets its own registry and its own id namespace; it never becomes "
            "a Thought, and a Thought never becomes a file."
        ),
    }


def validate_candidate_entity(payload: Any) -> dict[str, Any]:
    """Validate a *hypothetical* future ingestion payload without storing it.

    Useful now for two reasons: it makes the boundary executable rather than
    aspirational, and it lets the v0.2 test suite prove that a malformed or
    over-reaching payload is refused before any future code path could reach the
    database.
    """
    if not isinstance(payload, dict):
        raise EntityBoundaryError("an external entity payload must be an object")
    kind = payload.get("entityKind") or payload.get("entity_kind") or ENTITY_EXTERNAL
    if kind not in ENTITY_KINDS:
        raise EntityBoundaryError(f"unknown entityKind {kind!r}")
    if kind == ENTITY_THOUGHT:
        raise EntityBoundaryError(
            "an external-knowledge payload may not declare itself a THOUGHT_ENTITY; "
            "thoughts are created only by Vault.create_thought from user-authored text"
        )
    reference = ExternalEntityRef(
        entity_type=str(payload.get("entityType") or payload.get("entity_type") or ""),
        label=str(payload.get("label") or ""),
        uri=str(payload.get("uri") or ""),
        provenance=str(payload.get("provenance") or "USER_IMPORTED"),
        checksum=str(payload.get("checksum") or ""),
        anchor=str(payload.get("anchor") or ""),
        ingested_by=str(payload.get("ingestedBy") or payload.get("ingested_by") or ""),
        ingested_at=str(payload.get("ingestedAt") or payload.get("ingested_at") or ""),
        attributes=dict(payload.get("attributes") or {}),
    )
    # ``assert_not_thought`` guards a *canonical write* — it refuses to let an
    # external entity be recorded as a Thought.  This function is not writing a
    # Thought, it is validating an external payload, so the check that belongs here
    # is the opposite one: whatever was built must actually be external.  (Calling
    # the canonical-write guard here rejected every valid payload, which is how the
    # mistake was caught.)
    if entity_kind_of(reference) != ENTITY_EXTERNAL:
        raise EntityBoundaryError(
            "validate_candidate_entity produced something that is not an "
            "EXTERNAL_KNOWLEDGE_ENTITY (validate_candidate_entity)"
        )
    return reference.to_dict()
