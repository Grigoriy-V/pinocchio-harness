"""A model backend that records what each call cost.

The conversational graph brackets its own model call, because it has the turn's
identity in its configuration. The bounded task path does not: its planner,
implementer and validator are plain callables constructed once, and threading a
per-turn recorder through all of them would change three protocols and every
fake that implements them, for a measurement that can be taken at the one place
every one of those calls passes through.

So the task path is given a wrapped backend instead. It counts every request the
task spends — which is the point: a turn that reported only its router call
would say an autonomous task cost one model call, and the failed live PDF task
is exactly the case that has to stop being invisible.

The wrapper holds no run identity of its own. It asks for the current one when a
call happens, which keeps the single source of that answer in the runtime that
started the turn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from app.models import Completion, Message, ModelBackend, StreamEvent, TextDelta
from app.telemetry.trace import TurnTrace, resolve


class TracedBackend(ModelBackend):
    """Delegates every call, and records each one against the current turn."""

    def __init__(
        self,
        backend: ModelBackend,
        current: Callable[[], TurnTrace],
        purpose: str = "task",
    ) -> None:
        self.backend = backend
        self.current = current
        self.purpose = purpose

    def _trace(self) -> TurnTrace:
        return resolve(self.current)

    async def context_limit(self) -> int | None:
        return await self.backend.context_limit()

    async def warm(self) -> bool:
        return await self.backend.warm()

    def estimate_tokens(self, messages: Sequence[Message]) -> int:
        return self.backend.estimate_tokens(messages)

    async def invoke(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        with self._trace().model(self.purpose) as measured:
            completion = await self.backend.invoke(messages, tools, response_format)
            measured.done(completion)
            return completion

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        with self._trace().model(self.purpose) as measured:
            seen_text = False
            async for event in self.backend.stream(messages, tools, response_format):
                if isinstance(event, TextDelta) and not seen_text:
                    seen_text = True
                    measured.first_token()
                elif not isinstance(event, TextDelta):
                    measured.done(event.completion)
                yield event

    async def aclose(self) -> None:
        close = getattr(self.backend, "aclose", None)
        if close is not None:
            await close()
