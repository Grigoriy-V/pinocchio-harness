"""One suite every `TelemetryStore` implementation must pass.

The same rule as the conversation store: a second implementation that is not
exercised by the first one's tests drifts silently. The PostgreSQL entry appears
only when `AGENT_TEST_DATABASE_URL` is configured, so the offline suite stays
offline and no test can reach the deployed database by accident.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from app.telemetry import TelemetryStore, TraceEvent, TurnRun
from app.telemetry.sqlite import SqliteTelemetry

POSTGRES_DSN = os.environ.get("AGENT_TEST_DATABASE_URL", "")


def postgres_telemetry(_tmp_path: Path) -> TelemetryStore:
    from app.telemetry.postgres import PostgresTelemetry

    return PostgresTelemetry(
        POSTGRES_DSN,
        schema=f"telemetry_{uuid.uuid4().hex[:12]}",
        migrate_schema=True,
    )


FACTORIES: dict[str, Callable[[Path], TelemetryStore]] = {
    "sqlite": lambda tmp_path: SqliteTelemetry(tmp_path / "telemetry.sqlite3"),
}
if POSTGRES_DSN:
    FACTORIES["postgres"] = postgres_telemetry


@pytest.fixture(params=sorted(FACTORIES))
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[TelemetryStore]:
    opened = FACTORIES[request.param](tmp_path)
    try:
        yield opened
    finally:
        drop = getattr(opened, "drop_schema", None)
        if drop is not None:
            drop()
        opened.close()


def run(run_id: str = "r1") -> TurnRun:
    return TurnRun(
        run_id=run_id,
        user_id="user-alice",
        thread_id="t1",
        source="telegram",
        source_update_id="7",
    )


def test_a_started_turn_is_readable_before_it_finishes(store: TelemetryStore) -> None:
    """A container that dies mid-turn must still leave the turn visible."""

    store.start_turn(run())

    stored = store.get_turn("r1")
    assert stored is not None
    assert stored.status == "running"
    assert stored.outcome is None
    assert stored.finished_at is None


def test_finishing_rewrites_the_same_row(store: TelemetryStore) -> None:
    record = run()
    store.start_turn(record)
    record.status = "completed"
    record.outcome = "answer_delivered"
    record.finished_at = "2026-08-29T10:00:00.000+00:00"
    record.model_calls = 2
    record.tool_calls = 1
    record.input_tokens = 4872
    record.output_tokens = 614
    record.first_model_token_ms = 1160
    record.first_visible_ms = 2100
    record.total_ms = 8400
    store.finish_turn(record)

    stored = store.get_turn("r1")
    assert stored is not None
    assert (stored.status, stored.outcome) == ("completed", "answer_delivered")
    assert (stored.model_calls, stored.tool_calls) == (2, 1)
    assert (stored.input_tokens, stored.output_tokens) == (4872, 614)
    assert stored.first_model_token_ms == 1160
    assert stored.first_visible_ms == 2100
    assert stored.successful is True


def test_an_unknown_run_has_no_row(store: TelemetryStore) -> None:
    assert store.get_turn("never-happened") is None


def test_events_come_back_in_sequence_order(store: TelemetryStore) -> None:
    """`seq`, not the timestamp: events inside one millisecond are ordinary."""

    store.start_turn(run())
    store.record_events(
        [
            TraceEvent("r1", 3, "model_finished", timestamp="2026-08-29T10:00:00.000+00:00"),
            TraceEvent("r1", 1, "turn_started", timestamp="2026-08-29T10:00:00.000+00:00"),
            TraceEvent("r1", 2, "model_started", timestamp="2026-08-29T10:00:00.000+00:00"),
        ]
    )

    assert [event.type for event in store.events("r1")] == [
        "turn_started",
        "model_started",
        "model_finished",
    ]


def test_events_keep_their_data_and_duration(store: TelemetryStore) -> None:
    store.start_turn(run())
    store.record_events(
        [
            TraceEvent(
                "r1",
                1,
                "tool_finished",
                duration_ms=1834,
                data={"tool": "view_web_page", "status": "success"},
            )
        ]
    )

    [event] = store.events("r1")
    assert event.duration_ms == 1834
    assert event.data == {"tool": "view_web_page", "status": "success"}


def test_events_belong_to_their_own_run(store: TelemetryStore) -> None:
    store.start_turn(run("r1"))
    store.start_turn(run("r2"))
    store.record_events([TraceEvent("r1", 1, "turn_started")])
    store.record_events([TraceEvent("r2", 1, "turn_started"), TraceEvent("r2", 2, "turn_finished")])

    assert len(store.events("r1")) == 1
    assert len(store.events("r2")) == 2


def test_writing_a_batch_twice_does_not_duplicate_it(store: TelemetryStore) -> None:
    """A flush retried after a failure elsewhere must not double the trace."""

    store.start_turn(run())
    batch = [TraceEvent("r1", 1, "turn_started"), TraceEvent("r1", 2, "model_started")]
    store.record_events(batch)
    store.record_events(batch)

    assert len(store.events("r1")) == 2


def test_an_empty_batch_writes_nothing(store: TelemetryStore) -> None:
    store.start_turn(run())
    store.record_events([])

    assert store.events("r1") == []


# --- listing -----------------------------------------------------------------


def finished(
    run_id: str,
    *,
    user_id: str = "user-alice",
    started_at: str,
    outcome: str = "answer_delivered",
) -> TurnRun:
    record = TurnRun(run_id=run_id, user_id=user_id, started_at=started_at)
    record.status = "failed" if outcome == "failed" else "completed"
    record.outcome = outcome  # type: ignore[assignment]
    record.finished_at = started_at
    record.total_ms = 1000
    return record


def test_recent_runs_are_newest_first_and_bounded(store: TelemetryStore) -> None:
    for index in range(5):
        store.start_turn(finished(f"r{index}", started_at=f"2026-08-29T10:0{index}:00.000+00:00"))

    listed = store.recent_runs(limit=3)

    assert [record.run_id for record in listed] == ["r4", "r3", "r2"]


def test_recent_runs_can_be_restricted_to_one_user(store: TelemetryStore) -> None:
    store.start_turn(finished("r1", started_at="2026-08-29T10:00:00.000+00:00"))
    store.start_turn(
        finished("r2", user_id="user-bob", started_at="2026-08-29T10:01:00.000+00:00")
    )

    listed = store.recent_runs(user_id="user-bob")

    assert [record.run_id for record in listed] == ["r2"]


def test_the_unsuccessful_list_holds_failures_and_turns_that_never_ended(
    store: TelemetryStore,
) -> None:
    """A container that died leaves `running` forever, and that is the crash.

    Filtering on `status = 'failed'` would show only the failures the
    application survived long enough to record, which are the ones that were
    already the least mysterious.
    """

    store.start_turn(finished("ok", started_at="2026-08-29T10:00:00.000+00:00"))
    store.start_turn(
        finished("broke", started_at="2026-08-29T10:01:00.000+00:00", outcome="failed")
    )
    store.start_turn(TurnRun(run_id="abandoned", started_at="2026-08-29T10:02:00.000+00:00"))
    store.start_turn(
        finished(
            "stopped", started_at="2026-08-29T10:03:00.000+00:00", outcome="cancelled"
        )
    )

    listed = store.recent_runs(unsuccessful=True)

    assert sorted(record.run_id for record in listed) == ["abandoned", "broke"]


def test_an_empty_database_lists_nothing(store: TelemetryStore) -> None:
    assert store.recent_runs() == []


def test_the_postgres_store_connects_with_the_same_guards_as_the_memory_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit 2026-09-14 §2: a dead socket held a turn (ISS-0064); the guards
    the memory store got apply to the telemetry connection too."""

    import app.telemetry.postgres as module
    from app.memory.postgres import CONNECTION_GUARDS

    seen: dict[str, object] = {}

    class Connection:
        closed = False
        broken = False

    def connect(dsn: str, **kwargs: object) -> Connection:
        seen.update(kwargs)
        return Connection()

    monkeypatch.setattr(module.psycopg, "connect", connect)

    module.PostgresTelemetry("postgresql://example/db")

    assert all(seen[name] == value for name, value in CONNECTION_GUARDS.items())
