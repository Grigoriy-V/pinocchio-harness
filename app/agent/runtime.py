"""Wiring: one call builds a working agent, another answers a turn.

This is where the backend, the store, the tools and the graph meet, so that a
consumer — Chainlit today, an HTTP layer later — holds no business logic of its
own and does not know it is talking to a graph.
"""

from __future__ import annotations

import json

import hashlib
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.graph import (
    ASSISTANT_DELTA,
    RUN_ID,
    TurnBudget,
    build_agent,
    interrupted,
    latest_text,
)
from app.agent.mode import careful_enabled
from app.agent.stop import NO_STOPS, StopRequests
from app.agent.stopping import STOP_ON_ANSWER, TurnStopping
from app.agent.todo import FinishesItsOwnList, planning_enabled
from app.checkpoints import CheckpointHandle
from app.capabilities import (
    CHAT_DELIVERY,
    Delivery,
    capability_report,
    system_message,
)
from app.instructions import read_instructions
from app.config import AgentSettings, ModelSettings
from app.context import ContextPolicy, fold_older_messages, load_turn_context
from app.context.choice import context_choice, share
from app.context.window import DEFAULT_SYSTEM_PROMPT, system
from app.memory import LOCAL_USER_ID, ConversationStore, Thread, open_store
from app.models import ContentPart, Message, ModelBackend, Usage
from app.telemetry import NO_TRACE, Telemetry, TurnTrace
from app.preflight import Probe, backend_probe, report, run, store_probes, tool_probes
from app.models.openai_compatible import OpenAICompatibleBackend
from app.tools import (
    DEFAULT_CAPABILITIES,
    PRESENT_FILES,
    CapabilityGrant,
    CapabilityRegistry,
    Runner,
    ToolExecutor,
    Toolbox,
    history_tools,
    memory_tools,
    todo_tools,
    goal_tools,
)

# The checkpoint holds this project's own dataclasses, so LangGraph is told
# which types it is allowed to reconstruct. Nothing else may come back out.
CHECKPOINT_TYPES = [
    ("app.models.base", "Message"),
    ("app.models.base", "ContentPart"),
    ("app.models.base", "ToolCall"),
    ("app.models.base", "ToolFailure"),
    ("app.models.base", "Usage"),
    ("app.context.window", "Context"),
    ("app.agent.stopping", "Steered"),
    ("app.agent.stopping", "Steering"),
]


@dataclass(frozen=True)
class AssistantDelta:
    """Part of an answer being written. Presentation only; never stored."""

    text: str


@dataclass(frozen=True)
class MessageProduced:
    """A message the graph finished. This is what the conversation keeps."""

    message: Message


@dataclass(frozen=True)
class AnswerWithdrawn:
    """An answer the turn did not accept, after it was already being written.

    Streaming makes a candidate visible before the graph has decided it is the
    answer, exactly as a narrated tool call does. An interface that showed the
    text has to be told to take it back; one that shows nothing until a message
    is finished can ignore this. The message is never stored and never
    delivered — it is here so an interface can undo what it already did, not so
    it can present it.
    """

    message: Message


AgentEvent = AssistantDelta | MessageProduced | AnswerWithdrawn


@dataclass(frozen=True)
class Fill:
    """How large the last request actually was, against what it was allowed.

    `used` is the model's own count, so images are counted the way the model
    counts them. `budget` is `None` when the model does not say how much it can
    take, in which case the size is reported and not judged.
    """

    used: int
    budget: int | None

    @property
    def fraction(self) -> float | None:
        return self.used / self.budget if self.budget else None


@dataclass(frozen=True)
class ContextReport:
    """What one conversation's next request would be made of, without sending it.

    Estimates by layer, in the same units the fold decides in; the last
    request's own count and cache hits when this process made one; and the
    budget when the ceiling is already known here. Nothing in it wakes the
    model: the ceiling is read the next time the model answers, not for a
    report.
    """

    size: str
    fraction: float
    ceiling: int | None
    budget: int | None
    messages: int
    summarized_through: int
    stubbed: int
    placeholders: int
    layers: dict[str, int]
    last_used: int | None
    last_cached: int | None


@dataclass(frozen=True)
class Unfinished:
    """A turn a worker started and did not end, as the checkpoint has it.

    `node` is what was about to run when the process died: `tools` means the
    model asked for calls and their results were never recorded, some of them
    may have run; `model` or `load` means nothing is unknown; `persist` means
    the answer exists and was not stored. `request` is the user message the
    turn began with, so the caller can tell whether the update it holds is
    this turn or a later one.
    """

    node: str
    request: Message | None
    messages: tuple[Message, ...]
    tool_calls: int


class Agent:
    """A model, a memory and a set of tools, answering one thread at a time.

    A graph is compiled per thread because `remember_fact` records which
    conversation saved a fact. Compiling is cheap; the alternative is a mutable
    "current thread" hidden inside the toolbox.

    Checkpoints live in their own file. The conversation is in the store and is
    the durable record; a checkpoint is the state of a turn still in flight, in
    LangGraph's schema, and deleting the file loses nothing but the ability to
    finish an interrupted turn.
    """

    def __init__(
        self,
        backend: ModelBackend,
        store: ConversationStore,
        workspace: Path,
        policy: ContextPolicy | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        checkpoints: str | Path | None = None,
        checkpoint_database_url: str = "",
        context_fraction: float = 0.8,
        context_tokens: int | None = None,
        capability_registry: CapabilityRegistry | None = None,
        capability_grant: CapabilityGrant | None = None,
        user_id: str = LOCAL_USER_ID,
        delivery: Delivery = CHAT_DELIVERY,
        stream_answers: bool = True,
        telemetry: Telemetry | None = None,
        turn_budget: TurnBudget | None = None,
        stops: StopRequests = NO_STOPS,
        stopping: TurnStopping = STOP_ON_ANSWER,
    ) -> None:
        self.backend = backend
        self.stream_answers = stream_answers
        # What a turn may spend, and where a request to end one is recorded.
        # Both belong to the agent rather than to a graph, because a graph is
        # compiled per thread and these are the same for every thread a person
        # has: one person, one ceiling, one stop.
        # Named apart from `budget()` below, which answers a different question:
        # that one is how much context a request may occupy, this one is how
        # much work a turn may do.
        self.turn_budget = turn_budget or TurnBudget()
        self.stops = stops
        # Asked when a model result would otherwise end a turn. The default
        # stops, so an agent nobody wired an extension into behaves exactly as
        # it did before the seam existed.
        self.stopping = stopping
        # One recorder for every thread this agent serves. What varies per turn
        # is the run identity, which travels with the invocation.
        self.telemetry = telemetry or Telemetry(None)
        self.store = store
        self.user_id = user_id
        self.workspace = Path(workspace).resolve()
        self.policy = policy or ContextPolicy()
        self.system_prompt = system_prompt
        self.delivery = delivery
        self.checkpoints = checkpoints
        self.context_fraction = context_fraction
        self.context_tokens = context_tokens
        self.capability_registry = capability_registry or CapabilityRegistry(self.workspace)
        if capability_grant is None:
            capabilities = DEFAULT_CAPABILITIES
            if not (delivery.media or delivery.files):
                capabilities = tuple(
                    name for name in capabilities if name != PRESENT_FILES
                )
            capability_grant = self.capability_registry.grant(capabilities=capabilities)
        elif capability_grant.allows(PRESENT_FILES) and not (
            delivery.media or delivery.files
        ):
            raise ValueError("a text-only interface cannot grant file presentation")
        self.capability_grant = capability_grant
        self._graphs: dict[str, CompiledStateGraph] = {}
        self._checkpoint_handle = (
            CheckpointHandle(
                checkpoints,
                database_url=checkpoint_database_url,
                allowed_types=CHECKPOINT_TYPES,
            )
            if checkpoints is not None
            else None
        )
        self._limit: int | None = None
        self._asked_the_limit = False
        self._usage = Usage()

    async def budget(self) -> int | None:
        """How many tokens a request may take, or `None` if the model is silent.

        Asked once. The model behind an agent does not change while it runs, and
        a limit that arrived late would not match the graphs already compiled
        with the earlier one.

        A chosen `context_tokens` wins over the share, and is clamped to what
        the server said it accepts. The clamp is the whole point of asking the
        server rather than configuring the number: a person, or a stale setting,
        can ask for less than the model allows, never for more than it can
        serve. With no limit reported there is nothing to clamp against: a
        chosen number stands as it is (a hosted service says nothing about its
        length, and the person's number is then the only one there is), and
        with no number chosen either there is nothing to take a fraction of, so
        the request stays unbounded here and is bounded by the overflow path.
        """

        if not self._asked_the_limit:
            self._limit = await self.backend.context_limit()
            self._asked_the_limit = True
        if not self._limit:
            return self.context_tokens or None
        if self.context_tokens:
            return min(self.context_tokens, self._limit)
        return int(self._limit * share(context_choice(self.workspace), self.context_fraction))

    async def fill(self) -> Fill | None:
        """How full the last request was, or `None` before there was one."""

        used = self._usage.input_tokens
        return None if used is None else Fill(used=used, budget=await self.budget())

    async def _checkpointer(self) -> object | None:
        """Open the checkpoint file on first use.

        Lazily, because building an agent is synchronous and opening the file is
        not; and because an agent that never runs a turn should not create one.
        """

        if self._checkpoint_handle is None:
            return None
        return await self._checkpoint_handle.open()

    def toolbox(self, thread_id: str) -> Toolbox:
        """The tools this thread's graph is compiled with.

        Exposed because the assistant's own account of what it can do has to be
        read from here rather than described, and because a person asking the
        same question deserves the same source.
        """

        return self.capability_registry.toolbox(
            self.capability_grant,
            [
                *memory_tools(
                    self.store, self.user_id, thread_id, self.policy.retrieved_facts
                ),
                # The way back to what a summary or a stub stands for. Like
                # memory, part of what an agent is here rather than a grant.
                *history_tools(self.store, self.user_id, thread_id),
                # Planning is not a granted capability: it reaches nothing, costs
                # nothing to hold and has no root to be confined to. It is part
                # of what an agent is here, in the way memory is — when the
                # person has switched it on (`/plan on`). Off, the tool and
                # every brief line about it are simply absent.
                *(todo_tools() if planning_enabled(self.workspace) else ()),
                # The goal: the request's parts, written once by the model
                # before it starts. Always offered; the model decides whether
                # a request has more than one thing in it (2026-09-05).
                *goal_tools(),
            ],
            # `careful` mode: the tools that change the workspace ask first.
            # Read here, per toolbox, so `/mode` takes effect from the next
            # message like `/plan` does.
            ask_for_changes=careful_enabled(self.workspace),
        )

    def capabilities(self, thread_id: str) -> str:
        """What this agent can see, hear, send, read and change, for a person."""

        return capability_report(
            self.toolbox(thread_id), self.delivery, self.capability_grant.root
        )

    def probes(self, thread_id: str) -> list[Probe]:
        """Everything this agent claims, expressed as something to try.

        Built from the same store and toolbox the turns use, so a probe cannot
        pass against a arrangement the product does not have.
        """

        return [
            *store_probes(self.store, self.user_id),
            *tool_probes(self.toolbox(thread_id), self.capability_grant.root),
        ]

    async def selftest(self, thread_id: str, include: Sequence[str] = ("free",)) -> str:
        """Try each capability here and say which ones answered.

        `include` is the cost gate: the model probe wakes a GPU and is left out
        unless it is asked for by name.
        """

        probes = self.probes(thread_id)
        if "gpu" in include:
            probes.append(backend_probe(self.backend))
        return report(await run(probes, include))  # type: ignore[arg-type]

    def rewire(self) -> None:
        """Forget the compiled graphs, so the next turn builds its toolbox afresh.

        The toolbox is read once per thread when its graph is compiled. A
        switch the person flips between turns — `/plan off` — is read from
        the workspace at that moment and nowhere later, so the graphs are
        dropped here. Nothing about the conversation is lost: state lives in
        the checkpointer and the store, not in the compiled object.
        """

        self._graphs.clear()

    async def _graph(self, thread_id: str) -> CompiledStateGraph:
        if thread_id not in self._graphs:
            toolbox = self.toolbox(thread_id)
            # The model is told what it actually has. Left to its own account it
            # denies abilities it has and invents tools it does not. What it is
            # deliberately not told is where the workspace is: there is one, and
            # naming it taught the model to build paths into it.
            prompt = system_message(
                toolbox,
                self.delivery,
                self.system_prompt,
                where_commands_run=self.capability_registry.runner.where,
            )
            self._graphs[thread_id] = build_agent(
                self.backend,
                toolbox,
                self.store,
                self.user_id,
                replace(self.policy, max_input_tokens=await self.budget()),
                prompt,
                await self._checkpointer(),
                self.stream_answers,
                self.telemetry,
                self.turn_budget,
                self.stops,
                self.stopping,
                self.instructions,
            )
        return self._graphs[thread_id]

    def instructions(self) -> str:
        """The person's standing instructions, read fresh for each turn."""

        return read_instructions(self.workspace)

    async def _run(
        self, thread_id: str, command: Any, trace: TurnTrace = NO_TRACE
    ) -> AsyncIterator[AgentEvent]:
        """Drive the graph and report what the turn produced, as it happens.

        Two kinds of thing come out, and the difference is the whole point:
        `AssistantDelta` is text on its way to being an answer, presentation
        only and never stored, while `MessageProduced` is a message the graph
        finished and the store will keep. An interface may show the first and
        must act on the second.

        An interrupt arrives here as a patch that is not a node's messages; the
        caller learns about it from `pending`, which keeps this stream to what
        the conversation gained.
        """

        graph = await self._graph(thread_id)
        # The run identity rides beside the thread, so a node can find the
        # recorder for the turn it is part of without the graph holding one.
        config = {
            "configurable": {"thread_id": thread_id, RUN_ID: trace.run.run_id or None}
        }
        stream = graph.astream(command, config=config, stream_mode=["updates", "custom"])
        async for mode, payload in stream:
            if mode == "custom":
                text = (payload or {}).get(ASSISTANT_DELTA) if isinstance(payload, dict) else None
                if text:
                    yield AssistantDelta(text)
                continue
            for node, patch in payload.items():
                if node.startswith("__") or not isinstance(patch, dict):
                    continue
                usage = patch.get("usage")
                if usage is not None:
                    self._usage = usage
                steered = patch.get("steered")
                if steered is not None:
                    # The graph kept this out of the conversation; the only
                    # thing left to undo is whatever the stream already showed.
                    yield AnswerWithdrawn(steered.candidate)
                    continue
                for produced in patch.get("messages") or []:
                    yield MessageProduced(produced)

    async def events(
        self,
        thread_id: str,
        message: Message,
        trace: TurnTrace = NO_TRACE,
        sequence: int = 0,
    ) -> AsyncIterator[AgentEvent]:
        """Run one turn, reporting deltas and finished messages as they occur.

        `sequence` is the number this request arrived with — Telegram's update
        id, or an interface's own counter. A stop recorded after it ends this
        turn; a stop recorded before it belongs to a turn that is already over.

        The counters are reset here rather than defaulted in the state, because
        with a checkpointer the previous turn's state is still there: a turn
        that inherited it would start already out of budget.
        """

        command = {
            "thread_id": thread_id,
            "messages": [message],
            "sequence": sequence,
            "steps": 0,
            "tool_calls": 0,
            "spent_seconds": 0.0,
            "stopping": "",
            "steered": None,
            "steerings": 0,
        }
        async for event in self._run(thread_id, command, trace):
            yield event

    async def resume_events(
        self, thread_id: str, answers: dict[str, bool], trace: TurnTrace = NO_TRACE
    ) -> AsyncIterator[AgentEvent]:
        """Answer the pending question and carry on, reporting the same events."""

        async for event in self._run(thread_id, Command(resume=answers), trace):
            yield event

    async def steps(
        self, thread_id: str, message: Message, sequence: int = 0
    ) -> AsyncIterator[Message]:
        """Yield each message as its node finishes, so a UI can show the work.

        The user's own message is not yielded: the caller already has it. An
        interface that only wants finished messages keeps using this and never
        learns that a turn is streamed at all.
        """

        async for event in self.events(thread_id, message, NO_TRACE, sequence):
            if isinstance(event, MessageProduced):
                yield event.message

    def context_report(self, thread_id: str) -> ContextReport:
        """The next request of this conversation, by layer, without the model.

        The same assembly a turn uses, on an empty turn, so the numbers are the
        ones the fold and the trace see. The ceiling is only reported when it
        is already known in this process; asking the server would wake it.
        """

        context = load_turn_context(
            self.store,
            thread_id,
            self.user_id,
            "",
            self.policy.retrieved_facts,
            self.system_prompt,
            self.instructions(),
            keep_results=self.policy.keep_results,
        )
        surface = context.surface([])
        schemas = self.toolbox(thread_id).schemas()
        estimate = self.backend.estimate_tokens
        _, through = self.store.summary(thread_id)
        size = context_choice(self.workspace)
        fraction = share(size, self.context_fraction)
        ceiling = self._limit if self._asked_the_limit else None
        return ContextReport(
            size=size,
            fraction=fraction,
            ceiling=ceiling,
            budget=int(ceiling * fraction) if ceiling else None,
            messages=len(context.history),
            summarized_through=through,
            stubbed=surface.stubbed,
            placeholders=surface.placeholders,
            layers={
                "schemas": estimate([system(json.dumps(schemas, ensure_ascii=False))]) if schemas else 0,
                "prelude": estimate(surface.prelude),
                "history": estimate(surface.history),
                "facts": estimate(surface.facts),
            },
            last_used=self._usage.input_tokens,
            last_cached=self._usage.cached_tokens,
        )

    async def compact(self, thread_id: str) -> int:
        """Fold the older part of this conversation now. Returns how many
        messages the summary newly covers; zero when there was nothing to fold.

        One summarizer call, so it wakes the model; a person asked for it.
        """

        _, before = self.store.summary(thread_id)
        policy = replace(self.policy, max_input_tokens=await self.budget())
        folded = await fold_older_messages(
            self.backend, self.store, thread_id, policy, force=True, reason="asked"
        )
        if folded is None:
            return 0
        _, after = self.store.summary(thread_id)
        return after - before

    def context_prompt(
        self,
        thread_id: str,
        messages: Sequence[Message],
        system_prompt: str | None = None,
    ) -> list[Message]:
        """Assemble the same bounded conversation layers for an internal decision."""

        query = latest_text(list(messages))
        context = load_turn_context(
            self.store,
            thread_id,
            self.user_id,
            query,
            self.policy.retrieved_facts,
            system_prompt or self.system_prompt,
            keep_results=self.policy.keep_results,
        )
        return context.prompt(messages)

    async def unfinished(self, thread_id: str) -> Unfinished | None:
        """The turn a dead worker left in this thread, if there is one.

        A turn waiting on the person's answer is not one: that is `pending`,
        and it is waiting on purpose. What this finds is a graph with a next
        node and nobody asking anything — the shape a kill leaves behind.
        """

        graph = await self._graph(thread_id)
        state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        if not state.next or any(task.interrupts for task in state.tasks):
            return None
        messages = tuple(state.values.get("messages") or ())
        return Unfinished(
            node=state.next[0],
            request=next((m for m in messages if m.role == "user"), None),
            messages=messages,
            tool_calls=int(state.values.get("tool_calls") or 0),
        )

    async def resume_interrupted_events(
        self, thread_id: str, trace: TurnTrace = NO_TRACE
    ) -> AsyncIterator[AgentEvent]:
        """Take up the turn a dead worker left, reporting the same events.

        A step whose tools were running gets one result per call before the
        graph moves on: a read is simply run now, since running it twice
        changes nothing; anything else is answered `interrupted`, because the
        harness does not know whether it ran and will not run it again to
        find out (2026-09-04 review, DeepSeek's rule). The model then decides
        what to check. A death anywhere else has nothing unknown in it and the
        graph carries on from the checkpoint.
        """

        left = await self.unfinished(thread_id)
        if left is None:
            return
        graph = await self._graph(thread_id)
        config = {
            "configurable": {"thread_id": thread_id, RUN_ID: trace.run.run_id or None}
        }
        replayed = unknown = 0
        if left.node == "tools" and left.messages and left.messages[-1].tool_calls:
            toolbox = self.toolbox(thread_id)
            executor = ToolExecutor(toolbox, trace)
            results: list[Message] = []
            for call in left.messages[-1].tool_calls:
                tool = toolbox.get(toolbox.resolve(call.name) or call.name)
                if tool is not None and tool.replay_safe:
                    results.append(await executor.call(call))
                    replayed += 1
                else:
                    results.append(interrupted(call))
                    unknown += 1
            await graph.aupdate_state(
                config,
                {"messages": results, "tool_calls": left.tool_calls + replayed},
                as_node="tools",
            )
        trace.event("turn_resumed", node=left.node, unknown=unknown, replayed=replayed)
        async for event in self._run(thread_id, None, trace):
            yield event

    async def pending(self, thread_id: str) -> list[dict[str, Any]] | None:
        """The calls this thread is waiting on an answer for, if any.

        Survives a restart, which is the point: the question is in the
        checkpoint, not in the process that asked it.
        """

        graph = await self._graph(thread_id)
        state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        for task in state.tasks:
            for stop in task.interrupts:
                return list(stop.value)
        return None

    async def resume(self, thread_id: str, answers: dict[str, bool]) -> AsyncIterator[Message]:
        """Answer the pending question — call id to approved — and carry on."""

        async for event in self.resume_events(thread_id, answers):
            if isinstance(event, MessageProduced):
                yield event.message

    async def answer(
        self, thread_id: str, message: Message, sequence: int = 0
    ) -> list[Message]:
        """Run one turn and return everything the agent produced for it.

        A turn that stops to ask a question ends here; the caller answers with
        `pending` and `resume`.
        """

        return [
            produced async for produced in self.steps(thread_id, message, sequence)
        ]

    def history(self, thread_id: str) -> list[Message]:
        return self.store.messages(thread_id)

    def record(self, thread_id: str, messages: list[Message]) -> None:
        """Persist UI-native work that did not pass through the chat graph."""

        self.store.append(thread_id, messages, self.user_id)

    def threads(self) -> list[Thread]:
        return self.store.threads(self.user_id)

    async def aclose(self) -> None:
        close = getattr(self.backend, "aclose", None)
        if close is not None:
            await close()
        if self._checkpoint_handle is not None:
            await self._checkpoint_handle.close()
        self.store.close()


def text_message(text: str, role: str = "user") -> Message:
    return Message(role=role, content=[ContentPart(kind="text", text=text)])


# A directory name that cannot climb, hide or collide. Identifiers that already
# look like this are used unchanged so a workspace stays readable to a human;
# anything else is hashed, because sanitizing by substitution would let two
# different people land in one directory.
SAFE_SCOPE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def user_workspace(root: str | Path, user_id: str) -> Path:
    """The directory one person's files live in.

    The workspace is the permission boundary, and a boundary shared by several
    people is not one: the conversational file tools are rooted here, so a
    single directory would let anyone read what anyone else created. Every
    caller that turns a user into an agent goes through this function.
    """

    scope = user_id if SAFE_SCOPE.match(user_id) and user_id not in {".", ".."} else ""
    if not scope:
        scope = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]
    return Path(root).resolve() / scope


def create_agent(
    model_settings: ModelSettings | None = None,
    agent_settings: AgentSettings | None = None,
    user_id: str = LOCAL_USER_ID,
    delivery: Delivery = CHAT_DELIVERY,
    telemetry: Telemetry | None = None,
    stops: StopRequests = NO_STOPS,
    stopping: TurnStopping | None = None,
    system_prompt: str | None = None,
    runner: Runner | None = None,
) -> Agent:
    """Build the default agent from configuration.

    `stopping` defaults here rather than on `Agent`, and the two defaults differ
    on purpose. `Agent` is the mechanism and stops when the model stops, so a
    test or a tool that builds one gets the loop with nothing added. This
    function is the product, and the product's agent finishes what it said it
    would do: an unfinished todo list refuses one ending. An agent that wrote no
    list never meets it.

    `delivery` is the caller's statement of what its interface can put in front
    of a person. It controls whether the explicit presentation capability is
    wired at all; observation tools never infer delivery from their own output.

    `telemetry` is passed in rather than opened here: one interface serves
    several people, and each of them gets an agent, but they all belong to one
    process that should hold one connection and one set of active turns.

    `system_prompt` exists so one prompt can be measured against another
    through the same wiring the product uses. An interface never passes it: the
    prompt a person talks to is the default, and a variant is a comparison.

    `runner` is where a command runs. Left out, it is a process on this
    machine, which is the local profile; the deployed worker passes the Modal
    Function beside the renderer, because a command must never run in the
    container that holds the secrets.
    """

    agent_settings = agent_settings or AgentSettings()
    policy = ContextPolicy(
        keep_turns=agent_settings.keep_turns,
        summarize_after=agent_settings.summarize_after,
        retrieved_facts=agent_settings.retrieved_facts,
        keep_results=agent_settings.keep_results,
    )
    # Each person gets their own root inside the configured workspace. The
    # directory the agent may touch has to exist before it is resolved, or the
    # first `list_files` fails on a machine that has simply never run it.
    Path(agent_settings.workspace).mkdir(parents=True, exist_ok=True)
    workspace = user_workspace(agent_settings.workspace, user_id)
    workspace.mkdir(parents=True, exist_ok=True)
    return Agent(
        backend=OpenAICompatibleBackend(model_settings or ModelSettings()),
        store=open_store(agent_settings),
        workspace=workspace,
        capability_registry=CapabilityRegistry(workspace, runner=runner) if runner else None,
        policy=policy,
        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        checkpoints=agent_settings.checkpoints,
        checkpoint_database_url=agent_settings.database_url,
        context_fraction=agent_settings.context_fraction,
        context_tokens=agent_settings.context_tokens,
        user_id=user_id,
        delivery=delivery,
        stream_answers=agent_settings.stream_answers,
        telemetry=telemetry,
        turn_budget=TurnBudget(
            max_steps=agent_settings.turn_max_steps,
            max_tool_calls=agent_settings.turn_max_tool_calls,
            max_seconds=agent_settings.turn_max_seconds,
        ),
        stops=stops,
        stopping=stopping if stopping is not None else FinishesItsOwnList(),
    )
