# Project Map

The conceptual map of the **current implementation** of Pinocchio Harness:
components, ownership and runtime flows. Not a roadmap or a history.
`docs/CODEMAP.md` locates code, `docs/OPERATIONS_MAP.md` has configuration and
commands, `ROADMAP.md` has current work, `DECISIONS.md` the reasons behind the
boundaries below, `reports/` the evidence.

## System at a glance

```text
                     ┌──────────────────────────────────────┐
                     │ Model: any OpenAI-compatible endpoint │
                     │ default GLM 5.3 Flash via OpenRouter  │
                     │ or a Modal vLLM App (Gemma 4, Qwen3.8)│
                     │ chosen by MODEL=<set>                 │
                     └──────────────────┬────────────────────┘
                                        │ ModelBackend
Telegram ─┐                             ▼
          ├─> interface adapter ─────> Agent ── one loop, TurnWatch, StopRequests
Chainlit ─┘                             │
                                        ├─ Context engine (surface, fold, history tools)
                                        ├─ ConversationStore (SQLite | PostgreSQL)
                                        ├─ LangGraph checkpoints
                                        ├─ CapabilityRegistry / Toolbox / ToolExecutor
                                        └─ Telemetry (run_id, trace)
                                                 │
         ┌──────────────┬──────────────┬─────────┴──────┬───────────────┐
         ▼              ▼              ▼                ▼               ▼
     workspace      run_command    public web        memory         send_file
  files/docs/pages  a shell in the  search / fetch /  facts, history  explicit
                    workspace       isolated renderer                  presentation
```

`app/` is the application, `ui/` the interfaces, `deploy/modal/` the
deployment, `tools/` and `scripts/` the operational surface.

## Model boundary

`app/models/base.py` owns the domain types (`ContentPart`, `ToolCall`,
`Message`, `Usage`, `Completion`, `ModelBackend`); application code never
speaks provider or LangChain types. `app/models/openai_compatible.py` is the
one wire adapter: it builds the request, streams the completion, reads usage
(including cached and reasoning tokens), merges `extra_body` last, sends
`chat_template_kwargs` when set, follows the Modal edge's redirects, never
retries a timeout, and can dump every raw stream (`dump_dir`).

Which endpoint is a **model set**: `MODEL=<name>` makes `ModelSettings` read
`MODEL_<NAME>_*` and `AgentSettings.context_tokens` read
`AGENT_<NAME>_CONTEXT_TOKENS`; plain `MODEL_*` is the unnamed set. The
deployment runs the `or` set (GLM 5.3 Flash through OpenRouter, Novita then
Z.ai, thinking off); the GPU Apps are sets that sleep. The context ceiling is
read from `/v1/models` when the server reports one; a hosted service usually
does not, and then the set's own budget stands (`Agent.budget`).

## Agent runtime

`app/agent/graph.py` is the one loop:

```text
load context ─> model ─> tools ─> model ─> … ─> persist
                  ▲        │
                  └────────┘
   before each batch of tools: asked to stop?
   after a batch, once every check_seconds of work: "on track? what is left?"
```

- `Agent` (`app/agent/runtime.py`) wires backend, store, context policy,
  workspace, grant, checkpointer and telemetry for one user; a graph is
  compiled per thread.
- `TurnWatch`: no ceiling on steps, calls or seconds. After
  `check_seconds` (360 in `config.toml`) of work the tools node follows its results with one
  turn-control question (progress, what is left; continue or finish); the
  model's next completion answers and decides; asked again after each
  further interval. Time is accumulated by the nodes, so a wait for approval
  does not count. LangGraph's recursion limit (1000) is the guard against a
  loop that never ends, not a ceiling.
- `StopRequests` (`app/agent/stop.py`): memory locally, `turn_stops` deployed.
  A stop carries the sequence its update arrived with and applies only to
  turns begun before it.
- `Interjections` (`app/agent/interjections.py`): a message the person sends
  while a turn runs is taken at the tools boundary, after the batch's results,
  as their own words (`role: user`, unframed), and stored there; the
  interface sees it as `MessageTaken`, never as an answer. Memory locally
  (the polling door offers before it waits), the inbox itself deployed
  (`pending` → `done` in one statement, `ui/telegram/interjections.py`).
  A message during the final answer is the next turn, as before.
- `TurnStopping` (`app/agent/stopping.py`) is asked only for a result that
  would end the turn; the default stops; explicit `Steering` from an extension
  runs one more step. The one wired extension, the todo list's, objects to
  nothing by default.
- Repeat guards: an identical failed call is refused the third time; an
  identical successful call the third time too, both counts restarting when
  something changed in between (ISS-0013, ISS-0042).
- A tool failure is a result the model reads (`Message.failure` typed, the
  `error:` text is wording, not protocol). Only `BaseException` propagates.
- A turn a worker died in is taken up from its checkpoint: `replay_safe`
  tools of the dying step run again, the rest are answered `interrupted`;
  `persist` is idempotent (DECISIONS 2026-09-04).

Two things the model may write down for one turn, both living in the
arguments of its own last call inside the turn's messages and cleared by the
next user message: the **goal** (`set_goal`, offered always, the request's
parts written once) and the **plan** (`todo_write`, offered only with
`/plan on`). Nothing in the loop reads either back.

## Context and memory

Before every model step (`app/context/window.py`, `fitted` in the graph):

1. the prompt is assembled by stability: core (names no tool, ends with
   `WORKING_METHOD`), the capability brief generated from the wired toolbox
   (`app/capabilities.py`), tool schemas, the person's `AGENTS.md`
   (`app/instructions.py`), the rolling summary, retrieved facts, history,
   the turn;
2. the surface is shortened by age: tool results older than the newest two
   become stubs naming tool, subject, size and stored position; failures,
   short results, the model's own text and arguments, and the whole current
   turn stay verbatim; pictures share one media budget;
3. the request is estimated (`ModelBackend.estimate_tokens`, calibrated from
   reported usage) and folded before it is sent when over budget, the oldest
   exchanges one at a time, the last `keep_turns` (2) exchanges always
   verbatim. Nothing folds by message count (2026-09-07): only a request
   that would not fit, or `/compact`. The summarizer reads the same stubs; a
   failed fold leaves the turn as it is.

Stored history is canonical and never rewritten. The model gets back to it
with `search_history` (full text, this person only) and `read_history` (by
position, paged); `read_file`, `fetch_page` and `read_history` take an
`offset`. Long-term facts are saved only by `remember_fact` and reached by
`search_memory` plus a per-turn keyword layer (ISS-0028 records its silence
when nothing matches).

`ConversationStore` (`app/memory/base.py`): threads by owner, messages,
summaries, facts, per-turn context records, the person's current thread,
full-text search. `SqliteStore` locally, `PostgresStore` (Neon) deployed,
schema version 4, one contract suite. Checkpoints (`app/checkpoints.py`) are
in-flight graph state only. Telemetry (`app/telemetry/`) is a third store:
`turn_runs` and `trace_events` per `run_id`, no message text.

## Capability and tool system

`app/tools/base.py`: `Tool` (name, description, schema, `requires_approval`,
`mutates`, `replay_safe`, `delivers`, `timeout_seconds`), `ToolError(code)`,
`Toolbox` (allowlist, coercion, the signature a refused call gets, the
`handover` phrase every tool that leaves a workspace item appends).
`app/tools/execution.py`: `pre_execute -> execute -> post_execute`, the one
path for every call: resolve, coerce, validate, approval policy, timeout,
typed failure, bounds, sanitizing, telemetry, projection. An unreadable call
is refused once as `bad_arguments`; the request is never failed.
`docs/v2_tool_system.md` is the contract.

Capabilities (`app/tools/capabilities.py`) and their tools:

```text
filesystem.read / write   list_files, read_file, write_file, edit_file   (app/tools/filesystem.py)
shell.run                 run_command                                    (app/tools/shell.py)
documents.read            read_document, view_pages                      (app/tools/documents.py)
browser.page              use_page                                       (app/tools/browser.py)
web.search / fetch / view search_web, fetch_page, view_web_page          (app/tools/web.py)
presentation.files        send_file                                      (app/tools/presentation.py)
always                    remember_fact, search_memory, search_history, read_history, set_goal
with /plan on             todo_write
```

- **Filesystem:** every path resolves through `resolve_in_root`; a path
  carrying parser leftovers is refused; a trailing separator is refused with
  the reason.
- **Commands:** `run_command` over a one-method `Runner` the profile
  chooses: locally a process in the workspace with the agent's environment
  withheld, on Windows under a write-restricted token; deployed the
  `run_command` Modal Function beside the renderer, no secret, the
  workspaces Volume committed and reloaded on both sides of the call. Three
  places, on purpose: the workspace is the working directory and the work;
  home is the person's (the container's, deployed); temp is the runner's
  own, never the workspace. Nothing is activated or made for the model: a
  venv is the model's, in the task's folder, by name; the brief carries the
  one rule about a folder per piece of work. A container is disposable and
  the brief says so once (roadmap 17, 2026-09-08).
- **Modes:** `full` (default) runs everything inside the workspace without
  asking; `careful` makes `write_file`, `edit_file` and `run_command` ask
  (`app/agent/mode.py`, `Toolbox.ask_for_changes`).
- **Documents:** `app/attachments.py` admits uploads (image/audio become
  model parts, any other file is saved under the workspace's `inbox/`);
  `app/documents.py`
  parses and renders; `read_document` returns bounded sections, `view_pages`
  renders PDF pages to PNG under `.agent/documents/`.
- **Browser:** `app/tools/chromium.py` owns the process, the CDP session and
  `BrowserSession` with the full operation set (`open`, `snapshot` with refs,
  `screenshot`, `evaluate`, `console`, `navigate`, `click`, `type`, `press`,
  `select`; a stale ref is refused). `use_page` (`app/tools/browser.py`) is
  the one tool on it: `action` open / snapshot / click / type / press /
  select / evaluate / screenshot / console, one call per action. `open` takes
  a workspace HTML file (served at `http://artifact.local/` with its sibling
  files) or a public URL, both under the public request policy; every action
  returns the title, console errors since the last call, the structure with
  refs and the visible text. The page lives in `Pages`, held by the
  `CapabilityRegistry` so a turn's calls find it whichever toolbox they come
  through, and is closed by the next open or after `IDLE_SECONDS`. Every
  tool's description is rendered from three fields, `description`, `returns`
  and `leaves` (`Tool.contract`; `tests/test_tool_contracts.py` refuses a
  wired tool missing one).
- **Public web:** `search_web` (Firecrawl leads), `fetch_page` (bounded HTTP,
  no JavaScript), `view_web_page` (a real browser: locally in-process, deployed
  in the isolated `render_web_page` Function). `app/web.py` owns destination
  checks, redirects, size and time limits.
- **Presentation:** `send_file` marks parts `outbound`; adapters transport
  only those, several of one kind as one album. Observation never delivers
  (DECISIONS 2026-08-29).

## Interfaces

**Telegram** (`ui/telegram/`): `wire.py` parses raw updates with the standard
library only, names the canonical user and the conversation key, and marks
control updates (`/stop`, storage-answered commands) as out of band;
`adapter.py` maps identity, finds the thread, admits uploads, dispatches
`/new /chats /can /agents /plan /mode /context /compact /stop /check /help`,
runs the turn with the update id as its sequence, streams the answer as one
edited message, shows tool activity and the plan, asks the consent question,
transports outbound media and announces a fold; `api.py` is the Bot API with
rate-limit handling; `markdown.py` renders Markdown to Telegram HTML with a
plain fallback. Locally `run.py` long-polls with per-chat locks; deployed
`webhook.py` validates, writes the update to the Postgres inbox
(`inbox.py`, leased per conversation, deduplicated by `update_id`), spawns a
CPU worker and returns 200. The worker container lives up to
`WORKER_TIMEOUT_SECONDS` (four hours, a guard; the turn is bounded by its
health check) and drains its conversation for `DRAIN_SECONDS` (that less an
hour) before handing the rest to a fresh one; the conversation lease is
`LEASE_SECONDS` (60) and the worker extends it every `HEARTBEAT_SECONDS`
(20) while answering, so a dead worker frees its conversation within a
minute; every queued update starts a worker, and one that finds the
conversation held waits out a lease before taking it up; an update claimed
three times is given up on with a message. What the checkpoint holds for
the same update id was delivered before a death and is not sent again
(`Agent.delivered_before`).

**Chainlit** (`ui/chainlit_app.py`, `ui/chainlit_history.py`): the same
`Agent`, the same outbound rule, a stop button that records a `StopRequests`
entry. Uploads go through `load_attachments()` (media only), not the
Telegram document path; no `/mode` or `/plan` (roadmap item 7).

## Deployment

**`assistant-control`** (`deploy/modal/control_app.py`), CPU, images layered
with dependencies and Chromium below the source:

```text
telegram_webhook          ingress: validate, queue, wake the model if needed, spawn
process_telegram_update   the worker: secrets, workspaces Volume, the harness
render_web_page           isolated browser: no secret, no database, no workspace; proxy auth
run_command               a command in one workspace: no secret, the Volume, 180 s scaledown
scenarios                 the live scenarios inside the worker's environment
self_test                 capability probes in the deployed environment
measure_database_latency  diagnostic
```

The workspaces are the Volume `assistant-workspaces`, one directory per
canonical user. Secrets are the `assistant-control` secret, published from
the owner's `.env` by `tools/sync_control_secret.py`.

**Model Apps** (`deploy/modal/model_app.py`, `model_app_qwen.py`,
`model_app_qwen_int4.py`): `assistant-llm-v2` (Gemma 4 12B, A10, 65k),
`assistant-llm-qwen` (Qwen3.8-27B FP8, L40S, 128k), `assistant-llm-qwen-int4`
(A100-40GB, 128k). Each is a vLLM server behind proxy auth with CPU+GPU
snapshots and scale-to-zero, sharing the HF-cache Volume. None is in use
since 2026-09-06; they remain sets the assistant can be pointed back at. The
application never imports them.

## State ownership

| State | Owner | Durable |
|---|---|---|
| messages, summaries, facts, current thread | `ConversationStore` | yes |
| in-flight turn | LangGraph checkpointer | resumable |
| stop requests | `StopRequests` (memory / `turn_stops`) | yes |
| messages sent mid-turn | `Interjections` (memory / the inbox's pending rows) | yes |
| accepted Telegram updates, leases, `run_id` | Postgres inbox / polling loop | yes |
| turn runs and traces | `TelemetryStore` | yes |
| user files, previews, screenshots, `AGENTS.md`, `.agent/` switches | workspace dir / Volume | yes |
| model weights, compile caches | Modal Volumes | yes |
| raw model streams (`MODEL_DUMP_DIR`) | a directory, on the Volume deployed | evidence only |

## Trust boundaries

- **User scope:** every store operation and every workspace path is rooted by
  canonical user id.
- **Workspace:** path tools cannot leave the granted root; on Windows a
  command cannot write outside it; deployed, the command container holds no
  secret.
- **Consequence:** `requires_approval` pauses the graph through a durable
  interrupt; work inside the workspace and presentation to the same person
  are autonomous.
- **Public web:** destinations are checked before connection and on
  redirect; page JavaScript runs in the isolated renderer deployed.
- **Untrusted input:** model output, tool output, documents and web content
  are data and carry no instructions.

## Architectural references

DeepSeek Harness (harness architecture, one loop, typed session events),
OpenCode (headless agent core, permissions, subagents) and OpenClaw
(personal assistant: channels, persistent workspaces, browser control,
control plane) are references for the classes of problem this project meets,
not specifications. A pattern is borrowed when the same problem arrives here,
never because another harness has the feature.
