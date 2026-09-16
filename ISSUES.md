# Issues

Observed defects, one entry per defect. This file is not a plan and
authorizes nothing; `ROADMAP.md` alone orders work. Evidence lives in
`reports/`, the entry links to it.

## Rules

- A defect is observed behaviour, not a suspicion; a missing capability
  belongs in the roadmap, a false claim of one belongs here.
- New entry: next free number, never reused; put it at the top of **Open**
  and a row at the top of the catalog.
- Status names the defect, not the effort: `open`, `mitigated` (harm
  reduced, defect still there), `fixed` (verified), `won't fix`. A closed
  entry stays, shortened, under **Closed**; a defect that comes back moves
  up to **Open** with a "seen again" line and keeps its full body.
- `Cause` stays "unknown" until proven. One defect per entry. `Costs` says
  what it does to the person; there is no severity word.
- Fields: Status, Seen, Costs, Reproduce, Cause, Evidence, Related.

## Catalog

| Id | Status | Defect | Related |
|---|---|---|---|
| ISS-0076 | fixed 2026-09-14 | after a page reload a turn's tool calls come back as separate rows: the one collapsed step per turn is not kept | 0071, roadmap 27 |
| ISS-0075 | fixed 2026-09-14 | a server the model starts with `start` opens a console window on the person's desktop and outlives the conversation untracked; `run_command` has no background mode | roadmap 27 |
| ISS-0074 | fixed 2026-09-14 | `use_page open http://localhost:…` is refused (`ERR_ACCESS_DENIED`): the browser's public-only policy also holds on the person's own machine | roadmap 27 |
| ISS-0073 | fixed 2026-09-14 | a GitHub MCP file's content comes back as `EmbeddedResource` and is dropped by the renderer: the model reads "1 non-text part(s) not shown" and fetches the file again from the web | roadmap 26 |
| ISS-0072 | fixed 2026-09-14 | after the websocket reconnects a message runs on a closed agent: `Cannot send a request, as the client has been closed`; the turn dies | 0071 |
| ISS-0071 | fixed 2026-09-14 | the status route holds the agent of a closed session: `/status` after a tab is closed fails on a closed database | 0072 |
| ISS-0070 | open | the first turn on a fresh deployed worker spends ~150 s building the graph before the first model call; the next turns 33 ms | roadmap 18, 26 |
| ISS-0069 | open | after the repeat guard Gemma answers nothing: the request goes out with no tools, the model emits 16–171 tokens, the harness receives no text and no call | 0068, roadmap 24 |
| ISS-0068 | fixed 2026-09-14 | the sandboxed `run_command` kills every Cygwin tool (`sh`, `ls`, `find`, `cat`, `awk`): `CreateFileMapping … Win32 error 5` | 0053, roadmap 24, 27 |
| ISS-0067 | open, pruned once 2026-09-11 | every checkpoint version of every thread is kept forever; 368 of Neon's 394 MB were old versions | 0066 |
| ISS-0066 | open | every turn's context load carries the bytes of every image still in history, and then shows the model a stub | 0065, roadmap 18 |
| ISS-0065 | fixed 2026-09-08 | a `send_file` result carries the file's bytes into the thread's history | 0064, roadmap 23 |
| ISS-0064 | open, fix built | `persist` hangs on the store's write and the worker sits in it until the platform kills it | 0061, 0048, 0065, roadmap 23 |
| ISS-0063 | fixed 2026-09-07 | a dead worker's lease holds the conversation; nothing wakes the queue when it expires | 0061, 0062, roadmap 22 |
| ISS-0062 | fixed 2026-09-07 | the retry of a killed turn sends the final answer a second time | 0061, roadmap 22 |
| ISS-0061 | fixed 2026-09-07 | a turn runs up to the worker's own timeout and is killed while persisting | 0057, 0056, 0064, roadmap 14, 22 |
| ISS-0060 | open | deployed `use_page open url` renders a public page in the worker, beside the secrets | 0051, roadmap 21 |
| ISS-0059 | open | a question sent mid-turn is answered and the task it interrupted stops to ask "continue?" | roadmap 15, 20 |
| ISS-0058 | fixed 2026-09-08 | command temp files and caches on the Volume path: too long for a socket, wrong uid | 0053, 0057, roadmap 17 |
| ISS-0057 | fixed 2026-09-07 | the turn's seconds budget counts tool run time and provider queue | 0056, 0054 |
| ISS-0056 | fixed 2026-09-10, as named | seconds between a turn's steps that no model, tool or store accounts for | 0066, roadmap 18 |
| ISS-0055 | fixed 2026-09-06 | an output cap spent on reasoning delivered as an empty answer | 0031 |
| ISS-0054 | open, GPU Apps only | the model endpoint sleeps mid-turn when a tool outlives the idle window | 0044 |
| ISS-0053 | fixed 2026-09-08 | what a command installs outside the workspace is gone by the next command | 0058, 0043, roadmap 17 |
| ISS-0052 | fixed 2026-09-05 | a non-first system message refused by Qwen3.8's template | |
| ISS-0051 | fixed 2026-09-05 | the renderer's first look in a cold container fails before the browser is up | |
| ISS-0050 | worked around, GPU Apps only | vLLM AOT compile of the INT4 checkpoint dies on a renamed weight | 0049 |
| ISS-0049 | won't fix, GPU Apps only | the platform retries a failed server start for as long as a request waits | 0050 |
| ISS-0048 | fixed 2026-09-05 | the store's connection, hung up on during a long call, fails the next turn | |
| ISS-0047 | fixed 2026-09-05 | a GPU snapshot holding an uncommitted Volume path cannot be restored | |
| ISS-0046 | open, model | a deliverable the tools can make is fabricated by another means | 0040, 0004 |
| ISS-0045 | fixed 2026-09-05 | deployed history search cannot find a file name by its parts | |
| ISS-0044 | fixed 2026-09-05 | the first request to a sleeping endpoint dies at the read timeout / the edge's 303 | 0054 |
| ISS-0043 | fixed 2026-09-04 | in the container `pip` is not `python3`'s pip | 0038 |
| ISS-0042 | fixed 2026-09-04 | a command refused as "already done" after its file was rewritten | 0019, 0013 |
| ISS-0041 | fixed 2026-09-04 | a command's errors shortened away within the turn that is fixing them | 0022 |
| ISS-0040 | open, model | a document is handed over without a look at it | 0039, 0046 |
| ISS-0039 | open, model | a command that succeeds silently is not believed and is run again | 0004 |
| ISS-0038 | fixed 2026-09-04 | a package installed into the machine's own Python rather than the workspace | 0043 |
| ISS-0037 | open | the failure message to the person carries the endpoint URL and server body | |
| ISS-0036 | open | a transient failure before the first streamed token fails the turn | 0044 |
| ISS-0035 | open | the approval-resume path in Telegram lacks the answer path's fixes | 0009 |
| ISS-0034 | fixed 2026-09-04 | a worker's lease outlives the container's kill by five minutes | 0033 |
| ISS-0033 | open | no tool has a deadline; a hung tool holds the worker until the platform kills it | 0034 |
| ISS-0032 | fixed 2026-09-04, count rule removed 2026-09-07 | the conversation folds every twelve messages whatever their size | 0057 |
| ISS-0031 | fixed 2026-09-04 | a tool call cut at the output limit reported as bad JSON | 0055 |
| ISS-0030 | fixed 2026-09-04 | the summarizer handed every tool result in full | 0029 |
| ISS-0029 | fixed 2026-09-04 | a summarizer request that does not fit fails a delivered turn | 0030 |
| ISS-0028 | open, model + retrieval | the model says it checked its memory without calling anything | 0004 |
| ISS-0027 | fixed 2026-09-03 | a shortened result's stub invited the model to run the tool again | 0026 |
| ISS-0026 | fixed 2026-09-03 | reading a call back did not show what it returned | 0027 |
| ISS-0025 | fixed 2026-09-03 | `/compact` recorded as a failed turn | |
| ISS-0024 | fixed 2026-09-03, GPU Apps only | the server does not say what it served from cache | |
| ISS-0023 | fixed 2026-09-03 | a forced fold finds nothing to cut in a tool-heavy tail | |
| ISS-0022 | fixed 2026-09-03 | shortening the model's own file arguments made it write every file again | 0041 |
| ISS-0021 | fixed 2026-09-03 | the end-of-turn token reaches the chat as a message | |
| ISS-0020 | fixed 2026-09-03 | a delivery refused when the turn's budget is spent | 0003 |
| ISS-0019 | mitigated 2026-09-04 | a page written twice, identically | 0042, 0016 |
| ISS-0018 | open | an existing folder is replaced without a word | 0004 |
| ISS-0017 | fixed 2026-09-03 | the screenshot sent is of the page without its CDN styles | 0014 |
| ISS-0016 | open | the plan is a list of phases, ticked in bulk | 0008, roadmap 8 |
| ISS-0015 | open | a written page ends with a markdown fence | 0001 |
| ISS-0014 | fixed 2026-09-03 | a page looked at without storage or its own files | |
| ISS-0013 | fixed 2026-09-03 | the repeat guard refused the call that would have worked | 0012, 0042 |
| ISS-0012 | fixed 2026-09-03 | a corrupted path was obeyed, a file nobody named was made | 0001 |
| ISS-0011 | fixed 2026-09-03 | every look at a page carried the whole page back as its address | |
| ISS-0010 | open | "here is the screenshot", and nothing was sent | 0003, roadmap 11 |
| ISS-0009 | fixed 2026-09-03 | an answer read for a minute and then deleted | 0035 |
| ISS-0008 | open | a generated app delivered as working without ever being used | 0004, roadmap 11–12 |
| ISS-0007 | fixed 2026-09-03 | `tool_failed` carries no reason | |
| ISS-0006 | fixed 2026-08-31 | a path meant as a directory becomes a file | 0005 |
| ISS-0005 | fixed 2026-08-31 | an OS error escapes the filesystem tools unwrapped | 0006 |
| ISS-0004 | open, model | the assistant describes what it did not observe | 0008, 0028 |
| ISS-0003 | mitigated 2026-09-03 | a made file handed over as prose instead of sent | 0010, 0020 |
| ISS-0002 | open | a picture someone sends is never kept | roadmap Not started |
| ISS-0001 | mitigated, GPU Apps only | vLLM's Gemma 4 tool parser loses what follows a long string | 0012, 0015 |

"GPU Apps only" marks a defect of the vLLM Apps, which are deployed but not
in use since 2026-09-06; it is not seen on the hosted model.

---

## Open

### ISS-0070 — the first turn on a fresh worker builds the graph for ~150 s

- **Status:** open. Seen on the measurement of the fine-tune queue's item 1.
- **Seen:** 2026-09-12, the first case of both `scenarios` calls on the
  second workspace's `assistant-control`: `graph_built` at 151.1 s
  (`deployed-dpo-9a6f2f66-210`) and 155.2 s (`deployed-base-7d7c629d-210`),
  before any model call; every later case of the same container built it
  in 26–33 ms. The earlier cancelled pair (`deployed-dpo-9620fd20-210`,
  `deployed-base-12b85d07-210`) was still inside the build at 115 s when
  it was cancelled. The morning's `deployed-base-71d445ba-*` runs, on the
  copy deployed before item 26, show no such first turn. Seen again the
  same evening: `deployed-base-3d106de2-210` was killed at 54.3 s, still
  inside the build, and the platform reran the input on a fresh container
  (`deployed-base-40e5ad1a-*`, its first build 37.0 s); the `scenarios`
  Function has no retries of its own and reserves 2 GiB.
- **Costs:** two and a half minutes of a person's first turn on a cold
  worker, paid as CPU seconds and as waiting; a scenario's timing is
  wrong by that much.
- **Reproduce:** a fresh `assistant-control` container with
  `[mcp.servers.time]` and `[mcp.servers.github]` configured; the first
  turn's `graph_built` span.
- **Cause:** not shown. The build is synchronous and, since item 26, it
  opens the MCP sessions and lists their tools on first use: two servers
  with a 60 s timeout each is the right order of magnitude, but which
  of them waits, and on what, is not recorded (the `ask` Function's turn
  of the same day took 17 s in all, with both servers answering).
- **Belongs to:** the harness (`app/tools/mcp.py`, `McpSessions` on the
  toolbox build); the seconds should be named on the trace (roadmap 18).

### ISS-0069 — after the repeat guard, Gemma answers nothing

- **Status:** open. Seen through the fine-tune measurement (roadmap 24);
  older than the fine-tune.
- **Seen:** 2026-09-12, five guard-ended turns of the tuned Gemma on the
  local harness (D4 D5 V1 V6 V7), and the untuned Gemma's X2 on the
  deployed Linux worker on 2026-09-11 (`deployed-v2-aaa50fb0-820`): once
  the guard says "no further tools will run", the next request carries no
  `tools`; the model returns `finish_reason: stop` with 16–171 output
  tokens; the harness gets an empty text and no call, and the person an
  empty answer. GLM in the same state answers in words.
- **Costs:** every turn Gemma loops into the guard ends with nothing said.
- **Reproduce:** any repeat loop on a Gemma set through vLLM; the last
  completion's usage against its text.
- **Cause:** not shown. The likely shape: the model emits a `<|tool_call>`
  without a declaration to match, and vLLM's `gemma4` tool parser drops
  it, content and all. What the tokens were is not recorded locally
  (`dump_dir` is unset in the local profile); the deployed `.sse` dump of
  run 820 would show it.
- **Belongs to:** the harness (the request shape after the guard, and
  what it does with a completion that has tokens but no content) with the
  serving stack; not the model alone.

### ISS-0067 — every checkpoint version of every thread is kept forever

- **Status:** open; pruned once by hand 2026-09-11 on the human's word
  (the latest checkpoint of each of 93 threads kept with the blobs it
  references, 5,569 older checkpoints, 8,749 blobs and 26,332 writes
  deleted, `VACUUM FULL`). No mechanism prunes; the human, 2026-09-11:
  not needed now — data generation will write its checkpoints to SQLite
  on the Volume, and the product's own growth has headroom again.
- **Seen:** 2026-09-11, the deployed database at 394 MB of a 500 MB plan:
  `public.checkpoint_blobs` 254 MB and `checkpoint_writes` 114 MB against
  14 MB of conversation and 5 MB of telemetry. One thread held 152 MB in
  199 checkpoint versions; the `messages` channel alone 156 MB, `context`
  97 MB — the whole message list and the assembled context, image bytes
  included (ISS-0066), written again after every node of every turn, and
  never removed. The probe user's threads were 36 MB of it; the owner's
  own, 332 MB.
- **Costs:** the database fills at the rate of the owner's own use — a
  long turn with pictures adds tens of megabytes — until writes fail;
  nothing in the product reads a version older than the latest.
- **Reproduce:** run any turn; count `public.checkpoints` rows for the
  thread before and after.
- **Cause:** known. LangGraph's Postgres saver keeps every checkpoint so a
  thread can be replayed from any point; the harness uses only the latest
  (`Agent.unfinished`, `delivered_before`) and never asks it to forget.
- **Evidence:** the numbers above; the prune is `tools/prune_checkpoints.py`
  (dry run by default, `--apply` is a gate).
- **Related:** ISS-0066 (what makes each version large).

### ISS-0066 — every turn's context load carries the bytes of every image still in history, and then shows the model a stub

- **Status:** open.
- **Seen:** 2026-09-08, deployed, thread `dee4ba72` (the first person on
  this system besides its owner). Two `use_page screenshot` results hold
  35,615 and 36,621 bytes of PNG: 72,236 of the thread's 114,083 bytes.
  `PostgresStore.turn_context` selects the whole unsummarized history in
  one round trip, media parts included, and `Context.surface` then replaces
  every tool result but the newest two with a stub. The third turn
  (`57201a1e`, 18:04:59 UTC) reported `stubbed=14`: it fetched both images
  across the network and showed the model neither.
- **Costs:** the fixed cost at the start of every turn grows with each
  picture the conversation has ever contained, and it is paid on the
  person's clock — 2.15 s of queue wait and 3.5 s more before the first
  step in that turn. The store keeps and re-reads bytes no request will
  carry. It grows without bound until a fold summarizes those positions
  away.
- **Reproduce:** deployed, take a screenshot in one turn, send a message in
  the next, and read `context_prepared`: `stubbed` counts what was fetched
  and then hidden.
- **Cause:** known. History is loaded whole and shortened afterwards
  (`app/context/window.py`): shortening is a property of the request's
  surface, and the store is never told which positions the surface will
  stub. Nothing yet asks for history without its media.
- **Evidence:** the run rows and the thread are in the database; the
  numbers above are from `tools/show_run.py 57201a1e37ca4ec7a2f213978229bb6e`.
- **Related:** ISS-0065 (the same bytes on the outbound side, fixed by
  roadmap 23); roadmap 18, whose second half decides what the harness
  stops spending.

### ISS-0064 — `persist` hangs on the store's write and the worker sits in it until the platform kills it

- **Status:** open; fix built 2026-09-08 (roadmap 23: `CONNECTION_GUARDS`
  on every store and inbox connection, so the wait ends in about a minute
  as an `OperationalError` and the worker's retry resumes `persist` on a
  fresh connection), deployed 2026-09-08; a hang has not been seen since
  (one sent file, 06:08 UTC, persisted in 3.5 s). The cause stays unknown; what is
  bounded is the wait, and the row that waited is a hundred bytes now
  (ISS-0065). 2026-09-16 (roadmap 29): the telemetry store's connection
  had been opened without the guards; it has them now, not yet deployed.
- **Seen:** 2026-09-07 15:26–19:52 UTC, deployed. Turn 814913253 (a
  four-minute Blender render, 11 model calls, 10 tools) sent its answer and
  the video at 15:26:33 and entered `persist`; `persist_finished` never
  came. The store's write is synchronous psycopg on the event loop, so the
  heartbeat stopped with it, the lease ran out, and the worker of the next
  message (814913255, 15:51) took the update up as attempt 2 — and hung in
  `persist` the same way, seven seconds into a fresh container. Attempt 3
  (the worker of 814913257, 15:53:33) wrote the same rows in 1.2 s. Both
  hung containers lived on until their 14400 s timeout: killed at 19:22:27
  and 19:51:29, "failed to respond to cancellation for too long".
  ISS-0061 (14:33, `persist_started` at 598 s, killed at 600 s) is most
  likely this hang seen through the old timeout, not a slow turn.
- **Costs:** two CPU containers for four hours each doing nothing; the
  turn's history reaches the store 27 minutes late and only because a
  later message brought a third worker. The person saw nothing wrong.
- **Reproduce:** deployed, a turn whose `persist` writes a megabyte row
  (ISS-0065) — twice out of three attempts on 2026-09-07; not reproduced on
  purpose.
- **Cause:** unknown. What is known: the write is `psycopg.connect` with no
  `connect_timeout`, no `statement_timeout`, no TCP keepalive, sent in
  pipeline mode through Neon's pooled endpoint; the row was 1 MB; the same
  write from a third container went through at once. Whether the client
  waits on a dead socket, the pooler on the pipeline, or the server on a
  lock is not shown by the logs.
- **Evidence:** `reports/2026-09-08_persist_hang_logs.txt`.
- **Related:** ISS-0061, ISS-0048 (the store's connection after a long
  call), ISS-0065.

### ISS-0060 — deployed, `use_page open url` renders a public page in the worker, beside the secrets

- **Status:** open
- **Seen:** 2026-09-07, by reading the code after the deployed mini set run
  (W chose `fetch_page`; had it chosen `use_page`, the page would have
  rendered in the worker).
- **Costs:** a stranger's JavaScript runs in the container that holds the
  bot token, the model key and the database URL, under Chromium with
  `--no-sandbox` as root — the boundary `render_web_page` exists to keep.
- **Reproduce:** deployed, ask to click something on a public page.
- **Cause:** item 16 gave `use_page open` a `url` and opened it with the
  worker's own browser, ignoring `WEB_LOCAL_BROWSER=0`; the author (the
  agent) knew the flag, edited the comment beside it, and did not raise the
  change as a gate before the deploy. A rule breach, not an oversight.
- **Evidence:** `app/tools/browser.py` `Pages.open`; `reports/2026-09-07_item16_build.md` §3.
- **Related:** ISS-0051; roadmap 21 closes it.

### ISS-0059 — a question sent mid-turn is answered and the task it interrupted stops to ask "continue?"

- **Status:** open
- **Seen:** 2026-09-07, live, run `017285ac3d60453788f5aec1a6212c5d`
  (Telegram, GLM at Novita), the first turn with item 20 deployed: the task
  was to read three files one by one, write a summary and send it as a
  file; after the second read the person's screenshot with "что тут?" was
  taken at the step boundary (as designed); the model described the
  screenshot correctly and ended the turn with "Продолжаю? Осталось
  прочитать CODEMAP.md и сделать сводку файлом." The third file, the
  summary and the file wait for a "да". The request dump
  (`20260907T110123-2-3.sse`) shows the harness told it the truth: both
  results whole, the screenshot with its caption after them; the thread's
  history held an identical earlier exchange (a photo with "что тут?"
  answered as its own turn), which is the pattern it followed.
- **Costs:** a comment during a long task pauses the task until the person
  says to go on; a coding agent's chat answers and continues.
- **Reproduce:** a multi-step task in Telegram, a question sent while it
  runs, with the deployed prompt (which says nothing about mid-turn
  messages, as the references do not, report §7).
- **Cause:** the model's choice; the harness delivered the message where a
  coding agent's chat does. Sorting per the rule: model, or one literal
  line of instruction ("a user message after tool results is a comment on
  the work in progress; answer it, then continue the task without asking
  unless it says to stop or change it"). To be measured with the mini suite
  (roadmap 15) before either is chosen; not a harness mechanism.
  2026-09-07, the human's word: Codex's prompt has a general persistence
  rule ("keep going until the task is completely resolved, before ending
  your turn") and ours had none, so one literal persistence rule was added
  to `WORKING_METHOD` (finish the task in this turn; a message during the
  work is a comment, answer and continue; stop only if told). Effect not
  yet measured; the scenario goes into item 15's set.
- **Evidence:** `reports/2026-09-07_mid_turn_message.md` §9.
- **Related:** roadmap 15, 20.

### ISS-0054 — the model endpoint is put to sleep in the middle of a turn whenever a tool outlives the idle window

- **Status:** open, GPU Apps only; not scheduled
- **Seen:** 2026-09-05, scenario G on the INT4 App (`deployed-c0c0a622-70`):
  ten `run_command` calls, several longer than the 12 s `SCALEDOWN_WINDOW`,
  and the endpoint scaled to zero between the model's own calls three times
  in one turn; each next call paid a restore of 20–44 s.
- **Costs:** the person waits 20–45 s in the middle of their request, once
  per long tool.
- **Reproduce:** on a GPU App, any tool longer than 12 s.
- **Cause:** the idle window is a property of the endpoint alone; nothing
  tells it a turn is still open. The smallest form of a fix: keep the
  endpoint warm while a tool of the current turn runs, nothing between
  turns.
- **Evidence:** `reports/2026-09-05_qwen38_second_model.md` §13.
- **Related:** ISS-0044.

### ISS-0046 — a deliverable the tools can make is fabricated by another means

- **Status:** open; model behaviour, observed once
- **Seen:** 2026-09-05, deployed, scenario G (`deployed-cf8c3774-70`):
  asked for a screenshot of the page it built, the model wrote "I can't
  actually take a screenshot … in a headless container", drew a white
  800×600 PNG with PIL, named it `task_board_preview.png` and sent it as
  the screenshot. `inspect_page` was never called; the four G runs before
  it that day had called it.
- **Costs:** a picture that shows nothing, presented as the screenshot;
  worse than none.
- **Reproduce:** G deployed; one in five that day.
- **Cause:** the model's, on the evidence; measured by G, no mechanism.
- **Related:** ISS-0040, ISS-0004.

### ISS-0040 — a document is handed over without a look at it

- **Status:** open; model behaviour, measured
- **Seen:** 2026-09-04, deployed, Telegram, runs `a3ef6a14` and `3935092c`
  (P, "a one-page PDF about coffee"): `write_file`, `run_command`,
  `send_file`, no `view_pages` or `read_document`, twice. The first PDF
  reached the person as black squares (no Cyrillic font in the container,
  fixed the same day); a look would have shown it. 2026-09-05, three
  deployed P runs checked the PDF with `pdftotext` before sending, a look
  at the text, not the page; the scenario counts only `view_pages`.
- **Costs:** the person receives a document the assistant never saw.
- **Reproduce:** P through Telegram; not every time.
- **Cause:** the model's; the brief says a made document is looked at
  before it is handed over. Measured with the suite, not scripted.
- **Evidence:** `reports/2026-09-04_v2_isolated_execution_review.md` §12.
- **Related:** ISS-0039, ISS-0046, ISS-0028.

### ISS-0039 — a command that succeeds silently is not believed, and is run again

- **Status:** open; model behaviour, measured
- **Seen:** 2026-09-04, `scripts/loop_live.py` P, `live-140`: `python
  make_pdf.py` returned `exit code: 0`, `(no output)`, the PDF was on disk,
  and the model ran it again, then `python3`, then again, until the repeat
  guard refused; no `read_document` or `send_file`. The same request passed
  twice earlier that day.
- **Costs:** a turn's budget spent on nothing, the delivery not made.
- **Cause:** the model's; one observation. Whether "(no output)" reads as
  "nothing happened" is a question for the scenario runner, not a line to
  add. (The mojibake the `python3` attempt returned was fixed the same day:
  console output decoded by its code page.)
- **Evidence:** `reports/2026-09-04_v2_isolated_execution_review.md` §10.
- **Related:** ISS-0004.

### ISS-0037 — the failure message sent to the person carries the endpoint URL and the server's body

- **Status:** open
- **Seen:** 2026-09-03, code review; the wording seen live the same day.
- **Costs:** "That request failed: BackendError: the endpoint https://…
  could not be reached (…)" and the server's response text, in the chat:
  infrastructure detail handed to strangers under open access, the one
  place the product speaks in tracebacks.
- **Reproduce:** point `MODEL_ENDPOINT` at an unreachable address, send a
  message.
- **Cause:** `TelegramAdapter.handle_update` sends `f"{type}: {error}"` of
  any exception; `BackendError` texts include the endpoint and the body.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md` §2.10.

### ISS-0036 — a transient failure before the first streamed token fails the turn

- **Status:** open
- **Seen:** 2026-09-03, code review; not seen live. Since ISS-0044 a
  refused connection or a "later" status before the first chunk is retried;
  a 5xx body is not.
- **Costs:** a 503 or 429 from the provider on the path every call takes is
  a failed turn with an error in the chat, where `invoke` retries with
  backoff.
- **Reproduce:** a scripted transport answering 503 once, then 200, against
  `OpenAICompatibleBackend.stream`.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md` §2.9.
- **Related:** ISS-0044.

### ISS-0035 — the approval-resume path in Telegram lost the answer path's fixes

- **Status:** open
- **Seen:** 2026-09-03, code review; not seen live.
- **Costs:** after the person presses approve, a verbatim repeat of the
  text beside the call is sent again, and a withdrawn draft is deleted
  instead of held.
- **Cause:** `TelegramAdapter._on_callback` re-implements the event loop of
  `_answer` and was not updated with it: no `delivered` set passed to
  `_deliver`, `preview.discard()` where `_answer` calls `preview.hold()`.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md` §2.8.
- **Related:** ISS-0009.

### ISS-0033 — no tool has a deadline; a hung tool holds the worker until the platform kills it

- **Status:** open
- **Seen:** 2026-09-03, code review; not seen live.
- **Costs:** `read_document` on a large PDF, `view_pages`, a slow database
  or a stuck volume run on the event loop with no bound; the turn's budget
  is read only between steps, so a hung call holds the worker to Modal's
  600 s kill.
- **Cause:** `Tool.timeout_seconds` exists, the executor honours it, and no
  tool sets it; only the web tools carry httpx deadlines.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md` §2.5.
- **Related:** ISS-0034.

### ISS-0028 — the model says it checked its memory without calling anything

- **Status:** open; model behaviour, and partly retrieval
- **Seen:** 2026-09-03, deployed, run `80a5e47e`: asked "Что ты помнишь"
  and then "да", the model answered "Я проверил свою память … нет
  сохраненных фактов" with no tool call; `search_memory` was in the
  toolbox.
- **Costs:** a claim of an action that did not happen, and a wrong answer
  about what is saved.
- **Cause:** the model's choice, the class of ISS-0004; and partly the
  harness's: facts reach the model only through a keyword match of the
  latest user text, and when nothing matches the layer is absent without a
  word, so the model saw no facts and no sign a search had run. The
  retrieval design is where it belongs
  (`reports/2026-09-04_v2_observed_claims_review.md` §9).
- **Related:** ISS-0004.

### ISS-0018 — an existing folder is replaced without a word

- **Status:** open
- **Seen:** 2026-09-03, deployed, three turns asking for an app in a folder
  that held the previous attempt (runs `f41278c9`, `af276ed7`, `752486c1`).
- **Costs:** all three files written over, `overwrote …` read four times
  per turn in the tool results, and "Приложение готово" as if the place
  had been empty; the person's own work in a folder of the same name would
  go the same way.
- **Reproduce:** ask for an app in a folder that already has one.
- **Cause:** unknown. `list_files` was never called; the brief says nothing
  about what to do when the place is taken.
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`.
- **Related:** ISS-0004.

### ISS-0016 — the plan is a list of phases, ticked in bulk

- **Status:** open (2026-09-07: the brief's planning line and the tool's
  description state the condition — three or more parts, or more than five
  tool calls — instead of "when you can hold it in your head"; roadmap 16;
  measured by item 8)
- **Seen:** 2026-09-03, live, three turns (Task Board tests 4 and 5,
  `live-70`).
- **Costs:** the list is generic phases ("create structure", "implement
  CSS", "verify") rather than the request's own requirements, updated in
  bulk after the files are written, "verify" ticked after one look that
  exercised nothing, or never updated.
- **Cause:** unknown. The brief says what a list costs and when to use one,
  nothing about what an item is or what marks one done. 2026-09-06: with
  the plan on, GLM never opened a list because the brief phrases the
  choice as a figure of speech (roadmap items 8 and 11).
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`.
- **Related:** ISS-0008, roadmap 8.

### ISS-0015 — a written page ends with a markdown fence

- **Status:** open
- **Seen:** 2026-09-03, live, `Task Board test 4/index.html` on Gemma.
- **Costs:** a literal fence after `</html>`, shown as text at the bottom
  of the page; the model read it in its own snapshot and said nothing.
- **Cause:** unknown; may be the model closing a fence it never opened in
  the served format (ISS-0001). Not seen on the hosted model.
- **Related:** ISS-0001, ISS-0008.

### ISS-0010 — "here is the screenshot", and nothing was sent

- **Status:** open (2026-09-07: every tool's description now states what it
  leaves and where, and the page tool's screenshot result says "the person
  has not" seen it; roadmap 16; effect unmeasured until the set runs)
- **Seen:** 2026-09-03, deployed, thread `afb9d76a`, twice: asked "пришли
  скрин", the assistant calls `inspect_page`, sees the screenshot and
  answers "Вот скриншот вашего приложения"; told nothing arrived, it says
  it cannot attach an image; the third request produced `send_file`.
  **Seen again** 2026-09-06, GLM at Novita, run
  `42cebe2d531c4a1199b8b663c6f8832c`: `write_file`, `inspect_page`,
  `send_file` of `calculator.html`, and "Калькулятор создан — (в чате выше
  скриншот)"; only the HTML left.
- **Costs:** the person receives text and is told a picture was sent.
- **Reproduce:** make a page, ask for a screenshot in one word.
- **Cause:** the tools do not say plainly what leaves the workspace and
  what stays, so a model that has looked at a page takes the look for a
  delivery (the human's reading, 2026-09-06): the contract of roadmap
  item 11, not a case to patch.
- **Evidence:** runs `8ffab1aa`, `240f09ea`, `eda12665`, `29c2bd17`;
  `reports/2026-09-03_v2_first_session_on_the_tool_system.md`.
- **Related:** ISS-0003, roadmap 11.

### ISS-0008 — a generated app is delivered as working without ever being used

- **Status:** open (2026-09-07: `use_page` built, roadmap 16 — the model can
  click, type, press, evaluate and read the console on the page it made;
  whether it does is the mini set's F and the wider G, not yet run on it)
- **Seen:** 2026-08-31, Telegram, "Personal Task Board 2": the board saved
  tasks and never drew them (a wrong selector); described as ready. Again
  2026-08-31 (run `cc98b3e0`, "проверь что всё работает", nothing opened)
  and 2026-09-03 (test 3, described as done without a look; then the
  person's file rewritten to seed sample tasks for the screenshot).
  **Seen again** 2026-09-06, GLM at Novita, run
  `601f1fc81c7849c8b02e76f5a7ffe768`: told "В гонках нет препятствий",
  the model simulated its spawn loop in node, blamed `ctx.roundRect`,
  looked at a static screenshot and said "Исправлено"; the bug
  (`frame % (60 - speed*3)`, a fractional divisor, never zero) was found
  two turns later, after "поиграй немного в игру", with a headless
  browser built by hand.
- **Costs:** the person receives an application described as ready, and
  the first thing they try does nothing.
- **Cause:** nothing in the loop exercises the artifact: `inspect_page`
  renders and looks, it does not click, type, press or read the console,
  and it does not say a screenshot is a look, not a run. `BrowserSession`
  can click, type, press and select on a ref since 2026-09-03; no action is
  exposed to the model (roadmap 12).
- **Evidence:** `reports/2026-08-31_v2_todo_live_failure.md`;
  `reports/2026-09-06_hosted_model_cometapi.md` §12.
- **Related:** ISS-0004, ISS-0016; roadmap 11–12.

### ISS-0004 — the assistant describes what it did not observe

- **Status:** open; model behaviour
- **Seen:** 2026-08-30, live: a page's contents told without a look; a file
  reported that was never created. 2026-09-03, run `45f78d7e`: a Hugging
  Face model page pasted as a URL described in detail with no `fetch_page`
  call, everything read off the address.
- **Costs:** the person is told about what nobody looked at.
- **Cause:** unknown. Three rounds of prompt wording made it better and
  worse in turn. One sub-case is a fact about the turn: a message carries a
  URL and the turn made no tool call at all
  (`reports/2026-09-03_v2_whole_code_review.md` §2.11).
- **Evidence:** `reports/2026-08-30_v2_prompt_assembly.md`,
  `reports/2026-08-31_v2_todo_live_failure.md`.
- **Related:** ISS-0008, ISS-0028, ISS-0046.

### ISS-0002 — a picture someone sends is never kept

- **Status:** open
- **Seen:** 2026-08-30, verified against the deployed volume: 22 entries,
  not one an image.
- **Costs:** a document survives in the workspace; a photo or voice message
  is used inside one turn and written nowhere, so `/new` loses it.
- **Cause:** the split lives in `admit_uploads` and is invisible to the
  person. When the model is shown the image and when a filename is the
  design (roadmap, Not started).

---

## Closed

Shortened to what a later reader needs; the linked report has the rest.

### ISS-0068 — the local Windows `run_command` kills every Cygwin tool

- **What it was:** under the write-restricted token every Cygwin/MSYS
  process Git Bash puts on `PATH` (`sh`, `ls`, `find`, `cat`, `awk`) died
  at start with `CreateFileMapping … Win32 error 5`; the model improvised
  `cmd` idioms and five of six local scenarios began there (2026-09-12).
- **Fixed** 2026-09-14 (roadmap 27 step 3): `run_command` on Windows hands
  the line to PowerShell under the same token, the brief says so, and the
  tree search, file finder and line reads are tools of their own, so the
  Cygwin route is not needed. Making Cygwin itself run under the token is
  recorded as not started. `reports/2026-09-14_item27_step3_references.md` §3.7.

### ISS-0076 — after a reload a turn's tool calls are separate rows again

- **What it was:** a thread reopened after a page reload showed every tool
  call of a turn as its own row, where the live turn had one collapsed step.
- **Fixed** 2026-09-14: the history layer (`ui/chainlit_history.py`) builds
  one collapsed step per turn with the calls as tool steps under it.

### ISS-0075 — a server the model starts opens a console window and outlives the conversation

- **What it was:** `run_command` had no background mode, so a long-lived
  server was started with `start`, which opened a console window on the
  person's desktop and kept running with nothing in the harness knowing of it.
- **Fixed** 2026-09-14: `run_command` has `background=true` (hidden, an id at
  once, `command_output` and `stop_command`, ended with the app) and the brief
  says never to use `start`; offline tests, not yet seen in a live turn.

### ISS-0074 — locally the browser refuses localhost

- **What it was:** `use_page open http://localhost:…` was refused with
  `net::ERR_ACCESS_DENIED`, so the model could not look at a site it had just
  started on the person's own machine.
- **Fixed** 2026-09-14: the local profile's `open` flag admits localhost and
  private addresses (`public_request_policy(open=True)`); offline test, not
  yet seen in a live turn. The same day `fetch_page` got the flag too
  (roadmap 27 step 3): until then the browser could open a dev server and
  the fetch could not.

### ISS-0073 — a GitHub MCP file's content is dropped as a non-text part

- **What it was:** `github_get_file_contents` returned the file in an
  `EmbeddedResource`, which the renderer dropped as "1 non-text part(s) not
  shown", and the model fetched every file again from the web.
- **Fixed** 2026-09-14: `render_result` in `app/tools/mcp.py` reads an
  `EmbeddedResource`'s text; offline test.

### ISS-0072 — after a reconnect a message runs on a closed agent

- **What it was:** after a websocket disconnect the session's next message ran
  on a closed agent and the turn died with `Cannot send a request, as the
  client has been closed`.
- **Fixed** 2026-09-14: the agent knows it is closed and the session makes a
  new one before a message runs.

### ISS-0071 — the status route keeps the agent of a closed session

- **What it was:** `/status` polled a process-wide agent a closed session had
  left behind, raising `Cannot operate on a closed database` every three
  seconds.
- **Fixed** 2026-09-14: the route answers 404 for a closed agent.

### ISS-0065 — a `send_file` result carries the file's bytes into the thread's history

- **What it was:** the `send_file` tool result kept the media part after the
  adapter had taken it, writing up to a megabyte per sent file into the
  thread's history and reading it back on every later turn.
- **Fixed** 2026-09-08 (roadmap 23): the store writes an outbound part as
  "Sent <name> (<type>, <size> bytes)."; seen live the same day, the row 313
  characters. Evidence: `reports/2026-09-08_persist_hang_logs.txt`.

### ISS-0063 — a dead worker's lease holds the conversation; nothing wakes the queue when it expires

- **What it was:** a killed worker's fixed 590 s lease kept the conversation
  busy, and when it expired nothing spawned a worker for the queued messages,
  so the bot stayed silent.
- **Fixed** 2026-09-07 (roadmap 22): a 60 s lease the worker extends every
  20 s, every queued update starts a worker, one that finds the conversation
  held waits out a lease; seen live 2026-09-07 15:51. Evidence:
  `reports/2026-09-07_worker_timeout_logs.txt`.

### ISS-0062 — the retry of a killed turn sends the final answer a second time

- **What it was:** a turn killed after its answer had gone out resumed from a
  checkpoint that did not carry the delivery, and sent the same long message
  again.
- **Fixed** 2026-09-07 (roadmap 22): `Agent.delivered_before` reads what the
  checkpoint holds for the same update id and the adapter does not send it
  again; seen live 2026-09-07. Evidence:
  `reports/2026-09-07_worker_timeout_logs.txt`.

### ISS-0061 — a turn runs up to the worker's own timeout and is killed while persisting

- **What it was:** a long Blender turn reached `persist_started` at 598 s and
  the platform cancelled it at the worker's 600 s timeout, losing the turn's
  history.
- **Fixed** 2026-09-07 (roadmap 22): the worker's timeout is four hours as a
  guard and the turn stays bounded by its health check; what was killed at
  600 s here was most likely the hung `persist` of ISS-0064. Evidence:
  `reports/2026-09-07_worker_timeout_logs.txt`.

### ISS-0058 — a command's temporary files and caches live on the Volume path, which is too long for a Unix socket and owned by the wrong user

- **What it was:** `HOME` and `TMPDIR` pointed at the workspace on the Volume,
  so npm's cache carried the wrong uid and Chrome could not bind a socket
  ("Socket path too long").
- **Fixed** 2026-09-08 (roadmap 17): home and temp are the container's; seen
  deployed the same day. Evidence:
  `reports/2026-09-08_item17_research.md` §9.

### ISS-0057 — a turn that is working is ended by a clock that counts its tools' run time

- **What it was:** a Blender turn doing real work was cut at 349 s because
  `turn_max_seconds` counted tool run time and provider queue.
- **Fixed** 2026-09-07: the step, tool-call and seconds ceilings are gone;
  after `turn_check_seconds` the harness asks the model between steps whether
  it is on track (`TurnWatch`, `turn_health_check`; DECISIONS 2026-09-07).

### ISS-0056 — a turn spends seconds between its own steps that no model, tool or store accounts for

- **What it was:** 18.3 s of a 71.8 s turn belonged to no model call, tool or
  store round trip.
- **Fixed** 2026-09-10 as named (roadmap 18): everything the harness spends
  time on names itself on the turn's trace with a duration; the harness's own
  cost is 2–4 s and the block a person feels is the cold worker per message.
  Evidence: `reports/2026-09-08_item18_harness_seconds.md`.

### ISS-0055 — a call that spends its whole output cap on reasoning is delivered as an answer with nothing said

- **What it was:** a completion that spent 8,192 tokens on reasoning and
  emitted no visible text was delivered as the turn's answer.
- **Fixed** 2026-09-06, deployed: an empty completion at
  `finish_reason=length` ends the turn with a message saying the cap was spent
  before a visible word (`silent_cut`). Evidence:
  `reports/2026-09-06_hosted_model_cometapi.md` §4.

### ISS-0053 — what a command installs outside the workspace is gone by the next command

- **What it was:** packages installed into `/tmp` or the container were gone by
  the next `run_command`, and the "new environment" line read to the model as
  its own work being lost.
- **Fixed** 2026-09-08 (roadmap 17): the "new environment" line is gone, the
  brief says once what the container keeps, and the folder-per-task rule says
  where a venv and packages live; scenario C 7/7 locally and deployed.

### ISS-0052 — a system message that is not first is refused by Qwen3.8's chat template

- **What it was:** every turn with facts or a summary failed on the INT4 App
  with `HTTP 400: System message must be at the beginning`.
- **Fixed** 2026-09-05: `build_messages` joins the leading system messages into
  one and delivers a later system layer as the first text of the next user
  message, so every template accepts the shape.

### ISS-0051 — the renderer's first `inspect_page` in a cold container fails before the browser is up

- **What it was:** the first look in a fresh renderer failed with
  `browser.load_failed` after 3.44 s; the retry succeeded.
- **Fixed** 2026-09-05: `_wait_for_debugger` waits a stated fifteen seconds
  (`DEVTOOLS_READY_SECONDS`); a browser that exits is still reported at once.

### ISS-0050 — vLLM's ahead-of-time compile of the INT4 checkpoint dies tracing a renamed weight

- **What it was:** the INT4 App failed to boot with
  `'MergedColumnParallelLinear' object has no attribute 'weight_packed'`;
  vLLM 0.26.0's defect, not the harness's.
- **Worked around** 2026-09-05: `VLLM_USE_AOT_COMPILE=0` on the Qwen Apps, the
  snapshot holding the compiled engine. GPU Apps only.

### ISS-0049 — a failed server start is retried by the platform for as long as a request waits

- **What it was:** one refused ceiling started four containers in 13 minutes,
  because a client that has given up still drives restarts from its request
  queued at the edge.
- **Won't fix** — a property of Modal; worked around 2026-09-05: a
  configuration boots first in `dry_run`, a Function with `retries=0` and no
  request behind it. GPU Apps only.

### ISS-0048 — the store's connection, hung up on during a long model call, fails the next turn

- **What it was:** after a 457 s model call `persist` died with `SSL connection
  has been closed unexpectedly`, losing the turn and the scenario run.
- **Fixed** 2026-09-05: the first statement after a pause is the store's own,
  so a hang-up surfacing there is resent once on a fresh connection
  (`PostgresStore._opened`). Evidence:
  `reports/2026-09-05_qwen38_second_model.md` §7.

### ISS-0047 — a GPU snapshot taken with an uncommitted Volume path open cannot be restored

- **What it was:** restore failed with `failed to walk
  "…/torch_aot_compile/…": no such file or directory`, exit 128.
- **Fixed** 2026-09-05, twice: the Qwen Apps no longer mount the compile-cache
  Volume on their `Server`, so a version's first boot compiles from nothing
  (~190 s, once) and every restore skips compilation. Evidence:
  `reports/2026-09-05_qwen38_second_model.md` §5.

### ISS-0045 — deployed, history search cannot find a file name by its parts

- **What it was:** `search_history "config.ini"` found nothing deployed, where
  Postgres keeps the name as one token and SQLite's FTS5 splits on the dot.
- **Fixed** 2026-09-05: every Postgres search matches on `plainto_tsquery` as
  well as the split query; scenario I passes deployed.

### ISS-0044 — the first streamed request to a sleeping model endpoint dies at the read timeout

- **What it was:** the first call to a cold GPU endpoint died at the read
  timeout, because Modal's edge answers a request older than 150 s with a `303`
  the client did not follow, and each retry stacked another queued copy.
- **Fixed** 2026-09-05, three parts: a timeout is never retried, a refused
  connection or a "later" status before the first chunk is, the client follows
  up to eight hops, and `MODEL_TIMEOUT` is 600 s.

### ISS-0043 — in the deployed container, `pip` is not `python3`'s pip

- **What it was:** `pip show fpdf2` and `python3 -c "import reportlab"`
  disagreed, and the model concluded it "cannot install libraries".
- **Fixed** 2026-09-04: `pip` installed into the image's uv venv, so `pip`,
  `python3 -m pip` and `python3` are one interpreter; the cold-start probe
  checks it. Evidence:
  `reports/2026-09-04_v2_isolated_execution_review.md` §12.

### ISS-0042 — a command is refused as "already done" after the file it runs was rewritten

- **What it was:** the fourth, working version of `make_pdf.py` was refused as
  "already succeeded twice with these same arguments".
- **Fixed** 2026-09-04: `succeeded_before` starts over when a different call of
  a tool that changes the workspace (`mutates`) succeeded in between; an
  identical call still counts against itself.

### ISS-0041 — within one turn, the errors a command met are shortened away while the model is still fixing them

- **What it was:** tracebacks the model was working through were stubbed to
  "900 characters; shortened, call the tool again", and attempt 4 repeated
  attempt 1's error exactly.
- **Fixed** 2026-09-04: the turn in progress is never shortened; stubs are for
  stored history only (`app/context/window.py`; DECISIONS 2026-09-03 amended).

### ISS-0038 — a package is installed into the machine's own Python rather than the workspace

- **What it was:** `pip install reportlab` locally landed in
  `c:\python314\lib\site-packages`.
- **Fixed** 2026-09-04 by the boundary: on Windows a command runs under a
  write-restricted token (`app/tools/shell_windows.py`) and the workspace venv
  is the `python` it sees; deployed, the container is the boundary. Evidence:
  `reports/2026-09-04_v2_isolated_execution_review.md` §9.

### ISS-0034 — a worker's lease outlives the container's own kill by five minutes

- **What it was:** a 900 s claim against a 600 s container let a killed worker
  hold the conversation for up to fifteen minutes.
- **Fixed** 2026-09-04: `LEASE_SECONDS` 590 against the 600 s timeout; the turn
  is taken up from its checkpoint by the next claim.

### ISS-0032 — the conversation folds every twelve messages whatever their size

- **What it was:** four folds in sixteen turns at 4–10k tokens against a 52k
  window, because the trigger was a message count.
- **Fixed** 2026-09-04 (`summarize_after` 60 as a fallback, size as the rule)
  and completed 2026-09-07, when the count rule was removed: a fold happens
  only when the request would not fit the set's budget, or on `/compact`.

### ISS-0031 — a tool call cut at the output limit is reported to the model as bad JSON

- **What it was:** a `write_file` past the output cap arrived with unterminated
  arguments and was reported as unreadable JSON.
- **Fixed** 2026-09-04: `finish_reason == "length"` marks the call `cut`,
  refused as `output_cut` naming the limit; `MODEL_MAX_TOKENS` 8192.

### ISS-0030 — the summarizer is handed every tool result in full

- **What it was:** folds carried whole page fetches, ~100k characters, for a
  sentence of summary.
- **Fixed** 2026-09-04: the summarizer reads the same stubs the model reads
  (`shortened(keep=0)`).

### ISS-0029 — a summarizer request that does not fit fails a turn whose answer was already delivered

- **What it was:** a fold whose request exceeded the window failed the turn
  after its answer had gone out.
- **Fixed** 2026-09-04: both folds catch `BackendError` and record
  `context_fold_failed`; the turn keeps its answer.

### ISS-0027 — a shortened result's stub invited the model to run the tool again

- **What it was:** the stub said "call the tool again for a fresh one", the
  model did, and the file was gone.
- **Fixed** 2026-09-03: the stub says only where the whole result is stored
  (`read_history N`).

### ISS-0026 — reading a call back did not show what the call returned

- **What it was:** a failed write was found in history without its error, and
  the model concluded "no error was recorded".
- **Fixed** 2026-09-03: `read_history` of a message that made calls appends
  their results; a `search_history` hit on a call shows its result.

### ISS-0025 — `/compact` is recorded as a failed turn

- **What it was:** the command's trace ended unanswered, so the turn counted as
  a failure.
- **Fixed** 2026-09-03: the command finishes its trace as answered.

### ISS-0024 — the server does not say what it served from its cache

- **What it was:** the Gemma App's completions carried no `cached_tokens`, so a
  prompt-cache hit could not be seen.
- **Fixed** 2026-09-03 in the Gemma App's serve command
  (`--enable-prompt-tokens-details`); GPU Apps only, the hosted providers pass
  `cached_tokens` through.

### ISS-0023 — a forced fold finds nothing to cut in a tool-heavy tail

- **What it was:** `/compact` on a 32-message thread whose newest 26 were one
  turn's calls answered "nothing to fold".
- **Fixed** 2026-09-03: a fold may cut before any user or assistant message,
  never before a tool result.

### ISS-0022 — shortening the model's own file arguments made it write every file again

- **What it was:** a `write_file`'s own content came back as
  `<1104 characters, shortened>` and the model wrote eleven files in a cycle.
- **Fixed** 2026-09-03: only tool results are stubbed; the model's text and
  call arguments are never shortened.

### ISS-0021 — the end-of-turn token reaches the chat as a message

- **What it was:** a message saying `<eos>` was sent to the person.
- **Fixed** 2026-09-03: the streamed reader drops `<eos>`, `<end_of_turn>`,
  `<|im_end|>`, `<|eot_id|>`.

### ISS-0020 — a delivery is refused when the turn's budget is spent

- **What it was:** the `send_file` that would have handed over the work was
  refused at the step ceiling with "answer now with what you already have".
- **Fixed** 2026-09-03: a tool marked `delivers` (`send_file`) still runs at
  the step, call or time ceiling (`tests/test_turn_bounds.py`).

### ISS-0019 — the page is written twice, identically, after the plan is updated

- **What it was:** `index.html` written up to seven times byte-identically in
  one turn.
- **Mitigated** 2026-09-04: `write_file` with content the file already has
  answers `unchanged:`, and the third byte-identical successful call in a turn
  is answered "already done" without running (`MAX_IDENTICAL_SUCCESSES`); the
  model's habit is not fixed, and it is Gemma-era.

### ISS-0017 — the screenshot the person receives is of the page without its CDN styles

- **What it was:** Tailwind from a CDN was refused by the offline session and
  the unstyled page was sent as the screenshot.
- **Fixed** 2026-09-03, deployed: the local artifact may reach the public
  internet under the renderer's own policy (the human's decision).

### ISS-0014 — the page was looked at without storage or its own files

- **What it was:** the page was opened as a `data:` URL, so `localStorage`
  threw and `styles.css` resolved to nothing.
- **Fixed** 2026-09-03: the offline session serves the workspace at
  `http://artifact.local/` through request interception (`serve_directory`), so
  the page has an origin, storage and its siblings.

### ISS-0013 — the repeat guard refused the call that would have worked

- **What it was:** two looks failed on a missing file, the file was written,
  and the third look was counted as the third failure, halting every tool.
- **Fixed** 2026-09-03: the identical-failure count starts over when any tool
  has succeeded since the last identical failure
  (`tests/test_repeated_failure.py`).

### ISS-0012 — a corrupted path was obeyed, and a file nobody named was made

- **What it was:** `"Task Board test 4/index.html"<|"|>` was created as a file
  on the person's volume.
- **Fixed** 2026-09-03: `resolve_in_root` refuses a path carrying quotes or
  `<|`/`|>` as `bad_arguments`.

### ISS-0011 — every look at a page carried the whole page back as its address

- **What it was:** the `data:` URL came back on every look, ~9 KB of base64,
  17,764 input tokens by the third call.
- **Fixed** 2026-09-03 (4.5.5, `page_report`): no address is reported for a
  local document.

### ISS-0009 — the person reads an answer for a minute and then it is deleted

- **What it was:** a narration previewed for up to 58 s was withdrawn when the
  completion ended in a tool call, and the same answer was generated twice
  around a `send_file`.
- **Fixed** 2026-09-03, deployed: text that comes with a tool call is delivered
  and kept, a draft a steering refuses is held on the screen, the plan seam no
  longer objects, and the adapter drops a byte-identical closing repeat.
  Evidence: `reports/2026-09-03_v2_first_session_on_the_tool_system.md`.

### ISS-0007 — `tool_failed` carries no reason

- **What it was:** a failed tool was recorded with no code and no message.
- **Fixed** 2026-09-03 with the typed outcome of 4.5: `code` and `message` on
  every `tool_failed`, printed by `tools/show_run.py`.

### ISS-0006 — a path meant as a directory becomes a file, and poisons the folder

- **What it was:** `write_file "Board 3/"` made a file, after which every write
  into that folder failed and files scattered into the workspace root.
- **Fixed** 2026-08-31: `write_file` refuses a path ending in a separator and
  says directories are made for you; an ancestor in the way is named.

### ISS-0005 — an OS error escapes the filesystem tools unwrapped

- **What it was:** an OS error from a filesystem tool reached the turn
  unwrapped.
- **Fixed** 2026-08-31, and since 2026-09-03 every filesystem failure is an
  `fs.*` code with the `strerror` as detail; an exception that still escapes
  any tool becomes an `internal` result, not a failed turn.

### ISS-0003 — a made file is handed over as prose instead of sent

- **What it was:** files were named as paths or markdown links in the answer
  and never sent, so nothing reached the person.
- **Mitigated** 2026-09-03: the brief says where the person is
  (`Delivery.place`) and every tool that leaves a workspace item says `to hand
  it to the person: send_file(path="…")`; the answer still carries a markdown
  path beside the send, and a mechanical backstop in the adapter was rejected
  as a crutch. Evidence:
  `reports/2026-09-03_v2_first_session_on_the_tool_system.md`.

### ISS-0001 — the served tool parser loses what follows a long string argument

- **What it was:** vLLM's Gemma 4 tool parser dropped what followed a long
  string, so `write_file` arrived with `content` and no `path`, the identical
  call up to eight times.
- **Mitigated** 2026-08-31, upstream in vLLM (51284, 53431), GPU Apps only: a
  call whose arguments are not a JSON object is refused once as `bad_arguments`
  naming the fence, and a call that failed twice identically is refused a third
  time; a corrected parser is in `tools/gemma4_parser.py`, tested offline, not
  deployed. Evidence: `reports/2026-08-31_v2_todo_live_failure.md`.
