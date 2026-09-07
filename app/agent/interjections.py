"""A message the person sends while a turn is running.

It is not a new request and not a stop. In a coding agent's chat the person
comments on the work as it happens, and the comment reaches the model at the
next step boundary of the running turn, as the person's words. Claude Code,
Codex and Pi all deliver it the same way: after the current batch of tool
calls, before the next model request, as an ordinary user message, without
cutting the batch (`reports/2026-09-07_mid_turn_message.md` §7).

So it travels the lane a stop travels (`app/agent/stop.py`): out of band at
the front door, read by the running turn at the tools boundary. The one
difference is that a turn *takes* it: a message taken here is answered by
this turn and by nothing else, which is what the atomicity of `take` means.
The sequence keeps a message with its turn, as it keeps a stop: only what
arrived after the turn's own number is the turn's to take.

Two implementations for the two profiles, like the stops. Locally the turn
and the front door share a process and the lane is memory; deployed, the
durable inbox is the lane and `take` is one statement on it (`ui/telegram`).
"""

from __future__ import annotations

from typing import Protocol

from app.models import Message


class Interjections(Protocol):
    """Where a running turn takes the messages sent while it worked."""

    async def take(self, key: str, since: int) -> list[Message]:
        """Every message of this person offered after `since`, oldest first.

        Taken atomically: a message returned here is the caller's to answer,
        and no other turn will see it.
        """


class NoInterjections:
    """For every caller that has no front door — tests, one-shot runs."""

    async def take(self, key: str, since: int) -> list[Message]:
        return []


NO_INTERJECTIONS = NoInterjections()


class MemoryInterjections:
    """The local profile: the front door and the turn are one process.

    The front door `offer`s a message before it waits for the conversation,
    the turn `take`s at a boundary, and the front door, once it holds the
    conversation, asks with `settle` whether its message was taken: if so it
    is answered already and the front door says nothing; if not, the offer is
    withdrawn and the message is answered as its own turn. `settle` is one
    step with no wait in it, so a turn cannot take the message between the
    question and the withdrawal.
    """

    def __init__(self) -> None:
        self._offered: dict[str, list[tuple[int, Message]]] = {}
        self._taken: dict[str, set[int]] = {}

    async def offer(self, key: str, sequence: int, message: Message) -> None:
        self._offered.setdefault(key, []).append((sequence, message))

    async def take(self, key: str, since: int) -> list[Message]:
        offered = self._offered.get(key, [])
        taken = [(sequence, message) for sequence, message in offered if sequence > since]
        if not taken:
            return []
        self._offered[key] = [item for item in offered if item[0] <= since]
        self._taken.setdefault(key, set()).update(sequence for sequence, _ in taken)
        return [message for _, message in sorted(taken, key=lambda item: item[0])]

    async def settle(self, key: str, sequence: int) -> bool:
        """Whether the message offered with `sequence` was taken by a turn.

        Either way the message leaves the lane: taken ones are forgotten, the
        rest withdrawn, so nothing offered can be taken by a later turn.
        """

        taken = self._taken.get(key, set())
        if sequence in taken:
            taken.discard(sequence)
            return True
        self._offered[key] = [item for item in self._offered.get(key, []) if item[0] != sequence]
        return False
