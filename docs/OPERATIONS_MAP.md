# Operations Map

The **current operational surface** of Pinocchio Harness: configuration,
model sets, secrets, deployment, migrations, Telegram mode, storage,
runtime commands and diagnostics. Not a history: `ROADMAP.md` owns work,
`reports/` owns measurements, `AGENTS.md` owns the gates, `DECISIONS.md`
the reasons.

Never put secret values into documents, command strings, reports or chat.
On Windows run the Modal CLI as `.venv\Scripts\python.exe -m modal` with
`PYTHONIOENCODING=utf-8`; a cp1252 console cannot print Modal's closing tick
and the deploy ends with a `charmap` error after it has already succeeded.

## Topology

```text
Telegram
   │
   ▼
assistant-control (Modal, CPU)
   ├─ telegram_webhook          ingress
   ├─ process_telegram_update   the agent worker
   ├─ render_web_page           isolated browser
   ├─ run_command               a command in one workspace, no secret
   ├─ scenarios                 live scenarios inside the worker's environment
   ├─ self_test                 capability probes
   └─ measure_database_latency  diagnostic
          │
          ├──> Neon / PostgreSQL     conversations, facts, checkpoints, inbox, telemetry
          ├──> Volume assistant-workspaces   /workspaces/<user>/…
          └──> the model set named by MODEL:
                 or   OpenRouter, GLM 5.3 Flash at Novita then Z.ai   (default since 2026-09-06)
                 int4 / qwen / v2   the Modal vLLM Apps, scaled to zero, not in use
```

Deploying `assistant-control` never touches a model App.

## Configuration

**Owner:** `app/config.py`; `env.example` documents every name with its
default. Families: `MODEL_*` (`ModelSettings`), `AGENT_*` (`AgentSettings`),
`TELEGRAM_*`, `WEB_*`. `.env` is the local source and is never committed.

### Model sets

`MODEL=<name>` makes the assistant read `MODEL_<NAME>_ENDPOINT`, `_NAME`,
`_API_KEY`, `_AUTH_STYLE`, `_CHAT_TEMPLATE_KWARGS`, `_EXTRA_BODY`,
`_DUMP_DIR` and `AGENT_<NAME>_CONTEXT_TOKENS` instead of the plain lines,
which remain the unnamed set. Every set in `.env` is published with the
secret, so switching the deployment is the `MODEL` line, a publish and a
control-plane redeploy (a warm worker keeps the old values until it sleeps).

The deployed default:

```text
MODEL=or
MODEL_OR_ENDPOINT=https://openrouter.ai/api/v1
MODEL_OR_NAME=z-ai/glm-5.3-flash
MODEL_OR_API_KEY=<key>
MODEL_OR_AUTH_STYLE=bearer
MODEL_OR_EXTRA_BODY={"provider": {"order": ["novita/fp8", "z-ai/fp8"], "allow_fallbacks": false}, "thinking": {"type": "disabled"}, "reasoning_effort": "low"}
MODEL_OR_DUMP_DIR=/workspaces/.dumps
AGENT_OR_CONTEXT_TOKENS=131072
```

- `EXTRA_BODY` is merged into every request body last: provider routing,
  thinking flags, anything the OpenAI shape has no word for. OpenRouter
  provider slugs come from `/api/v1/models/<id>/endpoints`; `allow_fallbacks:
  true` falls to any host. `thinking: {"type": "disabled"}` with
  `reasoning_effort: low` leaves GLM with no reasoning tokens on tool calls;
  Qwen uses `enable_thinking: false` in `CHAT_TEMPLATE_KWARGS` (vLLM's field).
- A hosted service usually reports no context length on `/v1/models`; the
  set's `CONTEXT_TOKENS` is then the budget, and the count fallback
  `AGENT_SUMMARIZE_AFTER` (60) is what triggers folds (ISS-0032 note).
- `DUMP_DIR` keeps every streamed response as one `.sse` file, request body
  first; it holds conversation content, so it lives on the Volume deployed.
- Gemini 3.1 Flash-Lite is a second set on the same endpoint
  (`google/gemini-3.1-flash-lite`, provider `google-ai-studio/flex`), paused
  until its cache lands (roadmap item 13). Price and speed measurements:
  `reports/2026-09-06_hosted_model_cometapi.md`.
- The GPU Apps are sets too: endpoint the App's `.modal.run` URL plus `/v1`,
  `AUTH_STYLE=modal_proxy`, a `wk-…` proxy token; the ceiling is read from
  the server.

### Turn and context settings

`AGENT_TURN_MAX_STEPS` 12, `AGENT_TURN_MAX_TOOL_CALLS` 24,
`AGENT_TURN_MAX_SECONDS` 300 are the only ceiling on an autonomous turn; the
seconds count tool time too (ISS-0057). `AGENT_CONTEXT_FRACTION` 0.8 of a
reported ceiling, or `AGENT_CONTEXT_TOKENS`; `AGENT_KEEP_TURNS` 2;
`AGENT_SUMMARIZE_AFTER` 60. `AGENT_STREAM_ANSWERS` and `AGENT_TELEMETRY` are
on; telemetry holds timings and counts only and can never fail a turn.

## Secrets

**Owner:** `tools/sync_control_secret.py`.

```text
.venv\Scripts\python.exe tools/sync_control_secret.py
```

Reads `.env`, publishes the allow-list only (Telegram, database, every model
set, web keys, `AGENT_CONTEXT_TOKENS`), prints names never values, replaces
the Modal secret `assistant-control` with `--force`. `DEPLOY_WEB_RENDERER_URL`
is published as `WEB_RENDERER_URL` so the local profile never sends its page
views to the deployed renderer. No second sync path, no values typed into the
provider console.

## Database and migrations

**Owner:** `tools/setup_control_plane.py`, run by hand, never on a request.
It creates or migrates the store schema, the LangGraph checkpoint tables,
the `telegram_updates` inbox (`conversation_key`, `control`, `run_id`),
`turn_stops`, and the telemetry tables (`telemetry_version` 1). Every step is
additive. Store schema version is **4** in both implementations (`user_version`
in SQLite, `schema_version` in PostgreSQL): 2 `user_state`, 3 `messages.failure`
and `compactions`, 4 the derived `text` column with a full-text index (FTS5 /
`simple` tsvector + GIN) that `search_history` reads. The deployed database is
at 4. Migrating or resetting a populated database is a human gate; there is no
application path to a reset (`drop_schema` refuses `public`).

## Telegram mode

- **Profile and menu:** `tools/telegram_profile.py` previews; `--publish`
  sends (external mutation, ask first). Menu: `/new /chats /can /agents /plan
  /mode /context /compact /stop /help`; `/check` works but is not listed.
- **Webhook (deployed):** `tools/telegram_webhook.py` shows status, `--url
  <webhook>` registers, `--delete` returns to polling. The webhook is
  `telegram_webhook` in `control_app.py` over `ui/telegram/webhook.py`.
- **Polling (local):** `python -m ui.telegram.run`. Telegram refuses polling
  while a webhook is registered.

## Deploying `assistant-control`

**Owner:** `deploy/modal/control_app.py`.

```text
.venv\Scripts\python.exe -m modal deploy deploy/modal/control_app.py
```

Images: `control_image` (no browser, the webhook), `agent_image` (Chromium,
the worker), `render_image` (Chromium, the renderer), `command_image`
(`BASE_TOOLS`: fonts, node, git, ffmpeg, imagemagick, poppler, pandoc;
`BASE_PACKAGES`: reportlab, fpdf2, python-docx, openpyxl, pandas, matplotlib,
Pillow, pypdf, markdown). Heavy layers sit below the copied source.

| Function | CPU / memory | max | scaledown | timeout | notes |
|---|---|---|---|---|---|
| `telegram_webhook` | 0.25 / 512 MiB | 20 | 60 s | 30 s | validates, queues, wakes the model when the set is a GPU App, spawns |
| `process_telegram_update` | 1 / 2 GiB | 8 | 60 s | 600 s | secrets, the Volume; `retries=1`; lease 590 s; drains 240 s then hands over |
| `render_web_page` | 1 / 2 GiB | 4 | 20 s | 180 s | no secret, no database, no Volume; proxy auth |
| `run_command` | 1 / 2 GiB | 8 | 180 s | 660 s | no secret; the Volume; `HOME` and cwd in the workspace (ISS-0053, ISS-0058) |
| `scenarios` | 1 / 2 GiB | 1 | 2 s | 1800 s | `loop_live --deployed`, probe user `loop-live-check` |
| `self_test` | 0.25 / 512 MiB | 1 | 2 s | 300 s | `include_model`, `include_credit` opt in |

A deploy is not an invocation; an invocation starts a container (a gate,
`AGENTS.md`). **After every deploy:** `/check` in Telegram (free), and on
the human's word the after-deploy scenarios:

```text
.venv\Scripts\python.exe -m scripts.loop_live --deployed --after-deploy
```

(A, B and G; each turn is a paid model call and a worker.)

## Model Apps (not in use since 2026-09-06)

**Owners:** `deploy/modal/model_app.py` (`assistant-llm-v2`: Gemma 4 12B
QAT, A10, vLLM 0.26.0, ceiling 65,536, utilization 0.80, image=4 audio=1,
snapshot around a slept vLLM, proxy auth), `model_app_qwen.py`
(`assistant-llm-qwen`: Qwen3.8-27B FP8, L40S, 131,072, 0.90, `max_num_seqs`
16, vLLM 0.28.0 / transformers 5.15.0, prefix caching on, thinking off by
default, `qwen3_xml`/`qwen3` parsers), `model_app_qwen_int4.py`
(`assistant-llm-qwen-int4`: RedHatAI INT4, A100-40GB, the rest by import;
restore 20–31 s). Volumes `assistant-hf-cache` (weights) and
`assistant-vllm-cache` (compile cache; the Qwen Apps do not mount it on the
server, ISS-0047; `VLLM_USE_AOT_COMPILE=0`, ISS-0050).

The order for a Qwen App, each step a gate: `fetch_weights` (CPU) →
`preflight` (CPU, `fits` checks the ceiling against the pool) → optional
`dry_run` (one GPU boot, `retries=0`, ISS-0049) → `modal deploy` → the first
request creates the snapshot. `MAX_MODEL_LEN` is set once, high; the dial is
the application's context budget. `deploy/modal/autoscale.py --window N`
changes the running idle window (12 s default) without a deploy; a deploy
resets it. Wake measurement: `scripts/measure_endpoint_wake.py --url … --model
<served name>`. Engine baseline: `tools/vllm_baseline.py --run`. All of these
start a GPU container.

## Storage

```text
local     AGENT_DATABASE (SQLite)  AGENT_CHECKPOINTS  AGENT_TELEMETRY_DATABASE  AGENT_WORKSPACE
deployed  Neon: store, checkpoints, inbox, turn_stops, turn_runs/trace_events
          Volume assistant-workspaces: /workspaces/<user>/ (files, .agent/ switches, AGENTS.md, .dumps)
          Volumes assistant-hf-cache, assistant-vllm-cache (GPU Apps)
```

A worker may disappear between turns; durable product state is only in these.

## Web

`search_web` → Firecrawl (`WEB_FIRECRAWL_API_KEY`; empty means no search
tool); `fetch_page` → direct HTTP from the worker (`WEB_MAX_BYTES`,
`WEB_FETCH_TIMEOUT`, `WEB_FALLBACK_USER_AGENT` for sites that demand a named
client); `view_web_page` → the local browser (`WEB_LOCAL_BROWSER`, 0 in the
deployed image) or `WEB_RENDERER_URL` + `WEB_RENDERER_KEY`. With no renderer
configured the deployed worker refuses rather than rendering beside secrets.

## Runtime commands (Telegram)

| Command | What it does | Owner | Model call |
|---|---|---|---|
| `/can` | what is wired, from the runtime | `app/capabilities.py` | no |
| `/check` | real capability probes | `app/preflight.py`, `Agent.selftest` | no (GPU probe opt in) |
| `/plan [on\|off]` | the task list; marker `.agent/plan.on` | `app/agent/todo.py` | no |
| `/mode [full\|careful]` | ask before workspace changes; marker `.agent/careful.on` | `app/agent/mode.py` | no |
| `/context [small\|normal\|large]` | what the next request is made of, cached tokens, the chosen size (25% / fraction / 95%); marker `.agent/context` | `app/context/choice.py`, `Agent.context_report` | no |
| `/compact` | fold now | `Agent.compact` | yes, one summarizer call |
| `/agents [set\|clear]` | standing instructions, `AGENTS.md` in the workspace | `app/instructions.py` | no |
| `/new`, `/chats` | conversation choice, stored | `ui/telegram/adapter.py` | no |
| `/stop` | out of band; the loop reads it at its next step | `app/agent/stop.py` | no |

A fold during a turn is announced after the answer: how many messages, that
the last two exchanges stay verbatim, that `search_history` reaches the rest.

## Reading what a turn cost

```bash
python tools/show_run.py <run_id>
```

`--last N`, `--failed`, `--user <id>`, `--summary`. Reads the local SQLite
by default, the deployed database when `AGENT_DATABASE_URL` is set; read-only.
A run shows queue wait, first token, first visible response, model calls with
tokens (`cached`, `reasoning`), tool calls, loop steps and what ended the turn,
the full timeline with its gaps (ISS-0056), and totals. The **GPU section**
(active seconds, derived cost from `IDLE_WINDOW_SECONDS` and a GPU rate) is
meaningful only for a GPU App; on a hosted model the provider's own
`usage.cost` per call is the price, and the dumps carry it. A turn that ends
without an outcome is closed `failed/incomplete`; one whose container vanished
stays `running`, which `--failed` lists.

## Other tools

- `tools/prompt_scenarios.py --dry-run` composes the brief and contacts
  nothing; without it every scenario is a paid run. `--goal off`,
  `--prompt-file`; output under `reports/prompt_runs/`.
- `tools/showcase.py` renders a stored conversation with its media, read-only.
- `scripts/loop_live.py` runs scenarios locally (`A … S`) or deployed.
- `scripts/measure_command_cold_start.py` starts a command container.
- `scripts/doctor.py` diagnoses a local install. `scripts/smoke_test.py`,
  `stage3_live.py`, `v1_live.py` are earlier-stage runners.
- `tools/work_log.py` appends to `reports/agent_tasks.jsonl` and
  `reports/ml_work.jsonl`; never hand-edit them.

## Invariants

- `.env` is the source; publish the allow-list, never the file.
- No secret value in a report, a command string or a chat.
- No migration on a request path; no reset without the gate.
- A deploy is not an invocation; an invocation may bill.
- Page JavaScript never runs beside control-plane secrets deployed.
- The application and any model server share only the OpenAI-compatible
  endpoint named by the chosen set.
- Local and deployed differ behind settings, stores and adapters, never by
  branching `app/`.
- Check `tools/`, `scripts/` and `deploy/` before adding an operational
  helper.
