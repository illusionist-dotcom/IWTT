# IWTT v0.2 AI Boundaries

## Default: no AI

`NoAIProvider` is selected unless `IWTT_AI_BASE_URL` is explicitly set. There is no cloud account, default API key, analytics, or automatic transmission. Capture, persistence, revisions, lifecycle changes, local search, relationships, history, export/import, backup, and the graph remain functional with AI absent.

## Optional provider

The provider interface is in `iwtt/ai.py`. It includes summary, tag suggestion, and relationship-suggestion methods. The optional adapter speaks an OpenAI-compatible chat-completions API. Configuration is explicit:

```bash
IWTT_AI_BASE_URL="http://127.0.0.1:1234/v1" \
IWTT_AI_MODEL="your-local-model" \
python -m iwtt.server
```

If required, `IWTT_AI_API_KEY` may be set in the environment. Do not put secrets in source control. An endpoint may be remote if an operator deliberately configures one; use a local endpoint for fully local inference. Configuration alone does not generate summaries: the user must invoke the per-thought action. The current UI exposes explicit summary generation; other provider methods are extension points and are not exposed as automatic background jobs.

## Provenance and canonical boundaries

An AI summary is stored in `derived_artifacts`, not in original/current text or user title. It records artifact UUID, source thought IDs, artifact type, provider/model, generation time, and payload. The detail view labels it **AI-generated / Derived**. `AI_SUMMARY_GENERATED` is a canonical event that records the action; deleting the artifact does not erase thought history.

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
