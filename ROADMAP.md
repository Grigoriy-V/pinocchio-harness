# Roadmap

**Updated:** 2026-09-07

**Project status:** the assistant is deployed and used over Telegram on a
hosted model. The stage that begins now is cleaning defects and polishing
the base: the model is cheap, so the seconds to fight are the harness's
own, and what a tool tells the model it does. The order below was approved
by the human on 2026-09-07; each item still gets its own start signal, and
research before code where the item says so.

**Current approved step:** 14, 15 and 20 are in Done (2026-09-07). 16 researched (`reports/2026-09-07_item16_research.md`), the page tool's shape approved; 17 absorbs the folder-per-task rule. 16 built (`reports/2026-09-07_item16_build.md`); next: the mini set on it locally, then deploy and the set deployed, each on the human's word.

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

## Queue

The order approved 2026-09-07. One item at a time; research first where
noted; the human's word starts each.

16. **Tools with contracts, a browser with hands, a literal brief, and the
    whole system prompt reviewed.** Every
    tool's description states what it takes, returns and leaves where, and
    an offline test refuses a tool without all three. `inspect_page`, a
    remnant of the old system, goes: one page tool on the renderer with the
    actions the model built for itself when it was given none (click, type,
    press a key, evaluate, console, screenshot, on the refs the snapshot
    returns); `BrowserSession` already has them. Approved 2026-09-07: one
    tool, `use_page(action, ...)`, on the condition that its description
    teaches its use through what the model already knows — "the same thing
    as Playwright or Puppeteer, packed into one tool: one call per action"
    — and states, per action, what it takes and what comes back, that
    `open` comes first, that a ref lives until the next `open`, and that a
    screenshot returns to the model and is a file the person has not seen.
    Every brief line is a
    literal condition and action (the plan line first). Research: how
    DeepSeek Harness and Hermes describe tools and drive a page, tool by
    tool, in a report before the rewrite (`reports/2026-09-07_item16_research.md`,
    2026-09-07). With it, a review of everything the
    system prompt is assembled from (`DEFAULT_SYSTEM_PROMPT`, `WORKING_METHOD`,
    the capability list, the tool descriptions, the standing instructions,
    the brief lines): what each line is for, what it costs, what stays (the
    human, 2026-09-07). The review's findings and the rewrite's draft:
    `reports/2026-09-07_item16_research.md`. Built 2026-09-07, offline
    suite green, prefix measured (`reports/2026-09-07_item16_build.md`);
    open until the mini set has run on it, local and deployed. ISS-0008,
    ISS-0010, ISS-0016.

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
    rule. ISS-0053, ISS-0058.

18. **The harness's own seconds.** Name in the timeline what runs between
    steps and after persist (Telegram preview edits and status calls, the
    checkpoint write, the telemetry flush, the two Volume commits around
    every command), then remove what is needless. ISS-0056.

19. **The scenario suite, reconsidered.** After 16: checks on events rather
    than routes, time split into model, tool and wait, a batch that survives
    its container. Was item 10.

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
