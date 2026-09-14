# V2 step 3b — first restored cold start, and an audio dependency gap

**Date:** 2026-08-28
**Agent:** claude
**Outcome:** the snapshot delivers the intended speedup. One previously
unverified modality exposed a missing dependency; its fix is implemented but
unverified. A second restored wake is still required before cold-start
acceptance.

Builds on `reports/2026-08-28_v2_step3b_snapshot_boot.md`, which created the
snapshot but only measured a warm-container wake, not a genuine cold one.

## What was measured

`scripts/measure_endpoint_wake.py --auth bearer` against `assistant-llm-v2`
after `modal container list` had already confirmed zero active containers —
this run is a real restored cold start, not a warm re-check.

| | This run | Baseline (`assistant-llm`) |
|---|---|---|
| Request-to-serving | **25.0 s** | 189-201 s |
| Text, warm | 200, 1.67 s, 15.0 tok/s | 1.8-2.4 s, ~13 tok/s |
| Image, warm | 200, 3.81 s, correctly identified a red circle | not exercised in 3a |
| Audio, warm | **500: `Please install vllm[audio] for audio support`** | worked (2026-08-01 local smoke) |
| Bearer token on `.modal.run` | **accepted** | established on `.modal.direct` only |

25.0 s is roughly an 8x reduction against the unsnapshotted baseline, which is
the number step 3b exists to produce. **One restored wake is not two**; Modal
may build several worker-type-specific snapshots, so this is not yet an
acceptance-grade result on its own.

The bearer-token question from `reports/archive/modal_platform_notes.md` is answered: a
proxy token joined by a period works as an ordinary bearer token on this
`.modal.run` endpoint, the same as it did on the baseline's `.modal.direct` one.
`OpenAICompatibleBackend` needs no change to reach either shape.

## The audio dependency gap

Root cause, found immediately rather than guessed at: `deploy/modal/model_app.py`
installed plain `vllm==0.26.0`. `reports/2026-08-01_gemma4_endpoint_smoke.md`
already recorded, from the local WSL server, that this binds a placeholder audio
module at import time and that installing `av`/`scipy`/`soundfile`/`soxr`
separately does not fix it — only the `vllm[audio]` extra does, and only if
present before the server starts.

This is not a new defect introduced by the snapshot work. The baseline
`assistant-llm` has the same plain `vllm==0.26.0` install and was never sent an
audio request during step 3a or since — its own report lists it as untested.
The image inherited the omission; nothing regressed relative to a working
baseline configuration, because that configuration was never exercised.

**Fix applied:** `vllm[audio]==0.26.0`, one line in `deploy/modal/model_app.py`.
**Redeployed, not yet verified.** The updated image was deployed without
starting a container. A paid boot is still required to confirm the dependency,
and that boot doubles as the second restored-wake measurement step 3b already
needs.

## State

- `assistant-llm-v2`: redeployed with `vllm[audio]==0.26.0` and
  `scaledown_window=30`; zero active containers after deployment. The new image
  has not yet been invoked, so audio remains unverified.
- `assistant-llm`: deployed, zero containers, still serving `MODEL_ENDPOINT`.
  Untouched, and its own audio support remains unverified on Modal — only the
  2026-08-01 local WSL run confirmed it.
- No change to `app/`, `MODEL_ENDPOINT`, or anything Telegram-facing.

## Next

1. Run the second restored-wake
   measurement, this time with `--auth headers` to also confirm the two-header
   form still works. Verify audio succeeds this time.
2. Only after two consistent restored-wake numbers does step 3b have
   acceptance-grade evidence. Keep `SCALEDOWN_WINDOW = 30` as the product
   default unless measured usage justifies a different cost/latency tradeoff.

## Redeploy update

The first CLI attempt built image `im-R6yR9n26RHc05McUqNNuWc` in 45.73 s but
failed locally while printing a Unicode check mark through the Windows legacy
console codec. It did not update the App. Repeating the same deploy with
`PYTHONUTF8=1` reused the image and completed in 5.433 s.

- App: `assistant-llm-v2` (`ap-RTGuR9opYgu9usWcPeJcXb`)
- URL: `https://grigoriy-v--assistant-llm-v2-server-serve.modal.run`
- active tasks after deploy: 0
- active containers after deploy: 0
- GPU work and model calls: none

## First invocation after the audio redeploy

The first invocation of the new image was authorized and sent with
`--auth headers`. It did **not** provide the planned second independent restored
cold-start measurement: adding `vllm[audio]` changed the image and invalidated
the prior snapshot, so Modal first rebuilt the GPU snapshot.

The server log gives the actual sequence:

- the first request was enqueued at 07:54:43;
- vLLM reached health after **162.2 s**;
- weights loaded in 6.17 s and occupied 8.28 GiB;
- the engine exposed **11.06 GiB** of KV cache (86,664 tokens);
- the updated GPU snapshot was created at 07:58:13;
- the function then restored from that snapshot and reported
  `resume: healthy after 0.0s`;
- the queued `/v1/models` request completed after **3m34s** total, of which the
  final restore/serve phase was about **23.8 s**;
- text, image and audio completion requests all returned HTTP 200 in 0.71 s,
  1.50 s and 4.53 s respectively;
- the container shut down after the 30-second scale-down window and
  `modal container list --json` returned no containers.

The strict client reached the audio request, which proves that the preceding
text and image semantic checks passed. The local command wrapper timed out
while Modal was still rebuilding the snapshot and lost the detached client's
final stdout/exit code. Therefore the audio dependency gap is fixed at the HTTP
execution level, but the exact transcript assertion (`travel`) is not retained
as acceptance evidence from this run.

After restore, the single-GPU vLLM process repeatedly logged a NCCL TCPStore
`Broken pipe` heartbeat warning until shutdown. It did not prevent any of the
three 200 responses, but it remains a runtime warning to watch on the next
independent restored wake.

The next paid invocation should now use the newly created snapshot. It is still
required to retain the strict client's exit code and all three semantic results;
only that run can serve as the second restored-wake acceptance measurement.

The measurement client no longer creates a new `/v1/models` task every minute.
The 60-second client timeout had abandoned each local wait without cancelling
its still-pending Modal task, and the retry loop then submitted another one.
`wake()` now waits on one request for the full measurement budget and treats any
transport failure as terminal. Offline tests assert both the single request and
the no-retry failure path.

## Strict multimodal run after the duplicate-task fix

A separately authorized invocation used the unchanged audio-capable deployment
and the fixed single-request client:

```text
uv run python scripts/measure_endpoint_wake.py \
  --url https://grigoriy-v--assistant-llm-v2-server-serve.modal.run \
  --auth headers
```

The client completed with exit code 0 and retained all semantic assertions:

| Check | Result |
|---|---|
| Request to serving | **446.5 s**, HTTP 200 |
| Text | **1.64 s**, 15.2 tok/s; returned red, blue and yellow |
| Image | **2.75 s**; identified a red circle |
| Audio | **5.03 s**; transcribed the travel-series sentence |
| Scale to zero | confirmed; container list was empty on the second read-only check |

This is not the expected second restored-cold-start result. The Modal dashboard
marked the first container **Failed**. The single client request remained
pending, and Modal continued it on another container; this is platform retry,
not the duplicate client-request bug that was fixed earlier. The later container
again logged `Creating GPU memory snapshot for Function` and vLLM executed its
full startup, including weight loading and Torch/Dynamo compilation. The
apparent pause after `Dynamo bytecode transform time: 6.26 s` was not a deadlock:
at 01:13:54 the next log recorded that the compiled graph had been cached, and
the request later served successfully. The 446.5 s client time therefore
includes a failed first container, platform retry/wait and the successful full
snapshot-build path.

The NCCL TCPStore `Broken pipe` heartbeat warning also repeated around snapshot
handling and until shutdown. It did not break the three requests served by the
successful container, but it is now a reproducible runtime warning rather than
a one-off observation. The retained CLI logs do not expose a definitive fatal
traceback for the first container, so the warning is correlated with the failure
but is not yet proven to be its cause.

The deployment remains at `scaledown_window=30`. One read-only container-list
check 35 seconds after the client exited still showed the container; a second
check roughly 30 seconds later returned `[]`. This confirms eventual
scale-to-zero, not an exact 30-second observed shutdown latency.

**Decision:** do not pay for another nominally identical wake. First determine
from current Modal documentation, the dashboard status and retained logs why
the first container failed, why the unchanged deployment is producing multiple
GPU snapshot builds and how snapshot variants are selected. Any next invocation
must test one explicit hypothesis.

## Final 60-second restored-wake control

The human authorized one final control with a strict 60-second client ceiling.
The measurement utility gained a `--wake-timeout` argument so this limit is
explicit and testable rather than a shell-process timeout. The command was:

```text
uv run python scripts/measure_endpoint_wake.py \
  --url https://grigoriy-v--assistant-llm-v2-server-serve.modal.run \
  --auth headers \
  --wake-timeout 60 \
  --skip-modalities
```

The conditional cancellation path was not needed:

- the single `/v1/models` request returned HTTP 200 in **10.4 s**;
- Modal logged `resume: healthy after 0.0s`;
- the route log measured 8.64 s with 15 ms execution;
- there was no new `Creating GPU memory snapshot` event for this invocation;
- the required text check returned the three primary colours in **1.47 s** at
  17.0 tok/s;
- the script exited 0;
- a read-only container check after 35 seconds returned `[]`.

This proves that the audio-capable snapshot created by the preceding expensive
run is reusable. It does not explain why the preceding invocation first failed
one container and rebuilt a snapshot, but another identical paid wake is no
longer justified merely to establish whether restore can work.

The NCCL TCPStore `Broken pipe` warnings are independently reproducible: they
began around restore and repeated every second through the successful request
and idle period. They did not affect correctness or the 10.4-second wake, but
they materially pollute the logs. Their cause and suppression are the next
read-only investigation; no worker should be started solely for that diagnosis.
