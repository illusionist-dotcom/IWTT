# IWTT v0.2 AI Boundaries

## Default: no AI

`NoAIProvider` is selected unless `IWTT_AI_BASE_URL` is explicitly set. There is no cloud account, default API key, analytics, or automatic transmission. Capture, persistence, revisions, lifecycle changes, local search, relationships, history, export/import, backup, and the graph remain functional with AI absent.

## Optional provider

The provider interface is in `iwtt/ai.py`. It includes summary, interpretation, tag suggestion, relationship-suggestion, link-assessment, and pairwise-comparison methods. The optional adapter speaks an OpenAI-compatible chat-completions API. In addition to environment configuration, Data & privacy can configure an endpoint for the current process session. This uses IWTT's existing local app process and does not require starting a second server or app.

```bash
IWTT_AI_BASE_URL="http://127.0.0.1:1234/v1" \
IWTT_AI_MODEL="your-local-model" \
python -m iwtt.server
```

If required, `IWTT_AI_API_KEY` may be set in the environment. The in-app API key is held in process memory and is not written to SQLite or exports. Do not put secrets in source control. A remote endpoint receives selected thought content only when the user explicitly invokes an AI action; use a local endpoint for fully local inference. Configuration alone does not generate summaries or interpretations. Interpretation is capped at 30,000 characters per text field, reports when the source was truncated, and returns summary, concepts, questions, and possible next steps as reviewable derived data.

## Provenance and canonical boundaries

AI summaries and interpretations are stored in `derived_artifacts`, not in original/current text or user title. They record artifact UUID, source thought IDs, artifact type, provider/model, generation time, and payload. The detail view labels them **AI-generated / Derived**. Canonical events record that an AI action occurred; deleting its artifact does not erase thought history. The user may copy an interpretation into a new, editable capture draft, which is clearly marked as AI interpretation and is saved only after the user's review and action.

## Link detection: reading thoughts and telling whether they are linked

This is the one capability where AI is asked a *judgement* question rather than a
summarisation question: **"is this thought connected to that one, in any
way?"** The answer is always a proposal.

### Three surfaces

| Surface | Route | What it asks |
| --- | --- | --- |
| Link check | `POST /api/thoughts/<id>/ai/relationships` | One thought against up to 16 locally selected candidates. Returns a verdict per candidate: linked, or read and judged unrelated. |
| Pairwise | `POST /api/ai/link-check/pair` | Exactly two thoughts, with `sourceId` and `targetId`. Returns one verdict. |
| Sweep | `POST /api/ai/link-check/sweep` | The least-connected thoughts in the vault, each against a bounded candidate pool. Bounded to 12 sources and 16 candidates each; the response states how many provider requests it made. |

### Why the candidate pool is not lexical

A pool chosen by shared words can only ever show the model the neighbours the
deterministic recall engine already found. Two thoughts that are genuinely
connected but describe that connection in different words share **no** word,
**no** tag, **no** cluster and **no** stored edge — which is exactly the case
worth finding.

So candidates are drawn from six sources and interleaved round-robin, so no
single source can crowd out the others:

| Source | How a candidate is found |
| --- | --- |
| `LEXICAL` | the v0.1 local search over the thought's own text |
| `GRAPH` | reachable through a stored relationship, up to two hops |
| `COMMUNITY` | in the same automatically discovered cluster |
| `TAG` | shares a tag |
| `LIFECYCLE` | in the same lifecycle state |
| `VAULT_CONTEXT` | a deterministic sample of the remaining thoughts |

`VAULT_CONTEXT` is the residual pool and the weakest signal. It exists
because the other five all *require* an overlap; without a residual sample
the model would never be shown the very thoughts the feature is for. Every
candidate carries `candidateSource` and `candidateReason`, and the UI shows
the breakdown, so the reviewer can see why a candidate was offered.

The pool is capped at 16 candidates. A larger pool would cost more provider
tokens and make generic proposals more likely, not fewer.

### What the model is asked, and what it is not allowed to do

The provider is instructed to treat every title and body as untrusted quoted
data, to answer `linked: false` when two texts merely share a broad subject,
and to use only the relationship types from the closed eleven-type vocabulary.
The response is then **re-validated in IWTT**, not trusted:

- a verdict naming an id that was not offered is discarded;
- a relationship type outside the vocabulary falls back to `RELATED_TO`;
- a positive verdict without a reason is discarded;
- a negative verdict keeps its explanation, so "read and judged unrelated" is
  reportable rather than silent;
- a duplicate target is kept only once.

### The verdict is never canonical

An analysis writes **no relationship at all**. `POST /api/thoughts/<id>/ai/relationships`
stores an `AI_LINK_ANALYSIS` **derived artifact** and records an
`AI_LINK_ANALYSIS_GENERATED` event that records only that the action
happened. The artifact is erasable with the rest of the derived data, is
excluded from the canonical export, and never contains canonical text.

A relationship appears only when the user presses **Save proposal**. Then it
is created with `provenance=AI_SUGGESTED` and `status=SUGGESTED` — a
proposal, not a fact — and stays that way until a human presses **Confirm**.
`scripts/verify_release_v02.py` asserts that an AI link check, a pairwise
comparison and a vault sweep between them create **zero** relationships.

### It says what it is

Every surface says the same thing in plain language: this is a **screening
result, not proof**. When nothing is found, IWTT reports how many candidates
it read and states that "no likely link found" does not prove that no
connection exists. The provider's own words are shown with the verdict, so a
user can judge the reasoning rather than take a boolean on faith.

Relationship origin and confirmation are separate:

- `provenance=AI_SUGGESTED`, `status=SUGGESTED`: proposal only, not authoritative.
- After an explicit user confirmation, provenance remains AI-suggested to preserve origin, while `status=CONFIRMED`, `confirmed_by=USER`, and `confirmed_at` record consent.
- A user-created link begins `USER_CONFIRMED` / `CONFIRMED`.

The UI marks proposals and requires an explicit Confirm action. It never silently promotes an AI proposal to confirmed knowledge.

## Recall never uses AI

v0.2 adds a substantial retrieval layer, and none of it is AI:

- `embeddings_enabled`, `semantic_stage_enabled`, `ai_required` and `network_required` are all **`False`** in `RecallConfig`, and there is no code path that could set them from the environment.
- Matching is tokens, morphology and aliases, character trigrams, bounded `difflib` similarity, and stored relationships. Nothing is embedded, nothing is sent anywhere, and no model is loaded.
- **The AI link review is not part of recall.** Candidate selection (`iwtt/recall/candidates.py`) is deterministic and offline; it reads canonical data and the rebuildable indexes and calls no provider. The provider is invoked only by the three explicit HTTP routes above, only when the user presses a button. Recall ranking is untouched by any of it.
- Every recall outcome carries an `honesty` block stating this in plain language — `embeddings`: *"NOT IMPLEMENTED. IWTT v0.2 contains no embedding model, no vector index and no semantic stage. Retrieval is lexical + fuzzy + graph, and says so."*; `ai`: *"Not used. Recall ran entirely offline against derived indexes."*; `network`: *"None. No request left this process."*; `probabilisticConfidence`: *"Scores are deterministic weighted sums, not probabilities."*
- Derived query-outcome memory is arithmetic over four signed constants with a documented half-life — not training, not a model, and erasable in one call.
- Clusters are labelled `AUTOMATICALLY_DISCOVERED_CLUSTER`. No generated title, summary or grouping is ever presented as something the user wrote.
- Fuzzy recall is **not** solved by calling an LLM, and no "semantic AI" language is used anywhere in the product or the docs.

`scripts/verify_release_v02.py` asserts the default provider is `NoAIProvider`, that all four flags are false, and that `iwtt/` imports nothing outside the standard library.

## Not in v0.1

No embeddings, semantic vector index, automatic clustering, contradiction detector, autonomous resurfacing, agent, or unattended AI task runs. The local search layer is deterministic lexical/fuzzy matching, not AI. Future AI capabilities should use the same derived artifact/provenance boundary and must not overwrite the author's original thought.
