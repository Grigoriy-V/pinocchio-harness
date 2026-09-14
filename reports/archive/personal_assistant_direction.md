# Personal Assistant — Direction / Handoff

## Goal

Turn the existing `local-multimodal-agent` project into a practical personal AI assistant for myself and a small number of friends.

The first useful product should feel like a normal chat assistant, not like an "agent demo" or a collection of modes and tools.

> **Status.** This document records the direction and the decisions taken
> against its open points on 2026-08-27. It is not a plan and not an
> authorization: `ROADMAP.md` remains the only plan, and `DECISIONS.md` holds
> the durable architectural rationale.

**Decision — repository.** This work continues in this repository rather than a
new one. Roughly 70% of `app/` transfers unchanged, and the Chainlit adapter
boundary was built for exactly this substitution. The GPU model server stays
outside the repository, reached over `MODEL_ENDPOINT` only, which is the
existing rule and maps onto the separate Modal GPU app without change.

## Product direction

The assistant should:

- work through Telegram as the primary interface;
- use a self-hosted LLM endpoint, initially Gemma 12B through vLLM on Modal;
- keep persistent conversations and long-term memory per user;
- be able to save useful facts and retrieve them in later conversations;
- search the web and open relevant pages when current information is needed;
- read and work with uploaded files properly, including PDF, Markdown, text, common office documents, images, and code files;
- answer questions from documents, summarize them, find specific information, and preserve document/page context instead of treating files as a single pasted text blob;
- use basic tools automatically when they are useful;
- handle multimodal input where supported by the model;
- perform bounded coding tasks on repositories in an isolated sandbox;
- return useful results, diffs, files, or explanations back into the same chat.

The existing `local-multimodal-agent` should remain the core starting point rather than replacing it with Open WebUI, LibreChat, OpenHands, or another full assistant platform.

External projects may be used as components or references when they solve a specific problem better.

**Decision — Telegram is an adapter, not the interface.** Telegram is the first
and only interface built now, but it is a thin adapter like Chainlit, with no
business logic in handlers. The canonical user and thread identity belongs to
`app/`; a Telegram chat identifier is mapped onto it, never adopted as it. This
is what keeps a separately hosted UI possible later without retrofitting.

**Decision — the local profile is kept.** The assistant must remain runnable as
a local agent on the developer's own machine. Deployment is a configuration
axis, not a fork, and both profiles run the same `app/`.

## V1 scope

V1 does **not** need full computer control, GUI automation, deep autonomous browsing, or a general-purpose "Jarvis" system.

The target is a reliable assistant with:

1. normal conversation;
2. persistent user-specific memory;
3. memory search;
4. document/file reading and retrieval, including PDFs;
5. web search / page retrieval;
6. basic tool calling;
7. repository/code work inside an isolated sandbox;
8. explicit confirmation for consequential actions.

For coding requests, the intended experience is roughly:

> "Look at this repository, make this change, run the relevant checks, and show me what changed."

The implementation may initially use the existing agent loop rather than introducing a separate coding-agent orchestrator such as OpenHands. A specialized coding agent can be added later if real usage shows that it is necessary.

**Decision — item 8 is already built and is the right shape for Telegram.**
Consequential actions stop the graph with a LangGraph `interrupt()` whose
question lives in a checkpoint. A serverless webhook cannot hold a process open
waiting for an answer, so a durable question resumed by a later webhook is not
a workaround here — it is the mechanism.

**Decision — the current browser capability does not transfer.** `inspect_page`
drives a locally installed Chromium or Edge over CDP and has no browser in a
deployed container. Deep browsing is out of V1 scope anyway; web search and page
retrieval (item 5) are a separate capability, not yet designed.

## Files and documents

File handling is a first-class V1 capability, not just an attachment passthrough.

The assistant should be able to ingest uploaded documents, extract their usable structure, retrieve relevant sections later in the conversation, and answer from the source rather than from an approximate summary.

Initial target formats:

- PDF;
- Markdown / TXT;
- DOCX and similar office documents where practical;
- images/screenshots;
- source code and repository files.

For PDFs and long documents, preserve page/section boundaries where possible so the assistant can point back to the relevant part of the source. Prefer native text extraction and structured parsing; OCR should be a fallback for scanned documents, not the default path.

The file subsystem should be reusable by chat, memory/retrieval, and coding workflows rather than implemented separately for each tool.

**Decision — scheduled, but last of the four steps.** Attachments today accept
images and audio only; PDF, DOCX, Markdown and text are refused before a model
request. This is real new work rather than a port, and it is sequenced after
persistence, Telegram and the deployed profile so that it is built once, on a
per-user store that already exists.

## Hosting direction

Modal is the current preferred infrastructure to test:

- CPU/serverless components for the Telegram-facing application and orchestration;
- vLLM + Gemma 12B on GPU;
- scale-to-zero when inactive where latency remains acceptable;
- Modal Sandbox for temporary isolated code execution;
- persistent storage/database outside the ephemeral GPU worker.

The first working deployment uses an A10 24 GB and proves that Gemma 4 12B QAT,
16k context and the required multimodal configuration fit. Keep A10 for the
snapshot replacement so the cold-start comparison changes one architectural
variable rather than GPU hardware. L4 or a larger GPU remains a later
price/performance benchmark, justified only by measured latency, context,
multimodal or concurrency needs.

**Decision — scale-to-zero applies to the control plane too.** An always-on CPU
container that owns a database file was considered and rejected: an idle
application must cost nothing. This is what rules out SQLite on a network
volume in the deployed profile and forces a networked database.

**Reference — a working Modal deployment of this shape.** `h3zero`
(`D:\ML\Ai_render\.tools\h3zero`, the human's own project) already runs a
CPU gateway and a scale-to-zero GPU worker as two independently deployed Modal
apps linked by name. Patterns worth reusing at step 3: dispatch by `spawn()`
with the call id persisted and polled non-blockingly, so no request waits on the
GPU; status polling and health that never invoke the GPU; and
`FunctionCall.cancel()` for cancellation. Its CPU-only snapshot does not load H3
onto the GPU, so it is a reference for service separation rather than the
assistant's cold-start solution. The assistant's measured Gemma checkpoint loads
in under seven seconds; its dominant repeatable work is vLLM initialization, so
the planned replacement tests a CPU+GPU snapshot around vLLM sleep/wake. Its
per-job JSON files on a Volume are not a substitute for the store contract —
they carry one writer per record and no cross-record queries — but a Volume is
the right home for task artifacts. Reference only; no work happens in that
repository.

**Decision — optimize only after measurement, then preserve the baseline.** The
first deployed profile measured a 189–201 second wake, which is too slow for the
normal assistant experience. The next deployment therefore has a new identity
and tests CPU+GPU snapshots, explicit 0→1 scaling and a short idle window while
the measured endpoint remains available for comparison and rollback. No target
number is claimed before the replacement is measured. One rejected idea is
recorded so it is not re-proposed: routing conversational turns to a second
smaller model to keep the GPU asleep. The routing decision is itself a model
call, so it needs a second model to answer as well — that is two assistant
voices and a product change, not an optimization.

## Memory direction

Memory belongs to the application, not to the LLM process.

The current explicit fact memory and conversation summaries are a valid V1 baseline. Before multi-user use, memory and conversations must be scoped by user.

Do not introduce a vector database by default. Add embeddings / semantic retrieval only if real usage shows that text/FTS retrieval is insufficient.

**Decision — one store contract, two implementations.** SQLite stays for the
local profile; a networked database serves the deployed one. The divergence is
small in practice: ordinary SQL, inserts and the position counter are portable,
and only full-text search plus the connection layer are engine-specific. One
shared contract test suite runs against both implementations — without it the
second one silently rots. Keeping SQLite locally also preserves the offline,
temporary-database test rule in `AGENTS.md`, which a database-server-only
design would break.

**Decision — user scope comes first.** Facts are deliberately global today, so
that a later conversation can find them. With a second person that is a leak
between users, not a feature. This is a correctness change and lands before the
store holds anyone else's data.

## Development principle

Prefer extending the existing working system over redesigning it.

Add capabilities only when they solve a concrete assistant use case. Keep the natural-language chat as the single entry point; the assistant should decide when tools are needed.

The next work should optimize for **a usable personal assistant**, not maximum agent autonomy.

**Decision — no HTTP layer until a caller needs one.** `app/api/` stays an
empty stub. Telegram runs in the same process, so an API would once again be a
layer with no separately hosted caller. What is preserved instead is the
property that makes adding it cheap: no code in `app/` depends on a request,
response or session object, and both adapters stay thin.
