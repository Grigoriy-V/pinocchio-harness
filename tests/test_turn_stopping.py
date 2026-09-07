"""When a model result that would end the turn is allowed to end it.

The seam has one default and one alternative, and both are the subject here:
nothing wired means an ordinary answer ends the turn in one model call, and an
extension that returns explicit steering gets one more step of the same turn
without the candidate it steered ever becoming an answer.

Everything runs against a scripted backend and a temporary store. No network, no
model endpoint, no worker.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.graph import RUN_ID, TurnWatch, build_agent
from app.agent.runtime import Agent, AnswerWithdrawn, MessageProduced
from app.agent.stop import MemoryStopRequests
from app.agent.stopping import (
    STEERING_FRAME,
    Candidate,
    Steering,
    steering_message,
)
from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import Completion, ContentPart, Message
from app.telemetry import Telemetry, TurnRun
from app.telemetry.sqlite import SqliteTelemetry
from app.tools import Tool, Toolbox, filesystem_tools
from tests.fakes import ScriptedBackend, calls, says

OWNER = LOCAL_USER_ID
CHECK = "you have not looked at what you produced; check it before finishing"


@pytest.fixture
def store() -> SqliteStore:
    with SqliteStore() as store:
        yield store


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    room = tmp_path / "workspace"
    room.mkdir()
    return room


class Never:
    """Records what it was asked about and always lets the turn stop."""

    def __init__(self) -> None:
        self.seen: list[Candidate] = []

    async def stopping(self, candidate: Candidate) -> Steering | None:
        self.seen.append(candidate)
        return None


class Once(Never):
    """Steers the first candidate of a turn and accepts the next one."""

    def __init__(self, instruction: str = CHECK) -> None:
        super().__init__()
        self.instruction = instruction

    async def stopping(self, candidate: Candidate) -> Steering | None:
        self.seen.append(candidate)
        if len(self.seen) > 1:
            return None
        return Steering(self.instruction, source="test")


class Always(Never):
    """Never lets a turn stop. Only the recursion guard ends it."""

    async def stopping(self, candidate: Candidate) -> Steering | None:
        self.seen.append(candidate)
        return Steering("keep going", source="test")


class Broken:
    """Fails with a message that quotes the candidate, as a real one might."""

    async def stopping(self, candidate: Candidate) -> Steering | None:
        raise RuntimeError(f"cannot judge {candidate.text!r}")


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
    clock = Clock(step)
    monkeypatch.setattr(
        "app.agent.graph.time", SimpleNamespace(monotonic=clock.monotonic)
    )
    return clock


def ping(recorded: list[str] | None = None) -> Tool:
    def run() -> str:
        if recorded is not None:
            recorded.append("ran")
        return "pong"

    return Tool(
        name="ping",
        description="answer",
        parameters={"type": "object", "properties": {}},
        run=run,
    )


def inspect_page(recorded: list[str]) -> Tool:
    """Stands in for the real browser tool: it says what it saw."""

    def run(path: str) -> str:
        recorded.append(path)
        return "visible text: Hello. console errors: none."

    return Tool(
        name="inspect_page",
        description="open a local page and report what is visible",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        run=run,
    )


def loop(
    backend: ScriptedBackend,
    store: SqliteStore,
    tools: list[Tool] | None = None,
    stopping=None,
    watch: TurnWatch | None = None,
    stops=None,
):
    kwargs = {}
    if stopping is not None:
        kwargs["stopping"] = stopping
    if stops is not None:
        kwargs["stops"] = stops
    return build_agent(
        backend,
        Toolbox(tools if tools is not None else [ping()]),
        store,
        OWNER,
        watch=watch,
        **kwargs,
    )


def ask(text: str = "go", sequence: int = 10) -> dict[str, object]:
    return {
        "messages": [Message(role="user", content=[ContentPart(kind="text", text=text)])],
        "sequence": sequence,
    }


def spoken(message: Message) -> str:
    return " ".join(part.text or "" for part in message.content)


def prompt_text(messages) -> str:
    return "\n".join(spoken(message) for message in messages)


# --- the default: stop ------------------------------------------------------


async def test_an_ordinary_answer_ends_the_turn_in_one_model_call(
    store: SqliteStore,
) -> None:
    """The default costs nothing: no second opinion, no validation pass."""

    extension = Never()
    backend = ScriptedBackend(says("42"))
    agent = loop(backend, store, stopping=extension)

    result = await agent.ainvoke(ask("what is it"))

    assert len(backend.requests) == 1
    assert [message.role for message in result["messages"]] == ["user", "assistant"]
    assert spoken(result["messages"][-1]) == "42"
    assert [spoken(message) for message in store.messages("default")] == ["what is it", "42"]
    # It was asked, once, about the one result that would have ended the turn.
    assert len(extension.seen) == 1
    assert extension.seen[0].text == "42"


async def test_the_seam_is_optional(store: SqliteStore) -> None:
    """A caller that knows nothing about stopping still compiles and answers."""

    backend = ScriptedBackend(says("hello"))
    agent = build_agent(backend, Toolbox(), store, OWNER)

    result = await agent.ainvoke(ask("hi"))

    assert spoken(result["messages"][-1]) == "hello"


async def test_a_simple_write_does_not_acquire_a_validation_pass(
    store: SqliteStore, workspace: Path
) -> None:
    """The whole point of stopping by default, stated as a product outcome."""

    backend = ScriptedBackend(
        calls("write_file", path="note.txt", content="hello"),
        says("Written."),
    )
    agent = build_agent(backend, Toolbox(filesystem_tools(workspace)), store, OWNER)

    result = await agent.ainvoke(ask("write hello into note.txt"))

    assert (workspace / "note.txt").read_text(encoding="utf-8") == "hello"
    # Two model calls: the one that wrote and the one that answered. Nothing
    # inspected the file afterwards, because nothing asked it to.
    assert len(backend.requests) == 2
    assert spoken(result["messages"][-1]) == "Written."


# --- the alternative: explicit structured steering ---------------------------


async def test_steering_takes_another_step_of_the_same_turn(
    store: SqliteStore,
) -> None:
    extension = Once()
    backend = ScriptedBackend(says("Done."), says("Checked, and done."))
    agent = loop(backend, store, stopping=extension)

    result = await agent.ainvoke(ask("do it"))

    assert len(backend.requests) == 2
    assert [message.role for message in result["messages"]] == ["user", "assistant"]
    assert spoken(result["messages"][-1]) == "Checked, and done."


async def test_a_steered_candidate_is_never_settled_as_the_turn_result(
    store: SqliteStore,
) -> None:
    """The draft is working material of one turn, not a first answer."""

    backend = ScriptedBackend(says("Done."), says("Checked, and done."))
    agent = loop(backend, store, stopping=Once())

    result = await agent.ainvoke(ask("do it"))

    kept = [spoken(message) for message in store.messages("default")]
    assert kept == ["do it", "Checked, and done."]
    assert "Done." not in [spoken(message) for message in result["messages"]]
    assert result.get("steered") is None


async def test_a_steered_turn_that_adds_nothing_keeps_its_answer(
    store: SqliteStore,
) -> None:
    """Live 2026-09-03: the draft was refused for an open plan item, the model
    closed the item and wrote the same answer again. The draft is the answer
    when the model, having done what it was told, has nothing to add."""

    backend = ScriptedBackend(says("Done."), says(""))
    agent = loop(backend, store, stopping=Once())

    result = await agent.ainvoke(ask("do it"))

    assert len(backend.requests) == 2
    assert spoken(result["messages"][-1]) == "Done."
    assert [spoken(m) for m in store.messages("default")] == ["do it", "Done."]


async def test_an_empty_answer_after_delivered_text_ends_the_turn_quietly(
    store: SqliteStore,
) -> None:
    """The core prompt asks for nothing when nothing is new; that is not an error."""

    backend = ScriptedBackend(says(""))
    agent = loop(backend, store)

    result = await agent.ainvoke(ask("hi"))

    assert [message.role for message in result["messages"]] == ["user"]
    assert [spoken(m) for m in store.messages("default")] == ["hi"]


async def test_the_draft_and_the_instruction_reach_the_next_request(
    store: SqliteStore,
) -> None:
    """Without the draft the model is corrected about something it cannot see."""

    backend = ScriptedBackend(says("Done."), says("Checked, and done."))
    agent = loop(backend, store, stopping=Once())

    await agent.ainvoke(ask("do it"))

    second = prompt_text(backend.requests[1])
    assert "Done." in second
    assert STEERING_FRAME.format(instruction=CHECK) in second
    assert backend.requests[1][-1].role == "user"


async def test_the_steering_says_it_is_not_from_the_person(store: SqliteStore) -> None:
    message = steering_message(Steering("look again"))

    assert message.role == "user"
    assert "not from the user" in spoken(message)
    assert "look again" in spoken(message)


async def test_steering_requires_something_the_model_can_act_on() -> None:
    with pytest.raises(ValueError):
        Steering("   ")


async def test_the_extension_reads_the_turn_and_not_only_the_sentence(
    store: SqliteStore,
) -> None:
    ran: list[str] = []
    extension = Never()
    backend = ScriptedBackend(calls("ping"), says("pong received"))
    agent = loop(backend, store, [ping(ran)], stopping=extension)

    await agent.ainvoke(ask("ping it"))

    candidate = extension.seen[0]
    assert candidate.steps == 2 and candidate.tool_calls == 1
    assert [message.role for message in candidate.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


# --- when the extension is not asked -----------------------------------------


async def test_a_tool_call_is_not_a_result_that_would_end_the_turn(
    store: SqliteStore,
) -> None:
    extension = Never()
    backend = ScriptedBackend(calls("ping"), says("done"))
    agent = loop(backend, store, stopping=extension)

    await agent.ainvoke(ask("ping it"))

    assert [candidate.text for candidate in extension.seen] == ["done"]


async def test_a_turn_the_person_stopped_is_not_asked(store: SqliteStore) -> None:
    extension = Never()
    stops = MemoryStopRequests()
    await stops.request(OWNER, 11)
    backend = ScriptedBackend(default=calls("ping"))
    agent = loop(backend, store, stopping=extension, stops=stops)

    result = await agent.ainvoke(ask(sequence=10))

    assert result["stopping"] == "stopped"
    assert extension.seen == []


async def test_a_context_refusal_is_not_asked(store: SqliteStore) -> None:
    """There is nothing to steer: the request could not be made at all."""

    from app.models import ContextOverflowError

    extension = Never()
    backend = ScriptedBackend(ContextOverflowError("too large"))
    agent = loop(backend, store, stopping=extension)

    result = await agent.ainvoke(ask("a very long thing"))

    assert extension.seen == []
    assert "too large for the model" in spoken(result["messages"][-1])


# --- the seam cannot run away with the turn ----------------------------------


async def test_an_extension_that_never_stops_is_stopped_by_the_recursion_guard(
    store: SqliteStore,
) -> None:
    """There is no step ceiling since 2026-09-07; an extension caps itself
    (`FinishesItsOwnList(limit=…)`), and one that does not meets LangGraph's
    guard, which is an error rather than a turn."""

    from langgraph.errors import GraphRecursionError

    extension = Always()
    backend = ScriptedBackend(default=says("still going"))
    agent = loop(backend, store, stopping=extension)

    with pytest.raises(GraphRecursionError):
        await agent.ainvoke(ask("go"), config={"recursion_limit": 8})


async def test_an_extension_that_fails_does_not_fail_the_turn(
    store: SqliteStore,
) -> None:
    backend = ScriptedBackend(says("the answer"))
    agent = loop(backend, store, stopping=Broken())

    result = await agent.ainvoke(ask("go"))

    assert len(backend.requests) == 1
    assert spoken(result["messages"][-1]) == "the answer"


# --- what the model still owns -----------------------------------------------


async def test_a_failed_tool_reaches_the_model_which_adapts_in_the_same_loop(
    store: SqliteStore, workspace: Path
) -> None:
    """Nothing in the seam intercepts a tool error; the model answers for it."""

    (workspace / "notes.txt").write_text("the answer is 42", encoding="utf-8")
    backend = ScriptedBackend(
        calls("read_file", path="missing.txt"),
        calls("read_file", path="notes.txt"),
        says("missing.txt is not there; notes.txt says the answer is 42"),
    )
    agent = build_agent(backend, Toolbox(filesystem_tools(workspace)), store, OWNER)

    result = await agent.ainvoke(ask("read missing.txt"))

    failed = result["messages"][2]
    assert failed.role == "tool" and spoken(failed).startswith("error:")
    assert spoken(result["messages"][-1]).startswith("missing.txt is not there")


async def test_the_model_chooses_the_observation_a_steered_turn_uses(
    store: SqliteStore, workspace: Path
) -> None:
    """The HTML scenario: steering says finish properly, the model picks a tool.

    The extension names no tool and no file format. What it produced is another
    step; `inspect_page` is the model's own choice within it, and it is a real
    tool run whose result the model then answers from.
    """

    looked: list[str] = []
    (workspace / "page.html").write_text("<p>Hello</p>", encoding="utf-8")
    backend = ScriptedBackend(
        calls("write_file", path="page.html", content="<p>Hello</p>"),
        says("The page is ready."),
        calls("inspect_page", path="page.html"),
        says("Ready, and the page renders Hello with no console errors."),
    )
    agent = build_agent(
        backend,
        Toolbox([*filesystem_tools(workspace), inspect_page(looked)]),
        store,
        OWNER,
        stopping=Once(),
    )

    result = await agent.ainvoke(ask("make page.html say Hello"))

    assert looked == ["page.html"]
    assert spoken(result["messages"][-1]).startswith("Ready, and the page renders")
    assert [spoken(message) for message in store.messages("default")][-1].startswith(
        "Ready, and the page renders"
    )


def test_the_stopping_seam_names_no_tool_and_no_file_format() -> None:
    """Guard: policy stays with the extension and the model, not in the loop."""

    forbidden = ("inspect_page", "html", "pdf", "screenshot", "validate")
    for path in (
        Path("app/agent/stopping.py"),
        Path("app/agent/graph.py"),
        Path("app/agent/runtime.py"),
    ):
        source = path.read_text(encoding="utf-8").lower()
        assert not [word for word in forbidden if word in source], path


# --- what an interface is told -----------------------------------------------


async def test_a_withdrawn_candidate_is_not_produced_as_a_message(
    tmp_path: Path, workspace: Path
) -> None:
    """The runtime's own boundary: a draft is announced, never delivered."""

    backend = ScriptedBackend(says("Done."), says("Checked, and done."))
    agent = Agent(
        backend,
        SqliteStore(str(tmp_path / "memory.sqlite3")),
        workspace,
        stopping=Once(),
    )
    try:
        events = [
            event
            async for event in agent.events(
                "thread", Message(role="user", content=[ContentPart(kind="text", text="do it")])
            )
        ]
    finally:
        await agent.aclose()

    withdrawn = [event for event in events if isinstance(event, AnswerWithdrawn)]
    produced = [event for event in events if isinstance(event, MessageProduced)]
    assert [spoken(event.message) for event in withdrawn] == ["Done."]
    assert [spoken(event.message) for event in produced] == ["Checked, and done."]


# --- corrections ------------------------------------------------------------


async def test_steering_reaches_an_agent_that_has_no_tools(store: SqliteStore) -> None:
    """Whether a turn may end is a state of the turn, not a property of a toolbox.

    Reading finalization off "were tools offered" disabled the seam exactly
    where a caller most plainly meant it: an agent with no tools at all, which
    can only ever end a turn by answering.
    """

    extension = Once()
    backend = ScriptedBackend(says("draft"), says("the accepted answer"))
    agent = build_agent(backend, Toolbox(), store, OWNER, stopping=extension)

    result = await agent.ainvoke(ask("do it"))

    assert len(backend.requests) == 2
    assert len(extension.seen) == 2
    assert spoken(result["messages"][-1]) == "the accepted answer"
    assert [spoken(message) for message in store.messages("default")] == [
        "do it",
        "the accepted answer",
    ]


async def test_the_trace_records_that_a_turn_was_steered_and_no_word_of_it(
    store: SqliteStore, tmp_path: Path
) -> None:
    """Telemetry is timings, counts and state transitions. Never content.

    The instruction an extension writes is as private as anything else in the
    conversation — it can quote the person, the draft, or a file — so the trace
    keeps who objected and where, and nothing that was said.
    """

    private = "the note about Grigoriy's salary is wrong; check it"
    telemetry = Telemetry(SqliteTelemetry(tmp_path / "telemetry.sqlite3"))
    run = TurnRun(run_id="run-1", user_id=OWNER, thread_id="default")
    trace = telemetry.start(run)
    backend = ScriptedBackend(says("a private draft"), says("the accepted answer"))
    agent = build_agent(
        backend, Toolbox(), store, OWNER, telemetry=telemetry, stopping=Once(private)
    )

    await agent.ainvoke(
        ask("what about the note"),
        config={"configurable": {"thread_id": "default", RUN_ID: "run-1"}},
    )
    trace.flush()

    events = telemetry.store.events("run-1")
    steered = [event for event in events if event.type == "turn_steered"]
    assert [event.data.get("source") for event in steered] == ["test"]
    assert [event.data.get("step") for event in steered] == [1]
    written = json.dumps([event.data for event in events])
    for content in (private, "a private draft", "what about the note"):
        assert content not in written
    telemetry.close()


async def test_a_failing_extension_is_recorded_by_type_and_not_by_message(
    store: SqliteStore, tmp_path: Path
) -> None:
    """An exception raised over a candidate can carry the candidate inside it."""

    telemetry = Telemetry(SqliteTelemetry(tmp_path / "telemetry.sqlite3"))
    run = TurnRun(run_id="run-2", user_id=OWNER, thread_id="default")
    trace = telemetry.start(run)
    backend = ScriptedBackend(says("a private draft"))
    agent = build_agent(
        backend, Toolbox(), store, OWNER, telemetry=telemetry, stopping=Broken()
    )

    result = await agent.ainvoke(
        ask("go"), config={"configurable": {"thread_id": "default", RUN_ID: "run-2"}}
    )
    trace.flush()

    assert spoken(result["messages"][-1]) == "a private draft"
    failed = [
        event
        for event in telemetry.store.events("run-2")
        if event.type == "turn_stopping_failed"
    ]
    assert [event.data.get("error") for event in failed] == ["RuntimeError"]
    assert "a private draft" not in json.dumps([event.data for event in failed])
    telemetry.close()


async def test_the_spend_an_extension_is_shown_includes_the_call_it_judges(
    store: SqliteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch, step=2.0)
    extension = Never()
    backend = ScriptedBackend(says("done"))
    agent = loop(backend, store, stopping=extension)

    result = await agent.ainvoke(ask("go"))

    assert [candidate.spent_seconds for candidate in extension.seen] == [2.0]
    assert result["spent_seconds"] == 2.0
