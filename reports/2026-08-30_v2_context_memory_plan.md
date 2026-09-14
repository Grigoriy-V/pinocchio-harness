# Context and memory: reading the addendum against the repository

**Date:** 2026-08-30
**Subject:** `reports/archive/step4_context_memory_addendum_ru.md`, and what it changes in
the Step 4 plan.
**Status:** the analysis is complete; the two plan changes it proposed were
approved by the human on 2026-08-30 and are recorded in `ROADMAP.md` and
`DECISIONS.md`. The GPU boot it needs is not approved and remains its own gate.

The addendum is architecturally right. This report is what it looks like against
the code that exists, which differs from its assumptions in four places — one of
them because our own documentation was wrong.

## 1. What the addendum asks for that is already true

Worth stating, because each of these is a line item that should not be built
twice.

- **Capacity is discovered from the backend, not hardcoded.** `Agent.budget()`
  asks `context_limit()` once, which reads `/v1/models`. The addendum's §4
  requirement is met.
- **Raw history is never destroyed by folding.** `fold_older_messages` writes a
  summary and the position it covers; it deletes nothing. Acceptance items 1, 8
  and 12 of §15 hold today.
- **Some structured state already survives, as of 4.1.** `steps`, `tool_calls`,
  `spent_seconds` and `stopping` in `AgentState`, and `turn_stops` in the
  database. §11 describes the present for budget counters and cancellation
  state. What is genuinely missing from it is `todo`, a pending `ask_user` and
  side-effect receipts — which is 4.4 and 4.5, already in the queue.
- **A token trigger exists.** `fold_older_messages` folds when
  `used_tokens > max_input_tokens`, and `ContextOverflowError` is caught and
  retried in `graph.py`. So §4's "message counts decide compaction" is not
  quite the state of things.

The real gap is narrower and sharper than the addendum states it: the token
trigger reads the **previous** request's reported usage and folds **after** the
turn. It is exact and one turn late. The 4.6a delta is measuring the surface
before the call, not introducing token pressure as an idea.

## 2. The documentation defect this uncovered

`deploy/modal/README.md` said `MAX_MODEL_LEN` "reserves KV cache at start-up",
and the comment above the constant said the same. Both were wrong, and the first
version of this analysis repeated the error to the human.

What is actually true:

- `--gpu-memory-utilization 0.80` sizes the KV pool. vLLM takes 80% of the card
  whatever the ceiling is.
- `--max-model-len` is the longest single sequence the engine accepts. At
  start-up it is only validated against the pool: one sequence that long has to
  fit.
- Therefore raising it from 16,384 costs **no VRAM**. It costs concurrency —
  fewer long sequences share one pool. At roughly 11 GiB of pool and one or two
  users, that is not a constraint.
- What it does cost is one uncached boot. vLLM builds the engine with it, and
  the GPU snapshot captures a built engine, so a new value invalidates the
  snapshot: about 190 s once, then back to about 10.4 s.

The human's question was the right one — the server has already taken the
memory, so why is the ceiling not ours to move. It is ours to move. It is just
a process-start argument, which is a boot, not a deploy, and not a per-change
cost if it is set once and left.

Both files were corrected as part of this work.

## 3. Two collisions inside the addendum

- **Two thresholds would multiply.** §4 proposes `0.80 × capacity` while
  `AGENT_CONTEXT_FRACTION` is already 0.6 of capacity. Naively combined that is
  0.48 and nobody would notice. There must be one threshold; the existing
  configured fraction is the one to keep and to raise.
- **Where a compaction record lives.** §8's `context_compactions` is a source of
  what the model was shown, not a measurement of a turn, so it belongs to the
  memory schema and not to `turn_runs`/`trace_events`. That makes it schema v3
  and a migration of a populated Neon database — its own human gate. Telemetry
  gets an event saying a compaction happened and what it cost, which is cheap
  and needs no schema.

Separately, §10's `search_history` is full-text in two profiles plus an addition
to the `ConversationStore` contract suite. That is not a bullet inside another
sub-step; it is 4.6b.

## 4. The effective number

The limit in force before any of this is **9,830 tokens**: 16,384 at fraction
0.6. Not 16k, and not "a legacy baseline that only shows up in long
conversations". A single turn of the 4.1 loop with a few tool results reaches it.

## 5. Why capacity comes before the tool seam

4.2 implements autonomy inside the workspace, which by design removes the
consent stop between tool calls and therefore increases how many tool results
one turn accumulates. Building it on 9,830 tokens means accepting a product
degradation to keep the plan order, which the primary principle rejects. Hence
4.1.5: raise the ceiling once, unify the threshold, move the pressure check in
front of the model call. No pruning, no summarizer schema, no new tables — those
stay in 4.6a where the tool seam can feed them.

## 6. What a larger context does not solve

Prefill is measured dominant and superlinear
(`reports/2026-08-29_v2_gpu_baseline_measured.md`). A 64k turn that fits is
still a turn that spends real seconds assembling itself. Raising the ceiling
changes what the context engine is for — from avoiding a refusal to avoiding a
bill — and does not make it optional. This is the argument against treating
4.1.5 as a replacement for 4.6.

## 6a. What the measured KV pool allows

**Corrected 2026-08-30 after the boot. The prediction below was wrong; the
measurement follows it. Both are kept because the mistake is the reusable part.**

*Predicted.* Two boot logs appeared to give one constant:
`reports/2026-08-28_v2_step3b_snapshot_boot.md` records 11.06 GiB of pool
holding 86,664 tokens, and the failed boot in
`reports/2026-08-28_v2_step3b_first_boot_failure.md` records 13.77 GiB holding
107,923 — both ≈137 KB per token, both at a 16,384 ceiling. Dividing the pool by
it gave 8.36 GiB for one 64k sequence (1.32x concurrency), and 17.96 GiB for
128k, which no utilization on this card could reach.

*Measured, at `--max-model-len 65536`:*

```text
Available KV cache memory: 11.13 GiB
GPU KV cache size: 256,669 tokens
Maximum concurrency for 65,536 tokens per request: 3.92x
```

The same ~11 GiB of pool holds 86,664 tokens at a 16k ceiling and 256,669 at a
64k one. **KV per token is not a constant for this model**, so it cannot be
carried from one ceiling to another. Gemma 4 does not use the same attention in
every layer, and vLLM sizes a hybrid cache against the layer mix and the ceiling
together rather than as a flat per-token price; the earlier figure was a
property of the 16k configuration, not of the model.

Consequences, stated plainly:

- The real headroom at 64k is about three times what was predicted.
- **"128k is unreachable on the A10" is withdrawn**, and the section below says
  what replaces it.
- The general lesson, which is why this section keeps its own error: a constant
  derived from boot logs that all share one configuration describes that
  configuration. Read the two lines above from the boot that actually ran.

### What the number means, and what it implies for 128k

`GPU KV cache size` is the pool divided by the cost of one token, and the
concurrency line is that divided by the ceiling — literally
`256,669 / 65,536 = 3.92`. The same arithmetic on the older boot gives
`86,664 / 16,384 = 5.29`.

Those two are the whole finding. **Quadrupling the request length cost only 1.35x
of concurrency**, so the price of a request is far from linear in its length.

The mechanism is Gemma 4's attention, which is not the same in every layer: most
layers use a sliding window and need KV for the window rather than for the whole
sequence, while a minority use full attention. Past the window width the
sliding-window layers stop growing. So a request costs `a·L + c`, not `a·L` —
and dividing a pool by a per-token constant, as the withdrawn prediction did,
assumes the `c` away.

Fitting both measured points to that form:

| ceiling | request costs, as a share of the pool | concurrency |
|---|---|---|
| 16,384 | 18.9% | 5.29x, measured |
| 65,536 | 25.5% | 3.92x, measured |
| 131,072 | 34.3% | ~2.9x, extrapolated |

At 128k one full-length sequence would take about 3.8 GiB of the 11.13 GiB
pool. **128k fits on the current A10, unquantized, with room to spare.**

Two honest qualifications. This is still a two-point extrapolation, and the
previous one was wrong — but it was wrong because of its *form*, and this form
at least accounts for both measurements, which the constant-per-token form does
not account for at all. And only a boot settles it.

The reason not to raise it anyway is compute, not capacity. At the measured
~2,000 tokens/s of prefill, a full 128k prompt is around a minute before the
first output token. A 128k ceiling would be cheap insurance — one boot, no VRAM
— but requests that actually use it are not something this product wants to
send, and `AGENT_CONTEXT_FRACTION` would keep spending well below it regardless.
It raises the value of 4.6a rather than lowering it: the higher the ceiling, the
more a prefix-cache miss costs.

**Why `GPU_MEMORY_UTILIZATION` stays 0.80.** Raising it was considered and
declined on the numbers. The failed boot at 0.92 was short by 1.72 GiB. Each
0.01 is worth about 0.226 GiB, so 0.86 removes 1.36 GiB — less than the
shortfall — and the break-even is near 0.844 with no margin at all. 0.80 removes
2.71 GiB and clears the recorded failure by about a gigabyte. Against that risk,
the higher value buys pool that 64k has no use for. The shortfall is an estimate
from a single failure and the cumem allocator's overshoot scales with pool size,
so 0.86 might well boot; it was declined because it might also repeat an OOM
whose retries cost more than one boot, for room nothing needs. The human decided
0.80 on 2026-08-30 after seeing this.

The boot then produced a second opinion on the same question, from vLLM itself:

```text
CUDA graph memory profiling is enabled (default since v0.21.0). The current
--gpu-memory-utilization=0.8000 is equivalent to --gpu-memory-utilization=0.7640
without CUDA graph memory profiling. To maintain the same effective KV cache
size as before, increase --gpu-memory-utilization to 0.8360.
```

So part of what 0.80 gives up is not headroom against the OOM at all — it is
CUDA graph memory that newer vLLM accounts for and older vLLM did not, and 0.836
is where this version reproduces the old effective pool. That is close to the
0.844 break-even derived above by a different route, and it is the number to
probe from if the pool is ever worth raising. It was not raised here, because
3.92x concurrency is already more room than 64k needs.

**Why no KV-cache quantization.** `--kv-cache-dtype fp8` would halve the
constant to about 68.5 KB per token and put 128k inside the current pool at
8.98 GiB — it, not utilization, is what 128k actually needs. It is not in 4.1.5
because 64k does not need it and it carries two unverified costs: the A10 is
Ampere, where fp8 KV is a storage format with backend-dependent support rather
than a hardware path, and it would be quantization on top of an already
`w4a16-qat` checkpoint, whose effect on this product's real profile — images,
audio, tool calls, structured output — is unmeasured. A successful boot would
not be evidence of quality.

## 7. Risks carried into 4.1.5

- **A ceiling the pool cannot hold is a refused boot**, and a refused boot costs
  a boot. The value must come from `Available KV cache memory` in a boot log,
  not from arithmetic in a report.
- **Secondary engine effects are unverified.** vLLM may size `max_num_seqs`,
  CUDA graph capture or chunked prefill against the ceiling, and the snapshot
  may grow. These are to be read from the boot log of the change, not assumed
  either way.
- **The boot is a GPU gate every time.** It is bundled with the NCCL fix already
  owed to the next `assistant-llm-v2` deploy so that it costs one boot and not
  two, but bundling is not authorization.
- **`max_inputs=32` was chosen for a 16k sequence**, where the pool held 5.2 of
  them. At 64k it holds about 1.3, so 32 concurrent inputs into one container
  become preemption and queueing. Not a boot risk — vLLM queues rather than
  fails — but a latency risk under concurrency, and it belongs to the same boot.

## 8. Approved on 2026-08-30

1. A larger context leaves "Out of scope"; the addendum is the product trigger
   the roadmap required.
2. 4.1.5 is inserted before 4.2, and 4.6 splits into 4.6a (context engine,
   absorbing cache-friendly assembly) and 4.6b (exact recovery).
3. 4.1.5 raises the ceiling to 64k only, at utilization 0.80 and without KV
   quantization.
4. A person choosing their own context size from Telegram is product scope, and
   lands in 4.6a rather than 4.1.5 — a smaller budget is only a good trade once
   compaction enforces it, and it is a cost dial as much as a memory dial.
5. 128k moves to a separate future comparison on different hardware: L40S with
   Qwen3-8B and quantized KV, recorded in "Out of scope" and not begun.

Recorded as durable choices: stored history is canonical and the model surface
is a projection; the engine ceiling is set once and context is spent by the
application. Both in `DECISIONS.md`, 2026-08-30.

Not approved and not begun: the boot itself.
