"""What bounds one turn of the loop: its health, and the person.

The loop's only ordinary exit is the model answering without asking for a tool.
Since 2026-09-07 there is no ceiling on steps, calls or seconds: a long turn
is asked whether it is on track and decides for itself, and a turn the person
no longer wants is stopped by them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.graph import HEALTH_QUESTION, TurnWatch, build_agent
from app.agent.stop import NO_STOPS, MemoryStopRequests
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import Completion, ContentPart, Message, ToolCall
from app.tools import Tool, Toolbox
from tests.fakes import ScriptedBackend, calls, says

OWNER = LOCAL_USER_ID


@pytest.fixture
def store() -> SqliteStore:
    with SqliteStore() as store:
        yield store


def ping(recorded: list[str] | None = None) -> Tool:
    def run(n: int = 0) -> str:
        if recorded is not None:
            recorded.append("ran")
        return "pong"

    # `n` lets a script ask for a different ping each time, so the identical-
    # success guard (ISS-0019) is not what ends the turn.
    return Tool(
        name="ping",
        description="answer",
        parameters={"type": "object", "properties": {"n": {"type": "integer"}}},
        run=run,
    )


def loop(
    backend: ScriptedBackend,
    store: SqliteStore,
    tools: list[Tool] | None = None,
    watch: TurnWatch | None = None,
    stops=NO_STOPS,
):
    return build_agent(
        backend,
        Toolbox(tools if tools is not None else [ping()]),
        store,
        OWNER,
        watch=watch,
        stops=stops,
    )


def ask(text: str = "go", sequence: int = 10) -> dict[str, object]:
    return {
        "messages": [Message(role="user", content=[ContentPart(kind="text", text=text)])],
        "sequence": sequence,
    }


def spoken(message: Message) -> str:
    return " ".join(part.text or "" for part in message.content)


# --- the watch ---------------------------------------------------------------


class Clock:
    """A monotonic clock that moves a fixed amount every time it is read."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def monotonic(self) -> float:
        value = self.now
        self.now += self.step
        return value


def freeze(monkeypatch: pytest.MonkeyPatch, step: float) -> Clock:
    from types import SimpleNamespace

    clock = Clock(step)
    monkeypatch.setattr("app.agent.graph.time", SimpleNamespace(monotonic=clock.monotonic))
    return clock


def asked(request: list[Message]) -> bool:
    last = request[-1]
    return last.role == "user" and HEALTH_QUESTION[:30] in spoken(last)


async def test_a_working_turn_is_never_ended_by_a_count(store: SqliteStore) -> None:
    """Thirty tool steps, no ceiling: the loop ends when the model does."""

    ran: list[str] = []
    backend = ScriptedBackend(*[calls("ping", n=index) for index in range(30)], says("done"))
    agent = loop(backend, store, [ping(ran)])

    result = await agent.ainvoke(ask(), config={"recursion_limit": 1000})

    assert len(ran) == 30
    assert result.get("stopping", "") == ""
    assert spoken(result["messages"][-1]) == "done"
    assert not any(asked(request) for request in backend.requests)


async def test_a_long_turn_is_asked_whether_it_is_on_track(
    store: SqliteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each node reads the clock twice, so a step is 3 s of model and 3 s of
    tools; with a 10 s interval the question follows the second and the fourth
    batch of results, not every one."""

    freeze(monkeypatch, step=3.0)
    backend = ScriptedBackend(*[calls("ping", n=index) for index in range(4)], says("done"))
    agent = loop(backend, store, watch=TurnWatch(check_seconds=10.0))

    result = await agent.ainvoke(ask())

    assert [asked(request) for request in backend.requests] == [False, False, True, False, True]
    assert result.get("checked_seconds", 0.0) >= 20.0
    # The question is turn control, not conversation: nothing of it is stored.
    assert not any(asked([message]) for message in result["messages"])
    assert spoken(result["messages"][-1]) == "done"


async def test_the_question_names_the_minutes(
    store: SqliteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch, step=90.0)
    backend = ScriptedBackend(calls("ping"), says("done"))
    agent = loop(backend, store, watch=TurnWatch(check_seconds=60.0))

    await agent.ainvoke(ask())

    question = spoken(backend.requests[-1][-1])
    assert "Turn control (not from the user)" in question
    assert "working for 3 minutes" in question


async def test_the_model_may_answer_the_question_by_stopping(
    store: SqliteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decision is the model's: text after the question ends the turn."""

    freeze(monkeypatch, step=30.0)
    backend = ScriptedBackend(calls("ping"), says("Half done; I will stop here."))
    agent = loop(backend, store, watch=TurnWatch(check_seconds=1.0))

    result = await agent.ainvoke(ask())

    assert asked(backend.requests[-1])
    assert spoken(result["messages"][-1]) == "Half done; I will stop here."
    assert result.get("steered") is None


async def test_zero_asks_never(store: SqliteStore, monkeypatch: pytest.MonkeyPatch) -> None:
    freeze(monkeypatch, step=1000.0)
    backend = ScriptedBackend(calls("ping"), calls("ping", n=2), says("done"))
    agent = loop(backend, store, watch=TurnWatch(check_seconds=0.0))

    await agent.ainvoke(ask())

    assert not any(asked(request) for request in backend.requests)


async def test_an_ordinary_turn_starts_the_next_one_fresh(store: SqliteStore) -> None:
    """The counters are reset per turn, or the second one starts already asked."""

    backend = ScriptedBackend(default=says("done"))
    agent = loop(backend, store)

    first = await agent.ainvoke(ask("one"))
    second = await agent.ainvoke(ask("two"))

    assert first["steps"] == second["steps"] == 1
    assert second.get("checked_seconds", 0.0) == 0.0
    assert second.get("stopping", "") == ""


# --- the stop ----------------------------------------------------------------


async def test_a_stop_between_steps_ends_the_turn(store: SqliteStore) -> None:
    ran: list[str] = []
    stops = MemoryStopRequests()
    await stops.request(OWNER, 11)  # after the turn's own sequence of 10
    backend = ScriptedBackend(default=calls("ping"))
    agent = loop(backend, store, [ping(ran)], stops=stops)

    result = await agent.ainvoke(ask(sequence=10))

    assert result.get("stopping", "") == "stopped"
    assert ran == [], "nothing may run after the person has said stop"
    assert spoken(result["messages"][-1]) == "Stopped at your request."


async def test_a_stopped_turn_does_not_pay_for_another_model_call(
    store: SqliteStore,
) -> None:
    """Someone who asked for the work to end is not asking for one more request."""

    stops = MemoryStopRequests()
    await stops.request(OWNER, 11)
    backend = ScriptedBackend(default=calls("ping"))
    agent = loop(backend, store, stops=stops)

    await agent.ainvoke(ask(sequence=10))

    assert len(backend.requests) == 1


async def test_a_stop_from_before_this_turn_does_not_stop_it(
    store: SqliteStore,
) -> None:
    """The trap a plain flag has: an unconsumed stop cancelling the next turn.

    The number a stop arrives with is what tells the two apart, and every
    interface has one — Telegram's update id, a session's own counter.
    """

    ran: list[str] = []
    stops = MemoryStopRequests()
    await stops.request(OWNER, 9)
    backend = ScriptedBackend(calls("ping"), says("done"))
    agent = loop(backend, store, [ping(ran)], stops=stops)

    result = await agent.ainvoke(ask(sequence=10))

    assert result.get("stopping", "") == ""
    assert ran == ["ran"]


async def test_one_person_s_stop_does_not_end_another_person_s_turn(
    store: SqliteStore,
) -> None:
    stops = MemoryStopRequests()
    await stops.request("somebody-else", 99)
    backend = ScriptedBackend(calls("ping"), says("done"))
    agent = loop(backend, store, stops=stops)

    result = await agent.ainvoke(ask(sequence=10))

    assert result.get("stopping", "") == ""


async def test_a_stop_channel_that_fails_does_not_fail_the_turn(
    store: SqliteStore,
) -> None:
    """The turn this protects is the expensive half of the product."""

    class Broken:
        async def request(self, key: str, sequence: int) -> None:
            raise RuntimeError("the control plane is down")

        async def requested(self, key: str, since: int) -> bool:
            raise RuntimeError("the control plane is down")

    backend = ScriptedBackend(calls("ping"), says("done"))
    agent = loop(backend, store, stops=Broken())

    result = await agent.ainvoke(ask())

    assert spoken(result["messages"][-1]) == "done"


async def test_a_stopped_turn_is_a_history_the_next_request_can_be_built_from(
    store: SqliteStore,
) -> None:
    """Every call the model made has a result, including the ones never run."""

    stops = MemoryStopRequests()
    await stops.request(OWNER, 11)
    backend = ScriptedBackend(default=calls("ping"))
    agent = loop(backend, store, stops=stops)

    result = await agent.ainvoke(ask(sequence=10))

    asked = [call.id for message in result["messages"] for call in message.tool_calls]
    answered = [
        message.tool_call_id for message in result["messages"] if message.role == "tool"
    ]
    assert asked and asked == answered


# --- where a stop is recorded ------------------------------------------------


async def test_a_stop_applies_to_everything_that_began_before_it() -> None:
    stops = MemoryStopRequests()

    await stops.request("owner", 5)

    assert await stops.requested("owner", 4) is True
    assert await stops.requested("owner", 5) is False
    assert await stops.requested("owner", 6) is False


async def test_a_second_stop_never_moves_the_mark_backwards() -> None:
    """Two stops in quick succession are one intention, not an undo."""

    stops = MemoryStopRequests()

    await stops.request("owner", 5)
    await stops.request("owner", 3)

    assert await stops.requested("owner", 4) is True


async def test_nothing_is_stopped_when_there_is_no_way_to_ask() -> None:
    assert await NO_STOPS.requested("owner", 0) is False
    assert await NO_STOPS.request("owner", 1) is None
