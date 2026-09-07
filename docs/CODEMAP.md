# Code Map

The navigation index for Pinocchio Harness: find the **existing owner** of a
behaviour before reading widely or creating a second implementation. Not a
history. `ROADMAP.md` has current work, `docs/PRODUCT.md` the invariants,
`docs/PROJECT_MAP.md` the conceptual map, `docs/OPERATIONS_MAP.md` the
commands, `DECISIONS.md` the reasons behind a boundary.

## Rule for exploration

> **Find the existing owner before creating another owner.**

Before adding a script, registry, storage path, deployment entry point,
browser launcher, secret writer, tool abstraction, message type or UI flow:
find the intent below, open the owner, search the named symbols and env
keys, read the closest test, then decide. `tools/`, `scripts/` and `deploy/`
are not reachable by following imports from `app/`.

## First files to read by task

| Intent | Primary owner | Search terms |
|---|---|---|
| Change a setting (not a secret) | `config.toml`, `app/config.py` | `Configured`, `section`, `load_config`, `CONFIG_FILE`, `tests/test_config_file.py` |
| Change a secret's name or what is published | `env.example`, `tools/sync_control_secret.py` | `ALLOWED`, `MODEL_SET` |
| Choose or add a model set | `config.toml` `[model.sets.<name>]`, `app/config.py` | `ModelChoice`, `chosen_model`, `_env_prefix`, `ModelBudget`, `providers`, `tests/test_model_settings_chat_template.py` |
| Change the agent loop | `app/agent/graph.py` | `build_agent`, `AgentState`, `interrupt`, `tests/test_agent_graph.py` |
| Change when a long turn is asked how it is doing | `app/agent/graph.py` | `TurnWatch`, `HEALTH_QUESTION`, `health_question`, `checked_seconds`, `turn_health_check`, `tests/test_turn_bounds.py` |
| Change the repeat guards | `app/agent/graph.py` | `failed_before`, `succeeded_before`, `MAX_IDENTICAL_FAILURES`, `MAX_IDENTICAL_SUCCESSES`, `tests/test_repeated_failure.py` |
| Change what an empty or cut completion does | `app/agent/graph.py` | `silent_cut`, `nothing_to_add`, `output_cut_silent`, `finish_reason` |
| Change how a running turn is stopped | `app/agent/stop.py` | `StopRequests`, `MemoryStopRequests`, `PostgresStopRequests`, `asked_to_stop` |
| Change how a message sent mid-turn reaches the model | `app/agent/interjections.py`, `ui/telegram/interjections.py`, `inbox.take_pending` | `Interjections`, `MemoryInterjections`, `InboxInterjections`, `MessageTaken`, `tests/test_interjections.py` |
| Change whether a model result ends the turn | `app/agent/stopping.py` | `TurnStopping`, `Candidate`, `Steering`, `STOP_ON_ANSWER`, `tests/test_turn_stopping.py` |
| Change the plan tool or its switch | `app/tools/todo.py`, `app/agent/todo.py` | `todo_write`, `PLAN_SWITCH`, `planning_enabled`, `FinishesItsOwnList`, `tests/test_todo.py` |
| Change the goal the model writes first | `app/tools/goal.py` | `set_goal`, `DESCRIPTION`, `tests/test_goal.py` |
| Change the brief's wording (what the model is told about tools and method) | `app/capabilities.py`, `app/context/window.py` | `capability_brief`, `_planning_lines`, `_goal_lines`, `WORKING_METHOD`, `DEFAULT_SYSTEM_PROMPT` (names no tool; a test enforces it) |
| Change agent wiring | `app/agent/runtime.py` | `Agent`, `create_agent`, `toolbox`, `budget`, `rewire`, `_graph` |
| Change how an answer streams | `app/agent/graph.py`, `ui/telegram/adapter.py` | `complete`, `ASSISTANT_DELTA`, `AnswerPreview`, `AnswerWithdrawn`, `StreamedCompletion` |
| Change model-agnostic message types | `app/models/base.py` | `Message`, `ContentPart`, `ToolCall`, `Usage`, `ModelBackend` |
| Change request translation, usage, auth, redirects, dumps | `app/models/openai_compatible.py` | `OpenAICompatibleBackend`, `build_messages`, `_body`, `parse_usage`, `auth_headers`, `REDIRECT_HOPS`, `_dump`, `tests/test_openai_compatible.py` |
| Fix the served Gemma 4 parser (offline, not deployed) | `tools/gemma4_parser.py` | `extract_calls`, `parse_arguments`, `tests/test_gemma4_parser.py` |
| Change prompt layer order / the surface | `app/context/window.py` | `build_prelude`, `facts_layer`, `Context.surface`, `shortened`, `within_media_budget`, `ContextPolicy` |
| Change the person's context size | `app/context/choice.py`, `app/agent/runtime.py` | `CONTEXT_CHOICE`, `context_choice`, `Agent.budget`, `Agent.context_report`, `Agent.compact` |
| Read/write standing instructions | `app/instructions.py` | `AGENTS.md`, `read_instructions`, `instruction_message` |
| Change folding: when and how much | `app/context/summary.py`, `app/agent/graph.py` | `fold_older_messages`, `verbatim_floor`, `cut_for`, `keep_turns`, `fitted`, `context_folded` |
| Estimate a request's size | `app/models/base.py`, `app/models/openai_compatible.py` | `estimate_tokens`, `measure_request`, `_calibrate` |
| Change the store contract | `app/memory/base.py` | `ConversationStore`, `search_messages`, `active_thread` |
| Change SQLite / PostgreSQL persistence | `app/memory/store.py`, `app/memory/postgres.py` | `SqliteStore`, `PostgresStore`, `SCHEMA_VERSION`, `_opened`, `match_query` |
| Reach stored history from the model | `app/tools/history.py`, `app/tools/paging.py` | `search_history`, `read_history`, `page`, `offset` |
| Change graph checkpoints | `app/checkpoints.py` | `CheckpointHandle`, `setup_postgres_checkpoints` |
| Take up a turn a dead worker left | `app/agent/runtime.py`, `ui/telegram/adapter.py`, `ui/telegram/webhook.py` | `Agent.unfinished`, `resume_interrupted_events`, `Tool.replay_safe`, `same_request`, `LEASE_SECONDS`, `MAX_ATTEMPTS` |
| Measure a turn | `app/telemetry/` | `TurnTrace`, `Telemetry`, `TurnRun`, `TraceEvent`, `NO_TRACE`, `RUN_ID` |
| Read a turn back | `tools/show_run.py`, `app/telemetry/inspect.py` | `render_run`, `steps`, `--last`, `--failed`, `--summary` |
| GPU cost derivation (GPU Apps only) | `app/telemetry/cost.py` | `gpu_cost`, `IDLE_WINDOW_SECONDS` |
| Add/change a tool primitive or the execution path | `app/tools/base.py`, `app/tools/execution.py` | `Tool`, `ToolError`, `Toolbox`, `handover`, `ToolExecutor`, `pre_execute`, `execute`, `post_execute`, `tests/test_tool_outcomes.py` |
| Add a failure code | `app/models/base.py`, `app/tools/base.py`, the family module | `ToolFailure`, `Message.failure`, `fs.*`, `browser.*`, … |
| Add/change a capability/grant | `app/tools/capabilities.py` | `CapabilityRegistry`, `CapabilityGrant`, `DEFAULT_CAPABILITIES` |
| Change what the assistant says it can do, `/can` | `app/capabilities.py` | `system_message`, `capability_report`, `tool_inventory` |
| Change filesystem tools | `app/tools/filesystem.py` | `resolve_in_root`, `filesystem_tools` |
| Change attachment admission | `app/attachments.py` | `admit_uploads`, `load_attachments` |
| Change document parsing / tools | `app/documents.py`, `app/tools/documents.py` | `read_sections`, `render_pages`, `read_document`, `view_pages` |
| Change file delivery | `app/tools/presentation.py` | `send_file`, `outbound=True` |
| Run a command or change where commands run | `app/tools/shell.py`, `app/tools/shell_windows.py`, `deploy/modal/control_app.py` | `run_command`, `Runner`, `LocalRunner`, `ContainerRunner`, `ModalRunner`, `command_environment`, `ensure_venv`, `RestrictedProcess`, `BASE_TOOLS`, `tests/test_run_command.py` |
| Change the two modes | `app/agent/mode.py`, `app/tools/base.py` | `CAREFUL_SWITCH`, `set_mode`, `Tool.mutates`, `Toolbox.ask_for_changes` |
| Change local page inspection | `app/tools/browser.py` | `inspect_page`, `page_report`, `observe`, `inspect_local_page` |
| Change the browser session, snapshot, actions | `app/tools/chromium.py` | `BrowserSession`, `open_browser`, `serve_directory`, `format_snapshot`, `DEVTOOLS_READY_SECONDS`, `tests/test_browser_session.py` |
| Change public web networking/security | `app/web.py` | `check_destination`, `fetch_page`, `search_web`, `render_locally`, `render_remotely`, `public_request_policy` |
| Change model-facing web tools | `app/tools/web.py` | `search_web`, `fetch_page`, `view_web_page` |
| Change Telegram behaviour / commands | `ui/telegram/adapter.py` | `TelegramAdapter`, `_on_message`, `_deliver`, `_on_callback`, `plan_lines` |
| Change which updates skip the queue | `ui/telegram/wire.py` | `travels_out_of_band`, `needs_model`, `MODEL_FREE_WITH_ARGUMENTS` |
| Change Telegram rendering / Bot API | `ui/telegram/markdown.py`, `ui/telegram/api.py` | `render`, `TelegramClient`, `PRODUCT_COMMANDS`, `retry_after` |
| Change the deployed handoff / inbox / polling | `ui/telegram/webhook.py`, `ui/telegram/inbox.py`, `ui/telegram/run.py` | `TelegramWebhook`, `TelegramUpdateWorker`, `DRAIN_SECONDS`, `PostgresUpdateInbox`, `_claim_conversation`, `PollingBot` |
| Change Chainlit | `ui/chainlit_app.py`, `ui/chainlit_history.py` | `create_runtime_with_stops`, `MemoryStoreDataLayer` |
| Change the deployed control plane | `deploy/modal/control_app.py` | `telegram_webhook`, `process_telegram_update`, `render_web_page`, `run_command`, `scenarios`, the images |
| Change a model App | `deploy/modal/model_app.py`, `model_app_qwen.py`, `model_app_qwen_int4.py` | `Server`, `fetch_weights`, `preflight`, `dry_run`, `fits`, `SCALEDOWN_WINDOW` |
| Change the GPU idle window without a deploy | `deploy/modal/autoscale.py` | `update_autoscaler` |
| **Publish the control secret** | **`tools/sync_control_secret.py`** | **`ALLOWED`, `MODEL_SET`, `DEPLOY_WEB_RENDERER_URL`** |
| Migrate the deployed database | `tools/setup_control_plane.py` | `setup_control_plane` |
| Telegram webhook / bot profile | `tools/telegram_webhook.py`, `tools/telegram_profile.py` | `setWebhook`, `--delete`, `--publish` |
| Work journals | `tools/work_log.py` | `reports/agent_tasks.jsonl`, `reports/ml_work.jsonl` |
| Run the live scenarios | `scripts/loop_live.py` | scenarios A–K, O–S, `--after-deploy`, `--deployed`, `run_scenarios`, `Turn` |
| Compare prompts on fixed scenarios | `tools/prompt_scenarios.py` | `SCENARIOS`, `--dry-run`, `--goal off`, `--prompt-file` |
| Render a stored conversation as a page | `tools/showcase.py` | `render_thread`, read-only |
| Measure a GPU App: wake, engine baseline, command cold start | `scripts/measure_endpoint_wake.py`, `tools/vllm_baseline.py`, `scripts/measure_command_cold_start.py` | each starts a container: permission |
| Diagnose a local install | `scripts/doctor.py` | |

## Repository shape

```text
app/
  agent/       the loop, its wiring, budget, stop, stopping seam, mode, todo extension
  context/     prompt assembly, surface, folding, the person's context choice
  memory/      store contract + SQLite / PostgreSQL
  telemetry/   turn records, traces, inspector, GPU cost derivation
  models/      model contract + the OpenAI-compatible adapter
  tools/       tools, capabilities, execution, browser, shell, history, goal, todo
  attachments.py capabilities.py checkpoints.py config.py documents.py instructions.py preflight.py web.py
  api/         empty stub
ui/telegram/   wire, markdown, api, adapter, inbox, webhook, run
ui/chainlit_app.py, ui/chainlit_history.py
deploy/modal/  control_app, model_app, model_app_qwen, model_app_qwen_int4, autoscale
tools/         operational tools (not imported by app/)
scripts/       live runners, measurements, doctor, migration
tests/         offline suite (74 files)
docs/          the four maps, v2_tool_system.md (the tool contract), earlier notes
reports/       evidence and the two journals
```

## Ownership rules worth knowing

- **`app/config.py` owns configuration.** No `os.environ` or file reads
  elsewhere. Settings come from `config.toml` (committed, in the image);
  secrets from `.env` and the platform secret; the environment wins over the
  file. Families: `MODEL_*` (and `MODEL_<SET>_*`), `AGENT_*`, `TELEGRAM_*`,
  `WEB_*`. A deployed name may differ from the local one on purpose
  (`DEPLOY_WEB_RENDERER_URL` is published as `WEB_RENDERER_URL`).
- **`app/tools/capabilities.py` versus `app/capabilities.py`:** the first is
  wiring (grant to toolbox), the second is truth (the brief and `/can` read
  from the actual toolbox, admission and delivery). Do not merge them.
- **Web is split three ways:** `app/tools/web.py` (tools), `app/web.py`
  (validation, transport, render routing), `app/tools/chromium.py` (the
  browser). No second URL validator or launcher.
- **Documents are split:** `app/attachments.py` decides, `app/documents.py`
  parses, `app/tools/documents.py` exposes, `app/tools/presentation.py`
  sends.
- **Telegram is split:** `wire.py` is standard-library only and tested for
  it; do not move agent imports into it.
- **Telemetry is not conversation persistence.** Separate tables, no message
  text; the recorder is looked up by `run_id` through `Telemetry.trace()`,
  never put into graph state. `AgentState.tool_calls` (spent against the
  budget) and `TurnRun.tool_calls` (executed) are different questions.
- **A capability name is not a tool name.** The model is told tool names.
- **Both profiles serialize a conversation** by different means: a per-chat
  lock locally, a database lease deployed (`_claim_conversation` is the
  ordering guarantee).
- **A control signal is not an ordinary update:** marked at the front door,
  it skips the lease; the loop reading `StopRequests` at its next step is
  what ends the turn.
- **Deployment calls app code**, never reimplements it; `app/` never imports
  `deploy/`.

## Tests

Start with the test named after the owner (`tests/test_<owner>.py`), then
run the offline suite. Useful searches:

```text
rg "CapabilityRegistry|capability_brief" tests
rg "send_file|outbound" tests
rg "TurnWatch|StopRequests" tests
rg "OpenAICompatibleBackend|build_messages|extra_body" tests
rg "PostgresStore|ConversationStore" tests
rg "TelegramWebhook|PostgresUpdateInbox" tests
```

Offline tests never call a model endpoint, a network service or a credential
(`AGENTS.md`); the Postgres contract suite runs only under
`AGENT_TEST_DATABASE_URL`.

## Cheap exploration recipe

1. Find the intent row above and open the owner.
2. Search the named symbols and env keys; read the directly related test.
3. Read `PROJECT_MAP.md` when the change crosses boundaries,
   `OPERATIONS_MAP.md` when it touches deployment, config, secrets or state.
4. Read `ROADMAP.md` before treating proposed work as approved, and the
   `DECISIONS.md` entry before changing a durable boundary.
5. Use `reports/` only for evidence on the exact question.
