"""The local transport's own ordering, which is the deployed queue's twin.

Polling serializes one chat with an in-process lock where the deployed profile
serializes it with a database lease. Both must make the same exception for the
updates that exist to act on what is already running.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.telemetry import Telemetry
from ui.telegram.run import PollingBot

CHAT = 1000
PERSON = 4242


class RecordingAdapter:
    """Just enough adapter for the bot: it records, and it can be made to wait."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.telemetry = Telemetry(None)
        self.client = None
        self.release = asyncio.Event()
        self.hold: str | None = None

    async def handle_update(self, update: dict[str, Any], trace: Any = None) -> None:
        text = update["message"]["text"]
        self.seen.append(text)
        if text == self.hold:
            await self.release.wait()

    async def offer(self, update: dict[str, Any]) -> tuple[str, int] | None:
        return None

    async def taken(self, key: str, sequence: int) -> bool:
        return False


def message(text: str, update_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": CHAT}, "from": {"id": PERSON}, "text": text},
    }


async def test_a_stop_is_handled_while_the_turn_it_stops_is_still_running() -> None:
    adapter = RecordingAdapter()
    adapter.hold = "a long question"
    bot = PollingBot(adapter)  # type: ignore[arg-type]

    turn = asyncio.create_task(bot._guarded(message("a long question", 1)))
    await asyncio.sleep(0)
    await bot._guarded(message("/stop", 2))

    assert adapter.seen == ["a long question", "/stop"]
    adapter.release.set()
    await turn


async def test_an_ordinary_message_still_waits_for_the_one_before_it() -> None:
    """The exception is for control, not for everything."""

    adapter = RecordingAdapter()
    adapter.hold = "first"
    bot = PollingBot(adapter)  # type: ignore[arg-type]

    turn = asyncio.create_task(bot._guarded(message("first", 1)))
    await asyncio.sleep(0)
    second = asyncio.create_task(bot._guarded(message("second", 2)))
    await asyncio.sleep(0)

    assert adapter.seen == ["first"]
    adapter.release.set()
    await asyncio.gather(turn, second)
    assert adapter.seen == ["first", "second"]


# --- a message during a turn is offered to it (item 20) -----------------------


class OfferingAdapter(RecordingAdapter):
    """The adapter with the memory lane: offers, and answers whether a turn took it."""

    def __init__(self) -> None:
        super().__init__()
        self.offered: list[tuple[str, int]] = []
        self.taken_by_turn: set[int] = set()

    async def offer(self, update: dict[str, Any]) -> tuple[str, int] | None:
        text = update["message"]["text"]
        if text.startswith("/"):
            return None
        key = ("alice", int(update["update_id"]))
        self.offered.append(key)
        return key

    async def taken(self, key: str, sequence: int) -> bool:
        return sequence in self.taken_by_turn


async def test_a_message_during_a_turn_is_offered_before_it_waits() -> None:
    adapter = OfferingAdapter()
    adapter.hold = "first"
    bot = PollingBot(adapter)  # type: ignore[arg-type]

    turn = asyncio.create_task(bot._guarded(message("first", 1)))
    await asyncio.sleep(0)
    second = asyncio.create_task(bot._guarded(message("a comment", 2)))
    await asyncio.sleep(0)

    assert adapter.offered == [("alice", 1), ("alice", 2)]
    assert adapter.seen == ["first"], "still waiting for the conversation"
    # The running turn read it at its next step.
    adapter.taken_by_turn.add(2)
    adapter.release.set()
    await asyncio.gather(turn, second)
    assert adapter.seen == ["first"], "a message the turn read is not answered again"


async def test_a_message_the_turn_did_not_read_is_answered_as_its_own() -> None:
    adapter = OfferingAdapter()
    adapter.hold = "first"
    bot = PollingBot(adapter)  # type: ignore[arg-type]

    turn = asyncio.create_task(bot._guarded(message("first", 1)))
    await asyncio.sleep(0)
    second = asyncio.create_task(bot._guarded(message("a comment", 2)))
    await asyncio.sleep(0)
    adapter.release.set()
    await asyncio.gather(turn, second)

    assert adapter.seen == ["first", "a comment"]
