"""Reading one measured turn back.

The point of the inspector is a promise: given one bad or expensive turn, its
`run_id` explains where the time, model calls and tool calls went — without
reading Modal's logs and without reading anybody's conversation. These tests
assert both halves of that.
"""

from __future__ import annotations

from app.telemetry.base import TraceEvent, TurnRun
from app.telemetry.inspect import render_listing, render_run, render_summary


def event(seq: int, type: str, at: str, duration_ms: int | None = None, **data: object) -> TraceEvent:
    return TraceEvent(
        run_id="r1", seq=seq, type=type, timestamp=at, duration_ms=duration_ms, data=data
    )


def finished_run() -> TurnRun:
    run = TurnRun(
        run_id="r1",
        user_id="user-alice",
        thread_id="t1",
        source="telegram-webhook",
        source_update_id="7",
        started_at="2026-08-29T10:00:00.000+00:00",
    )
    run.finished_at = "2026-08-29T10:00:09.000+00:00"
    run.status = "completed"
    run.outcome = "answer_delivered"
    run.route = "answer"
    run.model_calls = 2
    run.tool_calls = 1
    run.input_tokens = 6292
    run.output_tokens = 632
    run.first_model_token_ms = 3160
    run.first_visible_ms = 3400
    run.total_ms = 10500
    return run


def full_trace() -> list[TraceEvent]:
    return [
        event(1, "turn_started", "2026-08-29T10:00:00.000+00:00", queued_ms=1500),
        event(2, "model_started", "2026-08-29T10:00:00.010+00:00", purpose="router", call_index=1),
        event(
            3,
            "model_finished",
            "2026-08-29T10:00:00.590+00:00",
            580,
            purpose="router",
            call_index=1,
            input_tokens=1420,
            output_tokens=18,
            finish_reason="stop",
        ),
        event(4, "tool_started", "2026-08-29T10:00:00.600+00:00", tool="read_file", call_index=1, path="notes.txt"),
        event(
            5,
            "tool_finished",
            "2026-08-29T10:00:01.410+00:00",
            810,
            tool="read_file",
            call_index=1,
            path="notes.txt",
            status="success",
        ),
        event(6, "model_started", "2026-08-29T10:00:01.420+00:00", purpose="answer", call_index=2),
        event(7, "model_first_token", "2026-08-29T10:00:01.660+00:00", 240, purpose="answer", call_index=2),
        event(
            8,
            "model_finished",
            "2026-08-29T10:00:05.450+00:00",
            4030,
            purpose="answer",
            call_index=2,
            input_tokens=4872,
            output_tokens=614,
            finish_reason="stop",
        ),
        event(9, "telegram_final_sent", "2026-08-29T10:00:05.700+00:00"),
        event(10, "turn_finished", "2026-08-29T10:00:09.000+00:00", 10500, outcome="answer_delivered"),
    ]


def test_a_finished_run_reports_what_it_cost() -> None:
    text = render_run(finished_run(), full_trace())

    assert "Run r1" in text
    assert "answer_delivered" in text
    assert "router" in text and "answer" in text
    assert "read_file" in text and "notes.txt" in text
    assert "tokens 6292 in / 632 out" in text
    assert "model calls 2" in text and "tool calls 1" in text


def test_the_queue_wait_is_part_of_the_turn() -> None:
    """It was the largest single part of the wait in the first live baseline.

    A listing that starts at the model would report a turn twice as fast as the
    person experienced, so the offsets are shifted by the wait and it is named.
    """

    text = render_run(finished_run(), full_trace())

    assert "queue wait" in text
    lines = [line for line in text.splitlines() if "model_started" in line]
    assert lines and "1.51s" in lines[0]


def test_time_nobody_measured_is_named_rather_than_absorbed() -> None:
    text = render_run(finished_run(), full_trace())

    assert "unattributed" in text


def test_a_run_that_never_finished_says_so() -> None:
    """Nothing closes the row of a container that died. That is the crash."""

    run = TurnRun(run_id="r1", started_at="2026-08-29T10:00:00.000+00:00")
    trace = [
        event(1, "turn_started", "2026-08-29T10:00:00.000+00:00"),
        event(2, "model_started", "2026-08-29T10:00:00.010+00:00", purpose="router"),
    ]

    text = render_run(run, trace)

    assert "UNFINISHED" in text
    assert "model_started" in text


def test_a_failed_tool_is_not_rendered_as_a_success() -> None:
    run = finished_run()
    trace = [
        event(1, "turn_started", "2026-08-29T10:00:00.000+00:00"),
        event(2, "tool_started", "2026-08-29T10:00:00.100+00:00", tool="read_document", call_index=1),
        event(
            3,
            "tool_failed",
            "2026-08-29T10:00:00.300+00:00",
            200,
            tool="read_document",
            call_index=1,
            status="failed",
            error_type="DocumentError",
        ),
    ]

    text = render_run(run, trace)

    assert "read_document" in text
    assert "failed" in text
    assert "success" not in text


def test_a_call_that_never_ran_is_shown_with_the_reason() -> None:
    trace = [
        event(1, "turn_started", "2026-08-29T10:00:00.000+00:00"),
        event(
            2,
            "tool_skipped",
            "2026-08-29T10:00:00.100+00:00",
            tool="write_file",
            status="budget_exhausted",
            path="b.txt",
            stage="implement",
        ),
    ]

    text = render_run(finished_run(), trace)

    assert "write_file" in text
    assert "budget_exhausted" in text
    assert "implement" in text


def test_the_loop_s_steps_are_what_a_long_turn_is_read_by() -> None:
    """Not "it took ninety seconds" but "nine steps, and where they went"."""

    trace = [
        event(1, "turn_started", "2026-08-29T10:00:00.000+00:00"),
        event(2, "loop_step", "2026-08-29T10:00:00.100+00:00", step=1, tool_calls=0, spent_ms=0),
        event(
            3,
            "loop_step",
            "2026-08-29T10:00:06.400+00:00",
            step=2,
            tool_calls=1,
            spent_ms=6300,
        ),
    ]

    section = render_run(finished_run(), trace).split("Steps")[1].split("Timeline")[0]

    assert "6.30s" in section
    assert "1 tool call(s) so far" in section


def test_a_turn_that_was_asked_how_it_was_doing_says_so_where_its_steps_are() -> None:
    trace = [
        event(1, "turn_started", "2026-08-29T10:00:00.000+00:00"),
        event(2, "loop_step", "2026-08-29T10:00:00.100+00:00", step=1, tool_calls=0, spent_ms=0),
        event(
            3,
            "turn_health_check",
            "2026-08-29T10:10:09.000+00:00",
            step=7,
            tool_calls=9,
            spent_ms=609_000,
        ),
    ]

    text = render_run(finished_run(), trace)

    assert "asked whether it was on track after" in text


def test_a_turn_the_person_stopped_says_that_instead() -> None:
    trace = [
        event(1, "turn_started", "2026-08-29T10:00:00.000+00:00"),
        event(2, "loop_step", "2026-08-29T10:00:00.100+00:00", step=1, tool_calls=0, spent_ms=0),
        event(3, "turn_stopped", "2026-08-29T10:00:09.000+00:00", step=1, tool_calls=2),
    ]

    text = render_run(finished_run(), trace)

    assert "was stopped by the person" in text


def test_the_listing_orders_and_summarizes_runs() -> None:
    first, second = finished_run(), finished_run()
    second.run_id = "r2"
    second.status = "running"
    second.outcome = None
    second.total_ms = None

    text = render_listing([first, second])

    assert text.splitlines()[2].startswith("r1")
    assert "r2" in text
    assert "running" in text


def test_an_empty_listing_says_nothing_happened() -> None:
    assert "(no runs)" in render_listing([])


# --- the primary metric ------------------------------------------------------


def failed_run() -> TurnRun:
    run = finished_run()
    run.run_id = "r2"
    run.status = "failed"
    run.outcome = "failed"
    run.error_type = "BackendError"
    return run


def test_gpu_seconds_are_reported_per_successful_turn() -> None:
    """A turn that burned GPU and crashed must make this worse, not vanish.

    Item 3 names GPU active seconds per successful turn as the metric to watch,
    so the failed turn's cost stays in the numerator while only successes count
    in the denominator.
    """

    both = [(finished_run(), full_trace()), (failed_run(), full_trace())]

    text = render_summary(both)

    assert "2 turns, 1 successful, 1 failed or unfinished" in text
    # Each trace spans 5.44 s from the first request to the last, plus a 12 s
    # window: 17.44 s twice, over one success.
    assert "34.88s" in text
    assert "BackendError" in text


def test_the_summary_counts_calls_and_tokens_per_successful_turn() -> None:
    text = render_summary([(finished_run(), full_trace())])

    assert "model calls          2.00" in text
    assert "tool calls           1.00" in text
    assert "input tokens         6292" in text


def test_a_window_with_no_turns_says_so() -> None:
    assert render_summary([]) == "(no runs)"
