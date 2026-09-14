# Turn telemetry (item 3A) — preparation

**Date:** 2026-08-29
**Task input:** `reports/archive/baseline_measurement_metrics_logs.md`
**Roadmap item:** queue 3, first bullet ("application telemetry first").

Nothing here is implemented. This is the design proposed before the
implementation gate, together with what it costs and what it deliberately leaves
out. The four choices in "Decisions taken in this session" were answered by the
human in words; everything else in this report is a proposal.

## What exists today

The deployed path already has clean boundaries and no observation of them:

```text
Telegram update → telegram_webhook (CPU) → telegram_updates (Neon)
    → _spawn(update_id) → process_telegram_update (separate CPU container)
    → TelegramAdapter.handle_update → GeneralHarness.decide (model call)
    → answer route: Agent.events → graph load/model/tools/persist
    → act route:    TaskRuntime  → plan/implement/validate/evaluate
    → Telegram preview and final delivery
```

Facts that shape the design:

- **The only thing crossing the process boundary is `update_id`.** The worker
  runs in a different container from the webhook, so anything the ingress knows
  must be written down or passed as an argument.
- **The router is a full model call outside the graph.** `GeneralHarness.decide`
  calls `backend.invoke` directly, and the act route makes several more model
  calls inside `task_worker.py` and `task_validator.py`. A turn's token count
  that only counts the graph's calls is wrong, and wrong in the direction that
  makes the harness look cheaper than it is.
- **The graph is compiled per thread and cached** (`Agent._graphs`), so a
  per-turn object cannot be closed over at build time. Per-turn identity has to
  travel with the invocation, not with the compilation.
- **`Toolbox.run_async` is called from one place** — the `tools` node in
  `app/agent/graph.py`. One instrumentation point covers every conversational
  tool call without touching any tool or the `Toolbox` contract.
- **Store latency here is dominated by distance to Neon**, measured in
  `reports/2026-08-28_v2_control_plane_database_latency_probe.md`. Telemetry
  that opens a connection per event would be paid for out of the person's wait.
- **Two profiles, two transports.** `ui/telegram/run.py` polls locally with no
  inbox and no webhook; the deployed profile has both. Whatever creates a turn
  record must work in both without the local profile growing a PostgreSQL
  dependency.

## Decisions taken in this session

The human chose all four:

1. **3A delivers the lifecycle *and* real counters.** `turn_runs` must carry
   truthful `model_calls`, `tool_calls`, `input_tokens`, `output_tokens` and
   `first_model_token_ms`. That formally reaches into 3B.1 and 3B.2; the
   alternative was a summary row whose measurable fields are all `NULL`.
2. **`run_id` is a column on `telegram_updates`.** Generated at the webhook and
   written by the `INSERT` that already happens, so the hot path gains no round
   trip, and one `update_id` maps to exactly one `run_id` however many times
   Telegram redelivers or a spawn is retried. This needs an additive migration
   on a populated deployed table — a human gate at deploy time.
3. **One row at the start, events batched afterwards** — amended below after the
   human questioned what a single end-of-turn batch would cost. The `turn_runs`
   row is written when the worker claims the update; `trace_events` are buffered
   and written in batches rather than one connection per event.
4. **LangGraph config carries `run_id`; the sink is injected explicitly.** No
   `contextvars`. A turn's identity rides in `configurable` beside `thread_id`,
   and the telemetry store is constructor-injected like `store` and `backend`.

## Proposed design

### Identity

`run_id` is a UUID4 string generated at ingress — the webhook for the deployed
profile, `PollingBot._guarded` for the local one — and derived from nothing.
`thread_id`, `user_id` and `update_id` are recorded as metadata on the row.

`PostgresUpdateInbox` gains a `run_id TEXT` column, set on `enqueue` and
returned by `claim`, so `InboxJob` carries it into the worker. The webhook also
records `enqueued_at`; the worker uses that value as the turn's `started_at`, so
the worker's own cold start (measured at about 4.9 s) lands *inside* the
measured turn instead of disappearing before it.

### Storage

A new `app/telemetry/` package, deliberately not part of `ConversationStore`:

```text
base.py       TelemetryStore contract, TurnRun, TraceEvent, Outcome
sqlite.py     local profile
postgres.py   deployed profile, its own tables and its own version row
open.py       open_telemetry(settings), mirroring app/memory/open.py
turn.py       TurnTrace — the per-turn recorder handed to the application
```

Tables and indexes as the task document specifies (`turn_runs`,
`trace_events`). The deployed tables live in the same Neon database and the same
schema as the conversation store, with their own schema-version row; the
migration is added to `tools/setup_control_plane.py`, which is already the owner
of deployed migrations.

`TurnTrace` is the only object the application sees. It buffers events, holds
the counters, and exposes `event(type, **data)`, `model_call(...)`,
`tool_call(...)` and `finish(outcome)`. **Every method swallows its own
failures.** Telemetry that can fail a turn is worse than no telemetry, and this
is the same rule the answer preview already follows.

### When a measurement is taken, and when it is written

These are separate, and conflating them is what made the first version of this
report look worse than the design is. An event's timestamp and its `duration_ms`
are taken **at the moment the step happens** — wall clock for the timestamp,
`time.monotonic()` for the duration, `seq` for the order. Buffering moves when
the rows travel to the database and changes no measured value: per-tool
durations, TTFT and the gap between the router and the first token are the same
whatever the write strategy is.

What a single end-of-turn batch would genuinely cost is two other things: a turn
killed hard — container OOM, timeout, expired lease — loses its whole trace,
which is exactly the class of turn item 3 exists for; and a running turn is
invisible until it ends, which for a minutes-long task is most of the time it
matters.

So the write strategy is two layers, as the task document's "Logging" section
describes:

- **Every event is written immediately as one structured log line** carrying
  `run_id`, the event name, the timestamp and its metadata. It costs nothing,
  blocks nothing, survives the container's death and is readable in Modal's log
  view while the turn is still running. The local profile, which has no
  dashboard at all, gets the same lines on its terminal.
- **The database is written in bounded batches**: a flush when the buffer
  reaches about 25 events, and a flush with the finalizing update. An ordinary
  conversational turn is still one write; a long task flushes a few times on the
  way and does not lose everything if it dies.

The log is the fast operational view, the database is the durable record, and
they share one `run_id`. The database is not optional in favour of the log: only
it survives long enough to answer "every failed turn this week" and to compare a
changed agent loop against this baseline, and only it exists in the local
profile in queryable form. The log line is the insurance, not a second source of
truth.

### Where events come from

| Event | Emitted at |
|---|---|
| `update_enqueued` | `TelegramWebhook._admit` (recorded as ingress time, written later) |
| `turn_started`, `inbox_claimed` | `TelegramUpdateWorker.run` after a successful claim |
| `router_started/finished/failed` | `GeneralHarness.decide` |
| `model_started/first_token/finished/failed` | `complete()` in `app/agent/graph.py`; the same helper wrapped around the task path's `invoke` calls |
| `tool_started/finished/failed` | the `tools` node, around `toolbox.run_async` |
| `approval_requested` | `TelegramAdapter._ask_pending_calls` |
| `persist_finished` | the `persist` node |
| `telegram_preview_started`, `telegram_final_sent` | `AnswerPreview._send` and `_deliver` |
| `turn_finished` / `turn_failed` | `TelegramUpdateWorker.run`, in a `finally` |

`first_visible_ms` comes from `telegram_preview_started` when there was a
preview and from `telegram_final_sent` when the answer arrived whole, so a short
answer is not reported as never having become visible.

### Plumbing

`Agent.events`/`resume_events` take an optional `trace`; `_run` puts
`run_id` into `configurable` beside `thread_id`. Graph nodes read it from their
`config` and record through the sink the `Agent` was built with. `TaskRuntime`
does the same for the act route, which is what makes the task path's model calls
countable. `GeneralHarness` receives the trace on the methods that already run
per turn (`decide`, `start_task`, `resume_task`). `Toolbox` and the tools
themselves are untouched.

### Outcome

`turn_runs.outcome` is set by the adapter, which is the only layer that knows
what the person actually received:

```text
answer_delivered        an assistant message reached the chat
approval_requested      the turn stopped on a pending call
task_result_delivered   the act route delivered its result message
cancelled               /stop
failed                  anything that reached the worker's exception path
```

`status` stays operational (`running`/`completed`/`failed`/`cancelled`).

### What does not get a row

Commands answered without a model — `/new`, `/chats`, `/can`, `/check`, `/help`
— get a `run_id` at ingress but no `turn_runs` row. They cost nothing, take
milliseconds, and would outnumber the turns worth measuring. This is a proposal
and easy to reverse; say so if you want every admitted update in the table.

### Privacy

Timings, counts and technical metadata only. No message text, no attachment
bytes, no prompts, no tool results, no deltas. `user_id` is the canonical UUID5
the adapter already derives from the Telegram account, not the Telegram id
itself. Tool events carry the tool name and status, never arguments — the task
document allows sanitized arguments later, and later is the right time for it.

### Configuration

`AGENT_TELEMETRY` (default on), same shape and reasoning as
`AGENT_STREAM_ANSWERS`: the way to turn an observation layer off is a redeployed
setting, not a reverted release. Storage follows the existing profile split —
SQLite file locally, `AGENT_DATABASE_URL` when set — with no new credential.

## Cost and risk

- **Per turn:** two Neon round trips for an ordinary answer (one `INSERT` at
  claim, one batched `INSERT` + `UPDATE` at finish), a few more for a long task
  that crosses the buffer threshold. Both ends are outside the person's visible
  wait — the first before the model is called, the second after delivery.
- **The visible risk is a slow flush at the end of a turn**, which delays only
  the worker's exit, not the answer. Bounded by making the flush best-effort.
- **A hard-killed turn** keeps its per-event detail in the structured log and,
  in the database, whatever crossed the last flush plus a `running` row that
  never finished — which is itself the finding.
- **The migration touches a populated deployed table.** `ADD COLUMN ... NULL` on
  `telegram_updates`, which holds queue rows and no conversation. Existing rows
  get `NULL` and are handled as "no run id", not as an error.
- **Not measured yet:** the actual cost of the two writes from a Modal worker to
  Neon. The existing latency probe measured the conversation store's operations
  from the same containers, so the order of magnitude is known; the exact number
  belongs to the implementation's own evidence, not to a separate live run.

## Acceptance for this step

Offline: one turn creates exactly one `turn_runs` row; events keep their
`run_id` and a deterministic order; a duplicate update does not create a second
run; both store implementations satisfy one contract suite; a failed turn still
finalizes its row; the streaming path records exactly one `model_first_token`;
token counts aggregate over router, graph and task calls; telemetry contains no
message text; every event produces one structured log line carrying its
`run_id`, and a trace whose flush never happens is still complete in the log; a
telemetry store that raises on every call does not fail a turn.

Live, and only after a separately authorized deploy: one real Telegram turn
whose `run_id` is visible from ingress through delivery, with truthful counts.

## Gates ahead

1. **Implementation start.** Approval of this preparation is not it; the human
   says the implementation may begin.
2. **The additive migration on the deployed database**, run through
   `tools/setup_control_plane.py`.
3. **The deploy** of `assistant-control`, and any live turn used as evidence.

3B's remaining pieces (the `show run <run_id>` inspector, approval and
persistence detail beyond what is listed above) and all of 3C (the vLLM metrics
probe and the controlled prefill/decode/prefix-cache baseline, every run of
which wakes a GPU) stay outside this step.
