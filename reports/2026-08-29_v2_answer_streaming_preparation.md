# Real answer streaming — preparation, live stream probe, proposed plan

**Date:** 2026-08-29
**Task input:** `reports/archive/telegram_real_answer_streaming.md`
**Roadmap item:** queue 2, deferred. Nothing here is approved work; the plan
below is an option until the human says the implementation may start.

## Why a live probe came first

The endpoint runs with `--enable-auto-tool-choice --tool-call-parser gemma4
--reasoning-parser gemma4`. In vLLM, a streamed request does not go through the
same code as a complete one: tool calls are assembled by the parser's streaming
path, and a reasoning parser can move text out of `delta.content` entirely.
Nothing in this repository had ever read a streamed response from this
deployment, so the shape the whole design rests on was assumed. If the gemma4
parser emitted tool calls differently under `stream=True`, the loss would not be
a cosmetic preview — it would be every tool the assistant uses.

One probe was authorized and run: three requests inside one wake window,
`stream=True` with `stream_options.include_usage`.

## What the deployment actually sends

Confirmed, not inferred:

- **Text** arrives as `delta.content` fragments. The first chunk carries
  `role` with `content: ""` — an empty delta that must not create a preview.
- **Tool calls** arrive in the standard fragmented OpenAI shape. The opening
  chunk carries `id`, `type` and `function.name` at an `index`; every later
  chunk carries only that `index` and a slice of `function.arguments`:

  ```text
  {"tool_calls":[{"id":"chatcmpl-tool-ac3f…","type":"function","index":0,
                  "function":{"name":"get_weather"}}]}
  {"tool_calls":[{"index":0,"function":{"arguments":"{\"city\": \""}}]}
  {"tool_calls":[{"index":0,"function":{"arguments":"Paris"}}]}
  {"tool_calls":[{"index":0,"function":{"arguments":"\"}"}}]}
  ```

  So arguments are only valid JSON once concatenated, which is why parsing
  belongs at the end of the stream and not in the loop.
- **`finish_reason`** arrives in a final chunk whose delta is empty:
  `stop` for the plain answer, `tool_calls` for both tool cases.
- **`usage`** arrives after that, in a chunk with an empty `choices` list, only
  because `include_usage` was asked for. A parser that skips choice-less chunks
  — which the current `parse_stream_line` does — throws it away.
- **No `reasoning_content` appeared at all**, in any of the three responses,
  despite the reasoning parser being enabled. It is still tolerated rather than
  trusted: an unexpected key must not break assembly, and it stays out of the
  preview, which is what `invoke` already does by reading `message.content`.
- **Text and a tool call can come from one completion.** The third request
  produced a finished sentence and then a tool call in the same response, which
  is the case the plan's message boundaries have to survive.

Timings from the same run, as a side measurement rather than a baseline:
first line at **9.50 s** on the cold request (GPU wake included), **0.31 s** and
**0.30 s** warm; server `vllm-0.26.0`.

The raw responses stayed in the scratchpad. They are worth re-capturing as a
test fixture during implementation, from the recorded shapes above.

**Conclusion: the design in `reports/archive/telegram_real_answer_streaming.md` is
implementable as written, and the one risk that could have changed it is gone.**

## Decisions taken with the human, 2026-08-29

- **Verify against the live model before designing** rather than build to the
  OpenAI specification and find out at acceptance. Done, above.
- **A configuration switch.** `AgentSettings.stream_answers`, default true, so
  `AGENT_STREAM_ANSWERS=false` plus a redeploy returns the old behaviour without
  reverting code. Costs one branch in the model node.
- **Telegram only.** The runtime gains an event API; Chainlit keeps consuming
  `steps()` and complete messages. A second live acceptance is not spent on the
  local interface.

## Proposed work, in order

1. **`ModelBackend.stream` becomes lossless.** Replace the `AsyncIterator[str]`
   contract with `TextDelta` / `CompletionDone` events; `CompletionDone` carries
   the same `Completion` `invoke` returns. Assemble content, tool calls by
   `index`, `finish_reason` and the choice-less usage chunk. Still not retried
   after a first delta. The current contract has no production consumer —
   `scripts/smoke_test.py` and two tests — so changing it is cheap.
2. **The graph streams instead of invoking.** The `model` node consumes the
   stream, forwards text deltas on LangGraph's custom channel, and returns the
   same state patch it returns today. The conditional edge, the tool loop, the
   context-overflow recovery and `persist` are untouched. Behind the switch,
   the node still calls `invoke`.
3. **A typed event API above `_run`.** `AssistantDelta` and `MessageProduced`,
   with `steps()` kept as it is for existing consumers.
4. **`AnswerPreview` in the Telegram adapter**, beside `ToolActivity`: one sent
   message, edits coalesced at about 1 s, finalized by editing the preview into
   the rendered answer, extra pieces sent after it, and a fallback to ordinary
   delivery if the final edit fails. `api.py` needs `edit_message` to accept
   `Formatted` — today it sends plain text with no `parse_mode`, so a finalized
   preview would lose its formatting.
5. **Persistence untouched.** No schema change; deltas, preview state and
   partial arguments are never written.

## Checks planned

Offline: the backend cases against a fixture built from the recorded chunks
above (fragmented arguments, several calls, usage-only chunk, `finish_reason`,
no retry after a delta); runtime cases (a delta observable before the node
finishes, the final message equal to the assembled stream, tool calls still
entering the loop, only complete messages persisted, usage still reaching
`fill`); adapter cases (many deltas producing one send plus throttled edits, the
final answer reusing the preview, no preview for a tool-only response, text →
tool → text boundaries, length limits, failed final edit, malformed Markdown).

Live acceptance, when it is authorized separately: an answer visibly growing in
one message, no duplicate final message, a tool turn still showing activity and
then streaming, the stored conversation holding only canonical messages.

## Gates still ahead

Deployment and any live run are separate human gates. This preparation used one
authorized probe and nothing else.

## Files

`reports/archive/telegram_real_answer_streaming.md` (the human's task input, now tracked),
this report. No source file was changed.
