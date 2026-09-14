# Native single-call mode selection for the GeneralHarness

## Goal

Preserve the current product behavior:

- one natural-language interface;
- no manual `Chat / Agent` switch;
- the model decides whether a request can be answered directly or requires the full autonomous work loop.

At the same time, avoid spending two LLM calls on simple conversational requests.

Target behavior:

```text
simple request
→ 1 LLM call
→ answer

complex work request
→ 1 LLM call decides to start work
→ GeneralHarness loop
→ plan / tools / validation / repair / final
```

## Current conceptual shape

```text
User
 ↓
LLM #1
"direct answer or work?"
 ↓
Graph
 ├─ direct → LLM #2 → final answer
 └─ work   → plan → tools → validation → repair → final
```

This preserves native mode selection, but even a trivial direct request may require two sequential model calls.

The problem is not that the model decides the mode. That behavior is desirable.

The unnecessary part is making the first call only classify the request and then asking the model again to produce the direct answer.

## Preferred design

Make the **first LLM call simultaneously capable of producing the final answer and deciding to enter the work lifecycle**.

```text
                    ┌───────────────┐
User ──────────────→│ First LLM call│
                    └───────┬───────┘
                            │
                 ┌──────────┴──────────┐
                 │                     │
           normal answer          start_work
                 │                     │
               FINAL                   ▼
                                GeneralHarness
                                     │
                              plan / approval
                                     │
                                   tools
                                     │
                                 evaluate
                                     │
                                  repair?
                                     │
                                   FINAL
```

The model remains the authority deciding whether work is required.

There is still no UI switch and no external heuristic classifier.

## Option A — explicit `start_work` capability

Expose a dedicated tool/capability:

```text
start_work(...)
```

The first model call gets normal conversational context plus this capability.

For a direct request:

```text
User:
"Почему небо голубое?"

LLM:
normal text response

→ FINAL
```

Only one model call is needed.

For a work request:

```text
User:
"Изучи этот репозиторий, найди проблему,
исправь её и проверь тестами."

LLM:
tool_call:
start_work(
    goal=...,
    constraints=...,
    acceptance_criteria=...
)
```

The graph interprets this as a transition into the durable autonomous work lifecycle.

`start_work` does not simply mean "call one tool". It means:

> this request requires the GeneralHarness work protocol.

That protocol can continue to own:

- planning;
- task-specific acceptance criteria;
- capability approval;
- consequential-action approval;
- implementation;
- tool execution;
- validation;
- evaluation;
- bounded repair;
- durable checkpoints;
- final result.

This preserves the existing architecture rather than replacing it with a generic ReAct loop.

## Why this fits the current architecture

The application already uses OpenAI-compatible tool calling with automatic tool selection.

Conceptually, the first completion can produce either:

```text
assistant.content
```

or:

```text
assistant.tool_calls
```

The graph therefore does not need a separate classifier response.

Routing becomes:

```text
if response contains start_work:
    enter GeneralHarness work lifecycle
else:
    return response as final conversational answer
```

This is simpler and more native than:

```text
LLM classifier
→ application router
→ second LLM answer
```

The model's own output becomes the routing decision.

## Option B — expose all tools immediately

A conventional agent architecture could give the first model call all tools:

```text
LLM
├─ text response → final
└─ tool call     → execute → LLM → ...
```

This is a classic tool-use / ReAct-style loop.

It is very native, but it is not necessarily the best fit for this project.

The current GeneralHarness provides useful structure above raw tool calling:

- explicit plans;
- acceptance criteria;
- policy/capability grants;
- durable approvals;
- validation;
- bounded repair;
- restartability.

Replacing that with a generic unrestricted tool loop would remove product behavior that is already useful.

Therefore the preferred design is:

```text
normal answer
OR
start_work
```

rather than immediately exposing the entire work lifecycle as an unstructured tool loop.

## Option C — structured router response

Another possible single-call design is to require the first response to follow a schema:

```json
{
  "mode": "direct",
  "answer": "..."
}
```

or:

```json
{
  "mode": "work",
  "plan": {
    "goal": "...",
    "acceptance_criteria": []
  }
}
```

This also reduces the direct path to one inference.

However, it is less attractive than `start_work` because every normal conversational response is forced through an artificial routing schema.

With tool calling, the semantics are cleaner:

```text
normal assistant content
→ answer

start_work tool call
→ autonomous task
```

No explicit mode field is required.

## Latency effect

Current direct path:

```text
LLM #1 → routing decision
LLM #2 → answer
```

If the two calls each take roughly 1.5–3 seconds warm:

```text
direct conversational turn
≈ 3–6 seconds
```

With single-call routing:

```text
LLM #1
├─ direct answer
└─ start_work
```

A direct conversation can approach:

```text
≈ 1.5–3 seconds warm
```

The complex work path changes very little because it already requires several model/tool iterations.

The gain is concentrated in:

- short conversation;
- follow-up questions;
- simple explanations;
- ordinary assistant use.

## Database implications

This optimization should not require changing the durability model.

The desired separation is:

```text
model-call count
≠
database round-trip count
```

A direct turn can load the necessary state once, keep graph state in memory during the turn, and persist final state at meaningful boundaries.

Likewise, a complex work task can checkpoint at durable boundaries such as:

```text
plan accepted
approval requested
approval received
tool batch completed
validation completed
final result
```

There is no requirement to read the full conversation from Postgres before every LLM call.

The routing improvement should therefore be implemented independently from Postgres optimization.

## Failure modes to test

### 1. False direct answer

The model answers directly when a real work task should have entered the harness.

Example:

```text
"Исправь ошибку в репозитории и запусти тесты."
```

The model must not merely explain what it would do.

The prompt/tool description should make the distinction explicit:

```text
If the user's requested outcome requires actions, tools, file changes,
external inspection, validation, or multi-step execution, call start_work
instead of describing hypothetical work.
```

### 2. Unnecessary `start_work`

The model sends simple questions into the expensive autonomous lifecycle.

Examples:

```text
"Что такое KV cache?"
"Переведи это предложение."
"Какая столица Японии?"
```

These should normally remain direct responses.

### 3. Work disguised as a question

Example:

```text
"Можешь посмотреть этот проект и понять, почему тесты падают?"
```

Although grammatically a question, the requested outcome requires inspection and tools.

Mode choice must be based on required execution, not sentence form.

### 4. Direct answer plus `start_work`

Define one unambiguous policy if the model returns both substantial text and `start_work`.

Recommended behavior:

```text
start_work wins as routing signal
```

Any accompanying text can be treated as a short preamble or ignored, depending on UI policy.

### 5. Tool hallucination

The model should not invent fake work-mode tool names.

The registered capability should be explicit and machine-readable.

## Acceptance tests

### Must remain one-call direct

```text
"Привет"
"Почему небо голубое?"
"Объясни speculative decoding"
"Переведи: ..."
"Что было сказано в предыдущем сообщении?"
```

Expected:

```text
1 initial model call
no start_work
no GeneralHarness loop
final answer returned directly
```

### Must enter work lifecycle

```text
"Проверь этот репозиторий и исправь failing tests"
"Создай HTML-файл, проверь его в браузере и пришли результат"
"Проанализируй документ и сохрани структурированный отчёт"
"Найди причину ошибки, исправь её и проверь тестами"
```

Expected:

```text
first model call emits start_work
GeneralHarness starts
normal plan / approval / tools / validation behavior remains intact
```

### Ambiguous cases

```text
"Посмотри код и скажи, что тут не так"
"Как бы ты исправил этот файл?"
"Проверь, есть ли тут проблема"
```

These should be used to tune the semantic boundary between:

```text
analysis that can be answered from already available context
```

and:

```text
work requiring inspection/actions/tools
```

## Recommended architecture

```text
                     User
                      │
                      ▼
             ┌──────────────────┐
             │ Initial LLM call │
             │ tool_choice=auto │
             └────────┬─────────┘
                      │
          ┌───────────┴───────────┐
          │                       │
          ▼                       ▼
  assistant content       start_work(...)
          │                       │
          ▼                       ▼
        FINAL              GeneralHarness
                                  │
                                  ▼
                                plan
                                  │
                             approval?
                                  │
                                  ▼
                                tools
                                  │
                                  ▼
                              validation
                                  │
                             repair if needed
                                  │
                                  ▼
                                FINAL
```

## Recommendation

Use **Option A: an explicit `start_work` capability/tool**.

It preserves the core product invariant:

> one natural-language interface where the model itself decides whether autonomous work is required.

At the same time it removes the unnecessary classifier-only LLM call from ordinary conversation.

The architectural principle is:

> The first model response should already be useful.  
> It either **is the final answer** or **is the decision to begin work**.

The graph remains responsible for deterministic workflow transitions and durable execution, while the model remains responsible for deciding whether that workflow is needed.
