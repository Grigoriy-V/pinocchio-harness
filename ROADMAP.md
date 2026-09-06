# Roadmap

**Updated:** 2026-09-04

**Project status:** Version 1.5 closed; Version 2 in progress

**Current approved step:** 5, isolated execution deployed on Modal, selected
by the human 2026-09-04 with item 4 closed as a whole. The tool, the modes
and the local runner were built the same day as its local half and then set
apart as item 7, a stage of its own, on the human's word; 5 is the deployed
runner and the deploy, and its start is a separate signal.

**Before changing media delivery**, read
`reports/2026-08-29_v2_capabilities_browser_workspace_documents.md`, section
"Correction after the last live test": automatic delivery of media returned by
any tool was rejected product behaviour and its replacement is what is deployed.

Observed defects are in `ISSUES.md`, which is not a plan and authorizes nothing.

This is the only source for current product direction, state, order and approved
work. The human approves one step before implementation.

`docs/PRODUCT.md` is the stable product contract; `docs/PROJECT_MAP.md`,
`docs/CODEMAP.md` and `docs/OPERATIONS_MAP.md` describe the current system,
ownership and operations. `AGENTS.md` holds execution rules. `DECISIONS.md`
preserves approved durable choices and their rationale. This file alone owns
current work, order and authorization. `README.md` and `chainlit.md` are display
documents.

## Current state

- Both databases at schema version 3 (2026-09-03); conversations, memory and files scoped by
  user, and the conversation each person is in stored as their own choice.
  Deployed database is **Neon**, reached through its pooled endpoint.
- `assistant-llm-v2` at
  `https://grigoriy-v--assistant-llm-v2-server-serve.modal.run` is the primary
  model deployment, reached with `MODEL_AUTH_STYLE=modal_proxy`. Its context
  ceiling is 65,536 since 2026-08-30, at 0.80 utilization and unquantized KV.
  The original `assistant-llm` stays deployed as rollback only; retiring it is a
  destructive human gate.
- `assistant-llm-qwen` at
  `https://grigoriy-v--assistant-llm-qwen-server-serve.modal.run`
  (`deploy/modal/model_app_qwen.py`, since 2026-09-05) serves
  Qwen3.8-27B-FP8 on an L40S at a 131,072 ceiling, 0.90 utilization,
  unquantized KV, as the second model; not yet the one the assistant uses.
- `assistant-control` also serves `run_command` (the command runner, no
  secret) and `scenarios` (the live scenarios in the worker's own
  environment, `loop_live --deployed`), since 2026-09-04/05.
- `assistant-control` serves the Telegram webhook and the update worker. Idle
  windows: 60 s on both CPU functions, 12 s on the GPU. The GPU value is live
  through `deploy/modal/autoscale.py` and matches `SCALEDOWN_WINDOW`, so a
  deploy restores it. A third function, `render_web_page`, is deployed at
  `https://grigoriy-v--assistant-control-render-web-page.modal.run` behind proxy
  auth. It has run both in the deployed self-test and in a real Telegram turn.
  The web keys are in the `assistant-control` secret, published from the owner's
  own `.env` by `tools/sync_control_secret.py`.
- The NCCL loopback rendezvous went in with the 2026-08-30 ceiling boot and the
  warning storm is gone: no `Broken pipe` line at or after that boot, where the
  previous revision produced one a second for a container's whole life.
  `reports/2026-08-28_v2_step3b_nccl_snapshot_warnings.md`,
  `reports/2026-08-30_v2_context_capacity.md`.

## Closed stages

- Stage 1 — multimodal smoke: `reports/2026-08-01_stage1_smoke_script.md`.
- Stage 2 — minimal LangGraph agent: `reports/2026-08-01_stage2_agent.md`.
- Stage 3 / Version 1 — the persistent local multimodal product:
  `reports/2026-08-01_v1_product_smoke.md`.
- Version 1.5 — general autonomous harness, with a known 16,384-token boundary:
  `reports/2026-08-02_v15_product_acceptance.md`, screenshots in
  `reports/test_v1.5/`, per-step evidence in `reports/2026-08-0[12]_v15_step*.md`.

## Version 2 — Deployable personal assistant

**Outcome:** the same harness serves a small number of people as a practical
assistant over Telegram, deployed serverless so that no GPU runs while idle,
while remaining fully usable as a local agent on the human's own machine.

The canonical product and current system are described by `docs/PRODUCT.md` and
`docs/PROJECT_MAP.md`; operational ownership is in `docs/OPERATIONS_MAP.md`.
Verified platform facts and cold-start evidence remain in
`docs/modal_platform_notes.md`, `docs/modal_vllm_cold_start.md` and
`docs/control_plane_cold_start_notes.md`.

### Done

What exists. How it was reached, and every number, is in the linked report.

- **Persistence contract** — `ConversationStore`, `SqliteStore`, per-owner
  scoping, one contract suite over every implementation, `user_version`
  migrations. `reports/2026-08-27_v2_step1_store_contract.md`.
- **Telegram adapter** — derived identity, allow list empty by default,
  transport isolated. `reports/2026-08-28_v2_step2_telegram_adapter.md`,
  `reports/2026-08-28_v2_step3b_telegram_live_acceptance.md`,
  `reports/2026-08-28_v2_telegram_voice_and_media_budget.md`.
- **Model endpoint, then optimized** — Gemma 4 12B on an A10 through vLLM, with
  snapshots, scale to zero and callers refused at the edge.
  `reports/2026-08-28_v2_step3a_model_endpoint.md`,
  `reports/2026-08-28_v2_step3b_*.md`.
- **Capability honesty** — the assistant describes itself from its own wiring;
  `/can` answers without a model call, `/check` tries each capability.
  `reports/2026-08-28_v2_capability_honesty_and_telegram_shape.md`.
- **Control plane, accepted live** — webhook, checked secret and allow list,
  Neon inbox, spawned CPU worker, harness, GPU wake, reply, with nothing on the
  human's machine. Polling retired; `PostgresStore` joins the contract suite
  under `AGENT_TEST_DATABASE_URL`. `reports/2026-08-28_v2_control_plane_*.md`.
- **Database latency gate withdrawn** — the probe is an instrument, not
  acceptance. `DECISIONS.md` 2026-08-28,
  `reports/2026-08-28_v2_control_plane_database_latency_probe.md`.
- **Cold start reduced** — the agent stack is off the webhook's import path and
  the webhook starts the model waking for updates that need one.
  `reports/2026-08-29_v2_control_plane_cold_start.md`.
- **1. Baseline capabilities, accepted live** — persistent per-user workspace,
  filesystem tools, document text and visual reading, isolated browser, web
  search/fetch/view, agent-controlled file delivery.
  `reports/2026-08-29_v2_capabilities_browser_workspace_documents.md`,
  `reports/2026-08-29_v2_web_capability.md`.
- **2. Baseline chat product, accepted live** — onboarding, Telegram Markdown
  with a plain fallback, transient tool activity, truthful inline settlement,
  and conversation selection as a stored choice (`/new`, `/chats`) behind an
  additive schema-2 migration.
  `reports/2026-08-29_v2_baseline_chat_product_offline.md`,
  `reports/2026-08-29_v2_conversation_selection.md`.
- **2. Real answer streaming, accepted live** — the model call streams through
  the graph, only finished messages are stored, and `AGENT_STREAM_ANSWERS`
  turns it off. `reports/2026-08-29_v2_answer_streaming_*.md`.
- **3. Baseline measurement, metrics and logs, closed** — a turn is one `run_id`
  from ingress to delivery, carrying no message text; `tools/show_run.py` reads
  one run and reports GPU active seconds per successful turn, labelled as
  derived. The engine baseline is measured.
  `reports/2026-08-29_v2_turn_telemetry_implementation.md`,
  `reports/2026-08-29_v2_run_inspector_implementation.md`,
  `reports/2026-08-29_v2_gpu_baseline_measured.md`.

- **Isolated execution, deployed** — `run_command` as a Modal Function
  beside the renderer: the worker's image plus base tools, fonts and the
  everyday libraries, the workspaces Volume, no secret, 180 s scaledown;
  cold container 8 s, warm under a second; the Volume round trip both
  ways. `read_file` shows a picture; a non-zero exit carries the harness's
  line; the goal is the request's parts written once by the model
  (`set_goal`, `DECISIONS.md` 2026-09-05). Scenarios O–S, and all sixteen
  run deployed through the `scenarios` Function with the goal in the
  toolbox: sixteen of sixteen pass except G's variance and P's instrument
  check. Five harness defects found by refusing "it's the model"
  (ISS-0041–0043, 0045, the fonts), and the goal check of §14 built,
  measured out and removed. `reports/2026-09-04_v2_isolated_execution_review.md`.

### Queue

4. **Agent harness and loop — closed 2026-09-04.** One loop, one tool
   path, a real context window, a browser that looks, resume across a dead
   worker, and a scenario suite that accepts on events rather than wording.
   Outcomes, each with its report:
   - 4.0 the lease belongs to the conversation and the worker drains it in
     order — `reports/2026-08-30_v2_conversation_serialization.md`;
   - 4.1 one loop with a `TurnBudget` and an out-of-band stop; the router and
     the plan/implement/test lifecycle are gone —
     `reports/2026-08-30_v2_one_loop.md`;
   - 4.1.5 the request is estimated before every model step and folds only
     over budget — `reports/2026-08-30_v2_context_capacity.md`;
   - 4.2 one `pre_execute -> execute -> post_execute` path for every tool,
     consent policy and telemetry inside it; a sandbox plugs in as another
     backend — `reports/2026-08-30_v2_tool_execution_seam.md`;
   - 4.3 stop by default, continue only through explicit structured
     steering; the seam's one extension, the plan objection, is off —
     `reports/2026-08-30_v2_turn_stopping.md`;
   - 4.3.5 the prompt assembled in order of stability, the person's own
     `AGENTS.md` as a prompt overlay, the scenario runner as the instrument
     — `reports/2026-08-30_v2_prompt_assembly.md`;
   - 4.4 `todo` as agent state, off unless the person turns it on —
     `reports/2026-08-31_v2_todo_live_failure.md`;
   - 4.5 typed tool outcomes, the executor owning validation, bounds,
     timeout and the model projection — `docs/v2_tool_system.md`,
     `reports/2026-09-03_v2_tool_system_implementation.md`;
   - 4.5.5 one `BrowserSession` with the full action set, observation alone
     exposed as `inspect_page` — `reports/2026-09-03_v2_browser_session.md`;
   - 4.6a context preparation before every model step, results shortened by
     age, the last two exchanges verbatim, a fold taking only what has to go
     — `reports/2026-09-03_v2_context_engine_review.md`;
   - 4.6b full-text recovery of what was actually said, `search_history` and
     `read_history` — `reports/2026-09-03_v2_history_recovery_review.md`;
   - 4.7 a turn a dead worker left is taken up from its checkpoint, reading
     tools re-run and the rest reported unknown; `scripts/loop_live.py` A–K
     — `reports/2026-09-04_v2_restart_resume_review.md`;
   - 4.9 saying only what was observed stays measurement, not a mechanism:
     the proposed checks were withdrawn as a script of past defects and the
     rule is in `AGENTS.md` — `reports/2026-09-04_v2_observed_claims_review.md`.

   Durable choices: `DECISIONS.md` 2026-08-30 through 2026-09-04. Left open
   as observations in `ISSUES.md`, to be fixed where the next work meets
   them: a page described from its address alone, a path offered beside a
   real send, an application called working without a look (browser actions
   exist and are not exposed), the memory layer that says nothing when its
   keyword retrieval finds nothing. `ask_user` and the `todo` follow-up are
   in Not started.

5. **Isolated execution, deployed (Modal) — closed 2026-09-05.** Outcome
   under Done. Two things left it as their own items: the local profile
   (7) and the plan/goal comparison (8).

6. **Optimization after the agent is observable.** Adaptive scaledown through
   `autoscale.py`. Prefix caching is confirmed active and needs no work before
   it is used deliberately; speculative decoding is the weakest lever, because
   prefill dominates long turns. `reports/2026-08-29_v2_gpu_baseline_measured.md`.
   Its first case, observed 2026-09-05: the model sleeps inside a turn
   whenever a tool outlives the 12 s window and the next call pays a
   restore (ISS-0054); the smallest form of the adaptive window is to keep
   the endpoint warm while a turn's tool runs, and nothing between turns.

7. **The local profile as a place to work — open, separate, not scheduled.**
   Working on the person's own files on their own machine, with the local
   UI: a stage with its own problems, opened by 5b and set apart from the
   sandbox on the human's word 2026-09-04. **Built and kept** (was 5b, done
   2026-09-04): `run_command` over a one-method `Runner`; a process in the
   workspace with the agent's environment withheld, killed with its tree at
   the deadline; the two modes and `/mode` in Telegram; on Windows a
   write-restricted token so a command writes only inside the workspace
   (`app/tools/shell_windows.py`, DeepSeek Harness's mechanism, four
   undocumented conditions found on this machine; report §10); console
   output decoded by its code page. Offline 24 shell tests and one adapter
   test; live O and Q passed, P passed twice and then missed its look-and-
   send on the model's side (ISS-0039). **Open, recorded, not approved:**
   the automatic workspace venv is a rule about one toolchain and hides the
   machine's own packages — to be removed, with the CPython 0o700
   accommodation moved to a `PYTHONPATH` `sitecustomize` (report §11); no
   way to choose the project folder in the UI, the workspace is
   `AGENT_WORKSPACE/<user>`; Chainlit has no `/mode` or `/plan`; the
   boundary's partial cases (Everyone-writable places, hard links); a
   non-Windows local profile has no boundary; the careful mode asks on
   every change, and OpenClaw's "allow always" (report §3) is the option
   once the mode is used. Order relative to 5 and 6 is the human's call.

`app/api/` stays deferred: Telegram runs in-process, so an HTTP layer would have
no separately hosted caller. The trigger is a UI hosted apart from the
application; see the amended FastAPI decision in `DECISIONS.md`.

8. **The plan and the goal together — open, a comparison to run, 2026-09-05.**
   With `/plan on` the model is offered both `todo_write` and `set_goal`,
   and nothing makes one stand down for the other; every goal measurement
   so far ran with the plan off. The question the human left open: whether
   the plan should replace the goal when it is on, or both may stand.
   Decided by a measurement, G and P deployed under `/plan on` with both,
   against the runs of 2026-09-05 with the goal alone
   (`reports/2026-09-04_v2_isolated_execution_review.md` §15).

9. **A second model: Qwen3.8-27B in FP8 on an L40S — selected 2026-09-05.**
   Its own App, `assistant-llm-qwen` (`deploy/modal/model_app_qwen.py`),
   beside `assistant-llm-v2`: 128k ceiling in bf16 KV, utilization 0.86 on
   the human's word, thinking at `low`, `qwen3_xml`/`qwen3` parsers; the
   assistant switches by `MODEL_ENDPOINT` and `MODEL_NAME`. Why this
   checkpoint and card, the arithmetic and the published quantization
   benchmarks: `reports/2026-09-05_qwen38_second_model.md`. Four boots to
   a served endpoint: 0.86 refused (7.04 GiB of KV against 8.18 needed),
   0.90 on the human's word, `max_num_seqs` 16 for the DeltaNet state
   blocks, and the compile-cache Volume committed before the snapshot
   (ISS-0047). Measured: 9.75 GiB of KV, 155,600 tokens, 1.19x at 131,072;
   restored wake 88.5 s; text, image and a parsed tool call with thinking
   at `low`. The scenarios ran once (report §7): A–F answered, G wrote
   8,334 tokens in one call and no tool, the first G was killed in the
   worker, and the store's idle connection failed the turn (ISS-0048,
   fixed). On the human's word the same model in INT4 on an A100-40GB is
   a third App, `assistant-llm-qwen-int4` (`model_app_qwen_int4.py`), for
   a snapshot half the size; its `preflight` refuses on CPU a ceiling the
   pool cannot hold, and `dry_run` boots it once with no request behind
   it (report §8–§9). Deployed and serving: restore 20–31 s on three
   samples; A B C E F R S pass on it, F's one failed check being the
   renderer's cold browser (ISS-0051, fixed); a turn is 2–3.5x Gemma's
   (report §10). Found and fixed on the way: ISS-0044, ISS-0047, ISS-0048,
   ISS-0051; open: ISS-0049 (platform restarts), ISS-0050 (AOT off).
   Read after: prefix caching is opt-in for hybrid models in vLLM 0.26.0
   and was off, so every call re-prefilled ~5k tokens (~3 s); the study
   of what else is faster is report §11. FP8 is not run again (the
   human's word, cost). On the human's word: the Qwen Apps move to vLLM
   0.28.0 / transformers 5.15.0, prefix caching on, thinking off by
   default with `MODEL_CHAT_TEMPLATE_KWARGS` as the dial; `dry_run` is
   not a required step. Deployed and measured (report §12): the seven
   scenarios all pass, B 4.1 s, C 6.6, E 5.9, F 11.3, R 26.6, S 14.4,
   against 10.9/15.8/14.2/28.0/40.0/27.7 before, with `cached 4704` on
   every call after a turn's first. The rest ran on 2026-09-05 too:
   fifteen of sixteen pass on INT4 (report §13); G does the work and
   loses the handover in a repeat loop with thinking off. Found on the
   way, all fixed: the edge's 303 after 150 s that the client never
   followed (ISS-0044's cause), a mid-thread system message the template
   refuses (ISS-0052). G with thinking off: two scenario runs failed on
   the model's own route (a repeat loop; a browser built by hand with
   `npm` and `apt`, during which the model slept three times behind the
   12 s window) and the human's own live run of the same request passed
   in 75 s with `inspect_page` and four `send_file`. The human's reading,
   2026-09-05: thinking is not the answer (2.5x Gemma's cost already);
   the route the model missed is the harness's to make obvious — items
   10–12. The FP8 App is not run again (cost); MTP stays a separate
   measurement, not scheduled.

10. **The scenario suite, reconsidered — next, the human's word 2026-09-05.**
    The suite does not do what it is for: checks that assert a route
    (`write_file then inspect_page` in F and G) against the suite's own
    rule; a runner whose long-running turns let the model sleep and pay
    restores inside the turn (G: three model containers in one turn);
    results that arrive only when the whole batch ends, so a crash loses
    every summary; a batch that dies with its container. What to keep,
    what to change and why, as a report first; then the changes.

11. **Tools as the references have them.** Every tool's description
    states what it takes, what it returns and what it leaves where, and an
    offline test refuses a tool without all three, the way one already
    refuses a tool without a Telegram label. The difference from Hermes
    and DeepSeek Harness read tool by tool, in a report, before the
    rewrite.

12. **One way to look at a page: `inspect_page` removed.** `view_web_page`
    already opens a real browser, returns the text and a screenshot, and
    says the screenshot's workspace path; it takes a workspace path as
    well as an address, returns the structure with refs too, and
    `inspect_page`, its brief line and the scenario checks that name it
    go. No new tool, no new name.

13. **The model chosen from Telegram, and a default model — tiers
    chosen 2026-09-06.** Model sets exist (`MODEL=<name>`, every set in
    the secret; DECISIONS 2026-09-06). The human's tiers among hosted
    models: default Gemini 3.1 Flash-Lite, stronger Gemini 3.5 Flash-Lite,
    low-price GLM 5.3 Flash. Later the same day, after B and C on
    OpenRouter (report §11): the deployment runs on OpenRouter, not
    CometAPI, and **the default is GLM 5.3 Flash served by Novita (fp8),
    Z.ai as the fallback**, thinking off and `reasoning_effort: low`
    (the human, after the four-host comparison of report §12: ~5 s per
    call, the cache on every repeat, $0.00007 a call). Gemini is paused
    until its cache can be made to land. Next, each on the human's word: (a) Gemini
    3.1 Flash-Lite with thinking against without, B and G; (b) a thin
    adapter for Google's native request format, so Gemini's cache can be
    made to land (explicit caching is native-only; report §8); (c) GLM
    5.3 Flash from a provider that streams it (OpenRouter lists Baseten
    0.5 s / 150 tok/s, DeepInfra 1.8 s / 41 tok/s at $0.075; report
    §10); (d) the Telegram command that switches between published sets.

Order: 10, 11, 12 together are one cohesive change to how the model is
told what it can do and how that is measured; 13 after. The analysis and
the proposal for the three: `reports/2026-09-05_suite_and_tools_review.md`,
awaiting the human's word on the contract shape, the merged tool's
parameter and G's checks. The command environment of ISS-0053 is
recorded, not scheduled.

### Not started

Recorded, not approved, not begun, and not in the order above. One line each;
an observed defect is described in `ISSUES.md`, not here.

- **An API-served model as a fourth choice, the human's word 2026-09-05.**
  One of the next experiments: point the assistant at a hosted
  OpenAI-compatible model instead of a GPU App, first candidate CometAPI's
  `glm-5.3-flash` (1M context, tools, images, thinking; $0.06/$0.20 per 1M
  in/out). The client already speaks bearer auth (`MODEL_API_KEY`,
  `MODEL_AUTH_STYLE=bearer`), so it is a secret change and a control
  deploy, no GPU; what has to be checked is the served context limit (the
  client reads `/v1/models`), the thinking dial and the tool-call format,
  then the same scenarios. Fits item 13's list of model choices. **Begun
  2026-09-06:** model sets (`MODEL=comet` reads `MODEL_COMET_*`), the
  deployment on GLM, scenario B passed (39.7 s, cache hits passed through
  and discounted; `reports/2026-09-06_hosted_model_cometapi.md`). Four
  candidates named: GLM 5.3 Flash, Qwen3.8-Flash-Next, DeepSeek V4 Flash
  Vision, Gemini 3.1 Flash-Lite. The suite on GLM the same day: 13 of 15
  pass, $0.0081 for fifteen turns, 3–20x slower per turn than a warm
  INT4 and no cold start; G spends its cap on reasoning (ISS-0055), P
  checks the PDF without looking (ISS-0040). Thinking flags reach GLM
  through CometAPI; with them G took the brief's route and lost to the
  seconds budget: CometAPI delivers a GLM response whole after 13–100 s,
  not streamed (report §5–§7). The raw stream can now be kept
  (`MODEL_DUMP_DIR`). Found on the way, for items 11–12: the model builds
  its own browser because the page tool returns refs and no click.
  Gemini 3.1 Flash-Lite through the same set: 14 of 16, G passes all its
  checks, 1.5–3 s per call, $0.0585 for the suite, cache rarely hit and
  not discounted (report §8).
- **The whole-code review of 2026-09-03**, thirteen findings ranked and an
  order proposed: `reports/2026-09-03_v2_whole_code_review.md`. Items 1 and 2
  of its order (§2.1–2.4: the summarizer reads stubs, a fold cannot fail a
  turn, folding by size, a cut call named, identical successes bounded) were
  built and deployed 2026-09-04 on the human's word; the rest is not approved.
- **Finish the `todo` tool.** What 4.4 left: a live turn where a plan and the
  finished work arrive together, the wording split into when a list is worth
  opening and how coarse its items are, and a turn ending on an item the model
  does not want to close. `reports/2026-08-31_v2_todo_live_failure.md`.
- **Let a plan be corrected by the person**, who can currently only read it.
- **`ask_user`** for a genuinely missing decision, not for permission. Was
  4.8 until 2026-09-04; a feature rather than architecture, so it waits for
  the base harness. It returns through the same interrupt seam consent
  uses.
- **Hand over what was made.** Delivery is `send_file` and the model reaches for
  a Markdown link instead. `ISSUES.md` ISS-0003.
- **Latency to the first visible word**, to give 4.1 a "before" number.
  `reports/2026-08-30_v2_first_visible_latency_handoff.md`.
- **Throttle the edits that write a streamed answer.** How often to edit is a
  measurement, not a constant to pick.
- **Keep a picture someone sends.** Saving the file is the easy half; when the
  model is shown the image and when it is shown a filename is the design.
  `ISSUES.md` ISS-0002.
- **Answer a Telegram album as one turn.** It means a turn whose identity is not
  one update, which 4.0 held back deliberately, and waiting out an album with no
  end marker. `reports/2026-08-30_v2_album_burst_incident.md`.
- **Show the preview only when it will survive.** `ISSUES.md` ISS-0009.
- **Put the reason in `tool_failed`.** `ISSUES.md` ISS-0007.
- **The local interface as a product path.** Chainlit and the agent on the
  person's machine, the model on Modal: a second way to use the same
  assistant, beside Telegram. Confirmed as a product path on 2026-09-03 and
  deferred by the human until the base harness is done; the adapter is
  covered by tests only since the 4.5 changes and has not been run live.

**Closing criterion:** through Telegram, a normal conversational request is
answered and a work request completes end to end for two different users without
either seeing the other's conversations or memory, with no GPU running while the
assistant is idle — and the same `app/` still serves the local profile.

## Out of scope

Fine-tuning, multi-agent orchestration, a vector database before text retrieval
works, Open WebUI as the main UI, and the superseded policy-platform/MCP version
of Version 2. Changing scope requires an edit here, and a `DECISIONS.md` entry
when the change is architecturally durable.

**A different endpoint for 128k**, recorded 2026-08-30: became item 9 on
2026-09-05, with Qwen3.8-27B instead of Qwen3-8B and no KV quantization,
because the newer model's hybrid attention makes 128k fit in bf16. 128k on
the A10 stays an open question rather than a settled no. `DECISIONS.md`
2026-08-30, 2026-09-05.

## How this file is kept

- **Only approved work.** A conclusion the human has not approved in words is a
  draft and belongs in `reports/`, not here. See `AGENTS.md`, Records.
- **State and order, not reasoning.** No options, comparisons, prices or
  research. Those go to `reports/`; durable architecture goes to `DECISIONS.md`.
- **One entry per item, a few lines, plus links.** Evidence lives in the report
  it links to and is not summarized twice.
- **Done is a list of outcomes**, not a history of how they were reached.
- **Queue is an order, not a list.** Unfinished work returns as its own queue
  item instead of staying as a caveat inside a closed one.
- **Short beats complete.** If this file needs a table of contents, cut it.
