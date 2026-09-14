# Control plane cold-start notes

## Current first-message path

```text
Telegram
  ↓
telegram_webhook
  ↓
Neon enqueue
  ↓
spawn process_telegram_update
  ↓
process_telegram_update
  ↓
GeneralHarness
  ↓
GPU restore
  ↓
inference
  ↓
Telegram reply
```

## Measured latency

### `telegram_webhook`

Current live measurements:

| Path | Total duration | Execution |
|---|---:|---:|
| Cold webhook | ~4.69 s | ~1.70 s |
| Warm webhook | ~306 ms | ~271 ms |

Approximate cold-only overhead:

```text
~3.0 s  Modal/container startup
~1.4 s  extra first-execution overhead
         (imports, initialization, first DB/TLS work, etc.)
```

The useful webhook work itself is small:

```text
validate Telegram request
→ enqueue update in Neon
→ spawn update worker
→ return HTTP 200
```

The difference between ~4.69 s cold and ~0.306 s warm shows that most webhook latency is cold-path overhead, not actual webhook logic.

### `process_telegram_update`

The cold start of the second CPU worker has **not yet been isolated and measured**.

Current sequence:

```text
spawn requested
→ Modal schedules/starts worker if no warm container exists
→ process_telegram_update enters
→ claim inbox lease
→ load state/context
→ run agent
→ call GPU
```

Therefore it is not yet valid to assume that the worker cold start is exactly the same ~3 s as the webhook.

### GPU

Latest end-to-end run observed approximately:

```text
GPU snapshot restore ≈ 5.5 s
```

So currently the largest confirmed first-turn components are roughly:

```text
telegram_webhook cold   ≈ 4.7 s
update-worker cold      = unknown
GPU restore             ≈ 5.5 s
inference               + additional time
```

## Important distinction: CPU vs GPU scaledown

Current control-plane CPU functions use:

```text
telegram_webhook          scaledown_window = 15 s
process_telegram_update   scaledown_window = 15 s
```

This was changed from 2 s because every message was otherwise paying a CPU cold start.

The intended GPU policy is different because GPU idle time is much more expensive:

```text
CPU → keeping warm briefly is cheap and improves UX
GPU → aggressive scale-to-zero is economically useful
```

## Is the webhook/update-worker split still justified?

Yes.

The webhook is the short-lived ingress/admission gate:

```text
Telegram
→ validate secret / allow list
→ persist update durably
→ spawn worker
→ return HTTP 200
```

The update worker owns the long-running turn:

```text
claim durable job
→ graph / agent loop
→ wait for GPU
→ tools / sandbox
→ send Telegram reply
→ complete or retry job
```

This keeps Telegram's HTTP request independent from long agent execution and preserves:

- durable retry;
- duplicate-update protection;
- leased processing;
- restart/recovery;
- long-running tool or sandbox work;
- independent scaling of ingress and agent work.

The split should only be reconsidered if the warm handoff itself is measured to add a substantial delay.

## What to measure next

Instrument one cold first message with explicit timestamps:

```text
T0  Telegram request enters webhook
T1  webhook function entered
T2  Neon enqueue completed
T3  spawn requested
T4  process_telegram_update function entered
T5  inbox claim completed
T6  adapter/harness ready
T7  context ready
T8  GPU request sent
T9  GPU ready / first model response
T10 inference completed
T11 Telegram reply sent
```

The critical unknown is:

```text
T4 - T3 = actual update-worker cold start / scheduling delay
```

Also separate:

```text
T1 - T0 = webhook platform/container cold start
T3 - T1 = webhook application work
T8 - T4 = worker initialization + DB/context work
T9 - T8 = GPU wake / TTFT portion
```

## Main cold-start optimization candidates

### 1. Optimize the webhook first

The webhook is currently the largest confirmed CPU cold component.

A useful experiment is to split the shared `control_image` into:

```text
tiny webhook image
├─ FastAPI
├─ Telegram validation
├─ psycopg / inbox
└─ Modal spawn

full worker image
├─ app
├─ LangGraph
├─ PostgreSQL/checkpointer
├─ tools
└─ full agent stack
```

Today both webhook and worker use the same full control image even though the webhook does not need the full agent stack.

### 2. CPU Memory Snapshot for the update worker

The update worker is a strong candidate for Modal CPU Memory Snapshots if its cold time is dominated by Python imports and graph initialization.

Conceptual layout:

```text
snapshot creation:
Python
→ import agent stack
→ import LangGraph
→ build pure graph/config state
→ snapshot

runtime:
restore snapshot
→ read runtime secrets
→ create fresh DB/HTTP clients
→ claim job
→ call GPU
```

Do **not** snapshot live DB connections, HTTP sessions, or mutable credential-dependent network state.

### 3. Move heavy imports before the snapshot boundary

Current worker imports much of the application inside `process_telegram_update()`. If using snapshots, heavy pure-Python imports and graph construction should happen before request processing so they are captured in the snapshot.

### 4. Keep `min_containers=0`

`min_containers=1` would nearly remove CPU cold start, but keeping a full worker permanently warm is disproportionately expensive for a personal serverless assistant.

It is better to first test:

```text
smaller webhook image
+
CPU memory snapshot
+
pre-imported worker state
```

## Current priority

Before changing architecture further:

1. measure the actual `spawn → worker entered` time;
2. separate webhook cold start from worker cold start;
3. test a minimal webhook image;
4. test CPU Memory Snapshot for the update worker;
5. compare the same first-message trace before/after.

The present evidence supports this conclusion:

> The largest confirmed CPU latency is currently the first cold `telegram_webhook`. The second worker may also be expensive, but its cold start has not yet been measured independently.
