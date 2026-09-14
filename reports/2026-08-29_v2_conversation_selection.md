# Telegram conversation selection — implementation and offline evidence

**Date:** 2026-08-29
**Task input:** `reports/archive/telegram_conversation_selection_task.md`
**Roadmap item:** queue 2, before 2B.

## What changed and why

The conversation a Telegram message joined was inferred: `current_thread`
returned the user's most recently updated thread. That made two things
impossible to express. A conversation could take somebody over merely by being
written to, and an older conversation could never be returned to, because
returning to it would have meant making it the newest.

`updated_at` now describes activity only. Which conversation a person is in is a
stored choice.

- `ConversationStore` gained `active_thread(user_id)` and
  `set_active_thread(user_id, thread_id)`. `set_active_thread` raises `KeyError`
  for a thread that does not exist *or* belongs to somebody else — one answer for
  both, because distinguishing them would confirm the existence of a thread the
  caller may not have.
- The ownership test is the `WHERE EXISTS` of the same statement that writes the
  choice, in both implementations, so no other writer can slip between a check
  and its write.
- `active_thread` joins `threads` and returns `None` when the chosen
  conversation is gone, so a caller never receives an identifier that no longer
  resolves.
- `/new` creates and activates a conversation, and reuses the active one when
  nothing has been said in it. Repeated presses otherwise fill the list with
  identical unnamed entries, and the chat would report making something when it
  made nothing; the reused case says so instead.
- `/chats` sends one message with an inline button per conversation — the ten
  most recent, labelled by opening text cut at 40 characters, with `●` on the
  current one — and a Close button. A press writes the choice and re-marks the
  same message. Selection does not touch `updated_at`, so the list does not
  reorder under the person reading it.
- A user who has threads but no recorded choice — anyone who was here before
  this existed — is given their most recent thread rather than an empty one
  beside it.

## Cost boundary

`/chats` and its callbacks are answered from storage. `MODEL_FREE_COMMANDS` gained
`/chats`, and `needs_model` now treats the `chats:` callback prefix as model-free
alongside `settled:`. This is the value the deployed webhook reads to decide
whether to wake the GPU, so browsing conversations cannot start the expensive
half of the system. The adapter's callback branch answers a `chats:` press
before any thread is read or created, for the same reason.

## Database

Schema version 2 in both implementations. `user_state` is a new table
(`user_id`, `active_thread_id`, `updated_at`) with
`active_thread_id REFERENCES threads(id) ON DELETE SET NULL`, which is what keeps
a choice from outliving its conversation without a second statement in
`delete_thread`.

The step from 1 to 2 is additive: nothing existing changes shape, so re-running
the schema is the whole migration. **No database was reset**, locally or in the
deployment, and none needed to be — the permission to drop them was not used.
Resetting remains a destructive operation behind the normal human gate, with no
application path to it; `docs/OPERATIONS_MAP.md` records this.

## Checks

- Offline suite: **678 passed, 1 skipped** (`.venv\Scripts\python.exe -m pytest tests/ -q`).
- Store contract suite: 22 passed, SQLite only. Seven of those are new and cover
  the choice: not chosen until chosen, remembered, unmoved by activity
  elsewhere, refused for another user's thread, refused for a thread that does
  not exist, cleared by deleting the thread, and surviving a reopen.
- Telegram adapter suite: 78 passed, including the list showing only this user's
  conversations newest-first with the current one marked, a long label cut
  rather than squeezed, a press sending the *next* message to the chosen
  conversation, a press producing exactly `answerCallbackQuery` +
  `editMessageReplyMarkup` with a backend that raises on any model call, Close
  removing the buttons, a second person's press being refused with nothing
  written, and `/new` reusing an untouched conversation.
- Bot profile preview (no network): the menu reads `/new`, `/chats`, `/can`,
  `/stop`, `/help`.

## Deployment, authorized 2026-08-29

Run in this order, because a menu entry published before the code is deployed
would offer a command the running bot answers by handing it to the model.

1. **PostgreSQL contract**, against `AGENT_TEST_DATABASE_URL`: **42 passed** in
   119 s — the same 22 SQLite cases plus 20 on a real database. Run before the
   deploy rather than after it: an untried SQL statement in the store is a
   failure of every message, not of one feature.
2. **Migration** of the deployed database, `tools/setup_control_plane.py`.
   Afterwards the deployed schema reports version **2**, `user_state` exists, and
   the **5 existing conversations are still there** — the additive migration
   proven on the database it matters for, not only in a test schema.
3. **Deploy** of `assistant-control`: 20.4 s, no image rebuild beyond the two
   `ENV` steps. `telegram_webhook`, `process_telegram_update`, `render_web_page`,
   `self_test` and `measure_database_latency` all re-created.
4. **Bot profile published**: the native menu now reads `/new`, `/chats`, `/can`,
   `/stop`, `/help`.

No deployed function was called: `self_test` and any live turn start a worker,
which is its own gate.

## Accepted live, 2026-08-29

The human switched conversation in the real chat and sent a message. Read out of
the deployed database afterwards — timings, positions and counts only, no message
text:

```text
user_state: one row
  user d992be3b -> thread 37b95a2d, chosen 2026-08-29T04:53:51+00:00

before the test    37b95a2d  last activity 2026-08-28T20:51:10   36 messages
                   05c36e0c  last activity 2026-08-29T03:47:58   12 messages  <- newest
after the test     37b95a2d  positions 36-37 written at 04:54:40
```

The conversation chosen was almost seven hours behind the newest one. Under the
rule this replaced, the message would have gone to `05c36e0c`; it went where the
person put it. Three further readings from the same query:

- the `user_state` row is stamped at the moment of the press, not the moment of
  the message, so the choice went through PostgreSQL rather than living in a
  container that disappears between turns;
- the thread count did not change, so listing and pressing created nothing;
- no empty thread exists, so `/new` left no unnamed entries behind.

Not covered: the rendered list itself was not observed here, only its effect —
the labels and the `●` marker were seen by the human in the chat.

## Files

`app/memory/base.py`, `app/memory/store.py`, `app/memory/postgres.py`,
`ui/telegram/adapter.py`, `ui/telegram/api.py`, `ui/telegram/wire.py`,
`tests/test_store_contract.py`, `tests/test_telegram_adapter.py`,
`docs/PRODUCT.md`, `docs/CODEMAP.md`, `docs/OPERATIONS_MAP.md`, `ROADMAP.md`.
