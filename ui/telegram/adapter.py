"""Telegram in front of the agent.

This file adapts between Telegram's world and the project's own: an update
becomes a `Message`, what the loop produces becomes chat messages, and a
consent question becomes two buttons. It holds no logic about tools, memory or
stopping — that lives in `app/`, which is why a second interface can exist
without moving any of it.

Two properties here are load-bearing for the deployed profile.

*Identity is mapped, never adopted.* A Telegram chat or user id is an input to
the derivation below, not the canonical identifier. Storing Telegram's own ids
as thread ids would mean a second interface could never address the same
conversation, and that is expensive to undo later rather than now.

*Accepting an update is separate from answering it.* `handle_update` is a plain
coroutine over one update; nothing in it assumes a caller that can be kept
waiting. Local polling drives it in `run.py`; a deployed webhook will hand the
same call to a spawned worker and answer Telegram immediately.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from app.agent.interjections import Interjections, MemoryInterjections
from app.agent.runtime import (
    Agent,
    AnswerWithdrawn,
    AssistantDelta,
    MessageTaken,
    create_agent,
)
from app.agent.mode import MODES, current_mode, set_mode
from app.agent.todo import PLAN_SWITCH, planning_enabled, set_planning
from app.context.choice import CONTEXT_CHOICE, SIZES, set_context_choice
from app.agent.stop import MemoryStopRequests, PostgresStopRequests, StopRequests
from app.attachments import AttachmentBytes, AttachmentError, admit_uploads
from app.instructions import (
    INSTRUCTIONS_FILE,
    InstructionsError,
    clear_instructions,
    read_instructions,
    write_instructions,
)
from app.capabilities import Delivery
from app.config import AgentSettings, TelegramSettings
from app.memory import ConversationStore, Thread
from app.tools import Runner
from app.models import ContentPart, Message
from app.telemetry import NO_TRACE, Telemetry, TurnTrace
from ui.telegram.interjections import InboxInterjections
from ui.telegram.api import (
    MAX_PHOTO_BYTES,
    PRODUCT_COMMANDS,
    Formatted,
    TelegramClient,
    TelegramError,
    approval_keyboard,
    conversations_keyboard,
    no_keyboard,
    settled_keyboard,
)
from ui.telegram.wire import (
    CHATS_CALLBACK_PREFIX,
    CHATS_CLOSE,
    CONTEXT_COMMANDS,
    INSTRUCTION_COMMANDS,
    MODE_COMMANDS,
    PLAN_COMMANDS,
    SETTLED_CALLBACK_PREFIX,
    Incoming,
    canonical_user_id,
    needs_model,
    read_update,
    travels_out_of_band,
)

REFUSAL = (
    "This assistant is private and your account is not on its allowed list. "
    "Nothing you send here is processed."
)
# What `/start` and `/help` say. One text for both: the first question a person
# has and the one they come back with are the same question, and a second
# wording is a second thing to keep true. Written as ordinary Markdown and put
# through the same renderer as an assistant answer, so this card is proof the
# formatting path works rather than a special case beside it.
#
# The command lines are generated from the native menu so the two cannot
# disagree. `/check` is named afterwards, in a sentence, because it is a
# diagnostic rather than one of the four things this assistant is for.
HELP_MARKDOWN = "\n".join(
    [
        "**Personal assistant**",
        "",
        "Talk to me normally. You can send text, images, voice messages and "
        "supported documents. I can read your files, use the web, and carry out "
        "longer tasks when they are needed. I ask before consequential actions.",
        "",
        "**Commands**",
        # Bold rather than code: Telegram turns a `/command` in message text
        # into something tappable, and monospace is the one style that reads as
        # a thing to copy rather than a thing to press.
        *(f"**/{entry.command}** — {entry.description}" for entry in PRODUCT_COMMANDS),
        "",
        "**/check** tries every capability for real and reports what actually works.",
    ]
)
HELP = Formatted.from_markdown(HELP_MARKDOWN)

# Telegram's own limit on one album.
ALBUM_LIMIT = 10

# What `_send_media` can actually put in this chat, declared where that method
# is, so the two cannot drift apart. Images go as photos and sound as a file,
# but both arrive, and the model is told so instead of guessing.
DELIVERY = Delivery(media=("image", "audio"), place="Telegram")

# Telegram names an upload by its filename, so an outgoing part needs a
# plausible extension. Only the types the assistant can produce are listed.
MEDIA_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
}


# What the person sees while a tool runs. Internal names are how the agent and
# the traces talk about a tool; they are not what a product says out loud, and
# `send_file` in a chat reads as a leaked implementation detail rather than as
# progress. Deliberately English whatever language the conversation is in: this
# is interface chrome, not part of the answer, and translating it would mean
# guessing a language from presentation code.
#
# Anything not listed says `Working…` — a new tool must be able to appear
# without leaking its name, so the default is the safe one rather than the
# informative one.
TOOL_ACTIVITY = {
    "search_web": "Searching the web…",
    "fetch_page": "Reading page…",
    "view_web_page": "Opening page…",
    "use_page": "Using the page…",
    "read_document": "Reading document…",
    "view_pages": "Inspecting document…",
    "list_files": "Listing files…",
    "read_file": "Reading file…",
    "write_file": "Writing file…",
    "edit_file": "Editing file…",
    "send_file": "Sending file…",
    "run_command": "Running a command…",
    "todo_write": "Planning…",
    "set_goal": "Noting what you asked for…",
    "remember_fact": "Saving to memory…",
    "search_memory": "Searching memory…",
    "search_history": "Searching the conversation…",
    "read_history": "Reading back…",
}
UNKNOWN_ACTIVITY = "Working…"


def activity_labels(calls: Iterable[Any]) -> list[str]:
    """The user-facing labels for one batch of tool calls, in order, deduped."""

    labels: list[str] = []
    for call in calls:
        label = TOOL_ACTIVITY.get(call.name, UNKNOWN_ACTIVITY)
        if label not in labels:
            labels.append(label)
    return labels


# A status per item, in one column, so the eye finds the current step without
# reading. Deliberately characters and not emoji: this sits under a line of
# interface chrome and should read as a list, not decorate one.
PLAN_MARKS = {"completed": "✓", "in_progress": "▸", "pending": "·"}
PLAN_MARK = "·"
# The plan is the model's own text and can be as long as the model made it.
# Telegram refuses a message over 4096 characters, and losing the status line
# because the plan below it was long would trade the useful part for the
# decorative one.
PLAN_ITEMS = 12
PLAN_ITEM_CHARS = 80


def plan_lines(calls: Iterable[Any]) -> list[str] | None:
    """The plan a batch of calls carries, as lines the person can read.

    Read straight out of the call's arguments, which is where the plan lives —
    the tool stores nothing. `None` means this batch said nothing about the
    plan, which is not the same as an empty plan and must leave what is already
    shown alone.

    Arguments are model output and are treated as such: anything that is not
    the shape this expects is skipped rather than trusted or repaired.
    """

    plan: list[str] | None = None
    for call in calls:
        if call.name != "todo_write":
            continue
        items = getattr(call, "arguments", None)
        items = items.get("todos") if isinstance(items, dict) else None
        if not isinstance(items, list):
            continue
        plan = []
        for item in items[:PLAN_ITEMS]:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            content = content.strip()
            if len(content) > PLAN_ITEM_CHARS:
                content = content[: PLAN_ITEM_CHARS - 1].rstrip() + "…"
            mark = PLAN_MARKS.get(item.get("status"), PLAN_MARK)
            plan.append(f"{mark} {content}")
        if len(items) > PLAN_ITEMS:
            plan.append(f"… {len(items) - PLAN_ITEMS} more")
    return plan


class ToolActivity:
    """One transient status message per turn, edited as the work moves on.

    A message per tool call turned a turn with a browser in it into a column of
    notifications that stayed in the history forever. This is the same
    information in one message that is edited while the tools run and deleted
    when there is an answer to read, so what remains in the chat afterwards is
    the conversation.

    When there is a plan it rides in the same message, under the line saying
    what is happening: one message, current action first, then the plan with a
    status per item. It is the only place the plan is visible at all — it lives
    in the arguments of a tool call otherwise — and it goes when the status
    goes, so the chat afterwards is still just the conversation.

    Nothing here can fail a turn. A status that could not be sent, edited or
    removed is a cosmetic loss, and the answer it was describing is not.
    """

    def __init__(self, client: TelegramClient, chat_id: int) -> None:
        self.client = client
        self.chat_id = chat_id
        self.message_id: int | None = None
        self._shown: str | None = None
        self.step = 0
        self.plan: list[str] = []

    async def show(self, labels: list[str], plan: list[str] | None = None) -> None:
        if not labels:
            return
        # A batch that said nothing about the plan leaves the one already shown
        # standing: the work carries on under a plan that has not changed.
        if plan is not None:
            self.plan = plan
        # One batch of tool calls is one step of the loop, so counting them here
        # is counting the loop. Shown only from the second onwards: a turn that
        # reads one file is not a process a person needs a progress report on,
        # and a turn on its fifth step is.
        self.step += 1
        text = "\n".join(labels)
        if self.step > 1:
            text = f"Step {self.step} · {text}"
        if self.plan:
            text = text + "\n\n" + "\n".join(self.plan)
        if text == self._shown:
            return
        try:
            if self.message_id is None:
                sent = await self.client.send_message(self.chat_id, text)
                self.message_id = int(sent["message_id"]) if sent else None
            else:
                await self.client.edit_message(self.chat_id, self.message_id, text)
        except (TelegramError, KeyError, TypeError, ValueError):
            return
        self._shown = text

    async def clear(self) -> None:
        """Take the status out of the chat, so it cannot sit under the answer."""

        message_id, self.message_id, self._shown = self.message_id, None, None
        if message_id is not None:
            await self.client.delete_message(self.chat_id, message_id)


# One edit per token would be refused by Telegram long before it would be
# readable. A second is roughly how fast an answer can be re-read anyway.
PREVIEW_INTERVAL = 1.0
# Below this, a preview is a bubble containing one word that is about to be
# replaced. The wait costs nothing: the deltas that follow arrive in the same
# breath, and a short answer simply arrives whole.
PREVIEW_START_CHARS = 24


class AnswerPreview:
    """The answer, shown in one message while it is still being written.

    The same message is edited and then finalized, rather than a preview being
    replaced by a fresh bubble: the person watches one answer appear once. What
    is shown is the model's raw text, because Markdown that is half-written is
    not valid markup — the rendering happens at the end, when the text is whole.

    Nothing here can fail a turn. A preview that could not be sent or edited is
    a turn that answers the ordinary way, which is why every failure ends with
    this object standing aside rather than raising.
    """

    def __init__(
        self,
        client: TelegramClient,
        chat_id: int,
        interval: float = PREVIEW_INTERVAL,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.chat_id = chat_id
        self.interval = interval
        self._now = now
        self.text = ""
        self.message_id: int | None = None
        self._shown = ""
        self._edited_at = 0.0
        self._stood_aside = False
        self._held = False

    @property
    def held(self) -> bool:
        return self._held and self.message_id is not None

    def hold(self) -> None:
        """Keep the bubble and its text; start collecting a possible replacement.

        A draft the turn did not accept as its ending is still on the screen
        and is still what the person will get unless the model writes something
        new. Nothing is deleted; the next answer edits this same message.
        """

        self.text = ""
        self._held = True

    async def add(self, delta: str) -> bool:
        """Take one delta. True when this is the moment the preview appeared."""

        self.text += delta
        if self._stood_aside:
            return False
        if self.message_id is None:
            if len(self.text.strip()) < PREVIEW_START_CHARS:
                return False
            return await self._send()
        if self._held and len(self.text.strip()) < PREVIEW_START_CHARS:
            # A replacement is not shown over the draft until it is one.
            return False
        if self._now() - self._edited_at >= self.interval:
            await self._edit()
        return False

    async def _send(self) -> bool:
        try:
            sent = await self.client.send_message(self.chat_id, self.text)
            self.message_id = int(sent["message_id"]) if sent else None
        except (TelegramError, KeyError, TypeError, ValueError):
            self._stood_aside = True
            return False
        self._shown, self._edited_at = self.text, self._now()
        return self.message_id is not None

    async def _edit(self) -> None:
        if self.text == self._shown or self.message_id is None:
            return
        try:
            await self.client.edit_message(self.chat_id, self.message_id, self.text)
        except (TelegramError, ValueError):
            self._stood_aside = True
            return
        self._shown, self._edited_at = self.text, self._now()

    async def finish(self, body: str) -> bool:
        """Make the preview the final answer. False means it must be sent normally.

        A failed final edit takes the half-written preview out of the chat: an
        answer arriving twice, once truncated, is worse than one arriving once.
        """

        message_id = self.message_id
        if message_id is None:
            self.reset()
            return False
        try:
            await self.client.replace_message(
                self.chat_id, message_id, Formatted.from_markdown(body)
            )
        except TelegramError:
            await self.discard()
            return False
        self.reset()
        return True

    async def discard(self) -> None:
        """Take an unfinished preview out of the chat. Never raises."""

        message_id = self.message_id
        self.reset()
        if message_id is not None:
            await self.client.delete_message(self.chat_id, message_id)

    def reset(self) -> None:
        """Forget this answer, so the next model call gets its own preview.

        One turn can produce several assistant messages — text, then a tool,
        then more text — and each is its own message in the chat.
        """

        self.text = self._shown = ""
        self.message_id = None
        self._edited_at = 0.0
        self._stood_aside = False
        self._held = False


def _split_argument(argument: str) -> tuple[str, str]:
    """What `/agents` was asked to do, and the text it was given.

    Split on any whitespace, because `set` and the instructions after it are
    very often on separate lines. And `set` is optional: anything that is not
    `clear` is taken as the instructions themselves.

    That last rule is deliberate. Recognising one exact form and quietly
    showing help for everything else is how a person ends up believing they
    saved instructions that were never written — which is exactly what
    happened on 2026-08-30, with no file on the volume to show for it.
    """

    text = argument.strip()
    if not text:
        return "show", ""
    parts = text.split(maxsplit=1)
    head = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    if head == "clear":
        return "clear", ""
    if head == "set":
        return "set", rest
    return "set", text


def current_thread(store: ConversationStore, user_id: str) -> str:
    """The conversation this user chose, created if they have none yet.

    Choosing is explicit and stored. It used to be inferred — the most recently
    updated thread was the open one — which meant a conversation could take
    somebody over merely by being written to, and made switching back to an
    older one impossible to express.

    A user who has threads but no recorded choice is one who was here before the
    choice existed. Adopting their most recent thread hands them back the
    conversation they were in, rather than opening an empty one beside it.
    """

    chosen = store.active_thread(user_id)
    if chosen is not None:
        return chosen
    existing = store.threads(user_id)
    if existing:
        store.set_active_thread(user_id, existing[0].id)
        return existing[0].id
    return start_thread(store, user_id)


def start_thread(store: ConversationStore, user_id: str) -> str:
    """Create a conversation and move the user into it."""

    thread_id = str(uuid.uuid4())
    store.ensure_thread(thread_id, user_id)
    store.set_active_thread(user_id, thread_id)
    return thread_id


def new_thread(store: ConversationStore, user_id: str) -> str:
    """What `/new` does: a fresh conversation, unless there already is one.

    An untouched conversation is what `/new` produces, so making another one is
    a promise to the person that something happened when nothing did — and it
    fills their list with identical unnamed entries they cannot tell apart.
    """

    chosen = store.active_thread(user_id)
    if chosen is not None and store.message_count(chosen) == 0:
        return chosen
    return start_thread(store, user_id)


# How many conversations `/chats` offers. Deliberately a plain cap rather than
# pagination: this is a personal assistant, the list is ordered by recency, and
# the tenth entry is already further back than anyone reaches for.
CONVERSATION_CHOICES = 10

# Telegram centres button text and squeezes it, so a long label becomes
# unreadable rather than truncated. This is where it is cut instead.
LABEL_CHARS = 40
UNNAMED_CONVERSATION = "New conversation"


def conversation_label(thread: Thread, *, chosen: bool) -> str:
    """One line naming a conversation by how it began.

    Model-written titles would read better and are deliberately not here: they
    would mean a model call to look at a list, and looking at the list must
    never wake anything.
    """

    opening = " ".join(thread.opening.split())
    if len(opening) > LABEL_CHARS:
        opening = opening[: LABEL_CHARS - 1].rstrip() + "…"
    label = opening or UNNAMED_CONVERSATION
    return f"● {label}" if chosen else label


def conversation_choices(
    store: ConversationStore, user_id: str, chosen: str
) -> list[tuple[str, str]]:
    """The buttons for `/chats`: label and callback data, most recent first."""

    return [
        (
            conversation_label(thread, chosen=thread.id == chosen),
            f"{CHATS_CALLBACK_PREFIX}{thread.id}",
        )
        for thread in store.threads(user_id)[:CONVERSATION_CHOICES]
    ]


# Telegram clears the indicator after about five seconds, so a turn that lasts
# longer has to renew it. Four leaves room for the round trip.
TYPING_INTERVAL = 4.0


@asynccontextmanager
async def typing(
    client: TelegramClient, chat_id: int, active: bool = True
) -> AsyncIterator[None]:
    """Keep Telegram's "typing…" on screen for as long as the block runs.

    A cold turn spends most of its time waiting for a GPU to wake, where there
    is nothing to stream and nothing to report — the only honest thing to show
    is that the assistant is still there. This is that, and it is the platform's
    own indicator rather than a message that would have to be cleaned up.

    The renewing task is always cancelled and awaited, because a task left
    running in a container that is about to be frozen is a warning in the logs
    at best.
    """

    if not active:
        yield
        return

    async def keep() -> None:
        while True:
            await client.send_chat_action(chat_id)
            await asyncio.sleep(TYPING_INTERVAL)

    task = asyncio.create_task(keep())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def spoken(message: Message) -> str:
    return " ".join(part.text or "" for part in message.content if part.kind == "text").strip()


class TelegramAdapter:
    """Turn Telegram updates into agent turns, for allowed accounts only."""

    def __init__(
        self,
        client: TelegramClient,
        settings: TelegramSettings | None = None,
        agent_settings: AgentSettings | None = None,
        agent_factory: Callable[[str], Agent] | None = None,
        telemetry: Telemetry | None = None,
        stops: StopRequests | None = None,
        runner: Runner | None = None,
        inbox: Any | None = None,
    ) -> None:
        self.client = client
        self.settings = settings or TelegramSettings()
        # Where a command runs, decided by whoever started this process: the
        # deployed worker hands over the Function beside the renderer.
        self.runner = runner
        self.agent_settings = agent_settings or AgentSettings()
        # One recorder for every person this process serves. Whether a turn is
        # measured at all is decided by whoever started the worker, not here.
        self.telemetry = telemetry or Telemetry(None)
        # Where a request to stop is recorded, chosen the same way the store is:
        # a personal machine runs one process, so memory is the whole truth,
        # while the deployed profile answers `/stop` in one container and runs
        # the turn in another, and only the database is visible to both.
        self.stops = stops or (
            PostgresStopRequests(
                self.agent_settings.database_url, self.agent_settings.database_schema
            )
            if self.agent_settings.database_url
            else MemoryStopRequests()
        )
        # Supplied by tests so a turn can be driven without a model endpoint.
        # Where a running turn takes what the person wrote meanwhile. With
        # the durable inbox (deployed) the inbox is the lane; in one process
        # (polling) the lane is memory and `offer`/`taken` below feed it.
        self.interjections: Interjections = (
            InboxInterjections(inbox, self) if inbox is not None else MemoryInterjections()
        )
        self.agent_factory = agent_factory or self._default_agent
        self._agents: dict[str, Agent] = {}

    def _default_agent(self, user_id: str) -> Agent:
        return create_agent(
            agent_settings=self.agent_settings,
            user_id=user_id,
            delivery=DELIVERY,
            telemetry=self.telemetry,
            stops=self.stops,
            runner=self.runner,
            interjections=self.interjections,
        )

    # --- identity and access -------------------------------------------------

    def allows(self, telegram_user_id: int) -> bool:
        """Whether this account may use the assistant.

        Open access admits everyone. It does not merge them: each account still
        maps to its own canonical user, so conversations and memory stay
        separate. What they do share is one workspace and one GPU.
        """

        return self.settings.open_access or telegram_user_id in self.settings.allowed

    def agent(self, user_id: str) -> Agent:
        """One agent per person, because an `Agent` works for one owner."""

        if user_id not in self._agents:
            self._agents[user_id] = self.agent_factory(user_id)
        return self._agents[user_id]

    # --- inbound -------------------------------------------------------------

    async def to_message(self, incoming: Incoming, workspace: Path) -> Message:
        """Turn one update into a turn's input, saving what should not be pasted.

        The workspace is passed in rather than read here: which directory a
        person's files live in is the application's decision, and an adapter
        that worked it out for itself would be a second answer to it.
        """

        parts: list[ContentPart] = []
        if incoming.text:
            parts.append(ContentPart(kind="text", text=incoming.text))
        uploads = [
            AttachmentBytes(
                name=name,
                media_type=media_type or None,
                data=await self.client.download(file_id),
            )
            for file_id, name, media_type in incoming.files
        ]
        parts.extend(admit_uploads(uploads, workspace))
        if not parts:
            raise AttachmentError("the message has no text or usable attachments")
        return Message(role="user", content=parts)

    async def offer(self, update: dict[str, Any]) -> tuple[str, int] | None:
        """Offer an ordinary message to the turn that may be running (polling).

        Returns the lane key and the sequence to `settle` with once the
        caller holds the conversation, or `None` when this update is not a
        message a turn could read: it then takes the ordinary path.
        """

        lane = self.interjections
        if not isinstance(lane, MemoryInterjections):
            return None
        incoming = read_update(update)
        if (
            incoming is None
            or incoming.callback_data is not None
            or not self.allows(incoming.telegram_user_id)
            or travels_out_of_band(incoming)
            or not needs_model(incoming)
        ):
            return None
        user_id = canonical_user_id(incoming.telegram_user_id)
        try:
            message = await self.to_message(incoming, self.agent(user_id).capability_grant.root)
        except Exception:  # noqa: BLE001 - answered as its own turn, with the refusal
            return None
        await lane.offer(user_id, incoming.update_id, message)
        return user_id, incoming.update_id

    async def taken(self, key: str, sequence: int) -> bool:
        """Whether an offered message was read by a running turn (polling)."""

        lane = self.interjections
        return isinstance(lane, MemoryInterjections) and await lane.settle(key, sequence)

    async def handle_update(
        self, update: dict[str, Any], trace: TurnTrace = NO_TRACE
    ) -> None:
        """Handle one update to completion. Never raises at the transport.

        This is the only layer that knows what the person actually received, so
        it is where a turn's outcome is decided. A turn left without one is a
        turn that did not reach any of the endings below, which the worker
        closes as a failure rather than guessing.
        """

        incoming = read_update(update)
        if incoming is None:
            return
        if not self.allows(incoming.telegram_user_id):
            if incoming.callback_id:
                await self.client.answer_callback(incoming.callback_id, "Not allowed")
            else:
                await self.client.send_message(incoming.chat_id, REFUSAL)
            return

        user_id = canonical_user_id(incoming.telegram_user_id)
        # The application's own identifier, never Telegram's: per-user cost is
        # part of the product, and telemetry has no reason to hold an account id.
        trace.run.user_id = user_id
        agent = self.agent(user_id)
        try:
            # `needs_model` decides this as well as whether the webhook wakes the
            # GPU, and it should: the updates worth showing progress for are
            # exactly the ones that take long enough to need it. A command
            # answers from storage and is done before an indicator would appear.
            async with typing(self.client, incoming.chat_id, needs_model(incoming)):
                if incoming.callback_data is not None:
                    await self._on_callback(agent, user_id, incoming, trace)
                else:
                    await self._on_message(agent, user_id, incoming, trace)
        except TelegramError:
            raise
        except Exception as error:  # noqa: BLE001 - a failed turn must not kill the bot
            trace.finish("failed", error_type=type(error).__name__)
            await self.client.send_message(
                incoming.chat_id, f"That request failed: {type(error).__name__}: {error}"
            )

    async def _on_message(
        self,
        agent: Agent,
        user_id: str,
        incoming: Incoming,
        trace: TurnTrace = NO_TRACE,
    ) -> None:
        store = agent.store
        command = incoming.text.strip().lower()
        if command in {"/start", "/help"}:
            await self.client.send_message(incoming.chat_id, HELP)
            return
        if command == "/new":
            # Reusing an untouched conversation must not be reported as having
            # made one: the chat would claim an action that did not happen.
            before = store.active_thread(user_id)
            started = new_thread(store, user_id)
            await self.client.send_message(
                incoming.chat_id,
                "Started a new conversation."
                if started != before
                else "You are already in a new conversation.",
            )
            return
        if command == "/chats":
            await self._show_conversations(store, user_id, incoming.chat_id)
            return
        head, _, argument = incoming.text.strip().partition(" ")
        if head.lower() in INSTRUCTION_COMMANDS:
            # Read from the raw text rather than the lowercased command: what
            # follows `set` is the person's own writing and must survive.
            await self._on_instructions(agent, incoming.chat_id, argument.strip())
            return
        if head.lower() in PLAN_COMMANDS:
            await self._on_plan(agent, incoming.chat_id, argument.strip().lower())
            return
        if head.lower() in MODE_COMMANDS:
            await self._on_mode(agent, incoming.chat_id, argument.strip().lower())
            return
        if head.lower() in CONTEXT_COMMANDS:
            await self._on_context(
                agent, current_thread(store, user_id), incoming.chat_id, argument.strip().lower()
            )
            return
        if command == "/compact":
            await self._on_compact(agent, current_thread(store, user_id), incoming.chat_id)
            # Answered, without a turn: a command that reached the model for a
            # summary is not a failed turn. Recorded as `incomplete` on
            # 2026-09-03 until this line.
            trace.finish("answer_delivered")
            return
        if command == "/can":
            # Answered from the wiring, not by the model. When the assistant
            # claims it cannot send a picture, this is what that is checked
            # against — and it costs nothing, because no GPU is involved.
            await self.client.send_message(
                incoming.chat_id,
                agent.capabilities(current_thread(store, user_id)),
            )
            return
        if command == "/check":
            # `/can` is the claim; this is the claim tried. Free probes only —
            # the model is not called, so nothing here wakes a GPU.
            await self.client.send_message(
                incoming.chat_id,
                await agent.selftest(current_thread(store, user_id)),
            )
            return
        if command == "/stop":
            # Recorded, not executed. This update reached here out of band, so
            # the turn it is about is still running — in another container, in
            # the deployed profile — and what ends it is the loop reading this
            # at its next step. Saying so is the honest message: claiming the
            # work has stopped before it has is how a person sends `/stop`
            # twice.
            await agent.stops.request(user_id, incoming.update_id)
            await self.client.send_message(
                incoming.chat_id,
                "Stopping. Anything running will stop at its next step.",
            )
            # Neither a failure nor a delivered answer. Its own outcome, so that
            # reliability figures do not count a person changing their mind as
            # the assistant breaking.
            trace.finish("cancelled", status="cancelled")
            return

        try:
            message = await self.to_message(incoming, agent.capability_grant.root)
        except AttachmentError as error:
            await self.client.send_message(incoming.chat_id, f"Upload refused: {error}.")
            return

        thread_id = current_thread(store, user_id)
        trace.run.thread_id = thread_id
        # One route, so nothing decides between two. What used to be a full
        # model request per message, before the answer the person was waiting
        # for, is now this line.
        trace.route("loop")
        # The queue hands a conversation's oldest unfinished update over first,
        # so the update a dead worker was answering comes back here before any
        # later one. If the checkpoint holds a turn that began with this very
        # message, it is taken up where it stopped rather than started again
        # with every tool run twice. A different message means the earlier
        # turn was given up on; it starts afresh, as `extend` makes it.
        left = await agent.unfinished(thread_id)
        await self._answer(
            agent,
            incoming.chat_id,
            thread_id,
            message,
            incoming.update_id,
            trace,
            resume=left is not None and same_request(left.request, message),
        )

    async def give_up(self, update: dict[str, Any], attempts: int) -> None:
        """Say that a request was interrupted too often to be finished.

        Called by the worker when the queue stops claiming an update, so the
        person learns why nothing came rather than waiting on it.
        """

        incoming = read_update(update)
        if incoming is None:
            return
        await self.client.send_message(
            incoming.chat_id,
            f"This request was interrupted {attempts} times and could not be "
            "finished. Send it again if you still want it.",
        )

    # --- choosing a conversation ---------------------------------------------

    async def _on_instructions(self, agent: Agent, chat_id: int, argument: str) -> None:
        """Show, replace or clear the standing instructions.

        A thin UI over one file and nothing else. The same `AGENTS.md` is an
        ordinary file in the person's workspace, so the assistant can read and
        edit it with the tools it already has, and this command has to write
        the same place — a command with its own store would be a second set of
        instructions that disagrees with the first.

        Replacement rather than appending: one message is the whole file. What
        does not fit in a message is what `edit_file` is for.

        Anything that is not `clear` is the instructions themselves, with a
        leading `set` dropped if it is there. Nothing here interprets the text:
        `set` is a word this command removes, not a word it acts on, and what
        is stored is exactly what was typed. The alternative — recognising one
        exact form and silently showing help for everything else — is how a
        person ends up believing they saved instructions that were never
        written.
        """

        action, body = _split_argument(argument)
        workspace = agent.workspace

        if action == "set":
            try:
                saved = write_instructions(workspace, body)
            except InstructionsError as error:
                await self.client.send_message(chat_id, f"Not saved: {error}.")
                return
            await self.client.send_message(
                chat_id,
                f"Saved {len(saved)} characters to {INSTRUCTIONS_FILE}, exactly as you "
                "wrote them. They apply from your next message.",
            )
            return

        if action == "clear":
            removed = clear_instructions(workspace)
            await self.client.send_message(
                chat_id,
                f"{INSTRUCTIONS_FILE} removed. I go back to my ordinary behaviour."
                if removed
                else "You had no standing instructions.",
            )
            return

        current = read_instructions(workspace)
        if not current:
            await self.client.send_message(
                chat_id,
                "You have no standing instructions. Send /agents followed by how you "
                "want me to work — for example, which language to answer in, or how "
                "much detail you want. Whatever you write is stored as you wrote it; "
                "I do not read it until the next message. It is kept as "
                f"{INSTRUCTIONS_FILE} in your workspace, so you can also ask me to "
                "edit it. /agents clear removes it.",
            )
            return
        await self.client.send_message(
            chat_id,
            f"Your standing instructions, from {INSTRUCTIONS_FILE}:\n\n{current}\n\n"
            "/agents followed by new text replaces this, /agents clear removes it.",
        )

    async def _on_plan(self, agent: Agent, chat_id: int, argument: str) -> None:
        """Show or flip whether the assistant keeps a task list.

        A marker file in the person's workspace, read when the next turn's
        toolbox is built, so `off` takes effect from the next message and is
        the same in every interface. Nothing else is touched: with the tool
        absent the brief has nothing to say about planning.
        """

        workspace = agent.workspace
        if argument in {"on", "off"}:
            set_planning(workspace, argument == "on")
            agent.rewire()
            await self.client.send_message(
                chat_id,
                "Planning is on from your next message: I may keep a task list for "
                f"longer work. Kept as {PLAN_SWITCH.as_posix()} in your workspace; "
                "/plan off turns it off again."
                if argument == "on"
                else "Planning is off from your next message: no task list, no "
                "planning tool. That is the default; /plan on turns it on.",
            )
            return
        state = "on" if planning_enabled(workspace) else "off (the default)"
        await self.client.send_message(
            chat_id,
            f"Planning is {state}. /plan on gives me a task list and the planning "
            "tool from the next message on; /plan off takes them away.",
        )

    async def _on_mode(self, agent: Agent, chat_id: int, argument: str) -> None:
        """Show or set whether changes to the workspace ask first.

        The same marker mechanism as `/plan`: read when the next toolbox is
        built, so it takes effect from the next message in every interface.
        """

        workspace = agent.workspace
        if argument in MODES:
            set_mode(workspace, argument)
            agent.rewire()
            await self.client.send_message(
                chat_id,
                "Careful mode from your next message: writing or changing a file and "
                "running a command wait for your yes, with the same buttons as before. "
                "/mode full turns it off."
                if argument == "careful"
                else "Full mode from your next message: everything inside your workspace "
                "runs without asking, and only effects beyond it ask. That is the "
                "default; /mode careful makes changes ask first.",
            )
            return
        mode = current_mode(workspace)
        await self.client.send_message(
            chat_id,
            f"Mode: {mode}{' (the default)' if mode == 'full' else ''}. In full mode "
            "everything inside your workspace runs without asking; in careful mode a "
            "change to a file or a command waits for your yes. /mode full or /mode "
            "careful sets it from the next message.",
        )

    async def _on_context(
        self, agent: Agent, thread_id: str, chat_id: int, argument: str
    ) -> None:
        """Say what the next request is made of, or set how large it may be.

        Read from the store and estimated, never sent: the one number this
        cannot always give is the model's ceiling, which is read when the model
        next answers rather than by waking it for a report.
        """

        if argument in SIZES:
            set_context_choice(agent.workspace, argument)
            agent.rewire()
            await self.client.send_message(
                chat_id,
                f"Context size is {argument} from your next message"
                + (
                    f", kept as {CONTEXT_CHOICE.as_posix()} in your workspace."
                    if argument != "normal"
                    else " (the default)."
                ),
            )
            return
        if argument:
            await self.client.send_message(
                chat_id, "Sizes are small, normal and large: /context small."
            )
            return
        report = agent.context_report(thread_id)
        layers = report.layers
        lines = [
            "What my next request in this chat is made of, estimated:",
            f"  core and capabilities  ~{layers['prelude']:,}",
            f"  tool schemas           ~{layers['schemas']:,}",
            f"  conversation           ~{layers['history']:,} "
            f"({report.messages} messages verbatim"
            + (f", {report.stubbed} older tool results shortened" if report.stubbed else "")
            + (f", {report.placeholders} pictures as placeholders" if report.placeholders else "")
            + ")",
        ]
        if report.summarized_through:
            lines.append(f"  summary covers the {report.summarized_through} messages before that")
        if layers["facts"]:
            lines.append(f"  facts for this turn    ~{layers['facts']:,}")
        if report.last_used is not None:
            cached = (
                f", {report.last_cached:,} of them from the cache"
                if report.last_cached is not None
                else ""
            )
            lines.append(f"Last request: {report.last_used:,} tokens{cached}.")
        if report.budget:
            lines.append(
                f"Size {report.size}: up to {report.budget:,} tokens of the model's "
                f"{report.ceiling:,}; older conversation folds into the summary past that."
            )
        else:
            lines.append(
                f"Size {report.size}: {int(report.fraction * 100)}% of the model's window, "
                "read when it next answers."
            )
        lines.append("/context small|normal|large sets the size; /compact folds the older part now.")
        await self.client.send_message(chat_id, "\n".join(lines))

    async def _on_compact(self, agent: Agent, thread_id: str, chat_id: int) -> None:
        """Fold the older part of this conversation now. One summarizer call."""

        folded = await agent.compact(thread_id)
        await self.client.send_message(
            chat_id,
            f"Folded {folded} older messages into the summary; "
            f"{exchanges(agent.policy.keep_turns)} stay verbatim."
            if folded
            else f"Nothing to fold: {exchanges(agent.policy.keep_turns)} always stay "
            "verbatim, and that is all there is past the summary.",
        )

    async def _show_conversations(
        self, store: ConversationStore, user_id: str, chat_id: int
    ) -> None:
        """Offer this user their own recent conversations, and nobody else's.

        `current_thread` first, so a person with no conversations is offered the
        one they are about to write in rather than an empty list.
        """

        chosen = current_thread(store, user_id)
        await self.client.send_message(
            chat_id,
            "Conversations",
            conversations_keyboard(
                conversation_choices(store, user_id, chosen), CHATS_CLOSE
            ),
        )

    async def _choose_conversation(
        self, store: ConversationStore, user_id: str, incoming: Incoming, data: str
    ) -> None:
        """Answer a press on the conversation list. Reads no model, wakes nothing."""

        if data == CHATS_CLOSE:
            if incoming.callback_id:
                await self.client.answer_callback(incoming.callback_id)
            if incoming.callback_message_id is not None:
                await self.client.edit_reply_markup(
                    incoming.chat_id, incoming.callback_message_id, no_keyboard()
                )
            return

        thread_id = data[len(CHATS_CALLBACK_PREFIX) :]
        try:
            store.set_active_thread(user_id, thread_id)
        except KeyError:
            # Somebody else's conversation, or one that has since been deleted.
            # The same answer for both, because the store deliberately does not
            # distinguish them.
            if incoming.callback_id:
                await self.client.answer_callback(
                    incoming.callback_id, "That conversation is not available"
                )
            return

        if incoming.callback_id:
            await self.client.answer_callback(incoming.callback_id, "Switched")
        if incoming.callback_message_id is not None:
            # The same message, remarked. Selecting does not touch `updated_at`,
            # so the order the person is looking at does not move under them.
            await self.client.edit_reply_markup(
                incoming.chat_id,
                incoming.callback_message_id,
                conversations_keyboard(
                    conversation_choices(store, user_id, thread_id), CHATS_CLOSE
                ),
            )

    # --- the answer branch ---------------------------------------------------

    async def _answer(
        self,
        agent: Agent,
        chat_id: int,
        thread_id: str,
        message: Message,
        sequence: int = 0,
        trace: TurnTrace = NO_TRACE,
        resume: bool = False,
    ) -> None:
        activity = ToolActivity(self.client, chat_id)
        preview = AnswerPreview(self.client, chat_id)
        delivered: set[str] = set()
        _, covered = agent.store.summary(thread_id)
        events = (
            agent.resume_interrupted_events(thread_id, trace)
            if resume
            else agent.events(thread_id, message, trace, sequence)
        )
        try:
            async for event in events:
                if isinstance(event, AssistantDelta):
                    if await preview.add(event.text):
                        # There is something to read now, so the status has
                        # done its job; it goes before the answer grows under it.
                        await activity.clear()
                        trace.visible("preview_started")
                    continue
                if isinstance(event, AnswerWithdrawn):
                    # The turn kept working instead of ending here. What the
                    # person watched being written stays where it is: the loop
                    # will hand it back as the answer if the model adds
                    # nothing, and replace it in place if the model does.
                    preview.hold()
                    continue
                if isinstance(event, MessageTaken):
                    # The person's own message, read by the running turn.
                    # They sent it; it is not sent back.
                    continue
                await self._deliver(
                    chat_id, event.message, activity, preview, trace, delivered
                )
        finally:
            # Including when the turn failed: the last thing a person should be
            # left looking at is not "Reading page…" on a turn that stopped, and
            # not half an answer that is never going to be finished.
            await activity.clear()
            await preview.discard()
        await self._fold_notice(agent, thread_id, chat_id, covered)
        asked = await self._ask_pending_calls(agent, chat_id, thread_id)
        # A turn that stopped to ask is not a turn that failed to answer. Both
        # are successful endings, and they are told apart here.
        trace.finish("approval_requested" if asked else "answer_delivered")

    async def _fold_notice(
        self, agent: Agent, thread_id: str, chat_id: int, covered: int
    ) -> None:
        """Say so when a turn folded older conversation into the summary.

        Asked for by the human on 2026-09-03: a fold changes what the
        assistant carries verbatim, and a person who does not know one
        happened cannot know why a detail now has to be asked for. The
        summary's covered position before and after the turn is the whole
        detection; nothing is threaded through the graph for it.
        """

        _, now = agent.store.summary(thread_id)
        if now <= covered:
            return
        await self.client.send_message(
            chat_id,
            f"Folded {now - covered} older messages into the summary, because this "
            f"conversation grew past its size; {exchanges(agent.policy.keep_turns)} stay "
            "verbatim, and the exact words stay reachable with search_history.",
        )

    async def _deliver(
        self,
        chat_id: int,
        produced: Message,
        activity: ToolActivity | None = None,
        preview: AnswerPreview | None = None,
        trace: TurnTrace = NO_TRACE,
        delivered: set[str] | None = None,
    ) -> None:
        body = spoken(produced)
        if produced.role == "tool":
            # Tool results are working material. Only a presentation tool can
            # mark a concrete item outbound; observing a page or screenshot is
            # never interpreted as a send decision by this adapter.
            if activity is not None and any(
                part.outbound and part.data for part in produced.content
            ):
                await activity.clear()
            await self._send_media(chat_id, produced, outbound_only=True)
            return
        # The model said this already in this turn — measured live on
        # 2026-09-03, the whole answer written with a send attached and then
        # written again after it. The person has it; the second copy is not
        # sent, and the core prompt asks the model not to write it.
        repeat = bool(body) and delivered is not None and body in delivered
        if body and not repeat:
            # Text is delivered whether or not a tool call rides with it. What
            # the model writes beside a call is what it is telling the person
            # while it acts — the same thing every chat interface shows — and
            # withdrawing it made the model write it all again (ISS-0009).
            if activity is not None:
                await activity.clear()
            # The canonical answer is the model's ordinary Markdown, which is
            # what the store keeps. This renders that same text for Telegram —
            # into the message the person already watched being written, when
            # there was one, and as a new message when there was not.
            if preview is None or not await preview.finish(body):
                await self.client.send_message(chat_id, Formatted.from_markdown(body))
            # A short answer arrives whole and never previewed, and it did
            # become visible: `visible` keeps the first of the two.
            trace.visible("final_sent")
            if delivered is not None:
                delivered.add(body)
        elif preview is not None and not preview.held:
            # A completion with no spoken text, or one repeating what was
            # delivered, cannot finalize a text preview. A held draft is not
            # this completion's preview: it waits for the answer that ends the
            # turn, which may be the draft itself.
            await preview.discard()
        await self._send_media(chat_id, produced)
        if produced.tool_calls:
            labels = activity_labels(produced.tool_calls)
            if activity is not None:
                await activity.show(labels, plan_lines(produced.tool_calls))
            else:
                await self.client.send_message(chat_id, "\n".join(labels))

    async def _send_media(
        self, chat_id: int, produced: Message, *, outbound_only: bool = False
    ) -> None:
        """Translate media the application explicitly selected to Telegram.

        Consecutive items of one kind — photos, or documents — go as one album,
        which is what one `send_file` with several paths asks for. A single
        item goes as it always did, and an album Telegram refuses falls back
        to one message per item rather than to nothing.
        """

        runs: list[tuple[str, list[tuple[str, bytes]]]] = []
        for index, part in enumerate(produced.content, start=1):
            if part.kind == "text" or not part.data:
                continue
            if outbound_only and not part.outbound:
                continue
            name = part.name or (
                f"{part.kind}-{index}{MEDIA_SUFFIXES.get(part.media_type or '', '.bin')}"
            )
            kind = "photo" if part.kind == "image" and len(part.data) <= MAX_PHOTO_BYTES else "document"
            if runs and runs[-1][0] == kind and len(runs[-1][1]) < ALBUM_LIMIT:
                runs[-1][1].append((name, part.data))
            else:
                runs.append((kind, [(name, part.data)]))
        for kind, items in runs:
            if len(items) > 1:
                try:
                    await self.client.send_media_group(chat_id, kind, items)
                    continue
                except TelegramError:
                    pass
            for name, data in items:
                if kind == "photo":
                    await self.client.send_photo(chat_id, name, data)
                else:
                    await self.client.send_document(chat_id, name, data)

    async def _ask_pending_calls(
        self, agent: Agent, chat_id: int, thread_id: str
    ) -> bool:
        """Put the graph's own consent question in front of the user.

        The question lives in the checkpoint, not here, so a restart between
        asking and answering loses nothing. Returns whether anything was asked,
        because a turn that ends in a question ended differently from one that
        ends in an answer.
        """

        pending = await agent.pending(thread_id)
        for call in pending or []:
            await self.client.send_message(
                chat_id,
                f"Run {call['name']}?\n{call['arguments']}",
                approval_keyboard(f"call:yes:{call['id']}", f"call:no:{call['id']}"),
            )
        return bool(pending)

    # --- consent answers -----------------------------------------------------

    async def _on_callback(
        self,
        agent: Agent,
        user_id: str,
        incoming: Incoming,
        trace: TurnTrace = NO_TRACE,
    ) -> None:
        data = incoming.callback_data or ""
        if data.startswith(SETTLED_CALLBACK_PREFIX):
            # A button that already says what happened. Answering the callback
            # is all Telegram needs, and it is all this may do: before any
            # thread is read or created, so pressing it cannot start a
            # conversation, resume a task, or wake anything.
            if incoming.callback_id:
                await self.client.answer_callback(
                    incoming.callback_id,
                    "Already approved" if data.endswith("approved") else "Already rejected",
                )
            return

        if data.startswith(CHATS_CALLBACK_PREFIX):
            # Also before any thread is read or created: choosing a conversation
            # is answered from storage, and a tap on this list must not become a
            # reason to start one, resume a turn or wake a GPU.
            await self._choose_conversation(agent.store, user_id, incoming, data)
            return

        thread_id = current_thread(agent.store, user_id)
        trace.run.thread_id = thread_id
        if incoming.callback_id:
            await self.client.answer_callback(incoming.callback_id)

        if data.startswith("call:"):
            trace.route("loop")
            _, verdict, call_id = data.split(":", 2)
            approved = verdict == "yes"
            settled = False
            activity = ToolActivity(self.client, incoming.chat_id)
            preview = AnswerPreview(self.client, incoming.chat_id)
            trace.event("approval_resumed" if approved else "approval_declined")
            _, covered = agent.store.summary(thread_id)
            try:
                events = agent.resume_events(thread_id, {call_id: approved}, trace)
                async for event in events:
                    if not settled:
                        await self._settle(incoming, approved=approved)
                        settled = True
                    if isinstance(event, AssistantDelta):
                        if await preview.add(event.text):
                            await activity.clear()
                            trace.visible("preview_started")
                        continue
                    if isinstance(event, AnswerWithdrawn):
                        await preview.discard()
                        continue
                    if isinstance(event, MessageTaken):
                        continue
                    await self._deliver(
                        incoming.chat_id, event.message, activity, preview, trace
                    )
            finally:
                await activity.clear()
                await preview.discard()
            if not settled:
                await self._settle(incoming, approved=approved)
            await self._fold_notice(agent, thread_id, incoming.chat_id, covered)
            asked = await self._ask_pending_calls(agent, incoming.chat_id, thread_id)
            trace.finish("approval_requested" if asked else "answer_delivered")

    async def _settle(self, incoming: Incoming, *, approved: bool) -> None:
        """Turn the choices back into the one state that was actually reached.

        Called only after the application transition succeeded, because that is
        what the button then claims. A settlement that could not be written is
        left unsettled rather than reported as done: a stale pair of buttons is
        honest, and a green tick over a transition that did not happen is not.
        """

        if incoming.callback_message_id is None:
            return
        for styled in (True, False):
            try:
                await self.client.edit_reply_markup(
                    incoming.chat_id,
                    incoming.callback_message_id,
                    settled_keyboard(approved, styled=styled),
                )
                return
            except TelegramError:
                # `style` is a recent Bot API field. An older server refuses the
                # whole edit for it, so the second attempt drops the colour and
                # keeps the state, which is the half that carries the meaning.
                continue

    async def aclose(self) -> None:
        for agent in self._agents.values():
            await agent.aclose()
        self._agents.clear()


def same_request(left: Message | None, incoming: Message) -> bool:
    """Whether the message an update carries is the one a turn began with.

    By text: an attachment is admitted under a fresh name each time, and the
    queue's order already makes the oldest unfinished update the one that
    comes back, so the text is the tie-break, not the proof.
    """

    if left is None:
        return False
    return "".join(part.text or "" for part in left.content) == "".join(
        part.text or "" for part in incoming.content
    )


def exchanges(keep_turns: int) -> str:
    """The verbatim floor in a person's words: exchanges, not messages."""

    return "the last exchange" if keep_turns == 1 else f"the last {keep_turns} exchanges"
