# IWTT v0.2 AI Boundaries

## Default: no AI

`NoAIProvider` is selected unless `IWTT_AI_BASE_URL` is explicitly set. There is no cloud account, default API key, analytics, or automatic transmission. Capture, persistence, revisions, lifecycle changes, local search, relationships, history, export/import, backup, and the graph remain functional with AI absent.

## Optional provider

The provider interface is in `iwtt/ai.py`. It includes summary, interpretation, tag suggestion, and relationship-suggestion methods. The optional adapter speaks an OpenAI-compatible chat-completions API. In addition to environment configuration, Data & privacy can configure an endpoint for the current process session. This uses IWTT's existing local app process and does not require starting a second server or app.

```bash
IWTT_AI_BASE_URL="http://127.0.0.1:1234/v1" \
IWTT_AI_MODEL="your-local-model" \
python -m iwtt.server
```

If required, `IWTT_AI_API_KEY` may be set in the environment. The in-app API key is held in process memory and is not written to SQLite or exports. Do not put secrets in source control. A remote endpoint receives selected thought content only when the user explicitly invokes an AI action; use a local endpoint for fully local inference. Configuration alone does not generate summaries or interpretations. Interpretation is capped at 30,000 characters per text field, reports when the source was truncated, and returns summary, concepts, questions, and possible next steps as reviewable derived data.

## Provenance and canonical boundaries

AI summaries and interpretations are stored in `derived_artifacts`, not in original/current text or user title. They record artifact UUID, source thought IDs, artifact type, provider/model, generation time, and payload. The detail view labels them **AI-generated / Derived**. Canonical events record that an AI action occurred; deleting its artifact does not erase thought history. The user may copy an interpretation into a new, editable capture draft, which is clearly marked as AI interpretation and is saved only after the user's review and action.

Relationship origin and confirmation are separate:

- `provenance=AI_SUGGESTED`, `status=SUGGESTED`: proposal only, not authoritative.
- After an explicit user confirmation, provenance remains AI-suggested to preserve origin, while `status=CONFIRMED`, `confirmed_by=USER`, and `confirmed_at` record consent.
- A user-created link begins `USER_CONFIRMED` / `CONFIRMED`.

The UI marks proposals and requires an explicit Confirm action. It never silently promotes an AI proposal to confirmed knowledge.

## Recall never uses AI

v0.2 adds a substantial retrieval layer, and none of it is AI:

- `embeddings_enabled`, `semantic_stage_enabled`, `ai_required` and `network_required` are all **`False`** in `RecallConfig`, and there is no code path that could set them from the environment.
- Matching is tokens, morphology and aliases, character trigrams, bounded `difflib` similarity, and stored relationships. Nothing is embedded, nothing is sent anywhere, and no model is loaded.
- Every recall outcome carries an `honesty` block stating this in plain language — `embeddings`: *"NOT IMPLEMENTED. IWTT v0.2 contains no embedding model, no vector index and no semantic stage. Retrieval is lexical + fuzzy + graph, and says so."*; `ai`: *"Not used. Recall ran entirely offline against derived indexes."*; `network`: *"None. No request left this process."*; `probabilisticConfidence`: *"Scores are deterministic weighted sums, not probabilities."*
- Derived query-outcome memory is arithmetic over four signed constants with a documented half-life — not training, not a model, and erasable in one call.
- Clusters are labelled `AUTOMATICALLY_DISCOVERED_CLUSTER`. No generated title, summary or grouping is ever presented as something the user wrote.
- Fuzzy recall is **not** solved by calling an LLM, and no "semantic AI" language is used anywhere in the product or the docs.

`scripts/verify_release_v02.py` asserts the default provider is `NoAIProvider`, that all four flags are false, and that `iwtt/` imports nothing outside the standard library.

## Not in v0.1

No embeddings, semantic vector index, automatic clustering, contradiction detector, autonomous resurfacing, agent, or unattended AI task runs. The local search layer is deterministic lexical/fuzzy matching, not AI. Future AI capabilities should use the same derived artifact/provenance boundary and must not overwrite the author's original thought.
