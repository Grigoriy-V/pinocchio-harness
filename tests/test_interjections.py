"""A message the person sends while a turn runs (item 20, 2026-09-07).

It enters the running turn at the tools boundary — after the batch's results,
before the next model request — as the person's own words, is stored there,
and is never sent back to them as an answer. What arrived before the turn, or
during its final answer, is not the turn's: it waits for its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.interjections import MemoryInterjections
from app.agent.runtime import Agent, MessageProduced, MessageTaken
from app.agent.stop import MemoryStopRequests
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import Message
from tests.fakes import ScriptedBackend, body, calls, says, user

OWNER = LOCAL_USER_ID


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    room = tmp_path / "workspace"
    room.mkdir()
    (room / "a.txt").write_text("alpha", encoding="utf-8")
    (room / "b.txt").write_text("beta", encoding="utf-8")
    return room


def agent_with(
    workspace: Path, backend: ScriptedBackend, lane: MemoryInterjections, **kwargs
) -> Agent:
    return Agent(backend, SqliteStore(), workspace, interjections=lane, **kwargs)


def roles(messages: list[Message]) -> list[str]:
    return [message.role for message in messages]


async def test_a_message_sent_during_a_batch_enters_after_its_results(workspace: Path) -> None:
    lane = MemoryInterjections()
    await lane.offer(OWNER, 11, user("also check b.txt"))
    backend = ScriptedBackend(
        calls("read_file", path="a.txt"), calls("read_file", path="b.txt"), says("done")
    )
    agent = agent_with(workspace, backend, lane)

    events = [event async for event in agent.events("t1", user("read a.txt"), sequence=10)]

    # The second request carries the batch, then the person's words, verbatim
    # and unframed, before the model's next step.
    second = backend.requests[1]
    assert roles(second[-3:]) == ["assistant", "tool", "user"]
    assert body(second[-1]) == "also check b.txt"
    # Stored in that position, as the conversation happened.
    assert roles(agent.history("t1")) == ["user", "assistant", "tool", "user", "assistant", "tool", "assistant"]
    assert body(agent.history("t1")[3]) == "also check b.txt"
    # Reported to the interface as taken, never as an answer to deliver.
    taken = [event.message for event in events if isinstance(event, MessageTaken)]
    assert [body(message) for message in taken] == ["also check b.txt"]
    assert not any(
        isinstance(event, MessageProduced) and event.message.role == "user" for event in events
    )
    # Taken once: a later turn finds nothing.
    assert await lane.take(OWNER, 10) == []


async def test_everything_pending_is_taken_at_once_in_order(workspace: Path) -> None:
    """A thought that arrives as three short messages is read as one."""

    lane = MemoryInterjections()
    await lane.offer(OWNER, 12, user("two"))
    await lane.offer(OWNER, 11, user("one"))
    await lane.offer(OWNER, 13, user("three"))
    backend = ScriptedBackend(calls("read_file", path="a.txt"), says("done"))
    agent = agent_with(workspace, backend, lane)

    await agent.answer("t1", user("go"), sequence=10)

    tail = backend.requests[1][-4:]
    assert roles(tail) == ["tool", "user", "user", "user"]
    assert [body(message) for message in tail[1:]] == ["one", "two", "three"]


async def test_a_message_from_before_the_turn_is_not_the_turns(workspace: Path) -> None:
    lane = MemoryInterjections()
    await lane.offer(OWNER, 9, user("earlier"))
    backend = ScriptedBackend(calls("read_file", path="a.txt"), says("done"))
    agent = agent_with(workspace, backend, lane)

    await agent.answer("t1", user("go"), sequence=10)

    assert not any(body(message) == "earlier" for request in backend.requests for message in request)
    assert await lane.settle(OWNER, 9) is False


async def test_a_message_during_the_final_answer_waits_for_its_own_turn(workspace: Path) -> None:
    """No tools ran, so there was no boundary to read it at; it is answered
    next, in order, as today."""

    lane = MemoryInterjections()
    await lane.offer(OWNER, 11, user("and this"))
    backend = ScriptedBackend(says("done"))
    agent = agent_with(workspace, backend, lane)

    await agent.answer("t1", user("go"), sequence=10)

    assert roles(agent.history("t1")) == ["user", "assistant"]
    assert await lane.settle(OWNER, 11) is False


async def test_a_stop_after_the_message_still_stops_and_leaves_it(workspace: Path) -> None:
    lane = MemoryInterjections()
    stops = MemoryStopRequests()
    await lane.offer(OWNER, 11, user("by the way"))
    await stops.request(OWNER, 12)
    backend = ScriptedBackend(default=calls("read_file", path="a.txt"))
    agent = agent_with(workspace, backend, lane, stops=stops)

    produced = await agent.answer("t1", user("go"), sequence=10)

    assert body(produced[-1]) == "Stopped at your request."
    # Nothing ran, so nothing was read: the message gets its own turn.
    assert await lane.settle(OWNER, 11) is False


async def test_the_lane_never_fails_a_turn(workspace: Path) -> None:
    class Broken:
        async def take(self, key: str, since: int) -> list[Message]:
            raise RuntimeError("database away")

    backend = ScriptedBackend(calls("read_file", path="a.txt"), says("done"))
    agent = Agent(backend, SqliteStore(), workspace, interjections=Broken())

    produced = await agent.answer("t1", user("go"), sequence=10)

    assert body(produced[-1]) == "done"


# --- the memory lane's own contract -------------------------------------------


async def test_settle_says_whether_a_turn_took_the_message() -> None:
    lane = MemoryInterjections()
    await lane.offer(OWNER, 11, user("taken"))
    await lane.offer(OWNER, 12, user("withdrawn"))

    assert [body(m) for m in await lane.take(OWNER, 11)] == ["withdrawn"]
    assert await lane.settle(OWNER, 12) is True
    assert await lane.settle(OWNER, 12) is False, "forgotten once answered"
    assert await lane.settle(OWNER, 11) is False
    assert await lane.take(OWNER, 0) == [], "a withdrawn offer cannot be taken later"


async def test_people_do_not_share_a_lane() -> None:
    lane = MemoryInterjections()
    await lane.offer("alice", 11, user("mine"))
    assert await lane.take("bob", 0) == []
    assert [body(m) for m in await lane.take("alice", 0)] == ["mine"]
