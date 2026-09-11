"""The export hands a trajectory over with its verdict attached (roadmap 24):
one JSON per run with the calls, the run's outcome, its failed-tool codes,
the repeat-guard count and the scenario's checks, and an index line."""

from __future__ import annotations

import json
from pathlib import Path

from app.telemetry import Telemetry, TraceEvent, TurnRun
from app.telemetry.base import stamp
from app.telemetry.sqlite import SqliteTelemetry
from tools.export_trajectories import export, read_local


def test_a_run_is_exported_with_its_verdict(tmp_path: Path) -> None:
    telemetry = Telemetry(SqliteTelemetry(tmp_path / "telemetry.sqlite3"))
    store = telemetry.store
    run = TurnRun(run_id="deployed-abc-210", source="loop-live", user_id="u")
    run.thread_id = "chat-d1"
    trace = telemetry.start(run)
    trace.event("tool_failed", tool="run_command", code="not_run")
    trace.event("tool_failed", tool="list_files", code="fs.not_found")
    trace.finish("answer_delivered")
    store.record_events(
        [
            TraceEvent(
                run_id="deployed-abc-210",
                seq=9000,
                type="scenario_checked",
                timestamp=stamp(),
                duration_ms=None,
                data={"letter": "D1", "name": "D1", "passed": False, "checks": {"x": True, "y": False}},
            )
        ]
    )
    folder = tmp_path / "traj"
    folder.mkdir()
    lines = [
        {"run_id": "deployed-abc-210", "thread_id": "chat-d1", "call_index": 2, "model": "m", "messages": [], "tools": [], "completion": {"text": "b"}},
        {"run_id": "deployed-abc-210", "thread_id": "chat-d1", "call_index": 1, "model": "m", "messages": [], "tools": [], "completion": {"text": "a"}},
    ]
    (folder / "deployed-abc-210.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    (folder / "other-1.jsonl").write_text(json.dumps(lines[0]) + "\n", encoding="utf-8")

    out = tmp_path / "export"
    count = export(read_local(folder, "deployed-"), store, out)
    telemetry.close()

    assert count == 1
    record = json.loads((out / "runs" / "deployed-abc-210.json").read_text(encoding="utf-8"))
    assert record["outcome"] == "answer_delivered" and record["model"] == "m"
    assert record["tool_failed"] == {"fs.not_found": 1} and record["repeat_guard"] == 1
    assert record["scenario"]["passed"] is False and record["scenario"]["checks"] == {"x": True, "y": False}
    assert [c["call_index"] for c in record["calls"]] == [1, 2]
    index = [json.loads(l) for l in (out / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    assert index[0]["run_id"] == "deployed-abc-210" and index[0]["calls"] == 2 and "messages" not in json.dumps(index)
