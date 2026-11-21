# RECALL ENGINE (IWTT v0.2)

How IWTT gets you from a fragment — a vague phrase, an approximate idea, a
half-remembered wording, a related thought, a lifecycle state, a connection, a
time — back to **the exact thought you wrote**.

Everything described here is implemented in `iwtt/recall/`, is deterministic, and
runs offline against a local SQLite file. The scored ranking uses no embedding model,
no vector index, no AI call and no network request. v0.3 adds an **opt-in semantic
stage** whose output is deliberately kept outside that ranking (section 12 below).
Where a technique was adapted from the Graphify research dossier, this document says
so; the full accounting is in `GRAPHIFY_ADAPTATION_NOTES.md`.

---

## 1. The promise, and its limits

**Promise.** If the words you type share *any* lexical evidence with a thought you
wrote — the same word, a different form of it, an alias, a prefix, a fragment, a
misspelling, a title, a tag — recall will surface that thought, rank it
deterministically, and tell you why.

**Limits, stated plainly.**

* Recall is **lexical + fuzzy + graph**. It does not understand meaning. A query
  with no word in common with any thought returns `NO_MATCH`, not a guess.
* Relationship edges can *enrich* a candidate set; they can never *replace*
  lexical evidence. A thought you never wrote words matching cannot be surfaced as
  a strong match by association alone (`graph_may_surface_without_lexical_evidence`
  is `False`, and `require_lexical_evidence` is `True`).
* Scores are **deterministic weighted sums, not probabilities**. A score of 13.75
  out of a possible 21.25 is not "65% confident".
* Nothing here writes to canonical data. Recall reads `thoughts`, `revisions`,
  `tags`, `relationships` and `events`; it writes only to `recall_*` tables, all of
  which are derived and all of which can be deleted without losing a word you wrote
  (see `V0.1_TO_V0.2_MIGRATION.md` §6 and `tests/test_canonical_destruction.py`).

---

## 2. Pipeline

```
query text
  │
  ├─ 1. analysis        strip memory filler → normalize → tokenize → per-term
  │                     variants (morphology + aliases) → trigrams → time hints
  │                     → lifecycle hints                          analysis.py
  ├─ 2. candidates      lexical pool (SQL, capped 600) ∪ trigram pool
  │                     (SQL, capped 400, never-worse guard)         index.py
  ├─ 3. canonical load  the requested ids only, in chunks of 500    scoring.py
  ├─ 4. filters         type / status / tag / time / demo — every
  │                     exclusion is reported, never silent         filters.py
  ├─ 5. base scoring    per-term tiers, IDF, coverage, title, tag,
  │                     trigram cosine                              scoring.py
  ├─ 6. graph enrich    deterministic seeds → bounded BFS/DFS
  │                     expansion with hub suppression           expansion.py
  ├─ 7. fuzzy pass      difflib, only on the top 120 candidates     scoring.py
  ├─ 8. final scoring   + temporal, lifecycle, query memory,
  │                     graph contribution; explanations built      scoring.py
  ├─ 9. ranking         total order, explicit tie-break             results.py
  └─ 10. state          NO_MATCH / AMBIGUOUS / STRONG / POSSIBLE    scoring.py
```

Each stage reports its own timing in `timingsMs`, and the outcome carries an
`honesty` block describing what was and was not used.

---

## 3. Signals and weights

`RecallConfig` (`iwtt/recall/config.py`) is the single source of truth. The default
weights:

| Signal | Weight | What it measures |
| --- | --- | --- |
| `exact_phrase` | 6.0 | Your phrase appears verbatim in the text or title |
| `idf_weighted` | 3.0 | Rare-term matches, weighted by `log(1 + N/(1+df))` |
| `coverage` | 2.0 | `(matched_terms / query_terms) ** 1.5` |
| `trigram` | 1.5 | Character-trigram cosine — spelling similarity |
| `fuzzy` | 1.5 | `difflib` ratio against a length-matched window |
| `token_overlap` | 0.75 | Plain shared-token count |
| `title` | 1.0 | Match in the title (stronger than body) |
| `tag` | 1.0 | Match against a tag |
| `graph` | 1.0 | Contribution from relationship expansion, capped at 2.0 |
| `temporal` | 0.5 | Created inside/near a requested time window |
| `lifecycle` | 0.5 | Matches a requested lifecycle state |
| `query_memory` | 1.5 | Signed, clamped to `[-0.75, +1.0]` (see `QUERY_MEMORY.md`) |

**Theoretical maximum: 21.25**, computed by `config.theoretical_max_score()` and
shown to the user in the strong-match message, so the number on screen is always
relative to a published ceiling rather than floating free.

Field weights inside the index: **title 3.0, tag 2.5, body 1.0**
(`index.FIELD_WEIGHTS`).

### 3.1 Per-term lexical tiers

Only the **strongest** tier per term counts, so one term can never double-count.
Values live in `scoring.TIER_VALUES` and are deliberately not configurable — a knob
that nothing reads is a lie told to the reader:

| Tier | Value | Meaning |
| --- | --- | --- |
| `EXACT` | 1.00 | The token itself |
| `VARIANT` | 0.90 | A morphological form or v0.1 alias (`adaptations` → `adapt`) |
| `PREFIX` | 0.86 | One is a prefix of the other, both ≥ 4 chars |
| `SUBSTRING` | 0.70 | One contains the other, both ≥ 4 chars |
| `TYPO` | 0.55 × trigram similarity | Trigram overlap ≥ 0.55 with a length-similar token |
| `DIFFLIB` | `min(ratio × 0.84, 0.82)` | Only for candidates that earned the fuzzy pass |
| `NONE` | 0.0 | No lexical evidence |

Bounded comparisons: at most `MAX_FUZZY_TOKEN_COMPARISONS = 32` per term, only
against tokens within `FUZZY_LENGTH_TOLERANCE = ±2` characters.

### 3.2 Morphology

`recall_variants(token)` → `morphology_forms(token, rounds=2)` in `iwtt/recall/text.py`.
This is a **fixed list of string rules applied at most twice**, never a language
model:

* v0.1's `_token_variants` (aliases + inflections) is reused unchanged, so the
  frozen v0.1 search path and recall cannot disagree about a word form;
* iteration — each round's output feeds the next, so `adaptations` → `adaptation` →
  `adapt`;
* derivational suffixes `-ation`, `-tion`, `-ness`, `-ment`, with a silent-`e`
  restoration that only fires where English actually uses it (a stem ending
  vowel+consonant: `creat` → `create`). It deliberately does **not** invent
  non-words like `adapt` → `adapte`.

The index side and the query side call the same function, which is why a thought
that only ever says "adaptation" is findable by a query for "adapt".

---

## 4. Candidate generation (the bounded part)

Recall never scans the corpus. Two pools, both absolute caps:

* **Lexical pool** — one SQL aggregation joining the query's term forms against
  `recall_tokens`, ordered by IDF-weighted score, capped at `token_candidate_cap = 600`.
* **Trigram pool** — one SQL aggregation joining the query's trigram vector against
  `recall_trigrams`, capped at `trigram_candidate_cap = 400`.

The union is the candidate set. Both joins are written
`FROM <small query table> CROSS JOIN <index table>`, which forbids SQLite from
reordering them. With a plain `JOIN` the planner scans the entire index table
(measured: 71,872 trigram rows at 1,000 thoughts, 717,587 at 10,000) on every
query; with `CROSS JOIN` it walks the ~40 query rows and probes the primary key.
Same rows, same order, cost proportional to matches instead of to the corpus.

**Never-worse guard.** If the rarest query trigram still covers more than
`trigram_guard_fraction = 0.10` of the vault, the trigram index is not selective and
is *not* used to narrow anything: the lexical set runs unfiltered and
`honesty.candidateGeneration.trigramGuardTripped` says so. A prefilter may only
speed things up, never change results.

**Ubiquitous-trigram cap.** A trigram that occurs in more documents than
`max(trigram_postings_floor=1000, trigram_postings_fraction=0.02 × documents)`
cannot discriminate between memories, yet reading its postings list is the most
expensive part of candidate generation at scale. Such trigrams are dropped from the
*join only*, and named in `honesty.candidateGeneration.trigramsSkippedTooCommon`.
The floor is calibrated so a 1,000-thought vault keeps every trigram it has always
kept (measured: largest postings list 938 < 1000, so **zero** trigrams are skipped
and results are unchanged); at 10,000 thoughts it removes the fragments that appear
everywhere.

Measured effect (`scripts/benchmark_recall.py --suite`): the vault grew 10× while the
examined candidate set grew 1.31× (735 → 966), i.e. **sublinear**.

---

## 5. Ranking and determinism

The total order is

```
score DESC → trigram DESC → fuzzy DESC → created_at ASC → thought_id ASC
```

published per result as `tieBreak` (a 5-tuple) and in the payload as
`tieBreak.order`. **Database row order never decides ranking.** Two thoughts with
identical scores are ordered by the older one first and then by id, and the outcome
states `orderingIsTieBreakNotJudgement: true` — the order is a tie-break, not a
verdict.

`tests/test_determinism.py` (15 tests) proves: byte-identical payloads across
repeats, across a full rebuild, across a restart, and across
delete-all-derived-then-rebuild; identical ordering when thoughts are inserted in
reverse order; unchanged relative order when unrelated thoughts are added; stable
cluster identity; and reproducible feedback effects that `reset_recall_learning()`
undoes exactly.

---

## 6. Outcome states

| State | When |
| --- | --- |
| `NO_MATCH` | Nothing carries lexical evidence above `min_score = 0.35` |
| `AMBIGUOUS` | ≥ `ambiguity_min_candidates = 3` results within `ambiguity_ratio = 0.90` of the top score, **or** ≥ `ambiguity_exact_tie_min = 2` exactly tied top scores |
| `STRONG_MATCH` | Top score ≥ `strong_min_score = 6.0` **and** it has lexical evidence |
| `POSSIBLE_MATCH` | Anything else worth showing |

### 6.1 Ambiguity refusal

IWTT would rather say *"I found three likely memories"* than deliver a confident
wrong answer. An `AMBIGUOUS` outcome reports `tiedCount`, `bandSize`, `topScore`,
`secondScore`, `ratio`, the ids in the band, and — per candidate — `differences`:
the concrete facts that distinguish them (type, lifecycle, date, distinctive tags).
It also states `refusal: "IWTT declines to declare one authoritative answer."`

A `STRONG_MATCH` is never awarded on graph association alone. If a candidate's only
evidence is a relationship hop, it is suppressed from the strong band and reported
in `honesty.suppressedNoLexicalEvidence` with a count, and the explanation
`NO_LEXICAL_EVIDENCE` ("No word in common — similarity suppressed") is attached.

### 6.2 Honest absence

A `NO_MATCH` message says what was searched and what the result does *not* prove:

> Nothing in the vault contains those words. IWTT searched the full text, titles and
> tags of every indexed thought (4) for exact, prefix, variant, trigram and fuzzy
> candidates and found none. That is a statement about these words, not proof the
> thought is gone — try a different fragment, a related word, or a time/lifecycle
> filter.

If filters excluded candidates, the message says so and `filters.excluded` lists the
counts by reason (`type`, `status`, `tag`, `time`, `demo`). **A filter may hide a
candidate; it may never hide the fact that something was excluded.** A Graveyard hint
annotates rather than silently excluding.

---

## 7. Time and lifecycle recall

`parse_time_expression()` (`iwtt/recall/text.py`) understands:

`today`, `yesterday`, `this week`, `last week`, `this month`, `last month`,
`this year`, `last year`, `all`/`any`/empty, `last|past|previous N
days|weeks|months|years` (bounded at 3660 days), `recently`, `lately` (30 days),
`before X`, `after X`, `between X and Y`, `in YEAR`, and a bare date.

Every window carries a **`precision`** field: `EXPLICIT` when you named a date or a
range, `ESTIMATED_RANGE` when IWTT inferred one (`recently` → last 30 days,
`this year` → January 1st onward). Estimated windows publish `estimated: true` and a
`note` explaining the assumption. **IWTT never fabricates precision it does not
have.**

SQL compares only the `YYYY-MM-DD` prefix (so an index on `created_at` is usable);
exact containment is checked in Python. Time filters compare against `created_at` by
default and `updated_at` when `time_field=updated`.

Lifecycle: `statuses=ACTIVE|GRAVEYARD|ALL`, `types=THOUGHT|EXPERIMENT|INVENTION|PROJECT`.
Graveyard results carry `graveyardAt` / `resurrectedAt` and the explanations
`GRAVEYARD` / `RESURRECTED`.

---

## 8. Explanations

Every result carries `explanations[]` — `{code, label, detail, contribution,
evidence}` — using 29 codes with fixed human labels (`results.EXPLANATION_LABELS`),
including `EXACT_PHRASE`, `RARE_TERM`, `QUERY_COVERAGE`, `TYPO_TOLERATED`,
`ALIAS_MATCH`, `MORPHOLOGY_MATCH`, `RELATED_CONFIRMED`, `RELATED_SUGGESTED`,
`MULTI_HOP`, `PREVIOUSLY_USEFUL`, `PREVIOUSLY_NOT_USEFUL`, `TIME_WINDOW`,
`TIME_HINT`, `LIFECYCLE_MATCH`, `GRAVEYARD`, `ORIGINAL_WORDING`,
`CURRENT_WORDING`, `IN_CLUSTER`, `NO_LEXICAL_EVIDENCE`.

Two rules the UI depends on:

1. `RELATED_CONFIRMED` and `RELATED_SUGGESTED` are **different labels**. A suggested
   edge is never presented as a confirmed one, and using search never confirms it.
2. The **original text** is returned separately, under
   `whatIActuallyWrote.originalText` / `.currentText`, and the UI renders it visually
   apart from the derived explanation. What you wrote is not an annotation.

`matchReason` is the short sentence; `why()` is the longer narrative; the full
per-term breakdown (`perTerm`, tiers, matched forms, contributions) comes from
`GET /api/recall/diagnostics` and is presented in a secondary details view, because
recall is a product feature, not a developer dashboard.

---

## 9. The `honesty` block

Every outcome ships these statements verbatim, so no caller has to guess what ran:

| Key | Statement |
| --- | --- |
| `embeddings` | "NOT IMPLEMENTED. IWTT v0.2 contains no embedding model, no vector index and no semantic stage. Retrieval is lexical + fuzzy + graph, and says so." |
| `semanticUnderstanding` | "None claimed. Matches come from tokens, morphology/aliases, character trigrams, bounded difflib similarity and stored relationships." |
| `ai` | "Not used. Recall ran entirely offline against derived indexes." |
| `network` | "None. No request left this process." |
| `probabilisticConfidence` | "Scores are deterministic weighted sums, not probabilities. Labels are assigned by fixed thresholds documented in RECALL_ENGINE.md." |

Plus machine-readable fields: `mode`, `filteredOut`, `candidateGeneration` (pool
sizes, caps, guard state, skipped trigrams), `suppressedNoLexicalEvidence` /
`suppressedCount`, `graphReached` / `graphReachedBelowThreshold` /
`graphReachedReturnedCount`, `indexRebuilt`.

---

## 10. Derived storage

Nine tables, all `recall_*`, all derived, all rebuildable, none containing canonical
truth. Six are per-thought and carry `REFERENCES thoughts(id) ON DELETE CASCADE`;
three are corpus-wide and deliberately have no `thought_id` foreign key, because a
document frequency belongs to the corpus, not to one thought:

| Table | Scope | Contents |
| --- | --- | --- |
| `recall_tokens` | per thought | `(thought_id, token, field ∈ title/body/tag, tf)` |
| `recall_trigrams` | per thought | `(trigram, thought_id, tf)` |
| `recall_docs` | per thought | type, status, timestamps, token/trigram counts, degree |
| `recall_graph_cache` | per thought | degree, in/out degree, hub factor |
| `recall_clusters` | per thought | cluster id, size, label, algorithm, authority, fingerprint |
| `recall_query_memory` | per thought | feedback events (see `QUERY_MEMORY.md`) |
| `recall_term_stats` | global | `term → df`, recomputed from `recall_tokens` |
| `recall_meta` | global | schema version, signatures, build stamps |
| `recall_build_log` | global | build/sync/reconcile history |

`recall_docs` stores **counts and timestamps only** — never verbatim thought text.

**Self-healing.** `RecallIndex.ensure_fresh()` compares a six-aggregate
`canonical_signature` (thought count, max `updated_at`, live relationship count, max
relationship `updated_at`, tag links, tag count) against `recall_meta.sync_signature`
and syncs when they differ. Because `ON DELETE CASCADE` can remove per-thought rows
*before* the sync sees the deletion, `ensure_fresh()` also **reconciles the global
aggregates** when the signature moved but no per-thought row is stale: without that,
a deleted thought would leave its document frequencies behind forever and quietly
skew IDF. `tests/test_layering.py::test_deleting_a_thought_cannot_leave_stale_corpus_statistics`
is the regression test.

`RECALL_INDEX_SCHEMA = 3`; a schema mismatch triggers a full rebuild, never a
partial one.

---

## 11. HTTP surface

| Method | Path | Purpose |
| --- | --- | --- |
| GET/POST | `/api/recall` | Ranked candidates with explanations |
| GET/POST | `/api/recall/find` | FIND MY THOUGHT payload (headline, primary action, provenance) |
| GET | `/api/recall/config` | Published configuration |
| GET | `/api/recall/index` | Index state, drift, counts |
| GET | `/api/recall/diagnostics` | Full scoring breakdown (`q`, optional `thought_id`) |
| GET | `/api/recall/learning` | Query-memory stats and learned queries |
| GET | `/api/recall/learning/history` | Per-event history |
| POST | `/api/recall/feedback` | Record `USEFUL` / `NOT_USEFUL` / `OPENED` / `CORRECTION` (201) |
| DELETE | `/api/recall/learning` | Reset learning |
| POST | `/api/recall/index/rebuild` | Rebuild derived indexes |
| GET | `/api/clusters` | Automatically discovered groupings |
| GET | `/api/graph/view` | Bounded `GraphViewModel` |
| GET | `/api/graph/path` | Path between two thoughts, or an honest failure |
| GET | `/api/graph/neighbourhood/<id>` | Bounded neighbourhood |
| GET | `/api/graph/lineage/<id>` | Directional lineage |
| GET | `/api/external/foundation` | Interfaces and documentation only |

All v0.1 routes are unchanged. All payloads are camelCase; the only snake_case map
keys anywhere are SCREAMING_SNAKE vocabulary constants (`THOUGHT_ENTITY`,
`USER_CONFIRMED`), which are domain values, not field names.

Query parameters on `/api/recall`: `q`, `limit`, `include_graveyard`, `types`,
`tags`, `statuses`, `tag_mode`, `time`, `time_field`, `include_demo`, `diagnostics`.

---

## 12. Measured performance

Real numbers from `scripts/benchmark_recall.py --suite --queries 200` on this
machine (single core, SQLite, stdlib only, synthetic corpus with a deliberately
small vocabulary — the worst case for lexical selectivity). Reproduce with the
script; details and method in `BENCHMARKS.md`.

| | 1,000 thoughts | 10,000 thoughts |
| --- | --- | --- |
| Index build | 0.56 s | 10.10 s |
| Recall p50 / p95 / max | 72 / 118 / 187 ms | 205 / 404 / 444 ms |
| Candidates examined (max) | 735 (73.5% of vault) | 966 (9.7% of vault) |
| Adding 50 thoughts + 1 recall | 0.24 s | 0.98 s |
| Cluster rebuild | 0.014 s | 0.83 s |
| Peak RSS | 88.8 MB | 279.3 MB |
| Ranking mismatches on repeat | 0 / 50 | 0 / 50 |

The candidate set is bounded by an **absolute** cap, so the fraction of the vault it
represents *falls* as the vault grows. Nothing holds the graph in memory: the graph
cache is a SQLite table, and `GraphViewModel` is an induced subgraph bounded by
`max_nodes`.

---

## 12. The opt-in semantic stage (v0.3)

**It never changes the ranking above.** The published 12-signal formula, its
weights, its lexical-evidence gate and its ambiguity refusal are unchanged. The
stage attaches a separate `semantic` object to the outcome, rendered under Find
My Thought as **“Same meaning, different words”**. Its entries are cosine
similarities between the embedded query and vectors explicitly built from this
vault; they cannot enter `results`, cannot raise a score, and are shown with a
percentage that is a ranked hint, not a probability.

* **Off by default.** `DEFAULT_CONFIG.embeddings_enabled` and
  `semantic_stage_enabled` remain `False`. With no `recall_embeddings` rows,
  recall is byte-identical to v0.2 and the honesty block says embeddings are OFF.
* **Built explicitly.** Data & privacy configures a provider (Ollama locally at
  `http://127.0.0.1:11434/v1`, an OpenAI-compatible endpoint, or the offline
  mock). `POST /api/recall/embeddings/build` embeds up to a bounded batch of
  thoughts per call into the derived table; the UI repeats the call until the
  index is current. With Ollama, an embedding model such as
  `ollama pull nomic-embed-text` must be present — installed chat models are not
  embedding models.
* **Only the query goes over the wire at recall time.** A semantic recall makes
  exactly one request: the query text to the configured provider's embeddings
  endpoint. No thought text is sent during search. Builds send the indexed text
  of the thoughts being embedded, and only when explicitly requested.
* **Deterministic.** Vectors are stored as float32 in thought-id order; cosine
  is accumulated in dimension order; neighbours sort by `(-similarity,
  thought_id)`. Repeating a query returns the identical list. Rows already in
  the scored results are omitted from the semantic section so the two lists
  never overlap.
* **Erasable.** `DELETE /api/recall/embeddings` and “Delete all derived data”
  remove the table's rows; no canonical data changes. Editing a thought marks
  its vector stale; switching models marks every vector stale.
* **References, never invented memories.** Every neighbour is a `thought_id`
  from the vault; titles and previews are read live from the canonical entry at
  answer time. A provider that fails to embed degrades the stage to “off” with
  a reason — it never produces a result.

The scored path's defaults remain `ai_required = False`,
`network_required = False` and `embeddings_enabled = False`; when the stage runs,
the honesty block is replaced per query with a truthful account of the model and
the single request made.

## 13. Cross-memory reasoning (v0.3)

`iwtt/recall/reasoning.py` is the analytical engine behind the Thinking Partner.
It answers six explicit modes: `similar`, `connections`, `themes`,
`contradictions`, `evolution` and `explain`. It reads canonical thoughts,
revisions and events plus the rebuildable recall indexes, and returns
**proposals** labelled `DERIVED_REASONING`. It is not a new search ranker and
does not sit inside Find My Thought's scored path.

* **A deterministic baseline always works.** With no provider, no key and no
  network, similarity uses IDF-weighted shared terms over title/body/tags,
  connection discovery uses the existing structural candidate pool, themes are
  recurring terms across separate captures, possible tensions are opposing cues
  with shared terms, evolution is a timestamped chronology of originals,
  revisions and events, and explanations cite selected originals. If nothing
  grounds an answer, it says so instead of inventing one.
* **A provider is an optional second view.** When a provider is configured its
  assessment is validated strictly: an id that was not supplied is dropped, a
  non-boolean verdict is dropped, an unknown relationship type falls back to
  `RELATED_TO`, and a failed or malformed response degrades to the deterministic
  baseline with a warning. Provider text is labelled as a proposal and never
  replaces the baseline findings.
* **Every finding points home.** Each result carries `citedThoughtIds`; each
  finding embeds a `citation` with the real `thoughtId`, title and a bounded
  excerpt read live from the canonical entry at answer time. No generated text
  becomes a memory, title, revision or relationship.
* **Nothing is saved automatically.** Findings are ephemeral. Only
  `POST /api/reasoning/save` writes one, and it writes to the existing
  `derived_artifacts` table with a type from `REASONING_ARTIFACT_TYPES`. The
  accompanying event records only that the action happened. All of it is erased
  by “Delete all derived data” without touching a canonical byte.
* **Connections still require a person.** A discovery finding is
  `AI_SUGGESTED` / `SUGGESTED`, has `requiresApproval: true`, and writes no
  relationship. The existing `POST /api/relationships` route saves it as an
  unconfirmed proposal; `confirm_relationship()` is the only path to
  `CONFIRMED` / `confirmedBy: USER`.
* **The semantic neighbour view stays separate.** `similar` may attach a
  `semanticNeighbours` section from the opt-in embedding index when one exists;
  it cannot change the lexical `findings` order or scores. With no index or no
  provider the section is absent.
* **HTTP surface.** `GET /api/reasoning` publishes the closed mode list,
  artifact types and boundary; `POST /api/reasoning` runs one mode with bounded
  inputs; `POST /api/reasoning/save` persists a user-chosen finding. There is no
  autonomous or scheduled reasoning.
* **Dedicated view.** `web/index.html` adds a Thinking Partner nav item beside
  Find My Thought. `web/app.js` turns the six modes into seven questions — Ask
  My Vault, Explore Ideas, Find Connections, Compare Thoughts, Identify
  Patterns, Track Thinking Evolution and Review & Reflect — with suggested
  prompts, up to 20 selected memories, clickable citations, follow-up context
  and visible uncertainty. Saving a finding or a connection proposal is a
  separate button; nothing is auto-edited, auto-deleted or auto-confirmed.

## 14. What is not implemented

* Semantic **reranking** of the scored list, cross-encoder scoring, or any use
  of embeddings in the weighted formula — the stage is supplementary only.
* Automatic index builds. A provider must be configured and a build requested.
* Query expansion from external knowledge (see
  `EXTERNAL_KNOWLEDGE_ARCHITECTURE.md`).
* Learned-to-rank models. Query-outcome memory is a documented, decaying,
  resettable arithmetic adjustment — not training.
* Persisted graph layout, coordinates, or any whole-vault in-memory graph.
* Autonomous reasoning, automatic artifact creation, automatic relationship
  creation or confirmation, and any generated text treated as an original
  capture. A person must request each finding and approve each canonical change.
