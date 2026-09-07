"""The Telegram adapter, driven without a network or a model endpoint.

Telegram is replaced by an `httpx.MockTransport` so the real wire format in
`ui/telegram/api.py` is exercised rather than mocked away, and the model is the
shared `ScriptedBackend`. Nothing here reaches outside the process.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agent.runtime import Agent
from app.agent.stop import MemoryStopRequests
from app.agent.stopping import STOP_ON_ANSWER, Candidate, Steering
from app.config import AgentSettings, TelegramSettings
from app.context import ContextPolicy
from app.memory import SqliteStore
from app.models import Completion, ContentPart, Message, ToolCall
from app.instructions import INSTRUCTIONS_FILE
from tests.fakes import ScriptedBackend, calls, prompt_text, says
from ui.telegram.adapter import (
    HELP,
    LABEL_CHARS,
    REFUSAL,
    TOOL_ACTIVITY,
    UNKNOWN_ACTIVITY,
    AnswerPreview,
    TelegramAdapter,
    ToolActivity,
    canonical_user_id,
    current_thread,
    spoken,
    start_thread,
)
from ui.telegram.wire import (
    CHATS_CALLBACK_PREFIX,
    CHATS_CLOSE,
    MODEL_FREE_COMMANDS,
    SETTLED_APPROVED,
    SETTLED_REJECTED,
    Incoming,
    needs_model,
    read_update,
)
from ui.telegram import markdown
from ui.telegram.api import (
    BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION,
    MAX_MESSAGE_CHARS,
    PRODUCT_COMMANDS,
    Formatted,
    TelegramClient,
    split_message,
)

ALLOWED = 4242
STRANGER = 9999
CHAT = 1000


class FakeTelegram:
    """Records every Bot API call and answers it the way Telegram would."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sent: list[str] = []
        self.keyboards: list[dict[str, Any]] = []
        self.documents: list[str] = []
        self.photos: list[str] = []
        self.albums: list[tuple[str, list[str]]] = []
        self.files: dict[str, bytes] = {}
        self._next_message_id = 100

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/file/bot" in path:
            name = path.rsplit("/", 1)[-1]
            return httpx.Response(200, content=self.files.get(name, b""))

        method = path.rsplit("/", 1)[-1]
        if method == "sendMediaGroup":
            body = request.content.decode("latin-1")
            names = re.findall(r'filename="([^"]+)"', body)
            media = body.partition('name="media"')[2].partition("\r\n\r\n")[2].partition("\r\n")[0]
            kind = json.loads(media)[0]["type"]
            self.albums.append((kind, names))
            (self.photos if kind == "photo" else self.documents).extend(names)
            self.calls.append((method, {}))
            return httpx.Response(200, json={"ok": True, "result": []})
        if method in {"sendDocument", "sendPhoto"}:
            # Uploads are multipart, so the filename is read off the body rather
            # than from JSON like every other call.
            body = request.content.decode("latin-1")
            name = body.partition('filename="')[2].partition('"')[0]
            (self.photos if method == "sendPhoto" else self.documents).append(name)
            self.calls.append((method, {}))
            return httpx.Response(200, json={"ok": True, "result": {}})

        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        if method == "sendMessage":
            self.sent.append(payload["text"])
            if payload.get("reply_markup"):
                self.keyboards.append(payload["reply_markup"])
            self._next_message_id += 1
            return httpx.Response(
                200, json={"ok": True, "result": {"message_id": self._next_message_id}}
            )
        if method == "getFile":
            return httpx.Response(
                200, json={"ok": True, "result": {"file_path": payload["file_id"]}}
            )
        return httpx.Response(200, json={"ok": True, "result": {}})


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def settings() -> TelegramSettings:
    return TelegramSettings(token="test-token", allowed_users=str(ALLOWED))


BUILT: list[TelegramAdapter] = []


@pytest.fixture(autouse=True)
async def close_adapters():
    """Close every adapter a test built.

    An adapter owns an agent per user, and an agent owns SQLite connections
    and a checkpoint file; leaving them open leaks worker threads into the next
    test and makes the suite's failures depend on its order.
    """

    yield
    while BUILT:
        adapter = BUILT.pop()
        await adapter.aclose()
        await adapter.client.aclose()


def build(
    telegram: FakeTelegram,
    settings: TelegramSettings,
    tmp_path: Path,
    backend: ScriptedBackend,
    stops: MemoryStopRequests | None = None,
    stopping=STOP_ON_ANSWER,
    policy: ContextPolicy | None = None,
) -> TelegramAdapter:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    agent_settings = AgentSettings(
        database=str(tmp_path / "memory.sqlite3"),
        checkpoints=str(tmp_path / "checkpoints.sqlite3"),
        workspace=str(workspace),
    )
    stops = stops or MemoryStopRequests()

    def factory(user_id: str) -> Agent:
        return Agent(
            backend,
            SqliteStore(agent_settings.database),
            workspace,
            checkpoints=agent_settings.checkpoints,
            user_id=user_id,
            stops=stops,
            stopping=stopping,
            policy=policy,
        )

    client = TelegramClient(settings, transport=telegram.transport())
    adapter = TelegramAdapter(
        client, settings, agent_settings, agent_factory=factory, stops=stops
    )
    BUILT.append(adapter)
    return adapter


def text_update(text: str, sender: int = ALLOWED, update_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": CHAT}, "from": {"id": sender}, "text": text},
    }


def said(text: str) -> Message:
    return Message(role="user", content=[ContentPart(kind="text", text=text)])


# --- identity ----------------------------------------------------------------


def test_a_telegram_id_is_mapped_not_adopted() -> None:
    """The canonical identifier must not be Telegram's own."""

    derived = canonical_user_id(ALLOWED)

    assert derived != str(ALLOWED)
    assert str(ALLOWED) not in derived
    assert derived == canonical_user_id(ALLOWED)
    assert derived != canonical_user_id(ALLOWED + 1)


def test_the_open_conversation_is_the_one_that_was_chosen(tmp_path: Path) -> None:
    with SqliteStore(tmp_path / "m.sqlite3") as store:
        user_id = canonical_user_id(ALLOWED)
        first = current_thread(store, user_id)

        assert current_thread(store, user_id) == first

        second = start_thread(store, user_id)
        assert second != first
        assert current_thread(store, user_id) == second

        store.set_active_thread(user_id, first)
        assert current_thread(store, user_id) == first


def test_activity_elsewhere_cannot_take_the_person_with_it(tmp_path: Path) -> None:
    """Recency orders the list; it does not decide where the next message lands."""

    with SqliteStore(tmp_path / "m.sqlite3") as store:
        user_id = canonical_user_id(ALLOWED)
        chosen = current_thread(store, user_id)
        other = start_thread(store, user_id)
        store.set_active_thread(user_id, chosen)

        store.append(other, [said("later")], user_id)

        assert store.threads(user_id)[0].id == other
        assert current_thread(store, user_id) == chosen


def test_a_conversation_from_before_the_choice_existed_is_adopted(
    tmp_path: Path,
) -> None:
    """The upgrade path: a user with threads and no recorded choice.

    Their most recent conversation is handed back to them, rather than an empty
    one opened beside it.
    """

    with SqliteStore(tmp_path / "m.sqlite3") as store:
        user_id = canonical_user_id(ALLOWED)
        store.append("older", [said("earlier")], user_id)
        store.append("newer", [said("later")], user_id)
        assert store.active_thread(user_id) is None

        assert current_thread(store, user_id) == "newer"
        assert store.active_thread(user_id) == "newer"


def test_a_new_conversation_survives_reopening_the_store(tmp_path: Path) -> None:
    path = tmp_path / "m.sqlite3"
    user_id = canonical_user_id(ALLOWED)
    with SqliteStore(path) as store:
        started = start_thread(store, user_id)

    with SqliteStore(path) as reopened:
        assert current_thread(reopened, user_id) == started


# --- reading updates ---------------------------------------------------------


def test_a_plain_message_is_read() -> None:
    incoming = read_update(text_update("hello"))

    assert incoming is not None
    assert (incoming.chat_id, incoming.telegram_user_id, incoming.text) == (CHAT, ALLOWED, "hello")


def test_the_largest_photo_size_is_the_one_taken() -> None:
    update = {
        "message": {
            "chat": {"id": CHAT},
            "from": {"id": ALLOWED},
            "caption": "look at this",
            "photo": [{"file_id": "small"}, {"file_id": "large"}],
        }
    }

    incoming = read_update(update)

    assert incoming is not None
    assert incoming.text == "look at this"
    assert incoming.files == (("large", "photo.jpg", "image/jpeg"),)


def test_a_callback_carries_its_answer() -> None:
    update = {
        "callback_query": {
            "id": "cb1",
            "from": {"id": ALLOWED},
            "data": "task:yes",
            "message": {"chat": {"id": CHAT}},
        }
    }

    incoming = read_update(update)

    assert incoming is not None
    assert (incoming.callback_id, incoming.callback_data) == ("cb1", "task:yes")


@pytest.mark.parametrize(
    "update", [{}, {"message": {}}, {"edited_message": {"text": "x"}}, {"message": {"text": "x"}}]
)
def test_an_unusable_update_is_ignored(update: dict[str, Any]) -> None:
    assert read_update(update) is None


# --- access ------------------------------------------------------------------


async def test_a_stranger_is_refused_and_nothing_is_processed(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend()
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("let me in", sender=STRANGER))

    assert telegram.sent == [REFUSAL]
    assert backend.requests == []


async def test_open_access_admits_a_stranger(
    telegram: FakeTelegram, tmp_path: Path
) -> None:
    opened = TelegramSettings(token="test-token", allowed_users="", open_access=True)
    backend = ScriptedBackend(says("Hello, stranger."), default=says("summary"))
    adapter = build(telegram, opened, tmp_path, backend)

    await adapter.handle_update(text_update("hello", sender=STRANGER))

    assert "Hello, stranger." in telegram.sent
    assert REFUSAL not in telegram.sent


async def test_open_access_still_separates_the_people_it_admits(
    telegram: FakeTelegram, tmp_path: Path
) -> None:
    """Admitting everyone is not the same as merging them."""

    opened = TelegramSettings(token="test-token", allowed_users="", open_access=True)
    backend = ScriptedBackend(says("one"), says("two"), default=says("summary")
    )
    adapter = build(telegram, opened, tmp_path, backend)

    await adapter.handle_update(text_update("mine", sender=ALLOWED))
    await adapter.handle_update(text_update("theirs", sender=STRANGER))

    store = SqliteStore(tmp_path / "memory.sqlite3")
    try:
        assert len(store.threads(canonical_user_id(ALLOWED))) == 1
        assert len(store.threads(canonical_user_id(STRANGER))) == 1
        assert canonical_user_id(ALLOWED) != canonical_user_id(STRANGER)
    finally:
        store.close()


async def test_open_access_is_never_the_default() -> None:
    assert TelegramSettings(token="t").open_access is False


async def test_an_empty_allow_list_admits_nobody(
    telegram: FakeTelegram, tmp_path: Path
) -> None:
    closed = TelegramSettings(token="test-token", allowed_users="")
    adapter = build(telegram, closed, tmp_path, ScriptedBackend())

    assert adapter.allows(ALLOWED) is False

    await adapter.handle_update(text_update("hello"))

    assert telegram.sent == [REFUSAL]


# --- the answer branch -------------------------------------------------------


async def test_an_ordinary_message_is_answered_into_the_chat(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(says("Hello back."), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("hello"))

    assert "Hello back." in telegram.sent


async def test_media_the_agent_produced_reaches_the_chat(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """A browser screenshot lives in the message, not in an on-disk artifact.

    The model cannot be scripted to emit one, so the delivery step is driven
    directly; before this existed the picture stopped at the store.
    """

    adapter = build(telegram, settings, tmp_path, ScriptedBackend(default=says("x")))
    produced = Message(
        role="assistant",
        content=[
            ContentPart(kind="text", text="Here is the page."),
            ContentPart(kind="image", data=b"\x89PNG", media_type="image/png"),
        ],
    )

    await adapter._deliver(CHAT, produced)

    assert "Here is the page." in telegram.sent
    assert telegram.photos == ["image-2.png"]
    assert telegram.documents == []


async def test_non_image_media_is_sent_as_a_document(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    adapter = build(telegram, settings, tmp_path, ScriptedBackend(default=says("x")))
    produced = Message(
        role="assistant",
        content=[ContentPart(kind="audio", data=b"RIFF", media_type="audio/wav")],
    )

    await adapter._deliver(CHAT, produced)

    assert telegram.documents == ["audio-1.wav"]
    assert telegram.photos == []


async def test_the_conversation_is_stored_under_the_mapped_user(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(says("Noted."), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("remember this"))

    store = SqliteStore(tmp_path / "memory.sqlite3")
    try:
        threads = store.threads(canonical_user_id(ALLOWED))
        assert len(threads) == 1
        assert store.threads(canonical_user_id(STRANGER)) == []
    finally:
        store.close()


async def test_new_starts_a_separate_conversation(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(says("first"), says("second"), default=says("summary")
    )
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("one"))
    await adapter.handle_update(text_update("/new"))
    await adapter.handle_update(text_update("two"))

    store = SqliteStore(tmp_path / "memory.sqlite3")
    try:
        assert len(store.threads(canonical_user_id(ALLOWED))) == 2
    finally:
        store.close()


# --- choosing a conversation -------------------------------------------------


def chats_press(
    data: str, sender: int = ALLOWED, message_id: int = 600
) -> dict[str, Any]:
    """A press on the conversation list."""

    return {
        "update_id": 3,
        "callback_query": {
            "id": "cb-chats",
            "from": {"id": sender},
            "data": data,
            "message": {"chat": {"id": CHAT}, "message_id": message_id},
        },
    }


def buttons(keyboard: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (button["text"], button["callback_data"])
        for row in keyboard["inline_keyboard"]
        for button in row
    ]


def threads_of(tmp_path: Path, sender: int = ALLOWED) -> list[Any]:
    store = SqliteStore(tmp_path / "memory.sqlite3")
    try:
        return store.threads(canonical_user_id(sender))
    finally:
        store.close()


async def test_chats_offers_this_person_s_conversations_newest_first(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(says("first"), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("what is the plan"))
    await adapter.handle_update(text_update("/new"))
    await adapter.handle_update(text_update("/chats"))

    assert telegram.sent[-1] == "Conversations"
    labels = [label for label, _data in buttons(telegram.keyboards[-1])]
    assert labels == ["● New conversation", "what is the plan", "Close"]


async def test_a_long_opening_is_cut_rather_than_squeezed(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    opening = "the first sentence of a conversation that went on for a while"
    backend = ScriptedBackend(says("noted"), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update(opening))
    await adapter.handle_update(text_update("/chats"))

    label = buttons(telegram.keyboards[-1])[0][0]
    assert label.startswith("● the first sentence")
    assert label.endswith("…")
    assert len(label) <= len("● ") + LABEL_CHARS


async def test_choosing_a_conversation_sends_the_next_message_to_it(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """The acceptance the whole feature exists for."""

    backend = ScriptedBackend(
        says("first"),
        says("second"),
        says("third"),
        default=says("summary"),
    )
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("about the roadmap"))
    await adapter.handle_update(text_update("/new"))
    await adapter.handle_update(text_update("about the weather"))
    older = next(
        thread for thread in threads_of(tmp_path) if thread.opening == "about the roadmap"
    )

    await adapter.handle_update(chats_press(f"{CHATS_CALLBACK_PREFIX}{older.id}"))
    await adapter.handle_update(text_update("and one more thing"))

    store = SqliteStore(tmp_path / "memory.sqlite3")
    try:
        spoken_in_older = [
            spoken(message) for message in store.messages(older.id) if message.role == "user"
        ]
        assert spoken_in_older == ["about the roadmap", "and one more thing"]
        assert store.active_thread(canonical_user_id(ALLOWED)) == older.id
    finally:
        store.close()


async def test_choosing_remarks_the_same_message_and_reads_no_model(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """A person browsing their conversations must not be able to wake a GPU."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/chats"))
    _label, data = buttons(telegram.keyboards[-1])[0]
    telegram.calls.clear()
    await adapter.handle_update(chats_press(data))

    methods = [method for method, _payload in telegram.calls]
    assert methods == ["answerCallbackQuery", "editMessageReplyMarkup"]
    edited = dict(telegram.calls)["editMessageReplyMarkup"]
    assert edited["message_id"] == 600
    assert buttons(edited["reply_markup"])[0][0].startswith("● ")


async def test_closing_the_list_takes_the_buttons_away(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/chats"))
    await adapter.handle_update(chats_press(CHATS_CLOSE))

    edited = dict(telegram.calls)["editMessageReplyMarkup"]
    assert edited["reply_markup"] == {"inline_keyboard": []}


async def test_one_person_cannot_open_another_person_s_conversation(
    telegram: FakeTelegram, tmp_path: Path
) -> None:
    """A callback carries a thread id, which is an identifier from outside."""

    shared = TelegramSettings(token="test-token", allowed_users=f"{ALLOWED},{STRANGER}")
    backend = ScriptedBackend(says("first"), default=says("summary"))
    adapter = build(telegram, shared, tmp_path, backend)

    await adapter.handle_update(text_update("private matters"))
    [private] = threads_of(tmp_path)

    await adapter.handle_update(
        chats_press(f"{CHATS_CALLBACK_PREFIX}{private.id}", sender=STRANGER)
    )

    answered = dict(telegram.calls)["answerCallbackQuery"]
    assert "not available" in answered["text"]
    store = SqliteStore(tmp_path / "memory.sqlite3")
    try:
        assert store.active_thread(canonical_user_id(STRANGER)) is None
        assert store.threads(canonical_user_id(STRANGER)) == []
    finally:
        store.close()


async def test_new_reuses_a_conversation_nothing_was_said_in(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Otherwise the list fills with identical entries nobody can tell apart."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/new"))
    await adapter.handle_update(text_update("/new"))

    assert len(threads_of(tmp_path)) == 1
    assert telegram.sent == [
        "Started a new conversation.",
        "You are already in a new conversation.",
    ]


async def test_help_does_not_reach_the_model(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend()
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("/help"))

    assert backend.requests == []
    assert telegram.sent and "/new" in telegram.sent[0]


# --- attachments -------------------------------------------------------------


async def test_a_photo_becomes_model_input(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    telegram.files["large"] = b"\x89PNG-bytes"
    backend = ScriptedBackend(says("A picture."), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(
        {
            "message": {
                "chat": {"id": CHAT},
                "from": {"id": ALLOWED},
                "caption": "what is this",
                "photo": [{"file_id": "small"}, {"file_id": "large"}],
            }
        }
    )

    kinds = [part.kind for request in backend.requests for m in request for part in m.content]
    assert "image" in kinds
    assert "A picture." in telegram.sent


async def test_a_document_is_saved_and_named_rather_than_pasted_into_the_turn(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """The bytes never reach the model; the name and the tool do.

    A long document pasted into a turn spends the context before the model has
    decided which part of it mattered, so it is saved and read on demand.
    """

    telegram.files["doc1"] = b"%PDF-1.7 pretend"
    backend = ScriptedBackend(says("Read it."), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(
        {
            "message": {
                "chat": {"id": CHAT},
                "from": {"id": ALLOWED},
                "caption": "what does it say",
                "document": {
                    "file_id": "doc1",
                    "file_name": "notes.pdf",
                    "mime_type": "application/pdf",
                },
            }
        }
    )

    saved = list(tmp_path.rglob("notes.pdf"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"%PDF-1.7 pretend"
    sent_text = [
        part.text or ""
        for request in backend.requests
        for message in request
        for part in message.content
    ]
    assert any("notes.pdf" in text for text in sent_text)
    assert any("read_document" in text for text in sent_text)
    assert b"%PDF" not in b"".join(
        part.data or b""
        for request in backend.requests
        for message in request
        for part in message.content
    )


async def test_an_unsupported_upload_is_still_refused_before_the_model(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Accepting documents is not accepting everything.

    A format nothing here can read must fail at the door rather than land in the
    workspace, or the workspace becomes a place unopened files accumulate.
    """

    telegram.files["doc1"] = b"MZ-executable"
    backend = ScriptedBackend()
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(
        {
            "message": {
                "chat": {"id": CHAT},
                "from": {"id": ALLOWED},
                "document": {
                    "file_id": "doc1",
                    "file_name": "tool.exe",
                    "mime_type": "application/x-msdownload",
                },
            }
        }
    )

    assert backend.requests == []
    assert any("Upload refused" in sent for sent in telegram.sent)
    assert not list(tmp_path.rglob("tool.exe"))


# --- the act branch ----------------------------------------------------------


# --- robustness --------------------------------------------------------------


async def test_a_failing_turn_answers_instead_of_killing_the_bot(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(RuntimeError("model exploded"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("hello"))

    assert any("failed" in sent for sent in telegram.sent)


# --- what the person reads ---------------------------------------------------


async def test_can_is_answered_from_the_wiring_and_never_by_the_model(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """The check has to be worth trusting, so no model and no GPU touch it."""

    backend = ScriptedBackend()
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("/can"))

    assert backend.requests == []
    answer = telegram.sent[0]
    assert "use_page" in answer
    assert "image/png" in answer
    assert "Ask first: nothing" in answer


async def test_stop_records_a_request_and_reaches_no_model(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """`/stop` is recorded, not executed.

    The turn it is about is still running — in another container, deployed —
    and what ends it is the loop reading this at its next step. So the chat
    says what is true, and a message claiming the work has already stopped
    would not be.
    """

    stops = MemoryStopRequests()
    backend = ScriptedBackend()
    adapter = build(telegram, settings, tmp_path, backend, stops=stops)

    await adapter.handle_update(text_update("/stop", update_id=40))

    assert backend.requests == []
    assert await stops.requested(canonical_user_id(ALLOWED), 39) is True
    assert await stops.requested(canonical_user_id(ALLOWED), 41) is False
    assert "Stopping" in telegram.sent[0]


async def test_a_stop_ends_the_turn_that_was_already_running(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """The two halves together: `/stop` is delivered, and the loop acts on it.

    The turn here is driven after the stop is recorded rather than beside it,
    because the durable record is the whole mechanism — a running turn in
    another container reads this and nothing else.
    """

    stops = MemoryStopRequests()
    backend = ScriptedBackend(calls("list_files", path="."), says("unreachable"))
    adapter = build(telegram, settings, tmp_path, backend, stops=stops)

    await adapter.handle_update(text_update("/stop", update_id=40))
    await adapter.handle_update(text_update("list the workspace", update_id=39))

    assert "Stopped at your request." in telegram.sent
    # The tool the model asked for never ran, and the model was not asked again.
    assert len(backend.requests) == 1


async def test_a_batch_of_tool_calls_arrives_as_one_message_of_readable_labels(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """One message rather than a burst, and not one internal name in it."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend(default=says("x")))
    produced = Message(
        role="assistant",
        content=[],
        tool_calls=(
            ToolCall(id="a", name="read_file", arguments={}),
            ToolCall(id="b", name="list_files", arguments={}),
        ),
    )

    await adapter._deliver(CHAT, produced)

    assert telegram.sent == ["Reading file…\nListing files…"]
    assert "read_file" not in telegram.sent[0]
    assert "list_files" not in telegram.sent[0]


async def test_a_formatted_message_marks_its_own_headings_and_escapes_the_rest(
    telegram: FakeTelegram, settings: TelegramSettings
) -> None:
    client = TelegramClient(settings, transport=telegram.transport())
    try:
        await client.send_message(
            CHAT, Formatted.build([("Result", "wrote <b>a & b</b>")])
        )
    finally:
        await client.aclose()

    _, payload = telegram.calls[-1]
    assert payload["parse_mode"] == "HTML"
    assert payload["text"] == "<b>Result</b>\nwrote &lt;b&gt;a &amp; b&lt;/b&gt;"


async def test_a_formatted_message_too_long_to_send_whole_arrives_plain(
    telegram: FakeTelegram, settings: TelegramSettings
) -> None:
    """A cut tag makes Telegram refuse the message; unstyled text is the price."""

    client = TelegramClient(settings, transport=telegram.transport())
    long_body = "\n".join(f"line {index}" for index in range(1200))
    try:
        await client.send_message(CHAT, Formatted.build([("Result", long_body)]))
    finally:
        await client.aclose()

    assert len(telegram.sent) > 1
    assert all("parse_mode" not in payload for _, payload in telegram.calls)
    assert "<b>" not in "".join(telegram.sent)
    assert telegram.sent[0].startswith("Result\nline 0")


def test_a_long_answer_is_split_rather_than_refused() -> None:
    text = "\n".join(f"line {index}" for index in range(1200))

    pieces = split_message(text)

    assert len(pieces) > 1
    assert all(len(piece) <= MAX_MESSAGE_CHARS for piece in pieces)
    assert "".join(piece.replace("\n", "") for piece in pieces) == text.replace("\n", "")


def test_a_short_answer_is_left_alone() -> None:
    assert split_message("just this") == ["just this"]


@pytest.mark.parametrize("command", sorted(MODEL_FREE_COMMANDS))
async def test_every_command_declared_model_free_really_is(
    command: str, telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """The webhook trusts this list to decide whether to spend a GPU wake.

    That list lives in `wire.py`, next to the front door, and the commands live
    here — so without this the two drift apart silently and the bill is what
    notices. `ScriptedBackend()` has nothing scripted, so any model call raises.
    """

    backend = ScriptedBackend()
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update(command))

    assert backend.requests == []
    assert telegram.sent, f"{command} answered nothing"


# --- standing instructions ----------------------------------------------------


async def test_agents_shows_how_to_start_when_there_are_none(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/agents"))

    [said] = telegram.sent
    assert "no standing instructions" in said
    assert "Send /agents followed by" in said
    assert "as you wrote them" in said or "as you wrote it" in said


async def test_plan_on_adds_the_planning_tool_from_the_next_turn(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Asked for 2026-09-03, to tell the plan's defects from everything else's;
    off by default the same day, once the plan was measured to cost only."""

    backend = ScriptedBackend(says("ok"), says("ok again"), says("and again"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("hello"))
    offered = [tool["function"]["name"] for tool in backend.tools_seen[-1]]
    assert "todo_write" not in offered and "write_file" in offered

    await adapter.handle_update(text_update("/plan on"))
    await adapter.handle_update(text_update("hello again"))

    assert "Planning is on" in telegram.sent[-2]
    assert (tmp_path / "workspace" / ".agent" / "plan.on").is_file()
    assert "todo_write" in [tool["function"]["name"] for tool in backend.tools_seen[-1]]

    await adapter.handle_update(text_update("/plan off"))
    await adapter.handle_update(text_update("once more"))

    assert not (tmp_path / "workspace" / ".agent" / "plan.on").exists()
    assert "todo_write" not in [tool["function"]["name"] for tool in backend.tools_seen[-1]]


async def test_several_sent_files_arrive_as_one_album(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Live 2026-09-03: three files, three model calls, three messages."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    for name in ("index.html", "style.css", "script.js"):
        (workspace / name).write_text(name, encoding="utf-8")
    backend = ScriptedBackend(
        calls("send_file", paths=["index.html", "style.css", "script.js"]),
        says("Sent."),
    )
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("send me the app"))

    assert telegram.albums == [("document", ["index.html", "style.css", "script.js"])]
    assert telegram.calls.count(("sendDocument", {})) == 0


async def test_context_reports_from_the_store_without_the_model(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Asked for on 2026-09-03: `/context` says what the next request is made of."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend(says("ok")))
    await adapter.handle_update(text_update("hello there"))

    await adapter.handle_update(text_update("/context", update_id=2))

    report = telegram.sent[-1]
    assert report.startswith("What my next request in this chat is made of")
    assert "2 messages verbatim" in report
    assert "tool schemas" in report
    assert "read when it next answers" in report, "no ceiling was ever read here"
    assert needs_model(Incoming(CHAT, ALLOWED, "/context")) is False
    assert needs_model(Incoming(CHAT, ALLOWED, "/context small")) is False
    assert needs_model(Incoming(CHAT, ALLOWED, "/compact")) is True


async def test_context_size_is_a_marker_read_by_the_budget(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(says("ok"), limit=40_000)
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("/context small"))
    await adapter.handle_update(text_update("hello", update_id=2))
    await adapter.handle_update(text_update("/context", update_id=3))

    assert "Context size is small" in telegram.sent[0]
    assert (tmp_path / "workspace" / ".agent" / "context").read_text(encoding="utf-8").strip() == "small"
    assert "Size small: up to 10,000 tokens of the model's 40,000" in telegram.sent[-1]

    await adapter.handle_update(text_update("/context normal", update_id=4))

    assert not (tmp_path / "workspace" / ".agent" / "context").exists()


async def test_a_fold_during_a_turn_is_announced(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Asked for by the human, 2026-09-03: a person should hear that older
    conversation was folded, and how much, without reading a trace."""

    backend = ScriptedBackend(default=says("a summary of what was said", input_tokens=9_000), limit=10_000)
    adapter = build(telegram, settings, tmp_path, backend, policy=ContextPolicy(keep_turns=2))
    for index in range(4):
        await adapter.handle_update(text_update(f"message {index}", update_id=index + 1))

    notice = telegram.sent[-1]
    assert notice.startswith("Folded ") and "older messages into the summary" in notice
    assert "because this conversation grew past its size" in notice
    assert "the last 2 exchanges stay verbatim" in notice and "search_history" in notice
    assert telegram.sent[-2].startswith("a summary of what was sa"), "the answer comes first"


async def test_compact_folds_the_older_part_now(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(default=says("a summary of what was said"))
    adapter = build(telegram, settings, tmp_path, backend)
    for index in range(6):
        await adapter.handle_update(text_update(f"message {index}", update_id=index + 1))

    await adapter.handle_update(text_update("/compact", update_id=20))

    assert telegram.sent[-1].startswith("Folded 8 older messages")
    store = SqliteStore(str(tmp_path / "memory.sqlite3"))
    summary, through = store.summary(current_thread(store, canonical_user_id(ALLOWED)))
    assert summary == "a summary of what was said"
    assert through == 8

    await adapter.handle_update(text_update("/compact", update_id=21))

    assert telegram.sent[-1].startswith("Nothing to fold")


async def test_plan_alone_says_which_way_it_is(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/plan"))

    assert telegram.sent[-1].startswith("Planning is off (the default)")
    assert needs_model(Incoming(CHAT, ALLOWED, "/plan on")) is False


async def test_mode_careful_makes_a_change_wait_for_a_yes(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Roadmap 5b: the two modes, switched from the chat, read by the next toolbox."""

    backend = ScriptedBackend(
        calls("write_file", path="a.txt", content="one"), says("done"), says("ok")
    )
    adapter = build(telegram, settings, tmp_path, backend)
    agent = adapter.agent(canonical_user_id(ALLOWED))
    thread = current_thread(agent.store, canonical_user_id(ALLOWED))

    await adapter.handle_update(text_update("/mode"))
    assert telegram.sent[-1].startswith("Mode: full (the default)")
    assert needs_model(Incoming(CHAT, ALLOWED, "/mode careful")) is False

    await adapter.handle_update(text_update("/mode careful"))
    assert "Careful mode" in telegram.sent[-1]
    assert (tmp_path / "workspace" / ".agent" / "careful.on").is_file()
    assert agent.toolbox(thread).requires_approval("write_file")
    assert agent.toolbox(thread).requires_approval("run_command")
    assert not agent.toolbox(thread).requires_approval("read_file")

    await adapter.handle_update(text_update("write a.txt"))
    assert await agent.pending(thread) is not None, "the write waits for a yes"
    assert not (tmp_path / "workspace" / "a.txt").exists()

    await adapter.handle_update(text_update("/mode full"))
    assert "Full mode" in telegram.sent[-1]
    assert not (tmp_path / "workspace" / ".agent" / "careful.on").exists()
    assert not agent.toolbox(thread).requires_approval("write_file")


async def test_agents_set_writes_the_workspace_file_itself(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """One file, so the command and `edit_file` cannot disagree."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/agents set Отвечай по-русски и коротко."))

    saved = tmp_path / "workspace" / INSTRUCTIONS_FILE
    assert saved.read_text(encoding="utf-8") == "Отвечай по-русски и коротко."
    assert "Saved" in telegram.sent[0]


async def test_the_instructions_may_follow_the_command_on_a_new_line(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """How people actually type a multi-line instruction."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/agents set\nОтвечай по-русски.\nБез воды."))

    saved = tmp_path / "workspace" / INSTRUCTIONS_FILE
    assert saved.read_text(encoding="utf-8") == "Отвечай по-русски.\nБез воды."


async def test_the_singular_spelling_is_the_same_command(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """`/agent set …` is what a person types. On 2026-08-30 it missed the
    command, went to the model as ordinary text, and the instructions were
    never saved — with a reply that read like success."""

    backend = ScriptedBackend()
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("/agent set делай визуальную проверку"))

    assert backend.requests == []
    saved = tmp_path / "workspace" / INSTRUCTIONS_FILE
    assert saved.read_text(encoding="utf-8") == "делай визуальную проверку"


def test_both_spellings_are_model_free_at_the_front_door() -> None:
    assert needs_model(Incoming(CHAT, ALLOWED, "/agent set что-нибудь")) is False
    assert needs_model(Incoming(CHAT, ALLOWED, "/agents set что-нибудь")) is False


async def test_the_word_set_is_optional(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Anything that is not `clear` is the instructions themselves.

    The alternative silently shows help, and a person walks away believing
    they saved something that was never written — which is what happened live
    on 2026-08-30.
    """

    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/agents Отвечай по-русски."))

    saved = tmp_path / "workspace" / INSTRUCTIONS_FILE
    assert saved.read_text(encoding="utf-8") == "Отвечай по-русски."


async def test_instructions_are_stored_exactly_and_never_reach_the_model(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """`ScriptedBackend()` has nothing scripted, so any model call raises."""

    backend = ScriptedBackend()
    adapter = build(telegram, settings, tmp_path, backend)
    typed = "Всегда:\n- отвечай по-русски\n- показывай план\n\nНикогда не выдумывай факты."

    await adapter.handle_update(text_update(f"/agents set {typed}"))

    assert backend.requests == []
    saved = tmp_path / "workspace" / INSTRUCTIONS_FILE
    assert saved.read_text(encoding="utf-8") == typed


async def test_agents_set_keeps_the_case_the_person_typed(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Command dispatch lowercases; the instructions are not the command."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/agents set Always Answer In English."))

    saved = tmp_path / "workspace" / INSTRUCTIONS_FILE
    assert saved.read_text(encoding="utf-8") == "Always Answer In English."


async def test_agents_shows_what_was_saved(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/agents set Показывай план."))
    await adapter.handle_update(text_update("/agents"))

    assert "Показывай план." in telegram.sent[1]


async def test_agents_clear_removes_the_file_and_says_so(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/agents set Что-нибудь."))
    await adapter.handle_update(text_update("/agents clear"))

    assert not (tmp_path / "workspace" / INSTRUCTIONS_FILE).exists()
    assert "removed" in telegram.sent[1]
    await adapter.handle_update(text_update("/agents clear"))
    assert "had no standing instructions" in telegram.sent[2]


async def test_agents_set_with_nothing_to_save_is_refused(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/agents set    "))

    assert "Not saved" in telegram.sent[0]
    assert not (tmp_path / "workspace" / INSTRUCTIONS_FILE).exists()


async def test_saved_instructions_reach_the_next_turn(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """The whole point, end to end: written through the chat, in the prompt of
    the next message, with no restart in between."""

    backend = ScriptedBackend(says("ладно"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("/agents set Отвечай по-русски."))
    await adapter.handle_update(text_update("привет"))

    assert "Отвечай по-русски." in prompt_text(backend.requests[0])


# --- progress ----------------------------------------------------------------


async def test_a_turn_that_reaches_the_model_says_it_is_working(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Most of a cold turn is spent waiting for a GPU, where the only honest
    thing to show is that the assistant is still there."""

    backend = ScriptedBackend(says("Hello."), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("hello"))

    actions = [payload for method, payload in telegram.calls if method == "sendChatAction"]
    assert actions and actions[0]["action"] == "typing"


async def test_a_command_does_not_pretend_to_be_working(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """It answers from storage and is finished before an indicator would show."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update("/help"))

    assert [method for method, _ in telegram.calls if method == "sendChatAction"] == []


async def test_the_indicator_stops_when_the_turn_fails(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """A renewing task outliving its turn is a leak in a container about to be
    frozen, and the failing path is where that is easiest to forget."""

    backend = ScriptedBackend(RuntimeError("model exploded"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("hello"))

    running = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and not task.done()
    ]

    assert running == []
    assert any("failed" in message for message in telegram.sent)


async def test_a_page_a_tool_rendered_stays_internal(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Observation is evidence for the agent, not an adapter send decision."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend(default=says("x")))
    produced = Message(
        role="tool",
        content=[
            ContentPart(kind="text", text="notes.pdf: page 1 of 3"),
            ContentPart(kind="image", data=b"rendered-page", media_type="image/png"),
        ],
        tool_call_id="call-1",
    )

    await adapter._deliver(CHAT, produced)

    assert telegram.photos == []
    assert not any("page 1 of 3" in sent for sent in telegram.sent)


async def test_a_file_the_agent_explicitly_selected_reaches_the_person(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    adapter = build(telegram, settings, tmp_path, ScriptedBackend(default=says("x")))
    produced = Message(
        role="tool",
        content=[
            ContentPart(kind="text", text="Selected page.png for delivery."),
            ContentPart(
                kind="image",
                data=b"rendered-page",
                media_type="image/png",
                name="page.png",
                outbound=True,
            ),
        ],
        tool_call_id="send-1",
    )

    await adapter._deliver(CHAT, produced)

    assert telegram.photos == ["page.png"]
    assert not any("Selected page.png" in sent for sent in telegram.sent)


# --- the command shell -------------------------------------------------------


def test_the_native_menu_is_the_product_and_not_the_diagnostics() -> None:
    """`/check` runs every capability for real; it is not one of four choices."""

    offered = [entry.command for entry in PRODUCT_COMMANDS]

    assert offered == ["new", "chats", "can", "agents", "plan", "mode", "context", "compact", "stop", "help"]
    assert "check" not in offered
    assert all(entry.description and entry.description[0].isupper() for entry in PRODUCT_COMMANDS)
    assert len(BOT_DESCRIPTION) <= 512
    assert len(BOT_SHORT_DESCRIPTION) <= 120


async def test_check_is_absent_from_the_menu_and_still_a_working_command(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Not promoted is not removed, and it still costs no GPU."""

    backend = ScriptedBackend()
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("/check"))

    assert "check" not in [entry.command for entry in PRODUCT_COMMANDS]
    assert backend.requests == []
    assert telegram.sent and telegram.sent[0].strip()
    assert "/check" in MODEL_FREE_COMMANDS


async def test_publishing_the_profile_sends_exactly_the_product_menu(
    telegram: FakeTelegram, settings: TelegramSettings
) -> None:
    """The tool is a deployment action; this asserts what it would send."""

    client = TelegramClient(settings, transport=telegram.transport())
    try:
        await client.set_my_commands(PRODUCT_COMMANDS)
        await client.set_my_description(BOT_DESCRIPTION)
        await client.set_my_short_description(BOT_SHORT_DESCRIPTION)
    finally:
        await client.aclose()

    sent = dict(telegram.calls)
    assert [entry["command"] for entry in sent["setMyCommands"]["commands"]] == [
        "new",
        "chats",
        "can",
        "agents",
        "plan",
        "mode",
        "context",
        "compact",
        "stop",
        "help",
    ]
    assert all(entry["description"] for entry in sent["setMyCommands"]["commands"])
    assert sent["setMyDescription"]["description"] == BOT_DESCRIPTION
    assert sent["setMyShortDescription"]["short_description"] == BOT_SHORT_DESCRIPTION


@pytest.mark.parametrize("command", ["/start", "/help"])
async def test_onboarding_is_one_short_formatted_message(
    command: str, telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    adapter = build(telegram, settings, tmp_path, ScriptedBackend())

    await adapter.handle_update(text_update(command))

    assert len(telegram.sent) == 1
    _, payload = telegram.calls[-1]
    assert payload["parse_mode"] == "HTML"
    assert markdown.balanced(payload["text"])
    assert "**" not in payload["text"]
    assert len(payload["text"]) < MAX_MESSAGE_CHARS
    for entry in PRODUCT_COMMANDS:
        assert f"/{entry.command}" in payload["text"]
    # `/can` is the truthful capability source; onboarding must not become a
    # second, staler answer to the same question.
    assert "use_page" not in payload["text"]


def test_the_onboarding_card_names_every_command_the_menu_does() -> None:
    for entry in PRODUCT_COMMANDS:
        assert f"/{entry.command}" in HELP.plain
    assert "/check" in HELP.plain


# --- rich text in the chat ---------------------------------------------------


ANSWER = """## Findings

The **first** point and the *second*, with `inline_code`.

1. Ordered one
2. Ordered two

- Bulleted one
- Bulleted two

> A quoted remark.

See [the notes](https://example.com/notes).

```python
print("hello")
```
"""


async def test_an_ordinary_markdown_answer_reaches_the_chat_as_telegram_markup(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(says(ANSWER), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("tell me"))

    # The answer was previewed while it was written, so the rendered version
    # arrives as the edit that finishes that message, not as a fresh one.
    payload = [payload for method, payload in telegram.calls if method == "editMessageText"][-1]
    assert payload["parse_mode"] == "HTML"
    assert markdown.balanced(payload["text"])
    for expected in ("<b>Findings</b>", "<i>second</i>", "<code>inline_code</code>", "<pre>"):
        assert expected in payload["text"]
    assert "<blockquote>" in payload["text"]
    assert '<a href="https://example.com/notes">' in payload["text"]
    assert "**" not in payload["text"] and "##" not in payload["text"]


async def test_the_store_keeps_the_model_text_and_not_the_telegram_rendering(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Rendering is presentation. The canonical answer stays ordinary Markdown."""

    backend = ScriptedBackend(says(ANSWER), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("tell me"))

    store = SqliteStore(tmp_path / "memory.sqlite3")
    try:
        thread = store.threads(canonical_user_id(ALLOWED))[0]
        stored = "\n".join(
            part.text or ""
            for message in store.messages(thread.id)
            for part in message.content
            if message.role == "assistant"
        )
    finally:
        store.close()

    assert "## Findings" in stored
    assert "<b>" not in stored


async def test_an_answer_telegram_refuses_to_parse_arrives_complete_and_plain(
    settings: TelegramSettings, tmp_path: Path
) -> None:
    """Telegram's parser is the only authority on what Telegram accepts."""

    accepted: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        if payload.get("parse_mode") == "HTML":
            return httpx.Response(
                200,
                json={"ok": False, "description": "Bad Request: can't parse entities"},
            )
        accepted.append(payload)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = TelegramClient(settings, transport=httpx.MockTransport(handle))
    try:
        await client.send_message(CHAT, Formatted.from_markdown(ANSWER))
    finally:
        await client.aclose()

    delivered = "\n\n".join(payload["text"] for payload in accepted)
    assert accepted and all("parse_mode" not in payload for payload in accepted)
    for word in ("Findings", "first", "second", "inline_code", "Ordered two", "Bulleted two"):
        assert word in delivered
    assert "https://example.com/notes" in delivered
    assert 'print("hello")' in delivered


async def test_a_long_markdown_answer_arrives_whole_with_no_half_written_markup(
    telegram: FakeTelegram, settings: TelegramSettings
) -> None:
    client = TelegramClient(settings, transport=telegram.transport())
    long_answer = "\n\n".join(f"**Point {index}** of a long answer." for index in range(400))
    try:
        await client.send_message(CHAT, Formatted.from_markdown(long_answer))
    finally:
        await client.aclose()

    assert len(telegram.sent) > 1
    for piece in telegram.sent:
        assert len(piece) <= MAX_MESSAGE_CHARS
        assert markdown.balanced(piece)
    joined = "".join(telegram.sent)
    assert "<b>Point 0</b>" in joined and "<b>Point 399</b>" in joined


# --- tool activity -----------------------------------------------------------


async def test_every_tool_the_agent_can_call_has_a_readable_label(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """`Working…` is the safety net, not the plan for tools that already exist."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend())
    agent = adapter.agent(canonical_user_id(ALLOWED))
    thread = current_thread(agent.store, canonical_user_id(ALLOWED))

    for name in agent.toolbox(thread).names:
        assert name in TOOL_ACTIVITY, f"{name} would be shown as {UNKNOWN_ACTIVITY}"

    for name, label in TOOL_ACTIVITY.items():
        assert name not in label
        assert label.endswith("…") and label[0].isupper()


def test_an_unknown_tool_says_something_rather_than_its_name(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    from ui.telegram.adapter import activity_labels  # noqa: PLC0415

    labels = activity_labels([ToolCall(id="x", name="frobnicate_widget", arguments={})])

    assert labels == [UNKNOWN_ACTIVITY] == ["Working…"]
    assert "frobnicate" not in labels[0]


async def test_consecutive_tool_calls_reuse_one_transient_status_message(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """One message, edited — not a column of notifications left in the history."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend(default=says("x")))
    activity = ToolActivity(adapter.client, CHAT)

    await adapter._deliver(
        CHAT,
        Message(role="assistant", content=[], tool_calls=(ToolCall("a", "search_web", {}),)),
        activity,
    )
    await adapter._deliver(
        CHAT,
        Message(role="assistant", content=[], tool_calls=(ToolCall("b", "fetch_page", {}),)),
        activity,
    )
    await adapter._deliver(
        CHAT,
        Message(role="assistant", content=[ContentPart(kind="text", text="Done.")]),
        activity,
    )

    methods = [method for method, _ in telegram.calls]
    assert methods.count("sendMessage") == 2  # the status once, then the answer
    assert "editMessageText" in methods
    assert methods.index("deleteMessage") < methods.index("sendMessage", 1)
    assert telegram.sent == ["Searching the web…", "Done."]
    edited = [payload for method, payload in telegram.calls if method == "editMessageText"]
    # The second batch of calls is the loop's second step, and says so: on a
    # long turn the useful question is not only what it is doing but how far in
    # it is. The first step is not numbered, because "Step 1" of a turn that
    # reads one file is noise.
    assert edited[-1]["text"] == "Step 2 · Reading page…"


async def test_a_whole_turn_that_uses_a_tool_never_shows_its_name(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(
        calls("list_files", path="."), says("Nothing there yet."), default=says("summary")
    )
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("what is in my workspace"))

    assert "Nothing there yet." in telegram.sent
    assert not any("list_files" in sent for sent in telegram.sent)
    assert any("Listing files…" in sent for sent in telegram.sent)
    # And it does not survive the turn it belonged to.
    assert "deleteMessage" in [method for method, _ in telegram.calls]


def a_plan(*items: tuple[str, str]) -> ToolCall:
    return ToolCall(
        id="p",
        name="todo_write",
        arguments={"todos": [{"content": text, "status": status} for text, status in items]},
    )


def test_the_plan_is_read_out_of_the_call_that_carries_it() -> None:
    """It lives nowhere else: the tool stores nothing."""

    from ui.telegram.adapter import plan_lines  # noqa: PLC0415

    lines = plan_lines(
        [a_plan(("Write the page", "completed"), ("Look at it", "in_progress"))]
    )

    assert lines == ["✓ Write the page", "▸ Look at it"]


def test_a_batch_with_no_plan_in_it_says_nothing_about_the_plan() -> None:
    """`None` is not an empty plan: it must leave what is shown standing."""

    from ui.telegram.adapter import plan_lines  # noqa: PLC0415

    assert plan_lines([ToolCall("a", "write_file", {"path": "a.html"})]) is None
    assert plan_lines([a_plan()]) == []


def test_a_plan_that_is_not_the_shape_it_should_be_is_skipped_not_trusted() -> None:
    """Arguments are model output, and one live turn's arguments held fragments
    of a different call."""

    from ui.telegram.adapter import plan_lines  # noqa: PLC0415

    assert plan_lines([ToolCall("a", "todo_write", {"todos": "everything"})]) is None
    assert (
        plan_lines(
            [
                ToolCall(
                    "a",
                    "todo_write",
                    {"todos": ["a string", {"content": "Real", "status": "nonsense"}]},
                )
            ]
        )
        == ["· Real"]
    )


async def test_the_plan_rides_under_the_action_in_the_same_message(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """One message: what is happening, a blank line, then the plan. And the
    plan stays up while later steps run, because a batch that says nothing
    about it has not changed it."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend(default=says("x")))
    activity = ToolActivity(adapter.client, CHAT)

    await adapter._deliver(
        CHAT,
        Message(
            role="assistant",
            content=[],
            tool_calls=(a_plan(("Write the page", "in_progress"), ("Look at it", "pending")),),
        ),
        activity,
    )
    await adapter._deliver(
        CHAT,
        Message(role="assistant", content=[], tool_calls=(ToolCall("w", "write_file", {}),)),
        activity,
    )

    assert telegram.sent[0] == "Planning…\n\n▸ Write the page\n· Look at it"
    edited = [payload for method, payload in telegram.calls if method == "editMessageText"]
    assert edited[-1]["text"] == (
        "Step 2 · Writing file…\n\n▸ Write the page\n· Look at it"
    )


async def test_the_plan_leaves_with_the_status_and_not_into_the_answer(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """What stays in the chat after a turn is the conversation."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend(default=says("x")))
    activity = ToolActivity(adapter.client, CHAT)

    await adapter._deliver(
        CHAT,
        Message(role="assistant", content=[], tool_calls=(a_plan(("Do it", "pending")),)),
        activity,
    )
    await adapter._deliver(
        CHAT,
        Message(role="assistant", content=[ContentPart(kind="text", text="Done.")]),
        activity,
    )

    assert "deleteMessage" in [method for method, _ in telegram.calls]
    assert telegram.sent[-1] == "Done."
    assert not any("Do it" in sent for sent in telegram.sent[1:])


async def test_a_long_plan_cannot_cost_the_status_line(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Telegram refuses a message over 4096 characters, and the useful part is
    the line at the top."""

    adapter = build(telegram, settings, tmp_path, ScriptedBackend(default=says("x")))
    activity = ToolActivity(adapter.client, CHAT)

    await adapter._deliver(
        CHAT,
        Message(
            role="assistant",
            content=[],
            tool_calls=(a_plan(*[(f"Step {n} " + "x" * 300, "pending") for n in range(30)]),),
        ),
        activity,
    )

    shown = telegram.sent[0]
    assert shown.startswith("Planning…\n\n")
    assert len(shown) < 4096
    assert "18 more" in shown


async def test_a_status_that_cannot_be_sent_does_not_fail_the_turn(
    settings: TelegramSettings, tmp_path: Path
) -> None:
    """Progress chrome is the least important thing in a turn."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("sendMessage"):
            return httpx.Response(200, json={"ok": False, "description": "Forbidden"})
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = TelegramClient(settings, transport=httpx.MockTransport(handle))
    activity = ToolActivity(client, CHAT)
    try:
        await activity.show(["Searching the web…"])
        await activity.clear()
    finally:
        await client.aclose()

    assert activity.message_id is None


# --- settled inline actions --------------------------------------------------


def approval_update(data: str, message_id: int | None = 500) -> dict[str, Any]:
    message: dict[str, Any] = {"chat": {"id": CHAT}}
    if message_id is not None:
        message["message_id"] = message_id
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "from": {"id": ALLOWED},
            "data": data,
            "message": message,
        },
    }


def test_a_callback_now_carries_the_message_it_belongs_to() -> None:
    incoming = read_update(approval_update("task:yes"))

    assert incoming is not None
    assert incoming.callback_message_id == 500


def test_a_settled_button_is_model_free_at_the_front_door() -> None:
    """The webhook reads this to decide whether to spend a GPU wake."""

    for data in (SETTLED_APPROVED, SETTLED_REJECTED):
        incoming = read_update(approval_update(data))
        assert incoming is not None
        assert needs_model(incoming) is False
    assert needs_model(Incoming(CHAT, ALLOWED, "", callback_data="task:yes")) is True


def test_standing_instructions_are_model_free_even_carrying_their_text() -> None:
    """`/agents set …` writes a file and answers from it. Judged by the bare
    command alone, the front door would wake an A10 to save a sentence."""

    assert needs_model(Incoming(CHAT, ALLOWED, "/agents")) is False
    assert needs_model(Incoming(CHAT, ALLOWED, "/agents set Отвечай коротко.")) is False
    assert needs_model(Incoming(CHAT, ALLOWED, "/agents clear")) is False
    assert needs_model(Incoming(CHAT, ALLOWED, "напиши про /agents")) is True


def test_a_conversation_button_is_model_free_at_the_front_door() -> None:
    """Browsing your own conversations may not be charged to a GPU."""

    for data in (CHATS_CLOSE, f"{CHATS_CALLBACK_PREFIX}0f0e0d0c-0b0a-0908-0706-050403020100"):
        incoming = read_update(chats_press(data))
        assert incoming is not None
        assert needs_model(incoming) is False
    assert needs_model(Incoming(CHAT, ALLOWED, "/chats")) is False


def settlements(telegram: FakeTelegram) -> list[dict[str, Any]]:
    return [
        payload for method, payload in telegram.calls if method == "editMessageReplyMarkup"
    ]


# The approval that settles is now the loop's own: the model asks for a
# destructive tool, the graph stops on it, and the two buttons are that
# question. There is no second lifecycle with a plan to approve.
WRITE = "call_write_file"


def writes() -> Completion:
    return calls("write_file", path="notes.txt", content="hello")


async def test_a_rejected_call_settles_the_same_message_to_one_status_button(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(writes(), says("Not written."), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)
    await adapter.handle_update(text_update("write notes.txt"))

    await adapter.handle_update(approval_update(f"call:no:{WRITE}"))

    settled = settlements(telegram)
    assert len(settled) == 1
    assert settled[0]["message_id"] == 500
    buttons = settled[0]["reply_markup"]["inline_keyboard"]
    assert len(buttons) == 1 and len(buttons[0]) == 1
    assert buttons[0][0]["text"] == "✕ Rejected"
    assert buttons[0][0]["callback_data"] == SETTLED_REJECTED
    assert buttons[0][0]["style"] == "danger"


async def test_an_approved_call_settles_to_the_success_button(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(writes(), says("Written."), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)
    await adapter.handle_update(text_update("write notes.txt"))

    await adapter.handle_update(approval_update(f"call:yes:{WRITE}"))

    settled = settlements(telegram)
    assert settled, "an accepted approval must show as accepted"
    button = settled[-1]["reply_markup"]["inline_keyboard"][0][0]
    assert button["text"] == "✓ Approved"
    assert button["callback_data"] == SETTLED_APPROVED
    assert button["style"] == "success"
    assert settled[-1]["message_id"] == 500
    assert (tmp_path / "workspace" / "notes.txt").exists()


async def test_a_button_pressed_late_still_approves_the_call(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Found live: an approval four minutes old failed the whole turn.

    Telegram expires a callback query after a few minutes and answers a late
    press with `query is too old`. That answer is the spinner on the button,
    not the work — and the work was a person saying yes to something the agent
    is waiting to do. Losing it because the animation failed is the wrong
    trade, and after 4.0 it was worse than losing one turn: the failed update
    went back to a queue that claims it ahead of every later message.
    """

    refusals: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("answerCallbackQuery"):
            refusals.append("asked")
            return httpx.Response(
                200,
                json={
                    "ok": False,
                    "description": (
                        "Bad Request: query is too old and response timeout "
                        "expired or query ID is invalid"
                    ),
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    backend = ScriptedBackend(writes(), says("Written."), default=says("summary"))
    client = TelegramClient(settings, transport=httpx.MockTransport(handle))
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    agent_settings = AgentSettings(
        database=str(tmp_path / "memory.sqlite3"),
        checkpoints=str(tmp_path / "checkpoints.sqlite3"),
        workspace=str(workspace),
    )
    adapter = TelegramAdapter(
        client,
        settings,
        agent_settings,
        agent_factory=lambda user_id: Agent(
            backend,
            SqliteStore(agent_settings.database),
            workspace,
            checkpoints=agent_settings.checkpoints,
            user_id=user_id,
        ),
    )
    BUILT.append(adapter)

    await adapter.handle_update(text_update("write notes.txt"))
    await adapter.handle_update(approval_update(f"call:yes:{WRITE}"))

    assert refusals, "the acknowledgement was attempted"
    assert (workspace / "notes.txt").exists()


async def test_a_transition_that_failed_is_never_shown_as_settled(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """The button is evidence of a state change, not decoration over one.

    Note what this does *not* claim. Settlement follows the resume producing
    something, so a turn that resumes and then fails on its own work is still
    genuinely approved and says so. What must never settle is the transition
    itself failing, which is what is forced here.
    """

    backend = ScriptedBackend(writes(), says("Written."), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)
    await adapter.handle_update(text_update("write notes.txt"))

    async def refuse(*_arguments: Any, **_keywords: Any) -> Any:
        raise RuntimeError("the turn could not be resumed")
        yield  # pragma: no cover - never reached, but makes this a generator

    adapter.agent(canonical_user_id(ALLOWED)).resume_events = refuse

    await adapter.handle_update(approval_update(f"call:yes:{WRITE}"))

    assert settlements(telegram) == []
    assert any("failed" in sent.lower() for sent in telegram.sent)


async def test_pressing_a_settled_button_changes_nothing(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """No model, no task, no new conversation — an acknowledgement and nothing."""

    backend = ScriptedBackend()
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(approval_update(SETTLED_APPROVED))
    await adapter.handle_update(approval_update(SETTLED_REJECTED))

    assert backend.requests == []
    assert [method for method, _ in telegram.calls] == [
        "answerCallbackQuery",
        "answerCallbackQuery",
    ]
    store = SqliteStore(tmp_path / "memory.sqlite3")
    try:
        assert store.threads(canonical_user_id(ALLOWED)) == []
    finally:
        store.close()


async def test_settlement_falls_back_to_an_uncoloured_button(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """`style` is recent; the word is the state and must survive without it."""

    refusals: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        if request.url.path.endswith("editMessageReplyMarkup"):
            button = payload["reply_markup"]["inline_keyboard"][0][0]
            if "style" in button:
                refusals.append(payload)
                return httpx.Response(
                    200, json={"ok": False, "description": "Bad Request: unknown field style"}
                )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = TelegramClient(settings, transport=httpx.MockTransport(handle))
    adapter = TelegramAdapter(client, settings, AgentSettings())
    try:
        await adapter._settle(
            Incoming(CHAT, ALLOWED, "", callback_data="task:yes", callback_message_id=500),
            approved=True,
        )
    finally:
        await client.aclose()

    assert len(refusals) == 1


# --- the answer, while it is being written -----------------------------------
#
# One message that grows and then becomes the answer. Two things must never
# happen: the same answer arriving twice, and half an answer staying in the chat.

LONG_ANSWER = (
    "Paris is the capital of France and has been for a very long time indeed."
)


def texts(telegram: FakeTelegram, method: str) -> list[str]:
    return [payload["text"] for name, payload in telegram.calls if name == method]


async def test_an_answer_is_previewed_once_and_finished_in_the_same_message(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    backend = ScriptedBackend(says(LONG_ANSWER), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("tell me about paris"))

    # One message was sent — the preview — and the finished answer replaced it.
    assert len(texts(telegram, "sendMessage")) == 1
    assert LONG_ANSWER.startswith(texts(telegram, "sendMessage")[0])
    assert texts(telegram, "editMessageText")[-1] == LONG_ANSWER
    # The answer never arrives twice: nothing was sent after the preview.
    assert [name for name, _ in telegram.calls].count("sendMessage") == 1
    assert "deleteMessage" not in [name for name, _ in telegram.calls]


async def test_a_short_answer_arrives_whole_rather_than_previewed(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """A preview holding one word that is about to be replaced helps nobody."""

    backend = ScriptedBackend(says("Yes."), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("is it true"))

    assert telegram.sent == ["Yes."]
    assert texts(telegram, "editMessageText") == []


async def test_a_tool_step_previews_only_the_answer(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """A model turn that only calls a tool has nothing to show yet."""

    backend = ScriptedBackend(
        calls("list_files", path="."),
        says(LONG_ANSWER),
        default=says("summary"),
    )
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("what is here"))

    sent = texts(telegram, "sendMessage")
    # Two messages: the tool activity, then the preview that becomes the answer.
    assert sent[0] == TOOL_ACTIVITY["list_files"]
    assert len(sent) == 2
    assert LONG_ANSWER.startswith(sent[1])
    assert texts(telegram, "editMessageText")[-1] == LONG_ANSWER


async def test_text_that_comes_with_a_tool_call_is_delivered_and_not_repeated(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Live 2026-09-03: the answer was written with a send attached, withdrawn,
    and written again after it — paid for twice, watched vanishing once. What
    the model says beside a call is delivered; a verbatim repeat is not."""

    answer_with_send = Completion(
        text=LONG_ANSWER,
        tool_calls=calls("list_files", path=".").tool_calls,
        finish_reason="tool_calls",
    )
    backend = ScriptedBackend(answer_with_send, says(LONG_ANSWER), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("look and then answer"))

    finals = texts(telegram, "editMessageText")
    assert finals.count(LONG_ANSWER) == 1
    sent = texts(telegram, "sendMessage")
    assert LONG_ANSWER not in sent
    deleted = [p["message_id"] for name, p in telegram.calls if name == "deleteMessage"]
    assert 101 not in deleted  # the preview became the answer, not a deletion


async def test_new_text_after_the_tool_is_delivered_as_its_own_message(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    narrated = Completion(
        text=LONG_ANSWER,
        tool_calls=calls("list_files", path=".").tool_calls,
        finish_reason="tool_calls",
    )
    final = "The actual final answer is complete and long enough to stream once."
    backend = ScriptedBackend(narrated, says(final), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)

    await adapter.handle_update(text_update("look and then answer"))

    finals = texts(telegram, "editMessageText")
    assert LONG_ANSWER in finals and final in finals


async def test_a_steered_draft_is_edited_into_the_real_answer(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Live 2026-09-03: the draft vanished, the plan closed, the same text came
    back as a new bubble. The draft stays on the screen and is edited in
    place when the model writes something new."""

    class SteersOnce:
        def __init__(self) -> None:
            self.asked = 0

        async def stopping(self, candidate: Candidate) -> Steering | None:
            self.asked += 1
            return Steering("check it first", source="test") if self.asked == 1 else None

    final = "The actual final answer is complete and long enough to stream once."
    backend = ScriptedBackend(says(LONG_ANSWER), says(final), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend, stopping=SteersOnce())

    await adapter.handle_update(text_update("do it properly"))

    deleted = [p["message_id"] for name, p in telegram.calls if name == "deleteMessage"]
    assert 101 not in deleted
    assert texts(telegram, "editMessageText")[-1] == final
    assert [p["message_id"] for name, p in telegram.calls if name == "editMessageText"][-1] == 101


async def test_a_held_draft_survives_the_tool_call_the_steering_asked_for(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Live 2026-09-03, run `af276ed7`: the draft was held, then the model's
    bare todo_write arrived, and the bare-call path deleted the held bubble;
    the same answer then came as a new one. The held draft waits."""

    class SteersOnce:
        def __init__(self) -> None:
            self.asked = 0

        async def stopping(self, candidate: Candidate) -> Steering | None:
            self.asked += 1
            return Steering("check it first", source="test") if self.asked == 1 else None

    backend = ScriptedBackend(
        says(LONG_ANSWER), calls("list_files", path="."), says(LONG_ANSWER), default=says("summary")
    )
    adapter = build(telegram, settings, tmp_path, backend, stopping=SteersOnce())

    await adapter.handle_update(text_update("do it properly"))

    deleted = [p["message_id"] for name, p in telegram.calls if name == "deleteMessage"]
    assert 101 not in deleted
    assert [p["message_id"] for name, p in telegram.calls if name == "editMessageText"][-1] == 101
    assert texts(telegram, "editMessageText")[-1] == LONG_ANSWER


async def test_a_steered_draft_the_model_does_not_change_is_the_answer(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    class SteersOnce:
        def __init__(self) -> None:
            self.asked = 0

        async def stopping(self, candidate: Candidate) -> Steering | None:
            self.asked += 1
            return Steering("check it first", source="test") if self.asked == 1 else None

    backend = ScriptedBackend(says(LONG_ANSWER), says(""), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend, stopping=SteersOnce())

    await adapter.handle_update(text_update("do it properly"))

    deleted = [p["message_id"] for name, p in telegram.calls if name == "deleteMessage"]
    assert 101 not in deleted
    assert texts(telegram, "editMessageText")[-1] == LONG_ANSWER
    assert LONG_ANSWER not in texts(telegram, "sendMessage")[1:]


async def test_a_failed_final_edit_delivers_the_answer_and_clears_the_preview(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    """Falling back must not leave a truncated answer sitting above the real one."""

    calls_seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        calls_seen.append(method)
        payload = json.loads(request.content or b"{}")
        if method == "editMessageText":
            return httpx.Response(
                200, json={"ok": False, "description": "Bad Request: message to edit not found"}
            )
        if method == "sendMessage":
            telegram.sent.append(payload["text"])
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 501}})
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = TelegramClient(settings, transport=httpx.MockTransport(handle))
    backend = ScriptedBackend(says(LONG_ANSWER), default=says("summary"))
    adapter = build(telegram, settings, tmp_path, backend)
    await adapter.client.aclose()
    adapter.client = client

    await adapter.handle_update(text_update("tell me about paris"))

    assert telegram.sent[-1] == LONG_ANSWER
    assert "deleteMessage" in calls_seen


async def test_the_preview_is_edited_on_a_throttle_not_on_every_delta(
    telegram: FakeTelegram, settings: TelegramSettings
) -> None:
    """One edit per token would be refused long before it would be readable."""

    clock = [0.0]
    client = TelegramClient(settings, transport=telegram.transport())
    preview = AnswerPreview(client, CHAT, interval=1.0, now=lambda: clock[0])
    try:
        for index in range(40):
            clock[0] += 0.1
            await preview.add(f"word{index} ")
    finally:
        await client.aclose()

    # Four seconds of deltas: one message, then an edit per whole second, rather
    # than forty edits. What is shown lags the text but is always a prefix of it,
    # and the deltas after the last edit arrive when the answer is finished.
    assert len(texts(telegram, "sendMessage")) == 1
    assert len(texts(telegram, "editMessageText")) == 3
    shown = texts(telegram, "editMessageText")[-1]
    assert preview.text.startswith(shown)
    assert "word39 " in preview.text and "word39" not in shown


async def test_a_preview_that_cannot_be_sent_stands_aside(
    telegram: FakeTelegram, settings: TelegramSettings
) -> None:
    """A cosmetic failure must not become a failed turn."""

    attempts: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"ok": False, "description": "Bad Request: chat not found"})

    client = TelegramClient(settings, transport=httpx.MockTransport(handle))
    preview = AnswerPreview(client, CHAT)
    try:
        for _ in range(5):
            assert await preview.add("a long enough piece of text ") is False
        assert await preview.finish("the answer") is False
    finally:
        await client.aclose()

    # Tried once, then stopped trying: the turn is answered the ordinary way.
    assert attempts == ["sendMessage"]


# --- a turn a dead worker left is taken up by the same update ---------------------------
#
# Roadmap 4.7. The queue hands the update a dead worker was answering back
# first; the adapter must take the checkpointed turn up rather than start it
# again with every tool run twice, and must start afresh for any other message.


async def test_the_same_message_again_takes_the_interrupted_turn_up(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    dying = ScriptedBackend(
        calls("write_file", path="page.html", content="<p>hi</p>"),
        RuntimeError("the worker died"),
    )
    first = build(telegram, settings, tmp_path, dying)
    await first.handle_update(text_update("make a page", update_id=1))
    assert any("That request failed" in text for text in telegram.sent)

    later = ScriptedBackend(says("Done."), default=says("summary"))
    second = build(telegram, settings, tmp_path, later)
    await second.handle_update(text_update("make a page", update_id=1))

    assert "Done." in telegram.sent
    request = later.requests[0]
    assert [m.role for m in request][-3:] == ["user", "assistant", "tool"], "continued, not restarted"
    assert request[-2].tool_calls[0].name == "write_file"
    store = SqliteStore(str(tmp_path / "memory.sqlite3"))
    thread = store.active_thread(canonical_user_id(ALLOWED))
    stored = store.messages(thread)
    assert [m.role for m in stored] == ["user", "assistant", "tool", "assistant"]
    assert sum(1 for m in stored if m.role == "user") == 1


async def test_a_different_message_starts_afresh_after_an_interrupted_turn(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    dying = ScriptedBackend(
        calls("write_file", path="page.html", content="<p>hi</p>"),
        RuntimeError("the worker died"),
    )
    first = build(telegram, settings, tmp_path, dying)
    await first.handle_update(text_update("make a page", update_id=1))

    later = ScriptedBackend(says("Fresh."), default=says("summary"))
    second = build(telegram, settings, tmp_path, later)
    await second.handle_update(text_update("something else", update_id=2))

    assert "Fresh." in telegram.sent
    assert not any(m.role == "tool" for m in later.requests[0])
    store = SqliteStore(str(tmp_path / "memory.sqlite3"))
    stored = store.messages(store.active_thread(canonical_user_id(ALLOWED)))
    assert [prompt_text([m]) for m in stored] == ["something else", "Fresh."]


async def test_giving_up_is_said_to_the_person(
    telegram: FakeTelegram, settings: TelegramSettings, tmp_path: Path
) -> None:
    adapter = build(telegram, settings, tmp_path, ScriptedBackend())
    await adapter.give_up(text_update("make a page"), 3)
    assert any("interrupted 3 times" in text for text in telegram.sent)


def test_the_adapter_passes_the_runner_it_was_started_with(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployed worker hands over the Function beside the renderer; a
    command must never run in the worker's own container."""

    import ui.telegram.adapter as module

    seen: dict[str, object] = {}

    def recording(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(module, "create_agent", recording)
    marker = object()

    TelegramAdapter(
        TelegramClient(TelegramSettings(), transport=FakeTelegram().transport()),
        TelegramSettings(),
        AgentSettings(),
        runner=marker,
    )._default_agent("someone")

    assert seen["runner"] is marker
