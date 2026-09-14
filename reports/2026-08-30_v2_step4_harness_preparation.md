# Step 4 preparation — one turn, one loop

**Date:** 2026-08-30
**Status:** preparation. Nothing here authorizes implementation; each sub-step
starts on its own word from the human.
**Design origin:** `reports/archive/step4_agent_harness_preparation_ru.md`, the human's own
handoff, with DeepSeek Harness as the primary lifecycle reference and Hermes as
a secondary practical one.
**Grounded against:** `app/agent/graph.py`, `harness.py`, `task_graph.py`,
`task_worker.py`, `task_validator.py`, `task_runtime.py`, `app/tools/base.py`,
`app/context/window.py`, `ui/telegram/adapter.py`, `ui/telegram/inbox.py`,
`deploy/modal/control_app.py`.
**Baseline it will be judged against:**
`reports/2026-08-29_v2_run_inspector_implementation.md`,
`reports/2026-08-29_v2_gpu_baseline_measured.md`.

## 1. The loop already exists

The handoff asks for a DeepSeek-shaped `Turn → Step* → TurnStopping` runtime.
`app/agent/graph.py` is already `load → model → tools → model → persist`, with
the only exit being an assistant turn without tool calls. What the project has
is not the absence of a general loop; it is a second lifecycle beside it and a
model call that chooses between them.

So the honest description of 4.1 is not "build a new loop". It is **delete the
router and the fixed task lifecycle, and give the surviving loop the three
things only the task path had**: a spend budget, visible stage progress, and a
stopping decision that is not "the model stopped calling tools".

That reframing matters for the estimate. The deletion is large — `harness.py`'s
router (~90 lines), `task_graph.py` (487), `task_worker.py` (428),
`task_validator.py` (299), `task_runtime.py` (291), plus the adapter's act
branch and its tests — but the replacement is small.

## 2. What must not disappear with the task path

Read as an inventory of behaviour, not of files. Each line is something the
bounded lifecycle provides today and the conversational loop does not.

- **A spend ceiling.** `TaskBudget` bounds iterations (3), tool calls (20) and
  wall time (300 s). The conversational graph has *no* tool-call or time budget
  at all — only LangGraph's recursion limit. Deleting the task path without
  giving the loop its own budget would remove the only ceiling on an autonomous
  turn, on a GPU that costs about $0.0003 a second. **This is the one piece of
  the old lifecycle that must land in 4.1 itself, not later.**
- **Progress a person can watch.** `TaskProgress` drives the chat's live edit
  during long work. Step events are the natural replacement, and the trace
  already records them; the adapter needs a source, not a lifecycle.
- **Artifacts reaching the person.** The task path pushes result files
  automatically. That is already superseded in principle by the 2026-08-29
  decision that observation and presentation are separate agent actions: the
  model has `send_file` and chooses. Dropping the automatic push is a
  simplification, not a loss — but it must be checked live, because "the agent
  forgot to deliver the file" is a product regression.
- **The grant.** `TaskGrant` narrows work to `tasks/<hash>` and expires. With
  workspace autonomy approved (§3) the per-task grant goes, but per-user
  confinement does **not**: `CapabilityGrant.root` in `app/agent/runtime.py` is
  what keeps one person out of another's files, and it is untouched by any of
  this.
- **Cancellation.** `/stop` writes a durable `stopped` outcome so a task cannot
  resume by accident, and the turn is recorded as `cancelled` rather than
  failed. A long autonomous loop still needs a stop, and the metric still needs
  the third outcome.

## 3. Three questions answered on 2026-08-30

**Approvals — full autonomy inside the workspace.** Recorded as an approved
durable choice in `DECISIONS.md` on the same date, superseding the consent half
of 2026-08-01. Worth stating plainly how small the blast radius is: exactly two
tools carry `destructive=True` today, `write_file` and `edit_file`
(`app/tools/filesystem.py`), and there is no delete tool at all. The change is
"writing a file in your own workspace stops asking", not "the agent may now
destroy things". `Tool.destructive` keeps its meaning for anything that crosses
the workspace boundary, which is where the future policy vocabulary belongs.

**Sandbox — its own queue item after step 4.** The handoff placed it inside
step 4 so the new execution model would not be built on the old `TaskRuntime`.
That concern is satisfied for free once 4.1 deletes `TaskRuntime`. Against it:
a sandbox is Modal infrastructure, every run of it is a separate human gate and
real money, and it should not be built before the loop it serves is measured as
better. The tool execution seam (4.2) is what keeps the door open.

**Serialization — the populated-database migration is not a gate.** The human's
words: old rows may be discarded if that is simpler. The additive
`ADD COLUMN IF NOT EXISTS` style already used in `ui/telegram/inbox.py` should
make discarding unnecessary; if it turns out to be needed, dropping rows is
still a destructive action that gets confirmed at the moment it happens. No
`max_containers=1` stopgap is planned, because the real lease is the first
sub-step anyway.

## 4. Ordered sub-steps

Each is a separate start authorization. Acceptance is stated per sub-step so
that "done" is not a matter of opinion later.

### 4.0 Conversation serialization

The known live race: a screenshot and a question sent seconds apart ran in two
containers and were answered out of order. `PostgresUpdateInbox.claim` leases
one `update_id` and knows nothing about conversations, so two updates from one
person are two independent claims.

Smallest shape that satisfies mutual exclusion and order: one additive column
holding the canonical conversation key, written at enqueue from the payload,
and a claim that refuses while another row for that key is `running` and
otherwise takes that key's lowest pending `update_id`. One column, one index,
no table rewrite.

**Coalescing is deliberately not in this sub-step.** Treating an image and the
question that follows it as one intent breaks the invariant item 3 established —
one update, one `run_id`, one turn — and therefore changes what every number in
the baseline counts. It needs its own decision, not a quiet implementation.

*Acceptance:* two updates sent seconds apart are answered in order, and
`tools/show_run.py` shows two runs on one thread whose intervals do not overlap.

### 4.1 One loop

Delete `GeneralHarness.decide` and the plan/implement/test/evaluate lifecycle.
The surviving loop gains step boundaries in the trace, its own spend budget
(§2), and progress events for the chat.

The router's removal belongs here rather than in the optimization item: it is
the same edit, and after it the optimization item is only autoscale and
speculative decoding. Priced from the baseline at roughly 1.0 s of prefill and
one of three model calls a turn — the router shares no prefix with the answer
call, so none of it is recovered by caching.

*Acceptance:* scenarios A–D from the handoff offline against a fake backend; one
live warm window; `tools/show_run.py --summary` over the live turns showing
model calls a turn below the measured 3.00, with no scenario needing a mode.

### 4.2 Tool execution seam

`pre_execute → execute → post_execute` around the existing `Toolbox`. There is
only one consumer once 4.1 lands, so this must stay a move of existing
behaviour — consent policy, validation, telemetry bracketing gathered into one
place — and not new machinery for a caller that does not exist yet. The
workspace-autonomy decision is implemented here.

*Acceptance:* every existing tool keeps its results and failure behaviour
through the seam; the trace still names tool, stage, duration, status and path.
The intentional policy change is explicit: workspace write/edit stops asking,
`send_file` back to the same person continues without asking, and effects on
third parties, publication, spending or infrastructure remain gated. No other
tool behaviour is added.

### 4.3 Turn stopping and proportional validation

A stopping seam instead of a mandatory `implement → test → evaluate → repair`.
The measured live task spent more on validation than on implementation — five
model calls and 9.79 s against three and 8.04 s — which is what proportional
means here in numbers.

**Approved refinement, 2026-08-30:** the seam is minimal: stop by default and
continue only through explicit structured steering. It adds no validator,
finish tool, text heuristic or new obligation state. HTML is only an acceptance
scenario in which the model may decide that `inspect_page` is useful; it is not
a workflow. The PDF scenario is removed from this sub-step until the sandbox
can create one through generic execution.

*Acceptance:* the HTML scenario and the tool-failure scenario (G): a failed
tool reaches the model, the model adapts, and no scenario reports success
without evidence. A simple text write must not acquire a validation pass.

### 4.4 `todo` as state, not a mode

Optional model-facing planning state that survives folding and restart. No
second classifier deciding whether to plan.

*Acceptance:* a multi-step workspace task (D) uses it; "привет" does not.

### 4.5 `ask_user`

Interrupt and resume for a genuinely missing decision — not approval. The
machinery exists: `interrupt()` in the graph, the checkpointer, and the
adapter's keyboard-and-settle path that already asks a question and resumes the
same state.

*Acceptance:* scenario E — two materially different plausible outcomes, the
agent asks, the answer resumes the same work; and the agent does *not* ask when
the answer is available from evidence.

### 4.6 Cache-friendly context assembly

Concrete and measurable, not a micro-optimization. Today `app/context/window.py`
assembles system prompt → rolling summary → **retrieved facts** → recent
messages. The retrieved facts are selected per turn from the current query, so
they change on nearly every turn and sit *in front of* the conversation history
— which means the prefix cache is invalidated from that point down. Measured
evidence that this is worth seconds: 98% reuse on a repeated 3,277-token
prefix, prefill 1.37 s → 0.082 s.

*Acceptance:* volatile layers move behind the stable ones, and a warm repeat
turn shows a measured prefill drop.

### 4.7 Restart, resume and the scenario suite

Scenario H, plus the before/after comparison against the item 3 baseline.

*Acceptance:* an interrupted multi-step task continues truthfully without
repeating completed side effects; the suite asserts on harness events, never on
model wording.

## 5. Risks that are not obvious from the handoff

- **Checkpoints outlive the code.** `CHECKPOINT_TYPES` in `app/agent/runtime.py`
  whitelists seven `app.agent.task_graph` dataclasses. Deleting that module
  makes any `task:<thread>` checkpoint row in the deployed database
  undeserializable — a task waiting for approval at deploy time dies rather
  than resumes. Land 4.1 when nothing is pending, and remove those rows as part
  of it.
- **Telemetry comparability survives, with one edit.** The primary metric is
  computed in `app/telemetry/cost.py` from model event timestamps and in
  `inspect.py` from the run's own counters; neither depends on the route or on
  task stages, so the baseline stays comparable. What needs updating is
  `stages()`, which recognises a stage by a `stage` key in the event data — the
  new loop should emit steps through the same context mechanism.
- **The "before" number is thin.** Six live turns produced 21.22 s of derived
  GPU and $0.0065 a successful turn. Reading a larger window costs nothing and
  wakes nothing — `tools/show_run.py --summary --last N` against the deployed
  database — and should be done before 4.1 changes anything.
- **The deletion is the largest in the project's history.** About 1,600 lines of
  application code plus adapter branches and their tests. Per `AGENTS.md` this
  is the size that routes to a bounded Claude implementation workflow in a Codex
  session; in a direct session the same start gate applies.

## 6. Still open, and not decided here

- **Coalescing changes what a turn is.** See 4.0. It needs its own decision
  because it redefines the unit every recorded number counts.
- **The local profile's sandbox.** The handoff describes the deployed shape and
  is silent on what executes arbitrary code on the human's own machine. That
  belongs to the sandbox item, not to step 4.
- **Whether `TurnRun.route` keeps a meaning** once there is one route. The
  column stays either way; writing `loop` into it or leaving it null is a
  detail for 4.1, and neither needs a migration.

## 7. What this document does not authorize

No implementation, no migration, no deploy, no GPU run, no sandbox, and no
product-runtime worker. The live acceptance in 4.1, 4.3 and 4.7 each need
explicit permission at the time, because each wakes a GPU.
