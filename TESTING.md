# IWTT v0.2 Testing and Verification

Four gates, run in this order. All four are green in the current tree.

The suite and release scripts also run on Windows. Test SQLite connections are
explicitly closed before temporary directories are removed, and source-inspection
tests decode UTF-8 explicitly rather than relying on the host ANSI code page. Windows
11 performance measurements are recorded separately in `BENCHMARKS.md`.

```bash
python -m unittest discover -s tests -v        # gate 1 — 286 tests
python scripts/verify_release.py               # gate 2 — the v0.1 contract
python scripts/verify_release_v02.py           # gate 3 — the v0.2 contract
python scripts/benchmark_recall.py --suite     # gate 4 — measured performance
```

Plus two hygiene checks before any commit:

```bash
git diff --check          # no whitespace errors, no conflict markers
git status --short        # nothing unintended is about to be committed
```

---

## Gate 1 — Automated tests (286)

Tests use temporary directories and temporary SQLite databases; they never touch the
user's vault.

| Module | Tests | What it proves |
| --- | --- | --- |
| `test_store.py` | 15 | **The frozen v0.1 contract.** Search behaviour, original-text immutability, revisions, lifecycle, Graveyard/resurrection, relationship provenance and confirmation, events, JSON round-trip, duplicate-import rejection, backup integrity, derived deletion |
| `test_recall_engine.py` | 24 | Signals, tiers, morphology, coverage, IDF, trigrams, fuzzy matching, time windows *and their declared precision*, lifecycle, outcome states, ambiguity refusal, honest absence, explanations, the lexical-evidence gate |
| `test_graph_intelligence.py` | 22 | Edge authority, provenance preservation, direction-aware traversal, hub suppression, expansion bounds, path finding, lineage, neighbourhoods, `GraphViewModel`, clusters and their stable identity |
| `test_query_memory.py` | 16 | Feedback weights, decay, corroboration gating, clamping, reset, inspection, and that learning never touches canonical data or confirms a suggestion |
| `test_determinism.py` | 15 | Identical payloads across repeats, rebuilds, restarts and derived deletion; insertion-order independence; published tie-breaks; stable clusters; reproducible feedback effects that reset undoes exactly |
| `test_layering.py` | 29 | Module boundaries and the dependency classifier; per-thought vs global derived-table contracts; canonical deletion cannot contaminate derived aggregates; the production deletion path; capability-manifest honesty; cross-domain documentation; camelCase public payloads; local-first and no-AI guarantees |
| `test_ai_links.py` | 28 | Explicit AI link checks, pairwise comparisons and vault sweeps; candidate diversity, untrusted output, derived artifacts and the approval workflow |
| `test_providers.py` | 26 | No-AI default, `MockProvider`, Ollama preset, model listing, invalid endpoints/credentials and deterministic provider switching |
| `test_semantic.py` | 47 | Opt-in embedding index, build/status/erase routes, separate semantic section, stale-vector detection and provider failures |
| `test_reasoning.py` | 29 | All six reasoning modes without a provider, similarity ranking, connection discovery, themes, tensions, chronology, citations, canonical immutability, invalid provider responses and the save/approval workflow |
| `test_thinking_partner.py` | 11 | Dedicated nav view, seven human questions mapped to closed modes, suggested prompts, memory selection, clickable citations, explicit uncertainty, follow-up context and no automatic canonical writes |
| `test_canonical_destruction.py` | 3 | **The mandatory destructive verification** (below) |
| `test_migration_v01_to_v02.py` | 7 | Migration from a genuine v0.1-format file (below) |
| `test_web_interface.py` | 14 | Static button/form/route wiring, explicit rendering for every artifact type, and Node execution of the artifact renderer |

### The v0.1 tests are frozen

`tests/test_store.py` was not modified to make v0.2 pass, and neither was any v0.1
production behaviour it pins: `Vault.search()`, `_fuzzy_token_score`, `_snippet`,
`_ensure_search_index` and `_token_variants` are unchanged. Recall adds word forms
only through its own `recall_variants()`, so the two paths cannot drift into
disagreement about what a word form is.

### The destructive test

`test_canonical_destruction.py` is the mission's mandatory verification, and the
sacred contract's only real proof:

1. create canonical memory (thoughts, original text, revisions, tags, relationships
   with both provenances, lifecycle changes, events);
2. build every derived artefact (indexes, trigrams, term statistics, graph cache,
   clusters, query memory);
3. exercise recall and the graph until both are demonstrably populated;
4. **delete all v0.2 derived data**;
5. verify thoughts, ids, original text, revisions, events, relationships and
   provenance survive — compared byte-for-byte against a raw `sqlite3` snapshot, and
   by `canonical_fingerprint()` equality;
6. rebuild from canonical records only;
7. verify recall works again, with the same ranking it had before deletion.

It also covers `remove_demo()` (the production deletion path) and proves that
`reset_recall_learning()` alone leaves indexes and canonical data untouched.

### Migration tests

`test_migration_v01_to_v02.py` builds a **byte-faithful v0.1-format database** with
raw `sqlite3` — three thoughts (one untitled, one edited, one in the Graveyard), four
revisions, one tag, one `AI_SUGGESTED`/`SUGGESTED` relationship, three events — where
every row satisfies v0.1's own invariants, including the one `get_thought` enforces.
It then asserts that opening the file with v0.2 leaves every canonical row identical,
preserves ids and original text, keeps `schema_version = '1'`, adds no recall table
before it should, leaves v0.1 `search()` behaving identically, makes recall work on
migrated data, survives rollback to a file v0.1 can still open, is idempotent, and
still imports a v0.1-shaped JSON export.

That module owns `v01_vault()` and `raw_snapshot()` — the single definition of "a
v0.1-format file" in this repository. `scripts/verify_release_v02.py` imports them
rather than keeping a second copy that could drift.

---

## Gate 2 — v0.1 release workflow

```bash
python scripts/verify_release.py
```

Unchanged from v0.1, still `PASS`: starts empty, creates and edits thoughts, checks
revision counts, Graveyard/resurrection with the timestamp retained, fuzzy recall,
relationship provenance (an `AI_SUGGESTED` edge stays unconfirmed until an explicit
user confirmation), export/import equality, duplicate-import rejection, derived
deletion with canonical preservation, restart persistence, and backup integrity.

---

## Gate 3 — v0.2 release workflow

```bash
python scripts/verify_release_v02.py
```

22 checks in one isolated temporary vault, printing `PASS` or `FAIL` with the evidence
and exiting non-zero on failure:

| Check | Asserts |
| --- | --- |
| `derivedDeletion` / `derivedTablesAfterDeletion` | All nine derived tables empty, `canonicalTablesTouched: []` |
| `canonicalFingerprintPreserved` / `canonicalRowsByteIdentical` | Canonical memory survives total derived deletion |
| `recallAfterRebuild` | Recall works again and produces the **same ranking** as before deletion |
| `determinism` | Byte-identical payloads across three repeats and across a restart; `tieBreak` published |
| `ambiguityRefusal` | A vague query yields `AMBIGUOUS` with the tied count named in the message and `orderingIsTieBreakNotJudgement: true` |
| `honestAbsence` | An absent query yields `NO_MATCH` with zero results and the five honesty statements |
| `provenancePreserved` | `USER_CONFIRMED` and `AI_SUGGESTED` stay distinct; a suggestion is still `SUGGESTED` with `confirmedBy: null` after being searched, expanded and rendered |
| `pathHonesty` | A nonexistent path returns `found: false` with a reason — no invented edge |
| `queryMemory` | Inspectable history and learned queries, resettable, canonical untouched, index stats before/after |
| `graphViewModel` | `entityType` on every node, no persisted layout, no coordinates, canonical/derived fields declared |
| `exportContract` / `importRoundTrip` | `iwtt-vault-export` at **schema version 1**, canonical-only export imports with an identical fingerprint and working recall |
| `migrationFromV01` | The full list in `V0.1_TO_V0.2_MIGRATION.md` §5 |
| `offlineNoAI` | `NoAIProvider` by default, all four config flags false, **zero third-party imports**, honesty statements present |
| `externalFoundation` | Not implemented, not canonical, not persisted, no storage created, closed vocabularies, every deferred edge names a blocker, no canonical schema change |
| `httpSurface` | Sixteen GET routes and three write routes answer, every payload is camelCase, and diagnostics for an unknown id states an explicit "no such thought" rather than pretending |
| `aiLinkSurface` | With no provider every AI route answers **503**. With a provider, a link check, a pairwise comparison and a vault sweep all run — and between them they create **zero** relationships, the analysis is stored as an `AI_LINK_ANALYSIS` derived artifact, and a verdict is marked `AI_SUGGESTED` |

### AI link detection tests

`test_ai_links.py` covers the whole explicit-AI path with a local fake provider, so
no test ever contacts a network:

- **Candidate selection.** The pool is structurally diverse, not lexical-only. The
  key case is asserted directly: a thought that shares *no vocabulary, no tag, no
  cluster and no stored edge* with the source is still offered as a candidate,
  because that is the connection a lexical pool can never surface. The pool is
  bounded at 16, already-connected thoughts are excluded, and every candidate
  explains why it was offered.
- **The provider boundary.** `NoAIProvider` stays the default; hosted endpoints
  require HTTPS; loopback HTTP is allowed; credentials, query strings and
  fragments are refused; and an unusable endpoint in the environment degrades to
  "no AI" instead of raising at startup.
- **Provider output is untrusted.** A verdict naming an id that was not offered is
  dropped, an unknown relationship type falls back to `RELATED_TO`, a positive
  verdict with no reason is dropped, a negative verdict keeps its explanation, and
  a duplicate target is kept once.
- **The analysis is derived.** It is stored as an `AI_LINK_ANALYSIS` artifact, it
  writes a canonical event that records only that the action happened, it is
  erased with the rest of the derived data, and it creates no relationship.
- **The HTTP surfaces.** Link check, pairwise and sweep all answer; the pairwise
  route refuses a self-comparison; a saved proposal stays `SUGGESTED` until a
  human confirms it; and all three routes answer **503** without a provider.

### Cross-memory reasoning tests

`test_reasoning.py` exercises the Thinking Partner engine with no provider, with
the deterministic mock, with a failing provider and with a provider returning
malformed data:

- **Every mode works offline.** Similarity, connections, themes, contradictions,
  evolution and explanation all return derived findings under `NoAIProvider`;
  evolution requires a topic or selected thought, and an ungrounded explanation
  returns no answer rather than an invented one.
- **Ranking and citations.** Similarity excludes the source, orders by descending
  IDF-weighted score and cites each real thought; themes require recurrence
  across separate captures; possible tensions cite both originals; evolution
  preserves originals, revisions and events in timestamp order.
- **Boundaries.** No reasoning mode changes canonical text, creates a
  relationship, confirms a suggestion or writes an artifact. A connection
  finding is `AI_SUGGESTED` / `SUGGESTED`, `requiresApproval: true`, and becomes
  canonical only through the existing save-then-confirm path.
- **Provider independence.** A provider adds only a labelled `providerView`;
  switching back to no provider restores the baseline. A missing provider is
  normal, a thrown error degrades with a warning, and unknown ids, non-boolean
  verdicts and out-of-vocabulary types are filtered or normalised.
- **HTTP and erasure.** The status route publishes modes and boundary, the run
  route validates inputs, the save route requires a real mode and original
  thought, and a saved finding is erased with all other derived data while
  canonical bytes stay identical.

### Thinking Partner view tests

`test_thinking_partner.py` checks the browser surface of the reasoning engine.
It asserts that the dedicated nav item exists alongside every older view, that
the router renders it, and that all seven labels map only to the six closed
modes. It also checks the boundaries that matter in an AI interface:

- suggested prompts and follow-ups are clickable and keep the selected context;
- up to 20 memories can be selected explicitly, and selecting one never edits it;
- every finding has a clickable reference to a real original thought;
- uncertainty, provider-only proposals and "no grounded finding" are visible;
- the view works with no provider, using the deterministic local baseline;
- only explicit save actions call `/api/reasoning/save` or
  `/api/relationships`, always with `AI_SUGGESTED`, and no partner action calls
  a confirmation route.

`test_web_interface.py` adds static guards on the browser interface, because a
dead button is invisible to every other gate: the server does not know a control
was rendered, the unit tests never touch the DOM, and the release verifiers
exercise HTTP routes rather than click handlers. These tests assert that every
rendered `data-action` has a handler, every rendered form has a submit handler,
the AI link actions and routes are wired, the interface references no remote
host, and every nav view is renderable. Two such dead buttons shipped in this
project before these guards existed.

The same module then **executes** the derived-artifact renderer under Node
against real payloads for every artifact type, and asserts that the detail view
produces readable HTML. This caught a second defect: an `AI_LINK_ANALYSIS`
artifact was falling through to `JSON.stringify(payload)` and reaching the
screen as a raw JSON blob. The renderer now handles each type by name, and an
unrecognised future type is rendered as a readable key/value list rather than
machine output. The Node checks skip cleanly when Node is not installed.

---

## Gate 4 — Measured performance

```bash
python scripts/benchmark_recall.py --suite --queries 200
```

Builds synthetic vaults of 1,000 and 10,000 thoughts from a fixed seed, runs 200 mixed
queries at each size, and reports latency percentiles, candidates examined, cluster
and graph-view cost, incremental-sync cost, peak RSS and determinism. It declares
budgets and **fails when one is exceeded**, including the scaling property: candidates
must grow more slowly than the vault, and the examined fraction must shrink.

Current results and the full method are in `BENCHMARKS.md`. Nothing in that document
is estimated, and Graphify's vendor benchmark numbers are quoted only as their claims.

---

## Manual UI smoke test

1. Start `python -m iwtt.server` and open the local URL (default
   `http://127.0.0.1:8000`, loopback only).
2. Capture a thought; refresh and confirm it remains.
3. Edit it and change its type; inspect original text and saved snapshots side by side.
4. Add two thoughts; create a confirmed link plus an unconfirmed proposal; confirm the
   proposal only when ready and watch the label change while provenance stays
   `AI_SUGGESTED`.
5. Move a thought to the Graveyard and resurrect it.
6. **FIND MY THOUGHT:** search a paraphrased fragment, then a misspelling, then a
   single vague word. Confirm that the original text is visually distinct from the
   explanation, that each result says *why* it matched, and that the vague word returns
   several candidates with an explicit refusal to pick one.
7. Open the diagnostics/breakdown view for one result and confirm the per-term tiers
   and contributions match the score shown.
8. Search something that is definitely absent and confirm IWTT says so rather than
   guessing.
9. Mark a result *Not this one*, re-run the query and confirm it moved down but is
   **still visible**; then reset learning and confirm the order returns.
10. Open Connection Explorer: inspect a neighbourhood, ask for a path between two
    unconnected thoughts and read the honest failure, then look at the derived clusters
    and confirm they are labelled as automatically discovered.
11. Use a time filter (`last week`, `past 3 months`, `recently`) and confirm an
    inferred window is labelled as estimated.
12. Export JSON and Markdown; import JSON into a fresh data directory.
13. Create/download a backup; delete all derived data; confirm canonical counts remain
    and that recall rebuilds itself on the next query.
14. Restart the server and revisit the vault.

`scripts/build_sample_vault.py` builds a 28-thought demonstration vault with the
relationships, tags, Graveyard item and revisions needed to exercise all of the above,
and prints the queries that demonstrate each behaviour.

---

## Scope of verification

The suite proves local persistence, data-layer invariants, the canonical/derived
boundary, determinism, and bounded performance at 1,000 and 10,000 thoughts.

It is **not** a browser automation suite, a multi-user security audit, an accessibility
certification, an encryption test, or a benchmark at 100,000+ thoughts. There is no
test of embedding quality because there are no embeddings. There is no test of AI
behaviour in the recall path because recall makes no AI call.
