# IWTT v0.2 — Thought Vault · Recall & Graph Intelligence

**Illusionist World of Thoughts & Tricks (IWTT)** is a local-first memory system for original thoughts, ideas, experiments, inventions, and projects. v0.1 established the durable data foundation; **v0.2 adds the way back to it** — deterministic recall from a fragment, a vague phrase, an approximate idea, a related thought, a lifecycle state, a connection or a time context, to the exact thought you wrote. It is still deliberately not the full visual Mind World.

> **Core rule:** the author's original capture is immutable. User edits are versioned. AI output, if explicitly enabled, is stored separately as derived data.

## Run locally

Requirements: Python 3.10+ (tested with the Python standard library only). No package installation, cloud account, or internet connection is needed for core use.

```bash
cd IWTT
python -m iwtt.server
```

Open <http://127.0.0.1:8000>. The default bind is loopback-only. Stop with Ctrl+C.

Optional data directory override:

```bash
IWTT_DATA_DIR="$HOME/my-iwtt-vault" python -m iwtt.server
```

Default database: `~/.iwtt/thought-vault.sqlite3` (SQLite, WAL mode). Backups are kept in `~/.iwtt/backups/` and are also downloaded by the browser. The database is created on first run. See [PRIVACY.md](PRIVACY.md) before changing the bind address.

## Launch as a Windows app

For a no-install first run, double-click `Start-IWTT.cmd`. It starts IWTT locally and opens the interface in your browser; leave the terminal window open while using it, then press Ctrl+C to stop. The vault is stored in `%LOCALAPPDATA%\\IWTT`.

To build a standalone Windows executable (Python is only needed on the build machine):

```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File scripts/build_windows_app.ps1
```

Run `dist\\IWTT-Thought-Communities.exe`; it opens the app on a free loopback port and keeps the vault in `%LOCALAPPDATA%\\IWTT`. The executable is a local server plus browser UI, not an embedded browser window. The `--data-dir` option can select a different vault folder.

## What is new in v0.2

- **FIND MY THOUGHT** — one search box that returns ranked candidates with the original text, why each one matched, its connections, and its lifecycle/time context. The text you wrote is rendered visually apart from every derived explanation of it.
- **Recall Engine V2** — twelve deterministic signals (exact phrase, IDF-weighted rare terms, coverage, character trigrams, bounded fuzzy, token overlap, title, tag, graph, temporal, lifecycle, query memory), a published scoring formula, and a theoretical maximum shown next to every score. No embedding model, no semantic stage, no AI call, no network request.
- **Explicit explanations** — 29 fixed codes with human labels and the arithmetic behind each, plus a full per-term breakdown in a secondary diagnostics view.
- **Ambiguity refusal** — when several memories are equally likely IWTT says *"I found three likely memories"* and shows what distinguishes them, instead of picking one. When nothing matches, it says so and explains what that does and does not prove.
- **Graph Intelligence** — bounded neighbourhood expansion, direction-aware traversal, hub suppression, and path finding that will not invent an edge to close a gap. `USER_CONFIRMED` and `AI_SUGGESTED` stay distinct everywhere, and searching never confirms a suggestion.
- **Derived query memory** — IWTT learns which answers were useful, with a documented 180-day half-life, a corroboration gate, and hard clamping so negative feedback demotes but never hides. Inspectable and erasable in one action; never canonical.
- **Community graph** — a clickable, bounded midnight graph with derived community filters and a selected-concept inspector for original text, source, lifecycle, tags, and relationship provenance. Dashed shared-concept links are transient visual hints, never saved relationships.
- **Capture source labels** — `THOUGHT`, `IDEA`, `MESSAGE`, `PROMPT`, `IMPORTED`, and `AI_INTERPRETATION` distinguish what a record came from while its lifecycle remains independently `THOUGHT`, `EXPERIMENT`, `INVENTION`, or `PROJECT`. Source selection is recorded in canonical event history, preserving older SQLite schemas.
- **In-app AI interpretation** — configure an OpenAI-compatible endpoint from Data & privacy, then explicitly request an interpretation. Generated concepts, questions, and next steps stay derived until you review and save an editable draft.
- **AI link detection** — ask IWTT to read a thought and tell you whether it is connected to anything. Candidates come from six structural sources (shared words, stored connections, the same community, a shared tag, the same lifecycle, and a vault-wide sample) rather than shared words alone, so a link that uses different vocabulary can still be found. Also available as an explicit two-thought comparison and a bounded vault sweep. Every verdict is a proposal: it lands as `AI_SUGGESTED`/`SUGGESTED`, is never canonical, and says so.
- **Local file reading** — import plain text, Markdown, CSV, JSON, HTML, notes, and source code into an explicitly marked thought. The selected text becomes the immutable original capture; PDF and Word extraction are not included.
- **Time and lifecycle recall** — `today`, `last week`, `past 3 months`, `recently`, `between X and Y`, `in 2024`, Graveyard inclusion — with inferred windows explicitly labelled `ESTIMATED_RANGE` rather than presented as precision IWTT does not have.
- **External-knowledge foundation** — entity kinds, a closed ten-type vocabulary, a separate provenance vocabulary, guards that raise, and a documented cross-domain chain (`Thought → Experiment → Project → Source File → Function → Bug → Fix → New Thought`) whose deferred edges each name their blocker. Interfaces and documentation only: nothing is ingested.
- **Measured performance** — recall p95 118 ms at 1,000 thoughts and 404 ms at 10,000, with the examined candidate set bounded by an absolute cap (73.5% of a 1,000-thought vault, 9.7% of a 10,000-thought one). See [BENCHMARKS.md](BENCHMARKS.md).
- **No canonical schema change.** `schema_info.schema_version` is still `1`, the export schema version is still `1`, every v0.1 route and test still behaves identically, and opening a v0.1 vault migrates it in place without rewriting a single canonical row. See [V0.1_TO_V0.2_MIGRATION.md](V0.1_TO_V0.2_MIGRATION.md).

## What works in v0.1

- Fast thought capture with a durable UUID, UTC timestamps, initial revision, and `THOUGHT_CREATED` event.
- Immutable original text plus editable current text/title, version snapshots, and a separate lifecycle type (`THOUGHT`, `EXPERIMENT`, `INVENTION`, `PROJECT`). Any type can move to any other type.
- Tags; a non-destructive Graveyard and resurrection; persistent relationships with user-confirmed vs. AI-suggested provenance and explicit confirmation.
- Local exact, substring, token, title/tag, fuzzy-word-form, and small transparent alias search. Results show the original text snippet. No embedding model is required.
- Event timeline, revision history, a clickable derived SVG community graph, vault JSON export/import, Markdown export, and actual SQLite backup files.
- Optional fictional demo records that can be removed separately.
- Rebuildable local search documents and a `Delete all derived data` safety operation.
- Optional AI provider boundary. No AI is configured by default. An OpenAI-compatible endpoint is contacted only after explicit configuration and an explicit AI action.

## Use the app

- **Capture:** type and save first; title/tags/type are optional.
- **Vault / Search:** browse active items or search a fragment. Search includes the Graveyard by default.
- **Thought detail:** inspect the original and current text separately; edit, promote, tag, connect, or inspect revisions/events.
- **Graveyard:** items remain recoverable; resurrecting records a new event and retains the previous Graveyard timestamp.
- **Connections:** browse saved relationships. Suggested links are visually marked and are not treated as confirmed until you press **Confirm**. Add append-only, user-authored evidence notes to explain why a connection matters; notes appear in connection history and path results.
- **Import a file:** choose a supported local text or source file, review a preview, and preserve its extracted text as an imported thought. File content is not silently rewritten or sent to AI. The optional AI summary remains an explicit, separate action.
- **AI interpretation:** optionally configure a hosted OpenAI-compatible provider in Data & privacy. This uses IWTT's existing local app process; it does not require a second local server. Only the selected thought text is sent when you explicitly request interpretation. A provider account/key and network connection are required for hosted AI.
- **AI link detection:** press **Check connections** on a thought to have the provider read it against up to 16 candidates and report which are linked, and which were read and judged unrelated. The Connections view adds a **compare two thoughts** box and a bounded **vault sweep**. Nothing is saved until you press **Save proposal**, and a saved proposal stays unconfirmed until you confirm it.
- **Timeline:** explore dated lifecycle, edit, tag, and relationship events.
- **Data & privacy:** export/import, backup, demo data, rebuild/delete derived data, and inspect local storage path.

A keyboard shortcut **N** opens Capture; **Ctrl/Cmd+K** focuses global search.

## Data boundaries

Canonical truth lives in SQLite tables for thoughts, revisions, tags, tag assignments, relationships, and events. `search_documents` and `derived_artifacts` can be rebuilt or deleted. Search coordinates and graph positions are never stored as thought meaning. Details are in [ARCHITECTURE.md](ARCHITECTURE.md), [DATA_MODEL.md](DATA_MODEL.md), and [EVENT_MODEL.md](EVENT_MODEL.md).

## Tests

```bash
python -m unittest discover -s tests -v        # 166 tests
python scripts/verify_release.py               # the v0.1 contract, end to end
python scripts/verify_release_v02.py           # the v0.2 contract, end to end
python scripts/benchmark_recall.py --suite     # 1,000 and 10,000 thoughts, measured
python scripts/build_sample_vault.py           # a demo vault to look at
```

The tests use temporary SQLite files and leave your personal vault untouched. Both
release verifiers print `PASS` or `FAIL` with the evidence, and exit non-zero on
failure; the benchmark script fails if a published performance budget is exceeded.

The suite includes a **destructive canonical/derived test**: it creates canonical
memory, builds every derived artefact, exercises recall and the graph, deletes all of
it, and proves that thoughts, original text, revisions, events, relationships and
provenance survive byte-identically — then rebuilds and shows recall returning the
same ranking.

## Project files

- `iwtt/store.py` — SQLite schema, canonical operations, v0.1 search, import/export, backup, demo records, and the v0.2 delegate surface (all v0.2 imports are lazy, inside methods).
- `iwtt/recall/` — the v0.2 recall engine: `config`, `text`, `analysis`, `index`, `scoring`, `filters`, `expansion`, `paths`, `graphview`, `clusters`, `querymemory`, `diagnostics`, `results`, `engine`, `external`.
- `iwtt/server.py` — standard-library HTTP API and static web server (v0.1 routes unchanged, v0.2 routes added).
- `iwtt/ai.py` — no-AI fallback and opt-in OpenAI-compatible provider interface. **Not used by recall.**
- `web/` — self-contained browser interface; no external CSS, JS, fonts, or image dependencies.
- `tests/` — 166 automated tests: store, recall engine, graph intelligence, query memory, determinism, layering boundaries, canonical destruction, v0.1→v0.2 migration, and the AI link-detection surface.
- `scripts/` — `verify_release.py`, `verify_release_v02.py`, `benchmark_recall.py`, `build_sample_vault.py`.
- `research/graphify-acquisition-audit/` — the research dossier v0.2 was designed from. Retained unchanged; see [GRAPHIFY_ADAPTATION_NOTES.md](GRAPHIFY_ADAPTATION_NOTES.md).

Documentation lives at the **repository root**, not in a `docs/` directory:

| Document | What it covers |
| --- | --- |
| [RECALL_ENGINE.md](RECALL_ENGINE.md) | Signals, weights, tiers, candidate generation, ranking, states, ambiguity refusal, time/lifecycle, explanations, honesty, API |
| [GRAPH_INTELLIGENCE.md](GRAPH_INTELLIGENCE.md) | Edge authority, provenance, expansion, hub suppression, paths, `GraphViewModel`, clustering |
| [QUERY_MEMORY.md](QUERY_MEMORY.md) | Derived query-outcome learning: weights, decay, corroboration, reset, boundary guarantees |
| [EXTERNAL_KNOWLEDGE_ARCHITECTURE.md](EXTERNAL_KNOWLEDGE_ARCHITECTURE.md) | Entity kinds, reserved vocabulary, guards, the cross-domain chain and its named blockers |
| [V0.1_TO_V0.2_MIGRATION.md](V0.1_TO_V0.2_MIGRATION.md) | What migration does, what it never changes, backups, rollback, verification evidence |
| [GRAPHIFY_ADAPTATION_NOTES.md](GRAPHIFY_ADAPTATION_NOTES.md) | Provenance: adapted / independently implemented / rejected, and the license posture |
| [BENCHMARKS.md](BENCHMARKS.md) | Measured performance at 1,000 and 10,000 thoughts, with method and budgets |
| [BASELINE_AUDIT.md](BASELINE_AUDIT.md) | Audit of the authoritative v0.1 baseline (Checkpoint 001) |
| [ARCHITECTURE.md](ARCHITECTURE.md) · [DATA_MODEL.md](DATA_MODEL.md) · [EVENT_MODEL.md](EVENT_MODEL.md) | System shape, canonical schema, event log |
| [AI_BOUNDARIES.md](AI_BOUNDARIES.md) · [PRIVACY.md](PRIVACY.md) | What AI may and may not do; local-first guarantees |
| [IMPORT_EXPORT.md](IMPORT_EXPORT.md) · [TESTING.md](TESTING.md) | Portability and the test strategy |
| [SOURCE_FILE_INVENTORY.md](SOURCE_FILE_INVENTORY.md) · [FUTURE_MIND_WORLD.md](FUTURE_MIND_WORLD.md) | Every file and its purpose; what v0.2 deliberately does not build |

## Honest limits

**Unchanged from v0.1:** no sync, no encryption-at-rest layer, no user accounts, no multi-user access control, no mobile client, no full infinite/3D Mind World, and no direct database restore/import from arbitrary SQLite files. The server has no authentication; keep it bound to `127.0.0.1` unless you deliberately add network access controls.

**Still true in v0.2, and said out loud by the software itself:**

- **No embeddings and no semantic model.** Recall is lexical + fuzzy + graph. Every outcome carries an `honesty` block stating this in plain language, so the claim cannot be inferred only from documentation.
- **No AI and no network in the recall path.** `ai_required`, `network_required`, `embeddings_enabled` and `semantic_stage_enabled` are all `False`, and the No-AI default is unchanged.
- **Scores are deterministic weighted sums, not probabilities.** A 13.75 out of a possible 21.25 is not "65% confident".
- **Relationship edges enrich candidates; they never replace lexical evidence.** A thought with no word in common with your query cannot become a strong match by association.
- **AI link detection is a screening result, not proof.** A model reading two thoughts can miss a real connection and can propose one that does not exist. IWTT shows you its reasoning, records how many candidates it read and where they came from, and refuses to let a verdict become canonical on its own. The candidate pool is capped at 16, so a link to a thought outside that pool will not be found.
- **No external-entity ingestion.** Local text/source files can be captured as clearly tagged imported thoughts. This build does not create repository/file/function entities, parse ASTs, or extract PDF/Word content.
- **Clustering is automatic and derived**, labelled `AUTOMATICALLY_DISCOVERED_CLUSTER`, and never canonical truth.

See each architecture and privacy document for more.
