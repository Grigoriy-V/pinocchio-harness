# Roadmap

**Updated:** 2026-09-14

**Project status:** the assistant is deployed and used over Telegram on a
hosted model, and the same harness runs locally in Chainlit on the owner's
machine. The goal since 2026-09-13 (the human): a harness that gives what
the references give, in capability and in the app, with nothing
artificially reduced: locally Claude Code, Codex, DeepSeek and Hermes;
deployed OpenClaw (the human, 2026-09-14). The audit of 2026-09-14
(`reports/2026-09-14_harness_audit.md`) names what holds it below that;
the queue below is the order approved for working through it.

**Current approved step:** 28 done 2026-09-14 (commit `60dcdaa`); next is
27 step 3, on the human's word.

Observed defects are in `ISSUES.md`, which is not a plan and authorizes
nothing. `docs/PRODUCT.md` is the product contract; `docs/PROJECT_MAP.md`,
`docs/CODEMAP.md` and `docs/OPERATIONS_MAP.md` describe the system,
ownership and operations; `AGENTS.md` holds execution rules; `DECISIONS.md`
preserves approved durable choices. This file alone owns current work,
order and authorization.

## Current state

- **Model:** GLM 5.3 Flash through OpenRouter, Novita first, Z.ai as the
  fallback, thinking off (`MODEL=or`; DECISIONS 2026-09-06); a model set is
  one line, `MODEL=<name>`, every set in the control secret. The GPU Apps
  of the self-hosted era (`assistant-llm-v2`, `assistant-llm-qwen`,
  `assistant-llm-qwen-int4`) and the fine-tune sets `tuned`/`base` stay
  deployed, scaled to zero, not in use.
- **Deployed profile:** `assistant-control` on Modal serves the webhook,
  the update worker, `render_web_page`, `run_command` and `scenarios`; the
  database is Neon, code at schema 5, the deployed database at 4 until the
  migration gate; secrets are published from the owner's `.env` by
  `tools/sync_control_secret.py`. The git tag `deployed` marks the commit
  the running deploy was built from (`c4694b8`); `git log deployed..HEAD`
  is what a deploy would carry. `tests/test_profiles.py` holds the
  deployed wiring shut.
- **Local profile:** the same `app/` in Chainlit (`.claude/launch.json`,
  port 8100), opened with `open=True`: a working folder per conversation
  (`/workspace`), reading anywhere, a write outside the folder after a
  yes, localhost in the browser, a command kept running in the background;
  the status card with the session's spend and the account's credits; the
  commands `/compact`, `/plan`, `/mode`, `/context`, `/workspace`; no
  inbox, a sent file is a path on the machine. The command boundary exists
  on Windows only.
- **MCP:** the harness is an MCP server (`python -m tools.mcp_server`,
  `.mcp.json`); the assistant uses MCP servers as tools (`time`, GitHub
  read-only) from `config.toml`, locally and deployed.
- **Measurement:** the mini set (`scripts/loop_live.py`), the seven
  scenario families (`scripts/training_scenarios.py`), blind Sonnet judges
  over `tools/judge_pack.py`.

## Done

Versions 1–1.5 and Version 2 are closed; the reports under `reports/`
carry the evidence, one per step. Items of the 2026-09-07 order, closed,
one line each, evidence in the linked report:

- **14, the turn bounded by health** (2026-09-07): no step or call ceiling,
  a health check after a set time, a fold only when the request would not
  fit. `reports/2026-09-07_turn_bounds_context_provider.md`.
- **15, the mini scenario set** (2026-09-07). `reports/2026-09-07_mini_set.md`.
- **20, a message in the middle of a turn** (2026-09-07).
  `reports/2026-09-07_mid_turn_message.md`.
- **16, tools with contracts, `use_page`, the prompt reviewed**
  (2026-09-07). `reports/2026-09-07_item16_build.md`.
- **Any sent file reaches the workspace** (2026-09-07): over Telegram under
  `inbox/`.
- **17, the command environment is a place to develop** (2026-09-08).
  `reports/2026-09-08_item17_research.md`.
- **22, the worker outlives the turn, known by its heartbeat** (2026-09-07).
- **23, a store write that nobody answers ends** (2026-09-08).
- **18, the harness's own seconds** (2026-09-10).
  `reports/2026-09-08_item18_harness_seconds.md`.
- **24, the fine-tune experiment** (closed 2026-09-12): the fine-tune did
  not help; further experiments in `pinocchio-finetune`.
  `reports/2026-09-11_gemma_finetune_experiment.md` §5.
- **25, the harness as an MCP server** (2026-09-12).
  `reports/2026-09-12_item25_mcp_server.md`.
- **26, MCP servers as the assistant's tools** (2026-09-12).
  `reports/2026-09-12_item26_mcp_tools.md`.
- **19, the scenario suite** (closed 2026-09-12): checks read outcomes,
  never a route the prompt did not name.
- **27 steps 1 and 2, the local app and the working folder** (2026-09-14):
  any file accepted without an inbox, the commands in the composer, the
  status card, one collapsed step per turn, notes kept on reload; the
  working folder per conversation with the references' rule (read
  anywhere, write in the folder, elsewhere after a yes); a command kept
  running in the background locally; ISS-0071..0076 fixed on the way.
  `reports/2026-09-14_profiles_and_platforms.md`.
- **28, the records brought to the current state** (2026-09-14): the goal
  and the references in `AGENTS.md`, a derived-limits rule, gates without
  GPU; `ISSUES.md` fixed entries under Closed; `DECISIONS.md` catalog and
  order regenerated; the maps at schema 5, tokens, the Chainlit adapter as
  built; sixteen closed-era documents in `reports/archive/`. Accepted by the
  greps named in the step. `reports/2026-09-14_harness_audit.md`.

## Queue

One item at a time; the human's word starts each. Order approved
2026-09-14 (the human), after the audit.

27. **The local profile as the references**, step 3 (steps 1 and 2 in
    Done). Approved 2026-09-13; step 3 widened 2026-09-14 (the human) by
    the audit's coding-tool gaps, because a coding mini set cannot pass
    without them:
    3. **Coding locally, without crutches.** The tools a coding agent
       has: search across a tree, glob, reading by line range with line
       numbers, a patch or multi-edit, the diff in a write's result,
       `fetch_page` under the local `open` flag (localhost and any port on
       the person's machine). The filesystem and shell descriptions state
       what each tool returns, without "use this instead of ls / cat /
       sed"; the model chooses. ISS-0068 (the Windows boundary kills every
       Cygwin tool); `python3` and other Linux-isms out of the brief on
       Windows. Accepted by a local coding mini set beside its deployed
       numbers. (The automatic workspace venv is already gone.)

29. **The defects the audit found** (`reports/2026-09-14_harness_audit.md`
    §2): `BAD_ARGUMENTS` undefined in `shell.py`, schema validation that
    ignores `enum`/`minimum`/`maximum`/`items`, the telemetry store without
    connection guards, SQLite without WAL, `TracedBackend` not forwarding
    `warm`/`estimate_tokens`, a silent decline without a checkpointer,
    `edit_message` keeping the last piece, the read/write split by list
    position, filesystem descriptions saying "inside the root" when open.
    No model needed; offline tests for each.

30. **Briefs and descriptions say what, not how.** Every text the model
    reads (`app/context/window.py`, `app/capabilities.py`, every tool's
    description and error text) rewritten to what a tool returns and
    reaches, the incident coaching removed (fence rules, traceback
    coaching, "one screenshot is not a run", "there are no others",
    "answer briefly", "say nothing", "change one thing then run it", the
    fixed web-tool ranking, the folder-and-venv layout). In the same step,
    so the measurement does not reward the old shape: `met()` in
    `tools/prompt_scenarios.py` for empty expectations, rubric items (b)
    and (e) in `tools/judge_pack.py` reworded to redundancy given what
    was known, the route checks in `loop_live` G/I/B replaced by
    outcomes. Measured on the mini set and by blind judges before and
    after. Touches the tests that pin wording (audit §4).

31. **Limits derived from the budget.** The context and output constants
    (`keep_results`, `keep_turns`, `STUB_MIN_CHARS`, the summary's words,
    `SUMMARY_ALLOWANCE`, `MEDIA_BUDGET`, `SIZES`, `max_tokens`,
    `MAX_INSTRUCTION_BYTES`, `retrieved_facts`) become functions of the
    model's window and the request budget; the remaining caps become
    settings; every cut gets a paging path or a spill file
    (`list_files`, `run_command`, `use_page`, screenshots, `select`,
    CSV, pages per view); the command timeout no longer clamps silently
    and has no ceiling where the runner can wait.

32. **The graph.** Independent tool calls run in parallel, a tool
    declaring `mutates` serialised; tool output streams while it runs;
    approval per call with the safe calls run first; stop and an
    interjection read between stream chunks; schemas read per step so a
    `find_tools` and MCP-on-demand can widen the set inside a turn (the
    Not-started item of 2026-09-13 joins here); plan mode as a third mode
    that withholds mutating tools; the repeat counters demoted to
    information in the result; an empty completion still ends with a
    line.

33. **The model layer and telemetry.** A per-model-family profile so the
    system-message flattening, the `<|"|>` repair and the end-marker
    stripping run only where the family needs them; a provider profile
    instead of a URL substring; `tool_choice`, `parallel_tool_calls`,
    cache hints, reasoning passthrough, per-call overrides, `Retry-After`;
    `usage.cost` stored on the run and in `model_finished`; telemetry
    opened in Chainlit; the A10 cost model retired.

34. **The adapters.** One command registry with properties (needs a
    model, its reply), one fold notice, one turn driver shared by
    Chainlit, Telegram and the scripts; `inbox/`, the album limit, the
    Telegram marker text, `telegram_*` events, `ContentPart.hidden`,
    `notes` and `ContentPart.path` moved out of `app/`; `/workspace` in
    Telegram. Chainlit: the answer streamed, tool output shown live and
    on reload, a diff on approval, approve-all and a remembered decision,
    `/help`, the plan in view.

21. **One browser tool, and the page rendered apart from the secrets.**
    Approved 2026-09-07 (the human); place in the order to be set when
    its turn comes. Deployed, `use_page open url` runs a stranger's page
    in the worker's own Chromium beside the secrets (ISS-0060). Build:
    `page_session` in the renderer image, actions over a `modal.Queue`;
    locally `BrowserSession` in-process; `view_web_page` and
    `render_web_page` go.

Waiting, not in the order above:

7. **The local profile as a place to work.** Built: `run_command`, the two
   modes, on Windows a write-restricted token, the working folder and the
   commands (27). Open: no boundary outside Windows (the platform seam
   below). `reports/2026-09-04_v2_isolated_execution_review.md` §10–§11.

8. **The plan and the goal together.** Whether `todo_write` replaces
   `set_goal` or both stand is decided by a measurement; joins 32 when
   plan mode is built.

13. **The model chosen from Telegram; Gemini's cache.** (a) Gemini 3.1
    Flash-Lite with thinking against without; (b) `cache_control`
    breakpoints (joins 33); (c) the Telegram command that switches sets.

## Not started

Recorded, not approved, not begun. One line each.

- **Decisions the audit questions, waiting for the human's word**
  (`reports/2026-09-14_harness_audit.md` §A items 25–35): "one owner and
  a small number of other users" as product scope, the 32k per-result cap,
  the 8,000-byte instruction bound, "a Modal Sandbox is v2", "one
  implementation per capability", embeddings waiting for an unscheduled
  measurement, `app/api/` waiting for a separately hosted caller, "assume
  only one application works in the repository at a time". None is
  changed until he says so.
- **The local profile on three operating systems, behind one platform
  seam** (`reports/2026-09-14_profiles_and_platforms.md` §2): the OS asked
  about once, in named modules; a profile matrix in the operations map;
  a macOS or Linux command boundary when the machine exists. What the
  model may run through `run_command` is to be discussed (§3 there).
- **The dead code and the closed era** (audit §3): the GPU Modal apps,
  `autoscale.py`, `vllm_baseline.py`, `gemma4_parser.py`,
  `app/telemetry/vllm.py`, the six one-shot scripts, the retired model
  sets in `config.toml` and the localhost default; removed on the human's
  word, since the sets are still deployed.
- **A deadline per tool** (ISS-0033).
- **Finish the `todo` tool**; **let a plan be corrected by the person**;
  **`ask_user`** for a missing decision through the consent seam.
- **A message during a long tool is answered while the tool runs** (seen
  2026-09-07): a side model call in the same worker, no tools, sent at
  once.
- **Sound routed by the configuration, and transcribed.**
- **Keep a picture someone sends.** **Answer a Telegram album as one turn.**
- **Throttle the edits that write a streamed answer** (a measurement).
- **Scenarios the suite lacks** (audit §G): git, a long-running server,
  MCP on demand, subagents, a plan proposed then approved.
- **Subagents, skills, hooks** as the references have them: no item yet;
  the graph step (32) makes the seam.

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
