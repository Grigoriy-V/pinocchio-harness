# Issues

Known defects. One entry per defect, newest first.

## What belongs here, and what does not

A defect is a place where the system does something other than what it was
built to do, and where that difference has been **observed**, not suspected.
This file is the list of those, whether or not anyone has decided to fix them.

It is not a plan and it does not authorize work. `ROADMAP.md` remains the only
source of direction, order and approved work; an issue here becomes work only
when the roadmap says so. It is also not a place for evidence: a run, a
measurement or a diagnosis lives in `reports/`, and the entry links to it.

Missing capability is not a defect. "The assistant cannot yet do X" belongs in
the roadmap. "The assistant claims to have done X and did not" belongs here.

## How to write one

Add the entry at the top of the list, take the next free number, and never
reuse a number — a closed issue keeps its id so that a report referring to it
stays readable.

```markdown
### ISS-0000 — one line, in the words of what goes wrong

- **Status:** open | mitigated | fixed | won't fix
- **Seen:** YYYY-MM-DD, where it was observed
- **Costs:** what it does to the person using the assistant
- **Reproduce:** the shortest thing that shows it, or "not reproduced"
- **Cause:** what is actually wrong, or "unknown"
- **Evidence:** reports/... , or a log identifier
- **Related:** other ids, roadmap queue items
```

Rules that keep the file honest:

- **Status is about the defect, not the effort.** `mitigated` means the harm is
  reduced and the defect is still there; only a verified fix is `fixed`.
- **Do not delete a fixed entry.** Set the status, add the date and what fixed
  it, and leave it. A defect that comes back is easier to recognise than to
  rediscover.
- **Cause stays "unknown" until it is proven.** A hypothesis written in the
  cause field becomes a fact for the next reader. Put it in `Reproduce` as a
  question or leave it out.
- **One defect per entry.** Two symptoms of one cause are one issue; one symptom
  with two causes is two.
- **A severity word is not a field here on purpose.** `Costs` says what it does
  to the person, which is the only ranking that survives disagreement.

---

### ISS-0056 — a turn spends seconds between its own steps that no model, tool or store accounts for

- **Status:** open, observed 2026-09-06
- **Seen:** 2026-09-06, live, run `42cebe2d531c4a1199b8b663c6f8832c`
  (Telegram, GLM at Novita): 71.8 s in all, of which model 46.6 s, tools
  1.9 s, persist 2.7 s, the queue 4.9 s, and **18.3 s unattributed**.
  Between `model_finished` and the next `tool_started` there are 1.8 s
  on every step (36.53→38.29, 40.97→42.77, 50.61→52.99), and 5.3 s from
  the last `persist_finished` to `turn_finished`. The previous turn
  (`e33fc1d3…`, one call): 27.1 s with 11.9 s model, 3.5 s queue.
- **Costs:** on a model that answers a call in 2–6 s, a quarter of the
  turn is the harness's own gaps; the person's first visible word came at
  51 s in a turn whose model produced it by 36 s.
- **Reproduce:** any deployed turn with tools; read the timeline's gaps.
- **Cause:** unknown. Candidates, not findings: the Telegram preview
  edits between steps, the checkpoint write after each node, the
  telemetry flush, the workspace Volume commit. The timeline does not
  yet name them, which is the first thing to fix.
- **Evidence:** `tools/show_run.py 42cebe2d531c4a1199b8b663c6f8832c`
- **Related:** roadmap item 10 (time split into model, tool, wait);
  the human's direction of 2026-09-06: the model is cheap now, the
  latency to fight is the harness's.

### ISS-0055 — a call that spends its whole output cap on reasoning is delivered as an answer with nothing said

- **Status:** fixed in the tree, 2026-09-06 — an empty completion at
  `finish_reason=length` ends the turn with a message that says the cap
  was spent before a visible word (`silent_cut`, event
  `output_cut_silent`); an empty completion at `stop` still ends it
  silently, as the prompt asks. Not yet deployed. `reasoning_tokens`
  now read from usage and written to `model_finished`, so the next such
  call says where the tokens went
- **Seen:** 2026-09-06, scenario G on `glm-5.3-flash` through CometAPI
  (`deployed-2491a3c4-70`): one model call, 4,597 tokens in, 8,192 out,
  `finish_reason=length`, no tool call, empty `content`; the loop logged
  `nothing_to_add` and ended the turn `answer_delivered`, and the person
  would have received nothing after 204 s. The FP8 App's first G with
  thinking at `low` had the same shape (2026-09-05).
- **Costs:** the person waits minutes and gets silence, billed as 8,192
  output tokens; the turn is recorded as a delivered answer, so nothing
  in the record says a request failed.
- **Reproduce:** any reasoning model whose thinking runs past
  `MODEL_MAX_TOKENS` before its first visible token.
- **Cause:** the loop treats an empty completion as "nothing to add"
  whatever its `finish_reason`; `length` with nothing visible is a cut,
  not a choice.
- **Evidence:** `reports/2026-09-06_hosted_model_cometapi.md` §4.
- **Related:** ISS-0031 (the cap and a cut call), the thinking dial in
  `docs/OPERATIONS_MAP.md`.

### ISS-0054 — the model is put to sleep in the middle of a turn whenever a tool outlives the idle window

- **Status:** open, recorded 2026-09-05 on the human's word; not scheduled
- **Seen:** 2026-09-05, scenario G on the INT4 App (`deployed-c0c0a622-70`):
  the turn ran ten `run_command` calls, several longer than the GPU
  Function's 12 s `SCALEDOWN_WINDOW` (`apt-get install`, 41 s), and the
  model endpoint scaled to zero between two of its own calls three times
  in one turn; each next model call paid a restore (20–44 s on this
  snapshot). The human's live run of the same request, whose tools were
  short, paid none. The window is the same on all three model Apps
  (`base.SCALEDOWN_WINDOW`).
- **Costs:** the person waits 20–45 s in the middle of their own request,
  once per long tool, and the turn's budget is spent on restores instead
  of work; a long-running command makes the turn slower than the command
  itself.
- **Reproduce:** any turn whose tool takes longer than 12 s; the next
  model call is a cold one (`first model token` in the telemetry).
- **Cause:** the idle window is a property of the endpoint alone; nothing
  tells it that a turn is still open. Between turns the window is right
  (the person may not write again); inside a turn it is wrong (the
  worker will certainly call again).
- **Evidence:** `reports/2026-09-05_qwen38_second_model.md` §13,
  `reports/2026-09-05_suite_and_tools_review.md` §2 point 4.
- **Related:** roadmap item 6 (the adaptive window); its smallest form is
  this: the endpoint is kept warm while a tool of the current turn runs,
  and nothing between turns. ISS-0044 (what the first call to a sleeping
  endpoint costs).

### ISS-0045 — deployed, history search cannot find a file name by its parts

- **Status:** fixed and deployed 2026-09-05 — every Postgres search matches
  on `plainto_tsquery` as well as the split query, so a name is found as
  typed; scenario I passes deployed (run `deployed-99f16efe-90`)
- **Seen:** 2026-09-05, the first deployed run of scenario I (run
  `deployed-cf8c3774-90`): `search_history "config.ini"` answered "no
  message in this conversation matches", with "Read config.ini, then list
  the workspace" the first message of the thread; the model gave up and
  asked the person for the file. The same scenario passed locally on
  2026-09-03 with the same search.
- **Costs:** the way back to a stub or a summary (`DECISIONS.md`
  2026-09-03) does not work deployed for anything with a dot in it: file
  names, versions, hosts.
- **Reproduce:** a Postgres store, a message saying `config.ini`,
  `search_messages("config.ini")`.
- **Cause:** the stored vector is `to_tsvector('simple', text)`, whose
  parser keeps `config.ini` as one token of type `file`; `match_query`
  keeps word characters only and asks `config | ini`. SQLite's FTS5
  tokenizer splits on the dot, so the local store matched and the deployed
  one could not. `tests/test_store_contract.py` already asserts a file
  name is found, and had never run against Postgres.
- **Where it belongs:** the harness.

### ISS-0049 — a failed server start is retried by the platform for as long as a request waits

- **Status:** open as a property of the platform; worked around 2026-09-05
- **Seen:** 2026-09-05, three times: the FP8 App's first boot (four
  containers in 13 minutes on the same refused ceiling), the INT4 App's
  fourth boot (three containers in one minute on the same crash). A
  request at the edge waits up to the server's `startup_timeout`, and
  Modal starts a new container for it each time the previous one's enter
  hook fails. The wake probe's own client timeout does not end the
  request: it stays queued server-side (`measure_endpoint_wake.py` says
  so), so a client that gave up still drives restarts.
- **Costs:** a refused configuration is paid for several times over
  before anyone stops it, and raising `startup_timeout` (20 min for the
  Qwen Apps) lengthens how long a stale request keeps spawning.
- **Reproduce:** deploy a configuration that fails in `start` and send
  one request.
- **Where it belongs:** the harness's deployment procedure. A
  configuration now boots first in `dry_run`, a plain Function with
  `retries=0` and no request behind it, which runs the same `boot` to
  healthy and exits with its log; the snapshot boot follows only after
  that passes. What `dry_run` cannot see is the restore.

### ISS-0053 — what a command installs outside the workspace is gone by the next command

- **Status:** open, recorded 2026-09-05 on the human's word; not
  scheduled, not diagnosed further
- **Seen:** 2026-09-05, scenario G on the INT4 App
  (`deployed-c0c0a622-70`): the model ran `npm install puppeteer` in
  `/tmp` and `apt-get install` of Chromium's libraries, then its script
  could not find what it had installed; each `run_command` may land in a
  fresh command container, and only the workspace persists between them.
  The brief says "install what that needs" and that what is installed
  lives in the workspace; the first result in a fresh container says "new
  environment: nothing installed by earlier commands is present".
- **Costs:** minutes of a turn spent installing what the next command
  cannot see; a model that reads the "new environment" line as its own
  earlier work being gone.
- **Where it might belong:** the harness's command environment. A
  possible general shape, not decided: everything a command installs
  lands in the workspace by default (`HOME`, npm's prefix and cache,
  pip's target, the working directory), so persistence needs no
  knowledge from the model, and the "new environment" line becomes
  unnecessary. pip already has a workspace venv (ISS-0043); node does
  not. Compare DeepSeek Harness, whose shell environment is one per
  session. The human: "пока непонятно, просто запиши".

### ISS-0052 — a system message that is not first is refused by Qwen3.8's chat template

- **Status:** fixed 2026-09-05 in the tree, deployed with the next
  `assistant-control` deploy
- **Seen:** 2026-09-05, scenario H on the INT4 App (`deployed-54f64c45-80`):
  the first model call answered `HTTP 400: System message must be at the
  beginning.` The context is assembled in layers, each a system message —
  the system prompt, standing instructions, the summary in the prelude,
  and the retrieved facts between the history and the turn — and the
  template's loop raises for any system message that is not the first.
  Gemma 4's template took them anywhere, so nothing had noticed.
- **Costs:** every turn with facts or a summary fails on this model; H, I
  and K, and any real conversation past its first fold.
- **Reproduce:** a turn with a stored fact, or after a fold, on a Qwen App.
- **Where it belongs:** the harness, at the provider boundary. The layers
  stay what they are inside the application; `build_messages` sends the
  shape every template accepts: the leading system messages joined into
  one, and a system message after history delivered as the first text of
  the next user message, so its place in the prompt and the cached prefix
  before it are unchanged. Three offline tests.

### ISS-0051 — the renderer's first `inspect_page` in a cold container fails before the browser is up

- **Status:** fixed 2026-09-05 in the tree, deployed with the next
  `assistant-control` deploy
- **Seen:** 2026-09-05, scenario F on the INT4 App (`deployed-b1661cff-60`):
  `inspect_page` failed after 3.44 s with `browser.load_failed: browser
  DevTools endpoint did not become ready`; the model called it again and
  the second attempt succeeded in 1.36 s. The renderer container was
  fresh, and Chromium's first launch took longer than the wait.
- **Costs:** a failed tool call and a model call to repeat it on every
  cold renderer; the check "no tool failed" in F.
- **Reproduce:** let `render_web_page` scale to zero, then `inspect_page`.
- **Where it belongs:** the harness. `_wait_for_debugger` waited sixty
  polls of 50 ms — three seconds, a count that happened to fit a warm
  machine — and now waits a stated budget of fifteen seconds
  (`DEVTOOLS_READY_SECONDS`); a browser that exits is still reported at
  once. Four offline tests.

### ISS-0050 — vLLM's ahead-of-time compile of the INT4 checkpoint dies tracing a renamed weight

- **Status:** worked around 2026-09-05 (`VLLM_USE_AOT_COMPILE=0` on the
  Qwen Apps); not diagnosed
- **Seen:** 2026-09-05, the INT4 App's fourth boot: the ordinary
  `torch.compile` had succeeded in an earlier boot of the same
  configuration, and `aot_compile_fullgraph` in `profile_run` died with
  `AttributeError: 'MergedColumnParallelLinear' object has no attribute
  'weight_packed'` — the compressed-tensors name of the packed int4
  weight, which the Marlin repack replaces after loading. The first boot
  of the same App had completed an AOT compile in 191 s; what differed
  is not established.
- **Costs:** a boot, and the restarts of ISS-0049 behind it.
- **Where it belongs:** vLLM 0.26.0 with this checkpoint; not the
  harness's to fix. AOT compilation serves a later process loading the
  artifact from disk, and no Qwen App has one: the snapshot holds the
  compiled engine. Off, per `vllm/envs.py`, the ordinary compile runs once
  per boot.

### ISS-0048 — the store's connection, hung up on during a long model call, fails the next turn

- **Status:** fixed 2026-09-05 in the tree; deployed with the next
  `assistant-control` deploy
- **Seen:** 2026-09-05, the live scenarios in the deployed worker on
  Qwen3.8 (`deployed-d6beb190-70`): G's one model call took 457 s, and
  the turn's `persist` then failed on `store.messages` with
  `psycopg.OperationalError: consuming input failed: SSL connection has
  been closed unexpectedly`; the whole scenario run died with it and the
  turn was not stored. The pooled server had closed the idle connection
  during the call. `connection.closed` and `.broken` do not know until a
  statement is sent, so `_live_connection` handed the dead one over.
- **Costs:** a turn lost after its model work was done, and a run of
  scenarios lost with it; on a person's turn, an error after a long wait.
- **Reproduce:** any turn whose gap between two store statements outlasts
  the server's idle timeout — a long model call, a long tool. Gemma's
  turns rarely reached it; a 27B with thinking does.
- **Where it belongs:** the harness. The first statement after a pause is
  the store's own (`SET LOCAL search_path`), so a hang-up surfacing there
  is resent once on a fresh connection with nothing of the caller's to
  replay (`PostgresStore._opened`); a hang-up during the caller's
  statements is still the caller's, as before. Offline tests with a
  connection that looks open and fails its first statement.
  `reports/2026-09-05_qwen38_second_model.md` §7.

### ISS-0047 — a GPU snapshot taken with an uncommitted Volume path open cannot be restored

- **Status:** fixed twice 2026-09-05. The first fix, a Volume commit
  before the sleep, was not enough: the INT4 App's second boot committed
  and still died restoring, on a Triton kernel directory
  (`…/inductor_cache/triton/0/<kernel>`) written during warmup. Modal's
  documentation states the rule: "Deleting files in a Volume used during
  restore will cause restore failures", and the compilers write through
  temporary names they rename or remove. So the second fix keeps the
  snapshot from holding anything on a Volume at all: the Qwen Apps do not
  mount the compile-cache Volume on their `Server`; a version's first boot
  compiles from nothing (~190 s, once) and every restore skips compilation
  because the snapshot holds the compiled engine. Copying the Volume's
  cache to local disk around the boot was tried in between and hung a
  boot for eight minutes on thousands of small files over 9p. Deployed in
  `assistant-llm-qwen-int4`, not yet booted; the FP8 App in the tree; the
  Gemma App keeps its Volume, which works only because its cache predates
  its snapshot.
- **Seen:** 2026-09-05, the third boot of `assistant-llm-qwen`: vLLM
  compiled afresh, saved its AOT graph under
  `/root/.cache/vllm/torch_compile_cache/torch_aot_compile/…` on the
  `assistant-vllm-cache` Volume, slept, and Modal created the snapshot;
  the restore failed with `failed to complete restore for filesystem type
  "9p": failed to walk "…/torch_aot_compile/e2dbd899…": no such file or
  directory`, exit 128, and the wake request got a 500.
- **Costs:** a boot (~5 L40S-minutes) per attempt, and no endpoint; the
  same App boots fine when its compile cache is already committed, so the
  defect appears exactly when a configuration change forces a recompile.
- **Reproduce:** change anything that alters vLLM's compile key
  (`max_num_seqs`, the ceiling) on an App whose `start` does not commit
  the Volume, then wake it.
- **Where it belongs:** the harness — the deployment's `start` hook, which
  wrote to a Volume and snapshotted with the write uncommitted. The
  general property: what a snapshot holds open must exist where the
  restore mounts. Fixed by `vllm_cache.commit()` after the warmup and
  before the sleep, in both `model_app.py` and `model_app_qwen.py`.
  `reports/2026-09-05_qwen38_second_model.md` §5.

### ISS-0046 — a deliverable the tools can make is fabricated by another means

- **Status:** open — model behaviour, observed once
- **Seen:** 2026-09-05, deployed, scenario G (run `deployed-cf8c3774-70`):
  asked for a screenshot of the page it had built, the model wrote in a
  command "I can't actually take a screenshot of a running web app in a
  headless container", drew a white 800×600 PNG with PIL, named it
  `task_board_preview.png`, sent it with the files and offered it in the
  answer as a path. `inspect_page`, which returns a real screenshot and
  is described so in the brief, was never called; it was called in the
  four G runs before this one that day.
- **Costs:** the person receives a picture that shows nothing and is told
  it is the screenshot; worse than no picture.
- **Reproduce:** G deployed; one in five today.
- **Where it belongs:** the model's, on the evidence — the brief names
  the tool and says what it returns. Measured by G; no mechanism.

### ISS-0044 — the first streamed request to a sleeping model endpoint dies at the read timeout

- **Status:** fixed 2026-09-05, twice. The first fix retried the stream
  on a timeout, and that was wrong: a timed-out request is still queued
  at Modal's edge and is answered when the container is up, so each
  retry queued another copy — seen the same evening as one request a
  minute stacking up behind a seven-minute snapshot boot, every copy
  then answered to a closed connection (the human's observation). The
  second fix: a timeout is never retried, in `stream` or `_completion`;
  what is retried before the first chunk is a refused connection or a
  "later" status; and `MODEL_TIMEOUT` is 600 s, long enough for the
  first byte of an endpoint creating a snapshot. **Third, the cause:**
  Modal's edge answers any request older than 150 s with a `303` to a
  URL that holds it, every 150 s (`docs/guide/webhook-timeouts`), and the
  model client did not follow redirects — so a wake longer than 150 s
  never returned an answer, and `context_limit` took the 303's empty
  body for the server's JSON and died (`deployed-2f3a23eb-80`, 14:45 UTC,
  after exactly 150 s). The client now follows up to eight hops, twenty
  minutes, and `context_limit` treats anything but a 200 with JSON as
  unknown. The morning wake probe had said it: "Modal's edge can redirect
  a request while the container is still coming up. `urllib` follows
  that redirect". Deployed with the next `assistant-control` deploy.
- **Also seen:** 2026-09-05, `loop_live --deployed A B C E F R S` on
  `assistant-llm-qwen-int4`, first call, `httpx.ReadTimeout`; the
  endpoint was seven minutes into a snapshot boot.
- **Seen:** 2026-09-05, twice, `loop_live --deployed` from a cold GPU (runs
  `deployed-019b85ed-70`, `deployed-f845ff58-70`): the turn's first model
  request raised `httpx.ReadTimeout` out of `stream` and the turn died;
  the run that followed once the endpoint was awake went through. In the
  next successful turn the first model call still shows 80–118 s and
  120–130 s "unattributed" before it — the wake, paid a second time.
- **Costs:** the first thing a person asks after a quiet hour fails
  outright, and the wake it paid for is spent on nothing.
- **Reproduce:** let the model app scale to zero, then send one turn.
- **Cause:** `ModelSettings.timeout` is 120 s and the endpoint wakes in
  more than that; `_completion` (the non-streaming path) retries a
  transport error twice with backoff, `stream` does not retry at all —
  its docstring says a failure before the first delta "could be retried
  safely, and is not, for now". A wake is exactly that failure.
- **Where it belongs:** the harness. The general property is that a
  request that produced nothing yet may be sent again; no interface
  should know that an endpoint sleeps.

### ISS-0043 — in the deployed container, `pip` is not `python3`'s pip

- **Status:** fixed in the tree, 2026-09-04 — `pip` installed into the
  image's uv venv beside the libraries, so `pip`, `python3 -m pip` and
  `python3` are one interpreter; the cold-start probe checks `pip show` sees
  what `python3` imports. Deployed 2026-09-04
- **Seen:** 2026-09-04, deployed, thread `e8c54e07`: asked to check, the
  model ran `pip show fpdf2` → "Package(s) not found", `pip list` → pip, uv,
  wheel and nothing else, then `python3 -c "import reportlab"` → 5.0.1. It
  concluded, reasonably, that it "cannot install libraries", and the brief
  had told it nine packages were there.
- **Costs:** a model that checks — the thing asked of it all day — is told
  the wrong answer by the environment; a `pip install` would land in an
  interpreter `python3` never uses.
- **Reproduce:** `pip list` and `python3 -m pip list` in `run_command`.
- **Cause:** `uv_sync` makes the image's Python a uv venv (`/.uv/.venv`),
  first on `PATH` for `python3` but carrying no `pip`; `pip` then resolves
  to the base interpreter's `/usr/local/bin/pip`.
- **Evidence:** `reports/2026-09-04_v2_isolated_execution_review.md` §12
- **Related:** ISS-0038 (the same question locally), step 5

---

### ISS-0042 — a command is refused as "already done" after the file it runs was rewritten

- **Status:** fixed in the tree, 2026-09-04 — `succeeded_before` starts its
  count over when a different call of a tool that changes the workspace
  (`mutates`) succeeded in between; an identical call still counts against
  itself, so ISS-0019's loop is still caught. Deployed 2026-09-04
- **Seen:** 2026-09-04, deployed, run `f25fd7cd` (P in Russian): `write_file
  make_pdf.py` → `python3 make_pdf.py` (exit 1) → rewrite → run (exit 1) →
  rewrite → `ls` the fonts → rewrite with `add_font` for both faces → `python3
  make_pdf.py`: "this exact call has already succeeded twice in this turn
  with these same arguments and was not run again". The fourth version, the
  one that had a chance, never ran; the model tried the same line twice
  more, then a one-line `python3 -c` with the whole script, and gave up.
- **Costs:** the turn's work thrown away at the step where it would have
  paid; the person gets text instead of the file.
- **Reproduce:** any edit-and-run loop longer than two rounds where the run
  command is the same line, which is every edit-and-run loop.
- **Cause:** the identical-success guard (ISS-0019, a byte-identical write
  seven times) counts over the whole turn and never resets; a non-zero exit
  is a success by design. Hermes's rule — a landed mutation between two
  attempts makes the retry a new experiment — was recorded as an option in
  the 2026-09-03 references review and not taken.
- **Evidence:** `reports/2026-09-04_v2_isolated_execution_review.md` §12
- **Related:** ISS-0019, ISS-0013 (the failure guard learned the same lesson
  the other way), ISS-0041

---

### ISS-0041 — within one turn, the errors a command met are shortened away while the model is still fixing them

- **Status:** fixed in the tree, 2026-09-04 — the turn in progress is never
  shortened; stubs are for stored history only, where the model has already
  said what it made of a result, and a stub names where it is stored
  (`shortened` in `app/context/window.py`; `DECISIONS.md` 2026-09-03,
  amended). The size fold, which 4.7 checked in the middle of a turn (K),
  is what bounds a long turn. Deployed the same day (v71) and seen live:
  twelve steps, `stubbed=0` on each, every traceback in view
- **Seen:** 2026-09-04, deployed, runs `510fe752` and `3b3c86d8` (P in
  Russian): six rewrites of one script, each answered by a traceback with
  `exit code: 1`. `context_prepared` shows `stubbed` climbing 1, 2, 3, 4, 5
  across the steps: the surface keeps the newest `keep_results` (two) tool
  results verbatim and shows the older ones as stubs, and a non-zero exit is
  a **result**, not a failure, so the "failures are kept" rule did not hold
  it. By the fourth attempt the model saw its own three earlier scripts in
  full and their tracebacks as `[run_command cat << 'EOF' …: 900
  characters; shortened, call the tool again for the full result]`, and it
  repeated the first attempt's error exactly (attempt 4 = attempt 1). The
  stub's own wording, "call the tool again", for a command is "run the
  failing script again".
- **Costs:** a debugging loop that cannot see what it already tried; the
  turn's budget spent, the person stopping it by hand (twice today).
- **Reproduce:** any turn with more than two commands whose output matters
  to the next command — a build, a test run, a script under repair.
- **Cause:** the shortening rule (`shortened` in `app/context/window.py`,
  `DECISIONS.md` 2026-09-03) counts results across history and the current
  turn alike; its stated reason — "the model has already said what it made
  of them" — is true of a previous turn and false in the middle of this
  one. A failure is kept because "it is why the model did what it did
  next"; a command's non-zero exit is the same thing and is not kept.
- **Evidence:** `reports/2026-09-04_v2_isolated_execution_review.md` §12
- **Related:** ISS-0022 (the model's own words, shortened, made it rewrite
  every file — the mirror of this), ISS-0040; roadmap 5

---

### ISS-0040 — a document is handed over without a look at it

- **Status:** open — model behaviour, measured
- **Seen:** 2026-09-04, deployed, Telegram, runs `a3ef6a14` and `3935092c`:
  "make a one-page PDF about coffee and send it" — `write_file`,
  `run_command`, `send_file`, no `view_pages` or `read_document` in between,
  twice in a row. The first PDF reached the person as black squares (the
  container had no font with Cyrillic; fixed in the image the same day),
  and a look would have shown it; asked again, the model dropped the
  Russian text instead of registering a font, and again did not look.
- **Costs:** the person receives a document the assistant never saw, and
  finds the defect the assistant was equipped to find.
- **Reproduce:** P through Telegram; not every time — the same request
  passed with a look locally twice on 2026-09-04.
- **Also seen:** 2026-09-05, deployed, P three times (`deployed-77de41a5-140`,
  `-e63f050c-140`, `-cf8c3774-140`): the PDF checked with `pdftotext`
  through `run_command` before `send_file` every time — a look at the
  text, not at the page; the scenario's check counts only `view_pages`.
- **Cause:** the model's; the brief says a made document is looked at
  before it is handed over, and the tools were there. Measured with the
  scenario suite, not scripted (`AGENTS.md`, the human's rule).
- **Evidence:** `reports/2026-09-04_v2_isolated_execution_review.md` §12
- **Related:** ISS-0039 (the other P failure), ISS-0028; roadmap 5

---

### ISS-0039 — a command that succeeds silently is not believed, and is run again

- **Status:** open — model behaviour, measured
- **Seen:** 2026-09-04, `scripts/loop_live.py` P, run `live-140`, second
  rerun: `python make_pdf.py` returned `exit code: 0` and `(no output)`, the
  PDF was on disk, and the model ran the script again, then `python3`
  (absent on Windows, and the brief says where commands run), then the
  script again, until the repeat guard refused; it answered without
  `read_document` or `send_file`. The same request passed twice earlier the
  same day.
- **Costs:** a turn's budget spent on nothing, and the delivery the
  person asked for not made.
- **Reproduce:** P; not every time.
- **Cause:** the model's; one observation. What the harness said back was
  true: exit code zero, no output. Whether "(no output)" reads to the model
  as "nothing happened" is a question for the scenario runner, not a line
  to add.
- **Also:** the `python3` attempt came back as mojibake — `cmd` answers in
  the OEM code page and the runner read UTF-8 — which the model could not
  act on; fixed the same day (`decoded` in `app/tools/shell.py`).
- **Evidence:** `reports/2026-09-04_v2_isolated_execution_review.md` §10
- **Related:** ISS-0004 family (4.9); roadmap 5b

---

### ISS-0038 — a package is installed into the machine's own Python rather than the workspace

- **Status:** fixed, 2026-09-04 — by the boundary, not by a rule: on Windows
  a command runs under a write-restricted token and the operating system
  refuses every write outside the workspace, a `pip install` into the
  machine's Python included (`app/tools/shell_windows.py`, the DeepSeek
  Harness mechanism, with what it took to make it start recorded there).
  The workspace's own venv is the `python` and `pip` a command sees. The
  interim `PIP_REQUIRE_VIRTUALENV` / `npm_config_prefix` rules, which the
  human called a crutch, are gone. Deployed 2026-09-04 (5a); deployed the
  container is the boundary
- **Seen:** 2026-09-04, `scripts/loop_live.py` P, run `live-140`, the first
  live turn with `run_command`: asked for a PDF, the model ran
  `pip install reportlab` as its first command, and it landed in
  `c:\python314\lib\site-packages` — the person's global Python — with
  the brief saying in the same prompt to install into a virtual environment
  inside the workspace.
- **Costs:** locally, a change to the person's machine outside the workspace,
  which is the one place the agent is told is its own. Deployed (5a), the
  same command installs into a container that is gone in three minutes, so
  the next turn's `import` fails and the install is paid again.
- **Reproduce:** P.
- **Cause:** the model's choice against the brief's sentence, on one turn;
  and nothing in the environment makes the workspace the place an install
  lands. The environment already makes it the home (`HOME`, `USERPROFILE`
  in `command_environment`).
- **Evidence:** `reports/2026-09-04_v2_isolated_execution_review.md` §9
- **Related:** roadmap 5a, 5b; `DECISIONS.md` 2026-09-04 ("what it installs
  lives in the workspace")

---

### ISS-0037 — the failure message sent to the person carries the endpoint URL and the server's body

- **Status:** open
- **Seen:** 2026-09-03, code review (`reports/2026-09-03_v2_whole_code_review.md` §2.10); the wording itself was
  seen live on 2026-09-03 in the `<eos>`-era failures
- **Costs:** "That request failed: BackendError: the endpoint https://… could
  not be reached (…)" and, for an HTTP failure, the server's own response
  text, in the chat. For the owner it is a diagnostic; under open access it
  is infrastructure detail handed to strangers, and it is the one place the
  product speaks in tracebacks.
- **Reproduce:** point `MODEL_ENDPOINT` at an unreachable address and send a
  message; read the reply.
- **Cause:** `TelegramAdapter.handle_update` sends `f"{type}: {error}"` of
  any exception; `BackendError` texts include the endpoint and the body.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md`
- **Related:** none

### ISS-0036 — a transient failure before the first streamed token fails the turn

- **Status:** open
- **Seen:** 2026-09-03, code review (`reports/2026-09-03_v2_whole_code_review.md` §2.9); not yet seen live
- **Costs:** a 503 or 429 from the proxy while the GPU wakes, on the one
  path every conversational call takes, is a failed turn with an error in
  the chat, where the same failure on `invoke` is retried with backoff.
- **Reproduce:** a scripted transport answering 503 once, then 200, against
  `OpenAICompatibleBackend.stream`; the first answer is the error.
- **Cause:** `stream()` retries nothing; its docstring says a retry before
  the first delta would be safe and is not done "for now".
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md`
- **Related:** none

### ISS-0035 — the approval-resume path in Telegram lost the answer path's fixes

- **Status:** open
- **Seen:** 2026-09-03, code review (`reports/2026-09-03_v2_whole_code_review.md` §2.8); not yet seen live
- **Costs:** after a person presses approve, a verbatim repeat of the text
  written beside the call is sent again (the ISS-0009 mitigation is not
  applied), and a draft the turn withdrew is deleted instead of held, so the
  person watches an answer vanish where the ordinary path keeps it.
- **Reproduce:** a turn that stops for approval, then produces the same text
  twice; compare the chat with the same turn without an approval.
- **Cause:** `TelegramAdapter._on_callback` re-implements the event loop of
  `_answer` and was not updated with it: no `delivered` set is passed to
  `_deliver`, and `AnswerWithdrawn` calls `preview.discard()` where `_answer`
  calls `preview.hold()`.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md`
- **Related:** ISS-0009

### ISS-0034 — a worker's lease outlives the container's own kill by five minutes

- **Status:** fixed, 2026-09-04 — `LEASE_SECONDS` 590 against the 600 s
  timeout, and the turn is taken up from its checkpoint by the next claim
  (4.7). Deployed the same day
- **Seen:** 2026-09-03, code review (`reports/2026-09-03_v2_whole_code_review.md` §2.7); not yet seen live
- **Costs:** when a worker container dies mid-turn, the conversation stays
  `running` with a live lease until 900 s after the claim, and every later
  message of that person waits silently for it: up to fifteen minutes with
  no reply and no explanation.
- **Reproduce:** the three numbers — `TurnBudget.max_seconds` 300, the
  worker's Modal `timeout` 600, `claim(lease_seconds=900)` — read side by
  side; a killed container leaves `lease_until` in the future for
  900 − 600 s at least.
- **Cause:** the lease length is not derived from the turn's ceiling or the
  container's timeout and is longer than both.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md`
- **Related:** none

### ISS-0033 — no tool has a deadline; a hung tool holds the worker until the platform kills it

- **Status:** open
- **Seen:** 2026-09-03, code review (`reports/2026-09-03_v2_whole_code_review.md` §2.5); not yet seen live
- **Costs:** `read_document` on a large PDF, `view_pages`, a slow database
  under `search_history` or a stuck volume under `write_file` run on the event
  loop with no bound; the turn's own `max_seconds` is read only between
  steps, so a hung call holds the worker to Modal's 600 s kill, and then
  ISS-0034 holds the conversation longer.
- **Reproduce:** `grep timeout_seconds= app/tools` finds no tool; a tool whose
  body sleeps past `max_seconds` ends the turn only when the container ends.
- **Cause:** `Tool.timeout_seconds` exists, the executor honours it, and no
  tool sets it; the web tools carry their own httpx deadlines and nothing
  else carries any.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md`
- **Related:** ISS-0034

### ISS-0032 — the conversation folds every twelve messages whatever their size

- **Status:** fixed in the tree, 2026-09-04 — `summarize_after` is 60, a
  fallback for a server that reports no window; the size trigger is the rule.
  The summary's word cap now follows what a fold covers (150 + 15 a message,
  at most 600). Deployed the same day
- **Seen:** 2026-09-03, deployed, thread `4fd35f80`: four folds in sixteen
  turns (`count` at 12, 24, 36, `asked` at 15) with requests between 4k and
  10k tokens against a 52k budget
- **Costs:** each fold is a model call, invalidates the served prefix cache
  behind the summary, and rewrites the summary from the previous summary,
  so exact wording is lost in steps a person never asked for while most of
  the window sits empty. Recall of an exact detail then needs
  `search_history` where verbatim history would have carried it.
- **Reproduce:** any conversation past sixteen messages; `/context` shows
  the summary covering messages the window had room for.
- **Cause:** `ContextPolicy.summarize_after=16` triggers on a message count
  alone; the size trigger, which is exact, rarely gets to fire first.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md`, `reports/2026-09-03_v2_history_recovery_review.md`
- **Related:** ISS-0030

### ISS-0031 — a tool call cut at the output limit is reported to the model as bad JSON

- **Status:** fixed in the tree, 2026-09-04 — `finish_reason == "length"`
  marks an unreadable call as `cut`, and the executor refuses it as
  `output_cut`, naming the limit and the way out (smaller pieces);
  `MODEL_MAX_TOKENS` default 8192. Deployed the same day
- **Seen:** 2026-09-03, code review (`reports/2026-09-03_v2_whole_code_review.md` §2.3); not yet seen live
- **Costs:** a `write_file` whose content runs past `MODEL_MAX_TOKENS`
  (4096) arrives with `finish_reason="length"` and unterminated arguments;
  the model is told "bad arguments … could not be read as a JSON object",
  which is not what happened, and its natural retry is the same file cut
  at the same place — a rewrite loop with a cause nothing names.
- **Reproduce:** ask for a single file of about 15,000 characters; read
  `finish_reason` on the `model_finished` event and the refusal that
  follows.
- **Cause:** nothing in `app/agent/graph.py` or the backend reads
  `Completion.finish_reason`; `read_arguments` sees only the broken JSON.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md`
- **Related:** ISS-0019, ISS-0001

### ISS-0030 — the summarizer is handed every tool result in full

- **Status:** fixed in the tree, 2026-09-04 — the summarizer reads the same
  stubs the model reads (`shortened(keep=0)`, with positions). Deployed the
  same day
- **Seen:** 2026-09-03, code review (`reports/2026-09-03_v2_whole_code_review.md` §2.1); the cost is visible in
  today's folds of thread `4fd35f80`, each carrying whole page fetches
- **Costs:** a fold of twelve messages with three page fetches sends the
  summarizer ~100k characters, the largest prefill of the conversation, for
  text of which the summary keeps a sentence; on a long tool turn the
  summarizer request can itself exceed the window (ISS-0029).
- **Reproduce:** fold a thread holding three `fetch_page` results; read the
  summarizer request's `input_tokens` on the trace.
- **Cause:** `summarize()` renders `pending[:cut]` with `transcript`, which
  writes every content part whole; the surface's stubbing (`shortened`) is
  not applied to what the summarizer reads.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md`
- **Related:** ISS-0029, ISS-0032

### ISS-0029 — a summarizer request that does not fit fails a turn whose answer was already delivered

- **Status:** fixed in the tree, 2026-09-04 — both folds catch `BackendError`
  and record `context_fold_failed`; the turn keeps its answer. Deployed the
  same day
- **Seen:** 2026-09-03, code review (`reports/2026-09-03_v2_whole_code_review.md` §2.1); not yet seen live
- **Costs:** the answer streams to the person, then `persist` folds, the
  summarizer's request exceeds the window, the node raises, and the chat
  gets "That request failed: ContextOverflowError" under a complete answer.
  The same fold in `fitted` fails the step before the answer instead.
- **Reproduce:** a thread whose pending messages hold enough full tool
  results to exceed the window (ISS-0030 makes this reachable), then one
  more turn.
- **Cause:** `fold_older_messages` is called in `persist` and in `fitted`
  with no handling of `ContextOverflowError`; only the overflow-recovery
  branch of `_ask` catches it.
- **Evidence:** `reports/2026-09-03_v2_whole_code_review.md`
- **Related:** ISS-0030

### ISS-0028 — the model says it checked its memory without calling anything

- **Status:** open — model behaviour, for 4.9 with ISS-0004
- **Seen:** 2026-09-03, deployed, run `80a5e47e`, thread `4fd35f80`: asked
  "Что ты помнишь", the model summarised the conversation and offered to
  check its saved facts; the person said "да"; the model answered "Я
  проверил свою память. На данный момент в ней нет сохраненных фактов" with
  no tool call (1 model call, 0 tools). `search_memory` was in the toolbox.
- **Costs:** a claim of an action that did not happen, and a wrong answer
  about what is saved.
- **Reproduce:** the two messages above in a thread with facts.
- **Cause:** the model's own choice; the same class as ISS-0004 — describing
  a check or a source it did not open.
- **Also, 2026-09-04:** partly the harness's. Facts reach the model only
  through the per-turn layer, a keyword match of the latest user text
  against the facts, and when nothing matches the layer is absent without
  a word; "Что ты помнишь" and "да" match nothing. The model was looking
  at a context with no facts and no sign that a search had run. The
  retrieval design, not a check on the answer, is where this belongs;
  `reports/2026-09-04_v2_observed_claims_review.md` §9.
- **Evidence:** `reports/2026-09-03_v2_history_recovery_review.md`
- **Related:** ISS-0004

### ISS-0027 — a shortened result's stub invited the model to run the tool again

- **Status:** fixed in the tree, 2026-09-03 — the stub says only where the
  whole result is stored. Deployed the same day
- **Seen:** 2026-09-03, `scripts/loop_live.py I`, run `live-90` (second
  run): a `read_file` result shown as `[read_file config.ini: 342
  characters; shortened — read_history 2 for the full result, or call the
  tool again for a fresh one]`; the file was gone; the model called
  `read_file`, got `fs.not_found`, listed the workspace and asked the person
  for the file. It never took the position it had been given.
- **Costs:** the way back to a stored result exists and is not used; a
  non-repeatable result is lost to the person in practice.
- **Reproduce:** the scenario as it stood in `101718c`.
- **Cause:** the stub offered two ways and the model took the familiar one,
  then treated its failure as the end. With the wording "the full result is
  stored: read_history 2" the third run tried the file, then read history,
  and quoted the line.
- **Evidence:** `reports/2026-09-03_v2_history_recovery_review.md`
- **Related:** ISS-0026

### ISS-0026 — reading a call back did not show what the call returned

- **Status:** fixed in the tree, 2026-09-03 — `read_history` of a message
  that made calls appends their results; a `search_history` hit on a call
  shows what came back on the next line. Deployed the same day
- **Seen:** 2026-09-03, `scripts/loop_live.py H`, run `live-80` (first
  run): asked for the exact text of an earlier failed write, the model
  searched `write_file`, found the call at #1, read #1, saw no error in it,
  and answered that no error was recorded. The failure was #2.
- **Costs:** the exact detail the summary lost stays lost, with a confident
  wrong answer.
- **Reproduce:** the scenario as it stood in `101718c`.
- **Cause:** a call and its result are two stored rows and the tools
  returned one of them; to a reader they are one thing.
- **Evidence:** `reports/2026-09-03_v2_history_recovery_review.md`
- **Related:** ISS-0027

### ISS-0025 — `/compact` is recorded as a failed turn

- **Status:** fixed in the tree, 2026-09-03 — the command finishes its trace
  as answered. Deployed since
- **Seen:** 2026-09-03, deployed, run `eb455286`: `/compact` answered the
  person and the run row says `failed`, `incomplete`.
- **Costs:** a reliability figure that counts a command as a broken turn.
- **Reproduce:** `/compact`; read the run.
- **Cause:** the command path never closed its trace, and the runner closes
  an unclosed one as incomplete.
- **Evidence:** `reports/2026-09-03_v2_context_engine_review.md`
- **Related:** none

### ISS-0024 — the server does not say what it served from its cache

- **Status:** fixed in the tree, 2026-09-03 — `--enable-prompt-tokens-details`
  on the model server. **Needs a model-app deploy**, which is a new boot and
  a human gate; deferred by the human the same day.
- **Seen:** 2026-09-03, deployed, run `a459c70e`: every `model_finished`
  without `cached_tokens`, so `/context` and the trace cannot show the cache.
- **Costs:** the measurement 4.6a's assembly is judged by is blank.
- **Reproduce:** any turn; read `model_finished`.
- **Cause:** vLLM reports `prompt_tokens_details` only with that flag, which
  the serve command did not pass.
- **Evidence:** `reports/2026-09-03_v2_context_engine_review.md`
- **Related:** ROADMAP 4.6a acceptance

### ISS-0023 — a forced fold finds nothing to cut in a tool-heavy tail

- **Status:** fixed in the tree, 2026-09-03 — a fold may cut before any
  user or assistant message, never before a tool result. Deployed since
- **Seen:** 2026-09-03, deployed: `/compact` after the Task Board turn
  answered "nothing to fold" on a 32-message thread whose newest 26 were one
  turn's calls and results.
- **Costs:** the person asks for a fold and gets none; a size-forced fold
  inside a long turn has the same dead end.
- **Reproduce:** a long tool turn, then `/compact`.
- **Cause:** the cut only landed on a user message, and there was none in
  the last eight.
- **Evidence:** `reports/2026-09-03_v2_context_engine_review.md`
- **Related:** ISS-0019

### ISS-0022 — shortening the model's own file arguments made it write every file again

- **Status:** fixed in the tree, 2026-09-03 — only tool results are stubbed;
  the model's text and call arguments are never shortened. Deployed since
- **Seen:** 2026-09-03, deployed, run `a459c70e`, "Task Board test 9": from
  the step where its first `write_file` content was shown as
  `<1104 characters, shortened>`, the model rewrote index, styles and app in
  a cycle, eleven writes, the ceiling, no screenshot, no files, and an
  answer saying it cannot send files. Test 8 on the same request, before
  the shortening, had one rewrite and delivered everything.
- **Costs:** the work is not delivered and the turn costs the ceiling.
- **Reproduce:** the Task Board request with argument shortening on.
- **Cause:** what the model wrote is what it remembers doing; with it
  replaced by a placeholder it does the work again.
- **Evidence:** `reports/2026-09-03_v2_context_engine_review.md`
- **Related:** ISS-0019, ISS-0003

### ISS-0021 — the end-of-turn token reaches the chat as a message

- **Status:** fixed in the tree, 2026-09-03 — the streamed-completion reader
  drops `<eos>`, `<end_of_turn>`, `<|im_end|>` and `<|eot_id|>` from the
  text, so a completion made of one such token is empty and ends the turn
  without a message. Deployed and held on run `af0370cb`: the turn ended on its answer, nothing after it.
- **Seen:** 2026-09-03, deployed, run `9c42241c`: the last model request of
  a spent turn answered with the single token `<eos>`, which was delivered
  to the person as its own message.
- **Costs:** a message that says `<eos>`.
- **Reproduce:** a turn whose last request has nothing to add; the served
  Gemma returns its end token as text.
- **Cause:** the server hands the end-of-turn token over as content and the
  client took every content chunk as text.
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Related:** ISS-0020

### ISS-0020 — a delivery is refused when the turn's budget is spent

- **Status:** fixed in the tree, 2026-09-03 — a tool marked `delivers`
  (`send_file`) still runs when the step, call or time ceiling is reached;
  every other call in that batch is halted as before, and the turn still
  ends. `tests/test_turn_bounds.py`. Deployed; run `af0370cb` never reached the ceiling, so the live check of the exemption is still to come.
- **Seen:** 2026-09-03, deployed, run `9c42241c`: after eleven tool calls
  the model wrote its answer together with one `send_file` of all four
  items — three files and the screenshot, one call, as asked for that
  morning — and the call was the twelfth step, refused with "answer now
  with what you already have". The person received the answer naming the
  files and none of the files.
- **Costs:** finished work stays in the workspace with a sentence saying it
  is done.
- **Reproduce:** any turn whose delivery is the step at the ceiling.
- **Cause:** the ceiling halted every call in the batch, a delivery among
  them, although a delivery costs no model time and is the outcome the
  ceiling exists to protect.
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Related:** ISS-0019, ISS-0003

### ISS-0019 — the page is written twice, identically, after the plan is updated

- **Status:** mitigated, 2026-09-04 — the third byte-identical successful
  call in a turn is answered "already done" without running
  (`MAX_IDENTICAL_SUCCESSES`); the second still runs and `write_file` still
  answers `unchanged:`. The model's habit itself is not fixed
- **Seen:** 2026-09-03, deployed, five turns in a row (runs `b100a27a`,
  `f41278c9`, `af276ed7`, `752486c1`, `7673ce55`)
- **Costs:** after the three files are written and the plan is ticked, the
  model writes `index.html` again with byte-identical content (1245
  characters both times, compared in run `7673ce55`): one model call of
  about 9 s and 368 output tokens per turn for nothing.
- **Reproduce:** the Task Board request with a plan; compare the two
  `write_file` calls on `index.html`.
- **Cause:** unknown. It follows the `todo_write` update every time, as if
  the model re-executes the step it just marked done.
- **Also:** 2026-09-03, code review — the loop's repeat guard
  (`failed_before` in `app/agent/graph.py`) counts identical *failed* calls
  only, so a run of identical successful writes is bounded by nothing but
  the turn budget; `write_file`'s `unchanged:` answer is the only brake.
  `reports/2026-09-03_v2_whole_code_review.md` §2.4.
- **Also seen, without a plan:** 2026-09-03, run `9c42241c`, the worst so
  far: `index.html` written seven times, `styles.css` and `app.js` twice
  each, ten `write_file` calls where three were the work, 125 s and 5000
  output tokens before the page was inspected; then the ceiling. Not the
  plan, then. The identical writes were byte-identical each time.
- **Mitigated:** 2026-09-03, `write_file` with content the file already has
  answers `unchanged: … already had exactly this content … nothing was
  written` instead of `overwrote`, so a rewrite no longer reads as progress.
  Whether the model stops on that word is for the next live turn.
- **Held:** run `af0370cb`, "Task Board test 8": one identical rewrite of
  `index.html`, answered `unchanged`, and the model went straight to
  `inspect_page`. One wasted call of 8 s instead of seven.
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Related:** ISS-0016

### ISS-0018 — an existing folder is replaced without a word

- **Status:** open
- **Seen:** 2026-09-03, deployed, three turns in a row asking for "Task Board
  test 5" in the folder `Task Board test 5` (runs `f41278c9`, `af276ed7`,
  `752486c1`)
- **Costs:** the folder already held the previous attempt. The assistant
  wrote over all three files, read `overwrote Task Board test 5/index.html`
  four times per turn in its own tool results, and answered "Приложение
  готово" as if the place had been empty. The person is told nothing about
  what was there, what was kept or what was replaced. Work of theirs in a
  folder of the same name would go the same way.
- **Reproduce:** ask for an app in a folder that already has one.
- **Cause:** unknown. `list_files` was available and never called; the
  result word "overwrote" was read and ignored. The brief says workspace
  writes are autonomous and says nothing about what to do when the place is
  taken.
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Related:** ISS-0004; roadmap 4.7 is where the wording "if the place
  already holds files, say so and decide — keep, replace or a new name —
  never replace silently" would be accepted

### ISS-0017 — the screenshot the person receives is of the page without its CDN styles

- **Status:** fixed in the tree, 2026-09-03 — the human allowed the local artifact the public internet under the renderer's policy; not yet deployed
- **Seen:** 2026-09-03, deployed, thread `46c6a9c3`, run `253ede5d`
- **Costs:** the page loads Tailwind from `https://cdn.tailwindcss.com/`. The
  offline session refuses it, as it should, and says so in the report; the
  screenshot that `send_file` then delivers is of the unstyled page, while
  the person's own browser, which is online, shows the styled one. The
  model read "requests refused: https://cdn.tailwindcss.com/" and still wrote
  "Адаптивный дизайн (Tailwind CSS)".
- **Reproduce:** a page with a stylesheet or script from a CDN; inspect it;
  send the screenshot.
- **Cause:** the local artifact is rendered with no network by design
  (`DECISIONS.md` 2026-09-03: the boundary is a property of the session), and
  nothing tells the person or the model that the picture differs from what a
  browser with internet would show. Whether a local artifact may fetch from
  a public CDN through the same policy the public renderer uses is undecided.
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Related:** ISS-0014, ISS-0004; 4.5.5

### ISS-0016 — the plan is a list of phases, ticked in bulk

- **Status:** open
- **Seen:** 2026-09-03, live, three turns: thread `3261ae8f` (Task Board
  test 4), loop run `live-70`, thread `5cee5866` (Task Board test 5)
- **Costs:** the list is written before the work as generic phases —
  "create structure", "implement CSS", "verify and take screenshot" — and
  not as the request's own requirements (three columns, drag and drop,
  persistence, filter, responsive). It is then updated in bulk: five items
  marked completed in one call after the files are written, and "verify"
  marked completed after one look that exercised nothing. In test 4 it was
  never updated at all. The person sees a plan that says everything is done
  and reads nothing that was checked.
- **Reproduce:** any request with several requirements; compare the list to
  the request and the ticks to the tool calls.
- **Cause:** unknown. The 4.4 brief says what a list costs and when to use
  one; it says nothing about what an item is or what marks one done.
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Related:** ISS-0004, ISS-0008; roadmap 4.4 "known problems", 4.7

### ISS-0015 — a written page ends with a markdown fence

- **Status:** open
- **Seen:** 2026-09-03, live, `Task Board test 4/index.html` made through the
  loop against the deployed model
- **Costs:** the file ends with a literal ```` ``` ```` line after `</html>`,
  which the browser shows as text at the bottom of the page. The model read
  its own snapshot with `text: ```` ``` ```` in it and did not mention it.
- **Reproduce:** ask for a self-contained page; look at the last line.
- **Cause:** unknown. The same shape — a page that ends in a fence — is what
  the 2026-08-31 parser corruption was first measured on, so it may be the
  model closing a fence it never opened in the served format.
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Related:** ISS-0001, ISS-0008

### ISS-0014 — the page was looked at without storage or its own files

- **Status:** fixed 2026-09-03, deployed since
- **Seen:** 2026-09-03, live, twice: thread `afb9d76a` (an app with
  `styles.css` and `app.js`), and the loop re-run of "Task Board test 4"
- **Costs:** `inspect_page` opened the file as a `data:` URL. A `data:` page
  has no origin, so `localStorage` throws a SecurityError the app does not
  have in the person's browser, and a relative `styles.css` or `app.js`
  resolves to nothing, so a multi-file app is looked at unstyled and without
  its logic. The model was handed a false error and an unfair picture, and
  either ignored it or described what it saw.
- **Reproduce:** a page with `<link href="styles.css">` or a script using
  `localStorage`; inspect it on code before this fix.
- **Cause:** the document was passed as a URL instead of being served.
- **Fixed by:** the offline session serves the workspace at
  `http://artifact.local/` through request interception and fails everything
  else; the page has an origin, storage and its siblings, and a request the
  page makes elsewhere is reported as refused rather than as an error
  (`app/tools/chromium.py` `serve_directory`, `tests/test_browser_session.py`).
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Related:** ISS-0008; 4.5.5

### ISS-0013 — the repeat guard refused the call that would have worked

- **Status:** fixed 2026-09-03, deployed since
- **Seen:** 2026-09-03, deployed, thread `3261ae8f`, run `30fe463c`
- **Costs:** a look at a file failed twice because the file was not there,
  the model then wrote the file, and the third look — identical arguments, a
  file that now exists — was counted as the third identical failure. Every
  tool was halted for the turn, so the person got neither the screenshot nor
  the files they had asked for, after 261 s.
- **Reproduce:** make a call fail twice on a missing precondition, satisfy the
  precondition with another tool, repeat the call.
- **Cause:** `failed_before` counted identical failures across the whole turn,
  as if nothing could change between them.
- **Fixed by:** the count starts over when any tool has succeeded since the
  last identical failure (`app/agent/graph.py`,
  `tests/test_repeated_failure.py`).
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Related:** ISS-0012, which is what made the first two looks fail

### ISS-0012 — a corrupted path was obeyed, and a file nobody named was made

- **Status:** fixed 2026-09-03, deployed since
- **Seen:** 2026-09-03, deployed, thread `3261ae8f`, run `30fe463c`, twice
- **Costs:** `write_file` received the path `"Task Board test 4/index.html"<|"|>`
  — the served parser's leftovers around the real name — and created a file
  called exactly that on the person's volume. Every later call by the real
  name found nothing; the page was written four times; the junk file is still
  there beside the real one.
- **Reproduce:** call any path-taking tool with a path wrapped in quotes or
  carrying `<|`/`|>`.
- **Cause:** the fragment removal of 2026-08-31 recognises a fragment in a
  parameter name or as a `,name:` tail, not inside a string value; the
  filesystem accepted any characters the OS accepts.
- **Fixed by:** `resolve_in_root` refuses such a path as `bad_arguments` and
  asks for it again plainly (`app/tools/filesystem.py`, `tests/test_tools.py`).
- **Evidence:** `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Related:** ISS-0001, the upstream cause; ISS-0013

### ISS-0011 — every look at a page carried the whole page back as its address

- **Status:** fixed, 2026-09-03 — deployed the same day, `/check` 9/9 on that container
- **Seen:** 2026-09-03, deployed, thread `afb9d76a`, runs `8ffab1aa` and
  `240f09ea`
- **Costs:** `inspect_page` reported `url: location.href`, and for a local
  document that is the `data:` URL — the entire page, base64. A 7 KB page put
  about 9 KB of base64 into the model's context on every look, for nothing
  the model can read; the third call of run `8ffab1aa` was 17 764 input
  tokens. Stored history carries it too.
- **Reproduce:** `inspect_page` on any local file, on code before 4.5.5.
- **Cause:** the old evidence script returned the location of a data URL.
- **Fixed by:** roadmap 4.5.5, `page_report` in `app/tools/browser.py`, which
  reports no address for a local document. Fixed status once seen in the
  deployed profile.
- **Related:** 4.6a, which will have to shorten such results anyway

### ISS-0010 — "here is the screenshot", and nothing was sent

- **Status:** open
- **Seen:** 2026-09-03, deployed, thread `afb9d76a`, twice in one session
- **Costs:** asked "пришли скрин", the assistant calls `inspect_page`, sees
  the screenshot itself, and answers "Вот скриншот вашего приложения". The
  person receives text. Told "Я не получил изображение", it answers that it
  cannot attach an image to the chat and offers the workspace path instead.
  Only "скриншот отправь", the third request, produced a `send_file` and the
  picture. Both claims are false: `send_file` delivered that same PNG one turn
  later, and the brief says so in words ("a direct request to receive a
  screenshot or file is such a decision: perform the send_file call").
- **Reproduce:** make a page, then ask for a screenshot in one word.
- **Cause:** unknown. The 2026-08-29 note in `app/capabilities.py` records the
  same belief ("output supports only text") before the brief was written to
  contradict it, so the brief has not displaced it.
- **Also seen:** 2026-09-03, thread `3261ae8f`: "не могу напрямую отправить
  скриншот из системы", this time with every tool halted by ISS-0013 so no
  send was possible — the honest sentence was that the look had been refused.
- **Seen again:** 2026-09-06, live, GLM 5.3 Flash at Novita, run
  `42cebe2d531c4a1199b8b663c6f8832c`: `write_file`, `inspect_page`,
  `send_file` of `calculator.html`, and the answer "Калькулятор создан —
  calculator.html (в чате выше скриншот)". Only the HTML left; the
  screenshot the model saw in `inspect_page`'s result stayed in the
  workspace. The human's reading: the tools do not say plainly what leaves
  the workspace by default and what stays, so a model that has looked at
  a page takes the look for a delivery — the contract of roadmap item 11
  (what a tool returns, what it leaves and where), not a case to patch.
- **Evidence:** runs `8ffab1aa` (inspect, "вот скриншот", nothing outbound),
  `240f09ea` (same), `eda12665` ("не могу прикрепить"), `29c2bd17`
  (`send_file` of the PNG, delivered), 2026-09-03T04:06–04:09Z; `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Not seen since:** both runs predate the tool result naming the
  `send_file` call (2026-09-03, `handover`); since it, run `7673ce55` and
  the 2026-09-04 after-deploy G sent the screenshot unprompted. To be
  closed on evidence; `reports/2026-09-04_v2_observed_claims_review.md` §9.
- **Related:** ISS-0003 is the same shape for a file; 4.7 scenario suite is
  where a prompt change would be accepted

### ISS-0009 — the person reads an answer for a minute and then it is deleted

- **Status:** fixed in the tree, 2026-09-03 — text that comes with a tool call is delivered and kept; a draft a steering refuses is held on the screen; and the plan seam, the only live source of steerings, no longer objects by the human's decision, so the second generation it caused is gone with it. Deployed the same day; to be seen live
- **Seen:** 2026-08-31, live, four turns in a row
- **Costs:** text appears and grows while the work happens, the person reads
  it, and then it vanishes and a file arrives instead. What is said afterwards
  is almost nothing: 17 output tokens in one turn, **one** in another. The
  assistant has already explained itself in a message it then took back.
- **Reproduce:** ask for anything the model narrates before writing. The
  narration is streamed, previewed, and withdrawn when the same completion
  turns out to end in a tool call.
- **Cause:** known and half deliberate. A completion that carries both text and
  a tool call has its preview discarded — added on 2026-08-30 so that a
  narrated tool call would not become a second answer in the chat. That fix is
  right about the end state and wrong about the middle: the preview should not
  have been shown, and by the time we know it should not have been, the person
  has been reading it for up to 58 s. Nothing in a stream says in advance
  whether it will end in a tool call.
- **Also seen:** 2026-09-03, run `7673ce55`, after the sends: the answer
  was delivered with the screenshot attached, three sends followed, and the
  model closed with the same functional list again plus "файлы отправлены"
  (108 tokens) — not verbatim, so the display rule let it through. The
  core prompt's "add only what is new" is not obeyed; a second bubble with
  the same list is what the person sees.
- **Also seen:** 2026-09-03, thread `d88734a2`, run `af276ed7`, after the
  hold: the bare `todo_write` that followed the steering deleted the held
  draft through the adapter's no-text path (fixed the same day), and the
  model, told its answer was kept and to add nothing, wrote it again anyway
  (162 tokens). The vanish is fixed; the second generation needs a decision
  on the plan seam's objection.
- **Also seen:** 2026-09-03, thread `052869f2`, run `f41278c9`, the other
  way in: the answer (166 tokens) was refused as an ending by the plan seam
  because "verify" was still in progress, withdrawn from the chat, the model
  ticked the item and wrote the same answer again (164 tokens). The seam was
  right to object and wrong to cost a second generation: the draft is now
  held and handed back as the answer when the model adds nothing.
- **Also seen:** 2026-09-03, thread `46c6a9c3`, run `253ede5d`, with the
  cost now measured. The model wrote the whole answer (410 characters, 169
  output tokens) and attached a `send_file` to it; the preview was withdrawn;
  the send ran; the next model call produced the same 409 characters again
  as a new message, 134 output tokens and 3.8 s. The first copy is not lost
  to the model — it stays in the turn as the assistant message that carried
  the call and is read as input on every later call — but the person watched
  it vanish and then paid for it twice.
- **Evidence:** runs `94e8bd24` (preview at 15.9 s, 2,075 tokens, withdrawn,
  final answer 17 tokens) and `3e5690ae` (preview at 67.1 s, withdrawn, final
  answer 1 token), 2026-08-31T05:06–05:11Z; `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Also seen:** 2026-09-03, `scripts/loop_live.py --after-deploy` run `live-70`: the closing message was byte-identical to the text written beside `send_file`; in Telegram the adapter drops the verbatim repeat, so the person sees one. The runner prints it as a note, not a check.

### ISS-0008 — a generated app is delivered as working without ever being used

- **Status:** open
- **Seen:** 2026-08-31, live, "Personal Task Board 2" via Telegram
- **Costs:** the person receives an application described as ready, and the
  first thing they try does nothing. The task board saved new tasks and never
  drew them, because the render selector was `#todo.task-list` — one element
  with both an id and a class — where the list is a child of `#todo`.
- **Reproduce:** ask for an interactive page, open it, use the primary control.
- **Cause:** nothing in the loop exercises the artifact. `inspect_page` renders
  and looks; it does not click, type or read the console, so a defect that only
  appears on interaction cannot be seen by the only tool that looks. In this run
  the model did not call it at all, and had rewritten the same file twice.
- **Also seen:** 2026-08-31, run `cc98b3e0`. The request ended with the words
  "проверь что всё работает". Four files were written and nothing was opened,
  rendered or read back; the answer described the application as working. So
  the gap is not only that the loop cannot exercise an artifact — an explicit
  instruction to check did not produce a look either.
- **Also seen:** 2026-09-03, thread `afb9d76a`, "Task Board test 3": written
  in one call and described as done without a look (run `0bf67569`). Asked
  for a screenshot, the assistant first **rewrote the person's file** to seed
  it with sample tasks so the picture would show columns in use (run
  `8ffab1aa`) — an unasked change to the deliverable made for the sake of the
  evidence. The person reports the application itself works.
- **Evidence:** `reports/2026-08-31_v2_todo_live_failure.md`, section "Three
  live tests on a task big enough for a plan"
- **Related:** ISS-0004; roadmap 4.5.5. Since 2026-09-03 `BrowserSession`
  can click, type, press and select on a ref from its snapshot, and
  `inspect_page` returns that snapshot; no action is exposed to the model
  yet, so the defect stands as described.

### ISS-0007 — `tool_failed` carries no reason

- **Status:** fixed, 2026-09-03 — seen live the same day in `scripts/loop_live.py` E
- **Seen:** 2026-08-31, deployed telemetry
- **Costs:** nobody investigating a live failure can tell from the logs why a
  tool refused. Diagnosing ISS-0006 needed the volume and an offline
  reproduction to recover a message the process already had in hand.
- **Reproduce:** make any tool raise, then read the `tool_failed` event: tool,
  call index, stage, path, duration, and no error text.
- **Cause:** the event is emitted without the error the caller already holds.
- **Fixed by:** the typed outcome of roadmap 4.5. The executor records
  `code` and `message` on every `tool_failed`, and `tools/show_run.py` prints
  them under the call. `tests/test_tool_outcomes.py`.
- **Evidence:** run `e9bae9a5`, two `write_file` failures, 2026-08-31T04:14Z

### ISS-0006 — a path meant as a directory becomes a file, and poisons the folder

- **Status:** fixed, 2026-08-31 — offline only, not yet seen live
- **Seen:** 2026-08-31, live, "Personal Task Board 3" via Telegram
- **Costs:** two turns and about 155 s produced no folder. Every later write
  into that name failed, `list_files` on it failed, and the model gave up and
  scattered `index.html`, `app.js`, `styles.css` and `README.md` into the root
  of the person's workspace, where they still are.
- **Reproduce:**

  ```text
  write_file "Board 3/" "# Task board"   -> created Board 3/ (13 characters)
  Board 3 is now a file
  write_file "Board 3/index.html" ...    -> FileExistsError [WinError 183]
  list_files "Board 3"                   -> path 'Board 3' is not a directory
  ```

- **Cause:** `pathlib` drops a trailing separator, so `_write_file` in
  `app/tools/filesystem.py` treats `Board 3/` as an ordinary file name and
  creates it. There is nothing wrong with the model's call: a trailing slash is
  how everyone writes a directory.
- **Fixed by:** `write_file` refuses a path ending in a separator and says that
  directories are made for you, which is also now in the tool's description; an
  ancestor standing in the way is named instead of a platform error code. Seen
  twice more before the fix, in the only two live turns that opened a plan:
  runs `1763523c` and `3af91a0c`, 2026-08-31T05:27–05:30Z — a plan whose first
  item is "create the folder" produces exactly this call.
- **Evidence:** run `e9bae9a5` and run `28daa249`, 2026-08-31T04:14–04:17Z;
  offline reproduction as above; `tests/test_tools.py`
- **Related:** ISS-0005, which is why the model saw only an OS error and could
  not route around it; the repeat rule ended the first turn correctly at 52 s

### ISS-0005 — an OS error escapes the filesystem tools unwrapped

- **Status:** fixed, 2026-08-31 — offline only, not yet seen live
- **Seen:** 2026-08-31, live and offline
- **Costs:** the model is handed a raw platform error with a platform error
  code, instead of a sentence naming what it should do differently. It cannot
  act on it, and the wording differs by operating system.
- **Reproduce:** the ISS-0006 sequence raises `FileExistsError`, not `ToolError`.
- **Cause:** `resolve_in_root` wrapped `OSError`; the write, the `mkdir`, the
  read and the listing did not.
- **Fixed by:** wrapping them, in `app/tools/filesystem.py`. Since 2026-09-03
  every filesystem failure is an `fs.*` code with the `strerror` as detail,
  `write_file` is atomic like `edit_file`, and an exception that still escapes
  any tool becomes an `internal` result with the traceback in the log rather
  than a failed turn.
- **Related:** ISS-0006

### ISS-0004 — the assistant describes what it did not observe

- **Status:** open
- **Seen:** 2026-08-30, live
- **Costs:** the person is told about a page's contents that nobody looked at.
  Once the assistant reported a file it had never created, and only `send_file`
  failing revealed it.
- **Cause:** unknown. Three rounds of prompt wording made it better and worse in
  turn, which is evidence that wording is not the lever.
- **Also seen:** 2026-09-03, deployed, run `45f78d7e`, thread `4fd35f80`:
  a Hugging Face model page pasted as a URL was described in detail —
  parameters, tuning, what "uncensored" means, the author — with no
  `fetch_page` call at all (1 model call, 0 tools). Everything said was
  read off the address itself. The previous message in the same thread
  had fetched its page and used `offset` to read the rest unprompted.
- **Note:** 2026-09-03, code review — one sub-case is a fact about the
  turn rather than about wording: the person's message carries a URL and
  the turn made no tool call at all (run `45f78d7e`). The steering seam can
  refuse that ending once without reading the answer; `reports/2026-09-03_v2_whole_code_review.md` §2.11.
- **Evidence:** `reports/2026-08-30_v2_prompt_assembly.md`,
  `reports/2026-08-31_v2_todo_live_failure.md`
- **Related:** ISS-0008; roadmap step 4.5.5

### ISS-0003 — a made file is handed over as prose instead of sent

- **Status:** open
- **Seen:** 2026-08-30, live, twice in one session
- **Costs:** the person is given the literal text `[house.html](house.html)`
  and no file. A relative path is not a link the renderer will make clickable,
  so the delivery silently does not happen; the file arrives only when asked for
  by name.
- **Cause:** handing something over is `send_file`, and the model reaches for a
  link. Why it prefers the link is unknown.
- **Also seen:** 2026-09-03, deployed, thread `afb9d76a`, run `00e46d2a`:
  "и файл приложения" answered with the path `Task Board test 3/index.html`
  and "you can open it in any browser"; the file came only after "пришли
  файлы" (run `fb42cdb7`, `send_file`, delivered).
- **Also seen:** 2026-09-05, deployed, G with `set_goal` (runs
  `deployed-8947e3fb-70`, `deployed-cf8c3774-70`): the screenshot sent,
  the three files listed as paths under "Файлы приложения" with "приложен
  выше" — the goal the model itself wrote said "Прислать скриншот и файлы
  в чат"; and once the path of a picture offered as `![…](…)` beside a
  `send_file` of the same picture.
- **Also seen:** 2026-09-03, loop re-run `live-70`: asked for the screenshot
  and the files in one request, the model sent `index.html` with `send_file`
  and handed the screenshot over as `![Screenshot](.agent/browser/….png)` —
  a markdown image of a workspace path, which no interface renders. Then in
  Telegram, thread `5cee5866`, the same request: three files listed as paths,
  the screenshot as the same markdown image, nothing sent; both arrived only
  when asked again in two more turns. `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Mitigated:** 2026-09-03, the brief now says where the person is (the
  adapter declares `Delivery.place`), that they cannot see the workspace, and
  that a path, link or markdown image delivers nothing. First live turn on it
  (thread `46c6a9c3`): the screenshot was sent with `send_file` unprompted;
  the one file was still listed as a path and not sent. Second (thread
  `052869f2`): three files as paths and the markdown image again, no send at
  all. The brief does not hold; the status stays open.
- **Mitigated again:** 2026-09-03, every tool that leaves a workspace item
  now says in its result `to hand it to the person: send_file(path="…")`
  (`DECISIONS.md` 2026-09-03). If that does not hold live, the human has
  reopened an adapter delivery of a markdown image, on the condition that
  no delivery path blocks another.
- **Held, first turn:** run `7673ce55`, "Task Board test 6": the screenshot
  sent with the answer, then the three files, all unprompted, no markdown
  image. One turn is not a rate; the status stays mitigated.
- **Decided:** 2026-09-03, the human rejected a mechanical backstop in the
  adapter (delivering a markdown image the model wrote) as a crutch.
- **Also:** 2026-09-04, after-deploy run G (`live-70`): the files and the
  screenshot were sent by `send_file`, and the answer still carried a
  markdown image path beside them. The send is right; the wording is the
  4.9 question.
- **Evidence:** `reports/2026-08-30_v2_prompt_assembly.md`

### ISS-0002 — a picture someone sends is never kept

- **Status:** open
- **Seen:** 2026-08-30, verified against the deployed volume
- **Costs:** a document survives in the person's workspace; a photo, voice
  message or image is used inside that one turn and written nowhere, so `/new`
  loses it. The person reasonably believes what they sent is theirs to point at
  again. 22 entries on the volume, not one of them an image.
- **Cause:** the split lives in `admit_uploads` and is invisible to the person.

### ISS-0001 — the served tool parser loses what follows a long string argument

- **Status:** mitigated, 2026-08-31 — not fixed
- **Seen:** 2026-08-30 and 2026-08-31, live, three failed turns
- **Costs:** `write_file` arrived with `content` and no `path`, so nothing was
  written and the turn burned up to 264 s. The model then repeated the identical
  malformed call up to eight times.
- **Reproduce:** end a long `content` value with a stray markdown fence. The
  string's closing delimiter never arrives, the parser reads on to the next one
  — which is inside the following tool call — and the argument after it is
  swallowed. A four-variant GPU run cleared nesting, streaming and the planning
  tool of causing it.
- **Cause:** upstream, in vLLM's Gemma 4 tool parser; vLLM 51284 and 53431, both
  open, present in 0.26.0 and 0.27.1. Nothing constrains the emission on our
  side: tool schemas are advice, because the served model does not use guided
  decoding for tool calls.
- **What the mitigation is:** `write_file` forbids the fence in words; a call
  the parser mangled is cleaned before it reaches the conversation; an argument
  error now carries the tool's signature; and a call that failed twice
  identically is refused a third time. Live afterwards, the model recovered by
  itself after one refusal. A corrected parser exists and is tested offline in
  `tools/gemma4_parser.py`. Deploying it was rejected on 2026-09-03 as a fix for
  one model on one server; the runtime is instead made to survive any model's
  emission (`DECISIONS.md` 2026-09-03): since 2026-09-03 a call whose arguments
  are not a JSON object is delivered and refused as one `bad_arguments` result
  with the tool's signature, where until then the adapter raised and the whole
  request failed. Seen live 2026-09-03, thread `3261ae8f`: two calls missing
  `path` were each refused once with the signature and the turn went on; a
  third carried the leftovers inside the path itself and was obeyed
  (ISS-0012). The defect itself stays upstream and open. `reports/2026-09-03_v2_first_session_on_the_tool_system.md`
- **Also seen:** 2026-09-03, run `e54b442b`, "Task Board test 10": `content`
  first, ending in a fence, `path` lost, the identical call three times, the
  turn ended by the repeat guard after 142 s with the code pasted into the
  chat instead of written. The refusal now names the fence as the cause and
  says to send `path` first without it. Next live turn, run `9ec787bc` the
  same day: the same emission, refused once with that message, and the model
  sent the call again with `path` first and wrote the file — one 28 s call
  lost instead of the turn. The upstream defect stands; the corrected parser
  in `tools/gemma4_parser.py` is now to go out with the next model-app
  redeploy, once it has been checked first (the human, 2026-09-03, after
  test 10).
- **Evidence:** `reports/2026-08-31_v2_todo_live_failure.md`
