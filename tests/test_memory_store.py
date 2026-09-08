"""Persistence, including the part that only matters after a restart."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.memory import LOCAL_USER_ID, SqliteStore
from app.memory.store import match_query
from app.models import ContentPart, Message, ToolCall


def text(value: str) -> list[ContentPart]:
    return [ContentPart(kind="text", text=value)]


def user(value: str) -> Message:
    return Message(role="user", content=text(value))


@pytest.fixture
def store() -> SqliteStore:
    with SqliteStore() as store:
        yield store


# --- messages ----------------------------------------------------------------


def test_messages_come_back_in_order(store: SqliteStore) -> None:
    store.append("t1", [user("one"), user("two"), user("three")], LOCAL_USER_ID)

    assert [m.content[0].text for m in store.messages("t1")] == ["one", "two", "three"]


def test_appending_twice_continues_the_positions(store: SqliteStore) -> None:
    store.append("t1", [user("one")], LOCAL_USER_ID)
    store.append("t1", [user("two")], LOCAL_USER_ID)

    assert store.message_count("t1") == 2
    assert [m.content[0].text for m in store.messages("t1")] == ["one", "two"]


def test_threads_do_not_see_each_other(store: SqliteStore) -> None:
    store.append("t1", [user("mine")], LOCAL_USER_ID)
    store.append("t2", [user("yours")], LOCAL_USER_ID)

    assert [m.content[0].text for m in store.messages("t1")] == ["mine"]
    assert {thread.id for thread in store.threads(LOCAL_USER_ID)} == {"t1", "t2"}


def test_deleting_a_thread_removes_messages_but_preserves_approved_facts(
    store: SqliteStore,
) -> None:
    store.append("deleted", [user("remove this chat")], LOCAL_USER_ID)
    store.append("kept", [user("keep this chat")], LOCAL_USER_ID)
    store.remember("The user prefers concise answers", LOCAL_USER_ID, thread_id="deleted")

    assert store.delete_thread("deleted") is True

    assert store.messages("deleted") == []
    assert {thread.id for thread in store.threads(LOCAL_USER_ID)} == {"kept"}
    assert store.search("concise", LOCAL_USER_ID) == ["The user prefers concise answers"]
    assert store.delete_thread("deleted") is False


def test_a_thread_carries_enough_to_be_recognised(store: SqliteStore) -> None:
    store.append("t1", [user("how do I read a file"), user("and write one")], LOCAL_USER_ID)

    [thread] = store.threads(LOCAL_USER_ID)

    assert thread.opening == "how do I read a file"
    assert thread.messages == 2


def test_a_thread_that_opened_with_a_picture_has_no_words(store: SqliteStore) -> None:
    store.append(
        "t1",
        [Message(role="user", content=[ContentPart(kind="image", data=b"\x89PNG", media_type="image/png")])], LOCAL_USER_ID)

    [thread] = store.threads(LOCAL_USER_ID)

    assert thread.opening == ""


def test_tool_calls_and_ids_survive_a_round_trip(store: SqliteStore) -> None:
    call = ToolCall(id="call_1", name="read_file", arguments={"path": "a.txt"})
    store.append(
        "t1",
        [
            Message(role="assistant", tool_calls=(call,)),
            Message(role="tool", content=text("contents"), tool_call_id="call_1"),
        ], LOCAL_USER_ID)

    assistant, tool = store.messages("t1")
    assert assistant.content == ()
    assert assistant.tool_calls == (call,)
    assert tool.tool_call_id == "call_1"


def test_media_bytes_survive_a_round_trip(store: SqliteStore) -> None:
    part = ContentPart(kind="image", data=b"\x89PNG\x00\xff", media_type="image/png")
    store.append("t1", [Message(role="user", content=[ContentPart(kind="text", text="see"), part])], LOCAL_USER_ID)

    [message] = store.messages("t1")
    assert message.content[1].data == b"\x89PNG\x00\xff"
    assert message.content[1].media_type == "image/png"


def test_a_sent_file_is_kept_by_name_and_not_by_its_bytes(store: SqliteStore) -> None:
    """ISS-0065: an outbound part is the transport's; the history keeps the
    delivery. Media the model was shown stays whole (the test above)."""

    part = ContentPart(
        kind="file",
        data=b"report",
        media_type="application/pdf",
        name="report.pdf",
        outbound=True,
    )
    store.append("t1", [Message(role="tool", content=[part], tool_call_id="send")], LOCAL_USER_ID)

    [message] = store.messages("t1")
    assert list(message.content) == [
        ContentPart(kind="text", text="Sent report.pdf (application/pdf, 6 bytes).")
    ]


def test_messages_can_be_read_from_a_position(store: SqliteStore) -> None:
    store.append("t1", [user("one"), user("two"), user("three")], LOCAL_USER_ID)

    assert [m.content[0].text for m in store.messages("t1", after=0)] == ["two", "three"]


def test_a_thread_survives_reopening_the_file(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    with SqliteStore(path) as first:
        first.append("t1", [user("remember me")], LOCAL_USER_ID)

    with SqliteStore(path) as second:
        assert [m.content[0].text for m in second.messages("t1")] == ["remember me"]


# --- rolling summary ---------------------------------------------------------


def test_a_thread_starts_without_a_summary(store: SqliteStore) -> None:
    store.ensure_thread("t1", LOCAL_USER_ID)

    assert store.summary("t1") == (None, 0)


def test_the_summary_records_what_it_covers(store: SqliteStore) -> None:
    store.ensure_thread("t1", LOCAL_USER_ID)

    store.set_summary("t1", "they discussed cats", 6)

    assert store.summary("t1") == ("they discussed cats", 6)


def test_an_unknown_thread_has_no_summary(store: SqliteStore) -> None:
    assert store.summary("never-seen") == (None, 0)


# --- facts -------------------------------------------------------------------


def test_a_fact_is_found_by_a_word_in_it(store: SqliteStore) -> None:
    store.remember("The human prefers PowerShell over cmd", LOCAL_USER_ID)

    assert store.search("powershell", LOCAL_USER_ID) == ["The human prefers PowerShell over cmd"]


def test_facts_are_not_scoped_to_the_thread_that_saved_them(store: SqliteStore) -> None:
    store.remember("The project targets an RTX 4090", LOCAL_USER_ID, thread_id="session-one")

    assert store.search("4090", LOCAL_USER_ID, limit=1) == ["The project targets an RTX 4090"]


def test_a_fact_found_in_a_later_session_survives_the_file(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    with SqliteStore(path) as first:
        first.remember("The vLLM server runs in WSL2", LOCAL_USER_ID, thread_id="session-one")

    with SqliteStore(path) as second:
        assert second.search("wsl2", LOCAL_USER_ID) == ["The vLLM server runs in WSL2"]


def test_search_returns_nothing_rather_than_everything_when_it_misses(store: SqliteStore) -> None:
    store.remember("The human prefers PowerShell", LOCAL_USER_ID)

    assert store.search("kangaroo", LOCAL_USER_ID) == []


def test_search_respects_the_limit(store: SqliteStore) -> None:
    for index in range(5):
        store.remember(f"fact number {index} about memory", LOCAL_USER_ID)

    assert len(store.search("memory", LOCAL_USER_ID, limit=2)) == 2


def test_an_empty_fact_is_refused(store: SqliteStore) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        store.remember("   ", LOCAL_USER_ID)


@pytest.mark.parametrize(
    "query", ["what about C++ ?", "NOT AND OR", "-dash *star", "", "   ", "((("]
)
def test_punctuation_in_a_query_does_not_reach_fts(store: SqliteStore, query: str) -> None:
    """A model writes the query, so FTS5 syntax must never leak through it."""

    store.remember("something unrelated", LOCAL_USER_ID)

    assert store.search(query, LOCAL_USER_ID) == []


def test_a_query_matches_any_of_its_words(store: SqliteStore) -> None:
    store.remember("The GPU is an RTX 4090 with 24 GB", LOCAL_USER_ID)

    assert store.search("How much VRAM does the GPU have?", LOCAL_USER_ID) != []


def test_match_query_quotes_every_token() -> None:
    assert match_query("C++ and Rust!") == '"C" OR "and" OR "Rust"'
