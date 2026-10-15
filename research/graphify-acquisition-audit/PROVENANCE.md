# PROVENANCE.md

**Audit 7 — Provenance.** A priority audit: Graphify advertises that every edge is
explained. This file establishes exactly what the code does, then compares it with
IWTT's requirements and identifies transferable patterns.

Verified against commit `10130e79ed9caf8f60d998073e097531eaa1a395`.

---

## 1. The advertisement versus the implementation

**Advertisement:** relationships carry a confidence label; uncertain ones are
"flagged for human review in GRAPH_REPORT.md"; the report explains surprising
connections.

**Implementation**, measured by package-wide grep over `graphify/*.py`:

| Field name | Occurrences | Where |
|---|---|---|
| `"provenance"` | **4** | all in `graphify/reflect.py` (memory citations) |
| `"evidence"` | **1** | a fallback key in `graphify/callflow_html.py` |
| `"extractor"` | **1** | a keyword list in the same file |
| `"origin"` | **1** | `build.py` (`_backfill_origin`) |
| `"why"` | 11 | generated prose in `graphify/analyze.py` |
| `"confidence"` | 200 | the actual provenance mechanism |
| `"source_file"` | 740 | the actual evidence anchor |
| `"source_location"` | 332 | the actual evidence anchor |

**Conclusion: there is no per-edge evidence object.** Edge provenance is *what*
(`relation`), *how sure* (`confidence`, occasionally `confidence_score`), *in what
syntactic situation* (`context`), and *where* (`source_file`, `source_location`).
Nothing records who or what asserted it, what the source text said, or how an
inference was derived.

## 2. What Graphify actually stores

| Aspect | Stored? | Implementation |
|---|---|---|
| Source attribution | Yes | `source_file` required on every edge |
| Location evidence | Mostly | `source_location` = `L<line>`; present where extractors emit it |
| Parser provenance | **No** | Nothing records which extractor produced an edge |
| Confidence kind | Yes, coarsely | `EXTRACTED` / `INFERRED` / `AMBIGUOUS`, edges only |
| Confidence degree | Partly | `confidence_score` (e.g. `0.85` on `indirect_call`), not universal |
| Evidence text | **No** | No field holds the justifying snippet |
| Explanation | **Generated** | `analyze.py` prose `why` strings at analysis time |
| Inference chain | **No** | Only the `context` tag hints at how |
| Verification flag | Yes, nodes | `verification = "unverified"` from the LLM evidence-binder |
| Build provenance | Yes, graph-level | `built_at_commit` via `export._git_head()` |
| Query/answer provenance | Yes, memory layer | `reflect.py::_provenance_for()` → `{q, date, outcome}`, capped at 5 |

### The three-tier vocabulary

| Label | Definition (`ARCHITECTURE.md`) | Producer |
|---|---|---|
| `EXTRACTED` | "explicitly stated in the source (e.g., an import statement, a direct call)" | deterministic parser |
| `INFERRED` | "a reasonable deduction (e.g., call-graph second pass, co-occurrence in context)" | deterministic resolver **or** LLM |
| `AMBIGUOUS` | "uncertain; flagged for human review" | resolver or LLM |

The most explicit reasoning in the codebase is the `indirect_call` edge:

> `confidence_score: 0.85` — "0.85 = 'strong inference' on the extraction-spec
> rubric. The symbol link is direct — the function is named right here — but that
> it is ever INVOKED is the inference, which is why this is not the 0.95 tier.
> Previously no score was emitted at all and the edge inherited the 0.5 default
> the rubric forbids (#2813)."

The *adjective* is stored (`0.85`); the *reasoning* is not.

### Strongest genuine mechanism: evidence binding

`graphify/llm.py::_bind_node_evidence()` is the most relevant provenance code for
IWTT's AI-suggestion risk:

- **Scope**: only `file_type == "code"` nodes from a **semantic (LLM)** pass,
  attributed to a file actually dispatched in that call.
- **Check**: does any identifier from the node's `label` or `id` occur
  (case-insensitive substring) in the source bytes the model was shown?
- **Outcome**: if none occur, set `verification = "unverified"` — **flag, never
  drop**; idempotent; skipped when the model already hedged (`INFERRED`/`AMBIGUOUS`).
- **Rationale**: a symbol a document merely *describes in prose* "is legitimately
  unverified — the model inferred it rather than read it."
- **Precision-first**: only code-typed nodes are checked (document labels are prose);
  both label and id are tested; identifiers under 3 chars are ignored because
  "they match too readily to be evidence".

### The memory-layer trail

`reflect.py` gets closest to real provenance:

- Outcome signals: `useful`, `dead_end`, `corrected` (`ingest.OUTCOMES`).
- Each citation contributes a **signed, time-decayed** value (half-life
  `_DEFAULT_HALF_LIFE_DAYS = 30.0`), so "a fresh dead end outweighs a months-old useful".
- Promotion is **corroboration-gated** (`_DEFAULT_MIN_CORROBORATION = 2`):
  "one save can't mint a trusted lesson."
- Each promoted node keeps a **provenance trail**: most-recent-first
  `(question, date, outcome)`, capped at `_PROVENANCE_CAP = 5`, resolved to a single
  canonical node id (ambiguous/stale citations skipped).
- Buckets: **preferred**, **tentative**, **contested** (recency decides),
  **known dead ends**, **corrections**.
- Scores rounded to `_SCORE_NDIGITS = 9` for cross-platform stability.

This is lifecycle-aware, deterministic, LLM-free, and stores *why* a belief is held
in the form of the questions that produced it.

## 3. Where explanations come from

`analyze.py` generates prose at analysis time (never attached to a record):

`"Edge tagged AMBIGUOUS (relation: {relation}) - confidence is low."` ·
`"High betweenness centrality ({score:.3f}) - this node is a cross-community bridge."` ·
`"'{label}' has {n} INFERRED edges - model-reasoned connections that need verification."` ·
`"{n} weakly-connected nodes found - possible documentation gaps or missing edges."` ·
`"Cohesion score {score} - nodes in this community are weakly interconnected."` ·
`"circular dependency"` · `"; ".join(reasons)`.

## 4. IWTT requirements versus Graphify capability

| IWTT requirement | Graphify equivalent | Verdict |
|---|---|---|
| Original human statement | None | **Missing** — IWTT owns this |
| Revision history | None | **Missing** |
| USER_CONFIRMED relation | None | **Missing** — critical gap |
| AI_SUGGESTED relation | Partial: `INFERRED` + `verification="unverified"` | **Insufficient** — `INFERRED` also covers deterministic resolution |
| Promotion history | Yes: tentative → preferred via corroboration | **Strong pattern to adapt** |
| Graveyard history | None | **Missing** |
| Resurrection history | None | **Missing** |
| Source project/document | Yes: `source_file`, `repo` tag, cross-repo guards | **Adopt** |
| Derived inference | Coarsely: confidence tag + numeric score | **Adapt with a richer vocabulary** |
| "Why does this edge exist?" | Generated prose, not stored | **Adapt**: store structured reason codes, render prose on demand |

The mismatch is structural. Graphify answers *"is this relationship trustworthy and
where did it come from?"* for machine-extracted code structure. IWTT must answer
*"did I say this, did I confirm it, or did a model suggest it — and what happened to
it since?"* for user-owned beliefs with a lifecycle.

## 5. Recommended IWTT provenance model (design only, not implemented)

### 5.1 Orthogonal fields instead of one enum

```yaml
relationship:
  # CANONICAL (user-owned)
  assertion: user_asserted | user_confirmed | user_rejected
  origin_ref: <thought revision id>        # the user statement that created it
  lifecycle: active | dormant | graveyard | resurrected
  history: [ {event, at, note} ]           # append-only, canonical

  # DERIVED (rebuildable, never authoritative)
  suggestion:
    source: deterministic | model | import | rule
    method: <closed vocabulary>            # shared_tag | co_revision | embedding_nn |
                                           # graph_neighbourhood | project_context
    score: 0.0-1.0
    evidence:
      - kind: shared_token | same_project | revision_proximity | neighbourhood
        ref: <canonical id or derived pointer>
    generated_at: <timestamp>
    generator_version: <string>            # rebuild can invalidate by version
  verification:
    state: unverified | verified | not_applicable
    checked_against: <what was consulted>
```

Three rules make this safe:

1. **Provenance kinds are orthogonal.** *Who asserted it* and *how confident* are
   separate fields. Graphify's overloaded `INFERRED` is the cautionary example.
2. **Derived provenance never deletes canonical data.** It may add a suggestion,
   mark something unverified, or lower a derived score — the "flag, never drop"
   principle.
3. **Generator version is part of the derived record**, so a better algorithm can
   invalidate its own old output without touching user data — the same reasoning as
   Graphify's `cache/ast/v{version}-s{schema}-g{grammars}/` namespacing.

### 5.2 Reason codes, rendered on demand

Store `method` + structured `evidence`; render the sentence ("connected because
both mention X and were revised the same week") in the UI at display time. This is
Graphify's `why`-string strategy, upgraded so the reasoning survives a re-render
and can be audited.

### 5.3 Corroboration and decay

- A single AI suggestion never becomes a displayed relationship; it enters a
  **tentative** state.
- Repeated independent signals promote it; contradictions demote it; recency decides
  ties. Half-life is configurable — Graphify defaults to 30 days; a personal thought
  system might want longer (**ESTIMATED** opening value: 180 days, to be tuned).
- Promotion records a capped trail (`_PROVENANCE_CAP = 5` is a reasonable default).

### 5.4 Lifecycle events as first-class history

Graphify has no lifecycle model; this is IWTT's own. The *shape* to borrow:
append-only, most-recent-first, capped, resolved to a single canonical id,
ambiguous entries skipped — applied to graveyard/resurrection events.

## 6. What to take and what to leave

**Take:** closed-vocabulary confidence plus a numeric score where a rule warrants
it · `source` + `location` anchors on every derived claim · flag-never-drop for
suspect AI output · cheap lexical evidence binding as a fabrication check ·
corroboration gating before promotion · signed, time-decayed scoring · a capped
most-recent-first provenance trail · derived annotations in a sidecar, never
stamped into canonical records · generator-version stamping · machine-checkable
reason codes with prose rendered at display time.

**Leave:** the three-value `INFERRED` bucket (too coarse for user-confirmed vs
AI-suggested) · line numbers as the primary anchor (IWTT anchors to a thought
revision) · graph-commit `built_at_commit` (replace with a vault checkpoint id) ·
the absence of a "who asserted this" field, which is unacceptable for IWTT.

## 7. Evidence anchors

`graphify/validate.py` (`VALID_CONFIDENCES`) · `ARCHITECTURE.md` (confidence table) ·
`graphify/extractors/engine.py` (`_semantic_reference_edge`, `indirect_call` +
`confidence_score`) · `graphify/llm.py` (`_bind_node_evidence`,
`_VERIFICATION_FIELD`, `_UNVERIFIED_VALUE`, `_label_identifiers`,
`_dispatched_source_text`) · `graphify/reflect.py` (`_provenance_for`,
`build_learning_overlay`, `write_learning_sidecar`, `LEARNING_SIDECAR_NAME`,
`_DEFAULT_HALF_LIFE_DAYS`, `_DEFAULT_MIN_CORROBORATION`, `_PROVENANCE_CAP`,
`_SCORE_NDIGITS`) · `graphify/ingest.py` (`OUTCOMES`, `save_query_result`) ·
`graphify/analyze.py` (`why` sites) · `graphify/export.py` (`_git_head`,
`backup_if_protected`) · `graphify/cache.py` (producer-version cache namespacing).
