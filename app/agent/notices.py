"""What the harness has to tell the model about something that ended on its
own: a background command that exited (roadmap 32; Hermes's `notify`,
OpenClaw's `notifyOnExit`).

A notice is not the person's words and not a tool result: it is a line from
the harness, read by the model at the next boundary of a running turn after
that batch's results, and by the next turn beside the request when no turn
is running; it rides as turn control (the steering seam) and is not stored
as anyone's words, the way the health question is not. It travels like a stop and an interjection: posted out of
band, taken by the loop. Locally the lane is memory; the deployed profile
keeps no background command and posts nothing.
"""

from __future__ import annotations

from typing import Protocol

from app.agent.stopping import Steered, Steering

NOTICE_SOURCE = "background"


def notice_steering(told: list[str], steered: Steered | None) -> Steered:
    """The notices as the next step's turn control (the steering seam, the
    same frame the health question uses); joined after an instruction already
    riding there, since a step carries one."""

    text = "\n".join(told)
    if steered is not None and steered.candidate is None:
        joined = f"{steered.steering.instruction}\n{text}"
        return Steered(candidate=None, steering=Steering(instruction=joined, source=steered.steering.source))
    return Steered(candidate=None, steering=Steering(instruction=text, source=NOTICE_SOURCE))


class Notices(Protocol):
    def post(self, key: str, text: str) -> None: ...

    def take(self, key: str) -> list[str]: ...


class NoNotices:
    def post(self, key: str, text: str) -> None:
        return None

    def take(self, key: str) -> list[str]:
        return []


NO_NOTICES = NoNotices()


class MemoryNotices:
    """The local profile: the runner and the turn share a process."""

    def __init__(self) -> None:
        self._posted: dict[str, list[str]] = {}

    def post(self, key: str, text: str) -> None:
        self._posted.setdefault(key, []).append(text)

    def take(self, key: str) -> list[str]:
        return self._posted.pop(key, [])
