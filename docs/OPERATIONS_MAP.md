# Operations Map

This document maps the **current operational surface** of Pinocchio Harness (`pinocchio-harness`): configuration, deployment, secrets, migrations, Telegram mode, runtime controls, storage and diagnostics.

It is a navigation/ownership document, not a deployment history. `ROADMAP.md` owns current work; `reports/` owns measurements and acceptance evidence; `AGENTS.md` owns permission and execution rules for coding agents.
`DECISIONS.md` explains approved durable operational choices. Read the relevant
entry when changing such a choice; this map remains the source for the current
operational surface and commands.

Never put secret values into repository documents, command examples, reports or chat output.

## Operational topology

```text
                         Modal

Telegram
   │
   ▼
assistant-control
   ├─ telegram_webhook          small CPU ingress
   ├─ process_telegram_update   CPU agent worker
   ├─ render_web_page           isolated CPU browser
   ├─ self_test                 runtime capability diagnostic
   └─ measure_database_latency  DB diagnostic
          │
          ├────────────> Neon/PostgreSQL
          │               conversations / facts / checkpoints / inbox
          │
          ├────────────> Modal Volume: assistant-workspaces
          │               per-user workspace directories
          │
          └────────────> assistant-llm-v2        or   assistant-llm-qwen
                           A10 / vLLM / scale-to-zero    L40S / vLLM / scale-to-zero
                           HF cache + vLLM cache Volumes (shared by both)
```

The CPU control plane and GPU model are separately deployed apps. Deploying `assistant-control` does not redeploy the model app.

## Configuration ownership

### Application settings

**Owner:** `app/config.py`

All normal application environment configuration belongs to one of these settings classes:

| Prefix | Class | Purpose |
|---|---|---|
| `MODEL_` | `ModelSettings` | model endpoint, model name, auth, timeouts/generation |
| `AGENT_` | `AgentSettings` | DB/store, checkpoints, workspace, context policy, answer streaming, turn telemetry |
| `TELEGRAM_` | `TelegramSettings` | Bot API, webhook secret, access policy |
| `WEB_` | `WebSettings` | search, direct fetch, renderer/browser configuration |

Local example/defaults: `env.example`.

`AGENT_STREAM_ANSWERS` is on by default: the conversational model call is
streamed, and Telegram shows the answer in one message while it is written.
Setting it to `false` and redeploying returns the turn to a single complete
request, without reverting code. It changes what is shown, never what is stored:
only finished messages reach the store either way.

`AGENT_TELEMETRY` is on by default: every turn that reaches the model gets one
`run_id` at ingress, a `turn_runs` row and an ordered `trace_events` trace.
Setting it to `false` and redeploying leaves every code path in place and
records nothing. `AGENT_TELEMETRY_DATABASE` is the local profile's own SQLite
file for that record; the deployed profile uses `AGENT_DATABASE_URL` with tables
of its own. Telemetry holds timings, counts and technical metadata only — never
message text, attachments, prompts, tool results or streamed deltas — and it can
never fail a turn: every recorder call swallows its own errors.

`env.example` documents both, along with `AGENT_STREAM_ANSWERS`, since
2026-08-30. It was named `.env.example` until then, which the agent permission
rule denying `.env.*` also caught, so sessions that added settings could not
edit it; the file was renamed rather than the rule weakened.

Application code should not invent a second environment-loading path when the value belongs in one of these classes.

## Local configuration and deployed secret

### Local source

The project's local configuration is `.env` (not committed). `env.example` documents names and safe defaults/placeholders.

### Deployed control-plane secret synchronization

**Existing owner: `tools/sync_control_secret.py`.**

Run from repository root:

```text
.venv\Scripts\python.exe tools/sync_control_secret.py
```

The script:

- reads local `.env`;
- publishes only an explicit allow-list;
- prints key names, never values;
- creates/replaces Modal Secret `assistant-control` using `--force`;
- intentionally does not copy the whole `.env`;
- supports source-name -> deployed-name translation.

Current allow-listed families include Telegram credentials/access configuration, deployed database settings, model endpoint/auth settings, and web search/renderer settings.

Important translation:

```text
local .env name:      DEPLOY_WEB_RENDERER_URL
published env name:   WEB_RENDERER_URL
```

This prevents the local profile from accidentally sending every `view_web_page` call to the deployed renderer.

**Do not create a new secret-sync script or type values manually into the provider as a new source of truth before inspecting this owner.**

## PostgreSQL/control-plane setup

**Owner:** `tools/setup_control_plane.py`.

Purpose: explicit trusted migration/setup for the deployed control plane.

It prepares:

```text
ConversationStore/PostgresStore schema
LangGraph PostgreSQL checkpoint tables
Telegram `telegram_updates` inbox table
`turn_stops`: where a request to stop a running turn is recorded
Telemetry `turn_runs` and `trace_events` tables
```

Every step is additive against a populated database. The telemetry tables are
new, and the inbox gains a nullable `run_id` column whose existing rows stay
valid as updates that were never measured. Telemetry keeps its own version row
(`telemetry_version`, currently **1**) rather than sharing the store's.

The inbox also gains a nullable `conversation_key` column and an index over
`(conversation_key, state, update_id)`. That column is what makes a lease belong
to a conversation instead of to one update, so **a deployment that skips this
step keeps answering a person's messages out of order**: rows without the key
are claimed one at a time, exactly as before. Nothing is rewritten and no row is
dropped.

A worker is asked for only when the conversation is not already being worked on:
`enqueue` suppresses the spawn while another row of the same conversation is
`running` with a live lease, because such a worker would claim nothing and exit.
The row is queued either way and the worker holding the lease drains it, so this
changes what is started, never what is answered. Control updates are exempt.
`reports/2026-08-30_v2_album_burst_incident.md`.

It then gains a `control BOOLEAN NOT NULL DEFAULT FALSE` column, which is the
out-of-band lane: a control update is claimed on its own and is never what a
conversation's lease takes. **A deployment that skips this step goes back to
`/stop` waiting behind the turn it exists to stop**, and to `/chats` waiting
behind a turn that may run for minutes. The default is what makes every existing row mean what it already
meant. `turn_stops` is a new table, one row per conversation, holding the
sequence number the last stop arrived with; without it a deployed `/stop` is
delivered promptly and then acts on nothing, because the container running the
turn has no other way to hear about it.

The normal runtime intentionally does not run these migrations on each request.

Store schema version is **4** in both implementations (`PRAGMA user_version` for
SQLite, the `schema_version` row for PostgreSQL). Version 2 adds the `user_state`
table, which records which conversation each person is in. Version 3 adds the
`failure` column on messages (the tool system's typed outcome, empty for every
row written before it) and the `compactions` table (one row per fold: the
position the summary came to cover, how many messages it took in, and why).
Version 4 adds the `text` column on messages — the message's words, derived
from what the row already holds — and a full-text index over it (FTS5 locally,
a `simple` tsvector with a GIN index on PostgreSQL), which `search_history`
reads. Every step is additive and no conversation is touched; version 4 is the
one step that writes to every existing message row, and what it writes is
derived from that row. Running a migration on the populated deployed database
is still the human gate for migrating one. **Deployed state:** Neon is at
version 3; the migration to 4 has not been run.

Resetting a store is a separate destructive operation and stays behind the human
gate for deleting or migrating a populated database. There is no application
path to it: `PostgresStore.drop_schema` refuses `public`, and the local file is
deleted by hand. Nothing about the local or deployed store is reset from a
worker starting up.

Primary configuration comes from `AgentSettings` / `AGENT_DATABASE_URL`.

What a turn may spend before it stops and says so is configuration too:
`AGENT_TURN_MAX_STEPS` (12), `AGENT_TURN_MAX_TOOL_CALLS` (24) and
`AGENT_TURN_MAX_SECONDS` (300). They are the only ceiling on an autonomous
turn, and the right values differ between a personal machine, where the GPU is
already paid for, and a deployment where every second is billed. Listed
commented-out in `env.example` with their defaults.

The script also has an `--alternate` path for the configured alternate database used by latency comparison work.

## Telegram operating mode

Telegram supports two transport modes that are mutually exclusive from Telegram's perspective.

### Bot profile and native command menu

**Owner:** `tools/telegram_profile.py`.

Preview the intended description and `/new`, `/chats`, `/can`, `/agents`,
`/stop`, `/help` menu without contacting Telegram:

```text
.venv\Scripts\python.exe tools/telegram_profile.py
```

Publish them only after the human authorizes the external mutation:

```text
.venv\Scripts\python.exe tools/telegram_profile.py --publish
```

The tool reads `TELEGRAM_TOKEN` from settings. `/check` remains a working typed
diagnostic but is intentionally absent from the native product menu.

### Deployed webhook

**Registration/status owner:** `tools/telegram_webhook.py`.

Examples from repository root:

```text
# inspect current webhook status
.venv\Scripts\python.exe tools/telegram_webhook.py

# point Telegram at a deployed webhook
.venv\Scripts\python.exe tools/telegram_webhook.py --url <deployed-webhook-url>

# remove webhook and return to polling
.venv\Scripts\python.exe tools/telegram_webhook.py --delete
```

The tool reads `TELEGRAM_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` from settings and does not print the secret.

The webhook itself is `telegram_webhook` in `deploy/modal/control_app.py` and delegates to the transport-neutral core in `ui/telegram/webhook.py`.

### Local polling

Entry point:

```text
python -m ui.telegram.run
```

`ui/telegram/run.py` uses the same `TelegramAdapter` as deployment. It serializes updates from the same chat locally and allows different chats to run concurrently.

If a Telegram webhook is registered, Telegram refuses normal `getUpdates` polling until the webhook is deleted.

## Modal control-plane deployment

**Owner:** `deploy/modal/control_app.py`.

On Windows, deploy with `PYTHONIOENCODING=utf-8`. Modal prints a tick character
when it finishes, a cp1252 console cannot encode it, and the deploy ends with
`'charmap' codec can't encode character '✓'` after every image has already
been built — which reads like a failed deployment and is not one.

App name:

```text
assistant-control
```

**After every deploy of `assistant-control`** (rule since 2026-09-03): `/check`
in Telegram, which is free, and the live check below, which wakes the GPU and
needs the human's permission for that run:

```text
.venv\Scripts\python.exe -m scripts.loop_live --deployed --after-deploy
```

It runs two quick answers (A, B) so a change to the loop has not broken the
simple case, then the person's own Task Board request (G) with the checks
that caught every defect of 2026-09-03: files and screenshot sent, one
answer, at most five writes, the turn ended before its ceiling.

The file defines three image shapes:

```text
control_image  -> dependencies + source; no Chromium
agent_image    -> Chromium/fonts + source; deployed agent worker
render_image   -> Chromium/fonts + source; isolated page renderer
command_image  -> dependencies + BASE_TOOLS (DejaVu/Liberation fonts, node, git, ffmpeg, imagemagick, poppler, pandoc, ...) + BASE_PACKAGES (reportlab, fpdf2, python-docx, openpyxl, pandas, matplotlib, Pillow, pypdf, markdown) + source; where a command runs
```

Heavy dependencies/browser are layered below copied source so source-only changes can reuse earlier image layers.

### Main functions

#### `telegram_webhook`

Purpose: fast HTTP ingress.

Current resource shape in code:

```text
CPU: 0.25
memory: 512 MiB
min containers: 0
max containers: 20
scaledown window: 60 s
timeout: 30 s
```

It validates Telegram admission, persists the update, starts a worker and asks the model to wake in parallel when needed.

The webhook deliberately uses `control_image`, not `agent_image`, so it does not carry/import the browser/agent stack on its critical path.

#### `process_telegram_update`

Purpose: claim a conversation and run its unanswered updates as full application turns.

Current resource shape:

```text
CPU: 1
memory: 2048 MiB
min containers: 0
max containers: 8
scaledown window: 60 s
timeout: 600 s   (`WORKER_TIMEOUT_SECONDS`)
retries: 1
```

It mounts the persistent workspace Volume, reloads before a turn and commits after the turn.

`retries=1` re-invokes the same `update_id` once after the container is killed
at its timeout; a crashed container is rescheduled by the platform on its own.
The inbox lease (`ui/telegram/webhook.py` `LEASE_SECONDS`, 590 s) is shorter
than the timeout on purpose: the re-invocation, or the conversation's next
message, finds the row claimable and the checkpointed turn is taken up rather
than started again. Raising the timeout means raising the lease with it, and
the lease must stay below it. Whether the platform retries a timeout the way
it retries a crash is to be confirmed on the first real kill; the next
message resumes the turn either way.

It keeps taking the next update of its conversation for `DRAIN_SECONDS`
(`ui/telegram/webhook.py`, 240 s) and then spawns a fresh worker for the rest.
That window is chosen against the two limits around it: a turn may spend up to
300 s, and this function is killed at 600 s. Raising the timeout or the turn
budget without revisiting it is how a container gets killed mid-turn.

#### `render_web_page`

Purpose: execute public page JavaScript in a separate trust boundary.

Current resource shape:

```text
CPU: 1
memory: 2048 MiB
min containers: 0
max containers: 4
scaledown window: 20 s
timeout: 180 s
proxy authentication: required
```

Critical isolation properties in code:

- no `assistant-control` secret;
- no database URL from the control secret;
- no user workspace volume;
- public URL is checked again in the renderer;
- response returns rendered text/screenshot/console/refusal evidence to the caller.

#### `run_command`

Purpose: run one shell command the model wrote in one person's workspace,
in a container that holds no secret (step 5, `DECISIONS.md` 2026-09-04).

Current resource shape:

```text
CPU: 1
memory: 2048 MiB
min containers: 0
max containers: 8
scaledown window: 180 s
timeout: 660 s   (above the tool's 600 s ceiling; the runner kills the command first)
secrets: none
volume: assistant-workspaces at /workspaces
```

The worker calls it through `ModalRunner` (`.remote.aio`), committing the
Volume before and reloading after; the Function reloads before the command
and commits after. Python is the image's 3.12; a venv the model makes in the
workspace is on the Volume and survives, an install into the container does
not; the everyday document and data libraries (`BASE_PACKAGES`) are in the
image, because a venv on the Volume pays per file (55 s for a no-op install). A venv on the Volume is bound to the image's Python version: a deploy
that changes it leaves the model making a new one. Every invocation starts a
container: a product-runtime worker during development.

Cold start is measured with `scripts/measure_command_cold_start.py`, which
invokes the deployed Function twice (one cold, one warm) and prints the
wall time beside the command's own; running it starts a container and needs
permission.

#### `scenarios`

Runs `scripts/loop_live.py`'s scenarios inside the worker's own environment
(image, secrets, Volume, `ModalRunner`), for a probe user `loop-live-check`,
writing the deployed telemetry under ids `deployed-<8 hex>-<sequence>`.
Driven from a person's machine:

```text
.venv\Scripts\python.exe -m scripts.loop_live --deployed A B G
.venv\Scripts\python.exe -m scripts.loop_live --deployed R S
```

Every turn wakes the GPU; permission each time. Since 2026-09-04 this is
the after-deploy check of the deployed profile; the local run answers what
the local profile does.

#### `self_test`

Runs the assistant's real capability probes in the deployed environment.

Default is free. Optional arguments can add:

```text
include_model=True   -> model/GPU probe
include_credit=True  -> provider-credit search probe
```

A deploy is not the same as an invocation; invoking this function can start containers and, depending on options, external paid resources.

#### `measure_database_latency`

Diagnostic for representative production read/write behavior and optional primary/alternate comparison. It is not application request handling.

## Modal model deployment

**Owner:** `deploy/modal/model_app.py`.

App name:

```text
assistant-llm-v2
```

Current model/server constants in code include:

```text
checkpoint: google/gemma-4-12B-it-qat-w4a16-ct
served name: gemma-4-12b-it
vLLM: 0.26.0
transformers: 5.14.1
GPU: A10
max model length: 65536
GPU memory utilization: 0.80
multimodal per-prompt limits: image=4, audio=1
min containers: 0
max containers: 1
scaledown window default: 12 s
concurrent inputs per GPU container: 8
```

The model endpoint requires Modal proxy authentication at the edge.

`MAX_MODEL_LEN` does not size the KV cache — `GPU_MEMORY_UTILIZATION` does — so
raising it costs concurrency rather than VRAM. It cannot change on a running
server and a new value invalidates the GPU snapshot, so it is set once rather
than used as a dial; the dial is `AGENT_CONTEXT_FRACTION`, which needs no
restart. Measured at 65536 on 2026-08-30: 11.13 GiB of KV pool, 256,669 tokens,
3.92x concurrency at full request length. Read those two lines from the boot log
rather than deriving a per-token constant — it is not constant across ceilings
for this model. `reports/2026-08-30_v2_context_memory_plan.md`.

The single-node rendezvous is pinned to loopback (`VLLM_HOST_IP=127.0.0.1`,
`NCCL_SOCKET_IFNAME=lo`, `GLOO_SOCKET_IFNAME=lo`) in the image environment, not
in an enter hook: all three are read while the process group is constructed,
which happens before the snapshot exists. Without them a restored container
polls a socket whose peer is gone and logs `Broken pipe` about once a second for
its whole life.

### The second model: `assistant-llm-qwen`

**Owner:** `deploy/modal/model_app_qwen.py`, which imports the machinery of
`model_app.py` and shares its image and both Volumes. Since 2026-09-05.

```text
checkpoint: Qwen/Qwen3.8-27B-FP8 (revision 017b9c7a)
served name: qwen3.8-27b
GPU: L40S
max model length: 131072
GPU memory utilization: 0.90 (0.86 was refused: 7.04 GiB of KV against 8.18 needed)
KV cache dtype: auto (bf16); measured 9.75 GiB, 155,600 tokens, 1.19x at 131,072
max num seqs: 16 (one Gated DeltaNet state block per decoding sequence)
default chat template kwargs: {"enable_thinking": false} (since the 0.28.0 change; the deployed FP8 App still runs 0.26.0 with reasoning_effort low until redeployed)
parsers: tool qwen3_xml, reasoning qwen3
multimodal per-prompt limits: image=4, video=0
container memory request: 32 GiB (sleep level 1 holds the weights in CPU memory)
scaledown window, containers, concurrency: the first App's, by import
restored wake: 88.5 s measured (a 28.5 GiB CPU snapshot); first boot ~290 s to healthy
```

Both Apps commit the compile-cache Volume after warmup and before the
sleep the snapshot captures; a snapshot holding a handle into an
uncommitted Volume path cannot be restored (ISS-0047).

### The third model: `assistant-llm-qwen-int4`

**Owner:** `deploy/modal/model_app_qwen_int4.py`: its own numbers and a
small class; the spec, command, boot and CPU checks are
`model_app_qwen.py`'s. Since 2026-09-05, on the human's word, for a
snapshot half the FP8 App's.

```text
checkpoint: RedHatAI/Qwen3.8-27B-INT4 (revision 2fb0debc), W4A16 g128
served name: qwen3.8-27b-int4
GPU: A100-40GB
max model length: 131072
GPU memory utilization: 0.90
vLLM 0.28.0 / transformers 5.15.0 (the Qwen Apps' own pair; Gemma stays on 0.26.0 / 5.14.1)
prefix caching: on (--enable-prefix-caching; align mode for the DeltaNet layers)
thinking: off by default on the server ({"enable_thinking": false})
everything else: the FP8 App's (parsers, max num seqs 16, image=4)
container memory request: 24 GiB
```

**The thinking dial is configuration, not a boot.** `MODEL_CHAT_TEMPLATE_KWARGS`
in `.env` (JSON, e.g. `{"enable_thinking": true, "reasoning_effort": "low"}`)
is sent on every request as `chat_template_kwargs` and overrides the
server's default; publish the secret and redeploy `assistant-control`.
Blank or unset sends nothing.

**A hosted model instead of a model App** is the same four settings pointed
elsewhere: `MODEL_ENDPOINT` at the service's OpenAI-compatible root,
`MODEL_NAME` its model id, `MODEL_API_KEY` the key with
`MODEL_AUTH_STYLE=bearer`, and `MODEL_CHAT_TEMPLATE_KWARGS` blank (vLLM's
field). `MODEL_EXTRA_BODY` (JSON) is merged into every request body last,
for what the service wants and the OpenAI shape has no word for
(`{"tool_stream": true}` for GLM through CometAPI). Such a service usually
reports no context length on `/v1/models`, so `AGENT_CONTEXT_TOKENS` names
the budget; publish the secret and redeploy `assistant-control`.

**The order for a Qwen App, each step its own gate:** `fetch_weights`
(CPU) → `preflight` (CPU: the engine configuration builds, and
`model_app_qwen.fits` — the pool arithmetic calibrated on the boots of
2026-09-05, good to about half a gigabyte — accepts the ceiling) →
`dry_run` (one GPU boot in a plain Function with no request behind it and
`retries=0`, so a failure is paid once, ISS-0049) → `modal deploy` → the
first request, which creates the snapshot and is the only step that can
show a failed restore. The Qwen Apps' `Server` mounts no compile-cache
Volume (ISS-0047) and runs with `VLLM_USE_AOT_COMPILE=0` (ISS-0050).

The same three functions, `fetch_weights`, `preflight` and `Server`, run
from this file:

```text
modal run deploy/modal/model_app_qwen.py::fetch_weights
modal run deploy/modal/model_app_qwen.py::preflight
modal deploy deploy/modal/model_app_qwen.py
```

**Switching the assistant between the two models** is configuration, never
code: set `MODEL_ENDPOINT` to the App's `.modal.run` URL plus `/v1` and
`MODEL_NAME` to its served name in `.env`, publish the secret with
`tools/sync_control_secret.py`, and redeploy `assistant-control` so that
fresh containers read it (a warm worker keeps the old values until it
sleeps). The context ceiling is read from the server, so nothing else
changes. Both Apps stay deployed; the one not in use costs nothing while
asleep. A run under one model is recorded under that model's served name.

### Model functions

#### `fetch_weights`

CPU-only weight-cache population.

Use it when the pinned checkpoint is not already present in the HF cache Volume. It avoids downloading model weights while GPU billing is active.

#### `preflight`

CPU-only check for known vLLM/transformers/model-config startup failures. It is intended to catch known configuration regressions before paying for a GPU boot.

#### `Server`

The GPU class. It starts vLLM, performs warmup, sleeps it before snapshot, restores from CPU+GPU snapshot, wakes it, and serves the OpenAI-compatible endpoint.

Persistent deployment storage:

```text
assistant-hf-cache    -> model weights
assistant-vllm-cache  -> vLLM compile/cache
```

Neither is canonical conversation/user data.

## GPU scaledown without deploy

**Owner:** `deploy/modal/autoscale.py`.

Examples:

```text
python deploy/modal/autoscale.py
python deploy/modal/autoscale.py --window 300
```

This updates the running Modal class autoscaler without rebuilding images or redeploying the app.

Important behavior: a later `model_app.py` deploy resets the setting to the `SCALEDOWN_WINDOW` constant in that file. Persistent policy changes therefore belong in code after measurement; `autoscale.py` is the experiment/control surface.

## Storage ownership

### Local profile

```text
conversation/memory        AGENT_DATABASE -> SQLite file
in-flight turns            AGENT_CHECKPOINTS -> SQLite file
turn telemetry             AGENT_TELEMETRY_DATABASE -> SQLite file
workspace                  AGENT_WORKSPACE -> local directory
"stop what is running"     in memory: one process, so nothing durable is needed
```

Telemetry is deliberately its own file: it is disposable in a way a conversation
is not, so deleting it costs nothing.

Exact defaults live in `AgentSettings` and `env.example`.

### Deployed profile

```text
Neon/PostgreSQL
  ├─ conversations/messages/summaries/facts
  ├─ LangGraph checkpoint tables
  ├─ telegram_updates durable inbox (turn run_id, conversation lease)
  └─ turn_runs / trace_events turn telemetry

Modal Volume assistant-workspaces
  └─ /workspaces/<canonical-user>/...

Modal Volumes
  ├─ assistant-hf-cache
  └─ assistant-vllm-cache
```

The deployed CPU worker may disappear between turns. Durable product state must therefore be in these stores rather than process memory.

## Web operational configuration

Current web capability has three distinct operational paths:

```text
search_web     -> Firecrawl provider
fetch_page     -> direct outbound HTTP from agent worker
view_web_page  -> configured renderer endpoint in deployment
```

Relevant settings:

```text
WEB_FIRECRAWL_API_KEY
WEB_FIRECRAWL_ENDPOINT
WEB_FALLBACK_USER_AGENT
WEB_LOCAL_BROWSER
WEB_RENDERER_URL          # deployed runtime name
WEB_RENDERER_KEY
```

Local `.env` intentionally uses `DEPLOY_WEB_RENDERER_URL` for the deployed renderer address; the secret sync tool renames it during publishing.

The deployed `agent_image` sets `WEB_LOCAL_BROWSER=0`. If renderer configuration is missing, public browser viewing should fail instead of silently running page JavaScript in the worker that holds secrets.

## Diagnostics and checks

### `/can`

User-facing claim generated from current runtime wiring. It does not call the model.

Owner path:

```text
app/capabilities.py -> capability_report()
ui/telegram/adapter.py -> /can dispatch
```

### `/plan`

`/plan` says whether the assistant keeps a task list; `/plan on` and
`/plan off` flip it. Off is the default. The switch is the marker file
`.agent/plan.on` in the person's workspace, read when the next turn's toolbox
is built: without it there is no `todo_write` tool and, since the brief is
generated from the toolbox, no planning guidance either. Answered without the
model. Asked for on 2026-09-03 to separate the plan's defects (ISS-0016,
ISS-0019) from the rest; off by default the same day, `DECISIONS.md`.

Measured 2026-09-04 (`reports/2026-09-04_v2_isolated_execution_review.md`
§14): the same request for a PDF in Russian, the same image — with the plan
on it was delivered in Russian, with it off in English. The benefit is not
better planning; it is that an open list refuses an ending, so the request
stays in front of the model until it is met. Still off by default.

```text
ui/telegram/adapter.py -> /plan dispatch
app/agent/todo.py      -> PLAN_SWITCH, planning_enabled, set_planning
```

### `/mode`

`/mode` says whether changes to the workspace ask first; `/mode careful` and
`/mode full` set it from the next message. Full is the default: everything
inside the workspace — reading, writing, running a command — is autonomous,
and only effects beyond it ask. Careful makes `write_file`, `edit_file` and
`run_command` wait for the same yes/no buttons. The switch is the marker
`.agent/careful.on` in the person's workspace, read when the next toolbox is
built, like `/plan`. Answered without the model. `DECISIONS.md` 2026-09-04.

```text
ui/telegram/adapter.py -> /mode dispatch
app/agent/mode.py      -> CAREFUL_SWITCH, careful_enabled, set_mode
app/tools/base.py      -> Tool.mutates, Toolbox.ask_for_changes
```

### `/context` and `/compact`

`/context` says what the next request in this chat is made of, estimated by
layer — core and capabilities, tool schemas, the conversation with how many
tool results are shortened, the summary's reach, the facts — plus the last
request's own token count and how many of them the server served from its
prefix cache, and the chosen size against the model's ceiling. Answered
without the model; the ceiling is reported only when this worker has already
read it, because asking the server would wake it.

`/context small|normal|large` chooses the size: 25%, the configured fraction
(`AGENT_CONTEXT_FRACTION`), or 95% of the ceiling. The choice is the marker
file `.agent/context` in the person's workspace, read by `Agent.budget` when
the next turn's graph is built; `normal` removes the marker.

`/compact` folds the older part of the conversation into the summary now,
one summarizer call, and says how many messages it newly covers. It wakes
the model, so it is not a model-free command.

A fold that happens on its own during a turn is announced after the answer
(since 2026-09-03, asked for by the human): how many messages were folded,
that the last `keep_turns` exchanges stay verbatim, and that the exact words stay
reachable with `search_history`. The adapter detects it from the summary's
covered position before and after the turn.

```text
ui/telegram/adapter.py -> /context, /compact dispatch
app/context/choice.py  -> CONTEXT_CHOICE, context_choice, set_context_choice
app/agent/runtime.py   -> Agent.context_report, Agent.compact, Agent.budget
```

### `/agents`

The person's own standing instructions for how the assistant should work. It
does not call the model, and is declared model-free at the front door with its
arguments included, so `/agents set …` cannot wake the GPU.

Stored as `AGENTS.md` at the root of that person's workspace — the Modal volume
in the deployed profile — and read again on every turn, so an edit applies to
the next message with no redeploy. There is no database copy and no migration.
It is a prompt overlay of lower authority than product and capability policy,
never memory.

Owner paths:

```text
app/instructions.py -> read/write/clear and the framed message
app/context/window.py -> build_prelude places it after the system message
ui/telegram/adapter.py -> /agents dispatch
ui/telegram/wire.py -> MODEL_FREE_WITH_ARGUMENTS
```

### `/check`

Runs actual capability probes from the current runtime. Telegram's normal `/check` uses free probes and does not wake the GPU.

Owner paths:

```text
app/preflight.py
app/agent/runtime.py -> Agent.selftest()
ui/telegram/adapter.py
```

Deployed `self_test` in `control_app.py` exists for the same question inside the actual container and can optionally include GPU/provider-credit checks.

### Reading what a turn cost

Every measured turn writes two things that share one `run_id`: an immediate
structured log line per event — visible in Modal's log view while the turn is
still running, and on the terminal in the local profile — and a durable record
in `turn_runs` / `trace_events`, written as one row at claim and bounded batches
afterwards (about every 25 events, and at the end).

```text
turn_runs      one row per turn: outcome, status, route, model/tool counts,
               tokens, first model token, first visible response, total time
trace_events   the ordered detail: turn, loop steps, model, tool, approval,
               persistence and Telegram delivery boundaries
```

A turn that ends without an outcome is closed as `failed`/`incomplete` by the
worker, so a container that died in a way the process survived leaves a finished
row. One whose container disappeared entirely stays `running` forever, and that
is what `--failed` looks for.

```bash
python tools/show_run.py <run_id>
```

```text
--last N        the most recent runs, newest first
--failed        runs that failed or never finished at all
--user <id>     one person's runs
--summary       the primary metric over those runs
```

`--summary` reports **GPU active seconds per successful turn**, plus derived
cost per turn and per user, model and tool calls per successful turn, and
failures by type. Successful means the outcome was an answer or an approval; a turn that burned GPU and failed stays in the numerator and
leaves the denominator, which is the point.

It reads the same database the application writes: the local SQLite file by
default, and the deployed one when `AGENT_DATABASE_URL` is set in the shell. It
is read-only — no migration, and nothing started. Rendering lives in
`app/telemetry/inspect.py`; the script is the entry point.

A rendered run shows the queue wait, first model token and first visible
response, then model calls with their tokens, tool calls with stage and path,
the loop's steps with what the turn had spent reaching each — and the limit or
the stop that ended it early, when one did — the full event timeline at its
offsets, the totals including time no measured step claimed, and a derived GPU
section.

### GPU seconds and cost per turn

Derived when a run is read, never stored, so a better formula improves every
past run instead of leaving a frozen number in a column.

```text
model request time       measured: the engine was working
estimated active         derived:  first request to last, plus the idle window
derived cost             derived:  active seconds x the configured GPU rate
platform billed time     not visible here; Modal aggregates per App
```

It is an **upper bound per turn**: the idle window is charged in full to the
turn that opened it, while a following turn inside that window shares the same
awake container. Do not sum these and call the result the bill; compare
aggregates against `modal billing` instead. `--idle-window` and `--gpu-rate`
override both inputs. `IDLE_WINDOW_SECONDS` mirrors `SCALEDOWN_WINDOW` in
`deploy/modal/model_app.py` and a test keeps them equal.

### Prompt scenario comparison

`python tools/prompt_scenarios.py --dry-run` composes the assembled system
message and the scenario list and contacts nothing. Without `--dry-run` it runs
each scenario through the same agent the bot uses.

**A real run wakes the GPU and needs explicit permission for that run.**
`--external` additionally sends a query to the search provider and spends its
credit, which is a separate permission; it is left out by default.

`--goal off` runs the same scenarios without the `set_goal` tool
(`app/tools/goal.py`), which the product offers always; the report header
names the setting, so two runs are compared as two named things.

`--prompt-file` measures a prompt variant instead of the shipped default;
`tools/prompts/` holds the variants worth keeping. Each run writes its own
directory under `reports/prompt_runs/`: `report.md`, the exact
`system_prompt.txt` that produced it, and a throwaway workspace, store and
telemetry file that never touch the deployed database.

### Model server baseline

`python tools/vllm_baseline.py` prints the plan and contacts nothing. `--run`
sends it.

**`--run` wakes the GPU and needs explicit permission for that run**, including
`--discover`, which only reads `/metrics` — the metrics endpoint is served by
the same scale-to-zero container as the model.

```text
--discover     read /metrics once, publish which names this vLLM actually has
--run          the whole suite: short-in/long-out, four input sizes, repeated prefix
--from-file    re-render saved readings; touches nothing
```

Raw readings are saved to `reports/vllm_baseline_<stamp>.json` before anything
is analysed, so a run is never repeated to recover a number. Metric names are
discovered rather than copied between vLLM releases, an absent counter is
reported as absent rather than as zero, and a delta spanning an engine restart
is refused instead of published. Every prompt except the repeated-prefix pair
starts with a marker unique to the run, so prefix caching cannot silently
answer the prefill question.

### Local doctor

`python scripts/doctor.py`

Use for environment-level local diagnostics before inventing one-off checks.

### Model wake measurement

`scripts/measure_endpoint_wake.py` owns the dedicated endpoint wake measurement workflow.

### Broad smoke/live scripts

`scripts/smoke_test.py`, `scripts/stage3_live.py`, and `scripts/v1_live.py` are existing runners. Some reflect earlier project stages; inspect their exact scope before treating them as current product acceptance.

## Work/evidence logging

**Owner:** `tools/work_log.py`.

Append-only journals:

```text
reports/agent_tasks.jsonl
reports/ml_work.jsonl
```

The tool captures repository metadata (branch, HEAD, changed files) when appending. Use it rather than hand-editing JSONL if a journal entry is required by `AGENTS.md`.

Human-readable implementation evidence belongs in `reports/` rather than in the four stable project maps.

## Common operational tasks: find the owner first

| Need | Existing owner |
|---|---|
| Add/change env setting | `app/config.py` + `env.example` |
| Publish control secret from `.env` | `tools/sync_control_secret.py` |
| Create/migrate deployed DB/checkpoints/inbox | `tools/setup_control_plane.py` |
| Register/remove/show Telegram webhook | `tools/telegram_webhook.py` |
| Preview/publish Telegram profile and command menu | `tools/telegram_profile.py` |
| Deploy CPU control plane | `deploy/modal/control_app.py` |
| Deploy model server | `deploy/modal/model_app.py` |
| Deploy the second model server (Qwen3.8 FP8, L40S) | `deploy/modal/model_app_qwen.py` |
| Deploy the third model server (Qwen3.8 INT4, A100-40GB) | `deploy/modal/model_app_qwen_int4.py` |
| Check a Qwen App's ceiling against its pool without a GPU | `modal run deploy/modal/model_app_qwen*.py::preflight` (`model_app_qwen.fits`) |
| Boot a Qwen configuration once, no snapshot, no retries, before deploying it | `modal run deploy/modal/model_app_qwen*.py::dry_run` (`model_app_qwen.dry_boot`; a GPU boot, permission each time) |
| Measure a wake and probe a model endpoint | `scripts/measure_endpoint_wake.py --url … --model <served name> [--no-audio]` |
| Point the assistant at the other model | `MODEL_ENDPOINT`, `MODEL_NAME` in `.env`, then `tools/sync_control_secret.py` and a control-plane deploy |
| Change current GPU idle window without deploy | `deploy/modal/autoscale.py` |
| Diagnose deployed capabilities | `control_app.py::self_test` |
| Diagnose local install | `scripts/doctor.py` |
| Measure endpoint wake | `scripts/measure_endpoint_wake.py` |
| Compare prompt variants on fixed scenarios | `tools/prompt_scenarios.py` |
| Render a stored conversation as Markdown with its media, read-only | `tools/showcase.py` |
| Record/search agent/ML work journal | `tools/work_log.py` |

## Operations invariants

- Treat `.env` as the local configuration source; publish a reviewed allow-list, not the whole file.
- Do not print or store secret values in reports or shell command strings.
- Do not run application DB migrations implicitly on normal serverless request paths.
- Do not conflate deploying a function with invoking it; invocation can start billable resources.
- Keep public page JavaScript away from control-plane secrets in the deployed profile.
- Keep the model app independent from the application code; their runtime contract is the configured OpenAI-compatible endpoint.
- Keep local/deployed differences behind settings, stores and deployment adapters rather than branching `app/`.
- Check `tools/`, `scripts/` and `deploy/` before adding an operational helper: many correct owners are intentionally not imported by the application.
