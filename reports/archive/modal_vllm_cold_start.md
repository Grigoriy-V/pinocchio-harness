# Serving vLLM on Modal: cold start and throughput

Reference for tuning `deploy/modal/model_app.py`. It records what the platform
and vLLM documentation actually say, which of it is applied, and what is still
untried — so the next change is a decision rather than a guess.

Measurements and the failure history are in
[`reports/2026-08-28_v2_step3a_model_endpoint.md`](../reports/2026-08-28_v2_step3a_model_endpoint.md).
Platform facts that are not specific to vLLM are in
[`modal_platform_notes.md`](modal_platform_notes.md). `ROADMAP.md` is the plan;
this file is neither.

## Current state

The deployed `assistant-llm` is the measured, unsnapshotted baseline. A read-only
Modal audit on 2026-08-28 confirmed that its launch command has no
`--enable-sleep-mode`, and its logs contain no `/sleep`, `/wake_up` or snapshot
restore.

`deploy/modal/model_app.py` now defines a different App, `assistant-llm-v2`, so
a deploy of this file can no longer overwrite the baseline that produced the
measurements below. The CPU+GPU snapshot implementation there is complete and
checked offline — it registers with the installed Modal client, its readiness
paths are unit-tested, and its scaling bounds are asserted — but it has never
been deployed or invoked. Nothing below the "Expectations" heading is a claim
about it; it has no measurements at all.

## What is being optimized

A wake measured **189 s** from request to ready, split by a timestamp in
`@modal.enter` and vLLM's own log:

| Stage | Time | What addresses it |
|---|---|---|
| Container scheduling, image availability, Python start | ~15–25 s | platform/cache dependent; not the first target |
| Subprocess start, torch/vLLM imports and configuration | ~77 s | CPU+GPU memory snapshot |
| Checkpoint read | 6.8–6.9 s | already fast; prefetch is a later A/B |
| Engine profiling, KV cache, compilation and CUDA graphs | 58 s on the latest boot | GPU snapshot, or `--enforce-eager` fallback |
| Route registration | 5 s | — |

The latest recorded server process took about 172 s from `vllm serve` to a ready
HTTP server; the earlier end-to-end wake measured 189–201 s. The exact stages do
not sum to one universal total because they come from two recorded boots, but
both show the same priority: rebuilding the image or relocating weights would
target the small part. The expensive repeatable initialization after process
start is what snapshots are meant to skip.

Separately, generation measured **~13 tok/s** on an A10 for a short answer.

## Sources

Modal:

- [examples/vllm_inference](https://modal.com/docs/examples/vllm_inference) —
  the OpenAI-compatible server: `@app.server`, the CUDA devel base image,
  `--revision` pinning, `@modal.exit()`, and the `FAST_BOOT` switch with the
  rule for choosing it. Its health check treats 503 as "keep waiting", which is
  the documented client contract for a Server.
- [examples/ministral3_inference](https://modal.com/docs/examples/ministral3_inference) —
  the same server plus GPU snapshots. This is the template for the current
  implementation.
- [examples/vllm_throughput](https://modal.com/docs/examples/vllm_throughput) —
  in-process `vllm.LLM` with a warmup request. Not used: the snapshot template
  keeps the subprocess.
- [guide/memory-snapshots](https://modal.com/docs/guide/memory-snapshots) —
  `snap=True` vs `snap=False`, `experimental_options={"enable_gpu_snapshot": True}`,
  the `torch.compile` conflict and `TORCHINDUCTOR_COMPILE_THREADS=1`.
- [guide/high-performance-llm-inference](https://modal.com/docs/guide/high-performance-llm-inference) —
  Volumes read at 1-2 GB/s, so roughly a second per gigabyte of weights; skip
  compilation at startup, or snapshot around it.
- [guide/cold-start](https://modal.com/docs/guide/cold-start) — pre-download
  weights, move work into `enter`, `scaledown_window`, `min_containers`.
- [guide/custom-container](https://modal.com/docs/guide/custom-container) —
  images are cached per layer, per `Image` method call, and breaking one layer
  cascades. Frequently-changing layers go last.
- [guide/servers](https://modal.com/docs/guide/servers) — a Server with no
  container answers 503 and does not queue, unlike Function inputs.
- [pricing](https://modal.com/pricing) — Volume storage $0.09/GiB/month with
  1 TiB free; an idle scaled-to-zero app has no compute cost.

vLLM and the model:

- [gemma4_mm.py](https://raw.githubusercontent.com/vllm-project/vllm/v0.28.0/vllm/model_executor/models/gemma4_mm.py) —
  `get_dummy_mm_data` reads `processor.feature_extractor.fft_length` only when
  audio is the profiled modality. See the report for why that matters.
- [google/gemma-4-12B-it-qat-w4a16-ct](https://huggingface.co/google/gemma-4-12B-it-qat-w4a16-ct) —
  the served checkpoint, 10.3 GB, one shard, not gated.
- [google/gemma-4-12B-it-qat-q4_0-unquantized-assistant](https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-unquantized-assistant) —
  a draft model for speculative decoding, not gated. Untried.

## Implemented locally, not deployed

- **GPU memory snapshot.** `@app.cls` with `enable_memory_snapshot=True` and
  `experimental_options={"enable_gpu_snapshot": True}`. `@modal.enter(snap=True)`
  starts vLLM, warms it with three real requests so compilation and graph
  capture happen there, then calls `/sleep`; `@modal.enter(snap=False)` calls
  `/wake_up`. Sleep moves GPU memory into CPU memory, which is what gives the
  snapshot something to capture.
- **`--enable-sleep-mode`** and **`VLLM_SERVER_DEV_MODE=1`**, without which
  `/sleep` and `/wake_up` do not exist.
- **`TORCHINDUCTOR_COMPILE_THREADS=1`**, the documented mitigation for
  `torch.compile` failing snapshot creation.
- **The subprocess is kept.** A GPU snapshot captures the container, so vLLM
  does not need rebuilding in-process and no vLLM internals are imported.
- **CUDA devel base image with `.entrypoint([])`** to clear the base image's own
  entrypoint, and `add_python` because CUDA images ship without Python. A
  `debian_slim` build left `vllm._C` missing.
- **Layer order**: `uv_pip_install` before `.env(...)`, so editing an
  environment variable rebuilds only the last layer. Redeploys measured 3-9 s.
- **Weights preloaded** into a Volume by a CPU function, so GPU time is never
  spent downloading.

- **Bounded readiness on `/health`.** Both `@modal.enter` hooks wait for vLLM to
  report healthy rather than for the port to bind, under a deadline
  (`START_READY_TIMEOUT` 600 s, `WAKE_READY_TIMEOUT` 300 s) that sits inside the
  15-minute container timeout. Each wait prints its elapsed seconds, which is
  where restore-to-health comes from, and each failure names its cause: the
  subprocess exited with a return code, or the budget expired while it was still
  running. Covered offline by `tests/test_model_endpoint.py`.
- **A separate App identity**, `assistant-llm-v2`, so deploying this file cannot
  replace the measured baseline.
- **Explicit autoscaling**: `min_containers=0` keeps scale-to-zero a stated
  decision rather than a platform default, `max_containers=1` caps the cost of a
  private service, and `scaledown_window=30` prioritizes scale-to-zero now that
  the first restored cold start measured 25 seconds.

## Not applied, and why

- **`FAST_BOOT` / `--enforce-eager`.** The vLLM example's rule is to set it
  `True` for a service that frequently scales from zero, which describes this
  one. It is `False` because the snapshot pays for compilation once instead of
  per wake. **If GPU snapshots do not work, this becomes required rather than
  optional.**
- **Speculative decoding.** The vLLM example runs a draft model through
  `--speculative-config`, and Google publishes one for this family. It targets
  the 13 tok/s, not the cold start, and it costs VRAM: 8.28 GiB of weights and
  10 GiB of KV cache already sit in 22 GiB, so the cache would shrink. Separate
  change, separate measurement.
- **`--async-scheduling`.** Present in the example, not carried over. Throughput,
  not cold start.
- **`min_containers`.** Removes cold starts entirely and costs about $26/day.
  A positive value contradicts the reason for deploying this way; set it
  explicitly to zero rather than relying on a platform default.
- **Raising `--max-model-len`.** vLLM reports room for 11.6 GiB of KV cache
  against 10.03 GiB in use at 16384 tokens, so the ceiling can rise a long way.
  Unrelated to cold start.

## Carried without justification

`VLLM_USE_V2_MODEL_RUNNER=0` and `VLLM_USE_FLASHINFER_SAMPLER=0` come from the
validated local configuration. The 2026-08-01 report calls both WSL-specific
workarounds — missing UVA and missing `nvcc` — and explicitly "none of them
model-related", so they are probably unnecessary on Modal. Remove them one at a
time against a measurement, not together.

## Expectations to hold this to

Modal documents practical 3–10x improvements and notes that GPU Functions may
create two or three worker-type-specific snapshots during their first few
invocations. A single wake after a deploy proves nothing. GPU snapshots are
Alpha; if they fail, `FAST_BOOT` / `--enforce-eager` becomes the measured
fallback rather than an assumed improvement.

## Ordered execution plan

Each deploy and every action that creates a worker remains a separate human
gate.

1. **Done.** The local definition: new App identity, bounded health readiness,
   explicit 0→1 scaling and a 30-second idle window.
2. **Done.** Static and offline checks — the module registers with the Modal
   client, `tests/test_model_endpoint.py` covers the readiness paths and the
   scaling bounds, and the full suite and `ruff` are clean — plus the CPU
   `preflight`, which passed on `assistant-llm-v2` on 2026-08-28: `vllm 0.26.0 /
   transformers 5.14.1`, `head_size=512`, `video` still unset. No GPU was
   involved and no model was called. That run also established that snapshots
   cannot be validated by `modal run`; see `modal_platform_notes.md`.
3. Deploy the replacement without changing `MODEL_ENDPOINT`.
4. With explicit permission, invoke it once to create a snapshot and record the
   full first-boot log.
5. Let it scale to zero, then separately authorize at least two restored cold
   wakes. Confirm snapshot creation/restore in Modal, not from latency alone.
6. Record request-to-ready, restore-to-health, TTFT, full answer latency,
   tokens/s, VRAM and cost against the existing baseline.
7. Verify ordinary text, structured output/tool calling, then one image and one
   audio input. Add multimodal snapshot warmup only if evidence shows first-use
   initialization after restore.
8. If snapshots fail or provide insufficient benefit, test `FAST_BOOT=True` as
   a new run identity. Only afterward consider safetensors prefetch, image
   changes or removing inherited WSL variables, one variable at a time.
9. Switch the application endpoint only after backend and Telegram acceptance.
   Keep deletion/retirement of the baseline as a later destructive gate.
