# AGENT_INTEGRATION.md

**Audit 10 — Agent integration.** How agents query and use Graphify's graph, what
permission and security properties that implies, and whether a comparable
interface could let an IWTT vault be queried by assistants *without* granting
unrestricted access to everything.

Research only. Nothing here is implemented, and no integration is authorized.

Verified against commit `10130e79ed9caf8f60d998073e097531eaa1a395`.

---

## 1. The integration surface, in full

Graphify reaches agents through **four** channels:

| Channel | Mechanism | Evidence |
|---|---|---|
| **MCP server** | `graphify-mcp` console script → `graphify.serve:_main`; stdio (default) or HTTP | `pyproject.toml` `[project.scripts]` |
| **CLI** | `graphify query/path/explain/…` invoked by the agent as shell commands | `cli.py`, `__main__.py` |
| **Skill/instruction files** | Per-host skill bodies + "always-on" blocks injected into agent context | `graphify/skill*.md`, `graphify/skills/<host>/references/`, `graphify/always_on/` |
| **Git hooks** | post-commit / post-checkout / merge hooks trigger detached incremental rebuilds | `hooks.py` |

Supported hosts, from directory names and skill files: Claude Code, Codex,
Cursor, Gemini CLI, Copilot, OpenCode, Kilo Code, Kiro, Amp, Trae, VS Code,
Factory Droid, OpenClaw/claw, Pi, Devin CLI, Aider, CodeBuddy, Google Antigravity
(14 host directories plus renamed skill bodies, 16 `skill-*.md` files).

## 2. The MCP tool surface (verified from `list_tools()`)

### 2.1 Tools — 10 total

| Tool | Purpose | Key parameters |
|---|---|---|
| `query_graph` | Search + traverse; returns nodes and edges as **text context** | `question` (required), `mode` ∈ {`bfs`,`dfs`}, `depth` (1–6, default 3), `token_budget` (default 2000), `context_filter` (edge-context list) |
| `get_node` | Full details for one node | `label` or `node_id` |
| `get_neighbors` | Direct neighbours with edge details | `label`, `relation_filter`, `token_budget` |
| `get_community` | All nodes in a community | `community_id`, `token_budget` |
| `god_nodes` | Most-connected nodes (degree) | `top_n`, `exclude_hubs_percentile` |
| `graph_stats` | Counts, community count, confidence breakdown | — |
| `shortest_path` | Path between two concepts | `source`, `target`, `max_hops` (default 8), `undirected` (default false) |
| `list_prs` | Open PRs with CI/review state + graph impact | `base`, `repo` |
| `get_pr_impact` | Per-PR blast radius over communities | `pr_number`, `repo` |
| `triage_prs` | Actionable PRs with impact data | `base`, `repo` |

### 2.2 Resources — 6, all read-only

`graphify://report` (the full `GRAPH_REPORT.md`) · `graphify://stats` ·
`graphify://god-nodes` · `graphify://surprises` · `graphify://audit` (confidence
breakdown) · `graphify://questions` (suggested questions).

### 2.3 Design decisions worth noting

- **Text, not JSON, as the agent-facing payload.** `query_graph` returns rendered
  text under a token budget, not a raw subgraph structure. This is a deliberate
  context-economy choice: the agent gets the smallest thing that answers the
  question, with per-node lines that are cheap to skim.
- **Multi-project support via one parameter.** Every tool accepts an optional
  `project_path`, so one server can serve several graphs, with a bounded number of
  resident graphs (`_GraphContextCache`, `_max_server_contexts()`).
- **Ambiguity refusal.** Several resolvers return an explanation of rival
  candidates rather than guessing (see `SEARCH_AND_QUERY.md` §2.5).
- **Error signalling.** `ToolError` maps to `isError: true`, distinguishing a
  genuine failure (missing `gh` CLI, unresolvable PR) from a normal text result.
- **Client-spelling tolerance.** `_node_arg()` accepts `label` / `node_id` / `id`
  because different agent frameworks pass different keys — a small compatibility
  detail with outsized practical value.
- **Version tolerance in the MCP layer itself.** `_build_server()` supports both
  the mcp 1.x decorator API and the 2.x `on_*` constructor callbacks.

## 3. How agents are *told* to use it

The always-on blocks are the load-bearing part of the integration — they change
agent behaviour before the agent decides to search:

From `graphify/always_on/claude-md.md`:

> - When `graphify-out/graph.json` exists, run `graphify query "<question>"` before
>   your first grep, search or source-file read for a codebase question, **and put
>   this rule in every subagent prompt that explores code**. Read raw files only for
>   lines the graph or an earlier read already located.
> - If `graphify-out/wiki/index.md` exists, use it for broad navigation instead of
>   raw source browsing.
> - Read `graphify-out/GRAPH_REPORT.md` only for broad architecture review or when
>   query/path/explain do not surface enough context.
> - After modifying code, run `graphify update .` to keep the graph current.
> - A committed `graphify-out/graph.json` is rewritten whole on every build, so a
>   large diff to it in a PR is expected. Do not flag it as churn.

Three things are notable here:

1. **Precedence over grep.** The instruction tries to make graph query the *first*
   action, which is what makes the tool actually used rather than available.
2. **Explicit "do not flag the big diff" rule.** The instruction anticipates the
   agent misreading a whole-file rewrite as churn — evidence that the storage
   design (§SCALING.md) creates agent-visible side effects that then need
   prompt-level mitigation. **IWTT should note this as a cost of whole-file derived
   snapshots** if such a file is ever committed to a repo.
3. **Instruction-level staleness guidance**: "Dirty graphify-out/ files are
   expected after hooks or incremental updates; dirty graph files are not a reason
   to skip graphify."

## 4. Permission and security model

| Concern | Graphify's position | Evidence |
|---|---|---|
| Transport default | **stdio** — no network listener by default | `SECURITY.md` |
| HTTP binding | Opt-in; binds `127.0.0.1` unless `--host 0.0.0.0`; warns when binding a public interface without a key | `serve_http()` |
| Authentication | Static bearer token: `Authorization: Bearer <key>` or `X-API-Key: <key>`; `GRAPHIFY_API_KEY` env; blank = disabled | `_ApiKeyMiddleware` |
| Authorization / scoping | **None beyond `project_path`.** No per-tool, per-project, per-community, or per-node permissions | `list_tools()` schemas |
| Read vs write | All MCP tools are **read-only over the graph**; nothing in the tool surface mutates the graph or the corpus | tool handlers |
| Filesystem reach | `validate_graph_path()` confines served graphs to `graphify-out/` | `security.py` |
| Prompt-injection surface | Corpus- and LLM-derived fields sanitized before entering agent context (F-010); inbound content wrapped in untrusted-data delimiters with sentinel defanging | `serve.py::_subgraph_to_text`, `llm.py` |
| Audit of agent access | Only the opt-in `querylog.py` (kind, question, corpus, nodes returned, duration) | `querylog.py` |
| Credentials stored | None | `SECURITY.md` |

**The honest summary of the permission model: Graphify is an all-or-nothing
read interface with a static shared token.** Any client that can reach the server
can query the entire graph — every path, every community, every node — with no
per-scope restriction. For a code graph on a developer's own machine that is a
reasonable posture. **For an IWTT vault of private thoughts it is not.**

## 5. Could an IWTT vault be exposed the same way without granting everything?

Design analysis only. Two directions are worth separating:

### 5.1 What IWTT should copy

- **Text-under-budget as the agent contract.** Never hand an assistant a raw
  object graph; hand it a ranked, truncated, sanitized rendering. This is the
  single biggest context-economy lesson.
- **Read-only tool surface.** No agent-facing write path in v1. If an assistant
  needs to create something, it proposes; the user writes.
- **Turnkey "use this first" instructions** as an artifact the user installs
  deliberately — and an explicit rule about what the assistant must *not* conclude
  (e.g. "large diffs to derived files are expected").
- **Sanitization at the boundary**, both directions, with the same honesty about
  limits.
- **Tool-shape conventions**: per-scope parameter (`project_path` analogue),
  ambiguity refusal with explanations, structured error signalling, tolerant
  argument spellings.
- **Opt-in, user-visible logging** of what was asked, never on by default.

### 5.2 What IWTT must add (Graphify has none of this)

| Missing capability | Why IWTT needs it | Sketch (design only) |
|---|---|---|
| **Scoped grants** | A vault contains health, relationships, finances-adjacent notes. An assistant asked about "project Chimera" has no business reading the rest. | Grants are first-class user-owned objects: `{agent, scope: tag/project/time-range/community, capabilities: [read-verbatim, read-summary, list-titles, suggest], expiry, revocable}` |
| **Redaction by default** | Even in scope, the assistant may not need verbatim private text | Return *derived* forms (titles, rationales, counts, community summaries) unless the grant includes verbatim read for that scope |
| **Write separation** | Protects canonical history from automated edits | Agent tools are read- or propose-only; proposals land in a review queue, never in the canonical store |
| **Provenance-labelled output** | The assistant must know what it is looking at | Every returned item labelled `user_authored` / `ai_derived` / `algorithmic`, mirroring the canonical/derived split |
| **Per-agent audit trail** | The user must be able to answer "what did it read?" | Local, user-visible log of served scope + item ids, deletable, never synced |
| **Session/token scoping** | A static shared key is one leak from total exposure | Per-agent tokens bound to a grant, with expiry; no ambient access |
| **Deletion propagation** | A revoked grant must actually stop serving | Grants checked at query time against canonical state, not cached in the derived snapshot |

### 5.3 Threat-model deltas versus Graphify

| Threat | Graphify exposure | IWTT exposure |
|---|---|---|
| Over-broad read | Accepted (single developer, own code) | **Unacceptable** — the vault is the sensitive asset |
| Prompt injection from corpus | Mitigated: untrusted delimiters, sentinel defanging, output sanitization | Higher stakes: IWTT ingests arbitrary external documents, and the vault itself is private |
| Credential leakage | Low — no secrets stored | Same, plus grant tokens must be treated as secrets |
| Agent writes corrupting truth | N/A (read-only) | Real risk if any write path exists — hence propose-only |
| Aggregation leakage | Low | Real: individually harmless titles can reveal a lot in aggregate; scope limits must cap total returned items, not just per-call size |
| Sync/backup leakage | Developer's own choice | Derived artifacts copied to cloud backup could expose the vault indirectly — a reason to keep sensitive derived data local and document it |

### 5.4 A concrete shape (not implemented)

```
agent (Chimera / Codex / local model)
   │  MCP over stdio or loopback HTTP, per-agent token
   ▼
IWTT agent gateway  ── enforces: grant lookup → scope filter → redaction policy
   │                                                     → item cap → audit log
   ▼
derived projection (graph snapshot + indexes)   ← rebuildable from canonical store
   ▲
   │  never written by the gateway
canonical store (thoughts, revisions, relationships, events, lifecycle)
```

The critical property: **the gateway is a read-only projection over derived data,
and grants are canonical user-owned objects.** Revoking a grant changes what the
projection serves; it never touches a thought.

## 6. Specific security concerns to flag for a later mission

1. **A static shared bearer token is not an authorisation model.** It answers "may
   you connect?", not "may you read this?".
2. **`project_path` is a scoping mechanism for *which graph*, not *which
   content*.** Multi-project support is not multi-tenant isolation.
3. **Rendered text can leak more than intended.** Once data is inside an agent's
   context window, it may be summarised, sent to another model, or logged by the
   host. Scope and redaction must be enforced *before* rendering.
4. **Community membership is a weak privacy boundary.** A community is an
   algorithmic cluster, not a user-meaningful access boundary; using it as a grant
   scope would leak across whatever the algorithm happened to group.
5. **The audit log is the user's only recourse** after a misconfiguration, and in
   Graphify it is off by default. For a private vault, an opt-out (rather than
   opt-in) local access log is defensible — but the user must control it.
6. **Local models are not automatically safe.** Routing a query to a local model
   avoids network egress but not local host compromise, other local processes, or
   prompt-injection persistence in the model's own context.

## 7. Evidence anchors

`pyproject.toml` (`[project.scripts] graphify-mcp = "graphify.serve:_main"`,
`mcp`/`starlette` extras) · `graphify/serve.py` (`list_tools()` tool schemas,
resource list, `_build_server`, `_select_graph`, `_resolve_graph_path`,
`_GraphContextCache`, `_max_server_contexts`, `_node_arg`, `ToolError`,
`_ApiKeyMiddleware`, `_build_http_app`, `serve`, `serve_http`, `_main`) ·
`graphify/security.py` (`validate_graph_path`, `sanitize_label`) ·
`graphify/llm.py` (untrusted-source wrapping, `_neutralise_injection_sentinels`) ·
`graphify/querylog.py` (`log_query`) · `graphify/hooks.py` (hook install,
`_PYTHON_DETECT`) · `graphify/always_on/claude-md.md` + five sibling blocks ·
`graphify/skills/` (14 host directories) · `graphify/skill*.md` (16 bodies) ·
`SECURITY.md` (transport defaults, "does not run a network listener by default") ·
`ARCHITECTURE.md` (`serve.py` row).
