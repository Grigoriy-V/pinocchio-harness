from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from psycopg.pq import TransactionStatus

from app.memory.postgres import PostgresStore
from app.memory.records import dump_content
from app.models import ContentPart, Message


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exception: object) -> None:
        return None

    def execute(self, statement: Any, parameters: object = None) -> None:
        rendered = statement.as_string() if hasattr(statement, "as_string") else statement
        self.connection.executions.append((rendered, parameters))
        # A statement outside autocommit opens a transaction, which is the whole
        # reason the live failure existed. The fake models it rather than
        # pretending every connection is always idle.
        if not self.connection.autocommit:
            self.connection.status = TransactionStatus.INTRANS

    def fetchone(self) -> dict[str, object]:
        return self.connection.row

    def fetchall(self) -> list[dict[str, object]]:
        # These tests care about what a read does to the connection, not about
        # what it returns, so a multi-row read is empty rather than invented.
        return []


class FakeInfo:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    @property
    def transaction_status(self) -> TransactionStatus:
        return self.connection.status


class FakeConnection:
    broken = False

    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.closed = False
        self._autocommit = False
        self.status = TransactionStatus.IDLE
        self.executions: list[tuple[str, object]] = []
        self.commits = 0
        self.pipeline_entries = 0
        self.info = FakeInfo(self)

    @property
    def autocommit(self) -> bool:
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        # psycopg refuses this inside a transaction, and refusing it here is
        # what makes the offline test able to see the live failure.
        if self.status != TransactionStatus.IDLE:
            raise psycopg.ProgrammingError(
                "can't change 'autocommit' now: connection in transaction status INTRANS"
            )
        self._autocommit = value

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    @contextmanager
    def pipeline(self) -> Iterator[None]:
        self.pipeline_entries += 1
        yield

    def commit(self) -> None:
        self.commits += 1
        self.status = TransactionStatus.IDLE

    def close(self) -> None:
        self.closed = True


def install_connection(
    monkeypatch: pytest.MonkeyPatch, row: dict[str, object]
) -> FakeConnection:
    connection = FakeConnection(row)
    monkeypatch.setattr(
        "app.memory.postgres.psycopg.connect",
        lambda _dsn, *, row_factory, **guards: connection,
    )
    return connection


def test_every_connection_carries_the_socket_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """ISS-0064: a write into a socket nobody answers waited for ever. The
    bounds are libpq's, so they have to ride on the connect call itself."""

    from app.memory.postgres import CONNECTION_GUARDS

    seen: list[dict[str, object]] = []

    def connect(_dsn: str, *, row_factory: object, **guards: object) -> FakeConnection:
        seen.append(dict(guards))
        return FakeConnection({})

    monkeypatch.setattr("app.memory.postgres.psycopg.connect", connect)
    PostgresStore("postgresql://unused", schema="test", migrate_schema=False)

    assert seen == [dict(CONNECTION_GUARDS)]
    assert CONNECTION_GUARDS["connect_timeout"] > 0
    assert CONNECTION_GUARDS["tcp_user_timeout"] > 0
    assert CONNECTION_GUARDS["keepalives"] == 1


class HungUpConnection(FakeConnection):
    """Looks open, and fails the first statement sent — a server's idle hang-up."""

    def cursor(self) -> FakeCursor:
        return HungUpCursor(self)


class HungUpCursor(FakeCursor):
    def execute(self, statement: Any, parameters: object = None) -> None:
        raise psycopg.OperationalError("consuming input failed: SSL connection has been closed unexpectedly")


def test_a_server_hang_up_during_idle_is_answered_with_a_fresh_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ISS-0048: the pooled server closes an idle connection during a long
    # model call; `closed` and `broken` still say the connection is fine, and
    # the first statement after the pause — the store's own search path — is
    # where the hang-up surfaces. That statement is resent on a fresh
    # connection and the caller's read runs there; the caller never sees it.
    hung_up = HungUpConnection({})
    fresh = FakeConnection({})
    handed_out = iter([hung_up, fresh])
    monkeypatch.setattr(
        "app.memory.postgres.psycopg.connect",
        lambda _dsn, *, row_factory, **guards: next(handed_out),
    )
    store = PostgresStore("postgresql://unused", schema="test", migrate_schema=False)

    assert store.messages("thread") == []

    assert hung_up.closed
    assert hung_up.executions == []
    statements = [statement for statement, _ in fresh.executions]
    assert statements[0] == 'SET LOCAL search_path TO "test"'
    assert any("FROM messages" in statement for statement in statements[1:])


def test_a_hang_up_on_the_fresh_connection_too_is_the_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handed_out = iter([HungUpConnection({}), HungUpConnection({})])
    monkeypatch.setattr(
        "app.memory.postgres.psycopg.connect",
        lambda _dsn, *, row_factory, **guards: next(handed_out),
    )
    store = PostgresStore("postgresql://unused", schema="test", migrate_schema=False)

    with pytest.raises(psycopg.OperationalError):
        store.messages("thread")


def test_runtime_open_does_not_execute_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = install_connection(monkeypatch, {})

    PostgresStore("postgresql://example/db", "assistant")

    assert connection.executions == []


def test_turn_context_is_one_schema_qualified_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = install_connection(
        monkeypatch,
        {
            "summary": "earlier",
            "summarized_through": 0,
            "history": [
                {
                    "position": 0,
                    "role": "user",
                    "content": dump_content(
                        [ContentPart(kind="text", text="hello")]
                    ),
                    "tool_calls": None,
                    "tool_call_id": None,
                }
            ],
            "facts": ["remembered"],
        },
    )
    store = PostgresStore("postgresql://example/db", "assistant")

    records = store.turn_context("thread", "owner", "hello", 5)

    assert records.summary == "earlier"
    assert records.messages[0].content[0].text == "hello"
    assert records.facts == ["remembered"]
    assert len(connection.executions) == 1
    statement, _ = connection.executions[0]
    assert '"assistant".threads' in statement
    assert '"assistant".messages' in statement
    assert '"assistant".facts' in statement
    assert "SET LOCAL" not in statement
    assert connection.autocommit is False


def test_append_is_one_execute_with_a_pipelined_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = install_connection(monkeypatch, {"count": 2})
    store = PostgresStore("postgresql://example/db", "assistant")

    count = store.append(
        "thread",
        [
            Message(role="user", content=[ContentPart(kind="text", text="hello")]),
            Message(
                role="assistant",
                content=[ContentPart(kind="text", text="hi")],
            ),
        ],
        "owner",
    )

    assert count == 2
    assert len(connection.executions) == 1
    assert connection.pipeline_entries == 1
    assert connection.commits == 1
    statement, _ = connection.executions[0]
    assert 'INSERT INTO "assistant".threads' in statement
    assert 'INSERT INTO "assistant".messages' in statement
    assert "SET LOCAL" not in statement


def test_a_prior_read_does_not_block_the_context_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live failure, reproduced without a database.

    Every deployed message died with `can't change 'autocommit' now`. A turn
    lists threads before it reads context; that first read left the connection
    in a transaction, and the single-round-trip context query cannot switch
    autocommit there. The fake connection refuses the switch exactly as psycopg
    does, so this test fails without the fix.
    """

    connection = install_connection(
        monkeypatch,
        {
            "summary": None,
            "summarized_through": 0,
            "history": [],
            "facts": [],
        },
    )
    store = PostgresStore("postgresql://example/db", "assistant")
    store.threads("user-alice")

    assert connection.status == TransactionStatus.IDLE, "a read must not stay open"

    store.turn_context("t1", "user-alice", "anything", 5)

    assert connection.autocommit is False, "autocommit is restored afterwards"
