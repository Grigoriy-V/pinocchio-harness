"""Telegram's wire format, reduced — and nothing else.

This is separate from `adapter.py` for one measured reason. The deployed
webhook only has to recognise an update and read the sender out of it, but it
imported this function from the adapter, and the adapter's first lines pull in
the harness, the agent runtime and through them LangGraph. Two thirds of the
webhook's import time was spent loading code it never executes.

So the rule for this module is its whole purpose: **it may import from the
standard library and nothing else.** Anything that needs a store, a model, a
setting or a client belongs in `adapter.py`, which imports from here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "local-multimodal-agent:telegram")

PHOTO_MEDIA_TYPE = "image/jpeg"
VOICE_MEDIA_TYPE = "audio/ogg"

# An inline action that has already been carried out leaves one status button
# behind. Telegram needs callback data for a button to exist at all, so the
# settled state gets its own prefix — and the prefix is what tells the front
# door that pressing it is a no-op. Recognising it here rather than in the
# adapter is what keeps a curious tap from waking a GPU.
SETTLED_CALLBACK_PREFIX = "settled:"
SETTLED_APPROVED = f"{SETTLED_CALLBACK_PREFIX}approved"
SETTLED_REJECTED = f"{SETTLED_CALLBACK_PREFIX}rejected"

# Choosing which conversation to be in. The rest of the data is a thread id,
# which is a UUID and fits Telegram's 64-byte callback limit with room to spare.
CHATS_CALLBACK_PREFIX = "chats:"
CHATS_CLOSE = f"{CHATS_CALLBACK_PREFIX}close"

# The commands the adapter answers from wiring and storage, without a model.
# Listing the exceptions rather than the rule is the safe direction: anything
# new needs the model until someone says otherwise, and the cost of being wrong
# is asymmetric. A model-free update misjudged as needing one wastes a GPU wake;
# a model-using update misjudged as free simply waits for the wake it would have
# waited for anyway. The second is today's behaviour, so it is the failure to
# prefer.
#
# `tests/test_telegram_adapter.py` answers each of these with a backend that
# raises on any call, so this list cannot quietly stop being true.
MODEL_FREE_COMMANDS = frozenset(
    {"/start", "/help", "/new", "/chats", "/can", "/check", "/stop", "/plan", "/context"}
)

# Commands that carry their own text and still never reach the model. `/agents`
# writes a file and answers from it; matching it only when it stands alone
# would wake an A10 to save a sentence.
#
# Both spellings, because the singular is what a person types. On 2026-08-30
# `/agent set …` fell through to the model, which answered as if it were being
# chatted to, and the instructions were silently never saved. A near miss on a
# command that writes a file has to hit the command, not the GPU.
INSTRUCTION_COMMANDS = frozenset({"/agents", "/agent"})
# `/plan` answers one line: its switch is gone (2026-09-17).
PLAN_COMMANDS = frozenset({"/plan"})
# `/mode full`, `/mode careful` and `/mode plan` write a marker file and answer from it.
MODE_COMMANDS = frozenset({"/mode"})
# `/context` reports from the store and an estimate; `/context small` writes a
# marker. `/compact` is deliberately not here: it calls the summarizer, so the
# GPU may as well start waking at the front door.
CONTEXT_COMMANDS = frozenset({"/context"})
MODEL_FREE_WITH_ARGUMENTS = tuple(
    sorted(INSTRUCTION_COMMANDS | PLAN_COMMANDS | MODE_COMMANDS | CONTEXT_COMMANDS)
)

# Buttons that are answered from storage. A settled status button describes
# something that already happened; a conversation button changes which thread
# the next message goes to. Neither reads a model, and neither may be paid for
# with a GPU wake — a person browsing their own conversations would otherwise
# start the expensive half of the system with every tap.
MODEL_FREE_CALLBACK_PREFIXES = (SETTLED_CALLBACK_PREFIX, CHATS_CALLBACK_PREFIX)


@dataclass(frozen=True)
class Incoming:
    """One Telegram update, reduced to what the application needs."""

    chat_id: int
    telegram_user_id: int
    text: str
    files: tuple[tuple[str, str, str], ...] = ()  # (file_id, name, media_type)
    # Telegram's own number for this update, which only grows. It is what a
    # stop is compared against: a stop that arrived after this update ends the
    # turn it started, and one that arrived before it belongs to an older turn.
    update_id: int = 0
    callback_id: str | None = None
    callback_data: str | None = None
    # The message the pressed button belongs to. Carried because settling an
    # inline action means editing that same message's keyboard, and without the
    # id the adapter would have to send a second message to say what happened.
    callback_message_id: int | None = None


def read_update(update: dict[str, Any]) -> Incoming | None:
    """Reduce a raw update, or return `None` for one this adapter ignores."""

    sequence = update.get("update_id")
    sequence = int(sequence) if isinstance(sequence, int) else 0
    callback = update.get("callback_query")
    if callback:
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        sender = callback.get("from") or {}
        if not chat.get("id") or not sender.get("id"):
            return None
        message_id = message.get("message_id")
        return Incoming(
            chat_id=int(chat["id"]),
            telegram_user_id=int(sender["id"]),
            text="",
            callback_id=str(callback.get("id", "")),
            callback_data=str(callback.get("data", "")),
            callback_message_id=int(message_id) if message_id is not None else None,
            update_id=sequence,
        )

    message = update.get("message")
    if not message:
        return None
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    if not chat.get("id") or not sender.get("id"):
        return None

    files: list[tuple[str, str, str]] = []
    photos = message.get("photo") or []
    if photos:
        # Telegram sends every rendered size; the last is the largest.
        largest = photos[-1]
        files.append((str(largest["file_id"]), "photo.jpg", PHOTO_MEDIA_TYPE))
    voice = message.get("voice")
    if voice:
        files.append(
            (str(voice["file_id"]), "voice.ogg", str(voice.get("mime_type") or VOICE_MEDIA_TYPE))
        )
    audio = message.get("audio")
    if audio:
        files.append(
            (
                str(audio["file_id"]),
                str(audio.get("file_name") or "audio"),
                str(audio.get("mime_type") or ""),
            )
        )
    document = message.get("document")
    if document:
        files.append(
            (
                str(document["file_id"]),
                str(document.get("file_name") or "document"),
                str(document.get("mime_type") or ""),
            )
        )

    return Incoming(
        chat_id=int(chat["id"]),
        telegram_user_id=int(sender["id"]),
        text=str(message.get("text") or message.get("caption") or ""),
        files=tuple(files),
        update_id=sequence,
    )


def canonical_user_id(telegram_user_id: int) -> str:
    """The application's own identifier for a Telegram account."""

    return str(uuid.uuid5(NAMESPACE, f"user:{telegram_user_id}"))


def conversation_key(incoming: Incoming) -> str:
    """What a turn must not run concurrently with.

    The person, not the thread. Which conversation a message lands in is the
    worker's own first read — `current_thread` consults the store — so the front
    door cannot know it without a query it exists to avoid. A person is in
    exactly one conversation at a time, so serializing their updates serializes
    that conversation, and it also closes the check-then-act in `current_thread`
    that let two workers meeting a new user create two threads for them.
    """

    return canonical_user_id(incoming.telegram_user_id)


def is_cancellation(incoming: Incoming) -> bool:
    """Is this the one model-free command that ends work already running?

    It costs no model call, so it is not `needs_model`, but it is the only free
    command that changes what an expensive turn does — which is why it is worth
    a measured turn of its own while `/chats` and `/can` are not.
    """

    return incoming.callback_data is None and incoming.text.strip().lower() == "/stop"


def travels_out_of_band(incoming: Incoming) -> bool:
    """Must this update skip the queue that serializes a conversation?

    Serializing a conversation is what stops two messages being answered out of
    order, and it is exactly wrong for the updates that exist to act on what is
    already running or beside it. `/stop` behind the turn it exists to stop
    arrives to find nothing running; `/chats` behind a five-minute task is a
    list of conversations that takes five minutes to open.

    The rule is the one already drawn: an update that never reaches the model
    is answered from wiring or storage in milliseconds, changes no conversation
    history, and has nothing to be ordered against. Those go out of band, and
    everything else keeps its place in the line.
    """

    return not needs_model(incoming)


def needs_model(incoming: Incoming) -> bool:
    """Will answering this update reach the model?

    Asked at the front door so the GPU can start waking while the worker is
    still being scheduled. Those two took about 5.5 s and 4.9 s one after the
    other; overlapped, the second disappears into the first.

    The comparison matches the adapter's own dispatch exactly: a bare command
    from the set above, or one of the few that take an argument and are still
    answered without a model. Anything else falls through to the model there,
    and must be judged that way here.
    """

    if incoming.callback_data is not None:
        # An approval button resumes a task, and resuming one calls the model.
        # The prefixes above are the exceptions, and they are exceptions the
        # adapter answers entirely from storage.
        return not incoming.callback_data.startswith(MODEL_FREE_CALLBACK_PREFIXES)
    text = incoming.text.strip().lower()
    if text in MODEL_FREE_COMMANDS:
        return False
    return text.partition(" ")[0] not in MODEL_FREE_WITH_ARGUMENTS
