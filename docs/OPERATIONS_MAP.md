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
   ├─ ask                       one turn on a free text, the same way (MCP run_turn --deployed)
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

**Owner:** `app/config.py`. Two files, one order:

- **`config.toml`** at the repository root, committed, shipped in the image
  beside the source: every setting that is not a secret, in sections
  `[model]`, `[model.sets.<name>]`, `[agent]`, `[telegram]`, `[web]`,
  `[mcp.servers.<name>]` (an MCP server as tools: transport, command or
  URL, the `.env` key of its token, the allowlist, the read-only list). The
  agent may edit it; a change deployed is a commit and a control-plane
  deploy.
- **`.env`**, never committed: tokens, keys, database URLs (`env.example`
  lists them). The environment, `.env` included, wins over the file, and the
  file wins over the defaults in code, so a line in `.env` still overrides one
  setting on one machine. `CONFIG_FILE` names another file; empty reads none
  (what the test suite sets).

### Model sets

`[model].chosen = "or"` names the set; `[model.sets.or]` holds its
`endpoint`, `name`, `auth_style`, `providers`, `extra_body`,
`chat_template_kwargs`, `dump_dir` and `context_tokens`; its key is
`MODEL_OR_API_KEY` in `.env` (a Modal App set without its own key uses the
shared `MODEL_API_KEY` proxy token). The plain `[model]` keys are the unnamed set.
Switching the deployment is the `chosen` line and a control-plane deploy (a
warm worker keeps the old values until it sleeps). The environment names are
`MODEL` and `MODEL_<SET>_<FIELD>`, `AGENT_<SET>_CONTEXT_TOKENS`.

The deployed default, `[model.sets.or]`: GLM 5.3 Flash through OpenRouter,
`providers = ["novita/fp8", "z-ai/fp8"]`, thinking off, 262,144 tokens.

- `providers`: the router's own slugs (`/api/v1/models/<id>/endpoints`).
  The first is the only one asked (`provider.order` of one,
  `allow_fallbacks: false`); the rest are asked by the client only after
  the first failed its retries, never for being slow, because a move loses
  the prefix cache (`reports/2026-09-07_turn_bounds_context_provider.md`).
- `extra_body` is merged into every request last: thinking flags, anything
  the OpenAI shape has no word for. `thinking = {type = "disabled"}` with
  `reasoning_effort = "low"` leaves GLM with no reasoning tokens on tool
  calls; Qwen uses `enable_thinking = false` in `chat_template_kwargs`
  (vLLM's field).
- A hosted service usually reports no context length on `/v1/models`; the
  set's `context_tokens` is then the budget.
- `dump_dir` keeps every streamed response as one `.sse` file, request body
  first; it holds conversation content, so it lives on the Volume deployed.
- `gemini` is a second set on the same endpoint, paused until its cache
  lands (roadmap item 13). `int4`, `qwen`, `v2` are the GPU Apps:
  `auth_style = "modal_proxy"`, the `wk-…` proxy token as the key; the
  ceiling is read from the server. `tuned` is the fine-tuned Gemma of
  roadmap 24, served from the training repository's App on the second
  Modal workspace; it needs its own `MODEL_TUNED_API_KEY`, that
  workspace's proxy token.

### Turn and context settings

A turn has no ceiling on steps, tool calls or seconds; the health question
and its shape are in `docs/PROJECT_MAP.md` ("Agent runtime"). The interval
is `turn_check_seconds = 360` in `config.toml`; zero asks never. Context:
the size the person chose (`/context small|normal|large`: 131,072, 262,144
or 524,288 tokens), clamped to the window the server reports, or to the
set's `context_tokens` where it reports none;
a fold happens only when the request would not fit, or on `/compact`;
`keep_turns` 2. `stream_answers` and `telemetry` are on; telemetry holds
timings and counts only and can never fail a turn.

The limits (`app/limits.py`, roadmap 31) are of two kinds. Shares of the
request's budget, in `[agent]`: `result_share` (1/8: the most one tool
result shows inline, the whole text in `.agent/results/` past it),
`keep_recent_share` (0.15: the newest text kept verbatim by a fold),
`summary_share` (0.05, at most `summary_ceiling_tokens` 12,000),
`stub_share` (0.0005), `instruction_share` (0.02, at most
`instruction_ceiling_bytes` 32,768: the person's `AGENTS.md`). One tool's
page, settings with the references' defaults: `read_lines` 2,000,
`line_chars` 2,000, `search_page` 100, `preview_chars` 400,
`shell_output_chars` 30,000 (the whole output in `.agent/commands/` past
it), `web_text_chars` 20,000, `snapshot_chars` 12,000,
`visible_text_chars` 8,000, `screenshot_height` 6,000, `csv_rows` 200,
`command_timeout` 120 and `command_timeout_max` 600 (above the most a
command is started in the background locally and refused by number
deployed; never clamped). Per model set: `max_tokens`, `max_images` (4),
`max_audio` (1). The loop (roadmap 32): `parallel_calls` 10 (reads of one
batch at once), `repeat_note_after` 2 and `repeat_stop_after` 8 (a repeated
identical call is noted, then not run). Every one is `AGENT_<FIELD>` or
`MODEL_<SET>_<FIELD>` in the environment. At a 256K budget and three characters a token, a result
shows 98,304 characters, a page 88,473, a fold keeps 39,321 tokens, a
summary is at most 12,000 tokens, the instruction file 15,728 bytes.

## Secrets

**Owner:** `tools/sync_control_secret.py`.

```text
.venv\Scripts\python.exe tools/sync_control_secret.py
```

Reads `.env`, publishes the allow-list only (Telegram, database, every
`MODEL_<SET>_API_KEY`, every `MCP_<NAME>_TOKEN`, web keys), prints names never values, replaces
the Modal secret `assistant-control` with `--force`. `DEPLOY_WEB_RENDERER_URL`
is published as `WEB_RENDERER_URL` so the local profile never sends its page
views to the deployed renderer. No second sync path, no values typed into the
provider console.

## Database and migrations

**Owner:** `tools/setup_control_plane.py`, run by hand, never on a request.
It creates or migrates the store schema, the LangGraph checkpoint tables,
the `telegram_updates` inbox (`conversation_key`, `control`, `run_id`),
`turn_stops`, and the telemetry tables (`telemetry_version` 1). Every step is
additive. Store schema version is **5** in both implementations (`user_version`
in SQLite, `schema_version` in PostgreSQL): 2 `user_state`, 3 `messages.failure`
and `compactions`, 4 the derived `text` column with a full-text index (FTS5 /
`simple` tsvector + GIN) that `search_history` reads, 5 `notes` (what the
harness said in a conversation, shown by an interface, never read by the
model). The code is at 5; the deployed Neon database is still at 4 until a
migration gate. Every connection the store and the inbox open carries libpq's bounds
(`app/memory/postgres.py` `CONNECTION_GUARDS`: `connect_timeout` 10 s, TCP
keepalives and `tcp_user_timeout` that declare an unanswered socket dead in
about a minute; ISS-0064). Migrating or resetting a populated database is a human gate; there is no
application path to a reset (`drop_schema` refuses `public`).

## Telegram mode

- **Profile and menu:** `tools/telegram_profile.py` previews; `--publish`
  sends (external mutation, ask first). Menu: `/new /chats /can /agents
  /mode /context /compact /stop /help`; `/check` and `/plan` work but are
  not listed (`/plan` answers that its switch is gone).
- **Webhook (deployed):** `tools/telegram_webhook.py` shows status, `--url
  <webhook>` registers, `--delete` returns to polling. The webhook is
  `telegram_webhook` in `control_app.py` over `ui/telegram/webhook.py`.
- **Polling (local):** `python -m ui.telegram.run`. Telegram refuses polling
  while a webhook is registered.

## Local profile

Chainlit on the person's own machine (`ui/chainlit_app.py`), the same
`app/` and the same hosted model set. A file sent through the app is saved
under `%TEMP%\assistant-uploads\<thread>` (`uploads_dir`) and named to the
model by its absolute path; there is no `inbox/` locally. The working folder
a conversation writes in is chosen with `/workspace <path>` and kept per
thread in `.agent/folders.json` in the workspace (`app/agent/folder.py`).
Reading reaches any path on the machine; a write outside the folder asks the
person first. A remembered yes (`once` / `conversation` / `always`) is
`.agent/grants.json` in the workspace (`app/agent/grants.py`); a background
command's exit is told to the model at its next step (`notify`, on by
default). `/status`, which the status card polls, is an HTTP route the
app adds to Chainlit's own server, not a command. On Windows `run_command`
hands the line to PowerShell (`pwsh` when installed, else Windows PowerShell
5.1) under the write-restricted token, as Codex and Claude Code do; the Cygwin
tools Git Bash carries are not on that route (ISS-0068). `fetch_page` reaches
localhost and any port on the machine, like `use_page`.

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
| `process_telegram_update` | 1 / 2 GiB | 8 | 60 s | 4 h | secrets, the Volume; `retries=1`; lease 60 s extended every 20 s; drains 3 h then hands over |
| `render_web_page` | 1 / 2 GiB | 4 | 20 s | 180 s | no secret, no database, no Volume; proxy auth |
| `run_command` | 1 / 2 GiB | 8 | 180 s | 660 s | no secret; the Volume; cwd is the workspace by its mount path, home and `/tmp` are the container's |
| `scenarios` | 1 / 2 GiB | 8 | 2 s | 1800 s | `loop_live --deployed`, probe user `loop-live-check` or one named per call (`--parallel`) |
| `self_test` | 0.25 / 512 MiB | 1 | 2 s | 300 s | `include_model`, `include_credit` opt in |

A deploy is not an invocation; an invocation starts a container (a gate,
`AGENTS.md`). **After every deploy:** `/check` in Telegram (free), and on
the human's word the mini set (eight scenarios, item 15), bare on both
sides; `--both` runs it here and deployed and prints the two side by side:

```text
.venv\Scripts\python.exe -m scripts.loop_live --deployed
```

(A, B and G; each turn is a paid model call and a worker.)

## Model Apps (not in use since 2026-09-06)

Three GPU Apps exist — `assistant-llm-v2`, `assistant-llm-qwen`,
`assistant-llm-qwen-int4` (`deploy/modal/model_app*.py`) — each a vLLM server
behind proxy auth, scaled to zero and unused since the hosted set became the
default. The operational detail is in
`reports/2026-09-14_model_apps_operations_archive.md`.

## Storage

```text
local     AGENT_DATABASE (SQLite)  AGENT_CHECKPOINTS  AGENT_TELEMETRY_DATABASE  AGENT_WORKSPACE
deployed  Neon: store, checkpoints, inbox, turn_stops, turn_runs/trace_events
          Volume assistant-workspaces: /workspaces/<user>/ (files, .agent/ switches, AGENTS.md, .dumps)
                                       and /workspaces/.trajectories/<run_id>.jsonl
          Volumes assistant-hf-cache, assistant-vllm-cache (GPU Apps)
```

A worker may disappear between turns; durable product state is only in these.
The local SQLite files run in WAL mode with a 5 s busy timeout (`-wal` and
`-shm` beside the file while open), so the Chainlit app and the MCP server
write the same file without colliding.

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
| `/plan` | answers that the task list is always available and a plan without changes is `/mode plan` | `app/agent/commands.py` | no |
| `/mode [full\|careful\|plan]` | ask before workspace changes, or offer nothing that changes; marker `.agent/mode` | `app/agent/mode.py` | no |
| `/context [small\|normal\|large]` | what the next request is made of, cached tokens, the chosen size (131,072 / 262,144 / 524,288 tokens); marker `.agent/context` | `app/context/choice.py`, `Agent.context_report` | no |
| `/compact` | fold now | `Agent.compact` | yes, one summarizer call |
| `/agents [set\|clear]` | standing instructions, `AGENTS.md` in the workspace | `app/instructions.py` | no |
| `/new`, `/chats` | conversation choice, stored | `ui/telegram/adapter.py` | no |
| `/stop` | out of band; the loop reads it at its next step | `app/agent/stop.py` | no |
| a message while a turn runs | taken by the running turn after its next tool batch, as the person's words; answered as its own turn if no batch follows | `app/agent/interjections.py`, `ui/telegram/interjections.py` | no |

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

**Trajectories** (roadmap 24): with `AGENT_TRAJECTORIES` set — the deployed
image sets it to `/workspaces/.trajectories` — every model call of every
turn is one JSON line in `<dir>/<run_id>.jsonl`: the request as the model
saw it (`messages`, `tools`), the `completion` (text, tool calls, finish
reason, usage), the run and thread ids and the served model's name; media
parts as kind and size, never bytes. Nothing resets the folder. Read them
with `python -m modal volume ls assistant-workspaces .trajectories` and
`python -m modal volume get assistant-workspaces .trajectories/<run_id>.jsonl <local path>`
(reads, not workers). Locally, set `AGENT_TRAJECTORIES=data/trajectories`.
`python tools/export_trajectories.py --out data/export [--prefix …]` reads
them all from the Volume (a client read) and writes one JSON per run with
the run's outcome, failed-tool codes, repeat-guard count and the scenario's
checks (the `scenario_checked` event `loop_live` writes) plus
`index.jsonl` — the hand-over to the training repository, which reads
those fields and nothing of this database. `python tools/judge_pack.py
--out data/judge/<batch> --prefix <run prefix> …` renders exported runs as
anonymized, shuffled transcripts plus the rubric; three Sonnet subagents
score them blind (`AGENTS.md`), and `key.json` unblinds afterwards.

**Checkpoint growth** (ISS-0067): every checkpoint version is kept; when the
database fills, `python tools/prune_checkpoints.py` shows what would go and
`--apply` (a gate, not during a turn) keeps only each thread's latest
checkpoint and vacuums — 394 MB to 35 MB on 2026-09-11. After a data run
is exported, `python tools/prune_probes.py --apply` (a gate) deletes the
probe users' conversations and checkpoints; their telemetry rows stay.

`python tools/show_thread.py <thread_id> [head_chars]` prints one thread's
rows the same way: role, tool calls, each part's kind and byte count, the
head of the text. Same store as above, read-only.

For the seconds a deployed turn spends outside the model and its tools
(ISS-0056, roadmap 18), save the control app's log to a file and run
`python tools/log_gaps.py <dump>` (the gap between consecutive events, and
per turn model/tools/persist/the rest) or
`python tools/log_named_seconds.py <dump>` (per turn, the seconds the
harness named with `spent(kind)` against the remainder). Both read the file
and start nothing. `python tools/run_named_seconds.py --last 20` makes the
second table from the store instead, since the named events are trace
events; only the log-only ones after a turn (inbox completion, the Volume
trips around the worker) need the dump.

## The harness as an MCP server (roadmap 25)

`python -m tools.mcp_server` serves the operator tools above over MCP on
stdio; `.mcp.json` at the repository root registers it for Claude Code as
`pinocchio` (Codex takes the same command in its own config), tools appear
as `mcp__pinocchio__<tool>`. Thirteen tools: `runs`, `run`,
`harness_seconds`, `thread`, `scenarios`, `judge_unblind`,
`work_log_search`, `doctor` (read-only); `export_trajectories`,
`judge_pack` (write files), `work_log_add` (appends); and two that start
priced work, `run_scenarios` (`loop_live` with its flags) and `run_turn`
(one turn of the assistant in a sealed room, a probe user, approvals
inside the turn answered no; `deployed=true` runs it in the deployed
worker through the `ask` Function of the active workspace). Each wrapped script runs as a subprocess in
this process's environment, stdout captured, so a script's `print` never
lands on the protocol's stdout.

The gate is in the protocol twice: the priced tools carry
`anthropic/requiresUserInteraction` (Claude Code asks before every call in
every mode but `dontAsk`) and ask again themselves through elicitation,
scope and price in the question; anything but an accepted yes is a
refusal, and a client without elicitation gets the refusal too. The rule
that a worker starts on the human's word is not replaced by this; the
project agent still asks in the chat. No tool returns an environment
value; the database is reached only by the scripts, through
`AgentSettings`. The SDK is held below 2 (`mcp>=1.29,<2`, group `mcp`)
because Chainlit pins it; that speaks protocol 2025-11-25, which the
clients accept.

## Other tools

- `tools/prompt_scenarios.py --dry-run` composes the brief and contacts
  nothing; without it every scenario is a paid run. `--goal off`,
  `--prompt-file`; output under `reports/prompt_runs/`.
- `tools/showcase.py` renders a stored conversation with its media, read-only.
- `scripts/loop_live.py` runs the mini set (A B C F W H E M) or wider letters
  (G I J K O P Q R S) locally, deployed, or both side by side;
  `--deployed --model <set>` runs them against another model set for that
  run only (its GPU App wakes: a gate), run ids `deployed-<set>-…`;
  `--repeat N` runs the chosen letters N times; `--temperature 0.7`
  samples for that deployed run (the product runs at 0); `--parallel N`
  spawns N deployed calls at once, each under a probe user of its own
  (`loop-live-p1`…, threads `<probe>:chat-…`), N runs' data in one run's
  time. The training families
  D L N T U V X (roadmap 24; seeds, prompts and outcome checks in
  `scripts/training_scenarios.py`) run the same way, several turns a letter.
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
