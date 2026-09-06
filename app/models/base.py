"""The only interface the application uses to reach a model.

Nothing outside this package may import a provider SDK, tokenizer, or processor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


# Characters per token before any request has been observed. Four is the usual
# figure for English prose; this assistant is talked to in Russian and carries
# JSON tool arguments and file contents, both of which tokenize worse than
# prose. Three is deliberately the pessimistic end, because the two errors are
# not symmetric: overestimating folds a conversation earlier than it had to,
# while underestimating sends a request the server refuses.
CHARS_PER_TOKEN = 3.0

# What one non-text part costs. An image becomes a fixed-length sequence, so a
# constant is the right shape for it; audio scales with duration, which the part
# alone does not reveal, so this is an upper estimate of a short clip rather
# than a measurement. Neither is corrected by calibration, which only watches
# text — see `OpenAICompatibleBackend.estimate_tokens`.
MEDIA_TOKENS = {"image": 320, "audio": 1500, "file": 0}


class BackendError(RuntimeError):
    """The model endpoint was reached but its answer cannot be used."""


class ContextOverflowError(BackendError):
    """The endpoint refused a request because it exceeded its context window."""


@dataclass(frozen=True)
class ContentPart:
    """One piece of a message. Parts keep the order the user supplied.

    `outbound` is an explicit application action, not a property inferred by an
    interface. Observation tools return ordinary media for the model to inspect;
    a presentation tool marks only the item the agent chose to send.
    """

    kind: Literal["text", "image", "audio", "file"]
    text: str | None = None
    data: bytes | None = None
    media_type: str | None = None
    name: str | None = None
    outbound: bool = False

    def __post_init__(self) -> None:
        if self.kind == "text":
            if not self.text:
                raise ValueError("a text part requires text")
        elif not self.data or not self.media_type:
            raise ValueError(f"a {self.kind} part requires data and media_type")
        if self.kind == "file" and not self.name:
            raise ValueError("a file part requires a name")


@dataclass(frozen=True)
class ToolCall:
    """What the model asked for.

    `raw_arguments` is the argument text as it arrived, kept only when it could
    not be read as a JSON object. The call is still delivered — with empty
    arguments and the text beside them — because a model that emitted one bad
    call must get one bad result and keep its turn, not lose the request. The
    executor is what refuses it, with the tool's signature, and the text is what
    makes two different unreadable attempts two different calls to the loop's
    repeat guard.
    """

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str | None = None
    # The model's answer ended at its output limit before this call's
    # arguments did (`finish_reason == "length"`). Until 2026-09-04 such a
    # call was refused as "bad JSON", which is not what happened, and the
    # model's natural retry was the same file cut at the same place
    # (ISS-0031). The executor names the real cause instead.
    cut: bool = False


@dataclass(frozen=True)
class ToolFailure:
    """Why a tool call did not do what was asked.

    `code` is stable and lower_snake, family-prefixed for a family's own
    failures (`fs.not_found`), and is what the runtime, telemetry and the
    context engine branch on. `message` is the one sentence the model reads.
    `detail` is the sanitized diagnostic, when it adds something.

    Its absence is the only definition of success. A shell command that exits
    non-zero or a page that renders with console errors is a successful call
    whose content says so: this is for the tool not doing what was asked.
    """

    code: str
    message: str
    detail: str | None = None


@dataclass(frozen=True)
class Message:
    """One turn.

    An assistant turn may carry tool calls instead of content: asking for a tool
    and saying nothing is a complete answer. A tool turn carries the result and
    the `tool_call_id` it answers.
    """

    role: Role
    content: Sequence[ContentPart] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    # Tool messages only. The typed failure rides on the message so the loop's
    # repeat guard, the plan reader and both interfaces ask a field and never a
    # string; it is checkpointed with the turn and joins stored history with the
    # schema-3 migration, until which stored rows carry the text projection.
    failure: ToolFailure | None = None

    def __post_init__(self) -> None:
        # Callers pass lists; a checkpoint gives lists back. Normalizing here is
        # what makes a message read out of a checkpoint equal to the one put in.
        object.__setattr__(self, "content", tuple(self.content))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        if not self.content and not self.tool_calls:
            raise ValueError("a message requires content or tool calls")
        if self.failure is not None and self.role != "tool":
            raise ValueError("only a tool message carries a failure")


def measure_request(messages: Sequence[Message]) -> tuple[int, int]:
    """Characters of text, and tokens of everything that is not text.

    The two are kept apart because only the first can be calibrated: a
    completion reports how many tokens a request became, which says what text
    is worth, and says nothing usable about an image that was in it too.

    Tool calls count. A model asking to write a file carries the file in its
    arguments, and a turn that measured only the prose would miss the largest
    thing in the request.
    """

    chars = 0
    media = 0
    for message in messages:
        for part in message.content:
            if part.kind == "text":
                chars += len(part.text or "")
            else:
                media += MEDIA_TOKENS.get(part.kind, 0)
        for call in message.tool_calls:
            chars += len(call.name) + len(str(call.arguments))
    return chars, media


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    # How many of the input tokens the server served from its prefix cache.
    # `None` when the server does not say; a server that says is the one
    # measurement of context assembly that costs nothing.
    cached_tokens: int | None = None
    # How many of the output tokens were reasoning the person never sees.
    # `None` when the server does not say. Billed as output, and the whole of
    # a slow call's time on a model that thinks first (2026-09-06).
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class Completion:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None


@dataclass(frozen=True)
class TextDelta:
    """A piece of assistant text, as it arrives."""

    text: str


@dataclass(frozen=True)
class CompletionDone:
    """The finished result, identical to what `invoke` would have returned.

    A stream that only yielded text would lose tool calls, usage and the finish
    reason, which is the difference between showing an answer being written and
    running the agent on the stream. Every stream ends with exactly one of
    these, so a caller never has to reassemble anything itself.
    """

    completion: Completion


StreamEvent = TextDelta | CompletionDone


class ModelBackend(ABC):
    """A provider-agnostic model.

    Implementations own request shaping, provider message formats, tool-schema
    translation, structured output, error translation, and retries. Call sites
    must not change when the implementation changes.
    """

    @abstractmethod
    async def invoke(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        """Return one complete result."""

    @abstractmethod
    def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield `TextDelta` as text arrives, then one final `CompletionDone`.

        The events carry everything `invoke` carries, so a caller can both show
        the answer being written and go on running the agent — tool calls,
        usage and finish reason included — from the same request.
        """

    async def warm(self) -> bool:
        """Start the model serving, without waiting for it to be ready.

        Optional because it only means anything for a backend that can be
        asleep. A deployment that scales to zero costs several seconds on the
        first request, and those seconds are worth spending in parallel with
        whatever else has to happen before the model is called.

        Implementations must not raise: a failed warm-up is a slower turn, never
        a failed one.
        """

        return False

    async def context_limit(self) -> int | None:
        """The largest request this model accepts, in tokens, or `None` if unknown.

        Asked of the backend rather than configured, because the number belongs
        to the model that is actually running: a tokenizer, or a limit, copied
        into project configuration is one that can quietly stop being true.
        """

        return None

    def estimate_tokens(self, messages: Sequence[Message]) -> int:
        """About how large this request would be, without sending it.

        Here rather than in `app/context/` because it is model-shaped: the same
        text is a different number of tokens for a different model, and the
        decision that keeps every tokenizer inside this package applies to
        estimates of one as much as to one itself.

        Deliberately an estimate. The exact answer needs the model's own
        tokenizer, which would be a dependency and a cold start on every worker
        for a number whose only job is to decide whether to fold a conversation
        *before* asking rather than after. The fraction of the context window
        the application actually spends is the margin that makes an approximate
        answer safe.
        """

        chars, media = measure_request(messages)
        return int(chars / CHARS_PER_TOKEN) + media
