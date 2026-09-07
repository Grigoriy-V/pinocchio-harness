"""What the person asked for, written down once by the model before it starts.

Measured on 2026-09-04: with a plan on, requests with several parts were
finished — a PDF in Russian, a chart made and sent, screenshot and files —
and without one the model stopped when it had *something*. The benefit was
not the plan's bookkeeping: `FinishesItsOwnList` was off, so nothing refused
an ending, and the six update calls of a twelve-call turn changed nothing.
What worked was the list itself, in the model's context at every step, so
the parts of the request stayed in front of it until each had been done.

This is that list with the bookkeeping removed (the human's decision,
2026-09-05: the plan stays as it is behind `/plan`; this is the goal). One
call, one line per thing asked, never updated, never marked. It lives where
the plan lives — in the arguments of the call, inside the turn's own
messages, checkpointed with the turn and cleared by the next user message —
so it costs one short model call in a turn that asks for several things and
nothing at all in a turn that asks for one. Nothing reads it back: the
ending is the model's, as it always was, and the harness makes no second
call about it.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import Tool, ToolError

TOOL_NAME = "set_goal"

INVALID = "goal.invalid"

# The bound is the price: the accepted call is carried on every step after
# it. A request rarely asks for more than a handful of things.
MAX_PARTS = 10
MAX_PART_CHARS = 200

DESCRIPTION = (
    "Write down what the person asked for, once, before you start, when the "
    "request asks for more than one thing: one short line per thing, in their "
    "words, including how they want it (a language, a format, sent to them). "
    "Then do the work; do not update this or mark anything, and do not use it "
    "for a request that asks for one thing."
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "parts": {
            "type": "array",
            "description": "One line per thing the person asked for.",
            "items": {"type": "string"},
        }
    },
    "required": ["parts"],
    "additionalProperties": False,
}


def normalise(parts: Any) -> list[str]:
    """The list as the model will read it back, or a refusal. Nothing repaired."""

    if not isinstance(parts, list):
        raise ToolError("parts must be a list of lines", code=INVALID)
    if not parts:
        raise ToolError("a goal needs at least one line", code=INVALID)
    if len(parts) > MAX_PARTS:
        raise ToolError(f"a goal may hold at most {MAX_PARTS} lines", code=INVALID)
    lines: list[str] = []
    for part in parts:
        if not isinstance(part, str) or not part.strip():
            raise ToolError("each part must be a non-empty line", code=INVALID)
        line = part.strip()
        if len(line) > MAX_PART_CHARS:
            raise ToolError(
                f"a line must be shorter than {MAX_PART_CHARS} characters", code=INVALID
            )
        if line in lines:
            raise ToolError(f"the goal repeats {line!r}", code=INVALID)
        lines.append(line)
    return lines


def _write(parts: Any) -> str:
    lines = normalise(parts)
    return f"Goal noted, {len(lines)} thing(s) asked for. Now do them; this is not updated."


def goal_tools() -> list[Tool]:
    """The one goal tool, which needs neither a workspace nor a store."""

    return [
        Tool(
            name=TOOL_NAME,
            description=DESCRIPTION,
            returns="that the goal was noted, and how many parts it has.",
            leaves="the list in this turn's messages, where you read it back as you work.",
            parameters=PARAMETERS,
            run=_write,
        )
    ]
