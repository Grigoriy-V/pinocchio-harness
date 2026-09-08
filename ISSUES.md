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
  up to **Open** with a "seen again" line.
- `Cause` stays "unknown" until proven. One defect per entry. `Costs` says
  what it does to the person; there is no severity word.
- Fields: Status, Seen, Costs, Reproduce, Cause, Evidence, Related.

## Catalog

| Id | Status | Defect | Related |
|---|---|---|---|
| ISS-0065 | fixed 2026-09-08 | a `send_file` result carries the file's bytes into the thread's history | 0064, roadmap 23 |
| ISS-0064 | open, fix built | `persist` hangs on the store's write and the worker sits in it until the platform kills it | 0061, 0048, 0065, roadmap 23 |
| ISS-0063 | fixed 2026-09-07 | a dead worker's lease holds the conversation; nothing wakes the queue when it expires | 0061, 0062, roadmap 22 |
| ISS-0062 | fixed 2026-09-07 | the retry of a killed turn sends the final answer a second time | 0061, roadmap 22 |
| ISS-0061 | fixed 2026-09-07 | a turn runs up to the worker's own timeout and is killed while persisting | 0057, 0056, 0064, roadmap 14, 22 |
| ISS-0060 | open | deployed `use_page open url` renders a public page in the worker, beside the secrets | 0051, roadmap 21 |
| ISS-0059 | open | a question sent mid-turn is answered and the task it interrupted stops to ask "continue?" | roadmap 15, 20 |
| ISS-0058 | open, fix built | command temp files and caches on the Volume path: too long for a socket, wrong uid | 0053, 0057, roadmap 17 |
| ISS-0057 | fixed 2026-09-07 | the turn's seconds budget counts tool run time and provider queue | 0056, 0054 |
| ISS-0056 | open | seconds between a turn's steps that no model, tool or store accounts for | roadmap 10 |
| ISS-0055 | fixed 2026-09-06 | an output cap spent on reasoning delivered as an empty answer | 0031 |
| ISS-0054 | open, GPU Apps only | the model endpoint sleeps mid-turn when a tool outlives the idle window | 0044 |
| ISS-0053 | open, fix built | what a command installs outside the workspace is gone by the next command | 0058, 0043, roadmap 17 |
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

### ISS-0065 — a `send_file` result carries the file's bytes into the thread's history

- **Status:** open; fix built 2026-09-08 (roadmap 23: the store writes an
  outbound part as "Sent <name> (<type>, <size> bytes)."). Seen live
  2026-09-08 06:08 UTC: the `send_file` result row of update 814913263 is
  313 characters; `persist` took 3.5 s in one attempt.
- **Seen:** 2026-09-07, deployed. The thread's `messages` row for the
  `send_file` result of `blender/street_video0000-0240.mp4` is 1,019,473
  characters (position 92); the earlier send of a shorter cut is 290,929
  (position 69). The text of the result is one line ("Selected … for
  delivery to the person."); the rest is the media part. A read of a PNG
  (`read_file`, position 90) is 242,931, which is the image the model is
  shown and is by design.
- **Costs:** a megabyte written to the store per sent video, read back by
  every later turn's history load, for a part no model is ever shown.
- **Reproduce:** deployed, ask for a rendered video; read the row.
- **Cause:** unknown; the tool result keeps the media part it built for the
  adapter after the adapter has taken it.
- **Evidence:** `reports/2026-09-08_persist_hang_logs.txt` (the row sizes at
  the end).
- **Related:** ISS-0064 (the write that hung was this row's).

### ISS-0064 — `persist` hangs on the store's write and the worker sits in it until the platform kills it

- **Status:** open; fix built 2026-09-08 (roadmap 23: `CONNECTION_GUARDS`
  on every store and inbox connection, so the wait ends in about a minute
  as an `OperationalError` and the worker's retry resumes `persist` on a
  fresh connection), deployed 2026-09-08; a hang has not been seen since
  (one sent file, 06:08 UTC, persisted in 3.5 s). The cause stays unknown; what is
  bounded is the wait, and the row that waited is a hundred bytes now
  (ISS-0065).
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

### ISS-0063 — a dead worker's lease holds the conversation; nothing wakes the queue when it expires

- **Status:** fixed 2026-09-07 (roadmap 22: a 60 s lease the worker
  extends every 20 s, every queued update starts a worker, one that finds
  the conversation held waits out a lease). Seen live 2026-09-07 15:51: the
  holder of 814913253 hung (ISS-0064), its lease ran out, and the worker of
  the next message took the update up within a minute of arriving.
- **Seen:** 2026-09-07 14:43 UTC, deployed. After the worker of ISS-0061 was
  killed, its row stayed `running` with a lease to 14:53:02 (attempts 2).
  Two later messages (814913247, 814913248) were queued `pending` with
  `spawning: false`, because the webhook saw the conversation as busy. No
  worker existed to drain them, and when the lease expires nothing spawns
  one: only the next message would.
- **Costs:** the bot goes silent for ten minutes, then stays silent until
  the person writes again.
- **Reproduce:** deployed, a turn killed by the platform; send two messages
  within `LEASE_SECONDS`.
- **Cause:** the lease is a fixed 590 s set once at the claim, so a dead
  worker is indistinguishable from a working one for that long; and the
  webhook's "busy, do not spawn" leaves no one responsible for the queue
  after the lease ends.
- **Evidence:** `reports/2026-09-07_worker_timeout_logs.txt`; the inbox rows
  read at 14:45 UTC (244 running/held, 247 and 248 pending, attempts 0).
- **Related:** ISS-0061, ISS-0062.

### ISS-0062 — the retry of a killed turn sends the final answer a second time

- **Status:** fixed 2026-09-07 (roadmap 22: `Agent.delivered_before`
  reads what the checkpoint holds for the same update id and the adapter
  does not send it again). Seen live 2026-09-07 15:51 and 15:53: update
  814913253 was taken up twice after its answer had gone out, and neither
  attempt sent anything (no `telegram_final_sent` in either run).
- **Seen:** 2026-09-07 14:43 UTC, deployed. The turn of ISS-0061 sent its
  final at 14:42:58; the platform's retry (`retries=1`) reclaimed the row at
  14:43:12, resumed from the checkpoint and sent the same final again at
  14:43:15, then was killed in `persist` with the rest of the container.
- **Costs:** the person gets the same long message twice, and the turn is
  still not persisted.
- **Reproduce:** deployed, a turn killed after `final_sent` and before
  `persist` finished.
- **Cause:** that the answer was delivered is known only in the worker's
  memory (`delivered`); the checkpoint the retry resumes from does not
  carry it.
- **Evidence:** `reports/2026-09-07_worker_timeout_logs.txt`.
- **Related:** ISS-0061.

### ISS-0061 — a turn runs up to the worker's own timeout and is killed while persisting

- **Status:** fixed 2026-09-07 (roadmap 22: the worker's timeout is four
  hours, a guard, the turn stays bounded by its health check). Deployed and
  seen 2026-09-07 15:26: no turn was killed at 600 s again. What was killed
  at 600 s here was most likely a hung `persist`, which is ISS-0064.
- **Seen:** 2026-09-07 14:33–14:43 UTC, deployed. A Blender scene turn
  (23 steps, 28 tool calls) reached `persist_started` at 598 s of elapsed
  time; at 600 s the platform cancelled the input
  (`WORKER_TIMEOUT_SECONDS`), and 30 s later killed the container.
- **Costs:** the turn's history is not saved, the answer is duplicated
  (ISS-0062) and the conversation is blocked (ISS-0063).
- **Reproduce:** deployed, a request the model works on for ten minutes.
- **Cause:** item 14 bounded the turn by health, not by a ceiling, and the
  health check (`turn_check_seconds` 360) lets the turn continue; nothing
  in the turn knows the container it runs in dies at 600 s, so no hand-off
  to a fresh worker happens before that.
- **Evidence:** `reports/2026-09-07_worker_timeout_logs.txt`.
- **Related:** ISS-0057, ISS-0056; roadmap 14.

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

### ISS-0058 — a command's temporary files and caches live on the Volume path, which is too long for a Unix socket and owned by the wrong user

- **Status:** open; fix built 2026-09-08 (roadmap 17: home and temp are
  the container's, cwd is the workspace by its short mount path), offline
  tests, deploy and a Chrome probe pending.
- **Seen:** 2026-09-06, live, run `75c145f09f624ef5b311517c7e889058`
  (Telegram, GLM at Novita): `npm i puppeteer` failed twice on the
  workspace's `.npm` cache ("please run: sudo chown -R 0:0 …/.npm");
  Chrome refused to start three times with "Socket path too long" because
  `TMPDIR` is the workspace on the Volume (`/__modal/volumes/vo-…/<user>/…`,
  over the 108-byte limit). The model found `npm_config_cache=/tmp/.npm`
  and `TMPDIR=/tmp` itself, ten commands and ~4 minutes in, and the budget
  ended before its diagnosis (ISS-0057).
- **Costs:** minutes of a turn spent on the sandbox rather than the work
  whenever a tool needs a socket or a package cache; what is put in `/tmp`
  to escape it is gone with the container (ISS-0053).
- **Reproduce:** in the deployed command environment, launch Chrome from
  the workspace; run `npm i` with the default cache.
- **Cause:** the command environment sets `HOME` and the working directory
  to the workspace on the Volume, so every default cache and temp path
  lands there; the path is long and the Volume's files carry another uid.
- **Evidence:** thread `ba7fe8cd-7816-4467-a22c-0921ac319c32`,
  `tools/show_run.py 75c145f09f624ef5b311517c7e889058`.
- **Related:** ISS-0053, ISS-0057.

### ISS-0056 — a turn spends seconds between its own steps that no model, tool or store accounts for

- **Status:** open
- **Seen:** 2026-09-06, live, run `42cebe2d531c4a1199b8b663c6f8832c`
  (Telegram, GLM at Novita): 71.8 s in all, model 46.6 s, tools 1.9 s,
  persist 2.7 s, queue 4.9 s, **18.3 s unattributed**: 1.8 s between every
  `model_finished` and the next `tool_started`, 5.3 s from the last
  `persist_finished` to `turn_finished`. Same evening, run
  `489357808c644569b787da03ac500663` (12 calls, 10 tools): 0.7–1.2 s per
  step and **12.7 s** in the tail.
- **Costs:** on a model that answers in 2–6 s, a quarter of the turn is
  the harness's own gaps; the first visible word came at 51 s in a turn
  whose model produced it by 36 s.
- **Reproduce:** any deployed turn with tools; read the timeline's gaps.
- **Cause:** unknown. Candidates by the code, not findings: Telegram
  preview edits and status calls between steps, the checkpoint write after
  each node, the telemetry flush, and around every `run_command` the
  worker commits and reloads the workspaces Volume twice (`ModalRunner.run`,
  `run_command`), a cost that grows with the workspace (this one held
  Blender, 350 MB). The timeline does not name them, which is the first
  thing to fix.
- **Evidence:** `tools/show_run.py 42cebe2d531c4a1199b8b663c6f8832c`.
- **Related:** roadmap item 10; the direction of 2026-09-06.

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

### ISS-0053 — what a command installs outside the workspace is gone by the next command

- **Status:** open; fix built 2026-09-08 (roadmap 17: the "new environment"
  line is gone, the brief says once what the container keeps, and the
  folder-per-task rule tells the model where a venv and packages live),
  offline tests, deploy pending. Whether the model follows the rule is the
  suite's measurement (scenario C).
- **Seen:** 2026-09-05, scenario G on the INT4 App (`deployed-c0c0a622-70`):
  `npm install puppeteer` in `/tmp` and `apt-get install` of Chromium's
  libraries, then the script could not find what it had installed; each
  `run_command` may land in a fresh container and only the workspace
  persists. The first result in a fresh container says "new environment:
  nothing installed by earlier commands is present".
- **Costs:** minutes of a turn installing what the next command cannot see;
  a model that reads the "new environment" line as its own work being gone.
- **Cause:** the command environment. A possible general shape, not
  decided: everything a command installs lands in the workspace by default
  (`HOME`, npm's prefix and cache, pip's target), so persistence needs no
  knowledge from the model. pip already has a workspace venv; node does
  not. The human: "пока непонятно, просто запиши".
- **Related:** ISS-0058 (the same environment, the other way), ISS-0043.

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

### ISS-0057 — a turn that is working is ended by a clock that counts its tools' run time

- **Status:** fixed 2026-09-07: the step, tool-call and seconds ceilings are
  gone; after `turn_check_seconds` (600) of work the harness asks the model,
  between steps, whether it is on track, and the model decides
  (`TurnWatch`, `turn_health_check`; DECISIONS 2026-09-07). Deployed with
  the next control-plane deploy.
- **Seen:** 2026-09-06, run `489357808c644569b787da03ac500663`: a Blender
  turn cut at 349 s one step before the check that would have finished it;
  model time 86 s, tools 231 s against `turn_max_seconds` 300.
- **Related:** ISS-0056, ISS-0054.

### ISS-0055 — a call that spends its whole output cap on reasoning is delivered as an answer with nothing said

- **Status:** fixed 2026-09-06, deployed: an empty completion at
  `finish_reason=length` ends the turn with a message saying the cap was
  spent before a visible word (`silent_cut`, event `output_cut_silent`);
  `reasoning_tokens` read from usage into `model_finished`.
- **Seen:** 2026-09-06, G on GLM through CometAPI: 8,192 tokens out, no
  visible text, logged `nothing_to_add`, turn `answer_delivered`.
- **Evidence:** `reports/2026-09-06_hosted_model_cometapi.md` §4.

### ISS-0052 — a system message that is not first is refused by Qwen3.8's chat template

- **Status:** fixed 2026-09-05: `build_messages` joins the leading system
  messages into one and delivers a later system layer as the first text of
  the next user message, so every template accepts the shape.
- **Seen:** 2026-09-05, H on the INT4 App: `HTTP 400: System message must
  be at the beginning`; every turn with facts or a summary failed.

### ISS-0051 — the renderer's first `inspect_page` in a cold container fails before the browser is up

- **Status:** fixed 2026-09-05: `_wait_for_debugger` waits a stated
  fifteen seconds (`DEVTOOLS_READY_SECONDS`) instead of sixty polls of
  50 ms; a browser that exits is still reported at once.
- **Seen:** 2026-09-05, F on the INT4 App: `browser.load_failed` after
  3.44 s on a fresh renderer, the retry succeeded.

### ISS-0050 — vLLM's ahead-of-time compile of the INT4 checkpoint dies tracing a renamed weight

- **Status:** worked around 2026-09-05 (`VLLM_USE_AOT_COMPILE=0` on the
  Qwen Apps); vLLM 0.26.0's, not the harness's. AOT serves a later process
  loading the artifact, and the snapshot holds the compiled engine.
- **Seen:** 2026-09-05, the INT4 App's fourth boot: `AttributeError:
  'MergedColumnParallelLinear' object has no attribute 'weight_packed'`.

### ISS-0049 — a failed server start is retried by the platform for as long as a request waits

- **Status:** won't fix, a property of Modal; worked around 2026-09-05: a
  configuration boots first in `dry_run`, a Function with `retries=0` and
  no request behind it.
- **Seen:** 2026-09-05, four containers in 13 minutes on one refused
  ceiling; a client that gave up still drives restarts, its request stays
  queued at the edge.

### ISS-0048 — the store's connection, hung up on during a long model call, fails the next turn

- **Status:** fixed 2026-09-05: the first statement after a pause is the
  store's own (`SET LOCAL search_path`), so a hang-up surfacing there is
  resent once on a fresh connection (`PostgresStore._opened`).
- **Seen:** 2026-09-05, G's 457 s call on Qwen3.8, then `psycopg.
  OperationalError: SSL connection has been closed unexpectedly` in
  `persist`; the turn and the scenario run lost.
- **Evidence:** `reports/2026-09-05_qwen38_second_model.md` §7.

### ISS-0047 — a GPU snapshot taken with an uncommitted Volume path open cannot be restored

- **Status:** fixed 2026-09-05, twice: a commit before the sleep was not
  enough (compilers write through temporary names they rename); the Qwen
  Apps no longer mount the compile-cache Volume on their `Server`, a
  version's first boot compiles from nothing (~190 s, once) and every
  restore skips compilation. The Gemma App keeps its Volume only because
  its cache predates its snapshot.
- **Seen:** 2026-09-05, restore failed with `failed to walk
  "…/torch_aot_compile/…": no such file or directory`, exit 128.
- **Evidence:** `reports/2026-09-05_qwen38_second_model.md` §5.

### ISS-0045 — deployed, history search cannot find a file name by its parts

- **Status:** fixed 2026-09-05: every Postgres search matches on
  `plainto_tsquery` as well as the split query; scenario I passes deployed.
- **Seen:** 2026-09-05, `search_history "config.ini"` found nothing: the
  `simple` parser keeps `config.ini` as one token, `match_query` asked
  `config | ini`; SQLite's FTS5 splits on the dot, so it passed locally.

### ISS-0044 — the first streamed request to a sleeping model endpoint dies at the read timeout

- **Status:** fixed 2026-09-05, three parts: a timeout is never retried (a
  timed-out request stays queued at Modal's edge, so each retry stacked a
  copy); a refused connection or a "later" status before the first chunk
  is; `MODEL_TIMEOUT` 600 s. The cause: Modal's edge answers a request
  older than 150 s with a `303` the client did not follow, so a wake over
  150 s never returned; the client follows up to eight hops, and
  `context_limit` treats anything but a 200 with JSON as unknown.
- **Seen:** 2026-09-05, `httpx.ReadTimeout` on the first call from a cold
  GPU, twice; `deployed-2f3a23eb-80` after exactly 150 s.
- **Related:** ISS-0054, ISS-0036.

### ISS-0043 — in the deployed container, `pip` is not `python3`'s pip

- **Status:** fixed 2026-09-04: `pip` installed into the image's uv venv,
  so `pip`, `python3 -m pip` and `python3` are one interpreter; the
  cold-start probe checks it.
- **Seen:** 2026-09-04, thread `e8c54e07`: `pip show fpdf2` → not found,
  `python3 -c "import reportlab"` → 5.0.1; the model concluded it "cannot
  install libraries".
- **Evidence:** `reports/2026-09-04_v2_isolated_execution_review.md` §12.

### ISS-0042 — a command is refused as "already done" after the file it runs was rewritten

- **Status:** fixed 2026-09-04: `succeeded_before` starts over when a
  different call of a tool that changes the workspace (`mutates`)
  succeeded in between; an identical call still counts against itself.
- **Seen:** 2026-09-04, run `f25fd7cd` (P): the fourth version of
  `make_pdf.py`, the one with a chance, refused as "already succeeded twice
  with these same arguments".
- **Related:** ISS-0019, ISS-0013.

### ISS-0041 — within one turn, the errors a command met are shortened away while the model is still fixing them

- **Status:** fixed 2026-09-04: the turn in progress is never shortened;
  stubs are for stored history only (`shortened` in
  `app/context/window.py`; DECISIONS 2026-09-03 amended). Seen live:
  twelve steps, `stubbed=0` on each.
- **Seen:** 2026-09-04, runs `510fe752`, `3b3c86d8` (P): six rewrites of
  one script, tracebacks stubbed to "900 characters; shortened, call the
  tool again", attempt 4 repeated attempt 1's error exactly.
- **Related:** ISS-0022, the mirror.

### ISS-0038 — a package is installed into the machine's own Python rather than the workspace

- **Status:** fixed 2026-09-04, by the boundary: on Windows a command runs
  under a write-restricted token and the OS refuses every write outside the
  workspace (`app/tools/shell_windows.py`); the workspace venv is the
  `python` a command sees. Deployed, the container is the boundary.
- **Seen:** 2026-09-04, P locally: `pip install reportlab` landed in
  `c:\python314\lib\site-packages`.
- **Evidence:** `reports/2026-09-04_v2_isolated_execution_review.md` §9.

### ISS-0034 — a worker's lease outlives the container's own kill by five minutes

- **Status:** fixed 2026-09-04: `LEASE_SECONDS` 590 against the 600 s
  timeout; the turn is taken up from its checkpoint by the next claim.
- **Seen:** 2026-09-03, code review: claim 900 s against a 600 s
  container, so a killed worker held the conversation up to fifteen
  minutes.

### ISS-0032 — the conversation folds every twelve messages whatever their size

- **Status:** fixed 2026-09-04: `summarize_after` 60 as a fallback for a
  server that reports no window; the size trigger is the rule. On
  OpenRouter, which reports no window, the fallback bound again (a fold
  every ~15 tool turns at 13–25k of 131k), so on 2026-09-07 the count rule
  was removed: a fold happens only when the request would not fit the set's
  budget, or on `/compact`.
- **Seen:** 2026-09-03, thread `4fd35f80`: four folds in sixteen turns at
  4–10k tokens against 52k.

### ISS-0031 — a tool call cut at the output limit is reported to the model as bad JSON

- **Status:** fixed 2026-09-04: `finish_reason == "length"` marks the call
  `cut`, refused as `output_cut` naming the limit; `MODEL_MAX_TOKENS` 8192.
- **Seen:** 2026-09-03, code review: a `write_file` past the cap arrived
  with unterminated arguments and "bad arguments … could not be read as
  JSON".
- **Related:** ISS-0055.

### ISS-0030 — the summarizer is handed every tool result in full

- **Status:** fixed 2026-09-04: the summarizer reads the same stubs the
  model reads (`shortened(keep=0)`).
- **Seen:** 2026-09-03, folds of thread `4fd35f80` carrying whole page
  fetches, ~100k characters for a sentence of summary.

### ISS-0029 — a summarizer request that does not fit fails a turn whose answer was already delivered

- **Status:** fixed 2026-09-04: both folds catch `BackendError` and record
  `context_fold_failed`; the turn keeps its answer.

### ISS-0027 — a shortened result's stub invited the model to run the tool again

- **Status:** fixed 2026-09-03: the stub says only where the whole result
  is stored (`read_history N`).
- **Seen:** 2026-09-03, `loop_live` I: "call the tool again for a fresh
  one" taken, the file gone, the position never used.

### ISS-0026 — reading a call back did not show what the call returned

- **Status:** fixed 2026-09-03: `read_history` of a message that made calls
  appends their results; a `search_history` hit on a call shows its result.
- **Seen:** 2026-09-03, `loop_live` H: the failed write found at #1, its
  error at #2, "no error was recorded".

### ISS-0025 — `/compact` is recorded as a failed turn

- **Status:** fixed 2026-09-03: the command finishes its trace as answered.

### ISS-0024 — the server does not say what it served from its cache

- **Status:** fixed 2026-09-03 in the Gemma App's serve command
  (`--enable-prompt-tokens-details`); GPU Apps only. The hosted providers
  pass `cached_tokens` through.

### ISS-0023 — a forced fold finds nothing to cut in a tool-heavy tail

- **Status:** fixed 2026-09-03: a fold may cut before any user or assistant
  message, never before a tool result.
- **Seen:** 2026-09-03, `/compact` on a 32-message thread whose newest 26
  were one turn's calls answered "nothing to fold".

### ISS-0022 — shortening the model's own file arguments made it write every file again

- **Status:** fixed 2026-09-03: only tool results are stubbed; the model's
  text and call arguments are never shortened.
- **Seen:** 2026-09-03, run `a459c70e`: eleven writes in a cycle once its
  first `write_file` content showed as `<1104 characters, shortened>`.
- **Related:** ISS-0041.

### ISS-0021 — the end-of-turn token reaches the chat as a message

- **Status:** fixed 2026-09-03: the streamed reader drops `<eos>`,
  `<end_of_turn>`, `<|im_end|>`, `<|eot_id|>`.
- **Seen:** 2026-09-03, run `9c42241c`: a message saying `<eos>`.

### ISS-0020 — a delivery is refused when the turn's budget is spent

- **Status:** fixed 2026-09-03: a tool marked `delivers` (`send_file`)
  still runs at the step, call or time ceiling (`tests/test_turn_bounds.py`).
- **Seen:** 2026-09-03, run `9c42241c`: the twelfth step, one `send_file`
  of four items, refused "answer now with what you already have".

### ISS-0019 — the page is written twice, identically, after the plan is updated

- **Status:** mitigated 2026-09-04: `write_file` with content the file
  already has answers `unchanged:`; the third byte-identical successful
  call in a turn is answered "already done" without running
  (`MAX_IDENTICAL_SUCCESSES`). The model's habit is not fixed; Gemma-era,
  not seen on the hosted model.
- **Seen:** 2026-09-03, five turns; worst run `9c42241c`, `index.html`
  written seven times byte-identically.
- **Related:** ISS-0042, ISS-0016.

### ISS-0017 — the screenshot the person receives is of the page without its CDN styles

- **Status:** fixed 2026-09-03, deployed: the local artifact may reach the
  public internet under the renderer's own policy (the human's decision).
- **Seen:** 2026-09-03, run `253ede5d`: Tailwind from a CDN refused by the
  offline session, the unstyled page sent as the screenshot.
- **Related:** ISS-0014.

### ISS-0014 — the page was looked at without storage or its own files

- **Status:** fixed 2026-09-03: the offline session serves the workspace at
  `http://artifact.local/` through request interception (`serve_directory`),
  so the page has an origin, storage and its siblings; a request elsewhere
  is reported as refused.
- **Seen:** 2026-09-03, twice: a `data:` URL, so `localStorage` threw and
  `styles.css` resolved to nothing.

### ISS-0013 — the repeat guard refused the call that would have worked

- **Status:** fixed 2026-09-03: the identical-failure count starts over
  when any tool has succeeded since the last identical failure
  (`tests/test_repeated_failure.py`).
- **Seen:** 2026-09-03, run `30fe463c`: two looks failed on a missing
  file, the file written, the third look counted as the third failure;
  every tool halted, 261 s.
- **Related:** ISS-0012, ISS-0042.

### ISS-0012 — a corrupted path was obeyed, and a file nobody named was made

- **Status:** fixed 2026-09-03: `resolve_in_root` refuses a path carrying
  quotes or `<|`/`|>` as `bad_arguments`.
- **Seen:** 2026-09-03, run `30fe463c`: `"Task Board test 4/index.html"<|"|>`
  created as a file on the person's volume.
- **Related:** ISS-0001.

### ISS-0011 — every look at a page carried the whole page back as its address

- **Status:** fixed 2026-09-03 (4.5.5, `page_report`): no address reported
  for a local document.
- **Seen:** 2026-09-03, runs `8ffab1aa`, `240f09ea`: the `data:` URL, ~9 KB
  of base64 per look, 17,764 input tokens by the third call.

### ISS-0009 — the person reads an answer for a minute and then it is deleted

- **Status:** fixed 2026-09-03, deployed: text that comes with a tool call
  is delivered and kept; a draft a steering refuses is held on the screen;
  the plan seam no longer objects, so the second generation it caused is
  gone. The adapter drops a byte-identical closing repeat.
- **Seen:** 2026-08-31, four turns: a narration previewed for up to 58 s,
  withdrawn when the completion ended in a tool call, then 1–17 tokens of
  answer. 2026-09-03: the same answer generated twice around a `send_file`
  (169 + 134 output tokens).
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`.
- **Related:** ISS-0035 (the approval path lacks these fixes).

### ISS-0007 — `tool_failed` carries no reason

- **Status:** fixed 2026-09-03 with the typed outcome of 4.5: `code` and
  `message` on every `tool_failed`, printed by `tools/show_run.py`.

### ISS-0006 — a path meant as a directory becomes a file, and poisons the folder

- **Status:** fixed 2026-08-31: `write_file` refuses a path ending in a
  separator and says directories are made for you; an ancestor in the way
  is named.
- **Seen:** 2026-08-31, "Personal Task Board 3": `write_file "Board 3/"`
  made a file, every later write into the folder failed, files scattered
  into the workspace root.
- **Related:** ISS-0005.

### ISS-0005 — an OS error escapes the filesystem tools unwrapped

- **Status:** fixed 2026-08-31, and since 2026-09-03 every filesystem
  failure is an `fs.*` code with the `strerror` as detail; an exception
  that still escapes any tool becomes an `internal` result, not a failed
  turn.
- **Related:** ISS-0006.

### ISS-0003 — a made file is handed over as prose instead of sent

- **Status:** mitigated 2026-09-03: the brief says where the person is
  (`Delivery.place`), that a path, link or markdown image delivers nothing;
  every tool that leaves a workspace item says `to hand it to the person:
  send_file(path="…")`. Held on the next turns and the 2026-09-04 G; the
  answer still carries a markdown path beside the send. A mechanical
  backstop in the adapter was rejected as a crutch.
- **Seen:** 2026-08-30, `[house.html](house.html)` and no file; 2026-09-03
  and 2026-09-05, files listed as paths and a screenshot as a markdown
  image of a workspace path.
- **Evidence:** `reports/2026-08-30_v2_prompt_assembly.md`,
  `reports/2026-09-03_v2_first_session_on_the_tool_system.md`.
- **Related:** ISS-0010, ISS-0020.

### ISS-0001 — the served tool parser loses what follows a long string argument

- **Status:** mitigated 2026-08-31; upstream in vLLM's Gemma 4 parser
  (vLLM 51284, 53431), GPU Apps only. The runtime survives any model's
  emission: a call whose arguments are not a JSON object is refused once
  as `bad_arguments` with the tool's signature, naming the fence as the
  cause; a call that failed twice identically is refused a third time. A
  corrected parser is in `tools/gemma4_parser.py`, tested offline, not
  deployed.
- **Seen:** 2026-08-30/31, three failed turns: `write_file` with `content`
  and no `path`, the identical call up to eight times.
- **Evidence:** `reports/2026-08-31_v2_todo_live_failure.md`.
- **Related:** ISS-0012, ISS-0015.
