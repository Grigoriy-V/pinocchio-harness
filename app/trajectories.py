"""The turns a model made, kept as the model saw them: training data, not telemetry.

Roadmap 24 (2026-09-11): a fine-tune needs, for every model call, the request
exactly as it was sent — prelude, history, the turn so far, the tool schemas —
and what the model answered. The conversation store keeps the thread, but
not the request's surface (what was stubbed, what was folded), and the
scenario runner resets its threads every run; the telemetry keeps tokens and
names, not text. So each call is written here, once, as one JSON line.

One file per run (`<dir>/<run_id>.jsonl`), because several workers write at
once and a shared file on a Volume would lose lines. Media parts are kept as
their kind and size, never their bytes. Off when no directory is configured
(`AGENT_TRAJECTORIES`); nothing else in the application changes either way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:  # Annotations only: the webhook imports the trace on its
    # cold path and the model layer is kept off it.
    from app.models.base import Completion, ContentPart, Message


def _part(part: ContentPart) -> dict[str, Any]:
    if part.kind == "text":
        return {"kind": "text", "text": part.text or ""}
    return {
        "kind": part.kind,
        "media_type": part.media_type,
        "name": part.name,
        "bytes": len(part.data or b""),
        "outbound": part.outbound,
    }


def _message(message: Message) -> dict[str, Any]:
    record: dict[str, Any] = {
        "role": message.role,
        "content": [_part(part) for part in message.content],
    }
    if message.tool_calls:
        record["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ]
    if message.tool_call_id:
        record["tool_call_id"] = message.tool_call_id
    if message.failure is not None:
        record["failure"] = {"code": message.failure.code, "message": message.failure.message}
    return record


def call_record(
    *,
    run_id: str,
    thread_id: str,
    call_index: int,
    model: str | None,
    prompt: Sequence[Message],
    tools: Sequence[dict[str, Any]] | None,
    completion: Completion,
) -> dict[str, Any]:
    """One model call as a training sample: what was sent, what came back."""

    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "call_index": call_index,
        "model": model,
        "messages": [_message(message) for message in prompt],
        "tools": list(tools or ()),
        "completion": {
            "text": completion.text,
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in completion.tool_calls
            ],
            "finish_reason": completion.finish_reason,
            "usage": {
                "input_tokens": completion.usage.input_tokens,
                "output_tokens": completion.usage.output_tokens,
                "cached_tokens": completion.usage.cached_tokens,
                "reasoning_tokens": completion.usage.reasoning_tokens,
            },
        },
    }


@dataclass(frozen=True)
class Trajectories:
    """Where the records go. `None` is off, and every write is then nothing."""

    directory: Path | None = None

    @classmethod
    def at(cls, path: str) -> "Trajectories":
        return cls(Path(path) if path else None)

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def write(self, run_id: str, record: dict[str, Any]) -> None:
        if self.directory is None or not run_id:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / f"{run_id}.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # A record that cannot be written is a record lost, never a failed
            # turn: this is a side channel, and the person's answer comes first.
            return


NO_TRAJECTORIES = Trajectories()
