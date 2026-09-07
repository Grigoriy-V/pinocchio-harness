"""The agent's own plan: what it accepts, where it lives, and what it refuses.

Three subjects, in the order they depend on each other. The tool validates one
whole list and stores nothing. The projection folds the current list back out of
the turn's messages, which is the only place it ever lived. The stopping
extension reads that list and objects, once, to a turn that ends with items the
model itself left open.

Everything runs against a scripted backend and a temporary store. No network, no
model endpoint, no worker.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.agent.graph import build_agent
from app.agent.stopping import Candidate
from app.agent.todo import INSTRUCTION, FinishesItsOwnList
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import ContentPart, Message, ToolCall
from app.tools import Toolbox, filesystem_tools, todo_tools
from app.tools.todo import (
    MAX_CONTENT_CHARS,
    MAX_ITEMS,
    TOOL_NAME,
    ToolError,
    current,
    normalise,
    unfinished,
)
from tests.fakes import ScriptedBackend, calls, says

OWNER = LOCAL_USER_ID


@pytest.fixture
def store() -> SqliteStore:
    with SqliteStore() as store:
        yield store


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    room = tmp_path / "workspace"
    room.mkdir()
    return room


def item(content: str, status: str = "pending") -> dict[str, str]:
    return {"content": content, "status": status}


def spoken(message: Message) -> str:
    return " ".join(part.text or "" for part in message.content)


def wrote(*items: dict[str, str], call_id: str = "call-1") -> Message:
    """The assistant message a `todo_write` call actually is."""

    return Message(
        role="assistant",
        content=[],
        tool_calls=[
            ToolCall(id=call_id, name=TOOL_NAME, arguments={"todos": list(items)})
        ],
    )


def acknowledged(call_id: str = "call-1", text: str = "Updated todo list.") -> Message:
    return Message(
        role="tool", content=[ContentPart(kind="text", text=text)], tool_call_id=call_id
    )


def run(**arguments: object) -> str:
    box = Toolbox(todo_tools())
    return spoken(box.run(ToolCall(id="c", name=TOOL_NAME, arguments=arguments)))


def ask(text: str = "go") -> dict[str, object]:
    return {
        "messages": [Message(role="user", content=[ContentPart(kind="text", text=text)])],
        "sequence": 10,
    }


# --- one whole list, or a refusal --------------------------------------------


def test_a_list_is_accepted_and_counted_back() -> None:
    answer = run(
        todos=[item("write the page", "completed"), item("look at it", "in_progress")]
    )

    assert answer == "Updated todo list: 0 pending, 1 in progress, 1 completed."


def test_an_empty_list_is_a_list() -> None:
    """Clearing the plan is a legitimate update, not an error.

    It is also the cheap way out the steering offers: a model that has decided
    not to do the rest says so by rewriting the list.
    """

    assert run(todos=[]) == "Updated todo list: 0 pending, 0 in progress, 0 completed."


def test_content_is_recorded_as_the_model_will_read_it_back() -> None:
    assert normalise([item("  tidy up  ")]) == [item("tidy up")]


@pytest.mark.parametrize(
    ("todos", "complaint"),
    [
        ("a plan", "must be a list"),
        (["a plan"], "object with content and status"),
        ([{"content": "x"}], "one of pending"),
        ([{"content": " ", "status": "pending"}], "non-empty content"),
        ([{"content": "x", "status": "later"}], "one of pending"),
        ([item("x"), item("x")], "repeats"),
        ([{"content": "x", "status": "pending", "id": 3}], "no field"),
        ([item("a", "in_progress"), item("b", "in_progress")], "at most one"),
        ([item("x" * (MAX_CONTENT_CHARS + 1))], "shorter than"),
        ([item(f"step {n}") for n in range(MAX_ITEMS + 1)], "at most"),
    ],
)
def test_a_list_that_cannot_be_recorded_honestly_is_refused(
    todos: object, complaint: str
) -> None:
    """Never repaired, always refused.

    A dropped item or a corrected status would leave the model planning against
    a list that is not the one it will read back.
    """

    with pytest.raises(ToolError) as refusal:
        normalise(todos)
    assert complaint in str(refusal.value)


def test_the_refusal_reaches_the_model_as_a_tool_error() -> None:
    assert run(todos=[item("a", "in_progress"), item("b", "in_progress")]).startswith(
        "error: at most one"
    )


def test_a_missing_list_is_caught_before_the_tool_runs() -> None:
    box = Toolbox(todo_tools())

    refusal = box.validation_error(ToolCall(id="c", name=TOOL_NAME, arguments={}))

    assert refusal is not None and "todos" in refusal


# --- where the list lives -----------------------------------------------------


def test_the_current_list_is_the_last_one_written() -> None:
    messages = [
        wrote(item("a"), call_id="1"),
        acknowledged("1"),
        wrote(item("a", "completed"), item("b"), call_id="2"),
        acknowledged("2"),
    ]

    assert current(messages) == (item("a", "completed"), item("b"))


def test_a_turn_with_no_plan_has_no_list() -> None:
    assert current([Message(role="user", content=[ContentPart(kind="text", text="hi")])]) == ()


def test_a_refused_list_is_not_the_plan() -> None:
    """Its arguments are in the transcript beside the error the model read."""

    messages = [
        wrote(item("a"), call_id="1"),
        acknowledged("1"),
        wrote(item("b", "in_progress"), item("c", "in_progress"), call_id="2"),
        acknowledged("2", "error: at most one todo may be in_progress, not 2"),
    ]

    assert current(messages) == (item("a"),)


def test_open_items_are_the_ones_the_model_did_not_close() -> None:
    plan = (item("a", "completed"), item("b", "in_progress"), item("c"))

    assert unfinished(plan) == (item("b", "in_progress"), item("c"))


# --- objecting to an ending ---------------------------------------------------


async def candidate(*messages: Message) -> Candidate:
    return Candidate(message=messages[-1], messages=tuple(messages))


async def test_by_default_an_open_plan_does_not_refuse_the_ending() -> None:
    """Decided 2026-09-03: the objection cost a second generation every time."""

    ending = await candidate(
        wrote(item("write the page", "completed"), item("look at the page")),
        acknowledged(),
        Message(role="assistant", content=[ContentPart(kind="text", text="Done.")]),
    )

    assert await FinishesItsOwnList().stopping(ending) is None


async def test_an_agent_that_wrote_no_plan_is_never_interrupted() -> None:
    """Which is most turns. The extension costs them nothing at all."""

    answer = Message(role="assistant", content=[ContentPart(kind="text", text="42")])

    assert await FinishesItsOwnList().stopping(await candidate(answer)) is None


async def test_a_finished_plan_lets_the_turn_end() -> None:
    ending = await candidate(
        wrote(item("a", "completed")),
        acknowledged(),
        Message(role="assistant", content=[ContentPart(kind="text", text="Done.")]),
    )

    assert await FinishesItsOwnList().stopping(ending) is None


async def test_an_open_item_refuses_the_ending_and_names_itself() -> None:
    ending = await candidate(
        wrote(item("write the page", "completed"), item("look at the page")),
        acknowledged(),
        Message(role="assistant", content=[ContentPart(kind="text", text="Done.")]),
    )

    steering = await FinishesItsOwnList(limit=1).stopping(ending)

    assert steering is not None
    assert steering.source == "todo"
    assert "look at the page" in steering.instruction
    # The way out that costs nothing is offered in the same sentence.
    assert TOOL_NAME in steering.instruction


async def test_the_objection_is_made_once_and_then_the_turn_may_end() -> None:
    """A stale list must not become an unbounded bill.

    The count comes from the turn rather than from the messages, because a
    steered draft and its instruction are deliberately never appended to them.
    """

    ending = await candidate(
        wrote(item("something left over")),
        acknowledged(),
        Message(role="assistant", content=[ContentPart(kind="text", text="Still done.")]),
    )

    assert await FinishesItsOwnList(limit=1).stopping(ending) is not None
    assert await FinishesItsOwnList(limit=1).stopping(replace(ending, steerings=1)) is None


# --- the whole loop -----------------------------------------------------------


def loop(backend: ScriptedBackend, store: SqliteStore, workspace: Path, **kwargs):
    return build_agent(
        backend,
        Toolbox([*filesystem_tools(workspace), *todo_tools()]),
        store,
        OWNER,
        stopping=FinishesItsOwnList(limit=1),
        **kwargs,
    )


async def test_a_turn_that_leaves_its_own_plan_open_takes_another_step(
    store: SqliteStore, workspace: Path
) -> None:
    """The seam finally has something to say no with, end to end."""

    backend = ScriptedBackend(
        calls(TOOL_NAME, todos=[item("write note.txt", "in_progress"), item("read it back")]),
        calls("write_file", path="note.txt", content="hello"),
        says("Written."),
        calls("read_file", path="note.txt"),
        says("Written, and it reads back hello."),
    )
    agent = loop(backend, store, workspace)

    result = await agent.ainvoke(ask("write hello into note.txt and check it"))

    assert len(backend.requests) == 5
    assert spoken(result["messages"][-1]).startswith("Written, and it reads back")
    # The refused draft never became an answer anybody saw.
    kept = [spoken(message) for message in store.messages("default")]
    assert "Written." not in kept
    assert kept[-1] == "Written, and it reads back hello."
    opening = INSTRUCTION.split(":")[0]
    assert opening in "\n".join(spoken(message) for message in backend.requests[3])


async def test_a_model_that_will_not_carry_on_is_asked_only_once(
    store: SqliteStore, workspace: Path
) -> None:
    backend = ScriptedBackend(
        calls(TOOL_NAME, todos=[item("something big", "in_progress")]),
        says("I stopped here."),
        says("I really stopped here."),
    )
    agent = loop(backend, store, workspace)

    result = await agent.ainvoke(ask("do the big thing"))

    assert len(backend.requests) == 3
    assert spoken(result["messages"][-1]) == "I really stopped here."


async def test_closing_the_plan_is_enough_to_finish(
    store: SqliteStore, workspace: Path
) -> None:
    """Being made to keep working is one outcome; being honest is the other."""

    backend = ScriptedBackend(
        calls(TOOL_NAME, todos=[item("a nice extra", "in_progress")]),
        says("Here you go."),
        calls(TOOL_NAME, todos=[item("a nice extra", "completed")]),
        says("Here you go; I left the extra out."),
    )
    agent = loop(backend, store, workspace)

    result = await agent.ainvoke(ask("do it"))

    assert len(backend.requests) == 4
    assert spoken(result["messages"][-1]).endswith("I left the extra out.")


async def test_an_ordinary_turn_still_ends_in_one_model_call(
    store: SqliteStore, workspace: Path
) -> None:
    """The product default must not have made every answer more expensive."""

    backend = ScriptedBackend(says("hello"))
    agent = loop(backend, store, workspace)

    await agent.ainvoke(ask("hi"))

    assert len(backend.requests) == 1
