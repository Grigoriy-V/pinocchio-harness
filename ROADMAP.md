# Roadmap

**Updated:** 2026-09-07

**Project status:** the assistant is deployed and used over Telegram on a
hosted model. The stage that begins now is cleaning defects and polishing
the base: the model is cheap, so the seconds to fight are the harness's
own, and what a tool tells the model it does. The order below was approved
by the human on 2026-09-07; each item still gets its own start signal, and
research before code where the item says so.

**Current approved step:** 14, 15 and 20 are in Done (2026-09-07). 16 researched (`reports/2026-09-07_item16_research.md`), the page tool's shape approved; 17 absorbs the folder-per-task rule. 16 deployed and run on both sides, in Done. 21 added (one browser tool on the renderer; ISS-0060 is the breach it closes). 17, 22 and 23 in Done (2026-09-08). 18 started, its first half built (the harness's seconds named), deploy pending; 21 waits for its research.

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
- **17, the command environment is a place to develop, and a folder per
  task** (2026-09-08): home is the person's (the container's, deployed),
  temp is the runner's own and never the workspace, the agent's venv off
  the command's `PATH`, nothing made or activated; the folder-per-task
  line in the brief; the "new environment" line gone. Scenario C with a
  venv and an install 7/7 on both sides; Chrome launches from the deployed
  workspace. ISS-0053, ISS-0058 fixed; two boundary defects found and
  fixed on the way (a venv's `ensurepip`, pip's cache).
  `reports/2026-09-08_item17_research.md`.
- **22, the worker outlives the turn, and a live worker is known by its
  heartbeat** (2026-09-07): a four-hour worker timeout as a guard, a 60 s
  lease extended every 20 s, every queued update starts a worker that
  waits out a lease, what was delivered before a death is not sent again.
  Seen live the same evening (ISS-0061..0063 fixed).
  `reports/2026-09-08_persist_hang_logs.txt`.
- **23, a store write that nobody answers ends, and a sent file is kept by
  name** (2026-09-08): libpq bounds on every store and inbox connection; an
  outbound part stored as its delivery in words. Seen live: a sent file's
  row is 313 characters, `persist` 3.5 s (ISS-0065 fixed; ISS-0064 stays
  open in `ISSUES.md` until a hang is seen ending).
- **18, the harness's own seconds** (2026-09-10): every thing the harness
  spends time on names itself on the turn's active trace with a duration;
  measured on the mini set and on two real Telegram turns. On a normal
  turn the harness costs 2–4 s in half-seconds; the one block a person
  feels is the cold worker per message (5.7–6.7 s, the worker's 60 s
  scaledown), a platform choice set aside for later on the human's word.
  Nothing removed; the candidates stay measured in the report. ISS-0056
  fixed as named; ISS-0066 recorded.
  `reports/2026-09-08_item18_harness_seconds.md`.

## Queue

The order approved 2026-09-07; 24 put first 2026-09-11. One item at a
time; research first where noted; the human's word starts each.

24. **An experiment: Gemma 4 12B fine-tuned on this harness's own turns.**
    Approved 2026-09-11 (the human). The goal, in the human's words, is
    experience and a portfolio piece — a LoRA/QLoRA of an open model on
    trajectories collected from one's own agent harness, measured before
    and after on the same scenario suite. It is not a product step: the
    hosted GLM stays the assistant's model, and the result is a table, not
    a replacement. "More agentic" means, measured on the mini set and the
    wider letters: tool calls that parse (ISS-0001), turns that finish,
    steps to finish, claims about what was not observed (ISS-0004), and —
    the main one — an LLM judge with a fixed rubric, the project agent
    itself in the first version (the human, 2026-09-11; the rubric in the
    report).
    Compute: there is no local GPU; every run — baseline, generation,
    training, evaluation — is a Modal GPU worker, dollars stated and a
    gate each. Order: (1) count the usable turns already in the deployed
    store, read-only — done 2026-09-11: ~40 trajectories survive, the
    scenario runner resets its threads, so data is collected at run time;
    (2) the metrics fixed and Gemma 4 12B's baseline on the suite through
    `assistant-llm-v2` (gate) — mini set done 2026-09-11: 8/8, judge 9.7,
    at the ceiling; the wider letters and 19's new scenarios are where a
    difference can show (report §2a); (3) the data — the capture built
    2026-09-11: every model call kept as the model saw it, one file per
    run on the Volume (`AGENT_TRAJECTORIES`); the training loop itself —
    export to SFT format, Unsloth/TRL on Modal, merge, publish — lives in
    a separate repository, made when step 4 starts (the human,
    2026-09-11; DECISIONS). This repository hands over trajectories with
    their outcome and check results attached (an export tool, to build)
    and takes back a model set. Deployed 2026-09-11; the first eleven
    trajectory files are GLM's mini set, judged 9.9 beside Gemma's 9.7
    (report §2a). Research for the scenarios read 2026-09-11
    (`reports/2026-09-11_finetune_scenarios_research.md`): 500–1,000
    kept trajectories is the range that moved 7–13B models; checks test
    outcomes, never a tool the prompt did not name (C and O corrected);
    seven scenario families — built the same day on the human's word
    ("давай все"): D L N T U V X, 21 prompts with seeds and outcome checks
    in `scripts/training_scenarios.py`, `--repeat N` in `loop_live`;
    offline tests; deployed and run on GLM the same day: 21/21, judge 9.95
    (one point: a `pip install` into the machine's python on D2, a
    standing-rule miss only the judge sees). 32 trajectory files on the
    Volume. Gemma on the same letters the same day: 18/21 (19 after a
    check fix), 151 model calls to GLM's 79, judge 9.0 to 9.7 — the gap has
    one shape, re-running the same command when a result surprises it,
    until the repeat guard ends the turn (report §3b). `--temperature`
    built for the data runs (the product samples at 0); two sampled
    repeats the same day: sequences differ on 12 of 21 cases (every wrong
    turn and long task), the same on the nine one-move cases; GLM 39/42
    (report §3c). Export built (`tools/export_trajectories.py`, the
    `scenario_checked` event; 95 runs exported) and variants of the nine
    one-move cases (32 cases now). The human's rule for the rest
    (2026-09-11): generation runs in parallel, the first training version
    from the necessary minimum, the loop closed once before any polish.
    Pending: a probe user per `scenarios` call so runs can go in parallel;
    then the volume run. Then GLM run
    many times over the scenario prompts, keeping the turns that pass
    their checks, plus the real turns that did — this is where 19's new
    scenarios are written, as prompts for data as well as checks (gate:
    model calls, priced); (4) training on Modal, LoRA on A100 or QLoRA on
    A10, assistant tokens only, the tool schemas kept in the context
    (gate); (5) the fine-tuned model deployed as one more model set and
    the same suite run before/after (gate). Research first: which of
    Unsloth or TRL+peft runs Gemma 4 on Modal, and the sequence length the
    turns need. Report: `reports/2026-09-11_gemma_finetune_experiment.md`
    (to be written with step 1).

19. **The scenario suite, reconsidered.** After 16: checks on events rather
    than routes, time split into model, tool and wait, a batch that survives
    its container. Was item 10. Its new scenarios are written inside 24's
    step 3, where they double as the data's prompts; the rest waits.

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
