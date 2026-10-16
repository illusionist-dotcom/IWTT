# QUERY MEMORY (IWTT v0.2)

IWTT remembers **which answers worked**, so the second time you hunt for the same
memory the right thought is a little closer to the top — and remembers which answers
did not work, so a wrong one drifts down.

This is *derived query-outcome learning*. It is not training a model, it is not
canonical, it is not permanent, and it can be erased in one call.

Code: `iwtt/recall/querymemory.py`. Storage: `recall_query_memory` (one derived
table). Boundary test: `tests/test_query_memory.py` (16 tests).

---

## 1. The boundary, first

| Guarantee | How it is enforced |
| --- | --- |
| Never canonical | The only table written is `recall_query_memory`. No `thoughts`, `revisions`, `tags`, `relationships` or `events` row is touched — asserted by comparing `canonical_fingerprint()` before and after feedback, and by `reset`/`clear` tests. |
| Never confirms a relationship | Recording feedback runs no relationship code path at all. `verify_release_v02.py` asserts an `AI_SUGGESTED` edge is still `SUGGESTED` with `confirmedBy: null` after feedback. |
| Erasable | `DELETE /api/recall/learning` → `reset_recall_learning()`; also erased by `clear_derived_data()` and `delete_all_derived_intelligence()`. |
| Inspectable | `recall_learning_stats()`, `recall_learning_history()`, `learned_queries()`, and the per-result explanation codes. |
| Cascades with the thought | `thought_id ... REFERENCES thoughts(id) ON DELETE CASCADE`. |
| Labelled | Every payload from this subsystem carries `"canonical": false, "derived": true`. |

The Graphify dossier recommended storing outcome signals in the canonical event log
(ADAPT #17). That recommendation was **rejected**: an event log is the record of what
the user did to their memories, and a search outcome is not that. Full reasoning in
`GRAPHIFY_ADAPTATION_NOTES.md`.

---

## 2. What gets recorded

`recall_query_memory(id, query_norm, thought_id, feedback, weight, rank_at_time,
score_at_time, note, created_at)`, with
`CHECK(feedback IN ('USEFUL','NOT_USEFUL','OPENED','CORRECTION'))`.

| Feedback | Weight | Recorded when |
| --- | --- | --- |
| `USEFUL` | **+1.0** | You say this was the thought you were looking for |
| `NOT_USEFUL` | **−1.0** | You say it was not |
| `CORRECTION` | **+0.6** | You point at the right answer after a miss |
| `OPENED` | **+0.15** | Automatically, when FIND MY THOUGHT's top result is opened (`record_opened_signal = True`) |

`rank_at_time` and `score_at_time` are stored so the history can say *where the
thought was when you judged it* — otherwise "not useful" is uninterpretable.

**Query key.** `memory_key(query)` = `strip_memory_filler(query) or
normalize_text(query)`. Twenty-two filler patterns ("I remember thinking something
about", "what did I write about", …) are stripped longest-first, each at most once,
and only if a non-stopword token survives — so *"I remember thinking something about
enemy adapting"* and *"enemy adapting"* learn together, while a query that is nothing
but filler still keys on something.

---

## 3. The decay model

Documented, deterministic arithmetic — the same formula is in the `usefulness()`
docstring:

```
decay(event) = 0.5 ** (age_days / HALF_LIFE_DAYS)
raw(t)       = Σ  weight(event) · decay(event)
gated(t)     = raw(t) · SINGLE_OBSERVATION_FACTOR   if explicit events < MIN_CORROBORATION
               raw(t)                               otherwise
value(t)     = clamp(gated(t), NEGATIVE_CAP, POSITIVE_CAP)
```

| Constant | Value | Why |
| --- | --- | --- |
| `query_memory_half_life_days` | **180** | Personal thoughts decay far more slowly than code facts. The dossier's ADAPT #14 recommends ~180 days over Graphify's 30; the value is configurable and is labelled as an estimate wherever it is surfaced. |
| `query_memory_min_corroboration` | **2** | "A single save cannot mint a trusted lesson." |
| `query_memory_single_observation_factor` | **0.5** | One click moves ranking a little, never decisively. |
| `query_memory_positive_cap` | **+1.0** | Enthusiasm cannot outweigh lexical evidence. |
| `query_memory_negative_cap` | **−0.75** | **Negative feedback demotes but never hides.** A demoted thought is still returned, still explained, and still opens to what you actually wrote. |

The signal enters the final score as `w_query_memory (1.5) × value`, clamped, so its
total possible contribution is `[-1.125, +1.5]` of a theoretical maximum 21.25.

`MemoryValue` exposes `value`, `raw`, `events`, `explicit_events`, `corroborated` and
`detail`; each `detail` item carries `feedback`, `rawWeight`, `ageDays`, `decay`,
`decayedWeight`, `createdAt`, `rankAtTime`, `scoreAtTime`. Because of the clamp,
`value != raw` is normal and expected — the UI shows the clamped contribution and the
raw sum, not one pretending to be the other.

`OPENED` events are **not** explicit events, so they do not satisfy corroboration on
their own: opening a result nudges, it does not endorse.

---

## 4. Explanations

Two codes, deliberately phrased as memory rather than authority:

* `PREVIOUSLY_USEFUL` — "Previously useful for a similar query"
* `PREVIOUSLY_NOT_USEFUL` — "Previously marked not useful"

Each carries the decayed `contribution` and the evidence, so a ranking change is
never mysterious. There is no `QUERY_MEMORY` code: the label has to say *which* way
the memory points.

---

## 5. API

**Python** (`Vault` delegates to `RecallEngine`):

```python
vault.record_recall_feedback(query=..., thought_id=..., feedback="USEFUL",
                             rank=1, score=12.0, note="")   # -> event dict
vault.recall_learning_stats()       # available, events, queries, thoughts,
                                    # byFeedback, learningResetAt, halfLifeDays,
                                    # minCorroboration, singleObservationFactor,
                                    # canonical: false, derived: true
vault.recall_learning_history(limit=200)
                                    # id, queryNorm, thoughtId, displayTitle,
                                    # feedback, weight, rankAtTime, scoreAtTime,
                                    # note, createdAt
vault.learned_queries(limit=100)    # queryNorm, events, thoughts, lastAt
vault.reset_recall_learning()       # deletedEvents, deletedQueries, resetAt, note
```

**HTTP**

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/recall/learning` | stats + learned queries + a plain-language `boundary` statement |
| GET | `/api/recall/learning/history` | per-event history, `limit` supported |
| POST | `/api/recall/feedback` | `query`, `thoughtId`, `feedback`, optional `rank`/`score`/`note` → **201** |
| DELETE | `/api/recall/learning` | erases every event, records `learningResetAt` |

`reset_recall_learning()` does not pretend the learning never happened: it deletes the
events and stamps `learningResetAt`, so the UI can say when memory was last cleared.
Ranking returns *exactly* to its pre-feedback order — asserted in
`tests/test_determinism.py`.

---

## 6. UI

Feedback lives on each recall result, next to the explanation of why it matched:
*Useful* / *Not this one* / *Opened* (implicit). Learning is presented as a
secondary, inspectable panel — `GET /api/recall/learning` renders counts by feedback
type, the half-life, the corroboration threshold and the reset timestamp, with a
**Reset learning** action.

The panel states the boundary in the user's words: query-outcome memory is derived,
it decays, it can be reset at any moment, and recording feedback never confirms an
AI-suggested relationship.

---

## 7. What this is not

* Not a ranking model. There are no learned weights, no features vector, no training
  step, no fit. It is a documented sum of four signed constants with exponential
  decay and a corroboration gate.
* Not a preference profile that follows you anywhere. It lives in one SQLite table in
  one local file.
* Not a way to hide a memory. The negative cap guarantees demotion, never exclusion;
  and `clear_derived_data()` restores the unlearned ranking in full.
* Not canonical. If you delete every derived table, your vault is byte-identical and
  recall rebuilds — with no memory of what you found useful, which is the correct
  behaviour for something that was only ever an opinion about search results.
