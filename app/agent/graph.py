"""The agent: load context, ask the model, run tools, persist.

Four nodes and two conditional edges — one asking whether the model wants a
tool, one asking whether the turn is still allowed to run. The state holds the
project's own `Message` objects — a framework's message classes are not adopted as the domain language,
so multimodal content stays in a format this repository controls.

Nothing here knows which model answers, where the tools read from, or where the
conversation is stored; all three are arguments.
"""

from __future__ import annotations

import json

import time
from dataclasses import dataclass, field, replace
from collections.abc import Callable, Collection, Sequence
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StreamWriter, interrupt

from app.agent.stop import NO_STOPS, StopRequests
from app.agent.stopping import (
    STOP_ON_ANSWER,
    Candidate,
    Steered,
    TurnStopping,
    steering_message,
)
from app.context import Context, ContextPolicy, fold_older_messages, load_turn_context
from app.context.window import system, DEFAULT_SYSTEM_PROMPT
from app.memory import ConversationStore
from app.models import (
    BackendError,
    Completion,
    ContentPart,
    ContextOverflowError,
    Message,
    ModelBackend,
    TextDelta,
    ToolCall,
    ToolFailure,
    Usage,
)
from app.telemetry import NO_TRACE, Telemetry, TurnTrace
from app.tools import (
    DECLINED,
    INTERRUPTED,
    NOT_RUN,
    ToolExecutor,
    Toolbox,
    refusal_message,
    tool_failed,
)

# The key a text delta travels under on LangGraph's custom stream channel. The
# channel carries anything, so the runtime and the graph have to agree on one
# name; nothing else in this project writes to it.
ASSISTANT_DELTA = "assistant_delta"

# The turn identity travels beside `thread_id` in LangGraph's own configurable
# dictionary. A string, never the recorder itself: this value is carried in
# checkpoint metadata and log lines, and neither may hold a live object.
RUN_ID = "run_id"


def run_id_of(config: RunnableConfig | None) -> str | None:
    if not config:
        return None
    return (config.get("configurable") or {}).get(RUN_ID)


def extend(current: list[Message], incoming: list[Message]) -> list[Message]:
    """Append, except that a user message starts a new turn.

    With a checkpointer the state outlives the turn, so something has to mark
    where one ends, or the next turn would inherit the last one's messages and
    store and send them twice. A user message is that mark: no node produces
    one, so it can only be the beginning of a turn.
    """

    if incoming and incoming[0].role == "user":
        return list(incoming)
    return [*current, *incoming]


@dataclass(frozen=True)
class TurnBudget:
    """What one turn may spend before it has to stop and say so.

    The loop's only other bound is LangGraph's recursion limit, which is a
    guard against a graph that cannot terminate rather than a ceiling on what
    an autonomous turn costs. This is the ceiling: a model that keeps finding
    one more thing to check spends a GPU at roughly $0.0003 a second, and
    nothing else stands between it and the bill.

    Time is accumulated by the nodes rather than measured from the turn's
    start, so a turn that waited an hour for someone to approve a call is not
    over budget the moment they answer.

    Every limit bounds the *work*: when one is crossed no further tool runs,
    and the model is asked once more, without tools, for the answer the person
    is owed. So a ceiling of N steps costs at most N + 1 model calls.
    """

    max_steps: int = 12
    max_tool_calls: int = 24
    max_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls cannot be negative")
        if self.max_seconds <= 0:
            raise ValueError("max_seconds must be positive")


# Why a turn stopped short of the model deciding it was finished. Empty is the
# ordinary case: nothing stopped it.
BUDGET_EXHAUSTED = "budget"
STOP_REQUESTED = "stopped"
REPEATED_FAILURE = "repeated"

# How many times one identical call may fail before the turn stops offering
# tools at all. Two is a real retry — a transient failure deserves one — and the
# third attempt at a call that has already failed twice with the same arguments
# is not recovery, it is a loop. Live on 2026-08-30 a malformed `write_file`
# was retried eight times over four minutes, each attempt regenerating a whole
# page, and only the person ended it.
MAX_IDENTICAL_FAILURES = 2

# How many times one identical call may *succeed* in a turn before it is not
# run again. Two is the same file written twice, which is ordinary; the third
# byte-identical write is the rewrite loop of ISS-0019 (seven in one turn,
# each a full generation). Unlike a repeating failure this does not end the
# turn: the call is answered with "already done" and the model goes on.
MAX_IDENTICAL_SUCCESSES = 2

# The states in which the turn is already finishing: the model gets one last
# request, without tools, for the answer the person is owed, and nothing asks an
# extension whether it may spend more.
ENDING = frozenset({BUDGET_EXHAUSTED, REPEATED_FAILURE})


@dataclass
class AgentState:
    """One turn.

    `messages` are the turn's own messages and the only ones ever stored;
    `context` is assembled per turn and deliberately not persisted. `usage` is
    what the model reported for the last request of the turn, which is how the
    request's real size reaches both the fold and the user interface.

    The counters are the turn's own spend, and `sequence` is the number the
    request arrived with — what a stop is compared against. All of them are
    reset by the caller when a turn begins, because with a checkpointer this
    state outlives the turn and an inherited counter would exhaust the next
    turn's budget before it ran.

    `steered` is the one candidate answer the turn did not accept, carried to
    the next model step and no further. It is deliberately not in `messages`:
    what is in `messages` is what the store keeps and what an interface is told
    about, and a candidate that was steered into another step is neither.
    `steerings` counts how often that has happened, and is the turn's own spend
    in the same way the other counters are: it is what lets an extension refuse
    to object twice about the same thing.
    """

    thread_id: str = "default"
    messages: Annotated[list[Message], extend] = field(default_factory=list)
    context: Context = field(default_factory=Context)
    usage: Usage = field(default_factory=Usage)
    sequence: int = 0
    steps: int = 0
    tool_calls: int = 0
    spent_seconds: float = 0.0
    stopping: str = ""
    steered: Steered | None = None
    steerings: int = 0


def assistant_message(completion: Completion) -> Message:
    """Turn a completion into the assistant turn that produced it.

    A turn that only calls tools has no content, which is why `Message` accepts
    tool calls in place of content.
    """

    parts = [ContentPart(kind="text", text=completion.text)] if completion.text else []
    return Message(role="assistant", content=parts, tool_calls=completion.tool_calls)


def describe_call(call: ToolCall) -> dict[str, Any]:
    """What the user is shown when asked to approve a call."""

    return {"id": call.id, "name": call.name, "arguments": call.arguments}


def declined(call: ToolCall) -> Message:
    """A refusal, phrased as a tool result so the model can react to it.

    The model is told not to retry, because a second identical request would be
    a second question to a user who has already said no.
    """

    return refusal_message(
        call,
        ToolFailure(
            code=DECLINED,
            message=f"the user declined the call to {call.name}; do not try it again",
        ),
    )


def failed_before(messages: Sequence[Message], call: ToolCall) -> int:
    """How often this exact call has failed since anything last succeeded.

    Identity is the tool's name and its arguments, because that is what decides
    the result: a call differing in one character is a different attempt and
    gets its own retries. Only failures count — a tool that succeeded and is
    called again with the same arguments is ordinary work, like writing the
    same file twice.

    A success of any tool in between starts the count over. The guard exists
    for a call that cannot come out differently, and a call whose world has
    changed can: live on 2026-09-03 a look at a file failed twice because the
    file did not exist, the model then wrote the file, and the third look — the
    one that would have worked — was the one refused (ISSUES.md ISS-0013).
    """

    failures = {
        message.tool_call_id
        for message in messages
        if message.role == "tool" and tool_failed(message)
    }
    seen = 0
    for message in messages:
        if message.role == "tool" and not tool_failed(message):
            seen = 0
            continue
        for earlier in message.tool_calls:
            if (
                earlier.id in failures
                and earlier.name == call.name
                and earlier.arguments == call.arguments
                and earlier.raw_arguments == call.raw_arguments
            ):
                seen += 1
    return seen


def succeeded_before(
    messages: Sequence[Message], call: ToolCall, changed_by: Collection[str] = ()
) -> int:
    """How often this exact call has succeeded since the world last changed.

    The same identity as `failed_before` — name and arguments — over the
    results that carry no failure. A success of a *different* call of a tool
    that changes the workspace (`changed_by`, the toolbox's `mutates` names)
    starts the count over: the same command after the file it runs was
    rewritten is a new experiment, not a replay (Hermes's rule, recorded in
    the 2026-09-03 references review and taken 2026-09-04 when the guard
    refused the run of a script's fourth version because the first three had
    "succeeded" — ISS-0042). An identical call does not reset itself, so the
    same script run three times unchanged is still the loop of ISS-0019.
    """

    successes = {
        message.tool_call_id
        for message in messages
        if message.role == "tool" and not tool_failed(message)
    }
    by_id = {
        earlier.id: earlier for message in messages for earlier in message.tool_calls
    }

    def same(earlier: ToolCall) -> bool:
        return (
            earlier.name == call.name
            and earlier.arguments == call.arguments
            and earlier.raw_arguments == call.raw_arguments
        )

    seen = 0
    for message in messages:
        if message.role != "tool" or message.tool_call_id not in successes:
            continue
        earlier = by_id.get(message.tool_call_id or "")
        if earlier is None:
            continue
        if same(earlier):
            seen += 1
        elif earlier.name in changed_by:
            seen = 0
    return seen


INTERRUPTED_REASON = (
    "the worker was restarted while this call was running, so whether it ran "
    "is unknown and nothing was recorded; check the workspace or the "
    "conversation before doing it again"
)


def interrupted(call: ToolCall) -> Message:
    """The result of a call a dead worker left without one.

    The references' shape (DeepSeek's `TOOL_OUTCOME_UNKNOWN`): not a failure
    and not a success, a fact about what the harness knows. The model decides
    what to check; the harness never runs a side effect twice on its own.
    """

    return refusal_message(call, ToolFailure(code=INTERRUPTED, message=INTERRUPTED_REASON))


def already_stored(store: ConversationStore, thread_id: str, messages: Sequence[Message]) -> bool:
    """Whether the thread's tail is already exactly these messages.

    `persist` runs again when a worker died inside it after the store was
    written and before the checkpoint was; appending a second time would give
    the person their own message twice. Compared by role, text and call ids,
    which is what a message is once it is stored.
    """

    if not messages:
        return True
    stored = store.messages(thread_id)
    if len(stored) < len(messages):
        return False
    tail = stored[-len(messages) :]
    return all(
        kept.role == fresh.role
        and kept.tool_call_id == fresh.tool_call_id
        and [call.id for call in kept.tool_calls] == [call.id for call in fresh.tool_calls]
        and "".join(part.text or "" for part in kept.content)
        == "".join(part.text or "" for part in fresh.content)
        for kept, fresh in zip(tail, messages, strict=True)
    )


def halted(call: ToolCall, reason: str) -> Message:
    """A call that was not run, phrased as a tool result the model can read.

    The same shape as `declined`, and for the same reason: the model asked for
    something, and the honest answer to it is a result, not silence.
    """

    return refusal_message(call, ToolFailure(code=NOT_RUN, message=reason))


BUDGET_REASON = (
    "this turn has reached the limit of what it may spend; no further tools "
    "will run, so answer now with what you already have"
)
STOP_REASON = "the user asked to stop; this call was not run"
DONE_REASON = (
    "this exact call has already succeeded twice in this turn with these same "
    "arguments and was not run again; the earlier result stands — change the "
    "arguments, or move on"
)
REPEAT_REASON = (
    "this exact call has already failed the same way in this turn and was not "
    "run again; no further tools will run, so say plainly what you could not do "
    "and answer with what you have"
)
# What the person is told when the model spent its last request asking for one
# more tool rather than answering. Two ways to reach it, and they are not the
# same news: one turn ran out of what it may spend, the other kept making a call
# that kept failing. Saying which is the difference between "try again" and
# "this will fail again the same way".
BUDGET_ANSWER = (
    "I stopped here: this turn reached the limit of what it is allowed to spend."
)
REPEAT_ANSWER = (
    "I stopped here: the same call kept failing in the same way, so trying it "
    "again would not have helped."
)


def stopped_message() -> Message:
    """The completed assistant turn after a person asked for it to end.

    Written here rather than by the model: someone who asked for the work to
    stop is not asking for one more model call to tell them it stopped.
    """

    return Message(
        role="assistant",
        content=[ContentPart(kind="text", text="Stopped at your request.")],
    )


def silent_cut() -> Message:
    """A completed assistant turn when the model's output ran out before a word.

    `finish_reason == "length"` with no text and no call: the whole cap went
    to reasoning the person never sees (ISS-0055, GLM through CometAPI,
    2026-09-06). Ending the turn silently would record a delivered answer
    that nobody received.
    """

    return Message(
        role="assistant",
        content=[
            ContentPart(
                kind="text",
                text=(
                    "I ran out of room for this answer before saying anything: the "
                    "model's output limit was spent before its first visible word. "
                    "Nothing was done. Ask for a smaller piece, or raise "
                    "MODEL_MAX_TOKENS."
                ),
            )
        ],
    )


def context_refusal() -> Message:
    """A completed assistant turn when even one bounded recovery cannot fit."""

    return Message(
        role="assistant",
        content=[
            ContentPart(
                kind="text",
                text=(
                    "I cannot process this request because it is too large for the model's "
                    "context window. Shorten it or start a new conversation."
                ),
            )
        ],
    )


def latest_text(messages: list[Message]) -> str:
    """The newest user text, which is what memory retrieval searches on."""

    for message in reversed(messages):
        if message.role == "user":
            return " ".join(part.text or "" for part in message.content).strip()
    return ""


def build_agent(
    backend: ModelBackend,
    toolbox: Toolbox,
    store: ConversationStore,
    user_id: str,
    policy: ContextPolicy | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    checkpointer: BaseCheckpointSaver | None = None,
    stream_answers: bool = True,
    telemetry: Telemetry | None = None,
    budget: TurnBudget | None = None,
    stops: StopRequests = NO_STOPS,
    stopping: TurnStopping = STOP_ON_ANSWER,
    instructions: Callable[[], str] | None = None,
) -> CompiledStateGraph:
    """Compile the graph. This is the loop, and there is only one of it.

    A turn ends in one of four ways: the model answers without asking for a
    tool, the person asks it to stop, it reaches its budget, or the request
    cannot be made to fit. The first is the ordinary one; the other three are
    the reason this loop is allowed to be autonomous.

    With a `checkpointer`, a turn that stops to ask a question — or dies — can be
    resumed from where it stopped. Without one the graph still runs; it just
    cannot stop and come back, so a call needing approval has nowhere to wait and is
    refused rather than run unasked.

    `stops` is asked at each step boundary and never at the start: a turn that
    has not run anything yet has nothing to stop, and in the deployed profile
    the question costs a round trip to the control plane.

    `stopping` is asked only at the first of those four endings, and only when
    the turn could still afford another step. Its default stops, so wiring
    nothing changes nothing: an ordinary answer still costs one model call.

    `instructions` is asked once per turn rather than captured, because the
    person may rewrite them between two messages and a graph is compiled once
    and kept. That is the whole mechanism by which an edit takes effect without
    a redeploy.
    """

    policy = policy or ContextPolicy()
    limits = budget or TurnBudget()
    schemas = toolbox.schemas() or None
    # The schemas are rendered into the request by the server's chat template
    # and are part of what it counts; estimated once, since a toolbox does not
    # change between the steps of a compiled graph.
    schema_tokens = (
        backend.estimate_tokens([system(json.dumps(schemas, ensure_ascii=False))])
        if schemas
        else 0
    )

    def trace_of(config: RunnableConfig | None) -> TurnTrace:
        """The recorder for the turn this invocation belongs to, if any.

        Looked up rather than captured: the graph is compiled once per thread
        and reused, while a trace belongs to one turn.
        """

        if telemetry is None:
            return NO_TRACE
        return telemetry.trace(run_id_of(config))

    def assemble_context(state: AgentState) -> Context:
        query = latest_text(state.messages)
        return load_turn_context(
            store,
            state.thread_id,
            user_id,
            query,
            policy.retrieved_facts,
            system_prompt,
            instructions() if instructions is not None else "",
            policy.keep_results,
        )

    def load(state: AgentState) -> dict[str, Context]:
        return {"context": assemble_context(state)}

    async def complete(
        prompt: list[Message],
        writer: StreamWriter,
        trace: TurnTrace,
        tools: list[dict[str, Any]] | None,
    ) -> Completion:
        """One model call, streamed or not, with the same result either way.

        Streaming is how the answer becomes visible while it is being written;
        it must not change what the graph does with it. The events carry the
        whole completion, so tool calls, usage and the finish reason survive the
        stream and the rest of this node cannot tell which path it took.
        """

        with trace.model("answer") as measured:
            if not stream_answers:
                # No first-token boundary exists on this path, and inventing one
                # would report a TTFT equal to the whole call.
                completion = await backend.invoke(prompt, tools=tools)
                measured.done(completion)
                return completion
            completion = None
            seen_text = False
            async for event in backend.stream(prompt, tools=tools):
                if isinstance(event, TextDelta):
                    if not seen_text:
                        seen_text = True
                        measured.first_token()
                    # Presentation only. Nothing on this channel is ever persisted.
                    writer({ASSISTANT_DELTA: event.text})
                else:
                    completion = event.completion
            if completion is None:
                raise BackendError("the model stream ended without a completion")
            measured.done(completion)
            return completion

    async def call_model(
        state: AgentState, config: RunnableConfig, writer: StreamWriter
    ) -> dict[str, Any]:
        started = time.monotonic()
        patch = await _ask(state, config, writer, started)
        # One step is one model call and whatever it decided to do next, which
        # is the unit a budget and a reader of the trace both care about.
        patch["steps"] = state.steps + 1
        # A path that reached a stopping decision has already priced this call,
        # because the decision is made *from* the accounted spend. The paths
        # that did not — a request that could not be made at all — are priced
        # here.
        patch.setdefault(
            "spent_seconds", state.spent_seconds + (time.monotonic() - started)
        )
        # A candidate is carried for one step only. Every path through `_ask`
        # that did not steer clears it here, so nothing can inherit a draft.
        patch.setdefault("steered", None)
        return patch

    async def _ask(
        state: AgentState,
        config: RunnableConfig,
        writer: StreamWriter,
        started: float,
    ) -> dict[str, Any]:
        trace = trace_of(config)
        trace.event(
            "loop_step",
            step=state.steps + 1,
            tool_calls=state.tool_calls,
            spent_ms=int(state.spent_seconds * 1000),
            stopping=state.stopping or None,
        )
        # A turn that has spent its budget still gets to answer, and is offered
        # no tools while it does: the alternative is to keep asking a model that
        # keeps calling tools whether it would like to stop now. A turn ended by
        # a repeating call is in exactly the same position: what it must not be
        # able to do is try that call once more.
        offered = None if state.stopping in ENDING else schemas

        def produced(completion: Completion) -> Message | None:
            """What the model wrote, and only that, once the turn is ending.

            `None` is a completion with nothing in it: no text and no call. The
            core prompt asks for exactly that when there is nothing new to say
            after a tool, so it is an ending, not an error.

            A model offered no tools can still ask for one, and a stored
            assistant message whose tool calls have no results is a history the
            next request cannot be built from.
            """

            if not completion.text and not completion.tool_calls:
                return None
            message = assistant_message(completion)
            if offered is None and message.tool_calls:
                if not message.content:
                    # It asked for another tool instead of answering. Saying so
                    # is better than an empty bubble, and better than a lie.
                    ended = (
                        REPEAT_ANSWER
                        if state.stopping == REPEATED_FAILURE
                        else BUDGET_ANSWER
                    )
                    return Message(
                        role="assistant",
                        content=[ContentPart(kind="text", text=ended)],
                    )
                return Message(role="assistant", content=message.content)
            return message

        turn = carried(state)
        prepared = await fitted(state, turn, trace)
        surface = prepared.surface(turn)
        trace.event(
            "context_prepared",
            step=state.steps + 1,
            schemas=schema_tokens if offered else 0,
            prelude=backend.estimate_tokens(surface.prelude),
            history=backend.estimate_tokens(surface.history),
            facts=backend.estimate_tokens(surface.facts),
            turn=backend.estimate_tokens(surface.turn),
            stubbed=surface.stubbed,
            placeholders=surface.placeholders,
        )
        try:
            completion = await complete(surface.messages, writer, trace, offered)
        except ContextOverflowError:
            try:
                folded = await fold_older_messages(
                    backend, store, state.thread_id, policy, force=True
                )
            except ContextOverflowError:
                folded = None
            if folded is None:
                return {"messages": [context_refusal()], "usage": Usage()}

            recovered = assemble_context(state)
            try:
                completion = await complete(recovered.prompt(turn), writer, trace, offered)
            except ContextOverflowError:
                return {
                    "context": recovered,
                    "messages": [context_refusal()],
                    "usage": Usage(),
                }
            return await settled(
                state, recovered, turn, produced(completion), completion, trace, started
            )
        return await settled(
            state, prepared, turn, produced(completion), completion, trace, started
        )

    def carried(state: AgentState) -> list[Message]:
        """The turn as the model sees it, including a candidate it did not keep.

        A steered draft and the instruction that replaced it are appended here
        and nowhere else. The model needs both — without the draft it is being
        corrected about something it cannot see, and without the instruction it
        would simply write the draft again — and neither belongs to the
        conversation, so neither is ever in `messages`.
        """

        if state.steered is None:
            return list(state.messages)
        return [
            *state.messages,
            state.steered.candidate,
            steering_message(state.steered.steering),
        ]

    async def settled(
        state: AgentState,
        context: Context,
        turn: list[Message],
        message: Message | None,
        completion: Completion,
        trace: TurnTrace,
        started: float,
    ) -> dict[str, Any]:
        """The model's result, once it is known whether it ends the turn.

        Only a result that would end the turn is offered to the extension: a
        tool call is the turn continuing on the model's own initiative, and a
        turn already finalizing after its budget or a stop is not asking anyone
        whether it may spend more. **What finalizing means is a state of the
        turn, not the shape of the toolbox.** Reading it off whether tools were
        offered would silently disable the seam for an agent that has no tools
        at all — the one arrangement in which a caller most plainly wired an
        extension on purpose.

        This is also where the step just taken is priced. The elapsed time of
        this very model call is added before the budget is asked whether
        another step fits, so a request that took the turn past its seconds
        cannot be steered into one more, and the spend an extension is shown is
        the spend that has actually happened.
        """

        # The whole node, including a recovery attempt, is what this call cost.
        spent = state.spent_seconds + (time.monotonic() - started)
        keep = {"context": context, "usage": completion.usage, "spent_seconds": spent}
        if message is None:
            if state.steered is not None:
                # The model did what the steering asked and had nothing new
                # to say. The answer it already wrote is the answer: the draft
                # was refused as an ending, never as text, and asking for it
                # again was a second generation of the same words (ISS-0009).
                trace.event("steered_candidate_kept", step=state.steps + 1)
                return {**keep, "messages": [state.steered.candidate]}
            if completion.finish_reason == "length":
                # Not a choice to say nothing: the cap was reached before a
                # visible token (ISS-0055). Said so, rather than silence.
                trace.event("output_cut_silent", step=state.steps + 1)
                return {**keep, "messages": [silent_cut()]}
            # Nothing new after what was already said beside the last call.
            # The turn ends here with no further message.
            trace.event("nothing_to_add", step=state.steps + 1)
            return {**keep, "messages": []}
        # `state.steps` has not been incremented yet, so the question asked of
        # the budget is whether the turn could afford the step *after* this one.
        priced = replace(state, steps=state.steps + 1, spent_seconds=spent)
        if (
            message.tool_calls
            or state.stopping in ENDING
            or exceeded(priced, 0)
        ):
            return {**keep, "messages": [message]}
        try:
            steering = await stopping.stopping(
                Candidate(
                    message=message,
                    messages=(*turn, message),
                    steps=priced.steps,
                    tool_calls=priced.tool_calls,
                    spent_seconds=priced.spent_seconds,
                    steerings=state.steerings,
                )
            )
        except Exception as error:  # noqa: BLE001 - an extension may not fail a turn
            # The type, never the message: an exception raised while an
            # extension was reading a candidate can carry that candidate inside
            # it, and a trace may not hold conversation content.
            trace.event("turn_stopping_failed", error=type(error).__name__)
            steering = None
        if steering is None:
            return {**keep, "messages": [message]}
        # Who objected and where. Nothing the person or the model wrote.
        trace.event("turn_steered", source=steering.source, step=priced.steps)
        return {
            **keep,
            "steered": Steered(candidate=message, steering=steering),
            "steerings": state.steerings + 1,
        }

    async def fitted(
        state: AgentState, turn: list[Message], trace: TurnTrace
    ) -> Context:
        """Fold before asking, if what is about to be sent is already too big.

        The fold used to happen in `persist`, from the size the *previous*
        request reported. That was exact and one turn late: the request that
        overshot was still sent, and with one loop able to spend many steps
        inside a single turn, "next turn" can be a long way past the point where
        the conversation stopped fitting.

        Measuring here means the oversized request is not sent at all. The cost
        is that the size is an estimate rather than a report — see
        `ModelBackend.estimate_tokens` — and the fraction of the window the
        application spends is what makes that trade safe.

        Only stored history folds. The current turn's own messages have not been
        written yet, so a turn that grew large by accumulating tool results is
        not what this shortens; shortening those is 4.6a's work, and until then
        `ContextOverflowError` remains the backstop underneath this.
        """

        if policy.max_input_tokens is None:
            return state.context
        estimated = backend.estimate_tokens(state.context.prompt(turn)) + schema_tokens
        if estimated <= policy.max_input_tokens:
            return state.context
        # As many exchanges as have to go, oldest first; a second fold only
        # when the first, sized on an estimate, fell short. Three is a bound
        # on a summarizer that frees less than it should, not a plan.
        context = state.context
        now = estimated
        folds = 0
        for _ in range(3):
            try:
                folded = await fold_older_messages(
                    backend,
                    store,
                    state.thread_id,
                    policy,
                    force=True,
                    excess=now - policy.max_input_tokens,
                )
            except BackendError as error:
                # A summarizer that could not answer is not a reason to lose
                # the step: the request goes as it is, and the overflow path
                # below answers if it does not fit (ISS-0029).
                trace.event("context_fold_failed", where="fitted", error_type=type(error).__name__)
                break
            if folded is None:
                # Nothing left to fold: the size is the current turn, not the
                # history behind it. Send it and let the overflow path answer.
                break
            folds += 1
            context = assemble_context(state)
            now = backend.estimate_tokens(context.prompt(turn)) + schema_tokens
            if now <= policy.max_input_tokens:
                break
        if folds:
            trace.event(
                "context_folded",
                estimated=estimated,
                budget=policy.max_input_tokens,
                now=now,
                folds=folds,
            )
        return context

    async def asked_to_stop(state: AgentState) -> bool:
        """Whether the person has asked for this turn to end. Never raises.

        A control channel that could fail a turn would be worse than not having
        one: the turn this is protecting is the expensive half of the product.
        """

        try:
            return await stops.requested(user_id, state.sequence)
        except Exception:  # noqa: BLE001 - a stop that cannot be read is not a stop
            return False

    def delivers(call: ToolCall) -> bool:
        tool = toolbox.get(toolbox.resolve(call.name) or call.name)
        return tool is not None and tool.delivers

    def exceeded(state: AgentState, incoming: int) -> str:
        """Which limit the next batch of tools would cross, if any."""

        if state.steps >= limits.max_steps:
            return "steps"
        if state.tool_calls + incoming > limits.max_tool_calls:
            return "tool_calls"
        if state.spent_seconds >= limits.max_seconds:
            return "seconds"
        return ""

    async def run_tools(
        state: AgentState, config: RunnableConfig
    ) -> dict[str, Any]:
        started = time.monotonic()
        trace = trace_of(config)
        calls = state.messages[-1].tool_calls

        # Both checks happen before anything runs, and before anyone is asked to
        # approve anything: a person who has already said stop should not then
        # be shown a consent question, and a turn out of budget should not spend
        # its last seconds waiting for an answer to one.
        if await asked_to_stop(state):
            trace.event("turn_stopped", step=state.steps, tool_calls=state.tool_calls)
            return {
                "messages": [
                    *(halted(call, STOP_REASON) for call in calls),
                    stopped_message(),
                ],
                "stopping": STOP_REQUESTED,
            }
        looping = [
            call
            for call in calls
            # Everything before this batch: the attempt being judged is in
            # `state.messages` too, and a fake or a server that reuses call ids
            # would otherwise let it count itself.
            if failed_before(state.messages[:-1], call) >= MAX_IDENTICAL_FAILURES
        ]
        if looping:
            # Not a budget and not a stop: the turn can afford more, and nobody
            # asked it to end. It is being ended because another attempt at a
            # call that has failed twice identically cannot produce anything
            # the last two did not, and each one costs a full generation.
            trace.event(
                "turn_repeating",
                tool=looping[0].name,
                attempts=failed_before(state.messages[:-1], looping[0]),
                step=state.steps,
            )
            return {
                "messages": [halted(call, REPEAT_REASON) for call in calls],
                "stopping": REPEATED_FAILURE,
            }
        # A call that keeps succeeding identically is not work either. It is
        # answered without running and the turn goes on — not a budget, not
        # an ending, one refused call (ISS-0019).
        # Judged against the whole batch, so a ceiling the batch crosses is
        # still the ceiling; the refused repeats are then simply not run.
        limit = exceeded(state, len(calls))
        changing = [name for name in toolbox.names if getattr(toolbox.get(name), "mutates", False)]
        done_again = [
            call
            for call in calls
            if succeeded_before(state.messages[:-1], call, changing) >= MAX_IDENTICAL_SUCCESSES
        ]
        if done_again:
            trace.event(
                "tool_repeated_success",
                tool=done_again[0].name,
                attempts=succeeded_before(state.messages[:-1], done_again[0], changing),
                step=state.steps,
            )
            calls = [call for call in calls if call not in done_again]
        stopping: dict[str, Any] = {}
        if limit:
            trace.event(
                "turn_budget_exhausted",
                limit=limit,
                step=state.steps,
                tool_calls=state.tool_calls,
                spent_ms=int(state.spent_seconds * 1000),
            )
            stopping = {"stopping": BUDGET_EXHAUSTED}
            # A delivery still goes: it is what the person is owed, it costs no
            # model time, and refusing it left a finished piece of work in the
            # workspace on 2026-09-03 (run `9c42241c`) with a sentence saying so.
            halted_calls = [call for call in calls if not delivers(call)]
            calls = [call for call in calls if delivers(call)]
            if not calls:
                return {
                    "messages": [
                        *(halted(call, BUDGET_REASON) for call in halted_calls),
                        *(halted(call, DONE_REASON) for call in done_again),
                    ],
                    **stopping,
                }
        else:
            halted_calls = []

        executor = ToolExecutor(toolbox, trace)
        prepared = [executor.pre_execute(call) for call in calls]
        # Invalid calls go straight back to the model as tool errors. Asking a
        # user to approve a call that cannot run is both noisy and misleading.
        risky = [item for item in prepared if item.approval_required]
        allowed = dict.fromkeys((call.id for call in calls), True)
        if risky and checkpointer is None:
            allowed.update(dict.fromkeys((item.call.id for item in risky), False))
        elif risky:
            # One question for the whole batch, asked before any tool has run:
            # resuming restarts this node from the top, and a tool that ran
            # before the pause would run a second time.
            trace.event("approval_requested", calls=[item.call.name for item in risky])
            answers = interrupt([describe_call(item.call) for item in risky])
            allowed.update(
                {item.call.id: bool(answers.get(item.call.id)) for item in risky}
            )
            trace.event(
                "approval_resumed",
                approved=[
                    item.call.name for item in risky if allowed[item.call.id]
                ],
            )
        messages = []
        spent = 0
        for item in prepared:
            call = item.call
            if not allowed[call.id]:
                # Never run, so never counted as a tool call the turn spent.
                trace.event(
                    "tool_failed", tool=call.name, status="declined", code=DECLINED
                )
                messages.append(declined(call))
                continue
            result = await executor.run(item)
            spent += 1
            messages.append(result)
        messages.extend(halted(call, BUDGET_REASON) for call in halted_calls)
        messages.extend(halted(call, DONE_REASON) for call in done_again)
        return {
            "messages": messages,
            "tool_calls": state.tool_calls + spent,
            "spent_seconds": state.spent_seconds + (time.monotonic() - started),
            **stopping,
        }

    async def persist(state: AgentState, config: RunnableConfig) -> None:
        trace = trace_of(config)
        with trace.step("persist"):
            if not already_stored(store, state.thread_id, state.messages):
                store.append(state.thread_id, state.messages, user_id)
        try:
            await fold_older_messages(
                backend, store, state.thread_id, policy, state.usage.input_tokens
            )
        except BackendError as error:
            # The answer is already with the person and the turn is stored. A
            # fold that could not be made is tried again before the next
            # step; it must not turn a delivered answer into "That request
            # failed" (ISS-0029).
            trace.event("context_fold_failed", where="persist", error_type=type(error).__name__)

    def after_model(state: AgentState) -> str:
        if state.steered is not None:
            # Nothing was produced for the conversation, so there is nothing to
            # persist yet; the turn takes another step with the steering in it.
            return "model"
        if state.stopping in ENDING:
            # The answer written without tools is the end of the turn, whatever
            # the model asked for while writing it.
            return "persist"
        return "tools" if state.messages[-1].tool_calls else "persist"

    def after_tools(state: AgentState) -> str:
        # A turn the person stopped does not get another model call to say so.
        return "persist" if state.stopping == STOP_REQUESTED else "model"

    graph = StateGraph(AgentState)
    graph.add_node("load", load)
    graph.add_node("model", call_model)
    graph.add_node("tools", run_tools)
    graph.add_node("persist", persist)
    graph.add_edge(START, "load")
    graph.add_edge("load", "model")
    graph.add_conditional_edges(
        "model",
        after_model,
        # The self-edge is the steering seam: a candidate that was not accepted
        # takes another step of the same turn rather than ending it.
        {"tools": "tools", "persist": "persist", "model": "model"},
    )
    graph.add_conditional_edges("tools", after_tools, {"model": "model", "persist": "persist"})
    graph.add_edge("persist", END)
    return graph.compile(checkpointer=checkpointer)
