# Roadmap

**Updated:** 2026-09-07

**Project status:** the assistant is deployed and used over Telegram on a
hosted model. The stage that begins now is cleaning defects and polishing
the base: the model is cheap, so the seconds to fight are the harness's
own, and what a tool tells the model it does. The order of that work is
not approved yet; the proposal is
`reports/2026-09-06_hosted_model_cometapi.md` §13.

**Current approved step:** none. The human approves one item before
implementation.

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
  database is Neon at schema version 3; secrets are published from the
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

Open items, none approved to start. The order is the human's to set.

7. **The local profile as a place to work.** Working on the person's own
   files on their own machine with the local UI. Built: `run_command` over
   a one-method `Runner`, the two modes and `/mode`, on Windows a
   write-restricted token so a command writes only inside the workspace.
   Open: the automatic workspace venv hides the machine's own packages;
   no way to choose the project folder in the UI; Chainlit has no `/mode`
   or `/plan`; no boundary outside Windows; the careful mode asks on every
   change. `reports/2026-09-04_v2_isolated_execution_review.md` §10–§11.

8. **The plan and the goal together.** With `/plan on` the model is
   offered both `todo_write` and `set_goal`; whether the plan replaces the
   goal or both stand is decided by a measurement. Observed 2026-09-06 on
   GLM: with the plan on, `todo_write` was never called, because the brief
   phrases the choice as a figure of speech; the brief line is rewritten
   as a literal condition first (item 11), the comparison after.

10. **The scenario suite, reconsidered.** Checks that assert a route
    against the suite's own rule; results that arrive only when the whole
    batch ends, so a crash loses every summary; a batch that dies with its
    container. Analysis and proposal:
    `reports/2026-09-05_suite_and_tools_review.md`.

11. **Tools as the references have them.** Every tool's description and
    every brief line is a literal condition and action, stating what the
    tool takes, returns and leaves where; an offline test refuses a tool
    without all three. Same report.

12. **One way to look at a page, with hands.** `inspect_page` merges into
    `view_web_page`; the renderer gains the actions the model reaches for
    (click, type, press, evaluate on the returned refs), with tool hints
    where a page needs them, instead of a browser inside the command
    image. Same report, and the live sessions of 2026-09-06
    (`reports/2026-09-06_hosted_model_cometapi.md` §12).

13. **The model chosen from Telegram; Gemini's cache.** Remaining after
    the default was chosen: (a) Gemini 3.1 Flash-Lite with thinking
    against without, B and G; (b) OpenRouter `cache_control` breakpoints
    so Gemini's cache lands; (c) the Telegram command that switches
    between published sets.

Two settings get in the way on the hosted model and are proposed as the
first change of the new stage: the turn budget counts tool time and queue
time in seconds (ISS-0057), and the context folds by message count because
the server reports no window (`summarize_after`), while the chosen
131,072 budget stands.

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
