"""Whether a model result that would end the turn is allowed to end it.

The loop's ordinary exit is the model answering without asking for a tool. That
exit is right nearly always, and the expensive mistake would be to make it
conditional on a second opinion: a mandatory validator is another model call on
every turn, and a text heuristic moves a semantic product judgement out of the
agent and into a regular expression.

So this is a seam and not a policy. **The default is to stop**, with no extra
model call and no validation pass, and the turn continues only when an injected
extension answers with explicit structured `Steering`. Nothing here inspects
what the model wrote, and nothing here knows about a file format, a tool or a
kind of task; the model remains the one deciding whether an outcome needs
checking and which real tool would check it.

The seam exists because later structured state — `todo` first — needs somewhere
to object to a turn ending while it still holds unfinished items, and that
objection must not require redesigning the loop when it arrives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import ContentPart, Message


@dataclass(frozen=True)
class Candidate:
    """The model result that would end the turn, and what the turn has spent.

    `messages` is the turn so far with the candidate last, so an extension can
    read what actually happened — which tools ran and what they returned —
    rather than being handed a sentence out of context. It is the turn's own
    messages, not the stored conversation: an extension is deciding about this
    turn.

    `steerings` is how many times this turn has already been refused an ending.
    It is here because an extension cannot count its own objections from the
    messages: a steered draft and its instruction are shown to the model and
    never appended to the turn, precisely so neither reaches the conversation.
    Without this number the only bound on an extension that keeps objecting is
    the turn's budget, which is a ceiling on cost rather than a decision.
    """

    message: Message
    messages: tuple[Message, ...] = ()
    steps: int = 0
    tool_calls: int = 0
    spent_seconds: float = 0.0
    steerings: int = 0

    @property
    def text(self) -> str:
        return " ".join(part.text or "" for part in self.message.content).strip()


@dataclass(frozen=True)
class Steering:
    """An explicit instruction to take another step in the same turn.

    Structured rather than a bare `True`, because "do not stop" alone gives the
    model nothing to act on and would produce the same candidate again. The
    instruction is what the model is told; `source` names who asked, and exists
    for the trace rather than for the model.
    """

    instruction: str
    source: str = "extension"

    def __post_init__(self) -> None:
        if not self.instruction.strip():
            raise ValueError("steering requires an instruction the model can act on")


@dataclass(frozen=True)
class Steered:
    """A candidate the turn did not accept, and what it was told instead.

    Held in the turn's state for exactly one further model step. It is never
    appended to the turn's messages, which is what keeps a steered candidate
    out of the store and out of every interface: it is working material of one
    turn, in the same way a tool result the model reads is not an answer.
    """

    # `None` for a question the harness asks a long turn on its own (the
    # health check): there is no draft, only the instruction.
    candidate: Message | None
    steering: Steering


class TurnStopping(Protocol):
    """Asked once, only when a model result would otherwise end the turn."""

    async def stopping(self, candidate: Candidate) -> Steering | None:
        """`None` to stop, which is the answer unless there is a reason."""


class StopsWhenTheModelStops:
    """The default: the model deciding it is finished finishes the turn.

    A null object rather than an optional, for the same reason `NO_TRACE` and
    `NO_STOPS` are: a loop that has to ask whether it has an extension before
    asking the extension ends up with the question in two places.
    """

    async def stopping(self, candidate: Candidate) -> Steering | None:
        return None


STOP_ON_ANSWER = StopsWhenTheModelStops()

# The steering reaches the model as an ordinary conversation turn, because that
# is the only channel a chat model has mid-turn. The frame says where it came
# from so the model does not answer it as if the person had typed it. It is a
# label, not an instruction: what to do is entirely the extension's sentence.
STEERING_FRAME = "Turn control (not from the user): {instruction}"


def steering_message(steering: Steering) -> Message:
    """What the next model step reads in place of having stopped."""

    return Message(
        role="user",
        content=[
            ContentPart(
                kind="text",
                text=STEERING_FRAME.format(instruction=steering.instruction),
            )
        ],
    )
