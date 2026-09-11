"""Hand trajectories over to the training side, each with its verdict attached.

    python tools/export_trajectories.py --out data/export            from the Volume
    python tools/export_trajectories.py --out data/export --from-dir data/trajectories
    python tools/export_trajectories.py --out data/export --prefix deployed-c8c79146

The boundary of roadmap 24 (DECISIONS 2026-09-11): the harness gives the
training repository trajectories it can filter on ready fields, so that side
never reads this database or these settings. One JSON file per run in
`<out>/runs/`, plus `<out>/index.jsonl` with one line per run:

    run_id, model, source, thread_id, started_at, outcome, status,
    model_calls, tool_calls, tool_failed (codes), repeat_guard (not_run count),
    held_out — a run of D1–3, V1–3 or X1–3: the measuring set, never trained on
    scenario {letter, name, passed, checks}   — from the `scenario_checked` event
    calls — the trajectory lines as written (`app/trajectories.py`)

Reads the Volume `assistant-workspaces` at `.trajectories/` (a client read,
no worker) or a local directory, and the telemetry store the application
would open (`AGENT_DATABASE_URL` for the deployed one). Read-only on both;
never prints a connection string.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.config import AgentSettings
from app.telemetry.open import open_telemetry
from scripts.training_scenarios import held_out_sequences

VOLUME = "assistant-workspaces"
FOLDER = ".trajectories"
HELD_OUT = held_out_sequences()


def is_held_out(run_id: str) -> bool:
    """A run of a held-out case (D1–3, V1–3, X1–3): measured on, never trained on."""

    tail = run_id.rsplit("-", 1)[-1]
    return tail.isdigit() and int(tail) in HELD_OUT and run_id.startswith(("deployed-", "live-"))


def read_local(directory: Path, prefix: str) -> Iterable[tuple[str, list[dict]]]:
    for path in sorted(directory.glob("*.jsonl")):
        if path.stem.startswith(prefix):
            yield path.stem, [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_volume(prefix: str) -> Iterable[tuple[str, list[dict]]]:
    import modal

    volume = modal.Volume.from_name(VOLUME)
    for entry in sorted(volume.listdir(FOLDER), key=lambda e: e.path):
        name = Path(entry.path).name
        if not name.endswith(".jsonl") or not name[:-6].startswith(prefix):
            continue
        text = b"".join(volume.read_file(entry.path)).decode("utf-8")
        yield name[:-6], [json.loads(line) for line in text.splitlines() if line.strip()]


def verdict_of(events: list[Any]) -> tuple[dict[str, Any] | None, Counter, int]:
    """The scenario verdict, the failed-tool codes and the repeat-guard refusals."""

    scenario = None
    codes: Counter = Counter()
    refused = 0
    for event in events:
        if event.type == "scenario_checked":
            scenario = dict(event.data)
        elif event.type == "tool_failed":
            code = str(event.data.get("code") or event.data.get("error") or "?")
            if code == "not_run":
                refused += 1
            else:
                codes[code] += 1
    return scenario, codes, refused


def export(runs: Iterable[tuple[str, list[dict]]], store, out: Path) -> int:
    (out / "runs").mkdir(parents=True, exist_ok=True)
    count = 0
    with (out / "index.jsonl").open("w", encoding="utf-8") as index:
        for run_id, calls in runs:
            run = store.get_turn(run_id) if store is not None else None
            events = store.events(run_id) if store is not None else []
            scenario, codes, refused = verdict_of(events)
            record = {
                "run_id": run_id,
                "model": next((c.get("model") for c in calls if c.get("model")), None),
                "source": run.source if run else None,
                "thread_id": run.thread_id if run else (calls[0].get("thread_id") if calls else None),
                "started_at": run.started_at if run else None,
                "outcome": run.outcome if run else None,
                "status": run.status if run else None,
                "model_calls": run.model_calls if run else len(calls),
                "tool_calls": run.tool_calls if run else None,
                "tool_failed": dict(codes),
                "repeat_guard": refused,
                "held_out": is_held_out(run_id),
                "scenario": scenario,
                "calls": sorted(calls, key=lambda c: c.get("call_index", 0)),
            }
            (out / "runs" / f"{run_id}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            summary = {k: v for k, v in record.items() if k != "calls"}
            summary["calls"] = len(calls)
            index.write(json.dumps(summary, ensure_ascii=False) + "\n")
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="the export directory")
    parser.add_argument("--from-dir", default=None, help="read local .jsonl files instead of the Volume")
    parser.add_argument("--prefix", default="", help="only runs whose id starts with this")
    options = parser.parse_args(sys.argv[1:] if argv is None else argv)
    telemetry = open_telemetry(AgentSettings())
    try:
        source = (
            read_local(Path(options.from_dir), options.prefix)
            if options.from_dir
            else read_volume(options.prefix)
        )
        count = export(source, telemetry.store, Path(options.out))
    finally:
        telemetry.close()
    print(f"exported {count} runs to {options.out} (runs/*.json, index.jsonl)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
