# Real Answer Streaming

## Goal

Add real assistant-response streaming to the normal conversational agent path without changing conversation selection, persistence semantics, tool execution, approvals, or the task runtime.

OpenClaw is the architectural reference for the separation of concerns:

**model/provider stream → agent runtime → channel preview**

The Telegram adapter must not fake streaming around a non-streaming agent call.

## References

- OpenClaw repository: https://github.com/openclaw/openclaw
- OpenClaw streaming architecture: https://github.com/openclaw/openclaw/blob/main/docs/concepts/streaming.md
- OpenClaw Telegram draft stream implementation: https://github.com/openclaw/openclaw/blob/main/extensions/telegram/src/draft-stream.ts
- OpenClaw Telegram streaming tests: https://github.com/openclaw/openclaw/blob/main/extensions/telegram/src/draft-stream.test.ts
- OpenClaw agent-core stream contracts: https://github.com/openclaw/openclaw/blob/main/packages/agent-core/src/types.ts
- LangGraph streaming docs: https://docs.langchain.com/oss/python/langgraph/streaming
- vLLM streaming tool-calling example: https://docs.vllm.ai/en/latest/examples/tool_calling/openai_chat_completion_client_with_tools_xlam_streaming/

## Current gap

The project already has the pieces, but they are disconnected:

- `ModelBackend.stream()` yields only text strings.
- `OpenAICompatibleBackend.stream()` reads only `delta.content`.
- streamed `tool_calls`, `usage`, and `finish_reason` are discarded.
- the LangGraph `model` node uses `backend.invoke()`.
- `Agent._run()` exposes only completed node updates.
- Telegram therefore receives complete `Message` objects only.

The streaming implementation must preserve exactly the same completed agent turn that exists today.

## Target architecture

```text
Telegram webhook
    ↓
TelegramAdapter
    ↓
Agent / LangGraph
    ↓
model node
    ↓
ModelBackend.stream()
    ↓
OpenAI-compatible SSE / vLLM

ModelBackend
    ├─ emits text deltas
    └─ assembles final Completion
           ├─ text
           ├─ tool_calls
           ├─ usage
           └─ finish_reason

LangGraph
    ├─ forwards assistant deltas as custom stream events
    └─ uses final Completion for the existing graph
           model → tools → model → persist

Telegram
    ├─ previews deltas by editing one message
    └─ receives the same final Message that is persisted
```

Partial output is presentation state only. It must never be written to conversation storage.

## 1. Make model streaming lossless

Replace the current text-only streaming contract with structured events.

A minimal contract is enough:

```text
TextDelta(text)
CompletionDone(completion)
```

`CompletionDone.completion` must contain the same fields as `invoke()`:

```text
Completion
  text
  tool_calls
  usage
  finish_reason
```

For OpenAI-compatible streaming:

- accumulate `delta.content`;
- accumulate fragmented `delta.tool_calls` by tool-call `index`;
- concatenate fragmented function argument strings and parse JSON only when the completion finishes;
- capture `finish_reason`;
- request streaming usage with `stream_options.include_usage = true`;
- accept usage chunks with no `choices`.

For the current vLLM backend this is supported directly.

Do not retry a stream after visible output has been emitted. A retry would duplicate already-visible text. Retry-before-first-delta can be added later if needed.

## 2. Stream through the graph, not around it

The `model` node must consume `backend.stream()` instead of calling `invoke()` for conversational model calls.

While consuming it:

1. forward text deltas through LangGraph custom streaming;
2. receive/assemble the final `Completion`;
3. create the normal `assistant_message(completion)`;
4. return the normal state patch with `messages` and `usage`.

The existing conditional edge remains unchanged:

```text
model
  ├─ tool_calls → tools → model
  └─ no tools   → persist
```

Use LangGraph's custom stream channel (`StreamWriter` / `stream_mode=["updates", "custom"]`) for provider-independent deltas.

Do not move tool execution into the streaming layer.

## 3. Expose typed agent events

Do not break the existing `steps() -> AsyncIterator[Message]` contract unnecessarily.

Add a streaming/event API above `_run()`, conceptually:

```text
AssistantDelta(text)
MessageProduced(message)
```

Telegram uses the event API.

Existing consumers that only care about completed messages can keep using `steps()`.

This gives the project the same important separation OpenClaw uses: model events belong to the runtime; presentation belongs to the channel.

## 4. Telegram preview

Implement a small `AnswerPreview` beside the existing `ToolActivity`.

Use:

```text
sendMessage
    ↓
editMessageText
    ↓
editMessageText
    ↓
final edit
```

Do **not** use Telegram `sendMessageDraft` for the answer. OpenClaw uses a persistent message plus edits so the preview can become the final message instead of creating a duplicate final bubble.

`AnswerPreview` should own:

- cumulative assistant text;
- Telegram `message_id`;
- throttling/coalescing;
- finalization;
- cleanup after failure.

Do not perform one Bot API edit per token. Coalesce deltas and update roughly every 0.5–1.0 s. OpenClaw defaults its Telegram preview throttle to 1 s.

The first useful text should appear quickly, but tiny one-token previews can be briefly debounced.

## 5. Finalization

The canonical final answer remains ordinary model Markdown.

On completion:

- render the final accumulated text through the existing Telegram Markdown renderer;
- edit the active preview into the final rendered first message;
- if the final answer requires several Telegram messages, reuse the preview for the first piece and send only the remaining pieces;
- if final editing fails, fall back to normal final delivery and clean up the stale preview.

Never send an additional complete answer after a successfully finalized preview.

This is one of the main OpenClaw behaviors to copy.

## 6. Tool-loop behavior

Streaming must work across multiple model calls in one user turn.

### Normal answer

```text
model deltas
→ preview grows
→ completed assistant Message
→ persist
```

### Tool-only call

```text
model emits tool call, no text
→ no answer preview
→ existing ToolActivity appears
→ tool executes
→ next model call streams final answer
```

### Text + tool call

```text
model text streams
→ that assistant message is finalized
→ tool activity
→ tool result
→ next assistant response gets a new preview
```

Tool calls, approvals and tool results must behave exactly as they do before streaming.

## 7. Persistence and failure semantics

Only completed graph messages are durable.

Never persist:

- token deltas;
- Telegram preview state;
- incomplete tool-call arguments;
- incomplete assistant text after a failed provider stream.

If a provider stream fails after partial output:

1. abandon/delete the unfinished Telegram preview where practical;
2. do not persist it;
3. let the existing turn error path report the failure.

No database schema change is required.

## 8. Telegram/API work

Current `TelegramClient.edit_message()` is sufficient as a primitive but will need final-message support for the existing `Formatted` rendering and safe fallback.

Keep Telegram-specific concerns here:

- 4096-character limits;
- HTML/Markdown rendering;
- message splitting;
- edit failures;
- throttling;
- fallback to plain text.

None of these rules belong in `ModelBackend` or the graph.

## 9. Tests

### Model backend

- streamed text assembles to the same final text;
- fragmented tool call name/arguments assemble correctly;
- multiple tool calls preserve order/index;
- usage-only final chunk is captured;
- `finish_reason` survives;
- stream failure after a delta is not retried.

### Agent/runtime

- first delta is observable before the model node finishes;
- final `Message` equals the assembled stream result;
- tool calls still enter the existing tool loop;
- tool result → second model call also streams;
- only completed messages are persisted;
- usage still reaches `Agent.fill()`.

### Telegram

- many deltas produce one send plus throttled edits, not many messages;
- final answer reuses the preview message;
- tool-only model response creates no empty preview;
- text → tool → text produces correct message boundaries;
- long answers respect Telegram limits;
- final edit failure falls back without losing the answer;
- malformed Markdown still degrades to readable plain text.

OpenClaw's `extensions/telegram/src/draft-stream.test.ts` is the primary test-case reference:
https://github.com/openclaw/openclaw/blob/main/extensions/telegram/src/draft-stream.test.ts

## 10. Acceptance

Live acceptance should prove:

1. a slow plain answer visibly grows in one Telegram message;
2. no duplicate final answer appears;
3. a request that uses a tool still shows tool activity and then streams the final answer;
4. the stored conversation contains only complete canonical messages;
5. `usage` is still available after a streamed turn;
6. restart/persistence/conversation selection behavior is unchanged.

Measure separately:

- provider TTFT;
- first visible Telegram preview;
- total turn time.

There is currently an additional `GeneralHarness.decide()` model call before the conversational answer. This streaming task does not need to redesign it. Until the later single-call routing change, end-to-end first-visible-token latency will still include that routing call.

## Non-goals

Do not combine this work with:

- `/new` or `/chats` changes;
- conversation schema changes;
- task-runtime streaming;
- router/single-call redesign;
- new model providers;
- replacing the Telegram transport;
- streaming partial messages into persistence.
