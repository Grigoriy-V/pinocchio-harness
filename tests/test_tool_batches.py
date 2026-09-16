"""Roadmap 32: the graph's batch, read against the references
(`reports/2026-09-17_item32_references.md` §4.9).

A batch of reads runs at once and a write between them is a barrier; the
results come back in the model's order and a failure fills its own slot; the
calls needing no yes run before the question; an answer that names only one
call asks again for the rest; a remembered yes skips the question; a stop
ends the call in flight and the model call between chunks; the toolbox grows
inside a turn and the schemas follow; plan mode offers no changing tool; a
repeated call is noted from the second time; an empty completion gets one
tool-free request; a background exit is told at the next boundary.

Everything runs against scripted backends and fake tools. No model, no
network, no worker.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from app.agent.graph import (
    STOP_REQUESTED,
    STOPPED_TEXT,
    TurnWatch,
    build_agent,
)
from app.agent.grants import GRANTS_FILE, Grants
from app.agent.mode import set_mode
from app.agent.notices import MemoryNotices
from app.agent.runtime import Agent, MessageProduced, ToolFinished, ToolStarted
from app.agent.stop import MemoryStopRequests
from app.capabilities import capability_brief, capability_report
from app.context import ContextPolicy
from app.limits import Limits
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import Completion, CompletionDone, ContentPart, Message, StreamEvent, TextDelta, ToolCall
from app.tools import CapabilityRegistry, Tool, ToolError, Toolbox
from app.tools.capabilities import FILESYSTEM_READ, FILESYSTEM_WRITE, SHELL_RUN
from app.tools.shell import Running, _watch_exit, shell_tools
from tests.fakes import ScriptedBackend, calls, says, user

OWNER = LOCAL_USER_ID


@pytest.fixture
def store() -> SqliteStore:
    with SqliteStore() as store:
        yield store


def spoken(message: Message) -> str:
    return " ".join(part.text or "" for part in message.content)


def ask(text: str = "go", sequence: int = 10) -> dict[str, Any]:
    return {"messages": [user(text)], "sequence": sequence}


def batch(*calls_: tuple[str, str, dict[str, Any]]) -> Completion:
    """One completion asking for several calls: (id, name, arguments)."""

    return Completion(
        text="",
        tool_calls=tuple(ToolCall(id=id_, name=name, arguments=args) for id_, name, args in calls_),
        finish_reason="tool_calls",
    )


class Clock:
    """Records when each fake tool started and ended, by its label."""

    def __init__(self) -> None:
        self.spans: dict[str, tuple[float, float]] = {}
        self.cancelled: list[str] = []

    def read(self, seconds: float = 0.25) -> Tool:
        async def run(label: str) -> str:
            started = time.monotonic()
            try:
                await asyncio.sleep(seconds)
            except asyncio.CancelledError:
                self.cancelled.append(label)
                raise
            self.spans[label] = (started, time.monotonic())
            return f"read {label}"

        return Tool(
            name="look",
            description="A read that takes a moment.",
            returns="the label.",
            leaves="nothing.",
            parameters={"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
            run=run,
            replay_safe=True,
        )

    def write(self, seconds: float = 0.05) -> Tool:
        async def run(label: str) -> str:
            started = time.monotonic()
            await asyncio.sleep(seconds)
            self.spans[label] = (started, time.monotonic())
            return f"wrote {label}"

        return Tool(
            name="change",
            description="A write that takes a moment.",
            returns="the label.",
            leaves="a change.",
            parameters={"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
            run=run,
            mutates=True,
        )


def broken() -> Tool:
    def run(label: str) -> str:
        raise ToolError(f"{label} is broken", code="test.broken")

    return Tool(
        name="fail",
        description="Always fails.",
        returns="nothing.",
        leaves="nothing.",
        parameters={"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
        run=run,
        replay_safe=True,
    )


def overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


# --- 4.1 parallel reads, a write as a barrier, results in order --------------------


async def test_reads_run_at_once_a_write_is_a_barrier_and_results_keep_the_models_order(
    store: SqliteStore,
) -> None:
    clock = Clock()
    backend = ScriptedBackend(
        batch(
            ("r1", "look", {"label": "a"}),
            ("r2", "look", {"label": "b"}),
            ("r3", "look", {"label": "c"}),
            ("w1", "change", {"label": "w"}),
            ("r4", "look", {"label": "d"}),
            ("f1", "fail", {"label": "x"}),
        ),
        says("Done."),
    )
    agent = build_agent(backend, Toolbox([clock.read(), clock.write(), broken()]), store, OWNER)

    result = await agent.ainvoke(ask())

    results = [m for m in result["messages"] if m.role == "tool"]
    assert [m.tool_call_id for m in results] == ["r1", "r2", "r3", "w1", "r4", "f1"]
    a, b, c, w, d = (clock.spans[k] for k in ("a", "b", "c", "w", "d"))
    assert overlap(a, b) and overlap(b, c) and overlap(a, c), "the three reads ran together"
    assert w[0] >= max(a[1], b[1], c[1]), "the write waited for the reads before it"
    assert d[0] >= w[1], "the read after the write waited for it"
    assert results[5].failure is not None and results[5].failure.code == "test.broken"
    assert all(m.failure is None for m in results[:5])
    assert result["tool_calls"] == 6
    assert spoken(result["messages"][-1]) == "Done."


async def test_the_fan_out_is_the_settings_number(store: SqliteStore) -> None:
    clock = Clock()
    backend = ScriptedBackend(
        batch(*((f"r{n}", "look", {"label": str(n)}) for n in range(4))), says("Done.")
    )
    policy = ContextPolicy(limits=Limits(parallel_calls=2))
    agent = build_agent(backend, Toolbox([clock.read()]), store, OWNER, policy)

    await agent.ainvoke(ask())

    first, second, third, fourth = (clock.spans[str(n)] for n in range(4))
    assert overlap(first, second) and overlap(third, fourth)
    assert third[0] >= min(first[1], second[1]), "two at a time"


# --- 4.2 the events, and a background exit told at the boundary ----------------------


async def test_a_call_is_reported_when_it_launches_and_when_it_returns(tmp_path: Path) -> None:
    clock = Clock()
    backend = ScriptedBackend(batch(("r1", "look", {"label": "a"})), says("Done."))
    agent = Agent(backend, SqliteStore(), tmp_path)
    agent.toolbox = lambda _thread: Toolbox([clock.read(0.01)])  # type: ignore[assignment]

    kinds = []
    async for event in agent.events("t", user("go")):
        kinds.append(type(event).__name__)
        if isinstance(event, ToolStarted):
            assert event.call.name == "look"
        if isinstance(event, ToolFinished):
            assert spoken(event.message) == "read a"
    await agent.aclose()

    assert kinds.index("ToolStarted") < kinds.index("ToolFinished")
    assert kinds[-1] == "MessageProduced"


async def test_a_notice_posted_during_a_batch_is_read_after_its_results(store: SqliteStore) -> None:
    notices = MemoryNotices()

    async def run(label: str) -> str:
        # The exit lands on the lane while the batch runs.
        notices.post(OWNER, "background command bg-1 (sleep 1) exited with code 0")
        return f"read {label}"

    look = Tool(
        name="look",
        description="A read.",
        returns="text.",
        leaves="nothing.",
        parameters={"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
        run=run,
        replay_safe=True,
    )
    backend = ScriptedBackend(batch(("r1", "look", {"label": "a"})), says("Noted."))
    agent = build_agent(backend, Toolbox([look]), store, OWNER, notices=notices)

    result = await agent.ainvoke(ask())

    roles = [m.role for m in result["messages"]]
    assert roles == ["user", "assistant", "tool", "assistant"], "turn control is read, not stored"
    second = backend.requests[1]
    assert second[-1].role == "user"
    assert "Turn control (not from the user): background command bg-1" in spoken(second[-1])
    assert second[-2].role == "tool", "after the batch's results"
    assert notices.take(OWNER) == []


async def test_a_notice_waiting_before_a_turn_is_the_first_thing_after_the_request(
    store: SqliteStore,
) -> None:
    notices = MemoryNotices()
    notices.post(OWNER, "background command bg-2 (npm start) exited with code 1")
    backend = ScriptedBackend(says("I see it ended."))
    agent = build_agent(backend, Toolbox(), store, OWNER, notices=notices)

    result = await agent.ainvoke(ask("what happened?"))

    assert [m.role for m in result["messages"]] == ["user", "assistant"]
    assert "Turn control (not from the user): background command bg-2" in spoken(backend.requests[0][-1]), "read by the first model call, after the request"
    assert "what happened?" in spoken(backend.requests[0][-2])
    assert notices.take(OWNER) == [], "taken once"


async def test_a_background_exit_is_watched_and_told(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.tools.shell as shell

    monkeypatch.setattr(shell, "EXIT_POLL_SECONDS", 0.02)

    class Process:
        returncode = None
        polls = 0

        def poll(self):
            self.polls += 1
            if self.polls >= 3:
                self.returncode = 3
            return self.returncode

    output = tmp_path / "out.txt"
    output.write_text("line one\nline two\n", encoding="utf-8")
    running = Running(id="bg-1", command="python spew.py", process=Process(), output=output, started=0.0)
    told: list[str] = []

    await _watch_exit(running, told.append)

    assert len(told) == 1
    assert "bg-1 (python spew.py) exited with code 3" in told[0]
    assert "line two" in told[0] and "command_output 'bg-1' reads the rest" in told[0]


def test_run_command_takes_notify_where_a_process_can_be_kept(tmp_path: Path) -> None:
    class Keeper:
        where = "here"

        async def run(self, command, cwd, timeout, output_chars=30_000):
            raise AssertionError("not used")

        async def start(self, command, cwd):
            raise AssertionError("not used")

        def peek(self, id):
            return None

        def stop(self, id):
            return None

    tools = {tool.name: tool for tool in shell_tools(tmp_path, Keeper(), notify=lambda text: None)}
    assert "notify" in tools["run_command"].parameters["properties"]

    class Remote:
        where = "in a Function"

        async def run(self, command, cwd, timeout, output_chars=30_000):
            raise AssertionError("not used")

    remote = {tool.name: tool for tool in shell_tools(tmp_path, Remote())}
    assert "notify" not in remote["run_command"].parameters["properties"]


# --- 4.3 the safe calls first, a partial answer asks again, a remembered yes ----------


def publish_tool(workspace: Path) -> Tool:
    def publish(path: str) -> str:
        (workspace / "published.txt").write_text(path, encoding="utf-8")
        return f"published {path}"

    return Tool(
        name="publish_file",
        description="Publish a file outside the conversation.",
        returns="that it was published.",
        leaves="the file published.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        run=publish,
        requires_approval=True,
    )


def checkpointed(tmp_path: Path, backend: ScriptedBackend, tools: Sequence[Tool]) -> Agent:
    agent = Agent(
        backend,
        SqliteStore(tmp_path / "memory.sqlite3"),
        tmp_path / "ws",
        checkpoints=tmp_path / "checkpoints.sqlite3",
    )
    agent.toolbox = lambda _thread: Toolbox(list(tools))  # type: ignore[assignment]
    return agent


async def test_the_safe_calls_run_before_the_question_and_a_partial_answer_asks_again(
    tmp_path: Path,
) -> None:
    (tmp_path / "ws").mkdir()
    clock = Clock()
    backend = ScriptedBackend(
        batch(
            ("r1", "look", {"label": "a"}),
            ("p1", "publish_file", {"path": "one.txt"}),
            ("p2", "publish_file", {"path": "two.txt"}),
        ),
        says("Published what you allowed."),
    )
    agent = checkpointed(tmp_path, backend, [clock.read(0.01), publish_tool(tmp_path / "ws")])
    try:
        first = await agent.answer("t", user("publish both"))
        assert "a" in clock.spans, "the read ran before anyone was asked"
        assert not (tmp_path / "ws" / "published.txt").exists()
        pending = await agent.pending("t")
        assert [call["id"] for call in pending] == ["p1", "p2"]
        assert [m.role for m in first] == ["assistant"], "the batch's results wait with the question"

        # Telegram's shape: one button, one answer. The other call stays asked.
        none = [m async for m in agent.resume("t", {"p1": True})]
        assert none == []
        pending = await agent.pending("t")
        assert [call["id"] for call in pending] == ["p2"]
        assert not (tmp_path / "ws" / "published.txt").exists(), "nothing runs until every asked call is answered"

        rest = [m async for m in agent.resume("t", {"p2": False})]
    finally:
        await agent.aclose()

    results = [m for m in rest if m.role == "tool"]
    assert [m.tool_call_id for m in results] == ["r1", "p1", "p2"], "the model's order, the held read first"
    assert spoken(results[0]) == "read a"
    assert spoken(results[1]) == "published one.txt"
    assert results[2].failure is not None and results[2].failure.code == "declined"
    assert spoken(rest[-1]) == "Published what you allowed."


async def test_a_remembered_yes_skips_the_question(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    backend = ScriptedBackend(
        batch(("p1", "publish_file", {"path": "one.txt"})),
        says("First."),
        batch(("p2", "publish_file", {"path": "two.txt"})),
        says("Second, unasked."),
        default=says("summary"),
    )
    agent = checkpointed(tmp_path, backend, [publish_tool(tmp_path / "ws")])
    try:
        await agent.answer("t", user("publish one"))
        assert await agent.pending("t") is not None
        first = [m async for m in agent.resume("t", {"p1": "conversation"})]
        assert spoken(first[-1]) == "First."
        grants = json.loads((tmp_path / "ws" / GRANTS_FILE).read_text(encoding="utf-8"))
        assert grants["conversation"]["t"] == [{"tool": "publish_file", "prefix": ""}]

        second = await agent.answer("t", user("publish two"), sequence=2)
        assert await agent.pending("t") is None, "the conversation's yes covers it"
        assert spoken(second[-1]) == "Second, unasked."
        assert (tmp_path / "ws" / "published.txt").read_text(encoding="utf-8") == "two.txt"
    finally:
        await agent.aclose()


def test_grants_are_keyed_by_tool_and_for_a_command_by_its_program(tmp_path: Path) -> None:
    grants = Grants(tmp_path)
    git = ToolCall(id="1", name="run_command", arguments={"command": "git status --short"})
    rm = ToolCall(id="2", name="run_command", arguments={"command": "rm -rf build"})
    assert not grants.allows("t", git)

    grants.remember("t", git, "always")
    assert grants.allows("t", git) and grants.allows("other", git)
    assert not grants.allows("t", rm), "another program is another grant"
    grants.remember("t", rm, "conversation")
    assert grants.allows("t", rm) and not grants.allows("other", rm)
    assert grants.listed("t") == ["always: run_command git", "this conversation: run_command rm"]

    grants.remember("t", rm, "once")
    grants.forget("t")
    assert not grants.allows("t", rm) and grants.allows("t", git)
    grants.forget()
    assert not grants.allows("t", git)


# --- 4.4 a stop ends the call in flight and the model call between chunks -----------------


async def test_a_stop_during_a_batch_ends_the_running_call(store: SqliteStore) -> None:
    clock = Clock()
    stops = MemoryStopRequests()
    backend = ScriptedBackend(
        batch(("r1", "look", {"label": "quick"}), ("w1", "change", {"label": "slow"}), ("r2", "look", {"label": "after"})),
        says("unused"),
    )
    slow = clock.write(5.0)
    agent = build_agent(
        backend,
        Toolbox([clock.read(0.01), slow]),
        store,
        OWNER,
        watch=TurnWatch(stop_poll_seconds=0.05),
        stops=stops,
    )

    async def stop_soon() -> None:
        await asyncio.sleep(0.3)
        await stops.request(OWNER, 11)

    started = time.monotonic()
    _, result = await asyncio.gather(stop_soon(), agent.ainvoke(ask(sequence=10)))

    assert time.monotonic() - started < 3, "the five-second write did not run to its end"
    results = [m for m in result["messages"] if m.role == "tool"]
    assert spoken(results[0]) == "read quick"
    assert results[1].failure is not None and "ended before it finished" in spoken(results[1])
    assert results[2].failure is not None and "was not run" in spoken(results[2])
    assert result["stopping"] == STOP_REQUESTED
    assert spoken(result["messages"][-1]) == STOPPED_TEXT
    assert len(backend.requests) == 1, "no model call after the stop"


class SlowStream(ScriptedBackend):
    """A model that says a word, then hangs, the way a provider stalls."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def stream(self, messages, tools=None, response_format=None) -> AsyncIterator[StreamEvent]:
        self.requests.append(list(messages))
        self.tools_seen.append(tools)
        try:
            yield TextDelta("Starting the answer")
            await asyncio.sleep(10)
            yield CompletionDone(says("never"))
        finally:
            self.closed = True


async def test_a_stop_between_chunks_closes_the_model_call_and_keeps_what_was_seen(
    store: SqliteStore,
) -> None:
    stops = MemoryStopRequests()
    backend = SlowStream()
    agent = build_agent(
        backend, Toolbox(), store, OWNER, watch=TurnWatch(stop_poll_seconds=0.05), stops=stops
    )

    async def stop_soon() -> None:
        await asyncio.sleep(0.3)
        await stops.request(OWNER, 11)

    started = time.monotonic()
    _, result = await asyncio.gather(stop_soon(), agent.ainvoke(ask(sequence=10)))

    assert time.monotonic() - started < 3
    assert backend.closed
    assert result["stopping"] == STOP_REQUESTED
    last = spoken(result["messages"][-1])
    assert last.startswith("Starting the answer") and last.endswith(STOPPED_TEXT)


# --- 4.5 the toolbox grows inside a turn ---------------------------------------------------


def deferred_tool(name: str, description: str) -> Tool:
    return Tool(
        name=name,
        description=description,
        returns="text.",
        leaves="nothing.",
        parameters={"type": "object", "properties": {}},
        run=lambda: f"{name} ran",
        replay_safe=True,
    )


async def test_find_tools_offers_what_it_finds_and_the_next_step_carries_it(store: SqliteStore) -> None:
    catalog = [
        deferred_tool("gh_issue_list", "List the issues of a GitHub repository."),
        deferred_tool("gh_issue_create", "Create an issue in a GitHub repository."),
        deferred_tool("clock_now", "The current time."),
    ]
    toolbox = Toolbox([], deferred=catalog, families=["gh", "clock"])
    backend = ScriptedBackend(
        calls("find_tools", query="github issue"),
        calls("gh_issue_list"),
        says("Listed."),
    )
    agent = build_agent(backend, toolbox, store, OWNER, system_prompt=lambda: capability_brief(toolbox))

    result = await agent.ainvoke(ask("list the issues"))

    assert [t["function"]["name"] for t in backend.tools_seen[0]] == ["find_tools"]
    second = [t["function"]["name"] for t in backend.tools_seen[1]]
    assert second[0] == "find_tools" and set(second[1:]) == {"gh_issue_list", "gh_issue_create"}, "found, both offered; the clock stays in the catalog"
    assert toolbox.deferred_names == ("clock_now",)
    results = [m for m in result["messages"] if m.role == "tool"]
    assert "2 tool(s) are callable from your next step" in spoken(results[0])
    assert spoken(results[1]) == "gh_issue_list ran"
    # The brief the second call read names the found tools and the catalog.
    brief = spoken(backend.requests[1][0])
    assert "gh_issue_list" in brief and "1 more from gh, clock are not listed" in brief
    first_brief = spoken(backend.requests[0][0])
    assert "3 more from gh, clock are not listed: find_tools finds them" in first_brief


def test_a_catalog_name_the_model_guesses_is_told_where_it_is() -> None:
    toolbox = Toolbox([], deferred=[deferred_tool("clock_now", "The current time.")])
    message = toolbox.run(ToolCall(id="1", name="clock_now", arguments={}))
    assert message.failure is not None and message.failure.code == "unknown_tool"
    assert "find_tools offers it" in spoken(message)


def test_the_catalog_and_the_mode_are_in_the_persons_report(tmp_path: Path) -> None:
    toolbox = Toolbox([deferred_tool("read_x", "x")], deferred=[deferred_tool("clock_now", "The current time.")], plan=True)
    report = capability_report(toolbox)
    assert "Findable with find_tools: clock_now" in report
    assert "Mode: plan" in report


# --- 4.6 plan mode -----------------------------------------------------------------------


def test_plan_mode_offers_no_tool_that_changes_or_runs(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    registry = CapabilityRegistry(tmp_path / "ws")
    grant = registry.grant(capabilities=(FILESYSTEM_READ, FILESYSTEM_WRITE, SHELL_RUN))

    full = registry.toolbox(grant)
    plan = registry.toolbox(grant, plan=True)

    assert {"write_file", "edit_file", "apply_patch", "run_command"} <= set(full.names)
    assert not {"write_file", "edit_file", "apply_patch", "run_command"} & set(plan.names)
    assert {"read_file", "search_files", "find_files"} <= set(plan.names)
    brief = capability_brief(plan)
    assert "asked for a plan and no change" in brief and "Your answer is the plan" in brief
    assert "asked for a plan" not in capability_brief(full)


async def test_the_agent_reads_plan_mode_when_it_builds_a_toolbox(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    agent = Agent(ScriptedBackend(), SqliteStore(), tmp_path / "ws")
    try:
        assert "write_file" in agent.toolbox("t").names
        set_mode(tmp_path / "ws", "plan")
        agent.rewire()
        names = agent.toolbox("t").names
        assert "write_file" not in names and "run_command" not in names
        assert "read_file" in names and "todo_write" in names
        set_mode(tmp_path / "ws", "careful")
        assert agent.toolbox("t").requires_approval("write_file")
    finally:
        await agent.aclose()


def test_the_mode_command_knows_three_modes(tmp_path: Path) -> None:
    from app.agent.commands import mode_reply, plan_reply

    (tmp_path / "ws").mkdir()
    agent = Agent(ScriptedBackend(), SqliteStore(), tmp_path / "ws")
    assert "Plan mode from your next message" in mode_reply(agent, "plan")
    assert "Mode: plan" in mode_reply(agent, "")
    assert "always available" in plan_reply(agent, "on")
    assert "Full mode" in mode_reply(agent, "full")


# --- 4.7 the empty completion is covered in test_agent_graph; the repeat note here ---------


async def test_a_declined_call_and_a_read_are_counted_the_way_they_ran(tmp_path: Path) -> None:
    """A declined call is never a spent tool call; a held read is."""

    (tmp_path / "ws").mkdir()
    clock = Clock()
    backend = ScriptedBackend(
        batch(("r1", "look", {"label": "a"}), ("p1", "publish_file", {"path": "one.txt"})),
        says("Done."),
    )
    agent = checkpointed(tmp_path, backend, [clock.read(0.01), publish_tool(tmp_path / "ws")])
    try:
        await agent.answer("t", user("go"))
        rest = [m async for m in agent.resume("t", {"p1": False})]
        graph = await agent._graph("t")
        state = await graph.aget_state({"configurable": {"thread_id": "t"}})
    finally:
        await agent.aclose()
    assert state.values["tool_calls"] == 1
    assert [m.tool_call_id for m in rest if m.role == "tool"] == ["r1", "p1"]


async def test_a_death_at_the_question_keeps_the_held_results_and_marks_the_rest_unknown(
    tmp_path: Path,
) -> None:
    """A worker that died between the batch's safe calls and the question:
    the held results stand, the asked calls are `interrupted`, the turn goes on."""

    (tmp_path / "ws").mkdir()
    clock = Clock()
    backend = ScriptedBackend(
        batch(("r1", "look", {"label": "a"}), ("p1", "publish_file", {"path": "one.txt"})),
        says("Went on."),
    )
    agent = checkpointed(tmp_path, backend, [clock.read(0.01), publish_tool(tmp_path / "ws")])
    try:
        await agent.answer("t", user("go"))
        graph = await agent._graph("t")
        config = {"configurable": {"thread_id": "t"}}
        # The shape a kill leaves: the approve node next, no question raised.
        state = await graph.aget_state(config)
        await graph.aupdate_state(
            config, {"held": state.values["held"], "asked": state.values["asked"]}, as_node="tools"
        )
        assert (await agent.unfinished("t")) is not None
        taken = [
            event.message
            async for event in agent.resume_interrupted_events("t")
            if isinstance(event, MessageProduced)
        ]
        stored = agent.history("t")
    finally:
        await agent.aclose()
    results = [m for m in stored if m.role == "tool"]
    assert [m.tool_call_id for m in results] == ["r1", "p1"]
    assert spoken(results[0]) == "read a"
    assert results[1].failure is not None and results[1].failure.code == "interrupted"
    assert spoken(taken[-1]) == "Went on." and spoken(stored[-1]) == "Went on."
