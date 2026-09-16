"""The way back to what a summary or a stub stands for."""

from __future__ import annotations

import pytest

from app.memory import LOCAL_USER_ID, SqliteStore
from app.models import ContentPart, Message, ToolCall, ToolFailure
from app.tools import Tool, ToolError, history_tools

OTHER = "someone-else"


@pytest.fixture
def store() -> SqliteStore:
    with SqliteStore() as store:
        yield store


def tools(store: SqliteStore, thread_id: str = "t1", user_id: str = LOCAL_USER_ID) -> dict[str, Tool]:
    return {tool.name: tool for tool in history_tools(store, user_id, thread_id)}


def user(text: str) -> Message:
    return Message(role="user", content=[ContentPart(kind="text", text=text)])


def result(text: str, failure: ToolFailure | None = None) -> Message:
    return Message(
        role="tool", tool_call_id="c1", content=[ContentPart(kind="text", text=text)], failure=failure
    )


def a_conversation(store: SqliteStore) -> None:
    store.append(
        "t1",
        [
            user("build the board"),
            Message(
                role="assistant",
                tool_calls=(ToolCall(id="c1", name="write_file", arguments={"path": "board/index.html", "content": "<html>"}),),
            ),
            result("error", ToolFailure(code="fs.not_found", message="no such folder: board")),
            user("what was that error exactly?"),
        ],
        LOCAL_USER_ID,
    )


def test_searching_names_the_position_and_shows_the_words_around_the_match(store: SqliteStore) -> None:
    a_conversation(store)

    found = tools(store)["search_history"].run(query="folder")

    assert found.startswith("#2 tool 20")
    assert "fs.not_found: no such folder: board" in found
    assert "in conversation" not in found, "this conversation needs no name"


def test_a_miss_says_so(store: SqliteStore) -> None:
    a_conversation(store)

    assert tools(store)["search_history"].run(query="kangaroo") == "no message in this conversation matches 'kangaroo'"


def test_other_conversations_are_searched_only_when_asked(store: SqliteStore) -> None:
    a_conversation(store)
    store.append("t2", [user("the board from last week")], LOCAL_USER_ID)
    store.append("t9", [user("someone else's board")], OTHER)

    here = tools(store)["search_history"].run(query="board")
    everywhere = tools(store)["search_history"].run(query="board", all_conversations=True)

    assert "in conversation t2" not in here
    assert "in conversation t2" in everywhere
    assert "in conversation t1" in everywhere
    assert "t9" not in everywhere


def test_reading_returns_the_message_as_it_was_said(store: SqliteStore) -> None:
    a_conversation(store)

    text = tools(store)["read_history"].run(position=1)

    assert text.startswith("#1 assistant\nwrite_file {\"path\": \"board/index.html\", \"content\": \"<html>\"}")
    assert "#2 tool\nerror\nfs.not_found: no such folder: board" in text, "a call comes with its result"
    assert "#3" not in text


def test_a_hit_on_a_call_shows_what_came_back(store: SqliteStore) -> None:
    """Run `live-80`, 2026-09-03: the model found the call, not the failure
    one message later, and said no error had happened."""

    a_conversation(store)

    found = tools(store)["search_history"].run(query="write_file")

    assert found.startswith("#1 assistant 20")
    assert "\n  → #2 failed: error fs.not_found: no such folder: board" in found


def test_a_long_message_comes_in_pages(store: SqliteStore) -> None:
    from app.tools.history import PAGE_CHARS

    # A page is the budget's (roadmap 31); the message is two and a half pages.
    page = PAGE_CHARS
    store.append("t1", [user("x"), result("a" * (page * 5 // 2))], LOCAL_USER_ID)
    read = tools(store)["read_history"]

    first = read.run(position=1)
    middle = read.run(position=1, offset=page)
    last = read.run(position=1, offset=2 * page)

    assert first.endswith(f"for the rest, read_history again with position=1, count=1, offset={page}")
    body = first.split("\n... showing")[0]
    assert body == "#1 tool\n" + "a" * (page - len("#1 tool\n"))
    assert middle.endswith(f"offset={2 * page}")
    assert last == "a" * (page * 5 // 2 - body.count("a") - page)


def test_another_person_s_conversation_is_not_there(store: SqliteStore) -> None:
    a_conversation(store)
    store.append("t9", [user("private")], OTHER)

    with pytest.raises(ToolError, match="no conversation 't9' of yours") as refused:
        tools(store)["read_history"].run(position=0, conversation="t9")
    assert refused.value.code == "history.not_found"
    assert tools(store, "t9", OTHER)["read_history"].run(position=0) == "#0 user\nprivate"


def test_a_position_past_the_end_says_how_long_the_conversation_is(store: SqliteStore) -> None:
    a_conversation(store)

    with pytest.raises(ToolError, match="nothing at position 7: the conversation has 4 messages"):
        tools(store)["read_history"].run(position=7)
