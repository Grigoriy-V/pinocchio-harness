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
  `stops`. Version 1 takes text messages only; a message with an attachment
  stays queued for its own turn (its file download belongs to a turn of its
  own, and a failed download must not lose the message).
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
- Version 1 takes text only; attachments wait for their own turn.
