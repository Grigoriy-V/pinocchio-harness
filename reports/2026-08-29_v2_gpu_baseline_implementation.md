# Model and GPU baseline (item 3C) — the instrument, before the measurement

**Date:** 2026-08-29
**Task input:** `reports/archive/baseline_measurement_metrics_logs.md`
**Preparation and approved decisions:**
`reports/2026-08-29_v2_run_inspector_and_gpu_baseline_preparation.md`
**Roadmap item:** queue 3, third and fifth bullets.

**No measurement has been taken.** Everything here is the instrument and the
arithmetic, proven offline. Running it wakes the GPU and is a separate
permission.

## What was built

- **The engine's metrics are read, not guessed at.** `app/telemetry/vllm.py`
  parses vLLM's Prometheus text and takes the difference between two readings
  around one controlled request. Three rules are enforced rather than assumed:
  metric names are **discovered** from a list of candidates per concept, because
  they move between releases and the task document forbids copying them; a
  concept the deployed engine does not publish reads as **absent**, never as a
  zero somebody could quote; and a delta whose totals went backwards is
  **refused**, because those counters belong to one container's engine and a
  restart between readings means two different engines.
- **The probe is a single continuous pass that analyses nothing while it runs.**
  `tools/vllm_baseline.py` builds every request before contacting anything, then
  loops: read metrics, send, read metrics. The raw readings are saved to
  `reports/vllm_baseline_<stamp>.json` first and the arithmetic happens
  afterwards, from the file, so no number is ever worth a second GPU wake to
  recover.
- **Its default is to do nothing.** With no flag it prints exactly what would be
  sent and contacts nothing. `--run` is the deliberate act — including with
  `--discover`, which only reads `/metrics`, because that endpoint is served by
  the same scale-to-zero container as the model. `--from-file` re-renders a
  saved run and touches nothing.
- **Prefix caching cannot silently answer the prefill question.** Every prompt
  except the repeated-prefix pair begins with a marker no other request in the
  run shares, carrying a per-run tag as well, because the container may still
  hold the previous invocation's blocks. Without that, scenario B's three
  repeats would have measured prefill once and the cache twice, and scenario C's
  prefix would already have been sent by B.
- **GPU seconds and cost are derived when a run is read.** `app/telemetry/cost.py`
  computes the span from the first model request to the last plus the idle
  window, times the configured rate, and the inspector prints it under a heading
  that says derived. Three quantities are kept apart by name — measured model
  request time, derived active time, and the platform's billed time, which is
  not visible from here. `IDLE_WINDOW_SECONDS` mirrors `SCALEDOWN_WINDOW` in the
  deployment and a test keeps the two equal, since that module cannot be
  imported without importing `modal`.
- **`auth_headers` was lifted out of the model backend**, because the metrics
  endpoint sits behind the same Modal proxy auth and a second copy of that logic
  would be a second thing to keep true.

## Two corrections to the preparation

- **The ten-minute window was padding.** The idle window bounds the pause
  *between* requests, not the length of the run. A script that sends back to
  back and analyses afterwards keeps the container alive on twelve seconds, so
  `autoscale.py` is not touched and that gate disappears. The suite is 15
  requests and 1,408 output tokens — roughly two minutes of GPU work, near
  **$0.05**, plus one trailing idle window; a full cold boot instead of a
  restore would add about $0.06.
- **Scenario B stops at ~12k input tokens**, not the task document's 16k: the
  server context is 16,384 and the fixed output needs room. Input sizes are
  targets at four characters per token; what gets reported is the count the
  engine itself made.

## What it cannot answer yet

Which metric names vLLM 0.26.0 actually publishes, whether it exposes a
prefix-cache reset under `VLLM_SERVER_DEV_MODE=1`, and every number the baseline
exists to produce. All of that needs one run.

## Checks

Offline suite: **765 passed, 1 skipped**, up from 744 — 21 new tests, none
removed.

- **Engine metrics** (`tests/test_vllm_metrics.py`, new, 14): counters and
  histogram parts parsed; bucket lines dropped, because summing `le` buckets
  produces a number that means nothing; label sets summed into one series; a
  concept matched to whichever name this version uses; a counter the engine does
  not publish reported missing rather than zero; a delta across a restart
  refused, by falling counters and by a changed process start; only what changed
  appearing in a delta; one request summarized, including a 75% prefix hit rate;
  a missing histogram reading as unknown; a cache nobody queried having no hit
  rate.
- **Derived cost** (`tests/test_gpu_cost.py`, new, 7): the span running from the
  first request to the last rather than from the start of the turn, so GPU-idle
  time waiting for a tool is inside it and CPU queue wait is not; the idle window
  charged to the turn that opened it; both inputs overridable; a turn that
  reached no model having no GPU cost at all; a failed call still costing GPU
  time; the rendered section naming measured and derived and denying it is an
  invoice; and the mirror test that keeps `IDLE_WINDOW_SECONDS` equal to
  `SCALEDOWN_WINDOW`.
- **Dry run executed**: the plan prints 15 requests with their sizes and the
  estimated generation time, and contacts nothing.

Not run: anything touching the endpoint.

## Files

New: `app/telemetry/vllm.py`, `app/telemetry/cost.py`,
`tools/vllm_baseline.py`, `tests/test_vllm_metrics.py`, `tests/test_gpu_cost.py`.

Changed: `app/telemetry/base.py` (`moment`), `app/telemetry/inspect.py`,
`app/models/openai_compatible.py` (`auth_headers`), `tools/show_run.py`,
`docs/CODEMAP.md`, `docs/OPERATIONS_MAP.md`, `ROADMAP.md`.

No schema change, no migration, no new configuration.

## The gate ahead, in one window

One warm window covers everything item 3 still owes:

1. `python tools/vllm_baseline.py --run --discover` — one `/metrics` read, which
   wakes the GPU, to publish the names before the suite depends on them.
2. `python tools/vllm_baseline.py --run` — the suite, about two minutes.
3. Deploy `assistant-control` so the deployed worker records stage detail.
4. One live autonomous task turn, read back with `tools/show_run.py` alone.

Steps 1, 2 and 4 each wake the GPU. Estimated total under $0.20.
