# Roadmap

**Updated:** 2026-09-07

**Project status:** the assistant is deployed and used over Telegram on a
hosted model. The stage that begins now is cleaning defects and polishing
the base: the model is cheap, so the seconds to fight are the harness's
own, and what a tool tells the model it does. The order below was approved
by the human on 2026-09-07; each item still gets its own start signal, and
research before code where the item says so.

**Current approved step:** 15 and 20 are done (2026-09-07): 20 built, deployed and seen live (`reports/2026-09-07_mid_turn_message.md`); 15 built, its first `--both` run passed 16/16 (`reports/2026-09-07_mini_set.md`) and it is the after-deploy check now. Next: 16, research first, on the human's word.

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

## Queue

The order approved 2026-09-07. One item at a time; research first where
noted; the human's word starts each.

14. **The turn is bounded by health, not by a clock or a step count.** The
    step and tool-call ceilings go: they end autonomous work that is going
    well. The seconds ceiling becomes a watchdog: when a turn has run longer
    than a set time, the harness asks the model between steps whether all is
    well and it should go on; an answer means the model decides, no answer
    within the timeout means the system hung and the turn is ended with a
    message to the person. Context: the default budget is 256k
    (`AGENT_OR_CONTEXT_TOKENS`; check the model's served ceiling first), and
    a fold happens only when the request would not fit, never by message
    count (`summarize_after` goes). Provider: one provider by default,
    Novita; a fallback to Z.ai only after a retry shows the provider is
    really down, never because it answered slowly, since a provider change
    loses the cache (11 of 89 deployed calls switched and every one lost it;
    `reports/2026-09-07_turn_bounds_context_provider.md`). First step:
    settings move out of `.env` into a committed `config.toml` that the
    agent may edit; `.env` keeps secrets only; environment still overrides. **Draft, for
    discussion, not approved:** a deadline per tool (ISS-0033), because a
    hung tool holds the worker and no model check can reach it.
    ISS-0057, ISS-0032.

15. **A mini scenario set, bare on both sides.** Approved 2026-09-07 (the
    human): eight scenarios, one per capability — a plain question, one
    tool, files and a command, the browser, the web, memory and history, a
    failing tool, control (a message mid-turn taken and the task finished,
    `/stop`) — each checked on harness events and the store, never on the
    model's wording, with the request text literal. A scenario passes when
    every fact in its column is seen; the set is accepted when all eight
    pass deployed in one run, each line with its time and cost. Both
    profiles run bare: no `AGENTS.md`, an empty workspace every run (the
    deployed probe user's directory is cleared first); a scenario seeds the
    files it needs. `--local`, `--deployed`, `--both` (two tables side by
    side, the difference per scenario). J, K, I, P, Q, R, S and an LLM judge
    of agentic quality are the wider set, 19. Built and run 2026-09-07:
    16/16 passed on both sides (`reports/2026-09-07_mini_set.md`).
    `reports/2026-09-05_suite_and_tools_review.md`.

16. **Tools with contracts, a browser with hands, a literal brief, and the
    whole system prompt reviewed.** Every
    tool's description states what it takes, returns and leaves where, and
    an offline test refuses a tool without all three. `inspect_page`, a
    remnant of the old system, goes: one page tool on the renderer with the
    actions the model built for itself when it was given none (click, type,
    press a key, evaluate, console, screenshot, on the refs the snapshot
    returns); `BrowserSession` already has them. Every brief line is a
    literal condition and action (the plan line first). Research: how
    DeepSeek Harness and Hermes describe tools and drive a page, tool by
    tool, in a report before the rewrite. With it, a review of everything the
    system prompt is assembled from (`DEFAULT_SYSTEM_PROMPT`, `WORKING_METHOD`,
    the capability list, the tool descriptions, the standing instructions,
    the brief lines): what each line is for, what it costs, what stays (the
    human, 2026-09-07). ISS-0008, ISS-0010, ISS-0016.

17. **The command environment is a place to develop.** Deployed, the command
    container is the assistant's own server: what a command needs works
    there without the model fighting the sandbox. Today `HOME` and temp sit
    on the Volume path, so Chrome cannot bind a socket, npm's cache carries
    the wrong uid, and what is put in `/tmp` to escape it vanishes with the
    container. Short temp and caches off the Volume, installs kept in the
    workspace, the "new environment" line gone. ISS-0053, ISS-0058.

18. **The harness's own seconds.** Name in the timeline what runs between
    steps and after persist (Telegram preview edits and status calls, the
    checkpoint write, the telemetry flush, the two Volume commits around
    every command), then remove what is needless. ISS-0056.

19. **The scenario suite, reconsidered.** After 16: checks on events rather
    than routes, time split into model, tool and wait, a batch that survives
    its container. Was item 10.

20. **A message in the middle of a turn.** The person can write while the
    assistant works, as in a coding agent's chat: a comment or a question
    arrives at the loop's next step boundary as the person's words, through
    the out-of-band lane `/stop` already uses, and the model decides what to
    do with it. Right after 14, the same step boundary (the human,
    2026-09-07).

Waiting, not in the order above:

7. **The local profile as a place to work.** Built: `run_command` over a
   one-method `Runner`, the two modes and `/mode`, on Windows a
   write-restricted token. Open: the automatic workspace venv hides the
   machine's own packages; no way to choose the project folder in the UI;
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
