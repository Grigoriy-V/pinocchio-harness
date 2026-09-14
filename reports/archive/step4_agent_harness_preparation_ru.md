# Step 4 Preparation — Agent Harness and Unified Loop

**Project:** `Grigoriy-V/local-multimodal-agent`  
**Status:** preparation/design handoff only; this document is not implementation authorization.  
**Primary architecture reference:** DeepSeek Harness.  
**Secondary practical reference:** Hermes Agent.  
**Date:** 2026-08-30.

---

## 0. Purpose

This file prepares roadmap **Step 4 — Agent harness and loop** so the next coding agent does not need to rediscover the architecture, repeat reference research, or invent a different lifecycle from scratch.

The working direction is **not** to keep growing the current `answer/act` split or add more task-specific workflow nodes. The target is one general agent loop, while preserving useful existing infrastructure: LangGraph checkpointing, persistence, telemetry, streaming, tools, budgets, workspace boundaries, restart continuity and UI progress.

The first implementation should stay deliberately close to the **DeepSeek Harness turn/step lifecycle**. Project-specific behavior should be added only where an observed requirement needs it.

Hermes is a secondary reference for:
- optional `todo(...)` planning;
- cache-friendly prompt construction;
- sandbox/execution backends;
- future persistent goal continuation.

`ROADMAP.md` remains the source of authorization. This document does not authorize live GPU runs, migrations, destructive changes or deploys.

---

# 1. Current project state

Roadmap:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/ROADMAP.md

Current harness/router:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/app/agent/harness.py

Current conversational graph:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/app/agent/graph.py

Current bounded task graph:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/app/agent/task_graph.py

Current task runtime:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/app/agent/task_runtime.py

Future-direction notes:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/docs/agent_future_directions_ru.md

Step 3 is closed, so Step 4 begins with an observable baseline rather than guesses.

Relevant reports:

- Turn telemetry:  
  https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/reports/2026-08-29_v2_turn_telemetry_implementation.md
- Run inspector / task-stage trace:  
  https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/reports/2026-08-29_v2_run_inspector_implementation.md
- Model/GPU baseline:  
  https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/reports/2026-08-29_v2_gpu_baseline_measured.md

Important measured baseline:

- first six live turns: about **21.2 derived GPU active seconds per successful turn**;
- about **$0.0065 derived cost per successful turn**;
- measured baseline averages **3 model calls per successful turn**;
- current router is one separate full model request;
- measured router cost is roughly a third of input tokens on ordinary turns;
- router is worth roughly ~1 s of prefill on a representative current turn;
- decode is about **21–24 ms/output token (~45 tok/s)**;
- long-prompt prefill dominates: about **4.69 s at 9,773 input tokens**;
- prefix caching is confirmed active: a repeated 3,277-token prefix got ~98% reuse and prefill fell from ~1.37 s to ~0.082 s;
- one measured autonomous task used **8 model calls + 6 tool calls**:
  - implementation: 3 model calls, ~8.04 s;
  - validation: 5 model calls, ~9.79 s.

These values are the baseline the new loop should beat or justify.

---

# 2. Current architecture problem

Current high-level shape:

```text
user
  |
  v
router model call
  |
  +-- answer --> conversational graph
  |               model <-> tools
  |               final
  |
  +-- act ------> TaskRuntime
                  plan
                  approval
                  implement
                  test
                  evaluate
                  retry/finalize
```

`GeneralHarness.decide()` forces every ordinary request into `answer | act` before useful observation happens.

Problems:

1. **Whole-task classification happens too early.** A request may become complex only after files/web/tool results are inspected.
2. **Two execution semantics.** The existing conversational graph already has a natural generic `model -> tools -> model` loop, while `act` switches into a separate fixed lifecycle.
3. **Mandatory workflow cost.** A measured task spent more model calls/time validating than implementing.
4. **Router cost is real.** It adds a full request, prefill, tokens and latency.
5. **Planning/validation become modes rather than optional capabilities/state.**

The Step 4 redesign should solve the split, not hide it under new names.

---

# 3. Primary reference — DeepSeek Harness

Repository:  
https://github.com/deepseek-ai/deepseek-harness

Architecture:  
https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md

Concrete loop driver:  
https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md

Core subsystem:  
https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/core.md

Tool execution pipeline:  
https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/tool-execution-pipeline.md

## 3.1 Canonical lifecycle

DeepSeek defines:

- **Step** = one model request plus the tools called by that response.
- **Turn** = zero or more steps; closes when nothing remains owed.

Reference lifecycle:

```text
TURN START
   |
   v
claim input
   |
   v
assemble prompt + tool schemas
   |
   v
STEP START
   |
   v
model request
   |
   v
assistant response
   |
   +-- tool calls --> tool execution --> tool results
   |                                  |
   |                                  v
   |                              NEXT STEP
   |
   +-- no owed work --> turn-stopping
                         |
                         +-- continue --> NEXT STEP
                         |
                         +-- stop ------> TURN END
```

This should be the **default reference lifecycle** for our first Step 4 implementation.

Do not begin by inventing a global state machine such as:

```text
PLAN -> ACT -> VALIDATE -> REPAIR
```

Those concepts may exist as state, tools or stopping policies, but should not become mandatory global modes.

## 3.2 Why this reference fits

DeepSeek keeps concrete loop logic concentrated in one loop driver. Other behavior attaches through services/events rather than alternate loops.

Important extension seams include concepts equivalent to:

```text
agent/pre-step
agent/request
agent validation / continuation
agent/turn-stopping

tools/pre-execute
tools/execute
tools/post-execute
```

Core principle:

> policy, validation, sandboxing, telemetry, guards and UI behavior wrap the loop; they do not create a second agent lifecycle.

## 3.3 Durability principle

DeepSeek reconstructs model history from durable session facts.

Useful principle for this project:

> Anything required to resume correctly after interruption/restart should be reconstructable from durable state, not exist only in a live Python object.

We do **not** need to copy Cordis, its plugin tree or its exact event system. The reference is the lifecycle and separation of responsibilities.

---

# 4. Secondary reference — Hermes Agent

Repository:  
https://github.com/NousResearch/hermes-agent

Agent loop internals:  
https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop/

Todo tool:  
https://github.com/NousResearch/hermes-agent/blob/main/tools/todo_tool.py

Persistent goals:  
https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/goals.md

Security/trust model:  
https://github.com/NousResearch/hermes-agent/blob/main/SECURITY.md

Sandbox/security comparison:  
https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/security.md

Code execution:  
https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/code-execution.md

Hermes is **not** the preferred code-organization reference for the core loop: its `AIAgent` orchestration layer is more monolithic than desired here.

Use Hermes mainly for practical capabilities layered around a generic loop.

---

# 5. Working target runtime

First target:

```text
                         TURN
                          |
                          v
                       prepare
                          |
                          v
                    +--- MODEL <-------------------+
                    |                              |
                    | tool calls                   |
                    v                              |
                 ToolRuntime                      |
                    |                              |
                    v                              |
                tool results ---------------------+
                    |
                    | final candidate
                    v
               TURN STOPPING
                 |       |
              stop     continue
                 |       |
                 v       +-----------------------> MODEL
              persist
                 |
                 v
                END
```

Vocabulary:

```text
Turn
  Step*
  TurnStopping
```

A `Step` should mean approximately:

```text
assemble context
-> model request
-> assistant result
-> zero or more tool calls
-> tool results / observations
```

LangGraph can stay. It already provides valuable checkpointing, interrupt/resume, state, streaming and conditional transitions.

The objective is to simplify the lifecycle, not to replace LangGraph with `while True` for aesthetic reasons.

Avoid a graph containing task-category nodes such as `pdf_node`, `research_node`, `coding_node`, `shotops_node`, etc.

---

# 6. Planning — optional `todo(...)`, not a mode

Hermes reference:  
https://github.com/NousResearch/hermes-agent/blob/main/tools/todo_tool.py

Hermes uses one model-facing `todo` tool/state to decompose complex tasks and track progress. Its state can be re-injected after context compression.

Working direction for this project:

```text
todo([
  {id, content, status},
  ...
])
```

Likely simple statuses:

```text
pending
in_progress
completed
cancelled
```

Exact schema is not yet an implementation decision.

## Intended behavior

Simple chat:

```text
"привет"
-> model -> final
```

Single read:

```text
"прочитай notes.txt"
-> model -> read_file -> model -> final
```

Simple write:

```text
"запиши этот текст в notes.txt"
-> model -> write_file -> model -> final
```

**No plan is required merely because a write occurs.**

Complex task:

```text
model
-> todo(create)
-> inspect
-> tool
-> todo(update)
-> tool
-> validate as needed
-> todo(update)
-> final
```

Planning is state available to the agent, not a separate planner runtime.

The model should normally decide when `todo` is useful. Avoid adding another LLM classifier for `PLAN / NO PLAN`.

---

# 7. Generic ToolRuntime pipeline

Primary reference:  
https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/tool-execution-pipeline.md

Target concept:

```text
assistant tool call
       |
       v
record / normalize
       |
       v
pre_execute
  - validate call
  - scope/policy
  - choose execution backend/sandbox
       |
       v
execute
  - timeout
  - metrics
  - backend dispatch
       |
       v
post_execute
  - normalize result
  - provenance/evidence
  - telemetry
       |
       v
model-facing tool result
```

The loop should know only that a tool call produced an observation/result. It should not know filesystem, shell, web, ShotOps or future Git implementation details.

This execution seam should exist before adding richer arbitrary execution.

---

# 8. Sandbox — early Step 4, after the execution seam

Sandbox should **not** be implemented first on top of the old `TaskRuntime`, because that would couple the new execution model to a lifecycle intended to be replaced.

Recommended sequence:

```text
unified loop foundation
-> generic ToolRuntime seam
-> sandbox backend
-> richer autonomous behavior/scenarios
```

So sandbox is an **early Step 4 capability**, not a late optimization.

## Desired sandbox role

Trusted runtime retains:

```text
model/API access
Telegram
Neon
conversation/memory stores
external credentials
policy/orchestration
```

Sandbox receives a restricted execution environment:

```text
scoped workspace
Python
uv/pip
shell/CLI
package installation
temporary environments
generated scripts
local files/results
optional npm later
Git later if useful
```

Default principle:

```text
memory/orchestration/secrets persist outside
arbitrary generated code executes inside isolation
```

## Security boundary

Hermes explicitly treats **OS-level isolation** as the load-bearing security boundary, not prompt instructions, approvals or in-process allowlists:

https://github.com/NousResearch/hermes-agent/blob/main/SECURITY.md

Hermes' user-facing security docs also show dangerous-command confirmations being skipped for container/Modal-style isolated terminal backends because isolation is the boundary:

https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/security.md

This aligns with the desired UX here.

---

# 9. Autonomy and approvals

Working design principle:

> **Scoped autonomy by default; user interruption for decisions, not routine permission.**

Desired UX is closer to Claude Code/Codex operating inside an assigned directory than to approving every write.

Once the agent has a secure bounded workspace/sandbox scope, routine operations inside it should normally be automatic:

```text
read workspace           allow
write workspace          allow
edit workspace           allow
delete within scope      allow
mkdir                    allow
run Python               allow
run shell                allow
install packages         allow
local git operations     allow
```

Do not automatically carry the current “destructive tool = approval” behavior into the new runtime.

## Future trust-boundary actions

Possible future `CONFIRM`/policy cases:

```text
send external email/message
git push
deploy production
publish publicly
purchase/spend money
delete external data
modify infrastructure
expose sensitive credentials
very expensive generation
```

These should be tool/capability policies, not a special global agent mode.

A future minimal policy vocabulary may simply be:

```text
ALLOW
CONFIRM
DENY
```

Do not over-design it in the first loop phase.

---

# 10. `ask_user` — native clarification/decision interrupt

The model should be able to stop and ask the user when a human decision is genuinely required.

Concept:

```text
model
-> ask_user(question, optional choices/context)
-> interrupt/checkpoint
-> UI sends question
-> wait
-> user replies
-> resume same state
-> model continues
```

This is **not approval**.

Use cases:

- genuinely missing required information;
- two materially different outcomes are plausible and cannot be safely inferred;
- a consequential decision belongs to the user;
- proceeding would create a significant irreversible consequence.

The agent should **not** ask when it can safely inspect evidence or choose a reasonable default.

Desired instruction principle:

> Do not ask the user when uncertainty can be safely resolved from available evidence. Ask only when information or a decision is genuinely required.

Existing LangGraph checkpoint/interrupt machinery is the natural implementation candidate.

---

# 11. Validation and turn stopping

Do not reproduce the current mandatory global lifecycle:

```text
implement
-> test
-> evaluate
-> repair
```

Use a DeepSeek-style stopping seam instead.

```text
model final candidate
       |
       v
turn stopping
       |
       +-- enough evidence / no obligation --> stop
       |
       +-- unfinished obligation -----------> continuation -> model
```

Possible future stopping checks:

- incomplete todo items;
- pending required tool result;
- explicitly requested artifact not produced;
- required validation/evidence missing;
- failed tool result ignored;
- task claims success without observable evidence;
- budget exhausted;
- unresolved external side effect.

Keep stopping small. It must not become another fixed workflow engine.

## Validation should be proportional

Simple text write:

```text
write_file success
-> often sufficient
```

Structured JSON:

```text
write_file
-> parse/read validation may be useful
```

PDF:

```text
create PDF
-> open/render/inspect actual output
-> repair if necessary
```

ShotOps later:

```text
generate
-> inspect visual result
-> compare/repair when useful
```

Validation follows the goal/result type, not merely the fact that a mutation occurred.

---

# 12. Cache-friendly prompt/context architecture

This is an architectural requirement, not a later micro-optimization.

Measured project facts:
- long prefill is expensive;
- prefix caching works extremely well when the prefix is stable.

Target prompt organization:

```text
STABLE / CACHEABLE PREFIX
  system identity
  stable behavioral contract
  stable capability guidance
  stable tool schemas
  stable workspace/sandbox rules

DYNAMIC SUFFIX
  retrieved memory
  recent conversation/history
  todo state
  current user message
  tool observations
  temporary warnings / budget state
```

Requirements:

- deterministic tool-schema ordering;
- avoid volatile timestamps/counters/random ordering near the front;
- avoid rebuilding semantically identical but byte-different system prefixes each step;
- keep transient state late in the prompt when practical;
- measure new context assembly against the existing prefix-cache baseline.

Hermes loop/context reference:  
https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop/

---

# 13. Conversation serialization — correctness prerequisite

`ROADMAP.md` already records a live race: two messages sent seconds apart can run in separate containers and answer out of order.

Required properties:

- **Mutual exclusion:** two turns must not execute simultaneously against one canonical thread.
- **Order:** the conversation consumes queued updates in the intended order.
- **Coalescing:** image + immediately following text may be one user intent and needs a defined policy.

Coordination belongs in a shared durable layer/database rather than one Modal container, so local/deployed behavior does not diverge.

Do not let the loop redesign hide this prerequisite.

---

# 14. Telemetry requirements for the new loop

Do not regress Step 3 observability.

New trace should expose approximately:

```text
turn
  step 1
    model
    tools
  step 2
    model
    tools
  stopping decision
  completion
```

Useful event concepts:

```text
turn_started
step_started
model_started / first_token / finished
tool_started / finished / failed
todo_updated
user_interrupt_requested
user_interrupt_resumed
stopping_checked
continuation_requested
turn_finished
```

Do not store private chain-of-thought or unnecessary conversation content in telemetry.

Keep comparisons possible for:

- model calls;
- tool calls;
- input/output tokens;
- model duration;
- GPU active seconds per successful turn;
- failures;
- first-visible latency;
- outcome.

---

# 15. Future latency instrumentation note

Separate issue recorded during preparation: warm request-to-first-visible latency should eventually be decomposed by **critical path**, not CPU-seconds.

Current Telegram streaming code waits for at least 24 characters before first preview:

https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/ui/telegram/adapter.py

Useful future boundaries:

```text
request_received
model_first_token
preview_threshold_reached
telegram_send_started
telegram_send_finished
```

Then:

```text
infra before inference
+ model TTFT
+ initial decode
+ Telegram delivery
= time to first visible
```

CPU/GPU durations may overlap, so CPU-seconds do not answer the latency question.

This should not block the unified-loop work.

---

# 16. Suggested Step 4 implementation order

This sequence is preparation, not authorization.

## 4.0 Conversation serialization

Fix the known shared-thread race.

## 4.1 Unified DeepSeek-shaped loop foundation

Establish:

```text
Turn
  Step*
  TurnStopping
```

Remove the separate model-owned `answer/act` routing requirement.

Keep the first version minimal.

## 4.2 Generic ToolRuntime pipeline

Introduce:

```text
pre_execute
-> execute
-> post_execute
-> model-facing result
```

Existing tools should continue to work through this seam.

## 4.3 Sandbox backend

Add isolated shell/Python/package execution with a restricted workspace and no main-worker secrets by default.

## 4.4 Optional `todo(...)` planning

Planning becomes agent state/tool rather than mode.

Todo state must survive relevant context compression/restart boundaries.

## 4.5 Native `ask_user` interrupt/resume

Allow critical clarification/decision requests without turning normal work into an approval flow.

## 4.6 Turn-stopping / proportional validation

Add minimum generic stopping semantics first. Do not recreate the old fixed task lifecycle under new names.

## 4.7 Cache-friendly context assembly

Make stable-prefix/dynamic-suffix behavior explicit and verify cache reuse.

This can evolve alongside earlier phases but should be part of the new architecture from the beginning.

## 4.8 Durable recovery and scenario suite

Prove restart/resume and compare new trajectories against Step 3 baseline metrics.

---

# 17. Required acceptance scenarios

Do not create hard-coded workflows for these scenarios. They test the generic harness.

## A. Simple conversation

```text
"Привет"
```

Expected:
- one ordinary loop;
- no todo;
- no sandbox;
- no unnecessary validation;
- final answer.

## B. Single read tool

```text
"Что написано в notes.txt?"
```

Expected:

```text
model -> read_file -> model -> final
```

## C. Simple write

```text
"Запиши этот текст в notes.txt"
```

Expected:
- no mandatory plan;
- workspace write without routine approval;
- truthful completion.

## D. Multi-step workspace task

Expected:
- model may use todo;
- multiple tools;
- autonomous work inside scope;
- progress survives multiple steps;
- no routine user approval for workspace/sandbox actions.

## E. Critical ambiguity

Construct a case with two materially different plausible outcomes.

Expected:
- model uses `ask_user`;
- state checkpoints;
- user response resumes the same work.

## F. Failed live PDF scenario

Preserve roadmap acceptance scenario:

From a natural-language request, create a simple PDF, validate the actual document and deliver it.

Historical failures to eliminate:
- ungrounded plan;
- implementation toolbox unable to execute its own script;
- invalid validation strategy;
- 20 calls exhausted before useful validation;
- capability refusal despite relevant tools being available.

This must remain a generic harness test, not a PDF workflow.

## G. Tool failure / repair

Expected:
- tool failure reaches the model;
- model adapts;
- no false success.

## H. Restart/resume

Interrupt/restart a multi-step task.

Expected:
- model-visible state reconstructs;
- completed side effects are not blindly repeated;
- agent continues truthfully.

---

# 18. Future candidate — persistent `/goal` outer loop

Record now; do not implement in the first Step 4 phase unless separately approved.

Hermes reference:  
https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/goals.md

Concept:

```text
normal turn
   |
   v
goal judge
  |
  +-- done --> stop
  |
  +-- continue --> another turn
```

Potential future use:
- long coding/research goals;
- iterative repair until tests pass;
- waiting on background work;
- later ShotOps iterative generation.

Keep this as an **outer goal loop**, separate from the core step loop.

---

# 19. Non-goals for the first Step 4 implementation

Do not expand scope into:

- multi-agent swarm;
- autonomous software-engineer replacement for Codex/Claude;
- giant universal plugin marketplace/framework;
- deterministic workflow graph with a node for each task type;
- persistent `/goal` unless separately approved;
- granular approvals for every workspace mutation;
- full desktop/computer-use system;
- vector DB/RAG rewrite;
- ShotOps integration itself;
- GitHub push/deploy automation;
- premature extraction of a generic standalone `agent-core` package/repo.

The harness may later influence ShotOps and the narrative game, but Step 4 first makes this single-agent runtime strong and clean.

---

# 20. Design principles

## One loop, many trajectories

```text
chat        = short trajectory
read task   = model/tool/model
work task   = longer trajectory
```

Do not classify them into separate agent species first.

## Planning is state, not mode

`todo(...)` is optional coordination state.

## Validation is proportional

Validate when the requested outcome needs evidence.

## Autonomy is scoped

Full autonomy inside a secure sandbox/workspace is preferred to repeated confirmation prompts.

## User interruption is for decisions

`ask_user` exists for genuinely missing information/critical choices, not routine permission.

## Sandbox is the real arbitrary-execution boundary

Do not treat prompt wording or in-process allowlists as containment.

## Stable prefix is architectural

Measured prefill/cache behavior makes context shape a first-class design concern.

## Durable observable facts

Resume/debugging should rely on reconstructable state and trace.

## Keep the loop boring

Start close to the conservative DeepSeek lifecycle. Add project-specific complexity only when measured scenarios require it.

---

# 21. Reference map

## Core turn/step lifecycle — PRIMARY

DeepSeek architecture:  
https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md

DeepSeek concrete agent loop:  
https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md

DeepSeek core subsystem:  
https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/core.md

Read these before redesigning `GeneralHarness`, `graph.py` or `TaskRuntime`.

## Tool execution / policies / sandbox seam — PRIMARY

DeepSeek tool pipeline:  
https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/tool-execution-pipeline.md

Use as the primary reference for generic `pre_execute -> execute -> post_execute` behavior.

## Optional planning — SECONDARY

Hermes todo:  
https://github.com/NousResearch/hermes-agent/blob/main/tools/todo_tool.py

Use as reference for “plan as tool/state, not mode”.

## Hermes core-loop comparison

https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop/

Use for behavioral ideas, not as the preferred code organization.

## Sandbox / trust boundary

Hermes security model:  
https://github.com/NousResearch/hermes-agent/blob/main/SECURITY.md

Hermes sandbox/security comparison:  
https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/security.md

Hermes code execution:  
https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/code-execution.md

## Future persistent goals

Hermes `/goal`:  
https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/goals.md

Do not implement in the first unified-loop phase.

## Current project

Roadmap:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/ROADMAP.md

Harness/router:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/app/agent/harness.py

Conversational graph:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/app/agent/graph.py

Fixed task graph:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/app/agent/task_graph.py

Task runtime:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/app/agent/task_runtime.py

Future directions:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/docs/agent_future_directions_ru.md

Turn telemetry:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/reports/2026-08-29_v2_turn_telemetry_implementation.md

Run inspector/task baseline:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/reports/2026-08-29_v2_run_inspector_implementation.md

GPU/model baseline:  
https://github.com/Grigoriy-V/local-multimodal-agent/blob/main/reports/2026-08-29_v2_gpu_baseline_measured.md

---

# 22. Handoff to the implementing agent

Before writing code:

1. Read `ROADMAP.md` and confirm which Step 4 sub-step is actually authorized.
2. Read this file fully.
3. Read DeepSeek `architecture.md`, `agent-loop/README.md` and `tool-execution-pipeline.md` from the direct links above.
4. Read current `harness.py`, `graph.py`, `task_graph.py`, `task_runtime.py`.
5. Do **not** start by patching individual defects in the old fixed task lifecycle unless the approved sub-step explicitly says to.
6. Preserve telemetry and baseline comparability.
7. Keep the first unified-loop design intentionally small and DeepSeek-shaped.
8. Do not add a new router for planning, validation, sandbox or `ask_user`.
9. Do not make routine workspace/sandbox mutations require approval merely because they mutate files.
10. Do not implement `/goal` yet unless separately approved.
11. Live GPU tests, migrations and deploys remain subject to existing human gates.

Desired architectural transition:

```text
CURRENT

router
  +-- conversational graph
  +-- fixed autonomous task graph


TARGET

one Turn
  -> Step*
       model
       tools/observations
  -> TurnStopping

with optional todo state,
sandboxed execution,
native ask_user interrupt,
and policies/services around the loop.
```

The goal is **not** to reproduce DeepSeek Harness as a framework. The goal is to start from its conservative agent lifecycle and adapt it to this project's existing Python/LangGraph/runtime/storage boundaries with as little new machinery as possible.
