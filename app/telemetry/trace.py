"""The recorder the application actually talks to.

Two layers, because they fail differently. Every event is written immediately as
one structured log line: it costs nothing, blocks nothing, survives the death of
the container and is readable while the turn is still running — and the local
profile, which has no dashboard at all, gets the same lines on its terminal. The
database is written in bounded batches, because it is the durable half: only it
survives long enough to answer "every failed turn this week" and to compare a
changed agent loop against a baseline.

A measurement is taken when the step happens; buffering changes only when rows
travel to the database, never a measured value.

**Nothing here may fail a turn.** Telemetry that can break the product it
observes is worse than no telemetry, so every public method swallows its own
errors. The same rule the answer preview follows.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from app.telemetry.base import (
    Outcome,
    Status,
    TelemetryStore,
    TraceEvent,
    TurnRun,
    stamp,
)

if TYPE_CHECKING:  # The webhook imports this module on its cold path, and the
    # model layer is deliberately not on it. Nothing here needs a `Completion`
    # at runtime — only the one call site that hands one over does.
    from app.models import Completion

# How many events may wait in memory. An ordinary conversational turn produces
# fewer than this and is one write; a long task crosses it and does not lose
# everything if its container dies.
FLUSH_AT = 25


def log_event(event: TraceEvent) -> None:
    """One structured line, immediately, whatever the database is doing."""

    payload: dict[str, Any] = {
        "run_id": event.run_id,
        "event": event.type,
        "seq": event.seq,
        "timestamp": event.timestamp,
        **event.data,
    }
    if event.duration_ms is not None:
        payload["duration_ms"] = event.duration_ms
    try:
        print(json.dumps(payload, default=str), flush=True)
    except Exception:  # noqa: BLE001 - an observation cannot break a turn
        pass


class TurnTrace:
    """One turn's counters, events and summary row."""

    def __init__(
        self,
        run: TurnRun,
        store: TelemetryStore | None = None,
        *,
        offset_ms: int = 0,
        flush_at: int = FLUSH_AT,
    ) -> None:
        self.run = run
        self.store = store
        # What happened before this object existed — the wait between the person
        # pressing send and a worker claiming their update. Without it every
        # measured latency would begin after the queue and the worker's cold
        # start, which are exactly the parts worth seeing.
        self.offset_ms = max(0, offset_ms)
        self.flush_at = flush_at
        self._origin = time.monotonic()
        self._seq = 0
        self._pending: list[TraceEvent] = []
        self._finished = False
        # What the turn is currently doing, carried by every event emitted
        # inside it. A trace of twenty tool calls is only readable if each one
        # says which stage and which attempt spent it.
        self._context: dict[str, Any] = {}

    # --- elapsed -------------------------------------------------------------

    def elapsed_ms(self) -> int:
        """Milliseconds since the person's message arrived, not since claim."""

        return self.offset_ms + int((time.monotonic() - self._origin) * 1000)

    # --- events --------------------------------------------------------------

    def event(self, type: str, duration_ms: int | None = None, **data: Any) -> None:
        try:
            self._seq += 1
            merged = {**self._context, **data}
            record = TraceEvent(
                run_id=self.run.run_id,
                seq=self._seq,
                type=type,
                timestamp=stamp(),
                duration_ms=duration_ms,
                data={key: value for key, value in merged.items() if value is not None},
            )
            log_event(record)
            self._pending.append(record)
            if len(self._pending) >= self.flush_at:
                self.flush()
        except Exception:  # noqa: BLE001
            pass

    def flush(self) -> None:
        """Write what has accumulated. Best effort, by design."""

        if not self._pending or self.store is None:
            self._pending.clear()
            return
        batch, self._pending = self._pending, []
        try:
            self.store.record_events(batch)
        except Exception:  # noqa: BLE001
            pass

    # --- the turn ------------------------------------------------------------

    def start(self) -> None:
        self.event(
            "turn_started",
            user_id=self.run.user_id or None,
            thread_id=self.run.thread_id or None,
            source=self.run.source or None,
            queued_ms=self.offset_ms or None,
        )
        if self.store is not None:
            try:
                self.store.start_turn(self.run)
            except Exception:  # noqa: BLE001
                pass

    def route(self, route: str) -> None:
        self.run.route = route

    def visible(self, how: str) -> None:
        """The moment the person had something to read.

        Recorded once. A preview and the final message are both first visibility
        depending on how the answer arrived, and a short answer that never
        previewed did become visible.
        """

        if self.run.first_visible_ms is None:
            self.run.first_visible_ms = self.elapsed_ms()
        self.event(f"telegram_{how}", elapsed_ms=self.elapsed_ms())

    def finish(
        self,
        outcome: Outcome,
        *,
        status: Status | None = None,
        error_type: str | None = None,
    ) -> None:
        """Close the turn. Safe to call twice; the first answer is the one kept."""

        if self._finished:
            return
        self._finished = True
        run = self.run
        run.outcome = outcome
        run.status = status or ("failed" if outcome == "failed" else "completed")
        run.error_type = error_type
        run.finished_at = stamp()
        run.total_ms = self.elapsed_ms()
        self.event(
            "turn_failed" if outcome == "failed" else "turn_finished",
            duration_ms=run.total_ms,
            outcome=outcome,
            error_type=error_type,
            model_calls=run.model_calls,
            tool_calls=run.tool_calls,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
        )
        self.flush()
        if self.store is not None:
            try:
                self.store.finish_turn(run)
            except Exception:  # noqa: BLE001
                pass

    @contextmanager
    def step(self, name: str, **data: Any) -> Iterator[None]:
        """Bracket a stage that is neither a model call nor a tool call."""

        started = time.monotonic()
        self.event(f"{name}_started", **data)
        try:
            yield
        except Exception as error:  # noqa: BLE001 - recorded, then re-raised
            self.event(
                f"{name}_failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                error_type=type(error).__name__,
                **data,
            )
            raise
        self.event(
            f"{name}_finished",
            duration_ms=int((time.monotonic() - started) * 1000),
            **data,
        )

    # --- model calls ---------------------------------------------------------

    @contextmanager
    def model(self, purpose: str) -> Iterator["ModelCall"]:
        """Bracket one model call, whatever path it takes.

        Every request a turn pays for is counted here, including the one it
        spends wrapping up after its budget is gone. A turn that counted only
        its visible answer would make the loop look cheaper than it is.
        """

        self.run.model_calls += 1
        call = ModelCall(self, purpose, self.run.model_calls)
        self.event("model_started", purpose=purpose, call_index=call.index)
        try:
            yield call
        except Exception as error:  # noqa: BLE001 - recorded, then re-raised
            self.event(
                "model_failed",
                duration_ms=call.duration_ms(),
                purpose=purpose,
                call_index=call.index,
                error_type=type(error).__name__,
            )
            raise
        self.event(
            "model_finished",
            duration_ms=call.duration_ms(),
            purpose=purpose,
            call_index=call.index,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            cached_tokens=call.cached_tokens,
            reasoning_tokens=call.reasoning_tokens,
            finish_reason=call.finish_reason,
        )

    # --- tool calls ----------------------------------------------------------

    @contextmanager
    def tool(self, name: str, **data: Any) -> Iterator["ToolCall"]:
        """Bracket one tool execution.

        `data` is for what makes twenty calls distinguishable from each other —
        in practice the `path` argument. Never an argument's value: the content
        a tool writes is the conversation's, and telemetry does not keep that.
        """

        self.run.tool_calls += 1
        call = ToolCall(self, name, self.run.tool_calls)
        self.event("tool_started", tool=name, call_index=call.index, **data)
        try:
            yield call
        except Exception as error:  # noqa: BLE001 - recorded, then re-raised
            self.event(
                "tool_failed",
                duration_ms=call.duration_ms(),
                tool=name,
                call_index=call.index,
                error_type=type(error).__name__,
                status="failed",
                **data,
            )
            raise
        self.event(
            "tool_finished" if call.status == "success" else "tool_failed",
            duration_ms=call.duration_ms(),
            tool=name,
            call_index=call.index,
            status=call.status,
            code=call.code,
            message=call.message,
            **data,
        )


class ModelCall:
    """One model request in progress."""

    def __init__(self, trace: TurnTrace, purpose: str, index: int) -> None:
        self.trace = trace
        self.purpose = purpose
        self.index = index
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cached_tokens: int | None = None
        self.reasoning_tokens: int | None = None
        self.finish_reason: str | None = None
        self._started = time.monotonic()

    def duration_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def first_token(self) -> None:
        """The first piece of this call's text. Only a streamed call has one.

        A non-streaming call has no such boundary, and inventing one from the
        moment the whole response arrived would report a TTFT equal to the total
        duration and call it a measurement.
        """

        if self.trace.run.first_model_token_ms is None:
            self.trace.run.first_model_token_ms = self.trace.elapsed_ms()
        self.trace.event(
            "model_first_token",
            duration_ms=self.duration_ms(),
            purpose=self.purpose,
            call_index=self.index,
        )

    def done(self, completion: "Completion") -> None:
        """What the call cost, taken from the model's own counts."""

        usage = completion.usage
        self.input_tokens = usage.input_tokens
        self.output_tokens = usage.output_tokens
        self.cached_tokens = usage.cached_tokens
        self.reasoning_tokens = usage.reasoning_tokens
        self.finish_reason = completion.finish_reason
        self.trace.run.input_tokens += usage.input_tokens or 0
        self.trace.run.output_tokens += usage.output_tokens or 0


class ToolCall:
    """One tool execution in progress."""

    def __init__(self, trace: TurnTrace, name: str, index: int) -> None:
        self.trace = trace
        self.name = name
        self.index = index
        self.status = "success"
        # Why it failed, when it did. A `tool_failed` event that named no reason
        # (ISS-0007) is what these two close: the code is what a reader groups
        # by and the message is what the model was told.
        self.code: str | None = None
        self.message: str | None = None
        self._started = time.monotonic()

    def duration_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def failed(
        self, status: str = "failed", *, code: str | None = None, message: str | None = None
    ) -> None:
        self.status = status
        self.code = code
        self.message = message


class NullTrace(TurnTrace):
    """A trace that records nothing, for every path without telemetry.

    A null object rather than an optional one: a caller that has to ask whether
    it is being observed before each event ends up observing inconsistently.
    """

    def __init__(self) -> None:
        super().__init__(TurnRun(run_id=""), None)

    def event(self, type: str, duration_ms: int | None = None, **data: Any) -> None:
        return None

    def flush(self) -> None:
        return None

    def start(self) -> None:
        return None

    def finish(
        self,
        outcome: Outcome,
        *,
        status: Status | None = None,
        error_type: str | None = None,
    ) -> None:
        return None


NO_TRACE = NullTrace()


def resolve(current: "Callable[[], TurnTrace] | None") -> TurnTrace:
    """The trace a long-lived object should record against right now.

    Objects built once and used for many turns — the task worker, the
    validator, the wrapped backend — hold no run identity of their own. They ask
    for the current one when something happens, which keeps the single source of
    that answer in the runtime that started the turn. A caller that cannot
    answer gets a trace that records nothing rather than an exception.
    """

    if current is None:
        return NO_TRACE
    try:
        return current() or NO_TRACE
    except Exception:  # noqa: BLE001 - an observation cannot break the work
        return NO_TRACE


class Telemetry:
    """Where a turn's trace is opened, and how deep code finds it again.

    Only a `run_id` travels into the graph, in LangGraph's own configurable
    dictionary, and the object it names is looked up here. That is deliberate:
    the identifier is a string a checkpoint can hold and a log line can carry,
    while the recorder is a live object that must never be serialized into
    either. Code that asks for an unknown run gets a trace that records nothing
    rather than an error, so every path that has no telemetry — Chainlit, the
    tests, a turn started before this existed — keeps working unchanged.
    """

    def __init__(self, store: TelemetryStore | None = None) -> None:
        self.store = store
        self._active: dict[str, TurnTrace] = {}

    @property
    def enabled(self) -> bool:
        return self.store is not None

    def start(self, run: TurnRun, *, offset_ms: int = 0) -> TurnTrace:
        if self.store is None or not run.run_id:
            return NO_TRACE
        trace = TurnTrace(run, self.store, offset_ms=offset_ms)
        self._active[run.run_id] = trace
        trace.start()
        return trace

    def trace(self, run_id: str | None) -> TurnTrace:
        if not run_id:
            return NO_TRACE
        return self._active.get(run_id, NO_TRACE)

    def release(self, run_id: str | None) -> None:
        """Forget a finished turn, so a long-lived worker does not accumulate."""

        if run_id:
            self._active.pop(run_id, None)

    def close(self) -> None:
        for trace in list(self._active.values()):
            trace.flush()
        self._active.clear()
        if self.store is not None:
            try:
                self.store.close()
            except Exception:  # noqa: BLE001
                pass
            self.store = None
