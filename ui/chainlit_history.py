"""Expose the canonical conversation store through Chainlit's native history.

Chainlit needs a data layer to draw its sidebar, but conversation content must
not acquire a second owner. This adapter therefore derives threads, steps and
elements from the store and ignores Chainlit's duplicate step writes.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
import json
import uuid
from typing import Any

from chainlit.data import BaseDataLayer
from chainlit.element import Element, ElementDict
from chainlit.step import StepDict
from chainlit.types import (
    Feedback,
    PageInfo,
    PaginatedResponse,
    Pagination,
    ThreadDict,
    ThreadFilter,
)
from chainlit.user import PersistedUser, User

from app.conversations import delete_conversation
from app.memory import LOCAL_USER_ID, ConversationStore, Note, Thread
from app.models import ContentPart, Message

LOCAL_USER_IDENTIFIER = "local"
LOCAL_USER_CREATED_AT = "2026-01-01T00:00:00+00:00"


def _id(thread_id: str, position: int, suffix: str = "message") -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-agent:{thread_id}:{position}:{suffix}"))


def _text(message: Message) -> str:
    return " ".join(
        part.text or "" for part in message.content if part.kind == "text" and not part.hidden
    ).strip()


def _thread_name(thread: Thread) -> str:
    opening = thread.opening.strip()
    return opening[:80] if opening else "Conversation"


def _element(
    thread_id: str, step_id: str, position: int, part_index: int, part: ContentPart
) -> ElementDict:
    encoded = base64.b64encode(part.data or b"").decode("ascii")
    return {
        "id": _id(thread_id, position, f"part-{part_index}"),
        "threadId": thread_id,
        "forId": step_id,
        "type": part.kind,
        "name": part.name or f"{part.kind}-{part_index}",
        "display": "inline",
        "size": "medium" if part.kind == "image" else None,
        "mime": part.media_type,
        "url": f"data:{part.media_type};base64,{encoded}",
    }


def _saved_files(
    thread_id: str, step_id: str, position: int, part_index: int, listed: str, workspace: Path
) -> list[ElementDict]:
    """The files a message attached, as they lie in the workspace now."""

    shown: list[ElementDict] = []
    for index, relative in enumerate(listed.splitlines()):
        path = workspace / relative
        try:
            data = path.read_bytes()
        except OSError:
            continue
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(data).decode("ascii")
        shown.append(
            {
                "id": _id(thread_id, position, f"part-{part_index}-file-{index}"),
                "threadId": thread_id,
                "forId": step_id,
                "type": "file",
                "name": path.name,
                "display": "inline",
                "size": None,
                "mime": media_type,
                "url": f"data:{media_type};base64,{encoded}",
            }
        )
    return shown


def _step(
    thread_id: str, position: int, message: Message, created_at: str, parent_id: str | None = None
) -> StepDict:
    step_id = _id(thread_id, position)
    if message.role == "user":
        step_type = "user_message"
        name = "You"
    elif message.role == "assistant":
        step_type = "assistant_message"
        name = "Assistant"
    else:
        step_type = "tool"
        name = "Tool"
    output = _text(message)
    # Stored history has no failure column until the schema-3 migration in
    # roadmap 4.6a, so a stored tool result is read by its text projection, which
    # is all the model ever read from it. Live results ask `message.failure`.
    if message.role == "tool" and not output.startswith("error:"):
        output = "Completed."
    if message.tool_calls and not output:
        output = "\n".join(
            f"{call.name}({json.dumps(call.arguments, ensure_ascii=False)})"
            for call in message.tool_calls
        )
    return {
        "id": step_id,
        "threadId": thread_id,
        "parentId": parent_id,
        "name": name,
        "type": step_type,
        "input": "" if step_type != "tool" else output,
        "output": output,
        "createdAt": created_at,
        "start": created_at,
        "end": created_at,
    }


def _turn_step(thread_id: str, position: int, calls: int, created_at: str) -> StepDict:
    """One collapsed step for a turn's tool calls, as the live turn shows
    them (ISS-0076): the calls are its children."""

    plural = "" if calls == 1 else "s"
    return {
        "id": _id(thread_id, position, "turn"),
        "threadId": thread_id,
        "parentId": None,
        "name": f"{calls} tool call{plural}",
        "type": "run",
        "input": "",
        "output": "",
        "createdAt": created_at,
        "start": created_at,
        "end": created_at,
    }


def _call_step(
    thread_id: str, position: int, index: int, call: Any, created_at: str, parent_id: str
) -> StepDict:
    """One tool call of a reopened turn, as the live turn showed it."""

    return {
        "id": _id(thread_id, position, f"call-{index}"),
        "threadId": thread_id,
        "parentId": parent_id,
        "name": call.name,
        "type": "tool",
        "input": json.dumps(call.arguments, ensure_ascii=False),
        "output": "",
        "createdAt": created_at,
        "start": created_at,
        "end": created_at,
    }


def _note_step(index: int, note: Note) -> StepDict:
    """A line the harness said, shown where it was said."""

    return {
        "id": _id(note.thread_id, note.position, f"note-{index}"),
        "threadId": note.thread_id,
        "parentId": None,
        "name": "You" if note.role == "user" else "Assistant",
        "type": "user_message" if note.role == "user" else "assistant_message",
        "input": "",
        "output": note.text,
        "createdAt": note.created_at,
        "start": note.created_at,
        "end": note.created_at,
    }


class MemoryStoreDataLayer(BaseDataLayer):
    """Read native Chainlit history from the project's existing SQLite store."""

    def __init__(
        self,
        store: ConversationStore,
        checkpoints: str = "data/checkpoints.sqlite3",
        workspace: Path | None = None,
    ) -> None:
        self.store = store
        self.checkpoints = checkpoints
        # Where a sent file was saved (`inbox/`), so a reopened conversation
        # shows the file the person attached and not the harness's note.
        self.workspace = workspace

    async def get_user(self, identifier: str) -> PersistedUser | None:
        if identifier != LOCAL_USER_IDENTIFIER:
            return None
        return PersistedUser(
            id=LOCAL_USER_ID,
            identifier=LOCAL_USER_IDENTIFIER,
            display_name="Local user",
            metadata={"provider": "header"},
            createdAt=LOCAL_USER_CREATED_AT,
        )

    async def create_user(self, user: User) -> PersistedUser | None:
        return await self.get_user(user.identifier)

    async def delete_feedback(self, feedback_id: str) -> bool:
        return False

    async def upsert_feedback(self, feedback: Feedback) -> str:
        raise NotImplementedError("feedback is not enabled")

    async def create_element(self, element: Element) -> None:
        return None

    async def get_element(self, thread_id: str, element_id: str) -> ElementDict | None:
        thread = await self.get_thread(thread_id)
        if thread is None:
            return None
        elements = thread.get("elements") or []
        return next((item for item in elements if item["id"] == element_id), None)

    async def delete_element(self, element_id: str, thread_id: str | None = None) -> None:
        return None

    async def create_step(self, step_dict: StepDict) -> None:
        return None

    async def update_step(self, step_dict: StepDict) -> None:
        return None

    async def delete_step(self, step_id: str) -> None:
        return None

    async def get_thread_author(self, thread_id: str) -> str:
        owner = self.store.thread_owner(thread_id)
        return LOCAL_USER_IDENTIFIER if owner == LOCAL_USER_ID else ""

    async def delete_thread(self, thread_id: str) -> None:
        await delete_conversation(self.store, thread_id, self.checkpoints)

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        threads = self.store.threads(LOCAL_USER_ID)
        if filters.search:
            needle = filters.search.casefold()
            threads = [thread for thread in threads if needle in _thread_name(thread).casefold()]
        start = 0
        if pagination.cursor:
            for index, thread in enumerate(threads):
                if thread.id == pagination.cursor:
                    start = index + 1
                    break
        page = threads[start : start + pagination.first]
        data = [self._thread(thread, include_content=False) for thread in page]
        return PaginatedResponse(
            pageInfo=PageInfo(
                hasNextPage=start + len(page) < len(threads),
                startCursor=page[0].id if page else None,
                endCursor=page[-1].id if page else None,
            ),
            data=data,
        )

    async def get_thread(self, thread_id: str) -> ThreadDict | None:
        thread = next(
            (item for item in self.store.threads(LOCAL_USER_ID) if item.id == thread_id),
            None,
        )
        return self._thread(thread, include_content=True) if thread else None

    async def update_thread(
        self,
        thread_id: str,
        name: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        # Canonical threads are created when the application stores the first
        # message. Treating Chainlit's metadata callback as content creates
        # phantom empty chats and can resurrect a thread immediately after its
        # native deletion callback completed.
        return None

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        self.store.close()

    async def get_favorite_steps(self, user_id: str) -> list[StepDict]:
        return []

    def _thread(self, thread: Thread, include_content: bool) -> ThreadDict:
        steps: list[StepDict] = []
        elements: list[ElementDict] = []
        if include_content:
            messages = self.store.messages(thread.id)
            # The harness's own lines, each after the messages it followed.
            notes = self.store.notes(thread.id)
            noted = 0
            turn: StepDict | None = None
            calls: dict[str, StepDict] = {}
            for position, message in enumerate(messages):
                while noted < len(notes) and notes[noted].position <= position:
                    steps.append(_note_step(noted, notes[noted]))
                    noted += 1
                if message.role == "user":
                    turn = None
                # A turn's calls sit under one collapsed step, each call a tool
                # step with its arguments and, once it came, its result: what
                # the live turn showed (ISS-0076). The person's message and
                # the answer stand on their own.
                if message.role == "assistant" and message.tool_calls:
                    if turn is None:
                        turn = _turn_step(thread.id, position, 0, thread.created_at)
                        steps.append(turn)
                    for index, call in enumerate(message.tool_calls):
                        turn["_calls"] = turn.get("_calls", 0) + 1  # type: ignore[typeddict-unknown-key]
                        plural = "" if turn["_calls"] == 1 else "s"  # type: ignore[typeddict-item]
                        turn["name"] = f"{turn['_calls']} tool call{plural}"  # type: ignore[typeddict-item]
                        child = _call_step(thread.id, position, index, call, thread.created_at, turn["id"])
                        calls[call.id] = child
                        steps.append(child)
                    if not _text(message):
                        continue
                if message.role == "tool":
                    child = calls.get(message.tool_call_id or "")
                    if child is None:
                        # A result whose call was not stored (an older thread).
                        if turn is None:
                            turn = _turn_step(thread.id, position, 0, thread.created_at)
                            steps.append(turn)
                        turn["_calls"] = turn.get("_calls", 0) + 1  # type: ignore[typeddict-unknown-key]
                        plural = "" if turn["_calls"] == 1 else "s"  # type: ignore[typeddict-item]
                        turn["name"] = f"{turn['_calls']} tool call{plural}"  # type: ignore[typeddict-item]
                        child = _step(thread.id, position, message, thread.created_at, parent_id=turn["id"])
                        steps.append(child)
                    else:
                        child["output"] = _step(thread.id, position, message, thread.created_at)["output"]
                    for part_index, part in enumerate(message.content):
                        if part.kind != "text" and part.outbound:
                            elements.append(_element(thread.id, child["id"], position, part_index, part))
                    continue
                step = _step(thread.id, position, message, thread.created_at)
                steps.append(step)
                for part_index, part in enumerate(message.content):
                    if part.kind != "text":
                        elements.append(_element(thread.id, step["id"], position, part_index, part))
                    elif part.hidden and part.name and self.workspace is not None:
                        elements.extend(
                            _saved_files(thread.id, step["id"], position, part_index, part.name, self.workspace)
                        )
            for index in range(noted, len(notes)):
                steps.append(_note_step(index, notes[index]))
            for step in steps:
                step.pop("_calls", None)  # type: ignore[misc]
        return {
            "id": thread.id,
            "createdAt": thread.created_at,
            "name": _thread_name(thread),
            "userId": LOCAL_USER_ID,
            "userIdentifier": LOCAL_USER_IDENTIFIER,
            "tags": [],
            "metadata": {},
            "steps": steps,
            "elements": elements,
        }
