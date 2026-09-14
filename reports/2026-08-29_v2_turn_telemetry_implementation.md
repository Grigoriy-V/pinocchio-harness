# Turn telemetry (item 3A) — implementation and offline evidence

**Date:** 2026-08-29
**Task input:** `reports/archive/baseline_measurement_metrics_logs.md`
**Preparation and approved decisions:** `reports/2026-08-29_v2_turn_telemetry_preparation.md`
**Roadmap item:** queue 3, first bullet.

Not deployed, not migrated and not accepted live. Everything below was proven
offline.

## What changed and why

The worker emitted nothing. A turn's shape existed only as Modal log lines that
the local profile does not have, and the one question item 3 exists to answer —
*where did this turn's time, model calls and tool calls go* — could not be asked
at all. The failed live PDF task reported that twenty tool calls were spent and
nothing about what they were.

- **A turn has one identity, generated where the person's message arrives.**
  The webhook creates a `run_id` and writes it with the insert that already
  happens, so the hot path gains no round trip. One `update_id` maps to exactly
  one `run_id` however many times Telegram redelivers or a spawn is retried,
  because the stored value wins over a freshly generated one. The inbox also
  reports how long the update waited, so the queue and the worker's cold start
  land *inside* the measured turn instead of before it.
- **Telemetry is its own contract, not a corner of the conversation store.**
  `app/telemetry/` holds `TurnRun`, `TraceEvent`, `TelemetryStore` and two
  implementations, SQLite locally and PostgreSQL deployed, both answering one
  contract suite. The deployed tables sit in the same database as the
  conversations and share nothing else, including their version row.
- **Two write layers, because they fail differently.** Every event is a
  structured log line immediately — free, non-blocking, survives the container's
  death, readable while the turn runs. The database gets bounded batches: one
  row at claim, a flush every ~25 events, and a flush with the finalizing
  update. An ordinary answer is two round trips; a long task flushes on the way
  and does not lose everything if it dies. A measurement is taken when the step
  happens, so buffering changes no measured value.
- **The identity travels as a string; the recorder is looked up.** `run_id`
  rides in LangGraph's `configurable` beside `thread_id` and the live recorder
  is found through `Telemetry.trace()`. The graph is compiled once per thread
  and reused, so capturing a per-turn object at build time was never available —
  and a recorder in configuration would be a live object in checkpoint metadata.
- **Every model call is counted, including the ones nobody sees.** The router is
  a full request before the answer; the bounded task path spends several more.
  The router is bracketed in the harness, the graph's call in the node, and the
  task path through a `TracedBackend` wrapper, because threading a recorder
  through the planner, implementer and validator protocols would have changed
  three signatures and every fake for a measurement available at the one place
  all of those calls pass through.
- **Tool calls are bracketed at the single execution boundary** — the `tools`
  node — so no tool and not the `Toolbox` contract changed. A failure is
  recognised through the toolbox's own `tool_failed`, not a string literal in
  the graph. A declined destructive call is recorded and deliberately not
  counted as a tool call the turn spent: it never ran.
- **Outcome is decided by the only layer that knows what the person got.** The
  adapter sets `answer_delivered`, `approval_requested`, `task_result_delivered`
  or `cancelled`; the worker closes anything left open as `failed`, which is
  what a crash looks like from outside.
- **`AGENT_TELEMETRY`** turns the whole thing off in configuration, and
  `NO_TRACE` keeps every unmeasured path — Chainlit, the tests, free commands —
  working unchanged.

Two smaller decisions worth naming. Free commands (`/new`, `/chats`, `/can`,
`/check`, the conversation buttons) get a `run_id` but no row: they cost nothing
and would outnumber the turns worth measuring. `/stop` is the exception and is
measured, because it is the one free command that ends something expensive —
without it the `cancelled` outcome could never be recorded at all.

## What it does not do

- **No inspector.** `show run <run_id>` and listing recent failed runs are the
  reading half of item 3 and are not here. Querying two tables is the current
  interface.
- **No GPU time and no derived cost.** `gpu_active_ms` and `derived_cost_usd`
  are deliberately not columns yet: they belong to 3C, and adding empty columns
  ahead of the work that fills them is scaffolding.
- **Task stage detail is coarse.** An act turn's model calls and tokens are
  truthful, and its own tool-call count is folded in at the end, but per-stage
  model attribution inside the task graph is not separated.
- **Privacy is enforced by construction, not by a filter.** Nothing writes text
  into telemetry, and a test asserts that no part of a real turn's question,
  answer, tool result or Telegram account id appears in what was stored.

## Checks

Offline suite: **721 passed, 1 skipped** (`.venv\Scripts\python.exe -m pytest
tests/ -q`), up from 698 — 23 new tests, none removed.

- **Store contract** (`tests/test_telemetry_store.py`, new, parameterised the
  same way the conversation store is): a started turn readable before it
  finishes; finishing rewriting the same row with its counts; events returned in
  `seq` order rather than timestamp order; data and durations preserved; events
  belonging to their own run; a batch written twice not duplicating the trace;
  an empty batch writing nothing. The PostgreSQL implementation joins this suite
  when `AGENT_TEST_DATABASE_URL` is set, and did not run here.
- **A measured turn end to end** (`tests/test_turn_telemetry.py`, new, driven
  through the real webhook, worker and adapter): one turn is one row carrying
  its user, thread, source, route and both model calls; the ingress identity is
  the one every event records; a redelivered update stays one turn; a streamed
  answer records exactly one `model_first_token` and the router records none;
  tokens add up across every model call; an executed tool has one start and one
  terminal event; a failing tool is not recorded as a success; a turn that stops
  for approval is successful rather than failed; a failed turn closes its own
  row with the error type and keeps the events gathered before the failure;
  `/stop` is `cancelled` and reaches no model; a free command is not measured at
  all; a telemetry store that raises on every call does not fail the turn; no
  conversation content reaches telemetry.
- **Tool failure coupling** (`tests/test_tools.py`): every way a call can fail —
  raised `ToolError`, an OS error, invalid arguments, an unknown tool — is
  recognisable through `tool_failed`, so a failing tool cannot start being
  counted as a successful one when a message is reworded.
- **Existing suites unchanged in meaning.** Three fakes gained the new
  parameters (`FakeInbox.enqueue`/`claim`, the worker's `Handler`, the harness's
  `StubTasks.start`) and `tests/test_control_plane.py` gained the telemetry
  migration to its expected order. No assertion was weakened.

One real regression was found by the suite and fixed: while adding the approval
events to the tools node I dropped the line that applies the user's answers, so
a declined destructive call would have run anyway.
`tests/test_confirmation.py` caught it.

Not run: the PostgreSQL contract suites, offline by design here.

## Files

New: `app/telemetry/__init__.py`, `base.py`, `sqlite.py`, `postgres.py`,
`trace.py`, `open.py`, `backend.py`; `tests/test_telemetry_store.py`,
`tests/test_turn_telemetry.py`.

Changed: `app/config.py`, `app/agent/graph.py`, `app/agent/runtime.py`,
`app/agent/harness.py`, `app/agent/task_runtime.py`, `app/tools/base.py`,
`app/tools/__init__.py`, `ui/telegram/inbox.py`, `ui/telegram/webhook.py`,
`ui/telegram/adapter.py`, `ui/telegram/run.py`, `ui/telegram/wire.py`,
`deploy/modal/control_app.py`, `tools/setup_control_plane.py`,
`tests/test_telegram_webhook.py`, `tests/test_general_harness.py`,
`tests/test_control_plane.py`, `tests/test_tools.py`, `docs/CODEMAP.md`,
`docs/PROJECT_MAP.md`, `docs/OPERATIONS_MAP.md`, `ROADMAP.md`.

`.env.example` was not updated: reading it was refused in this session, as in
the previous one. Both new settings default to a working configuration and are
documented in `docs/OPERATIONS_MAP.md`.

`docs/PRODUCT.md` is unchanged. Telemetry changes nothing a user sees.

## Migrated and deployed, 2026-08-29

Both gates were authorized by the human and crossed the same day.

The migration ran through `tools/setup_control_plane.py` against the deployed
Neon database. Afterwards the schema holds `telemetry_version` at 1, `turn_runs`
and `trace_events`, and `telegram_updates` has gained `run_id` as its last
column. Nothing existing was touched: 81 queue rows and 5 conversations are
still there, and `turn_runs` is empty because no turn has run yet.

`assistant-control` deployed in 8.3 s; five functions re-created
(`render_web_page`, `measure_database_latency`, `telegram_webhook`,
`process_telegram_update`, `self_test`). The first attempt failed on a Windows
console encoding error printing Modal's own tick character, not on anything in
this repository; re-running with `PYTHONIOENCODING=utf-8` succeeded.

**No deployed function was invoked and no live turn was run.** Telemetry in the
deployment is therefore live and untried: the first real Telegram message will
be the first row in `turn_runs`.

## Accepted live, 2026-08-29

The human sent four messages through the real chat. All four were measured, and
the rows were read back out of the deployed database rather than inferred from
the chat.

Every acceptance criterion 3A owns is met live: one `run_id` per turn from
ingress to delivery; events in order — `turn_started`, router, model,
`telegram_preview_started`, `telegram_final_sent`, persistence, `turn_finished`;
token counts covering both model calls; model TTFT and first visible response
separately measurable; and no message text, attachment or prompt anywhere in
what was stored.

| run | queued | router | TTFT from start | first visible | total | in/out |
|---|---|---|---|---|---|---|
| 9b861f1e | 6466 ms | 3963 ms | 14279 ms | 14605 ms | 15771 ms | 7388/28 |
| a7b50f07 | 5272 ms | 609 ms | 6196 ms | 6526 ms | 14151 ms | 7440/115 |
| 31deb3c3 | 241 ms | 966 ms | 1958 ms | 2245 ms | 9356 ms | 4864/342 |
| fcd7dd26 | 908 ms | 963 ms | 3736 ms | 4088 ms | 8562 ms | 5542/185 |

Three findings the baseline exists to produce:

- **Routing costs about a third of a turn's input tokens** — 2452 against 4936,
  2478 against 4962, 1190 against 3674, 1529 against 4013. Queue item 5's
  single-call change now has a measured target instead of an assumption.
- **The provider's own TTFT is small**: 2293, 140, 586 and 355 ms per call.
  Everything else before the first visible word is queue wait, worker cold start
  and the router. Decode-side optimization is not where this product's latency
  is.
- **One persistence outlier**: 5636 ms against 36–643 ms elsewhere. A single
  slow write inside a turn, previously invisible.

`reports/ml_work.jsonl` holds the same numbers as a measured record.

## A capability failure the same session exposed

Unrelated to telemetry, and found *by* it. Asked to send a screenshot of a PDF
in the workspace, the assistant refused twice — "я текстовая модель", "нет
технической возможности делать скриншоты" — and the trace shows **zero tool
calls**, so it never tried.

The wiring is correct. The generated brief names `view_pages` (renders a PDF
page to PNG in the workspace and returns the path) and `send_file`, and says in
as many words: *a direct request to receive a screenshot or file is such a
decision: perform the send_file call instead of only saying that you can*. The
model broke exactly the rule written against that behaviour, and omitted both
tools when it listed its own. It also read "скриншот" as screen capture rather
than a rendered page.

This is instruction adherence in a 12B model, not a wiring defect, so it belongs
to the agent-loop work in queue item 4 rather than to a one-line fix. The human
decided not to act on it now; it is recorded here so the next session does not
rediscover it. A cheap partial idea if it recurs: give the brief the recipe
(`view_pages` then `send_file`) rather than the rule, and map the word
"screenshot" onto rendering a page.

## Gates ahead

None for 3A; it is closed. The rest of item 3 — the `show run <run_id>`
inspector, finer task-stage detail, and 3C's vLLM prefill/decode/prefix-cache
baseline, every run of which wakes a GPU — is not approved work yet.

The rest of item 3 — the `show run <run_id>` inspector, finer task-stage detail,
and all of 3C's vLLM prefill/decode/prefix-cache baseline — is not approved
work yet.

Acceptance criteria 1 to 6 of the task document are met offline and need one
live turn to be met live. Criteria 7 to 9 belong to 3C and criterion 5's
inspector belongs to the rest of 3B.
