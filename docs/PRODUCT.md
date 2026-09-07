# Product

This document is the stable product contract for Pinocchio Harness (`pinocchio-harness`).
It explains what the product is and the principles that should survive implementation changes.
It is **not** a roadmap, work log, architecture history, or evidence report.

Current work and ordering live in `ROADMAP.md`. Evidence and experiments live in `reports/`.
Agent working rules live in `AGENTS.md`.
Approved durable choices and their rationale live in `DECISIONS.md`. Read the
relevant decision when a product boundary is being reconsidered; use this file,
not decision history, for the current product contract.

## Product

Pinocchio Harness is a practical personal multimodal assistant for one owner and a small number of other users.

The primary deployed interface is Telegram. A local Chainlit interface remains available for development and local use. The application is intended to remain the same product in both profiles: local versus deployed is a configuration and infrastructure difference, not a separate codebase or assistant.

The assistant should feel like one normal conversation. A person states an outcome in natural language; the assistant decides whether it can answer directly, inspect evidence, use tools, ask for approval, or perform longer work.

The product is not a collection of user-selected tools, workflows, or `Chat` / `Agent` modes.

## Primary product principle

> **The model is the agent. Infrastructure gives it truthful capabilities, evidence, durable state, safety boundaries and ways to act; infrastructure must not silently make product decisions that belong to the agent.**

A user asks for an outcome. The agent should choose how to achieve that outcome from the capabilities actually available to it.

This principle is the reason several current boundaries exist:

- the UI does not select tools for the model;
- observation and presentation are different actions;
- a screenshot returned by an observation tool stays evidence until the agent explicitly chooses `send_file`;
- web search, text fetch and browser view are separate capabilities because they have different cost and execution semantics;
- `/can` and the model's capability brief are generated from real wiring rather than from remembered prose;
- Telegram and Chainlit are adapters around application behavior rather than places where agent policy is implemented.

## Product consequences

### One natural-language entry point

Ordinary requests enter the same assistant. The user should not need to know whether a request is "chat", "agentic", "tool use", "document work" or another internal category.

Internal routing may exist, but it is an implementation detail. The product should not require a mode selector, special workflow button, or task-specific command to obtain autonomous behavior.

### Capabilities, not hidden workflows

A capability should expose a real action or source of evidence. It should not encode a benchmark-specific or task-specific sequence that pretends to be agent autonomy.

Examples of the current shape:

- `list_files` / `read_file` expose workspace observation;
- `read_document` exposes bounded document text;
- `view_pages` exposes visual page evidence;
- `search_web`, `fetch_page`, and `view_web_page` expose distinct web actions;
- `send_file` exposes presentation to the person.

The agent combines these actions. The adapter should not automatically turn an observation into a user-visible result unless the application explicitly marked it outbound.

### Truthful capability awareness

The assistant must be told what is actually wired into the current runtime.

Tool inventory, accepted inputs, delivery support and approval requirements should be derived from the runtime where possible. A capability that is unavailable should be absent or reported unavailable rather than advertised and allowed to fail later.

The assistant should not invent tools and should not deny capabilities that the current wiring actually provides.

### Evidence before claims

If an answer depends on an external source or generated artifact, the assistant should inspect the relevant evidence rather than infer success from intention.

Current examples:

- search results are leads; a factual answer should read the selected source;
- a rendered PDF page is evidence the multimodal model can inspect;
- a claim of success should rest on evidence the agent actually observed, not on its own summary of what it intended;
- an artifact should not be described as delivered until an explicit presentation action has happened.

Failures and uncertainty should remain visible to the agent so it can recover or report them honestly.

### Observation is not presentation

Evidence used by the agent is not automatically something the user should receive.

`read_document`, `view_pages`, `use_page`, `fetch_page`, `view_web_page`, and `read_file` on an image are observation paths. The agent may use them privately while solving a request.

`send_file` is the explicit current presentation capability. A UI transports explicitly outbound content; it does not decide which internal screenshots, page renders, or intermediate files should become chat messages.

### Application state outlives model workers

Conversation history, long-term facts and resumable work are application data. They must not depend on an ephemeral model process or on one CPU/GPU container staying alive.

The current implementation therefore keeps:

- conversation, summaries and facts behind `ConversationStore`;
- which conversation a person is in, as a stored explicit choice rather than an
  inference from recency: a person can start one, list recent ones and return to
  an older one, and background activity in another conversation never moves
  them;
- in-flight LangGraph state in checkpointers;
- deployed user workspaces on persistent storage;
- the GPU endpoint as inference infrastructure rather than the owner of product state.

### Local and deployed are the same product

The application core must not fork into a local implementation and a cloud implementation.

Provider/platform choices should sit behind configuration or infrastructure boundaries. The current examples are:

- SQLite locally versus PostgreSQL in the deployed profile behind one `ConversationStore` contract;
- any OpenAI-compatible endpoint behind `ModelBackend`: a hosted service, a
  Modal vLLM App or a local server, chosen by configuration;
- local browser execution where trusted versus an isolated renderer for public web pages in deployment.

A feature that only works in one profile should be treated as an implementation asymmetry to understand, not as permission to create a second product core.

### Interfaces stay thin

Telegram, Chainlit and any future UI translate between transport concepts and the application's domain types. They may own interface presentation, transport retries and interface identity mapping, but should not own agent reasoning, memory policy, tool policy or task validation.

### Safety boundaries are product behavior

Workspace confinement, user scoping, explicit approval for consequential actions, public-web destination checks and secret isolation are not optional implementation details.

The default should be the safe answer:

- no allowed Telegram users means nobody is admitted;
- model/page/tool output is untrusted data rather than instructions;
- path-taking tools stay inside their granted root;
- work inside the granted workspace root and explicit presentation back to the
  same person in the current conversation are autonomous; sending to another
  person or system, publishing, spending money or changing infrastructure
  requires approval;
- public web tools must not become access to internal infrastructure;
- page JavaScript should not run next to control-plane secrets in the deployed profile.

Multimodal input preserves the supplied order of text, image and audio parts.
Unsupported or oversized input is refused before a model request rather than
silently dropped, reordered or truncated.

### Simplicity serves the outcome

Prefer the smallest implementation that preserves the product outcome, agent freedom, correctness and safety.

Do not reduce engineering complexity by moving an agent decision into a hidden hard-coded workflow. Do not add abstraction for hypothetical future use when an existing boundary already supports the real requirement.

## Current product baseline

The current accepted deployed baseline includes:

- Telegram conversation;
- persistent per-user conversations and long-term facts;
- per-user persistent workspace;
- text, image and audio input where supported;
- document upload, structured reading and PDF page viewing in the Telegram path;
- autonomous filesystem read/write/edit inside scoped per-user workspace paths;
- commands run in the person's workspace, in a container that holds no secret
  when deployed;
- a real browser page the agent opens and drives (open, click, type, press, evaluate, screenshot);
- public web search, bounded text fetch and isolated visual browser view;
- explicit agent-controlled file/media delivery;
- resumable approvals, and one turn bounded by its own budget that a person can stop while it runs;
- a person's own standing instructions, plan switch, work mode and context size;
- an OpenAI-compatible multimodal model chosen by configuration: a hosted
  service by default, with scale-to-zero GPU Apps kept as alternatives.

`ROADMAP.md` is authoritative for what is accepted next and for known product gaps.

## Non-goals of the product contract

This file does not choose future harness architecture, subagent design, vector databases, sandbox implementation, speculative decoding, or a future UI. Those choices should be driven by product evidence and current roadmap work rather than frozen here prematurely.

It also does not preserve historical architectural debate. Historical evidence belongs in `reports/`; current ownership belongs in `PROJECT_MAP.md`, `CODEMAP.md`, and `OPERATIONS_MAP.md`.

## Repository knowledge model

Use the repository documents for distinct questions:

| Question | Source |
|---|---|
| What product are we building and what must remain true? | `docs/PRODUCT.md` |
| What does the current system consist of? | `docs/PROJECT_MAP.md` |
| Where is the code that owns a behavior? | `docs/CODEMAP.md` |
| How is the current system configured, deployed and operated? | `docs/OPERATIONS_MAP.md` |
| What work is current / next / accepted? | `ROADMAP.md` |
| What rules must a coding agent follow? | `AGENTS.md` |
| Why was a durable architectural or scope choice made? | `DECISIONS.md` |
| What actually happened in an experiment, test or implementation step? | `reports/` |
