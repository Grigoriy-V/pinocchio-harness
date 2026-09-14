# Baseline Measurement, Metrics and Logs

## Goal

Make the deployed and local assistant observable before changing the agent harness and loop.

After this step, any user turn should be traceable by one `run_id`, and it should be possible to answer:

- where wall-clock time was spent;
- how many model calls happened;
- how many tool calls happened and which tools were used;
- how many input/output tokens were consumed;
- when the first model token appeared;
- when the first visible Telegram preview appeared;
- whether the turn completed successfully;
- how much GPU-active time was attributable to the turn or serving window;
- whether a later agent-loop change improved or degraded the baseline.

This is not a general monitoring platform. Build the minimum observability needed to develop the agent loop with evidence.

## Current context

Real answer streaming is already deployed and accepted live.

The current runtime now exposes useful boundaries that did not exist before:

```text
Telegram update
→ webhook
→ inbox
→ worker
→ GeneralHarness router
→ conversational agent
→ model stream
→ tools
→ model stream
→ persistence
→ Telegram preview/final delivery
```

However, the chain is still not joined by one application-level identifier.

Past cold-start measurements had to be inferred manually from Modal logs across `assistant-control` and `assistant-llm-v2`, and the worker itself produced almost no useful trace.

The failed live PDF task also showed why aggregate counts are insufficient: knowing that a turn spent 20 tool calls does not explain what the agent actually did.

## References

### Project

- Repository: https://github.com/Grigoriy-V/local-multimodal-agent
- Current roadmap: https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/ROADMAP.md
- Control-plane cold-start notes: https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/docs/control_plane_cold_start_notes.md
- Existing cold-start report: https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/reports/2026-08-29_v2_control_plane_cold_start.md

### Model / infrastructure telemetry

- vLLM production metrics: https://docs.vllm.ai/en/latest/usage/metrics/
- vLLM OpenAI-compatible server: https://docs.vllm.ai/en/latest/serving/openai_compatible_server/
- Modal observability: https://modal.com/docs/guide/observability
- Modal GPU metrics: https://modal.com/docs/guide/gpu-metrics
- Modal billing CLI: https://modal.com/docs/reference/cli/billing

---

# 3A — Turn telemetry foundation

## One `run_id` for one user turn

Every accepted user turn gets one application-generated `run_id`.

That identifier must follow the turn through the whole application path where practical:

```text
Telegram update
    ↓
webhook
    ↓
durable inbox
    ↓
worker
    ↓
router model call
    ↓
agent graph
    ↓
model call(s)
    ↓
tool calls
    ↓
persistence
    ↓
Telegram delivery
```

The purpose is correlation, not identity.

Do not derive `run_id` from:

- Telegram message text;
- Telegram user id;
- thread id;
- model request id.

A UUID/ULID generated at ingress is sufficient.

`thread_id`, `user_id`, and Telegram `update_id` may be stored as metadata, but none replaces the run id.

## Storage boundary

Do not add telemetry methods to `ConversationStore`.

Conversation persistence and operational telemetry are different domains.

Use a small separate abstraction, conceptually:

```text
TelemetryStore
  start_turn(...)
  record_event(...)
  finish_turn(...)
  get_turn(...)
  events(...)
```

The local profile may use SQLite.

The deployed profile may use the same Neon database used by the application, but telemetry owns its own tables and API.

## Minimal schema

Use two tables.

### `turn_runs`

One row per user turn.

Suggested fields:

```text
run_id              TEXT PRIMARY KEY

user_id             TEXT
thread_id           TEXT
source               TEXT
source_update_id     TEXT

started_at           TIMESTAMP
finished_at          TIMESTAMP

status               TEXT
outcome              TEXT
route                TEXT

model_calls          INTEGER
tool_calls           INTEGER

input_tokens         INTEGER
output_tokens        INTEGER

first_model_token_ms INTEGER
first_visible_ms     INTEGER
total_ms             INTEGER

gpu_active_ms        INTEGER NULL
derived_cost_usd     REAL NULL

error_type           TEXT NULL
```

Do not store user message text here.

### `trace_events`

Ordered structured events belonging to one run.

Suggested fields:

```text
id          BIGSERIAL / INTEGER PRIMARY KEY
run_id      TEXT
seq         INTEGER
timestamp   TIMESTAMP
type        TEXT
duration_ms INTEGER NULL
data_json   JSON/TEXT NULL
```

Indexes:

```text
trace_events(run_id, seq)
turn_runs(started_at)
turn_runs(user_id, started_at)
turn_runs(status, started_at)
```

Do not create a large analytics schema before the first real traces exist.

## Privacy / data minimization

Application telemetry should contain timings, counts, state transitions and technical metadata.

Do not persist by default:

- user message text;
- assistant answer text;
- attachments;
- screenshots;
- document contents;
- full tool results;
- raw model prompts;
- streamed token deltas.

`user_id` may be retained because per-user cost/behaviour is part of the product requirement, but telemetry must not duplicate conversation content.

---

# 3B — Inspectable agent trace

## Principle

Use one event stream for both performance telemetry and agent debugging.

Do not build a separate tracing subsystem unless the minimal structured event model proves insufficient.

A trace should allow a failed turn to be reconstructed structurally without reproducing the conversation itself.

## Core events

Start with a small stable vocabulary.

### Turn lifecycle

```text
turn_started
turn_finished
turn_failed
```

### Ingress / worker

```text
update_enqueued
worker_started
inbox_claimed
harness_ready
```

### Router

```text
router_started
router_finished
router_failed
```

Example metadata:

```json
{
  "route": "answer",
  "input_tokens": 1530,
  "output_tokens": 24
}
```

### Model

```text
model_started
model_first_token
model_finished
model_failed
```

Example `model_finished` metadata:

```json
{
  "purpose": "answer",
  "call_index": 2,
  "input_tokens": 4872,
  "output_tokens": 614,
  "finish_reason": "stop",
  "duration_ms": 4120
}
```

Do not record one trace row per streamed token.

`model_first_token` is enough for TTFT.

### Tools

```text
tool_started
tool_finished
tool_failed
```

Example:

```json
{
  "tool": "view_web_page",
  "call_index": 4,
  "duration_ms": 1834,
  "status": "success"
}
```

For failures:

```json
{
  "tool": "read_document",
  "error_type": "DocumentError",
  "status": "failed"
}
```

Do not persist full tool output by default.

Tool arguments may be added later as sanitized debug metadata if traces prove impossible to understand without them.

### Approval / interrupts

```text
approval_requested
approval_resumed
approval_declined
```

A turn that correctly stops for approval is not a failure.

### Persistence

```text
persist_started
persist_finished
persist_failed
```

### Telegram presentation

```text
telegram_preview_started
telegram_preview_updated
telegram_final_sent
telegram_delivery_failed
```

Do not record every preview edit unless it becomes useful.

For baseline metrics, the first preview and final delivery are enough.

## Where events should originate

Events should be emitted where the fact is actually known.

Examples:

```text
Webhook
  → update_enqueued

Worker
  → worker_started
  → inbox_claimed

GeneralHarness
  → router_started
  → router_finished

Model boundary / graph
  → model_started
  → model_first_token
  → model_finished

Toolbox
  → tool_started
  → tool_finished

Persistence
  → persist_finished

Telegram adapter
  → telegram_preview_started
  → telegram_final_sent
```

Do not infer a model start in the Telegram adapter or infer tool completion from a later assistant answer.

## Logging

Structured logs should include at least:

```text
run_id
event
timestamp
```

Example:

```json
{
  "run_id": "01J...",
  "event": "model_finished",
  "purpose": "answer",
  "duration_ms": 4031,
  "input_tokens": 4872,
  "output_tokens": 614
}
```

The database trace is the durable application record.

Runtime logs are the fast operational view.

They should share the same `run_id`.

---

# Outcome and success semantics

## Do not use only `success: bool`

A turn can stop correctly without producing a final answer.

Use explicit outcomes.

Initial outcome vocabulary:

```text
answer_delivered
approval_requested
task_result_delivered
cancelled
failed
```

Then derive:

```text
successful =
    answer_delivered
    OR approval_requested
    OR task_result_delivered
```

`cancelled` is neither an application failure nor a successful completed result.

`failed` is a failure.

This definition prevents a turn that exhausts the tool budget and crashes from being counted as successful merely because the worker itself returned normally.

## Turn status

Operational status can remain separate:

```text
running
completed
failed
cancelled
```

`status` describes execution.

`outcome` describes what happened from the product's point of view.

---

# Baseline metrics

Keep the first metric set small.

## User-facing latency

### End-to-end turn latency

```text
turn_finished - turn_started
```

Represents total wait for a completed turn.

### First visible response

```text
telegram_preview_started - turn_started
```

This is the streaming UX metric.

It is not the same as model TTFT.

## Model latency

### Model TTFT

```text
model_first_token - model_started
```

### Model total duration

```text
model_finished - model_started
```

### Decode rate

Prefer provider/vLLM metrics when available.

Approximate application-level fallback:

```text
output_tokens /
(model_finished - model_first_token)
```

Label it approximate if derived from application timestamps.

## Token usage

Per turn:

```text
input_tokens
output_tokens
```

Count all model calls, including the router.

Do not report only the final answer model call.

## Agent efficiency

Per turn:

```text
model_calls
tool_calls
tool_time_ms
model_time_ms
```

Useful derived metrics:

```text
tool_calls / successful_turn
model_calls / successful_turn
```

These are particularly important before changing the loop.

## Reliability

```text
successful_turns / completed_turns
failed_turns / all_turns
```

Also group failures by:

```text
error_type
stage
tool
```

## Primary economic metric

The roadmap's primary metric remains:

```text
GPU active seconds per successful user turn
```

This should be kept distinct from total platform spend.

---

# 3C — Model / GPU baseline

## Goal

Separate:

```text
cold/wake
prefill
decode
```

and establish a baseline for:

```text
input-size scaling
output generation speed
prefix-cache effectiveness
```

Do not infer all three from a single `tokens/sec` number.

## Prefer vLLM's own metrics

vLLM already exposes model-serving metrics including TTFT, prefill and token statistics.

Reference:

https://docs.vllm.ai/en/latest/usage/metrics/

Use the engine metrics where possible rather than reimplementing model internals in the application.

Relevant categories include:

```text
time to first token
prefill time
inference time
time per output token
prompt tokens
generation tokens
prefix cache queries/hits
prefill KV computation
```

Exact metric names must be verified against the deployed vLLM version rather than copied blindly from a different release.

## Baseline probe

Implement a small explicit benchmark/probe rather than adding model-engine counters to product telemetry.

Conceptual flow:

```text
read metrics snapshot A
→ send controlled request
→ read metrics snapshot B
→ calculate delta
```

Use isolated requests so background traffic does not contaminate the result.

## Required scenarios

### A — short input / long output

Purpose:

- decode throughput;
- stable time-per-output-token;
- TTFT with small prefill.

### B — long input / fixed output

Use several prompt sizes.

For example:

```text
~1k tokens
~4k tokens
~8k tokens
~16k tokens
```

Purpose:

- prefill scaling;
- TTFT scaling;
- confirmation of the current context-cost baseline.

Do not require 64k/128k experiments here; those remain separately deferred.

### C — repeated common prefix

Send a controlled repeated-prefix request.

Purpose:

- verify that prefix caching is actually used;
- measure hit/query deltas;
- compare TTFT/prefill against the uncached request.

Do not claim prefix caching from configuration alone.

---

# GPU active time and cost

## GPU-active seconds

The application knows model request boundaries but does not automatically know the exact billed life of a Modal GPU container.

Therefore distinguish:

```text
model_request_time
estimated_gpu_active_time
platform_billed_time
```

Do not label one as another.

For the first implementation, `gpu_active_ms` may be an estimate derived from the serving window if exact platform correlation is unavailable.

Document the derivation.

## Derived per-turn cost

Per-turn cost is not an invoice.

Modal billing aggregates resources at the platform/application level.

Use:

```text
derived_gpu_cost =
estimated_gpu_active_seconds × configured GPU rate
```

and explicitly store/report it as:

```text
derived_cost_usd
```

Do not call it `cost_usd` without the qualifier.

Where useful, compare aggregate derived cost over a time window against Modal's actual billing totals.

Reference:

https://modal.com/docs/reference/cli/billing

---

# Minimal inspection surface

Do not build a dashboard in this step.

A CLI/script/query is sufficient if it can answer:

```text
show run <run_id>
```

with output conceptually like:

```text
Run 01J...
Outcome: answer_delivered
Total: 8.4 s
First visible: 2.1 s

Router
  0.58 s
  1,420 → 18 tokens

Model call 2
  first token: 1.16 s
  total: 4.03 s
  4,872 → 614 tokens

Tools
  search_web        0.81 s
  view_web_page     1.83 s

Persistence
  0.12 s

Telegram final
  0.21 s

Totals
  model calls: 2
  tool calls: 2
  input tokens: 6,292
  output tokens: 632
```

Also support listing recent failed runs.

That is enough to begin item 4.

---

# Implementation order

## 3A.1 — Correlation

Add `run_id` at ingress and propagate it through:

```text
webhook → inbox → worker → adapter/harness
```

No timing system is useful until this exists.

## 3A.2 — TelemetryStore

Implement:

```text
turn_runs
trace_events
```

for SQLite and PostgreSQL/deployed storage as needed.

Use an additive migration.

Do not reset the deployed database.

A populated-database migration remains a human-gated deployment action.

## 3A.3 — Turn lifecycle

Record:

```text
turn_started
turn_finished
turn_failed
```

and populate the summary row.

## 3B.1 — Model/router events

Instrument router and conversational model calls.

Capture:

```text
purpose
start
first token
finish
tokens
finish reason
error
```

## 3B.2 — Tool events

Instrument the common Toolbox execution boundary.

Do not instrument each tool independently unless a tool has internal stages worth measuring later.

## 3B.3 — Persistence and Telegram events

Capture first visible preview and final delivery.

This allows:

```text
model TTFT
vs
first visible Telegram response
```

to be measured separately.

## 3B.4 — Inspector

Add a simple run-trace inspection command or script.

Use one real failed/complex trace to verify that the sequence is understandable without message contents.

## 3C.1 — vLLM metrics probe

Read the deployed engine metrics and determine which required counters are actually available in the currently deployed version.

## 3C.2 — Controlled baseline

Run:

```text
short-input / long-output
long-input / fixed-output
repeated-prefix
```

and record the results in a report.

Every live GPU run remains explicitly gated.

---

# Tests

## Telemetry persistence

- one turn creates one `turn_runs` row;
- events preserve `run_id`;
- event order is deterministic per run;
- local and PostgreSQL implementations satisfy the same telemetry contract where practical;
- telemetry does not require message text;
- failure still closes/finalizes the run record.

## Correlation

- Telegram `update_id` maps to exactly one `run_id`;
- worker receives the same `run_id` created at ingress;
- router/model/tool events carry the same `run_id`;
- retry/duplicate update handling does not create duplicate successful turn records.

## Model telemetry

- streaming path records one `model_first_token`;
- non-streaming router calls do not invent a first-token event if the boundary is unavailable;
- input/output token counts aggregate across all model calls;
- `finish_reason` is retained;
- model errors produce `model_failed`.

## Tool telemetry

- each executed tool call has one start and one terminal event;
- validation failures are distinguishable from execution failures;
- declined/destructive approval paths do not appear as successful executions;
- tool results themselves are not persisted into telemetry.

## Outcome

- normal answer → `answer_delivered`;
- approval interrupt → `approval_requested`;
- delivered task result → `task_result_delivered`;
- user cancellation → `cancelled`;
- uncaught turn failure → `failed`.

---

# Acceptance criteria

Item 3 is ready to close when:

1. one live Telegram turn has a single `run_id` visible across the application chain;
2. its trace shows router, model, tools if used, persistence and Telegram delivery in order;
3. model token usage includes every model call in the turn;
4. model TTFT and first visible Telegram response are separately measurable;
5. a failed or intentionally constructed complex turn can be inspected without reading Modal's raw logs;
6. the trace contains no user message text, attachment bytes or raw model prompts by default;
7. a controlled vLLM baseline separates prefill/TTFT from decode;
8. prefix-cache behavior is measured rather than assumed;
9. GPU-active-time / cost numbers are clearly identified as measured or derived;
10. the resulting baseline is recorded so item 4 changes can be compared against it.

---

# Non-goals

Do not combine item 3 with:

- agent-loop redesign;
- router removal / single-call optimization;
- conversation serialization or coalescing;
- a Grafana/Datadog-style dashboard;
- distributed tracing infrastructure for its own sake;
- storing full prompts or conversation text in telemetry;
- storing every streaming delta;
- exact per-turn Modal invoice attribution;
- adaptive autoscaling;
- speculative decoding;
- prefix-cache tuning before prefix-cache behavior is measured.

Those belong to later work once this baseline exists.

## Completion principle

The purpose of item 3 is not to collect as many metrics as possible.

The required capability is:

> Given one bad or expensive user turn, open one `run_id` and see where time, model calls and tool calls went; after changing the agent loop, measure whether the same class of turn became better or worse.
