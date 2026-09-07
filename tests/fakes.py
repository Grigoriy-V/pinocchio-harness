"""A model that says what the test tells it to say.

Shared by the graph and session tests so both drive the same fake, and so a
change to `ModelBackend` breaks one place rather than three.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.models import (
    Completion,
    CompletionDone,
    ContentPart,
    Message,
    ModelBackend,
    StreamEvent,
    TextDelta,
    ToolCall,
    Usage,
)
from ui.telegram.inbox import EnqueueResult, InboxJob


class ScriptedBackend(ModelBackend):
    """Returns prepared completions in order, then `default` for anything else.

    `default` exists because summarization is also a model call: a test about
    conversation length should not have to script the summary it triggers.
    """

    def __init__(
        self,
        *completions: Completion | Exception,
        default: Completion | None = None,
        limit: int | None = None,
    ) -> None:
        self.completions = list(completions)
        self.default = default
        self.limit = limit
        self.requests: list[list[Message]] = []
        self.tools_seen: list[Any] = []
        self.formats_seen: list[Any] = []

    async def context_limit(self) -> int | None:
        return self.limit

    async def invoke(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        self.requests.append(list(messages))
        self.tools_seen.append(tools)
        self.formats_seen.append(response_format)
        if self.completions:
            result = self.completions.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        if self.default is None:
            raise AssertionError("the backend was called more times than the test scripted")
        return self.default

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """The scripted completion, delivered the way a server delivers one.

        The text arrives in pieces that concatenate back to it exactly, and the
        completion itself arrives at the end, so a test cannot pass because the
        fake was tidier than a real stream.
        """

        completion = await self.invoke(messages, tools, response_format)
        for start in range(0, len(completion.text), 8):
            yield TextDelta(completion.text[start : start + 8])
        yield CompletionDone(completion)


def says(text: str, input_tokens: int | None = None) -> Completion:
    return Completion(
        text=text, usage=Usage(input_tokens=input_tokens), finish_reason="stop"
    )


def calls(name: str, **arguments: Any) -> Completion:
    return Completion(
        text="",
        tool_calls=(ToolCall(id=f"call_{name}", name=name, arguments=arguments),),
        finish_reason="tool_calls",
    )


def user(text: str) -> Message:
    return Message(role="user", content=[ContentPart(kind="text", text=text)])


def body(message: Message) -> str:
    return " ".join(part.text or "" for part in message.content)


def prompt_text(messages: Sequence[Message]) -> str:
    return "\n".join(body(message) for message in messages)


class QueuedInbox:
    """The durable queue's rules in memory, for tests that are not about SQL.

    The worker's ordering behaviour is worth testing offline; the real queue's
    is not testable that way, and `tests/test_update_inbox_contract.py` asserts
    the same rules against PostgreSQL itself. So this holds exactly the three
    that the worker depends on — one conversation runs one update at a time, the
    oldest unfinished one goes first, and a control update is claimed on its own
    whatever the conversation is doing. It models a lease that never expires
    unless `expire` is called, because nothing offline moves a clock forward;
    heartbeats are recorded in `extended`.
    """

    def __init__(self, queued_ms: int = 0) -> None:
        self.payloads: dict[int, dict[str, Any]] = {}
        self.runs: dict[int, str] = {}
        self.keys: dict[int, str] = {}
        self.control: set[int] = set()
        self.state: dict[int, str] = {}
        self.completed: list[int] = []
        self.retried: list[tuple[int, str]] = []
        self.abandoned: list[tuple[int, str]] = []
        self.attempts: dict[int, int] = {}
        self.extended: list[int] = []
        self.queued_ms = queued_ms

    async def enqueue(
        self,
        update_id: int,
        payload: dict[str, Any],
        run_id: str = "",
        conversation_key: str = "",
        control: bool = False,
    ) -> "EnqueueResult":
        if update_id not in self.payloads:
            self.payloads[update_id] = payload
            if control:
                self.control.add(update_id)
            # The stored identity wins, exactly as the real queue's insert does:
            # a redelivered update is one turn seen twice.
            self.runs[update_id] = run_id
            self.keys[update_id] = conversation_key
            self.state[update_id] = "pending"
        spawn = self.state[update_id] == "pending"
        return EnqueueResult(update_id, spawn, self.runs[update_id])

    async def claim(self, update_id: int, lease_seconds: int = 900) -> "InboxJob | None":
        if update_id not in self.payloads:
            return None
        key = self.keys.get(update_id, "")
        if key and update_id not in self.control:
            return await self.claim_next(key, lease_seconds)
        # A control update, or one queued before conversations were part of the
        # queue: one row, alone, whatever else the conversation is doing.
        return self._lease(update_id) if self.state[update_id] == "pending" else None

    async def claim_next(
        self, conversation_key: str, lease_seconds: int = 900
    ) -> "InboxJob | None":
        if not conversation_key:
            return None
        theirs = [
            update_id
            for update_id, key in self.keys.items()
            if key == conversation_key and update_id not in self.control
        ]
        if any(self.state[update_id] == "running" for update_id in theirs):
            return None
        waiting = sorted(
            update_id for update_id in theirs if self.state[update_id] == "pending"
        )
        return self._lease(waiting[0]) if waiting else None

    def _lease(self, update_id: int) -> "InboxJob":
        self.state[update_id] = "running"
        self.attempts[update_id] = self.attempts.get(update_id, 0) + 1
        return InboxJob(
            update_id,
            self.payloads[update_id],
            "lease",
            run_id=self.runs.get(update_id, ""),
            queued_ms=self.queued_ms,
            conversation_key=self.keys.get(update_id, ""),
            control=update_id in self.control,
            attempts=self.attempts[update_id],
        )

    async def extend(self, job: "InboxJob", lease_seconds: int) -> None:
        self.extended.append(job.update_id)

    async def finished(self, update_id: int) -> bool:
        return self.state.get(update_id) == "done"

    def expire(self, update_id: int) -> None:
        """The worker holding this update died: its lease ran out."""

        self.state[update_id] = "pending"

    async def complete(self, job: "InboxJob") -> None:
        self.state[job.update_id] = "done"
        self.completed.append(job.update_id)

    async def retry(self, job: "InboxJob", error: str) -> None:
        self.state[job.update_id] = "pending"
        self.retried.append((job.update_id, error))

    async def abandon(self, job: "InboxJob", error: str) -> None:
        self.state[job.update_id] = "done"
        self.abandoned.append((job.update_id, error))
