# Audit: what in the documents and the code holds the harness below the references (2026-09-14)

Asked by the human: "все формулировки в документах и коде которые мешают нам
строить настоящих конкурентных Харнес, с полным функционалом, хвосты старых
версий, костыли и тд." Seven subagents (Opus on code, Sonnet on documents)
read their zones in full; the project agent spot-checked the loudest claims
by grep before writing this. Nothing here is approved; every item is a
finding with a proposal, to be sorted into roadmap steps on the human's word.

Zones: canonical documents; the rest of `docs/`; `app/agent`, `app/context`,
briefs; `app/tools`; `app/models`, `app/memory`, `app/telemetry`; `ui/`,
`deploy/`, `scripts/`, `tools/`; `tests/` and the scenario tooling. The
seven raw reports are appended in full (§A–§G) so nothing is lost; §1–§3
are the synthesis.

## 1. The ten things that matter most

1. **The prompt and the tool descriptions tell the model how to work, not
   what to reach.** `app/context/window.py:77` "Answer briefly."; `:76` "if
   nothing is new, say nothing"; `:57` "Change one thing, then run it";
   `app/tools/filesystem.py:344,367,399,434` "Use this instead of ls / cat /
   echo / sed"; `app/tools/shell.py:625` the same ban from the shell's side;
   `app/capabilities.py:359-374` a fixed ranking fetch_page → view_web_page
   → use_page with "only"; `:256-263` "Do not open one [a task list] for a
   simple request … every update resends the whole list"; `:168-171` every
   piece of work gets its own folder and its own venv. Most of these were
   written from one observed turn of a small model. The references state
   what a tool returns and leave the route to the agent.

2. **Descriptions are a changelog of past incidents.** Fence rules and
   argument order for write_file (ISS-0001) in `filesystem.py:399-402` and
   `base.py:435-446`; the traceback coaching on every non-zero exit
   (`shell.py:536-541, 627-630`, also sent when `grep` finds nothing);
   "one screenshot is not a run" in `browser.py:370-373`; "there are no
   others" in `capabilities.py:78-81`. By `AGENTS.md`'s own rule these are
   case crutches; they cost tokens on every request and teach the model
   about harness bugs.

3. **Constants from a 64K local Gemma bound a 1.3M-token product.**
   `keep_results = 2`, `keep_turns = 2` (`app/config.py:451,457`),
   `STUB_MIN_CHARS = 200`, `WORDS_CEILING = 600` for the fold summary,
   `SUMMARY_ALLOWANCE = 800`, `MEDIA_BUDGET = {"image": 4}` copied from a
   vLLM serving limit, `SIZES` capping "large" at 40% of the window,
   `max_tokens = 8192` output (`config.py:225`), `MAX_INSTRUCTION_BYTES =
   8_000` for the person's own AGENTS.md, `CHARS_PER_TOKEN = 3.0`
   pessimistic, `retrieved_facts = 5`. Almost all should derive from the
   model's window and the request budget.

4. **Caps without paging make content unreachable.** `list_files` 200
   entries with no offset (`filesystem.py:30`); `run_command` 16K chars,
   middle cut, no paging (`shell.py:73`); `use_page` 8K text / 12K snapshot
   with no `max_chars` although its own error text names one
   (`chromium.py:38,370,604`); full-page screenshot clipped at 6000 px
   silently; `<select>` shows 12 options; `view_web_page` has no offset;
   CSV cut at 200 rows; `MAX_PAGES_PER_VIEW = 2` from the same vLLM image
   limit; `MAX_TIMEOUT = 600` s clamped silently (`shell.py:604`), and
   deployed `timeout=660` in `control_app.py:233` makes ten minutes the
   ceiling of any command anywhere.

5. **The tool set is a document-and-browser assistant, not a coding
   agent.** No search across a tree, no glob, no line-range read with line
   numbers, no multi-edit/patch, no diff in write_file's result, no git
   awareness, no notebook, no subagent/task tool, no tool-output streaming
   deployed. The shell that could substitute is the tool the descriptions
   forbid (item 1). `fetch_page` is blocked from ports other than 80/443
   and from 8 media types, and the local `open` flag never reaches the web
   capabilities (`app/tools/capabilities.py:102-109`), so on the person's
   own machine `use_page` opens localhost and `fetch_page` cannot.

6. **Sequential everything, frozen toolset.** Tool calls in one batch run
   one after another (`graph.py:958`, no gather); tool output never
   streams; the schema list is compiled once per graph (`graph.py:514`), so
   no mid-turn tool discovery or lazy MCP load; approval is one
   all-or-nothing question per batch before anything runs (`graph.py:942`);
   stop and interjections are read only at the top of `run_tools`; no plan
   mode as a state, only a switch file off by default (`todo.py:48`);
   `MAX_IDENTICAL_FAILURES/SUCCESSES = 2` end the model's tools from a
   counter written for ISS-0013/0042; a health question is injected every
   600 s as a fake user turn.

7. **The model layer is still shaped by Gemma/Qwen chat templates and a
   self-hosted A10.** System messages flattened into user text
   (`openai_compatible.py:149-192`), the `<|"|>` argument repair and
   `END_MARKERS` stripping run for every provider, `context_limit` reads
   only vLLM's `max_model_len`, default model `gemma-4-12b-it` on
   `127.0.0.1:8000` (`config.py:203-204`, `config.toml:12-14`,
   `scripts/doctor.py:18`). No `tool_choice`, no `parallel_tool_calls`, no
   prompt-cache hints, reasoning dropped, no per-call overrides,
   `Retry-After` ignored. Telemetry's headline metric is "GPU active
   seconds" at an A10 price (`app/telemetry/cost.py`) while the provider's
   `usage.cost` is never stored, and the local profile records no
   telemetry at all.

8. **Interface behaviour keeps landing in `app/`, and harness behaviour is
   written three times in adapters.** In core: `inbox/` default and its
   prompt text (`attachments.py:34,183-199`), `MAX_ITEMS = 10` "Telegram's
   album limit" in `presentation.py:25`, "/plan off in Telegram" written
   to the workspace by `todo.py:66`, `telegram_*` event names in
   `trace.py:170`, `source_update_id`, `LOCAL_USER_ID`, OpenRouter's
   credits URL in `status.py`, `ContentPart.hidden` beside the known
   `notes`/`path`, English receipts in the wire builder and records. In
   adapters: command dispatch, the fold notice and turn driving are each
   written in `chainlit_app`, `telegram/adapter` and `scripts/loop_live.Turn`
   (which the MCP server and the deployed `scenarios`/`ask` use as a third
   interface).

9. **The local UI is the weaker half.** No streamed answer, tool results
   replaced by "Completed." live and on reload (`chainlit_app.py:242`,
   `chainlit_history.py:117`), approvals as raw JSON with no diff, no
   approve-all or remembered decision, a 600 s approval timeout that
   answers no, five commands against Telegram's ten, `/workspace` not in
   Telegram, no `/help`, cost and context behind a polled hidden card,
   `chainlit.md` still announcing "Gemma 4 12B IT" and "Version 1.5",
   audio off, three copies of the inbox story in comments.

10. **The records are behind the code, and measurement rewards the small
    agent.** `ROADMAP.md` dated 2026-09-07 with a Current-step line about
    a closed item; twelve fixed issues under `## Open`; schema "4" in two
    documents against 5 in code; `context_fraction` percentages still in
    OPERATIONS_MAP; `CODEMAP` without `commands.py`, `folder.py`,
    `status.py`, `public/`; a vLLM gate in AGENTS.md; five dead GPU model
    sets in `config.toml`; 16 of 18 non-canonical docs orphaned and from
    closed eras. `tools/judge_pack.py:36-40` docks points for "five reads
    where one search would do" and a listing before a write;
    `tools/prompt_scenarios.py:226-233` fails any scenario with empty
    `expected_tools` on any tool call; `loop_live` G/I/B pin tool routes
    against the rule quoted in the same file.

## 2. Real defects found on the way

- `app/tools/shell.py:610,616` — `BAD_ARGUMENTS` is never imported;
  `command_output`/`stop_command` on an unknown id raise `NameError`
  (confirmed by grep: the name occurs only on those two lines).
- `app/tools/shell.py:204,373` — `Any` used in annotations, not imported.
- `app/tools/shell.py:643-646` — the contract promises a `new environment`
  line that `describe()` never prints.
- `app/tools/chromium.py:604` — tells the model to "raise max_chars", a
  parameter `use_page` does not have.
- `app/tools/base.py:453-468` — `validation_error` ignores `enum`,
  `minimum`, `maximum`, array `items`; the schema shown is not the schema
  enforced.
- `app/telemetry/postgres.py:120` — connects without the
  `CONNECTION_GUARDS` the memory store got for ISS-0064, and writes inside
  the turn.
- `app/memory/store.py:226` — SQLite without WAL or `busy_timeout`; two
  local processes (Chainlit and the MCP server) collide on the first
  concurrent write.
- `app/telemetry/backend.py:29` — `TracedBackend` forwards neither `warm`
  nor `estimate_tokens`, so the traced path folds on the uncalibrated
  ratio.
- `app/agent/graph.py:939-940` — with no checkpointer, every
  approval-needing call is silently declined rather than reported as
  "nowhere to ask".
- `ui/telegram/api.py:520` — `edit_message` keeps only the last piece of
  an over-long text.
- `app/tools/capabilities.py:65-70` — read/write capability split by list
  position; a new read tool lands under the write capability.
- Filesystem descriptions still say "inside the workspace root" when
  `open` is on (`filesystem.py:380-383,411,449`); only `list_files` got
  the corrected text.

## 3. Dead weight (verified by grep)

Code: `runtime.py:415 if True:`, `Agent.context_prompt`, `Agent.record`,
`load_attachments`/`load_attachment_bytes` (tests only), `_memory_lines`
returning `[]`, `LEGACY_NAMES = {}`, `web_tools()` wrapper, `Finished.fresh`,
unused `cwd`/`workspace`/`root` parameters in `shell.py:222,365,370` and
`filesystem.py:210`, `compactions()` with no reader, `tool_skipped` with no
writer, `alt_database_url` in the product settings, the `data:` branch of
`BrowserSession.open`. Era: three GPU Modal apps (1205 lines),
`deploy/modal/autoscale.py`, `tools/vllm_baseline.py`, `tools/gemma4_parser.py`,
`app/telemetry/vllm.py`, `scripts/{stage3_live,v1_live,smoke_test,
migrate_workspace,measure_command_cold_start,measure_endpoint_wake}.py`,
`docs/comands.txt`, five `[model.sets.*]` for retired endpoints. Documents:
16 orphaned files under `docs/` (table in §B), the 105-line Done section
and item 24's numbers in `ROADMAP.md`, the Model Apps section of
`OPERATIONS_MAP.md`, `ISSUES.md` at 1,203 lines.

## 4. Tests that pin the wording the model sees

Changing a brief or a tool description touches these (from §G):
`tests/test_capability_brief.py:132-472`, `tests/test_context.py:97,598,673`,
`tests/test_goal.py:64-66`, `tests/test_run_command.py:275,280,299`,
`tests/test_tool_contracts.py:58-71`, `tests/test_turn_stopping.py:315,323`,
`tests/test_web.py:171`, `tests/test_web_tools.py:159-161`. The turn-bounds
suite itself asserts no ceiling on steps, calls or seconds, which is the
right shape and should stay.

## 5. How this could become work (options, not a plan)

- **Briefs and descriptions**: one pass rewriting every model-facing text
  to "what it returns / what it reaches", deleting incident coaching; the
  scenario suite and blind judges measure the difference. Touches §4's
  tests.
- **Budget-derived limits**: replace the context and output constants with
  functions of the model's window and the request budget; make the
  remaining caps settings; give every cut a paging path or a spill file.
- **Coding-agent tools**: search across a tree, glob, line-range reads
  with numbers, patch/multi-edit, diff in write results, `fetch_page`
  under the local `open` flag, git-aware results. Roadmap 27 step 3 is the
  natural home.
- **Graph**: parallel independent calls, streamed tool output, per-call
  approval with safe calls run first, stop/interjection between stream
  chunks, schemas read per step (the find_tools/MCP-on-demand item),
  plan mode as a third mode, the repeat counters demoted to information.
- **Model layer**: a per-model-family profile (flattening, markers,
  repair only where the family needs them), provider profile instead of
  URL substring, `tool_choice`, `parallel_tool_calls`, cache hints,
  reasoning passthrough, store `usage.cost`, open telemetry in Chainlit,
  retire the A10 cost model.
- **Adapters**: one command registry with properties (needs_model,
  reply), one fold notice, one turn driver; move `inbox`, the album limit,
  the Telegram marker text, `telegram_*` events out of `app/`; Chainlit
  streaming, tool output shown, diffs on approval, approve-all/remember.
- **Records**: refresh `ROADMAP.md`, move fixed issues to Closed, one
  owner per number, `CODEMAP` rows for the new owners, move 16 docs to
  `reports/`, drop the vLLM gate and the dead model sets.
- **Measurement**: fix `met()` for empty expectations, reword rubric (b)
  and (e) to redundancy-given-knowledge rather than call count, replace
  route checks in `loop_live` G/I/B with outcomes, add scenarios for git,
  a long-running server, MCP on demand, subagents.

---

# Appendices: the seven raw reports

## §A Canonical documents (Opus)

1. [B] `ROADMAP.md:3` — "Updated: 2026-09-07" — a week stale while HEAD, ISSUES and DECISIONS run to 09-14. Refresh on every edit.
2. [B/D] `ROADMAP.md:12` — "18 started, its first half built … 21 waits" — contradicts `:107` (18 closed 09-10); says nothing of 27. Name only the open step.
3. [B] `ROADMAP.md:34` — "Neon at schema version 4" — code is 5 (`postgres.py:67`, `store.py:38`). "code at 5, deployed at 4 until the migration gate".
4. [D] `docs/PROJECT_MAP.md:137` — "schema version 4" — third place, third answer. State once, link.
5. [B] `docs/OPERATIONS_MAP.md:234` — "(25% / fraction / 95%)" — `context_fraction` is gone; `choice.py:16` is tokens. Replace with the token counts.
6. [B] `ROADMAP.md:211` — "no way to choose the project folder in the UI" — `/workspace` exists. Delete.
7. [B] `ROADMAP.md:212` — "Chainlit has no /mode or /plan" — both registered. Delete.
8. [B] `docs/PROJECT_MAP.md:252` — "load_attachments() (media only) … no /mode or /plan" — Chainlit uses `admit_uploads` and has the commands. Rewrite.
9. [B] `ROADMAP.md:185` — "the automatic workspace venv removed as agreed" listed as to-do — `ensure_venv` already gone. Drop from 27 step 3.
10. [B] `ROADMAP.md:210` — same dead venv item in item 7. Delete.
11. [B] `docs/CODEMAP.md:70` — search term `ensure_venv` resolves to nothing. Replace with `own_venv_bin`, `_start_detached`.
12. [B] `docs/CODEMAP.md:113` — "offline suite (74 files)" — 86. Drop the count.
13. [B] `ROADMAP.md:164` — "Telegram has /status" — it does not; `/status` is a Chainlit route. Name the real set.
14. [B] `docs/PROJECT_MAP.md:207` — "both under the public request policy" — locally localhost is open (ISS-0074). Add the clause.
15. [B] `docs/PROJECT_MAP.md:176` — "one-method Runner" — background starts exist (ISS-0075). Document.
16. [B] `docs/PRODUCT.md:160` — baseline omits background commands. Add.
17. [B] `DECISIONS.md:613` — "a fresh shell per command" contradicted by the background runner. Amend.
18. [B/E] `docs/CODEMAP.md` — no rows for `commands.py`, `folder.py`, `status.py`, `public/status.js`. Add.
19. [B] `README.md:183-191`, `CODEMAP.md:99-116` — layout blocks miss `public/` and new `app/agent/` modules. Regenerate.
20. [B] `chainlit.md:3` — "over Gemma 4 12B IT" — model is GLM via OpenRouter. Rewrite.
21. [B] `chainlit.md:7` — "Version 1.5 is the closed general-agent baseline." Delete.
22. [A/B] `chainlit.md:16` — "Filesystem access stays inside the configured workspace." — false locally. Rewrite to the folder rule.
23. [A/E] `config.toml:12-14` — unnamed set defaults to `http://127.0.0.1:8000/v1`, a vLLM that does not exist. Make it empty with a clear error.
24. [A/B] `AGENTS.md:101` — vLLM server gate for infrastructure that does not exist. Delete.
25. [A] `docs/PRODUCT.md:15` — "one owner and a small number of other users" read as licence to skip multi-user work. Capability-first wording.
26. [A] `DECISIONS.md:192` — same cap in the durable entry. Amend.
27. [A] `ROADMAP.md:7` — "the model is cheap, so the seconds to fight are the harness's own" frames model quality as out of scope. Keep latency, drop the premise.
28. [A] `ROADMAP.md:158` — "Chainlit stays for now" defers the decision that most limits parity. A dated item with a gate.
29. [A/E] `ROADMAP.md:288-289` + `DECISIONS.md:208` — `app/api/` waits for a separately hosted caller; the project's own UI cannot qualify. Amend.
30. [A] `DECISIONS.md:99-105` — embeddings wait for a measurement never scheduled (ISS-0028 still open). Schedule or lift.
31. [A] `DECISIONS.md:484` — "The 32k per-result cap stays" from the 65k era. Function of the budget.
32. [A] `DECISIONS.md:361-362` — instructions bounded at 8,000 bytes. Raise or derive.
33. [A] `DECISIONS.md:623` — "A Modal Sandbox is v2." with no owning item. Roadmap or delete.
34. [A] `DECISIONS.md:465-466` — "One implementation per capability" blocks pluggable backends. Restate as "no abstraction without a second consumer".
35. [A] `AGENTS.md:69-71` — "assume only one works in it at a time" forbids parallel-agent workflows. Concurrency rule instead.
36. [C] `AGENTS.md:64-67` — the literal-instructions rule is phrased around the one GLM case. Keep the rule, move the case to the report.
37. [C] `DECISIONS.md:414-425` — the fixed `send_file(path=…)` sentence appended by every tool. Measure, then remove if unearned.
38. [C] `docs/PROJECT_MAP.md:92-94` — "refused the third time" twice, from ISS-0013/0042; ISS-0069 records the guard producing an empty answer. General no-progress property or a setting.
39. [C] `DECISIONS.md:502-503` — "claimed three times is given up on". Setting with measurement.
40. [C] `docs/PROJECT_MAP.md:104-105` — `set_goal` and `todo_write` kept apart by history; measurement pending since 09-05. Resolve.
41. [D] `DECISIONS.md:16-61` — catalog missing six later entries. Regenerate.
42. [D/E] `DECISIONS.md:10` — "Entries are in date order." They are not.
43. [D] `AGENTS.md:140-144` vs `DECISIONS.md:786-804` vs `docs/PRODUCT.md:132` — root rule in three wordings, PRODUCT stricter. One place.
44. [D] `OPERATIONS_MAP.md:95` vs `DECISIONS.md:718` vs `config.toml:100` vs `config.py:462` — `turn_check_seconds` 360/600 in four places. One.
45. [D] `PROJECT_MAP.md:71-77`, `OPERATIONS_MAP.md:94-103`, `DECISIONS.md:717-726` — the no-ceiling paragraph three times. One.
46. [D] `README.md:38` vs `ROADMAP.md:202-203` vs `PRODUCT.md:55` — three states of `view_web_page`. Note the pending change once.
47. [D] `OPERATIONS_MAP.md:210-212` — `assistant-workspaces` volume listed twice. Merge.
48. [E] `ISSUES.md:109-203` — ISS-0071..0076 "fixed" under `## Open`. Move.
49. [E] `ISSUES.md` — ISS-0065, 0063, 0062, 0061, 0058, 0053 likewise. Move.
50. [E] `ISSUES.md` — 1,203 lines, over half Closed bodies. Catalog row plus report link.
51. [E] `OPERATIONS_MAP.md:182-204` — Model Apps section for unused GPU apps. Three lines.
52. [E] `config.toml:46-91` — five GPU model sets. Keep `or`, `gemini`.
53. [E] `ROADMAP.md:43-148` — Done section 105 lines with evidence summaries against `:297`. One line each.
54. [E] `ROADMAP.md:116-126` — item 24's fine-tune numbers. One line.
55. [E] `DECISIONS.md:152-159` — "Version 1 closed at Stage 3" historical. Delete.
56. [E] `ROADMAP.md:256-258` — "whole-code review of 2026-09-03, items 3 onward" names no work. Lift or drop.
57. [A/E] `ROADMAP.md:226-289` — parity items (`ask_user`, correctable plan, message during a long tool) unordered under Not started. Promote.
58. [A] `ROADMAP.md` — no item for subagents, skills, hooks, diff review, git; `PRODUCT.md:172` declines to decide. Add a parity gap list.
59. [D] `docs/PRODUCT.md:55` — omits `use_page` listed at `:85`. Add.
60. [B] `README.md:6-8` — rename history with the local path mismatch unrecorded. Note or drop.
61. [E] `CLAUDE.md:200-202` — duplicates `AGENTS.md:38-41`. One file.
62. [D] four "which document answers what" blocks. Keep the table in PRODUCT.

## §B Non-canonical docs (Sonnet)

| file | referenced from | state | recommendation |
|---|---|---|---|
| docs/MCP.md | ROADMAP, DECISIONS, maps, code, tests | current | keep |
| docs/v2_tool_system.md | DECISIONS, CODEMAP, PROJECT_MAP, shell.py, tests | current | keep |
| docs/v2_tool_system_design.md | orphan | superseded draft | move to reports/ |
| docs/step4_agent_harness_preparation_ru.md | orphan | Step 4 closed | move to reports/ |
| docs/step4_context_memory_addendum_ru.md | orphan | closed | move to reports/ |
| docs/v2_4_3_prompt_assembly_agents_handoff.md | orphan | 4.3 closed | move to reports/ |
| docs/native_single_call_mode_selection.md | orphan | pre-decision note | move to reports/ |
| docs/personal_assistant_direction.md | orphan | 2026-08-27, superseded | move to reports/ |
| docs/agent_future_directions_ru.md | orphan | speculative "R&D-полигон" | move to reports/ |
| docs/telegram_baseline_chat_product.md | orphan | queue item 2, closed | move to reports/ |
| docs/telegram_conversation_selection_task.md | orphan | shipped | move to reports/ |
| docs/telegram_real_answer_streaming.md | orphan | shipped | move to reports/ |
| docs/control_plane_cold_start_notes.md | orphan | GPU era | move to reports/ |
| docs/modal_platform_notes.md | orphan | vLLM era | move to reports/ |
| docs/modal_vllm_cold_start.md | orphan | vLLM era | move to reports/ |
| docs/self_hosted_llm_speculative_persistent_kv_research.md | orphan | Gemma/vLLM era | move to reports/ |
| docs/baseline_measurement_metrics_logs.md | orphan | partly stale | move or fold live parts into OPERATIONS_MAP |
| docs/comands.txt | orphan | GPU-era snippets | delete |

Hindering lines: `agent_future_directions_ru.md:7` "R&D-полигон";
`telegram_baseline_chat_product.md:9` "not a new agent architecture";
`telegram_conversation_selection_task.md:11` "not a general
session-management system"; `v2_4_3_prompt_assembly_agents_handoff.md:9`
"must not use remember_fact … or any automatic 'learn the user'";
`step4_agent_harness_preparation_ru.md:14` "not to keep growing the
answer/act split". None carries a "superseded" marker. No `scenarios/`,
`skills/` or `prompts/` directory exists.

## §C Agent core (Opus)

A — to the model: 1 `window.py:77` "Answer briefly."; 2 `:75-76` "if nothing is new, say nothing" + `produced()` None → empty turn; 3 `:53-54` "Prefer what is already there over installing"; 4 `:57` "Change one thing, then run it"; 5 `capabilities.py:135-137` "Nothing outside it exists for you" false when open; 6 `:168-171` own folder + own venv per piece of work; 7 `:256-263` todo discouragement with price; 8 `todo.py:48` planning off by default from one 09-03 measurement; 9 `capabilities.py:225-228` set_goal "before you start"; 10 `:359-367` fetch_page default, others "only when"; 11 `:372-374` file page "only with use_page"; 12 `:378-383` mandatory fetch after search; 13 `:205-209` mandatory look at every artifact; 14 `:78-81` "there are no others" vs dynamic MCP; 15 `graph.py:387-391` HEALTH_QUESTION as fake user turn; 16 `:366-375` DONE/REPEAT_REASON ends agency from a heuristic; 17 `todo.py:32-37` second brevity order; 18 `attachments.py:202-209` per-format routing table; 19 `summary.py:19-25` fixed four-section schema; 20 `graph.py:224` "do not try it again" absolute; 21 `instructions.py:33` 8,000 bytes.

B — mechanisms: 22 `graph.py:142` MAX_IDENTICAL_FAILURES=2 ends tools; 23 `:149` MAX_IDENTICAL_SUCCESSES=2 refuses a re-run; 24 `:918` changed_by mechanism for ISS-0019/0042; 25 `:826` `range(3)` fold attempts; 26 `graph.py:121`/`config.py:462` check every 600 s; 27 `runtime.py:72` RECURSION_LIMIT=1000 ends with an exception; 28 `window.py:33` MEDIA_BUDGET from vLLM MM_LIMITS; 29 `keep_results=2`; 30 STUB_MIN_CHARS=200; 31 keep_turns=2; 32 `summary.py:33-35` 150/15/600 words; 33 SUMMARY_ALLOWANCE=800; 34 KEEP_STEPS=2; 35 `choice.py:16` SIZES cap at 40% of 1.3M; 36 `web.py:74` 12,000 chars; 37 `web.py:600-602` view_web_page no offset; 38 `web.py:75,528` search count clamped to 10; 39 `web.py:56` ports 80/443; 40 `web.py:700-778` console errors [:5]; 41 `documents.py:214` MAX_PAGES_PER_VIEW=2 "serving limit is four images"; 42 `:52` MAX_CSV_ROWS=200; 43 `:209` PAGE_LONG_SIDE=1400 no zoom; 44 `attachments.py:16-18` 5 files/20MB/50MB for both profiles; 45 `:218-222` all-or-nothing batch; 46 `todo.py:71` NAMED_ITEMS=5; 47 `preflight.py:336-353` hand-written probe table, no run_command/MCP probe; 48 `graph.py:939-940` no checkpointer → silent decline.

C — tails: 49 `runtime.py:415` `if True:`; 50 `context_prompt` no caller; 51 `Agent.record` no caller; 52 `load_attachments`/`load_attachment_bytes` tests only; 53 `config.py:204` "gemma-4-12b-it"; 54 `:203` 127.0.0.1:8000; 55 `:231-234` Qwen thinking comment; 56 `:463-473` "4.6a" stale comment; 57 `:436-438` alt_database_url in product settings; 58 `preflight.py:38` Cost "gpu"; 59 `runtime.py:388` "wakes a GPU"; 60 `window.py:28-32` comment pointing at deploy; 61 `graph.py:433` "raise MODEL_MAX_TOKENS" to the person; 62 `capabilities.py:267-271` `_memory_lines` returns [].

D — leakage: 63 `attachments.py:34,183,187` INBOX default; 64 `:190-199` per-profile prompt text chosen in core; 65 `status.py:29-31` OpenRouter credits URL; 66 `todo.py:66` "/plan off in Telegram"; 67 `capabilities.py:36-47` Delivery default from the two existing interfaces; 68 `commands.py` reply wording in core; 69 `graph.py:411` "Stopped at your request." and AnswerWithdrawn; 70 `runtime.py:834,880-886` `open` bool through the constructor; 71 notes/path known.

E — structural: 72 `graph.py:956-969` sequential batch; 73 `:580-593` tool output never streams; 74 `:514` schemas frozen per compiled graph; 75 `capabilities.py:76-81` every schema every request; 76 `:1035-1049` no subagent seam; 77 `mode.py:21` no plan mode state; 78 `:873-891` stop checked only at run_tools top; 79 `:992-1000` interjections only in run_tools; 80 `:942-949` all-or-nothing batch approval before anything runs; 81 `summary.py:60` summariser sees stubs only; 82 compact re-injects nothing, shows nothing; 83 `:646-658` empty completion ends with no message; 84 `runtime.py:266` `_graphs` never evicted; 85 `:632` only ENDING can restrict tools.

## §D Tools (Opus)

A: 1-4 "Use this instead of ls/cat/sed" (`filesystem.py:344,366,434`) and `shell.py:625-627`; 5 `shell.py:627-630` traceback coaching every request; 6 `:643-646` "new environment" never printed; 7 `:536-541` UNWANTED_EXIT on every non-zero exit; 8 `filesystem.py:399-402` fence/order rules (ISS-0001); 9 `base.py:435-446` same; 10 `execution.py:252-258` prescribes write-then-edit for a cut; 11 `browser.py:370-373` coaching from ISS-0008; 12 `chromium.py:604` names a non-existent `max_chars`; 13 `todo.py:51-53`; 14 `goal.py:40-42` write-only; 15 `memory.py:56-58` forbids working preferences; 16 `base.py:161` handover sentence on every result; 17 `browser.py:110-114` .html/.htm only while `serve_directory` serves anything; 18 `web.py:284-286` forced follow with fetch_page.

B: 19 `filesystem.py:30` MAX_ENTRIES=200 no paging; 20 `:31` MAX_CHARS=20,000 chars not lines; 21 `:165-170` four image suffixes; 22 `shell.py:69-70` 120/600 s, silent clamp `:604`; 23 `:73-74` 16,000/4,000 no paging; 24 `:79` env allowlist of six names (no proxy, SSH, GIT_*); 25 `:219` BACKGROUND_SETTLE=2.0; 26 `execution.py:59-60` 32,000/2,000; 27 `:61` MAX_IMAGES=4 set from today's tools; 28 `documents.py:444` 20MB refusal; 29 MAX_PAGES_PER_VIEW=2 in code, schema, description; 30 MAX_CSV_ROWS=200; 31 `tools/documents.py:445` 12,000 and mid-block cut with no offset; 32 `web.py:56` ports 80/443, fetch_page not open locally; 33 `:61-72` eight media types; 34 `:74`; 35 `tools/web.py:202` MAX_VIEWS_KEPT=20 deletes screenshots; 36 `:298-303` count ≤10 and the tool vanishes without Firecrawl; 37 `browser.py:52` 2MB HTML; 38 `:57` IDLE_SECONDS=120; 39 `chromium.py:38,370,371` 8,000/12,000/120; 40 `:509` 12 options; 41 `:716` 6,000 px; 42 `:879` fixed 900×700; 43 `:208,241` 5 s CDP / 2.5 s load no parameter; 44 `history.py:29-31,170,226` 300/12,000/8/20; 45 `todo.py:44-45`, `goal.py:34`, `memory.py:13` hard refusals; 46 `presentation.py:25` Telegram album limit in core.

C: 47 no tree search; 48 no glob; 49 no line-range reads; 50 no multi-edit/patch; 51 no git awareness, no diff in write result; 52 `capabilities.py:107-109` web capabilities never get `open`; 53 no subagent tool; 54 no notebook; 55 no result streaming, `command_output` not deployed; 56 image reading four suffixes, no region; 57 browser: no scroll/hover/drag/back/wait/upload/tabs/network/resize; 58 nothing declares concurrency safety.

D: 59 `filesystem.py:263-270` "unchanged" wording from one run; 60 `:54-61` `<|`/`|>` and same-quote rule in every path tool; 61 `browser.py:259` favicon special case; 62 `:107-114` suffix dispatch; 63 `:276-351` `if action ==` ladder over one all-optional schema; 64 `mcp.py:280` approval by `not read_only`, `mutates=False` hard-coded; 65 `:256` strict per-tool allowlist; 66 `:261` `_tool` parameter collision; 67 `:61` "python" string-matched; 68 `capabilities.py:65-70` split by list position; 69 `shell.py:586,682-684` duck-typed `start`; 70 `execution.py:392-395` `_identity` keys on `path`; 71 `base.py:251` five other harnesses' prefixes.

E: 72 `shell.py:610,616` BAD_ARGUMENTS undefined; 73 `:204,373` Any unimported; 74 `Finished.fresh` dead; 75 `:222,365,370` unused workspace/cwd; 76 `filesystem.py:210-213` unused root; 77 `shell.py:326,524` `Path.home()` at import; 78 `base.py:255` LEGACY_NAMES={}; 79 `:118` OUTPUT_CUT not exported; 80 `:453-468` validation ignores enum/min/max/items; 81 `chromium.py:44-58` hard-coded browser paths, `Pages.browser` never set; 82 `:531-546` 14 keys, wrong failure codes `:840-868`; 83 `shell_windows.py:157` quoting, pywin32 ImportError swallowed `shell.py:60-63`; 84 sys.platform branches: `shell.py:92,126,165,252,274,291,329,330,331,395,398,461,462`, `chromium.py:74`; SITECUSTOMIZE `shell.py:119-157` injected into every Python; 85 `web.py:402-410` `web_tools` no caller; 86 `chromium.py:634-660` data: branch unreachable; 87 `browser.py:337` screenshot naming.

## §E Models, memory, telemetry (Opus)

A: 1 `config.py:225` max_tokens=8192; 2 `base.py:22` CHARS_PER_TOKEN=3.0, no calibration on media requests; 3 `:29` MEDIA_TOKENS flat, "file": 0; 4 `openai_compatible.py:43-55` calibration weight/min/ceiling 6.0; 5 `:76` retries=2, backoff 0.5, Retry-After ignored; 6 `:80` REDIRECT_HOPS=8 Modal-era; 7 `config.py:220` one 600 s timeout for connect/read/stream; 8 `instructions.py:33` 8,000; 9 `memory/base.py:228` search limit 5, `retrieved_facts=5`; 10 `:232` facts limit 50 no paging; 11 `:177` search_messages 8; 12 `trace.py:47` FLUSH_AT=25 on the turn's thread; 13 `telemetry/base.py:138` recent_runs 20; 14 `openai_compatible.py:838,853` MODEL_DUMP_DIR unbounded; 15 `records.py:52` inbound media base64 in TEXT, re-decoded every turn.

B: 16 `:658` tool_choice="auto" fixed; 17 no parallel_tool_calls; 18 no cache hints; 19 `:490-493` reasoning dropped, no Completion.reasoning; 20 `base.py:191` no provider/model/id on Completion; 21 `:650-655` only model/messages/temperature/max_tokens; 22 `config.py:204` gemma default; 23 `:464` END_MARKERS stripped for every provider; 24 `:668-671` provider by URL substring, OpenRouter features unused; 25 `:676` provider order only; 26 `base.py:252` warm only from Telegram; TracedBackend does not forward warm; 27 nor estimate_tokens; 28 `:897` context_limit vLLM-only; 29 `:114-133` media inline only, `file` part raises; 30 `:761` no mid-stream resume; 31 `base.py:230/239` no per-call overrides.

C: 32 `cost.py:37,42` A10 cost model as the headline metric; 33 `telemetry/vllm.py` 322 lines, tools only; 34 `openai_compatible.py:245-351` `<|"|>` repair for every provider; 35 `:149-192` system flattening into user text; 36 `compactions()` no caller; 37 `forget()` only loop_live; 38 `telemetry/postgres.py:120` no CONNECTION_GUARDS; 39 `:30` no migration path; 40 `store.py:226` no WAL/busy_timeout; 41 both stores synchronous on the event loop; 42 `base.py:151-155` "schema-3 migration" stale comment; 43 search matching diverges (`postgres.py:575` vs `store.py:385`); 44 append atomicity diverges; 45 `SELECT *` vs columns; 46 `inspect.py:30` tool_skipped never emitted.

D: 47 `trace.py:170` `telegram_{how}` events; 48 `telemetry/base.py:75` source_update_id; 49 `memory/base.py:35` LOCAL_USER_ID as schema default; 50 `ContentPart.path`; 51 `ContentPart.hidden` read only by chainlit_history; 52 `openai_compatible.py:201` "The selected item was prepared for delivery."; 53 `records.py:33-38` "Sent … bytes." receipt; 54 notes table.

E: 55 `Usage.cost` never stored; 56 `first_model_token_ms` streamed only; 57 Chainlit opens no telemetry; 58 `route` set by Telegram/MCP only; 59 `trace.step` one call site ("persist"); 60 `trace.py:536` `_active` leaks; 61 `:344-352` tool_finished carries message prose; 62 `inspect.py:401` cost per successful turn divides by 1 when none.

## §F UI, deploy, scripts, tools (Opus)

A: 1-2 `.chainlit/config.toml:72-73` max_files=5, 20MB; 3 `attachments.py:16-18`; 4 `:65-68` `_check_kind` still reachable; 5 `:20-29` MEDIA_KINDS 3+5; 6 `chainlit_app.py:77` CONFIRM_TIMEOUT=600 → no; 7 `:262-277` serial modal approvals, no approve-all; 8 `:269` raw JSON, no diff; 9 `:242` "Completed."; 10 `chainlit_history.py:117-118` same on reload; 11 `:230-259` no AssistantDelta; 12 `:213-220` steps without output/time/state; 13 `:225-226` "N tool calls"; 14 `:82-88` five commands vs ten; 15 `telegram/adapter.py:811-826` no WORKSPACE_COMMANDS; 16 `:394` command list only on error; 17 plan a switch, list rendered only in Telegram; 18 `todo.py:66`; 19 `status.js:6,12` polled, hidden; 20 `:89` "not an OpenRouter set"; 21 `:99` folder not changeable; 22-23 `status.css` dark-first, pixel-positioned; 24-25 `chainlit.md:3,7` Gemma, Version 1.5; 26-28 inbox comments in `config.toml:69`, `chainlit_history.py:252`, `folder.py:10`; 29 audio off; 30 html/latex off; 31 favorites/sharing off; 32 `chainlit_history.py:50` title = first 80 chars; 33 `adapter.py:514` CONVERSATION_CHOICES=10; 34 `:199-200` 12 items/80 chars; 35 `:154-176` TOOL_ACTIVITY dictionary; 36 `:1246` raw dict approval; 37 `api.py:520` last piece only; 38 `:306,310` preview constants; 39 `:123` ALBUM_LIMIT mirrored in core.

B: 40 `presentation.py:25`; 41 `attachments.py:159-187` inbox default; 42 `todo.py:66`; 43 `openai_compatible.py:67` Telegram comment; 44 `stop.py:10-11` sequence in Telegram dialect; 45 `control_app.py:542,623,693` import Telegram DELIVERY for probes; 46 `mcp_server.py:469,568`, `control_app.py:710` loop_live.Turn as third adapter; 47 fold notice twice; 48 dispatch chain twice; 49 `chainlit_app.py:286-324` process globals, second tab wrong status; 50 `:313` `AgentSettings(database_url="")` in the adapter.

C: 51-53 three GPU apps; 54 `assistant-llm` fourth app in comments/README; 55 `autoscale.py`; 56 `deploy/modal/README.md:82,90` two endpoints; 57 `config.toml:12-14`; 58 `:46-60` retired sets; 59 `scripts/doctor.py:18`; 60 `telemetry/vllm.py`; 61 `tools/vllm_baseline.py`; 62 `scripts/measure_endpoint_wake.py`; 63 `tools/gemma4_parser.py`; 64 WORKER_TIMEOUT_SECONDS duplicated; 65 `control_app.py:70` ships scripts/ in the product image; 66 no ui group.

D: 67-71 dead scripts `stage3_live`, `v1_live`, `smoke_test`, `migrate_workspace`, `measure_command_cold_start`; 72-74 `judge_pack.py:28-52` rubric (b)/(e) reward fewer calls and short answers; 75 `loop_live.py:706` "write_file then use_page"; 76 `:713` "at most five write_file"; 77 `:752` "read_history" demanded; 78 `:459` "read_file ran"; 79-80 `prompt_scenarios.py:115,168,190,199` expected_tools routes; 81 `:67-68` a tool call as cost regression; 82 `:47` A10 pricing; 83 `work_log.py:16` schema 1; 84 `mcp_server.py:596` max_tool_calls=40; 85 `:186` head_chars=400; 86 `:140`; 87 `:223-224` in-process imports vs subprocess rule; 88 `prune_probes.py:20` delete by naming convention.

E: 89 `wire.py:49-51` MODEL_FREE_COMMANDS hand-kept; 90 `:61` INSTRUCTION_COMMANDS from one typo; 91 `:192-203` three classifications of /stop; 92 `adapter.py:792-867` eight-branch string dispatch; 93 `:439-462` clear/set special cases; 94-95 `:1261-1287` callback prefixes and suffix state; 96 `chainlit_app.py:186` two kinds + File; 97 `:390` "off" literal; 98 `adapter.py:132-141` MEDIA_SUFFIXES → .bin; 99 `api.py:224-228` substring error classification; 100 `:499,508` "not modified"; 101-103 SelectorEventLoop branch in `loop_live.py:1153`, `mcp_server.py:547`, `setup_control_plane.py:62`; 104 `mcp_server.py:91-119` Windows pump as universal; 105 `chainlit_app.py:29` LOCALAPPDATA; 106 `:137` temp uploads never cleaned; 107 `control_app.py:300-313` `where` inventory string vs BASE_PACKAGES/BASE_TOOLS; 108 `:310-312` venv workflow prescribed, checked by loop_live, scored by the judge; 109 `:229` scaledown 180 s unreachable by settings; 110 `:233` timeout=660 the ceiling of any command.

## §G Tests and scenarios (Sonnet, two passes)

Findings: `tools/prompt_scenarios.py:226-233` `met()` fails any scenario with empty `expected_tools` on any tool call; `tools/judge_pack.py:36-38` "five reads where one search would do" names a route; `tests/test_repeated_failure.py:20-22,263` pins MAX_IDENTICAL_FAILURES=2. Transport bounds (MAX_RESULT_CHARS, MAX_IMAGES, MAX_ITEMS, MAX_INSTRUCTION_BYTES) judged bounds, not crutches. `test_gemma4_parser.py`, `test_vllm_metrics.py` test live modules. `test_turn_bounds.py` asserts no ceiling on steps/calls/seconds (keep). Verbatim model-facing wording pinned: `test_capability_brief.py:132-472`, `test_context.py:97,598,673-674`, `test_goal.py:64-66`, `test_run_command.py:275,280,299`, `test_tool_contracts.py:58-71`, `test_turn_stopping.py:315,323`, `test_web.py:171`, `test_web_tools.py:159-161`. Coverage gaps: git, MCP on demand, subagents, a long-running server, propose-then-approve plan.
