# IWTT Checkpoint 001 — Baseline Audit

**Recorded before any v0.2 production code was modified.**

| Field | Verified value |
|---|---|
| Authoritative repository | `https://github.com/illusionist-dotcom/IWTT` |
| Branch | `main` |
| Checkpoint commit | `4124dc24063380113157853b6f1879833cb3f25c` |
| Commit message | `checkpoint: IWTT v0.1 foundation and Graphify research` |
| `git rev-parse HEAD` at audit time | `4124dc24063380113157853b6f1879833cb3f25c` — **equals the checkpoint** |
| Working tree at audit time | clean (`git status --porcelain` → 0 lines) |
| Commits in history | 1 (the checkpoint). History was **not** rewritten. |
| Files tracked (excluding `.git`) | 43 |
| Runtime dependencies | **none** — `requirements.txt` documents stdlib-only |
| Host | Linux, Python 3.13.16, SQLite 3.46.1 (FTS5, math functions, session available; **unused** by v0.1) |

## 1. Baseline verification results

### 1.1 `python -m unittest discover -s tests -v`

```
Ran 15 tests in 0.115s

OK
```

**15 / 15 passed. No failures, no errors, no skips.**

| # | Test | Result |
|---|---|---|
| 1 | `test_thought_creation_generates_durable_uuid_revision_and_event` | ok |
| 2 | `test_original_capture_is_database_protected_from_overwrite` | ok |
| 3 | `test_edit_preserves_revision_snapshots_and_original` | ok |
| 4 | `test_type_promotion_is_flexible_and_preserves_history` | ok |
| 5 | `test_graveyard_and_resurrection_preserve_timestamps_and_events` | ok |
| 6 | `test_relationship_provenance_requires_explicit_confirmation` | ok |
| 7 | `test_tags_and_event_creation` | ok |
| 8 | `test_exact_fuzzy_keyword_and_tag_search_lead_to_original` | ok |
| 9 | `test_json_export_import_round_trip_preserves_canonical_records` | ok |
| 10 | `test_duplicate_import_fails_atomically_without_overwriting` | ok |
| 11 | `test_malformed_relationship_reference_is_rejected_without_partial_import` | ok |
| 12 | `test_sqlite_backup_is_a_real_consistent_file` | ok |
| 13 | `test_delete_all_derived_data_keeps_every_canonical_record` | ok |
| 14 | `test_demo_records_are_explicit_and_removable` | ok |
| 15 | `test_no_ai_fallback_is_explicit_and_available_core_remains` | ok |

### 1.2 `python scripts/verify_release.py`

```json
{
  "result": "PASS",
  "startedEmpty": true,
  "thoughtsCreated": 3,
  "editAndTypeRevisions": 5,
  "relationshipProvenance": "AI_SUGGESTED remained unconfirmed until explicit USER confirmation",
  "graveyardResurrection": "PASS; graveyardAt retained",
  "fuzzyRecall": "Keyword + fuzzy",
  "canonicalImportEqualsExport": true,
  "duplicateImportRejected": true,
  "derivedDeletionCanonicalPreserved": true,
  "restartPersistence": true,
  "backupIntegrity": "ok",
  "importCounts": {
    "thoughts": 3, "revisions": 7, "tags": 1,
    "thoughtTags": 1, "relationships": 2, "events": 14
  }
}
```

**Baseline verdict: GREEN.** No pre-existing failure was found, so every later
failure is attributable to v0.2 work.

## 2. Actual v0.1 architecture (audited, not assumed)

```
Browser (web/index.html, web/app.js, web/style.css)   ← same-origin, no CDN
    │  JSON / download requests
    ▼
iwtt/server.py   BaseHTTPRequestHandler + ThreadingHTTPServer
    │            default bind 127.0.0.1:8000, --host/--port/--data-dir
    ├── iwtt/ai.py    NoAIProvider default; OpenAICompatibleProvider only when
    │                 IWTT_AI_BASE_URL is explicitly set
    ▼
iwtt/store.py    class Vault — one SQLite file, short-lived connection per
                 operation, BEGIN IMMEDIATE writes
    ├── CANONICAL: thoughts, revisions, tags, thought_tags, relationships, events
    └── DERIVED:   search_documents, derived_artifacts
    ▼
~/.iwtt/thought-vault.sqlite3   (WAL; IWTT_DATA_DIR / --data-dir override)
```

### 2.1 Canonical schema (v0.1, `schema_info.schema_version = '1'`)

| Table | Notes audited from `SCHEMA_SQL` |
|---|---|
| `schema_info` | `key/value`; holds `schema_version` |
| `thoughts` | `id` UUID PK, `original_text` (NOT NULL, non-blank CHECK), `current_text`, `title`, `type` ∈ {THOUGHT, EXPERIMENT, INVENTION, PROJECT}, `status` ∈ {ACTIVE, GRAVEYARD}, `created_at`, `updated_at`, `graveyard_at`, `resurrected_at`, `is_demo` |
| `revisions` | append-only snapshots; `UNIQUE(thought_id, revision_number)`; `change_reason` ∈ CAPTURED / EDITED / TYPE_CHANGED |
| `tags` | `normalized_name` UNIQUE |
| `thought_tags` | PK `(thought_id, tag_id)` |
| `relationships` | `source_id`, `target_id`, `type` (11-value CHECK), `provenance` ∈ {USER_CONFIRMED, AI_SUGGESTED}, `status` ∈ {SUGGESTED, CONFIRMED, REMOVED}, `confirmed_at`, `confirmed_by`, `metadata_json`, `CHECK(source_id <> target_id)` |
| `events` | `target_type` ∈ {THOUGHT, RELATIONSHIP, VAULT}, `event_type`, `timestamp`, `metadata_json` |

**Immutability is enforced in the database, not in Python:**

```sql
CREATE TRIGGER preserve_original_thought_text
BEFORE UPDATE OF original_text ON thoughts
WHEN NEW.original_text <> OLD.original_text
BEGIN
    SELECT RAISE(ABORT, 'original_text is immutable');
END;
```

### 2.2 Derived tables in v0.1

| Table | Content |
|---|---|
| `search_documents` | `thought_id` PK (FK cascade), `content` = `original_text \n current_text \n title \n tag names`, `indexed_at` |
| `derived_artifacts` | AI summaries etc.; `provider`, `model`, `generated_at`, `source_thought_ids_json` |

Both are cleared by `Vault.clear_derived_data()`, which returns
`{"derivedArtifactsDeleted": n, "searchDocumentsDeleted": m}`.
`Vault._ensure_search_index()` self-heals the projection when rows are missing or
orphaned, which is why search still works after a derived-data wipe.

### 2.3 v0.1 search (the behaviour v0.2 must not regress)

`Vault.search(query, include_graveyard=True, limit=100)`:

1. `normalize_text` = NFKD casefold, drop combining marks, `re.findall(r"[^\W_]+")`
   → **underscore is already a separator** in v0.1.
2. `tokenize` drops `STOPWORDS`, falling back to unfiltered tokens when the query
   is entirely stopwords.
3. `_token_variants` = tiny deterministic morphology (`ies→y`, `ing`, `ed`, `s`,
   double-consonant collapse, `+e`) **plus** a transparent `TOKEN_ALIASES` map
   (`adapt→adjust/change/learn/evolve`, `enemy→enemies/opponent/foe`,
   `fight→combat/battle/fighting`, `remember→memory/recall/learn`,
   `strategy→tactic/tactics/approach/behavior`).
4. `_fuzzy_token_score` tiers: exact `1.0`, variant `0.90`, prefix `0.86`,
   `difflib.SequenceMatcher` ratio ≥ 0.70 → `min(ratio*0.84, 0.82)`.
5. Scoring: `exact_text → 100.0`; otherwise
   `coverage*72 + exact_count*4 + fuzzy_count*2 (+12 title, +9 tag)`, gated by
   `minimum_recall` (`0.65` for a one-token query, else `1.25`).
6. Ordering: `sort(key=(score, updatedAt), reverse=True)`.
   → **Ties are broken by `updatedAt`, then by whatever order the rows arrive
   in.** This is the ranking-determinism weakness v0.2 is asked to fix.
7. `snippet` is taken from **`original_text`** — the result leads the user back to
   what they actually wrote.
8. Every query loads **all** joined rows into Python (`fetchall()`), so cost is
   O(vault) per query regardless of selectivity.

`matchReason` is a closed vocabulary: `Exact text`, `Title match`, `Tag match`,
`Fuzzy recall`, `Keyword + fuzzy`, `Keyword match`.

### 2.4 Contracts pinned by the v0.1 test suite (v0.2 regression freeze)

These are literal assertions in `tests/test_store.py` and
`scripts/verify_release.py`. v0.2 must keep all of them true:

| Pinned behaviour | Where |
|---|---|
| `search("game enemies remember how the player fights")[0]["matchReason"] == "Exact text"` | test 8 |
| `search("enemy adapting to player")` returns **exactly one** result, `matchReason` contains `fuzzy`, `snippet == originalText` | test 8 |
| `search("adaptive gameplay")[0]["matchReason"] == "Tag match"` | test 8 |
| `clear_derived_data()["derivedArtifactsDeleted"] == 1` and `["searchDocumentsDeleted"] == 2` | test 13 |
| after a derived wipe, `search("revised old idea")[0]` is the revised thought and `searchDocuments` is back to 2 | test 13 |
| `export_vault()` has **no** `derived` key by default; thoughts carry no `displayTitle` | test 9 |
| `destination.export_vault()["canonical"] == export["canonical"]` after import | test 9 |
| duplicate-ID import raises `ConflictError` atomically | test 10 |
| malformed relationship reference raises `ValidationError` with **no** partial import | test 11 |
| `seed_demo()` yields exactly 7 demo thoughts; `remove_demo()` removes exactly 7 | test 14 |
| direct `UPDATE thoughts SET original_text=…` raises `sqlite3.IntegrityError` | test 2 |
| confirming an AI suggestion keeps `provenance == "AI_SUGGESTED"` and sets `status/confirmedBy/confirmedAt` | test 6 |
| resurrection retains `graveyardAt` and adds a `RESURRECTED` event | test 5 |
| `verify_release` fuzzy recall reason is `"Keyword + fuzzy"` | verify_release |

**Consequence for the v0.2 design:** `Vault.search()` is frozen. Recall V2 is
built as an **additive** subsystem (`Vault.recall()` + `/api/recall`), never as a
rewrite of `search()`. The v0.1 alias map, morphology helper and normalization
functions are *reused* by Recall V2 so both paths agree on what a token is.

## 3. Graphify research dossier (audited)

Located at `research/graphify-acquisition-audit/` — 15 documents plus
`research_instrumentation/` (3 scripts + `timings.jsonl`).

| Field | Value from `MANIFEST.txt` / `README.md` |
|---|---|
| Subject | `https://github.com/Graphify-Labs/graphify.git` |
| Branch / commit inspected | `v8` @ `10130e79ed9caf8f60d998073e097531eaa1a395` (`chore(release): 0.9.91`, 2026-10-14) |
| Declared license | Apache-2.0 (`LICENSE`) + retained `LICENSE-MIT` + `NOTICE` |
| Posture | research/audit only; Graphify unmodified, IWTT unmodified, no fork/PR/issue |
| Matrix verdicts | **ADOPT 21 · ADAPT 16 · STUDY ONLY 6 · REJECT 12** |
| Benchmark numbers | recorded as **VENDOR CLAIMS, NOT REPRODUCED** (harness absent from the public repo) |

### 3.1 Verified findings that shape v0.2

1. **Graphify's retrieval is not vector search and not BM25.** `grep` for `bm25`,
   `fts5`, `vector_store`, `cosine`, `tfidf` over `graphify/*.py` → **zero hits**.
   It is a hand-weighted tiered lexical scorer (exact ≫ prefix ≫ substring),
   IDF-weighted, coverage-scaled, prefiltered by a lazily built character-trigram
   index, then BFS/DFS neighbourhood expansion rendered under a hard budget.
2. **IDF formula**: `w = log(1 + N/(1+df))`, cached per graph generation.
3. **Coverage scaling**: `score += tiered * (matched/n_terms)**2` — squared
   deliberately, because at linear coverage a 1-of-10 exact match beat a 3-of-10
   prefix+substring match.
4. **Trigram prefilter has a "never-worse" guard**: it returns `None` (full scan)
   when a needle is too short to trigram, or when its rarest trigram still covers
   more than `guard_frac = 0.10` of nodes. *The index may only speed things up,
   never change results.*
5. **Seed selection**: `max_k = 3`, `gap_ratio = 0.2`, **plus a per-term seed
   guarantee** added to fix failure #1445 (one incidental exact match at ~1000
   outranked every substring match at ~1.0 and the gap cutoff discarded all other
   seeds), and seed **dedupe by normalized label** to fix #1766 (dozens of
   identically-labelled nodes consuming all seed slots). Tie-break
   `(-score, -degree, label_len, node_id)`.
6. **Hub suppression**: `hub_threshold = max(50, p99(degree))`; hubs are *visited*
   but **not expanded as transit** unless they are seeds. Elsewhere the dossier
   stresses hub suppression must be **a penalty, not a filter**.
7. **Traversal**: depth-limited BFS/DFS (1–6, default 3) + `_complete_induced_edges`
   so the returned subgraph is not an arbitrary tree. Path search follows stored
   arc direction by default with an explicit `undirected=true` opt-out.
8. **Output ordering is by information value, not discovery order**: seeds first,
   then `(hop distance, -degree, id)`, under `char_budget = token_budget * 3`.
9. **Ambiguity refusal**: `_tier_refusal` / `_ambiguity_message` /
   `_rivals_across_files` return an explanation of the rival candidates instead of
   silently picking one.
10. **Work memory** (`reflect.py` + `querylog.py`): outcome signals
    `useful` / `dead_end` / `corrected`, aggregated with a **30-day half-life**,
    **corroboration-gated** (`_DEFAULT_MIN_CORROBORATION = 2`) so a single save
    cannot mint a trusted lesson, written to a **derived sidecar**
    (`.graphify_learning.json`) that never contaminates the canonical graph, and
    merged into output at display time.
11. **Clustering**: Leiden via `graspologic_native` (seed 42, trials 1) →
    `graspologic.partition.leiden` → `nx.community.louvain_communities(seed=42)`
    fallback chain; `ambiguous_scale = 0.5` on AMBIGUOUS edges; hub exclusion by
    percentile + majority reattachment; oversized (>25% of graph) and low-cohesion
    (<0.05 and ≥50 nodes) re-splits; `cohesion_score` self-loop safe;
    **stable community identity across rebuilds** via `sha256` membership
    fingerprints and greedy majority-overlap remapping.
12. **Provenance is weaker than marketed**: a three-value `confidence` enum
    (`EXTRACTED`/`INFERRED`/`AMBIGUOUS`), `source_file`, `source_location` (`L42`),
    optional `context`, one numeric `confidence_score`. **No per-edge evidence
    object**; `provenance` appears in 4 lines package-wide. Explanations are
    *generated* prose `why` strings, not stored per edge.
13. **Storage is the weak point at scale**: whole-file JSON, whole-graph-in-memory
    NetworkX, whole-file rewrite per build. Measured in this dossier: 1k nodes
    load 0.042 s / 0.9 MB; 10k 0.549 s / 8.5 MB; 100k 4.172 s / 88.1 MB, cluster
    37.9 s; 250k 11.8 s / 216 MB, cluster 82.6 s. 500k and 1M **exceeded available
    memory during JSON parsing** (recorded as MEASURED failures; 1M read alone
    peaked at 926 MB).
14. **License posture**: `LICENSE_AUDIT.md` §7 recommends **posture B — study the
    architecture and reimplement the small transferable pieces in IWTT's own
    idiom**, noting "Obligations: none from Graphify" for independently
    implementing a technique, and that the highest-value items (scoring model,
    seed guarantees, hub suppression, provenance vocabulary, canonical/derived
    split) are all in that category.

### 3.2 Dossier recommendations that v0.2 deliberately does NOT follow

The mission says not to assume the dossier is correct. Two recommendations were
verified against IWTT and **rejected**:

| Dossier recommendation | v0.2 decision | Why |
|---|---|---|
| Matrix #17: map outcome signals (`useful`/`dead_end`/`corrected`) onto **canonical events** | **Rejected.** Query-outcome memory is stored only in derived tables. | The mission states "DO NOT make query-learning outcomes canonical." Writing recall feedback into the canonical `events` log would make derived retrieval behaviour part of the user's permanent history and would survive a "reset recall learning" operation. Canonical events remain reserved for things the user did to their *thoughts*. |
| Matrix #14 / §2.14: adopt Graphify's tier weights (exact 1000 / prefix 100 / substring 1) and `hub_threshold = max(50, p99)` | **Rejected as constants; adopted as mechanisms.** | The dossier itself flags both as "right mechanism, wrong calibration" for prose and for sparse personal graphs. A 1000× exact tier would let one quoted phrase swamp every semantically relevant thought, and a degree-50 hub floor would never trigger in a personal vault. v0.2 re-derives both and tests them. |
| Matrix #19: Leiden via `graspologic_native` | **Rejected.** | "Do NOT add a massive dependency solely for clustering." v0.2 implements deterministic connected components + synchronous label propagation in the stdlib. |
| Matrix #12: add a closed-vocabulary `confidence` field to canonical relationships | **Rejected for v0.2.** | v0.1 already has a *stronger* model (`provenance` × `status` × `confirmed_by` × `confirmed_at`). Adding a parallel confidence enum would weaken it. v0.2 uses the existing fields as the authority signal. |

## 4. Reconciliation of earlier Arena work

Before the authoritative repository was available, a v0.1 baseline was
reconstructed from the mission's regression-freeze list. That reconstruction is
**incompatible** with Checkpoint 001 and was **discarded as production code**
(preserved read-only at `arena-work-quarantine/` outside the repository, with a
provenance note).

| Reconstruction assumption | Reality at Checkpoint 001 | Verdict |
|---|---|---|
| Durable IDs like `T-000001-a3f9c2d1e4b5` | UUIDv4 strings | **Discarded** |
| Separate `thought_originals` table for the sacred record | `thoughts.original_text` + a `BEFORE UPDATE` trigger | **Discarded** |
| Canonical and derived in **two separate SQLite files** | One file; `search_documents` / `derived_artifacts` are derived tables inside it | **Discarded** |
| Fields named `lifecycle` / `vault_state` | `type` / `status` | **Discarded** |
| Relationship vocabulary of 9 types | 11 types incl. `SUPPORTS`, `REVIVES`, `TESTS` | **Discarded** |
| Provenance alone decides authority | v0.1 splits `provenance` (origin, immutable) from `status` (CONFIRMED/SUGGESTED/REMOVED); confirming an AI suggestion **keeps** `provenance=AI_SUGGESTED` | **Discarded** — v0.2 weights the pair, not provenance alone |
| New canonical column `entity_kind` for external knowledge | — | **Discarded**: v0.2 makes **no canonical schema change** at all |
| Scoring design (IDF formula, trigram cosine, coverage, hub factor, ambiguity band, decay, corroboration gate, structured explanation codes, deterministic tie-break chain) | — | **Retained as design**, re-derived against the real schema and the dossier, re-implemented from scratch |

Nothing from the reconstruction was copied into the repository. The retained
items are architectural decisions, independently re-expressed against the real
`Vault` API.
