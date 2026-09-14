# Task — Telegram conversation selection

## Goal

Add minimal conversation/session management to Telegram so a user can:

- start a new conversation;
- see recent conversations;
- switch back to an older conversation;
- continue chatting in the explicitly selected conversation.

This is a small product/UI task, not a general session-management system.

## Current problem

Today `/new` creates a new durable thread, but Telegram has no way to list or reopen older threads.

The current thread is inferred as the most recently updated thread. Replace that with explicit user selection.

`updated_at` should describe activity ordering.  
`active_thread_id` should describe the user's current choice.

## Desired Telegram UX

Add:

```text
/chats — Switch conversation
```

`/chats` shows recent conversations using an inline keyboard.

Example:

```text
Conversations

[ ● Telegram UX baseline ]
[   Orca / Claude bridge ]
[   PDF generation issue ]
[   Architecture refs ]

[ Close ]
```

For now, use the thread's existing `opening` text as the label, truncated to a reasonable Telegram-friendly length.

Do not add model-generated titles in this task.

When a conversation is selected:

- persist it as the user's active thread;
- update the same Telegram message to show the selected state;
- the next normal message must use that thread.

`/new` must create a thread and immediately set it as active.

## Persistence

Add explicit active-conversation state, conceptually:

```text
user_state
---------
user_id
active_thread_id
```

Preferred store contract:

```python
active_thread(user_id: str) -> str | None
set_active_thread(user_id: str, thread_id: str) -> None
```

The store must verify that the selected thread belongs to that user.

If no active thread exists, create one and make it active.

## Database handling

There is no production data that needs preserving.

Do **not** spend time building a backward-compatible migration path for the current schema.

Instead:

- bump the schema version;
- define the new schema cleanly;
- recreate/reset the disposable local and deployed database/schema as needed;
- document the reset as an explicit destructive operation requiring the normal human gate.

Do not silently reset deployed storage from application startup.

## Likely owners

- `app/memory/base.py` — store contract / active-thread operations
- `app/memory/store.py` — SQLite implementation/schema
- `app/memory/postgres.py` — PostgreSQL implementation/schema
- `ui/telegram/adapter.py` — `/new`, `/chats`, active-thread lookup, callbacks
- `ui/telegram/api.py` — inline keyboard presentation primitives if needed
- `ui/telegram/wire.py` — only if a new callback prefix must be classified at the raw update layer

Use existing owners. Do not create a separate session service.

## Explicit non-goals

Do not add:

- rename conversation;
- model-generated titles;
- archive;
- pinning;
- folders;
- conversation search;
- delete UI;
- complex pagination;
- cross-platform session manager;
- conversation serialization/locking;
- coalescing of Telegram updates.

Conversation serialization remains a separate roadmap correctness item.

## Acceptance criteria

- `/new` creates and activates a new thread.
- `/chats` lists recent threads for the current user only.
- labels come from existing thread metadata/opening text.
- selecting a thread persists the choice.
- the next user message is appended to the selected thread.
- activity in another thread cannot implicitly steal the active selection merely by changing `updated_at`.
- users cannot select another user's thread.
- SQLite and PostgreSQL satisfy the same store contract.
- existing conversation and memory behavior remains intact.
- no model call is required to list or switch conversations.
- no GPU wake is required for `/chats` or its selection callbacks.

## Desired result

```text
/new
  → create + activate

/chats
  → list recent conversations
  → select one
  → persist active_thread_id

normal message
  → use explicit active thread
```

Keep the implementation small and explicit.
