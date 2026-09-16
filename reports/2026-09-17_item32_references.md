# Roadmap 32, research: how the references run the tool loop

**Date:** 2026-09-17. **Status:** research with options; nothing here is
approved until the human says so in words. Written under the AGENTS.md rule
of 2026-09-14 (a large step starts with the references; the build cites the
report and is checked against it).

Read on 2026-09-17 by five subagents from the sources in §6: Claude Code
(official docs: permission-modes, hooks, sub-agents, interactive-mode, tool
search; the extracted prompts; a third-party reading of the source for
concurrency, marked as such), Codex (`codex-rs/core/src/tools/parallel.rs`,
`session/turn.rs`, `tools/orchestrator.rs`, `unified_exec/*`,
`handlers/plan.rs`, `handlers/tool_search.rs`, `handlers/multi_agents*`,
`session/input_queue.rs`), DeepSeek Harness (`docs/subsystems/{approval,
jobs, goal, plan, agent-team}.md`, `docs/tool-execution-pipeline.md`, the
config catalog, discussions), Hermes (`agent/conversation_loop.py`,
`agent/turn_tool_validation.py`, `tools/{terminal_tool,todo_tool}.py`, the
developer guide), OpenClaw (docs: agent-loop, queue, queue-steering, steer,
exec, exec-approvals, permission-modes, loop-detection, progress-card,
tool-search, subagents). A sixth subagent inventoried this harness's loop
(§2). What was not found is in §7.

The step (ROADMAP 32): independent tool calls in parallel, a mutating tool
serialised; tool output streams while it runs; approval per call with the
safe calls run first; stop and an interjection read between stream chunks;
schemas read per step so `find_tools` and MCP-on-demand can widen the set
inside a turn; plan mode as a third mode that withholds mutating tools; the
repeat counters demoted to information in the result; an empty completion
still ends with a line.

## 1. What each reference does

| question | Claude Code | Codex | DeepSeek | Hermes | OpenClaw | this harness |
|---|---|---|---|---|---|---|
| parallel calls | batches by safety, parallel or serial per batch; ten at once (`CLAUDE_CODE_MAX_CONCURRENCY`); `Edit`/`Write` and a non-read-only `Bash` serialised; results in the model's order (third-party reading; a `PostToolBatch` hook is official) | each call dispatched as it streams in, on its own task; one `RwLock` a step: a tool that "supports parallel" takes the read lock, any other the write lock; `FuturesOrdered` returns results in the model's order; no cap | `maxParallelToolCalls` 10; "parallel-safe calls may overlap … exclusive calls run alone as ordering barriers"; classification is per tool, "unary"; results "model-ordered" | a thread pool of 8 for a batch of non-interactive tools; a batch with an interactive tool (`clarify`) runs sequentially; "results are reinserted in the original tool call order" | a batch, once past its "launch checkpoint", runs to completion; results in "assistant source order" | one `for` loop, sequential (`graph.py` `run_tools`) |
| tool output while it runs | the model sees the whole result at the end; the UI a spinner; a background command's output is read on demand (`BashOutput`, `Monitor`) | the model sees output after `yield_time_ms`; a broadcast channel lets the UI show output live | the model sees the result at the end; background jobs "deliver completion to the owning agent in-session instead of polled" | the model sees the result at the end; `notify=true` fires one notification when a background process exits, delivered as a pseudo user message | "Tool start/update/end events emit on the `tool` stream"; a backgrounded command wakes the session on exit (`notifyOnExit`) | no tool start/end event; Chainlit shows "Completed." for a result, Telegram a label; a background command is polled with `command_output` |
| approval | per call: allow/ask/deny rules, then read-only actions auto-approved, then the classifier (`auto`); hooks may deny; modes `default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, `bypassPermissions` | per call in the orchestrator: `Skip` runs at once, `NeedsApproval` asks; `on-request` default; a denial is a failed tool result with the rejection text; approvals cached per action; `prefix_rule` "don't ask again for commands starting with"; `request_permissions` mid-turn | per call; outcomes `allowed-once`/`rejected`/`cancelled`/`unavailable`, "fail-closed"; an escalation only after a real denial marker; presets `workspace-write`/`read-only`/`danger-full-access` | per command: `[o]nce / [s]ession / [a]lways / [d]eny`; `always` persisted to the allowlist; skipped in a container; a denial: "a BLOCKED error to the agent telling it not to retry" | modes `deny/allowlist/ask/auto/full`; buttons ✅ once, ♾️ always, ❌ deny; a grant lives until revoked or `grantExpiresInDays`; "a denied tool result without running the command" | one `interrupt` for the whole batch before anything runs; Telegram answers one button and the rest are declined by the partial dict (a defect, §2); careful mode asks for every mutating call; no grant beyond a turn |
| stop and a message mid-turn | a message is queued and handed over "as soon as those tool calls finish, within the same turn"; `Esc` interrupts and kills the tool in flight | a message goes to a mailbox read at boundaries; `Op::Interrupt` cancels the turn's token, an in-flight tool is aborted ("aborted by user after N s"), background terminals survive | cancel is cooperative "before and after awaited boundaries"; the interrupt kills jobs and children | a new message abandons the API call; `/steer` is appended to the last tool result "once the current tool batch finishes" | read "at tool-launch boundaries as well as model boundaries"; a running tool finishes; the unstarted sequential tail is skipped with a synthetic result "Skipped to process an incoming message."; `/queue interrupt` aborts | stop read at the top of `run_tools` only; an interjection taken at the end of `run_tools`; nothing cancels a tool in flight |
| schemas per step | everything under ~10% of the window is inlined; above it MCP tools are deferred behind `ToolSearch`, loaded five a search, kept until compaction | `ToolExposure` Direct / Deferred; `tool_search` (BM25 over `search_text`) returns specs that become callable for the next calls; MCP lazily refreshed | "each step sends … its visible tool schemas"; narrowing on demand is a plugin (`tool_search`, a palette) | `get_definitions()` re-resolved each step against toolsets; skills a compact index, loaded with `skill_view`; no widening | policy-filtered per turn; Tool Search's search/describe/call bridge for large catalogs; a hook may narrow, not widen | compiled once per graph (`schemas`, `schema_tokens` at build time); MCP listed at first toolbox build; no `find_tools` |
| plan mode | a permission mode: reads and exploring commands, no edits; `ExitPlanMode` asks the person to approve the plan and switches the mode; `TodoWrite` is a separate ordinary tool | `ModeKind::Plan` prompt-only (issue #11115: mutation not blocked); `update_plan` a display tool, refused in plan mode | "a soft-guidance collaboration feature" with `exit_plan_mode`; no tool gate | none; `todo_list` a tool ("3+ steps", one in progress, completed only when verified) | `read-only` permission mode omits the mutation tools; `progress_card` a separate durable step list, "at most one step in_progress" | `/plan on` adds `todo_write`; `/mode careful` asks for every mutating call; no mode withholds tools |
| repeat guards | none documented; a Stop hook cap of 8; `--max-turns` headless | none found in source | `repeat-tool-reminder`: consecutive same-tool-same-args at 3/5/8 get an advisory note, "never blocks anything" | invalid tool name / invalid JSON retried 3 times then "Stopping as partial"; `max_turns` 500 | loop detection off by default: a warning appended to the result first, a block after the threshold; a post-compaction guard on `(tool, args, result)` | `MAX_IDENTICAL_FAILURES` 2 ends the turn's tools; `MAX_IDENTICAL_SUCCESSES` 2 refuses the call |
| empty completion | not documented | not found | "empty content stays out of derived history" | a nudge for a reasoning-only completion: "Produce your final answer as plain text now" | a tool-free finalization pass "if a required-reply turn ends after a fully settled tool batch without a composed answer"; `NO_REPLY` the silent token | `nothing_to_add`: the turn ends with no message; a cut before the first word gets `silent_cut()` |
| subagents | `Agent`: background by default, a completion notification in a later turn, isolated context, 20 at once, depth 3 | `spawn_agent` / `wait_agent` / `send_message`, background, a `FINAL_ANSWER` message, a shared filesystem, depth and concurrency limits | six providers (spawn, fork, acp, claude-code, codex, sdk); an agent team with a mailbox and a task DAG | `delegate_task`: fresh context, inherits the parent's toolsets, up to 10 (or 3) at once, "summaries are self-reports" | `sessions_spawn` returns `{accepted, runId, childSessionKey}`; the result announced; `sessions_yield` waits; 8 at once, 5 per agent, depth 5 | none |

## 2. What this harness has (the inventory, 2026-09-17)

- `run_tools` (`app/agent/graph.py`): stop checked first; the repeat
  guards; `pre_execute` for every call; one `interrupt` for every risky
  call of the batch before any tool runs; then a sequential `for` loop;
  interjections taken at the end and appended after the batch's results;
  the health question rides on the batch's patch.
- Events: `AssistantDelta`, `MessageProduced`, `AnswerWithdrawn`,
  `MessageTaken`; no tool start/end event. Chainlit takes `steps()`
  (no deltas) and shows a step per call with "Completed." for a success,
  never the result; Telegram shows a label per call and streams the
  answer's text.
- Approval: Chainlit asks every pending call in turn and resumes once with
  all the answers; Telegram sends one keyboard per call and resumes with
  `{one id: answer}` on the first button, so the other risky calls of the
  batch are treated as declined (`answers.get` → `None`). Careful mode
  (`.agent/careful.on`) makes every mutating call ask; `.agent/plan.on`
  adds `todo_write`.
- Schemas: `toolbox.schemas()` and `schema_tokens` once at graph build;
  the graph cached per thread until `rewire()`; MCP tools listed at the
  first toolbox build of the process.
- Guards: two identical failures end the turn's tools with
  `REPEAT_REASON` and `REPEAT_ANSWER`; two identical successes refuse the
  call with `DONE_REASON`; an empty completion ends the turn silently.
- The task path (planner/implementer/validator) was deleted on
  2026-08-30; only stale `.pyc` files remain. No subagent mechanism.

## 3. What follows

1. **Every reference runs the parallel-safe calls of a batch at once and
   serialises the rest with one gate**, classifies per tool (not per
   argument), caps the fan-out (8–10) and returns results in the model's
   order, a failure filling only its own slot. `Tool.mutates` is that
   classification here already.
2. **No reference streams a foreground tool's partial output to the
   model.** The UI gets tool start/end events; the model gets the result
   when the call returns; a long command is backgrounded and its exit
   wakes the model (Hermes `notify`, OpenClaw `notifyOnExit`, DeepSeek's
   job notice, Claude Code's completion notification), delivered as a
   message the model reads at the next boundary.
3. **Approval is per call and the safe calls do not wait.** Codex's
   orchestrator decides `Skip`/`NeedsApproval` per call; Claude Code
   auto-approves reads before anything else; a denial is a tool result the
   model reads. Every reference remembers a yes: once / this session /
   always (Hermes), a prefix rule (Codex), a grant until revoked or
   expired (OpenClaw).
4. **A message mid-turn is read at boundaries, never by aborting a running
   tool**; a stop aborts. Codex and Claude Code queue the message and hand
   it over as soon as the current calls finish; OpenClaw additionally
   skips the calls not yet started, with a synthetic result so the
   transcript stays valid.
5. **Schemas are the step's, and a large catalog is searched, not sent.**
   Codex, Claude Code and OpenClaw defer tools behind a search that makes
   the found ones callable from the next call on; Hermes re-resolves the
   set each step; DeepSeek sends everything visible and narrows by plugin.
6. **Plan mode, where it is enforced, is a permission mode**: Claude Code
   and OpenClaw withhold the mutating tools and exploring stays free;
   Claude Code ends it with an approval of the plan that switches the
   mode. Codex and DeepSeek have it as prompt guidance only. A todo list is
   a separate tool everywhere.
7. **A repeated call is information, not an ending.** DeepSeek appends an
   advisory note at 3/5/8 and never blocks; OpenClaw warns first and blocks
   only past a threshold, off by default; Codex and Claude Code leave it to
   the model; Hermes counts only malformed calls. Two identical failures
   ending a turn is this harness's alone.
8. **An empty completion gets one more chance**: OpenClaw's tool-free
   finalization pass, Hermes's nudge; then a fixed line.
9. **Subagents exist in every reference** and not here; the roadmap holds
   them under Not started. They touch the loop (a background result
   delivered as a message) the same way a background command's exit does:
   the delivery lane of item 2 is the one they would use.

## 4. Options, with a recommendation

### 4.1 Parallel calls (recommended shape)

Within one batch: the calls whose tool has `mutates=False` run at once
with `asyncio.gather` under a limit (`max_parallel_tool_calls`, a setting,
default 10, the references' number); a call whose tool mutates runs alone,
as a barrier, in the model's order (DeepSeek's shape, Codex's lock); the
results are placed in the model's order; a failure fills its slot only.
A call that needs approval is not launched until answered (4.3). The
trace records the batch's launch and each call's own timing as now.
Option B: a per-call `parallel_safe` flag separate from `mutates`. Not
recommended: `mutates` is the same fact, and a read that is not safe to
run beside a write does not exist here.

### 4.2 Tool output and the background lane

- Runtime events `ToolStarted(call)` and `ToolFinished(call, message)` on
  the event stream, beside `MessageProduced`, so an interface shows a call
  when it launches and its result when it returns (OpenClaw's `tool`
  stream). What Chainlit and Telegram do with them is roadmap 34; this
  step gives them the events. No partial stdout to the model (none of the
  references).
- A background command's exit is delivered: `run_command(background=true,
  notify=true)` (Hermes's `notify`, OpenClaw's `notifyOnExit` default on):
  when the process exits, the runner puts a notice on the turn's
  interjection lane ("bg-1 exited with code 0; the last lines: …;
  command_output reads the rest") which the loop reads at the next
  boundary (4.4); when no turn is running, the notice waits for the next
  turn as the first thing the model reads (a steering message). Option B:
  the exit starts a turn of its own (OpenClaw's heartbeat wake). Not
  recommended now: a turn nobody asked for is a priced call on its own;
  it belongs with cron/intents, which this harness does not have.

### 4.3 Approval per call, the safe calls first, a remembered yes

- The batch is split: the calls needing no approval run first (4.1); the
  risky ones are asked in one `interrupt` afterwards (one question per
  batch is what makes the resume restart-safe: a tool that ran before the
  pause must not run twice, and now the safe ones have run and are in the
  state before the pause). Answers may arrive one at a time: a call not in
  the answers dict is asked again on the next resume, not declined
  (Telegram's one-button flow becomes correct); a declined call answers
  `declined`; "approve all" is the interface sending every id (34).
- A remembered yes, in the harness: `Grants` per user and workspace,
  scope `turn` / `conversation` / `always`, keyed by tool name and, for
  `run_command`, a command prefix (Codex's `prefix_rule`, Hermes's
  allowlist); `requires_approval` consults it before asking; the
  interface's buttons ("once", "this conversation", "always") are 34's,
  the answer carries the scope. Stored in the workspace's `.agent/`
  (`grants.json`), the "always" ones there too, never in the repository.
- A denial stays what it is: a tool result "the person declined … do not
  try it again".

### 4.4 Stop and a message, read at boundaries; a stop cancels

- The loop reads the stop and the interjection lane at every tool-launch
  boundary (before each mutating call and before each parallel group) and
  between the model's stream chunks (the model call is cancelled on a
  stop, as Codex's token does), not only at the top of `run_tools`.
- A stop cancels the tool in flight: the executor's task is cancelled,
  `run_command`'s process killed with `_kill_tree`, the result "the user
  asked to stop; this call was ended". Background commands survive
  (Codex: "without terminating background terminal processes").
- A message mid-turn: the calls not yet launched still run, and the
  message is appended before the next model call, as now, only sooner
  (Claude Code's and Codex's shape, the human's rule of roadmap 20: a
  message is a comment on the work in progress). Option B, OpenClaw's:
  skip the unstarted tail with a synthetic "Skipped to process an
  incoming message". Not recommended as the default: the calls were the
  model's plan for the person's earlier request; the message is read
  before the next decision either way.

### 4.5 Schemas per step and `find_tools`

- `_ask` reads `toolbox.schemas()` and the schema tokens at each step from
  the toolbox object, and the toolbox may grow within a turn: a `Toolbox`
  gains `offer(tools)`.
- A catalog of deferred tools: the MCP servers' tools (listed lazily per
  server, as now, but not offered until found) and any capability the
  registry marks `deferred`; `find_tools(query)` searches names and
  descriptions (a plain scored match over words; BM25 is Codex's choice,
  the difference is not worth a dependency at this catalog's size) and
  offers the found tools for the rest of the conversation (Claude Code
  keeps them until a compaction). The brief says the catalog exists in one
  line generated from the registry ("Tools not listed can be found with
  find_tools: {servers or families}").
- Option B: keep every tool inline and add `find_tools` only for MCP.
  Recommended as the first shape: the built-in set is small (22 tools,
  ~9,500 tokens of schemas); deferring built-ins would cost a search for
  what every turn uses. MCP servers and later skills are the catalog.

### 4.6 Plan mode

A third value of `/mode`: `plan`. In it the toolbox withholds every tool
with `mutates=True` (they are not offered, so the schema list is shorter,
not refused), `run_command` included (Claude Code and OpenClaw's
`read-only`: "managed mutation tools omitted; exec is denied"; option B
offers `run_command` for exploring commands with approval on each, Claude
Code's shape for its `plan`; recommended B only if the human wants
exploring commands in a plan; the simpler A first). The brief's mode line
says it: "You are in plan mode: you read and look, you do not change or
run anything; write the plan as your answer." The person leaves it with
`/mode default` or `/mode careful`; the approval of the plan by a button
that switches the mode is 34's. `todo_write` stays what it is, under its
own switch.

### 4.7 The repeat counters demoted; the empty completion

- A repeated identical failure: the call runs, and its result carries the
  count: "(this exact call failed the same way N times in this turn)"
  from the second time (DeepSeek's advisory; OpenClaw's warning first). No
  ending. A repeated identical success: the call runs, the result carries
  "(this exact call already succeeded N times in this turn)". The counts
  are settings (`repeat_note_after`, default 2). A hard stop on a runaway
  loop: only from a much larger count, a setting (`repeat_stop_after`,
  default 8, OpenClaw's critical block), and it ends the batch, not the
  turn's tools: the model gets one more response with its tools.
- An empty completion: one tool-free finalization call ("Say in one line
  what you did and what is left."), and if that is empty too, the fixed
  line "(no answer was produced)". `silent_cut()` stays for the
  max-tokens case.

### 4.8 Out of this step, noted

- Subagents, skills and hooks (Not started): they use the lanes and the
  events of this step; a report of their own when their turn comes.
- Interfaces: showing tool output, approve-all and the scope buttons, the
  plan approval button, the background notice's look (34).
- The provider's `parallel_tool_calls` flag and `tool_choice` (33).

### 4.9 Measurement

- Offline: a batch of three reads runs at once (a fake tool records
  overlap), a write between them is a barrier, results in order, one
  failure in its slot; the safe calls run before the question; a partial
  answers dict asks again; a grant skips the question; a stop mid-batch
  cancels the running call and ends its process; an interjection read
  before the next launch; a toolbox that grows and schemas that follow;
  `find_tools` over a fake MCP catalog; plan mode offers no mutating tool;
  the repeat note from the second time, the stop at the eighth; the
  finalization pass; a background exit notice on the lane.
- Live (a gate, priced): the mini set once (B, C, F, H, M exercise the
  batch, the approval and the message mid-turn) beside the 2026-09-16
  "after" run's numbers; one turn with two MCP tools found with
  `find_tools`; one Telegram-shaped approval with two risky calls answered
  one at a time (through `loop_live`'s fake answers, no deploy).

## 5. Acceptance for the step

Each row of §4.1–4.7 built and checked by the offline tests of §4.9; the
build report lists every row with where it landed; the mini set's checks
pass as on 2026-09-16; the deployed profile's wiring unchanged
(`tests/test_profiles.py`), the events and the grants reaching Telegram at
the next deploy.

## 6. Sources

Claude Code: code.claude.com/docs/en/{permission-modes, hooks-guide,
sub-agents, interactive-mode, agent-sdk/tool-search};
platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool;
Piebald-AI/claude-code-system-prompts (`tool-description-exitplanmode.md`);
claude-code-from-source.com ch. 7 (third-party). Codex: openai/codex
`codex-rs/core/src/tools/{parallel,router,orchestrator,approvals,spec_plan}.rs`,
`core/src/session/{turn,input_queue,turn_suspension,multi_agents}.rs`,
`core/src/stream_events_utils.rs`, `core/src/unified_exec/{mod,process,
process_manager}.rs`, `core/src/tools/handlers/{plan,tool_search,
multi_agents}.rs`, `protocol/src/{protocol,config_types}.rs`, issue #11115.
DeepSeek Harness: `packages/core/agent-loop/README.md`,
`docs/tool-execution-pipeline.md`, `docs/subsystems/{approval,jobs,goal,
agent-team,plan}.md`, `packages/{subagent,jobs}/README.md`, the config
catalog, discussions #4294, #1604, #2137, #5584. Hermes:
`agent/{conversation_loop,turn_tool_validation,prompt_builder}.py`,
`tools/{terminal_tool,todo_tool}.py`, docs developer-guide/{agent-loop,
tools-runtime}, user-guide/security, guides/delegation-patterns,
features/delegation. OpenClaw: docs {concepts/agent-loop, concepts/queue,
concepts/queue-steering, tools/steer, tools/exec, tools/exec-approvals,
gateway/permission-modes, tools/loop-detection, tools/progress-card,
concepts/standing-intents, tools/tool-search, tools/index,
tools/subagents, tools/subagents/{tool-reference,operations}}.

## 7. Not found

- Claude Code's concurrency internals from an official source (a
  third-party reading only); any reference's documented handling of an
  empty completion beyond OpenClaw's pass and Hermes's nudge; Codex's UI
  event for live exec output; whether a sibling call runs while one call
  waits for approval, in any reference; Hermes's concurrent-children
  default (10 or 3); DeepSeek's job-notice message shape.
