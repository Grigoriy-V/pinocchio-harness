"""The PostgreSQL implementation of the telemetry contract.

The deployed worker has no disk anything else can read, so the durable trace
goes where the conversations already are — the same database and the same
schema, in tables of its own with a version row of its own. Sharing the database
costs no second credential; sharing the tables would put operational data under
the rules that protect a conversation, which is not what either needs.

Connection handling mirrors `app/memory/postgres.py` for the same reason: a
serverless worker is idle by design between messages, and a pooled endpoint
hangs up on an idle client, so reconnecting after a pause is the normal path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.memory.postgres import CONNECTION_GUARDS

from app.telemetry.base import TelemetryStore, TraceEvent, TurnRun
from app.telemetry.sqlite import COLUMNS, filters

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS turn_runs (
    run_id               TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL DEFAULT '',
    thread_id            TEXT NOT NULL DEFAULT '',
    source               TEXT NOT NULL DEFAULT '',
    source_update_id     TEXT NOT NULL DEFAULT '',
    started_at           TEXT NOT NULL,
    finished_at          TEXT,
    status               TEXT NOT NULL DEFAULT 'running',
    outcome              TEXT,
    route                TEXT,
    model_calls          INTEGER NOT NULL DEFAULT 0,
    tool_calls           INTEGER NOT NULL DEFAULT 0,
    input_tokens         INTEGER NOT NULL DEFAULT 0,
    output_tokens        INTEGER NOT NULL DEFAULT 0,
    first_model_token_ms INTEGER,
    first_visible_ms     INTEGER,
    total_ms             INTEGER,
    error_type           TEXT
);

CREATE TABLE IF NOT EXISTS trace_events (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    timestamp   TEXT NOT NULL,
    type        TEXT NOT NULL,
    duration_ms INTEGER,
    data        JSONB,
    UNIQUE (run_id, seq)
);

CREATE INDEX IF NOT EXISTS runs_by_start ON turn_runs (started_at);
CREATE INDEX IF NOT EXISTS runs_by_user ON turn_runs (user_id, started_at);
CREATE INDEX IF NOT EXISTS runs_by_status ON turn_runs (status, started_at);
"""

NAME = re.compile(r"\w+", re.UNICODE)


def migrate(connection: psycopg.Connection, schema: str) -> int:
    """Create the telemetry tables, returning the version found.

    Additive from the first version: it creates tables that did not exist and
    touches nothing that did. Run from `tools/setup_control_plane.py`, never
    from a worker starting up.
    """

    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        cursor.execute(f'SET LOCAL search_path TO "{schema}"')
        cursor.execute(SCHEMA)
        cursor.execute("SELECT version FROM telemetry_version LIMIT 1")
        row = cursor.fetchone()
        found = 0 if row is None else int(row["version"])
        if found >= SCHEMA_VERSION:
            connection.commit()
            return found
        if row is None:
            cursor.execute(
                "INSERT INTO telemetry_version (version) VALUES (%s)", (SCHEMA_VERSION,)
            )
        else:
            cursor.execute("UPDATE telemetry_version SET version = %s", (SCHEMA_VERSION,))
    connection.commit()
    return found


class PostgresTelemetry(TelemetryStore):
    """Turn records and traces in the deployment's own database."""

    def __init__(
        self, dsn: str, schema: str = "public", *, migrate_schema: bool = False
    ) -> None:
        if not NAME.fullmatch(schema):
            raise ValueError(f"not a usable schema name: {schema!r}")
        self.dsn = dsn
        self.schema = schema
        self._connection: psycopg.Connection | None = self._open()
        if migrate_schema:
            migrate(self._connection, self.schema)

    def _open(self) -> psycopg.Connection:
        connection = psycopg.connect(self.dsn, row_factory=dict_row, **CONNECTION_GUARDS)
        self._connection = connection
        return connection

    def _live_connection(self) -> psycopg.Connection:
        connection = self._connection
        if connection is None or connection.closed or connection.broken:
            connection = self._open()
        return connection

    @contextmanager
    def _cursor(self) -> Iterator[psycopg.Cursor]:
        connection = self._live_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(f'SET LOCAL search_path TO "{self.schema}"')
                yield cursor
            if (
                not connection.closed
                and connection.info.transaction_status != TransactionStatus.IDLE
            ):
                connection.commit()
        except psycopg.OperationalError:
            self._connection = None
            connection.close()
            raise

    def _write(self, run: TurnRun) -> None:
        placeholders = ", ".join("%s" for _ in COLUMNS)
        assignments = ", ".join(f"{name} = EXCLUDED.{name}" for name in COLUMNS[1:])
        with self._cursor() as cursor:
            cursor.execute(
                f"INSERT INTO turn_runs ({', '.join(COLUMNS)}) VALUES ({placeholders})"
                f" ON CONFLICT (run_id) DO UPDATE SET {assignments}",
                [getattr(run, name) for name in COLUMNS],
            )
            cursor.connection.commit()

    def start_turn(self, run: TurnRun) -> None:
        self._write(run)

    def finish_turn(self, run: TurnRun) -> None:
        self._write(run)

    def record_events(self, events: Sequence[TraceEvent]) -> None:
        if not events:
            return
        with self._cursor() as cursor:
            cursor.executemany(
                "INSERT INTO trace_events"
                " (run_id, seq, timestamp, type, duration_ms, data)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (run_id, seq) DO NOTHING",
                [
                    (
                        event.run_id,
                        event.seq,
                        event.timestamp,
                        event.type,
                        event.duration_ms,
                        Jsonb(event.data) if event.data else None,
                    )
                    for event in events
                ],
            )
            cursor.connection.commit()

    def get_turn(self, run_id: str) -> TurnRun | None:
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM turn_runs WHERE run_id = %s", (run_id,))
            row = cursor.fetchone()
        return None if row is None else TurnRun(**{name: row[name] for name in COLUMNS})

    def recent_runs(
        self,
        *,
        limit: int = 20,
        user_id: str | None = None,
        unsuccessful: bool = False,
    ) -> list[TurnRun]:
        clauses, values = filters(user_id, unsuccessful, "%s")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM turn_runs{where} ORDER BY started_at DESC, run_id DESC"
                " LIMIT %s",
                (*values, max(1, limit)),
            )
            rows = cursor.fetchall()
        return [TurnRun(**{name: row[name] for name in COLUMNS}) for row in rows]

    def events(self, run_id: str) -> list[TraceEvent]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM trace_events WHERE run_id = %s ORDER BY seq", (run_id,)
            )
            rows = cursor.fetchall()
        return [
            TraceEvent(
                run_id=row["run_id"],
                seq=int(row["seq"]),
                type=row["type"],
                timestamp=row["timestamp"],
                duration_ms=row["duration_ms"],
                data=json.loads(row["data"]) if isinstance(row["data"], str) else (row["data"] or {}),
            )
            for row in rows
        ]

    def drop_schema(self) -> None:
        """Remove this store's schema and everything in it.

        For the contract suite, which gives every test a schema of its own. It
        refuses `public` outright: the default schema is where a real
        deployment's data lives, and a method that can delete it with default
        arguments is one that eventually will.
        """

        if self.schema == "public":
            raise ValueError("refusing to drop the public schema")
        with self._cursor() as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
            cursor.connection.commit()

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None and not connection.closed:
            connection.close()
