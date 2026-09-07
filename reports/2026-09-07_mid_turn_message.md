# Item 20: a message in the middle of a turn — research before code

2026-09-07. Read from the tree only; no paid call, no worker started.

## 1. What a message sent during a turn does today

**Deployed (webhook).** `TelegramWebhook._admit` writes every update to the
inbox with its `conversation_key`; an ordinary message arriving while a live
lease holds the conversation is queued and **not** spawned (`_busy`). The
worker that holds the lease drains it after its turn (`claim_next`, within
`drain_seconds`), so the message is answered as the next turn, in order.
A control update (`travels_out_of_band`: `/stop`, `/chats` and the other
model-free commands, `ui/telegram/wire.py:203`) is marked `control`, is
claimed on its own by its own worker, and never waits for the conversation.
`/stop` is then `stops.request(user_id, update_id)`, one row per person in
`turn_stops` (`app/agent/stop.py`), and the running turn reads it in
`run_tools` before a batch (`asked_to_stop`, `app/agent/graph.py:845`).

**Local (polling).** `ui/telegram/run.py` holds one `asyncio.Lock` per chat;
a control update goes past the lock, an ordinary message waits on it and is
answered after the turn. Same outcome, one process.

**Chainlit.** `on_message` runs the turn in the handler; the stop button is
`stops.request` with the session counter. Whether Chainlit runs a second
`on_message` for the same session while the first is still running was not
verified here; the local profile is not where the human uses this.

So the lane the human named exists and is exactly the stop's: a number per
event, a row a running turn can read, a step boundary that reads it. What is
missing is (a) a way for a running turn to *take* the person's words rather
than a flag, (b) a place in the loop where they enter the turn's messages,
(c) an interface that does not echo them back.

## 2. Where the message enters the loop

The step boundary is the end of `run_tools`, where the health check already
rides (`app/agent/graph.py:961`): the batch's results are known, the next
thing is a model call, and a user message placed after the results is what
the model reads next. One read per batch, like `asked_to_stop`.

Why not the model node: a message arriving during a model call cannot change
that call; it is read after the batch the call asked for. A message arriving
during the *last* model call (an answer, no tools) is not taken at all: the
turn goes to `persist` and the message is answered as the next turn, in
order, which is what happens today. Nothing is lost, only delayed by one
batch at most.

Three facts of the code the placement has to respect:

- **The `extend` reducer** (`app/agent/graph.py:80`): a patch whose *first*
  message is a user message starts a new turn and drops the turn so far.
  The taken message must therefore ride in the tools patch *after* the
  results (`[*results, taken]`), never as a patch of its own. The comment
  in the reducer gets a line saying so.
- **`verbatim_floor` and `cut_for`** (`app/context/summary.py:94,119`) treat
  every user message as an exchange boundary. A taken message counts as one
  of `keep_turns`, and a fold may cut right before it inside a turn. Both
  are acceptable: a cut before a user message is a legal cut, and the
  results ahead of it remain paired (`window.py:219`). Recorded, not
  changed.
- **`_run`** (`app/agent/runtime.py:450`) yields every patch message as
  `MessageProduced`, and `_deliver` sends any non-tool message with text.
  A taken user message would be echoed to the chat. `_run` yields it as
  its own event (`MessageTaken`), which interfaces may show or ignore.

`latest_text` (retrieval query) becomes the taken message's text for the
following steps; `same_request` and `already_stored` read the first user
message and are unchanged; `state.sequence` stays the turn's own, so a
`/stop` sent after the taken message still stops the turn.

## 3. How the model sees it

As the person's words: `role: user`, the text verbatim, no frame (the human,
item 20: "as the person's words"; a coding agent's chat does exactly this).
The model decides: answer it, fold it into the work, or ignore it and go on.
The core prompt gets one literal line so a cheap model does not treat it as
a new request that ends the current one: "A user message that arrives after
tool results is a comment on the work in progress. Read it, then continue
the current task unless it says to stop or change it."

A framed variant ("Message from the user while you were working: …") is the
alternative; it is a label the store would keep. Not chosen, because the
stored thread should read as the conversation was.

## 4. The lane, per profile

One protocol beside `StopRequests`, in `app/agent/`:

```
class Interjections(Protocol):
    async def take(self, key: str, since: int) -> list[Message]:
        """Every message of this person offered after `since`, taken
        atomically: a message taken here is answered by the running turn
        and by nothing else."""
```

- **Deployed:** the inbox is the lane; nothing new is written at the front
  door. `take` is one statement: `UPDATE inbox SET state='done' WHERE
  conversation_key=%s AND state='pending' AND NOT control AND update_id >
  %s RETURNING payload ORDER BY update_id`. `pending → done` in one
  statement is the atomicity: the drain loop's `claim_next` cannot take a
  done row, and a row a later worker already claimed (lease expired after a
  death) is `running` and not taken. The implementation lives in
  `ui/telegram/` because turning a payload into a `Message` is
  `adapter.to_message`; it is built by the adapter and injected like
  `stops`. Attachments are taken too (the human, 2026-09-07: a screenshot
  with an empty caption is nothing without its file): the worker holding the
  turn has the same client and workspace as a fresh turn would. Order: take
  the row, download, and on a failed download put the row back to `pending`,
  so the message gets its own turn and the usual "Upload refused" answer
  instead of being lost.
- **Local polling:** a memory lane. The front door offers an ordinary
  message *before* waiting on the chat lock; the turn takes it at a
  boundary; the handler, once it has the lock, asks whether its message was
  taken and returns without answering if so. Same contract, one process.
- **Chainlit:** the same memory lane if Chainlit runs handlers
  concurrently; verified when built, otherwise left as today.
- **Tests and one-shot runs:** `NO_INTERJECTIONS`, a null object, as
  `NO_STOPS`.

The trace records `turn_interjected` (step, count) and the inspector shows
"the person wrote while it worked: …". Telemetry: the taken message's own
`run_id` (the inbox row's) ends as `merged` into the running turn's, so the
reliability figures do not count it as a turn that never answered.

## 5. What to build, in order, and how it is checked

1. `Interjections`, `NO_INTERJECTIONS`, `MemoryInterjections`
   (`app/agent/interjections.py`); `Agent(interjections=…)`; the read at the
   end of `run_tools` after the health check; `MessageTaken` in `_run`; the
   prompt line. Offline tests in `tests/test_turn_stopping.py` or a new
   `tests/test_interjections.py`: a message offered during a batch is in the
   next request after the results and in the store in that position; one
   offered before the turn's sequence is not taken; one offered during the
   final answer is not taken; the interface receives `MessageTaken`, never
   `MessageProduced`, for it; a stop after it still stops.
2. `PostgresInterjections` over the inbox table (`ui/telegram/interjections.py`
   or inside `inbox.py`); the adapter builds and injects it. Contract test
   beside `tests/test_update_inbox_contract.py`, live Postgres only
   (`AGENT_TEST_DATABASE_URL`): taken rows are done, a control row and a
   running row are not taken, the drain loop skips the taken row.
3. Polling front door: offer before the lock, skip after it. Offline test in
   `tests/test_telegram_polling*.py`.
4. Deploy `assistant-control`; one live turn: a long G-like task, one text
   message sent while it runs, check the trace (`turn_interjected`), the
   model's reaction, the store order, and that the message was not answered
   twice. One paid turn; the human names the size.

Records after: `DECISIONS.md` one entry (a message during a turn is taken at
the step boundary as the person's words; the inbox is the lane); the four
maps: `PROJECT_MAP` (lanes), `CODEMAP` (owner), `OPERATIONS_MAP` (nothing
new to configure). ISS: none observed yet.

## 6. Open, for the human

- The wording of the prompt line (§3), and whether the message goes in
  unframed (chosen) or framed.
- Whether Telegram acknowledges a taken message (a reaction emoji on it via
  `setMessageReaction`, cheap, silent) or nothing until the model speaks.
  Proposed: nothing in version 1; the model's next text is the answer.
- Attachments: taken with the text (decided 2026-09-07).

## 7. How the references do it (read 2026-09-07, after the human asked)

| | Claude Code | Codex CLI | Pi (pi-mono) | OpenHands SDK |
|---|---|---|---|---|
| Delivered when | "as soon as those tool calls finish, within the same turn" | before the next model request, after the current sampling and its tools finish (`can_drain_pending_input` is false during sampling) | "after the current assistant turn finishes executing its tool calls" | only after the turn (queueing proposed, issue #333) |
| Form | a user message | a user message (`ResponseItem`, role user), no wrapper | `{ role: "user", content }`, "no text frame or prefix" | — |
| Tool batch cut? | no; `Esc` is the separate interrupt, which then sends the queued text at once | no; "no preempt after reasoning" is tested; `Esc` interrupts and sends immediately | no; `Escape` aborts and puts the queued text back in the editor | — |
| More than one queued | one at a time: at the turn's end "only the oldest as the next turn", the rest wait for that turn's next tool boundary | all pending input is drained together; what is left when the turn ends starts the next turn | `steeringMode` `"one-at-a-time"` (default) or `"all"` | FIFO after the turn |
| Two lanes | messages mid-turn; slash and shell commands held to the turn's end | `turn/steer` RPC with `expectedTurnId` (fails unless it names the active turn); review and compact turns refuse steering | `Enter` steers, `Alt+Enter` is a follow-up delivered only when all work is done | — |
| Told to the model? | nothing added to the prompt | nothing | nothing | — |

Sources: Claude Code interactive-mode docs ("Queue messages while Claude
works"); `openai/codex` `codex-rs/core/src/session/turn.rs`,
`session/input_queue.rs`, `tests/suite/pending_input.rs`,
`app-server-protocol/.../TurnSteerParams.ts`; `badlogic/pi-mono`
`packages/agent/src/agent-loop.ts`, `packages/coding-agent/README.md`,
`src/core/agent-session.ts`; `OpenHands/software-agent-sdk` issue 333;
`tiann/hapi` issue 888 (a Telegram-style front end that delivers to both
CLIs at the step boundary).

What this changes in §2–§6:

- **The boundary and the form are confirmed.** All three deliver after the
  tool batch, before the next model request, as a plain user message, and
  none cuts the batch; the interrupt is a separate key, which is our
  `/stop`. Codex's `expectedTurnId` is our `sequence`: a message is taken
  only by the turn that was running when it arrived.
- **No prompt line (§3 revised).** None of the three tells the model how to
  treat the message; the model reads a user message in the middle of its
  work and decides. The line proposed in §3 is dropped from version 1. If
  the live check shows GLM dropping the task on a comment, that is a model
  behaviour measured with the suite (item 15), and the line is the smallest
  answer then; a stronger frame would put a label into the stored thread.
- **All at once, not one at a time (new).** Claude Code and Pi default to one
  queued message per boundary; Codex drains all. In Telegram a person's
  thought often arrives as two or three short messages seconds apart; one at
  a time would answer them across three model steps. `take` returns every
  pending message in order and they enter as consecutive user messages.
  Recorded as the chosen default; Pi's `"all"` is the same choice.
- **What is left when the turn ends starts the next turn** in all three; the
  inbox already does that, nothing to add.
- **A turn that is not a turn is not steerable** (Codex: review, compact).
  Ours: `/compact` and the model-free commands are not turns, and a taken
  message can only enter a running graph; nothing to add.
- **Taking back a queued message** (Claude Code `Up`, Pi `Alt+Up`, Codex
  `Esc`) has no equivalent in a chat front end: a sent Telegram message is
  sent. Not built.

## 8. Built (2026-09-07), awaiting the deploy

Commits cf44925 (step 1) and 29fbcf1 (steps 2–3):

- `app/agent/interjections.py`: `Interjections`, `NO_INTERJECTIONS`,
  `MemoryInterjections` (`offer` / `take` / `settle`). `build_agent`, `Agent`
  and `create_agent` take `interjections`; `run_tools` takes after the health
  check and appends after the batch's results (`turn_interjected` in the
  trace, one inspector line). `_run` yields a taken user message as
  `MessageTaken`; the Telegram adapter skips it in both event loops.
- `ui/telegram/interjections.py`: `InboxInterjections` over
  `PostgresUpdateInbox.take_pending` (one `UPDATE … RETURNING`, `pending` to
  `done`, `last_error = "taken by the running turn"`, messages only, never a
  control row) and `release` (back to `pending` for a row whose file could
  not be read or which was a button press). The adapter builds it when given
  the inbox (`control_app.process_telegram_update` passes it) and the memory
  lane otherwise; the polling door `offer`s before the chat lock and asks
  `taken` after it.
- Not in the prompt: nothing, as the references (§7).
- Tests: `tests/test_interjections.py` (8), `tests/test_inbox_interjections.py`
  (3), two polling tests, four inbox contract tests (live Postgres only, not
  run here: `AGENT_TEST_DATABASE_URL` is unset on this machine). Suite: 1159
  passed, 31 skipped.
- Chainlit is unchanged: the memory lane is there but nothing offers to it;
  whether Chainlit runs a second `on_message` during a turn is still
  unverified.

The live check to run after the deploy: one G-like turn with several tool
batches, one text message and one screenshot sent while it runs; expected in
the trace `turn_interjected`, in the store the two user messages between the
batches, in the chat no echo and no second answer; the inbox rows `done` with
the marker.
