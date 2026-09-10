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

## 5. The second measured run, 2026-09-10: the seconds named, without Telegram

Redeployed with the fix, the mini set again on the human's word: 8/8,
runs `deployed-4b4c7f59-*`. `tools/run_named_seconds.py --user
loop-live-check --last 11`, seconds:

| run | total | model | tools | checkpoint_write | volume trips | rest named |
|---|---:|---:|---:|---:|---:|---:|
| -10 (A) | 11.4 | 10.5 | 0.0 | ×9 = 0.79 | — | graph_built 0.36 (cold), history 0.11, read ×2 0.10 |
| -20 (B) | 9.4 | 8.8 | 0.0 | ×13 = 0.89 | — | 0.25 |
| -40 (C) | 48.8 | 22.1 | 25.9 | ×21 = 1.97 | commit ×2 = 1.87, reload ×2 = 0.52; remote ×2 = 23.5 | 0.29 |
| -60 (F) | 24.1 | 22.1 | 1.5 | ×25 = 1.50 | — | 0.25 |
| -65 (W) | 10.6 | 9.1 | 1.0 | ×13 = 0.88 | — | 0.25 |
| -80/81/82 (H) | 10.9 / 4.9 / 11.3 | 10.2 / 4.5 / 10.8 | 0.1 / 0 / 0.1 | ×13 = 1.39 / ×9 = 0.70 / ×13 = 1.25 | — | 0.6 / 0.2 / 0.3 |
| -50 (E) | 14.5 | 14.0 | 0.0 | ×13 = 0.91 | — | 0.25 |
| -180/190 (M) | 31.5 / 12.5 | 30.8 / 12.0 | 0.1 / 0 | ×33 = 2.30 / ×19 = 1.36 | — | 0.3 / 0.25 |

Read with the timeline of C (`tools/show_run.py deployed-4b4c7f59-40`):

- **The checkpoint writes overlap the model call.** LangGraph writes them
  in its own tasks: the four `aput`/`aput_writes` of a step land 0.02–0.1 s
  after `model_started` and finish while the model is still answering. Their
  sum (0.7–2.3 s a turn, 9–33 writes) is not wall-clock cost, which is why
  the table's "unnamed" column goes negative: named seconds that ran under
  the model's. Only the tail after `persist` is serial: four writes of
  0.14–0.43 s each, 0.36 s from `persist_finished` to `turn_finished`.
- **A command pays its Volume trips on the clock:** commit before 0.76 and
  1.11 s, reload after 0.36 and 0.16 s — 1.1–1.3 s per command around the
  remote call, and the remote call's own overhead over the command's run
  (22.3 s for `venv` + `pip install tabulate` + the script, 1.26 s for a
  bare `python primes.py`) is inside `command_remote`.
- **Everything else is small and warm:** `graph_built` 22 ms (360 ms the
  first time in a container), `checkpoint_read` 16–24 ms, `context_loaded`
  14 ms on an empty history, `history_written` 90–110 ms, `persist` 90–250
  ms, `interjections_read` 0 ms (a memory lane here, not the inbox).
- **Turn start to first step: 0.12 s** (0.62 s in §4's run, on the colder
  container). Without Telegram the harness's own wall-clock is under a
  second a turn.

So the 2–17 s a one-step Telegram turn spends (§1) is not in what the
scenario path shares with it. What the Telegram path adds and the scenario
path never runs: the inbox claim and heartbeat, `delivered_before` and the
summary read (`turn_prepared`), the status message and the preview edits
(`telegram_call`), the inbox read for interjections on every batch (a fresh
connection each: 1.5 s where seen), the history load with its media
(ISS-0066), the fold notice and pending-calls check (`turn_closed`), and
outside the trace the inbox `complete`, the Volume commit for the turn and
the telemetry close. All of these are named now and none has been measured
named. The next number comes from one real Telegram turn with tools.

## 6. Two real Telegram turns, 2026-09-10 15:44 and 15:48 UTC, named

The human's own turns from their phone, same thread (history 2.5–3.3k
tokens, 7–8 results stubbed, so the media of ISS-0066 is in the load).
`tools/run_named_seconds.py 0e1146b2… 1d94a7af…` and the two timelines:

| | turn 1 `0e1146b2` | turn 2 `1d94a7af` |
|---|---:|---:|
| total from enqueue / model / tools | 32.1 / 14.4 / 7.3 | 50.5 / 28.1 / 3.9 |
| **queue wait** (enqueue → `turn_started`) | **6.69** | **5.67** |
| `turn_started` → first step | 1.57 | 3.76 |
| … `sendChatAction` first, serial | 0.30 | 0.48 |
| … `graph_built` | 0.36 | 0.80 |
| … `checkpoint_read` ×3 (delivered_before, the graph, the pending check) | 0.33 | 1.06 |
| … `turn_prepared` (summary read) | 0.11 | 0.45 |
| … `context_loaded` | 0.54 | 0.53 |
| per step boundary (status send/edit + checkpoint write + `interjections_read`) | 0.3–0.5 | 0.5–0.9 |
| … `interjections_read`, a fresh connection each | 0.11 ×2 | 0.49 ×3 |
| per command: `volume_commit` / `command_remote` / `volume_reload` | 0.87 / 6.16 / 0.24 | — |
| `telegram_call` ×n, total (mostly `sendChatAction` under the model) | ×12 = 1.82 | ×21 = 4.38 |
| tail: last `model_finished` → `turn_finished` | 1.09 | 4.13 |
| … `history_written` | 0.24 | 1.35 |
| … `checkpoint_write` ×4 after persist, serial-ish | 0.5 + 0.15 + 0.03 | 2.56 ∥ 2.56, 0.60, 0.15 |
| … `checkpoint_read` + `turn_closed` | 0.11 + 0.18 | 0.51 + 0.88 |
| after the trace: `telemetry_written` + `inbox_completed` + Volume commit | (log tail lost) | 0.71 + 0.53 + 0.007 |

Read:

- **Every second the person waits is named now.** Turn 1 outside the queue:
  25.4 s, of which model 14.4, tools 7.3, and the named harness ≈ 3.6 — the
  remainder is within the rounding of overlapping events. The queue wait is
  the one unnamed block left, and it is the largest single cost on both
  turns.
- **Both turns paid a cold worker.** 6.7 and 5.7 s from the enqueue to
  `turn_started`, four minutes apart: `process_telegram_update` scales to
  zero after 60 s, and every update spawns a worker (item 22), so a person
  who thinks between messages pays a container boot per message. Inside
  that boot the graph is built again (0.36–0.80 s) because the container is
  new. What of the 5.7–6.7 s is Modal's boot and what is our import and
  claim is not split in the log tail that survived; the earlier update
  (§4) showed the worker's first event 18 s after the enqueue on a cold
  image.
- **Postgres round trip decides the rest.** Turn 1's checkpoint reads take
  41–50 ms warm, turn 2's 155 ms; every store-bound event in turn 2 is 2–5×
  turn 1's (`graph_built` 0.80, `history_written` 1.35, `turn_closed`
  0.88, a checkpoint write up to 2.56 s). Same code, same database: the
  container landed farther from Neon. With 21 checkpoint writes and 4
  reads a turn, a 100 ms difference in round trip is 2–3 s on the serial
  path.
- **The serial parts of the store work, by count:** three checkpoint reads
  of the same tuple before the first step; four checkpoint writes after
  `persist` before the turn can end; one fresh inbox connection per tools
  batch for `interjections_read`; a summary read in `turn_prepared` and a
  second read of the store in `turn_closed` for the fold notice.
- **Telegram calls are small and serial where they matter:** the first
  `sendChatAction` runs before the graph is built (0.3–0.5 s in front of
  everything), then `sendMessage` / `editMessageText` / `deleteMessage`
  at 0.1–0.3 s each on every boundary; the typing indicator every 4 s
  runs under the model and costs nothing visible.

## 7. Candidates, from the table — not decisions

Each is a line for the human to approve or strike; none is built. Ordered by
the seconds on the person's clock.

1. **The cold worker per message: 5.7–6.7 s.** Split the boot first
   (Modal's start vs ours), then choose: a longer `scaledown_window` on
   `process_telegram_update` (idle CPU minutes for a warm second message),
   or a faster start (imports, the graph built once per container is
   already so — it is the container that is new).
2. **Three checkpoint reads before the first step → one: 0.3–1.0 s.**
   `delivered_before`, the graph's own `aget_tuple` and the pending check
   read the same tuple.
3. **The tail after `persist`: 1.1–4.1 s.** Four checkpoint writes the
   graph makes for its last node and its end, then a read and the fold
   notice's store read (`turn_closed`). Which of the four writes the turn
   needs before it can answer "done" is the question; the answer was
   already sent.
4. **`interjections_read` on a fresh connection: 0.1–0.5 s per batch.**
   Reuse the store's connection.
5. **The first `sendChatAction` in front of the graph build: 0.3–0.5 s.**
   Send it concurrently, not before.
6. **`volume_commit` before every command: 0.8–1.1 s.** Commit only when
   the workspace was written since the last commit.
7. **ISS-0066: `context_loaded` 0.53 s on a 3k-token history** that carries
   the bytes of every image ever seen.
8. **Container placement vs Neon: 40 vs 150 ms a round trip.** Not ours to
   remove, but a region pin on the App (Modal `region=`) would make turn 2
   look like turn 1; a cost and a platform choice, the human's.

## 8. Closed 2026-09-10: measured, and not worth the work

The human's decision, in their words: the report is closed, the Telegram
worker's cold start is to be thought about in the future, optimizing it
now does not matter, the item can be closed.

What the item established: every second of a turn is named now (§3, §5,
§6). On a normal turn the harness's own cost is 2–4 s spread in
half-seconds, half of it the database round trip that depends on where
the container lands; the one block a person feels is the cold worker per
message, 5.7–6.7 s, which is `scaledown_window=60` on
`process_telegram_update` — a platform choice, not a defect. The 12–18 s
of ISS-0056's first sighting were a 350 MB workspace's Volume commits on
a far container. §7's lines 2–8 stay here as measured and not built: each
is worth 0.3–1.5 s. Nothing was removed.
