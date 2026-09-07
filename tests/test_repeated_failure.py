"""What happens when a tool call keeps failing the same way.

The live failure of 2026-08-30, in three parts. A streamed response carrying two
tool calls was assembled into one, so `write_file` arrived holding another
tool's fields and missing `path`. The error it got back named what was missing
and nothing else. And the loop let it try again eight times, once every twenty-
seven seconds, until the person stopped it.

Everything runs against a scripted backend and a temporary store. No network, no
model endpoint, no worker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.graph import (
    MAX_IDENTICAL_FAILURES,
    REPEATED_FAILURE,
    build_agent,
    failed_before,
)
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import ContentPart, Message, ToolCall, ToolFailure
from app.models.openai_compatible import StreamedCompletion
from app.tools import Toolbox, filesystem_tools
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


def spoken(message: Message) -> str:
    return " ".join(part.text or "" for part in message.content)


def ask(text: str = "go") -> dict[str, object]:
    return {
        "messages": [Message(role="user", content=[ContentPart(kind="text", text=text)])],
        "sequence": 10,
    }


def fragment(**raw: object) -> dict[str, object]:
    return {"choices": [{"delta": {"tool_calls": [raw]}}]}


def failed(call_id: str) -> Message:
    """A tool result that failed, the way the executor marks one: by the field."""

    return Message(
        role="tool",
        content=[ContentPart(kind="text", text="error: no path")],
        tool_call_id=call_id,
        failure=ToolFailure(code="bad_arguments", message="no path"),
    )


# --- two calls must not become one -------------------------------------------


def test_a_second_call_without_a_position_is_not_the_first_one_continued() -> None:
    """The live corruption, in the shape that produced it.

    A server that opens a second call without repeating the index used to have
    its arguments appended to the call in progress. The result was one call
    holding both sets of fields.
    """

    stream = StreamedCompletion()
    stream.add(
        fragment(
            index=0,
            id="a",
            function={"name": "write_file", "arguments": '{"path": "p.html", '},
        )
    )
    stream.add(fragment(index=0, function={"arguments": '"content": "<p>hi</p>"}'}))
    stream.add(
        fragment(
            id="b",
            function={"name": "todo_write", "arguments": '{"todos": []}'},
        )
    )

    result = stream.result()

    assert [call.name for call in result.tool_calls] == ["write_file", "todo_write"]
    assert result.tool_calls[0].arguments == {"path": "p.html", "content": "<p>hi</p>"}
    assert result.tool_calls[1].arguments == {"todos": []}


def test_a_second_call_reusing_a_position_is_still_a_second_call() -> None:
    stream = StreamedCompletion()
    stream.add(fragment(index=0, id="a", function={"name": "read_file", "arguments": "{}"}))
    stream.add(fragment(index=0, id="b", function={"name": "list_files", "arguments": "{}"}))

    assert [call.name for call in stream.result().tool_calls] == ["read_file", "list_files"]


def test_a_server_that_echoes_the_same_identity_is_still_one_call() -> None:
    """Repeating the id and name on every fragment is a continuation."""

    stream = StreamedCompletion()
    stream.add(
        fragment(index=0, id="a", function={"name": "read_file", "arguments": '{"path": "a'})
    )
    stream.add(
        fragment(index=0, id="a", function={"name": "read_file", "arguments": '.txt"}'})
    )

    result = stream.result()

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].arguments == {"path": "a.txt"}


def test_ordinary_parallel_calls_keep_their_own_positions() -> None:
    stream = StreamedCompletion()
    stream.add(fragment(index=0, id="a", function={"name": "read_file", "arguments": "{}"}))
    stream.add(fragment(index=1, id="b", function={"name": "list_files", "arguments": "{}"}))
    stream.add(fragment(index=0, function={"arguments": ""}))

    assert [call.name for call in stream.result().tool_calls] == ["read_file", "list_files"]


# --- the error says how to call it -------------------------------------------


def test_a_rejected_call_is_told_the_shape_it_should_have_had(
    workspace: Path,
) -> None:
    """The model got "missing required argument(s): path" eight times."""

    box = Toolbox(filesystem_tools(workspace))

    result = box.run(ToolCall(id="c", name="write_file", arguments={"content": "x"}))

    assert "missing required argument(s): path" in spoken(result)
    assert "write_file takes: path (string), content (string)" in spoken(result)


def test_an_optional_argument_is_marked_as_one(workspace: Path) -> None:
    box = Toolbox(filesystem_tools(workspace))

    assert box.signature("list_files") == "list_files takes: path (string, optional)"


# --- and the loop stops paying for it ----------------------------------------


def test_the_same_failing_call_is_counted(workspace: Path) -> None:
    call = ToolCall(id="3", name="write_file", arguments={"content": "x"})
    history = [
        Message(role="assistant", content=[], tool_calls=[
            ToolCall(id="1", name="write_file", arguments={"content": "x"})
        ]),
        failed("1"),
        Message(role="assistant", content=[], tool_calls=[
            ToolCall(id="2", name="write_file", arguments={"content": "different"})
        ]),
        failed("2"),
    ]

    assert failed_before(history, call) == 1


def test_a_call_that_succeeded_is_not_a_failure_to_count(workspace: Path) -> None:
    """Writing the same file twice is ordinary work, not a loop."""

    call = ToolCall(id="2", name="write_file", arguments={"path": "a", "content": "x"})
    history = [
        Message(role="assistant", content=[], tool_calls=[
            ToolCall(id="1", name="write_file", arguments={"path": "a", "content": "x"})
        ]),
        Message(role="tool", content=[ContentPart(kind="text", text="created a")], tool_call_id="1"),
    ]

    assert failed_before(history, call) == 0


def test_a_success_in_between_starts_the_count_over(workspace: Path) -> None:
    """Live 2026-09-03: two looks at a missing file, then the file is written.

    The third look is not a third identical attempt; the world it runs in is
    different, and refusing it cost the person the screenshot they asked for.
    """

    look = {"path": "Task Board test 4/index.html"}
    call = ToolCall(id="5", name="inspect_page", arguments=look)
    history = [
        Message(role="assistant", content=[], tool_calls=[ToolCall(id="1", name="inspect_page", arguments=look)]),
        failed("1"),
        Message(role="assistant", content=[], tool_calls=[ToolCall(id="2", name="inspect_page", arguments=look)]),
        failed("2"),
        Message(role="assistant", content=[], tool_calls=[
            ToolCall(id="3", name="write_file", arguments={"path": look["path"], "content": "<p>"})
        ]),
        Message(role="tool", content=[ContentPart(kind="text", text="created")], tool_call_id="3"),
    ]

    assert failed_before(history, call) == 0
    history += [
        Message(role="assistant", content=[], tool_calls=[ToolCall(id="4", name="inspect_page", arguments=look)]),
        failed("4"),
    ]
    assert failed_before(history, call) == 1


async def test_a_call_that_works_once_its_file_exists_is_run(
    store: SqliteStore, workspace: Path
) -> None:
    backend = ScriptedBackend(
        calls("read_file", path="late.txt"),
        calls("read_file", path="late.txt"),
        calls("write_file", path="late.txt", content="here now"),
        calls("read_file", path="late.txt"),
        says("It says: here now."),
    )
    agent = build_agent(
        backend, Toolbox(filesystem_tools(workspace)), store, OWNER
    )

    result = await agent.ainvoke(ask("read late.txt"))

    assert result.get("stopping") != REPEATED_FAILURE
    reads = [m for m in result["messages"] if m.role == "tool" and "here now" in spoken(m)]
    assert reads, "the third read ran and returned the file"


async def test_a_call_that_keeps_failing_ends_the_turn_instead_of_the_person(
    store: SqliteStore, workspace: Path
) -> None:
    """Two attempts, then no more tools — and an answer that says why."""

    broken = calls("write_file", content="<!DOCTYPE html>")
    backend = ScriptedBackend(broken, broken, broken, broken, says("unused"))
    agent = build_agent(
        backend,
        Toolbox(filesystem_tools(workspace)),
        store,
        OWNER,
    )

    result = await agent.ainvoke(ask("make a page"))

    # Two failures, then the third call is refused before it runs, and the model
    # is asked once more with no tools at all.
    assert len(backend.requests) == MAX_IDENTICAL_FAILURES + 2
    assert backend.tools_seen[-1] is None
    assert result["stopping"] == REPEATED_FAILURE
    assert "kept failing in the same way" in spoken(result["messages"][-1])


async def test_a_call_that_fails_once_is_still_retried(
    store: SqliteStore, workspace: Path
) -> None:
    """A transient failure deserves another attempt; nothing here forbids one."""

    backend = ScriptedBackend(
        calls("read_file", path="missing.txt"),
        calls("read_file", path="missing.txt"),
        says("It is not there."),
    )
    agent = build_agent(backend, Toolbox(filesystem_tools(workspace)), store, OWNER)

    result = await agent.ainvoke(ask("read missing.txt"))

    assert len(backend.requests) == 3
    assert not result.get("stopping")
    assert spoken(result["messages"][-1]) == "It is not there."


def test_a_fenced_value_that_lost_the_next_argument_is_named_as_the_cause(
    tmp_path: Path,
) -> None:
    """Run `e54b442b`, 2026-09-03: three identical calls, `content` ending in
    a fence, `path` gone each time, and the model never changed the call."""

    box = Toolbox(filesystem_tools(tmp_path))
    fenced = "<html></html>" + chr(10) + "```"

    error = box.validation_error(
        ToolCall(id="c1", name="write_file", arguments={"content": fenced})
    )

    assert error is not None
    assert error.startswith("missing required argument(s): path; content ends with a markdown fence")
    assert "path first and no fence" in error


# --- identical successes are bounded too ------------------------------------------


async def test_the_third_identical_successful_call_is_answered_without_running(
    store: SqliteStore, workspace: Path
) -> None:
    """ISS-0019: the same page written seven times in one turn, each a full
    generation. The third byte-identical success is refused and the turn
    goes on; nothing ends."""

    same = calls("write_file", path="page.html", content="<p>hi</p>")
    backend = ScriptedBackend(same, same, same, says("Done."))
    agent = build_agent(backend, Toolbox(filesystem_tools(workspace)), store, OWNER)

    result = await agent.ainvoke(ask("make a page"))

    results = [m for m in result["messages"] if m.role == "tool"]
    assert len(results) == 3
    assert results[0].failure is None and results[1].failure is None
    assert results[2].failure is not None and results[2].failure.code == "not_run"
    assert "already succeeded twice" in spoken(results[2])
    assert not result.get("stopping"), "one refused call is not an ending"
    assert spoken(result["messages"][-1]) == "Done."
    assert backend.tools_seen[-1] is not None, "tools stay offered"
    assert (workspace / "page.html").read_text(encoding="utf-8") == "<p>hi</p>"


def _turn(*steps: tuple[str, dict[str, object]]) -> list[Message]:
    """Assistant call then its successful result, for each step."""

    messages: list[Message] = []
    for index, (name, arguments) in enumerate(steps):
        call_id = f"c{index}"
        messages.append(Message(role="assistant", content=[], tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)]))
        messages.append(Message(role="tool", tool_call_id=call_id, content=[ContentPart(kind="text", text="exit code: 1")]))
    return messages


def test_a_change_to_the_workspace_between_two_identical_commands_makes_the_second_new() -> None:
    """ISS-0042, deployed 2026-09-04: `python3 make_pdf.py` after each rewrite of
    `make_pdf.py`, and the fourth run — of the version that registered its
    font — was refused as "already succeeded twice", because a non-zero exit is
    a success and nothing reset the count. A write in between is a changed
    world; the same run unchanged three times is still the loop."""

    from app.agent.graph import succeeded_before

    run = ToolCall(id="x", name="run_command", arguments={"command": "python3 make_pdf.py"})
    rewrite = ("write_file", {"path": "make_pdf.py", "content": "v"})
    same_run = ("run_command", {"command": "python3 make_pdf.py"})

    edited_between = _turn(rewrite, same_run, rewrite, same_run, rewrite, same_run)
    unchanged = _turn(rewrite, same_run, same_run, same_run)
    read_between = _turn(same_run, ("read_file", {"path": "make_pdf.py"}), same_run, same_run)

    changing = ["write_file", "edit_file", "run_command"]
    assert succeeded_before(edited_between, run, changing) == 1
    assert succeeded_before(unchanged, run, changing) == 3
    assert succeeded_before(read_between, run, changing) == 3
    assert succeeded_before(edited_between, run) == 3, "without the names, the old count"

