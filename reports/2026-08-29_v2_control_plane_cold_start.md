# Control plane cold start: what was measured and what was decided

Deployed measurements of `assistant-control`, 2026-08-28/29. Design rationale
for the chain itself is in `reports/archive/control_plane_cold_start_notes.md`.

## Webhook imports

The webhook imported `read_update` from `ui/telegram/adapter.py`, which imports
the harness and through it LangGraph. Importing any submodule of `ui.telegram`
also ran the package `__init__`, which did the same.

Measured locally (Windows, warm cache, full core):

| | |
|---|---:|
| what the webhook needs (fastapi, psycopg, config) | 243 ms |
| what it additionally loaded | +507 ms |

Fixed by moving the wire format to `ui/telegram/wire.py`, which may import only
the standard library, and making `ui/telegram/__init__.py` resolve its names
lazily. Held by a subprocess test with a fresh interpreter and an AST check.

Deployed effect on the webhook's execution time:

| | cold | warm |
|---|---:|---:|
| before | 1.67-3.86 s | 271 ms |
| after | 0.34-0.46 s | ~200 ms |

## Memory snapshot: tried, measured, reverted

Nine deployed cold starts:

| | mean | n |
|---|---:|---:|
| no snapshot | 5.36 s | 6 |
| creating a snapshot | 8.56 s | 6 |
| restoring a snapshot | 4.06 s | 3 |

Subtracting execution leaves ~3.5 s of container in both the snapshot and
no-snapshot cases. A restore skips initialization, and the import change had
already removed the initialization, so the two were substitutes. Six of nine
cold starts were still creating rather than restoring, which Modal documents:
a snapshot restores only onto the worker type that made it, and placement here
is unpinned.

Reverted. An AST test asserts the argument is absent from the decorator.

## Image split: considered, dropped

The image-relevant install is ~100 MB: Modal client ~25 MB, cryptography
~11 MB, psycopg ~14 MB, pydantic ~9 MB, agent stack ~16 MB. Only the agent
stack could be dropped, and the import change already removed its cost. A split
would not touch the ~3.5 s of scheduling.

The 195 MB figure quoted earlier was the local virtualenv, which includes
chainlit, pytest and pywin32 and is not what the image installs.

## Worker cold start

From the platform's startup column, no run needed:

| | |
|---|---:|
| scheduling | 3.23 s (3.05-3.36) |
| first-execution work | 1.71 s |
| cold execution | 3.99 s |
| warm execution | 2.28 s |
| total over a warm worker | 4.93 s |

Scheduling matches the webhook's, so it is the platform floor rather than this
code. The 1.71 s is real initialization — unlike the webhook's, which is now
nothing — so a memory snapshot could pay here, worth roughly 1.2 s, at the cost
of module-scope imports and therefore a separate image. Not taken: the wider
idle window makes a cold worker rare.

## Warming the model from the webhook

Chain with everything cold, before and after:

| | before | after |
|---|---:|---:|
| webhook done, spawn issued | 4.00 s | 3.70 s (wake sent) |
| worker reaches the model | 8.94 s | 8.94 s |
| model ready | 14.44 s | 9.20 s |

The worker's 4.93 s disappears into the wake with 0.26 s of slack.

Awaiting the wake before the durable write cost about a second on every message
(warm execution 1.28-1.53 s). Running it concurrently with enqueue and spawn,
and keeping the HTTP client for the container's life, returned warm execution
to 0.199-0.213 s — the level before warming existed. Two regimes remain
visible: ~200 ms when the model is already awake, ~1.1 s when it is asleep,
which is when the wake is worth paying for. The spawn no longer waits for it.

## Prices used in these decisions

| | |
|---|---:|
| webhook idle, cpu=0.25 / 512 MiB | $0.0000044 /s |
| worker idle, cpu=1.0 / 2 GiB | $0.0000175 /s |
| A10 | $0.000306 /s |

Idle windows set to 60 s on both CPU functions (~$3/month at a hundred wakes a
day, dominated by the worker) and 12 s on the GPU. Modal's 2 s floor was tried
on the GPU and reversed: shorter than the pause between two messages, so an
ordinary back-and-forth paid a 10.4 s restored wake almost every turn.

`min_containers=1` was priced and not chosen: ~$5.7-11.4 a month on the webhook
depending on its size, ~$45 on the worker.

## Confirmed after the deploy

Three live messages, 2026-08-28 ~18:47-18:50 UTC, read from both apps' logs.

Webhook: 6.16 s / 1.55 s cold, then 1.20 s / 1.07 s and 1.24 s / 1.11 s. Every
one is the asleep regime — with a 12 s GPU window, an ordinary pause between two
messages puts the model back to sleep, so the ~0.9 s wake is paid nearly every
turn and the 200 ms regime is the rare one. The concurrency fix is still worth
it: it removed the wake from the critical path, so 1.28-1.53 s became 1.07-1.11 s
here and 0.2 s when the model happens to be up.

Model, per turn on a warm GPU: `GET /v1/models` (the wake), then a router call
at 567-613 ms, then an answer call at 3.03-5.62 s. Two of the three turns show
that sequence intact. No 4xx or 5xx anywhere in three hours.

The ~9.2 s cold first message is still not confirmed. Nothing joins a Telegram
update to a model call, so the chain can only be inferred from two logs with no
shared identifier.

## Open

- No application telemetry. Every number above came from Modal's dashboard or
  from inference over two coarse columns, and none of it exists in the local
  profile. Recorded as the first sub-item of roadmap queue 3.
- The worker is silent. Over three hours the control app logged nothing but the
  webhook's own HTTP lines; `process_telegram_update` emits no line at all, so a
  turn that fails leaves no trace.
- NCCL noise costs observability, not just tidiness: 2,520 of 2,704 lines in the
  model app's three-hour log are broken-pipe stanzas, so the default 100-entry
  fetch covers 38 seconds. The fix owed to the next `assistant-llm-v2` deploy is
  worth more than it looked.
