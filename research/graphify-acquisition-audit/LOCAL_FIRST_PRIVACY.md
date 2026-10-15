# LOCAL_FIRST_PRIVACY.md

**Audit 9 — Local-first / privacy.** What Graphify stores locally, what leaves the
machine, whether telemetry exists, and how this compares with IWTT's strict
local-first requirement.

Verified against commit `10130e79ed9caf8f60d998073e097531eaa1a395`.

---

## 1. Findings at a glance

| Question | Answer | Evidence |
|---|---|---|
| Does telemetry exist? | **No** | Zero matches for `telemetry\|posthog\|segment\|sentry\|analytics` in `graphify/*.py`; README: "No telemetry, no usage tracking, no analytics." |
| Are cloud services required? | **No** | The only vendor references in code are two `print("https://app.graphify.com")` upsell lines (`cli.py:139`, `__main__.py:1069`). No API calls home. Graphify Cloud is a separate commercial product. |
| Is an LLM required? | **Only for non-code content** | Code extraction is AST-only. A code-only corpus runs offline with no API key. Documents/PDFs/images/video need a backend. |
| Can source content leave the machine? | **Yes, if a cloud backend is configured** | Semantic pass sends dispatched file content to the selected provider. Fully avoidable via `ollama` (local) or by never ingesting non-code content. |
| What is stored locally? | Everything | `graphify-out/`: `graph.json`, `.graphify_analysis.json`, `.graphify_labels.json`, `.graphify_learning.json`, `GRAPH_REPORT.md`, `wiki/`, `obsidian/`, `canvas`, `graph.svg/html/graphml/cypher`, `cache/`, `reflections/LESSONS.md`, `memory/` |
| Is anything sensitive logged by default? | **No** | Query logging is opt-in only (`querylog.py`, #1797) |
| Does it store credentials? | **No** | `SECURITY.md`: "Does not store credentials or API keys" |

## 2. The local-first architecture, as implemented

### 2.1 The two-tier privacy model

Graphify draws an explicit line that IWTT should copy conceptually:

| Tier | Content | Locality |
|---|---|---|
| Deterministic AST | Source code | **Always local.** tree-sitter parse, no model, "nothing leaves your machine" (README line 36) |
| Semantic pass | Documents, PDFs, images, video transcripts | Sent to a configured backend, **only if one is configured** |

The code comment in `llm.py` states the boundary directly: *"The semantic (LLM)
extraction runs on documents/papers/images — code files are handled by the
deterministic AST engine and never reach the model."*

### 2.2 Backends, local and remote

`llm.py::BACKENDS` (verified in code):

| Backend | Endpoint | Local? | Key |
|---|---|---|---|
| `ollama` | `http://localhost:11434/v1` (default; `OLLAMA_BASE_URL`/`OLLAMA_HOST` override) | **Yes** — pricing `0.0`, default model `qwen2.5-coder:7b` | none |
| `claude-cli` | routes through the locally installed `claude` CLI | Local process, remote service | none (subscription) |
| `claude` | api.anthropic.com | No | `ANTHROPIC_API_KEY` |
| `kimi` | api.moonshot.ai | No | `MOONSHOT_API_KEY` |
| `openai` | OpenAI API | No | `OPENAI_API_KEY` |
| `gemini` | Google API | No | `GEMINI_API_KEY` / `GOOGLE_API_KEY` |
| `bedrock` | AWS | No | IAM chain |
| `deepseek` | DeepSeek API | No | `DEEPSEEK_API_KEY` |
| `azure` | Azure OpenAI | No | — |

The README's own warning is worth repeating as a privacy-relevant disclosure:
auto-detection order puts **Gemini → Kimi → … → Ollama** last, and README notes
Kimi routes to servers in China. A user who sets one cloud key in the environment
gets cloud extraction by default; `--backend ollama` or an explicit `--backend`
flag is required to keep everything local. **For IWTT, whose premise is that
personal thoughts never leave the machine, defaulting to the most remote option
would be unacceptable — this ordering is a pattern to invert, not adopt.**

### 2.3 Guards that show the authors take this seriously

- **`GRAPHIFY_ALLOW_LOCAL_PROVIDERS`**: a repo-local `./.graphify/providers.json`
  is **ignored** unless explicitly allowed, because a custom provider file
  "control[s] where prompts are sent" — i.e. a cloned repository cannot silently
  redirect your content to an attacker's endpoint. (Non-HTTPS, non-loopback
  endpoints also warn.)
- **Explicit-fetch-only networking**: `ingest.py` fetches only URLs the user
  supplies; there is no crawling and no phone-home.
- **`SECURITY.md` enumerates "Optional network calls"** — ingest, PDF (local),
  watch (local FS only) — and states there is no default network listener.
- **HTTP MCP is opt-in** and binds `127.0.0.1` unless `--host 0.0.0.0` is passed,
  with a warning printed when binding a public interface without an API key.

### 2.4 Query logging: the correct default

`querylog.py` is the one place where user questions could leak into a log, and it
is **opt-in only**, with an explicit rationale recorded in the source (#1797):

> "The log records every query/path/explain question and corpus path (and full
> responses if `GRAPHIFY_QUERY_LOG_RESPONSES`) in a plaintext file under
> `~/.cache` — outside any repo's `.gitignore`/retention. A default-on record of
> proprietary queries contradicts graphify's on-device, no-telemetry posture, so
> it is OFF unless explicitly enabled."

Switches: `GRAPHIFY_QUERY_LOG=<path>`, `GRAPHIFY_QUERY_LOG_ENABLE=1`
(→ `~/.cache/graphify-queries.log`), `GRAPHIFY_QUERY_LOG_RESPONSES=1` (adds full
responses), `GRAPHIFY_QUERY_LOG_DISABLE=1` (wins over everything, back-compat).
The logger "never raises" — logging cannot break a query.

**For IWTT this is the right precedent, including the reasoning.** A personal
thought system must not log questions by default; and if it does, the log must be
user-visible, user-deletable, and outside the canonical store.

### 2.5 Prompt-injection defences relevant to a local-first agent surface

Because Graphify feeds corpus-derived text into agent context, it hardens both
directions:

- **Inbound (content → model)**: `_read_files()` wraps each file in a hash-stamped
  `<untrusted_source path=… sha256=…>` delimiter block; the extraction system
  prompt instructs the model to treat it as inert data; and
  `_neutralise_injection_sentinels()` defangs known chat-template jailbreak tokens
  (`<|im_start|>`, `[INST]`, `<<SYS>>`, forged `</untrusted_source>` closers, …).
  SECURITY.md is refreshingly honest: *"table-stakes defense … it does not make
  injection impossible, but changes it from 'works on first try' to 'requires
  evasion.'"*
- **Outbound (graph → agent)**: every LLM- or corpus-derived field in MCP tool
  output passes through `sanitize_label()` (strips control characters, caps at 256
  chars) so a hostile node label cannot inject ANSI escapes or fake log lines into
  an agent's context (F-010).
- **File output**: `_yaml_str()` in `ingest.py` escapes control characters,
  `\t`, `\0`, U+2028/U+2029 so a hostile fetched page title cannot break out of a
  YAML scalar and inject sibling keys (F-009/F-019).
- **SSRF**: `validate_url()` (http/https only, blocks private/loopback/link-local
  and cloud-metadata endpoints, re-validates redirects), `safe_fetch()` (50 MB
  cap, non-2xx raises), `_NoFileRedirectHandler` (blocks `file://` redirects).

If an IWTT agent surface ever exposes a vault, these four controls (untrusted
delimiters, sentinel defanging, output sanitization, SSRF guard) are the minimum
baseline.

## 3. Comparison with IWTT's requirement

IWTT's requirement is *strict local-first*: the user's thoughts are private by
default, and derived metadata may be rebuilt without exposing the corpus.

| IWTT requirement | Graphify | Assessment |
|---|---|---|
| Nothing leaves the machine without explicit configuration | Holds, **but** cloud backends auto-detect from environment keys, and Ollama is last in that order | **Adoptable with an inverted default**: local-first must be the default, remote must require an explicit, per-vault opt-in |
| Works with no network at all | Yes for code-only corpora; non-code content needs a backend | **Partial** — an all-local stack (Ollama) is supported and documented |
| No telemetry | Yes, verified | **Adopt** |
| Derived data is rebuildable and separable | Yes — AST cache, semantic cache, sidecar learning file all rebuildable; `graph.json` itself is regenerable | **Adopt** |
| User data survives deletion of derived data | Yes for code: the corpus is the source of truth, `graphify-out/` is disposable | **Adopt** (this is exactly IWTT's canonical/derived rule) |
| Query/question privacy | Opt-in logging only | **Adopt** |
| Secrets handling | No credentials stored; keys come from env | **Adopt** |
| Content stays inside the vault boundary | `GRAPHIFY_ALLOW_LOCAL_PROVIDERS` stops a repo-local provider file from redirecting prompts | **Adopt** — a directly transferable threat model for a folder that may contain imported documents |

## 4. Residual risks to note honestly

1. **Environment-key auto-detection is a privacy footgun.** A user with
   `OPENAI_API_KEY` exported for unrelated work gets cloud extraction without
   opting in. IWTT should require an explicit per-vault setting for any remote
   processing and should show what will be sent, before sending.
2. **`~/.cache/graphify-queries.log`** is outside the repo, outside the vault, and
   outside most backup/gitignore hygiene if the user enables it. Any comparable
   IWTT log should live inside the vault, be visible in the UI, and be deletable
   in one action.
3. **Transitive network surface.** Optional extras pull packages with their own
   behaviours (e.g. `yt-dlp` for video ingestion, `boto3` for Bedrock). Enabling
   an extra is a privacy decision, not just a dependency decision.
4. **No encryption at rest.** `graph.json` and caches are plaintext. Graphify is a
   developer tool, so this is reasonable; IWTT's threat model (a personal vault of
   private thoughts) may demand encryption for derived artifacts, or at least a
   documented position on disk-level encryption.
5. **Injection defences are acknowledged as incomplete.** The authors explicitly
   decline to claim injection-proofing. A future IWTT agent interface should
   assume the same.

## 5. Evidence anchors

Package-wide grep (no telemetry terms) · `README.md` §Privacy (lines ~415–421),
line 36, line 123 · `SECURITY.md` §Security Model, §Threat Surface, §What graphify
does NOT do, §Optional network calls · `graphify/querylog.py` (`_log_path`,
`_log_responses`, reason comment #1797) · `graphify/llm.py` (`BACKENDS`,
`_resolve_ollama_base_url`, `_load_custom_providers`,
`GRAPHIFY_ALLOW_LOCAL_PROVIDERS`, `_read_files`, `_neutralise_injection_sentinels`,
`_bind_node_evidence`) · `graphify/security.py` (`validate_url`, `safe_fetch`,
`safe_fetch_text`, `_SSRFGuardedHTTPHandler`, `_NoFileRedirectHandler`,
`validate_graph_path`, `sanitize_label`, `check_graph_file_size_cap`) ·
`graphify/ingest.py` (`_yaml_str`, fetch sites) · `graphify/serve.py`
(`_ApiKeyMiddleware`, `serve_http` binding warnings, `sanitize_label` call sites) ·
`graphify/paths.py` (`GRAPHIFY_OUT`) · `graphify/cli.py:139`,
`graphify/__main__.py:1069` (vendor upsell lines, no API calls).
