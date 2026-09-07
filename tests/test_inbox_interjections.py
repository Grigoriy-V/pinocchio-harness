"""The deployed lane: inbox rows become the running turn's messages.

The SQL half is in `tests/test_update_inbox_contract.py` (live only). This is
the other half: what the lane does with the rows it took.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models import ContentPart, Message
from ui.telegram.interjections import InboxInterjections
from ui.telegram.wire import Incoming

KEY = "person-alice"


class FakeInbox:
    def __init__(self, *rows: dict[str, Any]) -> None:
        self.rows = list(rows)
        self.released: list[int] = []

    async def take_pending(self, conversation_key: str, after: int) -> list[dict[str, Any]]:
        assert conversation_key == KEY
        return [row for row in self.rows if row["update_id"] > after]

    async def release(self, update_id: int) -> None:
        self.released.append(update_id)


class FakeAdapter:
    def __init__(self, refuse: str = "") -> None:
        self.refuse = refuse

    def agent(self, key: str) -> Any:
        class Grant:
            root = Path(".")

        class Agent:
            capability_grant = Grant()

        return Agent()

    async def to_message(self, incoming: Incoming, workspace: Path) -> Message:
        if incoming.text == self.refuse:
            raise RuntimeError("download failed")
        return Message(role="user", content=[ContentPart(kind="text", text=incoming.text)])


def row(update_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "run_id": f"run-{update_id}",
        "payload": {
            "update_id": update_id,
            "message": {"chat": {"id": 1}, "from": {"id": 42}, "text": text},
        },
    }


def callback_row(update_id: int) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "run_id": "",
        "payload": {
            "update_id": update_id,
            "callback_query": {
                "id": "cb",
                "data": "x",
                "from": {"id": 42},
                "message": {"chat": {"id": 1}, "message_id": 5},
            },
        },
    }


def text(message: Message) -> str:
    return "".join(part.text or "" for part in message.content)


async def test_taken_rows_become_messages_oldest_first() -> None:
    lane = InboxInterjections(FakeInbox(row(12, "two"), row(11, "one")), FakeAdapter())
    assert [text(m) for m in await lane.take(KEY, 10)] == ["one", "two"]


async def test_a_row_whose_file_cannot_be_read_goes_back_to_the_queue() -> None:
    inbox = FakeInbox(row(11, "fine"), row(12, "broken"))
    lane = InboxInterjections(inbox, FakeAdapter(refuse="broken"))
    assert [text(m) for m in await lane.take(KEY, 10)] == ["fine"]
    assert inbox.released == [12]


async def test_a_button_press_is_not_a_message_and_is_put_back() -> None:
    inbox = FakeInbox(callback_row(11))
    lane = InboxInterjections(inbox, FakeAdapter())
    assert await lane.take(KEY, 10) == []
    assert inbox.released == [11]
