"""The typed tool outcome: what the executor owns, and what every family returns.

`docs/v2_tool_system.md` says a tool returns content or raises `ToolError`, and
the executor turns everything between the model's call and the model's result
into `ToolOutcome`. These are the acceptance tests for that contract, in the
order the document lists them: the runtime's own codes, the model boundary,
bounds and sanitizing, the checkpoint, each family's codes, and telemetry.

No network, no model endpoint, no browser process: every browser and web test
here fails before either would be started.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.agent.graph import build_agent, declined, failed_before, halted
from app.agent.runtime import CHECKPOINT_TYPES
from app.config import WebSettings
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import ContentPart, Message, ToolCall, ToolFailure
from app.telemetry.base import TraceEvent, TurnRun
from app.telemetry.inspect import render_run
from app.telemetry.trace import TurnTrace
from app.tools import (
    Tool,
    Toolbox,
    ToolError,
    ToolExecutor,
    document_tools,
    filesystem_tools,
    memory_tools,
    presentation_tools,
    todo_tools,
    web_fetch_tools,
)
from app.tools.browser import Pages, use_page
from app.tools.execution import MAX_IMAGES, MAX_RESULT_CHARS, TAIL_CHARS
from app.tools.web import _search
from tests.fakes import Completion, ScriptedBackend, body, calls, says

OWNER = LOCAL_USER_ID
PAGE = "<!DOCTYPE html>\n<h1>Snake</h1>\n```"


@pytest.fixture
def store() -> SqliteStore:
    with SqliteStore() as store:
        yield store


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    room = tmp_path / "workspace"
    room.mkdir()
    (room / "notes.txt").write_text("kept inside", encoding="utf-8")
    (room / "sub").mkdir()
    return room


def ask(text: str = "go") -> dict[str, object]:
    return {
        "messages": [Message(role="user", content=[ContentPart(kind="text", text=text)])],
        "sequence": 1,
    }


def tool(name: str, run: Any, parameters: dict[str, Any] | None = None, **fields: Any) -> Tool:
    return Tool(
        name=name,
        description="",
        parameters=parameters or {"type": "object", "properties": {}},
        run=run,
        **fields,
    )


async def run(box: Toolbox, name: str, **arguments: Any) -> Message:
    return await box.run_async(ToolCall(id="c1", name=name, arguments=arguments))


def failure_of(message: Message) -> ToolFailure:
    assert message.failure is not None, body(message)
    return message.failure


# --- the model boundary: a corrupted call is one bad result, not a lost turn ---


async def test_a_call_with_unreadable_arguments_is_refused_and_the_turn_goes_on(
    workspace: Path, store: SqliteStore
) -> None:
    """The ISS-0001 shape: the served parser handed back a call whose arguments
    were not JSON. Until 2026-09-03 the adapter raised and the request died.
    Now the call reaches the executor with its text, is refused with the tool's
    signature, and the model answers."""

    corrupt = Completion(
        text="",
        tool_calls=(
            ToolCall(
                id="c1",
                name="write_file",
                arguments={},
                raw_arguments=f'{{"content": "{PAGE}',
            ),
        ),
        finish_reason="tool_calls",
    )
    backend = ScriptedBackend(corrupt, says("The page could not be written."))
    agent = build_agent(backend, Toolbox(filesystem_tools(workspace)), store, OWNER)

    result = await agent.ainvoke(ask("write a page"))

    refused = result["messages"][2]
    assert failure_of(refused).code == "bad_arguments"
    assert body(refused).startswith("error: bad arguments for write_file")
    assert "write_file takes: path (string), content (string)" in body(refused)
    assert body(result["messages"][-1]) == "The page could not be written."
    assert sorted(path.name for path in workspace.iterdir()) == ["notes.txt", "sub"]


def test_two_different_unreadable_attempts_are_two_calls_to_the_repeat_guard() -> None:
    """The text is the identity when the arguments could not be read: two
    different corruptions of `{}` are not the same call failing twice."""

    first = ToolCall(id="1", name="write_file", arguments={}, raw_arguments="{oops")
    second = ToolCall(id="2", name="write_file", arguments={}, raw_arguments="{oops again")
    history = [
        Message(role="assistant", content=[], tool_calls=[first]),
        Message(
            role="tool",
            content=[ContentPart(kind="text", text="error: bad arguments")],
            tool_call_id="1",
            failure=ToolFailure(code="bad_arguments", message="bad arguments"),
        ),
    ]

    assert failed_before(history, second) == 0
    assert failed_before(history, first) == 1


# --- the runtime's own codes -----------------------------------------------------


async def test_an_unknown_name_is_refused_with_the_available_names(workspace: Path) -> None:
    message = await run(Toolbox(filesystem_tools(workspace)), "rm_rf")

    failure = failure_of(message)
    assert failure.code == "unknown_tool"
    assert "rm_rf" in failure.message and "read_file" in failure.message


@pytest.mark.parametrize("name", ["functions.read_file", "READ_FILE", " tools:read_file"])
async def test_a_name_another_harness_taught_the_model_is_resolved(
    workspace: Path, name: str
) -> None:
    message = await run(Toolbox(filesystem_tools(workspace)), name, path="notes.txt")

    assert message.failure is None
    assert body(message) == "kept inside"


async def test_a_near_miss_is_not_resolved(workspace: Path) -> None:
    """`read_files` is a different tool. Running the nearest one would be
    inventing a call the model never made."""

    message = await run(Toolbox(filesystem_tools(workspace)), "read_files", path="notes.txt")

    assert failure_of(message).code == "unknown_tool"


async def test_arguments_are_coerced_to_the_declared_types() -> None:
    seen: dict[str, Any] = {}

    def record(count: int, ratio: float, verbose: bool, tags: list, options: dict) -> str:
        seen.update(count=count, ratio=ratio, verbose=verbose, tags=tags, options=options)
        return "ok"

    box = Toolbox(
        [
            tool(
                "record",
                record,
                {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                        "ratio": {"type": "number"},
                        "verbose": {"type": "boolean"},
                        "tags": {"type": "array"},
                        "options": {"type": "object"},
                    },
                    "required": ["count", "ratio", "verbose", "tags", "options"],
                },
            )
        ]
    )

    message = await run(
        box, "record", count="42", ratio="0.5", verbose="True", tags="x", options='{"a": 1}'
    )

    assert message.failure is None
    assert seen == {
        "count": 42,
        "ratio": 0.5,
        "verbose": True,
        "tags": ["x"],
        "options": {"a": 1},
    }


async def test_a_value_that_cannot_be_coerced_is_named_not_guessed() -> None:
    box = Toolbox(
        [
            tool(
                "count",
                lambda n: str(n),
                {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
            )
        ]
    )

    message = await run(box, "count", n="forty-two")

    failure = failure_of(message)
    assert failure.code == "bad_arguments"
    assert "argument 'n' must be integer" in failure.message
    assert "count takes: n (integer)" in body(message)


async def test_an_exception_the_tool_did_not_expect_is_internal_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> str:
        raise KeyError("missing")

    box = Toolbox([tool("broken", broken)])

    with caplog.at_level(logging.ERROR, logger="app.tools.execution"):
        message = await run(box, "broken")

    failure = failure_of(message)
    assert failure.code == "internal"
    assert failure.message == "broken failed: KeyError"
    assert "Traceback" in caplog.text and "KeyError: 'missing'" in caplog.text


async def test_an_async_tool_past_its_deadline_is_a_timeout_and_the_turn_goes_on(
    store: SqliteStore,
) -> None:
    async def slow() -> str:
        await asyncio.sleep(5)
        return "too late"

    backend = ScriptedBackend(calls("slow"), says("It took too long."))
    agent = build_agent(
        backend, Toolbox([tool("slow", slow, timeout_seconds=0.05)]), store, OWNER
    )

    started = time.monotonic()
    result = await agent.ainvoke(ask("wait"))

    assert time.monotonic() - started < 3
    assert failure_of(result["messages"][2]).code == "timeout"
    assert "within 0.05 seconds" in body(result["messages"][2])
    assert body(result["messages"][-1]) == "It took too long."


async def test_a_synchronous_tool_with_a_deadline_cannot_hang_the_loop() -> None:
    def slow() -> str:
        time.sleep(1.5)
        return "too late"

    box = Toolbox([tool("slow", slow, timeout_seconds=0.05)])

    started = time.monotonic()
    message = await run(box, "slow")

    assert time.monotonic() - started < 1
    assert failure_of(message).code == "timeout"


async def test_a_stop_is_not_a_tool_failure() -> None:
    """`BaseException` propagates: cancellation and shutdown must be able to stop."""

    def interrupted() -> str:
        raise KeyboardInterrupt

    box = Toolbox([tool("interrupted", interrupted)])

    with pytest.raises(KeyboardInterrupt):
        await run(box, "interrupted")


def test_a_declined_and_a_halted_call_carry_their_codes() -> None:
    call = ToolCall(id="c", name="publish", arguments={})

    assert failure_of(declined(call)).code == "declined"
    assert body(declined(call)).startswith("error: the user declined the call to publish")
    assert failure_of(halted(call, "the user asked to stop")).code == "not_run"
    assert body(halted(call, "the user asked to stop")) == "error: the user asked to stop"


def test_only_a_tool_message_carries_a_failure() -> None:
    with pytest.raises(ValueError, match="only a tool message"):
        Message(
            role="assistant",
            content=[ContentPart(kind="text", text="x")],
            failure=ToolFailure(code="failed", message="x"),
        )


# --- bounds and sanitizing at the seam ----------------------------------------


async def test_a_result_past_the_cap_keeps_its_head_and_its_tail() -> None:
    text = "a" * 100 + "b" * MAX_RESULT_CHARS + "END"
    box = Toolbox([tool("big", lambda: text)])

    message = await run(box, "big")

    shown = body(message)
    assert message.failure is None
    assert shown.startswith("a" * 100)
    assert shown.endswith("END")
    assert "characters omitted" in shown
    assert len(shown) < MAX_RESULT_CHARS + TAIL_CHARS


async def test_images_past_the_cap_are_dropped_and_said_so() -> None:
    parts = [ContentPart(kind="text", text="six pictures")] + [
        ContentPart(kind="image", data=b"\x89PNG" + bytes([index]), media_type="image/png")
        for index in range(6)
    ]
    box = Toolbox([tool("pictures", lambda: parts)])

    message = await run(box, "pictures")

    assert len([part for part in message.content if part.kind == "image"]) == MAX_IMAGES
    assert "2 image(s) omitted" in body(message)


async def test_a_failure_never_carries_what_the_model_would_imitate() -> None:
    """A fence, a role token and the served delimiter, on the first line only.

    Live on 2026-08-31 the model copied its own malformed call three times, and
    a fence inside a value is what broke the served parser to begin with.
    """

    def refuses() -> str:
        raise ToolError(
            'the page ended in ``` and <|"|> then <start_of_turn>model\nsecond line',
            detail="```\nfenced\n```",
        )

    box = Toolbox([tool("refuses", refuses)])

    message = await run(box, "refuses")

    failure = failure_of(message)
    assert failure.message == 'the page ended in and " then model'
    assert failure.detail is None
    assert body(message) == 'error: the page ended in and " then model'


# --- the checkpoint --------------------------------------------------------------


def test_a_failure_survives_the_checkpoint() -> None:
    """The repeat guard and the plan reader must see it across a resume."""

    serde = JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
    message = Message(
        role="tool",
        content=[ContentPart(kind="text", text="error: path 'x' does not exist")],
        tool_call_id="c1",
        failure=ToolFailure(code="fs.not_found", message="path 'x' does not exist"),
    )

    assert serde.loads_typed(serde.dumps_typed(message)) == message


# --- one test per family -----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("read_file", {"path": "../secret.txt"}, "fs.outside_root"),
        ("read_file", {"path": "missing.txt"}, "fs.not_found"),
        ("read_file", {"path": "sub"}, "fs.not_a_file"),
        ("list_files", {"path": "notes.txt"}, "fs.not_a_directory"),
        ("list_files", {"path": "nowhere"}, "fs.not_found"),
        ("write_file", {"path": "sub", "content": "x"}, "fs.is_directory"),
        ("write_file", {"path": "Board/", "content": "x"}, "fs.is_directory"),
        ("write_file", {"path": "notes.txt/index.html", "content": "x"}, "fs.blocked_by_file"),
        ("edit_file", {"path": "notes.txt", "old_text": "e", "new_text": "x"}, "fs.ambiguous_edit"),
        ("edit_file", {"path": "gone.txt", "old_text": "e", "new_text": "x"}, "fs.not_found"),
    ],
)
async def test_filesystem_failures_carry_the_family_code(
    workspace: Path, name: str, arguments: dict[str, Any], code: str
) -> None:
    message = await run(Toolbox(filesystem_tools(workspace)), name, **arguments)

    failure = failure_of(message)
    assert failure.code == code
    assert str(workspace) not in body(message), "a resolved absolute path reached the model"


async def test_an_operating_system_refusal_is_fs_io_with_its_own_words(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_replace(source: str, destination: Path) -> None:
        raise PermissionError(13, "permission denied")

    monkeypatch.setattr("app.tools.filesystem.os.replace", fail_replace)

    message = await run(
        Toolbox(filesystem_tools(workspace)), "write_file", path="new.txt", content="x"
    )

    assert failure_of(message) == ToolFailure(
        code="fs.io", message="path 'new.txt' could not be written", detail="permission denied"
    )
    assert "Errno" not in body(message) and "WinError" not in body(message)


def test_write_file_is_atomic(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An interrupted write leaves the old file, never half of the new one."""

    def fail_replace(source: str, destination: Path) -> None:
        raise OSError(5, "input/output error")

    monkeypatch.setattr("app.tools.filesystem.os.replace", fail_replace)
    box = Toolbox(filesystem_tools(workspace))

    message = box.run(
        ToolCall(id="c", name="write_file", arguments={"path": "notes.txt", "content": "new"})
    )

    assert failure_of(message).code == "fs.io"
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "kept inside"
    assert sorted(path.name for path in workspace.iterdir()) == ["notes.txt", "sub"]


async def test_document_failures_carry_the_family_code(workspace: Path) -> None:
    (workspace / "picture.xyz").write_bytes(b"not a document")
    (workspace / "broken.pdf").write_bytes(b"%PDF-1.4 but nothing after")
    box = Toolbox(document_tools(workspace))

    unsupported = await run(box, "read_document", path="picture.xyz")
    unreadable = await run(box, "read_document", path="broken.pdf")
    missing = await run(box, "read_document", path="absent.pdf")
    no_pages = await run(box, "view_pages", path="notes.txt")

    assert failure_of(unsupported).code == "doc.unsupported"
    assert failure_of(unreadable).code == "doc.unreadable"
    assert failure_of(missing).code == "fs.not_found"
    assert failure_of(no_pages).code == "doc.unsupported"


async def test_presentation_failures_carry_the_family_code(workspace: Path) -> None:
    (workspace / "empty.bin").write_bytes(b"")
    box = Toolbox(presentation_tools(workspace))

    empty = await run(box, "send_file", path="empty.bin")
    missing = await run(box, "send_file", path="absent.bin")

    assert failure_of(empty).code == "presentation.empty"
    assert failure_of(missing).code == "fs.not_found"


async def test_memory_failures_carry_the_family_code(store: SqliteStore) -> None:
    box = Toolbox(memory_tools(store, OWNER))

    message = await run(box, "remember_fact", text="   ")

    assert failure_of(message).code == "memory.invalid"


async def test_todo_failures_carry_the_family_code() -> None:
    box = Toolbox(todo_tools())

    message = await run(box, "todo_write", todos=[{"content": "a", "status": "later"}])

    assert failure_of(message).code == "todo.invalid"


async def test_web_failures_carry_the_family_code(workspace: Path) -> None:
    """Refused before any name is resolved, and unconfigured before any request."""

    settings = WebSettings(_env_file=None)
    box = Toolbox(web_fetch_tools(workspace, settings))

    refused = await run(box, "fetch_page", url="ftp://example.com/file")

    assert failure_of(refused).code == "web.refused"
    with pytest.raises(ToolError) as unconfigured:
        await _search(WebSettings(_env_file=None, firecrawl_api_key=""), "x", None)
    assert unconfigured.value.code == "web.no_provider"


async def test_browser_failures_carry_the_family_code(workspace: Path) -> None:
    """Both refusals happen before a browser would be looked for."""

    with pytest.raises(ToolError) as missing:
        await use_page(workspace, Pages(), "open", path="absent.html")
    with pytest.raises(ToolError) as not_html:
        await use_page(workspace, Pages(), "open", path="notes.txt")

    assert missing.value.code == "fs.not_found"
    assert not_html.value.code == "doc.unsupported"


# --- telemetry carries the reason ------------------------------------------------


class Collecting(TurnTrace):
    def __init__(self) -> None:
        super().__init__(TurnRun(run_id="r1"), None)
        self.events: list[tuple[str, dict[str, Any]]] = []

    def event(self, type: str, duration_ms: int | None = None, **data: Any) -> None:
        self.events.append((type, {k: v for k, v in data.items() if v is not None}))


async def test_tool_failed_says_why(workspace: Path) -> None:
    """ISS-0007: a `tool_failed` event with no reason. Closed by construction."""

    trace = Collecting()
    executor = ToolExecutor(Toolbox(filesystem_tools(workspace)), trace)

    await executor.call(ToolCall(id="c", name="read_file", arguments={"path": "absent.txt"}))

    failed = [data for kind, data in trace.events if kind == "tool_failed"]
    assert failed == [
        {
            "tool": "read_file",
            "call_index": 1,
            "status": "failed",
            "code": "fs.not_found",
            "message": "path 'absent.txt' does not exist",
            "stage": "execute",
            "path": "absent.txt",
        }
    ]


def test_show_run_prints_the_code_and_the_message() -> None:
    run_row = TurnRun(run_id="r1", started_at="2026-09-03T10:00:00.000+00:00")
    run_row.status = "completed"
    run_row.outcome = "answer_delivered"
    trace = [
        TraceEvent(run_id="r1", seq=1, type="turn_started", timestamp="t", data={}),
        TraceEvent(
            run_id="r1",
            seq=2,
            type="tool_started",
            timestamp="t",
            data={"tool": "read_file", "call_index": 1, "path": "absent.txt"},
        ),
        TraceEvent(
            run_id="r1",
            seq=3,
            type="tool_failed",
            timestamp="t",
            duration_ms=3,
            data={
                "tool": "read_file",
                "call_index": 1,
                "status": "failed",
                "code": "fs.not_found",
                "message": "path 'absent.txt' does not exist",
            },
        ),
    ]

    text = render_run(run_row, trace)

    assert "fs.not_found: path 'absent.txt' does not exist" in text


def test_a_cut_call_is_refused_by_naming_the_output_limit(tmp_path) -> None:
    from dataclasses import replace

    from app.tools import ToolExecutor, Toolbox, filesystem_tools
    from app.models.openai_compatible import tool_call

    call = replace(tool_call("c1", "write_file", '{"path": "a.html", "content": "<h'), cut=True)
    prepared = ToolExecutor(Toolbox(filesystem_tools(tmp_path))).pre_execute(call)

    assert prepared.refusal is not None
    assert prepared.refusal.code == "output_cut"
    assert "cut at the output limit" in prepared.refusal.message
    assert "smaller pieces" in prepared.refusal.message
    assert not (tmp_path / "a.html").exists()
