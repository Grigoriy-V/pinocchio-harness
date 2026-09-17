# Roadmap 33, research: how the references shape the model layer and its telemetry

**Date:** 2026-09-17. **Status:** research with options; nothing here is
approved until the human says so in words. Written under the AGENTS.md rule
of 2026-09-14 (a large step starts with the references; the build cites the
report and is checked against it).

The step (ROADMAP 33): a per-model-family profile so the system-message
flattening, the `<|"|>` repair and the end-marker stripping run only where
the family needs them; a provider profile instead of a URL substring;
`tool_choice`, `parallel_tool_calls`, cache hints, reasoning passthrough,
per-call overrides, `Retry-After`; `usage.cost` stored on the run and in
`model_finished`; telemetry opened in Chainlit; the A10 cost model retired.

## 1. What each reference does

Read on 2026-09-17 by five subagents from the sources in §6: Codex
(`codex-rs/protocol`, `models-manager`, `model-provider-info`, `codex-api`,
`codex-client`, `otel`, `core/src/client.rs`, `context_manager`), Hermes
(`agent/transports/*`, `*_adapter.py`, `model_metadata.py`, `models_dev.py`,
`usage_pricing.py`, `turn_recovery.py`, `message_sanitization.py`,
`context_compressor.py`, the CLI status and usage mixins), OpenClaw (docs:
custom-providers, models, prompt-caching, thinking, model-failover,
usage-tracking, opentelemetry; the `pi-ai` package's `types.ts`,
`api/openai-completions.ts`, `api/openai-responses.ts`,
`api/anthropic-messages.ts`, `api/transform-messages.ts`, `models.ts`,
`utils/json-parse.ts`), DeepSeek Harness (`packages/llm/llm`,
`llm-deepseek`, `llm-pi-ai`, `llm-retry`, `token-meter`, `compaction-basic`,
`session-telemetry-otel`, the docs), Claude Code (official docs: env-vars,
model-config, prompt-caching, llm-gateway-protocol, network-config, errors,
costs, statusline, monitoring-usage; the Messages API reference). Every
subagent read through fetch summaries, not local clones; a name it could not
confirm is marked "not found" in §7.

| question | Claude Code | Codex | DeepSeek | Hermes | OpenClaw | this harness |
|---|---|---|---|---|---|---|
| the model's profile | one family; a *platform* profile (API, Bedrock, Vertex, Foundry, gateway) by `CLAUDE_CODE_USE_*`, aliases by `ANTHROPIC_DEFAULT_*_MODEL`; `[1m]` suffix | `ModelInfo` per slug from the backend `/models`, a bundled `models.json`, a slug fallback (272k, 10,000-byte truncation), config overrides last; matched by the longest slug prefix, then `namespace/suffix`; fields: window, efforts, summaries, verbosity, modalities, truncation policy, instructions template | a catalog per adapter: `DeepSeekCatalogModel` (window, max tokens, modalities, `systemPromptUpdate`), `PiAiModelProfile` (window, max tokens, `reasoningEfforts`, `compat`); `compat` is pi-ai's switch list (`thinkingFormat` deepseek/zai/qwen/chat-template/…, `maxTokensField`, `requiresReasoningContentOnAssistantMessages`, `supportsDeveloperRole`, `supportsUsageInStreaming`, …), auto-detected by base URL upstream | no enum: `api_mode` (chat / anthropic / responses), an adapter per native provider, substring tables by slug (`DEFAULT_CONTEXT_LENGTHS`: glm 202K, qwen 131K, kimi 262K, …; `_OPENROUTER_REASONING_PREFIXES`), models.dev cached 4 h for window, price, capabilities | `models.providers.<id>.models[]`: id, reasoning, input, contextWindow, maxTokens, `cost{input,output,cacheRead,cacheWrite}`, `compat` (`supportsStore`, `supportsDeveloperRole`, `supportsMidConvoSystemMessages`, `maxTokensField`, `thinkingFormat`, `supportsUsageInStreaming`, `requiresThinkingAsText`, …); catalog generated from models.dev, refreshed every ~6 h | a set: endpoint, name, `auth_style`, dials; no family; every repair runs for every model |
| system messages | Messages API `system` top level; mid-conversation `role: system` entries carry `cache_control` | never sent: instructions field + `developer` items; AGENTS.md as a `user` item | `systemPromptUpdate: 'in-history'` (a changed prompt appends after cached history) | Anthropic path: extracted to top-level `system`; chat path: left as system messages | `supportsMidConvoSystemMessages` compat flag; `developer` role only when `model.reasoning && supportsDeveloperRole` | leading system messages joined; a later one carried into the next user message, for every model (Qwen3.8's template, 2026-09-05) |
| tool JSON, markers, `<think>` | not documented | none: arguments pass through raw; no marker stripping | none: "raw JSON strings end-to-end"; `'string-thinking'` format upstream | `<think>`-family tags scrubbed on every surface (`think_scrubber.py`); bad JSON retried 3× then a tool-role error; no marker stripping | `parseStreamingJson()`: repair (control chars, escapes, trailing commas), partial JSON, `{}`; `[tool_name]` in text promoted only on an exact name | `<|"|>` repair (Gemma on vLLM) and four end markers stripped, on every completion of every endpoint |
| provider profile | env flags per platform; `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, `apiKeyHelper` | `ModelProviderInfo`: name, base_url, env_key, auth command, `wire_api` (Responses only), query params, headers, env headers, `request_max_retries` 4, `stream_max_retries` 5, `stream_idle_timeout_ms` 300000 | `PiAiProviderProfile`: apiKeyEnv, `api` (completions / responses / anthropic), baseURL, headers, timeoutMs, transport (sse / websocket), `retryPolicy`; the route is part of the model's identity | `ProviderConfig` (≈38 built-ins) + plugin `ProviderProfile`: api_mode, base_url, env vars, default headers, `fixed_temperature`, `default_max_tokens`, `supports_prompt_cache_key`; `api_mode` host-mandated by URL, then overlay, else chat | provider: baseUrl, apiKey, `api` enum, auth, headers, timeoutSeconds, maxTokens, `models[]`; failover `primary` + `fallbacks[]`; key rotation on rate-limit shapes only | `auth_style` bearer / modal_proxy; `"openrouter.ai" in endpoint` in two places; `providers` (router hosts) and `extra_body` |
| `tool_choice`, `parallel_tool_calls` | not documented; the API's default is auto with parallel calls | `tool_choice: "auto"` always; `parallel_tool_calls` a request bool (off on responses-lite) | neither sent ("no `tool_choice`, `top_p`, or penalty fields") | neither sent; `tool_use_enforcement` is prompt text | `toolChoice` passed through when given; `extra_body.tool_choice: "required"` for stubborn local backends; `parallel_tool_calls` not found | `tool_choice: "auto"` when tools are sent; no `parallel_tool_calls` |
| cache hints | `cache_control` on system blocks and messages entries; two TTL buckets (main 1 h on a subscription, 5 m elsewhere); disable per family | `prompt_cache_key` = session id; `store: false` | `cacheRetention` none/short/long through pi-ai; DeepSeek relies on in-history prompt updates | Anthropic `cache_control` at 4 breakpoints, TTL 5m/1h, on native and OpenRouter; `prompt_cache_key` scope id; Qwen 5-minute cache | `cacheRetention` none/short/long; Anthropic markers on system, last tool, last message; OpenAI `prompt_cache_key` + `prompt_cache_retention`; nothing sent to custom base URLs (vLLM) | none; `cached_tokens` read from usage |
| reasoning | thinking blocks with signatures passed back "complete and unmodified" in a tool loop; on rejection, dropped and retried; effort `output_config.effort`; adaptive thinking | `Reasoning{encrypted_content}` kept in history and resent; `include: ["reasoning.encrypted_content"]`; effort ladder `none…ultra` resolved per model | `reasoning_content` joined from reasoning blocks and sent "on every reasoning-carrying turn", never empty; signatures in `replayState`; compaction keeps text only | persisted (`reasoning`, `reasoning_content`, `reasoning_details`); DeepSeek/Kimi/MiMo require it on every assistant message (padded), Mistral/Groq get it stripped; after compression "on the newest assistant turn only"; effort clamped per wire | resent as `reasoning_content` / `reasoning_details` on chat completions when the model is the same; converted to text for a different model; `/think off…max`, `thinkingDefault` | dropped at the parser; nothing resent; thinking off by `extra_body` / `chat_template_kwargs` per set |
| per-call overrides | a "small fast model" (`ANTHROPIC_DEFAULT_HAIKU_MODEL`) for summaries and titles; subagent model; compaction inherits thinking | compaction: same model, its own effort, `compact` subagent header; `COMPACT_USER_MESSAGE_MAX_TOKENS` 20,000 | `purpose: 'compaction' | 'session-title'`; compaction `summarizationProvider/Model`, maxTokens 8192; session title `maxOutputTokens` 64, thinking off | `auxiliary.<task>.{model,provider,base_url}` (compression, vision, title), `transient_retries` 2, compression timeout 120; summary ≤ 10,000 tokens | `compaction.model`, `subagents.model`, `heartbeat.model`, `utilityModel`, `imageModel`; `compaction.thinkingLevel: inherit` | none: the summary call is the same model with the same body, and is not even traced |
| retries, `Retry-After` | SDK: 2 retries, backoff, `retry-after` honoured, 408/409/429/5xx; four stream watchdogs (180/300 s); `API_TIMEOUT_MS` 600000 | HTTP: 5xx and transport, 200 ms × 2ⁿ ± 10 %, 429 not at that layer; stream: 5 retries with the server's delay parsed from "try again in …"; idle 300 s | 5 retries, 500 → 10,000 ms, jitter 0.1, codes EMPTY_RESPONSE/RATE_LIMIT/SERVER/TIMEOUT/TRANSPORT; `retry-after` (seconds or date) replaces the delay when within the cap | 3 retries; `Retry-After` honoured on every retryable error capped 600 s, else jittered 2 → 60 s; cooldown `min(60·2ⁿ, 14400)`; empty-response ladder; overflow → compress → restart ≤ 3 | rate limits 10 attempts, other transient 8 in 90 s, cap 30 s, `Retry-After` / `retry-after-ms` as minimums; failover on auth, rate limit, overloaded, timeout, 404; never on overflow | 2 retries, 0.5 × 2ⁿ, `{408,409,425,429,500,502,503,504}`; `Retry-After` not read; a timeout never retried (ISS-0044); the next router host after the retries |
| usage and cost | cost "computed locally from token counts at list price", `modelPricing` override; `/cost` (= `/usage`): total cost, API and wall duration, per-model tokens, a prompt-cache line; status line `cost.total_cost_usd`, `context_window.used_percentage` | no pricing table; the backend prices the turn afterwards (`turn-costs`) → `ThreadUsage` micro-USD; `/status`: tokens, "% left", limit bars, thread cost | no cost: `NO_COST`, `emptyPiUsage()`; usage on `assistant/message`; a `/cost`: not found | provider `usage.cost` **dropped** (issue #105215); estimated from a price table (models.dev, OpenRouter `/models`); `sessions` columns for tokens, `estimated_cost_usd`, `actual_cost_usd`, `cost_status`; status bar `12.4K/200K │ 6% │ $0.06`, latency, t/s; `/usage`, `/context`, `/insights` | `calculateCost()` from the model's `cost` (USD per M) incl. cache write; `usage.cost` persisted on transcript entries; `/status`, `/usage off|tokens|full|cost`, `messages.responseUsage` footer with a template (`cost.turn_usd`, `usage.cache_hit_pct`, `timing.duration_ms`); OTel `openclaw.cost.usd` | `usage.cost` read (OpenRouter) → `Agent.spent` and the status card; not on the run, not in `model_finished`; the A10 derived cost printed by `show_run` for every turn |
| telemetry, and what the interface shows | OTel `api_request` event (model, cost_usd, duration_ms, tokens, cache read/creation, ttft-like `speed`), `api_error` (status, attempt, retry_after_ms), metrics; `Ctrl+O` transcript with the model per message | `codex.api_request`, `codex.sse_event` (token counts, ttft_ms, service_tier), `codex.turn_cost`; OTLP or Statsig; `/status` card | the session log is the telemetry: `request/header`, `assistant/message` with a timed stream (TTFT derived), `llm/retry`; JSONL + SQLite index; OTLP logs feedback-only | hooks `post_api_request` (duration, usage, finish_reason), `api_request_error` (status, retry_count); `logs/agent.log` stream diagnostics; OTLP content-free | span `openclaw.model.call` (provider, model, api, transport), metrics `openclaw.tokens`, `openclaw.cost.usd`, `model_call.duration_ms`, `time_to_first_byte_ms`, `gen_ai.*` | SQLite/Postgres `turn_runs` + `trace_events`: `model_finished` (four token counts, finish_reason), no model name, no cost, no attempt; `show_run.py` only; Chainlit shows the status card, nothing of the run |

## 2. What this harness has (the inventory, 2026-09-17)

**One backend, one wire format.** `app/models/openai_compatible.py`
(`OpenAICompatibleBackend`) speaks Chat Completions to any endpoint;
`app/models/base.py` holds the contract (`Message`, `ToolCall`, `Usage`,
`Completion`, `ModelBackend`). A set in `config.toml [model.sets.<name>]`
(`ModelSettings`) carries `endpoint`, `name`, `auth_style`
(`bearer | modal_proxy`), `timeout`, `max_tokens`, `max_images`,
`max_audio`, `temperature`, `retries`, `retry_backoff`,
`chat_template_kwargs`, `extra_body`, `dump_dir`, `providers`, `key_of`;
`ModelBudget.context_tokens` beside it. Nine sets today: `or` (GLM 5.3
Flash at OpenRouter, the default), `gemini`, `comet`, four Modal vLLM Apps
(`int4`, `qwen`, `v2`) and the tune workspace's three Gemma endpoints.

**What runs for every model, whatever the family:**

- `build_messages`: leading system messages joined into one; a system
  message after a user/assistant message delivered as the first text of the
  next user message (Qwen3.8's template refused a mid-thread system message,
  2026-09-05; Gemma 4's takes one anywhere). Runs for GLM at OpenRouter too,
  which accepts system anywhere, so the prompt's layered shape is lost there
  for nothing.
- `readable()` / `unreadable()` / `repaired()`: the `<|"|>` repair for
  Gemma 4's compact tool-call form as served by vLLM (2026-08-31). Runs on
  every completion from every endpoint; harmless but Gemma-shaped.
- `StreamedCompletion.END_MARKERS` (`<eos>`, `<end_of_turn>`, `<|im_end|>`,
  `<|eot_id|>`) stripped from every text delta of every model (a Gemma
  `<eos>` leaked on 2026-09-03). A model that means to write one literally
  loses it.
- `tool_choice` is always `"auto"` when tools are sent; no
  `parallel_tool_calls`; no cache hint of any kind; `temperature` and
  `max_tokens` from the set, never per call; `response_format` accepted by
  the contract and unused.
- Reasoning: a `reasoning`/`reasoning_content` delta is dropped on purpose
  (`StreamedCompletion.add`, "a preview must show what the answer will
  say"); nothing returned is ever sent back, so interleaved thinking across
  a tool loop is impossible; the default set turns thinking off through
  `extra_body = {thinking = {type = "disabled"}, reasoning_effort = "low"}`,
  Qwen through `chat_template_kwargs = {enable_thinking = false}`.
- Provider knowledge is a substring: `OPENROUTER = "openrouter.ai"` in the
  backend (`usage: {include: true}`) and again in `app/agent/status.py`
  (the credits endpoint). `providers` (the router's host slugs, one asked,
  `allow_fallbacks: false`) and `extra_body` are the only per-service
  dials; `auth_style` is the only per-provider shape.
- Retries: `TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}`,
  `retries=2`, backoff `0.5 × 2^attempt`; a timeout is never retried
  (ISS-0044); after the retries the next of `providers` is asked with a
  fresh count. `Retry-After` is not read. A stream that received anything
  is not retried. Context overflow is recognised by status and marker
  words (`is_context_overflow`) and handled by the graph's fold.
- Calibration of chars-per-token from each response's `prompt_tokens`
  (`_calibrate`), `context_limit()` from `/models` `max_model_len` (vLLM
  only; OpenRouter says nothing, so `context_tokens` in the set is the
  ceiling).
- Dumps: `dump_dir` keeps every streamed call's request and raw SSE lines.

**Usage and cost.** `parse_usage` reads `prompt_tokens`,
`completion_tokens`, `prompt_tokens_details.cached_tokens`,
`completion_tokens_details.reasoning_tokens`, `cost`. `Usage.cost` reaches
`Agent.spent` (summed per process, `app/agent/runtime.py`) and the status
card (`session_spend`, `session_spend_exact`; `CreditsWatch` reads
OpenRouter's `/credits` every 30 s as the inexact fallback). It reaches
telemetry nowhere: `ModelCall.done` copies four token counts into
`model_finished` and the run's `input_tokens`/`output_tokens`; `TurnRun`
has no cost column; `trace_events` carry no cost, no model name, no
provider, no set name. The summary call (`app/context/summary.py`,
`backend.invoke`) is bracketed by no `trace.model`, so a fold's tokens,
time and cost are counted in nothing (the run's counts, the status card's
spend, the trajectory file).

**The A10 cost model.** `app/telemetry/cost.py`: `gpu_cost(events)` =
model span + a 12 s idle window at `A10_USD_PER_SECOND = 0.000306`, "an
upper bound, never an invoice", written for the scale-to-zero Gemma App of
2026-08 and printed by `tools/show_run.py` (`render_cost`,
`render_summary` "GPU active per successful turn", `--gpu-rate`,
`--idle-window`) and `tools/prompt_scenarios.py` (`--gpu-rate`);
`tests/test_gpu_cost.py`. Since 2026-09-06 the default set is a hosted
router whose every call says its own price, and the GPU Apps sleep; the
number `show_run` prints for a GLM turn is a fiction about a card that was
not used.

**Telemetry today.** `TurnRun` (run, user, thread, source, status, outcome,
route, model_calls, tool_calls, input/output tokens, first token, first
visible, total, error) and `trace_events` (seq, type, duration, data) in
SQLite locally / Postgres deployed (`app/telemetry/{sqlite,postgres}.py`,
schema by `PRAGMA user_version`). Events per model call: `model_started`
(purpose, call_index), `model_first_token`, `model_finished` (four token
counts, finish_reason), `model_failed` (error_type). Read by
`tools/show_run.py` (one run, a listing, `--summary`), by
`tools/prompt_scenarios.py`, by the MCP server's `runs`/`harness_seconds`.
Chainlit shows the status card (`public/status.js`: context of window, the
mode, the folder, the session's spend, the credits) and, per turn, a step
per tool call; nothing of the run's telemetry (calls, tokens, time to first
token, what ended the turn) is reachable from the interface. Telegram shows
nothing of it either. The trajectory file (`app/trajectories.py`) keeps
every call's prompt and completion with the model name.

**Tests:** `tests/test_openai_compatible.py` (1,154 lines: message
building, the repair, streaming shapes, retries, hosts, usage),
`tests/test_model_settings_chat_template.py`, `tests/test_telemetry_store.py`,
`tests/test_gpu_cost.py`.

## 3. What follows

1. **Every reference describes a model by a profile with named switches,
   and matches the model to it by name.** Codex by the longest slug prefix,
   Hermes by substring tables, OpenClaw and DeepSeek by an explicit
   `compat` block that pi-ai fills from the base URL. What is switched:
   where a system message may stand (`supportsMidConvoSystemMessages`),
   the role for instructions (`supportsDeveloperRole`), the name of the
   output cap (`maxTokensField`), how thinking is asked for
   (`thinkingFormat`: deepseek / zai / qwen / chat-template / openai) and
   whether reasoning must be resent. Nobody switches a tokenizer repair or
   an end marker per family, because nobody has one: the `<|"|>` repair is
   this harness's alone (Gemma on vLLM, 2026-08-31) and stays a repair of
   one served parser; Hermes's `<think>` scrubber is the one universal
   cleaning, applied everywhere.
2. **The provider is a profile too, never a substring of the URL.** Codex,
   Hermes, OpenClaw and DeepSeek each carry: the wire API, the key's
   variable, headers, timeouts, retry policy, and what the service can
   take (`supports_prompt_cache_key`, usage in streaming, a routing
   block). Hermes derives the profile from the host when the set does not
   say; that is the one place a URL is read.
3. **`tool_choice: "auto"` is the whole story; `parallel_tool_calls` is
   Codex's alone** and is a request bool, on by default. Three of five send
   neither field. The API's default already allows parallel calls; the
   field exists to turn them off (or to say so on a proxy that defaults
   otherwise).
4. **Cache hints are per wire API: `cache_control` for Anthropic,
   `prompt_cache_key` for OpenAI, nothing for a custom base URL.**
   OpenClaw sends none to vLLM; Hermes and OpenClaw send Anthropic
   markers through OpenRouter for Claude. For GLM at Novita and Gemini at
   Google through the router the cache is the server's own, keyed by
   prefix, and the harness already measures it (`cached_tokens`). What is
   shaped by the harness is the prefix's stability (roadmap 30's layering),
   not a field.
5. **Reasoning returned by the model is kept on the assistant message and
   sent back inside the tool loop, by everyone.** Codex encrypted, Claude
   signed, DeepSeek/Kimi/Hermes/OpenClaw as `reasoning_content`; DeepSeek
   and Kimi refuse a replay without it. After compaction only the newest
   turn keeps it (Hermes), or it is text (OpenClaw, a different model).
   Dropping it at the parser, as here, makes interleaved thinking
   impossible for every model that has it; it costs nothing while
   thinking is off, which is the default set today.
6. **The effort is a ladder with one spelling, clamped per model**
   (Codex `none…ultra`, Hermes `none…ultra`, OpenClaw `off…max`, DeepSeek
   `off…max`); the profile spells it on the wire. Here it is the raw
   `extra_body` per set.
7. **An auxiliary call names its purpose and may name its model**
   (`purpose: compaction | session-title`, `auxiliary.compression.model`,
   `compaction.model`, the Haiku "small fast model"); thinking is off for
   it; its output is capped by the summary size. Here the summary call is
   the same body as an answer and is bracketed by no trace: its tokens,
   time and cost are counted in nothing.
8. **`Retry-After` is honoured by everyone but Codex's HTTP layer, capped,
   and the backoff is jittered.** Nobody retries more than a handful of
   times on a request; rate limits get more attempts than server errors
   (OpenClaw 10 vs 8, Hermes a cooldown). Nobody retries a timeout that
   may have been accepted, which matches ISS-0044.
9. **Cost is the provider's number where it exists, a price table where it
   does not, and unknown otherwise.** Codex asks the backend after the
   turn; OpenClaw multiplies the catalog's price and persists `usage.cost`
   on every transcript entry; Hermes estimates and drops the provider's
   figure (an open issue); DeepSeek zeroes it. The A10 formula (a span
   plus a 12 s idle window at a rate) has no counterpart anywhere: the
   references never derive a GPU's rent, they read an invoice or a price
   table, and say "estimated" when it is a table.
10. **Every interface shows the run's own numbers**: tokens in/out/cached,
    context % of window, the turn's cost and duration, the session's
    total; Codex and Hermes as a card or bar, OpenClaw as a per-response
    footer (`messages.responseUsage`) and `/usage`, Claude Code as
    `/cost` and the status line. Their per-call record carries the model,
    the provider, the duration, the tokens, the cost and the attempt;
    this harness's `model_finished` carries four counts and a finish
    reason.

## 4. Options

Each is a draft until the human's word. Where one is recommended it is
because it is the smallest shape that gives what the references give.

**A. The model's profile.**
- A1 *(recommended)*: a `family` per set, matched from the model name by
  longest prefix when the set does not name one (`gemma`, `qwen`, `glm`,
  `deepseek`, `kimi`, `gemini`, `openai`, `claude`, `other`), with a table
  in `app/models/families.py` giving the switches: `system_anywhere`
  (else the flattening of `build_messages`), `tool_args_repair`
  (the `<|"|>` repair, Gemma only), `end_markers` (Gemma's two, Qwen's
  `<|im_end|>`, Llama's `<|eot_id|>`, none for a hosted model),
  `reasoning_field` (`reasoning_content` for glm/deepseek/kimi/qwen,
  `reasoning` for OpenRouter's shape, none), `reasoning_required_on_replay`
  (deepseek, kimi), `thinking` spelling (`extra_body` shape for off/low/…:
  zai, deepseek, qwen chat-template, openai `reasoning_effort`). A set may
  override any switch by name (`system_anywhere = true`), so a new model
  needs no code. The `<think>` scrubber (Hermes) applied for every family.
- A2: no table; every switch written per set in `config.toml` (OpenClaw's
  `compat` without the catalog). Explicit, more to write for each set.
- A3: leave as is and only turn the three repairs off for hosted sets by a
  flag. Closes today's case only; rejected by AGENTS.md.

**B. The provider's profile.**
- B1 *(recommended)*: a `provider` per set (`openrouter`, `openai`,
  `cometapi`, `vllm`, `modal` (vLLM behind the proxy), `other`), derived
  from the host when not named, with a table in `app/models/providers.py`:
  auth shape (bearer / modal proxy), `usage.include` for the router's cost,
  the routing block (`provider.order`), the credits endpoint, whether
  `/models` reports `max_model_len`, the cache hint kind (none /
  `prompt_cache_key`), `max_tokens` field name, `stream_options`. Replaces
  `auth_style` and both `"openrouter.ai" in …` substrings. Settings keep
  their names; `auth_style` stays readable as an override.
- B2: keep `auth_style` and add flags one by one. Three substrings become
  five flags; no.

**C. Reasoning passthrough.**
- C1 *(recommended)*: the returned reasoning kept on the assistant
  `Message` (`reasoning: str | None`) inside the turn's checkpoint and sent
  back on the family's field for the rest of that turn's tool loop; dropped
  when the turn ends and from stored history (no schema migration): the
  references keep it "on the newest turn only" after compaction anyway,
  and thinking is off by default here. The trajectory file records it.
  Shown nowhere yet (Chainlit's collapsed step is roadmap 34's).
- C2: stored in history (schema 6), resent on every replay while the model
  is the same, stripped after a fold. Needed only for a model with
  thinking on across turns; DeepSeek/Kimi require the field on *every*
  assistant message, which C1 satisfies with an empty-string pad the way
  Hermes does. Deferred until a thinking set is the default.

**D. `tool_choice`, `parallel_tool_calls`.**
- D1 *(recommended)*: two optional set fields, `tool_choice` (default
  `auto`) and `parallel_tool_calls` (default unsent), sent when the
  provider profile says the service takes them. `parallel_tool_calls =
  true` on the `or` set is then the one-line way to exercise ISS-0079's
  parallel group live.
- D2: nothing. The references' majority; but then ISS-0079 has no handle.

**E. Cache hints.**
- E1 *(recommended)*: only the provider table's `cache_hint` with two
  values built, none and `prompt_cache_key` (= the thread id, as Codex and
  OpenClaw send it); Anthropic `cache_control` breakpoints not built: no
  Claude set exists, and a limit or a mechanism for a model that is not
  there is what AGENTS.md forbids. Measured by `cached_tokens`, already
  read.
- E2: also `cache_control` on the system block and the last message for a
  `claude`/`gemini` family through the router. Built when such a set is
  added.

**F. Per-call overrides.**
- F1 *(recommended)*: a `purpose` on `invoke`/`stream` (`answer`,
  `summary`); the family's table turns thinking off for `summary` and the
  backend caps `max_tokens` at the summary size the limits already derive;
  the summary call bracketed by `trace.model("summary")` so its tokens,
  time and cost land on the run, in `Agent.spent` and in the trajectory.
  This closes the untraced summary regardless of the rest.
- F2: also `summary_set = "<name>"` per set (Hermes's `auxiliary`,
  OpenClaw's `compaction.model`): a cheaper model for folds. One field and
  a second backend instance; recommended only if the human wants a
  cheaper folder now.

**G. `Retry-After`.**
- G1 *(recommended)*: read `Retry-After` (seconds or an HTTP date) on a
  transient status and wait that long when it is within the set's
  `timeout`, else the backoff; the backoff jittered ± 10 % (Codex); a 429
  allowed one more attempt than a 5xx (OpenClaw's shape, derived: `retries
  + 1`). Timeouts stay unretried (ISS-0044).

**H. Cost.**
- H1 *(recommended)*: `usage.cost` stored on the run (`TurnRun.cost_usd`,
  schema bump in SQLite and Postgres with a migration) and in
  `model_finished` (`cost`), beside `model` (the served name), `set`
  and `attempt`; the run's `cost_usd` is `None` when no call said a price.
  A per-set `price = {input, output, cached, reasoning}` in USD per million
  (OpenClaw's `cost`) for a service that reports tokens but no cost
  (CometAPI, the Modal Apps if the human wants a number): the cost is then
  computed and marked `estimated`. `app/telemetry/cost.py`, `render_cost`,
  `--gpu-rate`, `--idle-window`, `tests/test_gpu_cost.py` and the "GPU
  active per successful turn" line deleted; `show_run --summary` says cost
  per successful turn (exact / estimated / unknown), model seconds per
  turn, calls and tokens.
- H2: keep the A10 formula behind `--gpu-rate` for the GPU sets. A number
  no reference derives, for Apps that sleep; no.

**I. Telemetry in the interfaces.**
- I1 *(recommended)*: a `/usage` command in the harness's registry
  (`app/agent/commands.py`, so Chainlit, Telegram and the scripts answer
  the same text): the last turn (model calls, tokens in / out / cached /
  reasoning, model time, time to first token, tool calls, cost, what ended
  it) and the session (calls, tokens, cost exact or estimated, credits
  where the provider says). The status card gets one row, the last turn
  (`calls · tokens · seconds · $`), fed from the run's record the agent
  already holds; `public/status.js` renders it.
- I2: also OpenClaw's per-response footer, off by default
  (`AGENT_RESPONSE_USAGE=tokens|full|off`), appended under every answer in
  Chainlit and Telegram. Cheap once I1 exists; the human's call whether a
  line under every answer is wanted.
- I3: a telemetry page in Chainlit rendering `show_run` for the thread.
  Larger; roadmap 34's Chainlit work is the place.

## 5. What the build would touch

`app/models/families.py` (new), `app/models/providers.py` (new),
`app/models/openai_compatible.py` (the profile applied in
`build_messages`, `readable`, `END_MARKERS`, `_body`, `_completion`/
`stream` retries, reasoning kept and resent, `purpose`),
`app/models/base.py` (`Message.reasoning`, `purpose` on the contract,
`Usage` unchanged), `app/config.py` (`family`, `provider`, `tool_choice`,
`parallel_tool_calls`, `price`, `thinking` ladder; `auth_style` kept as
an override), `config.toml`, `env.example`, `app/context/summary.py`
(`purpose="summary"`, traced), `app/agent/graph.py` (the trace bracket
for the summary; `model` and `set` on `model_finished`),
`app/telemetry/{base,sqlite,postgres,trace,inspect}.py` (`cost_usd`,
schema bump, migration), `app/telemetry/cost.py` deleted,
`tools/show_run.py`, `tools/prompt_scenarios.py`, `app/agent/status.py`
(provider table instead of the substring; the last-turn row),
`app/agent/commands.py` (`/usage`), `ui/chainlit_app.py`,
`public/status.js`, `ui/telegram/api.py` (the menu entry),
`docs/{OPERATIONS_MAP,PROJECT_MAP,CODEMAP}.md`, tests
(`test_openai_compatible`, `test_model_settings_chat_template`,
`test_telemetry_store`, `test_gpu_cost` → deleted, `test_commands`,
`test_profiles`). Deployed: the same code; Telegram gets `/usage` and
the cost on the run; the deploy stays a gate.

## 6. Sources

- Codex: `codex-rs/protocol/src/openai_models.rs`,
  `codex-rs/models-manager/src/{manager,model_info}.rs`, `models.json`,
  `codex-rs/model-provider-info/src/lib.rs`,
  `codex-rs/model-provider/src/models_endpoint.rs`,
  `codex-rs/codex-api/src/{common.rs,api_bridge.rs,rate_limits.rs,sse/responses.rs,requests/headers.rs}`,
  `codex-rs/codex-client/src/retry.rs`, `codex-rs/core/src/{client.rs,compact.rs,responses_retry.rs,context_manager/{history,normalize}.rs}`,
  `codex-rs/protocol/src/{models.rs,protocol.rs,error.rs}`,
  `codex-rs/otel/src/{events/session_telemetry.rs,config.rs}`,
  `codex-rs/tui/src/status/{card,thread_usage,rate_limits}.rs`,
  `codex-rs/app-server/src/turn_cost_worker.rs`.
- Hermes: `hermes_cli/{auth,runtime_provider,config_providers,config_defaults,cli_status_bar_mixin,cli_info_mixin}.py`,
  `providers/base.py`, `agent/{transports/chat_completions,anthropic_adapter,anthropic_message_convert,reasoning_params,reasoning_effort,model_metadata,models_dev,usage_pricing,turn_usage,usage_anchor,message_sanitization,context_compressor,think_scrubber,turn_tool_validation,error_classifier,turn_recovery,retry_utils,fallback_cooldown,turn_empty_response,turn_truncation,turn_overflow,rate_limit_tracker,api_request_hooks,stream_diag,auxiliary_client,prompt_caching,prompt_cache_scope}.py`,
  `hermes_state.py`, `hermes_state_usage.py`, `website/docs/developer-guide/{provider-runtime,adding-providers,prompt-assembly,observer-hooks,middleware}.md`;
  GitHub issues #105215, #109976.
- OpenClaw: docs `/gateway/config-tools/custom-providers`,
  `/concepts/models`, `/reference/token-use`, `/reference/prompt-caching`,
  `/tools/thinking`, `/gateway/config-agents/models`,
  `/concepts/compaction`, `/concepts/model-failover`,
  `/concepts/usage-tracking`, `/concepts/oauth`,
  `/concepts/model-providers/control-ui-and-keys`, `/gateway/local-models`,
  `/providers/{deepseek,minimax,moonshot,zai,ollama,vllm,anthropic}`,
  `/gateway/opentelemetry/{configuration,model-calls-and-metrics}`,
  `/gateway/logging`; pi-ai `packages/ai/src/{types.ts,models.ts,api/openai-completions.ts,api/openai-responses.ts,api/openai-responses-shared.ts,api/anthropic-messages.ts,api/transform-messages.ts,utils/json-parse.ts,utils/provider-retry.ts,utils/retry.ts}`, `packages/ai/README.md`.
- DeepSeek Harness (`github.com/deepseek-ai/deepseek-harness`):
  `packages/llm/llm/src/{types,message,retry-policy,error,assembler,assistant-stream}.ts`,
  `packages/llm/llm-deepseek/src/{config.ts,common/*,protocols/chat-completions/serialize.ts,protocols/messages/{serialize,replay,transport}.ts,translate.ts}`,
  `packages/llm/llm-pi-ai/src/{config,catalog,adapter,replay,stream,provider}.ts`,
  `packages/llm/llm-retry/src/index.ts`, `packages/llm/token-meter/README.md`,
  `packages/core/agent-loop/src/{agent.ts,constants.ts}`,
  `packages/bundle/base/cordis.patch.yml`, `docs/{architecture,persistence-catalog,config-catalog,deepseek-llm-api-wire-extensions}.md`,
  `docs/subsystems/{llm-streaming,compaction,session-telemetry}.md`.
- Claude Code: `code.claude.com/docs/en/{env-vars,model-config,prompt-caching,llm-gateway,llm-gateway-protocol,llm-gateway-connect,amazon-bedrock,google-vertex-ai,microsoft-foundry,network-config,errors,costs,commands,statusline,monitoring-usage,interactive-mode,context-window,sub-agents}`;
  `platform.claude.com/docs/en/{api/messages,api/errors,api/rate-limits,build-with-claude/prompt-caching,build-with-claude/thinking,cli-sdks-libraries/sdks/typescript}`.

## 7. Not found

- Codex: `ModelFamily`, `chat_completions.rs`, `max_output_tokens`,
  `Retry-After` header parsing, any marker stripping or tool-JSON repair,
  a per-token price table, a TUI per-call latency line (the tree was
  re-cut; the old file names are gone).
- Hermes: `tool_choice`/`parallel_tool_calls` in the chat transport (the
  docs page claimed keys the source did not show; unverified), a
  vLLM `enable_thinking` branch, a GLM `thinking` branch, the literal
  OpenRouter headers, `AUXILIARY_*` env vars, `HERMES_*` telemetry vars.
- OpenClaw: `parallel_tool_calls` in any wire file, `<think>` stripping in
  `openai-completions.ts`, OpenRouter `usage.cost` passthrough in pi-ai, a
  per-session cost field name, `/stats`, a reasoning-on-compaction setting.
- DeepSeek: pricing anywhere, `tool_choice`, `<think>` or marker
  stripping, JSON repair, cross-provider failover, a `/cost` or `/status`
  command, pi-ai's `Retry-After` extraction (a partial fetch).
- Claude Code: the default of `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, which
  `tool_choice` it sends, the beta strings it emits, `/stats`, the exact
  retry count (a snippet says 10, capped 15; unverified), the spinner
  string.
