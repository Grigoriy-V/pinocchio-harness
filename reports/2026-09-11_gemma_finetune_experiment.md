# Roadmap 24: Gemma 4 12B fine-tuned on this harness's own turns

An experiment for experience and a portfolio piece (the human,
2026-09-11; DECISIONS 2026-09-11). Not a product step: the assistant's
model stays GLM 5.3 Flash. What this file carries: the counts, the
choices as options until the human picks, and every measured number.
There is no local GPU; every run is a Modal worker, priced and gated.

## 1. What the deployed store holds today (read-only, 2026-09-11)

Telemetry runs: 440 since 2026-08-29. `answer_delivered`: 418, of which
313 used a tool. Since 2026-09-06, when GLM became the model (the earlier
turns were Gemma 4 and Qwen3.8 through the GPU Apps, not a teacher):

| | turns |
|---|---:|
| delivered since 2026-09-06 | 145 |
| … with at least one tool call | 111 |
| … of them from Telegram / from `loop_live` | 32 / 79 |
| model calls per tool turn | 2: 46, 3: 25, 4: 11, 5: 11, 6–10+: 18 |
| input tokens per tool turn, median (all calls summed) | 15,014 |

**The rows are not all there.** The 111 turns sit in 24 threads holding
588 message rows in all. The scenario runner's probe threads (`chat-a`,
`chat-m2`, …) are reset each run, so of the 79 `loop_live` turns only the
last batch's rows survive (8–15 rows a thread). What can be rebuilt into a
trajectory today: the 32 Telegram turns of the owner and the first outside
person, plus about ten scenario turns of the latest run — **some forty
trajectories.** The telemetry trace has every call's tokens and every
tool's name and status, but not the text.

Consequence: the data must be collected at run time, as the turns are
made, into a store of its own that nothing resets — and most of it has
to be generated (step 3), not mined.

## 2. Options for step 2, the metrics and the baseline — for the human

Metrics proposed, each a number the suite already produces or a one-line
check on the trace:

1. **Parse rate:** tool calls the harness could execute over tool calls
   the model emitted (ISS-0001 on Gemma; `tool_failed` with a parse code
   against `tool_started`).
2. **Finish rate:** scenarios passed on the mini set and the wider
   letters, `--deployed`, one run each.
3. **Steps to finish:** model calls per passed scenario, against GLM's.
4. **Unobserved claims:** the checks that already exist for ISS-0004's
   shape ("the answer uses the result", "nothing was written or
   retried"), counted.

Baseline: Gemma 4 12B through `assistant-llm-v2` (deployed, scaled to
zero) as `MODEL=v2`, the mini set once (≈ 11 turns) and the wider letters
once. Cost: the GPU App's A10 minutes for the runs plus its cold start; a
gate.

## 3. Options for the data — for the human

- **Rejection sampling with GLM over the scenario prompts:** `loop_live`
  scenarios, and 19's new ones written for this, run N times each
  through `scenarios` deployed; a turn is kept when every check of its
  scenario passed and no tool failed (turns with one failed tool and a
  recovery kept on purpose, marked). Each run is ≈ 11 model turns at GLM
  prices — cents; a hundred runs is a few dollars.
- **The real turns** of the owner that passed (no `tool_failed`, answer
  delivered): kept as they are, marked as real.
- **Where it lands:** a JSONL of trajectories written by the runner at
  the end of each turn, one record per turn with the request as the model
  saw it (the surface: prelude, history, turn) and the model's outputs,
  under a path that nothing resets (`reports/finetune/` or a Volume
  folder); the schema in the report before the first record.

## 4. Research before step 4: the training run on Modal

Open, to be answered by reading, not running: Unsloth against TRL + peft
for Gemma 4 on a Modal A100; the sequence length the turns need (the
request is 5–20k tokens with the prelude, so 16k at least, which on a
12B model means A100 80 GB with QLoRA and gradient checkpointing, or the
schemas cut from the prelude); assistant-only loss masking with Gemma's
chat template and tool-call format; the price of one epoch over a few
hundred trajectories (an hour of A100 80 GB is a few dollars).
