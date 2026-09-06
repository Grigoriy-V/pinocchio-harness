# Decisions

This file records approved durable architectural and scope choices. It is not a
roadmap, current-state map, work log or evidence report.

Use the four canonical documents for the current product, system, code and
operations. Use `ROADMAP.md` for current work and authorization. Read a decision
when a canonical document links it, when its rationale matters, or when the
choice is being reconsidered. Measurements and implementation evidence belong
in `reports/`.

A decision is a draft until the human explicitly approves it. Every entry uses
the same fields; use `None` when it neither replaces nor is replaced by another
decision.

## 2026-08-01 — All model access goes through `ModelBackend`

Decision

Application code talks to the model through the asynchronous `ModelBackend`
contract. Provider SDKs, tokenizers and processors stay inside `app/models/`.

Why

Changing the model or its OpenAI-compatible endpoint must not require rewriting
the agent, context, memory, tools or interfaces.

Consequences

Provider-specific types and imports do not enter application domain code. Any
model-shaped estimation also belongs behind the model boundary.

Supersedes / Superseded by

None.

## 2026-08-01 — Use LangGraph without adopting LangChain message types

Decision

LangGraph owns orchestration and resumable interrupts, using the project's own
messages and state. The application does not use LangChain agents, `ToolNode`,
`create_react_agent` or `langchain_core` message classes.

Why

The prebuilt nodes require LangChain model/message types and would move the
multimodal application contract outside project control for little saved code.

Consequences

Agent graphs and tool execution remain explicit project code. A transitive
LangChain installation is not permission to import it into `app/`.

Supersedes / Superseded by

None.

## 2026-08-01 — Defer an application HTTP layer

Decision

Do not add FastAPI while every interface runs in the same process as the
application. Add an application HTTP boundary only for a separately hosted
caller.

Why

An HTTP layer with no remote consumer adds ceremony without creating a useful
boundary. Keeping interfaces thin makes that layer cheap to add when real.

Consequences

Application code cannot depend on request, response or session objects, and
business logic cannot move into interface callbacks.

Supersedes / Superseded by

Amended by `2026-08-27 — An HTTP layer requires a separately hosted caller`.

## 2026-08-01 — Start memory retrieval without a vector store

Decision

Long-term facts use text retrieval in the existing store. Embeddings and a
vector database are deferred until text retrieval works and is measured.

Why

The context and memory lifecycle must be proven before adding another storage
system and retrieval model.

Consequences

A vector database is not part of the current architecture or roadmap merely
because it may improve retrieval later.

Supersedes / Superseded by

None.

## 2026-08-01 — Conversation data is separate from checkpoints

Decision

Threads, ordered messages, rolling summaries and facts belong to the
application's `ConversationStore`. LangGraph checkpointers hold only resumable
in-flight graph state.

Why

Conversation is portable product data; checkpoint schemas are framework-owned
serialized execution state.

Consequences

History is never reconstructed from checkpoints. Deleting disposable
checkpoint state must not delete canonical conversation data.

Supersedes / Superseded by

Storage implementation details were generalized by
`2026-08-27 — One persistence contract, local and deployed implementations`.

## 2026-08-01 — Long-term facts require an explicit save decision

Decision

A fact enters long-term memory only through an explicit save action. Model
output is not harvested automatically as trusted memory.

Why

Generated claims can be wrong, and persistence makes them influence unrelated
future turns.

Consequences

Saved facts carry provenance and are scoped by user. Candidate-memory review or
narrow automatic policy would require a later approved decision.

Supersedes / Superseded by

The original single-user global retrieval scope was superseded by
`2026-08-27 — One persistence contract, local and deployed implementations`;
the explicit-save rule remains.

## 2026-08-01 — Tools declare consequence; the graph owns consent

Decision

`Tool.destructive` marks an action whose effect is not free to undo. The graph
pauses before execution and asks the user; without a resumable approval path,
the action is declined.

Why

Workspace confinement is not consent, and approval logic inside tools or UI
adapters would be inconsistent and transport-specific.

Consequences

Consequential actions cannot run before approval, and a resumed graph must not
repeat earlier tools from the same node.

Supersedes / Superseded by

The consent half is superseded by 2026-08-30, "Work inside a person's own
workspace does not ask permission". Declaring consequence in the tool and owning
the answer in the runtime still holds.

## 2026-08-01 — Token accounting comes from the model server

Decision

Actual request size comes from server usage and model limits. Any pre-request
estimate is owned by `ModelBackend`, not by a tokenizer in context code.

Why

Only the serving model can count its text and multimodal tokens correctly.

Consequences

The repository does not duplicate the serving tokenizer or treat message count
as token count. Context decisions use the configured fraction of the reported
model limit.

Supersedes / Superseded by

None.

## 2026-08-01 — Version 1 initially closed at Stage 3

Decision

Version 1 was initially limited to the Stage 3 persistent local product and the
minimum UI and workspace behavior needed to exercise it.

Why

The project needed a closeable baseline before expanding into policy and
autonomous-task work.

Consequences

This entry is historical and must not be used as current product scope.

Supersedes / Superseded by

Superseded by `2026-08-01 — Reopen Version 1 for product completion`.

## 2026-08-01 — Reopen Version 1 for product completion

Decision

Reopen Version 1 until persistent chat, bounded attachments, recoverable tool
failures, honest context overflow and a real browser/restart smoke pass as a
user experience rather than merely existing in code.

Why

Review showed that several first-pass checks proved narrower technical
properties than the closure language claimed.

Consequences

User-facing capability requires short end-to-end evidence. Historical Version
1 work is now closed; current sequencing lives only in `ROADMAP.md`.

Supersedes / Superseded by

Supersedes `2026-08-01 — Version 1 initially closed at Stage 3`.

## 2026-08-01 — No general project log

Decision

Do not maintain a `PROJECT_LOG.md`. Durable choices live here, human-readable
evidence in `reports/`, and structured task/measurement outcomes in the two
JSONL journals.

Why

Repeating one event across several logs creates agent work and conflicting
copies.

Consequences

Record each kind of information once in its owning document or journal.

Supersedes / Superseded by

None.

## 2026-08-02 — Benchmarks do not define the product agent

Decision

The production assistant is a general autonomous harness. Benchmark-specific
routes, prompts and verifiers remain evaluation artifacts and do not become
product control flow.

Why

A vertical slice can prove mechanics while still encoding one task instead of
agent autonomy.

Consequences

Users state outcomes through one ordinary interface. The agent derives the
plan, capabilities and evidence; scenario success alone is not product
acceptance.

Supersedes / Superseded by

Clarified by `2026-08-02 — One interface; the harness decides whether to act`.

## 2026-08-02 — Workspace confinement accepts absolute paths

Decision

Every path-taking tool validates against an explicit workspace root and accepts
both relative and absolute paths that resolve inside it. Ambiguous filenames
are clarified.

Why

The workspace is a permission boundary, not a demand that users translate a
known native path into an internal format.

Consequences

Absolute paths never bypass root validation, safe in-root paths are not rejected
for formatting alone, and ambiguous locations are not guessed.

Supersedes / Superseded by

None.

## 2026-08-02 — One interface; the harness decides whether to act

Decision

Every ordinary natural-language request enters one harness. The harness decides
whether to answer directly or plan, use tools, validate and repair.

Why

Tool use is part of the agent's work, not a product mode the user should have to
select.

Consequences

There is no Conversation/Agent selector, special task route or tool button
required for autonomous behavior.

Supersedes / Superseded by

Supersedes the earlier benchmark-era wording that referred to an agent mode.

## 2026-08-27 — The product becomes a deployable personal assistant

Decision

The local agent also deploys serverless for one owner and a small number of
other users, first through Telegram. Local and deployed profiles use the same
`app/`; deployment is configuration, infrastructure and adapters.

Why

The existing application boundaries were built to support another interface
and model endpoint without forking the product.

Consequences

Capabilities must work in both profiles before they are complete. Provider and
platform code stay behind adapters, and user-owned state is scoped by user.

Supersedes / Superseded by

Supersedes the earlier policy-platform/MCP definition of Version 2 as current
scope.

## 2026-08-27 — One persistence contract, local and deployed implementations

Decision

Conversation, summaries and facts use one `ConversationStore` contract. SQLite
implements it locally; PostgreSQL implements it in deployment. Both run the
same contract tests and scope data by user.

Why

SQLite preserves a zero-setup local profile, while concurrent serverless
workers require a network database rather than SQLite on a shared volume.

Consequences

Application code cannot depend on a concrete store. Cross-user data access and
SQLite as a deployed multi-writer database are excluded.

Supersedes / Superseded by

Generalizes the implementation-specific parts of
`2026-08-01 — Conversation data is separate from checkpoints` and supersedes
the original global fact scope.

## 2026-08-27 — An HTTP layer requires a separately hosted caller

Decision

Telegram and Chainlit remain in-process adapters. `app/api/` stays deferred
until a UI or consumer is hosted separately from the application.

Why

Two in-process adapters still do not create a useful network boundary.

Consequences

No application HTTP surface is built for its own sake, while application code
remains independent of interface request/session objects.

Supersedes / Superseded by

Amends `2026-08-01 — Defer an application HTTP layer` by making the trigger a
separately hosted caller rather than merely a second consumer.

## 2026-08-28 — Optimize a replacement model deployment, not the baseline

Decision

Validate model-serving optimizations under a separately named deployment. Move
the application endpoint only after backend, multimodal and interface
acceptance; retain the measured baseline as rollback until separately removed.

Why

A new identity preserves honest comparison and rollback and prevents a measured
configuration from being silently redefined.

Consequences

Experimental snapshot or startup changes do not overwrite the baseline.
Deleting the rollback deployment is a separate destructive human gate.

Supersedes / Superseded by

None.

## 2026-08-28 — Database latency is not a gate and control placement stays unpinned

Decision

Withdraw the invented 100 ms warm and 500 ms cold database closing limits.
Keep Neon in `us-east-2`, leave Modal control functions unpinned, and retain the
latency probe as an instrument rather than acceptance.

Why

After application reads were collapsed to one round trip, measurements showed
database execution was negligible and remaining delay was placement. Pinning
the whole worker cost more than the brief GPU wait it removed, while current
product delay was acceptable.

Consequences

Do not pin control or GPU functions or migrate the populated database solely to
reduce this round trip. Reconsider only with new product evidence and measured
economics.

Supersedes / Superseded by

Supersedes the provisional database-latency closing gate.

## 2026-08-29 — Observation and presentation are separate agent actions

Decision

Tools that read, render or inspect return evidence to the agent. Only an
explicit presentation action such as `send_file` makes selected content
outbound to the user.

Why

Automatically forwarding tool media makes an adapter or hidden workflow decide
what the user sees instead of the agent.

Consequences

Adapters transport explicit outbound content but do not turn arbitrary tool
results into chat messages. The agent may inspect several artifacts and choose
what, if anything, to send.

Supersedes / Superseded by

Supersedes automatic delivery of media returned by observation tools.

## 2026-08-29 — Web search, fetch and visual rendering are separate capabilities

Decision

Use Firecrawl for search leads, bounded direct HTTP for normal text fetch, and
an isolated secretless Chromium function for rendered page inspection.
Firecrawl scraping is an explicit fallback, not the normal fetch path.

Why

Search, byte fetching and JavaScript rendering have different costs, evidence
and trust boundaries. A universal sandbox is larger than the present need.

Consequences

Public page JavaScript does not run beside control-plane secrets or user
workspaces. Search results are not automatically fetched, screenshots are not
automatically sent, and direct fetch does not spend provider credit.

Supersedes / Superseded by

None.

## 2026-08-29 — Project configuration is the source; platforms receive a copy

Decision

Runtime values originate in project-owned local configuration. The deployment
receives an allow-listed copy through `tools/sync_control_secret.py`; provider
dashboards are not an authoring source.

Why

Provider-only values are hard to review, reproduce and move. An explicit allow
list also exposes exactly what leaves the machine.

Consequences

Do not copy the whole `.env` or manually create a second source of truth in a
provider console. Deployment-only values may be renamed during publication so
they cannot accidentally configure the local profile.

Supersedes / Superseded by

None.

## 2026-08-30 — Work inside a person's own workspace does not ask permission

Decision

Routine mutation inside the granted workspace root — creating, writing,
editing, replacing, removing files and directories — runs without asking. The
boundary, not the individual call, is what is authorized. Approval remains
required for actions whose effect leaves that boundary: sending or publishing
something, spending money, changing infrastructure, or touching data the person
did not put inside the workspace.

Why

The workspace is already confined per user and is the person's own directory;
asking before each write buys no safety the confinement does not already give,
and it turns autonomous work into a sequence of prompts. The desired experience
is an agent working inside an assigned directory, not one asking to save a file
it was told to write.

Consequences

`Tool.destructive` stops gating workspace tools and keeps its meaning for
boundary-crossing ones; consent policy belongs to the tool execution seam rather
than to the loop. `docs/PRODUCT.md` states the boundary rule instead of the
per-call one. The current baseline is unaffected until the runtime implements
it: today exactly two tools are marked destructive, `write_file` and
`edit_file`, and no tool deletes anything. Preparation and per-sub-step
acceptance are in `reports/2026-08-30_v2_step4_harness_preparation.md`.

Supersedes / Superseded by

Supersedes the consent half of 2026-08-01, "Tools declare consequence; the graph
owns consent". The other half stands: a tool declares consequence, and the
runtime — not the tool and not a UI adapter — owns what to do about it.

## 2026-08-30 — Same-user presentation and sandboxed work stay autonomous

Decision

Explicitly presenting a workspace file back to the same person through the
current conversation is part of fulfilling the request and does not ask for a
second approval. Effects beyond that relationship — sending to another person or
system, publishing, spending money or changing infrastructure — still require
approval.

Once a sandbox run itself has been separately authorized, shell, Python, package
installation and workspace mutation inside that restricted sandbox do not ask
for permission command by command. Starting each product-runtime sandbox worker
remains its own human gate under the current execution rules.

Why

`send_file` is already an explicit agent decision and an accepted part of the
conversation, not an accidental leak of an observation. Asking again would add
friction without changing the recipient. For generated code, isolation from
secrets and infrastructure is the useful boundary; confirming every command
inside that boundary would remove the autonomy the sandbox exists to enable.

Consequences

The 4.2 execution seam owns one policy across execution backends. `send_file`
remains non-destructive, while third-party and externally consequential tools
declare the need for approval. A later sandbox plugs into `execute`; it does not
change the loop or consent semantics. It receives a restricted workspace and no
control-plane secrets, and its worker-start gate is not implied by approval of a
surrounding roadmap step.

Supersedes / Superseded by

Clarifies the 2026-08-30 decision "Work inside a person's own workspace does not
ask permission"; it does not supersede it.

## 2026-08-30 — A control signal never travels in the conversation queue

Decision

An update whose purpose is to act on what is already running, or to be answered
instantly from storage, is delivered out of band: it skips the lease that
serializes a conversation, and skips the local profile's per-chat lock. `/stop`
is the case that matters — the rest of the model-free commands travel the same
way because they are the same kind of thing. Delivery alone is not enough: the
running turn has to look for the signal, so the loop checks at each step
boundary and the two halves ship together.

Why

The human's instruction on 2026-08-30, after sub-step 4.0 was accepted: a
cancellation or control signal must pass out of band relative to the ordinary
turn queue. Serializing a conversation is what makes two messages arrive in
order, and it is exactly wrong for a message about the conversation: `/stop`
queued behind the turn it exists to stop reaches the worker after that turn has
ended, finds nothing running, and says so. That was true of the local profile
from the day the per-chat lock existed, and 4.0 gave the deployed profile the
same flaw.

Consequences

`telegram_updates` gains a `control` column, and the claim never takes a control
row for a conversation. `turn_stops` records the sequence a stop arrived with,
because deployed the stop is answered in one container and the turn runs in
another. A stop applies to every turn that began before it and to no turn that
began after, so an unconsumed stop cannot cancel the next message. Evidence:
`reports/2026-08-30_v2_one_loop.md`.

Supersedes / Superseded by

Narrows the guarantee recorded on 2026-08-30 in
`reports/2026-08-30_v2_conversation_serialization.md`: a conversation's
*messages* are serialized, not everything a person sends it.

## 2026-08-30 — Stored history is canonical; what the model sees is a projection

Decision

The conversation as stored is lossless and is never rewritten or deleted by
anything that makes a request smaller. Summarizing, shortening a tool result or
dropping media produces a *model-visible surface* derived from that history, and
a derived surface may be rebuilt, rebuilt differently, or rebuilt by a different
model without the conversation changing. Compaction is always in place: a
`thread_id` before it is the same `thread_id` after it. Anything the product
depends on — the current goal, a pending decision, what has already been done —
lives in structured state, not only in the text of the surface, so no summarizer
has to guess it back.

Why

The one loop from 4.1 can spend many steps inside a single turn, so a turn can
now outgrow the request it is being assembled into before it ends. Every way of
making a request smaller is lossy, and the moment a lossy step is allowed to
write back to the store, the loss is permanent and the assistant's memory
becomes an artefact of whichever summarizer ran that day. Keeping the two apart
is what makes a summary safe to be wrong: it can be regenerated, and the exact
wording, error or filename is still recoverable from what was actually said.

Consequences

Folding writes a summary and the position it covers, never a deletion — which is
what `app/context/summary.py` already does, and is now a rule rather than an
implementation detail. Shortening a tool result on the surface leaves the full
result in history. Compaction gets a durable record of its own, in the memory
schema rather than in telemetry, because it is a source of what the model was
shown and not a measurement of a turn. `todo` and a pending `ask_user` are
structured state for this reason, which binds sub-steps 4.4 and 4.5.

Supersedes / Superseded by

None.

## 2026-08-30 — The engine's context ceiling is set once; context is spent by the application

Decision

`MAX_MODEL_LEN` is chosen once, as high as the measured KV pool validates, and
is not a tuning dial. How much context a turn actually uses is decided in the
application: it reads the ceiling from `/v1/models` and spends a fraction of it,
and later the context engine decides what fills that room. Changing how much
context the assistant uses must never require a deploy.

Why

The repository documented this backwards — that the ceiling "reserves KV cache
at start-up" — and the plan was nearly built on it. `GPU_MEMORY_UTILIZATION`
sizes the pool; the ceiling is only validated against it, so raising it costs no
VRAM at all. It costs concurrency, which for a handful of people is not a
constraint. What it does cost is one uncached boot, because vLLM builds the
engine with it and the GPU snapshot captures that engine — which is an argument
for setting it high once, not for leaving it low.

Consequences

The 16,384 ceiling stops being an architectural limit and becomes a value to
raise on the next `assistant-llm-v2` boot, which is already owed the NCCL fix.
The number comes from `Available KV cache memory` in a boot log, because a
ceiling the pool cannot hold is a refused boot. `AGENT_CONTEXT_FRACTION` remains
the only everyday control and stays a single threshold — a second fraction on
top of it would silently multiply. Raising the ceiling does not reduce the need
for compaction: prefill is measured dominant and superlinear, so a long context
is paid for in seconds per turn even when it fits.

Supersedes / Superseded by

None.

## 2026-08-30 — Turn stopping is a minimal steering seam

Decision

Turn stopping runs only when a model result would otherwise end the turn. Its
default is to stop. It continues the same loop only when an extension supplies
explicit structured steering. Sub-step 4.3 adds no validator model, finish tool,
text heuristic or new obligation state. The model decides whether the requested
outcome needs validation and which available observation capability to use.

HTML is only an acceptance scenario: the model may choose `inspect_page` when
visual evidence is material. PDF creation is removed from 4.3 acceptance until
the sandbox provides generic execution capable of creating it; no PDF-specific
workflow or tool is added to satisfy the test.

Why

A mandatory validator would recreate the fixed repair lifecycle and its cost,
while heuristics would move a semantic product decision out of the agent. A
small steering seam preserves one loop and leaves later structured state, such
as `todo`, a place to object to stopping without requiring that state now. The
current tools can read, render and deliver a PDF but cannot create its binary
contents, so retaining that acceptance before generic execution exists would
either be impossible or reward a benchmark-specific workaround.

Consequences

A normal final candidate settles immediately. Structured steering causes
another step in the same turn and must not become a second final answer in the
interface. A simple successful text write does not gain a validation pass.
Artifact validation is demonstrated through the model's trajectory and real
evidence, not through a universal validator. The natural-request PDF scenario
returns as sandbox acceptance and remains a generic harness test.

Supersedes / Superseded by

Refines the 4.3 acceptance proposed in
`reports/2026-08-30_v2_step4_harness_preparation.md`; no earlier durable
decision is superseded.

## 2026-08-30 — The prompt is assembled, and a person's instructions are an overlay

Decision

The system layer is assembled from parts rather than written as one paragraph.
A small stable core names no tool, no file format and no workflow. Everything
true only because a capability is wired up is generated from that wiring in
`app/capabilities.py`, and what each call does is owned by its tool schema.
The layers are ordered by how rarely each changes: core, capability guidance,
tool schemas, the person's standing instructions, the rolling summary, the
retrieved facts, the conversation.

A person has exactly one instruction file, `AGENTS.md`, at the root of their own
workspace. It is read on every turn and travels as its own message naming its
source, not concatenated into the system string. Its authority is below product
and capability policy: it shapes how work is done and can never widen what may
be done. It is not memory — nothing extracts it from conversation, there is no
database copy, and `remember_fact` never writes to it. `/agents` is a thin UI
over that same file.

Why

4.3 shipped one production lever, the text of a prompt, and hand-correcting
that text neither produced the behaviour it aimed at nor turned out to be what
had changed it. A measured comparison then found the real cause in what the
prompt could not say: the model was never told where its workspace was, and an
old instruction not to invent a location for a named file had generalised into
writing no file at all. Both are facts about the wiring, and a hand-written
paragraph cannot state them without going stale the moment a grant changes.

Ordering by stability is what a served prefix cache needs; today's per-turn
retrieved facts sit in front of the conversation and invalidate it from there
down. Fixing the order now costs nothing and means 4.6a measures a cache
rather than rebuilding this layer.

The instructions are a file in the workspace because the person already has an
assistant that can read and edit files there, and a command with its own store
would be a second set of instructions free to disagree with the first. They are
a separate message because the graph is compiled once per thread: an overlay
inside that compiled prompt would wait for a restart, which in the deployed
profile is invisible and on a personal machine simply does not work.

Consequences

A grant that withholds a tool also withholds the sentence about it, so guidance
cannot advertise what is not there. The core prompt may not name a tool, and a
test enforces it. Standing instructions cost tokens on every request and are
bounded at 8,000 bytes. `/agents` never reaches the model and is declared
model-free at the front door, arguments included, so writing them cannot wake a
GPU. Nothing is migrated: the file is new state in a directory that already
exists per person.

Supersedes / Superseded by

Supersedes the single hand-written `DEFAULT_SYSTEM_PROMPT` of every version up
to 2026-08-30, including the 4.3 correction to it. Does not change the 4.3
stopping seam.

## 2026-08-31 — The agent's plan is the state of one turn, and lives in that turn

Decision

`todo_write` gives the model a whole-list plan it owns, and the plan is the
state of **one unfinished turn**: it survives compaction, an interrupt, a resume
and a restarted worker, and it does not exist for the next thing the person
asks. It gets no table, no schema version and no store of its own. The list is
the arguments of the model's own last accepted call, inside the turn's messages,
which are checkpointed and are cleared by the `extend` reducer when a user
message begins a turn. Whole-list replacement is the only operation, items have
no identity, and at most one may be `in_progress`.

An unfinished plan is the first production extension in the turn-stopping seam.

*Measured 2026-09-04:* the plan's first proven benefit is that seam, not the
planning — with the list on, a request for a PDF in Russian was met; with it
off, the same model delivered English and explained why (`reports/2026-09-04_v2_isolated_execution_review.md` §14).
It refuses one ending, names the open items, and offers the alternative that
costs nothing: update the list to say what actually happened.

Why

The lifetime asked for is exactly the lifetime the loop already gives its own
messages, so a second copy in a database would be a second thing to keep true
and a populated-database migration bought with nothing. Carrying a plan between
finished turns is a different product and was explicitly not wanted.

Planning is state the model decides to use, never a mode the harness switches
into: nothing classifies a request as complex, and an agent that wrote no plan
is never interrupted, so the ordinary answer still costs one model call. The
objection is capped at one per turn because a stale list must not become an
unbounded bill, and being made to be honest about the plan is an acceptable
outcome alongside being made to finish it.

Consequences

`Candidate` gains `steerings`, because a steered draft is deliberately never
appended to the turn's messages and an extension therefore cannot count its own
objections; any capped extension needs that number. `create_agent` wires the
extension while `Agent` does not, so the product finishes what it planned and
the bare mechanism still stops when the model stops. The capability brief states
what reads the list, since a tool schema cannot. `todo_write` is declared
model-free of any workspace root: it reaches nothing and is granted to every
agent, like memory.

Supersedes / Superseded by

Fills the seam left deliberately empty by 2026-08-30, "Whether a turn may end is
a seam, not a policy". Does not change what that seam does or its default.

## 2026-09-03 — The model-visible surface is shortened by age, and the volatile layer goes last

Decision

Before every model step the request is assembled from canonical history as a
projection with three rules, and only these. The facts retrieved for the turn
are sent after history, immediately before the turn, so everything ahead of
them is stable between turns and a served prefix cache survives it. A tool
result older than the newest `keep_results` (two) is shown as a stub naming
the tool, its subject, the size and the way back; failures and short
results stay whole, and the model's own text and call arguments are never
shortened (the first deployed version shortened long call arguments too, and
the first live turn rewrote every file — ISS-0022; withdrawn the same day).
**Amended 2026-09-04, on the human's word:** the turn in progress is never
shortened, only stored history is. The rule's reason — the model has already
said what it made of a result — holds for a previous turn and not for the
one being worked on; with a command's tracebacks stubbed mid-turn the model
repeated its first error at the fourth attempt (ISS-0041), the mirror of
ISS-0022. A long turn is bounded by the size fold, not by the stub count. Pictures share one prompt's media budget whichever turn
they arrived in, newest kept. History is never rewritten; the summary is the
only step that spends a model call, and only when the shortened surface is
still above budget. Every step records what the surface was made of
(`context_prepared`) and what the server served from its cache
(`cached_tokens`).

A person chooses the size of their own context — `small`, `normal`, `large`
as shares of the model's real ceiling — and may fold now with `/compact`.
Both are per person, kept in their workspace, and neither needs a deploy.

Why

Prefill is dominant and superlinear, and the prefix cache is real: 98% reuse,
prefill 1,370 ms → 82 ms on a repeated prefix
(`reports/2026-08-29_v2_gpu_baseline_measured.md`). Within a turn the prefix
was already stable; between turns the facts changed first and everything
after them was re-prefilled. A twelve-step turn on 2026-09-03 carried every
earlier file argument and both screenshots on every step, 3k → 9.4k tokens,
for results the model had already read and described. The reference
harnesses all clear old tool results before they summarize and keep the
full text retrievable; here the full text is history itself, which is why
there is no spill store. The rule is age and size only, so it can be stated
in one sentence and measured in one number.

Consequences

`app/context/window.py` owns `Context.surface`, `shortened`, `facts_layer`;
`app/context/choice.py` the size; `Agent.context_report` and `Agent.compact`
the commands. The schema-3 migration (`messages.failure`, `compactions`) is
the one gate of 4.6a and lands after these, so nothing above needs it. 4.6b
reads the compaction record this leaves. `ROADMAP.md` 4.6a,
`reports/2026-09-03_v2_context_engine_review.md`.

Supersedes / Superseded by

Builds on 2026-08-30 "Stored history is canonical; what the model sees is a
projection" and "The engine's context ceiling is set once". None superseded.

## 2026-09-03 — The plan is off unless the person turns it on

Decision

The `todo_write` tool and the planning guidance are offered only when the
person has switched planning on (`/plan on` in Telegram, the marker
`.agent/plan.on` in their workspace). The default is off. The switch is per
person, across every conversation and interface. `send_file` takes several
paths in one call, and the Telegram adapter delivers several outbound items
of one kind as one album.

Why

The same request, "Task Board", with the plan on and off on 2026-09-03: 12
model calls, 11 tool calls and 90 s against 5, 4 and 62 s, the same files
delivered either way; with the plan on the page was written twice and the
answer twice (ISS-0016, ISS-0019). The plan's own defects go to 4.7; until it
earns its cost, it is not part of what the agent is by default. The switch
exists so the plan's defects can be measured apart from everything else's.
Files went one per model call, about 1.2 s and 6.6 k input tokens each; one
call carrying them all is the shape a person asks for.

Consequences

Amends 2026-08-31 "The agent's plan is the state of one turn": the lifetime,
storage and display of the plan are unchanged; its presence is opt-in.
`Agent.rewire` rebuilds the toolbox when the switch flips. `scripts/loop_live.py`
G is the plan-off shape of the person's request.

## 2026-09-03 — A tool result names the action its output enables

Decision

A tool whose result is a workspace item the person might want says, in the
result, how that item reaches them, in the shape of the call:
`to hand it to the person: send_file(path="…"); nothing is sent otherwise`.
One phrase, `app/tools/base.py` `handover`, used by every such tool —
`write_file`, `inspect_page`, `view_pages`, `view_web_page` — and by any
tool added later that leaves something in the workspace. The decision to
send stays the model's; the result only names the option where the model
reads it.

Why

Four live turns on 2026-09-03 ended with `![Screenshot](.agent/browser/….png)`
and a list of paths, on a brief that said in words the person cannot see the
workspace. A bare path reads as something to embed; a call reads as something
to make. Two tools already said "call send_file with that path" in their own
words and one did not; the human had asked for tools that tell the agent what
to do with their output and asked why that was not canon. It is now.

Consequences

`reports/2026-09-03_v2_first_session_on_the_tool_system.md`. If the phrase does not hold live, the human has reopened the adapter
option — treating a markdown image of a workspace file in the answer as a
delivery — on one condition: one delivery path must never block another.

## 2026-09-03 — An open plan item no longer refuses the ending

Decision

The `todo` extension of the stopping seam objects to no ending by default.
The seam, the extension and its `limit` stay, so an objection can be wired
where it is worth what it costs.

Why

Four live turns on 2026-09-03 (`reports/2026-09-03_v2_first_session_on_the_tool_system.md`):
every objection produced a `todo_write` that ticked the open item and the
same answer written a second time, 117 + ~164 output tokens and 7 s, and
never more work. The plan is the state of one turn and is gone at the next
user message, so an item left open costs the person nothing they can see
beyond a status line that vanishes. The human accepted that in words.

Consequences

Supersedes the objection in 2026-08-31 "The agent's plan is the state of one
turn"; the plan's lifetime, storage and display are unchanged.

## 2026-09-03 — What the model says beside a tool call is said once, and a local page may load its CDN

Decision

Text the model writes in the same completion as a tool call is delivered to
the person as it is written and stays delivered; an interface does not
withdraw it when the call follows. A later assistant message in the same turn
that repeats delivered text verbatim is not sent again. The core prompt tells
the model that such text reaches the person at once and that after the tool's
result it adds only what is new.

A local artifact inspected by the agent is served to the browser from the
workspace and may reach public addresses under the same request policy as the
public renderer; private and link-local addresses are refused, and the refusals
are reported to the model. This replaces "every network scheme blocked" in the
decision below.

Why

Measured 2026-09-03: the model wrote the whole answer with a send attached, the
adapter withdrew it, and the next model call wrote the same 409 characters
again — 134 output tokens and 3.8 s for text the person had already watched
vanish (ISS-0009). The 2026-08-30 correction was aimed at a narrated tool call
becoming a first answer; delivering the text and refusing the repeat keeps
that outcome without the second generation. Chainlit already delivered it.

On the network: the screenshot the person received was of a page without its
Tailwind stylesheet, refused by a boundary the person's own browser does not
have (ISS-0017). The public policy exists for exactly this: what a page may
reach when its scripts run. The human allowed it in words on 2026-09-03 and
rejected a mechanical delivery backstop in the adapter the same day.

Consequences

`Delivery.place` and the delivery sentence say where the person is. The
Telegram adapter keeps the preview as the answer when a call rides with it,
and holds a draft the stopping seam refused instead of deleting it. A draft
refused as an ending is refused as an ending only: when the model, having
done what the steering asked, answers with nothing, the draft is the answer;
the steering says so. An empty completion with nothing steered ends the turn
without a message. `open_browser` takes `serve` and `allow` together.
`tests/test_telegram_adapter.py`, `tests/test_turn_stopping.py`,
`tests/test_browser_session.py`.

## 2026-09-03 — A tool result is a typed outcome, and the runtime survives any model

Decision

A tool returns content on success and raises `ToolError` with a stable code on
failure; the executor turns every outcome, refusal and unexpected exception into
`ToolOutcome(content, failure: ToolFailure | None)`, projects it into the tool
message and carries the typed failure on that message through the checkpoint.
`failure is None` is the only definition of success. The `error:` prefix stays
as the wording the model reads and stops being a protocol anything reads back.
The executor owns normalization, bounds, sanitizing, a per-tool timeout,
telemetry with the reason, and the projection; the loop keeps deciding when a
batch runs, pauses or is halted.

The runtime, not the model server, is responsible for surviving what a model
emits. A call with unreadable arguments becomes one refused call with the
tool's signature, never a failed request; names are resolved against the
allowlist and arguments coerced to the declared schema; fragments of another
call are removed and nothing is ever invented. The corrected Gemma 4 parser in
`tools/gemma4_parser.py` stays offline and is not deployed.

One implementation per capability, parameterized by what actually differs — a
root for the filesystem — and a backend interface only at the first real second
implementation, which is the sandbox. The browser is designed as one session
with the full operation set, snapshot-with-refs first, and exposes observation
only until the roadmap says otherwise.

Why

Every live failure since 4.2 ran through the tool boundary, and the boundary was
the last Version 1 shape in the loop: a string convention four consumers parsed,
an OS error wrapped in platform wording, a corrupted call the request died on.
The context engine has to shorten tool results and cannot be built on prose it
parses. A per-model parser on the server would fix one emission of one model on
one server and would have to be redone for the next; the product runs on
whatever OpenAI-compatible endpoint is configured. DeepSeek Harness, Hermes
Agent and OpenClaw all converge on the typed union, the bounded and sanitized
error, the allowlisted repair that refuses rather than guesses, and the
snapshot-and-ref browser loop; the comparison is in
`reports/2026-09-03_v2_tool_system_references_and_queue.md`.

Consequences

`Message` gains `failure`, checkpointed now and stored when the schema-3
migration lands in 4.6a, so the tool system needs no migration of its own and
4.6a and 4.6b keep sharing one. `tool_failed` carries a code and a message
(ISS-0007). The migration order and acceptance are in
`docs/v2_tool_system.md`, which the implementation follows; the draft
`docs/v2_tool_system_design.md` is kept as its origin. The queue after 4.4 is
tools, browser, context engine, archive recovery, scenario suite, then
`ask_user` and "saying only what was observed", for the reasons in the report.

Supersedes / Superseded by

Refines "Tools declare consequence; the graph owns consent" (2026-08-01): the
declaration is renamed `requires_approval` and the consent path is unchanged.
Refines the tool execution seam of 2026-08-30: the same three stages, now owning
what flows through them. Rejects the parser-redeploy option recorded in
`reports/2026-08-31_v2_todo_live_failure.md`.

## 2026-09-03 — What a summary or a stub stands for is reachable by search and by position

Decision

The model gets back to stored history through two tools of its own.
`search_history` is full-text search over the words of stored messages —
text, failure message, and the calls the model made — within this person's
conversations and never anyone else's, the current one unless asked.
`read_history` returns messages by position as they were said, in pages. A
shortened result's stub names its stored position; the summary says the
exact words behind it are kept. Nothing found is injected into the prompt:
the model asks, and the trace shows that it did. The words live in a derived
`text` column with an index in both profiles (schema 4); ranking is match
then recency, nothing else.

The 32k per-result cap stays where it is, before the store: history is exact
up to it, and what keeps the agent's reach whole is paging, not a larger
row — `read_file`, `fetch_page` and `read_history` take an `offset`, and a
capped page ends by naming the call for the rest.

Why

The 2026-08-30 decision that history is canonical was justified by
recoverability, and until now recovery existed on paper: the model's only
way back to an old result was to run the tool again. The question 4.6b
answers — the exact filename, error, number — is keyword search by nature,
so BM25 over the person's own words is the right tool and vectors are not
needed (2026-08-01 stands). A spill store would be a second source of truth
for text the store already holds. A condensing model over the hits would be
another summary, which is the thing being recovered from. On the cap: no
live result to date has reached 32k, and the actual limit on reach was that
a capped reader had no way to the rest, which B would not have fixed.
`reports/2026-09-03_v2_history_recovery_review.md`.

Consequences

`app/tools/history.py` owns the two tools, `app/tools/paging.py` the page
shape, `ConversationStore.search_messages` the search, `records.message_text`
the one definition of a message's words. Schema 4 is 4.6b's one gate on the
deployed database. What stays unrecoverable is the middle of a
non-repeatable result over 32k; if a live case ever needs it, that is the
evidence to store the uncapped result.

Supersedes / Superseded by

Builds on 2026-08-30 "Stored history is canonical; what the model sees is a
projection" and 2026-09-03 "The model-visible surface is shortened by age".
None superseded.

## 2026-09-04 — A turn a worker died in is taken up, and what may run again is the tool's to say

Decision

A worker that dies mid-turn leaves the turn in the checkpoint, and the next
worker to claim that update continues it from there rather than starting it
again. The one thing a death can leave unknown is whether the tools of the
step it died in ran; each such call is answered before the graph moves on —
run again if the tool declared itself `replay_safe` (reading), otherwise
answered `interrupted`, "whether it ran is unknown", for the model to check.
The harness never repeats a side effect on its own and never drops the work
that was done. The inbox lease is shorter than the worker container's life,
the same update is re-invoked once after a kill, and an update claimed three
times without finishing is given up on and said so.

Why

The 2026-09-04 review found that a killed turn was silently lost until the
person's next message and then replayed from the start with every tool run
twice, files sent twice and facts saved twice. Every reference that survives
a restart does the same two things: make "a turn is running" durable with the
message, and on recovery tell the model per call what is known (OpenClaw's
synthetic interruption message and restart-safe tools, DeepSeek's
`TOOL_OUTCOME_UNKNOWN` with "retry only if read-only or idempotent"). The
replay decision is a property of the tool, like `requires_approval`, because
nobody can judge it at recovery time. Three attempts is OpenClaw's budget and
matches `MAX_ATTEMPTS`. `reports/2026-09-04_v2_restart_resume_review.md`.

Consequences

`Agent.unfinished` and `Agent.resume_interrupted_events` in
`app/agent/runtime.py`; `Tool.replay_safe`; the `interrupted` failure code;
`persist` idempotent; `LEASE_SECONDS` in `ui/telegram/webhook.py` derived from
the Modal timeout and kept below it; `retries=1` on the worker function.
Not decided here: clearing the dead attempt's status and preview messages in
the chat (their ids died with the process), and tool deadlines (ISS-0033).

## 2026-09-04 — What stays verbatim is the last two exchanges, and a fold takes only what has to go

Decision

The part of a conversation that always stays verbatim is the last two
exchanges — a person's message and everything the assistant did up to the
next one — not the newest eight messages. Inside one long tool-using turn,
with no earlier exchange to keep, it is the newest two assistant steps. A
fold by size folds the oldest exchanges one at a time until the overshoot
plus room for the summary is freed, and no more; a fold by count or by
`/compact` folds everything older than the floor. The floor is a
`ContextPolicy` field, `keep_turns`, with `AGENT_KEEP_TURNS`.

Why

Eight messages was two short sentences in one conversation and half a
window of tool results in another, and it was also the only measure of how
much a size-triggered fold took: all or nothing. The human called it a
crutch on 2026-09-04 and it was one: the principle — the model needs the
exchange it is answering and the one before it verbatim, the references all
keep a tail of recent turns — was carried by a number in the wrong unit.
Folding one exchange at a time keeps more exact wording and asks the
summarizer for less at a time; it costs one fold more when the estimate
falls short, which `fitted` bounds at three.

Consequences

`verbatim_floor`, `cut_for` and `SUMMARY_ALLOWANCE` in
`app/context/summary.py`; `fitted` in `app/agent/graph.py` loops;
`keep_recent` is gone from policy, settings and the Telegram wording.

## 2026-09-05 — The goal is the request's parts, written down once by the model; the plan stays a mode of its own

Decision

The model has a `set_goal` tool, offered always: when a request asks for
more than one thing, it writes the things down once before it starts, one
short line each in the person's words including how they want it, and
never updates or marks them. The goal lives where the plan lives — in the
arguments of the call, inside the turn's messages, checkpointed with the
turn and cleared by the next user message. Nothing in the loop reads it
back: the turn ends when the model stops, as it always has, and the
harness makes no second model call about the request. `/plan` and
`todo_write` stay exactly what they are, a separate mode with its own
bookkeeping and its own switch. `tools/prompt_scenarios.py --goal off`
measures the loop without the tool.

Why

Measured on 2026-09-04: with the plan on, requests with several parts were
finished — a PDF in Russian, screenshot and files — and without it the
model stopped when it had something. The benefit was not the plan's
bookkeeping: its ending objection was off, and six of the twelve calls of
a planned turn were updates that changed nothing. What worked was the list
of the request's parts in the model's context at every step. So the goal
is that list with everything else removed, at the price of one short call
in a turn that asks for several things and nothing in a turn that asks for
one; which requests have several parts stays the model's reading.

Built first the other way the same day and measured out: a check on the
stopping seam that asked the same model, in the same turn, whether its
work met the request. Two deployed samples answered `done` to half a
handover, one was skipped at the step ceiling by the seam's own rule, and
Hermes and DeepSeek both put such a judge outside the turn in a fresh
context (report §14, §15). The human rejected that direction as doubling
the cost of a turn with no controllable threshold and no measured benefit,
and chose this one: the plan, cut down to what helped.

Consequences

`app/tools/goal.py` (`set_goal`, `goal_tools`), offered in
`Agent.toolbox` beside memory and history; one brief line in
`app/capabilities.py` saying why. The stopping seam is back to one
extension, the todo list's. Measured next on G and P deployed, goal on and
off; if the parts written down do not change what is handed over, the tool
comes out the way the check did.

## 2026-09-06 — A hosted model is a set of lines, and the assistant keeps three: a default, a stronger one and a cheaper one

Decision

A model the assistant can talk to is a named set in configuration
(`MODEL=<name>` reads `MODEL_<NAME>_*` and `AGENT_<NAME>_CONTEXT_TOKENS`;
the plain `MODEL_*` lines are the unnamed set), every set is published
with the control secret, and switching is the `MODEL` line and a
control-plane redeploy. Among hosted models the human chose, 2026-09-06:
**Gemini 3.1 Flash-Lite as the default** (to be compared with and without
thinking through CometAPI's `-thinking` id), **Gemini 3.5 Flash-Lite as
the stronger tier**, **GLM 5.3 Flash as the low-price tier**. A thin
adapter for Google's native request format is to be built so that
Gemini's cache can be made to land; GLM is to be served by a provider
other than CometAPI, because through CometAPI it is delivered whole after
13–100 s.

**Amended the same evening, the human's words.** The hosted models are
reached through OpenRouter, not CometAPI (report §11–§12: Gemini 2–3x
faster there and half the price on the Flex rate; GLM 3–8x faster). The
**default is GLM 5.3 Flash at Novita, Z.ai as the fallback** (Z.ai's first B call stalled 19 s; Novita's timings were the steadier)
(`provider.order`, `allow_fallbacks: false`), with `thinking: disabled`
and `reasoning_effort: low`, which together leave no reasoning tokens
with tools: ~5 s per call, the cache on every repeat, $0.00007 a call.
DeepInfra was declined although four times faster: fp4, and its cache
never landed, so five times the price per call. **Gemini is paused**
until its cache can be made to land; the native adapter gives way to
OpenRouter's `cache_control` breakpoints for Gemini, a small change in
the existing client rather than a second backend.

Why

The suite of 2026-09-06 on three hosted models through one OpenAI-
compatible client: Gemini 3.1 Flash-Lite passed 14 of 16 with 1.5–3 s per
streamed call and the first G that ever passed all its checks, at $0.06
for the suite; GLM 5.3 Flash passed 13 of 15 at $0.008 with every call
cached, but CometAPI returns it whole after a 13–100 s wait; INT4 on the
A100 is faster per call when warm and pays 20–45 s restores and sleeps
mid-turn. Price, quality and speed together put Gemini first, speed above
all (the human). Gemini's implicit cache landed on 2 of 60 calls through
CometAPI although the prefix is byte-identical (the dumps), which is the
provider's routing; explicit caching exists only in the native API.
`reports/2026-09-06_hosted_model_cometapi.md`.

Consequences

`ModelSettings` and `AgentSettings` read the chosen set; `MODEL_EXTRA_BODY`
carries what a service wants and the OpenAI shape has no word for;
`MODEL_DUMP_DIR` keeps a call's raw stream. The GPU Apps remain sets of
their own (`MODEL_INT4_*` to be written before switching back). Which set
the assistant uses from Telegram is roadmap item 13; the adapter and the
GLM provider are its next work, each begun on the human's word.

## 2026-09-05 — A second model is a second App, and the assistant is pointed at one by configuration

Decision

A model the project wants to try or keep beside the current one gets its own
Modal App with its own identity, image layer, snapshot and scale-to-zero,
sharing the first App's machinery by import and its Volumes by name. The
assistant is pointed at a model by `MODEL_ENDPOINT` and `MODEL_NAME` in the
control secret and nothing else; switching is those two keys and fresh
containers, and the rollback is the same two keys back. The first of these
is `assistant-llm-qwen`: Qwen3.8-27B in Qwen's own FP8 on an L40S, a 128k
ceiling with the KV cache unquantized, utilization 0.86, thinking at `low`.

Why

The human asked for Qwen3.8-27B on 2026-09-05 and, when the card arithmetic
was shown, chose the official FP8 on an L40S over community int4 builds on
an A10 or an A100: the publisher's quantization, FP8 as a hardware path on
Ada, and 128k in bf16 with room, against the A10 where the same model needs
int4 and a quantized cache and 0.90 at once. A second App rather than a
change to the first because `assistant-llm-v2` is the configuration behind
every recorded measurement, and a redeploy over it would overwrite the
comparison and the rollback; both Apps sleep for free. The harness already
binds to nothing model-specific — the ceiling is read from the server, the
parsers are the server's — so the switch is configuration, which was the
point of `ModelBackend` from the start (2026-08-01).

Consequences

`deploy/modal/model_app_qwen.py`, importing `model_app`; `_warmup` takes
the served name. 0.86 is the human's number, and 262k was declined for now:
it needs the cache in fp8 with no scales in the checkpoint, and its prefill
is minutes. `reasoning_effort: low` is the first setting to measure against,
not a finding. Which model the assistant uses is decided by the live
scenarios run on both, then the human. `reports/2026-09-05_qwen38_second_model.md`.

**Amended the same day, the human's words.** The FP8 App's restore
measured 19–86 s on a 28.5 GiB snapshot, and "such cold starts do not
suit a scale-to-zero architecture"; the human chose a third App with the
same model in INT4 (`RedHatAI/Qwen3.8-27B-INT4`) on an A100-40GB, for a
snapshot half the size, and asked that it be deployed without the refused
boots the FP8 App went through. So every Qwen App shares one spec and one
CPU `preflight` that applies the pool arithmetic of those boots before a
GPU is paid for (`model_app_qwen.fits`). The FP8 App stays deployed as
the comparison.

**Amended again the same day, the human's words.** The Qwen Apps run
their own vLLM pair, 0.28.0 with transformers 5.15.0, apart from the
Gemma App's validated 0.26.0: 0.28.0 makes prefix caching the default for
hybrid models (it was opt-in and off through every Qwen boot of the day,
so every call prefilled the whole prompt), fixes `/wake_up` on hybrids
and the prefix-cache poisoning under MTP. Prefix caching is asked for
explicitly; thinking is off by default on the server and turned on by
`MODEL_CHAT_TEMPLATE_KWARGS`, a setting, so no boot of a model App is
needed to move it. `dry_run` stays available but is not a required step:
the human's point that it costs the same as the snapshot boot it
precedes, and the watcher that stops a failing container covers
ISS-0049. Report §11.

Supersedes: the 2026-08-30 note that 128k belongs to "L40S with Qwen3-8B
and quantized KV" — the model and the quantization changed; the card and
the separate identity did not.

## 2026-09-04 — Generated code runs where no secret is, and what it installs lives in the workspace

Decision

The assistant runs commands through one tool, `run_command`, a fresh shell
per command in the person's workspace. Deployed, the command runs in a
Modal Function beside the renderer: the same image plus base tools, the
workspaces Volume mounted, no control-plane secret, scaled down after 180 s
idle. Locally, it runs as a process on the person's machine in the
workspace, with the environment reduced to what a shell needs. Nothing
installed into a container is expected to survive it; what a person or the
assistant installs goes into the workspace (`HOME` there, a venv there),
where the Volume keeps it. Network is on. A conversation is in one of two
modes: `full`, the default in both profiles, where everything inside the
workspace runs without a question, and `careful`, where tools that change
the workspace ask first through the existing approval path; effects beyond
the workspace stay gated in both. A Modal Sandbox is v2, for background
processes and snapshots.

Why

What a coding agent needs is an environment that lives through a session;
the references agree, and their isolation is second to it — Claude Code on
native Windows has none. Deployed, the worker itself cannot host commands:
it scales to zero in 60 s, and any child of it can read its Telegram token
and database URL through `/proc` whatever its own environment says. A
Function without secrets, the pattern the renderer already uses, keeps the
secrets out at a third of a Sandbox's price and with no new primitive;
persistence is the same in both, because it comes from the workspace. On
the person's own machine the person is the boundary, as in Claude Code, and
the two modes are Claude Code's permission modes, general rather than
written for a case. This supersedes the 2026-08-30 wording "isolation, not
a confirmation prompt, is the boundary for arbitrary generated code" as a
universal: it holds deployed, where nobody can be asked; locally the mode
is the answer.

Consequences

`app/tools/shell.py` with a one-method `Runner` and two implementations
chosen by profile; a `run_command` Function and its image in
`deploy/modal/control_app.py`; a `mutates` flag on tools and a mode the
toolbox's `requires_approval` reads; the brief says where commands run and
that the environment between turns may be fresh. Cold start is measured
before the deployed shape is built on. **Amended the same day, on the
human's word:** locally the person is not the only boundary after all — on
Windows a command runs under a write-restricted token and can write only
inside the workspace (`app/tools/shell_windows.py`, DeepSeek Harness's
mechanism), because a rule about installers was a crutch and the
references' property is a boundary on every write.
`reports/2026-09-04_v2_isolated_execution_review.md` §10.
