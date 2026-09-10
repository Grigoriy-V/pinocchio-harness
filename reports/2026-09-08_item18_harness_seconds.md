# Item 18: the harness's own seconds

Started 2026-09-08 on the human's word ("перейти на 18"). ISS-0056 is the
defect: a deployed turn spends seconds between its own steps that no
model, tool or store accounts for, and the timeline does not name them.
The item has two halves: name them, then remove what is needless. This
report carries the first half and the measurement the second will start
from.

## 1. Where the seconds went, before anything was named

From the Modal log dump of 2026-09-07 13:37–15:57 UTC (`assistant-control`,
GLM through OpenRouter; scratchpad `logs_0908_full.txt`, `gaps.py`): the
gap between consecutive trace events of one run, grouped by the pair, for
the pairs where only the harness runs.

| from → to | n | median s | p90 s | max s | total s |
|---|---:|---:|---:|---:|---:|
| update_enqueued → turn_started | 14 | 4.69 | — | — | — |
| turn_started → loop_step 1 | 12 | 2.91 | 8.95 | 11.75 | — |
| model_finished → tool_started | 49 | 0.41 | 1.86 | 3.16 | 49.2 |
| model_finished → telegram_final_sent | 26 | 0.22 | 0.41 | 0.72 | 6.5 |
| telegram_final_sent → tool_started | 15 | 0.34 | 1.80 | 1.84 | 12.4 |
| tool_finished → loop_step | 57 | 0.41 | 1.57 | 4.90 | 50.8 |
| tool_finished → turn_interjected | 6 | 1.49 | 1.59 | 1.59 | 5.5 |
| persist_started → persist_finished | 10 | 0.88 | 4.87 | 4.87 | 13.9 |
| persist_finished → turn_finished | 10 | 1.76 | 6.84 | 6.84 | 27.4 |
| model_first_token → telegram_preview_started | 26 | 0.48 | 0.72 | 0.82 | 12.5 |

Per turn (total between `turn_started` and `turn_finished`; model and tools
from their own durations; the rest is the harness):

| run | total | model | tools | persist | harness | steps |
|---|---:|---:|---:|---:|---:|---:|
| ce5a716c | 424.1 | 79.6 | 301.6 | 3.1 | 39.8 | 8 |
| 54e4b103 | 271.0 | 84.6 | 131.8 | 4.9 | 49.7 | 10 |
| 8926b2a1 | 114.5 | 102.2 | 6.1 | 0.2 | 5.9 | 7 |
| 928c4a6b | 57.5 | 35.0 | 18.4 | 0.2 | 3.9 | 5 |
| 8fcb5941 | 36.4 | 20.7 | 7.1 | 0.9 | 7.8 | 3 |
| 2be1a0d9 | 35.5 | 27.8 | 0.0 | 0.8 | 6.9 | 3 |
| 4fb67ea0 | 32.7 | 13.7 | 0.0 | 2.0 | 17.0 | 1 |
| 668f9948 | 20.7 | 13.7 | 0.0 | 0.8 | 6.2 | 2 |
| 7abf7e9a | 14.1 | 8.2 | 0.0 | 0.9 | 5.0 | 1 |

(eb0620fe, the 31-minute turn of ISS-0064, left out: its harness time is
the hung persist.) On a one-step turn the harness is 5–17 s of 14–33; on
a ten-step turn 40–50 s. The shape: a fixed cost at the start (3–9 s
before the first step) and at the end (2–7 s after persist), and 0.4–1.9 s
per step boundary.

## 2. What runs there, by the code

- **Before the first step:** the worker claims the row (one new Postgres
  connection per inbox call), the adapter reads what the request already
  received (`delivered_before`: a checkpoint read), the store's summary,
  the graph is built for the thread (the checkpointer opened — a
  connection to Neon — and `context_limit` asked of the backend), then
  LangGraph reads the checkpoint and the `load` node reads the store for
  the context.
- **At each step boundary:** the Telegram calls (status message sent or
  edited, cleared before the answer; preview sent and edited at most once
  a second; the final edit), LangGraph's checkpoint write after every
  node (Postgres), and after a tools batch the interjections read (an
  inbox call, a fresh connection: 1.5 s where it was seen).
- **Around every command:** two Volume commits and a reload
  (`ModalRunner.run`, `run_command`), inside what the tool's duration
  already covers.
- **After persist:** the checkpoint write, the fold notice (a store read),
  the pending-calls check (a checkpoint read), the trace's flush and the
  run row (synchronous Postgres writes), then outside the trace the inbox
  `complete` (a fresh connection), the Volume commit for the turn, and
  `telemetry.close()`.

## 3. The first half, built 2026-09-08: the seconds named

One mechanism, not a signature change: the turn's trace is the **active
trace** of the task (`app/telemetry/trace.py` `set_active`, `active_trace`,
a `ContextVar` inherited by every task and thread the turn starts), set by
the adapter around the whole update. Anything that spends time says so
with `spent(kind, **data)`: one event with `duration_ms`, not a bracket.
Named now:

| event | where | what |
|---|---|---|
| `telegram_call` {method} | `TelegramClient._call` | every Bot API request |
| `checkpoint_read` / `checkpoint_write` {op} | `app/checkpoints.py` `timed_saver` | the saver's `aget_tuple`, `alist`, `aput`, `aput_writes` |
| `graph_built` | `Agent._graph` | the first build of a thread's graph in a container |
| `turn_prepared` | adapter `_answer` | `delivered_before` and the summary read |
| `context_loaded` | graph `load` node | the store reads for the context |
| `interjections_read` | graph tools node | the inbox read for mid-turn messages |
| `history_written` | graph `persist` | the store's append |
| `volume_commit` / `command_remote` / `volume_reload` | `ModalRunner.run` | the two Volume trips and the remote call around a command |
| `turn_closed` | adapter `_answer` | the fold notice and the pending-calls check |
| `telemetry_written` | `TurnTrace.finish` | the flush and the run row (log only: after the turn is closed) |
| `inbox_completed` | the worker | the inbox `complete` after the turn (log only) |
| `volume_reload_before_turn`, `volume_commit_after_turn`, `telemetry_closed` | `process_telegram_update` | the container's own work around the worker (log only) |

Offline: `tests/test_turn_telemetry.py::test_the_harness_names_its_own_seconds`.
`tools/show_run.py` prints the events like any other.

## 4. The first measured run, 2026-09-10: nothing named, and why

Deployed 2026-09-10 (the naming layer, then a redeploy for a new allowed
user). The mini set through `scenarios`, on the human's word: 8/8 passed,
runs `deployed-e725deec-*`. `tools/run_named_seconds.py --last 14`:

| run | total | model | tools | named | unnamed |
|---|---:|---:|---:|---:|---:|
| e725deec-10 (A) | 10.0 | 6.2 | 0.0 | 0.7 | 3.2 |
| e725deec-20 (B) | 13.3 | 10.7 | 0.0 | 0.6 | 1.9 |
| e725deec-40 (C) | 86.6 | 31.6 | 51.3 | 1.1 | 2.5 |
| e725deec-60 (F) | 34.8 | 26.4 | 5.3 | 0.7 | 2.5 |
| e725deec-65 (W) | 14.4 | 10.2 | 1.6 | 0.6 | 2.0 |
| e725deec-80/81/82 (H) | 12.4 / 7.8 / 16.9 | 9.2 / 4.5 / 13.6 | 0.4 / 0 / 0.7 | 0.7 / 1.5 / 0.6 | 2.1 / 1.9 / 1.9 |
| e725deec-50 (E) | 15.5 | 12.9 | 0.0 | 0.7 | 2.0 |
| e725deec-180/190 (M) | 40.2 / 14.2 | 36.5 / 11.5 | 0 / 0 | 0.9 / 0.7 | 2.8 / 2.1 |

"Named" is `persist_finished` alone: **not one `spent` event reached the
store.** Cause, by the code: `set_active` was called only by
`TelegramAdapter.handle_update`; `scenarios` runs `scripts/loop_live.py`'s
`Turn`, which starts its trace through `Telemetry.start` and never passes
the adapter, so `spent` found `NO_TRACE` and dropped every event. Fixed the
same day: `TurnTrace.start` makes the trace the active one and
`TurnTrace.finish` clears it, whoever started it
(`tests/test_turn_telemetry.py::test_a_started_trace_is_the_active_one_without_an_interface`).
The adapter's own set/reset stays for what it does before the trace starts.

What the scenario runs show anyway: deployed and without Telegram, the
unnamed remainder is 1.9–3.2 s per turn whatever its length (a fixed cost:
turn start to first step 0.6 s, persist 0.65 s, persist to finish 1.3 s),
against 2.1 / 63.1 / 9.3 s on the three real Telegram turns of thread
`dee4ba72` (2026-09-08, before the naming layer). The difference is what
runs only on the Telegram path: the status and preview calls, the checkpoint
writes and the inbox reads between steps, and the history load with its
media (ISS-0066).

One real update was seen in the log (814913269, 13:00 UTC): the webhook's
POST took 13.3 s of which 3.8 s was execution — a 9.5 s cold start of the
webhook container after the deploy — and the worker's first event came
18.3 s after the enqueue. It was then completed in 1.2 s with no turn, so
it was a command or a non-turn update; the queue wait on a cold image is
the number to keep.

## 5. Next: measure, then remove

Deploy (gate), then the mini set and one real Telegram turn with tools
(gate: a model call each), then `gaps2.py` over the log dump: per turn the
named seconds by kind against the unnamed rest. What is needless is
decided from that table, item by item, and each removal is its own line
here before it is built.
