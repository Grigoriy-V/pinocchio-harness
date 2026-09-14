# Inspectable agent trace (item 3B) — implementation and offline evidence

**Date:** 2026-08-29
**Task input:** `reports/archive/baseline_measurement_metrics_logs.md`
**Preparation and approved decisions:**
`reports/2026-08-29_v2_run_inspector_and_gpu_baseline_preparation.md`
**Builds on:** `reports/2026-08-29_v2_turn_telemetry_implementation.md` (3A)
**Roadmap item:** queue 3, second bullet.

Not deployed and not accepted live. Everything below was proven offline.

## What changed and why

3A recorded turns and could not read them back, and it left the most expensive
path in the product almost blind: the bounded task path executed its tools in a
loop of its own that emitted nothing, so the failed live PDF task would still
have reported twenty tool calls and no explanation.

- **The trace has a reading half.** `TelemetryStore.recent_runs` lists the
  newest turns, optionally one person's, optionally only the unsuccessful ones —
  and *unsuccessful* deliberately means *the outcome was a failure, or the turn
  never finished at all*. A container that disappears leaves `running` forever
  because nothing survived to close the row, and filtering on `status='failed'`
  would show only the failures the application lived through, which are the ones
  that were already least mysterious. The predicate is shared by both stores so
  the local and deployed answers cannot drift apart.
- **`tools/show_run.py` renders one turn or a list.** It opens what the
  application would open, so the local profile reads its SQLite file and
  `AGENT_DATABASE_URL` reads the deployed database. Read-only: no migration and
  nothing started. The rendering lives in `app/telemetry/inspect.py` so it is
  testable, and it is not imported by `app/telemetry/__init__.py` — the webhook's
  cold path stays as short as 3A left it.
- **A rendered run shows the queue wait first.** In 3A's own live baseline the
  wait between the person pressing send and a worker claiming the update ran from
  241 ms to 6.5 s, often the largest single part of the wait. Every event offset
  in the timeline is shifted by it, so the timeline agrees with `first_visible_ms`
  and `total_ms` rather than contradicting them.
- **Time nobody measured is named.** `unattributed` is the total minus queue
  wait, model time and tool time. A single 5.6 s persistence outlier was found
  once by noticing exactly that kind of remainder; now it has a line of its own.
- **An unfinished run is labelled, not rendered as if it ended**, with the last
  event it managed to write.
- **Every stage of a task says what it spent.** `TurnTrace.staged()` sets a
  context that every event emitted inside it carries — the stage and the attempt
  number — so a model call, a tool call and a nested step all say which stage
  spent them. One mechanism rather than two: the model events did not need a
  second `purpose` vocabulary because the stage already answers the question.
- **The task path's own tool loop is bracketed at last.** `toolbox.run(call)` in
  the implementer and `toolbox.run_async(call)` in the validator are both inside
  `trace.tool(...)`, with the toolbox's own `tool_failed` deciding the terminal
  event. The automatic directory listing is counted, because the budget counts
  it.
- **Calls that never ran are visible and never counted.** A path repeating the
  grant directory is `rejected`, a destructive call under a read-only grant is
  `not_granted`, and calls past the budget are `tool_skipped` /
  `budget_exhausted`. None of them is a tool call the turn spent, and all of them
  are the explanation a task that stopped for no visible reason previously
  failed to give.
- **The path is recorded and nothing else is.** The difference between an agent
  working through a directory and an agent rewriting one file twenty times is
  exactly this field. No other argument value is stored, because `write_file`'s
  content is the conversation's and telemetry does not keep that.
- **The worker and the validator reach the current turn the way the wrapped
  backend already does** — a `Callable[[], TurnTrace]` resolved when something
  happens, never a per-turn object captured at graph build time. The shared
  `resolve()` swallows its own errors, so an observation still cannot break the
  work.

One change is a correction rather than an addition. The adapter used to fold
`TaskOutcome.tool_calls` into the turn's counter. With the loop instrumented
that would count every task tool call twice, so it is gone; the number is still
reported in the `task_finished` event, now named `budget_spent`, because budget
spent and calls executed are different facts and only one of them belongs in the
run's total.

## What it does not do

- **No GPU time and no derived cost.** They belong to 3C, and the approved
  decision is that they are derived when read rather than stored in columns, so
  no migration is owed for them either.
- **No inspection from the chat.** A CLI was the chosen surface; a Telegram
  command would be a product surface with its own access rules.
- **No prefill/decode separation.** 3C.
- **Nothing is fixed about what the trace now reveals.** Making the agent behave
  better with this evidence is item 4.

## Checks

Offline suite: **744 passed, 1 skipped** (`.venv\Scripts\python.exe -m pytest
tests/ -q`), up from 721 — 23 new tests, none removed or weakened.

- **Store contract** (`tests/test_telemetry_store.py`, +5): newest first and
  bounded by the limit; one user's runs; an empty database; and the one that
  matters — the unsuccessful list holds a failed run and an abandoned `running`
  one, and holds neither a completed nor a cancelled one. PostgreSQL joins this
  suite when `AGENT_TEST_DATABASE_URL` is set, and did not run here.
- **Task telemetry** (`tests/test_task_telemetry.py`, new, 10, driven through
  the real `TaskRuntime`, worker and validator): every stage bracketed once;
  every tool and every model call carrying the stage that spent it; the attempt
  number on the work it did; each executed tool with one start and one terminal
  event and consecutive call indexes; a failing tool not recorded as a success;
  the path recorded while the file content and the model's words are absent from
  the whole trace; calls past the budget recorded as skipped and not counted; a
  call refused before it ran visible with its own status and not counted; and an
  unmeasured task running exactly the same.
- **The double-count guard** is its own test: the graph's budget total and the
  measured count are asserted separately, so re-adding the fold turns one of
  them into six.
- **Inspector** (`tests/test_run_inspector.py`, new, 9): a finished run
  reporting what it cost; the queue wait inside the turn and inside the offsets;
  unattributed time named; an unfinished run labelled; a failed tool not
  rendered as a success; a skipped call shown with its reason; a task stage
  reporting its duration; the listing ordered and summarized; an empty listing
  saying so.
- **Manual end-to-end of the script**, against a real SQLite file written
  through the real recorder: `--last` and `show run <id>` both render, the stage
  and path appear on the tool line, and the totals match the summary row.

Not run: the PostgreSQL contract suite (offline by design here), and `ruff` —
the configuration exists in `pyproject.toml` but the tool is not installed in
this environment.

## Files

New: `app/telemetry/inspect.py`, `tools/show_run.py`,
`tests/test_task_telemetry.py`, `tests/test_run_inspector.py`.

Changed: `app/telemetry/base.py`, `app/telemetry/sqlite.py`,
`app/telemetry/postgres.py`, `app/telemetry/trace.py`,
`app/telemetry/backend.py`, `app/telemetry/__init__.py`,
`app/agent/task_worker.py`, `app/agent/task_validator.py`,
`app/agent/task_runtime.py`, `ui/telegram/adapter.py`,
`tests/test_telemetry_store.py`, `docs/CODEMAP.md`, `docs/OPERATIONS_MAP.md`,
`ROADMAP.md`.

No schema change, no migration, no new configuration. `docs/PRODUCT.md` is
unchanged: nothing here alters what a user sees.

## Deployed and accepted live, 2026-08-29

`assistant-control` deployed in 22.1 s, five functions re-created. The human then
asked the real chat for a small autonomous task — create `prices.csv` and a
`readme.md` describing it — approved the grant, and the task completed. Both
turns were read back out of the deployed database with `tools/show_run.py`
alone. **No Modal log was opened**, which is acceptance criterion 5.

The planning turn (`c00580bb`, `approval_requested`, 19.87 s) shows the router
at 5.14 s for 1,730 tokens and the plan stage at 6.11 s for 217 → 265, with
6.45 s of queue wait before either.

The execution turn (`660c1728`, `task_result_delivered`, 22.75 s) is the one
that was invisible before this step:

```text
Model calls   3 in implement (2.06 s, 3.91 s, 2.06 s), 5 in validate
Tool calls    list_files [implement 1]
              write_file [implement 1]  prices.csv
              write_file [implement 1]  readme.md
              list_files [validate 1]   .
              read_file  [validate 1]   prices.csv
              read_file  [validate 1]   readme.md
Stages        implement attempt 1  8.04 s
              validate  attempt 1  9.79 s
Totals        8 model calls, 6 tool calls, 6,518 in / 685 out
GPU           17.55 s measured model time, 30.0 s derived active, $0.0092 derived
```

Every claim the step made is visible in that output: one identity across both
turns, each tool with its stage, attempt and path, per-stage durations, budget
spent (6) equal to calls executed (6) with no double count, and no message text
anywhere. Validation cost more than implementation — 9.79 s against 8.04 s, five
model calls against three — which is the first fact about this agent's loop that
the old summary line could not have produced.

## Three defects the live run exposed, and fixed

All three were found by reading real output, not by the suite.

- **A stage called "finished".** The inspector recognised a stage by its event
  name, so `task_finished` — the task's own summary — became a stage row, and
  the runtime's outer `task_planning` bracket was listed beside the `plan` stage
  inside it. A stage is now recognised by carrying one.
- **An approval turn reported no visible response at all.** `first_visible_ms`
  was empty on the planning turn, though the chat had said "Planning…" within a
  second and shown the plan at 19.6 s. Only the streamed answer marked
  visibility. The task branches now mark theirs: `planning_started`,
  `plan_sent`, `task_started`.
- **A task execution turn had the same hole**, reporting first visibility at
  22.75 s — the final message — when "Starting…" had appeared at about 3 s.

Each has a test. The suite is **768 passed, 1 skipped**.

The acceptance above was produced by the code as deployed at the time. The three
fixes went out in a second deploy on 2026-08-30, 22.1 s, five functions
re-created, no GPU touched. The first visibility numbers of turns recorded
before that deploy stay as they were measured: a run is evidence of what
happened, not of what the code does now.

## The primary metric, closing item 3

Item 3 names GPU active seconds per successful user turn as the number to
watch, and the per-turn ingredients existed without anything aggregating them.
`tools/show_run.py --summary` now does, over whatever window `--last`, `--user`
and `--failed` select. A failed turn's GPU stays in the numerator and leaves the
denominator; a cancelled one is neither a success nor a failure and is counted
as neither.

Over the six live turns recorded so far:

```text
6 turns, 6 successful, 0 failed or unfinished
Per successful turn
  GPU active          21.22s   derived, upper bound
  derived cost      $0.0065
  model calls          3.00
  tool calls           1.00
  input tokens         5616
  output tokens         282
Derived cost over the window $0.0390
```

Three model calls a turn is the harness counted honestly: the router, the
answer, and everything an autonomous task spends. That is the baseline item 4's
loop changes get compared against.

Suite after this addition: **771 passed, 1 skipped**.
