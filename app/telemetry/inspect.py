"""Reading a turn back: one run rendered, and lists of recent ones.

The writing half exists to answer one question — *given one bad or expensive
turn, where did its time, model calls and tool calls go* — and until something
renders it the answer is hand-written SQL against two tables. This is that
renderer, kept in `app/` rather than in the script so it can be tested.

It can only print what telemetry stores: timings, counts, state transitions,
tool names and paths inside the person's own workspace. There is no message
text to leak here because none was ever written.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.telemetry.base import TraceEvent, TurnRun, moment
from app.telemetry.cost import (
    A10_USD_PER_SECOND,
    IDLE_WINDOW_SECONDS,
    gpu_cost,
    render_cost,
)

# Events whose duration is already reported in their own section, so the summary
# of everything else does not repeat them.
MODEL_EVENTS = frozenset(
    {"model_started", "model_first_token", "model_finished", "model_failed"}
)
TOOL_EVENTS = frozenset({"tool_started", "tool_finished", "tool_failed", "tool_skipped"})


def seconds(milliseconds: float | None) -> str:
    return "     -  " if milliseconds is None else f"{milliseconds / 1000:7.2f}s"


def offsets(run: TurnRun, events: Sequence[TraceEvent]) -> dict[int, float]:
    """Milliseconds from the person pressing send to each event.

    Not from the row's `started_at`, which is when a worker claimed the update.
    The queue wait belongs inside the turn — it is often the largest single part
    of the wait — so every offset here is shifted by it, and the timeline then
    agrees with `first_visible_ms` and `total_ms` instead of contradicting them.
    """

    origin = moment(run.started_at)
    queued = 0.0
    for event in events:
        if event.type == "turn_started":
            queued = float(event.data.get("queued_ms") or 0)
            break
    found: dict[int, float] = {}
    for event in events:
        at = moment(event.timestamp)
        if origin is None or at is None:
            continue
        found[event.seq] = queued + (at - origin).total_seconds() * 1000
    return found


def model_calls(events: Sequence[TraceEvent]) -> list[dict[str, object]]:
    """One row per model request, joined from its start, first token and end."""

    calls: dict[int, dict[str, object]] = {}
    for event in events:
        if event.type not in MODEL_EVENTS:
            continue
        index = int(event.data.get("call_index") or 0)
        if not index:
            # Every model event the recorder writes carries its call index. One
            # that does not belongs to no call, and inventing call zero for it
            # would put a row in this section that no request produced.
            continue
        call = calls.setdefault(
            index,
            {
                "index": index,
                "purpose": event.data.get("purpose", "?"),
                "stage": event.data.get("stage"),
                "ttft_ms": None,
                "duration_ms": None,
                "input": None,
                "cached": None,
                "output": None,
                "reasoning": None,
                "finish": None,
                "error": None,
            },
        )
        if event.type == "model_first_token":
            call["ttft_ms"] = event.duration_ms
        elif event.type == "model_finished":
            call["duration_ms"] = event.duration_ms
            call["input"] = event.data.get("input_tokens")
            call["cached"] = event.data.get("cached_tokens")
            call["output"] = event.data.get("output_tokens")
            call["reasoning"] = event.data.get("reasoning_tokens")
            call["finish"] = event.data.get("finish_reason")
        elif event.type == "model_failed":
            call["duration_ms"] = event.duration_ms
            call["error"] = event.data.get("error_type", "failed")
    return [calls[index] for index in sorted(calls)]


def tool_calls(events: Sequence[TraceEvent]) -> list[dict[str, object]]:
    """One row per tool call, including the ones that never ran.

    A refused or skipped call has no start event and is not counted against the
    turn, but it is exactly what explains a task that ran out of budget, so it
    appears here with the status that says why it did not run.
    """

    rows: list[dict[str, object]] = []
    started: dict[int, dict[str, object]] = {}
    for event in events:
        if event.type not in TOOL_EVENTS:
            continue
        index = event.data.get("call_index")
        row = {
            "index": index,
            "tool": event.data.get("tool", "?"),
            "stage": event.data.get("stage"),
            "iteration": event.data.get("iteration"),
            "path": event.data.get("path"),
            "duration_ms": event.duration_ms,
            "status": event.data.get("status", "success"),
            "code": event.data.get("code"),
            "message": event.data.get("message"),
        }
        if event.type == "tool_started" and isinstance(index, int):
            started[index] = row
            rows.append(row)
            continue
        if isinstance(index, int) and index in started:
            started[index].update(
                duration_ms=event.duration_ms,
                status=event.data.get("status", "failed"),
                code=event.data.get("code"),
                message=event.data.get("message"),
            )
            continue
        rows.append(row)
    return rows


def steps(events: Sequence[TraceEvent]) -> list[tuple[int, int, int, str]]:
    """The loop's own shape: each step, and what the turn had spent reaching it.

    One step is one model call and whatever it decided to do next, so this is
    where a long autonomous turn becomes readable — not "it took ninety
    seconds" but "it took nine steps, and the fourth was where the tools went".
    """

    found = []
    for event in events:
        if event.type != "loop_step":
            continue
        found.append(
            (
                int(event.data.get("step") or 0),
                int(event.data.get("spent_ms") or 0),
                int(event.data.get("tool_calls") or 0),
                str(event.data.get("stopping") or ""),
            )
        )
    return found


def summary(run: TurnRun, events: Sequence[TraceEvent]) -> list[str]:
    unfinished = run.finished_at is None
    last = events[-1].type if events else "nothing recorded"
    lines = [
        f"Run {run.run_id}",
        f"  outcome    {run.outcome or ('unfinished' if unfinished else '-')}"
        f"   status {run.status}   route {run.route or '-'}",
        f"  user       {run.user_id or '-'}   thread {run.thread_id or '-'}"
        f"   source {run.source or '-'}",
        f"  started    {run.started_at}",
    ]
    if unfinished:
        # Nothing closed this row, which is what a container that died looks
        # like from outside. Saying so is the whole point of showing it.
        lines.append(
            f"  UNFINISHED no turn_finished was ever recorded; last event: {last}"
        )
    if run.error_type:
        lines.append(f"  error      {run.error_type}")
    return lines


def timings(run: TurnRun, events: Sequence[TraceEvent]) -> list[str]:
    queued = next(
        (event.data.get("queued_ms") for event in events if event.type == "turn_started"),
        None,
    )
    return [
        "",
        f"  queue wait        {seconds(float(queued) if queued else None)}",
        f"  first model token {seconds(run.first_model_token_ms)}",
        f"  first visible     {seconds(run.first_visible_ms)}",
        f"  total             {seconds(run.total_ms)}",
    ]


def timeline(run: TurnRun, events: Sequence[TraceEvent]) -> list[str]:
    """Every event at its offset, so unattributed time is visible as a gap."""

    at = offsets(run, events)
    lines = ["", "Timeline"]
    for event in events:
        detail = " ".join(
            f"{key}={value}"
            for key, value in event.data.items()
            if key not in {"call_index", "queued_ms", "elapsed_ms"}
        )
        duration = f" [{event.duration_ms} ms]" if event.duration_ms is not None else ""
        lines.append(
            f"  {seconds(at.get(event.seq))}  {event.type:<24}{duration} {detail}".rstrip()
        )
    return lines


def model_section(events: Sequence[TraceEvent]) -> list[str]:
    calls = model_calls(events)
    if not calls:
        return []
    lines = ["", "Model calls"]
    for call in calls:
        tokens = f"{call['input'] or 0:>6} -> {call['output'] or 0:<5}"
        if call["cached"] is not None:
            tokens += f" cached {call['cached']:>5}"
        if call["reasoning"]:
            tokens += f" reasoning {call['reasoning']:>5}"
        ttft = (
            f" first token {seconds(call['ttft_ms'])}"
            if call["ttft_ms"] is not None
            else ""
        )
        state = call["error"] or call["finish"] or "-"
        stage = f" [{call['stage']}]" if call["stage"] else ""
        lines.append(
            f"  {call['index']:>2}  {str(call['purpose']) + stage:<22}"
            f"{seconds(call['duration_ms'])}  {tokens}  {state}{ttft}"
        )
    return lines


def tool_section(events: Sequence[TraceEvent]) -> list[str]:
    calls = tool_calls(events)
    if not calls:
        return []
    lines = ["", "Tool calls"]
    for call in calls:
        index = f"{call['index']:>2}" if isinstance(call["index"], int) else " -"
        where = ""
        if call["stage"]:
            where = f" [{call['stage']}"
            if call["iteration"] is not None:
                where += f" {call['iteration']}"
            where += "]"
        lines.append(
            f"  {index}  {str(call['tool']) + where:<28}{seconds(call['duration_ms'])}"
            f"  {call['status']:<16}{call['path'] or ''}".rstrip()
        )
        if call.get("code"):
            # The reason under the row it belongs to, so a failed call reads as
            # what happened rather than as a status word.
            lines.append(f"        {call['code']}: {call.get('message') or ''}".rstrip())
    return lines


def step_section(events: Sequence[TraceEvent]) -> list[str]:
    found = steps(events)
    if not found:
        return []
    lines = ["", "Steps"]
    for index, spent_ms, tool_calls, stopping in found:
        note = f"  {stopping}" if stopping else ""
        lines.append(
            f"  {index:<4}{seconds(spent_ms):>9} spent"
            f"   {tool_calls} tool call(s) so far{note}"
        )
    ended = [
        event
        for event in events
        if event.type in {"turn_budget_exhausted", "turn_stopped"}
    ]
    for event in ended:
        why = (
            f"reached its {event.data.get('limit')} limit"
            if event.type == "turn_budget_exhausted"
            else "was stopped by the person"
        )
        lines.append(f"  the turn {why}")
    return lines


def totals(run: TurnRun, events: Sequence[TraceEvent]) -> list[str]:
    model_ms = sum(
        event.duration_ms or 0
        for event in events
        if event.type in {"model_finished", "model_failed"}
    )
    tool_ms = sum(
        event.duration_ms or 0
        for event in events
        if event.type in {"tool_finished", "tool_failed"}
    )
    queued = next(
        (
            float(event.data.get("queued_ms") or 0)
            for event in events
            if event.type == "turn_started"
        ),
        0.0,
    )
    lines = [
        "",
        "Totals",
        f"  model calls {run.model_calls}   tool calls {run.tool_calls}"
        f"   tokens {run.input_tokens} in / {run.output_tokens} out",
        f"  model time {seconds(model_ms)}   tool time {seconds(tool_ms)}",
    ]
    if run.total_ms is not None:
        # What no measured step claimed: the graph, persistence, the network to
        # Telegram, and anything nobody has bracketed yet. Named rather than
        # silently absorbed, because a 5.6 s persistence outlier was found once
        # by noticing exactly this kind of unexplained remainder.
        rest = run.total_ms - model_ms - tool_ms - queued
        lines.append(f"  unattributed{seconds(rest)}")
    return lines


def render_run(
    run: TurnRun,
    events: Sequence[TraceEvent],
    *,
    idle_window_seconds: float = IDLE_WINDOW_SECONDS,
    rate_per_second: float = A10_USD_PER_SECOND,
) -> str:
    parts = [
        *summary(run, events),
        *timings(run, events),
        *model_section(events),
        *tool_section(events),
        *step_section(events),
        *timeline(run, events),
        *totals(run, events),
        *render_cost(
            gpu_cost(
                events,
                idle_window_seconds=idle_window_seconds,
                rate_per_second=rate_per_second,
            )
        ),
    ]
    return "\n".join(parts)


def render_summary(
    measured: Sequence[tuple[TurnRun, Sequence[TraceEvent]]],
    *,
    idle_window_seconds: float = IDLE_WINDOW_SECONDS,
    rate_per_second: float = A10_USD_PER_SECOND,
) -> str:
    """The primary metric, over a window of turns.

    Item 3 names GPU active seconds per *successful* user turn as the number to
    watch, deliberately not total spend and deliberately not per turn: a turn
    that burned a minute and crashed must make this worse, not disappear from
    the denominator.
    """

    if not measured:
        return "(no runs)"
    successful = [run for run, _ in measured if run.successful]
    unsuccessful = [
        run for run, _ in measured if not run.successful and run.outcome != "cancelled"
    ]
    gpu_ms = 0.0
    cost = 0.0
    for _run, events in measured:
        estimate = gpu_cost(
            events,
            idle_window_seconds=idle_window_seconds,
            rate_per_second=rate_per_second,
        )
        if estimate is not None:
            gpu_ms += estimate.estimated_active_ms
            cost += estimate.derived_usd
    users = {run.user_id for run, _ in measured if run.user_id}
    wins = len(successful) or 1
    lines = [
        f"{len(measured)} turns, {len(successful)} successful,"
        f" {len(unsuccessful)} failed or unfinished",
        f"  {measured[-1][0].started_at[:19].replace('T', ' ')}"
        f" to {measured[0][0].started_at[:19].replace('T', ' ')}"
        f", {len(users)} distinct users",
        "",
        "Per successful turn",
        f"  GPU active        {gpu_ms / wins / 1000:7.2f}s   derived, upper bound",
        f"  derived cost      ${cost / wins:.4f}",
        f"  model calls       {sum(run.model_calls for run in successful) / wins:7.2f}",
        f"  tool calls        {sum(run.tool_calls for run in successful) / wins:7.2f}",
        f"  input tokens      {sum(run.input_tokens for run in successful) / wins:7.0f}",
        f"  output tokens     {sum(run.output_tokens for run in successful) / wins:7.0f}",
        "",
        f"Derived cost over the window ${cost:.4f}"
        f", ${cost / max(1, len(users)):.4f} a user",
    ]
    failures: dict[str, int] = {}
    for run in unsuccessful:
        failures[run.error_type or "unfinished"] = (
            failures.get(run.error_type or "unfinished", 0) + 1
        )
    if failures:
        lines += ["", "Failures by type"]
        lines += [f"  {name:<28}{count}" for name, count in sorted(failures.items())]
    return "\n".join(lines)


def render_listing(runs: Iterable[TurnRun]) -> str:
    header = (
        f"{'run_id':<34}{'started':<21}{'status':<11}{'outcome':<22}"
        f"{'route':<8}{'total':>9}  {'calls':<10}{'tokens':<14}user"
    )
    lines = [header, "-" * len(header)]
    for run in runs:
        calls = f"{run.model_calls}m/{run.tool_calls}t"
        tokens = f"{run.input_tokens}/{run.output_tokens}"
        # Seconds are enough to find a turn someone is complaining about; the
        # exact instant is in the run itself.
        started = run.started_at[:19].replace("T", " ")
        lines.append(
            f"{run.run_id:<34}{started:<21}{run.status:<11}"
            f"{run.outcome or '-':<22}{run.route or '-':<8}{seconds(run.total_ms):>9}  "
            f"{calls:<10}{tokens:<14}{run.user_id or '-'}"
        )
    if len(lines) == 2:
        lines.append("(no runs)")
    return "\n".join(lines)
