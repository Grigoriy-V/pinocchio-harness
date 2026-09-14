# Telegram Baseline Chat Product — UX / Rich Text / Streaming

## Purpose

This document defines the desired shape of **ROADMAP queue item 2: Baseline chat product and live evidence**.

The goal of this item is to make the Telegram interface feel like a normal, readable assistant product before deeper harness work begins.

This is primarily a **Telegram UX / presentation task**, not a new agent architecture. Do not move product decisions into Telegram that belong to the agent, and do not create new general abstractions unless the existing boundaries cannot support the requirement.

Implementation still follows `AGENTS.md`, `ROADMAP.md`, the four canonical docs, and normal human gates.

---

## Product outcome

A person opens the Telegram bot and immediately understands:

- what the assistant is;
- that they can simply talk to it in natural language;
- which small set of commands exists;
- that answers are formatted cleanly;
- that code, lists, emphasis, quotes and links are readable;
- that longer responses feel responsive rather than dead;
- that plans/results from autonomous work are readable.

The Telegram UI should not expose internal distinctions such as “chat mode”, “agent mode”, tool selection, or workflow selection.

---

# 1. Telegram command shell

## Desired behavior

Use Telegram’s native bot command/menu surface rather than inventing a custom command UI.

The visible product commands should be approximately:

```text
/new   — start a new conversation
/can   — show currently available capabilities
/stop  — stop the current task
/help  — show help
```

`/check` should remain available as a diagnostic command, but it does not need to be a primary user-facing menu item.

The current command owners are already in:

- `ui/telegram/adapter.py`
- `ui/telegram/api.py`
- `tools/telegram_webhook.py`

Do not create another command registry/service unless the existing ownership cannot support native Telegram command registration cleanly.


## Command/onboarding presentation

The command experience should look like product UI rather than a raw debug list.

Use three Telegram-native surfaces together, without inventing another menu system:

1. **Bot description / empty-chat description** — a very short explanation of what the assistant is.
2. **Native command menu** — command names with concise descriptions via Telegram's bot command API.
3. **`/start` / `/help` message** — a compact rich-text card-like message rendered through the same safe Telegram formatting path as other project-authored UI.

A suitable `/start` shape is:

```text
Personal Assistant

Talk to me normally. You can send text, images, voice messages and supported
documents. I can inspect files, use the web and carry out longer tasks when
needed. I will ask before consequential actions.

Commands
/new   New conversation
/can   Available capabilities
/stop  Stop current task
/help  Show this help
```

The exact wording may be improved during implementation, but keep it short.

Use formatting intentionally:

- one clear title;
- one short explanatory paragraph;
- one compact command section;
- bold command labels or other restrained emphasis where Telegram supports it;
- no wall of text and no internal tool names.

Do not use an inline keyboard as a duplicate permanent command menu. Telegram's
native command menu is the persistent navigation surface.

The native command menu itself should carry readable descriptions, not only raw
command names.

## `/start`

`/start` should send one concise onboarding message.

It should explain, in user-facing language:

- this is a personal multimodal assistant;
- the user can talk normally;
- images, voice and supported documents may be sent;
- the assistant may use tools or perform longer work when needed;
- consequential actions require approval;
- the main commands.

Do **not** dump the internal tool inventory into `/start`. `/can` already exists for truthful runtime capability reporting.

`/help` may reuse the same content or a slightly more command-oriented version.

---

# 2. Canonical answer format: ordinary Markdown

## Decision

The assistant should continue to produce **ordinary Markdown-style text** as its canonical textual answer format.

Plain prose is simply Markdown with no markup.

The model should not be prompted to emit Telegram-specific MarkdownV2 or Telegram HTML.

Examples of normal model output:

```md
## Основные моменты

1. **Cordis** — основной framework.
2. **Profiles** — композиции bundles.

Пример:

~~~python
print("hello")
~~~
```

This same conceptual answer should remain usable by any interface.

---

# 3. Telegram owns rendering, not content semantics

Telegram should render the model’s ordinary Markdown into a **safe Telegram-native representation**.

Preferred conceptual flow:

```text
assistant text
    ↓
ordinary Markdown
    ↓
Telegram renderer
    ↓
safe Telegram HTML/entities
    ↓
sendMessage
```

This rendering belongs in the Telegram interface layer.

Do not put Telegram markup rules into:

- the model system prompt;
- `Message` / `ContentPart`;
- the agent graph;
- the task runtime;
- persistence.

The persisted assistant text should remain the canonical model text, not a Telegram-transformed copy.

---

# 4. Supported rich-text subset

The first version does **not** need a complete CommonMark implementation.

Support only the structures that materially improve assistant UX:

- bold;
- italic;
- headings;
- inline code;
- fenced code blocks;
- ordered and unordered lists;
- block quotes;
- links.

Anything unsupported should degrade into readable text rather than fail the answer.

Tables, deeply nested Markdown, custom HTML, unusual extensions and perfect CommonMark compatibility are explicitly not required for this baseline.

---

# 5. Safety / degradation rule

Formatting is presentation polish. It must never become a new failure mode for the assistant.

Required invariant:

```text
valid render
→ send formatted message

renderer/parser failure
or unsafe/unbalanced output
or formatting cannot survive splitting
→ send readable plain text
```

A model response must not be lost because of malformed Markdown or Telegram parser rejection.

The current code already follows this philosophy for project-authored `Formatted` messages: model-derived bodies are escaped and overly long formatted messages fall back to plain text.

Relevant current owners:

- `ui/telegram/api.py`
  - `Formatted`
  - `TelegramClient.send_message`
  - `_render`
  - `split_message`
- `ui/telegram/adapter.py`
  - normal assistant delivery
  - task plan/result presentation

Extend/reuse these owners rather than creating a second Telegram rendering path.

---

# 6. Existing `Formatted` messages

Task plans and task results already have structured Telegram formatting through `Formatted`.

Preserve this behavior.

The new ordinary Markdown renderer should not make task-specific structured output less reliable.

A simple implementation may keep both concepts:

```text
project-authored structured UI text
→ Formatted

model-authored ordinary answer
→ Markdown renderer → Telegram-safe format
```

Do not force these into one abstraction unless doing so is clearly simpler.

---

# 7. Long-message behavior

Telegram has message-size limits. Rich text must remain correct when a response is long.

The implementation should prefer readable boundaries when splitting:

1. paragraph / line break;
2. whitespace;
3. hard cut only as a last resort.

Do not split in a way that causes Telegram markup to become invalid.

Acceptable baseline strategies include:

- render each independently safe chunk;
- or fall back to plain text when a formatted answer cannot be split safely.

Correctness and delivery are more important than preserving every formatting token.

---


# 8. Reusable inline action state

Inline keyboards should support a small reusable interaction pattern beyond the
current plan approval UI.

This is **Telegram presentation/state handling**, not task-plan architecture.
The current plan approval may disappear later; the capability to render and
settle inline actions should remain useful for confirmations and other compact
interactions.

## Desired lifecycle

Before interaction:

```text
[ Run it ]   [ Don't ]
```

After the callback is accepted and the application state transition succeeds,
edit the **same Telegram message's reply markup** so the original choices no
longer remain actionable.

Approved:

```text
[ ✓ Approved ]
```

Rejected:

```text
[ ✕ Rejected ]
```

Do not send a second "approved" / "rejected" message merely to represent button
state unless there is additional information that belongs in chat history.

## Button color

Current Telegram Bot API versions support an optional inline-button `style`:

- `success` — green;
- `danger` — red;
- `primary` — blue.

Use:

```text
Approved → success
Rejected → danger
```

Treat the text/icon as the semantic state and color as enhancement. The state
must remain understandable on clients/themes where color presentation differs.

## Settled buttons are status, not another action

The preferred result is one status button that visually remains with the
message. If Telegram requires callback data for the button shape, use a no-op
status callback which only acknowledges the callback (for example, "Already
approved") and performs **no application mutation and no model wake**.

A settled status callback must therefore be classified as model-free at the raw
Telegram wire layer.

Do not let pressing `✓ Approved` rerun the approval, resume the task again, or
wake the GPU.

## Message identity requirement

To edit the keyboard after a callback, the adapter needs the Telegram message
identity associated with that callback.

The current reduced `Incoming` callback shape carries `chat_id`,
`callback_id`, and `callback_data`, but not the callback message id. Extend the
Telegram wire representation only as far as required to preserve that message
identity.

Keep `ui/telegram/wire.py` standard-library-only and cheap to import.

## Generic ownership

Prefer a small generic keyboard/state helper in the existing Telegram surface,
for example conceptually:

```text
action keyboard
    ↓ callback
application transition
    ↓ success
settled keyboard
```

Likely owners remain:

- `ui/telegram/api.py` — construct/send/edit inline reply markup;
- `ui/telegram/wire.py` — reduce callback message identity and classify
  model-free settled callbacks;
- `ui/telegram/adapter.py` — map callback intent to application behavior and
  settle the UI only after the application transition succeeds.

Do not build a general cross-platform button framework for this baseline.

## Failure rule

The visible button state must reflect the real application state.

Therefore:

```text
callback received
→ attempt application action
→ action succeeds
→ edit keyboard to Approved / Rejected

action fails
→ do not falsely mark it settled
→ acknowledge/report the failure
```

UI settlement is evidence of an accepted state transition, not optimistic
decoration.



# 9. Tool activity presentation

Telegram must not expose internal tool names such as:

```text
· send_file
· fetch_page
· view_web_page
```

Those names are implementation details. They are useful in traces and diagnostics,
not as normal product UI.

## User-facing activity labels

Map known tool calls to short human-readable status labels in the Telegram layer.

These labels are intentionally **always in English**, regardless of the language
of the conversation. Assistant answers remain in the user's language.

Example mapping:

```text
search_web      → Searching the web…
fetch_page      → Reading page…
view_web_page   → Opening page…
read_document   → Reading document…
view_pages      → Inspecting document…
read_file       → Reading file…
write_file      → Writing file…
edit_file       → Editing file…
send_file       → Sending file…
```

Unknown tools should degrade to a generic:

```text
Working…
```

Do not ask the model to generate these labels. They are presentation for known
tool activity, so a small Telegram-owned mapping is appropriate.

## Presentation behavior

Prefer one transient activity message per turn rather than one permanent message
per tool call.

Conceptually:

```text
Searching the web…
        ↓ edit same status message
Reading page…
        ↓ edit same status message
Sending file…
        ↓
final assistant answer
```

The goal is to show progress without filling chat history with internal activity.

The exact lifecycle may depend on what Telegram allows cleanly, but follow these
rules:

- do not expose raw tool names in normal chat;
- do not emit a burst of one message per tool call;
- reuse/edit one status message when practical;
- when the final answer starts streaming or is ready, the transient tool status
  should no longer compete with the final answer;
- tool failures may be reflected in the final assistant answer or a meaningful
  product-facing error, not as raw exception/tool syntax.

This remains a Telegram presentation concern. Do not move the mapping into the
model prompt, tool definitions, or application-domain `Message` types.

Likely owners:

- `ui/telegram/adapter.py` — choose the user-facing activity label for a tool call;
- `ui/telegram/api.py` — send/edit the transient status message.


# 10. Streaming is part of the product baseline, but a separate sub-step

## Important distinction

There are two different problems:

### Telegram display streaming

Telegram can show an updating/draft message while the answer is being generated.

This is primarily interface work.

### Correct agent-runtime streaming

The current agent path is not yet a true streaming execution path.

Current facts:

- `ModelBackend.stream()` yields only text chunks;
- the current OpenAI-compatible streaming parser extracts text delta content;
- streamed tool-call deltas and final usage are not part of that contract;
- the ordinary agent graph currently uses `backend.invoke()` and receives a complete `Completion`;
- tool decisions therefore happen after a complete model result.

Relevant owners:

- `app/models/base.py`
  - `ModelBackend.stream`
  - `Completion`
- `app/models/openai_compatible.py`
  - streaming request/parsing
- `app/agent/graph.py`
  - current `backend.invoke()` loop
- `ui/telegram/api.py`
  - Telegram send/edit/draft transport
- `ui/telegram/adapter.py`
  - UI delivery

## Requirement

Do **not** implement fake streaming by bypassing the agent graph and calling the backend directly from Telegram.

The final architecture must preserve:

- tool calls;
- tool results;
- usage;
- finish reason;
- persistence;
- approvals;
- the same final assistant `Message`;
- the same agent loop semantics as non-streaming execution.

Streaming is presentation of an existing execution, not a parallel Telegram-only inference path.

---

# 11. Suggested implementation order

## 2A — Telegram UX baseline

Implement first:

1. native Telegram command menu;
2. concise `/start` / `/help`;
3. ordinary Markdown → safe Telegram rendering;
4. safe fallback to plain text;
5. readable long-message handling;
6. human-readable English tool activity labels instead of raw tool names;
7. reuse/edit one transient tool status message per turn where practical;
8. preserve existing task plan/result formatting;
9. live UX acceptance.

This should remain mostly inside `ui/telegram/` and existing operational Telegram setup.

## 2B — Real answer streaming

Then design/implement streaming through the normal runtime.

Desired conceptual behavior:

```text
turn starts
    ↓
Telegram typing indicator while no answer text exists
    ↓
first assistant text delta
    ↓
draft/update visible message
    ↓
more deltas
    ↓
tool call may interrupt textual answer
    ↓
tool execution / subsequent model step
    ↓
final answer
    ↓
final normal Telegram message
    ↓
canonical final Message persisted normally
```

The user should never receive a final answer that disagrees with the persisted assistant result.

---

# 12. Streaming UX constraints

Streaming does not need token-by-token UI updates.

Prefer throttled/coalesced updates to avoid excessive Telegram API calls.

For example, update when either:

- enough new text accumulated; or
- a short time interval elapsed.

Exact cadence should be chosen empirically and kept simple.

While the model is cold-starting or no textual output exists yet, retain the existing Telegram `typing…` indicator.

Do not add Telegram stop-generation support in this baseline unless separately justified. Existing `/stop` semantics and future streaming cancellation are different control paths.

---

# 13. Architecture boundary

This item should **not** become a general presentation framework.

Current intended ownership:

```text
agent/runtime
    owns semantic answer and tool behavior

Message / ContentPart
    owns interface-neutral domain content

Telegram adapter/API
    owns Telegram rendering and Telegram UX

Chainlit
    keeps its own rendering behavior
```

The main product rule remains:

> The model decides what to say. The interface decides how to render that same semantic content on its platform.

Do not make the adapter decide which internal evidence should be exposed to the user. Observation versus explicit presentation remains unchanged.

---

# 14. Acceptance criteria

## Command / onboarding

- Telegram exposes the intended command list through the native bot menu.
- `/start` gives a short useful onboarding message.
- `/help` clearly explains commands.
- `/can` remains the truthful capability source.
- `/check` remains available for diagnostics without becoming prominent product UI.

## Rich text

A normal model response containing all of the following renders readably:

- plain paragraphs;
- bold;
- italic;
- heading;
- ordered list;
- unordered list;
- inline code;
- fenced code block;
- quote;
- link.

A malformed or unsupported Markdown response is still delivered as readable text.

A long response is delivered fully without Telegram parse failures.

## Tool activity UX

- normal Telegram chat never exposes raw internal tool names such as `send_file`;
- known tools map to concise English activity labels;
- assistant answer language remains independent from these English status labels;
- unknown tools degrade to `Working…`;
- multiple tool calls do not create a noisy burst of permanent status messages;
- one transient status message is reused/edited where practical.

## Inline interaction UX

- an actionable inline keyboard can expose two or more choices;
- after a successful callback, the same message is updated to one settled status button;
- approved state uses `success` styling where supported;
- rejected state uses `danger` styling where supported;
- settled status callbacks cannot repeat the action or wake the model;
- a failed application transition is never displayed as approved/rejected.

## Task UX

- existing task plan approval remains readable;
- existing task result/check presentation remains readable;
- approval buttons still attach to the intended final message;
- current plan approval uses the reusable settled-button behavior rather than a
  plan-specific Telegram-only hack.

## Streaming

When streaming is implemented:

- text becomes visible before full generation finishes;
- tool-capable turns still work;
- tool calls are not lost;
- usage/final completion data remain available;
- the persisted final assistant message matches the completed answer;
- Telegram transport does not bypass the normal agent runtime;
- a stream failure does not create a false successful final answer.

---

# 15. Live evidence for queue closure

The live Telegram acceptance should be small and product-facing.

Suggested scenarios:

### Scenario A — onboarding

Fresh/open bot:

- command menu is visible;
- `/start` is readable and concise.

### Scenario B — formatting

Ask for an answer that naturally contains:

- a heading;
- a numbered list;
- bold emphasis;
- inline code;
- one code block.

Verify visually in Telegram that no raw `**`, broken fences, or parse errors remain unless intentionally shown as code.

### Scenario C — tool activity presentation

Use a request that causes at least two tool calls.

Verify:

- raw tool names are not shown;
- English activity labels are readable;
- the chat does not receive a burst of one permanent message per tool;
- the final assistant answer remains in the language used by the user.

### Scenario D — inline action settlement

Exercise the current approval interaction.

Verify:

- the initial choices are visible;
- after approval, they become one `✓ Approved` status button;
- after rejection, they become one `✕ Rejected` status button;
- success/danger styling is visible on a current Telegram client;
- pressing the settled button does not repeat the action or wake the model.

### Scenario E — capability truth

Ask the assistant in natural language what it can do.

Compare against `/can`.

The two do not need identical wording, but they must not materially contradict each other.

### Scenario F — ordinary tool-capable answer

Use a normal request that causes a tool call and then a final textual answer.

Verify that presentation remains readable and no tool/result semantics are broken.

### Scenario G — streaming, once implemented

Use one sufficiently long answer.

Verify:

- `typing…` covers initial waiting;
- draft text appears during generation;
- final message replaces/completes the draft cleanly;
- no duplicated answer;
- formatting is correct in the final message.

One warm live run may exercise several of these together where practical.

---

# 16. Explicit non-goals for this item

Do not use this queue item to redesign:

- `GeneralHarness`;
- task planning architecture;
- session event storage;
- subagents;
- sandbox execution;
- permission model;
- conversation serialization;
- general UI abstraction across all platforms.

If correct streaming exposes a small necessary runtime seam, change only what is needed to support the same agent execution with incremental output.

Deeper harness redesign belongs to the later harness/loop roadmap item after baseline observability exists.

---

# Desired result

After this item, Telegram should feel like a real assistant rather than a debug transport:

```text
native commands
+ concise onboarding
+ readable rich text
+ safe code/list formatting
+ clean English tool activity statuses
+ reusable settled inline actions
+ existing task UI
+ responsive generation
```

without compromising the existing product boundaries or turning Telegram UX into a new architecture layer.
