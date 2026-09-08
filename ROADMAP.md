# Roadmap

**Updated:** 2026-09-07

**Project status:** the assistant is deployed and used over Telegram on a
hosted model. The stage that begins now is cleaning defects and polishing
the base: the model is cheap, so the seconds to fight are the harness's
own, and what a tool tells the model it does. The order below was approved
by the human on 2026-09-07; each item still gets its own start signal, and
research before code where the item says so.

**Current approved step:** 14, 15 and 20 are in Done (2026-09-07). 16 researched (`reports/2026-09-07_item16_research.md`), the page tool's shape approved; 17 absorbs the folder-per-task rule. 16 deployed and run on both sides, in Done. 21 added (one browser tool on the renderer; ISS-0060 is the breach it closes). 22 deployed and seen live (the worker outlives the turn, a heartbeat lease); it uncovered a `persist` that hangs for the worker's whole life (ISS-0064); 23 deployed and seen live (connection bounds, a sent file kept by name). 17 built (home, temp and the workspace are three places; a folder per task), local check, deploy and deployed check pending. Next: 21, on the human's word.

Observed defects are in `ISSUES.md`, which is not a plan and authorizes
nothing. `docs/PRODUCT.md` is the product contract (it carries the rule that
media is delivered only by an explicit `send_file`, never by the adapter);
`docs/PROJECT_MAP.md`, `docs/CODEMAP.md` and `docs/OPERATIONS_MAP.md`
describe the system, ownership and operations; `AGENTS.md` holds execution
rules; `DECISIONS.md` preserves approved durable choices. This file alone
owns current work, order and authorization.

## Current state

- **Model:** GLM 5.3 Flash through OpenRouter, Novita first, Z.ai as the
  fallback, thinking off (`MODEL=or`; DECISIONS 2026-09-06). Model sets
  switch the whole deployment by one line, `MODEL=<name>`; every set is in
  the control secret. The three GPU Apps (`assistant-llm-v2` Gemma 4 12B,
  `assistant-llm-qwen` and `assistant-llm-qwen-int4`) stay deployed as
  sets, scaled to zero, not in use.
- **Control plane:** `assistant-control` on Modal serves the webhook, the
  update worker, `render_web_page`, `run_command` and `scenarios`; the
  database is Neon at schema version 4; secrets are published from the
  owner's `.env` by `tools/sync_control_secret.py`.
- **Local profile:** the same `app/` runs on the owner's machine with
  Chainlit; the boundary for commands exists on Windows only (item 7).

## Done

Versions 1–1.5 (local multimodal product, autonomous harness) and Version
2 (deployable personal assistant over Telegram, serverless, no GPU while
idle) are closed; the reports under `reports/` carry the evidence, one per
step. The last stages closed: the agent harness and loop (2026-09-04,
`reports/2026-08-30_v2_one_loop.md` and the 4.x reports), isolated
execution deployed (2026-09-05,
`reports/2026-09-04_v2_isolated_execution_review.md`), the second model
Qwen3.8-27B as GPU Apps (2026-09-05,
`reports/2026-09-05_qwen38_second_model.md`), and the hosted model with
model sets and the OpenRouter default (2026-09-06,
`reports/2026-09-06_hosted_model_cometapi.md`).

Items of the 2026-09-07 order, closed:

- **14, the turn bounded by health** (2026-09-07): no step or tool-call
  ceiling; a health check between steps after a set time; a fold only when
  the request would not fit; one provider, the next only after retries
  failed; settings in `config.toml`. Deployed, seen live on B and G.
  `reports/2026-09-07_turn_bounds_context_provider.md`.
- **15, the mini scenario set** (2026-09-07): eight scenarios, bare on both
  sides, `--local`/`--deployed`/`--both`; 16/16 on the first run; the
  after-deploy check. `reports/2026-09-07_mini_set.md`.
- **20, a message in the middle of a turn** (2026-09-07): taken at the tools
  boundary as the person's words, memory lane locally, the inbox deployed;
  seen live. `reports/2026-09-07_mid_turn_message.md`.
- **16, tools with contracts, a page with hands, the prompt reviewed**
  (2026-09-07): every tool states what it does, returns and leaves, an
  offline test refuses one that does not; `use_page` (open, snapshot, click,
  type, press, select, evaluate, screenshot, console, one page kept per
  turn) replaces `inspect_page`; the brief and the prompt cut to what is
  true of the grant, figures of speech replaced by conditions, the plan
  line Codex's; one routing line for fetch / view / use. Mini set after it:
  local 30/32 (W on its old check shape), deployed 31/32 (H on stale
  memory, now cleared by a bare run); F opens, clicks twice and reads 2 on
  both sides. `reports/2026-09-07_item16_research.md`,
  `reports/2026-09-07_item16_build.md`, `reports/2026-09-07_mini_set.md` §3–4.
  ISS-0008, ISS-0010, ISS-0016 stay open until the wider set measures them.
- **Any sent file reaches the workspace** (2026-09-07, out of order, on the
  human's word after `sedan_solid.json` was refused): over Telegram a file
  that is not a picture or a sound is saved under the workspace's `inbox/`,
  never its root, and the turn names it; archives are the model's to unpack
  with a command, moving a file into place is its decision. Offline tests.

## Queue

The order approved 2026-09-07. One item at a time; research first where
noted; the human's word starts each.

17. **The command environment is a place to develop, and a folder per
    task.** Where a command runs and where what it installs lands, both
    profiles (merged with the former 21, the human, 2026-09-07). Deployed:
    the command container is the assistant's own server; today `HOME` and
    temp sit on the Volume path, so Chrome cannot bind a socket, npm's
    cache carries the wrong uid, and what is put in `/tmp` to escape it
    vanishes with the container — short temp and caches off the Volume,
    the "new environment" line gone. Local: the runner stops making a
    `.venv` at the workspace root before every command and putting it
    first on `PATH` (`LocalRunner.prepare`, `command_environment`), which
    fills the root whatever the model intended and hides the machine's own
    packages (item 7's open note); a venv is made where a command makes
    one. The prompt, both profiles: one literal line — each piece of work
    gets its own folder in the workspace, named for the task; its files,
    its virtual environment and its packages go in that folder and nowhere
    else; a folder is reused when the person continues the same work.
    Accepted by scenario C extended with an install, on both sides: the
    package landed under the task's folder and the root holds no venv.
    Whether the model follows the line is the suite's measurement, not a
    rule. ISS-0053, ISS-0058. Researched 2026-09-08
    (`reports/2026-09-08_item17_research.md`; Hermes and DeepSeek for the
    local shape, OpenClaw for the deployed one, the human's word) and built
    the same day: home is the person's (the container's, deployed), temp is
    the runner's own and never the workspace, the agent's venv is off the
    command's `PATH`, no venv made, nothing activated, the "new
    environment" line gone, cwd by the mount path; the folder-per-task line
    in the brief; scenario C extended with a venv and an install. Offline
    tests. Local C, deploy, deployed C and the Chrome probe: pending.

18. **The harness's own seconds.** Name in the timeline what runs between
    steps and after persist (Telegram preview edits and status calls, the
    checkpoint write, the telemetry flush, the two Volume commits around
    every command), then remove what is needless. ISS-0056.

19. **The scenario suite, reconsidered.** After 16: checks on events rather
    than routes, time split into model, tool and wait, a batch that survives
    its container. Was item 10.

21. **One browser tool, and the page rendered apart from the secrets.**
    Approved 2026-09-07 (the human). Deployed, `use_page open url` runs a
    stranger's page in the worker's own Chromium, beside the bot token, the
    model key and the database URL — the arrangement the separate renderer
    exists to avoid, reintroduced by item 16 without a word (ISS-0060).
    Build: `page_session`, a Function in the renderer image (no secret, the
    workspaces Volume read-only), spawned once at the turn's first `open`
    and running until told to close or idle for a set time; the worker
    sends actions through a `modal.Queue` and reads results, screenshots as
    bytes, from another. Locally `BrowserSession` stays in-process with the
    same idle timeout; no end-of-turn close on either side. `view_web_page`
    and `render_web_page` go; the routing line becomes "read with
    fetch_page, everything else with use_page". Accepted by F and W on both
    sides, and a deployed W that opens the page with `use_page` and is
    served from the renderer, not the worker.

22. **The worker outlives the turn, and a live worker is known by its
    heartbeat.** Approved 2026-09-07 (the human), out of order, after a
    ten-minute Blender turn was killed at the worker's 600 s timeout while
    persisting, its answer sent twice by the platform's retry, and the two
    messages behind it left queued with no worker for ten minutes
    (ISS-0061, ISS-0062, ISS-0063). Built the same day: the worker's
    timeout is four hours, a guard and not a bound (the health check bounds
    the turn, as item 14 decided); the conversation lease is 60 s and the
    worker extends it every 20 s while it answers; every queued update
    starts a worker, and one that finds its conversation held waits out one
    lease and takes the conversation up if the holder died; what the
    checkpoint holds for the same update id was delivered before a death
    and is not sent again. Offline tests; deployed 2026-09-07; seen live the
    same evening (ISS-0061..0063 fixed; the hang it uncovered is ISS-0064).
    `reports/2026-09-08_persist_hang_logs.txt`.

23. **A store write that nobody answers ends, and a sent file is kept by
    name.** Approved 2026-09-08 (the human), out of order, after item 22's
    live check showed `persist` waiting for ever on a megabyte row, twice,
    each worker living to its four-hour timeout (ISS-0064), the row being
    a sent video's bytes (ISS-0065). Built the same day: every connection
    the store and the update inbox open carries libpq's bounds
    (`CONNECTION_GUARDS`: connect in 10 s, a socket unanswered for about a
    minute is dead, an `OperationalError` the worker's retry resumes from);
    the history stores an outbound part as the delivery in words — name,
    type, size — never the bytes, on both stores. Offline tests; the guards
    opened a live connection from the local machine. Deployed 2026-09-08;
    seen live the same morning: a sent file's row is 313 characters and
    `persist` took 3.5 s (ISS-0065 fixed; ISS-0064 stays open until a hang
    is seen ending).

Waiting, not in the order above:

7. **The local profile as a place to work.** Built: `run_command` over a
   one-method `Runner`, the two modes and `/mode`, on Windows a
   write-restricted token. Open: the automatic workspace venv hides the
   machine's own packages (goes with 17); no way to choose the project folder in the UI;
   Chainlit has no `/mode` or `/plan`; no boundary outside Windows.
   `reports/2026-09-04_v2_isolated_execution_review.md` §10–§11.

8. **The plan and the goal together.** With `/plan on` the model is offered
   both `todo_write` and `set_goal`; whether the plan replaces the goal or
   both stand is decided by a measurement, after 16 rewrites the brief line
   that kept GLM from ever calling `todo_write`.

13. **The model chosen from Telegram; Gemini's cache.** (a) Gemini 3.1
    Flash-Lite with thinking against without, B and G; (b) OpenRouter
    `cache_control` breakpoints so Gemini's cache lands; (c) the Telegram
    command that switches between published sets.

## Not started

Recorded, not approved, not begun. One line each.

- **A deadline per tool** (ISS-0033), drafted inside item 14 and not
  approved: a hung tool holds the worker and no health check can reach it.
- **The whole-code review of 2026-09-03**, its items 3 onward:
  `reports/2026-09-03_v2_whole_code_review.md`.
- **Finish the `todo` tool.** A turn ending on an item the model does not
  want to close; a plan and the finished work arriving together.
  `reports/2026-08-31_v2_todo_live_failure.md`.
- **Let a plan be corrected by the person**, who can currently only read it.
- **`ask_user`** for a genuinely missing decision, not for permission,
  through the same interrupt seam consent uses.
- **Throttle the edits that write a streamed answer.** How often to edit
  is a measurement, not a constant to pick.
- **A message during a long tool is answered while the tool runs.** Seen
  2026-09-07: "Статус" sent during a four-minute render was read only at the
  next step boundary (item 20 reads between tools). In the same worker, whose
  loop is free while a remote command or the renderer runs: listen to the
  conversation's queue during a tool and answer with a side model call —
  the model answers, never a harness-written status — carrying the turn so
  far and what the harness knows of the running tool (which, how long), with
  no tools of its own — and sent to the person at once, while the render
  or other long tool is still running, not after it; the message still
  reaches the turn at the step boundary as now. Not a second worker: it would
  need the first one's state.
- **Sound routed by the configuration, and transcribed.** The model does not
  declare what it hears; audio is admitted and sent regardless, and GLM
  fails the request. The configuration should say whether the model takes
  audio directly: if it does, send it; if not, save it under `inbox/` like
  any file and, later, a transcription tool (Whisper on the local card, a
  Function like the renderer on Modal) turns it into text.
- **Keep a picture someone sends.** When the model is shown the image and
  when a filename is the design.
- **Answer a Telegram album as one turn.**
  `reports/2026-08-30_v2_album_burst_incident.md`.
- **The local interface as a product path.** Chainlit and the agent on the
  person's machine, the model hosted; the adapter is covered by tests only
  and has not been run live since the 4.5 changes.
- **An HTTP API (`app/api/`)** waits for a UI hosted apart from the
  application; see the amended FastAPI decision in `DECISIONS.md`.

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
