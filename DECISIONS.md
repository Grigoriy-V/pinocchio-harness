# Decisions

Approved durable architectural and scope choices, and why they were made.
Not a roadmap, a current-state map or evidence: `ROADMAP.md` owns work,
the four `docs/` maps own the current system, `reports/` owns measurements.
Read an entry when a map links it, when its reason matters, or when the
choice is being reconsidered. A decision is a draft until the human
approves it in words.

Entries are in date order. Each has Decision, Why, Consequences, and a
Supersedes line where one applies. A superseded entry stays, shortened,
and says what replaced it.

## Catalog

| Date | Decision | Standing |
|---|---|---|
| 2026-08-01 | All model access goes through `ModelBackend` | standing |
| 2026-08-01 | LangGraph without LangChain message types | standing |
| 2026-08-01 | Defer an application HTTP layer | amended 2026-08-27 |
| 2026-08-01 | Memory retrieval without a vector store | standing |
| 2026-08-01 | Conversation data is separate from checkpoints | generalized 2026-08-27 |
| 2026-08-01 | Long-term facts require an explicit save | standing (scope by user since 2026-08-27) |
| 2026-08-01 | Tools declare consequence; the runtime owns consent | consent half superseded 2026-08-30 |
| 2026-08-01 | Token accounting comes from the model server | standing |
| 2026-08-01 | Version 1 closed at Stage 3, then reopened | historical |
| 2026-08-01 | No general project log | standing |
| 2026-08-02 | Benchmarks do not define the product agent | standing |
| 2026-08-02 | Workspace confinement accepts absolute paths | standing |
| 2026-08-02 | One interface; the harness decides whether to act | standing |
| 2026-08-27 | The product is a deployable personal assistant | standing |
| 2026-08-27 | One persistence contract, SQLite local, Postgres deployed | standing |
| 2026-08-27 | An HTTP layer requires a separately hosted caller | standing |
| 2026-08-28 | Optimize a replacement model deployment, not the baseline | standing |
| 2026-08-28 | Database latency is not a gate; placement unpinned | standing |
| 2026-08-29 | Observation and presentation are separate actions | standing |
| 2026-08-29 | Search, fetch and rendering are separate capabilities | standing |
| 2026-08-29 | Project configuration is the source; platforms get a copy | standing |
| 2026-08-30 | Work inside the workspace does not ask permission | standing |
| 2026-08-30 | Same-user presentation and sandboxed work stay autonomous | standing |
| 2026-08-30 | A control signal never travels in the conversation queue | standing |
| 2026-08-30 | Stored history is canonical; the model sees a projection | standing |
| 2026-08-30 | The engine's ceiling is set once; context is spent by the app | standing |
| 2026-08-30 | Turn stopping is a minimal steering seam | standing |
| 2026-08-30 | The prompt is assembled; a person's instructions are an overlay | standing |
| 2026-08-31 | The plan is the state of one turn | amended 2026-09-03 twice |
| 2026-09-03 | The surface is shortened by age; the volatile layer goes last | amended 2026-09-04 |
| 2026-09-03 | The plan is off unless the person turns it on | standing |
| 2026-09-03 | A tool result names the action its output enables | standing |
| 2026-09-03 | An open plan item no longer refuses the ending | standing |
| 2026-09-03 | Text beside a tool call is said once; a local page may load its CDN | standing |
| 2026-09-03 | A tool result is a typed outcome; the runtime survives any model | standing |
| 2026-09-03 | A summary or stub is reachable by search and by position | standing |
| 2026-09-04 | A dead worker's turn is taken up; replay is the tool's to say | standing |
| 2026-09-04 | The last two exchanges stay verbatim; a fold takes only what must go | standing |
| 2026-09-04 | Generated code runs where no secret is; installs live in the workspace | standing |
| 2026-09-05 | The goal is the request's parts, written once by the model | standing, measured next |
| 2026-09-05 | A second model is a second App, pointed at by configuration | standing, GPU Apps |
| 2026-09-06 | A hosted model is a set of lines; default GLM at Novita/Z.ai | standing |

---

## 2026-08-01 — All model access goes through `ModelBackend`

Decision: application code talks to the model through the asynchronous
`ModelBackend` contract; provider SDKs, tokenizers and processors stay in
`app/models/`.

Why: changing the model or endpoint must not rewrite the agent, context,
memory, tools or interfaces.

Consequences: provider types never enter domain code; model-shaped
estimation belongs behind the boundary too.

## 2026-08-01 — Use LangGraph without adopting LangChain message types

Decision: LangGraph owns orchestration and resumable interrupts over the
project's own messages and state; no LangChain agents, `ToolNode`,
`create_react_agent` or `langchain_core` message classes.

Why: the prebuilt nodes would move the multimodal contract outside project
control for little saved code.

Consequences: graphs and tool execution stay explicit project code; a
transitive install is not permission to import it.

## 2026-08-01 — Defer an application HTTP layer

Decision: no FastAPI while every interface runs in-process; an HTTP
boundary only for a separately hosted caller.

Why: a layer with no remote consumer is ceremony.

Consequences: application code cannot depend on request or session objects.
Amended by 2026-08-27, "An HTTP layer requires a separately hosted caller".

## 2026-08-01 — Start memory retrieval without a vector store

Decision: long-term facts use text retrieval in the existing store;
embeddings and a vector database wait until text retrieval works and is
measured.

Why: prove the context and memory lifecycle before a second storage system.

## 2026-08-01 — Conversation data is separate from checkpoints

Decision: threads, messages, summaries and facts belong to the
application's `ConversationStore`; LangGraph checkpointers hold only
in-flight graph state.

Why: conversation is portable product data; checkpoints are framework-owned
execution state.

Consequences: history is never reconstructed from checkpoints. Generalized
by 2026-08-27, "One persistence contract".

## 2026-08-01 — Long-term facts require an explicit save decision

Decision: a fact enters memory only through an explicit save; model output
is not harvested automatically.

Why: generated claims can be wrong, and persistence makes them influence
unrelated turns.

Consequences: facts carry provenance and are scoped by user (the original
global scope was superseded 2026-08-27; the explicit-save rule stands).

## 2026-08-01 — Tools declare consequence; the runtime owns consent

Decision: a tool declares consequence (`requires_approval`, formerly
`destructive`); the runtime, not the tool or a UI adapter, decides what to
do about it.

Consequences: the consent half, asking before every workspace write, was
superseded 2026-08-30 ("Work inside a person's own workspace does not ask
permission"); the declaration and the runtime's ownership stand.

## 2026-08-01 — Token accounting comes from the model server

Decision: request size comes from server usage and model limits; any
pre-request estimate is owned by `ModelBackend`, not by a tokenizer in
context code.

Why: only the serving model counts its text and multimodal tokens correctly.

Consequences: no duplicated tokenizer; message count is not token count;
context decisions use the configured fraction of the reported limit (or the
set's own budget when the server reports none, 2026-09-06).

## 2026-08-01 — Version 1 closed at Stage 3, then reopened for product completion

Historical. Version 1 was first closed at the Stage 3 local product, then
reopened the same day until persistent chat, bounded attachments,
recoverable tool failures, honest overflow and a real browser smoke passed
as a user experience, because first-pass checks had proven narrower
properties than the closure claimed. The rule that survives: a user-facing
capability needs short end-to-end evidence (`AGENTS.md`).

## 2026-08-01 — No general project log

Decision: no `PROJECT_LOG.md`. Durable choices here, evidence in
`reports/`, structured outcomes in the two JSONL journals; each fact
recorded once in its owning document.

## 2026-08-02 — Benchmarks do not define the product agent

Decision: the assistant is a general autonomous harness; benchmark-specific
routes, prompts and verifiers stay evaluation artifacts.

Consequences: users state outcomes through one interface; scenario success
alone is not product acceptance. Clarified by the next entry.

## 2026-08-02 — Workspace confinement accepts absolute paths

Decision: every path-taking tool validates against an explicit root and
accepts relative and absolute paths that resolve inside it; ambiguous names
are clarified, not guessed.

Why: the workspace is a permission boundary, not a path format.

## 2026-08-02 — One interface; the harness decides whether to act

Decision: every ordinary request enters one harness, which decides whether
to answer, plan, use tools, validate or repair.

Consequences: no conversation/agent selector, task route or tool button.

## 2026-08-27 — The product becomes a deployable personal assistant

Decision: the local agent also deploys serverless for one owner and a few
other users, first through Telegram; local and deployed profiles share one
`app/`, deployment is configuration, infrastructure and adapters.

Consequences: a capability is complete only when it works in both profiles;
user-owned state is scoped by user. Supersedes the policy-platform/MCP
definition of Version 2.

## 2026-08-27 — One persistence contract, local and deployed implementations

Decision: one `ConversationStore` contract; SQLite locally, PostgreSQL
deployed; both run the same contract tests and scope data by user.

Why: zero-setup local profile; concurrent serverless workers need a
network database, not SQLite on a shared volume.

## 2026-08-27 — An HTTP layer requires a separately hosted caller

Decision: Telegram and Chainlit stay in-process; `app/api/` waits for a UI
or consumer hosted apart from the application.

Amends 2026-08-01 "Defer an application HTTP layer": the trigger is a
separately hosted caller, not merely a second consumer.

## 2026-08-28 — Optimize a replacement model deployment, not the baseline

Decision: serving optimizations are validated under a separately named
deployment; the application moves only after acceptance; the measured
baseline stays as rollback until separately removed.

Why: a new identity preserves comparison and rollback; a measured
configuration is never silently redefined (`AGENTS.md`).

Consequences: deleting the rollback deployment is a separate destructive
gate.

## 2026-08-28 — Database latency is not a gate and control placement stays unpinned

Decision: the invented 100 ms warm / 500 ms cold database limits are
withdrawn; Neon stays in `us-east-2`, Modal functions unpinned; the probe
is an instrument.

Why: after reads collapsed to one round trip, database execution was
negligible and the rest was placement; pinning cost more than it saved.

## 2026-08-29 — Observation and presentation are separate agent actions

Decision: tools that read, render or inspect return evidence to the agent;
only an explicit presentation action (`send_file`) makes content outbound.

Why: automatic forwarding of tool media lets an adapter decide what the
person sees. Supersedes the automatic delivery of 2026-08-29 morning;
`docs/PRODUCT.md` carries the rule.

## 2026-08-29 — Web search, fetch and visual rendering are separate capabilities

Decision: Firecrawl for search leads, bounded direct HTTP for text fetch,
an isolated secretless Chromium function for rendered inspection; Firecrawl
scraping is an explicit fallback.

Why: different costs, evidence and trust boundaries; public page JavaScript
never runs beside secrets or workspaces.

## 2026-08-29 — Project configuration is the source; platforms receive a copy

Decision: runtime values originate in the project's `.env`; the deployment
receives an allow-listed copy through `tools/sync_control_secret.py`;
provider dashboards are not an authoring source.

Consequences: deployment-only values may be renamed on publication so they
cannot configure the local profile by accident.

## 2026-08-30 — Work inside a person's own workspace does not ask permission

Decision: routine mutation inside the granted workspace runs without
asking; the boundary, not the call, is what is authorized. Approval stays
for effects that leave it: sending or publishing, spending money, changing
infrastructure, touching data the person did not put there.

Why: the workspace is already confined per user; asking before each write
buys no safety and turns autonomous work into prompts.

Consequences: consent policy lives in the tool execution seam, not the
loop. Supersedes the consent half of 2026-08-01.

## 2026-08-30 — Same-user presentation and sandboxed work stay autonomous

Decision: presenting a workspace file back to the same person is part of
the request, not a second approval; inside a separately authorized sandbox
run, shell, Python, installs and workspace mutation do not ask command by
command. Starting a product-runtime worker stays its own human gate.

Consequences: `send_file` stays non-destructive; a sandbox plugs into
`execute` without changing consent semantics. Clarifies the entry above.

## 2026-08-30 — A control signal never travels in the conversation queue

Decision: an update that acts on what is already running, or is answered
from storage, is delivered out of band: it skips the conversation lease and
the local per-chat lock, and the running turn checks for it at each step
boundary. `/stop` is the case that matters; the model-free commands travel
the same way.

Why: the human's instruction after 4.0: `/stop` queued behind the turn it
exists to stop arrives after that turn ended.

Consequences: `telegram_updates.control`, `turn_stops` with the sequence a
stop arrived with; a stop applies to every turn begun before it and none
after. `reports/2026-08-30_v2_one_loop.md`.

## 2026-08-30 — Stored history is canonical; what the model sees is a projection

Decision: the stored conversation is lossless and never rewritten by
anything that makes a request smaller. Summaries, shortened results and
dropped media are a model-visible surface derived from it and may be
rebuilt differently. Compaction is in place: one `thread_id` before and
after. What the product depends on (goal, pending decision, what was done)
lives in structured state, not only in the surface's text.

Why: every way of making a request smaller is lossy; the moment a lossy
step writes back, the loss is permanent and memory becomes an artefact of
whichever summarizer ran.

Consequences: folding writes a summary and the position it covers, never a
deletion; compaction has a durable record in the memory schema; `todo` and
a pending `ask_user` are structured state.

## 2026-08-30 — The engine's context ceiling is set once; context is spent by the application

Decision: `MAX_MODEL_LEN` is chosen once, as high as the measured KV pool
validates, and is not a tuning dial. How much context a turn uses is
decided in the application from the ceiling it reads at `/v1/models` and a
single fraction; changing it never needs a deploy.

Why: the ceiling only validates against the pool; `GPU_MEMORY_UTILIZATION`
sizes it. Raising the ceiling costs one uncached boot and concurrency, not
VRAM. Prefill is dominant and superlinear, so a long context is still paid
in seconds even when it fits.

## 2026-08-30 — Turn stopping is a minimal steering seam

Decision: stopping runs only when a model result would otherwise end the
turn; its default is to stop; it continues only on explicit structured
steering from an extension. No validator model, finish tool or text
heuristic; the model decides whether an outcome needs validation and with
which observation tool.

Why: a mandatory validator recreates the fixed repair lifecycle; heuristics
move a product decision out of the agent.

Consequences: a steered draft never becomes a second answer; a plain text
write gains no validation pass.

## 2026-08-30 — The prompt is assembled, and a person's instructions are an overlay

Decision: the system layer is assembled from parts ordered by stability:
core (names no tool, format or workflow), capability guidance generated
from the wiring in `app/capabilities.py`, tool schemas, the person's
standing instructions, the summary, retrieved facts, the conversation. A
person has one instruction file, `AGENTS.md` in their workspace, read every
turn as its own message; its authority is below product and capability
policy and it is not memory.

Why: hand-correcting one paragraph neither produced the behaviour nor was
what changed it; the real causes were facts about the wiring the prose
could not state without going stale. Ordering by stability is what a served
prefix cache needs.

Consequences: a grant that withholds a tool withholds the sentence about
it; a test forbids tool names in the core; instructions are bounded at
8,000 bytes; `/agents` is model-free. Supersedes the hand-written
`DEFAULT_SYSTEM_PROMPT`.

## 2026-08-31 — The agent's plan is the state of one turn, and lives in that turn

Decision: `todo_write` gives the model a whole-list plan that is the state
of one unfinished turn: it survives compaction, interrupt, resume and a
restarted worker, and is gone at the next user message. No table, no
schema; the list is the arguments of the model's last accepted call inside
the turn's messages. Whole-list replacement only; at most one item
`in_progress`.

Why: the lifetime asked for is what the loop already gives its messages;
carrying a plan between turns is a different product. Planning is state
the model decides to use, never a mode the harness switches into.

Consequences: `Candidate.steerings`; `todo_write` is model-free of any
root. Amended 2026-09-03: the plan is off unless turned on, and its ending
objection is off.

## 2026-09-03 — The model-visible surface is shortened by age, and the volatile layer goes last

Decision: before every model step the request is a projection with three
rules only: retrieved facts go after history, immediately before the turn,
so the prefix ahead of them is stable; a tool result older than the newest
`keep_results` (two) is a stub naming tool, subject, size and the way back,
while failures, short results and the model's own text and arguments are
never shortened; pictures share one media budget, newest kept. **Amended
2026-09-04:** the turn in progress is never shortened, only stored history
(ISS-0041); a long turn is bounded by the size fold. A person chooses
their context size (`small`/`normal`/`large`) and may `/compact`.

Why: prefill is dominant and the prefix cache is real (98% reuse, 1,370 ms
to 82 ms); a twelve-step turn carried every earlier argument and screenshot
on every step. The references clear old results before summarizing and keep
the full text retrievable; here the full text is history itself.

Consequences: `app/context/window.py` (`surface`, `shortened`,
`facts_layer`), `app/context/choice.py`; schema 3 (`messages.failure`,
`compactions`). `reports/2026-09-03_v2_context_engine_review.md`.

## 2026-09-03 — The plan is off unless the person turns it on

Decision: `todo_write` and the planning guidance are offered only when the
person switched planning on (`/plan on`, marker `.agent/plan.on`), per
person across conversations and interfaces. `send_file` takes several paths
in one call; Telegram delivers several items of one kind as one album.

Why: the same request with the plan on and off: 12 calls / 90 s against
5 / 62 s, the same files delivered, the page and the answer written twice
with it on. Until the plan earns its cost it is not part of the default.

## 2026-09-03 — A tool result names the action its output enables

Decision: a tool whose result is a workspace item the person might want
says in the result how it reaches them, in the shape of the call:
`to hand it to the person: send_file(path="…"); nothing is sent otherwise`
(`handover` in `app/tools/base.py`, used by every such tool). The decision
to send stays the model's.

Why: four live turns ended with a markdown image of a workspace path; a
bare path reads as something to embed, a call as something to make. If the
phrase does not hold live, the human has reopened an adapter delivery of a
markdown image, on the condition that no delivery path blocks another.

## 2026-09-03 — An open plan item no longer refuses the ending

Decision: the `todo` extension of the stopping seam objects to no ending
by default; the seam, extension and `limit` stay.

Why: every objection produced a tick and the same answer written again,
never more work; the plan is gone at the next message, so an open item
costs the person nothing visible. Supersedes the objection of 2026-08-31.

## 2026-09-03 — What the model says beside a tool call is said once, and a local page may load its CDN

Decision: text written in the same completion as a tool call is delivered
as written and stays delivered; a later message repeating it verbatim is
not sent again; the core prompt says so. A local artifact is served to the
browser from the workspace and may reach public addresses under the public
renderer's policy; private and link-local addresses are refused and
reported.

Why: the adapter withdrew a whole answer and the model wrote it again
(ISS-0009); the screenshot of a page without its Tailwind was of a boundary
the person's browser does not have (ISS-0017). A mechanical delivery
backstop in the adapter was rejected the same day.

Consequences: `Delivery.place`; the Telegram adapter keeps the preview as
the answer and holds a refused draft; `open_browser` takes `serve` and
`allow`.

## 2026-09-03 — A tool result is a typed outcome, and the runtime survives any model

Decision: a tool returns content or raises `ToolError` with a stable code;
the executor turns every outcome into `ToolOutcome(content, failure)` and
owns normalization, bounds, sanitizing, per-tool timeout, telemetry with the
reason, and the projection. `failure is None` is the only definition of
success. The runtime, not the server, survives what a model emits: an
unreadable call becomes one refused call with the tool's signature, never a
failed request; names resolve against the allowlist, arguments are coerced
to the schema, fragments removed, nothing invented. The corrected Gemma 4
parser stays offline. One implementation per capability; a backend
interface only at the first real second implementation. The browser is one
session with the full operation set, snapshot-with-refs first.

Why: the tool boundary was the last Version 1 shape in the loop, a string
convention four consumers parsed; the context engine cannot be built on
prose it parses; a per-model parser fixes one emission on one server.
DeepSeek Harness, Hermes and OpenClaw converge on the same shape
(`reports/2026-09-03_v2_tool_system_references_and_queue.md`).

Consequences: `Message.failure`; `tool_failed` carries code and message;
`docs/v2_tool_system.md`. Refines 2026-08-01 (declaration renamed
`requires_approval`) and the 2026-08-30 execution seam.

## 2026-09-03 — What a summary or a stub stands for is reachable by search and by position

Decision: `search_history` is full-text search over the words of stored
messages within this person's conversations; `read_history` returns
messages by position in pages. A stub names its stored position; the
summary says the exact words are kept. Nothing found is injected: the model
asks. The 32k per-result cap stays; reach comes from paging (`offset` on
`read_file`, `fetch_page`, `read_history`), not larger rows.

Why: the canonical-history decision was justified by recoverability, and
recovery had existed on paper. The question is keyword search by nature, so
BM25 over the person's words is the tool and vectors are not needed.

Consequences: `app/tools/history.py`, `app/tools/paging.py`,
`ConversationStore.search_messages`, schema 4 (derived `text` column with
an index). `reports/2026-09-03_v2_history_recovery_review.md`.

## 2026-09-04 — A turn a worker died in is taken up, and what may run again is the tool's to say

Decision: a worker that dies mid-turn leaves the turn in the checkpoint and
the next worker continues it. Each call of the step it died in is answered
before the graph moves on: run again if the tool is `replay_safe`
(reading), otherwise `interrupted`, "whether it ran is unknown". The
harness never repeats a side effect on its own and never drops done work.
The lease is shorter than the container's life, the update is re-invoked
once after a kill, and an update claimed three times is given up on.

Why: a killed turn was silently lost and then replayed from the start with
every tool run twice. The references do the same two things: a durable
"turn is running" and per-call recovery knowledge; the replay decision is a
property of the tool.

Consequences: `Agent.unfinished`, `Agent.resume_interrupted_events`,
`Tool.replay_safe`, `LEASE_SECONDS` derived from the Modal timeout.
`reports/2026-09-04_v2_restart_resume_review.md`.

## 2026-09-04 — What stays verbatim is the last two exchanges, and a fold takes only what has to go

Decision: what always stays verbatim is the last two exchanges (a person's
message and everything up to the next), not the newest eight messages;
inside one long turn, the newest two assistant steps. A fold by size folds
the oldest exchanges one at a time until the overshoot plus the summary's
room is freed; a fold by count or `/compact` folds everything older than
the floor. `ContextPolicy.keep_turns`, `AGENT_KEEP_TURNS`.

Why: eight messages was two sentences in one conversation and half a
window in another, and the only measure of how much a fold took; the human
called it a crutch.

Consequences: `verbatim_floor`, `cut_for`, `SUMMARY_ALLOWANCE` in
`app/context/summary.py`; `keep_recent` is gone.

## 2026-09-04 — Generated code runs where no secret is, and what it installs lives in the workspace

Decision: one tool, `run_command`, a fresh shell per command in the
person's workspace. Deployed: a Modal Function beside the renderer, the
worker's image plus base tools, the workspaces Volume, no control-plane
secret, 180 s scaledown; nothing installed into a container survives it,
what is installed goes into the workspace (`HOME` there, a venv there).
Locally: a process on the person's machine with a reduced environment,
and on Windows a write-restricted token so a command writes only inside
the workspace (`app/tools/shell_windows.py`; a rule about installers was
a crutch). Two modes per conversation: `full` (default) and `careful`
(workspace changes ask first); effects beyond the workspace stay gated in
both. A Modal Sandbox is v2.

Why: a coding agent needs an environment that lives through a session;
the worker cannot host commands (scales to zero in 60 s, a child can read
its secrets through `/proc`); a secretless Function is the renderer's
pattern at a third of a Sandbox's price.

Consequences: `app/tools/shell.py` with a one-method `Runner`; the
`run_command` Function in `deploy/modal/control_app.py`; `mutates` on
tools. Supersedes the 2026-08-30 wording "isolation, not a prompt, is the
boundary for generated code" as a universal.
`reports/2026-09-04_v2_isolated_execution_review.md`.

## 2026-09-05 — The goal is the request's parts, written down once by the model; the plan stays a mode of its own

Decision: `set_goal`, offered always: when a request asks for more than
one thing, the model writes the parts down once before starting, in the
person's words, and never updates them. The goal lives where the plan
lives, in the call's arguments inside the turn's messages. Nothing in the
loop reads it back; no second model call about the request. `/plan` and
`todo_write` stay a separate mode.

Why: with the plan on, multi-part requests were finished and without it
the model stopped when it had something; the benefit was the list of parts
in context, not the bookkeeping. A self-check on the stopping seam was
built first and measured out (answered `done` to half a handover); the
human rejected doubling the cost of a turn.

Consequences: `app/tools/goal.py`; the seam back to one extension. To be
measured on G and P with the plan on and off (roadmap item 8); if the
parts written down change nothing, the tool comes out.

## 2026-09-05 — A second model is a second App, and the assistant is pointed at one by configuration

Decision: a model to try or keep gets its own Modal App with its own
identity, image, snapshot and scale-to-zero, sharing machinery by import
and Volumes by name; the assistant is pointed at it by configuration
alone. First `assistant-llm-qwen` (Qwen3.8-27B FP8, L40S, 128k in bf16
KV), then on the human's word `assistant-llm-qwen-int4` (A100-40GB, a
snapshot half the size; restore 20–31 s against 19–86 s). Every Qwen App
shares one spec and a CPU `preflight` that applies the pool arithmetic
before a GPU is paid for. The Qwen Apps run vLLM 0.28.0 / transformers
5.15.0 with prefix caching asked for explicitly and thinking off by
default (`MODEL_CHAT_TEMPLATE_KWARGS` the dial); `dry_run` exists and is
not required. The FP8 App is not run again (cost).

Why: `assistant-llm-v2` is the configuration behind every recorded
measurement; the harness binds to nothing model-specific, so the switch is
configuration, which was the point of `ModelBackend`. 0.28.0 made prefix
caching the default for hybrid models; it had been off through every Qwen
boot, so every call prefilled the whole prompt.

Consequences: `deploy/modal/model_app_qwen.py`, `model_app_qwen_int4.py`.
Supersedes the 2026-08-30 note that 128k belongs to "L40S with Qwen3-8B
and quantized KV". `reports/2026-09-05_qwen38_second_model.md`. Since
2026-09-06 the GPU Apps are model sets, deployed and not in use.

## 2026-09-06 — A hosted model is a set of lines, and the default is GLM 5.3 Flash through OpenRouter

Decision: a model the assistant can talk to is a named set in
configuration (`MODEL=<name>` reads `MODEL_<NAME>_*` and
`AGENT_<NAME>_CONTEXT_TOKENS`; plain `MODEL_*` is the unnamed set); every
set is published with the control secret; switching is the `MODEL` line
and a control-plane redeploy. Hosted models are reached through
OpenRouter, not CometAPI. **The default is GLM 5.3 Flash served by Novita
(fp8), Z.ai as the fallback** (`provider.order`, `allow_fallbacks:
false`), `thinking: disabled` and `reasoning_effort: low`, which together
leave no reasoning tokens with tools. DeepInfra was declined (fp4, no
cache, five times the price per call). Gemini 3.1 Flash-Lite is paused
until its cache lands, through OpenRouter's `cache_control` breakpoints
in the existing client rather than a native adapter. The human's tiers
among hosted models stand: a default, a stronger one (Gemini 3.5
Flash-Lite), a cheaper one.

Why: the 2026-09-06 suite through one OpenAI-compatible client: Gemini
14 of 16 at 1.5–3 s per call, GLM 13 of 15 at $0.008 per suite; CometAPI
delivered GLM whole after 13–100 s and Gemini's cache landed on 2 of 60
calls; through OpenRouter GLM is 3–8x faster, ~5 s per call, cached on
every repeat, $0.00007 a call. Speed above all, then price (the human).

Consequences: `ModelSettings` and `AgentSettings` read the chosen set;
`MODEL_EXTRA_BODY` carries what a service wants and the OpenAI shape has
no word for; `MODEL_DUMP_DIR` keeps a call's raw stream; the chosen
`AGENT_<NAME>_CONTEXT_TOKENS` stands when the server reports no window.
The GPU Apps remain sets of their own. Which set the assistant uses from
Telegram, and Gemini's cache, are roadmap item 13.
`reports/2026-09-06_hosted_model_cometapi.md`.
