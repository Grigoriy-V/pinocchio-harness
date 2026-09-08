from __future__ import annotations

import base64
from pathlib import Path

import aiosqlite
import pytest

pytest.importorskip("chainlit", reason="the ui dependency group is optional")

from chainlit.types import Pagination, ThreadFilter
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import ContentPart, Message
from ui.chainlit_history import MemoryStoreDataLayer


@pytest.fixture
def layer(tmp_path):
    store = SqliteStore(tmp_path / "memory.sqlite3")
    return MemoryStoreDataLayer(
        store, checkpoints=str(tmp_path / "checkpoints.sqlite3")
    )


async def checkpoint(path: Path, thread_id: str) -> None:
    connection = await aiosqlite.connect(path)
    try:
        saver = AsyncSqliteSaver(connection)
        await saver.setup()
        await saver.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            empty_checkpoint(),
            {"source": "input", "step": -1, "parents": {}},
            {},
        )
    finally:
        await connection.close()


async def has_checkpoint(path: Path, thread_id: str) -> bool:
    connection = await aiosqlite.connect(path)
    try:
        saver = AsyncSqliteSaver(connection)
        await saver.setup()
        return await saver.aget_tuple(
            {"configurable": {"thread_id": thread_id}}
        ) is not None
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_native_history_lists_existing_threads_newest_first(layer) -> None:
    layer.store.append("first", [Message(role="user", content=[ContentPart("text", text="old")])], LOCAL_USER_ID)
    layer.store.append("second", [Message(role="user", content=[ContentPart("text", text="new")])], LOCAL_USER_ID)

    result = await layer.list_threads(Pagination(first=10), ThreadFilter(userId=LOCAL_USER_ID))

    assert [thread["id"] for thread in result.data] == ["second", "first"]
    assert result.data[0]["name"] == "new"
    assert result.data[0]["steps"] == []


@pytest.mark.asyncio
async def test_native_history_resumes_text_and_media(layer) -> None:
    layer.store.append(
        "chat",
        [
            Message(
                role="user",
                content=[
                    ContentPart("text", text="look"),
                    ContentPart("image", data=b"png", media_type="image/png"),
                ],
            ),
            Message(role="assistant", content=[ContentPart("text", text="I see it")]),
        ], LOCAL_USER_ID)

    thread = await layer.get_thread("chat")

    assert thread is not None
    assert [step["type"] for step in thread["steps"]] == [
        "user_message",
        "assistant_message",
    ]
    assert [step["output"] for step in thread["steps"]] == ["look", "I see it"]
    element = thread["elements"][0]
    assert element["forId"] == thread["steps"][0]["id"]
    assert element["url"] == f"data:image/png;base64,{base64.b64encode(b'png').decode('ascii')}"


@pytest.mark.asyncio
async def test_native_history_hides_observation_media_and_names_what_was_sent(layer) -> None:
    layer.store.append(
        "chat",
        [
            Message(
                role="tool",
                content=[
                    ContentPart("text", text="private evidence"),
                    ContentPart("image", data=b"seen", media_type="image/png"),
                ],
                tool_call_id="look",
            ),
            Message(
                role="tool",
                content=[
                    ContentPart("text", text="selected"),
                    ContentPart(
                        "image",
                        data=b"sent",
                        media_type="image/png",
                        name="chosen.png",
                        outbound=True,
                    ),
                ],
                tool_call_id="send",
            ),
        ],
        LOCAL_USER_ID,
    )

    thread = await layer.get_thread("chat")

    assert thread is not None
    assert [step["output"] for step in thread["steps"]] == ["Completed.", "Completed."]
    # The sent picture is in the history by name, not as an element: the
    # store keeps the delivery, not the bytes (ISS-0065).
    assert thread["elements"] == []


@pytest.mark.asyncio
async def test_native_history_paginates_and_searches(layer) -> None:
    for thread_id, opening in (("one", "alpha"), ("two", "beta"), ("three", "gamma")):
        layer.store.append(
            thread_id, [Message(role="user", content=[ContentPart("text", text=opening)])], LOCAL_USER_ID)

    first = await layer.list_threads(Pagination(first=2), ThreadFilter())
    second = await layer.list_threads(
        Pagination(first=2, cursor=first.pageInfo.endCursor), ThreadFilter()
    )
    found = await layer.list_threads(Pagination(first=10), ThreadFilter(search="ALPHA"))

    assert first.pageInfo.hasNextPage is True
    assert len(second.data) == 1
    assert [thread["id"] for thread in found.data] == ["one"]


@pytest.mark.asyncio
async def test_chainlit_metadata_update_does_not_create_a_phantom_empty_thread(layer) -> None:
    await layer.update_thread("new-chat", name="first message", user_id=LOCAL_USER_ID)

    assert await layer.get_thread_author("new-chat") == ""
    assert await layer.get_thread("new-chat") is None


@pytest.mark.asyncio
async def test_native_delete_removes_chat_and_resumable_state_but_keeps_memory(
    layer,
) -> None:
    checkpoint_path = Path(layer.checkpoints)
    layer.store.append(
        "chat", [Message(role="user", content=[ContentPart("text", text="remove")])], LOCAL_USER_ID)
    layer.store.remember("Keep this approved fact", LOCAL_USER_ID, thread_id="chat")
    await checkpoint(checkpoint_path, "chat")
    await checkpoint(checkpoint_path, "other")

    await layer.delete_thread("chat")
    # Chainlit may emit a final metadata callback for the view it just removed.
    await layer.update_thread("chat", name="remove")

    assert await layer.get_thread("chat") is None
    assert layer.store.search("approved", LOCAL_USER_ID) == ["Keep this approved fact"]
    assert not await has_checkpoint(checkpoint_path, "chat")
    assert await has_checkpoint(checkpoint_path, "other")
