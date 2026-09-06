"""The tool loop, closed offline.

A scripted backend replaces the model, so these tests answer one question: does
what the model asked for come back to it, and does the loop end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.graph import build_agent
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import Completion, ContentPart, Message, ToolCall
from app.tools import Tool, Toolbox, filesystem_tools
from tests.fakes import ScriptedBackend, calls


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "notes.txt").write_text("the answer is 42", encoding="utf-8")
    return tmp_path


@pytest.fixture
def store() -> SqliteStore:
    with SqliteStore() as store:
        yield store


def agent_over(backend: ScriptedBackend, workspace: Path, store: SqliteStore):
    return build_agent(
        backend, Toolbox(filesystem_tools(workspace)), store, LOCAL_USER_ID
    )


def ask(text: str) -> dict[str, list[Message]]:
    return {"messages": [Message(role="user", content=[ContentPart(kind="text", text=text)])]}


async def test_a_call_tool_answer_cycle_closes(workspace: Path, store: SqliteStore) -> None:
    backend = ScriptedBackend(calls("read_file", path="notes.txt"), Completion(text="42"))
    agent = agent_over(backend, workspace, store)

    result = await agent.ainvoke(ask("What does notes.txt say?"))

    assert [message.role for message in result["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert result["messages"][-1].content[0].text == "42"


async def test_the_tool_result_is_what_the_second_request_carries(workspace: Path, store: SqliteStore) -> None:
    backend = ScriptedBackend(calls("read_file", path="notes.txt"), Completion(text="42"))
    agent = agent_over(backend, workspace, store)

    await agent.ainvoke(ask("What does notes.txt say?"))

    second = backend.requests[1]
    assert second[-2].tool_calls[0].name == "read_file"
    assert second[-1].role == "tool"
    assert second[-1].tool_call_id == "call_read_file"
    assert second[-1].content[0].text == "the answer is 42"


async def test_an_answer_without_tool_calls_ends_the_graph(workspace: Path, store: SqliteStore) -> None:
    backend = ScriptedBackend(Completion(text="no tool needed"))
    agent = agent_over(backend, workspace, store)

    result = await agent.ainvoke(ask("Say hello."))

    assert len(backend.requests) == 1
    assert [message.role for message in result["messages"]] == ["user", "assistant"]


async def test_a_call_cut_at_the_cap_before_a_word_is_said_so(workspace: Path, store: SqliteStore) -> None:
    """ISS-0055: reasoning spent the whole output cap; silence is not an answer."""

    backend = ScriptedBackend(Completion(text="", finish_reason="length"))
    agent = agent_over(backend, workspace, store)

    result = await agent.ainvoke(ask("Make the pages."))

    assert [message.role for message in result["messages"]] == ["user", "assistant"]
    assert "output limit" in result["messages"][-1].content[0].text


async def test_an_empty_completion_that_chose_silence_stays_silent(workspace: Path, store: SqliteStore) -> None:
    backend = ScriptedBackend(calls("read_file", path="notes.txt"), Completion(text="", finish_reason="stop"))
    agent = agent_over(backend, workspace, store)

    result = await agent.ainvoke(ask("Read notes.txt and say nothing."))

    assert [message.role for message in result["messages"]] == ["user", "assistant", "tool"]


async def test_the_loop_runs_more_than_once(workspace: Path, store: SqliteStore) -> None:
    backend = ScriptedBackend(
        calls("list_files"),
        calls("read_file", path="notes.txt"),
        Completion(text="42"),
    )
    agent = agent_over(backend, workspace, store)

    result = await agent.ainvoke(ask("Find the answer."))

    assert len(backend.requests) == 3
    assert [message.role for message in result["messages"]].count("tool") == 2


async def test_several_calls_in_one_turn_each_get_a_result(workspace: Path, store: SqliteStore) -> None:
    both = Completion(
        text="",
        tool_calls=(
            ToolCall(id="a", name="list_files", arguments={}),
            ToolCall(id="b", name="read_file", arguments={"path": "notes.txt"}),
        ),
    )
    backend = ScriptedBackend(both, Completion(text="done"))
    agent = agent_over(backend, workspace, store)

    result = await agent.ainvoke(ask("Look around and read."))

    tool_messages = [message for message in result["messages"] if message.role == "tool"]
    assert [message.tool_call_id for message in tool_messages] == ["a", "b"]


async def test_a_failing_tool_goes_back_to_the_model(workspace: Path, store: SqliteStore) -> None:
    backend = ScriptedBackend(calls("read_file", path="../secret.txt"), Completion(text="sorry"))
    agent = agent_over(backend, workspace, store)

    result = await agent.ainvoke(ask("Read the secret."))

    assert result["messages"][2].content[0].text.startswith("error:")
    assert result["messages"][-1].content[0].text == "sorry"


async def test_an_os_failure_stays_inside_the_tool_loop(store: SqliteStore) -> None:
    def denied() -> str:
        raise PermissionError(13, "permission denied")

    backend = ScriptedBackend(calls("blocked"), Completion(text="I could not read it."))
    agent = build_agent(
        backend,
        Toolbox([Tool(name="blocked", description="", parameters={}, run=denied)]),
        store,
        LOCAL_USER_ID,
    )

    result = await agent.ainvoke(ask("Read the blocked file."))

    failed = result["messages"][2]
    assert failed.content[0].text == "error: blocked failed: PermissionError (permission denied)"
    assert failed.failure is not None and failed.failure.code == "internal"
    assert result["messages"][-1].content[0].text == "I could not read it."


async def test_the_tool_schemas_are_sent_on_every_request(workspace: Path, store: SqliteStore) -> None:
    backend = ScriptedBackend(calls("list_files"), Completion(text="done"))
    agent = agent_over(backend, workspace, store)

    await agent.ainvoke(ask("Look around."))

    names = [[tool["function"]["name"] for tool in seen] for seen in backend.tools_seen]
    assert names == [
        ["list_files", "read_file", "write_file", "edit_file"],
        ["list_files", "read_file", "write_file", "edit_file"],
    ]


async def test_an_agent_without_tools_sends_none(workspace: Path, store: SqliteStore) -> None:
    backend = ScriptedBackend(Completion(text="hello"))
    agent = build_agent(backend, Toolbox(), store, LOCAL_USER_ID)

    await agent.ainvoke(ask("Say hello."))

    assert backend.tools_seen == [None]
