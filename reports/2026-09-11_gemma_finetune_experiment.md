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

5. **An LLM judge — the main one** (the human, 2026-09-11). In the first
   version the judge is the project agent (Claude) reading each turn's
   thread (`tools/show_thread.py`) and trace (`tools/show_run.py`) against
   a fixed rubric, the same for every model and run, written here before
   the first turn is judged. Rubric, 0–2 each, summed to 0–10: (a) did
   what was asked, not something near it; (b) used a tool where the task
   needed one and not where it did not; (c) every claim in the answer
   matches something observed in a tool result; (d) recovered from a
   failed call instead of repeating it or giving up; (e) the answer a
   person could act on — complete, no filler, no leaked internals. The
   judge sees the model's name only after scoring a batch. Later versions
   can hand the rubric to a model call with the same text.

Approved 2026-09-11 (the human): metrics 1–5 as above.

Baseline: Gemma 4 12B through `assistant-llm-v2` (deployed, scaled to
zero) as `MODEL=v2`, the mini set once (≈ 11 turns) and the wider letters
once. Cost: the GPU App's A10 minutes for the runs plus its cold start; a
gate. Needed first: `scenarios` takes the set's name, so a run can be
pointed at Gemma without changing the deployment's own model.

## 2a. Step 2 done: Gemma 4 12B's baseline on the mini set, 2026-09-11

`loop_live --deployed --model v2`, runs `deployed-v2-bb1f9594-*`, the
GPU App woken from its snapshot; 8/8 scenarios passed, 11 turns in about
2.5 minutes of wall clock. GLM's last mini set (`deployed-4b4c7f59-*`,
2026-09-10) beside it. The threads are kept whole in
`reports/2026-09-11_gemma_baseline_threads.txt`, because the next scenario
run resets them (§1).

| metric | Gemma 4 12B (A10) | GLM 5.3 Flash |
|---|---:|---:|
| 1. parse rate: tool calls executed / emitted | 28 / 28, no parse failure (the one `tool_failed` is E's intended `fs.ambiguous_edit`) | 28 / 28 |
| 2. finish rate, mini set | 8 / 8 | 8 / 8 |
| 3. model calls over the 11 turns (A B C F W H×3 E M×2) | 39: 1 3 8 6 2 2 2 2 2 8 3 | 31: 1 2 4 5 2 2 1 2 2 7 3 |
| 4. unobserved-claim checks failed | 0 | 0 |
| 5. judge (below) | 9.7 / 10 mean | not judged: its rows were reset by this run |
| first model token, ms, per turn | 1.7–4.0 s; 8.3, 10.4, 11.9 on the first three; 52.6 s on C's 8th call | 4–8 s (hosted) |
| input tokens, all turns | 204k | 172k |

Where Gemma's extra calls went: `set_goal` first on C, F and both M turns
(4 calls GLM did not make), `list_files` before `read_file` on B when the
path was given, `search_memory` on H-81 where the fact was already in the
context, and on C one failed run of `primes.py` (`tabulate` handed a flat
list) fixed on the next call. It chose `view_web_page` on W where the
routing line says `use_page`; the check accepts either.

**Judge, first pass** — the rubric of §2, scored by the project agent
(Claude) from the kept threads. Blind scoring was not possible for this
batch: the agent ran it. Scores (a b c d e = total):

| scenario | a | b | c | d | e | total | note |
|---|:-:|:-:|:-:|:-:|:-:|---:|---|
| A greeting | 2 | 2 | 2 | 2 | 2 | 10 | |
| B one tool | 2 | 1 | 2 | 2 | 2 | 9 | `list_files` before a named file |
| C venv, package, script | 2 | 2 | 2 | 2 | 2 | 10 | the traceback read and the fix right; output quoted verbatim |
| F browser | 2 | 2 | 2 | 2 | 2 | 10 | |
| W web | 2 | 1 | 2 | 2 | 2 | 9 | the reading tool for a read, not the page tool the line names |
| H-80 remember | 2 | 2 | 2 | 2 | 2 | 10 | |
| H-81 recall | 2 | 1 | 2 | 2 | 2 | 9 | a search for a fact already in the context |
| H-82 quote the error | 2 | 2 | 2 | 2 | 2 | 10 | quoted the stored line as stored, doubled text included |
| E failing tool | 2 | 2 | 2 | 2 | 2 | 10 | said what happened, did not rewrite |
| M-180 mid-turn message | 2 | 2 | 2 | 2 | 2 | 10 | "12 times 12 is 144" beside the next call, then finished |
| M-190 stop | 2 | 2 | 2 | 2 | 2 | 10 | |

Mean 9.7. **Reading:** on this mini set Gemma 4 12B is already at the
ceiling of every counting metric and one point under it on the judge, and
the point it loses is one extra tool call. The mini set was written to
catch a harness that lies, not to separate two capable models: it cannot
show a fine-tune's effect. What can: the wider letters (G I J K O P Q R S,
longer tasks, one that kills the agent, one that needs a plan), and the
new scenarios of 19 written with room at the top — multi-step tasks with
a wrong turn in them, where steps-to-finish and the judge's (b), (c), (d)
spread. The baseline on the wider letters is the next gate; GLM's judged
run needs step 3's capture first, since a scenario run erases the
previous one's rows.

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
