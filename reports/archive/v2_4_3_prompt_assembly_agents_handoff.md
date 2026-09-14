# V2 Step 4.3 Addendum: Prompt Assembly and User Instructions

Handoff / proposed correction to the current 4.3 plan

## 1. Decision

- Treat prompt composition as part of Step 4.3, because the live 4.3 regression showed that prompt/tool guidance is now a product-level dependency of autonomous behavior.
- Keep the already implemented TurnStopping seam unchanged unless a concrete defect is found. It remains stop-by-default and continues only through explicit structured steering.
- Replace tool-specific knowledge in DEFAULT_SYSTEM_PROMPT with capability-owned guidance assembled from what is actually wired to the agent.
- Add exactly one persistent AGENTS.md instruction file per user. It is global for that user across all chats/threads and is not tied to a project hierarchy yet.
- AGENTS.md is a prompt overlay, not memory. It must not use remember_fact, memory retrieval, preference extraction, or any automatic “learn the user” behavior.
## 2. Target prompt shape

```text
STABLE CORE
  minimal identity / persona / general authority rules

CAPABILITY GUIDANCE
  filesystem
  browser / visual observation
  documents
  web
  memory capability guidance
  presentation
  later: sandbox

TOOL SCHEMAS
  only tools actually available to this agent

USER INSTRUCTIONS
  one user-level AGENTS.md overlay

CONTEXT
  summary / retrieved facts / recent exact history

CURRENT TURN
  user request + tool trajectory
```

Core rule: the core prompt should know as little as possible about specific tool names. A capability owns its own guidance; the tool schema owns the concrete API.

## 3. Core prompt

Keep the fixed core deliberately small. Do not replace the current monolith with a different long monolith.

```text
You are a general-purpose AI assistant with tools.
Follow the capabilities, policies, and instructions provided in your context.
```

If additional global behavioral rules are truly model-independent and cannot belong to a capability, keep them short and stable. Do not put HTML-, PDF-, browser-, filesystem-, web-, memory-, or send-file-specific workflows here.

## 4. Capability-owned guidance

Move current tool-specific prose out of DEFAULT_SYSTEM_PROMPT into guidance generated only when that capability is present. The existing capability_brief/toolbox machinery is the starting point; this should become a real prompt-assembly layer rather than another duplicate description of the tools.

## 5. AGENTS.md: exact semantic boundary

AGENTS.md means: “how I want this agent to work.” It is an explicit, user-controlled extension of the prompt.

- One canonical file per user, always applicable to that user across chats/threads.
- No automatic extraction from conversation.
- No “I noticed you prefer X, so I saved it.”
- No coupling to remember_fact, retrieved facts, summaries, or long-term memory.
- No chat/thread-specific version in V1.
- No project/nested-directory hierarchy in V1.
- No database mirror as a second source of truth.
Canonical source of truth:

```text
/workspaces/<user>/AGENTS.md
```

The file may be edited through normal workspace tools. A Telegram command can be added as a thin UI over the same file, but the command must not create a second instruction store.

```text
/agents
  show current AGENTS.md

/agents set
  take the next user message as complete replacement text
  write /workspaces/<user>/AGENTS.md

/agents clear
  clear or remove the same file
```

## 6. Authority and prompt placement

AGENTS.md must be lower authority than system/security/capability policy. Treat it as sourced user instruction context, not as system policy. It may guide behavior but must not override permission, isolation, safety, or real capability boundaries.

```text
system / product policy
        >
capability + permission boundaries
        >
direct current user request
        >
user AGENTS.md guidance when applicable
        >
retrieved memory / other contextual hints
```

Implementation may use a durable user-role instruction message (DeepSeek-style) rather than concatenating AGENTS.md into the system string. The important property is reconstructability, stable ordering, explicit source, and lower authority.

## 7. AGENTS.md is not memory

Important semantic rule: a future “remember this” feature must write to memory, not to AGENTS.md. Conversely, editing AGENTS.md must not create memory facts.

## 8. Step 4.3 scope after this addendum

1. Keep the existing minimal TurnStopping seam and its streaming withdrawal behavior.
1. Introduce prompt assembly with a tiny core, capability-owned guidance, actual tool schemas, and the user AGENTS.md layer.
1. Move concrete tool guidance out of DEFAULT_SYSTEM_PROMPT; avoid duplicated instructions between core prompt, capability brief, and tool descriptions.
1. Load the single user AGENTS.md for every chat/thread of that user. Changes must become visible without a redeploy.
1. Do not implement memory redesign, fact extraction, vector search, project AGENTS hierarchy, sandbox, or PDF-specific tooling as part of this correction.
1. Re-run live proportional-validation acceptance only after the new prompt assembly is in place.
## 9. Acceptance

- Plain conversational answer: no extra validation/model call introduced by prompt assembly or TurnStopping.
- Simple text write: agent uses workspace action and may finish without an artificial validation pass.
- Natural visual artifact request: agent performs the requested workspace action and autonomously uses an appropriate observation capability when evidence is material; no permission round-trip for safe observation.
- The agent does not make visual/behavioral claims that are unsupported by observed evidence.
- The acceptance test does not require a hard-coded inspect_page workflow; current inspect_page is simply the available browser/evidence capability.
- Changing AGENTS.md changes subsequent agent behavior/context without touching memory and without redeploying the application.
- Starting a new chat/thread still receives the same single user AGENTS.md.
- Removing/clearing AGENTS.md removes that overlay; no stale copy remains in a second store.
- Tool inventory and capability guidance agree with the actually wired toolbox; unavailable tools are not advertised.
## 10. Explicit non-goals

- Do not make AGENTS.md a preference-learning feature.
- Do not infer or write AGENTS.md from ordinary conversation.
- Do not route “remember this” into AGENTS.md.
- Do not add a validator model, finish tool, HTML workflow, PDF workflow, or new obligation state to TurnStopping.
- Do not implement DeepSeek’s full global/project/nested instruction hierarchy yet.
- Do not duplicate capability guidance in a new large default prompt.
## 11. Reference architecture

The direction intentionally follows the useful separation in DeepSeek Harness: a small system-prompt core, plugin/capability-owned prompt sections and tool schemas, plus a separate AGENTS.md-compatible instruction context. This is a structural reference, not a requirement to port their implementation wholesale.

- DeepSeek Harness: packages/core/system-prompt — compositional prompt + tool-schema assembly per step.
- DeepSeek Harness: packages/context/agent-instructions — AGENTS.md/CLAUDE.md instruction context, lower authority than system/direct user instructions.
Status of this document: handoff/addendum defining the proposed 4.3 correction. It does not expand the scope into later memory, sandbox, or project-instruction work.
