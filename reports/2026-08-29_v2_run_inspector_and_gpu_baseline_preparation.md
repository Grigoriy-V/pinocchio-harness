# The rest of item 3 — run inspector, task-stage detail, GPU baseline

**Date:** 2026-08-29
**Task input:** `reports/archive/baseline_measurement_metrics_logs.md`
**Builds on:** `reports/2026-08-29_v2_turn_telemetry_implementation.md` (3A, closed
and accepted live)
**Roadmap item:** queue 3, remaining bullets.

Preparation only. Nothing here is implemented, and none of it is approved work.
Options and reasoning live here; only what the human approves in words moves to
`ROADMAP.md`.

## What is actually missing

3A recorded turns. It did not make them readable, and it left the most expensive
path in the product almost blind.

- **There is no reading half at all.** `TelemetryStore` can fetch one run by id
  and its events; there is no way to ask "the last twenty runs" or "what failed
  today", and no script that renders a trace. Today the interface is writing SQL
  by hand against two tables, which is what acceptance criterion 5 — *a failed
  turn can be inspected without reading Modal's raw logs* — exists to remove.
- **A turn that died leaves a `running` row forever.** Nothing closes it, by
  design: the worker's `finally` only runs if the process survives. Those rows
  are precisely the crashes worth reading, and no query names them yet.
- **The bounded task path counts, but does not explain.** Its model calls are
  measured through `TracedBackend`, and its tool calls are folded in at the end
  as a single number taken from `TaskOutcome.tool_calls`. The tools themselves
  run in `ModelTaskWorker.implement`, in a loop of its own (`toolbox.run(call)`,
  `app/agent/task_worker.py:311`) that emits nothing. The failed live PDF task
  reported twenty tool calls and, after 3A, still would. That loop is the single
  concrete reason the rest of 3B is worth building.
- **Nothing separates prefill from decode.** 3A measured the application: queue
  wait, router, TTFT as the client sees it. It cannot say whether a 4k-token
  prompt costs twice a 2k one, and prefix caching has still never been read out
  of the engine.

## Proposed split: two steps, not one

Approved answer from the human: **two steps**.

3B is entirely offline code plus a deploy and one live turn. 3C cannot be
written without waking a GPU to see which counters the deployed vLLM actually
exposes. Keeping them in one approved step would mean holding a GPU gate open
across days of ordinary implementation, and the gate would stop meaning
anything. So:

- **Step 3B — inspectable trace.** Offline; ends with a deploy and one live
  gated turn for acceptance.
- **Step 3C — model and GPU baseline.** Every run wakes the GPU; each run is its
  own permission.

Item 3 closes only after both. Acceptance criteria 1-6 belong to 3B, 7-9 to 3C,
and 10 is the recorded baseline from each.

---

# Step 3B — inspectable trace

## B1. A reading contract

Add to `TelemetryStore`, implemented in both SQLite and PostgreSQL:

```python
def recent_runs(
    self,
    *,
    limit: int = 20,
    user_id: str | None = None,
    unsuccessful: bool = False,
) -> list[TurnRun]
```

Ordered by `started_at` descending, straight onto the indexes 3A already
created. **No schema change and no migration**: every field the inspector needs
is a column that exists, and the new stage detail goes into the `data` JSON of
`trace_events`. That is the single most important property of this step — it
touches the populated Neon database only by reading it.

`unsuccessful` is deliberately not `status = 'failed'`. It means *outcome is
`failed`, or the row never finished at all* — because a container that dies
mid-turn leaves `running` and would otherwise be the one class of failure the
failure list cannot show.

## B2. `tools/show_run.py`

The human chose a script, not a Telegram command. The task document says a CLI
is sufficient, and a chat command would add a product surface with its own
access rules, in the same chat where the conversation happens.

```bash
python tools/show_run.py <run_id>
python tools/show_run.py --last 20
python tools/show_run.py --failed
python tools/show_run.py --user <telegram id> --last 10
```

It opens whatever the application would open — `open_telemetry(AgentSettings())`
without migration — so the local profile reads its SQLite file and setting
`AGENT_DATABASE_URL` reads the deployed Neon database. One code path, no second
notion of where telemetry lives.

Output follows the shape in the task document: header (outcome, route, total,
first visible), then the events grouped as router / model calls / tools /
persistence / delivery, each with duration and tokens, then the totals. Three
additions the real data asks for:

- **queue wait is shown first**, because 3A's own baseline found it was 241 ms
  to 6.5 s — often the largest single part of the wait, and invisible in a
  listing that starts at the model;
- **an unfinished run is labelled as such**, with the last event it managed to
  write, rather than rendered as if it had ended;
- **gaps are shown**, so time nobody attributed to a step is visible instead of
  silently absorbed. A 5.6 s persistence outlier was found this way once
  already.

By construction it can print nothing private: it renders columns and event
`data`, and neither ever held text. A test asserts that on a trace produced by a
real turn.

## B3. Making the task path readable

Four changes, all inside the task path.

**The current stage becomes part of every event.** `TurnTrace` gains a stage
that events carry automatically:

```python
with trace.staged("implement", iteration=2):
    ...
```

and every event emitted inside it gets `stage` and `iteration` in its data. One
mechanism rather than two: model calls do not need a second `purpose` vocabulary
(`task_plan`, `task_implement`, …) because the stage already says which node
spent them.

**The worker reaches the current turn the way the backend already does.**
`TracedBackend` resolves the live trace through a `Callable[[], TurnTrace]`
supplied by `TaskRuntime` (`lambda: self._trace`). `ModelTaskWorker` and
`ModelTaskValidator` get the same callable, so the stage is set where the fact
is known — inside `plan`, `implement` and the validator — rather than inferred
from a graph update after the node has already finished. No new state, no
contextvars, and nothing per-turn captured at graph build time.

**The implementation loop's tools are bracketed.** `toolbox.run(call)` goes
inside `trace.tool(call.name)`, with `tool_failed(result)` deciding the terminal
event, exactly as the conversational graph does. The two branches that refuse a
call before it runs — a path repeating the grant directory, a destructive call
under a grant that does not allow it — are recorded as failures with distinct
statuses (`rejected`, `not_granted`), which is what the task document means by
distinguishing validation failures from execution failures. Calls beyond the
budget become `tool_skipped`: they never ran, so counting them as spent would
misreport the exact failure the PDF task hit.

Per the human's answer, each tool event also carries the `path` argument when
there is one — never `content`, never the result, never any other argument
value. A trace of twenty calls is only readable if it shows the agent rewriting
one file twenty times, and the path is a name inside that user's own sandbox,
not conversation content.

**The double count goes away.** The adapter currently folds
`view.outcome.tool_calls` into the run's total. With the loop instrumented, that
line must be deleted or the number doubles. Named here because it is the one
change in this step that can silently corrupt the baseline 3A already recorded.

## B4. Tests

- Store contract gains `recent_runs`: ordering, limit, the user filter, and that
  an unfinished run appears in the unsuccessful list while a completed one does
  not. Runs against PostgreSQL too when `AGENT_TEST_DATABASE_URL` is set.
- A new `tests/test_run_inspector.py`: rendering a complete trace, an unfinished
  one, and one with a failed tool; the totals matching the summary row; and no
  text from a real turn appearing in the output.
- A new task-path telemetry test driving `TaskRuntime` with a fake backend
  through plan → implement → test: every executed tool has one start and one
  terminal event, every event inside a stage carries it, a budget-exhausted call
  is skipped rather than spent, a refused call is not a success, and the run's
  `tool_calls` equals the number actually executed — the double-count guard.

## B5. What closes 3B

Offline: the suite green, with the new tests. Live: deploy, then **one** real
turn that runs an autonomous task, read afterwards with `show_run.py` alone —
no Modal logs — and the trace has to explain where its tool calls went. That
live turn is a GPU wake and a separate permission.

Not in scope: fixing whatever the trace then reveals about the agent's
behaviour. That is item 4, and this step exists to make it possible.

## B6. Cost and risk

No migration. No new configuration. One deploy, one live turn. The only way this
step can damage anything already recorded is the double-count line in B3, which
has a test written against it.

---

# Step 3C — model and GPU baseline

Not written yet, and deliberately shallower here: three of its decisions cannot
honestly be made before the deployed engine has been read once.

## C1. Reading the engine

vLLM's own metrics, not reimplemented counters. The server is
`assistant-llm-v2` at `.../server-serve`, vLLM **0.26.0**, whole surface behind
Modal proxy auth — so `/metrics` is the same URL and the same
`Modal-Key`/`Modal-Secret` pair the model client already builds
(`app/models/openai_compatible.py:325`). That header construction gets lifted
into a small shared helper rather than copied.

**Metric names get verified, not assumed.** The first probe run dumps every
`vllm:` family the deployed version exposes and records it, because the task
document is explicit that names must not be copied from a different release.
Expected but unconfirmed: time-to-first-token, time-per-output-token, prefill
and decode time histograms, prompt/generation token totals, and prefix-cache
queries and hits.

The image sets `VLLM_SERVER_DEV_MODE=1` (`deploy/modal/model_app.py:235`), which
in principle also exposes a prefix-cache reset. If it exists in 0.26.0, scenario
C gets a clean baseline instead of an inferred one; if not, the first request of
a fresh container serves as the cold reference. This is checked in the same
first run.

## C2. Two problems that shape the probe

**Counters are per container.** `/metrics` is that container's engine, and
`MAX_CONTAINERS = 1` means at most one — but a container that scales to zero and
comes back resets everything. The probe therefore records a container identity
with every snapshot and refuses to report a delta across a boot rather than
publishing a negative number as a measurement.

**The idle window bounds the pause between requests, not the run.**
`SCALEDOWN_WINDOW = 12` seconds, and a gap longer than that ends the container,
so the next scenario would measure a cold start instead of prefill. The
correction that follows from stating it that way: the probe is one script that
issues its requests back to back, analysing nothing until every snapshot is
saved, and its gaps are then fractions of a second. **Twelve seconds is enough
and `autoscale.py` does not need to be touched** — one fewer gate. Raising the
window is the fallback if a real run turns out to be ragged, decided on
evidence rather than in advance.

The whole suite is about two minutes of GPU work: roughly 32 s for scenario A's
512-token generation at the measured ~16 tok/s, 60-70 s for B's four input sizes
times three repeats at 64 output tokens each, 15 s for C, and about 20 s of
metric reads and snapshot restore. At the A10's $0.000306/s that is near
**$0.05**, plus one trailing idle window. A full cold boot instead of a restore
would add roughly 190 s, about $0.06 more.

## C3. Scenarios

Per the task document, with one correction it does not make: `MAX_MODEL_LEN` is
16384, so a 16k input plus its output does not fit.

- **A — short input, long output.** Decode throughput, time per output token,
  TTFT with negligible prefill.
- **B — long input, fixed 64-token output**, at roughly 1k / 4k / 8k / 12k
  input tokens. Prefill and TTFT scaling. 12k rather than 16k so the output has
  room; the shortfall is stated in the report rather than quietly dropped.
- **C — repeated prefix.** The same long prefix with a different suffix, twice,
  comparing prefix-cache hits and queries and the TTFT/prefill delta against the
  uncached first request.

One warm window, isolated requests, snapshot-request-snapshot deltas, results in
`reports/` and `reports/ml_work.jsonl`. The prompts are synthetic filler, not
anyone's conversation.

## C4. GPU time and derived cost

The human's answer: **derived at read time, no new columns.** So
`turn_runs` stays as it is, and `tools/show_run.py` grows a cost section that
computes, per run:

```text
model_request_time        measured, from model_started/model_finished
estimated_gpu_active_ms   derived: model span + SCALEDOWN_WINDOW,
                          plus a wake when the turn paid for a cold start
derived_cost_usd          derived: estimated seconds x the configured A10 rate
```

Three names, never used for each other, and the platform's own billed time is a
fourth thing this cannot see. The reason not to store them is that the formula
will get better — the moment it does, every past run is recomputed instead of
carrying a frozen wrong number, and no populated-database migration was spent to
get there. The aggregate over a window can then be compared against `modal
billing`, which reads and starts nothing.

## C5. Gates

Every probe run wakes the GPU and needs permission for that run — not for the
step, and not for the session. Writing the probe, and analysing snapshots it
already produced, need nothing. If the scaledown window ever has to be raised,
that is a second permission, asked for when a ragged run shows it is needed.

---

# Order

1. 3B: reading contract → inspector → task-stage detail → tests.
2. 3B live: deploy, one gated task turn, read it back, close.
3. 3C: metrics discovery run (gated) → scenarios in one warm window (gated) →
   cost derivation in the inspector → record the baseline.

# What is not in either step

Agent-loop redesign, router removal, conversation serialization, a dashboard,
prompt or conversation text in telemetry, per-turn Modal invoice attribution,
adaptive autoscaling, speculative decoding, and any prefix-cache tuning before
prefix-cache behaviour has been measured. Those are items 4 and 5.
