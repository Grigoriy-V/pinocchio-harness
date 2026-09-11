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

Mean 9.7.

**GLM, the same mini set, judged** — run `deployed-6ac3ecc0-*`, 2026-09-11
12:19 UTC, the first run with the trajectory capture on (11 files in
`.trajectories/`); threads kept in `reports/2026-09-11_glm_mini_threads.txt`.
Counting: parse 18/18; 7/8 scenarios by the checks — C failed "write_file
then run_command" because GLM did the whole task in **one** `run_command`
(mkdir, venv, pip, `printf > primes.py`, run) and every other check of C
passed; model calls 29 (1 2 2 5 2 2 1 2 2 7 3); 0 unobserved claims;
first token 4.5–37 s on the hosted endpoint against Gemma's 1.7–4 s on the
A10. Judge:

| scenario | a | b | c | d | e | total | note |
|---|:-:|:-:|:-:|:-:|:-:|---:|---|
| A | 2 | 2 | 2 | 2 | 2 | 10 | |
| B | 2 | 2 | 2 | 2 | 2 | 10 | straight to `read_file` |
| C | 2 | 1 | 2 | 2 | 2 | 9 | wrote the file through the shell, not `write_file`; output quoted verbatim, the leading spaces explained |
| F | 2 | 2 | 2 | 2 | 2 | 10 | |
| W | 2 | 2 | 2 | 2 | 2 | 10 | `use_page`, as the line names |
| H-80 | 2 | 2 | 2 | 2 | 2 | 10 | |
| H-81 | 2 | 2 | 2 | 2 | 2 | 10 | answered from the context, no call |
| H-82 | 2 | 2 | 2 | 2 | 2 | 10 | quoted verbatim, then said what it meant |
| E | 2 | 2 | 2 | 2 | 2 | 10 | said what happened, offered the next step, did not do it |
| M-180 | 2 | 2 | 2 | 2 | 2 | 10 | "12 × 12 = 144. Continuing:" beside the next call; listed all three files |
| M-190 | 2 | 2 | 2 | 2 | 2 | 10 | |

Mean 9.9 against Gemma's 9.7: the whole difference is Gemma's extra tool
calls. C's failed check is a note for 19, not for either model: the check
tests the route (`write_file` first) where it should test the outcome (the
file exists in a folder, the venv holds the package, the output is
quoted); a model that writes through the shell did what was asked.

**Reading:** on this mini set Gemma 4 12B is already at the
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
- **Where it lands — built 2026-09-11 (the human: "давай второе").**
  `app/trajectories.py` and `TurnTrace.trajectory`: every model call, on
  every path that has a trace (Telegram worker, `scenarios`, the local
  app), is one JSON line in `<AGENT_TRAJECTORIES>/<run_id>.jsonl` — one
  file per run so parallel workers never share a file on the Volume.
  Record: `run_id`, `thread_id`, `call_index`, `model`, `messages` (the
  request as sent: roles, text parts, media as kind and size, tool calls,
  tool results with their failure codes), `tools` (the schemas offered),
  `completion` (text, tool calls, finish reason, usage). Deployed the
  folder is `/workspaces/.trajectories` on the workspaces Volume, which
  the worker and `scenarios` already commit after each turn; nothing
  resets it. Off when the setting is empty. Offline test:
  `tests/test_turn_telemetry.py::test_every_model_call_is_kept_as_the_model_saw_it`.
  A training sample is one line: prompt = `messages` + `tools`,
  completion = `completion`; a turn is its lines in `call_index` order.
  Deploy pending (gate); the first records come from the next scenario
  run, GLM's, which also gives the judge GLM's rows.

## 3a. The seven families, first live run on GLM, 2026-09-11 12:52 UTC

`loop_live --deployed D L N T U V X`, runs `deployed-176d001a-*`, 21 turns
in about nine minutes; threads kept in
`reports/2026-09-11_families_glm_threads.txt`; 21 more trajectory files
on the Volume (32 in all). **21/21 by the checks on the first run**, 71
tool calls, no `tool_failed`. Model calls per case: D 8 / 3 / 8, L 4 / 3 /
2, N 3 / 4 / 3, T 2 / 2 / 2, U 5 / 4 / 3, V 4 / 3 / 4, X 4 / 4 / 4;
seconds 10–59, first token 9–58 s on the hosted endpoint.

Judge (the rubric of §2, from the kept threads; scored by the agent that
ran it, so not blind): 20 turns at 10, one at 9 — **D2**, where GLM met
`ModuleNotFoundError: yaml` and ran `pip install pyyaml` into the
machine's own python, against the brief's rule that a task's packages go
in a venv in the task's folder; it said so in the answer, and the check
passed because the outcome was right. That is a rubric-(b) point, and a
kind the outcome checks cannot see: **following the brief's standing
rules** is a judge item, not a file check. Everything else was clean:
D1 found the relative-path cause, then the `abc` row, and said both; D3
fixed the test in two edits and left `calc.py`; T1 renamed through `mv`
after an `ls` of names only; U1 checked the trailing newline before
appending; V1 read the script, ran it, listed the folder and called the
message "a lie in the script"; V2 counted the `<li>` by `evaluate` and
named the title's overstatement; X1 answered "Lisbon" beside its next
calls and finished; X2 wrote the package and the tests in one heredoc
command and ran them green.

What this says: the families are **data generators**, not a stick that
separates GLM from the ceiling — as teacher it passes them at ~100 %, so
rejection sampling on them keeps nearly every run and costs cents. Whether
they separate a 12B model is Gemma's run on the same letters (a gate). For
the data's diversity two things matter before `--repeat`: the sampling
temperature of the deployed set (a repeat at temperature 0 is the same
trajectory again) and parameterized seeds — the same family with other
numbers, names and file layouts — which is the next thing to build if
repeats come back alike.

## 3b. The seven families on Gemma 4 12B, 2026-09-11 13:40 UTC — the first gap

`loop_live --deployed --model v2 D L N T U V X`, runs
`deployed-v2-aaa50fb0-*`; threads in
`reports/2026-09-11_families_gemma_threads.txt`. Beside GLM's run (§3a):

| metric | Gemma 4 12B | GLM 5.3 |
|---|---:|---:|
| cases passed by the checks | 18 / 21 (19 after the T3 check fix below) | 21 / 21 |
| model calls, all 21 cases | 151 | 79 |
| tool calls | 126 | 71 |
| `tool_failed` | 1 `fs.not_found`, 4 `not_run` (the repeat guard) | 0 |
| turns ended by the repeat guard | 1 (X2, nothing said) | 0 |
| first token | 4–19 s, three at 30–84 s | 9–58 s |
| judge, mean | **9.0** | **9.7** (see the rule below) |

The three failures:

- **V1** — the build's lie. Gemma checked `tools_task/out` (not found),
  then ran `bash tools_task/build.sh && find . -name app.bin` **eighteen
  times** with cosmetic variations, tripped the repeat guard once, and only
  then answered — correctly, but hedged ("possible the file is being
  written to a different location"). 23 model calls, 90 s. GLM: read the
  script, ran it, listed the folder, four calls.
- **X2** — the package. Gemma put the test file *inside* `calc_pkg/`, ran
  `cd calc_pkg && python -m unittest test_calc.py`, got
  `ModuleNotFoundError: calc_pkg`, and repeated the same command five
  times until the repeat guard ended the turn: no README, no green run,
  **no answer at all**. GLM wrote package, tests and README in one heredoc
  and ran green.
- **N3** — "exactly ten lines". Gemma wrote ten fruits with no final
  newline, `wc -l` said 9, so it appended an eleventh fruit and reported
  the 10 that `wc` then printed: the file has eleven lines. The check
  caught it; the answer was true to the command and false to the task.
- T3 was the check's fault, not Gemma's: it answered exactly `no`, as
  asked ("tell me yes or no"), and `says_no` looked for `"no "` with a
  space. Fixed 2026-09-11 (`\bno\b`); passes on a rerun.

Where Gemma was *better*: **D2** — it made a venv in `report/`, hit
`source: not found` under `sh`, switched to `./venv/bin/python`, hit the
relative path, `cd`'d, and finished: the brief's rule followed, three wrong
turns recovered, where GLM took the shortcut of a system `pip install`.

Judge, the rule made consistent: a `list_files` before a path the prompt
already named, or a `set_goal` on a two-step task, costs the (b) point on
both models (the mini-set pass docked Gemma for it on B and let GLM's L1
through — corrected). Scores (a b c d e): D1 9 (extra listing), D2 10, D3
10, L1–L3 9 9 9 (listing first), N1 10, N2 10, N3 9 (a: eleven lines), T1–T3
10 10 10, U1 9 (five reads where one grep would do), U2 9, U3 9, V1 **6**
(b 0, d 1, e 1), V2 10, V3 10, X1 10, X2 **2**, X3 9 → mean 9.0. GLM by the
same rule: 10 except D2 9 (the system pip) and L1, D3, U1, U2, V1, X2 at 9
for a listing first → 9.7.

**Reading.** The families do separate the models, and the gap has one
shape: **when a result does not match what Gemma expected, it re-runs the
same command instead of changing something** — V1's find, X2's unittest —
until the harness's repeat guard stops it. That is rubric (d), the item
FireAct's robustness result says fine-tuning moves most, and GLM's
trajectories on the same prompts show the other behaviour on every case.
The second gap is (b), calls that add nothing (`set_goal` on two-step
tasks, listing a named path, five reads for a grep): 151 calls to 79. The
counting metrics that carry this: model calls per case, `not_run`
failures, turns ended by the repeat guard, and the judge's (b) and (d).

## 3c. Two sampled repeats on GLM (temperature 0.7), 2026-09-11 14:20 UTC

`loop_live --deployed --temperature 0.7 --repeat 2 D L N T U V X`, runs
`deployed-3e13b634-*` and `deployed-c8c79146-*`, 42 turns in about 19
minutes. Against the temperature-0 run of §3a, per case, the tool
sequence across the three runs:

| | cases |
|---|---:|
| the same sequence in all three runs | 9 — L3, N1, N2, N3, T2, T3, U2, U3, V2 |
| different sequences | 12 — every D, L1, L2, T1, U1, V1, V3, every X |
| the same model-call count in all three | 8 |

So at 0.7 a repeat is new data wherever the task has room — every wrong
turn, every long task — and the same trajectory again on the one-move
tasks. For the generation this says: repeat the families with room (D, L,
U, V, X) and parameterize the seeds of the one-move ones (other fruits,
other counts, other names) rather than repeat them.

Sampling also made GLM fail, which is what rejection sampling is for.
Run 1: 20/21 (V3). Run 2: 19/21 (L1, V3). Both real:

- **L1, run 2:** read both files and answered "Anna (50) + Zoya (100 +
  25) = **215**" — the arithmetic wrong, the check caught it.
- **V3, both runs:** ran `python3 save.py; echo "exit=$?"` — which hides
  the status from the harness's exit-code line — saw `exit=1`, and
  answered "Mostly yes, but not cleanly … the records were saved": it
  trusted the script's claim without looking for `db.sqlite`. The check
  "the exit code reached the model" failed for the wrong reason (the
  status was in the output, not the line), and "says it did not succeed"
  passed for the wrong reason ("not cleanly"); both corrected 2026-09-11:
  the status counts wherever it appears, and a yes anywhere fails.

Trajectory files on the Volume: 74. Teacher pass rate on the families at
0.7: 39/42 by the checks, before the judge.

## 3d. The hand-over and the variants, 2026-09-11

The human's rule for the rest of the item: **generation runs in parallel,
and the first training version is built from the necessary minimum** — a
pitfall may sit elsewhere in the loop (export, format, training, serving),
so the loop is closed once end to end before any stage is polished.

- **Export built:** `tools/export_trajectories.py` reads `.trajectories/`
  from the Volume (a client read, no worker) and the run rows, and writes
  one JSON per run with `outcome`, `tool_failed` codes, the repeat-guard
  count and the scenario's verdict, plus `index.jsonl`. The verdict is a
  new `scenario_checked` trace event that `loop_live` writes after each
  scenario (letter, name, passed, every check), so the training side
  filters on ready fields and never reads this database. First export:
  **95 runs** (the owner's Telegram turns since the capture went live, both
  mini sets, the three family runs, Gemma's family run); the runs before
  the event exist have `scenario: null`.
- **Variants built:** the nine one-move cases that repeated identically at
  0.7 get other seeds as further cases — L4 L5 (other dates), N4 N5
  (7 colours, 12 animals), T4 T5 T6 (other files, another package), U4 U5
  (another folder and broken file; "later" items), V4 V5 (6 of 10, 14 of
  8). 32 cases in all; a run of a letter covers them.
- **Not yet built, needed for parallel runs:** `scenarios` runs with
  `max_containers=1` and one probe user (`loop-live-check`, one workspace,
  fixed thread names), so two calls at once would serialize or collide.
  Parallel generation needs a probe user per call (workspace and threads
  of its own) and a higher container cap — the next build before the
  volume run.

## 4. Research before step 4: the training run on Modal

Open, to be answered by reading, not running: Unsloth against TRL + peft
for Gemma 4 on a Modal A100; the sequence length the turns need (the
request is 5–20k tokens with the prelude, so 16k at least, which on a
12B model means A100 80 GB with QLoRA and gradient checkpointing, or the
schemas cut from the prelude); assistant-only loss masking with Gemma's
chat template and tool-call format; the price of one epoch over a few
hundred trajectories (an hour of A100 80 GB is a few dollars).
