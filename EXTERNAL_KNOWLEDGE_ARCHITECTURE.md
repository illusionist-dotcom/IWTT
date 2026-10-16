# EXTERNAL KNOWLEDGE ARCHITECTURE (IWTT v0.2)

**Status: foundation only. Interfaces, vocabulary and documentation — no ingestion.**

IWTT v0.2 reserves the namespace, entity types, provenance vocabulary, boundary
guards and cross-domain relationship design that a *future* version would need in
order to connect a human thought to external knowledge — a repository, a source file,
a function, a bug, a fix — and back again. It ingests nothing, parses nothing, stores
nothing, and changes no canonical schema.

Code: `iwtt/recall/external.py` (vocabulary, `ExternalEntityRef`, guards,
`foundation_payload()`), served read-only at `GET /api/external/foundation`.
Boundary tests: `tests/test_layering.py` (entity-kind vocabulary, guards, manifest).

---

## 1. Why this exists at all

The loop the mission describes is:

```
Thought → Experiment → Project → Source File → Function → Bug → Fix → New Thought
```

The first two segments are **already expressible in v0.1**, because Thought,
Experiment, Invention and Project are all `thoughts.type` values, and `LED_TO` /
`IMPLEMENTED_BY` already exist. The remaining segments cross a boundary that v0.1 does
not have: from *something a person thought* to *something a tool extracted from the
world*.

Crossing that boundary carelessly would destroy the thing that makes IWTT worth
having: `thoughts.original_text` means "text a human wrote", protected by a v0.1
`BEFORE UPDATE` trigger. If a source file could become a Thought, that sentence would
stop being true, and every guarantee built on it — the sacred contract, the
provenance model, the "what I actually wrote" panel — would quietly rot.

So v0.2 draws the line now, in code and in this document, and leaves the crossing for
a version that can afford to do it properly.

---

## 2. Entity kinds

Two kinds, closed vocabulary (`ENTITY_KINDS`):

| Kind | Definition |
| --- | --- |
| `THOUGHT_ENTITY` | "A human thought. Canonical, immutable, authored by the user." |
| `EXTERNAL_KNOWLEDGE_ENTITY` | "Reserved. External knowledge (repository, source file, class, function, document, pdf section, bug, test, commit, research source). Never a Thought." |

Every node in a `GraphViewModel` already carries `entityType` (`THOUGHT_ENTITY` in
v0.2). That is the one piece of this foundation that ships in a live payload, and it
exists so a future renderer can mix entity kinds **without reinterpreting a Thought**
— no renderer will ever have to guess whether a node is a memory or a file.

**The invariant, stated in the capability manifest:** *No external entity can become
a Thought. `thoughts.original_text` continues to mean "text a human wrote", protected
by the v0.1 `BEFORE UPDATE` trigger.*

---

## 3. Reserved external entity types

Ten, closed (`EXTERNAL_ENTITY_TYPES`). Constructing an `ExternalEntityRef` with
anything else raises `EntityBoundaryError`:

`repository`, `source_file`, `class`, `function`, `document`, `pdf_section`, `bug`,
`test`, `commit`, `research_source`

They are lowercase identifiers, deliberately unlike IWTT's SCREAMING_SNAKE canonical
vocabulary, so a stray external type can never be mistaken for a `thoughts.type`
(`THOUGHT`, `EXPERIMENT`, `INVENTION`, `PROJECT`).

---

## 4. External provenance — a separate vocabulary

`EXTERNAL_PROVENANCE = ("USER_IMPORTED", "TOOL_EXTRACTED", "AI_SUGGESTED")`.

This is **not** the relationship provenance vocabulary
(`USER_CONFIRMED` / `AI_SUGGESTED`), and the two are never merged. Relationship
provenance answers *"who asserts this connection?"*; external provenance answers
*"where did this external artefact come from?"*. Conflating them would let a tool's
extraction masquerade as a user's assertion — the exact failure mode v0.1's
provenance model was built to prevent.

---

## 5. `ExternalEntityRef` — the interface, not a table

```python
@dataclass(frozen=True)
class ExternalEntityRef:
    entity_type: str          # one of the ten reserved types
    label: str
    uri: str = ""
    provenance: str = "USER_IMPORTED"
    checksum: str = ""
    anchor: str = ""
    ingested_by: str = ""
    ingested_at: str = ""
    attributes: dict = field(default_factory=dict)

    entity_kind -> "EXTERNAL_KNOWLEDGE_ENTITY"    # property
    is_thought  -> False                          # property
    to_dict()   -> camelCase + entityKind
```

`__post_init__` validates the closed vocabularies and refuses an empty label.
**Nothing is persisted**: `foundation_payload()["storageCreated"] == []`.

`anchor` is IWTT's equivalent of Graphify's `source_location` (`L42`). The audit
notes that a line number is only meaningful for an imported external file, while an
IWTT-native anchor is a **thought revision id**. Both are expressible in one field
without mixing namespaces, which is why `anchor` is a string with a documented
convention rather than two typed columns pretending to be the same thing.

`ThoughtRef` is the mirror type for the canonical side, so a future cross-kind edge
has two distinct endpoint types to hold.

---

## 6. Boundary guards

Three functions, all raising `EntityBoundaryError`:

| Guard | Refuses |
| --- | --- |
| `assert_not_thought(entity, context="")` | Storing an external-knowledge entity as a Thought |
| `assert_thought(entity, context="")` | Treating anything that is not a `THOUGHT_ENTITY` as one |
| `validate_candidate_entity(reference)` | A candidate whose `entity_kind_of(reference)` is not `EXTERNAL_KNOWLEDGE_ENTITY` |

`assert_not_thought` is the enforcement point the whole boundary depends on: any
future ingestion path must call it **before** creating a canonical record. The test
suite asserts that it actually refuses, in both directions.

These guards exist now, before any ingestion code, on purpose. A boundary that is
only documented is a boundary that gets crossed by the first convenient shortcut; a
boundary that raises is a boundary a reviewer can point at.

---

## 7. Cross-domain relationship design

`CROSS_DOMAIN_DESIGN` documents all seven segments, with the relationship type each
would use, the direction, whether it is implementable today, and — where it is not —
**the specific thing that blocks it**.

| # | Edge | Type | Implementable | Note / blocker |
| --- | --- | --- | --- | --- |
| 1 | Thought → Experiment | `LED_TO` | **yes** | Already expressible in v0.1: promote the thought's type, or link two thoughts |
| 2 | Experiment → Project | `LED_TO` / `IMPLEMENTED_BY` | **yes** | Already expressible in v0.1 |
| 3 | Project → Source File | `IMPLEMENTED_BY` (cross-kind) | no | v0.1's `relationships` has CHECK constraints and foreign keys on `thoughts(id)` at **both** ends. A cross-kind edge needs a **new edge table** whose endpoints carry `(entity_kind, entity_id)` — not a widening of `thoughts` |
| 4 | Source File → Function | `CONTAINS` (new) | no | External-internal edge; belongs in the external registry, not in IWTT's thought graph |
| 5 | Function → Bug | `HAS_BUG` (new) | no | As above |
| 6 | Bug → Fix | `FIXED_BY` (new, external) or `RESOLVED_BY` | no | As above |
| 7 | Fix → New Thought | `INSPIRED_BY` (cross-kind, reversed reading) | no | **The edge that closes the loop back into human memory.** It must be created by the USER with provenance `USER_CONFIRMED`, because a tool cannot know that a commit inspired a thought. An ingestion pipeline may PROPOSE it as `AI_SUGGESTED` and leave it `SUGGESTED` until the user confirms — exactly the v0.1 contract that already exists for thought↔thought links |

Segment 7 is the reason this document exists. The loop is only worth closing if the
closing edge carries the same provenance discipline as every other relationship in
IWTT: a machine may suggest, only a human may confirm, and searching must never be
the thing that upgrades a suggestion.

---

## 8. Capability manifest

`CAPABILITY_MANIFEST` (served at `/api/external/foundation`) states what v0.2 does
and does not do, so no reader has to infer it:

**Implemented in v0.2**

1. Closed entity-kind vocabulary (`THOUGHT_ENTITY`, `EXTERNAL_KNOWLEDGE_ENTITY`)
2. Closed external entity-type vocabulary (10 reserved types)
3. External-entity provenance vocabulary, separate from relationship provenance
4. `ExternalEntityRef` interface (not persisted)
5. Boundary guards: `assert_not_thought` / `assert_thought`
6. Documented cross-domain relationship design with named blockers
7. `entityType` field on every `GraphViewModel` node, so a future renderer can mix
   entity kinds without reinterpreting a Thought

**Explicitly not implemented in v0.2**

* Repository or codebase ingestion, AST parsing, code intelligence
* PDF or large-scale document ingestion
* Any external-entity storage, registry or id namespace
* Cross-kind edges in `relationships`
* Any change to the canonical schema

**Schema impact:** `canonicalSchemaChanged: false`, `newCanonicalTables: []`,
`newCanonicalColumns: []`.

---

## 9. What a future implementation must do

Recorded here so the next version inherits constraints rather than rediscovering them:

1. **Its own registry, its own id namespace.** External entities get their own tables
   (`external_entities`, `external_edges`) keyed by their own ids. They never occupy a
   `thoughts.id`, and a `thoughts.id` never appears in an external registry as
   anything but an endpoint reference.
2. **A new edge table for cross-kind edges**, with `(entity_kind, entity_id)` at both
   ends. Widening `relationships` would break its foreign keys and its CHECK
   constraints, and with them every v0.1 guarantee about relationships.
3. **Call `assert_not_thought` on every write path** that could create a canonical
   record, and keep the tests that prove it refuses.
4. **Preserve the provenance distinction.** External provenance stays separate from
   relationship provenance. An `AI_SUGGESTED` cross-kind edge stays `SUGGESTED` until
   a user confirms it, and no automated pipeline may confirm on the user's behalf.
5. **Keep derived data derived.** Extracted symbols, file graphs and cached ASTs are
   derived artefacts: deletable, rebuildable, and never a source of canonical truth.
6. **Keep it optional and offline.** Ingestion must not become a requirement for
   recall, and must not introduce a network dependency into the recall path.

---

## 10. Explicitly out of scope for IWTT

Not deferred — rejected as a direction for this product:

* Turning IWTT into a code-analysis product.
* Repository/codebase AST ingestion as a v0.2 feature.
* PDF or large-scale external knowledge ingestion.
* Autonomous agents with write access to canonical data.
* Any feature that would let a machine-generated artefact be presented as something
  the user thought.
