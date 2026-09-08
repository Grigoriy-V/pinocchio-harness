"""How a stored message is encoded, shared by every SQL implementation.

A message is the same message whichever database holds it: the same JSON
content, the same base64 media, the same tool calls. The encoding lives here so
a second implementation cannot quietly invent a second format — the contract
suite checks behaviour, and behaviour would not notice.

Media the model was shown is stored rather than dropped, so a reloaded
conversation is the same conversation. Media the agent sent is the exception:
an outbound part carries the file's bytes for the transport, and once the
adapter has delivered them the history has no reader for them — no model is
ever shown an outbound part, and the file is in the workspace under its name.
Observed 2026-09-07 (ISS-0065): one sent video was a megabyte row, written by
every attempt at that turn's `persist` and read by every later turn. What is
stored is the delivery: the name, the type and the size, as text.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.models import ContentPart, Message, ToolCall, ToolFailure


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def delivered(part: ContentPart) -> ContentPart:
    """What the history keeps of an outbound part: the delivery, as text."""

    size = len(part.data or b"")
    return ContentPart(
        kind="text",
        text=f"Sent {part.name or part.kind} ({part.media_type}, {size} bytes).",
    )


def dump_content(parts: Sequence[ContentPart]) -> str:
    payload = [
        {
            "kind": part.kind,
            "text": part.text,
            "data": base64.b64encode(part.data).decode("ascii") if part.data else None,
            "media_type": part.media_type,
            "name": part.name,
            "outbound": part.outbound,
        }
        for part in (delivered(part) if part.outbound else part for part in parts)
    ]
    return json.dumps(payload)


def load_content(raw: str) -> list[ContentPart]:
    return [
        ContentPart(
            kind=item["kind"],
            text=item["text"],
            data=base64.b64decode(item["data"]) if item["data"] else None,
            media_type=item["media_type"],
            name=item.get("name"),
            outbound=bool(item.get("outbound", False)),
        )
        for item in json.loads(raw)
    ]


def dump_tool_calls(calls: Sequence[ToolCall]) -> str | None:
    if not calls:
        return None
    return json.dumps(
        [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls]
    )


def load_tool_calls(raw: str | None) -> tuple[ToolCall, ...]:
    if not raw:
        return ()
    return tuple(
        ToolCall(id=c["id"], name=c["name"], arguments=c["arguments"])
        for c in json.loads(raw)
    )


def dump_failure(failure: ToolFailure | None) -> str | None:
    if failure is None:
        return None
    return json.dumps(
        {"code": failure.code, "message": failure.message, "detail": failure.detail}
    )


def load_failure(raw: str | None) -> ToolFailure | None:
    if not raw:
        return None
    item = json.loads(raw)
    return ToolFailure(code=item["code"], message=item["message"], detail=item.get("detail"))


def row_to_message(row: Mapping[str, Any]) -> Message:
    # `failure` arrived with schema 3; a row read through an older SELECT has
    # no such key and is a message without one.
    keys = row.keys()
    return Message(
        role=row["role"],
        content=load_content(row["content"]),
        tool_calls=load_tool_calls(row["tool_calls"]),
        tool_call_id=row["tool_call_id"],
        failure=load_failure(row["failure"]) if "failure" in keys else None,
    )


def message_text(message: Message) -> str:
    """The words of a message, for the text column and full-text search.

    Text parts, then the failure — its message is exactly the detail a
    summary drops first — then the calls the model made, by name and
    arguments, so a filename the model wrote is found where it wrote it.
    Media contributes nothing: bytes are not words.
    """

    return stored_text(
        dump_content(message.content),
        dump_tool_calls(message.tool_calls),
        dump_failure(message.failure),
    )


def stored_text(content: str, tool_calls: str | None, failure: str | None) -> str:
    """`message_text` from the stored columns, so a migration can backfill
    rows it never loaded as messages."""

    words = [part.text or "" for part in load_content(content) if part.kind == "text"]
    outcome = load_failure(failure)
    if outcome is not None:
        words.append(f"{outcome.code}: {outcome.message}")
    for call in load_tool_calls(tool_calls):
        words.append(f"{call.name} {json.dumps(call.arguments, ensure_ascii=False)}")
    return "\n".join(word for word in words if word).strip()


def opening_text(raw: str | None) -> str:
    """The words a thread began with. A picture on its own leaves none."""

    if not raw:
        return ""
    return " ".join(
        part.text or "" for part in load_content(raw) if part.kind == "text"
    ).strip()
