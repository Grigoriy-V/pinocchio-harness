# Roadmap 24, step 3 research: what trajectories teach an agent, and what scenarios to write

Read on 2026-09-11 before writing a scenario for training data (the human:
research first). Sources at the end. Numbers are theirs; the proposals in
§5 are options for the human's word, not decisions.

## 1. How much data, and what kind, moved small open models

| work | teacher → student | trajectories kept | filter | result |
|---|---|---:|---|---|
| FireAct (2023) | GPT-4 ReAct → Llama-2 7B/13B | **500** of 2,000 questions | only successful | HotpotQA EM 14.8 → 26.2 (7B), 21.2 → 34.4 (13B); Llama learns nothing from 100–200 samples, the format "emerges" at 500; GPT-3.5 needs 100 |
| SWE-Gym (ICML 2025) | GPT-4o / Claude 3.5 → Qwen2.5-Coder 7B/32B | **491** | only patches that pass the tests (teacher success 8–29 %) | SWE-bench Verified 1.8 → 10.6 (7B), 7.0 → 20.6 (32B); gains still rising at 491, no saturation seen |
| SWE-smith (2025) | Claude 3.7 → Qwen2.5-Coder 32B | **5,016** of 8,686 attempts (36 % resolved, ≤ 3 per task) | only resolved; full fine-tune | 40.2 % Verified; PR-mirroring tasks best, LM-injected bugs worst; difficulty did **not** predict usefulness |
| TOUCAN (2025) | three teachers, two frameworks, real MCP tools → Qwen2.5 7/14/32B | 1.5 M | task rubric 1–5 (clarity, realism, verifiability, tool-selection difficulty); trajectory: completeness, conciseness, desired-tool ratio = 1.0 | BFCL v3 +3.2 / +7.4 / +8.7 points |
| APIGen-MT (NeurIPS 2025) | blueprint → simulated user ↔ agent | — | format, execution, semantic verification of a blueprint before any trajectory | small xLAM models beat larger closed ones on τ-bench |

What carries across all of them:

- **Rejection sampling is the recipe.** Run a strong teacher many times, keep
  only what passed a check the environment can make (tests pass, end state
  matches, rubric met). Nobody trains on unfiltered runs.
- **Hundreds are enough to move a 7–13B model; a few hundred is where the
  format takes.** Our 12B sits in FireAct's range: plan for 500–1,000 kept
  trajectories, not tens of thousands.
- **The check must be about the outcome, not the route.** τ-bench compares
  the database's end state with an annotated goal state; SWE-Gym runs the
  tests; TheAgentCompany scores checkpoints with partial credit. None of
  them asks "was tool X called before tool Y". (The human's rule of
  2026-09-11 on scenario C is the same rule.)
- **Fine-tuning buys robustness to bad tool results, not only accuracy.**
  FireAct broke the search tool (returns `None` or a stale result half the
  time): the fine-tuned model lost 5–14 points, the prompted one 28–34.
  This is the (d) of our rubric — recovery — and it says failed-tool turns
  belong in the data on purpose.
- **Mixing kinds helps a little; realism helps a lot.** FireAct: ReAct +
  CoT 41.0 against ReAct alone 39.4. SWE-smith: the tasks closest to the
  real distribution taught the most.
- **Consistency is a metric of its own.** τ-bench's pass^k: GPT-4o's pass^8
  under 25 % in retail. One run of a scenario says little; the same scenario
  run k times says whether the behaviour is learned or lucky.

## 2. What a judge can and cannot verify (for metric 5)

"Can LLM-as-a-Judge Reliably Verify Rubrics in Agentic Scenarios?" (2026,
2,458 human-labelled instances, κ = 0.81): frontier judges reach 89–95 %
agreement, worst on **Tools** and **Rules** items in agentic coding — the
ones that need the judge to track tool schemas and outputs across the
trajectory. Two failure modes: *partial satisfaction* (fragmentary evidence
taken as fulfilment) and *requirement expansion* (the judge adds constraints
the rubric did not state). What helped: one rubric item per verification
call (batching four or more cost double digits), three to five votes,
evidence-grounded binary items over scaled ones.

For our rubric (report `2026-09-11_gemma_finetune_experiment.md` §2): keep
the five items, but (c) "every claim matches an observation" should be
checked claim by claim against the tool results, not as one impression;
and when the judge becomes a model call, one item per call, three votes.

## 3. The format the student needs

Gemma 4's chat template carries tools in a `<|tool>declaration:` section,
emits `<|tool_call>call:name{arg:<|"|>value<|"|>}<tool_call|>`, and takes
results back as `tool_responses` on the assistant turn. TRL's SFTTrainer
takes the OpenAI-style `messages` with `tool_calls` and a `tool` role and
renders them through the template; Unsloth's `train_on_responses_only`
masks everything before `<|turn>model\n`. Two cautions from the model card
discussions: the 12B template re-injected prior-turn thoughts in multi-turn
tool use and looped (fixed in the July 2026 template; the 26B still does),
so **stored assistant turns must carry no reasoning content**; and our
trajectory records already hold exactly the messages-with-tools shape, with
the tool schemas in `tools`, so the export is a rename of fields, not a
reconstruction.

Memory: Unsloth's own numbers put Gemma 4 12B QLoRA at 14–16 GB peak at
short context; our turns are 5–20k tokens with the schemas, so 16k on an
L40S (48 GB) or A100-40 is the plan from the earlier estimate, `r=16`,
`lr=2e-4`, 2–3 epochs over 500–1,000 samples — hours, single digits of
dollars. Verified by a run, not by this reading.

## 4. What our mini set is, against this

Eight scenarios, each one capability, checked on the harness's own
evidence: written to catch a harness that lies. Both GLM and Gemma sit at
9.7–9.9 on the judge and 7–8 of 8 on the checks. As training prompts they
give short, single-purpose turns — the "format" half of FireAct's data —
but no wrong turns, no bad tool results except E's one refusal, no task
where the second step depends on reading the first step's result
carefully. That is the half a fine-tune is for.

## 5. Scenario families to write — options

Each family is a template with parameters, so one family yields many
distinct prompts (the diversity FireAct and TOUCAN both want) and the same
prompt can run k times (pass^k). Every check is an outcome the harness can
read — a file, a command's exit code, a page's text, a stored fact — never
a tool name the prompt did not ask for. Each family names what the rubric
item it stresses.

1. **A wrong turn on the way** (rubric d, c). A task whose first natural
   attempt fails for a reason in the environment: a script imports a
   package that is not installed; a test file references a fixture in a
   sibling file that has a typo; a data file has one malformed row. Check:
   the final artefact is right and the answer says what went wrong. Data
   value: the recovery step, which FireAct showed is what fine-tuning buys.
2. **Two files, one answer** (rubric c). Read two files, compute across
   them, write the result, quote it: "the total of column B in a.csv for
   the names listed in b.txt". Check: the number, and that it was read
   (the stub of an unread file cannot produce it). Data value: grounding.
3. **Make, use, report** (rubric a, e). Write a small artefact — a page, a
   script, a CSV — then use it with another tool and report what was
   observed, not what was intended (F and C are the seeds). Parameters:
   the artefact kind, the observation asked for.
4. **Stop when told not to** (rubric b). A task that says "do not read the
   file / do not rewrite / do not install" and can only be finished
   partially under that rule (E is the seed). Check: the forbidden tool did
   not run, and the answer says what was left undone.
5. **The result decides the next step** (rubric a, c). "List the folder;
   for every `.txt` that mentions apples, append a line" — the plan cannot
   be written before the first result is read. Check: exactly the right
   files changed.
6. **A tool that lies back** (rubric d). A command whose output says
   "done" but the file is not there; a page whose heading text differs from
   its title. Check: the answer trusts the observation, not the claim.
   Data value: FireAct's broken-tool robustness, made deliberate.
7. **Long enough to need the goal** (rubric a). Six to ten steps with
   three deliverables named up front; a mid-turn message from the person
   (M is the seed). Check: all three deliverables and the interjection
   answered. Data value: the only family whose turns are the size of a
   real task.

Each family: three to five concrete prompts, an outcome check per
prompt, and a note of which rubric items it stresses. Written in
`scripts/loop_live.py`'s style as new letters, runnable locally on the
scripted backend for the checks and deployed for data. Generation then:
every family's prompts × k runs of GLM, keep those that pass the check and
score ≥ 8 on the judge, mark the ones with a recovered failure.

## 6. What this changes in the plan

- Target: **500–1,000 kept trajectories**, mostly from §5's families, a
  minority from the mini set and the wider letters for format coverage.
- Scenario checks test outcomes (C and O corrected 2026-09-11; 19's
  rework follows the same rule).
- The judge stays the main metric with the §2 corrections; pass^k on a
  handful of scenarios is added as metric 6 when the wider run happens.
- The trajectory export strips any reasoning content from stored assistant
  turns and keeps the schemas.

## Sources

- FireAct — https://arxiv.org/abs/2310.05915
- SWE-Gym — https://arxiv.org/abs/2412.21139 , https://github.com/SWE-Gym/SWE-Gym
- SWE-smith — https://arxiv.org/abs/2504.21798
- TOUCAN — https://arxiv.org/abs/2510.01179
- APIGen-MT — https://arxiv.org/abs/2504.03601
- τ-bench — https://arxiv.org/abs/2406.12045 ; τ²-bench — https://arxiv.org/abs/2506.07982
- TheAgentCompany — https://arxiv.org/abs/2412.14161
- Can LLM-as-a-Judge Reliably Verify Rubrics in Agentic Scenarios? — https://arxiv.org/abs/2606.29920
- Gemma 4 function calling — https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4
- Gemma 4 12B template discussion (thought re-injection) — https://huggingface.co/google/gemma-4-12B-it/discussions/38
- Unsloth Gemma 4 guide — https://unsloth.ai/docs/models/gemma-4/train ; tool-calling guide — https://unsloth.ai/docs/basics/tool-calling-guide-for-local-llms
- TRL tool-calling SFT for Gemma 4 — https://dev.to/pulkitgovrani/fine-tuning-gemma-4-for-function-calling-with-trls-new-multimodal-tool-support-4ghf
- MidTool, ToolACE, ToolMind (read for the filters only) — https://arxiv.org/html/2608.20314 , https://arxiv.org/html/2511.15718v2
