# Real answer streaming — implementation and offline evidence

**Date:** 2026-08-29
**Task input:** `reports/archive/telegram_real_answer_streaming.md`
**Preparation and live probe:** `reports/2026-08-29_v2_answer_streaming_preparation.md`
**Roadmap item:** queue 2.

Not deployed and not accepted live. Everything below was proven offline.

## What changed and why

A person asking a question watched a blank chat until the whole answer existed.
The pieces to do better were already there and disconnected: the backend could
stream, but only text, so streaming was unusable by an agent that needs tool
calls; the graph called `invoke`; and the adapter only ever saw finished
messages.

The rule the design follows is that streaming may change what is **shown** and
nothing else. The turn the graph runs, the messages the store keeps, the tool
loop, the approvals and the reported usage are the ones that existed before.

- **`ModelBackend.stream` is now lossless.** It yields `TextDelta` while text
  arrives and exactly one `CompletionDone` carrying the same `Completion`
  `invoke` returns — text, tool calls, usage, finish reason. The old contract
  returned bare strings, which is why nothing but a smoke script could use it.
- **`StreamedCompletion` assembles a response** the way the deployed server
  actually sends one: tool calls opened by an `id`/`name` chunk and continued by
  argument slices at the same `index`, JSON parsed once at the end, a
  `finish_reason` arriving alone, and usage arriving in a chunk with no choices.
  `stream_options.include_usage` is now requested, without which usage never
  arrives at all and a turn of unknown size cannot be folded.
- **The graph streams instead of invoking.** The `model` node consumes the
  stream, forwards deltas on LangGraph's custom channel and returns the same
  state patch as before. The conditional edge, the tool node, the overflow
  recovery and `persist` are untouched. A stream that ends without a completion
  is an error, so a truncated answer can never be mistaken for a finished one.
- **The runtime reports typed events.** `AssistantDelta` is presentation and is
  never stored; `MessageProduced` is what the conversation keeps. `steps()` and
  `resume()` still yield finished messages, so Chainlit did not change and does
  not know that turns are streamed.
- **Telegram shows one message being written.** `AnswerPreview` sends one
  message, edits it about once a second, and finalizes it by editing the
  rendered answer into that same message — the preview becomes the answer
  rather than being followed by a duplicate of it. Nothing it does can fail a
  turn: a preview that cannot be sent or edited stands aside for the rest of the
  turn and the answer arrives the ordinary way.
- **`AGENT_STREAM_ANSWERS`** turns the whole thing off in configuration.

Two smaller decisions worth naming. A preview is not sent until about 24
characters exist, because a bubble holding one word that is about to be replaced
helps nobody and short answers simply arrive whole. And what the preview shows
is the model's raw text: half-written Markdown is not valid markup, so rendering
happens once, at the end, when the text is whole.

## Failure and boundary behaviour

- A model turn that only calls tools has no text, so no preview appears; the
  existing tool activity does its job unchanged.
- Text followed by a tool call in one response finalizes that text as its own
  message; the next model call gets its own preview.
- A failed final edit deletes the half-written preview and sends the answer
  normally. An answer arriving twice, once truncated, is worse than arriving
  once.
- A turn that fails part-way clears both the activity and the preview, so
  nobody is left looking at half an answer that will never be finished.
- A broken stream raises after the deltas already shown and is not retried:
  retrying would repeat text the person has already read.

## Checks

Offline suite: **697 passed, 1 skipped** (`.venv\Scripts\python.exe -m pytest
tests/ -q`), up from 684 — 13 new tests, none removed.

- **Backend** (`tests/test_openai_compatible.py`): text assembling to the same
  answer; a fragmented tool call assembling into one call; several calls keeping
  their positions when fragments interleave; text and a tool call from one
  response; a missing name and invalid argument JSON refused; separated
  reasoning ignored rather than shown or crashed on; usage-only and `[DONE]`
  chunks handled; `include_usage` actually requested; a stream broken after a
  delta not retried and yielding no completion.
- **Runtime** (`tests/test_answer_streaming.py`, new): deltas joining into the
  message produced; a delta observable *before* the model call finishes — the
  fake refuses to finish until the test has one, so the test deadlocks rather
  than passes if deltas were collected and handed over at the end; the switch
  off still answering; tool calls still entering the loop with the second call
  also streaming; only finished messages stored; usage still reaching `fill()`;
  `steps()` unchanged.
- **Telegram** (`tests/test_telegram_adapter.py`): an answer previewed once and
  finished in the same message with nothing sent after it; a short answer
  arriving whole; a tool step previewing only the answer; a failed final edit
  delivering the answer and deleting the preview; edits throttled to one a
  second rather than one a token, with what is shown always a prefix of what
  has arrived; a preview that cannot be sent standing aside after one attempt.

The scripted fake now streams in pieces that concatenate back to the completion
exactly, so every existing agent and adapter test runs through the streaming
path rather than around it.

Not run: the PostgreSQL contract suite, unchanged by this work and untouched by
it — no schema, store or query changed.

## Files

`app/models/base.py`, `app/models/openai_compatible.py`, `app/models/__init__.py`,
`app/agent/graph.py`, `app/agent/runtime.py`, `app/config.py`,
`ui/telegram/adapter.py`, `ui/telegram/api.py`, `scripts/smoke_test.py`,
`tests/fakes.py`, `tests/test_openai_compatible.py`, `tests/test_model_backend.py`,
`tests/test_context.py`, `tests/test_telegram_adapter.py`,
`tests/test_answer_streaming.py` (new), `docs/CODEMAP.md`,
`docs/OPERATIONS_MAP.md`, `ROADMAP.md`.

`.env.example` was not updated: reading it was refused in this session. The new
setting is documented in `docs/OPERATIONS_MAP.md` and defaults to on, so nothing
depends on the example file being edited first.

`docs/PRODUCT.md` is deliberately unchanged. Its baseline lists accepted product
behaviour, and this has not been accepted live.

## Accepted live, 2026-08-29

Deployed to `assistant-control` in 21.1 s; five functions re-created, the image
rebuilt only on its two `ENV` steps. `AGENT_STREAM_ANSWERS` is unset in the
deployed secret, so streaming is on by its default.

The human ran the acceptance in the real chat and reported it working: the
answer grows in one message, no duplicate arrives, a tool turn still shows its
activity and then streams, and conversation selection is unchanged. That is
their observation, not a reading of the database; the store was not inspected
afterwards, so "only complete canonical messages are stored" rests on the
offline tests rather than on deployed data.

One real failure came out of the same session, in the task route rather than
this work: see below.

## The task route failed on "create a text file"

Live, an `act` request ended at planning with
`planning failed: a validation step requires non-empty capabilities`.

Nothing to do with streaming. `PLAN_RESPONSE_FORMAT` permits
`"capabilities": []` — there is no `minItems` — while `ValidationStep` refuses an
empty list. The model returned a structurally valid plan whose validation step
named no evidence capability, and one blank field ended a task the user had
asked for, showing them an internal message.

Fixed where the two disagree: an empty list now becomes `filesystem.read`, the
evidence floor every sandbox criterion has, instead of a fatal error, and the
planner prompt says each validation item must name at least one capability.
`tests/test_task_worker.py` gained the regression.

`minItems: 1` in the schema would stop the model from producing an empty list at
all, and is deliberately not used: structured decoding on the deployed vLLM has
never been tested with that keyword here, and an unsupported keyword would fail
every planning request rather than one. The parse-level floor fixes the
behaviour without touching the request.

## Gates ahead

The planner fix was deployed the same day: `assistant-control` redeployed in
21.1 s, five functions re-created. It was not exercised afterwards — the human
asked for the deploy without a live check — so it is live and untried.

Never measured here: provider TTFT against first visible preview. The routing
call still runs before the conversational answer, so first-visible-token latency
includes it until the single-call change in queue item 5. That belongs to queue
item 3, which owns measurement.
