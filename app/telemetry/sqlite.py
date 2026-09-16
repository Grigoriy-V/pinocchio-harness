"""The SQLite implementation of the telemetry contract.

Its own file rather than a table beside the conversations: telemetry is
disposable in a way a conversation is not, so deleting it has to cost nothing.
The local profile has no dashboard of any kind, which is the whole reason this
exists on a personal machine as well as in the deployment.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from app.memory.store import connect
from app.telemetry.base import TelemetryStore, TraceEvent, TurnRun

# `PRAGMA user_version` is SQLite's own integer on the file, so the database
# states its shape instead of the code guessing from the columns it finds.
SCHEMA_VERSION = 1

SCHEMA = """
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

-- `UNIQUE (run_id, seq)` is what makes a repeated flush harmless: a batch that
-- was written and then retried after a failure elsewhere inserts nothing twice.
CREATE TABLE IF NOT EXISTS trace_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    timestamp   TEXT NOT NULL,
    type        TEXT NOT NULL,
    duration_ms INTEGER,
    data        TEXT,
    UNIQUE (run_id, seq)
);

CREATE INDEX IF NOT EXISTS runs_by_start ON turn_runs(started_at);
CREATE INDEX IF NOT EXISTS runs_by_user ON turn_runs(user_id, started_at);
CREATE INDEX IF NOT EXISTS runs_by_status ON turn_runs(status, started_at);
"""

COLUMNS = (
    "run_id",
    "user_id",
    "thread_id",
    "source",
    "source_update_id",
    "started_at",
    "finished_at",
    "status",
    "outcome",
    "route",
    "model_calls",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "first_model_token_ms",
    "first_visible_ms",
    "total_ms",
    "error_type",
)


def row_to_run(row: sqlite3.Row) -> TurnRun:
    return TurnRun(**{name: row[name] for name in COLUMNS})


def filters(
    user_id: str | None, unsuccessful: bool, placeholder: str
) -> tuple[list[str], list[object]]:
    """The `recent_runs` predicate, shared so both stores select the same rows.

    Two implementations of one contract that disagree about what "failed" means
    would make the deployed and local answers quietly different, which is worse
    than having no listing at all.
    """

    clauses: list[str] = []
    values: list[object] = []
    if user_id is not None:
        clauses.append(f"user_id = {placeholder}")
        values.append(user_id)
    if unsuccessful:
        # A turn that never finished is the crash case: nothing wrote its
        # outcome because nothing survived to write it.
        clauses.append("(outcome = 'failed' OR finished_at IS NULL)")
    return clauses, values


class SqliteTelemetry(TelemetryStore):
    """Turn records and traces in a local SQLite file."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = connect(self.path)
        self._db.executescript(SCHEMA)
        self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._db.commit()

    def _write(self, run: TurnRun) -> None:
        values = [getattr(run, name) for name in COLUMNS]
        placeholders = ", ".join("?" for _ in COLUMNS)
        assignments = ", ".join(f"{name} = excluded.{name}" for name in COLUMNS[1:])
        self._db.execute(
            f"INSERT INTO turn_runs ({', '.join(COLUMNS)}) VALUES ({placeholders})"
            f" ON CONFLICT(run_id) DO UPDATE SET {assignments}",
            values,
        )
        self._db.commit()

    def start_turn(self, run: TurnRun) -> None:
        self._write(run)

    def finish_turn(self, run: TurnRun) -> None:
        self._write(run)

    def record_events(self, events: Sequence[TraceEvent]) -> None:
        if not events:
            return
        self._db.executemany(
            "INSERT OR IGNORE INTO trace_events"
            " (run_id, seq, timestamp, type, duration_ms, data)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    event.run_id,
                    event.seq,
                    event.timestamp,
                    event.type,
                    event.duration_ms,
                    json.dumps(event.data) if event.data else None,
                )
                for event in events
            ],
        )
        self._db.commit()

    def get_turn(self, run_id: str) -> TurnRun | None:
        row = self._db.execute(
            "SELECT * FROM turn_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else row_to_run(row)

    def recent_runs(
        self,
        *,
        limit: int = 20,
        user_id: str | None = None,
        unsuccessful: bool = False,
    ) -> list[TurnRun]:
        clauses, values = filters(user_id, unsuccessful, "?")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.execute(
            f"SELECT * FROM turn_runs{where} ORDER BY started_at DESC, run_id DESC"
            " LIMIT ?",
            (*values, max(1, limit)),
        ).fetchall()
        return [row_to_run(row) for row in rows]

    def events(self, run_id: str) -> list[TraceEvent]:
        rows = self._db.execute(
            "SELECT * FROM trace_events WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
        return [
            TraceEvent(
                run_id=row["run_id"],
                seq=row["seq"],
                type=row["type"],
                timestamp=row["timestamp"],
                duration_ms=row["duration_ms"],
                data=json.loads(row["data"]) if row["data"] else {},
            )
            for row in rows
        ]

    def close(self) -> None:
        self._db.close()
