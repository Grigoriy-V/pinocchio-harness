"""A turn a dead worker left half-done is taken up, not lost and not redone.

Roadmap 4.7. The acceptance sentence of the 2026-08-30 preparation: "an
interrupted multi-step task continues truthfully without repeating completed
side effects". Everything here asserts on what the harness did — the
checkpoint, the store, the trace, which tools ran — never on what a model
said. Only the model is faked; the store, the checkpointer, the tools and the
graph are the real ones, because a resume is exactly the property that only
exists when they are wired together.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agent.runtime import Agent, MessageProduced
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import Completion, Message, ToolCall
from app.telemetry import TurnRun
from app.telemetry.sqlite import SqliteTelemetry
from app.telemetry.trace import Telemetry
from app.tools import (
    INTERRUPTED,
    Tool,
    Toolbox,
    browser_tools,
    document_tools,
    filesystem_tools,
    history_tools,
    memory_tools,
    presentation_tools,
    todo_tools,
    web_tools,
)
from app.tools.capabilities import CapabilityRegistry
from tests.fakes import ScriptedBackend, body, calls, says, user

THREAD = "t-resume"


class Killed(BaseException):
    """The process dying. Not an `Exception`: nothing in the loop catches it,
    which is what a kill is like."""


@pytest.fixture
def room(tmp_path: Path) -> Path:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("the answer is 42", encoding="utf-8")
    return tmp_path


def boom() -> str:
    raise Killed()


def open_agent(room: Path, backend: ScriptedBackend, telemetry: Telemetry | None = None) -> Agent:
    """One process's agent. Two of these on the same `room` are a restart."""

    registry = CapabilityRegistry(room / "workspace")
    grant = registry.grant()
    return Agent(
        backend,
        SqliteStore(room / "memory.sqlite3"),
        room / "workspace",
        checkpoints=room / "checkpoints.sqlite3",
        capability_registry=_WithBoom(registry),
        capability_grant=grant,
        telemetry=telemetry,
    )


class _WithBoom:
    """The real registry plus one tool that dies while running."""

    def __init__(self, inner: CapabilityRegistry) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def toolbox(self, grant: Any, extra: Any = (), **options: Any) -> Toolbox:
        dies = Tool(
            name="boom",
            description="dies",
            parameters={"type": "object", "properties": {}},
            run=boom,
        )
        return self.inner.toolbox(grant, [*extra, dies], **options)


def three_calls() -> Completion:
    return Completion(
        text="",
        tool_calls=(
            ToolCall(id="c-read", name="read_file", arguments={"path": "notes.txt"}),
            ToolCall(
                id="c-write",
                name="write_file",
                arguments={"path": "page.html", "content": "<p>hi</p>"},
            ),
            ToolCall(id="c-boom", name="boom", arguments={}),
        ),
        finish_reason="tool_calls",
    )


def traced(telemetry: Telemetry, run_id: str):
    return telemetry.start(TurnRun(run_id=run_id, user_id=LOCAL_USER_ID, thread_id=THREAD))


def events(store: SqliteTelemetry, run_id: str) -> list[tuple[str, dict[str, Any]]]:
    return [(event.type, event.data) for event in store.events(run_id)]


# --- the worker dies while a step's tools are running --------------------------------


async def test_a_turn_killed_inside_its_tools_is_taken_up_without_rerunning_a_write(
    room: Path,
) -> None:
    """Three calls in one step: a read, a write, and one that dies. The write
    ran before the death. The next worker runs the read again (it changes
    nothing), answers the write and the dead one `interrupted`, and the model
    goes on from there. Nothing is written twice by the harness."""

    first = open_agent(room, ScriptedBackend(three_calls()))
    with pytest.raises(Killed):
        async for _ in first.events(THREAD, user("make the page")):
            pass
    await first.aclose()
    written = room / "workspace" / "page.html"
    assert written.read_text(encoding="utf-8") == "<p>hi</p>", "the write had run"
    written.write_text("<p>edited after the kill</p>", encoding="utf-8")

    telemetry = Telemetry(SqliteTelemetry(":memory:"))
    backend = ScriptedBackend(says("Done."))
    second = open_agent(room, backend, telemetry)
    left = await second.unfinished(THREAD)
    assert left is not None and left.node == "tools"
    assert body(left.request) == "make the page"

    trace = traced(telemetry, "r1")
    produced = [
        event.message
        async for event in second.resume_interrupted_events(THREAD, trace)
        if isinstance(event, MessageProduced)
    ]
    trace.finish("answer_delivered")
    nothing_left = await second.unfinished(THREAD)
    await second.aclose()

    # What the model was handed: the read's real content, the two unknowns.
    request = backend.requests[0]
    results = {m.tool_call_id: m for m in request if m.role == "tool"}
    assert "the answer is 42" in body(results["c-read"]) and results["c-read"].failure is None
    assert results["c-write"].failure is not None
    assert results["c-write"].failure.code == INTERRUPTED
    assert "whether it ran is unknown" in body(results["c-write"])
    assert results["c-boom"].failure is not None and results["c-boom"].failure.code == INTERRUPTED
    # The harness did not run the write again: the person's edit stands.
    assert written.read_text(encoding="utf-8") == "<p>edited after the kill</p>"
    # The turn finished and was stored once, whole. The synthetic results are
    # written into the checkpoint, not produced: an interface has nothing to
    # show for them, and the store gets them from the turn's messages.
    assert [m.role for m in produced] == ["assistant"]
    stored = SqliteStore(room / "memory.sqlite3").messages(THREAD)
    assert [m.role for m in stored] == ["user", "assistant", "tool", "tool", "tool", "assistant"]
    assert body(stored[-1]) == "Done."
    assert nothing_left is None
    kinds = dict(events(telemetry.store, "r1"))
    assert kinds["turn_resumed"] == {"node": "tools", "unknown": 2, "replayed": 1}


async def test_a_turn_killed_between_steps_simply_continues(room: Path) -> None:
    """Dead after the tools' results were checkpointed, before the next model
    call: nothing is unknown, nothing is asked twice, the model is called."""

    first = open_agent(
        room,
        ScriptedBackend(calls("read_file", path="notes.txt"), RuntimeError("model died")),
    )
    with pytest.raises(RuntimeError):
        async for _ in first.events(THREAD, user("read it")):
            pass
    await first.aclose()

    backend = ScriptedBackend(says("42."))
    second = open_agent(room, backend)
    left = await second.unfinished(THREAD)
    assert left is not None and left.node == "model"

    async for _ in second.resume_interrupted_events(THREAD):
        pass
    await second.aclose()

    assert [m.role for m in backend.requests[0]][-3:] == ["user", "assistant", "tool"]
    stored = SqliteStore(room / "memory.sqlite3").messages(THREAD)
    assert [m.role for m in stored] == ["user", "assistant", "tool", "assistant"]
    assert stored[1].tool_calls[0].name == "read_file", "the read was not asked for again"


# --- the worker dies inside persist ----------------------------------------------------


async def test_a_turn_killed_after_it_was_stored_is_not_stored_twice(
    room: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`persist` writes the store, then folds; a death between the two leaves
    the checkpoint before `persist`, so the node runs again. The person's
    message must not appear twice for it."""

    import app.agent.graph as graph_module

    real_fold = graph_module.fold_older_messages
    deaths = {"left": 1}

    async def dying_fold(*args: Any, **kwargs: Any) -> Any:
        if deaths["left"]:
            deaths["left"] -= 1
            raise Killed()
        return await real_fold(*args, **kwargs)

    monkeypatch.setattr(graph_module, "fold_older_messages", dying_fold)

    first = open_agent(room, ScriptedBackend(says("Hello.")))
    with pytest.raises(Killed):
        async for _ in first.events(THREAD, user("hi")):
            pass
    await first.aclose()
    assert [m.role for m in SqliteStore(room / "memory.sqlite3").messages(THREAD)] == [
        "user",
        "assistant",
    ], "the store was written before the death"

    second = open_agent(room, ScriptedBackend())
    left = await second.unfinished(THREAD)
    assert left is not None and left.node == "persist"
    async for _ in second.resume_interrupted_events(THREAD):
        pass
    await second.aclose()

    stored = SqliteStore(room / "memory.sqlite3").messages(THREAD)
    assert [body(m) for m in stored] == ["hi", "Hello."]


# --- nothing to take up -----------------------------------------------------------------


async def test_a_finished_turn_and_a_question_are_not_unfinished(room: Path) -> None:
    agent = open_agent(room, ScriptedBackend(says("Hello.")))
    await agent.answer(THREAD, user("hi"))
    assert await agent.unfinished(THREAD) is None
    assert await agent.unfinished("never-used") is None
    taken = [event async for event in agent.resume_interrupted_events(THREAD)]
    assert taken == []
    await agent.aclose()


# --- what may be run again is declared on the tool --------------------------------------


def test_reading_tools_are_replay_safe_and_changing_ones_are_not(tmp_path: Path) -> None:
    store = SqliteStore()
    everything = [
        *filesystem_tools(tmp_path),
        *web_tools(tmp_path),
        *document_tools(tmp_path),
        *browser_tools(tmp_path),
        *history_tools(store, LOCAL_USER_ID, "t"),
        *memory_tools(store, LOCAL_USER_ID, "t", 3),
        *presentation_tools(tmp_path),
        *todo_tools(),
    ]
    safe = {tool.name for tool in everything if tool.replay_safe}
    # `search_web` is only wired with a search key, which a test has none of.
    assert safe | {"search_web"} == {
        "list_files",
        "read_file",
        "search_web",
        "fetch_page",
        "view_web_page",
        "read_document",
        "view_pages",
        "search_history",
        "read_history",
        "search_memory",
    }
    unsafe = {tool.name for tool in everything if not tool.replay_safe}
    # A page action is not run again after a worker died: the page went with
    # the worker, and a click may already have counted.
    assert {"write_file", "edit_file", "send_file", "remember_fact", "use_page"} <= unsafe
