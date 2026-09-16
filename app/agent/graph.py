"""The agent: load context, ask the model, run tools, persist.

Four nodes and two conditional edges — one asking whether the model wants a
tool, one asking whether the turn is still allowed to run. The state holds the
project's own `Message` objects — a framework's message classes are not adopted as the domain language,
so multimodal content stays in a format this repository controls.

Nothing here knows which model answers, where the tools read from, or where the
conversation is stored; all three are arguments.
"""

from __future__ import annotations

import asyncio
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

from app.agent.grants import NO_GRANTS, scope_of
from app.agent.interjections import NO_INTERJECTIONS, Interjections
from app.agent.notices import NO_NOTICES, Notices, notice_steering
from app.agent.stop import NO_STOPS, StopRequests
from app.agent.stopping import (
    STOP_ON_ANSWER,
    Candidate,
    Steered,
    Steering,
    TurnStopping,
    steering_message,
)
from app.context import Context, ContextPolicy, fold_older_messages, load_turn_context
from app.context.window import system, DEFAULT_SYSTEM_PROMPT
from app.tools.execution import PreparedToolCall, Spill
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
from app.telemetry.trace import spent as timed
from app.tools import (
    DECLINED,
    INTERRUPTED,
    NOT_RUN,
    ToolExecutor,
    Toolbox,
    refusal_message,
    tool_failed,
)

# The keys the graph's custom stream channel carries: a text delta, a tool
# call that launched, a call that returned. The channel carries anything, so
# the runtime and the graph have to agree on the names; nothing else in this
# project writes to it.
ASSISTANT_DELTA = "assistant_delta"
TOOL_STARTED = "tool_started"
TOOL_FINISHED = "tool_finished"

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
    # Which is why a message the person sent mid-turn rides in the tools
    # node's patch *after* that batch's results, never first in a patch.
    return [*current, *incoming]


@dataclass(frozen=True)
class TurnWatch:
    """How long a turn works before it is asked whether it is still on track.

    A turn has no ceiling on steps, tool calls or seconds (the human,
    2026-09-07): a ceiling ends work that is going well, and with a hosted
    model there is no bill it protects. What bounds a turn instead is health.
    After `check_seconds` of work, and again after each further interval, the
    tools' results are followed by one question from the harness, framed as
    turn control: is the work progressing and what is left. The model's next
    completion is the answer, and the decision stays the model's: a tool call
    or text continues, an answer ends the turn as it always did. A model that
    does not answer at all is a model call that timed out, which fails the
    turn with a message the way any timed-out call does.

    Time is accumulated by the nodes rather than measured from the turn's
    start, so a turn that waited an hour for someone to approve a call is not
    asked the moment they answer. Zero asks never.

    The loop's other bound is LangGraph's recursion limit, raised well above
    any real turn in `Agent`: a guard against a graph that cannot terminate,
    not a ceiling.
    """

    check_seconds: float = 600.0
    # How often a running tool call or model call asks whether the person
    # said stop (roadmap 32): the stop is read at every launch boundary and,
    # while something runs, at this interval; a stop ends the call in
    # flight. Deployed each read is one query on the control plane.
    stop_poll_seconds: float = 3.0

    def __post_init__(self) -> None:
        if self.stop_poll_seconds <= 0:
            raise ValueError("stop_poll_seconds must be positive")
        if self.check_seconds < 0:
            raise ValueError("check_seconds cannot be negative")

    def due(self, spent_seconds: float, checked_seconds: float) -> bool:
        return self.check_seconds > 0 and spent_seconds - checked_seconds >= self.check_seconds


# Why a turn stopped short of the model deciding it was finished. Empty is the
# ordinary case: nothing stopped it.
STOP_REQUESTED = "stopped"
REPEATED_FAILURE = "repeated"

# The states in which the turn is already finishing: the model gets one last
# request, without tools, for the answer the person is owed, and nothing asks an
# extension whether it may spend more.
ENDING = frozenset({REPEATED_FAILURE})

# A repeated identical call is information, not an ending (roadmap 32;
# DeepSeek's advisory note, OpenClaw's warning then block). From
# `Limits.repeat_note_after` identical outcomes the call runs and its result
# says how many times it already came out the same; from
# `Limits.repeat_stop_after` it is not run and the batch ends with the reason,
# the model keeping its tools for one more response; an identical attempt past
# that ends the turn's tools. Live on 2026-08-30 a malformed `write_file` was
# retried eight times over four minutes; ISS-0019 wrote one page seven times.


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
    # The spend at which the turn was last asked whether it is on track.
    checked_seconds: float = 0.0
    # A batch split by a consent question (roadmap 32): the results of the
    # calls that needed no yes, kept here while the risky ones wait for the
    # person, and the ids of those waiting; the notes a repeated call's result
    # carries. All three are the batch's own and are cleared when it ends.
    held: dict[str, Message] = field(default_factory=dict)
    asked: tuple[str, ...] = ()
    notes: dict[str, str] = field(default_factory=dict)
    # Whether the turn already asked the model, once, to say what it did when
    # its completion was empty; the second empty completion is the fixed line.
    finalized: bool = False
    # The toolbox's version the turn's context was assembled against; the set
    # may grow inside a turn (`find_tools`) and the brief is rebuilt when it did.
    toolbox_version: int = -1


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


def nowhere_to_ask(call: ToolCall) -> Message:
    """The refusal when the call needs a yes and this conversation cannot ask.

    Same answer as a no, said as what it is: the model can finish without the
    call or say what it would have done, rather than take a no the person
    never gave.
    """

    return refusal_message(
        call,
        ToolFailure(
            code=DECLINED,
            message=(
                f"{call.name} needs the person's approval and this conversation "
                "has nowhere to ask, so it was not run; do not try it again"
            ),
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


STOP_REASON = "the user asked to stop; this call was not run"
ENDED_REASON = "the user asked to stop; this call was ended before it finished"


def repeat_note(count: int, kind: str) -> str:
    """What a result carries when the same call already came out the same way."""

    return f"(this exact call already {kind} {count} times in this turn)"


def repeat_reason(count: int, kind: str, ending: bool) -> str:
    """Why a runaway repeat was not run: the count, and what is left to do."""

    tail = (
        "no further tools will run, so say plainly what you could not do and "
        "answer with what you have"
        if ending
        else "change the arguments, or say what you could not do and answer with what you have"
    )
    return f"this exact call already {kind} {count} times in this turn and was not run again; {tail}"


FAILED_KIND = "failed the same way"
SUCCEEDED_KIND = "succeeded"

# What the person is told when the model spent its last request asking for one
# more tool rather than answering, after the same call kept repeating.
REPEAT_ANSWER = (
    "I stopped here: the same call kept coming out the same way, so trying it "
    "again would not have helped."
)

# The one tool-free request a turn makes when the model's completion was empty
# before anything was said (roadmap 32; OpenClaw's finalization pass).
FINALIZE_SOURCE = "finalize"
FINALIZE_INSTRUCTION = "Say in one line what you did and what is left."
NO_ANSWER = "(no answer was produced)"


def finalize() -> Steering:
    return Steering(instruction=FINALIZE_INSTRUCTION, source=FINALIZE_SOURCE)


def no_answer() -> Message:
    return Message(role="assistant", content=[ContentPart(kind="text", text=NO_ANSWER)])


def said_anything(messages: Sequence[Message]) -> bool:
    """Whether the turn so far delivered any text of the assistant's."""

    return any(
        message.role == "assistant" and any(part.text for part in message.content)
        for message in messages
    )


class TurnStopped(Exception):
    """Raised inside a model call when the person asked to stop; carries the
    text streamed so far, which the person already saw."""

    def __init__(self, partial: str = "") -> None:
        super().__init__("stopped")
        self.partial = partial


# The harness's one question to a long turn (`TurnWatch`). Literal: a
# condition and an action, no figure of speech, because a cheap model reads
# imagery as permission (the human's rule, 2026-09-07).
HEALTH_QUESTION = (
    "This turn has been working for {minutes} minutes. In one line, say whether "
    "you are making progress and what is left. Then continue with the next "
    "step, or finish and answer."
)
HEALTH_SOURCE = "watch"


def health_question(spent_seconds: float) -> Steering:
    return Steering(
        instruction=HEALTH_QUESTION.format(minutes=max(1, round(spent_seconds / 60))),
        source=HEALTH_SOURCE,
    )


STOPPED_TEXT = "Stopped at your request."


def stopped_message(partial: str = "") -> Message:
    """The completed assistant turn after a person asked for it to end.

    Written here rather than by the model: someone who asked for the work to
    stop is not asking for one more model call to tell them it stopped. Text
    the model had streamed before the stop stays in front of it: the person
    saw it, and the record keeps what was seen.
    """

    text = f"{partial.rstrip()}\n\n{STOPPED_TEXT}" if partial.strip() else STOPPED_TEXT
    return Message(role="assistant", content=[ContentPart(kind="text", text=text)])


def noted(message: Message, note: str) -> Message:
    """The result with the repeat note after it."""

    return replace(message, content=[*message.content, ContentPart(kind="text", text=note)])


def parallel_ok(item: PreparedToolCall) -> bool:
    """Whether a prepared call may run beside others: a call refused before it
    ran touches nothing; a tool safe to run twice has no effect an order could
    matter to (the references classify per tool, not per argument)."""

    return item.refusal is not None or bool(item.tool is not None and item.tool.replay_safe)


def grouped(items: Sequence[PreparedToolCall], at_most: int) -> list[list[PreparedToolCall]]:
    """The batch as launch groups in the model's order: consecutive calls that
    may run together form one group of at most `at_most`; every other call
    is a group of its own, a barrier."""

    groups: list[list[PreparedToolCall]] = []
    for item in items:
        if parallel_ok(item) and groups and len(groups[-1]) < at_most and all(
            parallel_ok(earlier) for earlier in groups[-1]
        ):
            groups[-1].append(item)
        else:
            groups.append([item])
    return groups


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
    watch: TurnWatch | None = None,
    stops: StopRequests = NO_STOPS,
    stopping: TurnStopping = STOP_ON_ANSWER,
    interjections: Interjections = NO_INTERJECTIONS,
    instructions: Callable[[], str] | None = None,
    spill: Spill | None = None,
    grants: Any = NO_GRANTS,
    notices: Notices = NO_NOTICES,
) -> CompiledStateGraph:
    """Compile the graph. This is the loop, and there is only one of it.

    A turn ends in one of four ways: the model answers without asking for a
    tool, the person asks it to stop, the same call keeps failing, or the
    request cannot be made to fit. The first is the ordinary one. A long turn
    is not ended by the harness; it is asked how it is doing (`watch`).

    With a `checkpointer`, a turn that stops to ask a question — or dies — can be
    resumed from where it stopped. Without one the graph still runs; it just
    cannot stop and come back, so a call needing approval has nowhere to wait and is
    refused rather than run unasked.

    `stops` is asked at each launch boundary and every `stop_poll_seconds`
    while a call runs, never at the start: a turn that has not run anything
    yet has nothing to stop, and in the deployed profile the question costs
    a round trip to the control plane. A stop ends the call in flight.

    `grants` remembers a yes the person gave with a scope; `notices` is where
    the harness reads what ended on its own (a background command's exit).

    `interjections` is asked after each batch of tools, like `stops`: what the
    person wrote while the batch ran enters the turn there, after the results
    and before the next model request, as their own words.

    `stopping` is asked only at the first of those four endings, and only when
    the turn could still afford another step. Its default stops, so wiring
    nothing changes nothing: an ordinary answer still costs one model call.

    `instructions` is asked once per turn rather than captured, because the
    person may rewrite them between two messages and a graph is compiled once
    and kept. That is the whole mechanism by which an edit takes effect without
    a redeploy.
    """

    policy = policy or ContextPolicy()
    limits = watch or TurnWatch()
    # The schemas are rendered into the request by the server's chat template
    # and are part of what it counts. Read from the toolbox at every step
    # (roadmap 32): the offered set may grow inside a turn (`find_tools`), and
    # the estimate is kept per toolbox version so an unchanged set costs one.
    cached: dict[str, Any] = {"version": None, "schemas": None, "tokens": 0}

    def current_schemas() -> tuple[list[dict[str, Any]] | None, int]:
        if cached["version"] != toolbox.version:
            schemas = toolbox.schemas() or None
            cached["schemas"] = schemas
            cached["tokens"] = (
                backend.estimate_tokens([system(json.dumps(schemas, ensure_ascii=False))])
                if schemas
                else 0
            )
            cached["version"] = toolbox.version
        return cached["schemas"], cached["tokens"]

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
            system_prompt() if callable(system_prompt) else system_prompt,
            instructions() if instructions is not None else "",
            policy.keep_results,
            policy.limits,
        )

    def load(state: AgentState) -> dict[str, Any]:
        with timed("context_loaded"):
            return {"context": assemble_context(state), "toolbox_version": toolbox.version}

    # The served model's name, for the trajectory record; a backend without
    # settings (a scripted one) has none.
    model_name = getattr(getattr(backend, "settings", None), "name", None)

    async def until_stopped(awaitable: Any, state: AgentState) -> Any:
        """Await something, asking for a stop every `stop_poll_seconds`; a stop
        cancels it and raises `TurnStopped`. The cancellation is the ordinary
        one: a model request is closed, a tool's process is killed by the
        runner's own `except BaseException`."""

        task = asyncio.ensure_future(awaitable)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=limits.stop_poll_seconds)
                if done:
                    return task.result()
                if await asked_to_stop(state):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise TurnStopped()
        except BaseException:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise

    async def complete(
        prompt: list[Message],
        writer: StreamWriter,
        trace: TurnTrace,
        tools: list[dict[str, Any]] | None,
        state: AgentState,
    ) -> Completion:
        """One model call, streamed or not, with the same result either way.

        Streaming is how the answer becomes visible while it is being written;
        it must not change what the graph does with it. The events carry the
        whole completion, so tool calls, usage and the finish reason survive the
        stream and the rest of this node cannot tell which path it took.

        A stop is read between chunks (roadmap 32; Codex's cancellation
        token): the request is closed and the turn ends with what was seen.
        """

        with trace.model("answer") as measured:
            if not stream_answers:
                # No first-token boundary exists on this path, and inventing one
                # would report a TTFT equal to the whole call.
                completion = await until_stopped(backend.invoke(prompt, tools=tools), state)
                measured.done(completion)
                trace.trajectory(measured.index, prompt, tools, completion, model=model_name)
                return completion
            completion = None
            seen_text = False
            partial: list[str] = []
            stream = backend.stream(prompt, tools=tools)
            try:
                while True:
                    try:
                        event = await until_stopped(stream.__anext__(), state)
                    except StopAsyncIteration:
                        break
                    if isinstance(event, TextDelta):
                        if not seen_text:
                            seen_text = True
                            measured.first_token()
                        partial.append(event.text)
                        # Presentation only. Nothing on this channel is ever persisted.
                        writer({ASSISTANT_DELTA: event.text})
                    else:
                        completion = event.completion
            except TurnStopped:
                raise TurnStopped("".join(partial)) from None
            finally:
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:  # noqa: BLE001 - closing a stream is best effort
                        pass
            if completion is None:
                raise BackendError("the model stream ended without a completion")
            measured.done(completion)
            trace.trajectory(measured.index, prompt, tools, completion, model=model_name)
            return completion

    async def call_model(
        state: AgentState, config: RunnableConfig, writer: StreamWriter
    ) -> dict[str, Any]:
        started = time.monotonic()
        # What ended on its own while no turn ran (a background command's
        # exit) is read at the turn's first step and rides as turn control
        # beside the request, the way the health question rides: read by
        # the model, answered in what it says, not stored as anyone's words.
        told = notices.take(user_id) if state.steps == 0 else []
        if told:
            trace_of(config).event("turn_notified", step=state.steps + 1, count=len(told))
            state = replace(state, steered=notice_steering(told, None))
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
        schemas, schema_tokens = current_schemas()
        # A turn ended by a repeating call still gets to answer, and is offered
        # no tools while it does: what it must not be able to do is try that
        # call once more. The finalization pass is tool-free too.
        finalizing = state.steered is not None and state.steered.steering.source == FINALIZE_SOURCE
        offered = None if state.stopping in ENDING or finalizing else schemas

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
                    # It asked for another tool instead of answering. On the
                    # finalization pass that is an empty answer; after a
                    # runaway repeat, saying so is better than an empty bubble.
                    if finalizing:
                        return None
                    return Message(
                        role="assistant",
                        content=[ContentPart(kind="text", text=REPEAT_ANSWER)],
                    )
                return Message(role="assistant", content=message.content)
            return message

        turn = carried(state)
        # The brief names the tools; when the set grew inside the turn the
        # context is assembled again so the inventory the model reads is true.
        base = state.context if state.toolbox_version == toolbox.version else assemble_context(state)
        prepared = await fitted(state, base, turn, trace, schema_tokens)
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
            completion = await complete(surface.messages, writer, trace, offered, state)
        except TurnStopped as stop:
            trace.event("turn_stopped", step=state.steps + 1, tool_calls=state.tool_calls, where="model")
            return {
                "context": prepared,
                "messages": [stopped_message(stop.partial)],
                "usage": Usage(),
                "stopping": STOP_REQUESTED,
                "toolbox_version": toolbox.version,
            }
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
                completion = await complete(recovered.prompt(turn), writer, trace, offered, state)
            except TurnStopped as stop:
                trace.event("turn_stopped", step=state.steps + 1, tool_calls=state.tool_calls, where="model")
                return {
                    "context": recovered,
                    "messages": [stopped_message(stop.partial)],
                    "usage": Usage(),
                    "stopping": STOP_REQUESTED,
                    "toolbox_version": toolbox.version,
                }
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
        # A health check has no candidate: the question follows the tool
        # results that are already in `messages`.
        candidate = [state.steered.candidate] if state.steered.candidate is not None else []
        return [*state.messages, *candidate, steering_message(state.steered.steering)]

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

        This is also where the step just taken is priced, so the spend an
        extension is shown is the spend that has actually happened.
        """

        # The whole node, including a recovery attempt, is what this call cost.
        spent = state.spent_seconds + (time.monotonic() - started)
        keep = {
            "context": context,
            "usage": completion.usage,
            "spent_seconds": spent,
            "toolbox_version": toolbox.version,
        }
        if message is None:
            if state.steered is not None and state.steered.candidate is not None:
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
            if not said_anything(state.messages):
                # Nothing was said in the whole turn (roadmap 32). One
                # tool-free request asks for one line; if that is empty too,
                # the fixed line, so no turn ends with a delivered nothing.
                if not state.finalized:
                    trace.event("finalization_asked", step=state.steps + 1)
                    return {
                        **keep,
                        "steered": Steered(candidate=None, steering=finalize()),
                        "finalized": True,
                    }
                trace.event("no_answer_produced", step=state.steps + 1)
                return {**keep, "messages": [no_answer()]}
            # Nothing new after what was already said beside the last call.
            # The turn ends here with no further message.
            trace.event("nothing_to_add", step=state.steps + 1)
            return {**keep, "messages": []}
        priced = replace(state, steps=state.steps + 1, spent_seconds=spent)
        if message.tool_calls or state.stopping in ENDING:
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
        state: AgentState, base: Context, turn: list[Message], trace: TurnTrace, schema_tokens: int
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
            return base
        # Headroom for the answer (Codex: 95% of the window usable for
        # inputs): the request folds before it lands within the output's
        # reach of the budget.
        ceiling = max(1, policy.max_input_tokens - policy.limits.output_tokens)
        estimated = backend.estimate_tokens(base.prompt(turn)) + schema_tokens
        if estimated <= ceiling:
            return base
        # As many exchanges as have to go, oldest first; a second fold only
        # when the first, sized on an estimate, fell short. Three is a bound
        # on a summarizer that frees less than it should, not a plan.
        context = base
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
                    excess=now - ceiling,
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
            if now <= ceiling:
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

    async def run_group(
        group: Sequence[PreparedToolCall],
        executor: ToolExecutor,
        state: AgentState,
        trace: TurnTrace,
        writer: StreamWriter,
    ) -> tuple[dict[str, Message], bool]:
        """Launch one group at once and wait, asking for a stop every
        `stop_poll_seconds`; a stop cancels what is still running (the
        runner kills its process) and answers it as ended. Returns the
        results by call id and whether a stop was met."""

        tasks: dict[asyncio.Task[Message], PreparedToolCall] = {}
        for item in group:
            writer({TOOL_STARTED: item.call})
            tasks[asyncio.ensure_future(executor.run(item))] = item
        results: dict[str, Message] = {}
        stopped = False
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(pending, timeout=limits.stop_poll_seconds)
            for task in done:
                item = tasks[task]
                results[item.call.id] = task.result()
                writer({TOOL_FINISHED: (item.call, results[item.call.id])})
            if pending and await asked_to_stop(state):
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in pending:
                    item = tasks[task]
                    trace.event("tool_cancelled", tool=item.call.name)
                    results[item.call.id] = halted(item.call, ENDED_REASON)
                    writer({TOOL_FINISHED: (item.call, results[item.call.id])})
                pending = set()
                stopped = True
        return results, stopped

    async def run_batch(
        items: Sequence[PreparedToolCall],
        executor: ToolExecutor,
        state: AgentState,
        trace: TurnTrace,
        writer: StreamWriter,
    ) -> tuple[dict[str, Message], bool, int]:
        """The calls in the model's order: a group of reads at once, a call
        that changes something alone as a barrier (roadmap 32; DeepSeek's
        exclusive calls, Codex's lock). The stop is read before every launch.
        Returns the results, whether a stop was met, and how many calls ran."""

        results: dict[str, Message] = {}
        stopped = False
        ran = 0
        for group in grouped(items, max(1, policy.limits.parallel_calls)):
            if not stopped and await asked_to_stop(state):
                stopped = True
            if stopped:
                for item in group:
                    results[item.call.id] = halted(item.call, STOP_REASON)
                continue
            if len(group) > 1:
                trace.event("tools_parallel", count=len(group), tools=[item.call.name for item in group])
            found, stopped = await run_group(group, executor, state, trace, writer)
            results.update(found)
            ran += len(group)
        return results, stopped, ran

    async def finished_batch(
        state: AgentState,
        calls: Sequence[ToolCall],
        results: dict[str, Message],
        notes: dict[str, str],
        ran: int,
        stopped: bool,
        started: float,
        trace: TurnTrace,
    ) -> dict[str, Any]:
        """The batch's patch: every call's result in the model's order, then
        what rides after the results (the health question, the person's
        message, a background notice), or the stop."""

        messages = []
        for call in calls:
            message = results.get(call.id) or halted(call, STOP_REASON)
            if call.id in notes:
                message = noted(message, notes[call.id])
            messages.append(message)
        spent_seconds = state.spent_seconds + (time.monotonic() - started)
        patch: dict[str, Any] = {
            "messages": messages,
            "tool_calls": state.tool_calls + ran,
            "spent_seconds": spent_seconds,
            "held": {},
            "asked": (),
            "notes": {},
        }
        if stopped:
            trace.event("turn_stopped", step=state.steps, tool_calls=state.tool_calls + ran, where="tools")
            patch["messages"] = [*messages, stopped_message()]
            patch["stopping"] = STOP_REQUESTED
            return patch
        if limits.due(spent_seconds, state.checked_seconds):
            # The harness's question rides after these results as turn
            # control; the model's next completion answers it and decides.
            trace.event(
                "turn_health_check",
                step=state.steps,
                tool_calls=state.tool_calls + ran,
                spent_ms=int(spent_seconds * 1000),
            )
            patch["steered"] = Steered(candidate=None, steering=health_question(spent_seconds))
            patch["checked_seconds"] = spent_seconds
        # What the person wrote while the batch ran. After the results, so the
        # model reads the batch and then the comment on it; taken here and by
        # no later turn. Never raises, for the same reason `asked_to_stop`
        # does not: a lane that could fail the turn is worse than none.
        try:
            # `timed`, not `spent`: this node counts its calls in `spent`.
            with timed("interjections_read"):
                taken = await interjections.take(user_id, state.sequence)
        except Exception:  # noqa: BLE001 - a message that cannot be read waits for its own turn
            taken = []
        if taken:
            trace.event("turn_interjected", step=state.steps, count=len(taken))
        if taken:
            patch["messages"] = [*messages, *taken]
        # What ended on its own meanwhile (a background command's exit): turn
        # control for the next step, joined to the health question when both
        # are due; read by the model, not stored as anyone's words.
        told = notices.take(user_id)
        if told:
            trace.event("turn_notified", step=state.steps, count=len(told))
            patch["steered"] = notice_steering(told, patch.get("steered"))
        return patch

    def repeats(state: AgentState, prepared: Sequence[PreparedToolCall]) -> tuple[dict[str, str], list[tuple[PreparedToolCall, int, str]]]:
        """For each call, how often it already came out the same: the notes
        to carry, and the calls past the stop count."""

        # Everything before this batch: the attempt being judged is in
        # `state.messages` too, and a fake or a server that reuses call ids
        # would otherwise let it count itself.
        earlier = state.messages[:-1]
        changing = [name for name in toolbox.names if getattr(toolbox.get(name), "mutates", False)]
        notes: dict[str, str] = {}
        runaway: list[tuple[PreparedToolCall, int, str]] = []
        for item in prepared:
            failures = failed_before(earlier, item.call)
            successes = succeeded_before(earlier, item.call, changing)
            count, kind = (failures, FAILED_KIND) if failures >= successes else (successes, SUCCEEDED_KIND)
            if count >= policy.limits.repeat_stop_after:
                runaway.append((item, count, kind))
            elif count >= policy.limits.repeat_note_after:
                notes[item.call.id] = repeat_note(count, kind)
        return notes, runaway

    async def run_tools(
        state: AgentState, config: RunnableConfig, writer: StreamWriter
    ) -> dict[str, Any]:
        started = time.monotonic()
        trace = trace_of(config)
        calls = state.messages[-1].tool_calls

        # Asked before anything runs, and before anyone is asked to approve
        # anything: a person who has already said stop should not then be
        # shown a consent question.
        if await asked_to_stop(state):
            trace.event("turn_stopped", step=state.steps, tool_calls=state.tool_calls, where="tools")
            return {
                "messages": [
                    *(halted(call, STOP_REASON) for call in calls),
                    stopped_message(),
                ],
                "stopping": STOP_REQUESTED,
            }
        executor = ToolExecutor(toolbox, trace, limits=policy.limits, spill=spill)
        prepared = [executor.pre_execute(call) for call in calls]
        notes, runaway = repeats(state, prepared)
        if runaway:
            # A runaway repeat: the batch is not run and the model is told
            # why, keeping its tools for one more response; an identical
            # attempt past the stop count ends the turn's tools.
            item, count, kind = runaway[0]
            ending = count > policy.limits.repeat_stop_after
            trace.event("turn_repeating", tool=item.call.name, attempts=count, step=state.steps, ending=ending)
            patch: dict[str, Any] = {
                "messages": [halted(call, repeat_reason(count, kind, ending)) for call in calls],
                "held": {},
                "asked": (),
                "notes": {},
            }
            if ending:
                patch["stopping"] = REPEATED_FAILURE
            return patch
        # Invalid calls go straight back to the model as tool errors. Asking a
        # user to approve a call that cannot run is both noisy and misleading.
        # A yes the person asked to be remembered is not asked for again.
        risky = [
            item
            for item in prepared
            if item.approval_required and not grants.allows(state.thread_id, item.call)
        ]
        safe = [item for item in prepared if item not in risky]
        # The calls needing no yes run first (roadmap 32; Claude Code's
        # auto-approved reads, Codex's per-call decision); the risky ones are
        # asked after, in one question, by the next node, so that a resume
        # runs nothing twice: their results are already in the state.
        results, stopped, ran = await run_batch(safe, executor, state, trace, writer)
        if risky and checkpointer is None:
            # Nowhere to ask, so the answer is no; the model is told which.
            for item in risky:
                trace.event("tool_failed", tool=item.call.name, status="declined", code=DECLINED)
                results[item.call.id] = nowhere_to_ask(item.call)
            risky = []
        if risky and not stopped:
            trace.event("approval_requested", calls=[item.call.name for item in risky])
            return {
                "held": results,
                "asked": tuple(item.call.id for item in risky),
                "notes": notes,
                "tool_calls": state.tool_calls + ran,
                "spent_seconds": state.spent_seconds + (time.monotonic() - started),
            }
        return await finished_batch(state, calls, results, notes, ran, stopped, started, trace)

    async def approve(
        state: AgentState, config: RunnableConfig, writer: StreamWriter
    ) -> dict[str, Any]:
        """Ask the person about the batch's risky calls and run the approved
        ones. Answers may arrive one at a time (Telegram's one button): a
        call the answer does not name is asked again, not declined. Nothing
        runs before every asked call is answered, because a resume restarts
        this node from its top."""

        started = time.monotonic()
        trace = trace_of(config)
        calls = state.messages[-1].tool_calls
        risky = [call for call in calls if call.id in state.asked]
        answers: dict[str, Any] = {}
        unanswered = list(risky)
        while unanswered:
            reply = interrupt([describe_call(call) for call in unanswered])
            if isinstance(reply, dict):
                for call in unanswered:
                    if call.id in reply:
                        answers[call.id] = reply[call.id]
            unanswered = [call for call in unanswered if call.id not in answers]
        executor = ToolExecutor(toolbox, trace, limits=policy.limits, spill=spill)
        approved: list[PreparedToolCall] = []
        results: dict[str, Message] = dict(state.held)
        for call in risky:
            scope = scope_of(answers.get(call.id))
            if scope is None:
                # Never run, so never counted as a tool call the turn spent.
                trace.event("tool_failed", tool=call.name, status="declined", code=DECLINED)
                results[call.id] = declined(call)
                continue
            grants.remember(state.thread_id, call, scope)
            approved.append(executor.pre_execute(call))
        trace.event(
            "approval_resumed",
            approved=[item.call.name for item in approved],
            declined=[call.name for call in risky if call.id not in {item.call.id for item in approved}],
        )
        ran_results, stopped, ran = await run_batch(approved, executor, state, trace, writer)
        results.update(ran_results)
        return await finished_batch(state, calls, results, state.notes, ran, stopped, started, trace)

    async def persist(state: AgentState, config: RunnableConfig) -> None:
        trace = trace_of(config)
        with trace.step("persist"):
            with timed("history_written"):
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
        # A turn the person stopped does not get another model call to say so;
        # a batch with calls waiting on a yes goes to the question.
        if state.stopping == STOP_REQUESTED:
            return "persist"
        return "approve" if state.asked else "model"

    def after_approve(state: AgentState) -> str:
        return "persist" if state.stopping == STOP_REQUESTED else "model"

    graph = StateGraph(AgentState)
    graph.add_node("load", load)
    graph.add_node("model", call_model)
    graph.add_node("tools", run_tools)
    graph.add_node("approve", approve)
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
    graph.add_conditional_edges(
        "tools", after_tools, {"approve": "approve", "model": "model", "persist": "persist"}
    )
    graph.add_conditional_edges("approve", after_approve, {"model": "model", "persist": "persist"})
    graph.add_edge("persist", END)
    return graph.compile(checkpointer=checkpointer)
