"""The agent's own list of what the work in front of it still needs.

Planning as state the model owns, not as a mode the harness switches into.
Nothing here decides that a task is complex, and nothing classifies a request
before the model sees it: the model writes a list when a list helps and never
mentions one otherwise.

**The tool stores nothing.** It validates a list and acknowledges it, and the
list itself lives where it already was — in the arguments of the call the model
made, inside the turn's own messages. Those messages are checkpointed, so the
list survives an interrupt, a resume and a restarted worker; and the `extend`
reducer in `app/agent/graph.py` clears them when the next user message arrives,
so it does not survive into the next thing the person asks. That is exactly the
lifetime this list should have, and it is already implemented by the loop, so a
second copy in a table would be a second thing to keep true.

Whole-list replacement, for the same reason the reference harness chose it:
with no partial edits an item needs no identity, the model resends what it
believes the plan is, and the newest call is the plan. `current` folds that back
out of the messages.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.models import Message
from app.tools.base import Tool, ToolError, tool_failed

TOOL_NAME = "todo_write"

PENDING = "pending"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
STATUSES = (PENDING, IN_PROGRESS, COMPLETED)

# The one way this tool fails: the list could not be recorded honestly.
INVALID = "todo.invalid"

# Bounds on what one call may cost. Every update resends the whole list, and the
# accepted call stays in the turn's messages for the rest of the turn, so an
# unbounded list is paid for again on every model step that follows it.
MAX_ITEMS = 20
MAX_CONTENT_CHARS = 200

DESCRIPTION = (
    "Your own list of steps. Open one when the work has phases or dependencies "
    "where the order matters, when it is long and takes many actions, when the "
    "person asked for a plan, or when steps came up while you worked that you "
    "will do before answering. Do not open one for a simple or single-step "
    "request: a list that was not needed is resent in full on every update and "
    "carried on every step afterwards. When you do open one: "
    "send the ENTIRE list every call, because it replaces the previous one and "
    "there are no partial updates or per-item edits. One item per meaningful "
    "milestone or outcome — do not mirror individual tool calls, files or small "
    "implementation actions, and expect one item to stay in_progress across "
    "several of them. At most one in_progress while work remains, and an item "
    "marked completed the moment it is done rather than in one batch at the end."
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "description": "The COMPLETE list, replacing any previous one.",
            "items": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "What the step is, as one short imperative line.",
                    },
                    "status": {
                        "type": "string",
                        "enum": list(STATUSES),
                        "description": (
                            "pending (not started) | in_progress (being worked on "
                            "now) | completed (finished)."
                        ),
                    },
                },
                "required": ["content", "status"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["todos"],
    "additionalProperties": False,
}


def normalise(todos: Any) -> list[dict[str, str]]:
    """Validate one submitted list and return it in canonical form.

    Raises `ToolError` rather than repairing anything. What is recorded has to
    equal what the model believes it wrote: an item quietly dropped or a status
    quietly corrected would leave the model planning against a list that is not
    the one it will read back.

    `Toolbox.validation_error` checks the top level of a schema and does not
    descend into array items, so the item shape is checked here.
    """

    if not isinstance(todos, list):
        raise ToolError("todos must be a list", code=INVALID)
    if len(todos) > MAX_ITEMS:
        raise ToolError(f"a list may hold at most {MAX_ITEMS} items", code=INVALID)
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in todos:
        if not isinstance(entry, dict):
            raise ToolError(
                "each todo must be an object with content and status", code=INVALID
            )
        unexpected = sorted(set(entry) - {"content", "status"})
        if unexpected:
            raise ToolError(f"a todo has no field(s): {', '.join(unexpected)}", code=INVALID)
        content = entry.get("content")
        status = entry.get("status")
        if not isinstance(content, str) or not content.strip():
            raise ToolError("each todo needs a non-empty content line", code=INVALID)
        content = content.strip()
        if len(content) > MAX_CONTENT_CHARS:
            raise ToolError(
                f"a todo line must be shorter than {MAX_CONTENT_CHARS} characters",
                code=INVALID,
            )
        if content in seen:
            raise ToolError(f"the list repeats {content!r}", code=INVALID)
        seen.add(content)
        if status not in STATUSES:
            raise ToolError(
                f"a todo status must be one of {', '.join(STATUSES)}, not {status!r}",
                code=INVALID,
            )
        items.append({"content": content, "status": status})
    active = sum(1 for item in items if item["status"] == IN_PROGRESS)
    if active > 1:
        # One agent, one thing at a time. This is the tool's policy rather than
        # a property of the recorded shape: a list written when several steps
        # really do run at once would still be a coherent list, and nothing that
        # reads one back depends on the count.
        raise ToolError(f"at most one todo may be in_progress, not {active}", code=INVALID)
    return items


def summarise(items: Sequence[dict[str, str]]) -> str:
    counts = {status: 0 for status in STATUSES}
    for item in items:
        counts[item["status"]] += 1
    return (
        f"Updated todo list: {counts[PENDING]} pending, "
        f"{counts[IN_PROGRESS]} in progress, {counts[COMPLETED]} completed."
    )


def _write(todos: Any) -> str:
    return summarise(normalise(todos))


def current(messages: Sequence[Message]) -> tuple[dict[str, str], ...]:
    """The list as it stands, folded out of the turn's own messages.

    Last write wins, and a call the tool refused is not a write: its arguments
    are in the transcript beside the error the model read, and treating them as
    the plan would make a rejected list the current one.
    """

    failed = {
        message.tool_call_id
        for message in messages
        if message.role == "tool" and tool_failed(message)
    }
    for message in reversed(list(messages)):
        for call in reversed(message.tool_calls):
            if call.name != TOOL_NAME or call.id in failed:
                continue
            try:
                return tuple(normalise(call.arguments.get("todos")))
            except ToolError:
                continue
    return ()


def unfinished(items: Sequence[dict[str, str]]) -> tuple[dict[str, str], ...]:
    """The items the agent's own list still says are not done."""

    return tuple(item for item in items if item["status"] != COMPLETED)


def todo_tools() -> list[Tool]:
    """The one planning tool, which needs neither a workspace nor a store."""

    return [
        Tool(
            name=TOOL_NAME,
            description=DESCRIPTION,
            returns="the list as it now stands.",
            leaves="the list in this turn's messages; what is still open is read when you try to finish.",
            parameters=PARAMETERS,
            run=_write,
        )
    ]
