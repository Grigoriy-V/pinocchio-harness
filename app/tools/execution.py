"""The single lifecycle for a model-requested tool call.

The agent loop decides *when* a batch runs, pauses or is halted. This seam owns
everything that happens between the model's call and the model's result:

```text
pre_execute   resolve the name, read and coerce the arguments, validate, policy
execute       the tool body under its timeout; every exception becomes a failure
post_execute  bound the content, sanitize the failure, record it, project it
```

Every stage that refuses produces an outcome, and the outcome is what reaches
the model. The one deliberate exception is `BaseException` — cancellation,
`KeyboardInterrupt`, interpreter shutdown — which is not caught, because a stop
must be able to stop. A later execution backend replaces ``execute`` without
teaching the loop a second lifecycle.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from app.models import ContentPart, Message, ToolCall, ToolFailure
from app.telemetry import NO_TRACE, TurnTrace

from .base import (
    BAD_ARGUMENTS,
    INTERNAL,
    OUTPUT_CUT,
    TIMEOUT,
    UNKNOWN_TOOL,
    Tool,
    Toolbox,
    ToolError,
    ToolOutcome,
    ToolReturn,
)

log = logging.getLogger(__name__)

# What the model reads when a call failed. Wording it responds to well, and no
# longer a protocol: nothing in the runtime reads it back.
ERROR_WORD = "error: "

# --- bounds at the seam -------------------------------------------------------
#
# A tool result goes straight into the next request and the model cannot decline
# what it has already been given. Each tool caps its own output; this is the
# backstop that does not depend on a tool remembering to. The text cap sits
# above every current tool's own limit (filesystem 20k, documents 12k, web 12k,
# browser 8k of visible text plus its report), so it changes nothing today and
# catches the next tool. Head and tail both survive because the end of a result
# is where a tool says what it did not show.
MAX_RESULT_CHARS = 32_000
TAIL_CHARS = 2_000
MAX_IMAGES = 4  # `view_pages` returns at most two; the browsers return one
MAX_IMAGE_BYTES = 16 * 1024 * 1024

# --- sanitizing failure text --------------------------------------------------
#
# A failure message is the one thing the model is most likely to imitate: live
# on 2026-08-31 it copied its own malformed call three times, and a markdown
# fence inside a value is what broke the served parser in the first place. So
# a failure's text never contains a fence, a role tag, the served model's string
# delimiter or a tool-call token; it is the first line of the diagnostic,
# bounded. The full exception stays in the process log for the developer.
FORBIDDEN_IN_FAILURES = (
    "```",
    "<|",
    "|>",
    "<start_of_turn>",
    "<end_of_turn>",
    "<tool_call>",
    "</tool_call>",
    "<tool_response>",
    "</tool_response>",
)
MAX_FAILURE_CHARS = 400


def sanitized(text: str | None) -> str:
    if not text:
        return ""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    first = lines[0] if lines else ""
    for token in FORBIDDEN_IN_FAILURES:
        first = first.replace(token, "")
    return " ".join(first.split())[:MAX_FAILURE_CHARS]


def sanitized_failure(failure: ToolFailure) -> ToolFailure:
    return ToolFailure(
        code=failure.code,
        message=sanitized(failure.message) or failure.code,
        detail=sanitized(failure.detail) or None,
    )


def bounded(content: Sequence[ContentPart]) -> tuple[ContentPart, ...]:
    """The content within the caps, with a marker where something was cut."""

    kept: list[ContentPart] = []
    images = 0
    image_bytes = 0
    omitted_images = 0
    for part in content:
        if part.kind == "text":
            text = part.text or ""
            if len(text) > MAX_RESULT_CHARS:
                head = text[: MAX_RESULT_CHARS - TAIL_CHARS]
                tail = text[-TAIL_CHARS:]
                cut = len(text) - len(head) - len(tail)
                text = f"{head}\n[... {cut} characters omitted ...]\n{tail}"
            kept.append(replace(part, text=text))
        elif part.kind == "image":
            size = len(part.data or b"")
            if images >= MAX_IMAGES or image_bytes + size > MAX_IMAGE_BYTES:
                omitted_images += 1
                continue
            images += 1
            image_bytes += size
            kept.append(part)
        else:
            kept.append(part)
    if omitted_images:
        kept.append(
            ContentPart(
                kind="text",
                text=f"[{omitted_images} image(s) omitted: over the per-result image cap]",
            )
        )
    return tuple(kept)


def as_parts(result: ToolReturn) -> tuple[ContentPart, ...]:
    if isinstance(result, str):
        return (ContentPart(kind="text", text=result or "(empty)"),)
    parts = tuple(result)
    return parts or (ContentPart(kind="text", text="(empty)"),)


# --- the stages ---------------------------------------------------------------


@dataclass(frozen=True)
class PreparedToolCall:
    """A resolved, validated policy decision with no tool effect performed yet.

    `call` carries the resolved name and the coerced arguments under the
    model's own call id: what will run, and what the person is shown when
    asked to approve it.
    """

    call: ToolCall
    tool: Tool | None
    refusal: ToolFailure | None
    approval_required: bool


class ToolMeasurement(Protocol):
    def failed(
        self, status: str = "failed", *, code: str | None = None, message: str | None = None
    ) -> None: ...


def failure_of(call: ToolCall, tool: Tool | None, error: Exception) -> ToolFailure:
    """The typed failure one exception is. The traceback of a surprise goes to the log."""

    if isinstance(error, ToolError):
        return ToolFailure(code=error.code, message=str(error), detail=error.detail)
    if isinstance(error, TimeoutError):
        limit = tool.timeout_seconds if tool is not None else None
        within = f" within {limit:g} seconds" if limit is not None else ""
        return ToolFailure(
            code=TIMEOUT, message=f"{call.name} did not finish{within} and was stopped"
        )
    log.exception("tool %s raised %s", call.name, type(error).__name__)
    # An OS error's own words rather than its `[Errno N]` rendering: the number
    # is platform wording, and the sentence is what a model can act on.
    detail = getattr(error, "strerror", None) or str(error) or None
    return ToolFailure(
        code=INTERNAL,
        message=f"{call.name} failed: {type(error).__name__}",
        detail=detail,
    )


def refusal_message(call: ToolCall, failure: ToolFailure, signature: str = "") -> Message:
    """The tool message for a call that produced no content.

    The same projection the executor uses, so a call the loop halted or the
    person declined reads to the model exactly like a call that failed.
    """

    return project(call, ToolOutcome(content=(), failure=failure), signature)


def project(call: ToolCall, outcome: ToolOutcome, signature: str = "") -> Message:
    """The message the model reads, with the typed failure riding on it."""

    if outcome.failure is None:
        return Message(role="tool", content=outcome.content, tool_call_id=call.id)
    failure = outcome.failure
    text = f"{ERROR_WORD}{failure.message}"
    if failure.detail:
        text += f" ({failure.detail})"
    if failure.code == BAD_ARGUMENTS and signature:
        text += f". {signature}"
    return Message(
        role="tool",
        content=[ContentPart(kind="text", text=text)],
        tool_call_id=call.id,
        failure=failure,
    )


class ToolExecutor:
    """Run every tool through ``pre_execute -> execute -> post_execute``."""

    def __init__(self, toolbox: Toolbox, trace: TurnTrace = NO_TRACE) -> None:
        self.toolbox = toolbox
        self.trace = trace

    # --- pre_execute ---------------------------------------------------------

    def pre_execute(self, call: ToolCall) -> PreparedToolCall:
        """Resolve, read, coerce and validate the call, and apply consent policy."""

        name = self.toolbox.resolve(call.name)
        if name is None:
            available = ", ".join(self.toolbox.names)
            return PreparedToolCall(
                call=call,
                tool=None,
                refusal=ToolFailure(
                    code=UNKNOWN_TOOL,
                    message=f"unknown tool {call.name!r}; available: {available}",
                ),
                approval_required=False,
            )
        tool = self.toolbox.get(name)
        resolved = replace(call, name=name)
        if call.raw_arguments is not None:
            if call.cut:
                refusal = ToolFailure(
                    code=OUTPUT_CUT,
                    message=(
                        f"your answer was cut at the output limit before the arguments "
                        f"of {name} ended, so the call was not run and nothing was "
                        "changed; send it in smaller pieces — write_file with the first "
                        "part, then edit_file to add the rest — rather than the same "
                        "call again"
                    ),
                )
            else:
                refusal = ToolFailure(
                    code=BAD_ARGUMENTS,
                    message=(
                        f"bad arguments for {name}: they could not be read as a JSON "
                        "object, so the call was not run"
                    ),
                )
            return PreparedToolCall(
                call=resolved, tool=tool, refusal=refusal, approval_required=False
            )
        resolved = self.toolbox.coerce(resolved)
        problem = self.toolbox.validation_error(resolved)
        if problem:
            return PreparedToolCall(
                call=resolved,
                tool=tool,
                refusal=ToolFailure(
                    code=BAD_ARGUMENTS, message=f"bad arguments for {name}: {problem}"
                ),
                approval_required=False,
            )
        return PreparedToolCall(
            call=resolved,
            tool=tool,
            refusal=None,
            approval_required=self.toolbox.requires_approval(name, resolved.arguments),
        )

    # --- execute -------------------------------------------------------------

    @staticmethod
    def _unbindable(prepared: PreparedToolCall) -> ToolOutcome | None:
        """A schema the callable does not agree with, caught before the call.

        Binding first is what tells a wrong argument apart from a `TypeError`
        raised inside the tool: the first is the model's, the second is a bug.
        """

        assert prepared.tool is not None
        try:
            inspect.signature(prepared.tool.run).bind(**prepared.call.arguments)
        except TypeError as error:
            return ToolOutcome(
                content=(),
                failure=ToolFailure(
                    code=BAD_ARGUMENTS,
                    message=f"bad arguments for {prepared.call.name}: {error}",
                ),
            )
        return None

    async def execute(self, prepared: PreparedToolCall) -> ToolOutcome:
        """Execute one prepared call through the current local backend."""

        if prepared.refusal is not None:
            return ToolOutcome(content=(), failure=prepared.refusal)
        unbindable = self._unbindable(prepared)
        if unbindable is not None:
            return unbindable
        tool, call = prepared.tool, prepared.call
        assert tool is not None
        try:
            if tool.timeout_seconds is None:
                result = tool.run(**call.arguments)
                if inspect.isawaitable(result):
                    result = await result
            else:
                result = await asyncio.wait_for(
                    self._started(tool, call), tool.timeout_seconds
                )
        except Exception as error:  # noqa: BLE001 - every failure is a result
            return ToolOutcome(content=(), failure=failure_of(call, tool, error))
        return ToolOutcome(content=as_parts(result))

    @staticmethod
    async def _started(tool: Tool, call: ToolCall) -> ToolReturn:
        """The tool body as something a deadline can be put on.

        A coroutine already is one. A synchronous body is moved to a worker
        thread so the deadline can pass without it; the thread keeps running
        until the body returns, which is the price of a turn that goes on.
        """

        if inspect.iscoroutinefunction(tool.run):
            return await tool.run(**call.arguments)
        result = await asyncio.to_thread(tool.run, **call.arguments)
        if inspect.isawaitable(result):
            result = await result
        return result

    def execute_sync(self, prepared: PreparedToolCall) -> ToolOutcome:
        """`execute` for a caller with no event loop. Refuses an async tool."""

        if prepared.refusal is not None:
            return ToolOutcome(content=(), failure=prepared.refusal)
        unbindable = self._unbindable(prepared)
        if unbindable is not None:
            return unbindable
        tool, call = prepared.tool, prepared.call
        assert tool is not None
        try:
            result = tool.run(**call.arguments)
        except Exception as error:  # noqa: BLE001 - every failure is a result
            return ToolOutcome(content=(), failure=failure_of(call, tool, error))
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if close is not None:
                close()
            raise RuntimeError(f"tool {call.name!r} is async; use Toolbox.run_async")
        return ToolOutcome(content=as_parts(result))

    # --- post_execute --------------------------------------------------------

    def post_execute(
        self, prepared: PreparedToolCall, outcome: ToolOutcome, measured: ToolMeasurement
    ) -> Message:
        """Bound, sanitize, record and project one outcome."""

        content = bounded(outcome.content)
        failure = None
        if outcome.failure is not None:
            failure = sanitized_failure(outcome.failure)
            measured.failed(code=failure.code, message=failure.message)
        settled = ToolOutcome(content=content, failure=failure)
        return project(prepared.call, settled, self.toolbox.signature(prepared.call.name))

    # --- the whole lifecycle -------------------------------------------------

    def _identity(self, prepared: PreparedToolCall) -> dict[str, Any]:
        """What makes twenty calls distinguishable in a trace. Never a value."""

        path = prepared.call.arguments.get("path")
        data: dict[str, Any] = {"stage": "execute"}
        if isinstance(path, str):
            data["path"] = path
        return data

    async def run(self, prepared: PreparedToolCall) -> Message:
        """Execute and settle one prepared call, including its telemetry."""

        with self.trace.tool(prepared.call.name, **self._identity(prepared)) as measured:
            outcome = await self.execute(prepared)
            return self.post_execute(prepared, outcome, measured)

    async def call(self, call: ToolCall) -> Message:
        return await self.run(self.pre_execute(call))

    def call_sync(self, call: ToolCall) -> Message:
        prepared = self.pre_execute(call)
        with self.trace.tool(prepared.call.name, **self._identity(prepared)) as measured:
            outcome = self.execute_sync(prepared)
            return self.post_execute(prepared, outcome, measured)
